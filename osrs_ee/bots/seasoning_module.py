"""Stage 1B — F2P Trade-Unlock Sprint (`stage1b_seasoning_gatherer`).

**F2P trade restriction removal requires ALL THREE** (`OSRS Wiki: Free-to-play` — verified 2026-10-31):

    playtime  > 20 hours   AND   quest points >= 10   AND   total level >= 100

That third gate is the only thing still missing on `acc_02` (13 QP ✅, total level **41**). This module
exists to close it **bank-only, zero GE, zero operator clicks** (`AD-1`).

`PR-22` — why this file changed
------------------------------
`PR-13` wrote this module against `TotalLevelScript.java` and `T42` proved its **rotation logic** in
isolation. The logic was right; the *mechanics* were never live-proven, and three of them could not work
in the field:

1. **It withdrew an axe it could never own.** `BANK_TURNAROUND` demanded `Bronze axe (1351)` from a bank
   that has 53 GP and no axe, and there is **no shop, GE, or ground-item endpoint** in AgentServer — the
   bot cannot buy one. Result: a bank loop that never starts the woodcutting block.
2. **It cooked on a name nobody verified.** `INTERACT_OBJECT {"name": "Cooking range"}` at the Lumbridge
   kitchen. The **live-proven** cook target in this codebase is the permanent Barbarian Village fire
   `(3078,3445)` + the Cook-All widget `270:14` (`PR-15`, `StrongholdTaskModule`). Reuse beats invention.
3. **It would have latched total level `32` forever.** `ctx.total_level` was only ever populated from
   `/state.totalLevel`, which the upstream client omits, and the kernel fell back to the literal `32`
   (`_opt_metric` → `_coalesce_int(..., 32)`). See `PR-22` in `sandbox/http_adapter.py` and
   `core/execution_kernel.py`: the number is now transported (`/skills` fallback, throttled) and
   remembered monotonically.

Also: `target_playtime_minutes = 0` does **not** unlock trading — it only makes this module *report*
completion early. `strict_trade_unlock=True` (default) keeps the 20-hour gate honest, so
`COMPLETE_TASK` means "the account is actually unrestricted", not "we stopped counting".

Mechanics below are the live-proven ones (`LIVE_WIRE_VERIFIED`, `PR-15` / `task-11512`):

    Fishing tutor  -> free `Small fishing net (303)`      (no gold, no shop)
    Fishing spot   -> `Net`  -> raw shrimps/anchovies     at `LUMBRIDGE_SWAMP_FISHING (3243,3152)`
    Fire (26185)   -> `Cook` -> `270:14` Cook-All         at `BARBARIAN_RIVER_FIRE (3078,3445)`
    `LUMBRIDGE_WEST_TREES (3190,3245)` -> `Tree` / `Oak tree` above Woodcutting 15

Gold cost of the whole sprint: **0 GP**.
"""

from __future__ import annotations

from typing import Any, Mapping

from osrs_ee.bots.base import DirectiveType, TaskDirective, TaskModule, TickContext
from osrs_ee.navigation.waypoint_graph import DEFAULT_WORLD_NODES, WorldPoint

AXE_IDS: frozenset[int] = frozenset({1351, 1349, 1353, 1361, 1355, 1357, 1359})  # Bronze..Rune axe
SMALL_FISHING_NET_ID = 303
RAW_FISH_NAMES: frozenset[str] = frozenset({"raw shrimps", "raw anchovies"})

#: `OSRS Wiki: Free-to-play` — the 20-hour leg of the F2P trade restriction.
TRADE_UNLOCK_PLAYTIME_MINUTES = 1200

#: Live-proven Cook-All chatbox widget (`PR-15`, `StrongholdTaskModule`).
COOK_ALL_WIDGET: dict[str, int] = {"groupId": 270, "childId": 14}

#: Live-proven permanent fire outside Barbarian Village — no quest, no door, no fuel (`PR-15`).
BARBARIAN_RIVER_FIRE = WorldPoint(3078, 3445, 0, "BARBARIAN_RIVER_FIRE")

