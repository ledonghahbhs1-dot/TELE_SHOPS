"""Task 058 PR 5: wallet top-up through USDT (Plisio / BEP20 / Binance Pay). Offline: the web and the providers are simulated."""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import shop_bot
import wallet_store as ws
import wallet_topup as wt

ADDRESS = "0x" + "ab" * 20
FUTURE = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat().replace("+00:00", "Z")


def good_body(route, payload, **override):
    base = {"success": True, "order_code": payload["order_code"], "qty": 1, "amount_vnd": payload["amount_vnd"], "expires_at": FUTURE}
    if route.endswith("/plisio"):
        base.update(amount_usdt=4.12, txn_id="t1", invoice_url="https://plisio.net/invoice/abc", currency="USDT_BSC")
    elif route.endswith("/bep20"):
        base.update(network="BSC (BEP20)", token="USDT", address=ADDRESS, amount_usdt="4.37", min_confirmations=15)
    else:
        base.update(network="Binance Pay", token="USDT", receiver_id="123456789", amount_usdt="4.12")
    base.update(override)
    return base


class FakeWeb:
    def __init__(self, status=200, raises=None, **override):
        self.status, self.raises, self.override, self.calls = status, raises, override, []

    def __call__(self, path, payload, timeout=20):
        self.calls.append((path, payload))
        if self.raises:
            raise self.raises
        if self.status != 200:
            return self.status, dict(self.override)
        return 200, good_body(path, payload, **self.override)


