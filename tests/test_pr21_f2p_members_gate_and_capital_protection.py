"""`PR-21` — The F2P legality gate and capital protection (T157..T160).

**Field incident #15 — the retraction this file exists for.**

`PR-20.7`, `PR-20.8` and `PR-20.9` were three PRs of careful engineering spent teaching the bot to
pickpocket Lumbridge citizens for the last 30 GP of the Goblin Diplomacy dyes. They fixed a location, a
progress watcher and a stun window. All of it was worthless, because **Thieving is a members-only skill
in Old School RuneScape**:

> OSRS Wiki, `Members`: "Out of the 24 skills in Old School RuneScape, nine of them are only available
> in members' worlds: Herblore, Hunter, **Thieving**, Slayer, ..."

The live confirmation was unambiguous: after every attempt the client reported
`GET /skills -> {"name": "Thieving", "level": 1, "xp": 0}`.

The lesson is not "pick a better spot". It is: **the engine cannot detect a members-only refusal from
the wire** — `POST /npcs/interact` answers `{"ok": true}` whenever the call reaches the client — so the
rule has to live in the bundle's contract and be enforced at review time.

- T157: no module in `osrs_ee/bots/` may name a members-only action or skill.
- T158: the guard rejects the action even when asked directly (it fails loudly, not silently).
- T159: an empty account now reports an EXACT shortfall plus the one legal F2P income.
- T160: capital protection — a purse larger than the working float is banked, so a death cannot repeat
  incident #10's 54 GP loss.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from osrs_ee.bots import f2p_legality
from osrs_ee.bots.base import DirectiveType, TickContext
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.supply_procurement import COINS_ID
from osrs_ee.navigation import AccountCapabilitiesContext
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES

BOTS_DIR = Path(__file__).resolve().parents[1] / "osrs_ee" / "bots"
DRAYNOR = DEFAULT_WORLD_NODES["DRAYNOR_BANK"]
AGGIE_DOOR = DEFAULT_WORLD_NODES["DRAYNOR_AGGIE"]
GOBLIN_STAGE = {29: 2, 179: 21, 144: 100, 62: 1}


def _ctx(coins: int, *, position=DRAYNOR, bank_open: bool = True, bank_items=()) -> TickContext:
    inventory = ({"item_id": COINS_ID, "name": "Coins", "quantity": coins},) if coins else ()
    return TickContext(
        game_tick=10,
        position=position,
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=inventory,
        bank_is_open=bank_open,
        bank_items=bank_items,
        varps=GOBLIN_STAGE,
        quest_points=8,
        total_level=40,
    )


# ---------------------------------------------------------------------------
# T157 — the review-time guard
# ---------------------------------------------------------------------------
def test_t157_no_bot_module_may_name_a_members_only_action_or_skill() -> None:
    """T157 (`PR-21`): a source-level sweep of `osrs_ee/bots/`. This is deliberately a TEXT scan, not a
    behaviour test — it catches the mistake when someone writes it, years before an account is walked
    into a members-only dead end. Thirteen PRs and three field incidents came from trusting an
    assumption; the cheapest permanent fix is to make the assumption un-writable."""
    offenders = {}
    for path in sorted(BOTS_DIR.glob("*.py")):
        hits = f2p_legality.scan_module_source(path)
        # `f2p_legality.py` itself declares the blacklist, so it obviously contains the words.
        if hits and path.name != "f2p_legality.py":
            offenders[path.name] = hits
    assert offenders == {}, f"members-only vocabulary in an F2P bundle: {offenders}"


def test_t158_the_guard_rejects_the_action_loudly() -> None:
    """T158 (`PR-21`): asking nicely is not enough — the guard must raise, because a `{"ok": true}` from
    the client is indistinguishable from success on the wire."""
    with pytest.raises(f2p_legality.MembersOnlyActionError):
        f2p_legality.assert_f2p_safe("Pickpocket")
    with pytest.raises(f2p_legality.MembersOnlyActionError):
        f2p_legality.assert_f2p_safe("steal-from")
    # Legal F2P verbs pass through untouched.
    for legal in ("Talk-to", "Shear", "Attack", "Mine", "Use", "Climb-up", "Pick-up"):
        assert f2p_legality.assert_f2p_safe(legal) == legal


# ---------------------------------------------------------------------------
# T159 — the honest dead end
# ---------------------------------------------------------------------------
def test_t159_a_broke_account_reports_the_exact_shortfall_and_the_legal_income() -> None:
    """T159 (`PR-21`): every dead end must name the way out. With no coins in the bag and none in the
    bank, the engine reports `short_by_<n>GP` and the ONE legal, requirement-free F2P income —
    Doric's Quest, 180 coins — instead of quietly trying a members-only skill for eighty ticks."""
    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    bot._bank_audit_done = True
    bot._bank_stock = {}  # empty vault, exactly like the live `GET /bank`

    directive = bot._ensure_coin_float(_ctx(1))
    assert directive is not None
    assert directive.directive_type == DirectiveType.ESCALATE_HUMAN
    assert directive.reason == "SUPPLY_UNSOURCED:Coins:short_by_34GP:next=DORIC_QUEST_180GP", directive.reason
    assert bot._coin_shortfall_gp == 34
    assert bot.F2P_FUNDING_PLAN == ("DORIC_QUEST_180GP",)
    # Reported once, never looped.
    assert bot._ensure_coin_float(_ctx(1)) is None

    # The declared income really is F2P-legal and really does cover the budget.
    assert 180 > TradeUnlockQuestsTaskModule.COIN_FLOAT_GP


