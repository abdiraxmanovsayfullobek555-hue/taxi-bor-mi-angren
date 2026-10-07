import os
import re
import json
import time
import sqlite3
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None


# ============================================================
# SETTINGS
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)

DB_PATH = os.getenv("DB_PATH", "taxi_bor_mi.db")

ROUTE_NAME = "OBLIQ ↔ ANGREN"
ORDER_CLAIM_SECONDS = 120
MAX_ACTIVE_ORDERS = 7

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN topilmadi. Railway Variables bo'limiga BOT_TOKEN qo'shing."
    )


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

logger = logging.getLogger("taxi-bor-mi")


# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db_lock = asyncio.Lock()


def db_execute(
    sql: str,
    params: tuple = (),
    fetchone: bool = False,
    fetchall: bool = False,
    commit: bool = True,
):
    cur = db.cursor()
    cur.execute(sql, params)

    result = None

    if fetchone:
        result = cur.fetchone()

    if fetchall:
        result = cur.fetchall()

    if commit:
        db.commit()

    return result


def init_db():
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            full_name TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            language TEXT DEFAULT 'uz',
            role TEXT DEFAULT 'customer',
            blocked INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS drivers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            full_name TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            car_model TEXT DEFAULT '',
            plate TEXT DEFAULT '',
            license_photo TEXT DEFAULT '',
            tech_passport_photo TEXT DEFAULT '',
            car_photo TEXT DEFAULT '',
            route TEXT DEFAULT 'OBLIQ ↔ ANGREN',
            approved INTEGER DEFAULT 0,
            online INTEGER DEFAULT 0,
            rating REAL DEFAULT 5.0,
            rating_count INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            service TEXT NOT NULL,
            origin TEXT DEFAULT '',
            destination TEXT DEFAULT '',
            latitude REAL,
            longitude REAL,
            passengers INTEGER DEFAULT 1,
            price INTEGER DEFAULT 0,
            status TEXT DEFAULT 'searching',
            driver_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            accepted_at TEXT,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS order_offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            driver_id INTEGER NOT NULL,
            status TEXT DEFAULT 'sent',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(order_id, driver_id)
        );

        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            from_user INTEGER NOT NULL,
            to_user INTEGER NOT NULL,
            rating INTEGER NOT NULL,
            comment TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS support_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            status TEXT DEFAULT 'new',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS order_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            event TEXT NOT NULL,
            telegram_id INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    db.commit()


init_db()


# ============================================================
# BOT / DISPATCHER
# ============================================================

bot = Bot(BOT_TOKEN)
dp = Dispatcher()

openai_client = None

if OPENAI_API_KEY and AsyncOpenAI:
    try:
        openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        logger.info("OpenAI AI yoqildi.")
    except Exception as e:
        logger.error("OpenAI ulanish xatosi: %s", e)
        openai_client = None
else:
    logger.warning(
        "OPENAI_API_KEY topilmadi. Deterministic route parser ishlaydi."
    )


# ============================================================
# FSM STATES
# ============================================================

class RegistrationState(StatesGroup):
    name = State()
    phone = State()


class OrderState(StatesGroup):
    waiting_location = State()
    waiting_route = State()
    waiting_passengers = State()
    waiting_price = State()


class DriverRegistrationState(StatesGroup):
    name = State()
    phone = State()
    car_model = State()
    plate = State()
    license = State()
    tech = State()
    car_photo = State()


class SupportState(StatesGroup):
    message = State()


# ============================================================
# KEYBOARDS
# ============================================================

def main_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🚕 YO‘LOVCHI"),
                KeyboardButton(text="📦 DASTAVKA"),
            ],
            [
                KeyboardButton(text="🚗 HAYDOVCHI"),
            ],
            [
                KeyboardButton(text="📜 BUYURTMALARIM"),
                KeyboardButton(text="👤 PROFIL"),
            ],
            [
                KeyboardButton(text="📩 MUROJAAT"),
            ],
        ],
        resize_keyboard=True,
    )


def phone_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📱 Telefon raqamni yuborish",
                    request_contact=True,
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def location_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📍 Joylashuvimni yuborish",
                    request_location=True,
                )
            ],
            [
                KeyboardButton(text="❌ BEKOR QILISH"),
            ],
        ],
        resize_keyboard=True,
    )


def passenger_keyboard():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="1"),
                KeyboardButton(text="2"),
                KeyboardButton(text="3"),
            ],
            [
                KeyboardButton(text="4"),
                KeyboardButton(text="5"),
                KeyboardButton(text="6"),
            ],
            [
                KeyboardButton(text="7"),
            ],
            [
                KeyboardButton(text="❌ BEKOR QILISH"),
            ],
        ],
        resize_keyboard=True,
    )


def driver_keyboard(online: bool = False):
    status = "⚪ OFFLINE" if online else "🟢 ONLINE"

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=status),
            ],
            [
                KeyboardButton(text="📋 YANGI BUYURTMALAR"),
                KeyboardButton(text="🚕 FAOL BUYURTMALAR"),
            ],
            [
                KeyboardButton(text="📜 BUYURTMALAR TARIXI"),
                KeyboardButton(text="💰 DAROMAD"),
            ],
            [
                KeyboardButton(text="⭐ REYTING"),
                KeyboardButton(text="👤 PROFIL"),
            ],
            [
                KeyboardButton(text="📩 MUROJAAT"),
            ],
        ],
        resize_keyboard=True,
    )


def claim_keyboard(order_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚕 BUYURTMANI OLISH",
                    callback_data=f"claim:{order_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ RAD ETISH",
                    callback_data=f"decline:{order_id}",
                )
            ],
        ]
    )


def customer_confirm_keyboard():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ BUYURTMA BERISH",
                    callback_data="confirm_order",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ O‘ZGARTIRISH",
                    callback_data="edit_order",
                ),
                InlineKeyboardButton(
                    text="❌ BEKOR QILISH",
                    callback_data="cancel_order",
                ),
            ],
        ]
    )


def accepted_keyboard(order_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚗 YO‘LGA CHIQDIM",
                    callback_data=f"status:{order_id}:on_way",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👤 MIJOZ OLINDI",
                    callback_data=f"status:{order_id}:picked",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ YAKUNLASH",
                    callback_data=f"status:{order_id}:completed",
                )
            ],
        ]
    )


def rating_keyboard(order_id: int):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ 1",
                    callback_data=f"rate:{order_id}:1",
                ),
                InlineKeyboardButton(
                    text="⭐ 2",
                    callback_data=f"rate:{order_id}:2",
                ),
                InlineKeyboardButton(
                    text="⭐ 3",
                    callback_data=f"rate:{order_id}:3",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⭐ 4",
                    callback_data=f"rate:{order_id}:4",
                ),
                InlineKeyboardButton(
                    text="⭐ 5",
                    callback_data=f"rate:{order_id}:5",
                ),
            ],
        ]
    )


# ============================================================
# HELPERS
# ============================================================

def normalize_text(text: str) -> str:
    text = text.lower().strip()

    replacements = {
        "ў": "у",
        "қ": "к",
        "ғ": "г",
        "ҳ": "х",
        "ё": "е",
        "ъ": "",
        "ь": "",
    }

    for a, b in replacements.items():
        text = text.replace(a, b)

    text = re.sub(r"\s+", " ", text)
    return text


def clean_place(value: str) -> str:
    value = value.strip(" .,!?-")

    value = re.sub(
        r"^(dan|дан|from|из)\s+",
        "",
        value,
        flags=re.IGNORECASE,
    )

    value = re.sub(
        r"(ga|ка|to|до)$",
        "",
        value,
        flags=re.IGNORECASE,
    )

    aliases = {
        "obliq": "Obliq",
        "oblik": "Obliq",
        "облик": "Obliq",
        "обликдан": "Obliq",
        "obliqdan": "Obliq",
        "hokimiyat": "Hokimiyat",
        "хокимият": "Hokimiyat",
        "hokimiyatga": "Hokimiyat",
    }

    key = normalize_text(value)

    if key in aliases:
        return aliases[key]

    return value


