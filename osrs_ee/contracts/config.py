"""Session-pinned runtime config snapshot, safety ceilings, and persona timing (PR-02 / T26, T32)."""

from __future__ import annotations

import hashlib
import json
import math
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from osrs_ee.contracts.models import ContractValidationError


@dataclass(frozen=True)
class HardSafetyCeilings:
    """Immutable safety bounds that tunable ranges are forbidden to violate (ADR-008)."""

    max_probe_timeout_sec: int = 90
    max_drain_timeout_sec: int = 25
    max_abort_timeout_sec: int = 5
    max_tick_stall_sec: int = 60
    min_launch_stagger_sec: int = 90
    min_cooldown_minutes: int = 240
    min_session_minutes: int = 30
    max_session_minutes: int = 210
    min_reaction_delay_ms: int = 180
    max_reaction_delay_ms: int = 10000

    def validate(self) -> None:
        if self.max_abort_timeout_sec <= 0 or self.max_abort_timeout_sec > 15:
            raise ContractValidationError("max_abort_timeout_sec must be in [1, 15]")
        if self.max_drain_timeout_sec < self.max_abort_timeout_sec:
            raise ContractValidationError("max_drain_timeout_sec must be >= max_abort_timeout_sec")
        if self.min_launch_stagger_sec < 90:
            raise ContractValidationError("min_launch_stagger_sec cannot be < 90s per acceptance gate")
        if self.min_cooldown_minutes < 240:
            raise ContractValidationError("min_cooldown_minutes cannot be < 240m per safety ceiling")


def _validate_range(
    name: str,
    pair: tuple[int | float, int | float],
    *,
    hard_min: int | float | None = None,
    hard_max: int | float | None = None,
) -> tuple[float, float]:
    if not isinstance(pair, (tuple, list)) or len(pair) != 2:
        raise ContractValidationError(f"{name}: range must be a 2-element [min, max] pair")
    lo, hi = float(pair[0]), float(pair[1])
    if math.isnan(lo) or math.isnan(hi) or math.isinf(lo) or math.isinf(hi):
        raise ContractValidationError(f"{name}: non-finite values forbidden")
    if lo > hi:
        raise ContractValidationError(f"{name}: min ({lo}) cannot be greater than max ({hi})")
    if hard_min is not None and lo < hard_min:
        raise ContractValidationError(f"{name}: min ({lo}) violates hard safety floor ({hard_min})")
    if hard_max is not None and hi > hard_max:
        raise ContractValidationError(f"{name}: max ({hi}) violates hard safety ceiling ({hard_max})")
    return (lo, hi)


