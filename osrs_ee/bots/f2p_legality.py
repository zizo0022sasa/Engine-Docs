"""`PR-21` — The F2P legality gate.

**Field incident #15.** Three PRs (`PR-20.7`, `PR-20.8`, `PR-20.9`) were spent teaching the bot to
pickpocket citizens for the last 30 GP of the Goblin Diplomacy dyes. They were well engineered — a
location fix, a progress watcher, a stun window — and completely worthless, because **Thieving is a
members-only skill in Old School RuneScape**. A free-to-play world refuses every attempt, the client
reports no XP, and the bot simply never earns a coin:

    GET /skills -> {"name": "Thieving", "level": 1, "xp": 0}

The engine cannot detect that from the wire: `POST /npcs/interact` answers `{"ok": true}` whenever the
call reaches the client, whether the game accepts the action or not. So the guard cannot live in the
engine's head — it has to live in the **bundle's contract**. This module is that contract.

Rule: a recipe whose id or name declares `f2p` may never emit a members-only action or depend on a
members-only skill. `test_pr21_f2p_members_gate_and_capital_protection.py` enforces it by scanning the
bots package for the forbidden verbs, so the next contributor cannot reintroduce the class by
accident.
"""

from __future__ import annotations

import re
from pathlib import Path

# OSRS Wiki, `Members` — "Out of the 24 skills in Old School RuneScape, nine of them are only
# available in members' worlds": Herblore, Hunter, Thieving, Slayer, Agility, Construction, Farming,
# Fletching, Runecraft? (the free list is the inverse). Keep this list to what an F2P quest bundle can
# plausibly reach for; the point is the gate, not an encyclopaedia.
MEMBERS_ONLY_SKILLS: frozenset[str] = frozenset(
    {
        "thieving",
        "agility",
        "herblore",
        "hunter",
        "slayer",
        "farming",
        "fletching",
        "construction",
    }
)

# Action verbs that belong to those skills and must never appear in an F2P bundle.
MEMBERS_ONLY_ACTIONS: frozenset[str] = frozenset(
    {
        "pickpocket",
        "steal-from",
        "steal from",
        "trap",
        "light trap",
        "lay trap",
        "fletch",
        "mix",
    }
)

# A deliberately generous net: any of these words in a *bots* module is a smell worth failing on.
_SUSPECT_WORDS = tuple(sorted(MEMBERS_ONLY_ACTIONS)) + tuple(sorted(MEMBERS_ONLY_SKILLS))


class MembersOnlyActionError(RuntimeError):
    """Raised when an F2P bundle tries to use a members-only capability."""


def assert_f2p_safe(action: str, *, where: str = "bundle") -> str:
    """Return `action` unchanged, or raise if it is members-only.

    Called on the boundary where a directive is built, so a mistake fails loudly at build time
    instead of silently doing nothing for eighty ticks on the live wire.
    """
    normalised = action.strip().casefold().replace("-", " ")
    if normalised in {a.replace("-", " ").casefold() for a in MEMBERS_ONLY_ACTIONS}:
        raise MembersOnlyActionError(
            f"{where}: {action!r} is a MEMBERS-ONLY action — free-to-play worlds refuse it and no "
            f"engine-side retry can change that (field incident #15)."
        )
    return action


def scan_module_source(path: Path) -> list[str]:
    """Return the members-only words found in `path` (empty list means clean).

    Used by `T157`. Scanning source text rather than runtime behaviour is the point: it catches the
    mistake at review time, long before an account is walked into a members-only dead end.
    """
    text = Path(path).read_text(encoding="utf-8")
    lowered = text.casefold()
    hits: list[str] = []
    for word in _SUSPECT_WORDS:
        # A word only reaches the wire as a STRING LITERAL (`"action": "Pickpocket"`, `{"thieving": 1}`).
        # Prose in a comment explaining WHY the skill is banned must not trip the guard — the comment is
        # the part that stops the next contributor from reintroducing it.
        if re.search(rf"""["']{re.escape(word)}["']""", lowered):
            hits.append(word)
    return sorted(set(hits))
