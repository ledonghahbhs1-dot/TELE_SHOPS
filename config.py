import os
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_IDS = [int(value.strip()) for value in os.getenv("OWNER_IDS", "").split(",") if value.strip()]
ALLOWED_GROUP_IDS = [int(value.strip()) for value in os.getenv("ALLOWED_GROUP_IDS", "").split(",") if value.strip()]
WOLFMOD_API_URL = os.getenv("WOLFMOD_API_URL", "https://www.wolfmod.xyz").rstrip("/")
WOLFMOD_API_SECRET = os.getenv("WOLFMOD_API_SECRET", "").strip()
PRODUCT_SKU = os.getenv("PRODUCT_SKU", "gemini_plus").strip()
POLL_INTERVAL_SEC = int(os.getenv("POLL_INTERVAL_SEC", "15"))


def validate():
    if not BOT_TOKEN:
        raise ValueError("Set BOT_TOKEN using the new token from BotFather")
    if len(WOLFMOD_API_SECRET) < 32:
        raise ValueError("WOLFMOD_API_SECRET must match TG_BOT_API_SECRET on Vercel (at least 32 characters)")
    url = urlsplit(WOLFMOD_API_URL)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.path or url.query or url.fragment:
        raise ValueError("WOLFMOD_API_URL must be an HTTPS origin, for example https://www.wolfmod.xyz")
    if not OWNER_IDS or any(owner <= 0 for owner in OWNER_IDS):
        raise ValueError("Set OWNER_IDS to positive Telegram user IDs")
    if any(group >= 0 for group in ALLOWED_GROUP_IDS):
        raise ValueError("ALLOWED_GROUP_IDS must contain negative Telegram group IDs")
    if not 5 <= POLL_INTERVAL_SEC <= 300:
        raise ValueError("POLL_INTERVAL_SEC must be between 5 and 300")
