"""Universal Account Discovery Scanner — Any-Level Profiling (`<= 15s` budget) (PR-17 / T58, T59, T60).

Reads live from Microbot `AgentServerPlugin` (`127.0.0.1:<port>`) or `FakeGameAdapter`:
  1. All 23 canonical OSRS skill levels + XP (`GET /skills` -> `SkillsHandler.java`).
  2. Combat Level (`GET /state` `player.combatLevel` with exact Jagex formula fallback),
     Membership (`Varp 1780` + `GET /profiles`), Quest Points (`Varp 101`), Tutorial (`Varp 281`),
     Stronghold Floor claims (`Varbits 2309..2312`), and Playtime Minutes.
  3. Full Inventory + Bank audit of `Coins (995)`, Skilling Tools, Combat/Utility Gear, and Food.
  4. Dynamic Any-Level recipe eligibility matching (`CuratedRecipeRegistry`) and atomic SQLite
     persistence (`account_profiles` table in `state.db`).
"""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from osrs_ee.navigation.capabilities import AccountCapabilitiesContext
from osrs_ee.state.fsm import compute_progression_stage
from osrs_ee.tasks.catalog import CURATED_RECIPES, VERIFICATION_STAGE_ORDER

if TYPE_CHECKING:
    from osrs_ee.runtime.adapter_gate import GameAdapterGate
    from osrs_ee.state.store import StateStore

# Exact 23 canonical Old School RuneScape skills
CANONICAL_OSRS_SKILLS: tuple[str, ...] = (
    "attack",
    "defence",
    "strength",
    "hitpoints",
    "ranged",
    "prayer",
    "magic",
    "cooking",
    "woodcutting",
    "fletching",
    "fishing",
    "firemaking",
    "crafting",
    "smithing",
    "mining",
    "herblore",
    "agility",
    "thieving",
    "slayer",
    "farming",
    "runecraft",
    "hunter",
    "construction",
)
assert len(CANONICAL_OSRS_SKILLS) == 23

MAX_DISCOVERY_BUDGET_SEC: float = 15.0

_TOOL_KEYWORDS: tuple[str, ...] = (
    "axe",
    "pickaxe",
    "fishing net",
    "fishing rod",
    "fly fishing rod",
    "harpoon",
    "lobster pot",
    "feather",
    "fishing bait",
    "tinderbox",
    "knife",
    "hammer",
    "chisel",
    "shears",
    "needle",
    "thread",
    "spade",
    "secateurs",
    "rake",
    "seed dibber",
    "watering can",
    "pestle and mortar",
)

_GEAR_KEYWORDS: tuple[str, ...] = (
    "sword",
    "scimitar",
    "dagger",
    "mace",
    "longsword",
    "2h sword",
    "battleaxe",
    "warhammer",
    "shortbow",
    "longbow",
    "crossbow",
    "staff",
    "wand",
    "helm",
    "med helm",
    "full helm",
    "coif",
    "hat",
    "hood",
    "platebody",
    "chainbody",
    "leather body",
    "hardleather body",
    "d'hide body",
    "robe",
    "platelegs",
    "plateskirt",
    "chaps",
    "tassets",
    "shield",
    "sq shield",
    "kiteshield",
    "defender",
    "boots",
    "gloves",
    "vambraces",
    "gauntlets",
    "cape",
    "cloak",
    "amulet",
    "necklace",
    "ring",
    "bracelet",
)

_KNOWN_EDIBLE_FOOD: frozenset[str] = frozenset(
    {
        "shrimps",
        "anchovies",
        "cooked meat",
        "cooked chicken",
        "bread",
        "sardine",
        "herring",
        "mackerel",
        "trout",
        "cod",
        "pike",
        "salmon",
        "tuna",
        "cake",
        "2/3 cake",
        "slice of cake",
        "chocolate cake",
        "2/3 chocolate cake",
        "chocolate slice",
        "jug of wine",
        "lobster",
        "bass",
        "swordfish",
        "monkfish",
        "karambwan",
        "cooked karambwan",
        "shark",
        "sea turtle",
        "manta ray",
        "anglerfish",
    }
)


