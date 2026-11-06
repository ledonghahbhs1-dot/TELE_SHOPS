import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import shop_bot

PRO = "gemini_pro_account"
PLUS = "gemini_plus"

real_sync_web_product_price = shop_bot.sync_web_product_price


def make_query(data, chat_id=123, message_id=42):
    message = SimpleNamespace(chat_id=chat_id, message_id=message_id, photo=None,
                              reply_photo=AsyncMock(), reply_text=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(data=data, answer=AsyncMock(), message=message)


def make_update(query, user_id=123):
    return SimpleNamespace(callback_query=query,
                           effective_user=SimpleNamespace(id=user_id, username="fixture", first_name="Fixture"))


def buttons(markup):
    return [button for row in markup.inline_keyboard for button in row]


class MultiProductTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = patch.multiple(shop_bot,
            DATA_DIR=self.directory.name,
            DB_PATH=str(Path(self.directory.name) / "shop_database.db"),
            CLAIMED_LINKS_FILE=str(Path(self.directory.name) / "claimed_links.txt"))
        settings.start()
        self.addCleanup(settings.stop)
        price_refresh = patch.object(shop_bot, "sync_web_product_price", return_value=True)
        price_refresh.start()
        self.addCleanup(price_refresh.stop)
        shop_bot._web_stock_cache.clear()
        with patch.dict(os.environ, {"USDT_WALLET": ""}):
            shop_bot.init_database()

    def orders(self):
        conn = shop_bot.get_db_connection()
        self.addCleanup(conn.close)
        return conn.execute("SELECT * FROM orders ORDER BY created_at").fetchall()

    def stock(self, plus=10, pro=3):
        counts = {PLUS: plus, PRO: pro}
        return patch.object(shop_bot, "get_stock_count", side_effect=lambda sku="gemini_plus": counts.get(sku, 0))

    # ---------- catalogue / menu ----------
    def test_main_menu_lists_both_products_and_keeps_old_plus_button(self):
        with self.stock(plus=10, pro=3):
            keyboard = shop_bot.main_menu_keyboard(1)
        flat = buttons(keyboard)
        plus = [b for b in flat if b.callback_data == "buy_gemini_plus"]
        pro = [b for b in flat if b.callback_data == f"buy_{PRO}"]
        self.assertEqual(len(plus), 1)
        self.assertIn("MUA GOOGLE AI PLUS 12 THÁNG", plus[0].text)
        self.assertEqual(len(pro), 1)
        self.assertIn("ACCOUNT GEMINI PRO", pro[0].text)
        self.assertIn("125,000", pro[0].text)

    def test_out_of_stock_product_is_still_listed_but_cannot_be_bought(self):
        with self.stock(plus=10, pro=0):
            flat = buttons(shop_bot.main_menu_keyboard(1))
        sold_out = [b for b in flat if b.callback_data == f"soldout_{PRO}"]
        self.assertEqual(len(sold_out), 1)
        self.assertIn("HẾT HÀNG", sold_out[0].text)
        self.assertFalse([b for b in flat if b.callback_data == f"buy_{PRO}"])

    def test_product_without_known_price_is_hidden(self):
        conn = shop_bot.get_db_connection()
        conn.execute("UPDATE products SET price = 0 WHERE id = ?", (PRO,))
        conn.commit()
        conn.close()
        with self.stock():
            flat = buttons(shop_bot.main_menu_keyboard(1))
        self.assertFalse([b for b in flat if PRO in (b.callback_data or "")])

    async def test_sold_out_detail_screen_has_no_purchase_buttons(self):
        query = make_query(f"buy_{PRO}")
        with self.stock(plus=10, pro=0), patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock) as render:
            await shop_bot.callback_handler(make_update(query), SimpleNamespace())
        text = render.await_args.args[1]
        self.assertIn("HẾT HÀNG", text)
        datas = [b.callback_data for b in buttons(render.await_args.kwargs["reply_markup"])]
        self.assertEqual(datas, ["main_menu"])

    async def test_sold_out_button_only_alerts(self):
        query = make_query(f"soldout_{PRO}")
        with self.stock(plus=10, pro=0):
            await shop_bot.callback_handler(make_update(query), SimpleNamespace())
        self.assertTrue(query.answer.await_args_list[-1].kwargs.get("show_alert"))
        self.assertEqual(self.orders(), [])

    async def test_pro_detail_shows_description_and_single_buy_button(self):
        query = make_query(f"buy_{PRO}")
        with self.stock(plus=10, pro=3), patch.object(shop_bot, "get_product_banner_path", return_value=None), \
             patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock) as render:
            await shop_bot.callback_handler(make_update(query), SimpleNamespace())
        self.assertIn("ACCOUNT GEMINI PRO FULL BENEFITS", render.await_args.args[1])
        self.assertIn("Gmail|password", render.await_args.args[1])
        datas = [b.callback_data for b in buttons(render.await_args.kwargs["reply_markup"])]
        self.assertEqual(datas, [f"sq:{PRO}:1", "main_menu"])

    def test_callback_data_fits_telegram_limit_and_old_plus_format_is_unchanged(self):
        self.assertEqual(shop_bot.cb_qty("select", PLUS, 4), "select_qty_4")
        self.assertEqual(shop_bot.cb_qty("vietqr", PLUS, 2), "create_order_vietqr_2")
        self.assertEqual(shop_bot.cb_qty("usdt", PLUS, 2), "create_order_usdt_2")
        for kind in ("select", "vietqr", "usdt"):
            data = shop_bot.cb_qty(kind, PRO, 50)
            self.assertLessEqual(len(data.encode()), 64)
            self.assertEqual(shop_bot.parse_sku_qty_callback(data)[1:], (PRO, 50))
        self.assertIsNone(shop_bot.parse_sku_qty_callback("cv:not_a_listed_sku:1"))
        self.assertIsNone(shop_bot.parse_sku_qty_callback("cv:gemini_pro_account:1; DROP"))

    # ---------- orders record the real SKU ----------
    async def test_vietqr_order_for_pro_is_recorded_and_synced_as_pro(self):
        query = make_query(shop_bot.cb_qty("vietqr", PRO, 1))
        with self.stock(plus=10, pro=3), patch.object(shop_bot, "generate_vietqr_png_bytes", return_value=None), \
             patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock), \
             patch.object(shop_bot, "sync_order_to_web_async", new_callable=AsyncMock) as sync:
            await shop_bot.callback_handler(make_update(query), SimpleNamespace())
            await asyncio.sleep(0)
        rows = self.orders()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["product_id"], PRO)
        self.assertEqual(rows[0]["amount"], 125000)
        self.assertEqual(rows[0]["quantity"], 1)
        sync.assert_awaited_once()
        self.assertEqual(sync.await_args.kwargs["sku"], PRO)
        self.assertEqual(sync.await_args.args[3], 125000)

    async def test_vietqr_order_for_plus_still_recorded_as_plus(self):
        query = make_query("create_order_vietqr_2")
        price = shop_bot.get_product(PLUS)["price"]
        with self.stock(plus=10, pro=3), patch.object(shop_bot, "generate_vietqr_png_bytes", return_value=None), \
             patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock), \
             patch.object(shop_bot, "sync_order_to_web_async", new_callable=AsyncMock) as sync:
            await shop_bot.callback_handler(make_update(query), SimpleNamespace())
            await asyncio.sleep(0)
        rows = self.orders()
        self.assertEqual([(r["product_id"], r["amount"], r["quantity"]) for r in rows], [(PLUS, price * 2, 2)])
        self.assertEqual(sync.await_args.kwargs["sku"], PLUS)

    async def test_pro_order_refused_when_out_of_stock_or_over_max_qty(self):
        for data, stock_pro in ((shop_bot.cb_qty("vietqr", PRO, 1), 0), (shop_bot.cb_qty("vietqr", PRO, 2), 5)):
            query = make_query(data)
            with self.stock(plus=10, pro=stock_pro), patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock), \
                 patch.object(shop_bot, "sync_order_to_web_async", new_callable=AsyncMock) as sync:
                await shop_bot.callback_handler(make_update(query), SimpleNamespace())
            sync.assert_not_awaited()
        self.assertEqual(self.orders(), [])

    async def test_unlisted_sku_callback_creates_no_order(self):
        query = make_query("cv:evil_sku:1")
        with self.stock(), patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock), \
             patch.object(shop_bot, "sync_order_to_web_async", new_callable=AsyncMock) as sync:
            await shop_bot.callback_handler(make_update(query), SimpleNamespace())
        sync.assert_not_awaited()
        self.assertEqual(self.orders(), [])

    async def test_plisio_order_for_pro_is_recorded_as_pro_and_ids_differ_from_plus(self):
        user = SimpleNamespace(id=123, username="fixture", first_name="Fixture")
        seen = []

        def invoice(code, _user, qty, sku=shop_bot.DEFAULT_SKU):
            seen.append((code, sku))
            return {"success": True, "order_code": code, "amount_usdt": 5.0, "qty": qty, "currency": "USDT_BSC",
                    "expires_at": "2099-01-01T00:00:00Z", "invoice_url": "https://plisio.net/invoice/fixture"}
        with patch.object(shop_bot, "create_web_plisio_invoice", side_effect=invoice), \
             patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock):
            # same chat, same message, same qty: only the SKU differs
            await shop_bot.show_plisio_checkout(make_query("x"), user, 1)
            await shop_bot.show_plisio_checkout(make_query("x"), user, 1, sku=PRO)
        self.assertEqual([sku for _code, sku in seen], [PLUS, PRO])
        self.assertNotEqual(seen[0][0], seen[1][0])
        by_id = {row["order_id"]: row["product_id"] for row in self.orders()}
        self.assertEqual(by_id[seen[0][0]], PLUS)
        self.assertEqual(by_id[seen[1][0]], PRO)

    async def test_plisio_for_pro_rejects_quantity_over_max(self):
        user = SimpleNamespace(id=123, username="fixture", first_name="Fixture")
        with patch.object(shop_bot, "create_web_plisio_invoice") as create:
            await shop_bot.show_plisio_checkout(make_query("x"), user, 2, sku=PRO)
        create.assert_not_called()
        self.assertEqual(self.orders(), [])

    def test_web_invoice_payload_carries_the_sku(self):
        payload = {}

        def fake_urlopen(request, timeout=0):
            payload.update(json.loads(request.data))
            manager = MagicMock()
            manager.__enter__.return_value = SimpleNamespace(read=lambda: json.dumps({
                "success": True, "order_code": "TGSFIXTURE1", "invoice_url": "https://plisio.net/i/1"}).encode())
            return manager
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            shop_bot.create_web_plisio_invoice("TGSFIXTURE1", SimpleNamespace(id=1, username=""), 1, PRO)
        self.assertEqual(payload["sku"], PRO)

    def test_startup_resync_keeps_each_orders_own_sku(self):
        conn = shop_bot.get_db_connection()
        for code, sku in (("TGSAAAA1111", PLUS), ("TGSBBBB2222", PRO)):
            conn.execute("INSERT INTO orders (order_id,user_id,product_id,amount,status,quantity,currency) VALUES (?,?,?,?,?,?,?)",
                         (code, 123, sku, 125000, "pending", 1, "VND"))
        conn.commit()
        conn.close()
        with patch.object(shop_bot, "sync_order_to_web_sync") as sync:
            shop_bot.sync_all_pending_orders_on_startup()
        skus = {call.args[0]: call.kwargs["sku"] for call in sync.call_args_list}
        self.assertEqual(skus, {"TGSAAAA1111": PLUS, "TGSBBBB2222": PRO})

    def test_order_sync_payload_uses_product_name_and_sku(self):
        payload = {}

        def fake_urlopen(request, timeout=0):
            payload.update(json.loads(request.data))
            manager = MagicMock()
            manager.__enter__.return_value = SimpleNamespace(status=200)
            return manager
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            shop_bot.sync_order_to_web_sync("TGSCCCC3333", 123, "u", 125000, "VND", "sepay", 1, PRO)
        self.assertEqual(payload["sku"], PRO)
        self.assertEqual(payload["product_name"], "ACCOUNT GEMINI PRO FULL BENEFITS")
        self.assertEqual(payload["amount_vnd"], 125000)

    # ---------- delivery ----------
    async def test_pro_delivery_sends_account_text_without_url_buttons(self):
        conn = shop_bot.get_db_connection()
        conn.execute("INSERT INTO orders (order_id,user_id,product_id,amount,status,quantity) VALUES (?,?,?,?,?,?)",
                     ("TGSPRO000001", 123, PRO, 125000, "pending", 1))
        conn.commit()
        conn.close()
        bot = SimpleNamespace(send_message=AsyncMock())
        with patch.object(shop_bot, "claim_stock_from_web", return_value="someone@gmail.com|p&ss") as claim:
            self.assertTrue(await shop_bot.send_paid_order_delivery(bot, "TGSPRO000001", 123))
        self.assertEqual(claim.call_args.args[0], PRO)  # claims PRO stock, not gemini_plus
        kwargs = bot.send_message.await_args.kwargs
        self.assertIn("someone@gmail.com|p&amp;ss", kwargs["text"])
        self.assertNotIn("Google AI Plus", kwargs["text"])
        self.assertTrue(all(getattr(b, "url", None) is None for b in buttons(kwargs["reply_markup"])))

    async def test_plus_delivery_message_is_unchanged(self):
        text, markup = shop_bot.format_delivered_links_message("TGSX", ["https://example.com/a"])
        self.assertIn("Link nhận Google AI Plus 400GB", text)
        self.assertEqual(buttons(InlineKeyboardMarkup_from(markup))[0].url, "https://example.com/a")

    async def test_purchase_history_names_the_real_product(self):
        conn = shop_bot.get_db_connection()
        conn.execute("INSERT INTO orders (order_id,user_id,product_id,amount,status,item_delivered) VALUES (?,?,?,?,?,?)",
                     ("TGSPROHIST1", 123, PRO, 125000, "completed", "a@gmail.com|pw"))
        conn.commit()
        conn.close()
        query = make_query(f"ord:{PRO}")
        with patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock) as render:
            await shop_bot.callback_handler(make_update(query), SimpleNamespace())
        self.assertIn("ACCOUNT GEMINI PRO FULL BENEFITS", render.await_args.args[1])

    # ---------- per-SKU caches and prices ----------
    def test_stock_cache_is_per_sku(self):
        def fake_urlopen(request, timeout=0):
            sku = request.full_url.split("sku=")[1]
            manager = MagicMock()
            manager.__enter__.return_value = SimpleNamespace(
                read=lambda: json.dumps({"success": True, "available": {PLUS: 294, PRO: 0}[sku]}).encode())
            return manager
        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self.assertEqual(shop_bot.get_stock_count(PLUS), 294)
            self.assertEqual(shop_bot.get_stock_count(PRO), 0)  # was served from plus' cache before
            self.assertEqual(shop_bot.get_stock_count(PLUS), 294)

    def test_price_sync_is_per_sku_and_usdt_price_is_stored_per_sku(self):
        shop_bot._last_web_price_check.clear()

        def fake_urlopen(request, timeout=0):
            sku = request.full_url.split("sku=")[1]
            data = {PLUS: {"price_vnd": 35000, "price_usdt": 2.5}, PRO: {"price_vnd": 130000, "price_usdt": 5.5}}[sku]
            manager = MagicMock()
            manager.__enter__.return_value = SimpleNamespace(read=lambda: json.dumps({"success": True, "sku": sku, **data}).encode())
            return manager
        with patch.object(shop_bot, "BOT_TOKEN", "test-token"), patch("urllib.request.urlopen", side_effect=fake_urlopen):
            self.assertTrue(real_sync_web_product_price(PLUS))
            self.assertTrue(real_sync_web_product_price(PRO))  # within 10 s of plus: must not be skipped
        self.assertEqual(shop_bot.get_product(PLUS)["price"], 35000)
        self.assertEqual(shop_bot.get_product(PRO)["price"], 130000)
        self.assertEqual(shop_bot.get_usdt_price(PLUS), 2.5)
        self.assertEqual(shop_bot.get_usdt_price(PRO), 5.5)

    def test_shop_skus_always_start_with_default_and_reject_malformed_names(self):
        self.assertEqual(shop_bot._parse_shop_skus("gemini_pro_account"), [PLUS, PRO])
        self.assertEqual(shop_bot._parse_shop_skus(" a_b , ,BAD NAME,a_b,x;y"), [PLUS, "a_b"])


def InlineKeyboardMarkup_from(rows):
    return SimpleNamespace(inline_keyboard=rows)


if __name__ == "__main__":
    unittest.main()
