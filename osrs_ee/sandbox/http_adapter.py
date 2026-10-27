"""Loopback-only Keep-Alive HTTP client for Microbot AgentServerPlugin (PR-05a + PR-12 + PR-12.1).

Key Field-Remediation & Live Wire-Compatibility Guarantees:
1. Persistent HTTP/1.1 Keep-Alive `HTTPConnection` on `127.0.0.1:<port>` (eliminates cascading TCP handshake overhead).
2. Single-Tick Snapshot Coalescing (`coalesce_window_ms`) so safety, navigation, and task engines share 1 tick read.
3. Short-Circuit Login Guard: when `/state` (or `/status`) reports `loggedIn=False` / `LOGIN_SCREEN`, returns
   cleanly in 1 HTTP call without polling `/varps`, `/bank`, `/inventory`, `/skills`, or `/dialogue`.
4. Default `status_endpoint="/state"` (matching live Microbot `StateHandler`) with sticky automatic `404`
   discovery fallback (`/state <-> /status`) for legacy mock servers.
5. Null-safe Hitpoints / Vitals normalization across all Microbot payload variants (`hitpoints` / `maxHitpoints`,
   `hpCurrent` / `hpMax`, nested `player.*`, and `GET /skills?name=Hitpoints` `boostedLevel` / `level`),
   preserving `0` HP accurately without ever emitting `hp_current: None`.
6. Full live `WIDGET_CLICK` -> `POST /widgets/click` support (`groupId`+`childId` or `text`) and response
   boolean normalization (`ok`, `success`, `clicked`, `opened`, `closed`).
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


class EndpointNotFoundError(ConnectionError):
    """Raised when an AgentServer HTTP path returns HTTP 404 Not Found."""


def _first_int(*candidates: Any) -> int | None:
    """Return the first numeric candidate as int without treating 0 as falsy."""
    for val in candidates:
        if isinstance(val, bool):
            continue
        if isinstance(val, (int, float)):
            return int(val)
    return None


def _normalize_command_response(res: dict[str, Any]) -> dict[str, Any]:
    """Ensure canonical `ok` boolean exists alongside live AgentServer keys (`success`/`clicked`/`opened`/`closed`)."""
    out = dict(res)
    if "ok" not in out:
        for key in ("success", "clicked", "opened", "closed"):
            if key in out and isinstance(out[key], bool):
                out["ok"] = out[key]
                break
    return out


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
        status_endpoint: str = "/state",
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
        self._active_status_endpoint = status_endpoint

        self.alive = True
        self.plugin_running = False
        self.auto_login_suppressed = False
        self.player_dead = False
        self.equipment_items: list[str] = []
        self.last_dialogue_snapshot: dict[str, Any] = {}

        self._lock = threading.RLock()
        self._conn: http.client.HTTPConnection | None = None
        self._obs_seq = 0
        self._cached_obs: dict[str, Any] | None = None
        self._cached_at_mono_ms: int = -1_000_000
        self._supports_skills_endpoint: bool = True
        self._supports_dialogue_endpoint: bool = True
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
                    if resp.status == 404:
                        raise EndpointNotFoundError(f"AgentServer HTTP 404 on {path}")
                    if resp.status >= 400:
                        raise ConnectionError(f"AgentServer HTTP {resp.status} on {path}")
                    return json.loads(raw) if raw else {}
                except (PermissionError, EndpointNotFoundError):
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

    def _fetch_status_with_fallback(self) -> dict[str, Any]:
        """Query `/state` by default and sticky-fallback to `/status` (or vice versa) on HTTP 404."""
        primary = self._active_status_endpoint
        try:
            return self._request_json("GET", primary)
        except EndpointNotFoundError:
            alt = "/status" if primary == "/state" else "/state"
            res = self._request_json("GET", alt)
            self._active_status_endpoint = alt
            return res

    def _fetch_optional_json(self, path: str, *, flag_attr: str) -> dict[str, Any] | None:
        if not getattr(self, flag_attr, True):
            return None
        try:
            return self._request_json("GET", path)
        except EndpointNotFoundError:
            setattr(self, flag_attr, False)
            return None

    @staticmethod
    def _parse_login_state(status: dict[str, Any]) -> str:
        if "loginState" in status and isinstance(status["loginState"], str):
            return status["loginState"]
        if "loggedIn" in status:
            return "LOGGED_IN" if bool(status["loggedIn"]) else "LOGIN_SCREEN"
        game_state = status.get("gameState")
        if isinstance(game_state, str) and game_state:
            return "LOGGED_IN" if game_state == "LOGGED_IN" else "LOGIN_SCREEN"
        return "UNKNOWN"

    def _extract_normalized_vitals(self, status: dict[str, Any]) -> dict[str, Any] | None:
        """Normalize HP, Run Energy, and Combat status across all Microbot `/state` + `/skills` formats."""
        player_obj = status.get("player") if isinstance(status.get("player"), dict) else {}

        hp_curr = _first_int(
            status.get("hpCurrent"),
            status.get("hitpoints"),
            player_obj.get("hpCurrent"),
            player_obj.get("hitpoints"),
        )
        hp_max = _first_int(
            status.get("hpMax"),
            status.get("maxHitpoints"),
            player_obj.get("hpMax"),
            player_obj.get("maxHitpoints"),
        )

        # Upstream Microbot `StateHandler` omits exact HP in `/state` and serves it on `GET /skills?name=Hitpoints`
        if hp_curr is None and ("loggedIn" in status or "player" in status):
            skills_resp = self._fetch_optional_json("/skills?name=Hitpoints", flag_attr="_supports_skills_endpoint")
            if isinstance(skills_resp, dict) and isinstance(skills_resp.get("skills"), list):
                for sk in skills_resp["skills"]:
                    if isinstance(sk, dict) and str(sk.get("name", "")).lower() == "hitpoints":
                        hp_curr = _first_int(sk.get("boostedLevel"), sk.get("level"))
                        if hp_max is None:
                            hp_max = _first_int(sk.get("level"), sk.get("boostedLevel"))
                        break

        has_any_vital_signal = (
            hp_curr is not None
            or hp_max is not None
            or any(
                k in status
                for k in ("hpCurrent", "hitpoints", "hpMax", "maxHitpoints", "runEnergy", "runEnabled", "underAttack")
            )
            or bool(player_obj)
        )
        if not has_any_vital_signal:
            return None

        resolved_max = max(1, hp_max if hp_max is not None else (hp_curr if hp_curr is not None and hp_curr > 0 else 10))
        resolved_curr = hp_curr if hp_curr is not None else resolved_max
        run_energy = _first_int(status.get("runEnergy"), player_obj.get("runEnergy"))
        resolved_run_energy = max(0, min(100, run_energy if run_energy is not None else 100))
        run_enabled = bool(status.get("runEnabled", player_obj.get("runEnabled", False)))
        under_attack = bool(
            status.get(
                "underAttack",
                player_obj.get("underAttack", player_obj.get("interacting", False)),
            )
        )

        return {
            "hp_current": resolved_curr,
            "hp_max": resolved_max,
            "run_energy": resolved_run_energy,
            "run_enabled": run_enabled,
            "under_attack": under_attack,
        }

    def probe_session_state(self) -> dict[str, Any]:
        """Single lightweight HTTP probe used by `SessionLifecycleManager` (`DISCONNECTED / AT_LOGIN / IN_GAME`)."""
        status = self._fetch_status_with_fallback()
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

        status = self._fetch_status_with_fallback()
        login_state = self._parse_login_state(status)
        self._obs_seq += 1

        # PR-12 / Defect #3 Fix: Short-circuit immediately when NOT logged in!
        # Never fire /varps, /bank, /inventory, /skills, or /dialogue against an unauthenticated login screen!
        if login_state != "LOGGED_IN":
            self.last_dialogue_snapshot = {}
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

        # Dialogue state: prefer explicit `dialogueOpen` if present, else query `/dialogue` when live `/state` is used
        if "dialogueOpen" in status:
            dialogue_open = bool(status.get("dialogueOpen", False))
        elif "loggedIn" in status or "player" in status:
            dlg_resp = self._fetch_optional_json("/dialogue", flag_attr="_supports_dialogue_endpoint")
            if isinstance(dlg_resp, dict):
                self.last_dialogue_snapshot = dlg_resp
                dialogue_open = bool(dlg_resp.get("inDialogue", dlg_resp.get("dialogueOpen", False)))
            else:
                self.last_dialogue_snapshot = {}
                dialogue_open = False
        else:
            dialogue_open = False

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
            "dialogue_open": dialogue_open,
            "bank": {
                "is_open": bank_open,
                "pin_prompt_open": pin_open,
                "items_quality": bank_quality,
                "items": bank_items,
            },
            "inventory": inv_obj,
        }

        normalized_vitals = self._extract_normalized_vitals(status)
        if normalized_vitals is not None:
            obs["vitals"] = normalized_vitals

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
            return _normalize_command_response(res)
        if command_type == "SAFE_LOGOUT":
            res = self._request_json("POST", "/logout", {})
            self.auto_login_suppressed = True
            return _normalize_command_response(res)
        if command_type == "TRIGGER_LOGIN":
            return _normalize_command_response(self._request_json("POST", "/login", {"wait": False}))
        if command_type == "WALK_TO_WORLD_POINT":
            return _normalize_command_response(self._request_json("POST", "/walk", payload))
        if command_type == "CONSUME_FOOD":
            return _normalize_command_response(
                self._request_json(
                    "POST",
                    "/inventory/interact",
                    {"id": int(payload["item_id"]), "action": "Eat"},
                )
            )
        if command_type == "SET_RUN_MODE":
            try:
                return _normalize_command_response(
                    self._request_json("POST", "/run", {"enabled": bool(payload.get("enabled", True))})
                )
            except EndpointNotFoundError:
                # Upstream AgentServerPlugin toggles run via Minimap Run Orb widget (160, 27)
                return _normalize_command_response(
                    self._request_json("POST", "/widgets/click", {"groupId": 160, "childId": 27})
                )
        if command_type == "WIDGET_CLICK":
            req_body: dict[str, Any] = {}
            if "groupId" in payload or "group_id" in payload:
                req_body["groupId"] = int(payload["groupId"] if "groupId" in payload else payload["group_id"])
            if "childId" in payload or "child_id" in payload:
                req_body["childId"] = int(payload["childId"] if "childId" in payload else payload["child_id"])
            if "text" in payload and payload["text"] is not None:
                req_body["text"] = str(payload["text"])
            if not (("groupId" in req_body and "childId" in req_body) or "text" in req_body):
                raise ValueError("WIDGET_CLICK requires ('groupId' and 'childId') or 'text'")
            return _normalize_command_response(self._request_json("POST", "/widgets/click", req_body))
        if command_type == "INTERACT_OBJECT":
            return _normalize_command_response(self._request_json("POST", "/objects/interact", payload))
        if command_type == "DIALOGUE_SELECT":
            return _normalize_command_response(self._request_json("POST", "/dialogue/select", payload))
        if command_type == "DIALOGUE_CONTINUE":
            return _normalize_command_response(self._request_json("POST", "/dialogue/continue", {}))
        if command_type == "BANK_OPEN":
            return _normalize_command_response(self._request_json("POST", "/bank/open", {}))
        if command_type == "BANK_CLOSE":
            return _normalize_command_response(self._request_json("POST", "/bank/close", {}))
        if command_type == "BANK_DEPOSIT_INVENTORY":
            try:
                return _normalize_command_response(self._request_json("POST", "/bank/deposit", {"all": True}))
            except EndpointNotFoundError:
                return _normalize_command_response(self._request_json("POST", "/bank/deposit-all", {}))
        if command_type == "BANK_WITHDRAW_ITEM":
            withdraw_body = dict(payload)
            if "item_id" in withdraw_body and "id" not in withdraw_body:
                withdraw_body["id"] = int(withdraw_body.pop("item_id"))
            return _normalize_command_response(self._request_json("POST", "/bank/withdraw", withdraw_body))
        raise ValueError(f"Unsupported HTTP command_type: {command_type}")

    def kill_process_tree(self) -> None:
        self.alive = False
        with self._lock:
            self._reset_conn()
