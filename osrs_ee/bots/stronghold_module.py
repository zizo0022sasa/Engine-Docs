"""Complete 4-Floor Stronghold of Security Task Module (`osrs_ee/bots/stronghold_module.py` — PR-13 / PR-15).

Field-Verified OSRS Mechanics & Coordinates (`run_stronghold_bot.py` + `StrongholdScript.java` + Live Run 1/2):
- Pure tick-driven `TaskModule` with ZERO transport, socket, sleep, or safety imports (`ADR-012`).
- Autonomous Pre-Flight Food & Bank Staging (`PR-15` Req #2):
  1. Verifies inventory holds `>= min_required_food` (default `6`) verified un-noted food items on `SURFACE`
     before descending into `BARBARIAN_VILLAGE` (`id=20790`).
  2. Automatically visits the nearest safe surface bank (`DRAYNOR_BANK`, `VARROCK_WEST_BANK`, or
     `GRAND_EXCHANGE`) and withdraws up to `target_food_count` (default `10`) food items.
  3. Zero-Touch Fallback: if the bank holds `< min_required_food` food (e.g., fresh post-Tutorial account),
     autonomously nets Shrimps at `LUMBRIDGE_SWAMP_FISHING` (claiming a free `Small fishing net` `303` from
     `Fishing tutor` if needed) and cooks them on the permanent Barbarian Village `Fire` at `BARBARIAN_RIVER_FIRE`
     (`(3078, 3445, 0)`, `270:14`, zero quest/door lock) right beside `BARBARIAN_VILLAGE`.
- Surface Anchor: `GRAND_EXCHANGE` `(3164, 3487, 0)` <-> Entrance `BARBARIAN_VILLAGE` `(3081, 3421, 0)` (`id=20790`).
- Streamlined `chebyshev_distance <= 2` Dungeon Object & Portal Passage (`PR-15` Req #4):
  1. Floor 1 (*Vault of War*, `1855..1935, 5180..5250`, `Varbit 2309`):
     - 4 `Gate of War` stages -> *Gift of Peace* (`id=20656`, `Open`, `2,000 GP`) -> Shortcut `20786` / Ladder `20785`.
  2. Floor 2 (*Catacomb of Famine*, `1980..2060, 5180..5250`, `Varbit 2310`):
     - 4 `Rickety door` stages -> *Grain of Plenty* (`id=19000`, `Search`, `3,000 GP`) -> Shortcut `19005` / Ladder `19004`.
  3. Floor 3 (*Pit of Pestilence*, `2110..2185, 5240..5315`, `Varbit 2311`):
     - 3 `Oozing barrier` stages -> *Box of Health* (`id=23709`, `Open`, `5,000 GP`) -> Shortcut `23707` / Ladder `23706`.
  4. Floor 4 (*Sepulchre of Death*, `2300..2380, 5180..5260`, `Varbit 2312`):
     - 4 `Portal of Death` stages (`chebyshev_distance <= 2` + `target_x/target_y` disambiguation)
     - *Cradle of Life* (`id=23731`, `Search`) -> Widget `270:15` (*Fancy boots*)
     - Shortcut `23922` / Bone Chain exit (`id=23732`, `Climb-up` at `(2349, 5215, 0)`) via `AUTHORIZE_SURFACE_EXIT`.
- Post-Completion Homing: Routes back to `GRAND_EXCHANGE`, deposits `Fancy boots` (`9005`) + `Coins` (`995`),
  closes bank, and emits `COMPLETE_TASK`.
"""

from __future__ import annotations

from typing import Any, Mapping

from osrs_ee.ai.dialogue_solver import DeterministicStrongholdSolver
from osrs_ee.bots.base import DirectiveType, TaskDirective, TaskModule, TickContext
from osrs_ee.navigation.waypoint_graph import WorldPoint

STRONGHOLD_REWARD_VARBITS: tuple[int, int, int, int] = (2309, 2310, 2311, 2312)

# Verified Object & Widget Identifiers
ENTRANCE_ROCK_HOLE_ID = 20790
LADDER_WARNING_WIDGET = {"groupId": 579, "childId": 17}
FANCY_BOOTS_WIDGET = {"groupId": 270, "childId": 15}
COOK_ALL_WIDGET = {"groupId": 270, "childId": 14}
FANCY_BOOTS_ITEM_ID = 9005
COINS_ITEM_ID = 995
SMALL_FISHING_NET_ID = 303

FLOOR_1_REWARD_CHEST_ID = 20656     # Gift of Peace (Open)
FLOOR_1_SHORTCUT_PORTAL_ID = 20786  # Use
FLOOR_1_LADDER_DOWN_ID = 20785      # Climb-down
FLOOR_1_LADDER_UP_ID = 20784        # Climb-up (emergency/early exit)

FLOOR_2_REWARD_SACK_ID = 19000      # Grain of Plenty (Search)
FLOOR_2_SHORTCUT_PORTAL_ID = 19005  # Use
FLOOR_2_LADDER_DOWN_ID = 19004      # Climb-down

FLOOR_3_REWARD_CHEST_ID = 23709     # Box of Health (Open)
FLOOR_3_SHORTCUT_PORTAL_ID = 23707  # Use
FLOOR_3_LADDER_DOWN_ID = 23706      # Climb-down

