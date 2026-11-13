"""Task 059 (bot part): after a wallet refund the bot tells the web to close the order (wallet-cancel). Offline."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import shop_bot
import wallet_pay as wp
import wallet_store as ws

BUNDLE = "https://example.test/link-1"


class Web:
    def __init__(self, paid=wp.WEB_OK, claim=(wp.WEB_OK, BUNDLE), cancel="ok", cancel_raises=None, with_cancel=True):
        self.paid, self.claim_result, self.cancel_result, self.cancel_raises = paid, claim, cancel, cancel_raises
        self.calls, self.with_cancel = [], with_cancel

    def ops(self):
        return wp.WebOps(mark_paid=lambda o: self._log("paid", self.paid), claim=lambda s, o: self._log("claim", self.claim_result),
                         finish=lambda oid, uid, b: shop_bot.deliver_product(oid, uid, bundle=b),
                         cancel=self._cancel if self.with_cancel else None)

    def _log(self, name, value):
        self.calls.append(name)
        return value

    def _cancel(self, order):
        self.calls.append(("cancel", order["order_id"], int(order["user_id"])))
        if self.cancel_raises:
            raise self.cancel_raises
        return self.cancel_result


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
        self.conn = shop_bot.get_db_connection()
        self.addCleanup(self.conn.close)
        ws.credit_topup(self.conn, 5, 200_000, "TOP5")
        self.t0 = datetime(2026, 1, 1, 12, 0, 0)
        self.order = wp.begin_payment(self.conn, 5, "u", "gemini_plus", 1, 50_000, now=self.t0)

    def status(self):
        return self.conn.execute("SELECT status FROM orders WHERE order_id = ?", (self.order.order_id,)).fetchone()[0]


class CancelSyncTests(Base):
    def test_sold_out_refunds_then_tells_the_web(self):
        web = Web(claim=(wp.WEB_SOLD_OUT, None))
        result = wp.process_order(self.conn, self.order.order_id, web.ops())
        self.assertEqual(result.state, "refunded")
        self.assertEqual(web.calls[-1], ("cancel", self.order.order_id, 5))
        self.assertEqual(ws.get_balance(self.conn, 5), 200_000)

    def test_cancel_runs_after_the_refund_is_committed(self):
        seen = {}

        def cancel(order):
            seen["status"] = self.conn.execute("SELECT status FROM orders WHERE order_id = ?", (order["order_id"],)).fetchone()[0]
            seen["balance"] = ws.get_balance(self.conn, 5)
            return "ok"

        web = Web(claim=(wp.WEB_SOLD_OUT, None))
        ops = wp.WebOps(mark_paid=lambda o: wp.WEB_OK, claim=web.claim_result and (lambda s, o: web.claim_result),
                        finish=None, cancel=cancel)
        wp.process_order(self.conn, self.order.order_id, ops)
        self.assertEqual(seen, {"status": "cancelled", "balance": 200_000})

    def test_network_error_on_cancel_never_blocks_the_refund(self):
        web = Web(claim=(wp.WEB_SOLD_OUT, None), cancel_raises=OSError("down"))
        result = wp.process_order(self.conn, self.order.order_id, web.ops())
        self.assertEqual(result.state, "refunded")
        self.assertEqual((self.status(), ws.get_balance(self.conn, 5)), ("cancelled", 200_000))

    def test_web_says_already_delivered_flags_the_order_for_the_admin(self):
        web = Web(claim=(wp.WEB_SOLD_OUT, None), cancel=wp.WEB_DELIVERED)
        result = wp.process_order(self.conn, self.order.order_id, web.ops())
        self.assertEqual((result.state, self.status(), ws.get_balance(self.conn, 5)), ("refunded_review", "cancelled", 200_000))

    def test_timeout_refund_also_cancels_on_the_web(self):
        web = Web(paid=wp.WEB_UNCERTAIN)
        wp.process_order(self.conn, self.order.order_id, web.ops(), now=self.t0 + timedelta(minutes=3))
        self.assertNotIn(("cancel", self.order.order_id, 5), web.calls)
        result = wp.process_order(self.conn, self.order.order_id, web.ops(), now=self.t0 + timedelta(minutes=15))
        self.assertEqual(result.state, "refunded_review")
        self.assertEqual(web.calls[-1], ("cancel", self.order.order_id, 5))

    def test_a_refused_payment_was_never_on_the_web_so_no_cancel(self):
        web = Web(paid=wp.WEB_REFUSED)
        result = wp.process_order(self.conn, self.order.order_id, web.ops())
        self.assertEqual(result.state, "refunded")
        self.assertFalse([c for c in web.calls if isinstance(c, tuple)])

    def test_success_and_pending_never_cancel(self):
        web = Web()
        self.assertEqual(wp.process_order(self.conn, self.order.order_id, web.ops()).state, "completed")
        self.assertFalse([c for c in web.calls if isinstance(c, tuple)])

    def test_cancel_called_once_even_if_processed_again(self):
        web = Web(claim=(wp.WEB_SOLD_OUT, None))
        wp.process_order(self.conn, self.order.order_id, web.ops())
        again = wp.process_order(self.conn, self.order.order_id, web.ops())
        self.assertEqual(again.state, "closed")
        self.assertEqual(len([c for c in web.calls if isinstance(c, tuple)]), 1)

    def test_webops_without_cancel_still_works_as_before(self):
        web = Web(claim=(wp.WEB_SOLD_OUT, None), with_cancel=False)
        self.assertEqual(wp.process_order(self.conn, self.order.order_id, web.ops()).state, "refunded")


class RealCancelOpTests(Base):
    def row(self):
        return self.conn.execute("SELECT * FROM orders WHERE order_id = ?", (self.order.order_id,)).fetchone()

    def test_request_shape(self):
        sent = []

        def post(path, payload, timeout=20):
            sent.append((path, payload))
            return 200, {"success": True, "status": "cancelled"}

        with patch.object(shop_bot, "_post_web_json", post):
            self.assertEqual(shop_bot._wallet_web_cancel(self.row()), wp.WEB_OK)
        self.assertEqual(sent, [("/api/tg/orders/wallet-cancel", {"order_code": self.order.order_id, "tg_user_id": 5})])

    def test_classification(self):
        cases = [((409, {"error": "already_delivered"}), wp.WEB_DELIVERED), ((409, {"error": "not_wallet_order"}), wp.WEB_UNCERTAIN),
                 ((400, {"error": "wallet_pay_disabled"}), wp.WEB_UNCERTAIN), ((404, {}), wp.WEB_UNCERTAIN),
                 ((500, {}), wp.WEB_UNCERTAIN), ((200, {"success": False}), wp.WEB_UNCERTAIN)]
        for reply, expected in cases:
            with patch.object(shop_bot, "_post_web_json", lambda *a, **k: reply):
                self.assertEqual(shop_bot._wallet_web_cancel(self.row()), expected, reply)
        with patch.object(shop_bot, "_post_web_json", side_effect=OSError("down")):
            self.assertEqual(shop_bot._wallet_web_cancel(self.row()), wp.WEB_UNCERTAIN)

    def test_real_ops_refund_end_to_end_with_web_cancel_failing(self):
        def post(path, payload, timeout=20):
            if path.endswith("/orders/sync"):
                return 200, {"success": True}
            if path.endswith("/wallet-paid"):
                return 200, {"success": True}
            if path.endswith("/stock/claim"):
                return 409, {"error": "Insufficient stock for this paid order"}
            raise OSError("web down")        # wallet-cancel unreachable

        with patch.object(shop_bot, "_post_web_json", post):
            result = wp.process_order(self.conn, self.order.order_id, shop_bot.wallet_web_ops())
        self.assertEqual((result.state, ws.get_balance(self.conn, 5)), ("refunded", 200_000))


if __name__ == "__main__":
    unittest.main()
