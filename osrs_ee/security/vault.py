"""Permission-guarded LocalSecretVault and Dual-Auth Pipe Account Ingestor (PR-05b / T01, T02, AUD-11).

Keeps raw passwords, Jagex session tokens, and proxy user:pass credentials strictly
inside an owner-only local vault file (0600 on POSIX), emitting only `vault://...`
references into StateStore and automatically registering secrets with Redactor.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from osrs_ee.security.redaction import Redactor
from osrs_ee.security.secret_scan import check_secret_file_permissions
from osrs_ee.state.fsm import AccountStatus
from osrs_ee.state.store import StateStore

REGION_TO_TIMEZONE: dict[str, str] = {
    "EG": "Africa/Cairo",
    "EGYPT": "Africa/Cairo",
    "DE": "Europe/Berlin",
    "GERMANY": "Europe/Berlin",
    "UK": "Europe/London",
    "GB": "Europe/London",
    "US": "America/New_York",
    "US_EAST": "America/New_York",
    "US_WEST": "America/Los_Angeles",
}

REGION_CANONICAL: dict[str, str] = {
    "EG": "EG",
    "EGYPT": "EG",
    "DE": "DE",
    "GERMANY": "DE",
    "UK": "UK",
    "GB": "UK",
    "US": "US_EAST",
    "US_EAST": "US_EAST",
    "US_WEST": "US_WEST",
}


def normalize_proxy_uri(raw_proxy: str) -> str:
    """Normalize flexible proxy inputs into `socks5://[user:pass@]host:port` for the local sidecar."""
    val = raw_proxy.strip()
    if not val:
        raise ValueError("Empty proxy specification")
    if "://" not in val:
        # Support ip:port or host:port:user:pass shorthand
        parts = val.split(":")
        if len(parts) == 2:
            val = f"socks5://{parts[0]}:{parts[1]}"
        elif len(parts) == 4:
            host, port, user, pw = parts
            val = f"socks5://{user}:{pw}@{host}:{port}"
        else:
            raise ValueError(f"Unrecognized proxy format (expected socks5://... or host:port): {raw_proxy[:12]}...")
    parsed = urlparse(val)
    if parsed.scheme.lower() not in ("socks5", "socks5h", "socks"):
        raise ValueError(
            f"Upstream proxy must support SOCKS5 for remote DNS & UDP/TCP isolation (got scheme={parsed.scheme!r})"
        )
    if not parsed.hostname or not parsed.port:
        raise ValueError("Upstream proxy URI missing hostname or port")
    return val


class LocalSecretVault:
    """Owner-only (0600) JSON secret vault resolving `vault://...` URI references in memory."""

    def __init__(self, vault_path: str | Path, *, redactor: Redactor | None = None) -> None:
        self.vault_path = Path(vault_path)
        self.redactor = redactor or Redactor()
        self._data: dict[str, dict[str, Any]] = {}
        if self.vault_path.exists():
            self._load()
        else:
            self._save()

    def _load(self) -> None:
        ok, reason = check_secret_file_permissions(self.vault_path)
        if not ok:
            raise PermissionError(f"Refusing to load vault with insecure ACL: {reason}")
        raw = json.loads(self.vault_path.read_text(encoding="utf-8"))
        self._data = dict(raw.get("entries", {}))
        for ref, payload in self._data.items():
            self._register_in_redactor(ref, payload)

    def _save(self) -> None:
        self.vault_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.vault_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({"version": "1.0", "entries": self._data}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        if os.name != "nt":
            tmp.chmod(0o600)
        tmp.replace(self.vault_path)
        if os.name != "nt":
            self.vault_path.chmod(0o600)

    def _register_in_redactor(self, ref: str, payload: dict[str, Any]) -> None:
        alias = payload.get("account_id")
        email = payload.get("email")
        if email and alias:
            self.redactor.register_account_alias(str(email), str(alias))
        for k in ("password", "jx_session_id", "jx_access_token", "proxy_uri", "token"):
            val = payload.get(k)
            if isinstance(val, str) and len(val) >= 4:
                self.redactor.register_secret(val)
                if k == "proxy_uri":
                    parsed = urlparse(val)
                    if parsed.password and len(unquote(parsed.password)) >= 4:
                        self.redactor.register_secret(unquote(parsed.password))

    def put_secret(self, vault_ref: str, payload: dict[str, Any]) -> str:
        if not vault_ref.startswith("vault://"):
            raise ValueError(f"Vault reference must start with vault://, got {vault_ref!r}")
        self._data[vault_ref] = dict(payload)
        self._register_in_redactor(vault_ref, payload)
        self._save()
        return vault_ref

    def resolve_secret(self, vault_ref: str) -> dict[str, Any]:
        ok, reason = check_secret_file_permissions(self.vault_path)
        if not ok:
            raise PermissionError(f"Vault ACL check failed prior to secret resolution: {reason}")
        if vault_ref not in self._data:
            raise KeyError(f"Secret reference not found in vault: {vault_ref}")
        return dict(self._data[vault_ref])


@dataclass(frozen=True)
class IngestedAccountSummary:
    account_id: str
    auth_mode: str
    region: str
    timezone: str
    credential_ref: str
    proxy_ref: str


def ingest_pipe_lines(
    lines: list[str],
    *,
    store: StateStore,
    vault: LocalSecretVault,
    starting_index: int = 1,
) -> list[IngestedAccountSummary]:
    """Ingest Dual-Auth pipe lines into Vault + StateStore without storing secrets in SQLite.

    Supported formats:
      1. Legacy: `email@domain.com|Password123|socks5://user:pass@host:port|Egypt`
      2. Jagex:  `jx_sess_token|char_id_01|socks5://user:pass@host:port|Germany`
    """
    ingested: list[IngestedAccountSummary] = []
    idx = starting_index

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3 or len(parts) > 4:
            raise ValueError("Invalid pipe line format (expected 3 or 4 pipe-separated fields)")

        field1, field2, raw_proxy = parts[0], parts[1], parts[2]
        raw_region = parts[3] if len(parts) == 4 else "EG"
        reg_key = raw_region.upper()
        if reg_key not in REGION_CANONICAL:
            raise ValueError(f"Unsupported region {raw_region!r}; supported={sorted(REGION_CANONICAL)}")

        canonical_region = REGION_CANONICAL[reg_key]
        tz_name = REGION_TO_TIMEZONE[reg_key]
        normalized_proxy = normalize_proxy_uri(raw_proxy)

        while True:
            account_id = f"acc_{idx:02d}"
            idx += 1
            try:
                store.get_account(account_id)
            except KeyError:
                break

        seed_digest = hashlib.sha256(f"{account_id}:{field1}".encode("utf-8")).hexdigest()
        persona_seed = int(seed_digest[:8], 16)

        cred_ref = f"vault://accounts/{account_id}/auth"
        proxy_hash = hashlib.sha256(normalized_proxy.encode("utf-8")).hexdigest()[:10]
        proxy_ref = f"vault://proxies/{canonical_region.lower()}_{proxy_hash}"

        if "@" in field1:
            auth_mode = "LEGACY_CREDENTIALS"
            vault.put_secret(
                cred_ref,
                {"account_id": account_id, "auth_mode": auth_mode, "email": field1, "password": field2},
            )
        else:
            auth_mode = "JAGEX_SESSION"
            vault.put_secret(
                cred_ref,
                {
                    "account_id": account_id,
                    "auth_mode": auth_mode,
                    "jx_session_id": field1,
                    "jx_character_id": field2,
                    "jx_display_name": account_id,
                },
            )

        vault.put_secret(
            proxy_ref,
            {"proxy_uri": normalized_proxy, "region": canonical_region},
        )

        store.register_account(
            account_id=account_id,
            auth_mode=auth_mode,
            credential_ref=cred_ref,
            proxy_ref=proxy_ref,
            region=canonical_region,
            timezone_name=tz_name,
            persona_seed=persona_seed,
            initial_status=AccountStatus.PENDING_DISCOVERY,
        )
        ingested.append(
            IngestedAccountSummary(
                account_id=account_id,
                auth_mode=auth_mode,
                region=canonical_region,
                timezone=tz_name,
                credential_ref=cred_ref,
                proxy_ref=proxy_ref,
            )
        )

    return ingested
