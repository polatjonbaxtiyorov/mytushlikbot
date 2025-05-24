# handlers/admin_handlers.py
import re
import logging
from datetime import datetime, timezone
import pytz

from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
)

from database import get_collection
from utils.sheets_utils import get_worksheet, sync_prices_from_sheet, sync_balances_incremental
from utils import get_all_users_async, get_user_async, is_admin, get_default_kb
from models.user_model import User
from config import DEFAULT_DAILY_PRICE

menu_col = None
users_col = None
logger = logging.getLogger(__name__)

# ─── STATES ────────────────────────────────────────────────────────────────────
(
    S_ADD_ADMIN,      # selecting user to promote
    S_REMOVE_ADMIN,   # selecting admin to demote
    S_DELETE_USER,    # selecting user to delete
) = range(3)

# ─── BUTTON LABELS ─────────────────────────────────────────────────────────────
FOYD_BTN         = "Foydalanuvchilar"
ADD_ADMIN_BTN    = "Admin Qo'shish"
REMOVE_ADMIN_BTN = "Admin Olish"
DELETE_USER_BTN  = "Foydalanuvchini O‘chirish"
MENU_BTN         = "🍽 Menyu"
BACK_BTN         = "Ortga"
KASSA_BTN        = "Kassa"

# ─── MENU SUB‑BUTTONS ──────────────────────────────────────────────────────────
VIEW_MENU1_BTN = "1‑Menuni Ko'rish"
VIEW_MENU2_BTN = "2‑Menuni Ko'rish"
VIEW_MENU3_BTN = "3‑Menuni Ko'rish"
VIEW_MENU4_BTN = "4‑Menuni Ko'rish"

ADD_MENU1_BTN  = "1‑Menuga Qo'shish"
ADD_MENU2_BTN  = "2‑Menuga Qo'shish"
ADD_MENU3_BTN  = "3‑Menuga Qo'shish"
ADD_MENU4_BTN  = "4‑Menuga Qo'shish"

DEL_MENU1_BTN  = "1‑Menudan O'chirish"
DEL_MENU2_BTN  = "2‑Menudan O'chirish"
DEL_MENU3_BTN  = "3‑Menudan O'chirish"
DEL_MENU4_BTN  = "4‑Menudan O'chirish"

# ─── ADMIN PANEL KEYBOARD ──────────────────────────────────────────────────────
async def init_collections():
    """Initialize the menu collection and ensure all 4 menus exist."""
    global menu_col, users_col
    menu_col  = await get_collection("menu")
    users_col = await get_collection("users")
    
    # Initialize all 4 menus with descriptions
    menu_configs = {
        "menu1": {"items": [], "description": "Juft hafta - Toq kunlar"},
        "menu2": {"items": [], "description": "Juft hafta - Juft kunlar"},
        "menu3": {"items": [], "description": "Toq hafta - Toq kunlar"},
        "menu4": {"items": [], "description": "Toq hafta - Juft kunlar"}
    }
    
    for name, config in menu_configs.items():
        existing = await menu_col.find_one({"name": name})
        if not existing:
            await menu_col.insert_one({
                "name": name, 
                "items": config["items"],
                "description": config["description"]
            })

def get_menu_kb():
    """Alternative keyboard using the defined button constants."""
    current_menus = get_current_week_menus()
    
    # Map current menus to appropriate buttons
    if current_menus == ("menu1", "menu2"):
        current_view = [VIEW_MENU1_BTN, VIEW_MENU2_BTN]
        current_add = [ADD_MENU1_BTN, ADD_MENU2_BTN]
        current_del = [DEL_MENU1_BTN, DEL_MENU2_BTN]
        next_view = [VIEW_MENU3_BTN, VIEW_MENU4_BTN]
        next_add = [ADD_MENU3_BTN, ADD_MENU4_BTN]
        next_del = [DEL_MENU3_BTN, DEL_MENU4_BTN]
    else:
        current_view = [VIEW_MENU3_BTN, VIEW_MENU4_BTN]
        current_add = [ADD_MENU3_BTN, ADD_MENU4_BTN]
        current_del = [DEL_MENU3_BTN, DEL_MENU4_BTN]
        next_view = [VIEW_MENU1_BTN, VIEW_MENU2_BTN]
        next_add = [ADD_MENU1_BTN, ADD_MENU2_BTN]
        next_del = [DEL_MENU1_BTN, DEL_MENU2_BTN]
    
    keyboard = [
        # Current week
        [InlineKeyboardButton("📅 Joriy hafta", callback_data="separator")],
        [InlineKeyboardButton(current_view[0], callback_data=f"view_{current_menus[0]}"),
         InlineKeyboardButton(current_view[1], callback_data=f"view_{current_menus[1]}")],
        [InlineKeyboardButton(current_add[0], callback_data=f"add_{current_menus[0]}"),
         InlineKeyboardButton(current_add[1], callback_data=f"add_{current_menus[1]}")],
        [InlineKeyboardButton(current_del[0], callback_data=f"del_{current_menus[0]}"),
         InlineKeyboardButton(current_del[1], callback_data=f"del_{current_menus[1]}")],
        
        # Next week
        [InlineKeyboardButton("📅 Keyingi hafta", callback_data="separator")],
        [InlineKeyboardButton(next_view[0], callback_data=f"view_{'menu3' if current_menus[0] != 'menu3' else 'menu1'}"),
         InlineKeyboardButton(next_view[1], callback_data=f"view_{'menu4' if current_menus[1] != 'menu4' else 'menu2'}")],
        [InlineKeyboardButton(next_add[0], callback_data=f"add_{'menu3' if current_menus[0] != 'menu3' else 'menu1'}"),
         InlineKeyboardButton(next_add[1], callback_data=f"add_{'menu4' if current_menus[1] != 'menu4' else 'menu2'}")],
        [InlineKeyboardButton(next_del[0], callback_data=f"del_{'menu3' if current_menus[0] != 'menu3' else 'menu1'}"),
         InlineKeyboardButton(next_del[1], callback_data=f"del_{'menu4' if current_menus[1] != 'menu4' else 'menu2'}")],
        
        [InlineKeyboardButton("🔙 Orqaga", callback_data="menu_back")]
    ]
    
    return InlineKeyboardMarkup(keyboard)

