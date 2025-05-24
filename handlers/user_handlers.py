# handlers/user_handlers.py

import logging
import re
from datetime import datetime, time
import pytz
from telegram.error import BadRequest
from telegram.constants import ParseMode, ChatAction
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
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
from models.user_model import User
from utils import (
    validate_name,
    validate_phone,
    get_default_kb,
    get_user_async,
    get_all_users_async,
)
from utils.sheets_utils import find_user_in_sheet
from handlers.admin_handlers import admin_panel, get_menu_for_today

logger = logging.getLogger(__name__)

# ─── BUTTON LABELS ───────────────────────────
BAL_BTN   = "💸 Balansim"
NAME_BTN  = "✏️ Ism o'zgartirish"
ADMIN_BTN = "🔧 Admin panel"
CARD_BTN  = "💳 Karta Raqami"
HISTORY_BTN = "🗓️ Qatnashuv"

# ─── STATES ───────────────────────────────────
NAME, PHONE = range(2)
CHANGE_NAME = 2
YES, NO     = "att_yes", "att_no"
MONTH_NAMES = {
    1: "Yanvar", 2: "Fevral", 3: "Mart", 4: "Aprel",
    5: "May", 6: "Iyun", 7: "Iyul", 8: "Avgust",
    9: "Sentabr", 10: "Oktabr", 11: "Noyabr", 12: "Dekabr"
}

# ─── /start & REGISTRATION ────────────────────
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_async(update.effective_user.id)
    if not user:
        await update.message.reply_text(
            "Ismingizni kiriting:", reply_markup=ReplyKeyboardRemove()
        )
        return NAME

    kb = get_default_kb(user.is_admin)
    await update.message.reply_text(
        f"Assalomu alaykum, {user.name}!\nNimani bajarishni hohlaysiz?",
        reply_markup=kb
    )
    return ConversationHandler.END

async def register_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not validate_name(name):
        await update.message.reply_text("Ism noto'g'ri. Qaytadan kiriting:")
        return NAME
    context.user_data["name"] = name
    kb = [[KeyboardButton("Telefon raqamingizni yuboring", request_contact=True)]]
    await update.message.reply_text(
        "Telefon raqamingizni yuboring:",
        reply_markup=ReplyKeyboardMarkup(kb, resize_keyboard=True)
    )
    return PHONE

async def register_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = (
        update.message.contact.phone_number
        if update.message.contact
        else update.message.text
    )
    if not validate_phone(phone):
        await update.message.reply_text("Raqam noto'g'ri. Qaytadan kiriting:")
        return PHONE

    user = await User.create(
        update.effective_user.id,
        context.user_data["name"],
        phone
    )
    kb = get_default_kb(user.is_admin)
    await update.message.reply_text(
        f"Ro'yxatdan o'tish yakunlandi. Balans: {user.balance:,.0f} so'm.",
        reply_markup=kb
    )
    return ConversationHandler.END


# ─── NAME CHANGE ──────────────────────────────
async def change_name_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Yangi ismingizni kiriting:", reply_markup=ReplyKeyboardRemove()
    )
    return CHANGE_NAME

async def change_name_exec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_name = update.message.text.strip()
    if not validate_name(new_name):
        await update.message.reply_text("Ism noto'g'ri. Yana urinib ko'ring:")
        return CHANGE_NAME

    user = await get_user_async(update.effective_user.id)
    await user.change_name(new_name)
    kb = get_default_kb(user.is_admin)
    await update.message.reply_text(
        f"Ismingiz muvaffaqiyatli o'zgardi: {new_name}", reply_markup=kb
    )
    return ConversationHandler.END


# ─── CANCEL ───────────────────────────────────
async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Operatsiya bekor qilindi.")
    return ConversationHandler.END


# ─── HELP / BALANCE / HISTORY ─────────────────
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "/start — Ro'yxatdan o'tish\n"
        "/menu — Taom tanlash\n"
        "/balance — Balansni ko'rish\n"
        "/attendance — Qatnashuv tarixini ko'rish\n"
        "/history — To'lovlar tarixini ko'rish\n"
        "/name — Ism o'zgartirish\n"
        "/bekor_qilish — Buyurtmani bekor qilish\n"
        "/help — Yordam"
    )
    await update.message.reply_text(text)

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tg_id = update.effective_user.id
    user  = await get_user_async(tg_id)
    if not user:
        return await update.message.reply_text(
            "Iltimos, avval /start bilan ro'yxatdan o'ting."
        )

    # Tell them we're working
    await update.message.reply_text("⏳ Balans tekshirilmoqda...")
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action=ChatAction.TYPING
    )

    try:
        sheet_record = await find_user_in_sheet(tg_id)
        if sheet_record and "balance" in sheet_record:
            bal = float(str(sheet_record["balance"]).replace(",", ""))
            if bal != user.balance:
                users = await get_collection("users")
                await users.update_one(
                    {"telegram_id": tg_id},
                    {"$set": {"balance": bal}}
                )
                user.balance = bal
    except Exception as e:
        logger.error(f"Error fetching balance from sheet: {e}")

    await update.message.reply_text(
        f"Balansingiz: {user.balance:,.0f} so'm."
    )

