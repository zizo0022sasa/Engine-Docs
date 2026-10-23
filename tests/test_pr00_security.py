"""PR-00 Acceptance Tests: T01 (Secret Containment & Scan) and T02 (Zero Leakage & Ignore)."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from osrs_ee.security.redaction import Redactor
from osrs_ee.security.secret_scan import check_secret_file_permissions, scan_path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_t01_repository_has_zero_exposed_secrets() -> None:
    """T01: Entire repository working tree (including للمعمارية.md and docs) must contain 0 secrets."""
    findings = scan_path(REPO_ROOT)
    assert findings == [], f"Detected forbidden secret patterns: {findings}"


def test_t01_secret_scanner_detects_synthetic_tokens_without_echoing_value(tmp_path: Path) -> None:
    """T01: Scanner catches Telegram/GitHub/AI token shapes and never stores raw secret in finding."""
    # Construct synthetic shapes dynamically so source file itself stays clean
    fake_tg = "1234567890:" + ("A" * 35)
    fake_gh = "ghp_" + ("B" * 36)
    fake_ai = "gsk_" + ("C" * 32)
    sample = tmp_path / "leak.txt"
    sample.write_text(f"tg={fake_tg}\ngh={fake_gh}\nai={fake_ai}\n", encoding="utf-8")

    findings = scan_path(tmp_path)
    rules = {f.rule_id for f in findings}
    assert rules == {"TELEGRAM_BOT_TOKEN", "GITHUB_PAT", "GROQ_KEY"}
    for f in findings:
        assert fake_tg not in repr(f)
        assert fake_gh not in repr(f)
        assert fake_ai not in repr(f)


def test_t02_canary_password_and_credentials_never_leak() -> None:
    """T02: Canary password, proxy auth, emails, and tokens are stripped across text and dicts."""
    canary_pw = "CanarySuperSecret#2026!"
    fake_tg = "987654321:" + ("Z" * 35)
    redactor = Redactor(
        email_to_alias={"player1@example.com": "acc_01"},
        literal_secrets={canary_pw},
    )

    raw_payload = {
        "account": "player1@example.com",
        "other_email": "unknown_user@domain.org",
        "password": canary_pw,
        "proxy": f"socks5://proxyuser:{canary_pw}@192.0.2.10:1080",
        "log_line": (
            f"Auth failed for player1@example.com with pass={canary_pw} "
            f"and X-Agent-Token: tok_live_998877 and tg={fake_tg}"
        ),
        "nested": [{"JX_SESSION_ID": "jx_sess_abcdef123456"}],
    }

    sanitized = redactor.redact_obj(raw_payload)
    serialized = repr(sanitized)

    assert canary_pw not in serialized
    assert fake_tg not in serialized
    assert "tok_live_998877" not in serialized
    assert "jx_sess_abcdef123456" not in serialized
    assert "proxyuser" not in serialized
    assert "player1@example.com" not in serialized
    assert "unknown_user@domain.org" not in serialized

    assert sanitized["account"] == "acc_01"
    assert sanitized["other_email"] == "u***@domain.org"
    assert sanitized["password"] == "<REDACTED_SECRET>"
    assert sanitized["proxy"] == "socks5://<REDACTED_PROXY_AUTH>@192.0.2.10:1080"


def test_t02_gitignore_excludes_all_sensitive_and_mutable_state_paths() -> None:
    """T02: Verify git check-ignore protects databases, credential files, sandboxes, and tokens."""
    sensitive_paths = [
        ".env",
        "Root/keys.txt",
        "accounts.txt",
        "accounts_database.json",
        "OSRS_Bot/accounts_database.json",
        "data/state.sqlite3",
        "data/state.db-wal",
        "credentials.properties",
        ".agent-token",
        "spoofed-device-ids.properties",
        "sandboxes/acc_01/random.dat",
        "logs/acc_01.log",
        "state_snapshot.json",
    ]
    res = subprocess.run(
        ["git", "check-ignore", "--", *sensitive_paths],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    ignored = set(res.stdout.strip().splitlines())
    missing = set(sensitive_paths) - ignored
    assert not missing, f"Paths NOT excluded by .gitignore: {sorted(missing)}"


def test_t02_secret_file_acl_check(tmp_path: Path) -> None:
    """T02: Secret file permission validator rejects world-readable files on POSIX."""
    sec = tmp_path / "vault.secret"
    sec.write_text("placeholder", encoding="utf-8")
    if os.name != "nt":
        sec.chmod(0o644)
        ok_bad, _ = check_secret_file_permissions(sec)
        assert not ok_bad
        sec.chmod(0o600)
        ok_good, reason = check_secret_file_permissions(sec)
        assert ok_good and reason == "ok_0600"
    else:
        ok_win, _ = check_secret_file_permissions(sec)
        assert ok_win