# ============================================================
# DETERMINISTIC ROUTE PARSER
# ============================================================

def deterministic_route_parser(text: str) -> Optional[Tuple[str, str]]:
    """
    AI ishlamasa ham:
      Obliqdan Hokimiyatga
      Obliq -> Hokimiyat
      Obliq → Hokimiyat
      Obliqdan hokimiyatga
      Men Obliqdan Hokimiyatga boraman
    kabi formatlarni tushunadi.
    """

    original = text.strip()
    normalized = normalize_text(original)

    # Eng muhim format:
    # Obliqdan Hokimiyatga
    pattern = re.search(
        r"\bobliq\s*dan\s+(.+?)\s*(?:ga|ka)?$",
        normalized,
    )

    if pattern:
        destination = clean_place(pattern.group(1))
        if destination:
            return "Obliq", destination

    # "Obliq -> Hokimiyat"
    arrow_pattern = re.search(
        r"\bobliq\s*(?:->|→|➜|➡|to|dan)\s*(.+)$",
        normalized,
    )

    if arrow_pattern:
        destination = clean_place(arrow_pattern.group(1))
        if destination:
            return "Obliq", destination

    # "Obliq Hokimiyat"
    if normalized.startswith("obliq "):
        rest = normalized[len("obliq "):].strip()

        rest = re.sub(
            r"^(dan|->|→|to)\s*",
            "",
            rest,
        )

        destination = clean_place(rest)

        if destination:
            return "Obliq", destination

    # "Men Obliqdan Hokimiyatga boraman"
    pattern2 = re.search(
        r"\bobliq\s*dan\s+(.+?)\s+(?:boraman|boramiz|ketaman|ketamiz)$",
        normalized,
    )

    if pattern2:
        destination = clean_place(pattern2.group(1))
        if destination:
            return "Obliq", destination

    # "Obliqdan Hokimiyatga boraman"
    pattern3 = re.search(
        r"\bobliq\s*dan\s+(.+?)\s+(?:boraman|boramiz|ketaman|ketamiz)",
        normalized,
    )

    if pattern3:
        destination = clean_place(pattern3.group(1))
        if destination:
            return "Obliq", destination

    return None


# ============================================================
# OPENAI AI ROUTE PARSER
# ============================================================

async def ai_parse_route(text: str) -> Optional[Tuple[str, str]]:
    """
    AI:
    user -> "Men Obliqdan Hokimiyatga boraman"

    return:
    origin = Obliq
    destination = Hokimiyat
    """

    if not openai_client:
        return None

    prompt = f"""
Sen TAXI BOR MI? — ALBATTA BOR! tizimining route parser AI'sisan.

Faqat JSON qaytar.

Foydalanuvchi yozgani:
{text}

Vazifa:
1. Qayerdan ketayotganini top.
2. Qayerga ketayotganini top.
3. Uzbek Latin, Uzbek Cyrillic va Russian tabiiy gaplarni tushun.
4. "Obliqdan Hokimiyatga" deganda:
   origin = "Obliq"
   destination = "Hokimiyat"
5. "Obliq → Hokimiyat" ham xuddi shunday.
6. "Men Obliqdan Hokimiyatga boraman" ham xuddi shunday.
7. Agar ma'lumot yetarli bo'lmasa null qaytar.

JSON:
{{
  "origin": "Obliq",
  "destination": "Hokimiyat"
}}

Yoki:
{{
  "origin": null,
  "destination": null
}}
"""

    try:
        response = await openai_client.responses.create(
            model=OPENAI_MODEL,
            input=prompt,
        )

        text_out = getattr(response, "output_text", "") or ""

        text_out = text_out.strip()

        match = re.search(r"\{.*\}", text_out, re.S)

        if not match:
            return None

        data = json.loads(match.group(0))

        origin = data.get("origin")
        destination = data.get("destination")

        if not origin or not destination:
            return None

        return clean_place(origin), clean_place(destination)

    except Exception as e:
        logger.error("AI route parser error: %s", e)
        return None


async def parse_route(text: str) -> Optional[Tuple[str, str]]:
    # Avval deterministic parser.
    result = deterministic_route_parser(text)

    if result:
        return result

    # Keyin AI.
    result = await ai_parse_route(text)

    return result


# ============================================================
# USER FUNCTIONS
# ============================================================

def get_user(telegram_id: int):
    return db_execute(
        "SELECT * FROM users WHERE telegram_id = ?",
        (telegram_id,),
        fetchone=True,
    )


def create_or_update_user(
    telegram_id: int,
    full_name: str = "",
    phone: str = "",
):
    existing = get_user(telegram_id)

    if existing:
        db_execute(
            """
            UPDATE users
            SET full_name = COALESCE(NULLIF(?, ''), full_name),
                phone = COALESCE(NULLIF(?, ''), phone)
            WHERE telegram_id = ?
            """,
            (full_name, phone, telegram_id),
        )
    else:
        db_execute(
            """
            INSERT INTO users
            (telegram_id, full_name, phone)
            VALUES (?, ?, ?)
            """,
            (telegram_id, full_name, phone),
        )


def is_blocked(telegram_id: int) -> bool:
    row = get_user(telegram_id)

    if not row:
        return False

    return bool(row["blocked"])


def get_driver(telegram_id: int):
    return db_execute(
        "SELECT * FROM drivers WHERE telegram_id = ?",
        (telegram_id,),
        fetchone=True,
    )


def active_driver_orders(driver_id: int) -> int:
    row = db_execute(
        """
        SELECT COUNT(*) AS c
        FROM orders
        WHERE driver_id = ?
        AND status IN ('accepted', 'on_way', 'picked')
        """,
        (driver_id,),
        fetchone=True,
    )

    return int(row["c"])


def eligible_drivers():
    return db_execute(
        """
        SELECT *
        FROM drivers
        WHERE approved = 1
        AND online = 1
        AND route = ?
        """,
        (ROUTE_NAME,),
        fetchall=True,
    )


def log_event(order_id: int, event: str, telegram_id: Optional[int] = None):
    db_execute(
        """
        INSERT INTO order_events
        (order_id, event, telegram_id)
        VALUES (?, ?, ?)
        """,
        (order_id, event, telegram_id),
    )


# ============================================================
# REGISTRATION
# ============================================================

async def ensure_customer_registration(
    message: Message,
    state: FSMContext,
):
    user = get_user(message.from_user.id)

    if user and user["full_name"] and user["phone"]:
        return True

    await state.set_state(RegistrationState.name)

    await message.answer(
        "👋 Assalomu alaykum!\n\n"
        "🚕 TAXI BOR MI? — ALBATTA BOR!\n\n"
        "Avval ro‘yxatdan o‘tamiz.\n\n"
        "👤 Ismingizni kiriting:"
    )

    return False


# ============================================================
# START
# ============================================================

@dp.message(CommandStart())
async def start_handler(message: Message, state: FSMContext):
    await state.clear()

    if is_blocked(message.from_user.id):
        await message.answer(
            "⛔ Sizning akkauntingiz vaqtincha bloklangan."
        )
        return

    user = get_user(message.from_user.id)

    if not user or not user["full_name"] or not user["phone"]:
        await state.set_state(RegistrationState.name)

        await message.answer(
            "🚕 TAXI BOR MI? — ALBATTA BOR!\n\n"
            "📍 Hozirgi yo‘nalish:\n"
            "OBLIQ ↔ ANGGREN\n\n"
            "Ro‘yxatdan o‘tishni boshlaymiz.\n\n"
            "👤 Ismingizni kiriting:"
        )
        return

    await message.answer(
        "🚕 TAXI BOR MI? — ALBATTA BOR!\n\n"
        "⚡ Tez • Qulay • Ishonchli\n\n"
        "📍 OBLIQ ↔ ANGGREN",
        reply_markup=main_keyboard(),
    )


