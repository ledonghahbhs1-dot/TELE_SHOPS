import html

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message

import config
from database.db import db
from utils.logger import logger

router = Router(name="admin_handlers")


def is_owner(user) -> bool:
    return bool(user and user.id in config.OWNER_IDS)


@router.message(Command("stock"))
async def cmd_stock(message: Message):
    if not is_owner(message.from_user):
        return
    stats = await db.get_stock_stats()
    text = (
        f"📦 <b>INVENTORY</b>\n\n"
        f"✅ Available: <b>{stats.get('available', 0)}</b>\n"
        f"✔️ Sold: <b>{stats.get('sold', 0)}</b>"
    )
    await message.answer(text, parse_mode="HTML")


@router.message(Command("orders"))
async def cmd_orders(message: Message):
    if not is_owner(message.from_user):
        return
    orders = await db.get_recent_orders(limit=20)
    if not orders:
        await message.answer("No orders yet.")
        return
    lines = ["🧾 <b>RECENT ORDERS</b>\n"]
    for o in orders:
        uname = f"@{o['username']}" if o.get("username") else str(o["user_id"])
        lines.append(
            f"#{o['id']} - {html.escape(uname)} - {o['amount']:,} VND - <b>{o['status'].upper()}</b> "
            f"- memo <code>{html.escape(o['memo'])}</code>"
        )
    await message.answer("\n".join(lines), parse_mode="HTML")


@router.message(Command("addlinks"))
async def cmd_addlinks(message: Message, bot: Bot):
    """
    Adds more links to inventory. Usage:
    - Reply to a .txt file attachment with /addlinks, OR
    - /addlinks <link1>\\n<link2>\\n... (plain text, one per line)
    """
    if not is_owner(message.from_user):
        return

    raw_text = None
    if message.reply_to_message and message.reply_to_message.document:
        doc = message.reply_to_message.document
        if not doc.file_name or not doc.file_name.lower().endswith(".txt"):
            await message.answer("⚠️ Please reply to a .txt file.")
            return
        file = await bot.get_file(doc.file_id)
        buf = await bot.download_file(file.file_path)
        raw_text = buf.read().decode("utf-8", errors="ignore")
    else:
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                "Usage: reply to a .txt file with /addlinks, "
                "or /addlinks <link1>\n<link2>\n..."
            )
            return
        raw_text = parts[1]

    candidate_lines = [line.strip() for line in raw_text.splitlines()]
    links = [line for line in candidate_lines if line.startswith("http")]
    skipped = len(candidate_lines) - len(links)

    if not links:
        await message.answer("⚠️ No valid links found (expected lines starting with http).")
        return

    inserted = await db.add_links(links)
    stats = await db.get_stock_stats()
    logger.info("Owner %s added %s new link(s) (%s duplicates/invalid skipped)", message.from_user.id, inserted, skipped)
    await message.answer(
        f"✅ Added <b>{inserted}</b> new link(s) "
        f"({skipped + (len(links) - inserted)} duplicate/invalid skipped).\n"
        f"📦 Total available now: <b>{stats.get('available', 0)}</b>",
        parse_mode="HTML",
    )