async def attendance_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show the user’s attendance for the current month."""
    user = await get_user_async(update.effective_user.id)
    if not user:
        return await update.message.reply_text("❌ Siz ro‘yxatdan o‘tmagansiz.")

    # Today in Tashkent
    tz = pytz.timezone("Asia/Tashkent")
    today = datetime.now(tz).date()
    current_month = today.month
    current_year = today.year
    month_name = MONTH_NAMES[current_month]

    # Filter attendance for this month/year
    attended_dates = [
        datetime.strptime(d, "%Y-%m-%d").date()
        for d in user.attendance
    ]
    # keep only those in this month & year, sorted ascending
    this_month = sorted(
        d for d in attended_dates
        if d.year == current_year and d.month == current_month
    )

    # Build response
    if not this_month:
        text = f"⏳ {month_name} oyida qatnashuvingiz yo‘q."
    else:
        lines = [
            f"{i+1}. {d.strftime('%d.%m.%Y')}"
            for i, d in enumerate(this_month)
        ]
        text = (
            f"🗓️ *{month_name} oyida* siz qatnashgan kunlar:\n\n" +
            "\n".join(lines)
        )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=get_default_kb(user.is_admin)
    )

async def transaction_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_async(update.effective_user.id)
    txs  = user.transactions
    lines = [
        f"{t['date'][:10]}: {t['desc']} ({t['amount']} so'm)"
        for t in txs[-20:]
    ]
    text  = (
        "To'lovlar tarixi:\n" + "\n".join(lines)
        if lines else
        "Hech qanday tranzaksiya yo'q."
    )
    await update.message.reply_text(text)


# ─── CARD INFO ─────────────────────────────────
async def show_card_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    card_col = await get_collection("card_details")
    doc = await card_col.find_one({})
    if not doc:
        return await update.message.reply_text("❌ Karta ma'lumotlari topilmadi.")
    await update.message.reply_text(
        f"💳 *Karta raqami:* `{doc['card_number']}`\n"
        f"👤 *Karta egasi:* {doc['card_owner']}",
        parse_mode=ParseMode.MARKDOWN
    )


# ─── MENU & FOOD ──────────────────────────────
async def menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = await get_user_async(update.effective_user.id)
    if not user:
        return await update.message.reply_text(
            "Iltimos, avval /start bilan ro'yxatdan o'ting."
        )

    # Get today's menu using the new 4-menu system
    menu_name = get_menu_for_today()
    menu_col = await get_collection("menu")
    doc = await menu_col.find_one({"name": menu_name})
    items = doc.get("items", []) if doc else []

    if not items:
        await update.message.reply_text("❌ Bugungi menyu hali tayyor emas.")
        return

    kb = [[InlineKeyboardButton(i, callback_data=f"food:{i}")] for i in items]
    kb.append([InlineKeyboardButton("🔙 Ortga", callback_data="cancel_attendance")])
    
    await update.message.reply_text(
        "🍽 Bugungi taomlar:",
        reply_markup=InlineKeyboardMarkup(kb)
    )

# ─── FOR ATTENDANCE CALLBACK ──────────────────────────────
async def attendance_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query

    # ─── CUT-OFF CHECK: no answers after 09:40 ───────────────────
    tz = pytz.timezone("Asia/Tashkent")
    now_t = datetime.now(tz).time()
    cutoff = time(9, 40)
    if now_t >= cutoff:
        await q.answer("So'rovnoma vaqti tugadi!", show_alert=True)
        await q.edit_message_reply_markup(reply_markup=None)
        return
    # ─────────────────────────────────────────────────────────────

    await q.answer()
    user = await get_user_async(q.from_user.id)
    today_str = datetime.now(tz).strftime("%Y-%m-%d")

    # ─── NO branch ───────────────────────────────────────────────
    if q.data == NO:
        if today_str in user.attendance:
            await user.remove_attendance(today_str)
        await user.decline_attendance(today_str)

        # 1) clear inline
        await q.message.edit_text("❌ Bugungi tushlik rad etildi.")

        # 2) send fresh reply-keyboard
        kb = get_default_kb(user.is_admin)
        await context.bot.send_message(
            chat_id=q.from_user.id,
            text="Nimani xohlaysiz?",
            reply_markup=kb
        )
        return

    # ─── already said YES ────────────────────────────────────────
    if today_str in user.attendance:
        # remove inline menu
        await q.message.edit_text(
            f"⚠️ Allaqachon ro'yxatdasiz. Balans: {user.balance:,.0f} so'm."
        )
        # then fresh keyboard
        await context.bot.send_message(
            chat_id=q.from_user.id,
            text="Nimani xohlaysiz?",
            reply_markup=get_default_kb(user.is_admin)
        )
        return

    # ─── YES first time → show foods ─────────────────────────────
    if today_str in user.declined_days:
        await user.remove_decline(today_str)

    # Use the new 4-menu system instead of old 2-menu logic
    menu_name = get_menu_for_today()
    menu_col = await get_collection("menu")
    doc = await menu_col.find_one({"name": menu_name})
    foods = doc.get("items", []) if doc else []

    if not foods:
        await q.message.edit_text("❌ Bugungi menyu hali tayyor emas.")
        return

    kb = [[InlineKeyboardButton(f, callback_data=f"food:{f}")] for f in foods]
    kb.append([InlineKeyboardButton("🔙 Ortga", callback_data="cancel_attendance")])
    await q.message.edit_text(
        "🍽 Iltimos, taom tanlang:",
        reply_markup=InlineKeyboardMarkup(kb)
    )


async def food_selection_cb(update, context):
    q = update.callback_query
    await q.answer()

    user = await get_user_async(q.from_user.id)

    # “Cancel” branch: immediate UI
    if q.data == "cancel_attendance":
        await q.message.edit_text("✅ Bekor qilindi.")
        await q.message.reply_text(
            "Nimani xohlaysiz?",
            reply_markup=get_default_kb(user.is_admin)
        )
        return

    # parse the food choice
    food = q.data.split(":", 1)[1]

    # immediate UI update
    try:
        await q.message.edit_text(f"✅ {food} tanlandi!")
    except BadRequest:
        # already edited, ignore
        pass

    # show the cancel tip right away
    tz = pytz.timezone("Asia/Tashkent")
    today_str = datetime.now(tz).strftime("%Y-%m-%d")
    await q.message.reply_text(
        "Agar tushlikka qatnashish fikridan voz kechsangiz soat 10:00 gacha "
        "bekor qilishingiz mumkin. Shunchaki /bekor_qilish buyrug‘ini bosing.",
        reply_markup=get_default_kb(user.is_admin)
    )
    import asyncio
    # now fire off the real persistence in the background
    asyncio.create_task(_persist_choice_and_attendance(user, today_str, food))


async def _persist_choice_and_attendance(user, date_str, food):
    """
    Runs in background so your callback returns immediately.
    Does exactly what you had before: set food choice, deduct balance,
    sync to Sheets, etc.
    """
    try:
        # record in Mongo & in-memory
        await user.set_food_choice(date_str, food)
        # this will deduct balance + log txn + push to Sheets
        await user.add_attendance(date_str, food)
        logger.info(f"Successfully persisted attendance for user {user.telegram_id} on {date_str}")
    except Exception as e:
        # log the full error with more context
        logger.error(f"Failed to persist attendance for user {user.telegram_id} on {date_str}: {type(e).__name__}: {e}", exc_info=True)

async def get_admin_users_async():
    users = await get_all_users_async()  # or whatever function returns all users
    return [u for u in users if u.is_admin]

async def cancel_lunch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for /bekor_qilish"""
    tz = pytz.timezone("Asia/Tashkent")
    now = datetime.now(tz)
    today = now.strftime("%Y-%m-%d")

    # 1) Before survey opens
    if now.hour < 7:
        return await update.message.reply_text(
            "❌ Tushlik ro‘yxati hali ochilmadi.\n"
            f"Bekor qilish faqat soat 7:00 dan keyin mumkin."
        )

    # 2) After cutoff
    if now.time() >= time(10, 00):
        return await update.message.reply_text("❌ Bekor qilish vaqti o‘tdi.")

    # 3) Not in attendance
    user = await get_user_async(update.effective_user.id)
    if today not in user.attendance:
        return await update.message.reply_text("❌ Siz bugun ro‘yxatda emassiz.")

    # 4) Ask for confirmation
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Ha, bekor qilaman", callback_data="cancel_yes"),
        InlineKeyboardButton("❌ Yo‘q",            callback_data="cancel_no")
    ]])
    await update.message.reply_text(
        f"⚠️ {today} uchun tushlik ishtirokini bekor qilmoqchimisiz?",
        reply_markup=kb
    )

