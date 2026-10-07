import os
import asyncio
import sqlite3
import json
from datetime import datetime

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.utils.keyboard import ReplyKeyboardBuilder, InlineKeyboardBuilder
from openai import AsyncOpenAI


# =========================================================
# SETTINGS
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi")

ai = AsyncOpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

dp = Dispatcher()
bot = None

DB = "taxi.db"


# =========================================================
# DATABASE
# =========================================================

def db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def now():
    return datetime.now().isoformat(timespec="seconds")


def init_db():
    conn = db()

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        tg_id INTEGER PRIMARY KEY,
        name TEXT,
        phone TEXT,
        lang TEXT DEFAULT 'uz',
        role TEXT DEFAULT 'customer',
        lat REAL,
        lon REAL,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS drivers (
        tg_id INTEGER PRIMARY KEY,
        name TEXT,
        phone TEXT,
        car TEXT,
        plate TEXT UNIQUE,
        license_file TEXT,
        tech_file TEXT,
        car_photo TEXT,
        approved INTEGER DEFAULT 0,
        online INTEGER DEFAULT 0,
        active_orders INTEGER DEFAULT 0,
        rating REAL DEFAULT 5.0,
        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER,
        driver_id INTEGER,
        service TEXT,
        origin TEXT,
        destination TEXT,
        lat REAL,
        lon REAL,
        passengers INTEGER DEFAULT 1,
        price INTEGER,
        status TEXT DEFAULT 'SEARCHING',
        created_at TEXT,
        claimed_at TEXT
    );

    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER,
        actor INTEGER,
        event TEXT,
        created_at TEXT
    );
    """)

    conn.commit()
    conn.close()


def set_user(tg_id, **fields):
    conn = db()

    exists = conn.execute(
        "SELECT tg_id FROM users WHERE tg_id=?",
        (tg_id,)
    ).fetchone()

    if not exists:
        conn.execute(
            "INSERT INTO users(tg_id, name, created_at) VALUES (?, ?, ?)",
            (tg_id, "", now())
        )

    for key, value in fields.items():
        conn.execute(
            f"UPDATE users SET {key}=? WHERE tg_id=?",
            (value, tg_id)
        )

    conn.commit()
    conn.close()


def get_user(tg_id):
    conn = db()
    row = conn.execute(
        "SELECT * FROM users WHERE tg_id=?",
        (tg_id,)
    ).fetchone()
    conn.close()
    return row


def get_driver(tg_id):
    conn = db()
    row = conn.execute(
        "SELECT * FROM drivers WHERE tg_id=?",
        (tg_id,)
    ).fetchone()
    conn.close()
    return row


def log_event(order_id, actor, event):
    conn = db()

    conn.execute(
        """
        INSERT INTO events(order_id, actor, event, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (order_id, actor, event, now())
    )

    conn.commit()
    conn.close()


# =========================================================
# KEYBOARDS
# =========================================================

def main_menu():
    kb = ReplyKeyboardBuilder()

    buttons = [
        "👤 YO‘LOVCHI",
        "🚕 HAYDOVCHI",
        "📦 DASTAVKA",
        "📩 TAKLIF VA MUROJAATLAR",
        "🌐 TIL"
    ]

    for button in buttons:
        kb.button(text=button)

    kb.adjust(2, 2, 1)

    return kb.as_markup(resize_keyboard=True)


def back_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text="⬅️ ORQAGA")
    return kb.as_markup(resize_keyboard=True)


def phone_keyboard():
    kb = ReplyKeyboardBuilder()

    kb.button(
        text="📱 TELEFON RAQAMIMNI YUBORISH",
        request_contact=True
    )

    kb.button(text="⬅️ ORQAGA")

    kb.adjust(1)

    return kb.as_markup(resize_keyboard=True)


def location_keyboard():
    kb = ReplyKeyboardBuilder()

    kb.button(
        text="📍 JOYLASHUVIMNI YUBORISH",
        request_location=True
    )

    kb.button(text="⬅️ ORQAGA")

    kb.adjust(1)

    return kb.as_markup(resize_keyboard=True)


# =========================================================
# AI
# =========================================================

