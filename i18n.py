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


def fmt_money(lang, vnd, usd=None) -> str:
    """vi: giữ nguyên '125,000 VNĐ'. en: '125,000 VND (~$5.00)'; không có usd thì chỉ '125,000 VND' (không bịa tỉ giá)."""
    if normalize_lang(lang) != "en":
        return fmt_vnd(vnd)
    text = f"{int(vnd):,} VND"
    if usd is not None:
        text += f" (~${float(usd):,.2f})"
    return text


def fmt_money_rate(lang, vnd) -> str:
    """Như fmt_money nhưng USD quy đổi theo tỉ giá hiển thị sẵn có của bot (BOT_VND_PER_USD) — dùng cho ví/nạp ví."""
    return fmt_money(lang, vnd, usd_from_vnd(vnd) if normalize_lang(lang) == "en" else None)


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
    "btn_orders": {"vi": "Đơn đã mua", "en": "My Orders"},
    "btn_support": {"vi": "Hỗ trợ", "en": "Support"},
    "btn_wallet": {"vi": "Ví của tôi", "en": "My Wallet"},
    "btn_community": {"vi": "Cộng đồng", "en": "Community"},
    "btn_pay_binance": {
        "vi": "USDT — Binance Pay (dán ID lệnh)",
        "en": "USDT — Binance Pay (paste order ID)",
    },
    "btn_pay_bep20": {
        "vi": "USDT BEP20 — Quét QR ví",
        "en": "USDT BEP20 — Scan wallet QR",
    },
    "btn_bep20_check": {
        "vi": "TÔI ĐÃ THANH TOÁN — KIỂM TRA",
        "en": "I'VE PAID — CHECK",
    },
    "btn_binance_paste": {
        "vi": "TÔI ĐÃ CHUYỂN — DÁN ID LỆNH",
        "en": "I'VE SENT IT — PASTE ORDER ID",
    },
    "pay_method_disabled": {
        "vi": "Phương thức này chưa được bật.",
        "en": "This payment method is not enabled.",
    },
    "pay_invalid_qty": {
        "vi": "Số lượng không hợp lệ.",
        "en": "Invalid quantity.",
    },
    "pay_order_not_found": {
        "vi": "Không tìm thấy đơn hàng.",
        "en": "Order not found.",
    },
    "pay_order_not_pending": {
        "vi": "Đơn này không còn chờ thanh toán.",
        "en": "This order is no longer awaiting payment.",
    },
    "pay_err_out_of_stock": {
        "vi": "Sản phẩm hiện đã hết hàng.",
        "en": "This product is currently out of stock.",
    },
    "pay_err_busy": {
        "vi": "Hệ thống đang bận, vui lòng thử lại sau ít phút.",
        "en": "The system is busy, please try again in a few minutes.",
    },
    "pay_err_too_many_pending": {
        "vi": "Bạn đang có quá nhiều đơn chờ thanh toán. Vui lòng hoàn tất hoặc hủy bớt đơn cũ.",
        "en": "You have too many orders awaiting payment. Please complete or cancel some older orders.",
    },
    "bep20_err_product_unavailable": {
        "vi": "Sản phẩm này chưa hỗ trợ thanh toán USDT BEP20 hoặc không đủ số lượng cho phép.",
        "en": "This product does not support USDT BEP20 payment yet, or the quantity is not allowed.",
    },
    "bep20_err_generic": {
        "vi": "Chưa thể tạo hóa đơn USDT BEP20. Vui lòng thử lại sau hoặc chọn phương thức khác.",
        "en": "Could not create the USDT BEP20 invoice. Please try again later or choose another payment method.",
    },
    "binance_err_product_unavailable": {
        "vi": "Sản phẩm này chưa hỗ trợ thanh toán Binance Pay hoặc không đủ số lượng cho phép.",
        "en": "This product does not support Binance Pay yet, or the quantity is not allowed.",
    },
    "binance_err_generic": {
        "vi": "Chưa thể tạo hóa đơn Binance Pay. Vui lòng thử lại sau hoặc chọn phương thức khác.",
        "en": "Could not create the Binance Pay invoice. Please try again later or choose another payment method.",
    },
    "bep20_invoice": {
        "vi": "{crown} ═══════ {diamond} <b>HÓA ĐƠN USDT BEP20</b> {diamond} ═══════ {crown}\n\n{tag} <b>Mã đơn:</b> <code>{order}</code>\n{box} <b>Sản phẩm:</b> {product} × {qty}\n{globe} <b>Mạng:</b> BNB Smart Chain (BEP20) — token <b>USDT</b>\n{money} <b>Số tiền CHÍNH XÁC:</b> <code>{amount}</code> USDT\n{card} <b>Địa chỉ ví nhận:</b>\n<code>{address}</code>\n{calendar} <b>Hạn thanh toán:</b> <code>{deadline}</code>\n\n{shield} <i>Chỉ chuyển USDT trên mạng BEP20 (BSC), đúng từng chữ số ở trên (không làm tròn). Chuyển sai mạng, sai token hoặc sai số tiền sẽ không được xác nhận tự động và có thể mất tiền.</i>\n{lightning} <i>Sau khi chuyển, bấm “Tôi đã thanh toán — kiểm tra”. Cần khoảng {confirmations} xác nhận mạng.</i>",
        "en": "{crown} ═══════ {diamond} <b>USDT BEP20 INVOICE</b> {diamond} ═══════ {crown}\n\n{tag} <b>Order ID:</b> <code>{order}</code>\n{box} <b>Product:</b> {product} × {qty}\n{globe} <b>Network:</b> BNB Smart Chain (BEP20) — token <b>USDT</b>\n{money} <b>EXACT amount:</b> <code>{amount}</code> USDT\n{card} <b>Receiving wallet address:</b>\n<code>{address}</code>\n{calendar} <b>Pay before:</b> <code>{deadline}</code>\n\n{shield} <i>Send USDT only on the BEP20 (BSC) network, with exactly the digits above (no rounding). A wrong network, wrong token or wrong amount will not be confirmed automatically and you may lose your funds.</i>\n{lightning} <i>After sending, tap “I've paid — check”. About {confirmations} network confirmations are needed.</i>",
    },
    "binance_invoice": {
        "vi": "{crown} ═══════ {diamond} <b>HÓA ĐƠN USDT — BINANCE PAY</b> {diamond} ═══════ {crown}\n\n{tag} <b>Mã đơn:</b> <code>{order}</code>\n{box} <b>Sản phẩm:</b> {product} × {qty}\n{money} <b>Số tiền:</b> <code>{amount}</code> USDT\n{card} <b>Binance Pay ID nhận:</b> <code>{receiver}</code>\n{calendar} <b>Hạn thanh toán:</b> <code>{deadline}</code>\n\n{shield} <i>Mở Binance → Pay → Gửi, nhập Binance Pay ID ở trên và chuyển ĐÚNG số tiền trên bằng USDT. Sai số tiền hoặc sai loại tiền sẽ không được xác nhận tự động.</i>\n{lightning} <i>Sau khi chuyển, GIỮ LẠI “ID lệnh” (Order ID) trong chi tiết giao dịch, bấm nút bên dưới rồi dán ID lệnh vào đây.</i>",
        "en": "{crown} ═══════ {diamond} <b>USDT INVOICE — BINANCE PAY</b> {diamond} ═══════ {crown}\n\n{tag} <b>Order ID:</b> <code>{order}</code>\n{box} <b>Product:</b> {product} × {qty}\n{money} <b>Amount:</b> <code>{amount}</code> USDT\n{card} <b>Receiving Binance Pay ID:</b> <code>{receiver}</code>\n{calendar} <b>Pay before:</b> <code>{deadline}</code>\n\n{shield} <i>Open Binance → Pay → Send, enter the Binance Pay ID above and send EXACTLY the amount above in USDT. A wrong amount or wrong currency will not be confirmed automatically.</i>\n{lightning} <i>After sending, KEEP the transaction “Order ID” from the payment details, tap the button below, then paste the order ID here.</i>",
    },
    "binance_prompt": {
        "vi": "{tag} <b>Gửi ID lệnh Binance Pay</b> cho đơn <code>{order}</code>\n\nDán dãy số ID lệnh (Order ID) trong chi tiết giao dịch Binance, ví dụ: <code>453343187271753728</code>\n{shield} <i>Chỉ gửi ID lệnh (toàn chữ số). Mỗi đơn chỉ được thử tối đa vài lần.</i>",
        "en": "{tag} <b>Send your Binance Pay order ID</b> for order <code>{order}</code>\n\nPaste the order ID from your Binance transaction details, for example: <code>453343187271753728</code>\n{shield} <i>Send the order ID only (digits only). Each order allows only a few attempts.</i>",
    },
    "binance_reply_not_found": {
        "vi": "Chưa tìm thấy giao dịch với ID này. Hãy kiểm tra lại ID lệnh và thử lại sau 1–2 phút.",
        "en": "No transaction found with this ID. Please check the order ID and try again in 1–2 minutes.",
    },
    "binance_reply_amount_mismatch": {
        "vi": "Giao dịch có số tiền khác với hóa đơn. Hệ thống chỉ chấp nhận ĐÚNG số tiền trên hóa đơn.",
        "en": "The transaction amount differs from the invoice. Only the EXACT invoice amount is accepted.",
    },
    "binance_reply_invalid_transaction": {
        "vi": "Giao dịch này không hợp lệ cho đơn hàng (sai loại tiền, người nhận hoặc loại giao dịch).",
        "en": "This transaction is not valid for the order (wrong currency, recipient or transaction type).",
    },
    "binance_reply_before_order": {
        "vi": "Giao dịch này được thực hiện trước khi tạo đơn nên không thể dùng cho đơn này.",
        "en": "This transaction was made before the order was created, so it cannot be used for this order.",
    },
    "binance_reply_already_used": {
        "vi": "ID lệnh này đã được dùng cho một đơn khác.",
        "en": "This order ID has already been used for another order.",
    },
    "binance_reply_too_fast": {
        "vi": "Bạn thử hơi nhanh, vui lòng đợi khoảng 10 giây rồi gửi lại.",
        "en": "You are trying too fast, please wait about 10 seconds and send it again.",
    },
    "binance_reply_too_many_attempts": {
        "vi": "Đơn này đã thử quá số lần cho phép. Vui lòng liên hệ admin kèm mã đơn.",
        "en": "This order has exceeded the allowed number of attempts. Please contact the admin with your order ID.",
    },
    "binance_reply_unavailable": {
        "vi": "Tạm thời chưa kiểm tra được với Binance. Đơn của bạn không bị ảnh hưởng, vui lòng thử lại sau ít phút.",
        "en": "Binance cannot be checked right now. Your order is not affected, please try again in a few minutes.",
    },
    "binance_reply_config": {
        "vi": "Phương thức này đang được cấu hình. Vui lòng liên hệ admin kèm mã đơn.",
        "en": "This method is being configured. Please contact the admin with your order ID.",
    },
    "binance_reply_rejected": {
        "vi": "Không thể xác nhận giao dịch này.",
        "en": "This transaction cannot be confirmed.",
    },
    "binance_id_stopped": {
        "vi": "Đã dừng nhận ID lệnh. Bấm lại “Tôi đã chuyển — dán ID lệnh” khi bạn có ID.",
        "en": "Stopped waiting for an order ID. Tap “I've sent it — paste order ID” again when you have it.",
    },
    "binance_id_bad_format": {
        "vi": "ID lệnh phải là dãy 8–32 chữ số, ví dụ: 453343187271753728. Vui lòng gửi lại.",
        "en": "The order ID must be 8–32 digits, for example: 453343187271753728. Please send it again.",
    },
    "binance_paid_not_delivered": {
        "vi": "{check} Thanh toán đã được xác nhận cho đơn {order}, nhưng link chưa thể giao tự động lúc này. Vui lòng liên hệ admin @wolfmodyt kèm mã đơn.",
        "en": "{check} Payment is confirmed for order {order}, but the link cannot be delivered automatically right now. Please contact the admin @wolfmodyt with your order ID.",
    },
    "binance_needs_review": {
        "vi": "{shield} Đơn {order} cần admin kiểm tra thủ công (thanh toán đến sau hạn). Vui lòng liên hệ @wolfmodyt kèm mã đơn.",
        "en": "{shield} Order {order} needs a manual check by the admin (payment arrived after the deadline). Please contact @wolfmodyt with your order ID.",
    },
    "binance_expired": {
        "vi": "Đơn này đã hết hạn hoặc bị hủy. Nếu bạn đã chuyển tiền, vui lòng liên hệ @wolfmodyt kèm mã đơn.",
        "en": "This order has expired or been cancelled. If you have already paid, please contact @wolfmodyt with your order ID.",
    },
    "bep20_review_alert": {
        "vi": "Đã thấy giao dịch nhưng cần admin kiểm tra.",
        "en": "Transaction seen, but the admin needs to check it.",
    },
    "bep20_review_message": {
        "vi": "{shield} <b>Đơn <code>{order}</code> cần admin kiểm tra thủ công</b> (thanh toán đến sau hạn hoặc cần đối soát).\n{chat} Vui lòng liên hệ <b>@wolfmodyt</b> kèm mã đơn.",
        "en": "{shield} <b>Order <code>{order}</code> needs a manual check by the admin</b> (payment arrived after the deadline or needs reconciliation).\n{chat} Please contact <b>@wolfmodyt</b> with your order ID.",
    },
    "wallet_err_unavailable": {
        "vi": "Tính năng ví chưa mở hoặc đang tạm thời không dùng được. Vui lòng thử lại sau.",
        "en": "The wallet is not open yet or is temporarily unavailable. Please try again later.",
    },
    "wallet_screen": {
        "vi": "{wallet} <b>Ví của tôi</b>\n\nSố dư: <b>{balance}</b> (≈ {usd})\n\n<b>Giao dịch gần đây</b>\n{history}\n\n<i>Số dư chỉ dùng trong bot này. Nạp tiền và thanh toán bằng ví sẽ sớm có.</i>",
        "en": "{wallet} <b>My Wallet</b>\n\nBalance: <b>{balance}</b> (≈ {usd})\n\n<b>Recent transactions</b>\n{history}\n\n<i>The balance can only be used in this bot. Top-up and paying with the wallet are coming soon.</i>",
    },
    "wallet_screen_t": {
        "vi": "{wallet} <b>Ví của tôi</b>\n\nSố dư: <b>{balance}</b> (≈ {usd})\n\n<b>Giao dịch gần đây</b>\n{history}\n\n<i>Số dư chỉ dùng trong bot này. Thanh toán bằng ví sẽ sớm có.</i>",
        "en": "{wallet} <b>My Wallet</b>\n\nBalance: <b>{balance}</b> (≈ {usd})\n\n<b>Recent transactions</b>\n{history}\n\n<i>The balance can only be used in this bot. Paying with the wallet is coming soon.</i>",
    },
    "btn_topup_open": {"vi": "Nạp tiền", "en": "Top up"},
    "btn_topup_other": {"vi": "Nhập số khác", "en": "Other amount"},
    "topup_menu": {
        "vi": "<b>Nạp tiền vào ví</b>\n\nChọn số tiền hoặc nhập số khác.\nTối thiểu {min}, tối đa {max} mỗi lần; số dư tối đa {cap}.",
        "en": "<b>Top up your wallet</b>\n\nPick an amount or enter another one.\nMinimum {min}, maximum {max} per top-up; the balance can hold at most {cap}.",
    },
    "topup_prompt": {
        "vi": "Nhập số tiền muốn nạp (VNĐ), từ {min} đến {max}. Ví dụ: 150000",
        "en": "Send the amount to top up (VND), from {min} to {max}. Example: 150000",
    },
    "topup_not_number": {
        "vi": "Số tiền không hợp lệ. Vui lòng chỉ nhập chữ số, ví dụ 150000.",
        "en": "That is not a valid amount. Please send digits only, for example 150000.",
    },
    "topup_bill_service": {"vi": "Nạp tiền vào ví", "en": "Wallet top-up"},
    "topup_err_amount": {
        "vi": "Số tiền nạp phải từ {min} đến {max}.",
        "en": "The top-up amount must be between {min} and {max}.",
    },
    "topup_err_cap": {
        "vi": "Nạp số tiền này sẽ vượt số dư tối đa của ví ({cap}). Hãy chọn số nhỏ hơn.",
        "en": "This top-up would exceed the maximum wallet balance ({cap}). Please pick a smaller amount.",
    },
    "topup_err_pending": {
        "vi": "Bạn đang có {limit} đơn nạp chờ thanh toán. Hãy thanh toán hoặc huỷ bớt rồi thử lại.",
        "en": "You already have {limit} top-up orders waiting for payment. Pay or cancel one and try again.",
    },
    "topup_err_unavailable": {
        "vi": "Hiện chưa tạo được đơn nạp. Vui lòng thử lại sau ít phút. Chưa có khoản tiền nào bị trừ.",
        "en": "We could not create the top-up order right now. Please try again in a few minutes. Nothing has been charged.",
    },
    "topup_err_unavailable_short": {
        "vi": "Chưa xử lý được lúc này, vui lòng bấm kiểm tra lại sau ít phút.",
        "en": "Could not process this right now, please check again in a few minutes.",
    },
    "topup_credited_alert": {"vi": "Đã cộng tiền vào ví!", "en": "Your wallet has been credited!"},
    "topup_already_alert": {"vi": "Đơn nạp này đã được cộng vào ví trước đó.", "en": "This top-up was already credited to your wallet."},
    "topup_credited_msg": {
        "vi": "Đã nạp <b>{amount}</b> vào ví (đơn <code>{order}</code>). Số dư hiện tại: <b>{balance}</b>.",
        "en": "<b>{amount}</b> was added to your wallet (order <code>{order}</code>). Current balance: <b>{balance}</b>.",
    },
    "topup_expired": {
        "vi": "Đơn nạp <code>{order_id}</code> đã hết hạn. Nếu bạn đã chuyển tiền, hệ thống vẫn sẽ tự cộng vào ví khi nhận được; nếu chưa, hãy tạo đơn nạp mới.",
        "en": "Top-up order <code>{order_id}</code> has expired. If you already paid, the wallet will still be credited automatically once the payment is received; otherwise create a new top-up.",
    },
    "btn_pay_wallet_ok": {"vi": "Trả bằng ví · số dư {balance} · đủ", "en": "Pay with wallet · balance {balance} · enough"},
    "btn_pay_wallet_short": {"vi": "Trả bằng ví · số dư {balance} · thiếu {missing}", "en": "Pay with wallet · balance {balance} · short by {missing}"},
    "btn_wpay_check": {"vi": "Kiểm tra đơn", "en": "Check order"},
    "wpay_err_funds": {
        "vi": "Số dư ví không đủ để thanh toán đơn này. Hãy nạp thêm hoặc chọn phương thức khác. Chưa có khoản tiền nào bị trừ.",
        "en": "Your wallet balance is not enough for this order. Top up or pick another method. Nothing has been charged.",
    },
    "wpay_err_busy": {
        "vi": "Bạn đang có một đơn trả bằng ví đang xử lý. Vui lòng chờ đơn đó hoàn tất.",
        "en": "You already have a wallet payment being processed. Please wait for it to finish.",
    },
    "wpay_err_unavailable": {
        "vi": "Hiện chưa thanh toán bằng ví được (sản phẩm/số lượng/giá chưa hợp lệ). Chưa có khoản tiền nào bị trừ.",
        "en": "Wallet payment is not available right now (product, quantity or price not valid). Nothing has been charged.",
    },
    "wpay_processing": {
        "vi": "Đang xử lý đơn <code>{order}</code> ({amount}) đã trả bằng ví. Link sẽ được gửi ngay khi sẵn sàng; nếu chưa thấy, bấm Kiểm tra đơn.",
        "en": "Processing order <code>{order}</code> ({amount}) paid with your wallet. The link is sent as soon as it is ready; if you do not see it, tap Check order.",
    },
    "wpay_paid_msg": {
        "vi": "Đã thanh toán đơn <code>{order}</code> ({amount}) bằng ví. Số dư còn lại: <b>{balance}</b>. Link giao hàng đã được gửi.",
        "en": "Order <code>{order}</code> ({amount}) was paid with your wallet. Remaining balance: <b>{balance}</b>. Your delivery was sent.",
    },
    "wpay_refunded_msg": {
        "vi": "Đơn <code>{order}</code> không giao được (hết hàng/bị từ chối) nên <b>{amount}</b> đã được hoàn lại vào ví. Số dư hiện tại: <b>{balance}</b>.",
        "en": "Order <code>{order}</code> could not be delivered (out of stock/refused), so <b>{amount}</b> was returned to your wallet. Current balance: <b>{balance}</b>.",
    },
    "wpay_refunded_review_msg": {
        "vi": "Đơn <code>{order}</code> chưa giao được sau 15 phút nên <b>{amount}</b> đã được hoàn lại vào ví (số dư: <b>{balance}</b>). Admin sẽ kiểm tra đơn này; nếu bạn đã nhận hàng, vui lòng báo @wolfmodyt.",
        "en": "Order <code>{order}</code> could not be delivered within 15 minutes, so <b>{amount}</b> was returned to your wallet (balance: <b>{balance}</b>). The admin will review this order; if you did receive the item, please tell @wolfmodyt.",
    },
    "wpay_cancelled_alert": {"vi": "Đơn này đã được hoàn tiền vào ví.", "en": "This order was refunded to your wallet."},
    "wpay_wait_alert": {"vi": "Đơn đang được xử lý, vui lòng thử lại sau ít phút.", "en": "The order is still being processed, please try again in a few minutes."},
    "btn_topup_m_vietqr": {"vi": "VietQR (chuyển khoản ngân hàng)", "en": "VietQR (bank transfer)"},
    "btn_topup_m_plisio": {"vi": "USDT qua Plisio", "en": "USDT via Plisio"},
    "btn_topup_m_bep20": {"vi": "USDT BEP20 (chuyển ví)", "en": "USDT BEP20 (wallet transfer)"},
    "btn_topup_m_binance": {"vi": "USDT qua Binance Pay", "en": "USDT via Binance Pay"},
    "topup_method_menu": {
        "vi": "<b>Nạp {amount} vào ví</b>\n\nChọn phương thức thanh toán. Với USDT, số USDT cần trả do hệ thống tính và hiển thị ở bước sau.",
        "en": "<b>Top up {amount}</b>\n\nChoose a payment method. For USDT, the exact USDT amount to pay is calculated by the system and shown in the next step.",
    },
    "topup_err_usdt_minimum": {
        "vi": "Số tiền này nhỏ hơn mức tối thiểu của cổng USDT. Hãy chọn số lớn hơn hoặc dùng VietQR.",
        "en": "This amount is below the minimum of the USDT gateway. Please pick a larger amount or use VietQR.",
    },
    "topup_usdt_plisio": {
        "vi": "{money} <b>Nạp ví qua USDT (Plisio)</b>\n\nMã đơn: <code>{order}</code>\nCộng vào ví: <b>{vnd}</b>\nCần trả: <b>{usdt} USDT</b>\n{calendar} Hạn: <code>{deadline}</code>\n\nBấm \"Thanh toán\" để mở trang Plisio, trả xong bấm kiểm tra. Ví chỉ được cộng khi thanh toán được xác nhận.",
        "en": "{money} <b>Wallet top-up via USDT (Plisio)</b>\n\nOrder: <code>{order}</code>\nCredited to your wallet: <b>{vnd}</b>\nYou pay: <b>{usdt} USDT</b>\n{calendar} Deadline: <code>{deadline}</code>\n\nTap \"Pay\" to open the Plisio page, then tap check when done. The wallet is credited only after the payment is confirmed.",
    },
    "topup_usdt_bep20": {
        "vi": "{money} <b>Nạp ví qua USDT BEP20</b>\n\nMã đơn: <code>{order}</code>\nCộng vào ví: <b>{vnd}</b>\nSố tiền CHÍNH XÁC: <code>{usdt}</code> USDT (mạng BNB Smart Chain)\nĐịa chỉ ví nhận:\n<code>{address}</code>\n{calendar} Hạn: <code>{deadline}</code>\n\n{shield} Chuyển đúng số tiền (kể cả phần lẻ) và đúng mạng BEP20. Cần {confirmations} xác nhận trên chuỗi; sau đó bấm kiểm tra.",
        "en": "{money} <b>Wallet top-up via USDT BEP20</b>\n\nOrder: <code>{order}</code>\nCredited to your wallet: <b>{vnd}</b>\nEXACT amount: <code>{usdt}</code> USDT (BNB Smart Chain)\nReceiving address:\n<code>{address}</code>\n{calendar} Deadline: <code>{deadline}</code>\n\n{shield} Send exactly this amount (including decimals) on the BEP20 network. {confirmations} confirmations are needed; then tap check.",
    },
    "topup_usdt_binance": {
        "vi": "{money} <b>Nạp ví qua Binance Pay</b>\n\nMã đơn: <code>{order}</code>\nCộng vào ví: <b>{vnd}</b>\nSố tiền: <code>{usdt}</code> USDT\nBinance Pay ID nhận: <code>{receiver}</code>\n{calendar} Hạn: <code>{deadline}</code>\n\n{shield} Chuyển đúng số tiền bằng Binance Pay, rồi bấm nút bên dưới và dán ID lệnh. Ví chỉ được cộng khi xác minh thành công.",
        "en": "{money} <b>Wallet top-up via Binance Pay</b>\n\nOrder: <code>{order}</code>\nCredited to your wallet: <b>{vnd}</b>\nAmount: <code>{usdt}</code> USDT\nReceiving Binance Pay ID: <code>{receiver}</code>\n{calendar} Deadline: <code>{deadline}</code>\n\n{shield} Send exactly this amount with Binance Pay, then tap the button below and paste the order ID. The wallet is credited only after verification succeeds.",
    },
    "wallet_history_empty": {"vi": "Chưa có giao dịch nào.", "en": "No transactions yet."},
    "wallet_kind_topup": {"vi": "Nạp tiền", "en": "Top-up"},
    "wallet_kind_purchase": {"vi": "Mua hàng", "en": "Purchase"},
    "wallet_kind_refund": {"vi": "Hoàn tiền", "en": "Refund"},
    "wallet_kind_admin_adjust": {"vi": "Điều chỉnh", "en": "Adjustment"},
    "wallet_private_only": {
        "vi": "Vui lòng dùng tính năng này trong chat riêng với bot.",
        "en": "Please use this feature in a private chat with the bot.",
    },
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
    # Product list buttons: no "MUA"/"BUY" prefix and no price (the price is shown on the product page and at checkout).
    "btn_product_buy_nostock": {"vi": "{name}", "en": "{name}"},
    "btn_product_oos": {"vi": "{name} • HẾT HÀNG", "en": "{name} • OUT OF STOCK"},
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
    "bep20_confirming": {
        "vi": "<b>Đã thấy giao dịch trên mạng, đang chờ xác nhận ({n}/{need}). Vui lòng bấm kiểm tra lại sau ít phút.</b>",
        "en": "<b>The transaction is visible on the network and awaiting confirmations ({n}/{need}). Please check again in a few minutes.</b>",
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
    "offer_single_use": {"vi": "Link chỉ dùng được một lần. Bảo hành 1 đổi 1, liên hệ @wolfmodyt kèm mã đơn nếu lỗi.", "en": "The link works only once. 1-for-1 replacement warranty, contact @wolfmodyt with your order ID if faulty."},
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
