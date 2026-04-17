"""
Manager reminder service.

Background loop that periodically refreshes the pinned dashboard
in MANAGER_CHAT_ID so it stays up-to-date even if no orders come in.
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot

from db.settings import get_deadline, get_mgr_reminder_interval_min, get_reminder_work_start
from services.dashboard_service import update_manager_dashboard

log = logging.getLogger(__name__)


def _local_hhmm() -> str:
    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        return now_utc.astimezone(ZoneInfo("Asia/Yekaterinburg")).strftime("%H:%M")
    except ImportError:
        from datetime import timedelta
        return (now_utc + timedelta(hours=5)).strftime("%H:%M")


def _within_window() -> bool:
    now = _local_hhmm()
    start    = get_reminder_work_start()
    deadline = get_deadline()
    if start and now < start:
        return False
    if deadline and now > deadline:
        return False
    return True


def start_manager_reminder_loop(bot: Bot) -> asyncio.Task:
    async def _loop() -> None:
        log.info("[MgrReminder] Loop started.")
        while True:
            interval_s = get_mgr_reminder_interval_min() * 60
            await asyncio.sleep(interval_s)
            if _within_window():
                try:
                    await update_manager_dashboard(bot)
                except Exception as err:
                    log.error("[MgrReminder] Unhandled error: %s", err)

    return asyncio.create_task(_loop())
