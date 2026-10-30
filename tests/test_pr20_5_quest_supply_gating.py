"""`PR-20.5` — Quest supply gating: never walk to a turn-in NPC empty-handed (T140..T142).

Field Incident #10 (live `acc_02` @ 8081, immediately after `PR-20.4` was pulled):

    [tick=0001] dir=NAVIGATE_TO | Goblin Diplomacy (QP=8): routing safely to General Bentnoze
    [tick=0070] state=IN_DANGER | Goblin Diplomacy (QP=8): routing safely to General Bentnoze

`GET /varps?id=62` returned **`1`** — the quest had been *accepted*. The gate read

    if goblin_state == 0:        # <-- only while the quest was NOT started
        prep = self._prepare_goblin_diplomacy_supplies(ctx)

so the instant the quest moved to state 1 the entire supply chain (onions -> dyes -> combines ->
crates -> 3 mails) was skipped, and the bot set off 250 tiles for the generals with an empty bag,
through the Dark Wizard corridor, at 2 HP. The field operator rescued it into Draynor bank.

The fix is one comparison (`< 6` instead of `== 0`), but the *class* of defect — "supplies are only
procured in state 0" — also existed on Cook's Assistant (`Varp 29 == 1`). Both are fixed, and the
structural test below forbids the class from coming back:

    For every quest, for every live intermediate varp value, with an empty bag and an empty bank:
    the engine must NEVER interact with that quest's turn-in NPC.

- T140: the exact live case — `Varp 62 == 1`, no mails -> procurement first, never the general.
- T141: the structural sweep across all four quests and all intermediate states.
- T142: `Varp 29 == 1` (started Cook's Assistant) procures egg + milk + flour first.
"""

from __future__ import annotations

from typing import Any

from osrs_ee.bots.base import DirectiveType, TickContext
from osrs_ee.bots.quests_module import TradeUnlockQuestsTaskModule
from osrs_ee.bots.supply_procurement import (
    BLUE_GOBLIN_MAIL_ID,
    COINS_ID,
    GOBLIN_MAIL_ID,
    ORANGE_GOBLIN_MAIL_ID,
)
from osrs_ee.navigation import AccountCapabilitiesContext
from osrs_ee.navigation.waypoint_graph import WorldPoint

# Quest turn-in / stage NPCs: the bot must never speak to them with the supplies missing.
TURN_IN_NPCS = ("General Bentnoze", "Fred the Farmer", "Cook", "General Wartface")

ALL_DONE = {29: 2, 179: 21, 144: 100, 62: 6}


def _bot() -> TradeUnlockQuestsTaskModule:
    bot = TradeUnlockQuestsTaskModule(target_quest_points=12)
    # Isolate the gating logic from the bank audit (proven separately in T107/T97).
    bot._bank_audit_done = True
    bot._coin_float_resolved = True
    return bot


def _ctx(
    varps: dict[int, int],
    *,
    inventory: tuple[dict[str, Any], ...] = (),
    pos: WorldPoint | None = None,
    quest_points: int = 8,
) -> TickContext:
    return TickContext(
        game_tick=20,
        position=pos or WorldPoint(3092, 3245, 0),  # Draynor bank, where the live bot was rescued
        capabilities=AccountCapabilitiesContext("acc_02", skills={"hitpoints": 10}, combat_level=3),
        inventory_items=inventory,
        varps=varps,
        quest_points=quest_points,
        total_level=40,
    )


def _assert_no_turn_in(directive: Any, *, where: str) -> None:
    if directive.directive_type != DirectiveType.INTERACT_NPC:
        return
    name = str(directive.payload.get("name", ""))
    assert name not in TURN_IN_NPCS, (
        f"{where}: the bot walked to {name!r} with its supplies missing — this is incident #10 "
        f"(payload={dict(directive.payload)}, reason={directive.reason!r})"
    )


