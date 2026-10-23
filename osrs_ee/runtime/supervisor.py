"""Single-Slot Supervisor with Pause/Drain/Abort deadlines, Tick Watchdog, and Circuit Breaker (PR-04)."""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Any

from osrs_ee.contracts.config import RuntimeConfigSnapshot
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.worker import TaskWorker
from osrs_ee.state.fsm import AccountStatus, ReasonCode
from osrs_ee.state.store import StateStore


class WatchdogVerdict(StrEnum):
    HEALTHY_MOVING = "HEALTHY_MOVING"
    HEALTHY_STATIONARY_TICKING = "HEALTHY_STATIONARY_TICKING"
    FROZEN_TICK_STALLED = "FROZEN_TICK_STALLED"
    CIRCUIT_BREAKER_TRIPPED = "CIRCUIT_BREAKER_TRIPPED"


class SingleSlotSupervisor:
    """Manages a single isolated client slot (`concurrency = 1`) with bounded stop deadlines."""

    def __init__(
        self,
        *,
        store: StateStore,
        config: RuntimeConfigSnapshot | None = None,
        max_consecutive_restarts: int = 2,
    ) -> None:
        self.store = store
        self.config = config or RuntimeConfigSnapshot()
        self.max_consecutive_restarts = max_consecutive_restarts
        self.cancel_event = threading.Event()
        self._restart_counts: dict[str, int] = {}

        self.active_gate: GameAdapterGate | None = None
        self.active_worker: TaskWorker | None = None
        self.active_lease: dict[str, Any] | None = None

    def attach_session(self, *, lease: dict[str, Any], gate: GameAdapterGate) -> TaskWorker:
        """Bind a fresh client instance to the leased slot (never reuses a prior JVM!)."""
        self.cancel_event.clear()
        gate.bind_to_lease(
            account_id=lease["account_id"],
            session_id=lease["session_id"],
            lease_id=lease["lease_id"],
            session_epoch=lease["session_epoch"],
        )
        worker = TaskWorker(
            gate=gate,
            account_id=lease["account_id"],
            session_id=lease["session_id"],
            lease_id=lease["lease_id"],
            session_epoch=lease["session_epoch"],
        )
        self.active_lease = lease
        self.active_gate = gate
        self.active_worker = worker
        return worker

    def pause(self) -> None:
        """T16 Level 1: Immediately halt issuance of any new CommandIntent."""
        if self.active_worker is not None:
            self.active_worker.paused = True

    def drain(self, *, now_utc: datetime | None = None) -> str:
        """T16 Level 2: Cooperative PLUGIN_STOP -> SAFE_LOGOUT (AutoLogin suppressed) -> kill JVM.

        Automatically escalates to `abort()` if logout fails or exceeds `max_drain_timeout_sec`.
        """
        if self.active_lease is None or self.active_gate is None:
            return "NO_ACTIVE_SESSION"

        now_dt = now_utc or datetime.now(timezone.utc)
        lease = self.active_lease
        gate = self.active_gate
        t_start = time.monotonic()
        deadline_sec = float(self.config.ceilings.max_drain_timeout_sec)

        try:
            stop_intent = {
                "schema_version": "1.0",
                "command_id": f"cmd_stop_{uuid.uuid4().hex[:10]}",
                "session_id": lease["session_id"],
                "account_id": lease["account_id"],
                "client_instance_id": gate.adapter.client_instance_id,
                "lease_id": lease["lease_id"],
                "session_epoch": lease["session_epoch"],
                "base_state_version": 1,
                "command_type": "PLUGIN_STOP",
                "idempotency_class": "IDEMPOTENT",
                "payload": {},
                "precondition": "session_active",
                "expected_postcondition": "plugin_stopped",
                "timeout_ms": 3000,
            }
            gate.dispatch_verified_command(
                intent=stop_intent,
                postcondition_checker=lambda _obs: gate.adapter.plugin_running is False,
            )

            logout_intent = {
                "schema_version": "1.0",
                "command_id": f"cmd_logout_{uuid.uuid4().hex[:10]}",
                "session_id": lease["session_id"],
                "account_id": lease["account_id"],
                "client_instance_id": gate.adapter.client_instance_id,
                "lease_id": lease["lease_id"],
                "session_epoch": lease["session_epoch"],
                "base_state_version": 1,
                "command_type": "SAFE_LOGOUT",
                "idempotency_class": "IDEMPOTENT",
                "payload": {},
                "precondition": "plugin_stopped",
                "expected_postcondition": "login_state == LOGIN_SCREEN",
                "timeout_ms": int(deadline_sec * 1000),
            }
            logout_res = gate.dispatch_verified_command(
                intent=logout_intent,
                postcondition_checker=lambda obs: obs["login_state"] == "LOGIN_SCREEN",
            )
            elapsed = time.monotonic() - t_start
            if logout_res["effect_status"] != "VERIFIED" or elapsed > deadline_sec:
                return self.abort(now_utc=now_dt)

            # Terminate entire JVM process tree so next account gets a 100% fresh JVM
            gate.adapter.kill_process_tree()
            cooldown_min = self.config.cooldown_duration_minutes[0]
            self.store.close_session(
                account_id=lease["account_id"],
                session_id=lease["session_id"],
                lease_id=lease["lease_id"],
                session_epoch=lease["session_epoch"],
                target_status=AccountStatus.RESTING,
                reason_code=ReasonCode.NONE,
                cooldown_until=now_dt + timedelta(minutes=cooldown_min),
                now_utc=now_dt,
            )
            self._restart_counts[lease["account_id"]] = 0
            self._clear_slot()
            return "DRAINED_CLEANLY"
        except Exception:
            return self.abort(now_utc=now_dt)

    def abort(self, *, now_utc: datetime | None = None) -> str:
        """T16 Level 3: Hard emergency stop revoking lease immediately, waking waits, killing JVM tree."""
        self.cancel_event.set()
        if self.active_lease is None or self.active_gate is None:
            return "ABORTED_IDLE"

        now_dt = now_utc or datetime.now(timezone.utc)
        lease = self.active_lease
        gate = self.active_gate

        gate.adapter.kill_process_tree()
        self.store.close_session(
            account_id=lease["account_id"],
            session_id=lease["session_id"],
            lease_id=lease["lease_id"],
            session_epoch=lease["session_epoch"],
            target_status=AccountStatus.RECOVERY_REQUIRED,
            reason_code=ReasonCode.CRASH_UNCLOSED_SESSION,
            now_utc=now_dt,
        )
        self._clear_slot()
        return "ABORTED_RECOVERY_REQUIRED"

    def evaluate_watchdog(
        self,
        *,
        account_id: str,
        previous_obs: dict[str, Any],
        current_obs: dict[str, Any],
        stall_elapsed_sec: float,
        now_utc: datetime | None = None,
    ) -> WatchdogVerdict:
        """T17: Distinguish stationary skilling (tick advancing) from true client freeze + circuit breaker."""
        prev_pos = (previous_obs["position"]["x"], previous_obs["position"]["y"])
        curr_pos = (current_obs["position"]["x"], current_obs["position"]["y"])
        prev_tick = previous_obs["game_tick"]
        curr_tick = current_obs["game_tick"]

        if curr_tick is not None and prev_tick is not None and curr_tick > prev_tick:
            if curr_pos != prev_pos:
                return WatchdogVerdict.HEALTHY_MOVING
            # Coordinates fixed (e.g. cooking/fishing) while game_tick advances -> DO NOT KILL!
            return WatchdogVerdict.HEALTHY_STATIONARY_TICKING

        if stall_elapsed_sec < self.config.ceilings.max_tick_stall_sec:
            return WatchdogVerdict.HEALTHY_STATIONARY_TICKING

        # Tick is genuinely stalled beyond max_tick_stall_sec -> abort session & increment breaker
        count = self._restart_counts.get(account_id, 0) + 1
        self._restart_counts[account_id] = count
        self.abort(now_utc=now_utc)

        if count > self.max_consecutive_restarts:
            return WatchdogVerdict.CIRCUIT_BREAKER_TRIPPED
        return WatchdogVerdict.FROZEN_TICK_STALLED

    def _clear_slot(self) -> None:
        self.active_lease = None
        self.active_gate = None
        self.active_worker = None
