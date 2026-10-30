"""Stage 1A Non-Combat F2P Trade-Unlock Quests Module (`osrs_ee/bots/quests_module.py` — PR-13).

Field-Verified 12-QP Bundle (`TradeUnlockScript.java` + `جدول_الخطوات_وبروتوكول_العمل_المشترك.md`):
1. *Cook's Assistant* (`1 QP`, Varp `29`, terminal `>= 2`):
   - Supplies: `Egg` (`1944`), `Bucket of milk` (`1927`), `Pot of flour` (`1933`)
   - Objective: `Cook` at `LUMBRIDGE_KITCHEN` `(3206, 3214, 0)`
2. *Sheep Shearer* (`1 QP`, Varp `179`, terminal `>= 21`):
   - Supplies: `20x Ball of wool` (`1759` unnoted)
   - Objective: `Fred the Farmer` at `LUMBRIDGE_SHEEP_FARM` `(3190, 3273, 0)`
3. *Romeo & Juliet* (`5 QP`, Varp `144`, terminal `>= 100`):
   - Supplies: `Cadava berries` (`753`)
   - Varp-driven multi-floor stages across `Romeo` `(3211, 3422, 0)`, `Juliet` `(3158, 3427, 1)`
     via stairs `(3157, 3435, 0)` / `(3156, 3435, 1)`, `Father Lawrence` `(3254, 3483, 0)`,
     and `Apothecary` `(3195, 3405, 0)`.
4. *Goblin Diplomacy* (`5 QP`, Varp `62`, terminal `>= 6`):
   - Supplies: `Orange goblin mail` (`286`), `Blue goblin mail` (`287`), `Goblin mail` (`288`)
   - Objective: `General Bentnoze` at `GOBLIN_VILLAGE` `(2958, 3512, 0)`

Total Yield: `1 + 1 + 5 + 5 = 12 QP` (exceeding OSRS's `10 QP` Grand Exchange trade-unlock gate!).
Option selection is strictly text-verified (never 1-based numeric index guessing).
"""

from __future__ import annotations

from typing import Any, Mapping

from osrs_ee.bots.base import DirectiveType, TaskDirective, TaskModule, TickContext
from osrs_ee.bots.supply_procurement import (
    BALL_OF_WOOL_ID,
    BLUE_DYE_ID,
    BLUE_GOBLIN_MAIL_ID,
    BUCKET_OF_MILK_ID,
    CADAVA_BERRIES_ID,
    COINS_ID,
    EGG_ID,
    GOBLIN_MAIL_ID,
    ORANGE_DYE_ID,
    ORANGE_GOBLIN_MAIL_ID,
    POT_OF_FLOUR_ID,
    RED_DYE_ID,
    YELLOW_DYE_ID,
    ZeroTouchSupplyEngine,
)
from osrs_ee.navigation.underground import classify_underground
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint

VARP_COOKS_ASSISTANT = 29
VARP_SHEEP_SHEARER = 179
VARP_ROMEO_AND_JULIET = 144
VARP_GOBLIN_DIPLOMACY = 62

# Exact verified dialogue option texts across all 4 quests (priority-ordered against OSRS Wiki Transcripts)
VERIFIED_QUEST_DIALOGUE_OPTIONS: tuple[str, ...] = (
    # Cook's Assistant
    "What's wrong?",
    "I'm always happy to help a cook in distress.",
    "Actually, I know where to find this stuff.",
    "I've got all the information I need. Thanks.",
    # Sheep Shearer
    "I'm looking for a quest.",
    "Yes, okay. I can do that.",
    "I'm back!",
    # Romeo & Juliet
    "Perhaps I could help to find her for you?",
    "Yes, I have seen her actually!",
    "Yes, ok, I'll let her know.",
    "Yes, okay. I'll let her know.",
    "Talk about something else.",
    "Talk about Romeo & Juliet.",
    "Ok, thanks.",
    # Goblin Diplomacy
    # `PR-20.4` LIVE VERBATIM (Aggie's dye menu, confirmed on screen during the field run):
    "What could you make for me?",
    "Do you want me to pick an armour colour for you?",
    "What about a different colour?",
    "Yes, he looks fat.",
    "I have some orange armour here.",
    "I have some blue armour here.",
    "I have some brown armour here.",
    "Okay, I'll be back soon.",
    # Generic quest confirmation
    "Yes.",
    "Yes",
)


