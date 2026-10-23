"""PR-03 Acceptance Tests: T03 (RBAC/Approvals), T04 (Epoch Fencing), T07 (Journal UNKNOWN),
T08 (Crash Recovery & Snapshot), T09 (No Blind READY, Cooldown & Dedupe), T11 (10QP+100TTL+20h), T31 (Circadian).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from osrs_ee.state import (
    AccountStatus,
    ApprovalRejectedError,
    AuthorizationDeniedError,
    CircadianSleepActiveError,
    CooldownActiveError,
    InvalidStateTransitionError,
    OperatorRole,
    ProxyCollisionError,
    ReasonCode,
    StaleContextError,
    StateStore,
    UnknownEffectPendingError,
    compute_progression_stage,
)


def _find_awake_and_asleep_times(store: StateStore, account_id: str) -> tuple[datetime, datetime]:
    acc = store.get_account(account_id)
    from osrs_ee.state.fsm import is_in_circadian_sleep_window

    base = datetime(2026, 10, 23, 0, 0, tzinfo=timezone.utc)
    awake_dt = None
    asleep_dt = None
    for hour in range(24):
        cand = base + timedelta(hours=hour)
        if is_in_circadian_sleep_window(cand, acc["timezone"], acc["persona_seed"]):
            asleep_dt = cand
        else:
            awake_dt = cand
    assert awake_dt is not None and asleep_dt is not None
    return awake_dt, asleep_dt


def test_t04_and_t19_exclusive_lease_epoch_fencing_and_proxy_collision(tmp_path: Path) -> None:
    """T04/T19: Stale session_epoch/lease_id is rejected after slot rotation; shared active proxy blocked."""
    store = StateStore(tmp_path / "state.db", snapshot_path=tmp_path / "snapshot.json")
    store.register_account(
        account_id="acc_01",
        auth_mode="JAGEX_SESSION",
        credential_ref="vault://accounts/acc_01/jagex",
        proxy_ref="vault://proxies/eg_01",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=111,
        initial_status=AccountStatus.READY,
    )
    store.register_account(
        account_id="acc_02",
        auth_mode="LEGACY_CREDENTIALS",
        credential_ref="vault://accounts/acc_02/legacy",
        proxy_ref="vault://proxies/eg_01",  # intentionally same proxy_ref to test collision guard
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=222,
        initial_status=AccountStatus.READY,
    )

    awake_1, _ = _find_awake_and_asleep_times(store, "acc_01")
    lease1 = store.acquire_session_lease(
        account_id="acc_01", slot_id=0, task_id="STAGE0_STRONGHOLD", now_utc=awake_1
    )
    store.bind_client_instance(
        account_id="acc_01",
        session_id=lease1["session_id"],
        lease_id=lease1["lease_id"],
        session_epoch=lease1["session_epoch"],
        client_instance_id="jvm_first",
        now_utc=awake_1,
    )

    # Second account trying to use same proxy while acc_01 is active MUST fail
    awake_2, _ = _find_awake_and_asleep_times(store, "acc_02")
    with pytest.raises(ProxyCollisionError):
        store.acquire_session_lease(
            account_id="acc_02", slot_id=1, task_id="STAGE0_STRONGHOLD", now_utc=awake_2
        )

    # Rotate slot: close acc_01 into BLOCKED, releasing slot 0 & proxy
    store.close_session(
        account_id="acc_01",
        session_id=lease1["session_id"],
        lease_id=lease1["lease_id"],
        session_epoch=lease1["session_epoch"],
        target_status=AccountStatus.BLOCKED,
        reason_code=ReasonCode.HUMAN_AUTH_REQUIRED,
        now_utc=awake_1 + timedelta(minutes=10),
    )

    # Late command/callback carrying acc_01's old lease/epoch MUST be rejected with STALE_CONTEXT
    with pytest.raises(StaleContextError, match="STALE_CONTEXT"):
        store.verify_fencing_token(
            account_id="acc_01",
            session_id=lease1["session_id"],
            lease_id=lease1["lease_id"],
            session_epoch=lease1["session_epoch"],
            client_instance_id="jvm_first",
        )
    store.close()


def test_t07_and_t08_command_journal_unknown_blocks_blind_replay_and_recovers_on_boot(tmp_path: Path) -> None:
    """T07/T08: Interrupted DISPATCHING command becomes UNKNOWN, blocks replay, and reconciles to RECOVERY_REQUIRED."""
    db_file = tmp_path / "state.db"
    snap_file = tmp_path / "state_snapshot.json"
    store = StateStore(db_file, snapshot_path=snap_file)
    store.register_account(
        account_id="acc_01",
        auth_mode="JAGEX_SESSION",
        credential_ref="vault://accounts/acc_01/jagex",
        proxy_ref="vault://proxies/eg_01",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=42,
        initial_status=AccountStatus.READY,
    )
    awake_dt, _ = _find_awake_and_asleep_times(store, "acc_01")
    lease = store.acquire_session_lease(
        account_id="acc_01", slot_id=0, task_id="BANK_PREP", now_utc=awake_dt
    )
    store.bind_client_instance(
        account_id="acc_01",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
        client_instance_id="jvm_01",
        now_utc=awake_dt,
    )

    intent = {
        "schema_version": "1.0",
        "command_id": "cmd_withdraw_01",
        "session_id": lease["session_id"],
        "account_id": "acc_01",
        "client_instance_id": "jvm_01",
        "lease_id": lease["lease_id"],
        "session_epoch": lease["session_epoch"],
        "base_state_version": 1,
        "command_type": "BANK_WITHDRAW_ITEM",
        "idempotency_class": "NON_IDEMPOTENT",
        "payload": {"item_id": 1359, "quantity": 1, "noted": False},
        "precondition": "bank.is_open == true",
        "expected_postcondition": "inventory has 1359",
        "timeout_ms": 5000,
    }
    store.prepare_command_intent(intent, now_utc=awake_dt)
    store.mark_command_dispatching(
        command_id="cmd_withdraw_01",
        account_id="acc_01",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
        client_instance_id="jvm_01",
        now_utc=awake_dt,
    )
    # Transport timeout -> UNKNOWN effect
    store.finalize_command_result(
        {
            "schema_version": "1.0",
            "command_id": "cmd_withdraw_01",
            "event_id": "evt_timeout_01",
            "session_id": lease["session_id"],
            "session_epoch": lease["session_epoch"],
            "transport_status": "TRANSPORT_TIMEOUT",
            "effect_status": "UNKNOWN",
            "completed_at": "2026-10-23T12:00:05Z",
            "post_observation_id": None,
            "error_code": "HTTP_TIMEOUT",
            "diagnostic_note": "Timed out awaiting HTTP response",
        }
    )

    # Attempting to prepare a second command blindly before reconciling UNKNOWN must fail!
    intent2 = dict(intent, command_id="cmd_withdraw_02")
    with pytest.raises(UnknownEffectPendingError):
        store.prepare_command_intent(intent2, now_utc=awake_dt)

    # Simulate power outage / abrupt process crash and reopen DB on boot
    store.close()
    rebooted = StateStore(db_file, snapshot_path=snap_file)
    recovered = rebooted.reconcile_on_boot(now_utc=awake_dt + timedelta(minutes=5))
    assert recovered == ["acc_01"]

    acc_after = rebooted.get_account("acc_01")
    # NEVER blindly READY after crash!
    assert acc_after["status"] == AccountStatus.RECOVERY_REQUIRED.value
    assert acc_after["reason_code"] == ReasonCode.UNKNOWN_COMMAND_EFFECT.value

    # Verify exported JSON snapshot is valid and matches SQLite state
    snap_data = json.loads(snap_file.read_text(encoding="utf-8"))
    assert snap_data["accounts"]["acc_01"]["status"] == "RECOVERY_REQUIRED"
    rebooted.close()


def test_t03_and_t09_rbac_single_use_approval_cooldown_immutability_and_event_dedupe(tmp_path: Path) -> None:
    """T03/T09: Default-Deny RBAC, one-time bound approval -> RECONCILING, cooldown preserved, event_id deduped."""
    store = StateStore(tmp_path / "state.db")
    store.register_account(
        account_id="acc_04",
        auth_mode="JAGEX_SESSION",
        credential_ref="vault://accounts/acc_04/jagex",
        proxy_ref="vault://proxies/eg_04",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=777,
        initial_status=AccountStatus.READY,
    )
    awake_dt, _ = _find_awake_and_asleep_times(store, "acc_04")
    lease = store.acquire_session_lease(
        account_id="acc_04", slot_id=0, task_id="STAGE0_STRONGHOLD", now_utc=awake_dt
    )
    store.bind_client_instance(
        account_id="acc_04",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
        client_instance_id="jvm_04",
        now_utc=awake_dt,
    )

    # Event deduplication check (delivering same event_id twice counts GP only once!)
    obs = {
        "schema_version": "1.0",
        "observation_id": "obs_1",
        "observed_at": "2026-10-23T12:10:00Z",
        "monotonic_ms": 1000,
        "account_id": "acc_04",
        "session_id": lease["session_id"],
        "client_instance_id": "jvm_04",
        "session_epoch": lease["session_epoch"],
        "login_state": "LOGGED_IN",
        "game_tick": 50,
        "world": 308,
        "position": {"x": 3081, "y": 3421, "plane": 0, "quality": "KNOWN"},
        "tutorial_varp_281": {"value": 1000, "quality": "KNOWN"},
        "quest_points": {"value": 10, "quality": "KNOWN"},
        "total_level": {"value": 105, "quality": "KNOWN"},
        "playtime_minutes": {"value": 500, "quality": "KNOWN"},
        "dialogue_open": False,
        "bank": {"is_open": False, "pin_prompt_open": False, "items_quality": "UNKNOWN", "items": None},
        "inventory": {"free_slots": 28, "quality": "KNOWN", "items": []},
    }
    assert store.apply_observation_snapshot(event_id="evt_gp_1", observation=obs, realized_gp_delta=10000) is True
    assert store.apply_observation_snapshot(event_id="evt_gp_1", observation=obs, realized_gp_delta=10000) is False
    assert store.get_account("acc_04")["realized_gp"] == 10000

    # Close session as BLOCKED with a 4-hour mandatory cooldown
    cooldown_end = awake_dt + timedelta(hours=4, minutes=15)
    store.close_session(
        account_id="acc_04",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
        target_status=AccountStatus.BLOCKED,
        reason_code=ReasonCode.HUMAN_AUTH_REQUIRED,
        cooldown_until=cooldown_end,
        now_utc=awake_dt + timedelta(minutes=15),
    )

    # Illegal direct transition BLOCKED -> READY is rejected by FSM
    from osrs_ee.state.fsm import assert_valid_account_transition
    with pytest.raises(InvalidStateTransitionError):
        assert_valid_account_transition(AccountStatus.BLOCKED, AccountStatus.READY)

    # RBAC Default-Deny: unauthorized user and VIEWER role cannot approve
    store.grant_operator_role(user_id=10, chat_id=999, role=OperatorRole.VIEWER)
    store.grant_operator_role(user_id=20, chat_id=999, role=OperatorRole.OPERATOR)

    appr_id = store.create_approval_request(
        incident_id="inc_04",
        account_id="acc_04",
        plan_hash="sha256:plan_reconcile_2fa",
        expires_at=awake_dt + timedelta(minutes=45),
    )

    with pytest.raises(AuthorizationDeniedError):
        store.consume_approval(
            approval_id=appr_id,
            user_id=99999,  # unknown user
            chat_id=999,
            plan_hash="sha256:plan_reconcile_2fa",
            now_utc=awake_dt + timedelta(minutes=20),
        )
    with pytest.raises(AuthorizationDeniedError):
        store.consume_approval(
            approval_id=appr_id,
            user_id=10,  # VIEWER only
            chat_id=999,
            plan_hash="sha256:plan_reconcile_2fa",
            now_utc=awake_dt + timedelta(minutes=20),
        )

    # Authorized OPERATOR consumes approval -> moves to RECONCILING (NOT READY!) and keeps cooldown_until!
    updated = store.consume_approval(
        approval_id=appr_id,
        user_id=20,
        chat_id=999,
        plan_hash="sha256:plan_reconcile_2fa",
        now_utc=awake_dt + timedelta(minutes=20),
    )
    assert updated["status"] == AccountStatus.RECONCILING.value
    assert updated["cooldown_until"] is not None

    # Replay of same approval_id is rejected!
    with pytest.raises(ApprovalRejectedError, match="already consumed"):
        store.consume_approval(
            approval_id=appr_id,
            user_id=20,
            chat_id=999,
            plan_hash="sha256:plan_reconcile_2fa",
            now_utc=awake_dt + timedelta(minutes=21),
        )

    # Even in RECONCILING, cannot lease before cooldown_until elapses!
    with pytest.raises(CooldownActiveError):
        store.acquire_session_lease(
            account_id="acc_04",
            slot_id=0,
            task_id="RECONCILE_PROBE",
            now_utc=awake_dt + timedelta(minutes=30),
            session_kind="RECONCILE",
        )
    store.close()


def test_t11_and_t31_f2p_trade_unlock_20h_seasoning_and_circadian_sleep_block(tmp_path: Path) -> None:
    """T11/T31: 7 QP or <20h playtime stays locked (STAGE_1B_SEASONING); circadian sleep blocks launch."""
    assert compute_progression_stage(
        tutorial_varp_281=500, quest_points=0, total_level=32, playtime_minutes=10
    ) == ("STAGE_0_TUTORIAL", False)

    # 7 QP + 100 TTL + 25h is NOT enough (must be >= 10 QP!)
    assert compute_progression_stage(
        tutorial_varp_281=1000, quest_points=7, total_level=105, playtime_minutes=1500
    ) == ("STAGE_1A_QP_AND_TOTAL", False)

    # 10 QP + 105 TTL + 3 hours (180 mins) -> STAGE_1B_SEASONING, trade_unlocked=False!
    assert compute_progression_stage(
        tutorial_varp_281=1000, quest_points=10, total_level=105, playtime_minutes=180
    ) == ("STAGE_1B_SEASONING", False)

    # All three satisfied (10 QP + 105 TTL + 1200 mins = 20h) -> unlocked!
    assert compute_progression_stage(
        tutorial_varp_281=1000, quest_points=10, total_level=105, playtime_minutes=1200
    ) == ("STAGE_3_F2P_SKILLING", True)

    store = StateStore(tmp_path / "circ.db")
    store.register_account(
        account_id="acc_09",
        auth_mode="JAGEX_SESSION",
        credential_ref="vault://accounts/acc_09/jagex",
        proxy_ref="vault://proxies/eg_09",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=909,
        initial_status=AccountStatus.READY,
    )
    _, asleep_dt = _find_awake_and_asleep_times(store, "acc_09")
    with pytest.raises(CircadianSleepActiveError):
        store.acquire_session_lease(
            account_id="acc_09", slot_id=0, task_id="F2P_WOODCUT", now_utc=asleep_dt
        )
    store.close()
