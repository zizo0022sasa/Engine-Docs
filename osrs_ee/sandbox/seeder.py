"""Pre-launch sandbox profile seeder, Golden Cache bootstrapper, and JVM launcher args (PR-05a).

Addresses AUD-03 (--disable-telemetry), AUD-07 (AgentServerPlugin enabledByDefault=false),
AUD-08 (socks5://127.0.0.1 sidecar only), AUD-25 (-Xmx768m & GPU off), AUD-26 (Golden Cache + hwid-agent),
and C-06 (Regional Allowed World Pool).
"""

from __future__ import annotations

import os
import secrets
import shutil
from dataclasses import dataclass
from pathlib import Path

from osrs_ee.contracts.config import PersonaProfile

# True OSRS F2P regional server pools mapped by lowest latency (C-06: EG -> EU DE/UK)
REGIONAL_F2P_WORLDS: dict[str, tuple[int, ...]] = {
    "EG": (308, 316, 335, 379, 383),       # EU (UK / Germany) — closest real Jagex region (~45-75ms)
    "DE": (379, 383, 394, 463),            # Germany F2P worlds
    "UK": (308, 316, 335, 371),            # UK F2P worlds
    "US_EAST": (301, 330, 393, 434),       # US East F2P worlds
    "US_WEST": (307, 313, 430, 431),       # US West F2P worlds
}

_FORBIDDEN_CACHE_IDENTITY_FILES = frozenset(
    {
        "random.dat",
        "preferences.dat",
        "preferences2.dat",
        "settings.properties",
        "credentials.properties",
        "spoofed-device-ids.properties",
    }
)


def resolve_regional_world_pool(region: str) -> list[int]:
    key = region.strip().upper()
    if key not in REGIONAL_F2P_WORLDS:
        raise ValueError(f"Unsupported region {region!r}; allowed={sorted(REGIONAL_F2P_WORLDS)}")
    return list(REGIONAL_F2P_WORLDS[key])


@dataclass(frozen=True)
class SandboxBootstrapResult:
    account_id: str
    sandbox_root: Path
    user_home: Path
    profile_properties_path: Path
    token_file_path: Path
    hwid_store_path: Path
    adapter_port: int
    local_socks5_port: int
    persona: PersonaProfile


