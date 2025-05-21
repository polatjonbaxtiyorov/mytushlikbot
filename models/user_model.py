from datetime import datetime, timezone
import pytz
from bson.objectid import ObjectId
from database import get_collection
from config import DEFAULT_DAILY_PRICE, DEFAULT_INITIAL_BALANCE
from pymongo import ReadPreference
import logging

logger = logging.getLogger(__name__)

class User:
    def __init__(
        self,
        telegram_id: int,
        name: str,
        phone: str,
        balance: int = DEFAULT_INITIAL_BALANCE,
        daily_price: int = DEFAULT_DAILY_PRICE,
        attendance: list[str] = None,
        transactions: list[dict] = None,
        is_admin: bool = False,
        created_at: datetime = None,
        declined_days: list[str] = None,
        debt: float = 0.0,              # ← new
        _id: ObjectId = None,
        data: dict = None,
    ):
        data = data or {}

        self._id          = _id
        self.telegram_id  = data.get("telegram_id", telegram_id)
        self.name         = data.get("name", name)
        self.phone        = data.get("phone", phone)
        self.balance      = data.get("balance", balance)
        self.daily_price  = data.get("daily_price", daily_price)
        self.attendance   = data.get("attendance", attendance or [])
        self.transactions = data.get("transactions", transactions or [])
        self.is_admin     = data.get("is_admin", is_admin)
        self.created_at   = created_at or datetime.now(timezone.utc)
        self.declined_days= data.get("declined_days", declined_days or [])
        self.debt         = data.get("debt", debt)       # ← set from Mongo or default
        self.food_choices = data.get("food_choices", {})


    @classmethod
    async def create(cls, telegram_id, name, phone):
        users_col = await get_collection("users")
        existing = await users_col.find_one({"telegram_id": telegram_id})
        if existing:
            return cls(**existing)

        doc = {
            "telegram_id": telegram_id,
            "name": name,
            "phone": phone,
            "balance": DEFAULT_INITIAL_BALANCE,
            "daily_price": DEFAULT_DAILY_PRICE,
            "attendance": [],
            "transactions": [],
            "is_admin": False,
            "declined_days": [],
            "created_at": datetime.now(timezone.utc),
        }
        await users_col.insert_one(doc)
        return cls(**doc)

    @classmethod
    async def find_by_id(cls, telegram_id: int):
        users_col = await get_collection("users")
        raw = await users_col.find_one({"telegram_id": telegram_id})
        if not raw:
            return None
        return cls(**raw)

    @staticmethod
    async def find_all():
        users_col = await get_collection("users")
        async for doc in users_col.with_options(read_preference=ReadPreference.PRIMARY).find({}):
            yield User(**doc)

    async def save(self):
        users_col = await get_collection("users")
        await users_col.update_one(
            {"telegram_id": self.telegram_id},
            {"$set": {
                "name": self.name,
                "phone": self.phone,
                "balance": self.balance,
                "daily_price": self.daily_price,
                "attendance": self.attendance,
                "transactions": self.transactions,
                "is_admin": self.is_admin,
                "declined_days": self.declined_days,
            }}
        )

    def _record_txn(self, txn_type: str, amount: int, desc: str):
        """Record a transaction in‑memory; called synchronously."""
        now_iso = datetime.now(timezone.utc).isoformat()
        self.transactions.append({
            "type": txn_type,
            "amount": amount,
            "desc": desc,
            "date": now_iso
        })

    @staticmethod
    async def get_daily_food_counts(date_str: str) -> dict:
        col = await get_collection("daily_food_choices")
        pipeline = [
            {"$match": {"date": date_str}},
            {"$group": {
                "_id": "$food_choice",
                "count": {"$sum": 1},
                "users": {"$push": "$user_name"}
            }},
            {"$sort": {"count": -1}}
        ]
        result = {}
        async for doc in col.aggregate(pipeline):
            if doc["_id"]:
                result[doc["_id"]] = {
                    "count": doc["count"],
                    "users": doc["users"]
                }
        return result

    async def add_attendance(self, date_str: str, food: str = None):
        if date_str in self.attendance:
            return

        # only load these when needed
        from utils.sheets_utils import get_price_from_sheet, update_attendance_cell_in_sheet
        # 0) fetch live price
        price = await get_price_from_sheet(self.telegram_id)
        self.daily_price = price

        # 1) record attendance locally (no balance change here)
        self.attendance.append(date_str)
        self._record_txn("attendance", -price, f"Lunch on {date_str}")

        # 2) save food choice if provided
        if food:
            col = await get_collection("daily_food_choices")
            await col.update_one(
                {"telegram_id": self.telegram_id, "date": date_str},
                {"$set": {
                    "telegram_id": self.telegram_id,
                    "date": date_str,
                    "food_choice": food,
                    "user_name": self.name
                }},
                upsert=True
            )

        # 3) persist in Mongo
        await self.save()

        # 4) push only debt to Sheets (rollback on failure)
        ok = await update_attendance_cell_in_sheet(self.telegram_id, price)
        if not ok:
            # rollback in-memory & DB
            self.attendance.remove(date_str)
            self._record_txn("rollback", price, f"Rollback lunch on {date_str}")
            await self.save()
            raise RuntimeError(f"Failed to sync debt for {self.telegram_id}; rolled back")


    async def remove_attendance(self, date_str: str):
        """
        Undo attendance and subtract that daily_price from debt (Qarzlar) in Sheets.
        """
        if date_str not in self.attendance:
            return

        from utils.sheets_utils import get_price_from_sheet, clear_attendance_cell_in_sheet

        # 0) fetch live price
        price = await get_price_from_sheet(self.telegram_id)
        self.daily_price = price

        # 1) remove attendance locally (no balance change here)
        self.attendance.remove(date_str)
        await self._record_txn("cancel", price, f"Cancel lunch on {date_str}")

        # 2) remove the food-choice record
        col = await get_collection("daily_food_choices")
        await col.delete_one({"telegram_id": self.telegram_id, "date": date_str})

        # 3) persist in Mongo
        await self.save()

        # 4) push only debt decrease to Sheets (rollback on failure)
        ok = await clear_attendance_cell_in_sheet(self.telegram_id)
        if not ok:
            # rollback in-memory & DB
            self.attendance.append(date_str)
            self._record_txn("rollback", -price, f"Rollback cancel on {date_str}")
            await self.save()
            raise RuntimeError(f"Failed to sync debt rollback for {self.telegram_id}")

        
    async def decline_attendance(self, date_str: str):
        if date_str not in self.declined_days:
            self.declined_days.append(date_str)
            await self.save()

    async def remove_decline(self, date_str: str):
        if date_str in self.declined_days:
            self.declined_days.remove(date_str)
            await self.save()

    async def get_food_choice(self, date: str) -> str | None:
        """
        Returns the recorded food choice for this user on `date`,
        or None if they didn’t pick one.
        """
        col = await get_collection("daily_food_choices")
        doc = await col.find_one({"telegram_id": self.telegram_id, "date": date})
        return doc.get("food_choice") if doc else None
    @staticmethod
    async def cleanup_old_food_choices():
        tz = pytz.timezone("Asia/Tashkent")
        today = datetime.now(tz).strftime("%Y-%m-%d")
        col = await get_collection("daily_food_choices")
        await col.delete_many({"date": {"$lt": today}})

    async def change_name(self, new_name: str):
        self.name = new_name
        self._record_txn("name_change", 0, f"Name changed to {new_name}")
        await self.save()

    async def update_balance(self, amount: int, desc: str = "Balance adjustment"):
        self.balance += amount
        self._record_txn("balance", amount, desc)
        await self.save()

    async def promote_to_admin(self):
        self.is_admin = True
        self._record_txn("admin", 0, "Promoted to admin")
        await self.save()

    async def demote_from_admin(self):
        self.is_admin = False
        self._record_txn("admin", 0, "Demoted from admin")
        await self.save()
    async def set_food_choice(self, date: str, food: str) -> bool:
        """
        Record today’s food choice for this user, both in MongoDB and in-memory.

        Returns True on success, False on failure.
        """
        col = await get_collection("daily_food_choices")
        try:
            result = await col.update_one(
                {"telegram_id": self.telegram_id, "date": date},
                {
                    "$set": {
                        "food_choice": food,
                        "user_name": self.name,
                        # you could also store daily_price here if useful
                    }
                },
                upsert=True
            )
            # reflect in-memory
            self.food_choices[date] = food
            return True
        except Exception as e:
            logger.error(f"Failed to set food choice for {self.telegram_id} on {date}: {e}")
            return False

