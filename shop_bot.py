"""
Telegram Shop Bot - Bán Link/Tài Khoản Gemini Plus & Sản phẩm số tự động
Sử dụng python-telegram-bot v20+ và SQLite3
"""

import os
import asyncio
import re
import io
import sys
import sqlite3
import logging
import html

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, 'reconfigure'):
        stream.reconfigure(encoding='utf-8', errors='replace')
from datetime import datetime, timedelta
from dotenv import load_dotenv
import i18n
import product_content
import product_guides
import product_intros
import stock_notify
import wallet_pay
import wallet_admin
import wallet_store
import wallet_topup
from i18n import t
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

# ==================== CẤU HÌNH LOGGING ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
# HTTP request URLs contain the Telegram token; do not emit them in service logs.
logging.getLogger("httpx").setLevel(logging.WARNING)

# ==================== CẤU HÌNH BOT ====================
BOT_TOKEN = os.getenv("SHOP_BOT_TOKEN") or os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = os.getenv("ADMIN_ID", "6571486853").strip() or "6571486853"  # Telegram ID của Admin (wolfmodyt)
ADMIN_USERNAMES = ["wolfmodyt", "wolfmod"]

def is_admin(user_or_id) -> bool:
    """Kiểm tra quyền Admin theo Telegram ID hoặc Username @wolfmodyt"""
    if not user_or_id:
        return False
    if hasattr(user_or_id, "id"):
        if str(user_or_id.id) == str(ADMIN_ID) and str(ADMIN_ID) != "":
            return True
        username = (getattr(user_or_id, "username", "") or "").lower().replace("@", "")
        return username in ADMIN_USERNAMES
    uid_str = str(user_or_id).lower().replace("@", "")
    if uid_str == str(ADMIN_ID) and str(ADMIN_ID) != "":
        return True
    return uid_str in ADMIN_USERNAMES

DATA_DIR = os.getenv("SHOP_DATA_DIR") or os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "shop_database.db")
CLAIMED_LINKS_FILE = os.path.join(DATA_DIR, "claimed_links.txt")

# ==================== QUẢN LÝ CƠ SỞ DỮ LIỆU (SQLITE) ====================

# ==================== DANH MỤC SẢN PHẨM ====================
DEFAULT_SKU = "gemini_plus"
_SKU_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_]{1,39}")


def _parse_shop_skus(raw: str) -> list[str]:
    skus = [DEFAULT_SKU]
    for item in (raw or "").split(","):
        item = item.strip()
        if _SKU_RE.fullmatch(item) and item not in skus:
            skus.append(item)
    return skus


# Danh sách SKU được phép hiển thị/mua. Giá, tồn kho và trạng thái hoạt động luôn lấy từ web.
SHOP_SKUS = _parse_shop_skus(os.getenv("SHOP_SKUS", "gemini_plus,gemini_pro_account"))

# ==================== TỰ PHÁT HIỆN SẢN PHẨM TỪ WEB ====================
# GET /api/tg/products/list (Bearer BOT_TOKEN) trả mọi sản phẩm đang bán. Sản phẩm mới thêm trên
# dashboard tự xuất hiện trong menu, không cần sửa code. SHOP_SKUS chỉ còn là danh sách dự phòng
# khi web chưa tải được lần nào; nếu đã có cache thì lỗi mạng giữ nguyên cache cũ (không làm menu trống).
import json as _json
import threading as _threading
import time as _time

CATALOGUE_TTL_SEC = 45.0
CATALOGUE_RETRY_SEC = 10.0
CATALOGUE_MAX_ITEMS = 20
_catalogue_lock = _threading.Lock()
_catalogue: dict = {"items": None, "next": 0.0}  # items: {sku: row} hoặc None nếu chưa tải được lần nào


def _clean_catalogue(payload) -> dict:
    """Kiểm tra chặt dữ liệu từ web; sản phẩm không hợp lệ bị bỏ, không làm hỏng cả danh sách."""
    if not isinstance(payload, dict) or payload.get("success") is not True or not isinstance(payload.get("products"), list):
        raise ValueError("invalid catalogue payload")
    items: dict = {}
    for raw in payload["products"][:100]:
        if not isinstance(raw, dict):
            continue
        sku, name, description = raw.get("sku"), raw.get("name"), raw.get("description")
        price_vnd, price_usdt, max_qty = raw.get("price_vnd"), raw.get("price_usdt"), raw.get("max_qty")
        if not isinstance(sku, str) or not _SKU_RE.fullmatch(sku):
            continue
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
            continue
        if description is not None and not isinstance(description, str):
            continue
        if raw.get("type") != "item":  # key/credits được giao theo luồng khác
            continue
        if isinstance(price_vnd, bool) or not isinstance(price_vnd, int) or not 0 < price_vnd <= 100_000_000:
            continue
        if price_usdt is not None and (isinstance(price_usdt, bool) or not isinstance(price_usdt, (int, float)) or not 0 < price_usdt <= 1_000_000):
            continue
        if isinstance(max_qty, bool) or not isinstance(max_qty, int) or not 1 <= max_qty <= 50:
            continue
        if not isinstance(raw.get("in_stock"), bool):
            continue
        items[sku] = {
            "sku": sku, "name": name.strip(), "description": (description or "")[:2000],
            "price_vnd": price_vnd, "price_usdt": price_usdt, "max_qty": max_qty, "in_stock": raw["in_stock"],
        }
        if len(items) >= CATALOGUE_MAX_ITEMS:
            break
    return items


