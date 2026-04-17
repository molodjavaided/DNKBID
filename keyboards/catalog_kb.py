"""
Category and item selection keyboards.
"""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from db.catalog import get_active_categories_today, get_all_categories, get_all_items_by_category


class CategoryCB(CallbackData, prefix="cat"):
    id: int


class ItemCB(CallbackData, prefix="item"):
    id: int


def categories_kb(cart_count: int = 0, active_only: bool = True) -> InlineKeyboardMarkup:
    cats = get_active_categories_today() if active_only else get_all_categories()
    buttons = [
        [InlineKeyboardButton(text=cat.name, callback_data=CategoryCB(id=cat.id).pack())]
        for cat in cats
    ]
    bottom: list[InlineKeyboardButton] = []
    if cart_count > 0:
        bottom.append(InlineKeyboardButton(
            text=f"🛒 Корзина ({cart_count})",
            callback_data="order:view",
        ))
    bottom.append(InlineKeyboardButton(text="❌ Отмена", callback_data="order:cancel"))
    buttons.append(bottom)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def items_kb(category_id: int, cart_count: int = 0) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=item.name, callback_data=ItemCB(id=item.id).pack())]
        for item in get_all_items_by_category(category_id)
    ]
    bottom: list[InlineKeyboardButton] = [
        InlineKeyboardButton(text="◀️ К категориям", callback_data="order:back_cats"),
    ]
    if cart_count > 0:
        bottom.append(InlineKeyboardButton(
            text=f"🛒 ({cart_count})",
            callback_data="order:view",
        ))
    buttons.append(bottom)
    return InlineKeyboardMarkup(inline_keyboard=buttons)
