"""Hermetic & Loopback Wire Acceptance Tests for PR-18 Lite (`T67..T69`).

Covers:
- T67: Live `GET /varps?id=101` (`VarPlayer.QUEST_POINTS`) fallback in `MicrobotHttpAdapter.read_observation()`
  when upstream `StateHandler.java` omits `questPoints` from `/state`, plus real-time `StateStore` sync of
  `quest_points`, `stage`, and `trade_unlocked` (`QP >= 10`) inside `ExecutionEngineKernel`.
- T68: Exact upstream `InventoryHandler.java` + `Rs2Inventory.java` 2-step `USE_ITEM_ON_ITEM` wire sequence
  (`POST /inventory/interact` `{"id": 1769, "action": "Use"}` -> `{"id": 288, "action": "Use"}` and
  `{"id": 1767, "action": "Use"}` -> `{"id": 288, "action": "Use"}`) strictly AFTER `POST /bank/close`.
- T69: Full end-to-end `acc_02` Golden Reference rehearsal of `stage1a_f2p_quests_10qp` starting at
  `GRAND_EXCHANGE` `(3164, 3487, 0)` with tradeable GE bank supplies ONLY (zero pre-dyed untradeable goblin
  mails in bank), completing all 4 quests (`0 -> 1 -> 2 -> 7 -> 12 QP`), persisting `quest_points == 12`
  into `state.db`, returning to `GRAND_EXCHANGE`, and queuing the `SmartEventNotifier` completion alert.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from osrs_ee.bots import DirectiveType, TradeUnlockQuestsTaskModule
from osrs_ee.core import ExecutionEngineKernel, ReactiveSafetyWatchdog, SessionLifecycleManager
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph, NavigationEngine
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.sandbox.http_adapter import MicrobotHttpAdapter
from osrs_ee.security.redaction import Redactor
from osrs_ee.state import AccountStatus, StateStore
from osrs_ee.telemetry.outbox_and_bot import BoundedOutbox
from osrs_ee.telemetry.smart_notifier import SmartEventNotifier


def test_t67_live_varp101_quest_points_wire_observation_and_db_sync(tmp_path: Path) -> None:
    """T67 (`PR-18 Lite`): Proves `MicrobotHttpAdapter` reads `Varp 101` (`questPoints`) live when `/state`
    omits `questPoints`, and `ExecutionEngineKernel` updates `accounts.quest_points` in `state.db` in real time.
    """
    token = "tok_pr18_varp101_secret_987654"
    wire_state: dict[str, Any] = {
        "varp_101": 0,
        "varps": {29: 0, 179: 0, 144: 0, 62: 0, 173: 1, 281: 1000},
        "pos": {"x": 3164, "y": 3487, "plane": 0},
        "queried_varp_101": 0,
    }

    class _Varp101WireHandler(BaseHTTPRequestHandler):
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
                # Upstream StateHandler.java omits `questPoints`, `totalLevel`, `playtimeMinutes`
                self._json(
                    200,
                    {
                        "loggedIn": True,
                        "gameState": "LOGGED_IN",
                        "player": {
                            "name": "zaza266",
                            "combatLevel": 3,
                            "interacting": False,
                            "position": dict(wire_state["pos"]),
                        },
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
                if vid == 101:
                    wire_state["queried_varp_101"] += 1
                    self._json(200, {"varpId": 101, "value": int(wire_state["varp_101"])})
                else:
                    self._json(200, {"varpId": vid, "value": int(wire_state["varps"].get(vid, 0))})
                return
            if parsed.path == "/bank":
                self._json(200, {"open": False, "count": 0, "items": []})
                return
            if parsed.path == "/inventory":
                self._json(200, {"items": []})
                return
            self._json(404, {"error": parsed.path})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            _ = self.rfile.read(length) if length > 0 else b""
            self._json(200, {"ok": True, "opened": True, "closed": True})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Varp101WireHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    store = StateStore(tmp_path / "pr18_varp101.db")
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
    # Seed post-seasoning total_level=105 & playtime_minutes=1250 so QP>=10 is the sole remaining Jagex trade gate
    store.apply_observation_snapshot(
        event_id="seed_t67_pre_qp",
        observation={
            "schema_version": "1.0",
            "observation_id": "obs_seed_t67",
            "observed_at": "2026-10-29T12:00:01Z",
            "monotonic_ms": 1,
            "account_id": "acc_02",
            "session_id": lease["session_id"],
            "client_instance_id": "seed_cli",
            "session_epoch": lease["session_epoch"],
            "login_state": "LOGGED_IN",
            "tutorial_varp_281": {"value": 1000, "quality": "KNOWN"},
            "quest_points": {"value": 0, "quality": "KNOWN"},
            "total_level": {"value": 105, "quality": "KNOWN"},
            "playtime_minutes": {"value": 1250, "quality": "KNOWN"},
        },
    )
    http_ad = MicrobotHttpAdapter(
        account_id="acc_02",
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        port=srv.server_address[1],
        agent_token=token,
        coalesce_window_ms=0,
    )
    gate = GameAdapterGate(store, http_ad)  # type: ignore[arg-type]
    gate.bind_to_lease(
        account_id="acc_02",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
    )
    caps = AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3, quest_points=0)
    kernel = ExecutionEngineKernel(
        gate=gate,
        account_id="acc_02",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
        capabilities=caps,
    )
    try:
        bot = TradeUnlockQuestsTaskModule()
        # Tick 1 at QP=0 -> queries /varps?id=101 and keeps trade_unlocked=False
        wire_state["varp_101"] = 2
        kernel.step_tick(bot, now_monotonic=1.0)
        assert wire_state["queried_varp_101"] >= 1
        acc_mid = store.get_account("acc_02")
        assert int(acc_mid["quest_points"]) == 2
        assert bool(acc_mid["trade_unlocked"]) is False

        # Varp 101 jumps to 12 QP -> kernel immediately updates capabilities, closes quest goal, & unlocks trade in DB!
        wire_state["varp_101"] = 12
        res_done = kernel.step_tick(bot, now_monotonic=2.0)
        assert res_done.task_directive is not None
        assert res_done.task_directive.directive_type == DirectiveType.COMPLETE_TASK
        assert "QP=12" in res_done.detail

        acc_final = store.get_account("acc_02")
        assert int(acc_final["quest_points"]) == 12
        assert bool(acc_final["trade_unlocked"]) is True
    finally:
        http_ad.kill_process_tree()
        store.close()
        srv.server_close()


def test_t68_goblin_diplomacy_use_item_on_item_wire_protocol_and_closed_bank_guard(tmp_path: Path) -> None:
    """T68 (`PR-18 Lite`): Verifies exact 2-step `POST /inventory/interact` `Use` -> `Use` wire protocol
    for `Orange dye (1769)` + `Blue dye (1767)` on `Goblin mail (288)` over HTTP/1.1, strictly enforcing
    that the bank is closed BEFORE any `"Use"` click is sent (matching `Rs2Inventory.java` container logic).
    """
    token = "tok_pr18_dye_wire_secret_654321"
    world: dict[str, Any] = {
        "pos": {"x": 3164, "y": 3487, "plane": 0},
        "bank_open": False,
        "selected_item_id": None,
        "varp_62": 0,
        "varp_101": 7,
        "bank_items": [
            {"id": 1769, "name": "Orange dye", "quantity": 1},
            {"id": 1767, "name": "Blue dye", "quantity": 1},
            {"id": 288, "name": "Goblin mail", "quantity": 3},
        ],
        "inv": [],
        "use_sequence": [],
        "talked_to_bentnoze": False,
    }

    class _GoblinWireHandler(BaseHTTPRequestHandler):
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
                        "player": {"name": "zaza266", "combatLevel": 3, "position": dict(world["pos"])},
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
                val_map = {29: 2, 179: 21, 144: 100, 62: world["varp_62"], 101: world["varp_101"], 173: 1, 281: 1000}
                self._json(200, {"varpId": vid, "value": int(val_map.get(vid, 0))})
                return
            if parsed.path == "/bank":
                items = list(world["bank_items"]) if world["bank_open"] else []
                self._json(200, {"open": world["bank_open"], "count": len(items), "items": items})
                return
            if parsed.path == "/inventory":
                self._json(200, {"items": list(world["inv"])})
                return
            self._json(404, {"error": parsed.path})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            if self.path == "/bank/open":
                world["bank_open"] = True
                self._json(200, {"opened": True})
                return
            if self.path == "/bank/close":
                world["bank_open"] = False
                self._json(200, {"closed": True})
                return
            if self.path == "/bank/withdraw":
                wid = int(body["id"])
                wqty = int(body["quantity"])
                for b_it in list(world["bank_items"]):
                    if int(b_it["id"]) == wid:
                        take = min(wqty, int(b_it["quantity"]))
                        b_it["quantity"] -= take
                        if b_it["quantity"] <= 0:
                            world["bank_items"].remove(b_it)
                        for _ in range(take):
                            world["inv"].append({"id": wid, "name": b_it["name"], "quantity": 1, "noted": False})
                        self._json(200, {"success": True, "quantity": take})
                        return
                self._json(200, {"success": False})
                return
            if self.path == "/inventory/interact":
                # Strict upstream Rs2Inventory.java guard: NEVER click "Use" while bank is open!
                assert world["bank_open"] is False, "USE_ITEM_ON_ITEM clicked inventory while Bank was still open!"
                assert body.get("action") == "Use"
                iid = int(body["id"])
                world["use_sequence"].append(iid)
                if world["selected_item_id"] is None:
                    world["selected_item_id"] = iid
                else:
                    primary = int(world["selected_item_id"])
                    world["selected_item_id"] = None
                    pair = {primary, iid}
                    if pair == {1769, 288}:
                        out_id, out_name = 286, "Orange goblin mail"
                    elif pair == {1767, 288}:
                        out_id, out_name = 287, "Blue goblin mail"
                    else:
                        raise AssertionError(f"Unexpected item combine pair: {pair}")
                    # Remove 1 dye + 1 Goblin mail -> add 1 dyed goblin mail
                    for rem_id in (primary, iid):
                        idx = next(i for i, x in enumerate(world["inv"]) if int(x["id"]) == rem_id)
                        world["inv"].pop(idx)
                    world["inv"].append({"id": out_id, "name": out_name, "quantity": 1, "noted": False})
                self._json(200, {"success": True})
                return
            if self.path == "/walk":
                world["pos"] = {"x": int(body["x"]), "y": int(body["y"]), "plane": int(body.get("plane", 0))}
                self._json(200, {"success": True})
                return
            if self.path == "/npcs/interact":
                if str(body.get("action", "")).casefold() == "pickpocket":
                    # `PR-20.7`: the wire fake models the real mechanic — a citizen yields 3 GP.
                    for _ in range(3):
                        world["inv"].append({"id": 995, "name": "Coins", "quantity": 1, "noted": False})
                    self._json(200, {"success": True})
                    return
                if body.get("name") == "General Bentnoze":
                    # Coins legitimately survive the trip to the generals (the bot keeps its change),
                    # exactly as `T69` already accepts for the live run.
                    inv_ids = sorted(int(x["id"]) for x in world["inv"] if int(x["id"]) != 995)
                    assert inv_ids == [286, 287, 288], f"Expected [286, 287, 288] at Bentnoze, got {inv_ids}"
                    world["talked_to_bentnoze"] = True
                    world["inv"].clear()
                    world["varp_62"] = 6
                    world["varp_101"] = 12
                self._json(200, {"success": True})
                return
            self._json(404, {"error": self.path})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _GoblinWireHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    store = StateStore(tmp_path / "pr18_goblin_wire.db")
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
    http_ad = MicrobotHttpAdapter(
        account_id="acc_02",
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        port=srv.server_address[1],
        agent_token=token,
        coalesce_window_ms=0,
    )
    gate = GameAdapterGate(store, http_ad)  # type: ignore[arg-type]
    gate.bind_to_lease(
        account_id="acc_02",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
    )
    caps = AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3, quest_points=7)
    kernel = ExecutionEngineKernel(
        gate=gate,
        account_id="acc_02",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
        capabilities=caps,
    )
    try:
        bot = TradeUnlockQuestsTaskModule()
        completed = False
        for step in range(1, 35):
            r = kernel.step_tick(bot, now_monotonic=float(step))
            if r.halted:
                assert r.task_directive is not None
                assert r.task_directive.directive_type == DirectiveType.COMPLETE_TASK
                completed = True
                break

        assert completed is True
        assert world["talked_to_bentnoze"] is True
        assert world["use_sequence"] == [1769, 288, 1767, 288]
        assert int(store.get_account("acc_02")["quest_points"]) == 12
    finally:
        http_ad.kill_process_tree()
        store.close()
        srv.server_close()


def test_t69_e2e_stage1a_12qp_quests_with_tradeable_ge_dyes_and_juliet_balcony(tmp_path: Path) -> None:
    """T69 (`PR-18 Lite`): Full end-to-end Golden Reference rehearsal of `stage1a_f2p_quests_10qp` on `acc_02`
    starting at `GRAND_EXCHANGE` `(3164, 3487, 0)` with ONLY tradeable GE bank supplies (`Egg 1944`,
    `Bucket of milk 1927`, `Pot of flour 1933`, `20x Ball of wool 1759`, `Cadava berries 753`,
    `Orange dye 1769`, `Blue dye 1767`, `3x Goblin mail 288`), completing all 4 quests (`0 -> 1 -> 2 -> 7 -> 12 QP`),
    returning to `GRAND_EXCHANGE`, closing session cleanly, and queuing Smart Telegram completion notification.
    """
    redactor = Redactor()
    store = StateStore(tmp_path / "pr18_e2e.db", redactor=redactor)
    outbox = BoundedOutbox(tmp_path / "pr18_outbox.db", redactor=redactor)
    notifier = SmartEventNotifier(outbox=outbox, target_chat_ids=[777001])

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
    ge_tradeable_bank = [
        {"item_id": 995, "name": "Coins", "quantity": 8500, "noted": False},
        {"item_id": 9005, "name": "Fancy boots", "quantity": 1, "noted": False},
        {"item_id": 1944, "name": "Egg", "quantity": 1, "noted": False},
        {"item_id": 1927, "name": "Bucket of milk", "quantity": 1, "noted": False},
        {"item_id": 1933, "name": "Pot of flour", "quantity": 1, "noted": False},
        {"item_id": 1759, "name": "Ball of wool", "quantity": 20, "noted": False},
        {"item_id": 753, "name": "Cadava berries", "quantity": 1, "noted": False},
        {"item_id": 1769, "name": "Orange dye", "quantity": 1, "noted": False},
        {"item_id": 1767, "name": "Blue dye", "quantity": 1, "noted": False},
        {"item_id": 288, "name": "Goblin mail", "quantity": 3, "noted": False},
    ]
    adapter = FakeGameAdapter(
        account_id="acc_02",
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        tutorial_varp_281=1000,
        quest_points=0,
        total_level=35,
        bank_items=ge_tradeable_bank,
        inventory_items=[],
    )
    adapter.position = {"x": 3164, "y": 3487, "plane": 0}
    adapter.varps.update({29: 0, 179: 0, 144: 0, 62: 0})

    gate = GameAdapterGate(store, adapter)
    gate.bind_to_lease(
        account_id="acc_02",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
    )
    caps = AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3, quest_points=0)
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
        notifier=notifier,
        slot_id=0,
        adapter_port=8081,
    )

    combines_executed: list[tuple[int, int]] = []
    visited_juliet = False
    orig_exec = adapter.execute_raw_command

    def _sim_12qp_world(cmd_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal visited_juliet
        if cmd_type == "USE_ITEM_ON_ITEM":
            combines_executed.append((int(payload["source_item_id"]), int(payload["target_item_id"])))
        res = orig_exec(cmd_type, payload)
        if cmd_type == "INTERACT_OBJECT" and payload.get("name") == "Staircase":
            if payload.get("action") == "Climb-up":
                adapter.position = {"x": 3156, "y": 3435, "plane": 1}
            elif payload.get("action") == "Climb-down":
                adapter.position = {"x": 3157, "y": 3435, "plane": 0}
        elif cmd_type == "INTERACT_NPC":
            npc = payload.get("name")
            if npc == "Cook":
                adapter.inventory_items.clear()
                adapter.varps[29] = 2
                adapter.quest_points += 1
            elif npc == "Fred the Farmer":
                adapter.inventory_items.clear()
                adapter.varps[179] = 21
                adapter.quest_points += 1
            elif npc == "Romeo":
                cur = adapter.varps[144]
                if cur == 0:
                    adapter.varps[144] = 10
                elif cur == 20:
                    adapter.varps[144] = 30
                elif cur == 60:
                    adapter.varps[144] = 100
                    # Intentionally keep QP at 7 (<10) after Romeo so Goblin Diplomacy MUST also run & craft dyes!
                    adapter.quest_points += 5
            elif npc == "Juliet":
                assert adapter.position["plane"] == 1
                visited_juliet = True
                if adapter.varps[144] == 10:
                    adapter.varps[144] = 20
                elif adapter.varps[144] == 50:
                    adapter.inventory_items.clear()
                    adapter.varps[144] = 60
            elif npc == "Father Lawrence":
                adapter.varps[144] = 40
            elif npc == "Apothecary":
                adapter.inventory_items = [
                    {"item_id": 756, "name": "Cadava potion", "quantity": 1, "noted": False}
                ]
                adapter.varps[144] = 50
            elif npc == "General Bentnoze":
                # `PR-20.4`: the dye float leaves the bot's own change in the bag (live run ended
                # with 55 GP). Coins are not a quest supply — compare everything else.
                inv_ids = sorted(int(i["item_id"]) for i in adapter.inventory_items if int(i["item_id"]) != 995)
                assert inv_ids == [286, 287, 288], f"Expected dyed armours [286, 287, 288], got {inv_ids}"
                adapter.inventory_items.clear()
                adapter.varps[62] = 6
                adapter.quest_points += 5
        return res

    adapter.execute_raw_command = _sim_12qp_world  # type: ignore[method-assign]
    try:
        kernel.emit_session_started(task_id="stage1a_f2p_quests_10qp")
        bot = TradeUnlockQuestsTaskModule(target_quest_points=10)
        done = False
        for step in range(1, 140):
            rt = kernel.step_tick(bot, now_monotonic=float(step))
            if rt.halted:
                assert rt.task_directive is not None
                assert rt.task_directive.directive_type == DirectiveType.COMPLETE_TASK
                done = True
                break

        assert done is True
        assert visited_juliet is True
        assert combines_executed == [(1769, 288), (1767, 288)]
        assert (adapter.position["x"], adapter.position["y"], adapter.position["plane"]) == (3164, 3487, 0)

        acc_after = store.get_account("acc_02")
        assert int(acc_after["quest_points"]) == 12
        # Jagex 20h / 100-total-level gate rightly stays False at total_level=35 until stage1b_seasoning_gatherer
        assert bool(acc_after["trade_unlocked"]) is False
        assert acc_after["status"] == AccountStatus.RESTING.value

        sent_texts: list[str] = []
        outbox.flush_due(lambda _cid, txt: (sent_texts.append(txt) is None, 0.0), now_monotonic=time.monotonic() + 10.0)
        assert len(sent_texts) == 2  # Session started + Task completed
        assert "🏆" in sent_texts[1] and "stage1a_f2p_quests_10qp" in sent_texts[1]
    finally:
        outbox.close()
        store.close()
