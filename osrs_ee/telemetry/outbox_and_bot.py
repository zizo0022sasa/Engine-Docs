"""Bounded persistent Telegram Outbox and RBAC Operator Gateway (PR-07 / T02, T03, T29)."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from osrs_ee.runtime.supervisor import SingleSlotSupervisor
from osrs_ee.security.redaction import Redactor
from osrs_ee.state.fsm import OperatorRole
from osrs_ee.state.store import ApprovalRejectedError, AuthorizationDeniedError, StateStore


class BoundedOutbox:
    """SQLite-backed non-blocking notification queue protecting P0_INCIDENT alerts on overflow."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        max_pending_messages: int = 200,
        redactor: Redactor | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.max_pending = max_pending_messages
        self.redactor = redactor or Redactor()
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS outbox (
                    msg_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id INTEGER NOT NULL,
                    priority TEXT NOT NULL,
                    text TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_monotonic REAL NOT NULL DEFAULT 0.0,
                    delivered INTEGER NOT NULL DEFAULT 0
                );
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def enqueue(self, *, chat_id: int, text: str, priority: str = "ROUTINE") -> int:
        if priority not in ("ROUTINE", "P0_INCIDENT"):
            raise ValueError(f"Unknown outbox priority: {priority!r}")
        clean_text = self.redactor.redact_text(text)[:4000]
        with self._lock:
            pending = self._conn.execute(
                "SELECT COUNT(*) AS c FROM outbox WHERE delivered = 0"
            ).fetchone()["c"]
            if pending >= self.max_pending:
                # Evict oldest ROUTINE message first; NEVER evict P0_INCIDENT alerts!
                victim = self._conn.execute(
                    "SELECT msg_id FROM outbox WHERE delivered = 0 AND priority = 'ROUTINE' ORDER BY msg_id ASC LIMIT 1"
                ).fetchone()
                if victim is not None:
                    self._conn.execute("DELETE FROM outbox WHERE msg_id = ?", (victim["msg_id"],))
                elif priority == "ROUTINE":
                    # Queue is 100% P0_INCIDENT alerts -> drop incoming ROUTINE rather than P0!
                    return -1

            cur = self._conn.execute(
                "INSERT INTO outbox (chat_id, priority, text) VALUES (?, ?, ?)",
                (chat_id, priority, clean_text),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def flush_due(
        self,
        sender: Callable[[int, str], tuple[bool, float]],
        *,
        now_monotonic: float | None = None,
        batch_size: int = 20,
    ) -> int:
        """Send due messages via `sender(chat_id, text) -> (ok, retry_after_sec)`.

        Never raises or blocks game workers if Telegram is unreachable or returns HTTP 429.
        """
        now_m = now_monotonic if now_monotonic is not None else time.monotonic()
        delivered_count = 0
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM outbox
                 WHERE delivered = 0 AND next_attempt_monotonic <= ?
                 ORDER BY CASE priority WHEN 'P0_INCIDENT' THEN 0 ELSE 1 END, msg_id ASC
                 LIMIT ?
                """,
                (now_m, batch_size),
            ).fetchall()

        for r in rows:
            try:
                ok, retry_after = sender(int(r["chat_id"]), str(r["text"]))
            except Exception:
                ok, retry_after = False, min(60.0, 2.0 ** min(6, int(r["attempts"]) + 1))

            with self._lock:
                if ok:
                    self._conn.execute(
                        "UPDATE outbox SET delivered = 1 WHERE msg_id = ?", (r["msg_id"],)
                    )
                    delivered_count += 1
                else:
                    backoff = max(retry_after, min(60.0, 2.0 ** min(6, int(r["attempts"]) + 1)))
                    self._conn.execute(
                        "UPDATE outbox SET attempts = attempts + 1, next_attempt_monotonic = ? WHERE msg_id = ?",
                        (now_m + backoff, r["msg_id"]),
                    )
                self._conn.commit()
        return delivered_count

    def pending_counts_by_priority(self) -> dict[str, int]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT priority, COUNT(*) AS c FROM outbox WHERE delivered = 0 GROUP BY priority"
            ).fetchall()
            return {r["priority"]: int(r["c"]) for r in rows}


class TelegramGateway:
    """RBAC-protected Telegram command & approval processor (zero secrets in responses!)."""

    def __init__(
        self,
        *,
        store: StateStore,
        outbox: BoundedOutbox,
        supervisor: SingleSlotSupervisor | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self.store = store
        self.outbox = outbox
        self.supervisor = supervisor
        self.redactor = redactor or Redactor()
        self.security_audit_events: list[str] = []

    def handle_incoming(
        self,
        *,
        user_id: int,
        chat_id: int,
        text_or_callback: str,
        now_utc: datetime | None = None,
    ) -> str | None:
        """Process a command or callback button; returns None (silent drop) on unauthorized user!"""
        now_dt = now_utc or datetime.now(timezone.utc)
        msg = text_or_callback.strip()

        # 1. Default-Deny base check: must at least be VIEWER to interact at all
        try:
            self.store.authorize_operator(
                user_id=user_id, chat_id=chat_id, minimum_role=OperatorRole.VIEWER
            )
        except AuthorizationDeniedError:
            self.security_audit_events.append(f"UNAUTHORIZED_DROP user={user_id} chat={chat_id}")
            return None

        # 2. Callback query: approve:<approval_id>:<plan_hash>
        if msg.startswith("approve:"):
            parts = msg.split(":", 2)
            if len(parts) != 3:
                return "❌ صيغة موافقة غير صالحة."
            _, approval_id, plan_hash = parts
            try:
                updated = self.store.consume_approval(
                    approval_id=approval_id,
                    user_id=user_id,
                    chat_id=chat_id,
                    plan_hash=plan_hash,
                    now_utc=now_dt,
                )
                return self.redactor.redact_text(
                    f"✅ تم استهلاك الموافقة لمرة واحدة بنجاح؛ انتقل [{updated['account_id']}] إلى حالة {updated['status']} للمصالحة."
                )
            except (AuthorizationDeniedError, ApprovalRejectedError) as exc:
                return self.redactor.redact_text(f"⛔ رُفضت الموافقة: {exc}")

        # 3. Text commands
        tokens = msg.split()
        cmd = tokens[0].lower() if tokens else ""

        if cmd == "/status":
            rows = self.store._conn.execute(
                "SELECT status, COUNT(*) AS c FROM accounts GROUP BY status ORDER BY status"
            ).fetchall()
            summary = ", ".join(f"{r['status']}={r['c']}" for r in rows) or "لا توجد حسابات"
            return self.redactor.redact_text(f"📊 ملخص الأسطول: {summary}")

        if cmd in ("/2fa", "/blocked"):
            try:
                self.store.authorize_operator(
                    user_id=user_id, chat_id=chat_id, minimum_role=OperatorRole.OPERATOR
                )
            except AuthorizationDeniedError:
                return "⛔ يتطلب دور OPERATOR على الأقل."
            rows = self.store._conn.execute(
                "SELECT account_id, region, reason_code, credential_ref FROM accounts WHERE status = 'BLOCKED' ORDER BY account_id"
            ).fetchall()
            if not rows:
                return "✅ لا توجد حسابات متوقفة حالياً."
            lines = ["🔐 قائمة الحسابات المتوقفة (بدون أي بيانات سرية):"]
            for r in rows:
                lines.append(
                    f"• [{r['account_id']}] | النطاق: {r['region']} | السبب: {r['reason_code']} | المرجع: {r['credential_ref']}"
                )
            return self.redactor.redact_text("\n".join(lines))

        if cmd == "/pause":
            try:
                self.store.authorize_operator(
                    user_id=user_id, chat_id=chat_id, minimum_role=OperatorRole.OPERATOR
                )
            except AuthorizationDeniedError:
                return "⛔ يتطلب دور OPERATOR."
            if self.supervisor is not None:
                self.supervisor.pause()
            return "⏸️ تم تفعيل إيقاف الأوامر الجديدة (PAUSE)."

        if cmd == "/drain":
            try:
                self.store.authorize_operator(
                    user_id=user_id, chat_id=chat_id, minimum_role=OperatorRole.OPERATOR
                )
            except AuthorizationDeniedError:
                return "⛔ يتطلب دور OPERATOR."
            res = self.supervisor.drain(now_utc=now_dt) if self.supervisor else "NO_SUPERVISOR"
            return f"🛑 نتيجة الإغلاق التعاوني (DRAIN): {res}"

        if cmd in ("/abort_all", "/stop_shift"):
            try:
                self.store.authorize_operator(
                    user_id=user_id, chat_id=chat_id, minimum_role=OperatorRole.ADMIN
                )
            except AuthorizationDeniedError:
                return "⛔ يتطلب دور ADMIN لتنفيذ الإجهاض الطارئ."
            res = self.supervisor.abort(now_utc=now_dt) if self.supervisor else "NO_SUPERVISOR"
            return f"🚨 تم تنفيذ الإجهاض الطارئ (ABORT): {res}"

        return "ℹ️ أمر غير معروف. الأوامر المتاحة: /status, /blocked, /2fa, /pause, /drain, /abort_all"

    @staticmethod
    def build_live_bot_sender(bot_token: str, *, token_rotation_confirmed: bool) -> Callable[[int, str], tuple[bool, float]]:
        """Create stdlib HTTP sender to Telegram Bot API, gated by DEC-PEND-03 token rotation check."""
        if not token_rotation_confirmed:
            raise PermissionError(
                "DEC-PEND-03 Gate: Confirm old leaked Telegram tokens were rotated on @BotFather "
                "(set TELEGRAM_TOKEN_ROTATED=true) before enabling live Telegram HTTP traffic."
            )
        api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

        def _send(chat_id: int, text: str) -> tuple[bool, float]:
            body = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
            req = urllib.request.Request(
                api_url, data=body, headers={"Content-Type": "application/json"}, method="POST"
            )
            try:
                with urllib.request.urlopen(req, timeout=5.0) as resp:
                    return (resp.status == 200, 0.0)
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    try:
                        err = json.loads(exc.read().decode("utf-8"))
                        retry = float(err.get("parameters", {}).get("retry_after", 5.0))
                    except Exception:
                        retry = 5.0
                    return (False, retry)
                return (False, 5.0)

        return _send
