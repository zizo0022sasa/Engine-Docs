"""Dialogue text normalisation (`PR-21.6`).

The engine matches a live on-screen option against a wiki-verbatim string. That comparison is a
**string equality on human prose**, which fails on punctuation the wiki and the client disagree about:
Jagex writes `"Can you make dyes for me, please?"` while the transcript carried
`"Can you make dyes for me please?"`. `str.rstrip(".!?")` never touched the comma, so the option was
declared unrecognised and the bot **halted** — four separate times in one live session
(`UNRECOGNIZED_QUEST_DIALOGUE_OPTION` at ticks 1565, 133, 617 and 474), each one costing an operator
restart.

The fix is not "add the string that failed" — that repeats next quest. It is to compare the **words**,
not the typography:

    "Can you make dyes for me, please?"  ->  "can you make dyes for me please"
    "Can you make dyes for me please?"   ->  "can you make dyes for me please"

Anything that is not a letter or a digit becomes whitespace, then runs of whitespace collapse. That is
deliberately stricter than "fuzzy matching": two options that differ only in punctuation ARE the same
option, and anything that differs in words still fails loudly (never guess).
"""

from __future__ import annotations

import re

_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)


def normalise_option(text: object) -> str:
    """Return the comparison form of a dialogue option (punctuation-free, case-folded, single-spaced)."""
    if text is None:
        return ""
    return " ".join(_NON_WORD.sub(" ", str(text)).casefold().split())


def option_matches(offered: object, expected: object) -> bool:
    """True when the offered option and the expected one are the same WORDS, whatever the punctuation."""
    normalised = normalise_option(offered)
    return bool(normalised) and normalised == normalise_option(expected)
