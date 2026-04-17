"""
Chats table access — mirrors src/db/chats.ts.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import List
from db.database import get_db


@dataclass
class Chat:
    id: int
    title: str | None
    is_active: int


def upsert_chat(chat_id: int, title: str | None) -> None:
    get_db().execute(
        """
        INSERT INTO chats(id, title)
        VALUES (?, ?)
        ON CONFLICT(id) DO UPDATE
            SET title = excluded.title,
                is_active = 1
        """,
        (chat_id, title),
    )
    get_db().commit()


def get_active_chats() -> List[Chat]:
    rows = get_db().execute(
        "SELECT id, title, is_active FROM chats WHERE is_active = 1"
    ).fetchall()
    return [Chat(id=r["id"], title=r["title"], is_active=r["is_active"]) for r in rows]


def deactivate_chat(chat_id: int) -> None:
    get_db().execute("UPDATE chats SET is_active = 0 WHERE id = ?", (chat_id,))
    get_db().commit()
