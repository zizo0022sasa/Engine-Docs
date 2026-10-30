"""Hermetic in-memory Microbot AgentServer simulator with fault-injection hooks (PR-04)."""

from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from osrs_ee.contracts.models import validate_observation


class FakeGameAdapter:
    """Deterministic fake client instance supporting fault injection at every boundary."""

    def __init__(
        self,
        *,
        account_id: str,
        session_id: str,
        session_epoch: int,
        client_instance_id: str | None = None,
        jvm_flags: list[str] | None = None,
        tutorial_varp_281: int = 1000,
        quest_points: int = 10,
        total_level: int = 110,
        playtime_minutes: int = 1250,
        skills: dict[str, int] | None = None,
        bank_items: list[dict[str, Any]] | None = None,
        inventory_items: list[dict[str, Any]] | None = None,
        equipment_items: list[str] | None = None,
        ground_items: list[dict[str, Any]] | None = None,
        walk_speed_tiles_per_tick: int | None = None,
        unwalkable_tiles: set[tuple[int, int]] | None = None,
        interaction_grants: Callable[[str, dict[str, Any], "FakeGameAdapter"], None] | None = None,
        combat_level: int | None = None,
        is_member: bool = False,
        skill_xp: dict[str, int] | None = None,
    ) -> None:
        self.account_id = account_id
        self.session_id = session_id
        self.session_epoch = session_epoch
        self.client_instance_id = client_instance_id or f"fake_jvm_{uuid.uuid4().hex[:8]}"
        self.jvm_flags = list(
            jvm_flags
            if jvm_flags is not None
            else ["--disable-telemetry", "-Dmicrobot.disableTelemetry=true", "-Xmx768m"]
        )
        self.alive = True
        self.plugin_running = False
        self.auto_login_suppressed = False
        self.logged_in = True
        self.player_dead = False

        self.game_tick = 100
        self.freeze_ticks = False
        self.world = 308
        self.position = {"x": 3092, "y": 3243, "plane": 0}
        self.tutorial_varp_281 = tutorial_varp_281
        # Deliberately keep varbits namespace separate from varps (T10 proof!)
        self.varbits: dict[int, int] = {281: 0}
        self.varps: dict[int, int] = {281: tutorial_varp_281}
        self.dialogue_open: bool = False
        self.last_dialogue_snapshot: dict[str, Any] = {}
        self.quest_points = quest_points
        self.total_level = total_level
        self.playtime_minutes = playtime_minutes
        self.skills = dict(
            skills or {"hitpoints": 10, "agility": 1, "woodcutting": 35, "attack": 1, "fishing": 25, "mining": 20}
        )
        self.skill_xp: dict[str, int] = dict(skill_xp or {})
        self.combat_level: int | None = combat_level
        self.is_member: bool = bool(is_member)
        self.hp_current: int = int(self.skills.get("hitpoints", 10))
        self.hp_max: int = int(self.skills.get("hitpoints", 10))
        self.run_energy: int = 100
        self.run_enabled: bool = False
        # `None` = legacy instant teleport; an int = simulate a real client walk speed (PR-19.1).
        self.walk_speed_tiles_per_tick: int | None = walk_speed_tiles_per_tick
        self._walk_target: tuple[int, int, int] | None = None
        # `PR-19.3`: tiles the collision map rejects. Mirrors the live client behaviour
        # `[Walker] walk rejected: target has no walkable tile within threshold` — the command is
        # REFUSED and the character does not move at all.
        self.unwalkable_tiles: set[tuple[int, int]] = set(unwalkable_tiles or ())
        self.walk_rejections: int = 0
        self.under_attack: bool = False
        self.transient_disconnect: bool = False
        self._pending_heal_on_tick: tuple[int, int] | None = None  # (target_tick, heal_amount)

        self.bank_open = False
        self.bank_pin_prompt_open = False
        self.bank_items: list[dict[str, Any]] = copy.deepcopy(bank_items or [])
        self.inventory_items: list[dict[str, Any]] = copy.deepcopy(inventory_items or [])
        self.equipment_items: list[str] = list(equipment_items or [])
        # Zero-Touch world model: ground spawns available to `TAKE_GROUND_ITEM`
        # (upstream AgentServer `POST /ground-items/pickup`).
        # Optional test world script: called with (command_type, payload, adapter) so PR-19 zero-touch
        # gather chains can grant/consume items exactly like the live server would.
        self.interaction_grants: Callable[[str, dict[str, Any], "FakeGameAdapter"], None] | None = interaction_grants
        self.ground_items: list[dict[str, Any]] = [
            {"item_id": int(g["item_id"]), "name": str(g.get("name", "")), "quantity": int(g.get("quantity", 1))}
            for g in (ground_items or [])
        ]

        # Fault-injection toggles
        self.drop_response_after_effect_once = False
        self.fail_withdraw_silently = False
        self.hang_on_logout_sec: float = 0.0
        self._obs_seq = 0

    def advance_tick(self, steps: int = 1) -> None:
        if self.alive and not self.freeze_ticks:
            self.game_tick += steps
            # A real RuneLite client keeps walking toward the last commanded tile every tick.
            # Only active when `walk_speed_tiles_per_tick` is set (legacy tests still teleport).
            if self.walk_speed_tiles_per_tick is not None and self._walk_target is not None:
                for _ in range(steps):
                    self._step_towards_walk_target()
            if self._pending_heal_on_tick is not None:
                due_tick, heal_amt = self._pending_heal_on_tick
                if self.game_tick >= due_tick and not self.player_dead:
                    self.hp_current = min(self.hp_max, self.hp_current + heal_amt)
                    self._pending_heal_on_tick = None

    def probe_session_state(self) -> dict[str, Any]:
        """Lightweight single-probe connectivity & login check for `SessionLifecycleManager`."""
        if not self.alive or self.transient_disconnect:
            raise ConnectionError("Client endpoint unreachable or transiently disconnected")
        return {
            "reachable": True,
            "logged_in": bool(self.logged_in),
            "login_state": "LOGGED_IN" if self.logged_in else "LOGIN_SCREEN",
            "game_tick": self.game_tick if self.logged_in else None,
        }

    def kill_process_tree(self) -> None:
        self.alive = False
        self.plugin_running = False

    def read_varbit(self, varbit_id: int) -> int:
        """Reading Varbit 281 returns 0 even when Tutorial Varp 281 == 1000 (AUD-17 / T10)."""
        return self.varbits.get(varbit_id, 0)

    def read_varbits(self, varbit_ids: tuple[int, ...] | list[int]) -> dict[int, int]:
        return {int(vid): int(self.varbits.get(int(vid), 0)) for vid in varbit_ids}

    def read_varps(self, varp_ids: tuple[int, ...] | list[int]) -> dict[int, int]:
        out: dict[int, int] = {}
        for pid in varp_ids:
            ipid = int(pid)
            if ipid == 281:
                out[281] = int(self.tutorial_varp_281)
            else:
                out[ipid] = int(self.varps.get(ipid, 0))
        return out

    def read_skills_snapshot(self) -> dict[str, Any]:
        canonical = (
            "Attack",
            "Defence",
            "Strength",
            "Hitpoints",
            "Ranged",
            "Prayer",
            "Magic",
            "Cooking",
            "Woodcutting",
            "Fletching",
            "Fishing",
            "Firemaking",
            "Crafting",
            "Smithing",
            "Mining",
            "Herblore",
            "Agility",
            "Thieving",
            "Slayer",
            "Farming",
            "Runecraft",
            "Hunter",
            "Construction",
        )
        lower_levels = {str(k).strip().lower(): int(v) for k, v in self.skills.items()}
        lower_xp = {str(k).strip().lower(): int(v) for k, v in self.skill_xp.items()}
        skills_list: list[dict[str, Any]] = []
        for s_name in canonical:
            key = s_name.lower()
            default_lvl = 10 if key == "hitpoints" else 1
            lvl = max(1, int(lower_levels.get(key, default_lvl)))
            boosted = self.hp_current if key == "hitpoints" else lvl
            xp = lower_xp.get(key, 1154 if key == "hitpoints" and lvl == 10 else max(0, (lvl - 1) * 100))
            skills_list.append(
                {
                    "name": s_name,
                    "level": lvl,
                    "boostedLevel": boosted,
                    "xp": xp,
                }
            )
        total = sum(int(s["level"]) for s in skills_list)
        return {
            "count": len(skills_list),
            "totalLevel": total,
            "skills": skills_list,
        }

    def read_client_profile_hints(self) -> dict[str, Any]:
        return {
            "combat_level": self.combat_level,
            "profile_is_member": self.is_member,
            "playtime_minutes": self.playtime_minutes,
        }

    def read_observation(self, *, monotonic_ms: int = 1000) -> dict[str, Any]:
        if not self.alive or self.transient_disconnect:
            raise ConnectionError("Client JVM process is terminated or disconnected")
        if not self.logged_in:
            self._obs_seq += 1
            return validate_observation(
                {
                    "schema_version": "1.0",
                    "observation_id": f"obs_{self._obs_seq:06d}",
                    "observed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "monotonic_ms": monotonic_ms,
                    "account_id": self.account_id,
                    "session_id": self.session_id,
                    "client_instance_id": self.client_instance_id,
                    "session_epoch": self.session_epoch,
                    "login_state": "LOGIN_SCREEN",
                    "game_tick": None,
                    "world": None,
                    "position": {"x": None, "y": None, "plane": None, "quality": "UNKNOWN"},
                    "tutorial_varp_281": {"value": None, "quality": "UNKNOWN"},
                    "quest_points": {"value": None, "quality": "UNKNOWN"},
                    "total_level": {"value": None, "quality": "UNKNOWN"},
                    "playtime_minutes": {"value": None, "quality": "UNKNOWN"},
                    "dialogue_open": False,
                    "bank": {
                        "is_open": False,
                        "pin_prompt_open": False,
                        "items_quality": "UNKNOWN",
                        "items": None,
                    },
                    "inventory": {"free_slots": None, "quality": "UNKNOWN", "items": None},
                }
            )
        self.advance_tick(1)
        self._obs_seq += 1

        # Critical invariant (T10): BankHandler only knows bank items when bank is open!
        if self.bank_open and not self.bank_pin_prompt_open:
            bank_quality = "KNOWN"
            bank_list = copy.deepcopy(self.bank_items)
        else:
            bank_quality = "UNKNOWN"
            bank_list = None

        used_slots = sum(1 if not it["noted"] and it["item_id"] != 995 else 1 for it in self.inventory_items)
        obs = {
            "schema_version": "1.0",
            "observation_id": f"obs_{self._obs_seq:06d}",
            "observed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "monotonic_ms": monotonic_ms,
            "account_id": self.account_id,
            "session_id": self.session_id,
            "client_instance_id": self.client_instance_id,
            "session_epoch": self.session_epoch,
            "login_state": "LOGGED_IN" if self.logged_in else "LOGIN_SCREEN",
            "game_tick": self.game_tick,
            "world": self.world,
            "position": {
                "x": self.position["x"],
                "y": self.position["y"],
                "plane": self.position["plane"],
                "quality": "KNOWN",
            },
            "tutorial_varp_281": {"value": self.tutorial_varp_281, "quality": "KNOWN"},
            "quest_points": {"value": self.quest_points, "quality": "KNOWN"},
            "total_level": {"value": self.total_level, "quality": "KNOWN"},
            "playtime_minutes": {"value": self.playtime_minutes, "quality": "KNOWN"},
            "dialogue_open": bool(self.dialogue_open),
            "bank": {
                "is_open": self.bank_open,
                "pin_prompt_open": self.bank_pin_prompt_open,
                "items_quality": bank_quality,
                "items": bank_list,
            },
            "inventory": {
                "free_slots": max(0, 28 - used_slots),
                "quality": "KNOWN",
                "items": copy.deepcopy(self.inventory_items),
            },
            "vitals": {
                "hp_current": 0 if self.player_dead else self.hp_current,
                "hp_max": self.hp_max,
                "run_energy": self.run_energy,
                "run_enabled": self.run_enabled,
                "under_attack": self.under_attack,
            },
        }
        return validate_observation(obs)

    def execute_raw_command(self, command_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.alive:
            raise ConnectionError("Client JVM process is terminated")

        if command_type == "BANK_OPEN":
            self.bank_open = True
            return {"ok": True}

        if command_type == "BANK_CLOSE":
            self.bank_open = False
            return {"ok": True}

        if command_type == "BANK_DEPOSIT_INVENTORY":
            if not self.bank_open or self.bank_pin_prompt_open:
                return {"ok": False, "reason": "bank_not_accessible"}
            for inv_it in self.inventory_items:
                self._add_to_bank(inv_it)
            self.inventory_items.clear()
            return {"ok": True}

        if command_type == "BANK_DEPOSIT_EQUIPMENT":
            if not self.bank_open or self.bank_pin_prompt_open:
                return {"ok": False, "reason": "bank_not_accessible"}
            self.equipment_items.clear()
            return {"ok": True}

        if command_type == "BANK_WITHDRAW_ITEM":
            if not self.bank_open or self.bank_pin_prompt_open:
                return {"ok": False, "reason": "bank_not_accessible"}
            if self.fail_withdraw_silently:
                # Simulates Microbot withdrawX returning False while caller might ignore return
                return {"ok": False, "reason": "withdraw_failed"}

            target_name = payload.get("name")
            target_id = payload.get("item_id")
            qty = int(payload["quantity"])
            noted = bool(payload.get("noted", False))

            for b_it in self.bank_items:
                if (target_name and b_it["name"] == target_name) or (
                    target_id is not None and b_it["item_id"] == target_id
                ):
                    if b_it["quantity"] < qty:
                        return {"ok": False, "reason": "insufficient_quantity"}
                    b_it["quantity"] -= qty
                    if b_it["quantity"] == 0:
                        self.bank_items.remove(b_it)
                    self._add_to_inventory(
                        {
                            "item_id": b_it["item_id"],
                            "name": b_it["name"],
                            "quantity": qty,
                            "noted": noted,
                        }
                    )
                    if self.drop_response_after_effect_once:
                        self.drop_response_after_effect_once = False
                        raise TimeoutError("Simulated HTTP drop AFTER server applied bank withdrawal")
                    return {"ok": True}
            return {"ok": False, "reason": "item_not_found"}

        if command_type == "PLUGIN_START":
            self.plugin_running = True
            return {"ok": True}

        if command_type == "PLUGIN_STOP":
            self.plugin_running = False
            return {"ok": True}

        if command_type == "SAFE_LOGOUT":
            if self.hang_on_logout_sec > 0:
                import time
                time.sleep(self.hang_on_logout_sec)
                raise TimeoutError("SAFE_LOGOUT hung past deadline")
            self.plugin_running = False
            self.auto_login_suppressed = True
            self.logged_in = False
            return {"ok": True}

        if command_type == "TRIGGER_LOGIN":
            if self.auto_login_suppressed:
                return {"ok": False, "reason": "auto_login_suppressed"}
            self.logged_in = True
            return {"ok": True}

        if command_type == "WALK_TO_WORLD_POINT":
            tx, ty = int(payload["x"]), int(payload["y"])
            tplane = int(payload.get("plane", 0))
            if self.walk_speed_tiles_per_tick is None:
                self.position = {"x": tx, "y": ty, "plane": tplane}
            else:
                if (tx, ty) in self.unwalkable_tiles:
                    self.walk_rejections += 1
                    return {"ok": False, "reason": "target-not-walkable"}
                self._walk_target = (tx, ty, tplane)
                self._step_towards_walk_target()
                # Realistic RuneLite pathing: the character walks a few tiles per tick instead of
                # teleporting (default teleport keeps every legacy test byte-identical).
            self.run_energy = max(0, self.run_energy - 1) if self.run_enabled else min(100, self.run_energy + 1)
            return {"ok": True}

        if command_type == "SET_RUN_MODE":
            self.run_enabled = bool(payload.get("enabled", True))
            return {"ok": True}

        if command_type == "CONSUME_FOOD":
            food_id = int(payload["item_id"])
            heal = int(payload.get("expected_heal", 7))
            for it in self.inventory_items:
                if int(it["item_id"]) == food_id and int(it["quantity"]) > 0:
                    it["quantity"] -= 1
                    if it["quantity"] <= 0:
                        self.inventory_items.remove(it)
                    # Real OSRS behavior: HP updates on the NEXT game tick, NOT instantaneously!
                    self._pending_heal_on_tick = (self.game_tick + 1, heal)
                    return {"ok": True}
            return {"ok": False, "reason": "food_not_in_inventory"}

        if command_type in ("INTERACT_OBJECT", "INTERACT_NPC"):
            if self.interaction_grants is not None:
                self.interaction_grants(command_type, dict(payload), self)
            # `PR-20.7`: pickpocketing is a real world mechanic, so the FAKE must model it too —
            # otherwise every hermetic pipeline burns the 80-attempt earning cap and stalls on
            # `SUPPLY_UNSOURCED:Coins:pickpocket_exhausted`. Deliberately DETERMINISTIC (a successful
            # citizen pickpocket yields 3 GP, no RNG): a fake proves the chain, never the dice.
            if command_type == "INTERACT_NPC" and (
                str(payload.get("action", "")).strip().casefold() == "pickpocket"
            ):
                self._add_to_inventory(
                    {"item_id": 995, "name": "Coins", "quantity": 3, "noted": False}
                )
            return {"ok": True}

        if command_type in ("DIALOGUE_SELECT", "DIALOGUE_CONTINUE"):
            return {"ok": True}

        if command_type == "TAKE_GROUND_ITEM":
            if self.bank_open:
                return {"ok": False, "reason": "cannot_take_ground_item_while_bank_open"}
            spec_name = str(payload.get("name", "")).strip().casefold()
            spec_id = payload.get("id")
            match = next(
                (
                    g for g in self.ground_items
                    if (spec_id is not None and int(g["item_id"]) == int(spec_id))
                    or (spec_id is None and str(g.get("name", "")).strip().casefold() == spec_name)
                ),
                None,
            )
            if match is None:
                return {"ok": False, "reason": "ground_item_not_found"}
            self._add_to_inventory(
                {"item_id": int(match["item_id"]), "name": str(match.get("name", "")), "quantity": 1, "noted": False}
            )
            match["quantity"] = int(match.get("quantity", 1)) - 1
            if match["quantity"] <= 0:
                self.ground_items.remove(match)
            if self.interaction_grants is not None:
                self.interaction_grants(command_type, dict(payload), self)
            return {"ok": True, "item": {"id": int(match["item_id"]), "name": match.get("name", "")}}
        if command_type == "USE_ITEM_ON_ITEM":
            if self.interaction_grants is not None:
                self.interaction_grants(command_type, dict(payload), self)
                return {"ok": True}
            if self.bank_open:
                return {"ok": False, "reason": "cannot_use_item_on_item_while_bank_open"}
            src_id = int(payload["source_item_id"])
            dst_id = int(payload["target_item_id"])
            pair = frozenset({src_id, dst_id})
            combine_recipes: dict[frozenset[int], tuple[int, str]] = {
                frozenset({1769, 288}): (286, "Orange goblin mail"),
                frozenset({1767, 288}): (287, "Blue goblin mail"),
            }
            src_entry = next(
                (it for it in self.inventory_items if int(it["item_id"]) == src_id and not bool(it.get("noted")) and int(it["quantity"]) > 0),
                None,
            )
            dst_entry = next(
                (
                    it
                    for it in self.inventory_items
                    if int(it["item_id"]) == dst_id
                    and not bool(it.get("noted"))
                    and int(it["quantity"]) > (1 if src_id == dst_id else 0)
                ),
                None,
            )
            if src_entry is None or dst_entry is None:
                return {"ok": False, "reason": "combine_items_missing_in_inventory"}
            if pair in combine_recipes:
                out_id, out_name = combine_recipes[pair]
                src_entry["quantity"] -= 1
                if src_entry["quantity"] <= 0:
                    self.inventory_items.remove(src_entry)
                dst_entry_after = next(
                    it for it in self.inventory_items if int(it["item_id"]) == dst_id and not bool(it.get("noted")) and int(it["quantity"]) > 0
                )
                dst_entry_after["quantity"] -= 1
                if dst_entry_after["quantity"] <= 0:
                    self.inventory_items.remove(dst_entry_after)
                self._add_to_inventory({"item_id": out_id, "name": out_name, "quantity": 1, "noted": False})
            return {"ok": True}

        if command_type == "WIDGET_CLICK":
            has_coords = "groupId" in payload and "childId" in payload
            has_text = bool(payload.get("text"))
            if not (has_coords or has_text):
                return {"ok": False, "clicked": False, "reason": "Provide groupId+childId or text"}
            # `PR-20.2`: a production interface (Make-X `270:15`) APPLIES an effect on the server,
            # so the hermetic world script must see the click exactly like `INTERACT_OBJECT`.
            if self.interaction_grants is not None:
                self.interaction_grants(command_type, dict(payload), self)
            return {"ok": True, "clicked": True}

        raise ValueError(f"Unsupported command_type on FakeGameAdapter: {command_type}")

    def _step_towards_walk_target(self) -> None:
        if self._walk_target is None:
            return
        tx, ty, tplane = self._walk_target
        speed = max(1, int(self.walk_speed_tiles_per_tick or 1))
        cur = self.position
        cx, cy = int(cur["x"]), int(cur["y"])
        dx = max(-speed, min(speed, tx - cx))
        dy = max(-speed, min(speed, ty - cy))
        nx, ny = cx + dx, cy + dy
        if (nx, ny) in self.unwalkable_tiles:
            # A real client cannot step onto a blocked tile; it stops at the last walkable one.
            self._walk_target = None
            return
        self.position = {"x": nx, "y": ny, "plane": tplane if int(cur["plane"]) == tplane else int(cur["plane"])}
        if cx + dx == tx and cy + dy == ty and int(self.position["plane"]) == tplane:
            self._walk_target = None
        self.run_energy = max(0, self.run_energy - 1) if self.run_enabled else min(100, self.run_energy + 1)
        # Jagex behaviour: run auto-disables the moment the bar empties; it must then be re-enabled.
        if self.run_energy == 0:
            self.run_enabled = False

    def _add_to_bank(self, item: dict[str, Any]) -> None:
        for b in self.bank_items:
            if b["item_id"] == item["item_id"]:
                b["quantity"] += item["quantity"]
                return
        self.bank_items.append(
            {
                "item_id": item["item_id"],
                "name": item["name"],
                "quantity": item["quantity"],
                "noted": False,
            }
        )

    def _add_to_inventory(self, item: dict[str, Any]) -> None:
        for inv in self.inventory_items:
            if inv["item_id"] == item["item_id"] and inv["noted"] == item["noted"]:
                inv["quantity"] += item["quantity"]
                return
        self.inventory_items.append(dict(item))