#: `name -> (waypoint, object name, action)` for the cook leg.
#: `barbarian_fire` is `LIVE_WIRE_VERIFIED`; `lumbridge_range` is ~60 tiles closer to the swamp but its
#: object name has never been probed (`SUPPLY_STEP_STALLED` risk) — enable it only after a live `/objects`
#: reading confirms the name. Never let a guess become the default.
COOK_TARGETS: dict[str, tuple[str, str, str]] = {
    "barbarian_fire": ("BARBARIAN_RIVER_FIRE", "Fire", "Cook"),
    "lumbridge_range": ("LUMBRIDGE_KITCHEN", "Range", "Cook"),
}

#: Words that identify the fishing-tutor handout inside a dialogue menu (live `PR-15` behaviour).
_FISHING_TUTOR_HINTS: tuple[str, ...] = ("net", "shrimp", "fish", "yes", "okay", "sure")


def _has_any_axe(items: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]]) -> bool:
    for it in items:
        if int(it.get("item_id", -1)) in AXE_IDS or "axe" in str(it.get("name", "")).casefold():
            return True
    return False


def _has_small_net(items: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]]) -> bool:
    for it in items:
        if int(it.get("item_id", -1)) == SMALL_FISHING_NET_ID or (
            str(it.get("name", "")).strip().casefold() == "small fishing net"
        ):
            return True
    return False


def _count_raw_fish(items: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]]) -> int:
    return sum(
        int(it.get("quantity", 1))
        for it in items
        if str(it.get("name", "")).strip().casefold() in RAW_FISH_NAMES
    )


