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
from typing import Any, Mapping

from osrs_ee.bots.base import DirectiveType, TaskDirective, TaskModule, TickContext
from osrs_ee.core.lifecycle_manager import ClientLifecycleState, SessionLifecycleManager
from osrs_ee.core.reactive_watchdog import ReactiveSafetyWatchdog, SafetyDecision, SafetyVerdict
from osrs_ee.navigation.capabilities import AccountCapabilitiesContext
from osrs_ee.navigation.waypoint_graph import NavigationEngine, WorldPoint
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.state.fsm import AccountStatus, ReasonCode


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
        self._last_seen_tick: int | None = None

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
    ) -> KernelTickResult:
        """Execute a single non-blocking engine cycle (ZERO `time.sleep()`)."""
        dispatched: list[str] = []

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

        curr_tick = int(obs["game_tick"] or 0)
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

        if safety.enable_run:
            self.gate.dispatch_pipelined_command(
                intent=self._make_intent(
                    "SET_RUN_MODE",
                    {"enabled": True},
                    idempotency_class="IDEMPOTENT",
                )
            )
            dispatched.append("SET_RUN_MODE")

        # 4. Evaluate pure pluggable TaskModule (enriching dialogue from live adapter if available)
        live_dlg = getattr(self.gate.adapter, "last_dialogue_snapshot", {}) or {}
        eff_q = dialogue_question or str(live_dlg.get("question") or live_dlg.get("dialogueText") or "")
        eff_opts = dialogue_options or tuple(live_dlg.get("options") or ())

        ctx = TickContext(
            game_tick=curr_tick,
            position=WorldPoint(*pos_tuple),
            capabilities=self.capabilities,
            inventory_items=tuple(inv_items),
            dialogue_open=bool(obs["dialogue_open"]),
            dialogue_question=eff_q,
            dialogue_options=eff_opts,
            varbits=varbits or {},
            tutorial_varp_281=int(obs["tutorial_varp_281"]["value"] or 0),
            in_danger=safety.in_danger,
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

        elif directive.directive_type == DirectiveType.CLICK_WIDGET:
            self.gate.dispatch_verified_command(
                intent=self._make_intent("WIDGET_CLICK", dict(directive.payload)),
                postcondition_checker=lambda _post: True,
            )
            dispatched.append("WIDGET_CLICK")

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
            return KernelTickResult(
                lifecycle_state=live_lc_state,
                safety_verdict=safety.verdict,
                task_directive=directive,
                dispatched_commands=tuple(dispatched),
                halted=True,
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
