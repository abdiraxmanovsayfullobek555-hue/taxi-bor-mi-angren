import os
import re
import json
import base64
import asyncio
import sqlite3
from datetime import datetime
from io import BytesIO
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    KeyboardButton,
    ReplyKeyboardMarkup,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from openai import AsyncOpenAI


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

# OpenAI API modeli.
# Railway Variables orqali o'zgartirish mumkin.
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna").strip()

DB_FILE = os.getenv("DB_FILE", "taxi_bor_mi.db").strip()

ADMIN_ID_RAW = os.getenv("ADMIN_ID", "0").strip()

try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except ValueError:
    ADMIN_ID = 0


if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN topilmadi. Railway Variables ichiga BOT_TOKEN qo'shing."
    )


# ============================================================
# BOT
# ============================================================

bot = Bot(token=BOT_TOKEN)

dp = Dispatcher(storage=MemoryStorage())

ai = (
    AsyncOpenAI(api_key=OPENAI_API_KEY)
    if OPENAI_API_KEY
    else None
)


# ============================================================
# CONSTANTS
# ============================================================

ROUTE = "OBLIQ ↔ ANGREN"

MAX_ACTIVE_ORDERS = 7

# Haydovchi buyurtmani olish uchun 2 daqiqa.
CLAIM_SECONDS = 120

# Haydovchi topilmasa 60 soniya.
SEARCH_SECONDS = 60

MAX_PRICE = 10_000_000

ACTIVE_STATUSES = (
    "ACCEPTED",
    "CONTACTED",
    "ON_WAY",
    "PICKED_UP",
)

FINISHED_STATUSES = (
    "COMPLETED",
    "CANCELLED",
    "NO_DRIVER",
    "EXPIRED",
)


# Bitta process ichidagi SQLite transactionlarini himoya qiladi.
DB_LOCK = asyncio.Lock()


# ============================================================
# TIME
# ============================================================

def now() -> str:
    return datetime.utcnow().isoformat(timespec="seconds")


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(
        DB_FILE,
        check_same_thread=False,
        timeout=30,
    )

    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")

    return conn


