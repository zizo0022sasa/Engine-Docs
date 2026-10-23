"""PR-05a Acceptance Tests: T06, T10, T19 over real loopback HTTP socket and sandbox filesystem."""

from __future__ import annotations

import json
import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from osrs_ee.sandbox import (
    MicrobotHttpAdapter,
    SandboxProfileSeeder,
    build_isolated_jvm_command,
    resolve_regional_world_pool,
)


def test_t06_t19_sandbox_seeder_enables_agent_server_strips_identity_from_cache_and_enforces_socks5(
    tmp_path: Path,
) -> None:
    """T06/T19: Profile enables AgentServerPlugin, 0600 token, copies static cache (no random.dat),
    enforces --disable-telemetry & socks5://127.0.0.1, and maps EG -> EU world pool.
    """
    golden = tmp_path / "golden_cache"
    golden.mkdir()
    (golden / "main_file_cache.dat2").write_bytes(b"STATIC_CACHE_DATA")
    # Contaminate golden dir with random.dat to prove seeder refuses to copy identity files!
    (golden / "random.dat").write_bytes(b"DO_NOT_COPY_IDENTITY")

    seeder = SandboxProfileSeeder(tmp_path / "sandboxes", golden_cache_dir=golden)
    boot, token = seeder.prepare_sandbox(
        account_id="acc_01",
        persona_seed=12345,
        adapter_port=18081,
        local_socks5_port=19081,
    )

    assert len(token) >= 32
    props = boot.profile_properties_path.read_text(encoding="utf-8")
    assert "runelite.agentserverplugin=true" in props
    assert "agentServer.port=18081" in props
    assert "runelite.gpuplugin=false" in props
    assert "music.mute=true" in props

    # Static cache copied, identity random.dat excluded!
    assert (boot.user_home / "jagexcache" / "main_file_cache.dat2").is_file()
    assert not (boot.user_home / "jagexcache" / "random.dat").exists()

    if os.name != "nt":
        assert stat.S_IMODE(boot.token_file_path.stat().st_mode) == 0o600

    cmd = build_isolated_jvm_command(
        java_bin="java",
        client_jar="/opt/microbot/client.jar",
        bootstrap=boot,
        hwid_agent_jar="/opt/microbot/hwid-agent.jar",
    )
    assert "--disable-telemetry" in cmd
    assert "-Dmicrobot.disableTelemetry=true" in cmd
    assert "-Xmx768m" in cmd
    assert "--proxy=socks5://127.0.0.1:19081" in cmd

    # HTTP proxy or external non-loopback proxy must be rejected (prevents ProxyConfiguration System.exit(1)!)
    with pytest.raises(ValueError):
        build_isolated_jvm_command(
            java_bin="java",
            client_jar="/opt/microbot/client.jar",
            bootstrap=boot,
            proxy_uri="http://192.0.2.5:8080",
        )

    # C-06: Egypt maps to EU (UK/Germany) world pool
    eg_worlds = resolve_regional_world_pool("EG")
    assert 308 in eg_worlds and 383 in eg_worlds


def test_t06_t10_loopback_http_adapter_verifies_token_reads_varp_281_and_closed_bank() -> None:
    """T06/T10: Real loopback ThreadingHTTPServer verifying X-Agent-Token, /varps?id=281, and closed bank."""
    expected_token = "tok_csprng_loopback_secret_123456"
    requested_paths: list[str] = []

    class _MockAgentHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def _check_auth(self) -> bool:
            if self.headers.get("X-Agent-Token") != expected_token:
                self.send_response(401)
                self.end_headers()
                return False
            return True

        def do_GET(self) -> None:  # noqa: N802
            if not self._check_auth():
                return
            requested_paths.append(self.path)
            parsed = urlparse(self.path)
            qs = parse_qs(parsed.query)
            if parsed.path == "/status":
                payload = {
                    "loginState": "LOGGED_IN",
                    "gameTick": 512,
                    "world": 308,
                    "position": {"x": 3222, "y": 3218, "plane": 0},
                    "questPoints": 10,
                    "totalLevel": 108,
                    "playtimeMinutes": 450,
                    "dialogueOpen": False,
                }
            elif parsed.path == "/varps" and qs.get("id") == ["281"]:
                payload = {"id": 281, "value": 1000}
            elif parsed.path == "/varbits":
                # If adapter mistakenly called /varbits?id=281, return 0 to expose bug!
                payload = {"id": 281, "value": 0}
            elif parsed.path == "/bank":
                payload = {"open": False, "pinPromptOpen": False, "items": []}
            elif parsed.path == "/inventory":
                payload = {
                    "items": [{"item_id": 995, "name": "Coins", "quantity": 10000, "noted": False}]
                }
            else:
                self.send_response(404)
                self.end_headers()
                return

            raw = json.dumps(payload).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockAgentHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        # Non-loopback host is rejected at construction
        with pytest.raises(ValueError):
            MicrobotHttpAdapter(
                account_id="acc_01",
                session_id="sess_01",
                session_epoch=1,
                client_instance_id="jvm_live_01",
                port=port,
                agent_token=expected_token,
                jvm_flags=["--disable-telemetry", "-Dmicrobot.disableTelemetry=true"],
                host="0.0.0.0",
            )

        # Wrong X-Agent-Token is rejected with PermissionError
        bad_client = MicrobotHttpAdapter(
            account_id="acc_01",
            session_id="sess_01",
            session_epoch=1,
            client_instance_id="jvm_live_01",
            port=port,
            agent_token="wrong_token_value_1234567890",
            jvm_flags=["--disable-telemetry", "-Dmicrobot.disableTelemetry=true"],
        )
        with pytest.raises(PermissionError):
            bad_client.read_observation()

        # Valid client queries /varps?id=281 (NOT /varbits) and normalizes closed bank to UNKNOWN (None)
        good_client = MicrobotHttpAdapter(
            account_id="acc_01",
            session_id="sess_01",
            session_epoch=1,
            client_instance_id="jvm_live_01",
            port=port,
            agent_token=expected_token,
            jvm_flags=["--disable-telemetry", "-Dmicrobot.disableTelemetry=true"],
        )
        obs = good_client.read_observation()
        assert obs["tutorial_varp_281"] == {"value": 1000, "quality": "KNOWN"}
        assert "/varps?id=281" in requested_paths
        assert not any(p.startswith("/varbits") for p in requested_paths)
        assert obs["bank"]["is_open"] is False
        assert obs["bank"]["items_quality"] == "UNKNOWN"
        assert obs["bank"]["items"] is None

        # Raw /scripts/deploy endpoint is strictly blocked
        with pytest.raises(PermissionError):
            good_client._request_json("POST", "/scripts/deploy", {})
    finally:
        server.shutdown()
        server.server_close()
