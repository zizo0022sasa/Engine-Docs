"""Stage 0 Tutorial Island Task Module (`osrs_ee/bots/tutorial_module.py` — PR-13).

Key Guarantees (`AUD-17 / T10`):
- Reads `ctx.tutorial_varp_281` (`VarPlayerID.TUTORIAL = 281`, NEVER `Varbit 281`!).
- Enforces Non-Ironman safeguard at Magic Instructor (`"No, I'm not planning to do that."`) so automated
  F2P accounts never lock themselves out of Grand Exchange trading!
- Game-tick completion latch: once `tutorial_varp_281 >= 1000` on mainland Gielinor, optionally homes
  along the capability-safe corridor to `GRAND_EXCHANGE` `(3164, 3487, 0)` and emits `COMPLETE_TASK`.
"""

from __future__ import annotations

from osrs_ee.bots.base import DirectiveType, TaskDirective, TaskModule, TickContext
from osrs_ee.navigation.waypoint_graph import WorldPoint

# Exact option-text safeguards for Tutorial Island instructors (prevents accidental Ironman lock!)
TUTORIAL_SAFE_OPTIONS: tuple[str, ...] = (
    "No, I'm not planning to do that.",
    "I am an experienced player.",
    "Yes.",
    "Yes, send me to the mainland!",
    "Yes",
)


class TutorialIslandTaskModule(TaskModule):
    """Pure tick-driven Tutorial Island completion & mainland anchor module (`stage0_tutorial_island`)."""

    def __init__(self, *, home_to_grand_exchange_on_exit: bool = True) -> None:
        self.home_to_grand_exchange_on_exit = home_to_grand_exchange_on_exit

    @property
    def module_id(self) -> str:
        return "stage0_tutorial_island"

    @property
    def watched_varps(self) -> tuple[int, ...]:
        return (281,)

    def evaluate_tick(self, ctx: TickContext) -> TaskDirective:
        # 1. Immediate Game-Tick Latch once Varp 281 == 1000 (Tutorial Island complete!)
        if ctx.tutorial_varp_281 >= 1000:
            if self.home_to_grand_exchange_on_exit:
                ge_wp = WorldPoint(3164, 3487, 0, "GRAND_EXCHANGE")
                if ctx.position.chebyshev_distance(ge_wp) > 4:
                    return TaskDirective(
                        directive_type=DirectiveType.NAVIGATE_TO,
                        destination="GRAND_EXCHANGE",
                        reason="Varp 281 == 1000 verified; routing safely from Lumbridge spawn to Grand Exchange anchor",
                    )
            return TaskDirective(
                directive_type=DirectiveType.COMPLETE_TASK,
                reason="Tutorial Island completed (Varp 281 == 1000) and anchored safely",
            )

        # 2. Active Instructor Dialogue Handling (Option-Text Verified + Non-Ironman Guard)
        if ctx.dialogue_open:
            if ctx.dialogue_options:
                norm_map = {opt.strip().casefold(): opt for opt in ctx.dialogue_options}
                # Highest-priority check: NEVER select Ironman mode!
                for opt in ctx.dialogue_options:
                    low = opt.strip().casefold()
                    if "not planning to do that" in low or "no, i'm not" in low:
                        return TaskDirective(
                            directive_type=DirectiveType.SELECT_DIALOGUE,
                            payload={"option": opt},
                            reason="Selected explicit Non-Ironman safeguard option",
                        )
                for pref in TUTORIAL_SAFE_OPTIONS:
                    if pref.casefold() in norm_map:
                        return TaskDirective(
                            directive_type=DirectiveType.SELECT_DIALOGUE,
                            payload={"option": norm_map[pref.casefold()]},
                            reason=f"Selected verified Tutorial dialogue option: {pref}",
                        )
                return TaskDirective(
                    directive_type=DirectiveType.ESCALATE_HUMAN,
                    reason="UNRECOGNIZED_TUTORIAL_DIALOGUE_OPTIONS",
                )
            return TaskDirective(
                directive_type=DirectiveType.CONTINUE_DIALOGUE,
                reason="Continuing Tutorial instructor dialogue",
            )

        # 3. Canonical Varp 281 Instructor Progression
        vp = ctx.tutorial_varp_281
        if vp < 20:
            npc_name = "Gielinor Guide"
        elif vp < 120:
            npc_name = "Survival Expert"
        elif vp < 200:
            npc_name = "Master Chef"
        elif vp < 260:
            npc_name = "Quest Guide"
        elif vp < 370:
            npc_name = "Mining Instructor"
        elif vp < 500:
            npc_name = "Combat Instructor"
        elif vp < 550:
            npc_name = "Account Guide"
        elif vp < 620:
            npc_name = "Brother Brace"
        else:
            npc_name = "Magic Instructor"

        return TaskDirective(
            directive_type=DirectiveType.INTERACT_NPC,
            payload={"name": npc_name, "action": "Talk-to"},
            reason=f"Tutorial Varp 281={vp}: interacting with {npc_name}",
        )