def init_db():

    conn = db()

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users(
            tg_id INTEGER PRIMARY KEY,
            role TEXT DEFAULT 'customer',
            lang TEXT DEFAULT 'uz',
            name TEXT,
            phone TEXT,
            home_area TEXT,
            blocked INTEGER DEFAULT 0,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS drivers(
            tg_id INTEGER PRIMARY KEY,
            name TEXT,
            phone TEXT,
            car_model TEXT,
            plate TEXT UNIQUE,
            license_file_id TEXT,
            tech_file_id TEXT,
            car_photo_file_id TEXT,
            route TEXT DEFAULT 'OBLIQ ↔ ANGREN',
            approved INTEGER DEFAULT 0,
            online INTEGER DEFAULT 0,
            active_orders INTEGER DEFAULT 0,
            rating REAL DEFAULT 5.0,
            rating_count INTEGER DEFAULT 0,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            service TEXT NOT NULL,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,
            passengers INTEGER DEFAULT 1,
            price INTEGER NOT NULL,
            status TEXT DEFAULT 'SEARCHING',
            driver_id INTEGER,
            customer_phone TEXT,
            origin_lat REAL,
            origin_lon REAL,
            raw_text TEXT,
            ai_json TEXT,
            created_at TEXT,
            claimed_at TEXT,
            completed_at TEXT
        );

        CREATE TABLE IF NOT EXISTS ratings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER UNIQUE,
            from_id INTEGER,
            to_id INTEGER,
            score INTEGER,
            comment TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS support(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER,
            text TEXT,
            category TEXT,
            ai_json TEXT,
            status TEXT DEFAULT 'OPEN',
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER,
            event TEXT,
            payload TEXT,
            created_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_orders_status
        ON orders(status);

        CREATE INDEX IF NOT EXISTS idx_orders_customer
        ON orders(customer_id);

        CREATE INDEX IF NOT EXISTS idx_orders_driver
        ON orders(driver_id);

        CREATE INDEX IF NOT EXISTS idx_drivers_online
        ON drivers(approved, online);

        CREATE INDEX IF NOT EXISTS idx_support_status
        ON support(status);
        """
    )

    conn.commit()
    conn.close()


def get_user(tg_id: int):

    conn = db()

    row = conn.execute(
        "SELECT * FROM users WHERE tg_id=?",
        (tg_id,),
    ).fetchone()

    conn.close()

    return row


def get_driver(tg_id: int):

    conn = db()

    row = conn.execute(
        "SELECT * FROM drivers WHERE tg_id=?",
        (tg_id,),
    ).fetchone()

    conn.close()

    return row


def is_blocked(tg_id: int) -> bool:

    u = get_user(tg_id)

    if not u:
        return False

    return bool(u["blocked"])


def ensure_user(
    tg_id: int,
    name: Optional[str] = None,
    phone: Optional[str] = None,
):

    conn = db()

    conn.execute(
        """
        INSERT INTO users(
            tg_id,
            name,
            phone,
            created_at
        )
        VALUES(?,?,?,?)

        ON CONFLICT(tg_id)
        DO UPDATE SET
            name=COALESCE(excluded.name, users.name),
            phone=COALESCE(excluded.phone, users.phone)
        """,
        (
            tg_id,
            name,
            phone,
            now(),
        ),
    )

    conn.commit()
    conn.close()


def log_event(
    tg_id: int,
    event: str,
    payload="",
):

    conn = db()

    conn.execute(
        """
        INSERT INTO events(
            tg_id,
            event,
            payload,
            created_at
        )
        VALUES(?,?,?,?)
        """,
        (
            tg_id,
            event,
            str(payload)[:4000],
            now(),
        ),
    )

    conn.commit()
    conn.close()


# ============================================================
# AI
# ============================================================

AI_SYSTEM = """
Siz TAXI BOR MI? — ALBATTA BOR! platformasining yordamchi AI'sisiz.

Asosiy yo'nalish:
OBLIQ ↔ ANGREN

Xizmatlar:
1. YO'LOVCHI
2. DASTAVKA

Qoidalar:

- Bilmagan ma'lumotni o'ylab topmang.
- Ishonchingiz past bo'lsa confidence past bo'lsin.
- Huquqiy yoki xavfsizlik qarorini mustaqil chiqarmang.
- Muhim qarorlar rules engine va admin tasdig'i bilan qilinadi.
- Obliq / Облик / Облиқ -> OBLIQ.
- Angren / Ангрен -> ANGREN.
- Manzil va mahalla nomlarini imkon qadar aynan saqlang.
- JSON so'ralganda faqat JSON qaytaring.
"""


async def ai_text(
    prompt: str,
    image_data_urls=None,
) -> Optional[str]:

    if not ai:
        return None

    try:

        content = [
            {
                "type": "input_text",
                "text": prompt,
            }
        ]

        for url in image_data_urls or []:

            content.append(
                {
                    "type": "input_image",
                    "image_url": url,
                }
            )

        response = await ai.responses.create(
            model=OPENAI_MODEL,
            instructions=AI_SYSTEM,
            input=[
                {
                    "role": "user",
                    "content": content,
                }
            ],
            max_output_tokens=900,
        )

        result = (
            response.output_text
            if hasattr(response, "output_text")
            else ""
        )

        return (result or "").strip() or None

    except Exception as e:

        print(
            "OPENAI ERROR:",
            repr(e),
        )

        return None


def parse_json(text):

    if not text:
        return None

    try:
        return json.loads(text)
    except Exception:
        pass

    match = re.search(
        r"\{.*\}",
        text,
        re.S,
    )

    if not match:
        return None

    try:
        return json.loads(
            match.group(0)
        )
    except Exception:
        return None


# ============================================================
# NORMALIZE
# ============================================================

def normalize_area(value: str) -> str:

    value = re.sub(
        r"\s+",
        " ",
        (value or "").strip(),
    )

    if not value:
        return ""

    low = value.lower()

    low = (
        low
        .replace("ё", "е")
        .replace("‘", "'")
        .replace("’", "'")
    )

    aliases = {

        "obliq": "OBLIQ",
        "oblik": "OBLIQ",
        "облик": "OBLIQ",
        "облиқ": "OBLIQ",

        "angren": "ANGREN",
        "ангрен": "ANGREN",

        "sergu": "SERGU",
        "ilg'or": "SERGU",
        "ilg'or mahallasi": "SERGU",

        "keramicheski": "8 MART",
        "керамический": "8 MART",
    }

    if low in aliases:
        return aliases[low]

    return value.title()


# ============================================================
# ROUTE PARSER
# ============================================================

def deterministic_route(text: str):

    original = (
        text or ""
    ).strip()

    if not original:
        return None

    low = original.lower()

    low = (
        low
        .replace("→", " -> ")
        .replace("—", " -> ")
        .replace("–", " -> ")
        .replace("облик", "obliq")
        .replace("облиқ", "obliq")
        .replace("ангрен", "angren")
    )

    low = re.sub(
        r"\s+",
        " ",
        low,
    )

    # Misol:
    # Obliqdan Hokimiyatga

    match = re.search(
        r"^(.+?)\s+dan\s+(.+?)\s+ga$",
        low,
        re.I,
    )

    if match:

        origin = normalize_area(
            match.group(1)
        )

        destination = normalize_area(
            match.group(2)
        )

        if origin and destination:

            return {
                "origin": origin,
                "destination": destination,
                "confidence": 0.98,
                "source": "rules",
                "needs_confirmation": False,
            }

    # Misol:
    # Obliq -> Hokimiyat

    match = re.search(
        r"^(.+?)\s*(?:->|to)\s*(.+?)$",
        low,
        re.I,
    )

    if match:

        origin = normalize_area(
            match.group(1)
        )

        destination = normalize_area(
            match.group(2)
        )

        if origin and destination:

            return {
                "origin": origin,
                "destination": destination,
                "confidence": 0.96,
                "source": "rules",
                "needs_confirmation": False,
            }

    return None


async def parse_route(text: str):

    rule = deterministic_route(text)

    if rule:
        return rule

    prompt = f"""
Foydalanuvchi yozgan yo'nalish:

{text}

Qayerdan va qayerga ekanini aniqlang.

Faqat JSON qaytaring:

{{
    "origin": "string",
    "destination": "string",
    "confidence": 0.0,
    "needs_confirmation": true
}}
"""

    result = await ai_text(prompt)

    data = parse_json(result)

    if not data:
        return None

    if data.get("origin"):
        data["origin"] = normalize_area(
            data["origin"]
        )

    if data.get("destination"):
        data["destination"] = normalize_area(
            data["destination"]
        )

    return data


# ============================================================
# AI REGISTRATION
# ============================================================

async def ai_validate_registration(
    name,
    phone,
    role,
):

    prompt = f"""
Ro'yxatdan o'tish ma'lumotlarini yordamchi
tekshirib chiqing.

Role: {role}
Name: {name}
Phone: {phone}

Faqat JSON:

{{
    "valid": true,
    "risk": 0,
    "reason": "qisqa sabab"
}}
"""

    data = parse_json(
        await ai_text(prompt)
    )

    return (
        data
        or
        {
            "valid": True,
            "risk": 0,
            "reason": "Rules fallback.",
        }
    )


# ============================================================
# AI SUPPORT
# ============================================================

async def ai_support(text):

    prompt = f"""
Murojaat:

{text}

Kategoriya quyidagilardan biri bo'lsin:

PRICE
DRIVER
CUSTOMER
ADDRESS
PAYMENT
TECHNICAL
SAFETY
OTHER

Faqat JSON:

{{
    "category": "OTHER",
    "risk": 0,
    "summary": "qisqa",
    "needs_admin": false
}}
"""

    data = parse_json(
        await ai_text(prompt)
    )

    return (
        data
        or
        {
            "category": "OTHER",
            "risk": 0,
            "summary": text[:200],
            "needs_admin": True,
        }
    )


# ============================================================
# TELEGRAM IMAGE -> DATA URL
# ============================================================

async def telegram_image_to_data_url(
    file_id: str,
):

    if not ai or not file_id:
        return None

    try:

        file = await bot.get_file(
            file_id
        )

        if not file.file_path:
            return None

        buffer = BytesIO()

        await bot.download_file(
            file.file_path,
            destination=buffer,
        )

        raw = buffer.getvalue()

        mime = "image/jpeg"

        path = file.file_path.lower()

        if path.endswith(".png"):
            mime = "image/png"

        elif path.endswith(".webp"):
            mime = "image/webp"

        return (
            f"data:{mime};base64,"
            f"{base64.b64encode(raw).decode()}"
        )

    except Exception as e:

        print(
            "IMAGE ERROR:",
            repr(e),
        )

        return None


# ============================================================
# AI DRIVER DOCUMENT CHECK
# ============================================================

async def ai_check_driver_documents(
    license_id,
    tech_id,
    car_id,
    name,
    plate,
    model,
):

    urls = []

    for file_id in (
        license_id,
        tech_id,
        car_id,
    ):

        url = await telegram_image_to_data_url(
            file_id
        )

        if url:
            urls.append(url)

    if not urls:

        return {
            "risk": 0,
            "match": "unknown",
            "summary": "AI rasm tekshiruvi mavjud emas.",
        }

    prompt = f"""
Haydovchi:

F.I.Sh.: {name}
Mashina: {model}
Raqam: {plate}

Rasmlarni faqat yordamchi tekshiruv
sifatida tahlil qiling.

Tekshiring:

- rasm sifati;
- hujjat turi tushunarliligi;
- davlat raqami ko'rinishi;
- rasmlar o'zaro mosligi.

Huquqiy haqiqiylikni tasdiqlamang.

Faqat JSON:

{{
    "risk": 0,
    "match": "yes",
    "plate_seen": "string",
    "summary": "qisqa",
    "needs_admin": true
}}
"""

    data = parse_json(
        await ai_text(
            prompt,
            urls,
        )
    )

    return (
        data
        or
        {
            "risk": 20,
            "match": "unknown",
            "summary": "Admin tekshiruvi kerak.",
            "needs_admin": True,
        }
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
                ),
            ],
            [
                KeyboardButton(
                    text="🇷🇺 Русский"
                ),
                KeyboardButton(
                    text="🇬🇧 English"
                ),
            ],
        ],
        resize_keyboard=True,
    )


def main_kb():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="👤 Yo‘lovchi"
                ),
                KeyboardButton(
                    text="📦 Dastavka"
                ),
            ],
            [
                KeyboardButton(
                    text="🚕 Haydovchi"
                ),
                KeyboardButton(
                    text="📜 Buyurtmalarim"
                ),
            ],
            [
                KeyboardButton(
                    text="📩 Murojaat"
                ),
                KeyboardButton(
                    text="👤 Profil"
                ),
            ],
        ],
        resize_keyboard=True,
    )


def phone_kb():

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


def driver_kb(online=False):

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
                    text="📋 Yangi buyurtmalar"
                ),
                KeyboardButton(
                    text="🚕 Faol buyurtmalar"
                ),
            ],
            [
                KeyboardButton(
                    text="📜 Tarix"
                ),
                KeyboardButton(
                    text="💰 Daromad"
                ),
            ],
            [
                KeyboardButton(
                    text="⭐ Reyting"
                ),
                KeyboardButton(
                    text="👤 Profil"
                ),
            ],
            [
                KeyboardButton(
                    text="📩 Murojaat"
                ),
                KeyboardButton(
                    text="🏠 Asosiy menyu"
                ),
            ],
        ],
        resize_keyboard=True,
    )


def confirm_kb():

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ BUYURTMA BERISH",
                    callback_data="order_confirm",
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ O‘ZGARTIRISH",
                    callback_data="order_edit",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ BEKOR QILISH",
                    callback_data="order_cancel",
                )
            ],
        ]
    )


def claim_kb(order_id):

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


def customer_wait_kb(order_id):

    return InlineKeyboardMarkup(
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
                    callback_data=f"cancel_search:{order_id}",
                )
            ],
        ]
    )


def driver_order_kb(
    order_id,
    status="ACCEPTED",
):

    rows = []

    if status == "ACCEPTED":

        rows.append(
            [
                InlineKeyboardButton(
                    text="📞 MIJOZGA QO‘NG‘IROQ",
                    callback_data=f"contact:{order_id}",
                )
            ]
        )

        rows.append(
            [
                InlineKeyboardButton(
                    text="🚗 YO‘LGA CHIQDIM",
                    callback_data=f"status:ON_WAY:{order_id}",
                )
            ]
        )

    elif status == "CONTACTED":

        rows.append(
            [
                InlineKeyboardButton(
                    text="🚗 YO‘LGA CHIQDIM",
                    callback_data=f"status:ON_WAY:{order_id}",
                )
            ]
        )

    elif status == "ON_WAY":

        rows.append(
            [
                InlineKeyboardButton(
                    text="📍 MIJOZNI OLDIM",
                    callback_data=f"status:PICKED_UP:{order_id}",
                )
            ]
        )

    elif status == "PICKED_UP":

        rows.append(
            [
                InlineKeyboardButton(
                    text="✅ SAFAR YAKUNLANDI",
                    callback_data=f"complete:{order_id}",
                )
            ]
        )

    rows.append(
        [
            InlineKeyboardButton(
                text="❌ BUYURTMANI BEKOR QILISH",
                callback_data=f"driver_cancel:{order_id}",
            )
        ]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def rating_kb(order_id):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐1",
                    callback_data=f"rate:{order_id}:1",
                ),
                InlineKeyboardButton(
                    text="⭐2",
                    callback_data=f"rate:{order_id}:2",
                ),
                InlineKeyboardButton(
                    text="⭐3",
                    callback_data=f"rate:{order_id}:3",
                ),
                InlineKeyboardButton(
                    text="⭐4",
                    callback_data=f"rate:{order_id}:4",
                ),
                InlineKeyboardButton(
                    text="⭐5",
                    callback_data=f"rate:{order_id}:5",
                ),
            ]
        ]
    )


# ============================================================
# FSM
# ============================================================

class Registration(StatesGroup):

    lang = State()
    name = State()
    phone = State()
    home = State()


class DriverReg(StatesGroup):

    name = State()
    phone = State()
    model = State()
    plate = State()
    license = State()
    tech = State()
    car = State()


class OrderFlow(StatesGroup):

    gps = State()
    route = State()
    passengers = State()
    price = State()
    confirm = State()


class SupportFlow(StatesGroup):

    text = State()


# ============================================================
# /START
# ============================================================

@dp.message(CommandStart())
async def start(
    message: Message,
    state: FSMContext,
):

    uid = message.from_user.id

    u = get_user(uid)

    if u and u["blocked"]:

        await state.clear()

        await message.answer(
            "⛔ Akkauntingiz bloklangan."
        )

        return

    if (
        u
        and u["name"]
        and u["phone"]
    ):

        await state.clear()

        await message.answer(
            "🚕 TAXI BOR MI? — ALBATTA BOR!\n\n"
            "🤖 Aqlli taxi va dastavka platformasi.\n\n"
            f"📍 Yo‘nalish: {ROUTE}",
            reply_markup=main_kb(),
        )

        return

    await state.clear()

    await state.set_state(
        Registration.lang
    )

    await message.answer(
        "🚕 TAXI BOR MI? — ALBATTA BOR!\n\n"
        "🤖 Aqlli botga xush kelibsiz!\n\n"
        "Tilni tanlang:",
        reply_markup=lang_kb(),
    )


# ============================================================
# REGISTRATION
# ============================================================

@dp.message(Registration.lang)
async def reg_lang(
    message: Message,
    state: FSMContext,
):

    text = message.text or ""

    lang = "uz"

    if "Русский" in text:
        lang = "ru"

    elif "English" in text:
        lang = "en"

    elif "Ўзбекча" in text:
        lang = "uz_cyr"

    await state.update_data(
        lang=lang
    )

    await state.set_state(
        Registration.name
    )

    await message.answer(
        "👤 F.I.Sh. yoki ismingizni kiriting:"
    )


@dp.message(Registration.name)
async def reg_name(
    message: Message,
    state: FSMContext,
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
        Registration.phone
    )

    await message.answer(
        "📱 Telefon raqamingizni yuboring:",
        reply_markup=phone_kb(),
    )


@dp.message(Registration.phone)
async def reg_phone(
    message: Message,
    state: FSMContext,
):

    phone = (
        message.contact.phone_number
        if message.contact
        else
        (message.text or "").strip()
    )

    digits = re.sub(
        r"\D",
        "",
        phone,
    )

    if len(digits) < 7:

        await message.answer(
            "⚠️ Telefon raqami noto‘g‘ri.\n"
            "Tugma orqali yuboring."
        )

        return

    data = await state.get_data()

    ai_check = await ai_validate_registration(
        data.get("name", ""),
        phone,
        "customer",
    )

    await state.update_data(
        phone=phone
    )

    await state.set_state(
        Registration.home
    )

    if ai_check.get("risk", 0) >= 80:

        await message.answer(
            "⚠️ Ma'lumotlar qo‘shimcha tekshiruvga yuborilishi mumkin."
        )

    await message.answer(
        "🏘 Uy mahallangizni yozing.\n\n"
        "Masalan: Obliq"
    )


@dp.message(Registration.home)
async def reg_home(
    message: Message,
    state: FSMContext,
):

    home = normalize_area(
        message.text or ""
    )

    if len(home) < 2:

        await message.answer(
            "⚠️ Hududni qayta kiriting."
        )

        return

    data = await state.get_data()

    async with DB_LOCK:

        conn = db()

        conn.execute(
            """
            INSERT INTO users(
                tg_id,
                role,
                lang,
                name,
                phone,
                home_area,
                blocked,
                created_at
            )
            VALUES(?,?,?,?,?,?,0,?)

            ON CONFLICT(tg_id)
            DO UPDATE SET
                role='customer',
                lang=excluded.lang,
                name=excluded.name,
                phone=excluded.phone,
                home_area=excluded.home_area,
                blocked=0
            """,
            (
                message.from_user.id,
                "customer",
                data.get("lang", "uz"),
                data["name"],
                data["phone"],
                home,
                now(),
            ),
        )

        conn.commit()
        conn.close()

    log_event(
        message.from_user.id,
        "customer_registered",
        home,
    )

    await state.clear()

    await message.answer(
        "✅ Ro‘yxatdan o‘tish tugadi!\n\n"
        f"👤 {data['name']}\n"
        f"📱 {data['phone']}\n"
        f"🏘 {home}\n\n"
        "Endi buyurtma berishingiz mumkin.",
        reply_markup=main_kb(),
    )


# ============================================================
# CUSTOMER ORDER
# ============================================================

@dp.message(F.text == "👤 Yo‘lovchi")
async def passenger(
    message: Message,
    state: FSMContext,
):

    await begin_order(
        message,
        state,
        "PASSENGER",
    )


@dp.message(F.text == "📦 Dastavka")
async def delivery(
    message: Message,
    state: FSMContext,
):

    await begin_order(
        message,
        state,
        "DELIVERY",
    )


async def begin_order(
    message,
    state,
    service,
):

    uid = message.from_user.id

    if is_blocked(uid):

        await message.answer(
            "⛔ Akkauntingiz bloklangan."
        )

        return

    user = get_user(uid)

    if not user or not user["phone"]:

        await message.answer(
            "Avval /start orqali ro‘yxatdan o‘ting."
        )

        return

    await state.clear()

    await state.set_state(
        OrderFlow.gps
    )

    await state.update_data(
        service=service
    )

    gps_kb = ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="📍 Joylashuvimni yuborish",
                    request_location=True,
                )
            ],
            [
                KeyboardButton(
                    text="❌ Bekor qilish"
                )
            ],
        ],
        resize_keyboard=True,
    )

    service_name = (
        "👤 YO‘LOVCHI"
        if service == "PASSENGER"
        else
        "📦 DASTAVKA"
    )

    await message.answer(
        f"🚕 Xizmat: {service_name}\n\n"
        "📍 Avval hozirgi joylashuvingizni yuboring.",
        reply_markup=gps_kb,
    )


@dp.message(OrderFlow.gps)
async def order_gps(
    message: Message,
    state: FSMContext,
):

    if not message.location:

        await message.answer(
            "📍 Iltimos, Telegramdagi "
            "joylashuv tugmasini bosing."
        )

        return

    await state.update_data(
        lat=message.location.latitude,
        lon=message.location.longitude,
    )

    await state.set_state(
        OrderFlow.route
    )

    await message.answer(
        "📍 Joylashuvingiz qabul qilindi.\n\n"
        "Endi yo‘nalishni yozing:\n\n"
        "Masalan:\n"
        "Obliqdan Hokimiyatga\n"
        "yoki\n"
        "Obliq → Hokimiyat"
    )


@dp.message(OrderFlow.route)
async def order_route(
    message: Message,
    state: FSMContext,
):

    text = (
        message.text or ""
    ).strip()

    if not text:

        await message.answer(
            "⚠️ Yo‘nalishni matn ko‘rinishida yozing."
        )

        return

    parsed = await parse_route(text)

    if (
        not parsed
        or not parsed.get("origin")
        or not parsed.get("destination")
    ):

        await message.answer(
            "🤖 Manzilni tushunmadim.\n\n"
            "Masalan:\n"
            "Obliqdan Hokimiyatga"
        )

        return

    try:
        confidence = float(
            parsed.get(
                "confidence",
                0,
            )
        )
    except Exception:
        confidence = 0

    origin = normalize_area(
        parsed["origin"]
    )

    destination = normalize_area(
        parsed["destination"]
    )

    await state.update_data(
        origin=origin,
        destination=destination,
        raw_route=text,
    )

    if (
        confidence < 0.70
        or parsed.get("needs_confirmation")
    ):

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"✅ {origin} → {destination}",
                        callback_data="route_ok",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="✏️ Qayta yozish",
                        callback_data="route_retry",
                    )
                ],
            ]
        )

        await message.answer(
            "🤖 AI manzilni shunday tushundi:\n\n"
            f"📍 {origin}\n"
            f"🏁 {destination}\n\n"
            "To‘g‘rimi?",
            reply_markup=keyboard,
        )

        return

    await next_after_route(
        message,
        state,
    )


async def next_after_route(
    message,
    state,
):

    data = await state.get_data()

    if data["service"] == "PASSENGER":

        await state.set_state(
            OrderFlow.passengers
        )

        keyboard = ReplyKeyboardMarkup(
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
                    KeyboardButton(text="7")
                ],
                [
                    KeyboardButton(
                        text="❌ Bekor qilish"
                    )
                ],
            ],
            resize_keyboard=True,
        )

        await message.answer(
            "👥 Necha kishi borasiz?\n\n"
            "1–7 kishigacha.",
            reply_markup=keyboard,
        )

    else:

        await state.set_state(
            OrderFlow.price
        )

        await message.answer(
            "💰 Dastavka narxini so‘mda kiriting.\n\n"
            "Masalan: 30000"
        )


@dp.callback_query(F.data == "route_ok")
async def route_ok(
    call: CallbackQuery,
    state: FSMContext,
):

    await call.answer()

    await next_after_route(
        call.message,
        state,
    )


@dp.callback_query(F.data == "route_retry")
async def route_retry(
    call: CallbackQuery,
    state: FSMContext,
):

    await call.answer()

    await state.set_state(
        OrderFlow.route
    )

    await call.message.answer(
        "✏️ Qayerdan → qayerga manzilini qayta yozing."
    )


@dp.message(OrderFlow.passengers)
async def order_passengers(
    message: Message,
    state: FSMContext,
):

    try:
        count = int(
            (message.text or "").strip()
        )
    except Exception:
        count = 0

    if not 1 <= count <= 7:

        await message.answer(
            "⚠️ 1 dan 7 gacha son tanlang."
        )

        return

    await state.update_data(
        passengers=count
    )

    await state.set_state(
        OrderFlow.price
    )

    await message.answer(
        "💰 Safar narxini so‘mda kiriting.\n\n"
        "Masalan: 30000"
    )


@dp.message(OrderFlow.price)
async def order_price(
    message: Message,
    state: FSMContext,
):

    digits = re.sub(
        r"\D",
        "",
        message.text or "",
    )

    if not digits:

        await message.answer(
            "⚠️ Narxni raqamda kiriting.\n"
            "Masalan: 30000"
        )

        return

    price = int(digits)

    if price <= 0:

        await message.answer(
            "⚠️ Narx 0 dan katta bo‘lishi kerak."
        )

        return

    if price > MAX_PRICE:

        await message.answer(
            f"⚠️ Maksimal narx "
            f"{MAX_PRICE:,} so‘m."
        )

        return

    await state.update_data(
        price=price
    )

    await state.set_state(
        OrderFlow.confirm
    )

    data = await state.get_data()

    service_name = (
        "👤 Yo‘lovchi"
        if data["service"] == "PASSENGER"
        else
        "📦 Dastavka"
    )

    await message.answer(
        "🚕 BUYURTMA\n\n"
        f"🧾 Xizmat: {service_name}\n"
        f"📍 Qayerdan: {data['origin']}\n"
        f"🏁 Qayerga: {data['destination']}\n"
        f"👥 Kishi: {data.get('passengers', 1)}\n"
        f"💰 Narx: {price:,} so‘m\n\n"
        "Ma'lumotlar to‘g‘rimi?",
        reply_markup=confirm_kb(),
    )


# ============================================================
# ORDER EDIT / CANCEL
# ============================================================

@dp.callback_query(F.data == "order_edit")
async def order_edit(
    call: CallbackQuery,
    state: FSMContext,
):

    await call.answer()

    await state.set_state(
        OrderFlow.route
    )

    await call.message.answer(
        "✏️ Manzilni qayta kiriting.\n\n"
        "Masalan:\n"
        "Obliqdan Hokimiyatga"
    )


@dp.callback_query(F.data == "order_cancel")
async def order_cancel(
    call: CallbackQuery,
    state: FSMContext,
):

    await call.answer()

    await state.clear()

    await call.message.answer(
        "❌ Buyurtma bekor qilindi.",
        reply_markup=main_kb(),
    )


# ============================================================
# CREATE ORDER
# ============================================================

@dp.callback_query(F.data == "order_confirm")
async def order_confirm(
    call: CallbackQuery,
    state: FSMContext,
):

    await call.answer()

    uid = call.from_user.id

    if is_blocked(uid):

        await call.message.answer(
            "⛔ Akkauntingiz bloklangan."
        )

        return

    data = await state.get_data()

    user = get_user(uid)

    if not user or not user["phone"]:

        await call.message.answer(
            "Avval /start orqali ro‘yxatdan o‘ting."
        )

        return

    if not data.get("origin") or not data.get("destination"):

        await call.message.answer(
            "⚠️ Buyurtma ma'lumotlari to‘liq emas."
        )

        await state.clear()

        return

    async with DB_LOCK:

        conn = db()

        cursor = conn.execute(
            """
            INSERT INTO orders(
                customer_id,
                service,
                origin,
                destination,
                passengers,
                price,
                status,
                customer_phone,
                origin_lat,
                origin_lon,
                raw_text,
                created_at
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                uid,
                data["service"],
                data["origin"],
                data["destination"],
                data.get("passengers", 1),
                data["price"],
                "SEARCHING",
                user["phone"],
                data.get("lat"),
                data.get("lon"),
                data.get("raw_route", ""),
                now(),
            ),
        )

        order_id = cursor.lastrowid

        conn.commit()
        conn.close()

    await state.clear()

    log_event(
        uid,
        "order_created",
        order_id,
    )

    await call.message.answer(
        f"🔎 #{order_id} buyurtma haydovchilardan qidirilmoqda...\n\n"
        f"📍 {data['origin']} → {data['destination']}\n"
        f"💰 {data['price']:,} so‘m",
        reply_markup=main_kb(),
    )

    asyncio.create_task(
        dispatch_order(order_id)
    )


# ============================================================
# DRIVER SELECTION
# ============================================================

async def eligible_drivers():

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM drivers
        WHERE approved=1
          AND online=1
          AND route=?
          AND active_orders < ?
        ORDER BY
            rating DESC,
            rating_count DESC,
            tg_id ASC
        """,
        (
            ROUTE,
            MAX_ACTIVE_ORDERS,
        ),
    ).fetchall()

    conn.close()

    return rows


# ============================================================
# DISPATCH
# ============================================================

async def dispatch_order(order_id):

    await asyncio.sleep(1)

    conn = db()

    order = conn.execute(
        "SELECT * FROM orders WHERE id=?",
        (order_id,),
    ).fetchone()

    conn.close()

    if not order:
        return

    if order["status"] != "SEARCHING":
        return

    drivers = await eligible_drivers()

    if not drivers:

        await asyncio.sleep(
            SEARCH_SECONDS
        )

        await expire_search(
            order_id
        )

        return

    text = (
        f"🚕 YANGI BUYURTMA #{order_id}\n\n"
        f"📍 {order['origin']} → {order['destination']}\n"
        f"👥 {order['passengers']} kishi\n"
        f"💰 {order['price']:,} so‘m\n\n"
        "⏱ Qabul qilish oynasi: 2 daqiqa.\n"
        "📱 Mijoz telefoni faqat buyurtmani "
        "qabul qilgan haydovchiga beriladi."
    )

    sent = 0

    for driver in drivers:

        try:

            await bot.send_message(
                driver["tg_id"],
                text,
                reply_markup=claim_kb(
                    order_id
                ),
            )

            sent += 1

        except Exception as e:

            print(
                "DISPATCH ERROR:",
                repr(e),
            )

    if sent == 0:

        await expire_search(
            order_id
        )

        return

    asyncio.create_task(
        expire_claim(
            order_id
        )
    )


# ============================================================
# SEARCH TIMEOUT
# ============================================================

async def expire_search(order_id):

    async with DB_LOCK:

        conn = db()

        row = conn.execute(
            """
            SELECT
                status,
                customer_id
            FROM orders
            WHERE id=?
            """,
            (order_id,),
        ).fetchone()

        if not row:

            conn.close()

            return

        if row["status"] == "SEARCHING":

            conn.execute(
                """
                UPDATE orders
                SET status='NO_DRIVER'
                WHERE id=?
                  AND status='SEARCHING'
                """,
                (order_id,),
            )

            conn.commit()

        conn.close()

    if row["status"] == "SEARCHING":

        try:

            await bot.send_message(
                row["customer_id"],
                "⚠️ Hozircha haydovchi topilmadi.\n\n"
                "Sizga hali ham taksi kerakmi?",
                reply_markup=customer_wait_kb(
                    order_id
                ),
            )

        except Exception:
            pass


async def expire_claim(order_id):

    await asyncio.sleep(
        CLAIM_SECONDS
    )

    await expire_search(
        order_id
    )


# ============================================================
# DRIVER REGISTRATION
# ============================================================

@dp.message(F.text == "🚕 Haydovchi")
async def driver_start(
    message: Message,
    state: FSMContext,
):

    uid = message.from_user.id

    if is_blocked(uid):

        await message.answer(
            "⛔ Akkauntingiz bloklangan."
        )

        return

    driver = get_driver(uid)

    if driver:

        await state.clear()

        await message.answer(
            "🚕 HAYDOVCHI PANELI\n\n"
            f"Holat: "
            f"{'🟢 ONLINE' if driver['online'] else '⚪ OFFLINE'}\n"
            f"Yo‘nalish: {driver['route']}\n"
            f"⭐ {driver['rating']:.2f}",
            reply_markup=driver_kb(
                bool(driver["online"])
            ),
        )

        return

    await state.clear()

    await state.set_state(
        DriverReg.name
    )

    await message.answer(
        "🚕 HAYDOVCHI RO‘YXATDAN O‘TISH\n\n"
        "👤 F.I.Sh.:"
    )


@dp.message(DriverReg.name)
async def dr_name(
    message: Message,
    state: FSMContext,
):

    name = (
        message.text or ""
    ).strip()

    if len(name) < 3:

        await message.answer(
            "⚠️ To‘liqroq ism kiriting."
        )

        return

    await state.update_data(
        name=name
    )

    await state.set_state(
        DriverReg.phone
    )

    await message.answer(
        "📱 Telefon raqamingiz:",
        reply_markup=phone_kb(),
    )


@dp.message(DriverReg.phone)
async def dr_phone(
    message: Message,
    state: FSMContext,
):

    phone = (
        message.contact.phone_number
        if message.contact
        else
        (message.text or "").strip()
    )

    if len(
        re.sub(r"\D", "", phone)
    ) < 7:

        await message.answer(
            "⚠️ Telefon raqami noto‘g‘ri."
        )

        return

    await state.update_data(
        phone=phone
    )

    await state.set_state(
        DriverReg.model
    )

    await message.answer(
        "🚗 Mashina modeli:\n\n"
        "Masalan: Chevrolet Cobalt"
    )


@dp.message(DriverReg.model)
async def dr_model(
    message: Message,
    state: FSMContext,
):

    model = (
        message.text or ""
    ).strip()

    if len(model) < 2:

        await message.answer(
            "⚠️ Mashina modelini kiriting."
        )

        return

    await state.update_data(
        model=model
    )

    await state.set_state(
        DriverReg.plate
    )

    await message.answer(
        "🔢 Davlat raqami:"
    )


@dp.message(DriverReg.plate)
async def dr_plate(
    message: Message,
    state: FSMContext,
):

    plate = re.sub(
        r"\s+",
        " ",
        (message.text or "")
        .upper()
        .strip(),
    )

    if len(plate) < 3:

        await message.answer(
            "⚠️ Davlat raqamini to‘g‘ri kiriting."
        )

        return

    conn = db()

    existing = conn.execute(
        """
        SELECT tg_id
        FROM drivers
        WHERE plate=?
        """,
        (plate,),
    ).fetchone()

    conn.close()

    if (
        existing
        and existing["tg_id"]
        != message.from_user.id
    ):

        await message.answer(
            "⛔ Bu avtomobil raqami "
            "allaqachon ro‘yxatdan o‘tgan."
        )

        return

    await state.update_data(
        plate=plate
    )

    await state.set_state(
        DriverReg.license
    )

    await message.answer(
        "🪪 Haydovchilik guvohnomasi "
        "rasmini yuboring."
    )


@dp.message(
    DriverReg.license,
    F.photo,
)
async def dr_license(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        license=message.photo[-1].file_id
    )

    await state.set_state(
        DriverReg.tech
    )

    await message.answer(
        "📄 Texpasport rasmini yuboring."
    )


@dp.message(DriverReg.license)
async def dr_license_bad(
    message: Message,
):

    await message.answer(
        "📷 Iltimos, guvohnomani "
        "rasm sifatida yuboring."
    )


@dp.message(
    DriverReg.tech,
    F.photo,
)
async def dr_tech(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        tech=message.photo[-1].file_id
    )

    await state.set_state(
        DriverReg.car
    )

    await message.answer(
        "🚗 Mashinaning tashqi "
        "rasmini yuboring."
    )


@dp.message(DriverReg.tech)
async def dr_tech_bad(
    message: Message,
):

    await message.answer(
        "📷 Iltimos, texpasportni "
        "rasm sifatida yuboring."
    )


@dp.message(
    DriverReg.car,
    F.photo,
)
async def dr_car(
    message: Message,
    state: FSMContext,
):

    await state.update_data(
        car=message.photo[-1].file_id
    )

    data = await state.get_data()

    ai_doc = await ai_check_driver_documents(
        data["license"],
        data["tech"],
        data["car"],
        data["name"],
        data["plate"],
        data["model"],
    )

    ensure_user(
        message.from_user.id,
        data["name"],
        data["phone"],
    )

    async with DB_LOCK:

        conn = db()

        conn.execute(
            """
            INSERT INTO drivers(
                tg_id,
                name,
                phone,
                car_model,
                plate,
                license_file_id,
                tech_file_id,
                car_photo_file_id,
                route,
                approved,
                online,
                active_orders,
                created_at
            )
            VALUES(
                ?,?,?,?,?,?,?,?,?,
                0,0,0,?
            )

            ON CONFLICT(tg_id)
            DO UPDATE SET
                name=excluded.name,
                phone=excluded.phone,
                car_model=excluded.car_model,
                plate=excluded.plate,
                license_file_id=excluded.license_file_id,
                tech_file_id=excluded.tech_file_id,
                car_photo_file_id=excluded.car_photo_file_id,
                route=excluded.route,
                approved=0,
                online=0
            """,
            (
                message.from_user.id,
                data["name"],
                data["phone"],
                data["model"],
                data["plate"],
                data["license"],
                data["tech"],
                data["car"],
                ROUTE,
                now(),
            ),
        )

        conn.commit()
        conn.close()

    await state.clear()

    await message.answer(
        "✅ Haydovchi arizasi qabul qilindi.\n\n"
        f"👤 {data['name']}\n"
        f"📱 {data['phone']}\n"
        f"🚗 {data['model']}\n"
        f"🔢 {data['plate']}\n"
        f"📍 {ROUTE}\n\n"
        f"🤖 AI risk: "
        f"{ai_doc.get('risk', '?')}\n\n"
        "👨‍💼 Yakuniy tasdiq admin tomonidan beriladi.",
        reply_markup=main_kb(),
    )

    if ADMIN_ID:

        try:

            await bot.send_message(
                ADMIN_ID,
                "🚕 YANGI HAYDOVCHI ARIZASI\n\n"
                f"👤 {data['name']}\n"
                f"📱 {data['phone']}\n"
                f"🚗 {data['model']}\n"
                f"🔢 {data['plate']}\n"
                f"📍 {ROUTE}\n\n"
                f"🤖 AI risk: "
                f"{ai_doc.get('risk', '?')}\n"
                f"🤖 Moslik: "
                f"{ai_doc.get('match', 'unknown')}\n"
                f"📝 {ai_doc.get('summary', '')}\n\n"
                f"Telegram ID: "
                f"{message.from_user.id}\n\n"
                f"/approve_driver "
                f"{message.from_user.id}\n"
                f"/reject_driver "
                f"{message.from_user.id}"
            )

            for label, file_id in [
                (
                    "🪪 GUVOHNOMA",
                    data["license"],
                ),
                (
                    "📄 TEX PASPORT",
                    data["tech"],
                ),
                (
                    "🚗 MASHINA",
                    data["car"],
                ),
            ]:

                try:

                    await bot.send_photo(
                        ADMIN_ID,
                        file_id,
                        caption=label,
                    )

                except Exception as e:

                    print(
                        "ADMIN PHOTO ERROR:",
                        repr(e),
                    )

        except Exception as e:

            print(
                "ADMIN NOTIFY ERROR:",
                repr(e),
            )


@dp.message(DriverReg.car)
async def dr_car_bad(
    message: Message,
):

    await message.answer(
        "📷 Iltimos, mashina rasmini yuboring."
    )


# ============================================================
# DRIVER ONLINE / OFFLINE
# ============================================================

@dp.message(F.text.in_({"🟢 ONLINE", "⚪ OFFLINE"}))
async def driver_toggle(
    message: Message,
):

    uid = message.from_user.id

    driver = get_driver(uid)

    if not driver:

        await message.answer(
            "Avval haydovchi sifatida ro‘yxatdan o‘ting."
        )

        return

    if not driver["approved"]:

        await message.answer(
            "⏳ Arizangiz hali admin tomonidan "
            "tasdiqlanmagan."
        )

        return

    if is_blocked(uid):

        await message.answer(
            "⛔ Akkauntingiz bloklangan."
        )

        return

    new_online = (
        0
        if driver["online"]
        else
        1
    )

    conn = db()

    conn.execute(
        """
        UPDATE drivers
        SET online=?
        WHERE tg_id=?
        """,
        (
            new_online,
            uid,
        ),
    )

    conn.commit()
    conn.close()

    await message.answer(
        (
            "🟢 ONLINE — buyurtmalarni qabul qilasiz."
            if new_online
            else
            "⚪ OFFLINE — buyurtma kelmaydi."
        ),
        reply_markup=driver_kb(
            bool(new_online)
        ),
    )


# ============================================================
# DRIVER NEW ORDERS
# ============================================================

@dp.message(F.text == "📋 Yangi buyurtmalar")
async def driver_new_orders(
    message: Message,
):

    driver = get_driver(
        message.from_user.id
    )

    if not driver:

        await message.answer(
            "Haydovchi profili topilmadi."
        )

        return

    if not driver["approved"]:

        await message.answer(
            "⏳ Avval admin tasdig'idan o'tishingiz kerak."
        )

        return

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM orders
        WHERE status='SEARCHING'
        ORDER BY id DESC
        LIMIT 10
        """
    ).fetchall()

    conn.close()

    if not rows:

        await message.answer(
            "📭 Hozir yangi buyurtma yo‘q.",
            reply_markup=driver_kb(
                bool(driver["online"])
            ),
        )

        return

    for order in rows:

        await message.answer(
            f"🚕 #{order['id']}\n"
            f"📍 {order['origin']} → "
            f"{order['destination']}\n"
            f"👥 {order['passengers']}\n"
            f"💰 {order['price']:,} so‘m",
            reply_markup=claim_kb(
                order["id"]
            ),
        )


