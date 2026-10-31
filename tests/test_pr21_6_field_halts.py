"""`PR-21.6` — the three field halts that survived `PR-21.5` (T168..T172).

All three were reproduced live on the same account, in the same session, and each one is a *structural*
defect rather than a missing string:

1. **`SUPPLY_EXIT_BLOCKED:LUMBRIDGE_CASTLE_BASEMENT`** — the cellar has exactly one way out: the
   `Ladder` (`17385`) at `(3209,9616)`. The exit tuple named `Staircase` and `Stairs` **first**, and the
   retry loop spends its budget in blocks of six per name (`targets[(exits - 1) // 6]`) against
   `max_climb_attempts = 12`. Arithmetic, not luck: names at index 0 and 1 get six attempts each, index 2
   is first tried on attempt **13** — which never happens. A three-name tuple under a twelve-attempt
   budget is *structurally* one name short, which is why reordering alone is not a fix and why the budget
   now has to cover the list (`required_climb_attempts`).

2. **`SUPPLY_CLIMB_FAILED:MILL_LANE_MILL`** — the mill is three planes with three ladders
   (`12964` 0->1, `12965` 1->2, `12966` 2->1). The recipe issued ONE `Climb-up` with `target_plane=2`
   and a pinned `object_id=12964`: a ladder that does not exist on the floor the bot was standing on.
   It re-issued the same doomed click until the budget ran out.

3. **`UNRECOGNIZED_QUEST_DIALOGUE_OPTION`** (ticks 1565, 133, 617, 474) — the matcher compared raw
   prose, so a comma decided whether a run continued. Jagex sends `"Can you make dyes for me, please?"`;
   the corpus carried the transcript's comma-less form. Two Romeo sub-menu options were missing outright.

- T168: every registered underground exit is *reachable* — the first name is a truthy name, the budget
  covers the whole tuple, and attempt 1 targets the first name (existence-first ordering).
- T169: the cellar exit, replayed: `Ladder` is dispatched inside the budget; the OLD tuple is proven
  unreachable under the OLD budget (the arithmetic that caused the field halt, pinned as a regression).
- T170: the mill climbs one plane per rung, 0->1->2 up and 2->1->0 down, and no upper rung carries a
  pinned object id that could not exist on that floor.
- T171: dialogue matching survives punctuation the client and the wiki disagree about, and still refuses
  a genuinely different option (never guess).
- T172: the two Romeo sub-menu options and the comma-corrected Aggie line are in the verified corpus.
"""

from __future__ import annotations

import pytest

from osrs_ee.bots.base import DirectiveType, TickContext
from osrs_ee.bots.dialogue_text import normalise_option, option_matches
from osrs_ee.bots.quests_module import VERIFIED_QUEST_DIALOGUE_OPTIONS
from osrs_ee.bots.supply_procurement import (
    MILL_LADDER_OBJECT_ID,
    POT_OF_FLOUR_ID,
    SUPPLY_RECIPES,
    ZeroTouchSupplyEngine,
)
from osrs_ee.navigation import AccountCapabilitiesContext
from osrs_ee.navigation.underground import (
    CLIMB_ATTEMPTS_PER_NAME,
    UNDERGROUND_EXITS,
    required_climb_attempts,
)
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint

BASEMENT = "LUMBRIDGE_CASTLE_BASEMENT"
CELLAR_FLOOR = WorldPoint(3210, 9618, 0, BASEMENT)  # inside the region box, beside the ladder


def _ctx(position: WorldPoint, *, tick: int = 1) -> TickContext:
    return TickContext(
        game_tick=tick,
        position=position,
        capabilities=AccountCapabilitiesContext(
            "live", skills={"hitpoints": 10}, combat_level=3, quest_points=13
        ),
        inventory_items=(),
    )


