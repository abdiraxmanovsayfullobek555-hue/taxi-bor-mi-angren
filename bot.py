import os
import re
import json
import sqlite3
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ContentType
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.exceptions import TelegramBadRequest

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None


# ============================================================
# CONFIG
# Railway Variables:
# BOT_TOKEN       = Telegram token
# OPENAI_API_KEY  = OpenAI API key (optional; AI parser has fallback)
# OPENAI_MODEL    = e.g. gpt-6-luna
# ADMIN_ID        = Telegram numeric ID of main admin
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN Railway Variables ichida topilmadi.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("taxi_bor_mi")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher(storage=MemoryStorage())

ai = AsyncOpenAI(api_key=OPENAI_API_KEY) if (OPENAI_API_KEY and AsyncOpenAI) else None


# ============================================================
# DATABASE
# ============================================================

DB_PATH = os.getenv("DB_PATH", "taxi_bor_mi.db")
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
db_lock = asyncio.Lock()


def init_db():
    db.executescript("""
    PRAGMA journal_mode=WAL;

    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER UNIQUE NOT NULL,
        role TEXT NOT NULL DEFAULT 'customer',
        lang TEXT NOT NULL DEFAULT 'uz',
        name TEXT DEFAULT '',
        phone TEXT DEFAULT '',
        home_area TEXT DEFAULT '',
        blocked INTEGER NOT NULL DEFAULT 0,
        warning_count INTEGER NOT NULL DEFAULT 0,
        risk_score INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS drivers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER UNIQUE NOT NULL,
        full_name TEXT NOT NULL,
        phone TEXT NOT NULL,
        car_model TEXT NOT NULL,
        plate TEXT UNIQUE NOT NULL,
        license_file_id TEXT DEFAULT '',
        tech_file_id TEXT DEFAULT '',
        car_photo_file_id TEXT DEFAULT '',
        route TEXT NOT NULL DEFAULT 'OBLIQ_ANGREN',
        approved INTEGER NOT NULL DEFAULT 0,
        online INTEGER NOT NULL DEFAULT 0,
        active_orders INTEGER NOT NULL DEFAULT 0,
        rating REAL NOT NULL DEFAULT 5.0,
        rating_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_tg_id INTEGER NOT NULL,
        driver_tg_id INTEGER,
        service TEXT NOT NULL,
        origin TEXT NOT NULL,
        destination TEXT NOT NULL,
        origin_lat REAL,
        origin_lon REAL,
        passengers INTEGER DEFAULT 1,
        price INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'SEARCHING',
        created_at TEXT NOT NULL,
        accepted_at TEXT,
        finished_at TEXT,
        claim_deadline TEXT
    );

    CREATE TABLE IF NOT EXISTS order_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        actor_tg_id INTEGER,
        event TEXT NOT NULL,
        details TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS ratings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        from_tg_id INTEGER NOT NULL,
        to_tg_id INTEGER NOT NULL,
        score INTEGER NOT NULL,
        comment TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE(order_id, from_tg_id)
    );

    CREATE TABLE IF NOT EXISTS complaints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER,
        reporter_tg_id INTEGER NOT NULL,
        target_tg_id INTEGER,
        category TEXT NOT NULL,
        text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'NEW',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS support_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_tg_id INTEGER,
        action TEXT NOT NULL,
        details TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_tg_id);
    CREATE INDEX IF NOT EXISTS idx_orders_driver ON orders(driver_tg_id);
    CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
    CREATE INDEX IF NOT EXISTS idx_drivers_online ON drivers(online, approved);
    """)
    db.commit()


def now():
    return datetime.utcnow().replace(microsecond=0).isoformat()


async def db_execute(sql, params=(), fetch=False, fetchone=False, commit=True):
    async with db_lock:
        cur = db.execute(sql, params)
        if commit:
            db.commit()
        if fetchone:
            return cur.fetchone()
        if fetch:
            return cur.fetchall()
        return cur.lastrowid


async def log_event(order_id, actor, event, details=""):
    await db_execute(
        "INSERT INTO order_events(order_id,actor_tg_id,event,details,created_at) VALUES(?,?,?,?,?)",
        (order_id, actor, event, details, now())
    )


async def audit(actor, action, details=""):
    await db_execute(
        "INSERT INTO audit_logs(actor_tg_id,action,details,created_at) VALUES(?,?,?,?)",
        (actor, action, details, now())
    )


# ============================================================
# TEXT / KEYBOARDS
# ============================================================

LANGS = {
    "uz": "🇺🇿 O‘zbekcha",
    "uzc": "🇺🇿 Ўзбекча",
    "ru": "🇷🇺 Русский",
    "en": "🇬🇧 English"
}

MAIN = {
    "uz": {
        "passenger": "👤 Yo‘lovchi",
        "delivery": "📦 Dastavka",
        "driver": "🚕 Haydovchi",
        "profile": "👤 Profil",
        "history": "📜 Tarix",
        "support": "📩 Murojaat",
        "back": "⬅️ Orqaga",
        "online": "🟢 ONLINE",
        "offline": "⚪ OFFLINE",
    },
    "uzc": {
        "passenger": "👤 Йўловчи",
        "delivery": "📦 Даставка",
        "driver": "🚕 Ҳайдовчи",
        "profile": "👤 Профиль",
        "history": "📜 Тарих",
        "support": "📩 Мурожаат",
        "back": "⬅️ Орқага",
        "online": "🟢 ONLINE",
        "offline": "⚪ OFFLINE",
    },
    "ru": {
        "passenger": "👤 Пассажир",
        "delivery": "📦 Доставка",
        "driver": "🚕 Водитель",
        "profile": "👤 Профиль",
        "history": "📜 История",
        "support": "📩 Поддержка",
        "back": "⬅️ Назад",
        "online": "🟢 ONLINE",
        "offline": "⚪ OFFLINE",
    },
    "en": {
        "passenger": "👤 Passenger",
        "delivery": "📦 Delivery",
        "driver": "🚕 Driver",
        "profile": "👤 Profile",
        "history": "📜 History",
        "support": "📩 Support",
        "back": "⬅️ Back",
        "online": "🟢 ONLINE",
        "offline": "⚪ OFFLINE",
    }
}


def main_kb(lang="uz"):
    t = MAIN.get(lang, MAIN["uz"])
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t["passenger"]), KeyboardButton(text=t["delivery"])],
            [KeyboardButton(text=t["driver"])],
            [KeyboardButton(text=t["profile"]), KeyboardButton(text=t["history"])],
            [KeyboardButton(text=t["support"])]
        ],
        resize_keyboard=True
    )


def language_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🇺🇿 O‘zbekcha"), KeyboardButton(text="🇺🇿 Ўзбекча")],
            [KeyboardButton(text="🇷🇺 Русский"), KeyboardButton(text="🇬🇧 English")]
        ],
        resize_keyboard=True
    )


def location_kb(lang="uz"):
    label = {
        "uz": "📍 Joylashuvni yuborish",
        "uzc": "📍 Жойлашувни юбориш",
        "ru": "📍 Отправить геолокацию",
        "en": "📍 Send location"
    }.get(lang, "📍 Joylashuvni yuborish")
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=label, request_location=True)]],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def confirm_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ BUYURTMA BERISH", callback_data="order_confirm")],
        [InlineKeyboardButton(text="✏️ O‘ZGARTIRISH", callback_data="order_edit"),
         InlineKeyboardButton(text="❌ BEKOR QILISH", callback_data="order_cancel")]
    ])


def driver_order_kb(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚕 BUYURTMANI OLISH", callback_data=f"claim:{order_id}")],
        [InlineKeyboardButton(text="❌ RAD ETISH", callback_data=f"decline:{order_id}")]
    ])


def driver_panel_kb(driver):
    online = bool(driver["online"])
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚪ OFFLINE" if online else "🟢 ONLINE")],
            [KeyboardButton(text="📋 YANGI BUYURTMALAR"),
             KeyboardButton(text="🚕 FAOL BUYURTMALAR")],
            [KeyboardButton(text="📜 BUYURTMALAR TARIXI"),
             KeyboardButton(text="💰 DAROMAD")],
            [KeyboardButton(text="⭐ REYTING"), KeyboardButton(text="👤 PROFIL")],
            [KeyboardButton(text="📩 MUROJAAT"), KeyboardButton(text="⬅️ Orqaga")]
        ],
        resize_keyboard=True
    )


# ============================================================
# FSM
# ============================================================

class Registration(StatesGroup):
    language = State()
    name = State()
    phone = State()
    home_area = State()


class OrderFlow(StatesGroup):
    gps = State()
    route = State()
    passengers = State()
    price = State()
    confirm = State()


class DriverReg(StatesGroup):
    name = State()
    phone = State()
    car_model = State()
    plate = State()
    license = State()
    tech = State()
    car_photo = State()
    rules = State()


class SupportFlow(StatesGroup):
    text = State()


class ComplaintFlow(StatesGroup):
    text = State()


# ============================================================
# AI
# ============================================================

async def ai_json(system, user):
    if not ai:
        return None

    try:
        response = await ai.responses.create(
            model=OPENAI_MODEL,
            instructions=system,
            input=user
        )
        text = getattr(response, "output_text", "") or ""
        text = text.strip()
        text = re.sub(r"^```json\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
        return json.loads(text)
    except Exception as e:
        log.warning("AI error: %s", e)
        return None


def deterministic_route(text):
    s = text.strip()
    low = s.lower().replace("→", " ").replace("—", " ")
    low = re.sub(r"\s+", " ", low)

    # Common aliases / route vocabulary.
    aliases = {
        "obliq": "Obliq",
        "oblik": "Obliq",
        "облик": "Obliq",
        "облиқ": "Obliq",
        "angren": "Angren",
        "ангрен": "Angren",
        "hokimiyat": "Hokimiyat",
        "hokimiyati": "Hokimiyat",
        "hokimiyatga": "Hokimiyat",
        "ҳокимият": "Hokimiyat",
        "хокимият": "Hokimiyat",
    }

    # "Obliqdan Hokimiyatga", "Obliqdan Hokimiyatga boraman"
    m = re.search(r"(.+?)\s*dan\s+(.+?)\s*ga(?:\s+bor.*)?$", low, re.I)
    if m:
        o = aliases.get(m.group(1).strip(), m.group(1).strip())
        d = aliases.get(m.group(2).strip(), m.group(2).strip())
        return {"origin": o.title(), "destination": d.title(), "confidence": 0.95}

    # "Obliqdan Hokimiyat"
    m = re.search(r"(.+?)\s*dan\s+(.+?)(?:ga)?$", low, re.I)
    if m and len(m.group(1)) > 1:
        o = aliases.get(m.group(1).strip(), m.group(1).strip())
        d = aliases.get(m.group(2).strip(), m.group(2).strip())
        return {"origin": o.title(), "destination": d.title(), "confidence": 0.85}

    # Arrow form.
    parts = re.split(r"\s*(?:->|→|—|-)\s*", s)
    if len(parts) == 2 and all(parts):
        return {
            "origin": parts[0].strip().title(),
            "destination": parts[1].strip().title(),
            "confidence": 0.9
        }

    # Simple two-location form.
    words = low.split()
    known = []
    for w in words:
        w2 = re.sub(r"[^\wа-яёқғўҳʼ']", "", w)
        if w2 in aliases:
            known.append(aliases[w2])
    if len(known) >= 2:
        return {"origin": known[0], "destination": known[1], "confidence": 0.8}

    return None


async def parse_route(text):
    result = await ai_json(
        """You are the route parser for TAXI BOR MI? — ALBATTA BOR!, an Uzbek intercity taxi bot.
Return ONLY JSON:
{"origin":"...","destination":"...","confidence":0.0}
Understand Uzbek Latin, Uzbek Cyrillic, Russian and simple English.
Examples:
"Obliqdan Hokimiyatga" => origin Obliq, destination Hokimiyat
"Obliq → Hokimiyat" => same
"Men Obliqdan Hokimiyatga boraman" => same
Never invent a location. If uncertain, return empty strings and low confidence.""",
        text
    )
    if result and result.get("origin") and result.get("destination"):
        return result
    return deterministic_route(text)


async def ai_validate_registration(data):
    result = await ai_json(
        """You validate a taxi-driver registration for a local transport platform.
Return only JSON:
{"ok":true,"reason":"...","risk":0}
Do not claim a document is authentic with certainty. Only check obvious missing,
inconsistent, malformed or suspicious information from the supplied text.
Admin makes the final decision.""",
        json.dumps(data, ensure_ascii=False)
    )
    return result or {"ok": True, "reason": "AI unavailable; admin review required.", "risk": 0}


# ============================================================
# HELPERS
# ============================================================

async def get_user(tg_id):
    return await db_execute("SELECT * FROM users WHERE tg_id=?", (tg_id,), fetchone=True)


async def get_driver(tg_id):
    return await db_execute("SELECT * FROM drivers WHERE tg_id=?", (tg_id,), fetchone=True)


async def is_blocked(tg_id):
    u = await get_user(tg_id)
    if u and u["blocked"]:
        return True
    d = await get_driver(tg_id)
    return bool(d and u and u["blocked"])


async def ensure_user(message):
    u = await get_user(message.from_user.id)
    if not u:
        return None
    if u["blocked"]:
        await message.answer("🚫 Akkauntingiz vaqtincha bloklangan.")
        return None
    return u


def money(n):
    return f"{int(n):,}".replace(",", " ") + " so‘m"


async def eligible_drivers():
    return await db_execute(
        """SELECT * FROM drivers
           WHERE approved=1 AND online=1 AND route='OBLIQ_ANGREN'
           AND active_orders < 7""",
        fetch=True
    )


async def send_admin(text):
    if ADMIN_ID:
        try:
            await bot.send_message(ADMIN_ID, text)
        except Exception as e:
            log.warning("Admin message error: %s", e)


# ============================================================
# START / LANGUAGE / REGISTRATION
# ============================================================

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    u = await get_user(message.from_user.id)
    if u:
        if u["blocked"]:
            await message.answer("🚫 Akkauntingiz bloklangan.")
            return
        await message.answer(
            "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
            "📍 <b>OBLIQ ↔ ANGREN</b>\n"
            "👤 Yo‘lovchi • 📦 Dastavka\n\n"
            "Xizmatni tanlang:",
            reply_markup=main_kb(u["lang"])
        )
        return

    await state.clear()
    await state.set_state(Registration.language)
    await message.answer(
        "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
        "Tilni tanlang / Выберите язык / Choose language:",
        reply_markup=language_kb()
    )


@dp.message(Registration.language)
async def registration_language(message: Message, state: FSMContext):
    mapping = {
        "🇺🇿 O‘zbekcha": "uz",
        "🇺🇿 Ўзбекча": "uzc",
        "🇷🇺 Русский": "ru",
        "🇬🇧 English": "en"
    }
    lang = mapping.get(message.text)
    if not lang:
        await message.answer("Iltimos, tildan birini tanlang.", reply_markup=language_kb())
        return

    await state.update_data(lang=lang)
    await state.set_state(Registration.name)
    await message.answer("👤 Ism-familiyangizni kiriting:")


@dp.message(Registration.name)
async def registration_name(message: Message, state: FSMContext):
    name = (message.text or "").strip()
    if len(name) < 2:
        await message.answer("❗ Ism-familiya juda qisqa.")
        return
    await state.update_data(name=name)
    await state.set_state(Registration.phone)
    await message.answer(
        "📱 Telefon raqamingizni yuboring:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📱 Raqamni yuborish", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )


@dp.message(Registration.phone, F.contact)
async def registration_phone(message: Message, state: FSMContext):
    phone = message.contact.phone_number
    await state.update_data(phone=phone)
    await state.set_state(Registration.home_area)
    await message.answer(
        "🏘 Uy/mahallangizni yozing.\n"
        "Masalan: <b>Obliq</b>"
    )


@dp.message(Registration.phone)
async def registration_phone_text(message: Message, state: FSMContext):
    phone = (message.text or "").strip()
    if len(re.sub(r"\D", "", phone)) < 7:
        await message.answer("📱 To‘g‘ri telefon raqam yuboring.")
        return
    await state.update_data(phone=phone)
    await state.set_state(Registration.home_area)
    await message.answer("🏘 Uy/mahallangizni yozing. Masalan: <b>Obliq</b>")


@dp.message(Registration.home_area)
async def registration_home(message: Message, state: FSMContext):
    area = (message.text or "").strip()
    if len(area) < 2:
        await message.answer("🏘 Mahalla nomini yozing.")
        return

    data = await state.get_data()
    await db_execute(
        """INSERT INTO users(tg_id,role,lang,name,phone,home_area,created_at)
           VALUES(?,?,?,?,?,?,?)""",
        (message.from_user.id, "customer", data.get("lang", "uz"),
         data.get("name", ""), data.get("phone", ""), area, now())
    )
    await state.clear()
    await message.answer(
        "✅ <b>Ro‘yxatdan o‘tish yakunlandi!</b>\n\n"
        "🚕 TAXI BOR MI? — ALBATTA BOR!\n"
        "📍 OBLIQ ↔ ANGREN\n\n"
        "Xizmatni tanlang:",
        reply_markup=main_kb(data.get("lang", "uz"))
    )


# ============================================================
# CUSTOMER: SERVICE SELECTION
# ============================================================

async def begin_order(message: Message, state: FSMContext, service):
    u = await ensure_user(message)
    if not u:
        return
    await state.clear()
    await state.update_data(service=service, lang=u["lang"])
    await state.set_state(OrderFlow.gps)
    await message.answer(
        "📍 <b>Avval joylashuvingizni yuboring.</b>\n\n"
        "Keyin: <b>Qayerdan → Qayerga</b> manzilini yozasiz.",
        reply_markup=location_kb(u["lang"])
    )


@dp.message(F.text.in_({
    "👤 Yo‘lovchi", "👤 Йўловчи", "👤 Пассажир", "👤 Passenger"
}))
async def passenger_start(message: Message, state: FSMContext):
    await begin_order(message, state, "PASSENGER")


@dp.message(F.text.in_({
    "📦 Dastavka", "📦 Даставка", "📦 Доставка", "📦 Delivery"
}))
async def delivery_start(message: Message, state: FSMContext):
    await begin_order(message, state, "DELIVERY")


@dp.message(OrderFlow.gps, F.location)
async def order_gps(message: Message, state: FSMContext):
    await state.update_data(
        lat=message.location.latitude,
        lon=message.location.longitude
    )
    await state.set_state(OrderFlow.route)
    await message.answer(
        "📍 <b>Joylashuvingiz qabul qilindi.</b>\n\n"
        "Endi safar manzilini kiriting:\n"
        "📍 <b>Qayerdan → 🏁 Qayerga</b>\n\n"
        "Masalan: <b>Obliqdan Hokimiyatga</b>"
    )


@dp.message(OrderFlow.gps)
async def order_gps_text(message: Message, state: FSMContext):
    await message.answer(
        "📍 Iltimos, pastdagi <b>Joylashuvni yuborish</b> tugmasi orqali GPS yuboring.",
        reply_markup=location_kb()
    )


@dp.message(OrderFlow.route)
async def order_route(message: Message, state: FSMContext):
    parsed = await parse_route((message.text or "").strip())
    if not parsed or not parsed.get("origin") or not parsed.get("destination"):
        await message.answer(
            "❗ Manzilni tushunmadim.\n\n"
            "Masalan:\n"
            "• <b>Obliqdan Hokimiyatga</b>\n"
            "• <b>Obliq → Hokimiyat</b>\n"
            "• <b>Men Obliqdan Hokimiyatga boraman</b>"
        )
        return

    if float(parsed.get("confidence", 0)) < 0.5:
        await message.answer(
            f"🤖 Men quyidagicha tushundim:\n"
            f"📍 {parsed['origin']} → 🏁 {parsed['destination']}\n\n"
            "Iltimos, yana aniqroq yozing."
        )
        return

    await state.update_data(
        origin=parsed["origin"],
        destination=parsed["destination"]
    )
    data = await state.get_data()

    if data["service"] == "PASSENGER":
        await state.set_state(OrderFlow.passengers)
        kb = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=str(i)) for i in range(1, 5)],
                       [KeyboardButton(text=str(i)) for i in range(5, 8)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await message.answer("👥 Necha kishi?", reply_markup=kb)
    else:
        await state.set_state(OrderFlow.price)
        await message.answer("💰 Dastavka uchun narxni so‘mda kiriting:", reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="⬅️ Orqaga")]],
            resize_keyboard=True
        ))


