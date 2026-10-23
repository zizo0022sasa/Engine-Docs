"""Loopback-only HTTP client for Microbot AgentServerPlugin (PR-05a / T06, T10).

Strictly binds to 127.0.0.1, sends X-Agent-Token, reads Varp 281 via /varps?id=281
(never /varbits?id=281), and normalizes closed-bank responses to UNKNOWN (None).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

from osrs_ee.contracts.models import validate_observation


class MicrobotHttpAdapter:
    """Loopback HTTP client conforming to the same interface as FakeGameAdapter."""

    def __init__(
        self,
        *,
        account_id: str,
        session_id: str,
        session_epoch: int,
        client_instance_id: str,
        port: int,
        agent_token: str,
        jvm_flags: list[str],
        host: str = "127.0.0.1",
        timeout_sec: float = 4.0,
    ) -> None:
        if host != "127.0.0.1":
            raise ValueError(f"AgentServer HTTP adapter MUST bind to 127.0.0.1 only, got {host!r}")
        if not agent_token or len(agent_token) < 16:
            raise ValueError("Strong per-session X-Agent-Token (>=16 chars) is required")

        self.account_id = account_id
        self.session_id = session_id
        self.session_epoch = session_epoch
        self.client_instance_id = client_instance_id
        self.jvm_flags = list(jvm_flags)
        self.base_url = f"http://127.0.0.1:{port}"
        self._token = agent_token
        self.timeout_sec = timeout_sec
        self.alive = True
        self.plugin_running = False
        self.auto_login_suppressed = False
        self.player_dead = False
        self.equipment_items: list[str] = []
        self._obs_seq = 0

    def _request_json(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if path.startswith("/scripts/deploy"):
            raise PermissionError("Raw /scripts/deploy source upload is forbidden by ADR-006")
        url = f"{self.base_url}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"X-Agent-Token": self._token, "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise PermissionError(f"AgentServer rejected X-Agent-Token with HTTP {exc.code}") from exc
            raise ConnectionError(f"AgentServer HTTP {exc.code} on {path}") from exc
        except TimeoutError:
            raise
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise TimeoutError(f"Timed out calling {path}") from exc
            raise ConnectionError(f"Connection failed on {path}: {exc.reason}") from exc

    def read_observation(self, *, monotonic_ms: int | None = None) -> dict[str, Any]:
        mono = monotonic_ms if monotonic_ms is not None else int(time.monotonic() * 1000)
        status = self._request_json("GET", "/status")
        # Query /varps?id=281 (VarPlayerID.TUTORIAL), NOT /varbits!
        varp_281_resp = self._request_json("GET", "/varps?id=281")
        bank_resp = self._request_json("GET", "/bank")
        inv_resp = self._request_json("GET", "/inventory")

        self._obs_seq += 1
        pos_raw = status.get("position")
        if isinstance(pos_raw, dict) and all(k in pos_raw for k in ("x", "y", "plane")):
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
            inv_obj = {
                "free_slots": max(0, 28 - len(inv_items_raw)),
                "quality": "KNOWN",
                "items": inv_items_raw,
            }
        else:
            inv_obj = {"free_slots": None, "quality": "UNKNOWN", "items": None}

        obs = {
            "schema_version": "1.0",
            "observation_id": f"http_obs_{self._obs_seq:06d}",
            "observed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "monotonic_ms": mono,
            "account_id": self.account_id,
            "session_id": self.session_id,
            "client_instance_id": self.client_instance_id,
            "session_epoch": self.session_epoch,
            "login_state": status.get("loginState", "UNKNOWN"),
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
        return validate_observation(obs)

    def execute_raw_command(self, command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if command_type == "PLUGIN_STOP":
            res = self._request_json("POST", "/scripts/stop", payload)
            self.plugin_running = False
            return res
        if command_type == "SAFE_LOGOUT":
            res = self._request_json("POST", "/logout", {})
            self.auto_login_suppressed = True
            return res
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
