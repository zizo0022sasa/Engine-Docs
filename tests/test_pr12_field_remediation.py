"""PR-12 Field-Proven Architectural Remediation Tests (T33, T34, T35, T36):
Verifies permanent structural resolution of all 4 Live Runtime Inspector findings:
- T33 (AUD-36 / ADR-009): Non-blocking ReactiveSafetyWatchdog (AST zero-sleep proof, pipelined Eat+Run+Walk
  within a single tick <50ms, 3-tick OSRS food cooldown, next-tick HP heal confirmation, Keep-Alive coalescing).
- T34 (AUD-37 / ADR-010): ConstrainedWaypointGraph + AccountCapabilitiesContext pruning Agility-21 GE Underwall
  Shortcut (3149, 3480) & Varrock South Dark Wizards (3226, 3369) for Level-3 accounts + stall rerouting.
- T35 (AUD-38 / ADR-011): Central 5-state SessionLifecycleManager [DISCONNECTED -> AT_LOGIN -> LOGGING_IN -> IN_GAME <-> IN_DANGER]
  isolating tasks & short-circuiting HTTP queries when logged out + exponential-backoff self-healing.
- T36 (AUD-39 / ADR-012): Strict AST decoupling between `osrs_ee/bots/` (`TaskModule`) and Core Infrastructure
  with zero-regression hot-swapping across bot modules.
"""

from __future__ import annotations

import ast
import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from osrs_ee.bots import (
    DirectiveType,
    SkillingGathererTaskModule,
    StrongholdTaskModule,
    TaskDirective,
    TaskModule,
    TickContext,
)
from osrs_ee.core import (
    ClientLifecycleState,
    ExecutionEngineKernel,
    ReactiveSafetyWatchdog,
    SafetyVerdict,
    SessionLifecycleManager,
)
from osrs_ee.navigation import (
    AccountCapabilitiesContext,
    ConstrainedWaypointGraph,
    NavigationEngine,
    WorldPoint,
)
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.sandbox.http_adapter import MicrobotHttpAdapter
from osrs_ee.state import AccountStatus, StateStore

REPO_ROOT = Path(__file__).resolve().parent.parent


def _setup_leased_gate(tmp_path: Path, account_id: str = "acc_01") -> tuple[StateStore, FakeGameAdapter, GameAdapterGate, dict[str, Any]]:
    store = StateStore(tmp_path / "state.db")
    store.register_account(
        account_id=account_id,
        auth_mode="LEGACY_CREDENTIALS",
        credential_ref=f"vault://accounts/{account_id}/auth",
        proxy_ref="vault://proxies/eg_01",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=101,
        initial_status=AccountStatus.READY,
    )
    lease = store.acquire_session_lease(
        account_id=account_id,
        slot_id=0,
        task_id="stage2_stronghold_10k",
        now_utc=datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc),
    )
    adapter = FakeGameAdapter(
        account_id=account_id,
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        inventory_items=[
            {"item_id": 333, "name": "Trout", "quantity": 3, "noted": False},
        ],
    )
    gate = GameAdapterGate(store, adapter)
    gate.bind_to_lease(
        account_id=account_id,
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
    )
    return store, adapter, gate, lease


