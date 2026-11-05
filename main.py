import asyncio
from contextlib import suppress

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery

import config
from handlers.admin_handlers import router as admin_router
from handlers.group_handlers import GroupAccessMiddleware, router as group_router
from handlers.user_handlers import router as user_router
from utils.delivery import DeliveryWorker
from utils.logger import logger
from utils.wolfmod_api import WolfmodAPI, WolfmodError


class BackendErrorMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        try:
            return await handler(event, data)
        except WolfmodError as error:
            logger.warning("Wolfmod request failed: status=%s", error.status)
            text = "Cửa hàng tạm thời chưa sẵn sàng. Vui lòng thử lại sau hoặc liên hệ quản trị viên."
            if error.code in ("out_of_stock", "insufficient_stock"):
                text = "Sản phẩm hiện đã hết hàng. Vui lòng quay lại sau."
            elif error.status == 429:
                text = "Bạn thao tác quá nhanh hoặc đang có nhiều đơn chờ. Vui lòng thử lại sau."
            if isinstance(event, CallbackQuery):
                await data["bot"].send_message(event.from_user.id, text)
            else:
                await event.answer(text)


async def delivery_loop(bot, worker):
    while True:
        try:
            await worker.run_once(bot)
        except Exception as error:
            # Do not log API response bodies, credentials, or purchased links.
            logger.warning("Delivery will retry: %s", type(error).__name__)
        await asyncio.sleep(config.POLL_INTERVAL_SEC)


async def main():
    config.validate()
    async with WolfmodAPI(config.WOLFMOD_API_URL, config.WOLFMOD_API_SECRET, config.PRODUCT_SKU) as shop:
        product = await shop.product()
        logger.info("Connected to Wolfmod product %s", product["sku"])
        bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        worker = DeliveryWorker(shop)
        dp = Dispatcher()
        for observer in (dp.message, dp.callback_query, dp.channel_post):
            observer.outer_middleware(GroupAccessMiddleware())
            observer.outer_middleware(BackendErrorMiddleware())
        dp.include_router(group_router)
        dp.include_router(admin_router)
        dp.include_router(user_router)
        task = None
        try:
            identity = await bot.get_me()
            logger.info("Bot @%s is using Wolfmod's shared inventory and orders", identity.username)
            await bot.delete_webhook(drop_pending_updates=False)
            task = asyncio.create_task(delivery_loop(bot, worker))
            await dp.start_polling(
                bot, allowed_updates=dp.resolve_used_update_types(),
                close_bot_session=False, shop=shop, worker=worker,
            )
        finally:
            if task:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
