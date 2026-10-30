"""`PR-20.6` — Autonomous re-funding after the death tax (T143..T146).

Live situation after incident #10: the account was killed by a Highwayman and resurrected in Lumbridge
with **1 GP** of the 55 it had carried (OSRS keeps only the three most valuable items). The dye budget
still outstanding was `5 + 20 + 5 = 30 GP`.

Two defects made this far worse than it needed to be:

1. **`_coin_float_resolved` was a one-way latch.** Once the bot had been funded, dying could never
   trigger a top-up again — the flag said "handled" forever.
2. **Funding only knew one bank.** The bot stood **13 tiles** from `DRAYNOR_BANK` and the engine
   insisted on the Grand Exchange anchor **200+ tiles away**, through the corridor that had just
   killed the account.

Nothing here needs operator money, and nothing needs shop selling: **the account already banks its
own 10,000 GP from the Stage 2 Stronghold run.** The 55 GP was pocket money.

- T143: the nearest bank wins — Draynor when in Draynor, never a blind GE trek.
- T144: the shortfall is withdrawn from the bot's OWN bank, exactly the missing amount.
- T145: the death tax is survivable — a funded bot that dies asks the bank again (the latch is gone).
- T146: anti-false-positive — an account with no coins anywhere escalates precisely, it never loops.
"""

from __future__ import annotations

from typing import Any

from osrs_ee.bots.base import DirectiveType, TickContext
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.supply_procurement import COINS_ID
from osrs_ee.navigation import AccountCapabilitiesContext
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint

DRAYNOR = DEFAULT_WORLD_NODES["DRAYNOR_BANK"]
AGGIE_DOOR = DEFAULT_WORLD_NODES["DRAYNOR_AGGIE"]
ONION_GARDEN = DEFAULT_WORLD_NODES["FRED_ONION_GARDEN"]
GE = DEFAULT_WORLD_NODES["GRAND_EXCHANGE"]

GOBLIN_STAGE = {29: 2, 179: 21, 144: 100, 62: 1}


def _ctx(
    pos: WorldPoint,
    *,
    coins: int,
    varps: dict[int, int] | None = None,
    extra: tuple[dict[str, Any], ...] = (),
    bank_open: bool = False,
) -> TickContext:
    inventory: list[dict[str, Any]] = list(extra)
    if coins:
        inventory.append({"item_id": COINS_ID, "name": "Coins", "quantity": coins})
    return TickContext(
        game_tick=30,
        position=pos,
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=tuple(inventory),
        bank_is_open=bank_open,
        varps=varps or GOBLIN_STAGE,
        quest_points=8,
        total_level=40,
    )


def _bot(*, bank_stock: dict[int, int] | None = None) -> TradeUnlockQuestsTaskModule:
    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    bot._bank_audit_done = True
    bot._bank_stock = dict(bank_stock if bank_stock is not None else {COINS_ID: 10_000})
    return bot


# ---------------------------------------------------------------------------
# T143 — nearest bank, not the anchor
# ---------------------------------------------------------------------------
def test_t143_funding_uses_the_nearest_bank_instead_of_a_two_hundred_tile_trek() -> None:
    """T143 (`PR-20.6`): with 1 GP in the bag at Draynor, the engine must route to `DRAYNOR_BANK`
    (13 tiles) — never to the Grand Exchange (200+ tiles, through the wizard corridor)."""
    for position in (AGGIE_DOOR, ONION_GARDEN, WorldPoint(3092, 3245, 0)):
        directive = _bot().evaluate_tick(_ctx(position, coins=1))
        if directive.directive_type == DirectiveType.BANK_TURNAROUND:
            continue  # already standing in the bank
        assert directive.directive_type == DirectiveType.NAVIGATE_TO, directive
        assert directive.destination == "DRAYNOR_BANK", (
            f"from ({position.x},{position.y}) the engine chose {directive.destination!r} "
            f"instead of the bank under its feet"
        )

    # In the far north-west the nearest verified bank is Falador West, not the GE.
    falador = _bot().evaluate_tick(_ctx(WorldPoint(2960, 3370, 0), coins=1))
    assert falador.directive_type == DirectiveType.NAVIGATE_TO
    assert falador.destination == "FALADOR_WEST_BANK", falador.destination

    # ...and the anchor bank logic is untouched for the mission latch itself.
    assert DRAYNOR.chebyshev_distance(GE) > 200


