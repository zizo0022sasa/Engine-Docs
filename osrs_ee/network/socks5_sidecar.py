"""RFC 1928 / RFC 1929 Local Loopback SOCKS5h Proxy Sidecar with Fail-Closed Kill-Switch.

Solves:
- AUD-08 / Gap 3.1: Microbot JVM connects to `socks5://127.0.0.1:<port>` with NO-AUTH,
  while Python handles upstream `user:pass` authentication (RFC 1929) and remote DNS
  forwarding (`ATYP=0x03` / `socks5h`, zero local DNS resolution leak!).
- Fail-Closed Invariant: If upstream handshake/connection fails or `trip_kill_switch()`
  is called, client sockets are dropped immediately — NEVER falling back to direct internet.
- AUD-27 / Gap 3.2: Safe sticky-IP rotation (`rotate_upstream`) permitted ONLY when
  `active_connections == 0` and strictly locked to the account's pinned `region`.
"""

from __future__ import annotations

import select
import socket
import struct
import threading
from urllib.parse import unquote, urlparse

from osrs_ee.security.vault import normalize_proxy_uri


class ProxySidecarError(RuntimeError):
    """Raised when proxy configuration, region lock, or upstream tunnel fails."""


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("Unexpected EOF during SOCKS5 handshake")
        buf.extend(chunk)
    return bytes(buf)


