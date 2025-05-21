# utils/sheets_utils.py

import os
import json
import gspread
import logging
import asyncio
import pymongo
from google.oauth2.service_account import Credentials
from functools import wraps
from database import users_col, get_collection
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
SHEET_NAME = "tushlik"
WORKSHEET_NAME = "Sheet1"


def get_creds():
    creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
    creds_dict = json.loads(creds_json)
    return Credentials.from_service_account_info(creds_dict, scopes=SCOPES)


def to_async(func):
    """Run a sync gspread call in the default executor."""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))
    return wrapper


@to_async
def _open_worksheet(sheet_name: str = WORKSHEET_NAME):
    gc = gspread.authorize(get_creds())
    sh = gc.open(SHEET_NAME)
    return sh.worksheet(sheet_name)

async def get_worksheet(sheet_name: str = WORKSHEET_NAME):
    """Async handle to any worksheet in the spreadsheet."""
    return await _open_worksheet(sheet_name)

async def find_user_in_sheet(telegram_id: int) -> dict | None:
    """Return the entire row dict for this user, or None."""
    ws = await get_worksheet()
    if not ws:
        return None
    for rec in ws.get_all_records():
        if str(rec.get("telegram_id")) == str(telegram_id):
            return rec
    return None

async def sync_balances_from_sheet(context: ContextTypes.DEFAULT_TYPE = None) -> dict:
    """
    Full‐sheet sync: read EVERY row’s `balance` and overwrite Mongo.
    """
    ws = await get_worksheet()
    if not ws:
        return {"success": False, "error": "no worksheet"}
    updated = errors = 0

    for row in ws.get_all_records():
        try:
            tid = int(row.get("telegram_id"))
            bal = float(str(row.get("balance", 0)).replace(",", ""))
            res = await users_col.update_one(
                {"telegram_id": tid},
                {"$set": {"balance": bal}}
            )
            if res.modified_count:
                updated += 1
        except Exception as e:
            logger.error("sync_balances_from_sheet error on row %r: %s", row, e)
            errors += 1

    return {"success": True, "updated": updated, "errors": errors}


async def sync_balances_incremental() -> list[int]:
    """
    Incremental sync: fetch sheet once, compare to Mongo snapshot,
    and bulk‐write only changed balances. Returns list of updated IDs.
    """
    users_collection = await get_collection("users")

    db_users = await users_collection.find(
        {}, {"telegram_id": 1, "balance": 1}
    ).to_list(length=None)
    db_map = {u["telegram_id"]: u["balance"] for u in db_users}

    ws = await get_worksheet()
    rows = ws.get_all_records()

    updates = []
    for row in rows:
        raw_id = row.get("telegram_id")
        if not raw_id:
            continue
        try:
            tg_id = int(raw_id)
            bal_sheet = float(str(row.get("balance", 0)).replace(",", ""))
        except Exception:
            continue
        bal_db = db_map.get(tg_id)
        if bal_db is not None and bal_db != bal_sheet:
            updates.append((tg_id, bal_sheet))

    if updates:
        ops = [
            pymongo.UpdateOne({"telegram_id": tg}, {"$set": {"balance": bal}})
            for tg, bal in updates
        ]
        await users_collection.bulk_write(ops)

    return [tg for tg, _ in updates]

async def get_balance_from_sheet(telegram_id: int) -> float:
    """
    Returns the latest balance from the sheet for a single user.
    """
    ws = await get_worksheet()
    if not ws:
        raise RuntimeError("Worksheet not available")
    
    for row in ws.get_all_records():
        try:
            if int(row.get("telegram_id")) == telegram_id:
                return float(str(row.get("balance", 0)).replace(",", ""))
        except Exception as e:
            logger.error(f"Error parsing row for user {telegram_id}: {e}")
            continue

    raise ValueError(f"No balance found in sheet for telegram_id={telegram_id}")

async def get_price_from_sheet(telegram_id: int) -> float:
    """
    Look up this user’s `daily_price` (column E) live from the sheet.
    """
    ws = await get_worksheet()
    cell = ws.find(str(telegram_id), in_column=2)
    raw = ws.cell(cell.row, 5).value  # 1-based
    return float(raw.replace(",", "").strip())


async def sync_prices_from_sheet(context: ContextTypes.DEFAULT_TYPE = None) -> dict:
    """
    Full‐sheet scan to update each user’s `daily_price` in Mongo.
    """
    ws = await get_worksheet()
    if not ws:
        return {"success": False, "error": "could not open worksheet"}
    updated = errors = 0

    for row in ws.get_all_records():
        try:
            tid   = int(row.get("telegram_id", 0))
            price = float(str(row.get("daily_price", 0)).replace(",", "").strip())
            res = await users_col.update_one(
                {"telegram_id": tid},
                {"$set": {"daily_price": price}}
            )
            if res.modified_count:
                updated += 1
        except Exception as e:
            logger.error("sync_prices_from_sheet error on row %r: %s", row, e)
            errors += 1

    return {"success": True, "updated": updated, "errors": errors}

from datetime import datetime
async def update_attendance_cell_in_sheet(telegram_id: int, value: int):
    """Marks a cell in the 'Attendance' sheet for today's column."""
    ws = await get_worksheet("Attendance")
    all_data = ws.get_all_records()
    headers = ws.row_values(1)
    
    # Step 1: Find user row
    row_num = None
    for idx, row in enumerate(all_data, start=2):  # Header is row 1
        if str(row.get("telegram_id")) == str(telegram_id):
            row_num = idx
            break
    if row_num is None:
        logger.warning(f"User {telegram_id} not found in Attendance sheet.")
        return
    
    # Step 2: Find today's column
    today = f"{datetime.now().month}/{datetime.now().day}"
    if today not in headers:
        ws.update_cell(1, len(headers) + 1, today)  # Add today's column if missing
        col_num = len(headers) + 1
    else:
        col_num = headers.index(today) + 1
    
    # Step 3: Write attendance
    ws.update_cell(row_num, col_num, value)

async def clear_attendance_cell_in_sheet(telegram_id: int):
    """Clears today's attendance cell for a user in the Attendance sheet."""
    await update_attendance_cell_in_sheet(telegram_id, 0)