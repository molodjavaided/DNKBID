"""
ReminderService — mirrors src/services/reminder.service.ts.

On each tick:
  1. Skip if orders are closed.
  2. For every active chat, DELETE the previous reminder message (if stored).
  3. Send a new reminder and store its message_id.

Scheduling rules (mirror Node.js env.ts):
  TEST_MODE=true  → tick every REMINDER_INTERVAL_S (60 s), ignore working hours.
  TEST_MODE=false → tick every REMINDER_INTERVAL_S (default 2 h), skip outside 08:00–deadline.
"""

import asyncio
import logging
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from db.chats import get_active_chats
from db.settings import (
    get_deadline, is_orders_open,
    get_reminder_message_id, set_reminder_message_id, clear_reminder_message_id,
    get_reminder_work_start,
)
from config.env import BOT_USERNAME

log = logging.getLogger(__name__)


def _current_local_hhmm() -> str:
    """Returns current time as 'HH:MM' in Asia/Yekaterinburg."""
    from datetime import datetime, timezone
    now_utc = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        local_now = now_utc.astimezone(ZoneInfo("Asia/Yekaterinburg"))
    except ImportError:
        from datetime import timedelta
        local_now = now_utc + timedelta(hours=5)  # fallback: Yekaterinburg UTC+5
    return local_now.strftime("%H:%M")


def _is_within_working_hours() -> bool:
    now = _current_local_hhmm()
    work_start = get_reminder_work_start()
    if now < work_start:
        return False
    from db.settings import get_deadline
    work_end = get_deadline()
    if work_end and now > work_end:
        return False
    return True


def _build_order_status_text() -> str:
    from db.orders import get_location_order_status_today
    from db.database import get_db
    from datetime import datetime, timezone
    statuses = get_location_order_status_today()
    if not statuses:
        return ""
    now_utc = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        local_now = now_utc.astimezone(ZoneInfo("Asia/Yekaterinburg"))
    except ImportError:
        from datetime import timedelta
        local_now = now_utc + timedelta(hours=5)
    today_bit = 1 << local_now.weekday()
    all_cats = [
        r["name"] for r in get_db().execute(
            "SELECT name, order_days FROM categories ORDER BY sort_order, id"
        ).fetchall()
        if (r["order_days"] or 127) & today_bit
    ]
    if not all_cats:
        return ""
    lines = []
    for s in statuses:
        icon = "✅" if not s.missing_categories else "⏳"
        lines.append(f"{icon} *{s.location_name}*")
        missing = set(s.missing_categories)
        for cat in all_cats:
            lines.append(f"  {'⏳' if cat in missing else '✅'} {cat}")
    return "\n\n📊 *Статус заявок:*\n" + "\n".join(lines)


def _build_reminder_text() -> str:
    deadline = get_deadline()
    deadline_note = f"\n⏰ Дедлайн приёма заявок: `{deadline}`" if deadline else ""
    status = _build_order_status_text()
    return (
        "📢 *Напоминание*\n\n"
        f"Подайте заявки на поставку через бот.{deadline_note}{status}"
    )


def _build_reminder_kb() -> InlineKeyboardMarkup | None:
    """Build keyboard with 'Create order' button that opens bot in PM."""
    if not BOT_USERNAME:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📝 Создать заявку",
            url=f"https://t.me/{BOT_USERNAME}?start=order",
        )],
    ])


async def send_reminders(bot: Bot) -> None:
    """Send (or refresh) the pinned reminder message in every active chat."""
    if not is_orders_open():
        log.info("[Reminder] Orders closed — skipping.")
        return

    if not _is_within_working_hours():
        log.info("[Reminder] Outside working hours — skipping.")
        return

    chats = get_active_chats()
    if not chats:
        log.info("[Reminder] No active chats.")
        return

    for chat in chats:
        # Step 1 — delete old reminder
        old_msg_id = get_reminder_message_id(chat.id)
        if old_msg_id:
            try:
                await bot.delete_message(chat.id, old_msg_id)
            except Exception:
                pass  # already deleted or no permission — harmless
            clear_reminder_message_id(chat.id)

        # Step 2 — send new reminder and store its message_id
        try:
            sent = await bot.send_message(
                chat.id,
                _build_reminder_text(),
                parse_mode="Markdown",
                reply_markup=_build_reminder_kb(),
            )
            set_reminder_message_id(chat.id, sent.message_id)
            log.info("[Reminder] Sent to chat %s, msg_id=%s", chat.id, sent.message_id)
        except Exception as err:
            log.error("[Reminder] Failed for chat %s: %s", chat.id, err)


def start_reminder_loop(bot: Bot) -> asyncio.Task:
    """
    Spawn a background asyncio task that calls send_reminders() on the
    configured interval.  Interval is re-read from DB on every cycle.
    Returns the Task so the caller can cancel it.
    """
    from db.settings import get_reminder_interval_min

    async def _loop() -> None:
        log.info("[Reminder] Loop started.")
        while True:
            interval_s = get_reminder_interval_min() * 60
            await asyncio.sleep(interval_s)
            try:
                await send_reminders(bot)
            except Exception as err:
                log.error("[Reminder] Unhandled error: %s", err)

    return asyncio.create_task(_loop())
