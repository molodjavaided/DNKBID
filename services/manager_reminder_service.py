"""
Manager status-report reminder service.

Runs as a background asyncio task.  On each tick:
  1. Check if current time (Asia/Yekaterinburg) is within [start, deadline].
  2. Fetch today's per-location / per-category order status.
  3. If all locations have covered every category — skip (day is complete).
  4. Delete the previous status message from MANAGER_CHAT_ID (anti-flood).
  5. Send a fresh status report and store the new message_id.
  6. Sleep for the configured interval, then repeat.

Admin can change start, deadline, and interval live via the admin panel;
settings are re-read from the DB on every tick.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from config.env import MANAGER_CHAT_ID
from db.orders import get_location_order_status_today
from db.settings import (
    clear_mgr_reminder_last_msg_id,
    get_ignore_working_hours,
    get_mgr_reminder_deadline,
    get_mgr_reminder_interval_min,
    get_mgr_reminder_last_msg_id,
    get_mgr_reminder_start,
    set_mgr_reminder_last_msg_id,
)

log = logging.getLogger(__name__)


def _local_hhmm() -> str:
    """Current time as HH:MM in Asia/Yekaterinburg (UTC+5)."""
    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        local = now_utc.astimezone(ZoneInfo("Asia/Yekaterinburg"))
    except ImportError:
        from datetime import timedelta
        local = now_utc + timedelta(hours=5)
    return local.strftime("%H:%M")


def _within_window() -> bool:
    """True if current time is between mgr_reminder_start and mgr_reminder_deadline."""
    if get_ignore_working_hours():
        return True
    now = _local_hhmm()
    start    = get_mgr_reminder_start()
    deadline = get_mgr_reminder_deadline()
    if not start or not deadline:
        return True
    return start <= now <= deadline


def _build_status_report() -> str | None:
    """
    Build the HTML status report for MANAGER_CHAT_ID.
    Returns None if all locations have submitted all categories (nothing to report).
    """
    statuses = get_location_order_status_today()
    if not statuses:
        return None

    all_done = all(not s.missing_categories for s in statuses)
    if all_done:
        return None

    now = _local_hhmm()
    lines = [f"📊 <b>Статус заявок — {now}</b>\n"]
    for s in statuses:
        if not s.missing_categories:
            lines.append(f"✅ {s.location_name}")
        else:
            missing = "  ❌ " + "\n  ❌ ".join(s.missing_categories)
            lines.append(f"❌ <b>{s.location_name}</b>\n{missing}")

    interval = get_mgr_reminder_interval_min()
    lines.append(f"\n<i>Интервал: каждые {interval} мин. | до {get_mgr_reminder_deadline()}</i>")
    return "\n".join(lines)


async def send_manager_status_report(bot: Bot, force: bool = False) -> bool:
    """Send (or refresh) the status report in MANAGER_CHAT_ID. Set force=True to ignore time window.
    Returns True if report was sent, False otherwise."""
    if not force and not _within_window():
        log.debug("[MgrReminder] Outside window — skipping.")
        return False

    report = _build_status_report()
    if report is None:
        log.info("[MgrReminder] All locations done — nothing to report.")
        return False

    # Delete previous message
    old_id = get_mgr_reminder_last_msg_id()
    if old_id:
        try:
            await bot.delete_message(MANAGER_CHAT_ID, old_id)
        except Exception:
            pass  # already deleted or no permission
        clear_mgr_reminder_last_msg_id()

    # Send fresh report
    try:
        sent = await bot.send_message(MANAGER_CHAT_ID, report, parse_mode="HTML")
        set_mgr_reminder_last_msg_id(sent.message_id)
        log.info("[MgrReminder] Report sent, msg_id=%s", sent.message_id)
        return True
    except Exception as err:
        log.error("[MgrReminder] Failed to send: %s", err)
        return False


def start_manager_reminder_loop(bot: Bot) -> asyncio.Task:
    """
    Spawn a background asyncio task.  Interval is re-read from DB on every cycle
    so admin changes take effect without a restart.
    """
    async def _loop() -> None:
        log.info("[MgrReminder] Loop started.")
        while True:
            interval_s = get_mgr_reminder_interval_min() * 60
            await asyncio.sleep(interval_s)
            try:
                await send_manager_status_report(bot)
            except Exception as err:
                log.error("[MgrReminder] Unhandled error: %s", err)

    return asyncio.create_task(_loop())
