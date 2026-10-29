"""Exclusive GameAdapterGate enforcing lease/epoch fencing, telemetry off, and command journaling (PR-04)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from osrs_ee.contracts.models import validate_command_intent
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.state.store import StateStore


class AdapterSecurityError(PermissionError):
    """Raised when client launch flags or identity violate security invariants."""


class PolicyDeniedError(PermissionError):
    """Raised when a forbidden endpoint or post-MVP feature is invoked."""


ALLOWED_MVP_COMMANDS: frozenset[str] = frozenset(
    {
        "BANK_OPEN",
        "BANK_CLOSE",
        "BANK_DEPOSIT_INVENTORY",
        "BANK_DEPOSIT_EQUIPMENT",
        "BANK_WITHDRAW_ITEM",
        "INTERACT_OBJECT",
        "INTERACT_NPC",
        "USE_ITEM_ON_ITEM",
        "TAKE_GROUND_ITEM",
        "WIDGET_CLICK",
        "DIALOGUE_SELECT",
        "DIALOGUE_CONTINUE",
        "WALK_TO_WORLD_POINT",
        "CONSUME_FOOD",
        "SET_RUN_MODE",
        "TRIGGER_LOGIN",
        "PLUGIN_START",
        "PLUGIN_STOP",
        "SAFE_LOGOUT",
    }
)

PIPELINED_STREAM_COMMANDS: frozenset[str] = frozenset(
    {
        "WALK_TO_WORLD_POINT",
        "CONSUME_FOOD",
        "SET_RUN_MODE",
        "DIALOGUE_CONTINUE",
        "WIDGET_CLICK",
    }
)


def _is_client_response_accepted(raw_resp: dict[str, Any]) -> bool:
    """Normalize diverse AgentServerPlugin handler boolean keys (`ok`, `success`, `clicked`, `opened`, `closed`)."""
    return bool(
        raw_resp.get("ok")
        or raw_resp.get("success")
        or raw_resp.get("clicked")
        or raw_resp.get("opened")
        or raw_resp.get("closed")
    )


class GameAdapterGate:
    """Sole authorized gateway to read observations and dispatch commands to a client instance."""

    def __init__(self, store: StateStore, adapter: FakeGameAdapter) -> None:
        self.store = store
        self.adapter = adapter
        self._verify_telemetry_disabled()

    def _verify_telemetry_disabled(self) -> None:
        flags = set(self.adapter.jvm_flags)
        if "--disable-telemetry" not in flags or "-Dmicrobot.disableTelemetry=true" not in flags:
            raise AdapterSecurityError(
                "Refusing to bind client without --disable-telemetry and -Dmicrobot.disableTelemetry=true"
            )

    def bind_to_lease(
        self,
        *,
        account_id: str,
        session_id: str,
        lease_id: str,
        session_epoch: int,
    ) -> str:
        self._verify_telemetry_disabled()
        self.store.bind_client_instance(
            account_id=account_id,
            session_id=session_id,
            lease_id=lease_id,
            session_epoch=session_epoch,
            client_instance_id=self.adapter.client_instance_id,
        )
        return self.adapter.client_instance_id

    def read_verified_observation(
        self,
        *,
        account_id: str,
        session_id: str,
        lease_id: str,
        session_epoch: int,
        monotonic_ms: int = 1000,
    ) -> dict[str, Any]:
        self.store.verify_fencing_token(
            account_id=account_id,
            session_id=session_id,
            lease_id=lease_id,
            session_epoch=session_epoch,
            client_instance_id=self.adapter.client_instance_id,
        )
        return self.adapter.read_observation(monotonic_ms=monotonic_ms)

    def dispatch_verified_command(
        self,
        *,
        intent: dict[str, Any],
        postcondition_checker: Callable[[dict[str, Any]], bool],
    ) -> dict[str, Any]:
        """Execute Write-Ahead Journal -> Dispatch -> Post-Observation Verify -> Finalize Result."""
        cmd_type = intent.get("command_type", "")
        if cmd_type not in ALLOWED_MVP_COMMANDS:
            raise PolicyDeniedError(f"Command '{cmd_type}' is blocked by MVP allowlist policy")

        validate_command_intent(intent)

        # Step 1: Journal PREPARED -> DISPATCHING (also verifies lease, epoch, client_instance_id, no UNKNOWN pending)
        self.store.prepare_command_intent(intent)
        self.store.mark_command_dispatching(
            command_id=intent["command_id"],
            account_id=intent["account_id"],
            session_id=intent["session_id"],
            lease_id=intent["lease_id"],
            session_epoch=intent["session_epoch"],
            client_instance_id=intent["client_instance_id"],
        )

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        event_id = f"evt_{uuid.uuid4().hex[:12]}"

        # Step 2: Send over adapter boundary, catching transport uncertainty distinctly
        try:
            raw_resp = self.adapter.execute_raw_command(cmd_type, intent["payload"])
        except TimeoutError as exc:
            result = {
                "schema_version": "1.0",
                "command_id": intent["command_id"],
                "event_id": event_id,
                "session_id": intent["session_id"],
                "session_epoch": intent["session_epoch"],
                "transport_status": "TRANSPORT_TIMEOUT",
                "effect_status": "UNKNOWN",
                "completed_at": now_iso,
                "post_observation_id": None,
                "error_code": "TRANSPORT_TIMEOUT",
                "diagnostic_note": str(exc),
            }
            self.store.finalize_command_result(result)
            return result
        except ConnectionError as exc:
            result = {
                "schema_version": "1.0",
                "command_id": intent["command_id"],
                "event_id": event_id,
                "session_id": intent["session_id"],
                "session_epoch": intent["session_epoch"],
                "transport_status": "CONNECTION_ERROR",
                "effect_status": "UNKNOWN",
                "completed_at": now_iso,
                "post_observation_id": None,
                "error_code": "CONNECTION_ERROR",
                "diagnostic_note": str(exc),
            }
            self.store.finalize_command_result(result)
            return result

        # Step 3: Read post-observation to verify actual game effect (never trust HTTP 200 blindly!)
        post_obs = self.adapter.read_observation()
        effect_verified = _is_client_response_accepted(raw_resp) and postcondition_checker(post_obs)

        result = {
            "schema_version": "1.0",
            "command_id": intent["command_id"],
            "event_id": event_id,
            "session_id": intent["session_id"],
            "session_epoch": intent["session_epoch"],
            "transport_status": "HTTP_200",
            "effect_status": "VERIFIED" if effect_verified else "FAILED_VERIFIED",
            "completed_at": now_iso,
            "post_observation_id": post_obs["observation_id"],
            "error_code": None if effect_verified else str(raw_resp.get("reason", "POSTCONDITION_FAILED")),
            "diagnostic_note": "Effect verified by post-observation" if effect_verified else "Postcondition did not hold",
        }
        self.store.finalize_command_result(result)
        return result

    def dispatch_pipelined_command(self, *, intent: dict[str, Any]) -> dict[str, Any]:
        """Non-blocking stream dispatch for movement/eat/run (PR-12 / AUD-36 / ADR-009).

        Eliminates cascading synchronous post-observation polls in hot combat/movement paths;
        state confirmation occurs reactively on the next game tick via `ReactiveSafetyWatchdog`.
        """
        cmd_type = intent.get("command_type", "")
        if cmd_type not in PIPELINED_STREAM_COMMANDS:
            raise PolicyDeniedError(
                f"Command '{cmd_type}' is not eligible for non-blocking pipelined stream; use dispatch_verified_command"
            )
        validate_command_intent(intent)
        self.store.prepare_command_intent(intent)
        self.store.mark_command_dispatching(
            command_id=intent["command_id"],
            account_id=intent["account_id"],
            session_id=intent["session_id"],
            lease_id=intent["lease_id"],
            session_epoch=intent["session_epoch"],
            client_instance_id=intent["client_instance_id"],
        )

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        event_id = f"evt_{uuid.uuid4().hex[:12]}"
        try:
            raw_resp = self.adapter.execute_raw_command(cmd_type, intent["payload"])
        except (TimeoutError, ConnectionError) as exc:
            is_timeout = isinstance(exc, TimeoutError)
            result = {
                "schema_version": "1.0",
                "command_id": intent["command_id"],
                "event_id": event_id,
                "session_id": intent["session_id"],
                "session_epoch": intent["session_epoch"],
                "transport_status": "TRANSPORT_TIMEOUT" if is_timeout else "CONNECTION_ERROR",
                "effect_status": "FAILED_VERIFIED" if intent["idempotency_class"] == "IDEMPOTENT" else "UNKNOWN",
                "completed_at": now_iso,
                "post_observation_id": None,
                "error_code": "TRANSPORT_TIMEOUT" if is_timeout else "CONNECTION_ERROR",
                "diagnostic_note": str(exc),
            }
            self.store.finalize_command_result(result)
            return result

        accepted = _is_client_response_accepted(raw_resp)
        result = {
            "schema_version": "1.0",
            "command_id": intent["command_id"],
            "event_id": event_id,
            "session_id": intent["session_id"],
            "session_epoch": intent["session_epoch"],
            "transport_status": "HTTP_200",
            "effect_status": "PIPELINED_NEXT_TICK" if accepted else "FAILED_VERIFIED",
            "completed_at": now_iso,
            "post_observation_id": None,
            "error_code": None if accepted else str(raw_resp.get("reason", "REJECTED_BY_CLIENT")),
            "diagnostic_note": "Accepted in non-blocking pipeline; verified reactively on next game tick",
        }
        self.store.finalize_command_result(result)
        return result