@dataclass(frozen=True)
class RuntimeConfigSnapshot:
    """Validated, SHA-256 pinned configuration snapshot frozen at session launch (T26)."""

    ceilings: HardSafetyCeilings = field(default_factory=HardSafetyCeilings)
    session_duration_minutes: tuple[float, float] = (110.0, 165.0)
    cooldown_duration_minutes: tuple[float, float] = (240.0, 310.0)
    window_launch_stagger_sec: tuple[float, float] = (90.0, 145.0)
    afk_microbreak_interval_minutes: tuple[float, float] = (25.0, 45.0)
    afk_microbreak_duration_sec: tuple[float, float] = (45.0, 130.0)
    bank_interaction_delay_ms: tuple[float, float] = (450.0, 950.0)
    action_reaction_delay_ms: tuple[float, float] = (650.0, 1850.0)

    def __post_init__(self) -> None:
        self.ceilings.validate()
        object.__setattr__(
            self,
            "session_duration_minutes",
            _validate_range(
                "session_duration_minutes",
                self.session_duration_minutes,
                hard_min=self.ceilings.min_session_minutes,
                hard_max=self.ceilings.max_session_minutes,
            ),
        )
        object.__setattr__(
            self,
            "cooldown_duration_minutes",
            _validate_range(
                "cooldown_duration_minutes",
                self.cooldown_duration_minutes,
                hard_min=self.ceilings.min_cooldown_minutes,
                hard_max=1440.0,
            ),
        )
        object.__setattr__(
            self,
            "window_launch_stagger_sec",
            _validate_range(
                "window_launch_stagger_sec",
                self.window_launch_stagger_sec,
                hard_min=self.ceilings.min_launch_stagger_sec,
                hard_max=600.0,
            ),
        )
        object.__setattr__(
            self,
            "afk_microbreak_interval_minutes",
            _validate_range("afk_microbreak_interval_minutes", self.afk_microbreak_interval_minutes, hard_min=5.0, hard_max=120.0),
        )
        object.__setattr__(
            self,
            "afk_microbreak_duration_sec",
            _validate_range("afk_microbreak_duration_sec", self.afk_microbreak_duration_sec, hard_min=5.0, hard_max=300.0),
        )
        object.__setattr__(
            self,
            "bank_interaction_delay_ms",
            _validate_range(
                "bank_interaction_delay_ms",
                self.bank_interaction_delay_ms,
                hard_min=self.ceilings.min_reaction_delay_ms,
                hard_max=self.ceilings.max_reaction_delay_ms,
            ),
        )
        object.__setattr__(
            self,
            "action_reaction_delay_ms",
            _validate_range(
                "action_reaction_delay_ms",
                self.action_reaction_delay_ms,
                hard_min=self.ceilings.min_reaction_delay_ms,
                hard_max=self.ceilings.max_reaction_delay_ms,
            ),
        )

    def to_canonical_dict(self) -> dict[str, Any]:
        return {
            "ceilings": {
                "max_probe_timeout_sec": self.ceilings.max_probe_timeout_sec,
                "max_drain_timeout_sec": self.ceilings.max_drain_timeout_sec,
                "max_abort_timeout_sec": self.ceilings.max_abort_timeout_sec,
                "max_tick_stall_sec": self.ceilings.max_tick_stall_sec,
                "min_launch_stagger_sec": self.ceilings.min_launch_stagger_sec,
                "min_cooldown_minutes": self.ceilings.min_cooldown_minutes,
                "min_session_minutes": self.ceilings.min_session_minutes,
                "max_session_minutes": self.ceilings.max_session_minutes,
                "min_reaction_delay_ms": self.ceilings.min_reaction_delay_ms,
                "max_reaction_delay_ms": self.ceilings.max_reaction_delay_ms,
            },
            "tunables": {
                "session_duration_minutes": list(self.session_duration_minutes),
                "cooldown_duration_minutes": list(self.cooldown_duration_minutes),
                "window_launch_stagger_sec": list(self.window_launch_stagger_sec),
                "afk_microbreak_interval_minutes": list(self.afk_microbreak_interval_minutes),
                "afk_microbreak_duration_sec": list(self.afk_microbreak_duration_sec),
                "bank_interaction_delay_ms": list(self.bank_interaction_delay_ms),
                "action_reaction_delay_ms": list(self.action_reaction_delay_ms),
            },
        }

    @property
    def config_hash(self) -> str:
        canonical = json.dumps(self.to_canonical_dict(), sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"sha256:{digest}"


@dataclass(frozen=True)
class PersonaProfile:
    """Deterministic per-account behavioral & display profile (AUD-32 / T32)."""

    account_id: str
    persona_seed: int
    window_width: int
    window_height: int
    target_fps: int
    lognormal_sigma: float
    prefers_fkeys: bool

    @classmethod
    def from_seed(cls, account_id: str, persona_seed: int) -> PersonaProfile:
        rng = random.Random(f"persona:{account_id}:{persona_seed}")
        # Even dimensions within [784..828] x [592..618] to avoid universal 800x600 cluster
        width = rng.randrange(784, 830, 2)
        height = rng.randrange(592, 620, 2)
        fps = rng.randint(13, 18)
        sigma = round(rng.uniform(0.24, 0.38), 4)
        prefers_fkeys = rng.random() >= 0.45
        return cls(
            account_id=account_id,
            persona_seed=persona_seed,
            window_width=width,
            window_height=height,
            target_fps=fps,
            lognormal_sigma=sigma,
            prefers_fkeys=prefers_fkeys,
        )

    def sample_delay(
        self,
        bounds: tuple[float, float],
        *,
        rng: random.Random | None = None,
    ) -> float:
        """Sample a right-skewed Bounded Log-Normal value strictly inside [lo, hi]."""
        lo, hi = _validate_range("sample_delay", bounds)
        if lo == hi:
            return lo
        gen = rng if rng is not None else random.Random()
        span = hi - lo
        # Anchor median near 32% of the span so tail skews naturally toward hi (human reaction curve)
        median_offset = span * 0.32
        mu = math.log(max(median_offset, 1e-6))
        for _ in range(12):
            candidate = lo + gen.lognormvariate(mu, self.lognormal_sigma)
            if lo <= candidate <= hi:
                return candidate
        return min(hi, max(lo, lo + median_offset))


def cancellable_monotonic_wait(
    duration_sec: float,
    cancel_event: threading.Event,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[bool, float]:
    """Wait up to `duration_sec` on a monotonic clock, returning immediately if cancelled.

    Returns `(completed_normally, elapsed_sec)`.
    """
    if duration_sec < 0:
        raise ContractValidationError("duration_sec cannot be negative")
    start = clock()
    if duration_sec == 0:
        return (not cancel_event.is_set(), 0.0)
    cancelled = cancel_event.wait(timeout=duration_sec)
    elapsed = max(0.0, clock() - start)
    return (not cancelled, elapsed)