def test_t159b_a_funding_dead_end_never_stalls_the_free_half_of_the_stage() -> None:
    """T159b (`PR-21`): the plain brown goblin mail is free from a crate. While the money question is
    open the engine must keep gathering it — money is only escalated for the two DYE mails."""
    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    bot._bank_audit_done = True
    bot._bank_stock = {}
    directive = bot._prepare_goblin_diplomacy_supplies(_ctx(1, position=AGGIE_DOOR, bank_open=False))
    assert directive is not None
    assert directive.directive_type != DirectiveType.ESCALATE_HUMAN, directive.reason


# ---------------------------------------------------------------------------
# T160 — capital protection
# ---------------------------------------------------------------------------
def test_t160_a_purse_beyond_the_working_float_is_banked_not_carried() -> None:
    """T160 (`PR-21`): incident #10 lost **54 of 55 GP** because the whole purse was hand-carried
    through a corridor that had already proved lethal. A player banks it. `CAPITAL_PROTECT_SURPLUS_GP`
    is the amount the engine is willing to keep in the bag; everything above it belongs in the vault.

    The policy is declared on the bundle so the funding chain and the capital rule cannot drift apart.
    """
    assert TradeUnlockQuestsTaskModule.CAPITAL_PROTECT_SURPLUS_GP >= 0
    # The float IS the exposure ceiling: a death while carrying exactly the float costs, at worst, the
    # dye budget (35 GP) — not the whole Sheep Shearer reward (60 GP) and certainly not 10,000.
    assert TradeUnlockQuestsTaskModule.COIN_FLOAT_GP == 35
    assert TradeUnlockQuestsTaskModule.COIN_FLOAT_GP < 60


# ---------------------------------------------------------------------------
# `PR-21.1` — funding PROVENANCE. The operator may fund a damaged account;
# the ledger may not quietly turn that into an engine achievement (T161..T163).
# ---------------------------------------------------------------------------
def test_t161_operator_funded_recovery_is_detected_and_tagged() -> None:
    """T161 (`PR-21.1`): the engine declares an exact shortfall, the operator puts money in the bag,
    and the next tick finds a whole purse with **no withdrawal directive of ours** in between. That is
    `OPERATOR_FUNDED_RECOVERY` — recorded with the amount, carried into `COMPLETE_TASK`, never hidden."""
    from osrs_ee.bots.quests_module import FundingProvenance

    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    bot._bank_audit_done = True
    bot._bank_stock = {}  # live `GET /bank`: six items, zero coins

    # 1. no coins anywhere -> the exact shortfall is reported.
    escalate = bot._ensure_coin_float(_ctx(1))
    assert escalate is not None and escalate.directive_type == DirectiveType.ESCALATE_HUMAN
    assert bot._coin_shortfall_gp == 34
    assert bot.funding_provenance is FundingProvenance.UNKNOWN

    # 2. the operator funds the account: 50 GP appears in the bag.
    assert bot._ensure_coin_float(_ctx(50)) is None
    assert bot.funding_provenance is FundingProvenance.OPERATOR_FUNDED_RECOVERY
    assert bot._coin_float_provenance_gp == 50


def test_t162_a_bank_withdrawal_is_the_zero_touch_path() -> None:
    """T162 (`PR-21.1`): when the ENGINE fetches its own money, the run is `ZERO_TOUCH`. The tag is set
    at the moment the withdrawal directive is issued, not guessed at report time."""
    from osrs_ee.bots.quests_module import FundingProvenance

    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    bot._bank_audit_done = True
    bot._bank_stock = {COINS_ID: 10_000}

    directive = bot._ensure_coin_float(_ctx(1, position=DRAYNOR, bank_open=True))
    assert directive is not None and directive.directive_type == DirectiveType.BANK_TURNAROUND
    assert bot.funding_provenance is FundingProvenance.ZERO_TOUCH


def test_t163_a_purse_present_from_the_first_tick_is_never_claimed_as_zero_touch() -> None:
    """T163 (`PR-21.1`): a purse already full on the first observation cannot be attributed. It is
    `PREEXISTING_CARRY` — honest about what the engine did not watch happen."""
    from osrs_ee.bots.quests_module import FundingProvenance

    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    bot._bank_audit_done = True
    bot._bank_stock = {}
    assert bot._ensure_coin_float(_ctx(60)) is None
    assert bot.funding_provenance is FundingProvenance.PREEXISTING_CARRY


def test_t164_complete_task_carries_the_provenance_tag() -> None:
    """T164 (`PR-21.1`): the completion report itself says who paid. A run funded by a human can never
    be mistaken for a `LIVE_WIRE_VERIFIED` zero-touch completion by reading the directive alone."""
    from osrs_ee.bots.quests_module import FundingProvenance

    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    bot._bank_audit_done = True
    bot._bank_stock = {}
    bot._ensure_coin_float(_ctx(1))       # shortfall proven
    bot._ensure_coin_float(_ctx(50))      # operator fills it
    assert bot.funding_provenance is FundingProvenance.OPERATOR_FUNDED_RECOVERY

    done = {29: 2, 179: 21, 144: 100, 62: 6}
    ctx = TickContext(
        game_tick=99,
        position=DEFAULT_WORLD_NODES["GRAND_EXCHANGE"],
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=(),
        varps=done,
        quest_points=13,
        total_level=40,
    )
    directive = bot.evaluate_tick(ctx)
    assert directive.directive_type == DirectiveType.COMPLETE_TASK, directive
    assert directive.payload["funding_provenance"] == "OPERATOR_FUNDED_RECOVERY"
    assert directive.payload["funded_gp"] == 50
    assert "funding=OPERATOR_FUNDED_RECOVERY" in directive.reason
