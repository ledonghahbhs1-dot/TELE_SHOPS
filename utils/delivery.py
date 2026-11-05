import asyncio
import html

from aiogram.types import BufferedInputFile

from utils.logger import logger


class DeliveryWorker:
    def __init__(self, shop):
        self.shop = shop
        self.lock = asyncio.Lock()
        self.receipts = {}

    async def run_once(self, bot):
        if self.lock.locked():
            return
        async with self.lock:
            # Retry receipts before requesting work, so a temporary API outage does
            # not cause this running process to send an accepted Telegram message twice.
            for code, (lease, message_id) in list(self.receipts.items()):
                await self.shop.acknowledge(code, lease, message_id)
                del self.receipts[code]
            for _ in range(10):
                delivery = await self.shop.next_delivery()
                if not delivery:
                    return
                code = delivery["order_code"]
                links = "\n".join(item["content"] for item in delivery["items"])
                caption = (
                    f"✅ <b>Thanh toán thành công!</b>\n"
                    f"Đơn <code>{html.escape(code)}</code> — {html.escape(delivery['product_name'])}\n\n"
                )
                if len(caption) + len(html.escape(links)) < 3500:
                    sent = await bot.send_message(
                        delivery["tg_user_id"], caption + f"<code>{html.escape(links)}</code>\n\n"
                        "Link dành riêng cho bạn. Vui lòng không chia sẻ.", disable_web_page_preview=True,
                    )
                else:
                    sent = await bot.send_document(
                        delivery["tg_user_id"], BufferedInputFile(links.encode("utf-8"), filename=f"{code}.txt"),
                        caption=caption,
                    )
                self.receipts[code] = (delivery["lease_token"], sent.message_id)
                await self.shop.acknowledge(code, delivery["lease_token"], sent.message_id)
                del self.receipts[code]
                logger.info("Delivered Wolfmod order %s", code)
