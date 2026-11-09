"""Thông báo nhập kho cho khách: tên đầy đủ + quyền lợi chính + giá + tồn kho, theo ngôn ngữ từng người nhận.

Module thuần (không gọi mạng/Telegram) để test được:
  * `StockNotifier`   gộp các lần nhập kho trong vài phút thành MỘT tin (tối đa 1 tin / sản phẩm / 5 phút).
  * `build_message`   dựng nội dung theo ngôn ngữ; KHÔNG chứa mã kỹ thuật (SKU) – chỉ tên hiển thị.
  * `group_by_lang`   gom người nhận theo ngôn ngữ để gửi mỗi nhóm một bản.
Việc gửi thật nằm ở shop_bot.send_stock_notifications.
"""
from __future__ import annotations

import html
import time
from collections import defaultdict
from typing import Callable

import i18n

COALESCE_SEC = 300          # tối đa 1 tin / sản phẩm / 5 phút
MAX_FEATURE_LINES = 5       # quyền lợi chính hiển thị tối đa 5 dòng


class StockNotifier:
    """Gộp các lần nhập kho theo SKU. `record()` ghi nhận; `pop_due()` trả các (sku, tổng_nhập) đến hạn gửi."""

    def __init__(self, window: float = COALESCE_SEC, clock: Callable[[], float] = time.monotonic):
        self.window = window
        self.clock = clock
        self._pending: dict[str, int] = {}
        self._last_sent: dict[str, float] = {}

    def record(self, sku: str, added: int) -> None:
        if added > 0:
            self._pending[sku] = self._pending.get(sku, 0) + int(added)

    def pop_due(self) -> list[tuple[str, int]]:
        now = self.clock()
        due: list[tuple[str, int]] = []
        for sku, added in list(self._pending.items()):
            last = self._last_sent.get(sku)
            if last is None or now - last >= self.window:
                due.append((sku, added))
                del self._pending[sku]
                self._last_sent[sku] = now
        return due


def group_by_lang(recipients) -> dict[str, list]:
    """[(chat_id, lang), ...] -> {lang: [chat_id, ...]} (mỗi chat_id chỉ xuất hiện một lần)."""
    groups: dict[str, list] = defaultdict(list)
    seen = set()
    for chat_id, lang in recipients:
        if chat_id in seen:
            continue
        seen.add(chat_id)
        groups[i18n.normalize_lang(lang)].append(chat_id)
    return dict(groups)


def build_message(lang, *, name: str, features: list[str], price_vnd: int, stock: int, added: int,
                  em: Callable[[str, str], str]) -> str:
    """HTML của thông báo. Chỉ dùng tên hiển thị, không bao giờ in SKU/mã kỹ thuật."""
    lines = [
        f"{em('crown', '👑')} ═══ {em('diamond', '💎')} <b>{i18n.t(lang, 'stock_title')}</b> {em('diamond', '💎')} ═══ {em('crown', '👑')}",
        "",
        f"{em('sparkle', '✨')} <b>{html.escape(name)}</b>",
        f"{em('party', '🎉')} {i18n.t(lang, 'stock_added', added=added)}",
    ]
    shown = [str(f) for f in features if str(f).strip()][:MAX_FEATURE_LINES]
    if shown:
        lines.append("")
        lines += [f"• {html.escape(item)}" for item in shown]
    lines.append("")
    price_text = i18n.t(lang, "price_line", vnd=i18n.fmt_vnd(price_vnd), usd=i18n.fmt_usd(price_vnd)) if price_vnd > 0 else ""
    if price_text:
        lines.append(f"{em('money', '💰')} <b>{i18n.t(lang, 'stock_price')}:</b> {price_text}")
    lines.append(f"{em('box', '📦')} <b>{i18n.t(lang, 'stock_available')}:</b> <code>{i18n.t(lang, 'stock_available_value', stock=stock)}</code>")
    return "\n".join(lines)
