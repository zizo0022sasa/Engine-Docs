"""Verified TaskWorker implementing discovery, bank turnaround, death quarantine, and replan (PR-04)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from osrs_ee.contracts.models import validate_task_recipe
from osrs_ee.runtime.adapter_gate import GameAdapterGate, PolicyDeniedError
from osrs_ee.state.fsm import AccountStatus, ReasonCode

# True OSRS Woodcutting/Mining tool skill thresholds (prevents pulling unusable Rune axe at lvl 15!)
TOOL_SKILL_REQUIREMENTS: dict[str, tuple[str, int]] = {
    "Rune axe": ("woodcutting", 41),
    "Adamant axe": ("woodcutting", 31),
    "Mithril axe": ("woodcutting", 21),
    "Steel axe": ("woodcutting", 6),
    "Iron axe": ("woodcutting", 1),
    "Bronze axe": ("woodcutting", 1),
    "Rune pickaxe": ("mining", 41),
    "Adamant pickaxe": ("mining", 31),
    "Mithril pickaxe": ("mining", 21),
    "Iron pickaxe": ("mining", 1),
    "Bronze pickaxe": ("mining", 1),
    "Fly fishing rod": ("fishing", 20),
    "Small fishing net": ("fishing", 1),
}


@dataclass(frozen=True)
class WorkerOutcome:
    status: str  # COMPLETED | REPLAN_REQUESTED | BLOCKED | UNKNOWN_HALT | PAUSED
    reason_code: ReasonCode
    detail: str
    selected_tool: str | None = None


class TaskWorker:
    """Executes a single leased task without self-switching tasks or bypassing GameAdapterGate."""

    def __init__(
        self,
        *,
        gate: GameAdapterGate,
        account_id: str,
        session_id: str,
        lease_id: str,
        session_epoch: int,
    ) -> None:
        self.gate = gate
        self.account_id = account_id
        self.session_id = session_id
        self.lease_id = lease_id
        self.session_epoch = session_epoch
        self.paused = False

    def _new_intent(
        self,
        command_type: str,
        payload: dict[str, Any],
        *,
        idempotency_class: str,
        precondition: str,
        expected_postcondition: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "command_id": f"cmd_{uuid.uuid4().hex[:12]}",
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
            "timeout_ms": 5000,
        }

    def check_death_guard(self) -> WorkerOutcome | None:
        """T23: Immediately halt and quarantine account if player died (no Lumbridge death loop!)."""
        if self.gate.adapter.player_dead:
            self.gate.store.close_session(
                account_id=self.account_id,
                session_id=self.session_id,
                lease_id=self.lease_id,
                session_epoch=self.session_epoch,
                target_status=AccountStatus.BLOCKED,
                reason_code=ReasonCode.DEATH_QUARANTINE,
            )
            return WorkerOutcome(
                status="BLOCKED",
                reason_code=ReasonCode.DEATH_QUARANTINE,
                detail="Player death detected; session halted under Post-Death Quarantine.",
            )
        return None

    def run_discovery_probe(self) -> dict[str, Any]:
        """T10: Event-driven discovery reading Varp 281 and keeping closed-bank items UNKNOWN."""
        obs = self.gate.read_verified_observation(
            account_id=self.account_id,
            session_id=self.session_id,
            lease_id=self.lease_id,
            session_epoch=self.session_epoch,
        )
        self.gate.store.apply_observation_snapshot(
            event_id=f"evt_disc_{obs['observation_id']}",
            observation=obs,
        )
        return obs

    def execute_bank_turnaround(
        self,
        recipe: dict[str, Any],
        *,
        account_skills: dict[str, int],
        minimum_recipe_stage: str = "HERMETIC_TESTED",
    ) -> WorkerOutcome:
        """T12, T14, T21: Verified bank reset and level-eligible tool provisioning."""
        validate_task_recipe(recipe, minimum_stage=minimum_recipe_stage)

        death_check = self.check_death_guard()
        if death_check is not None:
            return death_check
        if self.paused:
            return WorkerOutcome(status="PAUSED", reason_code=ReasonCode.NONE, detail="Worker paused")

        # 1. Read initial observation
        obs = self.gate.read_verified_observation(
            account_id=self.account_id,
            session_id=self.session_id,
            lease_id=self.lease_id,
            session_epoch=self.session_epoch,
        )

        # 2. Open bank if closed
        if not obs["bank"]["is_open"]:
            open_res = self.gate.dispatch_verified_command(
                intent=self._new_intent(
                    "BANK_OPEN",
                    {},
                    idempotency_class="IDEMPOTENT",
                    precondition="logged_in",
                    expected_postcondition="bank.is_open == true",
                ),
                postcondition_checker=lambda post: post["bank"]["is_open"] is True,
            )
            if open_res["effect_status"] == "UNKNOWN":
                return WorkerOutcome("UNKNOWN_HALT", ReasonCode.UNKNOWN_COMMAND_EFFECT, "BANK_OPEN unknown")
            if open_res["effect_status"] != "VERIFIED":
                return WorkerOutcome("BLOCKED", ReasonCode.CAPABILITY_UNKNOWN, "Could not open bank")
            obs = self.gate.read_verified_observation(
                account_id=self.account_id,
                session_id=self.session_id,
                lease_id=self.lease_id,
                session_epoch=self.session_epoch,
            )

        # 3. Check Bank PIN barrier immediately (never guess PIN or report empty bank!)
        if obs["bank"]["pin_prompt_open"]:
            self.gate.store.close_session(
                account_id=self.account_id,
                session_id=self.session_id,
                lease_id=self.lease_id,
                session_epoch=self.session_epoch,
                target_status=AccountStatus.BLOCKED,
                reason_code=ReasonCode.BANK_PIN_REQUIRED,
            )
            return WorkerOutcome("BLOCKED", ReasonCode.BANK_PIN_REQUIRED, "Bank PIN prompt active")

        # 4. Deposit inventory if non-empty
        if obs["inventory"]["items"]:
            dep_res = self.gate.dispatch_verified_command(
                intent=self._new_intent(
                    "BANK_DEPOSIT_INVENTORY",
                    {},
                    idempotency_class="IDEMPOTENT",
                    precondition="bank.is_open == true",
                    expected_postcondition="len(inventory.items) == 0",
                ),
                postcondition_checker=lambda post: post["inventory"]["items"] == [],
            )
            if dep_res["effect_status"] != "VERIFIED":
                return WorkerOutcome("BLOCKED", ReasonCode.CAPABILITY_UNKNOWN, "Inventory deposit failed")

        # 5. Deposit equipment if recipe demands clear_equipment=True (fixes old D1 omission!)
        if recipe["clear_equipment"] and self.gate.adapter.equipment_items:
            eq_res = self.gate.dispatch_verified_command(
                intent=self._new_intent(
                    "BANK_DEPOSIT_EQUIPMENT",
                    {},
                    idempotency_class="IDEMPOTENT",
                    precondition="bank.is_open == true",
                    expected_postcondition="equipment empty",
                ),
                postcondition_checker=lambda _post: len(self.gate.adapter.equipment_items) == 0,
            )
            if eq_res["effect_status"] != "VERIFIED":
                return WorkerOutcome("BLOCKED", ReasonCode.CAPABILITY_UNKNOWN, "Equipment deposit failed")

        # Refresh open bank snapshot after deposits
        obs = self.gate.read_verified_observation(
            account_id=self.account_id,
            session_id=self.session_id,
            lease_id=self.lease_id,
            session_epoch=self.session_epoch,
        )
        bank_by_name = {it["name"]: it for it in (obs["bank"]["items"] or [])}

        # 6. Select highest tool candidate that exists in bank AND account meets skill level for
        chosen_tool: dict[str, Any] | None = None
        if recipe["tool_candidates"]:
            for tool_name in recipe["tool_candidates"]:
                req = TOOL_SKILL_REQUIREMENTS.get(tool_name)
                if req is not None:
                    skill_name, min_lvl = req
                    if account_skills.get(skill_name, 1) < min_lvl:
                        continue
                if tool_name in bank_by_name and bank_by_name[tool_name]["quantity"] >= 1:
                    chosen_tool = bank_by_name[tool_name]
                    break

            if chosen_tool is None:
                # T21: Worker MUST NOT switch tasks on its own; close bank and request REPLAN!
                self.gate.dispatch_verified_command(
                    intent=self._new_intent(
                        "BANK_CLOSE",
                        {},
                        idempotency_class="IDEMPOTENT",
                        precondition="bank.is_open == true",
                        expected_postcondition="bank.is_open == false",
                    ),
                    postcondition_checker=lambda post: post["bank"]["is_open"] is False,
                )
                return WorkerOutcome(
                    status="REPLAN_REQUESTED",
                    reason_code=ReasonCode.INSUFFICIENT_RESOURCES,
                    detail=f"No skill-eligible tool from {recipe['tool_candidates']} available in bank",
                )

            # 7. Withdraw chosen tool as un-noted item and verify post-inventory delta!
            withdraw_res = self.gate.dispatch_verified_command(
                intent=self._new_intent(
                    "BANK_WITHDRAW_ITEM",
                    {
                        "item_id": chosen_tool["item_id"],
                        "name": chosen_tool["name"],
                        "quantity": 1,
                        "noted": False,
                    },
                    idempotency_class="NON_IDEMPOTENT",
                    precondition="bank.is_open == true",
                    expected_postcondition=f"inventory contains {chosen_tool['name']} (noted=False)",
                ),
                postcondition_checker=lambda post: any(
                    it["name"] == chosen_tool["name"] and it["quantity"] >= 1 and not it["noted"]
                    for it in (post["inventory"]["items"] or [])
                ),
            )
            if withdraw_res["effect_status"] == "UNKNOWN":
                return WorkerOutcome(
                    "UNKNOWN_HALT",
                    ReasonCode.UNKNOWN_COMMAND_EFFECT,
                    "Withdraw outcome UNKNOWN due to transport drop; halting for reconciliation",
                )
            if withdraw_res["effect_status"] != "VERIFIED":
                return WorkerOutcome(
                    "BLOCKED",
                    ReasonCode.INSUFFICIENT_RESOURCES,
                    f"Withdraw postcondition failed: {withdraw_res['error_code']}",
                )

        # 8. Close bank cleanly
        close_res = self.gate.dispatch_verified_command(
            intent=self._new_intent(
                "BANK_CLOSE",
                {},
                idempotency_class="IDEMPOTENT",
                precondition="bank.is_open == true",
                expected_postcondition="bank.is_open == false",
            ),
            postcondition_checker=lambda post: post["bank"]["is_open"] is False,
        )
        if close_res["effect_status"] != "VERIFIED":
            return WorkerOutcome("BLOCKED", ReasonCode.CAPABILITY_UNKNOWN, "Failed to close bank")

        return WorkerOutcome(
            status="COMPLETED",
            reason_code=ReasonCode.NONE,
            detail="Bank turnaround verified",
            selected_tool=chosen_tool["name"] if chosen_tool else None,
        )

    def attempt_ge_liquidation(self) -> None:
        """T13: Grand Exchange auto-liquidation is strictly disabled in MVP per ADR-006."""
        raise PolicyDeniedError(
            "Grand Exchange auto-liquidation is disabled in MVP (ADR-006 / PR-10 gate)"
        )
