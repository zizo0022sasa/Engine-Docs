"""Acceptance Tests for PR-12.1 Live Microbot AgentServer Wire-Compatibility Patch (T37..T39 / AUD-40..42).

Directly verifies the 3 live port-8081 compatibility gaps reported by the Live Field Inspector on `ade6240`:
- T37 (Gap #1 / AUD-40): Hitpoints & Vitals normalization (`hitpoints`/`maxHitpoints`, `hpCurrent`/`hpMax`,
  upstream `/skills?name=Hitpoints` `boostedLevel`/`level`, `0 HP` preservation, and `ExecutionEngineKernel`
  null-safety so `int(None)` `TypeError` is impossible).
- T38 (Gap #2 / AUD-41): End-to-end `WIDGET_CLICK` support across `command_intent.schema.json`, `GameAdapterGate`,
  `FakeGameAdapter`, `DirectiveType.CLICK_WIDGET`, and `MicrobotHttpAdapter` -> `POST /widgets/click`
  (including Cradle of Life Fancy Boots `groupId: 270, childId: 15` and `{"clicked": true}` response parsing).
- T39 (Gap #3 / AUD-42): Default `status_endpoint="/state"` out of the box for live Microbot `StateHandler`,
  plus sticky automatic `HTTP 404` fallback (`/state <-> /status`) for legacy mock servers.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from osrs_ee.bots.base import DirectiveType, TaskDirective, TaskModule, TickContext
from osrs_ee.core import ExecutionEngineKernel, ReactiveSafetyWatchdog, SessionLifecycleManager
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph, NavigationEngine
from osrs_ee.runtime.adapter_gate import ALLOWED_MVP_COMMANDS, PIPELINED_STREAM_COMMANDS, GameAdapterGate
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.sandbox.http_adapter import MicrobotHttpAdapter
from osrs_ee.state import AccountStatus, StateStore


def _make_leased_gate_for_adapter(
    tmp_path: Path, adapter: Any, account_id: str = "acc_01"
) -> tuple[StateStore, GameAdapterGate, dict[str, Any]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = StateStore(tmp_path / "pr12_1_state.db")
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
        now_utc=datetime(2026, 10, 27, 12, 0, tzinfo=timezone.utc),
    )
    adapter.session_id = lease["session_id"]
    adapter.session_epoch = lease["session_epoch"]
    gate = GameAdapterGate(store, adapter)
    gate.bind_to_lease(
        account_id=account_id,
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
    )
    return store, gate, lease


def _build_kernel(gate: GameAdapterGate, lease: dict[str, Any], caps: AccountCapabilitiesContext) -> ExecutionEngineKernel:
    return ExecutionEngineKernel(
        gate=gate,
        account_id=lease["account_id"],
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
        capabilities=caps,
        lifecycle=SessionLifecycleManager(),
        watchdog=ReactiveSafetyWatchdog(),
        navigation=NavigationEngine(ConstrainedWaypointGraph()),
    )


def test_t37_live_hitpoints_normalization_skills_fallback_and_kernel_null_safety(tmp_path: Path) -> None:
    """Gap #1 (AUD-40):
    1. Live `/state` returning `hitpoints` / `maxHitpoints` + `runEnergy` normalizes cleanly (no `hp_current: None`).
    2. Upstream `chsami/Microbot` `/state` (omitting HP) + `GET /skills?name=Hitpoints` (`boostedLevel: 4, level: 10`)
       normalizes cleanly, triggers `ReactiveSafetyWatchdog` emergency Eat + Run Orb click `(160, 27)` without `TypeError`.
    3. `hitpoints: 0` is preserved as `0` (never masked by Python truthiness), and malformed `None` vitals in
       `ExecutionEngineKernel` never raise `TypeError`.
    """
    state_mode = {"variant": "custom_hitpoints"}
    posted_commands: list[tuple[str, dict[str, Any]]] = []

    class _LiveMicrobotWireHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def _send_json(self, code: int, data: dict[str, Any]) -> None:
            raw = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            if parsed.path == "/state":
                if state_mode["variant"] == "custom_hitpoints":
                    self._send_json(
                        200,
                        {
                            "loggedIn": True,
                            "gameTick": 101,
                            "hitpoints": 8,
                            "maxHitpoints": 10,
                            "runEnergy": 75,
                            "player": {"position": {"x": 3081, "y": 3421, "plane": 0}, "interacting": False},
                        },
                    )
                elif state_mode["variant"] == "upstream_skills_only":
                    self._send_json(
                        200,
                        {
                            "loggedIn": True,
                            "gameState": "LOGGED_IN",
                            "gameTick": 102,
                            "player": {
                                "name": "TestRunner",
                                "combatLevel": 3,
                                "healthRatio": 12,
                                "healthScale": 30,
                                "interacting": True,
                                "position": {"x": 1860, "y": 5244, "plane": 0},
                            },
                        },
                    )
                else:
                    self._send_json(
                        200,
                        {
                            "loggedIn": True,
                            "gameTick": 103,
                            "hitpoints": 0,
                            "maxHitpoints": 10,
                            "runEnergy": 0,
                            "player": {"position": {"x": 3222, "y": 3218, "plane": 0}},
                        },
                    )
                return
            if parsed.path == "/skills" and qs.get("name") == ["Hitpoints"]:
                self._send_json(
                    200,
                    {
                        "count": 1,
                        "totalLevel": 32,
                        "skills": [{"name": "Hitpoints", "level": 10, "boostedLevel": 4, "xp": 1154}],
                    },
                )
                return
            if parsed.path == "/dialogue":
                self._send_json(200, {"inDialogue": False, "hasContinue": False, "hasOptions": False})
                return
            if parsed.path == "/varps" and qs.get("id") == ["281"]:
                self._send_json(200, {"id": 281, "value": 1000})
                return
            if parsed.path == "/bank":
                self._send_json(200, {"open": False, "pinPromptOpen": False})
                return
            if parsed.path == "/inventory":
                self._send_json(200, {"items": [{"id": 333, "name": "Trout", "quantity": 1}]})
                return
            self._send_json(404, {"error": f"Unknown path: {parsed.path}"})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            posted_commands.append((self.path, body))
            if self.path == "/inventory/interact":
                self._send_json(200, {"success": True, "action": body.get("action")})
                return
            if self.path == "/widgets/click":
                self._send_json(200, {"clicked": True, **body})
                return
            if self.path == "/walk":
                self._send_json(200, {"success": True, "walking": True})
                return
            # Upstream AgentServer has NO `/run` endpoint -> returns 404 so Run Orb fallback (160, 27) triggers!
            self._send_json(404, {"error": f"Unknown path: {self.path}"})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _LiveMicrobotWireHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    class _PassiveTask(TaskModule):
        @property
        def module_id(self) -> str:
            return "passive_probe"

        def evaluate_tick(self, ctx: TickContext) -> TaskDirective:
            return TaskDirective(DirectiveType.WAIT_NEXT_TICK)

    try:
        http_ad = MicrobotHttpAdapter(
            account_id="acc_01",
            session_id="sess_init",
            session_epoch=1,
            port=srv.server_address[1],
            agent_token="live_microbot_token_123456",
        )
        store, gate, lease = _make_leased_gate_for_adapter(tmp_path / "live_hp", http_ad)
        try:
            caps = AccountCapabilitiesContext(account_id="acc_01", skills={"agility": 1, "hitpoints": 10})
            kernel = _build_kernel(gate, lease, caps)

            # 1. Custom `/state` with `hitpoints: 8, maxHitpoints: 10, runEnergy: 75` -> ZERO TypeError!
            obs1 = http_ad.read_observation()
            assert obs1["vitals"]["hp_current"] == 8
            assert obs1["vitals"]["hp_max"] == 10
            assert obs1["vitals"]["run_energy"] == 75
            res1 = kernel.step_tick(_PassiveTask(), now_monotonic=1.0)
            assert res1.safety_verdict == "NOMINAL"

            # 2. Upstream `/state` + `/skills?name=Hitpoints` (`boostedLevel: 4, level: 10, interacting: True`)
            # Triggers emergency Eat (`POST /inventory/interact`) + Run Orb fallback (`POST /widgets/click` 160:27)!
            state_mode["variant"] = "upstream_skills_only"
            http_ad.invalidate_tick_cache()
            res2 = kernel.step_tick(_PassiveTask(), now_monotonic=2.0)
            assert res2.safety_verdict == "PIPELINED_EAT_AND_MOVE"
            assert "CONSUME_FOOD" in res2.dispatched_commands
            assert "SET_RUN_MODE" in res2.dispatched_commands
            assert ("/inventory/interact", {"id": 333, "action": "Eat"}) in posted_commands
            assert ("/widgets/click", {"groupId": 160, "childId": 27}) in posted_commands

            # 3. Edge case: `hitpoints: 0, runEnergy: 0` preserved as exact `0` (NOT treated as falsy!)
            state_mode["variant"] = "zero_hp_edge"
            http_ad.invalidate_tick_cache()
            obs3 = http_ad.read_observation()
            assert obs3["vitals"]["hp_current"] == 0
            assert obs3["vitals"]["run_energy"] == 0
        finally:
            store.close()
            http_ad.kill_process_tree()
    finally:
        srv.shutdown()

    # 4. Kernel defensive `_coalesce_int` guard even if a custom adapter injects None inside `vitals`
    fake = FakeGameAdapter(account_id="acc_01", session_id="s1", session_epoch=1, client_instance_id="jvm_null")
    store2, gate2, lease2 = _make_leased_gate_for_adapter(tmp_path / "null_guard", fake)
    try:
        orig_read = fake.read_observation

        def _null_vitals_read(*, monotonic_ms: int = 1000) -> dict[str, Any]:
            d = orig_read(monotonic_ms=monotonic_ms)
            d["vitals"] = {"hp_current": None, "hp_max": None, "run_energy": None}
            return d

        fake.read_observation = _null_vitals_read  # type: ignore[method-assign]
        kernel2 = _build_kernel(gate2, lease2, AccountCapabilitiesContext(account_id="acc_01", skills={"hitpoints": 10}))
        fake.advance_tick(1)
        safe_res = kernel2.step_tick(_PassiveTask(), now_monotonic=5.0)
        assert safe_res.safety_verdict == "NOMINAL"
    finally:
        store2.close()


def test_t38_widget_click_allowlist_schema_directive_and_live_http_routing(tmp_path: Path) -> None:
    """Gap #2 (AUD-41):
    1. `WIDGET_CLICK` is registered in `ALLOWED_MVP_COMMANDS`, `PIPELINED_STREAM_COMMANDS`, and JSON Schema.
    2. `TaskDirective(DirectiveType.CLICK_WIDGET, {"groupId": 270, "childId": 15})` (Stronghold Fancy Boots)
       dispatches `POST /widgets/click` and parses `{"clicked": true}` as `effect_verified=True`!
    3. Upstream `BankHandler` responses (`{"opened": true}`, `POST /bank/deposit` `{"all": true}`) are accepted!
    """
    assert "WIDGET_CLICK" in ALLOWED_MVP_COMMANDS
    assert "WIDGET_CLICK" in PIPELINED_STREAM_COMMANDS

    received_posts: list[tuple[str, dict[str, Any]]] = []

    class _WidgetAndBankWireHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def _send_json(self, code: int, data: dict[str, Any]) -> None:
            raw = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/state":
                self._send_json(
                    200,
                    {
                        "loggedIn": True,
                        "gameTick": 250,
                        "hitpoints": 10,
                        "maxHitpoints": 10,
                        "dialogueOpen": False,
                        "position": {"x": 2344, "y": 5214, "plane": 0},
                    },
                )
                return
            if self.path == "/varps?id=281":
                self._send_json(200, {"id": 281, "value": 1000})
                return
            if self.path == "/bank":
                self._send_json(200, {"open": True, "pinPromptOpen": False, "items": []})
                return
            if self.path == "/inventory":
                self._send_json(200, {"items": []})
                return
            self._send_json(404, {})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            received_posts.append((self.path, body))
            if self.path == "/widgets/click":
                # Exact upstream WidgetClickHandler.java response (`clicked: true`, NO `ok` or `success`!)
                self._send_json(200, {"clicked": True, **body})
                return
            if self.path == "/bank/open":
                # Exact upstream BankHandler.java response (`opened: true`)
                self._send_json(200, {"opened": True})
                return
            if self.path == "/bank/deposit":
                # Exact upstream BankHandler.java expects `{"all": true}`
                self._send_json(200, {"success": bool(body.get("all"))})
                return
            self._send_json(404, {"error": f"Unknown path: {self.path}"})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _WidgetAndBankWireHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    class _FancyBootsWidgetTask(TaskModule):
        @property
        def module_id(self) -> str:
            return "cradle_fancy_boots_widget"

        def evaluate_tick(self, ctx: TickContext) -> TaskDirective:
            return TaskDirective(DirectiveType.CLICK_WIDGET, payload={"groupId": 270, "childId": 15})

    try:
        http_ad = MicrobotHttpAdapter(
            account_id="acc_01",
            session_id="sess_w",
            session_epoch=1,
            port=srv.server_address[1],
            agent_token="widget_click_live_token_1234",
        )
        store, gate, lease = _make_leased_gate_for_adapter(tmp_path / "widget_bank", http_ad)
        try:
            kernel = _build_kernel(gate, lease, AccountCapabilitiesContext(account_id="acc_01"))
            tick_res = kernel.step_tick(_FancyBootsWidgetTask(), now_monotonic=1.0)
            assert "WIDGET_CLICK" in tick_res.dispatched_commands
            assert ("/widgets/click", {"groupId": 270, "childId": 15}) in received_posts

            # Also verify pipelined text-based widget click & bank wire compatibility
            pipe_res = gate.dispatch_pipelined_command(
                intent=kernel._make_intent("WIDGET_CLICK", {"text": "Yes"})
            )
            assert pipe_res["effect_status"] == "PIPELINED_NEXT_TICK"
            assert ("/widgets/click", {"text": "Yes"}) in received_posts

            open_res = gate.dispatch_verified_command(
                intent=kernel._make_intent("BANK_OPEN", {}),
                postcondition_checker=lambda post: post["bank"]["is_open"] is True,
            )
            assert open_res["effect_status"] == "VERIFIED"

            dep_res = gate.dispatch_verified_command(
                intent=kernel._make_intent("BANK_DEPOSIT_INVENTORY", {}),
                postcondition_checker=lambda _post: True,
            )
            assert dep_res["effect_status"] == "VERIFIED"
            assert ("/bank/deposit", {"all": True}) in received_posts
        finally:
            store.close()
            http_ad.kill_process_tree()
    finally:
        srv.shutdown()


def test_t39_default_state_endpoint_and_sticky_404_fallback_discovery() -> None:
    """Gap #3 (AUD-42):
    1. `MicrobotHttpAdapter` defaults `status_endpoint="/state"` out of the box (zero 404s on live Microbot 8081).
    2. Against a legacy mock server serving ONLY `/status` (404 on `/state`), automatically discovers `/status`
       and caches `_active_status_endpoint == "/status"` so subsequent calls make 0 requests to `/state`!
    """
    live_paths: list[str] = []

    class _StateOnlyHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            live_paths.append(self.path)
            if self.path == "/state":
                raw = json.dumps({"loggedIn": False}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            self.send_response(404)
            self.end_headers()

    srv_live = ThreadingHTTPServer(("127.0.0.1", 0), _StateOnlyHandler)
    threading.Thread(target=srv_live.serve_forever, daemon=True).start()
    try:
        ad_live = MicrobotHttpAdapter(
            account_id="acc_01",
            session_id="sess_state_default",
            session_epoch=1,
            port=srv_live.server_address[1],
            agent_token="state_default_token_123456",
        )
        assert ad_live.status_endpoint == "/state"
        probe = ad_live.probe_session_state()
        assert probe["reachable"] is True
        assert probe["login_state"] == "LOGIN_SCREEN"
        assert live_paths == ["/state"]  # Hit `/state` directly on 1st try with ZERO `/status` 404s!
        ad_live.kill_process_tree()
    finally:
        srv_live.shutdown()

    legacy_paths: list[str] = []

    class _LegacyStatusOnlyHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def do_GET(self) -> None:  # noqa: N802
            legacy_paths.append(self.path)
            if self.path == "/status":
                raw = json.dumps({"loginState": "LOGIN_SCREEN"}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            self.send_response(404)
            self.end_headers()

    srv_legacy = ThreadingHTTPServer(("127.0.0.1", 0), _LegacyStatusOnlyHandler)
    threading.Thread(target=srv_legacy.serve_forever, daemon=True).start()
    try:
        ad_legacy = MicrobotHttpAdapter(
            account_id="acc_01",
            session_id="sess_legacy_fallback",
            session_epoch=1,
            port=srv_legacy.server_address[1],
            agent_token="legacy_fallback_token_123456",
        )
        # First call probes `/state` (404), auto-discovers `/status` (200), and caches it!
        p1 = ad_legacy.probe_session_state()
        assert p1["login_state"] == "LOGIN_SCREEN"
        assert ad_legacy._active_status_endpoint == "/status"
        # Second call goes straight to `/status` with ZERO `/state` retries!
        p2 = ad_legacy.probe_session_state()
        assert p2["login_state"] == "LOGIN_SCREEN"
        assert legacy_paths == ["/state", "/status", "/status"]
        ad_legacy.kill_process_tree()
    finally:
        srv_legacy.shutdown()
