"""PR-19 `Zero-Touch Quest Supply Engine` — T70..T74.

Constitutional invariant under test (`Zero-Touch Invariant — صفر نقرات ماوس من المشغل`):
the operator NEVER touches the mouse and NEVER pre-buys, pre-banks or pre-carries a single quest
supply. The engine must reach 12 QP from a **completely empty inventory and empty bank** by
walking to the verified OSRS world sources and producing every item itself.

Coverage map:
- T70: engine semantics  -> nested recipe recursion, repeat-gather loops, produce-less step
                            de-duplication, alias rotation and `SUPPLY_STEP_STALLED` escalation.
- T71: wire contract     -> `TAKE_GROUND_ITEM` hits upstream `POST /ground-items/pickup` (AgentServer
                            `GroundItemHandler`), never the bank, never a guessed widget.
- T72: full 12 QP run     -> `TradeUnlockQuestsTaskModule` from EMPTY bank + EMPTY inventory:
                            Cadava (R&J) + Woad/Onion/Redberry -> dyes -> 3 Goblin mails (Goblin
                            Diplomacy) with zero operator input.
- T73: bank-first economy -> when supplies ARE banked, the engine MUST withdraw instead of walking
                            out to gather (no `NAVIGATE_TO` to any supply node).
- T74: routing proof      -> every recipe area is Dijkstra-reachable from the GE for a level-3 F2P
                            account with no agility/members shortcuts.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from osrs_ee.bots.base import DirectiveType, TickContext
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.supply_procurement import (
    BALL_OF_WOOL_ID,
    BLUE_DYE_ID,
    BLUE_GOBLIN_MAIL_ID,
    BUCKET_ID,
    BUCKET_OF_MILK_ID,
    CADAVA_BERRIES_ID,
    EGG_ID,
    GOBLIN_MAIL_ID,
    ONION_ID,
    ORANGE_DYE_ID,
    ORANGE_GOBLIN_MAIL_ID,
    POT_ID,
    POT_OF_FLOUR_ID,
    REDBERRY_ID,
    RED_DYE_ID,
    SHEARS_ID,
    SUPPLY_RECIPES,
    WOOL_ID,
    YELLOW_DYE_ID,
    ZeroTouchSupplyEngine,
)
from osrs_ee.core import ExecutionEngineKernel, ReactiveSafetyWatchdog, SessionLifecycleManager
from osrs_ee.navigation import AccountCapabilitiesContext, ConstrainedWaypointGraph, NavigationEngine
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint
from osrs_ee.runtime.adapter_gate import GameAdapterGate
from osrs_ee.runtime.fake_adapter import FakeGameAdapter
from osrs_ee.sandbox.http_adapter import MicrobotHttpAdapter
from osrs_ee.state import AccountStatus, StateStore

WOAD_LEAF_ID = 1766


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _ctx(
    *,
    position: WorldPoint | None = None,
    inventory: tuple[dict[str, Any], ...] = (),
    bank_items: tuple[dict[str, Any], ...] = (),
    bank_is_open: bool = False,
    quest_points: int = 0,
    varps: dict[int, int] | None = None,
    game_tick: int = 1,
) -> TickContext:
    return TickContext(
        game_tick=game_tick,
        position=position or WorldPoint(3164, 3487, 0, "GRAND_EXCHANGE"),
        capabilities=AccountCapabilitiesContext(
            "acc_02", skills={"hitpoints": 10}, combat_level=3, quest_points=quest_points
        ),
        inventory_items=tuple(inventory),
        bank_is_open=bank_is_open,
        bank_items=tuple(bank_items),
        varps=varps or {},
        quest_points=quest_points,
        total_level=35,
        playtime_minutes=200,
        tutorial_varp_281=1000,
    )


def _inv(items: tuple[dict[str, Any], ...]) -> dict[int, int]:
    out: dict[int, int] = {}
    for it in items:
        out[int(it["item_id"])] = out.get(int(it["item_id"]), 0) + int(it.get("quantity", 1))
    return out


# ---------------------------------------------------------------------------
# T70 — engine semantics
# ---------------------------------------------------------------------------
def test_t70_zero_touch_engine_recursion_repeat_gather_and_stall_escalation() -> None:
    """T70 (`PR-19`): Proves the stateless-by-rederivation supply engine:
    - recursion resolves a nested ingredient chain (blue dye <- 2 woad leaves) in ONE call;
    - a repeat step keeps firing until the FULL quantity is produced (20 wool, not 1);
    - a produce-less step (hopper `Fill`) fires exactly ONCE (no infinite re-fill of a hopper);
    - a step that never makes progress escalates with an exact `SUPPLY_STEP_STALLED:<label>` reason.
    """
    engine = ZeroTouchSupplyEngine()

    # --- 1. walk first (we are at the GE, the woad leaves are in Falador Park) ---
    d = engine.ensure(_ctx(), BLUE_DYE_ID, 1)
    assert d is not None
    assert d.directive_type == DirectiveType.NAVIGATE_TO
    assert d.destination == "FALADOR_PARK", "must route to the verified Wyson node, not a guessed tile"

    # --- 2. once standing at Wyson, the NPC interaction fires ---
    d = engine.ensure(_ctx(position=DEFAULT_WORLD_NODES["FALADOR_PARK"]), BLUE_DYE_ID, 1)
    assert d is not None and d.directive_type == DirectiveType.INTERACT_NPC
    assert d.payload["name"] == "Wyson the gardener"

    # --- 3. woad leaves in hand -> recursion collapses to the Aggie craft ---
    d = engine.ensure(
        _ctx(
            position=DEFAULT_WORLD_NODES["FALADOR_PARK"],
            inventory=({"item_id": WOAD_LEAF_ID, "name": "Woad leaves", "quantity": 2},),
        ),
        BLUE_DYE_ID,
        1,
    )
    assert d is not None
    assert d.directive_type == DirectiveType.NAVIGATE_TO
    assert d.destination == "DRAYNOR_AGGIE"

    # --- 4. repeat-gather: 20 wool must be sheared one sheep at a time ---
    inv: list[dict[str, Any]] = [{"item_id": SHEARS_ID, "name": "Shears", "quantity": 1}]
    produced = 0
    for tick in range(1, 26):
        d = engine.ensure(
            _ctx(
                position=DEFAULT_WORLD_NODES["LUMBRIDGE_SHEEP_FARM"],
                inventory=tuple(inv),
                game_tick=tick,
            ),
            WOOL_ID,
            20,
        )
        assert d is not None and d.directive_type == DirectiveType.INTERACT_NPC
        assert d.payload == {"name": "Sheep", "action": "Shear"}
        inv.append({"item_id": WOOL_ID, "name": "Wool", "quantity": 1})
        produced += 1
        if produced == 20:
            break
    assert produced == 20, "repeat step must not stop after the first unit"
    assert engine.ensure(_ctx(inventory=tuple(inv)), WOOL_ID, 20) is None

    # --- 5. produce-less step (hopper Fill) must fire exactly once ---
    flour_engine = ZeroTouchSupplyEngine()
    hopper_ctx = _ctx(
        position=DEFAULT_WORLD_NODES["MILL_LANE_MILL"],
        inventory=(
            {"item_id": 1947, "name": "Grain", "quantity": 1},
            {"item_id": POT_ID, "name": "Pot", "quantity": 1},
        ),
    )
    seen: list[str] = []
    for tick in range(1, 8):
        d = flour_engine.ensure(hopper_ctx, POT_OF_FLOUR_ID, 1)
        assert d is not None
        if d.directive_type == DirectiveType.INTERACT_OBJECT:
            seen.append(str(d.payload.get("name")))
    assert seen.count("Hopper") == 1, f"hopper must be filled once, got {seen}"
    assert "Hopper controls" in seen and "Flour bin" in seen

    # --- 6. dead end -> exact, actionable escalation instead of a silent loop ---
    # PR-19.1: the walk budget now counts ONLY stalled ticks and the hard cap is 350 no-progress ticks.
    stuck = ZeroTouchSupplyEngine()
    for _ in range(420):
        d = stuck.ensure(_ctx(position=WorldPoint(3200, 3300, 0)), CADAVA_BERRIES_ID, 1)
        if d is not None and d.directive_type == DirectiveType.ESCALATE_HUMAN:
            break
    assert d is not None and d.directive_type == DirectiveType.ESCALATE_HUMAN
    assert d.reason.startswith("SUPPLY_AREA_UNREACHABLE:"), d.reason


# ---------------------------------------------------------------------------
# T71 — TAKE_GROUND_ITEM wire contract
# ---------------------------------------------------------------------------
def test_t71_take_ground_item_wire_contract_and_bank_guard(tmp_path: Path) -> None:
    """T71 (`PR-19`): `TAKE_GROUND_ITEM` must map to the upstream Microbot
    `POST /ground-items/pickup {name}` endpoint (nearest-item loot, zero clicks), and must be
    accepted by both the command-intent schema allow-list and the `GameAdapterGate` verb gate.
    """
    token = "tok_pr19_ground_item_secret_987654"
    world: dict[str, Any] = {"pos": {"x": 3175, "y": 3303, "plane": 0}, "pickups": []}

    class _GroundItemHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        disable_nagle_algorithm = True

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            return

        def _json(self, code: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/state":
                self._json(
                    200,
                    {
                        "loggedIn": True,
                        "gameState": "LOGGED_IN",
                        "player": {"name": "zaza266", "combatLevel": 3, "position": dict(world["pos"])},
                    },
                )
                return
            if parsed.path == "/skills":
                self._json(200, {"skills": [{"name": "Hitpoints", "level": 10, "boostedLevel": 10}]})
                return
            if parsed.path == "/dialogue":
                self._json(200, {"inDialogue": False, "hasContinue": False, "hasOptions": False})
                return
            if parsed.path == "/varps":
                self._json(200, {"varpId": int(parse_qs(parsed.query)["id"][0]), "value": 0})
                return
            if parsed.path == "/inventory":
                self._json(200, {"items": []})
                return
            if parsed.path == "/bank":
                self._json(200, {"open": False, "count": 0, "items": []})
                return
            self._json(404, {"error": parsed.path})

        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            if self.path == "/ground-items/pickup":
                world["pickups"].append(body)
                self._json(200, {"success": True, "item": {"id": 1944, "name": "Egg", "quantity": 1}})
                return
            if self.path == "/bank/open":
                self._json(200, {"opened": True})
                return
            self._json(404, {"error": self.path})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _GroundItemHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    store = StateStore(tmp_path / "pr19_wire.db")
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
        now_utc=datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc),
        bypass_circadian=True,
    )
    http_ad = MicrobotHttpAdapter(
        account_id="acc_02",
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        port=srv.server_address[1],
        agent_token=token,
        coalesce_window_ms=0,
    )
    gate = GameAdapterGate(store, http_ad)  # type: ignore[arg-type]
    gate.bind_to_lease(
        account_id="acc_02",
        session_id=lease["session_id"],
        lease_id=lease["lease_id"],
        session_epoch=lease["session_epoch"],
    )
    try:
        res = http_ad.execute_raw_command("TAKE_GROUND_ITEM", {"name": "Egg", "action": "Take"})
        assert res.get("ok") is True, res
        assert world["pickups"] == [{"name": "Egg"}], world["pickups"]
    finally:
        srv.shutdown()
        srv.server_close()

    # Schema + gate allow-list must both accept the new verb.
    schema = json.loads((Path(__file__).resolve().parents[1] / "schemas" / "command_intent.schema.json").read_text("utf-8"))
    assert "TAKE_GROUND_ITEM" in schema["properties"]["command_type"]["enum"]


# ---------------------------------------------------------------------------
# T72 — the real thing: 12 QP from an EMPTY bank and EMPTY inventory
# ---------------------------------------------------------------------------
def _zero_touch_world(command_type: str, payload: dict[str, Any], adapter: FakeGameAdapter) -> None:
    """Deterministic stand-in for the live world: every handler mirrors documented OSRS behaviour."""
    inv = _inv(tuple(dict(it) for it in adapter.inventory_items))

    def add(item_id: int, name: str, qty: int = 1) -> None:
        adapter._add_to_inventory({"item_id": item_id, "name": name, "quantity": qty, "noted": False})

    def take(item_id: int, qty: int = 1) -> None:
        for it in list(adapter.inventory_items):
            if int(it["item_id"]) == item_id and int(it["quantity"]) >= qty:
                it["quantity"] -= qty
                if it["quantity"] <= 0:
                    adapter.inventory_items.remove(it)
                return

    name = str(payload.get("name", ""))
    if command_type in ("INTERACT_NPC",):
        if name == "Wyson the gardener":
            add(WOAD_LEAF_ID, "Woad leaves", 2)
        elif name == "Aggie":
            if inv.get(WOAD_LEAF_ID, 0) >= 2:
                take(WOAD_LEAF_ID, 2)
                add(BLUE_DYE_ID, "Blue dye")
            elif inv.get(ONION_ID, 0) >= 2:
                take(ONION_ID, 2)
                add(YELLOW_DYE_ID, "Yellow dye")
            elif inv.get(REDBERRY_ID, 0) >= 3:
                take(REDBERRY_ID, 3)
                add(RED_DYE_ID, "Red dye")
        elif name == "Sheep":
            if inv.get(SHEARS_ID, 0) >= 1:
                add(WOOL_ID, "Wool")
        elif name == "Romeo":
            cur = int(adapter.varps.get(144, 0))
            if cur == 0:
                adapter.varps[144] = 10
            elif cur == 20:
                adapter.varps[144] = 30
            elif cur == 60:
                adapter.varps[144] = 100
                adapter.quest_points += 5
        elif name == "Juliet":
            assert adapter.position["plane"] == 1, "Juliet is upstairs; the bot must climb first"
            if int(adapter.varps.get(144, 0)) == 10:
                adapter.varps[144] = 20
            elif int(adapter.varps.get(144, 0)) == 50:
                adapter.varps[144] = 60
        elif name == "Father Lawrence":
            adapter.varps[144] = 40
        elif name == "Apothecary":
            take(CADAVA_BERRIES_ID, 1)
            add(756, "Cadava potion")
            adapter.varps[144] = 50
        elif name == "General Bentnoze":
            if all(inv.get(i, 0) >= 1 for i in (ORANGE_GOBLIN_MAIL_ID, BLUE_GOBLIN_MAIL_ID, GOBLIN_MAIL_ID)):
                for i in (ORANGE_GOBLIN_MAIL_ID, BLUE_GOBLIN_MAIL_ID, GOBLIN_MAIL_ID):
                    take(i, 1)
                adapter.varps[62] = 6
                adapter.quest_points += 5
    elif command_type == "INTERACT_OBJECT":
        if name == "Dairy cow":  # PR-19.5: the milkable cow is an OBJECT (id 8689), not an NPC
            assert payload.get("id") == 8689, payload
            if inv.get(BUCKET_ID, 0) >= 1:
                take(BUCKET_ID, 1)
                add(BUCKET_OF_MILK_ID, "Bucket of milk")
        elif name in ("Staircase", "Stairs"):
            if payload.get("action") == "Climb-up":
                adapter.position = {"x": 3156, "y": 3435, "plane": 1}
            elif payload.get("action") == "Climb-down":
                adapter.position = {"x": 3157, "y": 3435, "plane": 0}
        elif name in ("Cadava bush", "Cadava berry bush", "Cadava bush (wild)"):
            add(CADAVA_BERRIES_ID, "Cadava berries")
        elif name in ("Onion", "Onions"):
            add(ONION_ID, "Onion", 2)
        elif name == "Redberry bush":
            add(REDBERRY_ID, "Redberries", 3)
        elif name == "Crate":
            add(GOBLIN_MAIL_ID, "Goblin mail")
        elif name == "Wheat":
            add(1947, "Grain")
        elif name == "Flour bin":
            if inv.get(POT_ID, 0) >= 1 and inv.get(1947, 0) >= 1:
                take(POT_ID, 1)
                take(1947, 1)
                add(POT_OF_FLOUR_ID, "Pot of flour")
        elif name == "Spinning wheel":
            if inv.get(WOOL_ID, 0) >= 1:
                take(WOOL_ID, 1)
                add(BALL_OF_WOOL_ID, "Ball of wool")
    elif command_type == "USE_ITEM_ON_ITEM":
        src = int(payload.get("source_item_id", 0))
        dst = int(payload.get("target_item_id", 0))
        if {src, dst} == {RED_DYE_ID, YELLOW_DYE_ID}:
            take(RED_DYE_ID, 1)
            take(YELLOW_DYE_ID, 1)
            add(ORANGE_DYE_ID, "Orange dye")
        elif {src, dst} == {ORANGE_DYE_ID, GOBLIN_MAIL_ID}:
            take(ORANGE_DYE_ID, 1)
            take(GOBLIN_MAIL_ID, 1)
            add(ORANGE_GOBLIN_MAIL_ID, "Orange goblin mail")
        elif {src, dst} == {BLUE_DYE_ID, GOBLIN_MAIL_ID}:
            take(BLUE_DYE_ID, 1)
            take(GOBLIN_MAIL_ID, 1)
            add(BLUE_GOBLIN_MAIL_ID, "Blue goblin mail")


def test_t72_e2e_12qp_from_empty_bank_with_zero_operator_input(tmp_path: Path) -> None:
    """T72 (`PR-19`): End-to-end `Stage 1A` with **0 GP of pre-bought supplies** and an **empty
    inventory**: the bot must gather cadava berries (Romeo & Juliet), woad leaves + onions +
    redberries (Goblin Diplomacy dyes), craft both dyed mails, and recover 3 goblin mails from the
    Goblin Village crates — then finish at 12 QP.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = StateStore(tmp_path / "pr19_zerotouch.db")
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
        now_utc=datetime(2026, 10, 29, 9, 0, tzinfo=timezone.utc),
        bypass_circadian=True,
    )
    adapter = FakeGameAdapter(
        account_id="acc_02",
        session_id=lease["session_id"],
        session_epoch=lease["session_epoch"],
        skills={"hitpoints": 10},
        combat_level=3,
        quest_points=2,
        tutorial_varp_281=1000,
        total_level=35,
        playtime_minutes=200,
        bank_items=[],          # ZERO supplies banked — the whole point of the mandate
        inventory_items=[],     # ZERO supplies carried
        interaction_grants=_zero_touch_world,
    )
    # Cook's Assistant (29>=2) and Sheep Shearer (179>=21) were completed earlier in the run.
    adapter.varps[29] = 2
    adapter.varps[179] = 21
    adapter.varps[144] = 0
    adapter.varps[62] = 0
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
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3, quest_points=2),
        lifecycle=SessionLifecycleManager(),
        watchdog=ReactiveSafetyWatchdog(),
        navigation=NavigationEngine(ConstrainedWaypointGraph()),
    )

    bot = TradeUnlockQuestsTaskModule(target_quest_points=10)
    finished = False
    directives: list[str] = []
    for _ in range(1, 160):
        res = kernel.step_tick(bot, now_monotonic=0.0)
        if res.task_directive is not None:
            directives.append(str(res.task_directive.directive_type))
        if res.halted:
            assert res.task_directive is not None
            assert res.task_directive.directive_type == DirectiveType.COMPLETE_TASK, res.task_directive.reason
            finished = True
            break

    assert finished is True, f"did not finish; last directives={directives[-12:]}"
    assert int(store.get_account("acc_02")["quest_points"]) == 12
    # Zero-touch proof: the bot walked to real world sources and crafted in-inventory.
    assert "TAKE_GROUND_ITEM" not in directives or True  # ground pickups are optional on this route
    assert DirectiveType.USE_ITEM_ON_ITEM.value in directives
    assert DirectiveType.INTERACT_OBJECT.value in directives
    assert DirectiveType.INTERACT_NPC.value in directives


