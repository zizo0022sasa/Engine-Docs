"""Stage 1A Non-Combat F2P Trade-Unlock Quests Module (`osrs_ee/bots/quests_module.py` — PR-13).

Field-Verified 12-QP Bundle (`TradeUnlockScript.java` + `جدول_الخطوات_وبروتوكول_العمل_المشترك.md`):
1. *Cook's Assistant* (`1 QP`, Varp `29`, terminal `>= 2`):
   - Supplies: `Egg` (`1944`), `Bucket of milk` (`1927`), `Pot of flour` (`1933`)
   - Objective: `Cook` at `LUMBRIDGE_KITCHEN` `(3206, 3214, 0)`
2. *Sheep Shearer* (`1 QP`, Varp `179`, terminal `>= 21`):
   - Supplies: `20x Ball of wool` (`1759` unnoted)
   - Objective: `Fred the Farmer` at `LUMBRIDGE_SHEEP_FARM` `(3190, 3273, 0)`
3. *Romeo & Juliet* (`5 QP`, Varp `144`, terminal `>= 100`):
   - Supplies: `Cadava berries` (`753`)
   - Varp-driven multi-floor stages across `Romeo` `(3211, 3422, 0)`, `Juliet` `(3158, 3427, 1)`
     via stairs `(3157, 3435, 0)` / `(3156, 3435, 1)`, `Father Lawrence` `(3254, 3483, 0)`,
     and `Apothecary` `(3195, 3405, 0)`.
4. *Goblin Diplomacy* (`5 QP`, Varp `62`, terminal `>= 6`):
   - Supplies: `Orange goblin mail` (`286`), `Blue goblin mail` (`287`), `Goblin mail` (`288`)
   - Objective: `General Bentnoze` at `GOBLIN_VILLAGE` `(2958, 3512, 0)`

Total Yield: `1 + 1 + 5 + 5 = 12 QP` (exceeding OSRS's `10 QP` Grand Exchange trade-unlock gate!).
Option selection is strictly text-verified (never 1-based numeric index guessing).
"""

from __future__ import annotations

from typing import Any, Mapping

from osrs_ee.bots.base import DirectiveType, TaskDirective, TaskModule, TickContext
from osrs_ee.navigation.waypoint_graph import WorldPoint

VARP_COOKS_ASSISTANT = 29
VARP_SHEEP_SHEARER = 179
VARP_ROMEO_AND_JULIET = 144
VARP_GOBLIN_DIPLOMACY = 62

ORANGE_GOBLIN_MAIL_ID = 286
BLUE_GOBLIN_MAIL_ID = 287
GOBLIN_MAIL_ID = 288
BLUE_DYE_ID = 1767
ORANGE_DYE_ID = 1769

# Exact verified dialogue option texts across all 4 quests (priority-ordered against OSRS Wiki Transcripts)
VERIFIED_QUEST_DIALOGUE_OPTIONS: tuple[str, ...] = (
    # Cook's Assistant
    "What's wrong?",
    "I'm always happy to help a cook in distress.",
    "Actually, I know where to find this stuff.",
    "I've got all the information I need. Thanks.",
    # Sheep Shearer
    "I'm looking for a quest.",
    "Yes, okay. I can do that.",
    "I'm back!",
    # Romeo & Juliet
    "Perhaps I could help to find her for you?",
    "Yes, I have seen her actually!",
    "Yes, ok, I'll let her know.",
    "Yes, okay. I'll let her know.",
    "Talk about something else.",
    "Talk about Romeo & Juliet.",
    "Ok, thanks.",
    # Goblin Diplomacy
    "Do you want me to pick an armour colour for you?",
    "What about a different colour?",
    "Yes, he looks fat.",
    "I have some orange armour here.",
    "I have some blue armour here.",
    "I have some brown armour here.",
    "Okay, I'll be back soon.",
    # Generic quest confirmation
    "Yes.",
    "Yes",
)


def _inv_count(items: tuple[Mapping[str, Any], ...], *, item_id: int, noted: bool = False) -> int:
    return sum(
        int(it.get("quantity", 0))
        for it in items
        if int(it["item_id"] if "item_id" in it else it.get("id", -1)) == item_id
        and bool(it.get("noted", False)) == noted
    )