# ============================================================
# DRIVER ACTIVE ORDERS
# ============================================================

@dp.message(F.text == "🚕 Faol buyurtmalar")
async def driver_active(
    message: Message,
):

    driver = get_driver(
        message.from_user.id
    )

    if not driver:

        await message.answer(
            "Haydovchi profili topilmadi."
        )

        return

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM orders
        WHERE driver_id=?
          AND status IN(
              'ACCEPTED',
              'CONTACTED',
              'ON_WAY',
              'PICKED_UP'
          )
        ORDER BY id DESC
        """,
        (
            message.from_user.id,
        ),
    ).fetchall()

    conn.close()

    if not rows:

        await message.answer(
            "🚕 Faol buyurtmalar yo‘q."
        )

        return

    for order in rows:

        await message.answer(
            f"🚕 #{order['id']}\n"
            f"📍 {order['origin']} → "
            f"{order['destination']}\n"
            f"💰 {order['price']:,} so‘m\n"
            f"📱 Mijoz: {order['customer_phone']}\n"
            f"Holat: {order['status']}",
            reply_markup=driver_order_kb(
                order["id"],
                order["status"],
            ),
        )


# ============================================================
# HISTORY
# ============================================================

@dp.message(F.text == "📜 Tarix")
@dp.message(F.text == "📜 Buyurtmalarim")
async def history(
    message: Message,
):

    uid = message.from_user.id

    driver = get_driver(uid)

    conn = db()

    if driver:

        rows = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE driver_id=?
            ORDER BY id DESC
            LIMIT 20
            """,
            (uid,),
        ).fetchall()

    else:

        rows = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE customer_id=?
            ORDER BY id DESC
            LIMIT 20
            """,
            (uid,),
        ).fetchall()

    conn.close()

    if not rows:

        await message.answer(
            "📭 Hali buyurtmalar yo‘q."
        )

        return

    lines = [
        "📜 BUYURTMALAR TARIXI\n"
    ]

    for order in rows:

        lines.append(
            f"#{order['id']} | "
            f"{order['origin']} → "
            f"{order['destination']} | "
            f"{order['price']:,} so‘m | "
            f"{order['status']}"
        )

    await message.answer(
        "\n".join(lines)
    )


# ============================================================
# DRIVER INCOME
# ============================================================

@dp.message(F.text == "💰 Daromad")
async def income(
    message: Message,
):

    driver = get_driver(
        message.from_user.id
    )

    if not driver:

        await message.answer(
            "Haydovchi profili topilmadi."
        )

        return

    conn = db()

    row = conn.execute(
        """
        SELECT
            COUNT(*) AS count_orders,
            COALESCE(SUM(price),0) AS total
        FROM orders
        WHERE driver_id=?
          AND status='COMPLETED'
        """,
        (
            message.from_user.id,
        ),
    ).fetchone()

    conn.close()

    await message.answer(
        "💰 DAROMAD\n\n"
        f"🚕 Yakunlangan: "
        f"{row['count_orders']}\n"
        f"💵 Jami: "
        f"{row['total']:,} so‘m"
    )


# ============================================================
# DRIVER RATING
# ============================================================

@dp.message(F.text == "⭐ Reyting")
async def rating(
    message: Message,
):

    driver = get_driver(
        message.from_user.id
    )

    if not driver:

        await message.answer(
            "⭐ Siz hali haydovchi emassiz."
        )

        return

    await message.answer(
        f"⭐ Reytingingiz: "
        f"{driver['rating']:.2f}\n"
        f"📊 Baholar: "
        f"{driver['rating_count']}"
    )


# ============================================================
# PROFILE
# ============================================================

@dp.message(F.text == "👤 Profil")
async def profile(
    message: Message,
):

    uid = message.from_user.id

    driver = get_driver(uid)

    if driver:

        await message.answer(
            "🚕 HAYDOVCHI PROFILI\n\n"
            f"👤 {driver['name']}\n"
            f"📱 {driver['phone']}\n"
            f"🚗 {driver['car_model']}\n"
            f"🔢 {driver['plate']}\n"
            f"📍 {driver['route']}\n"
            f"⭐ {driver['rating']:.2f}\n"
            f"🟢 "
            f"{'ONLINE' if driver['online'] else 'OFFLINE'}\n"
            f"✅ "
            f"{'TASDIQLANGAN' if driver['approved'] else 'KUTILMOQDA'}"
        )

        return

    user = get_user(uid)

    if user:

        await message.answer(
            "👤 PROFIL\n\n"
            f"Ism: {user['name']}\n"
            f"Telefon: {user['phone']}\n"
            f"Uy hududi: {user['home_area']}"
        )

        return

    await message.answer(
        "Avval /start orqali ro‘yxatdan o‘ting."
    )


# ============================================================
# CLAIM ORDER
# ============================================================

@dp.callback_query(F.data.startswith("claim:"))
async def claim_order(
    call: CallbackQuery,
):

    driver_id = call.from_user.id

    try:

        order_id = int(
            call.data.split(
                ":",
                1,
            )[1]
        )

    except Exception:

        await call.answer(
            "Noto‘g‘ri buyurtma.",
            show_alert=True,
        )

        return

    driver = get_driver(
        driver_id
    )

    if (
        not driver
        or not driver["approved"]
        or not driver["online"]
    ):

        await call.answer(
            "⛔ Siz hozir buyurtma "
            "ola olmaysiz.",
            show_alert=True,
        )

        return

    if is_blocked(driver_id):

        await call.answer(
            "⛔ Akkauntingiz bloklangan.",
            show_alert=True,
        )

        return

    async with DB_LOCK:

        conn = db()

        order = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,),
        ).fetchone()

        if not order:

            conn.close()

            await call.answer(
                "❌ Buyurtma topilmadi.",
                show_alert=True,
            )

            return

        if order["status"] != "SEARCHING":

            conn.close()

            await call.answer(
                "⚠️ Bu buyurtma "
                "allaqachon olingan.",
                show_alert=True,
            )

            return

        if order["origin"] is None:

            conn.close()

            await call.answer(
                "⚠️ Yo‘nalish xatosi.",
                show_alert=True,
            )

            return

        active = conn.execute(
            """
            SELECT COUNT(*) AS count_orders
            FROM orders
            WHERE driver_id=?
              AND status IN(
                  'ACCEPTED',
                  'CONTACTED',
                  'ON_WAY',
                  'PICKED_UP'
              )
            """,
            (driver_id,),
        ).fetchone()

        active_count = int(
            active["count_orders"]
        )

        if active_count >= MAX_ACTIVE_ORDERS:

            conn.close()

            await call.answer(
                "⚠️ Sizda 7 ta faol "
                "buyurtma bor.",
                show_alert=True,
            )

            return

        cursor = conn.execute(
            """
            UPDATE orders
            SET
                status='ACCEPTED',
                driver_id=?,
                claimed_at=?
            WHERE id=?
              AND status='SEARCHING'
            """,
            (
                driver_id,
                now(),
                order_id,
            ),
        )

        if cursor.rowcount != 1:

            conn.close()

            await call.answer(
                "⚠️ Buyurtmani boshqa "
                "haydovchi oldi.",
                show_alert=True,
            )

            return

        conn.execute(
            """
            UPDATE drivers
            SET active_orders=?
            WHERE tg_id=?
            """,
            (
                active_count + 1,
                driver_id,
            ),
        )

        conn.commit()
        conn.close()

    await call.answer(
        "✅ Buyurtma sizniki!"
    )

    try:

        await call.message.edit_reply_markup(
            reply_markup=None
        )

    except Exception:
        pass

    await call.message.answer(
        f"✅ BUYURTMA QABUL QILINDI #{order_id}\n\n"
        f"📍 {order['origin']} → "
        f"{order['destination']}\n"
        f"💰 {order['price']:,} so‘m\n\n"
        f"👤 Mijoz: "
        f"{order['customer_phone']}\n\n"
        "📞 Mijoz bilan bog‘laning.",
        reply_markup=driver_order_kb(
            order_id,
            "ACCEPTED",
        ),
    )

    try:

        await bot.send_message(
            order["customer_id"],
            f"🚕 HAYDOVCHI TOPILDI!\n\n"
            f"📍 {order['origin']} → "
            f"{order['destination']}\n"
            f"💰 {order['price']:,} so‘m\n\n"
            f"🚗 {driver['car_model']}\n"
            f"🔢 {driver['plate']}\n"
            f"⭐ {driver['rating']:.2f}\n\n"
            f"📱 Haydovchi: {driver['phone']}",
        )

    except Exception as e:

        print(
            "CUSTOMER NOTIFY ERROR:",
            repr(e),
        )

    log_event(
        driver_id,
        "order_claimed",
        order_id,
    )


# ============================================================
# DECLINE
# ============================================================

@dp.callback_query(F.data.startswith("decline:"))
async def decline_order(
    call: CallbackQuery,
):

    await call.answer(
        "Buyurtma rad etildi."
    )

    try:

        await call.message.edit_reply_markup(
            reply_markup=None
        )

    except Exception:
        pass


# ============================================================
# CONTACT CUSTOMER
# ============================================================

@dp.callback_query(F.data.startswith("contact:"))
async def contact_customer(
    call: CallbackQuery,
):

    try:

        order_id = int(
            call.data.split(
                ":",
                1,
            )[1]
        )

    except Exception:

        await call.answer(
            "Noto‘g‘ri buyurtma.",
            show_alert=True,
        )

        return

    conn = db()

    order = conn.execute(
        """
        SELECT *
        FROM orders
        WHERE id=?
        """,
        (order_id,),
    ).fetchone()

    if (
        not order
        or order["driver_id"]
        != call.from_user.id
    ):

        conn.close()

        await call.answer(
            "⛔ Ruxsat yo‘q.",
            show_alert=True,
        )

        return

    if order["status"] != "ACCEPTED":

        conn.close()

        await call.answer(
            "ℹ️ Bu buyurtma holati o‘zgargan.",
            show_alert=True,
        )

        return

    conn.execute(
        """
        UPDATE orders
        SET status='CONTACTED'
        WHERE id=?
          AND driver_id=?
          AND status='ACCEPTED'
        """,
        (
            order_id,
            call.from_user.id,
        ),
    )

    conn.commit()
    conn.close()

    await call.answer(
        "📞 Mijoz raqami ko‘rsatildi."
    )

    await call.message.answer(
        f"📞 Mijoz telefoni:\n"
        f"{order['customer_phone']}\n\n"
        "Aloqa qilgach, "
        "'YO‘LGA CHIQDIM' tugmasini bosing.",
        reply_markup=driver_order_kb(
            order_id,
            "CONTACTED",
        ),
    )


# ============================================================
# STATUS
# ============================================================

@dp.callback_query(F.data.startswith("status:"))
async def update_order_status(
    call: CallbackQuery,
):

    parts = call.data.split(":")

    if len(parts) != 3:

        await call.answer(
            "Noto‘g‘ri so‘rov.",
            show_alert=True,
        )

        return

    new_status = parts[1]

    try:

        order_id = int(parts[2])

    except Exception:

        await call.answer(
            "Noto‘g‘ri buyurtma.",
            show_alert=True,
        )

        return

    allowed = {

        "ON_WAY": {
            "ACCEPTED",
            "CONTACTED",
        },

        "PICKED_UP": {
            "ON_WAY",
        },
    }

    async with DB_LOCK:

        conn = db()

        order = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,),
        ).fetchone()

        if (
            not order
            or order["driver_id"]
            != call.from_user.id
            or order["status"]
            not in allowed.get(
                new_status,
                set(),
            )
        ):

            conn.close()

            await call.answer(
                "⛔ Statusni "
                "o‘zgartirib bo‘lmaydi.",
                show_alert=True,
            )

            return

        conn.execute(
            """
            UPDATE orders
            SET status=?
            WHERE id=?
            """,
            (
                new_status,
                order_id,
            ),
        )

        conn.commit()
        conn.close()

    await call.answer(
        "✅ Status yangilandi."
    )

    if new_status == "ON_WAY":

        text = (
            "🚗 Haydovchi yo‘lga chiqdi."
        )

        keyboard = driver_order_kb(
            order_id,
            "ON_WAY",
        )

    else:

        text = (
            "📍 Haydovchi sizni olib ketdi."
        )

        keyboard = driver_order_kb(
            order_id,
            "PICKED_UP",
        )

    await call.message.answer(
        text,
        reply_markup=keyboard,
    )

    try:

        await bot.send_message(
            order["customer_id"],
            f"🚕 #{order_id}: {text}",
        )

    except Exception:
        pass


# ============================================================
# COMPLETE
# ============================================================

@dp.callback_query(F.data.startswith("complete:"))
async def complete_order(
    call: CallbackQuery,
):

    try:

        order_id = int(
            call.data.split(
                ":",
                1,
            )[1]
        )

    except Exception:

        await call.answer(
            "Noto‘g‘ri buyurtma.",
            show_alert=True,
        )

        return

    async with DB_LOCK:

        conn = db()

        order = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,),
        ).fetchone()

        if (
            not order
            or order["driver_id"]
            != call.from_user.id
            or order["status"]
            != "PICKED_UP"
        ):

            conn.close()

            await call.answer(
                "⛔ Buyurtmani "
                "yakunlab bo‘lmaydi.",
                show_alert=True,
            )

            return

        conn.execute(
            """
            UPDATE orders
            SET
                status='COMPLETED',
                completed_at=?
            WHERE id=?
            """,
            (
                now(),
                order_id,
            ),
        )

        conn.execute(
            """
            UPDATE drivers
            SET active_orders=
                CASE
                    WHEN active_orders > 0
                    THEN active_orders - 1
                    ELSE 0
                END
            WHERE tg_id=?
            """,
            (
                call.from_user.id,
            ),
        )

        conn.commit()
        conn.close()

    await call.answer(
        "✅ Safar yakunlandi."
    )

    await call.message.answer(
        f"✅ #{order_id} buyurtma yakunlandi."
    )

    try:

        await bot.send_message(
            order["customer_id"],
            f"✅ Safaringiz yakunlandi.\n\n"
            f"#{order_id}\n\n"
            "⭐ Haydovchini baholang:",
            reply_markup=rating_kb(
                order_id
            ),
        )

    except Exception:
        pass

    log_event(
        call.from_user.id,
        "order_completed",
        order_id,
    )


# ============================================================
# DRIVER CANCEL
# ============================================================

@dp.callback_query(F.data.startswith("driver_cancel:"))
async def driver_cancel(
    call: CallbackQuery,
):

    try:

        order_id = int(
            call.data.split(
                ":",
                1,
            )[1]
        )

    except Exception:

        await call.answer(
            "Noto‘g‘ri buyurtma.",
            show_alert=True,
        )

        return

    async with DB_LOCK:

        conn = db()

        order = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,),
        ).fetchone()

        if (
            not order
            or order["driver_id"]
            != call.from_user.id
            or order["status"]
            not in ACTIVE_STATUSES
        ):

            conn.close()

            await call.answer(
                "⛔ Bekor qilib bo‘lmaydi.",
                show_alert=True,
            )

            return

        conn.execute(
            """
            UPDATE orders
            SET status='CANCELLED'
            WHERE id=?
            """,
            (order_id,),
        )

        conn.execute(
            """
            UPDATE drivers
            SET active_orders=
                CASE
                    WHEN active_orders > 0
                    THEN active_orders - 1
                    ELSE 0
                END
            WHERE tg_id=?
            """,
            (
                call.from_user.id,
            ),
        )

        conn.commit()
        conn.close()

    await call.answer(
        "❌ Buyurtma bekor qilindi."
    )

    await call.message.answer(
        "❌ Buyurtma bekor qilindi.",
        reply_markup=driver_kb(True),
    )

    try:

        await bot.send_message(
            order["customer_id"],
            "⚠️ Haydovchi buyurtmani bekor qildi.\n\n"
            "Yangi haydovchi qidirish uchun "
            "buyurtmani qayta berishingiz mumkin.",
            reply_markup=customer_wait_kb(
                order_id
            ),
        )

    except Exception:
        pass


# ============================================================
# RATING
# ============================================================

@dp.callback_query(F.data.startswith("rate:"))
async def rate_driver(
    call: CallbackQuery,
):

    parts = call.data.split(":")

    if len(parts) != 3:

        await call.answer(
            "Noto‘g‘ri baho.",
            show_alert=True,
        )

        return

    try:

        order_id = int(parts[1])
        score = int(parts[2])

    except Exception:

        await call.answer(
            "Noto‘g‘ri baho.",
            show_alert=True,
        )

        return

    if not 1 <= score <= 5:

        await call.answer(
            "Baho 1–5 oralig‘ida.",
            show_alert=True,
        )

        return

    async with DB_LOCK:

        conn = db()

        order = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,),
        ).fetchone()

        if (
            not order
            or order["customer_id"]
            != call.from_user.id
            or order["status"]
            != "COMPLETED"
            or not order["driver_id"]
        ):

            conn.close()

            await call.answer(
                "⛔ Bu buyurtmani "
                "baholab bo‘lmaydi.",
                show_alert=True,
            )

            return

        existing = conn.execute(
            """
            SELECT id
            FROM ratings
            WHERE order_id=?
            """,
            (order_id,),
        ).fetchone()

        if existing:

            conn.close()

            await call.answer(
                "Bu buyurtma "
                "allaqachon baholangan.",
                show_alert=True,
            )

            return

        driver = conn.execute(
            """
            SELECT
                rating,
                rating_count
            FROM drivers
            WHERE tg_id=?
            """,
            (
                order["driver_id"],
            ),
        ).fetchone()

        old_rating = float(
            driver["rating"]
            if driver
            else 5.0
        )

        old_count = int(
            driver["rating_count"]
            if driver
            else 0
        )

        new_rating = (
            (
                old_rating * old_count
            )
            + score
        ) / (
            old_count + 1
        )

        conn.execute(
            """
            INSERT INTO ratings(
                order_id,
                from_id,
                to_id,
                score,
                created_at
            )
            VALUES(?,?,?,?,?)
            """,
            (
                order_id,
                call.from_user.id,
                order["driver_id"],
                score,
                now(),
            ),
        )

        conn.execute(
            """
            UPDATE drivers
            SET
                rating=?,
                rating_count=?
            WHERE tg_id=?
            """,
            (
                new_rating,
                old_count + 1,
                order["driver_id"],
            ),
        )

        conn.commit()
        conn.close()

    await call.answer(
        "⭐ Rahmat!"
    )

    try:

        await call.message.edit_reply_markup(
            reply_markup=None
        )

    except Exception:
        pass

    await call.message.answer(
        f"⭐ Bahoyingiz: {score}/5\n"
        "Rahmat!"
    )


# ============================================================
# CUSTOMER WAIT
# ============================================================

@dp.callback_query(F.data.startswith("wait:"))
async def wait_more(
    call: CallbackQuery,
):

    await call.answer()

    try:

        order_id = int(
            call.data.split(
                ":",
                1,
            )[1]
        )

    except Exception:
        return

    async with DB_LOCK:

        conn = db()

        order = conn.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,),
        ).fetchone()

        if (
            not order
            or order["customer_id"]
            != call.from_user.id
        ):

            conn.close()
            return

        if order["status"] not in (
            "NO_DRIVER",
            "SEARCHING",
        ):

            conn.close()

            await call.message.answer(
                "ℹ️ Buyurtma holati "
                "allaqachon o‘zgargan."
            )

            return

        conn.execute(
            """
            UPDATE orders
            SET status='SEARCHING'
            WHERE id=?
              AND customer_id=?
            """,
            (
                order_id,
                call.from_user.id,
            ),
        )

        conn.commit()
        conn.close()

    await call.message.answer(
        "🟢 Qidiruv davom etmoqda..."
    )

    asyncio.create_task(
        dispatch_order(
            order_id
        )
    )


# ============================================================
# CUSTOMER CANCEL SEARCH
# ============================================================

@dp.callback_query(
    F.data.startswith("cancel_search:")
)
async def cancel_search(
    call: CallbackQuery,
):

    await call.answer()

    try:

        order_id = int(
            call.data.split(
                ":",
                1,
            )[1]
        )

    except Exception:
        return

    async with DB_LOCK:

        conn = db()

        cursor = conn.execute(
            """
            UPDATE orders
            SET status='CANCELLED'
            WHERE id=?
              AND customer_id=?
              AND status IN(
                  'NO_DRIVER',
                  'SEARCHING'
              )
            """,
            (
                order_id,
                call.from_user.id,
            ),
        )

        conn.commit()
        conn.close()

    if cursor.rowcount:

        await call.message.answer(
            "❌ Buyurtma bekor qilindi.",
            reply_markup=main_kb(),
        )


# ============================================================
# SUPPORT
# ============================================================

@dp.message(F.text == "📩 Murojaat")
async def support_start(
    message: Message,
    state: FSMContext,
):

    await state.set_state(
        SupportFlow.text
    )

    await message.answer(
        "📩 Taklif, shikoyat yoki "
        "murojaatingizni yozing."
    )


@dp.message(SupportFlow.text)
async def support_receive(
    message: Message,
    state: FSMContext,
):

    text = (
        message.text or ""
    ).strip()

    if len(text) < 3:

        await message.answer(
            "⚠️ Murojaat juda qisqa."
        )

        return

    ai_result = await ai_support(
        text
    )

    async with DB_LOCK:

        conn = db()

        cursor = conn.execute(
            """
            INSERT INTO support(
                tg_id,
                text,
                category,
                ai_json,
                created_at
            )
            VALUES(?,?,?,?,?)
            """,
            (
                message.from_user.id,
                text,
                ai_result["category"],
                json.dumps(
                    ai_result,
                    ensure_ascii=False,
                ),
                now(),
            ),
        )

        ticket = cursor.lastrowid

        conn.commit()
        conn.close()

    await state.clear()

    await message.answer(
        f"✅ Murojaatingiz qabul qilindi.\n\n"
        f"🎫 Ticket: MR-{ticket:06d}\n"
        f"📂 Kategoriya: "
        f"{ai_result['category']}\n"
        f"🤖 AI: "
        f"{ai_result.get('summary', '')}",
        reply_markup=main_kb(),
    )

    if ADMIN_ID:

        try:

            await bot.send_message(
                ADMIN_ID,
                f"📩 YANGI MUROJAAT "
                f"MR-{ticket:06d}\n\n"
                f"👤 Telegram ID: "
                f"{message.from_user.id}\n"
                f"📂 {ai_result['category']}\n"
                f"⚠️ Risk: "
                f"{ai_result.get('risk', 0)}\n\n"
                f"{text}",
            )

        except Exception as e:

            print(
                "SUPPORT ADMIN ERROR:",
                repr(e),
            )


# ============================================================
# ADMIN
# ============================================================

def is_admin(uid: int):

    return (
        ADMIN_ID != 0
        and uid == ADMIN_ID
    )


def parse_admin_id(
    message: Message,
):

    parts = (
        message.text or ""
    ).split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):

        return None

    return int(parts[1])


@dp.message(Command("admin"))
async def admin(
    message: Message,
):

    if not is_admin(
        message.from_user.id
    ):

        await message.answer(
            "⛔ Ruxsat yo‘q."
        )

        return

    conn = db()

    users = conn.execute(
        """
        SELECT COUNT(*) AS count_users
        FROM users
        WHERE role='customer'
        """
    ).fetchone()["count_users"]

    drivers = conn.execute(
        """
        SELECT COUNT(*) AS count_drivers
        FROM drivers
        """
    ).fetchone()["count_drivers"]

    approved = conn.execute(
        """
        SELECT COUNT(*) AS count_approved
        FROM drivers
        WHERE approved=1
        """
    ).fetchone()["count_approved"]

    online = conn.execute(
        """
        SELECT COUNT(*) AS count_online
        FROM drivers
        WHERE approved=1
          AND online=1
        """
    ).fetchone()["count_online"]

    orders = conn.execute(
        """
        SELECT COUNT(*) AS count_orders
        FROM orders
        """
    ).fetchone()["count_orders"]

    active = conn.execute(
        """
        SELECT COUNT(*) AS count_active
        FROM orders
        WHERE status IN(
            'SEARCHING',
            'ACCEPTED',
            'CONTACTED',
            'ON_WAY',
            'PICKED_UP'
        )
        """
    ).fetchone()["count_active"]

    conn.close()

    await message.answer(
        "👨‍💼 ADMIN PANEL\n\n"
        f"👤 Mijozlar: {users}\n"
        f"🚕 Haydovchilar: {drivers}\n"
        f"✅ Tasdiqlangan: {approved}\n"
        f"🟢 Online: {online}\n"
        f"📦 Buyurtmalar: {orders}\n"
        f"🔄 Faol: {active}\n\n"
        "Buyruqlar:\n"
        "/drivers\n"
        "/approve_driver ID\n"
        "/reject_driver ID\n"
        "/block ID\n"
        "/unblock ID\n"
        "/order ID\n"
        "/stats"
    )


@dp.message(Command("stats"))
async def admin_stats(
    message: Message,
):

    if not is_admin(
        message.from_user.id
    ):
        return

    conn = db()

    rows = conn.execute(
        """
        SELECT
            status,
            COUNT(*) AS count_orders
        FROM orders
        GROUP BY status
        ORDER BY count_orders DESC
        """
    ).fetchall()

    conn.close()

    if not rows:

        await message.answer(
            "📊 Hali buyurtmalar yo‘q."
        )

        return

    text = (
        "📊 BUYURTMA STATISTIKASI\n\n"
    )

    for row in rows:

        text += (
            f"{row['status']}: "
            f"{row['count_orders']}\n"
        )

    await message.answer(
        text
    )


@dp.message(Command("drivers"))
async def admin_drivers(
    message: Message,
):

    if not is_admin(
        message.from_user.id
    ):
        return

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM drivers
        ORDER BY created_at DESC
        LIMIT 50
        """
    ).fetchall()

    conn.close()

    if not rows:

        await message.answer(
            "Haydovchilar yo‘q."
        )

        return

    for driver in rows:

        await message.answer(
            f"🚕 {driver['name']}\n"
            f"ID: {driver['tg_id']}\n"
            f"🚗 {driver['car_model']}\n"
            f"🔢 {driver['plate']}\n"
            f"📍 {driver['route']}\n"
            f"Approved: {driver['approved']}\n"
            f"Online: {driver['online']}\n"
            f"⭐ {driver['rating']:.2f}\n\n"
            f"/approve_driver "
            f"{driver['tg_id']}\n"
            f"/reject_driver "
            f"{driver['tg_id']}"
        )


