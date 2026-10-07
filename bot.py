import os
import sqlite3
import asyncio
import logging
from datetime import datetime, timedelta
from html import escape

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
    InlineKeyboardButton
)

# ============================================================
# TAXI BOR MI? — ALBATTA BOR!
# OBLIQ ↔ ANGREN
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.lstrip("-").isdigit() else 0

DB_PATH = os.getenv("DB_PATH", "taxi_bor_mi.db").strip() or "taxi_bor_mi.db"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN Railway Variables ichida yo'q.")

if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID Railway Variables ichida noto'g'ri yoki yo'q.")

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

db = sqlite3.connect(
    DB_PATH,
    check_same_thread=False
)

db.row_factory = sqlite3.Row
db_lock = asyncio.Lock()


# ============================================================
# CONSTANTS
# ============================================================

ROUTE = "OBLIQ_ANGREN"

MAX_ACTIVE = 4

CLAIM_MINUTES = 2

NO_ANSWER_SECONDS = 60

MIN_PRICE = 5000

MAX_PRICE = 1_000_000


# ============================================================
# LANGUAGES
# ============================================================

LANGS = {
    "uz": "🇺🇿 O‘zbekcha",
    "uzc": "🇺🇿 Ўзбекча",
    "ru": "🇷🇺 Русский",
    "en": "🇬🇧 English"
}


# ============================================================
# TRANSLATIONS
# ============================================================

