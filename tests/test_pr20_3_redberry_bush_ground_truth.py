"""`PR-20.3` — Redberry bushes: live ground truth & the last tether (T134..T136).

Live probe (`GET /objects?name=Redberry bush`, port 8081):

    { "id": 23628, "name": "Redberry bush", "position": {"x": 3272, "y": 3375, "plane": 0} }
    { "id": 23628, "name": "Redberry bush", "position": {"x": 3277, "y": 3371, "plane": 0} }

The graph node `VARROCK_REDBERRY_BUSHES` said `(3240, 3358)` — ~35 tiles south-west, right beside the
level-20 Dark Wizard circle. The resulting live loop (ticks 35..70):

    INTERACT_OBJECT Pick from Redberries   -> the client starts walking to the real bush (east)
    NAVIGATE_TO    walking to VARROCK_REDBERRY_BUSHES   -> outside the work site, dragged back
    INTERACT_OBJECT Pick from Redberries   -> ...

Exactly the pen (incident #7) and throne-room (incident #5) families: **a coordinate that is not the
world's.** The wiki was the source again; the probe is the authority.

- T134: both live bush tiles sit inside `area_radius` of the node, and the node IS their midpoint.
- T135: the bush is pinned to live object id `23628`, `Pick from` is preserved, and every route to it
        is still plannable with an honest edge cost.
- T136: the node is safely far from the Dark Wizard circle (the old tile was 14 tiles away and the
        work site is now 48) — a level-3 account must never be parked there.
"""

from __future__ import annotations

from typing import Any

from osrs_ee.bots.base import DirectiveType, TickContext
from osrs_ee.bots.supply_procurement import (
    REDBERRY_BUSH_OBJECT_ID,
    REDBERRY_ID,
    SUPPLY_RECIPES,
    ZeroTouchSupplyEngine,
)
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint

REDBERRY = DEFAULT_WORLD_NODES["VARROCK_REDBERRY_BUSHES"]
CADAVA = DEFAULT_WORLD_NODES["VARROCK_CADAVA_BUSHES"]
DARK_WIZARDS = DEFAULT_WORLD_NODES["VARROCK_SOUTH_DARK_WIZARDS"]

# The two bushes as the live client reports them.
LIVE_BUSHES = ((3272, 3375), (3277, 3371))
OLD_WRONG_TILE = (3240, 3358)


def _ctx(pos: WorldPoint, inventory: list[dict[str, Any]] | None = None, tick: int = 1) -> TickContext:
    return TickContext(
        game_tick=tick,
        position=pos,
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=tuple(inventory or ()),
        total_level=40,
    )


# ---------------------------------------------------------------------------
# T134 — geometry against the probe
# ---------------------------------------------------------------------------
def test_t134_both_live_bushes_sit_inside_the_work_site_and_the_node_is_their_midpoint() -> None:
    """T134 (`PR-20.3`): the node must be the midpoint of the two probed bushes and every bush must be
    inside `area_radius` — otherwise the engine drags the bot off the bush it is picking."""
    step = SUPPLY_RECIPES[REDBERRY_ID].steps[0]
    assert step.area_radius is not None

    for (bx, by) in LIVE_BUSHES:
        assert (bx, by) != OLD_WRONG_TILE
        distance = max(abs(bx - REDBERRY.x), abs(by - REDBERRY.y))
        assert distance <= step.area_radius, (
            f"live bush ({bx},{by}) is {distance} tiles from the node but area_radius="
            f"{step.area_radius}: the bot would be tethered mid-pick"
        )
        # The engine must not walk anywhere while standing on the bush tile.
        directive = ZeroTouchSupplyEngine().ensure(_ctx(WorldPoint(bx, by, 0)), REDBERRY_ID, 3)
        assert directive is not None and directive.directive_type == DirectiveType.INTERACT_OBJECT, directive

    # Midpoint, not a random tile in the neighbourhood.
    assert (REDBERRY.x, REDBERRY.y) == (
        (LIVE_BUSHES[0][0] + LIVE_BUSHES[1][0]) // 2,
        (LIVE_BUSHES[0][1] + LIVE_BUSHES[1][1]) // 2,
    ), REDBERRY
    assert (REDBERRY.x, REDBERRY.y) != OLD_WRONG_TILE


