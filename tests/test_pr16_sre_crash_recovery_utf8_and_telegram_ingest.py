"""Acceptance Tests for PR-16 Field Architecture Alignment & SRE Hardening (T52..T55):
- T52: Power-outage orphaned lease auto-recovery (`RUNNING -> RECOVERY_REQUIRED -> RECONCILING -> READY -> WORK`)
       without disturbing active live multi-account slots, plus legacy SQLite schema auto-migration.
- T53: Dedicated `python -m osrs_ee reconcile` CLI subcommand and mandatory `cooldown_until` preservation.
- T54: Windows `cp1252` (`charmap`) `UnicodeEncodeError` immunity across `telegram-check` & `_safe_print`.
- T55: RBAC-protected Telegram `/add_account`, `/accounts`, `/reconcile`, zero-secret error handling,
       and automatic `POST /deleteMessage` plaintext credential scrubbing.
"""

from __future__ import annotations

import io
import json
import os
import sqlite3
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from osrs_ee.cli import _safe_print, main
from osrs_ee.security.vault import LocalSecretVault
from osrs_ee.state import (
    AccountStatus,
    CommandJournalState,
    CooldownActiveError,
    OperatorRole,
    ReasonCode,
    StateStore,
)
from osrs_ee.telemetry import BoundedOutbox, TelegramGateway


