"""
Settings table access — mirrors src/db/settings.ts.
"""

from __future__ import annotations
from db.database import get_db


def get_setting(key: str) -> str | None:
    row = get_db().execute(
        "SELECT value FROM settings WHERE key = ?", (key,)
    ).fetchone()
    return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    get_db().execute(
        """
        INSERT INTO settings(key, value, updated_at)
        VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        ON CONFLICT(key) DO UPDATE
            SET value = excluded.value,
                updated_at = excluded.updated_at
        """,
        (key, value),
    )
    get_db().commit()


# ── Orders open/closed ─────────────────────────────────────────────────────

def is_orders_open() -> bool:
    return get_setting("orders_open") == "1"


def set_orders_open(open_: bool) -> None:
    set_setting("orders_open", "1" if open_ else "0")


# ── Deadline ───────────────────────────────────────────────────────────────

def get_deadline() -> str | None:
    v = get_setting("deadline_time")
    return v or None


def set_deadline(time: str) -> None:
    set_setting("deadline_time", time)


# ── Per-chat reminder message IDs ──────────────────────────────────────────

def get_reminder_message_id(chat_id: int) -> int | None:
    v = get_setting(f"reminder_msg_{chat_id}")
    return int(v) if v else None


def set_reminder_message_id(chat_id: int, message_id: int) -> None:
    set_setting(f"reminder_msg_{chat_id}", str(message_id))


def clear_reminder_message_id(chat_id: int) -> None:
    set_setting(f"reminder_msg_{chat_id}", "")


# ── Manager status-report reminder settings ────────────────────────────────

def get_mgr_reminder_start() -> str:
    return get_setting("mgr_reminder_start") or "08:00"

def set_mgr_reminder_start(t: str) -> None:
    set_setting("mgr_reminder_start", t)

def get_mgr_reminder_deadline() -> str:
    return get_setting("mgr_reminder_deadline") or "14:00"

def set_mgr_reminder_deadline(t: str) -> None:
    set_setting("mgr_reminder_deadline", t)

def get_mgr_reminder_interval_min() -> int:
    v = get_setting("mgr_reminder_interval_min")
    try:
        return max(1, int(v or "60"))
    except ValueError:
        return 60

def set_mgr_reminder_interval_min(minutes: int) -> None:
    set_setting("mgr_reminder_interval_min", str(max(1, minutes)))

def get_mgr_reminder_last_msg_id() -> int | None:
    v = get_setting("mgr_reminder_last_msg_id")
    return int(v) if v else None

def set_mgr_reminder_last_msg_id(msg_id: int) -> None:
    set_setting("mgr_reminder_last_msg_id", str(msg_id))

def clear_mgr_reminder_last_msg_id() -> None:
    set_setting("mgr_reminder_last_msg_id", "")


# ── Service intervals (configurable by admin) ──────────────────────────────

def get_reminder_interval_min() -> int:
    """Barista reminder interval in minutes (default 120)."""
    v = get_setting("reminder_interval_min")
    try:
        return max(1, int(v or "120"))
    except ValueError:
        return 120

def set_reminder_interval_min(minutes: int) -> None:
    set_setting("reminder_interval_min", str(max(1, minutes)))

def get_report_interval_min() -> int:
    """Manager report interval in minutes (default 60)."""
    v = get_setting("report_interval_min")
    try:
        return max(1, int(v or "60"))
    except ValueError:
        return 60

def set_report_interval_min(minutes: int) -> None:
    set_setting("report_interval_min", str(max(1, minutes)))

def get_ignore_working_hours() -> bool:
    """If true, reminders/reports ignore time restrictions (like TEST_MODE)."""
    return get_setting("ignore_working_hours") == "1"

def set_ignore_working_hours(ignore: bool) -> None:
    set_setting("ignore_working_hours", "1" if ignore else "0")

def get_reminder_work_start() -> str:
    """Start of working hours for reminders (default 08:00)."""
    return get_setting("reminder_work_start") or "08:00"

def set_reminder_work_start(time: str) -> None:
    set_setting("reminder_work_start", time)

def get_reminder_work_end() -> str:
    """End of working hours for reminders = deadline (configurable)."""
    return get_setting("reminder_work_end") or ""

def set_reminder_work_end(time: str) -> None:
    set_setting("reminder_work_end", time)
