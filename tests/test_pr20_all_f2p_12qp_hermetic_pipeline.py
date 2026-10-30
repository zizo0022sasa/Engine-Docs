"""`PR-20` Part 4 — the whole Stage 1A bundle, end to end (T119..T121).

One kernel run, an EMPTY bag, an EMPTY supplies bank, zero operator input:

    Cook's Assistant (1 QP)  egg + bucket + milk + pot + grain -> flour -> Cook
    Sheep Shearer    (1 QP)  shears -> 20 wool -> plane-1 spin wheel -> 20 balls -> Fred
    Romeo & Juliet   (5 QP)  cadava berries -> 5-stage FSM across two floors
    Goblin Diplomacy (5 QP)  paid dyes (own banked GP) + 3 crates + 2 combines -> generals
    --------------------------------------------------------------------------------------
    TOTAL                    12 QP -> anchor at the GE -> COMPLETE_TASK

- T119: the full `0 -> 12 QP` pipeline succeeds with zero escalations.
- T120: the QP latch never fires on QP the account already carried (baseline guard) — otherwise an
        account that banked QP in an earlier stage would "complete" the bundle without doing it.
- T121: the Zero-Touch audit of the same run: the only thing ever taken from the bank is the bot's
        own GP; every other supply was produced in-world (pick / search / shear / NPC craft / combine).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from osrs_ee.bots.base import DirectiveType, TickContext
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.supply_procurement import (
    BALL_OF_WOOL_ID,
    BLUE_DYE_ID,
    BLUE_GOBLIN_MAIL_ID,
    BUCKET_ID,
    BUCKET_OF_MILK_ID,
    CADAVA_BERRIES_ID,
    COINS_ID,
    EGG_ID,
    GOBLIN_MAIL_ID,
    GRAIN_ID,
    ONION_ID,
    ORANGE_DYE_ID,
    ORANGE_GOBLIN_MAIL_ID,
    POT_ID,
    POT_OF_FLOUR_ID,
    RED_DYE_ID,
    REDBERRY_ID,
    SHEARS_ID,
    WOAD_LEAF_ID,
    WOOL_ID,
    YELLOW_DYE_ID,
)
from osrs_ee.core import ExecutionEngineKernel, ReactiveSafetyWatchdog, SessionLifecycleManager
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph, NavigationEngine
from osrs_ee.navigation.waypoint_graph import WorldPoint
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.state import AccountStatus, StateStore

POTION_ID = 756
SURFACE_ANCHOR = {"x": 3164, "y": 3487, "plane": 0}

_RUN_CACHE: dict[str, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# The simulated OSRS world: it only ever reacts to what the bot ACTUALLY does.
# ---------------------------------------------------------------------------
def _world(command_type: str, payload: dict[str, Any], adapter: FakeGameAdapter) -> None:
    name = str(payload.get("name", ""))
    action = str(payload.get("action", ""))

    def take(item_id: int, qty: int = 1) -> None:
        for it in list(adapter.inventory_items):
            if int(it["item_id"]) == item_id and int(it["quantity"]) >= qty:
                it["quantity"] -= qty
                if it["quantity"] <= 0:
                    adapter.inventory_items.remove(it)
                return

    def add(item_id: int, label: str, qty: int = 1) -> None:
        adapter._add_to_inventory({"item_id": item_id, "name": label, "quantity": qty, "noted": False})

    def has(item_id: int, qty: int = 1) -> bool:
        return sum(int(i["quantity"]) for i in adapter.inventory_items if int(i["item_id"]) == item_id) >= qty

    inv_ids = {int(i["item_id"]) for i in adapter.inventory_items}

    if command_type == "INTERACT_NPC":
        if name == "Cook":
            if has(EGG_ID) and has(BUCKET_OF_MILK_ID) and has(POT_OF_FLOUR_ID):
                adapter.varps[29] = 2
                adapter.quest_points += 1
        elif name == "Sheep":
            if has(SHEARS_ID):
                add(WOOL_ID, "Wool")
        elif name == "Fred the Farmer":
            if int(adapter.varps.get(179, 0)) == 0:
                adapter.varps[179] = 1
            elif has(BALL_OF_WOOL_ID, 20):
                take(BALL_OF_WOOL_ID, 20)
                adapter.varps[179] = 21
                adapter.quest_points += 1
        elif name == "Romeo":
            state = int(adapter.varps.get(144, 0))
            if state == 0:
                adapter.varps[144] = 10
            elif state == 20:
                adapter.varps[144] = 30
            elif state == 60:
                adapter.varps[144] = 100
                adapter.quest_points += 5
        elif name == "Juliet":
            assert adapter.position["plane"] == 1, "Juliet is upstairs; the bot must climb first"
            state = int(adapter.varps.get(144, 0))
            if state == 10:
                adapter.varps[144] = 20
            elif state == 50 and has(POTION_ID):
                take(POTION_ID)
                adapter.varps[144] = 60
        elif name == "Father Lawrence":
            if int(adapter.varps.get(144, 0)) == 30:
                adapter.varps[144] = 40
        elif name == "Apothecary":
            if int(adapter.varps.get(144, 0)) == 40 and has(CADAVA_BERRIES_ID):
                take(CADAVA_BERRIES_ID)
                add(POTION_ID, "Cadava potion")
                adapter.varps[144] = 50
        elif name == "Wyson the gardener":
            if has(COINS_ID, 20):
                take(COINS_ID, 20)
                add(WOAD_LEAF_ID, "Woad leaves", 2)
        elif name == "Aggie":
            if not has(COINS_ID, 5):
                return
            if has(WOAD_LEAF_ID, 2):
                take(WOAD_LEAF_ID, 2)
                take(COINS_ID, 5)
                add(BLUE_DYE_ID, "Blue dye")
            elif has(REDBERRY_ID, 3):
                take(REDBERRY_ID, 3)
                take(COINS_ID, 5)
                add(RED_DYE_ID, "Red dye")
            elif has(ONION_ID, 2):
                take(ONION_ID, 2)
                take(COINS_ID, 5)
                add(YELLOW_DYE_ID, "Yellow dye")
        elif name == "General Bentnoze":
            if all(has(i) for i in (ORANGE_GOBLIN_MAIL_ID, BLUE_GOBLIN_MAIL_ID, GOBLIN_MAIL_ID)):
                for i in (ORANGE_GOBLIN_MAIL_ID, BLUE_GOBLIN_MAIL_ID, GOBLIN_MAIL_ID):
                    take(i)
                adapter.varps[62] = 6
                adapter.quest_points += 5

    elif command_type == "INTERACT_OBJECT":
        if adapter.position["y"] > 5000:
            # Anything interacted with while underground IS the cellar staircase out.
            adapter.position = dict(SURFACE_ANCHOR) | {"x": 3206, "y": 3214}
            return
        if name == "Dairy cow":
            if has(BUCKET_ID):
                take(BUCKET_ID)
                add(BUCKET_OF_MILK_ID, "Bucket of milk")
        elif name == "Wheat":
            add(GRAIN_ID, "Grain")
        elif name in ("Large door", "Door"):
            pass
        elif name in ("Ladder", "Staircase", "Stairs"):
            cur = dict(adapter.position)
            near_mill = 3155 <= int(cur["x"]) <= 3180 and 3290 <= int(cur["y"]) <= 3320
            if action == "Climb-down" and int(cur["plane"]) == 0:
                # The Lumbridge kitchen staircase into the cellar (a different map region).
                adapter.position = {"x": 3216, "y": 9625, "plane": 0}
            elif action == "Climb-up":
                cur["plane"] = 2 if near_mill else 1
                adapter.position = cur
            else:
                cur["plane"] = 0
                adapter.position = cur
        elif name == "Hopper":
            take(GRAIN_ID)
        elif name == "Hopper controls":
            pass
        elif name == "Flour bin":
            if has(POT_ID):
                take(POT_ID)
                add(POT_OF_FLOUR_ID, "Pot of flour")
        # `PR-20.2`: `Spin` opens the Make-X interface only — production happens in WIDGET_CLICK.
        elif name in ("Cadava bush", "Cadava berry bush", "Cadava bush (wild)"):
            add(CADAVA_BERRIES_ID, "Cadava berries")
        elif name == "Redberry bush":
            add(REDBERRY_ID, "Redberries", 3)
        elif name in ("Onion", "Onions"):
            add(ONION_ID, "Onion", 2)
        elif name == "Crate":
            add(GOBLIN_MAIL_ID, "Goblin mail")

    elif command_type == "WIDGET_CLICK":
        # `PR-20.2`: Make-X 270:15 = ball of wool, make-all (the whole stack in one click).
        if int(payload.get("groupId", 0)) == 270 and int(payload.get("childId", 0)) == 15:
            qty = sum(int(i["quantity"]) for i in adapter.inventory_items if int(i["item_id"]) == WOOL_ID)
            if qty:
                take(WOOL_ID, qty)
                add(BALL_OF_WOOL_ID, "Ball of wool", qty)

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
        else:  # pragma: no cover - a wrong combine must fail loudly, never silently
            raise AssertionError(f"unexpected combine {sorted(pair)} (inv={sorted(inv_ids)})")


# ---------------------------------------------------------------------------
# The single full run shared by T119 and T121
# ---------------------------------------------------------------------------
def _full_run(tmp_path: Path) -> dict[str, Any]:
    if "run" in _RUN_CACHE:
        return _RUN_CACHE["run"]

    tmp_path.mkdir(parents=True, exist_ok=True)
    store = StateStore(tmp_path / "pr20_12qp.db")
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
        now_utc=datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc),
        bypass_circadian=True,
    )
    adapter = FakeGameAdapter(
        account_id="acc_02",
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        skills={"hitpoints": 10},
        combat_level=3,
        quest_points=0,           # truly from zero
        total_level=35,
        playtime_minutes=60,
        # ZERO supplies banked. The only asset is the bot's OWN earnings from Stage 2.
        bank_items=[{"item_id": COINS_ID, "name": "Coins", "quantity": 10_000, "noted": False}],
        inventory_items=[],       # ZERO supplies carried
        ground_items=[
            {"item_id": EGG_ID, "name": "Egg", "quantity": 6},
            {"item_id": POT_ID, "name": "Pot", "quantity": 3},
            {"item_id": SHEARS_ID, "name": "Shears", "quantity": 1},
            {"item_id": BUCKET_ID, "name": "Bucket", "quantity": 2},
        ],
        walk_speed_tiles_per_tick=8,
        interaction_grants=_world,
    )
    adapter.varps.update({29: 0, 179: 0, 144: 0, 62: 0})
    adapter.run_energy = 100
    adapter.run_enabled = True
    adapter.position = dict(SURFACE_ANCHOR)

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
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3, quest_points=0),
        lifecycle=SessionLifecycleManager(),
        watchdog=ReactiveSafetyWatchdog(),
        navigation=NavigationEngine(ConstrainedWaypointGraph()),
    )
    bot = TradeUnlockQuestsTaskModule(target_quest_points=10)

    dispatched: list[str] = []
    withdrawals: list[int] = []
    escalations: list[str] = []
    original = adapter.execute_raw_command

    def _record(command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if command_type == "BANK_WITHDRAW_ITEM":
            withdrawals.append(int(payload.get("item_id", -1)))
        return original(command_type, payload)

    adapter.execute_raw_command = _record  # type: ignore[method-assign]

    completed = False
    halt_tick = -1
    try:
        for tick in range(1, 12000):
            result = kernel.step_tick(bot, now_monotonic=float(tick))
            for cmd in result.dispatched_commands:
                dispatched.append(cmd)
            directive = result.task_directive
            if directive is not None:
                if directive.directive_type == DirectiveType.ESCALATE_HUMAN:
                    escalations.append(f"tick {tick}: {directive.reason}")
                    break
                if directive.directive_type == DirectiveType.COMPLETE_TASK:
                    completed = True
                    halt_tick = tick
                    break
        payload_out = {
            "completed": completed,
            "halt_tick": halt_tick,
            "escalations": escalations,
            "dispatched": dispatched,
            "withdrawals": withdrawals,
            "varps": dict(adapter.varps),
            "quest_points": adapter.quest_points,
            "position": dict(adapter.position),
            "inventory": list(adapter.inventory_items),
            "bank": list(adapter.bank_items),
            "stored_qp": int(store.get_account("acc_02")["quest_points"]),
        }
    finally:
        store.close()

    _RUN_CACHE["run"] = payload_out
    return payload_out


# ---------------------------------------------------------------------------
# T119 — 0 -> 12 QP
# ---------------------------------------------------------------------------
def test_t119_full_stage1a_bundle_reaches_twelve_quest_points_from_scratch(tmp_path: Path) -> None:
    """T119 (`PR-20`): the entire 4-quest bundle, from an empty bag and an empty supplies bank, in one
    uninterrupted kernel run — and it must never ask a human for help."""
    run = _full_run(tmp_path)
    assert not run["escalations"], run["escalations"]
    assert run["completed"], f"no COMPLETE_TASK (inv={run['inventory']}, pos={run['position']})"
    varps = run["varps"]
    assert varps[29] >= 2, varps       # Cook's Assistant
    assert varps[179] >= 21, varps     # Sheep Shearer
    assert varps[144] >= 100, varps    # Romeo & Juliet
    assert varps[62] >= 6, varps       # Goblin Diplomacy
    assert run["quest_points"] == 12, run["quest_points"]
    assert run["stored_qp"] == 12
    assert (run["position"]["x"], run["position"]["y"], run["position"]["plane"]) == (3164, 3487, 0)


# ---------------------------------------------------------------------------
# T120 — the latch must not fire on pre-existing QP
# ---------------------------------------------------------------------------
def test_t120_quest_point_latch_never_fires_on_quest_points_the_account_already_had() -> None:
    """T120 (`PR-20`): an account that already carries 10+ QP from an earlier stage must still run the
    bundle. The old latch compared total QP to the target and would have "completed" instantly."""
    bot = TradeUnlockQuestsTaskModule(target_quest_points=10)
    bot._bank_audit_done = True
    bot._coin_float_resolved = True
    ctx = TickContext(
        game_tick=1,
        position=WorldPoint(3164, 3487, 0),
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3, quest_points=12),
        inventory_items=(),
        varps={29: 0, 179: 0, 144: 0, 62: 0},
        quest_points=12,          # already above the 10 QP target
        total_level=35,
    )
    first = bot.evaluate_tick(ctx)
    assert first.directive_type != DirectiveType.COMPLETE_TASK, first

    # ...while a genuinely finished bundle still latches home immediately.
    done_ctx = TickContext(
        game_tick=2,
        position=WorldPoint(3164, 3487, 0),
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3, quest_points=12),
        inventory_items=(),
        varps={29: 2, 179: 21, 144: 100, 62: 6},
        quest_points=12,
        total_level=35,
    )
    final = bot.evaluate_tick(done_ctx)
    assert final.directive_type == DirectiveType.COMPLETE_TASK, final


# ---------------------------------------------------------------------------
# T121 — Zero-Touch audit of the run
# ---------------------------------------------------------------------------
def test_t121_the_run_is_zero_touch_and_only_spends_the_bots_own_coins(tmp_path: Path) -> None:
    """T121 (`PR-20`): the only bank withdrawals in the whole bundle are the bot's own coins, and the
    supplies were all produced in-world — picked, sheared, searched, crafted or combined."""
    run = _full_run(tmp_path)
    assert run["completed"] and not run["escalations"]
    assert set(run["withdrawals"]) <= {COINS_ID}, run["withdrawals"]
    assert run["withdrawals"], "the coin float should have been withdrawn exactly once"
    assert run["withdrawals"].count(COINS_ID) <= 2, run["withdrawals"]

    dispatched = set(run["dispatched"])
    for required in (
        "TAKE_GROUND_ITEM",       # egg / pot / shears / bucket
        "INTERACT_OBJECT",        # wheat, hopper, flour bin, spin wheel, crates, basement stairs
        "INTERACT_NPC",           # Cook, Fred, Sheep, Romeo, Juliet, Aggie, Wyson, generals
        "USE_ITEM_ON_ITEM",       # dyes and dyed mails
        "WALK_TO_WORLD_POINT",    # zero-touch travel
    ):
        assert required in dispatched, (required, sorted(dispatched))

    # Not a single operator hand-out: no supply ever entered the bag from the bank.
    assert COINS_ID not in {int(i["item_id"]) for i in run["inventory"]}, run["inventory"]