# ============================================================
# CUSTOMER REGISTRATION
# ============================================================

@dp.message(RegistrationState.name)
async def registration_name(
    message: Message,
    state: FSMContext,
):
    name = message.text.strip()

    if len(name) < 2:
        await message.answer("❗ Iltimos, ismingizni to‘liqroq kiriting.")
        return

    await state.update_data(name=name)
    await state.set_state(RegistrationState.phone)

    await message.answer(
        "📱 Telefon raqamingizni yuboring:",
        reply_markup=phone_keyboard(),
    )


@dp.message(RegistrationState.phone)
async def registration_phone(
    message: Message,
    state: FSMContext,
):
    phone = ""

    if message.contact:
        phone = message.contact.phone_number
    elif message.text:
        phone = message.text.strip()

    if not phone:
        await message.answer(
            "❗ Telefon raqamingizni yuboring.",
            reply_markup=phone_keyboard(),
        )
        return

    data = await state.get_data()
    name = data.get("name", message.from_user.full_name)

    create_or_update_user(
        message.from_user.id,
        name,
        phone,
    )

    await state.clear()

    await message.answer(
        "✅ Ro‘yxatdan o‘tish yakunlandi!\n\n"
        f"👤 Ism: {name}\n"
        f"📱 Telefon: {phone}\n\n"
        "🚕 Endi buyurtma berishingiz mumkin.",
        reply_markup=main_keyboard(),
    )


# ============================================================
# CUSTOMER TAXI
# ============================================================

@dp.message(F.text == "🚕 YO‘LOVCHI")
async def passenger_start(
    message: Message,
    state: FSMContext,
):
    if is_blocked(message.from_user.id):
        await message.answer("⛔ Akkauntingiz bloklangan.")
        return

    if not await ensure_customer_registration(message, state):
        return

    await state.update_data(service="passenger")

    await state.set_state(OrderState.waiting_location)

    await message.answer(
        "🚕 YO‘LOVCHI BUYURTMASI\n\n"
        "📍 Avval hozirgi joylashuvingizni yuboring.\n\n"
        "Keyin:\n"
        "📍 Qayerdan → 🏁 Qayerga\n"
        "formatida manzil kiritasiz.\n\n"
        "Masalan:\n"
        "👉 Obliqdan Hokimiyatga",
        reply_markup=location_keyboard(),
    )


# ============================================================
# DELIVERY
# ============================================================

@dp.message(F.text == "📦 DASTAVKA")
async def delivery_start(
    message: Message,
    state: FSMContext,
):
    if is_blocked(message.from_user.id):
        await message.answer("⛔ Akkauntingiz bloklangan.")
        return

    if not await ensure_customer_registration(message, state):
        return

    await state.update_data(service="delivery")

    await state.set_state(OrderState.waiting_location)

    await message.answer(
        "📦 DASTAVKA BUYURTMASI\n\n"
        "📍 Avval hozirgi joylashuvingizni yuboring.\n\n"
        "Keyin:\n"
        "📍 Qayerdan → 🏁 Qayerga\n\n"
        "Masalan:\n"
        "👉 Obliqdan Hokimiyatga",
        reply_markup=location_keyboard(),
    )


# ============================================================
# LOCATION
# ============================================================

@dp.message(OrderState.waiting_location, F.location)
async def order_location(
    message: Message,
    state: FSMContext,
):
    await state.update_data(
        latitude=message.location.latitude,
        longitude=message.location.longitude,
    )

    await state.set_state(OrderState.waiting_route)

    await message.answer(
        "📍 Joylashuvingiz qabul qilindi.\n\n"
        "Endi safar manzilini kiriting:\n\n"
        "📍 Qayerdan → 🏁 Qayerga\n\n"
        "Masalan:\n"
        "👉 Obliqdan Hokimiyatga\n\n"
        "Yoki:\n"
        "👉 Obliq → Hokimiyat",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="❌ BEKOR QILISH")]
            ],
            resize_keyboard=True,
        ),
    )


# ============================================================
# ROUTE
# ============================================================

@dp.message(OrderState.waiting_route)
async def order_route(
    message: Message,
    state: FSMContext,
):
    if not message.text:
        await message.answer(
            "❗ Manzilni matn ko‘rinishida kiriting.\n\n"
            "Masalan: Obliqdan Hokimiyatga"
        )
        return

    text = message.text.strip()

    if normalize_text(text) in {
        "bekor qilish",
        "cancel",
        "отмена",
    }:
        await state.clear()
        await message.answer(
            "❌ Buyurtma bekor qilindi.",
            reply_markup=main_keyboard(),
        )
        return

    result = await parse_route(text)

    if not result:
        await message.answer(
            "❗ Manzilni aniqlay olmadim.\n\n"
            "Masalan:\n"
            "👉 Obliqdan Hokimiyatga\n\n"
            "Yoki:\n"
            "👉 Obliq → Hokimiyat"
        )
        return

    origin, destination = result

    await state.update_data(
        origin=origin,
        destination=destination,
    )

    data = await state.get_data()

    if data.get("service") == "passenger":
        await state.set_state(OrderState.waiting_passengers)

        await message.answer(
            f"📍 Qayerdan: {origin}\n"
            f"🏁 Qayerga: {destination}\n\n"
            "👥 Necha kishi bor?\n"
            "1 dan 7 kishigacha tanlang.",
            reply_markup=passenger_keyboard(),
        )
    else:
        await state.set_state(OrderState.waiting_price)

        await message.answer(
            f"📍 Qayerdan: {origin}\n"
            f"🏁 Qayerga: {destination}\n\n"
            "💰 Taklif qilayotgan narxingizni kiriting.\n\n"
            "Masalan:\n"
            "30000"
        )


# ============================================================
# PASSENGERS
# ============================================================

@dp.message(OrderState.waiting_passengers)
async def order_passengers(
    message: Message,
    state: FSMContext,
):
    if not message.text:
        return

    if message.text == "❌ BEKOR QILISH":
        await state.clear()
        await message.answer(
            "❌ Buyurtma bekor qilindi.",
            reply_markup=main_keyboard(),
        )
        return

    try:
        count = int(message.text.strip())
    except ValueError:
        await message.answer("❗ 1 dan 7 gacha son tanlang.")
        return

    if not 1 <= count <= 7:
        await message.answer("❗ Yo‘lovchilar soni 1–7 oralig‘ida bo‘lishi kerak.")
        return

    await state.update_data(passengers=count)
    await state.set_state(OrderState.waiting_price)

    await message.answer(
        "💰 Endi yo‘l uchun taklif qilayotgan narxingizni kiriting.\n\n"
        "Masalan:\n"
        "30000"
    )


# ============================================================
# PRICE
# ============================================================

