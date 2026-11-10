"""The bot's own wallet: balances and an append-only ledger in the bot's SQLite database (docs/bot-own-wallet-design.md).

Rules this module enforces:
- money is a whole number of VND (``int``); ``bool``, ``float``, ``str``, 0 and negative amounts are refused;
- every change is ONE transaction (``BEGIN IMMEDIATE``) that writes the ledger row and the balance together;
- ``wallet_ledger`` is append-only (triggers block UPDATE/DELETE) and ``UNIQUE(kind, ref)`` makes every operation idempotent:
  repeating a credit/debit/refund with the same ``ref`` changes nothing and reports ``applied=False``;
- balances can never go below zero (``CHECK`` in the database and a guarded UPDATE in the code).

The module does not import ``shop_bot`` (no network, no Telegram): callers pass an open ``sqlite3.Connection``
(use ``shop_bot.get_db_connection()``, which sets WAL / synchronous=FULL / busy_timeout / foreign_keys).
"""
from __future__ import annotations

import os
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass

# Limits decided by the shop owner (task 056). Top-up limits and the balance cap are enforced when a top-up order is
# CREATED (money already received is never refused at credit time).
MIN_TOPUP_VND = 20_000
MAX_TOPUP_VND = 2_000_000
MAX_BALANCE_VND = 5_000_000
MAX_ADMIN_ADJUST_VND = 2_000_000
MAX_NOTE_LENGTH = 200

