import asyncio
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import shop_bot

PLUS = "gemini_plus"
PRO = "gemini_pro_account"
WALLET = "0x" + "ab" * 20  # a fixture address, NOT the shop's real wallet
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def web_response(order_id, **overrides):
    body = {"success": True, "order_code": order_id, "network": "BSC (BEP20)", "token": "USDT", "contract": "0x" + "22" * 20,
            "address": WALLET, "amount_usdt": "5.01", "qty": 1, "amount_vnd": 125000,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z"),
            "min_confirmations": 12}
    body.update(overrides)
    return body


class FakeWeb:
    """Replaces urllib.request.urlopen: records requests and answers from a script."""
    def __init__(self, test):
        self.requests = []
        self.reply = lambda path, payload: (200, {})
        self.test = test

    def __call__(self, request, timeout=0):
        path = request.full_url.replace("https://www.wolfmod.xyz", "")
        payload = json.loads(request.data)
        self.requests.append((path, payload, dict(request.header_items())))
        status, body = self.reply(path, payload)
        if status != 200:
            raise urllib.error.HTTPError(request.full_url, status, "err", {}, BytesIO(json.dumps(body).encode()))
        manager = MagicMock()
        manager.__enter__.return_value = SimpleNamespace(status=200, read=lambda n=-1: json.dumps(body).encode())
        return manager


