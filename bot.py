import os
import asyncio

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import ReplyKeyboardBuilder


TOKEN = os.getenv("BOT_TOKEN")

dp = Dispatcher(storage=MemoryStorage())


# =========================
# HOLATLAR
# =========================

class TaxiOrder(StatesGroup):
    waiting_location = State()
    waiting_route = State()
    waiting_price = State()


# =========================
# ASOSIY MENYU
# =========================

def main_menu():
    builder = ReplyKeyboardBuilder()

    builder.button(text="👤 YO‘LOVCHI")
    builder.button(text="🚕 HAYDOVCHI")
    builder.button(text="📦 DASTAVKA")
    builder.button(text="📩 TAKLIF VA MUROJAATLAR")
    builder.button(text="🌐 TIL")

    builder.adjust(2, 2, 1)

    return builder.as_markup(
        resize_keyboard=True
    )


# =========================
# YO‘LOVCHI MENYU
# =========================

def passenger_menu():
    builder = ReplyKeyboardBuilder()

    builder.button(text="🚕 TAKSI BUYURTMA QILISH")
    builder.button(text="📜 BUYURTMALAR TARIXI")
    builder.button(text="👤 PROFIL")
    builder.button(text="⬅️ ORQAGA")

    builder.adjust(1, 2, 1)

    return builder.as_markup(
        resize_keyboard=True
    )


# =========================
# LOCATION TUGMASI
# =========================

def location_keyboard():
    builder = ReplyKeyboardBuilder()

    builder.button(
        text="📍 JOYLASHUVIMNI YUBORISH",
        request_location=True
    )

    builder.button(text="⬅️ ORQAGA")

    builder.adjust(1, 1)

    return builder.as_markup(
        resize_keyboard=True,
        one_time_keyboard=True
    )


# =========================
# START
# =========================

@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):

    await state.clear()

    text = (
        "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
        "📍 OBLIQ ↔ ANGGREN\n\n"
        "Xush kelibsiz!\n\n"
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

@dp.message(F.text == "👤 YO‘LOVCHI")
async def passenger(message: types.Message, state: FSMContext):

    await state.clear()

    text = (
        "👤 <b>YO‘LOVCHI</b>\n\n"
        "🚕 Taksi buyurtma qilish\n"
        "📜 Buyurtmalar tarixi\n"
        "👤 Profil\n\n"
        "Kerakli xizmatni tanlang:"
    )

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=passenger_menu()
    )


# =========================
# TAKSI BUYURTMA QILISH
# =========================

@dp.message(F.text == "🚕 TAKSI BUYURTMA QILISH")
async def taxi_order(message: types.Message, state: FSMContext):

    await state.set_state(TaxiOrder.waiting_location)

    text = (
        "🚕 <b>TAKSI BUYURTMA QILISH</b>\n\n"
        "📍 Avval hozirgi joylashuvingizni yuboring.\n\n"
        "Bu haydovchiga sizni topishga yordam beradi."
    )

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=location_keyboard()
    )


# =========================
# LOCATION QABUL QILISH
# =========================

@dp.message(
    TaxiOrder.waiting_location,
    F.location
)
async def get_location(
    message: types.Message,
    state: FSMContext
):

    latitude = message.location.latitude
    longitude = message.location.longitude

    await state.update_data(
        latitude=latitude,
        longitude=longitude
    )

    await state.set_state(TaxiOrder.waiting_route)

    text = (
        "✅ <b>Joylashuvingiz qabul qilindi.</b>\n\n"
        "Endi safar manzilini yozing:\n\n"
        "📍 Qayerdan → 🏁 Qayerga\n\n"
        "Masalan:\n"
        "<code>Versal Dreams → Xakkarmon</code>"
    )

    await message.answer(
        text,
        parse_mode="HTML"
    )


# =========================
# QAYERDAN → QAYERGA
# =========================

@dp.message(
    TaxiOrder.waiting_route,
    F.text
)
async def get_route(
    message: types.Message,
    state: FSMContext
):

    route = message.text.strip()

    if route == "⬅️ ORQAGA":
        await state.clear()

        await message.answer(
            "Asosiy menyu:",
            reply_markup=main_menu()
        )

        return

    if len(route) < 3:
        await message.answer(
            "⚠️ Manzilni to‘liqroq yozing.\n\n"
            "Masalan:\n"
            "Versal Dreams → Xakkarmon"
        )
        return

    await state.update_data(
        route=route
    )

    await state.set_state(TaxiOrder.waiting_price)

    await message.answer(
        "💰 <b>Safar narxini o‘zingiz kiriting.</b>\n\n"
        "Masalan: <code>30000</code>\n\n"
        "Faqat summani yozing.",
        parse_mode="HTML"
    )


