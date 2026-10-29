"""Hermetic Acceptance & Live-Wire Rehearsal Tests for PR-15 (`tests/test_pr15_stronghold_live_hardening.py` — T47..T50).

Covers all 4 field-mandated remediations from Live Run 1 & Live Run 2 (`HANDOFF.md`):
- T47: Auto-Enable Run Mode (`160:27`) with `VarPlayer 173` (`/varps?id=173`) ground-truth idempotency
  (never toggling Run back OFF!) + `DialogueHandler` `Widget 270` (`hasContinue=false, hasOptions=false`)
  filter so `Cradle of Life` -> `270:15` (*Fancy boots*) never loops on `CONTINUE_DIALOGUE`.
- T48: Autonomous Pre-Flight Food & Bank Staging from Lumbridge (`DRAYNOR_BANK` + `BankHandler` `id`-only
  schema normalization) AND zero-touch empty-bank fallback (claiming free `Small fishing net` `303`,
  netting `Raw shrimps` at `LUMBRIDGE_SWAMP_FISHING`, cooking on open-air `BARBARIAN_RIVER_FIRE` `270:14`).
- T49: Kernel Monotonic Tick Progression when `obs["game_tick"] is None` (`StateHandler.java`), unblocking
  stationary `/walk` re-dispatch (without `NoSafeRouteError`) and multi-food 3-tick Ankou survival (`eat <= 8`).
- T50: Full Live Run 3 Rehearsal for `acc_02` (`zaza266`: `v2309=v2310=v2311=1, v2312=0` from Lumbridge
  `(3221, 3218, 0)`), verifying Floor 1–3 shortcut portals (`20786`, `19005`, `23707`), `chebyshev_distance <= 2`
  `Portal of Death` passage across `(2361, 5232)` & `(2361, 5235)` with `/objects` ID disambiguation
  (`23727` vs `23728`), `Fancy boots` (`9005`) claim, `Bone chain` (`23732`) exit, and GE bank deposit.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from osrs_ee.bots import DirectiveType, StrongholdTaskModule
from osrs_ee.core import ExecutionEngineKernel, ReactiveSafetyWatchdog, SessionLifecycleManager
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph, NavigationEngine
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.sandbox.http_adapter import MicrobotHttpAdapter
from osrs_ee.state import AccountStatus, StateStore


def _make_leased_http_env(
    tmp_dir: Path,
    port: int,
    *,
    account_id: str = "acc_02",
) -> tuple[StateStore, MicrobotHttpAdapter, GameAdapterGate, ExecutionEngineKernel]:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    store = StateStore(tmp_dir / "pr15_http.db")
    store.register_account(
        account_id=account_id,
        auth_mode="LEGACY_CREDENTIALS",
        credential_ref=f"vault://accounts/{account_id}/auth",
        proxy_ref="vault://proxies/direct",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=202,
        initial_status=AccountStatus.READY,
    )
    lease = store.acquire_session_lease(
        account_id=account_id,
        slot_id=0,
        task_id="stage2_stronghold_10k",
        now_utc=datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc),
        bypass_circadian=True,
    )
    http_ad = MicrobotHttpAdapter(
        account_id=account_id,
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        port=port,
        agent_token="pr15_hermetic_token_123456",
        coalesce_window_ms=0,
    )
    gate = GameAdapterGate(store, http_ad)  # type: ignore[arg-type]
    gate.bind_to_lease(
        account_id=account_id,
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
    )
    caps = AccountCapabilitiesContext(
        account_id=account_id,
        skills={"agility": 1, "hitpoints": 10},
        combat_level=3,
        is_member=False,
    )
    kernel = ExecutionEngineKernel(
        gate=gate,
        account_id=account_id,
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
        capabilities=caps,
        lifecycle=SessionLifecycleManager(),
        watchdog=ReactiveSafetyWatchdog(),
        navigation=NavigationEngine(ConstrainedWaypointGraph()),
    )
    return store, http_ad, gate, kernel


def _make_leased_fake_env(
    tmp_dir: Path,
    *,
    bank_items: list[dict[str, Any]] | None = None,
    inventory_items: list[dict[str, Any]] | None = None,
) -> tuple[StateStore, FakeGameAdapter, GameAdapterGate, ExecutionEngineKernel]:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    store = StateStore(tmp_dir / "pr15_fake.db")
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
        task_id="stage2_stronghold_10k",
        now_utc=datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc),
        bypass_circadian=True,
    )
    adapter = FakeGameAdapter(
        account_id="acc_02",
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        skills={"hitpoints": 10, "agility": 1, "fishing": 1, "cooking": 1},
        bank_items=bank_items or [],
        inventory_items=inventory_items or [],
    )
    gate = GameAdapterGate(store, adapter)
    gate.bind_to_lease(
        account_id="acc_02",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
    )
    caps = AccountCapabilitiesContext(
        account_id="acc_02",
        skills={"agility": 1, "hitpoints": 10},
        combat_level=3,
        is_member=False,
    )
    kernel = ExecutionEngineKernel(
        gate=gate,
        account_id="acc_02",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
        capabilities=caps,
        lifecycle=SessionLifecycleManager(),
        watchdog=ReactiveSafetyWatchdog(),
        navigation=NavigationEngine(ConstrainedWaypointGraph()),
    )
    return store, adapter, gate, kernel


def test_t47_live_varp173_idempotent_run_orb_and_widget270_dialogue_filter(tmp_path: Path) -> None:
    """PR-15 Req #1 + Cradle Widget 270 wire compatibility:
    1. Live `/state` omits `gameTick` and `runEnabled`; `/varps?id=173` exposes `VarPlayer.RUNNING`.
    2. Clicking `160:27` toggles `Varp 173` (`0 <-> 1`). Kernel turns Run ON at session start and NEVER
       clicks `160:27` again while `Varp 173 == 1` (even under combat or on Floor 4!).
    3. When `Varp 173` resets to `0`, kernel immediately re-clicks `160:27` to restore Run.
    4. When Cradle of Life opens chatbox `Widget 270` (`inDialogue=True, hasContinue=False, hasOptions=False`),
       `dialogue_open` stays `False` so `StrongholdTaskModule` clicks `270:15` (*Fancy boots*) cleanly.
    """
    sim = {
        "varp_173": 0,
        "run_orb_clicks": 0,
        "fancy_boots_clicks": 0,
        "pos": {"x": 2343, "y": 5216, "plane": 0},  # Floor 4 Cradle room (dist 1 from 2343,5215)
        "v2312": 0,
        "widget_270_open": False,
        "interacting": False,
    }

    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def _json(self, code: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload).encode("utf-8")
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
                # Exact upstream StateHandler.java shape: NO gameTick, NO runEnabled!
                self._json(
                    200,
                    {
                        "loggedIn": True,
                        "gameState": "LOGGED_IN",
                        "player": {
                            "name": "zaza266",
                            "combatLevel": 3,
                            "healthRatio": 30,
                            "healthScale": 30,
                            "interacting": sim["interacting"],
                            "position": dict(sim["pos"]),
                        },
                    },
                )
                return
            if parsed.path == "/skills":
                self._json(
                    200,
                    {"count": 1, "totalLevel": 32, "skills": [{"name": "Hitpoints", "level": 10, "boostedLevel": 10}]},
                )
                return
            if parsed.path == "/dialogue":
                if sim["widget_270_open"]:
                    # Exact upstream DialogueHandler.java when chatbox skill/boots menu 270 is open!
                    self._json(200, {"inDialogue": True, "hasContinue": False, "hasOptions": False})
                else:
                    self._json(200, {"inDialogue": False, "hasContinue": False, "hasOptions": False})
                return
            if parsed.path == "/varps":
                vid = int(qs["id"][0])
                if vid == 173:
                    self._json(200, {"varpId": 173, "value": sim["varp_173"]})
                elif vid == 281:
                    self._json(200, {"varpId": 281, "value": 1000})
                else:
                    self._json(200, {"varpId": vid, "value": 0})
                return
            if parsed.path == "/varbits":
                vbid = int(qs["id"][0])
                val = sim["v2312"] if vbid == 2312 else (1 if vbid in (2309, 2310, 2311) else 0)
                self._json(200, {"varbitId": vbid, "value": val})
                return
            if parsed.path == "/bank":
                self._json(200, {"open": False, "count": 0, "items": []})
                return
            if parsed.path == "/inventory":
                self._json(200, {"items": [{"id": 333, "name": "Trout", "quantity": 1}] * 8})
                return
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            if self.path == "/widgets/click":
                if body.get("groupId") == 160 and body.get("childId") == 27:
                    sim["run_orb_clicks"] += 1
                    # Real OSRS toggle orb behavior: clicking when 1 turns Run OFF!
                    sim["varp_173"] = 0 if sim["varp_173"] == 1 else 1
                elif body.get("groupId") == 270 and body.get("childId") == 15:
                    sim["fancy_boots_clicks"] += 1
                    sim["widget_270_open"] = False
                    sim["v2312"] = 1
                self._json(200, {"clicked": True})
                return
            if self.path == "/objects/interact":
                if body.get("id") == 23731:
                    sim["widget_270_open"] = True
                elif body.get("id") == 23732:
                    sim["pos"] = {"x": 3081, "y": 3421, "plane": 0}
                self._json(200, {"success": True})
                return
            if self.path == "/walk":
                self._json(200, {"success": True})
                return
            self._json(404, {"error": f"Unknown POST {self.path}"})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    store, http_ad, _gate, kernel = _make_leased_http_env(tmp_path, srv.server_address[1])
    try:
        bot = StrongholdTaskModule()
        # Tick 1: Enables Run (`160:27` -> Varp 173 = 1) + Searches Cradle of Life (`23731` at dist=1 <= 2!)
        r1 = kernel.step_tick(bot, now_monotonic=1.0)
        assert "SET_RUN_MODE" in r1.dispatched_commands
        assert sim["varp_173"] == 1
        assert sim["run_orb_clicks"] == 1
        assert sim["widget_270_open"] is True

        # Tick 2: Widget 270 is open (`inDialogue=True, hasContinue=False, hasOptions=False`).
        # Must NOT emit CONTINUE_DIALOGUE AND must NOT click 160:27 a second time!
        sim["interacting"] = True
        r2 = kernel.step_tick(bot, now_monotonic=2.0)
        assert r2.task_directive is not None
        assert r2.task_directive.directive_type == DirectiveType.CLICK_WIDGET
        assert sim["fancy_boots_clicks"] == 1
        assert sim["v2312"] == 1
        assert sim["varp_173"] == 1
        assert sim["run_orb_clicks"] == 1, "Run Orb was clicked while already ON (would toggle Run OFF)!"

        # Simulate energy drain resetting Varp 173 -> 0, then recharging: Run must re-enable automatically!
        sim["varp_173"] = 0
        r3 = kernel.step_tick(bot, now_monotonic=3.0)
        assert "SET_RUN_MODE" in r3.dispatched_commands
        assert sim["varp_173"] == 1
        assert sim["run_orb_clicks"] == 2
    finally:
        http_ad.kill_process_tree()
        store.close()
        srv.server_close()


def test_t48_preflight_lumbridge_draynor_bank_staging_and_zero_bank_shrimp_fallback(tmp_path: Path) -> None:
    """PR-15 Req #2:
    1. Starting at Lumbridge `(3221, 3218, 0)` with `0` food and live `BankHandler` returning `id`-only
       `bank.items` (`[{"id": 333, "name": "Trout", "quantity": 15}]`), bot routes to `DRAYNOR_BANK`
       `(3092, 3245, 0)`, withdraws `10x Trout`, closes bank, and ONLY THEN walks to `BARBARIAN_VILLAGE`.
    2. When BOTH inventory and Draynor Bank hold `0` food, bot autonomously walks to `LUMBRIDGE_SWAMP_FISHING`,
       claims free `Small fishing net` (`303`) from `Fishing tutor`, nets `Raw shrimps`, cooks them on the
       permanent Barbarian Village `BARBARIAN_RIVER_FIRE` `(3078, 3445, 0)` (`270:14`), and enters `BARBARIAN_VILLAGE` (`20790`).
    """
    # --- Part A: Live HTTP BankHandler (`id` without `item_id`/`noted`) at Draynor Bank ---
    bank_state = {
        "pos": {"x": 3221, "y": 3218, "plane": 0},
        "bank_open": False,
        "bank_trout": 15,
        "inv_trout": 0,
        "entered_hole": False,
    }

    class _BankWireHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def _json(self, code: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload).encode("utf-8")
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
                self._json(
                    200,
                    {
                        "loggedIn": True,
                        "gameState": "LOGGED_IN",
                        "player": {"position": dict(bank_state["pos"]), "interacting": False},
                    },
                )
                return
            if parsed.path == "/skills":
                self._json(200, {"skills": [{"name": "Hitpoints", "level": 10, "boostedLevel": 10}]})
                return
            if parsed.path == "/dialogue":
                self._json(200, {"inDialogue": False, "hasContinue": False, "hasOptions": False})
                return
            if parsed.path == "/varps":
                vid = int(qs["id"][0])
                self._json(200, {"varpId": vid, "value": 1 if vid == 173 else 1000})
                return
            if parsed.path == "/varbits":
                self._json(200, {"varbitId": int(qs["id"][0]), "value": 0})
                return
            if parsed.path == "/bank":
                # Exact upstream BankHandler.java shape: `id`, `name`, `quantity` (NO `item_id`, NO `noted`)!
                items = (
                    [{"id": 333, "name": "Trout", "quantity": bank_state["bank_trout"]}]
                    if bank_state["bank_trout"] > 0
                    else []
                )
                self._json(200, {"open": bank_state["bank_open"], "count": len(items), "items": items})
                return
            if parsed.path == "/inventory":
                inv = [{"id": 333, "name": "Trout", "quantity": 1} for _ in range(bank_state["inv_trout"])]
                self._json(200, {"items": inv})
                return
            self._json(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            if self.path == "/walk":
                bank_state["pos"] = {"x": int(body["x"]), "y": int(body["y"]), "plane": int(body.get("plane", 0))}
                self._json(200, {"success": True})
                return
            if self.path == "/bank/open":
                bank_state["bank_open"] = True
                self._json(200, {"opened": True})
                return
            if self.path == "/bank/close":
                bank_state["bank_open"] = False
                self._json(200, {"closed": True})
                return
            if self.path == "/bank/withdraw":
                assert body["id"] == 333
                qty = min(int(body["quantity"]), bank_state["bank_trout"])
                bank_state["bank_trout"] -= qty
                bank_state["inv_trout"] += qty
                self._json(200, {"success": True, "quantity": qty})
                return
            if self.path == "/objects/interact" and body.get("id") == 20790:
                bank_state["entered_hole"] = True
                self._json(200, {"success": True})
                return
            self._json(404, {"error": self.path})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _BankWireHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    store_a, http_ad_a, _gate_a, kernel_a = _make_leased_http_env(tmp_path / "part_a", srv.server_address[1])
    try:
        bot_a = StrongholdTaskModule(min_required_food=6, target_food_count=10)
        for step in range(1, 30):
            kernel_a.step_tick(bot_a, now_monotonic=float(step))
            if bank_state["entered_hole"]:
                break
        assert bank_state["inv_trout"] == 10, f"Expected 10 staged Trout before entry, got {bank_state['inv_trout']}"
        assert bank_state["bank_open"] is False
        assert bank_state["entered_hole"] is True
    finally:
        http_ad_a.kill_process_tree()
        store_a.close()
        srv.server_close()

    # --- Part B: Zero-Food Bank -> Autonomous Swamp Net + River Fire Cook Fallback ---
    store_b, adapter_b, _gate_b, kernel_b = _make_leased_fake_env(tmp_path / "part_b", bank_items=[], inventory_items=[])
    try:
        adapter_b.position = {"x": 3221, "y": 3218, "plane": 0}
        bot_b = StrongholdTaskModule(min_required_food=6, target_food_count=8)
        entered_with_cooked_shrimps = False
        orig_exec = adapter_b.execute_raw_command

        def _sim_gather_world(cmd_type: str, payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal entered_with_cooked_shrimps
            res = orig_exec(cmd_type, payload)
            if cmd_type == "INTERACT_NPC":
                if payload.get("name") == "Fishing tutor":
                    adapter_b._add_to_inventory(
                        {"item_id": 303, "name": "Small fishing net", "quantity": 1, "noted": False}
                    )
                elif payload.get("name") == "Fishing spot" and payload.get("action") == "Net":
                    adapter_b._add_to_inventory(
                        {"item_id": 317, "name": "Raw shrimps", "quantity": 5, "noted": False}
                    )
            elif cmd_type == "WIDGET_CLICK" and payload.get("groupId") == 270 and payload.get("childId") == 14:
                # Convert Raw shrimps -> 8 Cooked Shrimps + 2 Burnt shrimp (realistic Cooking lvl 1 burn!)
                adapter_b.inventory_items = [
                    it for it in adapter_b.inventory_items if str(it.get("name", "")).casefold() != "raw shrimps"
                ]
                adapter_b._add_to_inventory({"item_id": 315, "name": "Shrimps", "quantity": 8, "noted": False})
                adapter_b._add_to_inventory({"item_id": 7954, "name": "Burnt shrimp", "quantity": 2, "noted": False})
            elif cmd_type == "INTERACT_OBJECT" and payload.get("id") == 20790:
                entered_with_cooked_shrimps = True
            return res

        adapter_b.execute_raw_command = _sim_gather_world  # type: ignore[method-assign]
        for step in range(1, 70):
            kernel_b.step_tick(bot_b, now_monotonic=float(step))
            if entered_with_cooked_shrimps:
                break

        assert entered_with_cooked_shrimps is True
        cooked_qty = StrongholdTaskModule._count_edible_food(tuple(adapter_b.inventory_items))
        assert cooked_qty >= 6
    finally:
        store_b.close()


def test_t49_null_gametick_monotonic_progression_stall_recovery_and_ankou_eat_threshold(tmp_path: Path) -> None:
    """PR-15 Req #3 + Ankou max-hit 8 survival:
    1. When `obs["game_tick"] is None` every cycle, `ExecutionEngineKernel` advances `_internal_tick_counter`
       monotonically (`1, 2, 3...`).
    2. If the player stands stationary for 3 ticks on the single Level-3 safe corridor, `NavigationEngine`
       re-dispatches `WALK_TO_WORLD_POINT` cleanly WITHOUT raising `NoSafeRouteError`.
    3. Underground (`y >= 5000`) at `8/10 HP` (Ankou max hit = 8), Watchdog eats food #1 immediately,
       confirms consumption via food count drop even when hit simultaneously back down to `6/10 HP`,
       and eats food #2 cleanly 3 synthetic ticks later!
    """
    store, adapter, _gate, kernel = _make_leased_fake_env(
        tmp_path,
        inventory_items=[{"item_id": 333, "name": "Trout", "quantity": 8, "noted": False}],
    )
    try:
        bot = StrongholdTaskModule()
        adapter.position = {"x": 3222, "y": 3218, "plane": 0}

        # Force read_observation() to return `game_tick = None` (exact upstream StateHandler.java behavior)
        # AND freeze position on first 3 walk attempts to prove stationary stall recovery re-fires `/walk`!
        walk_attempts = 0
        orig_read = adapter.read_observation
        orig_exec = adapter.execute_raw_command

        def _null_tick_read(monotonic_ms: int = 1000) -> dict[str, Any]:
            obs = orig_read(monotonic_ms=monotonic_ms)
            obs["game_tick"] = None
            return obs

        def _sticky_walk_exec(cmd_type: str, payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal walk_attempts
            if cmd_type == "WALK_TO_WORLD_POINT":
                walk_attempts += 1
                if walk_attempts <= 1:
                    # Simulate walk interrupted by combat/dialogue close (player stays stationary!)
                    return {"ok": True}
            return orig_exec(cmd_type, payload)

        adapter.read_observation = _null_tick_read  # type: ignore[method-assign]
        adapter.execute_raw_command = _sticky_walk_exec  # type: ignore[method-assign]

        # Tick 1 dispatches walk #1 (blocked); ticks 2..20 stay 100% debounced across 20-tick (12s) surface budget;
        # tick 21 re-dispatches walk #2 cleanly without NoSafeRouteError!
        for step in range(1, 21):
            kernel.step_tick(bot, now_monotonic=float(step))
        assert walk_attempts == 1, f"Walk re-dispatched too early during 20-tick surface budget: {walk_attempts}"
        kernel.step_tick(bot, now_monotonic=21.0)
        assert walk_attempts == 2, f"Expected debounced walk #2 on tick 21, got {walk_attempts}"
        assert (adapter.position["x"], adapter.position["y"]) != (3222, 3218)

        # Now place player underground on Floor 4 (`y=5232 >= 5000`) at `8/10 HP` under Ankou attack!
        adapter.position = {"x": 2361, "y": 5232, "plane": 0}
        adapter.varbits = {2309: 1, 2310: 1, 2311: 1, 2312: 0}
        adapter.hp_current = 8
        adapter.hp_max = 10
        adapter.under_attack = True

        r_eat1 = kernel.step_tick(bot, now_monotonic=10.0)
        assert "CONSUME_FOOD" in r_eat1.dispatched_commands, "Must eat at 8/10 HP underground (Ankou max hit = 8)!"

        # Simulate Ankou hitting -6 on the exact same tick Trout heals, leaving HP at 6/10 (< 8)!
        # Food count dropped (8 -> 7), so Watchdog confirms eat #1 and fires eat #2 after 3 synthetic ticks!
        adapter. _pending_heal_amount = 0
        adapter.hp_current = 6

        kernel.step_tick(bot, now_monotonic=11.0)  # +1 tick (cooldown)
        kernel.step_tick(bot, now_monotonic=12.0)  # +2 ticks (cooldown)
        r_eat2 = kernel.step_tick(bot, now_monotonic=13.0)  # +3 ticks -> cooldown satisfied with game_tick=None!
        assert "CONSUME_FOOD" in r_eat2.dispatched_commands, "Second food was blocked when game_tick is None!"
        assert r_eat2.halted is False
    finally:
        store.close()


def test_t50_live_run3_acc02_zaza266_full_rehearsal_floor4_chebyshev2_and_door_id_disambiguation(
    tmp_path: Path,
) -> None:
    """PR-15 Req #4 + Full `acc_02` (`zaza266`) Live Run 3 End-to-End Rehearsal over HTTP/1.1:
    - Starts at Lumbridge `(3221, 3218, 0)` with `v2309=1, v2310=1, v2311=1, v2312=0`, `gameTick=None`,
      `runEnabled` omitted (`Varp 173`), `0` inventory food, `12x Salmon` in Draynor Bank.
    - Stages `10x Salmon`, enables Run (`160:27`), walks Safe Corridor -> `20790` -> skips F1 (`20786`),
      F2 (`19005`), F3 (`23707`) -> lands on Floor 4 `(2361, 5212, 0)`.
    - Passes all 4 `Portal of Death` doors at `chebyshev_distance == 2`, including `(2361, 5232)` where
      `/objects?name=Portal%20of%20Death` returns BOTH `23727` (`y=5231` behind) and `23728` (`y=5234` ahead)
      and adapter binds `{"id": 23728}` (ahead!) without ever clicking backwards or ping-ponging at `(2361, 5235)`.
    - Survives Ankou hits, claims `Fancy boots` (`23731` + `270:15`), climbs `Bone chain` (`23732`),
      routes to `GRAND_EXCHANGE`, deposits `Fancy boots` (`9005`) + `Coins` (`995`), and hits `COMPLETE_TASK`.
    """
    world: dict[str, Any] = {
        "pos": {"x": 3221, "y": 3218, "plane": 0},
        "hp": 10,
        "varp_173": 0,
        "v2312": 0,
        "bank_open": False,
        "bank_items": [{"id": 329, "name": "Salmon", "quantity": 12}],
        "inv": [{"id": 995, "name": "Coins", "quantity": 10000}],
        "in_sec_q": False,
        "widget_270": False,
        "f4_stage": 0,
        "shortcuts_used": [],
        "interacted_door_ids": [],
    }

    class _Run3Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def _json(self, code: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload).encode("utf-8")
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
                self._json(
                    200,
                    {
                        "loggedIn": True,
                        "gameState": "LOGGED_IN",
                        "player": {
                            "name": "zaza266",
                            "combatLevel": 3,
                            "interacting": world["pos"]["y"] >= 5200 and world["v2312"] == 0,
                            "position": dict(world["pos"]),
                        },
                    },
                )
                return
            if parsed.path == "/skills":
                self._json(200, {"skills": [{"name": "Hitpoints", "level": 10, "boostedLevel": world["hp"]}]})
                return
            if parsed.path == "/dialogue":
                if world["in_sec_q"]:
                    self._json(
                        200,
                        {
                            "inDialogue": True,
                            "hasContinue": False,
                            "hasOptions": True,
                            "question": "To pass you must answer me this: How do I set up two-factor?",
                            "options": [
                                "Give my password to a friend.",
                                "Set up 2 step authentication with my email or authenticator.",
                            ],
                        },
                    )
                elif world["widget_270"]:
                    self._json(200, {"inDialogue": True, "hasContinue": False, "hasOptions": False})
                else:
                    self._json(200, {"inDialogue": False, "hasContinue": False, "hasOptions": False})
                return
            if parsed.path == "/varps":
                vid = int(qs["id"][0])
                self._json(200, {"varpId": vid, "value": world["varp_173"] if vid == 173 else 1000})
                return
            if parsed.path == "/varbits":
                vbid = int(qs["id"][0])
                val = world["v2312"] if vbid == 2312 else (1 if vbid in (2309, 2310, 2311) else 0)
                self._json(200, {"varbitId": vbid, "value": val})
                return
            if parsed.path == "/bank":
                self._json(200, {"open": world["bank_open"], "count": len(world["bank_items"]), "items": world["bank_items"]})
                return
            if parsed.path == "/inventory":
                self._json(200, {"items": list(world["inv"])})
                return
            if parsed.path == "/objects":
                # Inside Airlock 1->2 at (2361, 5232), portal 23727 (y=5231, dist=1) is CLOSER to player than 23728 (y=5234, dist=2)!
                if world["f4_stage"] == 1:
                    self._json(
                        200,
                        {
                            "objects": [
                                {"id": 23727, "name": "Portal of Death", "position": {"x": 2361, "y": 5231, "plane": 0}},
                                {"id": 23728, "name": "Portal of Death", "position": {"x": 2361, "y": 5234, "plane": 0}},
                            ]
                        },
                    )
                else:
                    self._json(200, {"objects": []})
                return
            self._json(404, {"error": parsed.path})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            if self.path == "/walk":
                tx, ty = int(body["x"]), int(body["y"])
                # On Floor 4 door approaches, stop at Chebyshev distance 2 to prove <=2 triggers immediately!
                if (tx, ty) == (2359, 5230):
                    world["pos"] = {"x": 2361, "y": 5228, "plane": 0}
                elif (tx, ty) == (2341, 5226):
                    world["pos"] = {"x": 2341, "y": 5228, "plane": 0}
                    world["hp"] = 4  # Simulate Ankou hit of 6 in North Hall!
                elif (tx, ty) == (2343, 5215):
                    world["pos"] = {"x": 2343, "y": 5217, "plane": 0}
                elif (tx, ty) == (2349, 5215):
                    world["pos"] = {"x": 2347, "y": 5215, "plane": 0}
                else:
                    world["pos"] = {"x": tx, "y": ty, "plane": int(body.get("plane", 0))}
                self._json(200, {"success": True})
                return
            if self.path == "/widgets/click":
                if body.get("groupId") == 160 and body.get("childId") == 27:
                    world["varp_173"] = 0 if world["varp_173"] == 1 else 1
                elif body.get("groupId") == 270 and body.get("childId") == 15:
                    world["widget_270"] = False
                    world["v2312"] = 1
                    world["inv"].append({"id": 9005, "name": "Fancy boots", "quantity": 1})
                self._json(200, {"clicked": True})
                return
            if self.path == "/inventory/interact" and body.get("action") == "Eat":
                for idx, it in enumerate(world["inv"]):
                    if it["id"] == int(body["id"]):
                        world["inv"].pop(idx)
                        world["hp"] = min(10, world["hp"] + 9)
                        break
                self._json(200, {"success": True})
                return
            if self.path == "/dialogue/select":
                assert "2 step authentication" in body["option"]
                world["in_sec_q"] = False
                # Passing Portal [1/4] places zaza266 at exact Run 2 tile (2361, 5232)!
                world["pos"] = {"x": 2361, "y": 5232, "plane": 0}
                world["f4_stage"] = 1
                world["hp"] = 7  # Ankou hit in vestibule!
                self._json(200, {"success": True})
                return
            if self.path == "/bank/open":
                world["bank_open"] = True
                self._json(200, {"opened": True})
                return
            if self.path == "/bank/close":
                world["bank_open"] = False
                self._json(200, {"closed": True})
                return
            if self.path == "/bank/withdraw":
                qty = int(body["quantity"])
                world["bank_items"][0]["quantity"] -= qty
                for _ in range(qty):
                    world["inv"].append({"id": 329, "name": "Salmon", "quantity": 1})
                self._json(200, {"success": True, "quantity": qty})
                return
            if self.path == "/bank/deposit":
                for it in world["inv"]:
                    world["bank_items"].append(dict(it))
                world["inv"].clear()
                self._json(200, {"success": True})
                return
            if self.path == "/objects/interact":
                oid = body.get("id")
                oname = body.get("name")
                if oid == 20790:
                    world["pos"] = {"x": 1861, "y": 5242, "plane": 0}
                elif oid in (20786, 19005, 23707):
                    world["shortcuts_used"].append(oid)
                    dest = {20786: (1904, 5222), 19005: (2026, 5218), 23707: (2146, 5283)}[oid]
                    world["pos"] = {"x": dest[0], "y": dest[1], "plane": 0}
                elif oid == 20785:
                    world["pos"] = {"x": 2043, "y": 5243, "plane": 0}
                elif oid == 19004:
                    world["pos"] = {"x": 2123, "y": 5254, "plane": 0}
                elif oid == 23706:
                    world["pos"] = {"x": 2361, "y": 5212, "plane": 0}
                elif oname == "Portal of Death" or oid in (23727, 23728):
                    if oid is not None:
                        world["interacted_door_ids"].append(oid)
                    if world["f4_stage"] == 0:
                        world["in_sec_q"] = True
                    elif world["f4_stage"] == 1:
                        assert oid == 23728, "Adapter must disambiguate forward door 23728 over backward door 23727!"
                        # Stepping North out of Portal [2/4] lands at (2361, 5235) where px >= 2352 STILL holds!
                        world["pos"] = {"x": 2361, "y": 5235, "plane": 0}
                        world["f4_stage"] = 2
                    elif world["f4_stage"] == 2:
                        world["pos"] = {"x": 2341, "y": 5224, "plane": 0}
                        world["f4_stage"] = 3
                    elif world["f4_stage"] == 3:
                        world["pos"] = {"x": 2343, "y": 5219, "plane": 0}
                        world["f4_stage"] = 4
                elif oid == 23731:
                    world["widget_270"] = True
                elif oid == 23732:
                    world["pos"] = {"x": 3081, "y": 3421, "plane": 0}
                self._json(200, {"success": True})
                return
            self._json(404, {"error": self.path})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Run3Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    store, http_ad, _gate, kernel = _make_leased_http_env(tmp_path, srv.server_address[1])
    try:
        bot = StrongholdTaskModule(min_required_food=6, target_food_count=10)
        completed = False
        for step in range(1, 90):
            res = kernel.step_tick(bot, now_monotonic=float(step))
            if res.halted:
                assert res.task_directive is not None
                assert res.task_directive.directive_type == DirectiveType.COMPLETE_TASK
                completed = True
                break

        assert completed is True, "Live Run 3 rehearsal did not complete!"
        assert world["varp_173"] == 1, "Run mode must remain enabled throughout session!"
        assert world["shortcuts_used"] == [20786, 19005, 23707], "Must skip claimed Floors 1, 2, AND 3 via shortcuts!"
        assert 23728 in world["interacted_door_ids"], "Must disambiguate Portal of Death [2/4] by ID 23728!"
        banked_ids = {it["id"] for it in world["bank_items"]}
        assert 9005 in banked_ids and 995 in banked_ids
    finally:
        http_ad.kill_process_tree()
        store.close()
        srv.server_close()


def test_t51_barbarian_bridge_walk_debounce_fire_cooking_levelup_resume_and_direct_hole_entry(
    tmp_path: Path,
) -> None:
    """PR-15.1 Field Remediation (Gunnarsgrunn Bridge Walk-Cancel Loop & Zero-Touch Fire Cooking):
    1. Graph edges `safe_bridge_east_to_barbarian_river_fire` (14.0) and
       `safe_barbarian_river_fire_to_stronghold_entrance` (26.0) exist and route directly from
       `BARBARIAN_RIVER_FIRE (3106, 3432)` -> `BARBARIAN_VILLAGE (3081, 3421)` without crossing back East.
    2. Even on a direct 2-point `WorldPoint` micro-route (`<= 20` tiles), `step_navigation_on_tick`
       dispatches `/walk` ONCE and stays 100% silent for `walk_redispatch_cooldown_ticks` (5 ticks = 3.0s)
       while `WebWalker` computes `1..19` across the bridge (ZERO 0.6s cancel-loop!).
    3. Starting `zaza266` at `(3105, 3420)` with `1x Cooked meat` + `11x Raw shrimps` immediately routes to
       `BARBARIAN_RIVER_FIRE (3106, 3432)` (no bank detour), crosses the bridge after a 3-tick WebWalker
       delay without a single extra `/walk`, clicks `Fire` (`Cook`), waits 2 ticks for `Widget 270`,
       clicks `270:14`, automatically dismisses & resumes across a Cooking Level-Up dialogue (`1 -> 2`),
       cooks the full batch, and walks straight to Stronghold `20790`!
    """
    from osrs_ee.navigation.waypoint_graph import WorldPoint

    # 1. Verify direct 2-point micro-route debounce (zero 0.6s walk spam!)
    graph = ConstrainedWaypointGraph()
    nav = NavigationEngine(graph)
    caps = AccountCapabilitiesContext("acc_02", skills={"agility": 1, "hitpoints": 10}, combat_level=3)
    p_bridge = WorldPoint(3105, 3420, 0)
    p_fire = WorldPoint(3106, 3432, 0)

    c1 = nav.step_navigation_on_tick(current_pos=p_bridge, tick_advanced=True, capabilities=caps, destination=p_fire)
    assert c1 is not None and (c1["x"], c1["y"]) == (3106, 3432)
    # Next 4 stationary ticks (2.4s while WebWalker calculates 1..19): MUST return None every tick!
    for _ in range(4):
        assert (
            nav.step_navigation_on_tick(current_pos=p_bridge, tick_advanced=True, capabilities=caps, destination=p_fire)
            is None
        ), "NavigationEngine spammed `/walk` before 5-tick WebWalker debounce window!"
    # 5th stalled tick (3.0s stationary): single clean re-dispatch, then silent again!
    c6 = nav.step_navigation_on_tick(current_pos=p_bridge, tick_advanced=True, capabilities=caps, destination=p_fire)
    assert c6 is not None
    assert (
        nav.step_navigation_on_tick(current_pos=p_bridge, tick_advanced=True, capabilities=caps, destination=p_fire)
        is None
    )

    # Verify direct Fire -> Stronghold Entrance edge avoids backtracking through GUNNARSGRunn_BRIDGE_EAST (3105, 3420)
    plan_fire_to_hole = graph.plan_safe_route(
        origin="BARBARIAN_RIVER_FIRE",
        destination="BARBARIAN_VILLAGE",
        capabilities=caps,
    )
    assert [(wp.x, wp.y) for wp in plan_fire_to_hole.waypoints] == [(3078, 3445), (3081, 3421)]

    # 2. Full end-to-end rehearsal starting from exact Live Run 3 bridge position (3105, 3420) with 11x Raw shrimps
    store, adapter, _gate, kernel = _make_leased_fake_env(
        tmp_path / "bridge_resume",
        bank_items=[],
        inventory_items=[
            {"item_id": 2142, "name": "Cooked meat", "quantity": 1, "noted": False},
            {"item_id": 317, "name": "Raw shrimps", "quantity": 11, "noted": False},
        ],
    )
    try:
        adapter.position = {"x": 3105, "y": 3420, "plane": 0}
        bot = StrongholdTaskModule(min_required_food=6, target_food_count=10)

        walk_targets: list[tuple[int, int]] = []
        bridge_walk_started_tick: int | None = None
        cooking_active = False
        levelup_triggered = False
        entered_stronghold_20790 = False
        tick_counter = 0

        orig_exec = adapter.execute_raw_command

        def _sim_bridge_and_fire(cmd_type: str, payload: dict[str, Any]) -> dict[str, Any]:
            nonlocal bridge_walk_started_tick, cooking_active, entered_stronghold_20790
            if cmd_type == "WALK_TO_WORLD_POINT":
                tx, ty = int(payload["x"]), int(payload["y"])
                walk_targets.append((tx, ty))
                if (tx, ty) == (3078, 3445):
                    # Simulate real WebWalker taking 3 stationary ticks to compute bridge path 1..19 before moving!
                    bridge_walk_started_tick = tick_counter
                    return {"ok": True}
                return orig_exec(cmd_type, payload)

            res = orig_exec(cmd_type, payload)
            if cmd_type == "WIDGET_CLICK" and payload.get("groupId") == 270 and payload.get("childId") == 14:
                cooking_active = True
            elif cmd_type == "DIALOGUE_CONTINUE":
                adapter.dialogue_open = False
            elif cmd_type == "INTERACT_OBJECT" and payload.get("id") == 20790:
                entered_stronghold_20790 = True
            return res

        adapter.execute_raw_command = _sim_bridge_and_fire  # type: ignore[method-assign]

        for step in range(1, 50):
            tick_counter = step
            # After 3 stationary ticks on the bridge, WebWalker moves player smoothly to the Fire!
            if bridge_walk_started_tick is not None and (step - bridge_walk_started_tick) == 3:
                adapter.position = {"x": 3078, "y": 3445, "plane": 0}
                bridge_walk_started_tick = None

            # Simulate real gradual OSRS cooking (2-3 shrimps per cycle) + Cooking Level 1->2 interruption!
            if cooking_active:
                raw_entry = next(
                    (it for it in adapter.inventory_items if str(it.get("name", "")).casefold() == "raw shrimps"),
                    None,
                )
                if raw_entry is not None and raw_entry["quantity"] > 0:
                    cook_now = min(3, int(raw_entry["quantity"]))
                    raw_entry["quantity"] -= cook_now
                    if raw_entry["quantity"] == 0:
                        adapter.inventory_items.remove(raw_entry)
                        cooking_active = False
                    adapter._add_to_inventory(
                        {"item_id": 315, "name": "Shrimps", "quantity": cook_now, "noted": False}
                    )
                    if not levelup_triggered:
                        # 3 cooked shrimps = 90 XP >= 83 XP -> Cooking level 2 interrupts Cook-All!
                        levelup_triggered = True
                        cooking_active = False
                        adapter.dialogue_open = True

            kernel.step_tick(bot, now_monotonic=float(step))
            if entered_stronghold_20790:
                break

        assert entered_stronghold_20790 is True, "Bot did not finish cooking and enter Stronghold 20790!"
        # Must dispatch ONLY ONE `/walk` to (3078, 3445) and ONE direct `/walk` to (3081, 3421)!
        assert walk_targets == [(3078, 3445), (3081, 3421)], f"Unexpected walk calls: {walk_targets}"
        assert StrongholdTaskModule._count_raw_swamp_fish(tuple(adapter.inventory_items)) == 0
        assert StrongholdTaskModule._count_edible_food(tuple(adapter.inventory_items)) >= 10
    finally:
        store.close()

