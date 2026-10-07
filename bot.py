import os
import re
import sqlite3
import asyncio
import logging
from datetime import datetime, timedelta

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

# ============================================================
# TAXI BOR MI? — ALBATTA BOR! | OBLIQ ↔ ANGREN
# Railway:
# BOT_TOKEN = Telegram bot token
# ADMIN_ID  = Telegram numeric ID
# Optional:
# DB_PATH   = sqlite database path
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0
DB_PATH = os.getenv("DB_PATH", "taxi_bor_mi.db")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN Railway Variables ichida topilmadi.")

if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID Railway Variables ichida topilmadi yoki noto‘g‘ri.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

log = logging.getLogger("taxi_bor_mi")

bot = Bot(
    BOT_TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)

dp = Dispatcher(storage=MemoryStorage())

# ============================================================
# SETTINGS
# ============================================================

MAX_ACTIVE_ORDERS = 4
SEARCH_TIMEOUT = 120
NO_ANSWER_TIMEOUT = 60

PRICES = (
    5000,
    10000,
    15000,
    20000,
)

ROUTE_CODE = "OBLIQ_ANGREN"


# ============================================================
# DATABASE
# ============================================================

db = sqlite3.connect(
    DB_PATH,
    check_same_thread=False
)

db.row_factory = sqlite3.Row
db_lock = asyncio.Lock()


def now():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def init_db():

    db.executescript(
        """
        PRAGMA journal_mode=WAL;

        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE NOT NULL,
            lang TEXT NOT NULL DEFAULT 'uz',
            name TEXT NOT NULL DEFAULT '',
            phone TEXT NOT NULL DEFAULT '',
            blocked INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS drivers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tg_id INTEGER UNIQUE NOT NULL,
            lang TEXT NOT NULL DEFAULT 'uz',
            full_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            car_model TEXT NOT NULL,
            plate TEXT UNIQUE NOT NULL,
            license_file_id TEXT NOT NULL,
            tech_file_id TEXT NOT NULL,
            car_photo_file_id TEXT NOT NULL,
            route TEXT NOT NULL DEFAULT 'OBLIQ_ANGREN',
            approved INTEGER NOT NULL DEFAULT 0,
            online INTEGER NOT NULL DEFAULT 0,
            blocked INTEGER NOT NULL DEFAULT 0,
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
            passengers INTEGER NOT NULL DEFAULT 1,
            price INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'SEARCHING',
            created_at TEXT NOT NULL,
            accepted_at TEXT,
            finished_at TEXT,
            no_answer_deadline TEXT
        );

        CREATE TABLE IF NOT EXISTS offers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            driver_tg_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'SENT',
            created_at TEXT NOT NULL,
            UNIQUE(order_id, driver_tg_id)
        );

        CREATE TABLE IF NOT EXISTS order_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            actor_tg_id INTEGER,
            event TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL,
            from_tg_id INTEGER NOT NULL,
            to_tg_id INTEGER NOT NULL,
            score INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(order_id, from_tg_id)
        );

        CREATE TABLE IF NOT EXISTS complaints (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER,
            reporter_tg_id INTEGER NOT NULL,
            target_tg_id INTEGER,
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
            details TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_orders_status
        ON orders(status);

        CREATE INDEX IF NOT EXISTS idx_orders_customer
        ON orders(customer_tg_id);

        CREATE INDEX IF NOT EXISTS idx_orders_driver
        ON orders(driver_tg_id);

        CREATE INDEX IF NOT EXISTS idx_drivers_online
        ON drivers(approved, online, blocked);

        CREATE INDEX IF NOT EXISTS idx_offers_order
        ON offers(order_id);
        """
    )

    db.commit()


async def db_exec(
    sql,
    params=(),
    *,
    fetchone=False,
    fetchall=False,
    commit=True
):
    async with db_lock:

        cur = db.execute(
            sql,
            params
        )

        if fetchone:
            result = cur.fetchone()

        elif fetchall:
            result = cur.fetchall()

        else:
            result = cur.lastrowid

        if commit:
            db.commit()

        return result


async def log_event(
    order_id,
    actor,
    event,
    details=""
):

    await db_exec(
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

    await db_exec(
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


async def get_user(tg_id):

    return await db_exec(
        "SELECT * FROM users WHERE tg_id=?",
        (tg_id,),
        fetchone=True
    )


async def get_driver(tg_id):

    return await db_exec(
        "SELECT * FROM drivers WHERE tg_id=?",
        (tg_id,),
        fetchone=True
    )


async def is_blocked(tg_id):

    u = await get_user(tg_id)
    d = await get_driver(tg_id)

    return bool(
        (u and u["blocked"]) or
        (d and d["blocked"])
    )


def money(value):

    return (
        f"{int(value):,}"
        .replace(",", " ")
        + " so‘m"
    )


# ============================================================
# TRANSLATIONS
# ============================================================

T = {

    "uz": {

        "passenger": "👤 Yo‘lovchi",
        "delivery": "📦 Dastavka",
        "driver": "🚕 Haydovchi",
        "profile": "👤 Profil",
        "history": "📜 Tarix",
        "support": "📩 Murojaat",
        "back": "⬅️ Orqaga",

        "location": "📍 Joylashuvni yuborish",

        "name":
            "👤 Ism-familiyangizni kiriting:",

        "phone":
            "📱 Telefon raqamingizni yuboring:",

        "route":
            "📍 Qayerdan → Qayerga?\n"
            "Masalan: <b>Obliqdan Hokimiyatga</b>",

        "route_bad":
            "❗ Manzilni tushunmadim.\n"
            "Masalan: <b>Obliq → Hokimiyat</b>",

        "gps":
            "📍 Endi <b>olib ketish joyingiz</b> "
            "GPS manzilini yuboring.",

        "gps_bad":
            "📍 Iltimos, pastdagi tugma orqali GPS yuboring.",

        "people":
            "👥 Necha kishi? (1–4)",

        "price":
            "💰 Narxni tanlang:",

        "created":
            "🔎 <b>Buyurtma #{}</b> yaratildi.\n"
            "🚕 Haydovchi qidirilmoqda...",

        "no_driver":
            "⚠️ Hozircha haydovchi topilmadi. "
            "Buyurtma yopildi.",

        "accepted":
            "✅ Haydovchi topildi!",

        "no_answer":
            "📵 Haydovchi siz bilan bog‘lana olmadi.",

        "need_car":
            "Sizga hali ham taksi kerakmi?\n"
            "⏱ 1 daqiqa ichida javob bering.",

        "yes":
            "✅ HA, KERAK",

        "no":
            "❌ YO‘Q, KERAK EMAS",

        "expired":
            "⏰ Javob berish vaqti tugadi. "
            "Buyurtma bekor qilindi.",

        "reopened":
            "🔎 Buyurtma boshqa haydovchiga qayta yuborildi.",

        "cancelled":
            "❌ Buyurtma bekor qilindi.",

        "completed":
            "✅ Buyurtma yakunlandi.",
    },

    "uzc": {

        "passenger": "👤 Йўловчи",
        "delivery": "📦 Даставка",
        "driver": "🚕 Ҳайдовчи",
        "profile": "👤 Профиль",
        "history": "📜 Тарих",
        "support": "📩 Мурожаат",
        "back": "⬅️ Орқага",

        "location":
            "📍 Жойлашувни юбориш",

        "name":
            "👤 Исм-фамилиянгизни киритинг:",

        "phone":
            "📱 Телефон рақамингизни юборинг:",

        "route":
            "📍 Қаердан → Қаерга?\n"
            "Масалан: <b>Облиқдан Ҳокимиятга</b>",

        "route_bad":
            "❗ Манзилни тушунмадим.\n"
            "Масалан: <b>Облиқ → Ҳокимият</b>",

        "gps":
            "📍 Энди <b>олиб кетиш жойингиз</b> "
            "GPS манзилини юборинг.",

        "gps_bad":
            "📍 Илтимос, пастдаги тугма орқали GPS юборинг.",

        "people":
            "👥 Неча киши? (1–4)",

        "price":
            "💰 Нархни танланг:",

        "created":
            "🔎 <b>Буюртма #{}</b> яратилди.\n"
            "🚕 Ҳайдовчи қидирилмоқда...",

        "no_driver":
            "⚠️ Ҳозирча ҳайдовчи топилмади. "
            "Буюртма ёпилди.",

        "accepted":
            "✅ Ҳайдовчи топилди!",

        "no_answer":
            "📵 Ҳайдовчи сиз билан боғлана олмади.",

        "need_car":
            "Сизга ҳали ҳам такси керакми?\n"
            "⏱ 1 дақиқа ичида жавоб беринг.",

        "yes":
            "✅ ҲА, КЕРАК",

        "no":
            "❌ ЙЎҚ, КЕРАК ЭМАС",

        "expired":
            "⏰ Жавоб бериш вақти тугади. "
            "Буюртма бекор қилинди.",

        "reopened":
            "🔎 Буюртма бошқа ҳайдовчига қайта юборилди.",

        "cancelled":
            "❌ Буюртма бекор қилинди.",

        "completed":
            "✅ Буюртма якунланди.",
    },

    "ru": {

        "passenger": "👤 Пассажир",
        "delivery": "📦 Доставка",
        "driver": "🚕 Водитель",
        "profile": "👤 Профиль",
        "history": "📜 История",
        "support": "📩 Поддержка",
        "back": "⬅️ Назад",

        "location":
            "📍 Отправить геолокацию",

        "name":
            "👤 Введите имя и фамилию:",

        "phone":
            "📱 Отправьте номер телефона:",

        "route":
            "📍 Откуда → Куда?\n"
            "Например: <b>Облик → Хокимият</b>",

        "route_bad":
            "❗ Не понял маршрут.\n"
            "Например: <b>Облик → Хокимият</b>",

        "gps":
            "📍 Теперь отправьте GPS "
            "<b>точки посадки</b>.",

        "gps_bad":
            "📍 Отправьте геолокацию кнопкой ниже.",

        "people":
            "👥 Сколько пассажиров? (1–4)",

        "price":
            "💰 Выберите цену:",

        "created":
            "🔎 <b>Заказ #{}</b> создан.\n"
            "🚕 Ищем водителя...",

        "no_driver":
            "⚠️ Пока водитель не найден. "
            "Заказ закрыт.",

        "accepted":
            "✅ Водитель найден!",

        "no_answer":
            "📵 Водитель не смог связаться с вами.",

        "need_car":
            "Вам ещё нужна машина?\n"
            "⏱ Ответьте в течение 1 минуты.",

        "yes":
            "✅ ДА, НУЖНА",

        "no":
            "❌ НЕТ, НЕ НУЖНА",

        "expired":
            "⏰ Время вышло. Заказ отменён.",

        "reopened":
            "🔎 Заказ отправлен другому водителю.",

        "cancelled":
            "❌ Заказ отменён.",

        "completed":
            "✅ Заказ завершён.",
    },

    "en": {

        "passenger": "👤 Passenger",
        "delivery": "📦 Delivery",
        "driver": "🚕 Driver",
        "profile": "👤 Profile",
        "history": "📜 History",
        "support": "📩 Support",
        "back": "⬅️ Back",

        "location":
            "📍 Send location",

        "name":
            "👤 Enter your full name:",

        "phone":
            "📱 Send your phone number:",

        "route":
            "📍 From → To?\n"
            "Example: <b>Obliq → Hokimiyat</b>",

        "route_bad":
            "❗ I could not understand the route.\n"
            "Example: <b>Obliq → Hokimiyat</b>",

        "gps":
            "📍 Now send the GPS of your "
            "<b>pickup point</b>.",

        "gps_bad":
            "📍 Please send your location using "
            "the button below.",

        "people":
            "👥 How many passengers? (1–4)",

        "price":
            "💰 Choose the price:",

        "created":
            "🔎 <b>Order #{}</b> created.\n"
            "🚕 Looking for a driver...",

        "no_driver":
            "⚠️ No driver found yet. "
            "Order closed.",

        "accepted":
            "✅ Driver found!",

        "no_answer":
            "📵 The driver could not reach you.",

        "need_car":
            "Do you still need a taxi?\n"
            "⏱ Reply within 1 minute.",

        "yes":
            "✅ YES, I NEED IT",

        "no":
            "❌ NO, CANCEL",

        "expired":
            "⏰ Time expired. Order cancelled.",

        "reopened":
            "🔎 Order sent to another driver.",

        "cancelled":
            "❌ Order cancelled.",

        "completed":
            "✅ Order completed.",
    },
}


LANG_BUTTONS = {
    "🇺🇿 O‘zbekcha": "uz",
    "🇺🇿 Ўзбекча": "uzc",
    "🇷🇺 Русский": "ru",
    "🇬🇧 English": "en",
}


def tr(lang, key, *args):

    text = T.get(
        lang,
        T["uz"]
    ).get(
        key,
        T["uz"].get(key, key)
    )

    return text.format(*args) if args else text


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
            ],
        ],
        resize_keyboard=True,
    )


