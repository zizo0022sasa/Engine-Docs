"""Unified Cross-Platform CLI for OSRS_ee (`python -m osrs_ee` / `osrs-ee`).

Supports Windows (PowerShell/CMD) and Linux (Bash/Xvfb) using 100% Python stdlib.
Subcommands:
  - doctor          : Check Python, Java, Xvfb, host RAM slot capacity, <500MB quota, and secret scan.
  - init            : Initialize local `.osrs_ee_data/` (SQLite WAL state, 0600 vault, outbox, GE ledger, logs).
  - ingest          : Ingest Legacy (`email|pass|proxy|region`) & Jagex (`jx_sess|jx_char|proxy|region`) accounts safely.
  - grant-operator  : Grant VIEWER / OPERATOR / ADMIN RBAC permissions for Telegram control.
  - status          : Print sanitized fleet status, GE PnL, and RAM slot capacity (zero secrets).
  - plan-stagger    : Generate deterministic >=90s staggered launch timestamps across accounts.
  - simulate-pilot  : Run an end-to-end hermetic pilot session (`FakeGameAdapter`) to verify installation locally.
  - dry-run-slot    : Prepare isolated sandbox + loopback SOCKS5h sidecar + print exact JVM command without launching.
  - launch-slot     : Launch and supervise a live isolated Microbot/RuneLite JVM slot with graceful Ctrl+C DRAIN.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from osrs_ee.economy import GEOrderLedger
from osrs_ee.network import LocalSocks5Sidecar
from osrs_ee.orchestrator import RssCapacityCalculator, StaggeredLaunchPlanner
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.runtime.supervisor import SingleSlotSupervisor
from osrs_ee.sandbox import (
    ManagedProcessTree,
    MicrobotHttpAdapter,
    SandboxProfileSeeder,
    build_isolated_jvm_command,
    resolve_regional_world_pool,
)
from osrs_ee.security.redaction import Redactor
from osrs_ee.security.secret_scan import scan_path
from osrs_ee.security.vault import LocalSecretVault, ingest_pipe_lines
from osrs_ee.state import AccountStatus, OperatorRole, StateStore
from osrs_ee.tasks import CuratedRecipeRegistry, SingleSlotPilotRunner
from osrs_ee.telemetry import GLOBAL_STORAGE_BUDGET_BYTES, BoundedOutbox, LogQuotaGuardian


def _resolve_data_dir(raw_dir: str | None) -> Path:
    return Path(raw_dir or os.environ.get("OSRS_EE_DATA_DIR", ".osrs_ee_data")).resolve()


def _cmd_doctor(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    java_bin = shutil.which(args.java_bin)
    java_version = None
    if java_bin:
        try:
            proc = subprocess.run(
                [java_bin, "-version"], capture_output=True, text=True, timeout=5, check=False
            )
            out = (proc.stderr or proc.stdout).splitlines()
            java_version = out[0] if out else "detected"
        except Exception as exc:
            java_version = f"error: {type(exc).__name__}"

    xvfb_bin = shutil.which("xvfb-run") if os.name != "nt" else "N/A (Windows native GUI)"
    calc = RssCapacityCalculator()
    cap = calc.compute_safe_slots()
    worst_log_bytes = LogQuotaGuardian.calculate_worst_case_budget_bytes(max_accounts=15)
    findings = scan_path(repo_root)

    report = {
        "status": "HEALTHY" if not findings else "SECRET_ALERT",
        "platform": f"{platform.system()} {platform.release()} ({os.name})",
        "python_version": platform.python_version(),
        "java_binary": java_bin or "NOT_FOUND (Install JDK 17+ before live launch-slot)",
        "java_version": java_version,
        "headless_display": xvfb_bin or "NOT_FOUND (Install xvfb on headless Linux VPS)",
        "host_ram_mb": cap.total_ram_mb,
        "recommended_safe_concurrent_slots": cap.computed_safe_slots,
        "worst_case_storage_mb": round(worst_log_bytes / (1024 * 1024), 1),
        "storage_ceiling_mb": GLOBAL_STORAGE_BUDGET_BYTES // (1024 * 1024),
        "secret_scan_findings": len(findings),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if not findings else 1


def _cmd_init(args: argparse.Namespace) -> int:
    data_dir = _resolve_data_dir(args.data_dir)
    (data_dir / "sandboxes").mkdir(parents=True, exist_ok=True)
    guardian = LogQuotaGuardian(data_dir / "logs")
    vault = LocalSecretVault(data_dir / "vault.json")
    store = StateStore(
        data_dir / "state.db",
        snapshot_path=data_dir / "state_snapshot.json",
        redactor=vault.redactor,
    )
    outbox = BoundedOutbox(data_dir / "outbox.db", redactor=vault.redactor)
    ge = GEOrderLedger(data_dir / "ge.db")
    store.export_snapshot()
    ge.close()
    outbox.close()
    store.close()
    print(
        json.dumps(
            {
                "initialized": True,
                "data_dir": str(data_dir),
                "vault_path": str(data_dir / "vault.json"),
                "state_db": str(data_dir / "state.db"),
                "snapshot_path": str(data_dir / "state_snapshot.json"),
                "triage_dir": str(guardian.triage_dir),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    data_dir = _resolve_data_dir(args.data_dir)
    in_file = Path(args.file).resolve()
    if not in_file.is_file():
        print(json.dumps({"error": f"Accounts file not found: {in_file}"}))
        return 2

    vault = LocalSecretVault(data_dir / "vault.json")
    store = StateStore(
        data_dir / "state.db",
        snapshot_path=data_dir / "state_snapshot.json",
        redactor=vault.redactor,
    )
    try:
        lines = in_file.read_text(encoding="utf-8").splitlines()
        summaries = ingest_pipe_lines(lines, vault=vault, store=store)
        store.export_snapshot()
        payload = {
            "ingested_count": len(summaries),
            "accounts": [
                {
                    "account_id": s.account_id,
                    "auth_mode": s.auth_mode,
                    "region": s.region,
                    "timezone": s.timezone,
                    "allowed_f2p_worlds": resolve_regional_world_pool(s.region),
                    "credential_ref": s.credential_ref,
                    "proxy_ref": s.proxy_ref,
                }
                for s in summaries
            ],
        }
        print(vault.redactor.redact_text(json.dumps(payload, indent=2, ensure_ascii=False)))
        return 0
    finally:
        store.close()


def _cmd_grant_operator(args: argparse.Namespace) -> int:
    data_dir = _resolve_data_dir(args.data_dir)
    store = StateStore(data_dir / "state.db", snapshot_path=data_dir / "state_snapshot.json")
    try:
        role = OperatorRole(args.role.upper())
        store.grant_operator_role(user_id=args.user_id, chat_id=args.chat_id, role=role)
        print(
            json.dumps(
                {"granted": True, "user_id": args.user_id, "chat_id": args.chat_id, "role": role.value},
                indent=2,
            )
        )
        return 0
    finally:
        store.close()


def _cmd_status(args: argparse.Namespace) -> int:
    data_dir = _resolve_data_dir(args.data_dir)
    if not (data_dir / "state.db").exists():
        print(json.dumps({"initialized": False, "hint": "Run `python -m osrs_ee init` first."}))
        return 0

    redactor = LocalSecretVault(data_dir / "vault.json").redactor if (data_dir / "vault.json").exists() else Redactor()
    store = StateStore(data_dir / "state.db", snapshot_path=data_dir / "state_snapshot.json", redactor=redactor)
    ge = GEOrderLedger(data_dir / "ge.db")
    try:
        rows = store._conn.execute(
            "SELECT account_id, auth_mode, region, status, reason_code, stage, quest_points, total_level, playtime_minutes, trade_unlocked, realized_gp FROM accounts ORDER BY account_id"
        ).fetchall()
        accounts_out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["ge_pnl"] = ge.account_pnl_summary(str(r["account_id"]))
            accounts_out.append(d)

        cap = RssCapacityCalculator().compute_safe_slots()
        out = {
            "total_accounts": len(accounts_out),
            "safe_concurrent_slots": cap.computed_safe_slots,
            "accounts": accounts_out,
        }
        print(redactor.redact_text(json.dumps(out, indent=2, ensure_ascii=False)))
        return 0
    finally:
        ge.close()
        store.close()


def _cmd_plan_stagger(args: argparse.Namespace) -> int:
    accs = [a.strip() for a in args.accounts.split(",") if a.strip()]
    planner = StaggeredLaunchPlanner()
    plan = planner.plan_launches(accs, base_epoch_sec=0.0)
    print(
        json.dumps(
            {
                "min_stagger_sec": planner.min_stagger_sec,
                "relative_launch_offsets_sec": plan,
            },
            indent=2,
        )
    )
    return 0


def _cmd_simulate_pilot(args: argparse.Namespace) -> int:
    data_dir = _resolve_data_dir(args.data_dir)
    (data_dir / "sandboxes").mkdir(parents=True, exist_ok=True)
    vault = LocalSecretVault(data_dir / "vault.json")
    store = StateStore(
        data_dir / "state.db",
        snapshot_path=data_dir / "state_snapshot.json",
        redactor=vault.redactor,
    )
    try:
        acc_id = args.account_id
        try:
            store.get_account(acc_id)
        except KeyError:
            store.register_account(
                account_id=acc_id,
                auth_mode="LEGACY_CREDENTIALS",
                credential_ref=f"vault://accounts/{acc_id}/auth",
                proxy_ref="vault://proxies/sim_loopback",
                region="EG",
                timezone_name="Africa/Cairo",
                persona_seed=20261023,
                initial_status=AccountStatus.PENDING_DISCOVERY,
            )

        runner = SingleSlotPilotRunner(store=store)

        def _make_fake(lease: dict[str, Any]) -> FakeGameAdapter:
            fa = FakeGameAdapter(
                account_id=acc_id,
                session_id=lease["session_id"],
                session_epoch=lease["session_epoch"],
            )
            fa.tutorial_varp_281 = 1000
            fa.skills = {"woodcutting": 35}
            fa.bank_items = {"Willow logs": 0, "Rune axe": 1}
            return fa

        res = runner.run_session(
            account_id=acc_id,
            recipe_id=args.recipe_id,
            adapter_factory=_make_fake,
            stronghold_dialogues=(
                [("To pass you must answer me this: How do I set up two-factor?", ["Set up 2 step authentication with my email or authenticator."])]
                if args.recipe_id == "stage2_stronghold_10k"
                else None
            ),
        )
        store.export_snapshot()
        print(
            json.dumps(
                {
                    "ok": res.ok,
                    "exit_code": res.exit_code,
                    "account_id": res.account_id,
                    "recipe_id": res.recipe_id,
                    "final_account_status": res.final_account_status,
                    "detail": res.detail,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return res.exit_code
    finally:
        store.close()


def _cmd_dry_run_or_launch_slot(args: argparse.Namespace, *, dry_run: bool) -> int:
    data_dir = _resolve_data_dir(args.data_dir)
    vault = LocalSecretVault(data_dir / "vault.json")
    store = StateStore(
        data_dir / "state.db",
        snapshot_path=data_dir / "state_snapshot.json",
        redactor=vault.redactor,
    )
    try:
        acc = store.get_account(args.account_id)
        proxy_secret = vault.resolve_secret(acc["proxy_ref"])
        cred_secret = vault.resolve_secret(acc["credential_ref"])

        sidecar = LocalSocks5Sidecar(
            upstream_proxy_uri=proxy_secret["proxy_uri"],
            pinned_region=acc["region"],
        )
        sidecar.start()
        try:
            seeder = SandboxProfileSeeder(
                data_dir / "sandboxes",
                golden_cache_dir=args.golden_cache_dir or None,
            )
            bootstrap, adapter_token = seeder.prepare_sandbox(
                account_id=args.account_id,
                persona_seed=int(acc["persona_seed"]),
                adapter_port=args.adapter_port,
                local_socks5_port=sidecar.listen_port,
            )
            jvm_cmd = build_isolated_jvm_command(
                java_bin=args.java_bin,
                client_jar=args.client_jar,
                bootstrap=bootstrap,
                hwid_agent_jar=args.hwid_agent_jar or None,
            )

            if dry_run:
                out = {
                    "mode": "DRY_RUN_VERIFIED",
                    "account_id": args.account_id,
                    "auth_mode": acc["auth_mode"],
                    "region": acc["region"],
                    "allowed_f2p_worlds": resolve_regional_world_pool(acc["region"]),
                    "sandbox_user_home": str(bootstrap.user_home),
                    "local_socks5_sidecar": f"socks5://127.0.0.1:{sidecar.listen_port}",
                    "jvm_command": jvm_cmd,
                }
                print(vault.redactor.redact_text(json.dumps(out, indent=2, ensure_ascii=False)))
                return 0

            # Live launch mode on operator host!
            env_vars: dict[str, str] = {}
            if acc["auth_mode"] == "JAGEX_SESSION":
                env_vars["JX_SESSION_ID"] = str(cred_secret["jx_session_id"])
                env_vars["JX_CHARACTER_ID"] = str(cred_secret["jx_character_id"])
                env_vars["JX_DISPLAY_NAME"] = str(cred_secret.get("jx_display_name", args.account_id))
            else:
                env_vars["OSRS_LEGACY_LOGIN"] = str(cred_secret["email"])
                env_vars["OSRS_LEGACY_PASSWORD"] = str(cred_secret["password"])

            proc_tree = ManagedProcessTree(
                jvm_cmd,
                cwd=bootstrap.sandbox_root,
                extra_env=env_vars,
                use_xvfb_if_headless=bool(args.use_xvfb),
            )
            supervisor = SingleSlotSupervisor(store=store)
            lease = store.acquire_session_lease(
                account_id=args.account_id,
                slot_id=args.slot_id,
                task_id=args.recipe_id,
            )
            proc_tree.start()
            adapter = MicrobotHttpAdapter(
                port=bootstrap.adapter_port,
                token=adapter_token,
                account_id=args.account_id,
                session_id=lease["session_id"],
                session_epoch=lease["session_epoch"],
            )
            print(
                vault.redactor.redact_text(
                    json.dumps(
                        {
                            "launched": True,
                            "pid": proc_tree.pid,
                            "account_id": args.account_id,
                            "adapter_port": bootstrap.adapter_port,
                            "sidecar_port": sidecar.listen_port,
                        },
                        indent=2,
                    )
                )
            )
            try:
                while proc_tree.is_running():
                    store.renew_session_lease(
                        session_id=lease["session_id"], session_epoch=lease["session_epoch"]
                    )
                    time.sleep(5.0)
            except KeyboardInterrupt:
                supervisor.abort()
            finally:
                _ = adapter
                proc_tree.terminate_tree()
                store.export_snapshot()
            return 0
        finally:
            sidecar.stop()
    finally:
        store.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="osrs-ee",
        description="OSRS_ee — Unified Cross-Platform Execution, Security & Orchestration CLI",
    )
    parser.add_argument("--data-dir", default=None, help="Local runtime directory (default: .osrs_ee_data)")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p_doc = sub.add_parser("doctor", help="Check system prerequisites, RAM capacity, and secret hygiene")
    p_doc.add_argument("--repo-root", default=".")
    p_doc.add_argument("--java-bin", default="java")

    sub.add_parser("init", help="Initialize local SQLite WAL databases, 0600 vault, and log directories")

    p_ing = sub.add_parser("ingest", help="Ingest Legacy/Jagex pipe-delimited accounts into LocalSecretVault")
    p_ing.add_argument("--file", required=True, help="Path to local accounts.txt (ignored by git)")

    p_op = sub.add_parser("grant-operator", help="Authorize a Telegram user_id + chat_id with RBAC role")
    p_op.add_argument("--user-id", type=int, required=True)
    p_op.add_argument("--chat-id", type=int, required=True)
    p_op.add_argument("--role", choices=["VIEWER", "OPERATOR", "ADMIN"], default="ADMIN")

    sub.add_parser("status", help="Display sanitized fleet status, GE PnL, and safe slot capacity")

    p_stag = sub.add_parser("plan-stagger", help="Generate >=90s staggered cold-start schedule")
    p_stag.add_argument("--accounts", required=True, help="Comma-separated account IDs (e.g. acc_01,acc_02)")

    p_sim = sub.add_parser("simulate-pilot", help="Run a hermetic end-to-end pilot session locally")
    p_sim.add_argument("--account-id", default="acc_sim_01")
    p_sim.add_argument(
        "--recipe-id",
        default="f2p_draynor_willow",
        choices=list(CuratedRecipeRegistry()._by_id.keys()),
    )

    for name in ("dry-run-slot", "launch-slot"):
        p_slot = sub.add_parser(name, help=f"{'Dry-run' if name == 'dry-run-slot' else 'Launch'} isolated JVM slot")
        p_slot.add_argument("--account-id", required=True)
        p_slot.add_argument("--client-jar", required=True, help="Path to built Microbot RuneLite shaded JAR")
        p_slot.add_argument("--java-bin", default=os.environ.get("JAVA_BIN", "java"))
        p_slot.add_argument("--adapter-port", type=int, default=18081)
        p_slot.add_argument("--slot-id", type=int, default=0)
        p_slot.add_argument("--recipe-id", default="stage2_stronghold_10k")
        p_slot.add_argument("--hwid-agent-jar", default="")
        p_slot.add_argument("--golden-cache-dir", default="")
        p_slot.add_argument("--use-xvfb", action="store_true", help="Run inside xvfb-run on Linux")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cmd = args.subcommand
    if cmd == "doctor":
        return _cmd_doctor(args)
    if cmd == "init":
        return _cmd_init(args)
    if cmd == "ingest":
        return _cmd_ingest(args)
    if cmd == "grant-operator":
        return _cmd_grant_operator(args)
    if cmd == "status":
        return _cmd_status(args)
    if cmd == "plan-stagger":
        return _cmd_plan_stagger(args)
    if cmd == "simulate-pilot":
        return _cmd_simulate_pilot(args)
    if cmd == "dry-run-slot":
        return _cmd_dry_run_or_launch_slot(args, dry_run=True)
    if cmd == "launch-slot":
        return _cmd_dry_run_or_launch_slot(args, dry_run=False)
    return 1
