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
OLD = "retired_product"  # bought in the past, no longer sold


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
    """The "Cẩm nang kích hoạt" screen exactly as it was before this change."""
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
        with patch.dict(os.environ, {"USDT_WALLET": ""}):
            shop_bot.init_database()

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

    # ---------- main menu ----------
    def test_main_menu_has_orders_and_generic_guide_buttons_and_no_product_specific_guide(self):
        with patch.object(shop_bot, "get_stock_count", return_value=5):
            keyboard = shop_bot.main_menu_keyboard(1)
        labels, callbacks = texts(keyboard), datas(keyboard)
        self.assertIn("my_orders", callbacks)
        self.assertEqual(callbacks.count("guide"), 1)
        self.assertIn("Hướng Dẫn Sử Dụng", labels)
        self.assertFalse([t for t in labels if "Cẩm Nang" in t or "400GB" in t])

    # ---------- orders: product list, then that product's orders ----------
    async def test_orders_button_lists_products_not_orders(self):
        self.add_order("TGSPLUS0001", PLUS)
        self.add_order("TGSPRO00001", PRO, item="a@gmail.com|pw")
        text, markup = await self.click("my_orders")
        self.assertIn(f"ord:{PLUS}", datas(markup))
        self.assertIn(f"ord:{PRO}", datas(markup))
        self.assertNotIn("TGSPLUS0001", text)
        self.assertNotIn("a@gmail.com|pw", text)
        self.assertIn("main_menu", datas(markup))
        self.assertTrue(any("(1 đơn)" in label for label in texts(markup)))

    async def test_product_orders_are_filtered_by_sku_and_owner(self):
        self.add_order("TGSPLUS0001", PLUS, item="https://example.com/plus")
        self.add_order("TGSPRO00001", PRO, item="a@gmail.com|pw")
        self.add_order("TGSPRO00002", PRO, user=999, item="other@gmail.com|secret")
        self.add_order("TGSPRO00003", PRO, status="pending", item=None)
        text, markup = await self.click(f"ord:{PRO}")
        self.assertIn("TGSPRO00001", text)
        self.assertIn("a@gmail.com|pw", text)
        self.assertIn("Thông tin nhận hàng", text)
        self.assertNotIn("TGSPLUS0001", text)
        self.assertNotIn("other@gmail.com|secret", text)  # someone else's order
        self.assertNotIn("TGSPRO00003", text)             # unpaid orders are never listed
        self.assertIn(f"gd:{PRO}", datas(markup))
        self.assertIn("my_orders", datas(markup))
        text, _ = await self.click(f"ord:{PLUS}")
        self.assertIn("TGSPLUS0001", text)
        self.assertIn("Link kích hoạt", text)
        self.assertNotIn("TGSPRO00001", text)

    async def test_retired_product_orders_stay_viewable_without_a_buy_button(self):
        self.add_order("TGSOLD00001", OLD, item="old-content")
        text, markup = await self.click("my_orders")
        self.assertIn(f"ord:{OLD}", datas(markup))
        text, markup = await self.click(f"ord:{OLD}")
        self.assertIn("TGSOLD00001", text)
        self.assertIn("old-content", text)
        self.assertEqual([d for d in datas(markup) if d and d.startswith("buy_")], [])

    async def test_empty_product_orders_screen(self):
        text, markup = await self.click(f"ord:{PRO}")
        self.assertIn("chưa có đơn hàng", text)
        self.assertIn(f"buy_{PRO}", datas(markup))

    # ---------- guides ----------
    async def test_guide_button_lists_products_and_shows_no_product_guide_itself(self):
        text, markup = await self.click("guide")
        self.assertIn(f"gd:{PLUS}", datas(markup))
        self.assertIn(f"gd:{PRO}", datas(markup))
        self.assertNotIn("Quy trình 3 bước", text)
        self.assertNotIn("Gemini Plus 2TB", text)
        self.assertNotIn("Chấp nhận (Accept)", text)

    async def test_gemini_plus_guide_text_is_unchanged(self):
        text, markup = await self.click(f"gd:{PLUS}")
        self.assertEqual(text, legacy_plus_guide())
        self.assertIn("buy_gemini_plus", datas(markup))

    async def test_pro_guide_is_its_own_content(self):
        text, markup = await self.click(f"gd:{PRO}")
        self.assertIn("Gmail|password", text)
        self.assertIn("GEMINI PRO", text)
        for plus_only in ("Gemini Plus 2TB", "Chấp nhận (Accept)", "Nhấp vào Link", "0đ", "tranh chấp gia đình"):
            self.assertNotIn(plus_only, text)
        self.assertIn(f"buy_{PRO}", datas(markup))

    async def test_unknown_product_gets_neutral_generic_guide_not_another_products(self):
        self.add_order("TGSOLD00002", OLD)
        text, markup = await self.click(f"gd:{OLD}")
        self.assertIn(product_guides.GENERIC_GUIDE["intro"], text)
        self.assertIn(OLD.upper(), text)
        for other in ("Gemini Plus 2TB", "Chấp nhận (Accept)", "Gmail|password"):
            self.assertNotIn(other, text)
        self.assertEqual([d for d in datas(markup) if d and d.startswith("buy_")], [])

    def test_every_catalogue_sku_gets_a_guide_and_data_has_required_fields(self):
        for sku, guide in {**product_guides.GUIDES, "generic": product_guides.GENERIC_GUIDE}.items():
            for key in ("title", "intro", "steps", "notes_title", "notes"):
                self.assertTrue(guide.get(key), (sku, key))
            self.assertTrue(all(len(note) == 3 for note in guide["notes"]), sku)
        for sku in shop_bot.SHOP_SKUS:
            self.assertTrue(shop_bot.render_guide(sku))

    # ---------- callback hygiene ----------
    def test_view_callbacks_are_validated_and_fit_telegram_limit(self):
        self.assertEqual(shop_bot.parse_view_callback(f"ord:{PRO}"), ("ord", PRO))
        self.assertEqual(shop_bot.parse_view_callback("gd:Mixed_Case9"), ("gd", "Mixed_Case9"))
        for bad in ("gd:../etc", "ord:a b", "gd:x", "gd:" + "a" * 41, "ord:", "ord:semi;colon", "gd:has-hyphen", "order:abc", None):
            self.assertIsNone(shop_bot.parse_view_callback(bad), bad)
        self.assertLessEqual(len(("ord:" + "a" * 40).encode()), 64)
        self.assertLessEqual(len(("gd:" + "a" * 40).encode()), 64)

    async def test_order_and_guide_screens_work_after_catalogue_discovery(self):
        payload = {"success": True, "products": [{
            "sku": "premium_vpn", "name": "Premium VPN", "description": "d", "type": "item",
            "price_vnd": 99000, "price_usdt": 3, "in_stock": True, "max_qty": 1}]}
        with patch.object(shop_bot, "_fetch_catalogue_payload", return_value=payload):
            shop_bot.refresh_catalogue(force=True)
        self.addCleanup(lambda: shop_bot._catalogue.update(items=None, next=0.0))
        text, markup = await self.click("guide")
        self.assertIn("gd:premium_vpn", datas(markup))
        text, markup = await self.click("gd:premium_vpn")
        self.assertIn("PREMIUM VPN", text)
        self.assertIn("buy_premium_vpn", datas(markup))


if __name__ == "__main__":
    unittest.main()
