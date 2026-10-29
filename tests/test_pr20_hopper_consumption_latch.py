"""`PR-20` Part 1 — Field Incident #6: the windmill hopper multi-tick latch (T103..T106).

Field evidence (live `acc_02` @ 8081, ticks 289..349):

    [tick=0289] INTERACT_OBJECT | Fill  Pot of flour source (top floor hopper)
    [tick=0290] INTERACT_OBJECT | Operate Pot of flour source
    [tick=0291] INTERACT_OBJECT | climbing to plane 0 for Pot of flour
    [tick=0323] INTERACT_OBJECT | Empty Pot of flour source (ground-floor flour bin 1781)
    [tick=0349] ESCALATE_HUMAN | SUPPLY_STEP_STALLED:Pot of flour:OBJECT:Flour bin

Post-run scan: `Grain (1947)` was STILL in the bag — the hopper never received it, so the bin was
empty and every `Empty` click was a no-op.

Root cause: `supply_procurement.py` treated *dispatch* as *completion* for any step without
`produces`. `Fill` fired once and was marked done 600 ms later; `Operate` and `Climb-down` were
pushed through on the next two ticks while the character was still walking to the hopper.

The fix is a **consumption latch** (`SupplyStep.uses_up`) plus an **action grace window**
(`settle_ticks`): a destructive step is only done when the live inventory actually shrinks, and the
rest of the chain is held back for a few ticks so the client can finish its walk/animation.

- T103: `Fill` is never complete while the grain is still in the bag.
- T104: `Operate` may not be followed by `Climb-down` inside the settle window.
- T105: a hopper that never eats the grain escalates with the exact field string (anti-false-positive:
        a genuinely broken chain still stops, it never loops forever).
- T106: a step that merely *requires* items (`consumes`) but does not destroy them (the mill door)
        is NOT latch-gated — the door must never freeze the chain.
"""

from __future__ import annotations

from typing import Any

from osrs_ee.bots.base import DirectiveType, TickContext
from osrs_ee.bots.supply_procurement import (
    GRAIN_ID,
    POT_ID,
    POT_OF_FLOUR_ID,
    SUPPLY_RECIPES,
    ZeroTouchSupplyEngine,
)
from osrs_ee.navigation import AccountCapabilitiesContext
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint

MILL = DEFAULT_WORLD_NODES["MILL_LANE_MILL"]
TOP = DEFAULT_WORLD_NODES["LUMBRIDGE_WINDMILL_TOP"]


def _ctx(pos: WorldPoint, inventory: list[dict[str, Any]], tick: int) -> TickContext:
    return TickContext(
        game_tick=tick,
        position=pos,
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=tuple(inventory),
        total_level=35,
    )


def _walk_mill(
    engine: ZeroTouchSupplyEngine,
    *,
    consume_grain: bool,
    max_ticks: int = 60,
) -> list[tuple[int, str, str]]:
    """Drive the full mill chain, returning `(tick, object_name, action)` for every interaction."""
    inventory: list[dict[str, Any]] = [
        {"item_id": GRAIN_ID, "name": "Grain", "quantity": 1},
        {"item_id": POT_ID, "name": "Pot", "quantity": 1},
    ]
    plane = 0
    seen: list[tuple[int, str, str]] = []
    for tick in range(1, max_ticks + 1):
        pos = WorldPoint(TOP.x, TOP.y, plane) if plane == 2 else WorldPoint(MILL.x, MILL.y, plane)
        directive = engine.ensure(_ctx(pos, inventory, tick), POT_OF_FLOUR_ID, 1)
        if directive is None:
            break
        if directive.directive_type == DirectiveType.ESCALATE_HUMAN:
            seen.append((tick, "ESCALATE", directive.reason))
            break
        if directive.directive_type != DirectiveType.INTERACT_OBJECT:
            continue
        name = str(directive.payload.get("name"))
        action = str(directive.payload.get("action"))
        seen.append((tick, name, action))
        if name in ("Ladder", "Staircase", "Stairs"):
            plane = 2 if action == "Climb-up" else 0
        elif name == "Hopper" and consume_grain:
            inventory = [it for it in inventory if int(it["item_id"]) != GRAIN_ID]
        elif name == "Flour bin":
            inventory = [it for it in inventory if int(it["item_id"]) != POT_ID]
            inventory.append({"item_id": POT_OF_FLOUR_ID, "name": "Pot of flour", "quantity": 1})
    return seen


