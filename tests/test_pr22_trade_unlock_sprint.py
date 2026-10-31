"""`PR-22` — F2P trade-unlock sprint: transport, tools, and the 20-hour truth (T173..T178).

The operator asked whether `stage1b_seasoning_gatherer` could simply be **configured** to sprint to
total level 100 instead of being rewritten. It cannot — and the reasons are all in this file:

1. **The number never arrived.** `total_level` was read from `/state.totalLevel` only; the upstream
   client omits it, so the kernel substituted the literal `32`. A module aiming at 100 would grind
   forever while believing it was at 32. (`T173`, `T174`)
2. **The tools never arrived.** The module demanded `Bronze axe (1351)` from a bank that has none, and
   AgentServer exposes **no shop, GE, or ground-item endpoint** — the axe is unbuyable. The net is
   obtainable for free from the Fishing tutor (live-proven `PR-15`). (`T176`)
3. **The cook target was a guess.** `"Cooking range"` was never probed; the live-proven target is the
   Barbarian fire `(3078,3445)` + the Cook-All widget `270:14`. (`T177`)
4. **`target_playtime_minutes = 0` does not unlock trading.** Jagex requires **20 hours AND 100 total
   level AND 10 QP**; `compute_progression_stage` already encodes it. Completing at 100 with 5 hours
   played would report success on an account that is still restricted. (`T178`)

Full mechanics reference: `osrs_ee/bots/seasoning_module.py` module docstring.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_pr13_complete_bots_suite import _make_kernel_env  # noqa: E402

from osrs_ee.bots.base import DirectiveType, TaskDirective, TickContext  # noqa: E402
from osrs_ee.bots.seasoning_module import (  # noqa: E402
    BARBARIAN_RIVER_FIRE,
    COOK_ALL_WIDGET,
    COOK_TARGETS,
    SMALL_FISHING_NET_ID,
    TRADE_UNLOCK_PLAYTIME_MINUTES,
    TotalLevel100TaskModule,
)
from osrs_ee.navigation import AccountCapabilitiesContext  # noqa: E402
from osrs_ee.navigation.waypoint_graph import WorldPoint  # noqa: E402
from osrs_ee.sandbox.http_adapter import MicrobotHttpAdapter  # noqa: E402

TOKEN = "tok_pr22_sprint_secret_0123456789"


def _ctx(
    *,
    pos: tuple[int, int] = (3243, 3152),
    inventory: tuple[dict[str, Any], ...] = (),
    bank_items: tuple[dict[str, Any], ...] = (),
    bank_is_open: bool = False,
    total_level: int = 41,
    playtime_minutes: int = 60,
    tick: int = 1,
    skills: dict[str, int] | None = None,
) -> TickContext:
    return TickContext(
        game_tick=tick,
        position=WorldPoint(pos[0], pos[1], 0, "TEST"),
        capabilities=AccountCapabilitiesContext(
            "acc_02", skills=skills or {"hitpoints": 10}, combat_level=3, quest_points=13
        ),
        inventory_items=inventory,
        bank_items=bank_items,
        bank_is_open=bank_is_open,
        total_level=total_level,
        playtime_minutes=playtime_minutes,
    )


# ---------------------------------------------------------------------------
# T173 — the total level must actually reach the observation
# ---------------------------------------------------------------------------
def _serve(handler_factory) -> tuple[ThreadingHTTPServer, threading.Thread, list[str]]:
    hits: list[str] = []

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # noqa: D401
            return

        def _json(self, code: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:  # noqa: N802
            hits.append(self.path)
            handler_factory(self, parse_qs(urlparse(self.path).query))

    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    return srv, thread, hits


class _StateNoTotalLevel:
    """`StateHandler.java` shape: logged in, positioned, and NO `totalLevel`."""

    def __call__(self, handler: Any, qs: dict[str, list[str]]) -> None:
        path = urlparse(handler.path).path
        if path == "/state":
            handler._json(
                200,
                {
                    "loggedIn": True,
                    "gameState": "LOGGED_IN",
                    "player": {"name": "ZizoScouttt", "combatLevel": 3, "position": {"x": 3243, "y": 3152, "plane": 0}},
                    "playtimeMinutes": 733,
                },
            )
        elif path == "/skills":
            handler._json(
                200,
                {
                    "count": 23,
                    "totalLevel": 41,
                    "skills": [
                        {"name": "Hitpoints", "level": 10, "boostedLevel": 10},
                        {"name": "Woodcutting", "level": 1, "boostedLevel": 1},
                        {"name": "Fishing", "level": 1, "boostedLevel": 1},
                        {"name": "Cooking", "level": 1, "boostedLevel": 1},
                    ],
                },
            )
        elif path == "/bank":
            handler._json(200, {"open": False, "pinPromptOpen": False, "items": []})
        elif path == "/inventory":
            handler._json(200, {"items": []})
        elif path == "/dialogue":
            handler._json(200, {"inDialogue": False, "hasContinue": False, "hasOptions": False})
        elif path == "/varps":
            handler._json(200, {"id": int(qs.get("id", ["0"])[0]), "value": 1000})
        else:
            handler._json(200, {"ok": True})


def test_t173_total_level_is_backfilled_from_skills_when_state_omits_it() -> None:
    """T173 (`PR-22`): without this backfill the goal is unreachable by construction."""
    srv, _thread, _hits = _serve(_StateNoTotalLevel())
    try:
        adapter = MicrobotHttpAdapter(
            account_id="acc_02",
            session_id="sess_pr22",
            session_epoch=1,
            port=srv.server_address[1],
            agent_token=TOKEN,
        )
        obs = adapter.read_observation(monotonic_ms=1000)
        assert obs["total_level"]["quality"] == "KNOWN", obs["total_level"]
        assert obs["total_level"]["value"] == 41, obs["total_level"]
        # The per-skill levels ride along so the kernel can refresh capabilities mid-run.
        assert obs.get("skills_levels", {}).get("woodcutting") == 1
        assert obs["playtime_minutes"] == {"value": 733, "quality": "KNOWN"}
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# T174 — an UNKNOWN tick must never move the number backwards
# ---------------------------------------------------------------------------
def test_t174_an_unknown_total_level_tick_cannot_unlatch_a_finished_goal(tmp_path: Path) -> None:
    """T174 (`PR-22`): the goal must stay latched even if the client stops reporting the field.

    This is the "success returned vs effect" family in its nastiest form: a single `UNKNOWN` tick used
    to collapse `total_level` back to `32`, so a completed objective silently resumed grinding.
    """
    store, adapter, _gate, kernel = _make_kernel_env(
        tmp_path / "unlatch", task_id="stage1b_seasoning_gatherer", total_level=100, playtime_minutes=1210
    )
    adapter.position = {"x": 3243, "y": 3152, "plane": 0}
    seen: list[tuple[int, int]] = []

    class _Capture:
        module_id = "capture"
        watched_varbits: tuple[int, ...] = ()
        watched_varps: tuple[int, ...] = ()

        def evaluate_tick(self, ctx: TickContext):  # noqa: ANN001
            seen.append((ctx.total_level, ctx.playtime_minutes))
            return TaskDirective(directive_type=DirectiveType.WAIT_NEXT_TICK, reason="capture")

    try:
        capture = _Capture()
        kernel.step_tick(capture, now_monotonic=1.0)  # type: ignore[arg-type]
        adapter.total_level = None  # type: ignore[assignment]
        adapter.playtime_minutes = None  # type: ignore[assignment]
        kernel.step_tick(capture, now_monotonic=2.0)  # type: ignore[arg-type]
        kernel.step_tick(capture, now_monotonic=3.0)  # type: ignore[arg-type]
        assert seen[0] == (100, 1210), seen
        assert seen[1] == (100, 1210), (
            f"an UNKNOWN tick rewrote the goal state to {seen[1]}: the kernel fabricated a fallback"
        )
        assert seen[2] == (100, 1210), seen
    finally:
        store.close()


# ---------------------------------------------------------------------------
# T175 — skill levels reach the capabilities context
# ---------------------------------------------------------------------------
def test_t175_live_skill_levels_refresh_the_capabilities_context(tmp_path: Path) -> None:
    """T175 (`PR-22`): a module that branches on a level (Tree -> Oak tree at Woodcutting 15) must see
    the LIVE level, not the value the account had at boot."""
    store, adapter, _gate, kernel = _make_kernel_env(
        tmp_path / "skills_refresh", task_id="stage1b_seasoning_gatherer", total_level=95
    )
    adapter.position = {"x": 3190, "y": 3245, "plane": 0}  # Lumbridge West Trees
    adapter.inventory_items.append({"item_id": 1351, "name": "Bronze axe", "quantity": 1, "noted": False})
    orig_read = adapter.read_observation

    def _with_skills(*args: Any, **kwargs: Any) -> dict[str, Any]:
        obs = orig_read(*args, **kwargs)
        obs["skills_levels"] = {"woodcutting": 22, "fishing": 12, "cooking": 9}
        return obs

    adapter.read_observation = _with_skills  # type: ignore[method-assign]
    try:
        mod = TotalLevel100TaskModule()
        directive = kernel.step_tick(mod, now_monotonic=1.0).task_directive
        assert directive is not None
        assert kernel.capabilities.skill_level("woodcutting") == 22
        assert directive.payload["name"] == "Oak tree", directive.payload
    finally:
        store.close()


# ---------------------------------------------------------------------------
# T176 — tools: bank look, then the free net, никогда a shop that does not exist
# ---------------------------------------------------------------------------
def test_t176_an_unbuyable_axe_falls_through_to_the_tool_free_sprint() -> None:
    """T176 (`PR-22`): the axe cannot be bought (no shop/GE endpoint). The module must not loop forever
    asking a bank that does not hold it — it must run the fishing half, which needs nothing but a net."""
    mod = TotalLevel100TaskModule()
    ctx = _ctx(
        inventory=(),
        bank_items=({"item_id": 995, "name": "Coins", "quantity": 53, "noted": False},
                    {"item_id": 2357, "name": "Gold bar", "quantity": 1, "noted": False}),
        bank_is_open=True,
        pos=(3185, 3436),  # at the bank, looking inside: no axe, no net
    )
    directive = mod.evaluate_tick(ctx)
    # It never demands the axe back from a bank that cannot give it.
    assert "withdraw" in directive.payload
    assert all(int(w["item_id"]) != 1351 for w in directive.payload["withdraw"]), directive.payload

    # Next tick, bank closed, still no axe: the block must have fallen back to fishing.
    follow = mod.evaluate_tick(_ctx(inventory=(), pos=(3243, 3152), tick=2))
    assert mod.active_block == "FISHING_COOKING"
    assert follow.directive_type in (DirectiveType.INTERACT_NPC, DirectiveType.NAVIGATE_TO)
    if follow.directive_type == DirectiveType.INTERACT_NPC:
        assert follow.payload["name"] == "Fishing tutor"


def test_t176b_the_free_net_comes_from_the_fishing_tutor_not_from_gold() -> None:
    """T176b (`PR-22`): zero-GP tool acquisition, live-proven in `PR-15`."""
    mod = TotalLevel100TaskModule(allow_woodcutting=False)
    # 1) Exactly one bank look first (the only way to know whether a net is already banked).
    probe = mod.evaluate_tick(_ctx(inventory=(), pos=(3243, 3152), tick=1))
    assert probe.directive_type == DirectiveType.NAVIGATE_TO
    assert probe.destination == "VARROCK_WEST_BANK"
    # 2) Inside a bank that holds no net: close and leave, never demand gold.
    inside = mod.evaluate_tick(
        _ctx(inventory=(), pos=(3185, 3436), bank_is_open=True, tick=2)
    )
    assert inside.directive_type == DirectiveType.BANK_TURNAROUND
    assert inside.payload["withdraw"] == []
    # 3) Back at the swamp with no net anywhere: the live-proven free handout.
    directive = mod.evaluate_tick(_ctx(inventory=(), pos=(3243, 3152), tick=5))
    assert directive.directive_type == DirectiveType.INTERACT_NPC
    assert directive.payload == {"name": "Fishing tutor", "action": "Talk-to"}

    # With a net in hand the tutor is never bothered again.
    with_net = mod.evaluate_tick(
        _ctx(inventory=({"item_id": SMALL_FISHING_NET_ID, "name": "Small fishing net", "quantity": 1},), tick=20)
    )
    assert with_net.payload.get("action") == "Net"

    # ...and if the operator disables the handout, the module escalates instead of looping.
    strict = TotalLevel100TaskModule(allow_woodcutting=False, claim_free_net=False)
    strict._tool_probe_done = True
    escalated = strict.evaluate_tick(_ctx(inventory=(), pos=(3243, 3152), tick=5))
    assert escalated.directive_type == DirectiveType.ESCALATE_HUMAN
    assert escalated.reason.startswith("TRADE_UNLOCK_NO_NET")


# ---------------------------------------------------------------------------
# T177 — the cook leg uses the live-proven fire, and only that
# ---------------------------------------------------------------------------
def test_t177_cooking_uses_the_live_proven_fire_and_cook_all_widget() -> None:
    """T177 (`PR-22`): `"Cooking range"` was never probed. The Barbarian fire + `270:14` are live."""
    assert COOK_TARGETS["barbarian_fire"] == ("BARBARIAN_RIVER_FIRE", "Fire", "Cook")
    assert COOK_ALL_WIDGET == {"groupId": 270, "childId": 14}
    assert (BARBARIAN_RIVER_FIRE.x, BARBARIAN_RIVER_FIRE.y) == (3078, 3445)

    mod = TotalLevel100TaskModule(allow_woodcutting=False)
    raw = tuple(
        {"item_id": 317, "name": "Raw shrimps", "quantity": 1, "noted": False} for _ in range(27)
    ) + ({"item_id": SMALL_FISHING_NET_ID, "name": "Small fishing net", "quantity": 1},)

    # Standing at the fire with a full batch of raw fish: it clicks the Fire (never a range).
    directive = mod.evaluate_tick(_ctx(inventory=raw, pos=(3078, 3445), tick=10))
    assert directive.directive_type == DirectiveType.INTERACT_OBJECT
    assert directive.payload["name"] == "Fire" and directive.payload["action"] == "Cook"
    assert (directive.payload["target_x"], directive.payload["target_y"]) == (3078, 3445)

    # Two ticks later the Cook-All widget is confirmed.
    widget = mod.evaluate_tick(_ctx(inventory=raw, pos=(3078, 3445), tick=13))
    assert widget.directive_type == DirectiveType.CLICK_WIDGET
    assert dict(widget.payload) == {**COOK_ALL_WIDGET}

    # The alternative (kitchen range) exists but is opt-in and never the default.
    assert TotalLevel100TaskModule().cook_target == "barbarian_fire"
    with pytest.raises(ValueError):
        TotalLevel100TaskModule(cook_target="a_range_somewhere")


# ---------------------------------------------------------------------------
# T178 — the 20-hour leg is part of the goal, not a setting to skip
# ---------------------------------------------------------------------------
def test_t178_completing_at_total_100_does_not_unlock_trading() -> None:
    """T178 (`PR-22`): Jagex requires 20h + 100 total + 10 QP. A module that reports completion at
    total-100-only would declare victory on an account that is still restricted."""
    from osrs_ee.state.fsm import compute_progression_stage

    stage, unlocked = compute_progression_stage(
        tutorial_varp_281=1000, quest_points=13, total_level=100, playtime_minutes=300
    )
    assert unlocked is False and stage == "STAGE_1B_SEASONING"

    mod = TotalLevel100TaskModule(target_playtime_minutes=0)  # the operator's "sprint" setting
    assert mod.effective_playtime_target == TRADE_UNLOCK_PLAYTIME_MINUTES
    directive = mod.evaluate_tick(_ctx(inventory=(), total_level=100, playtime_minutes=300))
    assert directive.directive_type != DirectiveType.COMPLETE_TASK, (
        "the module claimed completion while the account is still trade-restricted"
    )

    # It completes the instant the third gate is genuinely met.
    done = mod.evaluate_tick(
        _ctx(  # at the bank anchor with an empty bag
            inventory=(),
            pos=(3185, 3436),
            total_level=100,
            playtime_minutes=TRADE_UNLOCK_PLAYTIME_MINUTES,
        )
    )
    assert done.directive_type == DirectiveType.COMPLETE_TASK
    stage, unlocked = compute_progression_stage(
        tutorial_varp_281=1000, quest_points=13, total_level=100, playtime_minutes=1200
    )
    assert unlocked is True and stage == "STAGE_3_F2P_SKILLING"

    # An explicit opt-out exists for a pure total-level sprint, and it is never the default.
    sprint = TotalLevel100TaskModule(target_playtime_minutes=0, strict_trade_unlock=False)
    assert sprint.effective_playtime_target == 0
    assert TotalLevel100TaskModule().strict_trade_unlock is True


def test_t178b_the_goal_needs_both_gates_not_either() -> None:
    """T178b (`PR-22`): 100 total with 5 hours, or 20 hours with 90 total, are both still restricted."""
    mod = TotalLevel100TaskModule()
    for total, playtime in ((100, 100), (90, 1300)):
        directive = mod.evaluate_tick(_ctx(inventory=(), total_level=total, playtime_minutes=playtime))
        assert directive.directive_type != DirectiveType.COMPLETE_TASK, (total, playtime)
