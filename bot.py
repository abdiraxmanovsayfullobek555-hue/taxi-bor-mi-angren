import os
import re
import sqlite3
import asyncio
import logging
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardRemove,
)

# ============================================================
# TAXI BOR MI? — ALBATTA BOR!
# OBLIQ ↔ ANGREN
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
DB_PATH = os.getenv("DB_PATH", "taxi_bor_mi.db")

MAX_ACTIVE = 4
NO_ANSWER_SECONDS = 60
SEARCH_SECONDS = 120

MIN_PRICE = 5000
MAX_PRICE = 1_000_000

ROUTE_NAME = "OBLIQ ↔ ANGREN"

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN Railway Variables ichida topilmadi."
    )

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("taxi_bor_mi")

bot = Bot(
    BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    )
)

dp = Dispatcher(storage=MemoryStorage())

db_lock = asyncio.Lock()


# ============================================================
# DATABASE
# ============================================================

def conn():
    c = sqlite3.connect(
        DB_PATH,
        check_same_thread=False
    )
    c.row_factory = sqlite3.Row
    return c


def init_db():
    c = conn()

    c.executescript("""
    PRAGMA journal_mode=WAL;

    CREATE TABLE IF NOT EXISTS users(
        tg_id INTEGER PRIMARY KEY,
        lang TEXT NOT NULL DEFAULT 'uz',
        name TEXT DEFAULT '',
        phone TEXT DEFAULT '',
        blocked INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS drivers(
        tg_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        phone TEXT NOT NULL,
        car_model TEXT NOT NULL,
        plate TEXT NOT NULL UNIQUE,

        license_id TEXT DEFAULT '',
        tech_id TEXT DEFAULT '',
        car_photo_id TEXT DEFAULT '',

        approved INTEGER NOT NULL DEFAULT 0,
        online INTEGER NOT NULL DEFAULT 0,

        active_orders INTEGER NOT NULL DEFAULT 0,

        rating REAL NOT NULL DEFAULT 5.0,
        rating_count INTEGER NOT NULL DEFAULT 0,

        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        customer_id INTEGER NOT NULL,
        driver_id INTEGER,

        service TEXT NOT NULL,
        passengers INTEGER DEFAULT 1,

        route_text TEXT NOT NULL,

        lat REAL,
        lon REAL,

        price INTEGER NOT NULL,

        status TEXT NOT NULL DEFAULT 'SEARCHING',

        created_at TEXT NOT NULL,
        accepted_at TEXT,
        finished_at TEXT,

        no_answer_driver INTEGER,
        no_answer_started TEXT
    );

    CREATE TABLE IF NOT EXISTS declined(
        order_id INTEGER NOT NULL,
        driver_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,

        PRIMARY KEY(order_id, driver_id)
    );

    CREATE TABLE IF NOT EXISTS ratings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER UNIQUE NOT NULL,
        score INTEGER NOT NULL,
        comment TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER,
        actor INTEGER,
        event TEXT NOT NULL,
        details TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_orders_status
        ON orders(status);

    CREATE INDEX IF NOT EXISTS idx_orders_customer
        ON orders(customer_id);

    CREATE INDEX IF NOT EXISTS idx_orders_driver
        ON orders(driver_id);
    """)

    # Eski bazalar uchun migration
    cols = {
        r[1]
        for r in c.execute(
            "PRAGMA table_info(orders)"
        ).fetchall()
    }

    if "route_text" not in cols:
        c.execute(
            "ALTER TABLE orders "
            "ADD COLUMN route_text TEXT NOT NULL DEFAULT ''"
        )

        if "origin" in cols and "destination" in cols:
            c.execute("""
                UPDATE orders
                SET route_text =
                    TRIM(
                        COALESCE(origin, '') ||
                        ' → ' ||
                        COALESCE(destination, '')
                    )
                WHERE route_text = ''
            """)

    c.commit()
    c.close()


def ts():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


async def q(
    sql,
    args=(),
    one=False,
    many=False,
    commit=True
):
    async with db_lock:
        c = conn()

        cur = c.execute(sql, args)

        if one:
            result = cur.fetchone()
        elif many:
            result = cur.fetchall()
        else:
            result = cur.lastrowid

        if commit:
            c.commit()

        c.close()

        return result


async def event(
    order_id,
    actor,
    name,
    details=""
):
    await q(
        """
        INSERT INTO events(
            order_id,
            actor,
            event,
            details,
            created_at
        )
        VALUES(?,?,?,?,?)
        """,
        (
            order_id,
            actor,
            name,
            details,
            ts()
        )
    )


# ============================================================
# KEYBOARDS
# ============================================================

def lang_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="🇺🇿 O‘zbekcha"
                ),
                KeyboardButton(
                    text="🇺🇿 Ўзбекча"
                )
            ],
            [
                KeyboardButton(
                    text="🇷🇺 Русский"
                ),
                KeyboardButton(
                    text="🇬🇧 English"
                )
            ]
        ],
        resize_keyboard=True
    )


def phone_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📱 Telefon raqamimni yuborish",
                    request_contact=True
                )
            ]
        ],
        resize_keyboard=True
    )


def main_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="🚕 Buyurtma berish"
                )
            ],
            [
                KeyboardButton(
                    text="🚗 Haydovchi bo‘lish"
                ),
                KeyboardButton(
                    text="👤 Profil"
                )
            ],
            [
                KeyboardButton(
                    text="📋 Buyurtmalarim"
                ),
                KeyboardButton(
                    text="🆘 Yordam"
                )
            ]
        ],
        resize_keyboard=True
    )


# Aynan kelishganimizdek:
# 1,2,3,4 kishi + DASTAVKA
def service_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="1️⃣ 1 kishi"
                ),
                KeyboardButton(
                    text="2️⃣ 2 kishi"
                )
            ],
            [
                KeyboardButton(
                    text="3️⃣ 3 kishi"
                ),
                KeyboardButton(
                    text="4️⃣ 4 kishi"
                )
            ],
            [
                KeyboardButton(
                    text="📦 DASTAVKA"
                )
            ],
            [
                KeyboardButton(
                    text="🏠 Asosiy menyu"
                )
            ]
        ],
        resize_keyboard=True
    )


def gps_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📍 Joylashuvimni yuborish",
                    request_location=True
                )
            ],
            [
                KeyboardButton(
                    text="🏠 Asosiy menyu"
                )
            ]
        ],
        resize_keyboard=True
    )


def price_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="5 000 so‘m"
                ),
                KeyboardButton(
                    text="10 000 so‘m"
                )
            ],
            [
                KeyboardButton(
                    text="15 000 so‘m"
                ),
                KeyboardButton(
                    text="20 000 so‘m"
                )
            ],
            [
                KeyboardButton(
                    text="✍️ Boshqa narx"
                )
            ],
            [
                KeyboardButton(
                    text="🏠 Asosiy menyu"
                )
            ]
        ],
        resize_keyboard=True
    )


def driver_kb(
    online=False,
    active=0
):
    status = (
        "🟢 ONLINE"
        if online
        else
        "⚪ OFFLINE"
    )

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=status
                )
            ],
            [
                KeyboardButton(
                    text=f"📦 Buyurtmalar ({active}/{MAX_ACTIVE})"
                )
            ],
            [
                KeyboardButton(
                    text="💰 Daromad"
                ),
                KeyboardButton(
                    text="⭐ Reyting"
                )
            ],
            [
                KeyboardButton(
                    text="👤 Profil"
                ),
                KeyboardButton(
                    text="🏠 Asosiy menyu"
                )
            ]
        ],
        resize_keyboard=True
    )