KINDS = ("topup", "purchase", "refund", "admin_adjust")

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallet_accounts (
    user_id     INTEGER PRIMARY KEY,
    balance_vnd INTEGER NOT NULL DEFAULT 0 CHECK (balance_vnd >= 0),
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS wallet_ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id       INTEGER NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('topup','purchase','refund','admin_adjust')),
    amount_vnd    INTEGER NOT NULL,
    balance_after INTEGER NOT NULL CHECK (balance_after >= 0),
    ref           TEXT NOT NULL,
    note          TEXT,
    actor         TEXT,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (
        (kind IN ('topup','refund') AND amount_vnd > 0) OR
        (kind = 'purchase'          AND amount_vnd < 0) OR
        (kind = 'admin_adjust'      AND amount_vnd <> 0)
    ),
    UNIQUE (kind, ref),
    FOREIGN KEY (user_id) REFERENCES wallet_accounts(user_id)
);
CREATE INDEX IF NOT EXISTS idx_wallet_ledger_user ON wallet_ledger(user_id, id);
CREATE TRIGGER IF NOT EXISTS wallet_ledger_no_update BEFORE UPDATE ON wallet_ledger
BEGIN SELECT RAISE(ABORT, 'wallet_ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS wallet_ledger_no_delete BEFORE DELETE ON wallet_ledger
BEGIN SELECT RAISE(ABORT, 'wallet_ledger is append-only'); END;
"""


class WalletError(Exception):
    """Base class. ``code`` is a short stable string (never contains balances or secrets)."""
    code = "wallet_error"

    def __init__(self, message: str = "", code: str | None = None):
        super().__init__(message or (code or self.code))
        if code:
            self.code = code


class InvalidAmount(WalletError):
    code = "invalid_amount"


class InsufficientFunds(WalletError):
    code = "insufficient_funds"


class InvalidReference(WalletError):
    code = "invalid_reference"


@dataclass(frozen=True)
class WalletResult:
    applied: bool          # False when the same (kind, ref) had already been applied: nothing changed
    balance_vnd: int       # balance after the operation (or current balance when not applied)
    ledger_id: int


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the wallet tables if missing (idempotent). Safe to call on every start."""
    conn.executescript(SCHEMA)


def _money(amount, *, field: str = "amount") -> int:
    if isinstance(amount, bool) or not isinstance(amount, int):
        raise InvalidAmount(f"{field} must be a whole number of VND")
    if amount <= 0:
        raise InvalidAmount(f"{field} must be positive")
    return amount


def _user(user_id) -> int:
    if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
        raise WalletError("invalid user id", "invalid_user")
    return user_id


def _ref(ref) -> str:
    if not isinstance(ref, str) or not ref.strip() or len(ref) > 64 or ref != ref.strip():
        raise InvalidReference("ref must be a non-empty string of at most 64 characters")
    return ref


@contextmanager
def _transaction(conn: sqlite3.Connection):
    """``BEGIN IMMEDIATE`` ... COMMIT (ROLLBACK on any error). Restores the connection's isolation level."""
    if conn.in_transaction:
        raise WalletError("a wallet operation cannot join an open transaction", "nested_transaction")
    previous = conn.isolation_level
    conn.isolation_level = None          # manual transaction control
    try:
        ensure_schema(conn)
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


def _existing(conn, kind: str, ref: str):
    return conn.execute("SELECT id, user_id, amount_vnd, balance_after FROM wallet_ledger WHERE kind = ? AND ref = ?",
                        (kind, ref)).fetchone()


def _balance_row(conn, user_id: int) -> int:
    row = conn.execute("SELECT balance_vnd FROM wallet_accounts WHERE user_id = ?", (user_id,)).fetchone()
    return int(row[0]) if row else 0


def _apply(conn, *, user_id: int, kind: str, delta: int, ref: str, note: str | None = None,
           actor: str | None = None) -> WalletResult:
    """Inside an open transaction: replay-safe ledger insert + guarded balance update."""
    seen = _existing(conn, kind, ref)
    if seen is not None:
        if int(seen[1]) != user_id or int(seen[2]) != delta:
            raise InvalidReference("this reference was already used for a different operation")
        return WalletResult(False, _balance_row(conn, user_id), int(seen[0]))
    conn.execute("INSERT OR IGNORE INTO wallet_accounts (user_id) VALUES (?)", (user_id,))
    cursor = conn.execute(
        "UPDATE wallet_accounts SET balance_vnd = balance_vnd + ?, updated_at = CURRENT_TIMESTAMP "
        "WHERE user_id = ? AND balance_vnd + ? >= 0", (delta, user_id, delta))
    if cursor.rowcount != 1:
        raise InsufficientFunds("balance is not enough")
    balance = _balance_row(conn, user_id)
    cursor = conn.execute(
        "INSERT INTO wallet_ledger (user_id, kind, amount_vnd, balance_after, ref, note, actor) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, kind, delta, balance, ref, note, actor))
    return WalletResult(True, balance, int(cursor.lastrowid))


def get_balance(conn: sqlite3.Connection, user_id: int) -> int:
    """Current balance in VND (0 when the customer has no wallet yet). Read only; never creates anything."""
    _user(user_id)
    try:
        return _balance_row(conn, user_id)
    except sqlite3.OperationalError:       # tables not created yet
        return 0


def get_history(conn: sqlite3.Connection, user_id: int, limit: int = 10) -> list[dict]:
    """Newest ledger lines first (for the wallet screen)."""
    _user(user_id)
    limit = max(1, min(int(limit), 50))
    try:
        rows = conn.execute(
            "SELECT id, kind, amount_vnd, balance_after, ref, created_at FROM wallet_ledger "
            "WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()
    except sqlite3.OperationalError:
        return []
    return [{"id": int(r[0]), "kind": r[1], "amount_vnd": int(r[2]), "balance_after": int(r[3]),
             "ref": r[4], "created_at": r[5]} for r in rows]


def credit_topup(conn: sqlite3.Connection, user_id: int, amount_vnd: int, ref: str, *, after=None) -> WalletResult:
    """Add a confirmed top-up. ``ref`` is the top-up order id: the same order is credited at most once.

    ``after(conn)`` (optional) runs inside the SAME transaction only when the credit was applied, e.g. to mark the order
    completed (used by the top-up flow); if it raises, nothing is credited.
    """
    user_id, amount_vnd, ref = _user(user_id), _money(amount_vnd), _ref(ref)
    with _transaction(conn):
        result = _apply(conn, user_id=user_id, kind="topup", delta=amount_vnd, ref=ref, actor="system")
        if result.applied and after is not None:
            after(conn)
    return result


def debit_purchase(conn: sqlite3.Connection, user_id: int, amount_vnd: int, ref: str, *, after=None) -> WalletResult:
    """Pay an order from the wallet. Refused with InsufficientFunds when the balance is too small (nothing changes)."""
    user_id, amount_vnd, ref = _user(user_id), _money(amount_vnd), _ref(ref)
    with _transaction(conn):
        result = _apply(conn, user_id=user_id, kind="purchase", delta=-amount_vnd, ref=ref, actor="system")
        if result.applied and after is not None:
            after(conn)
    return result


def refund(conn: sqlite3.Connection, user_id: int, amount_vnd: int, ref: str, *, after=None) -> WalletResult:
    """Give back a wallet purchase (same ``ref`` as the purchase). Only a full refund of an existing purchase, once."""
    user_id, amount_vnd, ref = _user(user_id), _money(amount_vnd), _ref(ref)
    with _transaction(conn):
        purchase = _existing(conn, "purchase", ref)
        if purchase is None or int(purchase[1]) != user_id or -int(purchase[2]) != amount_vnd:
            raise InvalidReference("no matching wallet purchase to refund")
        result = _apply(conn, user_id=user_id, kind="refund", delta=amount_vnd, ref=ref, actor="system")
        if result.applied and after is not None:
            after(conn)
    return result


def admin_adjust(conn: sqlite3.Connection, user_id: int, delta_vnd: int, reason: str, actor: str | int,
                 ref: str | None = None) -> WalletResult:
    """Admin correction (+/-). A reason and the acting admin are required; limited per operation; never below zero."""
    user_id = _user(user_id)
    if isinstance(delta_vnd, bool) or not isinstance(delta_vnd, int) or delta_vnd == 0:
        raise InvalidAmount("delta must be a non-zero whole number of VND")
    if abs(delta_vnd) > MAX_ADMIN_ADJUST_VND:
        raise InvalidAmount("delta is above the per-operation limit")
    reason = (reason or "").strip() if isinstance(reason, str) else ""
    if not reason or len(reason) > MAX_NOTE_LENGTH:
        raise WalletError("a reason (up to 200 characters) is required", "reason_required")
    actor = str(actor).strip() if actor is not None else ""
    if not actor:
        raise WalletError("the acting admin is required", "actor_required")
    ref = _ref(ref) if ref is not None else "ADJ" + secrets.token_hex(6).upper()
    with _transaction(conn):
        return _apply(conn, user_id=user_id, kind="admin_adjust", delta=delta_vnd, ref=ref, note=reason, actor=actor)


def can_topup(balance_vnd: int, amount_vnd) -> bool:
    """Whether a NEW top-up order of this size may be created (limits and balance cap)."""
    if isinstance(amount_vnd, bool) or not isinstance(amount_vnd, int):
        return False
    return MIN_TOPUP_VND <= amount_vnd <= MAX_TOPUP_VND and balance_vnd + amount_vnd <= MAX_BALANCE_VND


def audit(conn: sqlite3.Connection) -> list[int]:
    """User ids whose stored balance differs from the sum of their ledger lines (must always be empty)."""
    rows = conn.execute(
        "SELECT a.user_id FROM wallet_accounts a "
        "LEFT JOIN (SELECT user_id, SUM(amount_vnd) AS s FROM wallet_ledger GROUP BY user_id) l ON l.user_id = a.user_id "
        "WHERE a.balance_vnd <> COALESCE(l.s, 0)").fetchall()
    return [int(r[0]) for r in rows]


# ---- where the data lives -----------------------------------------------------------------------------------------

def _on_railway() -> bool:
    return any(os.getenv(name) for name in ("RAILWAY_ENVIRONMENT", "RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID",
                                            "RAILWAY_SERVICE_ID"))


def storage_is_persistent(data_dir: str) -> tuple[bool, str]:
    """(ok, reason). On Railway the data directory MUST be inside the mounted Volume, otherwise balances would vanish on every
    deploy. Off Railway (local/dev/tests) there is nothing to check and the answer is ok."""
    if not _on_railway():
        return True, "not_on_railway"
    volume = (os.getenv("RAILWAY_VOLUME_MOUNT_PATH") or "").strip()
    if not volume:
        return False, "no_volume_mounted"
    try:
        data = os.path.realpath(data_dir)
        mount = os.path.realpath(volume)
        inside = os.path.commonpath([data, mount]) == mount
    except ValueError:                      # different drives
        inside = False
    return (True, "inside_volume") if inside else (False, "data_dir_outside_volume")
