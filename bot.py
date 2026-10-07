import os
import asyncio

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

TOKEN = os.getenv("BOT_TOKEN")

dp = Dispatcher()


def main_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 YO‘LOVCHI",
                    callback_data="passenger"
                ),
                InlineKeyboardButton(
                    text="🚕 HAYDOVCHI",
                    callback_data="driver"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📦 DASTAVKA",
                    callback_data="delivery"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📩 TAKLIF VA MUROJAATLAR",
                    callback_data="support"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🌐 TIL",
                    callback_data="language"
                )
            ]
        ]
    )


@dp.message(CommandStart())
async def start(message: types.Message):

    text = (
        "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
        "📍 <b>OBLIQ ↔ ANGREN</b>\n\n"
        "Xush kelibsiz!\n\n"
        "<b>Kerakli bo‘limni tanlang:</b>"
    )

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=main_menu()
    )


@dp.callback_query(lambda c: c.data == "passenger")
async def passenger(callback: types.CallbackQuery):

    await callback.answer()

    await callback.message.edit_text(
        "👤 <b>YO‘LOVCHI</b>\n\n"
        "🚕 Taksi buyurtma qilish\n"
        "📍 Yo‘nalish: OBLIQ ↔ ANGREN\n\n"
        "Keyingi bosqichda:\n"
        "📍 Joylashuv yuborasiz\n"
        "🏁 Qayerga borishingizni yozasiz\n"
        "💰 Narxni o‘zingiz kiritasiz\n"
        "🚕 Haydovchi topiladi.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🚕 TAKSI BUYURTMA QILISH",
                        callback_data="order_taxi"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 ORQAGA",
                        callback_data="back"
                    )
                ]
            ]
        )
    )


@dp.callback_query(lambda c: c.data == "driver")
async def driver(callback: types.CallbackQuery):

    await callback.answer()

    await callback.message.edit_text(
        "🚕 <b>HAYDOVCHI</b>\n\n"
        "📍 Yo‘nalish: <b>OBLIQ ↔ ANGREN</b>\n\n"
        "🟢 ONLINE / ⚪ OFFLINE\n"
        "📋 Yangi buyurtmalar\n"
        "🚕 Faol buyurtmalar\n"
        "📜 Buyurtmalar tarixi\n"
        "💰 Daromad\n"
        "⭐ Reyting\n"
        "👤 Profil\n\n"
        "Haydovchi tizimi tayyorlanmoqda.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📝 RO‘YXATDAN O‘TISH",
                        callback_data="driver_register"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 ORQAGA",
                        callback_data="back"
                    )
                ]
            ]
        )
    )


@dp.callback_query(lambda c: c.data == "delivery")
async def delivery(callback: types.CallbackQuery):

    await callback.answer()

    await callback.message.edit_text(
        "📦 <b>DASTAVKA</b>\n\n"
        "📍 Qayerdan → 🏁 Qayerga\n"
        "💰 Yetkazib berish narxini kiriting.\n\n"
        "📍 <b>OBLIQ ↔ ANGREN</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📦 DASTAVKA BUYURTMA QILISH",
                        callback_data="order_delivery"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 ORQAGA",
                        callback_data="back"
                    )
                ]
            ]
        )
    )


@dp.callback_query(lambda c: c.data == "support")
async def support(callback: types.CallbackQuery):

    await callback.answer()

    await callback.message.edit_text(
        "📩 <b>TAKLIF VA MUROJAATLAR</b>\n\n"
        "Taklifingiz, savolingiz, shikoyatingiz "
        "yoki muammoingizni yuborishingiz mumkin.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔙 ORQAGA",
                        callback_data="back"
                    )
                ]
            ]
        )
    )


@dp.callback_query(lambda c: c.data == "language")
async def language(callback: types.CallbackQuery):

    await callback.answer()

    await callback.message.edit_text(
        "🌐 <b>TILNI TANLANG</b>\n\n"
        "🇺🇿 O‘zbekcha\n"
        "🇺🇿 Ўзбекча\n"
        "🇷🇺 Русский\n"
        "🇬🇧 English",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🇺🇿 O‘zbekcha",
                        callback_data="lang_uz"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🇺🇿 Ўзбекча",
                        callback_data="lang_uz_cyr"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🇷🇺 Русский",
                        callback_data="lang_ru"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🇬🇧 English",
                        callback_data="lang_en"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔙 ORQAGA",
                        callback_data="back"
                    )
                ]
            ]
        )
    )


@dp.callback_query(lambda c: c.data == "back")
async def back(callback: types.CallbackQuery):

    await callback.answer()

    await callback.message.edit_text(
        "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
        "📍 <b>OBLIQ ↔ ANGREN</b>\n\n"
        "Kerakli bo‘limni tanlang:",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


@dp.callback_query()
async def other_buttons(callback: types.CallbackQuery):

    await callback.answer(
        "Bu funksiya keyingi bosqichda ishga tushadi."
    )


async def main():

    if not TOKEN:
        raise RuntimeError("BOT_TOKEN topilmadi!")

    bot = Bot(token=TOKEN)

    print("🚕 TAXI BOR MI? bot ishga tushdi!")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
