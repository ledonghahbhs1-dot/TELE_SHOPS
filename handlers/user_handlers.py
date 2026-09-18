import html
from datetime import datetime, timedelta, timezone

from aiogram import Router, F, Bot
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

import config
from database.db import db
from utils.logger import logger
from utils.vietqr import generate_memo, build_qr_url
from utils.sepay_api import fetch_recent_transactions, find_matching_transaction

router = Router(name="user_handlers")


def format_vnd(amount: int) -> str:
    return f"{amount:,} VND".replace(",", ".")


async def send_welcome(bot: Bot, chat_id: int):
    stock = await db.count_available_links()
    text = (
        f"🎁 <b>{html.escape(config.PRODUCT_NAME)}</b>\n\n"
        f"💰 <b>Price:</b> {format_vnd(config.PRICE_VND)} (~$1 USD) / link\n"
        f"📦 <b>In stock:</b> {stock} link(s)\n\n"
        f"Pay by bank transfer (VietQR), your link is delivered <b>automatically</b> "
        f"right after payment is confirmed (usually within a minute)."
    )
    if stock > 0:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=f"🛒 Buy Now - {format_vnd(config.PRICE_VND)}", callback_data="buy")]
        ])
    else:
        text += "\n\n⚠️ <b>Out of stock right now</b> - please check back later."
        keyboard = None
    await bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=keyboard)


@router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot):
    if message.chat.type != "private":
        return
    await send_welcome(bot, message.chat.id)


@router.message(Command("buy"))
async def cmd_buy(message: Message, bot: Bot):
    if message.chat.type != "private":
        return
    await create_and_send_order(bot, message.chat.id, message.from_user)


@router.callback_query(F.data == "buy")
async def on_buy_button(callback: CallbackQuery, bot: Bot):
    await callback.answer()
    await create_and_send_order(bot, callback.message.chat.id, callback.from_user)


async def create_and_send_order(bot: Bot, chat_id: int, user):
    stock = await db.count_available_links()
    if stock <= 0:
        await bot.send_message(chat_id, "⚠️ Sorry, we're out of stock right now. Please check back later.")
        return

    memo = generate_memo()
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=config.ORDER_EXPIRE_MINUTES)
    order_id = await db.create_order(
        user_id=user.id,
        username=user.username or "",
        memo=memo,
        amount=config.PRICE_VND,
        expires_at=expires_at,
    )
    qr_url = build_qr_url(config.PRICE_VND, memo)

    caption = (
        f"🎁 <b>ORDER #{order_id}</b>\n\n"
        f"💰 <b>Amount:</b> <code>{format_vnd(config.PRICE_VND)}</code>\n"
        f"🏦 <b>Bank:</b> {html.escape(config.BANK_NAME)}\n"
        f"👤 <b>Account Name:</b> {html.escape(config.ACCOUNT_NAME)}\n"
        f"🔢 <b>Account Number:</b> <code>{html.escape(config.ACCOUNT_NO)}</code>\n"
        f"📝 <b>Transfer Content (required):</b> <code>{html.escape(memo)}</code>\n\n"
        f"⚠️ Scan the QR with your banking app, or transfer manually using the info above "
        f"(the transfer content <b>must</b> match exactly, or the payment can't be matched). "
        f"Your link is delivered <b>automatically</b> right after payment is confirmed.\n\n"
        f"⏳ <i>This order expires in {config.ORDER_EXPIRE_MINUTES} minutes.</i>"
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 I've Paid - Check Now", callback_data=f"check:{order_id}")]
    ])

    try:
        await bot.send_photo(chat_id=chat_id, photo=qr_url, caption=caption, parse_mode="HTML", reply_markup=keyboard)
    except Exception as e:
        logger.error("Failed to send QR photo for order %s: %s", order_id, e)
        await bot.send_message(chat_id, caption, parse_mode="HTML", reply_markup=keyboard)

    logger.info("Order #%s created for user %s (memo=%s, amount=%s)", order_id, user.id, memo, config.PRICE_VND)


async def deliver_order(bot: Bot, order_id: int, chat_id: int, link: str):
    text = (
        f"✅ <b>PAYMENT CONFIRMED!</b>\n\n"
        f"🎁 <b>Order #{order_id}</b> - here is your link:\n"
        f"<code>{html.escape(link)}</code>\n\n"
        f"⚠️ <i>This link is for you only and can only be used once. Do not share it.</i>"
    )
    await bot.send_message(chat_id, text, parse_mode="HTML", disable_web_page_preview=True)
    logger.info("Delivered link for order #%s to chat %s", order_id, chat_id)


async def try_confirm_order(bot: Bot, order_id: int) -> str:
    """
    Checks recent SePay transactions for a match against this order and, if found,
    marks it paid + delivers the link. Returns a short status string for the caller
    to relay back to the user: "delivered" | "already_delivered" | "not_found" | "no_match".
    """
    order = await db.get_order(order_id)
    if not order:
        return "not_found"

    if order["status"] == "paid":
        link = await db.get_delivered_link(order_id)
        if link:
            return "already_delivered"

    if order["status"] != "pending":
        return "no_match"

    transactions = await fetch_recent_transactions()
    match = find_matching_transaction(transactions, order["memo"], order["amount"])
    if not match:
        return "no_match"

    won = await db.try_mark_order_paid(order_id, match["id"])
    if not won:
        # Someone else (the background poller) already confirmed it a moment ago.
        return "already_delivered"

    link = await db.claim_link_for_order(order_id, order["user_id"])
    if not link:
        logger.error("Order %s paid but no link available to deliver!", order_id)
        await bot.send_message(
            order["user_id"],
            "✅ Your payment was received, but we're temporarily out of stock. "
            "An admin will send your link shortly - sorry for the inconvenience!",
        )
        return "delivered"

    await deliver_order(bot, order_id, order["user_id"], link)
    return "delivered"


async def poll_pending_orders(bot: Bot):
    """Called periodically from main.py: batch-checks all pending orders against recent SePay transactions."""
    expired = await db.expire_stale_orders()
    if expired:
        logger.info("Expired %s stale order(s)", expired)

    pending = await db.get_pending_orders()
    if not pending:
        return

    transactions = await fetch_recent_transactions()
    if not transactions:
        return

    for order in pending:
        match = find_matching_transaction(transactions, order["memo"], order["amount"])
        if not match:
            continue
        won = await db.try_mark_order_paid(order["id"], match["id"])
        if not won:
            continue
        link = await db.claim_link_for_order(order["id"], order["user_id"])
        if not link:
            logger.error("Order %s paid but no link available to deliver!", order["id"])
            continue
        try:
            await deliver_order(bot, order["id"], order["user_id"], link)
        except Exception as e:
            logger.error("Failed to deliver order %s during background poll: %s", order["id"], e)


@router.callback_query(F.data.startswith("check:"))
async def on_check_button(callback: CallbackQuery, bot: Bot):
    order_id = int(callback.data.split(":", 1)[1])
    await callback.answer("🔍 Checking payment...")

    result = await try_confirm_order(bot, order_id)
    if result in ("delivered", "already_delivered"):
        await callback.answer("✅ Payment confirmed! Check the message above for your link.", show_alert=True)
    elif result == "not_found":
        await callback.answer("❌ Order not found.", show_alert=True)
    else:
        order = await db.get_order(order_id)
        if order and order["status"] == "expired":
            await callback.answer("⌛ This order has expired. Please create a new one with /buy.", show_alert=True)
        else:
            await callback.answer("⏳ Payment not received yet. Please wait a minute after transferring and try again.", show_alert=True)
