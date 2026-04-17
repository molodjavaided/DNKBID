"""Admin-only middleware — blocks non-admin users from all admin_router handlers."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from config.env import ADMIN_USER_ID


class AdminOnlyMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, CallbackQuery):
            if event.from_user.id != ADMIN_USER_ID:
                await event.answer("⛔ Нет доступа.", show_alert=True)
                return
        elif isinstance(event, Message):
            if not event.from_user or event.from_user.id != ADMIN_USER_ID:
                return
        return await handler(event, data)
