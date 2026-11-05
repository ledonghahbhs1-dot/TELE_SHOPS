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