def main_kb(lang):

    t = T.get(
        lang,
        T["uz"]
    )

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
            ],
        ],
        resize_keyboard=True,
    )


def contact_kb(lang):

    label = {
        "uz": "📱 Raqamni yuborish",
        "uzc": "📱 Рақамни юбориш",
        "ru": "📱 Отправить номер",
        "en": "📱 Send phone"
    }.get(
        lang,
        "📱 Raqamni yuborish"
    )

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=label,
                    request_contact=True
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def location_kb(lang):

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=T.get(
                        lang,
                        T["uz"]
                    )["location"],
                    request_location=True
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def price_kb():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=money(PRICES[0])),
                KeyboardButton(text=money(PRICES[1]))
            ],
            [
                KeyboardButton(text=money(PRICES[2])),
                KeyboardButton(text=money(PRICES[3]))
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def people_kb():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="1"),
                KeyboardButton(text="2"),
                KeyboardButton(text="3"),
                KeyboardButton(text="4")
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def confirm_kb(lang):

    labels = {
        "uz": (
            "✅ BUYURTMA BERISH",
            "✏️ O‘ZGARTIRISH",
            "❌ BEKOR"
        ),
        "uzc": (
            "✅ БУЮРТМА БЕРИШ",
            "✏️ ЎЗГАРТИРИШ",
            "❌ БЕКОР"
        ),
        "ru": (
            "✅ ЗАКАЗАТЬ",
            "✏️ ИЗМЕНИТЬ",
            "❌ ОТМЕНА"
        ),
        "en": (
            "✅ ORDER",
            "✏️ EDIT",
            "❌ CANCEL"
        )
    }

    a, b, c = labels.get(
        lang,
        labels["uz"]
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=a,
                    callback_data="order_confirm"
                )
            ],
            [
                InlineKeyboardButton(
                    text=b,
                    callback_data="order_edit"
                ),
                InlineKeyboardButton(
                    text=c,
                    callback_data="order_cancel"
                )
            ]
        ]
    )


def driver_offer_kb(order_id, lang):

    accept = {
        "uz": "✅ BUYURTMANI OLISH",
        "uzc": "✅ БУЮРТМАНИ ОЛИШ",
        "ru": "✅ ПРИНЯТЬ ЗАКАЗ",
        "en": "✅ ACCEPT ORDER"
    }.get(
        lang,
        "✅ BUYURTMANI OLISH"
    )

    decline = {
        "uz": "❌ RAD ETISH",
        "uzc": "❌ РАД ЭТИШ",
        "ru": "❌ ОТКАЗАТЬСЯ",
        "en": "❌ DECLINE"
    }.get(
        lang,
        "❌ RAD ETISH"
    )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=accept,
                    callback_data=f"claim:{order_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=decline,
                    callback_data=f"decline:{order_id}"
                )
            ]
        ]
    )


def driver_active_kb(order_id):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📵 JAVOB YO‘Q",
                    callback_data=f"noanswer:{order_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📞 BOG‘LANDIM",
                    callback_data=f"status:{order_id}:CONTACTED"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚗 YO‘LDA",
                    callback_data=f"status:{order_id}:ON_WAY"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👤 MIJOZ OLINDI",
                    callback_data=f"status:{order_id}:PICKED_UP"
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ YAKUNLASH",
                    callback_data=f"finish:{order_id}"
                )
            ],
        ]
    )


def driver_panel_kb(d):

    online = bool(d["online"])
    lang = d["lang"] or "uz"

    labels = {
        "uz": {
            "on": "🟢 ONLINE",
            "off": "⚪ OFFLINE",
            "active": "🚕 FAOL BUYURTMALAR",
            "hist": "📜 BUYURTMALAR TARIXI",
            "income": "💰 DAROMAD",
            "rating": "⭐ REYTING",
            "profile": "👤 HAYDOVCHI PROFILI",
            "support": "📩 MUROJAAT",
            "back": "⬅️ Orqaga"
        },

        "uzc": {
            "on": "🟢 ONLINE",
            "off": "⚪ OFFLINE",
            "active": "🚕 ФАОЛ БУЮРТМАЛАР",
            "hist": "📜 БУЮРТМАЛАР ТАРИХИ",
            "income": "💰 ДАРОМАД",
            "rating": "⭐ РЕЙТИНГ",
            "profile": "👤 ҲАЙДОВЧИ ПРОФИЛИ",
            "support": "📩 МУРОЖААТ",
            "back": "⬅️ Орқага"
        },

        "ru": {
            "on": "🟢 ONLINE",
            "off": "⚪ OFFLINE",
            "active": "🚕 АКТИВНЫЕ ЗАКАЗЫ",
            "hist": "📜 ИСТОРИЯ ЗАКАЗОВ",
            "income": "💰 ДОХОД",
            "rating": "⭐ РЕЙТИНГ",
            "profile": "👤 ПРОФИЛЬ ВОДИТЕЛЯ",
            "support": "📩 ПОДДЕРЖКА",
            "back": "⬅️ НАЗАД"
        },

        "en": {
            "on": "🟢 ONLINE",
            "off": "⚪ OFFLINE",
            "active": "🚕 ACTIVE ORDERS",
            "hist": "📜 ORDER HISTORY",
            "income": "💰 INCOME",
            "rating": "⭐ RATING",
            "profile": "👤 DRIVER PROFILE",
            "support": "📩 SUPPORT",
            "back": "⬅️ BACK"
        }
    }.get(
        lang,
        {}
    )

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=labels["off"] if online else labels["on"]
                )
            ],
            [
                KeyboardButton(text=labels["active"])
            ],
            [
                KeyboardButton(text=labels["hist"]),
                KeyboardButton(text=labels["income"])
            ],
            [
                KeyboardButton(text=labels["rating"]),
                KeyboardButton(text=labels["profile"])
            ],
            [
                KeyboardButton(text=labels["support"]),
                KeyboardButton(text=labels["back"])
            ],
        ],
        resize_keyboard=True,
    )


# ============================================================
# FSM
# ============================================================

class Registration(StatesGroup):
    language = State()
    name = State()
    phone = State()


class OrderFlow(StatesGroup):
    route = State()
    gps = State()
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
# START
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

        if await is_blocked(message.from_user.id):

            await message.answer(
                "🚫 Akkauntingiz bloklangan."
            )

            return

        await state.clear()

        await message.answer(
            "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n"
            "📍 <b>OBLIQ ↔ ANGREN</b>\n\n"
            "Xizmatni tanlang:",
            reply_markup=main_kb(u["lang"])
        )

        return

    await state.clear()

    await state.set_state(
        Registration.language
    )

    await message.answer(
        "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
        "Tilni tanlang:",
        reply_markup=language_kb()
    )


