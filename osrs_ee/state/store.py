"""ACID SQLite StateStore with exclusive leases, command journal, dedupe, and RBAC approvals (PR-03)."""

from __future__ import annotations

import ctypes
import json
import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from osrs_ee.contracts.models import validate_command_intent, validate_command_result
from osrs_ee.security.redaction import Redactor
from osrs_ee.state.fsm import (
    ROLE_RANK,
    UNCLOSED_SESSION_STATES,
    AccountStatus,
    CommandJournalState,
    OperatorRole,
    ReasonCode,
    assert_valid_account_transition,
    compute_progression_stage,
    is_in_circadian_sleep_window,
)


class StaleContextError(RuntimeError):
    """Raised when lease_id, session_epoch, or client_instance_id no longer owns the account/slot."""


class CooldownActiveError(RuntimeError):
    """Raised when attempting to schedule an account before its cooldown_until expires."""


class CircadianSleepActiveError(RuntimeError):
    """Raised when attempting to schedule an account during its local night sleep window."""


class ProxyCollisionError(RuntimeError):
    """Raised when another active session already holds the same dedicated proxy_ref."""


class UnknownEffectPendingError(RuntimeError):
    """Raised when a session has an un-reconciled UNKNOWN non-idempotent command."""


class AuthorizationDeniedError(PermissionError):
    """Raised when operator RBAC check fails (Default-Deny)."""


class ApprovalRejectedError(RuntimeError):
    """Raised when an approval token is invalid, expired, replayed, or stale."""


def _utc_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(ts: str) -> datetime:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts).astimezone(timezone.utc)


