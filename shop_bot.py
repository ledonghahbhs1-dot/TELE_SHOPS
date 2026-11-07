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
import product_guides
import product_intros
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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    """Khởi tạo các bảng SQLite và nạp dữ liệu mặc định"""
    conn = get_db_connection()
    cursor = conn.cursor()

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

    # 4. Bảng cấu hình hệ thống (Settings)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
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
    try:
        return float(get_setting(usdt_setting_key(sku), "1"))
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

def get_product_banner_path(sku: str) -> str | None:
    """Trả về đường dẫn file ảnh banner của sản phẩm nếu tồn tại (mỗi dịch vụ có 1 ảnh riêng)"""
    custom_map = {
        "gemini_plus": os.path.join(BASE_DIR, "assets", "gemini_plus.jpg")
    }
    path = custom_map.get(sku) or os.path.join(BASE_DIR, "assets", f"{sku}.jpg")
    if os.path.exists(path):
        return path
    return None

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

def deliver_product(order_id, user_id):
    """Xuất link từ kho và gán cho đơn hàng theo đúng số lượng mua"""
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

    qty = 1
    if "quantity" in order.keys() and order["quantity"]:
        try:
            qty = max(1, int(order["quantity"]))
        except Exception:
            qty = 1

    # The web claims the complete quantity atomically and returns the same bundle on retries.
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
    WHERE order_id = ?
    """, (all_links_text, now, order_id))

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
    if kind not in ("bep20", "binance") and sku == DEFAULT_SKU:
        return {"select": f"select_qty_{qty}", "vietqr": f"create_order_vietqr_{qty}", "usdt": f"create_order_usdt_{qty}"}[kind]
    prefix = {"select": "sq", "vietqr": "cv", "usdt": "cu", "bep20": "cb", "binance": "cp"}[kind]
    return f"{prefix}:{sku}:{qty}"


_SKU_CB_RE = re.compile(r"(sq|cv|cu|cb|cp):([A-Za-z0-9][A-Za-z0-9_]{1,39}):(\d{1,3})")


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

# Trạng thái chờ người dùng nhập số lượng tuỳ chỉnh: user_id -> sku
AWAITING_QTY_USERS: dict[int, str] = {}
DELIVERY_LOCK = asyncio.Lock()

def format_delivered_links_message(order_id: str, delivered_links: list[str], sku: str = DEFAULT_SKU) -> tuple[str, list]:
    """Định dạng tin nhắn và nút mở link sau khi thanh toán thành công"""
    if sku != DEFAULT_SKU:
        # Tài khoản/nội dung không phải URL: không tạo nút url (Telegram sẽ từ chối), chỉ hiển thị dạng code.
        items_text = "\n".join(f"<code>{html.escape(item)}</code>" for item in delivered_links)
        msg = (
            f"{vip_em('party', '🎉')} <b>THANH TOÁN THÀNH CÔNG ĐƠN HÀNG <code>{html.escape(order_id)}</code>!</b>\n\n"
            f"{vip_em('check', '✅')} Hệ thống đã xác nhận thanh toán của bạn.\n"
            f"{vip_em('box', '📦')} <b>Sản phẩm:</b> {html.escape(sku_display_name(sku))}\n"
            f"{vip_em('key', '🔗')} <b>Thông tin nhận hàng ({len(delivered_links)}):</b>\n{items_text}\n\n"
            f"{vip_em('crown', '👑')} Cảm ơn Quý Khách đã tin tưởng dịch vụ VIP của WolfMod! Cần hỗ trợ vui lòng liên hệ @wolfmodyt."
        )
        buttons = [
            [vip_btn("📦 Đơn Hàng Của Tôi", callback_data="my_orders", emoji_name="box")],
            [vip_btn("🔙 Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")],
        ]
        return msg, buttons

    if len(delivered_links) == 1:
        single_link = delivered_links[0]
        msg = (
            f"{vip_em('party', '🎉')} <b>THANH TOÁN THÀNH CÔNG ĐƠN HÀNG <code>{order_id}</code>!</b>\n\n"
            f"{vip_em('check', '✅')} Hệ thống đã xác nhận thanh toán của bạn.\n"
            f"{vip_em('key', '🔗')} <b>Link nhận Google AI Plus 400GB (12 Tháng - BH 24h):</b>\n<code>{html.escape(single_link)}</code>\n\n"
            f"{vip_em('diamond', '💎')} <b>Hướng dẫn kích hoạt 3 bước:</b>\n"
            f"{vip_em('num1', '1️⃣')} Mở link trên trình duyệt có Gmail cá nhân của bạn.\n"
            f"{vip_em('num2', '2️⃣')} Bấm 'Bắt đầu dùng thử' hoặc 'Nhận ưu đãi' 2TB.\n"
            f"{vip_em('num3', '3️⃣')} Hoàn tất xác nhận trên Google One.\n\n"
            f"{vip_em('crown', '👑')} Cảm ơn Quý Khách đã tin tưởng dịch vụ VIP của WolfMod!"
        )
        buttons = [
            [vip_btn("👑 Mở Link Kích Hoạt VIP 👑", url=single_link, emoji_name="crown", style="primary")],
            [vip_btn("📦 Đơn Hàng Của Tôi", callback_data="my_orders", emoji_name="box")],
            [vip_btn("🔙 Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")]
        ]
        return msg, buttons

    else:
        qty = len(delivered_links)
        links_text = ""
        buttons = []
        for idx, lk in enumerate(delivered_links, 1):
            links_text += f"{vip_em('star', '✦')} <b>Link #{idx}:</b> <code>{html.escape(lk)}</code>\n"
            buttons.append([vip_btn(f"👑 Mở Link #{idx} 👑", url=lk, emoji_name="crown", style="primary")])

        msg = (
            f"{vip_em('party', '🎉')} <b>THANH TOÁN THÀNH CÔNG ĐƠN HÀNG <code>{order_id}</code>!</b>\n\n"
            f"{vip_em('check', '✅')} Hệ thống đã xác nhận thanh toán của bạn.\n"
            f"{vip_em('box', '📦')} <b>Số lượng:</b> <code>{qty} link</code> Google AI Plus 400GB (12 Tháng - BH 24h)\n\n"
            f"{vip_em('key', '🔗')} <b>DANH SÁCH {qty} LINK KÍCH HOẠT:</b>\n"
            f"{links_text}\n"
            f"{vip_em('diamond', '💎')} <b>Hướng dẫn kích hoạt 3 bước:</b>\n"
            f"{vip_em('num1', '1️⃣')} Mở từng link trên trình duyệt có Gmail tương ứng.\n"
            f"{vip_em('num2', '2️⃣')} Bấm 'Bắt đầu dùng thử' hoặc 'Nhận ưu đãi' 2TB.\n"
            f"{vip_em('num3', '3️⃣')} Hoàn tất xác nhận trên Google One.\n\n"
            f"{vip_em('crown', '👑')} Cảm ơn Quý Khách đã tin tưởng dịch vụ VIP của WolfMod!"
        )
        buttons.append([vip_btn("📦 Đơn Hàng Của Tôi", callback_data="my_orders", emoji_name="box")])
        buttons.append([vip_btn("🔙 Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")])
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
        body, buttons = format_delivered_links_message(order_id, links, str(row["product_id"]))
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
    await asyncio.to_thread(sync_web_product_price, sku)
    prod = get_product(sku)
    stock = get_stock_count(sku)
    price_unit = prod["price"]
    total_vnd = price_unit * qty
    price_unit_fmt = f"{price_unit:,} VNĐ"
    total_vnd_fmt = f"{total_vnd:,} VNĐ"

    usdt_unit = get_usdt_price(sku)
    total_usdt = round(usdt_unit * qty, 2)
    total_usdt_str = str(int(total_usdt)) if total_usdt.is_integer() else str(total_usdt)

    if sku == DEFAULT_SKU:
        text = (
            f"{vip_em('crown', '👑')} ═══════ {vip_em('diamond', '💎')} <b>XÁC NHẬN ĐƠN HÀNG VIP</b> {vip_em('diamond', '💎')} ═══════ {vip_em('crown', '👑')}\n\n"
            f"{vip_em('tag', '🌟')} <b>Gói Đang Chọn:</b> <code>Google AI Plus 400GB (12 Tháng)</code>\n"
            f"{vip_em('box', '📦')} <b>Số lượng đặt mua:</b> <b>{qty} link</b>\n"
            f"{vip_em('chart', '🏷')} <b>Đơn giá ưu đãi:</b> <code>{price_unit_fmt}</code> / link (hoặc <code>{usdt_unit} USDT</code>)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{vip_em('money', '💰')} <b>TỔNG TIỀN THANH TOÁN:</b> <b>{total_vnd_fmt}</b> (hoặc <b>{total_usdt_str} USDT</b>)\n"
            f"{vip_em('calendar', '⏳')} <b>Thời hạn sử dụng:</b> 12 Tháng / link (Bảo hành 24h 1 đổi 1)\n\n"
            f"{vip_em('lightning', '⚡️')} <i>Vui lòng chọn phương thức thanh toán bên dưới để nhận mã QR kích hoạt tự động:</i>"
        )
    else:
        text = (
            f"{vip_em('crown', '👑')} ═══════ {vip_em('diamond', '💎')} <b>XÁC NHẬN ĐƠN HÀNG VIP</b> {vip_em('diamond', '💎')} ═══════ {vip_em('crown', '👑')}\n\n"
            f"{vip_em('tag', '🌟')} <b>Sản phẩm:</b> <code>{html.escape(str(prod['name']))}</code>\n"
            f"{vip_em('box', '📦')} <b>Số lượng đặt mua:</b> <b>{qty}</b>\n"
            f"{vip_em('chart', '🏷')} <b>Đơn giá:</b> <code>{price_unit_fmt}</code> (hoặc <code>{usdt_unit} USDT</code>)\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{vip_em('money', '💰')} <b>TỔNG TIỀN THANH TOÁN:</b> <b>{total_vnd_fmt}</b> (hoặc <b>{total_usdt_str} USDT</b>)\n\n"
            f"{vip_em('lightning', '⚡️')} <i>Vui lòng chọn phương thức thanh toán bên dưới. Thông tin nhận hàng được gửi tự động khi thanh toán được xác nhận:</i>"
        )

    keyboard = [
        [vip_btn(f" Thanh Toán VietQR ({total_vnd_fmt})", callback_data=cb_qty("vietqr", sku, qty), emoji_name="card", style="primary")],
        [vip_btn(f" Thanh Toán USDT ({total_usdt_str} USDT)", callback_data=cb_qty("usdt", sku, qty), emoji_name="usdt", style="success")],
        [vip_btn("🔄 Chọn Lại Số Lượng", callback_data=cb_buy(sku), emoji_name="refresh")],
        [vip_btn("🔙 Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")]
    ]
    if binance_pay_enabled():
        # Tùy chọn thêm (mặc định TẮT): USDT qua Binance Pay, khách trả đúng giá rồi dán ID lệnh.
        keyboard.insert(2, [vip_btn(" USDT — Binance Pay (dán ID lệnh)", callback_data=cb_qty("binance", sku, qty), emoji_name="usdt")])
    if bep20_enabled():
        # Tùy chọn thêm (mặc định TẮT): USDT BEP20 qua QR địa chỉ ví. Số tiền chính xác (có hậu tố duy nhất) hiện ở hóa đơn.
        keyboard.insert(2, [vip_btn(" USDT BEP20 — Quét QR ví", callback_data=cb_qty("bep20", sku, qty), emoji_name="usdt")])

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
        pending = conn.execute("SELECT * FROM orders WHERE status = 'pending'").fetchall()
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
NO_GUIDE_TEXT = "Sản phẩm này chưa có hướng dẫn riêng. Nếu cần hỗ trợ, vui lòng liên hệ CSKH kèm mã đơn hàng."


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


def render_guide(sku: str):
    """Hướng dẫn RIÊNG của sản phẩm, hoặc None nếu sản phẩm chưa có (không bao giờ mượn nội dung SKU khác)."""
    guide = product_guides.get_guide(sku)
    if guide is None:
        return None
    lines = [
        f"{vip_em('star', '⚜️')} ═══════ {vip_em('book', '📖')} <b>{guide['title']}</b> {vip_em('book', '📖')} ═══════ {vip_em('star', '⚜️')}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n",
        f"{vip_em('diamond', '💎')} <b>{guide['intro']}</b>\n\n",
    ]
    for number, step in enumerate(guide["steps"], 1):
        marker = vip_em(f"num{number}", f"{number}️⃣") if number <= 3 else f"{number}."
        lines.append(f"{marker} <b>Bước {number}:</b> {step}\n\n")
    lines.append(f"{vip_em('star', '💡')} <b>{guide['notes_title']}</b>\n")
    for emoji_name, fallback, text in guide["notes"]:
        lines.append(f"{vip_em(emoji_name, fallback)} {text}\n")
    lines.append(f"\n{vip_em('shield', '🛡')} <i>Đội ngũ kỹ thuật WolfMod luôn sẵn sàng đồng hành hỗ trợ Quý Khách 24/7!</i>")
    return "".join(lines)


def render_orders_page(user_id: int, page: int):
    """(text, keyboard) — đơn đã giao của CHÍNH khách này, mỗi đơn ghi đúng tên sản phẩm theo SKU của đơn."""
    conn = get_db_connection()
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM orders WHERE user_id = ? AND status = 'completed'", (user_id,)).fetchone()["n"]
        pages = max(1, (total + ORDERS_PAGE_SIZE - 1) // ORDERS_PAGE_SIZE)
        page = min(max(1, page), pages)
        rows = conn.execute(
            "SELECT * FROM orders WHERE user_id = ? AND status = 'completed' "
            "ORDER BY completed_at DESC, created_at DESC, order_id DESC LIMIT ? OFFSET ?",
            (user_id, ORDERS_PAGE_SIZE, (page - 1) * ORDERS_PAGE_SIZE),
        ).fetchall()
    finally:
        conn.close()
    text = f"{vip_em('diamond')} ═══════ {vip_em('crown')} <b>ĐƠN HÀNG VIP ĐÃ MUA</b> {vip_em('crown')} ═══════ {vip_em('diamond')}\n\n"
    keyboard = []
    if not rows:
        text += f"{vip_em('box')} <b>Bạn chưa có đơn hàng nào đã giao.</b>"
    seen_guides: list[str] = []
    # Telegram: tối đa 4096 ký tự sau khi bỏ thẻ HTML. Chia đều ngân sách cho phần nội dung của các đơn trên trang.
    def _display_name(row) -> str:
        return 'Google AI Plus 400GB (12 Tháng)' if str(row["product_id"]) == DEFAULT_SKU else sku_display_name(str(row["product_id"]))
    fixed_chars = sum(len(_display_name(row)) + len(str(row["order_id"])) + 200 for row in rows)
    preview_limit = max(60, min(ORDER_CONTENT_PREVIEW, (3600 - fixed_chars) // max(1, len(rows))))
    for row in rows:
        sku = str(row["product_id"])
        name = _display_name(row)
        label = "Link kích hoạt" if sku == DEFAULT_SKU else "Thông tin nhận hàng"
        content_lines = str(row["item_delivered"] or "").splitlines()
        content = "\n".join(content_lines)
        if len(content) > preview_limit:
            content = content[:preview_limit].rsplit("\n", 1)[0]
            content += f"\n… (rút gọn, {len(content_lines)} dòng trong đơn)"
        text += (
            f"{vip_em('tag')} <b>Đơn hàng:</b> <code>{html.escape(str(row['order_id']))}</code>\n"
            f"{vip_em('box')} <b>Sản phẩm:</b> {html.escape(name)}\n"
            f"{vip_em('calendar')} <b>Thời gian:</b> <code>{html.escape(str(row['completed_at'] or row['created_at'] or ''))}</code>\n"
            f"{vip_em('check')} <b>Trạng thái:</b> Đã giao\n"
            f"{vip_em('key')} <b>{label}:</b>\n<code>{html.escape(content)}</code>\n"
            "──────────────────────────\n"
        )
        if has_own_guide(sku) and sku not in seen_guides:
            seen_guides.append(sku)
            keyboard.append([vip_btn(f"Hướng dẫn: {name}"[:48], callback_data=f"gd:{sku}", emoji_name="book")])
    if pages > 1:
        text += f"\n<i>Trang {page}/{pages} • {total} đơn</i>"
        nav = []
        if page > 1:
            nav.append(vip_btn("◀ Trang trước", callback_data=f"ordp:{page - 1}", emoji_name="back"))
        if page < pages:
            nav.append(vip_btn("Trang sau ▶", callback_data=f"ordp:{page + 1}", emoji_name="refresh"))
        keyboard.append(nav)
    keyboard.append([vip_btn("Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")])
    return text, InlineKeyboardMarkup(keyboard)


def welcome_message(user) -> str:
    """Màn chính NGẮN: chỉ lời chào thương hiệu. Danh sách sản phẩm là các nút; giới thiệu dài nằm ở màn từng sản phẩm."""
    safe_name = html.escape(user.first_name) if user and getattr(user, "first_name", None) else "Quý Khách"
    return (
        f"{vip_em('crown', '👑')} ═══════ {vip_em('diamond', '💎')} <b>WOLFMOD VIP STORE</b> {vip_em('diamond', '💎')} ═══════ {vip_em('crown', '👑')}\n\n"
        f"{vip_em('sparkle', '✨')} <i>Kính chào Quý Khách VIP</i> <b>{safe_name}</b> <i>đến với hệ thống dịch vụ số cao cấp!</i>\n\n"
        f"{vip_em('star', '👇')} <i>Chọn sản phẩm bên dưới để xem chi tiết:</i>"
    )


def _plain_len(text: str) -> int:
    """Độ dài sau khi bỏ thẻ HTML (cách Telegram đếm giới hạn caption 1024 / tin nhắn 4096)."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", text)))