@dp.message(Registration.language)
async def reg_lang(
    message: Message,
    state: FSMContext
):

    lang = LANG_BUTTONS.get(
        (message.text or "").strip()
    )

    if not lang:

        await message.answer(
            "Til tugmalaridan birini tanlang.",
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
        tr(lang, "name")
    )


@dp.message(Registration.name)
async def reg_name(
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

    data = await state.get_data()

    await state.set_state(
        Registration.phone
    )

    await message.answer(
        tr(data["lang"], "phone"),
        reply_markup=contact_kb(
            data["lang"]
        )
    )


@dp.message(
    Registration.phone,
    F.contact
)
async def reg_phone_contact(
    message: Message,
    state: FSMContext
):

    data = await state.get_data()

    phone = message.contact.phone_number

    await db_exec(
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
            message.from_user.id,
            data["lang"],
            data["name"],
            phone,
            now()
        )
    )

    await state.clear()

    await message.answer(
        "✅ Ro‘yxatdan o‘tish yakunlandi.\n\n"
        "Xizmatni tanlang:",
        reply_markup=main_kb(
            data["lang"]
        )
    )


@dp.message(Registration.phone)
async def reg_phone_text(
    message: Message,
    state: FSMContext
):

    phone = (
        message.text or ""
    ).strip()

    if len(re.sub(r"\D", "", phone)) < 7:

        await message.answer(
            "📱 To‘g‘ri telefon raqam yuboring."
        )

        return

    data = await state.get_data()

    await db_exec(
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
            message.from_user.id,
            data["lang"],
            data["name"],
            phone,
            now()
        )
    )

    await state.clear()

    await message.answer(
        "✅ Ro‘yxatdan o‘tish yakunlandi.\n\n"
        "Xizmatni tanlang:",
        reply_markup=main_kb(
            data["lang"]
        )
    )


# ============================================================
# CUSTOMER ORDER
# ============================================================

CUSTOMER_BUTTONS = {
    "👤 Yo‘lovchi": "PASSENGER",
    "👤 Йўловчи": "PASSENGER",
    "👤 Пассажир": "PASSENGER",
    "👤 Passenger": "PASSENGER",

    "📦 Dastavka": "DELIVERY",
    "📦 Даставка": "DELIVERY",
    "📦 Доставка": "DELIVERY",
    "📦 Delivery": "DELIVERY",
}


async def begin_order(
    message: Message,
    state: FSMContext,
    service
):

    u = await get_user(
        message.from_user.id
    )

    if not u:
        return

    if await is_blocked(
        message.from_user.id
    ):
        return

    await state.clear()

    await state.update_data(
        service=service,
        lang=u["lang"]
    )

    await state.set_state(
        OrderFlow.route
    )

    await message.answer(
        tr(u["lang"], "route")
    )


@dp.message(
    F.text.in_(
        set(CUSTOMER_BUTTONS.keys())
    )
)
async def service_start(
    message: Message,
    state: FSMContext
):

    await begin_order(
        message,
        state,
        CUSTOMER_BUTTONS[
            message.text
        ]
    )


@dp.message(OrderFlow.route)
async def order_route(
    message: Message,
    state: FSMContext
):

    parsed = parse_route(
        message.text or ""
    )

    data = await state.get_data()

    lang = data.get(
        "lang",
        "uz"
    )

    if not parsed:

        await message.answer(
            tr(lang, "route_bad")
        )

        return

    await state.update_data(
        origin=parsed[0],
        destination=parsed[1]
    )

    await state.set_state(
        OrderFlow.gps
    )

    await message.answer(
        tr(lang, "gps"),
        reply_markup=location_kb(lang)
    )


@dp.message(
    OrderFlow.gps,
    F.location
)
async def order_gps(
    message: Message,
    state: FSMContext
):

    data = await state.get_data()

    lang = data.get(
        "lang",
        "uz"
    )

    await state.update_data(
        lat=message.location.latitude,
        lon=message.location.longitude
    )

    if data.get("service") == "PASSENGER":

        await state.set_state(
            OrderFlow.passengers
        )

        await message.answer(
            tr(lang, "people"),
            reply_markup=people_kb()
        )

    else:

        await state.set_state(
            OrderFlow.price
        )

        await message.answer(
            tr(lang, "price"),
            reply_markup=price_kb()
        )


@dp.message(OrderFlow.gps)
async def order_gps_bad(
    message: Message,
    state: FSMContext
):

    data = await state.get_data()

    await message.answer(
        tr(
            data.get("lang", "uz"),
            "gps_bad"
        ),
        reply_markup=location_kb(
            data.get("lang", "uz")
        )
    )


@dp.message(OrderFlow.passengers)
async def order_people(
    message: Message,
    state: FSMContext
):

    data = await state.get_data()

    lang = data.get(
        "lang",
        "uz"
    )

    try:
        n = int(
            (message.text or "").strip()
        )
    except ValueError:
        n = 0

    if n < 1 or n > 4:

        await message.answer(
            tr(lang, "people")
        )

        return

    await state.update_data(
        passengers=n
    )

    await state.set_state(
        OrderFlow.price
    )

    await message.answer(
        tr(lang, "price"),
        reply_markup=price_kb()
    )


@dp.message(OrderFlow.price)
async def order_price(
    message: Message,
    state: FSMContext
):

    data = await state.get_data()

    lang = data.get(
        "lang",
        "uz"
    )

    raw = re.sub(
        r"\D",
        "",
        message.text or ""
    )

    try:
        price = int(raw)
    except ValueError:
        price = 0

    if price not in PRICES:

        await message.answer(
            tr(lang, "price"),
            reply_markup=price_kb()
        )

        return

    await state.update_data(
        price=price
    )

    await state.set_state(
        OrderFlow.confirm
    )

    data = await state.get_data()

    p = (
        f"👥 {data.get('passengers', 1)} kishi\n"
        if data["service"] == "PASSENGER"
        else "📦 Dastavka\n"
    )

    text = (
        f"🚕 <b>BUYURTMA</b>\n\n"
        f"📍 {data['origin']}\n"
        f"🏁 {data['destination']}\n"
        f"{p}"
        f"💰 <b>{money(price)}</b>\n"
        f"📍 GPS: tayyor"
    )

    await message.answer(
        text,
        reply_markup=confirm_kb(lang)
    )


@dp.callback_query(
    F.data == "order_edit"
)
async def order_edit(
    callback: CallbackQuery,
    state: FSMContext
):

    data = await state.get_data()

    lang = data.get(
        "lang",
        "uz"
    )

    await callback.answer()

    await state.set_state(
        OrderFlow.route
    )

    await callback.message.answer(
        tr(lang, "route")
    )


@dp.callback_query(
    F.data == "order_cancel"
)
async def order_cancel(
    callback: CallbackQuery,
    state: FSMContext
):

    data = await state.get_data()

    lang = data.get(
        "lang",
        "uz"
    )

    await state.clear()

    await callback.answer()

    await callback.message.edit_text(
        tr(lang, "cancelled")
    )


@dp.callback_query(
    F.data == "order_confirm"
)
async def order_confirm(
    callback: CallbackQuery,
    state: FSMContext
):

    u = await get_user(
        callback.from_user.id
    )

    data = await state.get_data()

    required = (
        "origin",
        "destination",
        "lat",
        "lon",
        "price",
        "service"
    )

    if not u or not all(
        key in data
        for key in required
    ):

        await callback.answer(
            "Buyurtma ma’lumotlari to‘liq emas.",
            show_alert=True
        )

        return

    order_id = await db_exec(
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
            created_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            callback.from_user.id,
            data["service"],
            data["origin"],
            data["destination"],
            data["lat"],
            data["lon"],
            data.get("passengers", 1),
            data["price"],
            "SEARCHING",
            now()
        )
    )

    await log_event(
        order_id,
        callback.from_user.id,
        "CREATED"
    )

    await audit(
        callback.from_user.id,
        "ORDER_CREATED",
        str(order_id)
    )

    await state.clear()

    await callback.answer("OK")

    await callback.message.edit_text(
        tr(
            u["lang"],
            "created",
            order_id
        )
    )

    asyncio.create_task(
        dispatch_loop(order_id)
    )


# ============================================================
# ROUTE PARSER
# ============================================================

def normalize_place(value):

    v = value.strip(
        " .,!?‘’'\""
    )

    aliases = {
        "obliq": "Obliq",
        "oblik": "Obliq",
        "облик": "Obliq",
        "облиқ": "Obliq",

        "angren": "Angren",
        "ангрен": "Angren",

        "hokimiyat": "Hokimiyat",
        "hokimiyati": "Hokimiyat",
        "ҳокимият": "Hokimiyat",
        "хокимият": "Hokimiyat",
    }

    return aliases.get(
        v.lower(),
        v.title()
    )


def parse_route(text):

    s = " ".join(
        (text or "").strip().split()
    )

    if not s:
        return None

    # Obliqdan Hokimiyatga
    compact = re.match(
        r"^(.+?)dan\s+(.+?)ga$",
        s,
        re.IGNORECASE
    )

    if compact:

        return (
            normalize_place(
                compact.group(1)
            ),
            normalize_place(
                compact.group(2)
            )
        )

    # Obliq -> Hokimiyat
    parts = re.split(
        r"\s*(?:→|->|—)\s*",
        s
    )

    if len(parts) == 2 and all(
        x.strip()
        for x in parts
    ):

        return (
            normalize_place(parts[0]),
            normalize_place(parts[1])
        )

    low = s.lower()

    # Obliq dan Hokimiyat ga
    m = re.search(
        r"^(.+?)\s+dan\s+(.+?)\s+ga$",
        low,
        re.IGNORECASE
    )

    if m:

        return (
            normalize_place(m.group(1)),
            normalize_place(m.group(2))
        )

    aliases = {
        "obliq": "Obliq",
        "oblik": "Obliq",
        "облик": "Obliq",
        "облиқ": "Obliq",

        "angren": "Angren",
        "ангрен": "Angren",

        "hokimiyat": "Hokimiyat",
        "hokimiyati": "Hokimiyat",
        "ҳокимият": "Hokimiyat",
        "хокимият": "Hokimiyat",
    }

    tokens = re.findall(
        r"[\wʻ’'‘-]+",
        low,
        flags=re.UNICODE
    )

    known = []

    for token in tokens:

        if token in aliases:

            place = aliases[token]

            if place not in known:
                known.append(place)

    if len(known) >= 2:

        return (
            known[0],
            known[1]
        )

    return None


