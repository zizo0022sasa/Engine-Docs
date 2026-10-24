"""Decoupled Pluggable Task Modules (`bots/` Standard Interface — PR-12 / AUD-39 / ADR-012 / T36)."""

from osrs_ee.bots.base import DirectiveType, TaskDirective, TaskModule, TickContext
from osrs_ee.bots.stronghold_module import StrongholdTaskModule
from osrs_ee.bots.woodcutting_module import SkillingGathererTaskModule

__all__ = [
    "DirectiveType",
    "SkillingGathererTaskModule",
    "StrongholdTaskModule",
    "TaskDirective",
    "TaskModule",
    "TickContext",
]
