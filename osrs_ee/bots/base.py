"""Standardized Pluggable Task Interface (`TaskModule`) isolating bots from safety & transport (ADR-012 / T36).

Constitutional Rule (`معماريه.txt`):
- Task modules NEVER import `urllib`, `http`, `socket`, `time.sleep`, or safety controllers.
- Task modules receive a pure read-only `TickContext` snapshot (only when `SessionLifecycleManager`
  confirms `IN_GAME` / safe execution) and emit a declarative `TaskDirective`.
- All safety preemption, non-blocking eating, run toggling, constrained safe-corridor routing,
  bank turnarounds, and reconnect self-healing are executed transparently by `ExecutionEngineKernel`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Mapping

from osrs_ee.navigation.capabilities import AccountCapabilitiesContext
from osrs_ee.navigation.waypoint_graph import WorldPoint


class DirectiveType(StrEnum):
    WAIT_NEXT_TICK = "WAIT_NEXT_TICK"
    NAVIGATE_TO = "NAVIGATE_TO"
    INTERACT_OBJECT = "INTERACT_OBJECT"
    INTERACT_NPC = "INTERACT_NPC"
    USE_ITEM_ON_ITEM = "USE_ITEM_ON_ITEM"
    TAKE_GROUND_ITEM = "TAKE_GROUND_ITEM"
    CLICK_WIDGET = "CLICK_WIDGET"
    SELECT_DIALOGUE = "SELECT_DIALOGUE"
    CONTINUE_DIALOGUE = "CONTINUE_DIALOGUE"
    BANK_TURNAROUND = "BANK_TURNAROUND"
    AUTHORIZE_SURFACE_EXIT = "AUTHORIZE_SURFACE_EXIT"
    COMPLETE_TASK = "COMPLETE_TASK"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"


@dataclass(frozen=True)
class TickContext:
    """Immutable per-tick world view passed to a pluggable `TaskModule`."""

    game_tick: int
    position: WorldPoint
    capabilities: AccountCapabilitiesContext
    inventory_items: tuple[Mapping[str, Any], ...]
    dialogue_open: bool = False
    dialogue_question: str = ""
    dialogue_options: tuple[str, ...] = ()
    varbits: Mapping[int, int] = field(default_factory=dict)
    varps: Mapping[int, int] = field(default_factory=dict)
    tutorial_varp_281: int = 1000
    quest_points: int = 0
    total_level: int = 32
    playtime_minutes: int = 0
    bank_is_open: bool = False
    bank_items: tuple[Mapping[str, Any], ...] = ()
    in_danger: bool = False


@dataclass(frozen=True)
class TaskDirective:
    """Declarative intent emitted by a `TaskModule` for execution by `ExecutionEngineKernel`."""

    directive_type: DirectiveType
    destination: str | WorldPoint | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)
    reason: str = ""


class TaskModule(ABC):
    """Pure, tick-reactive interface implemented by all pluggable bots in `osrs_ee/bots/`."""

    @property
    @abstractmethod
    def module_id(self) -> str:
        """Unique identifier matching a curated recipe or task family."""

    @property
    def watched_varbits(self) -> tuple[int, ...]:
        """Optional Varbit IDs automatically sampled per tick by `ExecutionEngineKernel`."""
        return ()

    @property
    def watched_varps(self) -> tuple[int, ...]:
        """Optional Varp IDs automatically sampled per tick by `ExecutionEngineKernel`."""
        return ()

    @abstractmethod
    def evaluate_tick(self, ctx: TickContext) -> TaskDirective:
        """Compute the next declarative action from `ctx` without blocking or performing I/O."""
