"""Bộ chuỗi giao diện (vi / en) TẬP TRUNG cho bot cửa hàng.

Mọi chữ khách nhìn thấy (menu, mô tả, thanh toán, đơn hàng, thông báo, lỗi) đi qua `t(lang, key, **kwargs)`.
- Ngôn ngữ hỗ trợ: "vi", "en". Mặc định cho khách mới: "en" (khớp website).
- Thiếu khóa ở ngôn ngữ yêu cầu -> dùng bản tiếng Anh và ghi cảnh báo (log); thiếu cả hai -> trả về chính tên khóa.
- Giá trị là HTML hợp lệ của Telegram (<b>, <i>, <code>). Biến chèn vào qua {name}; nơi gọi chịu trách nhiệm html.escape
  dữ liệu người dùng/sản phẩm trước khi truyền vào.
- Chuỗi chỉ dành cho ADMIN (bảng quản trị, /setprice, /addstock...) giữ tiếng Việt, không nằm trong file này.

Thêm chuỗi mới: thêm một mục vào STRINGS với CẢ HAI ngôn ngữ (có test quét khóa bắt buộc đủ vi + en).
"""
from __future__ import annotations

import logging

logger = logging.getLogger("i18n")

SUPPORTED_LANGS = ("vi", "en")
DEFAULT_LANG = "en"          # khách mới
LEGACY_LANG = "vi"           # khách cũ đã có lịch sử đơn hàng
LANG_NAMES = {"vi": "Tiếng Việt", "en": "English"}

# Tỉ giá HIỂN THỊ do chủ shop đặt cho bot: 25.000 VNĐ = 1 USD. Không dùng USDT_VND_RATE của web.
BOT_VND_PER_USD = 25000

_warned: set[tuple[str, str]] = set()


def normalize_lang(lang) -> str:
    """Chuẩn hóa mã ngôn ngữ; giá trị lạ -> mặc định."""
    code = str(lang or "").strip().lower()[:2]
    return code if code in SUPPORTED_LANGS else DEFAULT_LANG


def fmt_vnd(amount) -> str:
    """100000 -> '100,000 VNĐ' (giữ định dạng hiện tại của bot)."""
    return f"{int(amount):,} VNĐ"


def usd_from_vnd(vnd) -> float:
    return round(int(vnd) / BOT_VND_PER_USD, 2)


def fmt_usd(vnd) -> str:
    """100000 -> '4.00 USD'."""
    return f"{usd_from_vnd(vnd):.2f} USD"