T = {

    "uz": {

        "welcome":
            "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
            "📍 <b>OBLIQ ↔ ANGREN</b>\n"
            "👤 Yo‘lovchi • 📦 Dastavka",

        "choose_lang":
            "🌐 Tilni tanlang:",

        "name":
            "👤 Ismingizni kiriting:",

        "phone":
            "📱 Telefon raqamingizni yuboring:",

        "send_phone":
            "📱 Raqamni yuborish",

        "registered":
            "✅ Ro‘yxatdan o‘tish yakunlandi!",

        "passenger":
            "👤 Yo‘lovchi",

        "profile":
            "👤 Profil",

        "history":
            "📜 Tarix",

        "support":
            "📩 Murojaat",

        "driver":
            "🚕 Haydovchi",

        "back":
            "⬅️ Orqaga",

        "change_lang":
            "🌐 Tilni almashtirish",

        "select_people":
            "👥 Necha kishi?",

        "one":
            "1️⃣ 1 kishi",

        "two":
            "2️⃣ 2 kishi",

        "three":
            "3️⃣ 3 kishi",

        "four":
            "4️⃣ 4 kishi",

        "delivery":
            "📦 DASTAVKA",

        "origin":
            "📍 <b>QAYERDAN?</b>\n"
            "Manzilni yozing.\n"
            "Masalan: <b>5/5 dan</b>",

        "destination":
            "🏁 <b>QAYERGA?</b>\n"
            "Manzilni yozing.\n"
            "Masalan: <b>Kaltsoga</b>",

        "gps":
            "📍 GPS yuborish",

        "nogps":
            "⏭ GPSsiz davom etish",

        "gps_received":
            "📍 GPS qabul qilindi.",

        "gps_optional":
            "📍 GPS ixtiyoriy. "
            "Yuborsangiz haydovchiga xaritadagi "
            "joylashuvingiz ham boradi.",

        "price":
            "💰 Narxni tanlang:",

        "p5":
            "5 000 so‘m",

        "p10":
            "10 000 so‘m",

        "p15":
            "15 000 so‘m",

        "p20":
            "20 000 so‘m",

        "other":
            "✍️ Boshqa narx",

        "custom_price":
            "✍️ Narxni kiriting "
            "(5 000–1 000 000 so‘m):",

        "bad_price":
            "❗ Narx 5 000 dan 1 000 000 so‘mgacha "
            "bo‘lishi kerak.",

        "confirm":
            "🚕 <b>BUYURTMANI TEKSHIRING</b>",

        "order":
            "BUYURTMA",

        "send_order":
            "✅ BUYURTMA BERISH",

        "edit":
            "✏️ O‘ZGARTIRISH",

        "cancel":
            "❌ BEKOR QILISH",

        "search":
            "🔎 Haydovchi qidirilmoqda...",

        "no_driver":
            "😔 Hozircha mos ONLINE haydovchi topilmadi. "
            "Keyinroq qayta urinib ko‘ring.",

        "driver_reg":
            "🚕 <b>Haydovchi ro‘yxatdan o‘tishi</b>",

        "fio":
            "F.I.Sh. ni kiriting:",

        "car":
            "🚗 Mashina rusmini kiriting:",

        "plate":
            "🔢 Davlat raqamini kiriting:",

        "license":
            "🪪 Prava/litsenziya rasmini yuboring:",

        "tech":
            "📄 Texnik pasport rasmini yuboring:",

        "carphoto":
            "📷 Mashina rasmini yuboring:",

        "rules":
            "📋 <b>Qoidalar:</b>\n"
            "• Buyurtmani olgach mijoz bilan bog‘laning.\n"
            "• Mijoz javob bermasa maxsus 60 soniyalik tartib ishlaydi.\n"
            "• Spam va yolg‘on ma’lumot taqiqlanadi.\n"
            "• Hujjatlar admin tomonidan tekshiriladi.\n\n"
            "Qabul qilasizmi?",

        "accept_rules":
            "✅ Qoidalarni qabul qilaman",

        "wait_approval":
            "⏳ Arizangiz admin tasdig‘ini kutmoqda.",

        "approved":
            "✅ Siz tasdiqlandingiz. "
            "Haydovchi paneli ochildi.",

        "online":
            "🟢 ONLINE",

        "offline":
            "⚪ OFFLINE",

        "active":
            "🚕 Faol buyurtmalar",

        "income":
            "💰 Daromad",

        "rating":
            "⭐ Reyting",

        "driver_profile":
            "👤 Profil",

        "driver_history":
            "📜 Buyurtmalar tarixi",

        "take":
            "✅ BUYURTMANI OLISH",

        "decline":
            "❌ RAD ETISH",

        "finish":
            "🏁 BUYURTMANI YAKUNLASH",

        "no_answer":
            "📵 MIJOZ JAVOB BERMADI",

        "no_answer_msg":
            "🚕 Haydovchi siz bilan bog‘lana olmadi.\n\n"
            "Sizga hali ham mashina kerakmi?\n"
            "⏱ 1 daqiqa ichida javob bering.",

        "yes":
            "✅ HA, KERAK",

        "no":
            "❌ YO‘Q, KERAK EMAS",

        "rating_ask":
            "⭐ Haydovchiga baho bering (1–5):",

        "thanks":
            "Rahmat!",

        "blocked":
            "🚫 Akkauntingiz bloklangan.",

        "support_ask":
            "📩 Murojaatingizni yozing:",

        "support_sent":
            "✅ Murojaat yuborildi."
    },


    # ========================================================
    # UZBEK CYRILLIC
    # ========================================================

    "uzc": {

        "welcome":
            "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
            "📍 <b>ОБЛИҚ ↔ АНГРЕН</b>\n"
            "👤 Йўловчи • 📦 Даставка",

        "choose_lang":
            "🌐 Тилни танланг:",

        "name":
            "👤 Исмингизни киритинг:",

        "phone":
            "📱 Телефон рақамингизни юборинг:",

        "send_phone":
            "📱 Рақамни юбориш",

        "registered":
            "✅ Рўйхатдан ўтиш якунланди!",

        "passenger":
            "👤 Йўловчи",

        "profile":
            "👤 Профиль",

        "history":
            "📜 Тарих",

        "support":
            "📩 Мурожаат",

        "driver":
            "🚕 Ҳайдовчи",

        "back":
            "⬅️ Орқага",

        "change_lang":
            "🌐 Тилни алмаштириш",

        "select_people":
            "👥 Неча киши?",

        "one":
            "1️⃣ 1 киши",

        "two":
            "2️⃣ 2 киши",

        "three":
            "3️⃣ 3 киши",

        "four":
            "4️⃣ 4 киши",

        "delivery":
            "📦 ДАСТАВКА",

        "origin":
            "📍 <b>ҚАЕРДАН?</b>\n"
            "Манзилни ёзинг.\n"
            "Масалан: <b>5/5 дан</b>",

        "destination":
            "🏁 <b>ҚАЕРГА?</b>\n"
            "Манзилни ёзинг.\n"
            "Масалан: <b>Калтсога</b>",

        "gps":
            "📍 GPS юбориш",

        "nogps":
            "⏭ GPSсиз давом этиш",

        "gps_received":
            "📍 GPS қабул қилинди.",

        "gps_optional":
            "📍 GPS ихтиёрий.",

        "price":
            "💰 Нарҳни танланг:",

        "p5":
            "5 000 сўм",

        "p10":
            "10 000 сўм",

        "p15":
            "15 000 сўм",

        "p20":
            "20 000 сўм",

        "other":
            "✍️ Бошқа нарҳ",

        "custom_price":
            "✍️ Нарҳни киритинг "
            "(5 000–1 000 000):",

        "bad_price":
            "❗ Нарҳ 5 000–1 000 000 сўм "
            "оралиғида бўлиши керак.",

        "confirm":
            "🚕 <b>БУЮРТМАНИ ТЕКШИРИНГ</b>",

        "order":
            "БУЮРТМА",

        "send_order":
            "✅ БУЮРТМА БЕРИШ",

        "edit":
            "✏️ ЎЗГАРТИРИШ",

        "cancel":
            "❌ БЕКОР ҚИЛИШ",

        "search":
            "🔎 Ҳайдовчи қидирилмоқда...",

        "no_driver":
            "😔 Ҳозирча ONLINE ҳайдовчи топилмади.",

        "driver_reg":
            "🚕 <b>Ҳайдовчи рўйхатдан ўтиши</b>",

        "fio":
            "Ф.И.Ш. ни киритинг:",

        "car":
            "🚗 Машина русумини киритинг:",

        "plate":
            "🔢 Давлат рақамини киритинг:",

        "license":
            "🪪 Права/лицензия расмини юборинг:",

        "tech":
            "📄 Техпаспорт расмини юборинг:",

        "carphoto":
            "📷 Машина расмини юборинг:",

        "rules":
            "📋 Қоидаларни қабул қиласизми?",

        "accept_rules":
            "✅ Қоидаларни қабул қиламан",

        "wait_approval":
            "⏳ Аризангиз админ тасдиғини кутмоқда.",

        "approved":
            "✅ Сиз тасдиқландингиз.",

        "online":
            "🟢 ONLINE",

        "offline":
            "⚪ OFFLINE",

        "active":
            "🚕 Фаол буюртмалар",

        "income":
            "💰 Даромад",

        "rating":
            "⭐ Рейтинг",

        "driver_profile":
            "👤 Профиль",

        "driver_history":
            "📜 Буюртмалар тарихи",

        "take":
            "✅ БУЮРТМАНИ ОЛИШ",

        "decline":
            "❌ РАД ЭТИШ",

        "finish":
            "🏁 БУЮРТМАНИ ЯКУНЛАШ",

        "no_answer":
            "📵 МИЖОЗ ЖАВОБ БЕРМАДИ",

        "no_answer_msg":
            "🚕 Ҳайдовчи сиз билан боғлана олмади.\n\n"
            "Сизга ҳали ҳам машина керакми?\n"
            "⏱ 1 дақиқа ичида жавоб беринг.",

        "yes":
            "✅ ҲА, КЕРАК",

        "no":
            "❌ ЙЎҚ, КЕРАК ЭМАС",

        "rating_ask":
            "⭐ Ҳайдовчига баҳо беринг (1–5):",

        "thanks":
            "Раҳмат!",

        "blocked":
            "🚫 Аккаунтингиз блокланган.",

        "support_ask":
            "📩 Мурожаатингизни ёзинг:",

        "support_sent":
            "✅ Мурожаат юборилди."
    },


    # ========================================================
    # RUSSIAN
    # ========================================================

    "ru": {

        "welcome":
            "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
            "📍 <b>ОБЛИК ↔ АНГРЕН</b>\n"
            "👤 Пассажир • 📦 Доставка",

        "choose_lang":
            "🌐 Выберите язык:",

        "name":
            "👤 Введите имя:",

        "phone":
            "📱 Отправьте номер телефона:",

        "send_phone":
            "📱 Отправить номер",

        "registered":
            "✅ Регистрация завершена!",

        "passenger":
            "👤 Пассажир",

        "profile":
            "👤 Профиль",

        "history":
            "📜 История",

        "support":
            "📩 Поддержка",

        "driver":
            "🚕 Водитель",

        "back":
            "⬅️ Назад",

        "change_lang":
            "🌐 Сменить язык",

        "select_people":
            "👥 Сколько человек?",

        "one":
            "1️⃣ 1 человек",

        "two":
            "2️⃣ 2 человека",

        "three":
            "3️⃣ 3 человека",

        "four":
            "4️⃣ 4 человека",

        "delivery":
            "📦 ДОСТАВКА",

        "origin":
            "📍 <b>ОТКУДА?</b>\n"
            "Введите адрес.\n"
            "Например: <b>5/5</b>",

        "destination":
            "🏁 <b>КУДА?</b>\n"
            "Введите адрес.\n"
            "Например: <b>Калтса</b>",

        "gps":
            "📍 Отправить GPS",

        "nogps":
            "⏭ Продолжить без GPS",

        "gps_received":
            "📍 GPS получен.",

        "gps_optional":
            "📍 GPS необязателен.",

        "price":
            "💰 Выберите цену:",

        "p5":
            "5 000 сум",

        "p10":
            "10 000 сум",

        "p15":
            "15 000 сум",

        "p20":
            "20 000 сум",

        "other":
            "✍️ Другая цена",

        "custom_price":
            "✍️ Введите цену "
            "(5 000–1 000 000):",

        "bad_price":
            "❗ Цена должна быть от "
            "5 000 до 1 000 000 сум.",

        "confirm":
            "🚕 <b>ПРОВЕРЬТЕ ЗАКАЗ</b>",

        "order":
            "ЗАКАЗ",

        "send_order":
            "✅ ЗАКАЗАТЬ",

        "edit":
            "✏️ ИЗМЕНИТЬ",

        "cancel":
            "❌ ОТМЕНА",

        "search":
            "🔎 Ищем водителя...",

        "no_driver":
            "😔 Пока нет подходящего ONLINE водителя.",

        "driver_reg":
            "🚕 <b>Регистрация водителя</b>",

        "fio":
            "Введите Ф.И.О.:",

        "car":
            "🚗 Введите модель автомобиля:",

        "plate":
            "🔢 Введите госномер:",

        "license":
            "🪪 Отправьте фото прав/лицензии:",

        "tech":
            "📄 Отправьте фото техпаспорта:",

        "carphoto":
            "📷 Отправьте фото автомобиля:",

        "rules":
            "📋 Правила:\n"
            "После принятия заказа свяжитесь с клиентом.\n"
            "Если клиент не отвечает, действует "
            "процедура 60 секунд.\n\n"
            "Принять правила?",

        "accept_rules":
            "✅ Принимаю правила",

        "wait_approval":
            "⏳ Заявка ожидает подтверждения администратора.",

        "approved":
            "✅ Вы одобрены.",

        "online":
            "🟢 ONLINE",

        "offline":
            "⚪ OFFLINE",

        "active":
            "🚕 Активные заказы",

        "income":
            "💰 Доход",

        "rating":
            "⭐ Рейтинг",

        "driver_profile":
            "👤 Профиль",

        "driver_history":
            "📜 История заказов",

        "take":
            "✅ ВЗЯТЬ ЗАКАЗ",

        "decline":
            "❌ ОТКАЗАТЬСЯ",

        "finish":
            "🏁 ЗАВЕРШИТЬ ЗАКАЗ",

        "no_answer":
            "📵 КЛИЕНТ НЕ ОТВЕЧАЕТ",

        "no_answer_msg":
            "🚕 Водитель не смог связаться с вами.\n\n"
            "Вам всё ещё нужна машина?\n"
            "⏱ Ответьте в течение 1 минуты.",

        "yes":
            "✅ ДА, НУЖНА",

        "no":
            "❌ НЕТ",

        "rating_ask":
            "⭐ Оцените водителя (1–5):",

        "thanks":
            "Спасибо!",

        "blocked":
            "🚫 Ваш аккаунт заблокирован.",

        "support_ask":
            "📩 Напишите обращение:",

        "support_sent":
            "✅ Обращение отправлено."
    },


    # ========================================================
    # ENGLISH
    # ========================================================

    "en": {

        "welcome":
            "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
            "📍 <b>OBLIQ ↔ ANGREN</b>\n"
            "👤 Passenger • 📦 Delivery",

        "choose_lang":
            "🌐 Choose language:",

        "name":
            "👤 Enter your name:",

        "phone":
            "📱 Send your phone number:",

        "send_phone":
            "📱 Send number",

        "registered":
            "✅ Registration completed!",

        "passenger":
            "👤 Passenger",

        "profile":
            "👤 Profile",

        "history":
            "📜 History",

        "support":
            "📩 Support",

        "driver":
            "🚕 Driver",

        "back":
            "⬅️ Back",

        "change_lang":
            "🌐 Change language",

        "select_people":
            "👥 How many people?",

        "one":
            "1️⃣ 1 person",

        "two":
            "2️⃣ 2 people",

        "three":
            "3️⃣ 3 people",

        "four":
            "4️⃣ 4 people",

        "delivery":
            "📦 DELIVERY",

        "origin":
            "📍 <b>FROM?</b>\n"
            "Enter address.\n"
            "Example: <b>5/5</b>",

        "destination":
            "🏁 <b>TO?</b>\n"
            "Enter address.\n"
            "Example: <b>Kaltsa</b>",

        "gps":
            "📍 Send GPS",

        "nogps":
            "⏭ Continue without GPS",

        "gps_received":
            "📍 GPS received.",

        "gps_optional":
            "📍 GPS is optional.",

        "price":
            "💰 Choose price:",

        "p5":
            "5,000 UZS",

        "p10":
            "10,000 UZS",

        "p15":
            "15,000 UZS",

        "p20":
            "20,000 UZS",

        "other":
            "✍️ Other price",

        "custom_price":
            "✍️ Enter price "
            "(5,000–1,000,000):",

        "bad_price":
            "❗ Price must be "
            "5,000–1,000,000 UZS.",

        "confirm":
            "🚕 <b>CHECK YOUR ORDER</b>",

        "order":
            "ORDER",

        "send_order":
            "✅ PLACE ORDER",

        "edit":
            "✏️ EDIT",

        "cancel":
            "❌ CANCEL",

        "search":
            "🔎 Looking for a driver...",

        "no_driver":
            "😔 No suitable ONLINE driver is available yet.",

        "driver_reg":
            "🚕 <b>Driver registration</b>",

        "fio":
            "Enter full name:",

        "car":
            "🚗 Enter car model:",

        "plate":
            "🔢 Enter plate number:",

        "license":
            "🪪 Send driver license photo:",

        "tech":
            "📄 Send vehicle registration photo:",

        "carphoto":
            "📷 Send car photo:",

        "rules":
            "📋 Rules:\n"
            "Contact the customer after accepting.\n"
            "If the customer does not answer, "
            "the 60-second procedure applies.\n\n"
            "Accept?",

        "accept_rules":
            "✅ Accept rules",

        "wait_approval":
            "⏳ Your application is awaiting admin approval.",

        "approved":
            "✅ You are approved.",

        "online":
            "🟢 ONLINE",

        "offline":
            "⚪ OFFLINE",

        "active":
            "🚕 Active orders",

        "income":
            "💰 Income",

        "rating":
            "⭐ Rating",

        "driver_profile":
            "👤 Profile",

        "driver_history":
            "📜 Order history",

        "take":
            "✅ TAKE ORDER",

        "decline":
            "❌ DECLINE",

        "finish":
            "🏁 FINISH ORDER",

        "no_answer":
            "📵 CUSTOMER DID NOT ANSWER",

        "no_answer_msg":
            "🚕 The driver could not reach you.\n\n"
            "Do you still need a car?\n"
            "⏱ Reply within 1 minute.",

        "yes":
            "✅ YES, I NEED IT",

        "no":
            "❌ NO",

        "rating_ask":
            "⭐ Rate the driver (1–5):",

        "thanks":
            "Thank you!",

        "blocked":
            "🚫 Your account is blocked.",

        "support_ask":
            "📩 Write your request:",

        "support_sent":
            "✅ Request sent."
    }
}