FLOOR_4_CRADLE_ID = 23731           # Cradle of Life (Search)
FLOOR_4_SHORTCUT_PORTAL_ID = 23922  # Use
FLOOR_4_BONE_CHAIN_UP_ID = 23732    # Bone Chain exit to Surface (Climb-up)

# Priority-ordered verified single-click edible F2P food names (kept local to honor ADR-012 isolation)
PRIORITY_EDIBLE_FOOD_NAMES: tuple[str, ...] = (
    "swordfish",
    "lobster",
    "tuna",
    "salmon",
    "pike",
    "trout",
    "herring",
    "sardine",
    "bread",
    "cooked meat",
    "cooked chicken",
    "anchovies",
    "shrimps",
    "cabbage",
)
EDIBLE_FOOD_SET: frozenset[str] = frozenset(PRIORITY_EDIBLE_FOOD_NAMES)
RAW_SWAMP_FISH_NAMES: frozenset[str] = frozenset({"raw shrimps", "raw anchovies"})

SURFACE_BANK_COORDS: dict[str, WorldPoint] = {
    "DRAYNOR_BANK": WorldPoint(3092, 3245, 0, "DRAYNOR_BANK"),
    "VARROCK_WEST_BANK": WorldPoint(3185, 3436, 0, "VARROCK_WEST_BANK"),
    "GRAND_EXCHANGE": WorldPoint(3164, 3487, 0, "GRAND_EXCHANGE"),
}


def classify_stronghold_region(pos: WorldPoint) -> str:
    """Classify player coordinates into `SURFACE`, `FLOOR_1..4`, or `UNKNOWN_REGION`."""
    px, py = pos.x, pos.y
    if py < 5000:
        return "SURFACE"
    if 1855 <= px <= 1935 and 5180 <= py <= 5250:
        return "FLOOR_1"
    if 1980 <= px <= 2060 and 5180 <= py <= 5250:
        return "FLOOR_2"
    if 2110 <= px <= 2185 and 5240 <= py <= 5315:
        return "FLOOR_3"
    if 2300 <= px <= 2380 and 5180 <= py <= 5260:
        return "FLOOR_4"
    return "UNKNOWN_REGION"