@dp.message(OrderState.waiting_price)
async def order_price(
    message: Message,
    state: FSMContext,
):
    if not message.text:
        return

    text = message.text.strip()

    if text == "❌ BEKOR QILISH":
        await state.clear()
        await message.answer(
            "❌ Buyurtma bekor qilindi.",
            reply_markup=main_keyboard(),
        )
        return

    numbers = re.sub(r"[^\d]", "", text)

    if not numbers:
        await message.answer(
            "❗ Narxni raqam bilan kiriting.\n\n"
            "Masalan: 30000"
        )
        return

    price = int(numbers)

    if price <= 0:
        await message.answer("❗ Narx 0 dan katta bo‘lishi kerak.")
        return

    if price > 100_000_000:
        await message.answer("❗ Narx juda katta.")
        return

    await state.update_data(price=price)

    data = await state.get_data()

    service = data.get("service")
    origin = data.get("origin")
    destination = data.get("destination")
    passengers = data.get("passengers", 1)

    if service == "passenger":
        text_confirm = (
            "🚕 BUYURTMA\n\n"
            f"📍 Qayerdan: {origin}\n"
            f"🏁 Qayerga: {destination}\n"
            f"👥 {passengers} kishi\n"
            f"💰 {price:,} so‘m\n\n"
            "Hammasi to‘g‘rimi?"
        )
    else:
        text_confirm = (
            "📦 DASTAVKA BUYURTMASI\n\n"
            f"📍 Qayerdan: {origin}\n"
            f"🏁 Qayerga: {destination}\n"
            f"💰 {price:,} so‘m\n\n"
            "Hammasi to‘g‘rimi?"
        )

    await message.answer(
        text_confirm,
        reply_markup=customer_confirm_keyboard(),
    )


# ============================================================
# CONFIRM ORDER
# ============================================================