@dp.message(Command("approve_driver"))
async def approve_driver(
    message: Message,
):

    if not is_admin(
        message.from_user.id
    ):
        return

    tid = parse_admin_id(
        message
    )

    if tid is None:

        await message.answer(
            "Format:\n"
            "/approve_driver TELEGRAM_ID"
        )

        return

    async with DB_LOCK:

        conn = db()

        cursor = conn.execute(
            """
            UPDATE drivers
            SET
                approved=1,
                online=0
            WHERE tg_id=?
            """,
            (tid,),
        )

        conn.commit()
        conn.close()

    if cursor.rowcount == 0:

        await message.answer(
            "❌ Haydovchi topilmadi."
        )

        return

    await message.answer(
        "✅ Haydovchi tasdiqlandi."
    )

    try:

        await bot.send_message(
            tid,
            "✅ Haydovchi arizangiz "
            "tasdiqlandi!\n\n"
            "Endi ONLINE bo‘lib "
            "buyurtma olishingiz mumkin.",
        )

    except Exception:
        pass


@dp.message(Command("reject_driver"))
async def reject_driver(
    message: Message,
):

    if not is_admin(
        message.from_user.id
    ):
        return

    tid = parse_admin_id(
        message
    )

    if tid is None:

        await message.answer(
            "Format:\n"
            "/reject_driver TELEGRAM_ID"
        )

        return

    async with DB_LOCK:

        conn = db()

        cursor = conn.execute(
            """
            DELETE FROM drivers
            WHERE tg_id=?
            """,
            (tid,),
        )

        conn.commit()
        conn.close()

    await message.answer(
        (
            "❌ Haydovchi arizasi "
            "rad etildi."
            if cursor.rowcount
            else
            "❌ Haydovchi topilmadi."
        )
    )


