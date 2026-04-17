"""
Quantity selection, cart action, unit picking, draft resume, and my-orders keyboards.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from config.catalog import QTY_OPTIONS

if TYPE_CHECKING:
    from db.orders import FullOrder


class QtyCB(CallbackData, prefix="qty"):
    value: int   # -1 = custom text input


class MyOrderCB(CallbackData, prefix="myord"):
    order_id: int
    action: str   # "edit"


class ExistingOrderCB(CallbackData, prefix="exord"):
    order_id: int
    action: str   # "view" | "close"


class UserItemUnitCB(CallbackData, prefix="uiu"):
    """Unit selection in the user ordering flow (multi-unit items)."""
    unit: str


class CartItemCB(CallbackData, prefix="ci"):
    """Edit or delete a specific cart line identified by (item_key, unit)."""
    action: str    # "edit" | "del"
    item_key: str
    unit: str


def quantity_kb(urgent: bool = False) -> InlineKeyboardMarkup:
    preset_row = [
        InlineKeyboardButton(text=str(q), callback_data=QtyCB(value=q).pack())
        for q in QTY_OPTIONS
    ]
    urgent_btn = InlineKeyboardButton(
        text="✅ Срочно (снять)" if urgent else "🚨 Отметить срочно",
        callback_data="order:toggle_urgent",
    )
    return InlineKeyboardMarkup(inline_keyboard=[
        preset_row,
        [
            InlineKeyboardButton(text="✏️ Своё кол-во", callback_data=QtyCB(value=-1).pack()),
            InlineKeyboardButton(text="◀️ Назад",        callback_data="order:back_items"),
        ],
        [urgent_btn],
    ])


def repeat_last_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Повторить последнюю", callback_data="order:repeat_last"),
            InlineKeyboardButton(text="🆕 Новый заказ",          callback_data="order:skip_repeat"),
        ]
    ])


def cart_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить заявку", callback_data="order:submit")],
        [
            InlineKeyboardButton(text="➕ Добавить ещё", callback_data="order:back_cats"),
            InlineKeyboardButton(text="🗑 Очистить",     callback_data="order:clear"),
        ],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="order:cancel")],
    ])


def cart_edit_kb(cart: list[dict]) -> InlineKeyboardMarkup:
    """Cart keyboard with per-line edit (✏️) and delete (❌) buttons."""
    buttons: list[list[InlineKeyboardButton]] = []
    for ln in cart:
        qty = ln["quantity"]
        qty_str = str(int(qty)) if qty == int(qty) else str(qty)
        name = ln["item_name"]
        if len(name) > 22:
            name = name[:21] + "…"
        label = f"✏️ {name} — {qty_str} {ln['unit']}"
        buttons.append([
            InlineKeyboardButton(
                text=label,
                callback_data=CartItemCB(action="edit", item_key=ln["item_key"], unit=ln["unit"]).pack(),
            ),
            InlineKeyboardButton(
                text="❌",
                callback_data=CartItemCB(action="del", item_key=ln["item_key"], unit=ln["unit"]).pack(),
            ),
        ])
    buttons.append([
        InlineKeyboardButton(text="➕ Добавить ещё", callback_data="order:back_cats"),
        InlineKeyboardButton(text="🗑 Очистить",     callback_data="order:clear"),
    ])
    buttons.append([InlineKeyboardButton(text="✅ Отправить заявку", callback_data="order:submit")])
    buttons.append([InlineKeyboardButton(text="🚫 Отмена",           callback_data="order:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def existing_order_intercept_kb(order_id: int) -> InlineKeyboardMarkup:
    """Shown when user runs /order but already has a submitted order today."""
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="✏️ Редактировать",
            callback_data=MyOrderCB(order_id=order_id, action="edit").pack(),
        )],
        [
            InlineKeyboardButton(
                text="📋 Проверить заявку",
                callback_data=ExistingOrderCB(order_id=order_id, action="view").pack(),
            ),
            InlineKeyboardButton(
                text="👋 Закрыть",
                callback_data=ExistingOrderCB(order_id=order_id, action="close").pack(),
            ),
        ],
    ])


def resume_draft_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🔄 Продолжить",    callback_data="order:resume_draft"),
            InlineKeyboardButton(text="🆕 Начать заново", callback_data="order:new_order"),
        ]
    ])


def user_unit_kb(units: list[str]) -> InlineKeyboardMarkup:
    """Unit picker shown to user when an item has multiple allowed units."""
    unit_buttons = [
        InlineKeyboardButton(text=unit, callback_data=UserItemUnitCB(unit=unit).pack())
        for unit in units
    ]
    return InlineKeyboardMarkup(inline_keyboard=[
        unit_buttons,
        [InlineKeyboardButton(text="◀️ К товарам", callback_data="order:back_items_from_unit")],
    ])


def my_orders_kb(orders: list[FullOrder], can_edit: bool) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for ord_ in orders:
        n = len(ord_.items)
        row = [InlineKeyboardButton(
            text=f"#{ord_.id} — {ord_.location_name} ({n} поз.)",
            callback_data="myord_noop",
        )]
        if can_edit:
            row.append(InlineKeyboardButton(
                text="✏️ Редактировать",
                callback_data=MyOrderCB(order_id=ord_.id, action="edit").pack(),
            ))
        buttons.append(row)
    buttons.append([InlineKeyboardButton(text="❌ Закрыть", callback_data="myord:close")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