def get_admin_kb():
    return ReplyKeyboardMarkup([
        [FOYD_BTN, MENU_BTN],
        [ADD_ADMIN_BTN, REMOVE_ADMIN_BTN],
        [DELETE_USER_BTN, KASSA_BTN],
        [BACK_BTN],
    ], resize_keyboard=True)  

# ─── 1) /admin ENTRY & FIRST-TIME SETUP ────────────────────────────────────────
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /admin or “Ortga” from other admin flows: assign first admin if needed, then show panel."""
    is_callback = bool(update.callback_query)
    tg_id = update.effective_user.id

    # Ensure users_col is initialized
    global users_col
    if users_col is None:
        users_col = await get_collection("users")

    # First admin bootstrapping
    admin_exists = await users_col.count_documents({"is_admin": True}, limit=1) > 0
    if not admin_exists:
        await users_col.update_one(
            {"telegram_id": tg_id},
            {
                "$setOnInsert": {
                    "telegram_id": tg_id,
                    "name": update.effective_user.full_name,
                    "phone": "",
                    "balance": 0,
                    "daily_price": DEFAULT_DAILY_PRICE,
                    "attendance": [],
                    "transactions": [],
                    "food_choices": {},
                },
                "$set": {"is_admin": True},
            },
            upsert=True,
        )
        # Acknowledge first‐admin creation
        if is_callback:
            await update.callback_query.answer()
            # delete any old inline message
            try:
                await update.callback_query.message.delete()
            except BadRequest:
                pass
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="✅ Siz birinchi admin bo‘ldingiz!"
        )

    # Fetch and show panel
    user = await users_col.find_one({"telegram_id": tg_id})
    if user and user.get("is_admin", False):
        text, kb = "🔧 Admin panelga xush kelibsiz:", get_admin_kb()
    else:
        text, kb = "❌ Siz admin emassiz!", None

    # If invoked by callback, answer + delete old message
    if is_callback:
        await update.callback_query.answer()
        try:
            await update.callback_query.message.delete()
        except BadRequest:
            pass
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=kb
        )
    else:
        await update.message.reply_text(text, reply_markup=kb)

    return ConversationHandler.END


# ─── 2) BACK TO MAIN MENU ───────────────────────────────────────────────────────
async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to the main menu with the correct reply keyboard."""
    from utils import get_default_kb

    tg_id = update.effective_user.id
    user = await users_col.find_one({"telegram_id": tg_id})
    is_admin = bool(user and user.get("is_admin", False))
    kb = get_default_kb(is_admin)
    text = "Bosh menyu:"

    if update.callback_query:
        await update.callback_query.answer()
        # Delete the current inline‐keyboard message
        try:
            await update.callback_query.message.delete()
        except BadRequest:
            pass
        # Send a fresh reply with the reply‐keyboard
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=kb
        )
    else:
        await update.message.reply_text(text, reply_markup=kb)

    return ConversationHandler.END

# ─── 3) LIST USERS ──────────────────────────────────────────────────────────────

def format_users_list(users: list[User]) -> str:
    if not users:
        return "Hech qanday foydalanuvchi yo‘q."
    lines = [
        f"• *{u.name}* (ID: {u.telegram_id})\n"
        f"   💰 Balans: *{u.balance:,}* so‘m | 📝 Narx: *{u.daily_price:,}* so‘m"
        for u in users
    ]
    return "\n\n".join(lines)

from telegram.constants import ParseMode
from utils.sheets_utils import sync_balances_incremental

