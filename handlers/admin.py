"""
Admin panel: /admin command + inline callbacks for management actions.
Access is restricted to ADMIN_USER_ID from .env.
"""

from __future__ import annotations

import logging
import re

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from db.catalog import (
    add_category, add_item, add_unit,
    delete_category, delete_item, delete_unit,
    get_all_categories, get_all_items_by_category, get_all_units,
    get_category_by_id, get_category_order_days,
    get_item_by_id, get_unit_by_id,
    rename_category, rename_item, rename_unit,
    set_item_allowed_units, toggle_category_day, toggle_item_availability,
)
from db.locations import add_location, delete_location, get_all_locations, get_location_by_id, rename_location
from db.orders import get_all_orders_today, get_location_avg_orders, get_location_order_status_today
from db.settings import (
    get_deadline, get_deadline_warning_min, get_mgr_reminder_interval_min,
    get_reminder_interval_min, get_reminder_work_start, is_orders_open,
    set_deadline, set_deadline_warning_min, set_mgr_reminder_interval_min,
    set_orders_open, set_reminder_interval_min, set_reminder_work_start,
)
from handlers.states import AdminFSM
from keyboards.admin_kb import (
    AdminCrudCB,
    AdminUnitToggleCB,
    AvgOrderLocCB,
    CatScheduleCB,
    DayToggleCB,
    admin_menu_kb,
    avg_order_location_kb,
    cat_list_kb,
    categories_mgmt_kb,
    confirm_delete_kb,
    day_toggle_kb,
    item_units_select_kb,
    items_cat_select_kb,
    items_mgmt_kb,
    locations_mgmt_kb,
    reminders_kb,
    units_mgmt_kb,
)

from handlers.admin_middleware import AdminOnlyMiddleware

log = logging.getLogger(__name__)
router = Router()
router.message.middleware(AdminOnlyMiddleware())
router.callback_query.middleware(AdminOnlyMiddleware())

_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def _menu_text(orders_open: bool) -> str:
    status = "🟢 открыт" if orders_open else "🔴 закрыт"
    return f"<b>⚙️ Панель администратора</b>\n\nПриём заявок: {status}"


def _menu_kb(orders_open: bool) -> object:
    return admin_menu_kb(orders_open, deadline=get_deadline() or "")


# ── No-op (display-only buttons) ─────────────────────────────────────────────

@router.callback_query(F.data == "adm_noop")
async def adm_noop(cq: CallbackQuery) -> None:
    await cq.answer()


# ── /admin ────────────────────────────────────────────────────────────────────

@router.message(Command("admin"))
async def cmd_admin(msg: Message, state: FSMContext) -> None:
    await state.clear()
    open_ = is_orders_open()
    await msg.answer(_menu_text(open_), reply_markup=_menu_kb(open_), parse_mode="HTML")


# ── Toggle orders ─────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:toggle")
async def adm_toggle(cq: CallbackQuery) -> None:
    new_open = not is_orders_open()
    set_orders_open(new_open)
    await cq.message.edit_text(
        _menu_text(new_open), reply_markup=admin_menu_kb(new_open), parse_mode="HTML"
    )
    await cq.answer("открыт" if new_open else "закрыт")


# ── Deadline ──────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:set_deadline")
async def adm_set_deadline_prompt(cq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFSM.await_deadline)
    await cq.message.edit_text(
        "⏰ Введите время дедлайна в формате <b>HH:MM</b> (например, <code>14:00</code>):",
        parse_mode="HTML",
    )
    await cq.answer()


@router.message(AdminFSM.await_deadline)
async def adm_deadline_input(msg: Message, state: FSMContext) -> None:
    text = (msg.text or "").strip()
    if not _TIME_RE.match(text):
        await msg.answer(
            "⚠️ Неверный формат. Введите время как HH:MM, например <code>14:00</code>.",
            parse_mode="HTML",
        )
        return
    set_deadline(text)
    await state.clear()
    open_ = is_orders_open()
    await msg.answer(_menu_text(open_), reply_markup=_menu_kb(open_), parse_mode="HTML")


@router.callback_query(F.data == "adm:clear_deadline")
async def adm_clear_deadline(cq: CallbackQuery) -> None:
    set_deadline("")
    open_ = is_orders_open()
    await cq.message.edit_text(
        _menu_text(open_), reply_markup=_menu_kb(open_), parse_mode="HTML"
    )
    await cq.answer("Дедлайн сброшен.")