# ---------------------------------------------------------------------------
# T135 — object id + routing
# ---------------------------------------------------------------------------
def test_t135_the_bush_is_pinned_to_object_23628_and_the_route_is_honest() -> None:
    """T135 (`PR-20.3`): the interaction carries the live id, `Pick from` survives, and the cadava ->
    redberry edge now costs what the tiles actually are (8), not the 30 inherited from the wrong tile."""
    step = SUPPLY_RECIPES[REDBERRY_ID].steps[0]
    assert step.object_id == REDBERRY_BUSH_OBJECT_ID == 23628
    assert step.action == "Pick from"
    assert step.produces == ((REDBERRY_ID, 3),)

    directive = ZeroTouchSupplyEngine().ensure(_ctx(REDBERRY), REDBERRY_ID, 3)
    assert directive is not None and directive.directive_type == DirectiveType.INTERACT_OBJECT
    assert directive.payload["id"] == 23628
    assert directive.payload["action"] == "Pick from"

    # Chebyshev between the two supply nodes is 6; the edge must not pretend it is 30.
    real = max(abs(CADAVA.x - REDBERRY.x), abs(CADAVA.y - REDBERRY.y))
    assert real == 6, real
    graph = ConstrainedWaypointGraph()
    edges = [e for e in graph.edges if e.dst == "VARROCK_REDBERRY_BUSHES" or e.src == "VARROCK_REDBERRY_BUSHES"]
    assert edges, "the redberry node must stay reachable"
    cadava_edges = [e for e in edges if {e.src, e.dst} == {"VARROCK_CADAVA_BUSHES", "VARROCK_REDBERRY_BUSHES"}]
    assert cadava_edges, "supply_cadava_to_redberry_bushes edge disappeared"
    assert cadava_edges[0].cost_tiles <= 12.0, cadava_edges[0].cost_tiles

    # ...and a level-3 account can still route to it from the anchor bank.
    plan = graph.plan_safe_route(
        origin="GRAND_EXCHANGE",
        destination="VARROCK_REDBERRY_BUSHES",
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
    )
    assert plan.total_cost_tiles > 0


# ---------------------------------------------------------------------------
# T136 — a level-3 account must never be parked next to the dark wizards
# ---------------------------------------------------------------------------
def test_t136_the_redberry_work_site_is_nowhere_near_the_dark_wizard_circle() -> None:
    """T136 (`PR-20.3`): the old tile `(3240,3358)` was 14 tiles from level-20 Dark Wizards — a death
    sentence for a combat-3 account. The live bushes are 48 tiles away; this test keeps it that way."""
    old_distance = max(abs(OLD_WRONG_TILE[0] - DARK_WIZARDS.x), abs(OLD_WRONG_TILE[1] - DARK_WIZARDS.y))
    new_distance = max(abs(REDBERRY.x - DARK_WIZARDS.x), abs(REDBERRY.y - DARK_WIZARDS.y))
    assert old_distance <= 15, "sanity: the replaced tile really was beside the wizards"
    assert new_distance >= 40, f"the live redberry node is only {new_distance} tiles from Dark Wizards"

    # The dangerous hazard edge is still gated on combat level (a level-3 account cannot take it).
    graph = ConstrainedWaypointGraph()
    hazard = [e for e in graph.edges if e.src == "VARROCK_WEST_GATE" and "LUMBRIDGE" in e.dst]
    assert hazard, "the hazard edge should still exist"
    assert min(int(e.min_combat_level or 0) for e in hazard) >= 26
