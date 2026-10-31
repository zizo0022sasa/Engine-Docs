"""`PR-20.1` — Field Incident #7: sheep-farm tether ping-pong & livestock budget (T122..T127).

Field evidence (live `acc_02` @ 8081, `PR-20` run, tick 431):

    [tick=0330] INTERACT_NPC    | Zero-Touch supply: Shear for Wool
    [tick=0335] IN_DANGER       | Zero-Touch supply: walking to LUMBRIDGE_SHEEP_FARM for Wool
    [tick=0370] NAVIGATE_TO     | Zero-Touch supply: walking to LUMBRIDGE_SHEEP_FARM for Wool
    [tick=0420] NAVIGATE_TO     | Zero-Touch supply: walking to LUMBRIDGE_SHEEP_FARM for Wool
    [tick=0431] ESCALATE_HUMAN  | SUPPLY_STEP_STALLED:Wool:NPC:Sheep

The bot had already taken `Shears (1735)` and gained `1 Wool (1737)` — the shearing itself worked.

Root cause: `LUMBRIDGE_SHEEP_FARM` is a **single tile** `(3190, 3273)` at the corner of a ~20x20
pen, and `_at_area()` demanded `chebyshev_distance <= walk_tolerance (3)`. Fred's sheep roam the
whole pen, so the moment the bot chased one 6 tiles from the corner the engine declared it
"outside the area", walked it back to the tile, sheared again, chased again ... Each pull-back
burned an attempt; `MAX_STEP_ATTEMPTS (26)` ran out on failures that were never the bot's fault.

`PR-20.1` makes the work site declarative:

    SupplyStep.area_radius  -> the site is a RADIUS around the node (routing is unaffected)
    SupplyStep.max_attempts -> an explicit per-step budget for wandering livestock

- T122: inside the pen, at every sheep position, the engine shears — it never walks back.
- T123: outside the pen the engine still navigates (the radius must not disable routing).
- T124: 30 failed shears do not escalate; the step escalates only past its own budget.
- T125: geometry sanity — the documented pen rectangle is fully inside the declared radius.
- T126: kernel run with a WANDERING sheep: 20 wool -> plane-1 spin -> Fred (`Varp 179 = 21`).
- T127: surgical-scope guard — no other step silently inherited a radius or a budget.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from osrs_ee.bots.base import DirectiveType, TickContext
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.supply_procurement import (
    BALL_OF_WOOL_ID,
    WOAD_LEAF_ID,
    CADAVA_BERRIES_ID,
    REDBERRY_ID,
    SHEARS_ID,
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

FARM = DEFAULT_WORLD_NODES["LUMBRIDGE_SHEEP_FARM"]
WHEEL = DEFAULT_WORLD_NODES["LUMBRIDGE_SPINNING_WHEEL"]

# Documented sheep-pen rectangle (Fred's farm, Lumbridge).
PEN = (3190, 3212, 3258, 3282)  # x_min, x_max, y_min, y_max
# Where the live sheep actually wandered during the incident run.
SHEEP_TILES = ((3196, 3274), (3204, 3264), (3209, 3280), (3195, 3278), (3211, 3270))


def _ctx(pos: WorldPoint, inventory: list[dict[str, Any]], tick: int = 1) -> TickContext:
    return TickContext(
        game_tick=tick,
        position=pos,
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=tuple(inventory),
        total_level=35,
    )


SHEARS = [{"item_id": SHEARS_ID, "name": "Shears", "quantity": 1}]


# ---------------------------------------------------------------------------
# T122 — no more tether: anywhere in the pen means "Shear"
# ---------------------------------------------------------------------------
def test_t122_anywhere_inside_the_pen_shears_instead_of_pulling_the_bot_back() -> None:
    """T122 (`PR-20.1`): the exact tick-431 defect. Standing next to a sheep 6/14/19 tiles from the
    corner anchor must produce `INTERACT_NPC Sheep`, never a walk back to the anchor tile."""
    for tile in SHEEP_TILES:
        pos = WorldPoint(tile[0], tile[1], 0)
        distance = pos.chebyshev_distance(FARM)
        directive = ZeroTouchSupplyEngine().ensure(_ctx(pos, SHEARS), WOOL_ID, 20)
        assert directive is not None
        assert directive.directive_type == DirectiveType.INTERACT_NPC, (
            f"at {tile} (dist={distance}) the engine issued {directive.directive_type} "
            f"({directive.reason!r}) — that is the tether ping-pong"
        )
        assert directive.payload == {"name": "Sheep", "action": "Shear"}

    # Walking a full loop around the pen never produces a single pull-back.
    engine = ZeroTouchSupplyEngine()
    for tick, tile in enumerate(SHEEP_TILES * 4, start=1):
        directive = engine.ensure(_ctx(WorldPoint(tile[0], tile[1], 0), SHEARS, tick), WOOL_ID, 20)
        assert directive is not None and directive.directive_type == DirectiveType.INTERACT_NPC


# ---------------------------------------------------------------------------
# T123 — anti-false-positive: the radius is a work site, not a substitute for routing
# ---------------------------------------------------------------------------
def test_t123_outside_the_pen_the_engine_still_navigates() -> None:
    """T123 (`PR-20.1`): a bot standing in Lumbridge (hundreds of tiles away) must still be routed to
    the farm. The wider work site must never mean "shear from anywhere"."""
    engine = ZeroTouchSupplyEngine()
    directive = engine.ensure(_ctx(WorldPoint(3222, 3218, 0), SHEARS), WOOL_ID, 20)
    assert directive is not None
    assert directive.directive_type == DirectiveType.NAVIGATE_TO, directive
    assert directive.destination == "LUMBRIDGE_SHEEP_FARM"

    # Just outside the declared radius (23 tiles from the anchor) is still a walk.
    far = WorldPoint(FARM.x, FARM.y + 23, 0)
    assert far.chebyshev_distance(FARM) == 23
    directive = engine.ensure(_ctx(far, SHEARS), WOOL_ID, 20)
    assert directive is not None and directive.directive_type == DirectiveType.NAVIGATE_TO, directive


# ---------------------------------------------------------------------------
# T124 — livestock budget
# ---------------------------------------------------------------------------
def test_t124_livestock_budget_tolerates_failed_shears_without_escalating_early() -> None:
    """T124 (`PR-20.1`): 30 consecutive failed shears (sheep out of the mood) must NOT escalate —
    the old 26-attempt budget is what produced `SUPPLY_STEP_STALLED:Wool:NPC:Sheep` — but a pen that
    never yields must still stop inside its declared budget."""
    engine = ZeroTouchSupplyEngine()
    pos = WorldPoint(3204, 3264, 0)
    for tick in range(1, 31):
        directive = engine.ensure(_ctx(pos, SHEARS, tick), WOOL_ID, 20)
        assert directive is not None
        assert directive.directive_type == DirectiveType.INTERACT_NPC, (tick, directive)

    directive = engine.ensure(_ctx(pos, SHEARS, 31), WOOL_ID, 20)
    assert directive is not None and directive.directive_type == DirectiveType.INTERACT_NPC
    assert directive.directive_type != DirectiveType.ESCALATE_HUMAN

    # ...and the escalation still exists, only later.
    escalation: Any = None
    for tick in range(32, 200):
        directive = engine.ensure(_ctx(pos, SHEARS, tick), WOOL_ID, 20)
        if directive is not None and directive.directive_type == DirectiveType.ESCALATE_HUMAN:
            escalation = directive
            break
    assert escalation is not None, "a pen that never yields must eventually escalate"
    assert escalation.reason == "SUPPLY_STEP_STALLED:Wool:NPC:Sheep", escalation.reason

    # Every unit of wool resets the budget (progress-aware, PR-19), so 20 sheep are reachable.
    fresh = ZeroTouchSupplyEngine()
    inventory = list(SHEARS)
    for i in range(20):
        directive = fresh.ensure(_ctx(pos, inventory, i + 1), WOOL_ID, 20)
        assert directive is not None and directive.directive_type == DirectiveType.INTERACT_NPC
        inventory.append({"item_id": WOOL_ID, "name": "Wool", "quantity": 1})
    assert fresh.ensure(_ctx(pos, inventory, 99), WOOL_ID, 20) is None


# ---------------------------------------------------------------------------
# T125 — geometry sanity (the "throne room" class, pen edition)
# ---------------------------------------------------------------------------
def test_t125_the_documented_pen_rectangle_is_fully_inside_the_declared_work_site() -> None:
    """T125 (`PR-20.1`): the declared radius must cover the ENTIRE pen, otherwise a corner sheep
    re-introduces the tether. This is the same "encode the map as a test" rule as `T101`."""
    step = SUPPLY_RECIPES[WOOL_ID].steps[0]
    assert step.area_radius is not None and step.area_radius >= 1
    x_min, x_max, y_min, y_max = PEN
    for corner in ((x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)):
        distance = max(abs(corner[0] - FARM.x), abs(corner[1] - FARM.y))
        assert distance <= step.area_radius, (
            f"pen corner {corner} is {distance} tiles from the anchor but area_radius="
            f"{step.area_radius}: the bot would be tethered again"
        )
    # The anchor tile itself is obviously inside, and the radius is not absurdly large
    # (the wheat field, 25+ tiles away, must stay outside).
    assert max(abs(3162 - FARM.x), abs(3296 - FARM.y)) > step.area_radius


# ---------------------------------------------------------------------------
# T126 — kernel run against a WANDERING sheep
# ---------------------------------------------------------------------------
def _wandering_sheep_world(command_type: str, payload: dict[str, Any], adapter: FakeGameAdapter) -> None:
    """Sheep that actually move between shears, and sometimes refuse — like the live pen."""
    name = str(payload.get("name", ""))
    action = str(payload.get("action", ""))
    if command_type == "INTERACT_NPC":
        if name == "Sheep":
            # Every 5th click the sheep is out of the mood; otherwise it yields wool and moves on.
            adapter.shear_clicks = getattr(adapter, "shear_clicks", 0) + 1
            if adapter.shear_clicks % 5 == 0:
                return
            if any(int(i["item_id"]) == SHEARS_ID for i in adapter.inventory_items):
                adapter._add_to_inventory({"item_id": WOOL_ID, "name": "Wool", "quantity": 1, "noted": False})
            # the flock wanders to the next documented tile
            tile = SHEEP_TILES[adapter.shear_clicks % len(SHEEP_TILES)]
            adapter.position = {"x": tile[0], "y": tile[1], "plane": 0}
        elif name == "Fred the Farmer":
            if int(adapter.varps.get(179, 0)) == 0:
                adapter.varps[179] = 1
            elif any(int(i["item_id"]) == BALL_OF_WOOL_ID for i in adapter.inventory_items):
                adapter.inventory_items.clear()
                adapter.varps[179] = 21
                adapter.quest_points += 1
    elif command_type == "WIDGET_CLICK":
        # `PR-20.2`: Make-X 270:15 = ball of wool, make-all.
        for it in list(adapter.inventory_items):
            if int(it["item_id"]) == WOOL_ID:
                adapter.inventory_items.remove(it)
                adapter._add_to_inventory(
                    {"item_id": BALL_OF_WOOL_ID, "name": "Ball of wool", "quantity": int(it["quantity"]), "noted": False}
                )
    elif command_type == "INTERACT_OBJECT":
        if name in ("Staircase", "Stairs", "Ladder"):
            cur = dict(adapter.position)
            cur["plane"] = 1 if action == "Climb-up" else 0
            adapter.position = cur
        # `PR-20.2`: `Spin` opens the Make-X interface only — production happens in WIDGET_CLICK.


def test_t126_kernel_run_completes_sheep_shearer_against_moving_sheep(tmp_path: Path) -> None:
    """T126 (`PR-20.1`): the real failure mode was a MOVING target. With sheep that teleport between
    documented pen tiles and refuse one shear in five, the bundle must still reach `Varp 179 = 21`."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = StateStore(tmp_path / "pr20_1_sheep.db")
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
        now_utc=datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc),
        bypass_circadian=True,
    )
    adapter = FakeGameAdapter(
        account_id="acc_02",
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        skills={"hitpoints": 10},
        combat_level=3,
        quest_points=3,
        total_level=38,
        playtime_minutes=260,
        bank_items=[],
        inventory_items=[],
        ground_items=[{"item_id": SHEARS_ID, "name": "Shears", "quantity": 1}],
        walk_speed_tiles_per_tick=4,
        interaction_grants=_wandering_sheep_world,
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
        assert adapter.varps[179] == 21, f"varps={adapter.varps} inv={adapter.inventory_items}"
        assert completed, f"no COMPLETE_TASK (inv={adapter.inventory_items}, pos={adapter.position})"
        assert adapter.quest_points == 4
    finally:
        store.close()


# ---------------------------------------------------------------------------
# T127 — surgical scope
# ---------------------------------------------------------------------------
def test_t127_only_the_livestock_step_declares_a_work_site_radius_or_budget() -> None:
    """T127 (`PR-20.1`): `PR-20.1` is a surgical field fix, not a global loosening. Exactly one step
    (the sheep `Shear`) may carry `area_radius`/`max_attempts`; everything else keeps the tight
    3-tile tolerance and the shared budget."""
    widened: list[tuple[int, int, str]] = []
    for item_id, recipe in SUPPLY_RECIPES.items():
        for idx, step in enumerate(recipe.steps):
            if step.area_radius is not None:
                widened.append((item_id, idx, recipe.label))
    # `PR-20.2` widened exactly TWO more steps, both justified by geometry rather than by a new
    # incident: the cadava cluster (far corner 9 tiles from its anchor) and the two redberry bushes.
    # Anything else appearing here is unrequested scope creep and must fail.
    allowed = {
        (WOOL_ID, "Sheep", "Shear"),
        (CADAVA_BERRIES_ID, "Cadava bush", "Pick"),
        (REDBERRY_ID, "Redberry bush", "Pick from"),
        # `PR-21.6`: `Wyson the gardener` PATROLS the east end of Falador park while the route anchor
        # is the park waypoint ~31 tiles west — the same "wandering target" shape as the sheep pen,
        # and the same remedy. Field signature: `SUPPLY_STEP_STALLED:Woad leaves:NPC:Wyson the gardener`.
        (WOAD_LEAF_ID, "Wyson the gardener", "Talk-to"),
    }
    found = {
        (item_id, SUPPLY_RECIPES[item_id].steps[idx].targets[0], SUPPLY_RECIPES[item_id].steps[idx].action)
        for item_id, idx, _label in widened
    }
    assert found == allowed, (found, allowed)
    # Attempt budgets are a narrower tool than radii: only the wandering livestock (`Wool`) and the
    # Make-X production click (`PR-20.2`, which needs ~60s of server-side production) may carry one.
    budgeted = {
        (item_id, idx)
        for item_id, recipe in SUPPLY_RECIPES.items()
        for idx, step in enumerate(recipe.steps)
        if step.max_attempts is not None
    }
    assert budgeted <= {(WOOL_ID, 0), (BALL_OF_WOOL_ID, 2)}, budgeted
    assert (WOOL_ID, 0) in budgeted

    # A default step is still gated at the global tolerance (proven with the wheat field).
    from osrs_ee.bots.supply_procurement import GRAIN_ID

    engine = ZeroTouchSupplyEngine()
    wheat = DEFAULT_WORLD_NODES["MILL_LANE_WHEAT_FIELD"]
    too_far = WorldPoint(wheat.x + 4, wheat.y, 0)
    directive = engine.ensure(_ctx(too_far, []), GRAIN_ID, 1)
    assert directive is not None and directive.directive_type == DirectiveType.NAVIGATE_TO, directive
    close = WorldPoint(wheat.x + 3, wheat.y, 0)
    directive = engine.ensure(_ctx(close, []), GRAIN_ID, 1)
    assert directive is not None and directive.directive_type == DirectiveType.INTERACT_OBJECT, directive