async def ai_parse_route(text):
    """
    AI:
    Qayerdan -> Qayerga
    Uzbek Latin
    Uzbek Cyrillic
    Russian
    spelling mistakes
    """

    fallback = {
        "origin": "",
        "destination": "",
        "confidence": 0
    }

    if not text:
        return fallback

    if not ai:
        if "→" in text:
            parts = text.split("→", 1)

            return {
                "origin": parts[0].strip(),
                "destination": parts[1].strip(),
                "confidence": 0.5
            }

        return {
            "origin": text.strip(),
            "destination": "",
            "confidence": 0.1
        }

    try:
        response = await ai.responses.create(
            model=OPENAI_MODEL,
            instructions="""
You are the route parsing AI for TAXI BOR MI? in Angren, Uzbekistan.

Your task:
Understand Uzbek Latin,
Uzbek Cyrillic,
Russian,
common spelling mistakes,
short informal messages.

The user describes a taxi route.

Return ONLY valid JSON:

{
  "origin": "...",
  "destination": "...",
  "confidence": 0.0
}

Never invent an address.

If one part is missing, return an empty string.

Examples:

Versal Dreams dan Xakkarmanga
=> origin = Versal Dreams
destination = Xakkarmon

Versal Dreams → Xakkarmon
=> origin = Versal Dreams
destination = Xakkarmon

Do not add explanations.
""",
            input=text
        )

        result = json.loads(response.output_text)

        return {
            "origin": str(result.get("origin", "")).strip(),
            "destination": str(result.get("destination", "")).strip(),
            "confidence": float(result.get("confidence", 0))
        }

    except Exception as e:
        print("AI ERROR:", e)

        if "→" in text:
            parts = text.split("→", 1)

            return {
                "origin": parts[0].strip(),
                "destination": parts[1].strip(),
                "confidence": 0.4
            }

        return fallback


# =========================================================
# STATES
# =========================================================

class Passenger(StatesGroup):
    location = State()
    route = State()
    passengers = State()
    price = State()


class Delivery(StatesGroup):
    location = State()
    route = State()
    price = State()


class DriverRegistration(StatesGroup):
    name = State()
    phone = State()
    car = State()
    plate = State()
    license = State()
    tech = State()
    photo = State()


class Support(StatesGroup):
    message = State()


# =========================================================
# MAIN
# =========================================================

async def show_main_menu(message):
    await message.answer(
        """
🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>

📍 <b>OBLIQ ↔ ANGREN</b>

Xush kelibsiz!

Kerakli xizmatni tanlang:
""",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):

    await state.clear()

    set_user(
        message.from_user.id,
        name=message.from_user.full_name,
        role="customer"
    )

    await show_main_menu(message)


# =========================================================
# LANGUAGE
# =========================================================

@dp.message(F.text == "🌐 TIL")
async def language_menu(message):

    kb = ReplyKeyboardBuilder()

    kb.button(text="🇺🇿 O‘zbekcha")
    kb.button(text="🇺🇿 Ўзбекча")
    kb.button(text="🇷🇺 Русский")
    kb.button(text="🇬🇧 English")

    kb.adjust(2)

    await message.answer(
        "🌐 <b>TILNI TANLANG</b>",
        parse_mode="HTML",
        reply_markup=kb.as_markup(resize_keyboard=True)
    )


@dp.message(
    F.text.in_(
        {
            "🇺🇿 O‘zbekcha",
            "🇺🇿 Ўзбекча",
            "🇷🇺 Русский",
            "🇬🇧 English"
        }
    )
)
async def set_language(message):

    mapping = {
        "🇺🇿 O‘zbekcha": "uz",
        "🇺🇿 Ўзбекча": "uz_cyr",
        "🇷🇺 Русский": "ru",
        "🇬🇧 English": "en"
    }

    set_user(
        message.from_user.id,
        lang=mapping[message.text]
    )

    await message.answer(
        "✅ Til saqlandi.",
        reply_markup=main_menu()
    )


# =========================================================
# PASSENGER MENU
# =========================================================

@dp.message(F.text == "👤 YO‘LOVCHI")
async def passenger_menu(message):

    kb = ReplyKeyboardBuilder()

    kb.button(text="🚕 TAKSI BUYURTMA QILISH")
    kb.button(text="📜 BUYURTMALAR TARIXI")
    kb.button(text="👤 PROFIL")
    kb.button(text="⬅️ ORQAGA")

    kb.adjust(1, 2, 1)

    await message.answer(
        """
👤 <b>YO‘LOVCHI</b>

Kerakli bo‘limni tanlang:
""",
        parse_mode="HTML",
        reply_markup=kb.as_markup(resize_keyboard=True)
    )


# =========================================================
# PASSENGER ORDER
# =========================================================

@dp.message(F.text == "🚕 TAKSI BUYURTMA QILISH")
async def taxi_start(message, state):

    set_user(
        message.from_user.id,
        role="customer"
    )

    await state.set_state(Passenger.location)

    await message.answer(
        """
🚕 <b>TAKSI BUYURTMA</b>

Avval joylashuvingizni yuboring.
""",
        parse_mode="HTML",
        reply_markup=location_keyboard()
    )