def claim_kb(order_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ BUYURTMANI OLISH",
                    callback_data=f"claim:{order_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ RAD ETISH",
                    callback_data=f"decline:{order_id}"
                )
            ]
        ]
    )


def no_answer_kb(order_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📵 MIJOZ JAVOB BERMADI",
                    callback_data=f"noans:{order_id}"
                )
            ]
        ]
    )


def customer_need_kb(order_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ HA, KERAK",
                    callback_data=f"needyes:{order_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ YO‘Q, KERAK EMAS",
                    callback_data=f"needno:{order_id}"
                )
            ]
        ]
    )


def admin_driver_kb(tg_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ TASDIQLASH",
                    callback_data=f"approve:{tg_id}"
                ),
                InlineKeyboardButton(
                    text="❌ RAD ETISH",
                    callback_data=f"reject:{tg_id}"
                )
            ]
        ]
    )


# ============================================================
# FSM
# ============================================================

class Reg(StatesGroup):
    lang = State()
    name = State()
    phone = State()


class Order(StatesGroup):
    passengers = State()
    route = State()
    gps = State()
    price = State()
    custom_price = State()


class Driver(StatesGroup):
    name = State()
    phone = State()
    model = State()
    plate = State()
    license = State()
    tech = State()
    car = State()
    rules = State()


class Support(StatesGroup):
    text = State()


# ============================================================
# HELPERS
# ============================================================

def clean_phone(value):
    digits = re.sub(
        r"\D",
        "",
        value or ""
    )

    return (
        "+" + digits
        if digits
        else ""
    )


def valid_phone(value):
    return len(
        re.sub(
            r"\D",
            "",
            value or ""
        )
    ) >= 7


def parse_price(text):
    s = (text or "").lower()

    s = s.replace(
        "so‘m",
        ""
    )

    s = s.replace(
        "so'm",
        ""
    )

    s = s.replace(
        "sum",
        ""
    )

    s = s.replace(
        "ming",
        "000"
    )

    s = s.replace(
        " ",
        ""
    )

    s = s.replace(
        ".",
        ""
    )

    s = s.replace(
        ",",
        ""
    )

    if not s.isdigit():
        return None

    n = int(s)

    if MIN_PRICE <= n <= MAX_PRICE:
        return n

    return None


async def get_user(uid):
    return await q(
        """
        SELECT *
        FROM users
        WHERE tg_id=?
        """,
        (uid,),
        one=True,
        commit=False
    )


async def get_driver(uid):
    return await q(
        """
        SELECT *
        FROM drivers
        WHERE tg_id=?
        """,
        (uid,),
        one=True,
        commit=False
    )


async def show_home(
    message,
    state=None
):
    if state:
        await state.clear()

    d = await get_driver(
        message.from_user.id
    )

    if d and d["approved"]:
        await message.answer(
            "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
            "🚗 Haydovchi paneli:",
            reply_markup=driver_kb(
                bool(d["online"]),
                d["active_orders"]
            )
        )

    else:
        await message.answer(
            "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
            "📍 OBLIQ ↔ ANGREN\n"
            "👤 Yo‘lovchi • 📦 Dastavka",
            reply_markup=main_kb()
        )


# ============================================================
# START
# ============================================================

@dp.message(CommandStart())
async def start(
    message: Message,
    state: FSMContext
):
    uid = message.from_user.id

    u = await get_user(uid)

    if u and u["blocked"]:
        await message.answer(
            "⛔ Akkauntingiz bloklangan."
        )
        return

    # Avval ro'yxatdan o'tgan bo'lsa,
    # tilni qayta so'ramaymiz.
    if (
        u
        and u["name"]
        and u["phone"]
    ):
        await show_home(
            message,
            state
        )
        return

    await state.clear()

    await state.set_state(
        Reg.lang
    )

    await message.answer(
        "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
        "🤖 Aqlli taxi va dastavka botiga xush kelibsiz.\n\n"
        "🌐 Tilni tanlang:",
        reply_markup=lang_kb()
    )


# ============================================================
# REGISTRATION
# ============================================================

@dp.message(Reg.lang)
async def reg_lang(
    message: Message,
    state: FSMContext
):
    text = message.text or ""

    if "Русский" in text:
        lang = "ru"

    elif "English" in text:
        lang = "en"

    elif "Ўзбекча" in text:
        lang = "uz_cyr"

    else:
        lang = "uz"

    await state.update_data(
        lang=lang
    )

    await state.set_state(
        Reg.name
    )

    await message.answer(
        "👤 Ismingiz / F.I.Sh. ni kiriting:"
    )


@dp.message(Reg.name)
async def reg_name(
    message: Message,
    state: FSMContext
):
    name = (
        message.text or ""
    ).strip()

    if len(name) < 2:
        await message.answer(
            "⚠️ Ism juda qisqa. Qayta kiriting."
        )
        return

    await state.update_data(
        name=name
    )

    await state.set_state(
        Reg.phone
    )

    await message.answer(
        "📱 Telefon raqamingizni yuboring:",
        reply_markup=phone_kb()
    )


@dp.message(Reg.phone)
async def reg_phone(
    message: Message,
    state: FSMContext
):
    phone = (
        message.contact.phone_number
        if message.contact
        else message.text
    )

    if not valid_phone(phone):
        await message.answer(
            "⚠️ Telefon raqami noto‘g‘ri.\n"
            "Tugma orqali yuboring."
        )
        return

    data = await state.get_data()

    uid = message.from_user.id

    await q(
        """
        INSERT INTO users(
            tg_id,
            lang,
            name,
            phone,
            created_at
        )
        VALUES(?,?,?,?,?)

        ON CONFLICT(tg_id)
        DO UPDATE SET
            lang=excluded.lang,
            name=excluded.name,
            phone=excluded.phone
        """,
        (
            uid,
            data["lang"],
            data["name"],
            clean_phone(phone),
            ts()
        )
    )

    await state.clear()

    await message.answer(
        "✅ Ro‘yxatdan o‘tish tugadi.\n\n"
        "🚕 Endi buyurtma berishingiz mumkin.",
        reply_markup=main_kb()
    )


# ============================================================
# CUSTOMER ORDER START
# ============================================================

@dp.message(
    F.text == "🚕 Buyurtma berish"
)
async def order_start(
    message: Message,
    state: FSMContext
):
    u = await get_user(
        message.from_user.id
    )

    if (
        not u
        or not u["name"]
        or not u["phone"]
    ):
        await start(
            message,
            state
        )
        return

    await state.clear()

    await state.set_state(
        Order.passengers
    )

    await message.answer(
        "👥 <b>Necha kishi?</b>\n\n"
        "Dastavka bo‘lsa, "
        "📦 DASTAVKA ni tanlang.",
        reply_markup=service_kb()
    )


# ============================================================
# PASSENGERS / DELIVERY
# ============================================================

@dp.message(Order.passengers)
async def order_passengers(
    message: Message,
    state: FSMContext
):
    text = (
        message.text or ""
    ).strip()

    mapping = {
        "1️⃣ 1 kishi": 1,
        "2️⃣ 2 kishi": 2,
        "3️⃣ 3 kishi": 3,
        "4️⃣ 4 kishi": 4
    }

    if text == "📦 DASTAVKA":
        await state.update_data(
            service="delivery",
            passengers=0
        )

    elif text in mapping:
        await state.update_data(
            service="passenger",
            passengers=mapping[text]
        )

    else:
        await message.answer(
            "Quyidagi tugmalardan birini tanlang.",
            reply_markup=service_kb()
        )
        return

    await state.set_state(
        Order.route
    )

    await message.answer(
        "📝 <b>Yo‘nalishingizni yozing.</b>\n\n"
        "Istagan shaklda yozishingiz mumkin.\n\n"
        "Masalan:\n"
        "<code>Obliqdan Angren hokimiyatiga boraman</code>\n\n"
        "<code>Obliq → Angren</code>\n\n"
        "<code>Men Obliqdan chiqaman, "
        "Angren markaziga borishim kerak</code>"
    )


