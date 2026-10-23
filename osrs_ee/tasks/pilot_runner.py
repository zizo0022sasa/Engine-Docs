"""Single-Slot Supervised Pilot Runner with strict non-zero exit semantics (PR-06 / AUD-35 / T27)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from osrs_ee.ai.dialogue_solver import DeterministicStrongholdSolver
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.supervisor import SingleSlotSupervisor
from osrs_ee.state.fsm import AccountStatus, ReasonCode
from osrs_ee.state.store import StateStore
from osrs_ee.tasks.catalog import CuratedRecipeRegistry


@dataclass(frozen=True)
class PilotRunResult:
    ok: bool
    exit_code: int
    account_id: str
    recipe_id: str
    final_account_status: str
    detail: str


class SingleSlotPilotRunner:
    """Executes an end-to-end supervised single-slot session cleanly."""

    def __init__(
        self,
        *,
        store: StateStore,
        supervisor: SingleSlotSupervisor | None = None,
        registry: CuratedRecipeRegistry | None = None,
    ) -> None:
        self.store = store
        self.supervisor = supervisor or SingleSlotSupervisor(store=store)
        self.registry = registry or CuratedRecipeRegistry()
        self.dialogue_solver = DeterministicStrongholdSolver()

    def run_session(
        self,
        *,
        account_id: str,
        recipe_id: str,
        adapter_factory: Callable[[dict[str, Any]], Any],
        account_skills: dict[str, int] | None = None,
        stronghold_dialogues: list[tuple[str, list[str]]] | None = None,
        now_utc: datetime | None = None,
    ) -> PilotRunResult:
        now_dt = now_utc or datetime.now(timezone.utc)
        # 1. Reconcile any crashed remnants on boot first
        self.store.reconcile_on_boot(now_utc=now_dt)

        try:
            recipe = self.registry.get(recipe_id, minimum_stage="HERMETIC_TESTED")
            acc = self.store.get_account(account_id)
            kind = "DISCOVERY" if acc["status"] == AccountStatus.PENDING_DISCOVERY.value else "WORK"

            lease = self.store.acquire_session_lease(
                account_id=account_id,
                slot_id=0,
                task_id=recipe_id,
                now_utc=now_dt,
                session_kind=kind,
            )
            adapter = adapter_factory(lease)
            gate = GameAdapterGate(self.store, adapter)
            worker = self.supervisor.attach_session(lease=lease, gate=gate)

            # 2. Always probe initial verified game state
            worker.run_discovery_probe()

            # 3. Provision bank equipment/tools if recipe requires bank turnaround
            if recipe["tool_candidates"] or recipe["clear_equipment"]:
                outcome = worker.execute_bank_turnaround(
                    recipe,
                    account_skills=account_skills or getattr(adapter, "skills", {}),
                )
                if outcome.status != "COMPLETED":
                    if self.supervisor.active_lease is not None:
                        self.supervisor.abort(now_utc=now_dt)
                    final_st = self.store.get_account(account_id)["status"]
                    return PilotRunResult(
                        ok=False,
                        exit_code=2,
                        account_id=account_id,
                        recipe_id=recipe_id,
                        final_account_status=final_st,
                        detail=f"Bank turnaround halted: {outcome.status} ({outcome.reason_code.value})",
                    )

            # 4. Solve Stronghold security questions deterministically if present
            for q_text, opts in stronghold_dialogues or []:
                res = self.dialogue_solver.solve(q_text, opts)
                if not res.matched:
                    self.store.close_session(
                        account_id=account_id,
                        session_id=lease["session_id"],
                        lease_id=lease["lease_id"],
                        session_epoch=lease["session_epoch"],
                        target_status=AccountStatus.BLOCKED,
                        reason_code=ReasonCode.HUMAN_AUTH_REQUIRED,
                        now_utc=now_dt,
                    )
                    self.supervisor._clear_slot()
                    return PilotRunResult(
                        ok=False,
                        exit_code=3,
                        account_id=account_id,
                        recipe_id=recipe_id,
                        final_account_status=AccountStatus.BLOCKED.value,
                        detail="Unrecognized security dialogue escalated for human review",
                    )

            # 5. Cooperative clean drain -> PLUGIN_STOP + SAFE_LOGOUT + kill JVM + RESTING cooldown
            drain_res = self.supervisor.drain(now_utc=now_dt)
            final_st = self.store.get_account(account_id)["status"]
            ok = drain_res == "DRAINED_CLEANLY"
            return PilotRunResult(
                ok=ok,
                exit_code=0 if ok else 4,
                account_id=account_id,
                recipe_id=recipe_id,
                final_account_status=final_st,
                detail=drain_res,
            )
        except Exception as exc:
            if self.supervisor.active_lease is not None:
                self.supervisor.abort(now_utc=now_dt)
            try:
                final_st = self.store.get_account(account_id)["status"]
            except Exception:
                final_st = "UNKNOWN"
            return PilotRunResult(
                ok=False,
                exit_code=1,
                account_id=account_id,
                recipe_id=recipe_id,
                final_account_status=final_st,
                detail=f"{type(exc).__name__}: {exc}",
            )
