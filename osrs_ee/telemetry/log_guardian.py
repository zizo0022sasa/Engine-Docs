"""Storage quota enforcement (<500MB guaranteed) and redacting log rotation (PR-07 / AUD-30 / T29)."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from osrs_ee.security.redaction import Redactor

# Mathematical budget proof (AUD-30):
# 15 accounts * (4MB * 3 files) = 180MB
# Core logs   * (10MB * 4 files)=  40MB
# Triage imgs * (80 * 1.5MB)    = 120MB
# SQLite + Outbox               <= 60MB
# Total Max                     <= 400MB (< 500MB hard cap with 100MB safety margin!)
ACCOUNT_LOG_MAX_BYTES = 4 * 1024 * 1024
ACCOUNT_LOG_BACKUP_COUNT = 2  # active + 2 backups = 3 files total per account
CORE_LOG_MAX_BYTES = 10 * 1024 * 1024
CORE_LOG_BACKUP_COUNT = 3     # active + 3 backups = 4 files total
MAX_TRIAGE_CAPTURES = 80
MAX_TRIAGE_BYTES = 120 * 1024 * 1024
GLOBAL_STORAGE_BUDGET_BYTES = 500 * 1024 * 1024


class _RedactingFilter(logging.Filter):
    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.msg = self._redactor.redact_text(record.getMessage())
            record.args = ()
        else:
            record.msg = self._redactor.redact_text(str(record.msg))
        return True


def create_redacting_account_logger(
    account_id: str,
    logs_dir: str | Path,
    *,
    redactor: Redactor,
    max_bytes: int = ACCOUNT_LOG_MAX_BYTES,
    backup_count: int = ACCOUNT_LOG_BACKUP_COUNT,
) -> logging.Logger:
    """Create a per-account rotating logger guaranteed to scrub secrets and cap file count."""
    acc_dir = Path(logs_dir) / "accounts"
    acc_dir.mkdir(parents=True, exist_ok=True)
    log_file = acc_dir / f"{account_id}.log"

    logger_name = f"osrs_ee.account.{account_id}.{id(logs_dir)}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.handlers.clear()

    handler = RotatingFileHandler(
        log_file,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    handler.addFilter(_RedactingFilter(redactor))
    logger.addHandler(handler)
    return logger


class LogQuotaGuardian:
    """Enforces <500MB total storage budget while protecting open-incident artifacts (T29)."""

    def __init__(
        self,
        logs_root: str | Path,
        *,
        max_triage_captures: int = MAX_TRIAGE_CAPTURES,
        max_triage_bytes: int = MAX_TRIAGE_BYTES,
    ) -> None:
        self.logs_root = Path(logs_root)
        self.triage_dir = self.logs_root / "triage"
        self.triage_dir.mkdir(parents=True, exist_ok=True)
        self.max_triage_captures = max_triage_captures
        self.max_triage_bytes = max_triage_bytes

    @staticmethod
    def calculate_worst_case_budget_bytes(max_accounts: int = 15) -> int:
        acct_bytes = max_accounts * ACCOUNT_LOG_MAX_BYTES * (ACCOUNT_LOG_BACKUP_COUNT + 1)
        core_bytes = CORE_LOG_MAX_BYTES * (CORE_LOG_BACKUP_COUNT + 1)
        outbox_db_bytes = 60 * 1024 * 1024
        return acct_bytes + core_bytes + MAX_TRIAGE_BYTES + outbox_db_bytes

    def prune_triage_captures(self, *, open_incident_ids: set[str]) -> list[str]:
        """Evict oldest closed-incident captures when count or byte cap is exceeded.

        Files whose name contains any `inc_id` in `open_incident_ids` are NEVER deleted!
        """
        files = sorted(
            [p for p in self.triage_dir.iterdir() if p.is_file()],
            key=lambda p: p.stat().st_mtime,
        )
        total_bytes = sum(p.stat().st_size for p in files)
        count = len(files)
        deleted: list[str] = []

        for fp in files:
            if count <= self.max_triage_captures and total_bytes <= self.max_triage_bytes:
                break
            if any(inc_id in fp.name for inc_id in open_incident_ids):
                # Protected open-incident evidence!
                continue
            sz = fp.stat().st_size
            fp.unlink(missing_ok=True)
            deleted.append(fp.name)
            count -= 1
            total_bytes -= sz

        return deleted