# ============================================================
# FREE ROUTE TEXT
# ============================================================

@dp.message(Order.route)
async def order_route(
    message: Message,
    state: FSMContext
):
    text = (
        message.text or ""
    ).strip()

    # MUHIM:
    # Route parser YO'Q.
    # Mijoz nima yozsa, o'sha saqlanadi.
    if len(text) < 2:
        await message.answer(
            "📝 Yo‘nalishni matn ko‘rinishida yozing."
        )
        return

    await state.update_data(
        route_text=text
    )

    await state.set_state(
        Order.gps
    )

    await message.answer(
        "📍 Endi <b>olib ketish joyingizning "
        "GPS lokatsiyasini</b> yuboring.",
        reply_markup=gps_kb()
    )


# ============================================================
# GPS
# ============================================================

@dp.message(
    Order.gps,
    F.location
)
async def order_gps(
    message: Message,
    state: FSMContext
):
    await state.update_data(
        lat=message.location.latitude,
        lon=message.location.longitude
    )

    await state.set_state(
        Order.price
    )

    await message.answer(
        "💰 <b>Narxni tanlang:</b>",
        reply_markup=price_kb()
    )


@dp.message(Order.gps)
async def order_gps_wrong(
    message: Message,
    state: FSMContext
):
    if message.text == "🏠 Asosiy menyu":
        await show_home(
            message,
            state
        )
        return

    await message.answer(
        "📍 Iltimos, Telegramdagi "
        "<b>Joylashuvimni yuborish</b> "
        "tugmasi orqali GPS yuboring.",
        reply_markup=gps_kb()
    )


# ============================================================
# PRICE
# ============================================================

@dp.message(Order.price)
async def order_price(
    message: Message,
    state: FSMContext
):
    if message.text == "🏠 Asosiy menyu":
        await show_home(
            message,
            state
        )
        return

    if message.text == "✍️ Boshqa narx":

        await state.set_state(
            Order.custom_price
        )

        await message.answer(
            "✍️ <b>O‘zingiz xohlagan narxni yozing.</b>\n\n"
            "Masalan: <code>25000</code>\n\n"
            f"Minimal narx: {MIN_PRICE:,} so‘m"
        )

        return

    price = parse_price(
        message.text
    )

    if price is None:
        await message.answer(
            "❌ Noto‘g‘ri narx.\n\n"
            "Tugmalardan tanlang yoki "
            "<b>Boshqa narx</b>ni bosing.",
            reply_markup=price_kb()
        )
        return

    await create_order(
        message,
        state,
        price
    )


# ============================================================
# CUSTOM PRICE
# ============================================================

@dp.message(Order.custom_price)
async def order_custom_price(
    message: Message,
    state: FSMContext
):
    if message.text == "🏠 Asosiy menyu":
        await show_home(
            message,
            state
        )
        return

    price = parse_price(
        message.text
    )

    if price is None:
        await message.answer(
            "❌ Narx noto‘g‘ri.\n\n"
            f"Narx {MIN_PRICE:,} dan "
            f"{MAX_PRICE:,} so‘mgacha bo‘lishi kerak.\n\n"
            "Masalan: <code>25000</code>"
        )
        return

    await create_order(
        message,
        state,
        price
    )


# ============================================================
# CREATE ORDER
# ============================================================

