"""Task 073: gemini_pro_3m offer-link SKU - content vi/en, Google icon, sold-out/buy buttons, one link per order. Offline."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import i18n
import product_content
import shop_bot

SKU = "gemini_pro_3m"
GOOGLE_ID = "5796314805564346672"
CHATGPT_ID = "5796185041717433060"


class ContentTests(unittest.TestCase):
    def test_sku_is_listed_with_full_vi_and_en_content(self):
        self.assertIn(SKU, product_content.listed_skus())
        for lang in ("vi", "en"):
            self.assertTrue(product_content.name(SKU, lang))
            self.assertTrue(product_content.short_name(SKU, lang))
            self.assertTrue(product_content.features(SKU, lang))
            self.assertTrue(product_content.field(SKU, "delivery", lang))
            self.assertTrue(product_content.field(SKU, "offer_step2", lang))
            self.assertTrue(product_content.field(SKU, "offer_single_use", lang))
        self.assertEqual(product_content.field(SKU, "delivery_kind", "vi"), "offer_link")
        self.assertEqual(product_content.price_fallback(SKU), 53000)
        self.assertEqual(product_content.name(SKU, "vi"), "Gemini Pro - 3 Tháng")


class ShopTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = patch.multiple(shop_bot, DATA_DIR=self.directory.name, DB_PATH=str(Path(self.directory.name) / "shop.db"),
                                  CLAIMED_LINKS_FILE=str(Path(self.directory.name) / "c.txt"), BOT_TOKEN="test-token")
        settings.start()
        self.addCleanup(settings.stop)
        shop_bot.init_database()

    def keyboard(self, stock, lang="vi"):
        with patch.object(shop_bot, "get_stock_count", side_effect=lambda sku="gemini_plus": stock.get(sku, 0)), \
                patch.object(shop_bot, "sku_price_vnd", return_value=53_000), \
                patch.object(shop_bot, "sku_is_in_stock", side_effect=lambda sku: stock.get(sku, 0) > 0):
            return shop_bot.shop_keyboard(1, lang)

    def buttons(self, keyboard):
        return {b.callback_data: b for row in keyboard.inline_keyboard for b in row}

    def test_sku_in_shop_keyboard_with_google_icon_not_chatgpt(self):
        self.assertIn(SKU, shop_bot.GOOGLE_SKUS)
        self.assertEqual(shop_bot.product_icon_id(SKU), GOOGLE_ID)
        self.assertEqual(shop_bot.product_icon_id("chatgpt_go_3m"), CHATGPT_ID)
        buttons = self.buttons(self.keyboard({SKU: 5}))
        self.assertIn(f"buy_{SKU}", buttons)
        self.assertEqual(buttons[f"buy_{SKU}"].api_kwargs.get("icon_custom_emoji_id"), GOOGLE_ID)

    def test_sold_out_shows_soldout_button_and_in_stock_shows_buy_without_count(self):
        out = self.buttons(self.keyboard({}))
        self.assertIn(f"soldout_{SKU}", out)
        self.assertNotIn(f"buy_{SKU}", out)
        self.assertEqual(out[f"soldout_{SKU}"].api_kwargs.get("icon_custom_emoji_id"), GOOGLE_ID)
        for lang in ("vi", "en"):
            for stock in ({}, {SKU: 7}):
                text = self.buttons(self.keyboard(stock, lang)).get(f"buy_{SKU}", None) or \
                    self.buttons(self.keyboard(stock, lang))[f"soldout_{SKU}"]
                self.assertNotIn("7", text.text)  # stock count never shown
        self.assertEqual(self.buttons(self.keyboard({SKU: 7}))[f"buy_{SKU}"].text, "GEMINI PRO 3 THÁNG")

    def test_price_shows_vnd_for_vi_and_vnd_plus_usd_for_en(self):
        shop_bot.set_setting(shop_bot.usdt_setting_key(SKU), "2.00")
        self.assertEqual(shop_bot.money_for_sku("vi", SKU, 53000), "53,000 VNĐ")
        self.assertEqual(shop_bot.money_for_sku("en", SKU, 53000), "53,000 VND (~$2.00)")

    def test_delivery_message_is_google_not_chatgpt_and_single_link(self):
        for lang in ("vi", "en"):
            msg, buttons = shop_bot.format_delivered_links_message("TGS1", ["https://example.com/offer/1"], SKU, lang)
            self.assertIn("https://example.com/offer/1", msg)
            self.assertIn("Google", msg)
            self.assertNotIn("ChatGPT", msg)
            self.assertNotIn("1 đổi 1", msg)
            self.assertNotIn("1-for-1", msg)
            self.assertEqual(buttons[0][0].url, "https://example.com/offer/1")

    def test_chatgpt_delivery_text_is_unchanged(self):
        msg, _ = shop_bot.format_delivered_links_message("TGS2", ["https://example.com/o"], "chatgpt_go_3m", "en")
        self.assertIn(i18n.t("en", "offer_step2"), msg)
        self.assertIn("1-for-1", msg)


if __name__ == "__main__":
    unittest.main()