def _fetch_catalogue_payload():
    import urllib.request
    if not BOT_TOKEN:
        raise RuntimeError("no bot token")
    req = urllib.request.Request("https://www.wolfmod.xyz/api/tg/products/list",
                                 headers={"Authorization": f"Bearer {BOT_TOKEN}", "User-Agent": "TelegramShopBot"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return _json.loads(resp.read(262144).decode("utf-8"))


def _store_catalogue(items: dict) -> None:
    """Ghi tên/mô tả/giá/max_qty của sản phẩm phát hiện được vào SQLite để mọi luồng hiện có (get_product...) dùng lại."""
    conn = get_db_connection()
    try:
        for sku, item in items.items():
            if sku == DEFAULT_SKU:
                continue  # gemini_plus giữ nguyên tên/mô tả cục bộ; giá được đồng bộ bởi sync_web_product_price
            conn.execute(
                """INSERT INTO products (id, name, description, price, guide_text, max_qty) VALUES (?, ?, ?, ?, '', ?)
                   ON CONFLICT(id) DO UPDATE SET name = excluded.name, description = excluded.description,
                       price = excluded.price, max_qty = excluded.max_qty""",
                (sku, item["name"], item["description"], item["price_vnd"], item["max_qty"]),
            )
            if item["price_usdt"] is not None:
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                             (usdt_setting_key(sku), str(item["price_usdt"])))
        conn.commit()
    finally:
        conn.close()


def refresh_catalogue(force: bool = False) -> bool:
    """Tải danh mục từ web (cache ~45 s). Lỗi: giữ cache cũ, thử lại sau ~10 s. Trả True nếu danh mục hiện có là dữ liệu web."""
    with _catalogue_lock:
        now = _time.monotonic()
        if not force and now < _catalogue["next"]:
            return _catalogue["items"] is not None
        try:
            items = _clean_catalogue(_fetch_catalogue_payload())
            _store_catalogue(items)
        except Exception as error:
            _catalogue["next"] = now + CATALOGUE_RETRY_SEC
            if not (isinstance(error, RuntimeError) and str(error) == "no bot token"):
                logger.warning("Không tải được danh mục sản phẩm từ web: %s", type(error).__name__)
            return _catalogue["items"] is not None
        _catalogue["items"] = items
        _catalogue["next"] = now + CATALOGUE_TTL_SEC
        return True


def known_skus() -> list[str]:
    """SKU được phép bán: danh mục web nếu đã tải được, nếu chưa thì SHOP_SKUS (dự phòng). gemini_plus luôn đứng đầu."""
    items = _catalogue["items"]
    if items is None:
        return list(SHOP_SKUS)
    return [DEFAULT_SKU] + [sku for sku in items if sku != DEFAULT_SKU]


def sku_in_stock_hint(sku: str) -> bool:
    """Còn hàng theo danh mục web; chưa có danh mục thì hỏi trực tiếp kho. (Màn mua vẫn kiểm tra kho thật.)"""
    items = _catalogue["items"]
    if items is not None and sku in items:
        return bool(items[sku]["in_stock"])
    return get_stock_count(sku) > 0


def get_db_connection():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    # Customer money lives in this file: write-ahead log + full fsync, wait for locks instead of failing, enforce FKs.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_database():
    """Khởi tạo các bảng SQLite và nạp dữ liệu mặc định"""
    conn = get_db_connection()
    cursor = conn.cursor()
    if wallet_enabled():
        wallet_store.ensure_schema(conn)

    # 1. Bảng sản phẩm
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS products (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        price INTEGER NOT NULL DEFAULT 20000,
        guide_text TEXT
    )
    """)

    # 2. Bảng kho hàng (Stock links/tài khoản)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS stock (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id TEXT NOT NULL,
        content TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'available',
        order_id TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        sold_at TIMESTAMP,
        sold_to_user INTEGER
    )
    """)

    # 3. Bảng đơn hàng (Orders)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS orders (
        order_id TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL,
        user_name TEXT,
        product_id TEXT NOT NULL,
        amount INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        item_delivered TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at TIMESTAMP
    )
    """)

    # Số lượng tối đa mỗi đơn của từng sản phẩm (đồng bộ với tg_products.max_qty trên web)
    try:
        cursor.execute("ALTER TABLE products ADD COLUMN max_qty INTEGER NOT NULL DEFAULT 50")
    except sqlite3.OperationalError:
        pass

    # Tự động bổ sung các cột mở rộng cho orders nếu chưa có
    try:
        cursor.execute("ALTER TABLE orders ADD COLUMN payment_method TEXT DEFAULT 'vietqr'")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE orders ADD COLUMN currency TEXT DEFAULT 'VND'")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE orders ADD COLUMN quantity INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE orders ADD COLUMN expires_at TIMESTAMP")
    except sqlite3.OperationalError:
        pass
    for column in ("bank_name", "bank_account", "bank_owner"):
        try:
            cursor.execute(f"ALTER TABLE orders ADD COLUMN {column} TEXT")
        except sqlite3.OperationalError:
            pass
    try:
        cursor.execute("ALTER TABLE orders ADD COLUMN delivery_notified_at TIMESTAMP")
        # Existing completed orders were already handled by the old bot.
        cursor.execute("UPDATE orders SET delivery_notified_at = COALESCE(completed_at, CURRENT_TIMESTAMP) WHERE status = 'completed'")
    except sqlite3.OperationalError:
        pass

    # Loại đơn: 'purchase' (mặc định) hoặc 'wallet_topup' (nạp ví, không có hàng để giao).
    try:
        cursor.execute("ALTER TABLE orders ADD COLUMN kind TEXT NOT NULL DEFAULT 'purchase'")
    except sqlite3.OperationalError:
        pass
    if wallet_enabled():
        # Trả bằng ví: cột review_reason + chỉ mục một đơn ví đang xử lý mỗi khách (chống bấm đúp / race).
        wallet_pay.ensure_schema(conn)

    # 4. Bảng cấu hình hệ thống (Settings)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)

    # 5. Ngôn ngữ giao diện theo từng khách (vi/en). Idempotent: chỉ CREATE IF NOT EXISTS, không đụng dữ liệu cũ.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_lang (
        user_id INTEGER PRIMARY KEY,
        lang TEXT NOT NULL,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # Khởi tạo sản phẩm Gemini Plus mặc định nếu chưa có
    cursor.execute("SELECT id FROM products WHERE id = 'gemini_plus'")
    if not cursor.fetchone():
        cursor.execute("""
        INSERT INTO products (id, name, description, price, guide_text)
        VALUES (
            'gemini_plus',
            'Google AI Plus (400 GB Cloud)',
            'Gói ưu đãi Google One AI Premium kèm Gemini Advanced model xịn nhất từ Google, dung lượng 2TB lưu trữ.',
            20000,
            'Hướng dẫn kích hoạt:\n1. Mở link kích hoạt trên trình duyệt đã đăng nhập Gmail của bạn.\n2. Bấm \"Bắt đầu dùng thử\" hoặc \"Nhận ưu đãi\".\n3. Hoàn tất xác nhận trên Google One.'
        )
        """)
    else:
        cursor.execute("UPDATE products SET name = 'Google AI Plus (400 GB Cloud)' WHERE id = 'gemini_plus'")

    # Sản phẩm thứ hai: tài khoản Gemini Pro (giá thật được đồng bộ từ web trước khi tạo đơn)
    cursor.execute("""
    INSERT OR IGNORE INTO products (id, name, description, price, guide_text, max_qty)
    VALUES (
        'gemini_pro_account',
        'ACCOUNT GEMINI PRO FULL BENEFITS',
        'Tài khoản Gmail|password. Google AI Pro với Gemini 4 Argon; quyền truy cập mở rộng Gemini 3.1 Pro và Deep Research; tạo ảnh, nhạc, video; Gemini Notebook; Google AI Studio, Antigravity, Colab; bộ nhớ dùng chung 5 TB. Quyền lợi có thể thay đổi theo khu vực và điều khoản Google.',
        125000,
        'Đăng nhập bằng Gmail|password nhận được trong tin nhắn giao hàng.',
        1
    )
    """)
    cursor.execute("UPDATE products SET max_qty = 1 WHERE id = 'gemini_pro_account'")
    # Mọi SKU khác trong SHOP_SKUS: tạo dòng rỗng, giá 0 = ẩn khỏi menu cho tới khi đồng bộ được giá từ web
    for extra_sku in SHOP_SKUS:
        cursor.execute(
            "INSERT OR IGNORE INTO products (id, name, description, price, guide_text, max_qty) VALUES (?, ?, '', 0, '', 1)",
            (extra_sku, extra_sku),
        )

    # Cài đặt mặc định nếu chưa có
    default_settings = {
        "bank_name": "ACB",
        "bank_account": "68986683",
        "bank_owner": "LE DONG HA",
        "test_mode": "false",       # Tắt hoàn toàn chế độ test tự nhận link theo yêu cầu của user
        "usdt_network": "BEP20",
        "usdt_wallet": os.getenv("USDT_WALLET", "").strip(),
        "usdt_price": "1",
        "support_contact": "@wolfmodyt",
        "web_admin_url": "https://www.wolfmod.xyz/m7k9xq2r/dashboard#tg-sales",
        "auto_push_web": "false"
    }
    for k, v in default_settings.items():
        cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))

    # Keep VietQR instructions attached to each order so payment details remain stable.
    cursor.execute(
        """UPDATE orders SET
               bank_name = COALESCE(NULLIF(bank_name, ''), (SELECT value FROM settings WHERE key = 'bank_name')),
               bank_account = COALESCE(NULLIF(bank_account, ''), (SELECT value FROM settings WHERE key = 'bank_account')),
               bank_owner = COALESCE(NULLIF(bank_owner, ''), (SELECT value FROM settings WHERE key = 'bank_owner'))
           WHERE payment_method = 'vietqr' OR payment_method IS NULL"""
    )

    if os.getenv("USDT_WALLET", "").strip():
        cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('usdt_wallet', ?)",
                       (os.environ["USDT_WALLET"].strip(),))

    # Đảm bảo test_mode luôn tắt theo yêu cầu user
    cursor.execute("UPDATE settings SET value = 'false' WHERE key = 'test_mode'")
    cursor.execute("UPDATE settings SET value = 'BEP20' WHERE key = 'usdt_network'")

    conn.commit()

    # Nạp link từ file claimed_links.txt vào kho nếu có
    import_claimed_links_to_db(cursor)
    conn.commit()
    conn.close()

def import_claimed_links_to_db(cursor):
    """Đã tắt nạp từ file máy - kho hàng được nạp và quản lý 100% qua Web Admin Dashboard"""
    pass

def get_setting(key, default=""):
    conn = get_db_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default

def set_setting(key, value):
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
    conn.commit()
    conn.close()

def get_product(prod_id="gemini_plus"):
    conn = get_db_connection()
    row = conn.execute("SELECT * FROM products WHERE id = ?", (prod_id,)).fetchone()
    conn.close()
    return row

_last_web_price_check: dict[str, float] = {}


def usdt_setting_key(sku: str) -> str:
    return "usdt_price" if sku == DEFAULT_SKU else f"usdt_price:{sku}"


def get_usdt_price(sku: str = DEFAULT_SKU) -> float:
    raw = get_setting(usdt_setting_key(sku), "")
    if raw == "":
        if sku != DEFAULT_SKU and product_content.usd_from_rate(sku):
            # Web chưa có giá USDT riêng: hiển thị theo tỉ giá cố định của bot (25.000 VNĐ = 1 USD).
            return i18n.usd_from_vnd(sku_price_vnd(sku))
        raw = "1"
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 1.0


def sync_web_product_price(prod_id="gemini_plus", force=False):
    """Refresh dashboard prices without changing any existing order amount."""
    import json
    import time
    import urllib.request
    now = time.monotonic()
    if not force and now - _last_web_price_check.get(prod_id, -1e9) < 10:
        return True
    if not BOT_TOKEN:
        return False
    try:
        url = f"https://www.wolfmod.xyz/api/tg/products/price?sku={prod_id}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {BOT_TOKEN}"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        vnd, usdt = data.get("price_vnd"), data.get("price_usdt")
        if (not data.get("success") or data.get("sku") != prod_id or
            isinstance(vnd, bool) or not isinstance(vnd, int) or vnd <= 0 or
            isinstance(usdt, bool) or not isinstance(usdt, (int, float)) or usdt <= 0):
            return False
        conn = get_db_connection()
        try:
            conn.execute("UPDATE products SET price = ? WHERE id = ?", (vnd, prod_id))
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (usdt_setting_key(prod_id), str(usdt)))
            conn.commit()
        finally:
            conn.close()
        _last_web_price_check[prod_id] = now
        return True
    except Exception as error:
        logger.warning("Không đồng bộ được giá dashboard: %s", type(error).__name__)
        return False

_web_stock_cache: dict[str, tuple[float, int]] = {}

BANNER_DIR = os.path.join(BASE_DIR, "assets", "products")
BANNER_EXTS = (".png", ".jpg", ".jpeg")


def get_product_banner_path(sku: str, lang: str | None = None) -> str | None:
    """Ảnh banner của sản phẩm theo ngôn ngữ: assets/products/<sku>_<lang>.(png|jpg); thiếu thì dùng ảnh ngôn ngữ kia;
    cuối cùng là ảnh cũ assets/<sku>.jpg; SKU không có ảnh -> None (chỉ văn bản)."""
    try:
        lang = i18n.normalize_lang(lang or get_default_lang())
    except Exception:
        lang = "vi"
    other = "en" if lang == "vi" else "vi"
    candidates = [os.path.join(BANNER_DIR, f"{sku}_{lg}{ext}") for lg in (lang, other) for ext in BANNER_EXTS]
    candidates.append(os.path.join(BASE_DIR, "assets", f"{sku}.jpg"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


# file_id Telegram của banner đã gửi, chỉ trong bộ nhớ (key = đường dẫn + mtime, nên đổi ảnh thì tự upload lại).
_banner_file_ids: dict[tuple[str, float], str] = {}


def _banner_key(path: str) -> tuple[str, float]:
    try:
        return (path, os.path.getmtime(path))
    except OSError:
        return (path, 0.0)

def get_stock_count(prod_id="gemini_plus"):
    import time
    import urllib.request
    import json

    now = time.time()
    cached = _web_stock_cache.get(prod_id)
    if cached is not None and (now - cached[0]) < 3:
        return cached[1]

    # 1. Lấy trực tiếp từ Web wolfmod.xyz để luôn đồng bộ 100% với Web Admin
    try:
        url = f"https://www.wolfmod.xyz/api/tg/stock/available?sku={prod_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "TelegramShopBot", "Authorization": f"Bearer {BOT_TOKEN}"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("success") and "available" in data:
                val = int(data["available"])
                _web_stock_cache[prod_id] = (now, val)
                return val
    except Exception:
        pass

    # 2. Fallback sang SQLite nếu mất mạng
    conn = get_db_connection()
    count = conn.execute(
        "SELECT COUNT(*) as cnt FROM stock WHERE product_id = ? AND status = 'available'",
        (prod_id,)
    ).fetchone()["cnt"]
    conn.close()
    _web_stock_cache[prod_id] = (now, count)
    return count

def push_stock_to_web(sku="gemini_plus", links=None):
    """Chỉ đẩy link lên Web khi được Admin cho phép (mặc định TẮT theo yêu cầu của Admin)"""
    if not links:
        return
    if get_setting("auto_push_web", "false").lower() != "true":
        return
    import urllib.request
    import json
    try:
        url = f"https://www.wolfmod.xyz/api/admin/tg/products/{sku}/stock"
        payload = json.dumps({"items": links}).encode("utf-8")
        token = BOT_TOKEN
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "TelegramShopBot"
            }
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            logger.info(f"Đã đồng bộ {len(links)} link lên Web Admin kho {sku}: {data.get('total_available')} link khả dụng")
    except Exception as e:
        logger.warning(f"Lỗi đẩy stock lên web: {e}")

def claim_stock_from_web(sku="gemini_plus", order_id="", content=""):
    """Lấy 1 link trực tiếp từ kho hàng trên Web Dashboard (wolfmod.xyz) hoặc đồng bộ link đã xuất lên Web"""
    import urllib.request
    import json
    try:
        url = "https://www.wolfmod.xyz/api/tg/stock/claim"
        body_dict = {"sku": sku, "order_code": order_id}
        if content:
            body_dict["content"] = content
        payload = json.dumps(body_dict).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {BOT_TOKEN}"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("success") and data.get("content"):
                return data["content"]
    except Exception as e:
        logger.warning(f"Lỗi claim/đồng bộ link từ web backend: {e}")
    return None

def claim_stock_detailed(sku: str, order_id: str):
    """Như claim_stock_from_web nhưng phân biệt kết quả cho đơn trả bằng ví:
    (WEB_OK, nội dung) | (WEB_SOLD_OUT, None) chắc chắn hết hàng | (WEB_UNCERTAIN, None) mọi trường hợp còn lại."""
    try:
        status, body = _post_web_json("/api/tg/stock/claim", {"sku": sku, "order_code": order_id}, timeout=15)
    except Exception:
        return wallet_pay.WEB_UNCERTAIN, None
    if status == 200 and body.get("success") is True and isinstance(body.get("content"), str) and body["content"]:
        return wallet_pay.WEB_OK, body["content"]
    if status == 409 and "insufficient stock" in str(body.get("error", "")).lower():
        return wallet_pay.WEB_SOLD_OUT, None
    return wallet_pay.WEB_UNCERTAIN, None


def deliver_product(order_id, user_id, bundle=None):
    """Xuất link từ kho và gán cho đơn hàng theo đúng số lượng mua.
    ``bundle``: nội dung web đã giao (chỉ luồng trả bằng ví truyền vào, tránh claim lần hai)."""
    conn = get_db_connection()
    cursor = conn.cursor()

    order = cursor.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
    if not order:
        conn.close()
        return None

    if int(order["user_id"]) != int(user_id):
        conn.close()
        return None
    if order["status"] == "completed":
        links = (order["item_delivered"] or "").splitlines()
        conn.close()
        return links or None
    if order["payment_method"] == "wallet" and bundle is None:
        # Đơn ví chỉ được giao bởi wallet_pay.process_order (đã trừ tiền, chưa hoàn); đơn đã hoàn không bao giờ được giao.
        conn.close()
        return None

    qty = 1
    if "quantity" in order.keys() and order["quantity"]:
        try:
            qty = max(1, int(order["quantity"]))
        except Exception:
            qty = 1

    # The web claims the complete quantity atomically and returns the same bundle on retries.
    if bundle is None:
        bundle = claim_stock_from_web(order["product_id"], order_id)
    delivered_links = bundle.splitlines() if bundle else []
    if len(delivered_links) != qty:
        conn.close()
        return None
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    all_links_text = "\n".join(delivered_links)

    # Cập nhật đơn hàng thành công
    cursor.execute("""
    UPDATE orders 
    SET status = 'completed', item_delivered = ?, completed_at = ? 
    WHERE order_id = ? AND status != 'completed'
    """, (all_links_text, now, order_id))
    if cursor.rowcount == 0:
        # Another worker completed this order first: return exactly what it stored (never a second link).
        row = cursor.execute("SELECT item_delivered FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        conn.close()
        return (row["item_delivered"] or "").splitlines() or None

    conn.commit()
    conn.close()

    # Ghi nhận từng link đã giao vào file used_links.txt và xóa khỏi claimed_links.txt
    used_file = os.path.join(DATA_DIR, "used_links.txt")
    for link_str in delivered_links:
        try:
            with open(used_file, "a", encoding="utf-8") as f_u:
                f_u.write(f"[{now}] Đơn: {order_id} | User ID: {user_id} | Link: {link_str}\n")
        except Exception as e_log:
            logger.warning(f"Lỗi ghi log used_links.txt: {e_log}")

        try:
            if os.path.exists(CLAIMED_LINKS_FILE):
                with open(CLAIMED_LINKS_FILE, "r", encoding="utf-8") as f_cl:
                    remaining = [line for line in f_cl if line.strip() != link_str]
                with open(CLAIMED_LINKS_FILE, "w", encoding="utf-8") as f_cl:
                    f_cl.writelines(remaining)
        except Exception as e_rm:
            logger.warning(f"Lỗi cập nhật file claimed_links.txt: {e_rm}")

    return delivered_links

# ==================== VIETQR CHUẨN PNG CHO TELEGRAM ====================

BANK_BINS = {
    "ACB": "970416",
    "MB": "970422",
    "MBBANK": "970422",
    "VCB": "970436",
    "VIETCOMBANK": "970436",
    "TCB": "970407",
    "TECHCOMBANK": "970407",
    "BIDV": "970418",
    "VIB": "970441",
    "VPB": "970432",
    "VPBANK": "970432",
    "TPB": "970423",
    "TPBANK": "970423",
    "OCB": "970448",
    "STB": "970403",
    "SACOMBANK": "970403",
    "CTG": "970415",
    "VIETINBANK": "970415",
}

def crc16_ccitt(data: str) -> str:
    crc = 0xFFFF
    for b in data.encode("utf-8"):
        crc ^= (b << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return f"{crc:04X}"

def build_vietqr_emvco_payload(bank_bin: str, account_no: str, amount: int, memo: str) -> str:
    """Tạo chuỗi QR thanh toán chuẩn EMVCo NAPAS (VietQR QRIBFTTA) quét nhận diện 100% trên tất cả App ngân hàng"""
    def fld(tag: str, val: str) -> str:
        return f"{tag}{len(val.encode('utf-8')):02d}{val}"

    guid = fld("00", "A000000727")
    beneficiary = fld("00", str(bank_bin).strip()) + fld("01", str(account_no).strip())
    service = fld("02", "QRIBFTTA")
    tag38 = fld("38", guid + fld("01", beneficiary) + service)
    tag62 = fld("62", fld("08", str(memo).strip()))

    payload_no_crc = (
        fld("00", "01") +
        fld("01", "12") +
        tag38 +
        fld("53", "704") +
        fld("54", str(amount).strip()) +
        fld("58", "VN") +
        tag62 +
        "6304"
    )
    return payload_no_crc + crc16_ccitt(payload_no_crc)

def generate_vietqr_png_bytes(bank_name: str, bank_account: str, bank_owner: str, amount: int, order_id: str) -> bytes | None:
    """
    Sinh ảnh VietQR chuẩn định dạng PNG (binary bytes) tương thích 100% sendPhoto của Telegram.
    Cả 2 cách (API VietQR hoặc Offline EMVCo) đều chứa chuỗi chuẩn NAPAS quốc gia để mọi App ngân hàng quét là tự điền đúng STK, Tên, Số tiền và Nội dung.
    """
    import base64
    import json
    import urllib.request

    bank_clean = str(bank_name).strip().upper()
    acq_id = BANK_BINS.get(bank_clean, "970416")

    # Cách 1: Gọi API VietQR v2 để nhận ảnh PNG base64 đầy đủ logo ngân hàng và khung chuyên nghiệp
    try:
        payload = {
            "accountNo": str(bank_account).strip(),
            "accountName": str(bank_owner).strip(),
            "acqId": acq_id,
            "amount": int(amount),
            "addInfo": str(order_id).strip(),
            "format": "text",
            "template": "compact2"
        }
        req = urllib.request.Request(
            "https://api.vietqr.io/v2/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            res_data = json.loads(resp.read().decode("utf-8"))
            if res_data.get("code") == "00":
                qr_data_url = res_data.get("data", {}).get("qrDataURL", "")
                if qr_data_url and "base64," in qr_data_url:
                    _, b64_str = qr_data_url.split("base64,", 1)
                    raw_png = base64.b64decode(b64_str)
                    if raw_png.startswith(b"\x89PNG"):
                        return raw_png
    except Exception as e_api:
        logger.warning(f"Lỗi gọi api.vietqr.io/v2/generate: {e_api}")

    # Cách 2: Dự phòng Offline tiêu chuẩn quốc gia NAPAS EMVCo (quét 100% nhận diện trên tất cả App ngân hàng)
    try:
        import qrcode
        emvco_payload = build_vietqr_emvco_payload(acq_id, bank_account, amount, order_id)
        qr = qrcode.QRCode(box_size=10, border=3)
        qr.add_data(emvco_payload)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        val = buf.getvalue()
        if val.startswith(b"\x89PNG"):
            return val
    except Exception as e_qr:
        logger.warning(f"Lỗi sinh QR offline bằng EMVCo: {e_qr}")

    return None

def generate_crypto_qr_png_bytes(address: str) -> bytes | None:
    """Sinh ảnh mã QR trực tiếp cho địa chỉ ví tiền ảo USDT (TRC20/BEP20)"""
    try:
        import qrcode
        qr = qrcode.QRCode(box_size=10, border=3)
        qr.add_data(str(address).strip())
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        val = buf.getvalue()
        if val.startswith(b"\x89PNG"):
            return val
    except Exception as e_qr:
        logger.warning(f"Lỗi sinh QR crypto bằng qrcode: {e_qr}")
    return None

# ==================== MENU & GIAO DIỆN INLINE (VIP EDITION WITH ANIMATED EMOJIS) ====================

VIP_EMOJIS = {
    "crown": "5807868868886009920",      # 👑 Vương miện VIP
    "diamond": "5963312935148195483",    # 💎 Kim cương VIP
    "star": "5874948844935974490",       # ⭐️ Ngôi sao sáng
    "sparkle": "5874948844935974490",    # ✨ Phát sáng
    "lightning": "5843553939672274145",   # ⚡️ Tia chớp tốc độ
    "fire": "6008118472066732010",        # 🔥 Ngọn lửa hot
    "shield": "5926783847453692661",      # 🛡 Khiên bảo vệ/bảo mật
    "box": "5924720918826848520",         # 📦 Kiện hàng/kho link
    "book": "5897850551156084824",        # 📖 Cẩm nang hướng dẫn
    "chat": "5886666250158870040",        # 💬 CSKH hỗ trợ
    "card": "5967548335542767952",        # 💳 Thẻ ngân hàng/VietQR
    "bank": "5967548335542767952",        # 🏦 Ngân hàng
    "check": "5776375003280838798",       # ✅ Tích xanh hoàn tất
    "cross": "5778527486270770928",       # ❌ Nút hủy/đóng
    "globe": "5879585266426973039",       # 🌐 Quả cầu/Mạng lưới Network
    "money": "5811989245761426317",       # 💰 Tiền thưởng/Giá bán
    "party": "5994502837327892086",       # 🎉 Pháo hoa chúc mừng
    "key": "6005570495603282482",         # 🔑 Chìa khóa/Link kích hoạt
    "lock": "5832546462478635761",        # 🔒 Bảo mật tuyệt đối
    "usdt": "5292251044204521072",        # 🪙 Đồng xu USDT Crypto
    "wallet": "5769403330761593044",      # 👛 Ví tiền/Địa chỉ ví nhận
    "memo": "5897737086710059363",        # 📝 Ghi chú/Nội dung CK/Memo
    "user": "5771887475421090729",        # 👤 Người dùng/Chủ tài khoản
    "tag": "5843478103434728287",         # 🏷 Thẻ mã đơn hàng VIP
    "chart": "5933629020301169337",       # 📊 Biểu đồ so sánh giá / thống kê VIP
    "calendar": "5967412305338568701",    # 📅 Ngày giờ đơn hàng
    "num1": "5798769667136819756",        # 1️⃣ Bước 1
    "num2": "5960582701747539918",        # 2️⃣ Bước 2
    "num3": "5798927915206840847",        # 3️⃣ Bước 3
    "refresh": "5839200986022812209",     # 🔄 Chọn lại / Làm mới
    "edit": "5879841310902324730",        # ✏️ Nhập tùy chỉnh / chỉnh sửa
    "back": "5875082500023258804",        # 🔙 Quay lại / trở về
}

FALLBACK_BUTTON_EMOJIS = {
    "crown": "👑",
    "diamond": "💎",
    "star": "⭐️",
    "sparkle": "✨",
    "lightning": "⚡️",
    "fire": "🔥",
    "shield": "🛡",
    "box": "📦",
    "book": "📖",
    "chat": "💬",
    "card": "💳",
    "bank": "🏦",
    "check": "✅",
    "cross": "❌",
    "globe": "🌐",
    "money": "💰",
    "party": "🎉",
    "key": "🔑",
    "lock": "🔒",
    "usdt": "🪙",
    "wallet": "👛",
    "memo": "📝",
    "user": "👤",
    "tag": "🏷",
    "chart": "📊",
    "calendar": "📅",
    "num1": "1️⃣",
    "num2": "2️⃣",
    "num3": "3️⃣",
    "refresh": "🔄",
    "back": "🔙",
    "edit": "✏️",
}

def vip_em(name: str, fallback: str = "") -> str:
    """Trả về thẻ HTML <tg-emoji> chuẩn động cho Telegram Premium. Luôn đảm bảo ký tự bên trong là UTF-16 Emoji hợp lệ."""
    cid = VIP_EMOJIS.get(name, "")
    std_emoji = FALLBACK_BUTTON_EMOJIS.get(name, "⭐️")
    if cid:
        # Telegram Bot API nghiêm ngặt yêu cầu nội dung bên trong <tg-emoji> PHẢI là emoji chuẩn đơn.
        return f'<tg-emoji emoji-id="{cid}">{std_emoji}</tg-emoji>'
    return fallback or std_emoji

def strip_button_emojis(text: str) -> str:
    """Loại bỏ các emoji/biểu tượng thừa ở đầu/cuối nhãn để chuẩn hóa chữ gốc"""
    s = text.strip()
    pattern_start = r"^(?:[0-9#*]️?⃣|[\s👑💎🌟✨⚡️🔥🛡📦📖💬💳🏦✅❌🌐💰🎉🔑🔒🪙👛📝👤🏷📅👇💡⚠️⛔🟢🔴🔙🔄✏️⚜️◈✦☀-⛿✀-➿🌀-🧿])+\s*"
    pattern_end = r"\s*(?:[0-9#*]️?⃣|[\s👑💎🌟✨⚡️🔥🛡📦📖💬💳🏦✅❌🌐💰🎉🔑🔒🪙👛📝👤🏷📅👇💡⚠️⛔🟢🔴🔙🔄✏️⚜️◈✦☀-⛿✀-➿🌀-🧿])+$"
    s = re.sub(pattern_start, "", s)
    s = re.sub(pattern_end, "", s)
    return s.strip()

def vip_btn(text: str, callback_data: str = None, url: str = None, emoji_name: str = None, style: str = None) -> InlineKeyboardButton:
    """Tạo nút InlineKeyboardButton VIP với DUY NHẤT 1 custom emoji id (icon_custom_emoji_id),
    TUYỆT ĐỐI KHÔNG DÙNG ICON THƯỜNG TRONG TEXT NHÃN NÚT THEO QUY TẮC GEMINI.MD."""
    clean_label = strip_button_emojis(text)
    kwargs = {}
    if style:
        kwargs["style"] = style

    if emoji_name and emoji_name in VIP_EMOJIS:
        kwargs["icon_custom_emoji_id"] = VIP_EMOJIS[emoji_name]
    elif emoji_name == "memo":
        kwargs["icon_custom_emoji_id"] = VIP_EMOJIS.get("edit", VIP_EMOJIS["memo"])

    # Nhãn nút CHỈ chứa text thuần túy, KHÔNG chứa emoji thông thường
    return InlineKeyboardButton(text=clean_label, callback_data=callback_data, url=url, api_kwargs=kwargs if kwargs else None)

def sku_max_qty(sku: str) -> int:
    """Số lượng tối đa mỗi đơn của SKU (tối đa 50, khớp giới hạn của web)."""
    row = get_product(sku)
    try:
        return max(1, min(50, int(row["max_qty"]))) if row else 1
    except (KeyError, TypeError, ValueError):
        return 1


def sku_display_name(sku: str) -> str:
    if sku == DEFAULT_SKU:
        return "Google AI Plus (400 GB Cloud)"
    row = get_product(sku)
    return str(row["name"]) if row else sku


def order_sku(order) -> str:
    """SKU thật của đơn hàng cục bộ (cột product_id)."""
    return str(order["product_id"])


def cb_buy(sku: str) -> str:
    return "buy_gemini_plus" if sku == DEFAULT_SKU else f"buy_{sku}"


def cb_qty(kind: str, sku: str, qty: int) -> str:
    """Callback data cho chọn số lượng / tạo đơn. gemini_plus giữ nguyên định dạng cũ."""
    if kind not in ("bep20", "binance", "wallet") and sku == DEFAULT_SKU:
        return {"select": f"select_qty_{qty}", "vietqr": f"create_order_vietqr_{qty}", "usdt": f"create_order_usdt_{qty}"}[kind]
    prefix = {"select": "sq", "vietqr": "cv", "usdt": "cu", "bep20": "cb", "binance": "cp", "wallet": "wp"}[kind]
    return f"{prefix}:{sku}:{qty}"


_SKU_CB_RE = re.compile(r"(sq|cv|cu|cb|cp|wp):([A-Za-z0-9][A-Za-z0-9_]{1,39}):(\d{1,3})")


def parse_sku_qty_callback(data: str):
    """('sq'|'cv'|'cu'|'cb', sku, qty) cho callback đa sản phẩm; None nếu không khớp hoặc SKU không được bán."""
    match = _SKU_CB_RE.fullmatch(data or "")
    if not match or match.group(2) not in known_skus():
        return None
    return match.group(1), match.group(2), int(match.group(3))


def sync_order_to_web_sync(order_id: str, tg_user_id: int, tg_username: str, amount_vnd: int, currency: str = "VND", provider: str = "sepay", qty: int = 1, sku: str = DEFAULT_SKU):
    """Đồng bộ đơn hàng lên Web Backend (wolfmod.xyz) để SePay và Admin Dashboard nhận diện"""
    import urllib.request
    import json
    try:
        url = "https://www.wolfmod.xyz/api/tg/orders/sync"
        payload = {
            "order_code": str(order_id).strip(),
            "tg_user_id": int(tg_user_id),
            "tg_username": str(tg_username or ""),
            "sku": sku,
            "product_name": sku_display_name(sku),
            "qty": int(qty),
            "amount_vnd": int(amount_vnd),
            "currency": currency,
            "provider": provider
        }
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {BOT_TOKEN}"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            logger.info(f"Đã đồng bộ đơn {order_id} (x{qty}) lên wolfmod.xyz: code {resp.status}")
    except Exception as e:
        logger.warning(f"Lỗi đồng bộ đơn {order_id} lên web: {e}")

async def sync_order_to_web_async(order_id: str, tg_user_id: int, tg_username: str, amount_vnd: int, currency: str = "VND", provider: str = "sepay", qty: int = 1, sku: str = DEFAULT_SKU):
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, sync_order_to_web_sync, order_id, tg_user_id, tg_username, amount_vnd, currency, provider, qty, sku)
    except Exception as e:
        logger.warning(f"Lỗi chạy sync_order_to_web_async: {e}")

# ==================== NGÔN NGỮ & HIỂN THỊ SẢN PHẨM ====================
def get_default_lang() -> str:
    """Ngôn ngữ mặc định cho khách mới: 'en' (khớp web); chủ shop đổi được bằng setting `default_lang`."""
    return i18n.normalize_lang(get_setting("default_lang", i18n.DEFAULT_LANG))


def get_user_lang(user_id) -> str:
    """Ngôn ngữ đã lưu của khách. Chưa lưu: khách ĐÃ có đơn hàng -> 'vi' (giữ trải nghiệm cũ), khách mới -> mặc định ('en')."""
    try:
        uid = int(user_id)
    except (TypeError, ValueError):
        return get_default_lang()
    conn = get_db_connection()
    try:
        row = conn.execute("SELECT lang FROM user_lang WHERE user_id = ?", (uid,)).fetchone()
        if row:
            return i18n.normalize_lang(row["lang"])
        has_history = conn.execute("SELECT 1 FROM orders WHERE user_id = ? AND kind <> 'wallet_topup' LIMIT 1", (uid,)).fetchone()
    finally:
        conn.close()
    return i18n.LEGACY_LANG if has_history else get_default_lang()


def set_user_lang(user_id, lang) -> str:
    code = i18n.normalize_lang(lang)
    conn = get_db_connection()
    try:
        conn.execute(
            "INSERT INTO user_lang (user_id, lang, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(user_id) DO UPDATE SET lang = excluded.lang, updated_at = CURRENT_TIMESTAMP",
            (int(user_id), code),
        )
        conn.commit()
    finally:
        conn.close()
    return code


def listed_skus() -> list[str]:
    """SKU hiện trong mục Mua hàng: danh mục web + SKU có trong product_content.json mà web chưa trả về (luôn hiện, ghi Hết hàng)."""
    skus = list(known_skus())
    for sku in product_content.listed_skus():
        if sku not in skus:
            skus.append(sku)
    return skus


def sku_price_vnd(sku: str) -> int:
    row = get_product(sku)
    price = int(row["price"] or 0) if row else 0
    return price if price > 0 else product_content.price_fallback(sku)


def sku_title(sku: str, lang) -> str:
    """Tên hiển thị đầy đủ theo ngôn ngữ (từ product_content.json; chưa có thì tên từ web/DB)."""
    return product_content.name(sku, lang, "") or sku_display_name(sku)


def sku_is_in_stock(sku: str) -> bool:
    """Còn hàng để mua: phải là SKU web đang bán VÀ còn tồn kho."""
    return sku in known_skus() and sku_in_stock_hint(sku)


# Trạng thái chờ người dùng nhập số lượng tuỳ chỉnh: user_id -> sku
AWAITING_QTY_USERS: dict[int, str] = {}
DELIVERY_LOCK = asyncio.Lock()

def format_delivered_links_message(order_id: str, delivered_links: list[str], sku: str = DEFAULT_SKU, lang: str = "vi") -> tuple[str, list]:
    """Định dạng tin nhắn và nút mở link sau khi thanh toán thành công (theo ngôn ngữ của khách)."""
    oid = html.escape(order_id)
    my_orders_btn = vip_btn(t(lang, "btn_my_orders"), callback_data="my_orders", emoji_name="box")
    back_btn = vip_btn(t(lang, "btn_back_menu"), callback_data="main_menu", emoji_name="back")
    head = (
        f"{vip_em('party', '🎉')} {t(lang, 'deliv_success', order_id=oid)}\n\n"
        f"{vip_em('check', '✅')} {t(lang, 'deliv_confirmed')}\n"
    )
    if product_content.field(sku, "delivery_kind", lang) == "offer_link":
        # Offer link (1 link/đơn): hiển thị link + nút mở + hướng dẫn; chỉ tạo nút url khi thật sự là http(s).
        links = [str(x).strip() for x in delivered_links if str(x).strip()]
        link_lines = "\n".join(f"<code>{html.escape(x)}</code>" for x in links)
        msg = (
            head +
            f"{vip_em('box', '📦')} <b>{t(lang, 'deliv_product')}:</b> {html.escape(sku_title(sku, lang))}\n"
            f"{vip_em('key', '🔗')} {t(lang, 'offer_title', n=len(links))}\n"
            f"{link_lines}\n\n"
            f"{vip_em('diamond', '💎')} {t(lang, 'offer_howto_title')}\n"
            f"{vip_em('num1', '1️⃣')} {t(lang, 'offer_step1')}\n"
            f"{vip_em('num2', '2️⃣')} {t(lang, 'offer_step2')}\n"
            f"{vip_em('num3', '3️⃣')} {t(lang, 'offer_step3')}\n\n"
            f"{vip_em('shield', '⚠️')} {t(lang, 'offer_single_use')}"
        )
        buttons = [[vip_btn(t(lang, "btn_open_offer"), url=x, emoji_name="crown", style="primary")]
                   for x in links if x.lower().startswith(("https://", "http://"))]
        return msg, buttons + [[my_orders_btn], [back_btn]]

    if sku != DEFAULT_SKU:
        # Tài khoản/nội dung không phải URL: không tạo nút url (Telegram sẽ từ chối), chỉ hiển thị dạng code.
        items_text = "\n".join(f"<code>{html.escape(item)}</code>" for item in delivered_links)
        msg = (
            head +
            f"{vip_em('box', '📦')} <b>{t(lang, 'deliv_product')}:</b> {html.escape(sku_title(sku, lang))}\n"
            f"{vip_em('key', '🔗')} <b>{t(lang, 'deliv_items', n=len(delivered_links))}:</b>\n{items_text}\n\n"
            f"{vip_em('crown', '👑')} {t(lang, 'deliv_thanks_support')}"
        )
        return msg, [[my_orders_btn], [back_btn]]

    howto = (
        f"{vip_em('diamond', '💎')} {t(lang, 'deliv_howto_title')}\n"
        f"{vip_em('num1', '1️⃣')} {t(lang, 'deliv_step1_single' if len(delivered_links) == 1 else 'deliv_step1_multi')}\n"
        f"{vip_em('num2', '2️⃣')} {t(lang, 'deliv_step2')}\n"
        f"{vip_em('num3', '3️⃣')} {t(lang, 'deliv_step3')}\n\n"
        f"{vip_em('crown', '👑')} {t(lang, 'deliv_thanks')}"
    )
    if len(delivered_links) == 1:
        single_link = delivered_links[0]
        msg = (
            head +
            f"{vip_em('key', '🔗')} {t(lang, 'deliv_plus_single_title')}\n<code>{html.escape(single_link)}</code>\n\n" +
            howto
        )
        buttons = [
            [vip_btn(t(lang, "btn_open_link"), url=single_link, emoji_name="crown", style="primary")],
            [my_orders_btn],
            [back_btn],
        ]
        return msg, buttons

    qty = len(delivered_links)
    links_text = ""
    buttons = []
    for idx, lk in enumerate(delivered_links, 1):
        links_text += f"{vip_em('star', '✦')} <b>{t(lang, 'deliv_link_n', idx=idx)}:</b> <code>{html.escape(lk)}</code>\n"
        buttons.append([vip_btn(t(lang, "btn_open_link_n", idx=idx), url=lk, emoji_name="crown", style="primary")])
    msg = (
        head +
        f"{vip_em('box', '📦')} {t(lang, 'deliv_plus_multi_qty', qty=qty)}\n\n"
        f"{vip_em('key', '🔗')} {t(lang, 'deliv_list_title', qty=qty)}\n"
        f"{links_text}\n" +
        howto
    )
    buttons.append([my_orders_btn])
    buttons.append([back_btn])
    return msg, buttons


async def send_paid_order_delivery(bot, order_id, user_id):
    """Claim the paid order idempotently and retry Telegram delivery until accepted."""
    async with DELIVERY_LOCK:
        conn = get_db_connection()
        row = conn.execute("SELECT delivery_notified_at, product_id FROM orders WHERE order_id = ? AND user_id = ?", (order_id, user_id)).fetchone()
        conn.close()
        if not row:
            return False
        if row["delivery_notified_at"]:
            return True
        links = await asyncio.to_thread(deliver_product, order_id, user_id)
        if not links:
            return False
        body, buttons = format_delivered_links_message(order_id, links, str(row["product_id"]), get_user_lang(user_id))
        try:
            await bot.send_message(chat_id=int(user_id), text=body, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
        except Exception as error:
            logger.warning("Không gửi được link đơn %s: %s", order_id, type(error).__name__)
            return False
        conn = get_db_connection()
        conn.execute("UPDATE orders SET delivery_notified_at = CURRENT_TIMESTAMP WHERE order_id = ? AND user_id = ?", (order_id, user_id))
        conn.commit()
        conn.close()
        logger.info("Telegram đã nhận tin giao link cho đơn %s", order_id)
        return True

async def show_order_confirmation(target, user, sku="gemini_plus", qty=1):
    """Hiển thị màn hình xác nhận đơn hàng kèm nút thanh toán VietQR và USDT theo số lượng"""
    lang = get_user_lang(getattr(user, "id", 0))
    await asyncio.to_thread(sync_web_product_price, sku)
    prod = get_product(sku)
    price_unit = prod["price"]
    total_vnd = price_unit * qty
    price_unit_fmt = i18n.fmt_vnd(price_unit)
    total_vnd_fmt = i18n.fmt_vnd(total_vnd)

    usdt_unit = get_usdt_price(sku)
    total_usdt = round(usdt_unit * qty, 2)
    total_usdt_str = str(int(total_usdt)) if total_usdt.is_integer() else str(total_usdt)
    or_word = t(lang, "or_usdt")

    title = (f"{vip_em('crown', '👑')} ═══════ {vip_em('diamond', '💎')} <b>{t(lang, 'confirm_title')}</b> "
             f"{vip_em('diamond', '💎')} ═══════ {vip_em('crown', '👑')}\n\n")
    lines = [
        f"{vip_em('tag', '🌟')} <b>{t(lang, 'confirm_product')}:</b> <code>{html.escape(sku_title(sku, lang))}</code>\n",
        f"{vip_em('box', '📦')} <b>{t(lang, 'confirm_qty')}:</b> <b>{qty}</b>\n",
        f"{vip_em('chart', '🏷')} <b>{t(lang, 'confirm_unit')}:</b> <code>{price_unit_fmt}</code> ({or_word} <code>{usdt_unit} USDT</code>)\n",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
        f"{vip_em('money', '💰')} <b>{t(lang, 'confirm_total')}:</b> <b>{total_vnd_fmt}</b> ({or_word} <b>{total_usdt_str} USDT</b>)\n",
    ]
    duration = product_content.field(sku, "duration", lang)
    if duration:
        lines.append(f"{vip_em('calendar', '⏳')} <b>{t(lang, 'confirm_duration')}:</b> {html.escape(str(duration))}\n")
    warranty = product_content.field(sku, "warranty", lang)
    if warranty:
        lines.append(f"{vip_em('shield', '🛡')} <b>{t(lang, 'lbl_warranty')}:</b> {html.escape(str(warranty))}\n")
    text = title + "".join(lines) + f"\n{vip_em('lightning', '⚡️')} {t(lang, 'confirm_choose_method')}"

    keyboard = [
        [vip_btn(t(lang, "btn_pay_vietqr", total=total_vnd_fmt), callback_data=cb_qty("vietqr", sku, qty), emoji_name="card", style="primary")],
        [vip_btn(t(lang, "btn_pay_usdt", total=total_usdt_str), callback_data=cb_qty("usdt", sku, qty), emoji_name="usdt", style="success")],
        [vip_btn(t(lang, "btn_reselect_qty"), callback_data=cb_buy(sku), emoji_name="refresh")],
        [vip_btn(t(lang, "btn_back_menu"), callback_data="main_menu", emoji_name="back")]
    ]
    if wallet_pay_enabled() and _is_private_chat(getattr(target, "message", target)):
        # Tùy chọn thêm (mặc định TẮT): trả bằng số dư ví của bot, hiện số dư và đủ/thiếu.
        try:
            balance = await asyncio.to_thread(_wallet_balance, getattr(user, "id", 0))
        except Exception as error:
            logger.warning("Trả bằng ví: không đọc được số dư: %s", type(error).__name__)
            balance = None
        if balance is not None:
            label = t(lang, "btn_pay_wallet_ok" if balance >= total_vnd else "btn_pay_wallet_short",
                      balance=i18n.fmt_vnd(balance), missing=i18n.fmt_vnd(max(0, total_vnd - balance)))
            keyboard.insert(2, [vip_btn(label, callback_data=cb_qty("wallet", sku, qty), emoji_name="wallet")])
    if binance_pay_enabled():
        # Tùy chọn thêm (mặc định TẮT): USDT qua Binance Pay, khách trả đúng giá rồi dán ID lệnh.
        keyboard.insert(2, [vip_btn(t(lang, "btn_pay_binance"), callback_data=cb_qty("binance", sku, qty), emoji_name="usdt")])
    if bep20_enabled():
        # Tùy chọn thêm (mặc định TẮT): USDT BEP20 qua QR địa chỉ ví. Số tiền chính xác (có hậu tố duy nhất) hiện ở hóa đơn.
        keyboard.insert(2, [vip_btn(t(lang, "btn_pay_bep20"), callback_data=cb_qty("bep20", sku, qty), emoji_name="usdt")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    if hasattr(target, "edit_message_text"):
        await safe_edit_or_reply(target, text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await target.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")


def check_web_order_status(order_id: str) -> dict:
    """Kiểm tra trạng thái đơn hàng trên Web wolfmod.xyz xem SePay đã duyệt thanh toán chưa"""
    import urllib.request
    import json
    try:
        url = f"https://www.wolfmod.xyz/api/tg/orders/status?order_code={str(order_id).strip()}"
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {BOT_TOKEN}"}, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.warning(f"Lỗi kiểm tra trạng thái đơn {order_id} trên web: {e}")
    return {}

def sync_all_pending_orders_on_startup():
    """Đồng bộ tất cả đơn pending trong SQLite lên web khi bot khởi động"""
    try:
        conn = get_db_connection()
        pending = conn.execute("SELECT * FROM orders WHERE status = 'pending' AND kind <> 'wallet_topup'").fetchall()
        conn.close()
        for ord_row in pending:
            cur = ord_row["currency"] if "currency" in ord_row.keys() and ord_row["currency"] else "VND"
            if cur == "USDT":
                # Plisio invoices are created/priced by the web; never overwrite them via legacy sync.
                continue
            prov = "plisio" if cur == "USDT" else "sepay"
            sync_order_to_web_sync(
                ord_row["order_id"],
                ord_row["user_id"],
                ord_row["user_name"],
                ord_row["amount"] if cur == "VND" else 20000,
                cur,
                prov,
                qty=int(ord_row["quantity"] or 1),
                sku=str(ord_row["product_id"] or DEFAULT_SKU),
            )
        logger.info(f"Đã đồng bộ {len(pending)} đơn pending từ SQLite lên wolfmod.xyz")
    except Exception as e:
        logger.warning(f"Lỗi sync_all_pending_orders_on_startup: {e}")

async def safe_edit_or_reply(query, text, reply_markup=None, parse_mode="HTML", disable_web_page_preview=True):
    """
    Chỉnh sửa tin nhắn an toàn:
    - Nếu tin nhắn cũ là dạng ảnh (photo): xóa ảnh cũ và gửi tin nhắn mới, tránh lỗi 'There is no text in the message to edit'
    - Nếu tin nhắn cũ là text: gọi edit_message_text trực tiếp
    """
    try:
        if query.message and query.message.photo:
            try:
                await query.message.delete()
            except Exception:
                pass
            return await query.message.reply_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview
            )
        else:
            return await query.edit_message_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview
            )
    except Exception as e:
        logger.warning(f"Fallback safe_edit_or_reply: {e}")
        try:
            return await query.message.reply_text(
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview
            )
        except Exception as e2:
            logger.error(f"Lỗi gửi tin nhắn fallback: {e2}")

ORDERS_PAGE_SIZE = 5
ORDER_CONTENT_PREVIEW = 600  # Telegram giới hạn 4096 ký tự/tin nhắn; đơn nhiều link được rút gọn khi liệt kê
_VIEW_CB_RE = re.compile(r"(gd:([A-Za-z0-9][A-Za-z0-9_]{1,39})|ordp:(\d{1,4}))")
NO_GUIDE_TEXT = i18n.STRINGS["guide_none"]["vi"]  # giữ tên cũ; bản đúng ngôn ngữ: t(lang, "guide_none")


def parse_view_callback(data: str):
    """('gd', sku) hoặc ('ordp', số_trang); None nếu không hợp lệ."""
    match = _VIEW_CB_RE.fullmatch(data or "")
    if not match:
        return None
    if match.group(2):
        return "gd", match.group(2)
    return "ordp", int(match.group(3))


def has_own_guide(sku: str) -> bool:
    return product_guides.has_guide(sku)


def render_guide(sku: str, lang: str = "vi"):
    """Hướng dẫn RIÊNG của sản phẩm theo ngôn ngữ, hoặc None nếu sản phẩm chưa có (không bao giờ mượn nội dung SKU khác)."""
    guide = product_guides.get_guide(sku, lang)
    if guide is None:
        return None
    lines = [
        f"{vip_em('star', '⚜️')} ═══════ {vip_em('book', '📖')} <b>{guide['title']}</b> {vip_em('book', '📖')} ═══════ {vip_em('star', '⚜️')}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
        f"{vip_em('diamond', '💎')} <b>{guide['intro']}</b>\n\n",
    ]
    for number, step in enumerate(guide["steps"], 1):
        marker = vip_em(f"num{number}", f"{number}️⃣") if number <= 3 else f"{number}."
        lines.append(f"{marker} <b>{t(lang, 'guide_step', n=number)}:</b> {step}\n\n")
    lines.append(f"{vip_em('star', '💡')} <b>{guide['notes_title']}</b>\n")
    for emoji_name, fallback, text in guide["notes"]:
        lines.append(f"{vip_em(emoji_name, fallback)} {text}\n")
    lines.append(f"\n{vip_em('shield', '🛡')} {t(lang, 'guide_footer')}")
    return "".join(lines)


def render_orders_page(user_id: int, page: int, lang: str | None = None):
    """(text, keyboard) — đơn đã giao của CHÍNH khách này, mỗi đơn ghi đúng tên sản phẩm theo SKU của đơn."""
    lang = lang or get_user_lang(user_id)
    conn = get_db_connection()
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM orders WHERE user_id = ? AND status = 'completed' AND kind <> 'wallet_topup'", (user_id,)).fetchone()["n"]
        pages = max(1, (total + ORDERS_PAGE_SIZE - 1) // ORDERS_PAGE_SIZE)
        page = min(max(1, page), pages)
        rows = conn.execute(
            "SELECT * FROM orders WHERE user_id = ? AND status = 'completed' AND kind <> 'wallet_topup' "
            "ORDER BY completed_at DESC, created_at DESC, order_id DESC LIMIT ? OFFSET ?",
            (user_id, ORDERS_PAGE_SIZE, (page - 1) * ORDERS_PAGE_SIZE),
        ).fetchall()
    finally:
        conn.close()
    text = f"{vip_em('diamond')} ═══════ {vip_em('crown')} <b>{t(lang, 'orders_title')}</b> {vip_em('crown')} ═══════ {vip_em('diamond')}\n\n"
    keyboard = []
    if not rows:
        text += f"{vip_em('box')} {t(lang, 'orders_empty')}"
    seen_guides: list[str] = []
    # Telegram: tối đa 4096 ký tự sau khi bỏ thẻ HTML. Chia đều ngân sách cho phần nội dung của các đơn trên trang.
    def _display_name(row) -> str:
        return sku_title(str(row["product_id"]), lang)
    fixed_chars = sum(len(_display_name(row)) + len(str(row["order_id"])) + 200 for row in rows)
    preview_limit = max(60, min(ORDER_CONTENT_PREVIEW, (3600 - fixed_chars) // max(1, len(rows))))
    for row in rows:
        sku = str(row["product_id"])
        name = _display_name(row)
        label = t(lang, "orders_label_link" if sku == DEFAULT_SKU else "orders_label_info")
        content_lines = str(row["item_delivered"] or "").splitlines()
        content = "\n".join(content_lines)
        if len(content) > preview_limit:
            content = content[:preview_limit].rsplit("\n", 1)[0]
            content += "\n" + t(lang, "orders_truncated", n=len(content_lines))
        text += (
            f"{vip_em('tag')} <b>{t(lang, 'orders_order')}:</b> <code>{html.escape(str(row['order_id']))}</code>\n"
            f"{vip_em('box')} <b>{t(lang, 'orders_product')}:</b> {html.escape(name)}\n"
            f"{vip_em('calendar')} <b>{t(lang, 'orders_time')}:</b> <code>{html.escape(str(row['completed_at'] or row['created_at'] or ''))}</code>\n"
            f"{vip_em('check')} <b>{t(lang, 'orders_status')}:</b> {t(lang, 'orders_status_delivered')}\n"
            f"{vip_em('key')} <b>{label}:</b>\n<code>{html.escape(content)}</code>\n"
            "──────────────────────────\n"
        )
        if has_own_guide(sku) and sku not in seen_guides:
            seen_guides.append(sku)
            keyboard.append([vip_btn(t(lang, "btn_guide_for", name=name)[:48], callback_data=f"gd:{sku}", emoji_name="book")])
    if pages > 1:
        text += "\n" + t(lang, "orders_page", page=page, pages=pages, total=total)
        nav = []
        if page > 1:
            nav.append(vip_btn("◀ " + t(lang, "btn_prev_page"), callback_data=f"ordp:{page - 1}", emoji_name="back"))
        if page < pages:
            nav.append(vip_btn(t(lang, "btn_next_page") + " ▶", callback_data=f"ordp:{page + 1}", emoji_name="refresh"))
        keyboard.append(nav)
    keyboard.append([vip_btn(t(lang, "btn_back_menu"), callback_data="main_menu", emoji_name="back")])
    return text, InlineKeyboardMarkup(keyboard)


def welcome_message(user, lang: str | None = None) -> str:
    """Màn chính NGẮN: chỉ lời chào thương hiệu. Danh sách sản phẩm nằm trong mục Mua hàng; chi tiết ở màn từng sản phẩm."""
    lang = lang or get_user_lang(getattr(user, "id", None))
    safe_name = html.escape(user.first_name) if user and getattr(user, "first_name", None) else t(lang, "guest_name")
    return (
        f"{vip_em('crown', '👑')} ═══════ {vip_em('diamond', '💎')} <b>WOLFMOD VIP STORE</b> {vip_em('diamond', '💎')} ═══════ {vip_em('crown', '👑')}\n\n"
        f"{vip_em('sparkle', '✨')} {t(lang, 'welcome_greet', name=safe_name)}\n\n"
        f"{vip_em('star', '👇')} {t(lang, 'welcome_hint')}"
    )


def _plain_len(text: str) -> int:
    """Độ dài sau khi bỏ thẻ HTML (cách Telegram đếm giới hạn caption 1024 / tin nhắn 4096)."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", text)))