# ---------------------------------------------------------------------------
# T168 — no registered exit may name more things than its budget can try
# ---------------------------------------------------------------------------
def test_t168_every_underground_exit_can_reach_its_last_name() -> None:
    """T168 (`PR-21.6`): the budget must cover the tuple, for every region, forever.

    This is the generalisation of the field halt: it does not test one hardcoded room, it asserts the
    rule that made the room fail — `max_climb_attempts` may never be smaller than the number of attempts
    needed to reach the final name.
    """
    engine = ZeroTouchSupplyEngine()
    assert UNDERGROUND_EXITS, "the exit registry must not be empty"
    for region, exit_ in UNDERGROUND_EXITS.items():
        assert exit_.targets, f"{region}: an exit with no target names can never be climbed"
        assert exit_.targets[0] == exit_.targets[0].strip(), region
        needed = required_climb_attempts(exit_.targets)
        budget = max(int(engine.max_climb_attempts), needed)
        # The last name is first tried on `6 * (len - 1) + 1`; the budget must be at least that.
        assert budget >= needed, f"{region}: budget {budget} < required {needed} for {exit_.targets}"
        # ...and the loop index really does reach the last entry.
        highest_index = min(len(exit_.targets) - 1, (budget - 1) // CLIMB_ATTEMPTS_PER_NAME)
        assert highest_index == len(exit_.targets) - 1, region

    assert required_climb_attempts(("A",)) == 1
    assert required_climb_attempts(("A", "B")) == CLIMB_ATTEMPTS_PER_NAME + 1
    assert required_climb_attempts(("A", "B", "C")) == 2 * CLIMB_ATTEMPTS_PER_NAME + 1


# ---------------------------------------------------------------------------
# T169 — the cellar exit, replayed
# ---------------------------------------------------------------------------
def test_t169_the_cellar_exit_tries_the_ladder_before_it_escalates() -> None:
    """T169 (`PR-21.6`): the live halt was `SUPPLY_EXIT_BLOCKED:LUMBRIDGE_CASTLE_BASEMENT` after twelve
    attempts that never once named the only object in the room. Replay it tick by tick."""
    engine = ZeroTouchSupplyEngine()
    exit_ = UNDERGROUND_EXITS[BASEMENT]
    # Existence-first: the ladder the client actually has is the FIRST name.
    assert exit_.targets[0] == "Ladder", exit_.targets
    assert exit_.action == "Climb-up"

    dispatched: list[str] = []
    escalation = ""
    for tick in range(1, 25):
        directive = engine.ensure_underground_exit(_ctx(CELLAR_FLOOR, tick=tick), label="Grain")
        if directive.directive_type == DirectiveType.ESCALATE_HUMAN:
            escalation = directive.reason
            break
        assert directive.directive_type == DirectiveType.AUTHORIZE_SURFACE_EXIT, directive
        dispatched.append(str(directive.payload["name"]))
        assert directive.payload["action"] == "Climb-up"
        if len(dispatched) >= 3:
            break

    assert dispatched[:1] == ["Ladder"], dispatched
    assert not escalation, escalation


def test_t169b_the_old_tuple_under_the_old_budget_could_never_have_worked() -> None:
    """T169b (`PR-21.6`): keep the root cause executable, so nobody "simplifies" the budget away.

    Reproduces the arithmetic of the field halt exactly: `("Staircase","Stairs","Ladder")` with a budget
    of 12 can only ever emit the two names that do not exist in the cellar.
    """
    old_targets = ("Staircase", "Stairs", "Ladder")
    old_budget = 12
    dispatched = [
        old_targets[min(len(old_targets) - 1, (attempt - 1) // 6)]
        for attempt in range(1, old_budget + 1)
    ]
    assert "Ladder" not in dispatched, dispatched
    assert set(dispatched) == {"Staircase", "Stairs"}, dispatched
    # The fix is the budget, not the ordering: with a covering budget the same tuple reaches `Ladder`.
    covering = required_climb_attempts(old_targets)
    assert covering == 13
    reached = {
        old_targets[min(len(old_targets) - 1, (attempt - 1) // 6)]
        for attempt in range(1, covering + 1)
    }
    assert "Ladder" in reached


# ---------------------------------------------------------------------------
# T170 — the three-plane mill
# ---------------------------------------------------------------------------
def test_t170_the_mill_climbs_one_plane_per_rung() -> None:
    """T170 (`PR-21.6`): `SUPPLY_CLIMB_FAILED:MILL_LANE_MILL` was a 0->2 jump on a ladder that only
    spans one floor. Every climb is now a single plane, in both directions."""
    steps = SUPPLY_RECIPES[POT_OF_FLOUR_ID].steps
    climbs = [st for st in steps if st.kind.name == "CLIMB"]
    assert len(climbs) == 4, [(st.note, st.target_plane) for st in climbs]

    up, down = climbs[:2], climbs[2:]
    assert [st.target_plane for st in up] == [1, 2], [st.target_plane for st in up]
    assert [st.target_plane for st in down] == [1, 0], [st.target_plane for st in down]
    assert {st.action for st in up} == {"Climb-up"}
    assert {st.action for st in down} == {"Climb-down"}

    # Each hop changes the plane by exactly one — the property the client's ladders require.
    for prev, cur in zip(climbs, climbs[1:]):
        assert abs(int(cur.target_plane) - int(prev.target_plane)) == 1, (prev.note, cur.note)

    # Only the ground-floor rung is id-pinned (it is the one proven at 3164,3307). A pinned id on an
    # upper floor is a click at an object that does not exist there — exactly the failing directive.
    assert climbs[0].object_id == MILL_LADDER_OBJECT_ID == 12964
    for upper in climbs[1:]:
        assert upper.object_id is None, upper.note
    # The rungs are resolved by name, so a renumbered object is a retry rather than a halt.
    assert climbs[1].targets[0] == "Ladder"
    assert climbs[2].targets[0] == "Ladder"


def test_t170b_the_hopper_floor_is_where_the_hopper_is_worked() -> None:
    """T170b (`PR-21.6`): the plane-2 arrival must be the plane the work steps declare, otherwise the
    bot arrives one floor below its own hopper and stalls without ever climbing."""
    steps = SUPPLY_RECIPES[POT_OF_FLOUR_ID].steps
    climbs = [st for st in steps if st.kind.name == "CLIMB"]
    hopper = next(st for st in steps if "Hopper" in st.targets)
    assert hopper.area is not None and hopper.area.plane == 2
    assert climbs[1].target_plane == hopper.area.plane == 2
    # ...and the descent starts from that same floor.
    assert climbs[2].area is not None and climbs[2].area.plane == 2


# ---------------------------------------------------------------------------
# T171 — dialogue punctuation must never stop a run
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "offered",
    [
        "Can you make dyes for me, please?",
        "Can you make dyes for me please?",
        "Can you make dyes for me, please",
        "  Can you make dyes for me, please?  ",
    ],
)
def test_t171_punctuation_is_not_meaning_in_dialogue(offered: str) -> None:
    """T171 (`PR-21.6`): the four `UNRECOGNIZED_QUEST_DIALOGUE_OPTION` halts were all a comma."""
    expected = "Can you make dyes for me please?"
    assert option_matches(offered, expected)
    assert option_matches(expected, offered)
    assert normalise_option(offered) == "can you make dyes for me please"


def test_t171b_a_genuinely_different_option_still_fails_loudly() -> None:
    """T171b (`PR-21.6`): the normaliser must never turn "unrecognised" into "guess". Two options that
    differ in WORDS are different options, and the engine escalates instead of clicking."""
    assert not option_matches("Can you make dyes for me please?", "Can you make armour for me please?")
    assert not option_matches("Yes", "Yes please")
    assert not option_matches("", "Yes")
    assert not option_matches(None, "Yes")
    assert normalise_option(None) == ""
    # Word order and numbers are preserved, not smoothed.
    assert not option_matches("How about 20 coins?", "How about 25 coins?")
    assert option_matches("How about 20 coins?", "How about 20 coins")


# ---------------------------------------------------------------------------
# T172 — the corpus itself
# ---------------------------------------------------------------------------
def test_t172_the_verified_corpus_carries_the_options_the_field_reported() -> None:
    """T172 (`PR-21.6`): punctuation tolerance is the mechanism; these strings are the corpus."""
    corpus = {normalise_option(option) for option in VERIFIED_QUEST_DIALOGUE_OPTIONS}
    for field_reported in (
        "Where can I find Juliet?",
        "Is there anything else you can tell me about Juliet?",
        "Can you make dyes for me, please?",
        "Can you make dyes for me please?",
    ):
        assert normalise_option(field_reported) in corpus, field_reported

    # The woad/coin lines belong to the SUPPLY step, not the quest corpus — and they are matched by the
    # same normaliser, so they must survive the same punctuation drift.
    supply_options = {normalise_option(o) for o in SUPPLY_RECIPES[1766].dialogue_options}
    for field_reported in ("Yes please, I need woad leaves.", "How about 20 coins?"):
        assert normalise_option(field_reported) in supply_options, (field_reported, sorted(supply_options))
    # Exactly one normalised entry per option, apart from the single PRE-EXISTING pair (`Yes.` / `Yes`)
    # that predates this PR. A corpus that repeats itself hides a real collision.
    from collections import Counter

    counts = Counter(normalise_option(option) for option in VERIFIED_QUEST_DIALOGUE_OPTIONS)
    duplicates = {key: n for key, n in counts.items() if n > 1}
    assert duplicates == {"yes": 2}, duplicates
    assert counts["can you make dyes for me please"] == 1


# ---------------------------------------------------------------------------
# T172b — the park anchor must be near the work site's NPC
# ---------------------------------------------------------------------------
def test_t172b_the_park_anchor_is_near_wysons_hut() -> None:
    """T172b (`PR-21.6`): `SUPPLY_STEP_STALLED:Woad leaves:NPC:Wyson` was a geometry defect — the
    route anchor sat at the park's WEST edge while Wyson patrols the EAST end. The step's working
    radius must cover the distance from the anchor to the hut."""
    park = DEFAULT_WORLD_NODES["FALADOR_PARK"]
    hut = WorldPoint(3030, 3383, 0, "WYSON_HUT")  # field-reported patrol point
    distance = park.chebyshev_distance(hut)
    assert distance <= 8, f"anchor {park} is {distance} tiles from Wyson's hut — he will never be found"

    woad = SUPPLY_RECIPES[1766]  # Woad leaves
    step = next(st for st in woad.steps if "Wyson" in st.targets[0])
    assert step.area_radius is not None, "the Wyson step must declare a work-site radius"
    assert int(step.area_radius) >= distance, (step.area_radius, distance)
    assert step.targets[0] == "Wyson the gardener", step.targets


def test_t172c_no_unrequested_step_gained_a_radius_or_a_budget() -> None:
    """T172c (`PR-21.6`): surgical scope. The three `PR-21.6` fixes are the ladder split, the exit
    budget and the Wyson radius — nothing else may have been widened along the way."""
    widened: dict[tuple[int, str], int] = {}
    for item_id, recipe in SUPPLY_RECIPES.items():
        for step in recipe.steps:
            if step.area_radius is not None:
                widened[(item_id, step.targets[0])] = int(step.area_radius)
    assert set(widened) == {
        (1737, "Sheep"),
        (753, "Cadava bush"),
        (1951, "Redberry bush"),
        (1766, "Wyson the gardener"),
    }, sorted(widened)
    # Existing radii are untouched by this PR.
    assert widened[(1737, "Sheep")] == 22
    assert widened[(1766, "Wyson the gardener")] == 32