# ── Status ────────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:status")
async def adm_status(cq: CallbackQuery) -> None:
    statuses = get_location_order_status_today()
    orders_today = get_all_orders_today()

    # Group orders by location_id, merge all items across multiple orders
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

    if not statuses:
        body = "Локации не найдены."
    else:
        lines: list[str] = []
        for s in statuses:
            if not s.missing_categories:
                lines.append(f"✅ <b>{s.location_name}</b>")
            else:
                missing = ", ".join(s.missing_categories)
                lines.append(f"❌ <b>{s.location_name}</b> — нет: <i>{missing}</i>")

            items = loc_items.get(s.location_id, {})
            if items:
                # Group by category
                cat_groups: dict[str, list] = {}
                for entry in items.values():
                    cat_groups.setdefault(entry["category_name"], []).append(entry)
                for cat_name, entries in cat_groups.items():
                    lines.append(f"  <b>{cat_name}:</b>")
                    for e in entries:
                        qty = int(e["quantity"]) if e["quantity"] == int(e["quantity"]) else e["quantity"]
                        prefix = "🚨 " if e["is_urgent"] else ""
                        lines.append(f"    • {prefix}{e['item_name']} — {qty} {e['unit']}")
            else:
                lines.append("  <i>заявок нет</i>")
            lines.append("")
        body = "\n".join(lines).rstrip()

    try:
        await cq.message.edit_text(
            f"<b>📊 Статус заявок на сегодня:</b>\n\n{body}",
            reply_markup=_menu_kb(is_orders_open()),
            parse_mode="HTML",
        )
    except Exception:
        pass
    await cq.answer()