class TotalLevel100TaskModule(TaskModule):
    """Pure tick-driven F2P trade-unlock sprint (`stage1b_seasoning_gatherer`), bank-only."""

    def __init__(
        self,
        *,
        target_total_level: int = 100,
        target_playtime_minutes: int = TRADE_UNLOCK_PLAYTIME_MINUTES,
        anchor_bank_node: str = "VARROCK_WEST_BANK",
        cook_caught_fish: bool = True,
        allow_woodcutting: bool = True,
        strict_trade_unlock: bool = True,
        claim_free_net: bool = True,
        cook_target: str = "barbarian_fire",
    ) -> None:
        self.target_total_level = max(32, int(target_total_level))
        self.target_playtime_minutes = max(0, int(target_playtime_minutes))
        #: `PR-22`: a module that reports completion while the account is still restricted is a lie of
        #: the exact family we keep paying for. Keep the 20-hour leg unless someone deliberately opts out.
        self.strict_trade_unlock = bool(strict_trade_unlock)
        self.anchor_bank_node = anchor_bank_node
        self.cook_caught_fish = bool(cook_caught_fish)
        self.allow_woodcutting = bool(allow_woodcutting)
        self.claim_free_net = bool(claim_free_net)
        if cook_target not in COOK_TARGETS:
            raise ValueError(f"Unknown cook_target {cook_target!r}; expected one of {sorted(COOK_TARGETS)}")
        self.cook_target = cook_target
        self._active_block: str = "WOODCUTTING" if self.allow_woodcutting else "FISHING_COOKING"
        self._was_banking_batch: bool = False
        self._awaiting_cook_widget: bool = False
        self._cook_clicked_tick: int = -(10**6)
        self._last_raw_fish_qty: int = 0
        self._last_action_tick: int = -(10**6)
        #: `PR-22`: one bank look per missing tool, then fall through to the free handout. Without this
        #: latch the module cannot even SEE the bank's contents (`ctx.bank_items` is only populated while
        #: the bank is OPEN), so "is the net in the bank?" is unanswerable from outside -> infinite loop.
        self._tool_probe_done: bool = False
        self.completed_rotation_batches: int = 0

    @property
    def module_id(self) -> str:
        return "stage1b_seasoning_gatherer"

    @property
    def active_block(self) -> str:
        return self._active_block

    @property
    def effective_playtime_target(self) -> int:
        """The playtime threshold this module will actually honour."""
        if self.strict_trade_unlock:
            return max(self.target_playtime_minutes, TRADE_UNLOCK_PLAYTIME_MINUTES)
        return self.target_playtime_minutes

    def _anchor_wp(self) -> WorldPoint:
        if self.anchor_bank_node == "GRAND_EXCHANGE":
            return WorldPoint(3164, 3487, 0, "GRAND_EXCHANGE")
        return WorldPoint(3185, 3436, 0, self.anchor_bank_node)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _free_slots(items: tuple[Mapping[str, Any], ...] | list[Mapping[str, Any]]) -> int:
        used = sum(max(1, int(it.get("quantity", 1))) for it in items)
        return max(0, 28 - used)

    def _dialogue_directive(self, ctx: TickContext) -> TaskDirective:
        """Claim the free net from the Fishing tutor, then dismiss everything else."""
        if ctx.dialogue_options:
            for opt in ctx.dialogue_options:
                low = str(opt).casefold()
                if any(h in low for h in _FISHING_TUTOR_HINTS):
                    return TaskDirective(
                        directive_type=DirectiveType.SELECT_DIALOGUE,
                        payload={"option": opt},
                        reason=f"Claiming free Small fishing net (303) from the Fishing tutor: {opt!r}",
                    )
        return TaskDirective(
            directive_type=DirectiveType.CONTINUE_DIALOGUE,
            reason="Dismissing dialogue during the trade-unlock sprint",
        )

    def _bank_directive(self, ctx: TickContext, *, withdraw: list[dict[str, Any]]) -> TaskDirective:
        anchor_wp = self._anchor_wp()
        if ctx.position.chebyshev_distance(anchor_wp) > 4:
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination=self.anchor_bank_node,
                reason=f"Routing to {self.anchor_bank_node} for the {self._active_block} turnaround",
            )
        return TaskDirective(
            directive_type=DirectiveType.BANK_TURNAROUND,
            payload={
                "deposit_inventory": True,
                "withdraw": withdraw,
                # `PR-22`: a tool the bank does not hold must never stall the whole sprint. The old
                # strict payload turned "no axe in the bank" into an infinite deposit loop.
                "allow_best_effort": True,
                "close_when_done": True,
            },
            reason=f"Bank-only turnaround for {self._active_block} (Zero GE, zero gold)",
        )

    # ------------------------------------------------------------------ main tick
    def evaluate_tick(self, ctx: TickContext) -> TaskDirective:
        if ctx.dialogue_open:
            return self._dialogue_directive(ctx)

        playtime_target = self.effective_playtime_target
        goal_reached = (
            ctx.total_level >= self.target_total_level and ctx.playtime_minutes >= playtime_target
        )
        anchor_wp = self._anchor_wp()

        # 1. Immediate latch: bank what is carried, then complete.
        if goal_reached:
            if ctx.position.chebyshev_distance(anchor_wp) > 4:
                return TaskDirective(
                    directive_type=DirectiveType.NAVIGATE_TO,
                    destination=self.anchor_bank_node,
                    reason=(
                        f"Trade-unlock goal latched (total={ctx.total_level}, "
                        f"playtime={ctx.playtime_minutes}m); homing to {self.anchor_bank_node}"
                    ),
                )
            if ctx.inventory_items or ctx.bank_is_open:
                return TaskDirective(
                    directive_type=DirectiveType.BANK_TURNAROUND,
                    payload={"deposit_inventory": True, "withdraw": [], "close_when_done": True},
                    reason="Depositing the final sprint batch (Bank-Only, Zero GE)",
                )
            return TaskDirective(
                directive_type=DirectiveType.COMPLETE_TASK,
                reason=(
                    f"Stage 1B completed: total_level={ctx.total_level} >= {self.target_total_level}, "
                    f"playtime_minutes={ctx.playtime_minutes} >= {playtime_target} "
                    f"(strict_trade_unlock={self.strict_trade_unlock})"
                ),
            )

        raw_qty = _count_raw_fish(ctx.inventory_items)
        free_slots = self._free_slots(ctx.inventory_items)

        # 2. Cook the fish we already hold — on the LIVE-PROVEN fire, never an unverified range name.
        cook_node, cook_object, cook_action = COOK_TARGETS[self.cook_target]
        cook_wp = DEFAULT_WORLD_NODES[cook_node]
        # Cook a FULL batch, never one shrimp: the fire is a long walk and a per-fish trip would make the
        # sprint slower than simply cooking nothing.
        should_cook = self._awaiting_cook_widget or (
            self.cook_caught_fish and raw_qty > 0 and free_slots <= 2 and self._active_block == "FISHING_COOKING"
        )
        if raw_qty > 0 and self._awaiting_cook_widget:
            should_cook = True
        if should_cook:
            if ctx.position.chebyshev_distance(cook_wp) > 2:
                self._awaiting_cook_widget = False
                return TaskDirective(
                    directive_type=DirectiveType.NAVIGATE_TO,
                    destination=cook_node,
                    reason=f"Routing {raw_qty} raw fish to the cook target {cook_node}",
                )
            if raw_qty < self._last_raw_fish_qty:
                # The batch is being consumed: never interrupt the animation (the tick-290 family).
                self._last_raw_fish_qty = raw_qty
                self._last_action_tick = ctx.game_tick
                self._awaiting_cook_widget = False
                return TaskDirective(
                    directive_type=DirectiveType.WAIT_NEXT_TICK,
                    reason=f"Cooking on the fire ({raw_qty} raw remaining)",
                )
            if not self._awaiting_cook_widget and ctx.game_tick - self._last_action_tick < 6:
                # `PR-22` LIVELOCK FIX: this WAIT must NOT refresh `_last_action_tick`, or the 6-tick
                # throttle resets itself every tick and the bot waits forever (reproduced here).
                return TaskDirective(
                    directive_type=DirectiveType.WAIT_NEXT_TICK,
                    reason=f"Waiting for the cook cycle to start ({raw_qty} raw held)",
                )
            if self._awaiting_cook_widget:
                if ctx.game_tick - self._cook_clicked_tick < 2:
                    return TaskDirective(
                        directive_type=DirectiveType.WAIT_NEXT_TICK,
                        reason="Waiting 1 tick for the Cook-All widget (270:14) to open",
                    )
                self._awaiting_cook_widget = False
                self._last_raw_fish_qty = raw_qty
                self._last_action_tick = ctx.game_tick
                return TaskDirective(
                    directive_type=DirectiveType.CLICK_WIDGET,
                    payload=dict(COOK_ALL_WIDGET),
                    reason="Confirming Cook-All on widget 270:14",
                )
            self._awaiting_cook_widget = True
            self._cook_clicked_tick = ctx.game_tick
            self._last_action_tick = ctx.game_tick
            return TaskDirective(
                directive_type=DirectiveType.INTERACT_OBJECT,
                payload={
                    "name": cook_object,
                    "action": cook_action,
                    "target_x": int(cook_wp.x),
                    "target_y": int(cook_wp.y),
                },
                reason=f"Interacting with {cook_object} ({cook_node}) to cook {raw_qty} raw fish",
            )

        # 3. Batch boundary: bank, then rotate block.
        needs_axe = self._active_block == "WOODCUTTING" and not _has_any_axe(ctx.inventory_items)
        needs_net = self._active_block == "FISHING_COOKING" and not _has_small_net(ctx.inventory_items)
        bank_axe = any(int(it.get("item_id", -1)) in AXE_IDS for it in ctx.bank_items)
        bank_net = any(int(it.get("item_id", -1)) == SMALL_FISHING_NET_ID for it in ctx.bank_items)
        if _has_any_axe(ctx.inventory_items) and _has_small_net(ctx.inventory_items):
            self._tool_probe_done = False  # both tools in hand: a future need may look again
        needs_tool = needs_axe or needs_net
        # `PR-22`: a bank trip is only worth making if the bank can actually GIVE us what is missing.
        # The old code routed to the bank for any missing tool -- including tools the bank never had --
        # and then re-routed there forever. Missing + not in bank must fall through to the live-proven
        # free handout (Fishing tutor) or to the other skill block.
        inv_full = len(ctx.inventory_items) >= 28
        # `PR-22` PING-PONG GUARD: the turn-around deposits the bag. If the bank is still open, the tool is
        # already in hand and the bag is not full, running another turnaround would DEPOSIT THE TOOL WE
        # JUST WITHDREW -- withdraw -> deposit -> withdraw, forever (reproduced on the very first run).
        # Nothing left to do here: close and leave.
        if ctx.bank_is_open and not inv_full and not needs_tool:
            return TaskDirective(
                directive_type=DirectiveType.BANK_TURNAROUND,
                payload={"deposit_inventory": False, "withdraw": [], "close_when_done": True},
                reason=f"Closing the bank before returning to {self._active_block}",
            )
        if inv_full or (needs_tool and (not self._tool_probe_done or ctx.bank_is_open)):
            if ctx.bank_is_open:
                # We are looking at the real contents right now: this was the probe.
                self._tool_probe_done = True
            if len(ctx.inventory_items) >= 28 and not self._was_banking_batch:
                self._was_banking_batch = True
                self.completed_rotation_batches += 1
                if self.allow_woodcutting:
                    self._active_block = (
                        "WOODCUTTING" if self._active_block == "FISHING_COOKING" else "FISHING_COOKING"
                    )
            withdraw: list[dict[str, Any]] = []
            if needs_axe and bank_axe:
                withdraw = [{"item_id": 1351, "name": "Bronze axe", "quantity": 1}]
            elif needs_net and bank_net:
                withdraw = [{"item_id": SMALL_FISHING_NET_ID, "name": "Small fishing net", "quantity": 1}]
            return self._bank_directive(ctx, withdraw=withdraw)

        self._was_banking_batch = False

        # 4. Woodcutting block (optional — skipped entirely when no axe can be obtained).
        if self._active_block == "WOODCUTTING" and not bank_axe and not _has_any_axe(ctx.inventory_items):
            # No axe in the bag and none in the bank, and AgentServer exposes no shop/GE endpoint: the
            # honest move is to stop asking and fall through to the tool-free half of the sprint.
            self._active_block = "FISHING_COOKING"
            needs_net = not _has_small_net(ctx.inventory_items)
        if self._active_block == "WOODCUTTING":
            trees_wp = WorldPoint(3190, 3245, 0, "LUMBRIDGE_WEST_TREES")
            if ctx.position.chebyshev_distance(trees_wp) > 5:
                return TaskDirective(
                    directive_type=DirectiveType.NAVIGATE_TO,
                    destination="LUMBRIDGE_WEST_TREES",
                    reason="Routing safely to Lumbridge West Trees",
                )
            wc_lvl = ctx.capabilities.skill_level("woodcutting")
            tree_name = "Oak tree" if wc_lvl >= 15 else "Tree"
            return TaskDirective(
                directive_type=DirectiveType.INTERACT_OBJECT,
                payload={"name": tree_name, "action": "Chop down"},
                reason=f"Chopping {tree_name} (WC={wc_lvl})",
            )

        # 5. Fishing block.
        fish_wp = WorldPoint(3243, 3152, 0, "LUMBRIDGE_SWAMP_FISHING")
        if ctx.position.chebyshev_distance(fish_wp) > 5:
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination="LUMBRIDGE_SWAMP_FISHING",
                reason="Routing safely to Lumbridge Swamp Fishing Spot",
            )

        if needs_net and not self.claim_free_net:
            return TaskDirective(
                directive_type=DirectiveType.ESCALATE_HUMAN,
                reason="TRADE_UNLOCK_NO_NET: no Small fishing net in the bag or bank, and free-net claim is disabled",
            )
        if needs_net and self.claim_free_net:
            if ctx.game_tick - self._last_action_tick < 3:
                return TaskDirective(
                    directive_type=DirectiveType.WAIT_NEXT_TICK,
                    reason="Waiting for the Fishing tutor net dialogue",
                )
            self._last_action_tick = ctx.game_tick
            return TaskDirective(
                directive_type=DirectiveType.INTERACT_NPC,
                payload={"name": "Fishing tutor", "action": "Talk-to"},
                reason="Claiming the free Small fishing net (303) from the Lumbridge Fishing tutor",
            )

        if ctx.game_tick - self._last_action_tick < 7:
            return TaskDirective(
                directive_type=DirectiveType.WAIT_NEXT_TICK,
                reason=f"Net fishing in progress ({raw_qty} raw, {free_slots} free slots)",
            )
        self._last_action_tick = ctx.game_tick
        self._last_raw_fish_qty = raw_qty
        return TaskDirective(
            directive_type=DirectiveType.INTERACT_NPC,
            payload={"name": "Fishing spot", "action": "Net"},
            reason="Net fishing Shrimps/Anchovies at Lumbridge Swamp",
        )
