import os
import asyncio

from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


TOKEN = os.getenv("BOT_TOKEN")

dp = Dispatcher()


# =========================
# ASOSIY MENYU
# =========================

def main_menu():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="👤 YO‘LOVCHI"),
                KeyboardButton(text="🚕 HAYDOVCHI")
            ],
            [
                KeyboardButton(text="📦 DASTAVKA"),
                KeyboardButton(text="📩 TAKLIF VA MUROJAATLAR")
            ],
            [
                KeyboardButton(text="🌐 TIL")
            ]
        ],
        resize_keyboard=True,
        is_persistent=True,
        one_time_keyboard=False
    )


# =========================
# START
# =========================

@dp.message(CommandStart())
async def start(message: types.Message):

    text = (
        "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
        "📍 <b>OBLIQ ↔️ ANGREN</b>\n\n"
        "Xush kelibsiz!\n"
        "Kerakli bo‘limni tanlang:"
    )

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=main_menu()
    )


# =========================
# YO‘LOVCHI
# =========================

@dp.message(lambda message: message.text == "👤 YO‘LOVCHI")
async def passenger(message: types.Message):

    await message.answer(
        "👤 <b>YO‘LOVCHI</b>\n\n"
        "🚕 Taksi buyurtma qilish\n"
        "📍 Yo‘nalish: OBLIQ ↔️ ANGREN\n\n"
        "Keyingi bosqichda:\n"
        "📍 Joylashuv yuborasiz\n"
        "🏁 Qayerga borishingizni yozasiz\n"
        "💰 Narxni o‘zingiz kiritasiz\n"
        "🚕 Haydovchi topiladi.",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


# =========================
# HAYDOVCHI
# =========================

@dp.message(lambda message: message.text == "🚕 HAYDOVCHI")
async def driver(message: types.Message):

    await message.answer(
        "🚕 <b>HAYDOVCHI PANELI</b>\n\n"
        "📍 Yo‘nalish: <b>OBLIQ ↔️ ANGREN</b>\n\n"
        "🟢 ONLINE / ⚪️ OFFLINE\n"
        "📋 Yangi buyurtmalar\n"
        "🚕 Faol buyurtmalar\n"
        "📜 Buyurtmalar tarixi\n"
        "💰 Daromad\n"
        "⭐️ Reyting\n"
        "👤 Profil\n\n"
        "Haydovchi ro‘yxatdan o‘tishi keyingi bosqichda ishga tushiriladi.",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


# =========================
# DASTAVKA
# =========================

@dp.message(lambda message: message.text == "📦 DASTAVKA")
async def delivery(message: types.Message):

    await message.answer(
        "📦 <b>DASTAVKA</b>\n\n"
        "📍 Qayerdan → 🏁 Qayerga\n"
        "💰 Yetkazib berish narxini kiriting.\n\n"
        "Yo‘nalish:\n"
        "📍 <b>OBLIQ ↔️ ANGREN</b>\n\n"
        "Dastavka buyurtmasi keyingi bosqichda ishga tushiriladi.",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


# =========================
# TAKLIF VA MUROJAATLAR
# =========================

@dp.message(lambda message: message.text == "📩 TAKLIF VA MUROJAATLAR")
async def support(message: types.Message):

    await message.answer(
        "📩 <b>TAKLIF VA MUROJAATLAR</b>\n\n"
        "Taklifingiz, savolingiz, shikoyatingiz "
        "yoki muammoingizni shu yerga yozing.\n\n"
        "✍️ Xabaringizni yuboring.",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


# =========================
# TIL
# =========================

@dp.message(lambda message: message.text == "🌐 TIL")
async def language(message: types.Message):

    await message.answer(
        "🌐 <b>TILNI TANLANG</b>\n\n"
        "🇺🇿 O‘zbekcha\n"
        "🇺🇿 Ўзбекча\n"
        "🇷🇺 Русский\n"
        "🇬🇧 English",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


# =========================
# BOTNI ISHGA TUSHIRISH
# =========================

async def main():

    if not TOKEN:
        raise RuntimeError("BOT_TOKEN topilmadi!")

    bot = Bot(token=TOKEN)

    print("🚕 TAXI BOR MI? bot ishga tushdi!")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
