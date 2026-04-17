"""
User-facing handlers: /start, /order, /cancel, /myorders and the full order FSM.

Flow:
  /order → [resume draft?] → select_location → browse_categories
         → browse_items (with live cart preview)
         → [single-unit item] await_qty
         → [multi-unit item]  await_unit → await_qty
         → after qty: [multi-unit, remaining units] await_unit (add another)
                      [single-unit / no remaining]   browse_items
         → order:view → cart_kb → order:submit → done

  /myorders → today's orders → [✏️ Edit] → FSM cart loaded → order:submit
"""

from __future__ import annotations

import logging
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config.env import ADMIN_USER_ID, MANAGER_CHAT_ID
from db.catalog import get_category_by_id, get_item_by_id
from db.chats import upsert_chat
from db.locations import get_location_by_id
from db.orders import (
    CartLineInput,
    create_order,
    delete_draft_cart,
    get_last_order_for_location,
    get_order_by_id,
    get_user_orders_today,
    load_draft_cart,
    save_draft_cart,
    update_order_items,
)
from db.settings import get_deadline, is_orders_open
from handlers.states import OrderFSM
from keyboards.catalog_kb import CategoryCB, ItemCB, categories_kb, items_kb
from keyboards.location_kb import LocationCB, locations_kb
from keyboards.order_kb import (
    CartItemCB,
    ExistingOrderCB,
    MyOrderCB,
    QtyCB,
    UserItemUnitCB,
    cart_edit_kb,
    cart_kb,
    existing_order_intercept_kb,
    my_orders_kb,
    quantity_kb,
    repeat_last_kb,
    resume_draft_kb,
    user_unit_kb,
)

log = logging.getLogger(__name__)
router = Router()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _before_deadline(deadline: str) -> bool:
    if not deadline:
        return True
    try:
        from datetime import datetime
        now = datetime.now()
        dl_h, dl_m = map(int, deadline.split(":"))
        return now <= now.replace(hour=dl_h, minute=dl_m, second=0, microsecond=0)
    except Exception:
        return True


def _cart_count(data: dict[str, Any]) -> int:
    return len(data.get("cart", []))


def _fmt_qty(qty: float) -> str:
    return str(int(qty)) if qty == int(qty) else str(qty)


def _group_cart(cart: list[dict]) -> list[dict]:
    """
    Merge cart lines that share the same item_key into one display entry.
    Multiple units are joined: "5 уп. + 1 л."
    Preserves first-appearance order of each item.
    Returns list of {category_name, item_name, units_str, is_urgent}.
    """
    seen: dict[str, dict] = {}
    order: list[str] = []
    for ln in cart:
        key = f"{ln['category_name']}::{ln['item_key']}"
        if key not in seen:
            seen[key] = {
                "category_name": ln["category_name"],
                "item_name":     ln["item_name"],
                "parts":         [],
                "is_urgent":     False,
            }
            order.append(key)
        seen[key]["parts"].append(f"{_fmt_qty(ln['quantity'])} {ln['unit']}")
        if ln.get("is_urgent"):
            seen[key]["is_urgent"] = True
    for entry in seen.values():
        entry["units_str"] = " + ".join(entry["parts"])
    return [seen[k] for k in order]


def _cart_text(data: dict[str, Any]) -> str:
    """Full HTML cart summary grouped by category, same item merged to one line."""
    cart: list[dict] = data.get("cart", [])
    if not cart:
        return "Корзина пуста."
    grouped = _group_cart(cart)
    cat_groups: dict[str, list[dict]] = {}
    for entry in grouped:
        cat_groups.setdefault(entry["category_name"], []).append(entry)
    parts = [f"🛒 <b>Корзина — {data.get('location_name', '')}:</b>"]
    for cat_name, entries in cat_groups.items():
        parts.append(f"\n<b>{cat_name}:</b>")
        for e in entries:
            prefix = "🚨 " if e["is_urgent"] else ""
            parts.append(f"  • {prefix}{e['item_name']} — {e['units_str']}")
    n = len(grouped)
    suffix = "я" if n == 1 else "и" if n < 5 else "й"
    parts.append(f"\n<i>Итого: {n} позици{suffix}</i>")
    return "\n".join(parts)


def _items_screen_text(location_name: str, cat_name: str, cart: list[dict]) -> str:
    """Browse-items screen with dynamic cart preview; same item merged to one line."""
    parts = [f"📍 {location_name} › <b>{cat_name}</b>"]
    if cart:
        parts.append("\n🛒 <b>В корзине:</b>")
        for e in _group_cart(cart):
            prefix = "🚨 " if e["is_urgent"] else ""
            parts.append(f"  • {prefix}{e['item_name']} — {e['units_str']}")
        parts.append("─" * 20)
    parts.append("\nВыберите товар:")
    return "\n".join(parts)


