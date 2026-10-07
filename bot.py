import os
import asyncio

from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import ReplyKeyboardBuilder


TOKEN = os.getenv("BOT_TOKEN")

dp = Dispatcher()


# =========================
# HOLATLAR
# =========================

class PassengerOrder(StatesGroup):
    waiting_location = State()
    waiting_route = State()
    waiting_passengers = State()
    waiting_price = State()
    confirmation = State()


class DeliveryOrder(StatesGroup):
    waiting_location = State()
    waiting_route = State()
    waiting_price = State()
    confirmation = State()


class SupportState(StatesGroup):
    waiting_message = State()


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
# YO‘LOVCHI MENYUSI
# =========================

def passenger_menu():
    builder = ReplyKeyboardBuilder()

    builder.button(text="🚕 TAKSI BUYURTMA QILISH")
    builder.button(text="📜 BUYURTMALAR TARIXI")
    builder.button(text="👤 PROFIL")
    builder.button(text="↩️ ORQAGA")

    builder.adjust(1, 2, 1)

    return builder.as_markup(
        resize_keyboard=True
    )


# =========================
# GPS TUGMASI
# =========================

def location_keyboard():
    builder = ReplyKeyboardBuilder()

    builder.button(
        text="📍 JOYLASHUVIMNI YUBORISH",
        request_location=True
    )

    builder.button(text="↩️ ORQAGA")

    builder.adjust(1, 1)

    return builder.as_markup(
        resize_keyboard=True
    )


# =========================
# YO‘LOVCHILAR SONI
# =========================

def passenger_count_keyboard():
    builder = ReplyKeyboardBuilder()

    for number in range(1, 8):
        builder.button(text=f"👤 {number}")

    builder.button(text="↩️ ORQAGA")

    builder.adjust(4, 3, 1)

    return builder.as_markup(
        resize_keyboard=True
    )


# =========================
# TASDIQLASH
# =========================

def confirmation_keyboard():
    builder = ReplyKeyboardBuilder()

    builder.button(text="✅ BUYURTMA BERISH")
    builder.button(text="✏️ O‘ZGARTIRISH")
    builder.button(text="❌ BEKOR QILISH")

    builder.adjust(1)

    return builder.as_markup(
        resize_keyboard=True
    )


# =========================
# /START
# =========================

@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):

    await state.clear()

    text = (
        "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
        "📍 <b>OBLIQ ↔ ANGGREN</b>\n\n"
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
# TAKSI BUYURTMA BOSHLASH
# =========================

@dp.message(F.text == "🚕 TAKSI BUYURTMA QILISH")
async def start_taxi_order(
    message: types.Message,
    state: FSMContext
):

    await state.set_state(
        PassengerOrder.waiting_location
    )

    text = (
        "🚕 <b>TAKSI BUYURTMA QILISH</b>\n\n"
        "📍 Yo‘nalish: <b>OBLIQ ↔ ANGGREN</b>\n\n"
        "Avval joylashuvingizni yuboring.\n\n"
        "👇 Pastdagi tugmani bosing:"
    )

    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=location_keyboard()
    )


# =========================
# GPS QABUL QILISH
# =========================

@dp.message(
    PassengerOrder.waiting_location,
    F.location
)
async def passenger_location(
    message: types.Message,
    state: FSMContext
):

    location = message.location

    await state.update_data(
        latitude=location.latitude,
        longitude=location.longitude
    )

    await state.set_state(
        PassengerOrder.waiting_route
    )

    text = (
        "✅ <b>Joylashuvingiz qabul qilindi.</b>\n\n"
        "Endi safar manzilini yozing:\n\n"
        "📍 <b>Qayerdan → 🏁 Qayerga</b>\n\n"
        "Masalan:\n"
        "<code>Versal Dreams → Xakkarmon</code>\n\n"
        "Yoki:\n"
        "<code>Obliqdan Angren markaziga</code>"
    )

    await message.answer(
        text,
        parse_mode="HTML"
    )


