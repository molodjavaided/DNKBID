"""
FSM state groups for all conversation flows.
"""

from aiogram.fsm.state import State, StatesGroup


class OrderFSM(StatesGroup):
    select_location   = State()
    browse_categories = State()
    browse_items      = State()
    await_unit        = State()   # multi-unit item: pick a unit before qty
    await_qty         = State()
    await_custom_qty  = State()
    editing_item_qty  = State()   # editing a specific cart line's quantity


class AdminFSM(StatesGroup):
    await_deadline = State()

    # Location CRUD
    await_new_location_name  = State()
    await_edit_location_name = State()

    # Category CRUD
    await_new_category_name  = State()
    await_edit_category_name = State()

    # Item CRUD
    await_new_item_name   = State()   # text input → then show unit multi-select
    await_new_item_unit   = State()   # multi-select units → create item
    await_edit_item_name  = State()   # text input → rename item
    await_edit_item_unit  = State()   # multi-select units → update allowed units

    # Units CRUD
    await_new_unit_name  = State()
    await_edit_unit_name = State()

    # Reminder settings
    await_reminder_start     = State()
    await_reminder_interval  = State()
    await_dashboard_interval = State()
