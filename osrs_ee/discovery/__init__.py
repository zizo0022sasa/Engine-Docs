"""Universal Account Discovery Scanner & Any-Level Profiling (`PR-17`)."""

from osrs_ee.discovery.scanner import (
    CANONICAL_OSRS_SKILLS,
    MAX_DISCOVERY_BUDGET_SEC,
    AccountProfileSnapshot,
    UniversalAccountScanner,
    compute_any_level_eligible_recipes,
    compute_osrs_combat_level,
)

__all__ = [
    "AccountProfileSnapshot",
    "CANONICAL_OSRS_SKILLS",
    "MAX_DISCOVERY_BUDGET_SEC",
    "UniversalAccountScanner",
    "compute_any_level_eligible_recipes",
    "compute_osrs_combat_level",
]
