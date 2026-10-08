
import os
import logging
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from database import (
    init_db,
    get_customer,
    create_customer,
    create_order,
)

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("CUSTOMER_BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("CUSTOMER_BOT_TOKEN topilmadi!")

bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher(storage=MemoryStorage())


# =========================
# STATES
# =========================

class Registration(StatesGroup):
    language = State()
    name = State()
    phone = State()
    route = State()


class OrderState(StatesGroup):
    waiting_order = State()
    waiting_gps = State()


# =========================
# TEXTS
# =========================

TEXT = {
    "uz": {
        "choose_language": "🇺🇿 Tilni tanlang:",
        "name": "👤 Ismingizni yozing:",
        "phone": "📞 Telefon raqamingizni yuboring:",
        "phone_button": "📞 Telefon raqamimni yuborish",
        "route": "📍 Yo‘nalishingizni tanlang:",
        "registered": "✅ Ro‘yxatdan o‘tish yakunlandi!",
        "menu": "🚕 <b>TAXI BOR MI?</b>\n\nBuyurtma berish uchun yozing.",
        "write_order": "✍️ Buyurtmangizni yozing:",
        "gps_question": "📍 GPS yuborasizmi?\n\nGPS majburiy emas.",
        "gps_button": "📍 GPS yuborish",
        "without_gps": "⏭ GPSsiz davom etish",
        "order_created": "✅ <b>Buyurtmangiz qabul qilindi!</b>\n\n🚕 Haydovchi qidirilmoqda.",
        "gps_received": "📍 GPS qabul qilindi.",
        "order_error": "❌ Buyurtmani yaratishda xatolik yuz berdi.",
    },
    "uz_cyr": {
        "choose_language": "🇺🇿 Тилни танланг:",
        "name": "👤 Исмингизни ёзинг:",
        "phone": "📞 Телефон рақамингизни юборинг:",
        "phone_button": "📞 Телефон рақамимни юбориш",
        "route": "📍 Йўналишингизни танланг:",
        "registered": "✅ Рўйхатдан ўтиш якунланди!",
        "menu": "🚕 <b>TAXI BOR MI?</b>\n\nБуюртма бериш учун ёзинг.",
        "write_order": "✍️ Буюртмангизни ёзинг:",
        "gps_question": "📍 GPS юборасизми?\n\nGPS мажбурий эмас.",
        "gps_button": "📍 GPS юбориш",
        "without_gps": "⏭ GPSсиз давом этиш",
        "order_created": "✅ <b>Буюртмангиз қабул қилинди!</b>\n\n🚕 Ҳайдовчи қидирилмоқда.",
        "gps_received": "📍 GPS қабул қилинди.",
        "order_error": "❌ Буюртмани яратишда хатолик юз берди.",
    },
}


def get_lang(user_id):
    customer = get_customer(user_id)

    if customer:
        return customer["language"]

    return "uz"


def t(user_id, key):
    lang = get_lang(user_id)
    return TEXT.get(lang, TEXT["uz"]).get(key, key)


# =========================
# KEYBOARDS
# =========================

def language_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🇺🇿 O‘zbekcha"),
                KeyboardButton(text="🇺🇿 Ўзбекча"),
            ]
        ],
        resize_keyboard=True
    )


def phone_keyboard(user_id):
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=t(user_id, "phone_button"),
                    request_contact=True
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def route_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="OBLIQ → ANGREN")],
            [KeyboardButton(text="ANGREN → OBLIQ")],
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def gps_keyboard(user_id):
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=t(user_id, "gps_button"),
                    request_location=True
                )
            ],
            [
                KeyboardButton(
                    text=t(user_id, "without_gps")
                )
            ],
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


# =========================
# START
# =========================

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    user_id = message.from_user.id

    customer = get_customer(user_id)

    if customer:
        await state.clear()

        await message.answer(
            t(user_id, "menu"),
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[],
                resize_keyboard=True
            )
        )

        await state.set_state(OrderState.waiting_order)

        return

    await state.clear()

    await message.answer(
        TEXT["uz"]["choose_language"],
        reply_markup=language_keyboard()
    )

    await state.set_state(Registration.language)


# =========================
# LANGUAGE
# =========================

@dp.message(Registration.language)
async def choose_language(message: Message, state: FSMContext):

    if message.text == "🇺🇿 O‘zbekcha":
        lang = "uz"

    elif message.text == "🇺🇿 Ўзбекча":
        lang = "uz_cyr"

    else:
        await message.answer("Tilni tugma orqali tanlang.")
        return

    await state.update_data(language=lang)

    await message.answer(
        TEXT[lang]["name"],
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[],
            resize_keyboard=True
        )
    )

    await state.set_state(Registration.name)