# ============================================================
# DISPATCH
# ============================================================

async def eligible_drivers(
    order_id,
    exclude_driver=None
):

    params = [
        ROUTE_CODE,
        MAX_ACTIVE_ORDERS,
        order_id
    ]

    extra = ""

    if exclude_driver:

        extra = " AND d.tg_id<>?"

        params.append(
            exclude_driver
        )

    return await db_exec(
        f"""
        SELECT d.*
        FROM drivers d
        WHERE d.approved=1
          AND d.online=1
          AND d.blocked=0
          AND d.route=?
          AND d.active_orders<?
          AND NOT EXISTS(
              SELECT 1
              FROM offers o
              WHERE o.order_id=?
                AND o.driver_tg_id=d.tg_id
                AND o.status IN(
                    'DECLINED',
                    'NO_ANSWER'
                )
          )
          {extra}
        ORDER BY
            d.active_orders ASC,
            d.id ASC
        """,
        tuple(params),
        fetchall=True
    )


async def send_offer(
    order,
    driver
):

    lang = driver["lang"] or "uz"

    service = (
        "👤 Yo‘lovchi"
        if order["service"] == "PASSENGER"
        else "📦 Dastavka"
    )

    text = (
        f"🚕 <b>YANGI BUYURTMA #{order['id']}</b>\n\n"
        f"📍 {order['origin']}\n"
        f"🏁 {order['destination']}\n"
        f"{service}\n"
        +
        (
            f"👥 {order['passengers']} kishi\n"
            if order["service"] == "PASSENGER"
            else ""
        )
        +
        f"💰 <b>{money(order['price'])}</b>\n\n"
        f"📍 Olib ketish GPS quyida.\n"
        f"⏱ Qabul qilish uchun vaqt cheklangan."
    )

    await bot.send_message(
        driver["tg_id"],
        text,
        reply_markup=driver_offer_kb(
            order["id"],
            lang
        )
    )

    try:

        await bot.send_location(
            driver["tg_id"],
            order["origin_lat"],
            order["origin_lon"]
        )

    except Exception as e:

        log.warning(
            "GPS send failed: %s",
            e
        )

    await db_exec(
        """
        INSERT OR IGNORE INTO offers(
            order_id,
            driver_tg_id,
            status,
            created_at
        )
        VALUES(?,?,?,?)
        """,
        (
            order["id"],
            driver["tg_id"],
            "SENT",
            now()
        )
    )


async def dispatch_loop(
    order_id,
    exclude_driver=None
):

    await asyncio.sleep(0.5)

    order = await db_exec(
        "SELECT * FROM orders WHERE id=?",
        (order_id,),
        fetchone=True
    )

    if not order:
        return

    if order["status"] != "SEARCHING":
        return

    drivers = await eligible_drivers(
        order_id,
        exclude_driver
    )

    sent = 0

    for driver in drivers:

        try:

            await send_offer(
                order,
                driver
            )

            sent += 1

        except Exception as e:

            log.warning(
                "Offer %s -> %s failed: %s",
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

    deadline = (
        datetime.utcnow()
        +
        timedelta(
            seconds=SEARCH_TIMEOUT
        )
    )

    while datetime.utcnow() < deadline:

        await asyncio.sleep(15)

        current = await db_exec(
            "SELECT * FROM orders WHERE id=?",
            (order_id,),
            fetchone=True
        )

        if not current:
            return

        if current["status"] != "SEARCHING":
            return

        more = await eligible_drivers(
            order_id,
            exclude_driver
        )

        for driver in more:

            try:

                existing = await db_exec(
                    """
                    SELECT 1
                    FROM offers
                    WHERE order_id=?
                      AND driver_tg_id=?
                    """,
                    (
                        order_id,
                        driver["tg_id"]
                    ),
                    fetchone=True
                )

                if not existing:

                    await send_offer(
                        current,
                        driver
                    )

            except Exception as e:

                log.warning(
                    "Wave offer failed: %s",
                    e
                )

    current = await db_exec(
        "SELECT * FROM orders WHERE id=?",
        (order_id,),
        fetchone=True
    )

    if current and current["status"] == "SEARCHING":

        await db_exec(
            """
            UPDATE orders
            SET status='NO_DRIVER'
            WHERE id=?
              AND status='SEARCHING'
            """,
            (order_id,)
        )

        await log_event(
            order_id,
            0,
            "NO_DRIVER"
        )

        u = await get_user(
            current["customer_tg_id"]
        )

        if u:

            await bot.send_message(
                current["customer_tg_id"],
                tr(
                    u["lang"],
                    "no_driver"
                )
            )


# ============================================================
# DRIVER DECLINE
# ============================================================

@dp.callback_query(
    F.data.startswith("decline:")
)
async def decline(
    callback: CallbackQuery
):

    oid = int(
        callback.data.split(":")[1]
    )

    d = await get_driver(
        callback.from_user.id
    )

    if (
        not d
        or not d["approved"]
        or d["blocked"]
    ):

        await callback.answer(
            "Ruxsat yo‘q.",
            show_alert=True
        )

        return

    o = await db_exec(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    if not o or o["status"] != "SEARCHING":

        await callback.answer(
            "Buyurtma endi mavjud emas.",
            show_alert=True
        )

        return

    await db_exec(
        """
        UPDATE offers
        SET status='DECLINED'
        WHERE order_id=?
          AND driver_tg_id=?
        """,
        (
            oid,
            callback.from_user.id
        )
    )

    await log_event(
        oid,
        callback.from_user.id,
        "DECLINED"
    )

    await callback.answer(
        "Rad etildi"
    )

    try:

        await callback.message.edit_reply_markup(
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
    callback: CallbackQuery
):

    oid = int(
        callback.data.split(":")[1]
    )

    uid = callback.from_user.id

    d = await get_driver(uid)

    if (
        not d
        or not d["approved"]
        or d["blocked"]
    ):

        await callback.answer(
            "Haydovchi tasdiqlanmagan.",
            show_alert=True
        )

        return

    claimed = False
    reason = "Buyurtma mavjud emas."

    async with db_lock:

        o = db.execute(
            "SELECT * FROM orders WHERE id=?",
            (oid,)
        ).fetchone()

        if o and o["status"] == "SEARCHING":

            dr = db.execute(
                "SELECT * FROM drivers WHERE tg_id=?",
                (uid,)
            ).fetchone()

            offer = db.execute(
                """
                SELECT *
                FROM offers
                WHERE order_id=?
                  AND driver_tg_id=?
                """,
                (
                    oid,
                    uid
                )
            ).fetchone()

            if not dr["online"]:

                reason = "Avval ONLINE bo‘ling."

            elif dr["active_orders"] >= MAX_ACTIVE_ORDERS:

                reason = "Sizda 4 ta faol buyurtma bor."

            elif not offer or offer["status"] != "SENT":

                reason = "Bu taklif endi faol emas."

            else:

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
                        uid,
                        now(),
                        oid
                    )
                )

                if cur.rowcount == 1:

                    db.execute(
                        """
                        UPDATE drivers
                        SET active_orders=active_orders+1
                        WHERE tg_id=?
                          AND active_orders<?
                        """,
                        (
                            uid,
                            MAX_ACTIVE_ORDERS
                        )
                    )

                    current_active = db.execute(
                        """
                        SELECT active_orders
                        FROM drivers
                        WHERE tg_id=?
                        """,
                        (uid,)
                    ).fetchone()[0]

                    if current_active > MAX_ACTIVE_ORDERS:

                        db.execute(
                            """
                            UPDATE drivers
                            SET active_orders=active_orders-1
                            WHERE tg_id=?
                            """,
                            (uid,)
                        )

                        db.execute(
                            """
                            UPDATE orders
                            SET
                                driver_tg_id=NULL,
                                status='SEARCHING',
                                accepted_at=NULL
                            WHERE id=?
                            """,
                            (oid,)
                        )

                        reason = "Faol buyurtmalar limiti tugadi."

                    else:

                        db.execute(
                            """
                            UPDATE offers
                            SET status='ACCEPTED'
                            WHERE order_id=?
                              AND driver_tg_id=?
                            """,
                            (
                                oid,
                                uid
                            )
                        )

                        db.commit()

                        claimed = True

                else:

                    reason = "Buyurtmani boshqa haydovchi oldi."

            if not claimed:
                db.commit()

    if not claimed:

        await callback.answer(
            reason,
            show_alert=True
        )

        return

    await db_exec(
        """
        UPDATE offers
        SET status='CLOSED'
        WHERE order_id=?
          AND driver_tg_id<>?
          AND status='SENT'
        """,
        (
            oid,
            uid
        )
    )

    o = await db_exec(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    customer = await get_user(
        o["customer_tg_id"]
    )

    await log_event(
        oid,
        uid,
        "ACCEPTED"
    )

    await audit(
        uid,
        "ORDER_ACCEPTED",
        str(oid)
    )

    await callback.answer(
        "Buyurtma sizniki!"
    )

    try:

        await callback.message.edit_reply_markup(
            reply_markup=None
        )

    except Exception:
        pass

    await bot.send_message(
        uid,
        f"✅ <b>BUYURTMA QABUL QILINDI #{oid}</b>\n\n"
        f"📍 {o['origin']} → 🏁 {o['destination']}\n"
        f"💰 {money(o['price'])}\n"
        f"👤 Mijoz: "
        f"<b>{customer['name'] if customer else 'Mijoz'}</b>\n"
        f"📞 Telefon: "
        f"<b>{customer['phone'] if customer else '—'}</b>"
    )

    try:

        await bot.send_location(
            uid,
            o["origin_lat"],
            o["origin_lon"]
        )

    except Exception:
        pass

    d = await get_driver(uid)

    await bot.send_message(
        o["customer_tg_id"],
        f"{tr(customer['lang'],'accepted') if customer else '✅ Haydovchi topildi!'}\n\n"
        f"🚕 <b>{d['car_model']}</b>\n"
        f"🔢 {d['plate']}\n"
        f"👤 {d['full_name']}\n"
        f"📞 {d['phone']}\n"
        f"💰 {money(o['price'])}"
    )

    if d["car_photo_file_id"]:

        try:

            await bot.send_photo(
                o["customer_tg_id"],
                d["car_photo_file_id"],
                caption=(
                    f"🚕 {d['car_model']} | "
                    f"{d['plate']}"
                )
            )

        except Exception:
            pass

    await bot.send_message(
        uid,
        "📵 Agar mijoz javob bermasa, "
        "<b>JAVOB YO‘Q</b> tugmasini bosing.",
        reply_markup=driver_active_kb(oid)
    )


# ============================================================
# NO ANSWER
# ============================================================

@dp.callback_query(
    F.data.startswith("noanswer:")
)
async def no_answer(
    callback: CallbackQuery
):

    oid = int(
        callback.data.split(":")[1]
    )

    uid = callback.from_user.id

    o = await db_exec(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    if (
        not o
        or o["driver_tg_id"] != uid
        or o["status"] not in {
            "ACCEPTED",
            "CONTACTED"
        }
    ):

        await callback.answer(
            "Bu buyurtma faol emas.",
            show_alert=True
        )

        return

    deadline = (
        datetime.utcnow()
        +
        timedelta(
            seconds=NO_ANSWER_TIMEOUT
        )
    ).isoformat()

    await db_exec(
        """
        UPDATE orders
        SET
            status='NO_ANSWER_WAIT',
            no_answer_deadline=?
        WHERE id=?
          AND driver_tg_id=?
        """,
        (
            deadline,
            oid,
            uid
        )
    )

    await db_exec(
        """
        UPDATE drivers
        SET active_orders=
            CASE
                WHEN active_orders>0
                THEN active_orders-1
                ELSE 0
            END
        WHERE tg_id=?
        """,
        (uid,)
    )

    await log_event(
        oid,
        uid,
        "NO_ANSWER_REPORTED"
    )

    await callback.answer(
        "Mijozga tasdiqlash yuborildi."
    )

    try:

        await callback.message.edit_reply_markup(
            reply_markup=None
        )

    except Exception:
        pass

    u = await get_user(
        o["customer_tg_id"]
    )

    lang = u["lang"] if u else "uz"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tr(lang, "yes"),
                    callback_data=f"needyes:{oid}:{uid}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=tr(lang, "no"),
                    callback_data=f"needno:{oid}"
                )
            ]
        ]
    )

    await bot.send_message(
        o["customer_tg_id"],
        tr(lang, "no_answer")
        + "\n\n"
        + tr(lang, "need_car"),
        reply_markup=kb
    )

    asyncio.create_task(
        no_answer_timeout(
            oid,
            uid
        )
    )


