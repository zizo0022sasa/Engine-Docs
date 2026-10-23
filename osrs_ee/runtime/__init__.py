"""Hermetic single-slot supervisor, adapter gate, verified worker, and fake adapter (PR-04)."""

from osrs_ee.runtime.adapter_gate import AdapterSecurityError, GameAdapterGate, PolicyDeniedError
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.runtime.supervisor import SingleSlotSupervisor, WatchdogVerdict
from osrs_ee.runtime.worker import TaskWorker, WorkerOutcome

__all__ = [
    "AdapterSecurityError",
    "FakeGameAdapter",
    "GameAdapterGate",
    "PolicyDeniedError",
    "SingleSlotSupervisor",
    "TaskWorker",
    "WatchdogVerdict",
    "WorkerOutcome",
]
