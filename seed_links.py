"""
One-off inventory seeding script - run manually once (or whenever you have a new
batch of links to add): `python seed_links.py [path/to/file.txt]`

Reads a newline-delimited .txt file of links, skips blank/invalid lines (anything
not starting with "http") and duplicates, then inserts the rest into the database.
Not imported or run by the bot itself at runtime.
"""

import asyncio
import sys

import config
from database.db import db


async def main():
    path = sys.argv[1] if len(sys.argv) > 1 else config.SEED_LINKS_FILE
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw_lines = [line.strip() for line in f.readlines()]
    except FileNotFoundError:
        print(f"File not found: {path}")
        return

    links = [line for line in raw_lines if line.startswith("http")]
    skipped = len(raw_lines) - len(links)

    await db.init_db()
    inserted = await db.add_links(links)
    stats = await db.get_stock_stats()

    print(f"Read {len(raw_lines)} line(s) from {path}")
    print(f"Skipped {skipped} blank/invalid line(s), {len(links) - inserted} duplicate(s)")
    print(f"Inserted {inserted} new link(s)")
    print(f"Total available now: {stats.get('available', 0)} | sold: {stats.get('sold', 0)}")


if __name__ == "__main__":
    asyncio.run(main())
