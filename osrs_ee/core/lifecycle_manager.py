"""Central 5-State Client Lifecycle State Machine & Self-Healing Reconnect Shield (PR-12 / AUD-38 / ADR-011 / T35).

Explicit Lifecycle States:
  `DISCONNECTED -> AT_LOGIN -> LOGGING_IN -> IN_GAME <-> IN_DANGER` (plus terminal `TERMINATED`).

Guarantees:
1. Tasks NEVER execute or query `/inventory`, `/bank`, `/skills`, or `/varps` when state is
   `DISCONNECTED`, `AT_LOGIN`, or `LOGGING_IN` (eliminating null/empty response crashes).
2. Self-heals transient socket drops (`DISCONNECTED`) and 5-minute OSRS idle logouts (`AT_LOGIN`)
   via bounded Exponential Backoff (`base_backoff_sec * 2**attempt`, capped at `max_backoff_sec`)
   tracked via monotonic deadlines — ZERO blocking `time.sleep` inside the state machine!
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ClientLifecycleState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    AT_LOGIN = "AT_LOGIN"
    LOGGING_IN = "LOGGING_IN"
    IN_GAME = "IN_GAME"
    IN_DANGER = "IN_DANGER"
    TERMINATED = "TERMINATED"


@dataclass(frozen=True)
class LifecycleStepOutcome:
    state: ClientLifecycleState
    task_execution_allowed: bool
    triggered_relogin: bool
    next_retry_delay_sec: float
    consecutive_heal_attempts: int
    detail: str


class SessionLifecycleManager:
    """Central middleware shielding execution engines from disconnections and login screens."""

    def __init__(
        self,
        *,
        base_backoff_sec: float = 1.0,
        max_backoff_sec: float = 30.0,
        max_heal_attempts: int = 6,
    ) -> None:
        self.base_backoff_sec = base_backoff_sec
        self.max_backoff_sec = max_backoff_sec
        self.max_heal_attempts = max_heal_attempts

        self.state: ClientLifecycleState = ClientLifecycleState.DISCONNECTED
        self.consecutive_heal_attempts: int = 0
        self._next_attempt_monotonic: float = 0.0
        self.backoff_history_sec: list[float] = []
        self.transition_history: list[ClientLifecycleState] = []

    def _transition(self, target: ClientLifecycleState) -> None:
        if self.state != target:
            self.state = target
            self.transition_history.append(target)

    def _schedule_backoff(self, now_monotonic: float) -> float:
        delay = min(self.max_backoff_sec, self.base_backoff_sec * (2 ** self.consecutive_heal_attempts))
        self.consecutive_heal_attempts += 1
        self._next_attempt_monotonic = now_monotonic + delay
        self.backoff_history_sec.append(delay)
        return delay

    def update_danger_state(self, *, in_danger: bool) -> ClientLifecycleState:
        if self.state in (ClientLifecycleState.IN_GAME, ClientLifecycleState.IN_DANGER):
            self._transition(
                ClientLifecycleState.IN_DANGER if in_danger else ClientLifecycleState.IN_GAME
            )
        return self.state

    def evaluate_session_health(
        self,
        *,
        adapter: Any,
        now_monotonic: float,
        relogin_dispatcher: Any | None = None,
    ) -> LifecycleStepOutcome:
        """Inspect client connectivity & login screen state before ANY task query runs."""
        if self.state == ClientLifecycleState.TERMINATED:
            return LifecycleStepOutcome(
                state=ClientLifecycleState.TERMINATED,
                task_execution_allowed=False,
                triggered_relogin=False,
                next_retry_delay_sec=0.0,
                consecutive_heal_attempts=self.consecutive_heal_attempts,
                detail="Session lifecycle is TERMINATED",
            )

        # Respect active exponential backoff window during recovery
        if self.state in (ClientLifecycleState.DISCONNECTED, ClientLifecycleState.LOGGING_IN) and (
            now_monotonic < self._next_attempt_monotonic
        ):
            rem = round(self._next_attempt_monotonic - now_monotonic, 3)
            return LifecycleStepOutcome(
                state=self.state,
                task_execution_allowed=False,
                triggered_relogin=False,
                next_retry_delay_sec=rem,
                consecutive_heal_attempts=self.consecutive_heal_attempts,
                detail=f"Awaiting self-healing backoff ({rem}s remaining)",
            )

        # Single lightweight probe (`/status` or `/state`) — NEVER queries `/inventory` or `/bank`!
        try:
            probe = adapter.probe_session_state()
        except (ConnectionError, TimeoutError, OSError) as exc:
            self._transition(ClientLifecycleState.DISCONNECTED)
            if self.consecutive_heal_attempts >= self.max_heal_attempts:
                self._transition(ClientLifecycleState.TERMINATED)
                return LifecycleStepOutcome(
                    state=ClientLifecycleState.TERMINATED,
                    task_execution_allowed=False,
                    triggered_relogin=False,
                    next_retry_delay_sec=0.0,
                    consecutive_heal_attempts=self.consecutive_heal_attempts,
                    detail=f"Max reconnect attempts ({self.max_heal_attempts}) exhausted: {exc}",
                )
            delay = self._schedule_backoff(now_monotonic)
            return LifecycleStepOutcome(
                state=ClientLifecycleState.DISCONNECTED,
                task_execution_allowed=False,
                triggered_relogin=False,
                next_retry_delay_sec=delay,
                consecutive_heal_attempts=self.consecutive_heal_attempts,
                detail=f"Client unreachable ({exc}); isolating tasks & retrying in {delay}s",
            )

        # Client is reachable over loopback HTTP!
        if probe.get("logged_in") is True:
            self.consecutive_heal_attempts = 0
            self._next_attempt_monotonic = 0.0
            if self.state not in (ClientLifecycleState.IN_GAME, ClientLifecycleState.IN_DANGER):
                self._transition(ClientLifecycleState.IN_GAME)
            return LifecycleStepOutcome(
                state=self.state,
                task_execution_allowed=True,
                triggered_relogin=False,
                next_retry_delay_sec=0.0,
                consecutive_heal_attempts=0,
                detail="Session verified IN_GAME",
            )

        # Client is at LOGIN_SCREEN (`loggedIn == False`)
        if getattr(adapter, "auto_login_suppressed", False):
            self._transition(ClientLifecycleState.AT_LOGIN)
            return LifecycleStepOutcome(
                state=ClientLifecycleState.AT_LOGIN,
                task_execution_allowed=False,
                triggered_relogin=False,
                next_retry_delay_sec=0.0,
                consecutive_heal_attempts=self.consecutive_heal_attempts,
                detail="At login screen with AutoLogin intentionally suppressed (DRAIN)",
            )

        if self.consecutive_heal_attempts >= self.max_heal_attempts:
            self._transition(ClientLifecycleState.TERMINATED)
            return LifecycleStepOutcome(
                state=ClientLifecycleState.TERMINATED,
                task_execution_allowed=False,
                triggered_relogin=False,
                next_retry_delay_sec=0.0,
                consecutive_heal_attempts=self.consecutive_heal_attempts,
                detail="Max self-healing re-login attempts exhausted",
            )

        # Transition AT_LOGIN -> LOGGING_IN and trigger controlled non-blocking login
        self._transition(ClientLifecycleState.AT_LOGIN)
        delay = self._schedule_backoff(now_monotonic)
        triggered = False
        if relogin_dispatcher is not None:
            relogin_dispatcher()
            triggered = True
            self._transition(ClientLifecycleState.LOGGING_IN)

        return LifecycleStepOutcome(
            state=self.state,
            task_execution_allowed=False,
            triggered_relogin=triggered,
            next_retry_delay_sec=delay,
            consecutive_heal_attempts=self.consecutive_heal_attempts,
            detail=f"Session at LOGIN_SCREEN; isolated tasks & scheduled self-heal backoff={delay}s",
        )
