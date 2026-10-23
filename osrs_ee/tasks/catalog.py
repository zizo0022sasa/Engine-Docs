"""Curated TaskRecipe Registry and anti-repeat weighted task selector (PR-06 / ADR-006 / T14)."""

from __future__ import annotations

import random
from typing import Any

from osrs_ee.contracts.models import VERIFICATION_STAGE_ORDER, validate_task_recipe

CURATED_RECIPES: tuple[dict[str, Any], ...] = (
    {
        "schema_version": "1.0",
        "recipe_id": "stage0_tutorial_island",
        "display_name": "Stage 0: Tutorial Island Completion",
        "plugin_id": "osrs_ee.plugins.tutorial.TutorialIslandPlugin",
        "verification_stage": "HERMETIC_TESTED",
        "stage_tier": "STAGE_0_TUTORIAL",
        "min_skills": {},
        "required_varps": {},
        "tool_candidates": [],
        "sellable_yields": [],
        "clear_equipment": False,
    },
    {
        "schema_version": "1.0",
        "recipe_id": "stage1a_f2p_quests_10qp",
        "display_name": "Stage 1A: F2P Quest Bundle to 10 QP",
        "plugin_id": "net.runelite.client.plugins.questhelper.QuestHelperPlugin",
        "verification_stage": "HERMETIC_TESTED",
        "stage_tier": "STAGE_1A_QP_AND_TOTAL",
        "min_skills": {},
        "required_varps": {"281": 1000},
        "tool_candidates": [],
        "sellable_yields": [],
        "clear_equipment": False,
    },
    {
        "schema_version": "1.0",
        "recipe_id": "stage1b_seasoning_gatherer",
        "display_name": "Stage 1B: 20-Hour Seasoning Woodcutting (Bank-Only, Zero GE)",
        "plugin_id": "net.runelite.client.plugins.microbot.woodcutting.AutoWoodcuttingPlugin",
        "verification_stage": "HERMETIC_TESTED",
        "stage_tier": "STAGE_1B_SEASONING",
        "min_skills": {"woodcutting": 1},
        "required_varps": {"281": 1000},
        "tool_candidates": ["Rune axe", "Adamant axe", "Mithril axe", "Steel axe", "Iron axe", "Bronze axe"],
        "sellable_yields": [],
        "clear_equipment": False,
    },
    {
        "schema_version": "1.0",
        "recipe_id": "stage2_stronghold_10k",
        "display_name": "Stage 2: Stronghold of Security 10k Capital Pilot",
        "plugin_id": "osrs_ee.plugins.stronghold.StrongholdSecurityPlugin",
        "verification_stage": "HERMETIC_TESTED",
        "stage_tier": "STAGE_2_CAPITAL",
        "min_skills": {},
        "required_varps": {"281": 1000},
        "tool_candidates": [],
        "sellable_yields": [],
        "clear_equipment": False,
    },
    {
        "schema_version": "1.0",
        "recipe_id": "f2p_draynor_willow",
        "display_name": "Stage 3: Draynor Willow Woodcutter",
        "plugin_id": "net.runelite.client.plugins.microbot.woodcutting.AutoWoodcuttingPlugin",
        "verification_stage": "HERMETIC_TESTED",
        "stage_tier": "STAGE_3_F2P_SKILLING",
        "min_skills": {"woodcutting": 30},
        "required_varps": {"281": 1000},
        "tool_candidates": ["Rune axe", "Adamant axe", "Mithril axe", "Steel axe", "Iron axe", "Bronze axe"],
        "sellable_yields": ["Willow logs"],
        "clear_equipment": False,
    },
    {
        "schema_version": "1.0",
        "recipe_id": "f2p_barbarian_fly_fishing",
        "display_name": "Stage 3: Barbarian Village Fly Fishing",
        "plugin_id": "net.runelite.client.plugins.microbot.autofishing.AutoFishingPlugin",
        "verification_stage": "HERMETIC_TESTED",
        "stage_tier": "STAGE_3_F2P_SKILLING",
        "min_skills": {"fishing": 20},
        "required_varps": {"281": 1000},
        "tool_candidates": ["Fly fishing rod"],
        "sellable_yields": ["Raw trout", "Raw salmon"],
        "clear_equipment": False,
    },
    {
        "schema_version": "1.0",
        "recipe_id": "f2p_alkharid_mining",
        "display_name": "Stage 3: Al Kharid Iron/Copper Miner",
        "plugin_id": "net.runelite.client.plugins.microbot.mining.AutoMiningPlugin",
        "verification_stage": "HERMETIC_TESTED",
        "stage_tier": "STAGE_3_F2P_SKILLING",
        "min_skills": {"mining": 15},
        "required_varps": {"281": 1000},
        "tool_candidates": ["Rune pickaxe", "Adamant pickaxe", "Mithril pickaxe", "Iron pickaxe", "Bronze pickaxe"],
        "sellable_yields": ["Iron ore", "Copper ore"],
        "clear_equipment": False,
    },
    {
        "schema_version": "1.0",
        "recipe_id": "f2p_tanner_alkharid_quarantined",
        "display_name": "Quarantined Concept: Al Kharid Cowhide Tanner",
        "plugin_id": "osrs_ee.custom.unbuilt.TannerPlugin",
        "verification_stage": "CONCEPT_ONLY",
        "stage_tier": "STAGE_3_F2P_SKILLING",
        "min_skills": {"crafting": 1},
        "required_varps": {"281": 1000},
        "tool_candidates": [],
        "sellable_yields": ["Leather"],
        "clear_equipment": True,
    },
)