def is_process_alive(pid: int | None) -> bool:
    """Cross-platform OS process liveness probe without sending destructive signals on Windows."""
    if pid is None or int(pid) <= 0:
        return False
    target_pid = int(pid)
    if target_pid == os.getpid():
        return True
    if os.name == "nt":
        # Windows: NEVER call os.kill(pid, 0) (sends CTRL_C_EVENT=0). Query kernel32 exit code safely.
        process_query_limited_info = 0x1000
        still_active = 259
        try:
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            handle = kernel32.OpenProcess(process_query_limited_info, False, target_pid)
            if not handle:
                err = int(kernel32.GetLastError())
                # ERROR_ACCESS_DENIED (5) means process exists under another elevation/session
                return err == 5
            try:
                exit_code = ctypes.c_ulong(0)
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return False
                return int(exit_code.value) == still_active
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False

    try:
        os.kill(target_pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


class StateStore:
    """Single-writer transactional SQLite state store with human-readable JSON snapshot export."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        snapshot_path: str | Path | None = None,
        redactor: Redactor | None = None,
        on_crash_recovered: Callable[[dict[str, Any], str], None] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_path = Path(snapshot_path) if snapshot_path else None
        self.redactor = redactor or Redactor()
        self.on_crash_recovered = on_crash_recovered
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.db_path),
            timeout=10.0,
            isolation_level=None,  # explicit transaction management
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA busy_timeout=10000;")
            self._conn.execute("PRAGMA synchronous=FULL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id TEXT PRIMARY KEY,
                    auth_mode TEXT NOT NULL,
                    credential_ref TEXT NOT NULL,
                    proxy_ref TEXT NOT NULL,
                    region TEXT NOT NULL,
                    timezone TEXT NOT NULL,
                    persona_seed INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reason_code TEXT NOT NULL,
                    state_version INTEGER NOT NULL DEFAULT 1,
                    session_epoch INTEGER NOT NULL DEFAULT 0,
                    cooldown_until TEXT,
                    stage TEXT NOT NULL DEFAULT 'STAGE_0_TUTORIAL',
                    tutorial_varp_281 INTEGER,
                    quest_points INTEGER,
                    total_level INTEGER,
                    playtime_minutes INTEGER,
                    trade_unlocked INTEGER NOT NULL DEFAULT 0,
                    realized_gp INTEGER NOT NULL DEFAULT 0,
                    last_death_task_id TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS active_leases (
                    account_id TEXT PRIMARY KEY REFERENCES accounts(account_id),
                    session_id TEXT NOT NULL UNIQUE,
                    lease_id TEXT NOT NULL UNIQUE,
                    slot_id INTEGER NOT NULL UNIQUE,
                    proxy_ref TEXT NOT NULL UNIQUE,
                    session_epoch INTEGER NOT NULL,
                    client_instance_id TEXT,
                    task_id TEXT NOT NULL,
                    has_unknown_command INTEGER NOT NULL DEFAULT 0,
                    acquired_at TEXT NOT NULL,
                    owner_pid INTEGER,
                    heartbeat_at TEXT
                );

                CREATE TABLE IF NOT EXISTS command_journal (
                    command_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    lease_id TEXT NOT NULL,
                    session_epoch INTEGER NOT NULL,
                    command_type TEXT NOT NULL,
                    idempotency_class TEXT NOT NULL,
                    journal_state TEXT NOT NULL,
                    transport_status TEXT,
                    effect_status TEXT,
                    payload_json TEXT NOT NULL,
                    post_observation_id TEXT,
                    diagnostic_note TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    session_epoch INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operator_acl (
                    user_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    PRIMARY KEY (user_id, chat_id)
                );

                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    incident_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    session_epoch INTEGER NOT NULL,
                    plan_hash TEXT NOT NULL,
                    expected_state_version INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT,
                    consumed_by_user_id INTEGER
                );

                CREATE TABLE IF NOT EXISTS account_profiles (
                    account_id TEXT PRIMARY KEY REFERENCES accounts(account_id),
                    combat_level INTEGER NOT NULL,
                    total_level INTEGER NOT NULL,
                    is_member INTEGER NOT NULL DEFAULT 0,
                    membership_days INTEGER NOT NULL DEFAULT 0,
                    tutorial_varp_281 INTEGER NOT NULL DEFAULT 0,
                    quest_points INTEGER NOT NULL DEFAULT 0,
                    playtime_minutes INTEGER NOT NULL DEFAULT 0,
                    inventory_coins_gp INTEGER NOT NULL DEFAULT 0,
                    bank_coins_gp INTEGER NOT NULL DEFAULT 0,
                    total_liquid_coins_gp INTEGER NOT NULL DEFAULT 0,
                    bank_scanned INTEGER NOT NULL DEFAULT 0,
                    skills_json TEXT NOT NULL,
                    skill_xp_json TEXT NOT NULL,
                    completed_quests_json TEXT NOT NULL,
                    stronghold_floors_json TEXT NOT NULL,
                    available_tools_json TEXT NOT NULL,
                    available_gear_json TEXT NOT NULL,
                    available_food_json TEXT NOT NULL,
                    inventory_json TEXT NOT NULL,
                    bank_json TEXT NOT NULL,
                    eligible_recipes_json TEXT NOT NULL,
                    position_json TEXT NOT NULL,
                    scan_duration_ms INTEGER NOT NULL DEFAULT 0,
                    scanned_at TEXT NOT NULL
                );
                """
            )
            existing_cols = {
                str(r["name"])
                for r in self._conn.execute("PRAGMA table_info(active_leases)").fetchall()
            }
            if "owner_pid" not in existing_cols:
                self._conn.execute("ALTER TABLE active_leases ADD COLUMN owner_pid INTEGER;")
            if "heartbeat_at" not in existing_cols:
                self._conn.execute("ALTER TABLE active_leases ADD COLUMN heartbeat_at TEXT;")

    def _tx_begin(self) -> None:
        self._conn.execute("BEGIN IMMEDIATE;")

    def _tx_commit(self) -> None:
        self._conn.execute("COMMIT;")
        if self.snapshot_path is not None:
            self._export_snapshot_unlocked()

    def _tx_rollback(self) -> None:
        if self._conn.in_transaction:
            self._conn.execute("ROLLBACK;")

    def export_snapshot(self) -> None:
        with self._lock:
            self._export_snapshot_unlocked()

    def _export_snapshot_unlocked(self) -> None:
        assert self.snapshot_path is not None
        rows = self._conn.execute("SELECT * FROM accounts ORDER BY account_id").fetchall()
        prof_rows = {
            str(pr["account_id"]): pr
            for pr in self._conn.execute("SELECT * FROM account_profiles").fetchall()
        }
        accounts_dict: dict[str, Any] = {}
        for r in rows:
            aid = str(r["account_id"])
            entry: dict[str, Any] = {
                "account_id": aid,
                "auth_mode": r["auth_mode"],
                "credential_ref": r["credential_ref"],
                "proxy_ref": r["proxy_ref"],
                "region": r["region"],
                "timezone": r["timezone"],
                "persona_seed": r["persona_seed"],
                "status": r["status"],
                "reason_code": r["reason_code"],
                "state_version": r["state_version"],
                "session_epoch": r["session_epoch"],
                "cooldown_until": r["cooldown_until"],
                "progression": {
                    "stage": r["stage"],
                    "tutorial_varp_281": r["tutorial_varp_281"],
                    "quest_points": r["quest_points"],
                    "total_level": r["total_level"],
                    "playtime_minutes": r["playtime_minutes"],
                    "trade_unlocked": bool(r["trade_unlocked"]),
                    "realized_gp": r["realized_gp"],
                },
            }
            pr = prof_rows.get(aid)
            if pr is not None:
                entry["discovered_profile"] = {
                    "combat_level": int(pr["combat_level"]),
                    "total_level": int(pr["total_level"]),
                    "is_member": bool(pr["is_member"]),
                    "total_liquid_coins_gp": int(pr["total_liquid_coins_gp"]),
                    "bank_scanned": bool(pr["bank_scanned"]),
                    "eligible_recipes": json.loads(str(pr["eligible_recipes_json"])),
                    "scanned_at": str(pr["scanned_at"]),
                }
            accounts_dict[aid] = entry
        snapshot = self.redactor.redact_obj(
            {
                "schema_version": "1.0",
                "exported_at": _utc_iso(datetime.now(timezone.utc)),
                "accounts": accounts_dict,
            }
        )
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.snapshot_path.with_suffix(f".{os.getpid()}.tmp")
        tmp_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(self.snapshot_path)

    # ------------------------------------------------------------------
    # Account Onboarding & Queries
    # ------------------------------------------------------------------
    def register_account(
        self,
        *,
        account_id: str,
        auth_mode: str,
        credential_ref: str,
        proxy_ref: str,
        region: str,
        timezone_name: str,
        persona_seed: int,
        initial_status: AccountStatus = AccountStatus.PENDING_DISCOVERY,
        now_utc: datetime | None = None,
    ) -> dict[str, Any]:
        if not account_id.startswith("acc_") or "@" in account_id:
            raise ValueError(f"Opaque account_id required (e.g. acc_01), got {account_id!r}")
        if auth_mode not in ("LEGACY_CREDENTIALS", "JAGEX_SESSION"):
            raise ValueError(f"Unsupported auth_mode: {auth_mode!r}")
        if not credential_ref.startswith("vault://") or not proxy_ref.startswith("vault://"):
            raise ValueError("credential_ref and proxy_ref must be vault:// references (zero raw secrets)")

        now_s = _utc_iso(now_utc or datetime.now(timezone.utc))
        with self._lock:
            try:
                self._tx_begin()
                self._conn.execute(
                    """
                    INSERT INTO accounts (
                        account_id, auth_mode, credential_ref, proxy_ref,
                        region, timezone, persona_seed, status, reason_code, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        auth_mode,
                        credential_ref,
                        proxy_ref,
                        region,
                        timezone_name,
                        persona_seed,
                        initial_status.value,
                        ReasonCode.NONE.value,
                        now_s,
                    ),
                )
                self._tx_commit()
            except Exception:
                self._tx_rollback()
                raise
            return self.get_account(account_id)

    def get_account(self, account_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown account_id: {account_id}")
            return dict(row)

    def transition_resting_to_ready_if_due(self, account_id: str, now_utc: datetime) -> bool:
        """Promote RESTING -> READY only after cooldown_until has elapsed."""
        with self._lock:
            try:
                self._tx_begin()
                row = self._conn.execute(
                    "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
                ).fetchone()
                if row is None:
                    raise KeyError(account_id)
                if row["status"] != AccountStatus.RESTING.value:
                    self._tx_rollback()
                    return False
                if row["cooldown_until"] and _parse_iso(row["cooldown_until"]) > now_utc:
                    self._tx_rollback()
                    return False
                assert_valid_account_transition(AccountStatus.RESTING, AccountStatus.READY)
                self._conn.execute(
                    """
                    UPDATE accounts
                       SET status = ?, reason_code = ?, state_version = state_version + 1, updated_at = ?
                     WHERE account_id = ?
                    """,
                    (AccountStatus.READY.value, ReasonCode.NONE.value, _utc_iso(now_utc), account_id),
                )
                self._tx_commit()
                return True
            except Exception:
                self._tx_rollback()
                raise

    # ------------------------------------------------------------------
    # Exclusive Leases & Epoch Fencing (AUD-05 / T04)
    # ------------------------------------------------------------------
    def acquire_session_lease(
        self,
        *,
        account_id: str,
        slot_id: int,
        task_id: str,
        now_utc: datetime | None = None,
        session_kind: str = "WORK",  # WORK | DISCOVERY | RECONCILE
        bypass_circadian: bool = False,
        owner_pid: int | None = None,
    ) -> dict[str, Any]:
        now_utc = now_utc or datetime.now(timezone.utc)
        with self._lock:
            try:
                self._tx_begin()
                acc = self._conn.execute(
                    "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
                ).fetchone()
                if acc is None:
                    raise KeyError(account_id)

                current_status = AccountStatus(acc["status"])
                if acc["cooldown_until"] and _parse_iso(acc["cooldown_until"]) > now_utc:
                    raise CooldownActiveError(
                        f"Account {account_id} is on mandatory cooldown until {acc['cooldown_until']}"
                    )

                if session_kind == "WORK":
                    if current_status != AccountStatus.READY:
                        raise StaleContextError(
                            f"Cannot acquire WORK lease for {account_id} in state {current_status.value}"
                        )
                    if not bypass_circadian and is_in_circadian_sleep_window(
                        now_utc, acc["timezone"], acc["persona_seed"]
                    ):
                        raise CircadianSleepActiveError(
                            f"Account {account_id} is inside its geo-aligned circadian sleep block"
                        )
                    if acc["last_death_task_id"] and acc["last_death_task_id"] == task_id:
                        raise StaleContextError(
                            f"Post-death quarantine forbids scheduling {task_id} immediately after death"
                        )
                    target_status = AccountStatus.LEASED
                elif session_kind == "DISCOVERY":
                    if current_status != AccountStatus.PENDING_DISCOVERY:
                        raise StaleContextError(f"Expected PENDING_DISCOVERY, got {current_status.value}")
                    target_status = AccountStatus.DISCOVERING
                elif session_kind == "RECONCILE":
                    if current_status not in (AccountStatus.RECOVERY_REQUIRED, AccountStatus.RECONCILING):
                        raise StaleContextError(
                            f"Expected RECOVERY_REQUIRED or RECONCILING, got {current_status.value}"
                        )
                    target_status = AccountStatus.RECONCILING
                else:
                    raise ValueError(f"Unknown session_kind: {session_kind!r}")

                if current_status != target_status:
                    assert_valid_account_transition(current_status, target_status)

                # Enforce 1 Active Account = 1 Dedicated Proxy & 1 Dedicated Slot
                proxy_busy = self._conn.execute(
                    "SELECT account_id FROM active_leases WHERE proxy_ref = ?", (acc["proxy_ref"],)
                ).fetchone()
                if proxy_busy is not None:
                    raise ProxyCollisionError(
                        f"Proxy {acc['proxy_ref']} already leased by active account {proxy_busy['account_id']}"
                    )
                slot_busy = self._conn.execute(
                    "SELECT account_id FROM active_leases WHERE slot_id = ?", (slot_id,)
                ).fetchone()
                if slot_busy is not None:
                    raise StaleContextError(
                        f"Slot {slot_id} already occupied by {slot_busy['account_id']}"
                    )

                new_epoch = int(acc["session_epoch"]) + 1
                session_id = f"sess_{uuid.uuid4().hex[:12]}"
                lease_id = f"lease_{uuid.uuid4().hex[:12]}"
                now_s = _utc_iso(now_utc)
                effective_pid = os.getpid() if owner_pid is None else int(owner_pid)

                self._conn.execute(
                    """
                    UPDATE accounts
                       SET status = ?, session_epoch = ?, state_version = state_version + 1, updated_at = ?
                     WHERE account_id = ?
                    """,
                    (target_status.value, new_epoch, now_s, account_id),
                )
                self._conn.execute(
                    """
                    INSERT INTO active_leases (
                        account_id, session_id, lease_id, slot_id, proxy_ref,
                        session_epoch, client_instance_id, task_id, acquired_at,
                        owner_pid, heartbeat_at
                    ) VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)
                    """,
                    (
                        account_id,
                        session_id,
                        lease_id,
                        slot_id,
                        acc["proxy_ref"],
                        new_epoch,
                        task_id,
                        now_s,
                        effective_pid,
                        now_s,
                    ),
                )
                self._tx_commit()
                return {
                    "account_id": account_id,
                    "session_id": session_id,
                    "lease_id": lease_id,
                    "slot_id": slot_id,
                    "session_epoch": new_epoch,
                    "task_id": task_id,
                    "status": target_status.value,
                    "owner_pid": effective_pid,
                }
            except Exception:
                self._tx_rollback()
                raise

    def bind_client_instance(
        self,
        *,
        account_id: str,
        session_id: str,
        lease_id: str,
        session_epoch: int,
        client_instance_id: str,
        now_utc: datetime | None = None,
    ) -> None:
        now_s = _utc_iso(now_utc or datetime.now(timezone.utc))
        with self._lock:
            try:
                self._tx_begin()
                self._assert_lease_unlocked(
                    account_id=account_id,
                    session_id=session_id,
                    lease_id=lease_id,
                    session_epoch=session_epoch,
                    expected_client_instance_id=None,
                )
                acc = self._conn.execute(
                    "SELECT status FROM accounts WHERE account_id = ?", (account_id,)
                ).fetchone()
                cur = AccountStatus(acc["status"])
                if cur == AccountStatus.LEASED:
                    assert_valid_account_transition(cur, AccountStatus.STARTING)
                    assert_valid_account_transition(AccountStatus.STARTING, AccountStatus.RUNNING)
                    next_state = AccountStatus.RUNNING
                elif cur in (AccountStatus.DISCOVERING, AccountStatus.RECONCILING):
                    next_state = cur
                else:
                    raise StaleContextError(f"Unexpected state {cur.value} during client bind")

                self._conn.execute(
                    "UPDATE active_leases SET client_instance_id = ?, heartbeat_at = ? WHERE lease_id = ?",
                    (client_instance_id, now_s, lease_id),
                )
                self._conn.execute(
                    "UPDATE accounts SET status = ?, state_version = state_version + 1, updated_at = ? WHERE account_id = ?",
                    (next_state.value, now_s, account_id),
                )
                self._tx_commit()
            except Exception:
                self._tx_rollback()
                raise

    def _assert_lease_unlocked(
        self,
        *,
        account_id: str,
        session_id: str,
        lease_id: str,
        session_epoch: int,
        expected_client_instance_id: str | None,
    ) -> sqlite3.Row:
        lease = self._conn.execute(
            "SELECT * FROM active_leases WHERE account_id = ?", (account_id,)
        ).fetchone()
        if lease is None:
            raise StaleContextError(f"STALE_CONTEXT: no active lease for {account_id}")
        if (
            lease["session_id"] != session_id
            or lease["lease_id"] != lease_id
            or int(lease["session_epoch"]) != int(session_epoch)
        ):
            raise StaleContextError(
                f"STALE_CONTEXT: epoch/lease mismatch (expected epoch={lease['session_epoch']}, got {session_epoch})"
            )
        if expected_client_instance_id is not None and lease["client_instance_id"] != expected_client_instance_id:
            raise StaleContextError(
                f"STALE_CONTEXT: client_instance_id mismatch ({lease['client_instance_id']} != {expected_client_instance_id})"
            )
        return lease

    def verify_fencing_token(
        self,
        *,
        account_id: str,
        session_id: str,
        lease_id: str,
        session_epoch: int,
        client_instance_id: str | None = None,
        now_utc: datetime | None = None,
        refresh_heartbeat_min_interval_sec: float = 15.0,
    ) -> None:
        now_dt = now_utc or datetime.now(timezone.utc)
        with self._lock:
            lease = self._assert_lease_unlocked(
                account_id=account_id,
                session_id=session_id,
                lease_id=lease_id,
                session_epoch=session_epoch,
                expected_client_instance_id=client_instance_id,
            )
            hb_raw = lease["heartbeat_at"]
            should_refresh = hb_raw is None
            if not should_refresh and refresh_heartbeat_min_interval_sec >= 0.0:
                try:
                    age_sec = (now_dt - _parse_iso(str(hb_raw))).total_seconds()
                    should_refresh = age_sec >= refresh_heartbeat_min_interval_sec
                except Exception:
                    should_refresh = True
            if should_refresh and not self._conn.in_transaction:
                try:
                    self._tx_begin()
                    self._conn.execute(
                        "UPDATE active_leases SET heartbeat_at = ? WHERE lease_id = ?",
                        (_utc_iso(now_dt), lease_id),
                    )
                    self._conn.execute("COMMIT;")
                except Exception:
                    self._tx_rollback()

    # ------------------------------------------------------------------
    # Write-Ahead Command Journal (AUD-12 / T07)
    # ------------------------------------------------------------------
    def prepare_command_intent(
        self,
        intent: dict[str, Any],
        *,
        now_utc: datetime | None = None,
    ) -> None:
        validate_command_intent(intent)
        now_s = _utc_iso(now_utc or datetime.now(timezone.utc))
        with self._lock:
            try:
                self._tx_begin()
                lease = self._assert_lease_unlocked(
                    account_id=intent["account_id"],
                    session_id=intent["session_id"],
                    lease_id=intent["lease_id"],
                    session_epoch=intent["session_epoch"],
                    expected_client_instance_id=intent["client_instance_id"],
                )
                if int(lease["has_unknown_command"]) == 1:
                    raise UnknownEffectPendingError(
                        f"Session {intent['session_id']} has an un-reconciled UNKNOWN command; blind dispatch forbidden"
                    )
                existing = self._conn.execute(
                    "SELECT journal_state FROM command_journal WHERE command_id = ?",
                    (intent["command_id"],),
                ).fetchone()
                if existing is not None:
                    raise ValueError(f"Duplicate command_id in journal: {intent['command_id']}")

                self._conn.execute(
                    """
                    INSERT INTO command_journal (
                        command_id, session_id, account_id, lease_id, session_epoch,
                        command_type, idempotency_class, journal_state, payload_json,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        intent["command_id"],
                        intent["session_id"],
                        intent["account_id"],
                        intent["lease_id"],
                        intent["session_epoch"],
                        intent["command_type"],
                        intent["idempotency_class"],
                        CommandJournalState.PREPARED.value,
                        json.dumps(self.redactor.redact_obj(intent["payload"]), sort_keys=True),
                        now_s,
                        now_s,
                    ),
                )
                self._tx_commit()
            except Exception:
                self._tx_rollback()
                raise

    def mark_command_dispatching(
        self,
        *,
        command_id: str,
        account_id: str,
        session_id: str,
        lease_id: str,
        session_epoch: int,
        client_instance_id: str,
        now_utc: datetime | None = None,
    ) -> None:
        now_s = _utc_iso(now_utc or datetime.now(timezone.utc))
        with self._lock:
            try:
                self._tx_begin()
                self._assert_lease_unlocked(
                    account_id=account_id,
                    session_id=session_id,
                    lease_id=lease_id,
                    session_epoch=session_epoch,
                    expected_client_instance_id=client_instance_id,
                )
                row = self._conn.execute(
                    "SELECT journal_state FROM command_journal WHERE command_id = ?", (command_id,)
                ).fetchone()
                if row is None or row["journal_state"] != CommandJournalState.PREPARED.value:
                    raise ValueError(f"Command {command_id} must be PREPARED before DISPATCHING")
                self._conn.execute(
                    "UPDATE command_journal SET journal_state = ?, updated_at = ? WHERE command_id = ?",
                    (CommandJournalState.DISPATCHING.value, now_s, command_id),
                )
                self._tx_commit()
            except Exception:
                self._tx_rollback()
                raise

    def finalize_command_result(self, result: dict[str, Any]) -> bool:
        """Record final CommandResult; returns False if event_id was already deduplicated."""
        validate_command_result(result)
        with self._lock:
            try:
                self._tx_begin()
                dup = self._conn.execute(
                    "SELECT 1 FROM processed_events WHERE event_id = ?", (result["event_id"],)
                ).fetchone()
                if dup is not None:
                    self._tx_rollback()
                    return False

                cmd = self._conn.execute(
                    "SELECT * FROM command_journal WHERE command_id = ?", (result["command_id"],)
                ).fetchone()
                if cmd is None:
                    raise KeyError(f"Unknown command_id: {result['command_id']}")
                if int(cmd["session_epoch"]) != int(result["session_epoch"]):
                    raise StaleContextError("CommandResult session_epoch mismatch")

                effect = result["effect_status"]
                self._conn.execute(
                    """
                    UPDATE command_journal
                       SET journal_state = ?, transport_status = ?, effect_status = ?,
                           post_observation_id = ?, diagnostic_note = ?, updated_at = ?
                     WHERE command_id = ?
                    """,
                    (
                        effect,
                        result["transport_status"],
                        effect,
                        result["post_observation_id"],
                        self.redactor.redact_text(result["diagnostic_note"]),
                        result["completed_at"],
                        result["command_id"],
                    ),
                )
                if effect == CommandJournalState.UNKNOWN.value and cmd["idempotency_class"] == "NON_IDEMPOTENT":
                    self._conn.execute(
                        "UPDATE active_leases SET has_unknown_command = 1 WHERE session_id = ?",
                        (result["session_id"],),
                    )

                self._conn.execute(
                    """
                    INSERT INTO processed_events (event_id, account_id, session_epoch, event_type, applied_at)
                    VALUES (?, ?, ?, 'COMMAND_RESULT', ?)
                    """,
                    (result["event_id"], cmd["account_id"], cmd["session_epoch"], result["completed_at"]),
                )
                self._tx_commit()
                return True
            except Exception:
                self._tx_rollback()
                raise

    # ------------------------------------------------------------------
    # Progression Update, Event Deduplication & Session Closure
    # ------------------------------------------------------------------
    def apply_observation_snapshot(
        self,
        *,
        event_id: str,
        observation: dict[str, Any],
        realized_gp_delta: int = 0,
    ) -> bool:
        """Idempotently update progression metrics from a verified Observation (T09, T11)."""
        with self._lock:
            try:
                self._tx_begin()
                if self._conn.execute(
                    "SELECT 1 FROM processed_events WHERE event_id = ?", (event_id,)
                ).fetchone():
                    self._tx_rollback()
                    return False

                acc_id = observation["account_id"]
                acc = self._conn.execute(
                    "SELECT * FROM accounts WHERE account_id = ?", (acc_id,)
                ).fetchone()
                if acc is None:
                    raise KeyError(acc_id)
                if int(observation["session_epoch"]) != int(acc["session_epoch"]):
                    raise StaleContextError("STALE_CONTEXT: observation epoch mismatch")

                varp_281 = (
                    observation["tutorial_varp_281"]["value"]
                    if observation["tutorial_varp_281"]["quality"] == "KNOWN"
                    else acc["tutorial_varp_281"]
                )
                qp = (
                    observation["quest_points"]["value"]
                    if observation["quest_points"]["quality"] == "KNOWN"
                    else acc["quest_points"]
                )
                ttl = (
                    observation["total_level"]["value"]
                    if observation["total_level"]["quality"] == "KNOWN"
                    else acc["total_level"]
                )
                play_mins = (
                    observation["playtime_minutes"]["value"]
                    if observation["playtime_minutes"]["quality"] == "KNOWN"
                    else acc["playtime_minutes"]
                )
                stage, trade_unlocked = compute_progression_stage(
                    tutorial_varp_281=varp_281,
                    quest_points=qp,
                    total_level=ttl,
                    playtime_minutes=play_mins,
                )

                self._conn.execute(
                    """
                    UPDATE accounts
                       SET tutorial_varp_281 = ?, quest_points = ?, total_level = ?,
                           playtime_minutes = ?, stage = ?, trade_unlocked = ?,
                           realized_gp = realized_gp + ?,
                           state_version = state_version + 1, updated_at = ?
                     WHERE account_id = ?
                    """,
                    (
                        varp_281,
                        qp,
                        ttl,
                        play_mins,
                        stage,
                        1 if trade_unlocked else 0,
                        realized_gp_delta,
                        observation["observed_at"],
                        acc_id,
                    ),
                )
                self._conn.execute(
                    """
                    INSERT INTO processed_events (event_id, account_id, session_epoch, event_type, applied_at)
                    VALUES (?, ?, ?, 'OBSERVATION_SYNC', ?)
                    """,
                    (event_id, acc_id, observation["session_epoch"], observation["observed_at"]),
                )
                self._tx_commit()
                return True
            except Exception:
                self._tx_rollback()
                raise

    def close_session(
        self,
        *,
        account_id: str,
        session_id: str,
        lease_id: str,
        session_epoch: int,
        target_status: AccountStatus,
        reason_code: ReasonCode = ReasonCode.NONE,
        cooldown_until: datetime | None = None,
        now_utc: datetime | None = None,
    ) -> None:
        now_dt = now_utc or datetime.now(timezone.utc)
        now_s = _utc_iso(now_dt)
        with self._lock:
            try:
                self._tx_begin()
                lease = self._assert_lease_unlocked(
                    account_id=account_id,
                    session_id=session_id,
                    lease_id=lease_id,
                    session_epoch=session_epoch,
                    expected_client_instance_id=None,
                )
                acc = self._conn.execute(
                    "SELECT * FROM accounts WHERE account_id = ?", (account_id,)
                ).fetchone()
                cur = AccountStatus(acc["status"])

                # If session had an unresolved UNKNOWN command, forbid closing as READY/RESTING cleanly
                if int(lease["has_unknown_command"]) == 1 and target_status in (
                    AccountStatus.READY,
                    AccountStatus.RESTING,
                ):
                    target_status = AccountStatus.RECOVERY_REQUIRED
                    reason_code = ReasonCode.UNKNOWN_COMMAND_EFFECT

                # Allow RUNNING -> DRAINING -> RESTING cleanly
                if cur in (AccountStatus.RUNNING, AccountStatus.STARTING, AccountStatus.LEASED) and target_status == AccountStatus.RESTING:
                    assert_valid_account_transition(cur, AccountStatus.DRAINING)
                    assert_valid_account_transition(AccountStatus.DRAINING, AccountStatus.RESTING)
                else:
                    assert_valid_account_transition(cur, target_status)

                # Preserve existing future cooldown_until (never shorten/wipe cooldown!)
                effective_cooldown = acc["cooldown_until"]
                if cooldown_until is not None:
                    cand_iso = _utc_iso(cooldown_until)
                    if not effective_cooldown or _parse_iso(cand_iso) > _parse_iso(effective_cooldown):
                        effective_cooldown = cand_iso

                death_task = lease["task_id"] if reason_code == ReasonCode.DEATH_QUARANTINE else acc["last_death_task_id"]
                if target_status in (AccountStatus.READY, AccountStatus.RESTING) and reason_code == ReasonCode.NONE:
                    death_task = None

                self._conn.execute("DELETE FROM active_leases WHERE account_id = ?", (account_id,))
                self._conn.execute(
                    """
                    UPDATE accounts
                       SET status = ?, reason_code = ?, cooldown_until = ?,
                           last_death_task_id = ?, state_version = state_version + 1, updated_at = ?
                     WHERE account_id = ?
                    """,
                    (
                        target_status.value,
                        reason_code.value,
                        effective_cooldown,
                        death_task,
                        now_s,
                        account_id,
                    ),
                )
                self._tx_commit()
            except Exception:
                self._tx_rollback()
                raise

    # ------------------------------------------------------------------
    # Crash / Power-Outage Boot Reconciliation (AUD-14 / T08, T09)
    # ------------------------------------------------------------------
    def reconcile_on_boot(self, *, now_utc: datetime | None = None) -> list[str]:
        """Transition any unclosed sessions to RECOVERY_REQUIRED (never READY) and preserve cooldowns."""
        now_s = _utc_iso(now_utc or datetime.now(timezone.utc))
        recovered_accounts: list[str] = []
        with self._lock:
            try:
                self._tx_begin()
                # 1. Mark any in-flight DISPATCHING / PREPARED commands as UNKNOWN / REJECTED
                self._conn.execute(
                    """
                    UPDATE command_journal
                       SET journal_state = ?, transport_status = 'CONNECTION_ERROR',
                           effect_status = ?, diagnostic_note = 'Crash recovery: outcome unknown',
                           updated_at = ?
                     WHERE journal_state = ?
                    """,
                    (
                        CommandJournalState.UNKNOWN.value,
                        CommandJournalState.UNKNOWN.value,
                        now_s,
                        CommandJournalState.DISPATCHING.value,
                    ),
                )
                self._conn.execute(
                    """
                    UPDATE command_journal
                       SET journal_state = ?, transport_status = 'NOT_SENT',
                           effect_status = ?, diagnostic_note = 'Crash recovery: aborted prior to dispatch',
                           updated_at = ?
                     WHERE journal_state = ?
                    """,
                    (
                        CommandJournalState.REJECTED.value,
                        CommandJournalState.REJECTED.value,
                        now_s,
                        CommandJournalState.PREPARED.value,
                    ),
                )

                # 2. Move any account with an active lease or unclosed state to RECOVERY_REQUIRED
                unclosed_values = tuple(s.value for s in UNCLOSED_SESSION_STATES)
                placeholders = ",".join("?" for _ in unclosed_values)
                rows = self._conn.execute(
                    f"""
                    SELECT account_id, status FROM accounts
                     WHERE status IN ({placeholders})
                        OR account_id IN (SELECT account_id FROM active_leases)
                    """,
                    unclosed_values,
                ).fetchall()

                for r in rows:
                    acc_id = r["account_id"]
                    unknown_cmd = self._conn.execute(
                        "SELECT 1 FROM command_journal WHERE account_id = ? AND journal_state = ?",
                        (acc_id, CommandJournalState.UNKNOWN.value),
                    ).fetchone()
                    reason = (
                        ReasonCode.UNKNOWN_COMMAND_EFFECT
                        if unknown_cmd is not None
                        else ReasonCode.CRASH_UNCLOSED_SESSION
                    )
                    self._conn.execute("DELETE FROM active_leases WHERE account_id = ?", (acc_id,))
                    self._conn.execute(
                        """
                        UPDATE accounts
                           SET status = ?, reason_code = ?, state_version = state_version + 1, updated_at = ?
                         WHERE account_id = ?
                        """,
                        (AccountStatus.RECOVERY_REQUIRED.value, reason.value, now_s, acc_id),
                    )
                    recovered_accounts.append(acc_id)

                self._tx_commit()
                return sorted(recovered_accounts)
            except Exception:
                self._tx_rollback()
                raise

    def reconcile_orphaned_leases(
        self,
        *,
        now_utc: datetime | None = None,
        target_account_id: str | None = None,
        stale_heartbeat_sec: float = 90.0,
        pid_alive_fn: Callable[[int | None], bool] = is_process_alive,
    ) -> list[str]:
        """Multi-slot-safe crash sweeper: reclaim ONLY dead/stale sessions without disturbing live slots.

        An account/lease is considered orphaned after power loss, SIGKILL, or terminal crash when:
          1. Account sits in `UNCLOSED_SESSION_STATES` with no row in `active_leases`; OR
          2. Its `active_leases.owner_pid` is NULL or `pid_alive_fn(owner_pid)` is False; OR
          3. Its `heartbeat_at` (or `acquired_at`) is older than `stale_heartbeat_sec` (guards PID reuse).
        """
        now_dt = now_utc or datetime.now(timezone.utc)
        now_s = _utc_iso(now_dt)
        recovered_accounts: list[str] = []

        with self._lock:
            try:
                self._tx_begin()
                unclosed_values = tuple(s.value for s in UNCLOSED_SESSION_STATES)
                placeholders = ",".join("?" for _ in unclosed_values)
                params: list[Any] = list(unclosed_values)
                target_clause = ""
                if target_account_id is not None:
                    target_clause = " AND a.account_id = ?"
                    params.append(target_account_id)

                rows = self._conn.execute(
                    f"""
                    SELECT a.account_id, a.status,
                           l.lease_id, l.owner_pid, l.heartbeat_at, l.acquired_at
                      FROM accounts a
                      LEFT JOIN active_leases l ON l.account_id = a.account_id
                     WHERE (a.status IN ({placeholders}) OR l.lease_id IS NOT NULL)
                    {target_clause}
                    """,
                    params,
                ).fetchall()

                for r in rows:
                    acc_id = str(r["account_id"])
                    lease_id = r["lease_id"]
                    is_orphan = False
                    if lease_id is None:
                        is_orphan = True
                    else:
                        owner_pid = int(r["owner_pid"]) if r["owner_pid"] is not None else None
                        if owner_pid is None or not pid_alive_fn(owner_pid):
                            is_orphan = True
                        elif stale_heartbeat_sec > 0.0:
                            ts_ref = r["heartbeat_at"] or r["acquired_at"]
                            if ts_ref:
                                try:
                                    age = (now_dt - _parse_iso(str(ts_ref))).total_seconds()
                                    if age > stale_heartbeat_sec:
                                        is_orphan = True
                                except Exception:
                                    is_orphan = True

                    if not is_orphan:
                        continue

                    self._conn.execute(
                        """
                        UPDATE command_journal
                           SET journal_state = ?, transport_status = 'CONNECTION_ERROR',
                               effect_status = ?, diagnostic_note = 'Orphan recovery: outcome unknown',
                               updated_at = ?
                         WHERE account_id = ? AND journal_state = ?
                        """,
                        (
                            CommandJournalState.UNKNOWN.value,
                            CommandJournalState.UNKNOWN.value,
                            now_s,
                            acc_id,
                            CommandJournalState.DISPATCHING.value,
                        ),
                    )
                    self._conn.execute(
                        """
                        UPDATE command_journal
                           SET journal_state = ?, transport_status = 'NOT_SENT',
                               effect_status = ?, diagnostic_note = 'Orphan recovery: aborted prior to dispatch',
                               updated_at = ?
                         WHERE account_id = ? AND journal_state = ?
                        """,
                        (
                            CommandJournalState.REJECTED.value,
                            CommandJournalState.REJECTED.value,
                            now_s,
                            acc_id,
                            CommandJournalState.PREPARED.value,
                        ),
                    )

                    unknown_cmd = self._conn.execute(
                        "SELECT 1 FROM command_journal WHERE account_id = ? AND journal_state = ?",
                        (acc_id, CommandJournalState.UNKNOWN.value),
                    ).fetchone()
                    reason = (
                        ReasonCode.UNKNOWN_COMMAND_EFFECT
                        if unknown_cmd is not None
                        else ReasonCode.CRASH_UNCLOSED_SESSION
                    )

                    self._conn.execute("DELETE FROM active_leases WHERE account_id = ?", (acc_id,))
                    cur_status = AccountStatus(r["status"])
                    if cur_status != AccountStatus.RECOVERY_REQUIRED:
                        if cur_status in UNCLOSED_SESSION_STATES or cur_status == AccountStatus.RECONCILING:
                            assert_valid_account_transition(cur_status, AccountStatus.RECOVERY_REQUIRED)
                            self._conn.execute(
                                """
                                UPDATE accounts
                                   SET status = ?, reason_code = ?, state_version = state_version + 1, updated_at = ?
                                 WHERE account_id = ?
                                """,
                                (AccountStatus.RECOVERY_REQUIRED.value, reason.value, now_s, acc_id),
                            )
                    recovered_accounts.append(acc_id)

                self._tx_commit()
                return sorted(recovered_accounts)
            except Exception:
                self._tx_rollback()
                raise

    def acknowledge_crash_recovery(
        self,
        account_id: str,
        *,
        diagnostic_note: str = "Crash recovery acknowledged; world state reconciled",
        now_utc: datetime | None = None,
    ) -> dict[str, Any]:
        """Complete legal `RECOVERY_REQUIRED -> RECONCILING -> READY` transition while preserving cooldowns."""
        now_dt = now_utc or datetime.now(timezone.utc)
        now_s = _utc_iso(now_dt)
        clean_note = self.redactor.redact_text(diagnostic_note)

        with self._lock:
            acc = self.get_account(account_id)
            cur = AccountStatus(acc["status"])
            if cur not in (AccountStatus.RECOVERY_REQUIRED, AccountStatus.RECONCILING):
                raise StaleContextError(
                    f"Account {account_id} is in {cur.value}; expected RECOVERY_REQUIRED or RECONCILING"
                )

            existing_lease = self._conn.execute(
                "SELECT * FROM active_leases WHERE account_id = ?", (account_id,)
            ).fetchone()

            if existing_lease is None:
                # Pick a free high reconcile slot (>= 900) so active fleet slots 0..15 never collide
                occupied_slots = {
                    int(r["slot_id"])
                    for r in self._conn.execute("SELECT slot_id FROM active_leases").fetchall()
                }
                rec_slot = 900
                while rec_slot in occupied_slots:
                    rec_slot += 1
                lease = self.acquire_session_lease(
                    account_id=account_id,
                    slot_id=rec_slot,
                    task_id="CRASH_RECONCILE",
                    now_utc=now_dt,
                    session_kind="RECONCILE",
                    bypass_circadian=True,
                )
            else:
                lease = dict(existing_lease)

            try:
                self._tx_begin()
                self._conn.execute(
                    """
                    UPDATE command_journal
                       SET journal_state = ?, effect_status = ?, diagnostic_note = ?, updated_at = ?
                     WHERE account_id = ? AND journal_state = ?
                    """,
                    (
                        CommandJournalState.FAILED_VERIFIED.value,
                        CommandJournalState.FAILED_VERIFIED.value,
                        clean_note,
                        now_s,
                        account_id,
                        CommandJournalState.UNKNOWN.value,
                    ),
                )
                self._conn.execute(
                    "UPDATE active_leases SET has_unknown_command = 0 WHERE lease_id = ?",
                    (lease["lease_id"],),
                )
                self._tx_commit()
            except Exception:
                self._tx_rollback()
                raise

            self.close_session(
                account_id=account_id,
                session_id=str(lease["session_id"]),
                lease_id=str(lease["lease_id"]),
                session_epoch=int(lease["session_epoch"]),
                target_status=AccountStatus.READY,
                reason_code=ReasonCode.NONE,
                now_utc=now_dt,
            )
            updated_acc = self.get_account(account_id)

        if self.on_crash_recovered is not None:
            try:
                self.on_crash_recovered(updated_acc, clean_note)
            except Exception:
                pass
        return updated_acc

    # ------------------------------------------------------------------
    # Universal Account Profile Persistence (`PR-17` / `AccountProfileSnapshot`)
    # ------------------------------------------------------------------
    def save_account_profile(
        self,
        profile: dict[str, Any],
        *,
        now_utc: datetime | None = None,
    ) -> dict[str, Any]:
        """Atomically persist a discovered `AccountProfileSnapshot` and sync `accounts` progression."""
        acc_id = str(profile["account_id"])
        clean_prof = self.redactor.redact_obj(dict(profile))
        now_s = _utc_iso(now_utc or datetime.now(timezone.utc))
        scanned_at = str(clean_prof.get("scanned_at") or now_s)

        tut_281 = int(clean_prof.get("tutorial_varp_281", 0))
        qp = int(clean_prof.get("quest_points", 0))
        ttl = int(clean_prof.get("total_level", 32))
        play_mins = int(clean_prof.get("playtime_minutes", 0))
        stage, trade_unlocked = compute_progression_stage(
            tutorial_varp_281=tut_281,
            quest_points=qp,
            total_level=ttl,
            playtime_minutes=play_mins,
        )

        with self._lock:
            _ = self.get_account(acc_id)
            try:
                self._tx_begin()
                self._conn.execute(
                    """
                    INSERT INTO account_profiles (
                        account_id, combat_level, total_level, is_member, membership_days,
                        tutorial_varp_281, quest_points, playtime_minutes,
                        inventory_coins_gp, bank_coins_gp, total_liquid_coins_gp, bank_scanned,
                        skills_json, skill_xp_json, completed_quests_json, stronghold_floors_json,
                        available_tools_json, available_gear_json, available_food_json,
                        inventory_json, bank_json, eligible_recipes_json, position_json,
                        scan_duration_ms, scanned_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account_id) DO UPDATE SET
                        combat_level = excluded.combat_level,
                        total_level = excluded.total_level,
                        is_member = excluded.is_member,
                        membership_days = excluded.membership_days,
                        tutorial_varp_281 = excluded.tutorial_varp_281,
                        quest_points = excluded.quest_points,
                        playtime_minutes = excluded.playtime_minutes,
                        inventory_coins_gp = excluded.inventory_coins_gp,
                        bank_coins_gp = excluded.bank_coins_gp,
                        total_liquid_coins_gp = excluded.total_liquid_coins_gp,
                        bank_scanned = excluded.bank_scanned,
                        skills_json = excluded.skills_json,
                        skill_xp_json = excluded.skill_xp_json,
                        completed_quests_json = excluded.completed_quests_json,
                        stronghold_floors_json = excluded.stronghold_floors_json,
                        available_tools_json = excluded.available_tools_json,
                        available_gear_json = excluded.available_gear_json,
                        available_food_json = excluded.available_food_json,
                        inventory_json = excluded.inventory_json,
                        bank_json = excluded.bank_json,
                        eligible_recipes_json = excluded.eligible_recipes_json,
                        position_json = excluded.position_json,
                        scan_duration_ms = excluded.scan_duration_ms,
                        scanned_at = excluded.scanned_at
                    """,
                    (
                        acc_id,
                        int(clean_prof.get("combat_level", 3)),
                        ttl,
                        1 if bool(clean_prof.get("is_member", False)) else 0,
                        int(clean_prof.get("membership_days", 0)),
                        tut_281,
                        qp,
                        play_mins,
                        int(clean_prof.get("inventory_coins_gp", 0)),
                        int(clean_prof.get("bank_coins_gp", 0)),
                        int(clean_prof.get("total_liquid_coins_gp", 0)),
                        1 if bool(clean_prof.get("bank_scanned", False)) else 0,
                        json.dumps(clean_prof.get("skills") or {}, ensure_ascii=False),
                        json.dumps(clean_prof.get("skill_xp") or {}, ensure_ascii=False),
                        json.dumps(clean_prof.get("completed_quests") or [], ensure_ascii=False),
                        json.dumps(clean_prof.get("stronghold_floors_claimed") or [], ensure_ascii=False),
                        json.dumps(clean_prof.get("available_tools") or [], ensure_ascii=False),
                        json.dumps(clean_prof.get("available_gear") or [], ensure_ascii=False),
                        json.dumps(clean_prof.get("available_food") or {}, ensure_ascii=False),
                        json.dumps(clean_prof.get("inventory_summary") or [], ensure_ascii=False),
                        json.dumps(clean_prof.get("bank_summary") or [], ensure_ascii=False),
                        json.dumps(clean_prof.get("eligible_recipes") or [], ensure_ascii=False),
                        json.dumps(clean_prof.get("position") or {}, ensure_ascii=False),
                        int(clean_prof.get("scan_duration_ms", 0)),
                        scanned_at,
                    ),
                )
                self._conn.execute(
                    """
                    UPDATE accounts
                       SET tutorial_varp_281 = ?,
                           quest_points = ?,
                           total_level = ?,
                           playtime_minutes = ?,
                           stage = ?,
                           trade_unlocked = ?,
                           state_version = state_version + 1,
                           updated_at = ?
                     WHERE account_id = ?
                    """,
                    (
                        tut_281,
                        qp,
                        ttl,
                        play_mins,
                        stage,
                        1 if trade_unlocked else 0,
                        now_s,
                        acc_id,
                    ),
                )
                self._tx_commit()
            except Exception:
                self._tx_rollback()
                raise
            stored = self.get_account_profile(acc_id)
            assert stored is not None
            return stored

    def get_account_profile(self, account_id: str) -> dict[str, Any] | None:
        """Return deserialized `AccountProfileSnapshot` dictionary from `account_profiles`, or `None`."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM account_profiles WHERE account_id = ?",
                (account_id,),
            ).fetchone()
            if row is None:
                return None
            tut_281 = int(row["tutorial_varp_281"])
            qp = int(row["quest_points"])
            ttl = int(row["total_level"])
            play_mins = int(row["playtime_minutes"])
            stage, trade_unlocked = compute_progression_stage(
                tutorial_varp_281=tut_281,
                quest_points=qp,
                total_level=ttl,
                playtime_minutes=play_mins,
            )
            return {
                "account_id": str(row["account_id"]),
                "combat_level": int(row["combat_level"]),
                "total_level": ttl,
                "skills": json.loads(str(row["skills_json"])),
                "skill_xp": json.loads(str(row["skill_xp_json"])),
                "is_member": bool(row["is_member"]),
                "membership_days": int(row["membership_days"]),
                "tutorial_varp_281": tut_281,
                "quest_points": qp,
                "completed_quests": json.loads(str(row["completed_quests_json"])),
                "stronghold_floors_claimed": json.loads(str(row["stronghold_floors_json"])),
                "playtime_minutes": play_mins,
                "progression_stage": stage,
                "trade_unlocked": trade_unlocked,
                "position": json.loads(str(row["position_json"])),
                "inventory_coins_gp": int(row["inventory_coins_gp"]),
                "bank_coins_gp": int(row["bank_coins_gp"]),
                "total_liquid_coins_gp": int(row["total_liquid_coins_gp"]),
                "bank_scanned": bool(row["bank_scanned"]),
                "available_tools": json.loads(str(row["available_tools_json"])),
                "available_gear": json.loads(str(row["available_gear_json"])),
                "available_food": json.loads(str(row["available_food_json"])),
                "inventory_summary": json.loads(str(row["inventory_json"])),
                "bank_summary": json.loads(str(row["bank_json"])),
                "eligible_recipes": json.loads(str(row["eligible_recipes_json"])),
                "scan_duration_ms": int(row["scan_duration_ms"]),
                "scanned_at": str(row["scanned_at"]),
            }

    # ------------------------------------------------------------------
    # RBAC & Single-Use Bound Approvals (AUD-04 / T03, T09)
    # ------------------------------------------------------------------
    def grant_operator_role(self, *, user_id: int, chat_id: int, role: OperatorRole | str) -> None:
        role_enum = OperatorRole(role)
        with self._lock:
            try:
                self._tx_begin()
                self._conn.execute(
                    """
                    INSERT INTO operator_acl (user_id, chat_id, role)
                    VALUES (?, ?, ?)
                    ON CONFLICT(user_id, chat_id) DO UPDATE SET role = excluded.role
                    """,
                    (user_id, chat_id, role_enum.value),
                )
                self._tx_commit()
            except Exception:
                self._tx_rollback()
                raise

    def authorize_operator(self, *, user_id: int, chat_id: int, minimum_role: OperatorRole) -> OperatorRole:
        with self._lock:
            row = self._conn.execute(
                "SELECT role FROM operator_acl WHERE user_id = ? AND chat_id = ?",
                (user_id, chat_id),
            ).fetchone()
            if row is None:
                raise AuthorizationDeniedError("Default-Deny: unknown (user_id, chat_id) pair")
            actual = OperatorRole(row["role"])
            if ROLE_RANK[actual] < ROLE_RANK[minimum_role]:
                raise AuthorizationDeniedError(
                    f"Role {actual.value} insufficient; requires {minimum_role.value}"
                )
            return actual

    def create_approval_request(
        self,
        *,
        incident_id: str,
        account_id: str,
        plan_hash: str,
        expires_at: datetime,
    ) -> str:
        with self._lock:
            try:
                self._tx_begin()
                acc = self._conn.execute(
                    "SELECT status, session_epoch, state_version FROM accounts WHERE account_id = ?",
                    (account_id,),
                ).fetchone()
                if acc is None:
                    raise KeyError(account_id)
                if acc["status"] != AccountStatus.BLOCKED.value:
                    raise ApprovalRejectedError("Approvals can only be issued for BLOCKED accounts")
                approval_id = f"appr_{uuid.uuid4().hex[:12]}"
                self._conn.execute(
                    """
                    INSERT INTO approvals (
                        approval_id, incident_id, account_id, session_epoch,
                        plan_hash, expected_state_version, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval_id,
                        incident_id,
                        account_id,
                        acc["session_epoch"],
                        plan_hash,
                        acc["state_version"],
                        _utc_iso(expires_at),
                    ),
                )
                self._tx_commit()
                return approval_id
            except Exception:
                self._tx_rollback()
                raise

    def consume_approval(
        self,
        *,
        approval_id: str,
        user_id: int,
        chat_id: int,
        plan_hash: str,
        now_utc: datetime,
    ) -> dict[str, Any]:
        """Atomically consume a bound approval once and move account BLOCKED -> RECONCILING (never READY)."""
        with self._lock:
            self.authorize_operator(
                user_id=user_id, chat_id=chat_id, minimum_role=OperatorRole.OPERATOR
            )
            try:
                self._tx_begin()
                appr = self._conn.execute(
                    "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
                ).fetchone()
                if appr is None:
                    raise ApprovalRejectedError("Unknown approval_id")
                if appr["consumed_at"] is not None:
                    raise ApprovalRejectedError("Approval already consumed (replay rejected)")
                if _parse_iso(appr["expires_at"]) <= now_utc:
                    raise ApprovalRejectedError("Approval expired")
                if appr["plan_hash"] != plan_hash:
                    raise ApprovalRejectedError("Approval plan_hash mismatch")

                acc = self._conn.execute(
                    "SELECT * FROM accounts WHERE account_id = ?", (appr["account_id"],)
                ).fetchone()
                if (
                    int(acc["session_epoch"]) != int(appr["session_epoch"])
                    or int(acc["state_version"]) != int(appr["expected_state_version"])
                ):
                    raise ApprovalRejectedError("Approval invalidated by newer account state/epoch")

                assert_valid_account_transition(
                    AccountStatus(acc["status"]), AccountStatus.RECONCILING
                )
                now_s = _utc_iso(now_utc)
                self._conn.execute(
                    "UPDATE approvals SET consumed_at = ?, consumed_by_user_id = ? WHERE approval_id = ?",
                    (now_s, user_id, approval_id),
                )
                self._conn.execute(
                    """
                    UPDATE accounts
                       SET status = ?, state_version = state_version + 1, updated_at = ?
                     WHERE account_id = ?
                    """,
                    (AccountStatus.RECONCILING.value, now_s, appr["account_id"]),
                )
                self._tx_commit()
                return self.get_account(appr["account_id"])
            except Exception:
                self._tx_rollback()
                raise
