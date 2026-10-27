"""Isolated Pluggable Task Modules (`osrs_ee/bots/` — PR-12 + PR-13).

Every module in `osrs_ee/bots/` is AST-verified to contain ZERO transport, socket, database,
`time.sleep`, or internal safety imports (`ADR-012 / T36`).
"""

from osrs_ee.bots.base import DirectiveType, TaskDirective, TaskModule, TickContext
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.registry import QUARANTINED_CONCEPT_RECIPES, BotModuleRegistry
from osrs_ee.bots.seasoning_module import TotalLevel100TaskModule
from osrs_ee.bots.stronghold_module import StrongholdTaskModule, classify_stronghold_region
from osrs_ee.bots.tutorial_module import TutorialIslandTaskModule
from osrs_ee.bots.woodcutting_module import SkillingGathererTaskModule

__all__ = [
    "BotModuleRegistry",
    "DirectiveType",
    "QUARANTINED_CONCEPT_RECIPES",
    "SkillingGathererTaskModule",
    "StrongholdTaskModule",
    "TaskDirective",
    "TaskModule",
    "TickContext",
    "TotalLevel100TaskModule",
    "TradeUnlockQuestsTaskModule",
    "TutorialIslandTaskModule",
    "classify_stronghold_region",
]
