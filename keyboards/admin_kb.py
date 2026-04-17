"""
Admin panel keyboards and callback-data classes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

if TYPE_CHECKING:
    from db.catalog import Category, Item, Unit
    from db.locations import Location


# ── Callback data ─────────────────────────────────────────────────────────────

class CatScheduleCB(CallbackData, prefix="catday"):
    cat_id: int


class DayToggleCB(CallbackData, prefix="daytog"):
    cat_id: int
    day: int   # 0=Mon … 6=Sun


class AdminCrudCB(CallbackData, prefix="ac"):
    section:   str        # "locs" | "cats" | "items" | "units"
    action:    str        # "list"|"add"|"edit"|"del"|"confirm_del"|"cancel_del"|
                          # "cat_sel"|"items_in_cat"|"edit_unit"
    entity_id: int = 0


class AdminUnitToggleCB(CallbackData, prefix="aut"):
    """Toggle a unit on/off in the multi-select item unit picker."""
    unit_name: str


class AvgOrderLocCB(CallbackData, prefix="avgordloc"):
    """Location picker for the /avg_order admin command."""
    location_id: int


# ── Constants ─────────────────────────────────────────────────────────────────

_DAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


# ── Main menu ─────────────────────────────────────────────────────────────────

def admin_menu_kb(orders_open: bool, deadline: str = "") -> InlineKeyboardMarkup:
    toggle_text = "🔴 Закрыть приём заявок" if orders_open else "🟢 Открыть приём заявок"
    dl_text = f"⏰ Дедлайн: {deadline}" if deadline else "⏰ Установить дедлайн"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=toggle_text, callback_data="adm:toggle")],
        [
            InlineKeyboardButton(text=dl_text,       callback_data="adm:set_deadline"),
            InlineKeyboardButton(text="🗑",          callback_data="adm:clear_deadline"),
        ],
        [
            InlineKeyboardButton(text="📊 Статус заявок",        callback_data="adm:status"),
            InlineKeyboardButton(text="📅 Расписание",           callback_data="adm:schedule"),
        ],
        [InlineKeyboardButton(text="🔔 Напоминания",             callback_data="adm:reminders")],
        [
            InlineKeyboardButton(text="🏢 Локации",   callback_data=AdminCrudCB(section="locs",  action="list").pack()),
            InlineKeyboardButton(text="📂 Категории", callback_data=AdminCrudCB(section="cats",  action="list").pack()),
        ],
        [
            InlineKeyboardButton(text="📝 Товары",    callback_data=AdminCrudCB(section="items", action="list").pack()),
            InlineKeyboardButton(text="📏 Единицы",   callback_data=AdminCrudCB(section="units", action="list").pack()),
        ],
    ])


def reminders_kb(start: str, barista_min: int, dashboard_min: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"🕗 Начало: {start}",
            callback_data="adm:set_reminder_start",
        )],
        [InlineKeyboardButton(
            text=f"📢 Баристы: каждые {barista_min} мин",
            callback_data="adm:set_reminder_interval",
        )],
        [InlineKeyboardButton(
            text=f"🔄 Доска менеджера: каждые {dashboard_min} мин",
            callback_data="adm:set_dashboard_interval",
        )],
        [InlineKeyboardButton(text="📤 Обновить доску сейчас", callback_data="adm:dashboard_now")],
        [InlineKeyboardButton(text="◀️ В меню", callback_data="adm:menu")],
    ])


# ── Confirmation dialog ───────────────────────────────────────────────────────

def confirm_delete_kb(section: str, entity_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="✅ Да, удалить",
                callback_data=AdminCrudCB(section=section, action="confirm_del", entity_id=entity_id).pack(),
            ),
            InlineKeyboardButton(
                text="❌ Отмена",
                callback_data=AdminCrudCB(section=section, action="cancel_del", entity_id=entity_id).pack(),
            ),
        ]
    ])


# ── Schedule keyboards ────────────────────────────────────────────────────────

def cat_list_kb(categories: list[Category]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=cat.name, callback_data=CatScheduleCB(cat_id=cat.id).pack())]
        for cat in categories
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def day_toggle_kb(cat_id: int, order_days: int) -> InlineKeyboardMarkup:
    day_buttons = [
        InlineKeyboardButton(
            text=("✅ " if order_days & (1 << i) else "⬜ ") + _DAY_LABELS[i],
            callback_data=DayToggleCB(cat_id=cat_id, day=i).pack(),
        )
        for i in range(7)
    ]
    return InlineKeyboardMarkup(inline_keyboard=[
        day_buttons[:4],
        day_buttons[4:],
        [InlineKeyboardButton(text="◀️ К категориям", callback_data="adm:schedule")],
    ])


# ── Locations management ──────────────────────────────────────────────────────

def locations_mgmt_kb(locations: list[Location]) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text="➕ Добавить локацию",
            callback_data=AdminCrudCB(section="locs", action="add").pack(),
        )]
    ]
    for loc in locations:
        buttons.append([
            InlineKeyboardButton(text=loc.name, callback_data="adm_noop"),
            InlineKeyboardButton(
                text="✏️",
                callback_data=AdminCrudCB(section="locs", action="edit", entity_id=loc.id).pack(),
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=AdminCrudCB(section="locs", action="del", entity_id=loc.id).pack(),
            ),
        ])
    buttons.append([InlineKeyboardButton(text="◀️ В меню", callback_data="adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ── Categories management ─────────────────────────────────────────────────────

def categories_mgmt_kb(categories: list[Category]) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text="➕ Добавить категорию",
            callback_data=AdminCrudCB(section="cats", action="add").pack(),
        )]
    ]
    for cat in categories:
        buttons.append([
            InlineKeyboardButton(text=cat.name, callback_data="adm_noop"),
            InlineKeyboardButton(
                text="✏️",
                callback_data=AdminCrudCB(section="cats", action="edit", entity_id=cat.id).pack(),
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=AdminCrudCB(section="cats", action="del", entity_id=cat.id).pack(),
            ),
        ])
    buttons.append([InlineKeyboardButton(text="◀️ В меню", callback_data="adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ── Items management ──────────────────────────────────────────────────────────

def items_cat_select_kb(categories: list[Category]) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(
            text=cat.name,
            callback_data=AdminCrudCB(section="items", action="items_in_cat", entity_id=cat.id).pack(),
        )]
        for cat in categories
    ]
    buttons.append([InlineKeyboardButton(text="◀️ В меню", callback_data="adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def items_mgmt_kb(items: list[Item], cat_id: int) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text="➕ Добавить товар",
            callback_data=AdminCrudCB(section="items", action="add", entity_id=cat_id).pack(),
        )]
    ]
    for item in items:
        units_label = ", ".join(item.allowed_units) if item.allowed_units else item.unit_type
        avail_icon = "✅" if item.is_available else "⛔"
        buttons.append([
            InlineKeyboardButton(text=f"{item.name} ({units_label})", callback_data="adm_noop"),
            InlineKeyboardButton(
                text=avail_icon,
                callback_data=AdminCrudCB(section="items", action="toggle_avail", entity_id=item.id).pack(),
            ),
            InlineKeyboardButton(
                text="✏️",
                callback_data=AdminCrudCB(section="items", action="edit", entity_id=item.id).pack(),
            ),
            InlineKeyboardButton(
                text="📐",
                callback_data=AdminCrudCB(section="items", action="edit_unit", entity_id=item.id).pack(),
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=AdminCrudCB(section="items", action="del", entity_id=item.id).pack(),
            ),
        ])
    buttons.append([
        InlineKeyboardButton(
            text="◀️ К категориям",
            callback_data=AdminCrudCB(section="items", action="list").pack(),
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def avg_order_location_kb(locations: list[Location]) -> InlineKeyboardMarkup:
    """Location picker for /avg_order admin command."""
    buttons = [
        [InlineKeyboardButton(
            text=loc.name,
            callback_data=AvgOrderLocCB(location_id=loc.id).pack(),
        )]
        for loc in locations
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


# ── Item unit multi-select picker ─────────────────────────────────────────────

def item_units_select_kb(
    all_units: list[Unit],
    selected: list[str],
    back_cat_id: int = 0,
) -> InlineKeyboardMarkup:
    """
    Multi-select keyboard for choosing allowed units on an item.
    ✅ = selected, ⬜ = not selected. Clicking a button toggles it.
    """
    toggle_buttons = [
        InlineKeyboardButton(
            text=("✅ " if u.name in selected else "⬜ ") + u.name,
            callback_data=AdminUnitToggleCB(unit_name=u.name).pack(),
        )
        for u in all_units
    ]
    # Split into rows of 3
    rows: list[list[InlineKeyboardButton]] = [
        toggle_buttons[i:i + 3] for i in range(0, len(toggle_buttons), 3)
    ]
    bottom: list[InlineKeyboardButton] = [
        InlineKeyboardButton(text="✅ Готово", callback_data="adm:units_select_done")
    ]
    if back_cat_id:
        bottom.append(InlineKeyboardButton(
            text="◀️ Отмена",
            callback_data=AdminCrudCB(section="items", action="items_in_cat", entity_id=back_cat_id).pack(),
        ))
    else:
        bottom.append(InlineKeyboardButton(text="◀️ Отмена", callback_data="adm:menu"))
    rows.append(bottom)
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Units management ──────────────────────────────────────────────────────────

def units_mgmt_kb(units: list[Unit]) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = [
        [InlineKeyboardButton(
            text="➕ Добавить единицу",
            callback_data=AdminCrudCB(section="units", action="add").pack(),
        )]
    ]
    for unit in units:
        buttons.append([
            InlineKeyboardButton(text=unit.name, callback_data="adm_noop"),
            InlineKeyboardButton(
                text="✏️",
                callback_data=AdminCrudCB(section="units", action="edit", entity_id=unit.id).pack(),
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=AdminCrudCB(section="units", action="del", entity_id=unit.id).pack(),
            ),
        ])
    buttons.append([InlineKeyboardButton(text="◀️ В меню", callback_data="adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
