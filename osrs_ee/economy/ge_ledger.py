"""Default-Deny Sell Policy, Active Recipe Input Lock, and GE Order State Machine (PR-10 / AUD-24..26 / T24)."""

from __future__ import annotations

import sqlite3
import threading
import uuid
from enum import Enum
from pathlib import Path
from typing import Any

GE_BUY_LIMIT_WINDOW_SEC = 4 * 3600  # Official OSRS 4-hour Grand Exchange buy limit window
MAX_GE_TAX_PER_UNIT_GP = 5_000_000

# Canonical OSRS item name -> item_id map for recipe tool & input locking
OSRS_ITEM_NAME_TO_ID: dict[str, int] = {
    "Coins": 995,
    "Bronze axe": 1351,
    "Iron axe": 1349,
    "Steel axe": 1353,
    "Mithril axe": 1355,
    "Adamant axe": 1357,
    "Rune axe": 1359,
    "Bronze pickaxe": 1265,
    "Iron pickaxe": 1267,
    "Mithril pickaxe": 1273,
    "Adamant pickaxe": 1271,
    "Rune pickaxe": 1275,
    "Fly fishing rod": 309,
    "Feather": 314,
    "Logs": 1511,
    "Willow logs": 1519,
    "Raw trout": 335,
    "Raw salmon": 331,
    "Iron ore": 440,
    "Copper ore": 436,
    "Bones": 526,
}


class SellPolicyViolationError(PermissionError):
    """Raised when attempting to liquidate unwhitelisted, equipped, quest, or active-recipe items."""


class GEOrderState(str, Enum):
    CREATED = "CREATED"
    SUBMITTED = "SUBMITTED"
    PARTIAL_FILL = "PARTIAL_FILL"
    FILLED = "FILLED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    COLLECTED = "COLLECTED"


