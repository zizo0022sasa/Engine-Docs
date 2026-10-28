"""Allowlisted `BotModuleRegistry` mapping curated `recipe_id`s to pure `TaskModule` bots (PR-13 / ADR-006, ADR-012)."""

from __future__ import annotations

from typing import Callable

from osrs_ee.bots.base import TaskModule
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.seasoning_module import TotalLevel100TaskModule
from osrs_ee.bots.stronghold_module import StrongholdTaskModule
from osrs_ee.bots.tutorial_module import TutorialIslandTaskModule
from osrs_ee.bots.woodcutting_module import SkillingGathererTaskModule

QUARANTINED_CONCEPT_RECIPES: frozenset[str] = frozenset(
    {
        "f2p_tanner_alkharid_quarantined",
        "clay_softener_varrock",
        "telegrab_zamorak_wine",
    }
)


class BotModuleRegistry:
    """Default-Deny factory mapping all 7 approved `CURATED_RECIPES` IDs to isolated `TaskModule` bots."""

    def __init__(self) -> None:
        self._factories: dict[str, Callable[[], TaskModule]] = {
            "stage0_tutorial_island": lambda: TutorialIslandTaskModule(),
            "stage1a_f2p_quests_10qp": lambda: TradeUnlockQuestsTaskModule(),
            "stage1b_seasoning_gatherer": lambda: TotalLevel100TaskModule(),
            "stage2_stronghold_10k": lambda: StrongholdTaskModule(),
            "f2p_draynor_willow": lambda: SkillingGathererTaskModule.draynor_willow(),
            "f2p_barbarian_fly_fishing": lambda: SkillingGathererTaskModule.barbarian_fly_fishing(),
            "f2p_alkharid_mining": lambda: SkillingGathererTaskModule.alkharid_mining(),
        }

    def list_registered_ids(self) -> tuple[str, ...]:
        return tuple(self._factories.keys())

    def create(self, recipe_id: str, *, min_required_food: int | None = None) -> TaskModule:
        if recipe_id in QUARANTINED_CONCEPT_RECIPES:
            raise PermissionError(f"Recipe {recipe_id!r} is QUARANTINED (CONCEPT_ONLY) under ADR-006")
        if recipe_id not in self._factories:
            raise KeyError(f"Unregistered bot module recipe_id (Default-Deny): {recipe_id!r}")
        if recipe_id == "stage2_stronghold_10k" and min_required_food is not None:
            return StrongholdTaskModule(min_required_food=int(min_required_food))
        return self._factories[recipe_id]()
