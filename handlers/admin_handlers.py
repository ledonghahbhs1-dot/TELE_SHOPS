from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from utils.wolfmod_api import WolfmodAPI

router = Router(name="admin_handlers")
router.message.filter(F.chat.type == "private")


@router.message(Command("stock"))
async def cmd_stock(message: Message, shop: WolfmodAPI):
    if not message.from_user or message.from_user.id not in config.OWNER_IDS:
        return
    inventory = await shop.inventory()
    await message.answer(
        f"📦 <b>Kho Wolfmod</b>\nCòn hàng: <b>{inventory['available']}</b>\n"
        f"Đã xuất: <b>{inventory['sold']}</b>"
    )


@router.message(Command("dashboard", "addlinks"))
async def cmd_dashboard(message: Message):
    if not message.from_user or message.from_user.id not in config.OWNER_IDS:
        return
    await message.answer(
        "Nhập link, đổi giá và quản lý đơn tại dashboard Wolfmod. Bot tự dùng chung dữ liệu tại đây.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="Mở dashboard", url=config.WOLFMOD_API_URL + "/m7k9xq2r/dashboard#tg-sales")
        ]]),
    )