@dp.message(OrderFlow.passengers)
async def order_passengers(message: Message, state: FSMContext):
    try:
        n = int((message.text or "").strip())
    except ValueError:
        n = 0
    if n < 1 or n > 7:
        await message.answer("❗ 1 dan 7 gacha son kiriting.")
        return
    await state.update_data(passengers=n)
    await state.set_state(OrderFlow.price)
    await message.answer("💰 Safar narxini so‘mda kiriting. Masalan: <b>30000</b>")


@dp.message(OrderFlow.price)
async def order_price(message: Message, state: FSMContext):
    raw = re.sub(r"[^\d]", "", message.text or "")
    if not raw:
        await message.answer("💰 Narxni raqamda kiriting. Masalan: 30000")
        return
    price = int(raw)
    if price <= 0 or price > 100_000_000:
        await message.answer("❗ Narx noto‘g‘ri.")
        return

    await state.update_data(price=price)
    await state.set_state(OrderFlow.confirm)
    data = await state.get_data()

    passengers = data.get("passengers", 1) if data["service"] == "PASSENGER" else None
    text = (
        "🚕 <b>BUYURTMA</b>\n\n"
        f"📍 Qayerdan: <b>{data['origin']}</b>\n"
        f"🏁 Qayerga: <b>{data['destination']}</b>\n"
    )
    if passengers:
        text += f"👥 {passengers} kishi\n"
    text += f"💰 <b>{money(price)}</b>\n"
    if data["service"] == "DELIVERY":
        text += "📦 Xizmat: <b>Dastavka</b>\n"

    await message.answer(text, reply_markup=confirm_kb())


@dp.callback_query(F.data == "order_edit")
async def order_edit(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.set_state(OrderFlow.route)
    await callback.message.answer(
        "✏️ Manzilni qayta kiriting:\n"
        "Masalan: <b>Obliqdan Hokimiyatga</b>"
    )


@dp.callback_query(F.data == "order_cancel")
async def order_cancel(callback: CallbackQuery, state: FSMContext):
    await callback.answer("Bekor qilindi")
    await state.clear()
    await callback.message.edit_text("❌ Buyurtma bekor qilindi.")


@dp.callback_query(F.data == "order_confirm")
async def order_confirm(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if not data.get("origin") or not data.get("destination") or not data.get("price"):
        await callback.answer("Buyurtma ma'lumotlari to‘liq emas.", show_alert=True)
        return

    deadline = datetime.utcnow() + timedelta(minutes=2)
    order_id = await db_execute(
        """INSERT INTO orders(
           customer_tg_id,service,origin,destination,origin_lat,origin_lon,
           passengers,price,status,created_at,claim_deadline)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            callback.from_user.id,
            data["service"],
            data["origin"],
            data["destination"],
            data.get("lat"),
            data.get("lon"),
            data.get("passengers", 1),
            data["price"],
            "SEARCHING",
            now(),
            deadline.isoformat()
        )
    )
    await log_event(order_id, callback.from_user.id, "ORDER_CREATED")
    await audit(callback.from_user.id, "ORDER_CREATED", str(order_id))
    await state.clear()

    await callback.answer("Buyurtma qabul qilindi!")
    await callback.message.edit_text(
        f"🔎 <b>Buyurtma #{order_id}</b>\n\n"
        f"📍 {data['origin']} → 🏁 {data['destination']}\n"
        f"💰 {money(data['price'])}\n\n"
        "🚕 Haydovchi qidirilmoqda..."
    )
    asyncio.create_task(dispatch_order(order_id))


# ============================================================
# DISPATCH / CLAIM
# ============================================================

async def dispatch_order(order_id):
    await asyncio.sleep(1)
    order = await db_execute("SELECT * FROM orders WHERE id=?", (order_id,), fetchone=True)
    if not order or order["status"] != "SEARCHING":
        return

    drivers = await eligible_drivers()
    if not drivers:
        await db_execute(
            "UPDATE orders SET status='NO_DRIVER' WHERE id=? AND status='SEARCHING'",
            (order_id,)
        )
        await log_event(order_id, 0, "NO_DRIVER")
        try:
            await bot.send_message(
                order["customer_tg_id"],
                f"⚠️ <b>Buyurtma #{order_id}</b>\n\n"
                "Hozircha haydovchi topilmadi.\n"
                "🟢 HA, KUTAMAN — buyurtma qidiruvda qoladi\n"
                "❌ /cancel — bekor qilish"
            )
        except Exception:
            pass
        return

    text = (
        f"🚕 <b>YANGI BUYURTMA #{order_id}</b>\n\n"
        f"📍 {order['origin']}\n"
        f"🏁 {order['destination']}\n"
        f"👥 {order['passengers']} kishi\n"
        f"💰 <b>{money(order['price'])}</b>\n\n"
        "⏱ Buyurtmani qabul qilish imkoniyati mavjud."
    )

    sent = 0
    for d in drivers:
        try:
            await bot.send_message(d["tg_id"], text, reply_markup=driver_order_kb(order_id))
            sent += 1
        except Exception as e:
            log.warning("Dispatch %s -> %s: %s", order_id, d["tg_id"], e)

    await log_event(order_id, 0, "DISPATCHED", f"drivers={sent}")

    await asyncio.sleep(120)
    await db_execute(
        "UPDATE orders SET status='EXPIRED' WHERE id=? AND status='SEARCHING'",
        (order_id,)
    )
    await log_event(order_id, 0, "EXPIRED")

    try:
        await bot.send_message(
            order["customer_tg_id"],
            f"⚠️ <b>Buyurtma #{order_id}</b> uchun haydovchi topilmadi.\n\n"
            "Agar hali ham taksi kerak bo‘lsa, yangi buyurtma berishingiz mumkin."
        )
    except Exception:
        pass


@dp.callback_query(F.data.startswith("claim:"))
async def claim_order(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])
    driver = await get_driver(callback.from_user.id)

    if not driver or not driver["approved"]:
        await callback.answer("Haydovchi tasdiqlanmagan.", show_alert=True)
        return

    if not driver["online"]:
        await callback.answer("Avval ONLINE bo‘ling.", show_alert=True)
        return

    if driver["active_orders"] >= 7:
        await callback.answer("Sizda 7 ta faol buyurtma bor.", show_alert=True)
        return

    async with db_lock:
        cur = db.execute(
            """UPDATE orders
               SET driver_tg_id=?, status='ACCEPTED', accepted_at=?
               WHERE id=? AND status='SEARCHING'""",
            (callback.from_user.id, now(), order_id)
        )
        if cur.rowcount != 1:
            db.commit()
            await callback.answer(
                "⚠️ Bu buyurtma boshqa haydovchi tomonidan qabul qilindi.",
                show_alert=True
            )
            return

        db.execute(
            "UPDATE drivers SET active_orders=active_orders+1 WHERE tg_id=?",
            (callback.from_user.id,)
        )
        db.commit()

    await log_event(order_id, callback.from_user.id, "ORDER_ACCEPTED")
    await audit(callback.from_user.id, "ORDER_ACCEPTED", str(order_id))

    order = await db_execute(
        "SELECT * FROM orders WHERE id=?",
        (order_id,),
        fetchone=True
    )
    customer = await get_user(order["customer_tg_id"])

    await callback.answer("🚕 Buyurtma sizga biriktirildi!")
    await callback.message.edit_text(
        f"✅ <b>BUYURTMA QABUL QILINDI #{order_id}</b>\n\n"
        f"📍 {order['origin']}\n"
        f"🏁 {order['destination']}\n"
        f"💰 {money(order['price'])}\n\n"
        f"👤 Mijoz: <b>{customer['name'] if customer else 'Mijoz'}</b>\n"
        f"📞 {customer['phone'] if customer else '—'}"
    )

    try:
        await bot.send_message(
            order["customer_tg_id"],
            f"🎉 <b>Haydovchi topildi!</b>\n\n"
            f"🚕 Haydovchi: <b>{driver['full_name']}</b>\n"
            f"🚗 Avtomobil: <b>{driver['car_model']}</b>\n"
            f"🔢 Raqam: <b>{driver['plate']}</b>\n"
            f"📞 Telefon: <b>{driver['phone']}</b>\n\n"
            f"💰 Narx: <b>{money(order['price'])}</b>\n"
            f"📍 {order['origin']} → 🏁 {order['destination']}\n\n"
            "Haydovchi bilan bog‘lanishingiz mumkin."
        )
    except Exception:
        pass


@dp.callback_query(F.data.startswith("decline:"))
async def decline_order(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])
    await callback.answer("Buyurtma rad etildi.")
    await callback.message.edit_reply_markup(reply_markup=None)
    await log_event(order_id, callback.from_user.id, "ORDER_DECLINED")


# ============================================================
# DRIVER REGISTRATION
# ============================================================

@dp.message(F.text.in_({
    "🚕 Haydovchi", "🚕 Ҳайдовчи", "🚕 Водитель", "🚕 Driver"
}))
async def driver_start(message: Message, state: FSMContext):
    u = await get_user(message.from_user.id)
    d = await get_driver(message.from_user.id)

    if d:
        if d["approved"]:
            await message.answer(
                "🚕 <b>HAYDOVCHI PANELI</b>\n\n"
                "Yo‘nalish: <b>OBLIQ ↔ ANGREN</b>",
                reply_markup=driver_panel_kb(d)
            )
        else:
            await message.answer(
                "⏳ Haydovchi arizangiz hali admin tomonidan ko‘rib chiqilmoqda."
            )
        return

    await state.clear()
    await state.set_state(DriverReg.name)
    await message.answer(
        "🚕 <b>Haydovchi ro‘yxatdan o‘tishi</b>\n\n"
        "F.I.Sh. ni kiriting:"
    )


@dp.message(DriverReg.name)
async def driver_name(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if len(value) < 3:
        await message.answer("❗ F.I.Sh. ni to‘liq kiriting.")
        return

    await state.update_data(full_name=value)
    await state.set_state(DriverReg.phone)
    await message.answer(
        "📞 Telefon raqamingizni yuboring:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📱 Raqamni yuborish", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )


@dp.message(DriverReg.phone, F.contact)
async def driver_phone(message: Message, state: FSMContext):
    await state.update_data(phone=message.contact.phone_number)
    await state.set_state(DriverReg.car_model)
    await message.answer("🚗 Avtomobil rusumi va modelini yozing. Masalan: <b>Cobalt</b>")


@dp.message(DriverReg.phone)
async def driver_phone_text(message: Message, state: FSMContext):
    phone = (message.text or "").strip()
    if len(re.sub(r"\D", "", phone)) < 7:
        await message.answer("📞 To‘g‘ri telefon raqam kiriting.")
        return

    await state.update_data(phone=phone)
    await state.set_state(DriverReg.car_model)
    await message.answer("🚗 Avtomobil rusumi va modelini yozing. Masalan: <b>Cobalt</b>")


@dp.message(DriverReg.car_model)
async def driver_car_model(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if len(value) < 2:
        await message.answer("🚗 Avtomobil modelini to‘g‘ri kiriting.")
        return

    await state.update_data(car_model=value)
    await state.set_state(DriverReg.plate)
    await message.answer("🔢 Davlat raqamingizni kiriting. Masalan: <b>01 A 123 BC</b>")


@dp.message(DriverReg.plate)
async def driver_plate(message: Message, state: FSMContext):
    v = re.sub(r"\s+", " ", (message.text or "").strip().upper())
    if len(v) < 3:
        await message.answer("Davlat raqamini to‘g‘ri kiriting.")
        return

    exists = await db_execute(
        "SELECT tg_id FROM drivers WHERE UPPER(plate)=UPPER(?)",
        (v,), fetchone=True
    )
    if exists:
        await message.answer("🚫 Bu avtomobil allaqachon ro‘yxatdan o‘tgan.")
        return

    await state.update_data(plate=v)
    await state.set_state(DriverReg.license)
    await message.answer("🪪 Haydovchilik guvohnomangiz rasmini yuboring.")


@dp.message(DriverReg.license, F.photo)
async def driver_license(message: Message, state: FSMContext):
    await state.update_data(license_file_id=message.photo[-1].file_id)
    await state.set_state(DriverReg.tech)
    await message.answer("📄 Texpasport rasmini yuboring.")


@dp.message(DriverReg.license)
async def driver_license_bad(message: Message):
    await message.answer("🪪 Iltimos, haydovchilik guvohnomasining rasmini yuboring.")


@dp.message(DriverReg.tech, F.photo)
async def driver_tech(message: Message, state: FSMContext):
    await state.update_data(tech_file_id=message.photo[-1].file_id)
    await state.set_state(DriverReg.car_photo)
    await message.answer("🚗 Avtomobilingizning tashqi rasmini yuboring.")


@dp.message(DriverReg.tech)
async def driver_tech_bad(message: Message):
    await message.answer("📄 Iltimos, texpasport rasmini yuboring.")


@dp.message(DriverReg.car_photo, F.photo)
async def driver_car_photo(message: Message, state: FSMContext):
    await state.update_data(car_photo_file_id=message.photo[-1].file_id)
    await state.set_state(DriverReg.rules)
    await message.answer(
        "📋 <b>Qoidalar</b>\n\n"
        "1. Buyurtmani halol bajarish.\n"
        "2. Mijozga hurmat bilan muomala qilish.\n"
        "3. Narxni qabul qilgandan keyin o‘zboshimchalik bilan o‘zgartirmaslik.\n"
        "4. Telefon raqamini tarqatmaslik.\n"
        "5. Noto‘g‘ri ma’lumot bermaslik.\n\n"
        "Qabul qilasizmi?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ QABUL QILAMAN", callback_data="driver_rules_yes")],
            [InlineKeyboardButton(text="❌ RAD ETAMAN", callback_data="driver_rules_no")]
        ])
    )


@dp.message(DriverReg.car_photo)
async def driver_car_photo_bad(message: Message):
    await message.answer("🚗 Iltimos, avtomobil rasmini yuboring.")


@dp.callback_query(F.data == "driver_rules_no")
async def driver_rules_no(callback: CallbackQuery, state: FSMContext):
    await callback.answer()
    await state.clear()
    await callback.message.edit_text("❌ Ro‘yxatdan o‘tish bekor qilindi.")


@dp.callback_query(F.data == "driver_rules_yes")
async def driver_rules_yes(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()

    ai_check = await ai_validate_registration({
        "full_name": data.get("full_name"),
        "phone": data.get("phone"),
        "car_model": data.get("car_model"),
        "plate": data.get("plate"),
        "route": "OBLIQ_ANGREN"
    })

    await db_execute(
        """INSERT INTO drivers(
           tg_id,full_name,phone,car_model,plate,
           license_file_id,tech_file_id,car_photo_file_id,
           route,approved,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (
            callback.from_user.id, data["full_name"], data["phone"],
            data["car_model"], data["plate"],
            data["license_file_id"], data["tech_file_id"],
            data["car_photo_file_id"], "OBLIQ_ANGREN", 0, now()
        )
    )
    await audit(callback.from_user.id, "DRIVER_REGISTERED", data["plate"])
    await state.clear()
    await callback.answer("Ariza yuborildi!")
    await callback.message.edit_text(
        "✅ <b>Haydovchi arizasi qabul qilindi.</b>\n\n"
        "👨‍💼 Admin hujjatlarni tekshiradi va tasdiqlaydi.\n"
        f"🤖 AI tekshiruvi: {'mos' if ai_check.get('ok') else 'shubhali'}\n"
        f"⚠️ Izoh: {ai_check.get('reason','')}"
    )

    if ADMIN_ID:
        await bot.send_message(
            ADMIN_ID,
            f"🚕 <b>YANGI HAYDOVCHI ARIZASI</b>\n\n"
            f"👤 {data['full_name']}\n"
            f"📞 {data['phone']}\n"
            f"🚗 {data['car_model']}\n"
            f"🔢 {data['plate']}\n"
            f"🤖 AI: {ai_check.get('reason','')}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="✅ TASDIQLASH",
                    callback_data=f"approve_driver:{callback.from_user.id}"
                )],
                [InlineKeyboardButton(
                    text="❌ RAD ETISH",
                    callback_data=f"reject_driver:{callback.from_user.id}"
                )]
            ])
        )