async def no_answer_timeout(
    oid,
    old_driver
):

    await asyncio.sleep(
        NO_ANSWER_TIMEOUT
    )

    o = await db_exec(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    if (
        not o
        or o["status"] != "NO_ANSWER_WAIT"
    ):
        return

    await db_exec(
        """
        UPDATE orders
        SET
            status='CANCELLED',
            no_answer_deadline=NULL
        WHERE id=?
          AND status='NO_ANSWER_WAIT'
        """,
        (oid,)
    )

    await log_event(
        oid,
        0,
        "NO_ANSWER_TIMEOUT"
    )

    u = await get_user(
        o["customer_tg_id"]
    )

    if u:

        await bot.send_message(
            o["customer_tg_id"],
            tr(
                u["lang"],
                "expired"
            )
        )


@dp.callback_query(
    F.data.startswith("needno:")
)
async def need_no(
    callback: CallbackQuery
):

    oid = int(
        callback.data.split(":")[1]
    )

    o = await db_exec(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    if (
        not o
        or o["customer_tg_id"] != callback.from_user.id
        or o["status"] != "NO_ANSWER_WAIT"
    ):

        await callback.answer(
            "Buyurtma faol emas.",
            show_alert=True
        )

        return

    await db_exec(
        """
        UPDATE orders
        SET
            status='CANCELLED',
            no_answer_deadline=NULL
        WHERE id=?
          AND status='NO_ANSWER_WAIT'
        """,
        (oid,)
    )

    await log_event(
        oid,
        callback.from_user.id,
        "CUSTOMER_CANCELLED_AFTER_NO_ANSWER"
    )

    await callback.answer("OK")

    u = await get_user(
        callback.from_user.id
    )

    await callback.message.edit_text(
        tr(
            u["lang"],
            "cancelled"
        )
    )


@dp.callback_query(
    F.data.startswith("needyes:")
)
async def need_yes(
    callback: CallbackQuery
):

    _, oid_s, old_s = callback.data.split(":")

    oid = int(oid_s)
    old = int(old_s)

    o = await db_exec(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    if (
        not o
        or o["customer_tg_id"] != callback.from_user.id
        or o["status"] != "NO_ANSWER_WAIT"
    ):

        await callback.answer(
            "Buyurtma faol emas.",
            show_alert=True
        )

        return

    await db_exec(
        """
        UPDATE orders
        SET
            status='SEARCHING',
            driver_tg_id=NULL,
            no_answer_deadline=NULL
        WHERE id=?
          AND status='NO_ANSWER_WAIT'
        """,
        (oid,)
    )

    await db_exec(
        """
        UPDATE offers
        SET status='NO_ANSWER'
        WHERE order_id=?
          AND driver_tg_id=?
        """,
        (
            oid,
            old
        )
    )

    await log_event(
        oid,
        callback.from_user.id,
        "REOPENED",
        f"excluded_driver={old}"
    )

    await callback.answer("OK")

    u = await get_user(
        callback.from_user.id
    )

    await callback.message.edit_text(
        tr(
            u["lang"],
            "reopened"
        )
    )

    asyncio.create_task(
        dispatch_loop(
            oid,
            exclude_driver=old
        )
    )


# ============================================================
# DRIVER STATUS
# ============================================================

@dp.message(
    F.text.in_(
        {
            "🟢 ONLINE",
            "⚪ OFFLINE"
        }
    )
)
async def toggle_online_localized(
    message: Message
):

    d = await get_driver(
        message.from_user.id
    )

    if not d:
        return

    if (
        not d["approved"]
        or d["blocked"]
    ):

        await message.answer(
            "🚫 Admin tasdig‘i kerak "
            "yoki akkaunt bloklangan."
        )

        return

    new = 0 if d["online"] else 1

    await db_exec(
        """
        UPDATE drivers
        SET online=?
        WHERE tg_id=?
        """,
        (
            new,
            message.from_user.id
        )
    )

    await message.answer(
        "🟢 ONLINE"
        if new
        else "⚪ OFFLINE",
        reply_markup=driver_panel_kb(
            await get_driver(
                message.from_user.id
            )
        )
    )


@dp.message(
    F.text.in_(
        {
            "🚕 FAOL BUYURTMALAR",
            "🚕 ФАОЛ БУЮРТМАЛАР",
            "🚕 АКТИВНЫЕ ЗАКАЗЫ",
            "🚕 ACTIVE ORDERS"
        }
    )
)
async def active_orders(
    message: Message
):

    rows = await db_exec(
        """
        SELECT *
        FROM orders
        WHERE driver_tg_id=?
          AND status IN(
              'ACCEPTED',
              'CONTACTED',
              'ON_WAY',
              'PICKED_UP'
          )
        ORDER BY id DESC
        """,
        (message.from_user.id,),
        fetchall=True
    )

    if not rows:

        await message.answer(
            "📭 Faol buyurtma yo‘q."
        )

        return

    for o in rows:

        await message.answer(
            f"🚕 <b>#{o['id']}</b>\n"
            f"📍 {o['origin']} → "
            f"🏁 {o['destination']}\n"
            f"💰 {money(o['price'])}\n"
            f"📌 {o['status']}",
            reply_markup=driver_active_kb(
                o["id"]
            )
        )


@dp.callback_query(
    F.data.startswith("status:")
)
async def status_update(
    callback: CallbackQuery
):

    _, oid_s, status = callback.data.split(":")

    oid = int(oid_s)

    o = await db_exec(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    allowed = {
        "ACCEPTED": "CONTACTED",
        "CONTACTED": "ON_WAY",
        "ON_WAY": "PICKED_UP"
    }

    if (
        not o
        or o["driver_tg_id"] != callback.from_user.id
        or allowed.get(o["status"]) != status
    ):

        await callback.answer(
            "Status ketma-ketligi noto‘g‘ri.",
            show_alert=True
        )

        return

    await db_exec(
        """
        UPDATE orders
        SET status=?
        WHERE id=?
        """,
        (
            status,
            oid
        )
    )

    await log_event(
        oid,
        callback.from_user.id,
        status
    )

    await callback.answer(
        "Yangilandi"
    )


@dp.callback_query(
    F.data.startswith("finish:")
)
async def finish(
    callback: CallbackQuery
):

    oid = int(
        callback.data.split(":")[1]
    )

    o = await db_exec(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    if (
        not o
        or o["driver_tg_id"] != callback.from_user.id
        or o["status"] != "PICKED_UP"
    ):

        await callback.answer(
            "Avval Mijoz olindi statusiga o‘ting.",
            show_alert=True
        )

        return

    await db_exec(
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

    await db_exec(
        """
        UPDATE drivers
        SET active_orders=
            CASE
                WHEN active_orders>0
                THEN active_orders-1
                ELSE 0
            END
        WHERE tg_id=?
        """,
        (callback.from_user.id,)
    )

    await log_event(
        oid,
        callback.from_user.id,
        "FINISHED"
    )

    await callback.answer(
        "Yakunlandi"
    )

    u = await get_user(
        o["customer_tg_id"]
    )

    lang = u["lang"] if u else "uz"

    await bot.send_message(
        o["customer_tg_id"],
        tr(
            lang,
            "completed"
        )
        +
        "\n\n⭐ Haydovchini baholang:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=str(i),
                        callback_data=f"rate:{oid}:{i}"
                    )
                    for i in range(1, 6)
                ]
            ]
        )
    )


# ============================================================
# RATING
# ============================================================

@dp.callback_query(
    F.data.startswith("rate:")
)
async def rate(
    callback: CallbackQuery
):

    _, oid_s, score_s = callback.data.split(":")

    oid = int(oid_s)
    score = int(score_s)

    o = await db_exec(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    if (
        not o
        or o["customer_tg_id"] != callback.from_user.id
        or not o["driver_tg_id"]
        or score not in range(1, 6)
    ):

        await callback.answer(
            "Ruxsat yo‘q.",
            show_alert=True
        )

        return

    try:

        await db_exec(
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
                o["driver_tg_id"],
                score,
                now()
            )
        )

    except sqlite3.IntegrityError:

        await callback.answer(
            "Siz allaqachon baholagansiz."
        )

        return

    d = await get_driver(
        o["driver_tg_id"]
    )

    count = d["rating_count"] + 1

    rating = (
        (
            d["rating"]
            * d["rating_count"]
        )
        + score
    ) / count

    await db_exec(
        """
        UPDATE drivers
        SET
            rating=?,
            rating_count=?
        WHERE tg_id=?
        """,
        (
            rating,
            count,
            o["driver_tg_id"]
        )
    )

    await callback.answer(
        "Rahmat!"
    )

    await callback.message.edit_text(
        f"⭐ {score}/5"
    )


# ============================================================
# DRIVER REGISTRATION
# ============================================================

@dp.message(Command("driver"))
async def driver_cmd(
    message: Message,
    state: FSMContext
):

    await start_driver(
        message,
        state
    )


DRIVER_BUTTONS = {
    "🚕 Haydovchi",
    "🚕 Ҳайдовчи",
    "🚕 Водитель",
    "🚕 Driver"
}


@dp.message(
    F.text.in_(DRIVER_BUTTONS)
)
async def start_driver(
    message: Message,
    state: FSMContext
):

    if await is_blocked(
        message.from_user.id
    ):

        await message.answer(
            "🚫 Akkauntingiz bloklangan."
        )

        return

    d = await get_driver(
        message.from_user.id
    )

    if d:

        if (
            d["approved"]
            and not d["blocked"]
        ):

            await message.answer(
                "🚕 Haydovchi paneli:",
                reply_markup=driver_panel_kb(d)
            )

        elif d["blocked"]:

            await message.answer(
                "🚫 Haydovchi akkaunti bloklangan."
            )

        else:

            await message.answer(
                "⏳ Arizangiz admin "
                "tasdiqlashini kutmoqda."
            )

        return

    u = await get_user(
        message.from_user.id
    )

    lang = u["lang"] if u else "uz"

    await state.clear()

    await state.set_state(
        DriverReg.name
    )

    await state.update_data(
        lang=lang
    )

    await message.answer(
        "🚕 <b>HAYDOVCHI RO‘YXATI</b>\n\n"
        "1/7 F.I.Sh.:"
    )


@dp.message(
    DriverReg.name
)
async def dr_name(
    message: Message,
    state: FSMContext
):

    v = (
        message.text or ""
    ).strip()

    if len(v) < 3:

        await message.answer(
            "F.I.Sh. ni to‘liq kiriting."
        )

        return

    await state.update_data(
        full_name=v
    )

    await state.set_state(
        DriverReg.phone
    )

    data = await state.get_data()

    await message.answer(
        "2/7 📱 Telefon:",
        reply_markup=contact_kb(
            data.get(
                "lang",
                "uz"
            )
        )
    )


@dp.message(
    DriverReg.phone,
    F.contact
)
async def dr_phone_c(
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
        "3/7 🚗 Avtomobil modeli:"
    )


@dp.message(
    DriverReg.phone
)
async def dr_phone(
    message: Message,
    state: FSMContext
):

    v = (
        message.text or ""
    ).strip()

    if len(
        re.sub(r"\D", "", v)
    ) < 7:

        await message.answer(
            "Telefon raqam noto‘g‘ri."
        )

        return

    await state.update_data(
        phone=v
    )

    await state.set_state(
        DriverReg.car_model
    )

    await message.answer(
        "3/7 🚗 Avtomobil modeli:"
    )


@dp.message(
    DriverReg.car_model
)
async def dr_car(
    message: Message,
    state: FSMContext
):

    v = (
        message.text or ""
    ).strip()

    if len(v) < 2:

        await message.answer(
            "Avtomobil modelini kiriting."
        )

        return

    await state.update_data(
        car_model=v
    )

    await state.set_state(
        DriverReg.plate
    )

    await message.answer(
        "4/7 🔢 Davlat raqami:"
    )


@dp.message(
    DriverReg.plate
)
async def dr_plate(
    message: Message,
    state: FSMContext
):

    v = re.sub(
        r"\s+",
        "",
        (message.text or "").strip().upper()
    )

    if len(v) < 4:

        await message.answer(
            "Davlat raqamini to‘g‘ri kiriting."
        )

        return

    duplicate = await db_exec(
        """
        SELECT 1
        FROM drivers
        WHERE plate=?
        """,
        (v,),
        fetchone=True
    )

    if duplicate:

        await message.answer(
            "❌ Bu raqam allaqachon ro‘yxatdan o‘tgan."
        )

        return

    await state.update_data(
        plate=v
    )

    await state.set_state(
        DriverReg.license
    )

    await message.answer(
        "5/7 📄 Prava rasmini yuboring:"
    )


@dp.message(
    DriverReg.license,
    F.photo
)
async def dr_license(
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
        "6/7 📄 Texpasport rasmini yuboring:"
    )


@dp.message(
    DriverReg.license
)
async def dr_license_bad(
    message: Message,
    state: FSMContext
):

    await message.answer(
        "📄 Prava rasmini yuboring."
    )


@dp.message(
    DriverReg.tech,
    F.photo
)
async def dr_tech(
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
        "7/7 🚗 Mashina rasmini yuboring:"
    )


@dp.message(
    DriverReg.tech
)
async def dr_tech_bad(
    message: Message,
    state: FSMContext
):

    await message.answer(
        "📄 Texpasport rasmini yuboring."
    )


@dp.message(
    DriverReg.car_photo,
    F.photo
)
async def dr_photo(
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
        "📋 Qoidalar:\n"
        "• Mijoz bilan bog‘lanish.\n"
        "• Mijoz ma’lumotlarini tarqatmaslik.\n"
        "• Buyurtma narxini o‘zboshimchalik bilan "
        "o‘zgartirmaslik.\n\n"
        "Qabul qilsangiz <b>HA</b> deb yozing."
    )


@dp.message(
    DriverReg.car_photo
)
async def dr_photo_bad(
    message: Message,
    state: FSMContext
):

    await message.answer(
        "🚗 Mashina rasmini yuboring."
    )


@dp.message(
    DriverReg.rules
)
async def dr_rules(
    message: Message,
    state: FSMContext
):

    if (
        message.text or ""
    ).strip().lower() not in {
        "ha",
        "xa",
        "yes",
        "да",
        "ҳа"
    }:

        await message.answer(
            "Qabul qilish uchun HA deb yozing."
        )

        return

    data = await state.get_data()

    uid = message.from_user.id

    if await get_driver(uid):

        await state.clear()

        await message.answer(
            "Ariza allaqachon mavjud."
        )

        return

    await db_exec(
        """
        INSERT INTO drivers(
            tg_id,
            lang,
            full_name,
            phone,
            car_model,
            plate,
            license_file_id,
            tech_file_id,
            car_photo_file_id,
            route,
            created_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            uid,
            data.get("lang", "uz"),
            data["full_name"],
            data["phone"],
            data["car_model"],
            data["plate"],
            data["license_file_id"],
            data["tech_file_id"],
            data["car_photo_file_id"],
            ROUTE_CODE,
            now()
        )
    )

    await db_exec(
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
            name=excluded.name,
            phone=excluded.phone,
            lang=excluded.lang
        """,
        (
            uid,
            data.get("lang", "uz"),
            data["full_name"],
            data["phone"],
            now()
        )
    )

    await state.clear()

    await message.answer(
        "✅ Ariza qabul qilindi. "
        "Admin tekshiradi."
    )

    await bot.send_message(
        ADMIN_ID,
        f"🚕 <b>YANGI HAYDOVCHI</b>\n\n"
        f"👤 {data['full_name']}\n"
        f"📞 {data['phone']}\n"
        f"🚗 {data['car_model']}\n"
        f"🔢 {data['plate']}\n"
        f"🆔 {uid}",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ TASDIQLASH",
                        callback_data=
                        f"approve_driver:{uid}"
                    ),
                    InlineKeyboardButton(
                        text="❌ RAD ETISH",
                        callback_data=
                        f"reject_driver:{uid}"
                    )
                ]
            ]
        )
    )

    for caption, key in [
        ("📄 PRAVA", "license_file_id"),
        ("📄 TEXPASPORT", "tech_file_id"),
        ("🚗 MASHINA", "car_photo_file_id")
    ]:

        try:

            await bot.send_photo(
                ADMIN_ID,
                data[key],
                caption=caption
            )

        except Exception as e:

            log.warning(
                "Document send failed: %s",
                e
            )