@dp.message(Passenger.location, F.location)
async def passenger_location(message, state):

    await state.update_data(
        lat=message.location.latitude,
        lon=message.location.longitude
    )

    await state.set_state(Passenger.route)

    await message.answer(
        """
📍 Joylashuvingiz qabul qilindi.

Endi safar manzilini kiriting:

📍 <b>Qayerdan → Qayerga</b>

Masalan:
<code>Versal Dreams → Xakkarmon</code>
""",
        parse_mode="HTML",
        reply_markup=back_menu()
    )


@dp.message(Passenger.route, F.text)
async def passenger_route(message, state):

    if message.text == "⬅️ ORQAGA":
        await state.clear()
        return await show_main_menu(message)

    parsed = await ai_parse_route(message.text)

    origin = parsed.get("origin", "")
    destination = parsed.get("destination", "")
    confidence = parsed.get("confidence", 0)

    if not origin or not destination:
        return await message.answer(
            """
⚠️ Manzil to‘liq aniqlanmadi.

Iltimos, quyidagicha yozing:

<code>Qayerdan → Qayerga</code>

Masalan:
<code>Versal Dreams → Xakkarmon</code>
""",
            parse_mode="HTML"
        )

    await state.update_data(
        origin=origin,
        destination=destination
    )

    await state.set_state(Passenger.passengers)

    kb = ReplyKeyboardBuilder()

    for number in range(1, 8):
        kb.button(text=f"👥 {number}")

    kb.button(text="⬅️ ORQAGA")
    kb.adjust(4, 3, 1)

    await message.answer(
        f"""
🤖 <b>AI MANZILNI ANIQLADI</b>

📍 Qayerdan: <b>{origin}</b>
🏁 Qayerga: <b>{destination}</b>

Aniqlik: <b>{int(confidence * 100)}%</b>

👥 Necha kishi?
""",
        parse_mode="HTML",
        reply_markup=kb.as_markup(resize_keyboard=True)
    )


@dp.message(
    Passenger.passengers,
    F.text.regexp(r"👥 [1-7]$")
)
async def passenger_count(message, state):

    count = int(message.text.split()[1])

    await state.update_data(
        passengers=count
    )

    await state.set_state(Passenger.price)

    await message.answer(
        """
💰 Safar uchun taklif qilayotgan narxingizni yozing.

Masalan:

<code>30000</code>
""",
        parse_mode="HTML",
        reply_markup=back_menu()
    )


@dp.message(Passenger.price, F.text)
async def passenger_price(message, state):

    if message.text == "⬅️ ORQAGA":
        await state.clear()
        return await show_main_menu(message)

    if not message.text.isdigit():
        return await message.answer(
            "⚠️ Faqat raqam kiriting.\nMasalan: 30000"
        )

    price = int(message.text)

    if price < 1000:
        return await message.answer(
            "⚠️ Narx juda kichik."
        )

    await state.update_data(price=price)

    data = await state.get_data()

    keyboard = InlineKeyboardBuilder()

    keyboard.button(
        text="✅ BUYURTMA BERISH",
        callback_data="passenger_confirm"
    )

    keyboard.button(
        text="✏️ O‘ZGARTIRISH",
        callback_data="passenger_edit"
    )

    keyboard.button(
        text="❌ BEKOR QILISH",
        callback_data="passenger_cancel"
    )

    keyboard.adjust(1)

    await message.answer(
        f"""
🚕 <b>BUYURTMA TASDIQLASH</b>

📍 Qayerdan:
<b>{data['origin']}</b>

🏁 Qayerga:
<b>{data['destination']}</b>

👥 Yo‘lovchilar:
<b>{data['passengers']} kishi</b>

💰 Narx:
<b>{price:,} so‘m</b>

Buyurtmani yuboramizmi?
""",
        parse_mode="HTML",
        reply_markup=keyboard.as_markup()
    )


@dp.callback_query(F.data == "passenger_edit")
async def passenger_edit(callback, state):

    await state.set_state(Passenger.route)

    await callback.message.answer(
        "✏️ Qayerdan → Qayerga ni qayta yozing."
    )

    await callback.answer()


@dp.callback_query(F.data == "passenger_cancel")
async def passenger_cancel(callback, state):

    await state.clear()

    await callback.message.answer(
        "❌ Buyurtma bekor qilindi.",
        reply_markup=main_menu()
    )

    await callback.answer()


