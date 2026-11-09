"""Nội dung chi tiết sản phẩm (vi/en) đọc từ `product_content.json` – sửa nội dung không cần sửa code.

Giá VNĐ và tồn kho luôn do web/DB quyết định; file chỉ cung cấp tên, thời hạn, quyền lợi, cách giao, bảo hành, lưu ý.
"""
from __future__ import annotations

import json
import logging
import os

import i18n

logger = logging.getLogger("product_content")

CONTENT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "product_content.json")
_cache: dict | None = None


def load(force: bool = False) -> dict:
    """Đọc file nội dung (cache). Lỗi file -> {} và log cảnh báo (bot vẫn chạy bằng dữ liệu web)."""
    global _cache
    if _cache is not None and not force:
        return _cache
    try:
        with open(CONTENT_PATH, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
        _cache = {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, dict)}
    except Exception as error:  # noqa: BLE001 - dữ liệu hỏng không được làm sập bot
        logger.warning("Không đọc được product_content.json: %s", type(error).__name__)
        _cache = {}
    return _cache


def listed_skus() -> list[str]:
    """SKU có trong file nội dung (kể cả chưa có trên web) – dùng để luôn hiển thị sản phẩm kèm 'Hết hàng'."""
    return list(load().keys())


def _pick(value, lang, default=None):
    if isinstance(value, dict):
        code = i18n.normalize_lang(lang)
        return value.get(code) or value.get("en") or default
    return value if value is not None else default


def get(sku: str) -> dict | None:
    return load().get(sku)


def name(sku: str, lang, fallback: str = "") -> str:
    item = get(sku)
    return _pick(item.get("name"), lang, fallback) if item else fallback


def short_name(sku: str, lang, fallback: str = "") -> str:
    item = get(sku)
    if not item:
        return fallback
    return _pick(item.get("short_name"), lang) or _pick(item.get("name"), lang, fallback)


def features(sku: str, lang) -> list[str]:
    item = get(sku)
    value = _pick(item.get("features"), lang, []) if item else []
    return [str(x) for x in value] if isinstance(value, list) else []


def field(sku: str, key: str, lang):
    item = get(sku)
    return _pick(item.get(key), lang) if item else None


def notes(sku: str, lang) -> list[str]:
    value = field(sku, "notes", lang)
    return [str(x) for x in value] if isinstance(value, list) else []


def price_fallback(sku: str) -> int:
    item = get(sku)
    try:
        return int(item.get("price_vnd_fallback") or 0) if item else 0
    except (TypeError, ValueError):
        return 0


def usd_from_rate(sku: str) -> bool:
    """True nếu SKU hiển thị giá USD quy đổi từ VNĐ khi web chưa có giá USDT riêng."""
    item = get(sku)
    return bool(item and item.get("usd_from_rate"))
