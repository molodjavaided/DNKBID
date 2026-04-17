# CLAUDE.md — BotDnkBid_py

Telegram supply-order bot for a Chelyabinsk coffee shop network.
Baristas submit daily ingredient/supply orders via inline keyboards; a manager chat receives summaries.

---

## Quick start

```bash
# venv already exists — just activate
venv\Scripts\activate          # Windows
python bot.py
```

Requires `.env` (see `.env.example`). Migrations run automatically at startup.

**First run setup:**
1. Run the bot — it creates an empty database
2. Open Telegram and send `/admin` to the bot
3. Add locations, categories, and items via the admin panel
4. No hardcoded data — everything is flexible and configurable

No test suite — verify changes manually in Telegram.

---

## Project layout

```
bot.py                      ← entry point: migrations → Bot/Dispatcher → routers → polling
config/
  env.py                    ← env vars (BOT_TOKEN, MANAGER_CHAT_ID, ADMIN_USER_ID, etc.)
  catalog.py                ← CATALOG list, QTY_OPTIONS — single source of truth for items
db/
  database.py               ← get_db() singleton (SQLite WAL, FK ON, row_factory=Row)
  migrations.py             ← run_migrations() — schema DDL + seed, additive ALTER TABLE
  catalog.py                ← Category / Item / Unit dataclasses + full CRUD
  orders.py                 ← OrderItem / FullOrder / CartLineInput + order CRUD + draft carts
  locations.py              ← Location dataclass + CRUD
  settings.py               ← key-value settings table (orders_open, deadline_time, etc.)
  chats.py                  ← chat registry (used by reminder service)
handlers/
  states.py                 ← OrderFSM, AdminFSM (StatesGroup)
  admin.py                  ← /admin + AdminFSM callbacks (Router, registered first)
  user.py                   ← /start /order /myorders /cancel + OrderFSM (Router)
keyboards/
  admin_kb.py               ← admin inline keyboards, AdminCrudCB, AdminUnitToggleCB
  catalog_kb.py             ← CategoryCB, ItemCB, categories_kb, items_kb
  location_kb.py            ← LocationCB, locations_kb
  order_kb.py               ← QtyCB, MyOrderCB, UserItemUnitCB, quantity_kb, cart_kb
services/
  reminder_service.py       ← async background reminder loop (Asia/Yekaterinburg)
```

---

## Database schema (current)

| Table | Key columns |
|---|---|
| `locations` | `id INTEGER PK`, `name TEXT UNIQUE` |
| `categories` | `id`, `name UNIQUE`, `sort_order`, `order_days INTEGER` (bitmask Mon=bit0…Sun=bit6, default 127) |
| `items` | `id`, `category_id FK`, `name`, `sort_order`, `unit_type TEXT`, `allowed_units TEXT` (JSON array), `is_available INTEGER DEFAULT 1` |
| `units` | `id`, `name UNIQUE` — reference table, seeded: шт. уп. л кг бут. |
| `orders` | `id`, `location_id FK`, `tg_message_id`, `tg_chat_id`, `tg_user_id`, `tg_user_name`, `item_name` (summary), `quantity` (count), `status` (pending/fulfilled/rejected), timestamps |
| `order_items` | `id`, `order_id FK CASCADE`, `item_key`, `item_name`, `category_name`, `quantity REAL`, `unit TEXT`, `is_urgent INTEGER DEFAULT 0` |
| `settings` | `key TEXT PK`, `value TEXT` — keys: `orders_open`, `deadline_time`, `reminder_cron`, `report_cron`, `report_timezone` |
| `chats` | `id PK`, `title`, `is_active`, `added_at` |
| `draft_carts` | `(tg_user_id, tg_chat_id) PK`, `data TEXT` (JSON FSM snapshot), `updated_at` |

**Adding columns:** use the additive migration pattern in `run_migrations()`:
```python
for ddl in ["ALTER TABLE t ADD COLUMN col TYPE DEFAULT val"]:
    try: db.execute(ddl); db.commit()
    except: pass  # already exists
```
Never DROP or rename columns — always additive.

---

## User order FSM flow

