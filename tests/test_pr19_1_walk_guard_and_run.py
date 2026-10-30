"""`PR-19.1` field remediation — T75..T79.

Root cause under test (live `acc_02` run, tick 337):
`SUPPLY_AREA_UNREACHABLE:LUMBRIDGE_CHICKEN_COOP` after 120 NAVIGATE_TO ticks while the character was
**still making progress** (mid-way at `(3134, 3401)`, 10/10 HP). The guard counted *ticks* instead of
*progress*, so a healthy 210-tile GE -> Lumbridge trek was misread as a blocked route.

- T75: a 330-tick progressing trek must NEVER escalate (the exact field scenario, replayed).
- T76: a genuinely blocked bot (zero distance reduction) must still escalate, precisely.
- T77: run is re-enabled at >= 20% energy during travel, and NEVER spammed when already running.
- T78: the plane-1 spinning wheel is reached by CLIMB, never by demanding an unreachable walk.
- T79: end-to-end kernel run — Cook's Assistant egg fetched from the coop after a long walk with a
       drained run bar, proving the live failure mode cannot recur through the real dispatch path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from osrs_ee.bots.base import DirectiveType, TickContext
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.supply_procurement import (
    BALL_OF_WOOL_ID,
    CADAVA_BERRIES_ID,
    EGG_ID,
    WOOL_ID,
    ZeroTouchSupplyEngine,
)
from osrs_ee.core import ExecutionEngineKernel, ReactiveSafetyWatchdog, SessionLifecycleManager
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph, NavigationEngine
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.state import AccountStatus, StateStore

COOP = DEFAULT_WORLD_NODES["LUMBRIDGE_CHICKEN_COOP"]
GE = DEFAULT_WORLD_NODES["GRAND_EXCHANGE"]


def _ctx(
    pos: WorldPoint,
    *,
    inventory: tuple[dict[str, Any], ...] = (),
    run_energy_pct: int | None = None,
    run_enabled: bool = False,
) -> TickContext:
    return TickContext(
        game_tick=1,
        position=pos,
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=inventory,
        run_energy_pct=run_energy_pct,
        run_enabled=run_enabled,
        total_level=35,
    )


def _walk_towards(pos: WorldPoint, target: WorldPoint, step: int = 1) -> WorldPoint:
    """Chebyshev step toward the target (the exact movement the live /walk produces)."""
    dx = max(-step, min(step, target.x - pos.x))
    dy = max(-step, min(step, target.y - pos.y))
    return WorldPoint(pos.x + dx, pos.y + dy, pos.plane, pos.label)


# ---------------------------------------------------------------------------
# T75 — the exact field scenario must survive
# ---------------------------------------------------------------------------
def test_t75_progressing_long_walk_never_escalates() -> None:
    """T75 (`PR-19.1`): Replays the live failure — GE (3164,3487) -> chicken coop (3175,3303), 186
    tiles — and proves the engine now keeps dispatching `NAVIGATE_TO` for as long as the distance
    keeps shrinking. The old code aborted at 121 walk ticks with
    `SUPPLY_AREA_UNREACHABLE:LUMBRIDGE_CHICKEN_COOP`.
    """
    engine = ZeroTouchSupplyEngine()
    pos = GE
    walk_ticks = 0
    for tick in range(1, 340):
        d = engine.ensure(_ctx(pos), EGG_ID, 1)
        assert d is not None
        assert d.directive_type != DirectiveType.ESCALATE_HUMAN, (
            f"escalated at tick {tick} at {pos} — a progressing trek must never be aborted"
        )
        if d.directive_type == DirectiveType.NAVIGATE_TO:
            walk_ticks += 1
            pos = _walk_towards(pos, COOP)
        elif d.directive_type == DirectiveType.TAKE_GROUND_ITEM:
            break
    assert walk_ticks > 170, f"expected a long trek, saw {walk_ticks} walk ticks"
    assert pos.chebyshev_distance(COOP) <= 3, pos


def test_t75b_walk_guard_still_has_a_hard_no_progress_backstop() -> None:
    """T75b: the guard is progress-based, NOT disabled — a bot that never gets closer still stops."""
    engine = ZeroTouchSupplyEngine()
    frozen = WorldPoint(3134, 3401, 0)  # exactly where the live run was cancelled
    for _ in range(engine.max_walk_attempts + 5):
        d = engine.ensure(_ctx(frozen), EGG_ID, 1)
        assert d is not None
        if d.directive_type == DirectiveType.ESCALATE_HUMAN:
            break
    assert d is not None and d.directive_type == DirectiveType.ESCALATE_HUMAN
    assert d.reason == "SUPPLY_AREA_UNREACHABLE:LUMBRIDGE_CHICKEN_COOP"


# ---------------------------------------------------------------------------
# T76 — oscillation must not masquerade as progress
# ---------------------------------------------------------------------------
def test_t76_distance_oscillation_does_not_reset_the_guard() -> None:
    """T76 (`PR-19.1`): Only a NEW best distance counts as progress. Walking back and forth between two
    points must still escalate (otherwise a rubber-banding route could loop forever)."""
    engine = ZeroTouchSupplyEngine()
    a = WorldPoint(3134, 3401, 0)
    b = WorldPoint(3135, 3401, 0)
    first = engine.ensure(_ctx(a), EGG_ID, 1)
    assert first is not None and first.directive_type == DirectiveType.NAVIGATE_TO
    for i in range(engine.max_walk_attempts + 5):
        d = engine.ensure(_ctx(b if i % 2 == 0 else a), EGG_ID, 1)
        assert d is not None
        if d.directive_type == DirectiveType.ESCALATE_HUMAN:
            break
    assert d is not None and d.directive_type == DirectiveType.ESCALATE_HUMAN
    assert d.reason.startswith("SUPPLY_AREA_UNREACHABLE:"), d.reason


# ---------------------------------------------------------------------------
# T77 — run re-enable during travel
# ---------------------------------------------------------------------------
def test_t77_travel_never_returns_a_run_toggle_instead_of_a_walk() -> None:
    """T77 (`PR-19.2` contract): `_travel()` ALWAYS yields `NAVIGATE_TO`. In `PR-19.1` it could return
    `SET_RUN_MODE` instead of the walk, which livelocked the bot in the field whenever the client
    lagged in reporting `run_enabled=True`.
    """
    engine = ZeroTouchSupplyEngine()
    mid = WorldPoint(3134, 3401, 0)  # exactly where the live session stalled at tick 992
    for energy, enabled in ((25, False), (60, True), (19, False), (None, False), (100, False)):
        d = engine.ensure(_ctx(mid, run_energy_pct=energy, run_enabled=enabled), EGG_ID, 1)
        assert d is not None
        assert d.directive_type == DirectiveType.NAVIGATE_TO, (
            f"travel must never be displaced by a run toggle (energy={energy}, enabled={enabled})"
        )
        assert d.destination == "LUMBRIDGE_CHICKEN_COOP"


# ---------------------------------------------------------------------------
# T78 — plane transitions
# ---------------------------------------------------------------------------
def test_t78_upper_floor_supply_uses_climb_not_an_unreachable_walk() -> None:
    """T78 (`PR-19.1`): the Lumbridge spinning wheel is on plane 1. The engine must climb the north
    staircase, and must only emit `Spin` once actually standing on that floor.
    """
    engine = ZeroTouchSupplyEngine()
    inv = ({"item_id": WOOL_ID, "name": "Wool", "quantity": 20},)

    # Ground floor, at the stairs -> Climb.
    d = engine.ensure(_ctx(DEFAULT_WORLD_NODES["LUMBRIDGE_CASTLE_NORTH_STAIRS"], inventory=inv), BALL_OF_WOOL_ID, 20)
    assert d is not None and d.directive_type == DirectiveType.INTERACT_OBJECT, d
    assert d.payload["action"] == "Climb-up"

    # Still on plane 0 (e.g. climb failed): keep climbing, never spin from downstairs.
    for _ in range(engine.max_climb_attempts + 3):
        d = engine.ensure(_ctx(DEFAULT_WORLD_NODES["LUMBRIDGE_CASTLE_NORTH_STAIRS"], inventory=inv), BALL_OF_WOOL_ID, 20)
        assert d is not None
        if d.directive_type == DirectiveType.ESCALATE_HUMAN:
            break
        assert d.payload["action"] == "Climb-up"
    assert d is not None and d.directive_type == DirectiveType.ESCALATE_HUMAN
    assert d.reason.startswith("SUPPLY_CLIMB_FAILED:"), d.reason

    # Now on plane 1 -> spin.
    fresh = ZeroTouchSupplyEngine()
    wheel = DEFAULT_WORLD_NODES["LUMBRIDGE_SPINNING_WHEEL"]
    d = fresh.ensure(_ctx(wheel, inventory=inv), BALL_OF_WOOL_ID, 20)
    assert d is not None and d.directive_type == DirectiveType.INTERACT_OBJECT
    # `PR-20.2`: the wheel is now pinned to its LIVE-probed object id (14889), not name-matched.
    assert d.payload == {"name": "Spinning wheel", "action": "Spin", "id": 14889}


# ---------------------------------------------------------------------------
# T79 — end-to-end through the real kernel dispatch path
# ---------------------------------------------------------------------------
def test_t79_kernel_long_walk_with_drained_run_bar_reaches_the_egg(tmp_path: Path) -> None:
    """T79 (`PR-19.1`): full kernel run of the live scenario — empty bank, run bar drained to 12%,
    a 186-tile trek to the coop — the kernel must re-enable run mid-journey, keep walking, pick the
    egg up and never halt with `SUPPLY_AREA_UNREACHABLE`.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = StateStore(tmp_path / "pr19_1.db")
    store.register_account(
        account_id="acc_02",
        auth_mode="LEGACY_CREDENTIALS",
        credential_ref="vault://accounts/acc_02/auth",
        proxy_ref="vault://proxies/direct",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=202,
        initial_status=AccountStatus.READY,
    )
    lease = store.acquire_session_lease(
        account_id="acc_02",
        slot_id=0,
        task_id="stage1a_f2p_quests_10qp",
        now_utc=datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc),
        bypass_circadian=True,
    )
    adapter = FakeGameAdapter(
        account_id="acc_02",
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        skills={"hitpoints": 10},
        combat_level=3,
        quest_points=2,
        total_level=35,
        playtime_minutes=200,
        bank_items=[],
        inventory_items=[],
        ground_items=[{"item_id": EGG_ID, "name": "Egg", "quantity": 1}],
        walk_speed_tiles_per_tick=2,  # reproduce the live 210-tile trek, not an instant teleport
    )
    adapter.varps.update({29: 0, 179: 21, 144: 100, 62: 6})
    adapter.run_energy = 12
    adapter.run_enabled = False
    adapter.position = {"x": 3164, "y": 3487, "plane": 0}

    gate = GameAdapterGate(store, adapter)
    gate.bind_to_lease(
        account_id="acc_02",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
    )
    kernel = ExecutionEngineKernel(
        gate=gate,
        account_id="acc_02",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3, quest_points=2),
        lifecycle=SessionLifecycleManager(),
        watchdog=ReactiveSafetyWatchdog(),
        navigation=NavigationEngine(ConstrainedWaypointGraph()),
    )

    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    nav_ticks = 0
    run_dispatches = 0
    reached = False
    for tick in range(1, 900):
        if tick == 120:
            adapter.run_energy = 35  # bar recovered mid-journey
        res = kernel.step_tick(bot, now_monotonic=0.0)
        directive = res.task_directive
        if directive is not None and directive.directive_type == DirectiveType.ESCALATE_HUMAN:
            raise AssertionError(f"regressed to the field bug at tick {tick}: {directive.reason}")
        run_dispatches += sum(1 for c in res.dispatched_commands if c == "SET_RUN_MODE")
        if directive is not None and directive.directive_type == DirectiveType.NAVIGATE_TO:
            nav_ticks += 1
        if any(int(it["item_id"]) == EGG_ID for it in adapter.inventory_items):
            reached = True
            break

    assert reached, f"bot never picked the egg (pos={adapter.position})"
    assert nav_ticks > 60, f"expected a long trek, saw {nav_ticks} navigation ticks"
    assert run_dispatches >= 2, (
        "run must be booted at session start AND re-enabled when the bar recovered to >=20% "
        f"(saw {run_dispatches})"
    )
