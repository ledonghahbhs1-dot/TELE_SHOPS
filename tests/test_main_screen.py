import html as html_lib
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import product_intros
import shop_bot

PLUS = "gemini_plus"
PRO = "gemini_pro_account"
NEW = "premium_vpn"


def item(sku, name=None, price=50000, in_stock=True, max_qty=1, usdt=2.5, type="item", description="desc"):
    return {"sku": sku, "name": name or sku.upper(), "description": description, "type": type,
            "price_vnd": price, "price_usdt": usdt, "in_stock": in_stock, "max_qty": max_qty}


def web(*products):
    return patch.object(shop_bot, "_fetch_catalogue_payload", return_value={"success": True, "products": list(products)})


def plain(text):
    return html_lib.unescape(re.sub(r"<[^>]+>", "", text))


def make_query(data):
    message = SimpleNamespace(chat_id=1, message_id=2, photo=None, reply_photo=AsyncMock(),
                              reply_text=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(data=data, answer=AsyncMock(), message=message)


def update_for(query, user_id=123):
    return SimpleNamespace(callback_query=query,
                           effective_user=SimpleNamespace(id=user_id, username="u", first_name="Khach"))


def datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def texts(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def legacy_plus_block(price, usdt):
    """The long Gemini Plus pitch exactly as the old /start screen had it (copied from the previous source)."""
    vip_em = shop_bot.vip_em
    return (
        f"{vip_em('star', '🌟')} <b>DỊCH VỤ ĐẶC QUYỀN ĐANG MỞ BÁN:</b>\n"
        f"<b>Google AI Plus 400GB (Thời Hạn 12 Tháng)</b>\n\n"
        f"{vip_em('chart', '📊')} <b>BẢNG SO SÁNH GIÁ 12 THÁNG SIÊU TIẾT KIỆM:</b>\n"
        f"├─ {vip_em('cross', '❌')} <b>Giá gốc Google:</b> <code>132.000 ₫/tháng</code> → <code>1.584.000 ₫ / 12 tháng</code>\n"
        f"├─ {vip_em('money', '💰')} <b>Giá VIP tại WolfMod:</b> <b>{price:,} VNĐ / 12 Tháng</b> (hoặc <b>{usdt} USDT</b>)\n"
        f"├─ {vip_em('calendar', '⏳')} <b>Thời hạn sử dụng:</b> <b>12 Tháng (1 Năm trọn vẹn)</b>\n"
        f"└─ {vip_em('shield', '🛡')} <b>Chính sách bảo hành:</b> <b>Bảo hành 24h (1 đổi 1 siêu tốc)</b>\n\n"
        f"{vip_em('lightning', '⚡️')} <b>Hiệu suất:</b> Gemini 3.6 Flash, Deep Research, hạn mức x2\n"
        f"{vip_em('sparkle', '✉️')} <b>Gmail AI:</b> Trợ lý Gemini hỗ trợ hiệu đính trong Gmail\n"
        f"{vip_em('star', '🎨')} <b>Sáng tạo:</b> Mở rộng tạo ảnh, nhạc, video & Gemini Notebook\n"
        f"{vip_em('box', '📦')} <b>Bộ nhớ 400 GB:</b> Dùng chung Gmail, Google Drive, Google Photos\n"
        f"{vip_em('shield', '👨‍👩‍👧‍👦')} <b>Gia đình:</b> Hỗ trợ chia sẻ tối đa 5 thành viên gia đình\n\n"
    )


PLUS_PITCH_MARKERS = ("BẢNG SO SÁNH", "132.000", "Bảo hành 24h", "Gmail AI", "Bộ nhớ 400 GB", "Hiệu suất", "Gia đình", "Sáng tạo")


class MainScreenTests(unittest.IsolatedAsyncioTestCase):
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

    def stock(self, plus=294, pro=1, new=5):
        counts = {PLUS: plus, PRO: pro, NEW: new}
        return patch.object(shop_bot, "get_stock_count", side_effect=lambda sku="gemini_plus": counts.get(sku, 0))

    async def start(self):
        update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()), callback_query=None,
                                 effective_user=SimpleNamespace(id=123, first_name="Khach"))
        await shop_bot.start_command(update, SimpleNamespace())
        return update.message.reply_text.await_args.args[0], update.message.reply_text.await_args.kwargs["reply_markup"]

    async def click(self, data, banner=None):
        query = make_query(data)
        with patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock) as render, \
             patch.object(shop_bot, "get_product_banner_path", return_value=banner):
            await shop_bot.callback_handler(update_for(query), SimpleNamespace())
        return query, render

    # ---------- main screen: short greeting + compact product list ----------
    async def test_start_screen_is_a_short_greeting_without_any_product_pitch(self):
        with self.stock():
            text, markup = await self.start()
        body = plain(text)
        self.assertIn("WOLFMOD VIP STORE", body)
        self.assertIn("Khach", body)
        self.assertLess(len(body), 260)
        self.assertLessEqual(body.count("\n"), 5)
        for marker in PLUS_PITCH_MARKERS + ("SẢN PHẨM KHÁC", "Google AI Plus", "14.999", "ACCOUNT GEMINI"):
            self.assertNotIn(marker, body)

    async def test_main_menu_callback_shows_the_same_short_greeting(self):
        with self.stock():
            _, render = await self.click("main_menu")
        body = plain(render.await_args.args[1])
        self.assertLess(len(body), 260)
        for marker in PLUS_PITCH_MARKERS:
            self.assertNotIn(marker, body)
        self.assertNotIn("guide", datas(render.await_args.kwargs["reply_markup"]))

    async def test_buttons_are_the_products_then_orders_then_support(self):
        with self.stock():
            _, markup = await self.start()
        self.assertEqual(datas(markup), ["buy_gemini_plus", f"buy_{PRO}", "my_orders", "support"])
        labels = texts(markup)
        self.assertEqual(labels[0], "MUA GOOGLE AI PLUS 12 THÁNG • %s VNĐ (Kho: 294)" % f"{shop_bot.get_product(PLUS)['price']:,}")
        self.assertIn("ACCOUNT GEMINI PRO FULL BENEFITS", labels[1])
        self.assertIn("125,000", labels[1])
        self.assertFalse([t for t in labels if "Cẩm Nang" in t or "Hướng Dẫn" in t])

    async def test_product_buttons_follow_the_web_catalogue_including_a_new_third_product(self):
        with web(item(PLUS), item(PRO, name="ACCOUNT GEMINI PRO FULL BENEFITS", price=125000, usdt=5.0),
                 item(NEW, name="Premium VPN 1 Year", price=99000)), self.stock(plus=10, pro=1, new=5):
            shop_bot.refresh_catalogue(force=True)
            _, markup = await self.start()
        self.assertEqual(datas(markup), ["buy_gemini_plus", f"buy_{PRO}", f"buy_{NEW}", "my_orders", "support"])
        self.assertTrue(any("Premium VPN 1 Year" in t and "99,000" in t for t in texts(markup)))

    async def test_out_of_stock_products_are_labelled_sold_out(self):
        with self.stock(plus=0, pro=0):
            _, markup = await self.start()
        labels, callbacks = texts(markup), datas(markup)
        self.assertIn("GOOGLE AI PLUS 12 THÁNG • %s VNĐ • HẾT HÀNG" % f"{shop_bot.get_product(PLUS)['price']:,}", labels)
        self.assertIn("buy_gemini_plus", callbacks)  # opens the intro, which says it is sold out
        self.assertIn(f"soldout_{PRO}", callbacks)
        self.assertTrue(any("HẾT HÀNG" in t and "GEMINI PRO" in t for t in labels))

    # ---------- product screens ----------
    async def test_gemini_plus_screen_contains_the_old_long_pitch_verbatim(self):
        price = shop_bot.get_product(PLUS)["price"]
        usdt = shop_bot.get_setting("usdt_price", "1")
        with self.stock(plus=294):
            _, render = await self.click("buy_gemini_plus")
        text = render.await_args.args[1]
        self.assertTrue(text.startswith(legacy_plus_block(price, usdt)), "pitch must be the old text, character for character")
        self.assertIn("<code>294</code>", text)
        keyboard = datas(render.await_args.kwargs["reply_markup"])
        self.assertEqual(keyboard[:4], ["select_qty_1", "select_qty_2", "select_qty_4", "select_qty_8"])
        self.assertIn("custom_qty", keyboard)
        self.assertIn(f"gd:{PLUS}", keyboard)
        self.assertEqual(keyboard[-1], "main_menu")

    async def test_pro_screen_uses_web_description_and_price_without_plus_text(self):
        with web(item(PLUS), item(PRO, name="ACCOUNT GEMINI PRO FULL BENEFITS", price=125000, usdt=5.0, max_qty=1,
                                  description="Tai khoan Pro tu web: Gemini Pro, Deep Research, 5 TB")), self.stock(plus=10, pro=1):
            shop_bot.refresh_catalogue(force=True)
            _, render = await self.click(f"buy_{PRO}")
        body = plain(render.await_args.args[1])
        self.assertIn("ACCOUNT GEMINI PRO FULL BENEFITS", body)
        self.assertIn("Tai khoan Pro tu web: Gemini Pro, Deep Research, 5 TB", body)
        self.assertIn("125,000 VNĐ", body)
        self.assertIn("5.0 USDT", body)
        self.assertIn("Tối đa mỗi đơn: 1", body)
        for marker in PLUS_PITCH_MARKERS + ("Google AI Plus", "400 GB", "Google One"):
            self.assertNotIn(marker, body)
        self.assertEqual(datas(render.await_args.kwargs["reply_markup"]), [f"sq:{PRO}:1", f"gd:{PRO}", "main_menu"])

    async def test_discovered_product_gets_neutral_intro_from_web_data(self):
        with web(item(NEW, name="Premium VPN", price=99000, usdt=3.0, max_qty=3, description="Fast VPN for 1 year")), self.stock():
            shop_bot.refresh_catalogue(force=True)
            _, render = await self.click(f"buy_{NEW}")
        body = plain(render.await_args.args[1])
        self.assertIn("Premium VPN", body)
        self.assertIn("Fast VPN for 1 year", body)
        self.assertIn("99,000 VNĐ", body)
        self.assertIn("Tối đa mỗi đơn: 3", body)
        for marker in PLUS_PITCH_MARKERS + ("Google AI Plus",):
            self.assertNotIn(marker, body)
        keyboard = datas(render.await_args.kwargs["reply_markup"])
        self.assertEqual(keyboard[0], f"sq:{NEW}:1")
        self.assertFalse([d for d in keyboard if d.startswith("gd:")])  # no guide of its own -> no guide button

    async def test_sold_out_product_screen_keeps_the_intro_but_offers_no_purchase(self):
        with self.stock(plus=0):
            _, render = await self.click("buy_gemini_plus")
        text = render.await_args.args[1]
        self.assertTrue(text.startswith(legacy_plus_block(shop_bot.get_product(PLUS)["price"], shop_bot.get_setting("usdt_price", "1"))))
        self.assertIn("HẾT HÀNG", text)
        keyboard = datas(render.await_args.kwargs["reply_markup"])
        self.assertEqual(keyboard, [f"gd:{PLUS}", "main_menu"])

    async def test_banner_caption_stays_within_telegram_limit(self):
        banner = Path(self.directory.name) / "banner.jpg"
        banner.write_bytes(b"\xff\xd8\xff\xd9")
        with self.stock(plus=294):
            query, render = await self.click("buy_gemini_plus", banner=str(banner))
        render.assert_not_awaited()
        if query.message.reply_photo.await_count and query.message.reply_text.await_count == 0:
            caption = query.message.reply_photo.await_args.kwargs["caption"]
            self.assertLessEqual(len(plain(caption)), 1024)
            self.assertTrue(caption.startswith(legacy_plus_block(shop_bot.get_product(PLUS)["price"], shop_bot.get_setting("usdt_price", "1"))))
        else:  # intro too long for a caption: short photo caption, then the full intro as its own message with the buttons
            self.assertLessEqual(len(plain(query.message.reply_photo.await_args.kwargs["caption"])), 1024)
            self.assertIn("reply_markup", query.message.reply_text.await_args.kwargs)

    async def test_long_intro_with_banner_is_split_into_photo_plus_text(self):
        banner = Path(self.directory.name) / "banner.jpg"
        banner.write_bytes(bytes([0xFF, 0xD8, 0xFF, 0xD9]))
        long_text = "LONGINTRO " * 120  # a future product-specific intro longer than a photo caption allows
        long_intro = lambda em, price, usdt: long_text + "\n\n"
        with web(item(NEW, name="Premium VPN")), self.stock(), patch.dict(product_intros.INTROS, {NEW: long_intro}):
            shop_bot.refresh_catalogue(force=True)
            query, _ = await self.click(f"buy_{NEW}", banner=str(banner))
        query.message.reply_photo.assert_awaited_once()
        self.assertLessEqual(len(plain(query.message.reply_photo.await_args.kwargs["caption"])), 1024)
        self.assertNotIn("reply_markup", query.message.reply_photo.await_args.kwargs)
        query.message.reply_text.assert_awaited_once()
        self.assertIn("LONGINTRO", query.message.reply_text.await_args.args[0])
        self.assertIn(f"sq:{NEW}:1", datas(query.message.reply_text.await_args.kwargs["reply_markup"]))

    # ---------- data and callbacks ----------
    def test_intro_registry_is_keyed_by_sku_and_plus_is_the_only_special_case(self):
        self.assertTrue(product_intros.has_own_intro(PLUS))
        self.assertFalse(product_intros.has_own_intro(PRO))
        self.assertFalse(product_intros.has_own_intro(NEW))

    def test_all_menu_and_product_callbacks_fit_telegram_limit(self):
        longest = "a" * 40
        for data in (f"buy_{longest}", f"soldout_{longest}", f"gd:{longest}", f"cq:{longest}",
                     shop_bot.cb_qty("select", longest, 50), shop_bot.cb_qty("vietqr", longest, 50), shop_bot.cb_qty("usdt", longest, 50)):
            self.assertLessEqual(len(data.encode()), 64, data)
        with self.stock():
            for button in [b for row in shop_bot.main_menu_keyboard(1).inline_keyboard for b in row]:
                self.assertLessEqual(len(button.callback_data.encode()), 64)

    # ---------- 017 behaviour keeps working on this branch ----------
    async def test_orders_button_shows_orders_directly_with_real_product_names_and_guide_buttons(self):
        conn = shop_bot.get_db_connection()
        for order_id, sku, item_text in (("TGSPLUS0001", PLUS, "https://example.com/a"), ("TGSPRO00001", PRO, "a@gmail.com|pw")):
            conn.execute("INSERT INTO orders (order_id,user_id,product_id,amount,status,item_delivered,completed_at) VALUES (?,?,?,?,?,?,?)",
                         (order_id, 123, sku, 1000, "completed", item_text, "2026-11-06 10:00:00"))
        conn.commit()
        conn.close()
        _, render = await self.click("my_orders")
        text = render.await_args.args[1]
        for expected in ("TGSPLUS0001", "https://example.com/a", "TGSPRO00001", "a@gmail.com|pw", "ACCOUNT GEMINI PRO FULL BENEFITS", "Đã giao"):
            self.assertIn(expected, text)
        keyboard = datas(render.await_args.kwargs["reply_markup"])
        self.assertIn(f"gd:{PLUS}", keyboard)
        self.assertIn(f"gd:{PRO}", keyboard)


if __name__ == "__main__":
    unittest.main()
