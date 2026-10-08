import asyncio
import os
from database import init_db,expire_no_answer_orders
from customer_bot import bot as customer_bot, dp as customer_dp
from driver_bot import bot as driver_bot, dp as driver_dp
from admin_bot import bot as admin_bot, dp as admin_dp

async def cleanup_loop():
    while True:
        try:
            expired=expire_no_answer_orders()
            for o in expired:
                try:
                    from aiogram import Bot
                    from aiogram.client.default import DefaultBotProperties
                    from aiogram.enums import ParseMode
                    token=os.getenv("CUSTOMER_BOT_TOKEN","").strip()
                    if token:
                        b=Bot(token,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
                        await b.send_message(o["customer_id"],"❌ 1 daqiqa ichida javob bo‘lmagani uchun buyurtma bekor qilindi.")
                        await b.session.close()
                except Exception:
                    pass
        except Exception as e:
            print("CLEANUP ERROR:",e)
        await asyncio.sleep(5)

async def main():
    init_db()
    await asyncio.gather(
        customer_dp.start_polling(customer_bot),
        driver_dp.start_polling(driver_bot),
        admin_dp.start_polling(admin_bot),
        cleanup_loop(),
    )

if __name__=="__main__": asyncio.run(main())
