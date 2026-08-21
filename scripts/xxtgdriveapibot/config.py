import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(dotenv_path=ENV_PATH)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
API_ID = int(os.getenv("API_ID", "29116029"))
API_HASH = os.getenv("API_HASH", "867fafeeabc20a75163ef2ddbd877f70").strip()

API_BASE_URL = os.getenv("API_BASE_URL", "https://tgdriveapi.youganksaini1.workers.dev").rstrip("/")
DEFAULT_API_KEY = os.getenv("DEFAULT_API_KEY", "").strip()
DATABASE_PATH = str(BASE_DIR / os.getenv("DATABASE_PATH", "tgdrive_bot.db"))
TEMP_DIR = BASE_DIR / "temp_uploads"
TEMP_DIR.mkdir(parents=True, exist_ok=True)

GENERATE_KEY_URL = "https://tgdriveo.pages.dev/#/developer"

# Authorized Admin User IDs
admin_env = os.getenv("ADMIN_IDS", "7249511572,7251749429")
ADMIN_IDS = [int(x.strip()) for x in admin_env.split(",") if x.strip().isdigit()]

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in .env file! Please add your Telegram Bot token.")