async def list_users_exec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user list, syncing prices then balances from Sheets first."""
    try:
        # 1) Sync daily_price column
        await update.message.reply_text("⏳ Narxlar yangilanmoqda…")
        price_sync = await sync_prices_from_sheet()
        # optional: you can tell admin how many changed:
        # await update.message.reply_text(f"✅ {price_sync['updated']} ta narx yangilandi.")
        
        # 2) Fetch all users (name, telegram_id, balance, daily_price)
        cursor = users_col.find(
            {}, 
            {"telegram_id": 1, "name": 1, "balance": 1, "daily_price": 1}
        )
        mongo_users = await cursor.to_list(length=None)

        # 3) Sync balances
        await update.message.reply_text("⏳ Balanslar yangilanmoqda…")
        bal_updated = await sync_balances_incremental()

        # 4) Re-fetch any balances that changed
        if bal_updated:
            ids = [u["telegram_id"] for u in mongo_users]
            fresh = await users_col.find(
                {"telegram_id": {"$in": ids}},
                {"telegram_id": 1, "balance": 1}
            ).to_list(length=None)
            bal_map = {u["telegram_id"]: u["balance"] for u in fresh}
            for u in mongo_users:
                if u["telegram_id"] in bal_map:
                    u["balance"] = bal_map[u["telegram_id"]]

        # 5) Build and send the list safely
        if mongo_users:
            lines = []
            for u in mongo_users:
                bal = u.get("balance", 0) or 0
                price = u.get("daily_price", 0) or 0
                # guard non-numeric:
                try:
                    bal = float(bal)
                except:
                    bal = 0
                try:
                    price = float(price)
                except:
                    price = 0
                lines.append(
                    f"• *{u['name']}* (ID: {u['telegram_id']})\n"
                    f"   💰 Balans: *{bal:,.0f}* so‘m | 📝 Narx: *{price:,.0f}* so‘m"
                )
            text = "\n\n".join(lines)
        else:
            text = "Hech qanday foydalanuvchi yo‘q."

        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
        # 6) Return to admin panel
        await update.message.reply_text("🔧 Admin panel:", reply_markup=get_admin_kb())

    except Exception as e:
        logger.error(f"Error in list_users_exec: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ Xatolik yuz berdi.",
            reply_markup=get_admin_kb()
        )

    return ConversationHandler.END
# ─── 4) ADMIN PROMOTION / DEMOTION ─────────────────────────────────────────────

async def start_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # get the message container (either query.message or update.message)
    msg = update.callback_query.message if update.callback_query else update.message

    users = await users_col.find({"is_admin": False}).to_list(length=None)
    if not users:
        return await msg.reply_text("Barcha foydalanuvchilar allaqachon admin!", reply_markup=get_admin_kb())

    keyboard = [
        [InlineKeyboardButton(u["name"], callback_data=f"add_admin:{u['telegram_id']}")]
        for u in users
    ]
    keyboard.append([InlineKeyboardButton("Ortga", callback_data="back_to_admin")])

    await msg.reply_text(
        "Admin qilmoqchi bo'lgan foydalanuvchini tanlang:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ─── Promote to admin ──────────────────────────────────────────────────────────
async def add_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "back_to_menu":
        # delete the inline menu and show admin panel
        await query.message.delete()
        await query.message.reply_text("🔧 Admin panelga qaytdingiz.", reply_markup=get_admin_kb())
        return

    # data is "add_admin:<id>"
    user_id = int(query.data.split(":", 1)[1])
    await users_col.update_one({"telegram_id": user_id}, {"$set": {"is_admin": True}})
    user = await users_col.find_one({"telegram_id": user_id})

    # update inline menu to confirm
    await query.message.edit_text(f"✅ {user['name']} admin qilindi!")
    # re‑display the promotion list
    await start_add_admin(update, context)

async def start_remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of admin users to demote, with a working back button."""
    # Determine where to send replies
    msg = update.callback_query.message if update.callback_query else update.message

    # Fetch current admins
    admins = await users_col.find({"is_admin": True}).to_list(length=None)
    if not admins:
        return await msg.reply_text(
            "Adminlar mavjud emas!",
            reply_markup=get_admin_kb()
        )

    # Build inline keyboard
    keyboard = [
        [InlineKeyboardButton(a["name"], callback_data=f"remove_admin:{a['telegram_id']}")]
        for a in admins
    ]
    keyboard.append([InlineKeyboardButton("Ortga", callback_data="back_to_admin")])

    await msg.reply_text(
        "Adminlikdan olib tashlamoqchi bo'lgan foydalanuvchini tanlang:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ─── Demote from admin ─────────────────────────────────────────────────────────
async def remove_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "back_to_menu":
        await query.message.delete()
        await query.message.reply_text("🔧 Admin panelga qaytdingiz.", reply_markup=get_admin_kb())
        return

    user_id = int(query.data.split(":", 1)[1])
    await users_col.update_one({"telegram_id": user_id}, {"$set": {"is_admin": False}})
    user = await users_col.find_one({"telegram_id": user_id})

    await query.message.edit_text(f"✅ {user['name']} adminlikdan olib tashlandi!")
    await start_remove_admin(update, context)

# ─── 6) DELETE USER ─────────────────────────────────────────────────────────────

async def start_delete_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show list of users for deletion."""
    # pick the right message object
    msg = update.callback_query.message if update.callback_query else update.message

    users = await users_col.find().to_list(length=None)
    if not users:
        return await msg.reply_text("Hech qanday foydalanuvchi yo‘q.", reply_markup=get_admin_kb())

    keyboard = [
        [InlineKeyboardButton(u["name"], callback_data=f"delete_user:{u['telegram_id']}")]
        for u in users
    ]
    # use the same back callback as your other panels
    keyboard.append([InlineKeyboardButton(BACK_BTN, callback_data="back_to_admin")])

    text = "O‘chirmoqchi bo‘lgan foydalanuvchini tanlang:"
    if update.callback_query:
        await msg.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await msg.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


# ─── Delete a user ─────────────────────────────────────────────────────────────
async def delete_user_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "back_to_menu":
        # go back
        await query.message.delete()
        await query.message.reply_text("🔧 Admin panelga qaytdingiz.", reply_markup=get_admin_kb())
        return

    user_id = int(query.data.split(":", 1)[1])
    user = await users_col.find_one({"telegram_id": user_id})
    if not user:
        await query.message.edit_text("❌ Foydalanuvchi topilmadi.", reply_markup=get_menu_kb())
        return

    # clean up
    await (await get_collection("daily_food_choices")).delete_many({"telegram_id": user_id})
    await users_col.delete_one({"telegram_id": user_id})

    # confirm and then show panel
    await query.message.delete()
    await query.message.reply_text(
        f"✅ {user['name']} muvaffaqiyatli o‘chirildi!\n🔧 Admin panelga qaytdingiz.",
        reply_markup=get_admin_kb()
    )

async def show_kassa(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current kassa amount from Google Sheets and save to DB."""
    try:
        await update.message.reply_text("⌛️ Kassa tekshirilmoqda…")
        # 1) Fetch the worksheet
        worksheet = await get_worksheet()
        if not worksheet:
            await update.message.reply_text("❌ Google Sheets bilan bog'lanishda xatolik yuz berdi.")
            return

        # 2) Read the kassa cell (D2)
        raw = worksheet.cell(2, 4).value  # row 2, col 4
        if not raw:
            await update.message.reply_text("❌ Kassa miqdori topilmadi.")
            return

        # 3) Parse as float
        try:
            kassa_value = float(str(raw).replace(',', '').strip())
        except ValueError:
            await update.message.reply_text("❌ Kassa miqdorini o'qishda xatolik.")
            return

        # 4) Save to MongoDB (single-document "kassa" collection)
        kassa_col = await get_collection("kassa")
        await kassa_col.update_one(
            {},
            {"$set": {
                "amount": kassa_value,
                "last_updated": datetime.now(timezone.utc)
            }},
            upsert=True
        )

        # 5) Send result back to admin with the admin keyboard
        text = f"💰 *Kassa miqdori:* {kassa_value:,.0f} so‘m"
        await update.message.reply_text(
            text,
            parse_mode='Markdown',
            reply_markup=get_admin_kb()
        )

    except Exception as e:
        logger.error(f"Error in show_kassa: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
            reply_markup=get_admin_kb()
        )

async def notify_response_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle user responses to notifications"""
    query = update.callback_query
    await query.answer()
    
    # Parse the callback data
    _, response, user_id = query.data.split(':')
    user_id = int(user_id)
    
    # Get the user who responded
    user = await get_user_async(user_id)
    if not user:
        return
    
    # Get the notification responses from context
    if 'notify_responses' not in context.user_data:
        return
    
    responses = context.user_data['notify_responses']
    user_info = f"{user.name} ({user.telegram_id})"
    
    # Update the response tracking
    if response == 'yes':
        if user_info not in responses['yes']:
            responses['yes'].append(user_info)
    else:  # response == 'no'
        if user_info not in responses['no']:
            responses['no'].append(user_info)
    
    # Edit the message to remove the buttons
    await query.message.edit_text(
        f"{query.message.text}\n\n✅ Javobingiz qabul qilindi."
    )

# ─── MENU MANAGEMENT ───────────────────────────────────────────────────────
def get_current_week_menus():
    """Determine which pair of menus to use based on current week."""
    # Get current week number (ISO week)
    current_week = datetime.datetime.now().isocalendar()[1]
    
    # Even weeks use menu1/menu2, odd weeks use menu3/menu4
    if current_week % 2 == 0:
        return ("menu1", "menu2")
    else:
        return ("menu3", "menu4")

def get_menu_for_today():
    """Get the appropriate menu for today based on day and week."""
    odd_menu, even_menu = get_current_week_menus()
    
    # Get current day (1=Monday, 7=Sunday)
    current_day = datetime.datetime.now().isoweekday()
    
    # Odd days (Mon, Wed, Fri, Sun) vs Even days (Tue, Thu, Sat)
    if current_day in [1, 3, 5, 7]:  # Odd days
        return odd_menu
    else:  # Even days
        return even_menu

async def menu_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the main menu management panel."""
    if menu_col is None:
        await init_collections()
    
    kb = get_menu_kb()
    
    # Show current week info
    current_week = datetime.datetime.now().isocalendar()[1]
    week_type = "Juft hafta" if current_week % 2 == 0 else "Toq hafta"
    current_menus = get_current_week_menus()
    today_menu = get_menu_for_today()
    
    text = f"""🍽 Menyu boshqaruvi:

📅 Joriy: {week_type} (hafta #{current_week})
🔄 Hozirgi menyu juftligi: {current_menus[0]} / {current_menus[1]}
📍 Bugungi menyu: {today_menu}"""
    
    if update.message:
        await update.message.reply_text(text, reply_markup=kb)
    else:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=kb)
        except BadRequest as e:
            if "Message is not modified" not in str(e):
                raise