def render_product_detail(sku: str, lang: str, price: int) -> str:
    """Trang chi tiết đầy đủ của sản phẩm (vi/en) dựng từ product_content.json + giá thật của web."""
    L = lambda key, **kw: t(lang, key, **kw)  # noqa: E731
    lines = [f"{vip_em('crown', '👑')} <b>{html.escape(sku_title(sku, lang))}</b> {vip_em('crown', '👑')}", ""]
    duration = product_content.field(sku, "duration", lang)
    if duration:
        lines.append(f"{vip_em('calendar', '⏳')} <b>{L('lbl_duration')}:</b> {html.escape(str(duration))}")
    if price > 0:
        lines.append(f"{vip_em('money', '💰')} <b>{L('lbl_price')}:</b> "
                     f"<b>{L('price_line', vnd=i18n.fmt_vnd(price), usd=i18n.fmt_usd(price))}</b>")
    feats = product_content.features(sku, lang)
    if feats:
        lines += ["", f"{vip_em('star', '🌟')} <b>{L('lbl_features')}:</b>"] + [f"• {html.escape(item)}" for item in feats]
    delivery = product_content.field(sku, "delivery", lang)
    if delivery:
        lines += ["", f"{vip_em('box', '📦')} <b>{L('lbl_delivery')}:</b> {html.escape(str(delivery))}"]
    warranty = product_content.field(sku, "warranty", lang)
    if warranty:
        lines.append(f"{vip_em('shield', '🛡')} <b>{L('lbl_warranty')}:</b> {html.escape(str(warranty))}")
    notes = product_content.notes(sku, lang)
    if notes:
        lines += ["", f"{vip_em('chat', '💬')} <b>{L('lbl_notes')}:</b>"] + [f"• {html.escape(item)}" for item in notes]
    return "\n".join(lines) + "\n\n"


def render_product_intro(sku: str, stock: int, lang: str | None = None) -> str:
    """Màn giới thiệu RIÊNG của sản phẩm (giá, mô tả, tồn kho, số lượng tối đa); hết hàng thì ghi rõ và không mời mua."""
    lang = i18n.normalize_lang(lang or get_default_lang())
    prod = get_product(sku)
    price = sku_price_vnd(sku)
    usdt_price = get_setting(usdt_setting_key(sku), "1")
    if product_content.get(sku):
        text = render_product_detail(sku, lang, price)
    elif product_intros.has_own_intro(sku):
        text = product_intros.INTROS[sku](vip_em, price, usdt_price, lang)
    else:
        text = product_intros.generic_intro(vip_em, str(prod["name"]) if prod else sku,
                                            str(prod["description"] or "") if prod else "", price, usdt_price, lang)
    if stock <= 0:
        return text + f"{vip_em('cross', '❌')} {t(lang, 'oos_notice')}"
    stock_line = f"{vip_em('box', '📦')} <b>{t(lang, 'lbl_stock')}:</b> <code>{t(lang, 'stock_available_value', stock=stock)}</code>\n"
    if sku == DEFAULT_SKU:
        return text + stock_line + f"\n{vip_em('lightning', '⚡️')} {t(lang, 'pick_qty')}"
    return (text + stock_line +
            f"{vip_em('shield', '🛡')} <b>{t(lang, 'lbl_max_per_order')}:</b> {sku_max_qty(sku)}\n\n"
            f"{vip_em('lightning', '⚡️')} {t(lang, 'pick_continue')}")


async def send_product_screen(query, sku: str, text: str, keyboard, lang: str | None = None) -> None:
    """Hiện màn sản phẩm: ảnh banner nếu có. Caption ảnh tối đa 1024 ký tự: nếu giới thiệu dài hơn thì gửi ảnh với
    caption ngắn (tên sản phẩm) rồi gửi giới thiệu thành tin nhắn riêng kèm nút."""
    markup = InlineKeyboardMarkup(keyboard)
    banner_path = get_product_banner_path(sku, lang)
    if banner_path and os.path.exists(banner_path) and query.message:
        try:
            key = _banner_key(banner_path)

            async def send_banner(**kwargs):
                """Gửi bằng file_id đã cache nếu có (không upload lại); lỗi file_id cũ -> upload lại từ file."""
                cached = _banner_file_ids.get(key)
                if cached:
                    try:
                        return await query.message.reply_photo(photo=cached, **kwargs)
                    except Exception:
                        _banner_file_ids.pop(key, None)
                with open(banner_path, "rb") as f_img:
                    bio = io.BytesIO(f_img.read())
                bio.name = os.path.basename(banner_path)
                sent = await query.message.reply_photo(photo=bio, **kwargs)
                try:
                    _banner_file_ids[key] = sent.photo[-1].file_id
                except Exception:
                    pass
                return sent

            try:
                await query.message.delete()
            except Exception:
                pass
            if _plain_len(text) <= 1000:
                await send_banner(caption=text, reply_markup=markup, parse_mode="HTML")
            else:
                await send_banner(caption=f"<b>{html.escape(sku_display_name(sku))}</b>", parse_mode="HTML")
                await query.message.reply_text(text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)
            return
        except Exception as e_banner:
            logger.warning(f"Lỗi gửi banner {sku}: {e_banner}")
    await safe_edit_or_reply(query, text, reply_markup=markup, parse_mode="HTML")


_wallet_storage_warned: set = set()


def wallet_enabled() -> bool:
    """Ví riêng của bot: chỉ bật khi WALLET_ENABLED=1 (mặc định TẮT) VÀ dữ liệu nằm trên Railway Volume.

    Trên Railway, nếu DATA_DIR không nằm dưới RAILWAY_VOLUME_MOUNT_PATH thì số dư sẽ mất mỗi lần deploy: từ chối bật ví."""
    if os.getenv("WALLET_ENABLED", "0").strip() != "1":
        return False
    ok, reason = wallet_store.storage_is_persistent(DATA_DIR)
    if not ok:
        if reason not in _wallet_storage_warned:
            _wallet_storage_warned.add(reason)
            logger.error("WALLET_ENABLED=1 bị từ chối: dữ liệu không nằm trên Railway Volume (%s). Gắn Volume và đặt SHOP_DATA_DIR "
                         "trong Volume trước khi bật ví.", reason)
        return False
    return True


def community_url() -> str | None:
    """Link cộng đồng từ COMMUNITY_URL; chỉ nhận https://t.me/... (không khoảng trắng), ngược lại ẩn nút."""
    url = os.getenv("COMMUNITY_URL", "").strip()
    prefix = "https://t.me/"
    if url.startswith(prefix) and len(url) > len(prefix) and not any(ch.isspace() for ch in url):
        return url
    return None


def main_menu_keyboard(user_id=None, lang: str | None = None):
    """Menu chính: [Mua hàng | Ví của tôi*] / [Đơn đã mua | Hỗ trợ] / [Cộng đồng*] / [Ngôn ngữ] (* chỉ hiện khi bật)."""
    lang = lang or get_user_lang(user_id)
    row1 = [vip_btn(t(lang, "btn_shop"), callback_data="shop", emoji_name="crown", style="primary")]
    if wallet_enabled():
        row1.append(vip_btn(t(lang, "btn_wallet"), callback_data="wallet", emoji_name="wallet"))
    rows = [
        row1,
        [vip_btn(t(lang, "btn_orders"), callback_data="my_orders", emoji_name="diamond"),
         vip_btn(t(lang, "btn_support"), callback_data="support", emoji_name="chat")],
    ]
    community = community_url()
    if community:
        rows.append([vip_btn(t(lang, "btn_community"), url=community, emoji_name="user")])
    rows.append([vip_btn(t(lang, "btn_language"), callback_data="lang_menu", emoji_name="globe")])
    return InlineKeyboardMarkup(rows)


