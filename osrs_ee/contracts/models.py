"""Domain invariant validators layered on top of JSON Schemas (PR-02 / T05, T10, T14, T20)."""

from __future__ import annotations

from typing import Any

from osrs_ee.contracts.schema_validator import (
    SchemaValidationError,
    load_schema,
    validate_against_schema,
)

VERIFICATION_STAGE_ORDER: dict[str, int] = {
    "CONCEPT_ONLY": 0,
    "AVAILABLE_SOURCE": 1,
    "HERMETIC_TESTED": 2,
    "LIVE_VERIFIED": 3,
}

_FORBIDDEN_PROPOSAL_PATTERNS = (
    "click(",
    "confirm_warning",
    "drop_item",
    "wilderness",
    "os.system",
    "subprocess",
)


class ContractValidationError(ValueError):
    """Raised when structural schema or semantic domain invariants fail."""


def _run_schema(payload: dict[str, Any], schema_file: str) -> None:
    if not isinstance(payload, dict):
        raise ContractValidationError(f"Contract payload must be a dict, got {type(payload).__name__}")
    try:
        validate_against_schema(payload, load_schema(schema_file))
    except SchemaValidationError as exc:
        raise ContractValidationError(str(exc)) from exc


def validate_session_launch(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate SessionLaunch contract (no raw secrets, telemetry disabled, distinct ports)."""
    _run_schema(payload, "session_launch.schema.json")
    if payload["local_socks5_port"] == payload["adapter_port"]:
        raise ContractValidationError(
            "local_socks5_port and adapter_port must not collide on the same loopback port"
        )
    return payload


def _check_quality_field(obj: dict[str, Any], value_keys: tuple[str, ...], label: str) -> None:
    quality = obj["quality"]
    if quality == "UNKNOWN":
        for k in value_keys:
            if obj.get(k) is not None:
                raise ContractValidationError(
                    f"{label}: quality is UNKNOWN so '{k}' must be None (never 0 or synthetic)"
                )
    elif quality == "KNOWN":
        for k in value_keys:
            if obj.get(k) is None:
                raise ContractValidationError(f"{label}: quality is KNOWN so '{k}' cannot be None")


def validate_observation(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate Observation contract (T10: UNKNOWN -> None, closed bank -> UNKNOWN items)."""
    _run_schema(payload, "observation.schema.json")

    _check_quality_field(payload["position"], ("x", "y", "plane"), "position")
    _check_quality_field(payload["tutorial_varp_281"], ("value",), "tutorial_varp_281")
    _check_quality_field(payload["quest_points"], ("value",), "quest_points")
    _check_quality_field(payload["total_level"], ("value",), "total_level")
    _check_quality_field(payload["playtime_minutes"], ("value",), "playtime_minutes")

    bank = payload["bank"]
    if not bank["is_open"]:
        if bank["items_quality"] != "UNKNOWN" or bank["items"] is not None:
            raise ContractValidationError(
                "bank: when is_open is False, items_quality MUST be 'UNKNOWN' and items MUST be None"
            )
    else:
        if bank["items_quality"] == "KNOWN" and bank["items"] is None:
            raise ContractValidationError("bank: when items_quality is KNOWN, items list is required")
        if bank["items_quality"] == "UNKNOWN" and bank["items"] is not None:
            raise ContractValidationError("bank: when items_quality is UNKNOWN, items must be None")

    inv = payload["inventory"]
    if inv["quality"] == "UNKNOWN" and (inv["items"] is not None or inv["free_slots"] is not None):
        raise ContractValidationError("inventory: UNKNOWN quality requires items=None and free_slots=None")
    if inv["quality"] == "KNOWN" and (inv["items"] is None or inv["free_slots"] is None):
        raise ContractValidationError("inventory: KNOWN quality requires items list and free_slots")

    return payload


def validate_command_intent(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate CommandIntent contract, forbidding pixel-coordinate payloads."""
    _run_schema(payload, "command_intent.schema.json")
    cmd_payload = payload["payload"]
    forbidden_coord_keys = {"screen_x", "screen_y", "click_x", "click_y", "pixel_x", "pixel_y"}
    overlap = forbidden_coord_keys.intersection(cmd_payload.keys())
    if overlap:
        raise ContractValidationError(f"Raw pixel coordinates forbidden in CommandIntent: {sorted(overlap)}")
    return payload


def validate_command_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate CommandResult contract separating transport_status from effect_status."""
    _run_schema(payload, "command_result.schema.json")
    transport = payload["transport_status"]
    effect = payload["effect_status"]
    if effect == "VERIFIED" and transport != "HTTP_200":
        raise ContractValidationError("effect_status cannot be VERIFIED without HTTP_200 transport")
    if effect == "VERIFIED" and not payload.get("post_observation_id"):
        raise ContractValidationError("VERIFIED effect_status requires a post_observation_id proof")
    if transport == "NOT_SENT" and effect not in ("REJECTED", "FAILED_VERIFIED"):
        raise ContractValidationError("NOT_SENT transport must have REJECTED or FAILED_VERIFIED effect")
    return payload


def validate_ai_proposal(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate AIProposal contract under ADR-002 (MVP Advisory-Only, T05 anti-injection)."""
    _run_schema(payload, "ai_proposal.schema.json")
    rationale_lower = payload["rationale"].lower()
    for bad in _FORBIDDEN_PROPOSAL_PATTERNS:
        if bad in rationale_lower:
            raise ContractValidationError(
                f"AIProposal rationale contains forbidden unsafe directive pattern: {bad!r}"
            )
    if payload["recommended_disposition"] == "REQUEST_HUMAN_REVIEW" and not payload["requires_human_approval"]:
        raise ContractValidationError(
            "REQUEST_HUMAN_REVIEW disposition requires requires_human_approval=True"
        )
    return payload


def validate_task_recipe(
    payload: dict[str, Any],
    *,
    minimum_stage: str = "CONCEPT_ONLY",
) -> dict[str, Any]:
    """Validate TaskRecipe contract and enforce verification maturity gate (T14)."""
    _run_schema(payload, "task_recipe.schema.json")
    if minimum_stage not in VERIFICATION_STAGE_ORDER:
        raise ContractValidationError(f"Unknown minimum_stage: {minimum_stage!r}")
    actual_rank = VERIFICATION_STAGE_ORDER[payload["verification_stage"]]
    required_rank = VERIFICATION_STAGE_ORDER[minimum_stage]
    if actual_rank < required_rank:
        raise ContractValidationError(
            f"Recipe '{payload['recipe_id']}' is quarantined for execution: "
            f"stage={payload['verification_stage']} < required={minimum_stage}"
        )
    return payload