def tr(lang, key):
    return T.get(
        lang,
        T["uz"]
    ).get(
        key,
        T["uz"].get(key, key)
    )


def now():
    return datetime.utcnow().replace(
        microsecond=0
    ).isoformat()


def money(n):
    return f"{int(n):,}".replace(",", " ") + " so‘m"


# ============================================================
# STATES
# ============================================================

class Reg(StatesGroup):
    lang = State()
    name = State()
    phone = State()


class Order(StatesGroup):
    people = State()
    origin = State()
    destination = State()
    gps = State()
    price = State()
    confirm = State()


class DriverReg(StatesGroup):
    name = State()
    phone = State()
    car = State()
    plate = State()
    license = State()
    tech = State()
    car_photo = State()
    rules = State()


class Support(StatesGroup):
    text = State()


# ============================================================
# DATABASE
# ============================================================

def init_db():
    db.executescript("""
    PRAGMA journal_mode=WAL;

    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER UNIQUE NOT NULL,
        role TEXT DEFAULT 'customer',
        lang TEXT DEFAULT 'uz',
        name TEXT DEFAULT '',
        phone TEXT DEFAULT '',
        blocked INTEGER DEFAULT 0,
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
        route TEXT DEFAULT 'OBLIQ_ANGREN',
        approved INTEGER DEFAULT 0,
        online INTEGER DEFAULT 0,
        active_orders INTEGER DEFAULT 0,
        rating REAL DEFAULT 5,
        rating_count INTEGER DEFAULT 0,
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
        status TEXT NOT NULL,
        created_at TEXT NOT NULL,
        accepted_at TEXT,
        finished_at TEXT,
        claim_deadline TEXT,
        no_answer_deadline TEXT,
        no_answer_driver_tg_id INTEGER
    );

    CREATE TABLE IF NOT EXISTS order_declines (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        driver_tg_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(order_id, driver_tg_id)
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

    CREATE TABLE IF NOT EXISTS support_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        status TEXT DEFAULT 'OPEN',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS audit_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_tg_id INTEGER,
        action TEXT,
        details TEXT,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_orders_status
    ON orders(status);

    CREATE INDEX IF NOT EXISTS idx_orders_customer
    ON orders(customer_tg_id);

    CREATE INDEX IF NOT EXISTS idx_orders_driver
    ON orders(driver_tg_id);
    """)

    db.commit()


async def q(
    sql,
    params=(),
    fetchone=False,
    fetch=False,
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


async def user(tg):
    return await q(
        "SELECT * FROM users WHERE tg_id=?",
        (tg,),
        fetchone=True
    )


async def driver(tg):
    return await q(
        "SELECT * FROM drivers WHERE tg_id=?",
        (tg,),
        fetchone=True
    )


async def audit(actor, action, details=""):
    await q(
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
# KEYBOARDS
# ============================================================

def main_kb(lang):
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=tr(lang, "passenger")),
                KeyboardButton(text=tr(lang, "driver"))
            ],
            [
                KeyboardButton(text=tr(lang, "profile")),
                KeyboardButton(text=tr(lang, "history"))
            ],
            [
                KeyboardButton(text=tr(lang, "support")),
                KeyboardButton(text=tr(lang, "change_lang"))
            ]
        ],
        resize_keyboard=True
    )


def lang_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=LANGS["uz"]),
                KeyboardButton(text=LANGS["uzc"])
            ],
            [
                KeyboardButton(text=LANGS["ru"]),
                KeyboardButton(text=LANGS["en"])
            ]
        ],
        resize_keyboard=True
    )


def phone_kb(lang):
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=tr(lang, "send_phone"),
                    request_contact=True
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True
    )


def people_kb(lang):
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=tr(lang, "one")),
                KeyboardButton(text=tr(lang, "two"))
            ],
            [
                KeyboardButton(text=tr(lang, "three")),
                KeyboardButton(text=tr(lang, "four"))
            ],
            [
                KeyboardButton(text=tr(lang, "delivery"))
            ],
            [
                KeyboardButton(text=tr(lang, "back"))
            ]
        ],
        resize_keyboard=True
    )


def gps_kb(lang):
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=tr(lang, "gps"),
                    request_location=True
                )
            ],
            [
                KeyboardButton(text=tr(lang, "nogps"))
            ],
            [
                KeyboardButton(text=tr(lang, "back"))
            ]
        ],
        resize_keyboard=True
    )


def price_kb(lang):
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=tr(lang, "p5")),
                KeyboardButton(text=tr(lang, "p10"))
            ],
            [
                KeyboardButton(text=tr(lang, "p15")),
                KeyboardButton(text=tr(lang, "p20"))
            ],
            [
                KeyboardButton(text=tr(lang, "other"))
            ],
            [
                KeyboardButton(text=tr(lang, "back"))
            ]
        ],
        resize_keyboard=True
    )


def confirm_kb(lang):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tr(lang, "send_order"),
                    callback_data="ord:confirm"
                )
            ],
            [
                InlineKeyboardButton(
                    text=tr(lang, "edit"),
                    callback_data="ord:edit"
                ),
                InlineKeyboardButton(
                    text=tr(lang, "cancel"),
                    callback_data="ord:cancel"
                )
            ]
        ]
    )


def driver_offer_kb(oid, lang):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tr(lang, "take"),
                    callback_data=f"claim:{oid}"
                ),
                InlineKeyboardButton(
                    text=tr(lang, "decline"),
                    callback_data=f"decline:{oid}"
                )
            ]
        ]
    )


def driver_panel_kb(lang, d):
    online = bool(d["online"])

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=tr(lang, "offline")
                    if online
                    else tr(lang, "online")
                )
            ],
            [
                KeyboardButton(text=tr(lang, "active")),
                KeyboardButton(text=tr(lang, "driver_history"))
            ],
            [
                KeyboardButton(text=tr(lang, "income")),
                KeyboardButton(text=tr(lang, "rating"))
            ],
            [
                KeyboardButton(text=tr(lang, "driver_profile")),
                KeyboardButton(text=tr(lang, "support"))
            ],
            [
                KeyboardButton(text=tr(lang, "back"))
            ]
        ],
        resize_keyboard=True
    )


def admin_kb():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 STATISTIKA",
                    callback_data="adm:stats"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚕 HAYDOVCHILAR",
                    callback_data="adm:drivers"
                ),
                InlineKeyboardButton(
                    text="⏳ TASDIQLASH",
                    callback_data="adm:pending"
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 MIJOZLAR",
                    callback_data="adm:users"
                ),
                InlineKeyboardButton(
                    text="📦 BUYURTMALAR",
                    callback_data="adm:orders"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🟢 ONLINE",
                    callback_data="adm:online"
                ),
                InlineKeyboardButton(
                    text="🚫 BLOK",
                    callback_data="adm:blocked"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📩 MUROJAATLAR",
                    callback_data="adm:support"
                )
            ]
        ]
    )


async def ensure(message):
    u = await user(message.from_user.id)

    if not u:
        return None

    if u["blocked"]:
        await message.answer(
            tr(u["lang"], "blocked")
        )
        return None

    return u
    # ---------- REGISTRATION / LANGUAGE ----------
    await state.set_state(Reg.lang)
    await m.answer(
        "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
        + tr("uz", "choose_lang"),
        reply_markup=lang_kb()
    )


LANG_TEXT = {v: k for k, v in LANGS.items()}


@dp.message(Reg.lang)
async def reg_lang(m: Message, state: FSMContext):
    lang = LANG_TEXT.get((m.text or "").strip())

    if not lang:
        await m.answer(
            "🌐 Tilni tugmadan tanlang.",
            reply_markup=lang_kb()
        )
        return

    data = await state.get_data()

    if data.get("changing"):
        await q(
            "UPDATE users SET lang=? WHERE tg_id=?",
            (lang, m.from_user.id)
        )

        await state.clear()

        await m.answer(
            tr(lang, "welcome") + "\n\nXizmatni tanlang:",
            reply_markup=main_kb(lang)
        )
        return

    await state.update_data(lang=lang)
    await state.set_state(Reg.name)

    await m.answer(
        tr(lang, "name"),
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(
                        text=tr(lang, "back")
                    )
                ]
            ],
            resize_keyboard=True
        )
    )


@dp.message(Reg.name)
async def reg_name(m: Message, state: FSMContext):
    data = await state.get_data()
    lang = data.get("lang", "uz")
    name = (m.text or "").strip()

    if len(name) < 2:
        await m.answer(tr(lang, "name"))
        return

    await state.update_data(name=name)
    await state.set_state(Reg.phone)

    await m.answer(
        tr(lang, "phone"),
        reply_markup=phone_kb(lang)
    )


@dp.message(Reg.phone, F.contact)
async def reg_contact(
    m: Message,
    state: FSMContext
):
    await finish_reg(
        m,
        state,
        m.contact.phone_number
    )


@dp.message(Reg.phone)
async def reg_phone(
    m: Message,
    state: FSMContext
):
    await finish_reg(
        m,
        state,
        (m.text or "").strip()
    )


