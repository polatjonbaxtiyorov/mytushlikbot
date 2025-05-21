# validation_utils.py

import re
from typing import Optional, List
from telegram import ReplyKeyboardMarkup
from models.user_model import User
from database import get_collection
from datetime import datetime, timezone

_NAME_RE = re.compile(r"^[A-Za-z\u0400-\u04FF'][A-Za-z\u0400-\u04FF' ]{1,49}$")
_PHONE_RE = re.compile(r"^\+?998\d{9}$")

def validate_name(name: str) -> bool:
    return bool(_NAME_RE.fullmatch(name.strip()))

def validate_phone(phone: str) -> bool:
    # Remove any non-digit characters except + at the start
    cleaned = re.sub(r'[^\d+]', '', phone)
    # Ensure leading +
    if not cleaned.startswith('+'):
        cleaned = '+' + cleaned
    return bool(_PHONE_RE.fullmatch(cleaned))

async def any_admins_exist() -> bool:
    """Return True if any admin users exist."""
    users_col = await get_collection("users")
    count = await users_col.count_documents({"is_admin": True}, limit=1)
    return count > 0

def get_default_kb(is_admin: bool, has_food_selection: bool = False) -> ReplyKeyboardMarkup:
    """Build the standard user keyboard."""
    row1 = ["💸 Balansim", "✏️ Ism o'zgartirish"]
    row2 = ["💳 Karta Raqami", "🗓️ Qatnashuv" ]
    row3 = []
    if has_food_selection:
        row3.append("❌ Tushlikni bekor qilish")
    if is_admin:
        row1.append("🔧 Admin panel")
    return ReplyKeyboardMarkup([row1, row2], resize_keyboard=True)

async def get_user_async(telegram_id: int) -> Optional[User]:
    """Fetch a User by telegram_id (or legacy user_id) and return a User object."""
    users_col = await get_collection("users")
    doc = await users_col.find_one({
        "$or": [
            {"telegram_id": telegram_id},
            {"user_id": telegram_id},
        ]
    })
    if not doc:
        return None

    # unify on telegram_id
    t_id = doc.get("telegram_id") or doc.get("user_id")

    return User(
        telegram_id   = t_id,
        name          = doc.get("name", ""),
        phone         = doc.get("phone", ""),
        balance       = doc.get("balance", 0),
        daily_price   = doc.get("daily_price", 0),
        attendance    = doc.get("attendance", []) or [],
        transactions  = doc.get("transactions", []) or [],
        is_admin      = bool(doc.get("is_admin", False)),
        declined_days = doc.get("declined_days", []) or [],
        created_at    = doc.get("created_at") or datetime.now(timezone.utc),
        _id           = doc.get("_id"),
        # pass through all sheet‐related choices
        data          = {"food_choices": doc.get("food_choices", {})}
    )

async def get_all_users_async() -> List[User]:
    """Fetch all users and return a list of User objects."""
    users_col = await get_collection("users")
    cursor = users_col.find({})
    users: List[User] = []

    async for doc in cursor:
        t_id = doc.get("telegram_id") or doc.get("user_id")
        users.append(
            User(
                telegram_id   = t_id,
                name          = doc.get("name", ""),
                phone         = doc.get("phone", ""),
                balance       = doc.get("balance", 0),
                daily_price   = doc.get("daily_price", 0),
                attendance    = doc.get("attendance", []) or [],
                transactions  = doc.get("transactions", []) or [],
                is_admin      = bool(doc.get("is_admin", False)),
                declined_days = doc.get("declined_days", []) or [],
                created_at    = doc.get("created_at") or datetime.now(timezone.utc),
                _id           = doc.get("_id"),
                data          = {"food_choices": doc.get("food_choices", {})}
            )
        )

    return users

async def is_admin(telegram_id: int) -> bool:
    """Return True if the given telegram_id belongs to an admin."""
    users_col = await get_collection("users")
    doc = await users_col.find_one({"telegram_id": telegram_id})
    return bool(doc and doc.get("is_admin", False))
