"""`PR-21.5` — The cellar object tile, and the snap ring that could not reach it (T165..T167).

**Field incident (live `ZizoScouttt`, 8081, `PR-21.4` run)** — verbatim client log:

    [Walker] walk rejected: target WorldPoint(x=3216, y=9625, plane=0) has no walkable tile within 0
        in the collision map (nearest walkable WorldPoint(x=3215, y=9624, plane=0));
        check the destination coordinate

Two defects, and the second one is why the first one became a *halt*:

1. **The coordinate.** `LUMBRIDGE_CASTLE_BASEMENT` pointed at `(3216,9625)` — the tile the **bucket
   object** spawns on. An object tile is never a walkable destination. Same family as the dairy cow
   `(3172,3317)` in `PR-19.3`, the throne room in `PR-19.6`, the redberries and Aggie in `PR-20.x`.
   This is its **fifth** member. The anchor is now the neighbour the client itself named.

2. **The snap ring was gated on `current_pos.y < 5000`.** `PR-19.3` added that gate to protect the
   underground 5-tick recovery contract — with the result that the self-healing ring was **unreachable
   exactly where destination tiles are most often objects**: every cell of the Lumbridge cellar is a
   `9xxx` coordinate. A guard that exists to fix "the destination itself is unwalkable" is worthless in
   the one region where destinations are objects. The gate is replaced by the *fast budget* the
   underground reroute already uses, so the contract is preserved and the ring works.

- T165: the anchor is the field-verified walkable tile, and a regional sanity guard keeps it in the cellar.
- T166: the snap ring must rotate **underground** (the live signature, replayed at `y = 9625`).
- T167: the bucket recipe still takes the bucket in the world (anchor, not object, is the walk target).
"""

from __future__ import annotations

import pytest

from osrs_ee.bots.supply_procurement import BUCKET_ID, SupplyStepKind, SUPPLY_RECIPES
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph, NavigationEngine
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, FINAL_TARGET_SNAP_OFFSETS, WorldPoint

# Verbatim from the live client rejection.
BLOCKED_OBJECT_TILE = (3216, 9625)
FIELD_VERIFIED_NEIGHBOUR = (3215, 9624)


def _caps() -> AccountCapabilitiesContext:
    return AccountCapabilitiesContext("live", skills={"hitpoints": 10}, combat_level=3, quest_points=1)


# ---------------------------------------------------------------------------
# T165 — the tile, plus the regional guard that stops the class returning
# ---------------------------------------------------------------------------
def test_t165_cellar_anchor_is_the_walkable_neighbour_the_client_named() -> None:
    """T165 (`PR-21.5`): the anchor must be the tile `Rs2Walker` accepted, never the object tile.
    The regional rectangle is the same "encode the map as a test" discipline as `T101` — it fails a
    transposed digit instead of waiting for a live halt."""
    node = DEFAULT_WORLD_NODES["LUMBRIDGE_CASTLE_BASEMENT"]
    assert (node.x, node.y) == FIELD_VERIFIED_NEIGHBOUR, (
        f"cellar anchor moved to ({node.x},{node.y}); the live client rejected {BLOCKED_OBJECT_TILE} "
        f"and named {FIELD_VERIFIED_NEIGHBOUR} as the nearest walkable tile"
    )
    assert (node.x, node.y) != BLOCKED_OBJECT_TILE, "an object tile is never a destination"

    # Regional sanity: the Lumbridge cellar lives in the 3200..3231 x 9600..9640 box, plane 0.
    assert 3200 <= node.x <= 3231, node.x
    assert 9600 <= node.y <= 9640, node.y
    assert node.plane == 0
    # ...and it is one tile from the object, so the take still reaches the spawn.
    assert max(abs(node.x - BLOCKED_OBJECT_TILE[0]), abs(node.y - BLOCKED_OBJECT_TILE[1])) == 1


