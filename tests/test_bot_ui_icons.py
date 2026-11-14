"""Task 067: bot UI - one custom icon constant, no "MUA" and no price on product buttons, safe plain-emoji fallback. Offline."""
import asyncio
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

import i18n
import shop_bot

ICON_ID = "5796185041717433060"  # ChatGPT
GOOGLE_ID = "5796314805564346672"  # Google (task 069)


def utf16_len(text):
    return len(text.encode("utf-16-le")) // 2


class IconConstantTests(unittest.TestCase):
    def test_chatgpt_constant_and_per_product_map(self):
        self.assertEqual(shop_bot.CHATGPT_ICON_CUSTOM_EMOJI_ID, ICON_ID)
        self.assertEqual(shop_bot.BOT_PRODUCT_ICON, {"chatgpt_go_3m": ICON_ID})
        self.assertEqual(shop_bot.product_icon_id("chatgpt_go_3m"), ICON_ID)
        self.assertEqual(shop_bot.GOOGLE_ICON_CUSTOM_EMOJI_ID, GOOGLE_ID)
        self.assertEqual(shop_bot.product_icon_id("gemini_plus"), GOOGLE_ID)
        self.assertEqual(shop_bot.product_icon_id("gemini_pro_account"), GOOGLE_ID)
        self.assertNotIn(ICON_ID, shop_bot.VIP_EMOJIS.values())

    def test_google_icon_applies_to_google_skus_only(self):
        self.assertEqual(shop_bot.product_icon_id("chatgpt_go_3m"), ICON_ID)
        self.assertEqual(shop_bot.product_icon_id("other"), "")

    def test_empty_google_constant_falls_back_to_the_default(self):
        with patch.object(shop_bot, "GOOGLE_ICON_CUSTOM_EMOJI_ID", ""):
            self.assertEqual(shop_bot.product_icon_id("gemini_plus"), "")
            self.assertEqual(shop_bot.product_icon_id("gemini_pro_account"), "")

    def test_id_is_not_hardcoded_elsewhere_in_the_bot_sources(self):
        root = Path(shop_bot.__file__).parent
        hits = [p.name for p in root.glob("*.py") if ICON_ID in p.read_text(encoding="utf-8")]
        self.assertEqual(hits, ["shop_bot.py"])
        self.assertEqual(Path(root / "shop_bot.py").read_text(encoding="utf-8").count(f'"{ICON_ID}"'), 1)

    def test_shop_title_keeps_the_two_crowns_and_not_the_chatgpt_icon(self):
        text = shop_bot.shop_message("vi")
        self.assertNotIn(ICON_ID, text)
        crown = shop_bot.VIP_EMOJIS["crown"]
        self.assertEqual(text.count(f'<tg-emoji emoji-id="{crown}">'), 2)
        for body in re.findall(r"<tg-emoji emoji-id=\"%s\">(.*?)</tg-emoji>" % crown, text):
            self.assertEqual(body, "👑")

    def test_entity_offsets_would_be_utf16_correct(self):
        # Messages use <tg-emoji> HTML, so Telegram computes entity offsets. This checks the plain text left after the
        # tags are stripped: a custom_emoji entity for the first crown starts at offset 0 and is 2 UTF-16 units long.
        plain = shop_bot.plain_text(shop_bot.shop_message("vi"))
        self.assertTrue(plain.startswith("👑"))
        self.assertEqual(utf16_len("👑"), 2)
        offset_second = utf16_len(plain[: plain.rindex("👑")])
        self.assertEqual(plain.encode("utf-16-le")[offset_second * 2: offset_second * 2 + 4].decode("utf-16-le"), "👑")


class PlainFallbackTests(unittest.TestCase):
    def test_plain_text_drops_tags_and_keeps_glyphs(self):
        src = f'<tg-emoji emoji-id="{ICON_ID}">👑</tg-emoji> <b>Xin chào</b>'
        self.assertEqual(shop_bot.plain_text(src), "👑 <b>Xin chào</b>")

    def test_plain_markup_drops_icons_keeps_text_and_callbacks(self):
        markup = InlineKeyboardMarkup([[shop_bot.vip_btn("Sản phẩm", callback_data="buy_x", emoji_name="crown", style="primary", icon_id=ICON_ID)],
                                       [InlineKeyboardButton(text="Link", url="https://example.test")]])
        self.assertIn("icon_custom_emoji_id", (markup.inline_keyboard[0][0].api_kwargs or {}))
        plain = shop_bot.plain_markup(markup)
        self.assertEqual([[b.text for b in row] for row in plain.inline_keyboard], [["Sản phẩm"], ["Link"]])
        self.assertEqual(plain.inline_keyboard[0][0].callback_data, "buy_x")
        self.assertEqual(plain.inline_keyboard[1][0].url, "https://example.test")
        self.assertFalse(any(b.api_kwargs for row in plain.inline_keyboard for b in row))

    def test_plain_markup_passes_none_through(self):
        self.assertIsNone(shop_bot.plain_markup(None))


