"""Measured RSS capacity calculator, >=90s staggered launcher, and unsynchronized microbreaks (PR-09)."""

from __future__ import annotations

import ctypes
import hashlib
import math
import os
from dataclasses import dataclass

from osrs_ee.contracts.config import HardSafetyCeilings

_CEILINGS = HardSafetyCeilings()
MAX_CONCURRENT_SLOTS_16GB_HOST = 6
MAX_CONCURRENT_SLOTS_32GB_PLUS_HOST = 15


def detect_host_total_ram_mb() -> int:
    """Detect physical host RAM in MB across Linux and Windows without third-party packages."""
    if os.name == "nt":
        class _MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = _MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):  # type: ignore[attr-defined]
            return int(stat.ullTotalPhys // (1024 * 1024))
        return 16384

    meminfo = "/proc/meminfo"
    if os.path.exists(meminfo):
        with open(meminfo, encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return kb // 1024
    return 16384


@dataclass(frozen=True)
class CapacityReport:
    total_ram_mb: int
    os_core_reserve_mb: int
    measured_per_slot_rss_mb: int
    safety_buffered_slot_mb: int
    computed_safe_slots: int


class RssCapacityCalculator:
    """Calculates safe concurrent JVM slots from measured total RSS (AUD-08 / T18 / T30).

    Never uses heap `-Xmx` alone: a `-Xmx768m` RuneLite/Microbot JVM routinely consumes
    `1,150-1,450 MB` total RSS once DirectBuffers, Metaspace, JIT, and OpenGL/Xvfb are counted.
    """

    def __init__(
        self,
        *,
        os_core_reserve_mb: int = 5632,
        rss_safety_multiplier: float = 1.25,
        min_free_ram_for_launch_mb: int = 1800,
        max_cpu_pct_for_launch: float = 82.0,
    ) -> None:
        self.os_core_reserve_mb = os_core_reserve_mb
        self.rss_safety_multiplier = rss_safety_multiplier
        self.min_free_ram_for_launch_mb = min_free_ram_for_launch_mb
        self.max_cpu_pct_for_launch = max_cpu_pct_for_launch

    def compute_safe_slots(
        self,
        *,
        total_ram_mb: int | None = None,
        measured_per_slot_rss_mb: int = 1250,
    ) -> CapacityReport:
        ram_mb = total_ram_mb if total_ram_mb is not None else detect_host_total_ram_mb()
        usable_mb = max(0, ram_mb - self.os_core_reserve_mb)
        buffered_slot = max(800, int(math.ceil(measured_per_slot_rss_mb * self.rss_safety_multiplier)))
        raw_slots = usable_mb // buffered_slot

        hard_cap = (
            MAX_CONCURRENT_SLOTS_16GB_HOST
            if ram_mb <= 16_800
            else MAX_CONCURRENT_SLOTS_32GB_PLUS_HOST
        )
        safe_slots = max(1, min(raw_slots, hard_cap)) if usable_mb >= buffered_slot else 0
        return CapacityReport(
            total_ram_mb=ram_mb,
            os_core_reserve_mb=self.os_core_reserve_mb,
            measured_per_slot_rss_mb=measured_per_slot_rss_mb,
            safety_buffered_slot_mb=buffered_slot,
            computed_safe_slots=safe_slots,
        )

    def can_admit_next_slot(
        self,
        *,
        active_slots: int,
        safe_slot_limit: int,
        current_free_ram_mb: int,
        current_cpu_pct: float,
    ) -> tuple[bool, str]:
        if active_slots >= safe_slot_limit:
            return (False, f"AT_SAFE_SLOT_LIMIT ({active_slots}/{safe_slot_limit})")
        if current_free_ram_mb < self.min_free_ram_for_launch_mb:
            return (
                False,
                f"LOW_FREE_RAM ({current_free_ram_mb}MB < {self.min_free_ram_for_launch_mb}MB)",
            )
        if current_cpu_pct > self.max_cpu_pct_for_launch:
            return (False, f"HIGH_CPU_PRESSURE ({current_cpu_pct:.1f}% > {self.max_cpu_pct_for_launch:.1f}%)")
        return (True, "ADMITTED")


class StaggeredLaunchPlanner:
    """Schedules multi-account cold starts >=90s apart with deterministic jitter (AUD-23 / T18)."""

    def __init__(self, *, min_stagger_sec: float = _CEILINGS.min_launch_stagger_sec) -> None:
        if min_stagger_sec < _CEILINGS.min_launch_stagger_sec:
            raise ValueError(
                f"min_stagger_sec ({min_stagger_sec}) violates ceiling ({_CEILINGS.min_launch_stagger_sec})"
            )
        self.min_stagger_sec = min_stagger_sec

    def plan_launches(
        self,
        account_ids: list[str],
        *,
        base_epoch_sec: float,
        day_key: str = "2026-10-23",
    ) -> dict[str, float]:
        schedule: dict[str, float] = {}
        cursor = float(base_epoch_sec)
        for idx, acc_id in enumerate(account_ids):
            if idx == 0:
                schedule[acc_id] = cursor
                continue
            digest = hashlib.sha256(f"{acc_id}:{day_key}".encode("utf-8")).digest()
            jitter = (int.from_bytes(digest[:2], "big") / 65535.0) * 35.0
            cursor += self.min_stagger_sec + jitter
            schedule[acc_id] = round(cursor, 3)
        return schedule


class MicrobreakCoordinator:
    """Prevents synchronized AFK microbreaks across concurrent accounts (AUD-22 / D5 v2.1)."""

    def __init__(self, *, deconflict_step_sec: float = 45.0) -> None:
        self.deconflict_step_sec = deconflict_step_sec
        self._windows: dict[str, tuple[float, float]] = {}

    def reserve_microbreak(
        self,
        *,
        account_id: str,
        proposed_start_sec: float,
        duration_sec: float,
        active_slots: int,
    ) -> tuple[float, float]:
        max_concurrent_breaks = max(1, active_slots // 2)
        start = float(proposed_start_sec)
        dur = max(5.0, float(duration_sec))

        while True:
            end = start + dur
            overlapping = sum(
                1
                for other_acc, (o_s, o_e) in self._windows.items()
                if other_acc != account_id and not (end <= o_s or start >= o_e)
            )
            if overlapping < max_concurrent_breaks:
                self._windows[account_id] = (start, end)
                return (start, end)
            start += self.deconflict_step_sec
