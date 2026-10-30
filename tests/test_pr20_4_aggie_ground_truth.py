"""`PR-20.4` — Aggie: house tile, live dialogue text, and the coin float (T137..T139).

Live evidence from the `PR-20.3` field run (`acc_02` @ 8081):

    Aggie = NPC `120` at (3088, 3259)   doorstep (3086, 3258)
    `DRAYNOR_AGGIE` was (3087, 3232) — **26 tiles south, at the fishing pier**
    On-screen dye menu opens with: "What could you make for me?"
    (the code's "Can you make dyes for me please?" never appears in the client)
    Red dye (1763) was crafted live: 3 Redberries + 5 GP.

Two failure classes, one release:

1. **A tile that is not the world's** (the #5 / #7 / #8 family) — but this time the bot did not
   crash, it *talked to empty air* at the water's edge and burned the dye attempt budget.
2. **A dialogue string that is not the client's** — the module only selects options it can see, so a
   wrong string means it silently escalates with `UNRECOGNIZED_QUEST_DIALOGUE_OPTION`.

- T137: Aggie's house tile is inside her documented area, the NPC is 2 tiles from it, and the
        Draynor bank edge is honest.
- T138: the live verbatim option is selected for all three dyes; the legacy string is still accepted
        as an alias (because the module matches what is on screen) but it is no longer the opener.
- T139: the coin float is demand-driven — it is only withdrawn at the Goblin stage, only for the
        exact missing amount, and never for a bot that already holds enough GP.
"""

from __future__ import annotations

from typing import Any

from osrs_ee.bots.base import DirectiveType, TaskDirective, TickContext
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.supply_procurement import (
    AGGIE_NPC_ID,
    BLUE_DYE_ID,
    COINS_ID,
    RED_DYE_ID,
    SUPPLY_RECIPES,
    YELLOW_DYE_ID,
    ZeroTouchSupplyEngine,
)
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint

AGGIE = DEFAULT_WORLD_NODES["DRAYNOR_AGGIE"]
AGGIE_NPC_POS = (3088, 3259)
OLD_WRONG_TILE = (3087, 3232)

LIVE_OPENER = "What could you make for me?"


def _ctx(
    pos: WorldPoint = None,  # type: ignore[assignment]
    *,
    inventory: tuple[dict[str, Any], ...] = (),
    options: tuple[str, ...] = (),
    bank_open: bool = False,
    quest_points: int = 8,
    varps: dict[int, int] | None = None,
) -> TickContext:
    return TickContext(
        game_tick=11,
        position=pos or AGGIE,
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=inventory,
        dialogue_open=bool(options),
        dialogue_options=options,
        bank_is_open=bank_open,
        varps=varps or {29: 2, 179: 21, 144: 100, 62: 0},
        quest_points=quest_points,
        total_level=40,
    )


# ---------------------------------------------------------------------------
# T137 — the house is where the probe says it is
# ---------------------------------------------------------------------------
def test_t137_aggie_node_matches_the_live_npc_and_the_edge_is_honest() -> None:
    """T137 (`PR-20.4`): `(3087,3232)` was the fishing pier. Aggie is NPC `120` at `(3088,3259)`, and
    the doorstep tile is `(3086,3258)` — two tiles away, comfortably inside the 3-tile tolerance."""
    assert AGGIE_NPC_ID == 120
    assert (AGGIE.x, AGGIE.y, AGGIE.plane) == (3086, 3258, 0)
    assert (AGGIE.x, AGGIE.y) != OLD_WRONG_TILE
    distance = max(abs(AGGIE_NPC_POS[0] - AGGIE.x), abs(AGGIE_NPC_POS[1] - AGGIE.y))
    assert distance <= 3, f"the work site is {distance} tiles from Aggie herself"
    assert 3070 <= AGGIE.x <= 3105 and 3245 <= AGGIE.y <= 3275, "outside the documented Draynor area"

    # Standing on the node must talk to Aggie, not walk somewhere.
    engine = ZeroTouchSupplyEngine()
    inventory = (
        {"item_id": 1951, "name": "Redberries", "quantity": 3},   # REDBERRY_ID
        {"item_id": COINS_ID, "name": "Coins", "quantity": 55},
    )
    directive = engine.ensure(_ctx(inventory=inventory), RED_DYE_ID, 1)
    assert directive is not None and directive.directive_type == DirectiveType.INTERACT_NPC, directive
    assert directive.payload["name"] == "Aggie"

    # The Draynor bank edge reflects the real tiles (chebyshev 13), not 16 from the wrong tile.
    bank = DEFAULT_WORLD_NODES["DRAYNOR_BANK"]
    real = max(abs(bank.x - AGGIE.x), abs(bank.y - AGGIE.y))
    assert real == 13, real
    graph = ConstrainedWaypointGraph()
    edges = [
        e for e in graph.edges
        if {e.src, e.dst} == {"DRAYNOR_BANK", "DRAYNOR_AGGIE"}
    ]
    assert edges and edges[0].cost_tiles <= 16.0, edges


