"""
Entry point.
"""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from config import env
from db.migrations import run_migrations
from handlers import admin_router, user_router
from services.manager_reminder_service import start_manager_reminder_loop
from services.reminder_service import start_reminder_loop


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stdout,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    log = logging.getLogger(__name__)
    log.info("[Boot] Starting BotDnkBid_py")

    # 1. DB migrations + seed
    run_migrations()

    # 2. Bot + dispatcher (MemoryStorage keeps FSM state in-process)
    bot = Bot(
        token=env.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    # 3. Register routers (admin first so its callbacks win over catch-alls)
    dp.include_router(admin_router)
    dp.include_router(user_router)

    # 4. Background services
    reminder_task     = start_reminder_loop(bot)
    mgr_reminder_task = start_manager_reminder_loop(bot)


    # 5. Polling
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            drop_pending_updates=True,
        )
    finally:
        reminder_task.cancel()
        mgr_reminder_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
