"""Decoupled Stage-3 F2P Gatherer Modules (`osrs_ee/bots/woodcutting_module.py` — PR-12 + PR-13).

Supports all 3 Stage-3 F2P Skilling recipes (`f2p_draynor_willow`, `f2p_barbarian_fly_fishing`,
`f2p_alkharid_mining`) with:
- Full multi-cycle loop (`Tool Prep -> Safe Corridor to Spot -> Gather Object/NPC -> Safe Corridor to Bank ->
  BANK_TURNAROUND Deposit & Tool Retention -> Next Cycle`).
- Optional `max_completed_cycles` (e.g. `3` cycles for Bolla's 3-Cycle Smoke-Test Protocol!).
- Backward-compatible default constructor (`SkillingGathererTaskModule()`) for hot-swap tests (`T36`).
"""

from __future__ import annotations

from typing import Any, Mapping

from osrs_ee.bots.base import DirectiveType, TaskDirective, TaskModule, TickContext
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint


def _has_required_tools(
    items: tuple[Mapping[str, Any], ...],
    required_tools: tuple[dict[str, Any], ...],
) -> bool:
    for req in required_tools:
        req_id = int(req["item_id"]) if "item_id" in req else None
        req_name = str(req.get("name", "")).strip().casefold()
        req_qty = int(req.get("quantity", 1))
        found = sum(
            int(it.get("quantity", 0))
            for it in items
            if (req_id is not None and int(it.get("item_id", -1)) == req_id)
            or (req_name and str(it.get("name", "")).strip().casefold() == req_name)
        )
        if found < req_qty:
            return False
    return True


