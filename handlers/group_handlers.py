"""Restrict group access while keeping private customer conversations available."""

from aiogram import BaseMiddleware, Bot, Router
from aiogram.enums import ChatMemberStatus, ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import ChatMemberUpdated, Message

import config
from utils.logger import logger

router = Router(name="group_handlers")


async def leave_unapproved_chat(bot: Bot, chat_id: int):
    # Leave before notifying owners, even if they have never started the bot.
    await bot.leave_chat(chat_id)
    logger.warning("Left unapproved chat %s", chat_id)
    for owner_id in config.OWNER_IDS:
        try:
            await bot.send_message(
                owner_id,
                f"Bot đã rời nhóm chưa được phép: <code>{chat_id}</code>.\n"
                "Nếu đây là nhóm của bạn, thêm ID này vào ALLOWED_GROUP_IDS "
                "trên Railway, redeploy, sau đó thêm lại bot vào nhóm.",
            )
        except TelegramAPIError:
            logger.warning("Could not notify owner %s about chat %s", owner_id, chat_id)


class GroupAccessMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        # Messages, channel posts and callbacks all pass through the same gate.
        chat = getattr(event, "chat", None)
        if chat is None:
            chat = getattr(getattr(event, "message", None), "chat", None)
        if chat and chat.type != ChatType.PRIVATE:
            if chat.id not in config.ALLOWED_GROUP_IDS:
                await leave_unapproved_chat(data["bot"], chat.id)
                return
        return await handler(event, data)


@router.my_chat_member()
async def on_bot_membership(event: ChatMemberUpdated, bot: Bot):
    if event.chat.type == ChatType.PRIVATE:
        return
    if event.new_chat_member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        return
    if event.chat.id not in config.ALLOWED_GROUP_IDS:
        await leave_unapproved_chat(bot, event.chat.id)


@router.message(Command("chatid"))
async def cmd_chatid(message: Message):
    if message.from_user and message.from_user.id in config.OWNER_IDS:
        await message.answer(f"Chat ID: <code>{message.chat.id}</code>")