```
/order
  └─ [draft exists?] → resume_draft_kb → resume | discard
  └─ OrderFSM.select_location     → LocationCB → cb_location
        └─ [last order exists for location?] → repeat_last_kb → repeat | skip
  └─ OrderFSM.browse_categories   → CategoryCB → cb_category
  └─ OrderFSM.browse_items        → ItemCB     → cb_item
        ├─ [single unit]  → OrderFSM.await_qty
        └─ [multi unit]   → OrderFSM.await_unit → UserItemUnitCB → OrderFSM.await_qty
  └─ OrderFSM.await_qty           → QtyCB(-1=custom) or preset
        ├─ order:toggle_urgent → toggles current_urgent in state, re-renders qty_kb
        ├─ [remaining units for same item] → back to OrderFSM.await_unit
        └─ [done]                          → back to OrderFSM.browse_items
  └─ order:view  → cart_kb
  └─ order:submit → create_order()
        → notify MANAGER_CHAT_ID (full summary)
        → if any is_urgent items → additional 🚨 urgent notification
```

**Multi-unit logic:** one item can have multiple `allowed_units` entries. After each qty is entered, `_cart_used_units()` computes which units are already in cart for that item; if unused units remain, the user is prompted to add another unit before returning to items. The cart stores one line per `(item_id, unit)` pair.

**Live cart preview:** `_items_screen_text(location, cat_name, cart)` renders a cart summary header on every `browse_items` message.

**Quick repeat:** after selecting a location, if the user has a previous order for it, `repeat_last_kb()` is shown. `order:repeat_last` loads those items into cart as a starting point. `order:skip_repeat` proceeds to empty categories screen.

**Urgency flag:** `order:toggle_urgent` toggles `current_urgent` in FSM state. The qty keyboard shows the current state. When qty is applied, `is_urgent` is written to the cart line and `current_urgent` is reset to False. On submit, any urgent lines trigger an immediate separate message to manager.

**Item availability:** `items.is_available = 0` hides an item from the user ordering menu. Admin sees all items with ✅/⛔ toggle. `get_all_items_by_category(cat_id, admin=True)` returns all; default returns only available.

---

## Admin FSM flow

```
/admin  (ADMIN_USER_ID only)
  ├─ Toggle orders open/closed
  ├─ Set/clear deadline (AdminFSM.await_deadline → HH:MM text)
  ├─ Schedule (order_days bitmask per category)
  ├─ Status (per-location missing categories today)
  └─ CRUD sections: locs | cats | items | units
       All deletes: AdminCrudCB action="del" → confirm_delete_kb → "confirm_del" | "cancel_del"
       Item add flow: await_new_item_name → await_new_item_unit (multi-select) → done
       Item unit edit: await_edit_item_unit (multi-select, AdminUnitToggleCB toggle)
       Item availability: AdminCrudCB action="toggle_avail" → toggle_item_availability() → refresh list

/avg_order  (ADMIN_USER_ID only)
  └─ Location picker (AvgOrderLocCB) → get_location_avg_orders(loc_id, last_n=10)
     → formatted message: AVG qty per (item, unit) over last 10 orders, grouped by category
```

---

## Callback data prefixes (never reuse)

| Prefix | Class | Fields |
|---|---|---|
| `ac:` | `AdminCrudCB` | `section` (locs/cats/items/units), `action`, `entity_id` |
| `aut:` | `AdminUnitToggleCB` | `unit_name` |
| `avgordloc:` | `AvgOrderLocCB` | `location_id` |
| `catday:` | `CatScheduleCB` | `cat_id` |
| `daytog:` | `DayToggleCB` | `cat_id`, `day` |
| `cat:` | `CategoryCB` | `id` |
| `item:` | `ItemCB` | `id` |
| `loc:` | `LocationCB` | `id` |
| `qty:` | `QtyCB` | `value` (-1 = custom text input) |
| `myord:` | `MyOrderCB` | `order_id`, `action` |
| `uiu:` | `UserItemUnitCB` | `unit` |

Literal callback strings: `start:order`, `adm:toggle`, `adm:menu`, `adm:status`, `adm:schedule`, `adm:set_deadline`, `adm:clear_deadline`, `adm:units_select_done`, `adm_noop`, `order:resume_draft`, `order:new_order`, `order:repeat_last`, `order:skip_repeat`, `order:toggle_urgent`, `order:back_cats`, `order:back_items`, `order:back_items_from_unit`, `order:view`, `order:clear`, `order:cancel`, `order:submit`, `myord_noop`, `myord:close`.