# ── Back to menu ──────────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:menu")
async def adm_menu(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    open_ = is_orders_open()
    await cq.message.edit_text(
        _menu_text(open_), reply_markup=_menu_kb(open_), parse_mode="HTML"
    )
    await cq.answer()


# ── Category schedule ─────────────────────────────────────────────────────────

@router.callback_query(F.data == "adm:schedule")
async def adm_schedule_list(cq: CallbackQuery) -> None:
    categories = get_all_categories()
    if not categories:
        await cq.answer("Нет категорий в базе.", show_alert=True)
        return
    await cq.message.edit_text(
        "<b>📅 Расписание категорий</b>\n\nВыберите категорию:",
        reply_markup=cat_list_kb(categories),
        parse_mode="HTML",
    )
    await cq.answer()


@router.callback_query(CatScheduleCB.filter())
async def adm_cat_days(cq: CallbackQuery, callback_data: CatScheduleCB) -> None:
    cat = get_category_by_id(callback_data.cat_id)
    if not cat:
        await cq.answer("Категория не найдена.", show_alert=True)
        return
    order_days = get_category_order_days(cat.id)
    await cq.message.edit_text(
        f"<b>📅 {cat.name}</b>\n\n"
        "✅ — принимаются  ⬜ — отключён",
        reply_markup=day_toggle_kb(cat.id, order_days),
        parse_mode="HTML",
    )
    await cq.answer()


@router.callback_query(DayToggleCB.filter())
async def adm_toggle_day(cq: CallbackQuery, callback_data: DayToggleCB) -> None:
    cat = get_category_by_id(callback_data.cat_id)
    if not cat:
        await cq.answer("Категория не найдена.", show_alert=True)
        return
    new_mask = toggle_category_day(callback_data.cat_id, callback_data.day)
    _DAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    day_name = _DAY_NAMES[callback_data.day]
    state_label = "включён" if new_mask & (1 << callback_data.day) else "отключён"
    await cq.message.edit_reply_markup(reply_markup=day_toggle_kb(cat.id, new_mask))
    await cq.answer(f"{day_name} — {state_label}.")


# ── CRUD dispatcher ───────────────────────────────────────────────────────────

@router.callback_query(AdminCrudCB.filter())
async def adm_crud(cq: CallbackQuery, callback_data: AdminCrudCB, state: FSMContext) -> None:
    section = callback_data.section
    action  = callback_data.action
    eid     = callback_data.entity_id

    if section == "locs":
        await _handle_locs(cq, action, eid, state)
    elif section == "cats":
        await _handle_cats(cq, action, eid, state)
    elif section == "items":
        await _handle_items(cq, action, eid, state)
    elif section == "units":
        await _handle_units(cq, action, eid, state)
    else:
        await cq.answer()


# ── Locations CRUD ────────────────────────────────────────────────────────────

async def _handle_locs(cq: CallbackQuery, action: str, eid: int, state: FSMContext) -> None:
    if action == "list":
        locs = get_all_locations()
        await cq.message.edit_text(
            "<b>🏢 Управление локациями</b>",
            reply_markup=locations_mgmt_kb(locs),
            parse_mode="HTML",
        )
        await cq.answer()

    elif action == "add":
        await state.set_state(AdminFSM.await_new_location_name)
        await cq.message.edit_text("🏢 Введите название новой локации:")
        await cq.answer()

    elif action == "edit":
        loc = get_location_by_id(eid)
        if not loc:
            await cq.answer("Локация не найдена.", show_alert=True)
            return
        await state.set_state(AdminFSM.await_edit_location_name)
        await state.update_data(editing_loc_id=eid)
        await cq.message.edit_text(
            f"✏️ Новое название для <b>{loc.name}</b>:", parse_mode="HTML"
        )
        await cq.answer()

    elif action == "del":
        loc = get_location_by_id(eid)
        name = loc.name if loc else f"#{eid}"
        await cq.message.edit_text(
            f"⚠️ Удалить локацию <b>{name}</b>?\n\nЭто действие необратимо.",
            reply_markup=confirm_delete_kb("locs", eid),
            parse_mode="HTML",
        )
        await cq.answer()

    elif action == "confirm_del":
        loc = get_location_by_id(eid)
        name = loc.name if loc else f"#{eid}"
        delete_location(eid)
        locs = get_all_locations()
        await cq.message.edit_text(
            f"🗑 Локация <b>{name}</b> удалена.\n\n<b>🏢 Управление локациями</b>",
            reply_markup=locations_mgmt_kb(locs),
            parse_mode="HTML",
        )
        await cq.answer("Удалено.")

    elif action == "cancel_del":
        locs = get_all_locations()
        await cq.message.edit_text(
            "<b>🏢 Управление локациями</b>",
            reply_markup=locations_mgmt_kb(locs),
            parse_mode="HTML",
        )
        await cq.answer("Отменено.")

    else:
        await cq.answer()


@router.message(AdminFSM.await_new_location_name)
async def adm_new_location_name(msg: Message, state: FSMContext) -> None:
    name = (msg.text or "").strip()
    if not name:
        await msg.answer("⚠️ Введите непустое название.")
        return
    if add_location(name) is None:
        await msg.answer("⚠️ Не удалось добавить. Такое название уже существует.")
        return
    await state.clear()
    await msg.answer(
        f"✅ Локация <b>{name}</b> добавлена.\n\n<b>🏢 Управление локациями</b>",
        reply_markup=locations_mgmt_kb(get_all_locations()),
        parse_mode="HTML",
    )


@router.message(AdminFSM.await_edit_location_name)
async def adm_edit_location_name(msg: Message, state: FSMContext) -> None:
    name = (msg.text or "").strip()
    if not name:
        await msg.answer("⚠️ Введите непустое название.")
        return
    data = await state.get_data()
    rename_location(data.get("editing_loc_id"), name)
    await state.clear()
    await msg.answer(
        f"✅ Переименовано в <b>{name}</b>.\n\n<b>🏢 Управление локациями</b>",
        reply_markup=locations_mgmt_kb(get_all_locations()),
        parse_mode="HTML",
    )


# ── Categories CRUD ───────────────────────────────────────────────────────────

async def _handle_cats(cq: CallbackQuery, action: str, eid: int, state: FSMContext) -> None:
    if action == "list":
        cats = get_all_categories()
        await cq.message.edit_text(
            "<b>📂 Управление категориями</b>\n"
            "<i>⚠️ Удаление категории удаляет все её товары.</i>",
            reply_markup=categories_mgmt_kb(cats),
            parse_mode="HTML",
        )
        await cq.answer()

    elif action == "add":
        await state.set_state(AdminFSM.await_new_category_name)
        await cq.message.edit_text("📂 Введите название новой категории:")
        await cq.answer()

    elif action == "edit":
        cat = get_category_by_id(eid)
        if not cat:
            await cq.answer("Категория не найдена.", show_alert=True)
            return
        await state.set_state(AdminFSM.await_edit_category_name)
        await state.update_data(editing_cat_id=eid)
        await cq.message.edit_text(
            f"✏️ Новое название для <b>{cat.name}</b>:", parse_mode="HTML"
        )
        await cq.answer()

    elif action == "del":
        cat = get_category_by_id(eid)
        name = cat.name if cat else f"#{eid}"
        await cq.message.edit_text(
            f"⚠️ Удалить категорию <b>{name}</b> и все её товары?\n\nЭто действие необратимо.",
            reply_markup=confirm_delete_kb("cats", eid),
            parse_mode="HTML",
        )
        await cq.answer()

    elif action == "confirm_del":
        cat = get_category_by_id(eid)
        name = cat.name if cat else f"#{eid}"
        delete_category(eid)
        await cq.message.edit_text(
            f"🗑 Категория <b>{name}</b> удалена.\n\n<b>📂 Управление категориями</b>",
            reply_markup=categories_mgmt_kb(get_all_categories()),
            parse_mode="HTML",
        )
        await cq.answer("Удалено.")

    elif action == "cancel_del":
        await cq.message.edit_text(
            "<b>📂 Управление категориями</b>",
            reply_markup=categories_mgmt_kb(get_all_categories()),
            parse_mode="HTML",
        )
        await cq.answer("Отменено.")

    else:
        await cq.answer()


@router.message(AdminFSM.await_new_category_name)
async def adm_new_category_name(msg: Message, state: FSMContext) -> None:
    name = (msg.text or "").strip()
    if not name:
        await msg.answer("⚠️ Введите непустое название.")
        return
    if add_category(name) is None:
        await msg.answer("⚠️ Такое название уже существует.")
        return
    await state.clear()
    await msg.answer(
        f"✅ Категория <b>{name}</b> добавлена.\n\n<b>📂 Управление категориями</b>",
        reply_markup=categories_mgmt_kb(get_all_categories()),
        parse_mode="HTML",
    )


@router.message(AdminFSM.await_edit_category_name)
async def adm_edit_category_name(msg: Message, state: FSMContext) -> None:
    name = (msg.text or "").strip()
    if not name:
        await msg.answer("⚠️ Введите непустое название.")
        return
    data = await state.get_data()
    rename_category(data.get("editing_cat_id"), name)
    await state.clear()
    await msg.answer(
        f"✅ Переименовано в <b>{name}</b>.\n\n<b>📂 Управление категориями</b>",
        reply_markup=categories_mgmt_kb(get_all_categories()),
        parse_mode="HTML",
    )


# ── Items CRUD ────────────────────────────────────────────────────────────────

async def _handle_items(cq: CallbackQuery, action: str, eid: int, state: FSMContext) -> None:
    if action == "list":
        cats = get_all_categories()
        await cq.message.edit_text(
            "<b>📝 Управление товарами</b>\n\nВыберите категорию:",
            reply_markup=items_cat_select_kb(cats),
            parse_mode="HTML",
        )
        await cq.answer()

    elif action == "items_in_cat":
        cat = get_category_by_id(eid)
        if not cat:
            await cq.answer("Категория не найдена.", show_alert=True)
            return
        items = get_all_items_by_category(eid, admin=True)
        await cq.message.edit_text(
            f"<b>📝 {cat.name}</b> — товары\n\n"
            "✅/⛔ — доступность  ✏️ — назв.  📐 — единицы  🗑 — удалить",
            reply_markup=items_mgmt_kb(items, cat_id=eid),
            parse_mode="HTML",
        )
        await cq.answer()

    elif action == "toggle_avail":
        item = get_item_by_id(eid)
        if not item:
            await cq.answer("Товар не найден.", show_alert=True)
            return
        new_state = toggle_item_availability(eid)
        state_label = "доступен" if new_state else "скрыт из меню"
        items = get_all_items_by_category(item.category_id, admin=True)
        cat = get_category_by_id(item.category_id)
        cat_name = cat.name if cat else ""
        await cq.message.edit_text(
            f"<b>📝 {cat_name}</b> — товары\n\n"
            "✅/⛔ — доступность  ✏️ — назв.  📐 — единицы  🗑 — удалить",
            reply_markup=items_mgmt_kb(items, cat_id=item.category_id),
            parse_mode="HTML",
        )
        await cq.answer(f"{item.name} — {state_label}.")

    elif action == "add":
        await state.set_state(AdminFSM.await_new_item_name)
        await state.update_data(pending_cat_id=eid)
        cat = get_category_by_id(eid)
        cat_name = cat.name if cat else ""
        await cq.message.edit_text(
            f"📝 Введите название нового товара для <b>{cat_name}</b>:",
            parse_mode="HTML",
        )
        await cq.answer()

    elif action == "edit":
        item = get_item_by_id(eid)
        if not item:
            await cq.answer("Товар не найден.", show_alert=True)
            return
        await state.set_state(AdminFSM.await_edit_item_name)
        await state.update_data(editing_item_id=eid, editing_item_cat_id=item.category_id)
        await cq.message.edit_text(
            f"✏️ Новое название для <b>{item.name}</b>:", parse_mode="HTML"
        )
        await cq.answer()

    elif action == "edit_unit":
        item = get_item_by_id(eid)
        if not item:
            await cq.answer("Товар не найден.", show_alert=True)
            return
        await state.set_state(AdminFSM.await_edit_item_unit)
        await state.update_data(
            editing_item_id=eid,
            editing_item_cat_id=item.category_id,
            selected_units=list(item.allowed_units),
        )
        all_units = get_all_units()
        await cq.message.edit_text(
            f"📐 Единицы измерения для <b>{item.name}</b>:\n"
            f"Текущие: <code>{', '.join(item.allowed_units)}</code>",
            reply_markup=item_units_select_kb(all_units, item.allowed_units, back_cat_id=item.category_id),
            parse_mode="HTML",
        )
        await cq.answer()

    elif action == "del":
        item = get_item_by_id(eid)
        if not item:
            await cq.answer("Товар не найден.", show_alert=True)
            return
        await state.update_data(confirm_del_item_cat_id=item.category_id)
        await cq.message.edit_text(
            f"⚠️ Удалить товар <b>{item.name}</b>?\n\nЭто действие необратимо.",
            reply_markup=confirm_delete_kb("items", eid),
            parse_mode="HTML",
        )
        await cq.answer()

    elif action == "confirm_del":
        item = get_item_by_id(eid)
        data = await state.get_data()
        cat_id = (item.category_id if item else None) or data.get("confirm_del_item_cat_id", 0)
        name = item.name if item else f"#{eid}"
        delete_item(eid)
        items = get_all_items_by_category(cat_id, admin=True)
        cat = get_category_by_id(cat_id)
        cat_name = cat.name if cat else ""
        await cq.message.edit_text(
            f"🗑 Товар <b>{name}</b> удалён.\n\n<b>📝 {cat_name}</b>",
            reply_markup=items_mgmt_kb(items, cat_id=cat_id),
            parse_mode="HTML",
        )
        await cq.answer("Удалено.")

    elif action == "cancel_del":
        data = await state.get_data()
        item = get_item_by_id(eid)
        cat_id = (item.category_id if item else None) or data.get("confirm_del_item_cat_id", 0)
        items = get_all_items_by_category(cat_id, admin=True)
        cat = get_category_by_id(cat_id)
        cat_name = cat.name if cat else ""
        await cq.message.edit_text(
            f"<b>📝 {cat_name}</b> — товары",
            reply_markup=items_mgmt_kb(items, cat_id=cat_id),
            parse_mode="HTML",
        )
        await cq.answer("Отменено.")

    else:
        await cq.answer()


@router.message(AdminFSM.await_new_item_name)
async def adm_new_item_name(msg: Message, state: FSMContext) -> None:
    name = (msg.text or "").strip()
    if not name:
        await msg.answer("⚠️ Введите непустое название.")
        return
    await state.update_data(pending_item_name=name, selected_units=[])
    await state.set_state(AdminFSM.await_new_item_unit)
    all_units = get_all_units()
    await msg.answer(
        f"📐 Выберите единицы измерения для <b>{name}</b>:\n"
        "<i>Выберите одну или несколько, затем нажмите ✅ Готово</i>",
        reply_markup=item_units_select_kb(all_units, []),
        parse_mode="HTML",
    )


@router.message(AdminFSM.await_edit_item_name)
async def adm_edit_item_name(msg: Message, state: FSMContext) -> None:
    name = (msg.text or "").strip()
    if not name:
        await msg.answer("⚠️ Введите непустое название.")
        return
    data = await state.get_data()
    cat_id = data.get("editing_item_cat_id")
    rename_item(data.get("editing_item_id"), name)
    await state.clear()
    items = get_all_items_by_category(cat_id, admin=True)
    cat = get_category_by_id(cat_id)
    cat_name = cat.name if cat else ""
    await msg.answer(
        f"✅ Переименовано в <b>{name}</b>.\n\n<b>📝 {cat_name}</b>",
        reply_markup=items_mgmt_kb(items, cat_id=cat_id),
        parse_mode="HTML",
    )


# ── Multi-select unit toggle ──────────────────────────────────────────────────

@router.callback_query(AdminUnitToggleCB.filter())
async def adm_unit_toggle(cq: CallbackQuery, callback_data: AdminUnitToggleCB, state: FSMContext) -> None:
    data = await state.get_data()
    selected: list[str] = list(data.get("selected_units", []))
    unit_name = callback_data.unit_name
    if unit_name in selected:
        selected.remove(unit_name)
    else:
        selected.append(unit_name)
    await state.update_data(selected_units=selected)
    all_units = get_all_units()
    back_cat_id = data.get("pending_cat_id") or data.get("editing_item_cat_id") or 0
    await cq.message.edit_reply_markup(
        reply_markup=item_units_select_kb(all_units, selected, back_cat_id=back_cat_id)
    )
    await cq.answer()


@router.callback_query(F.data == "adm:units_select_done")
async def adm_units_select_done(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    selected: list[str] = data.get("selected_units", [])
    if not selected:
        await cq.answer("⚠️ Выберите хотя бы одну единицу.", show_alert=True)
        return

    current_state = await state.get_state()

    if current_state == AdminFSM.await_new_item_unit:
        item_name = data.get("pending_item_name", "")
        cat_id    = data.get("pending_cat_id", 0)
        unit_type = selected[0]
        new_id = add_item(cat_id, item_name, unit_type, allowed_units=selected)
        await state.clear()
        if new_id is None:
            await cq.message.edit_text("⚠️ Не удалось добавить. Такое название уже существует.")
            await cq.answer()
            return
        items = get_all_items_by_category(cat_id, admin=True)
        cat = get_category_by_id(cat_id)
        cat_name = cat.name if cat else ""
        await cq.message.edit_text(
            f"✅ Товар <b>{item_name}</b> ({', '.join(selected)}) добавлен.\n\n<b>📝 {cat_name}</b>",
            reply_markup=items_mgmt_kb(items, cat_id=cat_id),
            parse_mode="HTML",
        )
        await cq.answer("Добавлено.")

    elif current_state == AdminFSM.await_edit_item_unit:
        item_id = data.get("editing_item_id")
        cat_id  = data.get("editing_item_cat_id", 0)
        item = get_item_by_id(item_id)
        if not item:
            await cq.answer("Товар не найден.", show_alert=True)
            return
        set_item_allowed_units(item_id, selected)
        await state.clear()
        items = get_all_items_by_category(cat_id, admin=True)
        cat = get_category_by_id(cat_id)
        cat_name = cat.name if cat else ""
        await cq.message.edit_text(
            f"✅ Единицы для <b>{item.name}</b>: <b>{', '.join(selected)}</b>\n\n<b>📝 {cat_name}</b>",
            reply_markup=items_mgmt_kb(items, cat_id=cat_id),
            parse_mode="HTML",
        )
        await cq.answer("Обновлено.")

    else:
        await cq.answer()


# ── Units CRUD ────────────────────────────────────────────────────────────────

async def _handle_units(cq: CallbackQuery, action: str, eid: int, state: FSMContext) -> None:
    if action == "list":
        units = get_all_units()
        await cq.message.edit_text(
            "<b>📏 Управление единицами измерения</b>",
            reply_markup=units_mgmt_kb(units),
            parse_mode="HTML",
        )
        await cq.answer()

    elif action == "add":
        await state.set_state(AdminFSM.await_new_unit_name)
        await cq.message.edit_text("📏 Введите название новой единицы (например: мл, г, пак):")
        await cq.answer()

    elif action == "edit":
        unit = get_unit_by_id(eid)
        if not unit:
            await cq.answer("Единица не найдена.", show_alert=True)
            return
        await state.set_state(AdminFSM.await_edit_unit_name)
        await state.update_data(editing_unit_id=eid)
        await cq.message.edit_text(
            f"✏️ Новое название для <b>{unit.name}</b>:", parse_mode="HTML"
        )
        await cq.answer()

    elif action == "del":
        unit = get_unit_by_id(eid)
        name = unit.name if unit else f"#{eid}"
        await cq.message.edit_text(
            f"⚠️ Удалить единицу <b>{name}</b>?\n\nТовары, использующие её, сохранят текущее значение.",
            reply_markup=confirm_delete_kb("units", eid),
            parse_mode="HTML",
        )
        await cq.answer()

    elif action == "confirm_del":
        unit = get_unit_by_id(eid)
        name = unit.name if unit else f"#{eid}"
        delete_unit(eid)
        await cq.message.edit_text(
            f"🗑 Единица <b>{name}</b> удалена.\n\n<b>📏 Управление единицами</b>",
            reply_markup=units_mgmt_kb(get_all_units()),
            parse_mode="HTML",
        )
        await cq.answer("Удалено.")

    elif action == "cancel_del":
        await cq.message.edit_text(
            "<b>📏 Управление единицами</b>",
            reply_markup=units_mgmt_kb(get_all_units()),
            parse_mode="HTML",
        )
        await cq.answer("Отменено.")

    else:
        await cq.answer()


@router.message(AdminFSM.await_new_unit_name)
async def adm_new_unit_name(msg: Message, state: FSMContext) -> None:
    name = (msg.text or "").strip()
    if not name:
        await msg.answer("⚠️ Введите непустое название.")
        return
    if add_unit(name) is None:
        await msg.answer("⚠️ Такая единица уже существует.")
        return
    await state.clear()
    await msg.answer(
        f"✅ Единица <b>{name}</b> добавлена.\n\n<b>📏 Управление единицами</b>",
        reply_markup=units_mgmt_kb(get_all_units()),
        parse_mode="HTML",
    )


@router.message(AdminFSM.await_edit_unit_name)
async def adm_edit_unit_name(msg: Message, state: FSMContext) -> None:
    name = (msg.text or "").strip()
    if not name:
        await msg.answer("⚠️ Введите непустое название.")
        return
    data = await state.get_data()
    rename_unit(data.get("editing_unit_id"), name)
    await state.clear()
    await msg.answer(
        f"✅ Переименовано в <b>{name}</b>.\n\n<b>📏 Управление единицами</b>",
        reply_markup=units_mgmt_kb(get_all_units()),
        parse_mode="HTML",
    )


# ── Reminders (unified) ───────────────────────────────────────────────────────

def _reminders_text() -> str:
    deadline = get_deadline()
    dl = deadline or "не установлен"
    return (
        "<b>🔔 Напоминания</b>\n\n"
        f"⏰ Дедлайн (конец окна): <code>{dl}</code>\n"
        f"🕗 Начало: <code>{get_reminder_work_start()}</code>\n"
        f"📢 Баристы: каждые <code>{get_reminder_interval_min()}</code> мин\n"
        f"🔄 Доска менеджера: каждые <code>{get_mgr_reminder_interval_min()}</code> мин\n"
        f"⏰ Предупреждение до дедлайна: за <code>{get_deadline_warning_min()}</code> мин"
    )


def _reminders_kb() -> object:
    return reminders_kb(
        start=get_reminder_work_start(),
        barista_min=get_reminder_interval_min(),
        dashboard_min=get_mgr_reminder_interval_min(),
        warning_min=get_deadline_warning_min(),
    )


@router.callback_query(F.data == "adm:reminders")
async def adm_reminders(cq: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await cq.message.edit_text(_reminders_text(), reply_markup=_reminders_kb(), parse_mode="HTML")
    await cq.answer()


@router.callback_query(F.data == "adm:set_reminder_start")
async def adm_set_reminder_start(cq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFSM.await_reminder_start)
    await cq.message.edit_text(
        "🕗 Введите время начала напоминаний в формате <b>HH:MM</b> (например, <code>08:00</code>):\n"
        "<i>Например: 08:00</i>",
        parse_mode="HTML",
    )
    await cq.answer()


@router.message(AdminFSM.await_reminder_start)
async def msg_reminder_start(msg: Message, state: FSMContext) -> None:
    time_str = (msg.text or "").strip()
    if not _TIME_RE.match(time_str):
        await msg.answer("⚠️ Неверный формат. Используйте HH:MM (например: 08:00)")
        return
    set_reminder_work_start(time_str)
    await state.clear()
    await msg.answer(_reminders_text(), reply_markup=_reminders_kb(), parse_mode="HTML")


@router.callback_query(F.data == "adm:set_reminder_interval")
async def adm_set_reminder_interval(cq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFSM.await_reminder_interval)
    await cq.message.edit_text(
        "📢 Введите интервал напоминаний для барист в <b>минутах</b> (например, <code>60</code>):",
        parse_mode="HTML",
    )
    await cq.answer()


@router.message(AdminFSM.await_reminder_interval)
async def msg_reminder_interval(msg: Message, state: FSMContext) -> None:
    if not (msg.text or "").strip().isdigit() or int(msg.text.strip()) < 1:
        await msg.answer("⚠️ Введите целое число ≥ 1.")
        return
    set_reminder_interval_min(int(msg.text.strip()))
    await state.clear()
    await msg.answer(_reminders_text(), reply_markup=_reminders_kb(), parse_mode="HTML")


@router.callback_query(F.data == "adm:set_dashboard_interval")
async def adm_set_dashboard_interval(cq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFSM.await_dashboard_interval)
    await cq.message.edit_text(
        "🔄 Введите интервал обновления доски менеджера в <b>минутах</b> (например, <code>30</code>):",
        parse_mode="HTML",
    )
    await cq.answer()


@router.message(AdminFSM.await_dashboard_interval)
async def msg_dashboard_interval(msg: Message, state: FSMContext) -> None:
    if not (msg.text or "").strip().isdigit() or int(msg.text.strip()) < 1:
        await msg.answer("⚠️ Введите целое число ≥ 1.")
        return
    set_mgr_reminder_interval_min(int(msg.text.strip()))
    await state.clear()
    await msg.answer(_reminders_text(), reply_markup=_reminders_kb(), parse_mode="HTML")


@router.callback_query(F.data == "adm:set_deadline_warning")
async def adm_set_deadline_warning(cq: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminFSM.await_deadline_warning_min)
    await cq.message.edit_text(
        "⏰ За сколько минут до дедлайна отправлять предупреждение менеджеру?\n"
        "Введите целое число (например, <code>15</code>):",
        parse_mode="HTML",
    )
    await cq.answer()


@router.message(AdminFSM.await_deadline_warning_min)
async def msg_deadline_warning_min(msg: Message, state: FSMContext) -> None:
    if not (msg.text or "").strip().isdigit() or int(msg.text.strip()) < 1:
        await msg.answer("⚠️ Введите целое число ≥ 1.")
        return
    set_deadline_warning_min(int(msg.text.strip()))
    await state.clear()
    await msg.answer(_reminders_text(), reply_markup=_reminders_kb(), parse_mode="HTML")


@router.callback_query(F.data == "adm:dashboard_now")
async def adm_dashboard_now(cq: CallbackQuery, bot: Bot) -> None:
    from services.dashboard_service import update_manager_dashboard
    await update_manager_dashboard(bot)
    await cq.answer("📤 Доска обновлена.")


# ── /avg_order — Smart Statistics ─────────────────────────────────────────────

@router.message(Command("avg_order"))
async def cmd_avg_order(msg: Message, state: FSMContext) -> None:
    await state.clear()
    locs = get_all_locations()
    if not locs:
        await msg.answer("Локации не найдены.")
        return
    await msg.answer(
        "📊 <b>Средний заказ по статистике</b>\n\nВыберите локацию:",
        reply_markup=avg_order_location_kb(locs),
        parse_mode="HTML",
    )


@router.callback_query(AvgOrderLocCB.filter())
async def cb_avg_order_location(cq: CallbackQuery, callback_data: AvgOrderLocCB) -> None:
    loc = get_location_by_id(callback_data.location_id)
    if not loc:
        await cq.answer("Локация не найдена.", show_alert=True)
        return

    rows = get_location_avg_orders(callback_data.location_id, last_n=10)
    if not rows:
        await cq.message.edit_text(
            f"📊 <b>{loc.name}</b>\n\n<i>История заказов пуста.</i>",
            parse_mode="HTML",
        )
        await cq.answer()
        return

    # Group by category
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["category_name"], []).append(r)

    parts = [f"📊 <b>Средний заказ — {loc.name}</b>", "<i>(на основе последних 10 заказов)</i>"]
    for cat_name, items in groups.items():
        parts.append(f"\n<b>{cat_name}:</b>")
        for it in items:
            qty = int(it["avg_qty"]) if it["avg_qty"] == int(it["avg_qty"]) else it["avg_qty"]
            parts.append(f"  • {it['item_name']} — {qty} {it['unit']} <i>({it['order_count']} зак.)</i>")

    await cq.message.edit_text("\n".join(parts), parse_mode="HTML")
    await cq.answer()