# ---------------------------------------------------------------------------
# T166 — the live signature, replayed underground
# ---------------------------------------------------------------------------
def test_t166_the_snap_ring_self_heals_underground_too() -> None:
    """T166 (`PR-21.5`): `PR-19.3`'s ring was surface-only (`y < 5000`), so an unwalkable destination in
    the cellar could never self-heal. Replay the exact field coordinates below ground and require the
    engine to rotate off the rejected tile — with the SAME fast budget the underground reroute uses."""
    nav = NavigationEngine(ConstrainedWaypointGraph())
    origin = WorldPoint(3219, 9628, 0, "CELLAR_START")  # inside the castle basement
    destination = WorldPoint(BLOCKED_OBJECT_TILE[0], BLOCKED_OBJECT_TILE[1], 0, "BLOCKED")

    seen: list[tuple[int, int]] = []
    for _ in range(1, 40):
        payload = nav.step_navigation_on_tick(
            current_pos=origin,  # the client refuses, so the character never moves
            tick_advanced=True,
            capabilities=_caps(),
            destination=destination,
        )
        if payload is not None:
            seen.append((int(payload["x"]), int(payload["y"])))

    assert seen, "no payload dispatched underground at all"
    rotated = [t for t in seen if t != BLOCKED_OBJECT_TILE]
    assert rotated, f"the ring never fired below y=5000 — it is still surface-only: {seen}"
    ox, oy = rotated[0]
    assert max(abs(ox - BLOCKED_OBJECT_TILE[0]), abs(oy - BLOCKED_OBJECT_TILE[1])) <= 3
    assert (ox, oy) in {(BLOCKED_OBJECT_TILE[0] + dx, BLOCKED_OBJECT_TILE[1] + dy) for dx, dy in FINAL_TARGET_SNAP_OFFSETS}


def test_t166b_the_underground_snap_keeps_its_fast_recovery_contract() -> None:
    """T166b (`PR-21.5`): the `y < 5000` gate existed so the cellar recovers in ~5 ticks, not 20. Enabling
    the ring underground must not slow that down: the first rotation has to come inside the fast window."""
    nav = NavigationEngine(ConstrainedWaypointGraph())
    origin = WorldPoint(3219, 9628, 0, "CELLAR_START")
    destination = WorldPoint(*BLOCKED_OBJECT_TILE, 0, "BLOCKED")

    ticks_to_rotate = 0
    first_tile: tuple[int, int] | None = None
    for tick in range(1, 40):
        payload = nav.step_navigation_on_tick(
            current_pos=origin,
            tick_advanced=True,
            capabilities=_caps(),
            destination=destination,
        )
        if payload is not None:
            tile = (int(payload["x"]), int(payload["y"]))
            if tile != BLOCKED_OBJECT_TILE:
                first_tile, ticks_to_rotate = tile, tick
                break
    assert first_tile is not None, "never rotated underground"
    assert ticks_to_rotate <= 8, f"underground rotation took {ticks_to_rotate} ticks; the fast contract is ~5"


# ---------------------------------------------------------------------------
# T167 — the recipe still collects the bucket
# ---------------------------------------------------------------------------
def test_t167_the_bucket_recipe_walks_to_the_anchor_and_takes_by_name() -> None:
    """T167 (`PR-21.5`): correcting the anchor must not break collection. The TAKE step stays name-based
    (`"Bucket"`), so the one-tile move from object tile to walkable neighbour changes nothing about how
    the item is picked up."""
    recipe = SUPPLY_RECIPES[BUCKET_ID]
    assert recipe is not None
    take = next(s for s in recipe.steps if s.produces and any(i == BUCKET_ID for i, _ in s.produces))
    # A bucket on the cellar floor is a GROUND ITEM -> `PICKUP` (name/id resolved), which is exactly why
    # moving the anchor one tile costs nothing: the take never needed the object's own tile.
    assert take.kind == SupplyStepKind.PICKUP, take.kind
    assert "Bucket" in take.targets
    assert take.area is not None
    assert (take.area.x, take.area.y) == FIELD_VERIFIED_NEIGHBOUR
    # an explicit descent still surrounds the take — the cellar is a different map region
    assert any(s.kind == SupplyStepKind.ENTER_UNDERGROUND for s in recipe.steps)
    assert any(s.kind == SupplyStepKind.EXIT_UNDERGROUND for s in recipe.steps)
