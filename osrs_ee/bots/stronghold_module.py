"""Complete 4-Floor Stronghold of Security Task Module (`osrs_ee/bots/stronghold_module.py` — PR-13).

Field-Verified OSRS Mechanics & Coordinates (`run_stronghold_bot.py` + `StrongholdScript.java`):
- Pure tick-driven `TaskModule` with ZERO transport, socket, sleep, or safety imports (`ADR-012`).
- Surface Anchor: `GRAND_EXCHANGE` `(3164, 3487, 0)` <-> Entrance `BARBARIAN_VILLAGE` `(3081, 3421, 0)` (`id=20790`).
- All 4 underground floors reside on `plane=0` (`y >= 5000`) inside distinct region bounding boxes:
  1. Floor 1 (*Vault of War*, `1855..1935, 5180..5250`, `Varbit 2309`):
     - 4 `Gate of War` stages -> *Gift of Peace* (`id=20656`, `Open`, `2,000 GP`) -> Shortcut `20786` / Ladder `20785`.
  2. Floor 2 (*Catacomb of Famine*, `1980..2060, 5180..5250`, `Varbit 2310`):
     - 4 `Rickety door` stages -> *Grain of Plenty* (`id=19000`, `Search`, `3,000 GP`) -> Shortcut `19005` / Ladder `19004`.
  3. Floor 3 (*Pit of Pestilence*, `2110..2185, 5240..5315`, `Varbit 2311`):
     - 3 `Oozing barrier` stages -> *Box of Health* (`id=23709`, `Open`, `5,000 GP`) -> Ladder `23706`.
  4. Floor 4 (*Sepulchre of Death*, `2300..2380, 5180..5260`, `Varbit 2312`):
     - 4 `Portal of Death` stages -> *Cradle of Life* (`id=23731`, `Search`) -> Widget `270:15` (*Fancy boots*)
     - Bone Chain exit (`id=23732`, `Climb-up` at `(2349, 5215, 0)`) via `AUTHORIZE_SURFACE_EXIT`.
- Post-Completion Homing: Routes back to `GRAND_EXCHANGE`, deposits `Fancy boots` (`9005`) + `Coins` (`995`),
  closes bank, and emits `COMPLETE_TASK`.
- Bounded Claim / Modal Guards: Dismisses ladder warning modal (`579:17`) when needed and escalates cleanly via
  `ESCALATE_HUMAN` if reward claiming is blocked (e.g., missing OSRS 2FA Authenticator) or out-of-order floor state.
"""

from __future__ import annotations

from osrs_ee.ai.dialogue_solver import DeterministicStrongholdSolver
from osrs_ee.bots.base import DirectiveType, TaskDirective, TaskModule, TickContext
from osrs_ee.navigation.waypoint_graph import WorldPoint

STRONGHOLD_REWARD_VARBITS: tuple[int, int, int, int] = (2309, 2310, 2311, 2312)

# Verified Object & Widget Identifiers
ENTRANCE_ROCK_HOLE_ID = 20790
LADDER_WARNING_WIDGET = {"groupId": 579, "childId": 17}
FANCY_BOOTS_WIDGET = {"groupId": 270, "childId": 15}
FANCY_BOOTS_ITEM_ID = 9005
COINS_ITEM_ID = 995

FLOOR_1_REWARD_CHEST_ID = 20656     # Gift of Peace (Open)
FLOOR_1_SHORTCUT_PORTAL_ID = 20786  # Use
FLOOR_1_LADDER_DOWN_ID = 20785      # Climb-down
FLOOR_1_LADDER_UP_ID = 20784        # Climb-up (emergency/early exit)

FLOOR_2_REWARD_SACK_ID = 19000      # Grain of Plenty (Search)
FLOOR_2_SHORTCUT_PORTAL_ID = 19005  # Use
FLOOR_2_LADDER_DOWN_ID = 19004      # Climb-down

FLOOR_3_REWARD_CHEST_ID = 23709     # Box of Health (Open)
FLOOR_3_LADDER_DOWN_ID = 23706      # Climb-down