def calculate_osrs_ge_tax_per_unit(unit_sale_price_gp: int) -> int:
    """Official OSRS Grand Exchange 2% seller tax formula:
    - Items sold below 100 gp/unit have 0 gp tax.
    - Otherwise floor(price * 0.02), capped at 5,000,000 gp per item.
    """
    if unit_sale_price_gp < 100:
        return 0
    return min(MAX_GE_TAX_PER_UNIT_GP, (unit_sale_price_gp * 2) // 100)


class DefaultDenySellPolicy:
    """Blocks selling anything outside the explicit whitelist OR locked by gear/quest/active recipe (T24)."""

    def __init__(
        self,
        *,
        approved_sell_whitelist: set[int],
        equipped_item_ids: set[int] | None = None,
        quest_protected_item_ids: set[int] | None = None,
        untradeable_item_ids: set[int] | None = None,
    ) -> None:
        self.approved_sell_whitelist = set(approved_sell_whitelist)
        self.equipped_item_ids = set(equipped_item_ids or set())
        self.quest_protected_item_ids = set(quest_protected_item_ids or {995})  # 995 = Coins!
        self.untradeable_item_ids = set(untradeable_item_ids or set())

    def assert_can_sell(
        self, item_id: int, *, active_recipe: dict[str, Any] | None = None
    ) -> None:
        if item_id in self.untradeable_item_ids:
            raise SellPolicyViolationError(f"Item {item_id} is untradeable")
        if item_id in self.quest_protected_item_ids:
            raise SellPolicyViolationError(f"Item {item_id} is quest/currency protected")
        if item_id in self.equipped_item_ids:
            raise SellPolicyViolationError(f"Item {item_id} is currently equipped gear")
        if active_recipe is not None:
            locked_ids = set(active_recipe.get("required_item_ids", ()))
            for tool_name in active_recipe.get("tool_candidates", ()):
                if tool_name in OSRS_ITEM_NAME_TO_ID:
                    locked_ids.add(OSRS_ITEM_NAME_TO_ID[tool_name])
            if item_id in locked_ids:
                raise SellPolicyViolationError(
                    f"ACTIVE_RECIPE_INPUT_LOCK: Item {item_id} is required by active recipe {active_recipe['recipe_id']!r}"
                )
        if item_id not in self.approved_sell_whitelist:
            raise SellPolicyViolationError(
                f"DEFAULT_DENY_SELL: Item {item_id} is not in approved_sell_whitelist"
            )


class GEOrderLedger:
    """SQLite-backed Grand Exchange ledger tracking slots 0..7, buy limits, 2% tax, and realized vs unrealized GP."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS ge_orders (
                    order_id TEXT PRIMARY KEY,
                    account_id TEXT NOT NULL,
                    ge_slot INTEGER NOT NULL CHECK(ge_slot BETWEEN 0 AND 7),
                    side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
                    item_id INTEGER NOT NULL,
                    total_qty INTEGER NOT NULL CHECK(total_qty > 0),
                    limit_price_gp INTEGER NOT NULL CHECK(limit_price_gp > 0),
                    filled_qty INTEGER NOT NULL DEFAULT 0,
                    gross_transacted_gp INTEGER NOT NULL DEFAULT 0,
                    tax_paid_gp INTEGER NOT NULL DEFAULT 0,
                    state TEXT NOT NULL,
                    created_epoch_sec REAL NOT NULL
                );
                """
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def create_sell_order(
        self,
        *,
        account_id: str,
        ge_slot: int,
        item_id: int,
        qty: int,
        unit_price_gp: int,
        policy: DefaultDenySellPolicy,
        active_recipe: dict[str, Any] | None,
        now_epoch_sec: float,
    ) -> str:
        policy.assert_can_sell(item_id, active_recipe=active_recipe)
        return self._insert_order(
            account_id=account_id,
            ge_slot=ge_slot,
            side="SELL",
            item_id=item_id,
            qty=qty,
            unit_price_gp=unit_price_gp,
            now_epoch_sec=now_epoch_sec,
        )

    def create_buy_order(
        self,
        *,
        account_id: str,
        ge_slot: int,
        item_id: int,
        qty: int,
        unit_price_gp: int,
        four_hour_buy_limit: int,
        now_epoch_sec: float,
    ) -> str:
        with self._lock:
            window_start = now_epoch_sec - GE_BUY_LIMIT_WINDOW_SEC
            bought_recent = self._conn.execute(
                """
                SELECT COALESCE(SUM(filled_qty), 0) AS q
                  FROM ge_orders
                 WHERE account_id = ? AND side = 'BUY' AND item_id = ? AND created_epoch_sec >= ?
                """,
                (account_id, item_id, window_start),
            ).fetchone()["q"]
            if int(bought_recent) + qty > four_hour_buy_limit:
                raise SellPolicyViolationError(
                    f"GE_BUY_LIMIT_COOLDOWN: {bought_recent}+{qty} exceeds 4h limit {four_hour_buy_limit} for item {item_id}"
                )
        return self._insert_order(
            account_id=account_id,
            ge_slot=ge_slot,
            side="BUY",
            item_id=item_id,
            qty=qty,
            unit_price_gp=unit_price_gp,
            now_epoch_sec=now_epoch_sec,
        )

    def _insert_order(
        self,
        *,
        account_id: str,
        ge_slot: int,
        side: str,
        item_id: int,
        qty: int,
        unit_price_gp: int,
        now_epoch_sec: float,
    ) -> str:
        if not (0 <= ge_slot <= 7):
            raise ValueError(f"Invalid GE slot {ge_slot}; OSRS has slots 0..7")
        with self._lock:
            active_in_slot = self._conn.execute(
                """
                SELECT order_id FROM ge_orders
                 WHERE account_id = ? AND ge_slot = ? AND state != 'COLLECTED'
                """,
                (account_id, ge_slot),
            ).fetchone()
            if active_in_slot is not None:
                raise ValueError(
                    f"GE slot {ge_slot} occupied by uncollected order {active_in_slot['order_id']}"
                )
            order_id = f"ge_{uuid.uuid4().hex[:10]}"
            self._conn.execute(
                """
                INSERT INTO ge_orders
                    (order_id, account_id, ge_slot, side, item_id, total_qty, limit_price_gp, state, created_epoch_sec)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    account_id,
                    ge_slot,
                    side,
                    item_id,
                    qty,
                    unit_price_gp,
                    GEOrderState.SUBMITTED.value,
                    now_epoch_sec,
                ),
            )
            self._conn.commit()
            return order_id

    def record_fill_progress(
        self,
        *,
        order_id: str,
        newly_filled_qty: int,
        execution_unit_price_gp: int,
    ) -> dict[str, Any]:
        """Record partial or complete GE fill (remains UNREALIZED until `mark_collected`!)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM ge_orders WHERE order_id = ?", (order_id,)
            ).fetchone()
            if row is None:
                raise KeyError(order_id)
            new_filled = int(row["filled_qty"]) + newly_filled_qty
            if new_filled > int(row["total_qty"]):
                raise ValueError("Filled quantity exceeds total_qty")

            gross_delta = newly_filled_qty * execution_unit_price_gp
            tax_delta = (
                newly_filled_qty * calculate_osrs_ge_tax_per_unit(execution_unit_price_gp)
                if row["side"] == "SELL"
                else 0
            )
            new_state = (
                GEOrderState.FILLED.value
                if new_filled == int(row["total_qty"])
                else GEOrderState.PARTIAL_FILL.value
            )
            self._conn.execute(
                """
                UPDATE ge_orders
                   SET filled_qty = ?,
                       gross_transacted_gp = gross_transacted_gp + ?,
                       tax_paid_gp = tax_paid_gp + ?,
                       state = ?
                 WHERE order_id = ?
                """,
                (new_filled, gross_delta, tax_delta, new_state, order_id),
            )
            self._conn.commit()
            return dict(
                self._conn.execute("SELECT * FROM ge_orders WHERE order_id = ?", (order_id,)).fetchone()
            )

    def cancel_and_collect(self, order_id: str) -> dict[str, Any]:
        """Cancel an open/partial order and collect remaining items/coins to release the GE slot."""
        with self._lock:
            self._conn.execute(
                "UPDATE ge_orders SET state = ? WHERE order_id = ?",
                (GEOrderState.COLLECTED.value, order_id),
            )
            self._conn.commit()
            return dict(
                self._conn.execute("SELECT * FROM ge_orders WHERE order_id = ?", (order_id,)).fetchone()
            )

    def mark_collected(self, order_id: str) -> dict[str, Any]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM ge_orders WHERE order_id = ?", (order_id,)).fetchone()
            if row is None:
                raise KeyError(order_id)
            if row["state"] not in (GEOrderState.FILLED.value, GEOrderState.CANCELLED.value):
                raise ValueError(f"Cannot collect order in state {row['state']}")
            self._conn.execute(
                "UPDATE ge_orders SET state = ? WHERE order_id = ?",
                (GEOrderState.COLLECTED.value, order_id),
            )
            self._conn.commit()
            return dict(
                self._conn.execute("SELECT * FROM ge_orders WHERE order_id = ?", (order_id,)).fetchone()
            )

    def account_pnl_summary(self, account_id: str) -> dict[str, int]:
        """Strictly separate unrealized (uncollected) vs realized (collected) net GP after 2% GE tax."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM ge_orders WHERE account_id = ?", (account_id,)
            ).fetchall()

        realized_net_sell_gp = 0
        unrealized_net_sell_gp = 0
        total_tax_paid_gp = 0

        for r in rows:
            if r["side"] == "SELL":
                net = int(r["gross_transacted_gp"]) - int(r["tax_paid_gp"])
                total_tax_paid_gp += int(r["tax_paid_gp"])
                if r["state"] == GEOrderState.COLLECTED.value:
                    realized_net_sell_gp += net
                else:
                    unrealized_net_sell_gp += net

        return {
            "realized_net_sell_gp": realized_net_sell_gp,
            "unrealized_net_sell_gp": unrealized_net_sell_gp,
            "total_tax_paid_gp": total_tax_paid_gp,
        }
