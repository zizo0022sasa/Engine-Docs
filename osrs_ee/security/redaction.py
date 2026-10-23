"""Runtime and artifact redaction engine (PR-00 / F01, F02 / T01, T02).

Ensures passwords, tokens, proxy credentials, session IDs, and PII never
appear in stdout, logs, exceptions, Telegram messages, or AI payloads.
"""

from __future__ import annotations

import re
from typing import Any

# Canonical secret patterns (kept structural — no real secrets embedded)
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Telegram Bot Token: 8-11 digits : 35 base64url chars
    (re.compile(r"\b\d{8,11}:[A-Za-z0-9_-]{35}\b"), "<REDACTED_TELEGRAM_BOT_TOKEN>"),
    # GitHub Classic & Fine-Grained PATs
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,255}\b"), "<REDACTED_GITHUB_TOKEN>"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,255}\b"), "<REDACTED_GITHUB_TOKEN>"),
    # AI Provider Keys (OpenAI, Groq, Anthropic, Google Gemini, AWS AKIA)
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,255}\b"), "<REDACTED_AI_KEY>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,255}\b"), "<REDACTED_AI_KEY>"),
    (re.compile(r"\bgsk_[A-Za-z0-9_-]{20,255}\b"), "<REDACTED_AI_KEY>"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "<REDACTED_AI_KEY>"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "<REDACTED_AWS_KEY>"),
    # Proxy / URI embedded user:password@host
    (
        re.compile(r"((?:socks5h?|socks|https?)://)([^/\s:@]+:[^/\s@]+)@", re.IGNORECASE),
        r"\1<REDACTED_PROXY_AUTH>@",
    ),
    # Header / KV secrets (X-Agent-Token, JX_SESSION_ID, JX_ACCESS_TOKEN, password=...)
    (
        re.compile(
            r"(?i)\b(X-Agent-Token|JX_SESSION_ID|JX_ACCESS_TOKEN|JX_REFRESH_TOKEN|authToken|password|passwd|secret_token)"
            r"(\s*[:=]\s*['\"]?)([^'\"\s,;}{]+)"
        ),
        r"\1\2<REDACTED_SECRET>",
    ),
]

_EMAIL_PATTERN = re.compile(r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")

_SENSITIVE_DICT_KEYS = frozenset(
    {
        "password",
        "passwd",
        "pass",
        "token",
        "auth_token",
        "authtoken",
        "x-agent-token",
        "adapter_token",
        "bot_token",
        "telegram_bot_token",
        "api_key",
        "jx_session_id",
        "jx_access_token",
        "jx_refresh_token",
        "secret",
    }
)


class Redactor:
    """Sanitizes strings and nested structures before logging or external transmission."""

    def __init__(
        self,
        *,
        mask_emails: bool = True,
        email_to_alias: dict[str, str] | None = None,
        literal_secrets: set[str] | None = None,
    ) -> None:
        self.mask_emails = mask_emails
        self._email_to_alias: dict[str, str] = {
            k.lower(): v for k, v in (email_to_alias or {}).items()
        }
        self._literals: set[str] = {
            s for s in (literal_secrets or set()) if s and len(s) >= 4
        }

    def register_secret(self, secret_value: str) -> None:
        """Register a dynamic secret/canary so even arbitrary strings never leak."""
        if secret_value and len(secret_value) >= 4:
            self._literals.add(secret_value)

    def register_account_alias(self, email: str, alias: str) -> None:
        """Bind an account email to its canonical alias (e.g. acc_01)."""
        if email and alias:
            self._email_to_alias[email.strip().lower()] = alias.strip()

    def redact_text(self, text: str) -> str:
        if not text:
            return text
        out = text
        # 1. Exact literal secrets first (longest first to avoid partial residue)
        for lit in sorted(self._literals, key=len, reverse=True):
            if lit in out:
                out = out.replace(lit, "<REDACTED_SECRET>")
        # 2. Structural regex patterns
        for pat, repl in _PATTERNS:
            out = pat.sub(repl, out)
        # 3. Email masking / alias substitution
        if self.mask_emails:
            def _sub_email(match: re.Match[str]) -> str:
                full = match.group(0).lower()
                if full in self._email_to_alias:
                    return self._email_to_alias[full]
                local = match.group(1)
                domain = match.group(2)
                prefix = local[0] if local else "*"
                return f"{prefix}***@{domain}"

            out = _EMAIL_PATTERN.sub(_sub_email, out)
        return out

    def redact_obj(self, value: Any) -> Any:
        """Recursively sanitize dicts, lists, tuples, and strings."""
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, dict):
            cleaned: dict[Any, Any] = {}
            for k, v in value.items():
                key_str = str(k).lower()
                if key_str in _SENSITIVE_DICT_KEYS:
                    cleaned[k] = "<REDACTED_SECRET>" if v is not None else None
                else:
                    cleaned[k] = self.redact_obj(v)
            return cleaned
        if isinstance(value, list):
            return [self.redact_obj(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.redact_obj(item) for item in value)
        return value
