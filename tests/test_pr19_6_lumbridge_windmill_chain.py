"""`PR-19.6` — Lumbridge windmill chain & coordinate sanity (T98..T102).

Field evidence (live `acc_02`, tick 221): `SUPPLY_STEP_STALLED:Grain:OBJECT:Wheat`.
The recorded wheat-field tile `(3307, 3163)` is inside the **Al Kharid Palace throne room** — the bot
was standing next to `Throne (1108)` and `Vase (583)` trying to pick wheat. 😅

Live ground truth for the Lumbridge windmill (this replaces the wiki-derived guess):
    Wheat      id 15506  "Pick"  at (3162, 3296)
    Large door id 1521/1524       at (3166, 3302)
    Flour bin  id 1781   "Empty" at (3166, 3306)   ground floor
    Ladder     id 12964           at (3164, 3307)  -> hopper floor (plane 2)

- T98:  the corrected coordinates + the real 25-tile distance between the cow field and the wheat field.
- T99:  the full 6-step mill chain, in order, with verified object ids, and the stair latch (no ping-pong).
- T100: `Pot (1931)` is procured BEFORE the bin is emptied.
- T101: **regional sanity guard** — every supply node must sit inside its documented area rectangle, so
        a future "throne room" style typo fails the suite instead of the field run.
- T102: end-to-end kernel run: wheat -> mill -> flour, ending with `Pot of flour (1933)` in the bag.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from osrs_ee.bots.base import DirectiveType, TickContext
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.supply_procurement import (
    FLOUR_BIN_OBJECT_ID,
    GRAIN_ID,
    MILL_DOOR_OBJECT_ID,
    MILL_LADDER_OBJECT_ID,
    POT_ID,
    POT_OF_FLOUR_ID,
    SUPPLY_RECIPES,
    WHEAT_OBJECT_ID,
    ZeroTouchSupplyEngine,
)
from osrs_ee.core import ExecutionEngineKernel, ReactiveSafetyWatchdog, SessionLifecycleManager
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph, NavigationEngine
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.state import AccountStatus, StateStore

WHEAT = DEFAULT_WORLD_NODES["MILL_LANE_WHEAT_FIELD"]
MILL = DEFAULT_WORLD_NODES["MILL_LANE_MILL"]
TOP = DEFAULT_WORLD_NODES["LUMBRIDGE_WINDMILL_TOP"]
COW = DEFAULT_WORLD_NODES["LUMBRIDGE_COW_FIELD"]


def _ctx(pos: WorldPoint, *, inventory: tuple[dict[str, Any], ...] = (), tick: int = 1) -> TickContext:
    return TickContext(
        game_tick=tick,
        position=pos,
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=inventory,
        total_level=35,
    )


# ---------------------------------------------------------------------------
# T98 — the coordinates the field actually verified
# ---------------------------------------------------------------------------
def test_t98_wheat_field_and_mill_use_the_live_verified_tiles() -> None:
    """T98 (`PR-19.6`): `(3307,3163)` was the Al Kharid throne room. The wheat field must be
    `(3162,3296)`, the mill door `(3166,3302)`, and the hopper floor node must exist on plane 2."""
    assert (WHEAT.x, WHEAT.y, WHEAT.plane) == (3162, 3296, 0)
    assert (MILL.x, MILL.y, MILL.plane) == (3166, 3302, 0)
    assert (TOP.x, TOP.y, TOP.plane) == (3164, 3307, 2)
    # The old, wrong tile must never come back.
    for node in (WHEAT, MILL, TOP):
        assert (node.x, node.y) != (3307, 3163)

    graph = ConstrainedWaypointGraph()
    plan = graph.plan_safe_route(
        origin="LUMBRIDGE_COW_FIELD",
        destination="MILL_LANE_MILL",
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
    )
    # Lumbridge sites are ~20 tiles apart — the old 205-tile trans-desert cost was nonsense.
    assert plan.total_cost_tiles < 60.0, plan.total_cost_tiles


# ---------------------------------------------------------------------------
# T99 — the six-step chain, with the stair latch
# ---------------------------------------------------------------------------
def test_t99_mill_chain_runs_in_order_once_per_step_without_stair_ping_pong() -> None:
    """T99 (`PR-19.6`): door -> ladder up -> Hopper Fill -> Hopper controls Operate -> ladder down ->
    Flour bin Empty. Every interaction carries the live-verified object id where one exists, and a
    completed climb is latched so the down-climb can never re-trigger the up-climb."""
    engine = ZeroTouchSupplyEngine()
    inventory = [
        {"item_id": GRAIN_ID, "name": "Grain", "quantity": 1},
        {"item_id": POT_ID, "name": "Pot", "quantity": 1},
    ]
    plane = 0
    sequence: list[tuple[str, str]] = []
    for tick in range(1, 30):
        pos = WorldPoint(TOP.x, TOP.y, plane) if plane == 2 else WorldPoint(MILL.x, MILL.y, plane)
        d = engine.ensure(_ctx(pos, inventory=tuple(inventory), tick=tick), POT_OF_FLOUR_ID, 1)
        assert d is not None
        if d.directive_type != DirectiveType.INTERACT_OBJECT:
            continue
        name, action = str(d.payload.get("name")), str(d.payload.get("action"))
        sequence.append((name, action))
        if name in ("Ladder", "Staircase", "Stairs"):
            plane = 2 if action == "Climb-up" else 0
        if name == "Hopper":
            # `PR-20`: the real hopper DESTROYS the grain. The engine may only advance to `Operate`
            # once it has observed that destruction on a later tick.
            inventory = [it for it in inventory if int(it["item_id"]) != GRAIN_ID]
        if len(sequence) >= 7:
            break

    assert sequence[0] == ("Large door", "Open")
    assert sequence[1] == ("Ladder", "Climb-up")
    assert sequence[2] == ("Hopper", "Fill")
    assert sequence[3] == ("Hopper controls", "Operate")
    assert sequence[4] == ("Ladder", "Climb-down")
    assert sequence[5][0] == "Flour bin"
    assert sum(1 for n, a in sequence if n == "Ladder" and a == "Climb-up") == 1, sequence

    # Verified object ids travel with the payload.
    assert SUPPLY_RECIPES[POT_OF_FLOUR_ID].steps[0].object_id == MILL_DOOR_OBJECT_ID == 1521
    assert SUPPLY_RECIPES[POT_OF_FLOUR_ID].steps[1].object_id == MILL_LADDER_OBJECT_ID == 12964
    assert SUPPLY_RECIPES[POT_OF_FLOUR_ID].steps[-1].object_id == FLOUR_BIN_OBJECT_ID == 1781
    assert SUPPLY_RECIPES[GRAIN_ID].steps[0].object_id == WHEAT_OBJECT_ID == 15506


# ---------------------------------------------------------------------------
# T100 — the pot is obtained first
# ---------------------------------------------------------------------------
def test_t100_pot_is_procured_before_the_flour_bin_is_emptied(tmp_path: Path) -> None:
    """T100 (`PR-19.6`): emptying the bin needs an empty `Pot (1931)`; with none in the bag (and none in
    the bank) the very next directive must be the pot, never the bin."""
    bot = TradeUnlockQuestsTaskModule()
    d = bot._procure(
        _ctx(MILL, inventory=({"item_id": GRAIN_ID, "name": "Grain", "quantity": 1},)),
        POT_OF_FLOUR_ID,
        1,
        "Pot of flour",
    )
    assert d is not None
    # Either the audit acknowledgement or a walk/gather for the pot — never the bin interaction.
    assert not (
        d.directive_type == DirectiveType.INTERACT_OBJECT and d.payload.get("name") == "Flour bin"
    ), d


# ---------------------------------------------------------------------------
# T101 — regional sanity guard (the "throne room" class)
# ---------------------------------------------------------------------------
def test_t101_every_supply_node_sits_inside_its_documented_region() -> None:
    """T101 (`PR-19.6`): a single transposed digit silently sent the bot into the Al Kharid palace.
    Each supply node is now bounded by the documented map rectangle, so this class of error fails the
    test suite instead of a live field run.
    """
    # (node id, x_min, x_max, y_min, y_max) — generous but decisive rectangles.
    regions: tuple[tuple[str, int, int, int, int], ...] = (
        ("MILL_LANE_WHEAT_FIELD", 3150, 3175, 3285, 3305),   # Lumbridge windmill area, west bank
        ("MILL_LANE_MILL", 3155, 3180, 3290, 3315),
        ("LUMBRIDGE_COW_FIELD", 3160, 3185, 3305, 3330),
        ("LUMBRIDGE_CHICKEN_COOP", 3160, 3195, 3290, 3315),
        ("FRED_ONION_GARDEN", 3180, 3200, 3260, 3275),
        ("LUMBRIDGE_SHEEP_FARM", 3180, 3200, 3265, 3285),
        ("LUMBRIDGE_KITCHEN", 3195, 3215, 3205, 3225),
        # `PR-20.4`: the live house is at (3086,3258); the old box (y<=3245) described the fishing
        # pier and therefore ACCEPTED a node 26 tiles away from Aggie.
        ("DRAYNOR_AGGIE", 3070, 3105, 3245, 3275),
        ("FALADOR_PARK", 2985, 3015, 3360, 3390),
        ("VARROCK_CADAVA_BUSHES", 3255, 3285, 3355, 3385),
        # `PR-20.3`: live probe puts the two bushes at (3272,3375) & (3277,3371); the old rectangle
        # described the WRONG (dark-wizard-adjacent) area and happily accepted the bug.
        ("VARROCK_REDBERRY_BUSHES", 3260, 3290, 3360, 3385),
        ("GOBLIN_VILLAGE", 2945, 2975, 3495, 3525),
    )
    for node_id, x_min, x_max, y_min, y_max in regions:
        node = DEFAULT_WORLD_NODES[node_id]
        assert x_min <= node.x <= x_max, f"{node_id} x={node.x} outside [{x_min},{x_max}]"
        assert y_min <= node.y <= y_max, f"{node_id} y={node.y} outside [{y_min},{y_max}]"

    # Explicitly: nothing may sit in the Al Kharid region (x>=3250, y<=3200) except Al Kharid nodes.
    al_kharid_prefixes = ("AL_KHARID",)
    for node_id, node in DEFAULT_WORLD_NODES.items():
        if node_id.startswith(al_kharid_prefixes):
            continue
        assert not (node.x >= 3250 and node.y <= 3200), f"{node_id} looks like it fell into Al Kharid"


# ---------------------------------------------------------------------------
# T102 — end-to-end: wheat -> mill -> flour
# ---------------------------------------------------------------------------
def test_t102_kernel_run_produces_pot_of_flour_end_to_end(tmp_path: Path) -> None:
    """T102 (`PR-19.6`): from the wheat field with a pot in the bag, the kernel must walk to the mill,
    open the door, climb, fill and operate the hopper, climb down and empty the bin — yielding
    `Pot of flour (1933)` with zero escalation."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = StateStore(tmp_path / "pr19_6.db")
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
        now_utc=datetime(2026, 10, 29, 18, 0, tzinfo=timezone.utc),
        bypass_circadian=True,
    )

    def _grants(command_type: str, payload: dict[str, Any], adapter: FakeGameAdapter) -> None:
        name = str(payload.get("name", ""))
        action = str(payload.get("action", ""))
        if command_type != "INTERACT_OBJECT":
            return
        if name == "Wheat":
            assert payload.get("id") == 15506
            adapter._add_to_inventory(
                {"item_id": GRAIN_ID, "name": "Grain", "quantity": 1, "noted": False}
            )
        elif name in ("Ladder", "Staircase", "Stairs"):
            cur = dict(adapter.position)
            cur["plane"] = 2 if action == "Climb-up" else 0
            adapter.position = cur
        elif name == "Hopper":
            # `PR-20`: filling the hopper consumes the grain (field tick 289-349 regression).
            for it in list(adapter.inventory_items):
                if int(it["item_id"]) == GRAIN_ID:
                    it["quantity"] -= 1
                    if it["quantity"] <= 0:
                        adapter.inventory_items.remove(it)
                    break
        elif name == "Flour bin":
            assert payload.get("id") == 1781
            for it in list(adapter.inventory_items):
                if int(it["item_id"]) == POT_ID:
                    it["quantity"] -= 1
                    if it["quantity"] <= 0:
                        adapter.inventory_items.remove(it)
                    break
            adapter._add_to_inventory(
                {"item_id": POT_OF_FLOUR_ID, "name": "Pot of flour", "quantity": 1, "noted": False}
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
        # Live `acc_02` state: egg + milk already collected, only the flour is missing.
        inventory_items=[
            {"item_id": 1944, "name": "Egg", "quantity": 1, "noted": False},
            {"item_id": 1927, "name": "Bucket of milk", "quantity": 1, "noted": False},
            {"item_id": POT_ID, "name": "Pot", "quantity": 1, "noted": False},
        ],
        ground_items=[],
        walk_speed_tiles_per_tick=2,
        interaction_grants=_grants,
    )
    adapter.varps.update({29: 0, 179: 21, 144: 100, 62: 6})
    adapter.run_energy = 100
    adapter.run_enabled = True
    adapter.position = {"x": WHEAT.x, "y": WHEAT.y, "plane": 0}

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
    got_flour = False
    for tick in range(1, 500):
        res = kernel.step_tick(bot, now_monotonic=0.0)
        directive = res.task_directive
        if directive is not None and directive.directive_type == DirectiveType.ESCALATE_HUMAN:
            raise AssertionError(f"halted at tick {tick}: {directive.reason}")
        if any(int(it["item_id"]) == POT_OF_FLOUR_ID for it in adapter.inventory_items):
            got_flour = True
            break

    assert got_flour, f"no flour produced (pos={adapter.position}, inv={adapter.inventory_items})"
