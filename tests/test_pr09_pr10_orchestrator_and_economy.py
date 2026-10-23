"""PR-09 & PR-10 Acceptance Tests:
- T18 / T30: Measured RSS capacity on 16GB host (<=6 slots, never 12-15), low-RAM refusal, >=90s launch stagger,
  and unsynchronized microbreaks.
- T15: 7-stage ReleaseStagingGate blocking untested/unapproved builds and auto-preserving active version on failed Canary.
- T24: Default-Deny Sell Policy + Active Recipe Input Lock + 2% OSRS GE Tax + Realized vs Unrealized PnL + 4h Buy-Limit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from osrs_ee.economy import (
    DefaultDenySellPolicy,
    GEOrderLedger,
    SellPolicyViolationError,
    calculate_osrs_ge_tax_per_unit,
)
from osrs_ee.orchestrator import (
    MicrobreakCoordinator,
    ReleaseStage,
    ReleaseStagingGate,
    RssCapacityCalculator,
    StaggeredLaunchPlanner,
)
from osrs_ee.orchestrator.release_gate import ReleaseGateError
from osrs_ee.tasks import CuratedRecipeRegistry


def test_t18_t30_rss_capacity_16gb_host_staggered_launches_and_unsynchronized_microbreaks() -> None:
    """T18/T30: 16GB host with realistic 1250MB JVM RSS yields 6 safe slots (never 15!) and enforces >=90s stagger."""
    calc = RssCapacityCalculator(os_core_reserve_mb=5632, rss_safety_multiplier=1.25)
    report_16g = calc.compute_safe_slots(total_ram_mb=16384, measured_per_slot_rss_mb=1250)
    # (16384 - 5632) = 10752 MB usable; 1250 * 1.25 = 1563 MB/slot => 6 slots!
    assert report_16g.computed_safe_slots == 6

    # Admission refuses new slot when free RAM drops below 1800MB without crashing active slots!
    ok, reason = calc.can_admit_next_slot(
        active_slots=4, safe_slot_limit=6, current_free_ram_mb=1200, current_cpu_pct=55.0
    )
    assert ok is False and "LOW_FREE_RAM" in reason

    planner = StaggeredLaunchPlanner(min_stagger_sec=90.0)
    plan = planner.plan_launches(["acc_01", "acc_02", "acc_03", "acc_04"], base_epoch_sec=1_000.0)
    times = list(plan.values())
    for prev_t, next_t in zip(times, times[1:]):
        assert (next_t - prev_t) >= 90.0

    # MicrobreakCoordinator prevents all 3 slots from taking AFK break at t=500 simultaneously!
    mb = MicrobreakCoordinator(deconflict_step_sec=40.0)
    w1 = mb.reserve_microbreak(account_id="acc_01", proposed_start_sec=500.0, duration_sec=30.0, active_slots=3)
    w2 = mb.reserve_microbreak(account_id="acc_02", proposed_start_sec=500.0, duration_sec=30.0, active_slots=3)
    # For active_slots=3, max_concurrent_breaks = 3 // 2 = 1 -> w2 MUST be shifted past w1!
    assert w1 == (500.0, 530.0)
    assert w2[0] >= 530.0


def test_t15_release_staging_gate_enforces_canary_and_preserves_baseline_on_failure() -> None:
    """T15: Upstream release cannot skip CONTRACT_TESTED + APPROVED + CANARY; failed canary preserves baseline."""
    gate = ReleaseStagingGate(initial_active_version="v2.1.0")
    gate.register_detected(version_tag="v2.2.0-rc1", commit_sha="abc1234")
    gate.mark_staged("v2.2.0-rc1")
    gate.mark_built("v2.2.0-rc1", jar_sha256="sha256:deadbeef")

    # Cannot jump straight to CANARY before contract tests & approval!
    with pytest.raises(ReleaseGateError):
        gate.start_canary("v2.2.0-rc1", canary_account_id="acc_canary")

    gate.mark_contract_tested("v2.2.0-rc1", passed=True)
    gate.approve_for_canary("v2.2.0-rc1", operator_id=42)
    gate.start_canary("v2.2.0-rc1", canary_account_id="acc_canary")

    # Simulate failed Canary -> active_version MUST remain v2.1.0!
    rec = gate.complete_canary("v2.2.0-rc1", canary_succeeded=False)
    assert rec.stage == ReleaseStage.ROLLED_BACK
    assert gate.active_version == "v2.1.0"

    # Now promote clean v2.2.1 all the way through Canary -> ACTIVE
    gate.register_detected(version_tag="v2.2.1", commit_sha="def5678")
    gate.mark_staged("v2.2.1")
    gate.mark_built("v2.2.1", jar_sha256="sha256:cafebabe")
    gate.mark_contract_tested("v2.2.1", passed=True)
    gate.approve_for_canary("v2.2.1", operator_id=42)
    gate.start_canary("v2.2.1", canary_account_id="acc_canary")
    gate.complete_canary("v2.2.1", canary_succeeded=True)
    assert gate.active_version == "v2.2.1"


def test_t24_economy_default_deny_active_recipe_lock_2pct_tax_and_realized_vs_unrealized_pnl(
    tmp_path: Path,
) -> None:
    """T24: Active recipe inputs CANNOT be liquidated; 2% GE tax & unrealized vs realized GP separated."""
    registry = CuratedRecipeRegistry()
    woodcut_recipe = registry.get("f2p_draynor_willow")  # requires axes including Bronze axe (1351)!

    # Even if operator accidentally added 1351 (Bronze axe) to whitelist, ActiveRecipeInputLock blocks selling it!
    policy = DefaultDenySellPolicy(
        approved_sell_whitelist={1351, 1511},  # 1351=Bronze axe, 1511=Logs
        equipped_item_ids={1277},              # Bronze sword equipped
    )
    ledger = GEOrderLedger(tmp_path / "ge.db")

    # 1. Selling active recipe tool (1351) -> BLOCKED by ACTIVE_RECIPE_INPUT_LOCK!
    with pytest.raises(SellPolicyViolationError, match="ACTIVE_RECIPE_INPUT_LOCK"):
        ledger.create_sell_order(
            account_id="acc_01",
            ge_slot=0,
            item_id=1351,
            qty=1,
            unit_price_gp=150,
            policy=policy,
            active_recipe=woodcut_recipe,
            now_epoch_sec=1_000.0,
        )

    # 2. Selling non-whitelisted item (526 = Bones) -> BLOCKED by DEFAULT_DENY_SELL!
    with pytest.raises(SellPolicyViolationError, match="DEFAULT_DENY_SELL"):
        ledger.create_sell_order(
            account_id="acc_01",
            ge_slot=0,
            item_id=526,
            qty=10,
            unit_price_gp=80,
            policy=policy,
            active_recipe=woodcut_recipe,
            now_epoch_sec=1_000.0,
        )

    # 3. Verify OSRS 2% GE tax math (<100gp -> 0 tax; 250gp -> 5gp tax; 500M gp -> 5M cap)
    assert calculate_osrs_ge_tax_per_unit(99) == 0
    assert calculate_osrs_ge_tax_per_unit(250) == 5
    assert calculate_osrs_ge_tax_per_unit(500_000_000) == 5_000_000

    # 4. Sell 100 Logs (1511) at 250 gp/ea => Gross 25,000 gp, Tax 500 gp, Net 24,500 gp
    oid = ledger.create_sell_order(
        account_id="acc_01",
        ge_slot=0,
        item_id=1511,
        qty=100,
        unit_price_gp=250,
        policy=policy,
        active_recipe=woodcut_recipe,
        now_epoch_sec=1_000.0,
    )
    ledger.record_fill_progress(order_id=oid, newly_filled_qty=40, execution_unit_price_gp=250)
    pnl_partial = ledger.account_pnl_summary("acc_01")
    # Still uncollected => 100% UNREALIZED, 0 REALIZED!
    assert pnl_partial["realized_net_sell_gp"] == 0
    assert pnl_partial["unrealized_net_sell_gp"] == 40 * (250 - 5)

    ledger.record_fill_progress(order_id=oid, newly_filled_qty=60, execution_unit_price_gp=250)
    ledger.mark_collected(oid)
    pnl_final = ledger.account_pnl_summary("acc_01")
    assert pnl_final["realized_net_sell_gp"] == 24_500
    assert pnl_final["unrealized_net_sell_gp"] == 0
    assert pnl_final["total_tax_paid_gp"] == 500

    # 5. Enforce 4-hour GE Buy-Limit cooldown!
    b1 = ledger.create_buy_order(
        account_id="acc_01",
        ge_slot=1,
        item_id=314,  # Feather (limit e.g. 13,000)
        qty=10_000,
        unit_price_gp=3,
        four_hour_buy_limit=13_000,
        now_epoch_sec=2_000.0,
    )
    ledger.record_fill_progress(order_id=b1, newly_filled_qty=10_000, execution_unit_price_gp=3)
    ledger.mark_collected(b1)
    with pytest.raises(SellPolicyViolationError, match="GE_BUY_LIMIT_COOLDOWN"):
        ledger.create_buy_order(
            account_id="acc_01",
            ge_slot=1,
            item_id=314,
            qty=5_000,
            unit_price_gp=3,
            four_hour_buy_limit=13_000,
            now_epoch_sec=2_500.0,  # within 4 hours!
        )

    ledger.close()
