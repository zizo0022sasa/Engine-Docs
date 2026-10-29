"""`PR-19.2` field remediation — T80..T82 (movement-first invariant).

Field incident #2 (live `acc_02`, ~992 ticks, stalled at Lumbridge `(3190, 3273)`):
`PR-19.1` let `_travel()` return `SET_RUN_MODE` **instead of** `NAVIGATE_TO`. When the client lagged in
reporting `runEnabled=True`, the engine re-issued the toggle every tick, the character never walked, and
the (correct) progress guard finally declared the route unreachable.

Invariant under test: **movement is never traded for any side action.** A tick that can walk must walk;
run enabling rides alongside as a throttled pipelined command.

- T80: with run permanently un-reported / stuck-off, the bot still walks the whole route to the coop.
- T81: `WALK_TO_WORLD_POINT` and `SET_RUN_MODE` are dispatched in the SAME tick when the bar recovers.
- T82: a lagging client cannot be spammed — assist dispatches are bounded by the cooldown, and a
       duplicate toggle in the same tick (watchdog already handled it) is never emitted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from osrs_ee.bots.base import DirectiveType, TickContext
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.supply_procurement import EGG_ID, ZeroTouchSupplyEngine
from osrs_ee.core import ExecutionEngineKernel, ReactiveSafetyWatchdog, SessionLifecycleManager
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph, NavigationEngine
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.state import AccountStatus, StateStore

COOP = DEFAULT_WORLD_NODES["LUMBRIDGE_CHICKEN_COOP"]
SHEEP_FARM = DEFAULT_WORLD_NODES["LUMBRIDGE_SHEEP_FARM"]


def _ctx(pos: WorldPoint, *, energy: int | None = None, enabled: bool = False) -> TickContext:
    return TickContext(
        game_tick=1,
        position=pos,
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=(),
        run_energy_pct=energy,
        run_enabled=enabled,
        total_level=35,
    )


def _kernel(tmp_path: Path, *, run_energy: int, walk_speed: int = 2) -> tuple[FakeGameAdapter, ExecutionEngineKernel, StateStore]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = StateStore(tmp_path / "pr19_2.db")
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
        now_utc=datetime(2026, 10, 29, 15, 0, tzinfo=timezone.utc),
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
        walk_speed_tiles_per_tick=walk_speed,
    )
    adapter.varps.update({29: 0, 179: 21, 144: 100, 62: 6})
    adapter.run_energy = run_energy
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
    return adapter, kernel, store


# ---------------------------------------------------------------------------
# T80 — the exact field livelock cannot recur
# ---------------------------------------------------------------------------
def test_t80_bot_keeps_walking_even_if_run_state_never_updates(tmp_path: Path) -> None:
    """T80 (`PR-19.2`): the field bug was a livelock — the client never reported `runEnabled=True`, so
    `PR-19.1` re-sent `SET_RUN_MODE` forever and stopped moving. Now the bot must walk the full
    GE -> coop route regardless of what the run flag says.
    """
    adapter, kernel, _store = _kernel(tmp_path / "t80", run_energy=100)

    class _StuckRunAdapter(FakeGameAdapter):
        pass

    # Freeze the reported run flag to False forever: /run succeeds at the wire level but the
    # observation snapshot never reflects it (exactly the lagging-client scenario).
    original_observe = adapter.read_observation

    def _observe_with_stale_run(*, monotonic_ms: int = 1000) -> dict[str, Any]:
        snap = original_observe(monotonic_ms=monotonic_ms)
        snap["vitals"]["run_enabled"] = False
        return snap

    adapter.read_observation = _observe_with_stale_run  # type: ignore[method-assign]

    start = WorldPoint(3164, 3487, 0)
    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    walked = 0
    reached = False
    for tick in range(1, 900):
        res = kernel.step_tick(bot, now_monotonic=0.0)
        directive = res.task_directive
        assert directive is None or directive.directive_type != DirectiveType.ESCALATE_HUMAN, (
            f"livelock regressed at tick {tick}: {directive.reason if directive else ''}"
        )
        walked += sum(1 for c in res.dispatched_commands if c == "WALK_TO_WORLD_POINT")
        if any(int(it["item_id"]) == EGG_ID for it in adapter.inventory_items):
            reached = True
            break

    assert reached, f"bot never reached the egg (pos={adapter.position})"
    final = WorldPoint(int(adapter.position["x"]), int(adapter.position["y"]), int(adapter.position["plane"]))
    assert start.chebyshev_distance(final) > 150, f"bot barely moved: {final}"
    assert final.chebyshev_distance(COOP) <= 3, f"ended away from the coop: {final}"
    assert walked >= 5, f"expected real navigation dispatches, saw {walked}"


# ---------------------------------------------------------------------------
# T81 — run rides alongside the walk in the same tick
# ---------------------------------------------------------------------------
def test_t81_run_assist_is_dispatched_alongside_the_walk_in_the_same_tick(tmp_path: Path) -> None:
    """T81 (`PR-19.2`): when the bar recovers mid-journey, the very same tick must contain BOTH
    `WALK_TO_WORLD_POINT` and `SET_RUN_MODE` (pipelined side channel), never one instead of the other.
    """
    adapter, kernel, _store = _kernel(tmp_path / "t81", run_energy=0)
    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    assert kernel.travel_run_assist_min_energy == 20

    joint_ticks = 0
    for tick in range(1, 200):
        if tick == 40:
            adapter.run_energy = 45   # bar recovered while the bot is still walking
        res = kernel.step_tick(bot, now_monotonic=0.0)
        cmds = res.dispatched_commands
        if "WALK_TO_WORLD_POINT" in cmds and "SET_RUN_MODE" in cmds:
            joint_ticks += 1
        if any(int(it["item_id"]) == EGG_ID for it in adapter.inventory_items):
            break
    assert joint_ticks >= 1, "run assist must be pipelined in the same tick as the walk, not instead of it"


# ---------------------------------------------------------------------------
# T82 — throttling / no duplicate toggles
# ---------------------------------------------------------------------------
def test_t82_run_assist_is_throttled_and_never_double_toggles(tmp_path: Path) -> None:
    """T82 (`PR-19.2`): with a permanently stale run flag and a full bar, `SET_RUN_MODE` must stay
    bounded by the cooldown (no per-tick spam), and a tick in which the watchdog already enabled run
    must never carry a second toggle.
    """
    adapter, kernel, _store = _kernel(tmp_path / "t82", run_energy=100)
    original_observe = adapter.read_observation

    def _observe_with_stale_run(*, monotonic_ms: int = 1000) -> dict[str, Any]:
        snap = original_observe(monotonic_ms=monotonic_ms)
        snap["vitals"]["run_enabled"] = False
        return snap

    adapter.read_observation = _observe_with_stale_run  # type: ignore[method-assign]

    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    toggles = 0
    max_toggles_in_a_tick = 0
    ticks = 0
    for tick in range(1, 200):
        res = kernel.step_tick(bot, now_monotonic=0.0)
        ticks += 1
        n = sum(1 for c in res.dispatched_commands if c == "SET_RUN_MODE")
        toggles += n
        max_toggles_in_a_tick = max(max_toggles_in_a_tick, n)
        if any(int(it["item_id"]) == EGG_ID for it in adapter.inventory_items):
            break

    assert max_toggles_in_a_tick <= 1, "a tick must never carry two SET_RUN_MODE dispatches"
    cooldown = kernel.travel_run_assist_cooldown_ticks
    assert toggles <= (ticks // cooldown) + 3, (
        f"{toggles} toggles in {ticks} ticks exceeds the {cooldown}-tick cooldown budget"
    )
