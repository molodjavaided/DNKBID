"""
Environment configuration — mirrors src/config/env.ts.
All required vars raise at import time if missing.
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _required(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


BOT_TOKEN: str       = _required("BOT_TOKEN")
BOT_USERNAME: str    = os.getenv("BOT_USERNAME", "")  # e.g., "mybot" for @mybot
MANAGER_CHAT_ID: int = int(_required("MANAGER_CHAT_ID"))
ADMIN_USER_ID: int   = int(_required("ADMIN_USER_ID"))
DATABASE_PATH: str   = os.getenv("DATABASE_PATH", "./data/bot.db")