class CuratedRecipeRegistry:
    """Allowlisted recipe catalog enforcing verification stage gates (ADR-006)."""

    def __init__(self, recipes: tuple[dict[str, Any], ...] = CURATED_RECIPES) -> None:
        self._by_id: dict[str, dict[str, Any]] = {}
        for r in recipes:
            validate_task_recipe(r, minimum_stage="CONCEPT_ONLY")
            self._by_id[r["recipe_id"]] = dict(r)

    def get(self, recipe_id: str, *, minimum_stage: str = "HERMETIC_TESTED") -> dict[str, Any]:
        if recipe_id not in self._by_id:
            raise KeyError(f"Unregistered recipe_id (Default-Deny): {recipe_id!r}")
        return dict(validate_task_recipe(self._by_id[recipe_id], minimum_stage=minimum_stage))

    def list_eligible(
        self,
        *,
        stage_tier: str,
        account_skills: dict[str, int],
        tutorial_varp_281: int,
        minimum_stage: str = "HERMETIC_TESTED",
    ) -> list[dict[str, Any]]:
        req_rank = VERIFICATION_STAGE_ORDER[minimum_stage]
        eligible: list[dict[str, Any]] = []
        for r in self._by_id.values():
            if VERIFICATION_STAGE_ORDER[r["verification_stage"]] < req_rank:
                continue
            if r["stage_tier"] != stage_tier:
                continue
            if r["required_varps"].get("281") and tutorial_varp_281 < int(r["required_varps"]["281"]):
                continue
            if any(account_skills.get(sk, 1) < int(lvl) for sk, lvl in r["min_skills"].items()):
                continue
            eligible.append(dict(r))
        return eligible


def select_eligible_recipe(
    eligible: list[dict[str, Any]],
    *,
    last_recipe_id: str | None = None,
    rng: random.Random | None = None,
) -> dict[str, Any]:
    """Select next task avoiding immediate back-to-back repetition when alternatives exist."""
    if not eligible:
        raise ValueError("No eligible recipes available for account")
    gen = rng or random.Random()
    if len(eligible) > 1 and last_recipe_id is not None:
        non_repeat = [r for r in eligible if r["recipe_id"] != last_recipe_id]
        if non_repeat:
            return gen.choice(non_repeat)
    return gen.choice(eligible)
