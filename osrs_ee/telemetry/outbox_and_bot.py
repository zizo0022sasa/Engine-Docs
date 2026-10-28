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
from osrs_ee.security.vault import LocalSecretVault, ingest_pipe_lines
from osrs_ee.state.fsm import AccountStatus, OperatorRole
from osrs_ee.state.store import (
    ApprovalRejectedError,
    AuthorizationDeniedError,
    CooldownActiveError,
    StateStore,
)


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
        self._conn = sqlite3.connect(str(self.db_path), timeout=10.0, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA busy_timeout=10000;")
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
        vault: LocalSecretVault | None = None,
        redactor: Redactor | None = None,
        allowed_chat_ids: set[int] | None = None,
    ) -> None:
        self.store = store
        self.outbox = outbox
        self.supervisor = supervisor
        self.vault = vault
        self.redactor = redactor or (vault.redactor if vault is not None else Redactor())
        self.allowed_chat_ids = set(allowed_chat_ids) if allowed_chat_ids is not None else None
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

        # 0. Optional perimeter chat_id allowlist from .env (ALLOWED_CHAT_IDS + DEFAULT_CHANNEL_ID)
        if self.allowed_chat_ids is not None and chat_id not in self.allowed_chat_ids:
            self.security_audit_events.append(f"DISALLOWED_CHAT_DROP user={user_id} chat={chat_id}")
            return None

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

        # 3. Text commands (strip optional @bot_username suffix in groups/channels)
        tokens = msg.split()
        cmd = tokens[0].split("@", 1)[0].lower() if tokens else ""

        if cmd == "/status":
            self.store.reconcile_orphaned_leases(now_utc=now_dt)
            rows = self.store._conn.execute(
                "SELECT status, COUNT(*) AS c FROM accounts GROUP BY status ORDER BY status"
            ).fetchall()
            total_acc = sum(int(r["c"]) for r in rows)
            active_leases = int(
                self.store._conn.execute("SELECT COUNT(*) AS c FROM active_leases").fetchone()["c"]
            )
            summary = (
                ", ".join(f"{r['status']}={r['c']}" for r in rows)
                or "لا توجد حسابات مسجلة بعد (جاهز لاستقبال /add_account أو accounts.txt)"
            )
            return self.redactor.redact_text(
                f"📊 ملخص الأسطول (الإجمالي: {total_acc} | الجلسات النشطة: {active_leases}): {summary}"
            )

        if cmd == "/accounts":
            self.store.reconcile_orphaned_leases(now_utc=now_dt)
            rows = self.store._conn.execute(
                """
                SELECT account_id, status, stage, region, auth_mode, realized_gp
                  FROM accounts
                 ORDER BY account_id
                """
            ).fetchall()
            if not rows:
                return "📋 لا توجد حسابات مسجلة حالياً. استخدم /add_account لإضافة حساب جديد."
            lines = [f"📋 قائمة الحسابات المسجلة ({len(rows)}) — بدون أي بيانات سرية:"]
            for r in rows:
                lines.append(
                    f"• [{r['account_id']}] {r['status']} | {r['stage']} | {r['region']} | {r['auth_mode']} | {r['realized_gp']} GP"
                )
            return self.redactor.redact_text("\n".join(lines))

        if cmd in ("/add_account", "/ingest"):
            try:
                self.store.authorize_operator(
                    user_id=user_id, chat_id=chat_id, minimum_role=OperatorRole.ADMIN
                )
            except AuthorizationDeniedError:
                return "⛔ يتطلب دور ADMIN لإضافة حسابات جديدة إلى الخزنة المشفرة."
            if self.vault is None:
                return "⚠️ الخزنة المشفرة (LocalSecretVault) غير مربوطة ببوابة التليجرام الحالية."
            return self._handle_telegram_account_ingest(msg)

        if cmd == "/reconcile":
            try:
                self.store.authorize_operator(
                    user_id=user_id, chat_id=chat_id, minimum_role=OperatorRole.OPERATOR
                )
            except AuthorizationDeniedError:
                return "⛔ يتطلب دور OPERATOR لتنفيذ مصالحة الجلسات العالقة."
            target_acc = tokens[1].strip() if len(tokens) > 1 else None
            orphaned = self.store.reconcile_orphaned_leases(
                now_utc=now_dt, target_account_id=target_acc
            )
            query = "SELECT account_id FROM accounts WHERE status IN ('RECOVERY_REQUIRED', 'RECONCILING')"
            q_params: tuple[Any, ...] = ()
            if target_acc:
                query += " AND account_id = ?"
                q_params = (target_acc,)
            rec_rows = self.store._conn.execute(query + " ORDER BY account_id", q_params).fetchall()
            readied: list[str] = []
            cooling: list[str] = []
            for r in rec_rows:
                aid = str(r["account_id"])
                try:
                    self.store.acknowledge_crash_recovery(
                        aid,
                        diagnostic_note=f"Telegram /reconcile by operator {user_id}",
                        now_utc=now_dt,
                    )
                    readied.append(aid)
                except CooldownActiveError:
                    cooling.append(aid)
            if not orphaned and not readied and not cooling:
                return "✅ لا توجد جلسات معلقة أو حسابات بحاجة إلى مصالحة."
            parts_msg = ["🔧 تقرير مصالحة الأعطال (Crash Recovery):"]
            if orphaned:
                parts_msg.append(f"• جلسات يتيمة جرى تحريرها: {', '.join(orphaned)}")
            if readied:
                parts_msg.append(f"• حسابات عادت إلى READY: {', '.join(readied)}")
            if cooling:
                parts_msg.append(f"• حسابات بانتظار انتهاء التهدئة الإلزامية: {', '.join(cooling)}")
            return self.redactor.redact_text("\n".join(parts_msg))

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

        return (
            "ℹ️ أمر غير معروف. الأوامر المتاحة: "
            "/status, /accounts, /add_account, /reconcile, /blocked, /2fa, /pause, /drain, /abort_all"
        )

    def _handle_telegram_account_ingest(self, raw_msg: str) -> str:
        """Ingest 1..N pipe-delimited accounts into `LocalSecretVault` (`0600`) + `StateStore` with zero secret echo."""
        assert self.vault is not None
        first_split = raw_msg.split(None, 1)
        body = first_split[1].strip() if len(first_split) > 1 else ""
        cleaned_lines: list[str] = []
        for raw_ln in body.splitlines():
            ln = raw_ln.strip()
            if ln.startswith("```") and ln.endswith("```") and len(ln) > 6:
                ln = ln[3:-3].strip()
            elif ln in ("```", "`"):
                continue
            else:
                ln = ln.strip("`").strip()
            if ln and not ln.startswith("#"):
                cleaned_lines.append(ln)

        if not cleaned_lines:
            return (
                "ℹ️ صيغة الإضافة المشفرة (ADMIN فقط):\n"
                "• Legacy: `/add_account email@domain.com|Password|DIRECT|EG`\n"
                "• Jagex: `/add_account jx_session_id|jx_char_id|socks5://u:p@host:port|DE`"
            )

        try:
            summaries = ingest_pipe_lines(cleaned_lines, store=self.store, vault=self.vault)
            if self.store.snapshot_path is not None:
                self.store.export_snapshot()
        except ValueError as exc:
            err_l = str(exc).lower()
            if "pipe" in err_l:
                reason = "عدد الحقول المفصولة بـ `|` غير صالح (المطلوب 3 أو 4 حقول)."
            elif "region" in err_l:
                reason = "النطاق الجغرافي غير مدعوم (المدعوم: EG, DE, UK, US_EAST, US_WEST)."
            elif "proxy" in err_l or "scheme" in err_l or "hostname" in err_l:
                reason = "صيغة البروكسي غير صالحة (استخدم socks5://... أو host:port أو DIRECT)."
            else:
                reason = "تعذر التحقق من بنية السطر المدخل."
            return f"❌ رُفض الإدخال دون كشف أي بيانات سرية: {reason}"
        except PermissionError:
            return "⛔ تعذر الحفظ: صلاحيات ملف الخزنة (vault.json) غير آمنة."

        skipped = max(0, len(cleaned_lines) - len(summaries))
        if not summaries:
            return self.redactor.redact_text(
                f"ℹ️ لم تُضف حسابات جديدة (تم تخطي {skipped} حساب موجود مسبقاً في الخزنة)."
            )

        out_lines = [
            f"✅ تم إيداع وتشفير {len(summaries)} حساب في LocalSecretVault بنجاح (المتخطى للتكرار: {skipped}):"
        ]
        for s in summaries:
            out_lines.append(
                f"• [{s.account_id}] | {s.auth_mode} | {s.region} ({s.timezone}) | الحالة: {AccountStatus.PENDING_DISCOVERY.value} | {s.credential_ref}"
            )
        return self.redactor.redact_text("\n".join(out_lines))

    @staticmethod
    def _try_delete_telegram_message(bot_token: str, chat_id: int, message_id: int) -> bool:
        """Best-effort `POST /deleteMessage` to scrub plaintext credential commands from chat history."""
        api_url = f"https://api.telegram.org/bot{bot_token}/deleteMessage"
        body = json.dumps({"chat_id": chat_id, "message_id": message_id}).encode("utf-8")
        req = urllib.request.Request(
            api_url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=4.0) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                return bool(payload.get("ok"))
        except Exception:
            return False

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

    @staticmethod
    def fetch_bot_me(bot_token: str, *, timeout_sec: float = 6.0) -> dict[str, Any]:
        """Verify live Telegram Bot API connectivity via `GET /getMe` without exposing token."""
        api_url = f"https://api.telegram.org/bot{bot_token}/getMe"
        req = urllib.request.Request(api_url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not payload.get("ok"):
            raise RuntimeError("Telegram getMe returned ok=false")
        res = payload.get("result") or {}
        return {
            "id": res.get("id"),
            "username": res.get("username"),
            "first_name": res.get("first_name"),
            "can_join_groups": res.get("can_join_groups"),
        }

    def poll_updates_once(
        self,
        bot_token: str,
        *,
        offset: int = 0,
        timeout_sec: int = 2,
        token_rotation_confirmed: bool = False,
    ) -> tuple[int, int]:
        """Poll `GET /getUpdates` once, process authorized commands/callbacks, and reply cleanly."""
        sender = self.build_live_bot_sender(
            bot_token, token_rotation_confirmed=token_rotation_confirmed
        )
        api_url = (
            f"https://api.telegram.org/bot{bot_token}/getUpdates"
            f"?offset={int(offset)}&timeout={max(0, int(timeout_sec))}&limit=25"
        )
        req = urllib.request.Request(api_url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=max(5.0, float(timeout_sec) + 5.0)) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                # Another poller instance is active — return cleanly without crashing
                return (offset, 0)
            raise

        updates = data.get("result") or []
        next_offset = offset
        processed = 0

        for upd in updates:
            upd_id = int(upd.get("update_id", 0))
            if upd_id >= next_offset:
                next_offset = upd_id + 1

            cb = upd.get("callback_query")
            if isinstance(cb, dict):
                from_obj = cb.get("from") or {}
                msg_obj = cb.get("message") or {}
                chat_obj = msg_obj.get("chat") or {}
                uid = from_obj.get("id")
                cid = chat_obj.get("id")
                cb_data = str(cb.get("data") or "")
                if uid is not None and cid is not None and cb_data:
                    reply = self.handle_incoming(
                        user_id=int(uid), chat_id=int(cid), text_or_callback=cb_data
                    )
                    if reply:
                        sender(int(cid), reply)
                        processed += 1
                    cb_id = cb.get("id")
                    if cb_id:
                        try:
                            ack_req = urllib.request.Request(
                                f"https://api.telegram.org/bot{bot_token}/answerCallbackQuery",
                                data=json.dumps({"callback_query_id": cb_id}).encode("utf-8"),
                                headers={"Content-Type": "application/json"},
                                method="POST",
                            )
                            urllib.request.urlopen(ack_req, timeout=4.0).close()
                        except Exception:
                            pass
                continue

            msg = upd.get("message") or upd.get("edited_message") or upd.get("channel_post")
            if isinstance(msg, dict):
                text = str(msg.get("text") or "").strip()
                if not text:
                    continue
                chat_obj = msg.get("chat") or {}
                from_obj = msg.get("from") or {}
                cid = chat_obj.get("id")
                uid = from_obj.get("id") if from_obj.get("id") is not None else cid
                msg_id = msg.get("message_id")
                if uid is not None and cid is not None:
                    first_tok = text.split()[0].split("@", 1)[0].lower() if text.split() else ""
                    is_sensitive_cmd = first_tok in ("/add_account", "/ingest")
                    reply = self.handle_incoming(
                        user_id=int(uid), chat_id=int(cid), text_or_callback=text
                    )
                    scrubbed = False
                    if is_sensitive_cmd and msg_id is not None and reply is not None:
                        scrubbed = self._try_delete_telegram_message(
                            bot_token, int(cid), int(msg_id)
                        )
                    if reply:
                        if is_sensitive_cmd:
                            scrub_note = (
                                "\n🗑️ تم حذف رسالة البيانات الأصلية من المحادثة تلقائياً."
                                if scrubbed
                                else "\n🔒 تذكير أمني: احذف رسالتك الأصلية من المحادثة لحماية بيانات الدخول."
                            )
                            reply = f"{reply}{scrub_note}"
                        sender(int(cid), reply)
                        processed += 1

        # Also flush any pending outbox messages
        self.outbox.flush_due(sender)
        return (next_offset, processed)