@dp.callback_query(F.data == "confirm_order")
async def confirm_order(
    callback: CallbackQuery,
    state: FSMContext,
):
    user = get_user(callback.from_user.id)

    if not user:
        await callback.answer(
            "Avval ro‘yxatdan o‘ting.",
            show_alert=True,
        )
        return

    data = await state.get_data()

    if not data.get("origin") or not data.get("destination"):
        await callback.answer(
            "Buyurtma ma'lumotlari topilmadi.",
            show_alert=True,
        )
        return

    service = data.get("service", "passenger")
    origin = data["origin"]
    destination = data["destination"]
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    passengers = int(data.get("passengers", 1))
    price = int(data.get("price", 0))

    db_execute(
        """
        INSERT INTO orders
        (
            customer_id,
            service,
            origin,
            destination,
            latitude,
            longitude,
            passengers,
            price,
            status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'searching')
        """,
        (
            user["telegram_id"],
            service,
            origin,
            destination,
            latitude,
            longitude,
            passengers,
            price,
        ),
    )

    order = db_execute(
        """
        SELECT *
        FROM orders
        WHERE customer_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user["telegram_id"],),
        fetchone=True,
    )

    order_id = order["id"]

    log_event(
        order_id,
        "order_created",
        callback.from_user.id,
    )

    await state.clear()

    await callback.message.edit_text(
        "🔎 Buyurtmangiz qabul qilindi.\n\n"
        "🚕 Hozir haydovchilar qidirilmoqda...\n"
        "⏱ Birinchi qidiruv: 1 daqiqa."
    )

    await dispatch_order(order_id)

    await callback.answer()


# ============================================================
# EDIT / CANCEL
# ============================================================

@dp.callback_query(F.data == "edit_order")
async def edit_order(
    callback: CallbackQuery,
    state: FSMContext,
):
    await state.set_state(OrderState.waiting_route)

    await callback.message.answer(
        "✏️ Manzilni qayta kiriting.\n\n"
        "Masalan:\n"
        "👉 Obliqdan Hokimiyatga"
    )

    await callback.answer()


@dp.callback_query(F.data == "cancel_order")
async def cancel_order(
    callback: CallbackQuery,
    state: FSMContext,
):
    await state.clear()

    await callback.message.edit_text(
        "❌ Buyurtma bekor qilindi."
    )

    await callback.answer()


# ============================================================
# DISPATCH
# ============================================================

async def dispatch_order(order_id: int):
    order = db_execute(
        "SELECT * FROM orders WHERE id = ?",
        (order_id,),
        fetchone=True,
    )

    if not order:
        return

    drivers = eligible_drivers()

    sent = 0

    for driver in drivers:
        if active_driver_orders(driver["id"]) >= MAX_ACTIVE_ORDERS:
            continue

        existing = db_execute(
            """
            SELECT id
            FROM order_offers
            WHERE order_id = ?
            AND driver_id = ?
            """,
            (order_id, driver["id"]),
            fetchone=True,
        )

        if existing:
            continue

        db_execute(
            """
            INSERT OR IGNORE INTO order_offers
            (order_id, driver_id, status)
            VALUES (?, ?, 'sent')
            """,
            (order_id, driver["id"]),
        )

        service_text = (
            "🚕 YO‘LOVCHI"
            if order["service"] == "passenger"
            else "📦 DASTAVKA"
        )

        if order["service"] == "passenger":
            order_text = (
                f"{service_text} — YANGI BUYURTMA\n\n"
                f"🆔 Buyurtma: #{order['id']}\n"
                f"📍 Qayerdan: {order['origin']}\n"
                f"🏁 Qayerga: {order['destination']}\n"
                f"👥 Yo‘lovchi: {order['passengers']}\n"
                f"💰 Narx: {order['price']:,} so‘m\n\n"
                "⏱ Buyurtmani olish uchun 2 daqiqa."
            )
        else:
            order_text = (
                f"{service_text} — YANGI BUYURTMA\n\n"
                f"🆔 Buyurtma: #{order['id']}\n"
                f"📍 Qayerdan: {order['origin']}\n"
                f"🏁 Qayerga: {order['destination']}\n"
                f"💰 Narx: {order['price']:,} so‘m\n\n"
                "⏱ Buyurtmani olish uchun 2 daqiqa."
            )

        try:
            await bot.send_message(
                driver["telegram_id"],
                order_text,
                reply_markup=claim_keyboard(order_id),
            )

            sent += 1

        except Exception as e:
            logger.warning(
                "Driver %s ga buyurtma yuborilmadi: %s",
                driver["telegram_id"],
                e,
            )

    if sent == 0:
        await bot.send_message(
            order["customer_id"],
            "⚠️ Hozircha haydovchi topilmadi.\n\n"
            "🟢 HA, KUTAMAN\n"
            "❌ BEKOR QILISH",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🟢 HA, KUTAMAN",
                            callback_data=f"wait:{order_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ BEKOR QILISH",
                            callback_data=f"cancelsearch:{order_id}",
                        )
                    ],
                ]
            ),
        )

        return

    await asyncio.sleep(ORDER_CLAIM_SECONDS)

    current = db_execute(
        "SELECT status FROM orders WHERE id = ?",
        (order_id,),
        fetchone=True,
    )

    if current and current["status"] == "searching":
        await bot.send_message(
            order["customer_id"],
            "⚠️ Hozircha hech bir haydovchi buyurtmani qabul qilmadi.\n\n"
            "🟢 HA, KUTAMAN\n"
            "❌ BEKOR QILISH",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="🟢 HA, KUTAMAN",
                            callback_data=f"wait:{order_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ BEKOR QILISH",
                            callback_data=f"cancelsearch:{order_id}",
                        )
                    ],
                ]
            ),
        )


# ============================================================
# WAIT FOR DRIVER
# ============================================================

@dp.callback_query(F.data.startswith("wait:"))
async def wait_for_driver(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])

    order = db_execute(
        "SELECT * FROM orders WHERE id = ?",
        (order_id,),
        fetchone=True,
    )

    if not order:
        await callback.answer(
            "Buyurtma topilmadi.",
            show_alert=True,
        )
        return

    if order["status"] != "searching":
        await callback.answer(
            "Buyurtma allaqachon hal qilingan.",
            show_alert=True,
        )
        return

    await callback.message.edit_text(
        "🟢 Buyurtma qidiruvda qoldi.\n\n"
        "🚕 Haydovchilar qidirilmoqda..."
    )

    await dispatch_order(order_id)

    await callback.answer()


@dp.callback_query(F.data.startswith("cancelsearch:"))
async def cancel_search(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])

    order = db_execute(
        "SELECT * FROM orders WHERE id = ?",
        (order_id,),
        fetchone=True,
    )

    if not order:
        await callback.answer(
            "Buyurtma topilmadi.",
            show_alert=True,
        )
        return

    db_execute(
        """
        UPDATE orders
        SET status = 'cancelled'
        WHERE id = ?
        AND customer_id = ?
        AND status = 'searching'
        """,
        (order_id, callback.from_user.id),
    )

    log_event(
        order_id,
        "customer_cancelled",
        callback.from_user.id,
    )

    await callback.message.edit_text(
        "❌ Buyurtma bekor qilindi."
    )

    await callback.answer()


# ============================================================
# DRIVER CLAIM
# ============================================================

@dp.callback_query(F.data.startswith("claim:"))
async def claim_order(callback: CallbackQuery):
    driver = get_driver(callback.from_user.id)

    if not driver:
        await callback.answer(
            "Siz haydovchi sifatida ro‘yxatdan o‘tmagansiz.",
            show_alert=True,
        )
        return

    if not driver["approved"]:
        await callback.answer(
            "⏳ Akkauntingiz hali admin tomonidan tasdiqlanmagan.",
            show_alert=True,
        )
        return

    if not driver["online"]:
        await callback.answer(
            "Avval ONLINE bo‘ling.",
            show_alert=True,
        )
        return

    if active_driver_orders(driver["id"]) >= MAX_ACTIVE_ORDERS:
        await callback.answer(
            "Sizda 7 ta faol buyurtma mavjud.",
            show_alert=True,
        )
        return

    order_id = int(callback.data.split(":")[1])

    async with db_lock:
        order = db_execute(
            """
            SELECT *
            FROM orders
            WHERE id = ?
            """,
            (order_id,),
            fetchone=True,
        )

        if not order:
            await callback.answer(
                "Buyurtma topilmadi.",
                show_alert=True,
            )
            return

        if order["status"] != "searching":
            await callback.answer(
                "⚠️ Bu buyurtma boshqa haydovchi tomonidan qabul qilindi.",
                show_alert=True,
            )

            try:
                await callback.message.edit_reply_markup(
                    reply_markup=None
                )
            except Exception:
                pass

            return

        db_execute(
            """
            UPDATE orders
            SET status = 'accepted',
                driver_id = ?,
                accepted_at = CURRENT_TIMESTAMP
            WHERE id = ?
            AND status = 'searching'
            """,
            (driver["id"], order_id),
        )

        updated = db_execute(
            "SELECT * FROM orders WHERE id = ?",
            (order_id,),
            fetchone=True,
        )

        if updated["driver_id"] != driver["id"]:
            await callback.answer(
                "⚠️ Buyurtma boshqa haydovchi tomonidan qabul qilindi.",
                show_alert=True,
            )
            return

        db_execute(
            """
            UPDATE order_offers
            SET status = 'accepted'
            WHERE order_id = ?
            AND driver_id = ?
            """,
            (order_id, driver["id"]),
        )

    log_event(
        order_id,
        "driver_accepted",
        callback.from_user.id,
    )

    customer = get_user(order["customer_id"])

    customer_phone = customer["phone"] if customer else ""

    await callback.message.edit_text(
        "✅ BUYURTMA SIZNIKI!\n\n"
        f"🆔 #{order_id}\n"
        f"📍 {order['origin']}\n"
        f"🏁 {order['destination']}\n"
        f"💰 {order['price']:,} so‘m\n\n"
        "📞 Mijoz bilan bog‘laning."
    )

    await callback.message.answer(
        "🚕 FAOL BUYURTMA\n\n"
        f"👤 Mijoz: {customer['full_name'] if customer else 'Noma’lum'}\n"
        f"📱 Telefon: {customer_phone or 'Mavjud emas'}\n"
        f"📍 Qayerdan: {order['origin']}\n"
        f"🏁 Qayerga: {order['destination']}\n"
        f"💰 Narx: {order['price']:,} so‘m",
        reply_markup=accepted_keyboard(order_id),
    )

    await bot.send_message(
        order["customer_id"],
        "🎉 HAYDOVCHI TOPILDI!\n\n"
        f"🚗 Avtomobil: {driver['car_model']}\n"
        f"🔢 Raqam: {driver['plate']}\n"
        f"👤 Haydovchi: {driver['full_name']}\n"
        f"⭐ Reyting: {driver['rating']:.1f}\n"
        f"📱 Telefon: {driver['phone']}\n\n"
        f"📍 Qayerdan: {order['origin']}\n"
        f"🏁 Qayerga: {order['destination']}\n"
        f"💰 Narx: {order['price']:,} so‘m"
    )

    await callback.answer("✅ Buyurtma sizniki!")


# ============================================================
# DRIVER DECLINE
# ============================================================

@dp.callback_query(F.data.startswith("decline:"))
async def decline_order(callback: CallbackQuery):
    driver = get_driver(callback.from_user.id)

    if not driver:
        await callback.answer("Haydovchi topilmadi.", show_alert=True)
        return

    order_id = int(callback.data.split(":")[1])

    db_execute(
        """
        UPDATE order_offers
        SET status = 'declined'
        WHERE order_id = ?
        AND driver_id = ?
        """,
        (order_id, driver["id"]),
    )

    try:
        await callback.message.edit_reply_markup(
            reply_markup=None
        )
    except Exception:
        pass

    await callback.answer("Buyurtma rad etildi.")


# ============================================================
# ORDER STATUS
# ============================================================

@dp.callback_query(F.data.startswith("status:"))
async def order_status(callback: CallbackQuery):
    parts = callback.data.split(":")

    order_id = int(parts[1])
    new_status = parts[2]

    driver = get_driver(callback.from_user.id)

    if not driver:
        await callback.answer(
            "Haydovchi topilmadi.",
            show_alert=True,
        )
        return

    order = db_execute(
        """
        SELECT *
        FROM orders
        WHERE id = ?
        AND driver_id = ?
        """,
        (order_id, driver["id"]),
        fetchone=True,
    )

    if not order:
        await callback.answer(
            "Buyurtma topilmadi.",
            show_alert=True,
        )
        return

    db_execute(
        """
        UPDATE orders
        SET status = ?,
            completed_at =
                CASE
                    WHEN ? = 'completed'
                    THEN CURRENT_TIMESTAMP
                    ELSE completed_at
                END
        WHERE id = ?
        """,
        (new_status, new_status, order_id),
    )

    log_event(
        order_id,
        f"status_{new_status}",
        callback.from_user.id,
    )

    status_names = {
        "on_way": "🚗 Haydovchi yo‘lga chiqdi.",
        "picked": "👤 Mijoz olindi.",
        "completed": "✅ Buyurtma yakunlandi.",
    }

    text = status_names.get(
        new_status,
        "Buyurtma statusi yangilandi.",
    )

    await callback.message.answer(text)

    await bot.send_message(
        order["customer_id"],
        text,
    )

    if new_status == "completed":
        await bot.send_message(
            order["customer_id"],
            "⭐ Haydovchiga baho bering:",
            reply_markup=rating_keyboard(order_id),
        )

    await callback.answer()


# ============================================================
# RATING
# ============================================================

@dp.callback_query(F.data.startswith("rate:"))
async def rate_driver(callback: CallbackQuery):
    parts = callback.data.split(":")

    order_id = int(parts[1])
    rating = int(parts[2])

    order = db_execute(
        """
        SELECT *
        FROM orders
        WHERE id = ?
        AND customer_id = ?
        AND status = 'completed'
        """,
        (order_id, callback.from_user.id),
        fetchone=True,
    )

    if not order or not order["driver_id"]:
        await callback.answer(
            "Baholash mumkin emas.",
            show_alert=True,
        )
        return

    existing = db_execute(
        """
        SELECT id
        FROM ratings
        WHERE order_id = ?
        AND from_user = ?
        """,
        (order_id, callback.from_user.id),
        fetchone=True,
    )

    if existing:
        await callback.answer(
            "Siz bu buyurtmani baholagansiz.",
            show_alert=True,
        )
        return

    db_execute(
        """
        INSERT INTO ratings
        (order_id, from_user, to_user, rating)
        VALUES (?, ?, ?, ?)
        """,
        (
            order_id,
            callback.from_user.id,
            order["driver_id"],
            rating,
        ),
    )

    driver = db_execute(
        """
        SELECT *
        FROM drivers
        WHERE id = ?
        """,
        (order["driver_id"],),
        fetchone=True,
    )

    if driver:
        old_count = driver["rating_count"]
        old_rating = driver["rating"]

        new_count = old_count + 1
        new_rating = (
            (old_rating * old_count) + rating
        ) / new_count

        db_execute(
            """
            UPDATE drivers
            SET rating = ?,
                rating_count = ?
            WHERE id = ?
            """,
            (
                new_rating,
                new_count,
                driver["id"],
            ),
        )

    await callback.message.edit_text(
        f"⭐ Rahmat! Siz {rating}/5 baho berdingiz."
    )

    await callback.answer()


# ============================================================
# DRIVER REGISTRATION
# ============================================================

@dp.message(F.text == "🚗 HAYDOVCHI")
async def driver_start(
    message: Message,
    state: FSMContext,
):
    existing = get_driver(message.from_user.id)

    if existing:
        if existing["approved"]:
            await message.answer(
                "🚕 HAYDOVCHI PANELI",
                reply_markup=driver_keyboard(
                    bool(existing["online"])
                ),
            )
        else:
            await message.answer(
                "⏳ Siz haydovchi sifatida ro‘yxatdan o‘tgansiz.\n"
                "Admin tasdiqlashini kuting."
            )
        return

    await state.set_state(DriverRegistrationState.name)

    await message.answer(
        "🚗 HAYDOVCHI RO‘YXATDAN O‘TISH\n\n"
        "👤 F.I.Sh. ni kiriting:"
    )


@dp.message(DriverRegistrationState.name)
async def driver_name(
    message: Message,
    state: FSMContext,
):
    await state.update_data(name=message.text.strip())
    await state.set_state(DriverRegistrationState.phone)

    await message.answer(
        "📱 Telefon raqamingizni yuboring:",
        reply_markup=phone_keyboard(),
    )


@dp.message(DriverRegistrationState.phone)
async def driver_phone(
    message: Message,
    state: FSMContext,
):
    phone = ""

    if message.contact:
        phone = message.contact.phone_number
    elif message.text:
        phone = message.text.strip()

    if not phone:
        await message.answer("❗ Telefon raqam kerak.")
        return

    await state.update_data(phone=phone)
    await state.set_state(DriverRegistrationState.car_model)

    await message.answer(
        "🚗 Mashina modeli:\n\n"
        "Masalan: Chevrolet Cobalt"
    )


@dp.message(DriverRegistrationState.car_model)
async def driver_car_model(
    message: Message,
    state: FSMContext,
):
    await state.update_data(
        car_model=message.text.strip()
    )

    await state.set_state(DriverRegistrationState.plate)

    await message.answer(
        "🔢 Avtomobil davlat raqamini kiriting.\n\n"
        "Masalan: 01 A 123 AA"
    )


@dp.message(DriverRegistrationState.plate)
async def driver_plate(
    message: Message,
    state: FSMContext,
):
    plate = message.text.strip().upper()

    # Bir moshina faqat bir marta
    existing = db_execute(
        """
        SELECT *
        FROM drivers
        WHERE UPPER(plate) = ?
        """,
        (plate,),
        fetchone=True,
    )

    if existing:
        await message.answer(
            "⛔ Bu avtomobil raqami allaqachon tizimda ro‘yxatdan o‘tgan."
        )
        await state.clear()
        return

    await state.update_data(plate=plate)

    await state.set_state(
        DriverRegistrationState.license
    )

    await message.answer(
        "🪪 Haydovchilik guvohnomangiz rasmini yuboring."
    )


@dp.message(DriverRegistrationState.license, F.photo)
async def driver_license(
    message: Message,
    state: FSMContext,
):
    photo_id = message.photo[-1].file_id

    await state.update_data(
        license_photo=photo_id
    )

    await state.set_state(
        DriverRegistrationState.tech
    )

    await message.answer(
        "📄 Texpasport rasmini yuboring."
    )


@dp.message(DriverRegistrationState.tech, F.photo)
async def driver_tech(
    message: Message,
    state: FSMContext,
):
    photo_id = message.photo[-1].file_id

    await state.update_data(
        tech_photo=photo_id
    )

    await state.set_state(
        DriverRegistrationState.car_photo
    )

    await message.answer(
        "🚗 Avtomobilingizning rasmini yuboring."
    )


@dp.message(DriverRegistrationState.car_photo, F.photo)
async def driver_car_photo(
    message: Message,
    state: FSMContext,
):
    photo_id = message.photo[-1].file_id

    data = await state.get_data()

    # Telegram ID uniqueness
    existing = get_driver(message.from_user.id)

    if existing:
        await message.answer(
            "Siz allaqachon haydovchi sifatida ro‘yxatdan o‘tgansiz."
        )
        await state.clear()
        return

    db_execute(
        """
        INSERT INTO drivers
        (
            telegram_id,
            full_name,
            phone,
            car_model,
            plate,
            license_photo,
            tech_passport_photo,
            car_photo,
            route,
            approved,
            online
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0)
        """,
        (
            message.from_user.id,
            data.get("name", ""),
            data.get("phone", ""),
            data.get("car_model", ""),
            data.get("plate", ""),
            data.get("license_photo", ""),
            data.get("tech_photo", ""),
            photo_id,
            ROUTE_NAME,
        ),
    )

    await state.clear()

    await message.answer(
        "✅ HAYDOVCHI ARIZASI QABUL QILINDI!\n\n"
        f"🚕 Yo‘nalish: {ROUTE_NAME}\n"
        "⏳ Admin hujjatlarni tekshiradi.\n"
        "Tasdiqlangandan keyin ONLINE bo‘lib buyurtma olishingiz mumkin."
    )

    if ADMIN_ID:
        await bot.send_message(
            ADMIN_ID,
            "🚗 YANGI HAYDOVCHI ARIZASI\n\n"
            f"👤 {data.get('name')}\n"
            f"📱 {data.get('phone')}\n"
            f"🚗 {data.get('car_model')}\n"
            f"🔢 {data.get('plate')}\n"
            f"🆔 Telegram: {message.from_user.id}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ TASDIQLASH",
                            callback_data=f"approve_driver:{message.from_user.id}",
                        ),
                        InlineKeyboardButton(
                            text="❌ RAD ETISH",
                            callback_data=f"reject_driver:{message.from_user.id}",
                        ),
                    ]
                ]
            ),
        )

        # Hujjatlarni admin ko'rishi uchun
        try:
            await bot.send_photo(
                ADMIN_ID,
                data.get("license_photo"),
                caption="🪪 Haydovchilik guvohnomasi",
            )

            await bot.send_photo(
                ADMIN_ID,
                data.get("tech_photo"),
                caption="📄 Texpasport",
            )

            await bot.send_photo(
                ADMIN_ID,
                photo_id,
                caption="🚗 Avtomobil rasmi",
            )
        except Exception as e:
            logger.warning(
                "Admin hujjat yuborish xatosi: %s",
                e,
            )


# ============================================================
# ADMIN DRIVER APPROVAL
# ============================================================

@dp.callback_query(F.data.startswith("approve_driver:"))
async def approve_driver(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "⛔ Siz admin emassiz.",
            show_alert=True,
        )
        return

    telegram_id = int(
        callback.data.split(":")[1]
    )

    db_execute(
        """
        UPDATE drivers
        SET approved = 1
        WHERE telegram_id = ?
        """,
        (telegram_id,),
    )

    await bot.send_message(
        telegram_id,
        "🎉 TABRIKLAYMIZ!\n\n"
        "✅ Sizning haydovchi arizangiz tasdiqlandi.\n"
        f"🚕 Yo‘nalish: {ROUTE_NAME}\n\n"
        "Endi HAYDOVCHI panelidan ONLINE bo‘lib buyurtma olishingiz mumkin.",
        reply_markup=driver_keyboard(False),
    )

    await callback.message.edit_text(
        "✅ Haydovchi tasdiqlandi."
    )

    await callback.answer()


@dp.callback_query(F.data.startswith("reject_driver:"))
async def reject_driver(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer(
            "⛔ Siz admin emassiz.",
            show_alert=True,
        )
        return

    telegram_id = int(
        callback.data.split(":")[1]
    )

    db_execute(
        """
        DELETE FROM drivers
        WHERE telegram_id = ?
        """,
        (telegram_id,),
    )

    await bot.send_message(
        telegram_id,
        "❌ Haydovchi arizangiz rad etildi."
    )

    await callback.message.edit_text(
        "❌ Haydovchi arizasi rad etildi."
    )

    await callback.answer()


# ============================================================
# DRIVER ONLINE / OFFLINE
# ============================================================

@dp.message(F.text == "🟢 ONLINE")
async def driver_online(message: Message):
    driver = get_driver(message.from_user.id)

    if not driver or not driver["approved"]:
        await message.answer(
            "⛔ Siz hali tasdiqlangan haydovchi emassiz."
        )
        return

    db_execute(
        """
        UPDATE drivers
        SET online = 1
        WHERE telegram_id = ?
        """,
        (message.from_user.id,),
    )

    await message.answer(
        "🟢 ONLINE rejim yoqildi.\n\n"
        "🚕 Endi sizga yangi buyurtmalar yuboriladi.",
        reply_markup=driver_keyboard(True),
    )


@dp.message(F.text == "⚪ OFFLINE")
async def driver_offline(message: Message):
    driver = get_driver(message.from_user.id)

    if not driver:
        await message.answer(
            "Siz haydovchi sifatida ro‘yxatdan o‘tmagansiz."
        )
        return

    db_execute(
        """
        UPDATE drivers
        SET online = 0
        WHERE telegram_id = ?
        """,
        (message.from_user.id,),
    )

    await message.answer(
        "⚪ OFFLINE rejim yoqildi.\n\n"
        "Yangi buyurtmalar kelmaydi.",
        reply_markup=driver_keyboard(False),
    )


# ============================================================
# DRIVER ACTIVE ORDERS
# ============================================================

@dp.message(F.text == "🚕 FAOL BUYURTMALAR")
async def active_orders(message: Message):
    driver = get_driver(message.from_user.id)

    if not driver:
        await message.answer("Haydovchi topilmadi.")
        return

    orders = db_execute(
        """
        SELECT *
        FROM orders
        WHERE driver_id = ?
        AND status IN ('accepted', 'on_way', 'picked')
        ORDER BY id DESC
        """,
        (driver["id"],),
        fetchall=True,
    )

    if not orders:
        await message.answer(
            "🚕 Hozircha faol buyurtmalar yo‘q."
        )
        return

    for order in orders:
        await message.answer(
            f"🚕 FAOL BUYURTMA #{order['id']}\n\n"
            f"📍 {order['origin']}\n"
            f"🏁 {order['destination']}\n"
            f"💰 {order['price']:,} so‘m\n"
            f"📌 Status: {order['status']}"
        )


# ============================================================
# DRIVER HISTORY
# ============================================================

@dp.message(F.text == "📜 BUYURTMALAR TARIXI")
async def driver_history(message: Message):
    driver = get_driver(message.from_user.id)

    if not driver:
        await message.answer("Haydovchi topilmadi.")
        return

    orders = db_execute(
        """
        SELECT *
        FROM orders
        WHERE driver_id = ?
        ORDER BY id DESC
        LIMIT 20
        """,
        (driver["id"],),
        fetchall=True,
    )

    if not orders:
        await message.answer(
            "📜 Hali buyurtmalar tarixi yo‘q."
        )
        return

    lines = ["📜 BUYURTMALAR TARIXI\n"]

    for order in orders:
        lines.append(
            f"#{order['id']} | "
            f"{order['origin']} → {order['destination']} | "
            f"{order['price']:,} so‘m | "
            f"{order['status']}"
        )

    await message.answer("\n".join(lines))


# ============================================================
# DRIVER INCOME
# ============================================================

@dp.message(F.text == "💰 DAROMAD")
async def driver_income(message: Message):
    driver = get_driver(message.from_user.id)

    if not driver:
        await message.answer("Haydovchi topilmadi.")
        return

    row = db_execute(
        """
        SELECT
            COUNT(*) AS count,
            COALESCE(SUM(price), 0) AS total
        FROM orders
        WHERE driver_id = ?
        AND status = 'completed'
        """,
        (driver["id"],),
        fetchone=True,
    )

    await message.answer(
        "💰 DAROMAD\n\n"
        f"🚕 Yakunlangan buyurtmalar: {row['count']}\n"
        f"💵 Jami: {row['total']:,} so‘m"
    )


# ============================================================
# DRIVER RATING
# ============================================================

@dp.message(F.text == "⭐ REYTING")
async def driver_rating(message: Message):
    driver = get_driver(message.from_user.id)

    if not driver:
        await message.answer("Haydovchi topilmadi.")
        return

    await message.answer(
        "⭐ SIZNING REYTINGINGIZ\n\n"
        f"⭐ {driver['rating']:.2f} / 5\n"
        f"👥 Baholar soni: {driver['rating_count']}"
    )


# ============================================================
# NEW ORDERS
# ============================================================

@dp.message(F.text == "📋 YANGI BUYURTMALAR")
async def new_orders(message: Message):
    driver = get_driver(message.from_user.id)

    if not driver:
        await message.answer("Haydovchi topilmadi.")
        return

    if not driver["approved"]:
        await message.answer(
            "⏳ Admin tasdig‘i kerak."
        )
        return

    if not driver["online"]:
        await message.answer(
            "⚪ Siz OFFLINE rejimdasiz.\n"
            "Avval 🟢 ONLINE bo‘ling."
        )
        return

    orders = db_execute(
        """
        SELECT o.*
        FROM orders o
        WHERE o.status = 'searching'
        ORDER BY o.id DESC
        LIMIT 10
        """,
        fetchall=True,
    )

    if not orders:
        await message.answer(
            "📭 Hozircha yangi buyurtmalar yo‘q."
        )
        return

    for order in orders:
        if active_driver_orders(driver["id"]) >= MAX_ACTIVE_ORDERS:
            break

        await message.answer(
            f"🚕 YANGI BUYURTMA #{order['id']}\n\n"
            f"📍 {order['origin']}\n"
            f"🏁 {order['destination']}\n"
            f"💰 {order['price']:,} so‘m",
            reply_markup=claim_keyboard(order["id"]),
        )


# ============================================================
# PROFILE
# ============================================================

@dp.message(F.text == "👤 PROFIL")
async def profile(message: Message):
    driver = get_driver(message.from_user.id)

    if driver:
        await message.answer(
            "🚗 HAYDOVCHI PROFILI\n\n"
            f"👤 {driver['full_name']}\n"
            f"📱 {driver['phone']}\n"
            f"🚗 {driver['car_model']}\n"
            f"🔢 {driver['plate']}\n"
            f"📍 {driver['route']}\n"
            f"⭐ {driver['rating']:.2f}\n"
            f"🟢 Online: {'HA' if driver['online'] else 'YO‘Q'}"
        )
        return

    user = get_user(message.from_user.id)

    if not user:
        await message.answer(
            "Siz hali ro‘yxatdan o‘tmagansiz."
        )
        return

    await message.answer(
        "👤 PROFIL\n\n"
        f"👤 {user['full_name']}\n"
        f"📱 {user['phone']}"
    )


# ============================================================
# CUSTOMER ORDER HISTORY
# ============================================================

@dp.message(F.text == "📜 BUYURTMALARIM")
async def customer_history(message: Message):
    orders = db_execute(
        """
        SELECT *
        FROM orders
        WHERE customer_id = ?
        ORDER BY id DESC
        LIMIT 20
        """,
        (message.from_user.id,),
        fetchall=True,
    )

    if not orders:
        await message.answer(
            "📜 Hali buyurtmalar tarixi yo‘q."
        )
        return

    lines = ["📜 BUYURTMALARIM\n"]

    for order in orders:
        lines.append(
            f"#{order['id']} | "
            f"{order['origin']} → {order['destination']} | "
            f"{order['price']:,} so‘m | "
            f"{order['status']}"
        )

    await message.answer("\n".join(lines))


# ============================================================
# SUPPORT
# ============================================================

@dp.message(F.text == "📩 MUROJAAT")
async def support_start(
    message: Message,
    state: FSMContext,
):
    await state.set_state(SupportState.message)

    await message.answer(
        "📩 TAKLIF VA MUROJAATLAR\n\n"
        "Muammo, taklif yoki shikoyatingizni yozing."
    )


@dp.message(SupportState.message)
async def support_message(
    message: Message,
    state: FSMContext,
):
    text = message.text.strip() if message.text else ""

    if not text:
        await message.answer(
            "❗ Murojaat matnini yozing."
        )
        return

    db_execute(
        """
        INSERT INTO support_tickets
        (telegram_id, message)
        VALUES (?, ?)
        """,
        (
            message.from_user.id,
            text,
        ),
    )

    ticket = db_execute(
        """
        SELECT id
        FROM support_tickets
        WHERE telegram_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (message.from_user.id,),
        fetchone=True,
    )

    ticket_id = ticket["id"]

    await state.clear()

    await message.answer(
        "✅ Murojaatingiz qabul qilindi.\n\n"
        f"🎫 Murojaat raqami: MR-{ticket_id:06d}\n\n"
        "Operator ko‘rib chiqadi.",
        reply_markup=main_keyboard(),
    )

    if ADMIN_ID:
        await bot.send_message(
            ADMIN_ID,
            "📩 YANGI MUROJAAT\n\n"
            f"🎫 MR-{ticket_id:06d}\n"
            f"👤 Telegram ID: {message.from_user.id}\n\n"
            f"{text}"
        )