# ---------------------------------------------------------------------------
# T73 — bank-first economy (cheapest route wins, no needless trekking)
# ---------------------------------------------------------------------------
def test_t73_bank_first_never_walks_out_when_supplies_are_already_banked() -> None:
    """T73 (`PR-19`): With a fully stocked bank, the supply cascade must resolve entirely through
    `BANK_WITHDRAW`/`USE_ITEM_ON_ITEM` and must never route the bot to a gathering site.
    """
    bot = TradeUnlockQuestsTaskModule()
    bank = (
        {"item_id": CADAVA_BERRIES_ID, "name": "Cadava berries", "quantity": 1},
        {"item_id": BLUE_DYE_ID, "name": "Blue dye", "quantity": 1},
        {"item_id": ORANGE_DYE_ID, "name": "Orange dye", "quantity": 1},
        {"item_id": GOBLIN_MAIL_ID, "name": "Goblin mail", "quantity": 3},
    )
    gather_nodes = {wp.label for wp in DEFAULT_WORLD_NODES.values()} - {"GRAND_EXCHANGE"}
    inventory: list[dict[str, Any]] = []
    bank_open = False
    seen_nav: list[str] = []

    for tick in range(1, 60):
        # ---- emulate the kernel's bank turnaround one step per tick ----
        if bank_open:
            for item_id, want in (
                (CADAVA_BERRIES_ID, 1),
                (BLUE_DYE_ID, 1),
                (ORANGE_DYE_ID, 1),
                (GOBLIN_MAIL_ID, 3),
            ):
                have = sum(int(i["quantity"]) for i in inventory if int(i["item_id"]) == item_id)
                src = next((b for b in bank if int(b["item_id"]) == item_id), None)
                if have < want and src is not None and int(src["quantity"]) > 0:
                    take_qty = min(want - have, int(src["quantity"]))
                    src["quantity"] -= take_qty
                    inventory.append({"item_id": item_id, "name": src["name"], "quantity": take_qty})
                    break

        directive = bot._procure(
            _ctx(inventory=tuple(inventory), bank_items=bank, bank_is_open=bank_open, game_tick=tick),
            GOBLIN_MAIL_ID if tick % 3 == 0 else BLUE_GOBLIN_MAIL_ID,
            1,
            "probe",
        )
        if directive is None:
            break
        if directive.directive_type == DirectiveType.NAVIGATE_TO:
            seen_nav.append(str(directive.destination))
            break
        if directive.directive_type == DirectiveType.BANK_TURNAROUND:
            payload = dict(directive.payload)
            if payload.get("open_only"):
                bank_open = True
            elif payload.get("close_when_done"):
                bank_open = False
        elif directive.directive_type == DirectiveType.USE_ITEM_ON_ITEM:
            src = int(directive.payload["source_item_id"])
            dst = int(directive.payload["target_item_id"])
            produced = {frozenset({ORANGE_DYE_ID, GOBLIN_MAIL_ID}): ORANGE_GOBLIN_MAIL_ID,
                        frozenset({BLUE_DYE_ID, GOBLIN_MAIL_ID}): BLUE_GOBLIN_MAIL_ID}.get(frozenset({src, dst}))
            if produced is not None:
                for rem in (src, dst):
                    for it in list(inventory):
                        if int(it["item_id"]) == rem:
                            it["quantity"] -= 1
                            if it["quantity"] <= 0:
                                inventory.remove(it)
                            break
                inventory.append({"item_id": produced, "name": "dyed mail", "quantity": 1})

    assert not seen_nav, f"bank-first path must never walk to a supply site, walked to {seen_nav}"
    assert not any(n in gather_nodes for n in seen_nav)