def render_product_intro(sku: str, stock: int) -> str:
    """Màn giới thiệu RIÊNG của sản phẩm (giá, mô tả, tồn kho, số lượng tối đa); hết hàng thì ghi rõ và không mời mua."""
    prod = get_product(sku)
    price = int(prod["price"] or 0) if prod else 0
    usdt_price = get_setting(usdt_setting_key(sku), "1")
    if product_intros.has_own_intro(sku):
        text = product_intros.INTROS[sku](vip_em, price, usdt_price)
    else:
        text = product_intros.generic_intro(vip_em, str(prod["name"]), str(prod["description"] or ""), price, usdt_price)
    if stock <= 0:
        return text + f"{vip_em('cross', '❌')} <b>Hiện sản phẩm đang HẾT HÀNG.</b> Vui lòng quay lại sau hoặc liên hệ CSKH VIP."
    if sku == DEFAULT_SKU:
        return (text + f"{vip_em('box', '📦')} <b>Tồn kho sẵn sàng:</b> <code>{stock}</code> link kích hoạt chính chủ\n\n"
                f"{vip_em('lightning', '⚡️')} <i>Vui lòng bấm chọn số lượng link bạn muốn mua bên dưới:</i>")
    return (text + f"{vip_em('box', '📦')} <b>Tồn kho sẵn sàng:</b> <code>{stock}</code>\n"
            f"{vip_em('shield', '🛡')} <b>Tối đa mỗi đơn:</b> {sku_max_qty(sku)}\n\n"
            f"{vip_em('lightning', '⚡️')} <i>Bấm bên dưới để tiếp tục:</i>")


