"""
Categories, items, and units table access.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List

from db.database import get_db


@dataclass
class Category:
    id: int
    name: str
    sort_order: int


@dataclass
class Item:
    id: int
    category_id: int
    category_name: str
    name: str
    unit_type: str = "шт."
    allowed_units: List[str] = field(default_factory=list)
    is_available: bool = True


@dataclass
class Unit:
    id: int
    name: str


# ── Category — Read ────────────────────────────────────────────────────────

def get_all_categories() -> List[Category]:
    rows = get_db().execute(
        "SELECT id, name, sort_order FROM categories ORDER BY sort_order, id"
    ).fetchall()
    return [Category(id=r["id"], name=r["name"], sort_order=r["sort_order"]) for r in rows]


def get_category_by_id(cat_id: int) -> Category | None:
    row = get_db().execute(
        "SELECT id, name, sort_order FROM categories WHERE id = ?", (cat_id,)
    ).fetchone()
    return Category(id=row["id"], name=row["name"], sort_order=row["sort_order"]) if row else None


# ── Item — Read ────────────────────────────────────────────────────────────

def _parse_allowed_units(raw: str, unit_type: str) -> List[str]:
    """Parse the allowed_units JSON column; fall back to [unit_type] if empty."""
    try:
        parsed = json.loads(raw) if raw else []
        return parsed if parsed else [unit_type or "шт."]
    except Exception:
        return [unit_type or "шт."]


def get_all_items_by_category(category_id: int, admin: bool = False) -> List[Item]:
    """Return items for a category. admin=True includes unavailable items."""
    avail_clause = "" if admin else "AND i.is_available = 1"
    rows = get_db().execute(
        f"""
        SELECT i.id, i.category_id, c.name AS category_name, i.name,
               i.unit_type, i.allowed_units, i.is_available
        FROM items i
        JOIN categories c ON c.id = i.category_id
        WHERE i.category_id = ? {avail_clause}
        ORDER BY i.sort_order, i.id
        """,
        (category_id,),
    ).fetchall()
    return [
        Item(
            id=r["id"], category_id=r["category_id"],
            category_name=r["category_name"], name=r["name"],
            unit_type=r["unit_type"] or "шт.",
            allowed_units=_parse_allowed_units(r["allowed_units"], r["unit_type"]),
            is_available=bool(r["is_available"]),
        )
        for r in rows
    ]


def get_item_by_id(item_id: int) -> Item | None:
    row = get_db().execute(
        """
        SELECT i.id, i.category_id, c.name AS category_name, i.name,
               i.unit_type, i.allowed_units, i.is_available
        FROM items i
        JOIN categories c ON c.id = i.category_id
        WHERE i.id = ?
        """,
        (item_id,),
    ).fetchone()
    if not row:
        return None
    return Item(
        id=row["id"], category_id=row["category_id"],
        category_name=row["category_name"], name=row["name"],
        unit_type=row["unit_type"] or "шт.",
        allowed_units=_parse_allowed_units(row["allowed_units"], row["unit_type"]),
        is_available=bool(row["is_available"]),
    )


# ── Category — Admin CRUD ──────────────────────────────────────────────────

def add_category(name: str) -> int | None:
    db = get_db()
    try:
        cursor = db.execute(
            """
            INSERT INTO categories (name, sort_order)
            VALUES (?, (SELECT COALESCE(MAX(sort_order) + 1, 0) FROM categories))
            """,
            (name,),
        )
        db.commit()
        return cursor.lastrowid
    except Exception:
        return None


def rename_category(cat_id: int, name: str) -> bool:
    cursor = get_db().execute(
        "UPDATE categories SET name = ? WHERE id = ?", (name, cat_id)
    )
    get_db().commit()
    return cursor.rowcount > 0


def delete_category(cat_id: int) -> bool:
    db = get_db()
    db.execute("DELETE FROM items WHERE category_id = ?", (cat_id,))
    cursor = db.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
    db.commit()
    return cursor.rowcount > 0


# ── Item — Admin CRUD ──────────────────────────────────────────────────────

def add_item(
    category_id: int,
    name: str,
    unit_type: str = "шт.",
    allowed_units: List[str] | None = None,
) -> int | None:
    if not allowed_units:
        allowed_units = [unit_type]
    units_json = json.dumps(allowed_units, ensure_ascii=False)
    db = get_db()
    try:
        cursor = db.execute(
            """
            INSERT INTO items (category_id, name, sort_order, unit_type, allowed_units)
            VALUES (?, ?, (
                SELECT COALESCE(MAX(sort_order) + 1, 0)
                FROM items WHERE category_id = ?
            ), ?, ?)
            """,
            (category_id, name, category_id, unit_type, units_json),
        )
        db.commit()
        return cursor.lastrowid
    except Exception:
        return None


def rename_item(item_id: int, name: str) -> bool:
    cursor = get_db().execute(
        "UPDATE items SET name = ? WHERE id = ?", (name, item_id)
    )
    get_db().commit()
    return cursor.rowcount > 0


def update_item_unit(item_id: int, unit_type: str) -> bool:
    cursor = get_db().execute(
        "UPDATE items SET unit_type = ? WHERE id = ?", (unit_type, item_id)
    )
    get_db().commit()
    return cursor.rowcount > 0


def set_item_allowed_units(item_id: int, units: List[str]) -> bool:
    """Replace the full allowed_units list; also updates unit_type to the first unit."""
    if not units:
        return False
    units_json = json.dumps(units, ensure_ascii=False)
    cursor = get_db().execute(
        "UPDATE items SET allowed_units = ?, unit_type = ? WHERE id = ?",
        (units_json, units[0], item_id),
    )
    get_db().commit()
    return cursor.rowcount > 0


def delete_item(item_id: int) -> bool:
    cursor = get_db().execute("DELETE FROM items WHERE id = ?", (item_id,))
    get_db().commit()
    return cursor.rowcount > 0


def toggle_item_availability(item_id: int) -> bool | None:
    """Toggle is_available flag. Returns new state (True/False) or None if not found."""
    row = get_db().execute(
        "SELECT is_available FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    if not row:
        return None
    new_val = 0 if row["is_available"] else 1
    get_db().execute(
        "UPDATE items SET is_available = ? WHERE id = ?", (new_val, item_id)
    )
    get_db().commit()
    return bool(new_val)


# ── Units — Read & Admin CRUD ──────────────────────────────────────────────

def get_all_units() -> List[Unit]:
    rows = get_db().execute("SELECT id, name FROM units ORDER BY id").fetchall()
    return [Unit(id=r["id"], name=r["name"]) for r in rows]


def get_unit_by_id(unit_id: int) -> Unit | None:
    row = get_db().execute("SELECT id, name FROM units WHERE id = ?", (unit_id,)).fetchone()
    return Unit(id=row["id"], name=row["name"]) if row else None


def add_unit(name: str) -> int | None:
    db = get_db()
    try:
        cursor = db.execute("INSERT INTO units (name) VALUES (?)", (name,))
        db.commit()
        return cursor.lastrowid
    except Exception:
        return None


def rename_unit(unit_id: int, name: str) -> bool:
    cursor = get_db().execute(
        "UPDATE units SET name = ? WHERE id = ?", (name, unit_id)
    )
    get_db().commit()
    return cursor.rowcount > 0


def delete_unit(unit_id: int) -> bool:
    cursor = get_db().execute("DELETE FROM units WHERE id = ?", (unit_id,))
    get_db().commit()
    return cursor.rowcount > 0


# ── Order-day schedule ─────────────────────────────────────────────────────

def get_category_order_days(cat_id: int) -> int:
    row = get_db().execute(
        "SELECT order_days FROM categories WHERE id = ?", (cat_id,)
    ).fetchone()
    return int(row["order_days"]) if row else 127


def toggle_category_day(cat_id: int, day_index: int) -> int:
    current = get_category_order_days(cat_id)
    new_mask = current ^ (1 << day_index)
    get_db().execute(
        "UPDATE categories SET order_days = ? WHERE id = ?", (new_mask, cat_id)
    )
    get_db().commit()
    return new_mask


def is_category_active_today(cat_id: int) -> bool:
    from datetime import datetime
    day_index = datetime.now().weekday()
    return bool(get_category_order_days(cat_id) & (1 << day_index))


def get_active_categories_today() -> List[Category]:
    from datetime import datetime
    bit = 1 << datetime.now().weekday()
    rows = get_db().execute(
        """
        SELECT id, name, sort_order FROM categories
        WHERE (order_days & ?) != 0
        ORDER BY sort_order, id
        """,
        (bit,),
    ).fetchall()
    return [Category(id=r["id"], name=r["name"], sort_order=r["sort_order"]) for r in rows]
