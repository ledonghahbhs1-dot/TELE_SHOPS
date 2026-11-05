import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from handlers.group_handlers import GroupAccessMiddleware, on_bot_membership


class GroupAccessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.bot = SimpleNamespace(leave_chat=AsyncMock(), send_message=AsyncMock())
        self.handler = AsyncMock(return_value="handled")
        self.guard = GroupAccessMiddleware()
        settings = patch.multiple(
            "handlers.group_handlers.config", OWNER_IDS=[123], ALLOWED_GROUP_IDS=[-1001]
        )
        settings.start()
        self.addCleanup(settings.stop)

    async def dispatch(self, event):
        return await self.guard(self.handler, event, {"bot": self.bot})

    async def test_private_customer_and_allowed_group_pass(self):
        for chat_id, chat_type in ((456, "private"), (-1001, "supergroup")):
            result = await self.dispatch(SimpleNamespace(chat=SimpleNamespace(id=chat_id, type=chat_type)))
            self.assertEqual(result, "handled")
        self.bot.leave_chat.assert_not_awaited()

    async def test_unknown_group_blocked_and_owner_notified(self):
        await self.dispatch(SimpleNamespace(chat=SimpleNamespace(id=-2002, type="group")))
        self.handler.assert_not_awaited()
        self.bot.leave_chat.assert_awaited_once_with(-2002)
        self.assertIn("-2002", self.bot.send_message.await_args.args[1])

    async def test_unknown_callback_and_channel_blocked(self):
        for event in (
            SimpleNamespace(message=SimpleNamespace(chat=SimpleNamespace(id=-2002, type="supergroup"))),
            SimpleNamespace(chat=SimpleNamespace(id=-3003, type="channel")),
        ):
            await self.dispatch(event)
        self.handler.assert_not_awaited()
        self.assertEqual(self.bot.leave_chat.await_count, 2)

    async def test_membership_join_and_admin_promotion_rejected(self):
        for status in ("member", "administrator"):
            await on_bot_membership(SimpleNamespace(
                chat=SimpleNamespace(id=-2002, type="supergroup"),
                new_chat_member=SimpleNamespace(status=status),
            ), self.bot)
        self.assertEqual(self.bot.leave_chat.await_count, 2)

    async def test_allowed_membership_private_block_and_leave_ignored(self):
        for chat_id, chat_type, status in (
            (-1001, "supergroup", "administrator"),
            (123, "private", "kicked"),
            (-2002, "supergroup", "left"),
            (-2002, "supergroup", "kicked"),
        ):
            await on_bot_membership(SimpleNamespace(
                chat=SimpleNamespace(id=chat_id, type=chat_type),
                new_chat_member=SimpleNamespace(status=status),
            ), self.bot)
        self.bot.leave_chat.assert_not_awaited()

    async def test_empty_allowlist_denies_all_groups(self):
        with patch("handlers.group_handlers.config.ALLOWED_GROUP_IDS", []):
            await self.dispatch(SimpleNamespace(chat=SimpleNamespace(id=-1001, type="supergroup")))
        self.handler.assert_not_awaited()
        self.bot.leave_chat.assert_awaited_once_with(-1001)

    async def test_owner_notification_failure_still_leaves(self):
        from aiogram.exceptions import TelegramForbiddenError
        from aiogram.methods import SendMessage
        self.bot.send_message.side_effect = TelegramForbiddenError(
            method=SendMessage(chat_id=123, text="test"), message="blocked"
        )
        await self.dispatch(SimpleNamespace(chat=SimpleNamespace(id=-2002, type="group")))
        self.bot.leave_chat.assert_awaited_once_with(-2002)
        self.handler.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
