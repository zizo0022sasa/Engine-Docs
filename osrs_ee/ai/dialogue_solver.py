"""Zero-LLM deterministic solver for Stronghold of Security doors (PR-06 / ADR-002).

Eliminates LLM latency, token cost, and prompt-injection exposure on known
in-game security questions by matching canonical OSRS safe-option patterns.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DialogueResolution:
    matched: bool
    option_index: int | None
    matched_text: str | None
    rule_id: str


# Ordered tuple of (question_keywords_any, safe_answer_keywords_any, rule_id)
_STRONGHOLD_RULES: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...] = (
    (
        ("two-factor", "authenticator", "two step", "secure my account"),
        ("authenticator", "two-factor", "every device", "set up"),
        "SH_AUTHENTICATOR",
    ),
    (
        ("share my account", "friend asks", "let my friend", "borrow", "level up my"),
        ("nobody", "never", "don't share", "do not share", "against the rules"),
        "SH_NO_ACCOUNT_SHARING",
    ),
    (
        ("moderator", "jmod", "jagex staff", "asks for your password"),
        ("never", "report", "politely decline", "don't give", "do not give"),
        "SH_FAKE_JMOD",
    ),
    (
        ("bank pin", "good bank pin", "choose a bank pin"),
        ("random", "not related", "nobody knows", "memorise", "memorize"),
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
    """Selects the exact 0-based dialogue option index for Stronghold of Security doors."""

    def solve(self, question_text: str, options: list[str]) -> DialogueResolution:
        q_low = (question_text or "").lower()
        norm_opts = [opt.strip() for opt in options]

        for q_keys, ans_keys, rule_id in _STRONGHOLD_RULES:
            if any(qk in q_low for qk in q_keys):
                for idx, opt in enumerate(norm_opts):
                    opt_low = opt.lower()
                    if any(ak in opt_low for ak in ans_keys):
                        return DialogueResolution(True, idx, opt, rule_id)

        # Secondary safe-answer heuristic across options
        for idx, opt in enumerate(norm_opts):
            opt_low = opt.lower()
            if any(fb in opt_low for fb in _GENERIC_SAFE_FALLBACKS):
                return DialogueResolution(True, idx, opt, "SH_GENERIC_SAFE")

        return DialogueResolution(False, None, None, "UNMATCHED_ESCALATE_ADVISORY")
