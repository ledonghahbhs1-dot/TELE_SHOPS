"""Hướng dẫn riêng cho từng sản phẩm, khóa theo SKU.

Hướng dẫn chỉ hiện khi khách đang xem đúng sản phẩm đó (màn chi tiết sản phẩm) hoặc trong đơn hàng
của sản phẩm đó. Không có hướng dẫn chung, và sản phẩm chưa có mục riêng thì KHÔNG có hướng dẫn
(bot không hiện nút hướng dẫn, không mượn nội dung của sản phẩm khác).

Thêm hướng dẫn cho sản phẩm mới: thêm một mục vào GUIDES.

Mỗi mục:
  title        tiêu đề màn hình (HTML đã được tin cậy, do lập trình viên viết)
  intro        dòng giới thiệu quy trình
  steps        danh sách bước (HTML tin cậy)
  notes_title  tiêu đề phần lưu ý
  notes        danh sách (tên_emoji, emoji_dự_phòng, nội_dung)
"""

GUIDES: dict[str, dict] = {
    # Nội dung giữ nguyên như màn "Cẩm nang kích hoạt" cũ; đó vốn là hướng dẫn của gemini_plus.
    "gemini_plus": {
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
    # Tài khoản Gmail|password: không có link kích hoạt, không có bước "chấp nhận ưu đãi".
    # BẢN NHÁP: chủ shop cần xác nhận chính sách bảo hành / đổi mật khẩu trước khi coi là chính thức.
    "gemini_pro_account": {
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
}


def has_guide(sku: str) -> bool:
    """True khi sản phẩm có hướng dẫn riêng."""
    return sku in GUIDES


def get_guide(sku: str) -> dict | None:
    """Hướng dẫn riêng của sản phẩm, hoặc None nếu chưa có (không bao giờ trả về nội dung của SKU khác)."""
    return GUIDES.get(sku)
