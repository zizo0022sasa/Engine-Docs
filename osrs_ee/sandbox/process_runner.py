"""Cross-platform isolated JVM / child process launcher & tree killer (PR-05b / AUD-24 / T16).

Supports:
- Windows: `CREATE_NEW_PROCESS_GROUP` + `taskkill /F /T /PID` tree termination.
- Linux/POSIX: `start_new_session=True` (`setsid`) + `os.killpg(pgid, SIGKILL)` orphan-free
  process-group termination + optional `xvfb-run -a` headless display wrapper.
- Secure environment injection for Jagex Account (`JX_*`) variables without exposing CLI args.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from pathlib import Path


class ManagedProcessTree:
    """Launches a session process in its own OS process group and kills the entire tree cleanly."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: str | Path | None = None,
        extra_env: dict[str, str] | None = None,
        use_xvfb_if_headless: bool = True,
    ) -> None:
        self._raw_command = list(command)
        self.cwd = Path(cwd) if cwd else None
        self._extra_env = dict(extra_env or {})
        self.use_xvfb_if_headless = use_xvfb_if_headless
        self._proc: subprocess.Popen[bytes] | None = None
        self.started_monotonic: float | None = None

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def build_effective_command(self) -> list[str]:
        cmd = list(self._raw_command)
        if (
            os.name != "nt"
            and self.use_xvfb_if_headless
            and not os.environ.get("DISPLAY")
            and shutil.which("xvfb-run")
        ):
            cmd = ["xvfb-run", "-a", *cmd]
        return cmd

    def start(self) -> int:
        if self._proc is not None and self.is_alive():
            raise RuntimeError("ManagedProcessTree is already running")

        env = os.environ.copy()
        env.update(self._extra_env)
        effective_cmd = self.build_effective_command()

        popen_kwargs: dict[str, object] = {
            "cwd": str(self.cwd) if self.cwd else None,
            "env": env,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
        }
        if os.name == "nt":
            popen_kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        else:
            popen_kwargs["start_new_session"] = True

        self._proc = subprocess.Popen(effective_cmd, **popen_kwargs)  # type: ignore[arg-type]
        self.started_monotonic = time.monotonic()
        return self._proc.pid

    def kill_process_tree(self, *, grace_sec: float = 0.5) -> None:
        """Terminate parent and ALL child/grandchild processes with zero zombie/orphan leakage."""
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is not None:
            self._close_pipes(proc)
            return

        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
        else:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                self._close_pipes(proc)
                return

            deadline = time.monotonic() + grace_sec
            while time.monotonic() < deadline and proc.poll() is None:
                time.sleep(0.03)

            if proc.poll() is None:
                try:
                    pgid = os.getpgid(proc.pid)
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()

        self._close_pipes(proc)

    @staticmethod
    def _close_pipes(proc: subprocess.Popen[bytes]) -> None:
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass
