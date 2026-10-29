"""`PR-19.4` — Underground classification & authorized surface exits (T87..T91).

Field incident #4 (live `acc_02`, tick 1094): after successfully taking the **Egg** (`1944`, tick 894)
and the **Bucket** (`1925`, tick 1076) the bot climbed out of the Lumbridge Castle basement and the
watchdog halted it:

    [tick=1094] state=IN_DANGER | dir=NONE
    Unexpected underground->surface transition to (3209, 3217, 0) without ladder authorization

Root cause: `y >= 5000` conflated *genuine dungeons* (Stronghold of Security, `5100..5400`) with an
ordinary stair-connected **cellar** (`y = 9625`). Climbing the kitchen staircase was then misread as a
death respawn / teleport.

Fix under test (two layers):
1. Region-aware classification — `UNDERGROUND_REGIONS` + `classify_underground()`; shallow basements
   never arm the guard; genuine dungeons keep it (fail-closed for unknown caves).
2. Authorized exits — `EXIT_UNDERGROUND` steps emit `AUTHORIZE_SURFACE_EXIT` (the established
   Stronghold pattern), and the engine refuses to walk out of a cave without one.

- T87: the cellar is shallow; the exact field transition no longer halts.
- T88: the Stronghold guard still fires (safety property preserved, not weakened).
- T89: unknown caves stay fail-closed; a >256-tile dungeon jump still halts.
- T90: the engine emits `AUTHORIZE_SURFACE_EXIT` while underground and refuses to walk out.
- T91: full kernel replay of the field session (egg + bucket + exit + milk) with zero escalation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from osrs_ee.bots.base import DirectiveType
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.supply_procurement import (
    BUCKET_ID,
    BUCKET_OF_MILK_ID,
    EGG_ID,
    ZeroTouchSupplyEngine,
)
from osrs_ee.core import ExecutionEngineKernel, ReactiveSafetyWatchdog, SessionLifecycleManager
from osrs_ee.core.reactive_watchdog import SafetyVerdict, classify_underground
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph, NavigationEngine
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.state import AccountStatus, StateStore

BASEMENT = DEFAULT_WORLD_NODES["LUMBRIDGE_CASTLE_BASEMENT"]
KITCHEN = DEFAULT_WORLD_NODES["LUMBRIDGE_KITCHEN"]
COW_FIELD = DEFAULT_WORLD_NODES["LUMBRIDGE_COW_FIELD"]
# Exact field coordinates from the live log.
FIELD_BASEMENT_POS = (3216, 9625)
FIELD_EXIT_POS = (3209, 3217)
# Stronghold of Security floor-4 coordinates used throughout this repo.
SOS_POS = (2340, 5200)


def _pulse(
    wd: ReactiveSafetyWatchdog,
    pos: tuple[int, int, int] | None = None,
    *,
    x: int | None = None,
    y: int | None = None,
    tick: int = 1,
    inventory: tuple[dict[str, Any], ...] = (),
):
    if pos is None:
        assert x is not None and y is not None
        pos = (x, y, 0)
    return wd.evaluate_pulse(
        game_tick=tick,
        hp_current=10,
        hp_max=10,
        run_energy=100,
        run_enabled=False,
        under_attack=False,
        position=pos,
        inventory_items=inventory,
    )


# ---------------------------------------------------------------------------
# T87 — the exact field transition is now legal
# ---------------------------------------------------------------------------
def test_t87_cellar_exit_no_longer_trips_the_displacement_guard() -> None:
    """T87 (`PR-19.4`): standing in the Lumbridge cellar (`y=9625`) then appearing in the kitchen
    (`y=3217`) must be NOMINAL — this is the exact tick-1094 halt from the live session."""
    assert classify_underground(*FIELD_BASEMENT_POS).name == "LUMBRIDGE_CASTLE_BASEMENT"
    assert classify_underground(*FIELD_BASEMENT_POS).requires_authorized_exit is False
    assert classify_underground(*FIELD_EXIT_POS) is None

    wd = ReactiveSafetyWatchdog()
    assert _pulse(wd, (*FIELD_BASEMENT_POS, 0)).verdict != SafetyVerdict.UNEXPECTED_DISPLACEMENT_HALT
    decision = _pulse(wd, (*FIELD_EXIT_POS, 0), tick=2)
    assert decision.verdict != SafetyVerdict.UNEXPECTED_DISPLACEMENT_HALT, decision.detail
    assert decision.halt_session is False
    assert decision.in_danger is False


# ---------------------------------------------------------------------------
# T88 — the real guard is preserved
# ---------------------------------------------------------------------------
def test_t88_stronghold_death_respawn_guard_still_fires() -> None:
    """T88 (`PR-19.4`): the guard this logic exists for must still work — leaving the Stronghold of
    Security `(5100..5400)` without authorization still halts the session."""
    assert classify_underground(*SOS_POS).name == "STRONGHOLD_OF_SECURITY"
    assert classify_underground(*SOS_POS).requires_authorized_exit is True

    wd = ReactiveSafetyWatchdog()
    assert _pulse(wd, (*SOS_POS, 0)).halt_session is False
    death_respawn = _pulse(wd, None, x=3222, y=3218, tick=2)  # Lumbridge courtyard respawn point
    assert death_respawn.verdict == SafetyVerdict.UNEXPECTED_DISPLACEMENT_HALT
    assert death_respawn.halt_session is True

    # ...and an EXPLICIT authorization (the Stronghold module's Bone-chain climb) clears it.
    wd2 = ReactiveSafetyWatchdog()
    _pulse(wd2, (*SOS_POS, 0))
    wd2.authorize_surface_exit()
    assert _pulse(wd2, None, x=3222, y=3218, tick=2).halt_session is False


# ---------------------------------------------------------------------------
# T89 — unknown caves stay fail-closed
# ---------------------------------------------------------------------------
def test_t89_unknown_underground_stays_fail_closed() -> None:
    """T89 (`PR-19.4`): an unclassified cave (`y >= 5000`, outside every known region) keeps the
    legacy protective behaviour, and a >256-tile in-dungeon jump still halts."""
    unknown = classify_underground(3300, 9999)
    assert unknown is not None and unknown.requires_authorized_exit is True

    wd = ReactiveSafetyWatchdog()
    _pulse(wd, (3300, 9999, 0))
    assert _pulse(wd, None, x=3222, y=3218, tick=2).verdict == SafetyVerdict.UNEXPECTED_DISPLACEMENT_HALT

    wd2 = ReactiveSafetyWatchdog()
    _pulse(wd2, (2340, 5200, 0))
    far_jump = _pulse(wd2, (2340, 5300, 0), tick=2)  # 100 tiles is fine
    assert far_jump.verdict != SafetyVerdict.UNEXPECTED_DISPLACEMENT_HALT
    # 260 tiles inside the dungeon (5300 -> 5040) is beyond the 256-tile sanity bound.
    huge_jump = _pulse(wd2, (2340, 5040, 0), tick=3)
    assert huge_jump.verdict == SafetyVerdict.UNEXPECTED_DISPLACEMENT_HALT


# ---------------------------------------------------------------------------
# T90 — the engine authorizes the exit by intent
# ---------------------------------------------------------------------------
def test_t90_engine_emits_authorized_exit_and_never_walks_out_of_a_cave() -> None:
    """T90 (`PR-19.4`): in the cellar with the bucket in hand, the engine must emit
    `AUTHORIZE_SURFACE_EXIT` (stairs interaction) instead of walking toward the surface; and a recipe
    with no declared exit must escalate `SUPPLY_EXIT_REQUIRED` rather than issuing a hopeless walk."""
    engine = ZeroTouchSupplyEngine()
    inv = ({"item_id": BUCKET_ID, "name": "Bucket", "quantity": 1},)

    d = engine.ensure(
        _tick_ctx(BASEMENT, inventory=inv),
        BUCKET_OF_MILK_ID,
        1,
    )
    assert d is not None, "bucket already taken -> the engine must progress to the exit step"
    assert d.directive_type == DirectiveType.AUTHORIZE_SURFACE_EXIT, d
    assert d.payload["action"] == "Climb-up"
    assert d.payload["name"] in ("Staircase", "Stairs", "Ladder")

    # On the surface it must no longer emit exits.
    d2 = engine.ensure(_tick_ctx(KITCHEN, inventory=inv), BUCKET_OF_MILK_ID, 1)
    assert d2 is not None and d2.directive_type == DirectiveType.NAVIGATE_TO
    assert d2.destination == "LUMBRIDGE_COW_FIELD"

    # A recipe that would leave a cave without an EXIT step is rejected loudly, not silently walked.
    from osrs_ee.bots.supply_procurement import SupplyRecipe, SupplyStep, SupplyStepKind

    no_exit = ZeroTouchSupplyEngine(
        recipes={
            9999: SupplyRecipe(
                item_id=9999,
                label="Test item",
                steps=(
                    SupplyStep(
                        kind=SupplyStepKind.OBJECT,
                        area=WorldPoint(3171, 3316, 0, "LUMBRIDGE_COW_FIELD"),
                        targets=("Crate",),
                        action="Search",
                        produces=((9999, 1),),
                    ),
                ),
            )
        }
    )
    unregistered = WorldPoint(3300, 9999, 0, "UNKNOWN_CAVE")
    blocked = no_exit.ensure(_tick_ctx(unregistered), 9999, 1)
    assert blocked is not None and blocked.directive_type == DirectiveType.ESCALATE_HUMAN
    assert blocked.reason.startswith("SUPPLY_EXIT_REQUIRED:"), blocked.reason

    # A REGISTERED region always gets an authorized exit instead of an escalation.
    registered_exit = no_exit.ensure(_tick_ctx(BASEMENT), 9999, 1)
    assert registered_exit is not None
    assert registered_exit.directive_type == DirectiveType.AUTHORIZE_SURFACE_EXIT, registered_exit


def _tick_ctx(pos: WorldPoint, *, inventory: tuple[dict[str, Any], ...] = ()):
    from osrs_ee.bots.base import TickContext

    return TickContext(
        game_tick=1,
        position=pos,
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=inventory,
        total_level=35,
    )


# ---------------------------------------------------------------------------
# T91 — full kernel replay of the live session
# ---------------------------------------------------------------------------
def test_t91_kernel_replay_egg_bucket_cellar_exit_and_milk(tmp_path: Path) -> None:
    """T91 (`PR-19.4`): replays the live session — take the egg, take the bucket in the cellar, climb
    the kitchen staircase, milk the cow. Must never halt with
    `Unexpected underground->surface transition`."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = StateStore(tmp_path / "pr19_4.db")
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
        now_utc=datetime(2026, 10, 29, 16, 0, tzinfo=timezone.utc),
        bypass_circadian=True,
    )

    def _grants(command_type: str, payload: dict[str, Any], adapter: FakeGameAdapter) -> None:
        name = str(payload.get("name", ""))
        action = str(payload.get("action", ""))
        if command_type == "INTERACT_OBJECT" and name in ("Staircase", "Stairs", "Ladder"):
            if action == "Climb-up" and int(adapter.position["y"]) >= 5000:
                adapter.position = {"x": 3209, "y": 3217, "plane": 0}  # field-verified exit tile
        elif command_type == "INTERACT_NPC" and name == "Dairy cow":
            for it in list(adapter.inventory_items):
                if int(it["item_id"]) == BUCKET_ID:
                    it["quantity"] -= 1
                    if it["quantity"] <= 0:
                        adapter.inventory_items.remove(it)
                    break
            adapter._add_to_inventory(
                {"item_id": BUCKET_OF_MILK_ID, "name": "Bucket of milk", "quantity": 1, "noted": False}
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
        inventory_items=[{"item_id": EGG_ID, "name": "Egg", "quantity": 1, "noted": False}],
        ground_items=[
            {"item_id": BUCKET_ID, "name": "Bucket", "quantity": 1},
        ],
        walk_speed_tiles_per_tick=2,
        interaction_grants=_grants,
    )
    adapter.varps.update({29: 0, 179: 21, 144: 100, 62: 6})
    adapter.run_energy = 100
    adapter.run_enabled = False
    # Start where the live run stopped: inside the cellar, bucket already taken.
    adapter.position = {"x": FIELD_BASEMENT_POS[0], "y": FIELD_BASEMENT_POS[1], "plane": 0}
    adapter.inventory_items.append({"item_id": BUCKET_ID, "name": "Bucket", "quantity": 1, "noted": False})

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
    got_milk = False
    saw_authorized_exit = False
    for tick in range(1, 600):
        res = kernel.step_tick(bot, now_monotonic=0.0)
        directive = res.task_directive
        if directive is not None and directive.directive_type == DirectiveType.AUTHORIZE_SURFACE_EXIT:
            saw_authorized_exit = True
        assert "Unexpected underground->surface transition" not in str(res.detail or ""), (
            f"the tick-1094 halt regressed at tick {tick}: {res.detail}"
        )
        if directive is not None and directive.directive_type == DirectiveType.ESCALATE_HUMAN:
            raise AssertionError(f"halted at tick {tick}: {directive.reason}")
        if any(int(it["item_id"]) == BUCKET_OF_MILK_ID for it in adapter.inventory_items):
            got_milk = True
            break

    assert saw_authorized_exit, "the bot never emitted an authorized surface exit"
    assert got_milk, f"never milked the cow (pos={adapter.position}, inv={adapter.inventory_items})"
    assert int(adapter.position["y"]) < 5000, "the bot is still underground"
