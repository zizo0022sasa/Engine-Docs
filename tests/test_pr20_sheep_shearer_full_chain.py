"""`PR-20` Part 3.1 — Sheep Shearer, fully zero-touch (T109..T111).

Supply chain (nothing banked, nothing bought):
    `Shears (1735)` ground spawn at Fred's farm
        -> `Wool (1737)` x20 by shearing sheep (repeat step, progress-aware stall reset)
        -> `Ball of wool (1759)` x20 at the Lumbridge Castle **plane 1** spinning wheel (3209,3212)
        -> `Fred the Farmer` (3190,3273) hand-in, `Varp 179 -> 21`, `+1 QP`.

- T109: the whole supply chain end-to-end through the engine (shears -> 20 wool -> climb -> 20 balls).
- T110: kernel run to `Varp 179 = 21` and `COMPLETE_TASK`, with zero escalations.
- T111: Fred's dialogue options are matched verbatim (never by index), and the wool demand follows
        the live varp (`20` at the start, `21 - state` when a partial hand-in already happened).
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from osrs_ee.bots.base import DirectiveType, TickContext
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.supply_procurement import (
    BALL_OF_WOOL_ID,
    SHEARS_ID,
    WOOL_ID,
    ZeroTouchSupplyEngine,
)
from osrs_ee.core import ExecutionEngineKernel, ReactiveSafetyWatchdog, SessionLifecycleManager
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph, NavigationEngine
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.state import AccountStatus, StateStore

FARM = DEFAULT_WORLD_NODES["LUMBRIDGE_SHEEP_FARM"]
WHEEL = DEFAULT_WORLD_NODES["LUMBRIDGE_SPINNING_WHEEL"]


def _ctx(pos: WorldPoint, inventory: list[dict[str, Any]], tick: int) -> TickContext:
    return TickContext(
        game_tick=tick,
        position=pos,
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=tuple(inventory),
        total_level=35,
    )


# ---------------------------------------------------------------------------
# T109 — shears -> 20 wool -> climb -> 20 balls of wool
# ---------------------------------------------------------------------------
def test_t109_shears_to_twenty_balls_of_wool_is_fully_self_served() -> None:
    """T109 (`PR-20`): from an EMPTY bag the engine must take the shears, shear 20 sheep one at a
    time, climb to plane 1 and spin the full 20 — then report the item as satisfied."""
    engine = ZeroTouchSupplyEngine()
    inventory: list[dict[str, Any]] = []
    plane = 0
    shears_taken = 0
    shears_actions = 0
    spins = 0

    for tick in range(1, 400):
        pos = WorldPoint(WHEEL.x, WHEEL.y, 1) if plane == 1 else WorldPoint(FARM.x, FARM.y, plane)
        directive = engine.ensure(_ctx(pos, inventory, tick), BALL_OF_WOOL_ID, 20)
        if directive is None:
            break
        assert directive.directive_type != DirectiveType.ESCALATE_HUMAN, directive.reason

        if directive.directive_type == DirectiveType.TAKE_GROUND_ITEM:
            assert directive.payload["name"] == "Shears"
            shears_taken += 1
            inventory.append({"item_id": SHEARS_ID, "name": "Shears", "quantity": 1})
        elif directive.directive_type == DirectiveType.INTERACT_NPC:
            assert directive.payload["name"] == "Sheep", directive.payload
            shears_actions += 1
            if sum(int(i["quantity"]) for i in inventory if int(i["item_id"]) == WOOL_ID) < 20:
                inventory.append({"item_id": WOOL_ID, "name": "Wool", "quantity": 1})
        elif directive.directive_type == DirectiveType.INTERACT_OBJECT:
            name = str(directive.payload.get("name"))
            action = str(directive.payload.get("action"))
            if name in ("Staircase", "Stairs", "Ladder"):
                plane = 1 if action == "Climb-up" else 0
            elif name == "Spinning wheel":
                assert plane == 1, "the Lumbridge spinning wheel is on plane 1"
                for it in list(inventory):
                    if int(it["item_id"]) == WOOL_ID and int(it["quantity"]) > 0:
                        it["quantity"] -= 1
                        if it["quantity"] <= 0:
                            inventory.remove(it)
                        break
                inventory.append({"item_id": BALL_OF_WOOL_ID, "name": "Ball of wool", "quantity": 1})
                spins += 1
        elif directive.directive_type == DirectiveType.NAVIGATE_TO:
            pass

    assert shears_taken == 1, f"shears taken {shears_taken} times"
    assert shears_actions == 20, f"expected 20 shear actions, got {shears_actions}"
    assert spins == 20, f"expected 20 spins, got {spins}"
    assert sum(int(i["quantity"]) for i in inventory if int(i["item_id"]) == BALL_OF_WOOL_ID) == 20
    assert engine.ensure(_ctx(WorldPoint(FARM.x, FARM.y, 0), inventory, 999), BALL_OF_WOOL_ID, 20) is None


# ---------------------------------------------------------------------------
# T110 — kernel run to Varp 179 = 21
# ---------------------------------------------------------------------------
def _sheep_world(command_type: str, payload: dict[str, Any], adapter: FakeGameAdapter) -> None:
    name = str(payload.get("name", ""))
    action = str(payload.get("action", ""))
    if command_type == "INTERACT_NPC":
        if name == "Sheep":
            if any(int(i["item_id"]) == SHEARS_ID for i in adapter.inventory_items):
                adapter._add_to_inventory({"item_id": WOOL_ID, "name": "Wool", "quantity": 1, "noted": False})
        elif name == "Fred the Farmer":
            if int(adapter.varps.get(179, 0)) == 0:
                adapter.varps[179] = 1
            elif any(int(i["item_id"]) == BALL_OF_WOOL_ID for i in adapter.inventory_items):
                adapter.inventory_items.clear()
                adapter.varps[179] = 21
                adapter.quest_points += 1
    elif command_type == "INTERACT_OBJECT":
        if name in ("Staircase", "Stairs", "Ladder"):
            cur = dict(adapter.position)
            cur["plane"] = 1 if action == "Climb-up" else 0
            adapter.position = cur
        elif name == "Spinning wheel":
            for it in list(adapter.inventory_items):
                if int(it["item_id"]) == WOOL_ID and int(it["quantity"]) > 0:
                    it["quantity"] -= 1
                    if it["quantity"] <= 0:
                        adapter.inventory_items.remove(it)
                    adapter._add_to_inventory(
                        {"item_id": BALL_OF_WOOL_ID, "name": "Ball of wool", "quantity": 1, "noted": False}
                    )
                    break


def test_t110_kernel_run_completes_sheep_shearer_and_hands_in_twenty_balls(tmp_path: Path) -> None:
    """T110 (`PR-20`): a single kernel run from the GE with an empty bag must gather the shears, shear,
    spin and hand in — `Varp 179 = 21`, `+1 QP`, `COMPLETE_TASK`, zero escalations."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = StateStore(tmp_path / "pr20_sheep.db")
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
        now_utc=datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc),
        bypass_circadian=True,
    )
    adapter = FakeGameAdapter(
        account_id="acc_02",
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        skills={"hitpoints": 10},
        combat_level=3,
        quest_points=3,
        total_level=35,
        playtime_minutes=220,
        bank_items=[],
        inventory_items=[],
        ground_items=[{"item_id": SHEARS_ID, "name": "Shears", "quantity": 1}],
        walk_speed_tiles_per_tick=4,
        interaction_grants=_sheep_world,
    )
    adapter.varps.update({29: 2, 179: 0, 144: 100, 62: 6})
    adapter.run_energy = 100
    adapter.run_enabled = True
    adapter.position = {"x": 3164, "y": 3487, "plane": 0}

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
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3, quest_points=3),
        lifecycle=SessionLifecycleManager(),
        watchdog=ReactiveSafetyWatchdog(),
        navigation=NavigationEngine(ConstrainedWaypointGraph()),
    )
    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    try:
        completed = False
        for tick in range(1, 1500):
            result = kernel.step_tick(bot, now_monotonic=float(tick))
            directive = result.task_directive
            if directive is not None:
                if directive.directive_type == DirectiveType.ESCALATE_HUMAN:
                    raise AssertionError(f"halted at tick {tick}: {directive.reason}")
                if directive.directive_type == DirectiveType.COMPLETE_TASK:
                    completed = True
                    break
        assert adapter.varps[179] == 21, f"varps={adapter.varps} inv={adapter.inventory_items}"
        assert completed, f"no COMPLETE_TASK (inv={adapter.inventory_items}, pos={adapter.position})"
        assert adapter.quest_points == 4
    finally:
        store.close()


