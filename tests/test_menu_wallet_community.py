import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import shop_bot

BAD_CHARS = ("dấ", "�", "Ä", "Ã", "?")


def layout(markup):
    return [[(b.text, b.callback_data, b.url) for b in row] for row in markup.inline_keyboard]


class MenuTests(unittest.TestCase):
    def menu(self, lang, **env):
        base = {"WALLET_ENABLED": "", "COMMUNITY_URL": ""}
        base.update(env)
        with patch.dict(os.environ, base):
            return shop_bot.main_menu_keyboard(1, lang)

    def test_default_menu_vi_en_has_no_wallet_no_community_and_old_callbacks(self):
        vi = layout(self.menu("vi"))
        en = layout(self.menu("en"))
        self.assertEqual(vi, [[("Mua hàng", "shop", None)],
                              [("Đơn đã mua", "my_orders", None), ("Hỗ trợ", "support", None)],
                              [("Ngôn ngữ: Tiếng Việt", "lang_menu", None)]])
        self.assertEqual(en, [[("Shop", "shop", None)],
                              [("My Orders", "my_orders", None), ("Support", "support", None)],
                              [("Language: English", "lang_menu", None)]])

    def test_full_menu_layout_when_wallet_and_community_enabled(self):
        env = {"WALLET_ENABLED": "1", "COMMUNITY_URL": "https://t.me/wolfmod_community"}
        vi = layout(self.menu("vi", **env))
        en = layout(self.menu("en", **env))
        self.assertEqual(vi, [[("Mua hàng", "shop", None), ("Ví của tôi", "wallet", None)],
                              [("Đơn đã mua", "my_orders", None), ("Hỗ trợ", "support", None)],
                              [("Cộng đồng", None, "https://t.me/wolfmod_community")],
                              [("Ngôn ngữ: Tiếng Việt", "lang_menu", None)]])
        self.assertEqual([[t for t, _, _ in row] for row in en],
                         [["Shop", "My Wallet"], ["My Orders", "Support"], ["Community"], ["Language: English"]])

    def test_community_hidden_unless_valid_telegram_https_url(self):
        for bad in ("", "   ", "http://t.me/x", "https://example.com/x", "https://t.me/", "t.me/x", "https://t.me/a b", "javascript:alert(1)"):
            texts = [t for row in layout(self.menu("vi", COMMUNITY_URL=bad)) for t, _, _ in row]
            self.assertNotIn("Cộng đồng", texts, bad)
        texts = [t for row in layout(self.menu("vi", COMMUNITY_URL="  https://t.me/+abcDEF123  ")) for t, _, _ in row]
        self.assertIn("Cộng đồng", texts)

    def test_wallet_hidden_unless_flag_is_exactly_1(self):
        for val in ("", "0", "true", "yes", "2"):
            texts = [t for row in layout(self.menu("vi", WALLET_ENABLED=val)) for t, _, _ in row]
            self.assertNotIn("Ví của tôi", texts, val)
        self.assertIn("Ví của tôi", [t for row in layout(self.menu("vi", WALLET_ENABLED="1")) for t, _, _ in row])

    def test_no_broken_characters_in_any_label(self):
        for lang in ("vi", "en"):
            for row in layout(self.menu(lang, WALLET_ENABLED="1", COMMUNITY_URL="https://t.me/x")):
                for text, _, _ in row:
                    for bad in BAD_CHARS:
                        self.assertNotIn(bad, text)
        self.assertIn("Đơn đã mua", [t for row in layout(self.menu("vi")) for t, _, _ in row])

    def test_wallet_button_has_no_url_and_callback_fits_limit(self):
        for row in layout(self.menu("vi", WALLET_ENABLED="1", COMMUNITY_URL="https://t.me/x")):
            for _, cb, url in row:
                self.assertTrue(bool(cb) != bool(url))
                if cb:
                    self.assertLessEqual(len(cb.encode()), 64)


class WalletHandlerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = patch.multiple(shop_bot, DATA_DIR=self.directory.name,
                                  DB_PATH=str(Path(self.directory.name) / "shop_database.db"),
                                  CLAIMED_LINKS_FILE=str(Path(self.directory.name) / "claimed_links.txt"))
        settings.start()
        self.addCleanup(settings.stop)
        with patch.dict(os.environ, {"USDT_WALLET": ""}):
            shop_bot.init_database()

    async def test_wallet_callback_shows_coming_soon_in_both_languages_and_no_balance(self):
        for lang, phrase in (("vi", "đang được hoàn thiện"), ("en", "being finished")):
            shop_bot.set_user_lang(777, lang)
            message = SimpleNamespace(chat_id=1, message_id=2, photo=None, reply_photo=AsyncMock(), reply_text=AsyncMock(), delete=AsyncMock())
            query = SimpleNamespace(data="wallet", answer=AsyncMock(), message=message)
            update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=777, first_name="A"),
                                     effective_chat=SimpleNamespace(id=1), message=None)
            with patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock) as render, \
                 patch.object(shop_bot, "refresh_catalogue"):
                await shop_bot.callback_handler(update, SimpleNamespace())
            text = render.await_args.args[1]
            self.assertIn(phrase, text)
            datas = [b.callback_data for row in render.await_args.kwargs["reply_markup"].inline_keyboard for b in row]
            self.assertEqual(datas, ["main_menu"])


if __name__ == "__main__":
    unittest.main()
