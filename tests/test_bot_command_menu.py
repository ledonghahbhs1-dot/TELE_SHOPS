import unittest
from unittest.mock import AsyncMock, MagicMock

import shop_bot


class BotCommandMenuTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_menu_starts_with_start_and_has_no_admin_commands(self):
        bot = MagicMock()
        bot.set_my_commands = AsyncMock()
        bot.set_chat_menu_button = AsyncMock()
        self.assertTrue(await shop_bot.setup_bot_commands(bot))
        calls = bot.set_my_commands.await_args_list
        self.assertEqual(len(calls), 3)
        self.assertNotIn("language_code", calls[0].kwargs)  # mặc định gọi đầu tiên
        self.assertEqual({c.kwargs.get("language_code") for c in calls}, {None, "en", "vi"})
        admin = {"setprice", "setbank", "setusdt", "testmode", "addstock", "setchannel",
                 "wallet", "wallet_add", "wallet_sub", "backup"}
        for call in calls:
            names = [c.command for c in call.args[0]]
            self.assertEqual(names[0], "start")
            self.assertFalse(admin & set(names), names)
        bot.set_chat_menu_button.assert_awaited_once()

    async def test_network_error_does_not_crash(self):
        bot = MagicMock()
        bot.set_my_commands = AsyncMock(side_effect=RuntimeError("network down"))
        bot.set_chat_menu_button = AsyncMock(side_effect=RuntimeError("network down"))
        with self.assertLogs(shop_bot.logger, level="WARNING"):
            self.assertFalse(await shop_bot.setup_bot_commands(bot))


if __name__ == "__main__":
    unittest.main()
