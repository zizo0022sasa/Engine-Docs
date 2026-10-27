"""Acceptance Tests for PR-14 Pre-Game Fleet Ingestion, `.env` Loader & Live Telegram Control Plane (T45..T46)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from osrs_ee.cli import main
from osrs_ee.security.vault import LocalSecretVault, ingest_pipe_lines, load_dotenv, normalize_proxy_uri
from osrs_ee.state import OperatorRole, StateStore
from osrs_ee.telemetry import BoundedOutbox, TelegramGateway


def test_t45_dotenv_loader_telegram_allowed_chats_botname_suffix_and_cli_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """T45: Verifies stdlib `.env` loading, `ALLOWED_CHAT_IDS` perimeter filter, `/status@bot` handling, and `telegram-check`."""
    data_dir = tmp_path / ".osrs_ee_data"
    env_file = tmp_path / ".env"
    fake_tok = "1111111111:" + ("T" * 35)
    env_file.write_text(
        "\n".join(
            [
                f"TELEGRAM_BOT_TOKEN={fake_tok}",
                "TELEGRAM_CHAT_ID=1124247595,6750672145",
                "ALLOWED_CHAT_IDS=1124247595,6750672145",
                "DEFAULT_CHANNEL_ID=-1004356848093",
                "TELEGRAM_TOKEN_ROTATED=true",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    for k in (
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "ALLOWED_CHAT_IDS",
        "DEFAULT_CHANNEL_ID",
        "TELEGRAM_TOKEN_ROTATED",
    ):
        monkeypatch.delenv(k, raising=False)

    parsed = load_dotenv(env_file)
    assert parsed["TELEGRAM_TOKEN_ROTATED"] == "true"

    assert main(["--data-dir", str(data_dir), "init"]) == 0
    _ = capsys.readouterr()

    assert (
        main(
            [
                "--data-dir",
                str(data_dir),
                "grant-operator",
                "--user-id",
                "1124247595",
                "--chat-id",
                "1124247595",
                "--role",
                "ADMIN",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "--data-dir",
                str(data_dir),
                "grant-operator",
                "--user-id",
                "6750672145",
                "--chat-id",
                "6750672145",
                "--role",
                "ADMIN",
            ]
        )
        == 0
    )
    _ = capsys.readouterr()

    rc = main(["--data-dir", str(data_dir), "telegram-check", "--offline"])
    assert rc == 0
    out_raw = capsys.readouterr().out
    assert fake_tok not in out_raw
    report = json.loads(out_raw)
    assert report["ok"] is True
    assert report["unauthorized_silent_drop_verified"] is True
    assert len(report["status_command_self_test"]) == 2
    assert "📊 ملخص الأسطول" in report["status_command_self_test"][0]["status_response"]

    # Also test `@botname` command suffix and `allowed_chat_ids` perimeter guard directly
    store = StateStore(data_dir / "state.db")
    outbox = BoundedOutbox(data_dir / "outbox.db")
    try:
        store.grant_operator_role(user_id=1124247595, chat_id=-1004356848093, role=OperatorRole.ADMIN)
        gw = TelegramGateway(
            store=store,
            outbox=outbox,
            allowed_chat_ids={1124247595, 6750672145, -1004356848093},
        )
        reply_group = gw.handle_incoming(
            user_id=1124247595,
            chat_id=-1004356848093,
            text_or_callback="/status@orangeconnebot",
        )
        assert reply_group is not None and "📊 ملخص الأسطول" in reply_group

        # Disallowed chat_id dropped immediately even if granted in DB
        store.grant_operator_role(user_id=1124247595, chat_id=-99999, role=OperatorRole.ADMIN)
        assert gw.handle_incoming(user_id=1124247595, chat_id=-99999, text_or_callback="/status") is None
    finally:
        outbox.close()
        store.close()


def test_t46_idempotent_ingest_duplicate_skip_and_flexible_proxy_normalization(tmp_path: Path) -> None:
    """T46: Re-running ingest on same `accounts.txt` skips duplicates instead of creating duplicate `acc_XX`."""
    assert normalize_proxy_uri("DIRECT") == "direct://local"
    assert normalize_proxy_uri("u1:p1@198.51.100.5:1080") == "socks5://u1:p1@198.51.100.5:1080"

    store = StateStore(tmp_path / "state.db")
    vault = LocalSecretVault(tmp_path / "vault.json")
    try:
        batch_1 = [
            "pilot1@example.com|SecretPass#1|DIRECT|Egypt",
            "jx_sess_abc|char_901|u1:p1@198.51.100.9:1080|Germany",
        ]
        res1 = ingest_pipe_lines(batch_1, store=store, vault=vault)
        assert len(res1) == 2

        # Second run with the 2 existing accounts + 1 brand-new account -> only 1 newly ingested!
        batch_2 = [
            "PILOT1@example.com|SecretPass#1|DIRECT|Egypt",
            "jx_sess_abc|char_901|u1:p1@198.51.100.9:1080|Germany",
            "pilot2@example.com|SecretPass#2|198.51.100.12:1080:u2:p2|Egypt",
        ]
        res2 = ingest_pipe_lines(batch_2, store=store, vault=vault)
        assert len(res2) == 1
        assert res2[0].account_id == "acc_03"
    finally:
        store.close()
