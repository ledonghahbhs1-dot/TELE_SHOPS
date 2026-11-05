import html
import re
from datetime import datetime, timedelta, timezone

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from utils.delivery import DeliveryWorker
from utils.wolfmod_api import WolfmodAPI

router = Router(name="user_handlers")


def format_vnd(amount):
    return f"{int(amount):,} VND".replace(",", ".")


async def private_shop_button(message, bot):
    identity = await bot.get_me()
    await message.answer(
        "Bấm nút bên dưới để mua hàng và nhận link qua tin nhắn riêng.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Mua link", url=f"https://t.me/{identity.username}?start=shop")
        ]]),
    )


async def send_welcome(bot, user_id, shop):
    product = await shop.product()
    text = f"🎁 <b>{html.escape(product['name'])}</b>\n\nGiá: <b>{format_vnd(product['price_vnd'])}</b> / link\n"
    if product.get("description"):
        text += "\n" + html.escape(product["description"][:1500]) + "\n"
    text += "\nThanh toán qua VietQR. Bot tự gửi link khi hệ thống xác nhận đã nhận tiền."
    buttons = []
    if product["in_stock"]:
        buttons.append([InlineKeyboardButton(text="🛒 Mua ngay", callback_data="buy")])
    else:
        text += "\n\nSản phẩm hiện đã hết hàng."
    buttons.append([InlineKeyboardButton(text="Đơn hàng của tôi", callback_data="my_orders")])
    await bot.send_message(user_id, text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons))


@router.message(Command("start"))
async def cmd_start(message: Message, bot: Bot, shop: WolfmodAPI):
    if message.chat.type != "private":
        await private_shop_button(message, bot)
        return
    await send_welcome(bot, message.from_user.id, shop)


async def create_and_send_order(bot, user, shop, request_id):
    result = await shop.create_order(user.id, user.username or "", request_id)
    order, payment = result["order"], result["payment"]
    expires = datetime.fromisoformat(order["expires_at"].replace("Z", "+00:00"))
    expires = expires.astimezone(timezone(timedelta(hours=7))).strftime("%H:%M %d/%m/%Y")
    code = order["order_code"]
    caption = (
        f"🧾 <b>Đơn hàng {html.escape(code)}</b>\n\n"
        f"Số tiền: <b>{format_vnd(order['amount_vnd'])}</b>\n"
        f"Ngân hàng: {html.escape(payment['bank_name'])}\n"
        f"Số tài khoản: <code>{html.escape(payment['account_no'])}</code>\n"
        f"Chủ tài khoản: {html.escape(payment['account_name'])}\n"
        f"Nội dung chuyển khoản: <code>{html.escape(payment['transfer_note'])}</code>\n\n"
        f"Hạn thanh toán: {expires} (giờ Việt Nam).\n"
        "Chuyển đúng số tiền và giữ nguyên nội dung. Link sẽ được gửi riêng khi thanh toán được xác nhận."
    )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Tôi đã thanh toán — kiểm tra", callback_data=f"check:{code}")
    ]])
    try:
        await bot.send_photo(user.id, photo=payment["qr_url"], caption=caption, reply_markup=keyboard)
    except Exception:
        await bot.send_message(user.id, caption, reply_markup=keyboard)


@router.message(Command("buy"))
async def cmd_buy(message: Message, bot: Bot, shop: WolfmodAPI):
    if message.chat.type != "private":
        await private_shop_button(message, bot)
        return
    await create_and_send_order(bot, message.from_user, shop, f"msg_{message.chat.id}_{message.message_id}")


def is_private_callback(callback):
    return bool(callback.message and callback.message.chat.type == "private" and
                callback.message.chat.id == callback.from_user.id)


@router.callback_query(F.data == "buy")
async def on_buy(callback: CallbackQuery, bot: Bot, shop: WolfmodAPI):
    if not is_private_callback(callback):
        await callback.answer("Vui lòng mở cuộc trò chuyện riêng với bot.", show_alert=True)
        return
    await callback.answer()
    await create_and_send_order(bot, callback.from_user, shop, f"cb_{callback.id}")


async def show_orders(bot, user_id, shop):
    orders = await shop.orders(user_id)
    if not orders:
        await bot.send_message(user_id, "Bạn chưa có đơn hàng nào.")
        return
    labels = {"pending": "Chờ thanh toán", "paid": "Đã thanh toán, đang giao", "delivered": "Đã giao",
              "needs_review": "Đang kiểm tra", "cancelled": "Đã hủy", "refunded": "Đã hoàn tiền"}
    lines = ["<b>Đơn hàng gần đây của bạn</b>\n"]
    buttons = []
    for order in orders:
        code = order["order_code"]
        lines.append(f"<code>{html.escape(code)}</code> · {format_vnd(order['amount_vnd'])} · "
                     f"{labels.get(order['status'], 'Đang xử lý')}")
        if order["status"] in ("pending", "paid"):
            buttons.append([InlineKeyboardButton(text=f"Kiểm tra {code}", callback_data=f"check:{code}")])
    await bot.send_message(user_id, "\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None)


@router.message(Command("orders", "myorders"))
async def cmd_orders(message: Message, bot: Bot, shop: WolfmodAPI):
    if message.chat.type == "private":
        await show_orders(bot, message.from_user.id, shop)


@router.callback_query(F.data == "my_orders")
async def on_my_orders(callback: CallbackQuery, bot: Bot, shop: WolfmodAPI):
    if not is_private_callback(callback):
        await callback.answer("Vui lòng mở cuộc trò chuyện riêng với bot.", show_alert=True)
        return
    await callback.answer()
    await show_orders(bot, callback.from_user.id, shop)


@router.callback_query(F.data.startswith("check:"))
async def on_check(callback: CallbackQuery, bot: Bot, shop: WolfmodAPI, worker: DeliveryWorker):
    if not is_private_callback(callback):
        await callback.answer("Vui lòng mở cuộc trò chuyện riêng với bot.", show_alert=True)
        return
    code = callback.data.split(":", 1)[1]
    if not re.fullmatch(r"TGS?[A-Z0-9]{4,17}", code):
        await callback.answer("Mã đơn không hợp lệ.", show_alert=True)
        return
    await callback.answer("Đang kiểm tra thanh toán…")
    result = await shop.order(code, callback.from_user.id)
    order = result["order"]
    if order["status"] == "paid":
        await worker.run_once(bot)
        result = await shop.order(code, callback.from_user.id)
        order = result["order"]
    if order["status"] == "delivered":
        text = "✅ Đơn đã được giao. Bạn xem tin nhắn chứa link trong cuộc trò chuyện này nhé."
    elif order.get("expired"):
        text = "Đơn đã hết hạn. Vui lòng tạo đơn mới bằng /buy."
    elif order["status"] == "pending":
        text = "Chưa nhận được xác nhận thanh toán. Vui lòng chờ một chút sau khi chuyển tiền."
    else:
        text = "Đơn đang được xử lý. Nếu đã chuyển tiền, bạn vui lòng chờ hoặc liên hệ quản trị viên."
    await bot.send_message(callback.from_user.id, text)
