"""Stage 1B Seasoning & Total Level 100 Rotation Module (`osrs_ee/bots/seasoning_module.py` — PR-13).

Field-Verified Mechanics (`TotalLevelScript.java` + `AUD-18 / T11`):
- Satisfies BOTH remaining OSRS F2P trade-unlock gates safely without ANY Grand Exchange selling:
  1. `total_level >= target_total_level` (default `100`)
  2. `playtime_minutes >= target_playtime_minutes` (default `1200` minutes = `20h` in-game seasoning,
     or `0` when running a pure Total-100 sprint)
- Alternates non-combat F2P skill batches (`WOODCUTTING` at safe `LUMBRIDGE_WEST_TREES` `(3190, 3245, 0)`
  <-> `FISHING_COOKING` at `LUMBRIDGE_SWAMP_FISHING` `(3243, 3152, 0)` & `LUMBRIDGE_KITCHEN` range `(3206, 3214, 0)`),
  banking yields at `VARROCK_WEST_BANK` / `GRAND_EXCHANGE` and rotating every completed inventory batch!
- Immediate Game-Tick Latch: the instant `total_level` and `playtime_minutes` meet target thresholds,
  routes immediately to the bank anchor, deposits items, and emits `COMPLETE_TASK`.
"""

from __future__ import annotations

from typing import Any, Mapping

from osrs_ee.bots.base import DirectiveType, TaskDirective, TaskModule, TickContext
from osrs_ee.navigation.waypoint_graph import WorldPoint

AXE_IDS: frozenset[int] = frozenset({1351, 1349, 1353, 1361, 1355, 1357, 1359})  # Bronze..Rune axe
SMALL_FISHING_NET_ID = 303
RAW_FISH_NAMES: frozenset[str] = frozenset({"raw shrimps", "raw anchovies"})


def _has_any_axe(items: tuple[Mapping[str, Any], ...]) -> bool:
    for it in items:
        if int(it.get("item_id", -1)) in AXE_IDS or "axe" in str(it.get("name", "")).casefold():
            return True
    return False


def _has_small_net(items: tuple[Mapping[str, Any], ...]) -> bool:
    for it in items:
        if int(it.get("item_id", -1)) == SMALL_FISHING_NET_ID or str(it.get("name", "")).strip().casefold() == "small fishing net":
            return True
    return False


def _has_raw_swamp_fish(items: tuple[Mapping[str, Any], ...]) -> bool:
    return any(str(it.get("name", "")).strip().casefold() in RAW_FISH_NAMES for it in items)