FLOOR_4_CRADLE_ID = 23731           # Cradle of Life (Search)
FLOOR_4_BONE_CHAIN_UP_ID = 23732    # Bone Chain exit to Surface (Climb-up)


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
    ) -> None:
        self._solver = DeterministicStrongholdSolver()
        self.anchor_bank_node = anchor_bank_node
        self.max_claim_attempts = max(2, int(max_claim_attempts))
        self._claim_attempts_by_floor: dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}
        self._cradle_searched_this_visit: bool = False
        self._pending_ladder_modal_floor: int | None = None

    @property
    def module_id(self) -> str:
        return "stage2_stronghold_10k"

    @property
    def watched_varbits(self) -> tuple[int, ...]:
        return STRONGHOLD_REWARD_VARBITS

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
    def _walk_or_pass_door(pos: WorldPoint, door_stand: WorldPoint, door_name: str, stage_label: str) -> TaskDirective:
        if pos.chebyshev_distance(door_stand) > 1:
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination=door_stand,
                reason=f"{stage_label}: walking to {door_name} at ({door_stand.x}, {door_stand.y})",
            )
        return TaskDirective(
            directive_type=DirectiveType.INTERACT_OBJECT,
            payload={"name": door_name, "action": "Pass-through"},
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
        if pos.chebyshev_distance(ladder_stand) > 1:
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
        if pos.chebyshev_distance(stand) > 1:
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

    def evaluate_tick(self, ctx: TickContext) -> TaskDirective:
        # 1. Active Dialogue Handler (100% local deterministic 39-rule Stronghold solver)
        if ctx.dialogue_open:
            if ctx.dialogue_options:
                ans = self._solver.solve(ctx.dialogue_question, list(ctx.dialogue_options))
                if not ans.matched or ans.selected_option is None:
                    return TaskDirective(
                        directive_type=DirectiveType.ESCALATE_HUMAN,
                        reason="UNRECOGNIZED_SECURITY_QUESTION",
                    )
                return TaskDirective(
                    directive_type=DirectiveType.SELECT_DIALOGUE,
                    payload={"option": ans.selected_option},
                    reason=f"Answered Stronghold security gate ({ans.rule_id})",
                )
            return TaskDirective(
                directive_type=DirectiveType.CONTINUE_DIALOGUE,
                reason="Advance Stronghold dialogue",
            )

        v1 = int(ctx.varbits.get(2309, 0))
        v2 = int(ctx.varbits.get(2310, 0))
        v3 = int(ctx.varbits.get(2311, 0))
        v4 = int(ctx.varbits.get(2312, 0))
        all_claimed = v1 == 1 and v2 == 1 and v3 == 1 and v4 == 1

        region = classify_stronghold_region(ctx.position)
        if region == "UNKNOWN_REGION":
            return TaskDirective(
                directive_type=DirectiveType.ESCALATE_HUMAN,
                reason=f"UNKNOWN_STRONGHOLD_REGION:({ctx.position.x},{ctx.position.y})",
            )

        # 2. Surface Logic: Enter Barbarian Village hole OR home to Grand Exchange Bank & deposit loot
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
            if px >= 2352 and py >= 5232:
                return self._walk_or_pass_door(ctx.position, WorldPoint(2359, 5234, 0), "Portal of Death", "Floor 4 [2/4]")
            if px < 2352 and py >= 5226:
                return self._walk_or_pass_door(ctx.position, WorldPoint(2341, 5226, 0), "Portal of Death", "Floor 4 [3/4]")
            if px < 2352 and 5221 <= py <= 5225:
                return self._walk_or_pass_door(ctx.position, WorldPoint(2341, 5222, 0), "Portal of Death", "Floor 4 [4/4]")

            cradle_stand = WorldPoint(2343, 5215, 0)
            if ctx.position.chebyshev_distance(cradle_stand) > 1:
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

        # v4 == 1 (all 4 rewards claimed!) -> climb up Bone Chain (23732) at (2349, 5215, 0)
        chain_stand = WorldPoint(2349, 5215, 0)
        if ctx.position.chebyshev_distance(chain_stand) > 1:
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