@dp.callback_query(F.data.startswith("approve_driver:"))
async def approve_driver(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    tg_id = int(callback.data.split(":")[1])
    await db_execute("UPDATE drivers SET approved=1 WHERE tg_id=?", (tg_id,))
    await audit(ADMIN_ID, "DRIVER_APPROVED", str(tg_id))
    await callback.answer("Tasdiqlandi")
    await callback.message.edit_text("✅ Haydovchi tasdiqlandi.")

    d = await get_driver(tg_id)

    await bot.send_message(
        tg_id,
        "🎉 <b>Siz tasdiqlandingiz!</b>\n\n"
        "Endi 🚕 Haydovchi bo‘limidan ONLINE bo‘lib buyurtma qabul qilishingiz mumkin.",
        reply_markup=driver_panel_kb(d)
    )


@dp.callback_query(F.data.startswith("reject_driver:"))
async def reject_driver(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    tg_id = int(callback.data.split(":")[1])
    d = await get_driver(tg_id)

    await db_execute(
        "DELETE FROM drivers WHERE tg_id=?",
        (tg_id,)
    )
    await audit(ADMIN_ID, "DRIVER_REJECTED", str(tg_id))
    await callback.answer("Rad etildi")
    await callback.message.edit_text("❌ Haydovchi arizasi rad etildi.")

    if d:
        await bot.send_message(
            tg_id,
            "❌ Haydovchi arizangiz rad etildi."
        )


# ============================================================
# DRIVER PANEL
# ============================================================

@dp.message(F.text.in_({"🟢 ONLINE", "⚪ OFFLINE"}))
async def driver_toggle(message: Message):
    d = await get_driver(message.from_user.id)

    if not d:
        await message.answer("Avval haydovchi sifatida ro‘yxatdan o‘ting.")
        return

    if not d["approved"]:
        await message.answer("⏳ Admin tasdiqlashi kerak.")
        return

    new_value = 0 if d["online"] else 1

    await db_execute(
        "UPDATE drivers SET online=? WHERE tg_id=?",
        (new_value, message.from_user.id)
    )

    d = await get_driver(message.from_user.id)

    await message.answer(
        "🟢 Siz ONLINE bo‘ldingiz." if new_value else "⚪ Siz OFFLINE bo‘ldingiz.",
        reply_markup=driver_panel_kb(d)
    )


@dp.message(F.text == "📋 YANGI BUYURTMALAR")
async def driver_new_orders(message: Message):
    d = await get_driver(message.from_user.id)

    if not d or not d["approved"]:
        await message.answer("🚫 Haydovchi paneli mavjud emas.")
        return

    rows = await db_execute(
        """SELECT * FROM orders
           WHERE status='SEARCHING'
           ORDER BY id DESC
           LIMIT 20""",
        fetch=True
    )

    if not rows:
        await message.answer("📭 Hozircha yangi buyurtmalar yo‘q.")
        return

    for o in rows:
        await message.answer(
            f"🚕 <b>BUYURTMA #{o['id']}</b>\n\n"
            f"📍 {o['origin']}\n"
            f"🏁 {o['destination']}\n"
            f"👥 {o['passengers']} kishi\n"
            f"💰 {money(o['price'])}",
            reply_markup=driver_order_kb(o["id"])
        )


@dp.message(F.text == "🚕 FAOL BUYURTMALAR")
async def driver_active_orders(message: Message):
    d = await get_driver(message.from_user.id)

    if not d or not d["approved"]:
        await message.answer("🚫 Haydovchi paneli mavjud emas.")
        return

    rows = await db_execute(
        """SELECT * FROM orders
           WHERE driver_tg_id=?
           AND status IN ('ACCEPTED','CONTACTED','ON_WAY','CUSTOMER_PICKED')
           ORDER BY id DESC""",
        (message.from_user.id,),
        fetch=True
    )

    if not rows:
        await message.answer("📭 Faol buyurtmalar yo‘q.")
        return

    for o in rows:
        await message.answer(
            f"🚕 <b>FAOL BUYURTMA #{o['id']}</b>\n\n"
            f"📍 {o['origin']}\n"
            f"🏁 {o['destination']}\n"
            f"💰 {money(o['price'])}\n"
            f"📌 Holat: <b>{o['status']}</b>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(
                    text="📞 MIJOZGA QO‘NG‘IROQ",
                    url=f"tg://user?id={o['customer_tg_id']}"
                )],
                [InlineKeyboardButton(
                    text="✅ YAKUNLASH",
                    callback_data=f"finish:{o['id']}"
                )]
            ])
        )


@dp.callback_query(F.data.startswith("finish:"))
async def finish_order(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])

    order = await db_execute(
        "SELECT * FROM orders WHERE id=?",
        (order_id,),
        fetchone=True
    )

    if not order:
        await callback.answer("Buyurtma topilmadi.", show_alert=True)
        return

    if order["driver_tg_id"] != callback.from_user.id:
        await callback.answer("Bu buyurtma sizniki emas.", show_alert=True)
        return

    if order["status"] not in (
        "ACCEPTED",
        "CONTACTED",
        "ON_WAY",
        "CUSTOMER_PICKED"
    ):
        await callback.answer("Buyurtmani yakunlab bo‘lmaydi.", show_alert=True)
        return

    await db_execute(
        """UPDATE orders
           SET status='COMPLETED', finished_at=?
           WHERE id=?""",
        (now(), order_id)
    )

    await db_execute(
        """UPDATE drivers
           SET active_orders=CASE
               WHEN active_orders>0 THEN active_orders-1
               ELSE 0
           END
           WHERE tg_id=?""",
        (callback.from_user.id,)
    )

    await log_event(
        order_id,
        callback.from_user.id,
        "ORDER_COMPLETED"
    )

    await audit(
        callback.from_user.id,
        "ORDER_COMPLETED",
        str(order_id)
    )

    await callback.answer("Buyurtma yakunlandi.")
    await callback.message.edit_text(
        f"✅ <b>BUYURTMA #{order_id} YAKUNLANDI</b>"
    )

    try:
        await bot.send_message(
            order["customer_tg_id"],
            f"✅ <b>Buyurtma #{order_id} yakunlandi.</b>\n\n"
            "⭐ Haydovchini baholashingiz mumkin.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⭐ 1",
                        callback_data=f"rate:{order_id}:1"
                    ),
                    InlineKeyboardButton(
                        text="⭐ 2",
                        callback_data=f"rate:{order_id}:2"
                    ),
                    InlineKeyboardButton(
                        text="⭐ 3",
                        callback_data=f"rate:{order_id}:3"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⭐ 4",
                        callback_data=f"rate:{order_id}:4"
                    ),
                    InlineKeyboardButton(
                        text="⭐ 5",
                        callback_data=f"rate:{order_id}:5"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⚠️ SHIKOYAT",
                        callback_data=f"complaint:{order_id}"
                    )
                ]
            ])
        )
    except Exception:
        pass


@dp.message(F.text == "📜 BUYURTMALAR TARIXI")
async def driver_history(message: Message):
    d = await get_driver(message.from_user.id)

    if not d:
        await message.answer("🚫 Haydovchi profili topilmadi.")
        return

    rows = await db_execute(
        """SELECT * FROM orders
           WHERE driver_tg_id=?
           ORDER BY id DESC
           LIMIT 20""",
        (message.from_user.id,),
        fetch=True
    )

    if not rows:
        await message.answer("📭 Buyurtmalar tarixi bo‘sh.")
        return

    text = "📜 <b>BUYURTMALAR TARIXI</b>\n\n"

    for o in rows:
        text += (
            f"#{o['id']} | "
            f"{o['origin']} → {o['destination']}\n"
            f"💰 {money(o['price'])} | "
            f"{o['status']}\n\n"
        )

    await message.answer(text)


@dp.message(F.text == "💰 DAROMAD")
async def driver_income(message: Message):
    d = await get_driver(message.from_user.id)

    if not d:
        await message.answer("🚫 Haydovchi profili topilmadi.")
        return

    row = await db_execute(
        """SELECT COALESCE(SUM(price),0) AS total,
                  COUNT(*) AS count
           FROM orders
           WHERE driver_tg_id=?
           AND status='COMPLETED'""",
        (message.from_user.id,),
        fetchone=True
    )

    await message.answer(
        "💰 <b>DAROMAD</b>\n\n"
        f"🚕 Yakunlangan buyurtmalar: <b>{row['count']}</b>\n"
        f"💵 Jami: <b>{money(row['total'])}</b>"
    )


@dp.message(F.text == "⭐ REYTING")
async def driver_rating(message: Message):
    d = await get_driver(message.from_user.id)

    if not d:
        await message.answer("🚫 Haydovchi profili topilmadi.")
        return

    await message.answer(
        "⭐ <b>REYTING</b>\n\n"
        f"⭐ Reyting: <b>{d['rating']:.2f}</b>\n"
        f"👥 Baholar soni: <b>{d['rating_count']}</b>"
    )


@dp.message(F.text == "👤 PROFIL")
async def driver_profile(message: Message):
    d = await get_driver(message.from_user.id)

    if not d:
        await message.answer("🚫 Haydovchi profili topilmadi.")
        return

    status = "🟢 ONLINE" if d["online"] else "⚪ OFFLINE"

    await message.answer(
        "👤 <b>HAYDOVCHI PROFILI</b>\n\n"
        f"👤 F.I.Sh.: <b>{d['full_name']}</b>\n"
        f"📞 Telefon: <b>{d['phone']}</b>\n"
        f"🚗 Avtomobil: <b>{d['car_model']}</b>\n"
        f"🔢 Raqam: <b>{d['plate']}</b>\n"
        f"📍 Yo‘nalish: <b>OBLIQ ↔ ANGREN</b>\n"
        f"📌 Holat: <b>{status}</b>\n"
        f"⭐ Reyting: <b>{d['rating']:.2f}</b>\n"
        f"🚕 Faol buyurtmalar: <b>{d['active_orders']}/7</b>"
    )


@dp.message(F.text == "⬅️ Orqaga")
async def back_to_main(message: Message, state: FSMContext):
    await state.clear()

    u = await get_user(message.from_user.id)

    if u:
        await message.answer(
            "🏠 <b>ASOSIY MENYU</b>",
            reply_markup=main_kb(u["lang"])
        )
    else:
        await message.answer(
            "🏠 Asosiy menyu.",
            reply_markup=language_kb()
        )


# ============================================================
# CUSTOMER PROFILE / HISTORY
# ============================================================

