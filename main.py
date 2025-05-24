#!/usr/bin/env python3
import logging
import os
import sys
import asyncio
import tempfile
import atexit
from datetime import time as dt_time
import datetime
import pytz

# Cross‑platform file locking
if os.name == 'nt':
    import msvcrt
else:
    import fcntl

from dotenv import load_dotenv
from telegram.ext import ApplicationBuilder

from database import init_db
from config import BOT_TOKEN, MONGODB_URI
from models.user_model import User
# Register handlers
import handlers.user_handlers as uh
import handlers.admin_handlers as ah

logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

async def error_handler(update, context):
    logger.error(f"Update {update} caused error {context.error}", exc_info=True)
    import traceback
    tb = traceback.format_exc()
    ADMIN_ID = 5192568051
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=f"❌ Xatolik:\n{context.error}\n\n{tb[:1000]}"
        )
    except Exception as e:
        logger.error(f"Failed to notify admin: {e}")

def check_single_instance():
    lock_file = os.path.join(tempfile.gettempdir(), "lunch_bot.lock")
    fd = open(lock_file, "w")
    try:
        if os.name == "nt":
            msvcrt.locking(fd.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        atexit.register(lambda: cleanup_lock(fd, lock_file))
        return fd
    except Exception:
        fd.close()
        logger.error("Bot already running, exiting.")
        sys.exit(1)

def cleanup_lock(fd, path):
    try:
        if os.name == "nt":
            msvcrt.locking(fd.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass

async def cleanup_old_data(context):
    logger.info("Midnight cleanup…")
    await User.cleanup_old_food_choices()
    logger.info("Cleanup done.")

def main():
    # Single‑instance guard
    lock_fd = check_single_instance()

    # Load env
    load_dotenv()
    if not os.getenv("BOT_TOKEN", BOT_TOKEN) or not os.getenv("MONGODB_URI", MONGODB_URI):
        logger.error("Missing BOT_TOKEN or MONGODB_URI")
        sys.exit(1)

    # Asyncio loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    try:
        # Init DB
        loop.run_until_complete(init_db())
        logger.info("Database ready")

        # Build app
        application = (
            ApplicationBuilder()
            .token(os.getenv("BOT_TOKEN", BOT_TOKEN))
            .connect_timeout(30.0)
            .read_timeout(30.0)
            .write_timeout(30.0)
            .get_updates_read_timeout(30.0)
            .build()
        )
        application.add_error_handler(error_handler)

        uh.register_handlers(application)
        ah.register_handlers(application)

        # Schedule jobs
        jq = application.job_queue
        tz = pytz.timezone("Asia/Tashkent")

        # Morning prompt 07:00 Mon–Fri
        jq.run_daily(
            uh.morning_prompt,
            time=dt_time(7, 00, tzinfo=tz),
            days=(1, 2, 3, 4, 5),
            name="morning_survey"
        )

        # Daily summary 9:40 Mon–Fri
        jq.run_daily(
            ah.send_summary,
            time=dt_time(9, 40, tzinfo=tz),
            days=(1, 2, 3, 4, 5),
            name="daily_summary"
        )

        jq.run_daily(
            uh.check_debts,
            time=dt_time(12, 0, tzinfo=tz),
            days=(1, 3, 5),  # Monday, Wednesday, Friday
            name="debt_check"
        )


        # Midnight cleanup
        jq.run_daily(
            cleanup_old_data,
            time=dt_time(0, 0, tzinfo=tz),
            name="midnight_cleanup"
        )

        logger.info("Bot started, polling…")
        application.run_polling(
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True
        )

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        raise
    finally:
        cleanup_lock(lock_fd, os.path.join(tempfile.gettempdir(), "lunch_bot.lock"))

if __name__ == "__main__":
    main()