@dp.message(Command("block"))
async def admin_block(
    message: Message,
):

    if not is_admin(
        message.from_user.id
    ):
        return

    tid = parse_admin_id(
        message
    )

    if tid is None:

        await message.answer(
            "Format:\n"
            "/block ID"
        )

        return

    ensure_user(tid)

    async with DB_LOCK:

        conn = db()

        conn.execute(
            """
            UPDATE users
            SET blocked=1
            WHERE tg_id=?
            """,
            (tid,),
        )

        conn.execute(
            """
            UPDATE drivers
            SET online=0
            WHERE tg_id=?
            """,
            (tid,),
        )

        conn.commit()
        conn.close()

    await message.answer(
        "⛔ Akkaunt bloklandi.\n"
        "Haydovchi bo‘lsa OFFLINE qilindi."
    )


@dp.message(Command("unblock"))
async def admin_unblock(
    message: Message,
):

    if not is_admin(
        message.from_user.id
    ):
        return

    tid = parse_admin_id(
        message
    )

    if tid is None:

        await message.answer(
            "Format:\n"
            "/unblock ID"
        )

        return

    ensure_user(tid)

    async with DB_LOCK:

        conn = db()

        conn.execute(
            """
            UPDATE users
            SET blocked=0
            WHERE tg_id=?
            """,
            (tid,),
        )

        conn.commit()
        conn.close()

    await message.answer(
        "✅ Blok olib tashlandi."
    )