# =========================
# QAYERDAN → QAYERGA
# =========================

@dp.message(
    PassengerOrder.waiting_route,
    F.text
)
async def passenger_route(
    message: types.Message,
    state: FSMContext
):

    if message.text == "↩️ ORQAGA":

        await state.clear()

        await message.answer(
            "Asosiy menyu:",
            reply_markup=main_menu()
        )

        return

    route = message.text.strip()

    if len(route) < 3:

        await message.answer(
            "⚠️ Iltimos, qayerdan va qayerga borishingizni yozing.\n\n"
            "Masalan:\n"
            "Versal Dreams → Xakkarmon"
        )

        return

    await state.update_data(
        route=route
    )

    await state.set_state(
        PassengerOrder.waiting_passengers
    )

    await message.answer(
        "👥 Necha kishi boradi?\n\n"
        "1 dan 7 kishigacha tanlang:",
        reply_markup=passenger_count_keyboard()
    )


# =========================
# YO‘LOVCHILAR SONI
# =========================

@dp.message(
    PassengerOrder.waiting_passengers,
    F.text
)
async def passenger_count(
    message: types.Message,
    state: FSMContext
):

    text = message.text

    if text == "↩️ ORQAGA":

        await state.clear()

        await message.answer(
            "Asosiy menyu:",
            reply_markup=main_menu()
        )

        return

    if not text.startswith("👤"):

        await message.answer(
            "⚠️ Iltimos, 1–7 kishidan birini tanlang."
        )

        return

    try:
        count = int(
            text.replace("👤", "").strip()
        )
    except ValueError:

        await message.answer(
            "⚠️ Noto‘g‘ri tanlov."
        )

        return

    await state.update_data(
        passengers=count
    )

    await state.set_state(
        PassengerOrder.waiting_price
    )

    await message.answer(
        "💰 <b>Safar narxini kiriting.</b>\n\n"
        "Masalan:\n"
        "<code>30000</code>\n\n"
        "Faqat so‘m miqdorini yozing.",
        parse_mode="HTML"
    )


# =========================
# NARX
# =========================

@dp.message(
    PassengerOrder.waiting_price,
    F.text
)
async def passenger_price(
    message: types.Message,
    state: FSMContext
):

    if message.text == "↩️ ORQAGA":

        await state.clear()

        await message.answer(
            "Asosiy menyu:",
            reply_markup=main_menu()
        )

        return

    price_text = message.text.replace(
        " ", ""
    ).replace(
        ",", ""
    )

    if not price_text.isdigit():

        await message.answer(
            "⚠️ Narxni faqat raqam bilan kiriting.\n\n"
            "Masalan: <code>30000</code>",
            parse_mode="HTML"
        )

        return

    price = int(price_text)

    if price <= 0:

        await message.answer(
            "⚠️ Narx 0 dan katta bo‘lishi kerak."
        )

        return

    data = await state.get_data()

    await state.update_data(
        price=price
    )

    await state.set_state(
        PassengerOrder.confirmation
    )

    confirmation = (
        "🚕 <b>BUYURTMA</b>\n\n"
        f"📍 <b>Yo‘nalish:</b> {data.get('route')}\n"
        f"👥 <b>Yo‘lovchilar:</b> {data.get('passengers')} kishi\n"
        f"💰 <b>Narx:</b> {price:,} so‘m\n\n"
        "Buyurtmani tasdiqlaysizmi?"
    )

    await message.answer(
        confirmation,
        parse_mode="HTML",
        reply_markup=confirmation_keyboard()
    )


# =========================
# BUYURTMA TASDIQLASH
# =========================