# =========================
# NAME
# =========================

@dp.message(Registration.name)
async def get_name(message: Message, state: FSMContext):

    name = message.text.strip()

    if len(name) < 2:
        await message.answer("Ismni to‘g‘ri yozing.")
        return

    await state.update_data(name=name)

    data = await state.get_data()
    lang = data["language"]

    await message.answer(
        TEXT[lang]["phone"],
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(
                        text=TEXT[lang]["phone_button"],
                        request_contact=True
                    )
                ]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )

    await state.set_state(Registration.phone)


# =========================
# PHONE
# =========================

@dp.message(Registration.phone, F.contact)
async def get_phone(message: Message, state: FSMContext):

    phone = message.contact.phone_number

    await state.update_data(phone=phone)

    data = await state.get_data()
    lang = data["language"]

    await message.answer(
        TEXT[lang]["route"],
        reply_markup=route_keyboard()
    )

    await state.set_state(Registration.route)


@dp.message(Registration.phone)
async def phone_required(message: Message, state: FSMContext):

    data = await state.get_data()
    lang = data["language"]

    await message.answer(
        TEXT[lang]["phone"],
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(
                        text=TEXT[lang]["phone_button"],
                        request_contact=True
                    )
                ]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )


# =========================
# ROUTE
# =========================

@dp.message(Registration.route)
async def get_route(message: Message, state: FSMContext):

    if message.text not in [
        "OBLIQ → ANGREN",
        "ANGREN → OBLIQ"
    ]:
        await message.answer(
            "Yo‘nalishni tugma orqali tanlang.",
            reply_markup=route_keyboard()
        )
        return

    route = message.text

    data = await state.get_data()

    create_customer(
        telegram_id=message.from_user.id,
        name=data["name"],
        phone=data["phone"],
        language=data["language"],
        route=route
    )

    lang = data["language"]

    await state.clear()

    await message.answer(
        TEXT[lang]["registered"],
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[],
            resize_keyboard=True
        )
    )

    await message.answer(
        TEXT[lang]["menu"]
    )

    await state.set_state(OrderState.waiting_order)


# =========================
# ORDER TEXT
# =========================

@dp.message(OrderState.waiting_order, F.text)
async def receive_order(message: Message, state: FSMContext):

    text = message.text.strip()

    if len(text) < 2:
        await message.answer(
            t(message.from_user.id, "write_order")
        )
        return

    customer = get_customer(message.from_user.id)

    if not customer:
        await message.answer("Avval /start bosing.")
        await state.clear()
        return

    await state.update_data(
        order_text=text,
        latitude=None,
        longitude=None
    )

    await message.answer(
        t(message.from_user.id, "gps_question"),
        reply_markup=gps_keyboard(message.from_user.id)
    )

    await state.set_state(OrderState.waiting_gps)


# =========================
# GPS
# =========================

@dp.message(OrderState.waiting_gps, F.location)
async def receive_gps(message: Message, state: FSMContext):

    await state.update_data(
        latitude=message.location.latitude,
        longitude=message.location.longitude
    )

    await create_customer_order(
        message,
        state
    )


@dp.message(OrderState.waiting_gps, F.text)
async def without_gps(message: Message, state: FSMContext):

    if message.text == t(message.from_user.id, "without_gps"):
        await create_customer_order(
            message,
            state
        )
        return

    await message.answer(
        t(message.from_user.id, "gps_question"),
        reply_markup=gps_keyboard(message.from_user.id)
    )


# =========================
# CREATE ORDER
# =========================

async def create_customer_order(message: Message, state: FSMContext):

    customer = get_customer(message.from_user.id)

    if not customer:
        await message.answer("Avval /start bosing.")
        await state.clear()
        return

    data = await state.get_data()

    try:
        order_id = create_order(
            customer_id=customer["id"],
            route=customer["route"],
            text=data["order_text"],
            latitude=data.get("latitude"),
            longitude=data.get("longitude")
        )

        await state.clear()

        await message.answer(
            f"{t(message.from_user.id, 'order_created')}\n\n"
            f"🚕 Buyurtma raqami: <b>#{order_id}</b>\n"
            f"📍 {customer['route']}"
        )

        # Keyingi bosqichda shu yerga
        # haydovchi botiga yuborish tizimini ulaymiz.

    except Exception:
        logging.exception("Order create error")

        await message.answer(
            t(message.from_user.id, "order_error")
        )

        await state.clear()


# =========================
# MAIN
# =========================

async def main():

    init_db()

    print("🚕 Customer bot ishga tushdi...")

    await dp.start_polling(bot)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