class TotalLevel100TaskModule(TaskModule):
    """Pure tick-driven F2P Skill Rotation & 20-Hour Bank-Only Seasoning Module (`stage1b_seasoning_gatherer`)."""

    def __init__(
        self,
        *,
        target_total_level: int = 100,
        target_playtime_minutes: int = 1200,
        anchor_bank_node: str = "VARROCK_WEST_BANK",
        cook_caught_fish: bool = True,
    ) -> None:
        self.target_total_level = max(32, int(target_total_level))
        self.target_playtime_minutes = max(0, int(target_playtime_minutes))
        self.anchor_bank_node = anchor_bank_node
        self.cook_caught_fish = cook_caught_fish
        self._active_block: str = "WOODCUTTING"  # Rotates WOODCUTTING <-> FISHING_COOKING on each bank batch
        self._was_banking_batch: bool = False
        self.completed_rotation_batches: int = 0

    @property
    def module_id(self) -> str:
        return "stage1b_seasoning_gatherer"

    @property
    def active_block(self) -> str:
        return self._active_block

    def _anchor_wp(self) -> WorldPoint:
        if self.anchor_bank_node == "GRAND_EXCHANGE":
            return WorldPoint(3164, 3487, 0, "GRAND_EXCHANGE")
        return WorldPoint(3185, 3436, 0, self.anchor_bank_node)

    def evaluate_tick(self, ctx: TickContext) -> TaskDirective:
        if ctx.dialogue_open:
            return TaskDirective(
                directive_type=DirectiveType.CONTINUE_DIALOGUE,
                reason="Dismissing level-up dialogue during skill rotation",
            )

        # 1. Immediate Game-Tick Latch (`Total >= target_total_level` AND `playtime >= target_playtime_minutes`)
        goal_reached = (
            ctx.total_level >= self.target_total_level
            and ctx.playtime_minutes >= self.target_playtime_minutes
        )
        anchor_wp = self._anchor_wp()
        if goal_reached:
            if ctx.position.chebyshev_distance(anchor_wp) > 4:
                return TaskDirective(
                    directive_type=DirectiveType.NAVIGATE_TO,
                    destination=self.anchor_bank_node,
                    reason=(
                        f"Seasoning goal latched (total={ctx.total_level}, playtime={ctx.playtime_minutes}m); "
                        f"homing to {self.anchor_bank_node}"
                    ),
                )
            if ctx.inventory_items or ctx.bank_is_open:
                return TaskDirective(
                    directive_type=DirectiveType.BANK_TURNAROUND,
                    payload={"deposit_inventory": True, "withdraw": [], "close_when_done": True},
                    reason="Depositing final seasoning batch (Bank-Only, Zero GE)",
                )
            return TaskDirective(
                directive_type=DirectiveType.COMPLETE_TASK,
                reason=f"Stage 1B completed: total_level={ctx.total_level}, playtime_minutes={ctx.playtime_minutes}",
            )

        inv_full = len(ctx.inventory_items) >= 28

        # 2. Full-inventory batch transition: Cook raw fish first (if in FISHING_COOKING block), else bank & rotate!
        if self._active_block == "FISHING_COOKING" and inv_full and self.cook_caught_fish and _has_raw_swamp_fish(ctx.inventory_items):
            kitchen_wp = WorldPoint(3206, 3214, 0, "LUMBRIDGE_KITCHEN")
            if ctx.position.chebyshev_distance(kitchen_wp) > 4:
                return TaskDirective(
                    directive_type=DirectiveType.NAVIGATE_TO,
                    destination="LUMBRIDGE_KITCHEN",
                    reason="Inventory full of raw fish; routing safely to Lumbridge Castle Range",
                )
            return TaskDirective(
                directive_type=DirectiveType.INTERACT_OBJECT,
                payload={"name": "Cooking range", "action": "Cook"},
                reason="Cooking fish batch on Lumbridge Castle Range",
            )

        needs_axe = self._active_block == "WOODCUTTING" and not _has_any_axe(ctx.inventory_items)
        needs_net = self._active_block == "FISHING_COOKING" and not _has_small_net(ctx.inventory_items)

        if inv_full or needs_axe or needs_net or ctx.bank_is_open:
            # Determine next block & tool BEFORE opening bank when depositing a full batch
            if inv_full and not self._was_banking_batch:
                self._was_banking_batch = True
                self.completed_rotation_batches += 1
                self._active_block = (
                    "FISHING_COOKING" if self._active_block == "WOODCUTTING" else "WOODCUTTING"
                )

            if ctx.position.chebyshev_distance(anchor_wp) > 4:
                return TaskDirective(
                    directive_type=DirectiveType.NAVIGATE_TO,
                    destination=self.anchor_bank_node,
                    reason=f"Routing to {self.anchor_bank_node} for bank turnaround ({self._active_block})",
                )

            tool_withdraw: list[dict[str, Any]] = (
                [{"item_id": 1351, "name": "Bronze axe", "quantity": 1}]
                if self._active_block == "WOODCUTTING"
                else [{"item_id": SMALL_FISHING_NET_ID, "name": "Small fishing net", "quantity": 1}]
            )
            return TaskDirective(
                directive_type=DirectiveType.BANK_TURNAROUND,
                payload={"deposit_inventory": True, "withdraw": tool_withdraw, "close_when_done": True},
                reason=f"Bank-Only turnaround & tool prep for {self._active_block}",
            )

        self._was_banking_batch = False

        # 3. Execute Active Non-Combat Skill Block
        if self._active_block == "WOODCUTTING":
            trees_wp = WorldPoint(3190, 3245, 0, "LUMBRIDGE_WEST_TREES")
            if ctx.position.chebyshev_distance(trees_wp) > 5:
                return TaskDirective(
                    directive_type=DirectiveType.NAVIGATE_TO,
                    destination="LUMBRIDGE_WEST_TREES",
                    reason="Routing safely to Lumbridge West Trees",
                )
            wc_lvl = ctx.capabilities.skill_level("woodcutting")
            tree_name = "Oak tree" if wc_lvl >= 15 else "Tree"
            return TaskDirective(
                directive_type=DirectiveType.INTERACT_OBJECT,
                payload={"name": tree_name, "action": "Chop down"},
                reason=f"Chopping {tree_name} (WC={wc_lvl})",
            )

        fish_wp = WorldPoint(3243, 3152, 0, "LUMBRIDGE_SWAMP_FISHING")
        if ctx.position.chebyshev_distance(fish_wp) > 5:
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination="LUMBRIDGE_SWAMP_FISHING",
                reason="Routing safely to Lumbridge Swamp Fishing Spot",
            )
        return TaskDirective(
            directive_type=DirectiveType.INTERACT_NPC,
            payload={"name": "Fishing spot", "action": "Net"},
            reason="Small-net fishing Shrimps/Anchovies at Lumbridge Swamp",
        )
