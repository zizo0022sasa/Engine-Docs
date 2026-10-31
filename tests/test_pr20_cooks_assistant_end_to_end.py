"""`PR-20` Part 2 — Cook's Assistant end-to-end delivery (T107..T108).

Live `acc_02` already holds `Egg (1944)` + `Bucket of milk (1927)`; after `PR-20` the mill yields
`Pot of flour (1933)`. The only thing left is the hand-in to the `Cook` in the Lumbridge Castle
kitchen `(3206,3214,0)`, which flips `Varp 29` to `>= 2` and awards the first quest point.

- T107: full kernel run — wheat -> mill -> flour -> Cook -> `Varp 29 = 2`, `+1 QP`, GE anchor,
        `COMPLETE_TASK`, with zero escalations and zero operator input.
- T108: dialogue safety — the hand-in options are selected by Wiki-verbatim TEXT (never by index),
        and an unrecognised option still escalates instead of guessing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from osrs_ee.bots.base import DirectiveType, TaskDirective, TickContext
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.supply_procurement import (
    EGG_ID,
    GRAIN_ID,
    BUCKET_OF_MILK_ID,
    POT_ID,
    POT_OF_FLOUR_ID,
)
from osrs_ee.core import ExecutionEngineKernel, ReactiveSafetyWatchdog, SessionLifecycleManager
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph, NavigationEngine
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.state import AccountStatus, StateStore

WHEAT = DEFAULT_WORLD_NODES["MILL_LANE_WHEAT_FIELD"]


def _cook_world(command_type: str, payload: dict[str, Any], adapter: FakeGameAdapter) -> None:
    """Minimal Lumbridge kitchen + windmill world, faithful to live object ids."""
    name = str(payload.get("name", ""))
    action = str(payload.get("action", ""))

    def take(item_id: int) -> None:
        for it in list(adapter.inventory_items):
            if int(it["item_id"]) == item_id:
                it["quantity"] -= 1
                if it["quantity"] <= 0:
                    adapter.inventory_items.remove(it)
                return

    if command_type == "INTERACT_OBJECT":
        if name == "Wheat":
            assert payload.get("id") == 15506
            adapter._add_to_inventory({"item_id": GRAIN_ID, "name": "Grain", "quantity": 1, "noted": False})
        elif name in ("Ladder", "Staircase", "Stairs"):
            cur = dict(adapter.position)
            # `PR-21.6`: one plane per rung — the mill needs two climbs each way.
            cur["plane"] = (min(2, int(cur["plane"]) + 1) if action == "Climb-up"
                            else max(0, int(cur["plane"]) - 1))
            adapter.position = cur
        elif name == "Hopper":
            # `PR-20`: the hopper EATS the grain — this is the field-incident postcondition.
            take(GRAIN_ID)
        elif name == "Hopper controls":
            pass  # operating the controls is what drops the flour into the bin
        elif name == "Flour bin":
            assert payload.get("id") == 1781
            take(POT_ID)
            adapter._add_to_inventory(
                {"item_id": POT_OF_FLOUR_ID, "name": "Pot of flour", "quantity": 1, "noted": False}
            )
    elif command_type == "INTERACT_NPC":
        if name == "Cook":
            assert adapter.position["plane"] == 0
            adapter.varps[29] = 2
            adapter.quest_points += 1


def _env(tmp_path: Path) -> tuple[StateStore, FakeGameAdapter, ExecutionEngineKernel, dict[str, Any]]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = StateStore(tmp_path / "pr20_cook.db")
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
        now_utc=datetime(2026, 10, 30, 9, 0, tzinfo=timezone.utc),
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
        # Exactly the live `acc_02` bag: egg + milk collected zero-touch, only the flour is missing.
        inventory_items=[
            {"item_id": EGG_ID, "name": "Egg", "quantity": 1, "noted": False},
            {"item_id": BUCKET_OF_MILK_ID, "name": "Bucket of milk", "quantity": 1, "noted": False},
            {"item_id": POT_ID, "name": "Pot", "quantity": 1, "noted": False},
        ],
        ground_items=[],
        walk_speed_tiles_per_tick=2,
        interaction_grants=_cook_world,
    )
    # Only Cook's Assistant is outstanding; the other three are already terminal.
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
    return store, adapter, kernel, lease


def _fetch_ctx(pos: WorldPoint, *, varps: dict[int, int], dialogue_options: tuple[str, ...] = ()) -> TickContext:
    return TickContext(
        game_tick=10,
        position=pos,
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=(
            {"item_id": EGG_ID, "name": "Egg", "quantity": 1},
            {"item_id": BUCKET_OF_MILK_ID, "name": "Bucket of milk", "quantity": 1},
            {"item_id": POT_OF_FLOUR_ID, "name": "Pot of flour", "quantity": 1},
        ),
        dialogue_open=bool(dialogue_options),
        dialogue_options=dialogue_options,
        varps=varps,
        quest_points=2,
        total_level=35,
    )


# ---------------------------------------------------------------------------
# T107 — the hand-in that awards the first quest point
# ---------------------------------------------------------------------------
def test_t107_cooks_assistant_end_to_end_awards_the_first_quest_point(tmp_path: Path) -> None:
    """T107 (`PR-20`): a single kernel run must take the live bag (egg + milk + empty pot) all the
    way through wheat -> mill -> flour -> Cook, set `Varp 29 = 2`, add `+1 QP`, anchor at the GE and
    end with `COMPLETE_TASK` — with no human in the loop."""
    store, adapter, kernel, _lease = _env(tmp_path)
    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    try:
        completed = False
        saw_cook = False
        for tick in range(1, 700):
            result = kernel.step_tick(bot, now_monotonic=float(tick))
            directive = result.task_directive
            if directive is not None:
                if directive.directive_type == DirectiveType.ESCALATE_HUMAN:
                    raise AssertionError(f"halted at tick {tick}: {directive.reason}")
                if directive.directive_type == DirectiveType.INTERACT_NPC and directive.payload.get("name") == "Cook":
                    saw_cook = True
                if directive.directive_type == DirectiveType.COMPLETE_TASK:
                    completed = True
                    break

        assert saw_cook, "the bot never talked to the Cook"
        assert completed, f"no COMPLETE_TASK (pos={adapter.position}, varps={adapter.varps})"
        assert adapter.varps[29] == 2, adapter.varps
        assert adapter.quest_points == 3, adapter.quest_points
        assert int(store.get_account("acc_02")["quest_points"]) >= 3
        # Anchored at the bank, bag emptied into the bank (no quest litter carried around).
        assert (adapter.position["x"], adapter.position["y"], adapter.position["plane"]) == (3164, 3487, 0)
        assert adapter.inventory_items == [], adapter.inventory_items
        assert not any(int(it["item_id"]) == POT_OF_FLOUR_ID for it in adapter.inventory_items)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# T108 — dialogue safety
# ---------------------------------------------------------------------------
def test_t108_cook_dialogue_is_selected_by_verbatim_text_and_never_by_index() -> None:
    """T108 (`PR-20`): every Cook's Assistant option is chosen by its exact Wiki text; an option
    outside the verified set escalates (`UNRECOGNIZED_QUEST_DIALOGUE_OPTION`) rather than guessing a
    numeric index — guessing is how a bot ends up asking the Cook for a different quest."""
    bot = TradeUnlockQuestsTaskModule()
    varps = {29: 0, 179: 21, 144: 100, 62: 6}
    for option in (
        "What's wrong?",
        "I'm always happy to help a cook in distress.",
        "Actually, I know where to find this stuff.",
        "I've got all the information I need. Thanks.",
    ):
        ctx = _fetch_ctx(WorldPoint(3206, 3214, 0), varps=varps, dialogue_options=("Some other line.", option))
        directive: TaskDirective = bot.evaluate_tick(ctx)
        assert directive.directive_type == DirectiveType.SELECT_DIALOGUE, directive
        assert directive.payload["option"] == option

    ctx = _fetch_ctx(
        WorldPoint(3206, 3214, 0),
        varps=varps,
        dialogue_options=("An entirely unexpected option.",),
    )
    directive = bot.evaluate_tick(ctx)
    assert directive.directive_type == DirectiveType.ESCALATE_HUMAN
    assert directive.reason.startswith("UNRECOGNIZED_QUEST_DIALOGUE_OPTION")

    # A plain "continue" (no options on screen) advances the dialogue instead of stalling.
    ctx = _fetch_ctx(WorldPoint(3206, 3214, 0), varps=varps)
    directive = bot.evaluate_tick(ctx)
    assert directive.directive_type != DirectiveType.ESCALATE_HUMAN
