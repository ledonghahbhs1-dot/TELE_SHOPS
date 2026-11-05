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

_last_web_stock_check = 0
_cached_web_stock = None

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
    global _last_web_stock_check, _cached_web_stock
    import time
    import urllib.request
    import json

    now = time.time()
    if _cached_web_stock is not None and (now - _last_web_stock_check) < 3:
        return _cached_web_stock

    # 1. Lấy trực tiếp từ Web wolfmod.xyz để luôn đồng bộ 100% với Web Admin
    try:
        url = f"https://www.wolfmod.xyz/api/tg/stock/available?sku={prod_id}"
        req = urllib.request.Request(url, headers={"User-Agent": "TelegramShopBot", "Authorization": f"Bearer {BOT_TOKEN}"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("success") and "available" in data:
                val = int(data["available"])
                _cached_web_stock = val
                _last_web_stock_check = now
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
    _cached_web_stock = count
    _last_web_stock_check = now
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

def sync_order_to_web_sync(order_id: str, tg_user_id: int, tg_username: str, amount_vnd: int, currency: str = "VND", provider: str = "sepay", qty: int = 1):
    """Đồng bộ đơn hàng lên Web Backend (wolfmod.xyz) để SePay và Admin Dashboard nhận diện"""
    import urllib.request
    import json
    try:
        url = "https://www.wolfmod.xyz/api/tg/orders/sync"
        payload = {
            "order_code": str(order_id).strip(),
            "tg_user_id": int(tg_user_id),
            "tg_username": str(tg_username or ""),
            "sku": "gemini_plus",
            "product_name": "Google AI Plus (400 GB Cloud)",
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

async def sync_order_to_web_async(order_id: str, tg_user_id: int, tg_username: str, amount_vnd: int, currency: str = "VND", provider: str = "sepay", qty: int = 1):
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, sync_order_to_web_sync, order_id, tg_user_id, tg_username, amount_vnd, currency, provider, qty)
    except Exception as e:
        logger.warning(f"Lỗi chạy sync_order_to_web_async: {e}")

# Trạng thái chờ người dùng nhập số lượng tuỳ chỉnh: user_id -> sku
AWAITING_QTY_USERS: dict[int, str] = {}

def format_delivered_links_message(order_id: str, delivered_links: list[str]) -> tuple[str, list]:
    """Định dạng tin nhắn và nút mở link sau khi thanh toán thành công"""
    if len(delivered_links) == 1:
        single_link = delivered_links[0]
        msg = (
            f"{vip_em('party', '🎉')} <b>THANH TOÁN THÀNH CÔNG ĐƠN HÀNG <code>{order_id}</code>!</b>\n\n"
            f"{vip_em('check', '✅')} Hệ thống đã xác nhận thanh toán của bạn.\n"
            f"{vip_em('key', '🔗')} <b>Link nhận Google AI Plus 400GB (12 Tháng - BH 24h):</b>\n<code>{single_link}</code>\n\n"
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
            links_text += f"{vip_em('star', '✦')} <b>Link #{idx}:</b> <code>{lk}</code>\n"
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

async def show_order_confirmation(target, user, sku="gemini_plus", qty=1):
    """Hiển thị màn hình xác nhận đơn hàng kèm nút thanh toán VietQR và USDT theo số lượng"""
    prod = get_product(sku)
    stock = get_stock_count(sku)
    price_unit = prod["price"]
    total_vnd = price_unit * qty
    price_unit_fmt = f"{price_unit:,} VNĐ"
    total_vnd_fmt = f"{total_vnd:,} VNĐ"

    usdt_unit = float(get_setting("usdt_price", "1"))
    total_usdt = round(usdt_unit * qty, 2)
    total_usdt_str = str(int(total_usdt)) if total_usdt.is_integer() else str(total_usdt)

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

    keyboard = [
        [vip_btn(f" Thanh Toán VietQR ({total_vnd_fmt})", callback_data=f"create_order_vietqr_{qty}", emoji_name="card", style="primary")],
        [vip_btn(f" Thanh Toán USDT ({total_usdt_str} USDT)", callback_data=f"create_order_usdt_{qty}", emoji_name="usdt", style="success")],
        [vip_btn("🔄 Chọn Lại Số Lượng", callback_data="buy_gemini_plus", emoji_name="refresh")],
        [vip_btn("🔙 Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")]
    ]

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
                qty=int(ord_row["quantity"] or 1)
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

def main_menu_keyboard(user_id=None):
    stock_count = get_stock_count("gemini_plus")

    keyboard = [
        [
            vip_btn(f"MUA GOOGLE AI PLUS 12 THÁNG • 20K (Kho: {stock_count})", callback_data="buy_gemini_plus", emoji_name="crown", style="primary")
        ],
        [
            vip_btn("Đơn Hàng VIP Của Tôi", callback_data="my_orders", emoji_name="diamond"),
            vip_btn("Cẩm Nang Kích Hoạt 400GB", callback_data="guide", emoji_name="book")
        ],
        [
            vip_btn("CSKH Trực Tuyến 24/7", callback_data="support", emoji_name="chat")
        ]
    ]

    return InlineKeyboardMarkup(keyboard)

# ==================== HANDLERS ====================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user:
        AWAITING_QTY_USERS.pop(user.id, None)
    safe_name = html.escape(user.first_name) if user and user.first_name else "Quý Khách"
    welcome_text = (
        f"{vip_em('crown', '👑')} ═══════ {vip_em('diamond', '💎')} <b>WOLFMOD VIP STORE</b> {vip_em('diamond', '💎')} ═══════ {vip_em('crown', '👑')}\n\n"
        f"{vip_em('sparkle', '✨')} <i>Kính chào Quý Khách VIP</i> <b>{safe_name}</b> <i>đến với hệ thống dịch vụ số cao cấp!</i>\n\n"
        f"{vip_em('star', '🌟')} <b>DỊCH VỤ ĐẶC QUYỀN ĐANG MỞ BÁN:</b>\n"
        f"<b>Google AI Plus 400GB (Thời Hạn 12 Tháng)</b>\n\n"
        f"{vip_em('chart', '📊')} <b>BẢNG SO SÁNH GIÁ 12 THÁNG SIÊU TIẾT KIỆM:</b>\n"
        f"├─ {vip_em('cross', '❌')} <b>Giá gốc Google:</b> <code>132.000 ₫/tháng</code> → <code>1.584.000 ₫ / 12 tháng</code>\n"
        f"├─ {vip_em('money', '💰')} <b>Giá VIP tại WolfMod:</b> <b>20.000 ₫ / 12 Tháng</b> (hoặc <b>1 USDT</b>)\n"
        f"├─ {vip_em('fire', '🔥')} <b>Mức tiết kiệm:</b> <b>99%</b> <i>(Rẻ hơn 1.564.000 ₫ — Chỉ ~1.6k/tháng)</i>\n"
        f"├─ {vip_em('calendar', '⏳')} <b>Thời hạn sử dụng:</b> <b>12 Tháng (1 Năm trọn vẹn)</b>\n"
        f"└─ {vip_em('shield', '🛡')} <b>Chính sách bảo hành:</b> <b>Bảo hành 24h (1 đổi 1 siêu tốc)</b>\n\n"
        f"{vip_em('lightning', '⚡️')} <b>Hiệu suất:</b> Gemini 3.6 Flash, Deep Research, hạn mức x2\n"
        f"{vip_em('sparkle', '✉️')} <b>Gmail AI:</b> Trợ lý Gemini hỗ trợ hiệu đính trong Gmail\n"
        f"{vip_em('star', '🎨')} <b>Sáng tạo:</b> Mở rộng tạo ảnh, nhạc, video & Gemini Notebook\n"
        f"{vip_em('box', '📦')} <b>Bộ nhớ 400 GB:</b> Dùng chung Gmail, Google Drive, Google Photos\n"
        f"{vip_em('shield', '👨‍👩‍👧‍👦')} <b>Gia đình:</b> Hỗ trợ chia sẻ tối đa 5 thành viên gia đình\n\n"
        f"{vip_em('star', '👇')} <i>Vui lòng chọn đặc quyền bên dưới để tiếp tục:</i>"
    )
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

def create_web_plisio_invoice(order_id, user, qty):
    import json
    import urllib.request
    from urllib.parse import urlsplit
    payload = json.dumps({"order_code": order_id, "tg_user_id": user.id,
                          "tg_username": user.username or "", "sku": "gemini_plus", "qty": qty}).encode("utf-8")
    req = urllib.request.Request("https://www.wolfmod.xyz/api/tg/orders/plisio", data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {BOT_TOKEN}"}, method="POST")
    with urllib.request.urlopen(req, timeout=25) as response:
        invoice = json.loads(response.read())
    url = urlsplit(invoice.get("invoice_url", ""))
    if not invoice.get("success") or url.scheme != "https" or url.username or url.password or not (
        url.hostname == "plisio.net" or (url.hostname or "").endswith(".plisio.net")
    ) or invoice.get("order_code") != order_id:
        raise ValueError("Invalid Plisio invoice response")
    return invoice


async def show_plisio_checkout(query, user, qty, order_id=None):
    import hashlib
    if not 1 <= qty <= 50:
        return
    if order_id is None:
        # Repeated taps on the same confirmation reuse the same provider invoice.
        reference = f"{user.id}:{query.message.chat_id}:{query.message.message_id}:{qty}"
        order_id = "TGS" + hashlib.sha256(reference.encode()).hexdigest()[:12].upper()
    try:
        invoice = await asyncio.to_thread(create_web_plisio_invoice, order_id, user, qty)
    except Exception as error:
        logger.warning("Plisio invoice unavailable: %s", type(error).__name__)
        await safe_edit_or_reply(query,
            f"{vip_em('shield')} <b>Chưa thể tạo hóa đơn Plisio.</b>\n"
            "Vui lòng thử lại hoặc chọn thanh toán VietQR.",
            reply_markup=InlineKeyboardMarkup([
                [vip_btn("Chọn Lại Phương Thức Thanh Toán", callback_data=f"select_qty_{qty}", emoji_name="refresh")],
                [vip_btn("Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")],
            ]), parse_mode="HTML")
        return
    total = float(invoice["amount_usdt"])
    expiry = datetime.fromisoformat(invoice["expires_at"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None)
    conn = get_db_connection()
    try:
        conn.execute("""INSERT INTO orders (order_id,user_id,user_name,product_id,amount,status,
            payment_method,currency,quantity,created_at,expires_at)
            VALUES (?,?,?,'gemini_plus',?,'pending','usdt','USDT',?,?,?)
            ON CONFLICT(order_id) DO UPDATE SET amount=excluded.amount,payment_method='usdt',
            currency='USDT',expires_at=excluded.expires_at WHERE orders.status='pending'""",
            (order_id,user.id,user.username or user.first_name,total,qty,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"),expiry.strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
    finally:
        conn.close()
    bill = (
        f"{vip_em('crown')} ═══════ {vip_em('diamond')} <b>HÓA ĐƠN THANH TOÁN USDT VIP</b> {vip_em('diamond')} ═══════ {vip_em('crown')}\n\n"
        f"{vip_em('tag')} <b>Mã đơn hàng VIP:</b> <code>{order_id}</code>\n"
        f"{vip_em('box')} <b>Số lượng:</b> <b>{qty} link</b>\n"
        f"{vip_em('money')} <b>Số tiền thanh toán:</b> <code>{total:g} USDT</code>\n"
        f"{vip_em('globe')} <b>Cổng thanh toán:</b> Plisio (<code>{html.escape(invoice['currency'])}</code>)\n"
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
    if data != "custom_qty":
        AWAITING_QTY_USERS.pop(user_id, None)

    if data == "main_menu":
        safe_name = html.escape(user.first_name) if user and user.first_name else "Quý Khách"
        welcome_text = (
            f"{vip_em('crown', '👑')} ═══════ {vip_em('diamond', '💎')} <b>WOLFMOD VIP STORE</b> {vip_em('diamond', '💎')} ═══════ {vip_em('crown', '👑')}\n\n"
            f"{vip_em('sparkle', '✨')} <i>Kính chào Quý Khách VIP</i> <b>{safe_name}</b> <i>đến với hệ thống dịch vụ số cao cấp!</i>\n\n"
            f"{vip_em('star', '🌟')} <b>DỊCH VỤ ĐẶC QUYỀN ĐANG MỞ BÁN:</b>\n"
            f"<b>Google AI Plus 400GB (Thời Hạn 12 Tháng)</b>\n\n"
            f"{vip_em('chart', '📊')} <b>BẢNG SO SÁNH GIÁ 12 THÁNG SIÊU TIẾT KIỆM:</b>\n"
            f"├─ {vip_em('cross', '❌')} <b>Giá gốc Google:</b> <code>132.000 ₫/tháng</code> → <code>1.584.000 ₫ / 12 tháng</code>\n"
            f"├─ {vip_em('money', '💰')} <b>Giá VIP tại WolfMod:</b> <b>20.000 ₫ / 12 Tháng</b> (hoặc <b>1 USDT</b>)\n"
            f"├─ {vip_em('fire', '🔥')} <b>Mức tiết kiệm:</b> <b>99%</b> <i>(Rẻ hơn 1.564.000 ₫ — Chỉ ~1.6k/tháng)</i>\n"
            f"├─ {vip_em('calendar', '⏳')} <b>Thời hạn sử dụng:</b> <b>12 Tháng (1 Năm trọn vẹn)</b>\n"
            f"└─ {vip_em('shield', '🛡')} <b>Chính sách bảo hành:</b> <b>Bảo hành 24h (1 đổi 1 siêu tốc)</b>\n\n"
            f"{vip_em('lightning', '⚡️')} <b>Hiệu suất:</b> Gemini 3.6 Flash, Deep Research, hạn mức x2\n"
            f"{vip_em('sparkle', '✉️')} <b>Gmail AI:</b> Trợ lý Gemini hỗ trợ hiệu đính trong Gmail\n"
            f"{vip_em('star', '🎨')} <b>Sáng tạo:</b> Mở rộng tạo ảnh, nhạc, video & Gemini Notebook\n"
            f"{vip_em('box', '📦')} <b>Bộ nhớ 400 GB:</b> Dùng chung Gmail, Google Drive, Google Photos\n"
            f"{vip_em('shield', '👨‍👩‍👧‍👦')} <b>Gia đình:</b> Hỗ trợ chia sẻ tối đa 5 thành viên gia đình\n\n"
            f"{vip_em('star', '👇')} <i>Vui lòng chọn đặc quyền bên dưới để tiếp tục:</i>"
        )
        await safe_edit_or_reply(
            query,
            welcome_text,
            reply_markup=main_menu_keyboard(user_id),
            parse_mode="HTML"
        )

    elif data == "buy_gemini_plus":
        prod = get_product("gemini_plus")
        stock = get_stock_count("gemini_plus")
        price_fmt = f"{prod['price']:,} VNĐ"
        usdt_price = get_setting("usdt_price", "1")

        if stock <= 0:
            keyboard = [[vip_btn("🔙 Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")]]
            await safe_edit_or_reply(
                query,
                f"{vip_em('cross', '❌')} <b>Hiện tại kho Gemini Plus VIP đang tạm hết link!</b>\nVui lòng quay lại sau ít phút hoặc liên hệ CSKH VIP.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode="HTML"
            )
            return

        detail_caption = (
            f"{vip_em('crown', '👑')} <b>GOOGLE AI PLUS VIP (12 THÁNG)</b> {vip_em('crown', '👑')}\n\n"
            f"{vip_em('tag', '🌟')} <b>Gói:</b> <code>Google AI Plus 400GB (1 Năm trọn vẹn)</code>\n"
            f"{vip_em('chart', '📊')} <b>So Sánh Giá Tiết Kiệm:</b>\n"
            f"├─ {vip_em('cross', '❌')} Giá gốc Google: <code>132.000 ₫/tháng</code> (<code>1.584.000 ₫/năm</code>)\n"
            f"├─ {vip_em('money', '💰')} <b>Giá VIP WolfMod:</b> <b>{price_fmt} / 12 Tháng</b> (hoặc <b>{usdt_price} USDT</b>)\n"
            f"└─ {vip_em('fire', '🔥')} <b>Mức tiết kiệm:</b> <b>99%</b> (Bảo hành 24h 1 đổi 1 siêu tốc)\n\n"
            f"{vip_em('box', '📦')} <b>Tồn kho sẵn sàng:</b> <code>{stock}</code> link kích hoạt chính chủ\n\n"
            f"{vip_em('lightning', '⚡️')} <i>Vui lòng bấm chọn số lượng link bạn muốn mua bên dưới:</i>"
        )

        keyboard = [
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
            [
                vip_btn("🔙 Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")
            ]
        ]

        banner_path = get_product_banner_path("gemini_plus")
        if banner_path and os.path.exists(banner_path):
            try:
                with open(banner_path, "rb") as f_img:
                    img_bytes = f_img.read()
                bio = io.BytesIO(img_bytes)
                bio.name = "gemini_plus.jpg"
                if query.message:
                    try:
                        await query.message.delete()
                    except Exception:
                        pass
                    await query.message.reply_photo(
                        photo=bio,
                        caption=detail_caption,
                        reply_markup=InlineKeyboardMarkup(keyboard),
                        parse_mode="HTML"
                    )
                    return
            except Exception as e_banner:
                logger.warning(f"Lỗi gửi banner gemini_plus: {e_banner}")

        await safe_edit_or_reply(query, detail_caption, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data.startswith("select_qty_"):
        try:
            qty = max(1, int(data.replace("select_qty_", "")))
        except Exception:
            qty = 1
        stock = get_stock_count("gemini_plus")
        if stock < qty:
            await query.answer(f"Kho hiện chỉ còn {stock} link khả dụng!", show_alert=True)
        await show_order_confirmation(query, user, sku="gemini_plus", qty=qty)

    elif data == "custom_qty":
        AWAITING_QTY_USERS[user_id] = "gemini_plus"
        stock = get_stock_count("gemini_plus")
        prod = get_product("gemini_plus")
        price_fmt = f"{prod['price']:,} VNĐ"
        usdt_price = get_setting("usdt_price", "1")
        prompt_text = (
            f"{vip_em('crown', '👑')} ═══════ {vip_em('memo', '✏️')} <b>NHẬP SỐ LƯỢNG TÙY CHỈNH</b> {vip_em('memo', '✏️')} ═══════ {vip_em('crown', '👑')}\n\n"
            f"Vui lòng gửi tin nhắn chứa <b>số lượng link</b> bạn muốn đặt mua vào đây (Ví dụ: <code>3</code>, <code>5</code>, <code>10</code>...)\n\n"
            f"{vip_em('chart', '🏷')} <b>Đơn giá ưu đãi:</b> <code>{price_fmt}</code> / link (hoặc <code>{usdt_price} USDT</code>)\n"
            f"{vip_em('box', '📦')} <b>Hiện có trong kho:</b> <code>{stock}</code> link sẵn sàng\n\n"
            f"{vip_em('lightning', '⚡️')} <i>Nhập số lượng từ 1 đến 50 link để hệ thống tự động tính hóa đơn VIP!</i>"
        )
        keyboard = [
            [vip_btn("🔙 Hủy & Chọn Lại", callback_data="buy_gemini_plus", emoji_name="cross")]
        ]
        await safe_edit_or_reply(query, prompt_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data in ("create_order_gemini", "create_order_vietqr") or data.startswith("create_order_vietqr_"):
        qty = 1
        if data.startswith("create_order_vietqr_"):
            try:
                qty = max(1, int(data.replace("create_order_vietqr_", "")))
            except Exception:
                qty = 1

        prod = get_product("gemini_plus")
        stock = get_stock_count("gemini_plus")

        if stock < qty:
            await safe_edit_or_reply(
                query,
                f"{vip_em('cross', '❌')} <b>Sản phẩm không đủ số lượng trong kho! (Còn {stock} link)</b>\nVui lòng giảm số lượng đặt mua.",
                reply_markup=InlineKeyboardMarkup([[vip_btn("🔄 Chọn Lại Số Lượng", callback_data="buy_gemini_plus", emoji_name="refresh")]]),
                parse_mode="HTML"
            )
            return

        import secrets
        order_id = f"TGS{secrets.token_hex(4).upper()}"
        amount = prod["price"] * qty
        usdt_unit = float(get_setting("usdt_price", "1"))
        total_usdt = round(usdt_unit * qty, 2)
        total_usdt_str = str(int(total_usdt)) if total_usdt.is_integer() else str(total_usdt)

        now_dt = datetime.now()
        expires_dt = now_dt + timedelta(minutes=10)
        now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
        expires_str = expires_dt.strftime("%Y-%m-%d %H:%M:%S")
        expires_display = expires_dt.strftime("%H:%M:%S")

        conn = get_db_connection()
        conn.execute(
            "INSERT INTO orders (order_id, user_id, user_name, product_id, amount, status, payment_method, currency, quantity, created_at, expires_at) VALUES (?, ?, ?, ?, ?, 'pending', 'vietqr', 'VND', ?, ?, ?)",
            (order_id, user_id, user.username or user.first_name, "gemini_plus", amount, qty, now_str, expires_str)
        )
        conn.commit()
        conn.close()

        # Đồng bộ đơn hàng lên Web Dashboard wolfmod.xyz
        asyncio.create_task(sync_order_to_web_async(order_id, user_id, user.username or user.first_name, amount, "VND", "sepay", qty=qty))

        bank_name = get_setting("bank_name", "ACB")
        bank_account = get_setting("bank_account", "68986683")
        bank_owner = get_setting("bank_owner", "LE DONG HA")

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

    elif data == "create_order_usdt" or data.startswith("create_order_usdt_"):
        suffix = data.removeprefix("create_order_usdt_")
        qty = int(suffix) if suffix.isdigit() else 1
        await show_plisio_checkout(query, user, qty)

    elif data.startswith("switch_pay_usdt_"):
        order_id = data.removeprefix("switch_pay_usdt_")
        conn = get_db_connection()
        order = conn.execute("SELECT * FROM orders WHERE order_id=? AND user_id=?", (order_id, user_id)).fetchone()
        conn.close()
        if order and order["status"] == "pending":
            await show_plisio_checkout(query, user, int(order["quantity"] or 1), order_id)

    elif data.startswith("switch_pay_vietqr_"):
        order_id = data.replace("switch_pay_vietqr_", "")
        conn = get_db_connection()
        order = conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
        qty = 1
        if order and "quantity" in order.keys() and order["quantity"]:
            try:
                qty = max(1, int(order["quantity"]))
            except Exception:
                qty = 1

        prod = get_product("gemini_plus")
        amount = prod["price"] * qty

        conn.execute("UPDATE orders SET payment_method = 'vietqr', currency = 'VND', amount = ? WHERE order_id = ?", (amount, order_id))
        conn.commit()
        conn.close()

        bank_name = get_setting("bank_name", "ACB")
        bank_account = get_setting("bank_account", "68986683")
        bank_owner = get_setting("bank_owner", "LE DONG HA")
        usdt_unit = float(get_setting("usdt_price", "1"))
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
            links = (order["item_delivered"] or "").splitlines()
            if links:
                msg_body, link_buttons = format_delivered_links_message(order_id, links)
                await query.message.reply_text(msg_body, reply_markup=InlineKeyboardMarkup(link_buttons), parse_mode="HTML")
            await query.answer("✅ Đơn hàng đã được bàn giao.", show_alert=True)
            return

        # Kiểm tra nếu đơn hàng đã bị hủy hoặc hết hạn
        if order["status"] in ("cancelled", "expired"):
            await query.answer("❌ Đơn hàng này đã hết hạn 10 phút hoặc đã bị hủy!", show_alert=True)
            await safe_edit_or_reply(
                query,
                f"{vip_em('cross', '❌')} <b>ĐƠN HÀNG <code>{order_id}</code> ĐÃ HẾT HẠN THANH TOÁN!</b>\n\n"
                f"{vip_em('calendar', '⏳')} Thời gian chờ 10 phút cho đơn hàng đã kết thúc và đơn đã được tự động hủy.\n\n"
                f"{vip_em('lightning', '⚡️')} <i>Quý Khách vui lòng tạo đơn hàng mới:</i>",
                reply_markup=InlineKeyboardMarkup([[vip_btn("Đặt Mua Đơn Mới", callback_data="buy_gemini_plus", emoji_name="crown", style="primary")]]),
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
                f"{vip_em('calendar', '⏳')} Đơn hàng chỉ có hiệu lực trong <b>10 phút</b>. Đơn này đã quá hạn và hệ thống đã hủy đơn.\n\n"
                f"{vip_em('lightning', '⚡️')} <i>Quý Khách vui lòng tạo đơn mới để nhận mã QR chuyển khoản hợp lệ:</i>",
                reply_markup=InlineKeyboardMarkup([[vip_btn("Đặt Mua Đơn Mới", callback_data="buy_gemini_plus", emoji_name="crown", style="primary")]]),
                parse_mode="HTML"
            )
            return

        qty = 1
        if "quantity" in order.keys() and order["quantity"]:
            try:
                qty = max(1, int(order["quantity"]))
            except Exception:
                qty = 1

        # 1. Tự động kiểm tra trên Web Backend xem SePay đã xác thực chuyển khoản chưa
        web_stat = check_web_order_status(order_id)
        if web_stat.get("status") in ("paid", "delivered"):
            delivered_links = deliver_product(order_id, user_id)
            if delivered_links:
                await query.answer("✅ Giao dịch đã được xác nhận thành công!", show_alert=True)
                msg_body, link_buttons = format_delivered_links_message(order_id, delivered_links)
                await query.message.reply_text(
                    msg_body,
                    reply_markup=InlineKeyboardMarkup(link_buttons),
                    parse_mode="HTML"
                )
                if ADMIN_ID and str(ADMIN_ID).isdigit():
                    try:
                        links_summary = "\n".join([f"• <code>{lk}</code>" for lk in delivered_links])
                        await context.bot.send_message(
                            chat_id=int(ADMIN_ID),
                            text=(
                                f"{vip_em('lightning', '⚡️')} <b>ĐƠN HÀNG VIP <code>{order_id}</code> ĐÃ ĐƯỢC TỰ ĐỘNG XÁC THỰC!</b>\n"
                                f"{vip_em('user', '👤')} <b>Khách VIP:</b> {user.mention_html()} (ID: <code>{user_id}</code>)\n"
                                f"{vip_em('box', '📦')} <b>Số lượng:</b> <code>{len(delivered_links)} link</code>\n"
                                f"{vip_em('key', '🔑')} <b>Link bàn giao:</b>\n{links_summary}"
                            ),
                            parse_mode="HTML"
                        )
                    except Exception:
                        pass
                return

        pay_method = order["payment_method"] if "payment_method" in order.keys() and order["payment_method"] else "vietqr"
        if pay_method == "usdt":
            method_desc = "USDT qua Plisio"
            usdt_unit = float(get_setting("usdt_price", "1"))
            total_usdt = round(usdt_unit * qty, 2)
            total_usdt_str = str(int(total_usdt)) if total_usdt.is_integer() else str(total_usdt)
            amt_display = f"{total_usdt_str} USDT ({get_setting('usdt_network', 'BEP20')})"
        else:
            method_desc = "VietQR ACB"
            amt_display = f"{order['amount']:,} VNĐ"

        await query.answer("✅ Đã ghi nhận! Bot đang kiểm tra giao dịch và chuyển cho Admin...", show_alert=True)
        await query.message.reply_text(
            f"{vip_em('star', '⏳')} <b>Đơn hàng VIP <code>{order_id}</code> đang được hệ thống đối soát.</b>\n"
            f"Hệ thống đang kiểm tra giao dịch <b>{method_desc}</b> qua hệ thống thanh toán. Bot sẽ tự động xuất link VIP gửi cho bạn ngay khi tiền vào tài khoản hoặc Admin bấm duyệt!\n\n"
            f"{vip_em('calendar', '⏳')} <i>Thời hạn thanh toán: 10 phút kể từ lúc tạo đơn.</i>",
            reply_markup=InlineKeyboardMarkup([[vip_btn("🔙 Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")]]),
            parse_mode="HTML"
        )

        # Gửi thông báo đến Admin nếu có ADMIN_ID kèm nút duyệt và link web dashboard
        if ADMIN_ID and str(ADMIN_ID).isdigit():
            admin_notify = (
                f"{vip_em('crown', '👑')} <b>CÓ ĐƠN HÀNG VIP MỚI CẦN DUYỆT!</b>\n\n"
                f"{vip_em('tag', '🆔')} <b>Đơn:</b> <code>{order_id}</code>\n"
                f"{vip_em('user', '👤')} <b>Khách VIP:</b> {user.mention_html()} (ID: <code>{user_id}</code>)\n"
                f"{vip_em('box', '📦')} <b>Số lượng:</b> <b>{qty} link</b>\n"
                f"{vip_em('card', '💳')} <b>Phương thức:</b> <b>{method_desc}</b>\n"
                f"{vip_em('money', '💵')} <b>Số tiền:</b> <code>{amt_display}</code>\n"
                f"{vip_em('memo', '📝')} <b>Nội dung CK/Memo:</b> <code>{order_id}</code>\n"
                f"{vip_em('calendar', '⏳')} <b>Thời hạn:</b> 10 phút"
            )
            admin_kb = [
                [
                    vip_btn("👑 Duyệt & Bắn Link VIP", callback_data=f"admin_approve_{order_id}", emoji_name="crown", style="success"),
                    vip_btn("❌ Từ chối", callback_data=f"admin_reject_{order_id}", emoji_name="cross", style="danger")
                ],
                [
                    vip_btn("🌐 Kiểm Tra Trên Web Dashboard", url="https://www.wolfmod.xyz/m7k9xq2r/dashboard#tg-sales", emoji_name="globe")
                ]
            ]
            try:
                await context.bot.send_message(
                    chat_id=int(ADMIN_ID),
                    text=admin_notify,
                    reply_markup=InlineKeyboardMarkup(admin_kb),
                    parse_mode="HTML"
                )
            except Exception as e_adm:
                logger.error(f"Không thể gửi thông báo cho Admin: {e_adm}")

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

    elif data == "my_orders":
        conn = get_db_connection()
        rows = conn.execute(
            "SELECT * FROM orders WHERE user_id = ? AND status = 'completed' ORDER BY created_at DESC LIMIT 10",
            (user_id,)
        ).fetchall()
        conn.close()

        if not rows:
            orders_text = (
                f"{vip_em('diamond', '💎')} ═══════ {vip_em('crown', '👑')} <b>LỊCH SỬ ĐƠN HÀNG VIP</b> {vip_em('crown', '👑')} ═══════ {vip_em('diamond', '💎')}\n\n"
                f"{vip_em('box', '📭')} <b>Quý khách chưa có đơn hàng nào đã hoàn tất!</b>\n"
                f"Hãy khám phá các gói đặc quyền tại Sảnh VIP nhé."
            )
        else:
            orders_text = f"{vip_em('diamond', '💎')} ═══════ {vip_em('crown', '👑')} <b>DANH SÁCH ĐƠN HÀNG VIP ĐÃ MUA</b> {vip_em('crown', '👑')} ═══════ {vip_em('diamond', '💎')}\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            for r in rows:
                orders_text += (
                    f"{vip_em('crown', '👑')} <b>Đơn hàng:</b> <code>{r['order_id']}</code>\n"
                    f"{vip_em('calendar', '📅')} <b>Thời gian:</b> <code>{r['created_at']}</code>\n"
                    f"{vip_em('key', '🔗')} <b>Link kích hoạt:</b> <code>{r['item_delivered']}</code>\n"
                    f"──────────────────────────\n"
                )

        keyboard = [
            [vip_btn(" Mua Thêm Gói VIP", callback_data="buy_gemini_plus", emoji_name="crown", style="primary")],
            [vip_btn("Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")]
        ]
        await safe_edit_or_reply(query, orders_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

    elif data == "guide":
        prod = get_product("gemini_plus")
        guide_msg = (
            f"{vip_em('star', '⚜️')} ═══════ {vip_em('book', '📖')} <b>CẨM NANG KÍCH HOẠT VIP</b> {vip_em('book', '📖')} ═══════ {vip_em('star', '⚜️')}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"{vip_em('diamond', '💎')} <b>Quy trình 3 bước kích hoạt Gemini Plus 2TB:</b>\n\n"
            f"{vip_em('num1', '1️⃣')} <b>Bước 1:</b> Chuẩn bị tài khoản Gmail cá nhân của bạn (khuyên dùng Gmail chưa dính gói tranh chấp gia đình).\n\n"
            f"{vip_em('num2', '2️⃣')} <b>Bước 2:</b> Nhấp vào Link nhận được từ bot sau khi thanh toán thành công.\n\n"
            f"{vip_em('num3', '3️⃣')} <b>Bước 3:</b> Google sẽ hiển thị thông báo mời tham gia gói ưu đãi 2TB. Bạn chỉ cần nhấn <b>Chấp nhận (Accept)</b>.\n\n"
            f"{vip_em('star', '💡')} <b>Lưu ý đặc quyền VIP:</b>\n"
            f"{vip_em('check', '✦')} Sử dụng song song trên cả Máy tính (PC), Điện thoại (Android/iOS).\n"
            f"{vip_em('shield', '✦')} Nếu Google yêu cầu thêm phương thức thanh toán để xác minh 0đ, bạn có thể thêm thẻ ngân hàng ảo/thật (Google hoàn toàn không trừ phí).\n\n"
            f"{vip_em('shield', '🛡')} <i>Đội ngũ kỹ thuật WolfMod luôn sẵn sàng đồng hành hỗ trợ Quý Khách 24/7!</i>"
        )
        keyboard = [
            [vip_btn(" Đặt Mua Gói VIP Ngay", callback_data="buy_gemini_plus", emoji_name="lightning", style="success")],
            [vip_btn("Trở Về Sảnh VIP", callback_data="main_menu", emoji_name="back")]
        ]
        await safe_edit_or_reply(query, guide_msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

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
            msg_body, link_buttons = format_delivered_links_message(order_id, delivered_links)
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
        f"├─ {vip_em('box', '📦')} <b>Tổng kho khả dụng:</b> <code>{total_stock}</code> link sẵn sàng bán\n"
        f"├─ {vip_em('shield', '🛡')} <b>Bảo hành:</b> 24h (1 đổi 1 link mới)\n"
        f"└─ {vip_em('lock', '🔒')} <b>Bảo mật:</b> Link được mã hóa và bảo vệ an toàn trong kho\n\n"
        f"{vip_em('check', '✅')} <i>Khách hàng có thể bấm mua và nhận link tự động ngay lập tức!</i>"
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

        if qty > 50:
            await update.message.reply_text(
                f"{vip_em('shield', '⚠️')} <b>Số lượng đặt mua tối đa mỗi đơn là 50 link!</b>\n"
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
                        [vip_btn("Đặt Mua Đơn Mới", callback_data="buy_gemini_plus", emoji_name="crown", style="primary")],
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
        asyncio.create_task(auto_cancel_expired_orders_task(application))

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