def _inv_count(items: tuple[Mapping[str, Any], ...], *, item_id: int, noted: bool = False) -> int:
    return sum(
        int(it.get("quantity", 0))
        for it in items
        if int(it["item_id"] if "item_id" in it else it.get("id", -1)) == item_id
        and bool(it.get("noted", False)) == noted
    )


class TradeUnlockQuestsTaskModule(TaskModule):
    """Pure tick-driven 12-QP non-combat quest chain (`stage1a_f2p_quests_10qp`)."""

    def __init__(self, *, target_quest_points: int = 10, anchor_bank_node: str = "GRAND_EXCHANGE") -> None:
        self.target_quest_points = max(1, int(target_quest_points))
        self.anchor_bank_node = anchor_bank_node
        self.supply = ZeroTouchSupplyEngine()
        self._bank_audit_done = False
        self._bank_stock: dict[int, int] = {}
        self._bank_source: str = "UNKNOWN"
        # `PR-20` Zero-Touch coin float: the goblin dyes are NOT free (Wyson 20 GP, Aggie 5 GP each),
        # so the bot withdraws ONE float from its own banked earnings while it is still parked at the
        # anchor bank. The operator never supplies GP — this is the bot's own Stage-2 income.
        self._coin_float_resolved = False
        self._coin_float_unavailable = False
        # `PR-20.7`: in-world coin EARNING (pickpocketing) once every bank is proven empty.
        self._coin_earning = False
        self._coin_earn_attempts = 0
        # Baseline QP captured on the first tick: the QP latch must fire on *progress made by this
        # run*, never on QP the account already carried from earlier stages.
        self._baseline_quest_points: int | None = None
        self._descend_attempts = 0

    @property
    def module_id(self) -> str:
        return "stage1a_f2p_quests_10qp"

    @property
    def watched_varps(self) -> tuple[int, ...]:
        return (VARP_COOKS_ASSISTANT, VARP_SHEEP_SHEARER, VARP_ROMEO_AND_JULIET, VARP_GOBLIN_DIPLOMACY)

    def _handle_quest_dialogue(self, ctx: TickContext) -> TaskDirective:
        if ctx.dialogue_options:
            # Zero-Touch supply dialogues (Wyson / Aggie) take priority while a supply chain is active,
            # and are resolved strictly against Wiki-verbatim option texts for the ACTIVE item only.
            for expected in self.supply.active_dialogue_options:
                for opt in ctx.dialogue_options:
                    if opt.strip().casefold().rstrip(".!?") == expected.casefold().rstrip(".!?"):
                        return TaskDirective(
                            directive_type=DirectiveType.SELECT_DIALOGUE,
                            payload={"option": opt},
                            reason=f"Zero-Touch supply dialogue: selected '{opt}'",
                        )
            for expected in VERIFIED_QUEST_DIALOGUE_OPTIONS:
                for opt in ctx.dialogue_options:
                    if opt.strip().casefold().rstrip(".!?") == expected.casefold().rstrip(".!?"):
                        return TaskDirective(
                            directive_type=DirectiveType.SELECT_DIALOGUE,
                            payload={"option": opt},
                            reason=f"Selected verified quest dialogue option: {opt}",
                        )
            return TaskDirective(
                directive_type=DirectiveType.ESCALATE_HUMAN,
                reason="UNRECOGNIZED_QUEST_DIALOGUE_OPTION",
            )
        return TaskDirective(
            directive_type=DirectiveType.CONTINUE_DIALOGUE,
            reason="Advancing quest dialogue / cutscene",
        )

    # ------------------------------------------------------------------
    # Zero-Touch Supply Procurement (PR-19 `Zero-Touch Invariant`)
    # ------------------------------------------------------------------
    # `PR-20.6`: every verified surface bank. A withdrawal never cares WHICH bank it is — only the
    # GRAND_EXCHANGE is a mission anchor (the QP latch homes there), not a funding requirement.
    BANK_NODES: tuple[str, ...] = (
        "DRAYNOR_BANK",
        "VARROCK_WEST_BANK",
        "FALADOR_WEST_BANK",
        "EDGEVILLE_BANK",
        "AL_KHARID_BANK",
        "GRAND_EXCHANGE",
    )

    def _nearest_bank_node(self, ctx: TickContext) -> str:
        """Closest verified bank to the bot (`PR-20.6`).

        The live failure mode: standing 13 tiles from the Draynor bank with 1 GP, while the funding
        logic insisted on a 200+ tile trek to the Grand Exchange — straight through the corridor that
        had already killed the account once.
        """
        best_node = self.anchor_bank_node
        best_distance = float("inf")
        for node_id in self.BANK_NODES:
            node = DEFAULT_WORLD_NODES.get(node_id)
            if node is None or node.plane != ctx.position.plane:
                continue
            distance = float(ctx.position.chebyshev_distance(node))
            if distance < best_distance:
                best_distance = distance
                best_node = node_id
        return best_node

    def _goto_anchor_bank(self, ctx: TickContext, reason: str, *, nearest: bool = False) -> TaskDirective:
        # `PR-20.6`: coin FUNDING uses the nearest bank (the live account stood 13 tiles from the
        # Draynor bank while the engine insisted on a 200+ tile GE trek). Everything else — the
        # one-time bank audit, the mission anchor — keeps the original anchor semantics.
        node_id = self._nearest_bank_node(ctx) if nearest else self.anchor_bank_node
        if ctx.position.plane != 0:
            return self._descend_to_ground(ctx.position, reason)
        anchor_wp = DEFAULT_WORLD_NODES.get(node_id)
        if anchor_wp is None or ctx.position.chebyshev_distance(anchor_wp) > 4:
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination=node_id,
                reason=reason,
            )
        return TaskDirective(
            directive_type=DirectiveType.BANK_TURNAROUND,
            payload={"open_only": True, "deposit_inventory": False},
            reason=reason,
        )

    def _audit_bank_once(self, ctx: TickContext) -> TaskDirective | None:
        """Bank stock snapshot, resolved WITHOUT a pointless trek.

        `PR-19.5`: a restarted session used to lose the in-memory `_bank_audit_done` flag and walk back
        to the GE (200+ tiles) merely to re-open the bank for one tick, even though `state.db` already
        holds `AccountProfileSnapshot.bank_summary`. Now:
          1. live open bank  -> authoritative audit (as before);
          2. persisted snapshot from `state.db` -> trusted immediately, NO walk at all;
          3. nothing known   -> one single live audit at the GE anchor.
        """
        if self._bank_audit_done:
            return None
        if ctx.bank_is_open and ctx.position.plane == 0:
            near_any_bank = any(
                DEFAULT_WORLD_NODES.get(node_id) is not None
                and ctx.position.chebyshev_distance(DEFAULT_WORLD_NODES[node_id]) <= 6
                for node_id in self.BANK_NODES
            )
            if near_any_bank or ctx.bank_items:
                audited_ids = self._audited_item_ids()
                self._bank_stock = {
                    iid: _inv_count(ctx.bank_items, item_id=iid, noted=False) for iid in audited_ids
                }
                self._bank_audit_done = True
                self._bank_source = "LIVE"
                return TaskDirective(
                    directive_type=DirectiveType.WAIT_NEXT_TICK,
                    reason=(
                        "Zero-Touch supply: bank audited live "
                        f"({sum(1 for v in self._bank_stock.values() if v)} usable stacks)"
                    ),
                )
        if ctx.bank_summary:
            audited_ids = self._audited_item_ids()
            self._bank_stock = {
                iid: _inv_count(ctx.bank_summary, item_id=iid, noted=False) for iid in audited_ids
            }
            self._bank_audit_done = True
            self._bank_source = f"PERSISTED({ctx.bank_summary_scanned_at or 'unknown'})"
            return TaskDirective(
                directive_type=DirectiveType.WAIT_NEXT_TICK,
                reason=(
                    "Zero-Touch supply: bank stock restored from state.db "
                    f"({sum(1 for v in self._bank_stock.values() if v)} usable stacks)"
                ),
            )
        if ctx.position.plane != 0 or ctx.position.chebyshev_distance(WorldPoint(3164, 3487, 0)) > 4 or not ctx.bank_is_open:
            return self._goto_anchor_bank(ctx, "Zero-Touch supply: auditing GE bank stock once")
        return TaskDirective(
            directive_type=DirectiveType.WAIT_NEXT_TICK,
            reason="Zero-Touch supply: waiting for the live bank snapshot",
        )

    def _audited_item_ids(self) -> set[int]:
        return set(self.supply.recipes.keys()) | {
            EGG_ID, BUCKET_OF_MILK_ID, POT_OF_FLOUR_ID, BALL_OF_WOOL_ID, CADAVA_BERRIES_ID,
            RED_DYE_ID, YELLOW_DYE_ID, BLUE_DYE_ID, ORANGE_DYE_ID, GOBLIN_MAIL_ID,
            ORANGE_GOBLIN_MAIL_ID, BLUE_GOBLIN_MAIL_ID, COINS_ID,
        }

    # `PR-20.4`: the dye budget is exactly what the quest costs — 20 GP for Wyson's woad leaves plus
    # 5 GP for each of Aggie's three dyes. A 512 GP lump made the bot trek to the bank for money it
    # did not need yet; the float is now demand-driven and only requested when the goblin stage runs.
    COIN_FLOAT_GP = GOBLIN_DYE_BUDGET_GP = 20 + 5 * 3

    @staticmethod
    def _needed_wool(sheep_state: int) -> int:
        """Balls of wool still required by Sheep Shearer for a given live `Varp 179` value.

        `0` = not started, `1` = accepted, `2..20` = partial hand-in progress, `21` = complete.
        """
        state = int(sheep_state)
        if state >= 21:
            return 0
        if state <= 1:
            return 20
        return max(0, 21 - state)

    def _ensure_coin_float(self, ctx: TickContext) -> TaskDirective | None:
        """Withdraw ONE coin float from the bot's own banked GP (`PR-20`).

        Called while the bot is still parked at the anchor bank, so the whole `Goblin Diplomacy`
        dye budget (20 GP for Wyson + 3 x 5 GP for Aggie) is covered without a single extra trek.
        Deliberately SOFT: if the account holds no coins at all the bundle keeps going, and the
        precise `SUPPLY_UNSOURCED:Coins` escalation still fires later, at the dye step that needs it.
        """
        # `PR-20.6` DEATH-TAX FIX: this used to be a one-way latch. The live account was funded
        # (`_coin_float_resolved = True`), then died to a Highwayman and kept 1 GP of its 55 — and the
        # engine never topped up again because the flag said "already handled". The purse is re-derived
        # from the live inventory every tick (the same stateless rule as the supply engine itself).
        if self._coin_float_unavailable:
            return None
        held = _inv_count(ctx.inventory_items, item_id=COINS_ID, noted=False)
        if held >= self.COIN_FLOAT_GP:
            self._coin_float_resolved = True
            # `PR-20.7`: the earned purse is spent on the dyes, so the earning counter only resets
            # once the float is whole again — never mid-chain.
            self._coin_earn_attempts = 0
            self._coin_earning = False
            return None
        self._coin_float_resolved = False
        if self._coin_earning:
            return self._earn_coins(ctx)
        # Only top up what is actually missing (a bot that already earned Fred's 60 GP needs nothing).
        directive = self._procure(ctx, COINS_ID, self.COIN_FLOAT_GP - held, "Coins")
        if directive is None:
            self._coin_float_resolved = True
            return None
        if directive.directive_type == DirectiveType.ESCALATE_HUMAN:
            # `PR-20.7` FIELD TRUTH: the bank is genuinely empty (live `GET /bank` on `acc_02`:
            # 6 items, ZERO coins — the documented 10,000 GP of `PR-17.5` is not there). The old
            # behaviour — give up and let the dye step escalate — left an entirely able account
            # blocked for want of 30 GP. The bot now EARNS the money the way a player would.
            self._coin_earning = True
            return self._earn_coins(ctx)
        return directive

    # ------------------------------------------------------------------
    # `PR-20.7` Zero-Touch coin EARNING (no operator GP, no shop, no guessing)
    # ------------------------------------------------------------------
    # `Pickpocket` goes through the SAME `/npcs/interact` shape already LIVE proven twice over:
    # `Talk-to` (Aggie / Wyson / Fred) and `Shear` (Sheep, 20 wool). A name-based action on a proven
    # endpoint — not a widget guess, not a shop interface.
    COIN_EARN_ATTEMPT_CAP = 80
    PICKPOCKET_TARGETS: tuple[str, ...] = ("Man", "Woman")
    # The bot funds itself where it already IS: the Draynor village centre is field-verified
    # (`DRAYNOR_BANK 3092,3245`) and full of citizens, while the dark wizards sit south of the wall
    # outside this radius — so earning never walks into the corridor that killed the account.
    COIN_EARN_NODE = "DRAYNOR_BANK"
    COIN_EARN_RADIUS = 14

    def _earn_coins(self, ctx: TickContext) -> TaskDirective | None:
        """Earn the missing dye budget in-world (`PR-20.7`), one pickpocket tick at a time."""
        held = _inv_count(ctx.inventory_items, item_id=COINS_ID, noted=False)
        if held >= self.COIN_FLOAT_GP:
            self._coin_earning = False
            self._coin_float_resolved = True
            self._coin_earn_attempts = 0
            return None
        if self._coin_earn_attempts >= self.COIN_EARN_ATTEMPT_CAP:
            self._coin_float_unavailable = True
            self._coin_earning = False
            return TaskDirective(
                directive_type=DirectiveType.ESCALATE_HUMAN,
                reason=f"SUPPLY_UNSOURCED:Coins:pickpocket_exhausted({self._coin_earn_attempts})",
            )
        node = DEFAULT_WORLD_NODES.get(self.COIN_EARN_NODE)
        if (
            node is not None
            and ctx.position.plane == 0
            and ctx.position.chebyshev_distance(node) > self.COIN_EARN_RADIUS
        ):
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination=self.COIN_EARN_NODE,
                reason=(
                    f"Zero-Touch earnings: walking to {self.COIN_EARN_NODE} to earn "
                    f"{self.COIN_FLOAT_GP - held} GP more"
                ),
            )
        target = self.PICKPOCKET_TARGETS[self._coin_earn_attempts % len(self.PICKPOCKET_TARGETS)]
        self._coin_earn_attempts += 1
        return TaskDirective(
            directive_type=DirectiveType.INTERACT_NPC,
            payload={"name": target, "action": "Pickpocket"},
            reason=(
                f"Zero-Touch earnings: pickpocketing {target} "
                f"({held}/{self.COIN_FLOAT_GP} GP, attempt {self._coin_earn_attempts})"
            ),
        )

    def _procure(self, ctx: TickContext, item_id: int, quantity: int, label: str) -> TaskDirective | None:
        """Zero-Touch cascade: inventory -> bank (once) -> in-world gathering/crafting -> escalate."""
        if _inv_count(ctx.inventory_items, item_id=item_id, noted=False) >= quantity:
            return None
        # `PR-19.4`: everything the cascade needs (bank audit included) lives on the surface, so an
        # underground start must first leave through an AUTHORIZED exit instead of walking across the map.
        # `PR-20`/`PR-20.4`: EXCEPT when this tick genuinely has to be spent underground (the
        # Lumbridge cellar bucket); forcing an exit first made the bot descend and get marched
        # straight back out, forever. The exemption is situational — `needs_descent_now` — so a bot
        # in the cellar whose bucket is already in the bag still leaves through the authorized exit.
        if not self.supply.needs_descent_now(ctx, item_id):
            underground_exit = self._authorize_exit_if_underground(ctx, label)
            if underground_exit is not None:
                return underground_exit
        audit = self._audit_bank_once(ctx)
        if audit is not None:
            return audit
        if self._bank_stock.get(item_id, 0) >= quantity:
            if not ctx.bank_is_open:
                return self._goto_anchor_bank(
                    ctx,
                    f"Zero-Touch supply: returning to bank for {label}",
                    nearest=(item_id == COINS_ID),
                )
            return TaskDirective(
                directive_type=DirectiveType.BANK_TURNAROUND,
                payload={
                    "deposit_inventory": False,
                    "withdraw": [{"item_id": item_id, "quantity": quantity}],
                    "close_when_done": True,
                },
                reason=f"Zero-Touch supply: withdrawing {quantity}x {label} from bank",
            )
        directive = self.supply.ensure(ctx, item_id, quantity, recurse=self._procure_nested)
        if directive is not None:
            return directive
        return TaskDirective(
            directive_type=DirectiveType.ESCALATE_HUMAN,
            reason=f"SUPPLY_UNSOURCED:{label}",
        )

    def _authorize_exit_if_underground(self, ctx: TickContext, label: str) -> TaskDirective | None:
        """Return an `AUTHORIZE_SURFACE_EXIT` directive while the bot is below ground, else `None`."""
        if not classify_underground(ctx.position.x, ctx.position.y):
            return None
        return self.supply.ensure_underground_exit(ctx, label)

    def _procure_nested(self, ctx: TickContext, item_id: int, quantity: int, label: str) -> TaskDirective | None:
        """Bank-aware recursion hook injected into the supply engine (ingredients too!)."""
        return self._procure(ctx, item_id, quantity, label)

    def _ensure_supplies_at_anchor(
        self,
        ctx: TickContext,
        required_items: list[dict[str, Any]],
        quest_label: str,
    ) -> TaskDirective | None:
        """Backwards-compatible declarative wrapper over `_procure` for every required item."""
        for req in required_items:
            directive = self._procure(
                ctx,
                int(req["item_id"]),
                int(req.get("quantity", 1)),
                str(req.get("name") or req["item_id"]),
            )
            if directive is not None:
                return directive
        return None

    def _owns_or_banked(self, ctx: TickContext, item_id: int) -> bool:
        """Is `item_id` already in the bag, or known to be in the bank? (`PR-20.7`)

        Conservative while the bank is unaudited (`_bank_stock` empty): an unknown vault must not be
        assumed to hold the dyes, so the coin budget stays demanded until the audit says otherwise.
        """
        if _inv_count(ctx.inventory_items, item_id=item_id, noted=False) > 0:
            return True
        return int(self._bank_stock.get(item_id, 0)) > 0

    def _coins_still_needed(self, ctx: TickContext, outstanding: list[tuple[int, str]]) -> bool:
        """Do the OUTSTANDING goblin mails still need a dye that costs money? (`PR-20.7`)

        Coins are only ever spent on dyes: Wyson's 20 GP for woad leaves and Aggie's 5 GP per dye.
        The plain brown mail is free from a crate. A finished dye already in the bag or the bank makes
        the whole coin budget moot — the live order-agnostic case, where the dyes survive a death while
        the cash does not (OSRS keeps only the three most valuable items).
        """
        outstanding_ids = {item_id for item_id, _ in outstanding}
        if ORANGE_GOBLIN_MAIL_ID in outstanding_ids and not self._owns_or_banked(ctx, ORANGE_DYE_ID):
            return True
        if BLUE_GOBLIN_MAIL_ID in outstanding_ids and not self._owns_or_banked(ctx, BLUE_DYE_ID):
            return True
        return False

    def _prepare_goblin_diplomacy_supplies(self, ctx: TickContext) -> TaskDirective | None:
        """Zero-Touch Goblin Diplomacy staging: 1 blue mail + 1 orange mail + 1 plain brown mail."""
        # Verified wire order (T68): Orange dye on Goblin mail FIRST, then Blue dye -> [1769, 288, 1767, 288].
        wanted = (
            (ORANGE_GOBLIN_MAIL_ID, "Orange goblin mail"),
            (BLUE_GOBLIN_MAIL_ID, "Blue goblin mail"),
            (GOBLIN_MAIL_ID, "Goblin mail"),
        )
        outstanding = [(i, l) for i, l in wanted if _inv_count(ctx.inventory_items, item_id=i, noted=False) < 1]
        # `PR-20.4`/`PR-20.7`: the coin float belongs HERE — the only stage that spends money — and
        # it is strictly DEMAND-DRIVEN. Only the two DYE mails cost coins (Wyson 20 GP, Aggie 5 GP per
        # dye); the plain brown mail is free from a crate. Seeking GP while those two are already made
        # (or while only the free mail is missing) burned a bank trek in `PR-20.4` and would now burn
        # 80 pickpocket attempts — so the demand gate covers earning too.
        if self._coins_still_needed(ctx, outstanding):
            coin_float = self._ensure_coin_float(ctx)
            if coin_float is not None:
                return coin_float
        for item_id, label in outstanding:
            directive = self._procure(ctx, item_id, 1, label)
            if directive is not None:
                return directive
        if ctx.bank_is_open:
            return TaskDirective(
                directive_type=DirectiveType.BANK_TURNAROUND,
                payload={"deposit_inventory": False, "withdraw": [], "close_when_done": True},
                reason="Goblin Diplomacy: all 3 goblin mails ready; closing bank",
            )
        return None

    @staticmethod
    def _ascend_juliet_stairs(pos: WorldPoint) -> TaskDirective:
        stairs_ground = WorldPoint(3157, 3435, 0, "VARROCK_JULIET_STAIRS")
        if pos.chebyshev_distance(stairs_ground) > 3:
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination="VARROCK_JULIET_STAIRS",
                reason="Romeo & Juliet: routing safely to Juliet ground stairs",
            )
        return TaskDirective(
            directive_type=DirectiveType.INTERACT_OBJECT,
            payload={"name": "Staircase", "action": "Climb-up"},
            reason="Romeo & Juliet: climbing up staircase to Juliet balcony (plane 1)",
        )

    @staticmethod
    def _descend_juliet_stairs(pos: WorldPoint) -> TaskDirective:
        stairs_up = WorldPoint(3156, 3435, 1, "JULIET_UPSTAIRS")
        if pos.chebyshev_distance(stairs_up) > 2:
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination=stairs_up,
                reason="Romeo & Juliet: walking to upstairs staircase (3156, 3435, 1)",
            )
        return TaskDirective(
            directive_type=DirectiveType.INTERACT_OBJECT,
            payload={"name": "Staircase", "action": "Climb-down"},
            reason="Romeo & Juliet: climbing down staircase to ground floor (plane 0)",
        )

    def _descend_to_ground(self, pos: WorldPoint, label: str) -> TaskDirective:
        """Plane-safe descent (`PR-20`).

        Every `_walk_and_talk` destination except Juliet's balcony is on plane 0. A bot left upstairs
        (spinning wheel, Juliet's house, the windmill) cannot be routed to a surface node: the
        navigation engine has no stairs edge, so it would orbit forever. Count the attempts and
        escalate instead of looping silently.
        """
        self._descend_attempts += 1
        if self._descend_attempts > 12:
            return TaskDirective(
                directive_type=DirectiveType.ESCALATE_HUMAN,
                reason=f"STUCK_UPSTAIRS:plane_{pos.plane}:{label}",
            )
        return TaskDirective(
            directive_type=DirectiveType.INTERACT_OBJECT,
            payload={"name": "Staircase", "action": "Climb-down"},
            reason=f"{label}: descending from plane {pos.plane} to the ground floor",
        )

    def _walk_and_talk(
        self, pos: WorldPoint, node_name: str, target_wp: WorldPoint, npc_name: str, label: str
    ) -> TaskDirective:
        if pos.plane > target_wp.plane:
            # `PR-20`: never ask the router for a surface node while standing upstairs.
            return self._descend_to_ground(pos, label)
        if pos.plane != target_wp.plane or pos.chebyshev_distance(target_wp) > 4:
            dest: str | WorldPoint = node_name if target_wp.plane == 0 else target_wp
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination=dest,
                reason=f"{label}: routing safely to {npc_name}",
            )
        return TaskDirective(
            directive_type=DirectiveType.INTERACT_NPC,
            payload={"name": npc_name, "action": "Talk-to"},
            reason=f"{label}: talking to {npc_name}",
        )

    def evaluate_tick(self, ctx: TickContext) -> TaskDirective:
        if ctx.position.plane == 0:
            self._descend_attempts = 0
        if ctx.tutorial_varp_281 < 1000:
            return TaskDirective(
                directive_type=DirectiveType.ESCALATE_HUMAN,
                reason="PRECONDITION_FAILED_TUTORIAL_VARP_281_INCOMPLETE",
            )

        if ctx.dialogue_open:
            return self._handle_quest_dialogue(ctx)

        if self._baseline_quest_points is None:
            self._baseline_quest_points = int(ctx.quest_points)

        cook_state = int(ctx.varps.get(VARP_COOKS_ASSISTANT, 0))
        sheep_state = int(ctx.varps.get(VARP_SHEEP_SHEARER, 0))
        romeo_state = int(ctx.varps.get(VARP_ROMEO_AND_JULIET, 0))
        goblin_state = int(ctx.varps.get(VARP_GOBLIN_DIPLOMACY, 0))

        all_four_done = cook_state >= 2 and sheep_state >= 21 and romeo_state >= 100 and goblin_state >= 6
        # `PR-20`: the goal latch must never fire on QP the account already carried. The mission is
        # defined by the four quest varps; total QP only counts when THIS run moved it.
        qp_progressed = int(ctx.quest_points) > int(self._baseline_quest_points or 0)

        # 1. Immediate QP Goal Latch (`quest_points >= 10` or all 4 quests complete -> home & bank!)
        if all_four_done or (qp_progressed and ctx.quest_points >= self.target_quest_points):
            if ctx.position.plane != 0:
                return self._descend_to_ground(ctx.position, "Stage 1A latch")
            anchor_wp = WorldPoint(3164, 3487, 0, self.anchor_bank_node)
            if ctx.position.chebyshev_distance(anchor_wp) > 4:
                return TaskDirective(
                    directive_type=DirectiveType.NAVIGATE_TO,
                    destination=self.anchor_bank_node,
                    reason=f"Quest target reached (QP={ctx.quest_points}); homing to {self.anchor_bank_node}",
                )
            if ctx.inventory_items or ctx.bank_is_open:
                return TaskDirective(
                    directive_type=DirectiveType.BANK_TURNAROUND,
                    payload={"deposit_inventory": True, "withdraw": [], "close_when_done": True},
                    reason="Depositing leftover quest items at anchor bank",
                )
            return TaskDirective(
                directive_type=DirectiveType.COMPLETE_TASK,
                reason=f"Stage 1A Quest Bundle completed (QP={ctx.quest_points}) and anchored at {self.anchor_bank_node}",
            )

        # 2. Quest 1: Cook's Assistant (1 QP)
        # `PR-20.5`: same defect class as incident #10 — `== 0` meant a started-but-unfinished quest
        # (`Varp 29 == 1`) walked to the Cook with an empty bag. Gate on "not finished".
        if cook_state < 2:
            prep = self._ensure_supplies_at_anchor(
                ctx,
                [
                    {"item_id": 1944, "name": "Egg", "quantity": 1},
                    {"item_id": 1927, "name": "Bucket of milk", "quantity": 1},
                    {"item_id": 1933, "name": "Pot of flour", "quantity": 1},
                ],
                "Cook's Assistant",
            )
            if prep is not None:
                return prep
            return self._walk_and_talk(
                ctx.position,
                "LUMBRIDGE_KITCHEN",
                WorldPoint(3206, 3214, 0),
                "Cook",
                f"Cook's Assistant (QP={ctx.quest_points})",
            )

        # 3. Quest 2: Sheep Shearer (1 QP, Varp 179: 0 -> 1 -> 2..20 -> 21)
        if sheep_state < 21:
            wool_needed = self._needed_wool(sheep_state)
            if wool_needed > 0:
                prep = self._ensure_supplies_at_anchor(
                    ctx,
                    [{"item_id": 1759, "name": "Ball of wool", "quantity": wool_needed}],
                    "Sheep Shearer",
                )
                if prep is not None:
                    return prep
            return self._walk_and_talk(
                ctx.position,
                "LUMBRIDGE_SHEEP_FARM",
                WorldPoint(3190, 3273, 0),
                "Fred the Farmer",
                f"Sheep Shearer (QP={ctx.quest_points})",
            )

        # 4. Quest 3: Romeo & Juliet (5 QP, Varp 144: 0 -> 10 -> 20 -> 30 -> 40 -> 50 -> 60 -> 100)
        if romeo_state < 100:
            # `PR-20`: the berries are needed up to the Apothecary hand-off (state 40 -> 50). Asking
            # only at state 0 left a relog/death with no berries and no way to finish the quest.
            if romeo_state < 50:
                prep = self._ensure_supplies_at_anchor(
                    ctx,
                    [{"item_id": 753, "name": "Cadava berries", "quantity": 1}],
                    "Romeo & Juliet",
                )
                if prep is not None:
                    return prep
            # Stages requiring Juliet on plane 1: state == 10 (deliver/get letter) or state == 50 with Cadava potion (756)
            has_potion = _inv_count(ctx.inventory_items, item_id=756, noted=False) >= 1
            needs_juliet = romeo_state == 10 or (romeo_state == 50 and has_potion)
            if needs_juliet:
                if ctx.position.plane == 0:
                    return self._ascend_juliet_stairs(ctx.position)
                return self._walk_and_talk(
                    ctx.position,
                    "JULIET_BALCONY",
                    WorldPoint(3158, 3427, 1, "JULIET_BALCONY"),
                    "Juliet",
                    "Romeo & Juliet",
                )

            # All other Romeo stages are on plane 0 -> descend first if currently on plane 1!
            if ctx.position.plane != 0:
                return self._descend_juliet_stairs(ctx.position)

            if romeo_state in (0, 20, 60):
                return self._walk_and_talk(
                    ctx.position, "VARROCK_SQUARE", WorldPoint(3211, 3422, 0), "Romeo", "Romeo & Juliet"
                )
            if romeo_state == 30:
                return self._walk_and_talk(
                    ctx.position,
                    "VARROCK_EAST_CHURCH",
                    WorldPoint(3254, 3483, 0),
                    "Father Lawrence",
                    "Romeo & Juliet",
                )
            return self._walk_and_talk(
                ctx.position,
                "VARROCK_APOTHECARY",
                WorldPoint(3195, 3405, 0),
                "Apothecary",
                "Romeo & Juliet",
            )

        # 5. Quest 4: Goblin Diplomacy (5 QP, Varp 62 < 6)
        # `PR-20.5` FIELD FIX (incident #10, live `Varp 62 = 1`): the gate said `== 0`, so the moment
        # the quest was ACCEPTED (state 1) the whole supply prep was skipped and the engine sent the
        # bot 250 tiles to General Bentnoze empty-handed — through the Dark Wizard corridor at 2 HP.
        # The gate must be "not finished yet", never "not started yet".
        if goblin_state < 6:
            prep = self._prepare_goblin_diplomacy_supplies(ctx)
            if prep is not None:
                return prep
        return self._walk_and_talk(
            ctx.position,
            "GOBLIN_VILLAGE",
            WorldPoint(2958, 3512, 0),
            "General Bentnoze",
            f"Goblin Diplomacy (QP={ctx.quest_points})",
        )
