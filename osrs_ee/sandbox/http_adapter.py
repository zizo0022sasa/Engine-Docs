"""Loopback-only Keep-Alive HTTP client for Microbot AgentServerPlugin (PR-05a + PR-12 / T06, T10, T33, T35).

Key Field-Remediation Enhancements (PR-12):
1. Persistent HTTP/1.1 Keep-Alive `HTTPConnection` on `127.0.0.1:<port>` (eliminates cascading TCP handshake overhead).
2. Single-Tick Snapshot Coalescing (`coalesce_window_ms`) so safety, navigation, and task engines share 1 tick read.
3. Short-Circuit Login Guard: when `/status` (or `/state`) reports `loggedIn=False` / `LOGIN_SCREEN`, returns
   cleanly in 1 HTTP call without polling `/varps`, `/bank`, `/inventory`, or `/skills` against an empty session.
4. Supports both canonical `/status` and live AgentServer `/state` + `/skills` payload shapes seamlessly.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Any

from osrs_ee.contracts.models import validate_observation


class MicrobotHttpAdapter:
    """Loopback HTTP/1.1 Keep-Alive client conforming to the same interface as FakeGameAdapter."""

    def __init__(
        self,
        *,
        account_id: str,
        session_id: str,
        session_epoch: int,
        client_instance_id: str = "live_jvm_01",
        port: int,
        agent_token: str,
        jvm_flags: list[str] | None = None,
        host: str = "127.0.0.1",
        timeout_sec: float = 4.0,
        coalesce_window_ms: int = 0,
        status_endpoint: str = "/status",
    ) -> None:
        if host != "127.0.0.1":
            raise ValueError(f"AgentServer HTTP adapter MUST bind to 127.0.0.1 only, got {host!r}")
        if not agent_token or len(agent_token) < 16:
            raise ValueError("Strong per-session X-Agent-Token (>=16 chars) is required")

        self.account_id = account_id
        self.session_id = session_id
        self.session_epoch = session_epoch
        self.client_instance_id = client_instance_id
        self.jvm_flags = list(
            jvm_flags
            if jvm_flags is not None
            else ["--disable-telemetry", "-Dmicrobot.disableTelemetry=true", "-Xmx768m"]
        )
        self.port = int(port)
        self.base_url = f"http://127.0.0.1:{self.port}"
        self._token = agent_token
        self.timeout_sec = timeout_sec
        self.coalesce_window_ms = max(0, int(coalesce_window_ms))
        self.status_endpoint = status_endpoint

        self.alive = True
        self.plugin_running = False
        self.auto_login_suppressed = False
        self.player_dead = False
        self.equipment_items: list[str] = []

        self._lock = threading.RLock()
        self._conn: http.client.HTTPConnection | None = None
        self._obs_seq = 0
        self._cached_obs: dict[str, Any] | None = None
        self._cached_at_mono_ms: int = -1_000_000
        self.http_request_count = 0
        self.tcp_connect_count = 0

    def _get_or_open_conn(self) -> http.client.HTTPConnection:
        if self._conn is None:
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.timeout_sec)
            conn.connect()
            self.tcp_connect_count += 1
            self._conn = conn
        return self._conn

    def _reset_conn(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    def _request_json(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if path.startswith("/scripts/deploy"):
            raise PermissionError("Raw /scripts/deploy source upload is forbidden by ADR-006")

        payload_bytes = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "X-Agent-Token": self._token,
            "Accept": "application/json",
            "Connection": "keep-alive",
        }
        if payload_bytes is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(payload_bytes))

        with self._lock:
            for attempt in range(2):
                try:
                    conn = self._get_or_open_conn()
                    conn.request(method, path, body=payload_bytes, headers=headers)
                    resp = conn.getresponse()
                    raw = resp.read().decode("utf-8")
                    self.http_request_count += 1
                    if resp.status in (401, 403):
                        raise PermissionError(f"AgentServer rejected X-Agent-Token with HTTP {resp.status}")
                    if resp.status >= 400:
                        raise ConnectionError(f"AgentServer HTTP {resp.status} on {path}")
                    return json.loads(raw) if raw else {}
                except PermissionError:
                    raise
                except http.client.BadStatusLine as exc:
                    self._reset_conn()
                    if attempt == 0:
                        continue
                    raise ConnectionError(f"Connection closed on {path}: {exc}") from exc
                except (TimeoutError, socket.timeout) as exc:
                    self._reset_conn()
                    raise TimeoutError(f"Timed out calling {path}") from exc
                except OSError as exc:
                    self._reset_conn()
                    if attempt == 0 and isinstance(exc, (ConnectionResetError, BrokenPipeError)):
                        continue
                    raise ConnectionError(f"Connection failed on {path}: {exc}") from exc

        raise ConnectionError(f"Unreachable retry loop on {path}")

    @staticmethod
    def _parse_login_state(status: dict[str, Any]) -> str:
        if "loginState" in status and isinstance(status["loginState"], str):
            return status["loginState"]
        if "loggedIn" in status:
            return "LOGGED_IN" if bool(status["loggedIn"]) else "LOGIN_SCREEN"
        return "UNKNOWN"

    def probe_session_state(self) -> dict[str, Any]:
        """Single lightweight HTTP probe used by `SessionLifecycleManager` (`DISCONNECTED / AT_LOGIN / IN_GAME`)."""
        status = self._request_json("GET", self.status_endpoint)
        login_state = self._parse_login_state(status)
        logged_in = login_state == "LOGGED_IN"
        return {
            "reachable": True,
            "logged_in": logged_in,
            "login_state": login_state,
            "game_tick": status.get("gameTick") if logged_in else None,
            "raw_status": status,
        }

    def invalidate_tick_cache(self) -> None:
        with self._lock:
            self._cached_obs = None

    def read_observation(self, *, monotonic_ms: int | None = None) -> dict[str, Any]:
        mono = monotonic_ms if monotonic_ms is not None else int(time.monotonic() * 1000)
        with self._lock:
            if (
                self.coalesce_window_ms > 0
                and self._cached_obs is not None
                and (mono - self._cached_at_mono_ms) <= self.coalesce_window_ms
            ):
                return dict(self._cached_obs)

        status = self._request_json("GET", self.status_endpoint)
        login_state = self._parse_login_state(status)
        self._obs_seq += 1

        # PR-12 / Defect #3 Fix: Short-circuit immediately when NOT logged in!
        # Never fire /varps, /bank, or /inventory against an unauthenticated login screen!
        if login_state != "LOGGED_IN":
            obs_logged_out = validate_observation(
                {
                    "schema_version": "1.0",
                    "observation_id": f"http_obs_{self._obs_seq:06d}",
                    "observed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "monotonic_ms": mono,
                    "account_id": self.account_id,
                    "session_id": self.session_id,
                    "client_instance_id": self.client_instance_id,
                    "session_epoch": self.session_epoch,
                    "login_state": login_state,
                    "game_tick": None,
                    "world": None,
                    "position": {"x": None, "y": None, "plane": None, "quality": "UNKNOWN"},
                    "tutorial_varp_281": {"value": None, "quality": "UNKNOWN"},
                    "quest_points": {"value": None, "quality": "UNKNOWN"},
                    "total_level": {"value": None, "quality": "UNKNOWN"},
                    "playtime_minutes": {"value": None, "quality": "UNKNOWN"},
                    "dialogue_open": False,
                    "bank": {
                        "is_open": False,
                        "pin_prompt_open": False,
                        "items_quality": "UNKNOWN",
                        "items": None,
                    },
                    "inventory": {"free_slots": None, "quality": "UNKNOWN", "items": None},
                }
            )
            with self._lock:
                self._cached_obs = obs_logged_out
                self._cached_at_mono_ms = mono
            return obs_logged_out

        # Query /varps?id=281 (VarPlayerID.TUTORIAL), NOT /varbits!
        varp_281_resp = self._request_json("GET", "/varps?id=281")
        bank_resp = self._request_json("GET", "/bank")
        inv_resp = self._request_json("GET", "/inventory")

        # Support both `status["position"]` and live AgentServer `status["player"]["position"]`
        pos_raw = status.get("position") or (status.get("player") or {}).get("position")
        if isinstance(pos_raw, dict) and all(isinstance(pos_raw.get(k), int) for k in ("x", "y", "plane")):
            pos = {"x": pos_raw["x"], "y": pos_raw["y"], "plane": pos_raw["plane"], "quality": "KNOWN"}
        else:
            pos = {"x": None, "y": None, "plane": None, "quality": "UNKNOWN"}

        varp_val = varp_281_resp.get("value")
        varp_obj = (
            {"value": int(varp_val), "quality": "KNOWN"}
            if isinstance(varp_val, int)
            else {"value": None, "quality": "UNKNOWN"}
        )

        def _opt_metric(key: str) -> dict[str, Any]:
            v = status.get(key)
            return {"value": int(v), "quality": "KNOWN"} if isinstance(v, int) else {"value": None, "quality": "UNKNOWN"}

        bank_open = bool(bank_resp.get("open", False))
        pin_open = bool(bank_resp.get("pinPromptOpen", False))
        if bank_open and not pin_open and isinstance(bank_resp.get("items"), list):
            bank_quality = "KNOWN"
            bank_items = bank_resp["items"]
        else:
            bank_quality = "UNKNOWN"
            bank_items = None

        inv_items_raw = inv_resp.get("items")
        if isinstance(inv_items_raw, list):
            # Normalize live AgentServer `id` -> canonical `item_id` if needed
            norm_inv: list[dict[str, Any]] = []
            for it in inv_items_raw:
                if isinstance(it, dict):
                    norm_inv.append(
                        {
                            "item_id": int(it["item_id"] if "item_id" in it else it.get("id", 0)),
                            "name": str(it.get("name", "Unknown")),
                            "quantity": max(1, int(it.get("quantity", 1))),
                            "noted": bool(it.get("noted", False)),
                        }
                    )
            inv_obj: dict[str, Any] = {
                "free_slots": max(0, 28 - len(norm_inv)),
                "quality": "KNOWN",
                "items": norm_inv,
            }
        else:
            inv_obj = {"free_slots": None, "quality": "UNKNOWN", "items": None}

        obs: dict[str, Any] = {
            "schema_version": "1.0",
            "observation_id": f"http_obs_{self._obs_seq:06d}",
            "observed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "monotonic_ms": mono,
            "account_id": self.account_id,
            "session_id": self.session_id,
            "client_instance_id": self.client_instance_id,
            "session_epoch": self.session_epoch,
            "login_state": login_state,
            "game_tick": status.get("gameTick"),
            "world": status.get("world"),
            "position": pos,
            "tutorial_varp_281": varp_obj,
            "quest_points": _opt_metric("questPoints"),
            "total_level": _opt_metric("totalLevel"),
            "playtime_minutes": _opt_metric("playtimeMinutes"),
            "dialogue_open": bool(status.get("dialogueOpen", False)),
            "bank": {
                "is_open": bank_open,
                "pin_prompt_open": pin_open,
                "items_quality": bank_quality,
                "items": bank_items,
            },
            "inventory": inv_obj,
        }

        if any(k in status for k in ("hpCurrent", "runEnergy", "underAttack")):
            obs["vitals"] = {
                "hp_current": status.get("hpCurrent"),
                "hp_max": status.get("hpMax", 10),
                "run_energy": status.get("runEnergy", 100),
                "run_enabled": bool(status.get("runEnabled", False)),
                "under_attack": bool(status.get("underAttack", False)),
            }

        validated = validate_observation(obs)
        with self._lock:
            self._cached_obs = validated
            self._cached_at_mono_ms = mono
        return validated

    def execute_raw_command(self, command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        # Any state-mutating command invalidates the intra-tick read cache
        self.invalidate_tick_cache()

        if command_type == "PLUGIN_STOP":
            res = self._request_json("POST", "/scripts/stop", payload)
            self.plugin_running = False
            return res
        if command_type == "SAFE_LOGOUT":
            res = self._request_json("POST", "/logout", {})
            self.auto_login_suppressed = True
            return res
        if command_type == "TRIGGER_LOGIN":
            return self._request_json("POST", "/login", {"wait": False})
        if command_type == "WALK_TO_WORLD_POINT":
            return self._request_json("POST", "/walk", payload)
        if command_type == "CONSUME_FOOD":
            return self._request_json(
                "POST",
                "/inventory/interact",
                {"id": int(payload["item_id"]), "action": "Eat"},
            )
        if command_type == "SET_RUN_MODE":
            return self._request_json("POST", "/run", {"enabled": bool(payload.get("enabled", True))})
        if command_type == "INTERACT_OBJECT":
            return self._request_json("POST", "/objects/interact", payload)
        if command_type == "DIALOGUE_SELECT":
            return self._request_json("POST", "/dialogue/select", payload)
        if command_type == "DIALOGUE_CONTINUE":
            return self._request_json("POST", "/dialogue/continue", {})
        if command_type == "BANK_OPEN":
            return self._request_json("POST", "/bank/open", {})
        if command_type == "BANK_CLOSE":
            return self._request_json("POST", "/bank/close", {})
        if command_type == "BANK_DEPOSIT_INVENTORY":
            return self._request_json("POST", "/bank/deposit-all", {})
        if command_type == "BANK_WITHDRAW_ITEM":
            return self._request_json("POST", "/bank/withdraw", payload)
        raise ValueError(f"Unsupported HTTP command_type: {command_type}")

    def kill_process_tree(self) -> None:
        self.alive = False
        with self._lock:
            self._reset_conn()
