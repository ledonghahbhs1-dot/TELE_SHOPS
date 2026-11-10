"""Task 053: the BEP20 / Binance Pay customer-facing text lives in i18n (vi + en), not in shop_bot.py."""
import ast
import os
import re
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("BOT_TOKEN", "test-token")

import i18n  # noqa: E402
import shop_bot  # noqa: E402

NEW_KEY_PREFIXES = ("bep20_", "binance_", "pay_", "btn_pay_", "btn_bep20_", "btn_binance_")
ACCENTED = re.compile(r"[ăâđêôơưáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]", re.I)
FUNCTIONS = ("render_bep20_invoice", "show_bep20_checkout", "render_binance_invoice", "show_binance_checkout",
             "prompt_binance_id", "binance_reply", "handle_binance_id_message")
RESULTS = ("not_found", "amount_mismatch", "invalid_transaction", "before_order", "already_used",
           "too_fast", "too_many_attempts", "unavailable", "config", "rejected")
BINANCE_RESULT_VI = {  # original Vietnamese, must stay identical
    "rejected": "Không thể xác nhận giao dịch này.",
    "already_used": "ID lệnh này đã được dùng cho một đơn khác.",
}


def new_keys():
    return [k for k in i18n.TEXTS if k.startswith(NEW_KEY_PREFIXES)] if hasattr(i18n, "TEXTS") else []


def _table():
    for name in ("TEXTS", "STRINGS", "TRANSLATIONS", "MESSAGES"):
        if hasattr(i18n, name):
            return getattr(i18n, name)
    raise AssertionError("i18n table not found")


def _literals(func):
    """String constants of a function, minus docstring (comments are not in the AST)."""
    doc = ast.get_docstring(func, clean=False)
    skip = set()   # operator-facing log messages are not customer text
    for node in ast.walk(func):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) \
                and node.func.value.id == "logger":
            skip.update(id(n) for n in ast.walk(node))
    out = []
    for node in ast.walk(func):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value != doc and id(node) not in skip:
            out.append(node.value)
    return out


class KeysTests(unittest.TestCase):
    def test_every_new_key_has_vi_and_en_with_matching_placeholders(self):
        table = _table()
        keys = [k for k in table if k.startswith(NEW_KEY_PREFIXES)]
        self.assertGreaterEqual(len(keys), 35)
        for key in keys:
            vi, en = table[key]["vi"], table[key]["en"]
            self.assertTrue(vi.strip() and en.strip(), key)
            self.assertEqual(set(re.findall(r"{(\w+)}", vi)), set(re.findall(r"{(\w+)}", en)), key)
            self.assertFalse(ACCENTED.search(en.replace("—", "")), f"{key}: English text contains Vietnamese")

    def test_every_binance_result_has_a_key(self):
        table = _table()
        for result in RESULTS:
            self.assertIn(f"binance_reply_{result}", table)
        self.assertEqual(tuple(shop_bot._BINANCE_REPLY_KEYS), RESULTS)

    def test_vietnamese_of_results_unchanged(self):
        for result, text in BINANCE_RESULT_VI.items():
            self.assertEqual(shop_bot.binance_reply("vi", result), text)


class NoHardcodedVietnameseTests(unittest.TestCase):
    def test_no_accented_literals_left_in_these_functions(self):
        tree = ast.parse(Path(shop_bot.__file__).read_text(encoding="utf-8"))
        found = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        for name in FUNCTIONS:
            self.assertIn(name, found)
            for lit in _literals(found[name]):
                self.assertIsNone(ACCENTED.search(lit), f"{name} still has hard-coded Vietnamese: {lit!r}")

    def test_payment_method_buttons_use_keys(self):
        src = Path(shop_bot.__file__).read_text(encoding="utf-8")
        self.assertFalse("USDT BEP20 — Quét QR ví" in src, "BEP20 button still hard-coded")
        self.assertFalse("Binance Pay (dán ID lệnh)" in src, "Binance button still hard-coded")
        self.assertFalse("_BINANCE_REPLIES" in src, "old reply dict still present")


class RenderTests(unittest.TestCase):
    info_bep20 = {"amount_usdt": "5.01", "address": "0x" + "ab" * 20, "expires_at": datetime(2026, 1, 1, 10, 20, 30),
                  "min_confirmations": 12}
    info_binance = {"amount_usdt": "5.00", "receiver_id": "9999000011", "expires_at": datetime(2026, 1, 1, 10, 20, 30)}

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = patch.multiple(shop_bot, DATA_DIR=d.name, DB_PATH=str(Path(d.name) / "shop.db"), BOT_TOKEN="test-token")
        p.start()
        self.addCleanup(p.stop)
        with patch.dict(os.environ, {"USDT_WALLET": ""}):
            shop_bot.init_database()

    def test_bep20_invoice_english_and_vietnamese(self):
        en = shop_bot.render_bep20_invoice("TGS1", self.info_bep20, 2, shop_bot.DEFAULT_SKU, "en")
        vi = shop_bot.render_bep20_invoice("TGS1", self.info_bep20, 2, shop_bot.DEFAULT_SKU, "vi")
        for needle in ("USDT BEP20 INVOICE", "Order ID:", "EXACT amount:", "BEP20 (BSC)", "10:20:30", "TGS1", "5.01", "12 network confirmations"):
            self.assertIn(needle, en)
        self.assertIsNone(ACCENTED.search(re.sub(r"<[^>]+>", "", en).replace("—", "")))
        for needle in ("HÓA ĐƠN USDT BEP20", "Mã đơn:", "Số tiền CHÍNH XÁC:", "đúng từng chữ số", "Cần khoảng 12 xác nhận mạng"):
            self.assertIn(needle, vi)

    def test_binance_invoice_english_and_vietnamese(self):
        en = shop_bot.render_binance_invoice("TGS2", self.info_binance, 1, shop_bot.DEFAULT_SKU, "en")
        vi = shop_bot.render_binance_invoice("TGS2", self.info_binance, 1, shop_bot.DEFAULT_SKU, "vi")
        for needle in ("USDT INVOICE — BINANCE PAY", "Receiving Binance Pay ID:", "9999000011", "paste the order ID here"):
            self.assertIn(needle, en)
        self.assertIsNone(ACCENTED.search(re.sub(r"<[^>]+>", "", en)))
        for needle in ("HÓA ĐƠN USDT — BINANCE PAY", "Binance Pay ID nhận:", "dán ID lệnh vào đây"):
            self.assertIn(needle, vi)

    def test_all_ten_binance_results_render_in_both_languages(self):
        seen_en = set()
        for result in RESULTS:
            vi = shop_bot.binance_reply("vi", result)
            en = shop_bot.binance_reply("en", result)
            self.assertTrue(vi and en)
            self.assertNotEqual(vi, en)
            self.assertIsNone(ACCENTED.search(en.replace("—", "")), result)
            self.assertNotIn("{", en)
            seen_en.add(en)
        self.assertEqual(len(seen_en), 10)
        self.assertEqual(shop_bot.binance_reply("en", "something-unknown"), shop_bot.binance_reply("en", "rejected"))


class EnvExampleTests(unittest.TestCase):
    def test_binance_pay_flag_documented_and_off(self):
        text = (Path(shop_bot.__file__).parent / ".env.example").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^BINANCE_PAY_ENABLED=0\s*$")


if __name__ == "__main__":
    unittest.main()