def shop_message(lang: str) -> str:
    return (f"{vip_em('crown', '👑')} ═══════ {vip_em('diamond', '💎')} {t(lang, 'shop_title')} {vip_em('diamond', '💎')} ═══════ {vip_em('crown', '👑')}\n\n"
            f"{vip_em('star', '👇')} {t(lang, 'shop_hint')}")


def shop_keyboard(user_id=None, lang: str | None = None):
    """Danh sách sản phẩm (callback_data cũ giữ nguyên: buy_<sku>, soldout_<sku>)."""
    lang = lang or get_user_lang(user_id)
    keyboard = []
    for sku in listed_skus():
        price = sku_price_vnd(sku)
        if price <= 0:
            continue  # chưa đồng bộ được giá từ web: ẩn như trước
        row = get_product(sku)
        label = (product_content.short_name(sku, lang) or (str(row["name"]) if row else sku))[:40]
        if sku_is_in_stock(sku):
            if sku == DEFAULT_SKU:
                text = t(lang, "btn_product_buy", name=label, price=i18n.fmt_vnd(price), stock=get_stock_count(sku))
            else:
                text = t(lang, "btn_product_buy_nostock", name=label, price=i18n.fmt_vnd(price))
            keyboard.append([vip_btn(text, callback_data=cb_buy(sku), emoji_name="crown", style="primary")])
        else:
            # gemini_plus keeps opening its own page (which says sold out), as before; others show a sold-out alert.
            cb = cb_buy(sku) if sku == DEFAULT_SKU else f"soldout_{sku}"
            keyboard.append([vip_btn(t(lang, "btn_product_oos", name=label, price=i18n.fmt_vnd(price)), callback_data=cb, emoji_name="cross")])
    keyboard.append([vip_btn(t(lang, "btn_back_menu"), callback_data="main_menu", emoji_name="back")])
    return InlineKeyboardMarkup(keyboard)


def language_keyboard(lang: str):
    return InlineKeyboardMarkup([
        [vip_btn(("✓ " if lang == "vi" else "") + i18n.LANG_NAMES["vi"], callback_data="setlang:vi", emoji_name="globe")],
        [vip_btn(("✓ " if lang == "en" else "") + i18n.LANG_NAMES["en"], callback_data="setlang:en", emoji_name="globe")],
        [vip_btn(t(lang, "btn_back_menu"), callback_data="main_menu", emoji_name="back")],
    ])