def test_t33_non_blocking_reactive_watchdog_pipelined_eat_run_move_and_keepalive_coalescing(
    tmp_path: Path,
) -> None:
    """Defect #1 Remediation (AUD-36 / ADR-009):
    1. AST proves ZERO `time.sleep` calls anywhere in `osrs_ee/core/`, `osrs_ee/navigation/`, or `osrs_ee/bots/`.
    2. Under monster attack (`hp=5/10` at GE), kernel pipelines `CONSUME_FOOD + SET_RUN_MODE + WALK_TO_WORLD_POINT`
       in ONE tick (<50ms) without stopping movement, honors 3-tick food cooldown, and verifies HP gain on tick+1.
    3. `MicrobotHttpAdapter` reuses 1 Keep-Alive TCP connection and coalesces intra-tick reads.
    """
    # 1. Static AST proof: zero `sleep` calls in core, navigation, and bots!
    for subpkg in ("core", "navigation", "bots"):
        for py_file in sorted((REPO_ROOT / "osrs_ee" / subpkg).glob("*.py")):
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func_str = ast.unparse(node.func)
                    assert "sleep" not in func_str, f"Forbidden blocking sleep `{func_str}` in {py_file}"

    # 2. Live kernel combat-movement pipeline proof
    store, adapter, gate, lease = _setup_leased_gate(tmp_path)
    try:
        adapter.position = {"x": 3164, "y": 3487, "plane": 0}  # GRAND_EXCHANGE
        adapter.hp_current = 5
        adapter.hp_max = 10
        adapter.run_energy = 85
        adapter.run_enabled = False
        adapter.under_attack = True

        caps = AccountCapabilitiesContext("acc_01", skills={"agility": 1, "hitpoints": 10}, combat_level=3)
        kernel = ExecutionEngineKernel(
            gate=gate,
            account_id="acc_01",
            session_id=lease["session_id"],
            lease_id=lease["lease_id"],
            session_epoch=lease["session_epoch"],
            capabilities=caps,
        )
        bot = StrongholdTaskModule()

        t0 = time.perf_counter()
        tick1 = kernel.step_tick(bot, now_monotonic=100.0)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0

        assert elapsed_ms < 50.0, f"Tick pipeline took {elapsed_ms:.1f}ms (must be non-blocking <50ms)"
        assert tick1.lifecycle_state == ClientLifecycleState.IN_DANGER
        assert tick1.safety_verdict == SafetyVerdict.PIPELINED_EAT_AND_MOVE
        # Eat + Enable Run + Continuous Safe Walk ALL dispatched in the SAME tick without freezing!
        assert tick1.dispatched_commands == ("CONSUME_FOOD", "SET_RUN_MODE", "WALK_TO_WORLD_POINT")
        # Player moved immediately to the first safe corridor waypoint (Varrock West Bank, NOT stuck!)
        assert (adapter.position["x"], adapter.position["y"]) == (3185, 3436)
        # Note: On tick 101 right as food was eaten, HP is STILL 5 until tick 102 (real OSRS next-tick behavior!)
        assert adapter.hp_current == 5

        # Next tick (`game_tick` advances 101 -> 102): FakeGameAdapter applies pending +7 heal -> HP=10!
        # Watchdog confirms HP recovery reactively and transitions IN_DANGER -> IN_GAME!
        adapter.under_attack = False
        tick2 = kernel.step_tick(bot, now_monotonic=100.6)
        assert adapter.hp_current == 10
        assert tick2.lifecycle_state == ClientLifecycleState.IN_GAME
        assert tick2.safety_verdict == SafetyVerdict.NOMINAL
        assert "CONSUME_FOOD" not in tick2.dispatched_commands
        assert kernel.watchdog._pending_heal_from_tick is None
    finally:
        store.close()

    # 3. Persistent HTTP/1.1 Keep-Alive + Single-Tick Snapshot Coalescing proof
    endpoint_hits: list[str] = []

    class _KeepAliveHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            endpoint_hits.append(self.path)
            payload_map: dict[str, Any] = {
                "/status": {
                    "loginState": "LOGGED_IN",
                    "gameTick": 500,
                    "world": 308,
                    "position": {"x": 3164, "y": 3487, "plane": 0},
                    "hpCurrent": 10,
                    "hpMax": 10,
                    "runEnergy": 90,
                    "runEnabled": True,
                    "underAttack": False,
                },
                "/varps?id=281": {"value": 1000},
                "/bank": {"open": False, "pinPromptOpen": False},
                "/inventory": {"items": [{"id": 333, "name": "Trout", "quantity": 1}]},
            }
            body = json.dumps(payload_map.get(self.path, {})).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _KeepAliveHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        http_ad = MicrobotHttpAdapter(
            account_id="acc_01",
            session_id="sess_ka",
            session_epoch=1,
            port=srv.server_address[1],
            agent_token="secret_keepalive_token_12345",
            coalesce_window_ms=200,
        )
        # 3 reads within 120ms (<200ms coalesce window) -> ONLY 4 HTTP requests total over 1 single TCP socket!
        _ = http_ad.read_observation(monotonic_ms=1000)
        _ = http_ad.read_observation(monotonic_ms=1050)
        _ = http_ad.read_observation(monotonic_ms=1120)
        assert http_ad.tcp_connect_count == 1
        assert http_ad.http_request_count == 4
        assert len(endpoint_hits) == 4
        http_ad.kill_process_tree()
    finally:
        srv.shutdown()