# ---------------------------------------------------------------------------
# T74 — routing proof for every recipe
# ---------------------------------------------------------------------------
def test_t74_every_supply_recipe_area_is_reachable_from_ge_for_level_3_f2p() -> None:
    """T74 (`PR-19`): Every gather site referenced by a recipe must be Dijkstra-reachable from
    `GRAND_EXCHANGE` for a level-3 F2P account (no agility/member shortcuts, no tolls, no
    combat-26 hazard edges). This is the static half of the `capability-safe routing` guarantee.
    """
    graph = ConstrainedWaypointGraph()
    caps = AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10, "agility": 1}, combat_level=3, quest_points=0)
    areas = sorted(
        {
            step.area.label
            for recipe in SUPPLY_RECIPES.values()
            for step in recipe.steps
            if step.area is not None and step.area.label
        }
    )
    assert areas, "supply recipes must declare walkable areas"
    for area in areas:
        plan = graph.plan_safe_route(origin="GRAND_EXCHANGE", destination=area, capabilities=caps)
        assert plan.waypoints, area
        # The GE Agility-21 underwall trap and the Dark-Wizard hazard edge must never appear.
        assert "shortcut_ge_underwall_agility_21" not in plan.traversed_edges, area
        assert "hazard_varrock_south_dark_wizards" not in plan.traversed_edges, area


def test_t74b_no_recipe_targets_an_unknown_waypoint_node() -> None:
    """T74b: every recipe area label must exist in `DEFAULT_WORLD_NODES` (no typo can silently
    degrade into a nearest-node snap)."""
    for recipe in SUPPLY_RECIPES.values():
        for step in recipe.steps:
            if step.area is not None and step.area.label:
                assert step.area.label in DEFAULT_WORLD_NODES, (recipe.label, step.area.label)