async def view_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, menu_name: str):
    """List all items in the specified menu."""
    if menu_col is None:
        await init_collections()
    
    query = update.callback_query
    await query.answer()
    
    doc = await menu_col.find_one({"name": menu_name})
    items = doc.get("items", []) if doc else []
    
    # Add status indicator
    current_menus = get_current_week_menus()
    today_menu = get_menu_for_today()
    
    status = ""
    if menu_name in current_menus:
        status = " (joriy hafta)"
    if menu_name == today_menu:
        status += " 🔥 (bugun)"
    
    text = f"🍽 {menu_name}{status} taomlari:\n\n" + ("\n".join(f"• {i}" for i in items) or "— Bo'sh")
    
    await query.message.edit_text(text, reply_markup=get_menu_kb())

async def add_menu_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, menu_name: str):
    """Ask admin to type a new item for the specified menu."""
    if menu_col is None:
        await init_collections()
    
    query = update.callback_query
    await query.answer()
    
    context.user_data["pending_menu_add"] = menu_name
    
    await query.message.edit_text(
        f"Yangi taom nomini kiriting ({menu_name}):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Orqaga", callback_data="menu_back")]])
    )

async def handle_menu_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the text input for a new menu item."""
    menu_name = context.user_data.pop("pending_menu_add", None)
    if not menu_name:
        return  # no menu in progress
    
    food = update.message.text.strip()
    if not food:
        await update.message.reply_text("❌ Bo'sh nom bo'lmaydi.", reply_markup=get_menu_kb())
        return
    
    await menu_col.update_one({"name": menu_name}, {"$addToSet": {"items": food}}, upsert=True)
    await update.message.reply_text(f"✅ «{food}» {menu_name} ga qo'shildi!", reply_markup=get_menu_kb())

async def del_menu_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE, menu_name: str):
    """Show inline buttons to delete an existing item."""
    if menu_col is None:
        await init_collections()
    
    query = update.callback_query
    await query.answer()
    
    doc = await menu_col.find_one({"name": menu_name})
    items = doc.get("items", []) if doc else []
    
    if not items:
        await query.message.edit_text(f"❌ {menu_name} bo'sh.", reply_markup=get_menu_kb())
        return
    
    kb = [[InlineKeyboardButton(i, callback_data=f"del_{menu_name}:{i}")] for i in items]
    kb.append([InlineKeyboardButton("🔙 Orqaga", callback_data="menu_back")])
    
    await query.message.edit_text(f"{menu_name} dan o'chirish:", reply_markup=InlineKeyboardMarkup(kb))

async def handle_menu_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Perform the deletion of a menu item."""
    query = update.callback_query
    await query.answer()
    
    data = query.data  # e.g. "del_menu1:Qovurma Lag'mon"
    _, rest = data.split("_", 1)
    menu_name, food = rest.split(":", 1)
    
    await menu_col.update_one({"name": menu_name}, {"$pull": {"items": food}})
    await query.message.edit_text(f"✅ «{food}» {menu_name} dan o'chirildi.", reply_markup=get_menu_kb())

