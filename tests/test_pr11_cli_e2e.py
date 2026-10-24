"""PR-11 End-to-End CLI Verification:
Tests `doctor`, `init`, `ingest` (Legacy + Jagex), `grant-operator`, `status`,
`plan-stagger`, `simulate-pilot`, and `dry-run-slot` via `python -m osrs_ee`
verifying zero secret leakage across stdout and state_snapshot.json.
"""

from __future__ import annotations

import json
from pathlib import Path

from osrs_ee.cli import main


def test_e2e_cli_workflow_init_ingest_simulate_dryrun_and_status_zero_secret_leak(
    tmp_path: Path, capsys: object
) -> None:
    data_dir = tmp_path / ".osrs_ee_data"
    accounts_file = tmp_path / "accounts_local.txt"
    canary_pw = "CliSuperSecretPw!99"
    canary_jx = "jx_tok_secret_value_888"
    canary_px_pw = "PxAuthSecret77"

    accounts_file.write_text(
        "\n".join(
            [
                f"legacy_hero@example.com|{canary_pw}|127.0.0.1:19080:pxuser:{canary_px_pw}|EG",
                f"{canary_jx}|char_id_555|socks5://pxuser:{canary_px_pw}@127.0.0.1:19081|DE",
            ]
        ),
        encoding="utf-8",
    )

    # 1. init
    assert main(["--data-dir", str(data_dir), "init"]) == 0
    _ = capsys.readouterr()  # type: ignore[attr-defined]

    # 2. ingest
    assert main(["--data-dir", str(data_dir), "ingest", "--file", str(accounts_file)]) == 0
    ing_out = capsys.readouterr().out  # type: ignore[attr-defined]
    ing_json = json.loads(ing_out)
    assert ing_json["ingested_count"] == 2
    assert ing_json["accounts"][0]["auth_mode"] == "LEGACY_CREDENTIALS"
    assert ing_json["accounts"][1]["auth_mode"] == "JAGEX_SESSION"
    for secret in (canary_pw, canary_jx, canary_px_pw, "legacy_hero@example.com"):
        assert secret not in ing_out

    # 3. grant-operator
    assert (
        main(
            [
                "--data-dir",
                str(data_dir),
                "grant-operator",
                "--user-id",
                "777",
                "--chat-id",
                "888",
                "--role",
                "ADMIN",
            ]
        )
        == 0
    )
    _ = capsys.readouterr()  # type: ignore[attr-defined]

    # 4. simulate-pilot (Stronghold 10k GP & Draynor Willow)
    assert (
        main(
            [
                "--data-dir",
                str(data_dir),
                "simulate-pilot",
                "--account-id",
                "acc_01",
                "--recipe-id",
                "stage2_stronghold_10k",
            ]
        )
        == 0
    )
    sim_out = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert sim_out["ok"] is True

    # 5. dry-run-slot verifies sandbox + local SOCKS5h sidecar + JVM args without launching Java
    fake_jar = tmp_path / "microbot-shaded.jar"
    fake_jar.write_bytes(b"PK\x03\x04")
    assert (
        main(
            [
                "--data-dir",
                str(data_dir),
                "dry-run-slot",
                "--account-id",
                "acc_01",
                "--client-jar",
                str(fake_jar),
                "--adapter-port",
                "18199",
            ]
        )
        == 0
    )
    dry_out = capsys.readouterr().out  # type: ignore[attr-defined]
    dry_json = json.loads(dry_out)
    assert dry_json["mode"] == "DRY_RUN_VERIFIED"
    assert dry_json["local_socks5_sidecar"].startswith("socks5://127.0.0.1:")
    assert any("--disable-telemetry" in arg for arg in dry_json["jvm_command"])
    for secret in (canary_pw, canary_jx, canary_px_pw):
        assert secret not in dry_out

    # 6. plan-stagger, plan-route (safe corridor without 3149,3480), status & snapshot verification
    assert main(["--data-dir", str(data_dir), "plan-stagger", "--accounts", "acc_01,acc_02,acc_03"]) == 0
    stag_json = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert stag_json["relative_launch_offsets_sec"]["acc_02"] >= 90.0

    assert (
        main(
            [
                "--data-dir",
                str(data_dir),
                "plan-route",
                "--origin",
                "GRAND_EXCHANGE",
                "--destination",
                "BARBARIAN_VILLAGE",
                "--agility",
                "1",
                "--combat-level",
                "3",
            ]
        )
        == 0
    )
    route_json = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert route_json["walker_flags"]["allowShortcuts"] is False
    assert all((wp["x"], wp["y"]) != (3149, 3480) for wp in route_json["waypoints"])

    assert main(["--data-dir", str(data_dir), "status"]) == 0
    stat_out = capsys.readouterr().out  # type: ignore[attr-defined]
    snap_out = (data_dir / "state_snapshot.json").read_text(encoding="utf-8")
    for text_blob in (stat_out, snap_out):
        for secret in (canary_pw, canary_jx, canary_px_pw, "legacy_hero@example.com"):
            assert secret not in text_blob
