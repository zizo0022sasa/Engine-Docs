"""PR-17 Hermetic Verification Suite (T56..T61):
1. Smart Telegram Event Notifications via `BoundedOutbox` (strict 4-event allowlist, zero per-tick spam,
   `P0_INCIDENT` eviction protection, and non-blocking fault resilience).
2. Universal Account Discovery Scanner (`<= 15s` budget, all 23 canonical OSRS skills, Combat Level,
   Membership/QP/Playtime/Stronghold varps & varbits, Bank + Inventory `Coins`/tools/gear/food audit,
   `state.db` `account_profiles` persistence, and `python -m osrs_ee scan-profile` Keep-Alive HTTP e2e).
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from osrs_ee.bots.base import DirectiveType, TaskDirective, TaskModule, TickContext
from osrs_ee.cli import main
from osrs_ee.core import ExecutionEngineKernel
from osrs_ee.core.lifecycle_manager import SessionLifecycleManager
from osrs_ee.discovery import (
    CANONICAL_OSRS_SKILLS,
    AccountProfileSnapshot,
    UniversalAccountScanner,
    compute_osrs_combat_level,
)
from osrs_ee.navigation import AccountCapabilitiesContext
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.security.redaction import Redactor
from osrs_ee.state import AccountStatus, OperatorRole, StateStore
from osrs_ee.telemetry import BoundedOutbox, SmartEventKind, SmartEventNotifier


class _MultiTickLootBot(TaskModule):
    """Deterministic multi-tick test bot exercising 150 normal ticks + bank deposit + COMPLETE_TASK."""

    def __init__(self, *, work_ticks: int = 150) -> None:
        self.work_ticks = work_ticks
        self._ticks_seen = 0
        self._deposited = False

    @property
    def module_id(self) -> str:
        return "stage2_stronghold_10k"

    def evaluate_tick(self, ctx: TickContext) -> TaskDirective:
        self._ticks_seen += 1
        if self._ticks_seen <= self.work_ticks:
            return TaskDirective(
                directive_type=DirectiveType.WAIT_NEXT_TICK,
                reason=f"WORKING_TICK_{self._ticks_seen}",
            )
        has_loot_in_inv = any(
            int(it.get("item_id", -1)) in (995, 9005) for it in ctx.inventory_items
        )
        if has_loot_in_inv or ctx.bank_is_open:
            self._deposited = True
            return TaskDirective(
                directive_type=DirectiveType.BANK_TURNAROUND,
                payload={"deposit_inventory": True, "withdraw": [], "close_when_done": True},
                reason="DEPOSIT_LOOT_AT_GE",
            )
        return TaskDirective(
            directive_type=DirectiveType.COMPLETE_TASK,
            reason="MISSION_ACCOMPLISHED_10K_AND_FANCY_BOOTS_BANKED",
        )


class _Escalate2FABot(TaskModule):
    @property
    def module_id(self) -> str:
        return "stage2_stronghold_10k"

    def evaluate_tick(self, ctx: TickContext) -> TaskDirective:
        return TaskDirective(
            directive_type=DirectiveType.ESCALATE_HUMAN,
            reason="JAGEX_2FA_CHALLENGE_REQUIRED",
        )


class _BankPinBot(TaskModule):
    @property
    def module_id(self) -> str:
        return "f2p_draynor_willow"

    def evaluate_tick(self, ctx: TickContext) -> TaskDirective:
        return TaskDirective(
            directive_type=DirectiveType.BANK_TURNAROUND,
            payload={"deposit_inventory": True, "withdraw": [], "close_when_done": True},
            reason="OPEN_BANK_FOR_DEPOSIT",
        )


def _setup_leased_kernel_with_notifier(
    tmp_path: Path,
    *,
    account_id: str = "acc_02",
    task_id: str = "stage2_stronghold_10k",
    sender: Any = None,
) -> tuple[StateStore, BoundedOutbox, SmartEventNotifier, FakeGameAdapter, ExecutionEngineKernel]:
    store = StateStore(tmp_path / "state.db", snapshot_path=tmp_path / "state_snapshot.json")
    store.register_account(
        account_id=account_id,
        auth_mode="LEGACY_CREDENTIALS",
        credential_ref=f"vault://accounts/{account_id}/auth",
        proxy_ref=f"vault://proxies/{account_id}",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=202,
        initial_status=AccountStatus.READY,
    )
    lease = store.acquire_session_lease(
        account_id=account_id,
        slot_id=0,
        task_id=task_id,
        now_utc=datetime(2026, 10, 29, 9, 0, tzinfo=timezone.utc),
        bypass_circadian=True,
    )
    adapter = FakeGameAdapter(
        account_id=account_id,
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        inventory_items=[
            {"item_id": 995, "name": "Coins", "quantity": 10000, "noted": False},
            {"item_id": 9005, "name": "Fancy boots", "quantity": 1, "noted": False},
        ],
    )
    gate = GameAdapterGate(store, adapter)
    gate.bind_to_lease(
        account_id=account_id,
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
    )
    outbox = BoundedOutbox(tmp_path / "outbox.db")
    notifier = SmartEventNotifier(
        outbox=outbox,
        target_chat_ids=(-10099887766,),
        sender=sender,
    )
    kernel = ExecutionEngineKernel(
        gate=gate,
        account_id=account_id,
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
        capabilities=AccountCapabilitiesContext(account_id=account_id, combat_level=3),
        notifier=notifier,
        slot_id=0,
        adapter_port=8081,
    )
    return store, outbox, notifier, adapter, kernel


def test_t56_smart_outbox_zero_tick_spam_session_start_and_complete_loot_report(tmp_path: Path) -> None:
    """T56: Proves zero per-tick notification spam across 150+ ticks, single-fire `🚀 بدء جلسة حساب`,
    exact `🏆 إتمام المهمة` coins/loot summary + clean `RESTING` shutdown, and network failure safety.
    """
    delivered: list[tuple[int, str]] = []
    fail_network = {"offline": True}

    def _flaky_sender(chat_id: int, text: str) -> tuple[bool, float]:
        if fail_network["offline"]:
            raise TimeoutError("Simulated router outage during gameplay")
        delivered.append((chat_id, text))
        return (True, 0.0)

    store, outbox, notifier, adapter, kernel = _setup_leased_kernel_with_notifier(
        tmp_path, sender=_flaky_sender
    )
    try:
        bot = _MultiTickLootBot(work_ticks=150)
        # Damage player slightly on tick 10 with food in inventory to prove survival ticks also produce ZERO spam
        adapter.inventory_items.append({"item_id": 315, "name": "Shrimps", "quantity": 1, "noted": False})
        adapter.hp_current = 4

        halted_res = None
        for step in range(1, 165):
            res = kernel.step_tick(bot, now_monotonic=float(step))
            if step <= 150:
                # Across all 150 active ticks (including eating & run toggle), ONLY the 1 session-start event exists!
                assert len(notifier.emitted_events) == 1
                assert notifier.emitted_events[0]["kind"] == SmartEventKind.SESSION_STARTED.value
            if res.halted:
                halted_res = res
                break

        assert halted_res is not None
        assert halted_res.task_directive is not None
        assert halted_res.task_directive.directive_type == DirectiveType.COMPLETE_TASK

        # Total events for entire 154-tick session = 2 (SESSION_STARTED + TASK_COMPLETED), ZERO tick spam!
        assert [e["kind"] for e in notifier.emitted_events] == [
            SmartEventKind.SESSION_STARTED.value,
            SmartEventKind.TASK_COMPLETED.value,
        ]
        start_txt = notifier.emitted_events[0]["text"]
        done_txt = notifier.emitted_events[1]["text"]
        assert "🚀 بدء جلسة حساب" in start_txt
        assert "[acc_02]" in start_txt
        assert "Slot 0 | Port 8081" in start_txt

        assert "🏆 إتمام المهمة وإيداع الغنائم في البنك" in done_txt
        assert "10,000 GP" in done_txt
        assert "Fancy boots x1" in done_txt
        assert "RESTING" in done_txt

        # Verify session closed cleanly in StateStore despite simulated Telegram outage!
        acc_after = store.get_account("acc_02")
        assert acc_after["status"] == AccountStatus.RESTING.value
        assert outbox.pending_counts_by_priority() == {"ROUTINE": 2}

        # Router comes back online -> buffered outbox messages flush cleanly regardless of host OS uptime
        fail_network["offline"] = False
        flushed = outbox.flush_due(_flaky_sender, now_monotonic=time.monotonic() + 100.0)
        assert flushed == 2
        assert len(delivered) == 2
        assert outbox.pending_counts_by_priority() == {}
    finally:
        outbox.close()
        store.close()


def test_t57_smart_outbox_p0_incidents_and_eviction_protection_all_triggers(tmp_path: Path) -> None:
    """T57: Verifies `🚨 تنبيه طوارئ P0` (`P0_INCIDENT`) on `DEATH_QUARANTINE`, `BANK_PIN_REQUIRED`,
    `HUMAN_AUTH_REQUIRED` (2FA), `PROXY_UNAVAILABLE`, and `ABORT_TASK`, plus overflow protection & redaction.
    """
    redactor = Redactor()
    secret_pw = "UltraSecretPin_998877!"
    redactor.register_secret(secret_pw)

    # 1. Death Quarantine (`PLAYER_DEAD_QUARANTINE`)
    s1, o1, n1, a1, k1 = _setup_leased_kernel_with_notifier(tmp_path / "p0_death")
    try:
        a1.player_dead = True
        r1 = k1.step_tick(_MultiTickLootBot(work_ticks=5), now_monotonic=1.0)
        assert r1.halted is True
        assert n1.emitted_events[-1]["kind"] == SmartEventKind.P0_INCIDENT.value
        assert n1.emitted_events[-1]["reason_code"] == "DEATH_QUARANTINE"
        assert "🚨 تنبيه طوارئ P0" in n1.emitted_events[-1]["text"]
    finally:
        o1.close()
        s1.close()

    # 2. Bank PIN Prompt (`BANK_PIN_REQUIRED`)
    s2, o2, n2, a2, k2 = _setup_leased_kernel_with_notifier(tmp_path / "p0_pin")
    try:
        a2.bank_open = True
        a2.bank_pin_prompt_open = True
        r2 = k2.step_tick(_BankPinBot(), now_monotonic=1.0)
        assert r2.halted is True
        assert n2.emitted_events[-1]["kind"] == SmartEventKind.P0_INCIDENT.value
        assert n2.emitted_events[-1]["reason_code"] == "BANK_PIN_REQUIRED"
    finally:
        o2.close()
        s2.close()

    # 3. 2FA / Human Escalation (`HUMAN_AUTH_REQUIRED`)
    s3, o3, n3, _a3, k3 = _setup_leased_kernel_with_notifier(tmp_path / "p0_2fa")
    try:
        r3 = k3.step_tick(_Escalate2FABot(), now_monotonic=1.0)
        assert r3.halted is True
        assert n3.emitted_events[-1]["kind"] == SmartEventKind.P0_INCIDENT.value
        assert n3.emitted_events[-1]["reason_code"] == "HUMAN_AUTH_REQUIRED"
    finally:
        o3.close()
        s3.close()

    # 4. Proxy / Connection Exhaustion (`PROXY_UNAVAILABLE`) + `ABORT_TASK` + Overflow Eviction Protection
    s4, _o4_unused, _n4_unused, a4, _k4_unused = _setup_leased_kernel_with_notifier(tmp_path / "p0_proxy")
    tiny_outbox = BoundedOutbox(tmp_path / "p0_proxy" / "tiny_outbox.db", max_pending_messages=3, redactor=redactor)
    try:
        # Fill tiny outbox (capacity=3) with 3 ROUTINE notifications
        for idx in range(3):
            tiny_outbox.enqueue(chat_id=-1001, text=f"Routine msg {idx}", priority="ROUTINE")
        assert tiny_outbox.pending_counts_by_priority() == {"ROUTINE": 3}

        n4 = SmartEventNotifier(outbox=tiny_outbox, target_chat_ids=(-1001,), redactor=redactor)
        lease4 = s4._conn.execute("SELECT * FROM active_leases WHERE account_id = 'acc_02'").fetchone()
        k4 = ExecutionEngineKernel(
            gate=_k4_unused.gate,
            account_id="acc_02",
            session_id=str(lease4["session_id"]),
            lease_id=str(lease4["lease_id"]),
            session_epoch=int(lease4["session_epoch"]),
            capabilities=AccountCapabilitiesContext(account_id="acc_02", combat_level=3),
            lifecycle=SessionLifecycleManager(max_heal_attempts=1, base_backoff_sec=0.01),
            notifier=n4,
            slot_id=2,
            adapter_port=8083,
        )
        a4.transient_disconnect = True
        assert k4.step_tick(_MultiTickLootBot(work_ticks=5), now_monotonic=1.0).halted is False
        r_drop = k4.step_tick(_MultiTickLootBot(work_ticks=5), now_monotonic=2.0)
        assert r_drop.halted is True

        # Also emit explicit ABORT_TASK containing a secret to verify redaction + P0 preservation
        k4.notify_emergency_abort(
            reason_code="ABORT_TASK",
            detail=f"Emergency stop triggered (pin={secret_pw})",
        )
        counts = tiny_outbox.pending_counts_by_priority()
        assert counts.get("P0_INCIDENT") == 2
        assert sum(counts.values()) == 3  # Evicted ROUTINE messages while preserving 100% of P0_INCIDENT alerts!

        rows = tiny_outbox._conn.execute("SELECT text FROM outbox WHERE priority = 'P0_INCIDENT'").fetchall()
        joined = "\n".join(str(r["text"]) for r in rows)
        assert secret_pw not in joined
        assert "<REDACTED_SECRET>" in joined
        assert "PROXY_UNAVAILABLE" in joined
        assert "ABORT_TASK" in joined
    finally:
        tiny_outbox.close()
        _o4_unused.close()
        s4.close()


def test_t58_crash_recovery_reconciliation_emits_smart_recovery_notification(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """T58: Proves `RECOVERY_REQUIRED -> READY` reconciliation emits `🔧 استرداد تلقائي بعد انقطاع`."""
    data_dir = tmp_path / "data"
    monkeypatch.setenv("OSRS_EE_TELEGRAM_ADMIN_CHAT_ID", "-100555666")
    assert main(["--data-dir", str(data_dir), "init"]) == 0
    _ = capsys.readouterr()

    store = StateStore(data_dir / "state.db", snapshot_path=data_dir / "state_snapshot.json")
    try:
        store.register_account(
            account_id="acc_02",
            auth_mode="LEGACY_CREDENTIALS",
            credential_ref="vault://accounts/acc_02/auth",
            proxy_ref="vault://proxies/acc_02",
            region="EG",
            timezone_name="Africa/Cairo",
            persona_seed=303,
            initial_status=AccountStatus.READY,
        )
        # Simulate crashed worker holding dead PID 99999999
        store.acquire_session_lease(
            account_id="acc_02",
            slot_id=0,
            task_id="stage2_stronghold_10k",
            bypass_circadian=True,
            owner_pid=99999999,
        )
    finally:
        store.close()

    rc = main(["--data-dir", str(data_dir), "reconcile", "--account-id", "acc_02", "--acknowledge"])
    assert rc == 0
    out_json = json.loads(capsys.readouterr().out)
    assert out_json["ok"] is True
    assert out_json["reclaimed_orphaned_leases"] == ["acc_02"]
    assert out_json["transitioned_to_ready"] == ["acc_02"]

    outbox = BoundedOutbox(data_dir / "outbox.db")
    try:
        rows = outbox._conn.execute("SELECT chat_id, priority, text FROM outbox").fetchall()
        assert len(rows) == 1
        assert int(rows[0]["chat_id"]) == -100555666
        assert str(rows[0]["priority"]) == "ROUTINE"
        assert "🔧 استرداد تلقائي بعد انقطاع" in str(rows[0]["text"])
        assert "[acc_02]" in str(rows[0]["text"])
        assert "RECOVERY_REQUIRED -> READY" in str(rows[0]["text"])
    finally:
        outbox.close()


def test_t59_universal_account_scanner_23_skills_combat_varps_and_bank_audit_under_15s(
    tmp_path: Path,
) -> None:
    """T59: Verifies `UniversalAccountScanner` across Level-3 F2P & Level-78 Member accounts:
    all 23 canonical OSRS skills, Combat Level math, Membership/QP/Stronghold varps & varbits,
    Bank + Inventory `Coins`/tools/gear/food audit, `<15s` runtime, and `state.db` persistence.
    """
    assert compute_osrs_combat_level({"attack": 1, "strength": 1, "defence": 1, "hitpoints": 10, "prayer": 1}) == 3
    assert compute_osrs_combat_level({sk: 99 for sk in CANONICAL_OSRS_SKILLS}) == 126

    store = StateStore(tmp_path / "state.db", snapshot_path=tmp_path / "state_snapshot.json")
    try:
        store.register_account(
            account_id="acc_02",
            auth_mode="LEGACY_CREDENTIALS",
            credential_ref="vault://accounts/acc_02/auth",
            proxy_ref="vault://proxies/acc_02",
            region="EG",
            timezone_name="Africa/Cairo",
            persona_seed=404,
            initial_status=AccountStatus.READY,
        )
        member_skills = {
            "attack": 65,
            "strength": 70,
            "defence": 60,
            "hitpoints": 68,
            "prayer": 43,
            "ranged": 55,
            "magic": 62,
            "agility": 45,
            "woodcutting": 52,
            "fishing": 48,
            "cooking": 50,
            "mining": 41,
            "crafting": 30,
        }
        fa = FakeGameAdapter(
            account_id="acc_02",
            session_id="scan_sess_01",
            session_epoch=1,
            tutorial_varp_281=1000,
            quest_points=42,
            playtime_minutes=1850,
            skills=member_skills,
            is_member=True,
            inventory_items=[
                {"item_id": 995, "name": "Coins", "quantity": 25000, "noted": False},
                {"item_id": 303, "name": "Small fishing net", "quantity": 1, "noted": False},
                {"item_id": 379, "name": "Lobster", "quantity": 5, "noted": False},
            ],
            bank_items=[
                {"item_id": 995, "name": "Coins", "quantity": 100000, "noted": False},
                {"item_id": 1359, "name": "Rune axe", "quantity": 1, "noted": False},
                {"item_id": 1275, "name": "Rune pickaxe", "quantity": 1, "noted": False},
                {"item_id": 309, "name": "Fly fishing rod", "quantity": 1, "noted": False},
                {"item_id": 314, "name": "Feather", "quantity": 800, "noted": False},
                {"item_id": 1333, "name": "Rune scimitar", "quantity": 1, "noted": False},
                {"item_id": 9005, "name": "Fancy boots", "quantity": 1, "noted": False},
                {"item_id": 379, "name": "Lobster", "quantity": 20, "noted": False},
            ],
        )
        fa.varps.update({101: 42, 1780: 14, 29: 2, 179: 21})
        fa.varbits.update({2309: 1, 2310: 1, 2311: 1, 2312: 1})

        scanner = UniversalAccountScanner(max_duration_sec=15.0)
        snap = scanner.scan(account_id="acc_02", adapter=fa, store=store)

        assert len(snap.skills) == 23
        assert set(snap.skills.keys()) == set(CANONICAL_OSRS_SKILLS)
        assert snap.skills["woodcutting"] == 52
        assert snap.skills["agility"] == 45
        assert snap.skills["runecraft"] == 1
        assert snap.combat_level == compute_osrs_combat_level(member_skills)
        assert snap.is_member is True
        assert snap.membership_days == 14
        assert snap.quest_points == 42
        assert "cooks_assistant" in snap.completed_quests
        assert "sheep_shearer" in snap.completed_quests
        assert snap.stronghold_floors_claimed == (1, 2, 3, 4)
        assert snap.bank_scanned is True
        assert fa.bank_open is False  # Scanner closed bank cleanly after audit!
        assert snap.inventory_coins_gp == 25_000
        assert snap.bank_coins_gp == 100_000
        assert snap.total_liquid_coins_gp == 125_000
        assert "Rune axe" in snap.available_tools
        assert "Rune pickaxe" in snap.available_tools
        assert "Fly fishing rod" in snap.available_tools
        assert "Rune scimitar" in snap.available_gear
        assert "Fancy boots" in snap.available_gear
        assert snap.available_food["Lobster"] == 25
        assert snap.trade_unlocked is True
        assert snap.progression_stage == "STAGE_3_F2P_SKILLING"
        assert "f2p_draynor_willow" in snap.eligible_recipes
        assert "f2p_barbarian_fly_fishing" in snap.eligible_recipes
        assert "f2p_alkharid_mining" in snap.eligible_recipes
        # Stronghold 1..4 all claimed -> stage2_stronghold_10k excluded from remaining eligible list!
        assert "stage2_stronghold_10k" not in snap.eligible_recipes
        assert snap.scan_duration_ms < 5000

        # Verify persistence in state.db + round-trip to AccountCapabilitiesContext
        persisted = store.get_account_profile("acc_02")
        assert persisted is not None
        rehydrated = AccountProfileSnapshot.from_dict(persisted)
        caps = rehydrated.to_capabilities_context()
        assert caps.agility_level == 45
        assert caps.is_member is True
        assert caps.coins_gp == 125_000
        assert caps.walker_flags["allowShortcuts"] is True if hasattr(caps, "walker_flags") else caps.to_walker_constraint_flags()["allowShortcuts"] is True
    finally:
        store.close()


class _KeepAliveMicrobotMock:
    """Real loopback HTTP/1.1 Keep-Alive server emulating upstream Microbot AgentServer Java handlers."""

    def __init__(self, token: str) -> None:
        self.token = token
        self.bank_open = False
        self.state_payload: dict[str, Any] = {
            "loggedIn": True,
            "gameState": "LOGGED_IN",
            "player": {
                "name": "zaza266",
                "combatLevel": 34,
                "healthRatio": 30,
                "healthScale": 30,
                "animating": False,
                "animationId": -1,
                "moving": False,
                "interacting": False,
                "position": {"x": 3164, "y": 3487, "plane": 0},
            },
        }
        self.varps: dict[int, int] = {281: 1000, 173: 1, 101: 14, 1780: 0, 29: 2}
        self.varbits: dict[int, int] = {2309: 1, 2310: 1, 2311: 1, 2312: 0}
        self.skills_list: list[dict[str, Any]] = []
        custom_lvls = {"hitpoints": 32, "agility": 25, "woodcutting": 38, "fishing": 24, "cooking": 28, "attack": 30, "strength": 30, "defence": 25}
        for sk in CANONICAL_OSRS_SKILLS:
            lvl = custom_lvls.get(sk, 1)
            self.skills_list.append(
                {"name": sk.capitalize(), "level": lvl, "boostedLevel": lvl, "xp": lvl * 1000}
            )
        self.inventory_items: list[dict[str, Any]] = [
            {"id": 995, "name": "Coins", "quantity": 10000, "slot": 0, "stackable": True, "noted": False},
            {"id": 303, "name": "Small fishing net", "quantity": 1, "slot": 1, "stackable": False, "noted": False},
            {"id": 315, "name": "Shrimps", "quantity": 8, "slot": 2, "stackable": False, "noted": False},
        ]
        self.bank_items: list[dict[str, Any]] = [
            {"id": 995, "name": "Coins", "quantity": 40000},
            {"id": 1359, "name": "Rune axe", "quantity": 1},
            {"id": 9005, "name": "Fancy boots", "quantity": 1},
        ]

        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            disable_nagle_algorithm = True

            def log_message(self, fmt: str, *args: Any) -> None:
                return

            def _send(self, code: int, obj: dict[str, Any]) -> None:
                raw = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                if self.headers.get("X-Agent-Token") != mock.token:
                    self._send(401, {"error": "unauthorized"})
                    return
                if self.path == "/state":
                    self._send(200, mock.state_payload)
                elif self.path.startswith("/skills"):
                    if "name=Hitpoints" in self.path:
                        hp_entry = [s for s in mock.skills_list if s["name"].lower() == "hitpoints"]
                        self._send(200, {"count": 1, "totalLevel": 32, "skills": hp_entry})
                    else:
                        total = sum(int(s["level"]) for s in mock.skills_list)
                        self._send(200, {"count": len(mock.skills_list), "totalLevel": total, "skills": mock.skills_list})
                elif self.path.startswith("/varps?id="):
                    pid = int(self.path.split("=", 1)[1])
                    self._send(200, {"varpId": pid, "value": mock.varps.get(pid, 0)})
                elif self.path.startswith("/varbits?id="):
                    vid = int(self.path.split("=", 1)[1])
                    self._send(200, {"varbitId": vid, "value": mock.varbits.get(vid, 0)})
                elif self.path == "/profiles":
                    self._send(200, {"count": 1, "activeProfile": "zaza266", "profiles": [{"name": "zaza266", "isMember": False, "active": True}]})
                elif self.path == "/inventory":
                    self._send(200, {"count": len(mock.inventory_items), "capacity": 28, "freeSlots": 28 - len(mock.inventory_items), "items": mock.inventory_items})
                elif self.path == "/bank":
                    self._send(200, {"open": mock.bank_open, "count": len(mock.bank_items) if mock.bank_open else 0, "items": mock.bank_items if mock.bank_open else []})
                elif self.path == "/dialogue":
                    self._send(200, {"inDialogue": False, "hasContinue": False, "hasOptions": False})
                else:
                    self._send(404, {"error": "not_found"})

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 0:
                    _ = self.rfile.read(length)
                if self.headers.get("X-Agent-Token") != mock.token:
                    self._send(401, {"error": "unauthorized"})
                    return
                if self.path == "/bank/open":
                    mock.bank_open = True
                    self._send(200, {"opened": True})
                elif self.path == "/bank/close":
                    mock.bank_open = False
                    self._send(200, {"closed": True})
                else:
                    self._send(200, {"success": True})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = int(self.server.server_address[1])
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.server.server_close()


def test_t60_cli_scan_profile_e2e_over_live_http_keepalive_server(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """T60: Verifies `python -m osrs_ee scan-profile --account-id acc_02 --port <port>` end-to-end
    over HTTP/1.1 Keep-Alive, promoting `PENDING_DISCOVERY -> READY` and persisting `AccountProfileSnapshot`.
    """
    token = "agent_tok_pr17_secret_abcdef123456"
    tok_file = tmp_path / ".agent-token"
    tok_file.write_text(token, encoding="utf-8")
    data_dir = tmp_path / "data"

    mock = _KeepAliveMicrobotMock(token)
    mock.start()
    try:
        rc = main(
            [
                "--data-dir",
                str(data_dir),
                "scan-profile",
                "--account-id",
                "acc_02",
                "--port",
                str(mock.port),
                "--token-file",
                str(tok_file),
            ]
        )
        assert rc == 0
        raw_out = capsys.readouterr().out
        assert token not in raw_out

        payload = json.loads(raw_out)
        assert payload["ok"] is True
        assert payload["account_id"] == "acc_02"
        assert payload["account_status"] == "READY"

        prof = payload["profile"]
        assert len(prof["skills"]) == 23
        assert prof["combat_level"] == 34
        assert prof["quest_points"] == 14
        assert prof["stronghold_floors_claimed"] == [1, 2, 3]
        assert prof["inventory_coins_gp"] == 10_000
        assert prof["bank_coins_gp"] == 40_000
        assert prof["total_liquid_coins_gp"] == 50_000
        assert prof["bank_scanned"] is True
        assert mock.bank_open is False
        assert "Rune axe" in prof["available_tools"]
        assert "Small fishing net" in prof["available_tools"]
        assert "Fancy boots" in prof["available_gear"]
        assert prof["available_food"]["Shrimps"] == 8
        assert "stage2_stronghold_10k" in prof["eligible_recipes"]
        assert "f2p_draynor_willow" in prof["eligible_recipes"]
        assert prof["scan_duration_ms"] < 15_000

        # Verify state.db & state_snapshot.json persistence
        snap_json = json.loads((data_dir / "state_snapshot.json").read_text(encoding="utf-8"))
        assert snap_json["accounts"]["acc_02"]["discovered_profile"]["total_liquid_coins_gp"] == 50_000
    finally:
        mock.close()


def test_t61_attach_bot_loads_scanned_profile_capabilities_and_records_smart_outbox_e2e(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """T61: Proves `attach-bot` loads the discovered `AccountProfileSnapshot` from `state.db` and
    records ONLY `SESSION_STARTED` + `TASK_COMPLETED` in `outbox.db` when completing a mission.
    """
    token = "agent_tok_pr17_attach_9876543210abcd"
    tok_file = tmp_path / ".agent-token"
    tok_file.write_text(token, encoding="utf-8")
    data_dir = tmp_path / "data"
    monkeypatch.setenv("OSRS_EE_TELEGRAM_ADMIN_CHAT_ID", "-100777888")

    mock = _KeepAliveMicrobotMock(token)
    # Mark all 4 Stronghold floors already claimed & inventory empty so StrongholdTaskModule immediately completes cleanly
    mock.varbits = {2309: 1, 2310: 1, 2311: 1, 2312: 1}
    mock.inventory_items = []
    mock.start()
    try:
        assert (
            main(
                [
                    "--data-dir",
                    str(data_dir),
                    "scan-profile",
                    "--account-id",
                    "acc_02",
                    "--port",
                    str(mock.port),
                    "--token-file",
                    str(tok_file),
                ]
            )
            == 0
        )
        _ = capsys.readouterr()

        rc_att = main(
            [
                "--data-dir",
                str(data_dir),
                "attach-bot",
                "--account-id",
                "acc_02",
                "--recipe-id",
                "stage2_stronghold_10k",
                "--port",
                str(mock.port),
                "--token-file",
                str(tok_file),
                "--max-ticks",
                "5",
                "--tick-interval-sec",
                "0",
                "--quiet",
            ]
        )
        assert rc_att == 0
        att_out = json.loads(capsys.readouterr().out)
        assert att_out["ok"] is True
        assert att_out["final_directive"] == "COMPLETE_TASK"

        outbox = BoundedOutbox(data_dir / "outbox.db")
        try:
            rows = outbox._conn.execute("SELECT text FROM outbox ORDER BY msg_id ASC").fetchall()
            assert len(rows) == 2
            assert "🚀 بدء جلسة حساب" in str(rows[0]["text"])
            assert "🏆 إتمام المهمة وإيداع الغنائم في البنك" in str(rows[1]["text"])
        finally:
            outbox.close()
    finally:
        mock.close()


def test_t62_surface_20_tick_vs_underground_5_tick_stall_reroute_budget() -> None:
    """T62 (`PR-17.1` / `PR-17.2`): Proves surface graph edges `(3080, 3421) -> BARBARIAN_RIVER_FIRE (3078, 3445)`
    allow 20 ticks (`12.0s`) for natural multi-tile WebWalker travel without premature edge blacklisting,
    while underground dungeon steps (`y >= 5000`) preserve fast `<= 5`-tick recovery.
    """
    from osrs_ee.navigation import ConstrainedWaypointGraph, NavigationEngine
    from osrs_ee.navigation.waypoint_graph import WorldPoint

    graph = ConstrainedWaypointGraph()
    nav = NavigationEngine(graph)
    caps = AccountCapabilitiesContext("acc_02", skills={"agility": 1, "hitpoints": 10}, combat_level=3)

    # 1. Surface walk from Barbarian Village (3080, 3421) -> BARBARIAN_RIVER_FIRE (3078, 3445)
    p_village = WorldPoint(3080, 3421, 0)
    cmd_1 = nav.step_navigation_on_tick(
        current_pos=p_village,
        tick_advanced=True,
        capabilities=caps,
        destination="BARBARIAN_RIVER_FIRE",
    )
    assert cmd_1 is not None and (cmd_1["x"], cmd_1["y"]) == (3078, 3445)

    # Across 19 ticks (11.4s), MUST NOT blacklist the direct edge or yank the player toward (3105, 3420)!
    for _ in range(19):
        nxt = nav.step_navigation_on_tick(
            current_pos=p_village,
            tick_advanced=True,
            capabilities=caps,
            destination="BARBARIAN_RIVER_FIRE",
        )
        assert nxt is None
    assert nav._blacklisted_edges_by_account.get("acc_02", set()) == set()

    # On the 20th stalled tick (12.0s completely stationary), reroutes cleanly
    cmd_21 = nav.step_navigation_on_tick(
        current_pos=p_village,
        tick_advanced=True,
        capabilities=caps,
        destination="BARBARIAN_RIVER_FIRE",
    )
    assert cmd_21 is not None

    # 2. Underground dungeon (`y >= 5000`) still recovers within 3 ticks!
    p_dungeon_start = WorldPoint(2358, 5218, 0)
    p_dungeon_goal = WorldPoint(2343, 5218, 0)
    d1 = nav.step_navigation_on_tick(
        current_pos=p_dungeon_start,
        tick_advanced=True,
        capabilities=caps,
        destination=p_dungeon_goal,
    )
    assert d1 is not None
    assert nav.step_navigation_on_tick(current_pos=p_dungeon_start, tick_advanced=True, capabilities=caps, destination=p_dungeon_goal) is None
    assert nav.step_navigation_on_tick(current_pos=p_dungeon_start, tick_advanced=True, capabilities=caps, destination=p_dungeon_goal) is None
    d4 = nav.step_navigation_on_tick(
        current_pos=p_dungeon_start,
        tick_advanced=True,
        capabilities=caps,
        destination=p_dungeon_goal,
    )
    assert d4 is not None


def test_t63_select_an_option_live_ground_truth_and_permanent_village_fire() -> None:
    """T63 (`PR-17.2`): Verifies:
    1. Live Floor 4 `Portal of Death` ground-truth payload (`"question": "Select an option"`,
       `["Talk to any banker.", "Use the account management section on the website."]`) resolves
       deterministically via Tier 1B `SH_KNOWN_ANSWER_MATCH`.
    2. All unique verified answers in `LOCAL_SECURITY_CACHE` resolve under `"Select an option"`,
       `_GENERIC_SAFE_FALLBACKS` covers `"two-factor"`, `"authenticator"`, and `"banker"`, and
       unrecognized/ambiguous options still fail closed (`matched is False`).
    3. `StrongholdTaskModule` at Floor 4 `(2359, 5231)` emits `SELECT_DIALOGUE` (`"Talk to any banker."`)
       and `BARBARIAN_RIVER_FIRE` resolves to `(3078, 3445, 0)` across graph & module.
    """
    from osrs_ee.ai.dialogue_solver import LOCAL_SECURITY_CACHE, DeterministicStrongholdSolver
    from osrs_ee.bots.base import DirectiveType, TickContext
    from osrs_ee.bots.stronghold_module import StrongholdTaskModule
    from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint

    solver = DeterministicStrongholdSolver()

    # 1. Exact live JSON payload captured on Port 8081 at Floor 4 Portal of Death
    live_res = solver.solve(
        "Select an option",
        [
            "Talk to any banker.",
            "Use the account management section on the website.",
        ],
    )
    assert live_res.matched is True
    assert live_res.option_index == 0
    assert live_res.selected_option == "Talk to any banker."
    assert live_res.rule_id == "SH_KNOWN_ANSWER_MATCH"

    # 2. Every unique answer in LOCAL_SECURITY_CACHE resolves cleanly under "Select an option"
    for ans in set(LOCAL_SECURITY_CACHE.values()):
        r = solver.solve("Select an option", ["Click the suspicious phishing link immediately.", ans])
        assert r.matched is True
        assert r.option_index == 1
        assert r.selected_option == ans
        assert r.rule_id == "SH_KNOWN_ANSWER_MATCH"

    # 3. Expanded _GENERIC_SAFE_FALLBACKS covers two-factor / authenticator / banker variants
    fb_2fa = solver.solve(
        "Select an option",
        ["Give password to clan leader.", "Enable two-factor protection right away."],
    )
    assert fb_2fa.matched is True and fb_2fa.option_index == 1 and fb_2fa.rule_id == "SH_GENERIC_SAFE"

    # Fail-closed invariant preserved when options are completely unknown
    unknown_res = solver.solve("Select an option", ["Option Alpha", "Option Beta"])
    assert unknown_res.matched is False

    # 4. StrongholdTaskModule at Floor 4 (2359, 5231) answers Portal of Death and routes Fire to (3078, 3445)
    assert (DEFAULT_WORLD_NODES["BARBARIAN_RIVER_FIRE"].x, DEFAULT_WORLD_NODES["BARBARIAN_RIVER_FIRE"].y) == (
        3078,
        3445,
    )
    bot = StrongholdTaskModule(min_required_food=1)
    f4_ctx = TickContext(
        game_tick=104,
        position=WorldPoint(2359, 5231, 0),
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=({"id": 2142, "name": "Cooked meat", "quantity": 1},),
        dialogue_open=True,
        dialogue_question="Select an option",
        dialogue_options=(
            "Talk to any banker.",
            "Use the account management section on the website.",
        ),
        varbits={2309: 1, 2310: 1, 2311: 1, 2312: 0},
        total_level=35,
    )
    f4_dir = bot.evaluate_tick(f4_ctx)
    assert f4_dir.directive_type == DirectiveType.SELECT_DIALOGUE
    assert f4_dir.payload == {"option": "Talk to any banker."}


def test_t64_floor4_vestibule_2_of_4_max_dist_1_anti_oscillation() -> None:
    """T64 (`PR-17.3`): Proves that inside Floor 4 vestibule `(2359, 5232)`, `StrongholdTaskModule`
    emits `NAVIGATE_TO (2360, 5234)` (`max_dist=1`) instead of clicking `Portal of Death` from `y=5232`
    (where Microbot `nearestOnClientThread()` would click the southern portal at `2359, 5232`), and
    only emits `INTERACT_OBJECT` once standing at `chebyshev_distance <= 1` (`(2360, 5234)`).
    """
    from osrs_ee.bots.base import DirectiveType, TickContext
    from osrs_ee.bots.stronghold_module import StrongholdTaskModule
    from osrs_ee.navigation.waypoint_graph import WorldPoint

    bot = StrongholdTaskModule(min_required_food=1)
    caps = AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3)

    # At (2359, 5232), dist to (2360, 5234) is 2 > max_dist=1 -> MUST walk north first!
    ctx_south_entry = TickContext(
        game_tick=180,
        position=WorldPoint(2359, 5232, 0),
        capabilities=caps,
        inventory_items=({"id": 2142, "name": "Cooked meat", "quantity": 1},),
        varbits={2309: 1, 2310: 1, 2311: 1, 2312: 0},
        total_level=35,
    )
    d_walk = bot.evaluate_tick(ctx_south_entry)
    assert d_walk.directive_type == DirectiveType.NAVIGATE_TO
    assert isinstance(d_walk.destination, WorldPoint)
    assert (d_walk.destination.x, d_walk.destination.y) == (2360, 5234)

    # Once standing at (2360, 5234) (dist=0 <= 1; 1 tile from north portal 2360,5235 vs 2 from south 2359,5232) -> Pass-through!
    ctx_north_door = TickContext(
        game_tick=182,
        position=WorldPoint(2360, 5234, 0),
        capabilities=caps,
        inventory_items=({"id": 2142, "name": "Cooked meat", "quantity": 1},),
        varbits={2309: 1, 2310: 1, 2311: 1, 2312: 0},
        total_level=35,
    )
    d_pass = bot.evaluate_tick(ctx_north_door)
    assert d_pass.directive_type == DirectiveType.INTERACT_OBJECT
    assert d_pass.payload == {
        "name": "Portal of Death",
        "action": "Pass-through",
        "target_x": 2360,
        "target_y": 5234,
    }


def test_t65_floor4_room3_west_curve_x_lt_2359_no_vestibule_bounceback() -> None:
    """T65 (`PR-17.4`): Proves that once the player crosses Portal [2/4] into Room 3 and curves
    west/south across `2352 <= x <= 2358` with `5232 <= y <= 5234` toward Portal [3/4] `(2341, 5226)`,
    `StrongholdTaskModule` NEVER misclassifies the player into Vestibule [2/4] (`px >= 2359`) and
    NEVER falls through prematurely to `Cradle of Life` `(2343, 5215)` — every tile stays locked on
    `Floor 4 [3/4]` `(2341, 5226)`, while `(2359, 5234)` inside the vestibule still passes `[2/4]`.
    """
    from osrs_ee.bots.base import DirectiveType, TickContext
    from osrs_ee.bots.stronghold_module import StrongholdTaskModule
    from osrs_ee.navigation.waypoint_graph import WorldPoint

    bot = StrongholdTaskModule(min_required_food=1)
    caps = AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3)

    # Current live standing tile (2359, 5234) inside vestibule -> passes Portal [2/4] immediately
    ctx_in_vestibule = TickContext(
        game_tick=120,
        position=WorldPoint(2359, 5234, 0),
        capabilities=caps,
        inventory_items=({"id": 2142, "name": "Cooked meat", "quantity": 1},),
        varbits={2309: 1, 2310: 1, 2311: 1, 2312: 0},
        total_level=35,
    )
    d_vest = bot.evaluate_tick(ctx_in_vestibule)
    assert d_vest.directive_type == DirectiveType.INTERACT_OBJECT
    assert "Floor 4 [2/4]" in d_vest.reason

    # Curving west & south inside Room 3 across x in 2352..2358, y in 5232..5234 -> MUST target [3/4] (2341, 5226)!
    for rx, ry in ((2358, 5234), (2356, 5233), (2352, 5232), (2348, 5229)):
        ctx_r3 = TickContext(
            game_tick=125,
            position=WorldPoint(rx, ry, 0),
            capabilities=caps,
            inventory_items=({"id": 2142, "name": "Cooked meat", "quantity": 1},),
            varbits={2309: 1, 2310: 1, 2311: 1, 2312: 0},
            total_level=35,
        )
        d_r3 = bot.evaluate_tick(ctx_r3)
        assert d_r3.directive_type == DirectiveType.NAVIGATE_TO, f"Unexpected directive at ({rx},{ry}): {d_r3}"
        assert isinstance(d_r3.destination, WorldPoint)
        assert (d_r3.destination.x, d_r3.destination.y) == (2341, 5226), (
            f"Room 3 tile ({rx},{ry}) bounced to {d_r3.destination} instead of Portal [3/4] (2341, 5226)!"
        )
        assert "Floor 4 [3/4]" in d_r3.reason