@dp.message(F.text.in_({
    "👤 Profil",
    "👤 Профиль",
    "👤 Profile"
}))
async def customer_profile(message: Message):
    u = await get_user(message.from_user.id)

    if not u:
        await message.answer(
            "Avval /start orqali ro‘yxatdan o‘ting."
        )
        return

    await message.answer(
        "👤 <b>PROFIL</b>\n\n"
        f"👤 Ism: <b>{u['name']}</b>\n"
        f"📞 Telefon: <b>{u['phone']}</b>\n"
        f"🏘 Uy/mahalla: <b>{u['home_area']}</b>\n"
        f"🌐 Til: <b>{LANGS.get(u['lang'], 'O‘zbekcha')}</b>"
    )


@dp.message(F.text.in_({
    "📜 Tarix",
    "📜 Тарих",
    "📜 История",
    "📜 History"
}))
async def customer_history(message: Message):
    rows = await db_execute(
        "SELECT * FROM orders WHERE customer_tg_id=? ORDER BY id DESC LIMIT 20",
        (message.from_user.id,),
        fetch=True
    )

    if not rows:
        await message.answer("📭 Buyurtmalar tarixi bo‘sh.")
        return

    text = "📜 <b>BUYURTMALAR TARIXI</b>\n\n"

    for o in rows:
        text += (
            f"#{o['id']} | {o['origin']} → {o['destination']}\n"
            f"💰 {money(o['price'])} | {o['status']}\n\n"
        )

    await message.answer(text)


# ============================================================
# SUPPORT / COMPLAINT
# ============================================================

@dp.message(F.text.in_({
    "📩 Murojaat",
    "📩 Мурожаат",
    "📩 Поддержка",
    "📩 Support"
}))
async def support_start(message: Message, state: FSMContext):
    await state.set_state(SupportFlow.text)

    await message.answer(
        "📩 Murojaat yoki taklifingizni yozing:"
    )


@dp.message(SupportFlow.text)
async def support_save(message: Message, state: FSMContext):
    text = (message.text or "").strip()

    if len(text) < 3:
        await message.answer(
            "Iltimos, batafsilroq yozing."
        )
        return

    tid = await db_execute(
        "INSERT INTO support_tickets(tg_id,text,created_at) VALUES(?,?,?)",
        (
            message.from_user.id,
            text,
            now()
        )
    )

    await state.clear()

    await message.answer(
        f"✅ Murojaatingiz qabul qilindi.\n"
        f"🎫 Ticket: <b>MR-{tid:06d}</b>"
    )

    await send_admin(
        f"📩 <b>YANGI MUROJAAT MR-{tid:06d}</b>\n\n"
        f"👤 {message.from_user.id}\n"
        f"{text}"
    )


@dp.callback_query(F.data.startswith("complaint:"))
async def complaint_start(
    callback: CallbackQuery,
    state: FSMContext
):
    oid = int(callback.data.split(":")[1])

    await state.update_data(order_id=oid)
    await state.set_state(ComplaintFlow.text)

    await callback.answer()

    await callback.message.answer(
        "⚠️ Muammo/shikoyatni yozing.\n"
        "AI va admin ko‘rib chiqadi."
    )


@dp.message(ComplaintFlow.text)
async def complaint_save(
    message: Message,
    state: FSMContext
):
    data = await state.get_data()
    oid = data.get("order_id")
    text = (message.text or "").strip()

    o = await db_execute(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    if not o:
        await state.clear()
        await message.answer(
            "Buyurtma topilmadi."
        )
        return

    if len(text) < 5:
        await message.answer(
            "Iltimos, muammoni batafsilroq yozing."
        )
        return

    target = (
        o["driver_tg_id"]
        if message.from_user.id == o["customer_tg_id"]
        else o["customer_tg_id"]
    )

    cid = await db_execute(
        """INSERT INTO complaints(
           order_id,
           reporter_tg_id,
           target_tg_id,
           category,
           text,
           created_at
        )
        VALUES(?,?,?,?,?,?)""",
        (
            oid,
            message.from_user.id,
            target,
            "GENERAL",
            text,
            now()
        )
    )

    await state.clear()

    await audit(
        message.from_user.id,
        "COMPLAINT_CREATED",
        f"complaint={cid};order={oid}"
    )

    await message.answer(
        f"✅ Shikoyatingiz qabul qilindi.\n"
        f"🎫 Shikoyat raqami: <b>SH-{cid:06d}</b>\n\n"
        "Admin ko‘rib chiqadi."
    )

    await send_admin(
        f"⚠️ <b>YANGI SHIKOYAT SH-{cid:06d}</b>\n\n"
        f"🚕 Buyurtma: #{oid}\n"
        f"👤 Reporter: {message.from_user.id}\n"
        f"🎯 Target: {target}\n\n"
        f"{text}"
    )


# ============================================================
# RATINGS
# ============================================================

@dp.callback_query(F.data.startswith("rate:"))
async def rate_driver(callback: CallbackQuery):
    parts = callback.data.split(":")

    if len(parts) != 3:
        await callback.answer("Noto‘g‘ri so‘rov.", show_alert=True)
        return

    order_id = int(parts[1])
    score = int(parts[2])

    if score < 1 or score > 5:
        await callback.answer("Noto‘g‘ri baho.", show_alert=True)
        return

    order = await db_execute(
        "SELECT * FROM orders WHERE id=?",
        (order_id,),
        fetchone=True
    )

    if not order:
        await callback.answer(
            "Buyurtma topilmadi.",
            show_alert=True
        )
        return

    if order["customer_tg_id"] != callback.from_user.id:
        await callback.answer(
            "Bu buyurtmani baholash huquqingiz yo‘q.",
            show_alert=True
        )
        return

    if order["status"] != "COMPLETED":
        await callback.answer(
            "Buyurtma hali yakunlanmagan.",
            show_alert=True
        )
        return

    if not order["driver_tg_id"]:
        await callback.answer(
            "Haydovchi topilmadi.",
            show_alert=True
        )
        return

    exists = await db_execute(
        """SELECT id FROM ratings
           WHERE order_id=? AND from_tg_id=?""",
        (
            order_id,
            callback.from_user.id
        ),
        fetchone=True
    )

    if exists:
        await callback.answer(
            "Siz allaqachon baholagansiz.",
            show_alert=True
        )
        return

    await db_execute(
        """INSERT INTO ratings(
           order_id,
           from_tg_id,
           to_tg_id,
           score,
           created_at
        )
        VALUES(?,?,?,?,?)""",
        (
            order_id,
            callback.from_user.id,
            order["driver_tg_id"],
            score,
            now()
        )
    )

    row = await db_execute(
        """SELECT
             COALESCE(SUM(score),0) AS total,
             COUNT(*) AS count
           FROM ratings
           WHERE to_tg_id=?""",
        (order["driver_tg_id"],),
        fetchone=True
    )

    rating = (
        float(row["total"]) / float(row["count"])
        if row["count"]
        else 5.0
    )

    await db_execute(
        """UPDATE drivers
           SET rating=?,
               rating_count=?
           WHERE tg_id=?""",
        (
            rating,
            row["count"],
            order["driver_tg_id"]
        )
    )

    await audit(
        callback.from_user.id,
        "DRIVER_RATED",
        f"order={order_id};score={score}"
    )

    await callback.answer(
        "⭐ Baho qabul qilindi!"
    )

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.message.answer(
        f"⭐ Rahmat! Siz haydovchiga <b>{score}/5</b> baho berdingiz."
    )


# ============================================================
# CANCEL COMMAND
# ============================================================

@dp.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext):
    await state.clear()

    u = await get_user(message.from_user.id)

    if u:
        await message.answer(
            "❌ Joriy amal bekor qilindi.",
            reply_markup=main_kb(u["lang"])
        )
    else:
        await message.answer(
            "❌ Joriy amal bekor qilindi."
        )


# ============================================================
# ADMIN COMMANDS
# ============================================================

def admin_only(message: Message):
    return ADMIN_ID and message.from_user.id == ADMIN_ID


@dp.message(Command("stats"))
async def admin_stats(message: Message):
    if not admin_only(message):
        await message.answer("🚫 Ruxsat yo‘q.")
        return

    users = await db_execute(
        "SELECT COUNT(*) AS c FROM users",
        fetchone=True
    )

    drivers = await db_execute(
        "SELECT COUNT(*) AS c FROM drivers",
        fetchone=True
    )

    approved = await db_execute(
        "SELECT COUNT(*) AS c FROM drivers WHERE approved=1",
        fetchone=True
    )

    online = await db_execute(
        "SELECT COUNT(*) AS c FROM drivers WHERE approved=1 AND online=1",
        fetchone=True
    )

    orders = await db_execute(
        "SELECT COUNT(*) AS c FROM orders",
        fetchone=True
    )

    completed = await db_execute(
        "SELECT COUNT(*) AS c FROM orders WHERE status='COMPLETED'",
        fetchone=True
    )

    searching = await db_execute(
        "SELECT COUNT(*) AS c FROM orders WHERE status='SEARCHING'",
        fetchone=True
    )

    await message.answer(
        "📊 <b>TAXI BOR MI? — ADMIN STATISTIKA</b>\n\n"
        f"👥 Foydalanuvchilar: <b>{users['c']}</b>\n"
        f"🚕 Haydovchilar: <b>{drivers['c']}</b>\n"
        f"✅ Tasdiqlangan: <b>{approved['c']}</b>\n"
        f"🟢 ONLINE: <b>{online['c']}</b>\n"
        f"📦 Jami buyurtmalar: <b>{orders['c']}</b>\n"
        f"✅ Yakunlangan: <b>{completed['c']}</b>\n"
        f"🔎 Qidiruvda: <b>{searching['c']}</b>"
    )


@dp.message(Command("drivers"))
async def admin_drivers(message: Message):
    if not admin_only(message):
        await message.answer("🚫 Ruxsat yo‘q.")
        return

    rows = await db_execute(
        """SELECT * FROM drivers
           ORDER BY id DESC
           LIMIT 50""",
        fetch=True
    )

    if not rows:
        await message.answer(
            "📭 Haydovchilar yo‘q."
        )
        return

    for d in rows:
        status = (
            "🟢 ONLINE"
            if d["online"]
            else "⚪ OFFLINE"
        )

        approved = (
            "✅"
            if d["approved"]
            else "⏳"
        )

        await message.answer(
            f"🚕 <b>Haydovchi #{d['id']}</b>\n\n"
            f"{approved} {d['full_name']}\n"
            f"📞 {d['phone']}\n"
            f"🚗 {d['car_model']}\n"
            f"🔢 {d['plate']}\n"
            f"📍 OBLIQ ↔ ANGREN\n"
            f"📌 {status}\n"
            f"⭐ {d['rating']:.2f}\n"
            f"🚕 Faol: {d['active_orders']}/7"
        )


@dp.message(Command("orders"))
async def admin_orders(message: Message):
    if not admin_only(message):
        await message.answer("🚫 Ruxsat yo‘q.")
        return

    rows = await db_execute(
        """SELECT * FROM orders
           ORDER BY id DESC
           LIMIT 50""",
        fetch=True
    )

    if not rows:
        await message.answer(
            "📭 Buyurtmalar yo‘q."
        )
        return

    text = "📋 <b>SO‘NGGI BUYURTMALAR</b>\n\n"

    for o in rows:
        text += (
            f"#{o['id']} | "
            f"{o['origin']} → {o['destination']}\n"
            f"💰 {money(o['price'])}\n"
            f"📌 {o['status']}\n"
            f"👤 {o['customer_tg_id']}\n"
            f"🚕 {o['driver_tg_id'] or '—'}\n\n"
        )

    await message.answer(text)


@dp.message(Command("block"))
async def admin_block(message: Message):
    if not admin_only(message):
        await message.answer("🚫 Ruxsat yo‘q.")
        return

    parts = (message.text or "").split()

    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer(
            "Foydalanish:\n"
            "<code>/block TELEGRAM_ID</code>"
        )
        return

    tg_id = int(parts[1])

    await db_execute(
        "UPDATE users SET blocked=1 WHERE tg_id=?",
        (tg_id,)
    )

    await db_execute(
        "UPDATE drivers SET online=0 WHERE tg_id=?",
        (tg_id,)
    )

    await audit(
        ADMIN_ID,
        "USER_BLOCKED",
        str(tg_id)
    )

    await message.answer(
        f"🚫 <b>{tg_id}</b> bloklandi."
    )

    try:
        await bot.send_message(
            tg_id,
            "🚫 Sizning TAXI BOR MI? akkauntingiz bloklandi."
        )
    except Exception:
        pass


@dp.message(Command("unblock"))
async def admin_unblock(message: Message):
    if not admin_only(message):
        await message.answer("🚫 Ruxsat yo‘q.")
        return

    parts = (message.text or "").split()

    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer(
            "Foydalanish:\n"
            "<code>/unblock TELEGRAM_ID</code>"
        )
        return

    tg_id = int(parts[1])

    await db_execute(
        "UPDATE users SET blocked=0 WHERE tg_id=?",
        (tg_id,)
    )

    await audit(
        ADMIN_ID,
        "USER_UNBLOCKED",
        str(tg_id)
    )

    await message.answer(
        f"✅ <b>{tg_id}</b> blokdan chiqarildi."
    )


# ============================================================
# FALLBACK
# ============================================================

@dp.message()
async def fallback(message: Message):
    u = await get_user(message.from_user.id)

    if u:
        await message.answer(
            "🤖 Buyruqni tushunmadim.\n\n"
            "Quyidagi menyudan foydalaning:",
            reply_markup=main_kb(u["lang"])
        )
    else:
        await message.answer(
            "🚕 TAXI BOR MI? botidan foydalanish uchun "
            "/start ni bosing."
        )


# ============================================================
# STARTUP
# ============================================================

async def main():
    init_db()

    me = await bot.get_me()

    log.info(
        "Bot started: @%s",
        me.username
    )

    await dp.start_polling(
        bot,
        allowed_updates=dp.resolve_used_update_types()
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        log.info("Bot stopped.") 
import os
import re
import json
import sqlite3
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ContentType
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)
from aiogram.exceptions import TelegramBadRequest

try:
    from openai import AsyncOpenAI
except ImportError:
    AsyncOpenAI = None


# ============================================================
# CONFIG
# Railway Variables:
# BOT_TOKEN       = Telegram token
# OPENAI_API_KEY  = OpenAI API key (optional; AI parser has fallback)
# OPENAI_MODEL    = e.g. gpt-6-luna
# ADMIN_ID        = Telegram numeric ID of main admin
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-6-luna").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN Railway Variables ichida topilmadi.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("taxi_bor_mi")

