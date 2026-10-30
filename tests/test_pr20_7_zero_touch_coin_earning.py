"""`PR-20.7` — Zero-Touch coin EARNING (T147..T150).

**Field retraction first.** `PR-20.6` was built on the belief that `acc_02` banked the `10,000 GP`
Stronghold reward. The live probe killed that belief outright:

    GET /bank -> {"open": true, "count": 6, "items": [Shortbow, Wooden shield, Bronze axe,
                 Small fishing net, Raw shrimps, Fancy boots]}      # ZERO coins

`Fancy boots (9005)` proves the Stronghold deposit happened; the coins are simply gone. That is
**field ground truth beating our own documentation** — the same rule that demoted every wiki tile.
The engine responded correctly (`SUPPLY_UNSOURCED:Coins` at tick 2, no phantom 200-tile trek), so the
defect was never in the cascade. The defect was that the cascade had **no way to earn**.

The two field proposals were:
  (a) operator drops 50 GP in / sells the bow by hand  -> REFUSED (Zero-Touch Invariant),
  (b) the engine earns 30 GP itself                    -> implemented here.

Why pickpocketing: it is the only income that needs **no new interface at all**. `Pickpocket` is a
name-based action on `/npcs/interact`, the exact shape already `LIVE_WIRE_VERIFIED` for `Talk-to`
(Aggie, Wyson, Fred) and `Shear` (Sheep, 20 wool). No shop widget ids, no dialogue guesswork, no GE.

- T147: empty bank -> the engine EARNS instead of escalating (the live regression).
- T148: the earning tick is a proven-shape `Pickpocket`, and it stops the instant the purse is whole.
- T149: a bounded failure mode — never an infinite pickpocket loop.
- T150: the whole 0 GP -> funded -> yellow-dye hand-off, tick by tick, with zero operator money.
"""

from __future__ import annotations

from typing import Any

from osrs_ee.bots.base import DirectiveType, TickContext
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.supply_procurement import COINS_ID
from osrs_ee.navigation import AccountCapabilitiesContext
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES

DRAYNOR_BANK = DEFAULT_WORLD_NODES["DRAYNOR_BANK"]
AGGIE_DOOR = DEFAULT_WORLD_NODES["DRAYNOR_AGGIE"]
GOBLIN_STAGE = {29: 2, 179: 21, 144: 100, 62: 1}
# `PR-20.8` field census: the Lumbridge castle courtyard is where `Man`/`Woman` actually are.
COURTYARD = DEFAULT_WORLD_NODES["LUMBRIDGE_CASTLE_COURTYARD"]
# `GET /bank` on `acc_02`, verbatim: six items, no 995.
LIVE_EMPTY_BANK = {
    841: 1,
    1171: 1,
    1351: 1,
    303: 1,
    317: 1,
    9005: 1,
}


def _ctx(
    position=DRAYNOR_BANK,
    *,
    coins: int,
    tick: int = 40,
    extra: tuple[dict[str, Any], ...] = (),
) -> TickContext:
    inventory: list[dict[str, Any]] = list(extra)
    if coins:
        inventory.append({"item_id": COINS_ID, "name": "Coins", "quantity": coins})
    return TickContext(
        game_tick=tick,
        position=position,
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=tuple(inventory),
        bank_is_open=True,
        bank_items=tuple({"item_id": i, "name": str(i), "quantity": q} for i, q in LIVE_EMPTY_BANK.items()),
        varps=GOBLIN_STAGE,
        quest_points=8,
        total_level=40,
    )


def _bot() -> TradeUnlockQuestsTaskModule:
    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    bot._bank_audit_done = True
    bot._bank_stock = dict(LIVE_EMPTY_BANK)  # the live bank, verbatim: no coins
    return bot


# ---------------------------------------------------------------------------
# T147 — an empty bank must not be a dead end
# ---------------------------------------------------------------------------
def test_t147_an_empty_bank_makes_the_bot_earn_instead_of_escalating() -> None:
    """T147 (`PR-20.7`): the live regression. `acc_02` held 1 GP with 6 items banked and the engine
    escalated `SUPPLY_UNSOURCED:Coins` at tick 2. It must now start earning instead."""
    bot = _bot()
    directive = bot._ensure_coin_float(_ctx(coins=1))
    assert directive is not None
    assert directive.directive_type != DirectiveType.ESCALATE_HUMAN, directive.reason
    # `PR-20.8`: from the bank the first earning directive is the walk to the citizen hub.
    assert directive.directive_type in (DirectiveType.INTERACT_NPC, DirectiveType.NAVIGATE_TO), directive
    if directive.directive_type == DirectiveType.NAVIGATE_TO:
        assert directive.destination == TradeUnlockQuestsTaskModule.COIN_EARN_NODE, directive.destination
    else:
        assert directive.payload["action"] == "Pickpocket"
    assert bot._coin_earning is True