async def create_order(
    message: Message,
    state: FSMContext,
    price: int
):
    data = await state.get_data()

    uid = message.from_user.id

    active = await q(
        """
        SELECT COUNT(*) AS n
        FROM orders
        WHERE customer_id=?
        AND status IN (
            'SEARCHING',
            'ACCEPTED',
            'NO_ANSWER_WAIT'
        )
        """,
        (uid,),
        one=True,
        commit=False
    )

    if active["n"] >= 1:
        await state.clear()

        await message.answer(
            "⚠️ Sizda tugallanmagan buyurtma bor.",
            reply_markup=main_kb()
        )
        return

    oid = await q(
        """
        INSERT INTO orders(
            customer_id,
            service,
            passengers,
            route_text,
            lat,
            lon,
            price,
            status,
            created_at
        )
        VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            uid,
            data["service"],
            data["passengers"],
            data["route_text"],
            data["lat"],
            data["lon"],
            price,
            "SEARCHING",
            ts()
        )
    )

    await event(
        oid,
        uid,
        "CREATED",
        data["route_text"]
    )

    await state.clear()

    service_text = (
        "📦 DASTAVKA"
        if data["service"] == "delivery"
        else
        f"👤 {data['passengers']} kishi"
    )

    await message.answer(
        f"✅ <b>Buyurtma #{oid} yaratildi.</b>\n\n"
        f"{service_text}\n"
        f"📝 {data['route_text']}\n"
        f"💰 {price:,} so‘m\n\n"
        "🔎 Haydovchi qidirilmoqda...",
        reply_markup=main_kb()
    )

    asyncio.create_task(
        dispatch(oid)
    )


# ============================================================
# DRIVER REGISTRATION
# ============================================================

@dp.message(
    F.text == "🚗 Haydovchi bo‘lish"
)
async def driver_start(
    message: Message,
    state: FSMContext
):
    uid = message.from_user.id

    d = await get_driver(uid)

    if d and d["approved"]:
        await message.answer(
            "🚗 Siz haydovchi sifatida "
            "allaqachon tasdiqlangansiz.",
            reply_markup=driver_kb(
                bool(d["online"]),
                d["active_orders"]
            )
        )
        return

    await state.clear()

    await state.set_state(
        Driver.name
    )

    await message.answer(
        "🚗 <b>Haydovchi ro‘yxatdan o‘tishi</b>\n\n"
        "F.I.Sh. ni kiriting:",
        reply_markup=ReplyKeyboardRemove()
    )


@dp.message(Driver.name)
async def dr_name(
    message: Message,
    state: FSMContext
):
    value = (
        message.text or ""
    ).strip()

    if len(value) < 2:
        await message.answer(
            "⚠️ F.I.Sh. noto‘g‘ri."
        )
        return

    await state.update_data(
        name=value
    )

    await state.set_state(
        Driver.phone
    )

    await message.answer(
        "📱 Telefon raqamingiz:",
        reply_markup=phone_kb()
    )


@dp.message(Driver.phone)
async def dr_phone(
    message: Message,
    state: FSMContext
):
    value = (
        message.contact.phone_number
        if message.contact
        else message.text
    )

    if not valid_phone(value):
        await message.answer(
            "⚠️ Telefon raqamini yuboring."
        )
        return

    await state.update_data(
        phone=clean_phone(value)
    )

    await state.set_state(
        Driver.model
    )

    await message.answer(
        "🚘 Mashina modeli:\n"
        "Masalan: Cobalt"
    )


@dp.message(Driver.model)
async def dr_model(
    message: Message,
    state: FSMContext
):
    value = (
        message.text or ""
    ).strip()

    if len(value) < 2:
        await message.answer(
            "⚠️ Mashina modelini kiriting."
        )
        return

    await state.update_data(
        model=value
    )

    await state.set_state(
        Driver.plate
    )

    await message.answer(
        "🔢 Davlat raqami:"
    )


@dp.message(Driver.plate)
async def dr_plate(
    message: Message,
    state: FSMContext
):
    value = (
        message.text or ""
    ).strip().upper()

    if len(value) < 3:
        await message.answer(
            "⚠️ Davlat raqamini "
            "to‘g‘ri kiriting."
        )
        return

    old = await q(
        """
        SELECT tg_id
        FROM drivers
        WHERE plate=?
        AND tg_id!=?
        """,
        (
            value,
            message.from_user.id
        ),
        one=True,
        commit=False
    )

    if old:
        await message.answer(
            "❌ Bu davlat raqami boshqa "
            "haydovchida ro‘yxatdan o‘tgan."
        )
        return

    await state.update_data(
        plate=value
    )

    await state.set_state(
        Driver.license
    )

    await message.answer(
        "🪪 Prava rasmini yuboring:"
    )


@dp.message(
    Driver.license,
    F.photo
)
async def dr_license(
    message: Message,
    state: FSMContext
):
    await state.update_data(
        license=message.photo[-1].file_id
    )

    await state.set_state(
        Driver.tech
    )

    await message.answer(
        "📄 Texpasport rasmini yuboring:"
    )


@dp.message(Driver.license)
async def dr_license_wrong(
    message: Message,
    state: FSMContext
):
    await message.answer(
        "🪪 Prava rasmini "
        "foto ko‘rinishida yuboring."
    )


@dp.message(
    Driver.tech,
    F.photo
)
async def dr_tech(
    message: Message,
    state: FSMContext
):
    await state.update_data(
        tech=message.photo[-1].file_id
    )

    await state.set_state(
        Driver.car
    )

    await message.answer(
        "🚘 Mashinaning rasmini yuboring:"
    )


@dp.message(Driver.tech)
async def dr_tech_wrong(
    message: Message,
    state: FSMContext
):
    await message.answer(
        "📄 Texpasport rasmini "
        "foto ko‘rinishida yuboring."
    )


@dp.message(
    Driver.car,
    F.photo
)
async def dr_car(
    message: Message,
    state: FSMContext
):
    await state.update_data(
        car=message.photo[-1].file_id
    )

    await state.set_state(
        Driver.rules
    )

    rules_kb = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="✅ Qabul qilaman"
                ),
                KeyboardButton(
                    text="❌ Qabul qilmayman"
                )
            ]
        ],
        resize_keyboard=True
    )

    await message.answer(
        "📋 <b>Haydovchi qoidalari</b>\n\n"
        "• Buyurtmani qabul qilgach "
        "o‘zboshimchalik bilan bekor "
        "qilinmaydi.\n"
        "• Mijoz javob bermasa, "
        "tizimdagi 1 daqiqalik jarayon "
        "ishlatiladi.\n"
        "• Mijoz bilan hurmat bilan "
        "muomala qilinadi.\n"
        "• Qoidalarni buzish bloklanishga "
        "olib kelishi mumkin.\n\n"
        "Qoidalarni qabul qilasizmi?",
        reply_markup=rules_kb
    )


@dp.message(Driver.car)
async def dr_car_wrong(
    message: Message,
    state: FSMContext
):
    await message.answer(
        "🚘 Mashinaning rasmini "
        "foto ko‘rinishida yuboring."
    )


@dp.message(Driver.rules)
async def dr_rules(
    message: Message,
    state: FSMContext
):
    if message.text != "✅ Qabul qilaman":
        await state.clear()

        await message.answer(
            "❌ Ro‘yxatdan o‘tish to‘xtatildi.",
            reply_markup=main_kb()
        )
        return

    data = await state.get_data()

    uid = message.from_user.id

    await q(
        """
        INSERT INTO drivers(
            tg_id,
            name,
            phone,
            car_model,
            plate,
            license_id,
            tech_id,
            car_photo_id,
            created_at
        )
        VALUES(?,?,?,?,?,?,?,?,?)

        ON CONFLICT(tg_id)
        DO UPDATE SET
            name=excluded.name,
            phone=excluded.phone,
            car_model=excluded.car_model,
            plate=excluded.plate,
            license_id=excluded.license_id,
            tech_id=excluded.tech_id,
            car_photo_id=excluded.car_photo_id,
            approved=0,
            online=0
        """,
        (
            uid,
            data["name"],
            data["phone"],
            data["model"],
            data["plate"],
            data["license"],
            data["tech"],
            data["car"],
            ts()
        )
    )

    await state.clear()

    await message.answer(
        "✅ <b>Ariza yuborildi.</b>\n\n"
        "Admin hujjatlaringizni tekshiradi.\n"
        "Tasdiqlangandan keyin ONLINE bo‘la olasiz.",
        reply_markup=main_kb()
    )

    if ADMIN_ID:
        await send_driver_application(uid)


async def send_driver_application(uid):
    d = await get_driver(uid)

    if not d:
        return

    text = (
        "🚗 <b>YANGI HAYDOVCHI ARIZASI</b>\n\n"
        f"👤 {d['name']}\n"
        f"📱 {d['phone']}\n"
        f"🚘 {d['car_model']}\n"
        f"🔢 {d['plate']}\n"
        f"🆔 {uid}"
    )

    await bot.send_message(
        ADMIN_ID,
        text,
        reply_markup=admin_driver_kb(uid)
    )

    files = [
        (
            d["license_id"],
            "🪪 PRAVA"
        ),
        (
            d["tech_id"],
            "📄 TEXPASPORT"
        ),
        (
            d["car_photo_id"],
            "🚘 MASHINA"
        )
    ]

    for file_id, caption in files:
        if file_id:
            await bot.send_photo(
                ADMIN_ID,
                file_id,
                caption=caption
            )


# ============================================================
# DRIVER DISPATCH
# ============================================================

async def eligible_drivers(order_id):
    return await q(
        """
        SELECT d.*
        FROM drivers d

        WHERE d.approved=1
        AND d.online=1
        AND d.active_orders < ?

        AND d.tg_id NOT IN (
            SELECT driver_id
            FROM declined
            WHERE order_id=?
        )

        ORDER BY
            d.active_orders ASC,
            d.rating DESC

        LIMIT 20
        """,
        (
            MAX_ACTIVE,
            order_id
        ),
        many=True,
        commit=False
    )


async def dispatch(order_id):
    await asyncio.sleep(0.2)

    o = await q(
        """
        SELECT *
        FROM orders
        WHERE id=?
        """,
        (order_id,),
        one=True,
        commit=False
    )

    if not o:
        return

    if o["status"] != "SEARCHING":
        return

    drivers = await eligible_drivers(
        order_id
    )

    if not drivers:
        await asyncio.sleep(15)

        o = await q(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,),
            one=True,
            commit=False
        )

        if (
            o
            and o["status"] == "SEARCHING"
        ):
            await q(
                """
                UPDATE orders
                SET status='NO_DRIVER'
                WHERE id=?
                """,
                (order_id,)
            )

            await bot.send_message(
                o["customer_id"],
                f"⚠️ <b>Buyurtma #{order_id}</b>\n\n"
                "Hozircha haydovchi topilmadi."
            )

        return

    for d in drivers:
        try:
            if o["service"] == "delivery":
                service_text = "📦 DASTAVKA"
            else:
                service_text = (
                    f"👤 Yo‘lovchi — "
                    f"{o['passengers']} kishi"
                )

            text = (
                f"🚕 <b>YANGI BUYURTMA #{order_id}</b>\n\n"
                f"{service_text}\n\n"
                "📝 <b>MIJOZ YOZGAN:</b>\n"
                f"{o['route_text']}\n\n"
                f"💰 <b>{o['price']:,} so‘m</b>\n\n"
                "📍 GPS lokatsiya yuborildi."
            )

            await bot.send_message(
                d["tg_id"],
                text,
                reply_markup=claim_kb(order_id)
            )

            if (
                o["lat"] is not None
                and o["lon"] is not None
            ):
                await bot.send_location(
                    d["tg_id"],
                    o["lat"],
                    o["lon"]
                )

        except Exception as e:
            log.warning(
                "Offer %s -> %s: %s",
                order_id,
                d["tg_id"],
                e
            )

    asyncio.create_task(
        search_timeout(order_id)
    )


async def search_timeout(order_id):
    await asyncio.sleep(
        SEARCH_SECONDS
    )

    o = await q(
        """
        SELECT *
        FROM orders
        WHERE id=?
        """,
        (order_id,),
        one=True,
        commit=False
    )

    if (
        o
        and o["status"] == "SEARCHING"
    ):
        await q(
            """
            UPDATE orders
            SET status='NO_DRIVER'
            WHERE id=?
            """,
            (order_id,)
        )

        await bot.send_message(
            o["customer_id"],
            f"⚠️ <b>Buyurtma #{order_id}</b>\n\n"
            "Haydovchi topilmadi."
        )


# ============================================================
# DRIVER DECLINE
# ============================================================

@dp.callback_query(
    F.data.startswith("decline:")
)
async def decline(
    cb: CallbackQuery
):
    order_id = int(
        cb.data.split(":")[1]
    )

    uid = cb.from_user.id

    o = await q(
        """
        SELECT *
        FROM orders
        WHERE id=?
        """,
        (order_id,),
        one=True,
        commit=False
    )

    if (
        not o
        or o["status"] != "SEARCHING"
    ):
        await cb.answer(
            "Buyurtma endi mavjud emas.",
            show_alert=True
        )
        return

    await q(
        """
        INSERT OR IGNORE INTO declined(
            order_id,
            driver_id,
            created_at
        )
        VALUES(?,?,?)
        """,
        (
            order_id,
            uid,
            ts()
        )
    )

    await cb.answer(
        "Buyurtma rad etildi."
    )

    try:
        await cb.message.edit_reply_markup(
            reply_markup=None
        )
    except Exception:
        pass


# ============================================================
# DRIVER CLAIM
# ============================================================

@dp.callback_query(
    F.data.startswith("claim:")
)
async def claim(
    cb: CallbackQuery
):
    order_id = int(
        cb.data.split(":")[1]
    )

    uid = cb.from_user.id

    async with db_lock:

        c = conn()

        o = c.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,)
        ).fetchone()

        d = c.execute(
            """
            SELECT *
            FROM drivers
            WHERE tg_id=?
            """,
            (uid,)
        ).fetchone()

        if (
            not o
            or not d
            or o["status"] != "SEARCHING"
            or not d["approved"]
            or not d["online"]
            or d["active_orders"] >= MAX_ACTIVE
        ):
            c.close()

            await cb.answer(
                "Buyurtma allaqachon olingan "
                "yoki sizga mavjud emas.",
                show_alert=True
            )
            return

        c.execute(
            """
            UPDATE orders

            SET
                status='ACCEPTED',
                driver_id=?,
                accepted_at=?

            WHERE id=?
            AND status='SEARCHING'
            """,
            (
                uid,
                ts(),
                order_id
            )
        )

        if c.total_changes != 1:
            c.close()

            await cb.answer(
                "Buyurtmani boshqa haydovchi oldi.",
                show_alert=True
            )
            return

        c.execute(
            """
            UPDATE drivers
            SET active_orders=active_orders+1
            WHERE tg_id=?
            """,
            (uid,)
        )

        c.commit()
        c.close()

    await event(
        order_id,
        uid,
        "ACCEPTED"
    )

    u = await get_user(
        o["customer_id"]
    )

    customer_text = (
        f"✅ <b>Haydovchi topildi — "
        f"buyurtma #{order_id}</b>\n\n"
        f"👤 Haydovchi: {d['name']}\n"
        f"📱 Telefon: {d['phone']}\n"
        f"🚘 Mashina: {d['car_model']}\n"
        f"🔢 Raqam: {d['plate']}\n"
        f"💰 Narx: {o['price']:,} so‘m\n\n"
        f"📝 {o['route_text']}"
    )

    await bot.send_message(
        o["customer_id"],
        customer_text
    )

    if d["car_photo_id"]:
        await bot.send_photo(
            o["customer_id"],
            d["car_photo_id"],
            caption="🚘 Haydovchi mashinasi"
        )

    driver_text = (
        f"✅ <b>Buyurtma #{order_id} qabul qilindi.</b>\n\n"
        f"👤 Mijoz: {u['name']}\n"
        f"📱 Telefon: {u['phone']}\n"
        f"📝 {o['route_text']}\n"
        f"💰 {o['price']:,} so‘m\n\n"
        "Mijoz javob bermasa, "
        "📵 <b>MIJOZ JAVOB BERMADI</b> "
        "tugmasidan foydalaning."
    )

    await bot.send_message(
        uid,
        driver_text,
        reply_markup=no_answer_kb(
            order_id
        )
    )

    if (
        o["lat"] is not None
        and o["lon"] is not None
    ):
        await bot.send_location(
            uid,
            o["lat"],
            o["lon"]
        )

    await cb.answer(
        "Buyurtma sizga biriktirildi."
    )


# ============================================================
# NO ANSWER
# ============================================================

@dp.callback_query(
    F.data.startswith("noans:")
)
async def no_answer(
    cb: CallbackQuery
):
    order_id = int(
        cb.data.split(":")[1]
    )

    uid = cb.from_user.id

    o = await q(
        """
        SELECT *
        FROM orders
        WHERE id=?
        """,
        (order_id,),
        one=True,
        commit=False
    )

    if (
        not o
        or o["status"] != "ACCEPTED"
        or o["driver_id"] != uid
    ):
        await cb.answer(
            "Bu buyurtma uchun amal qilib bo‘lmaydi.",
            show_alert=True
        )
        return

    await q(
        """
        UPDATE orders
        SET
            status='NO_ANSWER_WAIT',
            no_answer_driver=?,
            no_answer_started=?
        WHERE id=?
        """,
        (
            uid,
            ts(),
            order_id
        )
    )

    await q(
        """
        UPDATE drivers
        SET active_orders =
            MAX(active_orders-1, 0)
        WHERE tg_id=?
        """,
        (uid,)
    )

    await event(
        order_id,
        uid,
        "NO_ANSWER_STARTED"
    )

    await cb.answer(
        "1 daqiqalik jarayon boshlandi."
    )

    try:
        await cb.message.edit_reply_markup(
            reply_markup=None
        )
    except Exception:
        pass

    await bot.send_message(
        o["customer_id"],
        "🚕 Haydovchi siz bilan "
        "bog‘lana olmadi.\n\n"
        "Sizga hali ham mashina kerakmi?\n"
        "⏱ <b>1 daqiqa</b> ichida "
        "javob bering.",
        reply_markup=customer_need_kb(
            order_id
        )
    )

    asyncio.create_task(
        no_answer_timeout(order_id)
    )


async def no_answer_timeout(order_id):
    await asyncio.sleep(
        NO_ANSWER_SECONDS
    )

    o = await q(
        """
        SELECT *
        FROM orders
        WHERE id=?
        """,
        (order_id,),
        one=True,
        commit=False
    )

    if (
        o
        and o["status"] == "NO_ANSWER_WAIT"
    ):
        await q(
            """
            UPDATE orders
            SET status='CANCELLED'
            WHERE id=?
            """,
            (order_id,)
        )

        await event(
            order_id,
            None,
            "NO_ANSWER_TIMEOUT"
        )

        await bot.send_message(
            o["customer_id"],
            f"⏱ Buyurtma #{order_id} "
            "javob bo‘lmagani uchun "
            "avtomatik bekor qilindi."
        )


# ============================================================
# CUSTOMER: YES
# ============================================================

@dp.callback_query(
    F.data.startswith("needyes:")
)
async def need_yes(
    cb: CallbackQuery
):
    order_id = int(
        cb.data.split(":")[1]
    )

    o = await q(
        """
        SELECT *
        FROM orders
        WHERE id=?
        """,
        (order_id,),
        one=True,
        commit=False
    )

    if (
        not o
        or o["status"] != "NO_ANSWER_WAIT"
    ):
        await cb.answer(
            "Bu buyurtma endi faol emas.",
            show_alert=True
        )
        return

    old_driver = o[
        "no_answer_driver"
    ]

    if old_driver:
        await q(
            """
            INSERT OR IGNORE INTO declined(
                order_id,
                driver_id,
                created_at
            )
            VALUES(?,?,?)
            """,
            (
                order_id,
                old_driver,
                ts()
            )
        )

    await q(
        """
        UPDATE orders

        SET
            status='SEARCHING',
            driver_id=NULL,
            no_answer_driver=NULL,
            no_answer_started=NULL

        WHERE id=?
        """,
        (order_id,)
    )

    await event(
        order_id,
        cb.from_user.id,
        "REOPENED",
        f"excluded_driver={old_driver}"
    )

    try:
        await cb.message.edit_reply_markup(
            reply_markup=None
        )
    except Exception:
        pass

    await cb.message.answer(
        "🔎 Boshqa haydovchi qidirilmoqda..."
    )

    await cb.answer()

    asyncio.create_task(
        dispatch(order_id)
    )


# ============================================================
# CUSTOMER: NO
# ============================================================

@dp.callback_query(
    F.data.startswith("needno:")
)
async def need_no(
    cb: CallbackQuery
):
    order_id = int(
        cb.data.split(":")[1]
    )

    o = await q(
        """
        SELECT *
        FROM orders
        WHERE id=?
        """,
        (order_id,),
        one=True,
        commit=False
    )

    if (
        not o
        or o["status"] != "NO_ANSWER_WAIT"
    ):
        await cb.answer(
            "Bu buyurtma endi faol emas.",
            show_alert=True
        )
        return

    await q(
        """
        UPDATE orders
        SET status='CANCELLED'
        WHERE id=?
        """,
        (order_id,)
    )

    await event(
        order_id,
        cb.from_user.id,
        "CUSTOMER_CANCELLED"
    )

    try:
        await cb.message.edit_reply_markup(
            reply_markup=None
        )
    except Exception:
        pass

    await cb.message.answer(
        "❌ Buyurtma bekor qilindi."
    )

    await cb.answer()


# ============================================================
# DRIVER ONLINE / OFFLINE
# ============================================================

@dp.message(
    F.text.regexp(
        r"^(🟢 ONLINE|⚪ OFFLINE)$"
    )
)
async def toggle_online(
    message: Message
):
    d = await get_driver(
        message.from_user.id
    )

    if (
        not d
        or not d["approved"]
    ):
        await message.answer(
            "⚠️ Avval admin tasdig‘idan "
            "o‘tishingiz kerak."
        )
        return

    new_status = (
        0
        if d["online"]
        else 1
    )

    await q(
        """
        UPDATE drivers
        SET online=?
        WHERE tg_id=?
        """,
        (
            new_status,
            message.from_user.id
        )
    )

    if new_status:
        text = (
            "🟢 <b>ONLINE rejim yoqildi.</b>\n\n"
            "Endi sizga yangi buyurtmalar "
            "kelishi mumkin."
        )
    else:
        text = (
            "⚪ <b>OFFLINE rejim yoqildi.</b>\n\n"
            "Yangi buyurtmalar kelmaydi."
        )

    await message.answer(
        text,
        reply_markup=driver_kb(
            bool(new_status),
            d["active_orders"]
        )
    )


# ============================================================
# DRIVER ACTIVE ORDERS
# ============================================================

@dp.message(
    F.text.startswith("📦 Buyurtmalar")
)
async def driver_orders(
    message: Message
):
    d = await get_driver(
        message.from_user.id
    )

    if (
        not d
        or not d["approved"]
    ):
        return

    rows = await q(
        """
        SELECT *
        FROM orders

        WHERE driver_id=?
        AND status='ACCEPTED'

        ORDER BY id DESC
        """,
        (
            message.from_user.id,
        ),
        many=True,
        commit=False
    )

    if not rows:
        await message.answer(
            "📭 Faol buyurtma yo‘q."
        )
        return

    for o in rows:
        await message.answer(
            f"🚕 <b>#{o['id']}</b>\n\n"
            f"📝 {o['route_text']}\n"
            f"💰 {o['price']:,} so‘m\n"
            "📍 GPS mavjud",
            reply_markup=no_answer_kb(
                o["id"]
            )
        )


# ============================================================
# DRIVER INCOME
# ============================================================

@dp.message(
    F.text == "💰 Daromad"
)
async def driver_income(
    message: Message
):
    d = await get_driver(
        message.from_user.id
    )

    if not d:
        return

    result = await q(
        """
        SELECT
            COALESCE(SUM(price),0) AS total,
            COUNT(*) AS n

        FROM orders

        WHERE driver_id=?
        AND status='COMPLETED'
        """,
        (
            message.from_user.id,
        ),
        one=True,
        commit=False
    )

    await message.answer(
        "💰 <b>Daromad</b>\n\n"
        f"📦 Yakunlangan buyurtmalar: "
        f"{result['n']}\n"
        f"💵 Jami: "
        f"{result['total']:,} so‘m"
    )


# ============================================================
# DRIVER RATING
# ============================================================

@dp.message(
    F.text == "⭐ Reyting"
)
async def driver_rating(
    message: Message
):
    d = await get_driver(
        message.from_user.id
    )

    if d:
        await message.answer(
            "⭐ <b>Reyting</b>\n\n"
            f"⭐ Reyting: "
            f"<b>{d['rating']:.1f}</b>\n"
            f"👥 Baholar: "
            f"{d['rating_count']}"
        )


# ============================================================
# COMPLETE ORDER
# ============================================================

@dp.message(Command("done"))
async def done(
    message: Message
):
    d = await get_driver(
        message.from_user.id
    )

    if not d:
        return

    parts = (
        message.text or ""
    ).split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):
        await message.answer(
            "Format:\n"
            "<code>/done ORDER_ID</code>"
        )
        return

    order_id = int(
        parts[1]
    )

    o = await q(
        """
        SELECT *
        FROM orders
        WHERE id=?
        """,
        (order_id,),
        one=True,
        commit=False
    )

    if (
        not o
        or o["driver_id"]
        != message.from_user.id
        or o["status"] != "ACCEPTED"
    ):
        await message.answer(
            "❌ Buyurtma topilmadi "
            "yoki faol emas."
        )
        return

    await q(
        """
        UPDATE orders
        SET
            status='COMPLETED',
            finished_at=?
        WHERE id=?
        """,
        (
            ts(),
            order_id
        )
    )

    await q(
        """
        UPDATE drivers
        SET active_orders =
            MAX(active_orders-1,0)
        WHERE tg_id=?
        """,
        (
            message.from_user.id,
        )
    )

    await event(
        order_id,
        message.from_user.id,
        "COMPLETED"
    )

    await message.answer(
        f"✅ Buyurtma #{order_id} "
        "yakunlandi."
    )

    await bot.send_message(
        o["customer_id"],
        f"✅ Buyurtma #{order_id} "
        "yakunlandi.\n\n"
        "⭐ Haydovchini baholang:\n"
        "1 — 2 — 3 — 4 — 5"
    )


# ============================================================
# CUSTOMER RATING
# ============================================================

@dp.message(
    F.text.regexp(r"^[1-5]$")
)
async def rating(
    message: Message
):
    score = int(
        message.text
    )

    uid = message.from_user.id

    order = await q(
        """
        SELECT *
        FROM orders

        WHERE customer_id=?
        AND status='COMPLETED'

        AND id NOT IN (
            SELECT order_id
            FROM ratings
        )

        ORDER BY id DESC
        LIMIT 1
        """,
        (uid,),
        one=True,
        commit=False
    )

    if not order:
        return

    await q(
        """
        INSERT OR IGNORE INTO ratings(
            order_id,
            score,
            created_at
        )
        VALUES(?,?,?)
        """,
        (
            order["id"],
            score,
            ts()
        )
    )

    d = await get_driver(
        order["driver_id"]
    )

    if d:
        new_count = (
            d["rating_count"] + 1
        )

        new_rating = (
            (
                d["rating"]
                * d["rating_count"]
            )
            + score
        ) / new_count

        await q(
            """
            UPDATE drivers
            SET
                rating=?,
                rating_count=?
            WHERE tg_id=?
            """,
            (
                new_rating,
                new_count,
                order["driver_id"]
            )
        )

    await message.answer(
        "⭐ Rahmat!\n"
        "Bahoyingiz qabul qilindi.",
        reply_markup=main_kb()
    )


# ============================================================
# ADMIN
# ============================================================

def is_admin(uid):
    return (
        ADMIN_ID
        and uid == ADMIN_ID
    )


@dp.message(Command("admin"))
async def admin_panel(
    message: Message
):
    if not is_admin(
        message.from_user.id
    ):
        return

    users = await q(
        """
        SELECT COUNT(*) AS n
        FROM users
        """,
        one=True,
        commit=False
    )

    drivers = await q(
        """
        SELECT COUNT(*) AS n
        FROM drivers
        """,
        one=True,
        commit=False
    )

    online = await q(
        """
        SELECT COUNT(*) AS n
        FROM drivers
        WHERE online=1
        AND approved=1
        """,
        one=True,
        commit=False
    )

    active = await q(
        """
        SELECT COUNT(*) AS n
        FROM orders
        WHERE status IN(
            'SEARCHING',
            'ACCEPTED',
            'NO_ANSWER_WAIT'
        )
        """,
        one=True,
        commit=False
    )

    await message.answer(
        "🛠 <b>ADMIN PANEL</b>\n\n"
        f"👥 Mijozlar: {users['n']}\n"
        f"🚗 Haydovchilar: {drivers['n']}\n"
        f"🟢 ONLINE: {online['n']}\n"
        f"📦 Faol buyurtmalar: {active['n']}\n\n"

        "<b>Buyruqlar:</b>\n"
        "/pending\n"
        "/drivers\n"
        "/users\n"
        "/orders\n"
        "/order ID\n"
        "/block ID\n"
        "/unblock ID"
    )


# ============================================================
# ADMIN PENDING
# ============================================================

@dp.message(Command("pending"))
async def pending(
    message: Message
):
    if not is_admin(
        message.from_user.id
    ):
        return

    rows = await q(
        """
        SELECT *
        FROM drivers
        WHERE approved=0
        ORDER BY created_at DESC
        """,
        many=True,
        commit=False
    )

    if not rows:
        await message.answer(
            "📭 Kutilayotgan ariza yo‘q."
        )
        return

    for d in rows:
        await message.answer(
            "🚗 <b>YANGI HAYDOVCHI</b>\n\n"
            f"👤 {d['name']}\n"
            f"📱 {d['phone']}\n"
            f"🚘 {d['car_model']}\n"
            f"🔢 {d['plate']}\n"
            f"🆔 {d['tg_id']}",
            reply_markup=admin_driver_kb(
                d["tg_id"]
            )
        )


# ============================================================
# ADMIN APPROVE
# ============================================================

@dp.callback_query(
    F.data.startswith("approve:")
)
async def approve(
    cb: CallbackQuery
):
    if not is_admin(
        cb.from_user.id
    ):
        return

    uid = int(
        cb.data.split(":")[1]
    )

    await q(
        """
        UPDATE drivers
        SET
            approved=1,
            online=0
        WHERE tg_id=?
        """,
        (uid,)
    )

    await cb.answer(
        "Tasdiqlandi."
    )

    try:
        await cb.message.edit_reply_markup(
            reply_markup=None
        )
    except Exception:
        pass

    try:
        await bot.send_message(
            uid,
            "✅ <b>Haydovchi arizangiz "
            "tasdiqlandi!</b>\n\n"
            "Endi bot orqali ONLINE "
            "bo‘lishingiz mumkin."
        )
    except Exception:
        pass


# ============================================================
# ADMIN REJECT
# ============================================================

@dp.callback_query(
    F.data.startswith("reject:")
)
async def reject(
    cb: CallbackQuery
):
    if not is_admin(
        cb.from_user.id
    ):
        return

    uid = int(
        cb.data.split(":")[1]
    )

    # Rad etilganda o'chiriladi.
    # Haydovchi qayta ro'yxatdan o'tishi mumkin.
    await q(
        """
        DELETE FROM drivers
        WHERE tg_id=?
        """,
        (uid,)
    )

    await cb.answer(
        "Ariza rad etildi."
    )

    try:
        await cb.message.edit_reply_markup(
            reply_markup=None
        )
    except Exception:
        pass

    try:
        await bot.send_message(
            uid,
            "❌ <b>Arizangiz rad etildi.</b>\n\n"
            "Hujjatlarni to‘g‘rilab, "
            "qayta ro‘yxatdan o‘tishingiz mumkin."
        )
    except Exception:
        pass


# ============================================================
# ADMIN DRIVERS
# ============================================================

@dp.message(Command("drivers"))
async def admin_drivers(
    message: Message
):
    if not is_admin(
        message.from_user.id
    ):
        return

    rows = await q(
        """
        SELECT *
        FROM drivers
        ORDER BY created_at DESC
        LIMIT 100
        """,
        many=True,
        commit=False
    )

    if not rows:
        await message.answer(
            "🚗 Haydovchilar yo‘q."
        )
        return

    lines = []

    for d in rows:
        approval = (
            "APPROVED"
            if d["approved"]
            else
            "PENDING"
        )

        online = (
            "ON"
            if d["online"]
            else
            "OFF"
        )

        lines.append(
            f"{d['tg_id']} | "
            f"{d['name']} | "
            f"{approval} | "
            f"{online} | "
            f"{d['active_orders']}/{MAX_ACTIVE}"
        )

    text = (
        "🚗 <b>HAYDOVCHILAR</b>\n\n"
        + "\n".join(lines)
    )

    await message.answer(
        text[:4000]
    )


# ============================================================
# ADMIN USERS
# ============================================================

@dp.message(Command("users"))
async def admin_users(
    message: Message
):
    if not is_admin(
        message.from_user.id
    ):
        return

    rows = await q(
        """
        SELECT *
        FROM users
        ORDER BY tg_id DESC
        LIMIT 100
        """,
        many=True,
        commit=False
    )

    lines = []

    for u in rows:
        status = (
            "BLOCK"
            if u["blocked"]
            else
            "OK"
        )

        lines.append(
            f"{u['tg_id']} | "
            f"{u['name']} | "
            f"{u['phone']} | "
            f"{status}"
        )

    await message.answer(
        "👥 <b>USERS</b>\n\n"
        + "\n".join(lines)[:3900]
    )


# ============================================================
# ADMIN ORDERS
# ============================================================

@dp.message(Command("orders"))
async def admin_orders(
    message: Message
):
    if not is_admin(
        message.from_user.id
    ):
        return

    rows = await q(
        """
        SELECT *
        FROM orders
        ORDER BY id DESC
        LIMIT 50
        """,
        many=True,
        commit=False
    )

    lines = []

    for o in rows:
        lines.append(
            f"#{o['id']} | "
            f"{o['status']} | "
            f"{o['service']} | "
            f"{o['price']:,} | "
            f"driver={o['driver_id']}"
        )

    await message.answer(
        "📦 <b>ORDERS</b>\n\n"
        + "\n".join(lines)[:3900]
    )


# ============================================================
# ADMIN ORDER DETAIL
# ============================================================

@dp.message(Command("order"))
async def admin_order(
    message: Message
):
    if not is_admin(
        message.from_user.id
    ):
        return

    parts = (
        message.text or ""
    ).split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):
        await message.answer(
            "/order ORDER_ID"
        )
        return

    order_id = int(
        parts[1]
    )

    o = await q(
        """
        SELECT *
        FROM orders
        WHERE id=?
        """,
        (order_id,),
        one=True,
        commit=False
    )

    if not o:
        await message.answer(
            "❌ Buyurtma topilmadi."
        )
        return

    await message.answer(
        f"📦 <b>ORDER #{o['id']}</b>\n\n"
        f"Status: {o['status']}\n"
        f"Customer: {o['customer_id']}\n"
        f"Driver: {o['driver_id']}\n\n"
        f"📝 {o['route_text']}\n\n"
        f"💰 {o['price']:,} so‘m\n"
        f"Created: {o['created_at']}"
    )


# ============================================================
# ADMIN BLOCK
# ============================================================

@dp.message(Command("block"))
async def admin_block(
    message: Message
):
    if not is_admin(
        message.from_user.id
    ):
        return

    parts = (
        message.text or ""
    ).split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):
        await message.answer(
            "/block TELEGRAM_ID"
        )
        return

    uid = int(
        parts[1]
    )

    await q(
        """
        UPDATE users
        SET blocked=1
        WHERE tg_id=?
        """,
        (uid,)
    )

    await q(
        """
        UPDATE drivers
        SET online=0
        WHERE tg_id=?
        """,
        (uid,)
    )

    await message.answer(
        "🚫 Foydalanuvchi bloklandi."
    )


# ============================================================
# ADMIN UNBLOCK
# ============================================================

@dp.message(Command("unblock"))
async def admin_unblock(
    message: Message
):
    if not is_admin(
        message.from_user.id
    ):
        return

    parts = (
        message.text or ""
    ).split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):
        await message.answer(
            "/unblock TELEGRAM_ID"
        )
        return

    uid = int(
        parts[1]
    )

    await q(
        """
        UPDATE users
        SET blocked=0
        WHERE tg_id=?
        """,
        (uid,)
    )

    await message.answer(
        "🔓 Blok olib tashlandi."
    )


# ============================================================
# PROFILE
# ============================================================

@dp.message(
    F.text == "👤 Profil"
)
async def profile(
    message: Message
):
    uid = message.from_user.id

    u = await get_user(uid)
    d = await get_driver(uid)

    if d and d["approved"]:

        await message.answer(
            "🚗 <b>Haydovchi profili</b>\n\n"
            f"👤 {d['name']}\n"
            f"📱 {d['phone']}\n"
            f"🚘 {d['car_model']}\n"
            f"🔢 {d['plate']}\n"
            f"⭐ {d['rating']:.1f}\n"
            f"📦 Faol: "
            f"{d['active_orders']}/{MAX_ACTIVE}"
        )

    elif u:

        await message.answer(
            "👤 <b>Profil</b>\n\n"
            f"Ism: {u['name']}\n"
            f"Telefon: {u['phone']}"
        )


# ============================================================
# CUSTOMER HISTORY
# ============================================================

@dp.message(
    F.text == "📋 Buyurtmalarim"
)
async def history(
    message: Message
):
    rows = await q(
        """
        SELECT *
        FROM orders

        WHERE customer_id=?

        ORDER BY id DESC
        LIMIT 10
        """,
        (
            message.from_user.id,
        ),
        many=True,
        commit=False
    )

    if not rows:
        await message.answer(
            "📭 Hali buyurtmalar yo‘q."
        )
        return

    lines = []

    for o in rows:
        lines.append(
            f"#{o['id']} — "
            f"{o['status']} — "
            f"{o['price']:,} so‘m\n"
            f"📝 {o['route_text']}"
        )

    await message.answer(
        "📋 <b>Buyurtmalarim</b>\n\n"
        + "\n\n".join(lines)
    )


# ============================================================
# SUPPORT
# ============================================================

@dp.message(
    F.text == "🆘 Yordam"
)
async def help_start(
    message: Message,
    state: FSMContext
):
    await state.set_state(
        Support.text
    )

    await message.answer(
        "🆘 Muammo yoki savolingizni yozing.\n\n"
        "Admin ko‘rib chiqadi."
    )


@dp.message(Support.text)
async def support_text(
    message: Message,
    state: FSMContext
):
    text = (
        message.text or ""
    ).strip()

    if not text:
        await message.answer(
            "Matn yuboring."
        )
        return

    await q(
        """
        INSERT INTO events(
            order_id,
            actor,
            event,
            details,
            created_at
        )
        VALUES(NULL,?,?,?,?)
        """,
        (
            message.from_user.id,
            "SUPPORT",
            text,
            ts()
        )
    )

    await state.clear()

    await message.answer(
        "✅ Xabaringiz adminlarga yuborildi.",
        reply_markup=main_kb()
    )

    if ADMIN_ID:
        await bot.send_message(
            ADMIN_ID,
            "🆘 <b>YORDAM</b>\n\n"
            f"👤 {message.from_user.id}\n\n"
            f"{text}"
        )


# ============================================================
# HOME
# ============================================================

@dp.message(
    F.text == "🏠 Asosiy menyu"
)
async def home(
    message: Message,
    state: FSMContext
):
    await show_home(
        message,
        state
    )


# ============================================================
# CANCEL COMMAND
# ============================================================

@dp.message(Command("cancel"))
async def cancel(
    message: Message,
    state: FSMContext
):
    await show_home(
        message,
        state
    )


# ============================================================
# FALLBACK
# ============================================================

@dp.message()
async def fallback(
    message: Message,
    state: FSMContext
):
    current = await state.get_state()

    if current:
        return

    u = await get_user(
        message.from_user.id
    )

    if not u:
        await message.answer(
            "Avval /start bosing."
        )
        return

    if u["blocked"]:
        await message.answer(
            "⛔ Akkauntingiz bloklangan."
        )
        return

    await message.answer(
        "🤖 Menyudan foydalaning.",
        reply_markup=main_kb()
    )


# ============================================================
# MAIN
# ============================================================

async def main():

    init_db()

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    log.info(
        "TAXI BOR MI? bot started"
    )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
