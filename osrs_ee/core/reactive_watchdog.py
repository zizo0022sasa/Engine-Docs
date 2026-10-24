"""Non-Blocking Reactive Safety Watchdog (PR-12 / AUD-36 / ADR-009 / T33).

Replaces the legacy blocking `SafetyController.check()` (`while ... time.sleep(0.2)`) with a
pure O(1) per-tick state monitor that NEVER sleeps or freezes character movement:
- Evaluates HP, run energy, food availability, and unexpected underground->surface jumps in <0.1ms.
- Emits pipelined `eat_item_id` + `enable_run` directives alongside `preserve_movement=True` so the
  character eats AND runs continuously off dangerous tiles in the same 0.6s OSRS game tick!
- Respects OSRS's native 3-tick (1.8s) food eat delay and verifies HP gain reactively on subsequent
  game ticks (`tick >= pending_eat_tick + 1`) without blocking the execution stream.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Sequence

# Exact verified cooked foods (never raw fish, burnt meat, or substring guesses!)
VERIFIED_EDIBLE_FOOD_NAMES: frozenset[str] = frozenset(
    {
        "shrimps",
        "shrimp",
        "anchovies",
        "sardine",
        "herring",
        "mackerel",
        "trout",
        "cod",
        "pike",
        "salmon",
        "tuna",
        "lobster",
        "bass",
        "swordfish",
        "monkfish",
        "shark",
        "cooked meat",
        "cooked chicken",
        "bread",
        "cake",
        "jug of wine",
    }
)


class SafetyVerdict(StrEnum):
    NOMINAL = "NOMINAL"
    PIPELINED_EAT_AND_MOVE = "PIPELINED_EAT_AND_MOVE"
    AWAITING_NEXT_TICK_HEAL = "AWAITING_NEXT_TICK_HEAL"
    EMERGENCY_NO_FOOD_EVACUATE = "EMERGENCY_NO_FOOD_EVACUATE"
    EAT_UNCONFIRMED_EVACUATE = "EAT_UNCONFIRMED_EVACUATE"
    UNEXPECTED_DISPLACEMENT_HALT = "UNEXPECTED_DISPLACEMENT_HALT"
    PLAYER_DEAD_QUARANTINE = "PLAYER_DEAD_QUARANTINE"


@dataclass(frozen=True)
class SafetyDecision:
    verdict: SafetyVerdict
    in_danger: bool
    preserve_movement: bool
    halt_session: bool
    eat_food_item_id: int | None = None
    enable_run: bool = False
    detail: str = ""


class ReactiveSafetyWatchdog:
    """Zero-sleep reactive watchdog tracking HP & spatial invariants across game ticks."""

    def __init__(
        self,
        *,
        eat_hp_threshold: int = 7,
        run_energy_min_threshold: int = 20,
        eat_cooldown_ticks: int = 3,
        max_ticks_to_confirm_heal: int = 5,
    ) -> None:
        self.eat_hp_threshold = eat_hp_threshold
        self.run_energy_min_threshold = run_energy_min_threshold
        self.eat_cooldown_ticks = eat_cooldown_ticks
        self.max_ticks_to_confirm_heal = max_ticks_to_confirm_heal

        self.was_underground: bool = False
        self.surface_exit_authorized: bool = False
        self.last_position: tuple[int, int, int] | None = None

        self._last_eat_tick: int = -100
        self._pending_heal_from_tick: int | None = None
        self._hp_at_eat: int | None = None

    def authorize_surface_exit(self) -> None:
        self.surface_exit_authorized = True

    def evaluate_pulse(
        self,
        *,
        game_tick: int,
        hp_current: int,
        hp_max: int,
        run_energy: int,
        run_enabled: bool,
        under_attack: bool,
        position: tuple[int, int, int],
        inventory_items: Sequence[dict[str, Any]],
    ) -> SafetyDecision:
        """Pure non-blocking tick evaluation (ZERO I/O, ZERO sleep)."""
        # 1. Immediate death check (T23)
        if hp_current <= 0:
            return SafetyDecision(
                verdict=SafetyVerdict.PLAYER_DEAD_QUARANTINE,
                in_danger=True,
                preserve_movement=False,
                halt_session=True,
                detail="HP reached 0; halting immediately under DEATH_QUARANTINE",
            )

        # 2. Spatial displacement / unexpected Lumbridge respawn guard
        x, y, plane = position
        if self.was_underground and y < 5000 and not self.surface_exit_authorized:
            return SafetyDecision(
                verdict=SafetyVerdict.UNEXPECTED_DISPLACEMENT_HALT,
                in_danger=True,
                preserve_movement=False,
                halt_session=True,
                detail=f"Unexpected underground->surface transition to {position} without ladder authorization",
            )
        if self.last_position is not None and not self.surface_exit_authorized:
            ox, oy, _ = self.last_position
            if y >= 5000 and oy >= 5000 and max(abs(x - ox), abs(y - oy)) > 256:
                return SafetyDecision(
                    verdict=SafetyVerdict.UNEXPECTED_DISPLACEMENT_HALT,
                    in_danger=True,
                    preserve_movement=False,
                    halt_session=True,
                    detail=f"Unexpected large dungeon jump {self.last_position} -> {position}",
                )
        if y >= 5000:
            self.was_underground = True
            self.surface_exit_authorized = False
        self.last_position = position

        # 3. Reactive next-tick confirmation of a previously pipelined Eat action!
        if self._pending_heal_from_tick is not None and self._hp_at_eat is not None:
            if hp_current > self._hp_at_eat or hp_current >= hp_max:
                # Heal confirmed on a subsequent game tick without ever pausing movement!
                self._pending_heal_from_tick = None
                self._hp_at_eat = None
            elif (game_tick - self._pending_heal_from_tick) >= self.max_ticks_to_confirm_heal:
                self._pending_heal_from_tick = None
                self._hp_at_eat = None
                if hp_current <= self.eat_hp_threshold:
                    return SafetyDecision(
                        verdict=SafetyVerdict.EAT_UNCONFIRMED_EVACUATE,
                        in_danger=True,
                        preserve_movement=False,
                        halt_session=True,
                        detail="No HP increase observed after max game-tick confirmation window",
                    )

        low_hp = hp_current <= self.eat_hp_threshold
        in_danger = low_hp or under_attack
        should_enable_run = (not run_enabled) and (
            run_energy >= (10 if in_danger else self.run_energy_min_threshold)
        )

        # 4. If HP is above eat threshold, keep moving smoothly (toggling run non-blockingly if needed)
        if not low_hp:
            return SafetyDecision(
                verdict=SafetyVerdict.NOMINAL,
                in_danger=in_danger,
                preserve_movement=True,
                halt_session=False,
                enable_run=should_enable_run,
                detail="Vitals nominal",
            )

        # 5. Low HP: if still within OSRS 3-tick eat cooldown, DO NOT sleep or spam-click food — keep moving!
        if (game_tick - self._last_eat_tick) < self.eat_cooldown_ticks:
            return SafetyDecision(
                verdict=SafetyVerdict.AWAITING_NEXT_TICK_HEAL,
                in_danger=True,
                preserve_movement=True,
                halt_session=False,
                enable_run=should_enable_run,
                detail=f"Within {self.eat_cooldown_ticks}-tick OSRS eat window; preserving movement without blocking",
            )

        # 6. Select verified food item from inventory
        food_item = next(
            (
                it
                for it in inventory_items
                if str(it.get("name", "")).strip().casefold() in VERIFIED_EDIBLE_FOOD_NAMES
                and int(it.get("quantity", 0)) > 0
            ),
            None,
        )
        if food_item is None:
            return SafetyDecision(
                verdict=SafetyVerdict.EMERGENCY_NO_FOOD_EVACUATE,
                in_danger=True,
                preserve_movement=False,
                halt_session=True,
                detail=f"HP={hp_current} <= {self.eat_hp_threshold} with zero verified food remaining",
            )

        food_id = int(food_item["item_id"])
        self._last_eat_tick = game_tick
        self._pending_heal_from_tick = game_tick
        self._hp_at_eat = hp_current

        return SafetyDecision(
            verdict=SafetyVerdict.PIPELINED_EAT_AND_MOVE,
            in_danger=True,
            preserve_movement=True,
            halt_session=False,
            eat_food_item_id=food_id,
            enable_run=should_enable_run,
            detail=f"Pipelined non-blocking Eat (item_id={food_id}) at tick={game_tick}; verifying on next tick",
        )