@dp.callback_query(F.data == "passenger_confirm")
async def passenger_confirm(callback, state):

    data = await state.get_data()

    conn = db()

    cursor = conn.execute(
        """
        INSERT INTO orders(
            customer_id,
            service,
            origin,
            destination,
            lat,
            lon,
            passengers,
            price,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            callback.from_user.id,
            "passenger",
            data["origin"],
            data["destination"],
            data.get("lat"),
            data.get("lon"),
            data["passengers"],
            data["price"],
            "SEARCHING",
            now()
        )
    )

    order_id = cursor.lastrowid

    conn.commit()
    conn.close()

    log_event(
        order_id,
        callback.from_user.id,
        "ORDER_CREATED"
    )

    await state.clear()

    await callback.message.answer(
        f"""
🔎 <b>BUYURTMA #{order_id}</b>

🚕 Haydovchilar qidirilmoqda...

⏱ 1 daqiqa kuting.
""",
        parse_mode="HTML",
        reply_markup=main_menu()
    )

    asyncio.create_task(
        dispatch_order(order_id)
    )

    await callback.answer()


# =========================================================
# DELIVERY
# =========================================================

@dp.message(F.text == "📦 DASTAVKA")
async def delivery_start(message, state):

    set_user(
        message.from_user.id,
        role="customer"
    )

    await state.set_state(Delivery.location)

    await message.answer(
        """
📦 <b>DASTAVKA</b>

Avval joylashuvingizni yuboring.
""",
        parse_mode="HTML",
        reply_markup=location_keyboard()
    )


@dp.message(Delivery.location, F.location)
async def delivery_location(message, state):

    await state.update_data(
        lat=message.location.latitude,
        lon=message.location.longitude
    )

    await state.set_state(Delivery.route)

    await message.answer(
        """
📍 Joylashuv qabul qilindi.

Endi:

<code>Qayerdan → Qayerga</code>
""",
        parse_mode="HTML",
        reply_markup=back_menu()
    )


@dp.message(Delivery.route, F.text)
async def delivery_route(message, state):

    if message.text == "⬅️ ORQAGA":
        await state.clear()
        return await show_main_menu(message)

    parsed = await ai_parse_route(message.text)

    origin = parsed.get("origin", "")
    destination = parsed.get("destination", "")

    if not origin or not destination:
        return await message.answer(
            "⚠️ Qayerdan → Qayerga ni to‘liq yozing."
        )

    await state.update_data(
        origin=origin,
        destination=destination
    )

    await state.set_state(Delivery.price)

    await message.answer(
        f"""
🤖 <b>AI ANIQLADI</b>

📍 {origin}
🏁 {destination}

💰 Dastavka uchun narxingizni yozing:
""",
        parse_mode="HTML"
    )


@dp.message(Delivery.price, F.text)
async def delivery_price(message, state):

    if not message.text.isdigit():
        return await message.answer(
            "⚠️ Faqat raqam kiriting."
        )

    price = int(message.text)

    if price < 1000:
        return await message.answer(
            "⚠️ Narx juda kichik."
        )

    await state.update_data(
        price=price
    )

    data = await state.get_data()

    keyboard = InlineKeyboardBuilder()

    keyboard.button(
        text="✅ DASTAVKA BERISH",
        callback_data="delivery_confirm"
    )

    keyboard.button(
        text="❌ BEKOR QILISH",
        callback_data="delivery_cancel"
    )

    keyboard.adjust(1)

    await message.answer(
        f"""
📦 <b>DASTAVKA BUYURTMASI</b>

📍 {data['origin']}
🏁 {data['destination']}

💰 {price:,} so‘m

Tasdiqlaysizmi?
""",
        parse_mode="HTML",
        reply_markup=keyboard.as_markup()
    )


@dp.callback_query(F.data == "delivery_cancel")
async def delivery_cancel(callback, state):

    await state.clear()

    await callback.message.answer(
        "❌ Dastavka bekor qilindi.",
        reply_markup=main_menu()
    )

    await callback.answer()


@dp.callback_query(F.data == "delivery_confirm")
async def delivery_confirm(callback, state):

    data = await state.get_data()

    conn = db()

    cursor = conn.execute(
        """
        INSERT INTO orders(
            customer_id,
            service,
            origin,
            destination,
            lat,
            lon,
            passengers,
            price,
            status,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            callback.from_user.id,
            "delivery",
            data["origin"],
            data["destination"],
            data.get("lat"),
            data.get("lon"),
            1,
            data["price"],
            "SEARCHING",
            now()
        )
    )

    order_id = cursor.lastrowid

    conn.commit()
    conn.close()

    log_event(
        order_id,
        callback.from_user.id,
        "DELIVERY_CREATED"
    )

    await state.clear()

    await callback.message.answer(
        f"""
📦 <b>DASTAVKA #{order_id}</b>

Haydovchilar qidirilmoqda...
""",
        parse_mode="HTML",
        reply_markup=main_menu()
    )

    asyncio.create_task(
        dispatch_order(order_id)
    )

    await callback.answer()


# =========================================================
# DRIVER REGISTRATION
# =========================================================

