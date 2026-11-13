"""Task 057 PR 2: wallet top-up through VietQR/SePay (offline: the web is simulated, nothing is called for real)."""
import asyncio
import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import i18n
import shop_bot
import wallet_store as ws
import wallet_topup as wt

BANK = ("ACB", "123", "OWNER")


class FakeWeb:
    """Stands in for POST /api/tg/orders/sync."""
    def __init__(self, status=200, body=None, raises=None):
        self.status, self.body, self.raises, self.calls = status, body, raises, []

    def __call__(self, path, payload):
        self.calls.append((path, payload))
        if self.raises:
            raise self.raises
        if self.body is not None:
            return self.status, self.body
        return self.status, {"success": True, "order_code": payload["order_code"], "kind": "wallet_topup"}


class Base(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        settings = patch.multiple(shop_bot, DATA_DIR=self.directory.name, DB_PATH=str(Path(self.directory.name) / "shop.db"),
                                  CLAIMED_LINKS_FILE=str(Path(self.directory.name) / "c.txt"), BOT_TOKEN="test-token")
        settings.start()
        self.addCleanup(settings.stop)
        env = patch.dict(os.environ, {"WALLET_ENABLED": "1", "WALLET_TOPUP_ENABLED": "1", "USDT_WALLET": ""})
        env.start()
        self.addCleanup(env.stop)
        shop_bot.init_database()
        shop_bot.set_setting("default_lang", "vi")
        self.conn = shop_bot.get_db_connection()
        self.addCleanup(self.conn.close)

    def create(self, user=5, amount=100_000, web=None):
        return wt.create_order(self.conn, user, "u", amount, bank=BANK, post_web=web or FakeWeb())

    def row(self, order_id):
        return self.conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()

    def ledger(self):
        return self.conn.execute("SELECT COUNT(*) FROM wallet_ledger").fetchone()[0]


class CreateTests(Base):
    def test_order_is_a_wallet_topup_synced_to_web_with_kind(self):
        web = FakeWeb()
        order = self.create(web=web)
        row = self.row(order.order_id)
        self.assertEqual((row["kind"], row["product_id"], row["amount"], row["status"], row["payment_method"]),
                         ("wallet_topup", "wallet_topup", 100_000, "pending", "vietqr"))
        path, payload = web.calls[0]
        self.assertEqual(path, "/api/tg/orders/sync")
        self.assertEqual((payload["kind"], payload["amount_vnd"], payload["currency"], payload["tg_user_id"]),
                         ("wallet_topup", 100_000, "VND", 5))
        self.assertTrue(order.order_id.startswith("TGS"))

    def test_sync_failures_never_leave_a_pending_order_and_show_no_qr(self):
        for web in (FakeWeb(status=400, body={"error": "wallet_topup_disabled"}), FakeWeb(status=503, body={}),
                    FakeWeb(raises=TimeoutError()), FakeWeb(status=200, body={"success": True}),      # older web: no kind echoed
                    FakeWeb(status=200, body={"success": True, "kind": "purchase"})):
            with self.assertRaises(wt.TopupError):
                self.create(web=web)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM orders WHERE status = 'pending'").fetchone()[0], 0)
        self.assertEqual(self.ledger(), 0)

    def test_limits_cap_and_pending_limit(self):
        for bad in (19_999, 2_000_001, 0, -5, 50_000.5, "50000", True, None):
            with self.assertRaises(wt.TopupError) as ctx:
                self.create(amount=bad)
            self.assertEqual(ctx.exception.code, "invalid_amount", bad)
        ws.credit_topup(self.conn, 5, 2_000_000, "OLD1")
        ws.credit_topup(self.conn, 5, 2_000_000, "OLD2")
        ws.credit_topup(self.conn, 5, 900_000, "OLD3")                       # balance 4,900,000
        with self.assertRaises(wt.TopupError) as ctx:
            self.create(amount=200_000)
        self.assertEqual(ctx.exception.code, "balance_cap")
        self.create(amount=100_000)                                            # exactly reaches 5,000,000: allowed
        for _ in range(wt.MAX_PENDING_TOPUPS - 1):
            self.create(user=9, amount=20_000)
        self.create(user=9, amount=20_000)
        with self.assertRaises(wt.TopupError) as ctx:
            self.create(user=9, amount=20_000)
        self.assertEqual(ctx.exception.code, "too_many_pending")
        self.create(user=10, amount=20_000)                                    # another customer is unaffected

    def test_parse_amount(self):
        for text, expected in (("150000", 150000), (" 150.000 ", 150000), ("1,500,000", 1500000), ("20 000", 20000)):
            self.assertEqual(wt.parse_amount(text), expected)
        for text in ("abc", "150k", "1.5e5", "-1", "", "0x10", "١٥٠٠٠٠", "12345678901", None, 5):
            self.assertIsNone(wt.parse_amount(text), text)


class SettleTests(Base):
    def test_credit_once_even_when_confirmed_twice_and_amount_is_the_chosen_one(self):
        order = self.create(amount=150_000)
        first = wt.settle(self.conn, order.order_id, 5, {"status": "paid", "amount_vnd": 999})
        again = wt.settle(self.conn, order.order_id, 5, {"status": "delivered"})
        self.assertEqual((first.state, first.balance_vnd, first.amount_vnd), ("credited", 150_000, 150_000))
        self.assertEqual(again.state, "already_credited")
        self.assertEqual(ws.get_balance(self.conn, 5), 150_000)
        self.assertEqual(self.ledger(), 1)
        row = self.row(order.order_id)
        self.assertEqual(row["status"], "completed")
        self.assertIsNotNone(row["delivery_notified_at"])
        self.assertEqual(ws.audit(self.conn), [])

    def test_twenty_parallel_confirmations_credit_once(self):
        order = self.create(amount=100_000)
        results, errors = [], []

        def worker():
            conn = shop_bot.get_db_connection()
            try:
                results.append(wt.settle(conn, order.order_id, 5, {"status": "paid"}))
            except Exception as error:      # noqa: BLE001
                errors.append(repr(error))
            finally:
                conn.close()

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(errors, [])
        self.assertEqual(sum(1 for r in results if r.state == "credited"), 1)
        self.assertEqual(ws.get_balance(self.conn, 5), 100_000)
        self.assertEqual(self.ledger(), 1)

    def test_late_payment_on_cancelled_or_expired_order_is_still_credited(self):
        for status in ("cancelled", "expired"):
            order = self.create(user=6 if status == "cancelled" else 7, amount=40_000)
            self.conn.execute("UPDATE orders SET status = ? WHERE order_id = ?", (status, order.order_id))
            self.conn.commit()
            user = 6 if status == "cancelled" else 7
            result = wt.settle(self.conn, order.order_id, user, {"status": "paid"})
            self.assertEqual(result.state, "credited", status)
            self.assertEqual(ws.get_balance(self.conn, user), 40_000)
            self.assertEqual(self.row(order.order_id)["status"], "completed")

    def test_needs_review_pending_unknown_and_wrong_user_never_credit(self):
        order = self.create()
        self.assertEqual(wt.settle(self.conn, order.order_id, 5, {"status": "needs_review"}).state, "needs_review")
        for stat in ({"status": "pending"}, {}, {"status": "not_found"}, {"status": "weird"}, None):
            self.assertEqual(wt.settle(self.conn, order.order_id, 5, stat).state, "pending")
        self.assertEqual(wt.settle(self.conn, order.order_id, 6, {"status": "paid"}).state, "not_found")
        self.assertEqual(wt.settle(self.conn, "TGSNOPE0000", 5, {"status": "paid"}).state, "not_found")
        self.assertEqual(ws.get_balance(self.conn, 5), 0)
        self.assertEqual(self.ledger(), 0)
        self.assertEqual(self.row(order.order_id)["status"], "pending")

    def test_a_purchase_order_is_never_settled_as_a_topup(self):
        self.conn.execute("INSERT INTO orders (order_id, user_id, product_id, amount, status) VALUES ('TGSBUY00001', 5, 'gemini_plus', 20000, 'pending')")
        self.conn.commit()
        self.assertEqual(wt.settle(self.conn, "TGSBUY00001", 5, {"status": "paid"}).state, "not_found")
        self.assertEqual(self.ledger(), 0)


class IsolationTests(Base):
    def make_completed(self):
        order = self.create(amount=50_000)
        wt.settle(self.conn, order.order_id, 5, {"status": "paid"})
        return order

    def test_topup_is_not_in_orders_bought_and_does_not_change_language_default(self):
        self.make_completed()
        text, _ = shop_bot.render_orders_page(5, 1, "vi")
        self.assertIn(i18n.t("vi", "orders_empty"), text)
        self.assertEqual(shop_bot.get_user_lang(5), shop_bot.get_default_lang())   # a top-up is not shopping history

    def test_startup_sync_skips_topups(self):
        order = self.create()
        calls = []
        with patch.object(shop_bot, "sync_order_to_web_sync", lambda *a, **k: calls.append(a)):
            shop_bot.sync_all_pending_orders_on_startup()
        self.assertEqual([c for c in calls if c[0] == order.order_id], [])

    def test_confirm_button_credits_wallet_and_never_delivers_or_claims_stock(self):
        order = self.create(amount=100_000)
        query = MagicMock()
        query.data = f"confirm_pay_{order.order_id}"
        query.answer = AsyncMock()
        query.message = SimpleNamespace(chat=SimpleNamespace(type="private"), reply_text=AsyncMock())
        update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=5, username="u", first_name="U"))
        context = SimpleNamespace(bot=MagicMock())
        with patch.object(shop_bot, "check_web_order_status", return_value={"status": "paid"}), \
                patch.object(shop_bot, "send_paid_order_delivery", AsyncMock()) as deliver, \
                patch.object(shop_bot, "deliver_product") as claim, \
                patch.object(shop_bot, "claim_stock_from_web") as claim_web, \
                patch.object(shop_bot, "refresh_catalogue", lambda *a, **k: None), \
                patch.object(shop_bot, "sync_web_product_price", lambda *a, **k: True):
            asyncio.run(shop_bot.callback_handler(update, context))
            asyncio.run(shop_bot.callback_handler(update, context))          # pressed twice
        deliver.assert_not_awaited()
        claim.assert_not_called()
        claim_web.assert_not_called()
        self.assertEqual(ws.get_balance(self.conn, 5), 100_000)
        self.assertEqual(self.ledger(), 1)

    def test_background_loop_helper_credits_and_notifies_once(self):
        order = self.create(amount=60_000)
        self.conn.execute("UPDATE orders SET status = 'cancelled' WHERE order_id = ?", (order.order_id,))
        self.conn.commit()
        bot = MagicMock()
        bot.send_message = AsyncMock()
        for _ in range(2):
            asyncio.run(shop_bot.settle_and_notify_topup(bot, order.order_id, 5, {"status": "paid"}))
        self.assertEqual(ws.get_balance(self.conn, 5), 60_000)
        self.assertEqual(bot.send_message.await_count, 1)
        # a blocked customer (Telegram refuses the message) must not undo or repeat the credit
        order2 = self.create(user=8, amount=30_000)
        bot.send_message = AsyncMock(side_effect=RuntimeError("Forbidden"))
        asyncio.run(shop_bot.settle_and_notify_topup(bot, order2.order_id, 8, {"status": "paid"}))
        self.assertEqual(ws.get_balance(self.conn, 8), 30_000)