def make_query(data, message_id=42):
    message = SimpleNamespace(chat_id=1, message_id=message_id, photo=None, reply_photo=AsyncMock(), reply_text=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(data=data, answer=AsyncMock(), message=message)


def user_obj():
    return SimpleNamespace(id=123, username="u", first_name="U")


def datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class Bep20Tests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = patch.multiple(shop_bot, DATA_DIR=self.directory.name, DB_PATH=str(Path(self.directory.name) / "shop.db"),
                                  CLAIMED_LINKS_FILE=str(Path(self.directory.name) / "c.txt"), BOT_TOKEN="test-token")
        settings.start()
        self.addCleanup(settings.stop)
        for name, kwargs in (("sync_web_product_price", {"return_value": True}),):
            patcher = patch.object(shop_bot, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        shop_bot._catalogue.update(items=None, next=0.0)
        shop_bot._web_stock_cache.clear()
        self.addCleanup(lambda: shop_bot._catalogue.update(items=None, next=0.0))
        with patch.dict(os.environ, {"USDT_WALLET": ""}):
            shop_bot.init_database()
        shop_bot.set_setting("default_lang", "vi")  # legacy tests assert the Vietnamese UI
        self.web = FakeWeb(self)
        opener = patch("urllib.request.urlopen", self.web)
        opener.start()
        self.addCleanup(opener.stop)
        self.set_flag("1")

    def set_flag(self, value):
        patcher = patch.dict(os.environ, {"BEP20_ENABLED": value})
        patcher.start()
        self.addCleanup(patcher.stop)

    def orders(self):
        conn = shop_bot.get_db_connection()
        self.addCleanup(conn.close)
        return conn.execute("SELECT * FROM orders").fetchall()

    def ok(self, status=200):
        self.web.reply = lambda path, payload: (status, web_response(payload["order_code"], qty=payload["qty"]))

    async def checkout(self, sku=PLUS, qty=1, message_id=42):
        query = make_query("x", message_id)
        with patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock) as render, \
             patch.object(shop_bot, "generate_crypto_qr_png_bytes", return_value=PNG) as qr:
            await shop_bot.show_bep20_checkout(query, user_obj(), qty, sku=sku)
        return query, render, qr

    # ---------- switch ----------
    async def test_disabled_by_default_no_button_no_web_call_no_order(self):
        self.set_flag("0")
        target = SimpleNamespace(reply_text=AsyncMock())
        with patch.object(shop_bot, "get_stock_count", return_value=5):
            await shop_bot.show_order_confirmation(target, user_obj(), sku=PLUS, qty=1)
        keyboard = datas(target.reply_text.await_args.kwargs["reply_markup"])
        self.assertTrue(keyboard)
        self.assertFalse([d for d in keyboard if d and d.startswith("cb:")])
        query, render, _ = await self.checkout()
        query.answer.assert_awaited()
        self.assertEqual(self.web.requests, [])
        self.assertEqual(self.orders(), [])

    async def test_enabled_shows_a_bep20_button_next_to_the_existing_methods(self):
        for sku, expected in ((PLUS, "cb:gemini_plus:2"), (PRO, "cb:gemini_pro_account:1")):
            target = SimpleNamespace(reply_text=AsyncMock())
            qty = 2 if sku == PLUS else 1
            with patch.object(shop_bot, "get_stock_count", return_value=5):
                await shop_bot.show_order_confirmation(target, user_obj(), sku=sku, qty=qty)
            keyboard = datas(target.reply_text.await_args.kwargs["reply_markup"])
            self.assertIn(expected, keyboard)
            self.assertIn(shop_bot.cb_qty("vietqr", sku, qty), keyboard)  # existing methods untouched
            self.assertIn(shop_bot.cb_qty("usdt", sku, qty), keyboard)    # Plisio untouched

    async def test_callback_routes_to_checkout_and_is_valid_telegram_data(self):
        longest = "a" * 40
        self.assertLessEqual(len(shop_bot.cb_qty("bep20", longest, 50).encode()), 64)
        self.assertEqual(shop_bot.cb_qty("bep20", PLUS, 3), "cb:gemini_plus:3")
        self.assertEqual(shop_bot.parse_sku_qty_callback("cb:gemini_plus:3"), ("cb", PLUS, 3))
        self.assertIsNone(shop_bot.parse_sku_qty_callback("cb:not_a_listed_sku:1"))
        self.ok()
        query = make_query("cb:gemini_plus:1")
        with patch.object(shop_bot, "show_bep20_checkout", new_callable=AsyncMock) as show:
            await shop_bot.callback_handler(SimpleNamespace(callback_query=query, effective_user=user_obj()), SimpleNamespace())
        show.assert_awaited_once()
        self.assertEqual(show.await_args.args[2], 1)

    # ---------- invoice creation ----------
    async def test_checkout_creates_web_order_stores_local_order_and_shows_qr_of_the_address(self):
        self.ok()
        query, render, qr = await self.checkout(PRO)
        (path, payload, headers), = self.web.requests
        self.assertEqual(path, "/api/tg/orders/bep20")
        self.assertEqual((payload["sku"], payload["qty"], payload["tg_user_id"]), (PRO, 1, 123))
        self.assertRegex(payload["order_code"], r"^TGS[0-9A-F]{12}$")
        self.assertEqual(headers["Authorization"], "Bearer test-token")
        row, = self.orders()
        self.assertEqual((row["product_id"], row["amount"], row["payment_method"], row["currency"], row["quantity"], row["status"]),
                         (PRO, 5.01, "bep20", "USDT", 1, "pending"))
        self.assertEqual(row["order_id"], payload["order_code"])
        qr.assert_called_once_with(WALLET)  # the QR encodes exactly the plain wallet address
        query.message.reply_photo.assert_awaited_once()
        photo = query.message.reply_photo.await_args.kwargs
        caption = photo["caption"]
        for expected in ("BEP20", "USDT", "5.01", WALLET, "đúng từng chữ số", "Chuyển sai mạng"):
            self.assertIn(expected, caption)
        self.assertIn(f"confirm_pay_{row['order_id']}", datas(photo["reply_markup"]))

    def test_real_qr_generator_encodes_the_plain_address_locally(self):
        captured = {}
        import qrcode

        class SpyQR(qrcode.QRCode):
            def add_data(self, data, optimize=20):
                captured["data"] = data
                return super().add_data(data, optimize)
        with patch("qrcode.QRCode", SpyQR), patch("urllib.request.urlopen", side_effect=AssertionError("QR must be generated locally")):
            png = shop_bot.generate_crypto_qr_png_bytes(WALLET)
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertEqual(captured["data"], WALLET)

    async def test_same_message_gives_same_order_id_and_ids_never_collide_with_plisio_or_across_skus(self):
        self.ok()
        await self.checkout(PLUS, 1, 42)
        await self.checkout(PLUS, 1, 42)
        self.assertEqual(len(self.orders()), 1)
        self.assertEqual(self.web.requests[0][1]["order_code"], self.web.requests[1][1]["order_code"])
        await self.checkout(PRO, 1, 42)
        ids = {payload["order_code"] for _p, payload, _h in self.web.requests}
        self.assertEqual(len(ids), 2)
        import hashlib
        plisio_id = "TGS" + hashlib.sha256(f"123:1:42:1".encode()).hexdigest()[:12].upper()
        self.assertNotIn(plisio_id, ids)

    async def test_invalid_web_responses_are_rejected_without_creating_a_local_order(self):
        bad = [dict(network="TRC20"), dict(token="USDC"), dict(address="0x123"), dict(address="0x" + "zz" * 20), dict(address=None),
               dict(amount_usdt="5.1"), dict(amount_usdt="0.00"), dict(amount_usdt=5.01), dict(amount_usdt="-5.01"),
               dict(success=False), dict(order_code="TGSOTHER00000"), dict(qty=2), dict(expires_at=None), dict(expires_at="not a date"),
               dict(min_confirmations=0), dict(min_confirmations=True), dict(min_confirmations=5000)]
        for overrides in bad:
            self.web.reply = lambda path, payload, o=overrides: (200, web_response(payload["order_code"], **o))
            query, render, qr = await self.checkout(PLUS, 1, message_id=hash(json.dumps(overrides, default=str)) % 100000)
            self.assertEqual(self.orders(), [], overrides)
            qr.assert_not_called()
            query.message.reply_photo.assert_not_awaited()
            self.assertIn("Chưa thể tạo hóa đơn", render.await_args.args[1], overrides)

    async def test_web_errors_show_a_safe_message_and_create_no_order(self):
        cases = ((409, {"error": "out_of_stock"}, "hết hàng"), (409, {"error": "product_unavailable"}, "chưa hỗ trợ"),
                 (503, {"error": "busy_try_again"}, "bận"), (429, {"error": "too_many_pending_orders"}, "quá nhiều đơn"),
                 (503, {"error": "bep20_disabled"}, "Chưa thể tạo"), (500, {}, "Chưa thể tạo"), (401, {"error": "Unauthorized"}, "Chưa thể tạo"),
                 (409, {"error": "<script>alert(1)</script>"}, "Chưa thể tạo"))
        for status, body, expected in cases:
            self.web.reply = lambda path, payload, s=status, b=body: (s, b)
            query, render, _ = await self.checkout(PLUS, 1, message_id=status + len(str(body)))
            self.assertIn(expected, render.await_args.args[1])
            self.assertNotIn("<script>", render.await_args.args[1])
        self.assertEqual(self.orders(), [])

    async def test_network_failure_creates_no_order(self):
        with patch("urllib.request.urlopen", side_effect=OSError("down")):
            query, render, _ = await self.checkout()
        self.assertEqual(self.orders(), [])
        self.assertIn("Chưa thể tạo", render.await_args.args[1])

    async def test_quantity_and_sku_bounds(self):
        self.ok()
        for sku, qty in ((PRO, 2), (PLUS, 0), (PLUS, 51), ("unlisted_sku", 1)):
            query, _, _ = await self.checkout(sku, qty)
            query.answer.assert_awaited()
        self.assertEqual(self.web.requests, [])
        self.assertEqual(self.orders(), [])

    # ---------- confirmation ----------
    def add_bep20_order(self, order_id="TGSBEP200001", status="pending", method="bep20", sku=PLUS):
        conn = shop_bot.get_db_connection()
        now = datetime.now()  # the bot stores local time
        conn.execute("""INSERT INTO orders (order_id,user_id,user_name,product_id,amount,status,payment_method,currency,quantity,created_at,expires_at)
                        VALUES (?,?,?,?,?,?,?,'USDT',1,?,?)""",
                     (order_id, 123, "u", sku, 1.01, status, method, now.strftime("%Y-%m-%d %H:%M:%S"),
                      (now + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
        return order_id

    async def press_check(self, order_id, web_status, bep20_result):
        query = make_query(f"confirm_pay_{order_id}")
        context = SimpleNamespace(bot=object())
        with patch.object(shop_bot, "check_web_bep20", return_value=bep20_result) as chain, \
             patch.object(shop_bot, "check_web_order_status", return_value={"status": web_status}), \
             patch.object(shop_bot, "send_paid_order_delivery", new_callable=AsyncMock, return_value=True) as deliver:
            await shop_bot.callback_handler(SimpleNamespace(callback_query=query, effective_user=user_obj()), context)
        return query, chain, deliver

    async def test_check_button_asks_the_web_to_check_the_chain_then_delivers_when_paid(self):
        order_id = self.add_bep20_order()
        query, chain, deliver = await self.press_check(order_id, "paid", {"status": "paid"})
        chain.assert_called_once_with(order_id, 123)
        deliver.assert_awaited_once()

    async def test_pending_shows_confirmation_progress_and_never_delivers(self):
        order_id = self.add_bep20_order()
        query, chain, deliver = await self.press_check(order_id, "pending", {"status": "pending", "confirmations": 2, "min_confirmations": 12})
        deliver.assert_not_awaited()
        self.assertIn("2/12", query.message.reply_text.await_args.args[0])

    async def test_needs_review_tells_the_customer_to_contact_the_admin_and_never_delivers(self):
        order_id = self.add_bep20_order()
        query, chain, deliver = await self.press_check(order_id, "pending", {"status": "needs_review"})
        deliver.assert_not_awaited()
        self.assertIn("kiểm tra thủ công", query.message.reply_text.await_args.args[0])  # the dedicated needs_review message, not the generic one

    async def test_web_check_failure_is_never_treated_as_paid(self):
        order_id = self.add_bep20_order()
        query, chain, deliver = await self.press_check(order_id, "pending", {})
        deliver.assert_not_awaited()

    async def test_other_payment_methods_never_trigger_a_chain_check(self):
        order_id = self.add_bep20_order(order_id="TGSVQR000001", method="vietqr")
        _, chain, _ = await self.press_check(order_id, "pending", {})
        chain.assert_not_called()

    def test_check_web_bep20_parsing_is_strict(self):
        for status, body in ((500, {}), (200, {"success": False, "status": "paid"}), (200, {"success": True}), (200, {"success": True, "status": 5}),
                             (200, ["paid"]), (409, {"success": True, "status": "paid"})):
            self.web.reply = lambda path, payload, s=status, b=body: (s, b)
            self.assertEqual(shop_bot.check_web_bep20("TGSBEP200001", 123), {}, (status, body))
        self.web.reply = lambda path, payload: (200, {"success": True, "status": "pending", "confirmations": 3, "min_confirmations": True, "x": 1})
        self.assertEqual(shop_bot.check_web_bep20("TGSBEP200001", 123), {"status": "pending", "confirmations": 3})
        with patch("urllib.request.urlopen", side_effect=OSError("down")):
            self.assertEqual(shop_bot.check_web_bep20("TGSBEP200001", 123), {})

    async def test_background_recovery_checks_only_pending_bep20_orders(self):
        self.add_bep20_order("TGSBEP200001", "pending", "bep20")
        self.add_bep20_order("TGSVQR000001", "pending", "vietqr")
        self.add_bep20_order("TGSBEP200002", "cancelled", "bep20")
        with patch.object(shop_bot, "check_web_bep20", return_value={}) as chain, \
             patch.object(shop_bot, "check_web_order_status", return_value={"status": "pending"}), \
             patch.object(shop_bot.asyncio, "sleep", new_callable=AsyncMock, side_effect=[None, None, None, asyncio.CancelledError]):
            with self.assertRaises(asyncio.CancelledError):
                await shop_bot.auto_deliver_paid_orders_task(SimpleNamespace(bot=object()))
        self.assertEqual([call.args[0] for call in chain.call_args_list], ["TGSBEP200001"])

    # ---------- no hardcoded receiving data ----------
    def test_shop_wallet_and_binance_id_are_not_in_the_source(self):
        source = (Path(shop_bot.__file__).read_text(encoding="utf-8")).lower()
        self.assertNotIn("0xff924e6b692567a4d9462a3bbef33f48a138dd2e", source)
        self.assertNotIn("1158594960", source)


if __name__ == "__main__":
    unittest.main()
