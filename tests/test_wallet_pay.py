"""Task 058 PR 3: pay with the wallet + refunds (offline: the web is simulated, nothing is called for real)."""
import asyncio
import os
import tempfile
import threading
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import shop_bot
import wallet_pay as wp
import wallet_store as ws

BUNDLE = "https://example.test/link-1"


class FakeWeb:
    """Stands in for the web: mark_paid / claim / finish are what shop_bot would call."""
    def __init__(self, paid=wp.WEB_OK, claim=(wp.WEB_OK, BUNDLE), conn_factory=None):
        self.paid, self.claim_result, self.calls = paid, claim, []
        self.conn_factory = conn_factory

    def ops(self):
        return wp.WebOps(mark_paid=self._paid, claim=self._claim, finish=self._finish)

    def _paid(self, order):
        self.calls.append("paid")
        return self.paid

    def _claim(self, sku, order_id):
        self.calls.append("claim")
        return self.claim_result

    def _finish(self, order_id, user_id, bundle):
        self.calls.append("finish")
        return shop_bot.deliver_product(order_id, user_id, bundle=bundle)


class Base(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = patch.multiple(shop_bot, DATA_DIR=self.directory.name, DB_PATH=str(Path(self.directory.name) / "shop.db"),
                                  CLAIMED_LINKS_FILE=str(Path(self.directory.name) / "c.txt"), BOT_TOKEN="test-token")
        settings.start()
        self.addCleanup(settings.stop)
        env = patch.dict(os.environ, {"WALLET_ENABLED": "1", "WALLET_PAY_ENABLED": "1", "USDT_WALLET": ""})
        env.start()
        self.addCleanup(env.stop)
        shop_bot.init_database()
        shop_bot.set_setting("default_lang", "vi")
        self.conn = shop_bot.get_db_connection()
        self.addCleanup(self.conn.close)

    def fund(self, user=5, amount=200_000, ref=None):
        ws.credit_topup(self.conn, user, amount, ref or f"TOP{user}{amount}")

    def begin(self, user=5, qty=1, price=50_000, sku="gemini_plus", now=None):
        return wp.begin_payment(self.conn, user, "u", sku, qty, price, now=now)

    def row(self, order_id):
        return self.conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()

    def balance(self, user=5):
        return ws.get_balance(self.conn, user)

    def ledger(self, kind=None):
        sql = "SELECT COUNT(*) FROM wallet_ledger" + (" WHERE kind = ?" if kind else "")
        return self.conn.execute(sql, (kind,) if kind else ()).fetchone()[0]


class BeginPaymentTests(Base):
    def test_enough_balance_debits_once_and_creates_processing_order(self):
        self.fund()
        order = self.begin(qty=2, price=50_000)
        self.assertEqual((order.amount_vnd, order.balance_vnd, self.balance()), (100_000, 100_000, 100_000))
        row = self.row(order.order_id)
        self.assertEqual((row["status"], row["payment_method"], row["kind"], row["amount"], row["quantity"]),
                         ("wallet_processing", "wallet", "purchase", 100_000, 2))
        self.assertEqual(self.ledger("purchase"), 1)
        self.assertEqual(ws.audit(self.conn), [])

    def test_not_enough_balance_changes_nothing(self):
        self.fund(amount=20_000)
        with self.assertRaises(wp.PayError) as caught:
            self.begin(price=50_000)
        self.assertEqual(caught.exception.code, "insufficient_funds")
        self.assertEqual(self.balance(), 20_000)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)
        self.assertEqual(self.ledger("purchase"), 0)

    def test_no_wallet_at_all_is_insufficient_and_creates_no_account(self):
        with self.assertRaises(wp.PayError) as caught:
            self.begin(user=77)
        self.assertEqual(caught.exception.code, "insufficient_funds")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM wallet_accounts WHERE user_id = 77").fetchone()[0], 0)

    def test_exact_balance_goes_to_zero_never_negative(self):
        self.fund(amount=50_000)
        self.begin(price=50_000)
        self.assertEqual(self.balance(), 0)
        with self.assertRaises(wp.PayError):
            self.begin(price=20_000)
        self.assertEqual(self.balance(), 0)

    def test_double_tap_creates_one_order_and_debits_once(self):
        self.fund()
        self.begin()
        with self.assertRaises(wp.PayError) as caught:
            self.begin()
        self.assertEqual(caught.exception.code, "in_flight")
        self.assertEqual(self.balance(), 150_000)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 1)
        self.assertEqual(self.ledger("purchase"), 1)

    def test_parallel_taps_only_one_succeeds(self):
        self.fund(amount=1_000_000)
        results = []
        barrier = threading.Barrier(6)

        def tap():
            conn = shop_bot.get_db_connection()
            try:
                barrier.wait()
                results.append(wp.begin_payment(conn, 5, "u", "gemini_plus", 1, 50_000).order_id)
            except wp.PayError as error:
                results.append(error.code)
            finally:
                conn.close()

        threads = [threading.Thread(target=tap) for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sum(1 for r in results if r.startswith("TGS")), 1, results)
        self.assertEqual(self.balance(), 950_000)
        self.assertEqual(self.ledger("purchase"), 1)
        self.assertEqual(ws.audit(self.conn), [])

    def test_two_customers_can_pay_at_the_same_time(self):
        self.fund(5)
        self.fund(6)
        self.begin(user=5)
        self.begin(user=6)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'wallet_processing'").fetchone()[0], 2)

    def test_invalid_requests_refused(self):
        self.fund()
        for args in ((5, "u", "gemini_plus", 0, 50_000), (5, "u", "gemini_plus", 1, 0), (5, "u", "", 1, 50_000),
                     (True, "u", "gemini_plus", 1, 50_000), (5, "u", "gemini_plus", 1, 50_000.5)):
            with self.assertRaises(wp.PayError) as caught:
                wp.begin_payment(self.conn, *args)
            self.assertEqual(caught.exception.code, "invalid_request")
        self.assertEqual(self.balance(), 200_000)

    def test_new_order_can_start_after_previous_finished(self):
        self.fund()
        first = self.begin()
        wp.process_order(self.conn, first.order_id, FakeWeb().ops())
        second = self.begin()
        self.assertNotEqual(first.order_id, second.order_id)


