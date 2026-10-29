"""`PR-20` Part 3.2 — Romeo & Juliet: the 5-stage Varp-144 dialogue FSM (T112..T114).

Live stages (`Varp 144`):

    0  -> start (Romeo, Varrock square)          cadava berries gathered first
    10 -> Juliet (plane 1, via the ground stairs)
    20 -> Romeo again
    30 -> Father Lawrence (Varrock East church)
    40 -> Apothecary (cadava berries -> Cadava potion 756)
    50 -> Juliet (hand over the potion, still plane 1)
    60 -> Romeo -> 100 (quest complete, +5 QP)

- T112: every stage targets the right NPC on the right plane, and ground stages never ask the
        navigation engine for a plane-1 destination (or vice versa).
- T113: the cadava berries are re-checked up to the Apothecary hand-off — losing them to a death or
        a relog no longer dead-ends the quest.
- T114: kernel run end-to-end to `Varp 144 = 100`, `+5 QP`, `COMPLETE_TASK`.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from osrs_ee.bots.base import DirectiveType, TickContext
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.supply_procurement import CADAVA_BERRIES_ID
from osrs_ee.core import ExecutionEngineKernel, ReactiveSafetyWatchdog, SessionLifecycleManager
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph, NavigationEngine
from osrs_ee.navigation.waypoint_graph import WorldPoint
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.state import AccountStatus, StateStore

SQUARE = WorldPoint(3211, 3422, 0)
CHURCH = WorldPoint(3253, 3482, 0)
APOTHECARY = WorldPoint(3195, 3405, 0)
JULIET = WorldPoint(3158, 3427, 1)
POTION_ID = 756


def _bot() -> TradeUnlockQuestsTaskModule:
    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    # Isolate the FSM: the bank audit / coin float are exercised by T107, T114 and T118.
    bot._bank_audit_done = True
    bot._coin_float_resolved = True
    return bot


def _ctx(
    pos: WorldPoint,
    *,
    romeo_state: int,
    inventory: tuple[dict[str, Any], ...] = (),
) -> TickContext:
    return TickContext(
        game_tick=7,
        position=pos,
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=inventory,
        varps={29: 2, 179: 21, 144: romeo_state, 62: 6},
        quest_points=4,
        total_level=35,
    )


BERRIES = ({"item_id": CADAVA_BERRIES_ID, "name": "Cadava berries", "quantity": 1},)
STAIRS_UP = WorldPoint(3156, 3435, 1)


def _is_descending(directive: Any) -> bool:
    """Either the final climb, or the short walk to the upstairs stair tile that precedes it."""
    if directive.directive_type == DirectiveType.INTERACT_OBJECT:
        return directive.payload.get("action") == "Climb-down"
    if directive.directive_type == DirectiveType.NAVIGATE_TO:
        return getattr(directive.destination, "label", "") == "JULIET_UPSTAIRS"
    return False


# ---------------------------------------------------------------------------
# T112 — the FSM walks every stage on the right plane
# ---------------------------------------------------------------------------
def test_t112_every_romeo_stage_targets_the_right_npc_and_plane() -> None:
    """T112 (`PR-20`): stage -> (directive, NPC/object) mapping, verified stage by stage."""
    bot = _bot()

    # 0 -> Romeo at the Varrock square (berries already in the bag).
    d = bot.evaluate_tick(_ctx(SQUARE, romeo_state=0, inventory=BERRIES))
    assert d.directive_type == DirectiveType.INTERACT_NPC and d.payload["name"] == "Romeo", d

    # 10 -> Juliet lives upstairs: the ground floor must produce a climb, never a plane-1 walk.
    d = bot.evaluate_tick(_ctx(SQUARE, romeo_state=10, inventory=BERRIES))
    assert d.directive_type == DirectiveType.NAVIGATE_TO and d.destination == "VARROCK_JULIET_STAIRS", d
    d = bot.evaluate_tick(_ctx(WorldPoint(3157, 3435, 0), romeo_state=10, inventory=BERRIES))
    assert d.directive_type == DirectiveType.INTERACT_OBJECT and d.payload["action"] == "Climb-up", d
    d = bot.evaluate_tick(_ctx(JULIET, romeo_state=10, inventory=BERRIES))
    assert d.directive_type == DirectiveType.INTERACT_NPC and d.payload["name"] == "Juliet", d

    # 20 -> back to Romeo; a bot left upstairs must descend first (plane-safe, PR-20).
    d = bot.evaluate_tick(_ctx(JULIET, romeo_state=20, inventory=BERRIES))
    assert _is_descending(d), d
    d = bot.evaluate_tick(_ctx(STAIRS_UP, romeo_state=20, inventory=BERRIES))
    assert d.directive_type == DirectiveType.INTERACT_OBJECT and d.payload["action"] == "Climb-down", d
    d = bot.evaluate_tick(_ctx(SQUARE, romeo_state=20, inventory=BERRIES))
    assert d.directive_type == DirectiveType.INTERACT_NPC and d.payload["name"] == "Romeo", d

    # 30 -> Father Lawrence at the East church.
    d = bot.evaluate_tick(_ctx(CHURCH, romeo_state=30, inventory=BERRIES))
    assert d.directive_type == DirectiveType.INTERACT_NPC and d.payload["name"] == "Father Lawrence", d

    # 40 -> the Apothecary (consumes the berries).
    d = bot.evaluate_tick(_ctx(APOTHECARY, romeo_state=40, inventory=BERRIES))
    assert d.directive_type == DirectiveType.INTERACT_NPC and d.payload["name"] == "Apothecary", d

    # 50 + Cadava potion -> Juliet again, upstairs.
    potion = ({"item_id": POTION_ID, "name": "Cadava potion", "quantity": 1},)
    d = bot.evaluate_tick(_ctx(WorldPoint(3157, 3435, 0), romeo_state=50, inventory=potion))
    assert d.directive_type == DirectiveType.INTERACT_OBJECT and d.payload["action"] == "Climb-up", d
    d = bot.evaluate_tick(_ctx(JULIET, romeo_state=50, inventory=potion))
    assert d.directive_type == DirectiveType.INTERACT_NPC and d.payload["name"] == "Juliet", d

    # 60 -> Romeo for the final scene; upstairs again means descend first.
    d = bot.evaluate_tick(_ctx(JULIET, romeo_state=60, inventory=potion))
    assert _is_descending(d), d
    d = bot.evaluate_tick(_ctx(SQUARE, romeo_state=60, inventory=potion))
    assert d.directive_type == DirectiveType.INTERACT_NPC and d.payload["name"] == "Romeo", d


# ---------------------------------------------------------------------------
# T113 — berries are re-acquired until the Apothecary takes them
# ---------------------------------------------------------------------------
def test_t113_lost_cadava_berries_are_reacquired_before_the_apothecary() -> None:
    """T113 (`PR-20`): a death or a relog can empty the bag. From stage 30/40 the engine must go and
    pick fresh berries instead of walking to the Apothecary empty-handed (which burns the attempt
    budget and strands the quest)."""
    bot = _bot()
    d = bot.evaluate_tick(_ctx(CHURCH, romeo_state=30, inventory=()))
    assert d.directive_type == DirectiveType.NAVIGATE_TO, d
    assert d.destination == "VARROCK_CADAVA_BUSHES", d

    bot2 = _bot()
    d = bot2.evaluate_tick(_ctx(APOTHECARY, romeo_state=40, inventory=()))
    assert d.directive_type == DirectiveType.NAVIGATE_TO, d
    assert d.destination == "VARROCK_CADAVA_BUSHES", d

    # Past the hand-off (50+) the berries are irrelevant: never gather them again.
    bot3 = _bot()
    potion = ({"item_id": POTION_ID, "name": "Cadava potion", "quantity": 1},)
    d = bot3.evaluate_tick(_ctx(JULIET, romeo_state=50, inventory=potion))
    assert not (d.directive_type == DirectiveType.NAVIGATE_TO and d.destination == "VARROCK_CADAVA_BUSHES"), d


# ---------------------------------------------------------------------------
# T114 — kernel end-to-end
# ---------------------------------------------------------------------------
def _romeo_world(command_type: str, payload: dict[str, Any], adapter: FakeGameAdapter) -> None:
    name = str(payload.get("name", ""))
    action = str(payload.get("action", ""))
    state = int(adapter.varps.get(144, 0))
    if command_type == "INTERACT_NPC":
        if name == "Romeo":
            if state == 0:
                adapter.varps[144] = 10
            elif state == 20:
                adapter.varps[144] = 30
            elif state == 60:
                adapter.varps[144] = 100
                adapter.quest_points += 5
        elif name == "Juliet":
            assert adapter.position["plane"] == 1, "Juliet is upstairs — the bot must climb first"
            if state == 10:
                adapter.varps[144] = 20
            elif state == 50:
                adapter.varps[144] = 60
        elif name == "Father Lawrence":
            if state == 30:
                adapter.varps[144] = 40
        elif name == "Apothecary":
            if state == 40:
                for it in list(adapter.inventory_items):
                    if int(it["item_id"]) == CADAVA_BERRIES_ID:
                        it["quantity"] -= 1
                        if it["quantity"] <= 0:
                            adapter.inventory_items.remove(it)
                        break
                adapter._add_to_inventory(
                    {"item_id": POTION_ID, "name": "Cadava potion", "quantity": 1, "noted": False}
                )
                adapter.varps[144] = 50
    elif command_type == "INTERACT_OBJECT":
        if name in ("Staircase", "Stairs", "Ladder"):
            cur = dict(adapter.position)
            if action == "Climb-up":
                cur["plane"] = 1
            else:
                cur["plane"] = 0
            adapter.position = cur
        elif name in ("Cadava bush", "Cadava berry bush", "Cadava bush (wild)"):
            adapter._add_to_inventory(
                {"item_id": CADAVA_BERRIES_ID, "name": "Cadava berries", "quantity": 1, "noted": False}
            )


def test_t114_kernel_run_completes_romeo_and_juliet(tmp_path: Path) -> None:
    """T114 (`PR-20`): a full kernel run must walk the whole 5-stage FSM, gather the berries in the
    Varrock wilderness, craft the potion with the Apothecary and finish at `Varp 144 = 100` (+5 QP)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = StateStore(tmp_path / "pr20_romeo.db")
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
        now_utc=datetime(2026, 10, 30, 15, 0, tzinfo=timezone.utc),
        bypass_circadian=True,
    )
    adapter = FakeGameAdapter(
        account_id="acc_02",
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        skills={"hitpoints": 10},
        combat_level=3,
        quest_points=4,
        total_level=35,
        playtime_minutes=240,
        bank_items=[],
        inventory_items=[],
        walk_speed_tiles_per_tick=4,
        interaction_grants=_romeo_world,
    )
    adapter.varps.update({29: 2, 179: 21, 144: 0, 62: 6})
    adapter.run_energy = 100
    adapter.run_enabled = True
    adapter.position = {"x": 3211, "y": 3422, "plane": 0}

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
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3, quest_points=4),
        lifecycle=SessionLifecycleManager(),
        watchdog=ReactiveSafetyWatchdog(),
        navigation=NavigationEngine(ConstrainedWaypointGraph()),
    )
    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    try:
        completed = False
        visited_plane_1 = False
        for tick in range(1, 1500):
            result = kernel.step_tick(bot, now_monotonic=float(tick))
            if adapter.position["plane"] == 1:
                visited_plane_1 = True
            directive = result.task_directive
            if directive is not None:
                if directive.directive_type == DirectiveType.ESCALATE_HUMAN:
                    raise AssertionError(f"halted at tick {tick}: {directive.reason}")
                if directive.directive_type == DirectiveType.COMPLETE_TASK:
                    completed = True
                    break
        assert adapter.varps[144] == 100, f"varps={adapter.varps} inv={adapter.inventory_items}"
        assert completed, f"no COMPLETE_TASK (pos={adapter.position})"
        assert visited_plane_1, "the bot never visited Juliet upstairs"
        assert adapter.quest_points == 9
    finally:
        store.close()
