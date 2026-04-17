"""
Location selection keyboard.
"""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

from db.locations import get_all_locations


class LocationCB(CallbackData, prefix="loc"):
    id: int


def locations_kb() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=loc.name, callback_data=LocationCB(id=loc.id).pack())]
        for loc in get_all_locations()
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
