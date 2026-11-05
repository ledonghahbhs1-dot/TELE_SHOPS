import asyncio
import os
from datetime import datetime
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import shop_bot


class AgyShopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = patch.multiple(shop_bot,
            DATA_DIR=self.directory.name,
            DB_PATH=str(Path(self.directory.name) / "shop_database.db"),
            CLAIMED_LINKS_FILE=str(Path(self.directory.name) / "claimed_links.txt"))
        settings.start()
        self.addCleanup(settings.stop)
        with patch.dict(os.environ, {"USDT_WALLET": ""}):
            shop_bot.init_database()

    async def test_paid_delivery_retries_telegram_without_claiming_twice(self):
        conn = shop_bot.get_db_connection()
        conn.execute("INSERT INTO orders (order_id,user_id,product_id,amount,status,quantity) VALUES (?,?,?,?,?,?)", ("TGSTEST1234", 123, "gemini_plus", 20000, "pending", 1))
        conn.commit()
        conn.close()
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=[RuntimeError("offline"), None]))
        with patch.object(shop_bot, "claim_stock_from_web", return_value="https://example.com/activate?a=1&b=2") as claim:
            self.assertFalse(await shop_bot.send_paid_order_delivery(bot, "TGSTEST1234", 123))
            self.assertTrue(await shop_bot.send_paid_order_delivery(bot, "TGSTEST1234", 123))
            self.assertTrue(await shop_bot.send_paid_order_delivery(bot, "TGSTEST1234", 123))
        self.assertEqual(claim.call_count, 1)
        self.assertEqual(bot.send_message.await_count, 2)
        self.assertIn("&amp;", bot.send_message.await_args.kwargs["text"])
        self.assertIn("Hướng dẫn kích hoạt", bot.send_message.await_args.kwargs["text"])

    async def test_purchase_history_only_shows_completed_orders(self):
        conn = shop_bot.get_db_connection()
        for code, status, link in [("TGSPURCHASED", "completed", "https://example.com/link"), ("TGSCANCELLED", "cancelled", None), ("TGSPENDING", "pending", None)]:
            conn.execute("INSERT INTO orders (order_id,user_id,product_id,amount,status,item_delivered) VALUES (?,?,?,?,?,?)", (code, 123, "gemini_plus", 20000, status, link))
        conn.commit()
        conn.close()
        query = SimpleNamespace(data="my_orders", answer=AsyncMock())
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=123))
        with patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock) as render:
            await shop_bot.callback_handler(update, SimpleNamespace())
        text = render.await_args.args[1]
        self.assertIn("TGSPURCHASED", text)
        self.assertNotIn("TGSCANCELLED", text)
        self.assertNotIn("TGSPENDING", text)
        self.assertNotIn("Số tài khoản", text)
        self.assertIn('emoji-id="6005570495603282482"', text)

    async def test_worker_delivers_only_orders_confirmed_paid(self):
        conn = shop_bot.get_db_connection()
        for code in ("TGSPAY1234", "TGSWAIT1234"):
            conn.execute("INSERT INTO orders (order_id,user_id,product_id,amount,status) VALUES (?,?,?,?,?)", (code, 123, "gemini_plus", 20000, "pending"))
        conn.commit()
        conn.close()
        status = lambda code: {"status": "paid" if code == "TGSPAY1234" else "pending"}
        with patch.object(shop_bot, "check_web_order_status", side_effect=status), \
             patch.object(shop_bot, "send_paid_order_delivery", new_callable=AsyncMock) as deliver, \
             patch.object(shop_bot.asyncio, "sleep", new_callable=AsyncMock, side_effect=[None, None, asyncio.CancelledError]):
            with self.assertRaises(asyncio.CancelledError):
                await shop_bot.auto_deliver_paid_orders_task(SimpleNamespace(bot=object()))
        self.assertEqual(deliver.await_count, 1)
        self.assertEqual(deliver.await_args.args[1], "TGSPAY1234")

    async def test_original_confirmation_has_both_payments_and_quantity(self):
        target = SimpleNamespace(edit_message_text=AsyncMock())
        with patch.object(shop_bot, "get_stock_count", return_value=10), \
             patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock) as send:
            await shop_bot.show_order_confirmation(target, SimpleNamespace(id=123), qty=4)
        text = send.await_args.args[1]
        self.assertIn('emoji-id="5807868868886009920"', text)
        self.assertIn("4 link", text)
        self.assertIn("80,000 VNĐ", text)
        buttons = send.await_args.kwargs["reply_markup"].inline_keyboard
        self.assertEqual(buttons[0][0].callback_data, "create_order_vietqr_4")
        self.assertEqual(buttons[1][0].callback_data, "create_order_usdt_4")
        self.assertEqual(buttons[0][0].to_dict()["icon_custom_emoji_id"], "5967548335542767952")
        self.assertEqual(buttons[1][0].to_dict()["icon_custom_emoji_id"], "5292251044204521072")
        self.assertEqual(buttons[1][0].to_dict()["style"], "success")

    async def test_invoice_failure_does_not_create_local_order_or_show_wallet(self):
        query = SimpleNamespace(data="create_order_usdt_1", answer=AsyncMock(),
                                message=SimpleNamespace(chat_id=123, message_id=42))
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=123))
        with patch.object(shop_bot, "create_web_plisio_invoice", side_effect=RuntimeError("fixture")), \
             patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock) as send:
            await shop_bot.callback_handler(update, None)
        self.assertIn("Chưa thể tạo hóa đơn Plisio", send.await_args.args[1])
        conn = shop_bot.get_db_connection()
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)

    async def test_plisio_checkout_reuses_order_reference_and_keeps_server_price(self):
        query = SimpleNamespace(message=SimpleNamespace(chat_id=123, message_id=42))
        user = SimpleNamespace(id=123, username="fixture", first_name="Fixture")
        def invoice(code, _user, qty):
            return {"success": True, "order_code": code, "amount_usdt": 2.50, "qty": qty,
                    "currency": "USDT_TRX", "expires_at": "2099-01-01T00:00:00Z",
                    "invoice_url": "https://plisio.net/invoice/fixture"}
        with patch.object(shop_bot, "create_web_plisio_invoice", side_effect=invoice) as create, \
             patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock) as send:
            await shop_bot.show_plisio_checkout(query,user,2)
            await shop_bot.show_plisio_checkout(query,user,2)
        self.assertEqual(create.call_args_list[0].args[0],create.call_args_list[1].args[0])
        conn=shop_bot.get_db_connection()
        self.addCleanup(conn.close)
        rows=conn.execute("SELECT * FROM orders").fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["amount"],2.5)
        self.assertEqual(rows[0]["quantity"],2)
        self.assertIn("2.5 USDT",send.await_args.args[1])
        button=send.await_args.kwargs["reply_markup"].inline_keyboard[0][0]
        self.assertEqual(button.url,"https://plisio.net/invoice/fixture")

    async def test_delivery_claims_full_web_bundle_once_without_duplicates(self):
        conn=shop_bot.get_db_connection()
        conn.execute("INSERT INTO orders(order_id,user_id,product_id,amount,quantity) VALUES ('TGSBUNDLE',123,'gemini_plus',40000,2)")
        conn.commit()
        conn.close()
        with patch.object(shop_bot,"claim_stock_from_web",return_value="https://fixture/one\nhttps://fixture/two") as claim:
            self.assertEqual(shop_bot.deliver_product("TGSBUNDLE",123),["https://fixture/one","https://fixture/two"])
            self.assertEqual(shop_bot.deliver_product("TGSBUNDLE",123),["https://fixture/one","https://fixture/two"])
            self.assertIsNone(shop_bot.deliver_product("TGSBUNDLE",999))
            claim.assert_called_once()

    async def test_database_reinitialization_preserves_order_and_never_imports_local_links(self):
        conn = shop_bot.get_db_connection()
        conn.execute("INSERT INTO orders (order_id,user_id,product_id,amount) VALUES ('TGSFIXTURE',123,'gemini_plus',20000)")
        conn.commit()
        conn.close()
        Path(shop_bot.CLAIMED_LINKS_FILE).write_text("https://example.invalid/private-link", encoding="utf-8")
        shop_bot.init_database()
        conn = shop_bot.get_db_connection()
        self.addCleanup(conn.close)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 1)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM stock").fetchone()[0], 0)

    async def test_wallet_can_be_configured_from_railway_environment(self):
        wallet = "0x" + "a" * 40
        with patch.dict(os.environ, {"USDT_WALLET": wallet}):
            shop_bot.init_database()
        self.assertEqual(shop_bot.get_setting("usdt_wallet"), wallet)

    async def test_unconfirmed_payment_informs_customer_without_messaging_admin(self):
        conn = shop_bot.get_db_connection()
        conn.execute("INSERT INTO orders(order_id,user_id,product_id,amount,quantity,expires_at) VALUES ('TGSWAIT01',123,'gemini_plus',20000,1,'2099-01-01 00:00:00')")
        conn.commit()
        conn.close()
        for status, expected in (({"status": "pending"}, "chưa nhận được thanh toán"),
                                 ({}, "chưa kiểm tra được"),
                                 ({"status": "paid"}, "đã được xác nhận")):
            message = SimpleNamespace(reply_text=AsyncMock())
            query = SimpleNamespace(data="confirm_pay_TGSWAIT01", answer=AsyncMock(), message=message)
            update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=123))
            context = SimpleNamespace(bot=SimpleNamespace(send_message=AsyncMock()))
            with patch.object(shop_bot, "check_web_order_status", return_value=status), \
                 patch.object(shop_bot, "deliver_product", return_value=None):
                await shop_bot.callback_handler(update, context)
            text = message.reply_text.await_args.args[0]
            self.assertIn(expected, text)
            self.assertIn("@wolfmodyt", text)
            self.assertIn('emoji-id="5886666250158870040"', text)
            context.bot.send_message.assert_not_awaited()

    async def test_vietqr_checkout_creates_quantity_and_ten_minute_expiry(self):
        query = SimpleNamespace(data="create_order_vietqr_2", answer=AsyncMock(),
                                message=SimpleNamespace(reply_photo=AsyncMock(), delete=AsyncMock()))
        update = SimpleNamespace(callback_query=query,
                                 effective_user=SimpleNamespace(id=123, username="fixture", first_name="Fixture"))
        with patch.object(shop_bot, "get_stock_count", return_value=10), \
             patch.object(shop_bot, "generate_vietqr_png_bytes", return_value=b"fixture"), \
             patch.object(shop_bot, "sync_order_to_web_async", new_callable=AsyncMock) as sync:
            await shop_bot.callback_handler(update, None)
            await asyncio.sleep(0)
            self.assertEqual(sync.await_args.kwargs["qty"], 2)
        conn = shop_bot.get_db_connection()
        self.addCleanup(conn.close)
        order = conn.execute("SELECT * FROM orders").fetchone()
        self.assertEqual(order["amount"], 40000)
        self.assertEqual(order["quantity"], 2)
        self.assertEqual(order["status"], "pending")
        created = datetime.fromisoformat(order["created_at"])
        expires = datetime.fromisoformat(order["expires_at"])
        self.assertEqual((expires - created).total_seconds(), 600)
