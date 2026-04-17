"""
Manager dashboard service.

Maintains a single pinned message in MANAGER_CHAT_ID that shows
the current order status for all locations.  Called after every
order submit/edit and on each background tick.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest

from config.env import MANAGER_CHAT_ID
from db.database import get_db
from db.orders import get_all_orders_today, get_location_order_status_today
from db.settings import (
    clear_mgr_reminder_last_msg_id,
    get_deadline,
    get_deadline_warning_min,
    get_deadline_warning_sent_date,
    get_mgr_reminder_last_msg_id,
    set_deadline_warning_sent_date,
    set_mgr_reminder_last_msg_id,
)

log = logging.getLogger(__name__)


def _local_hhmm() -> str:
    now_utc = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        local = now_utc.astimezone(ZoneInfo("Asia/Yekaterinburg"))
    except ImportError:
        from datetime import timedelta
        local = now_utc + timedelta(hours=5)
    return local.strftime("%H:%M")


def _today_bit() -> int:
    now_utc = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        local = now_utc.astimezone(ZoneInfo("Asia/Yekaterinburg"))
    except ImportError:
        from datetime import timedelta
        local = now_utc + timedelta(hours=5)
    return 1 << local.weekday()


def _fmt_qty(qty: float) -> str:
    return str(int(qty)) if qty == int(qty) else str(qty)


def build_dashboard_text() -> str:
    statuses = get_location_order_status_today()
    orders_today = get_all_orders_today()

    today_bit = _today_bit()
    all_cats_today = [
        r["name"] for r in get_db().execute(
            "SELECT name, order_days FROM categories ORDER BY sort_order, id"
        ).fetchall()
        if (r["order_days"] or 127) & today_bit
    ]

    # Merge all items per location
    from collections import defaultdict
    loc_items: dict[int, dict[str, dict]] = defaultdict(dict)
    for order in orders_today:
        for item in order.items:
            key = f"{item.item_key}:{item.unit}"
            if key in loc_items[order.location_id]:
                loc_items[order.location_id][key]["quantity"] += item.quantity
            else:
                loc_items[order.location_id][key] = {
                    "item_name":     item.item_name,
                    "category_name": item.category_name,
                    "quantity":      item.quantity,
                    "unit":          item.unit,
                    "is_urgent":     item.is_urgent,
                }

    done_count = sum(1 for s in statuses if not s.missing_categories)
    total = len(statuses)
    deadline = get_deadline()
    deadline_note = f" | дедлайн {deadline}" if deadline else ""
    now = _local_hhmm()

    lines = [f"📋 <b>Доска заявок — {now}</b>{deadline_note}"]
    lines.append(f"<i>Принято: {done_count}/{total} точки</i>\n")

    for s in statuses:
        if not s.missing_categories:
            lines.append(f"✅ <b>{s.location_name}</b>")
        else:
            missing = ", ".join(s.missing_categories)
            lines.append(f"⏳ <b>{s.location_name}</b> — ждём: <i>{missing}</i>")

        items = loc_items.get(s.location_id, {})
        if items:
            cat_groups: dict[str, list] = {}
            for entry in items.values():
                cat_groups.setdefault(entry["category_name"], []).append(entry)
            for cat_name, entries in cat_groups.items():
                lines.append(f"  <b>{cat_name}:</b>")
                for e in entries:
                    prefix = "🚨 " if e["is_urgent"] else ""
                    lines.append(f"    • {prefix}{e['item_name']} — {_fmt_qty(e['quantity'])} {e['unit']}")
        else:
            lines.append("  <i>ещё не подали</i>")
        lines.append("")

    return "\n".join(lines).rstrip()


async def maybe_send_deadline_warning(bot: Bot) -> None:
    """Send a one-time warning to manager N minutes before the deadline."""
    deadline = get_deadline()
    if not deadline:
        return

    now_utc = datetime.now(timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        local_now = now_utc.astimezone(ZoneInfo("Asia/Yekaterinburg"))
    except ImportError:
        from datetime import timedelta as _td
        local_now = now_utc + _td(hours=5)

    today_str = local_now.strftime("%Y-%m-%d")
    if get_deadline_warning_sent_date() == today_str:
        return

    warn_min = get_deadline_warning_min()
    dl_h, dl_m = map(int, deadline.split(":"))
    dl_total = dl_h * 60 + dl_m
    warn_total = dl_total - warn_min
    if warn_total < 0:
        return

    now_total = local_now.hour * 60 + local_now.minute
    if not (warn_total <= now_total < dl_total):
        return

    # Build list of locations that still have missing categories
    from db.orders import get_location_order_status_today
    statuses = get_location_order_status_today()
    pending = [s.location_name for s in statuses if s.missing_categories]

    if pending:
        pending_text = "\n".join(f"⏳ {name}" for name in pending)
        text = (
            f"⏰ <b>Осталось {warn_min} минут до дедлайна ({deadline})</b>\n\n"
            f"Ещё не подали заявки:\n{pending_text}"
        )
    else:
        text = f"✅ <b>До дедлайна {warn_min} мин — все точки уже подали заявки!</b>"

    try:
        await bot.send_message(MANAGER_CHAT_ID, text, parse_mode="HTML")
        set_deadline_warning_sent_date(today_str)
        log.info("[DeadlineWarning] Sent warning, %d pending.", len(pending))
    except Exception as err:
        log.error("[DeadlineWarning] Failed to send: %s", err)


async def update_manager_dashboard(bot: Bot) -> None:
    """Edit the pinned dashboard, or create and pin a new one if missing."""
    try:
        text = build_dashboard_text()
    except Exception as err:
        log.error("[Dashboard] Failed to build text: %s", err)
        return

    msg_id = get_mgr_reminder_last_msg_id()

    if msg_id:
        try:
            await bot.edit_message_text(
                text, chat_id=MANAGER_CHAT_ID, message_id=msg_id, parse_mode="HTML"
            )
            return
        except TelegramBadRequest as e:
            if "message is not modified" in str(e):
                return
            # Message was deleted — fall through to create a new one
            clear_mgr_reminder_last_msg_id()
        except Exception as err:
            log.warning("[Dashboard] Edit failed (%s), will recreate.", err)
            clear_mgr_reminder_last_msg_id()

    # Send new dashboard and pin it
    try:
        sent = await bot.send_message(MANAGER_CHAT_ID, text, parse_mode="HTML")
        set_mgr_reminder_last_msg_id(sent.message_id)
        try:
            await bot.pin_chat_message(
                MANAGER_CHAT_ID, sent.message_id, disable_notification=True
            )
        except Exception:
            pass  # no pin permission — fine
        log.info("[Dashboard] Created new dashboard msg_id=%s", sent.message_id)
    except Exception as err:
        log.error("[Dashboard] Failed to send: %s", err)