async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dispatch menu panel callbacks to the correct helper."""
    if menu_col is None:
        await init_collections()
    
    query = update.callback_query
    await query.answer()
    data = query.data

    # Handle separator (do nothing)
    if data == "separator":
        return

    # View menu callbacks
    if data.startswith("view_"):
        menu_name = data.replace("view_", "")
        await view_menu(update, context, menu_name)
    
    # Add menu callbacks
    elif data.startswith("add_"):
        menu_name = data.replace("add_", "")
        await add_menu_prompt(update, context, menu_name)
    
    # Delete menu callbacks
    elif data.startswith("del_") and ":" not in data:
        menu_name = data.replace("del_", "")
        await del_menu_prompt(update, context, menu_name)
    
    # Handle specific delete item callbacks
    elif data.startswith("del_") and ":" in data:
        await handle_menu_del(update, context)
    
    # Back button
    elif data == "menu_back":
        try:
            await query.message.delete()
        except BadRequest:
            await update.callback_query.message.reply_text("🔧 Admin panelga qaytdingiz.", reply_markup=get_admin_kb())
    
    else:
        await query.message.edit_text("❌ Noma'lum buyruq.", reply_markup=get_menu_kb())

async def send_final_summary(context: ContextTypes.DEFAULT_TYPE):
    """Send final summary of broadcast at 10:00 AM."""
    job = context.job
    chat_id = job.data.get("chat_id")
    if not chat_id or "notify_responses" not in context.user_data:
        return

    resp = context.user_data["notify_responses"]
    total   = resp.get("total_sent", 0)
    yes     = len(resp.get("yes", []))
    no      = len(resp.get("no", []))
    pending = total - yes - no

    lines = [
        "📊 Xabar yuborish yakuniy natijalari:",
        f"👥 Jami yuborilgan: {total}",
        f"✅ Ha: {yes}",
        f"❌ Yoʻq: {no}",
        f"⏳ Javob bermaganlar: {pending}",
    ]
    if resp.get("failed"):
        lines.append(f"⚠️ Yuborilmadi: {len(resp['failed'])}")

    await context.bot.send_message(chat_id, "\n".join(lines))

    # clean up
    context.user_data.pop("notify_responses", None)
    context.user_data.pop("notify_message_id", None)


# ─── 9b) Daily lunch summary & deduction ────────────────────────────────────
async def send_summary(context: ContextTypes.DEFAULT_TYPE):
    """
    Send daily attendance summary to all admins and users, then deduct balances.
    Scheduled at 10:00 Asia/Tashkent.
    """
    tz    = pytz.timezone("Asia/Tashkent")
    now   = datetime.now(tz)
    today = now.strftime("%Y-%m-%d")
    
    cancelled = await get_collection("cancelled_lunches")
    if await cancelled.find_one({"date": today}):
        # do nothing today
        return

    # skip weekends
    if now.weekday() >= 5:
        return

    users = await get_all_users_async()
    attendees, attendee_details, declined, pending = [], [], [], []

    # categorize
    for u in users:
        if today in u.attendance:
            attendees.append(u)
            choice = await u.get_food_choice(today)
            attendee_details.append((u.name, choice))
        elif today in u.declined_days:
            declined.append(u.name)
        else:
            pending.append(u.name)

    # aggregate counts
    counts = await User.get_daily_food_counts(today)
    most   = []
    if counts:
        max_count = max(d["count"] for d in counts.values())
        tied = [f for f, d in counts.items() if d["count"] == max_count]
        most = sorted(tied) if len(tied) > 1 else [tied[0]]

    # build admin summary
    admin_lines = [
        "📊 *Bugungi tushlik uchun yig‘ilish:*",
        f"👥 Jami: *{len(attendees)}* kishi",
        "",
        "📝 *Ro‘yxat:*"
    ]
    if attendee_details:
        admin_lines += [f"{i+1}. {n} — {f or 'Tanlanmagan'}"
                        for i, (n, f) in enumerate(attendee_details)]
    else:
        admin_lines.append("Hech kim yo‘q")

    admin_lines.append("\n🍽 *Taomlar statistikasi:*")
    if counts:
        admin_lines += [f"{i+1}. {food} — {data['count']} ta"
                        for i, (food, data) in enumerate(counts.items())]
    else:
        admin_lines.append("— Hech qanday taom tanlanmadi")

    if declined:
        admin_lines += ["\n❌ *Rad etganlar:*"] + [
            f"{i+1}. {n}" for i, n in enumerate(declined)
        ]
    if pending:
        admin_lines += ["\n⏳ *Javob bermaganlar:*"] + [
            f"{i+1}. {n}" for i, n in enumerate(pending)
        ]

    admin_text = "\n".join(admin_lines)

    # send to each admin
    for u in users:
        if u.is_admin:
            try:
                await context.bot.send_message(u.telegram_id, admin_text, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.error(f"Failed sending summary to admin {u.telegram_id}: {e}")

    from utils.sheets_utils import get_balance_from_sheet  # make sure you have this function

    for u in attendees:
        try:
            # ✅ fetch latest balance from Google Sheets
            balance = await get_balance_from_sheet(u.telegram_id)

            if most:
                if len(most) > 1:
                    foods = " va ".join(most)
                    text = (
                        "✅🍽️ Siz bugungi tushlik ro‘yxatidasiz.\n\n"
                        f"🥇 Bugun tanlangan taomlar: 🍛 {foods}\n"
                        f"💰 Balansingiz: {balance:,.0f} so‘m\n\n"
                        "ℹ️ Agar tanlangan taom sizga to'g'ri kelmasa, "
                        "soat 10:00 gacha /bekor_qilish buyrug'i orqali ro'yxatdan chiqishingiz mumkin."
                    )
                else:
                    text = (
                        "✅🍽️ Siz bugungi tushlik ro‘yxatidasiz.\n\n"
                        f"🥇 Bugun tanlangan taom: 🍛 {most[0]}\n"
                        f"💰 Balansingiz: {balance:,.0f} so‘m\n\n"
                        "ℹ️ Agar tanlangan taom sizga to'g'ri kelmasa, "
                        "soat 10:00 gacha /bekor_qilish buyrug'i orqali ro'yxatdan chiqishingiz mumkin."
                    )
            else:
                text = (
                    "✅🍽️ Siz bugungi tushlik ro‘yxatidasiz.\n\n"
                    "🥄 Bugun asosiy taom aniqlanmadi.\n"
                    f"💰 Balansingiz: {balance:,.0f} so‘m\n\n"
                    "ℹ️ Agar tanlangan taom sizga to'g'ri kelmasa, "
                    "soat 10:00 gacha /bekor_qilish buyrug'i orqali ro'yxatdan chiqishingiz mumkin."
                )

            await context.bot.send_message(u.telegram_id, text, reply_markup=get_default_kb(u.is_admin))
        except Exception as e:
            logger.error(f"Failed user recap for {u.telegram_id}: {e}")

# ─── CARD MANAGEMENT ─────────────────────────────────────────────────────────

# ─── /karta_raqami — set card number ────────────────────────────────────────────
async def set_card_number_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("❌ Siz admin emassiz.")
    if not context.args:
        return await update.message.reply_text("❌ Foydalanish: /karta_raqami <raqam>")
    number = context.args[0]
    col = await get_collection("card_details")
    await col.update_one({}, {"$set": {"card_number": number}}, upsert=True)
    await update.message.reply_text(
        f"✅ Karta raqami o‘zgartirildi: `{number}`",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_default_kb(True)
    )

# ─── /karta_egasi — set card owner name ────────────────────────────────────────
async def set_card_owner_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("❌ Siz admin emassiz.")
    if not context.args:
        return await update.message.reply_text("❌ Foydalanish: /karta_egasi <ism>")
    owner = " ".join(context.args)
    col = await get_collection("card_details")
    await col.update_one({}, {"$set": {"card_owner": owner}}, upsert=True)
    await update.message.reply_text(
        f"✅ Karta egasi o‘zgartirildi: *{owner}*",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=get_default_kb(True)
    )


# ─── CONVERSATION HANDLERS ──────────────────────────────────────────────────────
async def cancel_conversation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel any ongoing conversation and return to admin panel."""
    if update.message:
        await update.message.reply_text(
            "❌ Operatsiya bekor qilindi.",
            reply_markup=get_admin_kb()
        )
    elif update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text("❌ Operatsiya bekor qilindi.")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="🔧 Admin panelga qaytdingiz:",
            reply_markup=get_admin_kb()
        )
    context.user_data.clear()
    return ConversationHandler.END

