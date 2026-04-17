"""
DB migrations + seed — mirrors src/db/migrations.ts.
Run once at startup before any other DB access.
"""

import json
import logging
from db.database import get_db
from config.catalog import CATALOG
from config.env import DATABASE_PATH  # imported only to trigger env validation early

log = logging.getLogger(__name__)

# Unit per catalog category key — used for fresh-DB seed
_CAT_KEY_UNIT: dict[str, str] = {
    "milk":   "л",
    "coffee": "кг",
    "tea":    "уп.",
    "syrup":  "бут.",
    "supply": "шт.",
    "other":  "шт.",
}

# Unit per category name — used to backfill unit_type on existing items
_CAT_NAME_UNIT: dict[str, str] = {
    "🥛 Молоко":     "л",
    "☕ Кофе":       "кг",
    "🍵 Чай":        "уп.",
    "🧴 Сиропы":     "бут.",
    "📦 Расходники": "шт.",
    "🛒 Другое":     "шт.",
}

# Default units to seed into the units reference table
_DEFAULT_UNITS = ["шт.", "уп.", "л", "кг", "бут."]


def run_migrations() -> None:
    db = get_db()

    db.executescript("""
        -- ── Locations ─────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS locations (
            id   INTEGER PRIMARY KEY,
            name TEXT    NOT NULL UNIQUE
        );

        -- ── Orders ────────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS orders (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            location_id    INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
            tg_message_id  INTEGER NOT NULL,
            tg_chat_id     INTEGER NOT NULL,
            tg_user_id     INTEGER,
            tg_user_name   TEXT,
            item_name      TEXT    NOT NULL,
            quantity       REAL    NOT NULL,
            status         TEXT    NOT NULL DEFAULT 'pending'
                             CHECK(status IN ('pending','fulfilled','rejected')),
            created_at     TEXT    NOT NULL
                             DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            updated_at     TEXT    NOT NULL
                             DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            reported_at    TEXT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_orders_tg_dedup
            ON orders(tg_chat_id, tg_message_id);

        CREATE INDEX IF NOT EXISTS idx_orders_location_status
            ON orders(location_id, status);

        -- ── Order Items ───────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS order_items (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id      INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            item_key      TEXT    NOT NULL,
            item_name     TEXT    NOT NULL,
            category_name TEXT    NOT NULL,
            quantity      REAL    NOT NULL,
            unit          TEXT    NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_order_items_order_id
            ON order_items(order_id);

        -- ── Settings ──────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS settings (
            key        TEXT PRIMARY KEY,
            value      TEXT NOT NULL,
            updated_at TEXT NOT NULL
                         DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );

        -- ── Categories ────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS categories (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT    NOT NULL UNIQUE,
            sort_order INTEGER NOT NULL DEFAULT 0
        );

        -- ── Items ─────────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            category_id INTEGER NOT NULL REFERENCES categories(id),
            name        TEXT    NOT NULL,
            sort_order  INTEGER NOT NULL DEFAULT 0,
            UNIQUE(category_id, name)
        );

        CREATE INDEX IF NOT EXISTS idx_items_category_id
            ON items(category_id);

        -- ── Units reference table ─────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS units (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        );

        -- ── Chats ─────────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS chats (
            id        INTEGER PRIMARY KEY,
            title     TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            added_at  TEXT    NOT NULL
                        DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
        );

        -- ── Draft Carts ───────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS draft_carts (
            tg_user_id INTEGER NOT NULL,
            tg_chat_id INTEGER NOT NULL,
            data       TEXT    NOT NULL,
            updated_at TEXT    NOT NULL
                          DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
            PRIMARY KEY (tg_user_id, tg_chat_id)
        );
    """)

    # Recreate orders table with ON DELETE CASCADE if needed (fix for existing DBs)
    try:
        # Check if the constraint already exists by attempting to drop it
        # SQLite doesn't support direct FK updates, so we recreate the table
        tables_info = db.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='orders'"
        ).fetchone()
        if tables_info and "ON DELETE CASCADE" not in (tables_info["sql"] or ""):
            log.info("[DB] Recreating orders table with ON DELETE CASCADE...")
            db.executescript("""
                BEGIN TRANSACTION;
                ALTER TABLE orders RENAME TO orders_old;

                CREATE TABLE orders (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    location_id    INTEGER NOT NULL REFERENCES locations(id) ON DELETE CASCADE,
                    tg_message_id  INTEGER NOT NULL,
                    tg_chat_id     INTEGER NOT NULL,
                    tg_user_id     INTEGER,
                    tg_user_name   TEXT,
                    item_name      TEXT    NOT NULL,
                    quantity       REAL    NOT NULL,
                    status         TEXT    NOT NULL DEFAULT 'pending'
                                     CHECK(status IN ('pending','fulfilled','rejected')),
                    created_at     TEXT    NOT NULL
                                     DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    updated_at     TEXT    NOT NULL
                                     DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
                    reported_at    TEXT
                );

                INSERT INTO orders SELECT * FROM orders_old;
                DROP TABLE orders_old;

                CREATE UNIQUE INDEX idx_orders_tg_dedup
                    ON orders(tg_chat_id, tg_message_id);
                CREATE INDEX idx_orders_location_status
                    ON orders(location_id, status);

                COMMIT;
            """)
            log.info("[DB] orders table recreated with ON DELETE CASCADE.")
    except Exception as e:
        log.warning("[DB] Could not migrate orders table: %s", e)
        db.rollback()

    # Fix broken FK in order_items caused by SQLite auto-updating REFERENCES
    # when orders was renamed to orders_old during the CASCADE migration above.
    oi_info = db.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='order_items'"
    ).fetchone()
    if oi_info and "orders_old" in (oi_info["sql"] or ""):
        log.info("[DB] Fixing order_items FK (orders_old → orders)...")
        db.executescript("""
            PRAGMA foreign_keys = OFF;
            BEGIN TRANSACTION;
            ALTER TABLE order_items RENAME TO order_items_old;

            CREATE TABLE order_items (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id      INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
                item_key      TEXT    NOT NULL,
                item_name     TEXT    NOT NULL,
                category_name TEXT    NOT NULL,
                quantity      REAL    NOT NULL,
                unit          TEXT    NOT NULL,
                is_urgent     INTEGER NOT NULL DEFAULT 0
            );

            INSERT INTO order_items SELECT * FROM order_items_old;
            DROP TABLE order_items_old;

            CREATE INDEX IF NOT EXISTS idx_order_items_order_id
                ON order_items(order_id);

            COMMIT;
            PRAGMA foreign_keys = ON;
        """)
        log.info("[DB] order_items FK fixed.")

    # Additive column migrations — safe to run on existing DBs
    for ddl in [
        "ALTER TABLE orders ADD COLUMN tg_user_name TEXT",
        "ALTER TABLE categories ADD COLUMN order_days INTEGER NOT NULL DEFAULT 127",
        "ALTER TABLE items ADD COLUMN unit_type TEXT NOT NULL DEFAULT 'шт.'",
        "ALTER TABLE items ADD COLUMN allowed_units TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE items ADD COLUMN is_available INTEGER NOT NULL DEFAULT 1",
        "ALTER TABLE order_items ADD COLUMN is_urgent INTEGER NOT NULL DEFAULT 0",
    ]:
        try:
            db.execute(ddl)
            db.commit()
        except Exception:
            pass  # column already exists

    # Backfill unit_type for existing items based on category name
    for cat_name, unit in _CAT_NAME_UNIT.items():
        db.execute(
            """
            UPDATE items SET unit_type = ?
            WHERE category_id IN (SELECT id FROM categories WHERE name = ?)
              AND unit_type = 'шт.'
            """,
            (unit, cat_name),
        )

    # Backfill allowed_units = [unit_type] for items where allowed_units is still empty
    rows = db.execute(
        "SELECT id, unit_type FROM items WHERE allowed_units = '[]'"
    ).fetchall()
    for row in rows:
        units_json = json.dumps([row["unit_type"] or "шт."], ensure_ascii=False)
        db.execute("UPDATE items SET allowed_units = ? WHERE id = ?", (units_json, row["id"]))

    db.commit()

    # Seed units reference table
    for unit_name in _DEFAULT_UNITS:
        db.execute("INSERT OR IGNORE INTO units (name) VALUES (?)", (unit_name,))

    # Seed default settings
    defaults = [
        ("orders_open",              "1"),
        ("deadline_time",            ""),
        ("reminder_cron",            "* * * * *"),
        ("report_cron",              "0 9 * * *"),
        ("report_timezone",          "Asia/Yekaterinburg"),
        ("mgr_reminder_start",       "08:00"),
        ("mgr_reminder_deadline",    "14:00"),
        ("mgr_reminder_interval_min","60"),
        ("mgr_reminder_last_msg_id", ""),
    ]
    db.executemany(
        "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)",
        defaults,
    )

    # Update existing timezone setting if it still points to Kiev
    db.execute(
        "UPDATE settings SET value = 'Asia/Yekaterinburg' WHERE key = 'report_timezone' AND value LIKE '%Kiev%'"
    )

    # Seed catalog (only on first run — when categories table is empty)
    (cat_count,) = db.execute("SELECT COUNT(*) FROM categories").fetchone()
    if cat_count == 0:
        for sort_i, cat in enumerate(CATALOG):
            cursor = db.execute(
                "INSERT INTO categories (name, sort_order) VALUES (?, ?)",
                (cat.name, sort_i),
            )
            cat_id = cursor.lastrowid
            unit = _CAT_KEY_UNIT.get(cat.key, "шт.")
            units_json = json.dumps([unit], ensure_ascii=False)
            db.executemany(
                "INSERT INTO items (category_id, name, sort_order, unit_type, allowed_units) VALUES (?, ?, ?, ?, ?)",
                [(cat_id, item.name, item_i, unit, units_json) for item_i, item in enumerate(cat.items)],
            )
        log.info("[DB] Catalog seeded.")

    db.commit()
    log.info("[DB] Migrations complete.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_migrations()
