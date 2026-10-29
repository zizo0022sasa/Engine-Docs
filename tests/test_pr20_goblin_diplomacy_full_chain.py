"""`PR-20` Part 3.3 — Goblin Diplomacy, complete zero-touch chain (T115..T118).

The quest needs three mails: `Goblin mail (288)` x3 from the Goblin Village crates, one dyed
**orange** (`286`) and one dyed **blue** (`287`).

That means the bot must *buy nothing and be given nothing* yet still pay for its dyes:

    Wyson the gardener (Falador Park)  -> 2x Woad leaves (1766)  ... 20 GP
    3x Redberries (1951) + 2x Onions (1957) -> Aggie (Draynor)   ... 5 GP per dye
    Red dye (1763) + Yellow dye (1765) -> Orange dye (1769)      ... in-inventory combine
    Orange/Blue dye + Goblin mail      -> dyed mails (286 / 287) ... in-inventory combine

`PR-20` therefore added a **coin requirement** to every paid step and a one-time **coin float**
withdrawn from the bot's own banked earnings (never from the operator).

- T115: the dye economy — Wyson needs 20 GP, Aggie 5 GP per dye; the engine withdraws them from the
        bank, and without any coins it escalates precisely instead of stalling.
- T116: the combine wire order: red+yellow -> orange dye, then orange-on-mail BEFORE blue-on-mail.
- T117: three Goblin mails are recovered from the crates, one per search.
- T118: kernel run end-to-end to `Varp 62 = 6`, `+5 QP`, `COMPLETE_TASK`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from osrs_ee.bots.base import DirectiveType, TickContext
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.supply_procurement import (
    BLUE_DYE_ID,
    BLUE_GOBLIN_MAIL_ID,
    COINS_ID,
    GOBLIN_MAIL_ID,
    ONION_ID,
    ORANGE_DYE_ID,
    ORANGE_GOBLIN_MAIL_ID,
    RED_DYE_ID,
    REDBERRY_ID,
    SUPPLY_RECIPES,
    WOAD_LEAF_ID,
    YELLOW_DYE_ID,
    ZeroTouchSupplyEngine,
)
from osrs_ee.core import ExecutionEngineKernel, ReactiveSafetyWatchdog, SessionLifecycleManager
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph, NavigationEngine
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.state import AccountStatus, StateStore

PARK = DEFAULT_WORLD_NODES["FALADOR_PARK"]
AGGIE = DEFAULT_WORLD_NODES["DRAYNOR_AGGIE"]
VILLAGE = DEFAULT_WORLD_NODES["GOBLIN_VILLAGE"]


def _ctx(pos: WorldPoint, inventory: list[dict[str, Any]], tick: int) -> TickContext:
    return TickContext(
        game_tick=tick,
        position=pos,
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=tuple(inventory),
        total_level=35,
    )


# ---------------------------------------------------------------------------
# T115 — dyes are not free
# ---------------------------------------------------------------------------
def test_t115_wyson_and_aggie_are_paid_from_the_bots_own_bank() -> None:
    """T115 (`PR-20`): `20 GP` for the woad leaves and `5 GP` per Aggie dye are real costs. They are
    withdrawn from the account's own banked earnings — never handed over by the operator — and a
    genuinely coinless account escalates precisely instead of clicking an NPC that refuses."""
    # Declared on the steps themselves.
    assert SUPPLY_RECIPES[WOAD_LEAF_ID].steps[0].consumes == ((COINS_ID, 20),)
    for dye_id in (BLUE_DYE_ID, RED_DYE_ID, YELLOW_DYE_ID):
        consumes = SUPPLY_RECIPES[dye_id].steps[0].consumes
        assert (COINS_ID, 5) in consumes, (dye_id, consumes)

    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    bot._bank_audit_done = True
    bot._bank_stock = {COINS_ID: 10_000}
    at_ge_open = TickContext(
        game_tick=3,
        position=WorldPoint(3164, 3487, 0),
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=(),
        bank_is_open=True,
        total_level=35,
    )
    directive = bot._procure(at_ge_open, WOAD_LEAF_ID, 2, "Woad leaves")
    assert directive is not None
    assert directive.directive_type == DirectiveType.BANK_TURNAROUND, directive
    withdraw = list(directive.payload.get("withdraw") or [])
    assert withdraw and int(withdraw[0]["item_id"]) == COINS_ID
    assert int(withdraw[0]["quantity"]) >= 20

    # A coinless account: a precise escalation, never a silent stall at Aggie's door.
    broke = TradeUnlockQuestsTaskModule(target_quest_points=12)
    broke._bank_audit_done = True
    broke._bank_stock = {}
    directive = broke._procure(at_ge_open, WOAD_LEAF_ID, 2, "Woad leaves")
    assert directive is not None
    assert directive.directive_type == DirectiveType.ESCALATE_HUMAN
    assert directive.reason == "SUPPLY_UNSOURCED:Coins", directive.reason

    # The float is a single withdrawal, not a trek per dye.
    bot._coin_float_resolved = False
    bot._bank_stock = {COINS_ID: 10_000}
    float_directive = bot._ensure_coin_float(at_ge_open)
    assert float_directive is not None and float_directive.directive_type == DirectiveType.BANK_TURNAROUND
    assert int(list(float_directive.payload["withdraw"])[0]["quantity"]) == TradeUnlockQuestsTaskModule.COIN_FLOAT_GP


# ---------------------------------------------------------------------------
# T116 — the combine order the live wire expects
# ---------------------------------------------------------------------------
def test_t116_orange_dye_then_orange_mail_then_blue_mail() -> None:
    """T116 (`PR-20`): combining is non-idempotent, so the ORDER matters. The engine must build the
    orange dye from red+yellow, then dye the FIRST mail with it, and only then dye the blue one."""
    engine = ZeroTouchSupplyEngine()
    inventory: list[dict[str, Any]] = [
        {"item_id": RED_DYE_ID, "name": "Red dye", "quantity": 1},
        {"item_id": YELLOW_DYE_ID, "name": "Yellow dye", "quantity": 1},
        {"item_id": BLUE_DYE_ID, "name": "Blue dye", "quantity": 1},
        {"item_id": GOBLIN_MAIL_ID, "name": "Goblin mail", "quantity": 3},
    ]
    combines: list[tuple[int, int]] = []
    for tick in range(1, 40):
        directive = engine.ensure(_ctx(WorldPoint(2958, 3512, 0), inventory, tick), ORANGE_GOBLIN_MAIL_ID, 1)
        if directive is None:
            break
        assert directive.directive_type == DirectiveType.USE_ITEM_ON_ITEM, directive
        src = int(directive.payload["source_item_id"])
        dst = int(directive.payload["target_item_id"])
        combines.append((src, dst))
        # simulate the combine
        for iid in (src, dst):
            for it in list(inventory):
                if int(it["item_id"]) == iid:
                    it["quantity"] -= 1
                    if it["quantity"] <= 0:
                        inventory.remove(it)
                    break
        out = {frozenset({RED_DYE_ID, YELLOW_DYE_ID}): ORANGE_DYE_ID}.get(frozenset({src, dst}))
        if out is None:
            out = ORANGE_GOBLIN_MAIL_ID if ORANGE_DYE_ID in (src, dst) else BLUE_GOBLIN_MAIL_ID
        inventory.append({"item_id": out, "name": "out", "quantity": 1})

    assert combines[:2] == [(RED_DYE_ID, YELLOW_DYE_ID), (ORANGE_DYE_ID, GOBLIN_MAIL_ID)], combines
    assert any(int(i["item_id"]) == ORANGE_GOBLIN_MAIL_ID for i in inventory)
    # The plain mail and the blue dye survive for the second craft.
    assert any(int(i["item_id"]) == BLUE_DYE_ID for i in inventory)
    assert any(int(i["item_id"]) == GOBLIN_MAIL_ID for i in inventory)


# ---------------------------------------------------------------------------
# T117 — three mails, three searches
# ---------------------------------------------------------------------------
def test_t117_three_goblin_mails_are_recovered_one_search_at_a_time() -> None:
    """T117 (`PR-20`): the crate step is a repeat step — three distinct mails must be recovered, and
    the progress-aware stall guard must never cut the loop short after the first one."""
    engine = ZeroTouchSupplyEngine()
    inventory: list[dict[str, Any]] = []
    searches = 0
    for tick in range(1, 60):
        directive = engine.ensure(_ctx(VILLAGE, inventory, tick), GOBLIN_MAIL_ID, 3)
        if directive is None:
            break
        assert directive.directive_type == DirectiveType.INTERACT_OBJECT, directive
        assert directive.payload["name"] == "Crate"
        assert directive.payload["action"] == "Search"
        searches += 1
        inventory.append({"item_id": GOBLIN_MAIL_ID, "name": "Goblin mail", "quantity": 1})

    assert searches == 3, f"expected 3 searches, got {searches}"
    assert sum(int(i["quantity"]) for i in inventory if int(i["item_id"]) == GOBLIN_MAIL_ID) == 3


# ---------------------------------------------------------------------------
# T118 — kernel end-to-end
# ---------------------------------------------------------------------------
def _goblin_world(command_type: str, payload: dict[str, Any], adapter: FakeGameAdapter) -> None:
    name = str(payload.get("name", ""))

    def take(item_id: int, qty: int = 1) -> None:
        for it in list(adapter.inventory_items):
            if int(it["item_id"]) == item_id and int(it["quantity"]) >= qty:
                it["quantity"] -= qty
                if it["quantity"] <= 0:
                    adapter.inventory_items.remove(it)
                return

    def add(item_id: int, label: str, qty: int = 1) -> None:
        adapter._add_to_inventory({"item_id": item_id, "name": label, "quantity": qty, "noted": False})

    inv = {int(i["item_id"]): int(i["quantity"]) for i in adapter.inventory_items}

    if command_type == "INTERACT_NPC":
        if name == "Wyson the gardener":
            if inv.get(COINS_ID, 0) >= 20:
                take(COINS_ID, 20)
                add(WOAD_LEAF_ID, "Woad leaves", 2)
        elif name == "Aggie":
            if inv.get(COINS_ID, 0) < 5:
                return
            if inv.get(WOAD_LEAF_ID, 0) >= 2:
                take(WOAD_LEAF_ID, 2)
                take(COINS_ID, 5)
                add(BLUE_DYE_ID, "Blue dye")
            elif inv.get(REDBERRY_ID, 0) >= 3:
                take(REDBERRY_ID, 3)
                take(COINS_ID, 5)
                add(RED_DYE_ID, "Red dye")
            elif inv.get(ONION_ID, 0) >= 2:
                take(ONION_ID, 2)
                take(COINS_ID, 5)
                add(YELLOW_DYE_ID, "Yellow dye")
        elif name == "General Bentnoze":
            if all(inv.get(i, 0) >= 1 for i in (ORANGE_GOBLIN_MAIL_ID, BLUE_GOBLIN_MAIL_ID, GOBLIN_MAIL_ID)):
                for i in (ORANGE_GOBLIN_MAIL_ID, BLUE_GOBLIN_MAIL_ID, GOBLIN_MAIL_ID):
                    take(i, 1)
                adapter.varps[62] = 6
                adapter.quest_points += 5
    elif command_type == "INTERACT_OBJECT":
        if name == "Crate":
            add(GOBLIN_MAIL_ID, "Goblin mail")
        elif name == "Redberry bush":
            add(REDBERRY_ID, "Redberries", 3)
        elif name in ("Onion", "Onions"):
            add(ONION_ID, "Onion", 2)
    elif command_type == "USE_ITEM_ON_ITEM":
        src = int(payload["source_item_id"])
        dst = int(payload["target_item_id"])
        pair = frozenset({src, dst})
        if pair == frozenset({RED_DYE_ID, YELLOW_DYE_ID}):
            take(RED_DYE_ID)
            take(YELLOW_DYE_ID)
            add(ORANGE_DYE_ID, "Orange dye")
        elif pair == frozenset({ORANGE_DYE_ID, GOBLIN_MAIL_ID}):
            take(ORANGE_DYE_ID)
            take(GOBLIN_MAIL_ID)
            add(ORANGE_GOBLIN_MAIL_ID, "Orange goblin mail")
        elif pair == frozenset({BLUE_DYE_ID, GOBLIN_MAIL_ID}):
            take(BLUE_DYE_ID)
            take(GOBLIN_MAIL_ID)
            add(BLUE_GOBLIN_MAIL_ID, "Blue goblin mail")


def test_t118_kernel_run_completes_goblin_diplomacy(tmp_path: Path) -> None:
    """T118 (`PR-20`): one kernel run must pay for its own dyes, gather everything in-world, dye both
    mails in the verified order and finish at `Varp 62 = 6` (+5 QP)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = StateStore(tmp_path / "pr20_goblin.db")
    store.register_account(
        account_id="acc_02",
        auth_mode="LEGACY_CREDENTIALS",
        credential_ref="vault://accounts/acc_02/auth",
        proxy_ref="vault://proxies/direct",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=202,
        initial_status=AccountStatus.READY,
    )
    lease = store.acquire_session_lease(
        account_id="acc_02",
        slot_id=0,
        task_id="stage1a_f2p_quests_10qp",
        now_utc=datetime(2026, 10, 30, 18, 0, tzinfo=timezone.utc),
        bypass_circadian=True,
    )
    adapter = FakeGameAdapter(
        account_id="acc_02",
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        skills={"hitpoints": 10},
        combat_level=3,
        quest_points=9,
        total_level=35,
        playtime_minutes=300,
        # The bot's own earnings from Stage 2 — never an operator hand-out.
        bank_items=[{"item_id": COINS_ID, "name": "Coins", "quantity": 10_000, "noted": False}],
        inventory_items=[],
        walk_speed_tiles_per_tick=6,
        interaction_grants=_goblin_world,
    )
    adapter.varps.update({29: 2, 179: 21, 144: 100, 62: 0})
    adapter.run_energy = 100
    adapter.run_enabled = True
    adapter.position = {"x": 3164, "y": 3487, "plane": 0}

    gate = GameAdapterGate(store, adapter)
    gate.bind_to_lease(
        account_id="acc_02",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
    )
    kernel = ExecutionEngineKernel(
        gate=gate,
        account_id="acc_02",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3, quest_points=9),
        lifecycle=SessionLifecycleManager(),
        watchdog=ReactiveSafetyWatchdog(),
        navigation=NavigationEngine(ConstrainedWaypointGraph()),
    )
    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    combines: list[tuple[int, int]] = []
    original = adapter.execute_raw_command

    def _record(command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if command_type == "USE_ITEM_ON_ITEM":
            combines.append((int(payload["source_item_id"]), int(payload["target_item_id"])))
        return original(command_type, payload)

    adapter.execute_raw_command = _record  # type: ignore[method-assign]
    try:
        completed = False
        for tick in range(1, 3000):
            result = kernel.step_tick(bot, now_monotonic=float(tick))
            directive = result.task_directive
            if directive is not None:
                if directive.directive_type == DirectiveType.ESCALATE_HUMAN:
                    raise AssertionError(f"halted at tick {tick}: {directive.reason}")
                if directive.directive_type == DirectiveType.COMPLETE_TASK:
                    completed = True
                    break
        assert adapter.varps[62] == 6, f"varps={adapter.varps} inv={adapter.inventory_items}"
        assert completed, f"no COMPLETE_TASK (inv={adapter.inventory_items}, pos={adapter.position})"
        assert combines[:3] == [
            (RED_DYE_ID, YELLOW_DYE_ID),
            (ORANGE_DYE_ID, GOBLIN_MAIL_ID),
            (BLUE_DYE_ID, GOBLIN_MAIL_ID),
        ], combines
        assert adapter.quest_points == 14
    finally:
        store.close()
