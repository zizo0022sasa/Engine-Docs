"""Decoupled Micro-Engine Execution Kernel (PR-12 / AUD-36..39 / ADR-009..012 / T33..T36).

Unites the 4 Field-Remediated Infrastructure Subsystems cleanly beneath pluggable `TaskModule`s:
1. `SessionLifecycleManager`: Shields tasks when `DISCONNECTED / AT_LOGIN / LOGGING_IN` & self-heals with backoff.
2. `ReactiveSafetyWatchdog`: Non-blocking survival monitor (zero `time.sleep`, pipelined Eat + Run + Move).
3. `NavigationEngine`: Capability-constrained safe-corridor router (blocks Agility-21 `(3149,3480)` trap on Lvl-3s).
4. `TaskModule` (`osrs_ee/bots/`): Swappable declarative task logic completely decoupled from safety & transport.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

from osrs_ee.bots.base import DirectiveType, TaskDirective, TaskModule, TickContext
from osrs_ee.core.lifecycle_manager import ClientLifecycleState, SessionLifecycleManager
from osrs_ee.core.reactive_watchdog import ReactiveSafetyWatchdog, SafetyDecision, SafetyVerdict
from osrs_ee.navigation.capabilities import AccountCapabilitiesContext
from osrs_ee.navigation.waypoint_graph import NavigationEngine, WorldPoint
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.state.fsm import AccountStatus, ReasonCode

if TYPE_CHECKING:
    from osrs_ee.telemetry.smart_notifier import SmartEventNotifier


def _coalesce_int(val: Any, fallback: int) -> int:
    """Null-safe integer normalizer preventing TypeError when live client omits or nulls a field."""
    if isinstance(val, bool):
        return fallback
    if isinstance(val, (int, float)):
        return int(val)
    return fallback


@dataclass(frozen=True)
class KernelTickResult:
    lifecycle_state: ClientLifecycleState
    safety_verdict: SafetyVerdict | None
    task_directive: TaskDirective | None
    dispatched_commands: tuple[str, ...]
    halted: bool
    detail: str


class ExecutionEngineKernel:
    """Hardened core runtime executing one non-blocking tick at a time for any pluggable `TaskModule`."""

    def __init__(
        self,
        *,
        gate: GameAdapterGate,
        account_id: str,
        session_id: str,
        lease_id: str,
        session_epoch: int,
        capabilities: AccountCapabilitiesContext,
        lifecycle: SessionLifecycleManager | None = None,
        watchdog: ReactiveSafetyWatchdog | None = None,
        navigation: NavigationEngine | None = None,
        notifier: SmartEventNotifier | None = None,
        slot_id: int = 0,
        adapter_port: int | None = None,
    ) -> None:
        self.gate = gate
        self.account_id = account_id
        self.session_id = session_id
        self.lease_id = lease_id
        self.session_epoch = session_epoch
        self.capabilities = capabilities
        self.lifecycle = lifecycle or SessionLifecycleManager()
        self.watchdog = watchdog or ReactiveSafetyWatchdog()
        self.navigation = navigation or NavigationEngine()
        self.notifier = notifier
        self.slot_id = int(slot_id)
        self.adapter_port = adapter_port if adapter_port is not None else getattr(gate.adapter, "port", None)
        self._last_seen_tick: int | None = None
        self._internal_tick_counter: int = 0
        self._session_run_bootstrapped: bool = False
        # `PR-19.2` travel run-assist: a throttled SIDE-CHANNEL toggle that never replaces a walk.
        self.travel_run_assist_min_energy: int = 20
        self.travel_run_assist_cooldown_ticks: int = 10
        self._last_run_assist_tick: int = -(10**6)
        self._last_run_toggle_tick: int = -(10**6)
        self._persisted_bank_summary: tuple[dict[str, Any], ...] | None = None
        self._persisted_bank_scanned_at: str = ""
        self._active_task_id: str = ""
        self._last_known_bank_items: tuple[dict[str, Any], ...] = ()
        self._last_persisted_qp: int | None = None

    def emit_session_started(self, *, task_id: str) -> None:
        """Emit single deduplicated `🚀 بدء جلسة حساب` notification via `BoundedOutbox`."""
        self._active_task_id = task_id
        if self.notifier is not None:
            self.notifier.notify_session_started(
                account_id=self.account_id,
                session_id=self.session_id,
                task_id=task_id,
                slot_id=self.slot_id,
                port=self.adapter_port,
            )

    def notify_emergency_abort(
        self,
        *,
        reason_code: str = "ABORT_TASK",
        detail: str = "Operator or supervisor emergency abort",
    ) -> None:
        """Emit deduplicated `🚨 تنبيه طوارئ P0` for `ABORT_TASK` or `PROXY_UNAVAILABLE`."""
        if self.notifier is not None:
            self.notifier.notify_p0_incident(
                account_id=self.account_id,
                session_id=self.session_id,
                reason_code=reason_code,
                task_id=self._active_task_id,
                slot_id=self.slot_id,
                port=self.adapter_port,
                detail=detail,
            )

    def _summarize_wealth_and_loot(
        self, inv_items: list[dict[str, Any]]
    ) -> tuple[int, tuple[str, ...]]:
        """Summarize total banked + inventory `Coins (995)` and non-coin loot for `COMPLETE_TASK`."""
        bank_coins = 0
        inv_coins = 0
        loot_totals: dict[str, int] = {}

        for b_it in self._last_known_bank_items:
            iid = b_it.get("item_id", b_it.get("id"))
            nm = str(b_it.get("name") or "").strip()
            qty = max(0, _coalesce_int(b_it.get("quantity"), 0))
            if qty <= 0:
                continue
            if (iid == 995 or nm.casefold() == "coins") and not bool(b_it.get("noted", False)):
                bank_coins += qty
            elif nm:
                loot_totals[nm] = loot_totals.get(nm, 0) + qty

        for i_it in inv_items:
            iid = i_it.get("item_id", i_it.get("id"))
            nm = str(i_it.get("name") or "").strip()
            qty = max(0, _coalesce_int(i_it.get("quantity"), 0))
            if qty <= 0:
                continue
            if (iid == 995 or nm.casefold() == "coins") and not bool(i_it.get("noted", False)):
                inv_coins += qty
            elif nm:
                loot_totals[nm] = loot_totals.get(nm, 0) + qty

        loot_labels = tuple(f"{name} x{cnt}" for name, cnt in list(loot_totals.items())[:8])
        return (bank_coins + inv_coins, loot_labels)

    def _make_intent(
        self,
        command_type: str,
        payload: dict[str, Any],
        *,
        idempotency_class: str = "IDEMPOTENT",
        precondition: str = "in_game",
        expected_postcondition: str = "verified_on_next_tick",
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "command_id": f"kcmd_{uuid.uuid4().hex[:12]}",
            "session_id": self.session_id,
            "account_id": self.account_id,
            "client_instance_id": self.gate.adapter.client_instance_id,
            "lease_id": self.lease_id,
            "session_epoch": self.session_epoch,
            "base_state_version": 1,
            "command_type": command_type,
            "idempotency_class": idempotency_class,
            "payload": payload,
            "precondition": precondition,
            "expected_postcondition": expected_postcondition,
            "timeout_ms": 4000,
        }

    def step_tick(
        self,
        task_module: TaskModule,
        *,
        now_monotonic: float,
        dialogue_question: str = "",
        dialogue_options: tuple[str, ...] = (),
        varbits: Mapping[int, int] | None = None,
        varps: Mapping[int, int] | None = None,
    ) -> KernelTickResult:
        """Execute a single non-blocking engine cycle (ZERO `time.sleep()`)."""
        dispatched: list[str] = []
        self.emit_session_started(task_id=task_module.module_id)

        def _dispatch_relogin() -> None:
            intent = self._make_intent(
                "TRIGGER_LOGIN",
                {},
                idempotency_class="IDEMPOTENT",
                precondition="login_state == LOGIN_SCREEN",
                expected_postcondition="login_state == LOGGED_IN",
            )
            self.gate.dispatch_verified_command(
                intent=intent,
                postcondition_checker=lambda post: post["login_state"] == "LOGGED_IN",
            )
            dispatched.append("TRIGGER_LOGIN")

        # 1. Central Session Lifecycle Gate (isolates tasks when DISCONNECTED / AT_LOGIN / LOGGING_IN!)
        lc = self.lifecycle.evaluate_session_health(
            adapter=self.gate.adapter,
            now_monotonic=now_monotonic,
            relogin_dispatcher=_dispatch_relogin,
        )
        if not lc.task_execution_allowed:
            halted = lc.state == ClientLifecycleState.TERMINATED
            if halted:
                self.gate.store.close_session(
                    account_id=self.account_id,
                    session_id=self.session_id,
                    lease_id=self.lease_id,
                    session_epoch=self.session_epoch,
                    target_status=AccountStatus.RECOVERY_REQUIRED,
                    reason_code=ReasonCode.CRASH_UNCLOSED_SESSION,
                )
                if self.notifier is not None:
                    self.notifier.notify_p0_incident(
                        account_id=self.account_id,
                        session_id=self.session_id,
                        reason_code=ReasonCode.PROXY_UNAVAILABLE.value,
                        task_id=task_module.module_id,
                        slot_id=self.slot_id,
                        port=self.adapter_port,
                        detail=lc.detail,
                    )
            return KernelTickResult(
                lifecycle_state=lc.state,
                safety_verdict=None,
                task_directive=None,
                dispatched_commands=tuple(dispatched),
                halted=halted,
                detail=lc.detail,
            )

        # 2. Read single coalesced tick observation
        obs = self.gate.read_verified_observation(
            account_id=self.account_id,
            session_id=self.session_id,
            lease_id=self.lease_id,
            session_epoch=self.session_epoch,
            monotonic_ms=int(now_monotonic * 1000),
        )
        if obs["login_state"] != "LOGGED_IN" or obs["position"]["quality"] != "KNOWN":
            return KernelTickResult(
                lifecycle_state=ClientLifecycleState.AT_LOGIN,
                safety_verdict=None,
                task_directive=None,
                dispatched_commands=tuple(dispatched),
                halted=False,
                detail="Observation transitioned out of LOGGED_IN mid-poll; deferring cleanly",
            )

        # PR-15 Req #3: Upstream StateHandler.java omits `gameTick` (`obs["game_tick"] is None`).
        # Maintain a monotonic internal tick counter so `tick_advanced`, stall recovery, and 3-tick
        # food cooldowns ALWAYS advance cleanly on live Microbot 8081!
        raw_tick = obs.get("game_tick")
        if isinstance(raw_tick, int) and raw_tick > 0:
            curr_tick = raw_tick
            if self._internal_tick_counter < curr_tick:
                self._internal_tick_counter = curr_tick
        else:
            self._internal_tick_counter += 1
            curr_tick = self._internal_tick_counter

        tick_advanced = self._last_seen_tick is None or curr_tick > self._last_seen_tick
        self._last_seen_tick = curr_tick

        pos_tuple = (
            int(obs["position"]["x"]),
            int(obs["position"]["y"]),
            int(obs["position"]["plane"]),
        )
        default_hp = self.capabilities.skill_level("hitpoints")
        raw_vitals = obs.get("vitals") or {}
        hp_max_val = max(1, _coalesce_int(raw_vitals.get("hp_max"), default_hp))
        hp_curr_val = _coalesce_int(raw_vitals.get("hp_current"), hp_max_val)
        run_energy_val = _coalesce_int(raw_vitals.get("run_energy"), 100)
        run_enabled_val = bool(raw_vitals.get("run_enabled", False))
        raw_run_energy = raw_vitals.get("run_energy")
        run_energy_opt = int(raw_run_energy) if isinstance(raw_run_energy, (int, float)) else None
        under_attack_val = bool(raw_vitals.get("under_attack", False))
        inv_items = obs["inventory"]["items"] or []

        # 3. Non-Blocking Reactive Safety Watchdog (pipelined Eat + Run without stopping movement!)
        safety: SafetyDecision = self.watchdog.evaluate_pulse(
            game_tick=curr_tick,
            hp_current=hp_curr_val,
            hp_max=hp_max_val,
            run_energy=run_energy_val,
            run_enabled=run_enabled_val,
            under_attack=under_attack_val,
            position=pos_tuple,
            inventory_items=inv_items,
        )
        live_lc_state = self.lifecycle.update_danger_state(in_danger=safety.in_danger)

        if safety.halt_session:
            reason = (
                ReasonCode.DEATH_QUARANTINE
                if safety.verdict == SafetyVerdict.PLAYER_DEAD_QUARANTINE
                else ReasonCode.CAPABILITY_UNKNOWN
            )
            self.gate.store.close_session(
                account_id=self.account_id,
                session_id=self.session_id,
                lease_id=self.lease_id,
                session_epoch=self.session_epoch,
                target_status=AccountStatus.BLOCKED,
                reason_code=reason,
            )
            if self.notifier is not None:
                self.notifier.notify_p0_incident(
                    account_id=self.account_id,
                    session_id=self.session_id,
                    reason_code=reason.value,
                    task_id=task_module.module_id,
                    slot_id=self.slot_id,
                    port=self.adapter_port,
                    detail=safety.detail,
                )
            return KernelTickResult(
                lifecycle_state=live_lc_state,
                safety_verdict=safety.verdict,
                task_directive=None,
                dispatched_commands=tuple(dispatched),
                halted=True,
                detail=safety.detail,
            )

        # Pipeline survival actions IMMEDIATELY without blocking or dropping movement!
        if safety.eat_food_item_id is not None:
            self.gate.dispatch_pipelined_command(
                intent=self._make_intent(
                    "CONSUME_FOOD",
                    {"item_id": safety.eat_food_item_id},
                    idempotency_class="IDEMPOTENT",
                )
            )
            dispatched.append("CONSUME_FOOD")

        # PR-15 Req #1: Auto-enable Run on session start, underground Floor 4, or when Watchdog requests it
        in_sepulchre_floor_4 = 2300 <= pos_tuple[0] <= 2380 and pos_tuple[1] >= 5180
        force_boot_run = (not self._session_run_bootstrapped or in_sepulchre_floor_4) and (not run_enabled_val) and (run_energy_val >= 5)
        # `PR-19.2`: a client that lags in reporting `runEnabled` must never be spammed with toggles.
        # Throttle the boot/watchdog path to the same cooldown, EXCEPT when actually threatened
        # (in_danger), where an immediate re-assert is intentional.
        _toggle_ready = (curr_tick - self._last_run_toggle_tick) >= self.travel_run_assist_cooldown_ticks
        if (safety.enable_run or force_boot_run) and (_toggle_ready or safety.in_danger):
            self._session_run_bootstrapped = True
            self._last_run_toggle_tick = curr_tick
            self.gate.dispatch_pipelined_command(
                intent=self._make_intent(
                    "SET_RUN_MODE",
                    {"enabled": True},
                    idempotency_class="IDEMPOTENT",
                )
            )
            dispatched.append("SET_RUN_MODE")
        elif run_enabled_val:
            self._session_run_bootstrapped = True

        # 4. Evaluate pure pluggable TaskModule (enriching dialogue, varbits, varps & metrics from live adapter)
        live_dlg = getattr(self.gate.adapter, "last_dialogue_snapshot", {}) or {}
        eff_q = dialogue_question or str(live_dlg.get("question") or live_dlg.get("dialogueText") or "")
        eff_opts = dialogue_options or tuple(live_dlg.get("options") or ())

        resolved_varbits: dict[int, int] = {}
        if task_module.watched_varbits and hasattr(self.gate.adapter, "read_varbits"):
            resolved_varbits.update(self.gate.adapter.read_varbits(task_module.watched_varbits))
        if varbits:
            resolved_varbits.update({int(k): int(v) for k, v in varbits.items()})

        resolved_varps: dict[int, int] = {}
        if task_module.watched_varps and hasattr(self.gate.adapter, "read_varps"):
            resolved_varps.update(self.gate.adapter.read_varps(task_module.watched_varps))
        if varps:
            resolved_varps.update({int(k): int(v) for k, v in varps.items()})

        bank_obs = obs.get("bank") or {}
        bank_is_open = bool(bank_obs.get("is_open", False))
        bank_items_tuple = tuple(bank_obs.get("items") or ())
        if bank_is_open and isinstance(bank_obs.get("items"), list):
            self._last_known_bank_items = tuple(dict(it) for it in bank_obs["items"] if isinstance(it, dict))

        qp_obs = obs.get("quest_points") or {}
        if qp_obs.get("quality") == "KNOWN" and isinstance(qp_obs.get("value"), int):
            live_qp_val = int(qp_obs["value"])
            if live_qp_val != self.capabilities.quest_points:
                from dataclasses import replace as _dc_replace

                self.capabilities = _dc_replace(self.capabilities, quest_points=live_qp_val)
            if live_qp_val != self._last_persisted_qp:
                try:
                    self.gate.store.apply_observation_snapshot(
                        event_id=f"qp_sync_{self.session_id}_{obs['observation_id']}",
                        observation=obs,
                    )
                    self._last_persisted_qp = live_qp_val
                except Exception:
                    pass

        ctx = TickContext(
            game_tick=curr_tick,
            position=WorldPoint(*pos_tuple),
            capabilities=self.capabilities,
            inventory_items=tuple(inv_items),
            dialogue_open=bool(obs["dialogue_open"]),
            dialogue_question=eff_q,
            dialogue_options=eff_opts,
            varbits=resolved_varbits,
            varps=resolved_varps,
            tutorial_varp_281=_coalesce_int((obs.get("tutorial_varp_281") or {}).get("value"), 0),
            quest_points=_coalesce_int(qp_obs.get("value"), self.capabilities.quest_points),
            total_level=_coalesce_int((obs.get("total_level") or {}).get("value"), 32),
            playtime_minutes=_coalesce_int((obs.get("playtime_minutes") or {}).get("value"), 0),
            bank_is_open=bank_is_open,
            bank_items=bank_items_tuple,
            in_danger=safety.in_danger,
            run_energy_pct=run_energy_opt,
            run_enabled=run_enabled_val,
            bank_summary=self._persisted_bank_snapshot(),
            bank_summary_scanned_at=self._persisted_bank_scanned_at,
        )
        directive = task_module.evaluate_tick(ctx)

        # 5. Execute declarative TaskDirective through Core Engines
        if directive.directive_type == DirectiveType.NAVIGATE_TO and directive.destination is not None:
            walk_payload = self.navigation.step_navigation_on_tick(
                current_pos=ctx.position,
                tick_advanced=tick_advanced,
                capabilities=self.capabilities,
                destination=directive.destination,
            )
            if walk_payload is not None:
                self.gate.dispatch_pipelined_command(
                    intent=self._make_intent(
                        "WALK_TO_WORLD_POINT",
                        walk_payload,
                        idempotency_class="IDEMPOTENT",
                    )
                )
                dispatched.append("WALK_TO_WORLD_POINT")
                # `PR-19.2`: run is enabled as a Pipelined side command in the SAME tick as the walk.
                # It must never replace movement (that livelocked the bot in the field), and it is
                # throttled so a client that lags in reporting `runEnabled` cannot cause command spam.
                if self._maybe_dispatch_travel_run_assist(
                    curr_tick=curr_tick,
                    run_energy=run_energy_opt,
                    run_enabled=run_enabled_val,
                    dispatched=dispatched,
                ):
                    pass

        elif directive.directive_type == DirectiveType.AUTHORIZE_SURFACE_EXIT:
            self.watchdog.authorize_surface_exit()
            self.gate.dispatch_verified_command(
                intent=self._make_intent("INTERACT_OBJECT", dict(directive.payload)),
                postcondition_checker=lambda _post: True,
            )
            dispatched.append("INTERACT_OBJECT")

        elif directive.directive_type == DirectiveType.INTERACT_OBJECT:
            # When IN_DANGER, preserve evasion/movement priority unless already eating & moving!
            self.gate.dispatch_verified_command(
                intent=self._make_intent("INTERACT_OBJECT", dict(directive.payload)),
                postcondition_checker=lambda _post: True,
            )
            dispatched.append("INTERACT_OBJECT")

        elif directive.directive_type == DirectiveType.INTERACT_NPC:
            self.gate.dispatch_verified_command(
                intent=self._make_intent("INTERACT_NPC", dict(directive.payload)),
                postcondition_checker=lambda _post: True,
            )
            dispatched.append("INTERACT_NPC")

        elif directive.directive_type == DirectiveType.USE_ITEM_ON_ITEM:
            self.gate.dispatch_verified_command(
                intent=self._make_intent(
                    "USE_ITEM_ON_ITEM",
                    dict(directive.payload),
                    idempotency_class="NON_IDEMPOTENT",
                ),
                postcondition_checker=lambda _post: True,
            )
            dispatched.append("USE_ITEM_ON_ITEM")

        elif directive.directive_type == DirectiveType.SET_RUN_MODE:
            self.gate.dispatch_pipelined_command(
                intent=self._make_intent(
                    "SET_RUN_MODE",
                    {"enabled": bool(dict(directive.payload).get("enabled", True))},
                    idempotency_class="IDEMPOTENT",
                )
            )
            dispatched.append("SET_RUN_MODE")

        elif directive.directive_type == DirectiveType.TAKE_GROUND_ITEM:
            self.gate.dispatch_verified_command(
                intent=self._make_intent(
                    "TAKE_GROUND_ITEM",
                    dict(directive.payload),
                    idempotency_class="NON_IDEMPOTENT",
                ),
                postcondition_checker=lambda _post: True,
            )
            dispatched.append("TAKE_GROUND_ITEM")

        elif directive.directive_type == DirectiveType.CLICK_WIDGET:
            self.gate.dispatch_verified_command(
                intent=self._make_intent("WIDGET_CLICK", dict(directive.payload)),
                postcondition_checker=lambda _post: True,
            )
            dispatched.append("WIDGET_CLICK")

        elif directive.directive_type == DirectiveType.BANK_TURNAROUND:
            turn_outcome = self._execute_nonblocking_bank_turnaround(
                obs=obs,
                inv_items=inv_items,
                payload=dict(directive.payload),
                dispatched=dispatched,
            )
            if turn_outcome is not None:
                if self.notifier is not None:
                    p0_reason = (
                        ReasonCode.BANK_PIN_REQUIRED.value
                        if turn_outcome == "BANK_PIN_REQUIRED"
                        else ReasonCode.INSUFFICIENT_RESOURCES.value
                    )
                    self.notifier.notify_p0_incident(
                        account_id=self.account_id,
                        session_id=self.session_id,
                        reason_code=p0_reason,
                        task_id=task_module.module_id,
                        slot_id=self.slot_id,
                        port=self.adapter_port,
                        detail=turn_outcome,
                    )
                return KernelTickResult(
                    lifecycle_state=live_lc_state,
                    safety_verdict=safety.verdict,
                    task_directive=directive,
                    dispatched_commands=tuple(dispatched),
                    halted=True,
                    detail=turn_outcome,
                )

        elif directive.directive_type == DirectiveType.SELECT_DIALOGUE:
            self.gate.dispatch_verified_command(
                intent=self._make_intent("DIALOGUE_SELECT", dict(directive.payload)),
                postcondition_checker=lambda _post: True,
            )
            dispatched.append("DIALOGUE_SELECT")

        elif directive.directive_type == DirectiveType.CONTINUE_DIALOGUE:
            self.gate.dispatch_pipelined_command(
                intent=self._make_intent("DIALOGUE_CONTINUE", {})
            )
            dispatched.append("DIALOGUE_CONTINUE")

        elif directive.directive_type == DirectiveType.ESCALATE_HUMAN:
            self.gate.store.close_session(
                account_id=self.account_id,
                session_id=self.session_id,
                lease_id=self.lease_id,
                session_epoch=self.session_epoch,
                target_status=AccountStatus.BLOCKED,
                reason_code=ReasonCode.HUMAN_AUTH_REQUIRED,
            )
            if self.notifier is not None:
                self.notifier.notify_p0_incident(
                    account_id=self.account_id,
                    session_id=self.session_id,
                    reason_code=ReasonCode.HUMAN_AUTH_REQUIRED.value,
                    task_id=task_module.module_id,
                    slot_id=self.slot_id,
                    port=self.adapter_port,
                    detail=directive.reason,
                )
            return KernelTickResult(
                lifecycle_state=live_lc_state,
                safety_verdict=safety.verdict,
                task_directive=directive,
                dispatched_commands=tuple(dispatched),
                halted=True,
                detail=directive.reason,
            )

        elif directive.directive_type == DirectiveType.COMPLETE_TASK:
            coins_gp, loot_labels = self._summarize_wealth_and_loot(inv_items)
            session_closed = False
            try:
                cur_acc = self.gate.store.get_account(self.account_id)
                cur_st = AccountStatus(cur_acc["status"])
                if cur_st in (AccountStatus.RUNNING, AccountStatus.STARTING, AccountStatus.LEASED):
                    self.gate.store.close_session(
                        account_id=self.account_id,
                        session_id=self.session_id,
                        lease_id=self.lease_id,
                        session_epoch=self.session_epoch,
                        target_status=AccountStatus.RESTING,
                        reason_code=ReasonCode.NONE,
                    )
                    session_closed = True
                elif cur_st == AccountStatus.DISCOVERING:
                    self.gate.store.close_session(
                        account_id=self.account_id,
                        session_id=self.session_id,
                        lease_id=self.lease_id,
                        session_epoch=self.session_epoch,
                        target_status=AccountStatus.READY,
                        reason_code=ReasonCode.NONE,
                    )
                    session_closed = True
            except Exception:
                session_closed = False

            if self.notifier is not None:
                self.notifier.notify_task_completed(
                    account_id=self.account_id,
                    session_id=self.session_id,
                    task_id=task_module.module_id,
                    slot_id=self.slot_id,
                    port=self.adapter_port,
                    coins_gp=coins_gp,
                    loot_summary=loot_labels,
                    session_closed=session_closed,
                    detail=directive.reason,
                )

        return KernelTickResult(
            lifecycle_state=live_lc_state,
            safety_verdict=safety.verdict,
            task_directive=directive,
            dispatched_commands=tuple(dispatched),
            halted=(directive.directive_type == DirectiveType.COMPLETE_TASK),
            detail=directive.reason or safety.detail,
        )

    def _persisted_bank_snapshot(self) -> tuple[dict[str, Any], ...]:
        """Cached `state.db` bank summary (loaded at most once per session)."""
        if self._persisted_bank_summary is not None:
            return self._persisted_bank_summary
        summary: tuple[dict[str, Any], ...] = ()
        try:
            profile = self.gate.store.get_account_profile(self.account_id)
            if isinstance(profile, dict):
                raw = profile.get("bank_summary") or []
                summary = tuple(dict(it) for it in raw if isinstance(it, (dict, Mapping)))
                self._persisted_bank_scanned_at = str(profile.get("scanned_at") or "")
        except Exception:  # a missing profile must never break a tick
            summary = ()
        self._persisted_bank_summary = summary
        return summary

    def _maybe_dispatch_travel_run_assist(
        self,
        *,
        curr_tick: int,
        run_energy: int | None,
        run_enabled: bool,
        dispatched: list[str],
    ) -> bool:
        """Enable run while travelling, without ever displacing the walk command.

        Returns `True` when a `SET_RUN_MODE` side command was dispatched this tick. Silent when:
        the adapter did not report run energy, run is already on, the bar is below the threshold, or
        the cooldown has not elapsed (a lagging client must not be spammed).
        """
        if run_energy is None or run_enabled:
            return False
        # Never double-toggle in one tick: the watchdog/boot-run pulse already covered this tick.
        if "SET_RUN_MODE" in dispatched:
            self._last_run_assist_tick = curr_tick
            return False
        if run_energy < self.travel_run_assist_min_energy:
            return False
        if curr_tick - self._last_run_assist_tick < self.travel_run_assist_cooldown_ticks:
            return False
        self._last_run_assist_tick = curr_tick
        self.gate.dispatch_pipelined_command(
            intent=self._make_intent(
                "SET_RUN_MODE",
                {"enabled": True},
                idempotency_class="IDEMPOTENT",
            )
        )
        dispatched.append("SET_RUN_MODE")
        return True

    def _execute_nonblocking_bank_turnaround(
        self,
        *,
        obs: dict[str, Any],
        inv_items: list[dict[str, Any]],
        payload: dict[str, Any],
        dispatched: list[str],
    ) -> str | None:
        """Execute one non-blocking step of a bank turnaround per tick (ZERO `time.sleep`).

        Returns an escalation reason string ONLY if halted (`BANK_PIN_REQUIRED` or `INSUFFICIENT_RESOURCES`),
        otherwise returns `None` after advancing one verified bank step.
        """
        bank = obs.get("bank") or {}
        if not bank.get("is_open", False):
            self.gate.dispatch_verified_command(
                intent=self._make_intent("BANK_OPEN", {}),
                postcondition_checker=lambda post: bool(post["bank"]["is_open"]),
            )
            dispatched.append("BANK_OPEN")
            return None

        if bank.get("pin_prompt_open", False):
            self.gate.store.close_session(
                account_id=self.account_id,
                session_id=self.session_id,
                lease_id=self.lease_id,
                session_epoch=self.session_epoch,
                target_status=AccountStatus.BLOCKED,
                reason_code=ReasonCode.BANK_PIN_REQUIRED,
            )
            return "BANK_PIN_REQUIRED"

        # Allow a TaskModule to open the bank on Tick N and inspect live `ctx.bank_items` on Tick N+1!
        if payload.get("open_only", False):
            return None

        withdraw_specs: list[dict[str, Any]] = list(payload.get("withdraw") or [])
        required_ids = {int(w["item_id"]) for w in withdraw_specs if "item_id" in w}
        required_names = {str(w["name"]).strip().casefold() for w in withdraw_specs if "name" in w}

        # Deposit inventory if requested and non-required items are still in inventory
        if payload.get("deposit_inventory", True):
            has_depositable = any(
                int(it.get("item_id", -1)) not in required_ids
                and str(it.get("name", "")).strip().casefold() not in required_names
                for it in inv_items
            )
            if has_depositable:
                def _verify_deposit_and_cache_bank(post: dict[str, Any]) -> bool:
                    post_bank = post.get("bank") or {}
                    if post_bank.get("is_open") and isinstance(post_bank.get("items"), list):
                        self._last_known_bank_items = tuple(
                            dict(it) for it in post_bank["items"] if isinstance(it, dict)
                        )
                    return len(post["inventory"]["items"] or []) == 0

                self.gate.dispatch_verified_command(
                    intent=self._make_intent(
                        "BANK_DEPOSIT_INVENTORY",
                        {},
                        idempotency_class="IDEMPOTENT",
                    ),
                    postcondition_checker=_verify_deposit_and_cache_bank,
                )
                dispatched.append("BANK_DEPOSIT_INVENTORY")
                return None

        # Withdraw next missing required item (1 item per tick, verified postcondition!)
        for req in withdraw_specs:
            req_qty = int(req.get("quantity", 1))
            req_noted = bool(req.get("noted", False))
            req_id = int(req["item_id"]) if "item_id" in req else None
            req_name = str(req["name"]).strip() if "name" in req else None

            inv_qty = sum(
                int(it.get("quantity", 0))
                for it in inv_items
                if bool(it.get("noted", False)) == req_noted
                and (
                    (req_id is not None and int(it.get("item_id", -1)) == req_id)
                    or (req_name is not None and str(it.get("name", "")).strip().casefold() == req_name.casefold())
                )
            )
            if inv_qty < req_qty:
                needed = req_qty - inv_qty
                w_payload: dict[str, Any] = {"quantity": needed, "noted": req_noted}
                if req_id is not None:
                    w_payload["item_id"] = req_id
                if req_name is not None:
                    w_payload["name"] = req_name

                res = self.gate.dispatch_verified_command(
                    intent=self._make_intent(
                        "BANK_WITHDRAW_ITEM",
                        w_payload,
                        idempotency_class="NON_IDEMPOTENT",
                    ),
                    postcondition_checker=lambda post, rid=req_id, rnm=req_name, tgt=req_qty, ntd=req_noted: sum(
                        int(i.get("quantity", 0))
                        for i in (post["inventory"]["items"] or [])
                        if bool(i.get("noted", False)) == ntd
                        and (
                            (rid is not None and int(i.get("item_id", -1)) == rid)
                            or (rnm is not None and str(i.get("name", "")).strip().casefold() == rnm.casefold())
                        )
                    ) >= tgt,
                )
                dispatched.append("BANK_WITHDRAW_ITEM")
                if res["effect_status"] != "VERIFIED":
                    if payload.get("allow_best_effort", False):
                        return None
                    self.gate.store.close_session(
                        account_id=self.account_id,
                        session_id=self.session_id,
                        lease_id=self.lease_id,
                        session_epoch=self.session_epoch,
                        target_status=AccountStatus.BLOCKED,
                        reason_code=ReasonCode.INSUFFICIENT_RESOURCES,
                    )
                    return f"INSUFFICIENT_RESOURCES:{req_name or req_id}"
                return None

        # All deposits & withdrawals satisfied -> close bank cleanly!
        if payload.get("close_when_done", True):
            self.gate.dispatch_verified_command(
                intent=self._make_intent("BANK_CLOSE", {}),
                postcondition_checker=lambda post: not bool(post["bank"]["is_open"]),
            )
            dispatched.append("BANK_CLOSE")
        return None