class ProcessOrderTests(Base):
    def test_success_completes_order_and_stores_links(self):
        self.fund()
        order = self.begin()
        web = FakeWeb()
        result = wp.process_order(self.conn, order.order_id, web.ops())
        self.assertEqual((result.state, result.links), ("completed", (BUNDLE,)))
        row = self.row(order.order_id)
        self.assertEqual((row["status"], row["item_delivered"]), ("completed", BUNDLE))
        self.assertEqual(web.calls, ["paid", "claim", "finish"])
        self.assertEqual(self.balance(), 150_000)
        self.assertEqual(self.ledger("refund"), 0)

    def test_repeat_after_completion_does_not_call_web_again(self):
        self.fund()
        order = self.begin()
        wp.process_order(self.conn, order.order_id, FakeWeb().ops())
        again = FakeWeb()
        result = wp.process_order(self.conn, order.order_id, again.ops())
        self.assertEqual((result.state, again.calls), ("completed", []))

    def test_restart_between_debit_and_delivery_delivers_once_without_losing_money(self):
        self.fund()
        order = self.begin()              # phase 1 done, then the process "dies": phase 2 never ran
        self.assertEqual(wp.processing_orders(self.conn), [order.order_id])
        fresh = shop_bot.get_db_connection()      # the restarted bot: new connection, startup sweep
        self.addCleanup(fresh.close)
        web = FakeWeb()
        for order_id in wp.processing_orders(fresh):
            wp.process_order(fresh, order_id, web.ops())
        wp.process_order(fresh, order.order_id, web.ops())     # a second sweep / the customer's button
        self.assertEqual(web.calls.count("claim"), 1)
        self.assertEqual((self.row(order.order_id)["status"], self.balance()), ("completed", 150_000))
        self.assertEqual((self.ledger("purchase"), self.ledger("refund")), (1, 0))
        self.assertEqual(wp.processing_orders(fresh), [])

    def test_failure_right_after_debit_is_recoverable(self):
        self.fund()
        order = self.begin()

        def boom(_order):
            raise RuntimeError("process killed")

        with self.assertRaises(RuntimeError):
            wp.process_order(self.conn, order.order_id, wp.WebOps(mark_paid=boom, claim=None, finish=None))
        self.assertEqual(self.row(order.order_id)["status"], "wallet_processing")
        self.assertEqual(self.balance(), 150_000)
        self.assertEqual(wp.process_order(self.conn, order.order_id, FakeWeb().ops()).state, "completed")

    def test_sold_out_refunds_exactly_once(self):
        self.fund()
        order = self.begin(qty=2)
        web = FakeWeb(claim=(wp.WEB_SOLD_OUT, None))
        result = wp.process_order(self.conn, order.order_id, web.ops())
        self.assertEqual((result.state, result.amount_vnd, result.balance_vnd), ("refunded", 100_000, 200_000))
        row = self.row(order.order_id)
        self.assertEqual((row["status"], row["review_reason"]), ("cancelled", "wallet_refund"))
        again = wp.process_order(self.conn, order.order_id, web.ops())
        self.assertEqual(again.state, "closed")
        self.assertEqual((self.balance(), self.ledger("refund")), (200_000, 1))
        self.assertEqual(ws.audit(self.conn), [])

    def test_web_refusing_the_payment_refunds(self):
        self.fund()
        order = self.begin()
        result = wp.process_order(self.conn, order.order_id, FakeWeb(paid=wp.WEB_REFUSED).ops())
        self.assertEqual((result.state, self.balance()), ("refunded", 200_000))

    def test_refund_function_called_twice_credits_once(self):
        self.fund()
        order = self.begin()
        row = self.row(order.order_id)
        wp._refund(self.conn, row, wp.REVIEW_REFUND)
        wp._refund(self.conn, row, wp.REVIEW_REFUND)
        self.assertEqual((self.balance(), self.ledger("refund")), (200_000, 1))

    def test_timeout_does_not_refund_early(self):
        self.fund()
        now = datetime(2026, 1, 1, 12, 0, 0)
        order = self.begin(now=now)
        web = FakeWeb(paid=wp.WEB_UNCERTAIN)
        result = wp.process_order(self.conn, order.order_id, web.ops(), now=now + timedelta(minutes=14, seconds=59))
        self.assertEqual(result.state, "processing")
        self.assertEqual((self.row(order.order_id)["status"], self.balance(), self.ledger("refund")), ("wallet_processing", 150_000, 0))

    def test_claim_uncertain_does_not_refund_early_either(self):
        self.fund()
        now = datetime(2026, 1, 1, 12, 0, 0)
        order = self.begin(now=now)
        result = wp.process_order(self.conn, order.order_id, FakeWeb(claim=(wp.WEB_UNCERTAIN, None)).ops(), now=now + timedelta(minutes=3))
        self.assertEqual((result.state, self.ledger("refund")), ("processing", 0))

    def test_timeout_refunds_after_15_minutes_and_flags_review(self):
        self.fund()
        now = datetime(2026, 1, 1, 12, 0, 0)
        order = self.begin(now=now)
        web = FakeWeb(paid=wp.WEB_UNCERTAIN)
        result = wp.process_order(self.conn, order.order_id, web.ops(), now=now + timedelta(minutes=15))
        self.assertEqual((result.state, result.balance_vnd), ("refunded_review", 200_000))
        row = self.row(order.order_id)
        self.assertEqual((row["status"], row["review_reason"]), ("cancelled", "wallet_refund_needs_review"))
        wp.process_order(self.conn, order.order_id, web.ops(), now=now + timedelta(minutes=40))
        self.assertEqual(self.ledger("refund"), 1)

    def test_retry_after_timeout_can_still_succeed(self):
        self.fund()
        now = datetime(2026, 1, 1, 12, 0, 0)
        order = self.begin(now=now)
        wp.process_order(self.conn, order.order_id, FakeWeb(paid=wp.WEB_UNCERTAIN).ops(), now=now + timedelta(minutes=2))
        result = wp.process_order(self.conn, order.order_id, FakeWeb().ops(), now=now + timedelta(minutes=4))
        self.assertEqual((result.state, self.ledger("refund")), ("completed", 0))

    def test_wrong_bundle_size_is_not_delivered_or_refunded_early(self):
        self.fund()
        now = datetime(2026, 1, 1, 12, 0, 0)
        order = self.begin(qty=2, now=now)
        web = FakeWeb(claim=(wp.WEB_OK, "only-one-link"))
        result = wp.process_order(self.conn, order.order_id, web.ops(), now=now + timedelta(minutes=1))
        self.assertEqual(result.state, "processing")
        self.assertIsNone(self.row(order.order_id)["item_delivered"])

    def test_non_wallet_order_is_left_alone(self):
        self.conn.execute("INSERT INTO orders (order_id, user_id, product_id, amount, status, payment_method) "
                          "VALUES ('TGSABCD1', 5, 'gemini_plus', 1000, 'pending', 'vietqr')")
        self.conn.commit()
        web = FakeWeb()
        self.assertEqual(wp.process_order(self.conn, "TGSABCD1", web.ops()).state, "not_wallet")
        self.assertEqual(wp.process_order(self.conn, "TGSNONE0", web.ops()).state, "not_found")
        self.assertEqual(web.calls, [])