@dp.message(F.text == "🚕 HAYDOVCHI")
async def driver_menu(message, state):

    driver = get_driver(
        message.from_user.id
    )

    if not driver:

        await state.set_state(
            DriverRegistration.name
        )

        return await message.answer(
            """
🚕 <b>HAYDOVCHI RO‘YXATDAN O‘TISH</b>

F.I.Sh. ni yozing:
""",
            parse_mode="HTML",
            reply_markup=back_menu()
        )

    if not driver["approved"]:

        return await message.answer(
            """
⏳ <b>ARIZA KO‘RIB CHIQILMOQDA</b>

Admin tasdig‘ini kuting.
""",
            parse_mode="HTML"
        )

    keyboard = ReplyKeyboardBuilder()

    if driver["online"]:
        keyboard.button(text="⚪ OFFLINE")
    else:
        keyboard.button(text="🟢 ONLINE")

    keyboard.button(text="📋 YANGI BUYURTMALAR")
    keyboard.button(text="🚕 FAOL BUYURTMALAR")
    keyboard.button(text="📜 TARIX")
    keyboard.button(text="💰 DAROMAD")
    keyboard.button(text="⭐ REYTING")
    keyboard.button(text="👤 PROFIL")
    keyboard.button(text="⬅️ ORQAGA")

    keyboard.adjust(2, 2, 2, 1, 1)

    await message.answer(
        f"""
🚕 <b>HAYDOVCHI PANELI</b>

📍 Yo‘nalish:
<b>OBLIQ ↔ ANGREN</b>

🚗 Avtomobil:
<b>{driver['car']}</b>

🔢 Raqam:
<b>{driver['plate']}</b>

⭐ Reyting:
<b>{driver['rating']}</b>

📋 Faol buyurtmalar:
<b>{driver['active_orders']}/7</b>
""",
        parse_mode="HTML",
        reply_markup=keyboard.as_markup(
            resize_keyboard=True
        )
    )


@dp.message(DriverRegistration.name, F.text)
async def driver_name(message, state):

    if message.text == "⬅️ ORQAGA":
        await state.clear()
        return await show_main_menu(message)

    await state.update_data(
        name=message.text
    )

    await state.set_state(
        DriverRegistration.phone
    )

    await message.answer(
        "📱 Telefon raqamingizni yuboring:",
        reply_markup=phone_keyboard()
    )


@dp.message(DriverRegistration.phone, F.contact)
async def driver_phone(message, state):

    await state.update_data(
        phone=message.contact.phone_number
    )

    await state.set_state(
        DriverRegistration.car
    )

    await message.answer(
        """
🚗 Avtomobil modelini yozing.

Masalan:
<code>Cobalt</code>
""",
        parse_mode="HTML"
    )


@dp.message(DriverRegistration.car, F.text)
async def driver_car(message, state):

    await state.update_data(
        car=message.text
    )

    await state.set_state(
        DriverRegistration.plate
    )

    await message.answer(
        """
🔢 Avtomobil davlat raqamini yozing.

Masalan:
<code>01 A 123 AA</code>
""",
        parse_mode="HTML"
    )


@dp.message(DriverRegistration.plate, F.text)
async def driver_plate(message, state):

    plate = message.text.upper().strip()

    conn = db()

    exists = conn.execute(
        "SELECT tg_id FROM drivers WHERE plate=?",
        (plate,)
    ).fetchone()

    conn.close()

    if exists:
        return await message.answer(
            """
❌ Bu avtomobil raqami allaqachon tizimda ro‘yxatdan o‘tgan.

Bitta avtomobil faqat bitta haydovchi akkauntiga biriktiriladi.
"""
        )

    await state.update_data(
        plate=plate
    )

    await state.set_state(
        DriverRegistration.license
    )

    await message.answer(
        """
🪪 Haydovchilik guvohnomangiz rasmini yuboring.
"""
    )


@dp.message(DriverRegistration.license, F.photo)
async def driver_license(message, state):

    await state.update_data(
        license_file=message.photo[-1].file_id
    )

    await state.set_state(
        DriverRegistration.tech
    )

    await message.answer(
        """
📄 Texpasport rasmini yuboring.
"""
    )


@dp.message(DriverRegistration.tech, F.photo)
async def driver_tech(message, state):

    await state.update_data(
        tech_file=message.photo[-1].file_id
    )

    await state.set_state(
        DriverRegistration.photo
    )

    await message.answer(
        """
🚗 Avtomobilingizning rasmini yuboring.
"""
    )


