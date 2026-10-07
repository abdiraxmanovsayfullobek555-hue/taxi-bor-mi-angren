import os
import asyncio
import logging

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from aiogram.types import KeyboardButton


# =========================
# SOZLAMALAR
# =========================

TOKEN = os.getenv("BOT_TOKEN")

logging.basicConfig(level=logging.INFO)

dp = Dispatcher()


# =========================
# HOLATLAR
# =========================

class TaxiStates(StatesGroup):
    location = State()
    route = State()
    price = State()
    confirm = State()


class DeliveryStates(StatesGroup):
    location = State()
    route = State()
    price = State()
    confirm = State()


class SupportStates(StatesGroup):
    message = State()


# =========================
# ASOSIY MENYU
# =========================

def main_menu():

    kb = ReplyKeyboardBuilder()

    kb.button(text="👤 YO‘LOVCHI")
    kb.button(text="🚕 HAYDOVCHI")

    kb.button(text="📦 DASTAVKA")
    kb.button(text="📩 TAKLIF VA MUROJAATLAR")

    kb.button(text="🌐 TIL")

    kb.adjust(2, 2, 1)

    return kb.as_markup(resize_keyboard=True)


# =========================
# YO‘LOVCHI MENYUSI
# =========================

def passenger_menu():

    kb = ReplyKeyboardBuilder()

    kb.button(text="🚕 TAKSI BUYURTMA QILISH")
    kb.button(text="📜 BUYURTMALAR TARIXI")

    kb.button(text="👤 PROFIL")
    kb.button(text="⬅️ ORQAGA")

    kb.adjust(1, 2, 1)

    return kb.as_markup(resize_keyboard=True)


# =========================
# HAYDOVCHI MENYUSI
# =========================

def driver_menu():

    kb = ReplyKeyboardBuilder()

    kb.button(text="🟢 ONLINE")
    kb.button(text="⚪ OFFLINE")

    kb.button(text="📋 YANGI BUYURTMALAR")
    kb.button(text="🚕 FAOL BUYURTMALAR")

    kb.button(text="📜 BUYURTMALAR TARIXI")
    kb.button(text="💰 DAROMAD")

    kb.button(text="⭐ REYTING")
    kb.button(text="👤 PROFIL")

    kb.button(text="⬅️ ORQAGA")

    kb.adjust(2, 2, 2, 2, 1)

    return kb.as_markup(resize_keyboard=True)


# =========================
# JOYLASHUV TUGMASI
# =========================

def location_menu():

    kb = ReplyKeyboardBuilder()

    kb.add(
        KeyboardButton(
            text="📍 JOYLASHUVNI YUBORISH",
            request_location=True
        )
    )

    kb.button(text="⬅️ ORQAGA")

    kb.adjust(1, 1)

    return kb.as_markup(resize_keyboard=True)


# =========================
# ORQAGA
# =========================

def back_menu():

    kb = ReplyKeyboardBuilder()

    kb.button(text="⬅️ ORQAGA")

    return kb.as_markup(resize_keyboard=True)


# =========================
# TASDIQLASH
# =========================

def confirm_menu():

    kb = ReplyKeyboardBuilder()

    kb.button(text="✅ BUYURTMA BERISH")
    kb.button(text="✏️ O‘ZGARTIRISH")
    kb.button(text="❌ BEKOR QILISH")

    kb.adjust(1, 2)

    return kb.as_markup(resize_keyboard=True)


# ==========================================================
# START
# ==========================================================

