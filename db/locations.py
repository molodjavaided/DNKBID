"""
Locations table access — mirrors src/db/locations.ts.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List
from db.database import get_db


@dataclass
class Location:
    id: int
    name: str


def get_all_locations() -> List[Location]:
    rows = get_db().execute(
        "SELECT id, name FROM locations ORDER BY id"
    ).fetchall()
    return [Location(id=r["id"], name=r["name"]) for r in rows]


def get_location_by_id(loc_id: int) -> Location | None:
    row = get_db().execute(
        "SELECT id, name FROM locations WHERE id = ?", (loc_id,)
    ).fetchone()
    return Location(id=row["id"], name=row["name"]) if row else None


def add_location(name: str) -> int | None:
    try:
        cursor = get_db().execute(
            "INSERT INTO locations (name) VALUES (?)", (name,)
        )
        get_db().commit()
        return cursor.lastrowid
    except Exception:
        return None


def rename_location(loc_id: int, name: str) -> bool:
    cursor = get_db().execute(
        "UPDATE locations SET name = ? WHERE id = ?", (name, loc_id)
    )
    get_db().commit()
    return cursor.rowcount > 0


def delete_location(loc_id: int) -> bool:
    cursor = get_db().execute(
        "DELETE FROM locations WHERE id = ?", (loc_id,)
    )
    get_db().commit()
    return cursor.rowcount > 0
