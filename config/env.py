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


TEST_MODE: bool = os.getenv("TEST_MODE", "false").lower() == "true"

BOT_TOKEN: str       = _required("BOT_TOKEN")
BOT_USERNAME: str    = os.getenv("BOT_USERNAME", "")  # e.g., "mybot" for @mybot
MANAGER_CHAT_ID: int = int(_required("MANAGER_CHAT_ID"))
ADMIN_USER_ID: int   = int(_required("ADMIN_USER_ID"))
DATABASE_PATH: str   = os.getenv("DATABASE_PATH", "./data/bot.db")

# Seconds between reminder pings.  TEST_MODE: 60 s | Prod: REMINDER_INTERVAL_MINUTES (default 120 min)
REMINDER_INTERVAL_S: int = (
    60
    if TEST_MODE
    else int(os.getenv("REMINDER_INTERVAL_MINUTES", "120")) * 60
)

# Seconds between report sends.  TEST_MODE: 30 s | Prod: REPORT_INTERVAL_MINUTES (default 1440 min)
REPORT_INTERVAL_S: int = (
    30
    if TEST_MODE
    else int(os.getenv("REPORT_INTERVAL_MINUTES", "1440")) * 60
)