# ---------------------------------------------------------------------------
# T111 — Fred dialogue + live wool demand
# ---------------------------------------------------------------------------
def test_t111_fred_dialogue_uses_verbatim_options_and_tracks_the_live_wool_demand() -> None:
    """T111 (`PR-20`): Fred's three options are matched by text; the wool demand follows the varp."""
    bot = TradeUnlockQuestsTaskModule()

    def ctx(options: tuple[str, ...], varps: dict[int, int]) -> TickContext:
        return TickContext(
            game_tick=5,
            position=WorldPoint(3190, 3273, 0),
            capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
            inventory_items=(),
            dialogue_open=True,
            dialogue_options=options,
            varps=varps,
            quest_points=3,
            total_level=35,
        )

    varps = {29: 2, 179: 1, 144: 100, 62: 6}
    for option in ("I'm looking for a quest.", "Yes, okay. I can do that.", "I'm back!"):
        directive = bot.evaluate_tick(ctx(("Something else.", option), varps))
        assert directive.directive_type == DirectiveType.SELECT_DIALOGUE
        assert directive.payload["option"] == option

    # Fresh quest -> 20 balls required.
    assert bot._needed_wool(0) == 20
    assert bot._needed_wool(1) == 20
    # A partial hand-in already recorded in the varp only asks for the remainder.
    assert bot._needed_wool(15) == 6
    assert bot._needed_wool(20) == 1
    assert bot._needed_wool(21) == 0
