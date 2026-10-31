"""Underground region registry & authorized exit routes (`PR-19.4`).

Why this module exists
----------------------
The reactive watchdog originally treated **every** `y >= 5000` position as a lethal dungeon, so
climbing out of the ordinary Lumbridge Castle cellar (`y = 9625`) was misread as a death respawn and
halted the session (live `acc_02`, tick 1094).

Underground space in OSRS is really a set of *named regions*, each with its own exit semantics:
some are lethal (an un-authorized pop to the surface means death/teleport), others are ordinary
stair-connected cellars. This module is the single source of truth for that classification, consumed
by both layers:

* `osrs_ee/core/reactive_watchdog.py` — decides whether a transition must halt the session;
* `osrs_ee/bots/supply_procurement.py` — knows *how* to leave a region, so an exit is always an
  explicit, authorized action instead of an accident.

Both layers therefore agree by construction; there is no coordinate list to keep in sync twice.
"""

from __future__ import annotations

from dataclasses import dataclass

# Anything at or below this Y that is not a classified region still gets the legacy protection
# (fail-closed): an unknown cave keeps its death/teleport guard.
UNDERGROUND_Y_THRESHOLD = 5000


@dataclass(frozen=True)
class UndergroundRegion:
    name: str
    x_min: int
    x_max: int
    y_min: int
    y_max: int
    # `True`  -> leaving without `authorize_surface_exit()` is a P0 incident (death / teleport / actor).
    # `False` -> a shallow, stair-connected basement; climbing out is normal play.
    requires_authorized_exit: bool

    def contains(self, x: int, y: int) -> bool:
        return self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max


@dataclass(frozen=True)
class UndergroundExit:
    """How to leave a region through its own staircase/ladder (never by walking across the map)."""

    region: str
    at_x: int
    at_y: int
    plane: int
    targets: tuple[str, ...]
    action: str = "Climb-up"


UNDERGROUND_REGIONS: tuple[UndergroundRegion, ...] = (
    # Stronghold of Security floors 1-4 — the very guard this logic was written for (`PR-15`).
    UndergroundRegion("STRONGHOLD_OF_SECURITY", 2290, 2410, 5100, 5400, True),
    # Lumbridge Castle basement: a shallow kitchen cellar with its own staircase.
    # Spawn pins (OSRS Wiki map data): bucket 3216,9625 · cabbage 3217,9622 · jug 3211,9625 ·
    # knife 3215,9625 · boots 3208,9620 / 3210,9615.
    UndergroundRegion("LUMBRIDGE_CASTLE_BASEMENT", 3195, 3240, 9595, 9650, False),
)

UNDERGROUND_EXITS: dict[str, UndergroundExit] = {
    # `PR-21.6` FIELD FIX (incident #12, `SUPPLY_EXIT_BLOCKED:LUMBRIDGE_CASTLE_BASEMENT`):
    # the cellar has exactly ONE way out — the `Ladder` (`17385`) at `(3209,9616)`. It had no staircase
    # and no stairs, yet the target list named `Staircase` and `Stairs` FIRST; the attempts budget is
    # spent in blocks of 6 per name, so 12 attempts exhausted on two names that do not exist and
    # `Ladder` was never even tried. Order is now existence-first, and the budget scales with the list
    # (`required_climb_attempts`) so a longer list can never starve its own last entry again.
    # `at_x/at_y` point at the ladder tile — an object tile is not a destination (see `PR-21.5`).
    "LUMBRIDGE_CASTLE_BASEMENT": UndergroundExit(
        region="LUMBRIDGE_CASTLE_BASEMENT",
        at_x=3210,
        at_y=9616,
        plane=0,
        targets=("Ladder", "Trapdoor", "Staircase"),
        action="Climb-up",
    ),
    # The Stronghold exit is driven by `StrongholdTaskModule` (Bone chain `23732`), not by the supply
    # engine, so no generic exit is registered here.
}


CLIMB_ATTEMPTS_PER_NAME = 6


def required_climb_attempts(targets: tuple[str, ...]) -> int:
    """Attempts needed to actually TRY the last name in a target list (`PR-21.6`).

    The exit loop walks `targets[(exits - 1) // 6]`, so the `n`-th name is first tried on attempt
    `6 * n + 1`. A budget smaller than that silently disables every name at the end of the list — which
    is exactly how the cellar's only exit (`Ladder`) became unreachable behind two names that do not
    exist in the room.
    """
    if not targets:
        return CLIMB_ATTEMPTS_PER_NAME
    return CLIMB_ATTEMPTS_PER_NAME * (len(targets) - 1) + 1


def classify_underground(x: int, y: int) -> UndergroundRegion | None:
    """Return the region descriptor for `(x, y)`, an `UNCLASSIFIED_UNDERGROUND` fallback, or `None`."""
    for region in UNDERGROUND_REGIONS:
        if region.contains(x, y):
            return region
    if y >= UNDERGROUND_Y_THRESHOLD:
        return UndergroundRegion("UNCLASSIFIED_UNDERGROUND", -1, 10**5, UNDERGROUND_Y_THRESHOLD, 10**5, True)
    return None


def is_underground(x: int, y: int) -> bool:
    return classify_underground(x, y) is not None


def exit_for(x: int, y: int) -> UndergroundExit | None:
    """The registered stair/ladder route out of the region containing `(x, y)`, if any."""
    region = classify_underground(x, y)
    if region is None:
        return None
    return UNDERGROUND_EXITS.get(region.name)
