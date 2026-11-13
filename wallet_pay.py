"""Paying an order from the bot's own wallet (docs/bot-own-wallet-design.md, section D). Off unless WALLET_PAY_ENABLED=1.

Two phases, because delivery is a network call to the web that SQLite cannot roll back:

1. ``begin_payment`` - ONE ``BEGIN IMMEDIATE`` transaction: debit the wallet (``balance >= price``, never negative), write the
   ledger ``purchase`` line and create the order ``payment_method='wallet'`` / ``status='wallet_processing'``. The partial unique
   index ``uq_wallet_inflight`` allows one in-flight wallet order per customer (double tap / two processes -> the second fails
   and the whole transaction, debit included, is rolled back).
2. ``process_order`` - tell the web the order is paid, claim the stock, finish the local order:
   * success -> ``completed`` (the caller then sends the delivery message with the existing idempotent helper);
   * CERTAIN failure (the web says sold out / refuses the payment) -> ``wallet_store.refund`` once (UNIQUE(kind, ref)),
     status ``cancelled``, ``review_reason='wallet_refund'``;
   * UNCERTAIN failure (network error, timeout, anything else) -> no refund yet (the web may have delivered); the order stays
     ``wallet_processing`` and is retried by the customer's "check" button and the background sweep; after
     ``UNCERTAIN_REFUND_MINUTES`` it is refunded and flagged ``review_reason='wallet_refund_needs_review'`` for the admin.

No Telegram and no network here: the caller injects the web operations (see ``WebOps``).
"""
from __future__ import annotations

import os
import secrets
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Callable

import wallet_store

PAY_METHOD = "wallet"
PROCESSING = "wallet_processing"
UNCERTAIN_REFUND_MINUTES = 15
REVIEW_REFUND = "wallet_refund"
REVIEW_REFUND_UNSURE = "wallet_refund_needs_review"
_TS = "%Y-%m-%d %H:%M:%S"

# Results of the injected web operations.
WEB_OK, WEB_REFUSED, WEB_SOLD_OUT, WEB_UNCERTAIN = "ok", "refused", "sold_out", "uncertain"
WEB_DELIVERED = "already_delivered"      # wallet-cancel answer: the web already handed the goods over

INFLIGHT_INDEX = ("CREATE UNIQUE INDEX IF NOT EXISTS uq_wallet_inflight ON orders(user_id) "
                  "WHERE payment_method = 'wallet' AND status = 'wallet_processing'")


class PayError(Exception):
    """``code``: invalid_request | insufficient_funds | in_flight (short, stable, never contains balances)."""
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class _NotProcessing(Exception):
    """Internal: the order left ``wallet_processing`` before the refund could close it."""


@dataclass(frozen=True)
class WebOps:
    """The web side, injected (real implementation lives in shop_bot, tests pass fakes).

    mark_paid(order_row) -> WEB_OK | WEB_REFUSED | WEB_UNCERTAIN   (sync the order, tell the web it is paid by wallet)
    claim(sku, order_id) -> (WEB_OK, bundle_text) | (WEB_SOLD_OUT | WEB_REFUSED | WEB_UNCERTAIN, None)
    finish(order_id, user_id, bundle_text) -> list[str] | None     (store the links on the order, mark it completed)
    cancel(order_row) -> WEB_OK | WEB_DELIVERED | anything else    (optional: tell the web the refunded order must never be
                                                                    delivered; errors are ignored, they never block a refund)
    """
    mark_paid: Callable
    claim: Callable
    finish: Callable
    cancel: Callable | None = None


@dataclass(frozen=True)
class PayOrder:
    order_id: str
    user_id: int
    sku: str
    qty: int
    amount_vnd: int
    balance_vnd: int


@dataclass(frozen=True)
class ProcessResult:
    state: str      # completed | refunded | refunded_review | processing | not_found | not_wallet | closed
    order_id: str = ""
    user_id: int = 0
    amount_vnd: int = 0
    balance_vnd: int | None = None
    links: tuple = ()


