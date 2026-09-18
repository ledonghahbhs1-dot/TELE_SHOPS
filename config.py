import os
from pathlib import Path
from typing import List
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")

raw_owners = os.getenv("OWNER_IDS", "")
OWNER_IDS: List[int] = [
    int(x.strip()) for x in raw_owners.split(",") if x.strip().lstrip("-").isdigit()
]

DATABASE_PATH: str = os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "shop.db"))

# --- Product ---
PRODUCT_NAME: str = os.getenv("PRODUCT_NAME", "Gemini / Google One Offer Link")
PRICE_VND: int = int(os.getenv("PRICE_VND", 25000))  # 25,000 VND (~$1 USD) per link
ORDER_EXPIRE_MINUTES: int = int(os.getenv("ORDER_EXPIRE_MINUTES", 15))

# --- VietQR bank details (same account already used for wolfmod.xyz VIP keys) ---
BANK_BIN: str = os.getenv("BANK_BIN", "970416")  # ACB
BANK_NAME: str = os.getenv("BANK_NAME", "ACB")
ACCOUNT_NO: str = os.getenv("ACCOUNT_NO", "68986683")
ACCOUNT_NAME: str = os.getenv("ACCOUNT_NAME", "LE DONG HA")

# --- SePay (transaction lookup API, used to auto-confirm bank transfers) ---
SEPAY_API_BASE_URL: str = os.getenv("SEPAY_API_BASE_URL", "https://my.sepay.vn/userapi")
SEPAY_API_TOKEN: str = os.getenv("SEPAY_API_TOKEN", "")

# Background poller interval for matching pending orders against new SePay transactions
POLL_INTERVAL_SEC: int = int(os.getenv("POLL_INTERVAL_SEC", 15))

# Path to a newline-delimited .txt file used to seed the initial link inventory
# (only read once, by seed_links.py - not touched at runtime by the bot)
SEED_LINKS_FILE: str = os.getenv("SEED_LINKS_FILE", str(BASE_DIR / "seed_links.txt"))