# ---------------------------------------------------------------------------
# T148 — proven call shape, and it stops when the purse is full
# ---------------------------------------------------------------------------
def test_t148_earning_uses_the_proven_npc_shape_and_stops_when_the_purse_is_whole() -> None:
    """T148 (`PR-20.7`): `Pickpocket` rides the same `/npcs/interact` shape as `Talk-to`/`Shear`
    (name-based, no ids), alternates between the village citizens, and the moment the purse reaches
    the 35 GP budget the bot goes back to the quest instead of pickpocketing a 36th time."""
    bot = _bot()
    bot._coin_earning = True

    # `PR-20.8`: standing IN the courtyard — a rejected pickpocket looks like an unlucky one, so the
    # strike counter must stay below the limit for these six successful-shape attempts.
    seen_actions = set()
    seen_names = set()
    for i in range(6):
        # `PR-20.9`: the stun must expire between attempts, so the clock advances a full window.
        courtyard = _ctx(COURTYARD, coins=1, tick=40 + i * TradeUnlockQuestsTaskModule.COIN_EARN_SETTLE_TICKS)
        directive = bot._earn_coins(courtyard)
        assert directive.directive_type == DirectiveType.INTERACT_NPC
        seen_actions.add(directive.payload["action"])
        seen_names.add(directive.payload["name"])

    assert seen_actions == {"Pickpocket"}, seen_actions
    assert seen_names <= set(TradeUnlockQuestsTaskModule.PICKPOCKET_TARGETS), seen_names
    assert len(seen_names) > 1, "the bot must vary targets — citizens move and get stunned"

    # Purse whole -> no further earning, and the engine is ready to spend it.
    assert bot._earn_coins(_ctx(coins=35)) is None
    assert bot._coin_earning is False
    assert bot._coin_float_resolved is True
    assert bot._coin_earn_attempts == 0


# ---------------------------------------------------------------------------
# T149 — bounded failure, never an infinite loop
# ---------------------------------------------------------------------------
def test_t149_earning_gives_up_loudly_after_a_bounded_number_of_attempts() -> None:
    """T149 (`PR-20.7`): if no citizen is reachable the engine must not pickpocket forever. At the
    cap it escalates with a reason that names the strategy and the count, and it does not re-arm."""
    bot = _bot()
    bot._coin_earning = True
    bot._coin_earn_attempts = TradeUnlockQuestsTaskModule.COIN_EARN_ATTEMPT_CAP

    directive = bot._earn_coins(_ctx(coins=1))
    assert directive is not None
    assert directive.directive_type == DirectiveType.ESCALATE_HUMAN
    assert directive.reason.startswith("SUPPLY_UNSOURCED:Coins:pickpocket_exhausted"), directive.reason
    assert bot._coin_float_unavailable is True

    # Disarmed: the bot stops asking (the dye step then escalates with the precise `Coins` label).
    assert bot._ensure_coin_float(_ctx(coins=1)) is None


# ---------------------------------------------------------------------------
# T150 — the whole self-funded run, tick by tick
# ---------------------------------------------------------------------------
def test_t150_zero_gp_to_a_funded_purse_with_no_operator_involvement() -> None:
    """T150 (`PR-20.7`): simulate the live account exactly — 6 banked items, no coins, 1 GP in the
    bag — and let the engine fund itself. Three GP per successful pickpocket; the run must stop at the
    35 GP budget, then hand straight over to the yellow-dye step. Zero operator GP, zero shop."""
    bot = _bot()
    coins = 1
    ticks = 0
    pickpockets = 0
    position = DRAYNOR_BANK  # the live starting point: inside the Draynor bank
    tick = 40
    while coins < TradeUnlockQuestsTaskModule.COIN_FLOAT_GP and ticks < 400:
        ticks += 1
        tick += 1
        directive = bot._ensure_coin_float(_ctx(position, coins=coins, tick=tick))
        if directive is None:
            break
        if directive.directive_type == DirectiveType.INTERACT_NPC:
            pickpockets += 1
            coins += 3  # a successful pickpocket from a citizen
        elif directive.directive_type == DirectiveType.NAVIGATE_TO:
            assert directive.destination == TradeUnlockQuestsTaskModule.COIN_EARN_NODE, directive.destination
            position = DEFAULT_WORLD_NODES[directive.destination]  # one tick to arrive
        elif directive.directive_type == DirectiveType.WAIT_NEXT_TICK:
            pass  # `PR-20.9`: the stun window; legitimate and free of strikes
        else:  # pragma: no cover - any other directive is a defect in this scenario
            raise AssertionError(f"unexpected directive {directive.directive_type}: {directive.reason}")

    assert coins >= TradeUnlockQuestsTaskModule.COIN_FLOAT_GP, f"only reached {coins} GP"
    assert pickpockets == 12, f"1 + 12*3 = 37 GP boundary, got {pickpockets}"
    # `PR-20.9`: twelve attempts need eleven full stun windows between them (the first needs no wait).
    settle = TradeUnlockQuestsTaskModule.COIN_EARN_SETTLE_TICKS
    assert ticks >= (pickpockets - 1) * settle, f"{ticks} ticks for {pickpockets} attempts"
    # ...and the pacing is real: ~3s of wall clock per attempt, not one per tick.
    assert ticks > pickpockets * 3, ticks
    # The purse exists ONLY because the bot earned it: nothing was withdrawn.
    assert bot._bank_stock.get(COINS_ID, 0) == 0
    # ...and it is now spendable on the dye budget (5 yellow + 20 woad + 5 blue = 30).
    assert coins - 30 >= 0