class LocalSocks5Sidecar:
    """Dedicated per-slot loopback SOCKS5 proxy bridge (`127.0.0.1:<port>`)."""

    def __init__(
        self,
        *,
        upstream_proxy_uri: str,
        pinned_region: str,
        listen_port: int = 0,
        connect_timeout_sec: float = 5.0,
        quota_limit_bytes: int | None = None,
    ) -> None:
        self.pinned_region = pinned_region.strip().upper()
        self._upstream_uri = normalize_proxy_uri(upstream_proxy_uri)
        self.connect_timeout_sec = connect_timeout_sec
        self.quota_limit_bytes = quota_limit_bytes

        self._lock = threading.RLock()
        self._kill_switch = threading.Event()
        self._server_sock: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._open_sockets: set[socket.socket] = set()
        self._active_tunnels = 0
        self.total_bytes_relayed = 0
        self.listen_port = listen_port

    @property
    def active_connections(self) -> int:
        with self._lock:
            return self._active_tunnels

    @property
    def local_client_uri(self) -> str:
        return f"socks5://127.0.0.1:{self.listen_port}"

    def start(self) -> int:
        with self._lock:
            if self._server_sock is not None:
                return self.listen_port
            self._kill_switch.clear()
            srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            srv.bind(("127.0.0.1", self.listen_port))
            srv.listen(16)
            self.listen_port = srv.getsockname()[1]
            self._server_sock = srv
            self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
            self._accept_thread.start()
            return self.listen_port

    def trip_kill_switch(self) -> None:
        """Fail-Closed: Immediately sever listener and all active client/upstream sockets."""
        self._kill_switch.set()
        with self._lock:
            srv = self._server_sock
            self._server_sock = None
            socks = list(self._open_sockets)
            self._open_sockets.clear()
        if srv is not None:
            try:
                srv.close()
            except OSError:
                pass
        for s in socks:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                s.close()
            except OSError:
                pass

    def stop(self) -> None:
        self.trip_kill_switch()
        if self._accept_thread is not None:
            self._accept_thread.join(timeout=1.5)

    def rotate_upstream(self, *, new_upstream_uri: str, region: str) -> None:
        """AUD-27: Rotate sticky upstream proxy ONLY during safe logout microbreak within same region."""
        cand_region = region.strip().upper()
        if cand_region != self.pinned_region:
            raise ProxySidecarError(
                f"Geo-Pinning violation: cannot switch account pinned to {self.pinned_region} into {cand_region}"
            )
        norm = normalize_proxy_uri(new_upstream_uri)
        with self._lock:
            if self._active_tunnels > 0:
                raise ProxySidecarError(
                    f"Cannot rotate upstream IP while {self._active_tunnels} game connections are active; logout first"
                )
            self._upstream_uri = norm

    def _accept_loop(self) -> None:
        while not self._kill_switch.is_set():
            srv = self._server_sock
            if srv is None:
                break
            try:
                client_sock, addr = srv.accept()
            except OSError:
                break
            if addr[0] != "127.0.0.1" or self._kill_switch.is_set():
                client_sock.close()
                continue
            t = threading.Thread(target=self._handle_client, args=(client_sock,), daemon=True)
            t.start()

    def _register_sock(self, s: socket.socket) -> None:
        with self._lock:
            self._open_sockets.add(s)

    def _unregister_sock(self, s: socket.socket) -> None:
        with self._lock:
            self._open_sockets.discard(s)
        try:
            s.close()
        except OSError:
            pass

    def _dial_upstream_socks5(self, target_atyp_payload: bytes) -> socket.socket:
        """Connect to upstream SOCKS5 proxy, authenticate via RFC 1929 if needed, and forward raw target."""
        with self._lock:
            uri = self._upstream_uri
        parsed = urlparse(uri)
        up_host = parsed.hostname
        up_port = parsed.port
        assert up_host is not None and up_port is not None
        username = unquote(parsed.username) if parsed.username else None
        password = unquote(parsed.password) if parsed.password else ""

        upstream = socket.create_connection((up_host, up_port), timeout=self.connect_timeout_sec)
        self._register_sock(upstream)
        try:
            if username is not None:
                upstream.sendall(b"\x05\x02\x00\x02")
            else:
                upstream.sendall(b"\x05\x01\x00")

            ver, method = _recv_exact(upstream, 2)
            if ver != 0x05 or method == 0xFF:
                raise ProxySidecarError("Upstream SOCKS5 rejected authentication methods")

            if method == 0x02:
                if username is None:
                    raise ProxySidecarError("Upstream demanded user/pass auth but none configured")
                u_bytes = username.encode("utf-8")
                p_bytes = password.encode("utf-8")
                auth_pkt = struct.pack("!BB", 0x01, len(u_bytes)) + u_bytes + struct.pack("!B", len(p_bytes)) + p_bytes
                upstream.sendall(auth_pkt)
                auth_ver, auth_status = _recv_exact(upstream, 2)
                if auth_ver != 0x01 or auth_status != 0x00:
                    raise ProxySidecarError("Upstream SOCKS5 RFC 1929 credentials rejected")
            elif method != 0x00:
                raise ProxySidecarError(f"Unsupported SOCKS5 auth method: {method}")

            # Forward CONNECT (0x01) + raw ATYP + dst.addr + dst.port WITHOUT resolving domain locally!
            upstream.sendall(b"\x05\x01\x00" + target_atyp_payload)
            rep_hdr = _recv_exact(upstream, 4)
            if rep_hdr[0] != 0x05 or rep_hdr[1] != 0x00:
                raise ProxySidecarError(f"Upstream SOCKS5 CONNECT failed with code {rep_hdr[1]}")

            # Drain bound address from upstream reply
            atyp = rep_hdr[3]
            if atyp == 0x01:
                _recv_exact(upstream, 4 + 2)
            elif atyp == 0x03:
                dlen = _recv_exact(upstream, 1)[0]
                _recv_exact(upstream, dlen + 2)
            elif atyp == 0x04:
                _recv_exact(upstream, 16 + 2)
            upstream.settimeout(None)
            return upstream
        except Exception:
            self._unregister_sock(upstream)
            raise

    def _handle_client(self, client_sock: socket.socket) -> None:
        self._register_sock(client_sock)
        with self._lock:
            self._active_tunnels += 1
        upstream_sock: socket.socket | None = None
        try:
            if self._kill_switch.is_set():
                return
            if self.quota_limit_bytes is not None and self.total_bytes_relayed >= self.quota_limit_bytes:
                self.trip_kill_switch()
                return

            client_sock.settimeout(self.connect_timeout_sec)
            ver, nmethods = _recv_exact(client_sock, 2)
            if ver != 0x05:
                return
            methods = _recv_exact(client_sock, nmethods)
            if 0x00 not in methods:
                client_sock.sendall(b"\x05\xff")
                return
            client_sock.sendall(b"\x05\x00")

            req_hdr = _recv_exact(client_sock, 4)
            if req_hdr[0] != 0x05 or req_hdr[1] != 0x01:
                client_sock.sendall(b"\x05\x07\x00\x01\x00\x00\x00\x00\x00\x00")
                return

            atyp = req_hdr[3]
            if atyp == 0x01:  # IPv4
                addr_port = _recv_exact(client_sock, 4 + 2)
                target_payload = bytes([atyp]) + addr_port
            elif atyp == 0x03:  # DOMAINNAME (remote DNS resolution via upstream!)
                dlen_b = _recv_exact(client_sock, 1)
                addr_port = _recv_exact(client_sock, dlen_b[0] + 2)
                target_payload = bytes([atyp]) + dlen_b + addr_port
            elif atyp == 0x04:  # IPv6
                addr_port = _recv_exact(client_sock, 16 + 2)
                target_payload = bytes([atyp]) + addr_port
            else:
                client_sock.sendall(b"\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00")
                return

            try:
                upstream_sock = self._dial_upstream_socks5(target_payload)
            except Exception:
                # FAIL-CLOSED: Reply general SOCKS failure and close immediately — zero direct bypass!
                try:
                    client_sock.sendall(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
                except OSError:
                    pass
                return

            client_sock.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")
            client_sock.settimeout(None)
            self._relay_loop(client_sock, upstream_sock)
        except Exception:
            pass
        finally:
            if upstream_sock is not None:
                self._unregister_sock(upstream_sock)
            self._unregister_sock(client_sock)
            with self._lock:
                self._active_tunnels = max(0, self._active_tunnels - 1)

    def _relay_loop(self, sock_a: socket.socket, sock_b: socket.socket) -> None:
        peers = [sock_a, sock_b]
        while not self._kill_switch.is_set():
            readable, _, _ = select.select(peers, [], [], 0.25)
            for r in readable:
                other = sock_b if r is sock_a else sock_a
                data = r.recv(16384)
                if not data:
                    return
                with self._lock:
                    self.total_bytes_relayed += len(data)
                    over_quota = (
                        self.quota_limit_bytes is not None
                        and self.total_bytes_relayed > self.quota_limit_bytes
                    )
                if over_quota:
                    self.trip_kill_switch()
                    return
                other.sendall(data)