# ==================== HANDLERS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.to_thread(refresh_catalogue)
    await asyncio.to_thread(sync_web_product_price)
    user = update.effective_user
    if user:
        AWAITING_QTY_USERS.pop(user.id, None)
    lang = get_user_lang(user.id) if user else get_default_lang()
    welcome_text = welcome_message(user, lang)
    markup = main_menu_keyboard(user.id if user else 0, lang)
    if update.message:
        await update.message.reply_text(welcome_text, reply_markup=markup, parse_mode="HTML")
    elif update.callback_query:
        await safe_edit_or_reply(update.callback_query, welcome_text, reply_markup=markup, parse_mode="HTML")


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/language: chọn Tiếng Việt / English (lưu theo user_id)."""
    user = update.effective_user
    lang = get_user_lang(user.id) if user else get_default_lang()
    if update.message:
        await update.message.reply_text(t(lang, "lang_title"), reply_markup=language_keyboard(lang), parse_mode="HTML")

class PlisioMinimumAmountError(Exception):
    def __init__(self, minimum, currency):
        self.minimum = minimum
        self.currency = currency
        super().__init__("Plisio minimum amount")


def create_web_plisio_invoice(order_id, user, qty, sku=DEFAULT_SKU):
    import json
    import re
    import urllib.error
    import urllib.request
    from urllib.parse import urlsplit
    payload = json.dumps({"order_code": order_id, "tg_user_id": user.id,
                          "tg_username": user.username or "", "sku": sku, "qty": qty}).encode("utf-8")
    req = urllib.request.Request("https://www.wolfmod.xyz/api/tg/orders/plisio", data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {BOT_TOKEN}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=25) as response:
            invoice = json.loads(response.read())
    except urllib.error.HTTPError as error:
        minimum = None
        minimum_currency = None
        try:
            body = json.loads(error.read(2048).decode("utf-8", errors="replace"))
            error_code = body.get("error", "request_rejected")
            if not isinstance(error_code, str) or not re.fullmatch(r"[a-z0-9_]{1,64}", error_code):
                error_code = "request_rejected"
            provider_code = body.get("provider_code")
            value, currency = body.get("minimum_exclusive"), body.get("minimum_currency")
            if (str(provider_code) == "106" and not isinstance(value, bool) and
                isinstance(value, (int, float)) and 0 < value <= 1000000 and
                currency in ("USDT_BSC", "USDT_TRX")):
                minimum, minimum_currency = float(value), currency
            if isinstance(provider_code, (int, str)) and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", str(provider_code)):
                error_code = f"{error_code}_provider_{provider_code}"
            provider_status = body.get("provider_http_status")
            if isinstance(provider_status, int) and 100 <= provider_status <= 599:
                error_code = f"{error_code}_http_{provider_status}"
        except Exception:
            error_code = "request_rejected"
        if minimum is not None:
            raise PlisioMinimumAmountError(minimum, minimum_currency) from None
        raise RuntimeError(f"HTTP {error.code}: {error_code}") from None
    url = urlsplit(invoice.get("invoice_url", ""))
    if not invoice.get("success") or url.scheme != "https" or url.username or url.password or not (
        url.hostname == "plisio.net" or (url.hostname or "").endswith(".plisio.net")
    ) or invoice.get("order_code") != order_id:
        raise ValueError("Invalid Plisio invoice response")
    return invoice


# ==================== USDT BEP20 (QR địa chỉ ví) ====================
# Địa chỉ ví, mạng và số tiền chính xác của từng đơn đều do WEB trả về; bot không lưu địa chỉ ví trong code. Việc xác nhận
# thanh toán (đọc blockchain, chống dùng lại giao dịch) nằm hoàn toàn ở web. Mặc định TẮT: đặt BEP20_ENABLED=1 để bật.
_BEP20_ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
_BEP20_AMOUNT_RE = re.compile(r"\d{1,6}\.\d{2}")


def bep20_enabled() -> bool:
    return os.getenv("BEP20_ENABLED", "0").strip() == "1"


class Bep20Unavailable(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _post_web_json(path: str, payload: dict, timeout: int = 20) -> tuple[int, dict]:
    import json
    import urllib.error
    import urllib.request
    req = urllib.request.Request("https://www.wolfmod.xyz" + path, data=json.dumps(payload).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {BOT_TOKEN}"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            data = json.loads(response.read(65536))
            return response.status, data if isinstance(data, dict) else {}
    except urllib.error.HTTPError as error:
        try:
            body = json.loads(error.read(4096).decode("utf-8", errors="replace"))
        except Exception:
            body = {}
        return error.code, body if isinstance(body, dict) else {}


def create_web_bep20_order(order_id: str, user, qty: int, sku: str) -> dict:
    """Tạo đơn BEP20 trên web và kiểm tra chặt phản hồi; lỗi -> Bep20Unavailable(mã an toàn)."""
    try:
        status, body = _post_web_json("/api/tg/orders/bep20", {
            "order_code": order_id, "tg_user_id": user.id, "tg_username": user.username or "", "sku": sku, "qty": qty})
    except Exception as error:
        logger.warning("BEP20 tạo đơn lỗi mạng: %s", type(error).__name__)
        raise Bep20Unavailable("network") from None
    if status != 200:
        code = body.get("error") if isinstance(body.get("error"), str) else "rejected"
        raise Bep20Unavailable(code if re.fullmatch(r"[a-z0-9_]{1,40}", code) else "rejected")
    amount = body.get("amount_usdt")
    address = body.get("address")
    try:
        if (body.get("success") is not True or body.get("order_code") != order_id or body.get("network") != "BSC (BEP20)"
                or body.get("token") != "USDT" or not isinstance(address, str) or not _BEP20_ADDRESS_RE.fullmatch(address)
                or not isinstance(amount, str) or not _BEP20_AMOUNT_RE.fullmatch(amount) or float(amount) <= 0
                or body.get("qty") != qty or not isinstance(body.get("expires_at"), str)):
            raise ValueError("invalid bep20 response")
        confirmations = body.get("min_confirmations")
        if isinstance(confirmations, bool) or not isinstance(confirmations, int) or not 1 <= confirmations <= 1000:
            raise ValueError("invalid confirmations")
        expiry = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except Exception:
        raise Bep20Unavailable("invalid_response") from None
    return {"address": address, "amount_usdt": amount, "expires_at": expiry, "min_confirmations": confirmations}


def check_web_bep20(order_id: str, user_id: int) -> dict:
    """Nhờ web đối chiếu blockchain cho đơn này. Trả {} khi lỗi (không bao giờ tự coi là đã thanh toán)."""
    try:
        status, body = _post_web_json("/api/tg/orders/bep20/check", {"order_code": order_id, "tg_user_id": int(user_id)}, timeout=25)
    except Exception as error:
        logger.warning("BEP20 kiểm tra lỗi mạng: %s", type(error).__name__)
        return {}
    if status != 200 or body.get("success") is not True or not isinstance(body.get("status"), str):
        return {}
    result = {"status": body["status"]}
    for key in ("confirmations", "min_confirmations"):
        if isinstance(body.get(key), int) and not isinstance(body.get(key), bool):
            result[key] = body[key]
    return result


def render_bep20_invoice(order_id: str, info: dict, qty: int, sku: str, lang: str | None = None) -> str:
    lang = i18n.normalize_lang(lang or get_default_lang())
    return t(lang, "bep20_invoice", crown=vip_em('crown'), diamond=vip_em('diamond'), tag=vip_em('tag'), box=vip_em('box'),
             globe=vip_em('globe'), money=vip_em('money'), card=vip_em('card'), calendar=vip_em('calendar'),
             shield=vip_em('shield', '⚠️'), lightning=vip_em('lightning', '⚡️'),
             order=html.escape(order_id), product=html.escape(sku_display_name(sku)), qty=qty, amount=info['amount_usdt'],
             address=info['address'], deadline=f"{info['expires_at']:%H:%M:%S}", confirmations=info['min_confirmations'])


async def show_bep20_checkout(query, user, qty, sku=DEFAULT_SKU):
    import hashlib
    lang = get_user_lang(user.id)
    if not bep20_enabled():
        await query.answer(t(lang, "pay_method_disabled"), show_alert=True)
        return
    if not 1 <= qty <= 50 or sku not in known_skus() or (sku != DEFAULT_SKU and qty > sku_max_qty(sku)):
        await query.answer(t(lang, "pay_invalid_qty"), show_alert=True)
        return
    # Cùng một tin nhắn + số lượng + sản phẩm -> cùng mã đơn (bấm lặp không tạo hóa đơn mới). Có hậu tố riêng cho BEP20.
    reference = f"{user.id}:{query.message.chat_id}:{query.message.message_id}:{qty}:bep20"
    if sku != DEFAULT_SKU:
        reference += f":{sku}"
    order_id = "TGS" + hashlib.sha256(reference.encode()).hexdigest()[:12].upper()
    back = InlineKeyboardMarkup([
        [vip_btn(t(lang, "btn_reselect_method"), callback_data=cb_qty("select", sku, qty), emoji_name="refresh")],
        [vip_btn(t(lang, "btn_back_menu"), callback_data="main_menu", emoji_name="back")],
    ])
    try:
        info = await asyncio.to_thread(create_web_bep20_order, order_id, user, qty, sku)
    except Bep20Unavailable as error:
        messages = {
            "out_of_stock": "pay_err_out_of_stock",
            "product_unavailable": "bep20_err_product_unavailable",
            "busy_try_again": "pay_err_busy",
            "too_many_pending_orders": "pay_err_too_many_pending",
        }
        await safe_edit_or_reply(query, f"{vip_em('shield')} <b>{t(lang, messages.get(error.code, 'bep20_err_generic'))}</b>",
                                 reply_markup=back, parse_mode="HTML")
        return
    conn = get_db_connection()
    try:
        conn.execute("""INSERT INTO orders (order_id,user_id,user_name,product_id,amount,status,
            payment_method,currency,quantity,created_at,expires_at)
            VALUES (?,?,?,?,?,'pending','bep20','USDT',?,?,?)
            ON CONFLICT(order_id) DO UPDATE SET amount=excluded.amount,expires_at=excluded.expires_at WHERE orders.status='pending'""",
            (order_id, user.id, user.username or user.first_name, sku, float(info["amount_usdt"]), qty,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), info["expires_at"].strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    finally:
        conn.close()
    text = render_bep20_invoice(order_id, info, qty, sku, lang)
    markup = InlineKeyboardMarkup([
        [vip_btn(t(lang, "btn_bep20_check"), callback_data=f"confirm_pay_{order_id}", emoji_name="check", style="primary")],
        [vip_btn(t(lang, "btn_back_menu"), callback_data="main_menu", emoji_name="back")],
    ])
    qr_bytes = generate_crypto_qr_png_bytes(info["address"])  # QR của địa chỉ ví thuần (0x...), sinh cục bộ, không gọi dịch vụ ngoài
    if qr_bytes and query.message:
        try:
            bio = io.BytesIO(qr_bytes)
            bio.name = f"{order_id}.png"
            if _plain_len(text) <= 1000:
                await query.message.reply_photo(photo=bio, caption=text, reply_markup=markup, parse_mode="HTML")
            else:
                await query.message.reply_photo(photo=bio)
                await query.message.reply_text(text, reply_markup=markup, parse_mode="HTML")
            try:
                await query.message.delete()
            except Exception:
                pass
            return
        except Exception as error:
            logger.warning("Lỗi gửi QR BEP20: %s", type(error).__name__)
    await safe_edit_or_reply(query, text, reply_markup=markup, parse_mode="HTML")


# ==================== USDT BINANCE PAY (khách dán ID lệnh) ====================
# Binance ID nhận tiền, số tiền (= GIÁ ĐÚNG, không số lẻ) và việc xác minh giao dịch đều do WEB quyết định; bot chỉ hiển thị phần
# web trả về (đã kiểm tra chặt) và chuyển ID lệnh khách dán lên web. Không có khóa/Binance ID nào trong code bot.
# Mặc định TẮT: đặt BINANCE_PAY_ENABLED=1 (sau khi web đã bật).
_BINANCE_ID_RE = re.compile(r"\d{6,20}")
_BINANCE_AMOUNT_RE = re.compile(r"\d{1,6}\.\d{2}")
_BINANCE_ORDER_ID_RE = re.compile(r"\d{8,32}")
_ORDER_CODE_RE = re.compile(r"TGS[A-Z0-9]{4,17}")
BINANCE_ID_WAIT_SECONDS = 20 * 60
BINANCE_MAX_BAD_INPUTS = 3
AWAITING_BINANCE_ID: dict = {}   # user_id -> {"order_id": str, "until": float, "bad": int}


def binance_pay_enabled() -> bool:
    return os.getenv("BINANCE_PAY_ENABLED", "0").strip() == "1"


class BinancePayUnavailable(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def create_web_binance_order(order_id: str, user, qty: int, sku: str) -> dict:
    """Tạo đơn Binance Pay trên web, kiểm tra chặt phản hồi (kể cả số tiền = giá đúng của bot); lỗi -> BinancePayUnavailable."""
    try:
        status, body = _post_web_json("/api/tg/orders/binance-pay", {
            "order_code": order_id, "tg_user_id": user.id, "tg_username": user.username or "", "sku": sku, "qty": qty})
    except Exception as error:
        logger.warning("Binance Pay tạo đơn lỗi mạng: %s", type(error).__name__)
        raise BinancePayUnavailable("network") from None
    if status != 200:
        code = body.get("error") if isinstance(body.get("error"), str) else "rejected"
        raise BinancePayUnavailable(code if re.fullmatch(r"[a-z0-9_]{1,40}", code) else "rejected")
    amount = body.get("amount_usdt")
    receiver = body.get("receiver_id")
    try:
        expected = f"{round(get_usdt_price(sku) * qty, 2):.2f}"
        if (body.get("success") is not True or body.get("order_code") != order_id or body.get("network") != "Binance Pay"
                or body.get("token") != "USDT" or not isinstance(receiver, str) or not _BINANCE_ID_RE.fullmatch(receiver)
                or not isinstance(amount, str) or not _BINANCE_AMOUNT_RE.fullmatch(amount) or float(amount) <= 0
                or amount != expected or body.get("qty") != qty or not isinstance(body.get("expires_at"), str)):
            raise ValueError("invalid binance pay response")
        expiry = datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    except Exception:
        raise BinancePayUnavailable("invalid_response") from None
    return {"receiver_id": receiver, "amount_usdt": amount, "expires_at": expiry}


def confirm_web_binance(order_id: str, user_id: int, pasted_id: str) -> dict:
    """Gửi ID lệnh khách dán lên web. Trả {"result": ...}; lỗi/không rõ KHÔNG BAO GIỜ là 'paid'."""
    try:
        status, body = _post_web_json("/api/tg/orders/binance-pay/confirm",
                                      {"order_code": order_id, "tg_user_id": int(user_id), "pasted_id": pasted_id}, timeout=25)
    except Exception as error:
        logger.warning("Binance Pay xác nhận lỗi mạng: %s", type(error).__name__)
        return {"result": "unavailable"}
    error = body.get("error") if isinstance(body.get("error"), str) else ""
    if status == 200 and body.get("success") is True and body.get("status") in ("paid", "needs_review", "expired", "cancelled"):
        return {"result": body["status"]}
    if status == 200 and body.get("success") is False and body.get("status") == "pending":
        reason = body.get("reason")
        known = ("not_found", "amount_mismatch", "invalid_transaction", "before_order", "already_used")
        return {"result": reason if reason in known else "rejected"}
    if status == 200 and body.get("success") is True and body.get("status") == "pending":
        return {"result": "rejected"}
    if status == 429 and error in ("too_fast", "too_many_attempts"):
        return {"result": error}
    if status == 400 and error == "invalid_order_id":
        return {"result": "invalid_order_id"}
    if status == 503 and error == "can_cau_hinh":
        return {"result": "config"}
    return {"result": "unavailable"}


def render_binance_invoice(order_id: str, info: dict, qty: int, sku: str, lang: str | None = None) -> str:
    lang = i18n.normalize_lang(lang or get_default_lang())
    return t(lang, "binance_invoice", crown=vip_em('crown'), diamond=vip_em('diamond'), tag=vip_em('tag'), box=vip_em('box'),
             money=vip_em('money'), card=vip_em('card'), calendar=vip_em('calendar'),
             shield=vip_em('shield', '⚠️'), lightning=vip_em('lightning', '⚡️'),
             order=html.escape(order_id), product=html.escape(sku_display_name(sku)), qty=qty, amount=info['amount_usdt'],
             receiver=info['receiver_id'], deadline=f"{info['expires_at']:%H:%M:%S}")


async def show_binance_checkout(query, user, qty, sku=DEFAULT_SKU):
    import hashlib
    lang = get_user_lang(user.id)
    if not binance_pay_enabled():
        await query.answer(t(lang, "pay_method_disabled"), show_alert=True)
        return
    if not 1 <= qty <= 50 or sku not in known_skus() or (sku != DEFAULT_SKU and qty > sku_max_qty(sku)):
        await query.answer(t(lang, "pay_invalid_qty"), show_alert=True)
        return
    reference = f"{user.id}:{query.message.chat_id}:{query.message.message_id}:{qty}:binance"
    if sku != DEFAULT_SKU:
        reference += f":{sku}"
    order_id = "TGS" + hashlib.sha256(reference.encode()).hexdigest()[:12].upper()
    back = InlineKeyboardMarkup([
        [vip_btn(t(lang, "btn_reselect_method"), callback_data=cb_qty("select", sku, qty), emoji_name="refresh")],
        [vip_btn(t(lang, "btn_back_menu"), callback_data="main_menu", emoji_name="back")],
    ])
    await asyncio.to_thread(sync_web_product_price, sku)
    try:
        info = await asyncio.to_thread(create_web_binance_order, order_id, user, qty, sku)
    except BinancePayUnavailable as error:
        messages = {
            "out_of_stock": "pay_err_out_of_stock",
            "product_unavailable": "binance_err_product_unavailable",
            "too_many_pending_orders": "pay_err_too_many_pending",
        }
        await safe_edit_or_reply(query, f"{vip_em('shield')} <b>{t(lang, messages.get(error.code, 'binance_err_generic'))}</b>",
                                 reply_markup=back, parse_mode="HTML")
        return
    conn = get_db_connection()
    try:
        conn.execute("""INSERT INTO orders (order_id,user_id,user_name,product_id,amount,status,
            payment_method,currency,quantity,created_at,expires_at)
            VALUES (?,?,?,?,?,'pending','binance_pay','USDT',?,?,?)
            ON CONFLICT(order_id) DO UPDATE SET amount=excluded.amount,expires_at=excluded.expires_at WHERE orders.status='pending'""",
            (order_id, user.id, user.username or user.first_name, sku, float(info["amount_usdt"]), qty,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"), info["expires_at"].strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    finally:
        conn.close()
    markup = InlineKeyboardMarkup([
        [vip_btn(t(lang, "btn_binance_paste"), callback_data=f"bpid_{order_id}", emoji_name="check", style="primary")],
        [vip_btn(t(lang, "btn_back_menu"), callback_data="main_menu", emoji_name="back")],
    ])
    await safe_edit_or_reply(query, render_binance_invoice(order_id, info, qty, sku, lang), reply_markup=markup, parse_mode="HTML")


async def prompt_binance_id(query, user, order_id: str):
    """Khách bấm 'Tôi đã chuyển': bot chờ họ gửi ID lệnh trong tin nhắn kế tiếp."""
    import time
    lang = get_user_lang(user.id)
    if not binance_pay_enabled() or not _ORDER_CODE_RE.fullmatch(order_id or ""):
        await query.answer(t(lang, "pay_method_disabled"), show_alert=True)
        return
    conn = get_db_connection()
    try:
        order = conn.execute("SELECT * FROM orders WHERE order_id = ? AND user_id = ?", (order_id, user.id)).fetchone()
    finally:
        conn.close()
    if not order or order["payment_method"] != "binance_pay":
        await query.answer(t(lang, "pay_order_not_found"), show_alert=True)
        return
    if order["status"] != "pending":
        await query.answer(t(lang, "pay_order_not_pending"), show_alert=True)
        return
    AWAITING_BINANCE_ID[user.id] = {"order_id": order_id, "until": time.monotonic() + BINANCE_ID_WAIT_SECONDS, "bad": 0}
    await query.answer()
    await query.message.reply_text(
        t(lang, "binance_prompt", tag=vip_em('tag'), shield=vip_em('shield', '⚠️'), order=html.escape(order_id)),
        parse_mode="HTML")


_BINANCE_REPLY_KEYS = ("not_found", "amount_mismatch", "invalid_transaction", "before_order", "already_used",
                       "too_fast", "too_many_attempts", "unavailable", "config", "rejected")


def binance_reply(lang, result) -> str:
    """Thông báo kết quả xác nhận ID lệnh theo ngôn ngữ; kết quả lạ -> 'rejected'."""
    return t(lang, f"binance_reply_{result if result in _BINANCE_REPLY_KEYS else 'rejected'}")


async def handle_binance_id_message(update, context) -> bool:
    """Trả True nếu tin nhắn đã được xử lý như một ID lệnh Binance Pay."""
    import time
    user = update.effective_user
    waiting = AWAITING_BINANCE_ID.get(user.id)
    if not waiting:
        return False
    if time.monotonic() > waiting["until"]:
        AWAITING_BINANCE_ID.pop(user.id, None)
        return False
    text = (update.message.text or "").strip()
    order_id = waiting["order_id"]
    lang = get_user_lang(user.id)
    if not _BINANCE_ORDER_ID_RE.fullmatch(text):
        waiting["bad"] += 1
        if waiting["bad"] >= BINANCE_MAX_BAD_INPUTS:
            AWAITING_BINANCE_ID.pop(user.id, None)
            await update.message.reply_text(t(lang, "binance_id_stopped"))
        else:
            await update.message.reply_text(t(lang, "binance_id_bad_format"))
        return True
    outcome = await asyncio.to_thread(confirm_web_binance, order_id, user.id, text)
    result = outcome["result"]
    if result == "paid":
        AWAITING_BINANCE_ID.pop(user.id, None)
        if await send_paid_order_delivery(context.bot, order_id, user.id):
            return True
        await update.message.reply_text(t(lang, "binance_paid_not_delivered", check=vip_em('check', '✅'), order=order_id))
        return True
    if result == "needs_review":
        AWAITING_BINANCE_ID.pop(user.id, None)
        await update.message.reply_text(t(lang, "binance_needs_review", shield=vip_em('shield'), order=order_id))
        return True
    if result in ("expired", "cancelled"):
        AWAITING_BINANCE_ID.pop(user.id, None)
        await update.message.reply_text(t(lang, "binance_expired"))
        return True
    if result in ("too_many_attempts", "config"):
        AWAITING_BINANCE_ID.pop(user.id, None)
    await update.message.reply_text(binance_reply(lang, result))
    return True


# ── "Ví của tôi": ví RIÊNG của bot (không liên kết web). PR1: chỉ xem số dư + lịch sử; nạp/trả bằng ví là các PR sau ──────
# Dữ liệu: wallet_store.py (SQLite của bot, sổ cái chỉ thêm dòng). Không log số dư hay mã giao dịch.
WALLET_HISTORY_LINES = 10


def _is_private_chat(message) -> bool:
    chat = getattr(message, "chat", None)
    return getattr(chat, "type", None) == "private"


def build_wallet_screen(user_id: int, lang: str):
    """(text, markup) của màn Ví: số dư (VNĐ, kèm USD tham khảo) + lịch sử gần đây. Chỉ đọc, không tạo dữ liệu."""
    conn = get_db_connection()
    try:
        balance = wallet_store.get_balance(conn, user_id)
        history = wallet_store.get_history(conn, user_id, WALLET_HISTORY_LINES)
    finally:
        conn.close()
    lines = []
    for row in history:
        sign = "+" if row["amount_vnd"] > 0 else "-"
        label = t(lang, f"wallet_kind_{row['kind']}")
        lines.append(f"{sign}{abs(row['amount_vnd']):,} · {label} · {str(row['created_at'])[:16]}")
    topup_on = topup_enabled()
    text = t(lang, "wallet_screen_t" if topup_on else "wallet_screen", wallet=vip_em('wallet', '👛'), balance=i18n.fmt_vnd(balance),
             usd=i18n.fmt_usd(balance), history=html.escape("\n".join(lines)) if lines else t(lang, "wallet_history_empty"))
    rows = []
    if topup_on:
        rows.append([vip_btn(t(lang, "btn_topup_open"), callback_data="wtop", emoji_name="money", style="primary")])
    rows.append([vip_btn(t(lang, "btn_back_main"), callback_data="main_menu", emoji_name="back")])
    return text, InlineKeyboardMarkup(rows)


AWAITING_TOPUP_AMOUNT: set[int] = set()
_TOPUP_AMOUNT_CB = re.compile(r"wtop_a:(\d{1,9})")


def topup_enabled() -> bool:
    """Nạp ví: cần WALLET_ENABLED hợp lệ (có Volume) VÀ WALLET_TOPUP_ENABLED=1 (mặc định TẮT)."""
    return wallet_topup.topup_flag_on() and wallet_enabled()


def _wallet_back_markup(lang: str, callback: str = "wallet") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[vip_btn(t(lang, "btn_back_main"), callback_data=callback, emoji_name="back")]])


def build_topup_menu(lang: str):
    amounts = [vip_btn(i18n.fmt_vnd(a), callback_data=f"wtop_a:{a}", emoji_name="money") for a in wallet_topup.QUICK_AMOUNTS]
    rows = [amounts[i:i + 2] for i in range(0, len(amounts), 2)]
    rows.append([vip_btn(t(lang, "btn_topup_other"), callback_data="wtop_c", emoji_name="memo")])
    rows.append([vip_btn(t(lang, "btn_back_main"), callback_data="wallet", emoji_name="back")])
    text = t(lang, "topup_menu", min=i18n.fmt_vnd(wallet_store.MIN_TOPUP_VND), max=i18n.fmt_vnd(wallet_store.MAX_TOPUP_VND),
             cap=i18n.fmt_vnd(wallet_store.MAX_BALANCE_VND))
    return text, InlineKeyboardMarkup(rows)


def settle_topup_sync(order_id: str, user_id: int, web_status: dict):
    conn = get_db_connection()
    try:
        return wallet_topup.settle(conn, order_id, int(user_id), web_status)
    finally:
        conn.close()


async def settle_and_notify_topup(bot, order_id, user_id, web_status: dict):
    """Điểm xác nhận nền cho đơn nạp: cộng ví (một lần) rồi báo khách. Lỗi gửi tin KHÔNG làm mất tiền đã cộng."""
    try:
        result = await asyncio.to_thread(settle_topup_sync, str(order_id), int(user_id), web_status)
    except Exception as error:
        logger.error("Nạp ví: không cộng được đơn %s: %s", order_id, type(error).__name__)
        return None
    if result.state == "credited":
        lang = get_user_lang(user_id)
        try:
            await bot.send_message(
                chat_id=int(user_id), parse_mode="HTML", reply_markup=_wallet_back_markup(lang),
                text=f"{vip_em('check', '✅')} " + t(lang, "topup_credited_msg", amount=i18n.fmt_vnd(result.amount_vnd),
                                                  balance=i18n.fmt_vnd(result.balance_vnd), order=html.escape(str(order_id))))
        except Exception as error:
            logger.warning("Nạp ví: không gửi được tin báo đơn %s: %s", order_id, type(error).__name__)
    return result


def build_topup_bill(lang: str, order) -> str:
    try:
        expires_display = datetime.strptime(order.expires_at, "%Y-%m-%d %H:%M:%S").strftime("%H:%M:%S")
    except ValueError:
        expires_display = t(lang, "bill_ten_minutes")
    return build_vietqr_bill(lang, order.order_id, t(lang, "topup_bill_service"), 1, order.amount_vnd, order.bank_name,
                             order.bank_account, order.bank_owner, expires_display)


async def send_topup_invoice(message, user, amount: int, lang: str) -> None:
    """Tạo đơn nạp (đã đồng bộ lên web) rồi mới gửi QR. Lỗi/giới hạn -> báo thân thiện, KHÔNG hiện QR."""
    def create():
        conn = get_db_connection()
        try:
            return wallet_topup.create_order(
                conn, user.id, user.username or user.first_name, amount,
                bank=(get_setting("bank_name", "ACB"), get_setting("bank_account", "68986683"), get_setting("bank_owner", "LE DONG HA")),
                post_web=_post_web_json)
        finally:
            conn.close()

    back = _wallet_back_markup(lang, "wtop")
    try:
        order = await asyncio.to_thread(create)
    except wallet_topup.TopupError as error:
        key = {"invalid_amount": "topup_err_amount", "balance_cap": "topup_err_cap",
               "too_many_pending": "topup_err_pending"}.get(error.code, "topup_err_unavailable")
        await message.reply_text(
            f"{vip_em('cross', '❌')} " + t(lang, key, min=i18n.fmt_vnd(wallet_store.MIN_TOPUP_VND), max=i18n.fmt_vnd(wallet_store.MAX_TOPUP_VND),
                                          cap=i18n.fmt_vnd(wallet_store.MAX_BALANCE_VND), limit=wallet_topup.MAX_PENDING_TOPUPS),
            reply_markup=back, parse_mode="HTML")
        return
    except Exception as error:
        logger.error("Nạp ví: lỗi tạo đơn: %s", type(error).__name__)
        await message.reply_text(f"{vip_em('cross', '❌')} " + t(lang, "topup_err_unavailable"), reply_markup=back, parse_mode="HTML")
        return
    bill = build_topup_bill(lang, order)
    buttons = [
        [vip_btn(t(lang, "btn_paid_done"), callback_data=f"confirm_pay_{order.order_id}", emoji_name="check", style="primary")],
        [vip_btn(t(lang, "btn_cancel_tx"), callback_data=f"cancel_order_{order.order_id}", emoji_name="cross", style="danger")],
        [vip_btn(t(lang, "btn_back_main"), callback_data="wallet", emoji_name="back")],
    ]
    qr_bytes = generate_vietqr_png_bytes(order.bank_name, order.bank_account, order.bank_owner, order.amount_vnd, order.order_id)
    try:
        if qr_bytes:
            bio = io.BytesIO(qr_bytes)
            bio.name = f"{order.order_id}.png"
            await message.reply_photo(photo=bio, caption=bill, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
            return
    except Exception as error:
        logger.warning("Nạp ví: lỗi gửi ảnh QR: %s", type(error).__name__)
    await message.reply_text(bill, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")


async def handle_topup_check(query, context, order, lang: str) -> None:
    """Nút "Tôi đã thanh toán" của đơn nạp: hỏi web rồi cộng ví qua settle (idempotent). Không bao giờ giao hàng/kho."""
    order_id = order["order_id"]
    web_stat = await asyncio.to_thread(check_web_order_status, order_id)
    try:
        result = await asyncio.to_thread(settle_topup_sync, order_id, order["user_id"], web_stat)
    except Exception as error:
        logger.error("Nạp ví: không cộng được đơn %s: %s", order_id, type(error).__name__)
        await query.answer(t(lang, "topup_err_unavailable_short"), show_alert=True)
        return
    if result.state == "credited":
        await query.answer(t(lang, "topup_credited_alert"), show_alert=True)
        await query.message.reply_text(
            f"{vip_em('check', '✅')} " + t(lang, "topup_credited_msg", amount=i18n.fmt_vnd(result.amount_vnd),
                                         balance=i18n.fmt_vnd(result.balance_vnd), order=html.escape(order_id)),
            reply_markup=_wallet_back_markup(lang), parse_mode="HTML")
    elif result.state == "already_credited":
        await query.answer(t(lang, "topup_already_alert"), show_alert=True)
    elif result.state == "needs_review":
        await query.answer(t(lang, "bep20_review_alert"), show_alert=True)
        await query.message.reply_text(
            t(lang, "bep20_review_message", shield=vip_em('shield'), chat=vip_em('chat', '💬'), order=html.escape(order_id)),
            reply_markup=InlineKeyboardMarkup([[vip_btn(t(lang, "btn_contact_admin"), url="https://t.me/wolfmodyt", emoji_name="chat")]]),
            parse_mode="HTML")
    elif order["status"] in ("cancelled", "expired"):
        await query.answer(t(lang, "order_expired_alert"), show_alert=True)
        await query.message.reply_text(f"{vip_em('cross', '❌')} " + t(lang, "topup_expired", order_id=html.escape(order_id)),
                                       reply_markup=_wallet_back_markup(lang), parse_mode="HTML")
    else:
        await query.answer(t(lang, "check_not_received"), show_alert=True)


async def handle_topup_callback(query, user, lang: str, data: str) -> None:
    """wtop (menu) | wtop_c (nhập số khác) | wtop_a:<số> (số tiền nhanh). Chỉ trong chat riêng và khi bật."""
    if not topup_enabled():
        await safe_edit_or_reply(query, f"{vip_em('shield', '🛡')} {t(lang, 'wallet_err_unavailable')}",
                                 reply_markup=_wallet_back_markup(lang, "main_menu"), parse_mode="HTML")
        return
    if not _is_private_chat(query.message):
        await query.answer(t(lang, "wallet_private_only"), show_alert=True)
        return
    if data == "wtop":
        text, markup = build_topup_menu(lang)
        await safe_edit_or_reply(query, text, reply_markup=markup, parse_mode="HTML")
    elif data == "wtop_c":
        AWAITING_TOPUP_AMOUNT.add(user.id)
        await safe_edit_or_reply(
            query, t(lang, "topup_prompt", min=i18n.fmt_vnd(wallet_store.MIN_TOPUP_VND), max=i18n.fmt_vnd(wallet_store.MAX_TOPUP_VND)),
            reply_markup=_wallet_back_markup(lang, "wtop"), parse_mode="HTML")
    else:
        match = _TOPUP_AMOUNT_CB.fullmatch(data)
        if not match:
            return
        await send_topup_invoice(query.message, user, int(match.group(1)), lang)


# ── Trả bằng ví (PR3, mặc định TẮT: WALLET_PAY_ENABLED=1 + ví hợp lệ). Xem wallet_pay.py ─────────────────────────────────
WALLET_PAY_LOCK = asyncio.Lock()
WALLET_SWEEP_SECONDS = 60


def wallet_pay_enabled() -> bool:
    """Trả bằng ví: cần WALLET_ENABLED hợp lệ (có Volume) VÀ WALLET_PAY_ENABLED=1 (mặc định TẮT)."""
    return wallet_pay.pay_flag_on() and wallet_enabled()


def _wallet_balance(user_id: int) -> int:
    conn = get_db_connection()
    try:
        return wallet_store.get_balance(conn, int(user_id))
    finally:
        conn.close()


def _wallet_processing_ids() -> list[str]:
    conn = get_db_connection()
    try:
        return wallet_pay.processing_orders(conn)
    finally:
        conn.close()


def _wallet_web_mark_paid(order) -> str:
    """Đồng bộ đơn lên web rồi báo web 'đã trả bằng ví' (route bot-only /api/tg/orders/wallet-paid). Idempotent theo order_code."""
    qty = int(order["quantity"] or 1)
    try:
        status, body = _post_web_json("/api/tg/orders/sync", {
            "order_code": order["order_id"], "tg_user_id": int(order["user_id"]), "tg_username": str(order["user_name"] or ""),
            "sku": order["product_id"], "product_name": sku_display_name(str(order["product_id"])), "qty": qty,
            "amount_vnd": int(order["amount"]), "currency": "VND", "provider": "sepay"})
        if status in (0, 408, 429) or status >= 500:
            return wallet_pay.WEB_UNCERTAIN
        if status != 200 or body.get("success") is not True:
            return wallet_pay.WEB_REFUSED
        status, body = _post_web_json("/api/tg/orders/wallet-paid", {
            "order_code": order["order_id"], "tg_user_id": int(order["user_id"]), "sku": order["product_id"], "qty": qty,
            "amount_vnd": int(order["amount"])})
    except Exception:
        return wallet_pay.WEB_UNCERTAIN
    if status == 200 and body.get("success") is True:
        return wallet_pay.WEB_OK
    if status in (0, 408, 429) or status >= 500:
        return wallet_pay.WEB_UNCERTAIN
    return wallet_pay.WEB_REFUSED


def _wallet_finish(order_id: str, user_id: int, bundle: str):
    return deliver_product(order_id, user_id, bundle=bundle)


def wallet_web_ops() -> "wallet_pay.WebOps":
    return wallet_pay.WebOps(mark_paid=_wallet_web_mark_paid, claim=claim_stock_detailed, finish=_wallet_finish)


def _process_wallet_order_sync(order_id: str):
    conn = get_db_connection()
    try:
        return wallet_pay.process_order(conn, order_id, wallet_web_ops())
    finally:
        conn.close()


async def _notify_wallet_result(bot, result) -> None:
    """Báo khách/admin. Mọi lỗi gửi tin KHÔNG ảnh hưởng tiền hay đơn (đơn đã giao có cơ chế gửi lại delivery_notified_at)."""
    lang = get_user_lang(result.user_id)
    try:
        if result.state == "completed":
            await send_paid_order_delivery(bot, result.order_id, result.user_id)
        elif result.state in ("refunded", "refunded_review"):
            await bot.send_message(
                chat_id=int(result.user_id), parse_mode="HTML",
                text=f"{vip_em('shield', '🛡')} " + t(lang, "wpay_refunded_msg" if result.state == "refunded" else "wpay_refunded_review_msg",
                                                    order=html.escape(result.order_id), amount=i18n.fmt_vnd(result.amount_vnd),
                                                    balance=i18n.fmt_vnd(result.balance_vnd or 0)),
                reply_markup=_wallet_back_markup(lang))
            if result.state == "refunded_review":
                logger.warning("Trả bằng ví: đơn %s đã hoàn tiền sau quá hạn, cần admin đối soát với web", result.order_id)
                await bot.send_message(chat_id=int(ADMIN_ID), text=f"⚠️ Wallet order {result.order_id} (user {result.user_id}) was refunded after "
                                       f"{wallet_pay.UNCERTAIN_REFUND_MINUTES} min without a confirmed delivery. Check the web order (needs_review).")
    except Exception as error:
        logger.warning("Trả bằng ví: không gửi được thông báo đơn %s: %s", result.order_id, type(error).__name__)


async def run_wallet_order(bot, order_id: str):
    """Giai đoạn 2 cho một đơn ví (một lần tại một thời điểm), rồi báo kết quả. Idempotent."""
    async with WALLET_PAY_LOCK:
        result = await asyncio.to_thread(_process_wallet_order_sync, order_id)
    await _notify_wallet_result(bot, result)
    return result


def _begin_wallet_payment(user, sku: str, qty: int, price: int):
    conn = get_db_connection()
    try:
        return wallet_pay.begin_payment(conn, user.id, user.username or user.first_name, sku, qty, price)
    finally:
        conn.close()


async def handle_wallet_pay(query, context, user, lang: str, sku: str, qty: int) -> None:
    """Nút "Trả bằng ví": kiểm tra trước, trừ ví + tạo đơn (GD1), giao hàng (GD2). Chỉ chat riêng, chỉ khi bật."""
    back = _wallet_back_markup(lang, cb_buy(sku))
    if not wallet_pay_enabled():
        await safe_edit_or_reply(query, f"{vip_em('shield', '🛡')} {t(lang, 'wallet_err_unavailable')}", reply_markup=back, parse_mode="HTML")
        return
    if not _is_private_chat(query.message):
        await query.answer(t(lang, "wallet_private_only"), show_alert=True)
        return
    # Kiểm tra trước (không đổi dữ liệu): SKU, số lượng, giá thật từ web, tồn kho.
    await asyncio.to_thread(sync_web_product_price, sku)
    product = get_product(sku)
    price = int(product["price"] or 0) if product else 0
    if sku not in known_skus() or price <= 0 or qty < 1 or qty > sku_max_qty(sku):
        await query.answer(t(lang, "wpay_err_unavailable"), show_alert=True)
        return
    stock = await asyncio.to_thread(get_stock_count, sku)
    if stock < qty:
        await query.answer(t(lang, "alert_only_left", stock=stock) if stock > 0 else t(lang, "alert_sold_out"), show_alert=True)
        return
    try:
        order = await asyncio.to_thread(_begin_wallet_payment, user, sku, qty, price)
    except wallet_pay.PayError as error:
        key = {"insufficient_funds": "wpay_err_funds", "in_flight": "wpay_err_busy"}.get(error.code, "wpay_err_unavailable")
        await query.answer(t(lang, key), show_alert=True)
        return
    except Exception as error:
        logger.error("Trả bằng ví: lỗi tạo đơn: %s", type(error).__name__)
        await query.answer(t(lang, "wpay_err_unavailable"), show_alert=True)
        return
    await safe_edit_or_reply(
        query, f"{vip_em('calendar', '⏳')} " + t(lang, "wpay_processing", order=html.escape(order.order_id), amount=i18n.fmt_vnd(order.amount_vnd)),
        reply_markup=InlineKeyboardMarkup([[vip_btn(t(lang, "btn_wpay_check"), callback_data=f"confirm_pay_{order.order_id}", emoji_name="refresh")]]),
        parse_mode="HTML")
    result = await run_wallet_order(context.bot, order.order_id)
    if result.state == "completed":
        await safe_edit_or_reply(
            query, f"{vip_em('check', '✅')} " + t(lang, "wpay_paid_msg", order=html.escape(order.order_id), amount=i18n.fmt_vnd(order.amount_vnd),
                                                  balance=i18n.fmt_vnd(order.balance_vnd)),
            reply_markup=_wallet_back_markup(lang, "main_menu"), parse_mode="HTML")


async def handle_wallet_check(query, context, order, lang: str) -> None:
    """Nút "Kiểm tra" của đơn ví: thử giao lại (idempotent) hoặc báo đã hoàn tiền."""
    order_id = order["order_id"]
    if order["status"] == "cancelled":
        await query.answer(t(lang, "wpay_cancelled_alert"), show_alert=True)
        return
    result = await run_wallet_order(context.bot, order_id)
    if result.state == "completed":
        await query.answer(t(lang, "payment_confirmed_alert"), show_alert=True)
    elif result.state in ("refunded", "refunded_review"):
        await query.answer(t(lang, "wpay_cancelled_alert"), show_alert=True)
    else:
        await query.answer(t(lang, "wpay_wait_alert"), show_alert=True)


# ── Admin ví (PR4): /wallet, /wallet_add, /wallet_sub, /backup. Chỉ admin, chỉ chat riêng, chỉ khi ví bật ────────────────────
# Không log số dư / lý do / số tiền vào log chung (chỉ mã lỗi).
_ADMIN_ERRORS = {
    "bad_user": "Telegram ID không hợp lệ (chỉ gồm chữ số).",
    "bad_amount": "Số tiền không hợp lệ: chỉ nhập số nguyên dương, ví dụ 50000.",
    "too_large": f"Mỗi lần chỉnh tối đa {wallet_store.MAX_ADMIN_ADJUST_VND:,} VNĐ.",
    "reason_required": f"Phải có lý do (tối đa {wallet_store.MAX_NOTE_LENGTH} ký tự).",
    "insufficient_funds": "Số dư của khách không đủ để trừ (ví không được âm).",
}


async def _admin_wallet_gate(update) -> bool:
    """Admin + chat riêng + ví bật. Người không phải admin bị từ chối; không tiết lộ gì thêm."""
    message = update.message
    if message is None:
        return False
    if not is_admin(update.effective_user):
        await message.reply_text(f"{vip_em('cross', '⛔')} <b>Bạn không có quyền Admin!</b>", parse_mode="HTML")
        return False
    if not _is_private_chat(message):
        await message.reply_text("Vui lòng dùng lệnh này trong chat riêng với bot.")
        return False
    return True


def _admin_args(update, context) -> list[str]:
    if context.args:
        return list(context.args)
    return update.message.text.split()[1:] if update.message and update.message.text else []


def _format_ledger_line(line) -> str:
    sign = "+" if line.amount_vnd > 0 else "-"
    note = f" · {html.escape(line.note)}" if line.note else ""
    return (f"#{line.id} {sign}{abs(line.amount_vnd):,} · {line.kind} · số dư {line.balance_after:,} · "
            f"<code>{html.escape(line.ref)}</code> · {str(line.created_at)[:16]}{note}")


def _admin_wallet_view_sync(user_id: int):
    conn = get_db_connection()
    try:
        return wallet_admin.view(conn, user_id, 10)
    finally:
        conn.close()


def _admin_wallet_adjust_sync(user_id: int, sign: int, amount: int, reason: str, admin_id):
    conn = get_db_connection()
    try:
        return wallet_admin.adjust(conn, user_id, sign, amount, reason, admin_id)
    finally:
        conn.close()


async def admin_wallet_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/wallet <telegram_id>: số dư + 10 dòng sổ cái gần nhất."""
    if not await _admin_wallet_gate(update):
        return
    if not wallet_enabled():
        await update.message.reply_text("Ví chưa bật (WALLET_ENABLED).")
        return
    args = _admin_args(update, context)
    if len(args) != 1:
        await update.message.reply_text("Cú pháp: /wallet <telegram_id>")
        return
    try:
        view = await asyncio.to_thread(_admin_wallet_view_sync, wallet_admin.parse_user_id(args[0]))
    except wallet_admin.AdminError as error:
        await update.message.reply_text(_ADMIN_ERRORS.get(error.code, "Lỗi."))
        return
    except Exception as error:
        logger.error("Admin ví: không đọc được ví: %s", type(error).__name__)
        await update.message.reply_text("Không đọc được ví lúc này.")
        return
    lines = "\n".join(_format_ledger_line(line) for line in view.lines) or "Chưa có giao dịch."
    await update.message.reply_text(f"<b>Ví {view.user_id}</b>\nSố dư: <b>{view.balance_vnd:,} VNĐ</b>\n\n{lines}", parse_mode="HTML")