# ---------------------------------------------------------------------------
# T151 — anti-false-positive: never earn money that is not needed
# ---------------------------------------------------------------------------
def test_t151_a_bot_whose_dyes_are_already_banked_never_pickpockets() -> None:
    """T151 (`PR-20.7`): coins exist only to pay for DYES (Wyson 20 GP, Aggie 5 GP). If the finished
    `Orange dye` and `Blue dye` are already in the bag or the bank, the bot must go straight to the
    combine — a pickpocket loop there would be pure waste. This is the `T68` wire case, guarded at the
    engine level rather than only in that test."""
    from osrs_ee.bots.supply_procurement import BLUE_DYE_ID, ORANGE_DYE_ID

    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    bot._bank_audit_done = True
    bot._bank_stock = {ORANGE_DYE_ID: 1, BLUE_DYE_ID: 1, 288: 3}  # dyes banked, no coins

    ctx = TickContext(
        game_tick=41,
        position=DRAYNOR_BANK,
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=(),
        bank_is_open=True,
        varps=GOBLIN_STAGE,
        quest_points=8,
        total_level=40,
    )
    directive = bot._prepare_goblin_diplomacy_supplies(ctx)
    assert directive is not None
    assert directive.directive_type != DirectiveType.INTERACT_NPC, directive
    assert bot._coin_earning is False, "the bot must not start earning for dyes it already owns"

    # ...whereas a missing BLUE dye does demand money (woad leaves are not free).
    bot2 = TradeUnlockQuestsTaskModule(target_quest_points=12)
    bot2._bank_audit_done = True
    bot2._bank_stock = {ORANGE_DYE_ID: 1, 288: 3}
    directive2 = bot2._prepare_goblin_diplomacy_supplies(ctx)
    assert directive2 is not None
    assert directive2.directive_type in (DirectiveType.INTERACT_NPC, DirectiveType.NAVIGATE_TO), directive2
    if directive2.directive_type == DirectiveType.INTERACT_NPC:
        assert directive2.payload["action"] == "Pickpocket"


# ---------------------------------------------------------------------------
# `PR-20.8` — the live Draynor failure, and the blindness behind it (T152..T154)
# ---------------------------------------------------------------------------
def test_t152_earning_happens_in_the_field_confirmed_citizen_hub() -> None:
    """T152 (`PR-20.8`): the live `GET /npcs?maxDistance=30` census showed Draynor holds NO `Man` or
    `Woman`, so `PR-20.7` fired 80 doomed pickpockets. The earning hubs must now be the courtyard and
    Varrock square — both real graph nodes the router can already reach."""
    node_id = TradeUnlockQuestsTaskModule.COIN_EARN_NODE
    assert node_id == "LUMBRIDGE_CASTLE_COURTYARD", node_id
    assert node_id != "DRAYNOR_BANK", "the live census proved Draynor has no citizens to rob"
    # `PR-20.9`: the Varrock fallback is GONE — it walked a level-3 account through the wizard corridor.
    assert not hasattr(TradeUnlockQuestsTaskModule, "COIN_EARN_NODES"), "single field-confirmed hub"
    assert node_id in DEFAULT_WORLD_NODES, f"{node_id} must be routable"
    assert DEFAULT_WORLD_NODES[node_id].plane == 0

    # From the bank the bot walks to the courtyard (it does not pickpocket thin air in Draynor).
    bot = _bot()
    bot._coin_earning = True
    directive = bot._earn_coins(_ctx(DRAYNOR_BANK, coins=1))
    assert directive.directive_type == DirectiveType.NAVIGATE_TO
    assert directive.destination == "LUMBRIDGE_CASTLE_COURTYARD"


