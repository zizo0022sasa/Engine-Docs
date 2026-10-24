"""Account Capabilities Context injected into pathfinding to prevent unusable shortcuts & lethal zones (ADR-010)."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class AccountCapabilitiesContext:
    """Immutable snapshot of an account's real skills, quests, membership, and combat readiness.

    Passed to `ConstrainedWaypointGraph` before EVERY route calculation so fresh Level-3 / Agility-1
    accounts never attempt Agility shortcuts (e.g., GE Underwall `(3149, 3480)` requiring Agility 21)
    or walk through lethal aggressive multi-aggro corridors (e.g., Varrock South Dark Wizards).
    """

    account_id: str
    skills: Mapping[str, int] = field(default_factory=dict)
    combat_level: int = 3
    quest_points: int = 0
    completed_quests: frozenset[str] = field(default_factory=frozenset)
    is_member: bool = False
    coins_gp: int = 0

    def __post_init__(self) -> None:
        normalized = {str(k).strip().lower(): max(1, int(v)) for k, v in self.skills.items()}
        if "hitpoints" not in normalized:
            normalized["hitpoints"] = 10
        object.__setattr__(self, "skills", MappingProxyType(normalized))
        object.__setattr__(
            self,
            "completed_quests",
            frozenset(str(q).strip().lower() for q in self.completed_quests),
        )

    def skill_level(self, skill_name: str) -> int:
        key = skill_name.strip().lower()
        return int(self.skills.get(key, 10 if key == "hitpoints" else 1))

    @property
    def agility_level(self) -> int:
        return self.skill_level("agility")

    def meets_requirements(
        self,
        *,
        min_skills: Mapping[str, int] | None = None,
        requires_member: bool = False,
        required_quests: frozenset[str] | set[str] | None = None,
        min_combat_level: int = 3,
        toll_coins_gp: int = 0,
        quest_waives_toll: str | None = None,
    ) -> tuple[bool, str]:
        if requires_member and not self.is_member:
            return (False, "MEMBERS_ONLY_SHORTCUT")
        if self.combat_level < min_combat_level:
            return (False, f"LETHAL_AGGRO_ZONE_MIN_COMBAT_{min_combat_level}")
        for sk, req_lvl in (min_skills or {}).items():
            actual = self.skill_level(sk)
            if actual < int(req_lvl):
                return (False, f"INSUFFICIENT_SKILL_{sk.upper()}_{actual}_LT_{req_lvl}")
        for q in required_quests or ():
            if q.strip().lower() not in self.completed_quests:
                return (False, f"MISSING_QUEST_{q.upper()}")
        if toll_coins_gp > 0:
            waived = bool(quest_waives_toll and quest_waives_toll.lower() in self.completed_quests)
            if not waived and self.coins_gp < toll_coins_gp:
                return (False, f"INSUFFICIENT_TOLL_GP_{self.coins_gp}_LT_{toll_coins_gp}")
        return (True, "ALLOWED")

    def to_walker_constraint_flags(self) -> dict[str, Any]:
        """Explicit constraint payload sent alongside `/walk` to disable client-side shortcut assumptions."""
        return {
            "allowShortcuts": bool(self.is_member and self.agility_level >= 21),
            "allowMembersTransports": bool(self.is_member),
            "allowWilderness": False,
            "allowTeleports": False,
            "agilityLevel": self.agility_level,
            "combatLevel": self.combat_level,
        }