@dp.message(DriverRegistration.photo, F.photo)
async def driver_photo(message, state):

    data = await state.get_data()

    conn = db()

    conn.execute(
        """
        INSERT OR REPLACE INTO drivers(
            tg_id,
            name,
            phone,
            car,
            plate,
            license_file,
            tech_file,
            car_photo,
            approved,
            online,
            active_orders,
            rating,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 5.0, ?)
        """,
        (
            message.from_user.id,
            data["name"],
            data["phone"],
            data["car"],
            data["plate"],
            data["license_file"],
            data["tech_file"],
            message.photo[-1].file_id,
            now()
        )
    )

    conn.commit()
    conn.close()

    await state.clear()

    await message.answer(
        """
✅ <b>ARIZA QABUL QILINDI</b>

📍 Yo‘nalish:
OBLIQ ↔ ANGREN

⏳ Admin hujjatlaringizni tekshiradi.

Tasdiqlangandan keyin ONLINE bo‘la olasiz.
""",
        parse_mode="HTML",
        reply_markup=main_menu()
    )

    if ADMIN_ID:

        keyboard = InlineKeyboardBuilder()

        keyboard.button(
            text="✅ TASDIQLASH",
            callback_data=f"approve:{message.from_user.id}"
        )

        keyboard.button(
            text="❌ RAD ETISH",
            callback_data=f"reject:{message.from_user.id}"
        )

        keyboard.adjust(2)

        await bot.send_message(
            ADMIN_ID,
            f"""
🚕 <b>YANGI HAYDOVCHI</b>

👤 {data['name']}
📞 {data['phone']}
🚗 {data['car']}
🔢 {data['plate']}

📍 Yo‘nalish:
OBLIQ ↔ ANGREN
""",
            parse_mode="HTML",
            reply_markup=keyboard.as_markup()
        )


# =========================================================
# ADMIN DRIVER APPROVAL
# =========================================================

