"""Wallet top-up orders for the bot's own wallet (docs/bot-own-wallet-design.md, PR 2: VietQR/SePay only).

Rules this module enforces:
- a top-up is an ``orders`` row with ``kind = 'wallet_topup'`` and ``product_id = 'wallet_topup'``; it never has stock, never
  appears in "orders bought", and is never delivered;
- the order is created only within the limits (``wallet_store.can_topup``) and at most ``MAX_PENDING_TOPUPS`` pending per customer,
  both checked inside one ``BEGIN IMMEDIATE`` transaction;
- the order MUST be synced to the web (``kind: wallet_topup``) successfully before the customer is shown a QR: the web would
  otherwise auto-create a Gemini Plus order when the SePay transfer arrives. A failed/refused sync cancels the local row;
- money is credited through ONE point, ``settle`` -> ``wallet_store.credit_topup``, which marks the order completed in the same
  transaction. The amount credited is the VND amount chosen when the order was created, never what the web reports.

No Telegram and no network here: the caller injects ``post_web(path, payload) -> (status, body)``.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta

import wallet_store

TOPUP_KIND = "wallet_topup"
TOPUP_SKU = "wallet_topup"
MAX_PENDING_TOPUPS = 3
TOPUP_TTL_MINUTES = 10
QUICK_AMOUNTS = (50_000, 100_000, 200_000, 500_000)
_TS = "%Y-%m-%d %H:%M:%S"


class TopupError(Exception):
    """``code`` is a short stable string: invalid_amount, balance_cap, too_many_pending, web_unavailable, web_refused."""
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class TopupOrder:
    order_id: str
    user_id: int
    amount_vnd: int
    expires_at: str
    bank_name: str
    bank_account: str
    bank_owner: str


@dataclass(frozen=True)
class SettleResult:
    state: str                     # credited | already_credited | pending | needs_review | not_found
    balance_vnd: int | None = None
    amount_vnd: int | None = None


def topup_flag_on() -> bool:
    """WALLET_TOPUP_ENABLED must be exactly "1" (default off). The caller also requires the wallet itself to be enabled."""
    return os.getenv("WALLET_TOPUP_ENABLED", "0").strip() == "1"


def parse_amount(text) -> int | None:
    """Customer-typed amount: digits with optional '.', ',' or space separators. Anything else (text, 'k', decimals) -> None."""
    if not isinstance(text, str):
        return None
    cleaned = text.strip().replace(".", "").replace(",", "").replace(" ", "")
    if not cleaned.isascii() or not cleaned.isdigit() or len(cleaned) > 9:
        return None
    return int(cleaned)


@contextmanager
def _immediate(conn: sqlite3.Connection):
    if conn.in_transaction:
        raise TopupError("nested_transaction")
    previous = conn.isolation_level
    conn.isolation_level = None
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        else:
            conn.execute("COMMIT")
    finally:
        conn.isolation_level = previous


def create_order(conn: sqlite3.Connection, user_id: int, user_name: str | None, amount_vnd, *, bank: tuple[str, str, str],
                 post_web, now: datetime | None = None) -> TopupOrder:
    """Reserve the order (limits + pending cap, one transaction), sync it to the web, and only then return it.

    Raises TopupError; on a sync failure the reserved row is cancelled so nothing is left pending locally.
    """
    if isinstance(amount_vnd, bool) or not isinstance(amount_vnd, int) or not isinstance(user_id, int) or isinstance(user_id, bool):
        raise TopupError("invalid_amount")
    if not wallet_store.MIN_TOPUP_VND <= amount_vnd <= wallet_store.MAX_TOPUP_VND:
        raise TopupError("invalid_amount")
    wallet_store.ensure_schema(conn)
    now = now or datetime.now()
    now_s, expires_s = now.strftime(_TS), (now + timedelta(minutes=TOPUP_TTL_MINUTES)).strftime(_TS)
    order_id = f"TGS{secrets.token_hex(4).upper()}"
    with _immediate(conn):
        balance = wallet_store.get_balance(conn, user_id)
        if not wallet_store.can_topup(balance, amount_vnd):
            raise TopupError("balance_cap")
        pending = conn.execute(
            "SELECT COUNT(*) FROM orders WHERE user_id = ? AND kind = ? AND status = 'pending' AND expires_at > ?",
            (user_id, TOPUP_KIND, now_s)).fetchone()[0]
        if pending >= MAX_PENDING_TOPUPS:
            raise TopupError("too_many_pending")
        conn.execute(
            """INSERT INTO orders (order_id, user_id, user_name, product_id, amount, status, payment_method, currency, quantity,
                                   created_at, expires_at, bank_name, bank_account, bank_owner, kind)
               VALUES (?, ?, ?, ?, ?, 'pending', 'vietqr', 'VND', 1, ?, ?, ?, ?, ?, ?)""",
            (order_id, user_id, user_name, TOPUP_SKU, amount_vnd, now_s, expires_s, bank[0], bank[1], bank[2], TOPUP_KIND))
    try:
        status, body = post_web("/api/tg/orders/sync", {
            "order_code": order_id, "tg_user_id": user_id, "tg_username": str(user_name or ""), "kind": TOPUP_KIND,
            "amount_vnd": amount_vnd, "currency": "VND", "provider": "sepay"})
    except Exception:
        _cancel(conn, order_id)
        raise TopupError("web_unavailable") from None
    # The reply must say it was stored as a wallet top-up: an older web would accept the unknown kind as a Gemini Plus order.
    if status != 200 or not isinstance(body, dict) or body.get("success") is not True or body.get("kind") != TOPUP_KIND:
        _cancel(conn, order_id)
        raise TopupError("web_unavailable" if status in (0, 408, 429) or status >= 500 else "web_refused")
    return TopupOrder(order_id, user_id, amount_vnd, expires_s, bank[0], bank[1], bank[2])


def _cancel(conn: sqlite3.Connection, order_id: str) -> None:
    conn.execute("UPDATE orders SET status = 'cancelled' WHERE order_id = ? AND status = 'pending'", (order_id,))
    conn.commit()


def get_order(conn: sqlite3.Connection, order_id: str, user_id: int | None = None):
    row = conn.execute("SELECT * FROM orders WHERE order_id = ? AND kind = ?", (order_id, TOPUP_KIND)).fetchone()
    if row is None or (user_id is not None and int(row["user_id"]) != int(user_id)):
        return None
    return row


def settle(conn: sqlite3.Connection, order_id: str, user_id: int, web_status: dict) -> SettleResult:
    """The ONLY way a top-up becomes balance. Every confirmation path (button, background loop) calls this.

    web ``paid``/``delivered`` -> credit (also for an order the bot already cancelled/expired: a late payment still counts);
    ``needs_review`` -> never credited automatically (the admin reconciles and uses admin_adjust).
    """
    order = get_order(conn, order_id, user_id)
    if order is None:
        return SettleResult("not_found")
    amount = int(order["amount"])
    if order["status"] == "completed":
        return SettleResult("already_credited", wallet_store.get_balance(conn, user_id), amount)
    state = (web_status or {}).get("status")
    if state == "needs_review":
        return SettleResult("needs_review", amount_vnd=amount)
    if state not in ("paid", "delivered"):
        return SettleResult("pending", amount_vnd=amount)

    def mark_completed(tx: sqlite3.Connection) -> None:
        tx.execute(
            "UPDATE orders SET status = 'completed', completed_at = CURRENT_TIMESTAMP, delivery_notified_at = CURRENT_TIMESTAMP "
            "WHERE order_id = ? AND kind = ? AND status IN ('pending', 'cancelled', 'expired')", (order_id, TOPUP_KIND))

    result = wallet_store.credit_topup(conn, int(order["user_id"]), amount, order_id, after=mark_completed)
    if not result.applied:       # credited before (e.g. by another process): make sure the order is closed too
        conn.execute("UPDATE orders SET status = 'completed', completed_at = COALESCE(completed_at, CURRENT_TIMESTAMP), "
                     "delivery_notified_at = COALESCE(delivery_notified_at, CURRENT_TIMESTAMP) WHERE order_id = ? AND kind = ?",
                     (order_id, TOPUP_KIND))
        conn.commit()
    return SettleResult("credited" if result.applied else "already_credited", result.balance_vnd, amount)
