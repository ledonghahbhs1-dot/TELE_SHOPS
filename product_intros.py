"""Màn giới thiệu của sản phẩm KHÔNG có trong `product_content.json`.

Sản phẩm có trong product_content.json (Google AI Plus, Gemini Pro, ChatGPT Go...) dùng trang chi tiết đầy đủ vi/en
dựng từ file đó (xem `shop_bot.render_product_intro`). Module này chỉ còn hai việc:
  * `generic_intro`: mẫu trung tính cho SKU mới phát hiện từ web nhưng chưa có nội dung riêng (tên/mô tả/giá từ web).
  * `INTROS`: nơi đăng ký hàm giới thiệu riêng (ngoại lệ) – chữ ký (em, price, usdt_price, lang) -> HTML.
"""
from __future__ import annotations

import html

import i18n


def generic_intro(em, name: str, description: str, price: int, usdt_price, lang: str = "en") -> str:
    """Mẫu trung tính: tên, mô tả và giá đều lấy từ dữ liệu web của chính sản phẩm đó."""
    description = (description or "").strip()
    lines = [f"{em('crown', '👑')} <b>{html.escape(name)}</b> {em('crown', '👑')}\n\n"]
    if description:
        lines.append(f"{em('tag', '🌟')} {html.escape(description[:600])}\n\n")
    lines.append(
        f"{em('money', '💰')} <b>{i18n.t(lang, 'lbl_price')}:</b> "
        f"<b>{i18n.t(lang, 'price_line_usdt', vnd=i18n.fmt_vnd(price), usdt=usdt_price)}</b>\n"
    )
    return "".join(lines)


# SKU -> hàm giới thiệu riêng. Chữ ký của hàm riêng: (em, price, usdt_price, lang).
INTROS: dict = {}


def has_own_intro(sku: str) -> bool:
    return sku in INTROS
