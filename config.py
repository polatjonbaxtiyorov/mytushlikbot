# config.py

from dotenv import load_dotenv
import os

# Load environment variables from .env into os.environ
load_dotenv()

# Retrieve the bot token and MongoDB URI by their variable names
BOT_TOKEN   = os.getenv("BOT_TOKEN")
MONGODB_URI = os.getenv("MONGODB_URI")
DEFAULT_INITIAL_BALANCE = int(os.getenv("DEFAULT_INITIAL_BALANCE", "0"))
DEFAULT_DAILY_PRICE = int(os.getenv("DEFAULT_DAILY_PRICE", "25000"))

# Validate that both values are present
if not BOT_TOKEN:
    raise RuntimeError("Missing BOT_TOKEN in .env")

if not MONGODB_URI:
    raise RuntimeError("Missing MONGODB_URI in .env")