class TradeUnlockQuestsTaskModule(TaskModule):
    """Pure tick-driven 12-QP non-combat quest chain (`stage1a_f2p_quests_10qp`)."""

    def __init__(self, *, target_quest_points: int = 10, anchor_bank_node: str = "GRAND_EXCHANGE") -> None:
        self.target_quest_points = max(1, int(target_quest_points))
        self.anchor_bank_node = anchor_bank_node
        self._last_combine_tick: int = -100

    @property
    def module_id(self) -> str:
        return "stage1a_f2p_quests_10qp"

    @property
    def watched_varps(self) -> tuple[int, ...]:
        return (VARP_COOKS_ASSISTANT, VARP_SHEEP_SHEARER, VARP_ROMEO_AND_JULIET, VARP_GOBLIN_DIPLOMACY)

    def _handle_quest_dialogue(self, ctx: TickContext) -> TaskDirective:
        if ctx.dialogue_options:
            for expected in VERIFIED_QUEST_DIALOGUE_OPTIONS:
                for opt in ctx.dialogue_options:
                    if opt.strip().casefold().rstrip(".!?") == expected.casefold().rstrip(".!?"):
                        return TaskDirective(
                            directive_type=DirectiveType.SELECT_DIALOGUE,
                            payload={"option": opt},
                            reason=f"Selected verified quest dialogue option: {opt}",
                        )
            return TaskDirective(
                directive_type=DirectiveType.ESCALATE_HUMAN,
                reason="UNRECOGNIZED_QUEST_DIALOGUE_OPTION",
            )
        return TaskDirective(
            directive_type=DirectiveType.CONTINUE_DIALOGUE,
            reason="Advancing quest dialogue / cutscene",
        )

    def _ensure_supplies_at_anchor(
        self,
        ctx: TickContext,
        required_items: list[dict[str, Any]],
        quest_label: str,
    ) -> TaskDirective | None:
        missing = any(
            _inv_count(ctx.inventory_items, item_id=int(req["item_id"]), noted=False) < int(req["quantity"])
            for req in required_items
        )
        if not missing and not ctx.bank_is_open:
            return None

        # Must return to ground plane before overworld navigation if upstairs at Juliet's
        if ctx.position.plane != 0:
            return self._descend_juliet_stairs(ctx.position)

        anchor_wp = WorldPoint(3164, 3487, 0, self.anchor_bank_node)
        if ctx.position.chebyshev_distance(anchor_wp) > 4:
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination=self.anchor_bank_node,
                reason=f"{quest_label}: routing to {self.anchor_bank_node} for quest supplies",
            )
        return TaskDirective(
            directive_type=DirectiveType.BANK_TURNAROUND,
            payload={
                "deposit_inventory": True,
                "withdraw": required_items,
                "close_when_done": True,
            },
            reason=f"{quest_label} (QP={ctx.quest_points}): preparing unnoted supplies at bank",
        )

    def _prepare_goblin_diplomacy_supplies(self, ctx: TickContext) -> TaskDirective | None:
        """Stage Goblin Diplomacy armours (`286`, `287`, `288`), crafting untradeable dyed mails in inventory."""
        orange_mail = _inv_count(ctx.inventory_items, item_id=ORANGE_GOBLIN_MAIL_ID, noted=False)
        blue_mail = _inv_count(ctx.inventory_items, item_id=BLUE_GOBLIN_MAIL_ID, noted=False)
        brown_mail = _inv_count(ctx.inventory_items, item_id=GOBLIN_MAIL_ID, noted=False)
        orange_dye = _inv_count(ctx.inventory_items, item_id=ORANGE_DYE_ID, noted=False)
        blue_dye = _inv_count(ctx.inventory_items, item_id=BLUE_DYE_ID, noted=False)

        if orange_mail >= 1 and blue_mail >= 1 and brown_mail >= 1:
            if ctx.bank_is_open:
                return TaskDirective(
                    directive_type=DirectiveType.BANK_TURNAROUND,
                    payload={"deposit_inventory": False, "withdraw": [], "close_when_done": True},
                    reason=f"Goblin Diplomacy (QP={ctx.quest_points}): all 3 goblin mails ready; closing bank",
                )
            return None

        need_orange_craft = 1 if orange_mail < 1 else 0
        need_blue_craft = 1 if blue_mail < 1 else 0
        required_brown_in_inv = 1 + need_orange_craft + need_blue_craft
        can_craft_in_inventory = (
            orange_dye >= need_orange_craft
            and blue_dye >= need_blue_craft
            and brown_mail >= required_brown_in_inv
        )

        if can_craft_in_inventory:
            # Upstream Rs2Inventory.java requires bank interface closed before clicking "Use" on inventory items!
            if ctx.bank_is_open:
                return TaskDirective(
                    directive_type=DirectiveType.BANK_TURNAROUND,
                    payload={"deposit_inventory": False, "withdraw": [], "close_when_done": True},
                    reason=f"Goblin Diplomacy (QP={ctx.quest_points}): closing bank before dyeing Goblin mail",
                )
            if (ctx.game_tick - self._last_combine_tick) < 2:
                return TaskDirective(
                    directive_type=DirectiveType.WAIT_NEXT_TICK,
                    reason=f"Goblin Diplomacy (QP={ctx.quest_points}): waiting 1 tick for dye combine",
                )
            self._last_combine_tick = ctx.game_tick
            if need_orange_craft == 1:
                return TaskDirective(
                    directive_type=DirectiveType.USE_ITEM_ON_ITEM,
                    payload={
                        "source_item_id": ORANGE_DYE_ID,
                        "target_item_id": GOBLIN_MAIL_ID,
                        "source_name": "Orange dye",
                        "target_name": "Goblin mail",
                    },
                    reason=f"Goblin Diplomacy (QP={ctx.quest_points}): dyeing Goblin mail with Orange dye (1769 -> 288)",
                )
            return TaskDirective(
                directive_type=DirectiveType.USE_ITEM_ON_ITEM,
                payload={
                    "source_item_id": BLUE_DYE_ID,
                    "target_item_id": GOBLIN_MAIL_ID,
                    "source_name": "Blue dye",
                    "target_name": "Goblin mail",
                },
                reason=f"Goblin Diplomacy (QP={ctx.quest_points}): dyeing Goblin mail with Blue dye (1767 -> 288)",
            )

        if ctx.position.plane != 0:
            return self._descend_juliet_stairs(ctx.position)

        anchor_wp = WorldPoint(3164, 3487, 0, self.anchor_bank_node)
        if ctx.position.chebyshev_distance(anchor_wp) > 4:
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination=self.anchor_bank_node,
                reason=f"Goblin Diplomacy (QP={ctx.quest_points}): routing to {self.anchor_bank_node} for dyes & Goblin mail",
            )

        if not ctx.bank_is_open:
            return TaskDirective(
                directive_type=DirectiveType.BANK_TURNAROUND,
                payload={"open_only": True},
                reason=f"Goblin Diplomacy (QP={ctx.quest_points}): opening bank to inspect dyes & Goblin mail",
            )

        bank_has_orange_mail = _inv_count(ctx.bank_items, item_id=ORANGE_GOBLIN_MAIL_ID, noted=False) >= 1
        bank_has_blue_mail = _inv_count(ctx.bank_items, item_id=BLUE_GOBLIN_MAIL_ID, noted=False) >= 1

        withdraw_list: list[dict[str, Any]] = []
        brown_needed_total = 1
        if orange_mail >= 1 or bank_has_orange_mail:
            withdraw_list.append({"item_id": ORANGE_GOBLIN_MAIL_ID, "name": "Orange goblin mail", "quantity": 1})
        else:
            withdraw_list.append({"item_id": ORANGE_DYE_ID, "name": "Orange dye", "quantity": 1})
            brown_needed_total += 1

        if blue_mail >= 1 or bank_has_blue_mail:
            withdraw_list.append({"item_id": BLUE_GOBLIN_MAIL_ID, "name": "Blue goblin mail", "quantity": 1})
        else:
            withdraw_list.append({"item_id": BLUE_DYE_ID, "name": "Blue dye", "quantity": 1})
            brown_needed_total += 1

        withdraw_list.append({"item_id": GOBLIN_MAIL_ID, "name": "Goblin mail", "quantity": brown_needed_total})

        return TaskDirective(
            directive_type=DirectiveType.BANK_TURNAROUND,
            payload={
                "deposit_inventory": True,
                "withdraw": withdraw_list,
                "close_when_done": True,
            },
            reason=f"Goblin Diplomacy (QP={ctx.quest_points}): withdrawing dyes & {brown_needed_total}x Goblin mail",
        )

    @staticmethod
    def _ascend_juliet_stairs(pos: WorldPoint) -> TaskDirective:
        stairs_ground = WorldPoint(3157, 3435, 0, "VARROCK_JULIET_STAIRS")
        if pos.chebyshev_distance(stairs_ground) > 3:
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination="VARROCK_JULIET_STAIRS",
                reason="Romeo & Juliet: routing safely to Juliet ground stairs",
            )
        return TaskDirective(
            directive_type=DirectiveType.INTERACT_OBJECT,
            payload={"name": "Staircase", "action": "Climb-up"},
            reason="Romeo & Juliet: climbing up staircase to Juliet balcony (plane 1)",
        )

    @staticmethod
    def _descend_juliet_stairs(pos: WorldPoint) -> TaskDirective:
        stairs_up = WorldPoint(3156, 3435, 1, "JULIET_UPSTAIRS")
        if pos.chebyshev_distance(stairs_up) > 2:
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination=stairs_up,
                reason="Romeo & Juliet: walking to upstairs staircase (3156, 3435, 1)",
            )
        return TaskDirective(
            directive_type=DirectiveType.INTERACT_OBJECT,
            payload={"name": "Staircase", "action": "Climb-down"},
            reason="Romeo & Juliet: climbing down staircase to ground floor (plane 0)",
        )

    @staticmethod
    def _walk_and_talk(pos: WorldPoint, node_name: str, target_wp: WorldPoint, npc_name: str, label: str) -> TaskDirective:
        if pos.plane != target_wp.plane or pos.chebyshev_distance(target_wp) > 4:
            dest: str | WorldPoint = node_name if target_wp.plane == 0 else target_wp
            return TaskDirective(
                directive_type=DirectiveType.NAVIGATE_TO,
                destination=dest,
                reason=f"{label}: routing safely to {npc_name}",
            )
        return TaskDirective(
            directive_type=DirectiveType.INTERACT_NPC,
            payload={"name": npc_name, "action": "Talk-to"},
            reason=f"{label}: talking to {npc_name}",
        )

    def evaluate_tick(self, ctx: TickContext) -> TaskDirective:
        if ctx.tutorial_varp_281 < 1000:
            return TaskDirective(
                directive_type=DirectiveType.ESCALATE_HUMAN,
                reason="PRECONDITION_FAILED_TUTORIAL_VARP_281_INCOMPLETE",
            )

        if ctx.dialogue_open:
            return self._handle_quest_dialogue(ctx)

        cook_state = int(ctx.varps.get(VARP_COOKS_ASSISTANT, 0))
        sheep_state = int(ctx.varps.get(VARP_SHEEP_SHEARER, 0))
        romeo_state = int(ctx.varps.get(VARP_ROMEO_AND_JULIET, 0))
        goblin_state = int(ctx.varps.get(VARP_GOBLIN_DIPLOMACY, 0))

        all_four_done = cook_state >= 2 and sheep_state >= 21 and romeo_state >= 100 and goblin_state >= 6

        # 1. Immediate QP Goal Latch (`quest_points >= 10` or all 4 quests complete -> home & bank!)
        if ctx.quest_points >= self.target_quest_points or all_four_done:
            if ctx.position.plane != 0:
                return self._descend_juliet_stairs(ctx.position)
            anchor_wp = WorldPoint(3164, 3487, 0, self.anchor_bank_node)
            if ctx.position.chebyshev_distance(anchor_wp) > 4:
                return TaskDirective(
                    directive_type=DirectiveType.NAVIGATE_TO,
                    destination=self.anchor_bank_node,
                    reason=f"Quest target reached (QP={ctx.quest_points}); homing to {self.anchor_bank_node}",
                )
            if ctx.inventory_items or ctx.bank_is_open:
                return TaskDirective(
                    directive_type=DirectiveType.BANK_TURNAROUND,
                    payload={"deposit_inventory": True, "withdraw": [], "close_when_done": True},
                    reason="Depositing leftover quest items at anchor bank",
                )
            return TaskDirective(
                directive_type=DirectiveType.COMPLETE_TASK,
                reason=f"Stage 1A Quest Bundle completed (QP={ctx.quest_points}) and anchored at {self.anchor_bank_node}",
            )

        # 2. Quest 1: Cook's Assistant (1 QP)
        if cook_state < 2:
            if cook_state == 0:
                prep = self._ensure_supplies_at_anchor(
                    ctx,
                    [
                        {"item_id": 1944, "name": "Egg", "quantity": 1},
                        {"item_id": 1927, "name": "Bucket of milk", "quantity": 1},
                        {"item_id": 1933, "name": "Pot of flour", "quantity": 1},
                    ],
                    "Cook's Assistant",
                )
                if prep is not None:
                    return prep
            return self._walk_and_talk(
                ctx.position,
                "LUMBRIDGE_KITCHEN",
                WorldPoint(3206, 3214, 0),
                "Cook",
                f"Cook's Assistant (QP={ctx.quest_points})",
            )

        # 3. Quest 2: Sheep Shearer (1 QP, Varp 179: 0 -> 1 -> 2..20 -> 21)
        if sheep_state < 21:
            wool_needed = 20 if sheep_state <= 1 else max(0, 21 - sheep_state)
            if wool_needed > 0:
                prep = self._ensure_supplies_at_anchor(
                    ctx,
                    [{"item_id": 1759, "name": "Ball of wool", "quantity": wool_needed}],
                    "Sheep Shearer",
                )
                if prep is not None:
                    return prep
            return self._walk_and_talk(
                ctx.position,
                "LUMBRIDGE_SHEEP_FARM",
                WorldPoint(3190, 3273, 0),
                "Fred the Farmer",
                f"Sheep Shearer (QP={ctx.quest_points})",
            )

        # 4. Quest 3: Romeo & Juliet (5 QP, Varp 144: 0 -> 10 -> 20 -> 30 -> 40 -> 50 -> 60 -> 100)
        if romeo_state < 100:
            if romeo_state == 0:
                prep = self._ensure_supplies_at_anchor(
                    ctx,
                    [{"item_id": 753, "name": "Cadava berries", "quantity": 1}],
                    "Romeo & Juliet",
                )
                if prep is not None:
                    return prep
            # Stages requiring Juliet on plane 1: state == 10 (deliver/get letter) or state == 50 with Cadava potion (756)
            has_potion = _inv_count(ctx.inventory_items, item_id=756, noted=False) >= 1
            needs_juliet = romeo_state == 10 or (romeo_state == 50 and has_potion)
            if needs_juliet:
                if ctx.position.plane == 0:
                    return self._ascend_juliet_stairs(ctx.position)
                return self._walk_and_talk(
                    ctx.position,
                    "JULIET_BALCONY",
                    WorldPoint(3158, 3427, 1, "JULIET_BALCONY"),
                    "Juliet",
                    "Romeo & Juliet",
                )

            # All other Romeo stages are on plane 0 -> descend first if currently on plane 1!
            if ctx.position.plane != 0:
                return self._descend_juliet_stairs(ctx.position)

            if romeo_state in (0, 20, 60):
                return self._walk_and_talk(
                    ctx.position, "VARROCK_SQUARE", WorldPoint(3211, 3422, 0), "Romeo", "Romeo & Juliet"
                )
            if romeo_state == 30:
                return self._walk_and_talk(
                    ctx.position,
                    "VARROCK_EAST_CHURCH",
                    WorldPoint(3254, 3483, 0),
                    "Father Lawrence",
                    "Romeo & Juliet",
                )
            return self._walk_and_talk(
                ctx.position,
                "VARROCK_APOTHECARY",
                WorldPoint(3195, 3405, 0),
                "Apothecary",
                "Romeo & Juliet",
            )

        # 5. Quest 4: Goblin Diplomacy (5 QP, Varp 62 < 6)
        if goblin_state == 0:
            prep = self._prepare_goblin_diplomacy_supplies(ctx)
            if prep is not None:
                return prep
        return self._walk_and_talk(
            ctx.position,
            "GOBLIN_VILLAGE",
            WorldPoint(2958, 3512, 0),
            "General Bentnoze",
            f"Goblin Diplomacy (QP={ctx.quest_points})",
        )
