"""Task 096: admin auth by numeric ID, admin callbacks gated, order ownership, HTML escaping, cooldown, no default admin."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from telegram.ext import ApplicationHandlerStop

import shop_bot
from rate_limit import Cooldown

ADMIN = 900001
OTHER = 111
VICTIM = 222


def make_query(data):
    message = SimpleNamespace(chat_id=1, message_id=2, photo=None, reply_photo=AsyncMock(),
                              reply_text=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(data=data, answer=AsyncMock(), message=message)


def cb_update(query, user_id, username="u"):
    return SimpleNamespace(callback_query=query, message=None,
                           effective_user=SimpleNamespace(id=user_id, username=username, first_name="K"))


class Base(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        for p in (patch.multiple(shop_bot, DATA_DIR=self.directory.name,
                                 DB_PATH=str(Path(self.directory.name) / "shop_database.db"),
                                 CLAIMED_LINKS_FILE=str(Path(self.directory.name) / "claimed.txt"),
                                 ADMIN_ID=str(ADMIN)),
                  patch.dict(os.environ, {"ADMIN_IDS": "", "USDT_WALLET": ""}),
                  patch.object(shop_bot, "sync_web_product_price", return_value=True),
                  patch.object(shop_bot, "refresh_catalogue")):
            p.start()
            self.addCleanup(p.stop)
        shop_bot.init_database()

    def add_order(self, order_id, user_id, status="pending"):
        conn = shop_bot.get_db_connection()
        conn.execute("INSERT INTO orders (order_id, user_id, user_name, product_id, amount, status) VALUES (?,?,?,?,?,?)",
                     (order_id, user_id, "n", "gemini_plus", 1000, status))
        conn.commit()
        conn.close()

    def status(self, order_id):
        conn = shop_bot.get_db_connection()
        row = conn.execute("SELECT status FROM orders WHERE order_id=?", (order_id,)).fetchone()
        conn.close()
        return row["status"]

    async def press(self, data, user_id, username="u"):
        query = make_query(data)
        with patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock) as render:
            await shop_bot.callback_handler(cb_update(query, user_id, username), SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock())))
        return query, render


class AdminCallbackTests(Base):  # item 1
    async def test_non_admin_is_refused_for_every_admin_callback_and_nothing_changes(self):
        self.add_order("WM1", VICTIM)
        shop_bot.set_setting("test_mode", "false")
        for data in ("admin_panel", "admin_toggle_test_mode", "admin_view_stock", "admin_approve_WM1", "admin_reject_WM1"):
            query, render = await self.press(data, OTHER)
            query.answer.assert_awaited_once_with(shop_bot.ADMIN_DENIED_TEXT, show_alert=True)
            render.assert_not_awaited()
            query.message.reply_text.assert_not_awaited()
        self.assertEqual(self.status("WM1"), "pending")
        self.assertEqual(shop_bot.get_setting("test_mode", "false"), "false")

    async def test_refusal_text_is_generic(self):
        query, _ = await self.press("admin_approve_NOPE", OTHER)
        text = query.answer.await_args.args[0]
        self.assertNotIn("NOPE", text)
        self.assertNotIn(str(ADMIN), text)

    async def test_admin_can_reject_a_pending_order(self):
        self.add_order("WM2", VICTIM)
        query, _ = await self.press("admin_reject_WM2", ADMIN)
        self.assertEqual(self.status("WM2"), "cancelled")
        self.assertNotEqual(query.answer.await_args_list[0].args, (shop_bot.ADMIN_DENIED_TEXT,))


class AdminIdentityTests(Base):  # items 2 and 6
    def test_username_never_grants_admin(self):
        for name in ("wolfmodyt", "wolfmod", "WolfMod"):
            self.assertFalse(shop_bot.is_admin(SimpleNamespace(id=5, username=name)))
        self.assertFalse(shop_bot.is_admin("wolfmodyt"))
        self.assertFalse(shop_bot.is_admin("@wolfmod"))
        self.assertFalse(hasattr(shop_bot, "ADMIN_USERNAMES"))

    def test_numeric_id_grants_admin(self):
        self.assertTrue(shop_bot.is_admin(SimpleNamespace(id=ADMIN)))
        self.assertTrue(shop_bot.is_admin(ADMIN))
        self.assertTrue(shop_bot.is_admin(str(ADMIN)))
        self.assertFalse(shop_bot.is_admin(OTHER))
        self.assertFalse(shop_bot.is_admin(None))
        self.assertFalse(shop_bot.is_admin(True))

    def test_admin_ids_list_is_supported(self):
        with patch.dict(os.environ, {"ADMIN_IDS": "7, 8;9,x,-3"}):
            self.assertEqual(shop_bot.admin_ids(), (ADMIN, 7, 8, 9))
            self.assertTrue(shop_bot.is_admin(8))
            self.assertFalse(shop_bot.is_admin(3))

    def test_no_hardcoded_default_admin(self):
        src = (Path(shop_bot.__file__)).read_text(encoding="utf-8")
        self.assertNotIn("6571486853", src)
        with patch.object(shop_bot, "ADMIN_ID", ""), patch.dict(os.environ, {"ADMIN_IDS": ""}):
            self.assertEqual(shop_bot.admin_ids(), ())
            self.assertIsNone(shop_bot.primary_admin_id())
            self.assertFalse(shop_bot.is_admin(6571486853))
            self.assertFalse(shop_bot.is_admin(0))
            self.assertEqual(shop_bot.get_stock_recipients()[:0], [])

    def test_missing_config_only_warns(self):
        with patch.object(shop_bot, "ADMIN_ID", ""), patch.dict(os.environ, {"ADMIN_IDS": ""}), \
             self.assertLogs(shop_bot.logger, level="WARNING") as logs:
            shop_bot.warn_if_admin_unconfigured()  # must not raise
        self.assertTrue(any("ADMIN_ID" in m for m in logs.output))

    def test_configured_admin_does_not_warn(self):
        with patch.object(shop_bot.logger, "warning") as warn:
            shop_bot.warn_if_admin_unconfigured()
        warn.assert_not_called()

    async def test_admin_callback_refused_when_unconfigured(self):
        with patch.object(shop_bot, "ADMIN_ID", ""):
            query, render = await self.press("admin_panel", 6571486853)
        query.answer.assert_awaited_once_with(shop_bot.ADMIN_DENIED_TEXT, show_alert=True)


class CancelOwnershipTests(Base):  # item 3
    async def test_cannot_cancel_someone_elses_order(self):
        self.add_order("WM3", VICTIM)
        query, _ = await self.press("cancel_order_WM3", OTHER)
        self.assertEqual(self.status("WM3"), "pending")
        self.assertTrue(query.answer.await_args.kwargs.get("show_alert"))

    async def test_unknown_order_gets_same_answer_as_foreign_order(self):
        self.add_order("WM3", VICTIM)
        q1, _ = await self.press("cancel_order_WM3", OTHER)
        shop_bot.CALLBACK_COOLDOWN._last.clear()
        q2, _ = await self.press("cancel_order_NOPE", OTHER)
        self.assertEqual(q1.answer.await_args, q2.answer.await_args)

    async def test_owner_can_cancel_own_pending_order(self):
        self.add_order("WM4", OTHER)
        await self.press("cancel_order_WM4", OTHER)
        self.assertEqual(self.status("WM4"), "cancelled")

    async def test_owner_cannot_reopen_completed_order(self):
        self.add_order("WM5", OTHER, status="completed")
        await self.press("cancel_order_WM5", OTHER)
        self.assertEqual(self.status("WM5"), "completed")

    async def test_admin_can_cancel_any_order(self):
        self.add_order("WM6", VICTIM)
        await self.press("cancel_order_WM6", ADMIN)
        self.assertEqual(self.status("WM6"), "cancelled")


class HtmlEscapeTests(Base):  # item 4
    EVIL = '<b>x</b><a href="javascript:1">&'

    async def test_setchannel_echo_is_escaped(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(effective_user=SimpleNamespace(id=ADMIN, username="a"), message=message)
        await shop_bot.admin_set_channel(update, SimpleNamespace(args=[self.EVIL]))
        sent = message.reply_text.await_args.args[0]
        self.assertNotIn("<b>x</b>", sent)
        self.assertIn("&lt;b&gt;x&lt;/b&gt;", sent)
        # and the stored value shown back by /setchannel (no args) is escaped too
        message2 = SimpleNamespace(reply_text=AsyncMock())
        await shop_bot.admin_set_channel(SimpleNamespace(effective_user=update.effective_user, message=message2),
                                         SimpleNamespace(args=[]))
        self.assertNotIn("<b>x</b>", message2.reply_text.await_args.args[0])

    async def test_document_name_and_error_are_escaped(self):
        message = SimpleNamespace(reply_text=AsyncMock(), document=SimpleNamespace(file_name="<i>a</i>.txt", file_id="f"))
        update = SimpleNamespace(effective_user=SimpleNamespace(id=ADMIN, username="a"), message=message)
        file = SimpleNamespace(download_as_bytearray=AsyncMock(return_value=bytearray(b"https://e.com/1")))
        bot = SimpleNamespace(get_file=AsyncMock(return_value=file))
        await shop_bot.admin_handle_document(update, SimpleNamespace(bot=bot))
        sent = message.reply_text.await_args.args[0]
        self.assertNotIn("<i>a</i>", sent)
        self.assertIn("&lt;i&gt;a&lt;/i&gt;.txt", sent)
        bot.get_file = AsyncMock(side_effect=RuntimeError("<u>boom</u>"))
        await shop_bot.admin_handle_document(update, SimpleNamespace(bot=bot))
        err = message.reply_text.await_args.args[0]
        self.assertNotIn("<u>boom</u>", err)
        self.assertIn("&lt;u&gt;boom&lt;/u&gt;", err)

    async def test_document_without_name_is_ignored_not_a_crash(self):
        message = SimpleNamespace(reply_text=AsyncMock(), document=SimpleNamespace(file_name=None, file_id="f"))
        update = SimpleNamespace(effective_user=SimpleNamespace(id=ADMIN, username="a"), message=message)
        await shop_bot.admin_handle_document(update, SimpleNamespace(bot=SimpleNamespace()))
        message.reply_text.assert_not_awaited()

    async def test_set_bank_echo_is_escaped(self):
        message = SimpleNamespace(reply_text=AsyncMock(), chat=SimpleNamespace(type="private"))
        update = SimpleNamespace(effective_user=SimpleNamespace(id=ADMIN, username="a"), message=message,
                                 effective_chat=message.chat)
        await shop_bot.admin_set_bank(update, SimpleNamespace(args=["<b>BANK", "123", "<i>OWNER"]))
        for call in message.reply_text.await_args_list:
            self.assertNotIn("<b>BANK", call.args[0])
            self.assertNotIn("<i>OWNER", call.args[0].upper().replace("&LT;", ""))


class CooldownTests(unittest.IsolatedAsyncioTestCase):  # item 5
    def test_cooldown_blocks_within_interval_and_allows_after(self):
        now = [100.0]
        cd = Cooldown(1.0, clock=lambda: now[0])
        self.assertTrue(cd.allow(1))
        now[0] = 100.5
        self.assertFalse(cd.allow(1))
        self.assertTrue(cd.allow(2))
        now[0] = 101.0  # measured from the last *accepted* action
        self.assertTrue(cd.allow(1))

    def test_cooldown_is_bounded(self):
        cd = Cooldown(1.0, max_users=3, clock=lambda: 0.0)
        for uid in range(50):
            cd.allow(uid)
        self.assertEqual(len(cd), 3)

    def test_invalid_config_rejected(self):
        with self.assertRaises(ValueError):
            Cooldown(0)
        with self.assertRaises(ValueError):
            Cooldown(1, max_users=0)

    async def test_gate_drops_fast_repeat_for_user_but_not_admin(self):
        with patch.object(shop_bot, "ADMIN_ID", str(ADMIN)), patch.dict(os.environ, {"ADMIN_IDS": ""}), \
             patch.object(shop_bot, "CALLBACK_COOLDOWN", Cooldown(1.0, clock=lambda: 5.0)):
            def upd(uid):
                return cb_update(make_query("shop"), uid)
            await shop_bot.rate_limit_gate(upd(OTHER), None)  # first passes
            second = upd(OTHER)
            with self.assertRaises(ApplicationHandlerStop):
                await shop_bot.rate_limit_gate(second, None)
            second.callback_query.answer.assert_awaited()  # spinner cleared
            await shop_bot.rate_limit_gate(upd(VICTIM), None)  # other user unaffected
            for _ in range(5):  # admin exempt
                await shop_bot.rate_limit_gate(upd(ADMIN), None)

    async def test_gate_ignores_updates_without_user(self):
        await shop_bot.rate_limit_gate(SimpleNamespace(effective_user=None, callback_query=None, message=None), None)

    async def test_commands_use_a_separate_bucket(self):
        with patch.object(shop_bot, "ADMIN_ID", str(ADMIN)), \
             patch.object(shop_bot, "CALLBACK_COOLDOWN", Cooldown(1.0, clock=lambda: 5.0)), \
             patch.object(shop_bot, "COMMAND_COOLDOWN", Cooldown(1.0, clock=lambda: 5.0)):
            await shop_bot.rate_limit_gate(cb_update(make_query("shop"), OTHER), None)
            text_update = SimpleNamespace(callback_query=None, message=SimpleNamespace(),
                                          effective_user=SimpleNamespace(id=OTHER))
            await shop_bot.rate_limit_gate(text_update, None)  # not blocked by the button press
            with self.assertRaises(ApplicationHandlerStop):
                await shop_bot.rate_limit_gate(text_update, None)


if __name__ == "__main__":
    unittest.main()