class StrongholdTaskModule(TaskModule):
    """Complete 4-floor autonomous Stronghold of Security `TaskModule` (`stage2_stronghold_10k`)."""

    def __init__(
        self,
        *,
        anchor_bank_node: str = "GRAND_EXCHANGE",
        max_claim_attempts: int = 6,
        min_required_food: int = 6,
        target_food_count: int = 10,
        enable_fish_cook_fallback: bool = True,
    ) -> None:
        self._solver = DeterministicStrongholdSolver()
        self.anchor_bank_node = anchor_bank_node
        self.max_claim_attempts = max(2, int(max_claim_attempts))
        self.min_required_food = max(0, int(min_required_food))
        self.target_food_count = max(self.min_required_food, int(target_food_count))
        self.enable_fish_cook_fallback = bool(enable_fish_cook_fallback)

        self._claim_attempts_by_floor: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}
        self._cradle_searched_this_visit: bool = False
        self._pending_ladder_modal_floor: int | None = None

        # Pre-flight surface food staging state
        self._staging_bank_node: str | None = None
        self._bank_food_exhausted: bool = False
        self._staging_cook_phase: bool = False
        self._awaiting_cook_widget: bool = False
        self._cook_object_clicked_tick: int = -100
        self._last_gather_action_tick: int = -100
        self._last_raw_fish_qty: int = 0

    @property
    def module_id(self) -> str:
        return "stage2_stronghold_10k"

    @property
    def watched_varbits(self) -> tuple[int, ...]:
        return STRONGHOLD_REWARD_VARBITS

    @staticmethod
    def _count_edible_food(items: tuple[Mapping[str, Any], ...]) -> int:
        total = 0
        for it in items:
            if bool(it.get("noted", False)):
                continue
            nm = str(it.get("name", "")).strip().casefold()
            qty = int(it.get("quantity", 0))
            if qty > 0 and nm in EDIBLE_FOOD_SET:
                total += qty
        return total

    @staticmethod
    def _count_raw_swamp_fish(items: tuple[Mapping[str, Any], ...]) -> int:
        total = 0
        for it in items:
            if bool(it.get("noted", False)):
                continue
            nm = str(it.get("name", "")).strip().casefold()
            iid = int(it.get("item_id", -1))
            qty = int(it.get("quantity", 0))
            if qty > 0 and (nm in RAW_SWAMP_FISH_NAMES or iid in (317, 321)):
                total += qty
        return total

    @staticmethod
    def _has_small_net(items: tuple[Mapping[str, Any], ...]) -> bool:
        for it in items:
            if bool(it.get("noted", False)):
                continue
            if (
                int(it.get("item_id", -1)) == SMALL_FISHING_NET_ID
                or str(it.get("name", "")).strip().casefold() == "small fishing net"
            ) and int(it.get("quantity", 0)) > 0:
                return True
        return False

    @staticmethod
    def _estimate_free_slots(items: tuple[Mapping[str, Any], ...]) -> int:
        used = 0
        for it in items:
            qty = int(it.get("quantity", 0))
            if qty <= 0:
                continue
            is_stackable = (
                bool(it.get("noted", False))
                or int(it.get("item_id", 0)) == COINS_ITEM_ID
                or str(it.get("name", "")).strip().casefold() == "coins"
            )
            used += 1 if is_stackable else qty
        return max(0, 28 - used)

    @staticmethod
    def _select_best_bank_food(bank_items: tuple[Mapping[str, Any], ...]) -> Mapping[str, Any] | None:
        rank_map = {nm: idx for idx, nm in enumerate(PRIORITY_EDIBLE_FOOD_NAMES)}
        best: tuple[int, int, Mapping[str, Any]] | None = None
        for it in bank_items:
            if bool(it.get("noted", False)):
                continue
            nm = str(it.get("name", "")).strip().casefold()
            qty = int(it.get("quantity", 0))
            if qty > 0 and nm in rank_map:
                cand = (rank_map[nm], -qty, it)
                if best is None or (cand[0], cand[1]) < (best[0], best[1]):
                    best = cand
        return best[2] if best is not None else None

    @staticmethod
    def _choose_nearest_staging_bank(pos: WorldPoint) -> str:
        if pos.y < 3380:
            return "DRAYNOR_BANK"
        if pos.y < 3462:
            return "VARROCK_WEST_BANK"
        return "GRAND_EXCHANGE"

    @staticmethod
    def _has_unbanked_loot(ctx: TickContext) -> bool:
        for it in ctx.inventory_items:
            iid = int(it.get("item_id", 0))
            nm = str(it.get("name", "")).strip().casefold()
            qty = int(it.get("quantity", 0))
            if qty <= 0:
                continue
            if iid == FANCY_BOOTS_ITEM_ID or nm in ("fancy boots", "fighting boots"):
                return True
            if (iid == COINS_ITEM_ID or nm == "coins") and qty >= 2000:
                return True
        return False

    @staticmethod
    def _walk_or_pass_door(
        pos: WorldPoint,
        door_stand: WorldPoint,
        door_name: str,
        stage_label: str,
        *,
        max_dist: int = 2,
    ) -> TaskDirective:
        if pos.chebyshev_distance(door_stand) > max_dist:
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination=door_stand,
                reason=f"{stage_label}: walking to {door_name} at ({door_stand.x}, {door_stand.y})",
            )
        return TaskDirective(
            directive_type=DirectiveType.INTERACT_OBJECT,
            payload={
                "name": door_name,
                "action": "Pass-through",
                "target_x": door_stand.x,
                "target_y": door_stand.y,
            },
            reason=f"{stage_label}: passing {door_name}",
        )

    def _descend_ladder_with_modal_support(
        self,
        *,
        pos: WorldPoint,
        ladder_stand: WorldPoint,
        ladder_id: int,
        floor_num: int,
    ) -> TaskDirective:
        if pos.chebyshev_distance(ladder_stand) > 2:
            self._pending_ladder_modal_floor = None
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination=ladder_stand,
                reason=f"Floor {floor_num}: walking to descent ladder {ladder_id}",
            )
        if self._pending_ladder_modal_floor == floor_num:
            self._pending_ladder_modal_floor = None
            return TaskDirective(
                directive_type=DirectiveType.CLICK_WIDGET,
                payload=dict(LADDER_WARNING_WIDGET),
                reason=f"Floor {floor_num}: confirming ladder warning modal (579:17)",
            )
        self._pending_ladder_modal_floor = floor_num
        return TaskDirective(
            directive_type=DirectiveType.INTERACT_OBJECT,
            payload={"id": ladder_id, "action": "Climb-down"},
            reason=f"Floor {floor_num}: climbing down ladder {ladder_id} to Floor {floor_num + 1}",
        )

    def _claim_floor_object(
        self,
        *,
        floor_num: int,
        pos: WorldPoint,
        stand: WorldPoint,
        object_id: int,
        action: str,
        label: str,
    ) -> TaskDirective:
        if pos.chebyshev_distance(stand) > 2:
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination=stand,
                reason=f"Floor {floor_num}: walking to {label} at ({stand.x}, {stand.y})",
            )
        attempts = self._claim_attempts_by_floor.get(floor_num, 0) + 1
        self._claim_attempts_by_floor[floor_num] = attempts
        if attempts > self.max_claim_attempts:
            return TaskDirective(
                directive_type=DirectiveType.ESCALATE_HUMAN,
                reason=f"FLOOR_{floor_num}_CLAIM_BLOCKED_CHECK_2FA",
            )
        return TaskDirective(
            directive_type=DirectiveType.INTERACT_OBJECT,
            payload={"id": object_id, "action": action},
            reason=f"Floor {floor_num}: claiming {label} (attempt {attempts}/{self.max_claim_attempts})",
        )

    def _stage_preflight_food(self, ctx: TickContext, edible_qty: int) -> TaskDirective:
        """Stage `>= min_required_food` (up to `target_food_count`) via nearest bank or Shrimp fish+cook."""
        raw_qty = self._count_raw_swamp_fish(ctx.inventory_items)
        # Resume-aware shortcut: if inventory ALREADY holds enough raw+cooked fish (e.g. restarted at bridge
        # with 11x Raw shrimps), go straight to cooking at BARBARIAN_RIVER_FIRE without backtracking to bank!
        if raw_qty > 0 and (edible_qty + raw_qty) >= self.min_required_food and not ctx.bank_is_open:
            self._bank_food_exhausted = True
            self._staging_cook_phase = True

        # Phase 1: Check nearest safe surface bank first
        if not self._bank_food_exhausted:
            if self._staging_bank_node is None:
                self._staging_bank_node = self._choose_nearest_staging_bank(ctx.position)
            bank_wp = SURFACE_BANK_COORDS.get(
                self._staging_bank_node, SURFACE_BANK_COORDS["DRAYNOR_BANK"]
            )
            if ctx.position.chebyshev_distance(bank_wp) > 4:
                return TaskDirective(
                    directive_type=DirectiveType.NAVIGATE_TO,
                    destination=self._staging_bank_node,
                    reason=(
                        f"Pre-flight food check ({edible_qty}/{self.min_required_food}); "
                        f"routing safely to {self._staging_bank_node}"
                    ),
                )

            if not ctx.bank_is_open:
                return TaskDirective(
                    directive_type=DirectiveType.BANK_TURNAROUND,
                    payload={"open_only": True, "deposit_inventory": False},
                    reason=f"Opening {self._staging_bank_node} to inspect food reserves",
                )

            free_slots = self._estimate_free_slots(ctx.inventory_items)
            best_food = self._select_best_bank_food(ctx.bank_items)
            if best_food is not None:
                needed_for_min = max(1, self.min_required_food - edible_qty)
                if free_slots < needed_for_min:
                    return TaskDirective(
                        directive_type=DirectiveType.BANK_TURNAROUND,
                        payload={"deposit_inventory": True, "withdraw": [], "close_when_done": False},
                        reason="Clearing non-food clutter in bank before withdrawing Stronghold food",
                    )

                food_id = int(best_food["item_id"])
                food_name = str(best_food["name"])
                avail = int(best_food["quantity"])
                want_delta = max(1, min(free_slots, self.target_food_count - edible_qty))
                take = min(avail, want_delta)
                have_same = sum(
                    int(i.get("quantity", 0))
                    for i in ctx.inventory_items
                    if not bool(i.get("noted", False))
                    and (
                        int(i.get("item_id", -1)) == food_id
                        or str(i.get("name", "")).strip().casefold() == food_name.casefold()
                    )
                )
                close_after = (edible_qty + take) >= self.min_required_food
                return TaskDirective(
                    directive_type=DirectiveType.BANK_TURNAROUND,
                    payload={
                        "deposit_inventory": False,
                        "withdraw": [
                            {
                                "item_id": food_id,
                                "name": food_name,
                                "quantity": have_same + take,
                            }
                        ],
                        "allow_best_effort": True,
                        "close_when_done": close_after,
                    },
                    reason=(
                        f"Withdrawing {take}x {food_name} for Stronghold survival "
                        f"({edible_qty + take}/{self.target_food_count})"
                    ),
                )

            # Bank holds no more verified food -> grab Small fishing net (303) if banked, then close bank
            self._bank_food_exhausted = True
            if (
                self.enable_fish_cook_fallback
                and not self._has_small_net(ctx.inventory_items)
                and self._has_small_net(ctx.bank_items)
                and free_slots >= 1
            ):
                return TaskDirective(
                    directive_type=DirectiveType.BANK_TURNAROUND,
                    payload={
                        "deposit_inventory": False,
                        "withdraw": [
                            {
                                "item_id": SMALL_FISHING_NET_ID,
                                "name": "Small fishing net",
                                "quantity": 1,
                            }
                        ],
                        "allow_best_effort": True,
                        "close_when_done": True,
                    },
                    reason="No food in bank; withdrawing Small fishing net (303) for autonomous Shrimp staging",
                )
            return TaskDirective(
                directive_type=DirectiveType.BANK_TURNAROUND,
                payload={"deposit_inventory": False, "withdraw": [], "close_when_done": True},
                reason="Bank food exhausted; closing bank to transition to autonomous Shrimp staging",
            )

        # Phase 2: Autonomous Zero-Touch Lumbridge Swamp Shrimp Netting + Barbarian River Fire Cooking
        if not self.enable_fish_cook_fallback:
            return TaskDirective(
                directive_type=DirectiveType.ESCALATE_HUMAN,
                reason=f"INSUFFICIENT_PREFLIGHT_FOOD:{edible_qty}<{self.min_required_food}",
            )

        if ctx.bank_is_open:
            return TaskDirective(
                directive_type=DirectiveType.BANK_TURNAROUND,
                payload={"deposit_inventory": False, "withdraw": [], "close_when_done": True},
                reason="Closing bank before autonomous Shrimp staging",
            )

        raw_qty = self._count_raw_swamp_fish(ctx.inventory_items)
        free_slots = self._estimate_free_slots(ctx.inventory_items)
        desired_batch = max(self.min_required_food + 4, self.target_food_count + 2)

        if raw_qty == 0:
            self._staging_cook_phase = False
            self._awaiting_cook_widget = False
            # Guard against inventory clogged by burnt fish
            if free_slots < 3:
                vwb_wp = SURFACE_BANK_COORDS["VARROCK_WEST_BANK"]
                if ctx.position.chebyshev_distance(vwb_wp) > 4:
                    return TaskDirective(
                        directive_type=DirectiveType.NAVIGATE_TO,
                        destination="VARROCK_WEST_BANK",
                        reason="Clearing burnt fish clutter at Varrock West Bank",
                    )
                return TaskDirective(
                    directive_type=DirectiveType.BANK_TURNAROUND,
                    payload={
                        "deposit_inventory": True,
                        "withdraw": [
                            {"item_id": SMALL_FISHING_NET_ID, "name": "Small fishing net", "quantity": 1}
                        ],
                        "allow_best_effort": True,
                        "close_when_done": True,
                    },
                    reason="Depositing burnt fish clutter while retaining Small fishing net",
                )
        elif (edible_qty + raw_qty) >= desired_batch or free_slots == 0:
            self._staging_cook_phase = True

        # Subphase 2A: Cook raw Shrimps on permanent Barbarian Village Fire (id=26185, zero quest/door lock)
        if self._staging_cook_phase and raw_qty > 0:
            fire_wp = WorldPoint(3078, 3445, 0, "BARBARIAN_RIVER_FIRE")
            if ctx.position.chebyshev_distance(fire_wp) > 2:
                self._awaiting_cook_widget = False
                return TaskDirective(
                    directive_type=DirectiveType.NAVIGATE_TO,
                    destination="BARBARIAN_RIVER_FIRE",
                    reason=f"Routing with {raw_qty} raw fish to Barbarian Village permanent Fire (3078, 3445)",
                )

            if raw_qty < self._last_raw_fish_qty:
                # Active cooking batch in progress — do not interrupt!
                self._last_raw_fish_qty = raw_qty
                self._last_gather_action_tick = ctx.game_tick
                self._awaiting_cook_widget = False
                return TaskDirective(
                    directive_type=DirectiveType.WAIT_NEXT_TICK,
                    reason=f"Cooking Shrimps on Fire ({raw_qty} raw remaining, {edible_qty} cooked)",
                )

            if self._awaiting_cook_widget:
                # Give client 2 ticks (1.2s) after clicking Fire so Make-All chatbox widget 270:14 is open!
                if (ctx.game_tick - self._cook_object_clicked_tick) < 2:
                    return TaskDirective(
                        directive_type=DirectiveType.WAIT_NEXT_TICK,
                        reason="Waiting 1 tick for Cook Make-All widget (270:14) to open",
                    )
                self._awaiting_cook_widget = False
                self._last_raw_fish_qty = raw_qty
                self._last_gather_action_tick = ctx.game_tick
                return TaskDirective(
                    directive_type=DirectiveType.CLICK_WIDGET,
                    payload=dict(COOK_ALL_WIDGET),
                    reason="Confirming Cook-All Shrimps widget (270:14)",
                )

            if (ctx.game_tick - self._last_gather_action_tick) < 6:
                return TaskDirective(
                    directive_type=DirectiveType.WAIT_NEXT_TICK,
                    reason="Waiting for cooking cycle on Barbarian River Fire",
                )

            self._awaiting_cook_widget = True
            self._cook_object_clicked_tick = ctx.game_tick
            self._last_raw_fish_qty = raw_qty
            self._last_gather_action_tick = ctx.game_tick
            return TaskDirective(
                directive_type=DirectiveType.INTERACT_OBJECT,
                payload={"name": "Fire", "action": "Cook", "target_x": 3078, "target_y": 3445},
                reason=f"Interacting with permanent Fire to cook {raw_qty} raw Shrimps",
            )

        # Subphase 2B: Claim free net (if needed) & net Shrimps at Lumbridge Swamp
        fish_wp = WorldPoint(3243, 3152, 0, "LUMBRIDGE_SWAMP_FISHING")
        if ctx.position.chebyshev_distance(fish_wp) > 5:
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination="LUMBRIDGE_SWAMP_FISHING",
                reason="Routing safely to Lumbridge Swamp to net Shrimps for Stronghold survival",
            )

        if not self._has_small_net(ctx.inventory_items):
            if (ctx.game_tick - self._last_gather_action_tick) < 3:
                return TaskDirective(
                    directive_type=DirectiveType.WAIT_NEXT_TICK,
                    reason="Waiting for Fishing tutor net dialogue",
                )
            self._last_gather_action_tick = ctx.game_tick
            return TaskDirective(
                directive_type=DirectiveType.INTERACT_NPC,
                payload={"name": "Fishing tutor", "action": "Talk-to"},
                reason="Claiming free Small fishing net (303) from Lumbridge Fishing tutor",
            )

        if raw_qty > self._last_raw_fish_qty:
            # Active net-fishing animation catching fish — do not cancel!
            self._last_raw_fish_qty = raw_qty
            self._last_gather_action_tick = ctx.game_tick
            return TaskDirective(
                directive_type=DirectiveType.WAIT_NEXT_TICK,
                reason=f"Netting Shrimps ({raw_qty + edible_qty}/{desired_batch} gathered)",
            )

        if (ctx.game_tick - self._last_gather_action_tick) < 7:
            return TaskDirective(
                directive_type=DirectiveType.WAIT_NEXT_TICK,
                reason=f"Net fishing in progress ({raw_qty + edible_qty}/{desired_batch} gathered)",
            )

        self._last_raw_fish_qty = raw_qty
        self._last_gather_action_tick = ctx.game_tick
        return TaskDirective(
            directive_type=DirectiveType.INTERACT_NPC,
            payload={"name": "Fishing spot", "action": "Net"},
            reason=f"Netting Shrimps at Lumbridge Swamp ({raw_qty + edible_qty}/{desired_batch})",
        )

    def evaluate_tick(self, ctx: TickContext) -> TaskDirective:
        region = classify_stronghold_region(ctx.position)

        # 1. Active Dialogue Handler (100% local deterministic 39-rule Stronghold solver + surface tutor support)
        if ctx.dialogue_open:
            if ctx.dialogue_options:
                ans = self._solver.solve(ctx.dialogue_question, list(ctx.dialogue_options))
                if ans.matched and ans.selected_option is not None:
                    return TaskDirective(
                        directive_type=DirectiveType.SELECT_DIALOGUE,
                        payload={"option": ans.selected_option},
                        reason=f"Answered Stronghold security gate ({ans.rule_id})",
                    )
                if region == "SURFACE":
                    for opt in ctx.dialogue_options:
                        low = opt.casefold()
                        if "net" in low or "shrimp" in low or "fish" in low or "yes" in low:
                            return TaskDirective(
                                directive_type=DirectiveType.SELECT_DIALOGUE,
                                payload={"option": opt},
                                reason="Selecting surface Fishing tutor option",
                            )
                return TaskDirective(
                    directive_type=DirectiveType.ESCALATE_HUMAN,
                    reason="UNRECOGNIZED_SECURITY_QUESTION",
                )
            if region == "SURFACE" and self._staging_cook_phase:
                # Level-up chatbox banner interrupted Cook-All -> reset timer so cooking resumes immediately!
                self._awaiting_cook_widget = False
                self._last_gather_action_tick = -100
            return TaskDirective(
                directive_type=DirectiveType.CONTINUE_DIALOGUE,
                reason="Advance dialogue",
            )

        v1 = int(ctx.varbits.get(2309, 0))
        v2 = int(ctx.varbits.get(2310, 0))
        v3 = int(ctx.varbits.get(2311, 0))
        v4 = int(ctx.varbits.get(2312, 0))
        all_claimed = v1 == 1 and v2 == 1 and v3 == 1 and v4 == 1

        if region == "UNKNOWN_REGION":
            return TaskDirective(
                directive_type=DirectiveType.ESCALATE_HUMAN,
                reason=f"UNKNOWN_STRONGHOLD_REGION:({ctx.position.x},{ctx.position.y})",
            )

        # 2. Surface Logic: Pre-flight food staging -> Enter Barbarian Village hole -> Home & bank loot
        if region == "SURFACE":
            self._cradle_searched_this_visit = False
            self._pending_ladder_modal_floor = None
            if all_claimed:
                anchor_wp = (
                    WorldPoint(3164, 3487, 0, "GRAND_EXCHANGE")
                    if self.anchor_bank_node == "GRAND_EXCHANGE"
                    else WorldPoint(3185, 3436, 0, self.anchor_bank_node)
                )
                if self._has_unbanked_loot(ctx) or ctx.bank_is_open:
                    if ctx.position.chebyshev_distance(anchor_wp) > 4:
                        return TaskDirective(
                            directive_type=DirectiveType.NAVIGATE_TO,
                            destination=self.anchor_bank_node,
                            reason=f"All 4 Stronghold rewards claimed; homing safely to {self.anchor_bank_node}",
                        )
                    return TaskDirective(
                        directive_type=DirectiveType.BANK_TURNAROUND,
                        payload={"deposit_inventory": True, "withdraw": [], "close_when_done": True},
                        reason="Depositing Fancy boots and 10,000 GP at anchor bank",
                    )
                return TaskDirective(
                    directive_type=DirectiveType.COMPLETE_TASK,
                    reason="All 4 Stronghold floors claimed and loot safely banked",
                )

            if self.min_required_food > 0:
                edible_qty = self._count_edible_food(ctx.inventory_items)
                raw_pending = self._count_raw_swamp_fish(ctx.inventory_items)
                still_cooking_batch = (
                    self._staging_cook_phase and raw_pending > 0 and edible_qty < self.target_food_count
                )
                if edible_qty < self.min_required_food or still_cooking_batch:
                    return self._stage_preflight_food(ctx, edible_qty)
                if ctx.bank_is_open:
                    return TaskDirective(
                        directive_type=DirectiveType.BANK_TURNAROUND,
                        payload={"deposit_inventory": False, "withdraw": [], "close_when_done": True},
                        reason=f"Pre-flight food staged ({edible_qty} items); closing bank to depart",
                    )

            barb_wp = WorldPoint(3081, 3421, 0, "BARBARIAN_VILLAGE")
            if ctx.position.chebyshev_distance(barb_wp) > 2:
                return TaskDirective(
                    directive_type=DirectiveType.NAVIGATE_TO,
                    destination="BARBARIAN_VILLAGE",
                    reason="Navigate along capability-safe corridor to Stronghold entrance",
                )
            return TaskDirective(
                directive_type=DirectiveType.INTERACT_OBJECT,
                payload={"id": ENTRANCE_ROCK_HOLE_ID, "action": "Climb-down"},
                reason="Enter Stronghold Floor 1 (Vault of War)",
            )

        # 3. Floor 1 — Vault of War (Varbit 2309)
        if region == "FLOOR_1":
            px, py = ctx.position.x, ctx.position.y
            if v1 == 0:
                if 1855 <= px <= 1867 and py >= 5232:
                    return self._walk_or_pass_door(ctx.position, WorldPoint(1859, 5232, 0), "Gate of War", "Floor 1 [1/4]")
                if 1855 <= px <= 1872 and 5220 <= py <= 5231:
                    return self._walk_or_pass_door(ctx.position, WorldPoint(1870, 5227, 0), "Gate of War", "Floor 1 [2/4]")
                if 1873 <= px <= 1890 and py >= 5235:
                    return self._walk_or_pass_door(ctx.position, WorldPoint(1883, 5243, 0), "Gate of War", "Floor 1 [3/4]")
                if 1891 <= px <= 1925 and py >= 5233:
                    return self._walk_or_pass_door(ctx.position, WorldPoint(1905, 5234, 0), "Gate of War", "Floor 1 [4/4]")
                return self._claim_floor_object(
                    floor_num=1,
                    pos=ctx.position,
                    stand=WorldPoint(1906, 5222, 0),
                    object_id=FLOOR_1_REWARD_CHEST_ID,
                    action="Open",
                    label="Gift of Peace (2,000 GP)",
                )

            if all_claimed:
                return TaskDirective(
                    directive_type=DirectiveType.AUTHORIZE_SURFACE_EXIT,
                    payload={"id": FLOOR_1_LADDER_UP_ID, "action": "Climb-up"},
                    reason="All rewards claimed; exiting Floor 1 to surface",
                )
            if px < 1880:
                return TaskDirective(
                    directive_type=DirectiveType.INTERACT_OBJECT,
                    payload={"id": FLOOR_1_SHORTCUT_PORTAL_ID, "action": "Use"},
                    reason="Floor 1 reward already claimed; taking shortcut portal 20786",
                )
            return self._descend_ladder_with_modal_support(
                pos=ctx.position,
                ladder_stand=WorldPoint(1903, 5222, 0),
                ladder_id=FLOOR_1_LADDER_DOWN_ID,
                floor_num=1,
            )

        # 4. Floor 2 — Catacomb of Famine (Varbit 2310)
        if region == "FLOOR_2":
            if v1 == 0:
                return TaskDirective(
                    directive_type=DirectiveType.ESCALATE_HUMAN,
                    reason="OUT_OF_ORDER_FLOOR_2_MISSING_FLOOR_1_VARBIT_2309",
                )
            px, py = ctx.position.x, ctx.position.y
            if v2 == 0:
                if 2035 <= px <= 2050 and 5238 <= py <= 5250:
                    return self._walk_or_pass_door(ctx.position, WorldPoint(2034, 5244, 0), "Rickety door", "Floor 2 [1/4]")
                if 2027 <= px <= 2036 and 5230 <= py <= 5240:
                    return self._walk_or_pass_door(ctx.position, WorldPoint(2027, 5236, 0), "Rickety door", "Floor 2 [2/4]")
                if 2016 <= px <= 2026 and 5237 <= y_val(py) <= 5250:
                    return self._walk_or_pass_door(ctx.position, WorldPoint(2019, 5237, 0), "Rickety door", "Floor 2 [3/4]")
                if 2000 <= px <= 2016 and 5225 <= py <= 5250:
                    return self._walk_or_pass_door(ctx.position, WorldPoint(2014, 5228, 0), "Rickety door", "Floor 2 [4/4]")
                return self._claim_floor_object(
                    floor_num=2,
                    pos=ctx.position,
                    stand=WorldPoint(2021, 5218, 0),
                    object_id=FLOOR_2_REWARD_SACK_ID,
                    action="Search",
                    label="Grain of Plenty (3,000 GP)",
                )

            if px >= 2035 and py >= 5238:
                return TaskDirective(
                    directive_type=DirectiveType.INTERACT_OBJECT,
                    payload={"id": FLOOR_2_SHORTCUT_PORTAL_ID, "action": "Use"},
                    reason="Floor 2 reward already claimed; taking shortcut portal 19005",
                )
            return self._descend_ladder_with_modal_support(
                pos=ctx.position,
                ladder_stand=WorldPoint(2026, 5219, 0),
                ladder_id=FLOOR_2_LADDER_DOWN_ID,
                floor_num=2,
            )

        # 5. Floor 3 — Pit of Pestilence (Varbit 2311)
        if region == "FLOOR_3":
            if v1 == 0 or v2 == 0:
                return TaskDirective(
                    directive_type=DirectiveType.ESCALATE_HUMAN,
                    reason="OUT_OF_ORDER_FLOOR_3_MISSING_PRIOR_VARBITS",
                )
            px, py = ctx.position.x, ctx.position.y
            if v3 == 0:
                if px < 2138 and py <= 5258:
                    return self._walk_or_pass_door(ctx.position, WorldPoint(2132, 5258, 0), "Oozing barrier", "Floor 3 [1/3]")
                if px < 2138 and 5259 <= py <= 5260:
                    return self._walk_or_pass_door(ctx.position, WorldPoint(2132, 5260, 0), "Oozing barrier", "Floor 3 [2/3]")
                if px < 2138 and 5261 <= py <= 5277:
                    return self._walk_or_pass_door(ctx.position, WorldPoint(2132, 5277, 0), "Oozing barrier", "Floor 3 [3/3]")
                return self._claim_floor_object(
                    floor_num=3,
                    pos=ctx.position,
                    stand=WorldPoint(2144, 5281, 0),
                    object_id=FLOOR_3_REWARD_CHEST_ID,
                    action="Open",
                    label="Box of Health (5,000 GP)",
                )

            if px < 2135 and py < 5265:
                return TaskDirective(
                    directive_type=DirectiveType.INTERACT_OBJECT,
                    payload={"id": FLOOR_3_SHORTCUT_PORTAL_ID, "action": "Use"},
                    reason="Floor 3 reward already claimed; taking shortcut portal 23707",
                )
            return self._descend_ladder_with_modal_support(
                pos=ctx.position,
                ladder_stand=WorldPoint(2147, 5284, 0),
                ladder_id=FLOOR_3_LADDER_DOWN_ID,
                floor_num=3,
            )

        # 6. Floor 4 — Sepulchre of Death (Varbit 2312 + Fancy Boots 270:15 + Bone Chain 23732)
        if v1 == 0 or v2 == 0 or v3 == 0:
            return TaskDirective(
                directive_type=DirectiveType.ESCALATE_HUMAN,
                reason="OUT_OF_ORDER_FLOOR_4_MISSING_PRIOR_VARBITS",
            )
        px, py = ctx.position.x, ctx.position.y
        if v4 == 0:
            if px >= 2352 and py <= 5231:
                return self._walk_or_pass_door(ctx.position, WorldPoint(2359, 5230, 0), "Portal of Death", "Floor 4 [1/4]")
            if px >= 2352 and 5232 <= py <= 5234:
                return self._walk_or_pass_door(ctx.position, WorldPoint(2360, 5234, 0), "Portal of Death", "Floor 4 [2/4]", max_dist=1)
            if py >= 5235 or (px < 2352 and py >= 5226):
                return self._walk_or_pass_door(ctx.position, WorldPoint(2341, 5226, 0), "Portal of Death", "Floor 4 [3/4]")
            if px < 2352 and 5221 <= py <= 5225:
                return self._walk_or_pass_door(ctx.position, WorldPoint(2341, 5222, 0), "Portal of Death", "Floor 4 [4/4]")

            cradle_stand = WorldPoint(2343, 5215, 0)
            if ctx.position.chebyshev_distance(cradle_stand) > 2:
                return TaskDirective(
                    directive_type=DirectiveType.NAVIGATE_TO,
                    destination=cradle_stand,
                    reason="Floor 4: walking to Cradle of Life at (2343, 5215)",
                )

            attempts = self._claim_attempts_by_floor.get(4, 0) + 1
            self._claim_attempts_by_floor[4] = attempts
            if attempts > self.max_claim_attempts:
                return TaskDirective(
                    directive_type=DirectiveType.ESCALATE_HUMAN,
                    reason="FLOOR_4_CRADLE_CLAIM_BLOCKED_CHECK_2FA",
                )

            if not self._cradle_searched_this_visit:
                self._cradle_searched_this_visit = True
                return TaskDirective(
                    directive_type=DirectiveType.INTERACT_OBJECT,
                    payload={"id": FLOOR_4_CRADLE_ID, "action": "Search"},
                    reason="Floor 4: searching Cradle of Life (23731)",
                )

            self._cradle_searched_this_visit = False
            return TaskDirective(
                directive_type=DirectiveType.CLICK_WIDGET,
                payload=dict(FANCY_BOOTS_WIDGET),
                reason="Floor 4: clicking Fancy boots widget (270:15)",
            )

        # v4 == 1 (all 4 rewards claimed!) -> take shortcut 23922 if at entry, else climb Bone Chain (23732)
        if px >= 2352:
            return TaskDirective(
                directive_type=DirectiveType.INTERACT_OBJECT,
                payload={"id": FLOOR_4_SHORTCUT_PORTAL_ID, "action": "Use"},
                reason="Floor 4 reward already claimed; taking shortcut portal 23922",
            )
        chain_stand = WorldPoint(2349, 5215, 0)
        if ctx.position.chebyshev_distance(chain_stand) > 2:
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination=chain_stand,
                reason="Floor 4 complete: walking to Bone Chain exit at (2349, 5215)",
            )
        return TaskDirective(
            directive_type=DirectiveType.AUTHORIZE_SURFACE_EXIT,
            payload={"id": FLOOR_4_BONE_CHAIN_UP_ID, "action": "Climb-up"},
            reason="All 4 Stronghold floors claimed; climbing Bone Chain (23732) to surface",
        )


def y_val(v: int) -> int:
    return v
