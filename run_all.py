import asyncio
from database import init_db
from customer_bot import bot as customer_bot, dp as customer_dp
from driver_bot import bot as driver_bot, dp as driver_dp
from admin_bot import bot as admin_bot, dp as admin_dp

async def main():
    init_db()
    await asyncio.gather(
        customer_dp.start_polling(customer_bot),
        driver_dp.start_polling(driver_bot),
        admin_dp.start_polling(admin_bot),
    )

if __name__ == '__main__':
    asyncio.run(main())