async def finish_reg(
    m,
    state,
    phone
):
    data = await state.get_data()
    lang = data.get("lang", "uz")

    digits = "".join(
        c for c in phone
        if c.isdigit()
    )

    if len(digits) < 7:
        await m.answer(
            "📱 Telefon raqami noto‘g‘ri."
        )
        return

    await q(
        """
        INSERT OR REPLACE INTO users
        (tg_id, role, lang, name, phone, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            m.from_user.id,
            "customer",
            lang,
            data.get("name", ""),
            phone,
            now()
        )
    )

    await state.clear()

    await m.answer(
        tr(lang, "registered")
        + "\n\n"
        + tr(lang, "welcome"),
        reply_markup=main_kb(lang)
    )


# ---------- CUSTOMER ORDER ----------

async def begin_order(
    m,
    state
):
    u = await ensure(m)

    if not u:
        return

    await state.clear()

    await state.update_data(
        lang=u["lang"]
    )

    await state.set_state(
        Order.people
    )

    await m.answer(
        tr(u["lang"], "select_people"),
        reply_markup=people_kb(
            u["lang"]
        )
    )


@dp.message(
    F.text.in_(
        {
            T[x]["passenger"]
            for x in T
        }
    )
)
async def passenger(
    m: Message,
    state: FSMContext
):
    await begin_order(
        m,
        state
    )


@dp.message(Order.people)
async def people(
    m: Message,
    state: FSMContext
):
    d = await state.get_data()

    lang = d.get(
        "lang",
        "uz"
    )

    text = (
        m.text or ""
    ).strip()

    if text == tr(
        lang,
        "back"
    ):
        await state.clear()

        u = await user(
            m.from_user.id
        )

        await m.answer(
            tr(
                lang,
                "welcome"
            ),
            reply_markup=main_kb(
                lang
            )
        )
        return

    mapping = {
        tr(lang, "one"): 1,
        tr(lang, "two"): 2,
        tr(lang, "three"): 3,
        tr(lang, "four"): 4,
        tr(lang, "delivery"): 0
    }

    if text not in mapping:
        await m.answer(
            tr(
                lang,
                "select_people"
            ),
            reply_markup=people_kb(
                lang
            )
        )
        return

    count = mapping[text]

    await state.update_data(
        service=(
            "DELIVERY"
            if count == 0
            else "PASSENGER"
        ),
        passengers=count
    )

    await state.set_state(
        Order.origin
    )

    await m.answer(
        tr(
            lang,
            "origin"
        )
    )


@dp.message(Order.origin)
async def origin(
    m: Message,
    state: FSMContext
):
    d = await state.get_data()

    lang = d.get(
        "lang",
        "uz"
    )

    s = (
        m.text or ""
    ).strip()

    if len(s) < 2:
        await m.answer(
            tr(
                lang,
                "origin"
            )
        )
        return

    await state.update_data(
        origin=s
    )

    await state.set_state(
        Order.destination
    )

    await m.answer(
        tr(
            lang,
            "destination"
        )
    )


@dp.message(Order.destination)
async def destination(
    m: Message,
    state: FSMContext
):
    d = await state.get_data()

    lang = d.get(
        "lang",
        "uz"
    )

    s = (
        m.text or ""
    ).strip()

    if len(s) < 2:
        await m.answer(
            tr(
                lang,
                "destination"
            )
        )
        return

    await state.update_data(
        destination=s
    )

    await state.set_state(
        Order.gps
    )

    await m.answer(
        tr(
            lang,
            "gps_optional"
        ),
        reply_markup=gps_kb(
            lang
        )
    )


@dp.message(
    Order.gps,
    F.location
)
async def gps(
    m: Message,
    state: FSMContext
):
    d = await state.get_data()

    lang = d.get(
        "lang",
        "uz"
    )

    await state.update_data(
        lat=m.location.latitude,
        lon=m.location.longitude,
        gps=True
    )

    await state.set_state(
        Order.price
    )

    await m.answer(
        tr(
            lang,
            "gps_received"
        )
        + "\n\n"
        + tr(
            lang,
            "price"
        ),
        reply_markup=price_kb(
            lang
        )
    )


@dp.message(Order.gps)
async def no_gps(
    m: Message,
    state: FSMContext
):
    d = await state.get_data()

    lang = d.get(
        "lang",
        "uz"
    )

    text = (
        m.text or ""
    ).strip()

    if text == tr(
        lang,
        "nogps"
    ):
        await state.update_data(
            gps=False,
            lat=None,
            lon=None
        )

        await state.set_state(
            Order.price
        )

        await m.answer(
            tr(
                lang,
                "price"
            ),
            reply_markup=price_kb(
                lang
            )
        )
        return

    if text == tr(
        lang,
        "back"
    ):
        await state.set_state(
            Order.destination
        )

        await m.answer(
            tr(
                lang,
                "destination"
            )
        )
        return

    await m.answer(
        tr(
            lang,
            "gps_optional"
        ),
        reply_markup=gps_kb(
            lang
        )
    )


PRICE_MAP = {
    "p5": 5000,
    "p10": 10000,
    "p15": 15000,
    "p20": 20000
}


@dp.message(Order.price)
async def price(
    m: Message,
    state: FSMContext
):
    d = await state.get_data()

    lang = d.get(
        "lang",
        "uz"
    )

    text = (
        m.text or ""
    ).strip()

    if text == tr(
        lang,
        "back"
    ):
        await state.set_state(
            Order.gps
        )

        await m.answer(
            tr(
                lang,
                "gps_optional"
            ),
            reply_markup=gps_kb(
                lang
            )
        )
        return

    vals = {
        tr(lang, "p5"): 5000,
        tr(lang, "p10"): 10000,
        tr(lang, "p15"): 15000,
        tr(lang, "p20"): 20000
    }

    if text in vals:
        p = vals[text]

    elif text == tr(
        lang,
        "other"
    ):
        await state.update_data(
            wait_custom=True
        )

        await m.answer(
            tr(
                lang,
                "custom_price"
            )
        )
        return

    elif d.get("wait_custom"):
        raw = "".join(
            c for c in text
            if c.isdigit()
        )

        p = (
            int(raw)
            if raw
            else 0
        )

    else:
        await m.answer(
            tr(
                lang,
                "price"
            ),
            reply_markup=price_kb(
                lang
            )
        )
        return

    if (
        p < MIN_PRICE
        or p > MAX_PRICE
    ):
        await m.answer(
            tr(
                lang,
                "bad_price"
            )
        )
        return

    await state.update_data(
        price=p,
        wait_custom=False
    )

    await state.set_state(
        Order.confirm
    )

    await send_confirmation(
        m,
        state
    )


async def send_confirmation(
    m,
    state
):
    d = await state.get_data()

    lang = d.get(
        "lang",
        "uz"
    )

    svc = (
        "📦 DASTAVKA"
        if d["service"] == "DELIVERY"
        else "👤 Yo‘lovchi"
    )

    pax = (
        ""
        if d["service"] == "DELIVERY"
        else (
            f"\n👥 Yo‘lovchilar: "
            f"<b>{d['passengers']} kishi</b>"
        )
    )

    gps_status = (
        "✅ Yuborilgan"
        if d.get("gps")
        else "❌ Yuborilmagan"
    )

    text = (
        f"{tr(lang, 'confirm')}\n\n"
        f"🚕 Xizmat: <b>{svc}</b>"
        f"{pax}\n\n"
        f"📍 QAYERDAN:\n"
        f"<b>{escape(d['origin'])}</b>\n\n"
        f"🏁 QAYERGA:\n"
        f"<b>{escape(d['destination'])}</b>\n\n"
        f"📍 GPS: {gps_status}\n"
        f"💰 NARX: "
        f"<b>{money(d['price'])}</b>"
    )

    await m.answer(
        text,
        reply_markup=confirm_kb(
            lang
        )
    )


@dp.callback_query(
    F.data == "ord:edit"
)
async def edit_order(
    c: CallbackQuery,
    state: FSMContext
):
    await c.answer()

    d = await state.get_data()

    lang = d.get(
        "lang",
        "uz"
    )

    await state.set_state(
        Order.origin
    )

    await c.message.answer(
        tr(
            lang,
            "origin"
        )
    )


@dp.callback_query(
    F.data == "ord:cancel"
)
async def cancel_order(
    c: CallbackQuery,
    state: FSMContext
):
    await c.answer()

    await state.clear()

    u = await user(
        c.from_user.id
    )

    lang = (
        u["lang"]
        if u
        else "uz"
    )

    await c.message.edit_text(
        "❌ Buyurtma bekor qilindi."
    )

    await c.message.answer(
        tr(
            lang,
            "welcome"
        ),
        reply_markup=main_kb(
            lang
        )
    )


@dp.callback_query(
    F.data == "ord:confirm"
)
async def confirm_order(
    c: CallbackQuery,
    state: FSMContext
):
    d = await state.get_data()

    u = await user(
        c.from_user.id
    )

    if (
        not u
        or not d.get("origin")
        or not d.get("destination")
        or not d.get("price")
    ):
        await c.answer(
            "Ma'lumot to‘liq emas.",
            show_alert=True
        )
        return

    deadline = (
        datetime.utcnow()
        + timedelta(
            minutes=CLAIM_MINUTES
        )
    ).replace(
        microsecond=0
    ).isoformat()

    oid = await q(
        """
        INSERT INTO orders
        (
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
        VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            c.from_user.id,
            d["service"],
            d["origin"],
            d["destination"],
            d.get("lat"),
            d.get("lon"),
            d.get("passengers", 0),
            d["price"],
            "SEARCHING",
            now(),
            deadline
        )
    )

    await audit(
        c.from_user.id,
        "ORDER_CREATED",
        str(oid)
    )

    await state.clear()

    await c.answer("OK")

    await c.message.edit_text(
        f"🚕 <b>BUYURTMA #{oid}</b>\n\n"
        f"{escape(d['origin'])} → "
        f"{escape(d['destination'])}\n"
        f"💰 {money(d['price'])}\n\n"
        f"{tr(u['lang'], 'search')}"
    )

    asyncio.create_task(
        dispatch(oid)
    )


async def eligible(oid):
    return await q(
        """
        SELECT d.*
        FROM drivers d
        WHERE d.approved=1
          AND d.online=1
          AND d.active_orders<?
          AND d.route=?
          AND NOT EXISTS(
              SELECT 1
              FROM order_declines x
              WHERE x.order_id=?
                AND x.driver_tg_id=d.tg_id
          )
          AND d.tg_id NOT IN (
              SELECT COALESCE(
                  no_answer_driver_tg_id,
                  0
              )
              FROM orders
              WHERE id=?
          )
        ORDER BY
            d.active_orders,
            d.id
        """,
        (
            MAX_ACTIVE,
            ROUTE,
            oid,
            oid
        ),
        fetch=True
    )


