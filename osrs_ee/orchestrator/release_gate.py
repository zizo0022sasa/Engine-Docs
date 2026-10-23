"""7-State Microbot/Upstream Release Staging & Canary Gate with Auto-Rollback (PR-09 / AUD-20 / T15)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ReleaseStage(str, Enum):
    DETECTED = "DETECTED"
    STAGED = "STAGED"
    BUILT = "BUILT"
    CONTRACT_TESTED = "CONTRACT_TESTED"
    APPROVED = "APPROVED"
    CANARY = "CANARY"
    ACTIVE = "ACTIVE"
    ROLLED_BACK = "ROLLED_BACK"


class ReleaseGateError(RuntimeError):
    """Raised when an upstream release tries to bypass contract tests, approval, or canary."""


@dataclass
class ReleaseRecord:
    version_tag: str
    commit_sha: str
    jar_sha256: str | None = None
    stage: ReleaseStage = ReleaseStage.DETECTED
    approved_by_operator_id: int | None = None
    canary_account_id: str | None = None


class ReleaseStagingGate:
    """Prevents raw upstream git pulls from touching production accounts without Canary verification."""

    def __init__(self, *, initial_active_version: str | None = None) -> None:
        self._releases: dict[str, ReleaseRecord] = {}
        self.active_version: str | None = initial_active_version
        self._previous_active_version: str | None = None
        if initial_active_version is not None:
            self._releases[initial_active_version] = ReleaseRecord(
                version_tag=initial_active_version,
                commit_sha="baseline",
                jar_sha256="sha256:baseline",
                stage=ReleaseStage.ACTIVE,
            )

    def get_release(self, version_tag: str) -> ReleaseRecord:
        if version_tag not in self._releases:
            raise ReleaseGateError(f"Unknown release version: {version_tag!r}")
        return self._releases[version_tag]

    def register_detected(self, *, version_tag: str, commit_sha: str) -> ReleaseRecord:
        rec = ReleaseRecord(version_tag=version_tag, commit_sha=commit_sha, stage=ReleaseStage.DETECTED)
        self._releases[version_tag] = rec
        return rec

    def mark_staged(self, version_tag: str) -> ReleaseRecord:
        rec = self.get_release(version_tag)
        if rec.stage != ReleaseStage.DETECTED:
            raise ReleaseGateError(f"Expected DETECTED before STAGED, got {rec.stage.value}")
        rec.stage = ReleaseStage.STAGED
        return rec

    def mark_built(self, version_tag: str, *, jar_sha256: str) -> ReleaseRecord:
        rec = self.get_release(version_tag)
        if rec.stage != ReleaseStage.STAGED:
            raise ReleaseGateError(f"Expected STAGED before BUILT, got {rec.stage.value}")
        rec.jar_sha256 = jar_sha256
        rec.stage = ReleaseStage.BUILT
        return rec

    def mark_contract_tested(self, version_tag: str, *, passed: bool) -> ReleaseRecord:
        rec = self.get_release(version_tag)
        if rec.stage != ReleaseStage.BUILT:
            raise ReleaseGateError(f"Expected BUILT before CONTRACT_TESTED, got {rec.stage.value}")
        if not passed:
            rec.stage = ReleaseStage.ROLLED_BACK
            raise ReleaseGateError(f"Release {version_tag} failed contract tests and was quarantined")
        rec.stage = ReleaseStage.CONTRACT_TESTED
        return rec

    def approve_for_canary(self, version_tag: str, *, operator_id: int) -> ReleaseRecord:
        rec = self.get_release(version_tag)
        if rec.stage != ReleaseStage.CONTRACT_TESTED:
            raise ReleaseGateError(
                f"Cannot approve {version_tag}: contract tests have not passed (stage={rec.stage.value})"
            )
        rec.approved_by_operator_id = operator_id
        rec.stage = ReleaseStage.APPROVED
        return rec

    def start_canary(self, version_tag: str, *, canary_account_id: str) -> ReleaseRecord:
        rec = self.get_release(version_tag)
        if rec.stage != ReleaseStage.APPROVED:
            raise ReleaseGateError(
                f"Cannot launch CANARY for {version_tag}: requires APPROVED stage (got {rec.stage.value})"
            )
        rec.canary_account_id = canary_account_id
        rec.stage = ReleaseStage.CANARY
        return rec

    def complete_canary(self, version_tag: str, *, canary_succeeded: bool) -> ReleaseRecord:
        rec = self.get_release(version_tag)
        if rec.stage != ReleaseStage.CANARY:
            raise ReleaseGateError(f"Expected CANARY stage, got {rec.stage.value}")
        if not canary_succeeded:
            rec.stage = ReleaseStage.ROLLED_BACK
            # Active fleet remains safely on `self.active_version`!
            return rec

        self._previous_active_version = self.active_version
        self.active_version = version_tag
        rec.stage = ReleaseStage.ACTIVE
        return rec

    def rollback_active(self) -> str | None:
        if self.active_version is not None:
            self._releases[self.active_version].stage = ReleaseStage.ROLLED_BACK
        self.active_version = self._previous_active_version
        return self.active_version