@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):

    await state.clear()

    await message.answer(
        "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
        "📍 <b>OBLIQ ↔ ANGGREN</b>\n\n"
        "Xush kelibsiz!\n\n"
        "Kerakli bo‘limni tanlang:",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


# ==========================================================
# YO‘LOVCHI
# ==========================================================

@dp.message(F.text == "👤 YO‘LOVCHI")
async def passenger(message: types.Message, state: FSMContext):

    await state.clear()

    await message.answer(
        "👤 <b>YO‘LOVCHI</b>\n\n"
        "🚕 Taksi buyurtma qilish\n"
        "📜 Buyurtmalar tarixi\n"
        "👤 Profil\n\n"
        "Kerakli xizmatni tanlang:",
        parse_mode="HTML",
        reply_markup=passenger_menu()
    )


# ==========================================================
# TAKSI BUYURTMA
# ==========================================================

@dp.message(F.text == "🚕 TAKSI BUYURTMA QILISH")
async def taxi_start(message: types.Message, state: FSMContext):

    await state.clear()

    await state.set_state(TaxiStates.location)

    await message.answer(
        "🚕 <b>TAKSI BUYURTMA QILISH</b>\n\n"
        "📍 Avval joylashuvingizni yuboring.\n\n"
        "Joylashuv tugmasini bosing.",
        parse_mode="HTML",
        reply_markup=location_menu()
    )


# ==========================================================
# TAKSI JOYLASHUV
# ==========================================================

@dp.message(TaxiStates.location, F.location)
async def taxi_location(
    message: types.Message,
    state: FSMContext
):

    await state.update_data(
        latitude=message.location.latitude,
        longitude=message.location.longitude
    )

    await state.set_state(TaxiStates.route)

    await message.answer(
        "✅ Joylashuvingiz qabul qilindi.\n\n"
        "Endi yo‘nalishni yozing:\n\n"
        "📍 Qayerdan → 🏁 Qayerga\n\n"
        "Masalan:\n"
        "<b>Versal Dreams → Xakkarmon</b>",
        parse_mode="HTML",
        reply_markup=back_menu()
    )


# ==========================================================
# TAKSI YO‘NALISH
# ==========================================================

@dp.message(TaxiStates.route)
async def taxi_route(
    message: types.Message,
    state: FSMContext
):

    if message.text == "⬅️ ORQAGA":

        await state.clear()

        await message.answer(
            "Asosiy menyu:",
            reply_markup=main_menu()
        )

        return

    await state.update_data(
        route=message.text
    )

    await state.set_state(TaxiStates.price)

    await message.answer(
        "💰 <b>Narxni kiriting</b>\n\n"
        "Masalan:\n"
        "<b>30000</b>",
        parse_mode="HTML",
        reply_markup=back_menu()
    )


# ==========================================================
# TAKSI NARX
# ==========================================================

@dp.message(TaxiStates.price)
async def taxi_price(
    message: types.Message,
    state: FSMContext
):

    if message.text == "⬅️ ORQAGA":

        await state.clear()

        await message.answer(
            "Asosiy menyu:",
            reply_markup=main_menu()
        )

        return

    price_text = message.text.replace(" ", "").replace(",", "")

    if not price_text.isdigit():

        await message.answer(
            "❗ Narxni faqat raqam bilan kiriting.\n\n"
            "Masalan: <b>30000</b>",
            parse_mode="HTML"
        )

        return

    price = int(price_text)

    if price < 1000 or price > 10000000:

        await message.answer(
            "❗ Narx 1 000 dan 10 000 000 so‘mgacha bo‘lishi kerak."
        )

        return

    await state.update_data(
        price=price
    )

    data = await state.get_data()

    await state.set_state(TaxiStates.confirm)

    await message.answer(
        "🚕 <b>BUYURTMA</b>\n\n"
        f"📍 Yo‘nalish:\n"
        f"<b>{data['route']}</b>\n\n"
        f"💰 Narx:\n"
        f"<b>{price:,} so‘m</b>\n\n"
        "Buyurtmani tasdiqlaysizmi?",
        parse_mode="HTML",
        reply_markup=confirm_menu()
    )


# ==========================================================
# TAKSI TASDIQLASH
# ==========================================================

@dp.message(
    TaxiStates.confirm,
    F.text == "✅ BUYURTMA BERISH"
)
async def taxi_confirm(
    message: types.Message,
    state: FSMContext
):

    data = await state.get_data()

    await state.clear()

    await message.answer(
        "✅ <b>BUYURTMANGIZ QABUL QILINDI!</b>\n\n"
        f"📍 {data['route']}\n"
        f"💰 {data['price']:,} so‘m\n\n"
        "🚕 Haydovchi qidirilmoqda...\n\n"
        "Hozircha tizim demo rejimida ishlamoqda.",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


# ==========================================================
# TAKSI BEKOR
# ==========================================================

@dp.message(
    TaxiStates.confirm,
    F.text == "❌ BEKOR QILISH"
)
async def taxi_cancel(
    message: types.Message,
    state: FSMContext
):

    await state.clear()

    await message.answer(
        "❌ Buyurtma bekor qilindi.",
        reply_markup=main_menu()
    )


# ==========================================================
# TAKSI O‘ZGARTIRISH
# ==========================================================

@dp.message(
    TaxiStates.confirm,
    F.text == "✏️ O‘ZGARTIRISH"
)
async def taxi_edit(
    message: types.Message,
    state: FSMContext
):

    await state.set_state(TaxiStates.route)

    await message.answer(
        "✏️ Yo‘nalishni qaytadan kiriting:\n\n"
        "📍 Qayerdan → 🏁 Qayerga",
        reply_markup=back_menu()
    )


# ==========================================================
# DASTAVKA
# ==========================================================

@dp.message(F.text == "📦 DASTAVKA")
async def delivery_start(
    message: types.Message,
    state: FSMContext
):

    await state.clear()

    await state.set_state(DeliveryStates.location)

    await message.answer(
        "📦 <b>DASTAVKA</b>\n\n"
        "📍 Avval joylashuvingizni yuboring.",
        parse_mode="HTML",
        reply_markup=location_menu()
    )


# ==========================================================
# DASTAVKA JOYLASHUV
# ==========================================================

@dp.message(
    DeliveryStates.location,
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

    await state.set_state(DeliveryStates.route)

    await message.answer(
        "✅ Joylashuvingiz qabul qilindi.\n\n"
        "📍 Qayerdan → 🏁 Qayerga manzilini yozing.\n\n"
        "Masalan:\n"
        "<b>Obliq → Angren markazi</b>",
        parse_mode="HTML",
        reply_markup=back_menu()
    )


# ==========================================================
# DASTAVKA YO‘NALISH
# ==========================================================

@dp.message(DeliveryStates.route)
async def delivery_route(
    message: types.Message,
    state: FSMContext
):

    if message.text == "⬅️ ORQAGA":

        await state.clear()

        await message.answer(
            "Asosiy menyu:",
            reply_markup=main_menu()
        )

        return

    await state.update_data(
        route=message.text
    )

    await state.set_state(DeliveryStates.price)

    await message.answer(
        "💰 Dastavka narxini kiriting.\n\n"
        "Masalan:\n"
        "<b>30000</b>",
        parse_mode="HTML",
        reply_markup=back_menu()
    )


# ==========================================================
# DASTAVKA NARX
# ==========================================================

@dp.message(DeliveryStates.price)
async def delivery_price(
    message: types.Message,
    state: FSMContext
):

    if message.text == "⬅️ ORQAGA":

        await state.clear()

        await message.answer(
            "Asosiy menyu:",
            reply_markup=main_menu()
        )

        return

    price_text = message.text.replace(" ", "").replace(",", "")

    if not price_text.isdigit():

        await message.answer(
            "❗ Narxni faqat raqam bilan kiriting.\n\n"
            "Masalan: <b>30000</b>",
            parse_mode="HTML"
        )

        return

    price = int(price_text)

    if price < 1000 or price > 10000000:

        await message.answer(
            "❗ Narx 1 000 dan 10 000 000 so‘mgacha bo‘lishi kerak."
        )

        return

    await state.update_data(
        price=price
    )

    data = await state.get_data()

    await state.set_state(DeliveryStates.confirm)

    await message.answer(
        "📦 <b>DASTAVKA BUYURTMASI</b>\n\n"
        f"📍 Yo‘nalish:\n"
        f"<b>{data['route']}</b>\n\n"
        f"💰 Narx:\n"
        f"<b>{price:,} so‘m</b>\n\n"
        "Tasdiqlaysizmi?",
        parse_mode="HTML",
        reply_markup=confirm_menu()
    )


# ==========================================================
# DASTAVKA TASDIQLASH
# ==========================================================

@dp.message(
    DeliveryStates.confirm,
    F.text == "✅ BUYURTMA BERISH"
)
async def delivery_confirm(
    message: types.Message,
    state: FSMContext
):

    data = await state.get_data()

    await state.clear()

    await message.answer(
        "✅ <b>DASTAVKA BUYURTMASI QABUL QILINDI!</b>\n\n"
        f"📍 {data['route']}\n"
        f"💰 {data['price']:,} so‘m\n\n"
        "🚕 Haydovchi qidirilmoqda...\n\n"
        "Hozircha tizim demo rejimida ishlamoqda.",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


# ==========================================================
# DASTAVKA BEKOR
# ==========================================================

@dp.message(
    DeliveryStates.confirm,
    F.text == "❌ BEKOR QILISH"
)
async def delivery_cancel(
    message: types.Message,
    state: FSMContext
):

    await state.clear()

    await message.answer(
        "❌ Dastavka buyurtmasi bekor qilindi.",
        reply_markup=main_menu()
    )


# ==========================================================
# DASTAVKA O‘ZGARTIRISH
# ==========================================================

@dp.message(
    DeliveryStates.confirm,
    F.text == "✏️ O‘ZGARTIRISH"
)
async def delivery_edit(
    message: types.Message,
    state: FSMContext
):

    await state.set_state(DeliveryStates.route)

    await message.answer(
        "✏️ Yo‘nalishni qaytadan kiriting:\n\n"
        "📍 Qayerdan → 🏁 Qayerga",
        reply_markup=back_menu()
    )


# ==========================================================
# HAYDOVCHI
# ==========================================================

@dp.message(F.text == "🚕 HAYDOVCHI")
async def driver(
    message: types.Message,
    state: FSMContext
):

    await state.clear()

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
        "Kerakli bo‘limni tanlang:",
        parse_mode="HTML",
        reply_markup=driver_menu()
    )


# ==========================================================
# HAYDOVCHI ONLINE
# ==========================================================

@dp.message(F.text == "🟢 ONLINE")
async def driver_online(message: types.Message):

    await message.answer(
        "🟢 <b>ONLINE</b>\n\n"
        "Siz buyurtmalarni qabul qilishga tayyorsiz.",
        parse_mode="HTML",
        reply_markup=driver_menu()
    )


# ==========================================================
# HAYDOVCHI OFFLINE
# ==========================================================

@dp.message(F.text == "⚪ OFFLINE")
async def driver_offline(message: types.Message):

    await message.answer(
        "⚪ <b>OFFLINE</b>\n\n"
        "Siz hozir buyurtma qabul qilmaysiz.",
        parse_mode="HTML",
        reply_markup=driver_menu()
    )


# ==========================================================
# YANGI BUYURTMALAR
# ==========================================================

@dp.message(F.text == "📋 YANGI BUYURTMALAR")
async def new_orders(message: types.Message):

    await message.answer(
        "📋 <b>YANGI BUYURTMALAR</b>\n\n"
        "Hozircha yangi buyurtmalar mavjud emas.",
        parse_mode="HTML"
    )


# ==========================================================
# FAOL BUYURTMALAR
# ==========================================================

@dp.message(F.text == "🚕 FAOL BUYURTMALAR")
async def active_orders(message: types.Message):

    await message.answer(
        "🚕 <b>FAOL BUYURTMALAR</b>\n\n"
        "Hozircha faol buyurtmalar yo‘q.",
        parse_mode="HTML"
    )


# ==========================================================
# TARIX
# ==========================================================

@dp.message(F.text == "📜 BUYURTMALAR TARIXI")
async def history(message: types.Message):

    await message.answer(
        "📜 <b>BUYURTMALAR TARIXI</b>\n\n"
        "Hozircha tarix bo‘sh.",
        parse_mode="HTML"
    )


# ==========================================================
# DAROMAD
# ==========================================================

@dp.message(F.text == "💰 DAROMAD")
async def income(message: types.Message):

    await message.answer(
        "💰 <b>DAROMAD</b>\n\n"
        "Bugungi daromad: <b>0 so‘m</b>",
        parse_mode="HTML"
    )


# ==========================================================
# REYTING
# ==========================================================

@dp.message(F.text == "⭐ REYTING")
async def rating(message: types.Message):

    await message.answer(
        "⭐ <b>REYTING</b>\n\n"
        "Hozircha reyting mavjud emas.",
        parse_mode="HTML"
    )


# ==========================================================
# PROFIL
# ==========================================================

@dp.message(F.text == "👤 PROFIL")
async def profile(message: types.Message):

    await message.answer(
        "👤 <b>PROFIL</b>\n\n"
        f"Ism: <b>{message.from_user.full_name}</b>\n"
        f"Telegram ID: <code>{message.from_user.id}</code>",
        parse_mode="HTML"
    )


# ==========================================================
# TAKLIF VA MUROJAATLAR
# ==========================================================

@dp.message(F.text == "📩 TAKLIF VA MUROJAATLAR")
async def support_start(
    message: types.Message,
    state: FSMContext
):

    await state.clear()

    await state.set_state(SupportStates.message)

    await message.answer(
        "📩 <b>TAKLIF VA MUROJAATLAR</b>\n\n"
        "Taklifingiz, savolingiz, shikoyatingiz "
        "yoki muammoingizni yozing:",
        parse_mode="HTML",
        reply_markup=back_menu()
    )


@dp.message(SupportStates.message)
async def support_message(
    message: types.Message,
    state: FSMContext
):

    if message.text == "⬅️ ORQAGA":

        await state.clear()

        await message.answer(
            "Asosiy menyu:",
            reply_markup=main_menu()
        )

        return

    ticket = f"MR-{message.from_user.id}-{message.message_id}"

    await state.clear()

    await message.answer(
        "✅ <b>Murojaatingiz qabul qilindi!</b>\n\n"
        f"🎫 Murojaat raqami: <code>{ticket}</code>\n\n"
        "Rahmat.",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


# ==========================================================
# TIL
# ==========================================================

@dp.message(F.text == "🌐 TIL")
async def language(message: types.Message):

    kb = ReplyKeyboardBuilder()

    kb.button(text="🇺🇿 O‘zbekcha")
    kb.button(text="🇺🇿 Ўзбекча")
    kb.button(text="🇷🇺 Русский")
    kb.button(text="🇬🇧 English")
    kb.button(text="⬅️ ORQAGA")

    kb.adjust(2, 2, 1)

    await message.answer(
        "🌐 <b>TILNI TANLANG</b>",
        parse_mode="HTML",
        reply_markup=kb.as_markup(resize_keyboard=True)
    )


# ==========================================================
# TIL TANLANDI
# ==========================================================

@dp.message(
    F.text.in_({
        "🇺🇿 O‘zbekcha",
        "🇺🇿 Ўзбекча",
        "🇷🇺 Русский",
        "🇬🇧 English"
    })
)
async def language_selected(message: types.Message):

    await message.answer(
        "✅ Til tanlandi.",
        reply_markup=main_menu()
    )


# ==========================================================
# ORQAGA
# ==========================================================

@dp.message(F.text == "⬅️ ORQAGA")
async def back(message: types.Message, state: FSMContext):

    await state.clear()

    await message.answer(
        "Asosiy menyu:",
        reply_markup=main_menu()
    )


# ==========================================================
# BOTNI ISHGA TUSHIRISH
# ==========================================================

async def main():

    if not TOKEN:

        raise RuntimeError(
            "BOT_TOKEN topilmadi! "
            "Railway → Variables ichiga BOT_TOKEN qo‘ying."
        )

    bot = Bot(token=TOKEN)

    me = await bot.get_me()

    logging.info(
        "🚕 TAXI BOR MI? ishga tushdi: @%s",
        me.username
    )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
