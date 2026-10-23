"""PR-04 Acceptance Tests: T06 (Gate & Telemetry), T07 (Adapter Drop -> UNKNOWN), T10 (Varp 281 vs Varbit),
T12 (Bank Turnaround Bugs Fixed), T13 (GE Disabled in MVP), T16 (Pause/Drain/Abort),
T17 (Stationary Ticking vs Freeze + Circuit Breaker), T21 (Replan without Self-Switch), T23 (Death Quarantine).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from osrs_ee.contracts.config import HardSafetyCeilings, RuntimeConfigSnapshot
from osrs_ee.runtime import (
    AdapterSecurityError,
    FakeGameAdapter,
    GameAdapterGate,
    PolicyDeniedError,
    SingleSlotSupervisor,
    WatchdogVerdict,
)
from osrs_ee.state import AccountStatus, OperatorRole, ReasonCode, StaleContextError, StateStore


def _awake_time(store: StateStore, account_id: str) -> datetime:
    from osrs_ee.state.fsm import is_in_circadian_sleep_window

    acc = store.get_account(account_id)
    base = datetime(2026, 10, 23, 0, 0, tzinfo=timezone.utc)
    for h in range(24):
        cand = base + timedelta(hours=h)
        if not is_in_circadian_sleep_window(cand, acc["timezone"], acc["persona_seed"]):
            return cand
    raise AssertionError("No awake hour found")


def _sample_recipe(*, clear_equipment: bool = True) -> dict:
    return {
        "schema_version": "1.0",
        "recipe_id": "f2p_draynor_willow",
        "display_name": "Draynor Willow Woodcutter",
        "plugin_id": "net.runelite.client.plugins.microbot.woodcutting.AutoWoodcuttingPlugin",
        "verification_stage": "HERMETIC_TESTED",
        "stage_tier": "STAGE_3_F2P_SKILLING",
        "min_skills": {"woodcutting": 30},
        "required_varps": {"281": 1000},
        "tool_candidates": ["Rune axe", "Mithril axe", "Bronze axe"],
        "sellable_yields": [],
        "clear_equipment": clear_equipment,
    }


def test_t06_and_t10_gate_enforces_telemetry_off_fencing_and_varp_281(tmp_path: Path) -> None:
    """T06/T10: Reject missing --disable-telemetry; read Varp 281 (not Varbit 281); closed bank is UNKNOWN."""
    store = StateStore(tmp_path / "state.db")
    store.register_account(
        account_id="acc_01",
        auth_mode="JAGEX_SESSION",
        credential_ref="vault://accounts/acc_01/jagex",
        proxy_ref="vault://proxies/eg_01",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=101,
        initial_status=AccountStatus.PENDING_DISCOVERY,
    )
    now_dt = _awake_time(store, "acc_01")
    lease = store.acquire_session_lease(
        account_id="acc_01", slot_id=0, task_id="DISCOVERY", now_utc=now_dt, session_kind="DISCOVERY"
    )

    # Missing --disable-telemetry must be rejected immediately by GameAdapterGate!
    unsafe_adapter = FakeGameAdapter(
        account_id="acc_01",
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        jvm_flags=["-Xmx768m"],  # missing telemetry disable flags!
    )
    with pytest.raises(AdapterSecurityError):
        GameAdapterGate(store, unsafe_adapter)

    safe_adapter = FakeGameAdapter(
        account_id="acc_01",
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        tutorial_varp_281=1000,
    )
    # Prove Varbit 281 is 0 while Varp 281 is 1000
    assert safe_adapter.read_varbit(281) == 0

    sup = SingleSlotSupervisor(store=store)
    worker = sup.attach_session(lease=lease, gate=GameAdapterGate(store, safe_adapter))
    obs = worker.run_discovery_probe()

    assert obs["tutorial_varp_281"] == {"value": 1000, "quality": "KNOWN"}
    assert obs["bank"]["is_open"] is False
    assert obs["bank"]["items_quality"] == "UNKNOWN"
    assert obs["bank"]["items"] is None
    assert store.get_account("acc_01")["trade_unlocked"] == 1
    store.close()


def test_t12_and_t21_bank_turnaround_level_selection_silent_withdraw_fail_pin_and_replan(tmp_path: Path) -> None:
    """T12/T21: Selects Mithril axe (lvl 35 < 41 Rune), clears equipment, catches silent withdraw fail,
    halts on Bank PIN, and requests REPLAN without switching tasks when tools are missing.
    """
    store = StateStore(tmp_path / "state.db")
    store.register_account(
        account_id="acc_02",
        auth_mode="JAGEX_SESSION",
        credential_ref="vault://accounts/acc_02/jagex",
        proxy_ref="vault://proxies/eg_02",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=202,
        initial_status=AccountStatus.READY,
    )
    now_dt = _awake_time(store, "acc_02")
    lease = store.acquire_session_lease(
        account_id="acc_02", slot_id=0, task_id="f2p_draynor_willow", now_utc=now_dt
    )

    # Bank has both Rune axe (req 41) and Mithril axe (req 21); player has woodcutting=35
    adapter = FakeGameAdapter(
        account_id="acc_02",
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        skills={"woodcutting": 35},
        bank_items=[
            {"item_id": 1359, "name": "Rune axe", "quantity": 1, "noted": False},
            {"item_id": 1355, "name": "Mithril axe", "quantity": 1, "noted": False},
        ],
        inventory_items=[{"item_id": 1511, "name": "Logs", "quantity": 3, "noted": False}],
        equipment_items=["Bronze sword"],
    )
    sup = SingleSlotSupervisor(store=store)
    worker = sup.attach_session(lease=lease, gate=GameAdapterGate(store, adapter))

    # Case A: Silent withdraw failure MUST NOT return COMPLETED!
    adapter.fail_withdraw_silently = True
    bad_out = worker.execute_bank_turnaround(_sample_recipe(), account_skills=adapter.skills)
    assert bad_out.status == "BLOCKED"
    assert bad_out.reason_code == ReasonCode.INSUFFICIENT_RESOURCES

    # Case B: Normal operation clears equipment, skips Rune axe (req 41), withdraws Mithril axe (req 21)
    adapter.fail_withdraw_silently = False
    good_out = worker.execute_bank_turnaround(_sample_recipe(), account_skills=adapter.skills)
    assert good_out.status == "COMPLETED"
    assert good_out.selected_tool == "Mithril axe"
    assert adapter.equipment_items == []
    assert [it["name"] for it in adapter.inventory_items] == ["Mithril axe"]

    # Case C: Empty bank -> REPLAN_REQUESTED (worker never mutates task_id on its own!)
    adapter.bank_items.clear()
    adapter.inventory_items.clear()
    replan_out = worker.execute_bank_turnaround(_sample_recipe(), account_skills=adapter.skills)
    assert replan_out.status == "REPLAN_REQUESTED"
    assert replan_out.reason_code == ReasonCode.INSUFFICIENT_RESOURCES

    # Case D: Bank PIN prompt active -> halts immediately as BLOCKED (BANK_PIN_REQUIRED)
    adapter.bank_pin_prompt_open = True
    pin_out = worker.execute_bank_turnaround(_sample_recipe(), account_skills=adapter.skills)
    assert pin_out.status == "BLOCKED"
    assert pin_out.reason_code == ReasonCode.BANK_PIN_REQUIRED
    assert store.get_account("acc_02")["status"] == AccountStatus.BLOCKED.value
    store.close()


def test_t07_and_t13_transport_drop_marks_unknown_and_mvp_blocks_ge(tmp_path: Path) -> None:
    """T07/T13: Drop after server withdraw records UNKNOWN & halts; GE selling raises PolicyDeniedError."""
    store = StateStore(tmp_path / "state.db")
    store.register_account(
        account_id="acc_03",
        auth_mode="JAGEX_SESSION",
        credential_ref="vault://accounts/acc_03/jagex",
        proxy_ref="vault://proxies/eg_03",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=303,
        initial_status=AccountStatus.READY,
    )
    now_dt = _awake_time(store, "acc_03")
    lease = store.acquire_session_lease(
        account_id="acc_03", slot_id=0, task_id="f2p_draynor_willow", now_utc=now_dt
    )
    adapter = FakeGameAdapter(
        account_id="acc_03",
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        skills={"woodcutting": 35},
        bank_items=[{"item_id": 1355, "name": "Mithril axe", "quantity": 1, "noted": False}],
    )
    adapter.drop_response_after_effect_once = True

    sup = SingleSlotSupervisor(store=store)
    worker = sup.attach_session(lease=lease, gate=GameAdapterGate(store, adapter))

    # T13: GE liquidation is disabled in MVP
    with pytest.raises(PolicyDeniedError):
        worker.attempt_ge_liquidation()

    # T07: Withdraw succeeds on server, HTTP drops -> worker returns UNKNOWN_HALT without double-withdrawing!
    out = worker.execute_bank_turnaround(_sample_recipe(), account_skills=adapter.skills)
    assert out.status == "UNKNOWN_HALT"
    assert out.reason_code == ReasonCode.UNKNOWN_COMMAND_EFFECT
    assert len(adapter.inventory_items) == 1  # withdrawn exactly once, not replayed!
    store.close()


def test_t16_t17_t23_pause_drain_abort_watchdog_and_death_quarantine(tmp_path: Path) -> None:
    """T16/T17/T23: Pause stops actions, hung drain escalates to abort, stationary ticking isn't killed,
    frozen tick trips breaker, and player death triggers Post-Death Quarantine.
    """
    cfg = RuntimeConfigSnapshot(
        ceilings=HardSafetyCeilings(max_drain_timeout_sec=5, max_abort_timeout_sec=2, max_tick_stall_sec=30)
    )
    store = StateStore(tmp_path / "state.db")
    store.register_account(
        account_id="acc_05",
        auth_mode="JAGEX_SESSION",
        credential_ref="vault://accounts/acc_05/jagex",
        proxy_ref="vault://proxies/eg_05",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=505,
        initial_status=AccountStatus.READY,
    )
    now_dt = _awake_time(store, "acc_05")
    lease1 = store.acquire_session_lease(
        account_id="acc_05", slot_id=0, task_id="f2p_draynor_willow", now_utc=now_dt
    )
    adapter1 = FakeGameAdapter(
        account_id="acc_05",
        session_id=lease1["session_id"],
        session_epoch=lease1["session_epoch"],
    )
    sup = SingleSlotSupervisor(store=store, config=cfg, max_consecutive_restarts=1)
    worker1 = sup.attach_session(lease=lease1, gate=GameAdapterGate(store, adapter1))

    # T16: Pause halts new commands
    sup.pause()
    assert worker1.execute_bank_turnaround(_sample_recipe(), account_skills=adapter1.skills).status == "PAUSED"
    worker1.paused = False

    # T17: Stationary skilling (same X,Y, advancing game_tick) is HEALTHY_STATIONARY_TICKING!
    obs_a = worker1.gate.read_verified_observation(
        account_id="acc_05",
        session_id=lease1["session_id"],
        lease_id=lease1["lease_id"],
        session_epoch=lease1["session_epoch"],
    )
    obs_b = worker1.gate.read_verified_observation(
        account_id="acc_05",
        session_id=lease1["session_id"],
        lease_id=lease1["lease_id"],
        session_epoch=lease1["session_epoch"],
    )
    assert (
        sup.evaluate_watchdog(
            account_id="acc_05", previous_obs=obs_a, current_obs=obs_b, stall_elapsed_sec=120.0
        )
        == WatchdogVerdict.HEALTHY_STATIONARY_TICKING
    )

    # T16: Normal drain suppresses AutoLoginScript, kills JVM, and puts account in RESTING
    assert sup.drain(now_utc=now_dt + timedelta(minutes=30)) == "DRAINED_CLEANLY"
    assert adapter1.auto_login_suppressed is True
    assert adapter1.alive is False
    assert store.get_account("acc_05")["status"] == AccountStatus.RESTING.value

    # Advance past cooldown + circadian sleep to test hung drain -> abort escalation & death quarantine
    later_dt = now_dt + timedelta(days=1)
    assert store.transition_resting_to_ready_if_due("acc_05", later_dt) is True
    lease2 = store.acquire_session_lease(
        account_id="acc_05", slot_id=0, task_id="f2p_draynor_willow", now_utc=later_dt
    )
    adapter2 = FakeGameAdapter(
        account_id="acc_05",
        session_id=lease2["session_id"],
        session_epoch=lease2["session_epoch"],
    )
    adapter2.hang_on_logout_sec = 0.02  # triggers TimeoutError on SAFE_LOGOUT -> escalates to abort!
    sup.attach_session(lease=lease2, gate=GameAdapterGate(store, adapter2))
    assert sup.drain(now_utc=later_dt + timedelta(minutes=5)) == "ABORTED_RECOVERY_REQUIRED"
    assert adapter2.alive is False
    assert store.get_account("acc_05")["status"] == AccountStatus.RECOVERY_REQUIRED.value

    # Reconcile recovery -> READY -> test T23 Post-Death Quarantine!
    rec_lease = store.acquire_session_lease(
        account_id="acc_05",
        slot_id=0,
        task_id="RECONCILE",
        now_utc=later_dt + timedelta(minutes=6),
        session_kind="RECONCILE",
    )
    store.close_session(
        account_id="acc_05",
        session_id=rec_lease["session_id"],
        lease_id=rec_lease["lease_id"],
        session_epoch=rec_lease["session_epoch"],
        target_status=AccountStatus.READY,
        reason_code=ReasonCode.NONE,
        now_utc=later_dt + timedelta(minutes=7),
    )

    lease3 = store.acquire_session_lease(
        account_id="acc_05",
        slot_id=0,
        task_id="f2p_draynor_willow",
        now_utc=later_dt + timedelta(minutes=8),
    )
    adapter3 = FakeGameAdapter(
        account_id="acc_05",
        session_id=lease3["session_id"],
        session_epoch=lease3["session_epoch"],
    )
    adapter3.player_dead = True
    worker3 = sup.attach_session(lease=lease3, gate=GameAdapterGate(store, adapter3))
    death_out = worker3.execute_bank_turnaround(_sample_recipe(), account_skills=adapter3.skills)
    assert death_out.status == "BLOCKED"
    assert death_out.reason_code == ReasonCode.DEATH_QUARANTINE
    assert store.get_account("acc_05")["last_death_task_id"] == "f2p_draynor_willow"

    # Even if operator approves, account is in RECONCILING (not READY), and death task remains tracked!
    store.grant_operator_role(user_id=1, chat_id=1, role=OperatorRole.OPERATOR)
    appr = store.create_approval_request(
        incident_id="inc_death",
        account_id="acc_05",
        plan_hash="sha256:ack_death",
        expires_at=later_dt + timedelta(hours=1),
    )
    store.consume_approval(
        approval_id=appr,
        user_id=1,
        chat_id=1,
        plan_hash="sha256:ack_death",
        now_utc=later_dt + timedelta(minutes=10),
    )
    with pytest.raises(StaleContextError):
        store.acquire_session_lease(
            account_id="acc_05",
            slot_id=0,
            task_id="f2p_draynor_willow",
            now_utc=later_dt + timedelta(minutes=11),
        )
    # Manually check T17 frozen tick circuit breaker on a separate account
    store.register_account(
        account_id="acc_06",
        auth_mode="JAGEX_SESSION",
        credential_ref="vault://accounts/acc_06/jagex",
        proxy_ref="vault://proxies/eg_06",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=606,
        initial_status=AccountStatus.READY,
    )
    t6 = _awake_time(store, "acc_06")
    l6 = store.acquire_session_lease(account_id="acc_06", slot_id=0, task_id="T", now_utc=t6)
    a6 = FakeGameAdapter(account_id="acc_06", session_id=l6["session_id"], session_epoch=l6["session_epoch"])
    a6.freeze_ticks = True
    w6 = sup.attach_session(lease=l6, gate=GameAdapterGate(store, a6))
    o1 = w6.gate.read_verified_observation(
        account_id="acc_06", session_id=l6["session_id"], lease_id=l6["lease_id"], session_epoch=l6["session_epoch"]
    )
    o2 = w6.gate.read_verified_observation(
        account_id="acc_06", session_id=l6["session_id"], lease_id=l6["lease_id"], session_epoch=l6["session_epoch"]
    )
    # First freeze -> FROZEN_TICK_STALLED; second freeze -> CIRCUIT_BREAKER_TRIPPED!
    assert (
        sup.evaluate_watchdog(account_id="acc_06", previous_obs=o1, current_obs=o2, stall_elapsed_sec=65.0)
        == WatchdogVerdict.FROZEN_TICK_STALLED
    )
    assert (
        sup.evaluate_watchdog(account_id="acc_06", previous_obs=o1, current_obs=o2, stall_elapsed_sec=65.0)
        == WatchdogVerdict.CIRCUIT_BREAKER_TRIPPED
    )
    store.close()