def compute_osrs_combat_level(skills: Mapping[str, int]) -> int:
    """Compute official Old School RuneScape Combat Level (`3..126`) from skill levels."""
    norm = {str(k).strip().lower(): int(v) for k, v in skills.items()}
    atk = max(1, norm.get("attack", 1))
    strength = max(1, norm.get("strength", 1))
    defence = max(1, norm.get("defence", 1))
    hp = max(10, norm.get("hitpoints", 10))
    prayer = max(1, norm.get("prayer", 1))
    ranged = max(1, norm.get("ranged", 1))
    magic = max(1, norm.get("magic", 1))

    base = 0.25 * (defence + hp + math.floor(prayer / 2))
    melee = 0.325 * (atk + strength)
    rng_style = 0.325 * math.floor(ranged * 1.5)
    mage_style = 0.325 * math.floor(magic * 1.5)
    return max(3, int(math.floor(base + max(melee, rng_style, mage_style))))


def _is_coins_item(item: Mapping[str, Any]) -> bool:
    if bool(item.get("noted", False)):
        return False
    iid = item.get("item_id", item.get("id"))
    if isinstance(iid, int) and iid == 995:
        return True
    return str(item.get("name", "")).strip().casefold() == "coins"


def _classify_item_name(raw_name: str) -> tuple[bool, bool, bool]:
    """Return `(is_tool, is_gear, is_food)` for an OSRS item display name."""
    low = raw_name.strip().casefold()
    if not low or low == "coins":
        return (False, False, False)

    is_food = low in _KNOWN_EDIBLE_FOOD
    # Battleaxes are weapons (gear), whereas woodcutting axes/pickaxes and magic runes ("* rune") are tools
    is_battleaxe = "battleaxe" in low
    is_magic_rune = low.endswith(" rune")
    is_tool = (not is_battleaxe) and (
        is_magic_rune
        or any(low == kw or low.endswith(f" {kw}") or kw in low for kw in _TOOL_KEYWORDS)
    )
    is_gear = (not is_tool) and any(kw in low for kw in _GEAR_KEYWORDS)
    return (is_tool, is_gear, is_food)


def _normalize_item_entry(raw: Mapping[str, Any]) -> dict[str, Any]:
    iid = raw.get("item_id", raw.get("id", -1))
    try:
        norm_id = int(iid) if iid is not None else -1
    except (TypeError, ValueError):
        norm_id = -1
    qty_raw = raw.get("quantity", 0)
    try:
        qty = max(0, int(qty_raw))
    except (TypeError, ValueError):
        qty = 0
    return {
        "item_id": norm_id,
        "name": str(raw.get("name") or "").strip(),
        "quantity": qty,
        "noted": bool(raw.get("noted", False)),
    }


