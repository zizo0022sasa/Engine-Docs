"""Decoupled F2P Gatherer/Woodcutting Task Module (`bots/` — zero transport/safety coupling, T36)."""

from __future__ import annotations

from osrs_ee.bots.base import DirectiveType, TaskDirective, TaskModule, TickContext
from osrs_ee.navigation.waypoint_graph import WorldPoint


class SkillingGathererTaskModule(TaskModule):
    """Pure tick-driven gatherer module proving swappable `TaskModule` architecture."""

    def __init__(
        self,
        *,
        recipe_id: str = "f2p_draynor_willow",
        gather_node_name: str = "DRAYNOR_BANK",
        bank_node_name: str = "DRAYNOR_BANK",
        target_object_name: str = "Willow tree",
        target_action: str = "Chop down",
    ) -> None:
        self._recipe_id = recipe_id
        self.gather_node_name = gather_node_name
        self.bank_node_name = bank_node_name
        self.target_object_name = target_object_name
        self.target_action = target_action

    @property
    def module_id(self) -> str:
        return self._recipe_id

    def evaluate_tick(self, ctx: TickContext) -> TaskDirective:
        # Full inventory (28 items) -> navigate safely to bank!
        if len(ctx.inventory_items) >= 28:
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination=self.bank_node_name,
                reason="Inventory full; routing to bank via safe corridor",
            )

        draynor_spot = WorldPoint(3092, 3245, 0, self.gather_node_name)
        if ctx.position.chebyshev_distance(draynor_spot) > 5:
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination=self.gather_node_name,
                reason="Routing to gathering spot via safe corridor",
            )

        return TaskDirective(
            directive_type=DirectiveType.INTERACT_OBJECT,
            payload={"name": self.target_object_name, "action": self.target_action},
            reason=f"Interact with {self.target_object_name}",
        )