# ---------------------------------------------------------------------------
# T144 — the withdrawal is the bot's own money
# ---------------------------------------------------------------------------
def test_t144_the_shortfall_is_withdrawn_from_the_bots_own_bank_account() -> None:
    """T144 (`PR-20.6`): standing at the Draynor bank with 1 GP, the turnaround withdraws exactly the
    missing amount (34 GP for a 35 GP budget) from the account's own stronghold earnings."""
    bot = _bot()
    at_bank = _ctx(DRAYNOR, coins=1, bank_open=True)
    directive = bot.evaluate_tick(at_bank)
    assert directive.directive_type == DirectiveType.BANK_TURNAROUND, directive
    withdraw = list(directive.payload.get("withdraw") or [])
    assert withdraw and int(withdraw[0]["item_id"]) == COINS_ID
    assert int(withdraw[0]["quantity"]) == TradeUnlockQuestsTaskModule.COIN_FLOAT_GP - 1 == 34, withdraw
    assert "Coins" in (directive.reason or "")

    # Once the purse covers the budget the engine goes back to the quest, not to the bank.
    funded = _bot().evaluate_tick(_ctx(AGGIE_DOOR, coins=35))
    assert funded.directive_type != DirectiveType.BANK_TURNAROUND, funded


# ---------------------------------------------------------------------------
# T145 — the death tax is survivable
# ---------------------------------------------------------------------------
def test_t145_a_funded_bot_that_dies_asks_the_bank_again() -> None:
    """T145 (`PR-20.6`): the regression that cost the live run. `_coin_float_resolved` used to be a
    one-way latch, so a bot that was funded once and then killed (keeping 1 GP of 55) never asked for
    money again — it just escalated at Aggie's door."""
    bot = _bot()
    # 1. funded: 55 GP in the bag (Fred's reward) -> nothing to do.
    assert bot._ensure_coin_float(_ctx(AGGIE_DOOR, coins=55)) is None
    assert bot._coin_float_resolved is True

    # 2. the bot dies and respawns with 1 GP -> the engine must ask again, immediately.
    directive = bot._ensure_coin_float(_ctx(AGGIE_DOOR, coins=1))
    assert directive is not None, "a dead-and-respawned bot must be able to re-fund itself"
    assert directive.directive_type in (DirectiveType.NAVIGATE_TO, DirectiveType.BANK_TURNAROUND), directive
    assert bot._coin_float_resolved is False

    # 3. after topping up it settles down again (no oscillation).
    assert bot._ensure_coin_float(_ctx(AGGIE_DOOR, coins=35)) is None
    assert bot._coin_float_resolved is True


# ---------------------------------------------------------------------------
# T146 — anti-false-positive
# ---------------------------------------------------------------------------
def test_t146_an_account_with_no_coins_anywhere_never_ping_pongs_to_the_bank() -> None:
    """T146 (`PR-20.6`, revised twice by field evidence).

    The invariant defended here never changed: **no bank ping-pong**. `PR-20.6` stopped at "give up and
    escalate"; `PR-20.7`..`PR-20.9` wrongly answered with *pickpocketing*, which field incident #15
    proved impossible on a free-to-play account (Thieving is members-only). `PR-21` answers with the
    truth: an exact shortfall, and the one legal F2P income named in the plan.
    """
    bot = _bot(bank_stock={})  # empty bank: nothing to withdraw
    directive = bot._ensure_coin_float(_ctx(AGGIE_DOOR, coins=1))
    assert directive is not None
    assert directive.directive_type != DirectiveType.BANK_TURNAROUND, directive
    assert directive.directive_type == DirectiveType.ESCALATE_HUMAN, directive
    assert directive.reason == "SUPPLY_UNSOURCED:Coins:short_by_34GP:next=DORIC_QUEST_180GP", directive.reason
    assert bot._coin_shortfall_gp == 34
    # It reports ONCE — never a silent loop, never a members-only action.
    assert bot._ensure_coin_float(_ctx(AGGIE_DOOR, coins=1)) is None