def test_t34_constrained_navigation_blocks_ge_underwall_agility21_trap_and_reroutes_on_stall() -> None:
    """Defect #2 Remediation (AUD-37 / ADR-010):
    1. Fresh Level-3 F2P account (`Agility=1, Combat=3, is_member=False`) routing from GE `(3164, 3487)`
       to Barbarian Village `(3081, 3421)` NEVER touches `(3149, 3480)` Underwall Shortcut!
    2. Routing to Lumbridge `(3222, 3218)` prunes Varrock South Dark Wizards `(3226, 3369)` for Combat 3!
    3. High-level Member (`Agility=25, is_member=True`) unlocks the GE Underwall Shortcut dynamically.
    4. 3 stalled ticks on an obstacle automatically blacklists the jammed segment and reroutes!
    """
    graph = ConstrainedWaypointGraph()
    lvl3_f2p = AccountCapabilitiesContext(
        account_id="acc_fresh",
        skills={"agility": 1, "hitpoints": 10},
        combat_level=3,
        is_member=False,
    )

    # 1. GE -> Barbarian Village (Stronghold) on Agility 1 F2P
    plan_barb = graph.plan_safe_route(
        origin=WorldPoint(3164, 3487, 0),
        destination="BARBARIAN_VILLAGE",
        capabilities=lvl3_f2p,
    )
    coords_barb = {(wp.x, wp.y) for wp in plan_barb.waypoints}
    assert (3149, 3480) not in coords_barb, "Agility-1 account MUST NOT route through (3149, 3480) Underwall!"
    assert "shortcut_ge_underwall_agility_21" in plan_barb.pruned_edges
    assert plan_barb.walker_flags["allowShortcuts"] is False
    assert plan_barb.waypoints[-1].x == 3081 and plan_barb.waypoints[-1].y == 3421

    # 2. GE -> Lumbridge on Combat 3 F2P avoids Varrock South Dark Wizards (3226, 3369)
    plan_lumb = graph.plan_safe_route(
        origin="GRAND_EXCHANGE",
        destination="LUMBRIDGE_CASTLE_COURTYARD",
        capabilities=lvl3_f2p,
    )
    coords_lumb = {(wp.x, wp.y) for wp in plan_lumb.waypoints}
    assert (3226, 3369) not in coords_lumb
    assert "hazard_varrock_south_dark_wizards" in plan_lumb.pruned_edges

    # 3. Member with Agility 25 & Combat 60 DOES unlock the shortcut cleanly
    veteran_mem = AccountCapabilitiesContext(
        account_id="acc_vet",
        skills={"agility": 25, "hitpoints": 50},
        combat_level=60,
        is_member=True,
    )
    plan_vet = graph.plan_safe_route(
        origin="GRAND_EXCHANGE",
        destination="BARBARIAN_VILLAGE",
        capabilities=veteran_mem,
    )
    assert (3149, 3480) in {(wp.x, wp.y) for wp in plan_vet.waypoints}
    assert plan_vet.total_cost_tiles < plan_barb.total_cost_tiles

    # 4. Obstacle Stall Detector & Automatic Reroute on 3 stalled game ticks
    nav = NavigationEngine(graph, max_stalled_ticks_before_reroute=3)
    # Give veteran_mem a route via the shortcut, then simulate getting stuck at (3164, 3487) for 3 ticks!
    pos_ge = WorldPoint(3164, 3487, 0)
    cmd0 = nav.step_navigation_on_tick(
        current_pos=pos_ge, tick_advanced=True, capabilities=veteran_mem, destination="BARBARIAN_VILLAGE"
    )
    assert cmd0 is not None and (cmd0["x"], cmd0["y"]) == (3149, 3480)

    # Simulate 3 advancing ticks without making spatial progress -> triggers automatic reroute via Varrock West!
    for _ in range(2):
        _ = nav.step_navigation_on_tick(
            current_pos=pos_ge, tick_advanced=True, capabilities=veteran_mem, destination="BARBARIAN_VILLAGE"
        )
    rerouted_cmd = nav.step_navigation_on_tick(
        current_pos=pos_ge, tick_advanced=True, capabilities=veteran_mem, destination="BARBARIAN_VILLAGE"
    )
    assert rerouted_cmd is not None
    assert (rerouted_cmd["x"], rerouted_cmd["y"]) == (3185, 3436)  # Rerouted to VARROCK_WEST_BANK safe corridor!


