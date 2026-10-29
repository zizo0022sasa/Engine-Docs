"""`PR-19.3` — Collision-map / non-walkable destination remediation (T83..T86).

Field incident #3 (live `acc_02`, tick 1230, stalled at the sheep farm `(3190, 3273)`).
Verbatim client evidence (`C:\\Users\\USER\\.runelite\\logs\\client.log`, 2026-10-29 15:39:43 EEST):

    [Walker] walk rejected: target WorldPoint(x=3172, y=3317, plane=0) has no walkable tile within 0
        in the collision map (nearest walkable WorldPoint(x=3171, y=3316, plane=0));
        check the destination coordinate
    [WalkerTelemetry] UNREACHABLE cause=target-not-walkable ... totalUnreachable=462

Root cause: `LUMBRIDGE_COW_FIELD` pointed at the tile the **dairy cow entity** occupies, so `Rs2Walker`
refused every walk (`threshold=0`) and the bot stood still until the progress guard fired.

Two-layer fix under test:
1. The exact coordinate is corrected to the walker-verified adjacent tile `(3171, 3316)`, and a direct
   `SHEEP_FARM -> CHICKEN_COOP` spur stops routing through the cow field at all.
2. **Systemic:** `FINAL_TARGET_SNAP_OFFSETS` — when the route stalls on its FINAL waypoint, the
   navigation engine rotates through a deterministic ring of nearby tiles until the client accepts one.
   Any future unwalkable destination (NPC/object on the tile) now self-heals instead of halting.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from osrs_ee.bots.base import DirectiveType
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.supply_procurement import EGG_ID
from osrs_ee.core import ExecutionEngineKernel, ReactiveSafetyWatchdog, SessionLifecycleManager
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph, NavigationEngine
from osrs_ee.navigation.waypoint_graph import (
    DEFAULT_WORLD_NODES,
    FINAL_TARGET_SNAP_OFFSETS,
    WorldPoint,
)
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.state import AccountStatus, StateStore

COOP = DEFAULT_WORLD_NODES["LUMBRIDGE_CHICKEN_COOP"]
COW_FIELD = DEFAULT_WORLD_NODES["LUMBRIDGE_COW_FIELD"]
# The exact tile the live client rejected (occupied by the dairy cow entity).
FIELD_REJECTED_TILE = (3172, 3317)
# The tile the live client reported as the nearest walkable one.
FIELD_WALKABLE_TILE = (3171, 3316)


# ---------------------------------------------------------------------------
# T83 — the corrected coordinate is the one the live walker accepted
# ---------------------------------------------------------------------------
def test_t83_cow_field_coord_is_the_walker_verified_tile_and_direct_spur_exists() -> None:
    """T83 (`PR-19.3`): `LUMBRIDGE_COW_FIELD` must be `(3171, 3316)` — the tile `Rs2Walker` itself
    reported as walkable in the live log — never the cow entity tile `(3172, 3317)`."""
    assert (COW_FIELD.x, COW_FIELD.y) == FIELD_WALKABLE_TILE, (COW_FIELD.x, COW_FIELD.y)
    assert (COW_FIELD.x, COW_FIELD.y) != FIELD_REJECTED_TILE

    graph = ConstrainedWaypointGraph()
    edge_ids = {e.edge_id for e in graph.edges}
    assert "supply_sheep_farm_to_chicken_coop" in edge_ids
    assert "supply_sheep_farm_to_chicken_coop__rev" in edge_ids

    caps = AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3, quest_points=12)
    plan = graph.plan_safe_route(
        origin="LUMBRIDGE_SHEEP_FARM", destination="LUMBRIDGE_CHICKEN_COOP", capabilities=caps
    )
    # The direct spur must win over the detour through the cow field.
    assert "supply_sheep_farm_to_chicken_coop" in plan.traversed_edges, plan.traversed_edges


# ---------------------------------------------------------------------------
# T84 — the engine's snap ring is well-formed and covers the interaction tolerance
# ---------------------------------------------------------------------------
def test_t84_snap_ring_is_deterministic_starts_at_origin_and_stays_within_tolerance() -> None:
    """T84 (`PR-19.3`): the ring starts at the original tile (so nothing changes unless the client
    actually refuses), is duplicate-free, and every offset stays within 3 tiles — the supply engine's
    `walk_tolerance`, so a snapped arrival still counts as "at the area"."""
    assert FINAL_TARGET_SNAP_OFFSETS[0] == (0, 0)
    assert len(set(FINAL_TARGET_SNAP_OFFSETS)) == len(FINAL_TARGET_SNAP_OFFSETS)
    for ox, oy in FINAL_TARGET_SNAP_OFFSETS:
        assert max(abs(ox), abs(oy)) <= 3, (ox, oy)
    # must offer real alternatives in all four directions
    assert any(ox > 0 for ox, _ in FINAL_TARGET_SNAP_OFFSETS)
    assert any(ox < 0 for ox, _ in FINAL_TARGET_SNAP_OFFSETS)
    assert any(oy > 0 for _, oy in FINAL_TARGET_SNAP_OFFSETS)
    assert any(oy < 0 for _, oy in FINAL_TARGET_SNAP_OFFSETS)


# ---------------------------------------------------------------------------
# T85 — replays the field rejection hermetically
# ---------------------------------------------------------------------------
def test_t85_navigation_self_heals_when_the_client_rejects_the_destination_tile() -> None:
    """T85 (`PR-19.3`): replays the live `walk rejected` signature. With the commanded destination tile
    blocked in the collision map, the engine must stop re-sending it and rotate through the snap ring
    until a walkable tile is offered — for a single-hop leg (the field case) AND for a micro-route.
    """
    caps = AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3, quest_points=12)

    cases = (
        # (label, origin, destination, blocked tile) — single-hop leg, exactly like the live session
        ("single-hop", WorldPoint(3190, 3273, 0, "LUMBRIDGE_SHEEP_FARM"), WorldPoint(3175, 3303, 0, "COOP"), (3175, 3303)),
        # micro-route (chebyshev <= 20 -> direct two-point leg)
        ("micro-route", WorldPoint(3171, 3317, 0, "A"), WorldPoint(3173, 3319, 0, "B"), (3173, 3319)),
    )
    for label, origin, destination, blocked_tile in cases:
        nav = NavigationEngine(ConstrainedWaypointGraph())
        seen: list[tuple[int, int]] = []
        for _ in range(1, 40):
            payload = nav.step_navigation_on_tick(
                current_pos=origin,  # the client refuses the walk, so the character never moves
                tick_advanced=True,
                capabilities=caps,
                destination=destination,
            )
            if payload is not None:
                seen.append((int(payload["x"]), int(payload["y"])))
        accepted = [t for t in seen if t != blocked_tile]
        assert seen, f"{label}: no payload dispatched at all"
        assert accepted, f"{label}: never rotated off the rejected tile: {seen}"
        ox, oy = accepted[0]
        assert max(abs(ox - blocked_tile[0]), abs(oy - blocked_tile[1])) <= 3, (label, accepted[0])


# ---------------------------------------------------------------------------
# T86 — full kernel replay of the live session
# ---------------------------------------------------------------------------
def test_t86_kernel_replay_of_the_live_collision_rejection_still_collects_the_egg(tmp_path: Path) -> None:
    """T86 (`PR-19.3`): end-to-end replay — empty bank, the dairy-cow tile blocked exactly as in the
    live client, `2 tiles/tick` walking. The bot must still reach the chicken coop and take the egg,
    with zero `ESCALATE_HUMAN`, proving the field halt at tick 1230 cannot recur.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = StateStore(tmp_path / "pr19_3.db")
    store.register_account(
        account_id="acc_02",
        auth_mode="LEGACY_CREDENTIALS",
        credential_ref="vault://accounts/acc_02/auth",
        proxy_ref="vault://proxies/direct",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=202,
        initial_status=AccountStatus.READY,
    )
    lease = store.acquire_session_lease(
        account_id="acc_02",
        slot_id=0,
        task_id="stage1a_f2p_quests_10qp",
        now_utc=datetime(2026, 10, 29, 15, 30, tzinfo=timezone.utc),
        bypass_circadian=True,
    )
    adapter = FakeGameAdapter(
        account_id="acc_02",
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        skills={"hitpoints": 10},
        combat_level=3,
        quest_points=2,
        total_level=35,
        playtime_minutes=200,
        bank_items=[],
        inventory_items=[],
        ground_items=[{"item_id": EGG_ID, "name": "Egg", "quantity": 1}],
        walk_speed_tiles_per_tick=2,
        # Both the historic cow tile AND the coop destination tile are blocked, so the snap ring itself
        # (not just the corrected coordinate) must carry the bot to the egg — the true generic class.
        unwalkable_tiles={FIELD_REJECTED_TILE, (COOP.x, COOP.y)},
    )
    adapter.varps.update({29: 0, 179: 21, 144: 100, 62: 6})
    adapter.run_energy = 100
    adapter.run_enabled = False
    # Start exactly where the live session stalled.
    adapter.position = {"x": 3190, "y": 3273, "plane": 0}

    gate = GameAdapterGate(store, adapter)
    gate.bind_to_lease(
        account_id="acc_02",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
    )
    kernel = ExecutionEngineKernel(
        gate=gate,
        account_id="acc_02",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3, quest_points=2),
        lifecycle=SessionLifecycleManager(),
        watchdog=ReactiveSafetyWatchdog(),
        navigation=NavigationEngine(ConstrainedWaypointGraph()),
    )

    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    reached = False
    for tick in range(1, 600):
        res = kernel.step_tick(bot, now_monotonic=0.0)
        directive = res.task_directive
        if directive is not None and directive.directive_type == DirectiveType.ESCALATE_HUMAN:
            raise AssertionError(f"halted at tick {tick} (the tick-1230 field bug): {directive.reason}")
        if any(int(it["item_id"]) == EGG_ID for it in adapter.inventory_items):
            reached = True
            break

    assert reached, f"never collected the egg (pos={adapter.position})"
    assert adapter.walk_rejections > 0, (
        "the collision-map rejection never fired — the test did not exercise the snap ring"
    )
