"""ChatGPT Go 3m: one offer link per order from stock, idempotent delivery, sold out blocks purchase (fake stock, no network)."""
import asyncio
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import shop_bot

GPT = "chatgpt_go_3m"
USER = 555


class FakeWebStock:
    """Mimics the web claim endpoint: atomic per order, same bundle on retries, None when empty."""

    def __init__(self, links):
        self.pool = list(links)
        self.claimed: dict[str, str] = {}
        self.lock = threading.Lock()
        self.calls = 0

    def claim(self, sku="x", order_id="", content=""):
        with self.lock:
            self.calls += 1
            if order_id in self.claimed:
                return self.claimed[order_id]
            if not self.pool:
                return None
            self.claimed[order_id] = self.pool.pop(0)
            return self.claimed[order_id]

    def count(self, sku="x"):
        with self.lock:
            return len(self.pool)


def item(in_stock=True, max_qty=1):
    return {"sku": GPT, "name": "CHATGPT GO 3 THÁNG", "description": "", "type": "item", "price_vnd": 100000,
            "price_usdt": None, "in_stock": in_stock, "max_qty": max_qty}


def make_query(data):
    message = SimpleNamespace(chat_id=1, message_id=2, photo=None, reply_photo=AsyncMock(), reply_text=AsyncMock(), delete=AsyncMock())
    return SimpleNamespace(data=data, answer=AsyncMock(), message=message, edit_message_text=AsyncMock())


class OfferDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        for target in (
            patch.multiple(shop_bot, DATA_DIR=self.directory.name, DB_PATH=str(Path(self.directory.name) / "shop_database.db"),
                           CLAIMED_LINKS_FILE=str(Path(self.directory.name) / "claimed_links.txt")),
            patch("urllib.request.urlopen", side_effect=OSError("network disabled in tests")),
            patch.object(shop_bot, "sync_web_product_price", return_value=True),
            patch.object(shop_bot, "_catalogue", {"items": None, "next": 0.0}),
        ):
            target.start()
            self.addCleanup(target.stop)
        shop_bot._web_stock_cache.clear()
        with patch.dict(os.environ, {"USDT_WALLET": ""}):
            shop_bot.init_database()
        shop_bot.set_user_lang(USER, "en")

    def fake_stock(self, links):
        fake = FakeWebStock(links)
        for target in (patch.object(shop_bot, "claim_stock_from_web", side_effect=fake.claim),
                       patch.object(shop_bot, "get_stock_count", side_effect=fake.count)):
            target.start()
            self.addCleanup(target.stop)
        return fake

    def add_order(self, order_id, user=USER, qty=1):
        conn = shop_bot.get_db_connection()
        conn.execute("INSERT INTO orders (order_id,user_id,product_id,amount,status,quantity,payment_method) VALUES (?,?,?,?,?,?,?)",
                     (order_id, user, GPT, 100000 * qty, "pending", qty, "vietqr"))
        conn.commit()
        conn.close()

    def order(self, order_id):
        conn = shop_bot.get_db_connection()
        row = conn.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        conn.close()
        return row

    def test_one_link_per_order_and_repeated_delivery_returns_the_same_link(self):
        fake = self.fake_stock(["https://chatgpt.com/offer/AAA", "https://chatgpt.com/offer/BBB"])
        self.add_order("TGSONE")
        first = shop_bot.deliver_product("TGSONE", USER)
        again = shop_bot.deliver_product("TGSONE", USER)
        self.assertEqual(first, ["https://chatgpt.com/offer/AAA"])
        self.assertEqual(again, first)                       # pressing again / duplicate webhook: same link
        self.assertEqual(fake.pool, ["https://chatgpt.com/offer/BBB"])   # only ONE link left the stock
        row = self.order("TGSONE")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["item_delivered"], "https://chatgpt.com/offer/AAA")

    def test_another_users_order_cannot_claim_the_link(self):
        fake = self.fake_stock(["https://chatgpt.com/offer/AAA"])
        self.add_order("TGSOWN")
        self.assertIsNone(shop_bot.deliver_product("TGSOWN", 999))
        self.assertEqual(len(fake.pool), 1)

    async def test_parallel_orders_never_receive_the_same_link_and_stock_runs_out(self):
        links = [f"https://chatgpt.com/offer/L{i}" for i in range(3)]
        fake = self.fake_stock(links)
        ids = [f"TGSP{i}" for i in range(6)]
        for oid in ids:
            self.add_order(oid)
        results = await asyncio.gather(*[asyncio.to_thread(shop_bot.deliver_product, oid, USER) for oid in ids])
        got = [r[0] for r in results if r]
        self.assertEqual(len(got), 3)                        # only 3 links existed
        self.assertEqual(sorted(got), sorted(links))
        self.assertEqual(len(set(got)), 3)                   # no link twice
        self.assertEqual(sum(1 for r in results if r is None), 3)   # the rest get nothing (retry later / support)
        self.assertEqual(fake.pool, [])

    async def test_same_order_delivered_concurrently_consumes_exactly_one_link(self):
        fake = self.fake_stock([f"https://chatgpt.com/offer/L{i}" for i in range(5)])
        self.add_order("TGSRACE")
        results = await asyncio.gather(*[asyncio.to_thread(shop_bot.deliver_product, "TGSRACE", USER) for _ in range(8)])
        flat = {tuple(r) for r in results if r}
        self.assertEqual(len(flat), 1)                       # every caller sees the same single link
        self.assertEqual(len(next(iter(flat))), 1)
        self.assertEqual(len(fake.pool), 4)
        self.assertEqual(self.order("TGSRACE")["item_delivered"].count("\n"), 0)

    async def test_duplicate_payment_webhook_sends_one_telegram_message(self):
        self.fake_stock(["https://chatgpt.com/offer/AAA", "https://chatgpt.com/offer/BBB"])
        self.add_order("TGSHOOK")
        bot = SimpleNamespace(send_message=AsyncMock())
        self.assertTrue(await shop_bot.send_paid_order_delivery(bot, "TGSHOOK", USER))
        self.assertTrue(await shop_bot.send_paid_order_delivery(bot, "TGSHOOK", USER))
        self.assertEqual(bot.send_message.await_count, 1)
        kwargs = bot.send_message.await_args.kwargs
        self.assertIn("https://chatgpt.com/offer/AAA", kwargs["text"])
        self.assertNotIn("BBB", kwargs["text"])
        urls = [b.url for row in kwargs["reply_markup"].inline_keyboard for b in row if b.url]
        self.assertEqual(urls, ["https://chatgpt.com/offer/AAA"])

    def test_offer_link_message_has_instructions_in_both_languages(self):
        vi, _ = shop_bot.format_delivered_links_message("TGS1", ["https://chatgpt.com/offer/AAA"], GPT, "vi")
        en, buttons = shop_bot.format_delivered_links_message("TGS1", ["https://chatgpt.com/offer/AAA"], GPT, "en")
        self.assertIn("Offer link của bạn (1)", vi)
        self.assertIn("Đăng nhập tài khoản ChatGPT", vi)
        self.assertIn("chỉ dùng được một lần", vi)
        self.assertIn("Your offer link (1)", en)
        self.assertIn("Sign in to your ChatGPT account", en)
        self.assertIn("works only once", en)
        self.assertIn("Bảo hành 1 đổi 1", vi)
        self.assertIn("1-for-1 replacement warranty", en)
        self.assertEqual(buttons[0][0].url, "https://chatgpt.com/offer/AAA")
        # a non-URL payload never produces a url button (Telegram would reject it)
        _, odd = shop_bot.format_delivered_links_message("TGS2", ["not a url"], GPT, "en")
        self.assertFalse([b for row in odd for b in row if b.url])

    async def test_sold_out_blocks_purchase_and_order_creation(self):
        fake = self.fake_stock([])
        with patch.object(shop_bot, "_fetch_catalogue_payload", return_value={"success": True, "products": [item(False)]}):
            shop_bot.refresh_catalogue(force=True)
            for data in (f"sq:{GPT}:1", f"cv:{GPT}:1"):
                query = make_query(data)
                with patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock):
                    await shop_bot.callback_handler(SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(
                        id=USER, username="u", first_name="M")), SimpleNamespace())
        conn = shop_bot.get_db_connection()
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders WHERE product_id=?", (GPT,)).fetchone()[0], 0)
        conn.close()
        self.assertEqual(fake.calls, 0)                      # nothing was ever claimed

    async def test_last_link_can_be_bought_once_then_it_is_sold_out(self):
        fake = self.fake_stock(["https://chatgpt.com/offer/LAST"])
        with patch.object(shop_bot, "_fetch_catalogue_payload", return_value={"success": True, "products": [item()]}):
            shop_bot.refresh_catalogue(force=True)
            self.assertTrue(shop_bot.sku_is_in_stock(GPT) or fake.count() == 1)
            self.add_order("TGSLAST")
            self.assertEqual(shop_bot.deliver_product("TGSLAST", USER), ["https://chatgpt.com/offer/LAST"])
            self.assertEqual(fake.count(), 0)
            query = make_query(f"sq:{GPT}:1")
            with patch.object(shop_bot, "safe_edit_or_reply", new_callable=AsyncMock) as render:
                await shop_bot.callback_handler(SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(
                    id=USER, username="u", first_name="M")), SimpleNamespace())
            render.assert_not_awaited()                      # blocked: no confirmation screen
            self.assertTrue(query.answer.await_args.kwargs.get("show_alert"))


if __name__ == "__main__":
    unittest.main()
