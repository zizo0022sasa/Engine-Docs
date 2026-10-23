"""PR-07 Acceptance Tests: T02/T03 (Telegram RBAC, silent drop, zero passwords in /2fa, single-use approvals)
and T29 (<500MB log math proof, open-incident screenshot protection, bounded outbox preserving P0 alerts).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from osrs_ee.security.redaction import Redactor
from osrs_ee.state import AccountStatus, OperatorRole, ReasonCode, StateStore
from osrs_ee.telemetry import (
    GLOBAL_STORAGE_BUDGET_BYTES,
    BoundedOutbox,
    LogQuotaGuardian,
    TelegramGateway,
    create_redacting_account_logger,
)


def test_t29_log_quota_math_under_500mb_redacting_rotation_and_open_incident_protection(
    tmp_path: Path,
) -> None:
    """T29/AUD-30: Worst-case 15-account quota <= 400MB (<500MB); open incidents never pruned!"""
    worst_case = LogQuotaGuardian.calculate_worst_case_budget_bytes(max_accounts=15)
    assert worst_case <= 400 * 1024 * 1024
    assert worst_case < GLOBAL_STORAGE_BUDGET_BYTES

    redactor = Redactor(literal_secrets={"LoggerCanaryPass!1"})
    logger = create_redacting_account_logger(
        "acc_01", tmp_path / "logs", redactor=redactor, max_bytes=512, backup_count=2
    )
    for i in range(25):
        logger.info("Cycle %d auth check LoggerCanaryPass!1 for user1@example.com", i)

    acc_logs = list((tmp_path / "logs" / "accounts").glob("acc_01.log*"))
    # backup_count=2 => strictly <= 3 files!
    assert 1 <= len(acc_logs) <= 3
    for lf in acc_logs:
        txt = lf.read_text(encoding="utf-8")
        assert "LoggerCanaryPass!1" not in txt
        assert "user1@example.com" not in txt

    guardian = LogQuotaGuardian(tmp_path / "logs", max_triage_captures=2, max_triage_bytes=10_000)
    # Create 4 triage captures: 2 belong to OPEN incident inc_open_99, 2 belong to closed incidents
    for idx, name in enumerate(
        ["inc_open_99_a.png", "inc_closed_01.png", "inc_open_99_b.png", "inc_closed_02.png"]
    ):
        fp = guardian.triage_dir / name
        fp.write_bytes(b"X" * 100)
        os_mtime = 1_700_000_000 + idx
        import os
        os.utime(fp, (os_mtime, os_mtime))

    deleted = guardian.prune_triage_captures(open_incident_ids={"inc_open_99"})
    assert set(deleted) == {"inc_closed_01.png", "inc_closed_02.png"}
    remaining = {p.name for p in guardian.triage_dir.iterdir()}
    assert remaining == {"inc_open_99_a.png", "inc_open_99_b.png"}


def test_t29_bounded_outbox_evicts_routine_preserving_p0_and_handles_429_nonblocking(
    tmp_path: Path,
) -> None:
    """T29: Outbox never blocks on 429/offline and protects P0_INCIDENT alerts when full."""
    outbox = BoundedOutbox(tmp_path / "outbox.db", max_pending_messages=3)
    outbox.enqueue(chat_id=100, text="Routine 1", priority="ROUTINE")
    outbox.enqueue(chat_id=100, text="P0 Critical 1", priority="P0_INCIDENT")
    outbox.enqueue(chat_id=100, text="Routine 2", priority="ROUTINE")
    # 4th message is P0 -> must evict oldest ROUTINE ("Routine 1") and keep both P0 alerts!
    outbox.enqueue(chat_id=100, text="P0 Critical 2", priority="P0_INCIDENT")

    counts = outbox.pending_counts_by_priority()
    assert counts["P0_INCIDENT"] == 2
    assert counts.get("ROUTINE", 0) == 1

    # Simulate Telegram HTTP 429 Retry-After -> returns 0 delivered without raising!
    assert outbox.flush_due(lambda _cid, _txt: (False, 30.0), now_monotonic=10.0) == 0
    # Advance past retry_after=30.0 -> delivers all 3!
    delivered: list[str] = []
    sent = outbox.flush_due(
        lambda _cid, txt: (delivered.append(txt) or True, 0.0),  # type: ignore[func-returns-value]
        now_monotonic=50.0,
    )
    assert sent == 3
    # P0 alerts delivered before ROUTINE!
    assert delivered[:2] == ["P0 Critical 1", "P0 Critical 2"]
    outbox.close()


def test_t02_t03_telegram_gateway_rbac_silent_drop_zero_secrets_in_2fa_and_bound_approval(
    tmp_path: Path,
) -> None:
    """T02/T03: Unauthorized dropped silently; /2fa has zero passwords; bound approval consumed once."""
    store = StateStore(tmp_path / "state.db")
    outbox = BoundedOutbox(tmp_path / "outbox.db")
    gw = TelegramGateway(store=store, outbox=outbox)

    store.register_account(
        account_id="acc_04",
        auth_mode="LEGACY_CREDENTIALS",
        credential_ref="vault://accounts/acc_04/auth",
        proxy_ref="vault://proxies/eg_04",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=404,
        initial_status=AccountStatus.BLOCKED,
    )
    store._conn.execute(
        "UPDATE accounts SET reason_code = ? WHERE account_id = 'acc_04'",
        (ReasonCode.HUMAN_AUTH_REQUIRED.value,),
    )
    store._conn.commit()

    # Unauthorized user is silently dropped (returns None) and logged in security audit!
    assert gw.handle_incoming(user_id=999, chat_id=888, text_or_callback="/status") is None
    assert len(gw.security_audit_events) == 1

    store.grant_operator_role(user_id=10, chat_id=500, role=OperatorRole.VIEWER)
    store.grant_operator_role(user_id=20, chat_id=500, role=OperatorRole.OPERATOR)

    # VIEWER can read /status, but CANNOT run /2fa or /abort_all
    assert "BLOCKED=1" in (gw.handle_incoming(user_id=10, chat_id=500, text_or_callback="/status") or "")
    assert "يتطلب دور OPERATOR" in (gw.handle_incoming(user_id=10, chat_id=500, text_or_callback="/2fa") or "")
    assert "يتطلب دور ADMIN" in (gw.handle_incoming(user_id=20, chat_id=500, text_or_callback="/abort_all") or "")

    # OPERATOR /2fa card contains alias & vault:// ref ONLY — zero passwords or emails!
    card = gw.handle_incoming(user_id=20, chat_id=500, text_or_callback="/2fa") or ""
    assert "[acc_04]" in card
    assert "vault://accounts/acc_04/auth" in card
    assert "كلمة المرور" not in card
    assert "@" not in card

    now_dt = datetime(2026, 10, 23, 15, 0, tzinfo=timezone.utc)
    appr_id = store.create_approval_request(
        incident_id="inc_2fa_04",
        account_id="acc_04",
        plan_hash="sha256:2fa_done",
        expires_at=now_dt + timedelta(minutes=30),
    )
    ok_msg = gw.handle_incoming(
        user_id=20,
        chat_id=500,
        text_or_callback=f"approve:{appr_id}:sha256:2fa_done",
        now_utc=now_dt + timedelta(minutes=2),
    )
    assert ok_msg and "RECONCILING" in ok_msg
    assert store.get_account("acc_04")["status"] == AccountStatus.RECONCILING.value

    # Pressing button a second time MUST be rejected!
    replay_msg = gw.handle_incoming(
        user_id=20,
        chat_id=500,
        text_or_callback=f"approve:{appr_id}:sha256:2fa_done",
        now_utc=now_dt + timedelta(minutes=3),
    )
    assert replay_msg and "⛔" in replay_msg

    # Live bot sender refuses to construct unless DEC-PEND-03 token rotation is confirmed!
    with pytest.raises(PermissionError, match="DEC-PEND-03"):
        TelegramGateway.build_live_bot_sender("dummy", token_rotation_confirmed=False)

    outbox.close()
    store.close()
