"""Repository secret scanner and secret-store ACL verifier (PR-00 / T01, T02)."""

from __future__ import annotations

import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_SCAN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("TELEGRAM_BOT_TOKEN", re.compile(r"\b\d{8,11}:[A-Za-z0-9_-]{35}\b")),
    ("GITHUB_PAT", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,255}\b")),
    ("GITHUB_FINE_PAT", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,255}\b")),
    ("OPENAI_OR_ANTHROPIC_KEY", re.compile(r"\bsk-[A-Za-z0-9_-]{24,255}\b")),
    ("GROQ_KEY", re.compile(r"\bgsk_[A-Za-z0-9_-]{24,255}\b")),
    ("GEMINI_KEY", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("AWS_ACCESS_KEY", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("SLACK_TOKEN", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,255}\b")),
]

_SKIP_DIRS = frozenset(
    {
        ".git",
        ".osrs_ee_data",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "venv",
        "node_modules",
    }
)

_BINARY_EXTS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".ico",
        ".pdf",
        ".zip",
        ".jar",
        ".class",
        ".sqlite",
        ".sqlite3",
        ".db",
    }
)


@dataclass(frozen=True)
class ScanFinding:
    """Metadata-only finding — NEVER stores the raw secret value."""

    relative_path: str
    line_number: int
    rule_id: str
    match_length: int


_AUTHORIZED_GITIGNORED_LOCAL_FILES = frozenset({".env", "accounts.txt"})


def _git_safe_untracked_ignored_relpaths(repo_root: Path) -> set[str]:
    """Return local runtime secret filenames ONLY if ignored by .gitignore AND NOT tracked in git."""
    if not (repo_root / ".git").exists():
        return set()
    candidates = [name for name in _AUTHORIZED_GITIGNORED_LOCAL_FILES if (repo_root / name).is_file()]
    if not candidates:
        return set()
    try:
        tracked_proc = subprocess.run(
            ["git", "ls-files", "--", *candidates],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
        tracked = set(tracked_proc.stdout.strip().splitlines())
        ignored_proc = subprocess.run(
            ["git", "check-ignore", "--", *candidates],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
        ignored = set(ignored_proc.stdout.strip().splitlines())
        return ignored - tracked
    except OSError:
        return set()


def scan_path(root: str | Path) -> list[ScanFinding]:
    """Scan a file or directory tree for high-confidence secret patterns."""
    root_path = Path(root).resolve()
    findings: list[ScanFinding] = []

    if root_path.is_file():
        files = [root_path]
        base = root_path.parent
    else:
        base = root_path
        safe_local_ignored = _git_safe_untracked_ignored_relpaths(root_path)
        files = []
        for dirpath, dirnames, filenames in os.walk(root_path):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for fn in filenames:
                fp = Path(dirpath) / fn
                rel_candidate = fp.relative_to(base).as_posix()
                if rel_candidate in safe_local_ignored:
                    continue
                if fp.suffix.lower() not in _BINARY_EXTS:
                    files.append(fp)

    for fp in sorted(files):
        try:
            content = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rel = str(fp.relative_to(base))
        for line_idx, line in enumerate(content.splitlines(), start=1):
            for rule_id, pat in _SCAN_PATTERNS:
                for m in pat.finditer(line):
                    findings.append(
                        ScanFinding(
                            relative_path=rel,
                            line_number=line_idx,
                            rule_id=rule_id,
                            match_length=len(m.group(0)),
                        )
                    )
    return findings


def check_secret_file_permissions(secret_file: str | Path) -> tuple[bool, str]:
    """Verify a local secret file is owner-only readable/writable (POSIX 0600)."""
    p = Path(secret_file)
    if not p.exists():
        return False, f"Secret file does not exist: {p.name}"
    if os.name == "nt":
        # On Windows, POSIX group/other bits are synthetic; require regular file
        return p.is_file(), "windows_file_present"
    mode = stat.S_IMODE(p.stat().st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        return False, f"Insecure permissions {oct(mode)} on {p.name}; expected 0o600"
    return True, "ok_0600"


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        except Exception:
            pass
    args = argv if argv is not None else sys.argv[1:]
    target = Path(args[0]) if args else Path.cwd()
    findings = scan_path(target)
    if findings:
        for f in findings:
            print(
                f"[SECRET_SCAN_FAIL] {f.relative_path}:{f.line_number} "
                f"rule={f.rule_id} len={f.match_length}"
            )
        return 1
    print(f"[SECRET_SCAN_OK] Zero secret patterns detected under {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