async def dispatch(oid):
    await asyncio.sleep(0.5)

    o = await q(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    if (
        not o
        or o["status"] != "SEARCHING"
    ):
        return

    ds = await eligible(oid)

    sent = 0

    for d in ds:
        try:
            u = await user(
                d["tg_id"]
            )

            lang = (
                u["lang"]
                if u
                else "uz"
            )

            svc = (
                "📦 DASTAVKA"
                if o["service"] == "DELIVERY"
                else "👤 YO‘LOVCHI"
            )

            pax = (
                ""
                if o["service"] == "DELIVERY"
                else f"\n👥 {o['passengers']} kishi"
            )

            gps_status = (
                "✅ Yuborilgan"
                if o["origin_lat"] is not None
                else "❌ Yuborilmagan"
            )

            text = (
                f"🚕 <b>YANGI BUYURTMA #{oid}</b>\n\n"
                f"{svc}{pax}\n\n"
                f"📍 QAYERDAN:\n"
                f"<b>{escape(o['origin'])}</b>\n\n"
                f"🏁 QAYERGA:\n"
                f"<b>{escape(o['destination'])}</b>\n\n"
                f"💰 NARX: "
                f"<b>{money(o['price'])}</b>\n"
                f"📍 GPS: {gps_status}"
            )

            await bot.send_message(
                d["tg_id"],
                text,
                reply_markup=driver_offer_kb(
                    oid,
                    lang
                )
            )

            sent += 1

        except Exception as e:
            log.warning(
                "dispatch %s %s",
                oid,
                e
            )

    if sent == 0:
        await q(
            """
            UPDATE orders
            SET status='NO_DRIVER'
            WHERE id=?
              AND status='SEARCHING'
            """,
            (oid,)
        )


# ---------- DRIVER ----------

@dp.message(
    F.text.in_(
        {
            T[x]["driver"]
            for x in T
        }
    )
)
async def driver_start(
    m: Message,
    state: FSMContext
):
    u = await ensure(m)

    if not u:
        return

    d = await driver(
        m.from_user.id
    )

    if d:
        if not d["approved"]:
            await m.answer(
                tr(
                    u["lang"],
                    "wait_approval"
                )
            )
            return

        await m.answer(
            tr(
                u["lang"],
                "approved"
            ),
            reply_markup=driver_panel_kb(
                u["lang"],
                d
            )
        )
        return

    await state.clear()

    await state.set_state(
        DriverReg.name
    )

    await m.answer(
        tr(
            u["lang"],
            "driver_reg"
        )
        + "\n\n"
        + tr(
            u["lang"],
            "fio"
        )
    )


@dp.message(DriverReg.name)
async def dr_name(
    m: Message,
    state: FSMContext
):
    u = await user(
        m.from_user.id
    )

    lang = (
        u["lang"]
        if u
        else "uz"
    )

    s = (
        m.text or ""
    ).strip()

    if len(s) < 3:
        await m.answer(
            tr(
                lang,
                "fio"
            )
        )
        return

    await state.update_data(
        full_name=s
    )

    await state.set_state(
        DriverReg.phone
    )

    await m.answer(
        tr(
            lang,
            "phone"
        ),
        reply_markup=phone_kb(
            lang
        )
    )


@dp.message(
    DriverReg.phone,
    F.contact
)
async def dr_phone_c(
    m: Message,
    state: FSMContext
):
    await dr_phone_save(
        m,
        state,
        m.contact.phone_number
    )


@dp.message(DriverReg.phone)
async def dr_phone(
    m: Message,
    state: FSMContext
):
    await dr_phone_save(
        m,
        state,
        (m.text or "").strip()
    )


async def dr_phone_save(
    m,
    state,
    phone
):
    if len(
        "".join(
            c for c in phone
            if c.isdigit()
        )
    ) < 7:
        await m.answer(
            "📱 Telefon noto‘g‘ri."
        )
        return

    await state.update_data(
        phone=phone
    )

    await state.set_state(
        DriverReg.car
    )

    u = await user(
        m.from_user.id
    )

    await m.answer(
        tr(
            u["lang"],
            "car"
        )
    )
    @dp.message(DriverReg.car)
async def dr_car(
    m: Message,
    state: FSMContext
):
    await state.update_data(
        car_model=(m.text or "").strip()
    )
    await state.set_state(
        DriverReg.plate
    )

    u = await user(m.from_user.id)
    await m.answer(
        tr(u["lang"], "plate")
    )


@dp.message(DriverReg.plate)
async def dr_plate(
    m: Message,
    state: FSMContext
):
    p = (
        m.text or ""
    ).strip().upper()

    if len(p) < 3:
        await m.answer(
            "❗ Raqam noto‘g‘ri."
        )
        return

    await state.update_data(
        plate=p
    )

    await state.set_state(
        DriverReg.license
    )

    u = await user(
        m.from_user.id
    )

    await m.answer(
        tr(u["lang"], "license")
    )


@dp.message(
    DriverReg.license,
    F.photo
)
async def dr_license(
    m: Message,
    state: FSMContext
):
    await state.update_data(
        license_file_id=m.photo[-1].file_id
    )

    await state.set_state(
        DriverReg.tech
    )

    u = await user(
        m.from_user.id
    )

    await m.answer(
        tr(u["lang"], "tech")
    )


@dp.message(DriverReg.license)
async def dr_license_bad(
    m: Message,
    state: FSMContext
):
    u = await user(
        m.from_user.id
    )

    await m.answer(
        tr(u["lang"], "license")
    )


@dp.message(
    DriverReg.tech,
    F.photo
)
async def dr_tech(
    m: Message,
    state: FSMContext
):
    await state.update_data(
        tech_file_id=m.photo[-1].file_id
    )

    await state.set_state(
        DriverReg.car_photo
    )

    u = await user(
        m.from_user.id
    )

    await m.answer(
        tr(u["lang"], "carphoto")
    )


@dp.message(DriverReg.tech)
async def dr_tech_bad(
    m: Message,
    state: FSMContext
):
    u = await user(
        m.from_user.id
    )

    await m.answer(
        tr(u["lang"], "tech")
    )


@dp.message(
    DriverReg.car_photo,
    F.photo
)
async def dr_carphoto(
    m: Message,
    state: FSMContext
):
    await state.update_data(
        car_photo_file_id=m.photo[-1].file_id
    )

    await state.set_state(
        DriverReg.rules
    )

    u = await user(
        m.from_user.id
    )

    await m.answer(
        tr(u["lang"], "rules"),
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[
                [
                    KeyboardButton(
                        text=tr(
                            u["lang"],
                            "accept_rules"
                        )
                    )
                ],
                [
                    KeyboardButton(
                        text=tr(
                            u["lang"],
                            "back"
                        )
                    )
                ]
            ],
            resize_keyboard=True
        )
    )


@dp.message(DriverReg.car_photo)
async def dr_carphoto_bad(
    m: Message,
    state: FSMContext
):
    u = await user(
        m.from_user.id
    )

    await m.answer(
        tr(u["lang"], "carphoto")
    )


