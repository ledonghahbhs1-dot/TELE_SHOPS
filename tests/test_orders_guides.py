import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import product_guides
import shop_bot

PLUS = "gemini_plus"
PRO = "gemini_pro_account"
NEW = "premium_vpn"  # no guide of its own
OLD = "retired_product"


def make_query(data):
    message = SimpleNamespace(chat_id=1, message_id=2, photo=None, reply_photo=AsyncMock(),
                              reply_text=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(data=data, answer=AsyncMock(), message=message)


def update_for(query, user_id=123):
    return SimpleNamespace(callback_query=query,
                           effective_user=SimpleNamespace(id=user_id, username="u", first_name="U"))


def datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def texts(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def legacy_plus_guide():
    """The old "Cẩm nang kích hoạt" screen (it was always gemini_plus's guide), character for character."""
    vip_em = shop_bot.vip_em
    return (
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


PLUS_GUIDE_MARKERS = ("Quy trình 3 bước", "Gemini Plus 2TB", "Chấp nhận (Accept)", "Nhấp vào Link", "tranh chấp gia đình")


class OrdersGuidesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = patch.multiple(shop_bot,
            DATA_DIR=self.directory.name,
            DB_PATH=str(Path(self.directory.name) / "shop_database.db"),
            CLAIMED_LINKS_FILE=str(Path(self.directory.name) / "claimed_links.txt"))
        settings.start()
        self.addCleanup(settings.stop)
        refresh = patch.object(shop_bot, "sync_web_product_price", return_value=True)
        refresh.start()
        self.addCleanup(refresh.stop)
        shop_bot._catalogue.update(items=None, next=0.0)
        shop_bot._web_stock_cache.clear()
        self.addCleanup(lambda: shop_bot._catalogue.update(items=None, next=0.0))
        with patch.dict(os.environ, {"USDT_WALLET": ""}):
            shop_bot.init_database()
        shop_bot.set_setting("default_lang", "vi")  # legacy tests assert the Vietnamese UI

    def add_order(self, order_id, sku, user=123, status="completed", item="content", at="2026-11-06 10:00:00"):
        conn = shop_bot.get_db_connection()
        conn.execute("INSERT INTO orders (order_id,user_id,product_id,amount,status,item_delivered,completed_at) VALUES (?,?,?,?,?,?,?)",
                     (order_id, user, sku, 1000, status, item, at))
        conn.commit()
        conn.close()

    async def click(self, data, user_id=123):
        query = make_query(data)
        with patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock) as render:
            await shop_bot.callback_handler(update_for(query, user_id), SimpleNamespace())
        return render.await_args.args[1], render.await_args.kwargs["reply_markup"]

    def assert_no_plus_guide(self, text):
        for marker in PLUS_GUIDE_MARKERS:
            self.assertNotIn(marker, text)

    # ---------- main screen: only the "Don hang" button, no orders, no guide ----------
    async def test_main_menu_and_welcome_show_no_orders_and_no_guide(self):
        self.add_order("TGSPLUS0001", PLUS, item="https://example.com/secret-link")
        with patch.object(shop_bot, "get_stock_count", return_value=5):
            keyboard = shop_bot.main_menu_keyboard(1)
        callbacks, labels = datas(keyboard), texts(keyboard)
        self.assertEqual(callbacks.count("my_orders"), 1)
        self.assertNotIn("guide", callbacks)
        self.assertFalse([c for c in callbacks if c and (c.startswith("gd:") or c.startswith("ordp:"))])
        self.assertFalse([t for t in labels if "Hướng Dẫn" in t or "Cẩm Nang" in t or "400GB" in t])
        # the welcome screen itself shows neither order contents nor guide steps
        update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()), callback_query=None,
                                 effective_user=SimpleNamespace(id=123, first_name="U"))
        with patch.object(shop_bot, "get_stock_count", return_value=5):
            await shop_bot.start_command(update, SimpleNamespace())
        welcome = update.message.reply_text.await_args.args[0]
        self.assertNotIn("TGSPLUS0001", welcome)
        self.assertNotIn("secret-link", welcome)
        self.assert_no_plus_guide(welcome)
        self.assertNotIn("Bước 1", welcome)

    async def test_main_menu_callback_screen_has_no_orders_or_guide_either(self):
        self.add_order("TGSPLUS0001", PLUS, item="https://example.com/secret-link")
        with patch.object(shop_bot, "get_stock_count", return_value=5):
            text, markup = await self.click("main_menu")
        self.assertNotIn("TGSPLUS0001", text)
        self.assert_no_plus_guide(text)
        self.assertNotIn("guide", datas(markup))

    # ---------- pressing "Don hang" shows the customer's orders ----------
    async def test_orders_button_shows_the_customers_orders_with_real_product_names(self):
        self.add_order("TGSPLUS0001", PLUS, item="https://example.com/plus")
        self.add_order("TGSPRO00001", PRO, item="a@gmail.com|pw")
        self.add_order("TGSPRO00002", PRO, user=999, item="other@gmail.com|secret")
        self.add_order("TGSPRO00003", PRO, status="pending", item=None)
        self.add_order("TGSPRO00004", PRO, status="cancelled", item=None)
        text, markup = await self.click("my_orders")
        self.assertIn("TGSPLUS0001", text)
        self.assertIn("https://example.com/plus", text)
        self.assertIn("TGSPRO00001", text)
        self.assertIn("a@gmail.com|pw", text)
        self.assertIn("ACCOUNT GEMINI PRO FULL BENEFITS", text)  # product name comes from the order's SKU
        self.assertIn("Google AI Plus", text)
        self.assertIn("Đã giao", text)
        for hidden in ("other@gmail.com|secret", "TGSPRO00002", "TGSPRO00003", "TGSPRO00004"):
            self.assertNotIn(hidden, text)
        self.assertIn("main_menu", datas(markup))

    async def test_no_orders_message(self):
        text, markup = await self.click("my_orders")
        self.assertIn("chưa có đơn hàng", text)
        self.assertEqual(datas(markup), ["main_menu"])

    async def test_orders_are_paginated(self):
        for n in range(12):
            self.add_order(f"TGSPAGE{n:04d}", PLUS, at=f"2026-11-06 10:{n:02d}:00", item=f"https://example.com/{n}")
        text, markup = await self.click("my_orders")
        self.assertIn("Trang 1/3", text)
        self.assertEqual(sum(text.count(f"TGSPAGE{n:04d}") for n in range(12)), shop_bot.ORDERS_PAGE_SIZE)
        self.assertIn("TGSPAGE0011", text)          # newest first
        self.assertNotIn("TGSPAGE0000", text)
        self.assertIn("ordp:2", datas(markup))
        self.assertNotIn("ordp:0", datas(markup))
        text, markup = await self.click("ordp:3")
        self.assertIn("Trang 3/3", text)
        self.assertEqual(sum(text.count(f"TGSPAGE{n:04d}") for n in range(12)), 2)
        self.assertIn("TGSPAGE0000", text)
        self.assertIn("ordp:2", datas(markup))
        self.assertNotIn("ordp:4", datas(markup))
        text, _ = await self.click("ordp:99")       # out-of-range page is clamped, not an error
        self.assertIn("Trang 3/3", text)

    async def test_long_order_content_is_shortened_to_fit_a_telegram_message(self):
        links = "\n".join(f"https://example.com/activate?token={n:040d}" for n in range(50))
        for n in range(5):
            self.add_order(f"TGSBIG{n:05d}", PLUS, item=links, at=f"2026-11-06 10:0{n}:00")
        text, _ = await self.click("my_orders")
        import html as html_lib, re as re_lib
        plain = html_lib.unescape(re_lib.sub(r"<[^>]+>", "", text))  # Telegram counts characters after tags are parsed
        self.assertLess(len(plain), 4096)
        self.assertIn("rút gọn", text)
        self.assertEqual(sum(text.count(f"TGSBIG{n:05d}") for n in range(5)), 5)  # every order on the page is still listed

    async def test_worst_case_page_with_160_char_product_names_still_fits_a_telegram_message(self):
        import html as html_lib, re as re_lib
        long_name = "N" * 160
        conn = shop_bot.get_db_connection()
        conn.execute("INSERT INTO products (id, name, description, price, guide_text, max_qty) VALUES (?,?,?,?,?,?)",
                     ("long_name_sku", long_name, "", 1000, "", 1))
        conn.commit()
        conn.close()
        links = "\n".join(f"https://example.com/activate?token={n:040d}" for n in range(50))
        for n in range(5):
            self.add_order(f"TGSLONG{n:05d}", "long_name_sku", item=links, at=f"2026-11-06 10:0{n}:00")
        text, _ = await self.click("my_orders")
        plain = html_lib.unescape(re_lib.sub(r"<[^>]+>", "", text))
        self.assertLess(len(plain), 4096)
        self.assertEqual(sum(text.count(f"TGSLONG{n:05d}") for n in range(5)), 5)

    # ---------- guides are per product and only inside product / order screens ----------
    async def test_guide_buttons_appear_only_for_products_that_have_a_guide(self):
        self.add_order("TGSPLUS0001", PLUS)
        self.add_order("TGSPRO00001", PRO)
        self.add_order("TGSNEW00001", NEW)
        self.add_order("TGSOLD00001", OLD)
        text, markup = await self.click("my_orders")
        guide_buttons = [d for d in datas(markup) if d and d.startswith("gd:")]
        self.assertEqual(sorted(guide_buttons), sorted([f"gd:{PLUS}", f"gd:{PRO}"]))
        self.assert_no_plus_guide(text)  # the orders list never contains guide text itself

    async def test_gemini_plus_guide_is_its_own_text_and_unchanged(self):
        text, markup = await self.click(f"gd:{PLUS}")
        self.assertEqual(text, legacy_plus_guide())
        self.assertIn("my_orders", datas(markup))

    async def test_pro_guide_is_its_own_content_without_plus_text(self):
        text, markup = await self.click(f"gd:{PRO}")
        self.assertIn("Gmail|password", text)
        self.assertIn("GEMINI PRO", text)
        self.assert_no_plus_guide(text)
        self.assertNotIn("0đ", text)
        self.assertIn(f"buy_{PRO}", datas(markup))

    async def test_product_without_guide_gets_neutral_notice_never_plus_guide(self):
        for sku in (NEW, OLD):
            text, markup = await self.click(f"gd:{sku}")
            self.assertIn("chưa có hướng dẫn riêng", text)
            self.assert_no_plus_guide(text)
            self.assertNotIn("Gmail|password", text)
            self.assertEqual([d for d in datas(markup) if d and d.startswith("buy_")], [])

    async def test_product_detail_has_guide_button_only_when_product_has_a_guide(self):
        payload = {"success": True, "products": [{
            "sku": NEW, "name": "Premium VPN", "description": "d", "type": "item",
            "price_vnd": 99000, "price_usdt": 3, "in_stock": True, "max_qty": 1}]}
        with patch.object(shop_bot, "_fetch_catalogue_payload", return_value=payload):
            shop_bot.refresh_catalogue(force=True)
        with patch.object(shop_bot, "get_stock_count", return_value=5), patch.object(shop_bot, "get_product_banner_path", return_value=None):
            _, plus_markup = await self.click("buy_gemini_plus")
            _, pro_markup = await self.click(f"buy_{PRO}") if PRO in shop_bot.known_skus() else (None, None)
            _, new_markup = await self.click(f"buy_{NEW}")
        self.assertIn(f"gd:{PLUS}", datas(plus_markup))
        self.assertNotIn(f"gd:{NEW}", datas(new_markup))
        self.assertFalse([d for d in datas(new_markup) if d and d.startswith("gd:")])

    async def test_pro_product_detail_shows_its_own_guide_button(self):
        with patch.object(shop_bot, "get_stock_count", return_value=5), patch.object(shop_bot, "get_product_banner_path", return_value=None):
            _, markup = await self.click(f"buy_{PRO}")
        self.assertIn(f"gd:{PRO}", datas(markup))
        self.assertNotIn(f"gd:{PLUS}", datas(markup))

    async def test_legacy_guide_button_no_longer_opens_a_generic_guide(self):
        text, markup = await self.click("guide")
        self.assert_no_plus_guide(text)
        self.assertNotIn("Bước 1", text)
        self.assertIn("my_orders", datas(markup))

    # ---------- data + callback hygiene ----------
    def test_guides_are_keyed_by_sku_with_required_fields_and_no_generic_fallback(self):
        self.assertTrue(product_guides.has_guide(PLUS))
        self.assertTrue(product_guides.has_guide(PRO))
        self.assertFalse(product_guides.has_guide(NEW))
        self.assertIsNone(product_guides.get_guide(NEW))
        self.assertIsNone(shop_bot.render_guide(NEW))
        for sku, by_lang in product_guides.GUIDES.items():
            self.assertEqual(set(by_lang), {"vi", "en"}, sku)  # every guide exists in both languages
            for lang, guide in by_lang.items():
                for key in ("title", "intro", "steps", "notes_title", "notes"):
                    self.assertTrue(guide.get(key), (sku, lang, key))
                self.assertTrue(all(len(note) == 3 for note in guide["notes"]), (sku, lang))

    def test_view_callbacks_are_validated_and_fit_telegram_limit(self):
        self.assertEqual(shop_bot.parse_view_callback(f"gd:{PRO}"), ("gd", PRO))
        self.assertEqual(shop_bot.parse_view_callback("ordp:3"), ("ordp", 3))
        for bad in ("gd:../etc", "gd:a b", "gd:x", "gd:" + "a" * 41, "gd:", "gd:semi;colon", "gd:has-hyphen",
                    "ordp:", "ordp:-1", "ordp:abc", "ordp:12345", "guide", "my_orders", None):
            self.assertIsNone(shop_bot.parse_view_callback(bad), bad)
        self.assertLessEqual(len(("gd:" + "a" * 40).encode()), 64)


if __name__ == "__main__":
    unittest.main()
