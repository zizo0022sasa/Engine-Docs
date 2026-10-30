"""`PR-20.2` — Make-X production interface & the live spinning-wheel truth (T128..T133).

Live ground truth captured during the `PR-20.1` field run (`acc_02` @ 8081):

    Spinning wheel   id 14889   at (3209, 3212) plane 1
    Make-X interface groupId 270, childId 15 = "Ball of wool"
    20 wool -> 20 balls took ~36 s of server-side production (make-all)

Two things did NOT match the code:

1. **`Spin` produces nothing.** It opens the Make-X interface. The old recipe assumed the object
   interaction itself yielded all 20 balls, so a bot without a human clicker would stand in front of
   an open interface until `SUPPLY_STEP_STALLED:Ball of wool:...`.
2. **The live completion needed a MANUAL click.** The field operator pressed `270:15` himself. That
   is a `Zero-Touch` violation even though the quest point is genuinely earned — this release makes
   the engine perform that click, so the next run is hands-free.

- T128: the wheel is pinned to live object id `14889` and the live plane-1 tile.
- T129: the chain is `Climb-up -> Spin (id 14889) -> CLICK_WIDGET {270, 15, "Ball of wool"}`.
- T130: make-all semantics — one click, 20 balls arrive over time, no escalation, no click spam.
- T131: self-heal — if the interface is closed mid-production the engine re-opens it (`Spin` again).
- T132: kernel end-to-end: a world that REFUSES to produce on `Spin` alone still reaches
        `Varp 179 = 21`, proving the run no longer needs an operator.
- T133: work sites — the cadava/redberry clusters (the same tether class as incident #7) are bounded
        by geometry, not by guesswork.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from osrs_ee.bots.base import DirectiveType, TickContext
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.supply_procurement import (
    BALL_OF_WOOL_ID,
    CADAVA_BERRIES_ID,
    REDBERRY_ID,
    SHEARS_ID,
    SPINNING_WHEEL_OBJECT_ID,
    SUPPLY_RECIPES,
    WOOL_ID,
    ZeroTouchSupplyEngine,
)
from osrs_ee.core import ExecutionEngineKernel, ReactiveSafetyWatchdog, SessionLifecycleManager
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph, NavigationEngine
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.state import AccountStatus, StateStore

WHEEL = DEFAULT_WORLD_NODES["LUMBRIDGE_SPINNING_WHEEL"]
STAIRS = DEFAULT_WORLD_NODES["LUMBRIDGE_CASTLE_NORTH_STAIRS"]
MAKE_X_GROUP = 270
MAKE_X_CHILD = 15


def _ctx(pos: WorldPoint, inventory: list[dict[str, Any]], tick: int = 1) -> TickContext:
    return TickContext(
        game_tick=tick,
        position=pos,
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=tuple(inventory),
        total_level=40,
    )


def _wool(qty: int = 20) -> list[dict[str, Any]]:
    return [{"item_id": WOOL_ID, "name": "Wool", "quantity": qty}]


def _balls(inventory: list[dict[str, Any]]) -> int:
    return sum(int(i["quantity"]) for i in inventory if int(i["item_id"]) == BALL_OF_WOOL_ID)


# ---------------------------------------------------------------------------
# T128 — the live object id
# ---------------------------------------------------------------------------
def test_t128_spinning_wheel_is_pinned_to_the_live_probed_object_id() -> None:
    """T128 (`PR-20.2`): name-matching alone was never exercised live. The wheel is now addressed by
    its probe-verified id `14889` on the live `(3209, 3212)` plane-1 tile."""
    step = SUPPLY_RECIPES[BALL_OF_WOOL_ID].steps[1]
    assert step.object_id == SPINNING_WHEEL_OBJECT_ID == 14889
    assert step.action == "Spin"
    assert (step.area.x, step.area.y, step.area.plane) == (3209, 3212, 1)
    assert (WHEEL.x, WHEEL.y, WHEEL.plane) == (3209, 3212, 1)

    directive = ZeroTouchSupplyEngine().ensure(_ctx(WHEEL, _wool()), BALL_OF_WOOL_ID, 20)
    assert directive is not None and directive.directive_type == DirectiveType.INTERACT_OBJECT
    assert directive.payload == {"name": "Spinning wheel", "action": "Spin", "id": 14889}, directive.payload


# ---------------------------------------------------------------------------
# T129 — climb -> open the interface -> click the product
# ---------------------------------------------------------------------------
def test_t129_the_chain_opens_make_x_and_then_clicks_the_product() -> None:
    """T129 (`PR-20.2`): `Spin` must NOT be relied on to produce. The very next directive after it is
    a `CLICK_WIDGET` carrying the live interface coordinates."""
    engine = ZeroTouchSupplyEngine()
    inventory = _wool()

    # standing at the stairs, wool in the bag -> climb
    directive = engine.ensure(_ctx(STAIRS, inventory), BALL_OF_WOOL_ID, 20)
    assert directive is not None and directive.payload.get("action") == "Climb-up"

    # now on the wheel floor -> open the Make-X interface
    directive = engine.ensure(_ctx(WHEEL, inventory, 2), BALL_OF_WOOL_ID, 20)
    assert directive is not None and directive.directive_type == DirectiveType.INTERACT_OBJECT
    assert directive.payload["id"] == 14889

    # the interface is open but nothing has been produced yet -> the production click
    directive = engine.ensure(_ctx(WHEEL, inventory, 3), BALL_OF_WOOL_ID, 20)
    assert directive is not None, "the engine must not consider the wool spun just because Spin fired"
    assert directive.directive_type == DirectiveType.CLICK_WIDGET, directive
    assert directive.payload["groupId"] == MAKE_X_GROUP
    assert directive.payload["childId"] == MAKE_X_CHILD
    assert directive.payload["text"] == "Ball of wool"


# ---------------------------------------------------------------------------
# T130 — make-all semantics
# ---------------------------------------------------------------------------
def test_t130_make_all_produces_twenty_balls_without_click_spam_or_escalation() -> None:
    """T130 (`PR-20.2`): 20 balls take ~36 s server-side. The engine clicks once, holds off for
    `redispatch_hold` ticks, and never escalates while the ball count keeps rising."""
    engine = ZeroTouchSupplyEngine()
    produced = 0
    clicks = 0
    clicked = False
    for tick in range(1, 400):
        inventory: list[dict[str, Any]] = []
        if produced:
            inventory.append({"item_id": BALL_OF_WOOL_ID, "name": "Ball of wool", "quantity": produced})
        if produced < 20:
            inventory.append({"item_id": WOOL_ID, "name": "Wool", "quantity": 20 - produced})
        directive = engine.ensure(_ctx(WHEEL, inventory, tick), BALL_OF_WOOL_ID, 20)
        if directive is None:
            break
        assert directive.directive_type != DirectiveType.ESCALATE_HUMAN, directive.reason
        # Only three kinds are legitimate once the wheel is reached: open it (once), click the
        # product, or wait — never a walk back and never a re-open storm.
        assert directive.directive_type in (
            DirectiveType.INTERACT_OBJECT,
            DirectiveType.CLICK_WIDGET,
            DirectiveType.WAIT_NEXT_TICK,
        ), directive
        if directive.directive_type == DirectiveType.INTERACT_OBJECT:
            assert directive.payload.get("action") == "Spin"
        if directive.directive_type == DirectiveType.CLICK_WIDGET:
            clicks += 1
            clicked = True
        # The server produces in the background once the interface has been used (1 ball / 3 ticks).
        if clicked and produced < 20 and tick % 3 == 0:
            produced += 1

    assert produced == 20
    assert 1 <= clicks <= 12, f"clicked {clicks} times — that is command spam"


# ---------------------------------------------------------------------------
# T131 — self-heal when the interface closes early
# ---------------------------------------------------------------------------
def test_t131_a_closed_interface_is_reopened_instead_of_dead_ending() -> None:
    """T131 (`PR-20.2`): if the Make-X window closes mid-production (death, region hop, a stray
    click), clicking `270:15` does nothing. After half the budget the engine forgets the
    "interface is open" latch and re-issues `Spin` — it must never dead-end in front of a closed
    window."""
    engine = ZeroTouchSupplyEngine()
    produced = 0
    reopened = 0
    for tick in range(1, 6000):
        inventory: list[dict[str, Any]] = []
        if produced:
            inventory.append({"item_id": BALL_OF_WOOL_ID, "name": "Ball of wool", "quantity": produced})
        if produced < 20:
            inventory.append({"item_id": WOOL_ID, "name": "Wool", "quantity": 20 - produced})
        directive = engine.ensure(_ctx(WHEEL, inventory, tick), BALL_OF_WOOL_ID, 20)
        if directive is None:
            break
        assert directive.directive_type != DirectiveType.ESCALATE_HUMAN, (tick, directive.reason)
        if directive.directive_type == DirectiveType.INTERACT_OBJECT and directive.payload.get("action") == "Spin":
            reopened += 1
            # Each re-open makes 5 balls before the interface drops again; clicks alone make nothing.
            produced = min(20, produced + 5)

    assert produced == 20, f"stuck at {produced} balls — the engine dead-ended in front of a closed window"
    assert reopened >= 2, f"the interface was only opened {reopened} time(s)"


# ---------------------------------------------------------------------------
# T132 — kernel end-to-end with a world that refuses the old assumption
# ---------------------------------------------------------------------------
def _strict_make_x_world(command_type: str, payload: dict[str, Any], adapter: FakeGameAdapter) -> None:
    """`Spin` ALONE never produces. Only the Make-X click does. This is the live client."""
    name = str(payload.get("name", ""))
    action = str(payload.get("action", ""))
    if command_type == "INTERACT_NPC":
        if name == "Sheep":
            if any(int(i["item_id"]) == SHEARS_ID for i in adapter.inventory_items):
                adapter._add_to_inventory({"item_id": WOOL_ID, "name": "Wool", "quantity": 1, "noted": False})
        elif name == "Fred the Farmer":
            if int(adapter.varps.get(179, 0)) == 0:
                adapter.varps[179] = 1
            elif any(int(i["item_id"]) == BALL_OF_WOOL_ID for i in adapter.inventory_items):
                adapter.inventory_items.clear()
                adapter.varps[179] = 21
                adapter.quest_points += 1
    elif command_type == "INTERACT_OBJECT":
        if name in ("Staircase", "Stairs", "Ladder"):
            cur = dict(adapter.position)
            cur["plane"] = 1 if action == "Climb-up" else 0
            adapter.position = cur
        elif name == "Spinning wheel":
            # NOTE: deliberately produces NOTHING — exactly like the live client.
            assert payload.get("id") == SPINNING_WHEEL_OBJECT_ID, payload
    elif command_type == "WIDGET_CLICK":
        if int(payload.get("groupId", 0)) == MAKE_X_GROUP and int(payload.get("childId", 0)) == MAKE_X_CHILD:
            for it in list(adapter.inventory_items):
                if int(it["item_id"]) == WOOL_ID:
                    adapter.inventory_items.remove(it)
                    adapter._add_to_inventory(
                        {"item_id": BALL_OF_WOOL_ID, "name": "Ball of wool", "quantity": int(it["quantity"]), "noted": False}
                    )


def test_t132_kernel_run_spins_twenty_balls_without_any_operator_click(tmp_path: Path) -> None:
    """T132 (`PR-20.2`): the regression that matters — against a client where `Spin` yields nothing
    on its own, the bundle must still reach `Varp 179 = 21` purely on its own directives."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = StateStore(tmp_path / "pr20_2_makex.db")
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
        now_utc=datetime(2026, 10, 31, 16, 0, tzinfo=timezone.utc),
        bypass_circadian=True,
    )
    adapter = FakeGameAdapter(
        account_id="acc_02",
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        skills={"hitpoints": 10},
        combat_level=3,
        quest_points=3,
        total_level=40,
        playtime_minutes=300,
        bank_items=[],
        inventory_items=[],
        ground_items=[{"item_id": SHEARS_ID, "name": "Shears", "quantity": 1}],
        walk_speed_tiles_per_tick=6,
        interaction_grants=_strict_make_x_world,
    )
    adapter.varps.update({29: 2, 179: 0, 144: 100, 62: 6})
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
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3, quest_points=3),
        lifecycle=SessionLifecycleManager(),
        watchdog=ReactiveSafetyWatchdog(),
        navigation=NavigationEngine(ConstrainedWaypointGraph()),
    )
    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    widgets: list[tuple[int, int]] = []
    original = adapter.execute_raw_command

    def _record(command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if command_type == "WIDGET_CLICK":
            widgets.append((int(payload.get("groupId", -1)), int(payload.get("childId", -1))))
        return original(command_type, payload)

    adapter.execute_raw_command = _record  # type: ignore[method-assign]
    try:
        completed = False
        for tick in range(1, 4000):
            result = kernel.step_tick(bot, now_monotonic=float(tick))
            directive = result.task_directive
            if directive is not None:
                if directive.directive_type == DirectiveType.ESCALATE_HUMAN:
                    raise AssertionError(f"halted at tick {tick}: {directive.reason}")
                if directive.directive_type == DirectiveType.COMPLETE_TASK:
                    completed = True
                    break
        assert completed, f"no COMPLETE_TASK (inv={adapter.inventory_items}, pos={adapter.position})"
        assert adapter.varps[179] == 21, adapter.varps
        assert (MAKE_X_GROUP, MAKE_X_CHILD) in widgets, widgets
        assert widgets.count((MAKE_X_GROUP, MAKE_X_CHILD)) <= 6, f"click spam: {widgets}"
    finally:
        store.close()


# ---------------------------------------------------------------------------
# T133 — bush clusters are areas too (pre-empting incident #8)
# ---------------------------------------------------------------------------
def test_t133_bush_clusters_are_bounded_by_their_documented_rectangles() -> None:
    """T133 (`PR-20.2`): the cadava bush cluster `(3264,3366)-(3277,3374)` and the two redberry bushes
    reproduce the sheep-pen geometry exactly — the far corner of the cluster is 9 tiles from the
    anchor while the global tolerance is 3. Both carry a work-site radius derived from the documented
    rectangle, so `Romeo & Juliet` + `Goblin Diplomacy` cannot tether the way the sheep pen did."""
    cadava = SUPPLY_RECIPES[CADAVA_BERRIES_ID].steps[0]
    redberry = SUPPLY_RECIPES[REDBERRY_ID].steps[0]
    anchor_cadava = DEFAULT_WORLD_NODES["VARROCK_CADAVA_BUSHES"]
    anchor_redberry = DEFAULT_WORLD_NODES["VARROCK_REDBERRY_BUSHES"]

    for corner in ((3264, 3366), (3277, 3366), (3277, 3374), (3264, 3374)):
        distance = max(abs(corner[0] - anchor_cadava.x), abs(corner[1] - anchor_cadava.y))
        assert distance <= (cadava.area_radius or 0), (corner, distance, cadava.area_radius)
    assert redberry.area_radius and redberry.area_radius >= 5

    # Behaviourally: standing at the far corner of the cluster must pick, not walk back.
    directive = ZeroTouchSupplyEngine().ensure(_ctx(WorldPoint(3277, 3374, 0), []), CADAVA_BERRIES_ID, 1)
    assert directive is not None and directive.directive_type == DirectiveType.INTERACT_OBJECT, directive
    # ...while a bot far away is still routed (the radius is not a licence to pick from anywhere).
    directive = ZeroTouchSupplyEngine().ensure(_ctx(WorldPoint(3164, 3487, 0), []), CADAVA_BERRIES_ID, 1)
    assert directive is not None and directive.directive_type == DirectiveType.NAVIGATE_TO