class SandboxProfileSeeder:
    """Prepares a fresh, isolated per-account sandbox before launching a Microbot JVM."""

    def __init__(self, sandboxes_base_dir: str | Path, *, golden_cache_dir: str | Path | None = None) -> None:
        self.base_dir = Path(sandboxes_base_dir)
        self.golden_cache_dir = Path(golden_cache_dir) if golden_cache_dir else None

    def prepare_sandbox(
        self,
        *,
        account_id: str,
        persona_seed: int,
        adapter_port: int,
        local_socks5_port: int,
    ) -> tuple[SandboxBootstrapResult, str]:
        """Seed RuneLite profile, 0600 CSPRNG token, and static Golden Cache.

        Returns `(SandboxBootstrapResult, ephemeral_adapter_token)`.
        """
        if not account_id.startswith("acc_") or "@" in account_id:
            raise ValueError(f"Invalid opaque account_id: {account_id!r}")
        if adapter_port == local_socks5_port:
            raise ValueError("adapter_port and local_socks5_port must be distinct")

        persona = PersonaProfile.from_seed(account_id, persona_seed)
        sandbox_root = self.base_dir / account_id
        user_home = sandbox_root / "home"
        runelite_dir = user_home / ".runelite"
        profiles_dir = runelite_dir / "profiles2"
        profiles_dir.mkdir(parents=True, exist_ok=True)

        # 1. Seed Golden Cache if provided, strictly excluding any identity/random.dat files!
        if self.golden_cache_dir and self.golden_cache_dir.is_dir():
            target_cache = user_home / "jagexcache"
            target_cache.mkdir(parents=True, exist_ok=True)
            for src_file in self.golden_cache_dir.rglob("*"):
                if src_file.is_file() and src_file.name not in _FORBIDDEN_CACHE_IDENTITY_FILES:
                    rel = src_file.relative_to(self.golden_cache_dir)
                    dst = target_cache / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if not dst.exists():
                        shutil.copy2(src_file, dst)

        # 2. Generate fresh per-session CSPRNG X-Agent-Token with 0600 permissions
        token = secrets.token_urlsafe(32)
        token_file = runelite_dir / ".agent-token"
        token_file.write_text(token, encoding="utf-8")
        if os.name != "nt":
            token_file.chmod(0o600)

        # 3. Pre-seed RuneLite profile enabling AgentServerPlugin (off by default in Microbot!),
        #    muting audio, stripping heavy GPU plugins, and applying per-account resolution jitter.
        props_lines = [
            "# Auto-seeded by OSRS_ee SandboxProfileSeeder (Normative v2.1)",
            "runelite.agentserverplugin=true",
            f"agentServer.port={adapter_port}",
            "runelite.gpuplugin=false",
            "runelite.hd117plugin=false",
            "runelite.loottrackerplugin=false",
            "runelite.groundmarkersplugin=false",
            "runelite.fpsplugin=true",
            f"fpscontrol.maxFps={persona.target_fps}",
            "fpscontrol.limitFps=true",
            "fpscontrol.limitFpsUnfocused=true",
            f"fpscontrol.maxFpsUnfocused={persona.target_fps}",
            f"runelite.gameSize={persona.window_width}x{persona.window_height}",
            "music.mute=true",
            "soundEffect.mute=true",
            "areaSoundEffect.mute=true",
        ]
        profile_props = profiles_dir / "default.properties"
        profile_props.write_text("\n".join(props_lines) + "\n", encoding="utf-8")

        hwid_path = sandbox_root / "hwid.properties"
        if not hwid_path.exists():
            hwid_path.write_text(f"# Per-account HWID store for {account_id}\n", encoding="utf-8")
            if os.name != "nt":
                hwid_path.chmod(0o600)

        return (
            SandboxBootstrapResult(
                account_id=account_id,
                sandbox_root=sandbox_root,
                user_home=user_home,
                profile_properties_path=profile_props,
                token_file_path=token_file,
                hwid_store_path=hwid_path,
                adapter_port=adapter_port,
                local_socks5_port=local_socks5_port,
                persona=persona,
            ),
            token,
        )


def build_isolated_jvm_command(
    *,
    java_bin: str,
    client_jar: str | Path,
    bootstrap: SandboxBootstrapResult,
    hwid_agent_jar: str | Path | None = None,
    proxy_uri: str | None = None,
) -> list[str]:
    """Construct verified Microbot JVM launch arguments.

    Forbids HTTP proxies (which trigger System.exit(1) in ProxyConfiguration.java) and non-loopback proxies.
    """
    effective_proxy = proxy_uri or f"socks5://127.0.0.1:{bootstrap.local_socks5_port}"
    if not effective_proxy.startswith("socks5://127.0.0.1:"):
        raise ValueError(
            f"Client JVM must connect only to Per-Slot Local SOCKS5 Sidecar (socks5://127.0.0.1:<port>), got {effective_proxy!r}"
        )

    cmd = [
        java_bin,
        "-Xms256m",
        "-Xmx768m",
        "-XX:MaxMetaspaceSize=192m",
        "-XX:+UseG1GC",
        "-XX:+UseStringDeduplication",
        f"-Duser.home={bootstrap.user_home}",
        "-Dmicrobot.disableTelemetry=true",
        f"-Dmicrobot.agent.port={bootstrap.adapter_port}",
    ]
    if hwid_agent_jar is not None:
        cmd.append(f"-javaagent:{hwid_agent_jar}={bootstrap.hwid_store_path}")
    cmd.extend(
        [
            "-jar",
            str(client_jar),
            "--disable-telemetry",
            f"--proxy={effective_proxy}",
        ]
    )
    return cmd