bot = Bot(
    token=BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher(storage=MemoryStorage())

ai = (
    AsyncOpenAI(api_key=OPENAI_API_KEY)
    if (OPENAI_API_KEY and AsyncOpenAI)
    else None
)


# ============================================================
# DATABASE
# ============================================================

DB_PATH = os.getenv("DB_PATH", "taxi_bor_mi.db")

db = sqlite3.connect(
    DB_PATH,
    check_same_thread=False
)

db.row_factory = sqlite3.Row
db_lock = asyncio.Lock()


def init_db():
    db.executescript("""
    PRAGMA journal_mode=WAL;

    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER UNIQUE NOT NULL,
        role TEXT NOT NULL DEFAULT 'customer',
        lang TEXT NOT NULL DEFAULT 'uz',
        name TEXT DEFAULT '',
        phone TEXT DEFAULT '',
        home_area TEXT DEFAULT '',
        blocked INTEGER NOT NULL DEFAULT 0,
        warning_count INTEGER NOT NULL DEFAULT 0,
        risk_score INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS drivers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER UNIQUE NOT NULL,
        full_name TEXT NOT NULL,
        phone TEXT NOT NULL,
        car_model TEXT NOT NULL,
        plate TEXT UNIQUE NOT NULL,
        license_file_id TEXT DEFAULT '',
        tech_file_id TEXT DEFAULT '',
        car_photo_file_id TEXT DEFAULT '',
        route TEXT NOT NULL DEFAULT 'OBLIQ_ANGREN',
        approved INTEGER NOT NULL DEFAULT 0,
        online INTEGER NOT NULL DEFAULT 0,
        active_orders INTEGER NOT NULL DEFAULT 0,
        rating REAL NOT NULL DEFAULT 5.0,
        rating_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_tg_id INTEGER NOT NULL,
        driver_tg_id INTEGER,
        service TEXT NOT NULL,
        origin TEXT NOT NULL,
        destination TEXT NOT NULL,
        origin_lat REAL,
        origin_lon REAL,
        passengers INTEGER DEFAULT 1,
        price INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'SEARCHING',
        created_at TEXT NOT NULL,
        accepted_at TEXT,
        finished_at TEXT,
        claim_deadline TEXT
    );

    CREATE TABLE IF NOT EXISTS order_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        actor_tg_id INTEGER,
        event TEXT NOT NULL,
        details TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS ratings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        from_tg_id INTEGER NOT NULL,
        to_tg_id INTEGER NOT NULL,
        score INTEGER NOT NULL,
        comment TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE(order_id, from_tg_id)
    );

    CREATE TABLE IF NOT EXISTS complaints (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER,
        reporter_tg_id INTEGER NOT NULL,
        target_tg_id INTEGER,
        category TEXT NOT NULL,
        text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'NEW',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS support_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_tg_id INTEGER,
        action TEXT NOT NULL,
        details TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_orders_customer
    ON orders(customer_tg_id);

    CREATE INDEX IF NOT EXISTS idx_orders_driver
    ON orders(driver_tg_id);

    CREATE INDEX IF NOT EXISTS idx_orders_status
    ON orders(status);

    CREATE INDEX IF NOT EXISTS idx_drivers_online
    ON drivers(online, approved);
    """)

    db.commit()


def now():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


async def db_execute(
    sql,
    params=(),
    fetch=False,
    fetchone=False,
    commit=True
):
    async with db_lock:
        cur = db.execute(sql, params)

        if commit:
            db.commit()

        if fetchone:
            return cur.fetchone()

        if fetch:
            return cur.fetchall()

        return cur.lastrowid


async def log_event(
    order_id,
    actor,
    event,
    details=""
):
    await db_execute(
        """
        INSERT INTO order_events(
            order_id,
            actor_tg_id,
            event,
            details,
            created_at
        )
        VALUES(?,?,?,?,?)
        """,
        (
            order_id,
            actor,
            event,
            details,
            now()
        )
    )


async def audit(
    actor,
    action,
    details=""
):
    await db_execute(
        """
        INSERT INTO audit_logs(
            actor_tg_id,
            action,
            details,
            created_at
        )
        VALUES(?,?,?,?)
        """,
        (
            actor,
            action,
            details,
            now()
        )
    )


# ============================================================
# TEXT / KEYBOARDS
# ============================================================

LANGS = {
    "uz": "🇺🇿 O‘zbekcha",
    "uzc": "🇺🇿 Ўзбекча",
    "ru": "🇷🇺 Русский",
    "en": "🇬🇧 English"
}


MAIN = {
    "uz": {
        "passenger": "👤 Yo‘lovchi",
        "delivery": "📦 Dastavka",
        "driver": "🚕 Haydovchi",
        "profile": "👤 Profil",
        "history": "📜 Tarix",
        "support": "📩 Murojaat",
        "back": "⬅️ Orqaga",
        "online": "🟢 ONLINE",
        "offline": "⚪ OFFLINE",
    },

    "uzc": {
        "passenger": "👤 Йўловчи",
        "delivery": "📦 Даставка",
        "driver": "🚕 Ҳайдовчи",
        "profile": "👤 Профиль",
        "history": "📜 Тарих",
        "support": "📩 Мурожаат",
        "back": "⬅️ Орқага",
        "online": "🟢 ONLINE",
        "offline": "⚪ OFFLINE",
    },

    "ru": {
        "passenger": "👤 Пассажир",
        "delivery": "📦 Доставка",
        "driver": "🚕 Водитель",
        "profile": "👤 Профиль",
        "history": "📜 История",
        "support": "📩 Поддержка",
        "back": "⬅️ Назад",
        "online": "🟢 ONLINE",
        "offline": "⚪ OFFLINE",
    },

    "en": {
        "passenger": "👤 Passenger",
        "delivery": "📦 Delivery",
        "driver": "🚕 Driver",
        "profile": "👤 Profile",
        "history": "📜 History",
        "support": "📩 Support",
        "back": "⬅️ Back",
        "online": "🟢 ONLINE",
        "offline": "⚪ OFFLINE",
    }
}


def main_kb(lang="uz"):
    t = MAIN.get(lang, MAIN["uz"])

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=t["passenger"]),
                KeyboardButton(text=t["delivery"])
            ],
            [
                KeyboardButton(text=t["driver"])
            ],
            [
                KeyboardButton(text=t["profile"]),
                KeyboardButton(text=t["history"])
            ],
            [
                KeyboardButton(text=t["support"])
            ]
        ],
        resize_keyboard=True
    )


def language_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🇺🇿 O‘zbekcha"),
                KeyboardButton(text="🇺🇿 Ўзбекча")
            ],
            [
                KeyboardButton(text="🇷🇺 Русский"),
                KeyboardButton(text="🇬🇧 English")
            ]
        ],
        resize_keyboard=True
    )


def location_kb(lang="uz"):
    label = {
        "uz": "📍 Joylashuvni yuborish",
        "uzc": "📍 Жойлашувни юбориш",
        "ru": "📍 Отправить геолокацию",
        "en": "📍 Send location"
    }.get(
        lang,
        "📍 Joylashuvni yuborish"
    )

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=label,
                    request_location=True
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def confirm_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ BUYURTMA BERISH",
                    callback_data="order_confirm"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ O‘ZGARTIRISH",
                    callback_data="order_edit"
                ),
                InlineKeyboardButton(
                    text="❌ BEKOR QILISH",
                    callback_data="order_cancel"
                )
            ]
        ]
    )


def driver_order_kb(order_id):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚕 BUYURTMANI OLISH",
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


def driver_panel_kb(driver):
    online = bool(driver["online"])

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="⚪ OFFLINE"
                    if online
                    else "🟢 ONLINE"
                )
            ],
            [
                KeyboardButton(
                    text="📋 YANGI BUYURTMALAR"
                ),
                KeyboardButton(
                    text="🚕 FAOL BUYURTMALAR"
                )
            ],
            [
                KeyboardButton(
                    text="📜 BUYURTMALAR TARIXI"
                ),
                KeyboardButton(
                    text="💰 DAROMAD"
                )
            ],
            [
                KeyboardButton(text="⭐ REYTING"),
                KeyboardButton(text="👤 PROFIL")
            ],
            [
                KeyboardButton(text="📩 MUROJAAT"),
                KeyboardButton(text="⬅️ Orqaga")
            ]
        ],
        resize_keyboard=True
    )


# ============================================================
# FSM
# ============================================================

class Registration(StatesGroup):
    language = State()
    name = State()
    phone = State()
    home_area = State()


class OrderFlow(StatesGroup):
    gps = State()
    route = State()
    passengers = State()
    price = State()
    confirm = State()


class DriverReg(StatesGroup):
    name = State()
    phone = State()
    car_model = State()
    plate = State()
    license = State()
    tech = State()
    car_photo = State()
    rules = State()


class SupportFlow(StatesGroup):
    text = State()


class ComplaintFlow(StatesGroup):
    text = State()


# ============================================================
# AI
# ============================================================

async def ai_json(system, user):
    if not ai:
        return None

    try:
        response = await ai.responses.create(
            model=OPENAI_MODEL,
            instructions=system,
            input=user
        )

        text = getattr(
            response,
            "output_text",
            ""
        ) or ""

        text = text.strip()

        text = re.sub(
            r"^```json\s*",
            "",
            text,
            flags=re.I
        )

        text = re.sub(
            r"\s*```$",
            "",
            text
        )

        return json.loads(text)

    except Exception as e:
        log.warning(
            "AI error: %s",
            e
        )
        return None


def deterministic_route(text):
    s = text.strip()

    low = (
        s.lower()
        .replace("→", " ")
        .replace("—", " ")
    )

    low = re.sub(
        r"\s+",
        " ",
        low
    )

    # Common aliases / route vocabulary.
    aliases = {
        "obliq": "Obliq",
        "oblik": "Obliq",
        "облик": "Obliq",
        "облиқ": "Obliq",

        "angren": "Angren",
        "ангрен": "Angren",

        "hokimiyat": "Hokimiyat",
        "hokimiyati": "Hokimiyat",
        "hokimiyatga": "Hokimiyat",
        "ҳокимият": "Hokimiyat",
        "хокимият": "Hokimiyat",
    }

    # "Obliqdan Hokimiyatga"
    # "Obliqdan Hokimiyatga boraman"
    m = re.search(
        r"(.+?)\s*dan\s+(.+?)\s*ga(?:\s+bor.*)?$",
        low,
        re.I
    )

    if m:
        o = aliases.get(
            m.group(1).strip(),
            m.group(1).strip()
        )

        d = aliases.get(
            m.group(2).strip(),
            m.group(2).strip()
        )

        return {
            "origin": o.title(),
            "destination": d.title(),
            "confidence": 0.95
        }

    # "Obliqdan Hokimiyat"
    m = re.search(
        r"(.+?)\s*dan\s+(.+?)(?:ga)?$",
        low,
        re.I
    )

    if m and len(m.group(1)) > 1:
        o = aliases.get(
            m.group(1).strip(),
            m.group(1).strip()
        )

        d = aliases.get(
            m.group(2).strip(),
            m.group(2).strip()
        )

        return {
            "origin": o.title(),
            "destination": d.title(),
            "confidence": 0.85
        }

    # Arrow form.
    parts = re.split(
        r"\s*(?:->|→|—|-)\s*",
        s
    )

    if len(parts) == 2 and all(parts):
        return {
            "origin": parts[0].strip().title(),
            "destination": parts[1].strip().title(),
            "confidence": 0.9
        }

    # Simple two-location form.
    words = low.split()

    known = []

    for w in words:
        w2 = re.sub(
            r"[^\wа-яёқғўҳʼ']",
            "",
            w
        )

        if w2 in aliases:
            known.append(
                aliases[w2]
            )

    if len(known) >= 2:
        return {
            "origin": known[0],
            "destination": known[1],
            "confidence": 0.8
        }

    return None


async def parse_route(text):
    result = await ai_json(
        """
You are the route parser for
TAXI BOR MI? — ALBATTA BOR!,
an Uzbek intercity taxi bot.

Return ONLY JSON:

{
  "origin":"...",
  "destination":"...",
  "confidence":0.0
}

Understand:
- Uzbek Latin
- Uzbek Cyrillic
- Russian
- simple English

Examples:

"Obliqdan Hokimiyatga"
=> origin Obliq, destination Hokimiyat

"Obliq → Hokimiyat"
=> same

"Men Obliqdan Hokimiyatga boraman"
=> same

Never invent a location.

If uncertain, return empty strings
and low confidence.
        """,
        text
    )

    if (
        result
        and result.get("origin")
        and result.get("destination")
    ):
        return result

    return deterministic_route(text)


async def ai_validate_registration(data):
    result = await ai_json(
        """
You validate a taxi-driver registration
for a local transport platform.

Return ONLY JSON:

{
  "ok":true,
  "reason":"...",
  "risk":0
}

Do not claim a document is authentic
with certainty.

Only check obvious missing,
inconsistent, malformed or suspicious
information from the supplied text.

Admin makes the final decision.
        """,
        json.dumps(
            data,
            ensure_ascii=False
        )
    )

    return result or {
        "ok": True,
        "reason": "AI unavailable; admin review required.",
        "risk": 0
    }


# ============================================================
# HELPERS
# ============================================================

async def get_user(tg_id):
    return await db_execute(
        "SELECT * FROM users WHERE tg_id=?",
        (tg_id,),
        fetchone=True
    )


async def get_driver(tg_id):
    return await db_execute(
        "SELECT * FROM drivers WHERE tg_id=?",
        (tg_id,),
        fetchone=True
    )


async def is_blocked(tg_id):
    u = await get_user(tg_id)

    if u and u["blocked"]:
        return True

    d = await get_driver(tg_id)

    return bool(
        d
        and u
        and u["blocked"]
    )


async def ensure_user(message):
    u = await get_user(
        message.from_user.id
    )

    if not u:
        return None

    if u["blocked"]:
        await message.answer(
            "🚫 Akkauntingiz vaqtincha bloklangan."
        )
        return None

    return u


def money(n):
    return (
        f"{int(n):,}"
        .replace(",", " ")
        + " so‘m"
    )


async def eligible_drivers():
    return await db_execute(
        """
        SELECT * FROM drivers
        WHERE approved=1
          AND online=1
          AND route='OBLIQ_ANGREN'
          AND active_orders < 7
        """,
        fetch=True
    )


async def send_admin(text):
    if ADMIN_ID:
        try:
            await bot.send_message(
                ADMIN_ID,
                text
            )
        except Exception as e:
            log.warning(
                "Admin message error: %s",
                e
            )


# ============================================================
# START / LANGUAGE / REGISTRATION
# ============================================================

@dp.message(CommandStart())
async def start(
    message: Message,
    state: FSMContext
):
    u = await get_user(
        message.from_user.id
    )

    if u:
        if u["blocked"]:
            await message.answer(
                "🚫 Akkauntingiz bloklangan."
            )
            return

        await message.answer(
            "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
            "📍 <b>OBLIQ ↔ ANGREN</b>\n"
            "👤 Yo‘lovchi • 📦 Dastavka\n\n"
            "Xizmatni tanlang:",
            reply_markup=main_kb(
                u["lang"]
            )
        )
        return

    await state.clear()

    await state.set_state(
        Registration.language
    )

    await message.answer(
        "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
        "Tilni tanlang / Выберите язык / Choose language:",
        reply_markup=language_kb()
    )


@dp.message(Registration.language)
async def registration_language(
    message: Message,
    state: FSMContext
):
    mapping = {
        "🇺🇿 O‘zbekcha": "uz",
        "🇺🇿 Ўзбекча": "uzc",
        "🇷🇺 Русский": "ru",
        "🇬🇧 English": "en"
    }

    lang = mapping.get(
        message.text
    )

    if not lang:
        await message.answer(
            "Iltimos, tildan birini tanlang.",
            reply_markup=language_kb()
        )
        return

    await state.update_data(
        lang=lang
    )

    await state.set_state(
        Registration.name
    )

    await message.answer(
        "👤 Ism-familiyangizni kiriting:"
    )


@dp.message(Registration.name)
async def registration_name(
    message: Message,
    state: FSMContext
):
    name = (
        message.text or ""
    ).strip()

    if len(name) < 2:
        await message.answer(
            "❗ Ism-familiya juda qisqa."
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
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(
                        text="📱 Raqamni yuborish",
                        request_contact=True
                    )
                ]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )


@dp.message(
    Registration.phone,
    F.contact
)
async def registration_phone(
    message: Message,
    state: FSMContext
):
    phone = message.contact.phone_number

    await state.update_data(
        phone=phone
    )

    await state.set_state(
        Registration.home_area
    )

    await message.answer(
        "🏘 Uy/mahallangizni yozing.\n"
        "Masalan: <b>Obliq</b>"
    )


@dp.message(Registration.phone)
async def registration_phone_text(
    message: Message,
    state: FSMContext
):
    phone = (
        message.text or ""
    ).strip()

    if len(
        re.sub(r"\D", "", phone)
    ) < 7:
        await message.answer(
            "📱 To‘g‘ri telefon raqam yuboring."
        )
        return

    await state.update_data(
        phone=phone
    )

    await state.set_state(
        Registration.home_area
    )

    await message.answer(
        "🏘 Uy/mahallangizni yozing. "
        "Masalan: <b>Obliq</b>"
    )


