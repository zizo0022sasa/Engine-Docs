"""PR-05b Acceptance Tests: Vault + Dual-Auth Pipe Ingestion (T02), Real RFC 1928/1929 SOCKS5h Sidecar
with Fail-Closed & Geo-Pinned Rotation (T19), and Orphan-Free Process Tree Termination (T16).
"""

from __future__ import annotations

import os
import socket
import struct
import sys
import threading
import time
from pathlib import Path

import pytest

from osrs_ee.network import LocalSocks5Sidecar, ProxySidecarError
from osrs_ee.sandbox.process_runner import ManagedProcessTree
from osrs_ee.security.redaction import Redactor
from osrs_ee.security.vault import LocalSecretVault, ingest_pipe_lines
from osrs_ee.state import StateStore


def test_t02_dual_auth_pipe_ingestion_stores_only_vault_refs_and_redacts_secrets(tmp_path: Path) -> None:
    """T02/AUD-11: Legacy & Jagex pipe lines store secrets in 0600 Vault and refs only in SQLite."""
    redactor = Redactor()
    store = StateStore(tmp_path / "state.db", snapshot_path=tmp_path / "snap.json", redactor=redactor)
    vault = LocalSecretVault(tmp_path / "vault.secret.json", redactor=redactor)

    raw_lines = [
        "# Legacy & Jagex accounts",
        "legacy_user@example.com|MyLegacyPass#77|198.51.100.10:1080:puser:ppass|Egypt",
        "jx_live_session_tok_9988|char_id_55|socks5://spuser:sppass@198.51.100.20:7000|Germany",
    ]
    summaries = ingest_pipe_lines(raw_lines, store=store, vault=vault)
    assert len(summaries) == 2
    assert summaries[0].account_id == "acc_01"
    assert summaries[0].auth_mode == "LEGACY_CREDENTIALS"
    assert summaries[0].region == "EG"
    assert summaries[1].account_id == "acc_02"
    assert summaries[1].auth_mode == "JAGEX_SESSION"
    assert summaries[1].region == "DE"

    # Verify SQLite and snapshot contain ZERO raw passwords, session tokens, or emails!
    snap_raw = (tmp_path / "snap.json").read_text(encoding="utf-8")
    for secret in ("MyLegacyPass#77", "jx_live_session_tok_9988", "legacy_user@example.com", "ppass", "sppass"):
        assert secret not in snap_raw
        assert secret not in redactor.redact_text(f"Leak check: {secret}")

    # Vault resolves secrets cleanly when authorized
    assert vault.resolve_secret(summaries[0].credential_ref)["password"] == "MyLegacyPass#77"
    assert vault.resolve_secret(summaries[1].credential_ref)["jx_character_id"] == "char_id_55"
    store.close()