# ============================================================
# ADMIN DRIVER APPROVAL
# ============================================================

@dp.callback_query(
    F.data.startswith("approve_driver:")
)
async def approve_driver(
    callback: CallbackQuery
):

    if callback.from_user.id != ADMIN_ID:

        await callback.answer(
            "Ruxsat yo‘q",
            show_alert=True
        )

        return

    uid = int(
        callback.data.split(":")[1]
    )

    d = await get_driver(uid)

    if not d:

        await callback.answer(
            "Ariza topilmadi",
            show_alert=True
        )

        return

    await db_exec(
        """
        UPDATE drivers
        SET
            approved=1,
            blocked=0
        WHERE tg_id=?
        """,
        (uid,)
    )

    await audit(
        ADMIN_ID,
        "DRIVER_APPROVED",
        str(uid)
    )

    await callback.answer(
        "Tasdiqlandi"
    )

    try:

        await callback.message.edit_reply_markup(
            reply_markup=None
        )

    except Exception:
        pass

    await bot.send_message(
        uid,
        "🎉 Haydovchilik arizangiz tasdiqlandi!",
        reply_markup=driver_panel_kb(
            await get_driver(uid)
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
            "Ruxsat yo‘q",
            show_alert=True
        )

        return

    uid = int(
        callback.data.split(":")[1]
    )

    d = await get_driver(uid)

    if not d:

        await callback.answer(
            "Ariza topilmadi",
            show_alert=True
        )

        return

    await db_exec(
        """
        DELETE FROM drivers
        WHERE tg_id=?
          AND approved=0
        """,
        (uid,)
    )

    await audit(
        ADMIN_ID,
        "DRIVER_REJECTED",
        str(uid)
    )

    await callback.answer(
        "Rad etildi"
    )

    try:

        await callback.message.edit_reply_markup(
            reply_markup=None
        )

    except Exception:
        pass

    await bot.send_message(
        uid,
        "❌ Haydovchilik arizangiz "
        "tasdiqlanmadi.\n\n"
        "Qayta ariza topshirishingiz mumkin."
    )


# ============================================================
# DRIVER HISTORY
# ============================================================

@dp.message(
    F.text.in_(
        {
            "📜 BUYURTMALAR TARIXI",
            "📜 БУЮРТМАЛАР ТАРИХИ",
            "📜 ИСТОРИЯ ЗАКАЗОВ",
            "📜 ORDER HISTORY"
        }
    )
)
async def driver_history(
    message: Message
):

    d = await get_driver(
        message.from_user.id
    )

    if not d:
        return

    rows = await db_exec(
        """
        SELECT *
        FROM orders
        WHERE driver_tg_id=?
        ORDER BY id DESC
        LIMIT 20
        """,
        (message.from_user.id,),
        fetchall=True
    )

    if not rows:

        await message.answer(
            "📭 Tarix bo‘sh."
        )

        return

    text = (
        "📜 <b>BUYURTMALAR TARIXI</b>\n\n"
        +
        "\n".join(
            f"#{o['id']} | "
            f"{o['origin']} → "
            f"{o['destination']} | "
            f"{money(o['price'])} | "
            f"{o['status']}"
            for o in rows
        )
    )

    await message.answer(
        text[:4000]
    )


# ============================================================
# DRIVER INCOME
# ============================================================

@dp.message(
    F.text.in_(
        {
            "💰 DAROMAD",
            "💰 ДАРОМАД",
            "💰 ДОХОД",
            "💰 INCOME"
        }
    )
)
async def driver_income(
    message: Message
):

    d = await get_driver(
        message.from_user.id
    )

    if not d:
        return

    row = await db_exec(
        """
        SELECT
            COUNT(*) cnt,
            COALESCE(SUM(price),0) total
        FROM orders
        WHERE driver_tg_id=?
          AND status='FINISHED'
        """,
        (message.from_user.id,),
        fetchone=True
    )

    await message.answer(
        f"💰 <b>DAROMAD</b>\n\n"
        f"🚕 Buyurtmalar: {row['cnt']}\n"
        f"💵 Jami: <b>{money(row['total'])}</b>"
    )


# ============================================================
# DRIVER RATING
# ============================================================

@dp.message(
    F.text.in_(
        {
            "⭐ REYTING",
            "⭐ РЕЙТИНГ",
            "⭐ RATING"
        }
    )
)
async def driver_rating(
    message: Message
):

    d = await get_driver(
        message.from_user.id
    )

    if not d:
        return

    await message.answer(
        f"⭐ <b>REYTING</b>\n\n"
        f"{d['rating']:.2f}/5\n"
        f"Baholar: {d['rating_count']}"
    )


# ============================================================
# DRIVER PROFILE
# ============================================================

@dp.message(
    F.text.in_(
        {
            "👤 HAYDOVCHI PROFILI",
            "👤 ҲАЙДОВЧИ ПРОФИЛИ",
            "👤 ПРОФИЛЬ ВОДИТЕЛЯ",
            "👤 DRIVER PROFILE"
        }
    )
)
async def driver_profile(
    message: Message
):

    d = await get_driver(
        message.from_user.id
    )

    if not d:
        return

    await message.answer(
        f"👤 <b>HAYDOVCHI PROFILI</b>\n\n"
        f"F.I.Sh.: {d['full_name']}\n"
        f"📞 {d['phone']}\n"
        f"🚗 {d['car_model']}\n"
        f"🔢 {d['plate']}\n"
        f"📍 OBLIQ ↔ ANGREN\n"
        f"⭐ {d['rating']:.2f}\n"
        f"🟢 ONLINE: "
        f"{'HA' if d['online'] else 'YO‘Q'}"
    )


# ============================================================
# DRIVER BACK
# ============================================================

@dp.message(
    F.text.in_(
        {
            "⬅️ Orqaga",
            "⬅️ Орқага",
            "⬅️ НАЗАД",
            "⬅️ BACK"
        }
    )
)
async def driver_back(
    message: Message,
    state: FSMContext
):

    d = await get_driver(
        message.from_user.id
    )

    if (
        d
        and d["approved"]
        and not d["blocked"]
    ):

        await state.clear()

        await message.answer(
            "🚕 Haydovchi paneli:",
            reply_markup=driver_panel_kb(d)
        )

        return

    u = await get_user(
        message.from_user.id
    )

    await state.clear()

    if u:

        await message.answer(
            "Asosiy menyu:",
            reply_markup=main_kb(
                u["lang"]
            )
        )


# ============================================================
# CUSTOMER PROFILE
# ============================================================

@dp.message(
    F.text.in_(
        {
            "👤 Profil",
            "👤 Профиль",
            "👤 Profile"
        }
    )
)
async def profile(
    message: Message
):

    u = await get_user(
        message.from_user.id
    )

    if not u:
        return

    await message.answer(
        f"👤 <b>PROFIL</b>\n\n"
        f"Ism: {u['name']}\n"
        f"📞 {u['phone']}\n"
        f"🌐 Til: {u['lang']}"
    )


# ============================================================
# CUSTOMER HISTORY
# ============================================================

@dp.message(
    F.text.in_(
        {
            "📜 Tarix",
            "📜 Тарих",
            "📜 История",
            "📜 History"
        }
    )
)
async def history(
    message: Message
):

    rows = await db_exec(
        """
        SELECT *
        FROM orders
        WHERE customer_tg_id=?
        ORDER BY id DESC
        LIMIT 20
        """,
        (message.from_user.id,),
        fetchall=True
    )

    if not rows:

        await message.answer(
            "📭 Tarix bo‘sh."
        )

        return

    await message.answer(
        "📜 <b>TARIX</b>\n\n"
        +
        "\n".join(
            f"#{o['id']} | "
            f"{o['origin']} → "
            f"{o['destination']} | "
            f"{money(o['price'])} | "
            f"{o['status']}"
            for o in rows
        )
    )


# ============================================================
# SUPPORT
# ============================================================

@dp.message(
    F.text.in_(
        {
            "📩 Murojaat",
            "📩 Мурожаат",
            "📩 Поддержка",
            "📩 Support"
        }
    )
)
async def support(
    message: Message,
    state: FSMContext
):

    await state.set_state(
        SupportFlow.text
    )

    await message.answer(
        "📩 Murojaat yoki taklifingizni yozing:"
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
            "Batafsilroq yozing."
        )

        return

    tid = await db_exec(
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
        f"✅ Murojaat qabul qilindi.\n"
        f"Ticket: <b>MR-{tid:06d}</b>"
    )

    await bot.send_message(
        ADMIN_ID,
        f"📩 <b>MUROJAAT MR-{tid:06d}</b>\n"
        f"👤 {message.from_user.id}\n"
        f"{text}"
    )


# ============================================================
# ADMIN PANEL
# ============================================================

def admin_only(message):

    return message.from_user.id == ADMIN_ID


@dp.message(Command("admin"))
async def admin_panel(
    message: Message
):

    if not admin_only(message):
        return

    u = await db_exec(
        "SELECT COUNT(*) c FROM users",
        fetchone=True
    )

    d = await db_exec(
        "SELECT COUNT(*) c FROM drivers",
        fetchone=True
    )

    a = await db_exec(
        "SELECT COUNT(*) c FROM drivers WHERE approved=1",
        fetchone=True
    )

    on = await db_exec(
        """
        SELECT COUNT(*) c
        FROM drivers
        WHERE approved=1
          AND online=1
          AND blocked=0
        """,
        fetchone=True
    )

    o = await db_exec(
        "SELECT COUNT(*) c FROM orders",
        fetchone=True
    )

    act = await db_exec(
        """
        SELECT COUNT(*) c
        FROM orders
        WHERE status IN(
            'SEARCHING',
            'ACCEPTED',
            'CONTACTED',
            'ON_WAY',
            'PICKED_UP',
            'NO_ANSWER_WAIT'
        )
        """,
        fetchone=True
    )

    pending = await db_exec(
        """
        SELECT COUNT(*) c
        FROM drivers
        WHERE approved=0
          AND blocked=0
        """,
        fetchone=True
    )

    comp = await db_exec(
        """
        SELECT COUNT(*) c
        FROM complaints
        WHERE status='NEW'
        """,
        fetchone=True
    )

    await message.answer(
        f"👨‍💼 <b>ADMIN PANEL</b>\n\n"
        f"👥 Users: {u['c']}\n"
        f"🚕 Drivers: {d['c']}\n"
        f"⏳ Pending: {pending['c']}\n"
        f"✅ Approved: {a['c']}\n"
        f"🟢 Online: {on['c']}\n"
        f"📦 Orders: {o['c']}\n"
        f"🔥 Active: {act['c']}\n"
        f"⚠️ Complaints: {comp['c']}\n\n"
        f"<b>Buyruqlar:</b>\n"
        f"/pending\n"
        f"/drivers\n"
        f"/orders\n"
        f"/users\n"
        f"/block ID\n"
        f"/unblock ID"
    )


# ============================================================
# ADMIN PENDING
# ============================================================

@dp.message(Command("pending"))
async def admin_pending(
    message: Message
):

    if not admin_only(message):
        return

    rows = await db_exec(
        """
        SELECT *
        FROM drivers
        WHERE approved=0
          AND blocked=0
        ORDER BY id DESC
        """,
        fetchall=True
    )

    if not rows:

        await message.answer(
            "⏳ Pending ariza yo‘q."
        )

        return

    for d in rows:

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ TASDIQLASH",
                        callback_data=
                        f"approve_driver:{d['tg_id']}"
                    ),
                    InlineKeyboardButton(
                        text="❌ RAD ETISH",
                        callback_data=
                        f"reject_driver:{d['tg_id']}"
                    )
                ]
            ]
        )

        await message.answer(
            f"🚕 <b>YANGI HAYDOVCHI</b>\n\n"
            f"👤 {d['full_name']}\n"
            f"📞 {d['phone']}\n"
            f"🚗 {d['car_model']}\n"
            f"🔢 {d['plate']}\n"
            f"🆔 {d['tg_id']}\n"
            f"📍 OBLIQ ↔ ANGREN",
            reply_markup=kb
        )

        for caption, key in [
            ("🪪 PRAVA", "license_file_id"),
            ("📄 TEXPASPORT", "tech_file_id"),
            ("🚗 MASHINA", "car_photo_file_id")
        ]:

            try:

                await bot.send_photo(
                    ADMIN_ID,
                    d[key],
                    caption=caption
                )

            except Exception as e:

                log.warning(
                    "Could not send driver document %s: %s",
                    key,
                    e
                )