class DeliverProductGuardTests(Base):
    def test_wallet_order_is_never_delivered_by_the_generic_path(self):
        self.fund()
        order = self.begin()
        with patch.object(shop_bot, "claim_stock_from_web", return_value=BUNDLE) as claim:
            self.assertIsNone(shop_bot.deliver_product(order.order_id, 5))
        claim.assert_not_called()

    def test_refunded_wallet_order_is_never_delivered(self):
        self.fund()
        order = self.begin()
        wp.process_order(self.conn, order.order_id, FakeWeb(claim=(wp.WEB_SOLD_OUT, None)).ops())
        with patch.object(shop_bot, "claim_stock_from_web", return_value=BUNDLE):
            self.assertIsNone(shop_bot.deliver_product(order.order_id, 5, bundle=None))

    def test_auto_delivery_loop_query_skips_cancelled_wallet_orders(self):
        self.fund()
        order = self.begin()
        wp.process_order(self.conn, order.order_id, FakeWeb(claim=(wp.WEB_SOLD_OUT, None)).ops())

        async def one_pass():
            seen = []

            async def fake_sleep(_seconds):
                if _seconds == 15:
                    raise asyncio.CancelledError

            with patch.object(shop_bot, "check_web_order_status", side_effect=lambda oid: seen.append(oid) or {"status": "paid"}), \
                    patch.object(shop_bot.asyncio, "sleep", fake_sleep), \
                    patch.object(shop_bot, "send_paid_order_delivery", new=AsyncMock()) as send:
                with self.assertRaises(asyncio.CancelledError):
                    await shop_bot.auto_deliver_paid_orders_task(SimpleNamespace(bot=MagicMock()))
            return seen, send

        seen, send = asyncio.run(one_pass())
        self.assertNotIn(order.order_id, seen)
        send.assert_not_called()