async def _admin_wallet_adjust(update: Update, context: ContextTypes.DEFAULT_TYPE, sign: int, usage: str):
    if not await _admin_wallet_gate(update):
        return
    if not wallet_enabled():
        await update.message.reply_text("Ví chưa bật (WALLET_ENABLED).")
        return
    args = _admin_args(update, context)
    if len(args) < 3:
        await update.message.reply_text(f"Cú pháp: {usage}\nLý do là bắt buộc.")
        return
    try:
        user_id = wallet_admin.parse_user_id(args[0])
        amount = wallet_admin.parse_amount(args[1])
        line = await asyncio.to_thread(_admin_wallet_adjust_sync, user_id, sign, amount, " ".join(args[2:]), update.effective_user.id)
    except wallet_admin.AdminError as error:
        await update.message.reply_text(_ADMIN_ERRORS.get(error.code, "Lỗi."))
        return
    except Exception as error:
        logger.error("Admin ví: không chỉnh được ví: %s", type(error).__name__)
        await update.message.reply_text("Không chỉnh được ví lúc này. Chưa có thay đổi nào.")
        return
    await update.message.reply_text(f"{vip_em('check', '✅')} Đã ghi dòng sổ cái:\n{_format_ledger_line(line)}", parse_mode="HTML")


async def admin_wallet_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _admin_wallet_adjust(update, context, 1, "/wallet_add <telegram_id> <số_tiền> <lý do>")


async def admin_wallet_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _admin_wallet_adjust(update, context, -1, "/wallet_sub <telegram_id> <số_tiền> <lý do>")


TELEGRAM_FILE_LIMIT_BYTES = 45 * 1024 * 1024


def _make_backup_sync() -> str:
    conn = get_db_connection()
    try:
        return wallet_admin.make_backup(conn, DATA_DIR)
    finally:
        conn.close()