class Base(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = patch.multiple(shop_bot, DATA_DIR=self.directory.name, DB_PATH=str(Path(self.directory.name) / "shop.db"),
                                  CLAIMED_LINKS_FILE=str(Path(self.directory.name) / "c.txt"), BOT_TOKEN="test-token")
        settings.start()
        self.addCleanup(settings.stop)
        env = patch.dict(os.environ, {"WALLET_ENABLED": "1", "WALLET_TOPUP_ENABLED": "1", "WALLET_TOPUP_USDT_ENABLED": "1",
                                      "BEP20_ENABLED": "1", "BINANCE_PAY_ENABLED": "1", "USDT_WALLET": ""})
        env.start()
        self.addCleanup(env.stop)
        shop_bot.init_database()
        shop_bot.set_setting("default_lang", "vi")
        self.conn = shop_bot.get_db_connection()
        self.addCleanup(self.conn.close)

    def create(self, method="plisio", user=5, amount=200_000, web=None):
        return wt.create_usdt_order(self.conn, user, "u", amount, method, post_web=web or FakeWeb())

    def row(self, order_id):
        return self.conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()

    def pending_count(self):
        return self.conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'pending'").fetchone()[0]

    def ledger(self):
        return self.conn.execute("SELECT COUNT(*) FROM wallet_ledger").fetchone()[0]


class CreateUsdtOrderTests(Base):
    def test_each_method_creates_a_topup_order_with_the_web_amounts(self):
        expected = {"plisio": ("usdt", "4.12"), "bep20": ("bep20", "4.37"), "binance": ("binance_pay", "4.12")}
        for method, (payment_method, usdt) in expected.items():
            web = FakeWeb()
            order = self.create(method, web=web)
            row = self.row(order.order_id)
            self.assertEqual((row["kind"], row["product_id"], row["status"], row["payment_method"], row["currency"], row["amount"]),
                             ("wallet_topup", "wallet_topup", "pending", payment_method, "USDT", 200_000), method)
            self.assertEqual(order.amount_usdt, usdt)
            self.assertEqual(order.amount_vnd, 200_000)
            path, payload = web.calls[0]
            self.assertEqual((payload["kind"], payload["amount_vnd"], payload["currency"]), ("wallet_topup", 200_000, "USDT"))
            self.assertNotIn("qty", payload)
            self.assertNotIn("amount_usdt", payload)        # the bot never prices USDT itself

    def test_routes(self):
        for method, route in (("plisio", "/api/tg/orders/plisio"), ("bep20", "/api/tg/orders/bep20"),
                              ("binance", "/api/tg/orders/binance-pay")):
            web = FakeWeb()
            self.create(method, web=web, user=hash(method) % 1000 + 1)
            self.assertEqual(web.calls[0][0], route)

    def test_method_specific_fields(self):
        self.assertEqual(self.create("plisio").invoice_url, "https://plisio.net/invoice/abc")
        bep = self.create("bep20")
        self.assertEqual((bep.address, bep.min_confirmations), (ADDRESS, 15))
        self.assertEqual(self.create("binance").receiver_id, "123456789")

    def test_local_expiry_follows_the_web(self):
        order = self.create("bep20")
        self.assertEqual(self.row(order.order_id)["expires_at"], order.expires_at)
        stamp = datetime.strptime(order.expires_at, "%Y-%m-%d %H:%M:%S")
        self.assertGreater(stamp, datetime.now() + timedelta(minutes=8))

    def test_unknown_method_refused_without_a_row(self):
        with self.assertRaises(wt.TopupError):
            self.create("paypal")
        self.assertEqual(self.pending_count(), 0)

    def test_limits_are_checked_before_calling_the_web(self):
        web = FakeWeb()
        for amount in (19_999, 2_000_001, 0, -5, True, 50_000.0):
            with self.assertRaises(wt.TopupError) as caught:
                self.create(amount=amount, web=web)
            self.assertEqual(caught.exception.code, "invalid_amount")
        ws.credit_topup(self.conn, 5, 4_900_000, "TOPCAP1")
        with self.assertRaises(wt.TopupError) as caught:
            self.create(amount=200_000, web=web)
        self.assertEqual(caught.exception.code, "balance_cap")
        self.assertEqual(web.calls, [])

    def test_pending_cap(self):
        for _ in range(3):
            self.create()
        with self.assertRaises(wt.TopupError) as caught:
            self.create()
        self.assertEqual(caught.exception.code, "too_many_pending")

    def test_web_failures_leave_no_pending_order(self):
        cases = [(FakeWeb(status=503, error="wallet_topup_rate_unavailable"), "web_unavailable"),
                 (FakeWeb(status=500), "web_unavailable"), (FakeWeb(status=0), "web_unavailable"),
                 (FakeWeb(status=408), "web_unavailable"),
                 (FakeWeb(status=400, error="wallet_topup_disabled"), "web_refused"),
                 (FakeWeb(status=409, error="order_mismatch"), "web_refused"),
                 (FakeWeb(status=429, error="too_many_pending_orders"), "too_many_pending"),
                 (FakeWeb(status=429, error="slow_down"), "web_unavailable"),
                 (FakeWeb(status=502, error="invoice_creation_failed"), "web_unavailable"),
                 (FakeWeb(status=502, error="invoice_creation_failed", provider_code="106", minimum_exclusive=1.5), "usdt_minimum"),
                 (FakeWeb(raises=TimeoutError()), "web_unavailable")]
        for web, code in cases:
            with self.assertRaises(wt.TopupError) as caught:
                self.create(web=web)
            self.assertEqual(caught.exception.code, code, web.override)
            self.assertEqual(self.pending_count(), 0)
        self.assertEqual(self.ledger(), 0)

    def test_malformed_web_replies_are_refused_and_cancelled(self):
        bad = {
            "plisio": [dict(order_code="OTHER"), dict(amount_vnd=1), dict(success=False), dict(amount_usdt=0), dict(amount_usdt="x"),
                       dict(amount_usdt=True), dict(invoice_url="http://plisio.net/x"), dict(invoice_url="https://evil.example/x"),
                       dict(invoice_url="https://user:pw@plisio.net/x"), dict(expires_at=5), dict(expires_at="not a date")],
            "bep20": [dict(address="0x123"), dict(network="Tron"), dict(token="USDC"), dict(amount_usdt="4.3"),
                      dict(amount_usdt=4.37), dict(min_confirmations=0), dict(min_confirmations=True)],
            "binance": [dict(receiver_id="abc"), dict(network="BSC (BEP20)"), dict(amount_usdt="12"), dict(amount_usdt=4.12)],
        }
        for method, overrides in bad.items():
            for override in overrides:
                with self.assertRaises(wt.TopupError, msg=(method, override)) as caught:
                    self.create(method, web=FakeWeb(**override))
                self.assertEqual(caught.exception.code, "web_refused", (method, override))
                self.assertEqual(self.pending_count(), 0, (method, override))

    def test_a_failed_attempt_does_not_use_up_the_pending_cap(self):
        for _ in range(5):
            with self.assertRaises(wt.TopupError):
                self.create(web=FakeWeb(status=500))
        self.create()


class SettleUsdtTests(Base):
    def test_paid_credits_the_vnd_amount_once_per_method(self):
        for user, method in enumerate(("plisio", "bep20", "binance"), start=10):
            order = self.create(method, user=user)
            first = wt.settle(self.conn, order.order_id, user, {"status": "paid"})
            second = wt.settle(self.conn, order.order_id, user, {"status": "delivered"})
            self.assertEqual((first.state, first.amount_vnd, first.balance_vnd), ("credited", 200_000, 200_000), method)
            self.assertEqual(second.state, "already_credited")
            self.assertEqual(ws.get_balance(self.conn, user), 200_000)
            self.assertEqual(self.row(order.order_id)["status"], "completed")
        self.assertEqual(self.ledger(), 3)
        self.assertEqual(ws.audit(self.conn), [])

    def test_pending_does_not_credit(self):
        order = self.create("bep20")
        self.assertEqual(wt.settle(self.conn, order.order_id, 5, {"status": "pending"}).state, "pending")
        self.assertEqual(wt.settle(self.conn, order.order_id, 5, {}).state, "pending")
        self.assertEqual(ws.get_balance(self.conn, 5), 0)

    def test_needs_review_is_never_credited(self):
        order = self.create("bep20")
        self.assertEqual(wt.settle(self.conn, order.order_id, 5, {"status": "needs_review"}).state, "needs_review")
        self.assertEqual((ws.get_balance(self.conn, 5), self.ledger()), (0, 0))

    def test_late_payment_after_local_cancel_still_credits_once(self):
        order = self.create("binance")
        self.conn.execute("UPDATE orders SET status = 'cancelled' WHERE order_id = ?", (order.order_id,))
        self.conn.commit()
        self.assertEqual(wt.settle(self.conn, order.order_id, 5, {"status": "paid"}).state, "credited")
        self.assertEqual(wt.settle(self.conn, order.order_id, 5, {"status": "paid"}).state, "already_credited")
        self.assertEqual(ws.get_balance(self.conn, 5), 200_000)

    def test_credit_is_the_vnd_amount_not_what_the_web_reports(self):
        order = self.create("plisio")
        result = wt.settle(self.conn, order.order_id, 5, {"status": "paid", "amount_vnd": 5_000_000, "amount_usdt": 999})
        self.assertEqual(result.balance_vnd, 200_000)

    def test_other_customers_cannot_settle_it(self):
        order = self.create("plisio")
        self.assertEqual(wt.settle(self.conn, order.order_id, 99, {"status": "paid"}).state, "not_found")

    def test_topup_is_never_delivered_or_listed_as_bought(self):
        order = self.create("plisio")
        wt.settle(self.conn, order.order_id, 5, {"status": "paid"})
        text, _ = shop_bot.render_orders_page(5, 0, "vi")
        self.assertNotIn(order.order_id, text)


class FlagTests(Base):
    def methods(self, **env):
        with patch.dict(os.environ, env):
            return shop_bot.topup_methods()

    def test_default_is_vietqr_only(self):
        self.assertEqual(self.methods(WALLET_TOPUP_USDT_ENABLED="0"), ["vietqr"])

    def test_usdt_flag_opens_plisio_and_each_method_needs_its_own_flag(self):
        self.assertEqual(self.methods(), ["vietqr", "plisio", "bep20", "binance"])
        self.assertEqual(self.methods(BEP20_ENABLED="0"), ["vietqr", "plisio", "binance"])
        self.assertEqual(self.methods(BINANCE_PAY_ENABLED="0"), ["vietqr", "plisio", "bep20"])
        self.assertEqual(self.methods(BEP20_ENABLED="0", BINANCE_PAY_ENABLED="0"), ["vietqr", "plisio"])

    def test_flag_must_be_exactly_one(self):
        for value in ("true", "yes", "2", " ", ""):
            self.assertEqual(self.methods(WALLET_TOPUP_USDT_ENABLED=value), ["vietqr"], value)

    def test_needs_topup_and_wallet_on(self):
        self.assertEqual(self.methods(WALLET_TOPUP_ENABLED="0"), ["vietqr"])
        self.assertEqual(self.methods(WALLET_ENABLED="0"), ["vietqr"])


class BotFlowTests(Base):
    def setUp(self):
        super().setUp()
        self.user = SimpleNamespace(id=5, username="u", first_name="U")
        self.replies = []

        async def reply_text(text, reply_markup=None, parse_mode=None):
            self.replies.append((text, reply_markup))

        async def reply_photo(photo=None, caption=None, reply_markup=None, parse_mode=None):
            self.replies.append((caption or "", reply_markup))

        self.message = SimpleNamespace(chat=SimpleNamespace(type="private"), reply_text=reply_text, reply_photo=reply_photo)

    def buttons(self, index=-1):
        markup = self.replies[index][1]
        return [b for row in markup.inline_keyboard for b in row] if markup else []

    def run_amount(self, amount=200_000):
        asyncio.run(shop_bot.start_topup(self.message, self.user, amount, "vi"))

    def test_only_vietqr_goes_straight_to_the_invoice_as_before(self):
        with patch.dict(os.environ, {"WALLET_TOPUP_USDT_ENABLED": "0"}), \
                patch.object(shop_bot, "send_topup_invoice", new=AsyncMock()) as invoice:
            self.run_amount()
        invoice.assert_awaited_once()

    def test_with_usdt_a_method_menu_is_shown_first(self):
        with patch.object(shop_bot, "send_topup_invoice", new=AsyncMock()) as invoice:
            self.run_amount()
        invoice.assert_not_awaited()
        self.assertEqual([b.callback_data for b in self.buttons() if b.callback_data.startswith("wtm:")],
                         ["wtm:vq:200000", "wtm:pl:200000", "wtm:bp:200000", "wtm:bn:200000"])

    def test_menu_hides_methods_that_are_off(self):
        with patch.dict(os.environ, {"BEP20_ENABLED": "0", "BINANCE_PAY_ENABLED": "0"}):
            self.run_amount()
        self.assertEqual([b.callback_data for b in self.buttons() if b.callback_data.startswith("wtm:")],
                         ["wtm:vq:200000", "wtm:pl:200000"])

    def test_bad_amount_still_gets_the_amount_error(self):
        self.run_amount(1_000)
        self.assertIn("20", self.replies[-1][0])
        self.assertFalse([b for b in self.buttons() if b.callback_data.startswith("wtm:")])

    def invoice(self, method, web=None):
        with patch.object(shop_bot, "_post_web_json", web or FakeWeb()):
            asyncio.run(shop_bot.send_topup_invoice(self.message, self.user, 200_000, "vi", method))

    def test_plisio_invoice_has_pay_link_and_check_and_shows_the_web_amount(self):
        self.invoice("plisio")
        text = self.replies[-1][0]
        self.assertIn("4.12 USDT", text)
        urls = [b.url for b in self.buttons() if b.url]
        self.assertEqual(urls, ["https://plisio.net/invoice/abc"])
        self.assertTrue([b for b in self.buttons() if str(b.callback_data).startswith("confirm_pay_TGS")])

    def test_bep20_invoice_shows_address_exact_amount_and_check(self):
        self.invoice("bep20")
        text = self.replies[-1][0]
        self.assertIn(ADDRESS, text)
        self.assertIn("4.37", text)
        self.assertTrue([b for b in self.buttons() if str(b.callback_data).startswith("confirm_pay_TGS")])

    def test_binance_invoice_asks_for_the_order_id(self):
        self.invoice("binance")
        self.assertIn("123456789", self.replies[-1][0])
        self.assertTrue([b for b in self.buttons() if str(b.callback_data).startswith("bpid_TGS")])

    def test_failed_invoice_shows_no_payment_and_leaves_no_pending_order(self):
        for web in (FakeWeb(status=503), FakeWeb(raises=OSError("down")), FakeWeb(order_code="WRONG")):
            self.replies.clear()
            self.invoice("bep20", web)
            (text, markup), = self.replies
            self.assertIn("Chưa có khoản tiền nào bị trừ", text)
            self.assertFalse([b for b in self.buttons() if str(b.callback_data).startswith(("confirm_pay_", "bpid_"))])
            self.assertNotIn(ADDRESS, text)
        self.assertEqual(self.pending_count(), 0)

    def test_minimum_error_message(self):
        self.invoice("plisio", FakeWeb(status=502, provider_code="106", minimum_exclusive=2.0))
        self.assertIn("tối thiểu", self.replies[-1][0])

    def query(self, data):
        query = MagicMock()
        query.data = data
        query.answer = AsyncMock()
        query.message = self.message
        return query

    def test_method_callback_rejects_disabled_or_unknown_method(self):
        for data, env in (("wtm:bp:200000", {"BEP20_ENABLED": "0"}), ("wtm:bn:200000", {"BINANCE_PAY_ENABLED": "0"}),
                          ("wtm:pl:200000", {"WALLET_TOPUP_USDT_ENABLED": "0"}), ("wtm:xx:200000", {}), ("wtm:pl:abc", {})):
            query = self.query(data)
            with patch.dict(os.environ, env), patch.object(shop_bot, "_post_web_json", FakeWeb()) as web:
                asyncio.run(shop_bot.handle_topup_callback(query, self.user, "vi", data))
            query.answer.assert_awaited()
            self.assertEqual(web.calls, [], data)
        self.assertEqual(self.pending_count(), 0)

    def test_method_callback_creates_the_chosen_invoice(self):
        query = self.query("wtm:pl:200000")
        with patch.object(shop_bot, "_post_web_json", FakeWeb()) as web:
            asyncio.run(shop_bot.handle_topup_callback(query, self.user, "vi", "wtm:pl:200000"))
        self.assertEqual(web.calls[0][0], "/api/tg/orders/plisio")
        self.assertEqual(self.pending_count(), 1)

    def test_group_chat_is_refused(self):
        self.message.chat.type = "group"
        query = self.query("wtm:pl:200000")
        with patch.object(shop_bot, "_post_web_json", FakeWeb()) as web:
            asyncio.run(shop_bot.handle_topup_callback(query, self.user, "vi", "wtm:pl:200000"))
        self.assertEqual(web.calls, [])

    # ---- confirmation paths -------------------------------------------------------------------------------------------
    def order_row(self, order):
        return self.row(order.order_id)

    def check(self, order, bep20=None, web_status=None):
        query = self.query("x")
        query.message = SimpleNamespace(reply_text=AsyncMock())
        with patch.object(shop_bot, "check_web_bep20", return_value=bep20 or {}) as chain, \
                patch.object(shop_bot, "check_web_order_status", return_value=web_status or {}):
            asyncio.run(shop_bot.handle_topup_check(query, SimpleNamespace(bot=MagicMock()), self.order_row(order), "vi"))
        return query, chain

    def test_bep20_check_asks_the_chain_check_then_credits_once(self):
        order = self.create("bep20")
        query, chain = self.check(order, web_status={"status": "paid"})
        chain.assert_called_once()
        self.assertEqual(ws.get_balance(self.conn, 5), 200_000)
        query, _ = self.check(order, web_status={"status": "paid"})
        self.assertEqual((ws.get_balance(self.conn, 5), self.ledger()), (200_000, 1))

    def test_bep20_needs_review_is_not_credited(self):
        order = self.create("bep20")
        self.check(order, bep20={"status": "needs_review"}, web_status={"status": "paid"})
        self.assertEqual((ws.get_balance(self.conn, 5), self.ledger()), (0, 0))

    def test_plisio_check_credits_when_web_says_paid_and_not_before(self):
        order = self.create("plisio")
        query, chain = self.check(order, web_status={"status": "pending"})
        chain.assert_not_called()
        self.assertEqual(ws.get_balance(self.conn, 5), 0)
        self.check(order, web_status={"status": "paid"})
        self.assertEqual(ws.get_balance(self.conn, 5), 200_000)

    def test_binance_pasted_id_credits_the_wallet_and_never_delivers(self):
        order = self.create("binance")
        update = SimpleNamespace(effective_user=self.user, message=SimpleNamespace(text="123456789012", reply_text=AsyncMock()))
        context = SimpleNamespace(bot=MagicMock())
        context.bot.send_message = AsyncMock()
        shop_bot.AWAITING_BINANCE_ID[5] = {"order_id": order.order_id, "until": 10 ** 12, "bad": 0}
        self.addCleanup(shop_bot.AWAITING_BINANCE_ID.pop, 5, None)
        with patch.object(shop_bot, "confirm_web_binance", return_value={"result": "paid"}), \
                patch.object(shop_bot, "send_paid_order_delivery", new=AsyncMock()) as deliver:
            handled = asyncio.run(shop_bot.handle_binance_id_message(update, context))
        self.assertTrue(handled)
        deliver.assert_not_awaited()
        self.assertEqual(ws.get_balance(self.conn, 5), 200_000)
        self.assertEqual(self.row(order.order_id)["status"], "completed")
        context.bot.send_message.assert_awaited_once()

    def test_binance_needs_review_is_not_credited(self):
        order = self.create("binance")
        update = SimpleNamespace(effective_user=self.user, message=SimpleNamespace(text="123456789012", reply_text=AsyncMock()))
        shop_bot.AWAITING_BINANCE_ID[5] = {"order_id": order.order_id, "until": 10 ** 12, "bad": 0}
        self.addCleanup(shop_bot.AWAITING_BINANCE_ID.pop, 5, None)
        with patch.object(shop_bot, "confirm_web_binance", return_value={"result": "needs_review"}):
            asyncio.run(shop_bot.handle_binance_id_message(update, SimpleNamespace(bot=MagicMock())))
        self.assertEqual(ws.get_balance(self.conn, 5), 0)

    def test_binance_ordinary_purchase_still_delivers(self):
        self.conn.execute("INSERT INTO orders (order_id, user_id, product_id, amount, status, payment_method, currency, quantity) "
                          "VALUES ('TGSBUY001', 5, 'gemini_plus', 1.0, 'pending', 'binance_pay', 'USDT', 1)")
        self.conn.commit()
        update = SimpleNamespace(effective_user=self.user, message=SimpleNamespace(text="123456789012", reply_text=AsyncMock()))
        shop_bot.AWAITING_BINANCE_ID[5] = {"order_id": "TGSBUY001", "until": 10 ** 12, "bad": 0}
        self.addCleanup(shop_bot.AWAITING_BINANCE_ID.pop, 5, None)
        with patch.object(shop_bot, "confirm_web_binance", return_value={"result": "paid"}), \
                patch.object(shop_bot, "send_paid_order_delivery", new=AsyncMock(return_value=True)) as deliver:
            asyncio.run(shop_bot.handle_binance_id_message(update, SimpleNamespace(bot=MagicMock())))
        deliver.assert_awaited_once()

    def test_background_loop_runs_the_chain_check_for_bep20_topups(self):
        order = self.create("bep20")
        self.conn.execute("UPDATE orders SET created_at = datetime('now') WHERE order_id = ?", (order.order_id,))
        self.conn.commit()
        seen = {"chain": 0}

        async def one_pass():
            async def fake_sleep(seconds):
                if seconds == 15:
                    raise asyncio.CancelledError

            def chain(order_id, user_id):
                seen["chain"] += 1
                return {}

            with patch.object(shop_bot, "check_web_bep20", side_effect=chain), \
                    patch.object(shop_bot, "check_web_order_status", return_value={"status": "paid"}), \
                    patch.object(shop_bot.asyncio, "sleep", fake_sleep):
                bot = MagicMock()
                bot.send_message = AsyncMock()
                with self.assertRaises(asyncio.CancelledError):
                    await shop_bot.auto_deliver_paid_orders_task(SimpleNamespace(bot=bot))

        asyncio.run(one_pass())
        self.assertEqual(seen["chain"], 1)
        self.assertEqual(ws.get_balance(self.conn, 5), 200_000)

    def test_vietqr_topup_still_works_unchanged(self):
        web = FakeWeb()
        with patch.object(shop_bot, "_post_web_json", lambda path, payload, timeout=20: (200, {"success": True, "kind": "wallet_topup"})):
            asyncio.run(shop_bot.send_topup_invoice(self.message, self.user, 100_000, "vi"))
        self.assertEqual(self.conn.execute("SELECT payment_method, currency FROM orders").fetchone()[:], ("vietqr", "VND"))


if __name__ == "__main__":
    unittest.main()
