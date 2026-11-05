import asyncio
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import config
from database.db import db
from utils.logger import logger
from handlers.admin_handlers import router as admin_router
from handlers.group_handlers import router as group_router, GroupAccessMiddleware
from handlers.user_handlers import router as user_router, poll_pending_orders


async def payment_poll_loop(bot: Bot):
    while True:
        await asyncio.sleep(config.POLL_INTERVAL_SEC)
        try:
            await poll_pending_orders(bot)
        except Exception as e:
            logger.error("Error in payment poll loop: %s", e)


async def main():
    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN is missing! Set it in .env or Railway environment variables.")
        print("\n[ERROR] Please configure BOT_TOKEN in .env or Railway environment variables!\n")
        return

    logger.info("Starting TELE_SHOP bot...")

    await db.init_db()

    bot = Bot(token=config.BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    for observer in (dp.message, dp.callback_query, dp.channel_post):
        observer.outer_middleware(GroupAccessMiddleware())

    dp.include_router(group_router)
    dp.include_router(admin_router)
    dp.include_router(user_router)

    bot_user = await bot.get_me()
    logger.info("Bot authenticated: @%s (%s)", bot_user.username, bot_user.id)

    stats = await db.get_stock_stats()
    logger.info("Inventory: %s available, %s sold", stats.get("available", 0), stats.get("sold", 0))

    asyncio.create_task(payment_poll_loop(bot))

    print("\n" + "=" * 50)
    print(f"🛍️  TELE_SHOP BOT IS RUNNING!")
    print(f"• Username : @{bot_user.username}")
    print(f"• Product  : {config.PRODUCT_NAME}")
    print(f"• Price    : {config.PRICE_VND:,} VND")
    print(f"• Stock    : {stats.get('available', 0)} available")
    print("=" * 50 + "\n")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        logger.info("Bot stopped gracefully.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Application exited by user.")
