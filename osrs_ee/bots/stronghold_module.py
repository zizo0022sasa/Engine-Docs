"""Decoupled Stronghold of Security Task Module (`bots/` — zero transport/safety coupling, T36)."""

from __future__ import annotations

from osrs_ee.ai.dialogue_solver import DeterministicStrongholdSolver
from osrs_ee.bots.base import DirectiveType, TaskDirective, TaskModule, TickContext
from osrs_ee.navigation.waypoint_graph import WorldPoint

STRONGHOLD_ENTRANCE_SURFACE = "BARBARIAN_VILLAGE"
STRONGHOLD_ENTRANCE_OBJECT_ID = 20790
FLOOR_1_LADDER_UP_OBJECT_ID = 20784


class StrongholdTaskModule(TaskModule):
    """Pure tick-driven Stronghold module delegating routing & survival entirely to Core Infrastructure."""

    def __init__(self) -> None:
        self._solver = DeterministicStrongholdSolver()

    @property
    def module_id(self) -> str:
        return "stage2_stronghold_10k"

    def evaluate_tick(self, ctx: TickContext) -> TaskDirective:
        # 1. Handle active security dialogue purely via local deterministic solver
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
                    reason="Answered Stronghold security gate",
                )
            return TaskDirective(
                directive_type=DirectiveType.CONTINUE_DIALOGUE,
                reason="Advance Stronghold dialogue",
            )

        all_claimed = all(ctx.varbits.get(vid, 0) == 1 for vid in (2309, 2310, 2311, 2312))
        on_surface = ctx.position.y < 5000

        # 2. On surface: either navigate safely to Barbarian Village entrance or deposit boots at bank
        if on_surface:
            if all_claimed:
                has_boots = any(
                    int(it.get("item_id", 0)) == 9005
                    or str(it.get("name", "")).strip().lower() == "fancy boots"
                    for it in ctx.inventory_items
                )
                if has_boots:
                    return TaskDirective(
                        directive_type=DirectiveType.NAVIGATE_TO,
                        destination="VARROCK_WEST_BANK",
                        reason="All 4 Stronghold floors claimed; routing safely to bank",
                    )
                return TaskDirective(
                    directive_type=DirectiveType.COMPLETE_TASK,
                    reason="All 4 Stronghold rewards claimed and safely on surface",
                )

            barb_wp = WorldPoint(3081, 3421, 0, "BARBARIAN_VILLAGE")
            if ctx.position.chebyshev_distance(barb_wp) > 2:
                return TaskDirective(
                    directive_type=DirectiveType.NAVIGATE_TO,
                    destination=STRONGHOLD_ENTRANCE_SURFACE,
                    reason="Navigate along capability-safe corridor to Stronghold entrance",
                )
            return TaskDirective(
                directive_type=DirectiveType.INTERACT_OBJECT,
                payload={"id": STRONGHOLD_ENTRANCE_OBJECT_ID, "action": "Climb-down"},
                reason="Enter Stronghold Floor 1",
            )

        # 3. Underground: if all 4 rewards claimed, authorize surface exit & climb out
        if all_claimed:
            return TaskDirective(
                directive_type=DirectiveType.AUTHORIZE_SURFACE_EXIT,
                payload={"id": FLOOR_1_LADDER_UP_OBJECT_ID, "action": "Climb-up"},
                reason="All rewards claimed underground; authorize surface exit",
            )

        # Otherwise progress toward active floor reward chest
        if ctx.varbits.get(2309, 0) == 0:
            return TaskDirective(
                directive_type=DirectiveType.INTERACT_OBJECT,
                payload={"id": 20788, "action": "Open"},
                reason="Claim Floor 1 Gift of Peace",
            )
        return TaskDirective(
            directive_type=DirectiveType.WAIT_NEXT_TICK,
            reason="Advancing Stronghold floor progression",
        )