@dp.message(Registration.home_area)
async def registration_home(
    message: Message,
    state: FSMContext
):
    area = (
        message.text or ""
    ).strip()

    if len(area) < 2:
        await message.answer(
            "🏘 Mahalla nomini yozing."
        )
        return

    data = await state.get_data()

    await db_execute(
        """
        INSERT INTO users(
            tg_id,
            role,
            lang,
            name,
            phone,
            home_area,
            created_at
        )
        VALUES(?,?,?,?,?,?,?)
        """,
        (
            message.from_user.id,
            "customer",
            data.get("lang", "uz"),
            data.get("name", ""),
            data.get("phone", ""),
            area,
            now()
        )
    )

    await state.clear()

    await message.answer(
        "✅ <b>Ro‘yxatdan o‘tish yakunlandi!</b>\n\n"
        "🚕 TAXI BOR MI? — ALBATTA BOR!\n"
        "📍 OBLIQ ↔ ANGREN\n\n"
        "Xizmatni tanlang:",
        reply_markup=main_kb(
            data.get("lang", "uz")
        )
    )


# ============================================================
# CUSTOMER: SERVICE SELECTION
# ============================================================

async def begin_order(
    message: Message,
    state: FSMContext,
    service
):
    u = await ensure_user(message)

    if not u:
        return

    await state.clear()

    await state.update_data(
        service=service,
        lang=u["lang"]
    )

    await state.set_state(
        OrderFlow.gps
    )

    await message.answer(
        "📍 <b>Avval joylashuvingizni yuboring.</b>\n\n"
        "Keyin: <b>Qayerdan → Qayerga</b> manzilini yozasiz.",
        reply_markup=location_kb(
            u["lang"]
        )
    )


@dp.message(
    F.text.in_({
        "👤 Yo‘lovchi",
        "👤 Йўловчи",
        "👤 Пассажир",
        "👤 Passenger"
    })
)
async def passenger_start(
    message: Message,
    state: FSMContext
):
    await begin_order(
        message,
        state,
        "PASSENGER"
    )


@dp.message(
    F.text.in_({
        "📦 Dastavka",
        "📦 Даставка",
        "📦 Доставка",
        "📦 Delivery"
    })
)
async def delivery_start(
    message: Message,
    state: FSMContext
):
    await begin_order(
        message,
        state,
        "DELIVERY"
    )


@dp.message(
    OrderFlow.gps,
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
        OrderFlow.route
    )

    await message.answer(
        "📍 <b>Joylashuvingiz qabul qilindi.</b>\n\n"
        "Endi safar manzilini kiriting:\n"
        "📍 <b>Qayerdan → 🏁 Qayerga</b>\n\n"
        "Masalan: <b>Obliqdan Hokimiyatga</b>"
    )


@dp.message(OrderFlow.gps)
async def order_gps_text(
    message: Message,
    state: FSMContext
):
    await message.answer(
        "📍 Iltimos, pastdagi "
        "<b>Joylashuvni yuborish</b> "
        "tugmasi orqali GPS yuboring.",
        reply_markup=location_kb()
    )


@dp.message(OrderFlow.route)
async def order_route(
    message: Message,
    state: FSMContext
):
    parsed = await parse_route(
        (message.text or "").strip()
    )

    if (
        not parsed
        or not parsed.get("origin")
        or not parsed.get("destination")
    ):
        await message.answer(
            "❗ Manzilni tushunmadim.\n\n"
            "Masalan:\n"
            "• <b>Obliqdan Hokimiyatga</b>\n"
            "• <b>Obliq → Hokimiyat</b>\n"
            "• <b>Men Obliqdan Hokimiyatga boraman</b>"
        )
        return

    if float(
        parsed.get("confidence", 0)
    ) < 0.5:
        await message.answer(
            f"🤖 Men quyidagicha tushundim:\n"
            f"📍 {parsed['origin']} → "
            f"🏁 {parsed['destination']}\n\n"
            "Iltimos, yana aniqroq yozing."
        )
        return

    await state.update_data(
        origin=parsed["origin"],
        destination=parsed["destination"]
    )

    data = await state.get_data()

    if data["service"] == "PASSENGER":

        await state.set_state(
            OrderFlow.passengers
        )

        kb = ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(text=str(i))
                    for i in range(1, 5)
                ],
                [
                    KeyboardButton(text=str(i))
                    for i in range(5, 8)
                ]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )

        await message.answer(
            "👥 Necha kishi?",
            reply_markup=kb
        )

    else:

        await state.set_state(
            OrderFlow.price
        )

        await message.answer(
            "💰 Dastavka uchun narxni "
            "so‘mda kiriting:",
            reply_markup=ReplyKeyboardMarkup(
                keyboard=[
                    [
                        KeyboardButton(
                            text="⬅️ Orqaga"
                        )
                    ]
                ],
                resize_keyboard=True
            )
        )


@dp.message(OrderFlow.passengers)
async def order_passengers(
    message: Message,
    state: FSMContext
):
    try:
        n = int(
            (message.text or "").strip()
        )
    except ValueError:
        n = 0

    if n < 1 or n > 7:
        await message.answer(
            "❗ 1 dan 7 gacha son kiriting."
        )
        return

    await state.update_data(
        passengers=n
    )

    await state.set_state(
        OrderFlow.price
    )

    await message.answer(
        "💰 Safar narxini so‘mda kiriting. "
        "Masalan: <b>30000</b>"
    )


@dp.message(OrderFlow.price)
async def order_price(
    message: Message,
    state: FSMContext
):
    raw = re.sub(
        r"[^\d]",
        "",
        message.text or ""
    )

    if not raw:
        await message.answer(
            "💰 Narxni raqamda kiriting. "
            "Masalan: 30000"
        )
        return

    price = int(raw)

    if price <= 0 or price > 100_000_000:
        await message.answer(
            "❗ Narx noto‘g‘ri."
        )
        return

    await state.update_data(
        price=price
    )

    await state.set_state(
        OrderFlow.confirm
    )

    data = await state.get_data()

    passengers = (
        data.get("passengers", 1)
        if data["service"] == "PASSENGER"
        else None
    )

    text = (
        "🚕 <b>BUYURTMA</b>\n\n"
        f"📍 Qayerdan: "
        f"<b>{data['origin']}</b>\n"
        f"🏁 Qayerga: "
        f"<b>{data['destination']}</b>\n"
    )

    if passengers:
        text += (
            f"👥 {passengers} kishi\n"
        )

    text += (
        f"💰 <b>{money(price)}</b>\n"
    )

    if data["service"] == "DELIVERY":
        text += (
            "📦 Xizmat: "
            "<b>Dastavka</b>\n"
        )

    await message.answer(
        text,
        reply_markup=confirm_kb()
    )


@dp.callback_query(
    F.data == "order_edit"
)
async def order_edit(
    callback: CallbackQuery,
    state: FSMContext
):
    await callback.answer()

    await state.set_state(
        OrderFlow.route
    )

    await callback.message.answer(
        "✏️ Manzilni qayta kiriting:\n"
        "Masalan: <b>Obliqdan Hokimiyatga</b>"
    )


@dp.callback_query(
    F.data == "order_cancel"
)
async def order_cancel(
    callback: CallbackQuery,
    state: FSMContext
):
    await callback.answer(
        "Bekor qilindi"
    )

    await state.clear()

    await callback.message.edit_text(
        "❌ Buyurtma bekor qilindi."
    )


@dp.callback_query(
    F.data == "order_confirm"
)
async def order_confirm(
    callback: CallbackQuery,
    state: FSMContext
):
    data = await state.get_data()

    if (
        not data.get("origin")
        or not data.get("destination")
        or not data.get("price")
    ):
        await callback.answer(
            "Buyurtma ma'lumotlari "
            "to‘liq emas.",
            show_alert=True
        )
        return

    deadline = (
        datetime.utcnow()
        + timedelta(minutes=2)
    )

    order_id = await db_execute(
        """
        INSERT INTO orders(
            customer_tg_id,
            service,
            origin,
            destination,
            origin_lat,
            origin_lon,
            passengers,
            price,
            status,
            created_at,
            claim_deadline
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            callback.from_user.id,
            data["service"],
            data["origin"],
            data["destination"],
            data.get("lat"),
            data.get("lon"),
            data.get("passengers", 1),
            data["price"],
            "SEARCHING",
            now(),
            deadline.isoformat()
        )
    )

    await log_event(
        order_id,
        callback.from_user.id,
        "ORDER_CREATED"
    )

    await audit(
        callback.from_user.id,
        "ORDER_CREATED",
        str(order_id)
    )

    await state.clear()

    await callback.answer(
        "Buyurtma qabul qilindi!"
    )

    await callback.message.edit_text(
        f"🔎 <b>Buyurtma #{order_id}</b>\n\n"
        f"📍 {data['origin']} → "
        f"🏁 {data['destination']}\n"
        f"💰 {money(data['price'])}\n\n"
        "🚕 Haydovchi qidirilmoqda..."
    )

    asyncio.create_task(
        dispatch_order(order_id)
    )
    # ============================================================
# DISPATCH / CLAIM
# ============================================================

async def dispatch_order(order_id):
    await asyncio.sleep(1)

    order = await db_execute(
        "SELECT * FROM orders WHERE id=?",
        (order_id,),
        fetchone=True
    )

    if not order or order["status"] != "SEARCHING":
        return

    drivers = await eligible_drivers()

    if not drivers:
        await db_execute(
            """
            UPDATE orders
            SET status='NO_DRIVER'
            WHERE id=? AND status='SEARCHING'
            """,
            (order_id,)
        )

        await log_event(
            order_id,
            0,
            "NO_DRIVER"
        )

        try:
            await bot.send_message(
                order["customer_tg_id"],
                f"⚠️ <b>Buyurtma #{order_id}</b>\n\n"
                "Hozircha haydovchi topilmadi.\n\n"
                "🟢 HA, KUTAMAN — buyurtma qidiruvda qoladi\n"
                "❌ /cancel — bekor qilish"
            )
        except Exception:
            pass

        return

    text = (
        f"🚕 <b>YANGI BUYURTMA #{order_id}</b>\n\n"
        f"📍 {order['origin']}\n"
        f"🏁 {order['destination']}\n"
        f"👥 {order['passengers']} kishi\n"
        f"💰 <b>{money(order['price'])}</b>\n\n"
        "⏱ Buyurtmani qabul qilish uchun tugmani bosing."
    )

    sent = 0

    for driver in drivers:
        try:
            await bot.send_message(
                driver["tg_id"],
                text,
                reply_markup=driver_order_kb(
                    order_id
                )
            )

            sent += 1

        except Exception as e:
            log.warning(
                "Dispatch %s -> %s: %s",
                order_id,
                driver["tg_id"],
                e
            )

    await log_event(
        order_id,
        0,
        "DISPATCHED",
        f"drivers={sent}"
    )

    # 2 daqiqa kutamiz.
    await asyncio.sleep(120)

    await db_execute(
        """
        UPDATE orders
        SET status='NO_RESPONSE'
        WHERE id=? AND status='SEARCHING'
        """,
        (order_id,)
    )

    final = await db_execute(
        "SELECT status FROM orders WHERE id=?",
        (order_id,),
        fetchone=True
    )

    if final and final["status"] == "NO_RESPONSE":

        try:
            await bot.send_message(
                order["customer_tg_id"],
                f"⚠️ <b>Buyurtma #{order_id}</b>\n\n"
                "Haydovchi javob bermadi.\n\n"
                "Yangi buyurtma berishingiz mumkin."
            )

        except Exception:
            pass


@dp.callback_query(
    F.data.startswith("decline:")
)
async def decline_order(
    callback: CallbackQuery
):
    await callback.answer(
        "Rad etildi"
    )

    try:
        await callback.message.edit_reply_markup(
            reply_markup=None
        )
    except Exception:
        pass

    await callback.message.answer(
        "❌ Buyurtma rad etildi."
    )


@dp.callback_query(
    F.data.startswith("claim:")
)
async def claim_order(
    callback: CallbackQuery
):
    order_id = int(
        callback.data.split(":")[1]
    )

    driver = await get_driver(
        callback.from_user.id
    )

    if not driver or not driver["approved"]:
        await callback.answer(
            "Haydovchi tasdiqlanmagan.",
            show_alert=True
        )
        return

    if not driver["online"]:
        await callback.answer(
            "Avval ONLINE bo‘ling.",
            show_alert=True
        )
        return

    if driver["active_orders"] >= 7:
        await callback.answer(
            "Sizda 7 ta faol buyurtma bor.",
            show_alert=True
        )
        return

    async with db_lock:

        cur = db.execute(
            """
            UPDATE orders
            SET
                driver_tg_id=?,
                status='ACCEPTED',
                accepted_at=?
            WHERE id=?
              AND status='SEARCHING'
            """,
            (
                callback.from_user.id,
                now(),
                order_id
            )
        )

        db.commit()

        claimed = (
            cur.rowcount == 1
        )

        if claimed:

            db.execute(
                """
                UPDATE drivers
                SET active_orders=active_orders+1
                WHERE tg_id=?
                """,
                (
                    callback.from_user.id,
                )
            )

            db.commit()

    if not claimed:

        await callback.answer(
            "Bu buyurtma boshqa haydovchi "
            "tomonidan olindi.",
            show_alert=True
        )

        try:
            await callback.message.edit_reply_markup(
                reply_markup=None
            )
        except Exception:
            pass

        return

    order = await db_execute(
        "SELECT * FROM orders WHERE id=?",
        (order_id,),
        fetchone=True
    )

    customer = await get_user(
        order["customer_tg_id"]
    )

    await log_event(
        order_id,
        callback.from_user.id,
        "ACCEPTED"
    )

    await audit(
        callback.from_user.id,
        "ORDER_ACCEPTED",
        str(order_id)
    )

    await callback.answer(
        "Buyurtma sizniki!"
    )

    await callback.message.edit_text(
        f"✅ <b>BUYURTMA QABUL QILINDI "
        f"#{order_id}</b>\n\n"
        f"📍 {order['origin']} → "
        f"🏁 {order['destination']}\n"
        f"💰 {money(order['price'])}\n\n"
        f"👤 Mijoz: "
        f"<b>{customer['name']}</b>\n"
        f"📞 Telefon: "
        f"<b>{customer['phone']}</b>\n\n"
        "Mijoz bilan bog‘laning."
    )

    await bot.send_message(
        order["customer_tg_id"],

        f"✅ <b>Haydovchi topildi!</b>\n\n"
        f"🚕 {driver['car_model']}\n"
        f"🔢 {driver['plate']}\n"
        f"👤 {driver['full_name']}\n"
        f"📞 {driver['phone']}\n"
        f"💰 {money(order['price'])}\n\n"
        f"📍 {order['origin']} → "
        f"🏁 {order['destination']}"
    )


# ============================================================
# DRIVER REGISTRATION
# ============================================================

@dp.message(
    F.text == "🚕 Haydovchi"
)
async def driver_start(
    message: Message,
    state: FSMContext
):
    driver = await get_driver(
        message.from_user.id
    )

    if driver:

        await message.answer(
            f"🚕 <b>HAYDOVCHI PANELI</b>\n\n"
            f"Holat: "
            f"{'🟢 ONLINE' if driver['online'] else '⚪ OFFLINE'}\n"
            f"Yo‘nalish: "
            f"OBLIQ ↔ ANGREN\n"
            f"Avtomobil: "
            f"{driver['car_model']} / "
            f"{driver['plate']}\n"
            f"⭐ {driver['rating']:.1f}",

            reply_markup=driver_panel_kb(
                driver
            )
        )

        return

    await state.clear()

    await state.set_state(
        DriverReg.name
    )

    await message.answer(
        "🚕 <b>Haydovchi ro‘yxatdan "
        "o‘tishi</b>\n\n"
        "F.I.Sh.:"
    )


@dp.message(
    DriverReg.name
)
async def driver_name(
    message: Message,
    state: FSMContext
):
    value = (
        message.text or ""
    ).strip()

    if len(value) < 3:
        await message.answer(
            "F.I.Sh. to‘liqroq bo‘lsin."
        )
        return

    await state.update_data(
        full_name=value
    )

    await state.set_state(
        DriverReg.phone
    )

    await message.answer(
        "📱 Telefon raqamingiz:",

        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(
                        text="📱 Raqamni yuborish",
                        request_contact=True
                    )
                ]
            ],
            resize_keyboard=True
        )
    )


@dp.message(
    DriverReg.phone,
    F.contact
)
async def driver_phone_contact(
    message: Message,
    state: FSMContext
):
    await state.update_data(
        phone=message.contact.phone_number
    )

    await state.set_state(
        DriverReg.car_model
    )

    await message.answer(
        "🚗 Mashina modeli:\n"
        "Masalan: <b>Cobalt</b>"
    )


@dp.message(
    DriverReg.phone
)
async def driver_phone_text(
    message: Message,
    state: FSMContext
):
    value = (
        message.text or ""
    ).strip()

    if len(
        re.sub(r"\D", "", value)
    ) < 7:

        await message.answer(
            "Telefon raqam noto‘g‘ri."
        )

        return

    await state.update_data(
        phone=value
    )

    await state.set_state(
        DriverReg.car_model
    )

    await message.answer(
        "🚗 Mashina modeli:"
    )


@dp.message(
    DriverReg.car_model
)
async def driver_car(
    message: Message,
    state: FSMContext
):
    value = (
        message.text or ""
    ).strip()

    if len(value) < 2:
        await message.answer(
            "Mashina modelini kiriting."
        )
        return

    await state.update_data(
        car_model=value
    )

    await state.set_state(
        DriverReg.plate
    )

    await message.answer(
        "🔢 Davlat raqami:\n"
        "Masalan: <b>01 A 123 BC</b>"
    )


@dp.message(
    DriverReg.plate
)
async def driver_plate(
    message: Message,
    state: FSMContext
):
    value = re.sub(
        r"\s+",
        " ",
        (
            message.text or ""
        ).strip().upper()
    )

    if len(value) < 3:
        await message.answer(
            "Davlat raqamini to‘g‘ri kiriting."
        )
        return

    exists = await db_execute(
        """
        SELECT tg_id
        FROM drivers
        WHERE UPPER(plate)=UPPER(?)
        """,
        (value,),
        fetchone=True
    )

    if exists:

        await message.answer(
            "🚫 Bu avtomobil allaqachon "
            "ro‘yxatdan o‘tgan."
        )

        return

    await state.update_data(
        plate=value
    )

    await state.set_state(
        DriverReg.license
    )

    await message.answer(
        "🪪 Haydovchilik "
        "guvohnomangiz rasmini yuboring."
    )


@dp.message(
    DriverReg.license,
    F.photo
)
async def driver_license(
    message: Message,
    state: FSMContext
):
    await state.update_data(
        license_file_id=
        message.photo[-1].file_id
    )

    await state.set_state(
        DriverReg.tech
    )

    await message.answer(
        "📄 Texpasport rasmini yuboring."
    )


@dp.message(
    DriverReg.license
)
async def driver_license_bad(
    message: Message
):
    await message.answer(
        "🪪 Iltimos, haydovchilik "
        "guvohnomasining rasmini yuboring."
    )


@dp.message(
    DriverReg.tech,
    F.photo
)
async def driver_tech(
    message: Message,
    state: FSMContext
):
    await state.update_data(
        tech_file_id=
        message.photo[-1].file_id
    )

    await state.set_state(
        DriverReg.car_photo
    )

    await message.answer(
        "🚗 Avtomobilingizning "
        "tashqi rasmini yuboring."
    )


@dp.message(
    DriverReg.tech
)
async def driver_tech_bad(
    message: Message
):
    await message.answer(
        "📄 Iltimos, texpasport "
        "rasmini yuboring."
    )


@dp.message(
    DriverReg.car_photo,
    F.photo
)
async def driver_car_photo(
    message: Message,
    state: FSMContext
):
    await state.update_data(
        car_photo_file_id=
        message.photo[-1].file_id
    )

    await state.set_state(
        DriverReg.rules
    )

    await message.answer(
        "📋 <b>QOIDALAR</b>\n\n"
        "1. Buyurtmani halol bajarish.\n"
        "2. Mijozga hurmat bilan muomala qilish.\n"
        "3. Qabul qilingan narxni "
        "o‘zboshimchalik bilan o‘zgartirmaslik.\n"
        "4. Telefon raqamini tarqatmaslik.\n"
        "5. Noto‘g‘ri ma’lumot bermaslik.\n\n"
        "Qabul qilasizmi?",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ QABUL QILAMAN",
                        callback_data=
                        "driver_rules_yes"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ RAD ETAMAN",
                        callback_data=
                        "driver_rules_no"
                    )
                ]
            ]
        )
    )


@dp.message(
    DriverReg.car_photo
)
async def driver_car_photo_bad(
    message: Message
):
    await message.answer(
        "🚗 Iltimos, avtomobil "
        "rasmini yuboring."
    )


@dp.callback_query(
    F.data == "driver_rules_no"
)
async def driver_rules_no(
    callback: CallbackQuery,
    state: FSMContext
):
    await callback.answer()

    await state.clear()

    await callback.message.edit_text(
        "❌ Ro‘yxatdan o‘tish "
        "bekor qilindi."
    )


@dp.callback_query(
    F.data == "driver_rules_yes"
)
async def driver_rules_yes(
    callback: CallbackQuery,
    state: FSMContext
):
    data = await state.get_data()

    ai_check = await ai_validate_registration(
        {
            "full_name":
                data.get("full_name"),

            "phone":
                data.get("phone"),

            "car_model":
                data.get("car_model"),

            "plate":
                data.get("plate"),

            "route":
                "OBLIQ_ANGREN"
        }
    )

    await db_execute(
        """
        INSERT INTO drivers(
            tg_id,
            full_name,
            phone,
            car_model,
            plate,
            license_file_id,
            tech_file_id,
            car_photo_file_id,
            route,
            approved,
            created_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            callback.from_user.id,
            data["full_name"],
            data["phone"],
            data["car_model"],
            data["plate"],
            data["license_file_id"],
            data["tech_file_id"],
            data["car_photo_file_id"],
            "OBLIQ_ANGREN",
            0,
            now()
        )
    )

    await audit(
        callback.from_user.id,
        "DRIVER_REGISTERED",
        data["plate"]
    )

    await state.clear()

    await callback.answer(
        "Ariza yuborildi!"
    )

    await callback.message.edit_text(
        "✅ <b>Haydovchi arizasi "
        "qabul qilindi.</b>\n\n"
        "👨‍💼 Admin hujjatlarni tekshiradi "
        "va tasdiqlaydi.\n\n"
        f"🤖 AI tekshiruvi: "
        f"{'mos' if ai_check.get('ok') else 'shubhali'}\n"
        f"⚠️ Izoh: "
        f"{ai_check.get('reason', '')}"
    )

    if ADMIN_ID:

        await bot.send_message(
            ADMIN_ID,

            f"🚕 <b>YANGI HAYDOVCHI "
            f"ARIZASI</b>\n\n"

            f"👤 {data['full_name']}\n"
            f"📞 {data['phone']}\n"
            f"🚗 {data['car_model']}\n"
            f"🔢 {data['plate']}\n\n"

            f"🤖 AI: "
            f"{ai_check.get('reason', '')}",

            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ TASDIQLASH",
                            callback_data=
                            f"approve_driver:"
                            f"{callback.from_user.id}"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ RAD ETISH",
                            callback_data=
                            f"reject_driver:"
                            f"{callback.from_user.id}"
                        )
                    ]
                ]
            )
        )


