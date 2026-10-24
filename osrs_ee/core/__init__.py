"""Decoupled Core Infrastructure (`osrs_ee/core/` — PR-12 Field-Remediation Edition).

Provides:
- `ReactiveSafetyWatchdog`: Non-blocking tick-reactive survival guard (zero `time.sleep`, next-tick HP verification).
- `SessionLifecycleManager`: 5-state client lifecycle shield (`DISCONNECTED -> AT_LOGIN -> LOGGING_IN -> IN_GAME <-> IN_DANGER`)
  with self-healing exponential backoff.
- `ExecutionEngineKernel`: Decoupled micro-engine runner executing pluggable `TaskModule`s safely.
"""

from osrs_ee.core.execution_kernel import ExecutionEngineKernel, KernelTickResult
from osrs_ee.core.lifecycle_manager import (
    ClientLifecycleState,
    LifecycleStepOutcome,
    SessionLifecycleManager,
)
from osrs_ee.core.reactive_watchdog import (
    ReactiveSafetyWatchdog,
    SafetyDecision,
    SafetyVerdict,
)

__all__ = [
    "ClientLifecycleState",
    "ExecutionEngineKernel",
    "KernelTickResult",
    "LifecycleStepOutcome",
    "ReactiveSafetyWatchdog",
    "SafetyDecision",
    "SafetyVerdict",
    "SessionLifecycleManager",
]