@dp.message(Command("order"))
async def admin_order(
    message: Message,
):

    if not is_admin(
        message.from_user.id
    ):
        return

    order_id = parse_admin_id(
        message
    )

    if order_id is None:

        await message.answer(
            "Format:\n"
            "/order ORDER_ID"
        )

        return

    conn = db()

    order = conn.execute(
        """
        SELECT *
        FROM orders
        WHERE id=?
        """,
        (order_id,),
    ).fetchone()

    conn.close()

    if not order:

        await message.answer(
            "❌ Buyurtma topilmadi."
        )

        return

    await message.answer(
        f"📦 ORDER #{order['id']}\n\n"
        f"Status: {order['status']}\n"
        f"Customer: {order['customer_id']}\n"
        f"Driver: {order['driver_id']}\n"
        f"{order['origin']} → "
        f"{order['destination']}\n"
        f"💰 {order['price']:,} so‘m\n"
        f"Created: {order['created_at']}"
    )


# ============================================================
# ID
# ============================================================

@dp.message(Command("id"))
async def my_id(
    message: Message,
):

    await message.answer(
        f"Telegram ID: "
        f"{message.from_user.id}"
    )


# ============================================================
# CANCEL
# ============================================================

@dp.message(F.text == "❌ Bekor qilish")
async def generic_cancel(
    message: Message,
    state: FSMContext,
):

    await state.clear()

    await message.answer(
        "❌ Bekor qilindi.",
        reply_markup=main_kb(),
    )


