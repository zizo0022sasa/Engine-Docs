"""Unified Account Lifecycle FSM, Progression Gates, and Circadian Windows (PR-03 / T09, T11, T31)."""

from __future__ import annotations

import random
from datetime import datetime
from enum import StrEnum
from zoneinfo import ZoneInfo


class InvalidStateTransitionError(ValueError):
    """Raised when an illegal account or command state transition is attempted."""


class AccountStatus(StrEnum):
    NEW = "NEW"
    PENDING_DISCOVERY = "PENDING_DISCOVERY"
    DISCOVERING = "DISCOVERING"
    READY = "READY"
    LEASED = "LEASED"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    DRAINING = "DRAINING"
    RESTING = "RESTING"
    BLOCKED = "BLOCKED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    RECONCILING = "RECONCILING"
    DISABLED = "DISABLED"


class ReasonCode(StrEnum):
    NONE = "NONE"
    HUMAN_AUTH_REQUIRED = "HUMAN_AUTH_REQUIRED"
    BANK_PIN_REQUIRED = "BANK_PIN_REQUIRED"
    INSUFFICIENT_RESOURCES = "INSUFFICIENT_RESOURCES"
    PROXY_UNAVAILABLE = "PROXY_UNAVAILABLE"
    CAPABILITY_UNKNOWN = "CAPABILITY_UNKNOWN"
    DEATH_QUARANTINE = "DEATH_QUARANTINE"
    CRASH_UNCLOSED_SESSION = "CRASH_UNCLOSED_SESSION"
    UNKNOWN_COMMAND_EFFECT = "UNKNOWN_COMMAND_EFFECT"
    POLICY_DENIED = "POLICY_DENIED"


class CommandJournalState(StrEnum):
    PREPARED = "PREPARED"
    DISPATCHING = "DISPATCHING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    VERIFIED = "VERIFIED"
    UNKNOWN = "UNKNOWN"
    FAILED_VERIFIED = "FAILED_VERIFIED"
    REJECTED = "REJECTED"


class OperatorRole(StrEnum):
    VIEWER = "VIEWER"
    OPERATOR = "OPERATOR"
    ADMIN = "ADMIN"


ROLE_RANK: dict[OperatorRole, int] = {
    OperatorRole.VIEWER: 1,
    OperatorRole.OPERATOR: 2,
    OperatorRole.ADMIN: 3,
}

UNCLOSED_SESSION_STATES: frozenset[AccountStatus] = frozenset(
    {
        AccountStatus.DISCOVERING,
        AccountStatus.LEASED,
        AccountStatus.STARTING,
        AccountStatus.RUNNING,
        AccountStatus.DRAINING,
    }
)

