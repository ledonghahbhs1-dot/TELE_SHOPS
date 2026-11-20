import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import i18n
import shop_bot

SKU = "gemini_plus"
GPT = "chatgpt_go_3m"


def plain(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text)


class EnglishUsdPriceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = patch.multiple(shop_bot, DATA_DIR=self.directory.name, DB_PATH=str(Path(self.directory.name) / "shop.db"),
                                  CLAIMED_LINKS_FILE=str(Path(self.directory.name) / "c.txt"), BOT_TOKEN="test-token")
        settings.start()
        self.addCleanup(settings.stop)
        shop_bot.init_database()

    # ---------- helpers ----------
    def test_fmt_money_formats(self):
        self.assertEqual(i18n.fmt_money("en", 125000, 5), "125,000 VND (~$5.00)")
        self.assertEqual(i18n.fmt_money("en", 125000), "125,000 VND")
        self.assertEqual(i18n.fmt_money("vi", 125000, 5), "125,000 VNĐ")
        self.assertEqual(i18n.fmt_money("en", 1500000, 60), "1,500,000 VND (~$60.00)")

    def test_money_for_sku_multiplies_price_usdt_by_qty(self):
        shop_bot.set_setting(shop_bot.usdt_setting_key(GPT), "4.00")
        self.assertEqual(shop_bot.money_for_sku("en", GPT, 100000), "100,000 VND (~$4.00)")
        self.assertEqual(shop_bot.money_for_sku("en", GPT, 300000, 3), "300,000 VND (~$12.00)")
        self.assertEqual(shop_bot.money_for_sku("vi", GPT, 300000, 3), "300,000 VNĐ")

    def test_en_without_price_usdt_shows_vnd_only_without_error(self):
        self.assertEqual(shop_bot.money_for_sku("en", GPT, 100000, 2), "100,000 VND")
        shop_bot.set_setting(shop_bot.usdt_setting_key(GPT), "garbage")
        self.assertEqual(shop_bot.money_for_sku("en", GPT, 100000), "100,000 VND")
        shop_bot.set_setting(shop_bot.usdt_setting_key(GPT), "0")
        self.assertEqual(shop_bot.money_for_sku("en", GPT, 100000), "100,000 VND")

    # ---------- confirmation screen ----------
    async def confirmation(self, lang, usdt=None, qty=2):
        shop_bot.set_user_lang(7, lang)
        if usdt is not None:
            shop_bot.set_setting(shop_bot.usdt_setting_key(SKU), usdt)
        target = MagicMock()
        with patch.object(shop_bot, "sync_web_product_price"), \
                patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock) as render:
            await shop_bot.show_order_confirmation(target, SimpleNamespace(id=7), sku=SKU, qty=qty)
        return plain(render.await_args.args[1])

    async def test_confirmation_en_shows_unit_and_total_usd(self):
        body = await self.confirmation("en", "5.00", qty=2)
        unit = int(shop_bot.get_product(SKU)["price"])
        self.assertIn(f"{unit:,} VND (~$5.00)", body)
        self.assertIn(f"{unit * 2:,} VND (~$10.00)", body)

    async def test_confirmation_vi_has_no_usd_dollar_sign(self):
        body = await self.confirmation("vi", "5.00")
        self.assertNotIn("$", body)
        self.assertNotIn("VND", body.replace("VNĐ", ""))

    async def test_confirmation_en_without_price_usdt_is_vnd_only(self):
        shop_bot.set_setting(shop_bot.usdt_setting_key(SKU), "")
        body = await self.confirmation("en", None)
        self.assertNotIn("~$", body)
        self.assertIn(" VND", body)

    # ---------- bills ----------
    def test_vietqr_bill_en_product_and_wallet_topup(self):
        shop_bot.set_setting(shop_bot.usdt_setting_key(SKU), "5.00")
        bill = plain(shop_bot.build_vietqr_bill("en", "TG1", "X", 2, 250000, "B", "1", "O", "10:00", sku=SKU))
        self.assertIn("250,000 VND (~$10.00)", bill)
        topup = plain(shop_bot.build_vietqr_bill("en", "TG2", "Top up", 1, 100000, "B", "1", "O", "10:00"))
        self.assertIn("100,000 VND (~$4.00)", topup)  # ví: tỉ giá hiển thị sẵn có của bot
        vi = plain(shop_bot.build_vietqr_bill("vi", "TG1", "X", 2, 250000, "B", "1", "O", "10:00", sku=SKU))
        self.assertIn("250,000 VNĐ", vi)
        self.assertNotIn("$", vi)

    def test_vietqr_bill_en_without_price_usdt_is_vnd_only(self):
        shop_bot.set_setting(shop_bot.usdt_setting_key(SKU), "")
        bill = plain(shop_bot.build_vietqr_bill("en", "TG1", "X", 1, 125000, "B", "1", "O", "10:00", sku=SKU))
        self.assertIn("125,000 VND", bill)
        self.assertNotIn("~$", bill)

    # ---------- product page / intro ----------
    def test_product_detail_en_adds_usd_from_price_usdt(self):
        shop_bot.set_setting(shop_bot.usdt_setting_key(GPT), "4.00")
        self.assertIn("100,000 VND (~$4.00)", plain(shop_bot.render_product_detail(GPT, "en", 100000)))

    def test_product_detail_vi_unchanged(self):
        shop_bot.set_setting(shop_bot.usdt_setting_key(GPT), "4.00")
        body = plain(shop_bot.render_product_detail(GPT, "vi", 100000))
        self.assertIn("100,000 VNĐ", body)
        self.assertNotIn("~$", body)

    def test_generic_intro_en_uses_price_text(self):
        import product_intros
        text = plain(product_intros.generic_intro(lambda *a: "", "N", "", 100000, "4.0", "en", price_text="100,000 VND (~$4.00)"))
        self.assertIn("100,000 VND (~$4.00)", text)

    # ---------- wallet ----------
    def test_wallet_money_en_uses_existing_display_rate_and_vi_unchanged(self):
        self.assertEqual(i18n.fmt_money_rate("en", 150000), "150,000 VND (~$6.00)")
        self.assertEqual(i18n.fmt_money_rate("vi", 150000), "150,000 VNĐ")


if __name__ == "__main__":
    unittest.main()
