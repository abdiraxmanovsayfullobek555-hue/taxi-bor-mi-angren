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
# TAXI BOR MI? — ALBATTA BOR! | OBLIQ ↔ ANGREN
# VERSION: 2.0 - tartiblangan va asosiy oqimlar tuzatilgan
# CONFIG
# Railway Variables:
# BOT_TOKEN       = Telegram token
# OPENAI_API_KEY  = OpenAI API key (optional; used only for admin/driver analysis)
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

    CREATE TABLE IF NOT EXISTS order_declines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        driver_tg_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(order_id, driver_tg_id)
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
    CREATE INDEX IF NOT EXISTS idx_declines_order_driver ON order_declines(order_id, driver_tg_id);
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
            [KeyboardButton(text="🚕 FAOL BUYURTMALAR")],
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
    """
    IMPORTANT:
    Route parsing never depends on AI.
    The customer message is parsed locally so the taxi order still works
    even when OpenAI/API/network is unavailable.
    """
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


async def eligible_drivers(order_id=None):
    if order_id is None:
        return await db_execute(
            """SELECT * FROM drivers
               WHERE approved=1 AND online=1 AND route='OBLIQ_ANGREN'
               AND active_orders < 7""",
            fetch=True
        )
    return await db_execute(
        """SELECT d.* FROM drivers d
           WHERE d.approved=1 AND d.online=1 AND d.route='OBLIQ_ANGREN'
           AND d.active_orders < 7
           AND NOT EXISTS (
               SELECT 1 FROM order_declines od
               WHERE od.order_id=? AND od.driver_tg_id=d.tg_id
           )
           ORDER BY d.active_orders ASC, d.id ASC""",
        (order_id,), fetch=True
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
            f"📍 Qayerdan: <b>{parsed['origin']}</b>\n"
            f"🏁 Qayerga: <b>{parsed['destination']}</b>\n\n"
            "Iltimos, manzilni aniqroq yozing."
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
    # First check: order must still be searchable. We intentionally wait a little
    # before declaring failure so temporary Telegram/API delays do not create
    # false NO_DRIVER results.
    await asyncio.sleep(1)
    order = await db_execute("SELECT * FROM orders WHERE id=?", (order_id,), fetchone=True)
    if not order or order["status"] != "SEARCHING":
        return

    drivers = await eligible_drivers(order_id)
    if drivers:
        text = (
            f"🚕 <b>YANGI BUYURTMA #{order_id}</b>\n\n"
            f"📍 {order['origin']}\n"
            f"🏁 {order['destination']}\n"
            f"👥 {order['passengers']} kishi\n"
            f"💰 <b>{money(order['price'])}</b>\n\n"
            "⏱ Sizda 2 daqiqa ichida qabul qilish imkoniyati bor."
        )
        sent = 0
        for d in drivers:
            try:
                await bot.send_message(
                    d["tg_id"], text,
                    reply_markup=driver_order_kb(order_id)
                )
                sent += 1
            except Exception as e:
                log.warning("Dispatch %s -> %s: %s", order_id, d["tg_id"], e)
        await log_event(order_id, 0, "DISPATCHED", f"drivers={sent}")
    else:
        await log_event(order_id, 0, "NO_DRIVER_YET")

    # Keep searching for one minute. A driver can claim during this whole window.
    await asyncio.sleep(60)
    current = await db_execute(
        "SELECT * FROM orders WHERE id=?", (order_id,), fetchone=True
    )
    if not current or current["status"] != "SEARCHING":
        return

    drivers = await eligible_drivers(order_id)
    if drivers:
        # Redispatch only to drivers who did not decline and are still eligible.
        text = (
            f"🚕 <b>BUYURTMA HALI HAM QIDIRILMOQDA #{order_id}</b>\n\n"
            f"📍 {current['origin']}\n"
            f"🏁 {current['destination']}\n"
            f"👥 {current['passengers']} kishi\n"
            f"💰 <b>{money(current['price'])}</b>\n\n"
            "⏱ Qabul qilish uchun 2 daqiqa."
        )
        for d in drivers:
            try:
                await bot.send_message(
                    d["tg_id"], text,
                    reply_markup=driver_order_kb(order_id)
                )
            except Exception as e:
                log.warning("Redispatch %s -> %s: %s", order_id, d["tg_id"], e)
        await log_event(order_id, 0, "REDISPATCHED", f"drivers={len(drivers)}")
    else:
        await db_execute(
            "UPDATE orders SET status='NO_DRIVER' WHERE id=? AND status='SEARCHING'",
            (order_id,)
        )
        await log_event(order_id, 0, "NO_DRIVER")
        try:
            await bot.send_message(
                current["customer_tg_id"],
                f"⚠️ <b>Buyurtma #{order_id}</b>\n\n"
                "Hozircha haydovchi topilmadi.\n"
                "🟢 HA, KUTAMAN — qidiruvni davom ettirish uchun /wait yozing.\n"
                "❌ /cancel — bekor qilish."
            )
        except Exception:
            pass

    # Hard claim deadline: two minutes after order creation.
    await asyncio.sleep(60)
    await db_execute(
        """UPDATE orders SET status='NO_RESPONSE'
           WHERE id=? AND status='SEARCHING'
           AND claim_deadline IS NOT NULL
           AND claim_deadline <= ?""",
        (order_id, now())
    )
    final = await db_execute(
        "SELECT * FROM orders WHERE id=?", (order_id,), fetchone=True
    )
    if final and final["status"] == "NO_RESPONSE":
        await log_event(order_id, 0, "NO_RESPONSE")
        try:
            await bot.send_message(
                final["customer_tg_id"],
                f"⚠️ <b>Buyurtma #{order_id}</b>\n\n"
                "Haydovchi 2 daqiqa ichida buyurtmani qabul qilmadi."
            )
        except Exception:
            pass


@dp.callback_query(F.data.startswith("decline:"))
async def decline_order(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])
    d = await get_driver(callback.from_user.id)
    if not d or not d["approved"]:
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return
    order = await db_execute(
        "SELECT * FROM orders WHERE id=?", (order_id,), fetchone=True
    )
    if not order or order["status"] != "SEARCHING":
        await callback.answer("Bu buyurtma endi mavjud emas.", show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return
    await db_execute(
        "INSERT OR IGNORE INTO order_declines(order_id,driver_tg_id,created_at) VALUES(?,?,?)",
        (order_id, callback.from_user.id, now())
    )
    await log_event(order_id, callback.from_user.id, "DECLINED")
    await callback.answer("Rad etildi")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass


@dp.callback_query(F.data.startswith("claim:"))
async def claim_order(callback: CallbackQuery):
    order_id = int(callback.data.split(":")[1])
    d = await get_driver(callback.from_user.id)

    if not d or not d["approved"]:
        await callback.answer("Haydovchi tasdiqlanmagan.", show_alert=True)
        return
    if not d["online"]:
        await callback.answer("Avval ONLINE bo‘ling.", show_alert=True)
        return

    async with db_lock:
        order = db.execute(
            "SELECT * FROM orders WHERE id=?", (order_id,)
        ).fetchone()
        if not order:
            claimed = False
            reason = "Buyurtma topilmadi."
        elif order["status"] != "SEARCHING":
            claimed = False
            reason = "Bu buyurtma boshqa haydovchi tomonidan olindi."
        elif order["claim_deadline"] and order["claim_deadline"] < now():
            db.execute(
                "UPDATE orders SET status='NO_RESPONSE' WHERE id=? AND status='SEARCHING'",
                (order_id,)
            )
            db.commit()
            claimed = False
            reason = "Qabul qilish vaqti tugagan."
        else:
            drow = db.execute(
                "SELECT * FROM drivers WHERE tg_id=? AND approved=1 AND online=1",
                (callback.from_user.id,)
            ).fetchone()
            if not drow or drow["active_orders"] >= 7:
                claimed = False
                reason = "Sizda 7 ta faol buyurtma bor yoki ONLINE emassiz."
            else:
                declined = db.execute(
                    "SELECT 1 FROM order_declines WHERE order_id=? AND driver_tg_id=?",
                    (order_id, callback.from_user.id)
                ).fetchone()
                if declined:
                    claimed = False
                    reason = "Siz bu buyurtmani oldin rad etgansiz."
                else:
                    cur = db.execute(
                        """UPDATE orders SET driver_tg_id=?,status='ACCEPTED',accepted_at=?
                           WHERE id=? AND status='SEARCHING'""",
                        (callback.from_user.id, now(), order_id)
                    )
                    claimed = cur.rowcount == 1
                    if claimed:
                        db.execute(
                            "UPDATE drivers SET active_orders=active_orders+1 WHERE tg_id=? AND active_orders<7",
                            (callback.from_user.id,)
                        )
                        if db.execute("SELECT changes()").fetchone()[0] != 1:
                            db.execute(
                                "UPDATE orders SET driver_tg_id=NULL,status='SEARCHING',accepted_at=NULL WHERE id=?",
                                (order_id,)
                            )
                            claimed = False
                            reason = "Faol buyurtmalar limiti tugadi."
                    db.commit()

    if not claimed:
        await callback.answer(reason, show_alert=True)
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    order = await db_execute("SELECT * FROM orders WHERE id=?", (order_id,), fetchone=True)
    customer = await get_user(order["customer_tg_id"])
    await log_event(order_id, callback.from_user.id, "ACCEPTED")
    await audit(callback.from_user.id, "ORDER_ACCEPTED", str(order_id))

    await callback.answer("Buyurtma sizniki!")
    await callback.message.edit_text(
        f"✅ <b>BUYURTMA QABUL QILINDI #{order_id}</b>\n\n"
        f"📍 {order['origin']} → 🏁 {order['destination']}\n"
        f"💰 {money(order['price'])}\n\n"
        f"👤 Mijoz: <b>{customer['name'] if customer else 'Mijoz'}</b>\n"
        f"📞 Telefon: <b>{customer['phone'] if customer else '—'}</b>\n\n"
        "Mijoz bilan bog‘laning."
    )
    await bot.send_message(
        order["customer_tg_id"],
        f"✅ <b>Haydovchi topildi!</b>\n\n"
        f"🚕 {d['car_model']}\n"
        f"🔢 {d['plate']}\n"
        f"👤 {d['full_name']}\n"
        f"📞 {d['phone']}\n"
        f"💰 {money(order['price'])}\n\n"
        f"📍 {order['origin']} → 🏁 {order['destination']}"
    )



# ============================================================
# DRIVER REGISTRATION
# ============================================================

@dp.message(Command("driver"))
async def driver_command(message: Message, state: FSMContext):
    await driver_start(message, state)


@dp.message(F.text.in_({
    "🚕 Haydovchi", "🚕 Ҳайдовчи", "🚕 Водитель", "🚕 Driver"
}))
async def driver_start(message: Message, state: FSMContext):
    u = await get_user(message.from_user.id)
    if u and u["blocked"]:
        await message.answer("🚫 Akkauntingiz bloklangan.")
        return

    existing = await get_driver(message.from_user.id)
    if existing:
        if existing["approved"]:
            await message.answer(
                "🚕 Siz haydovchi sifatida tasdiqlangansiz.",
                reply_markup=driver_panel_kb(existing)
            )
        else:
            await message.answer(
                "⏳ Haydovchilik arizangiz admin tomonidan ko‘rib chiqilmoqda."
            )
        return

    # Make sure there is a user record for the driver as well.
    if not u:
        await db_execute(
            """INSERT INTO users(tg_id,role,lang,name,phone,home_area,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (message.from_user.id, "driver", "uz",
             message.from_user.full_name or "", "", "", now())
        )

    await state.clear()
    await state.set_state(DriverReg.name)
    await message.answer(
        "🚕 <b>HAYDOVCHI RO‘YXATDAN O‘TISH</b>\n\n"
        "1/7 F.I.Sh.ni kiriting:"
    )


@dp.message(DriverReg.name)
async def driver_reg_name(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if len(value) < 3:
        await message.answer("❗ F.I.Sh. ni to‘liqroq kiriting.")
        return
    await state.update_data(full_name=value)
    await state.set_state(DriverReg.phone)
    await message.answer(
        "2/7 📱 Telefon raqamingizni yuboring:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="📱 Raqamni yuborish", request_contact=True)]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    )


@dp.message(DriverReg.phone, F.contact)
async def driver_reg_phone_contact(message: Message, state: FSMContext):
    await state.update_data(phone=message.contact.phone_number)
    await state.set_state(DriverReg.car_model)
    await message.answer("3/7 🚗 Avtomobil rusmi/modelini yozing:")


@dp.message(DriverReg.phone)
async def driver_reg_phone_text(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if len(re.sub(r"\D", "", value)) < 7:
        await message.answer("📱 To‘g‘ri telefon raqam yuboring.")
        return
    await state.update_data(phone=value)
    await state.set_state(DriverReg.car_model)
    await message.answer("3/7 🚗 Avtomobil rusmi/modelini yozing:")


@dp.message(DriverReg.car_model)
async def driver_reg_car(message: Message, state: FSMContext):
    value = (message.text or "").strip()
    if len(value) < 2:
        await message.answer("🚗 Avtomobil modelini kiriting.")
        return
    await state.update_data(car_model=value)
    await state.set_state(DriverReg.plate)
    await message.answer("4/7 🔢 Avtomobil davlat raqamini kiriting:")


@dp.message(DriverReg.plate)
async def driver_reg_plate(message: Message, state: FSMContext):
    value = (message.text or "").strip().upper().replace(" ", "")
    if len(value) < 4:
        await message.answer("🔢 Davlat raqamini to‘g‘ri kiriting.")
        return

    duplicate = await db_execute(
        "SELECT tg_id FROM drivers WHERE UPPER(REPLACE(plate,' ',''))=?",
        (value,), fetchone=True
    )
    if duplicate:
        await message.answer("❌ Bu avtomobil raqami allaqachon ro‘yxatdan o‘tgan.")
        return

    await state.update_data(plate=value)
    await state.set_state(DriverReg.license)
    await message.answer("5/7 📄 Haydovchilik guvohnomangiz rasmini yuboring:")


@dp.message(DriverReg.license, F.photo)
async def driver_reg_license(message: Message, state: FSMContext):
    await state.update_data(license_file_id=message.photo[-1].file_id)
    await state.set_state(DriverReg.tech)
    await message.answer("6/7 📄 Avtomobil texpasporti rasmini yuboring:")


@dp.message(DriverReg.license)
async def driver_reg_license_bad(message: Message, state: FSMContext):
    await message.answer("📄 Iltimos, haydovchilik guvohnomasi rasmini yuboring.")


@dp.message(DriverReg.tech, F.photo)
async def driver_reg_tech(message: Message, state: FSMContext):
    await state.update_data(tech_file_id=message.photo[-1].file_id)
    await state.set_state(DriverReg.car_photo)
    await message.answer("7/7 🚗 Avtomobilingizning tashqi rasmini yuboring:")


@dp.message(DriverReg.tech)
async def driver_reg_tech_bad(message: Message, state: FSMContext):
    await message.answer("📄 Iltimos, texpasport rasmini yuboring.")


@dp.message(DriverReg.car_photo, F.photo)
async def driver_reg_car_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    car_photo = message.photo[-1].file_id

    validation = await ai_validate_registration({
        "full_name": data.get("full_name", ""),
        "phone": data.get("phone", ""),
        "car_model": data.get("car_model", ""),
        "plate": data.get("plate", ""),
        "route": "OBLIQ_ANGREN"
    })

    # AI only assists; it does not approve/reject a driver.
    await state.update_data(
        car_photo_file_id=car_photo,
        ai_risk=int(validation.get("risk", 0) or 0)
    )
    await state.set_state(DriverReg.rules)

    await message.answer(
        "📋 <b>HAYDOVCHI QOIDALARI</b>\n\n"
        "• Buyurtmani qabul qilgach mijoz bilan bog‘lanish.\n"
        "• Narxni o‘zgartirmaslik, o‘zgarish bo‘lsa ikki tomon tasdiqlashi.\n"
        "• Mijoz ma’lumotlarini tarqatmaslik.\n"
        "• Platforma qoidalariga rioya qilish.\n\n"
        "Qoidalarni qabul qilsangiz: <b>HA</b> deb yozing."
    )


@dp.message(DriverReg.car_photo)
async def driver_reg_car_photo_bad(message: Message, state: FSMContext):
    await message.answer("🚗 Iltimos, avtomobil rasmini yuboring.")


@dp.message(DriverReg.rules)
async def driver_reg_rules(message: Message, state: FSMContext):
    answer = (message.text or "").strip().lower()
    if answer not in {"ha", "xa", "yes", "да", "ҳа"}:
        await message.answer("Qoidalarni qabul qilish uchun <b>HA</b> deb yozing.")
        return

    data = await state.get_data()
    tg_id = message.from_user.id

    duplicate_driver = await get_driver(tg_id)
    if duplicate_driver:
        await state.clear()
        await message.answer("Sizning haydovchi arizangiz allaqachon mavjud.")
        return

    duplicate_plate = await db_execute(
        "SELECT tg_id FROM drivers WHERE UPPER(REPLACE(plate,' ',''))=?",
        (data["plate"].upper().replace(" ", ""),), fetchone=True
    )
    if duplicate_plate:
        await state.clear()
        await message.answer("❌ Bu avtomobil raqami allaqachon ro‘yxatdan o‘tgan.")
        return

    await db_execute(
        """INSERT INTO drivers(
           tg_id,full_name,phone,car_model,plate,
           license_file_id,tech_file_id,car_photo_file_id,
           route,approved,online,active_orders,rating,rating_count,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,0,0,0,5.0,0,?)""",
        (
            tg_id, data.get("full_name", ""), data.get("phone", ""),
            data.get("car_model", ""), data.get("plate", ""),
            data.get("license_file_id", ""), data.get("tech_file_id", ""),
            data.get("car_photo_file_id", ""), "OBLIQ_ANGREN", now()
        )
    )

    await db_execute(
        "UPDATE users SET role='driver',name=?,phone=? WHERE tg_id=?",
        (data.get("full_name", ""), data.get("phone", ""), tg_id)
    )
    await state.clear()

    await message.answer(
        "✅ <b>Arizangiz qabul qilindi!</b>\n\n"
        "📍 Yo‘nalish: OBLIQ ↔ ANGREN\n"
        "⏳ Admin tasdiqlashidan keyin ONLINE bo‘la olasiz."
    )

    if ADMIN_ID:
        try:
            await bot.send_message(
                ADMIN_ID,
                f"🚕 <b>YANGI HAYDOVCHI ARIZASI</b>\n\n"
                f"👤 {data.get('full_name')}\n"
                f"📱 {data.get('phone')}\n"
                f"🚗 {data.get('car_model')}\n"
                f"🔢 {data.get('plate')}\n"
                f"📍 OBLIQ ↔ ANGREN\n"
                f"🆔 Telegram ID: {tg_id}\n"
                f"🤖 AI risk: {data.get('ai_risk', 0)}",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ TASDIQLASH", callback_data=f"approve_driver:{tg_id}"),
                        InlineKeyboardButton(text="❌ RAD ETISH", callback_data=f"reject_driver:{tg_id}")
                    ]
                ])
            )
            # Send documents separately so admin can inspect them.
            for label, file_id in [
                ("📄 Guvohnoma", data.get("license_file_id")),
                ("📄 Texpasport", data.get("tech_file_id")),
                ("🚗 Avtomobil", data.get("car_photo_file_id")),
            ]:
                if file_id:
                    await bot.send_photo(
                        ADMIN_ID, file_id,
                        caption=f"{label} | {data.get('full_name')} | {data.get('plate')}"
                    )
        except Exception as e:
            log.warning("Admin driver notification error: %s", e)


@dp.callback_query(F.data.startswith("approve_driver:"))
async def approve_driver(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    tg_id = int(callback.data.split(":")[1])
    d = await get_driver(tg_id)
    if not d:
        await callback.answer("Haydovchi topilmadi.", show_alert=True)
        return

    await db_execute(
        "UPDATE drivers SET approved=1 WHERE tg_id=?",
        (tg_id,)
    )
    await db_execute(
        "UPDATE users SET role='driver' WHERE tg_id=?",
        (tg_id,)
    )
    await audit(ADMIN_ID, "DRIVER_APPROVED", str(tg_id))
    await callback.answer("Tasdiqlandi.")
    await callback.message.edit_reply_markup(reply_markup=None)

    try:
        await bot.send_message(
            tg_id,
            "🎉 <b>Haydovchilik arizangiz tasdiqlandi!</b>\n\n"
            "Endi ONLINE bo‘lib buyurtma qabul qilishingiz mumkin.",
            reply_markup=driver_panel_kb(await get_driver(tg_id))
        )
    except Exception:
        pass


@dp.callback_query(F.data.startswith("reject_driver:"))
async def reject_driver(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    tg_id = int(callback.data.split(":")[1])
    d = await get_driver(tg_id)
    if not d:
        await callback.answer("Haydovchi topilmadi.", show_alert=True)
        return

    await db_execute(
        "DELETE FROM drivers WHERE tg_id=? AND approved=0",
        (tg_id,)
    )
    await audit(ADMIN_ID, "DRIVER_REJECTED", str(tg_id))
    await callback.answer("Rad etildi.")
    await callback.message.edit_reply_markup(reply_markup=None)

    try:
        await bot.send_message(
            tg_id,
            "❌ Haydovchilik arizangiz tasdiqlanmadi.\n"
            "Qo‘shimcha ma’lumot uchun admin bilan bog‘laning."
        )
    except Exception:
        pass


# ============================================================
# DRIVER PANEL
# ============================================================

@dp.message(F.text.in_({"🟢 ONLINE", "⚪ OFFLINE"}))
async def driver_toggle(message: Message):
    d = await get_driver(message.from_user.id)
    if not d:
        return
    if not d["approved"]:
        await message.answer("⏳ Admin tasdiqlashi kerak.")
        return

    new_value = 0 if d["online"] else 1
    await db_execute("UPDATE drivers SET online=? WHERE tg_id=?", (new_value, message.from_user.id))
    d = await get_driver(message.from_user.id)
    await message.answer(
        "🟢 Siz ONLINE bo‘ldingiz." if new_value else "⚪ Siz OFFLINE bo‘ldingiz.",
        reply_markup=driver_panel_kb(d)
    )


@dp.message(F.text == "📋 YANGI BUYURTMALAR")
async def driver_new_orders(message: Message):
    # Kept only for old keyboards; it never exposes a global order list.
    d = await get_driver(message.from_user.id)
    if not d or not d["approved"]:
        await message.answer("🚫 Haydovchi tasdiqlanmagan.")
        return
    await message.answer(
        "📩 Yangi buyurtmalar siz ONLINE bo‘lganingizda "
        "shaxsiy xabarda avtomatik yuboriladi."
    )

@dp.message(F.text == "🚕 FAOL BUYURTMALAR")
async def driver_active(message: Message):
    orders = await db_execute(
        """SELECT * FROM orders
           WHERE driver_tg_id=? AND status IN
           ('ACCEPTED','CONTACTED','ON_WAY','PICKED_UP')
           ORDER BY id DESC""",
        (message.from_user.id,), fetch=True
    )
    if not orders:
        await message.answer("📭 Faol buyurtma yo‘q.")
        return

    for o in orders:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📞 BOG‘LANISH", callback_data=f"status:{o['id']}:CONTACTED")],
            [InlineKeyboardButton(text="🚗 YO‘LDA", callback_data=f"status:{o['id']}:ON_WAY")],
            [InlineKeyboardButton(text="👤 MIJOZ OLINDI", callback_data=f"status:{o['id']}:PICKED_UP")],
            [InlineKeyboardButton(text="✅ YAKUNLASH", callback_data=f"finish:{o['id']}")],
            [InlineKeyboardButton(text="⚠️ MUAMMO", callback_data=f"complaint:{o['id']}")]
        ])
        await message.answer(
            f"🚕 <b>#{o['id']}</b>\n"
            f"📍 {o['origin']} → 🏁 {o['destination']}\n"
            f"💰 {money(o['price'])}\n"
            f"📌 Status: {o['status']}",
            reply_markup=kb
        )


@dp.callback_query(F.data.startswith("status:"))
async def order_status(callback: CallbackQuery):
    _, oid, status = callback.data.split(":")
    oid = int(oid)
    allowed = {
        "ACCEPTED": {"CONTACTED"},
        "CONTACTED": {"ON_WAY"},
        "ON_WAY": {"PICKED_UP"},
        "PICKED_UP": set(),
    }
    o = await db_execute("SELECT * FROM orders WHERE id=?", (oid,), fetchone=True)
    if not o or o["driver_tg_id"] != callback.from_user.id:
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return
    if status not in allowed.get(o["status"], set()):
        await callback.answer("Status ketma-ketligi noto‘g‘ri.", show_alert=True)
        return
    await db_execute(
        "UPDATE orders SET status=? WHERE id=? AND driver_tg_id=? AND status=?",
        (status, oid, callback.from_user.id, o["status"])
    )
    await log_event(oid, callback.from_user.id, status)
    await callback.answer("Status yangilandi")
    try:
        labels = {"CONTACTED":"Bog‘landi", "ON_WAY":"Yo‘lda", "PICKED_UP":"Mijoz olindi"}
        await bot.send_message(
            o["customer_tg_id"],
            f"🚕 Buyurtma #{oid}: <b>{labels.get(status,status)}</b>"
        )
    except Exception:
        pass


@dp.callback_query(F.data.startswith("finish:"))
async def finish_order(callback: CallbackQuery):
    oid = int(callback.data.split(":")[1])
    o = await db_execute("SELECT * FROM orders WHERE id=?", (oid,), fetchone=True)
    if not o or o["driver_tg_id"] != callback.from_user.id:
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return
    if o["status"] == "FINISHED":
        await callback.answer("Allaqachon yakunlangan.")
        return
    if o["status"] != "PICKED_UP":
        await callback.answer("Avval statuslarni ketma-ket bajaring: Bog‘landi → Yo‘lda → Mijoz olindi.", show_alert=True)
        return

    await db_execute(
        "UPDATE orders SET status='FINISHED',finished_at=? WHERE id=?",
        (now(), oid)
    )
    await db_execute(
        "UPDATE drivers SET active_orders=CASE WHEN active_orders>0 THEN active_orders-1 ELSE 0 END WHERE tg_id=?",
        (callback.from_user.id,)
    )
    await log_event(oid, callback.from_user.id, "FINISHED")
    await callback.answer("Yakunlandi")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer("✅ Buyurtma yakunlandi.")

    await bot.send_message(
        o["customer_tg_id"],
        f"✅ <b>Buyurtma #{oid} yakunlandi.</b>\n\n"
        "⭐ Haydovchini baholang:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=str(i), callback_data=f"rate:{oid}:{i}") for i in range(1, 6)]
        ])
    )


@dp.callback_query(F.data.startswith("rate:"))
async def rate_driver(callback: CallbackQuery):
    _, oid, score = callback.data.split(":")
    oid, score = int(oid), int(score)
    o = await db_execute("SELECT * FROM orders WHERE id=?", (oid,), fetchone=True)
    if not o or o["customer_tg_id"] != callback.from_user.id or not o["driver_tg_id"]:
        await callback.answer("Ruxsat yo‘q.", show_alert=True)
        return

    try:
        await db_execute(
            """INSERT INTO ratings(order_id,from_tg_id,to_tg_id,score,created_at)
               VALUES(?,?,?,?,?)""",
            (oid, callback.from_user.id, o["driver_tg_id"], score, now())
        )
    except sqlite3.IntegrityError:
        await callback.answer("Siz allaqachon baholagansiz.")
        return

    d = await get_driver(o["driver_tg_id"])
    count = d["rating_count"] + 1
    new_rating = ((d["rating"] * d["rating_count"]) + score) / count
    await db_execute(
        "UPDATE drivers SET rating=?,rating_count=? WHERE tg_id=?",
        (new_rating, count, o["driver_tg_id"])
    )
    await callback.answer("Rahmat!")
    await callback.message.edit_text(f"⭐ Siz {score}/5 baho berdingiz.")


@dp.message(F.text == "📜 BUYURTMALAR TARIXI")
async def driver_history(message: Message):
    rows = await db_execute(
        """SELECT * FROM orders WHERE driver_tg_id=?
           ORDER BY id DESC LIMIT 20""",
        (message.from_user.id,), fetch=True
    )
    if not rows:
        await message.answer("📭 Tarix bo‘sh.")
        return
    text = "📜 <b>BUYURTMALAR TARIXI</b>\n\n"
    for o in rows:
        text += f"#{o['id']} | {o['origin']} → {o['destination']} | {money(o['price'])} | {o['status']}\n"
    await message.answer(text)


@dp.message(F.text == "💰 DAROMAD")
async def driver_income(message: Message):
    row = await db_execute(
        """SELECT COALESCE(SUM(price),0) AS total,
                  COUNT(*) AS cnt
           FROM orders
           WHERE driver_tg_id=? AND status='FINISHED'""",
        (message.from_user.id,), fetchone=True
    )
    await message.answer(
        "💰 <b>DAROMAD</b>\n\n"
        f"🚕 Yakunlangan buyurtmalar: {row['cnt']}\n"
        f"💵 Jami: <b>{money(row['total'])}</b>"
    )


@dp.message(F.text == "⭐ REYTING")
async def driver_rating(message: Message):
    d = await get_driver(message.from_user.id)
    if not d:
        await message.answer("Haydovchi profili topilmadi.")
        return
    await message.answer(
        f"⭐ <b>REYTING</b>\n\n"
        f"Bahosi: <b>{d['rating']:.2f}/5</b>\n"
        f"Baholar soni: {d['rating_count']}"
    )


@dp.message(F.text == "👤 PROFIL")
async def driver_profile(message: Message):
    d = await get_driver(message.from_user.id)
    if not d:
        await message.answer("Haydovchi profili topilmadi.")
        return
    await message.answer(
        f"👤 <b>HAYDOVCHI PROFILI</b>\n\n"
        f"F.I.Sh.: {d['full_name']}\n"
        f"📞 {d['phone']}\n"
        f"🚗 {d['car_model']}\n"
        f"🔢 {d['plate']}\n"
        f"📍 OBLIQ ↔ ANGREN\n"
        f"⭐ {d['rating']:.2f}\n"
        f"🟢 ONLINE: {'HA' if d['online'] else 'YO‘Q'}"
    )


# ============================================================
# CUSTOMER PROFILE / HISTORY
# ============================================================

@dp.message(F.text.in_({"👤 Profil", "👤 Профиль", "👤 Profile"}))
async def customer_profile(message: Message):
    u = await get_user(message.from_user.id)
    if not u:
        await message.answer("Avval /start bosing.")
        return
    await message.answer(
        f"👤 <b>PROFIL</b>\n\n"
        f"Ism: {u['name']}\n"
        f"📞 {u['phone']}\n"
        f"🏘 Mahalla: {u['home_area']}\n"
        f"🌐 Til: {u['lang']}"
    )


@dp.message(F.text.in_({"📜 Tarix", "📜 Тарих", "📜 История", "📜 History"}))
async def customer_history(message: Message):
    rows = await db_execute(
        "SELECT * FROM orders WHERE customer_tg_id=? ORDER BY id DESC LIMIT 20",
        (message.from_user.id,), fetch=True
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

@dp.message(F.text.in_({"📩 Murojaat", "📩 Мурожаат", "📩 Поддержка", "📩 Support"}))
async def support_start(message: Message, state: FSMContext):
    await state.set_state(SupportFlow.text)
    await message.answer("📩 Murojaat yoki taklifingizni yozing:")


@dp.message(SupportFlow.text)
async def support_save(message: Message, state: FSMContext):
    text = (message.text or "").strip()
    if len(text) < 3:
        await message.answer("Iltimos, batafsilroq yozing.")
        return
    tid = await db_execute(
        "INSERT INTO support_tickets(tg_id,text,created_at) VALUES(?,?,?)",
        (message.from_user.id, text, now())
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
async def complaint_start(callback: CallbackQuery, state: FSMContext):
    oid = int(callback.data.split(":")[1])
    await state.update_data(order_id=oid)
    await state.set_state(ComplaintFlow.text)
    await callback.answer()
    await callback.message.answer(
        "⚠️ Muammo/shikoyatni yozing.\n"
        "AI va admin ko‘rib chiqadi."
    )


@dp.message(ComplaintFlow.text)
async def complaint_save(message: Message, state: FSMContext):
    data = await state.get_data()
    oid = data.get("order_id")
    text = (message.text or "").strip()
    o = await db_execute("SELECT * FROM orders WHERE id=?", (oid,), fetchone=True)

    if not o:
        await state.clear()
        await message.answer("Buyurtma topilmadi.")
        return

    target = o["driver_tg_id"] if message.from_user.id == o["customer_tg_id"] else o["customer_tg_id"]
    cid = await db_execute(
        """INSERT INTO complaints(order_id,reporter_tg_id,target_tg_id,category,text,created_at)
           VALUES(?,?,?,?,?,?)""",
        (oid, message.from_user.id, target, "OTHER", text, now())
    )
    await state.clear()
    await message.answer(f"✅ Shikoyat qabul qilindi. №{cid}")
    await send_admin(
        f"⚠️ <b>SHIKOYAT #{cid}</b>\n"
        f"Buyurtma: #{oid}\n"
        f"Reporter: {message.from_user.id}\n\n{text}"
    )


# ============================================================
# ADMIN
# ============================================================

@dp.message(Command("admin"))
async def admin_command(message: Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("🚫 Ruxsat yo‘q.")
        return

    users = await db_execute("SELECT COUNT(*) c FROM users", fetchone=True)
    drivers = await db_execute("SELECT COUNT(*) c FROM drivers", fetchone=True)
    approved = await db_execute("SELECT COUNT(*) c FROM drivers WHERE approved=1", fetchone=True)
    online = await db_execute("SELECT COUNT(*) c FROM drivers WHERE approved=1 AND online=1", fetchone=True)
    orders = await db_execute("SELECT COUNT(*) c FROM orders", fetchone=True)
    active = await db_execute(
        "SELECT COUNT(*) c FROM orders WHERE status IN ('SEARCHING','ACCEPTED','CONTACTED','ON_WAY','PICKED_UP')",
        fetchone=True
    )
    complaints = await db_execute("SELECT COUNT(*) c FROM complaints WHERE status='NEW'", fetchone=True)
    tickets = await db_execute("SELECT COUNT(*) c FROM support_tickets WHERE status='OPEN'", fetchone=True)

    await message.answer(
        "👨‍💼 <b>ADMIN PANEL</b>\n\n"
        f"👥 Foydalanuvchilar: {users['c']}\n"
        f"🚕 Haydovchilar: {drivers['c']}\n"
        f"✅ Tasdiqlangan: {approved['c']}\n"
        f"🟢 Online: {online['c']}\n"
        f"📦 Jami buyurtmalar: {orders['c']}\n"
        f"🔥 Faol buyurtmalar: {active['c']}\n"
        f"⚠️ Yangi shikoyatlar: {complaints['c']}\n"
        f"📩 Ochiq murojaatlar: {tickets['c']}"
    )


@dp.message(Command("drivers"))
async def admin_drivers(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    rows = await db_execute(
        "SELECT * FROM drivers ORDER BY id DESC LIMIT 50",
        fetch=True
    )
    if not rows:
        await message.answer("Haydovchilar yo‘q.")
        return
    for d in rows:
        await message.answer(
            f"🚕 <b>{d['full_name']}</b>\n"
            f"ID: {d['tg_id']}\n"
            f"📞 {d['phone']}\n"
            f"🚗 {d['car_model']}\n"
            f"🔢 {d['plate']}\n"
            f"✅ Tasdiq: {'HA' if d['approved'] else 'YO‘Q'}\n"
            f"🟢 Online: {'HA' if d['online'] else 'YO‘Q'}"
        )


@dp.message(Command("users"))
async def admin_users(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    rows = await db_execute(
        "SELECT * FROM users ORDER BY id DESC LIMIT 50",
        fetch=True
    )
    text = "👥 <b>FOYDALANUVCHILAR</b>\n\n"
    for u in rows:
        text += f"{u['tg_id']} | {u['name']} | {u['phone']} | {u['home_area']}\n"
    await message.answer(text or "Foydalanuvchilar yo‘q.")


@dp.message(Command("orders"))
async def admin_orders(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    rows = await db_execute(
        "SELECT * FROM orders ORDER BY id DESC LIMIT 50",
        fetch=True
    )
    text = "📦 <b>BUYURTMALAR</b>\n\n"
    for o in rows:
        text += (
            f"#{o['id']} | {o['origin']} → {o['destination']} | "
            f"{money(o['price'])} | {o['status']}\n"
        )
    await message.answer(text or "Buyurtmalar yo‘q.")


@dp.message(Command("block"))
async def admin_block(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Foydalanish: /block TELEGRAM_ID")
        return
    tg_id = int(parts[1])
    await db_execute("UPDATE users SET blocked=1 WHERE tg_id=?", (tg_id,))
    await db_execute("UPDATE drivers SET online=0 WHERE tg_id=?", (tg_id,))
    await audit(ADMIN_ID, "USER_BLOCKED", str(tg_id))
    await message.answer("🚫 Bloklandi.")


@dp.message(Command("unblock"))
async def admin_unblock(message: Message):
    if message.from_user.id != ADMIN_ID:
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        await message.answer("Foydalanish: /unblock TELEGRAM_ID")
        return
    tg_id = int(parts[1])
    await db_execute("UPDATE users SET blocked=0 WHERE tg_id=?", (tg_id,))
    await audit(ADMIN_ID, "USER_UNBLOCKED", str(tg_id))
    await message.answer("✅ Blok olib tashlandi.")


# ============================================================
# CANCEL / FALLBACK
# ============================================================

@dp.message(Command("wait"))
async def wait_order(message: Message):
    u = await get_user(message.from_user.id)
    if not u:
        await message.answer("Avval /start orqali ro‘yxatdan o‘ting.")
        return
    o = await db_execute(
        """SELECT * FROM orders WHERE customer_tg_id=?
           AND status IN ('NO_DRIVER','NO_RESPONSE','SEARCHING')
           ORDER BY id DESC LIMIT 1""",
        (message.from_user.id,), fetchone=True
    )
    if not o:
        await message.answer("📭 Davom ettiriladigan buyurtma topilmadi.")
        return
    if o["status"] == "SEARCHING":
        await message.answer(f"🔎 Buyurtma #{o['id']} hali qidirilmoqda.")
        return
    # Re-open only if the two-minute original claim window has not expired.
    if o["claim_deadline"] and o["claim_deadline"] > now():
        await db_execute("UPDATE orders SET status='SEARCHING' WHERE id=?", (o["id"],))
        await log_event(o["id"], message.from_user.id, "SEARCH_REOPENED")
        asyncio.create_task(dispatch_order(o["id"]))
        await message.answer(f"🔎 Buyurtma #{o['id']} qayta qidirilmoqda...")
    else:
        await message.answer("⏱ Bu buyurtmaning qabul qilish vaqti tugagan. Yangi buyurtma bering.")


@dp.message(Command("cancel"))
async def cancel_any(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "❌ Jarayon bekor qilindi.",
        reply_markup=main_kb((await get_user(message.from_user.id) or {"lang": "uz"})["lang"])
    )


@dp.message()
async def fallback(message: Message):
    u = await get_user(message.from_user.id)
    if not u:
        await message.answer("Avval /start bosing.")
        return

    # Driver registration messages are handled by FSM.
    if message.text:
        await message.answer(
            "🤖 Buyruqni tushunmadim.\n\n"
            "Quyidagi menyudan foydalaning.",
            reply_markup=main_kb(u["lang"])
        )


# ============================================================
# STARTUP
# ============================================================

async def main():
    init_db()
    log.info("TAXI BOR MI? bot starting...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