@dp.callback_query(F.data.startswith("approve:"))
async def approve_driver(callback):

    if callback.from_user.id != ADMIN_ID:
        return await callback.answer(
            "⛔ Ruxsat yo‘q.",
            show_alert=True
        )

    user_id = int(
        callback.data.split(":")[1]
    )

    conn = db()

    conn.execute(
        """
        UPDATE drivers
        SET approved=1
        WHERE tg_id=?
        """,
        (user_id,)
    )

    conn.commit()
    conn.close()

    try:
        await bot.send_message(
            user_id,
            """
🎉 <b>HAYDOVCHI PROFILINGIZ TASDIQLANDI!</b>

Endi:

🚕 HAYDOVCHI
→ 🟢 ONLINE

qilib buyurtmalarni qabul qilishingiz mumkin.
""",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await callback.message.edit_text(
        "✅ Haydovchi tasdiqlandi."
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("reject:"))
async def reject_driver(callback):

    if callback.from_user.id != ADMIN_ID:
        return await callback.answer(
            "⛔ Ruxsat yo‘q.",
            show_alert=True
        )

    user_id = int(
        callback.data.split(":")[1]
    )

    try:
        await bot.send_message(
            user_id,
            """
❌ <b>Haydovchi arizasi rad etildi.</b>

Qo‘shimcha ma’lumot uchun admin bilan bog‘laning.
""",
            parse_mode="HTML"
        )
    except Exception:
        pass

    await callback.message.edit_text(
        "❌ Haydovchi rad etildi."
    )

    await callback.answer()


# =========================================================
# DRIVER ONLINE / OFFLINE
# =========================================================

@dp.message(F.text.in_({"🟢 ONLINE", "⚪ OFFLINE"}))
async def toggle_online(message):

    driver = get_driver(
        message.from_user.id
    )

    if not driver:
        return await message.answer(
            "Avval haydovchi sifatida ro‘yxatdan o‘ting."
        )

    if not driver["approved"]:
        return await message.answer(
            "⏳ Admin tasdig‘i kerak."
        )

    new_status = 0 if driver["online"] else 1

    conn = db()

    conn.execute(
        """
        UPDATE drivers
        SET online=?
        WHERE tg_id=?
        """,
        (new_status, message.from_user.id)
    )

    conn.commit()
    conn.close()

    if new_status:
        await message.answer(
            """
🟢 <b>ONLINE</b>

Sizga yangi buyurtmalar yuboriladi.
""",
            parse_mode="HTML"
        )
    else:
        await message.answer(
            """
⚪ <b>OFFLINE</b>

Sizga yangi buyurtmalar yuborilmaydi.
""",
            parse_mode="HTML"
        )


# =========================================================
# DISPATCH
# =========================================================

async def dispatch_order(order_id):

    await asyncio.sleep(1)

    conn = db()

    order = conn.execute(
        "SELECT * FROM orders WHERE id=?",
        (order_id,)
    ).fetchone()

    drivers = conn.execute(
        """
        SELECT *
        FROM drivers
        WHERE approved=1
        AND online=1
        AND active_orders < 7
        """,
    ).fetchall()

    conn.close()

    if not order:
        return

    if order["status"] != "SEARCHING":
        return

    if not drivers:

        await asyncio.sleep(60)

        conn = db()

        current = conn.execute(
            "SELECT status, customer_id FROM orders WHERE id=?",
            (order_id,)
        ).fetchone()

        conn.close()

        if current and current["status"] == "SEARCHING":

            conn = db()

            conn.execute(
                """
                UPDATE orders
                SET status='NO_DRIVER'
                WHERE id=?
                """,
                (order_id,)
            )

            conn.commit()
            conn.close()

            try:
                await bot.send_message(
                    current["customer_id"],
                    """
⚠️ <b>Hozircha haydovchi topilmadi.</b>

🟢 HA, KUTAMAN
🔴 YO‘Q, KERAK EMAS
""",
                    parse_mode="HTML"
                )
            except Exception:
                pass

        return

    keyboard = InlineKeyboardBuilder()

    keyboard.button(
        text="🚕 BUYURTMANI OLISH",
        callback_data=f"take:{order_id}"
    )

    service_name = (
        "👤 YO‘LOVCHI"
        if order["service"] == "passenger"
        else "📦 DASTAVKA"
    )

    text = f"""
🚕 <b>YANGI BUYURTMA #{order_id}</b>

{service_name}

📍 <b>{order['origin']}</b>
🏁 <b>{order['destination']}</b>

"""

    if order["service"] == "passenger":
        text += f"👥 {order['passengers']} kishi\n"

    text += f"""
💰 <b>{order['price']:,} so‘m</b>

⏱ Qabul qilish uchun vaqt cheklangan.
"""

    sent = 0

    for driver in drivers:

        try:
            await bot.send_message(
                driver["tg_id"],
                text,
                parse_mode="HTML",
                reply_markup=keyboard.as_markup()
            )

            sent += 1

        except Exception as e:
            print(
                "Driver send error:",
                driver["tg_id"],
                e
            )

    print(
        f"ORDER #{order_id} yuborildi: {sent} ta haydovchiga"
    )

    await asyncio.sleep(120)

    conn = db()

    current = conn.execute(
        "SELECT status, customer_id FROM orders WHERE id=?",
        (order_id,)
    ).fetchone()

    conn.close()

    if current and current["status"] == "SEARCHING":

        conn = db()

        conn.execute(
            """
            UPDATE orders
            SET status='NO_RESPONSE'
            WHERE id=?
            """,
            (order_id,)
        )

        conn.commit()
        conn.close()

        try:
            await bot.send_message(
                current["customer_id"],
                """
⚠️ <b>Hozircha buyurtma qabul qilinmadi.</b>

Keyinroq yana urinib ko‘rishingiz mumkin.
""",
                parse_mode="HTML"
            )
        except Exception:
            pass


# =========================================================
# DRIVER TAKES ORDER
# =========================================================

@dp.callback_query(F.data.startswith("take:"))
async def take_order(callback):

    order_id = int(
        callback.data.split(":")[1]
    )

    driver = get_driver(
        callback.from_user.id
    )

    if not driver:
        return await callback.answer(
            "Haydovchi profili topilmadi.",
            show_alert=True
        )

    if not driver["approved"]:
        return await callback.answer(
            "Profilingiz tasdiqlanmagan.",
            show_alert=True
        )

    if not driver["online"]:
        return await callback.answer(
            "Siz OFFLINE holatdasiz.",
            show_alert=True
        )

    if driver["active_orders"] >= 7:
        return await callback.answer(
            "⚠️ Sizda 7 ta faol buyurtma bor.",
            show_alert=True
        )

    conn = db()

    # ATOMIC CLAIM
    cursor = conn.execute(
        """
        UPDATE orders
        SET
            driver_id=?,
            status='ACCEPTED',
            claimed_at=?
        WHERE
            id=?
            AND status='SEARCHING'
            AND driver_id IS NULL
        """,
        (
            callback.from_user.id,
            now(),
            order_id
        )
    )

    if cursor.rowcount == 0:

        conn.rollback()
        conn.close()

        return await callback.answer(
            "⚠️ Bu buyurtmani boshqa haydovchi oldi.",
            show_alert=True
        )

    conn.execute(
        """
        UPDATE drivers
        SET active_orders=active_orders+1
        WHERE tg_id=?
        """,
        (callback.from_user.id,)
    )

    conn.commit()

    order = conn.execute(
        "SELECT * FROM orders WHERE id=?",
        (order_id,)
    ).fetchone()

    conn.close()

    log_event(
        order_id,
        callback.from_user.id,
        "DRIVER_ACCEPTED"
    )

    customer = get_user(
        order["customer_id"]
    )

    await callback.message.edit_text(
        f"""
✅ <b>BUYURTMA QABUL QILINDI #{order_id}</b>

📍 {order['origin']}
🏁 {order['destination']}

💰 {order['price']:,} so‘m

👤 Mijoz:
{customer['name'] if customer else 'Noma’lum'}

📞 Telefon:
{customer['phone'] if customer and customer['phone'] else 'Mavjud emas'}
""",
        parse_mode="HTML"
    )

    try:

        await bot.send_message(
            order["customer_id"],
            f"""
🚕 <b>HAYDOVCHI TOPILDI!</b>

👤 {driver['name']}

🚗 {driver['car']}

🔢 {driver['plate']}

📞 {driver['phone']}

💰 {order['price']:,} so‘m
""",
            parse_mode="HTML"
        )

    except Exception:
        pass

    # Other drivers get information
    conn = db()

    others = conn.execute(
        """
        SELECT tg_id
        FROM drivers
        WHERE approved=1
        AND tg_id != ?
        """,
        (callback.from_user.id,)
    ).fetchall()

    conn.close()

    for other in others:

        try:
            await bot.send_message(
                other["tg_id"],
                f"""
⚠️ Buyurtma #{order_id}
boshqa haydovchi tomonidan qabul qilindi.
"""
            )
        except Exception:
            pass

    await callback.answer(
        "✅ Buyurtma sizniki!"
    )


# =========================================================
# SUPPORT
# =========================================================

@dp.message(F.text == "📩 TAKLIF VA MUROJAATLAR")
async def support_start(message, state):

    await state.set_state(
        Support.message
    )

    await message.answer(
        """
📩 <b>TAKLIF VA MUROJAATLAR</b>

Taklif, shikoyat yoki savolingizni yozing.
""",
        parse_mode="HTML",
        reply_markup=back_menu()
    )


@dp.message(Support.message, F.text)
async def support_message(message, state):

    if message.text == "⬅️ ORQAGA":
        await state.clear()
        return await show_main_menu(message)

    ticket_id = (
        "MR-" +
        str(message.from_user.id)[-6:] +
        str(int(datetime.now().timestamp()))[-4:]
    )

    if ADMIN_ID:

        await bot.send_message(
            ADMIN_ID,
            f"""
📩 <b>YANGI MUROJAAT</b>

🎫 ID:
<code>{ticket_id}</code>

👤 Ism:
{message.from_user.full_name}

🆔 Telegram ID:
<code>{message.from_user.id}</code>

💬 Xabar:

{message.text}
""",
            parse_mode="HTML"
        )

    await state.clear()

    await message.answer(
        f"""
✅ Murojaatingiz qabul qilindi.

🎫 Murojaat ID:
<code>{ticket_id}</code>
""",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


# =========================================================
# CUSTOMER HISTORY
# =========================================================

@dp.message(F.text == "📜 BUYURTMALAR TARIXI")
async def customer_history(message):

    conn = db()

    orders = conn.execute(
        """
        SELECT *
        FROM orders
        WHERE customer_id=?
        ORDER BY id DESC
        LIMIT 10
        """,
        (message.from_user.id,)
    ).fetchall()

    conn.close()

    if not orders:
        return await message.answer(
            "📜 Hozircha buyurtmalar yo‘q."
        )

    text = "📜 <b>BUYURTMALAR TARIXI</b>\n\n"

    for order in orders:

        text += (
            f"#{order['id']} | "
            f"{order['origin']} → "
            f"{order['destination']} | "
            f"{order['price']:,} so‘m | "
            f"{order['status']}\n\n"
        )

    await message.answer(
        text,
        parse_mode="HTML"
    )


# =========================================================
# CUSTOMER PROFILE
# =========================================================

@dp.message(F.text == "👤 PROFIL")
async def customer_profile(message):

    user = get_user(
        message.from_user.id
    )

    if not user:

        return await message.answer(
            "Profil topilmadi."
        )

    await message.answer(
        f"""
👤 <b>PROFIL</b>

Ism:
<b>{user['name']}</b>

Telefon:
<b>{user['phone'] or 'Kiritilmagan'}</b>

Til:
<b>{user['lang']}</b>
""",
        parse_mode="HTML"
    )


# =========================================================
# BACK
# =========================================================

@dp.message(F.text == "⬅️ ORQAGA")
async def back(message, state):

    await state.clear()

    await show_main_menu(message)


# =========================================================
# FALLBACK
# =========================================================

@dp.message()
async def fallback(message):

    await message.answer(
        """
🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>

Iltimos, menyudagi tugmalardan foydalaning.
""",
        parse_mode="HTML",
        reply_markup=main_menu()
    )


# =========================================================
# START BOT
# =========================================================

async def run():

    global bot

    init_db()

    bot = Bot(
        token=BOT_TOKEN
    )

    print(
        "===================================="
    )

    print(
        "TAXI BOR MI? BOT ISHGA TUSHDI"
    )

    print(
        "OBLIQ <-> ANGREN"
    )

    print(
        "AI:",
        "YOQ" if ai else "KALIT YO‘Q"
    )

    print(
        "===================================="
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":
    asyncio.run(run())