class UiTests(Base):
    def invoice(self, post_web, amount=100_000):
        message = MagicMock()
        message.reply_text = AsyncMock()
        message.reply_photo = AsyncMock()
        user = SimpleNamespace(id=5, username="u", first_name="U")
        with patch.object(shop_bot, "_post_web_json", post_web):
            asyncio.run(shop_bot.send_topup_invoice(message, user, amount, "vi"))
        return message

    def test_qr_is_shown_only_after_a_successful_sync(self):
        ok = self.invoice(FakeWeb())
        self.assertEqual(ok.reply_photo.await_count + ok.reply_text.await_count, 1)
        sent = ok.reply_photo.await_args.kwargs["caption"] if ok.reply_photo.await_count else ok.reply_text.await_args.args[0]
        self.assertIn("100,000", sent)
        for web in (FakeWeb(status=400, body={"error": "wallet_topup_disabled"}), FakeWeb(raises=OSError())):
            failed = self.invoice(web)
            failed.reply_photo.assert_not_awaited()
            self.assertIn(i18n.t("vi", "topup_err_unavailable"), failed.reply_text.await_args.args[0])

    def test_out_of_range_amount_is_refused_with_friendly_text(self):
        web = FakeWeb()
        message = self.invoice(web, amount=5_000)
        self.assertEqual(web.calls, [])
        message.reply_photo.assert_not_awaited()
        self.assertIn("20,000", message.reply_text.await_args.args[0])

    def test_topup_button_appears_only_when_both_flags_are_on(self):
        def callbacks(**env):
            with patch.dict(os.environ, env):
                _, markup = shop_bot.build_wallet_screen(5, "vi")
            return [b.callback_data for row in markup.inline_keyboard for b in row]
        self.assertEqual(callbacks(), ["wtop", "main_menu"])
        for value in ("", "0", "true", "yes", "2"):
            self.assertEqual(callbacks(WALLET_TOPUP_ENABLED=value), ["main_menu"], value)
        self.assertEqual(callbacks(WALLET_ENABLED="0"), ["main_menu"])   # wallet off: no top-up button either

    def test_topup_callbacks_refused_when_off_or_in_groups(self):
        def run(data, chat="private", **env):
            query = MagicMock()
            query.message = SimpleNamespace(chat=SimpleNamespace(type=chat), reply_text=AsyncMock(), reply_photo=AsyncMock())
            query.answer = AsyncMock()
            edit = AsyncMock()
            web = FakeWeb()
            with patch.dict(os.environ, env), patch.object(shop_bot, "safe_edit_or_reply", edit), patch.object(shop_bot, "_post_web_json", web):
                asyncio.run(shop_bot.handle_topup_callback(query, SimpleNamespace(id=5, username="u", first_name="U"), "vi", data))
            return query, edit, web
        _, edit, web = run("wtop_a:100000", WALLET_TOPUP_ENABLED="0")
        self.assertIn(i18n.t("vi", "wallet_err_unavailable"), edit.await_args.args[1])
        self.assertEqual(web.calls, [])
        query, edit, web = run("wtop_a:100000", chat="group")
        self.assertTrue(query.answer.await_args.kwargs["show_alert"])
        self.assertEqual(web.calls, [])
        _, edit, _ = run("wtop")
        self.assertIn("50,000", str(edit.await_args))
        _, edit, web = run("wtop_a:abc")
        self.assertEqual(web.calls, [])

    def test_custom_amount_text_flow(self):
        update = MagicMock()
        update.effective_user = SimpleNamespace(id=5, username="u", first_name="U")
        update.message = MagicMock()
        update.message.chat = SimpleNamespace(type="private")
        update.message.reply_text = AsyncMock()
        update.message.reply_photo = AsyncMock()
        shop_bot.AWAITING_TOPUP_AMOUNT.add(5)
        self.addCleanup(shop_bot.AWAITING_TOPUP_AMOUNT.discard, 5)
        update.message.text = "abc"
        asyncio.run(shop_bot.handle_user_text_message(update, MagicMock()))
        self.assertIn(5, shop_bot.AWAITING_TOPUP_AMOUNT)                    # still waiting after a bad value
        update.message.text = "150.000"
        web = FakeWeb()
        with patch.object(shop_bot, "_post_web_json", web):
            asyncio.run(shop_bot.handle_user_text_message(update, MagicMock()))
        self.assertNotIn(5, shop_bot.AWAITING_TOPUP_AMOUNT)
        self.assertEqual(web.calls[0][1]["amount_vnd"], 150_000)


if __name__ == "__main__":
    unittest.main()