@dp.callback_query(
    F.data.startswith("approve_driver:")
)
async def approve_driver(
    callback: CallbackQuery
):
    if callback.from_user.id != ADMIN_ID:

        await callback.answer(
            "Ruxsat yo‘q.",
            show_alert=True
        )

        return

    tg_id = int(
        callback.data.split(":")[1]
    )

    await db_execute(
        """
        UPDATE drivers
        SET approved=1
        WHERE tg_id=?
        """,
        (tg_id,)
    )

    await audit(
        ADMIN_ID,
        "DRIVER_APPROVED",
        str(tg_id)
    )

    await callback.answer(
        "Tasdiqlandi"
    )

    await callback.message.edit_text(
        "✅ Haydovchi tasdiqlandi."
    )

    driver = await get_driver(
        tg_id
    )

    await bot.send_message(
        tg_id,

        "🎉 <b>Siz tasdiqlandingiz!</b>\n\n"
        "Endi 🚕 Haydovchi bo‘limidan "
        "ONLINE bo‘lib buyurtma "
        "qabul qilishingiz mumkin.",

        reply_markup=driver_panel_kb(
            driver
        )
    )


@dp.callback_query(
    F.data.startswith("reject_driver:")
)
async def reject_driver(
    callback: CallbackQuery
):
    if callback.from_user.id != ADMIN_ID:

        await callback.answer(
            "Ruxsat yo‘q.",
            show_alert=True
        )

        return

    tg_id = int(
        callback.data.split(":")[1]
    )

    driver = await get_driver(
        tg_id
    )

    await db_execute(
        "DELETE FROM drivers WHERE tg_id=?",
        (tg_id,)
    )

    await audit(
        ADMIN_ID,
        "DRIVER_REJECTED",
        str(tg_id)
    )

    await callback.answer(
        "Rad etildi"
    )

    await callback.message.edit_text(
        "❌ Haydovchi arizasi rad etildi."
    )

    if driver:

        await bot.send_message(
            tg_id,
            "❌ Haydovchi arizangiz "
            "rad etildi."
        )


# ============================================================
# DRIVER PANEL
# ============================================================

@dp.message(
    F.text.in_({
        "🟢 ONLINE",
        "⚪ OFFLINE"
    })
)
async def driver_toggle(
    message: Message
):
    driver = await get_driver(
        message.from_user.id
    )

    if not driver:

        await message.answer(
            "Avval haydovchi sifatida "
            "ro‘yxatdan o‘ting."
        )

        return

    if not driver["approved"]:

        await message.answer(
            "⏳ Admin tasdiqlashi kerak."
        )

        return

    new_value = (
        0
        if driver["online"]
        else 1
    )

    await db_execute(
        """
        UPDATE drivers
        SET online=?
        WHERE tg_id=?
        """,
        (
            new_value,
            message.from_user.id
        )
    )

    driver = await get_driver(
        message.from_user.id
    )

    await message.answer(
        "🟢 Siz ONLINE bo‘ldingiz."
        if new_value
        else
        "⚪ Siz OFFLINE bo‘ldingiz.",

        reply_markup=driver_panel_kb(
            driver
        )
    )


@dp.message(
    F.text == "📋 YANGI BUYURTMALAR"
)
async def driver_new_orders(
    message: Message
):
    driver = await get_driver(
        message.from_user.id
    )

    if (
        not driver
        or not driver["approved"]
    ):

        await message.answer(
            "🚫 Haydovchi tasdiqlanmagan."
        )

        return

    orders = await db_execute(
        """
        SELECT *
        FROM orders
        WHERE status='SEARCHING'
        ORDER BY id DESC
        LIMIT 20
        """,
        fetch=True
    )

    if not orders:

        await message.answer(
            "📭 Hozircha yangi "
            "buyurtma yo‘q."
        )

        return

    for order in orders:

        await message.answer(
            f"🚕 <b>#{order['id']}</b>\n"
            f"📍 {order['origin']} → "
            f"🏁 {order['destination']}\n"
            f"💰 {money(order['price'])}",

            reply_markup=driver_order_kb(
                order["id"]
            )
        )