# =========================
# NARX
# =========================

@dp.message(
    TaxiOrder.waiting_price,
    F.text
)
async def get_price(
    message: types.Message,
    state: FSMContext
):

    price_text = message.text.strip()

    if not price_text.isdigit():
        await message.answer(
            "⚠️ Narxni faqat raqam bilan yozing.\n\n"
            "Masalan: <code>30000</code>",
            parse_mode="HTML"
        )
        return

    price = int(price_text)

    data = await state.get_data()

    route = data.get("route")

    text = (
        "🚕 <b>BUYURTMA TAYYOR</b>\n\n"
        "📍 <b>Yo‘nalish:</b>\n"
        f"{route}\n\n"
        f"💰 <b>Narx:</b> {price:,} so‘m\n\n"
        "Buyurtmani tasdiqlaysizmi?"
    )

    builder = ReplyKeyboardBuilder()

    builder.button(text="✅ BUYURTMA BERISH")
    builder.button(text="✏️ O‘ZGARTIRISH")
    builder.button(text="❌ BEKOR QILISH")

    builder.adjust(1)

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=builder.as_markup(
            resize_keyboard=True
        )
    )


# =========================
# BUYURTMA TASDIQLASH
# =========================

@dp.message(F.text == "✅ BUYURTMA BERISH")
async def confirm_order(
    message: types.Message,
    state: FSMContext
):

    data = await state.get_data()

    route = data.get("route")
    price = data.get("price", "—")

    await message.answer(
        "🚕 <b>BUYURTMA QABUL QILINDI!</b>\n\n"
        f"📍 {route}\n"
        f"💰 {price}\n\n"
        "🔎 Haydovchi qidirilmoqda...",
        parse_mode="HTML",
        reply_markup=main_menu()
    )

    await state.clear()


# =========================
# BEKOR QILISH
# =========================

@dp.message(F.text == "❌ BEKOR QILISH")
async def cancel_order(
    message: types.Message,
    state: FSMContext
):

    await state.clear()

    await message.answer(
        "❌ Buyurtma bekor qilindi.",
        reply_markup=main_menu()
    )


# =========================
# ORQAGA
# =========================

@dp.message(F.text == "⬅️ ORQAGA")
async def back(message: types.Message, state: FSMContext):

    await state.clear()

    await message.answer(
        "Asosiy menyu:",
        reply_markup=main_menu()
    )


# =========================
# HAYDOVCHI
# =========================

@dp.message(F.text == "🚕 HAYDOVCHI")
async def driver(message: types.Message):

    await message.answer(
        "🚕 <b>HAYDOVCHI PANELI</b>\n\n"
        "📍 Yo‘nalish: OBLIQ ↔ ANGGREN\n\n"
        "🟢 ONLINE / ⚪ OFFLINE\n"
        "📋 Yangi buyurtmalar\n"
        "🚕 Faol buyurtmalar\n"
        "📜 Buyurtmalar tarixi\n"
        "💰 Daromad\n"
        "⭐ Reyting\n"
        "👤 Profil",
        parse_mode="HTML"
    )


# =========================
# DASTAVKA
# =========================

@dp.message(F.text == "📦 DASTAVKA")
async def delivery(message: types.Message):

    await message.answer(
        "📦 <b>DASTAVKA</b>\n\n"
        "Tez orada dastavka buyurtma qilish tizimi ishga tushadi.",
        parse_mode="HTML"
    )


# =========================
# TAKLIF VA MUROJAATLAR
# =========================

@dp.message(F.text == "📩 TAKLIF VA MUROJAATLAR")
async def support(message: types.Message):

    await message.answer(
        "📩 <b>TAKLIF VA MUROJAATLAR</b>\n\n"
        "Taklifingiz, savolingiz yoki muammoingizni yozing.",
        parse_mode="HTML"
    )


# =========================
# TIL
# =========================

@dp.message(F.text == "🌐 TIL")
async def language(message: types.Message):

    await message.answer(
        "🌐 <b>TILNI TANLANG</b>\n\n"
        "🇺🇿 O‘zbekcha\n"
        "🇺🇿 Ўзбекча\n"
        "🇷🇺 Русский\n"
        "🇬🇧 English",
        parse_mode="HTML"
    )


# =========================
# ISHGA TUSHIRISH
# =========================

async def main():

    if not TOKEN:
        raise RuntimeError("BOT_TOKEN topilmadi")

    bot = Bot(token=TOKEN)

    print("🚕 TAXI BOR MI? bot ishga tushdi!")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