class SkillingGathererTaskModule(TaskModule):
    """Pure tick-driven F2P gatherer supporting Woodcutting, Fly Fishing, and Mining cycles."""

    def __init__(
        self,
        *,
        recipe_id: str = "f2p_draynor_willow",
        gather_node_name: str = "DRAYNOR_BANK",
        bank_node_name: str = "DRAYNOR_BANK",
        target_object_name: str = "Willow tree",
        target_action: str = "Chop down",
        target_is_npc: bool = False,
        required_tools: tuple[dict[str, Any], ...] = (),
        max_completed_cycles: int | None = None,
    ) -> None:
        self._recipe_id = recipe_id
        self.gather_node_name = gather_node_name
        self.bank_node_name = bank_node_name
        self.target_object_name = target_object_name
        self.target_action = target_action
        self.target_is_npc = target_is_npc
        self.required_tools = required_tools
        self.max_completed_cycles = max_completed_cycles
        self.completed_cycles: int = 0
        self._depositing_full_load: bool = False

    @classmethod
    def draynor_willow(
        cls,
        *,
        require_axe: bool = True,
        max_completed_cycles: int | None = None,
    ) -> SkillingGathererTaskModule:
        tools = ({"item_id": 1351, "name": "Bronze axe", "quantity": 1},) if require_axe else ()
        return cls(
            recipe_id="f2p_draynor_willow",
            gather_node_name="DRAYNOR_BANK",
            bank_node_name="DRAYNOR_BANK",
            target_object_name="Willow tree",
            target_action="Chop down",
            target_is_npc=False,
            required_tools=tools,
            max_completed_cycles=max_completed_cycles,
        )

    @classmethod
    def barbarian_fly_fishing(
        cls,
        *,
        max_completed_cycles: int | None = None,
    ) -> SkillingGathererTaskModule:
        return cls(
            recipe_id="f2p_barbarian_fly_fishing",
            gather_node_name="BARBARIAN_VILLAGE",
            bank_node_name="VARROCK_WEST_BANK",
            target_object_name="Rod Fishing spot",
            target_action="Lure",
            target_is_npc=True,
            required_tools=(
                {"item_id": 309, "name": "Fly fishing rod", "quantity": 1},
                {"item_id": 314, "name": "Feather", "quantity": 50},
            ),
            max_completed_cycles=max_completed_cycles,
        )

    @classmethod
    def alkharid_mining(
        cls,
        *,
        max_completed_cycles: int | None = None,
    ) -> SkillingGathererTaskModule:
        return cls(
            recipe_id="f2p_alkharid_mining",
            gather_node_name="AL_KHARID_MINE",
            bank_node_name="AL_KHARID_BANK",
            target_object_name="Iron rocks",
            target_action="Mine",
            target_is_npc=False,
            required_tools=({"item_id": 1265, "name": "Bronze pickaxe", "quantity": 1},),
            max_completed_cycles=max_completed_cycles,
        )

    @property
    def module_id(self) -> str:
        return self._recipe_id

    def evaluate_tick(self, ctx: TickContext) -> TaskDirective:
        if ctx.dialogue_open:
            return TaskDirective(
                directive_type=DirectiveType.CONTINUE_DIALOGUE,
                reason="Dismissing skill level-up dialogue",
            )

        bank_wp = DEFAULT_WORLD_NODES.get(self.bank_node_name, WorldPoint(3092, 3245, 0, self.bank_node_name))
        gather_wp = DEFAULT_WORLD_NODES.get(self.gather_node_name, WorldPoint(3092, 3245, 0, self.gather_node_name))

        inv_full = len(ctx.inventory_items) >= 28
        missing_tools = bool(self.required_tools) and not _has_required_tools(ctx.inventory_items, self.required_tools)

        # Count cycle completion when full load has been deposited and bank closed!
        if self._depositing_full_load and not inv_full and not missing_tools and not ctx.bank_is_open:
            self._depositing_full_load = False
            self.completed_cycles += 1

        if self.max_completed_cycles is not None and self.completed_cycles >= self.max_completed_cycles:
            if ctx.position.chebyshev_distance(bank_wp) > 4:
                return TaskDirective(
                    directive_type=DirectiveType.NAVIGATE_TO,
                    destination=self.bank_node_name,
                    reason=f"Target {self.completed_cycles} cycles reached; homing to {self.bank_node_name}",
                )
            return TaskDirective(
                directive_type=DirectiveType.COMPLETE_TASK,
                reason=f"Completed {self.completed_cycles} verified gathering & banking cycles ({self._recipe_id})",
            )

        # 1. Bank Turnaround when inventory is full, required tools are missing, or bank is still open
        if inv_full or missing_tools or ctx.bank_is_open:
            if inv_full:
                self._depositing_full_load = True
            if ctx.position.chebyshev_distance(bank_wp) > 4:
                return TaskDirective(
                    directive_type=DirectiveType.NAVIGATE_TO,
                    destination=self.bank_node_name,
                    reason=f"Routing to {self.bank_node_name} via capability-safe corridor",
                )
            return TaskDirective(
                directive_type=DirectiveType.BANK_TURNAROUND,
                payload={
                    "deposit_inventory": True,
                    "withdraw": list(self.required_tools),
                    "close_when_done": True,
                },
                reason=f"Executing bank turnaround at {self.bank_node_name}",
            )

        # 2. Navigate safely to gathering spot
        if ctx.position.chebyshev_distance(gather_wp) > 5:
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination=self.gather_node_name,
                reason=f"Routing to {self.gather_node_name} via safe corridor",
            )

        # 3. Gather resource (Object or NPC)
        if self.target_is_npc:
            return TaskDirective(
                directive_type=DirectiveType.INTERACT_NPC,
                payload={"name": self.target_object_name, "action": self.target_action},
                reason=f"Interacting with NPC {self.target_object_name} ({self.target_action})",
            )

        target_name = self.target_object_name
        if self._recipe_id == "f2p_alkharid_mining" and ctx.capabilities.skill_level("mining") < 15:
            target_name = "Copper rocks"

        return TaskDirective(
            directive_type=DirectiveType.INTERACT_OBJECT,
            payload={"name": target_name, "action": self.target_action},
            reason=f"Interacting with {target_name} ({self.target_action})",
        )
