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
import sys
import threading
import time
from pathlib import Path
from typing import Any

from osrs_ee.bots import BotModuleRegistry, TaskModule
from osrs_ee.core import ExecutionEngineKernel
from osrs_ee.economy import GEOrderLedger
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph
from osrs_ee.network import LocalSocks5Sidecar
from osrs_ee.orchestrator import RssCapacityCalculator, StaggeredLaunchPlanner
from osrs_ee.runtime.adapter_gate import GameAdapterGate
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
from osrs_ee.security.vault import LocalSecretVault, ingest_pipe_lines, load_dotenv
from osrs_ee.state import AccountStatus, OperatorRole, StateStore
from osrs_ee.tasks import CuratedRecipeRegistry, SingleSlotPilotRunner
from osrs_ee.telemetry import GLOBAL_STORAGE_BUDGET_BYTES, BoundedOutbox, LogQuotaGuardian, TelegramGateway


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
        non_comment_count = sum(1 for ln in lines if ln.strip() and not ln.strip().startswith("#"))
        summaries = ingest_pipe_lines(lines, vault=vault, store=store)
        store.export_snapshot()
        total_registered = int(
            store._conn.execute("SELECT COUNT(*) AS c FROM accounts").fetchone()["c"]
        )
        payload = {
            "ingested_count": len(summaries),
            "skipped_duplicates": max(0, non_comment_count - len(summaries)),
            "total_registered_accounts": total_registered,
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


def _resolve_task_module(recipe_id: str) -> TaskModule:
    return BotModuleRegistry().create(recipe_id)


def _read_agent_token_file(token_file: str | None) -> str:
    default_path = Path(os.environ.get("OSRS_AGENT_TOKEN_PATH", str(Path.home() / ".runelite" / ".agent-token")))
    path = Path(token_file).expanduser().resolve() if token_file else default_path
    if not path.exists():
        raise FileNotFoundError(f"Agent token file not found at {path}; start Microbot AgentServer first")
    token = path.read_text(encoding="utf-8").strip()
    if len(token) < 16:
        raise ValueError(f"Token in {path} is shorter than required 16 characters")
    return token


def _cmd_live_probe(args: argparse.Namespace) -> int:
    redactor = Redactor()
    token = _read_agent_token_file(args.token_file)
    redactor.register_secret(token)
    adapter = MicrobotHttpAdapter(
        account_id=args.account_id,
        session_id="live_probe_01",
        session_epoch=1,
        port=int(args.port),
        agent_token=token,
        coalesce_window_ms=150,
    )
    try:
        probe = adapter.probe_session_state()
        obs = adapter.read_observation()
        summary = {
            "ok": True,
            "port": adapter.port,
            "active_status_endpoint": adapter._active_status_endpoint,
            "reachable": probe["reachable"],
            "login_state": obs["login_state"],
            "game_tick": obs["game_tick"],
            "position": obs["position"],
            "vitals": obs.get("vitals"),
            "tcp_connect_count": adapter.tcp_connect_count,
            "http_request_count": adapter.http_request_count,
        }
        print(redactor.redact_text(json.dumps(summary, indent=2, ensure_ascii=False)))
        return 0
    finally:
        adapter.kill_process_tree()


def _cmd_attach_bot(args: argparse.Namespace) -> int:
    data_dir = _resolve_data_dir(args.data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    redactor = Redactor()
    token = _read_agent_token_file(args.token_file)
    redactor.register_secret(token)

    store = StateStore(
        data_dir / "state.db",
        snapshot_path=data_dir / "state_snapshot.json",
        redactor=redactor,
    )
    try:
        try:
            acc = store.get_account(args.account_id)
        except KeyError:
            store.register_account(
                account_id=args.account_id,
                auth_mode="LEGACY_CREDENTIALS",
                credential_ref=f"vault://accounts/{args.account_id}/auth",
                proxy_ref="vault://proxies/local_attached",
                region="EG",
                timezone_name="Africa/Cairo",
                persona_seed=101,
                initial_status=AccountStatus.READY,
            )
            acc = store.get_account(args.account_id)

        from datetime import datetime, timedelta, timezone

        now_utc = datetime.now(timezone.utc)
        cur_state = AccountStatus(acc["status"])
        if cur_state == AccountStatus.RESTING:
            # Interactive operator attach: clear resting cooldown if still active from prior run/simulation
            store._conn.execute(
                "UPDATE accounts SET cooldown_until = ? WHERE account_id = ?",
                ((now_utc - timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ"), args.account_id),
            )
            store._conn.commit()
            store.transition_resting_to_ready_if_due(args.account_id, now_utc)
            acc = store.get_account(args.account_id)
            cur_state = AccountStatus(acc["status"])

        sess_kind = "DISCOVERY" if cur_state == AccountStatus.PENDING_DISCOVERY else "WORK"
        lease = store.acquire_session_lease(
            account_id=args.account_id,
            slot_id=0,
            task_id=args.recipe_id,
            session_kind=sess_kind,
            bypass_circadian=True,
        )
        adapter = MicrobotHttpAdapter(
            account_id=args.account_id,
            session_id=lease["session_id"],
            session_epoch=lease["session_epoch"],
            port=int(args.port),
            agent_token=token,
            coalesce_window_ms=180,
        )
        gate = GameAdapterGate(store, adapter)  # type: ignore[arg-type]
        gate.bind_to_lease(
            account_id=args.account_id,
            session_id=lease["session_id"],
            lease_id=lease["lease_id"],
            session_epoch=lease["session_epoch"],
        )
        caps = AccountCapabilitiesContext(
            account_id=args.account_id,
            skills={"agility": int(args.agility), "hitpoints": 10},
            combat_level=int(args.combat_level),
            quest_points=int(acc["quest_points"] or 0),
            is_member=bool(args.member),
        )
        kernel = ExecutionEngineKernel(
            gate=gate,
            account_id=args.account_id,
            session_id=lease["session_id"],
            lease_id=lease["lease_id"],
            session_epoch=lease["session_epoch"],
            capabilities=caps,
        )
        task_mod = _resolve_task_module(args.recipe_id)
        stop_event = threading.Event()
        max_ticks = max(1, int(args.max_ticks))
        ticks_run = 0
        last_res = None
        last_logged_sig = ""

        try:
            while ticks_run < max_ticks and not stop_event.is_set():
                ticks_run += 1
                t0 = time.monotonic()
                last_res = kernel.step_tick(task_mod, now_monotonic=t0)
                if not getattr(args, "quiet", False) and float(args.tick_interval_sec) > 0:
                    d_name = str(last_res.task_directive.directive_type) if last_res.task_directive else "NONE"
                    sig = f"{last_res.lifecycle_state}|{d_name}|{last_res.detail}"
                    if sig != last_logged_sig or ticks_run % 10 == 0:
                        last_logged_sig = sig
                        msg_line = redactor.redact_text(
                            f"[tick={ticks_run:04d}] state={last_res.lifecycle_state} | dir={d_name} | {last_res.detail}"
                        )
                        print(msg_line, file=sys.stderr, flush=True)
                if last_res.halted:
                    break
                if args.tick_interval_sec > 0:
                    rem = max(0.01, float(args.tick_interval_sec) - (time.monotonic() - t0))
                    stop_event.wait(timeout=rem)
        except KeyboardInterrupt:
            stop_event.set()
        finally:
            adapter.kill_process_tree()
            cur_acc = store.get_account(args.account_id)
            if AccountStatus(cur_acc["status"]) in (
                AccountStatus.RUNNING,
                AccountStatus.STARTING,
                AccountStatus.LEASED,
                AccountStatus.DISCOVERING,
            ):
                store.close_session(
                    account_id=args.account_id,
                    session_id=lease["session_id"],
                    lease_id=lease["lease_id"],
                    session_epoch=lease["session_epoch"],
                    target_status=AccountStatus.RESTING,
                    cooldown_until=datetime.now(timezone.utc),
                )
            store.export_snapshot()

        out = {
            "ok": True,
            "recipe_id": task_mod.module_id,
            "ticks_executed": ticks_run,
            "halted": bool(last_res.halted) if last_res else False,
            "final_lifecycle_state": str(last_res.lifecycle_state) if last_res else "UNKNOWN",
            "final_directive": str(last_res.task_directive.directive_type) if (last_res and last_res.task_directive) else None,
            "detail": last_res.detail if last_res else "",
        }
        print(redactor.redact_text(json.dumps(out, indent=2, ensure_ascii=False)))
        return 0
    finally:
        store.close()


def _parse_int_csv(raw: str | None) -> list[int]:
    if not raw:
        return []
    out: list[int] = []
    for part in raw.split(","):
        s = part.strip()
        if not s:
            continue
        out.append(int(s))
    return out


def _build_telegram_env_context(data_dir: Path) -> tuple[Redactor, StateStore, BoundedOutbox, TelegramGateway, dict[str, Any]]:
    data_dir.mkdir(parents=True, exist_ok=True)
    redactor = (
        LocalSecretVault(data_dir / "vault.json").redactor
        if (data_dir / "vault.json").exists()
        else Redactor()
    )
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if bot_token and not bot_token.startswith("<"):
        redactor.register_secret(bot_token)

    rotated = os.environ.get("TELEGRAM_TOKEN_ROTATED", "false").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    admin_chats = _parse_int_csv(os.environ.get("TELEGRAM_CHAT_ID"))
    allowed_chats = _parse_int_csv(os.environ.get("ALLOWED_CHAT_IDS"))
    default_channel_raw = os.environ.get("DEFAULT_CHANNEL_ID", "").strip()
    default_channel = (
        int(default_channel_raw)
        if default_channel_raw and not default_channel_raw.startswith("<")
        else None
    )

    allowed_set = set(allowed_chats) | set(admin_chats)
    if default_channel is not None:
        allowed_set.add(default_channel)

    store = StateStore(
        data_dir / "state.db",
        snapshot_path=data_dir / "state_snapshot.json",
        redactor=redactor,
    )
    outbox = BoundedOutbox(data_dir / "outbox.db", redactor=redactor)
    gw = TelegramGateway(
        store=store,
        outbox=outbox,
        redactor=redactor,
        allowed_chat_ids=allowed_set if allowed_set else None,
    )
    meta = {
        "bot_token": bot_token,
        "token_rotated": rotated,
        "admin_chat_ids": admin_chats,
        "allowed_chat_ids": sorted(allowed_set),
        "default_channel_id": default_channel,
    }
    return redactor, store, outbox, gw, meta


def _cmd_telegram_check(args: argparse.Namespace) -> int:
    data_dir = _resolve_data_dir(args.data_dir)
    redactor, store, outbox, gw, meta = _build_telegram_env_context(data_dir)
    try:
        acl_rows = store._conn.execute(
            "SELECT user_id, chat_id, role FROM operator_acl ORDER BY user_id, chat_id"
        ).fetchall()
        operators = [
            {"user_id": int(r["user_id"]), "chat_id": int(r["chat_id"]), "role": str(r["role"])}
            for r in acl_rows
        ]
        status_checks: list[dict[str, Any]] = []
        for op in operators:
            resp = gw.handle_incoming(
                user_id=op["user_id"], chat_id=op["chat_id"], text_or_callback="/status"
            )
            status_checks.append(
                {
                    "user_id": op["user_id"],
                    "chat_id": op["chat_id"],
                    "role": op["role"],
                    "status_response": resp,
                }
            )
        unauth_resp = gw.handle_incoming(
            user_id=999999999, chat_id=999999999, text_or_callback="/status"
        )

        bot_me: dict[str, Any] | None = None
        processed_live_updates = 0
        delivered_ping_chats: list[int] = []

        if not args.offline and meta["bot_token"] and not meta["bot_token"].startswith("<"):
            bot_me = gw.fetch_bot_me(meta["bot_token"])
            if meta["token_rotated"]:
                _, processed_live_updates = gw.poll_updates_once(
                    meta["bot_token"],
                    offset=0,
                    timeout_sec=1,
                    token_rotation_confirmed=True,
                )
                if args.send_status_ping and status_checks:
                    sender = gw.build_live_bot_sender(
                        meta["bot_token"], token_rotation_confirmed=True
                    )
                    sample_status = status_checks[0]["status_response"] or "📊 ملخص الأسطول"
                    targets: list[int] = []
                    if meta["default_channel_id"] is not None:
                        targets.append(int(meta["default_channel_id"]))
                    targets.extend(int(c) for c in meta["admin_chat_ids"] if int(c) not in targets)
                    for cid in targets:
                        ok_send, _ = sender(cid, f"✅ فحص جاهزية OSRS_ee التلقائي:\n{sample_status}")
                        if ok_send:
                            delivered_ping_chats.append(cid)

        report = {
            "ok": bool(status_checks) and unauth_resp is None,
            "token_configured": bool(meta["bot_token"] and not meta["bot_token"].startswith("<")),
            "token_rotated": meta["token_rotated"],
            "allowed_chat_ids": meta["allowed_chat_ids"],
            "default_channel_id": meta["default_channel_id"],
            "registered_operators": operators,
            "status_command_self_test": status_checks,
            "unauthorized_silent_drop_verified": unauth_resp is None,
            "live_bot_identity": bot_me,
            "processed_pending_live_updates": processed_live_updates,
            "delivered_ping_chats": delivered_ping_chats,
        }
        print(redactor.redact_text(json.dumps(report, indent=2, ensure_ascii=False)))
        return 0 if report["ok"] else 1
    finally:
        outbox.close()
        store.close()


def _cmd_telegram_poll(args: argparse.Namespace) -> int:
    data_dir = _resolve_data_dir(args.data_dir)
    redactor, store, outbox, gw, meta = _build_telegram_env_context(data_dir)
    try:
        if not meta["bot_token"] or meta["bot_token"].startswith("<"):
            raise ValueError("TELEGRAM_BOT_TOKEN is not configured in .env or environment")
        offset = 0
        cycles = 0
        max_cycles = int(args.max_cycles)
        while max_cycles <= 0 or cycles < max_cycles:
            cycles += 1
            offset, _ = gw.poll_updates_once(
                meta["bot_token"],
                offset=offset,
                timeout_sec=int(args.timeout_sec),
                token_rotation_confirmed=bool(meta["token_rotated"]),
            )
        return 0
    except KeyboardInterrupt:
        return 0
    finally:
        outbox.close()
        store.close()


def _cmd_plan_route(args: argparse.Namespace) -> int:
    caps = AccountCapabilitiesContext(
        account_id=args.account_id,
        skills={"agility": args.agility},
        combat_level=args.combat_level,
        is_member=bool(args.member),
        coins_gp=args.coins_gp,
    )
    graph = ConstrainedWaypointGraph()
    plan = graph.plan_safe_route(
        origin=args.origin,
        destination=args.destination,
        capabilities=caps,
    )
    print(
        json.dumps(
            {
                "account_id": args.account_id,
                "origin": plan.origin_node,
                "destination": plan.destination_node,
                "total_cost_tiles": plan.total_cost_tiles,
                "walker_flags": plan.walker_flags,
                "waypoints": [
                    {"x": wp.x, "y": wp.y, "plane": wp.plane, "label": wp.label}
                    for wp in plan.waypoints
                ],
                "pruned_shortcuts_and_hazards": dict(plan.pruned_edges),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


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
        stronghold_summary: dict[str, Any] | None = None

        def _make_fake(lease: dict[str, Any]) -> FakeGameAdapter:
            nonlocal stronghold_summary
            fa = FakeGameAdapter(
                account_id=acc_id,
                session_id=lease["session_id"],
                session_epoch=lease["session_epoch"],
                tutorial_varp_281=1000,
                skills={"hitpoints": 10, "agility": 1, "woodcutting": 35},
                bank_items=[{"item_id": 1359, "name": "Rune axe", "quantity": 1, "noted": False}],
            )
            if args.recipe_id == "stage2_stronghold_10k":
                fa.position = {"x": 3164, "y": 3487, "plane": 0}
                for vid in (2309, 2310, 2311, 2312):
                    fa.varbits[vid] = 0
                f1_d = [(1864, 5227), (1875, 5239), (1912, 5242), (1905, 5230)]
                f2_d = [(2030, 5236), (2025, 5245), (2014, 5237), (2020, 5225)]
                f3_d = [(2132, 5259), (2132, 5265), (2140, 5280)]
                f4_d = [(2359, 5233), (2345, 5238), (2341, 5224), (2343, 5218)]
                d_idx = {"Gate of War": 0, "Rickety door": 0, "Oozing barrier": 0, "Portal of Death": 0}
                orig_exec = fa.execute_raw_command

                def _sim_sh(cmd_type: str, payload: dict[str, Any]) -> dict[str, Any]:
                    r = orig_exec(cmd_type, payload)
                    if cmd_type == "INTERACT_OBJECT":
                        oid, oname = payload.get("id"), payload.get("name")
                        if oid == 20790:
                            fa.position = {"x": 1860, "y": 5244, "plane": 0}
                        elif oname in d_idx:
                            table = {"Gate of War": f1_d, "Rickety door": f2_d, "Oozing barrier": f3_d, "Portal of Death": f4_d}[oname]
                            nx, ny = table[d_idx[oname]]
                            d_idx[oname] += 1
                            fa.position = {"x": nx, "y": ny, "plane": 0}
                        elif oid == 20656:
                            fa.varbits[2309] = 1
                            fa._add_to_inventory({"item_id": 995, "name": "Coins", "quantity": 2000, "noted": False})
                        elif oid == 20785:
                            fa.position = {"x": 2042, "y": 5245, "plane": 0}
                        elif oid == 19000:
                            fa.varbits[2310] = 1
                            fa._add_to_inventory({"item_id": 995, "name": "Coins", "quantity": 3000, "noted": False})
                        elif oid == 19004:
                            fa.position = {"x": 2123, "y": 5252, "plane": 0}
                        elif oid == 23709:
                            fa.varbits[2311] = 1
                            fa._add_to_inventory({"item_id": 995, "name": "Coins", "quantity": 5000, "noted": False})
                        elif oid == 23706:
                            fa.position = {"x": 2358, "y": 5218, "plane": 0}
                        elif oid == 23732:
                            fa.position = {"x": 3081, "y": 3421, "plane": 0}
                    elif cmd_type == "WIDGET_CLICK" and payload.get("groupId") == 270 and payload.get("childId") == 15:
                        fa.varbits[2312] = 1
                        fa._add_to_inventory({"item_id": 9005, "name": "Fancy boots", "quantity": 1, "noted": False})
                    return r

                fa.execute_raw_command = _sim_sh  # type: ignore[method-assign]
                stronghold_summary = {"adapter": fa, "lease": lease}
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

        mission_report: dict[str, Any] | None = None
        if res.ok and args.recipe_id == "stage2_stronghold_10k" and stronghold_summary is not None:
            # Also execute hermetic 4-floor Stronghold + GE Bank Turnaround simulation in an isolated scratch DB
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_sim_dir:
                sim_store = StateStore(Path(tmp_sim_dir) / "sim_kernel.db")
                try:
                    sim_store.register_account(
                        account_id=acc_id,
                        auth_mode="LEGACY_CREDENTIALS",
                        credential_ref=f"vault://accounts/{acc_id}/auth",
                        proxy_ref="vault://proxies/sim_loopback",
                        region="EG",
                        timezone_name="Africa/Cairo",
                        persona_seed=102,
                        initial_status=AccountStatus.READY,
                    )
                    from datetime import datetime, timezone
                    sim_lease = sim_store.acquire_session_lease(
                        account_id=acc_id,
                        slot_id=0,
                        task_id=args.recipe_id,
                        now_utc=datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc),
                        bypass_circadian=True,
                    )
                    sim_fa = _make_fake(sim_lease)
                    sim_gate = GameAdapterGate(sim_store, sim_fa)
                    sim_gate.bind_to_lease(
                        account_id=acc_id,
                        session_id=sim_lease["session_id"],
                        lease_id=sim_lease["lease_id"],
                        session_epoch=sim_lease["session_epoch"],
                    )
                    sim_kernel = ExecutionEngineKernel(
                        gate=sim_gate,
                        account_id=acc_id,
                        session_id=sim_lease["session_id"],
                        lease_id=sim_lease["lease_id"],
                        session_epoch=sim_lease["session_epoch"],
                        capabilities=AccountCapabilitiesContext(
                            account_id=acc_id, skills={"agility": 1, "hitpoints": 10}, combat_level=3, is_member=False
                        ),
                    )
                    sh_mod = _resolve_task_module("stage2_stronghold_10k")
                    ticks_used = 0
                    for step in range(1, 140):
                        ticks_used = step
                        tr = sim_kernel.step_tick(sh_mod, now_monotonic=float(step))
                        if tr.halted:
                            break
                    banked_map = {it["name"]: it["quantity"] for it in sim_fa.bank_items}
                    mission_report = {
                        "floors_cleared": [f for f, vid in enumerate((2309, 2310, 2311, 2312), start=1) if sim_fa.varbits.get(vid) == 1],
                        "ticks_simulated": ticks_used,
                        "final_position": sim_fa.position,
                        "banked_coins_gp": banked_map.get("Coins", 0),
                        "fancy_boots_banked": banked_map.get("Fancy boots", 0) >= 1,
                    }
                finally:
                    sim_store.close()

        store.export_snapshot()
        out_payload: dict[str, Any] = {
            "ok": res.ok,
            "exit_code": res.exit_code,
            "account_id": res.account_id,
            "recipe_id": res.recipe_id,
            "final_account_status": res.final_account_status,
            "detail": res.detail,
            "snapshot_path": str(data_dir / "state_snapshot.json"),
        }
        if mission_report is not None:
            out_payload["stronghold_mission_simulation"] = mission_report
        print(
            json.dumps(
                out_payload,
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
                agent_token=adapter_token,
                account_id=args.account_id,
                session_id=lease["session_id"],
                session_epoch=lease["session_epoch"],
                jvm_flags=jvm_cmd,
                coalesce_window_ms=180,
            )
            gate = GameAdapterGate(store, adapter)  # type: ignore[arg-type]
            _ = supervisor.attach_session(lease=lease, gate=gate)
            caps = AccountCapabilitiesContext(
                account_id=args.account_id,
                combat_level=max(3, int(acc["total_level"] or 32) // 10),
                quest_points=int(acc["quest_points"] or 0),
                is_member=False,
            )
            kernel = ExecutionEngineKernel(
                gate=gate,
                account_id=args.account_id,
                session_id=lease["session_id"],
                lease_id=lease["lease_id"],
                session_epoch=lease["session_epoch"],
                capabilities=caps,
            )
            task_mod = _resolve_task_module(args.recipe_id)
            print(
                vault.redactor.redact_text(
                    json.dumps(
                        {
                            "launched": True,
                            "pid": proc_tree.pid,
                            "account_id": args.account_id,
                            "task_module": task_mod.module_id,
                            "adapter_port": bootstrap.adapter_port,
                            "sidecar_port": sidecar.listen_port,
                        },
                        indent=2,
                    )
                )
            )
            try:
                while proc_tree.is_running() and not supervisor.cancel_event.is_set():
                    t_tick = time.monotonic()
                    tick_res = kernel.step_tick(task_mod, now_monotonic=t_tick)
                    if tick_res.halted:
                        break
                    # Wait only the remainder of the 0.6s OSRS game tick via cancellable Event (not blocking sleep!)
                    spent = time.monotonic() - t_tick
                    rem = max(0.05, 0.6 - spent)
                    supervisor.cancel_event.wait(timeout=rem)
            except KeyboardInterrupt:
                supervisor.abort()
            finally:
                adapter.kill_process_tree()
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

    p_tchk = sub.add_parser("telegram-check", help="Verify .env Telegram config, RBAC /status response, and live getMe")
    p_tchk.add_argument("--offline", action="store_true", help="Skip live api.telegram.org HTTP call")
    p_tchk.add_argument("--send-status-ping", action="store_true", help="Send live /status readiness card to configured chats")

    p_tpoll = sub.add_parser("telegram-poll", help="Run live Telegram Bot API long-polling daemon for /status & controls")
    p_tpoll.add_argument("--timeout-sec", type=int, default=15)
    p_tpoll.add_argument("--max-cycles", type=int, default=0, help="0 = run continuously until Ctrl+C")

    sub.add_parser("status", help="Display sanitized fleet status, GE PnL, and safe slot capacity")

    p_stag = sub.add_parser("plan-stagger", help="Generate >=90s staggered cold-start schedule")
    p_stag.add_argument("--accounts", required=True, help="Comma-separated account IDs (e.g. acc_01,acc_02)")

    p_route = sub.add_parser("plan-route", help="Compute capability-constrained Level-3 safe corridor route")
    p_route.add_argument("--account-id", default="acc_01")
    p_route.add_argument("--origin", default="GRAND_EXCHANGE")
    p_route.add_argument("--destination", default="BARBARIAN_VILLAGE")
    p_route.add_argument("--agility", type=int, default=1)
    p_route.add_argument("--combat-level", type=int, default=3)
    p_route.add_argument("--coins-gp", type=int, default=0)
    p_route.add_argument("--member", action="store_true")

    p_sim = sub.add_parser("simulate-pilot", help="Run a hermetic end-to-end pilot session locally")
    p_sim.add_argument("--account-id", default="acc_sim_01")
    p_sim.add_argument(
        "--recipe-id",
        default="f2p_draynor_willow",
        choices=list(CuratedRecipeRegistry()._by_id.keys()),
    )

    p_probe = sub.add_parser("live-probe", help="Run read-only Keep-Alive wire handshake against Microbot port 8081")
    p_probe.add_argument("--port", type=int, default=8081)
    p_probe.add_argument("--account-id", default="acc_live_01")
    p_probe.add_argument("--token-file", default=None, help="Path to .agent-token (default: ~/.runelite/.agent-token)")

    p_att = sub.add_parser("attach-bot", help="Attach a decoupled TaskModule bot to a running Microbot port 8081")
    p_att.add_argument("--recipe-id", default="stage2_stronghold_10k", choices=list(BotModuleRegistry().list_registered_ids()))
    p_att.add_argument("--port", type=int, default=8081)
    p_att.add_argument("--account-id", default="acc_live_01")
    p_att.add_argument("--token-file", default=None, help="Path to .agent-token (default: ~/.runelite/.agent-token)")
    p_att.add_argument("--agility", type=int, default=1)
    p_att.add_argument("--combat-level", type=int, default=3)
    p_att.add_argument("--member", action="store_true")
    p_att.add_argument("--max-ticks", type=int, default=2500)
    p_att.add_argument("--tick-interval-sec", type=float, default=0.6)
    p_att.add_argument("--quiet", action="store_true", help="Suppress live per-tick stderr telemetry")

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
    load_dotenv(Path.cwd() / ".env")
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.data_dir:
        alt_env = Path(args.data_dir).resolve().parent / ".env"
        if alt_env.is_file():
            load_dotenv(alt_env)
    cmd = args.subcommand
    if cmd == "doctor":
        return _cmd_doctor(args)
    if cmd == "init":
        return _cmd_init(args)
    if cmd == "ingest":
        return _cmd_ingest(args)
    if cmd == "grant-operator":
        return _cmd_grant_operator(args)
    if cmd == "telegram-check":
        return _cmd_telegram_check(args)
    if cmd == "telegram-poll":
        return _cmd_telegram_poll(args)
    if cmd == "status":
        return _cmd_status(args)
    if cmd == "plan-stagger":
        return _cmd_plan_stagger(args)
    if cmd == "plan-route":
        return _cmd_plan_route(args)
    if cmd == "simulate-pilot":
        return _cmd_simulate_pilot(args)
    if cmd == "live-probe":
        return _cmd_live_probe(args)
    if cmd == "attach-bot":
        return _cmd_attach_bot(args)
    if cmd == "dry-run-slot":
        return _cmd_dry_run_or_launch_slot(args, dry_run=True)
    if cmd == "launch-slot":
        return _cmd_dry_run_or_launch_slot(args, dry_run=False)
    return 1