def _start_mock_microbot_server() -> ThreadingHTTPServer:
    class _Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _json(self, code: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            p = self.path.split("?", 1)[0]
            if p == "/state":
                self._json(
                    200,
                    {
                        "loginState": "LOGGED_IN",
                        "world": 308,
                        "position": {"x": 3105, "y": 3420, "plane": 0},
                        "hitpoints": 10,
                        "maxHitpoints": 10,
                        "questPoints": 0,
                        "totalLevel": 35,
                    },
                )
            elif p in ("/varps", "/varp"):
                self._json(200, {"varpId": 173, "value": 1})
            elif p in ("/varbits", "/varbit"):
                v_val = 0 if "id=2312" in self.path else 1
                self._json(200, {"varbitId": 2312 if v_val == 0 else 2309, "value": v_val})
            elif p == "/inventory":
                self._json(
                    200,
                    {
                        "items": [
                            {"id": 315, "name": "Shrimps", "quantity": 8},
                        ]
                    },
                )
            elif p == "/bank":
                self._json(200, {"open": False, "items": []})
            elif p == "/dialogue":
                self._json(200, {"inDialogue": False, "hasContinue": False, "hasOptions": False})
            elif p == "/objects":
                self._json(200, {"objects": []})
            elif p == "/skills":
                self._json(200, {"name": "Hitpoints", "level": 10, "boostedLevel": 10})
            else:
                self._json(200, {"ok": True})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            if length > 0:
                _ = self.rfile.read(length)
            self._json(200, {"success": True, "ok": True, "clicked": True})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_t52_power_outage_orphaned_lease_reconciliation_preserves_live_slot_and_unblocks_attach_bot(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """T52: Simulates exact Windows power-outage scenario:
    1. Upgrades a pre-PR-16 `active_leases` table lacking `owner_pid`/`heartbeat_at` cleanly.
    2. `acc_01` is actively running in-process (`owner_pid == os.getpid()`), while `acc_02` was left stuck
       in `RUNNING` with a dead OS PID (`99999999`) and an in-flight `DISPATCHING` command.
    3. Running `attach-bot --account-id acc_02` immediately recovers `acc_02` (`RUNNING -> RECOVERY_REQUIRED ->
       RECONCILING -> READY -> RUNNING -> RESTING`) WITHOUT `StaleContextError` and WITHOUT disturbing `acc_01`!
    """
    data_dir = tmp_path / ".osrs_ee_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "state.db"

    # 1. Seed legacy pre-PR-16 active_leases table WITHOUT owner_pid / heartbeat_at columns
    raw_conn = sqlite3.connect(str(db_path))
    raw_conn.executescript(
        """
        CREATE TABLE active_leases (
            account_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL UNIQUE,
            lease_id TEXT NOT NULL UNIQUE,
            slot_id INTEGER NOT NULL UNIQUE,
            proxy_ref TEXT NOT NULL UNIQUE,
            session_epoch INTEGER NOT NULL,
            client_instance_id TEXT,
            task_id TEXT NOT NULL,
            has_unknown_command INTEGER NOT NULL DEFAULT 0,
            acquired_at TEXT NOT NULL
        );
        """
    )
    raw_conn.close()

    # Opening StateStore must auto-migrate owner_pid and heartbeat_at columns idempotently
    store = StateStore(db_path, snapshot_path=data_dir / "state_snapshot.json")
    cols = {r["name"] for r in store._conn.execute("PRAGMA table_info(active_leases)").fetchall()}
    assert {"owner_pid", "heartbeat_at"}.issubset(cols)

    store.register_account(
        account_id="acc_01",
        auth_mode="LEGACY_CREDENTIALS",
        credential_ref="vault://accounts/acc_01/auth",
        proxy_ref="vault://proxies/eg_slot0",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=101,
        initial_status=AccountStatus.READY,
    )
    store.register_account(
        account_id="acc_02",
        auth_mode="LEGACY_CREDENTIALS",
        credential_ref="vault://accounts/acc_02/auth",
        proxy_ref="vault://proxies/eg_slot1",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=202,
        initial_status=AccountStatus.READY,
    )

    # Slot 0 (acc_01): LIVE active process (current PID)
    live_lease = store.acquire_session_lease(
        account_id="acc_01",
        slot_id=0,
        task_id="stage3_f2p_skilling",
        bypass_circadian=True,
        owner_pid=os.getpid(),
    )
    store.bind_client_instance(
        account_id="acc_01",
        session_id=live_lease["session_id"],
        lease_id=live_lease["lease_id"],
        session_epoch=live_lease["session_epoch"],
        client_instance_id="jvm_live_01",
    )

    # Slot 1 (acc_02): CRASHED process from power outage (non-existent PID 999_999_991 + DISPATCHING cmd)
    dead_lease = store.acquire_session_lease(
        account_id="acc_02",
        slot_id=1,
        task_id="stage2_stronghold_10k",
        bypass_circadian=True,
        owner_pid=999_999_991,
    )
    store.bind_client_instance(
        account_id="acc_02",
        session_id=dead_lease["session_id"],
        lease_id=dead_lease["lease_id"],
        session_epoch=dead_lease["session_epoch"],
        client_instance_id="jvm_dead_02",
    )
    store.prepare_command_intent(
        {
            "schema_version": "1.0",
            "command_id": "cmd_powercut_01",
            "session_id": dead_lease["session_id"],
            "account_id": "acc_02",
            "client_instance_id": "jvm_dead_02",
            "lease_id": dead_lease["lease_id"],
            "session_epoch": dead_lease["session_epoch"],
            "base_state_version": 1,
            "command_type": "WALK_TO_WORLD_POINT",
            "idempotency_class": "IDEMPOTENT",
            "payload": {"x": 3106, "y": 3432, "plane": 0},
            "precondition": "logged_in",
            "expected_postcondition": "moving",
            "timeout_ms": 3000,
        }
    )
    store.mark_command_dispatching(
        command_id="cmd_powercut_01",
        account_id="acc_02",
        session_id=dead_lease["session_id"],
        lease_id=dead_lease["lease_id"],
        session_epoch=dead_lease["session_epoch"],
        client_instance_id="jvm_dead_02",
    )
    assert store.get_account("acc_02")["status"] == AccountStatus.RUNNING.value
    store.close()

    # 2. Operator reboots and immediately runs `attach-bot --account-id acc_02` on port 8082!
    tok_file = tmp_path / ".agent-token"
    tok_file.write_text("T" * 32, encoding="utf-8")
    srv = _start_mock_microbot_server()
    port = int(srv.server_address[1])
    try:
        rc = main(
            [
                "--data-dir",
                str(data_dir),
                "attach-bot",
                "--account-id",
                "acc_02",
                "--recipe-id",
                "stage2_stronghold_10k",
                "--port",
                str(port),
                "--slot-id",
                "1",
                "--token-file",
                str(tok_file),
                "--max-ticks",
                "2",
                "--tick-interval-sec",
                "0",
            ]
        )
        assert rc == 0
        captured = capsys.readouterr()
        out_json = json.loads(captured.out)
        assert out_json["ok"] is True
        assert out_json["ticks_executed"] == 2
    finally:
        srv.server_close()

    # 3. Verify acc_01's live lease was 100% preserved while acc_02 recovered, ran, and closed to RESTING!
    verify_store = StateStore(db_path)
    try:
        assert verify_store.get_account("acc_01")["status"] == AccountStatus.RUNNING.value
        verify_store.verify_fencing_token(
            account_id="acc_01",
            session_id=live_lease["session_id"],
            lease_id=live_lease["lease_id"],
            session_epoch=live_lease["session_epoch"],
            client_instance_id="jvm_live_01",
        )
        acc2_after = verify_store.get_account("acc_02")
        assert acc2_after["status"] == AccountStatus.RESTING.value
        assert int(acc2_after["total_level"] or 0) == 35  # Synced during live post-crash RECONCILE!
        cmd_row = verify_store._conn.execute(
            "SELECT journal_state, diagnostic_note FROM command_journal WHERE command_id = 'cmd_powercut_01'"
        ).fetchone()
        assert cmd_row["journal_state"] == CommandJournalState.FAILED_VERIFIED.value
        assert "reconciled" in str(cmd_row["diagnostic_note"]).lower()
    finally:
        verify_store.close()


def test_t53_cli_reconcile_subcommand_and_cooldown_preservation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """T53: `python -m osrs_ee reconcile --acknowledge` recovers orphaned accounts and never wipes active cooldowns."""
    data_dir = tmp_path / ".osrs_ee_data"
    assert main(["--data-dir", str(data_dir), "init"]) == 0
    _ = capsys.readouterr()

    store = StateStore(data_dir / "state.db", snapshot_path=data_dir / "state_snapshot.json")
    store.register_account(
        account_id="acc_02",
        auth_mode="LEGACY_CREDENTIALS",
        credential_ref="vault://accounts/acc_02/auth",
        proxy_ref="vault://proxies/eg_02",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=202,
        initial_status=AccountStatus.READY,
    )
    _ = store.acquire_session_lease(
        account_id="acc_02",
        slot_id=0,
        task_id="stage2_stronghold_10k",
        bypass_circadian=True,
        owner_pid=999_999_992,
    )
    store.close()

    # Step 1: `reconcile` without `--acknowledge` sweeps dead lease -> RECOVERY_REQUIRED
    assert main(["--data-dir", str(data_dir), "reconcile"]) == 0
    r1 = json.loads(capsys.readouterr().out)
    assert r1["reclaimed_orphaned_leases"] == ["acc_02"]
    assert r1["awaiting_reconciliation"] == ["acc_02"]

    # Step 2: `reconcile --acknowledge` completes legal RECOVERY_REQUIRED -> RECONCILING -> READY
    assert main(["--data-dir", str(data_dir), "reconcile", "--acknowledge"]) == 0
    r2 = json.loads(capsys.readouterr().out)
    assert r2["transitioned_to_ready"] == ["acc_02"]

    store2 = StateStore(data_dir / "state.db")
    try:
        assert store2.get_account("acc_02")["status"] == AccountStatus.READY.value

        # Verify mandatory future cooldown is NEVER bypassed by acknowledge_crash_recovery!
        future_cd = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        store2._conn.execute(
            "UPDATE accounts SET status = ?, cooldown_until = ? WHERE account_id = 'acc_02'",
            (AccountStatus.RECOVERY_REQUIRED.value, future_cd),
        )
        store2._conn.commit()
        with pytest.raises(CooldownActiveError):
            store2.acknowledge_crash_recovery("acc_02")
    finally:
        store2.close()


def test_t54_windows_cp1252_console_unicode_encode_guard_across_cli_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T54: Exact reproduction of Windows PowerShell `cp1252` (`charmap`) crash on `📊 ملخص الأسطول`."""
    data_dir = tmp_path / ".osrs_ee_data"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "2222222222:" + ("B" * 35))
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1124247595")
    monkeypatch.setenv("ALLOWED_CHAT_IDS", "1124247595")
    monkeypatch.setenv("TELEGRAM_TOKEN_ROTATED", "false")

    assert main(["--data-dir", str(data_dir), "init"]) == 0
    assert (
        main(
            [
                "--data-dir",
                str(data_dir),
                "grant-operator",
                "--user-id",
                "1124247595",
                "--chat-id",
                "1124247595",
                "--role",
                "ADMIN",
            ]
        )
        == 0
    )

    # 1. Bind strict Windows `cp1252` TextIOWrapper to sys.stdout & sys.stderr and invoke `telegram-check`!
    raw_out = io.BytesIO()
    raw_err = io.BytesIO()
    cp1252_stdout = io.TextIOWrapper(raw_out, encoding="cp1252", errors="strict")
    cp1252_stderr = io.TextIOWrapper(raw_err, encoding="cp1252", errors="strict")
    monkeypatch.setattr("sys.stdout", cp1252_stdout)
    monkeypatch.setattr("sys.stderr", cp1252_stderr)

    rc = main(["--data-dir", str(data_dir), "telegram-check", "--offline"])
    cp1252_stdout.flush()
    assert rc == 0
    decoded_out = raw_out.getvalue().decode("utf-8")
    report = json.loads(decoded_out)
    assert report["ok"] is True
    assert "📊 ملخص الأسطول" in report["status_command_self_test"][0]["status_response"]

    # 2. Even on a custom non-reconfigurable strict `cp1252` stream, `_safe_print` must NEVER raise!
    class _LockedCp1252Stream:
        encoding = "cp1252"

        def __init__(self) -> None:
            self.buffer = io.BytesIO()

        def write(self, s: str) -> int:
            # Raises UnicodeEncodeError('charmap', ...) on emojis/Arabic just like legacy Windows console!
            self.buffer.write(s.encode("cp1252", errors="strict"))
            return len(s)

        def flush(self) -> None:
            return

    locked = _LockedCp1252Stream()
    _safe_print("📊 فحص ويندوز CP1252 بدون كراش ✅", file=locked, flush=True)
    assert "📊 فحص ويندوز CP1252 بدون كراش ✅" in locked.buffer.getvalue().decode("utf-8")


def test_t55_telegram_rbac_account_ingest_zero_secret_leak_and_message_auto_scrub(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T55: Verifies `/add_account`, `/accounts`, `/reconcile`, RBAC enforcement, zero secret leaks,
    and automatic `POST /deleteMessage` scrubbing of plaintext credential messages in `poll_updates_once`.
    """
    vault = LocalSecretVault(tmp_path / "vault.json")
    store = StateStore(
        tmp_path / "state.db",
        snapshot_path=tmp_path / "state_snapshot.json",
        redactor=vault.redactor,
    )
    outbox = BoundedOutbox(tmp_path / "outbox.db", redactor=vault.redactor)
    gw = TelegramGateway(
        store=store,
        outbox=outbox,
        vault=vault,
        allowed_chat_ids={500},
    )

    try:
        store.grant_operator_role(user_id=10, chat_id=500, role=OperatorRole.VIEWER)
        store.grant_operator_role(user_id=20, chat_id=500, role=OperatorRole.OPERATOR)
        store.grant_operator_role(user_id=30, chat_id=500, role=OperatorRole.ADMIN)

        # 1. Unauthorized user silently dropped (None) and nothing ingested
        assert (
            gw.handle_incoming(
                user_id=999,
                chat_id=500,
                text_or_callback="/add_account intruder@evil.com|EvilPass!1|DIRECT|EG",
            )
            is None
        )

        # 2. VIEWER and OPERATOR denied `/add_account` without leaking credentials
        viewer_deny = gw.handle_incoming(
            user_id=10,
            chat_id=500,
            text_or_callback="/add_account v@example.com|ViewerSecret#9|DIRECT|EG",
        )
        op_deny = gw.handle_incoming(
            user_id=20,
            chat_id=500,
            text_or_callback="/add_account o@example.com|OpSecret#9|DIRECT|EG",
        )
        assert viewer_deny is not None and "ADMIN" in viewer_deny and "ViewerSecret#9" not in viewer_deny
        assert op_deny is not None and "ADMIN" in op_deny and "OpSecret#9" not in op_deny

        # 3. Malformed line from ADMIN never echoes raw input or password
        bad_reply = (
            gw.handle_incoming(
                user_id=30,
                chat_id=500,
                text_or_callback="/add_account broken_line_containing_UltraTopSecret999",
            )
            or ""
        )
        assert "❌" in bad_reply
        assert "UltraTopSecret999" not in bad_reply

        # 4. Valid multi-line batch ingest from ADMIN (Legacy + Jagex Session)
        batch_cmd = (
            "/add_account@orangeconnebot\n"
            "fleet_one@example.com|MyVaultPass#2026|socks5://pxu:pxpass99@198.51.100.10:1080|Egypt\n"
            "jx_live_sess_tok_888|char_de_02|DIRECT|Germany"
        )
        ok_reply = gw.handle_incoming(user_id=30, chat_id=500, text_or_callback=batch_cmd) or ""
        assert "✅" in ok_reply
        assert "acc_01" in ok_reply and "acc_02" in ok_reply
        for secret_tok in (
            "fleet_one@example.com",
            "MyVaultPass#2026",
            "pxpass99",
            "jx_live_sess_tok_888",
        ):
            assert secret_tok not in ok_reply

        # Idempotent re-send skips duplicates cleanly
        dup_reply = gw.handle_incoming(user_id=30, chat_id=500, text_or_callback=batch_cmd) or ""
        assert "تخطي 2" in dup_reply

        # `/accounts` lists sanitized roster to VIEWER with zero secrets
        roster = gw.handle_incoming(user_id=10, chat_id=500, text_or_callback="/accounts") or ""
        assert "acc_01" in roster and "acc_02" in roster
        assert "fleet_one@example.com" not in roster
        assert "MyVaultPass#2026" not in roster

        # 5. Live `poll_updates_once` auto-deletes plaintext `/add_account` message via `/deleteMessage`!
        deleted_msg_ids: list[int] = []
        sent_replies: list[str] = []

        def _fake_urlopen(req: urllib.request.Request, timeout: float = 5.0) -> Any:
            url = req.full_url
            if "/getUpdates" in url:
                payload = {
                    "ok": True,
                    "result": [
                        {
                            "update_id": 700,
                            "message": {
                                "message_id": 4242,
                                "from": {"id": 30},
                                "chat": {"id": 500},
                                "text": "/add_account fleet_three@example.com|ScrubbedPass!77|DIRECT|EG",
                            },
                        }
                    ],
                }
            elif "/deleteMessage" in url:
                body = json.loads((req.data or b"{}").decode("utf-8"))
                deleted_msg_ids.append(int(body["message_id"]))
                payload = {"ok": True, "result": True}
            elif "/sendMessage" in url:
                body = json.loads((req.data or b"{}").decode("utf-8"))
                sent_replies.append(str(body["text"]))
                payload = {"ok": True, "result": {"message_id": 4243}}
            else:
                payload = {"ok": True}

            class _Resp:
                status = 200

                def read(self) -> bytes:
                    return json.dumps(payload).encode("utf-8")

                def __enter__(self) -> _Resp:
                    return self

                def __exit__(self, *args: Any) -> None:
                    return

                def close(self) -> None:
                    return

            return _Resp()

        monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)
        next_off, proc_count = gw.poll_updates_once(
            "3333333333:" + ("C" * 35),
            offset=0,
            timeout_sec=1,
            token_rotation_confirmed=True,
        )
        assert next_off == 701
        assert proc_count == 1
        assert deleted_msg_ids == [4242], "Must call POST /deleteMessage on sensitive /add_account message!"
        assert len(sent_replies) == 1
        assert "acc_03" in sent_replies[0]
        assert "ScrubbedPass!77" not in sent_replies[0]
        assert "تم حذف رسالة البيانات الأصلية" in sent_replies[0]
    finally:
        outbox.close()
        store.close()