@dp.message(DriverReg.rules)
async def dr_rules(
    m: Message,
    state: FSMContext
):
    u = await user(
        m.from_user.id
    )

    lang = (
        u["lang"]
        if u
        else "uz"
    )

    if (
        (m.text or "").strip()
        != tr(lang, "accept_rules")
    ):
        await m.answer(
            tr(lang, "rules")
        )
        return

    d = await state.get_data()

    try:
        await q(
            """
            INSERT INTO drivers
            (
                tg_id,
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
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                m.from_user.id,
                d["full_name"],
                d["phone"],
                d["car_model"],
                d["plate"],
                d["license_file_id"],
                d["tech_file_id"],
                d["car_photo_file_id"],
                ROUTE,
                now()
            )
        )

    except sqlite3.IntegrityError:
        await m.answer(
            "❗ Bu haydovchi yoki "
            "davlat raqami allaqachon mavjud."
        )
        await state.clear()
        return

    await state.clear()

    await m.answer(
        tr(lang, "wait_approval"),
        reply_markup=main_kb(lang)
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ TASDIQLASH",
                    callback_data=(
                        f"approve:{m.from_user.id}"
                    )
                ),
                InlineKeyboardButton(
                    text="❌ RAD ETISH",
                    callback_data=(
                        f"reject:{m.from_user.id}"
                    )
                )
            ]
        ]
    )

    await bot.send_message(
        ADMIN_ID,
        f"🚕 <b>YANGI HAYDOVCHI ARIZASI</b>\n\n"
        f"👤 {escape(d['full_name'])}\n"
        f"📞 {escape(d['phone'])}\n"
        f"🚗 {escape(d['car_model'])}\n"
        f"🔢 {escape(d['plate'])}\n"
        f"📍 OBLIQ ↔ ANGREN",
        reply_markup=kb
    )

    for label, fid in [
        ("🪪 Prava", d["license_file_id"]),
        ("📄 Texpasport", d["tech_file_id"]),
        ("📷 Mashina", d["car_photo_file_id"])
    ]:
        try:
            await bot.send_photo(
                ADMIN_ID,
                fid,
                caption=(
                    label
                    + " | "
                    + escape(d["full_name"])
                )
            )
        except Exception:
            pass


@dp.callback_query(
    F.data.startswith("approve:")
)
async def approve(
    c: CallbackQuery
):
    if c.from_user.id != ADMIN_ID:
        await c.answer(
            "Ruxsat yo‘q",
            show_alert=True
        )
        return

    tg = int(
        c.data.split(":")[1]
    )

    d = await driver(tg)

    if not d:
        await c.answer(
            "Ariza topilmadi",
            show_alert=True
        )
        return

    await q(
        """
        UPDATE drivers
        SET approved=1,
            online=0
        WHERE tg_id=?
        """,
        (tg,)
    )

    await q(
        """
        UPDATE users
        SET role='driver'
        WHERE tg_id=?
        """,
        (tg,)
    )

    await audit(
        ADMIN_ID,
        "DRIVER_APPROVED",
        str(tg)
    )

    await c.answer(
        "Tasdiqlandi"
    )

    await c.message.edit_reply_markup(
        reply_markup=None
    )

    u = await user(tg)

    lang = (
        u["lang"]
        if u
        else "uz"
    )

    await bot.send_message(
        tg,
        tr(lang, "approved"),
        reply_markup=driver_panel_kb(
            lang,
            await driver(tg)
        )
    )


@dp.callback_query(
    F.data.startswith("reject:")
)
async def reject(
    c: CallbackQuery
):
    if c.from_user.id != ADMIN_ID:
        return

    tg = int(
        c.data.split(":")[1]
    )

    await q(
        """
        DELETE FROM drivers
        WHERE tg_id=?
          AND approved=0
        """,
        (tg,)
    )

    await audit(
        ADMIN_ID,
        "DRIVER_REJECTED",
        str(tg)
    )

    await c.answer(
        "Rad etildi"
    )

    await c.message.edit_reply_markup(
        reply_markup=None
    )

    try:
        await bot.send_message(
            tg,
            "❌ Haydovchi arizangiz rad etildi."
        )
    except Exception:
        pass


# ---------- DRIVER PANEL ----------

@dp.message(
    F.text.in_(
        {
            T[x]["online"]
            for x in T
        }
        |
        {
            T[x]["offline"]
            for x in T
        }
    )
)
async def toggle_online(
    m: Message
):
    u = await ensure(m)
    d = await driver(
        m.from_user.id
    )

    if not u or not d:
        return

    if not d["approved"]:
        await m.answer(
            tr(
                u["lang"],
                "wait_approval"
            )
        )
        return

    new = (
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
            new,
            m.from_user.id
        )
    )

    d = await driver(
        m.from_user.id
    )

    await m.answer(
        "🟢 ONLINE"
        if new
        else "⚪ OFFLINE",
        reply_markup=driver_panel_kb(
            u["lang"],
            d
        )
    )
    @dp.callback_query(F.data.startswith("decline:"))
async def decline(c: CallbackQuery):
    d = await driver(c.from_user.id)
    if not d:
        return

    oid = int(c.data.split(":")[1])

    await q(
        """
        INSERT OR IGNORE INTO order_declines
        (order_id, driver_tg_id, created_at)
        VALUES (?, ?, ?)
        """,
        (oid, c.from_user.id, now())
    )

    await c.answer("Rad etildi")

    try:
        await c.message.edit_reply_markup(
            reply_markup=None
        )
    except Exception:
        pass


@dp.callback_query(F.data.startswith("claim:"))
async def claim(c: CallbackQuery):
    tg = c.from_user.id
    oid = int(c.data.split(":")[1])

    d = await driver(tg)

    if (
        not d
        or not d["approved"]
        or not d["online"]
    ):
        await c.answer(
            "Avval tasdiqlangan ONLINE haydovchi bo‘ling.",
            show_alert=True
        )
        return

    async with db_lock:
        o = db.execute(
            "SELECT * FROM orders WHERE id=?",
            (oid,)
        ).fetchone()

        if not o or o["status"] != "SEARCHING":
            await c.answer(
                "Buyurtma allaqachon olingan.",
                show_alert=True
            )
            return

        if (
            o["claim_deadline"]
            and o["claim_deadline"] < now()
        ):
            db.execute(
                """
                UPDATE orders
                SET status='NO_DRIVER'
                WHERE id=?
                """,
                (oid,)
            )
            db.commit()

            await c.answer(
                "Vaqt tugagan.",
                show_alert=True
            )
            return

        declined = db.execute(
            """
            SELECT 1
            FROM order_declines
            WHERE order_id=?
              AND driver_tg_id=?
            """,
            (oid, tg)
        ).fetchone()

        if declined:
            await c.answer(
                "Siz bu buyurtmani rad etgansiz.",
                show_alert=True
            )
            return

        drow = db.execute(
            """
            SELECT *
            FROM drivers
            WHERE tg_id=?
              AND approved=1
              AND online=1
            """,
            (tg,)
        ).fetchone()

        if (
            not drow
            or drow["active_orders"] >= MAX_ACTIVE
        ):
            await c.answer(
                "Faol buyurtmalar limiti 4 ta.",
                show_alert=True
            )
            return

        cur = db.execute(
            """
            UPDATE orders
            SET driver_tg_id=?,
                status='ACCEPTED',
                accepted_at=?
            WHERE id=?
              AND status='SEARCHING'
            """,
            (tg, now(), oid)
        )

        if cur.rowcount != 1:
            db.rollback()

            await c.answer(
                "Buyurtma boshqa haydovchiga berildi.",
                show_alert=True
            )
            return

        db.execute(
            """
            UPDATE drivers
            SET active_orders=active_orders+1
            WHERE tg_id=?
              AND active_orders<?
            """,
            (tg, MAX_ACTIVE)
        )

        changed = db.execute(
            "SELECT changes()"
        ).fetchone()[0]

        if changed != 1:
            db.rollback()

            await c.answer(
                "Faol limit tugadi.",
                show_alert=True
            )
            return

        db.commit()

    await c.answer("Buyurtma olindi!")

    try:
        await c.message.edit_reply_markup(
            reply_markup=None
        )
    except Exception:
        pass

    o = await q(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    cu = await user(
        o["customer_tg_id"]
    )

    lang = (
        cu["lang"]
        if cu
        else "uz"
    )

    dd = await driver(tg)

    customer_text = (
        "🚕 <b>HAYDOVCHI TOPILDI</b>\n\n"
        f"👤 Ism: <b>{escape(dd['full_name'])}</b>\n"
        f"📞 Telefon: <b>{escape(dd['phone'])}</b>\n"
        f"🚗 Mashina: <b>{escape(dd['car_model'])}</b>\n"
        f"🔢 Raqam: <b>{escape(dd['plate'])}</b>\n\n"
        f"📍 {escape(o['origin'])}"
        f" → 🏁 {escape(o['destination'])}\n"
        f"💰 {money(o['price'])}"
    )

    await bot.send_message(
        o["customer_tg_id"],
        customer_text
    )

    driver_text = (
        f"🚕 <b>BUYURTMA #{oid}</b>\n\n"
        f"👤 Mijoz: <b>{escape(cu['name'])}</b>\n"
        f"📞 Telefon: <b>{escape(cu['phone'])}</b>\n"
        f"📍 {escape(o['origin'])}\n"
        f"🏁 {escape(o['destination'])}\n"
        f"💰 {money(o['price'])}"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tr(lang, "no_answer"),
                    callback_data=f"noanswer:{oid}"
                )
            ],
            [
                InlineKeyboardButton(
                    text=tr(lang, "finish"),
                    callback_data=f"finish:{oid}"
                )
            ]
        ]
    )

    await bot.send_message(
        tg,
        driver_text,
        reply_markup=kb
    )

    if o["origin_lat"] is not None:
        try:
            await bot.send_location(
                tg,
                o["origin_lat"],
                o["origin_lon"]
            )
        except Exception:
            pass


@dp.callback_query(
    F.data.startswith("noanswer:")
)
async def noanswer(c: CallbackQuery):
    tg = c.from_user.id
    oid = int(c.data.split(":")[1])

    d = await driver(tg)

    o = await q(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    if (
        not d
        or not o
        or o["driver_tg_id"] != tg
        or o["status"] != "ACCEPTED"
    ):
        await c.answer(
            "Buyurtma holati noto‘g‘ri.",
            show_alert=True
        )
        return

    deadline = (
        datetime.utcnow()
        + timedelta(
            seconds=NO_ANSWER_SECONDS
        )
    ).replace(
        microsecond=0
    ).isoformat()

    await q(
        """
        UPDATE orders
        SET status='NO_ANSWER_WAIT',
            no_answer_deadline=?
        WHERE id=?
        """,
        (deadline, oid)
    )

    await c.answer(
        "60 soniyalik jarayon boshlandi"
    )

    cu = await user(
        o["customer_tg_id"]
    )

    lang = (
        cu["lang"]
        if cu
        else "uz"
    )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tr(lang, "yes"),
                    callback_data=f"na_yes:{oid}"
                ),
                InlineKeyboardButton(
                    text=tr(lang, "no"),
                    callback_data=f"na_no:{oid}"
                )
            ]
        ]
    )

    await bot.send_message(
        o["customer_tg_id"],
        tr(lang, "no_answer_msg"),
        reply_markup=kb
    )

    asyncio.create_task(
        noanswer_timeout(
            oid,
            tg
        )
    )


async def noanswer_timeout(
    oid,
    tg
):
    await asyncio.sleep(
        NO_ANSWER_SECONDS
    )

    async with db_lock:
        o = db.execute(
            "SELECT * FROM orders WHERE id=?",
            (oid,)
        ).fetchone()

        if (
            not o
            or o["status"] != "NO_ANSWER_WAIT"
        ):
            return

        deadline = (
            datetime.utcnow()
            + timedelta(
                minutes=CLAIM_MINUTES
            )
        ).isoformat()

        db.execute(
            """
            UPDATE orders
            SET status='SEARCHING',
                driver_tg_id=NULL,
                no_answer_driver_tg_id=?,
                no_answer_deadline=NULL,
                claim_deadline=?
            WHERE id=?
            """,
            (
                tg,
                deadline,
                oid
            )
        )

        db.execute(
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
            (tg,)
        )

        db.commit()

    asyncio.create_task(
        dispatch(oid)
    )


@dp.callback_query(
    F.data.startswith("na_yes:")
)
async def na_yes(c: CallbackQuery):
    oid = int(
        c.data.split(":")[1]
    )

    o = await q(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    if (
        not o
        or o["status"] != "NO_ANSWER_WAIT"
    ):
        await c.answer(
            "Jarayon tugagan.",
            show_alert=True
        )
        return

    old = o["driver_tg_id"]

    deadline = (
        datetime.utcnow()
        + timedelta(
            minutes=CLAIM_MINUTES
        )
    ).isoformat()

    await q(
        """
        UPDATE orders
        SET status='SEARCHING',
            driver_tg_id=NULL,
            no_answer_driver_tg_id=?,
            no_answer_deadline=NULL,
            claim_deadline=?
        WHERE id=?
        """,
        (
            old,
            deadline,
            oid
        )
    )

    await q(
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
        (old,)
    )

    await c.answer(
        "Qayta qidirilmoqda"
    )

    await c.message.edit_text(
        "🔎 Yangi haydovchi qidirilmoqda..."
    )

    asyncio.create_task(
        dispatch(oid)
    )


@dp.callback_query(
    F.data.startswith("na_no:")
)
async def na_no(c: CallbackQuery):
    oid = int(
        c.data.split(":")[1]
    )

    o = await q(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    if (
        not o
        or o["status"] != "NO_ANSWER_WAIT"
    ):
        await c.answer(
            "Jarayon tugagan.",
            show_alert=True
        )
        return

    old = o["driver_tg_id"]

    await q(
        """
        UPDATE orders
        SET status='CANCELLED',
            driver_tg_id=NULL,
            no_answer_deadline=NULL
        WHERE id=?
        """,
        (oid,)
    )

    await q(
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
        (old,)
    )

    await c.answer(
        "Bekor qilindi"
    )

    await c.message.edit_text(
        "❌ Buyurtma bekor qilindi."
    )
    @dp.callback_query(
    F.data.startswith("finish:")
)
async def finish(c: CallbackQuery):
    oid = int(
        c.data.split(":")[1]
    )

    tg = c.from_user.id

    o = await q(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    if (
        not o
        or o["driver_tg_id"] != tg
        or o["status"] != "ACCEPTED"
    ):
        await c.answer(
            "Buyurtma faol emas.",
            show_alert=True
        )
        return

    await q(
        """
        UPDATE orders
        SET status='COMPLETED',
            finished_at=?
        WHERE id=?
        """,
        (now(), oid)
    )

    await q(
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
        (tg,)
    )

    await c.answer(
        "Yakunlandi"
    )

    await c.message.edit_reply_markup(
        reply_markup=None
    )

    cu = await user(
        o["customer_tg_id"]
    )

    lang = (
        cu["lang"]
        if cu
        else "uz"
    )

    kb = InlineKeyboardMarkup(
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

    await bot.send_message(
        o["customer_tg_id"],
        tr(lang, "rating_ask"),
        reply_markup=kb
    )


@dp.callback_query(
    F.data.startswith("rate:")
)
async def rate(c: CallbackQuery):
    _, oid_s, score_s = (
        c.data.split(":")
    )

    oid = int(oid_s)
    score = int(score_s)

    if score < 1 or score > 5:
        await c.answer(
            "Noto‘g‘ri baho.",
            show_alert=True
        )
        return

    o = await q(
        "SELECT * FROM orders WHERE id=?",
        (oid,),
        fetchone=True
    )

    if (
        not o
        or o["status"] != "COMPLETED"
        or o["customer_tg_id"]
        != c.from_user.id
    ):
        await c.answer(
            "Noto‘g‘ri.",
            show_alert=True
        )
        return

    try:
        await q(
            """
            INSERT INTO ratings
            (
                order_id,
                from_tg_id,
                to_tg_id,
                score,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                oid,
                c.from_user.id,
                o["driver_tg_id"],
                score,
                now()
            )
        )

        await q(
            """
            UPDATE drivers
            SET rating=
                (
                    (rating*rating_count)+?
                )/(rating_count+1),
                rating_count=
                    rating_count+1
            WHERE tg_id=?
            """,
            (
                score,
                o["driver_tg_id"]
            )
        )

        u = await user(
            c.from_user.id
        )

        lang = (
            u["lang"]
            if u
            else "uz"
        )

        await c.answer(
            "Rahmat!"
        )

        await c.message.edit_text(
            tr(lang, "thanks")
        )

    except sqlite3.IntegrityError:
        await c.answer(
            "Siz allaqachon baholagansiz.",
            show_alert=True
        )