# ============================================================
# HOME
# ============================================================

@dp.message(F.text == "🏠 Asosiy menyu")
async def home(
    message: Message,
    state: FSMContext,
):

    await state.clear()

    await message.answer(
        "🏠 Asosiy menyu",
        reply_markup=main_kb(),
    )


# ============================================================
# FALLBACK AI
# ============================================================

@dp.message()
async def fallback(
    message: Message,
    state: FSMContext,
):

    current_state = await state.get_state()

    if current_state:
        return

    if is_blocked(
        message.from_user.id
    ):

        await message.answer(
            "⛔ Akkauntingiz bloklangan."
        )

        return

    text = (
        message.text or ""
    ).strip()

    if not text:

        await message.answer(
            "Iltimos, matn yuboring."
        )

        return

    answer = await ai_text(
        f"""
Foydalanuvchi Telegram botda yozdi:

{text}

Juda qisqa va amaliy javob bering.

Agar taxi buyurtmasiga o'xshasa,
foydalanuvchidan:

Qayerdan → Qayerga

formatida manzil so'rang.

Asosiy yo'nalish:
OBLIQ ↔ ANGREN.
"""
    )

    await message.answer(
        answer
        or
        (
            "🤖 Sizga yordam beraman.\n\n"
            "🚕 Yo‘lovchi\n"
            "📦 Dastavka\n"
            "🚕 Haydovchi"
        ),
        reply_markup=main_kb(),
    )


# ============================================================
# ERROR HANDLER
# ============================================================

@dp.errors()
async def global_error_handler(
    event,
):

    print(
        "BOT ERROR:",
        repr(event.exception),
    )

    return True


# ============================================================
# STARTUP
# ============================================================

async def main():

    init_db()

    print(
        "===================================="
    )

    print(
        "TAXI BOR MI? — ALBATTA BOR!"
    )

    print(
        "Bot started successfully."
    )

    print(
        "AI:",
        "ON" if ai else "OFF",
    )

    print(
        "MODEL:",
        OPENAI_MODEL,
    )

    print(
        "ADMIN_ID:",
        ADMIN_ID,
    )

    print(
        "ROUTE:",
        ROUTE,
    )

    print(
        "===================================="
    )

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types(),
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        asyncio.run(
            main()
        )

    except KeyboardInterrupt:

        print(
            "Bot stopped."
        )
