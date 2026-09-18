import aiosqlite
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional

import config
from utils.logger import logger


class Database:
    def __init__(self, db_path: str = config.DATABASE_PATH):
        self.db_path = db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    async def init_db(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    link TEXT UNIQUE NOT NULL,
                    status TEXT NOT NULL DEFAULT 'available',
                    sold_to_user_id INTEGER,
                    order_id INTEGER,
                    sold_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    memo TEXT UNIQUE NOT NULL,
                    amount INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    link_id INTEGER,
                    matched_transaction_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    paid_at TIMESTAMP
                )
            """)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_links_status ON links(status)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)")
            await db.commit()
            logger.info("Database initialized at %s", self.db_path)

    # ------------------------------------------------------------------
    # Inventory
    # ------------------------------------------------------------------
    async def add_links(self, links: List[str]) -> int:
        """Inserts new links into inventory, skipping duplicates. Returns count actually inserted."""
        inserted = 0
        async with aiosqlite.connect(self.db_path) as db:
            for link in links:
                link = link.strip()
                if not link:
                    continue
                cursor = await db.execute(
                    "INSERT OR IGNORE INTO links (link, status) VALUES (?, 'available')", (link,)
                )
                if cursor.rowcount:
                    inserted += 1
            await db.commit()
        return inserted

    async def count_available_links(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM links WHERE status = 'available'")
            row = await cursor.fetchone()
            return row[0] if row else 0

    async def get_stock_stats(self) -> Dict[str, int]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT status, COUNT(*) FROM links GROUP BY status"
            )
            rows = await cursor.fetchall()
            stats = {"available": 0, "sold": 0}
            for status, count in rows:
                stats[status] = count
            return stats

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------
    async def create_order(self, user_id: int, username: str, memo: str, amount: int, expires_at: datetime) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "INSERT INTO orders (user_id, username, memo, amount, status, expires_at) VALUES (?, ?, ?, ?, 'pending', ?)",
                (user_id, username, memo, amount, expires_at),
            )
            await db.commit()
            return cursor.lastrowid

    async def get_order(self, order_id: int) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_pending_orders(self) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM orders WHERE status = 'pending' AND expires_at > CURRENT_TIMESTAMP"
            )
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def expire_stale_orders(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE orders SET status = 'expired' WHERE status = 'pending' AND expires_at <= CURRENT_TIMESTAMP"
            )
            await db.commit()
            return cursor.rowcount

    async def try_mark_order_paid(self, order_id: int, matched_transaction_id: str) -> bool:
        """Atomically flips a pending order to paid. Returns True only for the caller that won the race."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE orders SET status = 'paid', matched_transaction_id = ?, paid_at = CURRENT_TIMESTAMP "
                "WHERE id = ? AND status = 'pending'",
                (matched_transaction_id, order_id),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def claim_link_for_order(self, order_id: int, user_id: int) -> Optional[str]:
        """Assigns one available link to an order. Call only right after try_mark_order_paid() returns True."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT id, link FROM links WHERE status = 'available' ORDER BY id LIMIT 1")
            row = await cursor.fetchone()
            if not row:
                logger.error("Order %s marked paid but inventory is empty!", order_id)
                return None
            link_id, link = row["id"], row["link"]
            await db.execute(
                "UPDATE links SET status = 'sold', sold_to_user_id = ?, order_id = ?, sold_at = CURRENT_TIMESTAMP WHERE id = ?",
                (user_id, order_id, link_id),
            )
            await db.execute("UPDATE orders SET link_id = ? WHERE id = ?", (link_id, order_id))
            await db.commit()
            return link

    async def get_delivered_link(self, order_id: int) -> Optional[str]:
        """Returns the link already assigned to a paid order (idempotent re-check/re-delivery)."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT l.link FROM orders o JOIN links l ON l.id = o.link_id WHERE o.id = ?", (order_id,)
            )
            row = await cursor.fetchone()
            return row["link"] if row else None

    async def get_recent_orders(self, limit: int = 20) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


db = Database()
