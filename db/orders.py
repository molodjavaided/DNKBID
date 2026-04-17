"""
Orders table access — mirrors src/db/orders.ts.
All SQL for order reads/writes lives here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, List

from db.database import get_db


# ── Types ──────────────────────────────────────────────────────────────────

@dataclass
class OrderItem:
    id: int
    order_id: int
    item_key: str
    item_name: str
    category_name: str
    quantity: float
    unit: str
    is_urgent: bool = False


@dataclass
class OrderHeader:
    id: int
    location_id: int
    location_name: str
    tg_message_id: int
    tg_chat_id: int
    tg_user_id: int | None
    tg_user_name: str | None
    status: str
    created_at: str
    reported_at: str | None


@dataclass
class FullOrder(OrderHeader):
    items: List[OrderItem] = field(default_factory=list)


@dataclass
class CartLineInput:
    item_key: str
    item_name: str
    category_name: str
    quantity: float
    unit: str
    is_urgent: bool = False


# ── Helpers ────────────────────────────────────────────────────────────────

def _local_today_start_iso() -> str:
    """Return today's midnight (Asia/Yekaterinburg, UTC+5) as UTC ISO string."""
    now_utc = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        local_tz = ZoneInfo("Asia/Yekaterinburg")
        now_local = now_utc.astimezone(local_tz)
        today_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = today_local.astimezone(timezone.utc)
    except ImportError:
        local_offset = timedelta(hours=5)
        now_local = now_utc + local_offset
        today_local_naive = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        today_start_utc = (today_local_naive - local_offset).replace(tzinfo=timezone.utc)
    return today_start_utc.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _item_rows_to_list(item_rows) -> dict[int, List[OrderItem]]:
    result: dict[int, List[OrderItem]] = {}
    for r in item_rows:
        oi = OrderItem(
            id=r["id"], order_id=r["order_id"], item_key=r["item_key"],
            item_name=r["item_name"], category_name=r["category_name"],
            quantity=r["quantity"], unit=r["unit"],
            is_urgent=bool(r["is_urgent"]) if "is_urgent" in r.keys() else False,
        )
        result.setdefault(oi.order_id, []).append(oi)
    return result


def _row_to_full_order(r, items: List[OrderItem]) -> FullOrder:
    return FullOrder(
        id=r["id"], location_id=r["location_id"],
        location_name=r["location_name"], tg_message_id=r["tg_message_id"],
        tg_chat_id=r["tg_chat_id"], tg_user_id=r["tg_user_id"],
        tg_user_name=r["tg_user_name"], status=r["status"],
        created_at=r["created_at"], reported_at=r["reported_at"],
        items=items,
    )


# ── Write ──────────────────────────────────────────────────────────────────

def create_order(
    *,
    location_id: int,
    tg_message_id: int,
    tg_chat_id: int,
    tg_user_id: int | None = None,
    tg_user_name: str | None = None,
    cart: List[CartLineInput],
) -> int | None:
    """
    Inserts one order header + all cart line items atomically.
    Returns the new order id, or None if message_id was already used (dedup).
    """
    n = len(cart)
    suffix = "я" if n == 1 else "и" if n < 5 else "й"
    summary = f"{n} позици{suffix}"

    db = get_db()
    try:
        cursor = db.execute(
            """
            INSERT INTO orders
                (location_id, tg_message_id, tg_chat_id, tg_user_id, tg_user_name,
                 item_name, quantity)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (location_id, tg_message_id, tg_chat_id, tg_user_id, tg_user_name,
             summary, n),
        )
        order_id = cursor.lastrowid
        db.executemany(
            """
            INSERT INTO order_items
                (order_id, item_key, item_name, category_name, quantity, unit, is_urgent)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (order_id, line.item_key, line.item_name,
                 line.category_name, line.quantity, line.unit, int(line.is_urgent))
                for line in cart
            ],
        )
        db.commit()
        return order_id
    except Exception as e:
        db.rollback()
        if "UNIQUE" in str(e):
            return None  # duplicate message
        raise


def update_order_items(order_id: int, cart: List[CartLineInput]) -> bool:
    """Replace all line items of an existing order with a new cart."""
    n = len(cart)
    suffix = "я" if n == 1 else "и" if n < 5 else "й"
    summary = f"{n} позици{suffix}"

    db = get_db()
    try:
        db.execute("DELETE FROM order_items WHERE order_id = ?", (order_id,))
        db.execute(
            """
            UPDATE orders
            SET item_name  = ?,
                quantity   = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')
            WHERE id = ?
            """,
            (summary, n, order_id),
        )
        db.executemany(
            """
            INSERT INTO order_items
                (order_id, item_key, item_name, category_name, quantity, unit, is_urgent)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (order_id, line.item_key, line.item_name,
                 line.category_name, line.quantity, line.unit, int(line.is_urgent))
                for line in cart
            ],
        )
        db.commit()
        return True
    except Exception:
        db.rollback()
        return False


# ── Read ───────────────────────────────────────────────────────────────────

def get_order_by_id(order_id: int) -> FullOrder | None:
    db = get_db()
    row = db.execute(
        """
        SELECT o.id, o.location_id, o.tg_message_id, o.tg_chat_id,
               o.tg_user_id, o.tg_user_name, o.status, o.created_at,
               o.reported_at, l.name AS location_name
        FROM orders o
        JOIN locations l ON l.id = o.location_id
        WHERE o.id = ?
        """,
        (order_id,),
    ).fetchone()
    if not row:
        return None
    item_rows = db.execute(
        "SELECT * FROM order_items WHERE order_id = ? ORDER BY id",
        (order_id,),
    ).fetchall()
    items = [
        OrderItem(
            id=r["id"], order_id=r["order_id"], item_key=r["item_key"],
            item_name=r["item_name"], category_name=r["category_name"],
            quantity=r["quantity"], unit=r["unit"],
        )
        for r in item_rows
    ]
    return _row_to_full_order(row, items)


def get_user_orders_today(tg_user_id: int) -> List[FullOrder]:
    """Return all orders submitted today by the given user (newest first)."""
    today_start_iso = _local_today_start_iso()
    db = get_db()
    header_rows = db.execute(
        """
        SELECT o.id, o.location_id, o.tg_message_id, o.tg_chat_id,
               o.tg_user_id, o.tg_user_name, o.status, o.created_at,
               o.reported_at, l.name AS location_name
        FROM orders o
        JOIN locations l ON l.id = o.location_id
        WHERE o.tg_user_id = ? AND o.created_at >= ?
        ORDER BY o.id DESC
        """,
        (tg_user_id, today_start_iso),
    ).fetchall()
    if not header_rows:
        return []
    order_ids = [r["id"] for r in header_rows]
    placeholders = ",".join("?" * len(order_ids))
    item_rows = db.execute(
        f"SELECT * FROM order_items WHERE order_id IN ({placeholders}) ORDER BY order_id, id",
        order_ids,
    ).fetchall()
    items_by_order = _item_rows_to_list(item_rows)
    return [_row_to_full_order(r, items_by_order.get(r["id"], [])) for r in header_rows]


def get_unreported_orders() -> List[FullOrder]:
    """Returns all unreported orders (header + items) for report generation."""
    db = get_db()
    header_rows = db.execute(
        """
        SELECT o.id, o.location_id, o.tg_message_id, o.tg_chat_id,
               o.tg_user_id, o.tg_user_name, o.status, o.created_at,
               o.reported_at, l.name AS location_name
        FROM orders o
        JOIN locations l ON l.id = o.location_id
        WHERE o.reported_at IS NULL
        ORDER BY o.location_id, o.id
        """
    ).fetchall()
    if not header_rows:
        return []
    order_ids = [r["id"] for r in header_rows]
    placeholders = ",".join("?" * len(order_ids))
    item_rows = db.execute(
        f"SELECT * FROM order_items WHERE order_id IN ({placeholders}) ORDER BY order_id, id",
        order_ids,
    ).fetchall()
    items_by_order = _item_rows_to_list(item_rows)
    return [_row_to_full_order(r, items_by_order.get(r["id"], [])) for r in header_rows]


def get_all_orders_today() -> List[FullOrder]:
    """Returns all orders submitted today (all locations), newest per location first."""
    today_start_iso = _local_today_start_iso()
    db = get_db()
    header_rows = db.execute(
        """
        SELECT o.id, o.location_id, o.tg_message_id, o.tg_chat_id,
               o.tg_user_id, o.tg_user_name, o.status, o.created_at,
               o.reported_at, l.name AS location_name
        FROM orders o
        JOIN locations l ON l.id = o.location_id
        WHERE o.created_at >= ?
        ORDER BY o.location_id, o.id
        """,
        (today_start_iso,),
    ).fetchall()
    if not header_rows:
        return []
    order_ids = [r["id"] for r in header_rows]
    placeholders = ",".join("?" * len(order_ids))
    item_rows = db.execute(
        f"SELECT * FROM order_items WHERE order_id IN ({placeholders}) ORDER BY order_id, id",
        order_ids,
    ).fetchall()
    items_by_order = _item_rows_to_list(item_rows)
    return [_row_to_full_order(r, items_by_order.get(r["id"], [])) for r in header_rows]


def mark_orders_reported(order_ids: List[int]) -> None:
    if not order_ids:
        return
    placeholders = ",".join("?" * len(order_ids))
    get_db().execute(
        f"""
        UPDATE orders
        SET reported_at = strftime('%Y-%m-%dT%H:%M:%fZ','now'),
            updated_at  = strftime('%Y-%m-%dT%H:%M:%fZ','now')
        WHERE id IN ({placeholders})
        """,
        order_ids,
    )
    get_db().commit()


# ── Draft carts (persistent FSM state) ────────────────────────────────────

def save_draft_cart(tg_user_id: int, tg_chat_id: int, data: dict[str, Any]) -> None:
    """Upsert the current FSM cart data to the database."""
    get_db().execute(
        """
        INSERT INTO draft_carts (tg_user_id, tg_chat_id, data, updated_at)
        VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        ON CONFLICT(tg_user_id, tg_chat_id) DO UPDATE SET
            data       = excluded.data,
            updated_at = excluded.updated_at
        """,
        (tg_user_id, tg_chat_id, json.dumps(data, ensure_ascii=False)),
    )
    get_db().commit()


def load_draft_cart(tg_user_id: int, tg_chat_id: int) -> dict[str, Any] | None:
    """Load saved draft cart, or None if none exists."""
    row = get_db().execute(
        "SELECT data FROM draft_carts WHERE tg_user_id = ? AND tg_chat_id = ?",
        (tg_user_id, tg_chat_id),
    ).fetchone()
    return json.loads(row["data"]) if row else None


def delete_draft_cart(tg_user_id: int, tg_chat_id: int) -> None:
    """Remove saved draft cart after submit or cancel."""
    get_db().execute(
        "DELETE FROM draft_carts WHERE tg_user_id = ? AND tg_chat_id = ?",
        (tg_user_id, tg_chat_id),
    )
    get_db().commit()


# ── Daily status ───────────────────────────────────────────────────────────

@dataclass
class LocationOrderStatus:
    location_id: int
    location_name: str
    missing_categories: List[str]


def get_location_order_status_today() -> List[LocationOrderStatus]:
    """
    For every location, returns which mandatory categories are missing today.
    Only categories scheduled for today (order_days bitmask) are included.
    'Today' is computed as midnight–now in Asia/Yekaterinburg time.
    """
    today_start_iso = _local_today_start_iso()
    # weekday(): Mon=0 … Sun=6, matches order_days bitmask bit positions
    now_utc = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        local_now = now_utc.astimezone(ZoneInfo("Asia/Yekaterinburg"))
    except ImportError:
        local_now = now_utc + timedelta(hours=5)
    today_bit = 1 << local_now.weekday()

    db = get_db()
    all_categories = [
        r["name"] for r in db.execute(
            "SELECT name, order_days FROM categories ORDER BY sort_order, id"
        ).fetchall()
        if (r["order_days"] or 127) & today_bit
    ]
    locations = db.execute(
        "SELECT id, name FROM locations ORDER BY id"
    ).fetchall()

    covered_rows = db.execute(
        """
        SELECT o.location_id, oi.category_name
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.id
        WHERE o.created_at >= ?
        GROUP BY o.location_id, oi.category_name
        """,
        (today_start_iso,),
    ).fetchall()

    covered: dict[int, set[str]] = {}
    for r in covered_rows:
        covered.setdefault(r["location_id"], set()).add(r["category_name"])

    return [
        LocationOrderStatus(
            location_id=loc["id"],
            location_name=loc["name"],
            missing_categories=[
                cat for cat in all_categories
                if cat not in covered.get(loc["id"], set())
            ],
        )
        for loc in locations
    ]


# ── Quick repeat & smart statistics ───────────────────────────────────────

def get_last_order_for_location(location_id: int, tg_user_id: int) -> FullOrder | None:
    """Return the most recent order by this user for this location, or None."""
    db = get_db()
    row = db.execute(
        """
        SELECT o.id, o.location_id, o.tg_message_id, o.tg_chat_id,
               o.tg_user_id, o.tg_user_name, o.status, o.created_at,
               o.reported_at, l.name AS location_name
        FROM orders o
        JOIN locations l ON l.id = o.location_id
        WHERE o.location_id = ? AND o.tg_user_id = ?
        ORDER BY o.id DESC
        LIMIT 1
        """,
        (location_id, tg_user_id),
    ).fetchone()
    if not row:
        return None
    item_rows = db.execute(
        "SELECT * FROM order_items WHERE order_id = ? ORDER BY id",
        (row["id"],),
    ).fetchall()
    items = [
        OrderItem(
            id=r["id"], order_id=r["order_id"], item_key=r["item_key"],
            item_name=r["item_name"], category_name=r["category_name"],
            quantity=r["quantity"], unit=r["unit"],
            is_urgent=bool(r["is_urgent"]) if "is_urgent" in r.keys() else False,
        )
        for r in item_rows
    ]
    return _row_to_full_order(row, items)


def get_location_avg_orders(location_id: int, last_n: int = 10) -> List[dict]:
    """
    Compute average quantities per (item_key, unit) over the last `last_n`
    orders for a location. Returns rows sorted by category then item name.
    Only includes items that appear in at least one of those orders.
    """
    rows = get_db().execute(
        """
        SELECT
            oi.item_key,
            oi.item_name,
            oi.category_name,
            oi.unit,
            ROUND(AVG(oi.quantity), 1) AS avg_qty,
            COUNT(DISTINCT o.id)       AS order_count
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        WHERE o.location_id = ?
          AND o.id IN (
              SELECT id FROM orders
              WHERE location_id = ?
              ORDER BY id DESC
              LIMIT ?
          )
        GROUP BY oi.item_key, oi.category_name, oi.item_name, oi.unit
        ORDER BY oi.category_name, oi.item_name
        """,
        (location_id, location_id, last_n),
    ).fetchall()
    return [dict(r) for r in rows]