async def admin_backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/backup: tạo bản sao lưu DB mới (giữ 14 bản) và gửi file cho admin trong chat riêng."""
    if not await _admin_wallet_gate(update):
        return
    try:
        path = await asyncio.to_thread(_make_backup_sync)
    except Exception as error:
        logger.error("Sao lưu DB lỗi: %s", type(error).__name__)
        await update.message.reply_text("Không tạo được bản sao lưu lúc này.")
        return
    if os.path.getsize(path) > TELEGRAM_FILE_LIMIT_BYTES:
        await update.message.reply_text(f"Đã tạo bản sao lưu nhưng quá lớn để gửi qua Telegram: {os.path.basename(path)} (trong thư mục backups của ổ dữ liệu).")
        return
    try:
        with open(path, "rb") as handle:
            await update.message.reply_document(document=handle, filename=os.path.basename(path))
    except Exception as error:
        logger.warning("Không gửi được file sao lưu: %s", type(error).__name__)
        await update.message.reply_text("Đã tạo bản sao lưu nhưng không gửi được file. Thử lại /backup.")


async def wallet_backup_task(application: Application):
    """Sao lưu định kỳ (mỗi 6 giờ, giữ 14 bản) khi ví bật. Không log nội dung."""
    await asyncio.sleep(30)
    while True:
        try:
            if wallet_enabled():
                await asyncio.to_thread(_make_backup_sync)
                logger.info("Đã sao lưu DB định kỳ.")
        except Exception as error:
            logger.error("Sao lưu định kỳ lỗi: %s", type(error).__name__)
        await asyncio.sleep(wallet_admin.BACKUP_INTERVAL_HOURS * 3600)


async def handle_wallet_view(query, user, lang: str) -> None:
    back = InlineKeyboardMarkup([[vip_btn(t(lang, "btn_back_main"), callback_data="main_menu", emoji_name="back")]])
    if not wallet_enabled():
        await safe_edit_or_reply(query, f"{vip_em('shield', '🛡')} {t(lang, 'wallet_err_unavailable')}", reply_markup=back,
                                 parse_mode="HTML")
        return
    if not _is_private_chat(query.message):
        await query.answer(t(lang, "wallet_private_only"), show_alert=True)
        return
    try:
        text, markup = await asyncio.to_thread(build_wallet_screen, user.id, lang)
    except Exception as error:
        logger.warning("Ví: không đọc được số dư: %s", type(error).__name__)
        await safe_edit_or_reply(query, f"{vip_em('shield', '🛡')} {t(lang, 'wallet_err_unavailable')}", reply_markup=back,
                                 parse_mode="HTML")
        return
    await safe_edit_or_reply(query, text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)


async def show_plisio_checkout(query, user, qty, order_id=None, sku=DEFAULT_SKU):
    import hashlib
    if not 1 <= qty <= 50 or sku not in known_skus() or (sku != DEFAULT_SKU and qty > sku_max_qty(sku)):
        return
    if order_id is None:
        # Repeated taps on the same confirmation reuse the same provider invoice.
        # The SKU is part of the reference (only for non-default products, keeping existing
        # gemini_plus order ids unchanged) so two products can never share an order code.
        reference = f"{user.id}:{query.message.chat_id}:{query.message.message_id}:{qty}"
        if sku != DEFAULT_SKU:
            reference += f":{sku}"
        order_id = "TGS" + hashlib.sha256(reference.encode()).hexdigest()[:12].upper()
    lang = get_user_lang(user.id)
    try:
        # gemini_plus keeps the original 3-argument call; other products pass their SKU explicitly.
        invoice_args = (order_id, user, qty) if sku == DEFAULT_SKU else (order_id, user, qty, sku)
        invoice = await asyncio.to_thread(create_web_plisio_invoice, *invoice_args)
    except PlisioMinimumAmountError as error:
        network = "BEP20 (BSC)" if error.currency == "USDT_BSC" else "TRC20"
        await safe_edit_or_reply(query,
            f"{vip_em('shield')} {t(lang, 'plisio_minimum', minimum=format(error.minimum, 'g'), network=network)}",
            reply_markup=InlineKeyboardMarkup([
                [vip_btn(t(lang, "btn_reselect_method"), callback_data=cb_qty("select", sku, qty), emoji_name="refresh", style="primary")],
                [vip_btn(t(lang, "btn_back_menu"), callback_data="main_menu", emoji_name="back")],
            ]), parse_mode="HTML")
        return
    except Exception as error:
        message = str(error)
        safe_error = message if re.fullmatch(r"HTTP [0-9]{3}: [a-z0-9_]{1,64}", message) else type(error).__name__
        logger.warning("Plisio invoice unavailable: %s", safe_error)
        await safe_edit_or_reply(query,
            f"{vip_em('shield')} {t(lang, 'plisio_unavailable')}",
            reply_markup=InlineKeyboardMarkup([
                [vip_btn(t(lang, "btn_reselect_method"), callback_data=cb_qty("select", sku, qty), emoji_name="refresh")],
                [vip_btn(t(lang, "btn_back_menu"), callback_data="main_menu", emoji_name="back")],
            ]), parse_mode="HTML")
        return
    total = float(invoice["amount_usdt"])
    expiry = datetime.fromisoformat(invoice["expires_at"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    conn = get_db_connection()
    try:
        conn.execute("""INSERT INTO orders (order_id,user_id,user_name,product_id,amount,status,
            payment_method,currency,quantity,created_at,expires_at)
            VALUES (?,?,?,?,?,'pending','usdt','USDT',?,?,?)
            ON CONFLICT(order_id) DO UPDATE SET amount=excluded.amount,payment_method='usdt',
            currency='USDT',expires_at=excluded.expires_at WHERE orders.status='pending'""",
            (order_id,user.id,user.username or user.first_name,sku,total,qty,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"),expiry.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    finally:
        conn.close()
    bill = (
        f"{vip_em('crown')} ═══════ {vip_em('diamond')} <b>{t(lang, 'usdt_bill_title')}</b> {vip_em('diamond')} ═══════ {vip_em('crown')}\n\n"
        f"{vip_em('tag')} <b>{t(lang, 'bill_order_id')}:</b> <code>{order_id}</code>\n"
        f"{vip_em('box')} <b>{t(lang, 'bill_qty')}:</b> <b>{qty}</b>\n"
        f"{vip_em('money')} <b>{t(lang, 'usdt_amount')}:</b> <code>{total:g} USDT</code>\n"
        f"{vip_em('globe')} <b>{t(lang, 'usdt_gateway')}:</b> Plisio (<code>{'USDT BEP20 (BSC)' if invoice['currency'] == 'USDT_BSC' else html.escape(invoice['currency'])}</code>)\n"
        f"{vip_em('calendar')} <b>{t(lang, 'usdt_deadline')}:</b> <code>{expiry:%H:%M:%S}</code>\n\n"
        f"{vip_em('lightning')} {t(lang, 'usdt_bill_hint')}"
    )
    await safe_edit_or_reply(query, bill, reply_markup=InlineKeyboardMarkup([
        [vip_btn(t(lang, "btn_pay_plisio"), url=invoice["invoice_url"], emoji_name="usdt", style="success")],
        [vip_btn(t(lang, "btn_check_paid"), callback_data=f"confirm_pay_{order_id}", emoji_name="check", style="primary")],
        [vip_btn(t(lang, "btn_back_menu"), callback_data="main_menu", emoji_name="back")],
    ]), parse_mode="HTML")


def build_vietqr_bill(lang, order_id, product_name, qty, amount, bank_name, bank_account, bank_owner, expires_display) -> str:
    return (
        f"{vip_em('crown', '👑')} ═══════ {vip_em('diamond', '💎')} <b>{t(lang, 'bill_title')}</b> {vip_em('diamond', '💎')} ═══════ {vip_em('crown', '👑')}\n\n"
        f"{vip_em('tag', '🆔')} <b>{t(lang, 'bill_order_id')}:</b> <code>{order_id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{vip_em('box', '📦')} <b>{t(lang, 'bill_service')}:</b> {html.escape(str(product_name))}\n"
        f"{vip_em('sparkle', '✨')} <b>{t(lang, 'bill_qty')}:</b> <b>{qty}</b>\n"
        f"{vip_em('money', '💵')} <b>{t(lang, 'bill_amount')}:</b> <code>{i18n.fmt_vnd(amount)}</code>\n\n"
        f"{vip_em('bank', '🏦')} <b>{t(lang, 'bill_bank')}:</b> <code>{bank_name}</code> {t(lang, 'bill_bank_suffix')}\n"
        f"{vip_em('card', '💳')} <b>{t(lang, 'bill_account')}:</b> <code>{bank_account}</code>\n"
        f"{vip_em('user', '👤')} <b>{t(lang, 'bill_owner')}:</b> <code>{bank_owner}</code>\n"
        f"{vip_em('memo', '📝')} <b>{t(lang, 'bill_memo')}:</b> <code>{order_id}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{vip_em('calendar', '⏳')} <b>{t(lang, 'bill_deadline')}:</b> {t(lang, 'bill_deadline_value', expires=expires_display)}\n"
        f"{vip_em('lightning', '⚡️')} {t(lang, 'bill_tip')}\n"
        f"{vip_em('shield', '⚠️')} {t(lang, 'bill_warning')}"
    )


def build_vietqr_buttons(lang, order_id, total_usdt_str) -> list:
    return [
        [vip_btn(t(lang, "btn_paid_done"), callback_data=f"confirm_pay_{order_id}", emoji_name="check", style="primary")],
        [vip_btn(t(lang, "btn_switch_usdt", total=total_usdt_str), callback_data=f"switch_pay_usdt_{order_id}", emoji_name="usdt", style="success")],
        [vip_btn(t(lang, "btn_cancel_tx"), callback_data=f"cancel_order_{order_id}", emoji_name="cross", style="danger")],
        [vip_btn(t(lang, "btn_back_lobby"), callback_data="main_menu", emoji_name="back")],
    ]


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user = update.effective_user
    user_id = user.id
    lang = get_user_lang(user_id)
    await asyncio.to_thread(refresh_catalogue)
    sku_cb = parse_sku_qty_callback(data)
    view_cb = parse_view_callback(data)
    custom_sku = data[3:] if data.startswith("cq:") and data[3:] in known_skus() else None
    if data != "custom_qty" and custom_sku is None:
        AWAITING_QTY_USERS.pop(user_id, None)
    if data != "wtop_c":
        AWAITING_TOPUP_AMOUNT.discard(user_id)

    if data in ("main_menu", "buy_gemini_plus", "custom_qty"):
        await asyncio.to_thread(sync_web_product_price)
    elif data.startswith("buy_") and data[4:] in known_skus():
        await asyncio.to_thread(sync_web_product_price, data[4:])

    if data == "main_menu":
        await safe_edit_or_reply(
            query,
            welcome_message(user, lang),
            reply_markup=main_menu_keyboard(user_id, lang),
            parse_mode="HTML"
        )

    elif data == "shop":
        await safe_edit_or_reply(query, shop_message(lang), reply_markup=shop_keyboard(user_id, lang), parse_mode="HTML")

    elif data == "lang_menu":
        await safe_edit_or_reply(query, t(lang, "lang_title"), reply_markup=language_keyboard(lang), parse_mode="HTML")

    elif data in ("setlang:vi", "setlang:en"):
        lang = set_user_lang(user_id, data.split(":", 1)[1])
        await safe_edit_or_reply(
            query,
            welcome_message(user, lang),
            reply_markup=main_menu_keyboard(user_id, lang),
            parse_mode="HTML"
        )

    elif data == "buy_gemini_plus" or (data.startswith("buy_") and data[4:] in listed_skus()):
        sku = DEFAULT_SKU if data == "buy_gemini_plus" else data[4:]
        prod = get_product(sku)
        stock = get_stock_count(sku) if sku in known_skus() else 0
        if sku != DEFAULT_SKU and sku_price_vnd(sku) <= 0:
            await safe_edit_or_reply(
                query,
                f"{vip_em('cross', '❌')} {t(lang, 'product_unavailable')}",
                reply_markup=InlineKeyboardMarkup([[vip_btn(t(lang, "btn_back_menu"), callback_data="main_menu", emoji_name="back")]]),
                parse_mode="HTML"
            )
            return

        keyboard = []
        if stock > 0:
            if sku == DEFAULT_SKU:
                keyboard += [
                    [
                        vip_btn(t(lang, "btn_qty_n", n=1), callback_data="select_qty_1", emoji_name="crown"),
                        vip_btn(t(lang, "btn_qty_n", n=2), callback_data="select_qty_2", emoji_name="crown"),
                    ],
                    [
                        vip_btn(t(lang, "btn_qty_n", n=4), callback_data="select_qty_4", emoji_name="crown"),
                        vip_btn(t(lang, "btn_qty_n", n=8), callback_data="select_qty_8", emoji_name="crown"),
                    ],
                    [
                        vip_btn(t(lang, "btn_custom_qty"), callback_data="custom_qty", emoji_name="edit", style="primary")
                    ],
                ]
            else:
                max_qty = sku_max_qty(sku)
                if max_qty == 1:
                    keyboard.append([vip_btn(t(lang, "btn_buy_one"), callback_data=cb_qty("select", sku, 1), emoji_name="crown", style="primary")])
                else:
                    choices = [n for n in (1, 2, 4, 8) if n <= max_qty]
                    keyboard.append([vip_btn(f"{n}", callback_data=cb_qty("select", sku, n), emoji_name="crown") for n in choices])
                    keyboard.append([vip_btn(t(lang, "btn_custom_qty"), callback_data=f"cq:{sku}", emoji_name="edit", style="primary")])
        if has_own_guide(sku):
            keyboard.append([vip_btn(t(lang, "btn_guide"), callback_data=f"gd:{sku}", emoji_name="book")])
        keyboard.append([vip_btn(t(lang, "btn_back_shop"), callback_data="shop", emoji_name="back")])

        await send_product_screen(query, sku, render_product_intro(sku, stock, lang), keyboard, lang)

    elif data.startswith("soldout_") and data[8:] in listed_skus():
        await query.answer(t(lang, "soldout_alert"), show_alert=True)

    elif data.startswith("select_qty_") or (sku_cb and sku_cb[0] == "sq"):
        if sku_cb:
            _, sku, qty = sku_cb
        else:
            sku = DEFAULT_SKU
            try:
                qty = max(1, int(data.replace("select_qty_", "")))
            except Exception:
                qty = 1
        stock = get_stock_count(sku)
        if sku != DEFAULT_SKU and (qty < 1 or qty > sku_max_qty(sku) or stock < qty):
            reason = t(lang, "alert_sold_out") if stock <= 0 else (
                t(lang, "alert_max_per_order", limit=sku_max_qty(sku)) if qty > sku_max_qty(sku) else t(lang, "alert_only_left", stock=stock))
            await query.answer(reason, show_alert=True)
            return
        if stock < qty:
            await query.answer(t(lang, "alert_only_left", stock=stock), show_alert=True)
        await show_order_confirmation(query, user, sku=sku, qty=qty)

    elif data == "custom_qty" or custom_sku is not None:
        sku = custom_sku or DEFAULT_SKU
        AWAITING_QTY_USERS[user_id] = sku
        stock = get_stock_count(sku)
        prod = get_product(sku)
        price_fmt = i18n.fmt_vnd(prod['price'])
        usdt_price = get_setting(usdt_setting_key(sku), "1")
        prompt_text = (
            f"{vip_em('crown', '👑')} ═══════ {vip_em('memo', '✏️')} <b>{t(lang, 'qty_title')}</b> {vip_em('memo', '✏️')} ═══════ {vip_em('crown', '👑')}\n\n"
            f"{t(lang, 'qty_body')}\n\n"
            f"{vip_em('chart', '🏷')} <b>{t(lang, 'confirm_unit')}:</b> <code>{price_fmt}</code> ({t(lang, 'or_usdt')} <code>{usdt_price} USDT</code>)\n"
            f"{vip_em('box', '📦')} <b>{t(lang, 'qty_in_stock')}:</b> <code>{t(lang, 'stock_available_value', stock=stock)}</code>\n\n"
            f"{vip_em('lightning', '⚡️')} {t(lang, 'qty_range_hint', limit=sku_max_qty(sku))}"
        )
        keyboard = [
            [vip_btn(t(lang, "btn_cancel_reselect"), callback_data=cb_buy(sku), emoji_name="cross")]
        ]
        await safe_edit_or_reply(query, prompt_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data in ("create_order_gemini", "create_order_vietqr") or data.startswith("create_order_vietqr_") or (sku_cb and sku_cb[0] == "cv"):
        if sku_cb:
            _, sku, qty = sku_cb
            if qty < 1 or qty > sku_max_qty(sku):
                await query.answer(t(lang, "alert_max_per_order", limit=sku_max_qty(sku)), show_alert=True)
                return
        else:
            sku = DEFAULT_SKU
            qty = 1
            if data.startswith("create_order_vietqr_"):
                try:
                    qty = max(1, int(data.replace("create_order_vietqr_", "")))
                except Exception:
                    qty = 1

        if not await asyncio.to_thread(sync_web_product_price, sku, True):
            await safe_edit_or_reply(query, f"{vip_em('shield')} {t(lang, 'price_unavailable')}",
                reply_markup=InlineKeyboardMarkup([[vip_btn(t(lang, "btn_back_menu"), callback_data="main_menu", emoji_name="back")]]), parse_mode="HTML")
            return
        prod = get_product(sku)
        stock = get_stock_count(sku)

        if stock < qty:
            await safe_edit_or_reply(
                query,
                f"{vip_em('cross', '❌')} {t(lang, 'stock_insufficient', stock=stock)}",
                reply_markup=InlineKeyboardMarkup([[vip_btn(t(lang, "btn_reselect_qty"), callback_data=cb_buy(sku), emoji_name="refresh")]]),
                parse_mode="HTML"
            )
            return

        import secrets
        order_id = f"TGS{secrets.token_hex(4).upper()}"
        amount = prod["price"] * qty
        usdt_unit = get_usdt_price(sku)
        total_usdt = round(usdt_unit * qty, 2)
        total_usdt_str = str(int(total_usdt)) if total_usdt.is_integer() else str(total_usdt)

        now_dt = datetime.now()
        expires_dt = now_dt + timedelta(minutes=10)
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        expires_str = expires_dt.strftime("%Y-%m-%d %H:%M:%S")
        expires_display = expires_dt.strftime("%H:%M:%S")

        conn = get_db_connection()
        bank_name = get_setting("bank_name", "ACB")
        bank_account = get_setting("bank_account", "68986683")
        bank_owner = get_setting("bank_owner", "LE DONG HA")
        conn.execute(
            """INSERT INTO orders
               (order_id, user_id, user_name, product_id, amount, status, payment_method, currency,
                quantity, created_at, expires_at, bank_name, bank_account, bank_owner)
               VALUES (?, ?, ?, ?, ?, 'pending', 'vietqr', 'VND', ?, ?, ?, ?, ?, ?)""",
            (order_id, user_id, user.username or user.first_name, sku, amount, qty,
             now_str, expires_str, bank_name, bank_account, bank_owner)
        )
        conn.commit()
        conn.close()

        # Đồng bộ đơn hàng lên Web Dashboard wolfmod.xyz
        asyncio.create_task(sync_order_to_web_async(order_id, user_id, user.username or user.first_name, amount, "VND", "sepay", qty=qty, sku=sku))

        bill_text = build_vietqr_bill(lang, order_id, sku_title(sku, lang), qty, amount, bank_name, bank_account, bank_owner, expires_display)
        buttons = build_vietqr_buttons(lang, order_id, total_usdt_str)

        qr_bytes = generate_vietqr_png_bytes(bank_name, bank_account, bank_owner, amount, order_id)

        try:
            if qr_bytes:
                bio = io.BytesIO(qr_bytes)
                bio.name = f"{order_id}.png"
                await query.message.reply_photo(
                    photo=bio,
                    caption=bill_text,
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode="HTML"
                )
                try:
                    await query.message.delete()
                except Exception:
                    pass
            else:
                await safe_edit_or_reply(
                    query,
                    bill_text,
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.warning(f"Lỗi gửi ảnh QR VietQR: {e}")
            await safe_edit_or_reply(
                query,
                bill_text,
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )

    elif sku_cb and sku_cb[0] == "cb":
        _, sku, qty = sku_cb
        await show_bep20_checkout(query, user, qty, sku=sku)

    elif sku_cb and sku_cb[0] == "cp":
        _, sku, qty = sku_cb
        await show_binance_checkout(query, user, qty, sku=sku)

    elif data.startswith("bpid_"):
        await prompt_binance_id(query, user, data[len("bpid_"):])

    elif data == "create_order_usdt" or data.startswith("create_order_usdt_") or (sku_cb and sku_cb[0] == "cu"):
        if sku_cb:
            _, sku, qty = sku_cb
        else:
            sku = DEFAULT_SKU
            suffix = data.removeprefix("create_order_usdt_")
            qty = int(suffix) if suffix.isdigit() else 1
        await show_plisio_checkout(query, user, qty, sku=sku)

    elif data.startswith("switch_pay_usdt_"):
        order_id = data.removeprefix("switch_pay_usdt_")
        conn = get_db_connection()
        order = conn.execute("SELECT * FROM orders WHERE order_id=? AND user_id=?", (order_id, user_id)).fetchone()
        conn.close()
        if order and order["status"] == "pending":
            if order_sku(order) not in known_skus():
                await query.answer(t(lang, "sku_not_sold_alert"), show_alert=True)
                return
            await show_plisio_checkout(query, user, int(order["quantity"] or 1), order_id, sku=order_sku(order))

    elif data.startswith("switch_pay_vietqr_"):
        order_id = data.replace("switch_pay_vietqr_", "")
        conn = get_db_connection()
        order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        conn.close()
        if not order or int(order["user_id"]) != int(user_id) or order["status"] != "pending":
            await query.answer(t(lang, "order_invalid_alert"), show_alert=True)
            return
        sku = order_sku(order)
        if sku not in known_skus():
            await query.answer(t(lang, "sku_not_sold_alert"), show_alert=True)
            return
        if not await asyncio.to_thread(sync_web_product_price, sku, True):
            await query.answer(t(lang, "price_unavailable_alert"), show_alert=True)
            return
        qty = 1
        if order and "quantity" in order.keys() and order["quantity"]:
            try:
                qty = max(1, int(order["quantity"]))
            except Exception:
                qty = 1

        prod = get_product(sku)
        amount = prod["price"] * qty

        bank_name = get_setting("bank_name", "ACB")
        bank_account = get_setting("bank_account", "68986683")
        bank_owner = get_setting("bank_owner", "LE DONG HA")
        conn = get_db_connection()
        conn.execute(
            """UPDATE orders SET payment_method = 'vietqr', currency = 'VND', amount = ?,
                   bank_name = ?, bank_account = ?, bank_owner = ? WHERE order_id = ?""",
            (amount, bank_name, bank_account, bank_owner, order_id)
        )
        conn.commit()
        conn.close()

        usdt_unit = get_usdt_price(sku)
        total_usdt = round(usdt_unit * qty, 2)
        total_usdt_str = str(int(total_usdt)) if total_usdt.is_integer() else str(total_usdt)

        expires_display = t(lang, "bill_ten_minutes")
        if order and "expires_at" in order.keys() and order["expires_at"]:
            try:
                exp_dt = datetime.strptime(order["expires_at"], "%Y-%m-%d %H:%M:%S")
                expires_display = exp_dt.strftime("%H:%M:%S")
            except Exception:
                pass

        bill_text = build_vietqr_bill(lang, order_id, sku_title(sku, lang), qty, amount, bank_name, bank_account, bank_owner, expires_display)
        buttons = build_vietqr_buttons(lang, order_id, total_usdt_str)

        qr_bytes = generate_vietqr_png_bytes(bank_name, bank_account, bank_owner, amount, order_id)
        try:
            if qr_bytes:
                bio = io.BytesIO(qr_bytes)
                bio.name = f"{order_id}.png"
                await query.message.reply_photo(
                    photo=bio,
                    caption=bill_text,
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode="HTML"
                )
                try:
                    await query.message.delete()
                except Exception:
                    pass
            else:
                await safe_edit_or_reply(
                    query,
                    bill_text,
                    reply_markup=InlineKeyboardMarkup(buttons),
                    parse_mode="HTML"
                )
        except Exception as e:
            logger.warning(f"Lỗi gửi ảnh QR VietQR: {e}")
            await safe_edit_or_reply(
                query,
                bill_text,
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode="HTML"
            )

    elif sku_cb and sku_cb[0] == "wp":
        await handle_wallet_pay(query, context, user, lang, sku_cb[1], sku_cb[2])

    elif data.startswith("confirm_pay_"):
        order_id = data.replace("confirm_pay_", "")
        conn = get_db_connection()
        order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        conn.close()

        if not order:
            await query.answer(t(lang, "order_not_found"), show_alert=True)
            return

        if int(order["user_id"]) != int(user_id):
            await query.answer(t(lang, "order_not_found"), show_alert=True)
            return

        if order["kind"] == "wallet_topup":
            await handle_topup_check(query, context, order, lang)
            return

        if order["payment_method"] == "wallet" and order["status"] in (wallet_pay.PROCESSING, "cancelled"):
            await handle_wallet_check(query, context, order, lang)
            return

        if order["status"] == "completed":
            if not order["delivery_notified_at"]:
                sent = await send_paid_order_delivery(context.bot, order_id, user_id)
                await query.answer(t(lang, "delivery_sent" if sent else "delivery_retry"), show_alert=True)
                return
            links = (order["item_delivered"] or "").splitlines()
            if links:
                msg_body, link_buttons = format_delivered_links_message(order_id, links, order_sku(order), lang)
                await query.message.reply_text(msg_body, reply_markup=InlineKeyboardMarkup(link_buttons), parse_mode="HTML")
                conn = get_db_connection()
                conn.execute("UPDATE orders SET delivery_notified_at = CURRENT_TIMESTAMP WHERE order_id = ? AND user_id = ?", (order_id, user_id))
                conn.commit()
                conn.close()
            await query.answer(t(lang, "order_delivered_alert"), show_alert=True)
            return

        # Confirm the provider status before local expiry/cancellation checks. VietQR
        # may have been marked paid by the website while this SQLite order is stale.
        bep20_stat = {}
        if order["payment_method"] == "bep20" and order["status"] == "pending":
            bep20_stat = await asyncio.to_thread(check_web_bep20, order_id, user_id)
            if bep20_stat.get("status") == "needs_review":
                await query.answer(t(lang, "bep20_review_alert"), show_alert=True)
                await query.message.reply_text(
                    t(lang, "bep20_review_message", shield=vip_em('shield'), chat=vip_em('chat', '💬'), order=html.escape(order_id)),
                    reply_markup=InlineKeyboardMarkup([[vip_btn(t(lang, "btn_contact_admin"), url="https://t.me/wolfmodyt", emoji_name="chat")]]),
                    parse_mode="HTML")
                return
        web_stat = check_web_order_status(order_id)
        if web_stat.get("status") in ("paid", "delivered"):
            if await send_paid_order_delivery(context.bot, order_id, user_id):
                await query.answer(t(lang, "payment_confirmed_alert"), show_alert=True)
                return

            await query.answer(t(lang, "payment_waiting_alert"), show_alert=True)
            await query.message.reply_text(
                f"{vip_em('check', '✅')} {t(lang, 'pay_confirmed_head', order_id=html.escape(order_id))}\n"
                f"{vip_em('calendar', '⏳')} {t(lang, 'pay_no_auto')}\n"
                f"{vip_em('chat', '💬')} {t(lang, 'pay_contact_admin')}",
                reply_markup=InlineKeyboardMarkup([[vip_btn(t(lang, "btn_contact_admin"), url="https://t.me/wolfmodyt", emoji_name="chat")]]),
                parse_mode="HTML"
            )
            return

        # Kiểm tra nếu đơn hàng đã bị hủy hoặc hết hạn
        if order["status"] in ("cancelled", "expired"):
            await query.answer(t(lang, "order_expired_alert"), show_alert=True)
            await safe_edit_or_reply(
                query,
                f"{vip_em('cross', '❌')} {t(lang, 'order_expired_text', order_id=order_id)}",
                reply_markup=InlineKeyboardMarkup([[vip_btn(t(lang, "btn_new_order"), callback_data=cb_buy(order_sku(order)), emoji_name="crown", style="primary")]]),
                parse_mode="HTML"
            )
            return

        # Kiểm tra nếu quá hạn 10 phút so với thời điểm bấm
        now_dt = datetime.now()
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        is_expired = False
        if "expires_at" in order.keys() and order["expires_at"]:
            if str(order["expires_at"]) < now_str:
                is_expired = True
        elif "created_at" in order.keys() and order["created_at"]:
            try:
                c_dt = datetime.strptime(str(order["created_at"]), "%Y-%m-%d %H:%M:%S")
                if (now_dt - c_dt).total_seconds() > 600:
                    is_expired = True
            except Exception:
                pass

        if is_expired:
            conn = get_db_connection()
            conn.execute("UPDATE orders SET status = 'cancelled' WHERE order_id = ? AND status = 'pending'", (order_id,))
            conn.commit()
            conn.close()

            await query.answer(t(lang, "order_expired_alert_short"), show_alert=True)
            await safe_edit_or_reply(
                query,
                f"{vip_em('cross', '❌')} {t(lang, 'order_expired_text', order_id=order_id)}",
                reply_markup=InlineKeyboardMarkup([[vip_btn(t(lang, "btn_new_order"), callback_data=cb_buy(order_sku(order)), emoji_name="crown", style="primary")]]),
                parse_mode="HTML"
            )
            return

        qty = 1
        if "quantity" in order.keys() and order["quantity"]:
            try:
                qty = max(1, int(order["quantity"]))
            except Exception:
                qty = 1

        # Status was checked before the local expiry guard above.
        status = web_stat.get("status")
        if status in ("paid", "delivered"):
            notice = t(lang, "check_paid_no_link")
        elif status == "pending" or status == "not_found" or web_stat.get("found") is False:
            notice = t(lang, "check_not_received")
            if bep20_stat.get("confirmations"):
                notice = t(lang, "bep20_confirming", n=bep20_stat["confirmations"],
                           need=bep20_stat.get("min_confirmations", "?"))
        else:
            notice = t(lang, "check_unknown")
        await query.message.reply_text(
            f"{vip_em('crown')} ═══════ {vip_em('diamond')} <b>{t(lang, 'check_title')}</b> {vip_em('diamond')} ═══════ {vip_em('crown')}\n\n"
            f"{vip_em('tag')} <b>{t(lang, 'check_order')}:</b> <code>{html.escape(order_id)}</code>\n\n"
            f"{vip_em('shield')} {notice}\n\n"
            f"{vip_em('chat')} {t(lang, 'check_footer')}",
            reply_markup=InlineKeyboardMarkup([
                [vip_btn(t(lang, "btn_check_again"), callback_data=f"confirm_pay_{order_id}", emoji_name="refresh", style="primary")],
                [vip_btn(t(lang, "btn_contact_admin"), url="https://t.me/wolfmodyt", emoji_name="chat")],
                [vip_btn(t(lang, "btn_back_menu"), callback_data="main_menu", emoji_name="back")],
            ]), parse_mode="HTML"
        )

    elif data.startswith("cancel_order_"):
        order_id = data.replace("cancel_order_", "")
        conn = get_db_connection()
        conn.execute("UPDATE orders SET status = 'cancelled' WHERE order_id = ? AND status = 'pending'", (order_id,))
        conn.commit()
        conn.close()

        await safe_edit_or_reply(
            query,
            f"{vip_em('cross', '❌')} {t(lang, 'cancelled_text', order_id=html.escape(order_id))}",
            reply_markup=InlineKeyboardMarkup([[vip_btn(t(lang, "btn_back_menu"), callback_data="main_menu", emoji_name="back")]]),
            parse_mode="HTML"
        )

    elif data == "my_orders" or (view_cb and view_cb[0] == "ordp"):
        # Bấm "Đơn hàng" mới hiện đơn của chính khách (có phân trang); màn chính không liệt kê đơn.
        orders_text, orders_markup = render_orders_page(user_id, 1 if data == "my_orders" else view_cb[1], lang)
        await safe_edit_or_reply(query, orders_text, reply_markup=orders_markup, parse_mode="HTML")

    elif view_cb and view_cb[0] == "gd":
        # Hướng dẫn chỉ của đúng sản phẩm này; chưa có thì báo trung tính, không mượn hướng dẫn sản phẩm khác.
        sku = view_cb[1]
        guide_text = render_guide(sku, lang)
        keyboard = []
        if sku in known_skus():
            keyboard.append([vip_btn(t(lang, "btn_view_product"), callback_data=cb_buy(sku), emoji_name="lightning", style="success")])
        keyboard.append([vip_btn(t(lang, "btn_my_orders"), callback_data="my_orders", emoji_name="diamond")])
        keyboard.append([vip_btn(t(lang, "btn_back_menu"), callback_data="main_menu", emoji_name="back")])
        await safe_edit_or_reply(query, guide_text if guide_text is not None else f"{vip_em('shield', '🛡')} {t(lang, 'guide_none')}",
                                 reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "guide":
        # Nút cũ còn sót trong tin nhắn đã gửi: hướng dẫn không còn là màn chung.
        await safe_edit_or_reply(
            query,
            f"{vip_em('book', '📖')} {t(lang, 'guide_moved')}",
            reply_markup=InlineKeyboardMarkup([
                [vip_btn(t(lang, "btn_my_orders"), callback_data="my_orders", emoji_name="diamond")],
                [vip_btn(t(lang, "btn_back_menu"), callback_data="main_menu", emoji_name="back")],
            ]), parse_mode="HTML")

    elif data == "wtop" or data == "wtop_c" or data.startswith("wtop_a:"):
        await handle_topup_callback(query, user, lang, data)

    elif data == "wallet":
        await handle_wallet_view(query, user, lang)

    elif data == "support":
        contact = get_setting("support_contact", "@wolfmodyt")
        clean_contact = contact.replace("@", "")
        supp_text = (
            f"{vip_em('shield', '🛡')} ═══════ {vip_em('diamond', '💎')} <b>{t(lang, 'support_title')}</b> {vip_em('diamond', '💎')} ═══════ {vip_em('shield', '🛡')}\n\n"
            f"{t(lang, 'support_body')}\n\n"
            f"{vip_em('crown', '👑')} <b>{t(lang, 'support_admin')}:</b> <a href=\"https://t.me/{clean_contact}\">@{clean_contact}</a>\n"
            f"{vip_em('lightning', '⚡️')} <b>{t(lang, 'support_hours')}:</b> {t(lang, 'support_hours_value')}\n"
            f"{vip_em('globe', '🌐')} <b>{t(lang, 'support_web')}:</b> <a href=\"https://www.wolfmod.xyz\">wolfmod.xyz</a>\n\n"
            f"{vip_em('star', '⚜️')} {t(lang, 'support_footer')}"
        )
        keyboard = [
            [vip_btn(t(lang, "btn_msg_admin", contact=clean_contact), url=f"https://t.me/{clean_contact}", emoji_name="chat")],
            [vip_btn(t(lang, "btn_back_main"), callback_data="main_menu", emoji_name="back")]
        ]
        await safe_edit_or_reply(query, supp_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML", disable_web_page_preview=True)

    # ==================== ADMIN ACTIONS ====================
    elif data == "admin_panel":
        if not is_admin(user):
            await query.answer("⛔ Bạn không có quyền truy cập!", show_alert=True)
            return

        stock = get_stock_count("gemini_plus")
        conn = get_db_connection()
        total_sold = conn.execute("SELECT COUNT(*) as cnt FROM stock WHERE status = 'sold'").fetchone()["cnt"]
        revenue_vnd = conn.execute("SELECT SUM(amount) as rev FROM orders WHERE status = 'completed' AND (payment_method = 'vietqr' OR payment_method IS NULL)").fetchone()["rev"] or 0
        usdt_sold = conn.execute("SELECT COUNT(*) as cnt FROM orders WHERE status = 'completed' AND payment_method = 'usdt'").fetchone()["cnt"] or 0
        conn.close()

        bank_name = get_setting("bank_name", "ACB")
        bank_account = get_setting("bank_account", "68986683")
        bank_owner = get_setting("bank_owner", "LE DONG HA")
        usdt_network = get_setting("usdt_network", "BEP20")
        usdt_wallet = get_setting("usdt_wallet", "0xWolfModVIPBEP20Payment88888888888888888888")
        usdt_price = get_setting("usdt_price", "1")

        try:
            usdt_rev = usdt_sold * float(usdt_price)
        except Exception:
            usdt_rev = 0

        admin_text = (
            f"{vip_em('crown', '👑')} ═══════ {vip_em('star', '⚜️')} <b>BẢNG QUẢN TRỊ VIÊN VIP (ADMIN)</b> {vip_em('star', '⚜️')} ═══════ {vip_em('crown', '👑')}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{vip_em('box', '📦')} <b>Tồn kho Gemini Plus VIP:</b> <code>{stock}</code> link sẵn sàng\n"
            f"{vip_em('fire', '🔥')} <b>Đơn hàng đã xuất:</b> <code>{total_sold}</code> link\n"
            f"{vip_em('money', '💰')} <b>Doanh thu VNĐ:</b> <code>{revenue_vnd:,} VNĐ</code> | <b>USDT:</b> <code>{usdt_rev:g} USDT</code>\n\n"
            f"{vip_em('bank', '🏦')} <b>VietQR:</b> <code>{bank_name}</code> - <code>{bank_account}</code> ({bank_owner})\n"
            f"{vip_em('usdt', '🪙')} <b>Ví USDT ({usdt_network}):</b> <code>{usdt_price} USDT</code>\n"
            f"<code>{usdt_wallet}</code>\n\n"
            f"{vip_em('diamond', '💎')} <b>Lệnh quản trị nhanh:</b>\n"
            f"{vip_em('diamond', '💎')} <code>/setprice &lt;giá_vnđ&gt;</code> - Cập nhật giá bán VNĐ\n"
            f"{vip_em('bank', '🏦')} <code>/setbank &lt;ACB&gt; &lt;STK&gt; &lt;Tên&gt;</code> - Cập nhật STK VietQR\n"
            f"{vip_em('usdt', '🪙')} <code>/setusdt &lt;BEP20&gt; &lt;địa_chỉ_ví&gt; [giá_usdt]</code> - Cập nhật ví USDT\n"
            f"{vip_em('box', '📦')} <code>/addstock &lt;link1&gt; &lt;link2&gt;</code> - Nạp thêm link vào kho"
        )

        keyboard = [
            [
                vip_btn(" Xem Kho Link VIP", callback_data="admin_view_stock", emoji_name="box"),
                vip_btn(" Web Admin Dashboard", url="https://www.wolfmod.xyz/m7k9xq2r/dashboard#tg-sales", emoji_name="globe")
            ],
            [vip_btn("Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")]
        ]
        await safe_edit_or_reply(query, admin_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "admin_toggle_test_mode":
        current = get_setting("test_mode", "false").lower() == "true"
        new_val = "false" if current else "true"
        set_setting("test_mode", new_val)
        await query.answer(f"Đã chuyển Test Mode thành: {new_val.upper()}!", show_alert=True)
        query.data = "admin_panel"
        await callback_handler(update, context)

    elif data == "admin_view_stock":
        conn = get_db_connection()
        avail = conn.execute("SELECT COUNT(*) as c FROM stock WHERE status = 'available'").fetchone()["c"]
        sold = conn.execute("SELECT COUNT(*) as c FROM stock WHERE status = 'sold'").fetchone()["c"]
        conn.close()

        txt = (
            f"{vip_em('diamond', '💎')} ═══════ {vip_em('box', '📦')} <b>CHI TIẾT KHO LINK VIP</b> {vip_em('box', '📦')} ═══════ {vip_em('diamond', '💎')}\n\n"
            f"{vip_em('crown', '👑')} <b>Gói:</b> Google AI Plus (400 GB Cloud)\n"
            f"{vip_em('check', '🟢')} <b>Khả dụng trong kho:</b> <code>{avail}</code> link sẵn sàng xuất\n"
            f"{vip_em('fire', '🔥')} <b>Đã bàn giao:</b> <code>{sold}</code> link\n\n"
            f"{vip_em('star', '💡')} <i>Nạp thêm link bất cứ lúc nào bằng lệnh:</i>\n<code>/addstock &lt;link1&gt; &lt;link2&gt;</code> hoặc thêm trực tiếp vào file <code>claimed_links.txt</code>."
        )
        keyboard = [[vip_btn(" Trở Về Bảng Admin VIP", callback_data="admin_panel", emoji_name="crown")]]
        await safe_edit_or_reply(query, txt, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data.startswith("admin_approve_"):
        order_id = data.replace("admin_approve_", "")
        conn = get_db_connection()
        order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        conn.close()

        if not order:
            await query.answer("Đơn hàng không tồn tại!", show_alert=True)
            return

        delivered_links = deliver_product(order_id, order["user_id"])
        if delivered_links:
            links_summary = "\n".join([f"• <code>{lk}</code>" for lk in delivered_links])
            await safe_edit_or_reply(
                query,
                f"{vip_em('check', '✅')} <b>ĐÃ DUYỆT ĐƠN <code>{order_id}</code> ({len(delivered_links)} link) THÀNH CÔNG!</b>\n"
                f"{links_summary}",
                parse_mode="HTML"
            )

            # Bắn link cho khách
            msg_body, link_buttons = format_delivered_links_message(order_id, delivered_links, order_sku(order), get_user_lang(order["user_id"]))
            try:
                await context.bot.send_message(
                    chat_id=order["user_id"],
                    text=msg_body,
                    reply_markup=InlineKeyboardMarkup(link_buttons),
                    parse_mode="HTML"
                )
            except Exception as e_send:
                logger.error(f"Lỗi gửi tin nhắn cho khách: {e_send}")
        else:
            await query.answer("❌ Kho hàng đã hết link!", show_alert=True)

    elif data.startswith("admin_reject_"):
        order_id = data.replace("admin_reject_", "")
        conn = get_db_connection()
        order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        conn.execute("UPDATE orders SET status = 'cancelled' WHERE order_id = ?", (order_id,))
        conn.commit()
        conn.close()

        await safe_edit_or_reply(query, f"{vip_em('cross', '❌')} <b>ĐÃ TỪ CHỐI ĐƠN HÀNG <code>{order_id}</code>.</b>", parse_mode="HTML")
        if order:
            try:
                await context.bot.send_message(
                    chat_id=order["user_id"],
                    text=f"{vip_em('cross', '❌')} {t(get_user_lang(order['user_id']), 'order_rejected', order_id=html.escape(str(order_id)))}",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            except Exception:
                pass

# ==================== ADMIN COMMANDS ====================

async def admin_set_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user):
        await update.message.reply_text(f"{vip_em('cross', '⛔')} <b>Bạn không có quyền Admin!</b>", parse_mode="HTML")
        return

    args = context.args if (context.args is not None and len(context.args) > 0) else (update.message.text.split()[1:] if update.message and update.message.text else [])
    if not args:
        await update.message.reply_text(f"{vip_em('shield', '⚠️')} <b>Cú pháp:</b> <code>/setprice &lt;giá_tiền&gt;</code>\nVí dụ: <code>/setprice 25000</code>", parse_mode="HTML")
        return

    try:
        new_price = int(args[0])
        conn = get_db_connection()
        conn.execute("UPDATE products SET price = ? WHERE id = 'gemini_plus'", (new_price,))
        conn.commit()
        conn.close()
        await update.message.reply_text(f"{vip_em('check', '✅')} <b>Đã cập nhật giá Gemini Plus thành:</b> <code>{new_price:,} VNĐ</code>", parse_mode="HTML")
    except ValueError:
        await update.message.reply_text(f"{vip_em('cross', '❌')} <b>Giá tiền phải là số nguyên!</b>", parse_mode="HTML")

async def admin_set_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user):
        await update.message.reply_text(f"{vip_em('cross', '⛔')} <b>Bạn không có quyền Admin!</b>", parse_mode="HTML")
        return

    args = context.args if (context.args is not None and len(context.args) > 0) else (update.message.text.split()[1:] if update.message and update.message.text else [])
    if len(args) < 3:
        await update.message.reply_text(
            f"{vip_em('shield', '⚠️')} <b>Cú pháp:</b> <code>/setbank &lt;ngân_hàng&gt; &lt;số_tài_khoản&gt; &lt;tên_chủ_tài_khoản&gt;</code>\n"
            "Ví dụ: <code>/setbank ACB 68986683 LE DONG HA</code>",
            parse_mode="HTML"
        )
        return

    bank_name = args[0].upper()
    bank_account = args[1]
    bank_owner = " ".join(args[2:]).upper()

    set_setting("bank_name", bank_name)
    set_setting("bank_account", bank_account)
    set_setting("bank_owner", bank_owner)

    await update.message.reply_text(
        f"{vip_em('check', '✅')} <b>ĐÃ CẬP NHẬT THÔNG TIN THANH TOÁN VIETQR:</b>\n\n"
        f"{vip_em('bank', '🏦')} <b>Ngân hàng:</b> <code>{bank_name}</code>\n"
        f"{vip_em('card', '💳')} <b>STK:</b> <code>{bank_account}</code>\n"
        f"{vip_em('user', '👤')} <b>Chủ TK:</b> <code>{bank_owner}</code>",
        parse_mode="HTML"
    )

async def admin_set_usdt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user):
        await update.message.reply_text(f"{vip_em('cross', '⛔')} <b>Bạn không có quyền Admin!</b>", parse_mode="HTML")
        return

    args = context.args if (context.args is not None and len(context.args) > 0) else (update.message.text.split()[1:] if update.message and update.message.text else [])
    if not args or len(args) < 2:
        await update.message.reply_text(
            "⚠️ <b>Cú pháp:</b> <code>/setusdt &lt;mạng&gt; &lt;địa_chỉ_ví&gt; [giá_usdt]</code>\n"
            "Ví dụ: <code>/setusdt BEP20 0xWolfModVIPBEP20Payment88888888888888888888 1</code>",
            parse_mode="HTML"
        )
        return

    network = args[0].upper()
    wallet = args[1]
    price = args[2] if len(args) >= 3 else "1"

    set_setting("usdt_network", network)
    set_setting("usdt_wallet", wallet)
    set_setting("usdt_price", price)

    await update.message.reply_text(
        f"{vip_em('check', '✅')} <b>ĐÃ CẬP NHẬT THÔNG TIN THANH TOÁN USDT:</b>\n\n"
        f"{vip_em('globe', '🌐')} <b>Mạng lưới:</b> <code>{network}</code>\n"
        f"{vip_em('wallet', '👛')} <b>Địa chỉ ví nhận:</b> <code>{wallet}</code>\n"
        f"{vip_em('money', '💵')} <b>Giá bán USDT:</b> <code>{price} USDT</code>",
        parse_mode="HTML"
    )

async def admin_set_test_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user):
        await update.message.reply_text(f"{vip_em('cross', '⛔')} <b>Bạn không có quyền Admin!</b>", parse_mode="HTML")
        return

    args = context.args if (context.args is not None and len(context.args) > 0) else (update.message.text.split()[1:] if update.message and update.message.text else [])
    if not args or args[0].lower() not in ["on", "off"]:
        await update.message.reply_text(f"{vip_em('shield', '⚠️')} <b>Cú pháp:</b> <code>/testmode on</code> hoặc <code>/testmode off</code>", parse_mode="HTML")
        return

    val = "true" if args[0].lower() == "on" else "false"
    set_setting("test_mode", val)
    status_str = "🟢 ĐÃ BẬT" if val == "true" else "🔴 ĐÃ TẮT"
    await update.message.reply_text(f"{vip_em('crown', '🛠️')} <b>Chế độ Test Mode:</b> {status_str}", parse_mode="HTML")

# ==================== THÔNG BÁO NHẬP KHO (khách) ====================
STOCK_WATCH_SEC = 60
STOCK_NOTIFIER = stock_notify.StockNotifier()
_last_stock_seen: dict[str, int] = {}


def format_stock_added_notification(added_count: int, total_stock: int, sample_links: list = None,
                                    sku: str = DEFAULT_SKU, lang: str = "vi") -> str:
    """Thông báo nhập kho theo ngôn ngữ người nhận: tên đầy đủ + quyền lợi chính + giá + tồn kho.
    Tuyệt đối không in link/tài khoản (sample_links bị bỏ qua) và không in mã kỹ thuật (SKU)."""
    return stock_notify.build_message(
        lang, name=sku_title(sku, lang), features=product_content.features(sku, lang),
        price_vnd=sku_price_vnd(sku), stock=total_stock, added=added_count, em=vip_em)


def stock_notification_keyboard(sku: str, lang: str) -> InlineKeyboardMarkup:
    """Nút 'Mua ngay' mở thẳng trang chi tiết sản phẩm (cùng callback buy_<sku> như menu)."""
    return InlineKeyboardMarkup([[vip_btn(t(lang, "btn_buy_now"), callback_data=cb_buy(sku), emoji_name="crown", style="primary")]])


def get_stock_recipients() -> list:
    """Người nhận thông báo nhập kho: admin, kênh `notify_channel` (nếu đặt) và – chỉ khi bật setting
    `stock_notify_customers=true` – mọi khách đã từng dùng bot/đặt hàng. Mỗi người kèm ngôn ngữ của họ."""
    recipients: list = []
    try:
        admin_chat = int(ADMIN_ID)
        recipients.append((admin_chat, get_user_lang(admin_chat)))
    except (TypeError, ValueError):
        pass
    channel = get_setting("notify_channel", "").strip()
    if channel:
        recipients.append((channel, get_setting("notify_channel_lang", "vi")))
    if get_setting("stock_notify_customers", "false").lower() == "true":
        conn = get_db_connection()
        try:
            ids = {int(r[0]) for r in conn.execute("SELECT user_id FROM user_lang").fetchall()}
            ids |= {int(r[0]) for r in conn.execute("SELECT DISTINCT user_id FROM orders").fetchall()}
        finally:
            conn.close()
        recipients += [(uid, get_user_lang(uid)) for uid in sorted(ids)]
    return recipients


async def send_stock_notifications(bot, sku: str, added: int) -> int:
    """Gửi MỖI nhóm ngôn ngữ một bản thông báo (kèm nút Mua ngay). Trả về số tin gửi thành công."""
    stock = await asyncio.to_thread(get_stock_count, sku)
    sent = 0
    for lang, chat_ids in stock_notify.group_by_lang(get_stock_recipients()).items():
        text = format_stock_added_notification(added, stock, sku=sku, lang=lang)
        markup = stock_notification_keyboard(sku, lang)
        for chat_id in chat_ids:
            try:
                await bot.send_message(chat_id=chat_id, text=text, reply_markup=markup, parse_mode="HTML")
                sent += 1
            except Exception as error:  # noqa: BLE001 - một người chặn bot không được làm hỏng cả đợt
                logger.warning("Không gửi được thông báo nhập kho: %s", type(error).__name__)
    return sent


async def stock_watch_task(application: Application):
    """Mỗi ~60 giây so sánh tồn kho từng SKU; tăng thì ghi nhận, gộp các lần nhập trong 5 phút thành MỘT tin / sản phẩm."""
    while True:
        try:
            await asyncio.sleep(STOCK_WATCH_SEC)
            if get_setting("stock_notify_enabled", "true").lower() != "true":
                continue
            for sku in known_skus():
                count = await asyncio.to_thread(get_stock_count, sku)
                previous = _last_stock_seen.get(sku)
                if previous is not None and count > previous:
                    STOCK_NOTIFIER.record(sku, count - previous)
                _last_stock_seen[sku] = count
            for sku, added in STOCK_NOTIFIER.pop_due():
                await send_stock_notifications(application.bot, sku, added)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001
            logger.warning("stock_watch_task lỗi: %s", type(error).__name__)

async def watch_and_notify_new_links(app: Application):
    """ĐÃ TẮT HOÀN TOÀN THEO YÊU CẦU: Không tự ý quét file claimed_links.txt hay nạp link từ máy.
    Kho hàng chỉ được nạp và cập nhật qua Web Admin Dashboard."""
    return

async def admin_add_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user):
        await update.message.reply_text(f"{vip_em('cross', '⛔')} <b>Bạn không có quyền Admin!</b>", parse_mode="HTML")
        return

    text = update.message.text
    # Tìm tất cả link bắt đầu bằng http
    links = [l.strip() for l in re.findall(r'https?://[^\s]+', text) if l.strip()]
    if not links:
        await update.message.reply_text(f"{vip_em('shield', '⚠️')} <b>Vui lòng gửi kèm ít nhất một đường link để nạp vào kho!</b>\nVí dụ: <code>/addstock https://one.google.com/offer/...</code>", parse_mode="HTML")
        return

    # Lưu thêm vào file claimed_links.txt nếu chưa có
    try:
        existing_lines = []
        if os.path.exists(CLAIMED_LINKS_FILE):
            with open(CLAIMED_LINKS_FILE, "r", encoding="utf-8") as f_cl:
                existing_lines = [l.strip() for l in f_cl if l.strip()]
        existing_set = set(existing_lines)
        to_append = [l for l in links if l not in existing_set]
        if to_append:
            with open(CLAIMED_LINKS_FILE, "a", encoding="utf-8") as f_cl:
                for l in to_append:
                    f_cl.write(l + "\n")
    except Exception as e_file:
        logger.warning(f"Lỗi lưu file claimed_links.txt: {e_file}")

    conn = get_db_connection()
    added = 0
    for link in links:
        try:
            conn.execute(
                "INSERT INTO stock (product_id, content, status) VALUES ('gemini_plus', ?, 'available')",
                (link,)
            )
            added += 1
        except sqlite3.IntegrityError:
            pass
    conn.commit()
    conn.close()

    if added > 0:
        push_stock_to_web("gemini_plus", links)
    total_avail = get_stock_count("gemini_plus")
    if added > 0:
        # Ghi nhận để gộp vào thông báo khách (và tránh stock_watch_task đếm lại lần tăng này).
        STOCK_NOTIFIER.record("gemini_plus", added)
        _last_stock_seen["gemini_plus"] = total_avail
    reply_msg = format_stock_added_notification(added, total_avail, links, sku="gemini_plus", lang=get_user_lang(user.id))
    await update.message.reply_text(reply_msg, parse_mode="HTML")

async def admin_handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin có thể tải thẳng file .txt chứa danh sách link vào khung chat để nạp kho"""
    user = update.effective_user
    if not is_admin(user):
        return

    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith((".txt", ".log", ".csv")):
        return

    try:
        file = await context.bot.get_file(doc.file_id)
        content_bytes = await file.download_as_bytearray()
        text = content_bytes.decode("utf-8", errors="ignore")
        links = [l.strip() for l in re.findall(r'https?://[^\s]+', text) if l.strip()]
        if not links:
            await update.message.reply_text(f"{vip_em('shield', '⚠️')} <b>Không tìm thấy đường link hợp lệ nào trong file!</b>", parse_mode="HTML")
            return

        # Nối vào file claimed_links.txt để watcher tự xử lý và thông báo
        existing_lines = []
        if os.path.exists(CLAIMED_LINKS_FILE):
            with open(CLAIMED_LINKS_FILE, "r", encoding="utf-8") as f_cl:
                existing_lines = [l.strip() for l in f_cl if l.strip()]
        existing_set = set(existing_lines)
        to_append = [l for l in links if l not in existing_set]
        if to_append:
            with open(CLAIMED_LINKS_FILE, "a", encoding="utf-8") as f_cl:
                for l in to_append:
                    f_cl.write(l + "\n")

        await update.message.reply_text(f"{vip_em('check', '✅')} <b>Đã nhận file <code>{doc.file_name}</code> chứa {len(links)} link! Hệ thống đang nạp kho...</b>", parse_mode="HTML")
    except Exception as e:
        logger.error(f"Lỗi đọc file từ Telegram: {e}")
        await update.message.reply_text(f"{vip_em('cross', '❌')} <b>Lỗi xử lý file:</b> <code>{e}</code>", parse_mode="HTML")