class RealWebOpsTests(Base):
    """shop_bot's real WebOps with only the HTTP function replaced."""
    def make_order(self):
        self.fund()
        return self.row(self.begin().order_id)

    def test_mark_paid_ok_sends_sync_then_wallet_paid(self):
        order = self.make_order()
        calls = []

        def post(path, payload, timeout=20):
            calls.append(path)
            return 200, {"success": True}

        with patch.object(shop_bot, "_post_web_json", post):
            self.assertEqual(shop_bot._wallet_web_mark_paid(order), wp.WEB_OK)
        self.assertEqual(calls, ["/api/tg/orders/sync", "/api/tg/orders/wallet-paid"])

    def test_mark_paid_classification(self):
        order = self.make_order()
        cases = [((0, {}), wp.WEB_UNCERTAIN), ((503, {}), wp.WEB_UNCERTAIN), ((429, {}), wp.WEB_UNCERTAIN),
                 ((404, {}), wp.WEB_REFUSED), ((409, {"error": "x"}), wp.WEB_REFUSED), ((200, {"success": False}), wp.WEB_REFUSED)]
        for reply, expected in cases:
            with patch.object(shop_bot, "_post_web_json", lambda *a, **k: reply):
                self.assertEqual(shop_bot._wallet_web_mark_paid(order), expected, reply)
        with patch.object(shop_bot, "_post_web_json", side_effect=OSError("down")):
            self.assertEqual(shop_bot._wallet_web_mark_paid(order), wp.WEB_UNCERTAIN)

    def test_claim_classification(self):
        cases = [((200, {"success": True, "content": "a\nb"}), (wp.WEB_OK, "a\nb")),
                 ((409, {"error": "Insufficient stock for this paid order"}), (wp.WEB_SOLD_OUT, None)),
                 ((409, {"error": "Delivery requires administrator review"}), (wp.WEB_UNCERTAIN, None)),
                 ((500, {}), (wp.WEB_UNCERTAIN, None)), ((200, {"success": True}), (wp.WEB_UNCERTAIN, None))]
        for reply, expected in cases:
            with patch.object(shop_bot, "_post_web_json", lambda *a, **k: reply):
                self.assertEqual(shop_bot.claim_stock_detailed("gemini_plus", "TGSX"), expected, reply)
        with patch.object(shop_bot, "_post_web_json", side_effect=TimeoutError()):
            self.assertEqual(shop_bot.claim_stock_detailed("gemini_plus", "TGSX"), (wp.WEB_UNCERTAIN, None))