# ---------- CUSTOMER MENU ----------

@dp.message(
    F.text.in_(
        {
            T[x]["profile"]
            for x in T
        }
    )
)
async def profile(m: Message):
    u = await ensure(m)

    if not u:
        return

    d = await driver(
        m.from_user.id
    )

    extra = (
        "\n🚕 Haydovchi: tasdiqlangan"
        if d and d["approved"]
        else ""
    )

    await m.answer(
        f"👤 <b>{escape(u['name'])}</b>\n"
        f"📞 {escape(u['phone'])}"
        f"{extra}"
    )


@dp.message(
    F.text.in_(
        {
            T[x]["history"]
            for x in T
        }
    )
)
async def history(m: Message):
    u = await ensure(m)

    if not u:
        return

    rows = await q(
        """
        SELECT *
        FROM orders
        WHERE customer_tg_id=?
        ORDER BY id DESC
        LIMIT 10
        """,
        (m.from_user.id,),
        fetch=True
    )

    if not rows:
        await m.answer(
            "📭 Tarix bo‘sh."
        )
        return

    out = [
        "📜 <b>BUYURTMALAR TARIXI</b>"
    ]

    for o in rows:
        out.append(
            f"#{o['id']} | "
            f"{escape(o['origin'])} → "
            f"{escape(o['destination'])} | "
            f"{money(o['price'])} | "
            f"{o['status']}"
        )

    await m.answer(
        "\n".join(out)
    )


@dp.message(
    F.text.in_(
        {
            T[x]["change_lang"]
            for x in T
        }
    )
)
async def change_lang(
    m: Message,
    state: FSMContext
):
    u = await ensure(m)

    if not u:
        return

    await state.set_state(
        Reg.lang
    )

    await state.update_data(
        changing=True
    )

    await m.answer(
        tr(
            u["lang"],
            "choose_lang"
        ),
        reply_markup=lang_kb()
    )


@dp.message(
    F.text.in_(
        {
            T[x]["support"]
            for x in T
        }
    )
)
async def support_start(
    m: Message,
    state: FSMContext
):
    u = await ensure(m)

    if not u:
        return

    await state.set_state(
        Support.text
    )

    await state.update_data(
        lang=u["lang"]
    )

    await m.answer(
        tr(
            u["lang"],
            "support_ask"
        )
    )


@dp.message(Support.text)
async def support_save(
    m: Message,
    state: FSMContext
):
    u = await user(
        m.from_user.id
    )

    if not u:
        return

    text = (
        m.text or ""
    ).strip()

    if len(text) < 2:
        return

    tid = await q(
        """
        INSERT INTO support_tickets
        (tg_id, text, created_at)
        VALUES (?, ?, ?)
        """,
        (
            m.from_user.id,
            text,
            now()
        )
    )

    await state.clear()

    await m.answer(
        tr(
            u["lang"],
            "support_sent"
        ),
        reply_markup=main_kb(
            u["lang"]
        )
    )

    await bot.send_message(
        ADMIN_ID,
        f"📩 <b>MUROJAAT #{tid}</b>\n"
        f"👤 {escape(u['name'])}\n"
        f"📞 {escape(u['phone'])}\n\n"
        f"{escape(text)}"
    )
    # ---------- DRIVER MENU ----------

@dp.message(
    F.text.in_(
        {
            T[x]["active"]
            for x in T
        }
    )
)
async def active_orders(
    m: Message
):
    u = await ensure(m)
    d = await driver(
        m.from_user.id
    )

    if not u or not d:
        return

    rows = await q(
        """
        SELECT *
        FROM orders
        WHERE driver_tg_id=?
          AND status IN
          ('ACCEPTED','NO_ANSWER_WAIT')
        ORDER BY id DESC
        """,
        (m.from_user.id,),
        fetch=True
    )

    if not rows:
        await m.answer(
            "📭 Faol buyurtma yo‘q."
        )
        return

    await m.answer(
        "\n".join(
            [
                f"#{o['id']} | "
                f"{escape(o['origin'])} → "
                f"{escape(o['destination'])} | "
                f"{money(o['price'])}"
                for o in rows
            ]
        )
    )


@dp.message(
    F.text.in_(
        {
            T[x]["driver_history"]
            for x in T
        }
    )
)
async def driver_history(
    m: Message
):
    d = await driver(
        m.from_user.id
    )

    u = await user(
        m.from_user.id
    )

    if not d or not u:
        return

    rows = await q(
        """
        SELECT *
        FROM orders
        WHERE driver_tg_id=?
        ORDER BY id DESC
        LIMIT 10
        """,
        (m.from_user.id,),
        fetch=True
    )

    if not rows:
        await m.answer(
            "📭 Tarix bo‘sh."
        )
        return

    await m.answer(
        "\n".join(
            [
                f"#{o['id']} | "
                f"{o['status']} | "
                f"{money(o['price'])}"
                for o in rows
            ]
        )
    )


@dp.message(
    F.text.in_(
        {
            T[x]["income"]
            for x in T
        }
    )
)
async def income(
    m: Message
):
    d = await driver(
        m.from_user.id
    )

    if not d:
        return

    r = await q(
        """
        SELECT COALESCE(
            SUM(price), 0
        ) AS s
        FROM orders
        WHERE driver_tg_id=?
          AND status='COMPLETED'
        """,
        (m.from_user.id,),
        fetchone=True
    )

    await m.answer(
        f"💰 <b>Daromad:</b> "
        f"{money(r['s'])}"
    )


@dp.message(
    F.text.in_(
        {
            T[x]["rating"]
            for x in T
        }
    )
)
async def rating(
    m: Message
):
    d = await driver(
        m.from_user.id
    )

    if d:
        await m.answer(
            f"⭐ <b>{d['rating']:.2f}</b>\n"
            f"👥 Baholar: "
            f"{d['rating_count']}"
        )


@dp.message(
    F.text.in_(
        {
            T[x]["driver_profile"]
            for x in T
        }
    )
)
async def driver_profile(
    m: Message
):
    d = await driver(
        m.from_user.id
    )

    if not d:
        return

    await m.answer(
        f"👤 {escape(d['full_name'])}\n"
        f"📞 {escape(d['phone'])}\n"
        f"🚗 {escape(d['car_model'])}\n"
        f"🔢 {escape(d['plate'])}\n"
        f"📍 OBLIQ ↔ ANGREN\n"
        f"⭐ {d['rating']:.2f}"
    )


# ---------- ADMIN ----------

async def admin_only(m):
    return m.from_user.id == ADMIN_ID


@dp.message(Command("admin"))
async def admin_cmd(
    m: Message
):
    if await admin_only(m):
        await m.answer(
            "👨‍💼 <b>TAXI BOR MI? — "
            "ADMIN PANEL</b>",
            reply_markup=admin_kb()
        )


