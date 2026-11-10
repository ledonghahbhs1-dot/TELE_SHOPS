"""Hướng dẫn riêng cho từng sản phẩm, khóa theo SKU, có đủ hai ngôn ngữ (vi/en).

Hướng dẫn chỉ hiện khi khách đang xem đúng sản phẩm đó (màn chi tiết sản phẩm) hoặc trong đơn hàng
của sản phẩm đó. Không có hướng dẫn chung, và sản phẩm chưa có mục riêng thì KHÔNG có hướng dẫn
(bot không hiện nút hướng dẫn, không mượn nội dung của sản phẩm khác).

Thêm hướng dẫn cho sản phẩm mới: thêm một mục vào GUIDES với CẢ HAI ngôn ngữ.

Mỗi mục: GUIDES[sku][lang] với lang in ("vi", "en"):
  title        tiêu đề màn hình (HTML đã được tin cậy, do lập trình viên viết)
  intro        dòng giới thiệu quy trình
  steps        danh sách bước (HTML tin cậy)
  notes_title  tiêu đề phần lưu ý
  notes        danh sách (tên_emoji, emoji_dự_phòng, nội_dung)
"""
import logging

logger = logging.getLogger("product_guides")

GUIDES: dict[str, dict[str, dict]] = {
    # Nội dung giữ nguyên như màn "Cẩm nang kích hoạt" cũ; đó vốn là hướng dẫn của gemini_plus.
    "gemini_plus": {
        "vi": {
            "title": "CẨM NANG KÍCH HOẠT VIP",
            "intro": "Quy trình 3 bước kích hoạt Gemini Plus 2TB:",
            "steps": [
                "Chuẩn bị tài khoản Gmail cá nhân của bạn (khuyên dùng Gmail chưa dính gói tranh chấp gia đình).",
                "Nhấp vào Link nhận được từ bot sau khi thanh toán thành công.",
                "Google sẽ hiển thị thông báo mời tham gia gói ưu đãi 2TB. Bạn chỉ cần nhấn <b>Chấp nhận (Accept)</b>.",
            ],
            "notes_title": "Lưu ý đặc quyền VIP:",
            "notes": [
                ("check", "✦", "Sử dụng song song trên cả Máy tính (PC), Điện thoại (Android/iOS)."),
                ("shield", "✦", "Nếu Google yêu cầu thêm phương thức thanh toán để xác minh 0đ, bạn có thể thêm thẻ ngân hàng ảo/thật (Google hoàn toàn không trừ phí)."),
            ],
        },
        "en": {
            "title": "VIP ACTIVATION GUIDE",
            "intro": "3 steps to activate Gemini Plus 2TB:",
            "steps": [
                "Prepare your personal Gmail account (a Gmail that is not in a family-plan dispute is recommended).",
                "Open the link you received from the bot after a successful payment.",
                "Google shows an invitation to the 2TB offer. Just tap <b>Accept</b>.",
            ],
            "notes_title": "VIP notes:",
            "notes": [
                ("check", "✦", "Works in parallel on computers (PC) and phones (Android/iOS)."),
                ("shield", "✦", "If Google asks for a payment method to verify the 0 VND charge, you can add a virtual or real bank card (Google does not charge anything)."),
            ],
        },
    },
    # Tài khoản Gmail|password: không có link kích hoạt, không có bước "chấp nhận ưu đãi".
    # BẢN NHÁP: chủ shop cần xác nhận chính sách bảo hành / đổi mật khẩu trước khi coi là chính thức.
    "gemini_pro_account": {
        "vi": {
            "title": "HƯỚNG DẪN SỬ DỤNG ACCOUNT GEMINI PRO",
            "intro": "Cách dùng tài khoản Gemini Pro sau khi thanh toán:",
            "steps": [
                "Sau khi thanh toán được xác nhận, bot gửi thông tin tài khoản dạng <code>Gmail|password</code> trong tin nhắn riêng. Phần trước dấu <code>|</code> là Gmail, phần sau là mật khẩu.",
                "Đăng nhập Google bằng đúng Gmail và mật khẩu đó (nên dùng trình duyệt/hồ sơ riêng hoặc cửa sổ ẩn danh để không lẫn với tài khoản cá nhân).",
                "Mở Gemini hoặc Google AI Studio để sử dụng. Quyền lợi gói Google AI Pro hiển thị trong phần gói đăng ký của tài khoản.",
            ],
            "notes_title": "Lưu ý khi dùng tài khoản:",
            "notes": [
                ("shield", "✦", "Không chia sẻ Gmail/mật khẩu cho người khác và không gửi thông tin này vào nhóm chat."),
                ("check", "✦", "Quyền lợi có thể thay đổi theo khu vực và điều khoản của Google."),
                ("chat", "✦", "Không đăng nhập được hoặc cần hỗ trợ: liên hệ CSKH kèm mã đơn hàng."),
            ],
        },
        "en": {
            "title": "GEMINI PRO ACCOUNT USER GUIDE",
            "intro": "How to use your Gemini Pro account after payment:",
            "steps": [
                "Once payment is confirmed, the bot sends the account as <code>Gmail|password</code> in a private message. The part before <code>|</code> is the Gmail, the part after it is the password.",
                "Sign in to Google with exactly that Gmail and password (use a separate browser/profile or an incognito window so it does not mix with your personal account).",
                "Open Gemini or Google AI Studio to use it. The Google AI Pro benefits show up in the account's subscription section.",
            ],
            "notes_title": "Notes when using the account:",
            "notes": [
                ("shield", "✦", "Do not share the Gmail/password with anyone and never post them in a group chat."),
                ("check", "✦", "Benefits may vary by region and Google's terms."),
                ("chat", "✦", "Cannot sign in or need help: contact support with your order ID."),
            ],
        },
    },
    "chatgpt_go_3m": {
        "vi": {
            "title": "HƯỚNG DẪN KÍCH HOẠT CHATGPT GO",
            "intro": "Sau khi thanh toán, bot gửi 1 offer link (link ưu đãi) cho mỗi đơn:",
            "steps": [
                "Mở offer link nhận được từ bot.",
                "Đăng nhập tài khoản ChatGPT của bạn.",
                "Làm theo màn hình kích hoạt để nhận gói ChatGPT Go.",
            ],
            "notes_title": "Lưu ý:",
            "notes": [
                ("shield", "✦", "Link chỉ dùng được một lần, đừng chia sẻ cho người khác."),
                ("chat", "✦", "Bảo hành 1 đổi 1, liên hệ CSKH nếu lỗi (kèm mã đơn hàng)."),
            ],
        },
        "en": {
            "title": "CHATGPT GO ACTIVATION GUIDE",
            "intro": "After payment the bot sends 1 offer link per order:",
            "steps": [
                "Open the offer link you received from the bot.",
                "Sign in to your ChatGPT account.",
                "Follow the activation screen to get your ChatGPT Go plan.",
            ],
            "notes_title": "Notes:",
            "notes": [
                ("shield", "✦", "The link works only once, do not share it."),
                ("chat", "✦", "1-for-1 replacement warranty, contact support if faulty (include your order ID)."),
            ],
        },
    },
}


def has_guide(sku: str) -> bool:
    """True khi sản phẩm có hướng dẫn riêng."""
    return sku in GUIDES


def get_guide(sku: str, lang: str = "vi") -> dict | None:
    """Hướng dẫn riêng của sản phẩm theo ngôn ngữ (thiếu -> tiếng Anh + log), hoặc None nếu sản phẩm chưa có
    (không bao giờ trả về nội dung của SKU khác)."""
    entry = GUIDES.get(sku)
    if entry is None:
        return None
    guide = entry.get(lang)
    if guide is None:
        logger.warning("product_guides: missing %s guide for %s, falling back to English", lang, sku)
        guide = entry.get("en")
    return guide