async def cancel_lunch_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the confirmation buttons"""
    query = update.callback_query
    await query.answer()
    tz = pytz.timezone("Asia/Tashkent")
    today = datetime.now(tz).strftime("%Y-%m-%d")

    user = await get_user_async(query.from_user.id)

    if query.data == "cancel_no":
        # user changed mind
        await query.message.edit_text("❌ Bekor qilish bekor qilindi.", reply_markup=None)
        return

    # confirmed → actually remove and refund
    await user.remove_attendance(today)
    text = (
        f"✅ {today} uchun tushlik bekor qilindi.\n"
        f"Yangi balans: {user.balance:,.0f} so‘m"
    )
    await query.message.edit_text(text, reply_markup=None)

    # restore their reply keyboard
    await context.bot.send_message(
        chat_id=query.from_user.id,
        text="Bosh menyu:",
        reply_markup=get_default_kb(user.is_admin)
    )

    # Notify other admins
    admin_users = await get_admin_users_async()
    for admin in admin_users:
        if admin.telegram_id != user.telegram_id:
            admin_notice = (
                f"⚠️ <b>{user.name}</b> "
                f"{'@' + user.username if user.username else ''} "
                f"{today} uchun tushlik ro‘yxatidan chiqdi.\n"
            )
            await context.bot.send_message(
                chat_id=admin.telegram_id,
                text=admin_notice,
                parse_mode="HTML"
            )

# ─── SCHEDULED JOBS ────────────────────────────
async def reminder_callback(context: ContextTypes.DEFAULT_TYPE):
    tz = pytz.timezone("Asia/Tashkent")
    today = datetime.now(tz).strftime("%Y-%m-%d")

    users = await get_all_users_async()
    for user in users:
        if today not in user.attendance:
            await context.bot.send_message(
                chat_id=user.telegram_id,
                text="So'rovnomaga ovoz berishni unutmang. E'tiboringiz uchun rahmat!"
            )

async def morning_prompt(context: ContextTypes.DEFAULT_TYPE):
    cancelled_lunches = await get_collection("cancelled_lunches")
    tz = pytz.timezone("Asia/Tashkent")
    now = datetime.now(tz)
    today = now.strftime("%Y-%m-%d")

    if now.weekday() >= 5:
        return

    cancelled = await cancelled_lunches.find_one({"date": today})
    if cancelled:
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Ha", callback_data=YES),
         InlineKeyboardButton("Yo'q", callback_data=NO)]
    ])
    for u in await get_all_users_async():
        await context.bot.send_message(
            chat_id=u.telegram_id,
            text="Bugun tushlikka borasizmi?",
            reply_markup=kb
        )

    # Schedule reminder for 8:20
    reminder_time = datetime.combine(now.date(), time(8, 20, tzinfo=tz))
    delay_seconds = (reminder_time - now).total_seconds()
    if delay_seconds > 0:
        context.job_queue.run_once(
            reminder_callback,
            when=delay_seconds,
            name=f"reminder_{today}"
        )

async def check_debts(context: ContextTypes.DEFAULT_TYPE):
    for u in await get_all_users_async():
        if u.balance < 0:
            try:
                await context.bot.send_message(
                    chat_id=u.telegram_id,
                    text=(
                        f"👋 Assalomu alaykum!\n"
                        f"Sizning balansingizda {abs(u.balance):,.0f} so‘m qarzdorlik mavjud.\n"
                        "Iltimos, balansingizni to‘ldiring. 🙏"
                    )
                )
            except Exception as e:
                logger.error(f"Error notifying debt: {e}")


# ─── ADMIN SHORTCUT ───────────────────────────
async def admin_button_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await admin_panel(update, context)


# ─── REGISTER HANDLERS ────────────────────────
def register_handlers(app):
    # /start registration
    reg = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, register_name)],
            PHONE: [MessageHandler(filters.CONTACT | (filters.TEXT & ~filters.COMMAND), register_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(reg)

    # name change
    name_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex(f"^{re.escape(NAME_BTN)}$"), change_name_start)],
        states={CHANGE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, change_name_exec)]},
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )
    app.add_handler(name_conv)

    app.add_handler(CommandHandler("bekor_qilish", cancel_lunch))

    # core commands
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("balance", balance))
    app.add_handler(CommandHandler("attendance", attendance_history))
    app.add_handler(CommandHandler("history", transaction_history))
    app.add_handler(CommandHandler("menu", menu))

    # reply‑keyboard shortcuts
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(BAL_BTN)}$"), balance))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(NAME_BTN)}$"), change_name_start))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(CARD_BTN)}$"), show_card_info))
    app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(ADMIN_BTN)}$"), admin_panel))
    app.add_handler(
    MessageHandler(
        filters.Regex(f"^{re.escape(HISTORY_BTN)}$"),
        attendance_history
    )
)

    # inline callbacks
    app.add_handler(CallbackQueryHandler(attendance_cb, pattern=f"^{YES}$"))
    app.add_handler(CallbackQueryHandler(attendance_cb, pattern=f"^{NO}$"))
    app.add_handler(CallbackQueryHandler(food_selection_cb, pattern="^food:"))
    app.add_handler(CallbackQueryHandler(cancel_lunch_callback, pattern="^cancel_(yes|no)$"))