# Strict transition table: notice crash states CANNOT jump directly to READY,
# and BLOCKED CANNOT jump directly to READY (must pass through RECONCILING).
ALLOWED_ACCOUNT_TRANSITIONS: dict[AccountStatus, frozenset[AccountStatus]] = {
    AccountStatus.NEW: frozenset({AccountStatus.PENDING_DISCOVERY, AccountStatus.DISABLED}),
    AccountStatus.PENDING_DISCOVERY: frozenset(
        {AccountStatus.DISCOVERING, AccountStatus.BLOCKED, AccountStatus.DISABLED}
    ),
    AccountStatus.DISCOVERING: frozenset(
        {
            AccountStatus.READY,
            AccountStatus.RESTING,
            AccountStatus.BLOCKED,
            AccountStatus.RECOVERY_REQUIRED,
            AccountStatus.DISABLED,
        }
    ),
    AccountStatus.READY: frozenset(
        {AccountStatus.LEASED, AccountStatus.RESTING, AccountStatus.BLOCKED, AccountStatus.DISABLED}
    ),
    AccountStatus.LEASED: frozenset(
        {
            AccountStatus.STARTING,
            AccountStatus.DRAINING,
            AccountStatus.BLOCKED,
            AccountStatus.RECOVERY_REQUIRED,
            AccountStatus.DISABLED,
        }
    ),
    AccountStatus.STARTING: frozenset(
        {
            AccountStatus.RUNNING,
            AccountStatus.DRAINING,
            AccountStatus.BLOCKED,
            AccountStatus.RECOVERY_REQUIRED,
            AccountStatus.DISABLED,
        }
    ),
    AccountStatus.RUNNING: frozenset(
        {
            AccountStatus.DRAINING,
            AccountStatus.BLOCKED,
            AccountStatus.RECOVERY_REQUIRED,
            AccountStatus.DISABLED,
        }
    ),
    AccountStatus.DRAINING: frozenset(
        {
            AccountStatus.RESTING,
            AccountStatus.BLOCKED,
            AccountStatus.RECOVERY_REQUIRED,
            AccountStatus.DISABLED,
        }
    ),
    AccountStatus.RESTING: frozenset(
        {AccountStatus.READY, AccountStatus.BLOCKED, AccountStatus.DISABLED}
    ),
    AccountStatus.BLOCKED: frozenset({AccountStatus.RECONCILING, AccountStatus.DISABLED}),
    AccountStatus.RECOVERY_REQUIRED: frozenset(
        {AccountStatus.RECONCILING, AccountStatus.BLOCKED, AccountStatus.DISABLED}
    ),
    AccountStatus.RECONCILING: frozenset(
        {
            AccountStatus.READY,
            AccountStatus.RESTING,
            AccountStatus.BLOCKED,
            AccountStatus.RECOVERY_REQUIRED,
            AccountStatus.DISABLED,
        }
    ),
    AccountStatus.DISABLED: frozenset(),
}


def assert_valid_account_transition(current: AccountStatus, target: AccountStatus) -> None:
    allowed = ALLOWED_ACCOUNT_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidStateTransitionError(
            f"Illegal account state transition: {current.value} -> {target.value}"
        )


def compute_progression_stage(
    *,
    tutorial_varp_281: int | None,
    quest_points: int | None,
    total_level: int | None,
    playtime_minutes: int | None,
) -> tuple[str, bool]:
    """Compute true OSRS progression stage and F2P GE trade-unlock flag (AUD-18 / T11).

    F2P trade unlock strictly requires ALL THREE:
    - Tutorial Island completed (Varp 281 >= 1000)
    - Quest Points >= 10 (NOT 7!)
    - Total Level >= 100
    - In-game playtime >= 1200 minutes (20 hours)
    """
    if tutorial_varp_281 is None or tutorial_varp_281 < 1000:
        return ("STAGE_0_TUTORIAL", False)
    if quest_points is None or total_level is None or quest_points < 10 or total_level < 100:
        return ("STAGE_1A_QP_AND_TOTAL", False)
    if playtime_minutes is None or playtime_minutes < 1200:
        return ("STAGE_1B_SEASONING", False)
    return ("STAGE_3_F2P_SKILLING", True)


def is_in_circadian_sleep_window(
    utc_now: datetime,
    timezone_name: str,
    persona_seed: int,
) -> bool:
    """Check whether `utc_now` falls in the account's geo-aligned 8–9.5h night sleep block (T31)."""
    tz = ZoneInfo(timezone_name)
    local_dt = utc_now.astimezone(tz)
    rng = random.Random(f"circadian:{persona_seed}")

    # Deterministic per-account sleep start in [22:30 .. 00:30] and duration in [480 .. 570] mins (8.0–9.5h)
    sleep_start_min_of_day = (22 * 60 + 30 + rng.randint(0, 120)) % 1440
    sleep_duration_mins = rng.randint(480, 570)
    sleep_end_min_of_day = (sleep_start_min_of_day + sleep_duration_mins) % 1440

    current_min = local_dt.hour * 60 + local_dt.minute
    if sleep_start_min_of_day < sleep_end_min_of_day:
        return sleep_start_min_of_day <= current_min < sleep_end_min_of_day
    # Crosses midnight (e.g., 23:15 -> 07:45)
    return current_min >= sleep_start_min_of_day or current_min < sleep_end_min_of_day
