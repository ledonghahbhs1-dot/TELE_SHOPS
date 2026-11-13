"""Audit 061 finding F1: a refund must never be paid for an order that is no longer waiting for phase 2 (offline)."""
import unittest

import wallet_pay as wp
from tests.test_wallet_pay import Base


class RefundOnlyWhileProcessingTests(Base):
    def test_completed_order_is_never_refunded(self):
        self.fund()
        order = self.begin()
        self.conn.execute("UPDATE orders SET status = 'completed' WHERE order_id = ?", (order.order_id,))
        self.conn.commit()
        result = wp._refund(self.conn, self.row(order.order_id), wp.REVIEW_REFUND)
        self.assertEqual(result.state, "closed")
        self.assertEqual(self.balance(), 150_000)          # still paid for: no money back for delivered goods
        self.assertEqual(self.ledger("refund"), 0)
        self.assertEqual(self.row(order.order_id)["status"], "completed")

    def test_processing_order_is_still_refunded_once(self):
        self.fund()
        order = self.begin()
        first = wp._refund(self.conn, self.row(order.order_id), wp.REVIEW_REFUND)
        again = wp._refund(self.conn, self.row(order.order_id), wp.REVIEW_REFUND)
        self.assertEqual((first.state, again.state), ("refunded", "refunded"))
        self.assertEqual((self.balance(), self.ledger("refund")), (200_000, 1))
        self.assertEqual(self.row(order.order_id)["status"], "cancelled")


if __name__ == "__main__":
    unittest.main()
