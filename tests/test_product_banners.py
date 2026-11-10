import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import shop_bot

BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "products")
EXPECTED = {
    ("chatgpt_go_3m", "vi"): "chatgpt_go_3m_vi.png", ("chatgpt_go_3m", "en"): "chatgpt_go_3m_en.png",
    ("gemini_pro_account", "vi"): "gemini_pro_account_vi.png", ("gemini_pro_account", "en"): "gemini_pro_account_en.jpg",
    ("gemini_plus", "vi"): "gemini_plus_vi.jpg", ("gemini_plus", "en"): "gemini_plus_en.png",
}


def base(path):
    return os.path.basename(path) if path else None


class BannerFilesTests(unittest.TestCase):
    def test_all_six_files_exist_and_are_real_images(self):
        for name in EXPECTED.values():
            path = os.path.join(BASE, name)
            self.assertTrue(os.path.isfile(path), name)
            with open(path, "rb") as f:
                head = f.read(8)
            self.assertTrue(head.startswith(b"\x89PNG") if name.endswith(".png") else head.startswith(b"\xff\xd8"), name)
            self.assertLess(os.path.getsize(path), 10 * 1024 * 1024, name)

    def test_picks_the_banner_for_the_chosen_language(self):
        for (sku, lang), name in EXPECTED.items():
            self.assertEqual(base(shop_bot.get_product_banner_path(sku, lang)), name, (sku, lang))

    def test_language_falls_back_to_the_other_language(self):
        with patch.object(shop_bot, "BANNER_DIR", BASE), patch.object(shop_bot.os.path, "isfile",
                side_effect=lambda p: not os.path.basename(p).startswith("chatgpt_go_3m_en") and os.path.exists(p)):
            self.assertEqual(base(shop_bot.get_product_banner_path("chatgpt_go_3m", "en")), "chatgpt_go_3m_vi.png")
            self.assertEqual(base(shop_bot.get_product_banner_path("chatgpt_go_3m", "vi")), "chatgpt_go_3m_vi.png")

    def test_sku_without_banner_keeps_text_only_behaviour(self):
        self.assertIsNone(shop_bot.get_product_banner_path("premium_vpn", "vi"))
        self.assertIsNone(shop_bot.get_product_banner_path("premium_vpn", "en"))


class BannerSendTests(unittest.IsolatedAsyncioTestCase):
    def query(self, file_id="FILEID1"):
        sent = SimpleNamespace(photo=[SimpleNamespace(file_id="small"), SimpleNamespace(file_id=file_id)])
        message = SimpleNamespace(delete=AsyncMock(), reply_photo=AsyncMock(return_value=sent), reply_text=AsyncMock())
        return SimpleNamespace(message=message)

    async def test_uploads_once_then_reuses_cached_file_id_and_language_changes_image(self):
        shop_bot._banner_file_ids.clear()
        q1 = self.query()
        await shop_bot.send_product_screen(q1, "chatgpt_go_3m", "<b>x</b>", [], "vi")
        first = q1.message.reply_photo.await_args.kwargs["photo"]
        self.assertEqual(first.name, "chatgpt_go_3m_vi.png")          # uploaded from file
        q2 = self.query()
        await shop_bot.send_product_screen(q2, "chatgpt_go_3m", "<b>x</b>", [], "vi")
        self.assertEqual(q2.message.reply_photo.await_args.kwargs["photo"], "FILEID1")   # cached, no re-upload
        q3 = self.query("FILEID2")
        await shop_bot.send_product_screen(q3, "chatgpt_go_3m", "<b>x</b>", [], "en")
        self.assertEqual(q3.message.reply_photo.await_args.kwargs["photo"].name, "chatgpt_go_3m_en.png")
        shop_bot._banner_file_ids.clear()

    async def test_stale_cached_file_id_falls_back_to_upload(self):
        shop_bot._banner_file_ids.clear()
        path = shop_bot.get_product_banner_path("gemini_plus", "vi")
        shop_bot._banner_file_ids[shop_bot._banner_key(path)] = "STALE"
        q = self.query("NEW")
        sent = q.message.reply_photo.return_value
        q.message.reply_photo = AsyncMock(side_effect=[Exception("wrong file id"), sent])
        await shop_bot.send_product_screen(q, "gemini_plus", "<b>x</b>", [], "vi")
        self.assertEqual(q.message.reply_photo.await_count, 2)
        self.assertEqual(shop_bot._banner_file_ids[shop_bot._banner_key(path)], "NEW")
        shop_bot._banner_file_ids.clear()

    async def test_sku_without_banner_sends_text_only(self):
        q = self.query()
        with patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock) as render:
            await shop_bot.send_product_screen(q, "premium_vpn", "<b>x</b>", [], "en")
        render.assert_awaited_once()
        q.message.reply_photo.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