@dp.message(
    F.text == "🚕 FAOL BUYURTMALAR"
)
async def driver_active(
    message: Message
):
    orders = await db_execute(
        """
        SELECT *
        FROM orders
        WHERE driver_tg_id=?
          AND status IN (
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
        fetch=True
    )

    if not orders:

        await message.answer(
            "📭 Faol buyurtma yo‘q."
        )

        return

    for order in orders:

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📞 BOG‘LANISH",
                        callback_data=
                        f"status:{order['id']}:CONTACTED"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🚗 YO‘LDA",
                        callback_data=
                        f"status:{order['id']}:ON_WAY"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="👤 MIJOZ OLINDI",
                        callback_data=
                        f"status:{order['id']}:PICKED_UP"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="✅ YAKUNLASH",
                        callback_data=
                        f"finish:{order['id']}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⚠️ MUAMMO",
                        callback_data=
                        f"complaint:{order['id']}"
                    )
                ]
            ]
        )

        await message.answer(
            f"🚕 <b>#{order['id']}</b>\n"
            f"📍 {order['origin']} → "
            f"🏁 {order['destination']}\n"
            f"💰 {money(order['price'])}\n"
            f"📌 Status: {order['status']}",

            reply_markup=keyboard
        )


@dp.callback_query(
    F.data.startswith("status:")
)
async def order_status(
    callback: CallbackQuery
):
    _, oid, status = (
        callback.data.split(":")
    )

    oid = int(oid)

    order = await db_execute(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    if (
        not order
        or order["driver_tg_id"]
        != callback.from_user.id
    ):

        await callback.answer(
            "Ruxsat yo‘q.",
            show_alert=True
        )

        return

    await db_execute(
        """
        UPDATE orders
        SET status=?
        WHERE id=?
          AND driver_tg_id=?
        """,
        (
            status,
            oid,
            callback.from_user.id
        )
    )

    await log_event(
        oid,
        callback.from_user.id,
        status
    )

    await callback.answer(
        "Status yangilandi"
    )

    try:

        await bot.send_message(
            order["customer_tg_id"],
            f"🚕 Buyurtma #{oid}:\n"
            f"<b>{status}</b>"
        )

    except Exception:
        pass


@dp.callback_query(
    F.data.startswith("finish:")
)
async def finish_order(
    callback: CallbackQuery
):
    oid = int(
        callback.data.split(":")[1]
    )

    order = await db_execute(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    if (
        not order
        or order["driver_tg_id"]
        != callback.from_user.id
    ):

        await callback.answer(
            "Ruxsat yo‘q.",
            show_alert=True
        )

        return

    if order["status"] == "FINISHED":

        await callback.answer(
            "Allaqachon yakunlangan."
        )

        return

    await db_execute(
        """
        UPDATE orders
        SET
            status='FINISHED',
            finished_at=?
        WHERE id=?
        """,
        (
            now(),
            oid
        )
    )

    await db_execute(
        """
        UPDATE drivers
        SET active_orders=
            CASE
                WHEN active_orders > 0
                THEN active_orders-1
                ELSE 0
            END
        WHERE tg_id=?
        """,
        (
            callback.from_user.id,
        )
    )

    await log_event(
        oid,
        callback.from_user.id,
        "FINISHED"
    )

    await callback.answer(
        "Yakunlandi"
    )

    try:
        await callback.message.edit_reply_markup(
            reply_markup=None
        )
    except Exception:
        pass

    await callback.message.answer(
        "✅ Buyurtma yakunlandi."
    )

    await bot.send_message(
        order["customer_tg_id"],

        f"✅ <b>Buyurtma #{oid} "
        f"yakunlandi.</b>\n\n"
        "⭐ Haydovchini baholang:",

        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=str(i),
                        callback_data=
                        f"rate:{oid}:{i}"
                    )
                    for i in range(1, 6)
                ]
            ]
        )
    )


@dp.callback_query(
    F.data.startswith("rate:")
)
async def rate_driver(
    callback: CallbackQuery
):
    _, oid, score = (
        callback.data.split(":")
    )

    oid = int(oid)
    score = int(score)

    order = await db_execute(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    if (
        not order
        or order["customer_tg_id"]
        != callback.from_user.id
        or not order["driver_tg_id"]
    ):

        await callback.answer(
            "Ruxsat yo‘q.",
            show_alert=True
        )

        return

    try:

        await db_execute(
            """
            INSERT INTO ratings(
                order_id,
                from_tg_id,
                to_tg_id,
                score,
                created_at
            )
            VALUES(?,?,?,?,?)
            """,
            (
                oid,
                callback.from_user.id,
                order["driver_tg_id"],
                score,
                now()
            )
        )

    except sqlite3.IntegrityError:

        await callback.answer(
            "Siz allaqachon baholagansiz."
        )

        return

    driver = await get_driver(
        order["driver_tg_id"]
    )

    count = (
        driver["rating_count"] + 1
    )

    new_rating = (
        (
            driver["rating"]
            * driver["rating_count"]
        )
        + score
    ) / count

    await db_execute(
        """
        UPDATE drivers
        SET
            rating=?,
            rating_count=?
        WHERE tg_id=?
        """,
        (
            new_rating,
            count,
            order["driver_tg_id"]
        )
    )

    await callback.answer(
        "Rahmat!"
    )

    await callback.message.edit_text(
        f"⭐ Siz {score}/5 baho berdingiz."
    )


@dp.message(
    F.text == "📜 BUYURTMALAR TARIXI"
)
async def driver_history(
    message: Message
):
    rows = await db_execute(
        """
        SELECT *
        FROM orders
        WHERE driver_tg_id=?
        ORDER BY id DESC
        LIMIT 20
        """,
        (
            message.from_user.id,
        ),
        fetch=True
    )

    if not rows:

        await message.answer(
            "📭 Tarix bo‘sh."
        )

        return

    text = (
        "📜 <b>BUYURTMALAR TARIXI</b>\n\n"
    )

    for order in rows:

        text += (
            f"#{order['id']} | "
            f"{order['origin']} → "
            f"{order['destination']} | "
            f"{money(order['price'])} | "
            f"{order['status']}\n"
        )

    await message.answer(
        text
    )


@dp.message(
    F.text == "💰 DAROMAD"
)
async def driver_income(
    message: Message
):
    row = await db_execute(
        """
        SELECT
            COALESCE(SUM(price),0) AS total,
            COUNT(*) AS cnt
        FROM orders
        WHERE driver_tg_id=?
          AND status='FINISHED'
        """,
        (
            message.from_user.id,
        ),
        fetchone=True
    )

    await message.answer(
        "💰 <b>DAROMAD</b>\n\n"
        f"🚕 Yakunlangan buyurtmalar: "
        f"{row['cnt']}\n"
        f"💵 Jami: "
        f"<b>{money(row['total'])}</b>"
    )


@dp.message(
    F.text == "⭐ REYTING"
)
async def driver_rating(
    message: Message
):
    driver = await get_driver(
        message.from_user.id
    )

    if not driver:

        await message.answer(
            "Haydovchi profili topilmadi."
        )

        return

    await message.answer(
        "⭐ <b>REYTING</b>\n\n"
        f"Bahosi: "
        f"<b>{driver['rating']:.2f}/5</b>\n"
        f"Baholar soni: "
        f"{driver['rating_count']}"
    )


@dp.message(
    F.text == "👤 PROFIL"
)
async def driver_profile(
    message: Message
):
    driver = await get_driver(
        message.from_user.id
    )

    if not driver:

        await message.answer(
            "Haydovchi profili topilmadi."
        )

        return

    await message.answer(
        "👤 <b>HAYDOVCHI PROFILI</b>\n\n"
        f"F.I.Sh.: {driver['full_name']}\n"
        f"📞 {driver['phone']}\n"
        f"🚗 {driver['car_model']}\n"
        f"🔢 {driver['plate']}\n"
        f"📍 OBLIQ ↔ ANGREN\n"
        f"⭐ {driver['rating']:.2f}\n"
        f"🟢 ONLINE: "
        f"{'HA' if driver['online'] else 'YO‘Q'}"
    )


# ============================================================
# CUSTOMER PROFILE / HISTORY
# ============================================================

@dp.message(
    F.text.in_({
        "👤 Profil",
        "👤 Профиль",
        "👤 Profile"
    })
)
async def customer_profile(
    message: Message
):
    user = await get_user(
        message.from_user.id
    )

    if not user:

        await message.answer(
            "Avval /start bosing."
        )

        return

    await message.answer(
        "👤 <b>PROFIL</b>\n\n"
        f"Ism: {user['name']}\n"
        f"📞 {user['phone']}\n"
        f"🏘 Mahalla: "
        f"{user['home_area']}\n"
        f"🌐 Til: {user['lang']}"
    )


@dp.message(
    F.text.in_({
        "📜 Tarix",
        "📜 Тарих",
        "📜 История",
        "📜 History"
    })
)
async def customer_history(
    message: Message
):
    rows = await db_execute(
        """
        SELECT *
        FROM orders
        WHERE customer_tg_id=?
        ORDER BY id DESC
        LIMIT 20
        """,
        (
            message.from_user.id,
        ),
        fetch=True
    )

    if not rows:

        await message.answer(
            "📭 Buyurtmalar tarixi bo‘sh."
        )

        return

    text = (
        "📜 <b>BUYURTMALAR TARIXI</b>\n\n"
    )

    for order in rows:

        text += (
            f"#{order['id']} | "
            f"{order['origin']} → "
            f"{order['destination']}\n"
            f"💰 {money(order['price'])} | "
            f"{order['status']}\n\n"
        )

    await message.answer(
        text
    )


# ============================================================
# SUPPORT / COMPLAINT
# ============================================================

@dp.message(
    F.text.in_({
        "📩 Murojaat",
        "📩 Мурожаат",
        "📩 Поддержка",
        "📩 Support"
    })
)
async def support_start(
    message: Message,
    state: FSMContext
):
    await state.set_state(
        SupportFlow.text
    )

    await message.answer(
        "📩 Murojaat yoki "
        "taklifingizni yozing:"
    )


@dp.message(
    SupportFlow.text
)
async def support_save(
    message: Message,
    state: FSMContext
):
    text = (
        message.text or ""
    ).strip()

    if len(text) < 3:

        await message.answer(
            "Iltimos, batafsilroq yozing."
        )

        return

    ticket_id = await db_execute(
        """
        INSERT INTO support_tickets(
            tg_id,
            text,
            created_at
        )
        VALUES(?,?,?)
        """,
        (
            message.from_user.id,
            text,
            now()
        )
    )

    await state.clear()

    await message.answer(
        "✅ Murojaatingiz qabul qilindi.\n"
        f"🎫 Ticket: "
        f"<b>MR-{ticket_id:06d}</b>"
    )

    await send_admin(
        f"📩 <b>YANGI MUROJAAT "
        f"MR-{ticket_id:06d}</b>\n\n"
        f"👤 {message.from_user.id}\n"
        f"{text}"
    )


@dp.callback_query(
    F.data.startswith("complaint:")
)
async def complaint_start(
    callback: CallbackQuery,
    state: FSMContext
):
    order_id = int(
        callback.data.split(":")[1]
    )

    await state.update_data(
        order_id=order_id
    )

    await state.set_state(
        ComplaintFlow.text
    )

    await callback.answer()

    await callback.message.answer(
        "⚠️ Muammo/shikoyatni yozing.\n\n"
        "AI va admin ko‘rib chiqadi."
    )


@dp.message(
    ComplaintFlow.text
)
async def complaint_save(
    message: Message,
    state: FSMContext
):
    data = await state.get_data()

    order_id = data.get(
        "order_id"
    )

    text = (
        message.text or ""
    ).strip()

    order = await db_execute(
        "SELECT * FROM orders WHERE id=?",
        (order_id,),
        fetchone=True
    )

    if not order:

        await state.clear()

        await message.answer(
            "Buyurtma topilmadi."
        )

        return

    target = (
        order["driver_tg_id"]
        if message.from_user.id
        == order["customer_tg_id"]
        else
        order["customer_tg_id"]
    )

    complaint_id = await db_execute(
        """
        INSERT INTO complaints(
            order_id,
            reporter_tg_id,
            target_tg_id,
            category,
            text,
            created_at
        )
        VALUES(?,?,?,?,?,?)
        """,
        (
            order_id,
            message.from_user.id,
            target,
            "OTHER",
            text,
            now()
        )
    )

    await state.clear()

    await message.answer(
        f"✅ Shikoyat qabul qilindi. "
        f"№{complaint_id}"
    )

    await send_admin(
        f"⚠️ <b>SHIKOYAT #{complaint_id}</b>\n"
        f"Buyurtma: #{order_id}\n"
        f"Reporter: {message.from_user.id}\n\n"
        f"{text}"
    )


# ============================================================
# ADMIN
# ============================================================

@dp.message(
    Command("admin")
)
async def admin_command(
    message: Message
):
    if message.from_user.id != ADMIN_ID:

        await message.answer(
            "🚫 Ruxsat yo‘q."
        )

        return

    users = await db_execute(
        "SELECT COUNT(*) c FROM users",
        fetchone=True
    )

    drivers = await db_execute(
        "SELECT COUNT(*) c FROM drivers",
        fetchone=True
    )

    approved = await db_execute(
        """
        SELECT COUNT(*) c
        FROM drivers
        WHERE approved=1
        """,
        fetchone=True
    )

    online = await db_execute(
        """
        SELECT COUNT(*) c
        FROM drivers
        WHERE approved=1
          AND online=1
        """,
        fetchone=True
    )

    orders = await db_execute(
        "SELECT COUNT(*) c FROM orders",
        fetchone=True
    )

    active = await db_execute(
        """
        SELECT COUNT(*) c
        FROM orders
        WHERE status IN (
            'SEARCHING',
            'ACCEPTED',
            'CONTACTED',
            'ON_WAY',
            'PICKED_UP'
        )
        """,
        fetchone=True
    )

    complaints = await db_execute(
        """
        SELECT COUNT(*) c
        FROM complaints
        WHERE status='NEW'
        """,
        fetchone=True
    )

    tickets = await db_execute(
        """
        SELECT COUNT(*) c
        FROM support_tickets
        WHERE status='OPEN'
        """,
        fetchone=True
    )

    await message.answer(
        "👨‍💼 <b>ADMIN PANEL</b>\n\n"

        f"👥 Foydalanuvchilar: "
        f"{users['c']}\n"

        f"🚕 Haydovchilar: "
        f"{drivers['c']}\n"

        f"✅ Tasdiqlangan: "
        f"{approved['c']}\n"

        f"🟢 Online: "
        f"{online['c']}\n"

        f"📦 Jami buyurtmalar: "
        f"{orders['c']}\n"

        f"🔥 Faol buyurtmalar: "
        f"{active['c']}\n"

        f"⚠️ Yangi shikoyatlar: "
        f"{complaints['c']}\n"

        f"📩 Ochiq murojaatlar: "
        f"{tickets['c']}"
    )


@dp.message(
    Command("drivers")
)
async def admin_drivers(
    message: Message
):
    if message.from_user.id != ADMIN_ID:
        return

    rows = await db_execute(
        """
        SELECT *
        FROM drivers
        ORDER BY id DESC
        LIMIT 50
        """,
        fetch=True
    )

    if not rows:

        await message.answer(
            "Haydovchilar yo‘q."
        )

        return

    for driver in rows:

        await message.answer(
            f"🚕 <b>{driver['full_name']}</b>\n"
            f"ID: {driver['tg_id']}\n"
            f"📞 {driver['phone']}\n"
            f"🚗 {driver['car_model']}\n"
            f"🔢 {driver['plate']}\n"
            f"✅ Tasdiq: "
            f"{'HA' if driver['approved'] else 'YO‘Q'}\n"
            f"🟢 Online: "
            f"{'HA' if driver['online'] else 'YO‘Q'}"
        )


@dp.message(
    Command("users")
)
async def admin_users(
    message: Message
):
    if message.from_user.id != ADMIN_ID:
        return

    rows = await db_execute(
        """
        SELECT *
        FROM users
        ORDER BY id DESC
        LIMIT 50
        """,
        fetch=True
    )

    text = (
        "👥 <b>FOYDALANUVCHILAR</b>\n\n"
    )

    for user in rows:

        text += (
            f"{user['tg_id']} | "
            f"{user['name']} | "
            f"{user['phone']} | "
            f"{user['home_area']}\n"
        )

    await message.answer(
        text
        if rows
        else
        "Foydalanuvchilar yo‘q."
    )


@dp.message(
    Command("orders")
)
async def admin_orders(
    message: Message
):
    if message.from_user.id != ADMIN_ID:
        return

    rows = await db_execute(
        """
        SELECT *
        FROM orders
        ORDER BY id DESC
        LIMIT 50
        """,
        fetch=True
    )

    text = (
        "📦 <b>BUYURTMALAR</b>\n\n"
    )

    for order in rows:

        text += (
            f"#{order['id']} | "
            f"{order['origin']} → "
            f"{order['destination']} | "
            f"{money(order['price'])} | "
            f"{order['status']}\n"
        )

    await message.answer(
        text
        if rows
        else
        "Buyurtmalar yo‘q."
    )


@dp.message(
    Command("block")
)
async def admin_block(
    message: Message
):
    if message.from_user.id != ADMIN_ID:
        return

    parts = (
        message.text or ""
    ).split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):

        await message.answer(
            "Foydalanish:\n"
            "/block TELEGRAM_ID"
        )

        return

    tg_id = int(
        parts[1]
    )

    await db_execute(
        """
        UPDATE users
        SET blocked=1
        WHERE tg_id=?
        """,
        (tg_id,)
    )

    await db_execute(
        """
        UPDATE drivers
        SET online=0
        WHERE tg_id=?
        """,
        (tg_id,)
    )

    await audit(
        ADMIN_ID,
        "USER_BLOCKED",
        str(tg_id)
    )

    await message.answer(
        "🚫 Bloklandi."
    )


@dp.message(
    Command("unblock")
)
async def admin_unblock(
    message: Message
):
    if message.from_user.id != ADMIN_ID:
        return

    parts = (
        message.text or ""
    ).split()

    if (
        len(parts) != 2
        or not parts[1].isdigit()
    ):

        await message.answer(
            "Foydalanish:\n"
            "/unblock TELEGRAM_ID"
        )

        return

    tg_id = int(
        parts[1]
    )

    await db_execute(
        """
        UPDATE users
        SET blocked=0
        WHERE tg_id=?
        """,
        (tg_id,)
    )

    await audit(
        ADMIN_ID,
        "USER_UNBLOCKED",
        str(tg_id)
    )

    await message.answer(
        "✅ Blok olib tashlandi."
    )


# ============================================================
# CANCEL / FALLBACK
# ============================================================

@dp.message(
    Command("cancel")
)
async def cancel_any(
    message: Message,
    state: FSMContext
):
    await state.clear()

    user = await get_user(
        message.from_user.id
    )

    lang = (
        user["lang"]
        if user
        else "uz"
    )

    await message.answer(
        "❌ Jarayon bekor qilindi.",
        reply_markup=main_kb(lang)
    )


@dp.message()
async def fallback(
    message: Message
):
    user = await get_user(
        message.from_user.id
    )

    if not user:

        await message.answer(
            "Avval /start bosing."
        )

        return

    if message.text:

        await message.answer(
            "🤖 Buyruqni tushunmadim.\n\n"
            "Quyidagi menyudan foydalaning.",

            reply_markup=main_kb(
                user["lang"]
            )
        )


# ============================================================
# STARTUP
# ============================================================

async def main():

    init_db()

    log.info(
        "TAXI BOR MI? bot starting..."
    )

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":
    asyncio.run(main())