# ---------------------------------------------------------------------------
# T138 — the dialogue text the client actually shows
# ---------------------------------------------------------------------------
def test_t138_the_dye_menu_opens_with_the_live_verbatim_option() -> None:
    """T138 (`PR-20.4`): every dye selects the on-screen opener. Matching is text-based, so the old
    string survives as an alias — but it is no longer the first option the engine looks for."""
    for dye_id in (BLUE_DYE_ID, RED_DYE_ID, YELLOW_DYE_ID):
        options = SUPPLY_RECIPES[dye_id].dialogue_options
        assert options[0] == LIVE_OPENER, (dye_id, options)
        assert "Can you make dyes for me please?" in options, "alias must be kept, not deleted"

    bot = TradeUnlockQuestsTaskModule()
    bot._bank_audit_done = True
    bot._coin_float_resolved = True
    # The engine must be mid-dye for its supply dialogue to take priority.
    bot.supply._active_item_id = RED_DYE_ID

    directive: TaskDirective = bot.evaluate_tick(_ctx(options=("Some flavour text.", LIVE_OPENER, "Yes.")))
    assert directive.directive_type == DirectiveType.SELECT_DIALOGUE, directive
    assert directive.payload["option"] == LIVE_OPENER

    # The legacy string still resolves (a client rebuild may show either).
    directive = bot.evaluate_tick(_ctx(options=("Can you make dyes for me please?",)))
    assert directive.directive_type == DirectiveType.SELECT_DIALOGUE
    assert directive.payload["option"] == "Can you make dyes for me please?"

    # An option nobody verified still escalates instead of guessing an index.
    directive = bot.evaluate_tick(_ctx(options=("A menu we have never seen.",)))
    assert directive.directive_type == DirectiveType.ESCALATE_HUMAN
    assert directive.reason == "UNRECOGNIZED_QUEST_DIALOGUE_OPTION"


# ---------------------------------------------------------------------------
# T139 — the coin float is demand-driven
# ---------------------------------------------------------------------------
def test_t139_coin_float_is_demand_driven_and_only_requested_at_the_goblin_stage() -> None:
    """T139 (`PR-20.4`): a 512 GP lump sent the bot trekking to the bank before it had done any quest
    work. The float is now the exact dye budget (`20 + 3x5 = 35 GP`), requested only when the goblin
    stage starts, and only for what is missing."""
    assert TradeUnlockQuestsTaskModule.COIN_FLOAT_GP == 35

    # (a) Cook's Assistant stage: NO bank trek, even with an empty purse.
    fresh = TradeUnlockQuestsTaskModule(target_quest_points=12)
    fresh._bank_audit_done = True
    fresh._bank_stock = {COINS_ID: 10_000}
    directive = fresh.evaluate_tick(
        _ctx(inventory=(), varps={29: 0, 179: 21, 144: 100, 62: 6})
    )
    assert directive.directive_type != DirectiveType.BANK_TURNAROUND, directive
    assert not fresh._coin_float_resolved

    # (b) Goblin stage with an empty purse: withdraw exactly the budget.
    at_bank = TickContext(
        game_tick=12,
        position=WorldPoint(3164, 3487, 0),
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=(),
        bank_is_open=True,
        varps={29: 2, 179: 21, 144: 100, 62: 0},
        quest_points=8,
        total_level=40,
    )
    goblin = TradeUnlockQuestsTaskModule(target_quest_points=12)
    goblin._bank_audit_done = True
    goblin._bank_stock = {COINS_ID: 10_000}
    directive = goblin.evaluate_tick(at_bank)
    assert directive.directive_type == DirectiveType.BANK_TURNAROUND, directive
    withdraw = list(directive.payload.get("withdraw") or [])
    assert withdraw and int(withdraw[0]["item_id"]) == COINS_ID
    assert int(withdraw[0]["quantity"]) == 35, withdraw

    # (c) A bot that already earned Fred's 60 GP asks for nothing.
    rich = TradeUnlockQuestsTaskModule(target_quest_points=12)
    rich._bank_audit_done = True
    rich._bank_stock = {COINS_ID: 10_000}
    rich_ctx = TickContext(
        game_tick=13,
        position=WorldPoint(3164, 3487, 0),
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=({"item_id": COINS_ID, "name": "Coins", "quantity": 55},),
        bank_is_open=True,
        varps={29: 2, 179: 21, 144: 100, 62: 0},
        quest_points=8,
        total_level=40,
    )
    directive = rich.evaluate_tick(rich_ctx)
    assert not (
        directive.directive_type == DirectiveType.BANK_TURNAROUND
        and list(directive.payload.get("withdraw") or [])
    ), directive
    assert rich._coin_float_resolved