def test_t153_attempts_are_paced_by_the_stun_window_and_a_wait_is_not_a_strike() -> None:
    """T153 (`PR-20.9`): incident #14 in one assertion. A failed pickpocket stuns for ~3s (5 ticks),
    and the game refuses every attempt inside that window. `PR-20.8` fired one attempt PER TICK, so a
    single unlucky roll became six "silent strikes" in 3.6 seconds and a 250-tile march to Varrock."""
    settle = TradeUnlockQuestsTaskModule.COIN_EARN_SETTLE_TICKS
    assert settle >= 5, "a stun is ~3.0s; 5 game ticks at minimum"

    bot = _bot()
    bot._coin_earning = True
    t0 = 100
    first = bot._earn_coins(_ctx(COURTYARD, coins=1, tick=t0))
    assert first.directive_type == DirectiveType.INTERACT_NPC
    assert bot._coin_no_progress == 1

    # Every tick inside the stun must HOLD, not pickpocket, and must not add a strike.
    for offset in range(1, settle):
        held = bot._earn_coins(_ctx(COURTYARD, coins=1, tick=t0 + offset))
        assert held.directive_type == DirectiveType.WAIT_NEXT_TICK, (offset, held)
        assert bot._coin_no_progress == 1, "a waiting tick is not a silent attempt"
        assert bot._coin_earn_attempts == 1

    # The window expires -> exactly one new attempt is allowed.
    nxt = bot._earn_coins(_ctx(COURTYARD, coins=1, tick=t0 + settle))
    assert nxt.directive_type == DirectiveType.INTERACT_NPC
    assert bot._coin_earn_attempts == 2

    # Six strikes therefore span six full windows — never six ticks.
    assert 6 * settle > 6, "the whole incident was that these were the same number"


def test_t154_a_barren_hub_is_reported_and_never_answered_with_a_trek_to_varrock() -> None:
    """T154 (`PR-20.9`): with the single field-confirmed hub silent, the engine must STOP and name it.
    The `PR-20.8` fallback sent a level-3 account to Varrock square — through the dark wizards — to
    avoid admitting defeat."""
    bot = _bot()
    bot._coin_earning = True
    bot._coin_no_progress = TradeUnlockQuestsTaskModule.COIN_EARN_NO_PROGRESS_LIMIT
    bot._coin_earn_last_tick = None

    directive = bot._earn_coins(_ctx(COURTYARD, coins=1))
    assert directive is not None
    assert directive.directive_type == DirectiveType.ESCALATE_HUMAN, directive
    assert directive.reason == "SUPPLY_UNSOURCED:Coins:no_progress(LUMBRIDGE_CASTLE_COURTYARD)", directive.reason
    assert bot._coin_float_unavailable is True

    # Disarmed: the bot stops instead of earning forever or walking anywhere dangerous.
    assert bot._ensure_coin_float(_ctx(COURTYARD, coins=1)) is None


def test_t156_a_danger_tick_never_starts_a_new_pickpocket() -> None:
    """T156 (`PR-20.9`): the live log showed `state=IN_DANGER` for five consecutive ticks while the
    engine kept issuing pickpockets. The safety layer owns a danger tick; the earning loop must hold
    without burning an attempt or a strike."""
    bot = _bot()
    bot._coin_earning = True

    dangerous = TickContext(
        game_tick=200,
        position=COURTYARD,
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 6}, combat_level=3),
        inventory_items=({"item_id": COINS_ID, "name": "Coins", "quantity": 1},),
        varps=GOBLIN_STAGE,
        quest_points=8,
        total_level=40,
        in_danger=True,
    )
    directive = bot._earn_coins(dangerous)
    assert directive is not None
    assert directive.directive_type == DirectiveType.WAIT_NEXT_TICK, directive
    assert bot._coin_earn_attempts == 0
    assert bot._coin_no_progress == 0
    assert "holding" in directive.reason

    # Once safe, the very next tick resumes normally.
    safe = bot._earn_coins(_ctx(COURTYARD, coins=1, tick=201))
    assert safe.directive_type == DirectiveType.INTERACT_NPC


def test_t155_progress_resets_the_strike_counter() -> None:
    """T155 (`PR-20.8`): five misses followed by one success is bad luck, not a wrong target — the
    counter must reset on ANY new GP, or a slow-but-working hub would be abandoned."""
    bot = _bot()
    bot._coin_earning = True
    settle = TradeUnlockQuestsTaskModule.COIN_EARN_SETTLE_TICKS
    for i in range(TradeUnlockQuestsTaskModule.COIN_EARN_NO_PROGRESS_LIMIT - 1):
        bot._earn_coins(_ctx(COURTYARD, coins=1, tick=300 + i * settle))
    assert bot._coin_no_progress == 5

    bot._earn_coins(_ctx(COURTYARD, coins=4, tick=300 + 5 * settle))  # one citizen paid out
    assert bot._coin_best_gp == 4
    # The five strikes are gone; only the fresh attempt issued on this tick counts.
    assert bot._coin_no_progress == 1, "a successful pickpocket must clear the strikes"