class FlowTests(Base):
    """Handlers with a simulated Telegram and a simulated web."""
    def setUp(self):
        super().setUp()
        self.user = SimpleNamespace(id=5, username="u", first_name="U")
        self.bot = MagicMock()
        self.bot.send_message = AsyncMock()
        self.context = SimpleNamespace(bot=self.bot)
        self.query = MagicMock()
        self.query.answer = AsyncMock()
        self.query.message = SimpleNamespace(chat=SimpleNamespace(type="private"), reply_text=AsyncMock())
        patches = [patch.object(shop_bot, "sync_web_product_price"), patch.object(shop_bot, "known_skus", return_value=["gemini_plus"]),
                   patch.object(shop_bot, "get_stock_count", return_value=5),
                   patch.object(shop_bot, "safe_edit_or_reply", new=AsyncMock()),
                   patch.object(shop_bot, "send_paid_order_delivery", new=AsyncMock(return_value=True))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.edit = shop_bot.safe_edit_or_reply
        self.send_delivery = shop_bot.send_paid_order_delivery
        self.fund()

    def run_pay(self, web=None, qty=1):
        web = web or FakeWeb()
        with patch.object(shop_bot, "wallet_web_ops", return_value=web.ops()):
            asyncio.run(shop_bot.handle_wallet_pay(self.query, self.context, self.user, "vi", "gemini_plus", qty))
        return web

    def orders(self):
        return self.conn.execute("SELECT * FROM orders").fetchall()

    def test_paying_delivers_and_debits(self):
        web = self.run_pay()
        self.assertEqual(web.calls, ["paid", "claim", "finish"])
        (order,) = self.orders()
        self.assertEqual((order["status"], order["payment_method"]), ("completed", "wallet"))
        self.assertEqual(self.balance(), 200_000 - int(shop_bot.get_product("gemini_plus")["price"]))
        self.send_delivery.assert_awaited_once()

    def test_not_enough_balance_alerts_and_creates_nothing(self):
        poor = SimpleNamespace(id=9, username="p", first_name="P")
        asyncio.run(shop_bot.handle_wallet_pay(self.query, self.context, poor, "vi", "gemini_plus", 1))
        self.assertEqual(self.orders(), [])
        self.assertIn("không đủ", self.query.answer.await_args.args[0])

    def test_sold_out_on_web_refunds_and_tells_the_customer(self):
        before = self.balance()
        self.run_pay(FakeWeb(claim=(wp.WEB_SOLD_OUT, None)))
        (order,) = self.orders()
        self.assertEqual((order["status"], self.balance()), ("cancelled", before))
        self.send_delivery.assert_not_awaited()
        self.assertIn("hoàn lại", self.bot.send_message.await_args.kwargs["text"])

    def test_telegram_forbidden_does_not_lose_the_transaction(self):
        self.send_delivery.side_effect = RuntimeError("Forbidden: bot was blocked by the user")
        self.run_pay()
        (order,) = self.orders()
        self.assertEqual(order["status"], "completed")
        self.assertEqual(order["item_delivered"], BUNDLE)

    def test_only_one_of_two_simultaneous_handlers_pays(self):
        async def both():
            web = FakeWeb()
            with patch.object(shop_bot, "wallet_web_ops", return_value=web.ops()):
                await asyncio.gather(*[shop_bot.handle_wallet_pay(self.query, self.context, self.user, "vi", "gemini_plus", 1) for _ in range(3)])

        asyncio.run(both())
        self.assertEqual(len(self.orders()), 1)
        self.assertEqual(self.ledger("purchase"), 1)

    def test_flag_off_refuses_and_creates_nothing(self):
        with patch.dict(os.environ, {"WALLET_PAY_ENABLED": "0"}):
            asyncio.run(shop_bot.handle_wallet_pay(self.query, self.context, self.user, "vi", "gemini_plus", 1))
        self.assertEqual(self.orders(), [])

    def test_group_chat_refused(self):
        self.query.message.chat.type = "group"
        asyncio.run(shop_bot.handle_wallet_pay(self.query, self.context, self.user, "vi", "gemini_plus", 1))
        self.assertEqual(self.orders(), [])

    def test_out_of_stock_checked_before_charging(self):
        with patch.object(shop_bot, "get_stock_count", return_value=0):
            asyncio.run(shop_bot.handle_wallet_pay(self.query, self.context, self.user, "vi", "gemini_plus", 1))
        self.assertEqual((self.orders(), self.balance()), ([], 200_000))

    def test_sweep_task_recovers_and_check_button_reports(self):
        order = wp.begin_payment(self.conn, 5, "u", "gemini_plus", 1, 50_000)
        with patch.object(shop_bot, "wallet_web_ops", return_value=FakeWeb().ops()):
            asyncio.run(shop_bot.run_wallet_order(self.bot, order.order_id))
        self.assertEqual(self.row(order.order_id)["status"], "completed")

    def test_wallet_order_shows_in_orders_bought(self):
        self.run_pay()
        text, _ = shop_bot.render_orders_page(5, 0, "vi")
        self.assertIn("TGS", text)


class ButtonTests(Base):
    def setUp(self):
        super().setUp()
        self.user = SimpleNamespace(id=5, username="u", first_name="U")
        self.sent = {}

        async def reply_text(text, reply_markup=None, parse_mode=None):
            self.sent["markup"] = reply_markup

        self.target = SimpleNamespace(chat=SimpleNamespace(type="private"), reply_text=reply_text)
        patches = [patch.object(shop_bot, "sync_web_product_price"), patch.object(shop_bot, "known_skus", return_value=["gemini_plus"])]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def buttons(self):
        asyncio.run(shop_bot.show_order_confirmation(self.target, self.user, "gemini_plus", 1))
        return [b for row in self.sent["markup"].inline_keyboard for b in row]

    def test_no_button_when_flag_off(self):
        with patch.dict(os.environ, {"WALLET_PAY_ENABLED": "0"}):
            self.assertFalse([b for b in self.buttons() if str(b.callback_data).startswith("wp:")])

    def test_no_button_when_wallet_off(self):
        with patch.dict(os.environ, {"WALLET_ENABLED": "0"}):
            self.assertFalse([b for b in self.buttons() if str(b.callback_data).startswith("wp:")])

    def test_button_shows_short_then_enough(self):
        (short,) = [b for b in self.buttons() if str(b.callback_data).startswith("wp:")]
        self.assertEqual(short.callback_data, "wp:gemini_plus:1")
        self.assertIn("thiếu", short.text)
        ws.credit_topup(self.conn, 5, 500_000, "TOPBTN1")
        (enough,) = [b for b in self.buttons() if str(b.callback_data).startswith("wp:")]
        self.assertIn("đủ", enough.text)

    def test_no_button_in_group(self):
        self.target.chat.type = "group"
        self.assertFalse([b for b in self.buttons() if str(b.callback_data).startswith("wp:")])


if __name__ == "__main__":
    unittest.main()
