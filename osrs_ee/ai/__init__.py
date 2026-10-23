"""Deterministic local dialogue solver and isolated AI Advisory Client (PR-06, PR-08 / ADR-002)."""

from osrs_ee.ai.advisory_client import AIAdvisoryClient
from osrs_ee.ai.dialogue_solver import DeterministicStrongholdSolver

__all__ = ["AIAdvisoryClient", "DeterministicStrongholdSolver"]
