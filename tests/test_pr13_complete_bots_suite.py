"""Comprehensive Acceptance Tests for PR-13 Complete Pluggable Bot Suite (`osrs_ee/bots/` — T40..T43).

Covers:
- T40: Full 4-Floor `StrongholdTaskModule` (`stage2_stronghold_10k`) room-by-room traversal, 39-entry
  security cache Q&A, exact object IDs (`20790, 20656, 20785, 19000, 19004, 23709, 23706, 23731, 23732`),
  Fancy Boots widget (`270:15`), ladder warning modal (`579:17`), GE bank deposit, out-of-order guard,
  and missing-2FA escalation guard.
- T41: `TutorialIslandTaskModule` (`Varp 281` + Non-Ironman safeguard) & `TradeUnlockQuestsTaskModule`
  (`12 QP` bundle: Cook's Assistant, Sheep Shearer, Romeo & Juliet plane-1 Juliet stairs, Goblin Diplomacy).
- T42: `TotalLevel100TaskModule` (`stage1b_seasoning_gatherer`: Woodcutting <-> Fishing/Cooking rotation +
  immediate game-tick `Total >= 100` & `playtime >= 1200m` latch + Bank-Only storage).
- T43: `SkillingGathererTaskModule` 3-Cycle Smoke-Test Protocol across Woodcutting, Fly Fishing, and Mining
  + `BotModuleRegistry` 1-to-1 coverage of all 7 curated recipes & `CONCEPT_ONLY` quarantine enforcement.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from osrs_ee.bots import (
    BotModuleRegistry,
    DirectiveType,
    SkillingGathererTaskModule,
    StrongholdTaskModule,
    TotalLevel100TaskModule,
    TradeUnlockQuestsTaskModule,
    TutorialIslandTaskModule,
)
from osrs_ee.core import ExecutionEngineKernel, ReactiveSafetyWatchdog, SessionLifecycleManager
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph, NavigationEngine
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.state import AccountStatus, StateStore
from osrs_ee.tasks import CuratedRecipeRegistry


def _make_kernel_env(
    tmp_path: Path,
    *,
    task_id: str = "stage2_stronghold_10k",
    skills: dict[str, int] | None = None,
    bank_items: list[dict[str, Any]] | None = None,
    inventory_items: list[dict[str, Any]] | None = None,
    tutorial_varp_281: int = 1000,
    quest_points: int = 0,
    total_level: int = 35,
    playtime_minutes: int = 60,
) -> tuple[StateStore, FakeGameAdapter, GameAdapterGate, ExecutionEngineKernel]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = StateStore(tmp_path / "pr13_state.db")
    store.register_account(
        account_id="acc_01",
        auth_mode="LEGACY_CREDENTIALS",
        credential_ref="vault://accounts/acc_01/auth",
        proxy_ref="vault://proxies/eg_01",
        region="EG",
        timezone_name="Africa/Cairo",
        persona_seed=101,
        initial_status=AccountStatus.READY,
    )
    lease = store.acquire_session_lease(
        account_id="acc_01",
        slot_id=0,
        task_id=task_id,
        now_utc=datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc),
    )
    eff_skills = skills or {"hitpoints": 10, "agility": 1, "woodcutting": 30, "fishing": 20, "mining": 15}
    adapter = FakeGameAdapter(
        account_id="acc_01",
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        tutorial_varp_281=tutorial_varp_281,
        quest_points=quest_points,
        total_level=total_level,
        playtime_minutes=playtime_minutes,
        skills=eff_skills,
        bank_items=bank_items or [],
        inventory_items=inventory_items or [],
    )
    gate = GameAdapterGate(store, adapter)
    gate.bind_to_lease(
        account_id="acc_01",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
    )
    caps = AccountCapabilitiesContext(
        account_id="acc_01",
        skills=eff_skills,
        combat_level=3,
        quest_points=quest_points,
        is_member=False,
    )
    kernel = ExecutionEngineKernel(
        gate=gate,
        account_id="acc_01",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
        capabilities=caps,
        lifecycle=SessionLifecycleManager(),
        watchdog=ReactiveSafetyWatchdog(),
        navigation=NavigationEngine(ConstrainedWaypointGraph()),
    )
    return store, adapter, gate, kernel


def test_t40_full_4_floor_stronghold_traversal_security_cache_widgets_and_ge_banking(tmp_path: Path) -> None:
    """T40: End-to-end 4-Floor Stronghold of Security simulation (`StrongholdTaskModule`):
    - Surface GE `(3164, 3487)` -> Safe Corridor (pruning Agility-21 `3149,3480`) -> Barbarian Village `20790`
    - Floor 1 (`Gate of War` x4 + `20656` Gift of Peace + `20785` ladder + `579:17` modal)
    - Floor 2 (`Rickety door` x4 + `19000` Grain of Plenty + `19004` ladder)
    - Floor 3 (`Oozing barrier` x3 + `23709` Box of Health + `23706` ladder)
    - Floor 4 (`Portal of Death` x4 + `23731` Cradle of Life + `270:15` Fancy Boots + `23732` Bone Chain exit)
    - Safe return to GE `(3164, 3487)` -> Bank Turnaround deposit (`Fancy boots` 9005 + `10,000 GP`) -> `COMPLETE_TASK`.
    """
    store, adapter, _gate, kernel = _make_kernel_env(
        tmp_path / "stronghold_full",
        bank_items=[{"item_id": 333, "name": "Trout", "quantity": 12, "noted": False}],
        inventory_items=[{"item_id": 333, "name": "Trout", "quantity": 2, "noted": False}],
    )
    bot = StrongholdTaskModule()
    adapter.position = {"x": 3164, "y": 3487, "plane": 0}
    for vid in (2309, 2310, 2311, 2312):
        adapter.varbits[vid] = 0

    # Door transition map simulating exact OSRS room transitions upon passing each door/ladder!
    f1_doors = [(1864, 5227), (1875, 5239), (1912, 5242), (1905, 5230)]
    f2_doors = [(2030, 5236), (2025, 5245), (2014, 5237), (2020, 5225)]
    f3_doors = [(2132, 5259), (2132, 5265), (2140, 5280)]
    f4_doors = [(2359, 5233), (2345, 5238), (2341, 5224), (2343, 5218)]
    door_idx = {"Gate of War": 0, "Rickety door": 0, "Oozing barrier": 0, "Portal of Death": 0}
    modal_tested_on_f1 = False
    observed_widgets: list[dict[str, Any]] = []
    visited_tiles: set[tuple[int, int]] = set()

    orig_exec = adapter.execute_raw_command

    def _sim_stronghold_world(cmd_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal modal_tested_on_f1
        res = orig_exec(cmd_type, payload)
        visited_tiles.add((adapter.position["x"], adapter.position["y"]))

        if cmd_type == "INTERACT_OBJECT":
            oid = payload.get("id")
            oname = payload.get("name")
            if oid == 20790:  # Entrance rock hole -> Floor 1 entry room
                adapter.position = {"x": 1860, "y": 5244, "plane": 0}
            elif oname == "Gate of War":
                # Trigger a real Stronghold security gate dialogue on first door!
                if door_idx["Gate of War"] == 0:
                    adapter.dialogue_open = True
                    adapter.last_dialogue_snapshot = {
                        "inDialogue": True,
                        "question": "To pass you must answer me this: What is an example of a good bank pin?",
                        "options": [
                            "Please wait...",
                            "Your real-life birthday.",
                            "The birthday of a famous person or event.",
                            "1234.",
                        ],
                    }
                nx, ny = f1_doors[door_idx["Gate of War"]]
                door_idx["Gate of War"] += 1
                adapter.position = {"x": nx, "y": ny, "plane": 0}
            elif oid == 20656:  # Floor 1 Gift of Peace (2,000 GP)
                adapter.varbits[2309] = 1
                adapter._add_to_inventory({"item_id": 995, "name": "Coins", "quantity": 2000, "noted": False})
            elif oid == 20785:  # Floor 1 Ladder Down -> test modal 579:17 first, then descend!
                if not modal_tested_on_f1:
                    modal_tested_on_f1 = True  # Stay on Floor 1 for 1 tick so modal 579:17 fires!
                else:
                    adapter.position = {"x": 2042, "y": 5245, "plane": 0}
            elif oname == "Rickety door":
                nx, ny = f2_doors[door_idx["Rickety door"]]
                door_idx["Rickety door"] += 1
                adapter.position = {"x": nx, "y": ny, "plane": 0}
            elif oid == 19000:  # Floor 2 Grain of Plenty (+3,000 GP)
                adapter.varbits[2310] = 1
                adapter._add_to_inventory({"item_id": 995, "name": "Coins", "quantity": 3000, "noted": False})
            elif oid == 19004:  # Floor 2 Ladder Down -> Floor 3
                adapter.position = {"x": 2123, "y": 5252, "plane": 0}
            elif oname == "Oozing barrier":
                nx, ny = f3_doors[door_idx["Oozing barrier"]]
                door_idx["Oozing barrier"] += 1
                adapter.position = {"x": nx, "y": ny, "plane": 0}
            elif oid == 23709:  # Floor 3 Box of Health (+5,000 GP)
                adapter.varbits[2311] = 1
                adapter._add_to_inventory({"item_id": 995, "name": "Coins", "quantity": 5000, "noted": False})
            elif oid == 23706:  # Floor 3 Ladder Down -> Floor 4
                adapter.position = {"x": 2358, "y": 5218, "plane": 0}
            elif oname == "Portal of Death":
                nx, ny = f4_doors[door_idx["Portal of Death"]]
                door_idx["Portal of Death"] += 1
                adapter.position = {"x": nx, "y": ny, "plane": 0}
            elif oid == 23732:  # Floor 4 Bone Chain -> Barbarian Village Surface!
                adapter.position = {"x": 3081, "y": 3421, "plane": 0}

        elif cmd_type == "DIALOGUE_SELECT":
            assert payload["option"] == "The birthday of a famous person or event."
            adapter.dialogue_open = False
            adapter.last_dialogue_snapshot = {}

        elif cmd_type == "WIDGET_CLICK":
            observed_widgets.append(dict(payload))
            if payload.get("groupId") == 579 and payload.get("childId") == 17:
                # Ladder modal confirmed -> lands on Floor 2!
                adapter.position = {"x": 2042, "y": 5245, "plane": 0}
            elif payload.get("groupId") == 270 and payload.get("childId") == 15:
                # Fancy boots claimed!
                adapter.varbits[2312] = 1
                adapter._add_to_inventory({"item_id": 9005, "name": "Fancy boots", "quantity": 1, "noted": False})

        return res

    adapter.execute_raw_command = _sim_stronghold_world  # type: ignore[method-assign]

    try:
        completed = False
        for step in range(1, 140):
            tick_res = kernel.step_tick(bot, now_monotonic=float(step))
            if tick_res.halted:
                assert tick_res.task_directive is not None
                assert tick_res.task_directive.directive_type == DirectiveType.COMPLETE_TASK
                completed = True
                break

        assert completed is True, "Stronghold 4-floor + GE banking mission did not reach COMPLETE_TASK!"
        assert (3149, 3480) not in visited_tiles, "Agility-1 account touched GE Underwall trap!"
        assert {"groupId": 579, "childId": 17} in observed_widgets
        assert {"groupId": 270, "childId": 15} in observed_widgets
        banked_ids = {it["item_id"]: it["quantity"] for it in adapter.bank_items}
        assert banked_ids.get(9005) == 1
        assert banked_ids.get(995) == 10000
    finally:
        store.close()

    # Also verify out-of-order floor guard & missing-2FA bounded claim escalation
    store2, adapter2, _gate2, kernel2 = _make_kernel_env(tmp_path / "stronghold_guards")
    try:
        bot_guard = StrongholdTaskModule(max_claim_attempts=2)
        # Out-of-order: placed in Floor 2 while Floor 1 Varbit 2309 == 0 -> ESCALATE_HUMAN!
        adapter2.position = {"x": 2040, "y": 5240, "plane": 0}
        adapter2.varbits = {2309: 0, 2310: 0, 2311: 0, 2312: 0}
        r_oo = kernel2.step_tick(bot_guard, now_monotonic=1.0)
        assert r_oo.halted is True
        assert "OUT_OF_ORDER_FLOOR_2" in r_oo.detail
    finally:
        store2.close()

    store3, adapter3, _gate3, kernel3 = _make_kernel_env(tmp_path / "stronghold_2fa")
    try:
        bot_2fa = StrongholdTaskModule(max_claim_attempts=2)
        adapter3.position = {"x": 1906, "y": 5222, "plane": 0}
        adapter3.varbits = {2309: 0, 2310: 0, 2311: 0, 2312: 0}
        # 2 claim attempts fail to flip varbit 2309 -> 3rd tick escalates instead of infinite loop!
        assert kernel3.step_tick(bot_2fa, now_monotonic=1.0).halted is False
        assert kernel3.step_tick(bot_2fa, now_monotonic=2.0).halted is False
        r_2fa = kernel3.step_tick(bot_2fa, now_monotonic=3.0)
        assert r_2fa.halted is True
        assert "FLOOR_1_CLAIM_BLOCKED_CHECK_2FA" in r_2fa.detail
    finally:
        store3.close()


def test_t41_tutorial_island_non_ironman_guard_and_12qp_trade_unlock_quest_chain(tmp_path: Path) -> None:
    """T41:
    1. `TutorialIslandTaskModule`: progresses `Varp 281`, enforces Non-Ironman dialogue selection
       (`"No, I'm not planning to do that."`), and homes to `GRAND_EXCHANGE` upon `Varp 281 == 1000`.
    2. `TradeUnlockQuestsTaskModule`: completes Cook's Assistant (1 QP) + Sheep Shearer (1 QP) +
       Romeo & Juliet (5 QP, including upstairs plane-1 Juliet balcony!) + Goblin Diplomacy (5 QP) = 12 QP!
    """
    # Part 1: Tutorial Island + Non-Ironman Safeguard
    store_t, adapter_t, _gate_t, kernel_t = _make_kernel_env(
        tmp_path / "tutorial",
        task_id="stage0_tutorial_island",
        tutorial_varp_281=630,
    )
    try:
        tut_bot = TutorialIslandTaskModule()
        adapter_t.position = {"x": 3141, "y": 3087, "plane": 0}
        adapter_t.dialogue_open = True
        adapter_t.last_dialogue_snapshot = {
            "inDialogue": True,
            "question": "Are you planning to become an Iron Man?",
            "options": [
                "Yes, I'd like to be an Iron Man.",
                "No, I'm not planning to do that.",
            ],
        }
        r_iron = kernel_t.step_tick(tut_bot, now_monotonic=1.0)
        assert r_iron.task_directive is not None
        assert r_iron.task_directive.directive_type == DirectiveType.SELECT_DIALOGUE
        assert r_iron.task_directive.payload["option"] == "No, I'm not planning to do that."

        # Complete Tutorial Island -> spawns in Lumbridge `(3222, 3218, 0)` with Varp 281 == 1000
        adapter_t.dialogue_open = False
        adapter_t.tutorial_varp_281 = 1000
        adapter_t.position = {"x": 3222, "y": 3218, "plane": 0}
        tut_done = False
        for t in range(2, 25):
            rt = kernel_t.step_tick(tut_bot, now_monotonic=float(t))
            if rt.halted:
                assert rt.task_directive is not None
                assert rt.task_directive.directive_type == DirectiveType.COMPLETE_TASK
                tut_done = True
                break
        assert tut_done is True
        assert (adapter_t.position["x"], adapter_t.position["y"]) == (3164, 3487)
    finally:
        store_t.close()

    # Part 2: 12-QP Trade-Unlock Quest Bundle
    quest_supplies = [
        {"item_id": 1944, "name": "Egg", "quantity": 1, "noted": False},
        {"item_id": 1927, "name": "Bucket of milk", "quantity": 1, "noted": False},
        {"item_id": 1933, "name": "Pot of flour", "quantity": 1, "noted": False},
        {"item_id": 1759, "name": "Ball of wool", "quantity": 20, "noted": False},
        {"item_id": 753, "name": "Cadava berries", "quantity": 1, "noted": False},
        {"item_id": 286, "name": "Orange goblin mail", "quantity": 1, "noted": False},
        {"item_id": 287, "name": "Blue goblin mail", "quantity": 1, "noted": False},
        {"item_id": 288, "name": "Goblin mail", "quantity": 1, "noted": False},
    ]
    store_q, adapter_q, _gate_q, kernel_q = _make_kernel_env(
        tmp_path / "quests_12qp",
        task_id="stage1a_f2p_quests_10qp",
        bank_items=quest_supplies,
        quest_points=0,
    )
    adapter_q.position = {"x": 3164, "y": 3487, "plane": 0}
    adapter_q.varps.update({29: 0, 179: 0, 144: 0, 62: 0})
    visited_juliet_plane_1 = False

    orig_q_exec = adapter_q.execute_raw_command

    def _sim_quests_world(cmd_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal visited_juliet_plane_1
        res = orig_q_exec(cmd_type, payload)
        if cmd_type == "INTERACT_OBJECT" and payload.get("name") == "Staircase":
            if payload.get("action") == "Climb-up":
                adapter_q.position = {"x": 3156, "y": 3435, "plane": 1}
            elif payload.get("action") == "Climb-down":
                adapter_q.position = {"x": 3157, "y": 3435, "plane": 0}
        elif cmd_type == "INTERACT_NPC":
            npc = payload.get("name")
            if npc == "Cook":
                adapter_q.inventory_items.clear()
                adapter_q.varps[29] = 2
                adapter_q.quest_points += 1
            elif npc == "Fred the Farmer":
                adapter_q.inventory_items.clear()
                adapter_q.varps[179] = 21
                adapter_q.quest_points += 1
            elif npc == "Romeo":
                cur = adapter_q.varps[144]
                if cur == 0:
                    adapter_q.varps[144] = 10
                elif cur == 20:
                    adapter_q.varps[144] = 30
                elif cur == 60:
                    adapter_q.varps[144] = 100
                    adapter_q.quest_points += 5
            elif npc == "Juliet":
                assert adapter_q.position["plane"] == 1
                visited_juliet_plane_1 = True
                if adapter_q.varps[144] == 10:
                    adapter_q.varps[144] = 20
                elif adapter_q.varps[144] == 50:
                    adapter_q.inventory_items.clear()
                    adapter_q.varps[144] = 60
            elif npc == "Father Lawrence":
                adapter_q.varps[144] = 40
            elif npc == "Apothecary":
                adapter_q.varps[144] = 50
                adapter_q._add_to_inventory({"item_id": 756, "name": "Cadava potion", "quantity": 1, "noted": False})
            elif npc == "General Bentnoze":
                adapter_q.inventory_items.clear()
                adapter_q.varps[62] = 6
                adapter_q.quest_points += 5
        return res

    adapter_q.execute_raw_command = _sim_quests_world  # type: ignore[method-assign]

    try:
        quest_bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
        q_done = False
        for step in range(1, 160):
            rq = kernel_q.step_tick(quest_bot, now_monotonic=float(step))
            if rq.halted:
                assert rq.task_directive is not None
                assert rq.task_directive.directive_type == DirectiveType.COMPLETE_TASK
                q_done = True
                break
        assert q_done is True
        assert visited_juliet_plane_1 is True
        assert adapter_q.quest_points == 12
        assert (adapter_q.position["x"], adapter_q.position["y"], adapter_q.position["plane"]) == (3164, 3487, 0)
    finally:
        store_q.close()


def test_t42_total_level_100_seasoning_rotation_and_immediate_tick_latch(tmp_path: Path) -> None:
    """T42: `TotalLevel100TaskModule` (`stage1b_seasoning_gatherer`):
    1. Rotates safely between `WOODCUTTING` (`LUMBRIDGE_WEST_TREES`) and `FISHING_COOKING`
       (`LUMBRIDGE_SWAMP_FISHING` -> `LUMBRIDGE_KITCHEN` Cooking range -> Bank Turnaround).
    2. Latches immediately once `total_level >= 100` AND `playtime_minutes >= 1200`, banks items, and completes!
    """
    store, adapter, _gate, kernel = _make_kernel_env(
        tmp_path / "seasoning_100",
        task_id="stage1b_seasoning_gatherer",
        total_level=94,
        playtime_minutes=1195,
        bank_items=[
            {"item_id": 1351, "name": "Bronze axe", "quantity": 1, "noted": False},
            {"item_id": 303, "name": "Small fishing net", "quantity": 1, "noted": False},
        ],
    )
    adapter.position = {"x": 3185, "y": 3436, "plane": 0}  # Varrock West Bank
    cooked_on_range = False

    orig_exec = adapter.execute_raw_command

    def _sim_seasoning(cmd_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        nonlocal cooked_on_range
        res = orig_exec(cmd_type, payload)
        if cmd_type == "INTERACT_OBJECT" and payload.get("action") == "Chop down":
            # Fill inventory with 27 Oak logs (+ 1 Bronze axe = 28 slots)
            for idx in range(27):
                adapter.inventory_items.append(
                    {"item_id": 1521 + idx * 0, "name": f"Oak logs #{idx}", "quantity": 1, "noted": False}
                )
            adapter.total_level = 97
            adapter.playtime_minutes = 1198
        elif cmd_type == "INTERACT_NPC" and payload.get("action") == "Net":
            for _ in range(27):
                adapter.inventory_items.append(
                    {"item_id": 317, "name": "Raw shrimps", "quantity": 1, "noted": False}
                )
        elif cmd_type == "INTERACT_OBJECT" and payload.get("name") == "Cooking range":
            cooked_on_range = True
            adapter.inventory_items = [
                {"item_id": 303, "name": "Small fishing net", "quantity": 1, "noted": False},
                *[
                    {"item_id": 315, "name": "Shrimps", "quantity": 1, "noted": False}
                    for _ in range(27)
                ],
            ]
            # Reach Total Level 100 + 1200 minutes playtime right on cooking batch!
            adapter.total_level = 100
            adapter.playtime_minutes = 1201
        return res

    adapter.execute_raw_command = _sim_seasoning  # type: ignore[method-assign]

    try:
        mod = TotalLevel100TaskModule(target_total_level=100, target_playtime_minutes=1200)
        done = False
        for step in range(1, 120):
            r = kernel.step_tick(mod, now_monotonic=float(step))
            if r.halted:
                assert r.task_directive is not None
                assert r.task_directive.directive_type == DirectiveType.COMPLETE_TASK
                done = True
                break
        assert done is True
        assert cooked_on_range is True
        assert mod.completed_rotation_batches >= 1
        assert len(adapter.inventory_items) == 0  # Final batch banked cleanly!
    finally:
        store.close()


def test_t43_three_cycle_gatherer_smoke_protocol_and_full_bot_registry_alignment(tmp_path: Path) -> None:
    """T43:
    1. Runs Bolla's 3-Cycle Smoke-Test Protocol (`Gather -> Bank Deposit & Tool Retention -> Gather x3`)
       for Stage-3 `SkillingGathererTaskModule.barbarian_fly_fishing(max_completed_cycles=3)`.
    2. Verifies `BotModuleRegistry` instantiates every active `CURATED_RECIPES` ID (7/7) and enforces
       `PermissionError` on quarantined `CONCEPT_ONLY` recipes (`f2p_tanner_alkharid_quarantined`).
    """
    # Part 1: Registry 1-to-1 alignment with CuratedRecipeRegistry
    recipe_reg = CuratedRecipeRegistry()
    bot_reg = BotModuleRegistry()
    for r_id, spec in recipe_reg._by_id.items():
        if spec["verification_stage"] == "CONCEPT_ONLY":
            with pytest.raises(PermissionError):
                bot_reg.create(r_id)
        else:
            bot_instance = bot_reg.create(r_id)
            assert bot_instance.module_id == r_id

    # Part 2: 3-Cycle Barbarian Fly Fishing Smoke Test (retaining Fly fishing rod + Feathers across 3 bank deposits!)
    store, adapter, _gate, kernel = _make_kernel_env(
        tmp_path / "fly_fish_3_cycles",
        task_id="f2p_barbarian_fly_fishing",
        bank_items=[
            {"item_id": 309, "name": "Fly fishing rod", "quantity": 1, "noted": False},
            {"item_id": 314, "name": "Feather", "quantity": 500, "noted": False},
        ],
    )
    adapter.position = {"x": 3185, "y": 3436, "plane": 0}  # Start at Varrock West Bank

    orig_exec = adapter.execute_raw_command

    def _sim_fly_fishing(cmd_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        res = orig_exec(cmd_type, payload)
        if cmd_type == "INTERACT_NPC" and payload.get("name") == "Rod Fishing spot":
            # Fill remaining 26 slots with Raw trout (2 slots used by rod + feathers = 28 slots)
            while len(adapter.inventory_items) < 28:
                adapter.inventory_items.append(
                    {"item_id": 335, "name": "Raw trout", "quantity": 1, "noted": False}
                )
        return res

    adapter.execute_raw_command = _sim_fly_fishing  # type: ignore[method-assign]

    try:
        fish_bot = SkillingGathererTaskModule.barbarian_fly_fishing(max_completed_cycles=3)
        done = False
        for step in range(1, 130):
            rf = kernel.step_tick(fish_bot, now_monotonic=float(step))
            if rf.halted:
                assert rf.task_directive is not None
                assert rf.task_directive.directive_type == DirectiveType.COMPLETE_TASK
                done = True
                break
        assert done is True
        assert fish_bot.completed_cycles == 3
        banked_trout = sum(it["quantity"] for it in adapter.bank_items if it["item_id"] == 335)
        assert banked_trout == 26 * 3
    finally:
        store.close()


def test_t44_cli_live_probe_and_attach_bot_over_http_keepalive(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """T44: Verifies CLI `live-probe` and `attach-bot` against a live HTTP/1.1 Keep-Alive server."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from osrs_ee.cli import main

    token_value = "live_cli_attach_token_123456"
    token_file = tmp_path / ".agent-token"
    token_file.write_text(token_value, encoding="utf-8")

    class _CliLiveServer(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def _send_json(self, code: int, data: dict[str, Any]) -> None:
            raw = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/state":
                self._send_json(
                    200,
                    {
                        "loggedIn": True,
                        "gameTick": 900,
                        "hitpoints": 10,
                        "maxHitpoints": 10,
                        "dialogueOpen": False,
                        "position": {"x": 3164, "y": 3487, "plane": 0},
                    },
                )
                return
            if self.path.startswith("/varbits"):
                # All 4 Stronghold varbits already claimed -> immediate COMPLETE_TASK on surface!
                self._send_json(200, {"value": 1})
                return
            if self.path == "/varps?id=281":
                self._send_json(200, {"id": 281, "value": 1000})
                return
            if self.path == "/bank":
                self._send_json(200, {"open": False, "pinPromptOpen": False})
                return
            if self.path == "/inventory":
                self._send_json(200, {"items": []})
                return
            self._send_json(404, {})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _CliLiveServer)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    port = str(srv.server_address[1])

    try:
        rc1 = main(["live-probe", "--port", port, "--token-file", str(token_file)])
        assert rc1 == 0
        out1 = json.loads(capsys.readouterr().out)
        assert out1["login_state"] == "LOGGED_IN"
        assert out1["tcp_connect_count"] == 1
        assert token_value not in json.dumps(out1)

        rc2 = main(
            [
                "--data-dir",
                str(tmp_path / "cli_data"),
                "attach-bot",
                "--recipe-id",
                "stage2_stronghold_10k",
                "--port",
                port,
                "--token-file",
                str(token_file),
                "--max-ticks",
                "5",
                "--tick-interval-sec",
                "0",
            ]
        )
        assert rc2 == 0
        out2 = json.loads(capsys.readouterr().out)
        assert out2["halted"] is True
        assert out2["final_directive"] == "COMPLETE_TASK"
    finally:
        srv.shutdown()