STRINGS: dict[str, dict[str, str]] = {
    # ---------- Màn chính / ngôn ngữ ----------
    "guest_name": {"vi": "Quý Khách", "en": "Valued Customer"},
    "welcome_greet": {
        "vi": "<i>Kính chào Quý Khách VIP</i> <b>{name}</b> <i>đến với hệ thống dịch vụ số cao cấp!</i>",
        "en": "<i>Welcome, VIP customer</i> <b>{name}</b><i>, to our premium digital services store!</i>",
    },
    "welcome_hint": {"vi": "<i>Chọn một mục bên dưới:</i>", "en": "<i>Choose an option below:</i>"},
    "btn_shop": {"vi": "Mua hàng", "en": "Shop"},
    "btn_orders": {"vi": "Đơn Hàng VIP Của Tôi", "en": "My VIP Orders"},
    "btn_support": {"vi": "CSKH Trực Tuyến 24/7", "en": "24/7 Live Support"},
    "btn_language": {"vi": "Ngôn ngữ: Tiếng Việt", "en": "Language: English"},
    "btn_back_menu": {"vi": "Trở Về Sảnh VIP", "en": "Back to VIP Lobby"},
    "btn_back_shop": {"vi": "Về Danh Sách Sản Phẩm", "en": "Back to Products"},
    "btn_my_orders": {"vi": "Đơn Hàng Của Tôi", "en": "My Orders"},
    "btn_contact_admin": {"vi": "Liên Hệ Admin", "en": "Contact Admin"},
    "lang_title": {
        "vi": "<b>Chọn ngôn ngữ</b>\nChoose your language",
        "en": "<b>Choose your language</b>\nChọn ngôn ngữ",
    },
    "lang_changed": {"vi": "Đã chuyển sang Tiếng Việt.", "en": "Language set to English."},
    "shop_title": {"vi": "<b>MUA HÀNG</b>", "en": "<b>SHOP</b>"},
    "shop_hint": {
        "vi": "<i>Chọn sản phẩm để xem chi tiết (đầy đủ quyền lợi, giá VNĐ/USD, bảo hành):</i>",
        "en": "<i>Pick a product to see the full details (benefits, VND/USD price, warranty):</i>",
    },
    "shop_empty": {"vi": "Hiện chưa có sản phẩm nào đang mở bán.", "en": "No products are on sale right now."},

    # ---------- Nút sản phẩm ----------
    "btn_product_buy": {"vi": "MUA {name} • {price} (Kho: {stock})", "en": "BUY {name} • {price} (Stock: {stock})"},
    "btn_product_buy_nostock": {"vi": "MUA {name} • {price}", "en": "BUY {name} • {price}"},
    "btn_product_oos": {"vi": "{name} • {price} • HẾT HÀNG", "en": "{name} • {price} • OUT OF STOCK"},
    "soldout_alert": {
        "vi": "Sản phẩm này hiện đã hết hàng. Vui lòng quay lại sau!",
        "en": "This product is out of stock. Please check back later!",
    },
    "product_unavailable": {
        "vi": "<b>Sản phẩm tạm thời chưa mở bán.</b>\nVui lòng quay lại sau hoặc liên hệ CSKH VIP.",
        "en": "<b>This product is not on sale yet.</b>\nPlease check back later or contact VIP support.",
    },

    # ---------- Trang chi tiết sản phẩm ----------
    "lbl_duration": {"vi": "Thời hạn", "en": "Duration"},
    "lbl_price": {"vi": "Giá", "en": "Price"},
    "lbl_features": {"vi": "Quyền lợi", "en": "Benefits"},
    "lbl_delivery": {"vi": "Cách giao hàng", "en": "Delivery"},
    "lbl_warranty": {"vi": "Bảo hành", "en": "Warranty"},
    "lbl_notes": {"vi": "Lưu ý", "en": "Notes"},
    "lbl_stock": {"vi": "Tồn kho", "en": "Stock"},
    "lbl_max_per_order": {"vi": "Tối đa mỗi đơn", "en": "Max per order"},
    "price_line": {"vi": "{vnd} (≈ {usd})", "en": "{vnd} (≈ {usd})"},
    "price_line_usdt": {"vi": "{vnd} (hoặc {usdt} USDT)", "en": "{vnd} (or {usdt} USDT)"},
    "oos_notice": {
        "vi": "<b>Hiện sản phẩm đang HẾT HÀNG.</b> Vui lòng quay lại sau hoặc liên hệ CSKH VIP.",
        "en": "<b>This product is currently OUT OF STOCK.</b> Please check back later or contact VIP support.",
    },
    "pick_qty": {
        "vi": "<i>Vui lòng bấm chọn số lượng bạn muốn mua bên dưới:</i>",
        "en": "<i>Please choose how many you want to buy below:</i>",
    },
    "pick_continue": {"vi": "<i>Bấm bên dưới để tiếp tục:</i>", "en": "<i>Tap below to continue:</i>"},
    "btn_buy_one": {"vi": "Mua Ngay (1 tài khoản)", "en": "Buy Now (1 account)"},
    "btn_buy_now": {"vi": "Mua ngay", "en": "Buy now"},
    "btn_qty_n": {"vi": "{n} Link", "en": "{n} Link"},
    "btn_custom_qty": {"vi": "Nhập Số Lượng Tùy Chỉnh", "en": "Enter a Custom Quantity"},
    "btn_guide": {"vi": "Hướng Dẫn Sản Phẩm Này", "en": "Guide For This Product"},
    "btn_view_product": {"vi": "Xem Sản Phẩm Này", "en": "View This Product"},
    "draft_badge": {"vi": "", "en": ""},

    # ---------- Nhập số lượng ----------
    "qty_not_number": {
        "vi": "<b>Vui lòng chỉ nhập số nguyên dương hợp lệ!</b>\nVí dụ: <code>3</code>, <code>5</code>, <code>10</code>...",
        "en": "<b>Please enter a valid positive whole number only!</b>\nExample: <code>3</code>, <code>5</code>, <code>10</code>...",
    },
    "qty_min": {
        "vi": "<b>Số lượng mua tối thiểu là 1!</b>\nVui lòng nhập lại:",
        "en": "<b>The minimum quantity is 1!</b>\nPlease enter it again:",
    },
    "qty_max": {
        "vi": "<b>Số lượng đặt mua tối đa mỗi đơn là {limit}!</b>\nNếu muốn mua số lượng lớn hơn, vui lòng chia làm nhiều đơn hoặc liên hệ CSKH VIP.",
        "en": "<b>The maximum quantity per order is {limit}!</b>\nFor a larger quantity, please split it into several orders or contact VIP support.",
    },
    "qty_stock_short": {
        "vi": "<b>Kho hiện chỉ còn {stock} khả dụng!</b>\nBạn có thể đặt tối đa <b>{stock}</b> hoặc liên hệ Admin nhập thêm hàng.",
        "en": "<b>Only {stock} left in stock!</b>\nYou can order up to <b>{stock}</b>, or contact the admin to restock.",
    },
    "qty_prompt": {
        "vi": "<b>Nhập số lượng bạn muốn mua</b> (tối đa {limit}, kho còn {stock}):",
        "en": "<b>Enter the quantity you want to buy</b> (max {limit}, {stock} in stock):",
    },
    "out_of_stock_alert": {
        "vi": "Sản phẩm đã hết hàng, không thể tạo đơn!",
        "en": "This product is out of stock, the order cannot be created!",
    },

    "qty_title": {"vi": "NHẬP SỐ LƯỢNG TÙY CHỈNH", "en": "ENTER A CUSTOM QUANTITY"},
    "qty_body": {
        "vi": "Vui lòng gửi tin nhắn chứa <b>số lượng</b> bạn muốn đặt mua vào đây (Ví dụ: <code>3</code>, <code>5</code>, <code>10</code>...)",
        "en": "Please send a message with the <b>quantity</b> you want to buy (Example: <code>3</code>, <code>5</code>, <code>10</code>...)",
    },
    "qty_in_stock": {"vi": "Hiện có trong kho", "en": "Currently in stock"},
    "qty_range_hint": {
        "vi": "<i>Nhập số lượng từ 1 đến {limit} để hệ thống tự động tính hóa đơn VIP!</i>",
        "en": "<i>Enter a quantity from 1 to {limit} and the system will calculate your VIP invoice!</i>",
    },
    "btn_cancel_reselect": {"vi": "Hủy & Chọn Lại", "en": "Cancel & Choose Again"},
    "alert_sold_out": {"vi": "Sản phẩm đã hết hàng!", "en": "This product is out of stock!"},
    "alert_max_per_order": {"vi": "Mỗi đơn tối đa {limit} sản phẩm!", "en": "Maximum {limit} per order!"},
    "alert_only_left": {"vi": "Kho hiện chỉ còn {stock}!", "en": "Only {stock} left in stock!"},
    "price_unavailable": {
        "vi": "<b>Chưa thể lấy giá mới nhất.</b> Vui lòng thử lại sau.",
        "en": "<b>The latest price could not be loaded.</b> Please try again later.",
    },
    "price_unavailable_alert": {
        "vi": "Chưa thể lấy giá mới nhất, vui lòng thử lại.",
        "en": "The latest price could not be loaded, please try again.",
    },
    "stock_insufficient": {
        "vi": "<b>Sản phẩm không đủ số lượng trong kho! (Còn {stock})</b>\nVui lòng giảm số lượng đặt mua.",
        "en": "<b>Not enough stock for this quantity! ({stock} left)</b>\nPlease reduce the quantity.",
    },
    "sku_not_sold_alert": {
        "vi": "Sản phẩm của đơn này hiện không còn được bán.",
        "en": "The product of this order is no longer on sale.",
    },
    "order_invalid_alert": {"vi": "Đơn hàng không hợp lệ.", "en": "Invalid order."},
    "expired_auto": {
        "vi": "<b>ĐƠN HÀNG <code>{order_id}</code> ĐÃ HẾT HẠN THANH TOÁN!</b>\n\nThời hạn <b>10 phút</b> cho đơn hàng đã kết thúc nên hệ thống đã tự động hủy giao dịch này.\n\n<i>Nếu Quý Khách vẫn có nhu cầu mua gói, vui lòng bấm nút bên dưới để đặt đơn mới:</i>",
        "en": "<b>ORDER <code>{order_id}</code> HAS EXPIRED!</b>\n\nThe <b>10-minute</b> window ended, so the system cancelled this transaction automatically.\n\n<i>If you still want this plan, tap the button below to place a new order:</i>",
    },

    # ---------- Xác nhận đơn ----------
    "confirm_title": {"vi": "XÁC NHẬN ĐƠN HÀNG VIP", "en": "VIP ORDER CONFIRMATION"},
    "confirm_product": {"vi": "Sản phẩm", "en": "Product"},
    "confirm_qty": {"vi": "Số lượng đặt mua", "en": "Quantity"},
    "confirm_unit": {"vi": "Đơn giá", "en": "Unit price"},
    "confirm_total": {"vi": "TỔNG TIỀN THANH TOÁN", "en": "TOTAL TO PAY"},
    "confirm_duration": {"vi": "Thời hạn sử dụng", "en": "Duration"},
    "confirm_choose_method": {
        "vi": "<i>Vui lòng chọn phương thức thanh toán bên dưới. Thông tin nhận hàng được gửi tự động khi thanh toán được xác nhận:</i>",
        "en": "<i>Please choose a payment method below. Your delivery details are sent automatically once payment is confirmed:</i>",
    },
    "or_usdt": {"vi": "hoặc", "en": "or"},
    "btn_pay_vietqr": {"vi": "Thanh Toán VietQR ({total})", "en": "Pay with VietQR ({total})"},
    "btn_pay_usdt": {"vi": "Thanh Toán USDT ({total} USDT)", "en": "Pay with USDT ({total} USDT)"},
    "btn_reselect_qty": {"vi": "Chọn Lại Số Lượng", "en": "Change Quantity"},
    "btn_reselect_method": {"vi": "Chọn Lại Phương Thức Thanh Toán", "en": "Choose Another Payment Method"},

    # ---------- Hóa đơn VietQR ----------
    "bill_title": {"vi": "HÓA ĐƠN GIAO DỊCH VIETQR VIP", "en": "VIETQR VIP INVOICE"},
    "bill_order_id": {"vi": "Mã đơn hàng VIP", "en": "VIP order ID"},
    "bill_service": {"vi": "Dịch vụ", "en": "Service"},
    "bill_qty": {"vi": "Số lượng", "en": "Quantity"},
    "bill_amount": {"vi": "Số tiền cần thanh toán", "en": "Amount to pay"},
    "bill_bank": {"vi": "Ngân hàng", "en": "Bank"},
    "bill_bank_suffix": {"vi": "(Ngân Hàng Á Châu)", "en": "(Asia Commercial Bank)"},
    "bill_account": {"vi": "Số tài khoản", "en": "Account number"},
    "bill_owner": {"vi": "Chủ tài khoản", "en": "Account holder"},
    "bill_memo": {"vi": "Nội dung chuyển khoản", "en": "Transfer note"},
    "bill_deadline": {"vi": "Thời hạn thanh toán", "en": "Payment deadline"},
    "bill_deadline_value": {
        "vi": "<b>10 phút</b> (Hết hạn lúc: <code>{expires}</code>)",
        "en": "<b>10 minutes</b> (Expires at: <code>{expires}</code>)",
    },
    "bill_ten_minutes": {"vi": "10 phút", "en": "10 minutes"},
    "bill_tip": {
        "vi": "<i>Mẹo VIP: Quét mã QR ACB phía trên trên App ngân hàng để hệ thống tự điền đúng STK &amp; Nội dung!</i>",
        "en": "<i>VIP tip: scan the ACB QR code above in your banking app to auto-fill the right account &amp; transfer note!</i>",
    },
    "bill_warning": {
        "vi": "<i>Lưu ý: Đơn hàng sẽ tự động hủy sau 10 phút nếu chưa hoàn tất chuyển tiền.</i>",
        "en": "<i>Note: the order is cancelled automatically after 10 minutes if the transfer is not completed.</i>",
    },
    "btn_paid_done": {"vi": "TÔI ĐÃ CHUYỂN KHOẢN XONG", "en": "I HAVE PAID"},
    "btn_switch_usdt": {"vi": "Đổi Sang Thanh Toán USDT ({total} USDT)", "en": "Switch to USDT Payment ({total} USDT)"},
    "btn_cancel_tx": {"vi": "HỦY GIAO DỊCH NÀY", "en": "CANCEL THIS TRANSACTION"},
    "btn_back_lobby": {"vi": "VỀ SẢNH MENU CHÍNH", "en": "BACK TO MAIN MENU"},

    # ---------- Hóa đơn USDT ----------
    "usdt_bill_title": {"vi": "HÓA ĐƠN THANH TOÁN USDT VIP", "en": "VIP USDT INVOICE"},
    "usdt_amount": {"vi": "Số tiền thanh toán", "en": "Amount to pay"},
    "usdt_gateway": {"vi": "Cổng thanh toán", "en": "Payment gateway"},
    "usdt_deadline": {"vi": "Hạn thanh toán", "en": "Payment deadline"},
    "usdt_bill_hint": {
        "vi": "<i>Bấm nút bên dưới để mở hóa đơn và mã QR Plisio. Chuyển đúng số tiền và mạng được ghi trên hóa đơn.</i>",
        "en": "<i>Tap the button below to open the Plisio invoice and QR code. Send the exact amount on the network shown on the invoice.</i>",
    },
    "btn_pay_plisio": {"vi": "Thanh Toán USDT Qua Plisio", "en": "Pay USDT via Plisio"},
    "btn_check_paid": {"vi": "TÔI ĐÃ THANH TOÁN — KIỂM TRA", "en": "I HAVE PAID — CHECK"},
    "plisio_minimum": {
        "vi": "<b>Plisio yêu cầu hóa đơn lớn hơn {minimum} USDT trên mạng {network}.</b>\n\nVui lòng chọn VietQR hoặc tăng số lượng để tổng tiền vượt mức này.",
        "en": "<b>Plisio requires an invoice above {minimum} USDT on the {network} network.</b>\n\nPlease choose VietQR or increase the quantity so the total exceeds this minimum.",
    },
    "plisio_unavailable": {
        "vi": "<b>Chưa thể tạo hóa đơn Plisio.</b>\nVui lòng thử lại hoặc chọn thanh toán VietQR.",
        "en": "<b>The Plisio invoice could not be created.</b>\nPlease try again or pay with VietQR.",
    },

    # ---------- Kiểm tra thanh toán / đơn hàng ----------
    "order_not_found": {"vi": "Đơn hàng không tồn tại!", "en": "Order not found!"},
    "delivery_sent": {"vi": "Đã gửi link.", "en": "Delivery sent."},
    "delivery_retry": {"vi": "Đang thử gửi link, vui lòng thử lại.", "en": "Retrying delivery, please try again."},
    "order_delivered_alert": {"vi": "Đơn hàng đã được bàn giao.", "en": "This order has already been delivered."},
    "payment_confirmed_alert": {"vi": "Giao dịch đã được xác nhận thành công!", "en": "Payment confirmed successfully!"},
    "payment_waiting_alert": {
        "vi": "Đã nhận thanh toán; đang chờ xử lý link.",
        "en": "Payment received; delivery is being processed.",
    },
    "pay_confirmed_head": {
        "vi": "<b>Thanh toán đã được xác nhận cho đơn <code>{order_id}</code>.</b>",
        "en": "<b>Payment confirmed for order <code>{order_id}</code>.</b>",
    },
    "pay_no_auto": {
        "vi": "Giao hàng tự động chưa thể thực hiện lúc này.",
        "en": "Automatic delivery is not possible right now.",
    },
    "pay_contact_admin": {
        "vi": "Vui lòng liên hệ admin <b>@wolfmodyt</b> để được hỗ trợ.",
        "en": "Please contact admin <b>@wolfmodyt</b> for help.",
    },
    "order_expired_alert": {
        "vi": "Đơn hàng này đã hết hạn 10 phút hoặc đã bị hủy!",
        "en": "This order has expired (10 minutes) or was cancelled!",
    },
    "order_expired_alert_short": {"vi": "Đơn hàng đã hết hạn 10 phút!", "en": "The order has expired (10 minutes)!"},
    "order_expired_text": {
        "vi": "<b>ĐƠN HÀNG <code>{order_id}</code> ĐÃ HẾT HẠN THANH TOÁN!</b>\n\nThời gian chờ 10 phút cho đơn hàng đã kết thúc và đơn đã được tự động hủy. Nếu đã chuyển khoản, vui lòng liên hệ @wolfmodyt để được hỗ trợ.\n\n<i>Quý Khách vui lòng tạo đơn hàng mới:</i>",
        "en": "<b>ORDER <code>{order_id}</code> HAS EXPIRED!</b>\n\nThe 10-minute payment window ended and the order was cancelled automatically. If you already transferred money, please contact @wolfmodyt for help.\n\n<i>Please place a new order:</i>",
    },
    "order_rejected": {
        "vi": "Rất tiếc, đơn hàng <code>{order_id}</code> của bạn đã bị từ chối do không tìm thấy giao dịch hợp lệ. Vui lòng liên hệ Admin nếu có nhầm lẫn.",
        "en": "Sorry, your order <code>{order_id}</code> was rejected because no valid transaction was found. Please contact the Admin if this is a mistake.",
    },
    "btn_new_order": {"vi": "Đặt Mua Đơn Mới", "en": "Place a New Order"},
    "check_title": {"vi": "KIỂM TRA THANH TOÁN VIP", "en": "VIP PAYMENT CHECK"},
    "check_order": {"vi": "Mã đơn", "en": "Order ID"},
    "check_paid_no_link": {
        "vi": "<b>Thanh toán đã được xác nhận nhưng link chưa sẵn sàng.</b>",
        "en": "<b>Payment is confirmed but the delivery is not ready yet.</b>",
    },
    "check_not_received": {
        "vi": "<b>Hệ thống chưa nhận được thanh toán cho đơn hàng này.</b>",
        "en": "<b>The system has not received a payment for this order yet.</b>",
    },
    "check_unknown": {
        "vi": "<b>Hiện chưa kiểm tra được trạng thái thanh toán.</b>",
        "en": "<b>The payment status cannot be checked right now.</b>",
    },
    "check_footer": {
        "vi": "Nếu bạn đã chuyển khoản, vui lòng liên hệ admin <b>@wolfmodyt</b> để được hỗ trợ.\nNếu chưa chuyển, hãy thanh toán theo hóa đơn rồi bấm kiểm tra lại.",
        "en": "If you already paid, please contact admin <b>@wolfmodyt</b> for help.\nIf not, pay according to the invoice and tap check again.",
    },
    "btn_check_again": {"vi": "Kiểm Tra Thanh Toán", "en": "Check Payment"},
    "cancelled_text": {
        "vi": "<b>Đã hủy giao dịch đơn hàng <code>{order_id}</code>.</b>",
        "en": "<b>Order <code>{order_id}</code> has been cancelled.</b>",
    },

    # ---------- Giao hàng ----------
    "deliv_success": {
        "vi": "<b>THANH TOÁN THÀNH CÔNG ĐƠN HÀNG <code>{order_id}</code>!</b>",
        "en": "<b>PAYMENT SUCCESSFUL FOR ORDER <code>{order_id}</code>!</b>",
    },
    "deliv_confirmed": {"vi": "Hệ thống đã xác nhận thanh toán của bạn.", "en": "Your payment has been confirmed."},
    "deliv_product": {"vi": "Sản phẩm", "en": "Product"},
    "deliv_items": {"vi": "Thông tin nhận hàng ({n})", "en": "Delivery details ({n})"},
    "deliv_thanks_support": {
        "vi": "Cảm ơn Quý Khách đã tin tưởng dịch vụ VIP của WolfMod! Cần hỗ trợ vui lòng liên hệ @wolfmodyt.",
        "en": "Thank you for trusting WolfMod VIP services! Need help? Contact @wolfmodyt.",
    },
    "deliv_thanks": {
        "vi": "Cảm ơn Quý Khách đã tin tưởng dịch vụ VIP của WolfMod!",
        "en": "Thank you for trusting WolfMod VIP services!",
    },
    "deliv_plus_single_title": {
        "vi": "<b>Link nhận Google AI Plus 400GB (12 Tháng - BH 24h):</b>",
        "en": "<b>Your Google AI Plus 400GB link (12 months, 24h warranty):</b>",
    },
    "deliv_plus_multi_qty": {
        "vi": "<b>Số lượng:</b> <code>{qty} link</code> Google AI Plus 400GB (12 Tháng - BH 24h)",
        "en": "<b>Quantity:</b> <code>{qty} links</code> Google AI Plus 400GB (12 months, 24h warranty)",
    },
    "deliv_list_title": {"vi": "<b>DANH SÁCH {qty} LINK KÍCH HOẠT:</b>", "en": "<b>LIST OF {qty} ACTIVATION LINKS:</b>"},
    "deliv_link_n": {"vi": "Link #{idx}", "en": "Link #{idx}"},
    "deliv_howto_title": {"vi": "<b>Hướng dẫn kích hoạt 3 bước:</b>", "en": "<b>Activation in 3 steps:</b>"},
    "deliv_step1_single": {
        "vi": "Mở link trên trình duyệt có Gmail cá nhân của bạn.",
        "en": "Open the link in a browser signed in to your personal Gmail.",
    },
    "deliv_step1_multi": {
        "vi": "Mở từng link trên trình duyệt có Gmail tương ứng.",
        "en": "Open each link in a browser signed in to the matching Gmail.",
    },
    "deliv_step2": {
        "vi": "Bấm 'Bắt đầu dùng thử' hoặc 'Nhận ưu đãi' 2TB.",
        "en": "Tap 'Start trial' or 'Get offer' (2TB).",
    },
    "deliv_step3": {"vi": "Hoàn tất xác nhận trên Google One.", "en": "Finish the confirmation on Google One."},
    "offer_title": {"vi": "<b>Offer link của bạn ({n}):</b>", "en": "<b>Your offer link ({n}):</b>"},
    "offer_howto_title": {"vi": "<b>Cách kích hoạt:</b>", "en": "<b>How to activate:</b>"},
    "offer_step1": {"vi": "Mở offer link.", "en": "Open the offer link."},
    "offer_step2": {"vi": "Đăng nhập tài khoản ChatGPT của bạn.", "en": "Sign in to your ChatGPT account."},
    "offer_step3": {"vi": "Làm theo màn hình kích hoạt.", "en": "Follow the activation screen."},
    "offer_single_use": {"vi": "Link chỉ dùng được một lần. Link lỗi: liên hệ @wolfmodyt kèm mã đơn.", "en": "The link works only once. If it fails, contact @wolfmodyt with your order ID."},
    "btn_open_offer": {"vi": "Mở Offer Link", "en": "Open Offer Link"},
    "btn_open_link": {"vi": "Mở Link Kích Hoạt VIP", "en": "Open VIP Activation Link"},
    "btn_open_link_n": {"vi": "Mở Link #{idx}", "en": "Open Link #{idx}"},

    # ---------- Danh sách đơn hàng ----------
    "orders_title": {"vi": "ĐƠN HÀNG VIP ĐÃ MUA", "en": "YOUR VIP ORDERS"},
    "orders_empty": {"vi": "<b>Bạn chưa có đơn hàng nào đã giao.</b>", "en": "<b>You have no delivered orders yet.</b>"},
    "orders_order": {"vi": "Đơn hàng", "en": "Order"},
    "orders_product": {"vi": "Sản phẩm", "en": "Product"},
    "orders_time": {"vi": "Thời gian", "en": "Time"},
    "orders_status": {"vi": "Trạng thái", "en": "Status"},
    "orders_status_delivered": {"vi": "Đã giao", "en": "Delivered"},
    "orders_label_link": {"vi": "Link kích hoạt", "en": "Activation link"},
    "orders_label_info": {"vi": "Thông tin nhận hàng", "en": "Delivery details"},
    "orders_truncated": {"vi": "… (rút gọn, {n} dòng trong đơn)", "en": "… (truncated, {n} lines in the order)"},
    "orders_page": {"vi": "<i>Trang {page}/{pages} • {total} đơn</i>", "en": "<i>Page {page}/{pages} • {total} orders</i>"},
    "btn_prev_page": {"vi": "Trang trước", "en": "Previous"},
    "btn_next_page": {"vi": "Trang sau", "en": "Next"},
    "btn_guide_for": {"vi": "Hướng dẫn: {name}", "en": "Guide: {name}"},

    # ---------- Hướng dẫn ----------
    "guide_none": {
        "vi": "Sản phẩm này chưa có hướng dẫn riêng. Nếu cần hỗ trợ, vui lòng liên hệ CSKH kèm mã đơn hàng.",
        "en": "This product has no dedicated guide yet. If you need help, please contact support with your order ID.",
    },
    "guide_moved": {
        "vi": "Hướng dẫn nằm trong từng sản phẩm (màn chi tiết sản phẩm) và trong đơn hàng của sản phẩm đó.",
        "en": "Guides now live inside each product (its detail page) and inside the orders of that product.",
    },
    "guide_step": {"vi": "Bước {n}", "en": "Step {n}"},
    "guide_footer": {
        "vi": "<i>Đội ngũ kỹ thuật WolfMod luôn sẵn sàng đồng hành hỗ trợ Quý Khách 24/7!</i>",
        "en": "<i>The WolfMod technical team is here to support you 24/7!</i>",
    },

    # ---------- Hỗ trợ ----------
    "support_title": {"vi": "TRUNG TÂM HỖ TRỢ VIP 24/7", "en": "VIP SUPPORT CENTER 24/7"},
    "support_body": {
        "vi": "Quý khách cần hỗ trợ kích hoạt, kiểm tra đơn hàng hoặc tư vấn kỹ thuật, vui lòng liên hệ trực tiếp Admin &amp; CSKH VIP:",
        "en": "Need help with activation, your orders or technical advice? Contact the Admin &amp; VIP support directly:",
    },
    "support_admin": {"vi": "Admin Quản Trị", "en": "Administrator"},
    "support_hours": {"vi": "Thời gian hỗ trợ", "en": "Support hours"},
    "support_hours_value": {
        "vi": "Trực tuyến 24/7 (Phản hồi &lt; 3 phút)",
        "en": "Online 24/7 (reply in &lt; 3 minutes)",
    },
    "support_web": {"vi": "Cổng Web Portal", "en": "Web portal"},
    "support_footer": {
        "vi": "<i>WolfMod cam kết bảo hành uy tín và hỗ trợ kỹ thuật tận tâm cho Quý Khách!</i>",
        "en": "<i>WolfMod stands behind its warranty and dedicated technical support!</i>",
    },
    "btn_msg_admin": {"vi": "Nhắn Tin Cho Admin @{contact}", "en": "Message Admin @{contact}"},
    "btn_back_main": {"vi": "Trở Về Menu Chính", "en": "Back to Main Menu"},

    # ---------- Thông báo nhập kho (gửi cho từng nhóm ngôn ngữ) ----------
    "stock_title": {"vi": "HÀNG MỚI VỀ KHO", "en": "NEW STOCK ARRIVED"},
    "stock_added": {"vi": "Vừa nhập thêm <b>+{added}</b>", "en": "Just restocked <b>+{added}</b>"},
    "stock_price": {"vi": "Giá", "en": "Price"},
    "stock_available": {"vi": "Kho khả dụng", "en": "Available stock"},
    "stock_available_value": {"vi": "{stock} sẵn sàng", "en": "{stock} available"},
}

# Khóa thật sự cần có đủ 2 ngôn ngữ nhưng được phép là chuỗi rỗng.
ALLOW_EMPTY = {"draft_badge"}


def t(lang, key: str, **kwargs) -> str:
    """Dịch `key` sang `lang`; thiếu bản dịch -> tiếng Anh + log cảnh báo; thiếu khóa -> trả về khóa."""
    code = normalize_lang(lang)
    entry = STRINGS.get(key)
    if entry is None:
        if (code, key) not in _warned:
            _warned.add((code, key))
            logger.warning("i18n: missing key %r (lang=%s)", key, code)
        return key
    text = entry.get(code)
    if text is None or (text == "" and key not in ALLOW_EMPTY):
        if (code, key) not in _warned:
            _warned.add((code, key))
            logger.warning("i18n: missing %s translation for %r, falling back to English", code, key)
        text = entry.get("en", key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            logger.warning("i18n: bad format arguments for %r (lang=%s)", key, code)
            return text
    return text