async def send_product_screen(query, sku: str, text: str, keyboard) -> None:
    """Hiện màn sản phẩm: ảnh banner nếu có. Caption ảnh tối đa 1024 ký tự: nếu giới thiệu dài hơn thì gửi ảnh với
    caption ngắn (tên sản phẩm) rồi gửi giới thiệu thành tin nhắn riêng kèm nút."""
    markup = InlineKeyboardMarkup(keyboard)
    banner_path = get_product_banner_path(sku)
    if banner_path and os.path.exists(banner_path) and query.message:
        try:
            with open(banner_path, "rb") as f_img:
                bio = io.BytesIO(f_img.read())
            bio.name = f"{sku}.jpg"
            try:
                await query.message.delete()
            except Exception:
                pass
            if _plain_len(text) <= 1000:
                await query.message.reply_photo(photo=bio, caption=text, reply_markup=markup, parse_mode="HTML")
            else:
                await query.message.reply_photo(photo=bio, caption=f"<b>{html.escape(sku_display_name(sku))}</b>", parse_mode="HTML")
                await query.message.reply_text(text, reply_markup=markup, parse_mode="HTML", disable_web_page_preview=True)
            return
        except Exception as e_banner:
            logger.warning(f"Lỗi gửi banner {sku}: {e_banner}")
    await safe_edit_or_reply(query, text, reply_markup=markup, parse_mode="HTML")


def main_menu_keyboard(user_id=None):
    stock_count = get_stock_count("gemini_plus")
    price = get_product("gemini_plus")["price"]

    plus_label = (f"MUA GOOGLE AI PLUS 12 THÁNG • {price:,} VNĐ (Kho: {stock_count})" if stock_count > 0
                  else f"GOOGLE AI PLUS 12 THÁNG • {price:,} VNĐ • HẾT HÀNG")
    keyboard = [
        [
            vip_btn(plus_label, callback_data="buy_gemini_plus", emoji_name="crown", style="primary")
        ],
    ]
    # Sản phẩm khác: luôn được liệt kê; hết hàng thì nút không cho mua.
    for sku in known_skus():
        if sku == DEFAULT_SKU:
            continue
        row = get_product(sku)
        if not row or int(row["price"] or 0) <= 0:
            continue
        label = str(row["name"])[:32]
        if sku_in_stock_hint(sku):
            keyboard.append([vip_btn(f"MUA {label} • {int(row['price']):,} VNĐ", callback_data=cb_buy(sku), emoji_name="crown", style="primary")])
        else:
            keyboard.append([vip_btn(f"{label} • HẾT HÀNG", callback_data=f"soldout_{sku}", emoji_name="cross")])
    keyboard += [
        [
            vip_btn("Đơn Hàng VIP Của Tôi", callback_data="my_orders", emoji_name="diamond")
        ],
        [
            vip_btn("CSKH Trực Tuyến 24/7", callback_data="support", emoji_name="chat")
        ]
    ]

    return InlineKeyboardMarkup(keyboard)

# ==================== HANDLERS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.to_thread(refresh_catalogue)
    await asyncio.to_thread(sync_web_product_price)
    user = update.effective_user
    if user:
        AWAITING_QTY_USERS.pop(user.id, None)
    welcome_text = welcome_message(user)
    if update.message:
        await update.message.reply_text(
            welcome_text,
            reply_markup=main_menu_keyboard(user.id if user else 0),
            parse_mode="HTML"
        )
    elif update.callback_query:
        await safe_edit_or_reply(
            update.callback_query,
            welcome_text,
            reply_markup=main_menu_keyboard(user.id if user else 0),
            parse_mode="HTML"
        )

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


def render_bep20_invoice(order_id: str, info: dict, qty: int, sku: str) -> str:
    return (
        f"{vip_em('crown')} ═══════ {vip_em('diamond')} <b>HÓA ĐƠN USDT BEP20</b> {vip_em('diamond')} ═══════ {vip_em('crown')}\n\n"
        f"{vip_em('tag')} <b>Mã đơn:</b> <code>{html.escape(order_id)}</code>\n"
        f"{vip_em('box')} <b>Sản phẩm:</b> {html.escape(sku_display_name(sku))} × {qty}\n"
        f"{vip_em('globe')} <b>Mạng:</b> BNB Smart Chain (BEP20) — token <b>USDT</b>\n"
        f"{vip_em('money')} <b>Số tiền CHÍNH XÁC:</b> <code>{info['amount_usdt']}</code> USDT\n"
        f"{vip_em('card')} <b>Địa chỉ ví nhận:</b>\n<code>{info['address']}</code>\n"
        f"{vip_em('calendar')} <b>Hạn thanh toán:</b> <code>{info['expires_at']:%H:%M:%S}</code>\n\n"
        f"{vip_em('shield', '⚠️')} <i>Chỉ chuyển USDT trên mạng BEP20 (BSC), đúng từng chữ số ở trên (không làm tròn). "
        f"Chuyển sai mạng, sai token hoặc sai số tiền sẽ không được xác nhận tự động và có thể mất tiền.</i>\n"
        f"{vip_em('lightning', '⚡️')} <i>Sau khi chuyển, bấm “Tôi đã thanh toán — kiểm tra”. Cần khoảng {info['min_confirmations']} xác nhận mạng.</i>"
    )


