import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web

from utils.delivery import DeliveryWorker
from utils.wolfmod_api import WolfmodAPI, WolfmodError, sign_request

SECRET = "fixture-api-secret-at-least-32-characters"


class SignedClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.seen = []
        self.nonces = set()
        self.fail_first_order = False
        self.redirect = False
        self.sink_called = False

        async def handler(request):
            raw = await request.text()
            expected = sign_request(SECRET, request.method, request.raw_path, request.headers.get("X-Bot-Timestamp", ""), raw)
            if not hmac.compare_digest(request.headers.get("X-Bot-Signature", ""), expected):
                return web.json_response({"error": "unauthorized"}, status=401)
            nonce = request.query.get("nonce")
            if not nonce or nonce in self.nonces:
                return web.json_response({"error": "replay"}, status=401)
            self.nonces.add(nonce)
            self.assertNotIn("Authorization", request.headers)
            self.assertNotIn(SECRET, raw)
            self.seen.append((request.method, request.path, json.loads(raw) if raw else None))
            if self.redirect:
                raise web.HTTPFound("/sink")
            if request.path == "/api/bot/products":
                return web.json_response({"products": [{"sku": "gemini_plus", "type": "item", "price_vnd": 20000, "in_stock": True}]})
            if request.path == "/api/bot/orders" and request.method == "POST":
                if self.fail_first_order:
                    self.fail_first_order = False
                    return web.json_response({"error": "temporary"}, status=503)
                return web.json_response({"order": {"order_code": "TGSABC001", "amount_vnd": 20000}})
            return web.json_response({"success": True})

        async def sink(_request):
            self.sink_called = True
            return web.json_response({})

        app = web.Application()
        app.router.add_route("*", "/api/bot/{path:.*}", handler)
        app.router.add_get("/sink", sink)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        self.base = f"http://127.0.0.1:{self.site._server.sockets[0].getsockname()[1]}"

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def test_signed_reads_use_new_nonce_in_same_second(self):
        async with WolfmodAPI(self.base, SECRET) as shop:
            with patch("utils.wolfmod_api.time.time", return_value=1800000000):
                self.assertEqual((await shop.product())["price_vnd"], 20000)
                await shop.product()
        self.assertEqual(len(self.nonces), 2)

    async def test_server_owns_price_and_retry_preserves_order_idempotency_key(self):
        self.fail_first_order = True
        async with WolfmodAPI(self.base, SECRET) as shop:
            result = await shop.create_order(123, "fixture_user", "msg_123_42")
        self.assertEqual(result["order"]["amount_vnd"], 20000)
        self.assertEqual(len(self.seen), 2)
        for _, _, body in self.seen:
            self.assertEqual(body["client_request_id"], "msg_123_42")
            self.assertNotIn("price_vnd", body)
            self.assertNotIn("amount_vnd", body)

    async def test_wrong_secret_rejected_without_exposing_it(self):
        async with WolfmodAPI(self.base, "wrong-secret") as shop:
            with self.assertRaises(WolfmodError) as caught:
                await shop.product()
        self.assertEqual(caught.exception.status, 401)
        self.assertNotIn("wrong-secret", str(caught.exception))

    async def test_redirect_is_not_followed(self):
        self.redirect = True
        async with WolfmodAPI(self.base, SECRET) as shop:
            with self.assertRaises(WolfmodError) as caught:
                await shop.product()
        self.assertEqual(caught.exception.status, 302)
        self.assertFalse(self.sink_called)

    async def test_unicode_body_is_signed_as_exact_utf8_bytes(self):
        async with WolfmodAPI(self.base, SECRET) as shop:
            await shop.request("POST", "/api/bot/events", {"label": "Tiếng Việt"})
        self.assertEqual(self.seen[0][2]["label"], "Tiếng Việt")


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.delivery = {
            "order_code": "TGSABC001", "tg_user_id": 123, "product_name": "Fixture",
            "lease_token": "fixture-lease", "items": [{"content": "https://fixture.test/one"}],
        }
        self.shop = SimpleNamespace(next_delivery=AsyncMock(side_effect=[self.delivery, None]), acknowledge=AsyncMock())
        self.bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=42)), send_document=AsyncMock())
        self.worker = DeliveryWorker(self.shop)

    async def test_receipt_only_after_successful_telegram_send(self):
        self.bot.send_message.side_effect = RuntimeError("Telegram unavailable")
        with self.assertRaises(RuntimeError):
            await self.worker.run_once(self.bot)
        self.shop.acknowledge.assert_not_awaited()

    async def test_ack_failure_retries_receipt_without_sending_link_again(self):
        self.shop.acknowledge.side_effect = [WolfmodError(503), {}]
        with self.assertRaises(WolfmodError):
            await self.worker.run_once(self.bot)
        await self.worker.run_once(self.bot)
        self.bot.send_message.assert_awaited_once()
        self.assertEqual(self.shop.acknowledge.await_count, 2)
        self.assertEqual(self.worker.receipts, {})
        self.shop.acknowledge.assert_awaited_with("TGSABC001", "fixture-lease", 42)

    async def test_long_delivery_uses_private_document(self):
        self.delivery["items"] = [{"content": "https://fixture.test/" + "a" * 5000}]
        self.bot.send_document.return_value = SimpleNamespace(message_id=43)
        await self.worker.run_once(self.bot)
        self.bot.send_message.assert_not_awaited()
        self.bot.send_document.assert_awaited_once()
        self.shop.acknowledge.assert_awaited_once_with("TGSABC001", "fixture-lease", 43)

    async def test_group_callback_cannot_read_or_create_private_orders(self):
        from handlers.user_handlers import on_check, on_buy
        callback = SimpleNamespace(
            message=SimpleNamespace(chat=SimpleNamespace(id=-1001, type="supergroup")),
            from_user=SimpleNamespace(id=123), answer=AsyncMock(), data="check:TGSABC001",
        )
        shop = SimpleNamespace(order=AsyncMock(), create_order=AsyncMock())
        await on_check(callback, self.bot, shop, self.worker)
        await on_buy(callback, self.bot, shop)
        shop.order.assert_not_awaited()
        shop.create_order.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