# ============================================================
# ADMIN DRIVERS
# ============================================================

@dp.message(Command("drivers"))
async def admin_drivers(
    message: Message
):

    if not admin_only(message):
        return

    rows = await db_exec(
        """
        SELECT *
        FROM drivers
        ORDER BY id DESC
        LIMIT 100
        """,
        fetchall=True
    )

    if not rows:

        await message.answer(
            "Haydovchi yo‘q."
        )

        return

    for d in rows:

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🚫 BLOK",
                        callback_data=
                        f"admblock:{d['tg_id']}"
                    ),
                    InlineKeyboardButton(
                        text="✅ UNBLOCK",
                        callback_data=
                        f"admunblock:{d['tg_id']}"
                    )
                ]
            ]
        )

        await message.answer(
            f"🚕 <b>{d['full_name']}</b>\n"
            f"ID: {d['tg_id']}\n"
            f"📞 {d['phone']}\n"
            f"🚗 {d['car_model']}\n"
            f"🔢 {d['plate']}\n"
            f"Tasdiq: "
            f"{'HA' if d['approved'] else 'YO‘Q'}\n"
            f"Online: "
            f"{'HA' if d['online'] else 'YO‘Q'}\n"
            f"Faol: {d['active_orders']}/4\n"
            f"⭐ {d['rating']:.2f}",
            reply_markup=kb
        )