async def test_debts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from handlers.user_handlers import check_debts
    await update.message.reply_text("🚀 Testing debt check…")
    await check_debts(context)
    await update.message.reply_text("✅ Done.")

async def run_summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("❌ Siz admin emassiz!")
    await update.message.reply_text("⏳ Today’s summary being sent…")
    # reuse send_summary logic
    await send_summary(context)
    return ConversationHandler.END

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only admins can invoke this
    if not await is_admin(update.effective_user.id):
        return await update.message.reply_text("❌ Siz admin emassiz.")

    if not context.args:
        return await update.message.reply_text(
            "❌ Iltimos, xabar matnini yozing.\n"
            "Misol: /broadcast Assalomu alaykum!"
        )

    text = " ".join(context.args)
    sent = failed = 0

    users = await get_all_users_async()
    for u in users:
        try:
            await context.bot.send_message(u.telegram_id, text)
            sent += 1
        except Exception:
            failed += 1

    await update.message.reply_text(
        f"✅ Jami {sent} ta foydalanuvchiga yuborildi\n"
        f"⚠️ {failed} ta xatolik yuz berdi."
    )

async def cancel_lunch_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin only: cancel lunch on a date (future or today), skip survey, notify & refund if needed."""
    user = await get_user_async(update.effective_user.id)
    if not (user and user.is_admin):
        return await update.message.reply_text("❌ Siz admin emassiz!")

    # Expect at least: /cancel_lunch YYYY-MM-DD Sabab
    if len(context.args) < 2:
        return await update.message.reply_text(
            "❌ Foydalanish: /cancel_lunch YYYY-MM-DD Sabab",
            reply_markup=get_default_kb(True)
        )

    raw_date, *reason_parts = context.args
    reason = " ".join(reason_parts).strip() or "Sabab ko‘rsatilmagan"

    # Normalize date_str
    tz = pytz.timezone("Asia/Tashkent")
    today = datetime.now(tz).date()
    if raw_date.lower() in ("bugun", "today"):
        date_str = today.strftime("%Y-%m-%d")
    else:
        try:
            dt = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            return await update.message.reply_text(
                "❌ Sana noto‘g‘ri formatda. Iltimos: YYYY-MM-DD yoki “bugun”.",
                reply_markup=get_default_kb(True)
            )
        # ─── Reject truly past dates ───────────────────────────────────
        if dt < today:
            return await update.message.reply_text(
                f"❌ {raw_date} sanasi allaqachon o‘tgani uchun bekor qilib bo‘lmaydi.\n"
                "Iltimos, bugungi yoki kelajakdagi sanani tanlang.",
                reply_markup=get_default_kb(True)
            )
        date_str = raw_date

    # 1) Mark this date as cancelled in your own collection
    coll = await get_collection("cancelled_lunches")
    await coll.update_one(
        {"date": date_str},
        {"$set": {
            "date": date_str,
            "reason": reason,
            "cancelled_at": datetime.now(timezone.utc),
            "cancelled_by": update.effective_user.id
        }},
        upsert=True
    )

    # 2) Notify everyone and refund if they’d already checked in
    users = await get_all_users_async()
    refunded = 0
    for u in users:
        # if they already had attendance, remove & refund
        if date_str in u.attendance:
            await u.remove_attendance(date_str)
            refunded += 1

        text = (
            f"⚠️ {date_str} kuni tushlik bekor qilindi.\n\n"
            f"Sabab: {reason}"
        )
        # note: after remove_attendance, u.daily_price is still the same
        if date_str not in u.attendance:
            text += f"\n💰 Balansingizga {u.daily_price:,.0f} so‘m qaytarildi."

        try:
            await context.bot.send_message(
                chat_id=u.telegram_id,
                text=text,
                reply_markup=get_default_kb(u.is_admin)
            )
        except Exception as e:
            logger.warning(f"Unable to notify {u.telegram_id}: {e}")

    # 3) Confirm back to the admin
    await update.message.reply_text(
        f"✅ {date_str} uchun tushlik bekor qilindi.\n"
        f"Refund: {refunded} foydalanuvchi.",
        reply_markup=get_default_kb(True)
    )

    
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller = await get_user_async(update.effective_user.id)
    if not (caller and caller.is_admin):
        return await update.message.reply_text("❌ Siz admin emassiz.")

    help_text = """\
        🔧 *Admin Qo‘llanma*

        1️⃣ `/admin`  
        • Admin panelini ochadi. Tugmalar orqali foydalanuvchilar, narxlar, karta va menyu boshqaruvi.

        2️⃣ `/run_summary`  
        • Bugungi tushlik holatini darhol jo‘natadi.

        3️⃣ `/test_debts`  
        • Qarzdor foydalanuvchilarni tekshiradi va hisobot yuboradi.

        4️⃣ `/broadcast <xabar>`  
        • Barcha foydalanuvchilarga xabar yuboradi.  
        • Misol: `/broadcast Assalomu alaykum, bugun ta’til!`

        5️⃣ `/cancel_lunch <YYYY-MM-DD> <sabab>`  
        • Ko‘rsatilgan sanadagi tushlikni bekor qiladi va balansni qaytaradi.  
        • Misol: `/cancel_lunch 2025-05-14 Texnik ishlar tufayli`

        6️⃣ `/karta_raqami <raqam>`  
        • Yangi karta raqamini o‘rnatish.

        7️⃣ `/karta_egasi <ism>`  
        • Karta egasining ismini o‘rnatish.

        _Har bir buyruqdan keyin bot sizga keyingi amallar bo‘yicha yo‘l-yo‘riq beradi._\
        """

    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)

def register_handlers(app):
    # ─── INITIALIZATION ────────────────────────────────────────────────
    app.job_queue.run_once(lambda _: init_collections(), when=0)

    # ─── 1) CORE COMMANDS ──────────────────────────────────────────────
    app.add_handler(CommandHandler("admin", admin_panel))
    app.add_handler(CommandHandler("run_summary", run_summary_command))
    app.add_handler(CommandHandler("test_debts", test_debts_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("cancel_lunch_date", cancel_lunch_command))
    app.add_handler(CommandHandler("help_admin", help_command))
    app.add_handler(CommandHandler("karta_raqami", set_card_number_cmd))
    app.add_handler(CommandHandler("karta_egasi",   set_card_owner_cmd))
    
    # ─── 3) ADMIN SHORTCUTS (Reply‑Keyboard Buttons) ──────────────────
    single_buttons = [
        (FOYD_BTN,         list_users_exec),
        (ADD_ADMIN_BTN,    start_add_admin),
        (REMOVE_ADMIN_BTN, start_remove_admin),
        (DELETE_USER_BTN,  start_delete_user),
        (KASSA_BTN,        show_kassa),
        (MENU_BTN,         menu_panel),
        (BACK_BTN,         back_to_menu),  # Ortga always goes to menu
    ]
    for text, handler in single_buttons:
        app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(text)}$"), handler))

    # ─── 4) ORTGA SHORTCUT (Reply & Inline) ────────────────────────────
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BACK_BTN)}$"), back_to_menu))
    app.add_handler(CallbackQueryHandler(back_to_menu, pattern="^back_to_menu$"))
    app.add_handler(CallbackQueryHandler(admin_panel, pattern="^back_to_admin$"))
  
    # ─── 7) INLINE CALLBACKS FOR USER MGMT ─────────────────────────────
    app.add_handler(CallbackQueryHandler(add_admin_callback,    pattern=r"^add_admin:\d+$"))
    app.add_handler(CallbackQueryHandler(remove_admin_callback, pattern=r"^remove_admin:\d+$"))
    app.add_handler(CallbackQueryHandler(delete_user_callback,  pattern=r"^delete_user:\d+$"))

    # Updated regex pattern to include all 4 menus
    menu_pattern = r"^(view_menu[1-4]|add_menu[1-4]|del_menu[1-4]|menu_back)$"

    # Register the callback query handler with updated pattern
    app.add_handler(CallbackQueryHandler(menu_callback, pattern=menu_pattern))

    # Register the text message handler for menu additions
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_add))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_menu_del))

    # ─── 10) NOTIFY RESPONSE INLINE (Optional) ─────────────────────────
    app.add_handler(CallbackQueryHandler(notify_response_callback, pattern=r"^notify_response:(yes|no):\d+$"))

    logging.info("✅ All admin handlers registered.") 