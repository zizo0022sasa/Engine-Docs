"""Smart Event Notifier wired to BoundedOutbox for zero-spam fleet telemetry (PR-17 / T56, T57).

Constitutional SRE Rule:
- Emits ONLY the 4 high-value operator lifecycle/incident notifications (zero per-tick log spam):
  1. `SESSION_STARTED` (`🚀 بدء جلسة حساب`)
  2. `TASK_COMPLETED`  (`🏆 إتمام المهمة وإيداع الغنائم في البنك`)
  3. `P0_INCIDENT`     (`🚨 تنبيه طوارئ P0` — protected from eviction in `BoundedOutbox`)
  4. `CRASH_RECOVERED` (`🔧 إشعار استرداد تلقائي بعد انقطاع`)
- All alerts are redacted via `Redactor`, persisted atomically in SQLite WAL `outbox.db`,
  deduplicated per session/incident, and flushed best-effort without ever blocking or crashing
  the 0.6s OSRS game tick loop if Telegram is unreachable or rate-limited.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from osrs_ee.security.redaction import Redactor
from osrs_ee.telemetry.outbox_and_bot import BoundedOutbox, TelegramGateway

if TYPE_CHECKING:
    from osrs_ee.state.store import StateStore


class SmartEventKind(StrEnum):
    """Strict 4-event allowlist preventing per-tick notification spam."""

    SESSION_STARTED = "SESSION_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    P0_INCIDENT = "P0_INCIDENT"
    CRASH_RECOVERED = "CRASH_RECOVERED"


def _parse_chat_ids_csv(raw: str | None) -> list[int]:
    if not raw:
        return []
    out: list[int] = []
    for part in raw.split(","):
        token = part.strip()
        if not token or token.startswith("<"):
            continue
        try:
            cid = int(token)
        except ValueError:
            continue
        if cid not in out:
            out.append(cid)
    return out


def resolve_authorized_alert_chats(
    *,
    store: StateStore | None = None,
    explicit_chat_ids: Sequence[int] | None = None,
) -> tuple[int, ...]:
    """Resolve authorized Telegram destination chat IDs in deterministic priority cascade."""
    if explicit_chat_ids is not None:
        deduped: list[int] = []
        for cid in explicit_chat_ids:
            icid = int(cid)
            if icid not in deduped:
                deduped.append(icid)
        return tuple(deduped)

    for env_key in (
        "OSRS_EE_TELEGRAM_ADMIN_CHAT_ID",
        "DEFAULT_CHANNEL_ID",
        "TELEGRAM_CHAT_ID",
        "ALLOWED_CHAT_IDS",
    ):
        parsed = _parse_chat_ids_csv(os.environ.get(env_key))
        if parsed:
            return tuple(parsed)

    resolved: list[int] = []
    if store is not None:
        try:
            with store._lock:
                rows = store._conn.execute(
                    """
                    SELECT DISTINCT chat_id
                      FROM operator_acl
                     WHERE role IN ('ADMIN', 'OPERATOR')
                     ORDER BY chat_id
                    """
                ).fetchall()
            for row in rows:
                cid = int(row["chat_id"])
                if cid not in resolved:
                    resolved.append(cid)
        except Exception:
            pass

    return tuple(resolved)


class SmartEventNotifier:
    """Zero-spam event bridge from `ExecutionEngineKernel`, `StateStore`, and CLI to `BoundedOutbox`."""

    def __init__(
        self,
        outbox: BoundedOutbox,
        *,
        target_chat_ids: Sequence[int] = (),
        sender: Callable[[int, str], tuple[bool, float]] | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self.outbox = outbox
        self.target_chat_ids: tuple[int, ...] = tuple(dict.fromkeys(int(c) for c in target_chat_ids))
        self.sender = sender
        self.redactor = redactor or outbox.redactor
        self._lock = threading.RLock()
        self._emitted_keys: set[str] = set()
        self.emitted_events: list[dict[str, Any]] = []

    @classmethod
    def from_runtime_env(
        cls,
        *,
        outbox: BoundedOutbox,
        store: StateStore | None = None,
        redactor: Redactor | None = None,
        explicit_chat_ids: Sequence[int] | None = None,
        enable_live_http: bool = True,
    ) -> SmartEventNotifier:
        """Build a `SmartEventNotifier` wired to `.env` + `operator_acl` authorized chats."""
        eff_redactor = redactor or outbox.redactor
        chats = resolve_authorized_alert_chats(store=store, explicit_chat_ids=explicit_chat_ids)

        bot_token = (
            os.environ.get("OSRS_EE_TELEGRAM_BOT_TOKEN", "").strip()
            or os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        )
        if bot_token and not bot_token.startswith("<"):
            eff_redactor.register_secret(bot_token)

        rotated_raw = (
            os.environ.get("TELEGRAM_TOKEN_ROTATED", "false").strip().lower()
        )
        token_rotated = rotated_raw in ("1", "true", "yes") or bool(
            os.environ.get("OSRS_EE_TELEGRAM_BOT_TOKEN", "").strip()
        )

        if "PYTEST_CURRENT_TEST" in os.environ and not os.environ.get("OSRS_EE_ALLOW_LIVE_TELEGRAM_IN_TESTS"):
            enable_live_http = False

        sender: Callable[[int, str], tuple[bool, float]] | None = None
        if enable_live_http and chats and bot_token and not bot_token.startswith("<") and token_rotated:
            try:
                sender = TelegramGateway.build_live_bot_sender(
                    bot_token,
                    token_rotation_confirmed=True,
                )
            except Exception:
                sender = None

        return cls(
            outbox=outbox,
            target_chat_ids=chats,
            sender=sender,
            redactor=eff_redactor,
        )

    @staticmethod
    def _format_slot_port(slot_id: int | None, port: int | None) -> str:
        parts: list[str] = []
        if slot_id is not None:
            parts.append(f"Slot {int(slot_id)}")
        if port is not None:
            parts.append(f"Port {int(port)}")
        return " | ".join(parts) if parts else "Slot 0"

    def _dispatch(
        self,
        *,
        kind: SmartEventKind,
        dedupe_key: str,
        text: str,
        priority: str,
        metadata: dict[str, Any],
    ) -> list[int]:
        if kind not in SmartEventKind:
            raise ValueError(f"Unsupported notification kind: {kind!r}")

        clean_text = self.redactor.redact_text(text)
        msg_ids: list[int] = []
        with self._lock:
            if dedupe_key in self._emitted_keys:
                return []
            self._emitted_keys.add(dedupe_key)

            for chat_id in self.target_chat_ids:
                mid = self.outbox.enqueue(
                    chat_id=int(chat_id),
                    text=clean_text,
                    priority=priority,
                )
                if mid > 0:
                    msg_ids.append(mid)

            self.emitted_events.append(
                {
                    "kind": kind.value,
                    "priority": priority,
                    "dedupe_key": dedupe_key,
                    "message_ids": tuple(msg_ids),
                    "text": clean_text,
                    **metadata,
                }
            )

        if self.sender is not None and msg_ids:
            try:
                self.outbox.flush_due(self.sender)
            except Exception:
                # Never allow transient Telegram HTTP errors to affect game execution
                pass

        return msg_ids

    # ------------------------------------------------------------------
    # 1. 🚀 Session Started
    # ------------------------------------------------------------------
    def notify_session_started(
        self,
        *,
        account_id: str,
        session_id: str,
        task_id: str,
        slot_id: int | None = 0,
        port: int | None = None,
    ) -> list[int]:
        slot_label = self._format_slot_port(slot_id, port)
        text = (
            "🚀 بدء جلسة حساب\n"
            f"• الحساب: [{account_id}]\n"
            f"• المهمة: {task_id}\n"
            f"• المنفذ / الفتحة: {slot_label}\n"
            f"• الجلسة: {session_id}"
        )
        return self._dispatch(
            kind=SmartEventKind.SESSION_STARTED,
            dedupe_key=f"start:{account_id}:{session_id}",
            text=text,
            priority="ROUTINE",
            metadata={
                "account_id": account_id,
                "session_id": session_id,
                "task_id": task_id,
                "slot_id": slot_id,
                "port": port,
            },
        )

    # ------------------------------------------------------------------
    # 2. 🏆 Task Completed & Loot Banked
    # ------------------------------------------------------------------
    def notify_task_completed(
        self,
        *,
        account_id: str,
        session_id: str,
        task_id: str,
        slot_id: int | None = 0,
        port: int | None = None,
        coins_gp: int = 0,
        loot_summary: Sequence[str] = (),
        session_closed: bool = True,
        detail: str = "",
    ) -> list[int]:
        slot_label = self._format_slot_port(slot_id, port)
        loot_str = "، ".join(loot_summary) if loot_summary else "لا توجد غنائم إضافية غير العملات"
        close_str = "تم إغلاق الجلسة بأمان (RESTING)" if session_closed else "بانتظار الإغلاق الآمن"
        lines = [
            "🏆 إتمام المهمة وإيداع الغنائم في البنك",
            f"• الحساب: [{account_id}]",
            f"• المهمة: {task_id}",
            f"• المنفذ / الفتحة: {slot_label}",
            f"• العملات (Coins): {int(coins_gp):,} GP",
            f"• الغنائم المودعة: {loot_str}",
            f"• حالة الجلسة: {close_str}",
        ]
        if detail:
            lines.append(f"• الملاحظة: {detail}")
        return self._dispatch(
            kind=SmartEventKind.TASK_COMPLETED,
            dedupe_key=f"complete:{account_id}:{session_id}:{task_id}",
            text="\n".join(lines),
            priority="ROUTINE",
            metadata={
                "account_id": account_id,
                "session_id": session_id,
                "task_id": task_id,
                "coins_gp": int(coins_gp),
                "loot_summary": tuple(loot_summary),
                "session_closed": bool(session_closed),
            },
        )

    # ------------------------------------------------------------------
    # 3. 🚨 P0 Emergency Incident (Protected from Outbox Eviction)
    # ------------------------------------------------------------------
    def notify_p0_incident(
        self,
        *,
        account_id: str,
        session_id: str,
        reason_code: str,
        task_id: str = "",
        slot_id: int | None = 0,
        port: int | None = None,
        detail: str = "",
    ) -> list[int]:
        slot_label = self._format_slot_port(slot_id, port)
        lines = [
            "🚨 تنبيه طوارئ P0 (P0_INCIDENT)",
            f"• الحساب: [{account_id}]",
            f"• سبب الطوارئ: {reason_code}",
            f"• المهمة: {task_id or 'UNKNOWN'} | {slot_label}",
        ]
        if detail:
            lines.append(f"• التفاصيل: {detail}")
        return self._dispatch(
            kind=SmartEventKind.P0_INCIDENT,
            dedupe_key=f"p0:{account_id}:{session_id}:{reason_code}",
            text="\n".join(lines),
            priority="P0_INCIDENT",
            metadata={
                "account_id": account_id,
                "session_id": session_id,
                "reason_code": reason_code,
                "task_id": task_id,
                "detail": detail,
            },
        )

    # ------------------------------------------------------------------
    # 4. 🔧 Automatic Crash Recovery Reconciliation
    # ------------------------------------------------------------------
    def notify_crash_recovered(
        self,
        *,
        account_id: str,
        recovery_key: str,
        previous_status: str = "RECOVERY_REQUIRED",
        new_status: str = "READY",
        diagnostic_note: str = "",
    ) -> list[int]:
        lines = [
            "🔧 استرداد تلقائي بعد انقطاع (Crash Recovery)",
            f"• الحساب: [{account_id}]",
            f"• الانتقال الآمن: {previous_status} -> {new_status}",
        ]
        if diagnostic_note:
            lines.append(f"• التشخيص: {diagnostic_note}")
        return self._dispatch(
            kind=SmartEventKind.CRASH_RECOVERED,
            dedupe_key=f"recovery:{account_id}:{recovery_key}",
            text="\n".join(lines),
            priority="ROUTINE",
            metadata={
                "account_id": account_id,
                "previous_status": previous_status,
                "new_status": new_status,
                "diagnostic_note": diagnostic_note,
            },
        )
