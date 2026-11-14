"""Task 046: i18n (vi/en), language persistence, grouped "Mua hàng" menu, ChatGPT Go 3 months, stock notifications."""
import ast
import asyncio
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import i18n
import product_content
import product_guides
import shop_bot
import stock_notify

ROOT = Path(__file__).resolve().parent.parent
PLUS, PRO, GPT, NEW = "gemini_plus", "gemini_pro_account", "chatgpt_go_3m", "premium_vpn"


def item(sku, name=None, price=50000, in_stock=True, max_qty=1, usdt=None):
    return {"sku": sku, "name": name or sku.upper(), "description": "desc", "type": "item",
            "price_vnd": price, "price_usdt": usdt, "in_stock": in_stock, "max_qty": max_qty}


def web(*products):
    return patch.object(shop_bot, "_fetch_catalogue_payload", return_value={"success": True, "products": list(products)})


def datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


def texts(markup):
    return [b.text for row in markup.inline_keyboard for b in row]


def plain(text):
    import html
    return html.unescape(re.sub(r"<[^>]+>", "", text))


def make_query(data):
    message = SimpleNamespace(chat_id=1, message_id=2, photo=None, reply_photo=AsyncMock(),
                              reply_text=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(data=data, answer=AsyncMock(), message=message, edit_message_text=AsyncMock())


def update_for(query, user_id=555):
    return SimpleNamespace(callback_query=query,
                           effective_user=SimpleNamespace(id=user_id, username="u", first_name="Minh"))


VI_ONLY_WORDS = ("Quý Khách", "Trở Về", "Hướng dẫn", "Đơn hàng", "Đơn Hàng", "Thanh toán", "Số lượng", "Tồn kho",
                 "Bảo hành", "Thời hạn", "Giá:", "Sản phẩm", "Liên hệ")


def _t_keys_used_in(path: Path) -> set[str]:
    """Every literal key passed to t(lang, "key") / i18n.t(...), including both branches of `"a" if c else "b"`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or len(node.args) < 2:
            continue
        func = node.func
        is_t = (isinstance(func, ast.Name) and func.id == "t") or (
            isinstance(func, ast.Attribute) and func.attr == "t" and isinstance(func.value, ast.Name) and func.value.id == "i18n")
        if not is_t:
            continue
        arg = node.args[1]
        branches = [arg.body, arg.orelse] if isinstance(arg, ast.IfExp) else [arg]
        for branch in branches:
            if isinstance(branch, ast.Constant) and isinstance(branch.value, str):
                keys.add(branch.value)
    return keys


class CatalogueTests(unittest.TestCase):
    """Pure tests: no DB, no network."""

    def test_every_string_has_non_empty_vi_and_en(self):
        for key, entry in i18n.STRINGS.items():
            for lang in i18n.SUPPORTED_LANGS:
                self.assertIn(lang, entry, (key, lang))
                if key not in i18n.ALLOW_EMPTY:
                    self.assertTrue(entry[lang].strip(), (key, lang))

    def test_vi_and_en_use_the_same_placeholders(self):
        field = re.compile(r"\{([a-z_0-9]+)\}")
        for key, entry in i18n.STRINGS.items():
            self.assertEqual(set(field.findall(entry["vi"])), set(field.findall(entry["en"])), key)

    def test_every_key_used_in_code_exists_in_the_catalogue(self):
        used: set[str] = set()
        for name in ("shop_bot.py", "product_intros.py", "stock_notify.py"):
            used |= _t_keys_used_in(ROOT / name)
        self.assertGreater(len(used), 80)  # the scan really found the call sites
        self.assertEqual(sorted(k for k in used if k not in i18n.STRINGS), [])

    def test_no_unused_catalogue_keys_are_missing_a_language(self):
        # the scan above would not catch keys used via other helpers; make sure nothing is half-translated
        for key, entry in i18n.STRINGS.items():
            self.assertEqual(set(entry), {"vi", "en"}, key)

    def test_missing_translation_falls_back_to_english_and_logs_a_warning(self):
        with patch.dict(i18n.STRINGS, {"only_en_demo": {"en": "Hello {name}"}}), patch.object(i18n, "_warned", set()):
            with self.assertLogs("i18n", level="WARNING") as logs:
                self.assertEqual(i18n.t("vi", "only_en_demo", name="Ann"), "Hello Ann")
        self.assertTrue(any("only_en_demo" in line for line in logs.output))
        with patch.object(i18n, "_warned", set()), self.assertLogs("i18n", level="WARNING"):
            self.assertEqual(i18n.t("en", "no_such_key_at_all"), "no_such_key_at_all")

    def test_unknown_language_normalises_to_default(self):
        self.assertEqual(i18n.normalize_lang("fr"), i18n.DEFAULT_LANG)
        self.assertEqual(i18n.normalize_lang(None), "en")
        self.assertEqual(i18n.normalize_lang("VI"), "vi")
        self.assertEqual(i18n.normalize_lang("vi-VN"), "vi")

    def test_product_content_is_complete_in_both_languages(self):
        skus = product_content.listed_skus()
        self.assertEqual(set(skus), {PLUS, PRO, GPT})
        for sku in skus:
            for lang in i18n.SUPPORTED_LANGS:
                self.assertTrue(product_content.name(sku, lang), (sku, lang))
                self.assertTrue(product_content.short_name(sku, lang), (sku, lang))
                self.assertTrue(product_content.field(sku, "delivery", lang), (sku, lang))
                self.assertTrue(product_content.notes(sku, lang), (sku, lang))
            self.assertEqual(len(product_content.features(sku, "vi")), len(product_content.features(sku, "en")), sku)
            self.assertGreaterEqual(len(product_content.features(sku, "en")), 3, sku)

    def test_chatgpt_go_content_is_flagged_for_owner_approval_with_source(self):
        gpt = product_content.get(GPT)
        self.assertFalse(gpt["needs_owner_approval"])      # content now supplied by Goof
        self.assertEqual(gpt["delivery_kind"], "offer_link")
        self.assertEqual(gpt["warranty"]["vi"], "Bảo hành 1 đổi 1, liên hệ CSKH nếu lỗi.")
        self.assertEqual(gpt["warranty"]["en"], "1-for-1 replacement warranty, contact support if faulty.")
        self.assertEqual(len(gpt["features"]["vi"]), 4)
        self.assertEqual(len(gpt["features"]["en"]), 4)
        self.assertIn("GPT-5", gpt["features"]["en"][1])
        self.assertEqual(product_content.price_fallback(GPT), 100000)

    def test_guides_exist_in_both_languages(self):
        for sku in product_guides.GUIDES:
            for lang in i18n.SUPPORTED_LANGS:
                self.assertIsNotNone(product_guides.get_guide(sku, lang), (sku, lang))

    def test_bot_rate_is_25000_vnd_per_usd_and_chatgpt_go_is_4_usd(self):
        self.assertEqual(i18n.BOT_VND_PER_USD, 25000)
        self.assertEqual(i18n.usd_from_vnd(100000), 4.0)
        self.assertEqual(i18n.fmt_usd(100000), "4.00 USD")
        self.assertEqual(i18n.fmt_vnd(100000), "100,000 VNĐ")

    # ---- stock_notify (pure) ----
    def test_notifier_sends_first_addition_then_merges_within_five_minutes(self):
        now = [1000.0]
        notifier = stock_notify.StockNotifier(clock=lambda: now[0])
        notifier.record(PLUS, 3)
        self.assertEqual(notifier.pop_due(), [(PLUS, 3)])      # first one goes out straight away
        notifier.record(PLUS, 2)
        now[0] += 60
        notifier.record(PLUS, 5)
        now[0] += 120
        self.assertEqual(notifier.pop_due(), [])               # still inside the 5-minute window
        now[0] += 150                                          # 330 s after the first message
        self.assertEqual(notifier.pop_due(), [(PLUS, 7)])      # merged into ONE message
        self.assertEqual(notifier.pop_due(), [])
        notifier.record(PRO, 1)                                # another product is independent
        self.assertEqual(notifier.pop_due(), [(PRO, 1)])

    def test_group_by_lang_dedupes_and_normalises(self):
        groups = stock_notify.group_by_lang([(1, "vi"), (2, "en"), (3, "vi"), (1, "en"), (4, "xx")])
        self.assertEqual(groups, {"vi": [1, 3], "en": [2, 4]})

    def test_message_has_name_features_price_stock_and_no_technical_code(self):
        em = lambda name, fallback="": fallback  # noqa: E731
        feats = product_content.features(GPT, "en") + ["extra 1", "extra 2"]
        for lang, title, name in (("vi", "HÀNG MỚI VỀ KHO", "CHATGPT GO 3 THÁNG"), ("en", "NEW STOCK ARRIVED", "ChatGPT Go – 3 Months")):
            text = stock_notify.build_message(lang, name=name, features=feats, price_vnd=100000, stock=7, added=3, em=em)
            body = plain(text)
            self.assertIn(title, body)
            self.assertIn(name, body)
            self.assertIn("100,000 VNĐ (≈ 4.00 USD)", body)
            self.assertIn("+3", body)
            self.assertIn("7", body)
            self.assertLessEqual(body.count("• "), stock_notify.MAX_FEATURE_LINES)
            for code in (GPT, PRO, PLUS, "_3m", "gemini_", "sku"):
                self.assertNotIn(code, text)
        self.assertIn("Kho khả dụng", plain(stock_notify.build_message("vi", name="X", features=[], price_vnd=0, stock=1, added=1, em=em)))
        self.assertIn("Available stock", plain(stock_notify.build_message("en", name="X", features=[], price_vnd=0, stock=1, added=1, em=em)))


class BotI18nTests(unittest.IsolatedAsyncioTestCase):
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
        # Hard guarantee: these tests never reach the real website/Telegram; any stray HTTP call fails fast.
        no_network = patch("urllib.request.urlopen", side_effect=OSError("network disabled in tests"))
        no_network.start()
        self.addCleanup(no_network.stop)
        catalogue = patch.object(shop_bot, "_catalogue", {"items": None, "next": 0.0})
        catalogue.start()
        self.addCleanup(catalogue.stop)
        shop_bot._web_stock_cache.clear()
        with patch.dict(os.environ, {"USDT_WALLET": ""}):
            shop_bot.init_database()

    def set_plus_price(self, price):
        conn = shop_bot.get_db_connection()
        conn.execute("UPDATE products SET price = ? WHERE id = 'gemini_plus'", (price,))
        conn.commit()
        conn.close()

    def stock(self, **counts):
        return patch.object(shop_bot, "get_stock_count", side_effect=lambda sku="gemini_plus": counts.get(sku, 0))

    async def click(self, data, user_id=555):
        query = make_query(data)
        with patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock) as render, \
             patch.object(shop_bot, "get_product_banner_path", return_value=None):
            await shop_bot.callback_handler(update_for(query, user_id), SimpleNamespace())
        return query, render

    # ---------- language save / load ----------
    def test_language_is_saved_and_loaded_per_user(self):
        self.assertEqual(shop_bot.set_user_lang(1, "vi"), "vi")
        self.assertEqual(shop_bot.set_user_lang(2, "en"), "en")
        self.assertEqual(shop_bot.get_user_lang(1), "vi")
        self.assertEqual(shop_bot.get_user_lang(2), "en")
        shop_bot.set_user_lang(1, "en")                       # update, not duplicate
        self.assertEqual(shop_bot.get_user_lang(1), "en")
        conn = shop_bot.get_db_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM user_lang").fetchone()[0], 2)
        conn.close()
        self.assertEqual(shop_bot.set_user_lang(3, "klingon"), "en")  # invalid value never stored

    def test_new_users_default_to_english_existing_customers_keep_vietnamese(self):
        self.assertEqual(shop_bot.get_user_lang(900), "en")                   # brand-new user
        conn = shop_bot.get_db_connection()
        conn.execute("INSERT INTO orders (order_id,user_id,product_id,amount,status) VALUES ('TGSOLD1',901,'gemini_plus',20000,'completed')")
        conn.commit()
        conn.close()
        self.assertEqual(shop_bot.get_user_lang(901), "vi")                   # has order history
        shop_bot.set_user_lang(901, "en")                                     # explicit choice always wins
        self.assertEqual(shop_bot.get_user_lang(901), "en")

    def test_migration_is_idempotent_and_keeps_existing_data(self):
        shop_bot.set_user_lang(7, "vi")
        conn = shop_bot.get_db_connection()
        conn.execute("INSERT INTO orders (order_id,user_id,product_id,amount,status) VALUES ('TGSKEEP',7,'gemini_plus',20000,'completed')")
        conn.commit()
        conn.close()
        for _ in range(3):
            with patch.dict(os.environ, {"USDT_WALLET": ""}):
                shop_bot.init_database()
        self.assertEqual(shop_bot.get_user_lang(7), "vi")
        conn = shop_bot.get_db_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders WHERE order_id='TGSKEEP'").fetchone()[0], 1)
        conn.close()

    # ---------- menus ----------
    async def test_main_menu_in_both_languages_with_old_callback_data(self):
        expected = {
            "vi": ["Mua hàng", "Đơn đã mua", "Hỗ trợ", "Ngôn ngữ: Tiếng Việt"],
            "en": ["Shop", "My Orders", "Support", "Language: English"],
        }
        for lang, labels in expected.items():
            shop_bot.set_user_lang(555, lang)
            update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()), callback_query=None,
                                     effective_user=SimpleNamespace(id=555, first_name="Minh"))
            with patch.object(shop_bot, "refresh_catalogue"):
                await shop_bot.start_command(update, SimpleNamespace())
            text, markup = update.message.reply_text.await_args.args[0], update.message.reply_text.await_args.kwargs["reply_markup"]
            self.assertEqual(texts(markup), labels)
            self.assertEqual(datas(markup), ["shop", "my_orders", "support", "lang_menu"])
            self.assertIn("Minh", text)
        self.assertIn("Welcome", plain(shop_bot.welcome_message(SimpleNamespace(first_name="Minh"), "en")))
        self.assertIn("Kính chào", plain(shop_bot.welcome_message(SimpleNamespace(first_name="Minh"), "vi")))

    async def test_shop_menu_groups_the_three_products_in_both_languages(self):
        self.set_plus_price(14999)
        with web(item(PLUS, price=14999), item(PRO, price=125000), item(GPT, price=100000)), \
             self.stock(**{PLUS: 293, PRO: 1, GPT: 4}):
            shop_bot.refresh_catalogue(force=True)
            for lang, buy, name in (("vi", "MUA", "CHATGPT GO 3 THÁNG"), ("en", "BUY", "CHATGPT GO 3 MONTHS")):
                shop_bot.set_user_lang(555, lang)
                markup = shop_bot.shop_keyboard(555)
                self.assertEqual(datas(markup), ["buy_gemini_plus", f"buy_{PRO}", f"buy_{GPT}", "main_menu"])
                labels = texts(markup)
                self.assertTrue(labels[0].startswith("GOOGLE AI PLUS 12 "), labels[0])
                self.assertFalse(labels[0].upper().startswith(buy), labels[0])
                self.assertNotIn("14,999", labels[0])
                self.assertNotIn("293", labels[0])  # nút không hiện tồn kho
                self.assertIn(name, labels[2])
                self.assertNotIn("100,000", labels[2])

    async def test_language_switch_via_callbacks_persists_and_rerenders_menu(self):
        _, render = await self.click("lang_menu")
        self.assertEqual(datas(render.await_args.kwargs["reply_markup"]), ["setlang:vi", "setlang:en", "main_menu"])
        _, render = await self.click("setlang:vi")
        self.assertEqual(shop_bot.get_user_lang(555), "vi")
        self.assertEqual(texts(render.await_args.kwargs["reply_markup"])[0], "Mua hàng")
        _, render = await self.click("setlang:en")
        self.assertEqual(shop_bot.get_user_lang(555), "en")
        self.assertEqual(texts(render.await_args.kwargs["reply_markup"])[0], "Shop")
        _, render = await self.click("shop")
        self.assertIn("SHOP", plain(render.await_args.args[1]))

    async def test_language_command(self):
        update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()), callback_query=None,
                                 effective_user=SimpleNamespace(id=555, first_name="Minh"))
        await shop_bot.language_command(update, SimpleNamespace())
        markup = update.message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual(datas(markup), ["setlang:vi", "setlang:en", "main_menu"])
        self.assertIn("Choose your language", update.message.reply_text.await_args.args[0])

    async def test_old_callback_data_from_previous_menus_still_work(self):
        shop_bot.set_user_lang(555, "vi")
        with web(item(PLUS, price=14999, max_qty=50), item(PRO, price=125000)), self.stock(**{PLUS: 10, PRO: 1}):
            shop_bot.refresh_catalogue(force=True)
            for data in ("main_menu", "buy_gemini_plus", f"buy_{PRO}", "my_orders", "support", "select_qty_1",
                         "custom_qty", f"gd:{PLUS}", "guide", f"sq:{PRO}:1", "ordp:1"):
                query, render = await self.click(data)
                self.assertTrue(render.await_count >= 1, data)
            query, _ = await self.click(f"soldout_{PRO}")
            query.answer.assert_awaited()
        with web(item(PLUS, price=14999), item(PRO, price=125000)), self.stock(**{PLUS: 10, PRO: 0}):
            shop_bot.refresh_catalogue(force=True)
            query, _ = await self.click(f"soldout_{PRO}")
            self.assertIn("hết hàng", query.answer.await_args.args[0])

    # ---------- every customer screen in both languages ----------
    async def test_core_screens_render_in_both_languages_without_cross_language_leaks(self):
        with web(item(PLUS, price=14999, max_qty=50), item(PRO, price=125000), item(GPT, price=100000, max_qty=1)), \
             self.stock(**{PLUS: 10, PRO: 1, GPT: 2}):
            shop_bot.refresh_catalogue(force=True)
            for lang, vi_marker in (("vi", True), ("en", False)):
                shop_bot.set_user_lang(555, lang)
                for data in ("main_menu", "shop", "my_orders", "support", f"buy_{PLUS}", f"buy_{PRO}", f"buy_{GPT}",
                             f"gd:{PLUS}", f"gd:{PRO}", f"sq:{GPT}:1", "select_qty_2", "custom_qty", "guide", "lang_menu"):
                    _, render = await self.click(data)
                    body = plain(render.await_args.args[1])
                    if lang == "en" and data != "lang_menu":
                        for word in VI_ONLY_WORDS:
                            self.assertNotIn(word, body, (data, word))
                    if lang == "vi" and data != "lang_menu":
                        self.assertTrue(re.search(r"[ăâđêôơưáàạảãéèẹẻẽíìịỉĩóòọỏõúùụủũýỳỵỷỹ]", body), (data, body[:100]))

    async def test_purchase_flow_confirmation_and_bill_in_english(self):
        shop_bot.set_user_lang(555, "en")
        with web(item(PLUS, price=14999, max_qty=50), item(GPT, price=100000)), self.stock(**{PLUS: 10, GPT: 5}), \
             patch.object(shop_bot, "generate_vietqr_png_bytes", return_value=None), \
             patch.object(shop_bot, "sync_order_to_web_async", new_callable=AsyncMock):
            shop_bot.refresh_catalogue(force=True)
            _, render = await self.click(f"sq:{GPT}:1")
            body = plain(render.await_args.args[1])
            self.assertIn("VIP ORDER CONFIRMATION", body)
            self.assertIn("100,000 VND", body)
            self.assertIn("4.0 USDT", body)
            self.assertIn("Pay with VietQR", " ".join(texts(render.await_args.kwargs["reply_markup"])))
            _, render = await self.click(f"cv:{GPT}:1")
            body = plain(render.await_args.args[1])
            self.assertIn("VIETQR VIP INVOICE", body)
            self.assertIn("Transfer note", body)
            self.assertIn("ChatGPT Go – 3 Months", body)
            self.assertIn("I HAVE PAID", " ".join(texts(render.await_args.kwargs["reply_markup"])))

    def test_delivery_message_follows_the_customer_language(self):
        vi, _ = shop_bot.format_delivered_links_message("TGS1", ["https://example.com/a"], PLUS, "vi")
        en, buttons = shop_bot.format_delivered_links_message("TGS1", ["https://example.com/a"], PLUS, "en")
        self.assertIn("THANH TOÁN THÀNH CÔNG", vi)
        self.assertIn("PAYMENT SUCCESSFUL", en)
        self.assertIn("Activation in 3 steps", en)
        self.assertIn("Open VIP Activation Link", texts(SimpleNamespace(inline_keyboard=buttons)))
        acct, _ = shop_bot.format_delivered_links_message("TGS2", ["a@gmail.com|pw"], PRO, "en")
        self.assertIn("GEMINI PRO ACCOUNT", acct)
        self.assertIn("Delivery details (1)", acct)

    def test_orders_page_and_guides_follow_language(self):
        conn = shop_bot.get_db_connection()
        conn.execute("INSERT INTO orders (order_id,user_id,product_id,amount,status,item_delivered,completed_at) "
                     "VALUES ('TGSA',555,'gemini_pro_account',1000,'completed','a@gmail.com|pw','2026-11-06 10:00:00')")
        conn.commit()
        conn.close()
        text_en, markup_en = shop_bot.render_orders_page(555, 1, "en")
        text_vi, markup_vi = shop_bot.render_orders_page(555, 1, "vi")
        self.assertIn("YOUR VIP ORDERS", text_en)
        self.assertIn("Delivered", text_en)
        self.assertIn("ĐƠN HÀNG VIP ĐÃ MUA", text_vi)
        self.assertIn("Guide:", texts(markup_en)[0])
        self.assertIn("Hướng dẫn:", texts(markup_vi)[0])
        self.assertIn("Step 1", shop_bot.render_guide(PRO, "en"))
        self.assertIn("Bước 1", shop_bot.render_guide(PRO, "vi"))
        self.assertIn("Sign in to your ChatGPT account", shop_bot.render_guide(GPT, "en"))

    # ---------- ChatGPT Go ----------
    async def test_chatgpt_go_detail_page_shows_100000_vnd_and_4_00_usd_in_both_languages(self):
        with web(item(PLUS, price=14999), item(GPT, price=100000, name="CHATGPT GO 3 THÁNG")), self.stock(**{GPT: 4}):
            shop_bot.refresh_catalogue(force=True)
            shop_bot.set_user_lang(555, "vi")
            _, render = await self.click(f"buy_{GPT}")
            vi = plain(render.await_args.args[1])
            shop_bot.set_user_lang(555, "en")
            _, render = await self.click(f"buy_{GPT}")
            en = plain(render.await_args.args[1])
        for body in (vi, en):
            self.assertIn("100,000 VNĐ (≈ 4.00 USD)", body)
            self.assertNotIn("Google AI Plus", body)
        self.assertIn("CHATGPT GO 3 THÁNG", vi)
        self.assertIn("Thời hạn: 3 Tháng", vi)
        self.assertIn("Quyền lợi:", vi)
        self.assertIn("Cách giao hàng:", vi)
        self.assertIn("ChatGPT Go – 3 Months", en)
        self.assertIn("Duration: 3 months", en)
        self.assertIn("Benefits:", en)
        self.assertIn("Delivery:", en)

    async def test_chatgpt_go_price_comes_from_the_fixed_bot_rate_not_the_web_usdt_rate(self):
        with web(item(GPT, price=100000, usdt=None)):
            shop_bot.refresh_catalogue(force=True)
            self.assertEqual(shop_bot.get_usdt_price(GPT), 4.0)
            self.assertEqual(shop_bot.get_usdt_price(PRO), 1.0)    # existing products keep the old behaviour

    async def test_chatgpt_go_out_of_stock_is_labelled_and_blocks_purchase(self):
        for lang, label, notice in (("vi", "HẾT HÀNG", "HẾT HÀNG"), ("en", "OUT OF STOCK", "OUT OF STOCK")):
            shop_bot.set_user_lang(555, lang)
            with web(item(PLUS, price=14999), item(GPT, price=100000, in_stock=False)), self.stock(**{PLUS: 5, GPT: 0}):
                shop_bot.refresh_catalogue(force=True)
                markup = shop_bot.shop_keyboard(555)
                gpt_label = [txt for txt in texts(markup) if "CHATGPT" in txt][0]
                self.assertIn(label, gpt_label)
                self.assertIn(f"soldout_{GPT}", datas(markup))
                self.assertNotIn(f"buy_{GPT}", datas(markup))
                query, _ = await self.click(f"soldout_{GPT}")
                self.assertTrue(query.answer.await_args.args[0])
                _, render = await self.click(f"buy_{GPT}")               # opening the page directly: still no purchase
                self.assertIn(notice, plain(render.await_args.args[1]))
                self.assertEqual(datas(render.await_args.kwargs["reply_markup"]), [f"gd:{GPT}", "shop"])  # guide + back, no purchase
                query, render = await self.click(f"sq:{GPT}:1")          # forged/old quantity button: blocked
                render.assert_not_awaited()
                self.assertTrue(query.answer.await_args.kwargs.get("show_alert"))
                query, render = await self.click(f"cv:{GPT}:1")          # forged order button: no order created
                conn = shop_bot.get_db_connection()
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders WHERE product_id=?", (GPT,)).fetchone()[0], 0)
                conn.close()

    async def test_chatgpt_go_not_yet_on_the_web_is_listed_as_out_of_stock_without_fake_stock(self):
        shop_bot.set_user_lang(555, "en")
        with web(item(PLUS, price=14999)), self.stock(**{PLUS: 5}):
            shop_bot.refresh_catalogue(force=True)
            markup = shop_bot.shop_keyboard(555)
            self.assertNotIn("100,000", [txt for txt in texts(markup) if "CHATGPT" in txt][0])
            self.assertIn("OUT OF STOCK", [txt for txt in texts(markup) if "CHATGPT" in txt][0])
            _, render = await self.click(f"buy_{GPT}")
            body = plain(render.await_args.args[1])
            self.assertIn("100,000 VNĐ (≈ 4.00 USD)", body)
            self.assertIn("OUT OF STOCK", body)
            self.assertIsNone(shop_bot.parse_sku_qty_callback(f"cv:{GPT}:1"))   # cannot be ordered until the web sells it
        conn = shop_bot.get_db_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM stock WHERE product_id=?", (GPT,)).fetchone()[0], 0)
        conn.close()

    async def test_chatgpt_go_in_stock_can_be_bought(self):
        shop_bot.set_user_lang(555, "vi")
        with web(item(GPT, price=100000, max_qty=1)), self.stock(**{GPT: 3}):
            shop_bot.refresh_catalogue(force=True)
            _, render = await self.click(f"buy_{GPT}")
            self.assertEqual(datas(render.await_args.kwargs["reply_markup"])[0], f"sq:{GPT}:1")
            self.assertIn("Mua Ngay", texts(render.await_args.kwargs["reply_markup"])[0])

    # ---------- stock notification ----------
    async def test_stock_notification_is_sent_per_language_group_without_technical_codes(self):
        bot = SimpleNamespace(send_message=AsyncMock())
        recipients = [(11, "vi"), (22, "en"), (33, "vi"), (44, "en")]
        with web(item(PLUS, price=14999), item(PRO, price=125000), item(GPT, price=100000)), self.stock(**{GPT: 6, PRO: 1, PLUS: 9}), \
             patch.object(shop_bot, "get_stock_recipients", return_value=recipients):
            shop_bot.refresh_catalogue(force=True)
            for sku in (PLUS, PRO, GPT):
                bot.send_message.reset_mock()
                self.assertEqual(await shop_bot.send_stock_notifications(bot, sku, 3), 4)
                by_chat = {call.kwargs["chat_id"]: call.kwargs for call in bot.send_message.await_args_list}
                self.assertEqual(set(by_chat), {11, 22, 33, 44})
                self.assertEqual(by_chat[11]["text"], by_chat[33]["text"])      # one text per language group
                self.assertEqual(by_chat[22]["text"], by_chat[44]["text"])
                self.assertIn("HÀNG MỚI VỀ KHO", by_chat[11]["text"])
                self.assertIn("NEW STOCK ARRIVED", by_chat[22]["text"])
                for chat_id, lang in ((11, "vi"), (22, "en")):
                    text = by_chat[chat_id]["text"]
                    body = plain(text)
                    self.assertIn(shop_bot.sku_title(sku, lang), body)
                    self.assertIn("+3", body)
                    self.assertTrue(re.search(r"VNĐ", body))
                    for code in (PLUS, PRO, GPT, "chatgpt_go", "gemini_pro", "_account"):
                        self.assertNotIn(code, text)
                    markup = by_chat[chat_id]["reply_markup"]
                    self.assertEqual(datas(markup), [shop_bot.cb_buy(sku)])    # "Mua ngay" -> product detail page
                    self.assertEqual(texts(markup), ["Mua ngay" if lang == "vi" else "Buy now"])
                self.assertEqual(by_chat[11]["parse_mode"], "HTML")

    async def test_stock_notification_uses_web_name_for_new_products_and_never_prints_links(self):
        text = shop_bot.format_stock_added_notification(2, 5, ["https://secret.example/abc"], sku=PRO, lang="en")
        self.assertNotIn("secret.example", text)
        with web(item(NEW, name="Premium VPN 1 Year", price=99000)):
            shop_bot.refresh_catalogue(force=True)
            with self.stock(**{NEW: 4}):
                text = shop_bot.format_stock_added_notification(2, 4, sku=NEW, lang="vi")
        self.assertIn("Premium VPN 1 Year", text)
        self.assertNotIn(NEW, text)

    async def test_watch_task_merges_additions_into_one_message_per_product(self):
        sent = []

        async def fake_send(bot, sku, added):
            sent.append((sku, added))
            return 1

        now = [0.0]
        notifier = stock_notify.StockNotifier(clock=lambda: now[0])
        counts = {PLUS: 10}
        shop_bot._last_stock_seen.clear()
        steps = [None, 12, 15, 15, 15, 15]   # baseline, +2, +3, quiet..., 100 s per tick

        class StopLoop(asyncio.CancelledError):  # BaseException: the task re-raises it instead of swallowing
            pass

        calls = {"n": 0}

        async def fake_sleep(_):
            if calls["n"] >= len(steps):
                raise StopLoop
            value = steps[calls["n"]]
            if value is not None:
                counts[PLUS] = value
            calls["n"] += 1
            now[0] += 100

        with patch.object(shop_bot, "STOCK_NOTIFIER", notifier), patch.object(shop_bot, "send_stock_notifications", fake_send), \
             patch.object(shop_bot, "known_skus", return_value=[PLUS]), \
             patch.object(shop_bot, "get_stock_count", side_effect=lambda sku="gemini_plus": counts[sku]), \
             patch.object(shop_bot.asyncio, "sleep", fake_sleep):
            with self.assertRaises(StopLoop):
                await shop_bot.stock_watch_task(SimpleNamespace(bot=object()))
        # +2 goes out at once; the +3 is held back until 5 minutes later (one message per product per 5 minutes)
        self.assertEqual(sent, [(PLUS, 2), (PLUS, 3)])

    def test_recipients_are_admin_and_channel_by_default_customers_only_when_enabled(self):
        shop_bot.set_setting("notify_channel", "@shop_channel")
        shop_bot.set_user_lang(31, "vi")
        recipients = dict(shop_bot.get_stock_recipients())
        self.assertIn("@shop_channel", recipients)
        self.assertNotIn(31, recipients)
        shop_bot.set_setting("stock_notify_customers", "true")
        shop_bot.set_user_lang(32, "en")
        recipients = dict(shop_bot.get_stock_recipients())
        self.assertEqual(recipients[31], "vi")
        self.assertEqual(recipients[32], "en")

    def test_old_admin_stock_notification_still_brief_and_uses_vip_emoji(self):
        text = shop_bot.format_stock_added_notification(3, 294)
        self.assertIn("+3", text)
        self.assertIn("294", text)
        self.assertIn('emoji-id="5924720918826848520"', text)


if __name__ == "__main__":
    unittest.main()