class SafeEditFallbackTests(unittest.IsolatedAsyncioTestCase):
    def query(self, reply_side_effect):
        query = MagicMock()
        query.message.photo = None
        query.edit_message_text = AsyncMock(side_effect=Exception("Bad Request: custom emoji"))
        query.message.reply_text = AsyncMock(side_effect=reply_side_effect)
        return query

    async def test_resends_plain_version_when_telegram_rejects_the_custom_emoji(self):
        text = f'<tg-emoji emoji-id="{ICON_ID}">👑</tg-emoji> Đơn hàng'
        markup = InlineKeyboardMarkup([[shop_bot.vip_btn("Đơn", callback_data="my_orders", emoji_name="crown", icon_id=ICON_ID)]])
        query = self.query([Exception("custom emoji refused"), "sent"])
        result = await shop_bot.safe_edit_or_reply(query, text, reply_markup=markup)
        self.assertEqual(result, "sent")
        calls = query.message.reply_text.await_args_list
        self.assertEqual(len(calls), 2)
        last = calls[-1].kwargs
        self.assertNotIn("tg-emoji", last["text"])
        self.assertEqual(last["text"], "👑 Đơn hàng")
        self.assertFalse(any(b.api_kwargs for row in last["reply_markup"].inline_keyboard for b in row))
        self.assertEqual(last["reply_markup"].inline_keyboard[0][0].callback_data, "my_orders")

    async def test_normal_path_keeps_the_custom_emoji(self):
        text = f'<tg-emoji emoji-id="{ICON_ID}">👑</tg-emoji> ok'
        query = MagicMock()
        query.message.photo = None
        query.edit_message_text = AsyncMock(return_value="edited")
        result = await shop_bot.safe_edit_or_reply(query, text)
        self.assertEqual(result, "edited")
        self.assertIn(ICON_ID, query.edit_message_text.await_args.kwargs["text"])


class ShopButtonTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = patch.multiple(shop_bot, DATA_DIR=self.directory.name, DB_PATH=str(Path(self.directory.name) / "shop.db"),
                                  CLAIMED_LINKS_FILE=str(Path(self.directory.name) / "c.txt"), BOT_TOKEN="test-token")
        settings.start()
        self.addCleanup(settings.stop)
        shop_bot.init_database()

    def keyboard(self, stock):
        with patch.object(shop_bot, "get_stock_count", side_effect=lambda sku="gemini_plus": stock.get(sku, 0)), \
                patch.object(shop_bot, "sku_price_vnd", return_value=125_000), \
                patch.object(shop_bot, "sku_is_in_stock", side_effect=lambda sku: stock.get(sku, 0) > 0):
            return shop_bot.shop_keyboard(1, "vi")

    def icon_by_callback(self, keyboard):
        return {b.callback_data: (b.api_kwargs or {}).get("icon_custom_emoji_id") for row in keyboard.inline_keyboard for b in row}

    def test_buttons_have_no_mua_and_no_price(self):
        keyboard = self.keyboard({"gemini_plus": 290, "gemini_pro_account": 3, "chatgpt_go_3m": 4})
        product_buttons = [b for row in keyboard.inline_keyboard for b in row if b.callback_data != "main_menu"]
        self.assertEqual(len(product_buttons), 3)
        for button in product_buttons:
            self.assertFalse(button.text.upper().startswith("MUA"), button.text)
            self.assertNotRegex(button.text, r"\d[\d.,]*\s*(VNĐ|VND|đ)", button.text)
        self.assertEqual(product_buttons[0].text, "GOOGLE AI PLUS 12 THÁNG")

    def test_google_buttons_have_the_google_icon_and_not_chatgpt_and_vice_versa(self):
        icons = self.icon_by_callback(self.keyboard({"gemini_plus": 290, "gemini_pro_account": 3, "chatgpt_go_3m": 4}))
        self.assertEqual(icons["buy_gemini_plus"], GOOGLE_ID)
        self.assertEqual(icons["buy_gemini_pro_account"], GOOGLE_ID)
        self.assertNotEqual(icons["buy_gemini_plus"], ICON_ID)
        self.assertNotEqual(icons["buy_gemini_pro_account"], ICON_ID)
        self.assertEqual(icons["buy_chatgpt_go_3m"], ICON_ID)
        self.assertNotEqual(icons["buy_chatgpt_go_3m"], GOOGLE_ID)

    def test_shop_title_still_two_crowns_without_google_or_chatgpt_icon(self):
        text = shop_bot.shop_message("vi")
        self.assertEqual(text.count(f'emoji-id="{shop_bot.VIP_EMOJIS["crown"]}"'), 2)
        self.assertNotIn(ICON_ID, text)
        self.assertNotIn(GOOGLE_ID, text)

    def test_empty_google_constant_keeps_crown_default_on_buttons(self):
        with patch.object(shop_bot, "GOOGLE_ICON_CUSTOM_EMOJI_ID", ""):
            icons = self.icon_by_callback(self.keyboard({"gemini_plus": 290, "gemini_pro_account": 3, "chatgpt_go_3m": 4}))
        self.assertEqual(icons["buy_gemini_plus"], shop_bot.VIP_EMOJIS["crown"])
        self.assertEqual(icons["buy_chatgpt_go_3m"], ICON_ID)

    def test_out_of_stock_keeps_cross_for_google_and_chatgpt_icon_for_chatgpt(self):
        with patch.object(shop_bot, "GOOGLE_ICON_CUSTOM_EMOJI_ID", ""):
            icons = self.icon_by_callback(self.keyboard({}))
        self.assertEqual(icons["soldout_chatgpt_go_3m"], ICON_ID)
        self.assertEqual(icons["soldout_gemini_pro_account"], shop_bot.VIP_EMOJIS["cross"])
        self.assertEqual(icons["buy_gemini_plus"], shop_bot.VIP_EMOJIS["cross"])  # plus keeps its own page when sold out

    def test_out_of_stock_google_buttons_use_the_google_icon(self):
        icons = self.icon_by_callback(self.keyboard({"chatgpt_go_3m": 1}))
        self.assertEqual(icons["soldout_gemini_pro_account"], GOOGLE_ID)
        self.assertEqual(icons["buy_gemini_plus"], GOOGLE_ID)

    def test_callback_data_is_unchanged(self):
        keyboard = self.keyboard({"gemini_plus": 5, "gemini_pro_account": 3, "chatgpt_go_3m": 4})
        self.assertEqual([b.callback_data for row in keyboard.inline_keyboard for b in row],
                         ["buy_gemini_plus", "buy_gemini_pro_account", "buy_chatgpt_go_3m", "main_menu"])

    def test_out_of_stock_shows_het_hang_without_price(self):
        keyboard = self.keyboard({})
        texts = [b.text for row in keyboard.inline_keyboard for b in row]
        self.assertTrue(any(t.endswith("• HẾT HÀNG") for t in texts), texts)
        self.assertFalse(any("VNĐ" in t or "VND" in t for t in texts))

    def test_gemini_plus_button_has_no_stock_number_even_when_in_stock(self):
        with patch.object(shop_bot, "get_stock_count", side_effect=AssertionError("shop_keyboard must not query stock counts")), (
                patch.object(shop_bot, "sku_price_vnd", return_value=125_000)), (
                patch.object(shop_bot, "sku_is_in_stock", return_value=True)):
            for lang in ("vi", "en"):
                keyboard = shop_bot.shop_keyboard(1, lang)
                button = next(b for row in keyboard.inline_keyboard for b in row if b.callback_data == "buy_gemini_plus")
                self.assertNotRegex(button.text, r"\(.*\d.*\)", button.text)
                for marker in ("Kho", "Stock", "{stock}"):
                    self.assertNotIn(marker, button.text)

    def test_i18n_button_strings_have_no_stock_placeholder(self):
        for key, value in i18n.STRINGS.items():
            if key.startswith("btn_"):
                for lang, text in value.items():
                    self.assertNotIn("{stock}", text, (key, lang))

    def test_english_labels_follow_the_same_rule(self):
        keyboard = self.keyboard({"gemini_plus": 5})
        first = keyboard.inline_keyboard[0][0]
        self.assertFalse(first.text.upper().startswith("BUY"))
        self.assertEqual(shop_bot.t("en", "btn_product_buy_nostock", name="X"), "X")
        self.assertEqual(shop_bot.t("en", "btn_product_oos", name="X"), "X • OUT OF STOCK")


if __name__ == "__main__":
    unittest.main()