@dp.message(
    PassengerOrder.confirmation,
    F.text == "✅ BUYURTMA BERISH"
)
async def confirm_order(
    message: types.Message,
    state: FSMContext
):

    data = await state.get_data()

    order_text = (
        "✅ <b>BUYURTMANGIZ QABUL QILINDI!</b>\n\n"
        f"📍 {data.get('route')}\n"
        f"👥 {data.get('passengers')} kishi\n"
        f"💰 {data.get('price'):,} so‘m\n\n"
        "🔎 Haydovchi qidirilmoqda...\n\n"
        "🚕 Tez orada haydovchi topiladi."
    )

    await message.answer(
        order_text,
        parse_mode="HTML",
        reply_markup=main_menu()
    )

    await state.clear()


# =========================
# O‘ZGARTIRISH
# =========================

@dp.message(
    PassengerOrder.confirmation,
    F.text == "✏️ O‘ZGARTIRISH"
)
async def edit_order(
    message: types.Message,
    state: FSMContext
):

    await state.set_state(
        PassengerOrder.waiting_route
    )

    await message.answer(
        "✏️ Yangi yo‘nalishni kiriting:\n\n"
        "Masalan:\n"
        "<code>Versal Dreams → Xakkarmon</code>",
        parse_mode="HTML"
    )


# =========================
# BEKOR QILISH
# =========================

@dp.message(
    PassengerOrder.confirmation,
    F.text == "❌ BEKOR QILISH"
)
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
# BUYURTMALAR TARIXI
# =========================

@dp.message(F.text == "📜 BUYURTMALAR TARIXI")
async def history(message: types.Message):

    await message.answer(
        "📜 <b>BUYURTMALAR TARIXI</b>\n\n"
        "Hozircha buyurtmalar tarixi bo‘sh.",
        parse_mode="HTML"
    )


# =========================
# PROFIL
# =========================

@dp.message(F.text == "👤 PROFIL")
async def profile(message: types.Message):

    await message.answer(
        "👤 <b>PROFIL</b>\n\n"
        f"Ism: {message.from_user.full_name}\n"
        f"Telegram ID: <code>{message.from_user.id}</code>\n\n"
        "📱 Telefon raqami keyingi bosqichda qo‘shiladi.",
        parse_mode="HTML"
    )


# =========================
# DASTAVKA
# =========================

@dp.message(F.text == "📦 DASTAVKA")
async def delivery(
    message: types.Message,
    state: FSMContext
):

    await state.clear()

    await state.set_state(
        DeliveryOrder.waiting_location
    )

    await message.answer(
        "📦 <b>DASTAVKA</b>\n\n"
        "📍 Avval joylashuvingizni yuboring:",
        parse_mode="HTML",
        reply_markup=location_keyboard()
    )


# =========================
# DASTAVKA GPS
# =========================

@dp.message(
    DeliveryOrder.waiting_location,
    F.location
)
async def delivery_location(
    message: types.Message,
    state: FSMContext
):

    await state.update_data(
        latitude=message.location.latitude,
        longitude=message.location.longitude
    )

    await state.set_state(
        DeliveryOrder.waiting_route
    )

    await message.answer(
        "✅ Joylashuv qabul qilindi.\n\n"
        "📍 <b>Qayerdan → 🏁 Qayerga</b>\n\n"
        "Masalan:\n"
        "<code>Obliq → Angren markazi</code>",
        parse_mode="HTML"
    )


# =========================
# DASTAVKA YO‘NALISHI
# =========================

@dp.message(
    DeliveryOrder.waiting_route,
    F.text
)
async def delivery_route(
    message: types.Message,
    state: FSMContext
):

    if message.text == "↩️ ORQAGA":

        await state.clear()

        await message.answer(
            "Asosiy menyu:",
            reply_markup=main_menu()
        )

        return

    await state.update_data(
        route=message.text
    )

    await state.set_state(
        DeliveryOrder.waiting_price
    )

    await message.answer(
        "💰 Dastavka narxini kiriting.\n\n"
        "Masalan: <code>30000</code>",
        parse_mode="HTML"
    )


# =========================
# DASTAVKA NARXI
# =========================