async def show_bep20_checkout(query, user, qty, sku=DEFAULT_SKU):
    import hashlib
    if not bep20_enabled():
        await query.answer("Phương thức này chưa được bật.", show_alert=True)
        return
    if not 1 <= qty <= 50 or sku not in known_skus() or (sku != DEFAULT_SKU and qty > sku_max_qty(sku)):
        await query.answer("Số lượng không hợp lệ.", show_alert=True)
        return
    # Cùng một tin nhắn + số lượng + sản phẩm -> cùng mã đơn (bấm lặp không tạo hóa đơn mới). Có hậu tố riêng cho BEP20.
    reference = f"{user.id}:{query.message.chat_id}:{query.message.message_id}:{qty}:bep20"
    if sku != DEFAULT_SKU:
        reference += f":{sku}"
    order_id = "TGS" + hashlib.sha256(reference.encode()).hexdigest()[:12].upper()
    back = InlineKeyboardMarkup([
        [vip_btn("Chọn Lại Phương Thức Thanh Toán", callback_data=cb_qty("select", sku, qty), emoji_name="refresh")],
        [vip_btn("Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")],
    ])
    try:
        info = await asyncio.to_thread(create_web_bep20_order, order_id, user, qty, sku)
    except Bep20Unavailable as error:
        messages = {
            "out_of_stock": "Sản phẩm hiện đã hết hàng.",
            "product_unavailable": "Sản phẩm này chưa hỗ trợ thanh toán USDT BEP20 hoặc không đủ số lượng cho phép.",
            "busy_try_again": "Hệ thống đang bận, vui lòng thử lại sau ít phút.",
            "too_many_pending_orders": "Bạn đang có quá nhiều đơn chờ thanh toán. Vui lòng hoàn tất hoặc hủy bớt đơn cũ.",
        }
        await safe_edit_or_reply(query, f"{vip_em('shield')} <b>{messages.get(error.code, 'Chưa thể tạo hóa đơn USDT BEP20. Vui lòng thử lại sau hoặc chọn phương thức khác.')}</b>",
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
    text = render_bep20_invoice(order_id, info, qty, sku)
    markup = InlineKeyboardMarkup([
        [vip_btn("TÔI ĐÃ THANH TOÁN — KIỂM TRA", callback_data=f"confirm_pay_{order_id}", emoji_name="check", style="primary")],
        [vip_btn("Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")],
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


def render_binance_invoice(order_id: str, info: dict, qty: int, sku: str) -> str:
    return (
        f"{vip_em('crown')} ═══════ {vip_em('diamond')} <b>HÓA ĐƠN USDT — BINANCE PAY</b> {vip_em('diamond')} ═══════ {vip_em('crown')}\n\n"
        f"{vip_em('tag')} <b>Mã đơn:</b> <code>{html.escape(order_id)}</code>\n"
        f"{vip_em('box')} <b>Sản phẩm:</b> {html.escape(sku_display_name(sku))} × {qty}\n"
        f"{vip_em('money')} <b>Số tiền:</b> <code>{info['amount_usdt']}</code> USDT\n"
        f"{vip_em('card')} <b>Binance Pay ID nhận:</b> <code>{info['receiver_id']}</code>\n"
        f"{vip_em('calendar')} <b>Hạn thanh toán:</b> <code>{info['expires_at']:%H:%M:%S}</code>\n\n"
        f"{vip_em('shield', '⚠️')} <i>Mở Binance → Pay → Gửi, nhập Binance Pay ID ở trên và chuyển ĐÚNG số tiền trên bằng USDT. "
        f"Sai số tiền hoặc sai loại tiền sẽ không được xác nhận tự động.</i>\n"
        f"{vip_em('lightning', '⚡️')} <i>Sau khi chuyển, GIỮ LẠI “ID lệnh” (Order ID) trong chi tiết giao dịch, bấm nút bên dưới rồi dán ID lệnh vào đây.</i>"
    )


async def show_binance_checkout(query, user, qty, sku=DEFAULT_SKU):
    import hashlib
    if not binance_pay_enabled():
        await query.answer("Phương thức này chưa được bật.", show_alert=True)
        return
    if not 1 <= qty <= 50 or sku not in known_skus() or (sku != DEFAULT_SKU and qty > sku_max_qty(sku)):
        await query.answer("Số lượng không hợp lệ.", show_alert=True)
        return
    reference = f"{user.id}:{query.message.chat_id}:{query.message.message_id}:{qty}:binance"
    if sku != DEFAULT_SKU:
        reference += f":{sku}"
    order_id = "TGS" + hashlib.sha256(reference.encode()).hexdigest()[:12].upper()
    back = InlineKeyboardMarkup([
        [vip_btn("Chọn Lại Phương Thức Thanh Toán", callback_data=cb_qty("select", sku, qty), emoji_name="refresh")],
        [vip_btn("Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")],
    ])
    await asyncio.to_thread(sync_web_product_price, sku)
    try:
        info = await asyncio.to_thread(create_web_binance_order, order_id, user, qty, sku)
    except BinancePayUnavailable as error:
        messages = {
            "out_of_stock": "Sản phẩm hiện đã hết hàng.",
            "product_unavailable": "Sản phẩm này chưa hỗ trợ thanh toán Binance Pay hoặc không đủ số lượng cho phép.",
            "too_many_pending_orders": "Bạn đang có quá nhiều đơn chờ thanh toán. Vui lòng hoàn tất hoặc hủy bớt đơn cũ.",
        }
        await safe_edit_or_reply(query, f"{vip_em('shield')} <b>{messages.get(error.code, 'Chưa thể tạo hóa đơn Binance Pay. Vui lòng thử lại sau hoặc chọn phương thức khác.')}</b>",
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
        [vip_btn("TÔI ĐÃ CHUYỂN — DÁN ID LỆNH", callback_data=f"bpid_{order_id}", emoji_name="check", style="primary")],
        [vip_btn("Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")],
    ])
    await safe_edit_or_reply(query, render_binance_invoice(order_id, info, qty, sku), reply_markup=markup, parse_mode="HTML")


async def prompt_binance_id(query, user, order_id: str):
    """Khách bấm 'Tôi đã chuyển': bot chờ họ gửi ID lệnh trong tin nhắn kế tiếp."""
    import time
    if not binance_pay_enabled() or not _ORDER_CODE_RE.fullmatch(order_id or ""):
        await query.answer("Phương thức này chưa được bật.", show_alert=True)
        return
    conn = get_db_connection()
    try:
        order = conn.execute("SELECT * FROM orders WHERE order_id = ? AND user_id = ?", (order_id, user.id)).fetchone()
    finally:
        conn.close()
    if not order or order["payment_method"] != "binance_pay":
        await query.answer("Không tìm thấy đơn hàng.", show_alert=True)
        return
    if order["status"] != "pending":
        await query.answer("Đơn này không còn chờ thanh toán.", show_alert=True)
        return
    AWAITING_BINANCE_ID[user.id] = {"order_id": order_id, "until": time.monotonic() + BINANCE_ID_WAIT_SECONDS, "bad": 0}
    await query.answer()
    await query.message.reply_text(
        f"{vip_em('tag')} <b>Gửi ID lệnh Binance Pay</b> cho đơn <code>{html.escape(order_id)}</code>\n\n"
        f"Dán dãy số ID lệnh (Order ID) trong chi tiết giao dịch Binance, ví dụ: <code>453343187271753728</code>\n"
        f"{vip_em('shield', '⚠️')} <i>Chỉ gửi ID lệnh (toàn chữ số). Mỗi đơn chỉ được thử tối đa vài lần.</i>",
        parse_mode="HTML")


_BINANCE_REPLIES = {
    "not_found": "Chưa tìm thấy giao dịch với ID này. Hãy kiểm tra lại ID lệnh và thử lại sau 1–2 phút.",
    "amount_mismatch": "Giao dịch có số tiền khác với hóa đơn. Hệ thống chỉ chấp nhận ĐÚNG số tiền trên hóa đơn.",
    "invalid_transaction": "Giao dịch này không hợp lệ cho đơn hàng (sai loại tiền, người nhận hoặc loại giao dịch).",
    "before_order": "Giao dịch này được thực hiện trước khi tạo đơn nên không thể dùng cho đơn này.",
    "already_used": "ID lệnh này đã được dùng cho một đơn khác.",
    "too_fast": "Bạn thử hơi nhanh, vui lòng đợi khoảng 10 giây rồi gửi lại.",
    "too_many_attempts": "Đơn này đã thử quá số lần cho phép. Vui lòng liên hệ admin kèm mã đơn.",
    "unavailable": "Tạm thời chưa kiểm tra được với Binance. Đơn của bạn không bị ảnh hưởng, vui lòng thử lại sau ít phút.",
    "config": "Phương thức này đang được cấu hình. Vui lòng liên hệ admin kèm mã đơn.",
    "rejected": "Không thể xác nhận giao dịch này.",
}


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
    if not _BINANCE_ORDER_ID_RE.fullmatch(text):
        waiting["bad"] += 1
        if waiting["bad"] >= BINANCE_MAX_BAD_INPUTS:
            AWAITING_BINANCE_ID.pop(user.id, None)
            await update.message.reply_text("Đã dừng nhận ID lệnh. Bấm lại “Tôi đã chuyển — dán ID lệnh” khi bạn có ID.")
        else:
            await update.message.reply_text("ID lệnh phải là dãy 8–32 chữ số, ví dụ: 453343187271753728. Vui lòng gửi lại.")
        return True
    outcome = await asyncio.to_thread(confirm_web_binance, order_id, user.id, text)
    result = outcome["result"]
    if result == "paid":
        AWAITING_BINANCE_ID.pop(user.id, None)
        if await send_paid_order_delivery(context.bot, order_id, user.id):
            return True
        await update.message.reply_text(
            f"{vip_em('check', '✅')} Thanh toán đã được xác nhận cho đơn {order_id}, nhưng link chưa thể giao tự động lúc này. "
            f"Vui lòng liên hệ admin @wolfmodyt kèm mã đơn.")
        return True
    if result == "needs_review":
        AWAITING_BINANCE_ID.pop(user.id, None)
        await update.message.reply_text(
            f"{vip_em('shield')} Đơn {order_id} cần admin kiểm tra thủ công (thanh toán đến sau hạn). Vui lòng liên hệ @wolfmodyt kèm mã đơn.")
        return True
    if result in ("expired", "cancelled"):
        AWAITING_BINANCE_ID.pop(user.id, None)
        await update.message.reply_text("Đơn này đã hết hạn hoặc bị hủy. Nếu bạn đã chuyển tiền, vui lòng liên hệ @wolfmodyt kèm mã đơn.")
        return True
    if result in ("too_many_attempts", "config"):
        AWAITING_BINANCE_ID.pop(user.id, None)
    await update.message.reply_text(_BINANCE_REPLIES.get(result, _BINANCE_REPLIES["rejected"]))
    return True


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
    try:
        # gemini_plus keeps the original 3-argument call; other products pass their SKU explicitly.
        invoice_args = (order_id, user, qty) if sku == DEFAULT_SKU else (order_id, user, qty, sku)
        invoice = await asyncio.to_thread(create_web_plisio_invoice, *invoice_args)
    except PlisioMinimumAmountError as error:
        network = "BEP20 (BSC)" if error.currency == "USDT_BSC" else "TRC20"
        await safe_edit_or_reply(query,
            f"{vip_em('shield')} <b>Plisio yêu cầu hóa đơn lớn hơn {error.minimum:g} USDT trên mạng {network}.</b>\n\n"
            f"{vip_em('lightning')} Vui lòng chọn VietQR hoặc tăng số lượng để tổng tiền vượt mức này.",
            reply_markup=InlineKeyboardMarkup([
                [vip_btn("Chọn Lại Phương Thức Thanh Toán", callback_data=cb_qty("select", sku, qty), emoji_name="refresh", style="primary")],
                [vip_btn("Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")],
            ]), parse_mode="HTML")
        return
    except Exception as error:
        message = str(error)
        safe_error = message if re.fullmatch(r"HTTP [0-9]{3}: [a-z0-9_]{1,64}", message) else type(error).__name__
        logger.warning("Plisio invoice unavailable: %s", safe_error)
        await safe_edit_or_reply(query,
            f"{vip_em('shield')} <b>Chưa thể tạo hóa đơn Plisio.</b>\n"
            "Vui lòng thử lại hoặc chọn thanh toán VietQR.",
            reply_markup=InlineKeyboardMarkup([
                [vip_btn("Chọn Lại Phương Thức Thanh Toán", callback_data=cb_qty("select", sku, qty), emoji_name="refresh")],
                [vip_btn("Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")],
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
        f"{vip_em('crown')} ═══════ {vip_em('diamond')} <b>HÓA ĐƠN THANH TOÁN USDT VIP</b> {vip_em('diamond')} ═══════ {vip_em('crown')}\n\n"
        f"{vip_em('tag')} <b>Mã đơn hàng VIP:</b> <code>{order_id}</code>\n"
        f"{vip_em('box')} <b>Số lượng:</b> <b>{qty} link</b>\n"
        f"{vip_em('money')} <b>Số tiền thanh toán:</b> <code>{total:g} USDT</code>\n"
        f"{vip_em('globe')} <b>Cổng thanh toán:</b> Plisio (<code>{'USDT BEP20 (BSC)' if invoice['currency'] == 'USDT_BSC' else html.escape(invoice['currency'])}</code>)\n"
        f"{vip_em('calendar')} <b>Hạn thanh toán:</b> <code>{expiry:%H:%M:%S}</code>\n\n"
        f"{vip_em('lightning')} <i>Bấm nút bên dưới để mở hóa đơn và mã QR Plisio. "
        "Chuyển đúng số tiền và mạng được ghi trên hóa đơn.</i>"
    )
    await safe_edit_or_reply(query, bill, reply_markup=InlineKeyboardMarkup([
        [vip_btn("Thanh Toán USDT Qua Plisio", url=invoice["invoice_url"], emoji_name="usdt", style="success")],
        [vip_btn("TÔI ĐÃ THANH TOÁN — KIỂM TRA", callback_data=f"confirm_pay_{order_id}", emoji_name="check", style="primary")],
        [vip_btn("Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")],
    ]), parse_mode="HTML")


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    user = update.effective_user
    user_id = user.id
    await asyncio.to_thread(refresh_catalogue)
    sku_cb = parse_sku_qty_callback(data)
    view_cb = parse_view_callback(data)
    custom_sku = data[3:] if data.startswith("cq:") and data[3:] in known_skus() else None
    if data != "custom_qty" and custom_sku is None:
        AWAITING_QTY_USERS.pop(user_id, None)

    if data in ("main_menu", "buy_gemini_plus", "custom_qty"):
        await asyncio.to_thread(sync_web_product_price)
    elif data.startswith("buy_") and data[4:] in known_skus():
        await asyncio.to_thread(sync_web_product_price, data[4:])

    if data == "main_menu":
        await safe_edit_or_reply(
            query,
            welcome_message(user),
            reply_markup=main_menu_keyboard(user_id),
            parse_mode="HTML"
        )

    elif data == "buy_gemini_plus" or (data.startswith("buy_") and data[4:] in known_skus()):
        sku = DEFAULT_SKU if data == "buy_gemini_plus" else data[4:]
        prod = get_product(sku)
        stock = get_stock_count(sku)
        if sku != DEFAULT_SKU and (prod is None or int(prod["price"] or 0) <= 0):
            await safe_edit_or_reply(
                query,
                f"{vip_em('cross', '❌')} <b>Sản phẩm tạm thời chưa mở bán.</b>\nVui lòng quay lại sau hoặc liên hệ CSKH VIP.",
                reply_markup=InlineKeyboardMarkup([[vip_btn("🔙 Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")]]),
                parse_mode="HTML"
            )
            return

        keyboard = []
        if stock > 0:
            if sku == DEFAULT_SKU:
                keyboard += [
                    [
                        vip_btn("1 Link", callback_data="select_qty_1", emoji_name="crown"),
                        vip_btn("2 Link", callback_data="select_qty_2", emoji_name="crown"),
                    ],
                    [
                        vip_btn("4 Link", callback_data="select_qty_4", emoji_name="crown"),
                        vip_btn("8 Link", callback_data="select_qty_8", emoji_name="crown"),
                    ],
                    [
                        vip_btn("✏️ Nhập Số Lượng Tùy Chỉnh", callback_data="custom_qty", emoji_name="edit", style="primary")
                    ],
                ]
            else:
                max_qty = sku_max_qty(sku)
                if max_qty == 1:
                    keyboard.append([vip_btn("🛒 Mua Ngay (1 tài khoản)", callback_data=cb_qty("select", sku, 1), emoji_name="crown", style="primary")])
                else:
                    choices = [n for n in (1, 2, 4, 8) if n <= max_qty]
                    keyboard.append([vip_btn(f"{n}", callback_data=cb_qty("select", sku, n), emoji_name="crown") for n in choices])
                    keyboard.append([vip_btn("✏️ Nhập Số Lượng Tùy Chỉnh", callback_data=f"cq:{sku}", emoji_name="edit", style="primary")])
        if has_own_guide(sku):
            keyboard.append([vip_btn("📖 Hướng Dẫn Sản Phẩm Này", callback_data=f"gd:{sku}", emoji_name="book")])
        keyboard.append([vip_btn("🔙 Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")])

        await send_product_screen(query, sku, render_product_intro(sku, stock), keyboard)

    elif data.startswith("soldout_") and data[8:] in known_skus():
        await query.answer("Sản phẩm này hiện đã hết hàng. Vui lòng quay lại sau!", show_alert=True)

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
            reason = "Sản phẩm đã hết hàng!" if stock <= 0 else (
                f"Mỗi đơn tối đa {sku_max_qty(sku)} sản phẩm!" if qty > sku_max_qty(sku) else f"Kho hiện chỉ còn {stock}!")
            await query.answer(reason, show_alert=True)
            return
        if stock < qty:
            await query.answer(f"Kho hiện chỉ còn {stock} link khả dụng!", show_alert=True)
        await show_order_confirmation(query, user, sku=sku, qty=qty)

    elif data == "custom_qty" or custom_sku is not None:
        sku = custom_sku or DEFAULT_SKU
        AWAITING_QTY_USERS[user_id] = sku
        stock = get_stock_count(sku)
        prod = get_product(sku)
        price_fmt = f"{prod['price']:,} VNĐ"
        usdt_price = get_setting(usdt_setting_key(sku), "1")
        prompt_text = (
            f"{vip_em('crown', '👑')} ═══════ {vip_em('memo', '✏️')} <b>NHẬP SỐ LƯỢNG TÙY CHỈNH</b> {vip_em('memo', '✏️')} ═══════ {vip_em('crown', '👑')}\n\n"
            f"Vui lòng gửi tin nhắn chứa <b>số lượng link</b> bạn muốn đặt mua vào đây (Ví dụ: <code>3</code>, <code>5</code>, <code>10</code>...)\n\n"
            f"{vip_em('chart', '🏷')} <b>Đơn giá ưu đãi:</b> <code>{price_fmt}</code> / link (hoặc <code>{usdt_price} USDT</code>)\n"
            f"{vip_em('box', '📦')} <b>Hiện có trong kho:</b> <code>{stock}</code> link sẵn sàng\n\n"
            f"{vip_em('lightning', '⚡️')} <i>Nhập số lượng từ 1 đến {sku_max_qty(sku)} link để hệ thống tự động tính hóa đơn VIP!</i>"
        )
        keyboard = [
            [vip_btn("🔙 Hủy & Chọn Lại", callback_data=cb_buy(sku), emoji_name="cross")]
        ]
        await safe_edit_or_reply(query, prompt_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data in ("create_order_gemini", "create_order_vietqr") or data.startswith("create_order_vietqr_") or (sku_cb and sku_cb[0] == "cv"):
        if sku_cb:
            _, sku, qty = sku_cb
            if qty < 1 or qty > sku_max_qty(sku):
                await query.answer(f"Mỗi đơn tối đa {sku_max_qty(sku)} sản phẩm!", show_alert=True)
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
            await safe_edit_or_reply(query, f"{vip_em('shield')} <b>Chưa thể lấy giá mới nhất.</b> Vui lòng thử lại sau.",
                reply_markup=InlineKeyboardMarkup([[vip_btn("Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")]]), parse_mode="HTML")
            return
        prod = get_product(sku)
        stock = get_stock_count(sku)

        if stock < qty:
            await safe_edit_or_reply(
                query,
                f"{vip_em('cross', '❌')} <b>Sản phẩm không đủ số lượng trong kho! (Còn {stock} link)</b>\nVui lòng giảm số lượng đặt mua.",
                reply_markup=InlineKeyboardMarkup([[vip_btn("🔄 Chọn Lại Số Lượng", callback_data=cb_buy(sku), emoji_name="refresh")]]),
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

        bill_text = (
            f"{vip_em('crown', '👑')} ═══════ {vip_em('diamond', '💎')} <b>HÓA ĐƠN GIAO DỊCH VIETQR VIP</b> {vip_em('diamond', '💎')} ═══════ {vip_em('crown', '👑')}\n\n"
            f"{vip_em('tag', '🆔')} <b>Mã đơn hàng VIP:</b> <code>{order_id}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{vip_em('box', '📦')} <b>Dịch vụ:</b> {prod['name']}\n"
            f"{vip_em('sparkle', '✨')} <b>Số lượng:</b> <b>{qty} link</b>\n"
            f"{vip_em('money', '💵')} <b>Số tiền cần thanh toán:</b> <code>{amount:,} VNĐ</code>\n\n"
            f"{vip_em('bank', '🏦')} <b>Ngân hàng:</b> <code>{bank_name}</code> (Ngân Hàng Á Châu)\n"
            f"{vip_em('card', '💳')} <b>Số tài khoản:</b> <code>{bank_account}</code>\n"
            f"{vip_em('user', '👤')} <b>Chủ tài khoản:</b> <code>{bank_owner}</code>\n"
            f"{vip_em('memo', '📝')} <b>Nội dung chuyển khoản:</b> <code>{order_id}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{vip_em('calendar', '⏳')} <b>Thời hạn thanh toán:</b> <b>10 phút</b> (Hết hạn lúc: <code>{expires_display}</code>)\n"
            f"{vip_em('lightning', '⚡️')} <i>Mẹo VIP: Quét mã QR ACB phía trên trên App ngân hàng để hệ thống tự điền đúng STK & Nội dung!</i>\n"
            f"{vip_em('shield', '⚠️')} <i>Lưu ý: Đơn hàng sẽ tự động hủy sau 10 phút nếu chưa hoàn tất chuyển tiền.</i>"
        )

        buttons = [
            [vip_btn("✅ TÔI ĐÃ CHUYỂN KHOẢN XONG", callback_data=f"confirm_pay_{order_id}", emoji_name="check", style="primary")],
            [vip_btn(f"🪙 Đổi Sang Thanh Toán USDT ({total_usdt_str} USDT)", callback_data=f"switch_pay_usdt_{order_id}", emoji_name="usdt", style="success")],
            [vip_btn("❌ HỦY GIAO DỊCH NÀY", callback_data=f"cancel_order_{order_id}", emoji_name="cross", style="danger")],
            [vip_btn("🔙 VỀ SẢNH MENU CHÍNH", callback_data="main_menu", emoji_name="back")]
        ]

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
                await query.answer("Sản phẩm của đơn này hiện không còn được bán.", show_alert=True)
                return
            await show_plisio_checkout(query, user, int(order["quantity"] or 1), order_id, sku=order_sku(order))

    elif data.startswith("switch_pay_vietqr_"):
        order_id = data.replace("switch_pay_vietqr_", "")
        conn = get_db_connection()
        order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        conn.close()
        if not order or int(order["user_id"]) != int(user_id) or order["status"] != "pending":
            await query.answer("Đơn hàng không hợp lệ.", show_alert=True)
            return
        sku = order_sku(order)
        if sku not in known_skus():
            await query.answer("Sản phẩm của đơn này hiện không còn được bán.", show_alert=True)
            return
        if not await asyncio.to_thread(sync_web_product_price, sku, True):
            await query.answer("Chưa thể lấy giá mới nhất, vui lòng thử lại.", show_alert=True)
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

        expires_display = "10 phút"
        if order and "expires_at" in order.keys() and order["expires_at"]:
            try:
                exp_dt = datetime.strptime(order["expires_at"], "%Y-%m-%d %H:%M:%S")
                expires_display = exp_dt.strftime("%H:%M:%S")
            except Exception:
                pass

        bill_text = (
            f"{vip_em('crown', '👑')} ═══════ {vip_em('diamond', '💎')} <b>HÓA ĐƠN GIAO DỊCH VIETQR VIP</b> {vip_em('diamond', '💎')} ═══════ {vip_em('crown', '👑')}\n\n"
            f"{vip_em('tag', '🆔')} <b>Mã đơn hàng VIP:</b> <code>{order_id}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{vip_em('box', '📦')} <b>Dịch vụ:</b> {prod['name']}\n"
            f"{vip_em('sparkle', '✨')} <b>Số lượng:</b> <b>{qty} link</b>\n"
            f"{vip_em('money', '💵')} <b>Số tiền cần thanh toán:</b> <code>{amount:,} VNĐ</code>\n\n"
            f"{vip_em('bank', '🏦')} <b>Ngân hàng:</b> <code>{bank_name}</code> (Ngân Hàng Á Châu)\n"
            f"{vip_em('card', '💳')} <b>Số tài khoản:</b> <code>{bank_account}</code>\n"
            f"{vip_em('user', '👤')} <b>Chủ tài khoản:</b> <code>{bank_owner}</code>\n"
            f"{vip_em('memo', '📝')} <b>Nội dung chuyển khoản:</b> <code>{order_id}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{vip_em('calendar', '⏳')} <b>Thời hạn thanh toán:</b> <b>10 phút</b> (Hết hạn lúc: <code>{expires_display}</code>)\n"
            f"{vip_em('lightning', '⚡️')} <i>Mẹo VIP: Quét mã QR ACB phía trên trên App ngân hàng để hệ thống tự điền đúng STK & Nội dung!</i>\n"
            f"{vip_em('shield', '⚠️')} <i>Lưu ý: Đơn hàng sẽ tự động hủy sau 10 phút nếu chưa hoàn tất chuyển tiền.</i>"
        )

        buttons = [
            [vip_btn("✅ TÔI ĐÃ CHUYỂN KHOẢN XONG", callback_data=f"confirm_pay_{order_id}", emoji_name="check", style="primary")],
            [vip_btn(f"🪙 Đổi Sang Thanh Toán USDT ({total_usdt_str} USDT)", callback_data=f"switch_pay_usdt_{order_id}", emoji_name="usdt", style="success")],
            [vip_btn("❌ HỦY GIAO DỊCH NÀY", callback_data=f"cancel_order_{order_id}", emoji_name="cross", style="danger")],
            [vip_btn("🔙 VỀ SẢNH MENU CHÍNH", callback_data="main_menu", emoji_name="back")]
        ]

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

    elif data.startswith("confirm_pay_"):
        order_id = data.replace("confirm_pay_", "")
        conn = get_db_connection()
        order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        conn.close()

        if not order:
            await query.answer("❌ Đơn hàng không tồn tại!", show_alert=True)
            return

        if int(order["user_id"]) != int(user_id):
            await query.answer("❌ Đơn hàng không tồn tại!", show_alert=True)
            return

        if order["status"] == "completed":
            if not order["delivery_notified_at"]:
                sent = await send_paid_order_delivery(context.bot, order_id, user_id)
                await query.answer("Đã gửi link." if sent else "Đang thử gửi link, vui lòng thử lại.", show_alert=True)
                return
            links = (order["item_delivered"] or "").splitlines()
            if links:
                msg_body, link_buttons = format_delivered_links_message(order_id, links, order_sku(order))
                await query.message.reply_text(msg_body, reply_markup=InlineKeyboardMarkup(link_buttons), parse_mode="HTML")
                conn = get_db_connection()
                conn.execute("UPDATE orders SET delivery_notified_at = CURRENT_TIMESTAMP WHERE order_id = ? AND user_id = ?", (order_id, user_id))
                conn.commit()
                conn.close()
            await query.answer("✅ Đơn hàng đã được bàn giao.", show_alert=True)
            return

        # Confirm the provider status before local expiry/cancellation checks. VietQR
        # may have been marked paid by the website while this SQLite order is stale.
        bep20_stat = {}
        if order["payment_method"] == "bep20" and order["status"] == "pending":
            bep20_stat = await asyncio.to_thread(check_web_bep20, order_id, user_id)
            if bep20_stat.get("status") == "needs_review":
                await query.answer("Đã thấy giao dịch nhưng cần admin kiểm tra.", show_alert=True)
                await query.message.reply_text(
                    f"{vip_em('shield')} <b>Đơn <code>{html.escape(order_id)}</code> cần admin kiểm tra thủ công</b> (thanh toán đến sau hạn hoặc cần đối soát).\n"
                    f"{vip_em('chat', '💬')} Vui lòng liên hệ <b>@wolfmodyt</b> kèm mã đơn.",
                    reply_markup=InlineKeyboardMarkup([[vip_btn("Liên Hệ Admin", url="https://t.me/wolfmodyt", emoji_name="chat")]]),
                    parse_mode="HTML")
                return
        web_stat = check_web_order_status(order_id)
        if web_stat.get("status") in ("paid", "delivered"):
            if await send_paid_order_delivery(context.bot, order_id, user_id):
                await query.answer("Giao dịch đã được xác nhận thành công!", show_alert=True)
                return

            await query.answer("Đã nhận thanh toán; đang chờ xử lý link.", show_alert=True)
            await query.message.reply_text(
                f"{vip_em('check', '✅')} <b>Thanh toán đã được xác nhận cho đơn <code>{html.escape(order_id)}</code>.</b>\n"
                f"{vip_em('calendar', '⏳')} Link chưa thể giao tự động lúc này.\n"
                f"{vip_em('chat', '💬')} Vui lòng liên hệ admin <b>@wolfmodyt</b> để được hỗ trợ.",
                reply_markup=InlineKeyboardMarkup([[vip_btn("Liên Hệ Admin", url="https://t.me/wolfmodyt", emoji_name="chat")]]),
                parse_mode="HTML"
            )
            return

        # Kiểm tra nếu đơn hàng đã bị hủy hoặc hết hạn
        if order["status"] in ("cancelled", "expired"):
            await query.answer("❌ Đơn hàng này đã hết hạn 10 phút hoặc đã bị hủy!", show_alert=True)
            await safe_edit_or_reply(
                query,
                f"{vip_em('cross', '❌')} <b>ĐƠN HÀNG <code>{order_id}</code> ĐÃ HẾT HẠN THANH TOÁN!</b>\n\n"
                f"{vip_em('calendar', '⏳')} Thời gian chờ 10 phút cho đơn hàng đã kết thúc và đơn đã được tự động hủy. Nếu đã chuyển khoản, vui lòng liên hệ @wolfmodyt để được hỗ trợ.\n\n"
                f"{vip_em('lightning', '⚡️')} <i>Quý Khách vui lòng tạo đơn hàng mới:</i>",
                reply_markup=InlineKeyboardMarkup([[vip_btn("Đặt Mua Đơn Mới", callback_data=cb_buy(order_sku(order)), emoji_name="crown", style="primary")]]),
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

            await query.answer("❌ Đơn hàng đã hết hạn 10 phút!", show_alert=True)
            await safe_edit_or_reply(
                query,
                f"{vip_em('cross', '❌')} <b>ĐƠN HÀNG <code>{order_id}</code> ĐÃ HẾT HẠN THANH TOÁN!</b>\n\n"
                f"{vip_em('calendar', '⏳')} Đơn hàng chỉ có hiệu lực trong <b>10 phút</b>. Đơn này đã quá hạn và hệ thống đã hủy đơn. Nếu đã chuyển khoản, vui lòng liên hệ @wolfmodyt để được hỗ trợ.\n\n"
                f"{vip_em('lightning', '⚡️')} <i>Quý Khách vui lòng tạo đơn mới để nhận mã QR chuyển khoản hợp lệ:</i>",
                reply_markup=InlineKeyboardMarkup([[vip_btn("Đặt Mua Đơn Mới", callback_data=cb_buy(order_sku(order)), emoji_name="crown", style="primary")]]),
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
            notice = "<b>Thanh toán đã được xác nhận nhưng link chưa sẵn sàng.</b>"
        elif status == "pending" or status == "not_found" or web_stat.get("found") is False:
            notice = "<b>Hệ thống chưa nhận được thanh toán cho đơn hàng này.</b>"
            if bep20_stat.get("confirmations"):
                notice = (f"<b>Đã thấy giao dịch trên mạng, đang chờ xác nhận ({bep20_stat['confirmations']}/"
                          f"{bep20_stat.get('min_confirmations', '?')}). Vui lòng bấm kiểm tra lại sau ít phút.</b>")
        else:
            notice = "<b>Hiện chưa kiểm tra được trạng thái thanh toán.</b>"
        await query.message.reply_text(
            f"{vip_em('crown')} ═══════ {vip_em('diamond')} <b>KIỂM TRA THANH TOÁN VIP</b> {vip_em('diamond')} ═══════ {vip_em('crown')}\n\n"
            f"{vip_em('tag')} <b>Mã đơn:</b> <code>{html.escape(order_id)}</code>\n\n"
            f"{vip_em('shield')} {notice}\n\n"
            f"{vip_em('chat')} Nếu bạn đã chuyển khoản, vui lòng liên hệ admin <b>@wolfmodyt</b> để được hỗ trợ.\n"
            f"{vip_em('calendar')} Nếu chưa chuyển, hãy thanh toán theo hóa đơn rồi bấm kiểm tra lại.",
            reply_markup=InlineKeyboardMarkup([
                [vip_btn("Kiểm Tra Thanh Toán", callback_data=f"confirm_pay_{order_id}", emoji_name="refresh", style="primary")],
                [vip_btn("Liên Hệ Admin", url="https://t.me/wolfmodyt", emoji_name="chat")],
                [vip_btn("Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")],
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
            f"{vip_em('cross', '❌')} <b>Đã hủy giao dịch đơn hàng <code>{order_id}</code>.</b>",
            reply_markup=InlineKeyboardMarkup([[vip_btn("Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")]]),
            parse_mode="HTML"
        )

    elif data == "my_orders" or (view_cb and view_cb[0] == "ordp"):
        # Bấm "Đơn hàng" mới hiện đơn của chính khách (có phân trang); màn chính không liệt kê đơn.
        orders_text, orders_markup = render_orders_page(user_id, 1 if data == "my_orders" else view_cb[1])
        await safe_edit_or_reply(query, orders_text, reply_markup=orders_markup, parse_mode="HTML")

    elif view_cb and view_cb[0] == "gd":
        # Hướng dẫn chỉ của đúng sản phẩm này; chưa có thì báo trung tính, không mượn hướng dẫn sản phẩm khác.
        sku = view_cb[1]
        guide_text = render_guide(sku)
        keyboard = []
        if sku in known_skus():
            keyboard.append([vip_btn(" Xem Sản Phẩm Này", callback_data=cb_buy(sku), emoji_name="lightning", style="success")])
        keyboard.append([vip_btn("Đơn Hàng Của Tôi", callback_data="my_orders", emoji_name="diamond")])
        keyboard.append([vip_btn("Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")])
        await safe_edit_or_reply(query, guide_text if guide_text is not None else f"{vip_em('shield', '🛡')} {NO_GUIDE_TEXT}",
                                 reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "guide":
        # Nút cũ còn sót trong tin nhắn đã gửi: hướng dẫn không còn là màn chung.
        await safe_edit_or_reply(
            query,
            f"{vip_em('book', '📖')} Hướng dẫn nằm trong từng sản phẩm (màn chi tiết sản phẩm) và trong đơn hàng của sản phẩm đó.",
            reply_markup=InlineKeyboardMarkup([
                [vip_btn("Đơn Hàng Của Tôi", callback_data="my_orders", emoji_name="diamond")],
                [vip_btn("Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")],
            ]), parse_mode="HTML")

    elif data == "support":
        contact = get_setting("support_contact", "@wolfmodyt")
        clean_contact = contact.replace("@", "")
        supp_text = (
            f"{vip_em('shield', '🛡')} ═══════ {vip_em('diamond', '💎')} <b>TRUNG TÂM HỖ TRỢ VIP 24/7</b> {vip_em('diamond', '💎')} ═══════ {vip_em('shield', '🛡')}\n\n"
            "Quý khách cần hỗ trợ kích hoạt gói Google AI Plus, kiểm tra đơn hàng hoặc tư vấn kỹ thuật, vui lòng liên hệ trực tiếp Admin & CSKH VIP:\n\n"
            f"{vip_em('crown', '👑')} <b>Admin Quản Trị:</b> <a href=\"https://t.me/{clean_contact}\">@{clean_contact}</a>\n"
            f"{vip_em('lightning', '⚡️')} <b>Thời gian hỗ trợ:</b> Trực tuyến 24/7 (Phản hồi < 3 phút)\n"
            f"{vip_em('globe', '🌐')} <b>Cổng Web Portal:</b> <a href=\"https://www.wolfmod.xyz\">wolfmod.xyz</a>\n\n"
            f"{vip_em('star', '⚜️')} <i>WolfMod cam kết bảo hành uy tín và hỗ trợ kỹ thuật tận tâm cho Quý Khách!</i>"
        )
        keyboard = [
            [vip_btn("Nhắn Tin Cho Admin @wolfmodyt", url=f"https://t.me/{clean_contact}", emoji_name="chat")],
            [vip_btn("Trở Về Menu Chính", callback_data="main_menu", emoji_name="back")]
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
            msg_body, link_buttons = format_delivered_links_message(order_id, delivered_links, order_sku(order))
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
                    text=f"{vip_em('cross', '❌')} Rất tiếc, đơn hàng <code>{order_id}</code> của bạn đã bị từ chối do không tìm thấy giao dịch hợp lệ. Vui lòng liên hệ Admin nếu có nhầm lẫn.",
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

def format_stock_added_notification(added_count: int, total_stock: int, sample_links: list = None) -> str:
    """Định dạng thông báo thêm link mới với thẻ VIP Custom Emoji Telegram Premium (Tuyệt đối bảo mật, không để lộ link)"""
    return (
        f"{vip_em('crown', '👑')} ═══════ {vip_em('diamond', '💎')} <b>THÔNG BÁO KHO HÀNG VIP</b> {vip_em('diamond', '💎')} ═══════ {vip_em('crown', '👑')}\n\n"
        f"{vip_em('party', '🎉')} <b>Hệ thống vừa cập nhật thêm <code>+{added_count}</code> link offer Gemini!</b>\n\n"
        f"├─ {vip_em('sparkle', '✨')} <b>Gói dịch vụ:</b> <b>Google AI Plus 400GB (12 Tháng)</b>\n"
        f"└─ {vip_em('box', '📦')} <b>Tổng kho khả dụng:</b> <code>{total_stock}</code> link"
    )

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
    reply_msg = format_stock_added_notification(added, total_avail, links)
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

    # Nếu người dùng đang trong luồng nhập số lượng tùy chỉnh
    if user_id in AWAITING_QTY_USERS:
        sku = AWAITING_QTY_USERS[user_id]
        if not raw_text.isdigit():
            await update.message.reply_text(
                f"{vip_em('cross', '❌')} <b>Vui lòng chỉ nhập số nguyên dương hợp lệ!</b>\n"
                f"Ví dụ: <code>3</code>, <code>5</code>, <code>10</code>...",
                parse_mode="HTML"
            )
            return

        qty = int(raw_text)
        if qty <= 0:
            await update.message.reply_text(
                f"{vip_em('cross', '❌')} <b>Số lượng mua tối thiểu là 1 link!</b>\nVui lòng nhập lại:",
                parse_mode="HTML"
            )
            return

        qty_limit = sku_max_qty(sku)
        if qty > qty_limit:
            await update.message.reply_text(
                f"{vip_em('shield', '⚠️')} <b>Số lượng đặt mua tối đa mỗi đơn là {qty_limit} link!</b>\n"
                f"Nếu muốn mua số lượng lớn hơn, vui lòng chia làm nhiều đơn hoặc liên hệ CSKH VIP.",
                parse_mode="HTML"
            )
            return

        stock = get_stock_count(sku)
        if stock < qty:
            await update.message.reply_text(
                f"{vip_em('box', '📦')} <b>Kho hiện chỉ còn {stock} link khả dụng!</b>\n"
                f"Bạn có thể đặt tối đa <b>{stock}</b> link hoặc liên hệ Admin nhập thêm hàng.",
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
                """SELECT order_id, user_id, status, payment_method FROM orders
                   WHERE delivery_notified_at IS NULL
                     AND status IN ('pending', 'cancelled', 'expired', 'completed')
                     AND created_at >= datetime('now', '-7 days')
                   ORDER BY created_at DESC LIMIT 100"""
            ).fetchall()
            conn.close()
            for row in rows:
                # The web status endpoint has a 10 requests/minute limit.
                await asyncio.sleep(8)
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
                    continue
                conn.execute("UPDATE orders SET status = 'cancelled' WHERE order_id = ? AND status = 'pending'", (order_id,))
                conn.commit()
                logger.info(f"Đã tự động hủy đơn hết hạn 10 phút: {order_id} (User: {user_id})")

                # Gửi thông báo đến khách hàng trên Telegram
                try:
                    cancel_msg = (
                        f"{vip_em('cross', '❌')} <b>ĐƠN HÀNG <code>{order_id}</code> ĐÃ HẾT HẠN THANH TOÁN!</b>\n\n"
                        f"{vip_em('calendar', '⏳')} Thời hạn <b>10 phút</b> cho đơn hàng đã kết thúc nên hệ thống đã tự động hủy giao dịch này.\n\n"
                        f"{vip_em('lightning', '⚡️')} <i>Nếu Quý Khách vẫn có nhu cầu mua gói, vui lòng bấm nút bên dưới để đặt đơn mới:</i>"
                    )
                    kb = [
                        [vip_btn("Đặt Mua Đơn Mới", callback_data=cb_buy(order_sku(ord_row)), emoji_name="crown", style="primary")],
                        [vip_btn("Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")]
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

    app = Application.builder().token(use_token).post_init(post_init_hook).build()

    # User commands (hỗ trợ cả /start, .start, /menu, .menu, /help, .help)
    app.add_handler(CommandHandler(["start", "menu", "help"], start_command))
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex(r"(?i)^[\/\.](start|menu|help|shop|store)\b"), start_command))
    app.add_handler(CallbackQueryHandler(callback_handler))

    # Admin commands (hỗ trợ cả /command và .command)
    app.add_handler(CommandHandler("setprice", admin_set_price))
    app.add_handler(CommandHandler("setbank", admin_set_bank))
    app.add_handler(CommandHandler("setusdt", admin_set_usdt))
    app.add_handler(CommandHandler("testmode", admin_set_test_mode))
    app.add_handler(CommandHandler("addstock", admin_add_stock))
    app.add_handler(CommandHandler("setchannel", admin_set_channel))

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