---

## Communication

- **Always respond in English** — all explanations, suggestions, code comments, and technical discussions.
- User-facing bot text remains in Russian (Telegram messages, button labels, etc.).

---

## Coding conventions

- **Language:** all user-facing text is Russian; code/comments in English/Russian mix is fine.
- **Parse mode:** HTML everywhere except `reminder_service.py` (uses Markdown).
- **`from __future__ import annotations`** at the top of every module.
- **DB access:** always via `get_db()` — never open a second connection. Sync SQLite, thread-safe via WAL + `check_same_thread=False`.
- **Router registration:** `admin_router` first (more specific filters), `user_router` second.
- **Admin guard:** `_is_admin(user_id)` at the top of every admin handler; early-exit with `cq.answer("⛔ Нет доступа.", show_alert=True)`.
- **Always call `await cq.answer()`** at the end of every `CallbackQuery` handler (prevents spinner hanging).
- **FSM state data keys** used across handlers (do not rename):
  `location_id`, `location_name`, `cart`, `current_category_id`, `current_category_name`,
  `current_item_id`, `current_item_name`, `current_unit`, `current_urgent`, `editing_order_id`.
- **Cart line dict keys:** `item_key` (str(item.id)), `item_name`, `category_name`, `quantity` (float), `unit`, `is_urgent` (bool, default False).
- **Cart display grouping:** `_group_cart(cart)` merges lines sharing `item_key` into one entry with `units_str = "5 уп. + 1 л."`. Use it in all display helpers — `_cart_text`, `_items_screen_text`, urgent notification. Never iterate raw cart lines for display.
- **Timezone:** `Asia/Yekaterinburg` (UTC+5). Use `zoneinfo.ZoneInfo` with `timedelta(hours=5)` fallback. Never hardcode Kiev/Kyiv.
- **No type annotations on unchanged code.** No docstrings on trivial functions. No extra error handling for impossible cases.

---

## Reminder service

- Background `asyncio.Task` started in `bot.py`, cancelled on shutdown.
- `TEST_MODE=true` → interval 60 s, skips working-hours check.
- `TEST_MODE=false` → interval `REMINDER_INTERVAL_MINUTES * 60` (default 7200 s), active 08:00–deadline.
- Deletes previous reminder message per chat before sending a new one (stored in settings table via `get/set/clear_reminder_message_id`).

---

## Environment variables

| Var | Required | Default | Purpose |
|---|---|---|---|
| `BOT_TOKEN` | ✅ | — | Telegram bot token |
| `MANAGER_CHAT_ID` | ✅ | — | Chat that receives order notifications |
| `ADMIN_USER_ID` | ✅ | — | Telegram user ID with /admin access |
| `DATABASE_PATH` | — | `./data/bot.db` | SQLite file path |
| `TEST_MODE` | — | `false` | Fast reminder interval, no hour check |
| `REMINDER_INTERVAL_MINUTES` | — | `120` | Reminder loop interval (prod) |
| `REPORT_INTERVAL_MINUTES` | — | `1440` | Report loop interval (prod) |

---

## Recurring pitfalls

1. **Never duplicate handler body.** Previously `msg_custom_qty` had its full body copy-pasted twice inside one function — the item was added to cart twice. If a handler is long, extract a helper (`_apply_qty`) and call it once.
2. **Multi-unit `allowed_units`** is stored as a JSON array in `items.allowed_units`. Always parse via `_parse_allowed_units()` in `db/catalog.py`; never read the raw column string directly.
3. **Confirm before delete.** All CRUD delete operations must go through `confirm_delete_kb(section, eid)` → `confirm_del` action. Never delete directly on the first press.
4. **Draft cart saves FSM snapshot.** `save_draft_cart` serialises the entire `state.get_data()` dict. Only call it when `not data.get("editing_order_id")` — edits of existing orders must not create drafts.
5. **Router order matters.** `admin_router` before `user_router` in `dp.include_router()`. Reverting this breaks admin callbacks.
6. **`order:back_items` callback is state-filtered** (`OrderFSM.await_qty`) — if you add it elsewhere, aiogram will drop it silently.
