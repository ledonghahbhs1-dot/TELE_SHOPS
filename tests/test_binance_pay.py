import asyncio
import json
import os
import tempfile
import time
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
RECEIVER = "9999000011"  # a fixture, NOT the shop's real Binance Pay ID
ORDER_ID_TEXT = "453343187271753728"


def web_response(order_id, **overrides):
    body = {"success": True, "order_code": order_id, "network": "Binance Pay", "token": "USDT", "receiver_id": RECEIVER,
            "amount_usdt": "5.00", "qty": 1, "amount_vnd": 125000,
            "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat().replace("+00:00", "Z")}
    body.update(overrides)
    return body


class FakeWeb:
    def __init__(self):
        self.requests = []
        self.reply = lambda path, payload: (200, {})

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


def user_obj(uid=123):
    return SimpleNamespace(id=uid, username="u", first_name="U")


def make_update(text, uid=123):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    return SimpleNamespace(effective_user=user_obj(uid), message=message)


def datas(markup):
    return [b.callback_data for row in markup.inline_keyboard for b in row]


class BinancePayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = patch.multiple(shop_bot, DATA_DIR=self.directory.name, DB_PATH=str(Path(self.directory.name) / "shop.db"),
                                  CLAIMED_LINKS_FILE=str(Path(self.directory.name) / "c.txt"), BOT_TOKEN="test-token")
        settings.start()
        self.addCleanup(settings.stop)
        for name, kwargs in (("sync_web_product_price", {"return_value": True}), ("get_usdt_price", {"return_value": 5.0})):
            patcher = patch.object(shop_bot, name, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)
        shop_bot._catalogue.update(items=None, next=0.0)
        shop_bot._web_stock_cache.clear()
        self.addCleanup(lambda: shop_bot._catalogue.update(items=None, next=0.0))
        with patch.dict(os.environ, {"USDT_WALLET": ""}):
            shop_bot.init_database()
        shop_bot.set_setting("default_lang", "vi")  # legacy tests assert the Vietnamese UI
        self.web = FakeWeb()
        opener = patch("urllib.request.urlopen", self.web)
        opener.start()
        self.addCleanup(opener.stop)
        shop_bot.AWAITING_BINANCE_ID.clear()
        self.addCleanup(shop_bot.AWAITING_BINANCE_ID.clear)
        self.set_flag("1")

    def set_flag(self, value):
        patcher = patch.dict(os.environ, {"BINANCE_PAY_ENABLED": value})
        patcher.start()
        self.addCleanup(patcher.stop)

    def orders(self):
        conn = shop_bot.get_db_connection()
        self.addCleanup(conn.close)
        return conn.execute("SELECT * FROM orders").fetchall()

    def ok(self, **overrides):
        self.web.reply = lambda path, payload: (200, web_response(payload["order_code"], qty=payload["qty"], **overrides))

    async def checkout(self, sku=PLUS, qty=1, message_id=42):
        query = make_query("x", message_id)
        with patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock) as render:
            await shop_bot.show_binance_checkout(query, user_obj(), qty, sku=sku)
        return query, render

    # ---------- switch ----------
    async def test_disabled_by_default_no_button_no_web_call_no_order(self):
        self.set_flag("0")
        target = SimpleNamespace(reply_text=AsyncMock())
        with patch.object(shop_bot, "get_stock_count", return_value=5):
            await shop_bot.show_order_confirmation(target, user_obj(), sku=PLUS, qty=1)
        keyboard = datas(target.reply_text.await_args.kwargs["reply_markup"])
        self.assertTrue(keyboard)
        self.assertFalse([d for d in keyboard if d and d.startswith("cp:")])
        query, _ = await self.checkout()
        query.answer.assert_awaited()
        self.assertEqual(self.web.requests, [])
        self.assertEqual(self.orders(), [])
        self.assertFalse(shop_bot.binance_pay_enabled())
        # the id-prompt button is inert while disabled
        q = make_query("bpid_TGSABCDEF0123")
        await shop_bot.prompt_binance_id(q, user_obj(), "TGSABCDEF0123")
        self.assertEqual(shop_bot.AWAITING_BINANCE_ID, {})

    async def test_enabled_adds_a_button_and_leaves_existing_methods_untouched(self):
        for sku, qty, expected in ((PLUS, 2, "cp:gemini_plus:2"), (PRO, 1, "cp:gemini_pro_account:1")):
            target = SimpleNamespace(reply_text=AsyncMock())
            with patch.object(shop_bot, "get_stock_count", return_value=5):
                await shop_bot.show_order_confirmation(target, user_obj(), sku=sku, qty=qty)
            keyboard = datas(target.reply_text.await_args.kwargs["reply_markup"])
            self.assertIn(expected, keyboard)
            self.assertIn(shop_bot.cb_qty("vietqr", sku, qty), keyboard)
            self.assertIn(shop_bot.cb_qty("usdt", sku, qty), keyboard)

    async def test_bep20_button_is_unaffected_and_separate(self):
        with patch.dict(os.environ, {"BEP20_ENABLED": "1"}):
            target = SimpleNamespace(reply_text=AsyncMock())
            with patch.object(shop_bot, "get_stock_count", return_value=5):
                await shop_bot.show_order_confirmation(target, user_obj(), sku=PLUS, qty=1)
        keyboard = datas(target.reply_text.await_args.kwargs["reply_markup"])
        self.assertIn("cb:gemini_plus:1", keyboard)
        self.assertIn("cp:gemini_plus:1", keyboard)

    async def test_callback_routing_and_telegram_limits(self):
        self.assertLessEqual(len(shop_bot.cb_qty("binance", "a" * 40, 50).encode()), 64)
        self.assertEqual(shop_bot.cb_qty("binance", PLUS, 3), "cp:gemini_plus:3")
        self.assertEqual(shop_bot.parse_sku_qty_callback("cp:gemini_plus:3"), ("cp", PLUS, 3))
        self.assertIsNone(shop_bot.parse_sku_qty_callback("cp:not_a_listed_sku:1"))
        self.assertLessEqual(len(("bpid_TGS" + "A" * 17).encode()), 64)
        query = make_query("cp:gemini_plus:1")
        with patch.object(shop_bot, "show_binance_checkout", new_callable=AsyncMock) as show:
            await shop_bot.callback_handler(SimpleNamespace(callback_query=query, effective_user=user_obj()), SimpleNamespace())
        show.assert_awaited_once()
        query = make_query("bpid_TGSABCDEF0123")
        with patch.object(shop_bot, "prompt_binance_id", new_callable=AsyncMock) as prompt:
            await shop_bot.callback_handler(SimpleNamespace(callback_query=query, effective_user=user_obj()), SimpleNamespace())
        prompt.assert_awaited_once()
        self.assertEqual(prompt.await_args.args[2], "TGSABCDEF0123")

    # ---------- invoice ----------
    async def test_checkout_creates_web_order_stores_local_order_and_shows_the_exact_price_without_any_cents_talk(self):
        self.ok()
        query, render = await self.checkout(PRO)
        (path, payload, headers), = self.web.requests
        self.assertEqual(path, "/api/tg/orders/binance-pay")
        self.assertEqual((payload["sku"], payload["qty"], payload["tg_user_id"]), (PRO, 1, 123))
        self.assertRegex(payload["order_code"], r"^TGS[0-9A-F]{12}$")
        self.assertEqual(headers["Authorization"], "Bearer test-token")
        row, = self.orders()
        self.assertEqual((row["product_id"], row["amount"], row["payment_method"], row["currency"], row["quantity"], row["status"]),
                         (PRO, 5.0, "binance_pay", "USDT", 1, "pending"))
        text = render.await_args.args[1]
        for expected in ("5.00", RECEIVER, row["order_id"], "ID lệnh", "ĐÚNG số tiền"):
            self.assertIn(expected, text)
        for banned in ("lẻ", "phần lẻ", "0.01", "hậu tố", "BEP20", "ví"):
            self.assertNotIn(banned, text)
        self.assertIn(f"bpid_{row['order_id']}", datas(render.await_args.kwargs["reply_markup"]))

    async def test_same_message_same_order_id_and_never_collides_with_other_methods(self):
        self.ok()
        await self.checkout(PLUS, 1, 42)
        await self.checkout(PLUS, 1, 42)
        self.assertEqual(len(self.orders()), 1)
        self.assertEqual(self.web.requests[0][1]["order_code"], self.web.requests[1][1]["order_code"])
        import hashlib
        others = {"TGS" + hashlib.sha256(f"123:1:42:1{suffix}".encode()).hexdigest()[:12].upper() for suffix in ("", ":bep20")}
        self.assertNotIn(self.web.requests[0][1]["order_code"], others)

    async def test_invalid_or_tampered_web_responses_never_create_an_order(self):
        bad = [dict(network="BSC (BEP20)"), dict(token="USDC"), dict(receiver_id="abc"), dict(receiver_id=None), dict(receiver_id="12"),
               dict(amount_usdt="5.01"), dict(amount_usdt="4.99"), dict(amount_usdt="5.1"), dict(amount_usdt="0.00"), dict(amount_usdt=5.0),
               dict(success=False), dict(order_code="TGSOTHER00000"), dict(qty=2), dict(expires_at=None), dict(expires_at="not a date")]
        for index, overrides in enumerate(bad):
            self.web.reply = lambda path, payload, o=overrides: (200, web_response(payload["order_code"], **o))
            await self.checkout(PLUS, 1, message_id=1000 + index)
            self.assertEqual(self.orders(), [], overrides)

    async def test_amount_must_equal_the_bot_price_times_quantity(self):
        self.web.reply = lambda path, payload: (200, web_response(payload["order_code"], qty=3, amount_usdt="15.00"))
        await self.checkout(PLUS, 3, 77)
        self.assertEqual(len(self.orders()), 1)
        self.web.reply = lambda path, payload: (200, web_response(payload["order_code"], qty=3, amount_usdt="15.01"))
        await self.checkout(PLUS, 3, 78)
        self.assertEqual(len(self.orders()), 1)

    async def test_web_errors_and_network_failures_show_a_safe_message_only(self):
        for status, body in ((409, {"error": "out_of_stock"}), (503, {"error": "binance_pay_disabled"}), (429, {"error": "too_many_pending_orders"}),
                             (401, {"error": "Unauthorized"}), (500, {"error": "<script>alert(1)</script>"})):
            self.web.reply = lambda path, payload, s=status, b=body: (s, b)
            _, render = await self.checkout(PLUS, 1, message_id=2000 + status)
            self.assertEqual(self.orders(), [])
            self.assertNotIn("<script>", render.await_args.args[1])
        with patch("urllib.request.urlopen", side_effect=OSError("boom")):
            _, render = await self.checkout(PLUS, 1, message_id=3000)
        self.assertEqual(self.orders(), [])
        self.assertIn("Chưa thể tạo hóa đơn", render.await_args.args[1])

    async def test_out_of_range_quantity_or_unknown_sku_do_not_call_the_web(self):
        for qty, sku in ((0, PLUS), (51, PLUS), (1, "nope_sku"), (2, PRO)):
            await self.checkout(sku, qty, message_id=4000 + qty)
        self.assertEqual(self.web.requests, [])

    # ---------- id prompt ----------
    async def make_order(self, uid=123):
        self.ok()
        await self.checkout(PLUS, 1, 42)
        return self.orders()[0]["order_id"]

    async def test_prompt_waits_for_an_id_only_for_the_owner_of_a_pending_binance_order(self):
        order_id = await self.make_order()
        q = make_query("x")
        await shop_bot.prompt_binance_id(q, user_obj(999), order_id)               # someone else
        self.assertEqual(shop_bot.AWAITING_BINANCE_ID, {})
        await shop_bot.prompt_binance_id(q, user_obj(), "TGSNOSUCH0001")
        self.assertEqual(shop_bot.AWAITING_BINANCE_ID, {})
        await shop_bot.prompt_binance_id(q, user_obj(), "not-an-order")
        self.assertEqual(shop_bot.AWAITING_BINANCE_ID, {})
        await shop_bot.prompt_binance_id(q, user_obj(), order_id)
        self.assertEqual(shop_bot.AWAITING_BINANCE_ID[123]["order_id"], order_id)
        self.assertIn(ORDER_ID_TEXT, q.message.reply_text.await_args.args[0])

    async def test_prompt_refuses_orders_of_other_payment_methods_and_orders_no_longer_pending(self):
        order_id = await self.make_order()
        conn = shop_bot.get_db_connection()
        try:
            conn.execute("UPDATE orders SET payment_method='vietqr' WHERE order_id=?", (order_id,))
            conn.commit()
            q = make_query("x")
            await shop_bot.prompt_binance_id(q, user_obj(), order_id)
            self.assertEqual(shop_bot.AWAITING_BINANCE_ID, {})
            conn.execute("UPDATE orders SET payment_method='binance_pay', status='cancelled' WHERE order_id=?", (order_id,))
            conn.commit()
            await shop_bot.prompt_binance_id(q, user_obj(), order_id)
            self.assertEqual(shop_bot.AWAITING_BINANCE_ID, {})
            conn.execute("UPDATE orders SET status='completed' WHERE order_id=?", (order_id,))
            conn.commit()
            await shop_bot.prompt_binance_id(q, user_obj(), order_id)
            self.assertEqual(shop_bot.AWAITING_BINANCE_ID, {})
        finally:
            conn.close()

    # ---------- confirm ----------
    async def paste(self, text, reply, uid=123):
        update = make_update(text, uid)
        self.web.reply = lambda path, payload: reply(path, payload)
        context = SimpleNamespace(bot=object())
        with patch.object(shop_bot, "send_paid_order_delivery", new_callable=AsyncMock, return_value=True) as deliver:
            await shop_bot.handle_user_text_message(update, context)
        return update, deliver

    async def test_paid_delivers_exactly_once_and_stops_waiting(self):
        order_id = await self.make_order()
        await shop_bot.prompt_binance_id(make_query("x"), user_obj(), order_id)
        update, deliver = await self.paste(ORDER_ID_TEXT, lambda p, payload: (200, {"success": True, "status": "paid"}))
        (path, payload, headers), = self.web.requests[-1:]
        self.assertEqual(path, "/api/tg/orders/binance-pay/confirm")
        self.assertEqual(payload, {"order_code": order_id, "tg_user_id": 123, "pasted_id": ORDER_ID_TEXT})
        self.assertEqual(headers["Authorization"], "Bearer test-token")
        deliver.assert_awaited_once()
        self.assertEqual(deliver.await_args.args[1:], (order_id, 123))
        self.assertEqual(shop_bot.AWAITING_BINANCE_ID, {})

    async def test_every_refusal_gets_a_safe_message_and_never_delivers(self):
        order_id = await self.make_order()
        cases = [
            ((200, {"success": False, "status": "pending", "reason": "not_found"}), "Chưa tìm thấy"),
            ((200, {"success": False, "status": "pending", "reason": "amount_mismatch"}), "số tiền khác"),
            ((200, {"success": False, "status": "pending", "reason": "invalid_transaction"}), "không hợp lệ"),
            ((200, {"success": False, "status": "pending", "reason": "before_order"}), "trước khi tạo đơn"),
            ((200, {"success": False, "status": "pending", "reason": "already_used"}), "đã được dùng"),
            ((200, {"success": False, "status": "pending", "reason": "<script>"}), "Không thể xác nhận"),
            ((200, {"success": True, "status": "pending"}), "Không thể xác nhận"),
            ((429, {"error": "too_fast"}), "10 giây"),
            ((429, {"error": "too_many_attempts"}), "quá số lần"),
            ((503, {"error": "temporarily_unavailable"}), "Tạm thời chưa kiểm tra"),
            ((503, {"error": "can_cau_hinh"}), "cấu hình"),
            ((503, {"error": "binance_pay_disabled"}), "Tạm thời chưa kiểm tra"),
            ((500, {}), "Tạm thời chưa kiểm tra"),
            ((401, {"error": "Unauthorized"}), "Tạm thời chưa kiểm tra"),
            ((200, {"success": "yes", "status": "paid"}), "Tạm thời chưa kiểm tra"),
            ((200, {"status": "paid"}), "Tạm thời chưa kiểm tra"),
        ]
        for reply, expected in cases:
            await shop_bot.prompt_binance_id(make_query("x"), user_obj(), order_id)
            update, deliver = await self.paste(ORDER_ID_TEXT, lambda p, payload, r=reply: r)
            deliver.assert_not_awaited()
            sent = update.message.reply_text.await_args.args[0]
            self.assertIn(expected, sent, reply)
            self.assertNotIn("<script>", sent)
        # a network failure is also "cannot check", never paid
        await shop_bot.prompt_binance_id(make_query("x"), user_obj(), order_id)
        update = make_update(ORDER_ID_TEXT)
        with patch("urllib.request.urlopen", side_effect=OSError("down")), \
             patch.object(shop_bot, "send_paid_order_delivery", new_callable=AsyncMock) as deliver:
            await shop_bot.handle_user_text_message(update, SimpleNamespace(bot=object()))
        deliver.assert_not_awaited()
        self.assertIn("Tạm thời chưa kiểm tra", update.message.reply_text.await_args.args[0])

    async def test_needs_review_expired_and_cancelled_tell_the_customer_to_contact_admin(self):
        order_id = await self.make_order()
        for status, expected in (("needs_review", "admin kiểm tra"), ("expired", "hết hạn"), ("cancelled", "hết hạn")):
            await shop_bot.prompt_binance_id(make_query("x"), user_obj(), order_id)
            update, deliver = await self.paste(ORDER_ID_TEXT, lambda p, payload, s=status: (200, {"success": True, "status": s}))
            deliver.assert_not_awaited()
            self.assertIn(expected, update.message.reply_text.await_args.args[0])
            self.assertEqual(shop_bot.AWAITING_BINANCE_ID, {})

    async def test_malformed_ids_never_reach_the_web_and_waiting_stops_after_three_bad_inputs(self):
        order_id = await self.make_order()
        await shop_bot.prompt_binance_id(make_query("x"), user_obj(), order_id)
        before = len(self.web.requests)
        for bad in ("hello", "12345", "4533 4318", "45334318727175372a", "1" * 40):
            update = make_update(bad)
            await shop_bot.handle_user_text_message(update, SimpleNamespace(bot=object()))
            if 123 not in shop_bot.AWAITING_BINANCE_ID:
                break
        self.assertEqual(len(self.web.requests), before)
        self.assertNotIn(123, shop_bot.AWAITING_BINANCE_ID)

    async def test_waiting_expires_and_other_users_text_is_not_treated_as_an_id(self):
        order_id = await self.make_order()
        await shop_bot.prompt_binance_id(make_query("x"), user_obj(), order_id)
        before = len(self.web.requests)
        update = make_update(ORDER_ID_TEXT, uid=555)                                # another user pastes digits
        with patch.object(shop_bot, "handle_binance_id_message", new_callable=AsyncMock) as handler:
            await shop_bot.handle_user_text_message(update, SimpleNamespace(bot=object()))
        handler.assert_not_awaited()
        shop_bot.AWAITING_BINANCE_ID[123]["until"] = time.monotonic() - 1
        update = make_update(ORDER_ID_TEXT)
        await shop_bot.handle_user_text_message(update, SimpleNamespace(bot=object()))
        self.assertEqual(len(self.web.requests), before)
        self.assertNotIn(123, shop_bot.AWAITING_BINANCE_ID)

    async def test_when_disabled_a_pending_wait_is_ignored_and_nothing_is_sent(self):
        order_id = await self.make_order()
        await shop_bot.prompt_binance_id(make_query("x"), user_obj(), order_id)
        self.set_flag("0")
        before = len(self.web.requests)
        await shop_bot.handle_user_text_message(make_update(ORDER_ID_TEXT), SimpleNamespace(bot=object()))
        self.assertEqual(len(self.web.requests), before)

    async def test_paid_but_delivery_failure_tells_the_customer_to_contact_admin(self):
        order_id = await self.make_order()
        await shop_bot.prompt_binance_id(make_query("x"), user_obj(), order_id)
        update = make_update(ORDER_ID_TEXT)
        self.web.reply = lambda p, payload: (200, {"success": True, "status": "paid"})
        with patch.object(shop_bot, "send_paid_order_delivery", new_callable=AsyncMock, return_value=False):
            await shop_bot.handle_user_text_message(update, SimpleNamespace(bot=object()))
        self.assertIn("@wolfmodyt", update.message.reply_text.await_args.args[0])

    def test_no_binance_id_or_secret_in_the_bot_source(self):
        source = Path(shop_bot.__file__).read_text(encoding="utf8")
        self.assertNotIn("1158594960", source)
        self.assertNotIn("BINANCE_PAY_READ_API", source)
        self.assertNotIn("api.binance.com", source)


if __name__ == "__main__":
    unittest.main()