def test_t19_real_socks5h_sidecar_remote_dns_rfc1929_auth_fail_closed_and_geo_rotation() -> None:
    """T19/AUD-08/AUD-27: End-to-end TCP test with mock upstream SOCKS5 (RFC 1929 user:pass) + target echo server."""
    # 1. Start a local TCP echo server pretending to be OSRS game world
    echo_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    echo_srv.bind(("127.0.0.1", 0))
    echo_srv.listen(4)
    echo_port = echo_srv.getsockname()[1]

    def _echo_worker() -> None:
        while True:
            try:
                conn, _ = echo_srv.accept()
            except OSError:
                break
            with conn:
                data = conn.recv(1024)
                if data:
                    conn.sendall(b"WORLD_OK:" + data)

    threading.Thread(target=_echo_worker, daemon=True).start()

    # 2. Start a mock Upstream Residential SOCKS5 Server requiring RFC 1929 auth `res_user:res_pass`
    #    and recording the exact ATYP + domain bytes sent by our sidecar (proving zero local DNS leak!)
    observed_domains: list[bytes] = []
    upstream_srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    upstream_srv.bind(("127.0.0.1", 0))
    upstream_srv.listen(4)
    upstream_port = upstream_srv.getsockname()[1]
    upstream_reject = False

    def _upstream_worker() -> None:
        while True:
            try:
                c, _ = upstream_srv.accept()
            except OSError:
                break
            with c:
                if upstream_reject:
                    c.close()
                    continue
                ver, nm = c.recv(2)
                methods = c.recv(nm)
                assert ver == 5 and 0x02 in methods
                c.sendall(b"\x05\x02")  # demand RFC 1929 auth
                aver, ulen = c.recv(2)
                uname = c.recv(ulen)
                plen = c.recv(1)[0]
                pw = c.recv(plen)
                if aver != 1 or uname != b"res_user" or pw != b"res_pass":
                    c.sendall(b"\x01\x01")
                    continue
                c.sendall(b"\x01\x00")

                hdr = c.recv(4)
                assert hdr[:3] == b"\x05\x01\x00"
                assert hdr[3] == 0x03  # MUST be ATYP=0x03 DOMAINNAME (remote DNS!)
                dlen = c.recv(1)[0]
                domain = c.recv(dlen)
                dst_port = struct.unpack("!H", c.recv(2))[0]
                observed_domains.append(domain)

                # Connect to local echo server on behalf of unresolvable fake domain!
                target = socket.create_connection(("127.0.0.1", dst_port), timeout=2.0)
                c.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
                payload = c.recv(1024)
                target.sendall(payload)
                reply = target.recv(1024)
                c.sendall(reply)
                target.close()

    threading.Thread(target=_upstream_worker, daemon=True).start()

    sidecar = LocalSocks5Sidecar(
        upstream_proxy_uri=f"socks5://res_user:res_pass@127.0.0.1:{upstream_port}",
        pinned_region="EG",
    )
    local_port = sidecar.start()

    try:
        # 3. Connect as Java client (NO-AUTH) requesting synthetic domain `oldschool8.runescape.invalid`
        #    (which would FAIL local DNS if leaked, proving remote socks5h forwarding!)
        cli = socket.create_connection(("127.0.0.1", local_port), timeout=3.0)
        cli.sendall(b"\x05\x01\x00")
        assert cli.recv(2) == b"\x05\x00"
        fake_domain = b"oldschool8.runescape.invalid"
        req = b"\x05\x01\x00\x03" + bytes([len(fake_domain)]) + fake_domain + struct.pack("!H", echo_port)
        cli.sendall(req)
        rep = cli.recv(10)
        assert rep[1] == 0x00
        cli.sendall(b"HELLO_JAGEX")
        assert cli.recv(1024) == b"WORLD_OK:HELLO_JAGEX"
        cli.close()

        assert observed_domains == [b"oldschool8.runescape.invalid"]
        assert sidecar.total_bytes_relayed > 0

        # Wait briefly for tunnel counter to hit 0, then verify Geo-Pinning on sticky rotation
        for _ in range(20):
            if sidecar.active_connections == 0:
                break
            time.sleep(0.02)

        with pytest.raises(ProxySidecarError, match="Geo-Pinning"):
            sidecar.rotate_upstream(
                new_upstream_uri=f"socks5://res_user:res_pass@127.0.0.1:{upstream_port}",
                region="US_EAST",
            )
        sidecar.rotate_upstream(
            new_upstream_uri=f"socks5://res_user:res_pass@127.0.0.1:{upstream_port}",
            region="EG",
        )

        # 4. Simulate upstream failure -> MUST Fail-Closed (SOCKS error 0x01, zero direct bypass!)
        upstream_reject = True
        cli2 = socket.create_connection(("127.0.0.1", local_port), timeout=3.0)
        cli2.sendall(b"\x05\x01\x00")
        assert cli2.recv(2) == b"\x05\x00"
        cli2.sendall(req)
        fail_rep = cli2.recv(10)
        assert fail_rep[1] != 0x00
        cli2.close()
    finally:
        sidecar.stop()
        upstream_srv.close()
        echo_srv.close()


def test_t16_managed_process_tree_kills_parent_and_grandchild_without_orphans(tmp_path: Path) -> None:
    """T16/AUD-24: Spawns parent + child subprocesses and verifies kill_process_tree() terminates BOTH."""
    pid_file = tmp_path / "grandchild.pid"
    script = (
        "import subprocess, sys, time, pathlib\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    runner = ManagedProcessTree(
        [sys.executable, "-c", script],
        extra_env={"JX_SESSION_ID": "test_env_injection"},
        use_xvfb_if_headless=False,
    )
    parent_pid = runner.start()
    assert parent_pid > 0

    for _ in range(50):
        if pid_file.exists():
            break
        time.sleep(0.03)
    assert pid_file.exists()
    grandchild_pid = int(pid_file.read_text().strip())

    runner.kill_process_tree()
    assert not runner.is_alive()

    # Verify grandchild process is also dead (ProcessLookupError on signal 0)
    if os.name != "nt":
        time.sleep(0.05)
        with pytest.raises(ProcessLookupError):
            os.kill(grandchild_pid, 0)
