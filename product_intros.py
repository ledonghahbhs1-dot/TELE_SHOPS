"""Màn giới thiệu RIÊNG của từng sản phẩm, khóa theo SKU.

Màn chính của bot chỉ liệt kê sản phẩm ngắn gọn; nội dung giới thiệu dài của mỗi sản phẩm nằm ở đây và chỉ
hiện khi khách bấm vào đúng sản phẩm đó.

Mỗi hàm nhận `em(name, fallback)` (hàm tạo emoji tùy chỉnh của bot) và các giá trị động (giá, giá USDT),
và trả về HTML. Thêm giới thiệu riêng cho sản phẩm mới: viết một hàm rồi thêm vào INTROS. SKU chưa có mục sẽ dùng
`generic_intro` (mẫu trung tính từ tên/mô tả/giá lấy từ web, không bao giờ dùng nội dung của sản phẩm khác).
"""
from __future__ import annotations

import html


def gemini_plus_intro(em, price: int, usdt_price) -> str:
    """Nội dung giới thiệu dài của Google AI Plus. CHUYỂN NGUYÊN VĂN từ màn chào cũ (không đổi chữ, giữ emoji tùy chỉnh)."""
    return (
        f"{em('star', '🌟')} <b>DỊCH VỤ ĐẶC QUYỀN ĐANG MỞ BÁN:</b>\n"
        f"<b>Google AI Plus 400GB (Thời Hạn 12 Tháng)</b>\n\n"
        f"{em('chart', '📊')} <b>BẢNG SO SÁNH GIÁ 12 THÁNG SIÊU TIẾT KIỆM:</b>\n"
        f"├─ {em('cross', '❌')} <b>Giá gốc Google:</b> <code>132.000 ₫/tháng</code> → <code>1.584.000 ₫ / 12 tháng</code>\n"
        f"├─ {em('money', '💰')} <b>Giá VIP tại WolfMod:</b> <b>{price:,} VNĐ / 12 Tháng</b> (hoặc <b>{usdt_price} USDT</b>)\n"
        f"├─ {em('calendar', '⏳')} <b>Thời hạn sử dụng:</b> <b>12 Tháng (1 Năm trọn vẹn)</b>\n"
        f"└─ {em('shield', '🛡')} <b>Chính sách bảo hành:</b> <b>Bảo hành 24h (1 đổi 1 siêu tốc)</b>\n\n"
        f"{em('lightning', '⚡️')} <b>Hiệu suất:</b> Gemini 3.6 Flash, Deep Research, hạn mức x2\n"
        f"{em('sparkle', '✉️')} <b>Gmail AI:</b> Trợ lý Gemini hỗ trợ hiệu đính trong Gmail\n"
        f"{em('star', '🎨')} <b>Sáng tạo:</b> Mở rộng tạo ảnh, nhạc, video & Gemini Notebook\n"
        f"{em('box', '📦')} <b>Bộ nhớ 400 GB:</b> Dùng chung Gmail, Google Drive, Google Photos\n"
        f"{em('shield', '👨‍👩‍👧‍👦')} <b>Gia đình:</b> Hỗ trợ chia sẻ tối đa 5 thành viên gia đình\n\n"
    )


def generic_intro(em, name: str, description: str, price: int, usdt_price) -> str:
    """Mẫu trung tính: tên, mô tả và giá đều lấy từ dữ liệu web của chính sản phẩm đó."""
    description = (description or "").strip()
    lines = [f"{em('crown', '👑')} <b>{html.escape(name)}</b> {em('crown', '👑')}\n\n"]
    if description:
        lines.append(f"{em('tag', '🌟')} {html.escape(description[:600])}\n\n")
    lines.append(f"{em('money', '💰')} <b>Giá:</b> <b>{price:,} VNĐ</b> (hoặc <b>{usdt_price} USDT</b>)\n")
    return "".join(lines)


# SKU -> hàm giới thiệu riêng. Chữ ký của hàm riêng: (em, price, usdt_price).
INTROS = {
    "gemini_plus": gemini_plus_intro,
}


def has_own_intro(sku: str) -> bool:
    return sku in INTROS