@dp.callback_query(
    F.data.startswith("admblock:")
)
async def adm_block(
    callback: CallbackQuery
):

    if callback.from_user.id != ADMIN_ID:
        return

    uid = int(
        callback.data.split(":")[1]
    )

    await db_exec(
        """
        UPDATE drivers
        SET
            blocked=1,
            online=0
        WHERE tg_id=?
        """,
        (uid,)
    )

    await db_exec(
        """
        UPDATE users
        SET blocked=1
        WHERE tg_id=?
        """,
        (uid,)
    )

    await audit(
        ADMIN_ID,
        "DRIVER_BLOCKED",
        str(uid)
    )

    await callback.answer(
        "Bloklandi"
    )


@dp.callback_query(
    F.data.startswith("admunblock:")
)
async def adm_unblock(
    callback: CallbackQuery
):

    if callback.from_user.id != ADMIN_ID:
        return

    uid = int(
        callback.data.split(":")[1]
    )

    await db_exec(
        """
        UPDATE drivers
        SET blocked=0
        WHERE tg_id=?
        """,
        (uid,)
    )

    await db_exec(
        """
        UPDATE users
        SET blocked=0
        WHERE tg_id=?
        """,
        (uid,)
    )

    await audit(
        ADMIN_ID,
        "DRIVER_UNBLOCKED",
        str(uid)
    )

    await callback.answer(
        "Unblock qilindi"
    )


# ============================================================
# ADMIN USERS
# ============================================================

@dp.message(Command("users"))
async def admin_users(
    message: Message
):

    if not admin_only(message):
        return

    rows = await db_exec(
        """
        SELECT *
        FROM users
        ORDER BY id DESC
        LIMIT 100
        """,
        fetchall=True
    )

    if not rows:

        await message.answer(
            "User yo‘q."
        )

        return

    text = (
        "👥 <b>USERS</b>\n\n"
        +
        "\n".join(
            f"{u['tg_id']} | "
            f"{u['name']} | "
            f"{u['phone']} | "
            f"block={u['blocked']}"
            for u in rows
        )
    )

    await message.answer(
        text[:4000]
    )


# ============================================================
# ADMIN ORDERS
# ============================================================

@dp.message(Command("orders"))
async def admin_orders(
    message: Message
):

    if not admin_only(message):
        return

    rows = await db_exec(
        """
        SELECT *
        FROM orders
        ORDER BY id DESC
        LIMIT 100
        """,
        fetchall=True
    )

    if not rows:

        await message.answer(
            "Buyurtma yo‘q."
        )

        return

    text = (
        "📦 <b>ORDERS</b>\n\n"
        +
        "\n".join(
            f"#{o['id']} | "
            f"{o['origin']} → "
            f"{o['destination']} | "
            f"{money(o['price'])} | "
            f"{o['status']} | "
            f"D={o['driver_tg_id'] or '-'}"
            for o in rows
        )
    )

    await message.answer(
        text[:4000]
    )


# ============================================================
# ADMIN BLOCK
# ============================================================

@dp.message(Command("block"))
async def admin_block_user(
    message: Message
):

    if not admin_only(message):
        return

    p = (
        message.text or ""
    ).split()

    if (
        len(p) != 2
        or not p[1].isdigit()
    ):

        await message.answer(
            "/block TELEGRAM_ID"
        )

        return

    uid = int(p[1])

    await db_exec(
        """
        UPDATE users
        SET blocked=1
        WHERE tg_id=?
        """,
        (uid,)
    )

    await db_exec(
        """
        UPDATE drivers
        SET
            blocked=1,
            online=0
        WHERE tg_id=?
        """,
        (uid,)
    )

    await audit(
        ADMIN_ID,
        "USER_BLOCKED",
        str(uid)
    )

    await message.answer(
        "🚫 Bloklandi."
    )


# ============================================================
# ADMIN UNBLOCK
# ============================================================

@dp.message(Command("unblock"))
async def admin_unblock_user(
    message: Message
):

    if not admin_only(message):
        return

    p = (
        message.text or ""
    ).split()

    if (
        len(p) != 2
        or not p[1].isdigit()
    ):

        await message.answer(
            "/unblock TELEGRAM_ID"
        )

        return

    uid = int(p[1])

    await db_exec(
        """
        UPDATE users
        SET blocked=0
        WHERE tg_id=?
        """,
        (uid,)
    )

    await db_exec(
        """
        UPDATE drivers
        SET blocked=0
        WHERE tg_id=?
        """,
        (uid,)
    )

    await audit(
        ADMIN_ID,
        "USER_UNBLOCKED",
        str(uid)
    )

    await message.answer(
        "✅ Blok olib tashlandi."
    )


# ============================================================
# CANCEL
# ============================================================

@dp.message(Command("cancel"))
async def cancel_cmd(
    message: Message,
    state: FSMContext
):

    await state.clear()

    u = await get_user(
        message.from_user.id
    )

    await message.answer(
        "❌ Jarayon bekor qilindi.",
        reply_markup=(
            main_kb(u["lang"])
            if u
            else language_kb()
        )
    )


# ============================================================
# FALLBACK
# ============================================================

@dp.message()
async def fallback(
    message: Message
):

    u = await get_user(
        message.from_user.id
    )

    if not u:

        await message.answer(
            "Avval /start bosing."
        )

        return

    await message.answer(
        "🤖 Menyudan foydalaning.",
        reply_markup=main_kb(
            u["lang"]
        )
    )


# ============================================================
# STARTUP
# ============================================================

async def main():

    init_db()

    log.info(
        "TAXI BOR MI? V1 starting"
    )

    await bot.delete_webhook(
        drop_pending_updates=True
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":
    asyncio.run(main())