def _cart_lines(cart: list[dict]) -> list[CartLineInput]:
    return [
        CartLineInput(
            item_key=ln["item_key"],
            item_name=ln["item_name"],
            category_name=ln["category_name"],
            quantity=ln["quantity"],
            unit=ln["unit"],
            is_urgent=ln.get("is_urgent", False),
        )
        for ln in cart
    ]


def _upsert_cart_line(cart: list[dict], new_line: dict) -> None:
    """Add new_line to cart, merging quantity if (item_key, unit) already exists."""
    for entry in cart:
        if entry["item_key"] == new_line["item_key"] and entry["unit"] == new_line["unit"]:
            entry["quantity"] += new_line["quantity"]
            if new_line.get("is_urgent"):
                entry["is_urgent"] = True
            return
    cart.append(new_line)


def _cart_used_units(cart: list[dict], item_id: int) -> set[str]:
    """Units already in cart for a specific item_id."""
    return {ln["unit"] for ln in cart if ln["item_key"] == str(item_id)}


# ── /start ────────────────────────────────────────────────────────────────────

def _start_kb(user_id: int) -> InlineKeyboardMarkup:
    buttons = [[InlineKeyboardButton(text="🚀 Сделать заказ", callback_data="start:order")]]
    if user_id == ADMIN_USER_ID:
        buttons.append([InlineKeyboardButton(text="⚙️ Панель администратора", callback_data="adm:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@router.message(CommandStart())
async def cmd_start(msg: Message, state: FSMContext) -> None:
    if msg.chat.type in ("group", "supergroup"):
        upsert_chat(msg.chat.id, msg.chat.title)
    await state.clear()
    await msg.answer(
        "👋 <b>Добро пожаловать в систему заявок ДНК!</b>\n\n"
        "Этот бот помогает бариста оформлять ежедневные заявки на поставку "
        "товаров и позволяет менеджерам отслеживать состояние запасов по всем локациям.\n\n"
        "📋 Выбирайте позиции из каталога по категориям\n"
        "🚨 Отмечайте срочные позиции — менеджер получит отдельное уведомление\n"
        "✅ Заявка мгновенно поступает менеджеру после отправки\n\n"
        "Нажмите кнопку ниже, чтобы начать 👇",
        reply_markup=_start_kb(msg.from_user.id),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "start:order")
async def cb_start_order(cq: CallbackQuery, state: FSMContext) -> None:
    if not is_orders_open():
        await cq.answer("⛔ Приём заявок временно закрыт.", show_alert=True)
        return

    draft = load_draft_cart(cq.from_user.id, cq.message.chat.id)
    if draft and draft.get("cart"):
        n = len(draft["cart"])
        suffix = "я" if n == 1 else "и" if n < 5 else "й"
        await state.set_data(draft)
        await state.set_state(OrderFSM.browse_categories)
        await cq.message.edit_text(
            f"📋 <b>Найдена несохранённая корзина</b> ({n} позици{suffix})\n"
            f"📍 Локация: <b>{draft.get('location_name', '?')}</b>\n\n"
            "Продолжить или начать заново?",
            reply_markup=resume_draft_kb(),
            parse_mode="HTML",
        )
        await cq.answer()
        return

    today_orders = [o for o in get_user_orders_today(cq.from_user.id) if o.items]
    if today_orders:
        order = today_orders[0]
        n = len(order.items)
        suffix = "я" if n == 1 else "и" if n < 5 else "й"
        await state.clear()
        await cq.message.edit_text(
            f"📋 <b>У вас уже есть заявка на сегодня</b>\n\n"
            f"📍 Локация: <b>{order.location_name}</b>\n"
            f"🗂 Позиций: {n} ({suffix})\n\n"
            "Хотите отредактировать или проверить её?",
            reply_markup=existing_order_intercept_kb(order.id),
            parse_mode="HTML",
        )
        await cq.answer()
        return

    await state.clear()
    await state.set_state(OrderFSM.select_location)
    await cq.message.edit_text("📍 Выберите вашу локацию:", reply_markup=locations_kb())
    await cq.answer()


# ── /help ─────────────────────────────────────────────────────────────────────

@router.message(Command("help"))
async def cmd_help(msg: Message) -> None:
    await msg.answer(
        "<b>Доступные команды:</b>\n\n"
        "/order     — создать заявку\n"
        "/myorders  — мои заявки за сегодня\n"
        "/cancel    — отменить текущую заявку\n"
        "/help      — эта справка",
        parse_mode="HTML",
    )


# ── /order ────────────────────────────────────────────────────────────────────

@router.message(Command("order"))
async def cmd_order(msg: Message, state: FSMContext) -> None:
    if not is_orders_open():
        await msg.answer("⛔ Приём заявок временно закрыт.")
        return

    draft = load_draft_cart(msg.from_user.id, msg.chat.id)
    if draft and draft.get("cart"):
        n = len(draft["cart"])
        suffix = "я" if n == 1 else "и" if n < 5 else "й"
        await state.set_data(draft)
        await state.set_state(OrderFSM.browse_categories)
        await msg.answer(
            f"📋 <b>Найдена несохранённая корзина</b> ({n} позици{suffix})\n"
            f"📍 Локация: <b>{draft.get('location_name', '?')}</b>\n\n"
            "Продолжить или начать заново?",
            reply_markup=resume_draft_kb(),
            parse_mode="HTML",
        )
        return

    # Check for existing orders today — intercept before showing location list
    today_orders = [o for o in get_user_orders_today(msg.from_user.id) if o.items]
    if today_orders:
        order = today_orders[0]  # most recent
        n = len(order.items)
        suffix = "я" if n == 1 else "и" if n < 5 else "й"
        await state.clear()
        await msg.answer(
            f"📋 <b>У вас уже есть заявка на сегодня</b>\n\n"
            f"📍 Локация: <b>{order.location_name}</b>\n"
            f"🗂 Позиций: {n} ({suffix})\n\n"
            "Хотите отредактировать или проверить её?",
            reply_markup=existing_order_intercept_kb(order.id),
            parse_mode="HTML",
        )
        return

    await state.clear()
    await state.set_state(OrderFSM.select_location)
    await msg.answer("📍 Выберите вашу локацию:", reply_markup=locations_kb())


@router.callback_query(F.data == "order:resume_draft")
async def cb_resume_draft(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(OrderFSM.browse_categories)
    await cq.message.edit_text(
        f"📍 Локация: <b>{data.get('location_name')}</b>\n\nВыберите категорию:",
        reply_markup=categories_kb(cart_count=_cart_count(data)),
        parse_mode="HTML",
    )
    await cq.answer()


@router.callback_query(F.data == "order:new_order")
async def cb_new_order(cq: CallbackQuery, state: FSMContext) -> None:
    delete_draft_cart(cq.from_user.id, cq.message.chat.id)
    await state.clear()
    await state.set_state(OrderFSM.select_location)
    await cq.message.edit_text("📍 Выберите вашу локацию:", reply_markup=locations_kb())
    await cq.answer()


@router.callback_query(ExistingOrderCB.filter())
async def cb_existing_order(cq: CallbackQuery, callback_data: ExistingOrderCB, state: FSMContext) -> None:
    if callback_data.action == "view":
        order = get_order_by_id(callback_data.order_id)
        if not order:
            await cq.answer("Заявка не найдена.", show_alert=True)
            return
        cart = [
            {
                "item_key":      item.item_key,
                "item_name":     item.item_name,
                "category_name": item.category_name,
                "quantity":      item.quantity,
                "unit":          item.unit,
                "is_urgent":     item.is_urgent,
            }
            for item in order.items
        ]
        summary = _cart_text({"location_name": order.location_name, "cart": cart})
        await cq.message.edit_text(
            f"📋 <b>Заявка #{order.id}</b>\n\n{summary}",
            reply_markup=existing_order_intercept_kb(order.id),
            parse_mode="HTML",
        )
        await cq.answer()

    elif callback_data.action == "close":
        await state.clear()
        await cq.message.delete()
        await cq.answer("Закрыто.")


# ── /cancel ───────────────────────────────────────────────────────────────────

@router.message(Command("cancel"))
async def cmd_cancel(msg: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await msg.answer("Нет активной заявки.")
        return
    delete_draft_cart(msg.from_user.id, msg.chat.id)
    await state.clear()
    await msg.answer("❌ Заявка отменена.")


# ── /myorders ─────────────────────────────────────────────────────────────────

@router.message(Command("myorders"))
async def cmd_myorders(msg: Message, state: FSMContext) -> None:
    await state.clear()
    orders = get_user_orders_today(msg.from_user.id)
    if not orders:
        await msg.answer("📭 У вас нет заявок за сегодня.")
        return
    can_edit = _before_deadline(get_deadline()) and is_orders_open()
    await msg.answer(
        "<b>📋 Ваши заявки за сегодня:</b>",
        reply_markup=my_orders_kb(orders, can_edit),
        parse_mode="HTML",
    )


@router.callback_query(F.data == "myord_noop")
async def cb_myord_noop(cq: CallbackQuery) -> None:
    await cq.answer()


@router.callback_query(F.data == "myord:close")
async def cb_myord_close(cq: CallbackQuery) -> None:
    await cq.message.delete()
    await cq.answer()


@router.callback_query(MyOrderCB.filter(F.action == "edit"))
async def cb_edit_order(cq: CallbackQuery, callback_data: MyOrderCB, state: FSMContext) -> None:
    if not _before_deadline(get_deadline()) or not is_orders_open():
        await cq.answer("⏰ Редактирование недоступно.", show_alert=True)
        return
    order = get_order_by_id(callback_data.order_id)
    if not order:
        await cq.answer("Заявка не найдена.", show_alert=True)
        return
    if order.tg_user_id != cq.from_user.id:
        await cq.answer("⛔ Это не ваша заявка.", show_alert=True)
        return
    cart = [
        {
            "item_key":      item.item_key,
            "item_name":     item.item_name,
            "category_name": item.category_name,
            "quantity":      item.quantity,
            "unit":          item.unit,
        }
        for item in order.items
    ]
    fsm_data = {
        "location_id":           order.location_id,
        "location_name":         order.location_name,
        "cart":                  cart,
        "current_category_id":   None,
        "current_category_name": None,
        "current_item_id":       None,
        "current_item_name":     None,
        "current_unit":          None,
        "editing_order_id":      order.id,
    }
    await state.set_data(fsm_data)
    await state.set_state(OrderFSM.browse_categories)
    await cq.message.edit_text(
        f"✏️ <b>Редактирование заявки #{order.id}</b>\n"
        f"📍 Локация: <b>{order.location_name}</b>\n\n"
        f"{_cart_text(fsm_data)}\n\n"
        "Добавьте товары или отправьте обновлённую заявку:",
        reply_markup=categories_kb(cart_count=len(cart)),
        parse_mode="HTML",
    )
    await cq.answer()


# ── Step 1 — location selection ───────────────────────────────────────────────

@router.callback_query(LocationCB.filter(), OrderFSM.select_location)
async def cb_location(cq: CallbackQuery, callback_data: LocationCB, state: FSMContext) -> None:
    loc = get_location_by_id(callback_data.id)
    if not loc:
        await cq.answer("Локация не найдена.", show_alert=True)
        return
    await state.update_data(
        location_id=loc.id,
        location_name=loc.name,
        cart=[],
        current_category_id=None,
        current_category_name=None,
        current_item_id=None,
        current_item_name=None,
        current_unit=None,
        current_urgent=False,
        editing_order_id=None,
    )
    await state.set_state(OrderFSM.browse_categories)

    last_order = get_last_order_for_location(loc.id, cq.from_user.id)
    if last_order and last_order.items:
        n = len(last_order.items)
        suffix = "я" if n == 1 else "и" if n < 5 else "й"
        await cq.message.edit_text(
            f"📍 <b>{loc.name}</b>\n\n"
            f"🔄 Найден предыдущий заказ ({n} позици{suffix}). Повторить?",
            reply_markup=repeat_last_kb(),
            parse_mode="HTML",
        )
    else:
        await cq.message.edit_text(
            f"📍 Локация: <b>{loc.name}</b>\n\nВыберите категорию товаров:",
            reply_markup=categories_kb(cart_count=0),
            parse_mode="HTML",
        )
    await cq.answer()


@router.callback_query(F.data == "order:repeat_last", OrderFSM.browse_categories)
async def cb_repeat_last(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    last_order = get_last_order_for_location(data["location_id"], cq.from_user.id)
    if not last_order or not last_order.items:
        await cq.answer("Предыдущий заказ не найден.", show_alert=True)
        return
    cart = [
        {
            "item_key":      item.item_key,
            "item_name":     item.item_name,
            "category_name": item.category_name,
            "quantity":      item.quantity,
            "unit":          item.unit,
            "is_urgent":     False,
        }
        for item in last_order.items
    ]
    await state.update_data(cart=cart)
    save_draft_cart(cq.from_user.id, cq.message.chat.id, await state.get_data())
    await cq.message.edit_text(
        f"✅ Загружена последняя заявка ({len(cart)} поз.)\n\n"
        f"📍 Локация: <b>{data['location_name']}</b>\n\nДобавьте или скорректируйте позиции:",
        reply_markup=categories_kb(cart_count=len(cart)),
        parse_mode="HTML",
    )
    await cq.answer()


@router.callback_query(F.data == "order:skip_repeat", OrderFSM.browse_categories)
async def cb_skip_repeat(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await cq.message.edit_text(
        f"📍 Локация: <b>{data['location_name']}</b>\n\nВыберите категорию товаров:",
        reply_markup=categories_kb(cart_count=0),
        parse_mode="HTML",
    )
    await cq.answer()


# ── Step 2 — category selection ───────────────────────────────────────────────

@router.callback_query(CategoryCB.filter(), OrderFSM.browse_categories)
async def cb_category(cq: CallbackQuery, callback_data: CategoryCB, state: FSMContext) -> None:
    cat = get_category_by_id(callback_data.id)
    if not cat:
        await cq.answer("Категория не найдена.", show_alert=True)
        return
    data = await state.get_data()
    await state.update_data(current_category_id=cat.id, current_category_name=cat.name)
    await state.set_state(OrderFSM.browse_items)
    await cq.message.edit_text(
        _items_screen_text(data["location_name"], cat.name, data.get("cart", [])),
        reply_markup=items_kb(cat.id, cart_count=_cart_count(data)),
        parse_mode="HTML",
    )
    await cq.answer()


# ── Step 3 — item selection ───────────────────────────────────────────────────

@router.callback_query(ItemCB.filter(), OrderFSM.browse_items)
async def cb_item(cq: CallbackQuery, callback_data: ItemCB, state: FSMContext) -> None:
    item = get_item_by_id(callback_data.id)
    if not item:
        await cq.answer("Товар не найден.", show_alert=True)
        return
    data = await state.get_data()
    allowed = item.allowed_units if item.allowed_units else [item.unit_type or "шт."]
    await state.update_data(current_item_id=item.id, current_item_name=item.name, current_urgent=False)

    if len(allowed) > 1:
        # Multi-unit: pick unit first
        await state.set_state(OrderFSM.await_unit)
        await cq.message.edit_text(
            f"📍 {data['location_name']} › {item.category_name} › <b>{item.name}</b>\n\n"
            "Выберите единицу измерения:",
            reply_markup=user_unit_kb(allowed),
            parse_mode="HTML",
        )
    else:
        # Single unit: go directly to qty
        unit = allowed[0]
        await state.update_data(current_unit=unit)
        await state.set_state(OrderFSM.await_qty)
        await cq.message.edit_text(
            f"📍 {data['location_name']} › {item.category_name} › <b>{item.name}</b>\n\n"
            f"Укажите количество ({unit}):",
            reply_markup=quantity_kb(urgent=False),
            parse_mode="HTML",
        )
    await cq.answer()


# ── Step 3b — unit selection (multi-unit items) ───────────────────────────────

@router.callback_query(UserItemUnitCB.filter(), OrderFSM.await_unit)
async def cb_unit_select(cq: CallbackQuery, callback_data: UserItemUnitCB, state: FSMContext) -> None:
    data = await state.get_data()
    unit = callback_data.unit
    await state.update_data(current_unit=unit)
    await state.set_state(OrderFSM.await_qty)
    item_name = data.get("current_item_name", "")
    await cq.message.edit_text(
        f"📍 {data['location_name']} › {data.get('current_category_name', '')} › <b>{item_name}</b>\n\n"
        f"Укажите количество ({unit}):",
        reply_markup=quantity_kb(urgent=data.get("current_urgent", False)),
        parse_mode="HTML",
    )
    await cq.answer()


@router.callback_query(F.data == "order:back_items_from_unit")
async def cb_back_items_from_unit(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    cat_id   = data.get("current_category_id")
    cat_name = data.get("current_category_name", "")
    cart     = data.get("cart", [])
    await state.set_state(OrderFSM.browse_items)
    await cq.message.edit_text(
        _items_screen_text(data.get("location_name", ""), cat_name, cart),
        reply_markup=items_kb(cat_id, cart_count=len(cart)),
        parse_mode="HTML",
    )
    await cq.answer()


# ── Urgency toggle ───────────────────────────────────────────────────────────

@router.callback_query(F.data == "order:toggle_urgent", OrderFSM.await_qty)
async def cb_toggle_urgent(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    new_urgent = not data.get("current_urgent", False)
    await state.update_data(current_urgent=new_urgent)
    await cq.message.edit_reply_markup(reply_markup=quantity_kb(urgent=new_urgent))
    await cq.answer("🚨 Срочно включено" if new_urgent else "Срочность снята")


# ── Step 4 — quantity ─────────────────────────────────────────────────────────

@router.callback_query(QtyCB.filter(), OrderFSM.await_qty)
async def cb_qty(cq: CallbackQuery, callback_data: QtyCB, state: FSMContext) -> None:
    if callback_data.value == -1:
        await state.set_state(OrderFSM.await_custom_qty)
        data = await state.get_data()
        unit = data.get("current_unit") or "шт."
        await cq.message.edit_text(
            f"✏️ Введите количество для <b>{data['current_item_name']}</b> ({unit}):\n"
            f"<i>(отправьте число в чат)</i>",
            parse_mode="HTML",
        )
        await cq.answer()
        return
    await _apply_qty(cq, state, float(callback_data.value))


@router.message(OrderFSM.await_custom_qty)
async def msg_custom_qty(msg: Message, state: FSMContext) -> None:
    try:
        # Принимаем и 1.5 и 1,5
        qty = float(msg.text.replace(",", ".").strip())
        if qty <= 0:
            raise ValueError
    except (ValueError, AttributeError):
        await msg.answer("⚠️ Введите положительное число (например: 3 или 1.5).")
        return

    data = await state.get_data()
    item = get_item_by_id(data["current_item_id"])

    if not item:
        await msg.answer("Ошибка: товар не найден. Начните заново /order.")
        await state.clear()
        return

    unit = data.get("current_unit") or item.unit_type or "шт."
    is_urgent = data.get("current_urgent", False)
    cart: list[dict] = data.get("cart", [])

    _upsert_cart_line(cart, {
        "item_key":      str(item.id),
        "item_name":     item.name,
        "category_name": item.category_name,
        "quantity":      qty,
        "unit":          unit,
        "is_urgent":     is_urgent,
    })
    await state.update_data(cart=cart, current_urgent=False)

    if not data.get("editing_order_id"):
        save_draft_cart(msg.from_user.id, msg.chat.id, await state.get_data())

    # Display merged total for this (item, unit)
    merged_qty = next(
        ln["quantity"] for ln in cart
        if ln["item_key"] == str(item.id) and ln["unit"] == unit
    )
    qty_str = int(merged_qty) if merged_qty == int(merged_qty) else merged_qty

    allowed = item.allowed_units if item.allowed_units else [item.unit_type or "шт."]
    used = _cart_used_units(cart, item.id)
    remaining = [u for u in allowed if u not in used]

    if len(allowed) > 1 and remaining:
        await state.set_state(OrderFSM.await_unit)
        await msg.answer(
            f"✅ <b>{item.name}</b> × {qty_str} {unit} в корзине.\n\n"
            f"Добавить ещё единицу измерения для <b>{item.name}</b> (например, литры к коробкам) или вернуться к списку?",
            reply_markup=user_unit_kb(remaining),
            parse_mode="HTML",
        )
    else:
        cat_id   = data.get("current_category_id")
        cat_name = data.get("current_category_name", "")
        await state.set_state(OrderFSM.browse_items)
        await msg.answer(
            f"✅ <b>{item.name}</b> × {qty_str} {unit} в корзине.\n\n"
            + _items_screen_text(data["location_name"], cat_name, cart),
            reply_markup=items_kb(cat_id, cart_count=len(cart)),
            parse_mode="HTML",
        )


async def _apply_qty(cq: CallbackQuery, state: FSMContext, qty: float) -> None:
    data = await state.get_data()
    item = get_item_by_id(data["current_item_id"])
    if not item:
        await cq.answer("Ошибка: товар не найден.", show_alert=True)
        return

    unit = data.get("current_unit") or item.unit_type or "шт."
    is_urgent = data.get("current_urgent", False)
    cart: list[dict] = data.get("cart", [])
    _upsert_cart_line(cart, {
        "item_key":      str(item.id),
        "item_name":     item.name,
        "category_name": item.category_name,
        "quantity":      qty,
        "unit":          unit,
        "is_urgent":     is_urgent,
    })
    await state.update_data(cart=cart, current_urgent=False)

    if not data.get("editing_order_id"):
        save_draft_cart(cq.from_user.id, cq.message.chat.id, await state.get_data())

    # Display merged total for this (item, unit)
    merged_qty = next(
        ln["quantity"] for ln in cart
        if ln["item_key"] == str(item.id) and ln["unit"] == unit
    )
    qty_str = int(merged_qty) if merged_qty == int(merged_qty) else merged_qty

    # Check for multi-unit follow-up
    allowed = item.allowed_units if item.allowed_units else [item.unit_type or "шт."]
    used = _cart_used_units(cart, item.id)
    remaining = [u for u in allowed if u not in used]

    if len(allowed) > 1 and remaining:
        await state.set_state(OrderFSM.await_unit)
        await cq.message.edit_text(
            f"✅ <b>{item.name}</b> × {qty_str} {unit} в корзине.\n\n"
            f"Добавить ещё единицу для <b>{item.name}</b> или вернуться к товарам?",
            reply_markup=user_unit_kb(remaining),
            parse_mode="HTML",
        )
    else:
        cat_id   = data.get("current_category_id")
        cat_name = data.get("current_category_name", "")
        await state.set_state(OrderFSM.browse_items)
        await cq.message.edit_text(
            f"✅ <b>{item.name}</b> × {qty_str} {unit} в корзине.\n\n"
            + _items_screen_text(data["location_name"], cat_name, cart),
            reply_markup=items_kb(cat_id, cart_count=len(cart)),
            parse_mode="HTML",
        )
    await cq.answer()


# ── Navigation ────────────────────────────────────────────────────────────────

@router.callback_query(F.data == "order:back_cats")
async def cb_back_cats(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.set_state(OrderFSM.browse_categories)
    await cq.message.edit_text(
        f"📍 Локация: <b>{data.get('location_name')}</b>\n\nВыберите категорию:",
        reply_markup=categories_kb(cart_count=_cart_count(data)),
        parse_mode="HTML",
    )
    await cq.answer()


@router.callback_query(F.data == "order:back_items", OrderFSM.await_qty)
async def cb_back_items(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    cat_id   = data.get("current_category_id")
    cat_name = data.get("current_category_name", "")
    cart     = data.get("cart", [])
    await state.set_state(OrderFSM.browse_items)
    await cq.message.edit_text(
        _items_screen_text(data.get("location_name", ""), cat_name, cart),
        reply_markup=items_kb(cat_id, cart_count=len(cart)),
        parse_mode="HTML",
    )
    await cq.answer()


@router.callback_query(F.data == "order:view")
async def cb_view_cart(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    cart: list[dict] = data.get("cart", [])
    if not cart:
        await cq.answer("Корзина пуста.", show_alert=True)
        return
    await state.set_state(OrderFSM.browse_categories)
    editing_id = data.get("editing_order_id")
    header = f"✏️ <b>Редактирование заявки #{editing_id}</b>\n\n" if editing_id else ""
    await cq.message.edit_text(
        header + _cart_text(data),
        reply_markup=cart_edit_kb(cart),
        parse_mode="HTML",
    )
    await cq.answer()


@router.callback_query(CartItemCB.filter())
async def cb_cart_item_action(cq: CallbackQuery, callback_data: CartItemCB, state: FSMContext) -> None:
    data = await state.get_data()
    cart: list[dict] = data.get("cart", [])
    item_key = callback_data.item_key
    unit = callback_data.unit

    if callback_data.action == "del":
        cart = [ln for ln in cart if not (ln["item_key"] == item_key and ln["unit"] == unit)]
        await state.update_data(cart=cart)
        if not data.get("editing_order_id"):
            save_draft_cart(cq.from_user.id, cq.message.chat.id, await state.get_data())
        if not cart:
            await state.set_state(OrderFSM.browse_categories)
            await cq.message.edit_text(
                f"🗑 Позиция удалена. Корзина пуста.\n\n"
                f"📍 Локация: <b>{data.get('location_name')}</b>\n\nВыберите категорию:",
                reply_markup=categories_kb(cart_count=0),
                parse_mode="HTML",
            )
        else:
            editing_id = data.get("editing_order_id")
            header = f"✏️ <b>Редактирование заявки #{editing_id}</b>\n\n" if editing_id else ""
            updated_data = {**data, "cart": cart}
            await cq.message.edit_text(
                header + _cart_text(updated_data),
                reply_markup=cart_edit_kb(cart),
                parse_mode="HTML",
            )
        await cq.answer("Удалено.")

    elif callback_data.action == "edit":
        item = get_item_by_id(int(item_key))
        if not item:
            await cq.answer("Товар не найден.", show_alert=True)
            return
        # Remove existing line so the user enters a fresh qty (prevents double-merge)
        cart = [ln for ln in cart if not (ln["item_key"] == item_key and ln["unit"] == unit)]
        await state.update_data(
            cart=cart,
            current_item_id=item.id,
            current_item_name=item.name,
            current_category_id=item.category_id,
            current_category_name=item.category_name,
            current_unit=unit,
            current_urgent=False,
        )
        await state.set_state(OrderFSM.await_qty)
        await cq.message.edit_text(
            f"✏️ Изменить количество: <b>{item.name}</b> ({unit})\n"
            f"<i>Введите новое количество:</i>",
            reply_markup=quantity_kb(urgent=False),
            parse_mode="HTML",
        )
        await cq.answer()


@router.callback_query(F.data == "order:clear")
async def cb_clear_cart(cq: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.update_data(cart=[])
    await state.set_state(OrderFSM.browse_categories)
    save_draft_cart(cq.from_user.id, cq.message.chat.id, await state.get_data())
    await cq.message.edit_text(
        f"🗑 Корзина очищена.\n\n"
        f"📍 Локация: <b>{data.get('location_name')}</b>\n\nВыберите категорию:",
        reply_markup=categories_kb(cart_count=0),
        parse_mode="HTML",
    )
    await cq.answer("Корзина очищена.")


@router.callback_query(F.data == "order:cancel")
async def cb_cancel_order(cq: CallbackQuery, state: FSMContext) -> None:
    delete_draft_cart(cq.from_user.id, cq.message.chat.id)
    await state.clear()
    await cq.message.edit_text("❌ Заявка отменена.")
    await cq.answer()


# ── Order submission ──────────────────────────────────────────────────────────

@router.callback_query(F.data == "order:submit")
async def cb_submit(cq: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    data = await state.get_data()
    cart: list[dict] = data.get("cart", [])
    if not cart:
        await cq.answer("Корзина пуста.", show_alert=True)
        return

    editing_order_id: int | None = data.get("editing_order_id")
    cart_lines = _cart_lines(cart)
    summary    = _cart_text(data)
    who        = f"@{cq.from_user.username}" if cq.from_user.username else cq.from_user.full_name

    delete_draft_cart(cq.from_user.id, cq.message.chat.id)
    await state.clear()

    if editing_order_id:
        success = update_order_items(editing_order_id, cart_lines)
        if not success:
            await cq.message.edit_text("⚠️ Не удалось обновить заявку. Попробуйте ещё раз.")
            await cq.answer()
            return
        await cq.message.edit_text(
            f"✅ <b>Заявка #{editing_order_id} обновлена!</b>\n\n{summary}",
            parse_mode="HTML",
        )
        await cq.answer("Заявка обновлена!")
        try:
            await bot.send_message(
                MANAGER_CHAT_ID,
                f"✏️ <b>Заявка #{editing_order_id} обновлена</b> ({who})\n\n{summary}",
                parse_mode="HTML",
            )
        except Exception as exc:
            log.error("[Order] Manager notify failed: %s", exc)

    else:
        order_id = create_order(
            location_id=data["location_id"],
            tg_message_id=cq.message.message_id,
            tg_chat_id=cq.message.chat.id,
            tg_user_id=cq.from_user.id,
            tg_user_name=cq.from_user.username or cq.from_user.full_name,
            cart=cart_lines,
        )
        if order_id is None:
            await cq.message.edit_text("⚠️ Эта заявка уже была отправлена ранее.")
            await cq.answer()
            return
        await cq.message.edit_text(
            f"✅ <b>Заявка #{order_id} принята!</b>\n\n{summary}",
            parse_mode="HTML",
        )
        await cq.answer("Заявка отправлена!")
        try:
            await bot.send_message(
                MANAGER_CHAT_ID,
                f"📦 <b>Новая заявка #{order_id}</b> от {who}\n\n{summary}",
                parse_mode="HTML",
            )
        except Exception as exc:
            log.error("[Order] Manager notify failed: %s", exc)

    # Urgent items — immediate separate notification
    urgent_grouped = [e for e in _group_cart(cart) if e["is_urgent"]]
    if urgent_grouped:
        urgent_parts = [
            "🚨 <b>СРОЧНО! Нехватка товаров</b>",
            f"📍 {data['location_name']} — {who}",
            "",
        ]
        for e in urgent_grouped:
            urgent_parts.append(f"  • {e['item_name']} — {e['units_str']}")
        try:
            await bot.send_message(
                MANAGER_CHAT_ID,
                "\n".join(urgent_parts),
                parse_mode="HTML",
            )
        except Exception as exc:
            log.error("[Order] Urgent notify failed: %s", exc)