@dataclass(frozen=True)
class AccountProfileSnapshot:
    """Complete Any-Level profile snapshot persisted in `state.db` (`account_profiles`)."""

    account_id: str
    combat_level: int
    total_level: int
    skills: Mapping[str, int]
    skill_xp: Mapping[str, int]
    is_member: bool
    membership_days: int
    tutorial_varp_281: int
    quest_points: int
    completed_quests: tuple[str, ...]
    stronghold_floors_claimed: tuple[int, ...]
    playtime_minutes: int
    progression_stage: str
    trade_unlocked: bool
    position: Mapping[str, int | None]
    inventory_coins_gp: int
    bank_coins_gp: int
    total_liquid_coins_gp: int
    bank_scanned: bool
    available_tools: tuple[str, ...]
    available_gear: tuple[str, ...]
    available_food: Mapping[str, int]
    inventory_summary: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    bank_summary: tuple[Mapping[str, Any], ...] = field(default_factory=tuple)
    eligible_recipes: tuple[str, ...] = field(default_factory=tuple)
    scan_duration_ms: int = 0
    scanned_at: str = ""

    def __post_init__(self) -> None:
        norm_skills: dict[str, int] = {}
        for sk in CANONICAL_OSRS_SKILLS:
            default_lvl = 10 if sk == "hitpoints" else 1
            norm_skills[sk] = max(1, int(self.skills.get(sk, default_lvl)))
        for extra_k, extra_v in self.skills.items():
            lk = str(extra_k).strip().lower()
            if lk not in norm_skills:
                norm_skills[lk] = max(1, int(extra_v))

        norm_xp: dict[str, int] = {}
        for sk in CANONICAL_OSRS_SKILLS:
            norm_xp[sk] = max(0, int(self.skill_xp.get(sk, 0)))
        for extra_k, extra_v in self.skill_xp.items():
            lk = str(extra_k).strip().lower()
            if lk not in norm_xp:
                norm_xp[lk] = max(0, int(extra_v))

        object.__setattr__(self, "skills", MappingProxyType(norm_skills))
        object.__setattr__(self, "skill_xp", MappingProxyType(norm_xp))
        object.__setattr__(
            self,
            "available_food",
            MappingProxyType({str(k): int(v) for k, v in self.available_food.items()}),
        )
        object.__setattr__(
            self,
            "position",
            MappingProxyType(
                {
                    "x": self.position.get("x"),
                    "y": self.position.get("y"),
                    "plane": self.position.get("plane"),
                }
            ),
        )
        object.__setattr__(
            self,
            "inventory_summary",
            tuple(MappingProxyType(_normalize_item_entry(it)) for it in self.inventory_summary),
        )
        object.__setattr__(
            self,
            "bank_summary",
            tuple(MappingProxyType(_normalize_item_entry(it)) for it in self.bank_summary),
        )

    def to_capabilities_context(self) -> AccountCapabilitiesContext:
        """Convert this discovered profile directly into an `AccountCapabilitiesContext`."""
        return AccountCapabilitiesContext(
            account_id=self.account_id,
            skills=dict(self.skills),
            combat_level=self.combat_level,
            quest_points=self.quest_points,
            completed_quests=frozenset(self.completed_quests),
            is_member=self.is_member,
            coins_gp=self.total_liquid_coins_gp,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "combat_level": int(self.combat_level),
            "total_level": int(self.total_level),
            "skills": dict(self.skills),
            "skill_xp": dict(self.skill_xp),
            "is_member": bool(self.is_member),
            "membership_days": int(self.membership_days),
            "tutorial_varp_281": int(self.tutorial_varp_281),
            "quest_points": int(self.quest_points),
            "completed_quests": list(self.completed_quests),
            "stronghold_floors_claimed": list(self.stronghold_floors_claimed),
            "playtime_minutes": int(self.playtime_minutes),
            "progression_stage": self.progression_stage,
            "trade_unlocked": bool(self.trade_unlocked),
            "position": dict(self.position),
            "inventory_coins_gp": int(self.inventory_coins_gp),
            "bank_coins_gp": int(self.bank_coins_gp),
            "total_liquid_coins_gp": int(self.total_liquid_coins_gp),
            "bank_scanned": bool(self.bank_scanned),
            "available_tools": list(self.available_tools),
            "available_gear": list(self.available_gear),
            "available_food": dict(self.available_food),
            "inventory_summary": [dict(it) for it in self.inventory_summary],
            "bank_summary": [dict(it) for it in self.bank_summary],
            "eligible_recipes": list(self.eligible_recipes),
            "scan_duration_ms": int(self.scan_duration_ms),
            "scanned_at": self.scanned_at,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AccountProfileSnapshot:
        return cls(
            account_id=str(data["account_id"]),
            combat_level=int(data["combat_level"]),
            total_level=int(data["total_level"]),
            skills=dict(data.get("skills") or {}),
            skill_xp=dict(data.get("skill_xp") or {}),
            is_member=bool(data.get("is_member", False)),
            membership_days=int(data.get("membership_days", 0)),
            tutorial_varp_281=int(data.get("tutorial_varp_281", 0)),
            quest_points=int(data.get("quest_points", 0)),
            completed_quests=tuple(str(q) for q in (data.get("completed_quests") or ())),
            stronghold_floors_claimed=tuple(int(f) for f in (data.get("stronghold_floors_claimed") or ())),
            playtime_minutes=int(data.get("playtime_minutes", 0)),
            progression_stage=str(data.get("progression_stage", "STAGE_0_TUTORIAL")),
            trade_unlocked=bool(data.get("trade_unlocked", False)),
            position=dict(data.get("position") or {"x": None, "y": None, "plane": None}),
            inventory_coins_gp=int(data.get("inventory_coins_gp", 0)),
            bank_coins_gp=int(data.get("bank_coins_gp", 0)),
            total_liquid_coins_gp=int(data.get("total_liquid_coins_gp", 0)),
            bank_scanned=bool(data.get("bank_scanned", False)),
            available_tools=tuple(str(t) for t in (data.get("available_tools") or ())),
            available_gear=tuple(str(g) for g in (data.get("available_gear") or ())),
            available_food={str(k): int(v) for k, v in (data.get("available_food") or {}).items()},
            inventory_summary=tuple(dict(it) for it in (data.get("inventory_summary") or ())),
            bank_summary=tuple(dict(it) for it in (data.get("bank_summary") or ())),
            eligible_recipes=tuple(str(r) for r in (data.get("eligible_recipes") or ())),
            scan_duration_ms=int(data.get("scan_duration_ms", 0)),
            scanned_at=str(data.get("scanned_at") or ""),
        )


def compute_any_level_eligible_recipes(
    *,
    skills: Mapping[str, int],
    tutorial_varp_281: int,
    stronghold_floors_claimed: tuple[int, ...],
    available_tools: tuple[str, ...],
) -> tuple[str, ...]:
    """Match any account (`Level 3 .. 126`) to all currently executable curated recipes."""
    min_rank = VERIFICATION_STAGE_ORDER["HERMETIC_TESTED"]
    tool_set_lower = {t.strip().casefold() for t in available_tools}
    matched: list[str] = []

    for recipe in CURATED_RECIPES:
        if VERIFICATION_STAGE_ORDER[recipe["verification_stage"]] < min_rank:
            continue
        rid = str(recipe["recipe_id"])
        if rid == "stage0_tutorial_island" and tutorial_varp_281 >= 1000:
            continue
        if rid == "stage2_stronghold_10k" and set(stronghold_floors_claimed) >= {1, 2, 3, 4}:
            continue
        req_281 = recipe["required_varps"].get("281")
        if req_281 is not None and tutorial_varp_281 < int(req_281):
            continue
        if any(int(skills.get(sk.lower(), 1)) < int(req_lvl) for sk, req_lvl in recipe["min_skills"].items()):
            continue
        tool_cands = recipe.get("tool_candidates") or []
        if tool_cands and tool_set_lower:
            # Prefer recipes whose tools exist in bank/inventory, or keep eligible if starter tool can be bought/fetched
            has_tool = any(str(tc).strip().casefold() in tool_set_lower for tc in tool_cands)
            if not has_tool and rid != "stage1b_seasoning_gatherer":
                continue
        matched.append(rid)

    return tuple(matched)


class UniversalAccountScanner:
    """Fast (`<= 15s` hard wall-clock budget) Any-Level Account Profile Scanner."""

    def __init__(self, *, max_duration_sec: float = MAX_DISCOVERY_BUDGET_SEC) -> None:
        if max_duration_sec <= 0 or max_duration_sec > MAX_DISCOVERY_BUDGET_SEC:
            raise ValueError(
                f"max_duration_sec must be in (0, {MAX_DISCOVERY_BUDGET_SEC}], got {max_duration_sec}"
            )
        self.max_duration_sec = float(max_duration_sec)

    def scan(
        self,
        *,
        account_id: str,
        adapter: Any,
        store: StateStore | None = None,
        gate: GameAdapterGate | None = None,
        lease: Mapping[str, Any] | None = None,
        open_bank_if_closed: bool = True,
        read_only: bool = False,
        now_utc: datetime | None = None,
    ) -> AccountProfileSnapshot:
        """Scan all 23 skills, progression varps/varbits, combat level, and bank/inventory wealth."""
        t0 = time.monotonic()
        deadline = t0 + self.max_duration_sec
        scanned_dt = now_utc or datetime.now(timezone.utc)
        scanned_iso = scanned_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        if hasattr(adapter, "invalidate_tick_cache"):
            adapter.invalidate_tick_cache()

        obs = adapter.read_observation()
        if obs.get("login_state") != "LOGGED_IN":
            raise RuntimeError(
                f"Cannot scan account {account_id}: client login_state is {obs.get('login_state')!r} (expected LOGGED_IN)"
            )

        # 1. Read all 23 canonical OSRS skills (`GET /skills`)
        skills_map: dict[str, int] = {
            sk: (10 if sk == "hitpoints" else 1) for sk in CANONICAL_OSRS_SKILLS
        }
        skill_xp_map: dict[str, int] = {sk: 0 for sk in CANONICAL_OSRS_SKILLS}

        raw_skills_payload = (
            adapter.read_skills_snapshot()
            if hasattr(adapter, "read_skills_snapshot")
            else {"skills": [], "totalLevel": 0}
        )
        for entry in raw_skills_payload.get("skills") or []:
            if not isinstance(entry, dict):
                continue
            s_name = str(entry.get("name") or "").strip().lower()
            if not s_name:
                continue
            lvl = entry.get("level")
            xp = entry.get("xp", 0)
            if isinstance(lvl, (int, float)) and not isinstance(lvl, bool):
                skills_map[s_name] = max(1, int(lvl))
            if isinstance(xp, (int, float)) and not isinstance(xp, bool):
                skill_xp_map[s_name] = max(0, int(xp))

        total_level = sum(skills_map[sk] for sk in CANONICAL_OSRS_SKILLS)
        reported_total = raw_skills_payload.get("totalLevel")
        if isinstance(reported_total, int) and reported_total > total_level:
            total_level = reported_total

        # 2. Read Varps (`281` Tutorial, `101` Quest Points, `1780` Membership Days, F2P Quests) & Stronghold Varbits
        varps = (
            adapter.read_varps([281, 101, 1780, 29, 179, 144, 62])
            if hasattr(adapter, "read_varps")
            else {}
        )
        varbits = (
            adapter.read_varbits([2309, 2310, 2311, 2312])
            if hasattr(adapter, "read_varbits")
            else {}
        )
        hints = (
            adapter.read_client_profile_hints()
            if hasattr(adapter, "read_client_profile_hints")
            else {}
        )

        obs_varp_281 = (obs.get("tutorial_varp_281") or {}).get("value")
        tutorial_varp_281 = int(
            varps.get(281, obs_varp_281 if isinstance(obs_varp_281, int) else 0)
        )

        obs_qp = (obs.get("quest_points") or {}).get("value")
        varp_qp = varps.get(101)
        quest_points = max(
            int(varp_qp) if isinstance(varp_qp, int) else 0,
            int(obs_qp) if isinstance(obs_qp, int) else 0,
        )

        membership_days = max(0, int(varps.get(1780, 0)))
        is_member = bool(membership_days > 0 or hints.get("profile_is_member") is True)

        hint_cmb = hints.get("combat_level")
        combat_level = (
            int(hint_cmb)
            if isinstance(hint_cmb, int) and hint_cmb >= 3
            else compute_osrs_combat_level(skills_map)
        )

        completed_quests: list[str] = []
        if tutorial_varp_281 >= 1000:
            completed_quests.append("tutorial_island")
        if int(varps.get(29, 0)) >= 2:
            completed_quests.append("cooks_assistant")
        if int(varps.get(179, 0)) >= 21:
            completed_quests.append("sheep_shearer")
        if int(varps.get(144, 0)) >= 100:
            completed_quests.append("romeo_and_juliet")
        if int(varps.get(62, 0)) >= 6:
            completed_quests.append("goblin_diplomacy")

        stronghold_claimed = tuple(
            idx for idx, vid in enumerate((2309, 2310, 2311, 2312), start=1) if int(varbits.get(vid, 0)) == 1
        )

        # Playtime minutes: client observation / hints, or fallback to stored `accounts.playtime_minutes`
        obs_play = (obs.get("playtime_minutes") or {}).get("value")
        hint_play = hints.get("playtime_minutes")
        stored_play = 0
        prior_profile: dict[str, Any] | None = None
        if store is not None:
            try:
                acc_row = store.get_account(account_id)
                if acc_row.get("playtime_minutes") is not None:
                    stored_play = int(acc_row["playtime_minutes"])
            except KeyError:
                pass
            prior_profile = store.get_account_profile(account_id)

        if isinstance(obs_play, int):
            playtime_minutes = max(0, obs_play)
        elif isinstance(hint_play, int):
            playtime_minutes = max(0, hint_play)
        else:
            playtime_minutes = max(0, stored_play)

        # 3. Audit Inventory + Bank within strict `<= 15s` budget
        inv_raw = list((obs.get("inventory") or {}).get("items") or [])
        inv_items = [_normalize_item_entry(it) for it in inv_raw if isinstance(it, dict)]

        bank_obs = obs.get("bank") or {}
        bank_scanned = False
        bank_items: list[dict[str, Any]] = []

        if bool(bank_obs.get("is_open")) and isinstance(bank_obs.get("items"), list):
            bank_scanned = True
            bank_items = [_normalize_item_entry(it) for it in bank_obs["items"] if isinstance(it, dict)]
        elif (
            open_bank_if_closed
            and not read_only
            and not bool(bank_obs.get("pin_prompt_open"))
            and (deadline - time.monotonic()) >= 0.8
        ):
            opened_by_scanner = self._try_open_nearby_bank(
                account_id=account_id,
                adapter=adapter,
                gate=gate,
                lease=lease,
                deadline=deadline,
            )
            if opened_by_scanner:
                try:
                    if hasattr(adapter, "invalidate_tick_cache"):
                        adapter.invalidate_tick_cache()
                    bank_after = (adapter.read_observation().get("bank") or {})
                    if bool(bank_after.get("is_open")) and isinstance(bank_after.get("items"), list):
                        bank_scanned = True
                        bank_items = [
                            _normalize_item_entry(it)
                            for it in bank_after["items"]
                            if isinstance(it, dict)
                        ]
                finally:
                    if (deadline - time.monotonic()) >= 0.2:
                        self._try_close_bank(
                            account_id=account_id,
                            adapter=adapter,
                            gate=gate,
                            lease=lease,
                        )

        if not bank_scanned and prior_profile is not None:
            # Preserve last known bank snapshot when scanning away from a bank booth
            bank_items = [
                _normalize_item_entry(it)
                for it in (prior_profile.get("bank_summary") or [])
                if isinstance(it, dict)
            ]

        inv_coins = sum(int(it["quantity"]) for it in inv_items if _is_coins_item(it))
        bank_coins = sum(int(it["quantity"]) for it in bank_items if _is_coins_item(it))
        total_coins = inv_coins + bank_coins

        tools_found: list[str] = []
        gear_found: list[str] = []
        food_counts: dict[str, int] = {}

        # Include worn equipment if exposed on adapter
        for eq_name in getattr(adapter, "equipment_items", ()) or ():
            clean_eq = str(eq_name).strip()
            if clean_eq and clean_eq not in gear_found:
                gear_found.append(clean_eq)

        for item in (*inv_items, *bank_items):
            nm = str(item.get("name") or "").strip()
            qty = int(item.get("quantity", 0))
            if not nm or qty <= 0:
                continue
            is_tool, is_gear, is_food = _classify_item_name(nm)
            if is_tool and nm not in tools_found:
                tools_found.append(nm)
            if is_gear and nm not in gear_found:
                gear_found.append(nm)
            if is_food and not bool(item.get("noted", False)):
                food_counts[nm] = food_counts.get(nm, 0) + qty

        stage, trade_unlocked = compute_progression_stage(
            tutorial_varp_281=tutorial_varp_281,
            quest_points=quest_points,
            total_level=total_level,
            playtime_minutes=playtime_minutes,
        )
        eligible = compute_any_level_eligible_recipes(
            skills=skills_map,
            tutorial_varp_281=tutorial_varp_281,
            stronghold_floors_claimed=stronghold_claimed,
            available_tools=tuple(tools_found),
        )

        pos_obs = obs.get("position") or {}
        duration_ms = max(1, int(round((time.monotonic() - t0) * 1000)))

        snapshot = AccountProfileSnapshot(
            account_id=account_id,
            combat_level=combat_level,
            total_level=total_level,
            skills=skills_map,
            skill_xp=skill_xp_map,
            is_member=is_member,
            membership_days=membership_days,
            tutorial_varp_281=tutorial_varp_281,
            quest_points=quest_points,
            completed_quests=tuple(completed_quests),
            stronghold_floors_claimed=stronghold_claimed,
            playtime_minutes=playtime_minutes,
            progression_stage=stage,
            trade_unlocked=trade_unlocked,
            position={
                "x": pos_obs.get("x"),
                "y": pos_obs.get("y"),
                "plane": pos_obs.get("plane"),
            },
            inventory_coins_gp=inv_coins,
            bank_coins_gp=bank_coins,
            total_liquid_coins_gp=total_coins,
            bank_scanned=bank_scanned,
            available_tools=tuple(tools_found),
            available_gear=tuple(gear_found),
            available_food=food_counts,
            inventory_summary=tuple(inv_items),
            bank_summary=tuple(bank_items),
            eligible_recipes=eligible,
            scan_duration_ms=duration_ms,
            scanned_at=scanned_iso,
        )

        if store is not None:
            store.save_account_profile(snapshot.to_dict(), now_utc=scanned_dt)

        return snapshot

    @staticmethod
    def _try_open_nearby_bank(
        *,
        account_id: str,
        adapter: Any,
        gate: GameAdapterGate | None,
        lease: Mapping[str, Any] | None,
        deadline: float,
    ) -> bool:
        try:
            if gate is not None and lease is not None:
                res = gate.dispatch_verified_command(
                    intent={
                        "schema_version": "1.0",
                        "command_id": f"scan_bank_open_{int(time.monotonic() * 1000)}",
                        "account_id": account_id,
                        "session_id": str(lease["session_id"]),
                        "lease_id": str(lease["lease_id"]),
                        "session_epoch": int(lease["session_epoch"]),
                        "client_instance_id": str(adapter.client_instance_id),
                        "base_state_version": 1,
                        "command_type": "BANK_OPEN",
                        "idempotency_class": "IDEMPOTENT",
                        "precondition": "in_game",
                        "expected_postcondition": "bank.is_open == True",
                        "timeout_ms": 2500,
                        "payload": {},
                    },
                    postcondition_checker=lambda post: bool((post.get("bank") or {}).get("is_open")),
                )
                if res.get("effect_status") == "VERIFIED":
                    return True
            else:
                raw_res = adapter.execute_raw_command("BANK_OPEN", {})
                if not bool(raw_res.get("ok")):
                    return False

            poll_limit = min(deadline, time.monotonic() + 2.5)
            while time.monotonic() < poll_limit:
                if hasattr(adapter, "invalidate_tick_cache"):
                    adapter.invalidate_tick_cache()
                obs_chk = adapter.read_observation()
                bank_chk = obs_chk.get("bank") or {}
                if bool(bank_chk.get("is_open")):
                    return True
                if bool(bank_chk.get("pin_prompt_open")):
                    return False
                time.sleep(0.15)
        except Exception:
            return False
        return False

    @staticmethod
    def _try_close_bank(
        *,
        account_id: str,
        adapter: Any,
        gate: GameAdapterGate | None,
        lease: Mapping[str, Any] | None,
    ) -> None:
        try:
            if gate is not None and lease is not None:
                gate.dispatch_verified_command(
                    intent={
                        "schema_version": "1.0",
                        "command_id": f"scan_bank_close_{int(time.monotonic() * 1000)}",
                        "account_id": account_id,
                        "session_id": str(lease["session_id"]),
                        "lease_id": str(lease["lease_id"]),
                        "session_epoch": int(lease["session_epoch"]),
                        "client_instance_id": str(adapter.client_instance_id),
                        "base_state_version": 1,
                        "command_type": "BANK_CLOSE",
                        "idempotency_class": "IDEMPOTENT",
                        "precondition": "bank.is_open == True",
                        "expected_postcondition": "bank.is_open == False",
                        "timeout_ms": 2000,
                        "payload": {},
                    },
                    postcondition_checker=lambda post: not bool((post.get("bank") or {}).get("is_open")),
                )
            else:
                adapter.execute_raw_command("BANK_CLOSE", {})
        except Exception:
            pass
