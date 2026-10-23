"""Curated TaskRecipe catalog, weighted anti-repeat selector, and Single-Slot Pilot Runner (PR-06)."""

from osrs_ee.tasks.catalog import (
    CURATED_RECIPES,
    CuratedRecipeRegistry,
    select_eligible_recipe,
)
from osrs_ee.tasks.pilot_runner import PilotRunResult, SingleSlotPilotRunner

__all__ = [
    "CURATED_RECIPES",
    "CuratedRecipeRegistry",
    "PilotRunResult",
    "SingleSlotPilotRunner",
    "select_eligible_recipe",
]