# ---------------------------------------------------------------------------
# T140 — the exact live failure
# ---------------------------------------------------------------------------
def test_t140_goblin_state_one_no_longer_skips_the_supply_chain() -> None:
    """T140 (`PR-20.5`): `Varp 62 = 1` (accepted, not finished) must still procure. With an empty bag
    the next directive is supply work — never a 250-tile walk to the generals."""
    bot = _bot()
    directive = bot.evaluate_tick(_ctx({**ALL_DONE, 62: 1}))
    _assert_no_turn_in(directive, where="goblin_state=1, empty bag")
    assert directive.directive_type != DirectiveType.COMPLETE_TASK

    # It must be oriented toward the FAIRY-TALE-FREE source of a mail, not the general's hut:
    # either a walk toward a supply node, a gather action, or the coin float's bank turnaround.
    assert directive.directive_type in (
        DirectiveType.NAVIGATE_TO,
        DirectiveType.BANK_TURNAROUND,
        DirectiveType.TAKE_GROUND_ITEM,
        DirectiveType.INTERACT_OBJECT,
        DirectiveType.INTERACT_NPC,
        DirectiveType.WAIT_NEXT_TICK,
    )
    if directive.directive_type == DirectiveType.NAVIGATE_TO:
        dest = directive.destination
        # `GOBLIN_VILLAGE` is legitimate (the crates live there) — but only for supplies, so the
        # engine must not be claiming it is "routing safely to General Bentnoze".
        assert "General Bentnoze" not in (directive.reason or ""), directive.reason
        assert dest != "GOBLIN_VILLAGE" or "Crate" in (directive.reason or "") or "Goblin mail" in (
            directive.reason or ""
        ), directive.reason

    # Once the three mails ARE held, the general becomes the correct target.
    ready = (
        {"item_id": ORANGE_GOBLIN_MAIL_ID, "name": "Orange goblin mail", "quantity": 1},
        {"item_id": BLUE_GOBLIN_MAIL_ID, "name": "Blue goblin mail", "quantity": 1},
        {"item_id": GOBLIN_MAIL_ID, "name": "Goblin mail", "quantity": 1},
    )
    directive = _bot().evaluate_tick(_ctx({**ALL_DONE, 62: 1}, inventory=ready))
    assert directive.directive_type == DirectiveType.NAVIGATE_TO, directive
    assert directive.destination == "GOBLIN_VILLAGE"
    assert "General Bentnoze" in directive.reason


# ---------------------------------------------------------------------------
# T141 — the class, not the instance
# ---------------------------------------------------------------------------
def test_t141_no_quest_stage_ever_talks_to_a_turn_in_npc_without_its_supplies() -> None:
    """T141 (`PR-20.5`): sweep EVERY quest across EVERY live intermediate varp value with an empty bag
    and an empty bank. Talking to a turn-in NPC in any of those states is incident #10 again."""
    cases: list[tuple[str, dict[int, int]]] = []
    for cook in (0, 1):
        cases.append((f"cook={cook}", {**ALL_DONE, 29: cook}))
    for sheep in (0, 1, 2, 10, 20):
        cases.append((f"sheep={sheep}", {**ALL_DONE, 179: sheep}))
    for romeo in (0, 20, 30, 40):
        cases.append((f"romeo={romeo}", {**ALL_DONE, 144: romeo}))
    for goblin in (0, 1, 2, 3, 4, 5):
        cases.append((f"goblin={goblin}", {**ALL_DONE, 62: goblin}))

    for label, varps in cases:
        bot = _bot()
        bot._bank_stock = {}
        directive = bot.evaluate_tick(_ctx(varps))
        _assert_no_turn_in(directive, where=f"{label}, empty bag + empty bank at Draynor")

    # Every state must also stay *actionable* (never a dead `WAIT` forever, never a completion).
    for label, varps in cases:
        bot = _bot()
        bot._bank_stock = {COINS_ID: 10_000}
        directive = bot.evaluate_tick(_ctx(varps))
        assert directive.directive_type != DirectiveType.COMPLETE_TASK, label
        _assert_no_turn_in(directive, where=f"{label}, funded bank")


# ---------------------------------------------------------------------------
# T142 — the same defect class on Cook's Assistant
# ---------------------------------------------------------------------------
def test_t142_cooks_assistant_state_one_also_procures_before_visiting_the_cook() -> None:
    """T142 (`PR-20.5`): `Varp 29 == 1` (a started Cook's Assistant) used to skip the egg/milk/flour
    procurement exactly like the goblin case. The bot now gathers first and only then walks to the
    kitchen to hand in."""
    bot = _bot()
    directive = bot.evaluate_tick(_ctx({**ALL_DONE, 29: 1}))
    _assert_no_turn_in(directive, where="cook=1, empty bag")
    assert directive.directive_type == DirectiveType.NAVIGATE_TO, directive
    # ...and the destination is a supply source, not the Cook's kitchen hand-in.
    assert directive.destination in (
        "LUMBRIDGE_CHICKEN_COOP",
        "LUMBRIDGE_COW_FIELD",
        "LUMBRIDGE_KITCHEN",
        "MILL_LANE_WHEAT_FIELD",
        "GRAND_EXCHANGE",
    ), directive.destination
    assert "Cook's Assistant (QP" not in (directive.reason or ""), directive.reason

    # With the full ingredient set the hand-in is correct again.
    ready = (
        {"item_id": 1944, "name": "Egg", "quantity": 1},
        {"item_id": 1927, "name": "Bucket of milk", "quantity": 1},
        {"item_id": 1933, "name": "Pot of flour", "quantity": 1},
    )
    directive = _bot().evaluate_tick(_ctx({**ALL_DONE, 29: 1}, inventory=ready))
    assert directive.directive_type == DirectiveType.NAVIGATE_TO
    assert directive.destination == "LUMBRIDGE_KITCHEN"
    assert "Cook" in directive.reason