def test_t35_session_lifecycle_state_machine_isolates_tasks_and_self_heals_with_exponential_backoff(
    tmp_path: Path,
) -> None:
    """Defect #3 Remediation (AUD-38 / ADR-011):
    1. Tracks `IN_GAME -> DISCONNECTED -> AT_LOGIN -> LOGGING_IN -> IN_GAME`.
    2. Isolates `TaskModule` completely (0 calls) during `DISCONNECTED` and `AT_LOGIN`, with exponential backoff.
    3. `MicrobotHttpAdapter` short-circuits when `loggedIn: false`, making 0 calls to `/inventory`, `/bank`, `/varps`.
    """
    store, adapter, gate, lease = _setup_leased_gate(tmp_path)
    task_eval_calls = 0

    class _CountingTask(TaskModule):
        @property
        def module_id(self) -> str:
            return "counting_probe"

        def evaluate_tick(self, ctx: TickContext) -> TaskDirective:
            nonlocal task_eval_calls
            task_eval_calls += 1
            return TaskDirective(directive_type=DirectiveType.WAIT_NEXT_TICK)

    try:
        caps = AccountCapabilitiesContext("acc_01")
        lifecycle = SessionLifecycleManager(base_backoff_sec=1.0, max_backoff_sec=16.0, max_heal_attempts=5)
        kernel = ExecutionEngineKernel(
            gate=gate,
            account_id="acc_01",
            session_id=lease["session_id"],
            lease_id=lease["lease_id"],
            session_epoch=lease["session_epoch"],
            capabilities=caps,
            lifecycle=lifecycle,
        )
        task = _CountingTask()

        # Tick 1: Healthy IN_GAME
        r1 = kernel.step_tick(task, now_monotonic=10.0)
        assert r1.lifecycle_state == ClientLifecycleState.IN_GAME
        assert task_eval_calls == 1

        # Tick 2: Transient socket disconnect -> transitions to DISCONNECTED, task isolated, backoff=1.0s
        adapter.transient_disconnect = True
        r2 = kernel.step_tick(task, now_monotonic=11.0)
        assert r2.lifecycle_state == ClientLifecycleState.DISCONNECTED
        assert task_eval_calls == 1  # Task NOT called!

        # Tick 3 (at t=12.1s, still disconnected): second failure -> backoff doubles to 2.0s!
        r3 = kernel.step_tick(task, now_monotonic=12.1)
        assert r3.lifecycle_state == ClientLifecycleState.DISCONNECTED
        assert lifecycle.backoff_history_sec[:2] == [1.0, 2.0]
        assert task_eval_calls == 1

        # Tick 4 (at t=15.0s): Socket recovers, but client was kicked to LOGIN_SCREEN (`logged_in=False`)!
        # Lifecycle transitions AT_LOGIN -> LOGGING_IN, triggers relogin, STILL isolates task for this tick!
        adapter.transient_disconnect = False
        adapter.logged_in = False
        r4 = kernel.step_tick(task, now_monotonic=15.0)
        assert r4.lifecycle_state == ClientLifecycleState.LOGGING_IN
        assert "TRIGGER_LOGIN" in r4.dispatched_commands
        assert task_eval_calls == 1  # Still isolated until IN_GAME verified on next tick!

        # Tick 5 (after backoff window at t=20.0s): Client is now LOGGED_IN -> resumes IN_GAME & resets attempts!
        r5 = kernel.step_tick(task, now_monotonic=20.0)
        assert r5.lifecycle_state == ClientLifecycleState.IN_GAME
        assert lifecycle.consecutive_heal_attempts == 0
        assert task_eval_calls == 2
    finally:
        store.close()

    # Also prove HTTP short-circuit when client is at Login Screen (`loggedIn: false`)
    queried_paths: list[str] = []

    class _LoggedOutHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            queried_paths.append(self.path)
            body = json.dumps({"loggedIn": False}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _LoggedOutHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        http_ad = MicrobotHttpAdapter(
            account_id="acc_01",
            session_id="sess_lo",
            session_epoch=1,
            port=srv.server_address[1],
            agent_token="secret_loggedout_token_1234",
            status_endpoint="/state",
        )
        obs = http_ad.read_observation(monotonic_ms=5000)
        assert obs["login_state"] == "LOGIN_SCREEN"
        # ZERO requests to /varps, /bank, or /inventory when loggedIn is False!
        assert queried_paths == ["/state"]
        http_ad.kill_process_tree()
    finally:
        srv.shutdown()


def test_t36_decoupled_micro_engines_ast_boundary_and_hot_swappable_bot_modules(
    tmp_path: Path,
) -> None:
    """Defect #4 Remediation (AUD-39 / ADR-012):
    1. AST firewall proves `osrs_ee/bots/*.py` imports ZERO transport, socket, database, sleep, or safety internals.
    2. Swapping `StrongholdTaskModule` <-> `SkillingGathererTaskModule` on the same `ExecutionEngineKernel`
       works seamlessly with zero coupling to safety or lifecycle internals.
    """
    forbidden_bot_imports = {
        "urllib",
        "http",
        "socket",
        "subprocess",
        "sqlite3",
        "time",
        "osrs_ee.core",
        "osrs_ee.sandbox",
        "osrs_ee.state",
        "osrs_ee.runtime",
    }

    for py_file in sorted((REPO_ROOT / "osrs_ee" / "bots").glob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for bad in forbidden_bot_imports:
                        assert not alias.name.startswith(bad), (
                            f"Architectural violation in {py_file.name}: forbidden import `{alias.name}`"
                        )
            elif isinstance(node, ast.ImportFrom) and node.module:
                for bad in forbidden_bot_imports:
                    assert not node.module.startswith(bad), (
                        f"Architectural violation in {py_file.name}: forbidden import from `{node.module}`"
                    )

    store, adapter, gate, lease = _setup_leased_gate(tmp_path)
    try:
        adapter.position = {"x": 3164, "y": 3487, "plane": 0}
        caps = AccountCapabilitiesContext("acc_01", skills={"agility": 1, "woodcutting": 35}, combat_level=3)
        kernel = ExecutionEngineKernel(
            gate=gate,
            account_id="acc_01",
            session_id=lease["session_id"],
            lease_id=lease["lease_id"],
            session_epoch=lease["session_epoch"],
            capabilities=caps,
        )

        # Run Module A: StrongholdTaskModule
        mod_stronghold = StrongholdTaskModule()
        res_a = kernel.step_tick(mod_stronghold, now_monotonic=50.0)
        assert res_a.task_directive is not None
        assert res_a.task_directive.directive_type == DirectiveType.NAVIGATE_TO
        assert res_a.task_directive.destination == "BARBARIAN_VILLAGE"

        # Hot-swap to Module B: SkillingGathererTaskModule on the SAME kernel without touching safety/lifecycle!
        adapter.position = {"x": 3092, "y": 3245, "plane": 0}  # At Draynor willows
        mod_woodcut = SkillingGathererTaskModule()
        res_b = kernel.step_tick(mod_woodcut, now_monotonic=51.0)
        assert res_b.task_directive is not None
        assert res_b.task_directive.directive_type == DirectiveType.INTERACT_OBJECT
        assert res_b.task_directive.payload["name"] == "Willow tree"
    finally:
        store.close()