@dp.callback_query(
    F.data.startswith("adm:")
)
async def admin_panel(
    c: CallbackQuery
):
    if c.from_user.id != ADMIN_ID:
        await c.answer(
            "Ruxsat yo‘q",
            show_alert=True
        )
        return

    action = c.data.split(
        ":",
        1
    )[1]

    if action == "stats":

        vals = {}

        stats = [
            (
                "Mijozlar",
                "SELECT COUNT(*) FROM users"
            ),
            (
                "Haydovchilar",
                "SELECT COUNT(*) FROM drivers"
            ),
            (
                "Online",
                """
                SELECT COUNT(*)
                FROM drivers
                WHERE approved=1
                  AND online=1
                """
            ),
            (
                "Kutilmoqda",
                """
                SELECT COUNT(*)
                FROM drivers
                WHERE approved=0
                """
            ),
            (
                "Bugungi buyurtmalar",
                """
                SELECT COUNT(*)
                FROM orders
                WHERE date(created_at)=date('now')
                """
            ),
            (
                "Faol",
                """
                SELECT COUNT(*)
                FROM orders
                WHERE status IN
                (
                    'SEARCHING',
                    'ACCEPTED',
                    'NO_ANSWER_WAIT'
                )
                """
            ),
            (
                "Yakunlangan",
                """
                SELECT COUNT(*)
                FROM orders
                WHERE status='COMPLETED'
                """
            ),
            (
                "Bekor",
                """
                SELECT COUNT(*)
                FROM orders
                WHERE status IN
                (
                    'CANCELLED',
                    'NO_DRIVER'
                )
                """
            )
        ]

        for key, sql in stats:
            row = await q(
                sql,
                fetchone=True
            )
            vals[key] = row[0]

        r = await q(
            """
            SELECT COALESCE(
                SUM(price), 0
            )
            FROM orders
            WHERE status='COMPLETED'
              AND date(finished_at)=date('now')
            """,
            fetchone=True
        )

        text = (
            "📊 <b>STATISTIKA</b>\n\n"
            + "\n".join(
                f"• {k}: <b>{v}</b>"
                for k, v in vals.items()
            )
            + "\n"
            + f"• Bugungi tushum: "
            f"<b>{money(r[0])}</b>"
        )

        await c.message.edit_text(
            text,
            reply_markup=admin_kb()
        )

    elif action == "pending":

        rows = await q(
            """
            SELECT *
            FROM drivers
            WHERE approved=0
            ORDER BY id DESC
            LIMIT 30
            """,
            fetch=True
        )

        text = (
            "⏳ <b>TASDIQLASH "
            "KUTILMOQDA</b>\n\n"
            + "\n".join(
                f"#{d['id']} "
                f"{escape(d['full_name'])} | "
                f"{escape(d['phone'])} | "
                f"{escape(d['plate'])}"
                for d in rows
            )
            if rows
            else
            "⏳ Kutilayotgan ariza yo‘q."
        )

        await c.message.answer(
            text
        )

        for d in rows:

            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ TASDIQLASH",
                            callback_data=
                            f"approve:{d['tg_id']}"
                        ),
                        InlineKeyboardButton(
                            text="❌ RAD",
                            callback_data=
                            f"reject:{d['tg_id']}"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="🚫 BLOK",
                            callback_data=
                            f"block:{d['tg_id']}"
                        )
                    ]
                ]
            )

            await c.message.answer(
                f"👤 "
                f"{escape(d['full_name'])}\n"
                f"📞 {escape(d['phone'])}\n"
                f"🚗 {escape(d['car_model'])}\n"
                f"🔢 {escape(d['plate'])}",
                reply_markup=kb
            )

        await c.answer()
        return
            elif action == "drivers":

        rows = await q(
            """
            SELECT *
            FROM drivers
            ORDER BY id DESC
            LIMIT 30
            """,
            fetch=True
        )

        text = (
            "🚕 <b>HAYDOVCHILAR</b>\n\n"
            + "\n".join(
                f"#{d['id']} "
                f"{escape(d['full_name'])} | "
                f"{'🟢' if d['online'] else '⚪'} | "
                f"{'✅' if d['approved'] else '⏳'} | "
                f"{d['active_orders']}/{MAX_ACTIVE}"
                for d in rows
            )
            if rows
            else
            "Haydovchi yo‘q."
        )

        await c.message.edit_text(
            text,
            reply_markup=admin_kb()
        )

    elif action == "users":

        rows = await q(
            """
            SELECT *
            FROM users
            ORDER BY id DESC
            LIMIT 30
            """,
            fetch=True
        )

        text = (
            "👥 <b>MIJOZLAR</b>\n\n"
            + "\n".join(
                f"#{u['id']} "
                f"{escape(u['name'])} | "
                f"{escape(u['phone'])} | "
                f"{'🚫' if u['blocked'] else '✅'}"
                for u in rows
            )
            if rows
            else
            "Mijoz yo‘q."
        )

        await c.message.edit_text(
            text,
            reply_markup=admin_kb()
        )

    elif action == "orders":

        rows = await q(
            """
            SELECT *
            FROM orders
            ORDER BY id DESC
            LIMIT 30
            """,
            fetch=True
        )

        text = (
            "📦 <b>BUYURTMALAR</b>\n\n"
            + "\n".join(
                f"#{o['id']} | "
                f"{o['status']} | "
                f"{escape(o['origin'])} → "
                f"{escape(o['destination'])} | "
                f"{money(o['price'])}"
                for o in rows
            )
            if rows
            else
            "Buyurtma yo‘q."
        )

        await c.message.edit_text(
            text,
            reply_markup=admin_kb()
        )

    elif action == "online":

        rows = await q(
            """
            SELECT *
            FROM drivers
            WHERE approved=1
              AND online=1
            """,
            fetch=True
        )

        text = (
            "🟢 <b>ONLINE "
            "HAYDOVCHILAR</b>\n\n"
            + "\n".join(
                f"{escape(d['full_name'])} | "
                f"{escape(d['phone'])} | "
                f"{d['active_orders']}/{MAX_ACTIVE}"
                for d in rows
            )
            if rows
            else
            "Online haydovchi yo‘q."
        )

        await c.message.edit_text(
            text,
            reply_markup=admin_kb()
        )

    elif action == "blocked":

        rows = await q(
            """
            SELECT *
            FROM users
            WHERE blocked=1
            """,
            fetch=True
        )

        text = (
            "🚫 <b>BLOKLANGANLAR</b>\n\n"
            + "\n".join(
                f"{u['tg_id']} | "
                f"{escape(u['name'])}"
                for u in rows
            )
            if rows
            else
            "Bloklangan yo‘q."
        )

        await c.message.edit_text(
            text,
            reply_markup=admin_kb()
        )

    else:

        rows = await q(
            """
            SELECT *
            FROM support_tickets
            WHERE status='OPEN'
            ORDER BY id DESC
            LIMIT 30
            """,
            fetch=True
        )

        text = (
            "📩 <b>MUROJAATLAR</b>\n\n"
            + "\n".join(
                f"#{x['id']} | "
                f"{x['tg_id']} | "
                f"{escape(x['text'])}"
                for x in rows
            )
            if rows
            else
            "Ochiq murojaat yo‘q."
        )

        await c.message.edit_text(
            text,
            reply_markup=admin_kb()
        )

    await c.answer()


@dp.callback_query(
    F.data.startswith("block:")
)
async def block(
    c: CallbackQuery
):
    if c.from_user.id != ADMIN_ID:
        return

    tg = int(
        c.data.split(":")[1]
    )

    await q(
        """
        UPDATE users
        SET blocked=1
        WHERE tg_id=?
        """,
        (tg,)
    )

    await q(
        """
        UPDATE drivers
        SET online=0
        WHERE tg_id=?
        """,
        (tg,)
    )

    await audit(
        ADMIN_ID,
        "BLOCK",
        str(tg)
    )

    await c.answer(
        "Bloklandi"
    )


@dp.message(Command("block"))
async def block_cmd(
    m: Message
):
    if m.from_user.id != ADMIN_ID:
        return

    p = (
        m.text or ""
    ).split()

    if (
        len(p) == 2
        and p[1].isdigit()
    ):
        tg = int(p[1])

        await q(
            """
            UPDATE users
            SET blocked=1
            WHERE tg_id=?
            """,
            (tg,)
        )

        await q(
            """
            UPDATE drivers
            SET online=0
            WHERE tg_id=?
            """,
            (tg,)
        )

        await m.answer(
            "🚫 Bloklandi."
        )


@dp.message(Command("unblock"))
async def unblock_cmd(
    m: Message
):
    if m.from_user.id != ADMIN_ID:
        return

    p = (
        m.text or ""
    ).split()

    if (
        len(p) == 2
        and p[1].isdigit()
    ):
        await q(
            """
            UPDATE users
            SET blocked=0
            WHERE tg_id=?
            """,
            (int(p[1]),)
        )

        await m.answer(
            "✅ Blokdan chiqarildi."
        )


# ---------- FALLBACK ----------

@dp.message(
    F.text.in_(
        {
            T[x]["back"]
            for x in T
        }
    )
)
async def back(
    m: Message,
    state: FSMContext
):
    await state.clear()

    u = await user(
        m.from_user.id
    )

    if u:
        await m.answer(
            tr(
                u["lang"],
                "welcome"
            ),
            reply_markup=main_kb(
                u["lang"]
            )
        )


@dp.message(Command("cancel"))
async def cmd_cancel(
    m: Message,
    state: FSMContext
):
    await back(
        m,
        state
    )


@dp.message()
async def fallback(
    m: Message
):
    u = await user(
        m.from_user.id
    )

    if not u:
        await m.answer(
            "Avval /start bosing.",
            reply_markup=lang_kb()
        )
        return

    if u["blocked"]:
        await m.answer(
            tr(
                u["lang"],
                "blocked"
            )
        )
        return

    await m.answer(
        tr(
            u["lang"],
            "welcome"
        ),
        reply_markup=main_kb(
            u["lang"]
        )
    )


# ---------- START BOT ----------

async def main():
    init_db()

    log.info(
        "TAXI BOR MI? bot started | "
        "route=%s | max_active=%s",
        ROUTE,
        MAX_ACTIVE
    )

    await dp.start_polling(
        bot,
        allowed_updates=
        dp.resolve_used_update_types()
    )


if __name__ == "__main__":
    asyncio.run(main())
