"""Zero-Touch Quest Supply Procurement Engine (`osrs_ee/bots/supply_procurement.py` — PR-19).

Constitutional Mandate (`Zero-Touch Invariant`): the operator NEVER touches the mouse or the game.
Every quest supply must be obtained by the engine itself, in priority cascade:

    1. `BANK`   -> withdraw already-banked supplies (cheapest, zero risk).
    2. `GATHER` -> walk to the verified OSRS world source and produce the item in-world
                   (pick / search / NPC dialogue craft / item-on-item combine).

> `GE_BUY` (Grand Exchange auto-buy) is deliberately **NOT** implemented in this module yet:
> the pinned upstream `microbot-2.6.34.jar` AgentServer exposes **no** `/grand-exchange` handler
> (verified endpoint catalog: `State, Inventory, Npc, Object, Walk, Bank, Dialogue, GroundItem,
> Skills, Script, Login, Logout, Screenshot, Varbit, Varp, Widget*, Settings, Keyboard,
> QuestHelper, StateMachineDebug, Profile, DynamicScriptDeploy, Observe, Path, VarChanges, Tile`),
> so a GE purchase would require blind `Widget 465` coordinate guessing — forbidden by
> `EXECUTION MANDATE §Zero Guessing`. It stays `PLANNED` until the Field Inspector captures the
> read-only `GET /widgets/search?groupId=465` tree on `8081`.

Design invariants:
- Pure & stateless-by-rederivation: every tick re-derives the next step from **live inventory counts**,
  so a crash / relog / partial progress resumes exactly where it stopped (idempotent, self-healing).
- Every step carries cumulative `produces` tuples (`(item_id, cumulative_qty)`), which makes repeated
  gathering (e.g. 3 Goblin mails) naturally resumable.
- No `time.sleep`, no HTTP, no sockets — returns declarative `TaskDirective` only.
- `PR-20.1` work-site radius: a step may declare `area_radius` when its target WANDERS (Fred's
  sheep pen), so the bot is not tethered to one anchor tile, plus a per-step `max_attempts` budget.
- `PR-20` consumption latch: a step that DESTROYS inventory (`uses_up`) is never "done" merely
  because it was dispatched; it completes only when the live inventory actually shrinks, and the next
  step waits out a short action-grace window (`settle_ticks`) so the client can finish its animation.
- Stall detection per `(item, step)`: alias rotation, then precise `SUPPLY_STEP_STALLED` escalation
  so the field operator gets an exact, actionable diagnostic line instead of a silent bank loop.

`PR-19.1` field fixes (from the live `acc_02` run, tick 337):
- **Progress-aware walk guard**: the walk budget counts only ticks in which the distance to the target
  did NOT shrink. A healthy 210-tile GE -> Lumbridge trek can never be aborted again; a genuinely
  blocked route (no distance reduction) still escalates with `SUPPLY_AREA_UNREACHABLE`.
- **Run-aware travel** (superseded by `PR-19.2`): run is a kernel side-channel, never a directive.
- **`PR-19.2` movement-first invariant**: `_travel()` ALWAYS returns `NAVIGATE_TO`; run enabling is a
  pipelined side command in the same tick as the walk (with a cooldown), so a client that lags in
  reporting `run_enabled=True` can never livelock the journey.
- **Plane-safe climbing**: `CLIMB` steps move the bot between floors (Lumbridge Castle spinning wheel
  lives on plane 1) instead of demanding a walk to an unreachable tile, with
  `SUPPLY_CLIMB_FAILED` as the precise diagnostic when a staircase cannot be used.
- **`PR-19.4` authorized exits**: `EXIT_UNDERGROUND` steps leave caves/cellars through an explicit
  `AUTHORIZE_SURFACE_EXIT` (the Stronghold pattern), and the engine refuses to walk from `y >= 5000`
  to a surface tile without one (`SUPPLY_EXIT_REQUIRED`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Mapping

from osrs_ee.bots.base import DirectiveType, TaskDirective, TickContext
from osrs_ee.navigation.underground import classify_underground, exit_for
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint

# ---------------------------------------------------------------------------
# Verified OSRS item IDs (OSRS Wiki `Item ID`) — used across the quest bundle
# ---------------------------------------------------------------------------
DAIRY_COW_OBJECT_ID = 8689
WHEAT_OBJECT_ID = 15506        # live: `Wheat` action "Pick" at (3162,3296)
MILL_DOOR_OBJECT_ID = 1521     # live: `Large door` (1521 / 1524) at (3166,3302)
FLOUR_BIN_OBJECT_ID = 1781     # live: `Flour bin` at (3166,3306)
REDBERRY_BUSH_OBJECT_ID = 23628  # live: 2x `Redberry bush` at (3272,3375) & (3277,3371)  # PR-20.3
SPINNING_WHEEL_OBJECT_ID = 14889  # live: `Spinning wheel` at (3209,3212) plane 1  # PR-20.2
MILL_LADDER_OBJECT_ID = 12964  # live: `Ladder` at (3164,3307)  # live `GET /objects?name=Dairy cow` -> id 8689 (Milk option)
POT_ID = 1931
BUCKET_ID = 1925
GRAIN_ID = 1947
SHEARS_ID = 1735
WOOL_ID = 1737
EGG_ID = 1944
BUCKET_OF_MILK_ID = 1927
POT_OF_FLOUR_ID = 1933
BALL_OF_WOOL_ID = 1759
CADAVA_BERRIES_ID = 753
GOBLIN_MAIL_ID = 288
ORANGE_GOBLIN_MAIL_ID = 286
BLUE_GOBLIN_MAIL_ID = 287
REDBERRY_ID = 1951
ONION_ID = 1957
WOAD_LEAF_ID = 1766
RED_DYE_ID = 1763
YELLOW_DYE_ID = 1765
BLUE_DYE_ID = 1767
ORANGE_DYE_ID = 1769

COINS_ID = 995  # Wyson (20 GP) + Aggie (5 GP per dye) — withdrawn from the bot's OWN banked earnings

MAX_STEP_ATTEMPTS = 26
MAX_RECURSION_DEPTH = 3


class SupplyStepKind(StrEnum):
    """Action kinds. Travel is never a standalone step: every action step carries an `area`, and the
    engine emits `NAVIGATE_TO` implicitly while the bot is still outside `walk_tolerance` of it.
    This structurally forbids the "walk back and forth" ping-pong class of bugs."""
    PICKUP = "PICKUP"
    OBJECT = "OBJECT"
    NPC = "NPC"
    COMBINE = "COMBINE"
    CLIMB = "CLIMB"
    # `PR-20.2` FIELD-PROVEN: some OSRS production actions open a **Make-X interface** instead of
    # producing directly. Live evidence (acc_02, `PR-20.1` run): `Spin` on the Lumbridge wheel opened
    # `groupId 270`, and the wool was only spun after clicking `childId 15` ("Ball of wool"). In that
    # run the click was performed MANUALLY by the field operator — which breaks the Zero-Touch
    # Invariant. This step kind makes the engine perform it itself.
    WIDGET = "WIDGET"
    ENTER_UNDERGROUND = "ENTER_UNDERGROUND"
    EXIT_UNDERGROUND = "EXIT_UNDERGROUND"


@dataclass(frozen=True)
class SupplyStep:
    """One declarative, resumable procurement step."""

    kind: SupplyStepKind
    area: WorldPoint | None = None
    targets: tuple[str, ...] = ()
    action: str = ""
    partner_id: int | None = None
    combine_source_id: int | None = None
    # CLIMB only: the plane this step must reach (e.g. Lumbridge Castle spinning wheel = plane 1).
    target_plane: int = 0
    # Live-verified scenery id (from `GET /objects`). When set, the interaction targets the exact object
    # instead of relying on display-name matching — far more robust across client rebuilds.
    object_id: int | None = None
    # `(item_id, cumulative_quantity)` achieved when this step has completed at least once.
    produces: tuple[tuple[int, int], ...] = ()
    # Items that MUST be in inventory before this step can run (recursively procured).
    consumes: tuple[tuple[int, int], ...] = ()
    # `PR-20` consumption latch: items this step **destroys** in the world (e.g. the hopper eats the
    # grain). A produce-less step that declares `uses_up` is NOT complete on dispatch — it only counts
    # as done once the live inventory actually shrinks. This is the exact field defect of tick 289:
    # `Fill` was "done" the instant it was clicked, so `Operate` and `Climb-down` fired 600 ms later
    # while the character was still walking, leaving the grain in the bag and the flour bin empty.
    uses_up: tuple[tuple[int, int], ...] = ()
    # `PR-20` action grace: ticks that must elapse after this step before the NEXT step of the same
    # chain may be dispatched (`None` = engine default). Prevents the client from being interrupted
    # mid-animation.
    settle_ticks: int | None = None
    # `PR-20.1` FIELD FIX (incident #7): a step whose target WANDERS cannot be served by one anchor
    # tile + the global 3-tile tolerance. Fred's sheep roam a ~20x20 pen, so a bot standing next to a
    # sheep 6 tiles from the anchor tile was declared "outside the area" and dragged back — the
    # tether ping-pong. `area_radius` widens the *work site*, not the routing: outside it the engine
    # still navigates normally.
    area_radius: int | None = None
    # `PR-20.1` per-step attempt budget (`None` = engine default `MAX_STEP_ATTEMPTS`). Wandering
    # livestock genuinely needs more than 26 ticks between successful shearings.
    max_attempts: int | None = None
    # `PR-20.2` `WIDGET` steps: the Make-X production interface coordinates (live: `270` / `15`) and
    # the clickable label, used both for the wire payload and for text-based fallback.
    widget_group: int | None = None
    widget_child: int | None = None
    # `PR-20.2` minimum ticks between two dispatches of the SAME step. A Make-X product needs ~60s
    # of server-side production; re-clicking every tick would be pointless command spam.
    redispatch_hold: int | None = None
    note: str = ""


@dataclass(frozen=True)
class SupplyRecipe:
    item_id: int
    steps: tuple[SupplyStep, ...]
    # Ordered, Wiki-verbatim dialogue options that drive this recipe's NPC craft.
    dialogue_options: tuple[str, ...] = ()
    label: str = ""


def _at(label: str, *, plane: int = 0) -> WorldPoint:
    """Resolve a verified waypoint node id to its immutable world point."""
    node = DEFAULT_WORLD_NODES[label]
    assert node.plane == plane, f"{label} is plane {node.plane}, not {plane}"
    return node


# ---------------------------------------------------------------------------
# Verified Zero-Touch Supply Recipes (every coordinate pinned to OSRS Wiki map data)
# ---------------------------------------------------------------------------
# Priority design note: `Romeo & Juliet` (5 QP) + `Goblin Diplomacy` (5 QP) == exactly 10 QP, the
# Jagex GE trade-unlock gate, and both are single-plane surface chains with ZERO stairs.
SUPPLY_RECIPES: dict[int, SupplyRecipe] = {
    # =================================================== COOK'S ASSISTANT
    EGG_ID: SupplyRecipe(
        item_id=EGG_ID,
        label="Egg",
        steps=(
            SupplyStep(
                kind=SupplyStepKind.PICKUP,
                area=_at("LUMBRIDGE_CHICKEN_COOP"),
                targets=("Egg",),
                action="Take",
                produces=((EGG_ID, 1),),
                note="6 egg spawns inside/next to the Groats' farm chicken coop",
            ),
        ),
    ),
    POT_ID: SupplyRecipe(
        item_id=POT_ID,
        label="Pot",
        steps=(
            SupplyStep(
                kind=SupplyStepKind.PICKUP,
                area=_at("LUMBRIDGE_KITCHEN"),
                targets=("Pot",),
                action="Take",
                produces=((POT_ID, 1),),
                note="pot spawn in the Lumbridge Castle kitchen (3209,3214)",
            ),
        ),
    ),
    BUCKET_ID: SupplyRecipe(
        item_id=BUCKET_ID,
        label="Bucket",
        steps=(
            SupplyStep(
                # `PR-20`: `(3216,9625)` is a DIFFERENT map region, not a walk away. The engine needs an
                # explicit, authorized descent through the kitchen staircase before it can stand next
                # to the bucket — mirroring `EXIT_UNDERGROUND` on the way out.
                kind=SupplyStepKind.ENTER_UNDERGROUND,
                area=_at("LUMBRIDGE_KITCHEN"),
                targets=("Staircase", "Stairs", "Ladder"),
                action="Climb-down",
                note="Lumbridge Castle kitchen staircase down into the cellar",
            ),
            SupplyStep(
                kind=SupplyStepKind.PICKUP,
                area=_at("LUMBRIDGE_CASTLE_BASEMENT"),
                targets=("Bucket",),
                action="Take",
                produces=((BUCKET_ID, 1),),
                note="bucket spawn in the Lumbridge Castle basement (3216,9625)",
            ),
            SupplyStep(
                kind=SupplyStepKind.EXIT_UNDERGROUND,
                area=_at("LUMBRIDGE_CASTLE_BASEMENT"),
                targets=("Staircase", "Stairs", "Ladder"),
                action="Climb-up",
                note="kitchen staircase out of the cellar (authorized surface exit)",
            ),
        ),
    ),
    BUCKET_OF_MILK_ID: SupplyRecipe(
        item_id=BUCKET_OF_MILK_ID,
        label="Bucket of milk",
        steps=(
            SupplyStep(
                # `PR-19.5` FIELD-PROVEN: the milkable "Dairy cow" is a **GAME OBJECT** (`id=8689`),
                # NOT an NPC. Live probe: `GET /npcs` lists only "Cow" (2790) and "Cow calf" (2794) —
                # plain cows cannot be milked — while `GET /objects?name=Dairy cow` returns
                # `{id: 8689, name: "Dairy cow", position: {3172, 3317}}`. Sending `Milk` to
                # `/npcs/interact` produced no item and burned the whole attempt budget.
                kind=SupplyStepKind.OBJECT,
                area=_at("LUMBRIDGE_COW_FIELD"),
                targets=("Dairy cow",),
                action="Milk",
                object_id=DAIRY_COW_OBJECT_ID,
                consumes=((BUCKET_ID, 1),),
                produces=((BUCKET_OF_MILK_ID, 1),),
                note="dairy cow OBJECT 8689 at Lumbridge West farm (3172,3317)",
            ),
        ),
    ),
    GRAIN_ID: SupplyRecipe(
        item_id=GRAIN_ID,
        label="Grain",
        steps=(
            SupplyStep(
                kind=SupplyStepKind.OBJECT,
                area=_at("MILL_LANE_WHEAT_FIELD"),
                targets=("Wheat",),
                action="Pick",
                object_id=WHEAT_OBJECT_ID,
                produces=((GRAIN_ID, 1),),
                note="Lumbridge wheat field (3162,3296) — object 15506 `Pick`",
            ),
        ),
    ),
    POT_OF_FLOUR_ID: SupplyRecipe(
        item_id=POT_OF_FLOUR_ID,
        label="Pot of flour",
        steps=(
            # `PR-19.6` Lumbridge windmill, verified live object ids at every stage.
            SupplyStep(
                kind=SupplyStepKind.OBJECT,
                area=_at("MILL_LANE_MILL"),
                targets=("Large door", "Door"),
                action="Open",
                object_id=MILL_DOOR_OBJECT_ID,
                # Everything the mill consumes is procured BEFORE entering: grain (wheat field) and an
                # empty pot (kitchen) are both ground-floor errands, so the bot can never strand itself
                # upstairs on the hopper floor looking for ingredients it should already hold.
                consumes=((GRAIN_ID, 1), (POT_ID, 1)),
                # Opening a door is instantaneous; the ladder step needs no grace window.
                settle_ticks=0,
                note="mill entrance door 1521 at (3166,3302)",
            ),
            SupplyStep(
                kind=SupplyStepKind.CLIMB,
                area=_at("MILL_LANE_MILL"),
                targets=("Ladder", "Staircase", "Stairs"),
                action="Climb-up",
                target_plane=2,
                object_id=MILL_LADDER_OBJECT_ID,
                note="ladder 12964 up to the hopper floor (plane 2)",
            ),
            SupplyStep(
                kind=SupplyStepKind.OBJECT,
                area=_at("LUMBRIDGE_WINDMILL_TOP", plane=2),
                targets=("Hopper",),
                action="Fill",
                consumes=((GRAIN_ID, 1),),
                # `PR-20` FIELD FIX (tick 289): the hopper EATS the grain. Until that grain has actually
                # left the inventory, this step is not done — never advance to `Operate`/`Climb-down`.
                uses_up=((GRAIN_ID, 1),),
                note="top floor hopper: Fill with grain",
            ),
            SupplyStep(
                kind=SupplyStepKind.OBJECT,
                area=_at("LUMBRIDGE_WINDMILL_TOP", plane=2),
                targets=("Hopper controls", "Hopper control"),
                action="Operate",
                note="operate the controls so the grain drops into the flour bin",
            ),
            SupplyStep(
                kind=SupplyStepKind.CLIMB,
                area=_at("LUMBRIDGE_WINDMILL_TOP", plane=2),
                targets=("Ladder", "Staircase", "Stairs"),
                action="Climb-down",
                target_plane=0,
                object_id=MILL_LADDER_OBJECT_ID,
                note="back down to the ground floor",
            ),
            SupplyStep(
                kind=SupplyStepKind.OBJECT,
                area=_at("MILL_LANE_MILL"),
                targets=("Flour bin",),
                action="Empty",
                object_id=FLOUR_BIN_OBJECT_ID,
                consumes=((POT_ID, 1),),
                produces=((POT_OF_FLOUR_ID, 1),),
                note="ground-floor flour bin 1781 (3166,3306): empty it into the Pot",
            ),
        ),
    ),
    # =================================================== SHEEP SHEARER
    SHEARS_ID: SupplyRecipe(
        item_id=SHEARS_ID,
        label="Shears",
        steps=(
            SupplyStep(
                kind=SupplyStepKind.PICKUP,
                area=_at("LUMBRIDGE_SHEEP_FARM"),
                targets=("Shears",),
                action="Take",
                produces=((SHEARS_ID, 1),),
                note="shears ground spawn at (3192,3272); Fred also hands them out",
            ),
        ),
    ),
    WOOL_ID: SupplyRecipe(
        item_id=WOOL_ID,
        label="Wool",
        steps=(
            SupplyStep(
                kind=SupplyStepKind.NPC,
                area=_at("LUMBRIDGE_SHEEP_FARM"),
                targets=("Sheep",),
                action="Shear",
                consumes=((SHEARS_ID, 1),),
                produces=((WOOL_ID, 20),),
                # `PR-20.1` FIELD FIX (incident #7, tick 431): `LUMBRIDGE_SHEEP_FARM` is a single tile
                # at the pen's corner and the sheep roam a ~20x20 pen. With the global 3-tile
                # tolerance the engine pulled the bot back to that corner the moment it chased a sheep
                # 6 tiles away — `Shear` -> dragged back -> `Shear` -> ... until
                # `SUPPLY_STEP_STALLED:Wool:NPC:Sheep`.
                # 22 tiles is the chebyshev distance from the corner anchor (3190,3273) to the
                # pen's far corner (3212,3258), so the ENTIRE documented pen is a valid work site
                # (T125 asserts every corner of the rectangle is inside).
                area_radius=22,
                # 26 attempts (~16s) is far too tight for 20 wandering sheep; 80 leaves room for
                # failed shears ("It's not in the mood") while still escalating a truly broken pen.
                max_attempts=80,
                note="shear 20x inside the pen (repeat step, Ram/'The Thing' cannot be sheared)",
            ),
        ),
    ),
    BALL_OF_WOOL_ID: SupplyRecipe(
        item_id=BALL_OF_WOOL_ID,
        label="Ball of wool",
        steps=(
            SupplyStep(
                kind=SupplyStepKind.CLIMB,
                area=_at("LUMBRIDGE_CASTLE_NORTH_STAIRS"),
                targets=("Staircase", "Stairs", "Ladder"),
                action="Climb-up",
                target_plane=1,
                # `PR-20` STRANDING FIX: the climb must not happen until the wool is actually in the
                # bag, otherwise the bot walks upstairs empty-handed, realises the spin wheel needs
                # wool, walks back down to shear, and can never climb again (the stair latch has
                # already fired). Acquire first, then climb — same rule as the mill door.
                consumes=((WOOL_ID, 20),),
                note="Lumbridge Castle north staircase -> 1st floor (spin wheel is NOT reachable on plane 0)",
            ),
            SupplyStep(
                # `PR-20.2` LIVE-PROVEN: `Spin` does NOT produce the balls by itself — it opens the
                # Make-X production interface (`groupId 270`). Targeting the object by name alone was
                # never exercised live before this run.
                kind=SupplyStepKind.OBJECT,
                area=_at("LUMBRIDGE_SPINNING_WHEEL", plane=1),
                targets=("Spinning wheel",),
                action="Spin",
                object_id=SPINNING_WHEEL_OBJECT_ID,
                # Opening the interface is instantaneous; the next step needs no grace window.
                settle_ticks=0,
                note="Lumbridge Castle 1st floor spin wheel 14889 (3209,3212, plane 1) — opens Make-X",
            ),
            SupplyStep(
                # The actual production click. Without this the bot stands in front of an open
                # interface forever (the live `PR-20.1` run needed a HUMAN click here).
                kind=SupplyStepKind.WIDGET,
                targets=("Ball of wool",),
                action="Make-X",
                widget_group=270,
                widget_child=15,
                consumes=((WOOL_ID, 20),),
                produces=((BALL_OF_WOOL_ID, 20),),
                # Make-all of 20 balls takes ~36s server-side; re-click only every 8 ticks and let the
                # progress-aware budget reset on each produced ball.
                redispatch_hold=8,
                max_attempts=120,
                note="Make-X interface 270:15 (Ball of wool) — make-all",
            ),
        ),
    ),
    # =================================================== ROMEO & JULIET
    CADAVA_BERRIES_ID: SupplyRecipe(
        item_id=CADAVA_BERRIES_ID,
        label="Cadava berries",
        steps=(
            SupplyStep(
                kind=SupplyStepKind.OBJECT,
                area=_at("VARROCK_CADAVA_BUSHES"),
                targets=("Cadava bush", "Cadava berry bush", "Cadava bush (wild)"),
                action="Pick",
                produces=((CADAVA_BERRIES_ID, 1),),
                # `PR-20.2` PRE-EMPTIVE (same class as incident #7): the cadava bushes are a CLUSTER
                # spanning (3264,3366)-(3277,3374); the anchor sits inside it, so the far corner is
                # 9 tiles away and the 3-tile gate would tether the bot exactly like the sheep pen.
                area_radius=10,
                note="cadava bushes (3264,3366)-(3277,3374), west of the East Varrock mine",
            ),
        ),
    ),
    # =================================================== GOBLIN DIPLOMACY
    WOAD_LEAF_ID: SupplyRecipe(
        item_id=WOAD_LEAF_ID,
        label="Woad leaves",
        dialogue_options=("Yes please, I need woad leaves.", "How about 20 coins?"),
        steps=(
            SupplyStep(
                kind=SupplyStepKind.NPC,
                area=_at("FALADOR_PARK"),
                targets=("Wyson the gardener", "Wyson"),
                action="Talk-to",
                # `PR-20`: 2 woad leaves are NOT free — Wyson asks for 20 GP. The coins are withdrawn
                # from the bot's OWN banked earnings (never handed over by the operator).
                consumes=((COINS_ID, 20),),
                produces=((WOAD_LEAF_ID, 2),),
                note="Wyson sells 2 woad leaves for 20 GP",
            ),
        ),
    ),
    BLUE_DYE_ID: SupplyRecipe(
        item_id=BLUE_DYE_ID,
        label="Blue dye",
        dialogue_options=(
            "Can you make dyes for me please?",
            "What do you need to make blue dye?",
            "Okay, make me some blue dye please.",
        ),
        steps=(
            SupplyStep(
                kind=SupplyStepKind.NPC,
                area=_at("DRAYNOR_AGGIE"),
                targets=("Aggie",),
                action="Talk-to",
                consumes=((WOAD_LEAF_ID, 2), (COINS_ID, 5)),
                produces=((BLUE_DYE_ID, 1),),
                note="Aggie crafts blue dye from 2 woad leaves + 5 GP",
            ),
        ),
    ),
    REDBERRY_ID: SupplyRecipe(
        item_id=REDBERRY_ID,
        label="Redberries",
        steps=(
            SupplyStep(
                kind=SupplyStepKind.OBJECT,
                area=_at("VARROCK_REDBERRY_BUSHES"),
                targets=("Redberry bush",),
                action="Pick from",
                # `PR-20.3` LIVE: both bushes are object `23628`; name-matching alone is a coin flip.
                object_id=REDBERRY_BUSH_OBJECT_ID,
                produces=((REDBERRY_ID, 3),),
                # `PR-20.2`/`PR-20.3`: two separate wild bushes ~6 tiles apart. With the anchor now at
                # their midpoint, radius 6 covers both and their immediate surroundings.
                area_radius=6,
                note="two wild redberry bushes 23628 at (3272,3375) and (3277,3371)",
            ),
        ),
    ),
    ONION_ID: SupplyRecipe(
        item_id=ONION_ID,
        label="Onions",
        steps=(
            SupplyStep(
                kind=SupplyStepKind.OBJECT,
                area=_at("FRED_ONION_GARDEN"),
                targets=("Onion", "Onions"),
                action="Pick",
                produces=((ONION_ID, 2),),
                note="Fred's onion garden spawns (3188-3191, 3266-3268)",
            ),
        ),
    ),
    RED_DYE_ID: SupplyRecipe(
        item_id=RED_DYE_ID,
        label="Red dye",
        dialogue_options=(
            "Can you make dyes for me please?",
            "What do you need to make red dye?",
            "Okay, make me some red dye please.",
        ),
        steps=(
            SupplyStep(
                kind=SupplyStepKind.NPC,
                area=_at("DRAYNOR_AGGIE"),
                targets=("Aggie",),
                action="Talk-to",
                consumes=((REDBERRY_ID, 3), (COINS_ID, 5)),
                produces=((RED_DYE_ID, 1),),
                note="Aggie crafts red dye from 3 redberries + 5 GP",
            ),
        ),
    ),
    YELLOW_DYE_ID: SupplyRecipe(
        item_id=YELLOW_DYE_ID,
        label="Yellow dye",
        dialogue_options=(
            "Can you make dyes for me please?",
            "What do you need to make yellow dye?",
            "Okay, make me some yellow dye please.",
        ),
        steps=(
            SupplyStep(
                kind=SupplyStepKind.NPC,
                area=_at("DRAYNOR_AGGIE"),
                targets=("Aggie",),
                action="Talk-to",
                consumes=((ONION_ID, 2), (COINS_ID, 5)),
                produces=((YELLOW_DYE_ID, 1),),
                note="Aggie crafts yellow dye from 2 onions + 5 GP",
            ),
        ),
    ),
    ORANGE_DYE_ID: SupplyRecipe(
        item_id=ORANGE_DYE_ID,
        label="Orange dye",
        steps=(
            SupplyStep(
                kind=SupplyStepKind.COMBINE,
                partner_id=YELLOW_DYE_ID,
                combine_source_id=RED_DYE_ID,
                consumes=((RED_DYE_ID, 1), (YELLOW_DYE_ID, 1)),
                produces=((ORANGE_DYE_ID, 1),),
                note="combine red dye + yellow dye -> orange dye (never dye the mail with them separately)",
            ),
        ),
    ),
    BLUE_GOBLIN_MAIL_ID: SupplyRecipe(
        item_id=BLUE_GOBLIN_MAIL_ID,
        label="Blue goblin mail",
        steps=(
            SupplyStep(
                kind=SupplyStepKind.COMBINE,
                partner_id=GOBLIN_MAIL_ID,
                combine_source_id=BLUE_DYE_ID,
                consumes=((BLUE_DYE_ID, 1), (GOBLIN_MAIL_ID, 1)),
                produces=((BLUE_GOBLIN_MAIL_ID, 1),),
                note="use blue dye on goblin mail",
            ),
        ),
    ),
    ORANGE_GOBLIN_MAIL_ID: SupplyRecipe(
        item_id=ORANGE_GOBLIN_MAIL_ID,
        label="Orange goblin mail",
        steps=(
            SupplyStep(
                kind=SupplyStepKind.COMBINE,
                partner_id=GOBLIN_MAIL_ID,
                combine_source_id=ORANGE_DYE_ID,
                consumes=((ORANGE_DYE_ID, 1), (GOBLIN_MAIL_ID, 1)),
                produces=((ORANGE_GOBLIN_MAIL_ID, 1),),
                note="use orange dye on goblin mail",
            ),
        ),
    ),
    GOBLIN_MAIL_ID: SupplyRecipe(
        item_id=GOBLIN_MAIL_ID,
        label="Goblin mail",
        steps=(
            SupplyStep(
                kind=SupplyStepKind.OBJECT,
                area=_at("GOBLIN_VILLAGE"),
                targets=("Crate",),
                action="Search",
                produces=((GOBLIN_MAIL_ID, 1),),
                note="crate behind the generals' hut",
            ),
            SupplyStep(
                kind=SupplyStepKind.OBJECT,
                area=_at("GOBLIN_VILLAGE"),
                targets=("Crate",),
                action="Search",
                produces=((GOBLIN_MAIL_ID, 2),),
                note="crate inside the western hut",
            ),
            SupplyStep(
                kind=SupplyStepKind.OBJECT,
                area=_at("GOBLIN_VILLAGE"),
                targets=("Crate",),
                action="Search",
                produces=((GOBLIN_MAIL_ID, 3),),
                note="crate up the ladder near the village gate",
            ),
        ),
    ),
}


# Human-readable labels for items that have no recipe of their own (they are WITHDRAWN, not crafted).
ITEM_LABELS: dict[int, str] = {COINS_ID: "Coins"}


def _inv_qty(items: tuple[Mapping[str, Any], ...], item_id: int) -> int:
    return sum(
        int(it.get("quantity", 0))
        for it in items
        if int(it["item_id"] if "item_id" in it else it.get("id", -1)) == item_id
        and not bool(it.get("noted", False))
    )


@dataclass
class ZeroTouchSupplyEngine:
    """Stateless-by-rederivation procurement controller (zero `sleep`, zero HTTP)."""

    recipes: Mapping[int, SupplyRecipe] = field(default_factory=lambda: SUPPLY_RECIPES)
    walk_tolerance: int = 3
    max_step_attempts: int = MAX_STEP_ATTEMPTS
    max_climb_attempts: int = 12
    # `PR-19.1` field fix: this budget now counts *stalled* walk ticks (ticks with ZERO reduction in
    # distance to the target), NOT total walk ticks. The live run proved the old 120-tick cap aborted a
    # perfectly healthy 210-tile GE -> Lumbridge trek at tick 337 while the bot was still progressing.
    max_walk_attempts: int = 350
    # Superseded by the kernel travel run-assist (`PR-19.2`): the engine itself never toggles run.
    run_min_energy_pct: int = 20
    _attempts: dict[tuple[int, int], int] = field(default_factory=dict)
    _alias_cursor: dict[tuple[int, int], int] = field(default_factory=dict)
    _progress: dict[tuple[int, int], int] = field(default_factory=dict)
    _done: set[tuple[int, int]] = field(default_factory=set)
    _walk_attempts: dict[tuple[int, int], int] = field(default_factory=dict)
    _walk_best_distance: dict[tuple[int, int], float] = field(default_factory=dict)
    _emitted: set[tuple[int, int]] = field(default_factory=set)
    _active_item_id: int | None = None
    _escalation: str = ""
    _recurse: Callable[[TickContext, int, int, str], TaskDirective | None] | None = None
    # `PR-20` consumption latch: key -> inventory snapshot taken when the consuming action was
    # dispatched `((item_id, qty_at_dispatch), ...)`. The step is complete only once every entry has
    # dropped BELOW its snapshot (i.e. the world actually took the item).
    _pending_consumption: dict[tuple[int, int], tuple[tuple[int, int], ...]] = field(default_factory=dict)
    _consume_wait: dict[tuple[int, int], int] = field(default_factory=dict)
    # `PR-20` action grace: key -> first game tick at which the FOLLOWING step may be dispatched.
    _settle_until: dict[tuple[int, int], int] = field(default_factory=dict)
    _settle_waits: dict[tuple[int, int], int] = field(default_factory=dict)
    # `PR-20.2`: key -> first game tick at which the SAME step may be dispatched again.
    _hold_until: dict[tuple[int, int], int] = field(default_factory=dict)
    # Ticks the client is left alone after an action so its walk/animation can finish.
    settle_ticks: int = 3
    # How long the engine re-issues a consuming action while waiting for the inventory to shrink.
    max_consume_wait: int = 16

    # -- public API ---------------------------------------------------------
    @property
    def active_item_id(self) -> int | None:
        return self._active_item_id

    @property
    def active_dialogue_options(self) -> tuple[str, ...]:
        if self._active_item_id is None:
            return ()
        recipe = self.recipes.get(self._active_item_id)
        return recipe.dialogue_options if recipe else ()

    def farmed_count(self, item_id: int) -> int:
        return sum(
            qty
            for (iid, _idx), attempts in self._attempts.items()
            if iid == item_id and attempts > 0
        )

    def ensure(
        self,
        ctx: TickContext,
        item_id: int,
        quantity: int,
        *,
        recurse: Callable[[TickContext, int, int, str], TaskDirective | None] | None = None,
    ) -> TaskDirective | None:
        """Return the single next directive needed to obtain `quantity` x `item_id`, or `None` if done.

        `recurse` lets the caller inject the FULL cascade (inventory -> bank -> gather) for nested
        ingredients, so a banked dye is withdrawn instead of being re-crafted from scratch.
        """
        self._recurse = recurse
        directive = self._ensure(ctx, item_id, quantity, depth=0)
        if directive is not None and directive.directive_type != DirectiveType.ESCALATE_HUMAN:
            self._active_item_id = item_id
        return directive

    def escalation_reason(self) -> str:
        return self._escalation

    # -- internals ----------------------------------------------------------
    def _ensure(self, ctx: TickContext, item_id: int, quantity: int, *, depth: int) -> TaskDirective | None:
        if _inv_qty(ctx.inventory_items, item_id) >= quantity:
            if depth == 0:
                self._attempts.clear()
            return None
        if depth > MAX_RECURSION_DEPTH:
            return self._escalate(f"SUPPLY_RECURSION_LIMIT:{item_id}")
        recipe = self.recipes.get(item_id)
        if recipe is None:
            return None

        for idx, step in enumerate(recipe.steps):
            key = (recipe.item_id, idx)
            # `PR-20` action grace: never dispatch the next step of a chain inside the settle window of
            # the previous one. In the field the bot was still walking/animately busy 600 ms after
            # clicking, so `Operate` + `Climb-down` were swallowed.
            settle_key = (recipe.item_id, idx - 1)
            settle_until = self._settle_until.get(settle_key)
            if settle_until is not None:
                waits = self._settle_waits.get(settle_key, 0)
                # Anti-deadlock: a client that stops reporting ticks must never freeze the chain.
                if ctx.game_tick < settle_until and waits <= self.settle_ticks * 3:
                    self._settle_waits[settle_key] = waits + 1
                    return TaskDirective(
                        directive_type=DirectiveType.WAIT_NEXT_TICK,
                        reason=(
                            f"Zero-Touch supply: letting {recipe.label} settle "
                            f"({max(0, settle_until - ctx.game_tick)} tick(s) of action grace left)"
                        ),
                    )
                self._settle_until.pop(settle_key, None)
                self._settle_waits.pop(settle_key, None)
            # `PR-20` consumption latch: a consuming step is only complete when the item is GONE.
            pending = self._pending_consumption.get(key)
            if pending is not None:
                if all(
                    _inv_qty(ctx.inventory_items, iid) < baseline for iid, baseline in pending
                ):
                    self._pending_consumption.pop(key, None)
                    self._consume_wait.pop(key, None)
                    self._done.add(key)
                    self._settle_until[key] = ctx.game_tick + self._settle_for(step)
                    continue
                waits = self._consume_wait.get(key, 0) + 1
                self._consume_wait[key] = waits
                if waits > self.max_consume_wait:
                    return self._escalate(
                        f"SUPPLY_STEP_STALLED:{recipe.label}:{step.kind}:"
                        f"{step.targets[0] if step.targets else recipe.label}"
                    )
                # The client has not applied the consumption yet: walk to the object if needed and
                # re-issue the action (idempotent for a hopper: "The hopper is already full").
                if step.area is not None and not self._at_area(ctx, step):
                    return self._travel(ctx, step, recipe)
                if ctx.bank_is_open:
                    return TaskDirective(
                        directive_type=DirectiveType.BANK_TURNAROUND,
                        payload={"deposit_inventory": False, "withdraw": [], "close_when_done": True},
                        reason=f"Zero-Touch supply: closing bank while stoking {recipe.label}",
                    )
                return self._emit(ctx, recipe, idx, step)
            if step.produces:
                if all(_inv_qty(ctx.inventory_items, pid) >= qty for pid, qty in step.produces):
                    continue
            elif key in self._done:
                continue
            # CRITICAL ORDERING: ingredients are resolved BEFORE any bank-close guard, because a nested
            # ingredient (e.g. Orange dye 1769) is often BANKED and must be withdrawn, which needs the
            # bank OPEN. Closing first caused an open/close ping-pong stall in the T68 wire fixture.
            # A repeat step CONSUMES its input while producing (e.g. 20 wool -> 20 balls of wool), so the
            # ingredient gate is only enforced BEFORE the first unit is produced, never mid-loop.
            already_producing = bool(step.produces) and _inv_qty(ctx.inventory_items, step.produces[0][0]) > 0
            for needed_id, needed_qty in (() if already_producing else step.consumes):
                if _inv_qty(ctx.inventory_items, needed_id) >= needed_qty:
                    continue
                if self._recurse is not None:
                    needed_recipe = self.recipes.get(needed_id)
                    needed_label = (
                        needed_recipe.label
                        if needed_recipe is not None
                        else ITEM_LABELS.get(needed_id, str(needed_id))
                    )
                    nested = self._recurse(ctx, needed_id, needed_qty, needed_label)
                    if nested is not None:
                        return nested
                    continue
                nested = self._ensure(ctx, needed_id, needed_qty, depth=depth + 1)
                if nested is not None:
                    return nested
                continue
            # `PR-19.4`: leaving a cave/basement is an AUTHORIZED exit, never accidental displacement.
            # Handled before the area gate so the bot never tries to walk to a surface tile from y>=5000.
            if step.kind == SupplyStepKind.EXIT_UNDERGROUND:
                if not classify_underground(ctx.position.x, ctx.position.y):
                    continue  # already on the surface
                return self._emit_underground_exit(
                    ctx,
                    key,
                    label=recipe.label,
                    targets=step.targets or ("Staircase", "Stairs", "Ladder"),
                    action=step.action or "Climb-up",
                    area_label=step.area.label if step.area else "",
                )

            # `PR-20`: entering a cellar/cave is an explicit AUTHORIZED descent, never a `/walk` into a
            # different map region. Stateless: the live classification IS the latch.
            if step.kind == SupplyStepKind.ENTER_UNDERGROUND:
                if classify_underground(ctx.position.x, ctx.position.y):
                    continue
                if ctx.bank_is_open:
                    return TaskDirective(
                        directive_type=DirectiveType.BANK_TURNAROUND,
                        payload={"deposit_inventory": False, "withdraw": [], "close_when_done": True},
                        reason=f"Zero-Touch supply: closing bank before descending for {recipe.label}",
                    )
                if step.area is not None and not self._at_area(ctx, step):
                    return self._travel(ctx, step, recipe)
                descents = self._walk_attempts.get(key, 0) + 1
                self._walk_attempts[key] = descents
                if descents > self.max_climb_attempts:
                    return self._escalate(
                        f"SUPPLY_ENTER_FAILED:{step.area.label if step.area else recipe.label}"
                    )
                down_payload: dict[str, Any] = {
                    "name": self._pick_alias(recipe.item_id, idx, step),
                    "action": step.action or "Climb-down",
                }
                if step.object_id is not None:
                    down_payload["id"] = int(step.object_id)
                return TaskDirective(
                    directive_type=DirectiveType.INTERACT_OBJECT,
                    payload=down_payload,
                    reason=f"Zero-Touch supply: descending into the cellar for {recipe.label}",
                )

            # CLIMB is resolved before the surface area gate: once we are on the target plane there is
            # nothing left to do, and walking back down to the stair tile would be a ping-pong bug.
            if step.kind == SupplyStepKind.CLIMB:
                if ctx.position.plane == step.target_plane:
                    # Arrived -> remember it, otherwise a LATER climb-down would re-trigger this
                    # climb-up forever (stair ping-pong observed while validating the mill chain).
                    self._done.add(key)
                    continue
                if key in self._done:
                    continue
                if ctx.bank_is_open:
                    return TaskDirective(
                        directive_type=DirectiveType.BANK_TURNAROUND,
                        payload={"deposit_inventory": False, "withdraw": [], "close_when_done": True},
                        reason=f"Zero-Touch supply: closing bank before climbing for {recipe.label}",
                    )
                climbs = self._walk_attempts.get(key, 0) + 1
                self._walk_attempts[key] = climbs
                if climbs > self.max_climb_attempts:
                    return self._escalate(f"SUPPLY_CLIMB_FAILED:{step.area.label if step.area else recipe.label}")
                climb_payload: dict[str, Any] = {
                    "name": self._pick_alias(recipe.item_id, idx, step),
                    "action": step.action or "Climb-up",
                }
                if step.object_id is not None:
                    climb_payload["id"] = int(step.object_id)
                return TaskDirective(
                    directive_type=DirectiveType.INTERACT_OBJECT,
                    payload=climb_payload,
                    reason=f"Zero-Touch supply: climbing to plane {step.target_plane} for {recipe.label}",
                )

            # Travel is implicit: any step with an `area` first walks there (never a standalone step).
            if (
                step.area is not None
                and ctx.position.y >= 5000
                and step.area.y < 5000
                and not self._at_area(ctx, step)
            ):
                # Any requested item may be produced on the surface, so leaving the cave is mandatory.
                # The registered exit route makes this an AUTHORIZED action (never accidental
                # displacement); if no route is registered for this region we fail loudly instead of
                # issuing a 40-minute `/walk` across the map.
                registered = exit_for(ctx.position.x, ctx.position.y)
                if registered is None:
                    return self._escalate(f"SUPPLY_EXIT_REQUIRED:{recipe.label}")
                return self._emit_underground_exit(ctx, (recipe.item_id, -1), label=recipe.label)

            if step.area is not None and not self._at_area(ctx, step):
                if self._walk_progressed(ctx, step, key):
                    # Distance to the destination is still shrinking -> the trek is HEALTHY, no matter
                    # how long it takes. Never escalate a bot that is provably making progress.
                    return self._travel(ctx, step, recipe)
                walks = self._walk_attempts.get(key, 0) + 1
                self._walk_attempts[key] = walks
                if walks > self.max_walk_attempts:
                    return self._escalate(f"SUPPLY_AREA_UNREACHABLE:{step.area.label or step.area}")
                return self._travel(ctx, step, recipe)
            # In-world / inventory interactions cannot run while the bank interface is open
            # (`Rs2Inventory.invokeMenu` re-targets to the bank container).
            if ctx.bank_is_open:
                return TaskDirective(
                    directive_type=DirectiveType.BANK_TURNAROUND,
                    payload={"deposit_inventory": False, "withdraw": [], "close_when_done": True},
                    reason=f"Zero-Touch supply: closing bank before gathering {recipe.label}",
                )
            # `PR-20.2`: a step may ask to be left alone for a few ticks after it is dispatched
            # (Make-X production runs server-side; re-clicking every tick is pointless spam).
            hold_until = self._hold_until.get(key)
            if hold_until is not None:
                if ctx.game_tick < hold_until:
                    return TaskDirective(
                        directive_type=DirectiveType.WAIT_NEXT_TICK,
                        reason=(
                            f"Zero-Touch supply: letting the client finish {recipe.label} "
                            f"({max(0, hold_until - ctx.game_tick)} tick(s) left)"
                        ),
                    )
                self._hold_until.pop(key, None)
            directive = self._emit(ctx, recipe, idx, step)
            if directive.directive_type != DirectiveType.ESCALATE_HUMAN:
                if step.uses_up and not step.produces:
                    # `PR-20` FIELD FIX (live tick 289): a produce-less step that DESTROYS inventory
                    # (the hopper eats the grain) is NOT done because it was dispatched. Latch on the
                    # observable postcondition instead — the inventory must actually shrink.
                    self._pending_consumption[key] = tuple(
                        (iid, _inv_qty(ctx.inventory_items, iid)) for iid, _qty in step.uses_up
                    )
                    self._consume_wait[key] = 0
                    self._emitted.add(key)
                elif not step.produces:
                    # A step with no derivable postcondition (e.g. `Operate`) is dispatched EXACTLY
                    # ONCE — re-firing it would empty a hopper that is already primed — but the rest of
                    # the chain is held back for `settle_ticks` so the animation can finish.
                    self._done.add(key)
                    self._emitted.add(key)
                    settle = self._settle_for(step)
                    if settle > 0:
                        self._settle_until[key] = ctx.game_tick + settle
                if step.redispatch_hold is not None and step.redispatch_hold > 0:
                    self._hold_until[key] = ctx.game_tick + int(step.redispatch_hold)
            return directive
        return self._escalate(f"SUPPLY_CHAIN_EXHAUSTED:{recipe.label}")

    def _emit(self, ctx: TickContext, recipe: SupplyRecipe, idx: int, step: SupplyStep) -> TaskDirective:
        key = (recipe.item_id, idx)
        # Self-healing stall detector: ANY inventory progress on this step's product resets the budget,
        # so long repeat-gather steps (20x shear, 20x spin, 3x crate) never trip the stall guard.
        if step.produces:
            progress_id = step.produces[0][0]
            current = _inv_qty(ctx.inventory_items, progress_id)
            if current > self._progress.get(key, -1):
                self._progress[key] = current
                self._attempts[key] = 0
                self._alias_cursor.pop(key, None)
        attempts = self._attempts.get(key, 0) + 1
        self._attempts[key] = attempts
        # `PR-20.1`: wandering livestock (and other repeat-gather sites) get a wider, explicit budget.
        budget = self.max_step_attempts if step.max_attempts is None else int(step.max_attempts)
        if attempts > budget:
            return self._escalate(
                f"SUPPLY_STEP_STALLED:{recipe.label}:{step.kind}:{step.targets[0] if step.targets else recipe.label}"
            )

        if step.kind == SupplyStepKind.PICKUP:
            return TaskDirective(
                directive_type=DirectiveType.TAKE_GROUND_ITEM,
                payload={"name": self._pick_alias(recipe.item_id, idx, step), "action": step.action or "Take"},
                reason=f"Zero-Touch supply: taking {recipe.label} ({step.note})",
            )

        if step.kind == SupplyStepKind.OBJECT:
            payload: dict[str, Any] = {
                "name": self._pick_alias(recipe.item_id, idx, step),
                "action": step.action,
            }
            if step.object_id is not None:
                payload["id"] = int(step.object_id)
            return TaskDirective(
                directive_type=DirectiveType.INTERACT_OBJECT,
                payload=payload,
                reason=f"Zero-Touch supply: {step.action} {recipe.label} source ({step.note})",
            )

        if step.kind == SupplyStepKind.NPC:
            return TaskDirective(
                directive_type=DirectiveType.INTERACT_NPC,
                payload={"name": self._pick_alias(recipe.item_id, idx, step), "action": step.action},
                reason=f"Zero-Touch supply: {step.action} for {recipe.label} ({step.note})",
            )

        if step.kind == SupplyStepKind.WIDGET:
            # `PR-20.2`: click the Make-X product. Coordinates are live-verified (`270`/`15`); the
            # label is carried too so a client that exposes text-based clicking can use it, and so a
            # future interface renumbering degrades into a readable failure instead of a silent one.
            widget_payload: dict[str, Any] = {}
            if step.widget_group is not None:
                widget_payload["groupId"] = int(step.widget_group)
            if step.widget_child is not None:
                widget_payload["childId"] = int(step.widget_child)
            if step.targets:
                widget_payload["text"] = step.targets[0]
            # Self-heal: if the interface was closed mid-production the click does nothing. After half
            # the budget, forget the "interface is open" latch so the previous step re-opens it.
            budget = self.max_step_attempts if step.max_attempts is None else int(step.max_attempts)
            if attempts > budget // 2:
                self._done.discard((recipe.item_id, idx - 1))
            return TaskDirective(
                directive_type=DirectiveType.CLICK_WIDGET,
                payload=widget_payload,
                reason=f"Zero-Touch supply: selecting {recipe.label} in the {step.action or 'Make-X'} interface ({step.note})",
            )

        if step.kind == SupplyStepKind.COMBINE:
            assert step.partner_id is not None
            source_id = step.combine_source_id if step.combine_source_id is not None else step.consumes[0][0]
            return TaskDirective(
                directive_type=DirectiveType.USE_ITEM_ON_ITEM,
                payload={
                    "source_item_id": source_id,
                    "target_item_id": step.partner_id,
                    "source_name": recipe.label,
                },
                reason=f"Zero-Touch supply: combining {recipe.label} ({step.note})",
            )

        return self._escalate(f"SUPPLY_UNKNOWN_STEP_KIND:{step.kind}")

    def _walk_progressed(self, ctx: TickContext, step: SupplyStep, key: tuple[int, int]) -> bool:
        """True when the chebyshev distance to the step area shrank since the best tick seen so far."""
        assert step.area is not None
        if ctx.position.plane != step.area.plane:
            # A plane mismatch can only be fixed by a CLIMB step; it is never "progress".
            return False
        dist = float(ctx.position.chebyshev_distance(step.area))
        best = self._walk_best_distance.get(key)
        if best is None or dist < best - 0.5:
            self._walk_best_distance[key] = dist
            self._walk_attempts[key] = 0
            return True
        return False

    def recipe_is_underground(self, item_id: int) -> bool:
        """True when `item_id` is produced BELOW ground (`PR-20`).

        The `PR-19.4` surface guard exists so the bot never walks across the map from `y >= 5000`.
        It must NOT preempt a recipe whose whole point is to go down there (the Lumbridge cellar
        bucket) — otherwise the bot descends, is immediately marched back out, and ping-pongs until
        `SUPPLY_ENTER_FAILED`.
        """
        return self._requires_underground(item_id, depth=0)

    def _requires_underground(self, item_id: int, *, depth: int) -> bool:
        """Recursive over the ingredient tree: `Bucket of milk` needs the cellar because its
        `Bucket (1925)` does. A non-recursive check made the surface guard fight the descent forever
        (descend -> guard drags the bot out -> descend ...)."""
        if depth > MAX_RECURSION_DEPTH:
            return False
        recipe = self.recipes.get(item_id)
        if recipe is None:
            return False
        for step in recipe.steps:
            if step.kind in (SupplyStepKind.ENTER_UNDERGROUND, SupplyStepKind.EXIT_UNDERGROUND):
                return True
            if step.area is not None and classify_underground(step.area.x, step.area.y):
                return True
            for needed_id, _qty in step.consumes:
                if self._requires_underground(needed_id, depth=depth + 1):
                    return True
        return False

    def ensure_underground_exit(self, ctx: TickContext, label: str = "") -> TaskDirective:
        """Public entry point: leave whatever cave/cellar the bot is standing in, authorized."""
        return self._emit_underground_exit(ctx, (-1, -1), label=label or "surface travel")

    def _emit_underground_exit(
        self,
        ctx: TickContext,
        key: tuple[int, int],
        *,
        label: str,
        targets: tuple[str, ...] = ("Staircase", "Stairs", "Ladder"),
        action: str = "Climb-up",
        area_label: str = "",
    ) -> TaskDirective:
        """Leave a cave/cellar through its own staircase, explicitly authorized for the watchdog."""
        if ctx.bank_is_open:
            return TaskDirective(
                directive_type=DirectiveType.BANK_TURNAROUND,
                payload={"deposit_inventory": False, "withdraw": [], "close_when_done": True},
                reason=f"Zero-Touch supply: closing bank before leaving {area_label} for {label}",
            )
        registered = exit_for(ctx.position.x, ctx.position.y)
        if registered is not None:
            targets, action, area_label = registered.targets, registered.action, registered.region
        area_label = area_label or "underground"
        exits = self._walk_attempts.get(key, 0) + 1
        self._walk_attempts[key] = exits
        if exits > self.max_climb_attempts:
            return self._escalate(f"SUPPLY_EXIT_BLOCKED:{area_label}")
        name = targets[min(len(targets) - 1, (exits - 1) // 6)] if targets else "Staircase"
        return TaskDirective(
            directive_type=DirectiveType.AUTHORIZE_SURFACE_EXIT,
            payload={"name": name, "action": action},
            reason=(
                f"Zero-Touch supply: climbing out of {area_label} for {label} (authorized surface exit)"
            ),
        )

    def _destination(self, step: SupplyStep) -> str | WorldPoint:
        """Node id for surface targets (graph routing), raw point for upper floors (local micro-route)."""
        assert step.area is not None
        if step.area.plane != 0 or step.area.label not in DEFAULT_WORLD_NODES:
            return step.area
        return step.area.label

    def _travel(self, ctx: TickContext, step: SupplyStep, recipe: SupplyRecipe) -> TaskDirective:
        """Walk toward `step.area`. ALWAYS returns `NAVIGATE_TO` — never anything else.

        `PR-19.2` invariant: movement is never traded for a run toggle. Returning `SET_RUN_MODE` here
        (the `PR-19.1` behaviour) livelocked the bot whenever the client lagged in reporting
        `run_enabled=True`: it re-issued the toggle every tick instead of walking, and the progress
        guard then (correctly) declared the route unreachable. Run enabling is a SIDE-CHANNEL command
        dispatched *alongside* the walk by `ExecutionEngineKernel`, exactly like the watchdog pulse.
        """
        return TaskDirective(
            directive_type=DirectiveType.NAVIGATE_TO,
            destination=self._destination(step),
            reason=f"Zero-Touch supply: walking to {step.area.label or step.area} for {recipe.label}",
        )

    def _at_area(self, ctx: TickContext, step: SupplyStep) -> bool:
        """Is the bot inside this step's WORK SITE?

        `PR-20.1`: sites that contain wandering targets (Fred's sheep pen) declare a wider
        `area_radius`; every other step keeps the tight global `walk_tolerance`.
        """
        assert step.area is not None
        tolerance = self.walk_tolerance if step.area_radius is None else int(step.area_radius)
        return (
            ctx.position.plane == step.area.plane
            and ctx.position.chebyshev_distance(step.area) <= tolerance
        )

    def _settle_for(self, step: SupplyStep) -> int:
        """Action-grace window for one completed step (`PR-20`)."""
        if step.settle_ticks is not None:
            return max(0, int(step.settle_ticks))
        return max(0, int(self.settle_ticks))

    def _pick_alias(self, item_id: int, idx: int, step: SupplyStep) -> str:
        """Rotate through Wiki-verified display-name aliases when a step stalls on name drift."""
        if not step.targets:
            return ""
        key = (item_id, idx)
        attempts = self._attempts.get(key, 0)
        cursor = self._alias_cursor.get(key, 0)
        # Rotate one alias every 6 attempts so a single wrong spelling cannot burn the whole budget.
        if attempts and attempts % 6 == 0 and len(step.targets) > 1:
            cursor = (cursor + 1) % len(step.targets)
            self._alias_cursor[key] = cursor
        return step.targets[cursor % len(step.targets)]

    def _escalate(self, reason: str) -> TaskDirective:
        self._escalation = reason
        return TaskDirective(directive_type=DirectiveType.ESCALATE_HUMAN, reason=reason)
