"""Admin tools for the bot's own wallet (docs/bot-own-wallet-design.md sections E and A): look up a customer, correct a balance,
and back the SQLite database up.

Rules:
- there is NO way to edit or delete a ledger line (the ledger is append-only); a correction is a NEW ``admin_adjust`` line;
- an adjustment needs a reason (<= 200 chars), a whole non-zero amount of at most ``wallet_store.MAX_ADMIN_ADJUST_VND`` and can
  never push the balance below zero; the acting admin is recorded;
- backups use ``sqlite3.Connection.backup()`` (a consistent snapshot, safe while the bot is writing in WAL mode) and the newest
  ``KEEP_BACKUPS`` files are kept.

No Telegram and no network here. Nothing in this module logs balances, reasons or amounts.
"""
from __future__ import annotations

import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime

import wallet_store

KEEP_BACKUPS = 14
BACKUP_INTERVAL_HOURS = 6
BACKUP_DIRNAME = "backups"
STALE_PART_SECONDS = 3600
_BACKUP_RE = re.compile(r"shop-(\d{8}-\d{6})(?:-(\d+))?\.db")


class AdminError(Exception):
    """``code`` is a short stable string: bad_user, bad_amount, reason_required, too_large, insufficient_funds, no_wallet."""
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class LedgerLine:
    id: int
    user_id: int
    kind: str
    amount_vnd: int
    balance_after: int
    ref: str
    note: str | None
    actor: str | None
    created_at: str


@dataclass(frozen=True)
class WalletView:
    user_id: int
    balance_vnd: int
    lines: list


def parse_user_id(text) -> int:
    if not isinstance(text, str) or not text.strip().isascii() or not text.strip().isdigit() or len(text.strip()) > 15:
        raise AdminError("bad_user")
    value = int(text.strip())
    if value <= 0:
        raise AdminError("bad_user")
    return value


def parse_amount(text) -> int:
    """Digits only, with optional '.' ',' separators; no sign, no decimals, no 'k'."""
    if not isinstance(text, str):
        raise AdminError("bad_amount")
    cleaned = text.strip().replace(".", "").replace(",", "")
    if not cleaned.isascii() or not cleaned.isdigit() or len(cleaned) > 9:
        raise AdminError("bad_amount")
    value = int(cleaned)
    if value <= 0:
        raise AdminError("bad_amount")
    if value > wallet_store.MAX_ADMIN_ADJUST_VND:
        raise AdminError("too_large")
    return value


def _line(row) -> LedgerLine:
    return LedgerLine(int(row[0]), int(row[1]), row[2], int(row[3]), int(row[4]), row[5], row[6], row[7], row[8])


_COLUMNS = "id, user_id, kind, amount_vnd, balance_after, ref, note, actor, created_at"


def get_line(conn: sqlite3.Connection, ledger_id: int) -> LedgerLine:
    return _line(conn.execute(f"SELECT {_COLUMNS} FROM wallet_ledger WHERE id = ?", (ledger_id,)).fetchone())


def view(conn: sqlite3.Connection, user_id: int, limit: int = 10) -> WalletView:
    """Balance + the newest ledger lines (read only; never creates anything)."""
    wallet_store._user(user_id)
    try:
        rows = conn.execute(f"SELECT {_COLUMNS} FROM wallet_ledger WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                            (user_id, max(1, min(int(limit), 50)))).fetchall()
    except sqlite3.OperationalError:       # wallet tables not created yet
        rows = []
    return WalletView(user_id, wallet_store.get_balance(conn, user_id), [_line(r) for r in rows])


def adjust(conn: sqlite3.Connection, user_id: int, sign: int, amount_vnd: int, reason: str, admin_id) -> LedgerLine:
    """+amount (sign=+1) or -amount (sign=-1) as a new ``admin_adjust`` ledger line; returns that line."""
    if sign not in (1, -1):
        raise AdminError("bad_amount")
    reason = (reason or "").strip() if isinstance(reason, str) else ""
    if not reason or len(reason) > wallet_store.MAX_NOTE_LENGTH:
        raise AdminError("reason_required")
    try:
        result = wallet_store.admin_adjust(conn, user_id, sign * amount_vnd, reason, admin_id)
    except wallet_store.InsufficientFunds:
        raise AdminError("insufficient_funds") from None
    except wallet_store.InvalidAmount:
        raise AdminError("too_large" if abs(amount_vnd) > wallet_store.MAX_ADMIN_ADJUST_VND else "bad_amount") from None
    except wallet_store.WalletError as error:
        raise AdminError("reason_required" if error.code == "reason_required" else "bad_user") from None
    return get_line(conn, result.ledger_id)


# ---- backups -------------------------------------------------------------------------------------------------------------

def backup_dir(data_dir: str) -> str:
    return os.path.join(data_dir, BACKUP_DIRNAME)


def list_backups(data_dir: str) -> list[str]:
    """Backup files, oldest first."""
    directory = backup_dir(data_dir)
    try:
        names = [n for n in os.listdir(directory) if _BACKUP_RE.fullmatch(n)]
    except FileNotFoundError:
        return []

    def order(name: str):
        match = _BACKUP_RE.fullmatch(name)
        return match.group(1), int(match.group(2) or 0)

    return [os.path.join(directory, n) for n in sorted(names, key=order)]


def make_backup(conn: sqlite3.Connection, data_dir: str, *, now: datetime | None = None, keep: int = KEEP_BACKUPS) -> str:
    """Write a consistent snapshot of the database into ``<data_dir>/backups`` and prune to the newest ``keep`` files."""
    directory = backup_dir(data_dir)
    os.makedirs(directory, exist_ok=True)
    _remove_stale_parts(directory)
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    path, n = os.path.join(directory, f"shop-{stamp}.db"), 0
    while os.path.exists(path):             # two backups in the same second
        n += 1
        path = os.path.join(directory, f"shop-{stamp}-{n}.db")
    temp = path + ".part"
    target = sqlite3.connect(temp)
    try:
        conn.backup(target)
    except BaseException:
        target.close()
        _remove(temp)
        raise
    target.close()
    os.replace(temp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    for old in list_backups(data_dir)[:-keep]:
        _remove(old)
    return path


def _remove_stale_parts(directory: str) -> None:
    """Delete ``shop-*.db.part`` files older than ``STALE_PART_SECONDS`` (a backup killed half way). Newer ones may still be
    being written; files with any other name are never touched."""
    cutoff = time.time() - STALE_PART_SECONDS
    try:
        names = os.listdir(directory)
    except OSError:
        return
    for name in names:
        if name.endswith(".part") and _BACKUP_RE.fullmatch(name[:-len(".part")]):
            path = os.path.join(directory, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    _remove(path)
            except OSError:
                pass


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass
