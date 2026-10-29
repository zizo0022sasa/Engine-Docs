"""`PR-19.5` — Field-proven corrections from the live `acc_02` session (T92..T97).

Field results before this patch (all zero operator clicks):
    tick 894   Egg (1944) collected            ✅
    tick 1076  Bucket (1925) collected         ✅
    tick ~811  Bucket of milk: SUPPLY_STEP_STALLED  ❌  (`Milk` was sent to `/npcs/interact`)
    (earlier)  tick 135 IN_DANGER freeze inside a Highwayman's attack range  ❌
    (on re-attach) 200-tile walk back to the GE purely to re-audit the bank  ❌

Ground truth captured live by the field reviewer:
  * `GET /npcs` lists only "Cow" (2790) and "Cow calf" (2794) — neither can be milked.
  * `GET /objects?name=Dairy cow` -> `{id: 8689, name: "Dairy cow", position: {3172,3317}}`.
  * `INTERACT_OBJECT {id:8689, action:"Milk"}` -> `{"success": true}` and the milk lands in the bag.

- T92: the milkable cow is an OBJECT carrying the verified id 8689.
- T93: the watchdog flees on the surface with no food (Anti-Safety-Freeze) instead of freezing,
       and still halts inside a real dungeon.
- T94: the freeze detail message reports the EFFECTIVE threshold, never the base one.
- T95: bank stock is restored from `state.db` with NO walk to the GE; and a stale/empty snapshot
       still falls back to a single live audit.
- T96: `VARROCK_EAST_CHURCH` sits on the field-corrected tile.
- T97: end-to-end kernel run with the persisted bank snapshot: Cook's Assistant supplies resolved
       without the 200-tile round trip.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from osrs_ee.bots.base import DirectiveType, TickContext
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.supply_procurement import (
    BUCKET_ID,
    BUCKET_OF_MILK_ID,
    DAIRY_COW_OBJECT_ID,
    EGG_ID,
    POT_OF_FLOUR_ID,
    SUPPLY_RECIPES,
    ZeroTouchSupplyEngine,
)
from osrs_ee.core import ExecutionEngineKernel, ReactiveSafetyWatchdog, SessionLifecycleManager
from osrs_ee.core.reactive_watchdog import SafetyVerdict
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph, NavigationEngine
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.state import AccountStatus, StateStore

COW_FIELD = DEFAULT_WORLD_NODES["LUMBRIDGE_COW_FIELD"]
GE = DEFAULT_WORLD_NODES["GRAND_EXCHANGE"]
DRAYNOR = DEFAULT_WORLD_NODES["DRAYNOR_CROSSROAD"]


def _ctx(
    pos: WorldPoint,
    *,
    inventory: tuple[dict[str, Any], ...] = (),
    bank_items: tuple[dict[str, Any], ...] = (),
    bank_summary: tuple[dict[str, Any], ...] = (),
    bank_open: bool = False,
) -> TickContext:
    return TickContext(
        game_tick=1,
        position=pos,
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=inventory,
        bank_items=bank_items,
        bank_summary=bank_summary,
        bank_is_open=bank_open,
        total_level=35,
    )


# ---------------------------------------------------------------------------
# T92 — the dairy cow is scenery, not an NPC
# ---------------------------------------------------------------------------
def test_t92_dairy_cow_is_an_object_with_the_verified_id_and_never_targets_npcs() -> None:
    """T92 (`PR-19.5`): the milkable cow must be interacted with as OBJECT `8689`. Sending `Milk` to
    `/npcs/interact` returns success but produces no item (live field proof: budget exhausted)."""
    recipe = SUPPLY_RECIPES[BUCKET_OF_MILK_ID]
    milk_step = recipe.steps[0]
    assert milk_step.kind.value == "OBJECT", milk_step.kind
    assert milk_step.object_id == DAIRY_COW_OBJECT_ID == 8689
    assert milk_step.action == "Milk"

    engine = ZeroTouchSupplyEngine()
    inv = ({"item_id": BUCKET_ID, "name": "Bucket", "quantity": 1},)
    d = engine.ensure(_ctx(COW_FIELD, inventory=inv), BUCKET_OF_MILK_ID, 1)
    assert d is not None
    assert d.directive_type == DirectiveType.INTERACT_OBJECT
    assert d.payload["name"] == "Dairy cow"
    assert d.payload["action"] == "Milk"
    assert d.payload["id"] == 8689


# ---------------------------------------------------------------------------
# T93 — Anti-Safety-Freeze
# ---------------------------------------------------------------------------
def _pulse(wd: ReactiveSafetyWatchdog, x: int, y: int, *, tick: int = 1, hp: int = 8, attacked: bool = True):
    return wd.evaluate_pulse(
        game_tick=tick,
        hp_current=hp,
        hp_max=10,
        run_energy=100,
        run_enabled=False,
        under_attack=attacked,
        position=(x, y, 0),
        inventory_items=(),
    )


def test_t93_surface_no_food_flees_instead_of_freezing_and_dungeon_still_halts() -> None:
    """T93 (`PR-19.5`): the tick-135 halt parked a level-3 account inside a Highwayman's reach. With no
    food on the surface the watchdog must KEEP MOVING (flee + run); the hard halt stays for dungeons.
    """
    surface = _pulse(ReactiveSafetyWatchdog(), 3105, 3268)  # Draynor road, bandit territory
    assert surface.verdict == SafetyVerdict.EMERGENCY_NO_FOOD_FLEE
    assert surface.halt_session is False, "must never freeze the account inside attack range"
    assert surface.preserve_movement is True
    assert surface.enable_run is True
    assert surface.in_danger is True

    # The shallow Lumbridge cellar is also a flee case (stair-connected, not a dungeon).
    cellar = _pulse(ReactiveSafetyWatchdog(), 3216, 9625)
    assert cellar.verdict == SafetyVerdict.EMERGENCY_NO_FOOD_FLEE

    # A real dungeon keeps the legacy hard halt (death there is unrecoverable).
    dungeon = _pulse(ReactiveSafetyWatchdog(), 2340, 5200)
    assert dungeon.verdict == SafetyVerdict.EMERGENCY_NO_FOOD_EVACUATE
    assert dungeon.halt_session is True


def test_t94_freeze_message_reports_the_effective_threshold() -> None:
    """T94 (`PR-19.5`): the live log printed `HP=8 <= 7` (the BASE threshold) while the effective one
    was 8. The message must always quote the threshold actually applied."""
    decision = _pulse(ReactiveSafetyWatchdog(), 2340, 5200, hp=8)
    assert "<= 8" in decision.detail, decision.detail
    assert "<= 7" not in decision.detail


# ---------------------------------------------------------------------------
# T95 — bank stock survives a restart
# ---------------------------------------------------------------------------
def test_t95_bank_snapshot_from_state_db_avoids_the_ge_round_trip() -> None:
    """T95 (`PR-19.5`): with persisted bank stock available, the audit must resolve instantly from
    `state.db` — no 200-tile walk to the GE. Without it, the single live audit is still performed."""
    bot = TradeUnlockQuestsTaskModule()
    summary = (
        {"item_id": EGG_ID, "name": "Egg", "quantity": 1, "noted": False},
        {"item_id": BUCKET_OF_MILK_ID, "name": "Bucket of milk", "quantity": 1, "noted": False},
        {"item_id": POT_OF_FLOUR_ID, "name": "Pot of flour", "quantity": 1, "noted": False},
    )
    # Standing in Draynor (200+ tiles from the GE) with an empty bag.
    d = bot._procure(
        _ctx(DRAYNOR, bank_summary=summary), POT_OF_FLOUR_ID, 1, "Pot of flour"
    )
    assert d is not None
    # The very first directive is the persisted-audit acknowledgement — never a 200-tile walk.
    assert d.directive_type == DirectiveType.WAIT_NEXT_TICK, d
    assert "restored from state.db" in d.reason
    assert bot._bank_source.startswith("PERSISTED")
    assert bot._bank_stock[POT_OF_FLOUR_ID] == 1

    # The NEXT tick (audit done) may then walk to a bank to make the purposeful withdrawal.
    d_next = bot._procure(_ctx(DRAYNOR, bank_summary=summary), POT_OF_FLOUR_ID, 1, "Pot of flour")
    assert d_next is not None
    assert d_next.directive_type == DirectiveType.NAVIGATE_TO
    assert d_next.destination == "GRAND_EXCHANGE"

    # Empty persisted summary -> the bot still performs exactly one live audit at the GE anchor.
    fresh = TradeUnlockQuestsTaskModule()
    d2 = fresh._procure(_ctx(DRAYNOR), POT_OF_FLOUR_ID, 1, "Pot of flour")
    assert d2 is not None and d2.directive_type == DirectiveType.NAVIGATE_TO
    assert d2.destination == "GRAND_EXCHANGE"
    assert fresh._bank_source == "UNKNOWN" or fresh._bank_source.startswith("PERSISTED") is False

    # An item that is NOT in the persisted bank must skip the GE entirely and go gather.
    not_banked = TradeUnlockQuestsTaskModule()
    d3 = not_banked._procure(
        _ctx(DRAYNOR, bank_summary=(summary[0],)), POT_OF_FLOUR_ID, 1, "Pot of flour"
    )
    assert d3 is not None
    assert not (d3.directive_type == DirectiveType.NAVIGATE_TO and d3.destination == "GRAND_EXCHANGE"), (
        "an item missing from the bank must not trigger a GE trip; gather it instead"
    )


# ---------------------------------------------------------------------------
# T96 — field-corrected church tile
# ---------------------------------------------------------------------------
def test_t96_varrock_east_church_uses_the_field_corrected_tile() -> None:
    """T96 (`PR-19.5`): `(3254, 3483)` sits on the church's outer wall; the field-measured walkable
    tile is `(3253, 3482)`."""
    church = DEFAULT_WORLD_NODES["VARROCK_EAST_CHURCH"]
    assert (church.x, church.y) == (3253, 3482)


# ---------------------------------------------------------------------------
# T97 — kernel end-to-end with the persisted snapshot
# ---------------------------------------------------------------------------
def test_t97_kernel_cooks_assistant_from_persisted_bank_without_ge_round_trip(tmp_path: Path) -> None:
    """T97 (`PR-19.5`): a restarted session standing in Draynor with the three Cook's Assistant supplies
    already in the persisted bank must not first walk 200 tiles to the GE just to audit. The audit is
    restored from `state.db`, so the very first supply action is a deliberate bank visit."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = StateStore(tmp_path / "pr19_5.db")
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
    store.save_account_profile(
        {
            "account_id": "acc_02",
            "combat_level": 3,
            "total_level": 35,
            "skills": {"hitpoints": 10},
            "skill_xp": {"hitpoints": 1154},
            "is_member": False,
            "membership_days": 0,
            "tutorial_varp_281": 1000,
            "quest_points": 2,
            "completed_quests": [],
            "stronghold_floors_claimed": [],
            "playtime_minutes": 200,
            "bank_coins_gp": 10000,
            "bank_scanned": True,
            "bank_summary": [
                {"item_id": EGG_ID, "name": "Egg", "quantity": 1, "noted": False},
                {"item_id": BUCKET_OF_MILK_ID, "name": "Bucket of milk", "quantity": 1, "noted": False},
                {"item_id": POT_OF_FLOUR_ID, "name": "Pot of flour", "quantity": 1, "noted": False},
            ],
            "scanned_at": "2026-10-29T16:40:00+00:00",
            "inventory_coins_gp": 0,
            "inventory_summary": [],
            "position": {"x": 3110, "y": 3295, "plane": 0},
        }
    )
    lease = store.acquire_session_lease(
        account_id="acc_02",
        slot_id=0,
        task_id="stage1a_f2p_quests_10qp",
        now_utc=datetime(2026, 10, 29, 17, 0, tzinfo=timezone.utc),
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
        bank_items=[
            {"item_id": EGG_ID, "name": "Egg", "quantity": 1, "noted": False},
            {"item_id": BUCKET_OF_MILK_ID, "name": "Bucket of milk", "quantity": 1, "noted": False},
            {"item_id": POT_OF_FLOUR_ID, "name": "Pot of flour", "quantity": 1, "noted": False},
        ],
        inventory_items=[],
        walk_speed_tiles_per_tick=2,
    )
    adapter.varps.update({29: 0, 179: 21, 144: 100, 62: 6})
    adapter.position = {"x": 3110, "y": 3295, "plane": 0}

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
    reasons: list[str] = []
    for _ in range(1, 40):
        res = kernel.step_tick(bot, now_monotonic=0.0)
        if res.task_directive is not None:
            reasons.append(str(res.task_directive.reason))
        # The persisted snapshot must be consumed before any GE-only audit instruction appears.
        if any("restored from state.db" in r for r in reasons):
            break
    assert any("restored from state.db" in r for r in reasons), reasons[:10]
    assert bot._bank_source.startswith("PERSISTED"), bot._bank_source
    assert bot._bank_stock[POT_OF_FLOUR_ID] == 1