async def admin_set_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not is_admin(user):
        return
    args = context.args
    if not args:
        cur_chan = get_setting("notify_channel", "Chưa cài đặt (Mặc định gửi Admin)")
        await update.message.reply_text(
            f"📢 <b>Kênh thông báo kho hiện tại:</b> <code>{cur_chan}</code>\n\n"
            f"💡 <b>Cú pháp cài đặt:</b> <code>/setchannel &lt;@channel_name hoặc id&gt;</code>\n"
            f"Ví dụ: <code>/setchannel @wolfmodyt_channel</code> hoặc <code>/setchannel -1001234567890</code>",
            parse_mode="HTML"
        )
        return
    channel = args[0].strip()
    set_setting("notify_channel", channel)
    await update.message.reply_text(f"{vip_em('check', '✅')} <b>Đã cài đặt kênh nhận thông báo kho mới:</b> <code>{channel}</code>", parse_mode="HTML")

async def handle_user_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Xử lý tin nhắn văn bản tự do của người dùng (ví dụ: nhập số lượng link tùy chỉnh)"""
    user = update.effective_user
    if not user or not update.message or not update.message.text:
        return

    user_id = user.id
    raw_text = update.message.text.strip()

    # Bỏ qua nếu là lệnh bắt đầu bằng / hoặc .
    if raw_text.startswith(("/", ".")):
        return

    # Đang chờ ID lệnh Binance Pay của khách (chỉ khi tính năng bật)
    if binance_pay_enabled() and user_id in AWAITING_BINANCE_ID and await handle_binance_id_message(update, context):
        return

    # Đang chờ số tiền nạp ví tuỳ chọn
    if user_id in AWAITING_TOPUP_AMOUNT:
        lang = get_user_lang(user_id)
        amount = wallet_topup.parse_amount(raw_text)
        if amount is None:
            await update.message.reply_text(f"{vip_em('cross', '❌')} {t(lang, 'topup_not_number')}", parse_mode="HTML")
            return
        AWAITING_TOPUP_AMOUNT.discard(user_id)
        if not topup_enabled() or not _is_private_chat(update.message):
            await update.message.reply_text(f"{vip_em('shield', '🛡')} {t(lang, 'wallet_err_unavailable')}", parse_mode="HTML")
            return
        await send_topup_invoice(update.message, user, amount, lang)
        return

    # Nếu người dùng đang trong luồng nhập số lượng tùy chỉnh
    if user_id in AWAITING_QTY_USERS:
        sku = AWAITING_QTY_USERS[user_id]
        lang = get_user_lang(user_id)
        if not raw_text.isdigit():
            await update.message.reply_text(
                f"{vip_em('cross', '❌')} {t(lang, 'qty_not_number')}",
                parse_mode="HTML"
            )
            return

        qty = int(raw_text)
        if qty <= 0:
            await update.message.reply_text(
                f"{vip_em('cross', '❌')} {t(lang, 'qty_min')}",
                parse_mode="HTML"
            )
            return

        qty_limit = sku_max_qty(sku)
        if qty > qty_limit:
            await update.message.reply_text(
                f"{vip_em('shield', '⚠️')} {t(lang, 'qty_max', limit=qty_limit)}",
                parse_mode="HTML"
            )
            return

        stock = get_stock_count(sku)
        if stock < qty:
            await update.message.reply_text(
                f"{vip_em('box', '📦')} {t(lang, 'qty_stock_short', stock=stock)}",
                parse_mode="HTML"
            )
            return

        # Xóa trạng thái chờ và hiển thị bảng xác nhận đơn
        del AWAITING_QTY_USERS[user_id]
        await show_order_confirmation(update.message, user, sku=sku, qty=qty)

async def auto_deliver_paid_orders_task(application: Application):
    """Recover paid orders, including cancelled local orders and interrupted sends."""
    while True:
        try:
            conn = get_db_connection()
            rows = conn.execute(
                """SELECT order_id, user_id, status, payment_method, kind FROM orders
                   WHERE delivery_notified_at IS NULL
                     AND status IN ('pending', 'cancelled', 'expired', 'completed')
                     AND created_at >= datetime('now', '-7 days')
                     AND (kind <> 'wallet_topup' OR created_at >= datetime('now', '-2 days'))
                     AND (payment_method IS NULL OR payment_method <> 'wallet' OR status = 'completed')
                   ORDER BY created_at DESC LIMIT 100"""
            ).fetchall()
            conn.close()
            for row in rows:
                # The web status endpoint has a 10 requests/minute limit.
                await asyncio.sleep(8)
                if row["kind"] == "wallet_topup":
                    # Nạp ví: không bao giờ giao hàng/kho; chỉ cộng ví qua settle (kể cả đơn đã hủy/hết hạn mà web báo paid).
                    stat = await asyncio.to_thread(check_web_order_status, row["order_id"])
                    await settle_and_notify_topup(application.bot, row["order_id"], row["user_id"], stat)
                    continue
                if row["status"] == "completed":
                    await send_paid_order_delivery(application.bot, row["order_id"], row["user_id"])
                    continue
                if row["status"] == "pending" and row["payment_method"] == "bep20":
                    await asyncio.to_thread(check_web_bep20, row["order_id"], row["user_id"])
                status = await asyncio.to_thread(check_web_order_status, row["order_id"])
                if status.get("status") in ("paid", "delivered"):
                    await send_paid_order_delivery(application.bot, row["order_id"], row["user_id"])
        except Exception as error:
            logger.error("Lỗi kiểm tra giao hàng tự động: %s", type(error).__name__)
        await asyncio.sleep(15)


async def wallet_pay_sweep_task(application: Application):
    """Quét đơn trả bằng ví đang chờ giao (chạy ngay khi khởi động rồi mỗi phút): xử lý lại idempotent sau restart/lỗi mạng,
    và hoàn tiền + báo admin khi quá 15 phút vẫn không giao được."""
    while True:
        try:
            if wallet_pay_enabled():
                for order_id in await asyncio.to_thread(_wallet_processing_ids):
                    await run_wallet_order(application.bot, order_id)
        except Exception as error:
            logger.error("Quét đơn trả bằng ví lỗi: %s", type(error).__name__)
        await asyncio.sleep(WALLET_SWEEP_SECONDS)


async def auto_cancel_expired_orders_task(application: Application):
    """Vòng lặp tự động kiểm tra mỗi 15 giây: nếu đơn hàng pending quá 10 phút thì hủy đơn và gửi thông báo cho khách"""
    while True:
        try:
            await asyncio.sleep(15)
            now_dt = datetime.now()
            now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

            conn = get_db_connection()
            expired_orders = conn.execute("""
                SELECT * FROM orders 
                WHERE status = 'pending' 
                AND (
                    (expires_at IS NOT NULL AND expires_at < ?)
                    OR (expires_at IS NULL AND (strftime('%s', ?) - strftime('%s', created_at)) > 600)
                )
            """, (now_str, now_str)).fetchall()

            for ord_row in expired_orders:
                order_id = ord_row["order_id"]
                user_id = ord_row["user_id"]
                web_stat = await asyncio.to_thread(check_web_order_status, order_id)
                if web_stat.get("status") in ("paid", "delivered"):
                    logger.info("Giữ đơn %s đã được web xác nhận thanh toán, không tự hủy.", order_id)
                    if ord_row["kind"] == "wallet_topup":
                        await settle_and_notify_topup(application.bot, order_id, user_id, web_stat)
                    continue
                conn.execute("UPDATE orders SET status = 'cancelled' WHERE order_id = ? AND status = 'pending'", (order_id,))
                conn.commit()
                logger.info(f"Đã tự động hủy đơn hết hạn 10 phút: {order_id} (User: {user_id})")
                if ord_row["kind"] == "wallet_topup":
                    try:
                        ulang = get_user_lang(user_id)
                        await application.bot.send_message(
                            chat_id=int(user_id), text=f"{vip_em('cross', '❌')} {t(ulang, 'topup_expired', order_id=html.escape(str(order_id)))}",
                            reply_markup=InlineKeyboardMarkup([[vip_btn(t(ulang, "btn_back_main"), callback_data="wallet", emoji_name="back")]]),
                            parse_mode="HTML")
                    except Exception as e_notify:
                        logger.warning("Không gửi được thông báo hủy đơn nạp %s: %s", order_id, type(e_notify).__name__)
                    continue

                # Gửi thông báo đến khách hàng trên Telegram
                try:
                    ulang = get_user_lang(user_id)
                    cancel_msg = (
                        f"{vip_em('cross', '❌')} {t(ulang, 'expired_auto', order_id=html.escape(str(order_id)))}"
                    )
                    kb = [
                        [vip_btn(t(ulang, "btn_new_order"), callback_data=cb_buy(order_sku(ord_row)), emoji_name="crown", style="primary")],
                        [vip_btn(t(ulang, "btn_back_menu"), callback_data="main_menu", emoji_name="back")]
                    ]
                    await application.bot.send_message(
                        chat_id=int(user_id),
                        text=cancel_msg,
                        reply_markup=InlineKeyboardMarkup(kb),
                        parse_mode="HTML"
                    )
                except Exception as e_notify:
                    logger.warning(f"Không thể gửi thông báo hủy đơn {order_id} cho user {user_id}: {e_notify}")

            conn.close()
        except Exception as e:
            logger.error(f"Lỗi trong auto_cancel_expired_orders_task: {e}")
            await asyncio.sleep(5)

# ==================== MAIN APPLICATION ====================

def run_shop_bot(token=None):
    use_token = token or BOT_TOKEN
    if not use_token:
        print("[X] Chưa cấu hình BOT_TOKEN! Vui lòng cung cấp token hoặc thiết lập trong .env")
        return

    init_database()
    sync_all_pending_orders_on_startup()

    # Tạo và thiết lập event loop tương thích Python 3.12+ / 3.14+
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    async def post_init_hook(application: Application):
        # asyncio.create_task(watch_and_notify_new_links(application)) # TẮT HOÀN TOÀN THEO YÊU CẦU USER
        conn = get_db_connection()
        awaiting = conn.execute("SELECT COUNT(*) AS n FROM orders WHERE status = 'completed' AND delivery_notified_at IS NULL").fetchone()["n"]
        conn.close()
        logger.info("Số đơn đã cấp link đang chờ gửi Telegram: %s", awaiting)
        asyncio.create_task(auto_cancel_expired_orders_task(application))
        asyncio.create_task(auto_deliver_paid_orders_task(application))
        asyncio.create_task(wallet_backup_task(application))
        asyncio.create_task(wallet_pay_sweep_task(application))
        asyncio.create_task(stock_watch_task(application))

    app = Application.builder().token(use_token).post_init(post_init_hook).build()

    # User commands (hỗ trợ cả /start, .start, /menu, .menu, /help, .help)
    app.add_handler(CommandHandler(["start", "menu", "help"], start_command))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^[\/\.](start|menu|help|shop|store)\b"), start_command))
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Admin commands (hỗ trợ cả /command và .command)
    app.add_handler(CommandHandler("setprice", admin_set_price))
    app.add_handler(CommandHandler("setbank", admin_set_bank))
    app.add_handler(CommandHandler("setusdt", admin_set_usdt))
    app.add_handler(CommandHandler("testmode", admin_set_test_mode))
    app.add_handler(CommandHandler("addstock", admin_add_stock))
    app.add_handler(CommandHandler("setchannel", admin_set_channel))
    app.add_handler(CommandHandler("wallet", admin_wallet_command))
    app.add_handler(CommandHandler("wallet_add", admin_wallet_add))
    app.add_handler(CommandHandler("wallet_sub", admin_wallet_sub))
    app.add_handler(CommandHandler("backup", admin_backup_command))

    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^\.setprice\b"), admin_set_price))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^\.setbank\b"), admin_set_bank))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^\.setusdt\b"), admin_set_usdt))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^\.testmode\b"), admin_set_test_mode))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^\.addstock\b"), admin_add_stock))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^\.setchannel\b"), admin_set_channel))
    app.add_handler(MessageHandler(filters.Document.ALL, admin_handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_user_text_message))

    print("🤖 SHOP TELEGRAM BOT ĐANG CHẠY...")
    print(f"📦 Số link Gemini Plus có sẵn trong kho: {get_stock_count('gemini_plus')}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    run_shop_bot()