# ============================================================
# ADMIN COMMANDS
# ============================================================

@dp.message(Command("admin"))
async def admin_command(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Siz admin emassiz.")
        return

    users_count = db_execute(
        "SELECT COUNT(*) AS c FROM users",
        fetchone=True,
    )["c"]

    drivers_count = db_execute(
        "SELECT COUNT(*) AS c FROM drivers",
        fetchone=True,
    )["c"]

    approved_drivers = db_execute(
        """
        SELECT COUNT(*) AS c
        FROM drivers
        WHERE approved = 1
        """,
        fetchone=True,
    )["c"]

    online_drivers = db_execute(
        """
        SELECT COUNT(*) AS c
        FROM drivers
        WHERE approved = 1
        AND online = 1
        """,
        fetchone=True,
    )["c"]

    orders_count = db_execute(
        "SELECT COUNT(*) AS c FROM orders",
        fetchone=True,
    )["c"]

    active_orders = db_execute(
        """
        SELECT COUNT(*) AS c
        FROM orders
        WHERE status IN
        ('searching','accepted','on_way','picked')
        """,
        fetchone=True,
    )["c"]

    await message.answer(
        "👑 ADMIN PANEL\n\n"
        f"👥 Foydalanuvchilar: {users_count}\n"
        f"🚗 Haydovchilar: {drivers_count}\n"
        f"✅ Tasdiqlangan: {approved_drivers}\n"
        f"🟢 Online: {online_drivers}\n"
        f"🚕 Jami buyurtmalar: {orders_count}\n"
        f"🔥 Faol buyurtmalar: {active_orders}"
    )


# ============================================================
# ADMIN / BLOCK USER
# ============================================================

@dp.message(Command("block"))
async def admin_block(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split()

    if len(parts) != 2:
        await message.answer(
            "Format:\n/block TELEGRAM_ID"
        )
        return

    try:
        telegram_id = int(parts[1])
    except ValueError:
        await message.answer("Telegram ID noto‘g‘ri.")
        return

    db_execute(
        """
        UPDATE users
        SET blocked = 1
        WHERE telegram_id = ?
        """,
        (telegram_id,),
    )

    await message.answer(
        f"⛔ {telegram_id} bloklandi."
    )


@dp.message(Command("unblock"))
async def admin_unblock(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    parts = message.text.split()

    if len(parts) != 2:
        await message.answer(
            "Format:\n/unblock TELEGRAM_ID"
        )
        return

    try:
        telegram_id = int(parts[1])
    except ValueError:
        await message.answer("Telegram ID noto‘g‘ri.")
        return

    db_execute(
        """
        UPDATE users
        SET blocked = 0
        WHERE telegram_id = ?
        """,
        (telegram_id,),
    )

    await message.answer(
        f"✅ {telegram_id} blokdan chiqarildi."
    )


# ============================================================
# ADMIN PENDING DRIVERS
# ============================================================

@dp.message(Command("drivers"))
async def admin_drivers(message: Message):
    if message.from_user.id != ADMIN_ID:
        return

    drivers = db_execute(
        """
        SELECT *
        FROM drivers
        ORDER BY id DESC
        LIMIT 30
        """,
        fetchall=True,
    )

    if not drivers:
        await message.answer("Haydovchilar yo‘q.")
        return

    for driver in drivers:
        status = (
            "✅ TASDIQLANGAN"
            if driver["approved"]
            else "⏳ KUTILMOQDA"
        )

        await message.answer(
            f"🚗 DRIVER #{driver['id']}\n\n"
            f"👤 {driver['full_name']}\n"
            f"📱 {driver['phone']}\n"
            f"🚗 {driver['car_model']}\n"
            f"🔢 {driver['plate']}\n"
            f"📍 {driver['route']}\n"
            f"📌 {status}"
        )


# ============================================================
# FALLBACK
# ============================================================

@dp.message()
async def fallback(message: Message):
    if is_blocked(message.from_user.id):
        await message.answer(
            "⛔ Sizning akkauntingiz bloklangan."
        )
        return

    await message.answer(
        "🚕 TAXI BOR MI? — ALBATTA BOR!\n\n"
        "Kerakli bo‘limni tanlang.",
        reply_markup=main_keyboard(),
    )


# ============================================================
# STARTUP
# ============================================================

async def main():
    logger.info("TAXI BOR MI? bot ishga tushmoqda...")

    me = await bot.get_me()

    logger.info(
        "Bot: @%s",
        me.username,
    )

    logger.info(
        "Route: %s",
        ROUTE_NAME,
    )

    logger.info(
        "AI: %s",
        "ON" if openai_client else "FALLBACK",
    )

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot to'xtatildi.")