def pay_flag_on() -> bool:
    """WALLET_PAY_ENABLED must be exactly "1" (default off). The caller also requires the wallet itself to be enabled."""
    return os.getenv("WALLET_PAY_ENABLED", "0").strip() == "1"


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Idempotent: the review column and the one-in-flight-per-customer index (the orders table must already exist)."""
    try:
        conn.execute("ALTER TABLE orders ADD COLUMN review_reason TEXT")
    except sqlite3.OperationalError:
        pass
    conn.execute(INFLIGHT_INDEX)
    conn.commit()


def begin_payment(conn: sqlite3.Connection, user_id: int, user_name: str | None, sku: str, qty: int, unit_price_vnd: int,
                  *, now: datetime | None = None) -> PayOrder:
    """Phase 1 (one transaction): debit + ledger + order. Raises PayError; on any error nothing changes."""
    ints = (user_id, qty, unit_price_vnd)
    if any(isinstance(v, bool) or not isinstance(v, int) for v in ints) or user_id <= 0 or qty < 1 or unit_price_vnd < 1:
        raise PayError("invalid_request")
    if not isinstance(sku, str) or not sku.strip():
        raise PayError("invalid_request")
    total = unit_price_vnd * qty
    order_id = f"TGS{secrets.token_hex(4).upper()}"
    created = (now or datetime.now()).strftime(_TS)

    def insert_order(tx: sqlite3.Connection) -> None:
        tx.execute(
            """INSERT INTO orders (order_id, user_id, user_name, product_id, amount, status, payment_method, currency, quantity,
                                   created_at, kind)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'VND', ?, ?, 'purchase')""",
            (order_id, user_id, user_name, sku, total, PROCESSING, PAY_METHOD, qty, created))

    try:
        result = wallet_store.debit_purchase(conn, user_id, total, order_id, after=insert_order)
    except wallet_store.InsufficientFunds:
        raise PayError("insufficient_funds") from None
    except sqlite3.IntegrityError:           # uq_wallet_inflight: another wallet payment of this customer is being processed
        raise PayError("in_flight") from None
    return PayOrder(order_id, user_id, sku, qty, total, result.balance_vnd)


def _row(conn, order_id: str):
    return conn.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()


def _refund(conn, order, review_reason: str) -> ProcessResult:
    """Give the money back exactly once and close the order (one transaction)."""
    order_id, user_id, amount = str(order["order_id"]), int(order["user_id"]), int(order["amount"])

    def close(tx: sqlite3.Connection) -> None:
        closed = tx.execute("UPDATE orders SET status = 'cancelled', review_reason = ? WHERE order_id = ? AND status = ?",
                            (review_reason, order_id, PROCESSING))
        if closed.rowcount != 1:             # no longer waiting (e.g. completed meanwhile): no refund, the whole transaction rolls back
            raise _NotProcessing()

    try:
        result = wallet_store.refund(conn, user_id, amount, order_id, after=close)
    except _NotProcessing:
        return ProcessResult("closed", order_id, user_id, amount)
    if not result.applied:                   # refunded before: make sure the order is closed as well
        conn.execute("UPDATE orders SET status = 'cancelled', review_reason = COALESCE(review_reason, ?) "
                     "WHERE order_id = ? AND status = ?", (review_reason, order_id, PROCESSING))
        conn.commit()
    state = "refunded_review" if review_reason == REVIEW_REFUND_UNSURE else "refunded"
    return ProcessResult(state, order_id, user_id, amount, result.balance_vnd)


def _refund_and_cancel(conn, order, review_reason: str, web: WebOps, web_may_know: bool) -> ProcessResult:
    """Refund locally (the money never waits for the web), then best-effort tell the web to close the order. If the web says
    it already delivered, the customer got both the goods and the refund: flag it for the admin."""
    result = _refund(conn, order, review_reason)
    if web.cancel is None or not web_may_know:
        return result
    try:
        outcome = web.cancel(order)
    except Exception:
        return result
    if outcome == WEB_DELIVERED and result.state == "refunded":
        return replace(result, state="refunded_review")
    return result


def process_order(conn: sqlite3.Connection, order_id: str, web: WebOps, *, now: datetime | None = None) -> ProcessResult:
    """Phase 2. Safe to call any number of times for the same order (restart, button, sweep): it never debits again, never
    refunds twice and never delivers twice. The caller must not run two of these for the same order at once."""
    order = _row(conn, order_id)
    if order is None:
        return ProcessResult("not_found", order_id)
    base = dict(order_id=order_id, user_id=int(order["user_id"]), amount_vnd=int(order["amount"]))
    if order["payment_method"] != PAY_METHOD:
        return ProcessResult("not_wallet", **base)
    if order["status"] == "completed":
        links = tuple((order["item_delivered"] or "").splitlines())
        return ProcessResult("completed", links=links, **base)
    if order["status"] != PROCESSING:
        return ProcessResult("closed", **base)

    now = now or datetime.now()
    outcome = web.mark_paid(order)
    web_may_know = outcome != WEB_REFUSED       # a refused payment was never recorded on the web: nothing to cancel there
    bundle = None
    if outcome == WEB_OK:
        outcome, bundle = web.claim(str(order["product_id"]), order_id)
    if outcome == WEB_OK and bundle:
        links = web.finish(order_id, int(order["user_id"]), bundle)
        if links:
            return ProcessResult("completed", links=tuple(links), **base)
        outcome = WEB_UNCERTAIN              # bundle did not match the order: let the admin decide after the wait
    if outcome in (WEB_SOLD_OUT, WEB_REFUSED):
        return _refund_and_cancel(conn, order, REVIEW_REFUND, web, web_may_know)
    # Uncertain: the web may already have delivered, so wait; refund + admin review only after the grace period.
    try:
        started = datetime.strptime(str(order["created_at"]), _TS)
    except (TypeError, ValueError):
        started = now - timedelta(minutes=UNCERTAIN_REFUND_MINUTES)
    if now - started >= timedelta(minutes=UNCERTAIN_REFUND_MINUTES):
        return _refund_and_cancel(conn, order, REVIEW_REFUND_UNSURE, web, web_may_know)
    return ProcessResult("processing", **base)


def processing_orders(conn: sqlite3.Connection) -> list[str]:
    """Order ids still waiting for phase 2 (startup sweep and background loop), oldest first."""
    rows = conn.execute("SELECT order_id FROM orders WHERE payment_method = ? AND status = ? ORDER BY created_at",
                        (PAY_METHOD, PROCESSING)).fetchall()
    return [str(r[0]) for r in rows]