@dp.message(
    DeliveryOrder.waiting_price,
    F.text
)
async def delivery_price(
    message: types.Message,
    state: FSMContext
):

    price_text = message.text.replace(
        " ", ""
    ).replace(",", "")

    if not price_text.isdigit():

        await message.answer(
            "⚠️ Narxni faqat raqam bilan kiriting.\n"
            "Masalan: 30000"
        )

        return

    price = int(price_text)

    data = await state.get_data()

    await state.update_data(
        price=price
    )

    await state.set_state(
        DeliveryOrder.confirmation
    )

    await message.answer(
        "📦 <b>DASTAVKA BUYURTMASI</b>\n\n"
        f"📍 {data.get('route')}\n"
        f"💰 {price:,} so‘m\n\n"
        "Buyurtmani berasizmi?",
        parse_mode="HTML",
        reply_markup=confirmation_keyboard()
    )


# =========================
# DASTAVKA TASDIQLASH
# =========================

@dp.message(
    DeliveryOrder.confirmation,
    F.text == "✅ BUYURTMA BERISH"
)
async def confirm_delivery(
    message: types.Message,
    state: FSMContext
):

    data = await state.get_data()

    await message.answer(
        "✅ <b>DASTAVKA BUYURTMASI QABUL QILINDI!</b>\n\n"
        f"📍 {data.get('route')}\n"
        f"💰 {data.get('price'):,} so‘m\n\n"
        "🔎 Haydovchi qidirilmoqda...",
        parse_mode="HTML",
        reply_markup=main_menu()
    )

    await state.clear()


# =========================
# HAYDOVCHI
# =========================

@dp.message(F.text == "🚕 HAYDOVCHI")
async def driver(message: types.Message):

    await message.answer(
        "🚕 <b>HAYDOVCHI PANELI</b>\n\n"
        "📍 Yo‘nalish: <b>OBLIQ ↔ ANGGREN</b>\n\n"
        "🟢 ONLINE / ⚪ OFFLINE\n"
        "📋 Yangi buyurtmalar\n"
        "🚕 Faol buyurtmalar\n"
        "📜 Buyurtmalar tarixi\n"
        "💰 Daromad\n"
        "⭐ Reyting\n"
        "👤 Profil\n\n"
        "🚧 Haydovchini ro‘yxatdan o‘tkazish "
        "keyingi bosqichda qo‘shiladi.",
        parse_mode="HTML"
    )


# =========================
# TAKLIF VA MUROJAATLAR
# =========================

@dp.message(F.text == "📩 TAKLIF VA MUROJAATLAR")
async def support(
    message: types.Message,
    state: FSMContext
):

    await state.set_state(
        SupportState.waiting_message
    )

    await message.answer(
        "📩 <b>TAKLIF VA MUROJAATLAR</b>\n\n"
        "Taklifingiz, savolingiz, shikoyatingiz "
        "yoki muammoingizni yozing.\n\n"
        "✍️ Xabaringizni yuboring:",
        parse_mode="HTML"
    )


@dp.message(
    SupportState.waiting_message,
    F.text
)
async def support_message(
    message: types.Message,
    state: FSMContext
):

    await message.answer(
        "✅ Murojaatingiz qabul qilindi.\n\n"
        "📩 Rahmat! Administrator ko‘rib chiqadi.",
        reply_markup=main_menu()
    )

    await state.clear()


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
        "🇬🇧 English\n\n"
        "⚙️ Ko‘p tilli tizim keyingi bosqichda "
        "to‘liq ishga tushiriladi.",
        parse_mode="HTML"
    )


# =========================
# ORQAGA
# =========================

@dp.message(F.text == "↩️ ORQAGA")
async def back(message: types.Message, state: FSMContext):

    await state.clear()

    await message.answer(
        "🏠 Asosiy menyu:",
        reply_markup=main_menu()
    )


# =========================
# BOTNI ISHGA TUSHIRISH
# =========================

async def main():

    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN topilmadi. Railway Variables bo‘limiga BOT_TOKEN kiriting."
        )

    bot = Bot(token=TOKEN)

    print("🚕 TAXI BOR MI? bot ishga tushdi!")

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