# ---------------------------------------------------------------------------
# T103 — the consumption latch
# ---------------------------------------------------------------------------
def test_t103_hopper_fill_is_not_done_until_the_grain_leaves_the_inventory() -> None:
    """T103 (`PR-20`): while `Grain (1947)` is still carried, the engine may only ever re-issue
    `Fill`. `Operate` / `Climb-down` / `Flour bin` must be unreachable."""
    engine = ZeroTouchSupplyEngine()
    inventory = [
        {"item_id": GRAIN_ID, "name": "Grain", "quantity": 1},
        {"item_id": POT_ID, "name": "Pot", "quantity": 1},
    ]
    plane = 0
    filler_hits = 0
    reached_hopper = False
    for tick in range(1, 22):
        pos = WorldPoint(TOP.x, TOP.y, plane) if plane == 2 else WorldPoint(MILL.x, MILL.y, plane)
        directive = engine.ensure(_ctx(pos, inventory, tick), POT_OF_FLOUR_ID, 1)
        assert directive is not None
        if directive.directive_type != DirectiveType.INTERACT_OBJECT:
            continue
        name = str(directive.payload.get("name"))
        if name in ("Ladder", "Staircase", "Stairs"):
            plane = 2 if directive.payload.get("action") == "Climb-up" else 0
            continue
        if name == "Large door":
            assert not reached_hopper
            continue
        assert name == "Hopper", (
            f"tick {tick}: dispatched '{name}' while the grain was still in the bag — "
            "this is exactly the tick-289 field defect"
        )
        filler_hits += 1
        reached_hopper = True

    assert filler_hits > 1, "the hopper must be re-clicked while the grain has not been consumed"
    # The latch state is observable and never claims completion.
    assert (POT_OF_FLOUR_ID, 2) in engine._pending_consumption
    assert (POT_OF_FLOUR_ID, 2) not in engine._done


# ---------------------------------------------------------------------------
# T104 — action grace between Operate and Climb-down
# ---------------------------------------------------------------------------
def test_t104_operate_and_climb_down_are_separated_by_the_settle_window() -> None:
    """T104 (`PR-20`): once the grain IS consumed, `Operate` fires — but `Climb-down` waits out the
    action grace so the client can play the animation instead of being interrupted."""
    engine = ZeroTouchSupplyEngine()
    seen = _walk_mill(engine, consume_grain=True)
    interact = [(tick, name, action) for tick, name, action in seen if name != "ESCALATE"]

    operate = next(t for t, n, _a in interact if n == "Hopper controls")
    climb_down = next(t for t, n, a in interact if n in ("Ladder", "Staircase", "Stairs") and a == "Climb-down")
    assert climb_down - operate >= engine.settle_ticks, (
        f"Operate at tick {operate}, Climb-down at tick {climb_down}: the client was interrupted "
        "mid-animation (the tick-290/tick-291 defect)"
    )

    # Same guarantee between the hopper Fill (consumption confirmed) and Operate.
    hopper_fill = next(t for t, n, _a in interact if n == "Hopper")
    assert operate - hopper_fill >= engine.settle_ticks


# ---------------------------------------------------------------------------
# T105 — anti-false-positive: a hopper that never eats still escalates
# ---------------------------------------------------------------------------
def test_t105_a_hopper_that_never_consumes_escalates_with_the_exact_field_string() -> None:
    """T105 (`PR-20`): the latch must never turn an impossible step into an infinite loop. If the
    grain is never taken, the run halts with the same actionable string the field already knows."""
    engine = ZeroTouchSupplyEngine()
    seen = _walk_mill(engine, consume_grain=False)
    escalations = [entry for entry in seen if entry[1] == "ESCALATE"]
    assert len(escalations) == 1, seen
    assert escalations[0][2] == "SUPPLY_STEP_STALLED:Pot of flour:OBJECT:Hopper", escalations[0][2]
    assert escalations[0][0] <= 1 + engine.max_consume_wait + 6, escalations[0]


# ---------------------------------------------------------------------------
# T106 — `consumes` alone must never latch
# ---------------------------------------------------------------------------
def test_t106_a_step_that_only_requires_items_is_not_consumption_latched() -> None:
    """T106 (`PR-20`): the mill door declares `consumes=((Grain,1),(Pot,1))` — it needs them in the
    bag but does not destroy them. Latching on `consumes` would freeze the chain at the doorway."""
    engine = ZeroTouchSupplyEngine()
    seen = _walk_mill(engine, consume_grain=True)
    names = [name for _t, name, _a in seen]
    assert names[0] == "Large door", names
    assert "Ladder" in names and "Hopper" in names, names
    # The door is never held pending, even though it declares `consumes`.
    assert (POT_OF_FLOUR_ID, 0) not in engine._pending_consumption
    assert (POT_OF_FLOUR_ID, 0) in engine._done

    door = SUPPLY_RECIPES[POT_OF_FLOUR_ID].steps[0]
    assert door.consumes == ((GRAIN_ID, 1), (POT_ID, 1))
    assert door.uses_up == (), "the door must not declare uses_up — it does not destroy its inputs"
    # ...and the hopper DOES declare it, which is the whole point of the latch.
    hopper = SUPPLY_RECIPES[POT_OF_FLOUR_ID].steps[2]
    assert hopper.targets == ("Hopper",)
    assert hopper.uses_up == ((GRAIN_ID, 1),)
