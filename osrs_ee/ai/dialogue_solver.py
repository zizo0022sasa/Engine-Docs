"""Zero-LLM deterministic solver for Stronghold of Security doors (PR-06 + PR-13 / ADR-002).

Combines:
1. Tier 1: Verbatim 39-entry OSRS Stronghold Security Cache (`LOCAL_SECURITY_CACHE`, field-verified)
   with exact normalized text matching and `"please wait"` / `"click here to continue"` option filtering.
2. Tier 2: Canonical 12-Principle keyword rules (`_STRONGHOLD_RULES`) + safe fallbacks.
3. Fail-Closed Escalation: Never guesses randomly when no rule matches (`matched=False`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

IGNORED_DIALOGUE_OPTIONS: frozenset[str] = frozenset({"please wait", "click here to continue"})

# Verbatim field-verified Stronghold of Security question-fragment -> exact safe option text
LOCAL_SECURITY_CACHE: dict[str, str] = {
    "randomly selected for a prize": "No way! I'm reporting you to Jagex!",
    "logged in while i'm out of the room": "No.",
    "remove a hijacker": "Use the Account Recovery System.",
    "make you a player moderator": "Don't give them the information and send an 'Abuse report'.",
    "watching a stream": "Report the stream as a scam. Real Jagex streams have a 'verified' mark.",
    "do a difficult quest": "Don't give them my password.",
    "who can i give my password": "Nobody.",
    "how do i set up two-factor": "Through account settings on oldschool.runescape.com.",
    "example of a good bank pin": "The birthday of a famous person or event.",
    "check your stats": "Don't give out your password to anyone. Not even close friends.",
    "comment on the forum post": "Report the player for phishing.",
    "invitation via in- game chat or email": "Report the incident and do not click any links.",
    "how do i set a bank pin": "Talk to any banker.",
    "moderator asks me for my account details": "Politely tell them no, then use the 'Report Abuse' button.",
    "after you answer the question": "Read the text and follow the advice given.",
    "prevent me from saying my pin": "No.",
    "that sound?": "No way! You'll just take my gold for your own! Reported!",
    "verify my identity": "Delete it - it's a fake!",
    "website claims": "Nothing, it's a fake.",
    "react?": "Don't share your information and report the player.",
    "make you a member for free": "Don't tell them anything and click the 'Report Abuse' button.",
    "okay to buy an old school": "No, you should never buy an account.",
    "invitation via social media": "Report the incident and do not click any links.",
    "where is it safe": "Only on the Old School RuneScape website.",
    "best way to secure your account": "Two-factor authentication on your account and your registered email.",
    "share my account with": "Nobody.",
    "purchase items and/or gold": "Do not visit the website and report the player who messaged you.",
    "whose responsibility": "Me.",
    "someone to level your account": "No, you should never allow anyone to use your account.",
    "account is compromised": "Secure my device and reset my password.",
    "respond?": "Decline the offer and report that player.",
    "password backwards": "Don't type in my password backwards and report the player.",
    "keylogger or virus": "Virus scan my device then change my password.",
    "registered email secure": "Set up two-factor authentication with my email provider.",
    "characteristic of a secure password": "It's never reused on other websites or accounts.",
    "lose out! what do you say?": "Nope, you're tricking me into going somewhere dangerous.",
    "not through responding to emails.": "No.",
    "authenticator": "Through account settings",
    "phishing": "Report the player",
    "giveaway": "Report the incident and do not click any links.",
}


def _normalize_dialogue_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().casefold()).rstrip(".!?")


@dataclass(frozen=True)
class DialogueResolution:
    matched: bool
    option_index: int | None
    matched_text: str | None
    rule_id: str

    @property
    def selected_option(self) -> str | None:
        """Exact option text for option-text selection (`POST /dialogue/select`)."""
        return self.matched_text


# Ordered tuple of (question_keywords_any, safe_answer_keywords_any, rule_id)
_STRONGHOLD_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    (
        ("two-factor", "authenticator", "two step", "secure my account"),
        ("authenticator", "two-factor", "every device", "set up", "account settings"),
        "SH_AUTHENTICATOR",
    ),
    (
        ("share my account", "friend asks", "let my friend", "borrow", "level up my"),
        ("nobody", "never", "don't share", "do not share", "against the rules"),
        "SH_NO_ACCOUNT_SHARING",
    ),
    (
        ("moderator", "jmod", "jagex staff", "asks for your password"),
        ("never", "report", "politely decline", "politely tell them no", "don't give", "do not give"),
        "SH_FAKE_JMOD",
    ),
    (
        ("bank pin", "good bank pin", "choose a bank pin"),
        ("random", "not related", "nobody knows", "memorise", "memorize", "famous person", "any banker"),
        "SH_BANK_PIN",
    ),
    (
        ("website", "double xp", "stream", "giveaway", "forum link", "free membership"),
        ("phishing", "fake", "official website", "don't click", "do not enter", "report"),
        "SH_PHISHING_STREAM",
    ),
    (
        ("virus", "keylogger", "download", "cheat client", "auto typer"),
        ("virus scan", "never download", "don't download", "remove", "official"),
        "SH_MALWARE_GUARD",
    ),
    (
        ("recovery", "forgotten password", "hijacked"),
        ("official", "account recovery", "change password", "authenticator"),
        "SH_RECOVERY",
    ),
)

_GENERIC_SAFE_FALLBACKS: tuple[str, ...] = (
    "don't share",
    "do not share",
    "never give",
    "report the player",
    "politely decline",
    "use the authenticator",
    "nobody",
)


class DeterministicStrongholdSolver:
    """Selects both the exact option text and 0-based index for Stronghold of Security doors."""

    def solve(self, question_text: str, options: list[str]) -> DialogueResolution:
        q_raw = question_text or ""
        q_fold = q_raw.casefold()

        # Filter out transient wait prompts while keeping original 0-based index alignment
        indexed_valid: list[tuple[int, str]] = [
            (idx, opt.strip())
            for idx, opt in enumerate(options)
            if isinstance(opt, str) and _normalize_dialogue_text(opt) not in IGNORED_DIALOGUE_OPTIONS
        ]
        if not indexed_valid:
            return DialogueResolution(False, None, None, "EMPTY_OR_WAIT_PROMPT")

        # Tier 1: Exact normalized match against field-verified 39-entry Stronghold cache
        expected_norm = {
            _normalize_dialogue_text(ans)
            for q_frag, ans in LOCAL_SECURITY_CACHE.items()
            if q_frag.casefold() in q_fold
        }
        if expected_norm:
            exact_hits = [
                (idx, opt) for idx, opt in indexed_valid if _normalize_dialogue_text(opt) in expected_norm
            ]
            if len(exact_hits) == 1:
                hit_idx, hit_opt = exact_hits[0]
                return DialogueResolution(True, hit_idx, hit_opt, "SH_EXACT_CACHE")

        # Tier 2: Canonical 12-Principle keyword match
        q_low = q_raw.lower()
        for q_keys, ans_keys, rule_id in _STRONGHOLD_RULES:
            if any(qk in q_low for qk in q_keys):
                for idx, opt in indexed_valid:
                    opt_low = opt.lower()
                    if any(ak in opt_low for ak in ans_keys):
                        return DialogueResolution(True, idx, opt, rule_id)

        # Tier 3: Safe-answer heuristic across valid options
        for idx, opt in indexed_valid:
            opt_low = opt.lower()
            if any(fb in opt_low for fb in _GENERIC_SAFE_FALLBACKS):
                return DialogueResolution(True, idx, opt, "SH_GENERIC_SAFE")

        return DialogueResolution(False, None, None, "UNMATCHED_ESCALATE_ADVISORY")
