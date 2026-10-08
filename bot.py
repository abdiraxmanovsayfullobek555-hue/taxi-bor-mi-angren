import asyncio
import os
import re
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery, KeyboardButton, Message, ReplyKeyboardMarkup,
    InlineKeyboardButton, InlineKeyboardMarkup
)

# ============================================================
# TAXI BOR MI? — ALBATTA BOR!
# Simple V1: OBLIQ <-> ANGREN
# Railway variables: BOT_TOKEN (required), ADMIN_ID (required)
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
DB_FILE = os.getenv("DB_FILE", "taxi_bor_mi.db")
MAX_ACTIVE = 4
NO_ANSWER_SECONDS = 60
MIN_PRICE = 5000
MAX_PRICE = 1_000_000

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN berilmagan.")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID berilmagan.")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
DB_LOCK = asyncio.Lock()

# ------------------------- database -------------------------

def conn():
    c = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def now():
    return datetime.utcnow().isoformat(timespec="seconds")


def init_db():
    c = conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        tg_id INTEGER PRIMARY KEY,
        role TEXT NOT NULL DEFAULT 'customer',
        lang TEXT NOT NULL DEFAULT 'uz',
        name TEXT,
        phone TEXT,
        blocked INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS drivers(
        tg_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        phone TEXT NOT NULL,
        car_model TEXT NOT NULL,
        plate TEXT NOT NULL,
        license_file TEXT NOT NULL,
        tech_file TEXT NOT NULL,
        car_photo TEXT NOT NULL,
        approved INTEGER NOT NULL DEFAULT 0,
        blocked INTEGER NOT NULL DEFAULT 0,
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
        passengers INTEGER NOT NULL DEFAULT 0,
        route_text TEXT NOT NULL,
        gps_lat REAL,
        gps_lon REAL,
        price INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'SEARCHING',
        previous_driver_id INTEGER,
        created_at TEXT NOT NULL,
        accepted_at TEXT,
        completed_at TEXT
    );
    CREATE TABLE IF NOT EXISTS ratings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER UNIQUE NOT NULL,
        driver_id INTEGER NOT NULL,
        customer_id INTEGER NOT NULL,
        rating INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS support(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN',
        created_at TEXT NOT NULL
    );
    """)
    c.commit()
    c.close()

async def db_exec(sql, params=(), fetchone=False, fetchall=False, commit=True):
    async with DB_LOCK:
        c = conn()
        cur = c.execute(sql, params)
        if commit:
            c.commit()
        result = cur.fetchone() if fetchone else (cur.fetchall() if fetchall else cur.lastrowid)
        c.close()
        return result

async def get_user(tg_id):
    return await db_exec("SELECT * FROM users WHERE tg_id=?", (tg_id,), True)

async def get_driver(tg_id):
    return await db_exec("SELECT * FROM drivers WHERE tg_id=?", (tg_id,), True)

# ------------------------- translations -------------------------
T = {
    "uz": {
        "lang":"🌐 Tilni tanlang", "role":"Siz kimsiz?", "customer":"👤 Mijoz", "driver":"🚗 Haydovchi",
        "name":"👤 Ismingizni kiriting", "phone":"📱 Telefon raqamingizni yuboring", "saved":"✅ Saqlandi.",
        "menu":"🚕 TAXI BOR MI?", "order":"🚕 BUYURTMA BERISH", "orders":"📋 BUYURTMALARIM", "profile":"👤 PROFIL", "help":"📞 YORDAM",
        "people":"👥 NECHA KISHI?", "delivery":"📦 DASTAVKA", "fromto":"📍 QAYERDAN → QAYERGA?\nMasalan: 5/5 dan → Kaltsoga",
        "gps":"📍 GPS yuborasizmi?", "gps_yes":"📍 GPS YUBORISH", "gps_no":"⏭ GPSsiz DAVOM ETISH", "price":"💰 NARXNI TANLANG",
        "other":"✍️ BOSHQA NARX", "confirm":"🚕 BUYURTMA", "send":"✅ BUYURTMA BERISH", "back":"❌ BEKOR QILISH",
        "bad_route":"❗ Format: Qayerdan → Qayerga. Masalan: 5/5 dan → Kaltsoga", "bad_price":"❗ Narx 5 000 dan 1 000 000 so‘mgacha bo‘lishi kerak.",
        "sent":"✅ Buyurtma haydovchilarga yuborildi.", "no_drivers":"😔 Hozir online haydovchi yo‘q. Keyinroq urinib ko‘ring.",
        "online":"🟢 ONLINE", "offline":"🔴 OFFLINE", "driver_menu":"🚗 HAYDOVCHI PANELI", "register_driver":"🚗 HAYDOVCHI BO‘LISH",
        "car":"🚗 Mashina modeli", "plate":"🔢 Davlat raqami", "license":"🪪 Prava rasmini yuboring", "tech":"📄 Texpasport rasmini yuboring", "car_photo":"🚗 Mashina rasmini yuboring",
        "rules":"📋 Qoidalarni qabul qilasizmi?", "accept_rules":"✅ QABUL QILAMAN", "pending":"⏳ Arizangiz admin tasdig‘ini kutmoqda.",
        "approved":"✅ Siz tasdiqlandingiz!", "rejected":"❌ Arizangiz rad etildi.", "new_order":"🚕 YANGI BUYURTMA", "take":"✅ BUYURTMANI OLISH", "reject":"❌ RAD ETISH",
        "taken":"✅ Buyurtma sizga biriktirildi.", "finish":"🏁 BUYURTMANI YAKUNLASH", "no_answer":"📵 MIJOZ JAVOB BERMADI", "contact":"📞 MIJOZGA QO‘NG‘IROQ",
        "need":"🚕 Haydovchi siz bilan bog‘lana olmadi.\n\nSizga hali ham mashina kerakmi?\n\n⏱ 1 daqiqa ichida javob bering.",
        "yes_need":"✅ HA, KERAK", "no_need":"❌ YO‘Q, KERAK EMAS", "reopened":"🔎 Boshqa haydovchi qidirilmoqda.", "cancelled":"❌ Buyurtma bekor qilindi.",
        "expired":"⏱ Vaqt tugadi. Buyurtma bekor qilindi.", "rate":"⭐ HAYDOVCHINI BAHOLANG", "rated":"✅ Rahmat! Reyting saqlandi.",
        "admin":"👨‍💼 ADMIN PANEL", "stats":"📊 STATISTIKA", "drivers":"🚗 HAYDOVCHILAR", "pending_drivers":"⏳ TASDIQLASH", "customers":"👥 MIJOZLAR", "all_orders":"📦 BUYURTMALAR", "online_drivers":"🟢 ONLINE HAYDOVCHILAR", "blocked":"🚫 BLOKLANGANLAR", "support":"📩 MUROJAATLAR", "settings":"⚙️ SOZLAMALAR",
        "not_allowed":"⛔ Sizda ruxsat yo‘q.", "blocked_user":"🚫 Akkauntingiz bloklangan.", "support_prompt":"📩 Murojaatingizni yozing:", "support_sent":"✅ Murojaat yuborildi.",
    },
    "uz_cy": {
        "lang":"🌐 Тилни танланг", "role":"Сиз кимсиз?", "customer":"👤 Мижоз", "driver":"🚗 Ҳайдовчи",
        "name":"👤 Исмингизни киритинг", "phone":"📱 Телефон рақамингизни юборинг", "saved":"✅ Сақланди.",
        "menu":"🚕 TAXI BOR MI?", "order":"🚕 БУЮРТМА БЕРИШ", "orders":"📋 БУЮРТМАЛАРИМ", "profile":"👤 ПРОФИЛЬ", "help":"📞 ЁРДАМ",
        "people":"👥 НЕЧА КИШИ?", "delivery":"📦 ДАСТАВКА", "fromto":"📍 ҚАЕРДАН → ҚАЕРГА?\nМасалан: 5/5 дан → Калцога",
        "gps":"📍 GPS юборишни хоҳлайсизми?", "gps_yes":"📍 GPS ЮБОРИШ", "gps_no":"⏭ GPSсиз ДАВОМ ЭТИШ", "price":"💰 НАРХНИ ТАНЛАНГ",
        "other":"✍️ БОШҚА НАРХ", "confirm":"🚕 БУЮРТМА", "send":"✅ БУЮРТМА БЕРИШ", "back":"❌ БЕКОР ҚИЛИШ",
        "bad_route":"❗ Формат: Қаердан → Қаерга. Масалан: 5/5 дан → Калцога", "bad_price":"❗ Нарх 5 000 дан 1 000 000 сўмгача бўлиши керак.",
        "sent":"✅ Буюртма ҳайдовчиларга юборилди.", "no_drivers":"😔 Ҳозир онлайн ҳайдовчи йўқ.", "online":"🟢 ONLINE", "offline":"🔴 OFFLINE",
        "driver_menu":"🚗 ҲАЙДОВЧИ ПАНЕЛИ", "register_driver":"🚗 ҲАЙДОВЧИ БЎЛИШ", "car":"🚗 Машина модели", "plate":"🔢 Давлат рақами", "license":"🪪 Права расмини юборинг", "tech":"📄 Техпаспорт расмини юборинг", "car_photo":"🚗 Машина расмини юборинг", "rules":"📋 Қоидаларни қабул қиласизми?", "accept_rules":"✅ ҚАБУЛ ҚИЛАМАН", "pending":"⏳ Аризангиз админ тасдиғини кутмоқда.", "approved":"✅ Сиз тасдиқландингиз!", "rejected":"❌ Аризангиз рад этилди.",
        "new_order":"🚕 ЯНГИ БУЮРТМА", "take":"✅ БУЮРТМАНИ ОЛИШ", "reject":"❌ РАД ЭТИШ", "taken":"✅ Буюртма сизга бириктирилди.", "finish":"🏁 БУЮРТМАНИ ЯКУНЛАШ", "no_answer":"📵 МИЖОЗ ЖАВОБ БЕРМАДИ", "contact":"📞 МИЖОЗГА ҚЎНҒИРОҚ",
        "need":"🚕 Ҳайдовчи сиз билан боғлана олмади.\n\nСизга ҳали ҳам машина керакми?\n\n⏱ 1 дақиқа ичида жавоб беринг.", "yes_need":"✅ ҲА, КЕРАК", "no_need":"❌ ЙЎҚ, КЕРАК ЭМАС", "reopened":"🔎 Бошқа ҳайдовчи қидирилмоқда.", "cancelled":"❌ Буюртма бекор қилинди.", "expired":"⏱ Вақт тугади. Буюртма бекор қилинди.", "rate":"⭐ ҲАЙДОВЧИНИ БАҲОЛАНГ", "rated":"✅ Раҳмат! Рейтинг сақланди.",
        "admin":"👨‍💼 ADMIN PANEL", "stats":"📊 СТАТИСТИКА", "drivers":"🚗 ҲАЙДОВЧИЛАР", "pending_drivers":"⏳ ТАСДИҚЛАШ", "customers":"👥 МИЖОЗЛАР", "all_orders":"📦 БУЮРТМАЛАР", "online_drivers":"🟢 ONLINE ҲАЙДОВЧИЛАР", "blocked":"🚫 БЛОКЛАНГАНЛАР", "support":"📩 МУРОЖААТЛАР", "settings":"⚙️ СОЗЛАМАЛАР", "not_allowed":"⛔ Рухсат йўқ.", "blocked_user":"🚫 Аккаунтингиз блокланган.", "support_prompt":"📩 Мурожаатингизни ёзинг:", "support_sent":"✅ Мурожаат юборилди."
    },
    "ru": {
        "lang":"🌐 Выберите язык", "role":"Кто вы?", "customer":"👤 Клиент", "driver":"🚗 Водитель", "name":"👤 Введите имя", "phone":"📱 Отправьте номер телефона", "saved":"✅ Сохранено.",
        "menu":"🚕 TAXI BOR MI?", "order":"🚕 ЗАКАЗАТЬ ТАКСИ", "orders":"📋 МОИ ЗАКАЗЫ", "profile":"👤 ПРОФИЛЬ", "help":"📞 ПОМОЩЬ", "people":"👥 СКОЛЬКО ЧЕЛОВЕК?", "delivery":"📦 ДОСТАВКА", "fromto":"📍 ОТКУДА → КУДА?\nНапример: 5/5 → Кальцово", "gps":"📍 Отправить GPS?", "gps_yes":"📍 ОТПРАВИТЬ GPS", "gps_no":"⏭ ПРОДОЛЖИТЬ БЕЗ GPS", "price":"💰 ВЫБЕРИТЕ ЦЕНУ", "other":"✍️ ДРУГАЯ ЦЕНА", "confirm":"🚕 ЗАКАЗ", "send":"✅ ОТПРАВИТЬ ЗАКАЗ", "back":"❌ ОТМЕНА", "bad_route":"❗ Формат: Откуда → Куда. Например: 5/5 → Кальцово", "bad_price":"❗ Цена от 5 000 до 1 000 000 сум.", "sent":"✅ Заказ отправлен водителям.", "no_drivers":"😔 Сейчас нет онлайн-водителей.", "online":"🟢 ONLINE", "offline":"🔴 OFFLINE", "driver_menu":"🚗 ПАНЕЛЬ ВОДИТЕЛЯ", "register_driver":"🚗 СТАТЬ ВОДИТЕЛЕМ", "car":"🚗 Модель машины", "plate":"🔢 Госномер", "license":"🪪 Отправьте фото прав", "tech":"📄 Отправьте фото техпаспорта", "car_photo":"🚗 Отправьте фото машины", "rules":"📋 Принимаете правила?", "accept_rules":"✅ ПРИНИМАЮ", "pending":"⏳ Заявка ждёт подтверждения администратора.", "approved":"✅ Вы одобрены!", "rejected":"❌ Заявка отклонена.", "new_order":"🚕 НОВЫЙ ЗАКАЗ", "take":"✅ ВЗЯТЬ ЗАКАЗ", "reject":"❌ ОТКАЗАТЬСЯ", "taken":"✅ Заказ закреплён за вами.", "finish":"🏁 ЗАВЕРШИТЬ ЗАКАЗ", "no_answer":"📵 КЛИЕНТ НЕ ОТВЕЧАЕТ", "contact":"📞 ПОЗВОНИТЬ КЛИЕНТУ", "need":"🚕 Водитель не смог связаться с вами.\n\nМашина вам ещё нужна?\n\n⏱ Ответьте за 1 минуту.", "yes_need":"✅ ДА, НУЖНА", "no_need":"❌ НЕТ, НЕ НУЖНА", "reopened":"🔎 Ищем другого водителя.", "cancelled":"❌ Заказ отменён.", "expired":"⏱ Время вышло. Заказ отменён.", "rate":"⭐ ОЦЕНИТЕ ВОДИТЕЛЯ", "rated":"✅ Спасибо! Оценка сохранена.", "admin":"👨‍💼 АДМИН-ПАНЕЛЬ", "stats":"📊 СТАТИСТИКА", "drivers":"🚗 ВОДИТЕЛИ", "pending_drivers":"⏳ ПОДТВЕРЖДЕНИЕ", "customers":"👥 КЛИЕНТЫ", "all_orders":"📦 ЗАКАЗЫ", "online_drivers":"🟢 ОНЛАЙН ВОДИТЕЛИ", "blocked":"🚫 ЗАБЛОКИРОВАННЫЕ", "support":"📩 ОБРАЩЕНИЯ", "settings":"⚙️ НАСТРОЙКИ", "not_allowed":"⛔ Нет доступа.", "blocked_user":"🚫 Ваш аккаунт заблокирован.", "support_prompt":"📩 Напишите обращение:", "support_sent":"✅ Обращение отправлено."
    },
    "en": {
        "lang":"🌐 Choose language", "role":"Who are you?", "customer":"👤 Customer", "driver":"🚗 Driver", "name":"👤 Enter your name", "phone":"📱 Send your phone number", "saved":"✅ Saved.",
        "menu":"🚕 TAXI BOR MI?", "order":"🚕 ORDER TAXI", "orders":"📋 MY ORDERS", "profile":"👤 PROFILE", "help":"📞 HELP", "people":"👥 HOW MANY PEOPLE?", "delivery":"📦 DELIVERY", "fromto":"📍 FROM → TO?\nExample: 5/5 → Kaltsovo", "gps":"📍 Send GPS?", "gps_yes":"📍 SEND GPS", "gps_no":"⏭ CONTINUE WITHOUT GPS", "price":"💰 CHOOSE PRICE", "other":"✍️ OTHER PRICE", "confirm":"🚕 ORDER", "send":"✅ SEND ORDER", "back":"❌ CANCEL", "bad_route":"❗ Format: From → To. Example: 5/5 → Kaltsovo", "bad_price":"❗ Price must be 5,000–1,000,000 sum.", "sent":"✅ Order sent to drivers.", "no_drivers":"😔 No online drivers right now.", "online":"🟢 ONLINE", "offline":"🔴 OFFLINE", "driver_menu":"🚗 DRIVER PANEL", "register_driver":"🚗 BECOME A DRIVER", "car":"🚗 Car model", "plate":"🔢 Plate number", "license":"🪪 Send license photo", "tech":"📄 Send vehicle document photo", "car_photo":"🚗 Send car photo", "rules":"📋 Accept the rules?", "accept_rules":"✅ ACCEPT", "pending":"⏳ Your application is waiting for admin approval.", "approved":"✅ You are approved!", "rejected":"❌ Application rejected.", "new_order":"🚕 NEW ORDER", "take":"✅ TAKE ORDER", "reject":"❌ REJECT", "taken":"✅ Order assigned to you.", "finish":"🏁 FINISH ORDER", "no_answer":"📵 CUSTOMER DID NOT ANSWER", "contact":"📞 CALL CUSTOMER", "need":"🚕 The driver could not reach you.\n\nDo you still need the car?\n\n⏱ Reply within 1 minute.", "yes_need":"✅ YES, I NEED IT", "no_need":"❌ NO, I DON'T", "reopened":"🔎 Looking for another driver.", "cancelled":"❌ Order cancelled.", "expired":"⏱ Time expired. Order cancelled.", "rate":"⭐ RATE THE DRIVER", "rated":"✅ Thanks! Rating saved.", "admin":"👨‍💼 ADMIN PANEL", "stats":"📊 STATISTICS", "drivers":"🚗 DRIVERS", "pending_drivers":"⏳ APPROVALS", "customers":"👥 CUSTOMERS", "all_orders":"📦 ORDERS", "online_drivers":"🟢 ONLINE DRIVERS", "blocked":"🚫 BLOCKED", "support":"📩 SUPPORT", "settings":"⚙️ SETTINGS", "not_allowed":"⛔ No access.", "blocked_user":"🚫 Your account is blocked.", "support_prompt":"📩 Write your message:", "support_sent":"✅ Message sent."
    }
}

def tr(lang, key):
    return T.get(lang, T["uz"]).get(key, T["uz"].get(key, key))

# ------------------------- keyboards -------------------------

def lang_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇺🇿 O‘zbekcha", callback_data="lang:uz"), InlineKeyboardButton(text="🇺🇿 Ўзбекча", callback_data="lang:uz_cy")],
        [InlineKeyboardButton(text="🇷🇺 Русский", callback_data="lang:ru"), InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:en")]
    ])

def role_kb(lang):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr(lang,"customer"),callback_data="role:customer"),InlineKeyboardButton(text=tr(lang,"driver"),callback_data="role:driver")]])

def contact_kb(lang):
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📱 Telefon raqamini yuborish", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)

def customer_kb(lang):
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=tr(lang,"order"))],[KeyboardButton(text=tr(lang,"orders")),KeyboardButton(text=tr(lang,"profile"))],[KeyboardButton(text=tr(lang,"help"))]], resize_keyboard=True)

def people_kb(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1️⃣",callback_data="people:1"),InlineKeyboardButton(text="2️⃣",callback_data="people:2"),InlineKeyboardButton(text="3️⃣",callback_data="people:3"),InlineKeyboardButton(text="4️⃣",callback_data="people:4")],
        [InlineKeyboardButton(text=tr(lang,"delivery"),callback_data="people:0")]
    ])

def gps_kb(lang):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr(lang,"gps_yes"),callback_data="gps:yes"),InlineKeyboardButton(text=tr(lang,"gps_no"),callback_data="gps:no")]])

def price_kb(lang):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="5 000",callback_data="price:5000"),InlineKeyboardButton(text="10 000",callback_data="price:10000")],
        [InlineKeyboardButton(text="15 000",callback_data="price:15000"),InlineKeyboardButton(text="20 000",callback_data="price:20000")],
        [InlineKeyboardButton(text=tr(lang,"other"),callback_data="price:other")]
    ])

def confirm_kb(lang):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr(lang,"send"),callback_data="order:send"),InlineKeyboardButton(text=tr(lang,"back"),callback_data="order:cancel")]])

def driver_kb(lang, online=False):
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=tr(lang,"online") if not online else tr(lang,"offline"))],[KeyboardButton(text="📋 FAOL BUYURTMALAR"),KeyboardButton(text="💰 DAROMAD")],[KeyboardButton(text="👤 PROFIL")]], resize_keyboard=True)

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistika",callback_data="admin:stats"),InlineKeyboardButton(text="🚗 Haydovchilar",callback_data="admin:drivers")],
        [InlineKeyboardButton(text="⏳ Tasdiqlash",callback_data="admin:pending"),InlineKeyboardButton(text="👥 Mijozlar",callback_data="admin:customers")],
        [InlineKeyboardButton(text="📦 Buyurtmalar",callback_data="admin:orders"),InlineKeyboardButton(text="🟢 Online",callback_data="admin:online")],
        [InlineKeyboardButton(text="🚫 Bloklanganlar",callback_data="admin:blocked"),InlineKeyboardButton(text="📩 Murojaatlar",callback_data="admin:support")]
    ])

# ------------------------- FSM -------------------------
class Reg(StatesGroup):
    name = State(); phone = State(); car = State(); plate = State(); license = State(); tech = State(); car_photo = State(); rules = State()
class Order(StatesGroup):
    route = State(); gps = State(); price = State(); custom_price = State()
class Support(StatesGroup):
    text = State()

# ------------------------- helpers -------------------------
async def ensure_user(tg_id, lang=None, role=None):
    u = await get_user(tg_id)
    if not u:
        await db_exec("INSERT INTO users(tg_id,role,lang,created_at) VALUES(?,?,?,?)", (tg_id, role or "customer", lang or "uz", now()))
    else:
        if lang: await db_exec("UPDATE users SET lang=? WHERE tg_id=?", (lang,tg_id))
        if role: await db_exec("UPDATE users SET role=? WHERE tg_id=?", (role,tg_id))
    return await get_user(tg_id)

def fmt_money(n):
    return f"{n:,}".replace(","," ") + " so‘m"

def order_text(r, title="🚕 BUYURTMA"):
    service = "📦 DASTAVKA" if r["service"] == "delivery" else f"👥 {r['passengers']} kishi"
    gps = "✅" if r["gps_lat"] is not None else "❌"
    return f"{title} #{r['id']}\n\n{service}\n📍 {r['route_text']}\n📍 GPS: {gps}\n💰 {fmt_money(r['price'])}"

def valid_route(s):
    return "→" in s and len(s.strip()) >= 5

async def send_order_to_drivers(order_id, exclude_driver=None):
    rows = await db_exec("SELECT * FROM drivers WHERE approved=1 AND blocked=0 AND online=1 AND active_orders<?", (MAX_ACTIVE,), fetchall=True)
    if exclude_driver:
        rows = [r for r in rows if r["tg_id"] != exclude_driver]
    if not rows: return 0
    r = await db_exec("SELECT * FROM orders WHERE id=?", (order_id,), True)
    sent = 0
    for d in rows:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ BUYURTMANI OLISH",callback_data=f"take:{order_id}"),InlineKeyboardButton(text="❌ RAD ETISH",callback_data=f"reject:{order_id}")]])
        try:
            await bot.send_message(d["tg_id"], order_text(r,"🚕 YANGI BUYURTMA"), reply_markup=kb)
            if r["gps_lat"] is not None:
                await bot.send_location(d["tg_id"], r["gps_lat"], r["gps_lon"])
            sent += 1
        except Exception:
            pass
    return sent

# ------------------------- start / registration -------------------------
@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    u = await get_user(m.from_user.id)
    if u and u["blocked"]:
        return await m.answer(tr(u["lang"],"blocked_user"))
    if not u:
        await ensure_user(m.from_user.id)
        return await m.answer(T["uz"]["lang"], reply_markup=lang_kb())
    if u["role"] == "driver":
        d = await get_driver(m.from_user.id)
        if d and d["approved"]:
            return await m.answer(tr(u["lang"],"driver_menu"), reply_markup=driver_kb(u["lang"], bool(d["online"])))
        if d: return await m.answer(tr(u["lang"],"pending"))
    if not u["name"] or not u["phone"]:
        await state.set_state(Reg.name)
        return await m.answer(tr(u["lang"],"name"))
    await m.answer(tr(u["lang"],"menu"), reply_markup=customer_kb(u["lang"]))

@dp.callback_query(F.data.startswith("lang:"))
async def choose_lang(c: CallbackQuery, state: FSMContext):
    lang = c.data.split(":",1)[1]
    await ensure_user(c.from_user.id, lang=lang)
    await c.message.edit_text(tr(lang,"role"), reply_markup=role_kb(lang))
    await c.answer()

@dp.callback_query(F.data.startswith("role:"))
async def choose_role(c: CallbackQuery, state: FSMContext):
    role = c.data.split(":",1)[1]
    u = await ensure_user(c.from_user.id, role=role)
    lang = u["lang"]
    if role == "customer":
        await state.set_state(Reg.name)
        await c.message.edit_text(tr(lang,"name"))
    else:
        await state.set_state(Reg.name)
        await c.message.edit_text(tr(lang,"name") + "\n\n🚗 Haydovchi ro‘yxati")
        await state.update_data(driver_reg=True)
    await c.answer()

@dp.message(Reg.name)
async def reg_name(m: Message, state: FSMContext):
    u = await get_user(m.from_user.id)
    if not m.text or len(m.text.strip()) < 2: return await m.answer(tr(u["lang"],"name"))
    await state.update_data(reg_name=m.text.strip())
    data = await state.get_data()
    await db_exec("UPDATE users SET name=? WHERE tg_id=?", (m.text.strip(),m.from_user.id))
    if data.get("driver_reg"):
        await state.set_state(Reg.phone)
        return await m.answer(tr(u["lang"],"phone"), reply_markup=contact_kb(u["lang"]))
    await state.set_state(Reg.phone)
    await m.answer(tr(u["lang"],"phone"), reply_markup=contact_kb(u["lang"]))

@dp.message(Reg.phone, F.contact)
async def reg_phone(m: Message, state: FSMContext):
    u = await get_user(m.from_user.id)
    phone = m.contact.phone_number
    await db_exec("UPDATE users SET phone=? WHERE tg_id=?", (phone,m.from_user.id))
    data = await state.get_data()
    if data.get("driver_reg"):
        await state.set_state(Reg.car)
        return await m.answer(tr(u["lang"],"car"), reply_markup=None)
    await state.clear()
    await m.answer(tr(u["lang"],"saved"), reply_markup=customer_kb(u["lang"]))

@dp.message(Reg.phone)
async def reg_phone_text(m: Message, state: FSMContext):
    u = await get_user(m.from_user.id)
    if not m.text: return await m.answer(tr(u["lang"],"phone"), reply_markup=contact_kb(u["lang"]))
    phone = re.sub(r"[^+0-9]", "", m.text)
    await db_exec("UPDATE users SET phone=? WHERE tg_id=?", (phone,m.from_user.id))
    data = await state.get_data()
    if data.get("driver_reg"):
        await state.set_state(Reg.car); return await m.answer(tr(u["lang"],"car"), reply_markup=None)
    await state.clear(); await m.answer(tr(u["lang"],"saved"), reply_markup=customer_kb(u["lang"]))

@dp.message(Reg.car)
async def reg_car(m: Message, state: FSMContext):
    await state.update_data(car=m.text.strip()); await state.set_state(Reg.plate); await m.answer("🔢 Davlat raqamini kiriting")

@dp.message(Reg.plate)
async def reg_plate(m: Message, state: FSMContext):
    await state.update_data(plate=m.text.strip().upper()); await state.set_state(Reg.license); await m.answer("🪪 Prava rasmini yuboring")

@dp.message(Reg.license, F.photo)
async def reg_license(m: Message, state: FSMContext):
    await state.update_data(license=m.photo[-1].file_id); await state.set_state(Reg.tech); await m.answer("📄 Texpasport rasmini yuboring")

@dp.message(Reg.tech, F.photo)
async def reg_tech(m: Message, state: FSMContext):
    await state.update_data(tech=m.photo[-1].file_id); await state.set_state(Reg.car_photo); await m.answer("🚗 Mashina rasmini yuboring")

@dp.message(Reg.car_photo, F.photo)
async def reg_car_photo(m: Message, state: FSMContext):
    await state.update_data(car_photo=m.photo[-1].file_id); await state.set_state(Reg.rules)
    await m.answer("📋 Haydovchilik qoidalarini qabul qilasizmi?", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ QABUL QILAMAN",callback_data="driver_rules:yes")]]))

@dp.callback_query(F.data == "driver_rules:yes")
async def driver_rules(c: CallbackQuery, state: FSMContext):
    data = await state.get_data(); u = await get_user(c.from_user.id)
    await db_exec("INSERT OR REPLACE INTO drivers(tg_id,name,phone,car_model,plate,license_file,tech_file,car_photo,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (c.from_user.id,u["name"],u["phone"],data["car"],data["plate"],data["license"],data["tech"],data["car_photo"],now()))
    await db_exec("UPDATE users SET role='driver' WHERE tg_id=?", (c.from_user.id,))
    await state.clear(); await c.message.edit_text("⏳ Arizangiz yuborildi. Admin tasdig‘ini kuting.")
    await bot.send_message(ADMIN_ID, f"🚗 YANGI HAYDOVCHI\nID: <code>{c.from_user.id}</code>\n👤 {u['name']}\n📞 {u['phone']}\n🚗 {data['car']}\n🔢 {data['plate']}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ TASDIQLASH",callback_data=f"approve:{c.from_user.id}"),InlineKeyboardButton(text="❌ RAD ETISH",callback_data=f"deny:{c.from_user.id}")]]))
    await c.answer()

# ------------------------- customer order -------------------------
@dp.message(F.text)
async def text_router(m: Message, state: FSMContext):
    current = await state.get_state()
    if current: return
    u = await get_user(m.from_user.id)
    if not u or u["blocked"]: return
    d = await get_driver(m.from_user.id)
    txt = m.text
    if d and d["approved"]:
        if txt in (tr(u["lang"],"online"), tr(u["lang"],"offline")):
            new = 0 if d["online"] else 1
            await db_exec("UPDATE drivers SET online=? WHERE tg_id=?", (new,m.from_user.id)); return await m.answer(tr(u["lang"],"online") if new else tr(u["lang"],"offline"), reply_markup=driver_kb(u["lang"], bool(new)))
        if txt == "📋 FAOL BUYURTMALAR":
            rows=await db_exec("SELECT * FROM orders WHERE driver_id=? AND status='ACCEPTED' ORDER BY id DESC",(m.from_user.id,),fetchall=True)
            return await m.answer("\n\n".join(order_text(r) for r in rows) if rows else "📋 Faol buyurtma yo‘q.")
        if txt == "💰 DAROMAD":
            r=await db_exec("SELECT COALESCE(SUM(price),0) s FROM orders WHERE driver_id=? AND status='COMPLETED'",(m.from_user.id,),True)
            return await m.answer(f"💰 Jami daromad: {fmt_money(r['s'])}")
        if txt == "👤 PROFIL":
            return await m.answer(f"👤 {d['name']}\n📞 {d['phone']}\n🚗 {d['car_model']}\n🔢 {d['plate']}\n⭐ {d['rating']:.1f}")
    if txt == tr(u["lang"],"order"):
        await state.set_state(Order.route); return await m.answer(tr(u["lang"],"people"), reply_markup=people_kb(u["lang"]))
    if txt == tr(u["lang"],"orders"):
        rows=await db_exec("SELECT * FROM orders WHERE customer_id=? ORDER BY id DESC LIMIT 10",(m.from_user.id,),fetchall=True)
        return await m.answer("\n\n".join(order_text(r) for r in rows) if rows else "📋 Buyurtmalar yo‘q.")
    if txt == tr(u["lang"],"profile"):
        return await m.answer(f"👤 {u['name']}\n📞 {u['phone']}")
    if txt == tr(u["lang"],"help"):
        await state.set_state(Support.text); return await m.answer(tr(u["lang"],"support_prompt"))

@dp.callback_query(F.data.startswith("people:"))
async def people(c: CallbackQuery, state: FSMContext):
    u=await get_user(c.from_user.id); n=int(c.data.split(":")[1]); await state.update_data(service="delivery" if n==0 else "passenger",passengers=n); await state.set_state(Order.route); await c.message.edit_text(tr(u["lang"],"fromto")); await c.answer()

@dp.message(Order.gps)
async def gps_skip_or_wait(m: Message, state: FSMContext):
    u = await get_user(m.from_user.id)
    if m.text == tr(u["lang"], "gps_no"):
        await state.update_data(gps_lat=None, gps_lon=None, gps_needed=False)
        await state.set_state(Order.price)
        return await m.answer(tr(u["lang"], "price"), reply_markup=price_kb(u["lang"]))
    await m.answer("📍 GPS tugmasini bosing yoki GPSsiz davom eting.")

@dp.message(Order.route)
async def route(m: Message, state: FSMContext):
    u=await get_user(m.from_user.id)
    if not valid_route(m.text): return await m.answer(tr(u["lang"],"bad_route"))
    await state.update_data(route=m.text.strip()); await state.set_state(Order.gps); await m.answer(tr(u["lang"],"gps"), reply_markup=gps_kb(u["lang"]))

@dp.callback_query(F.data.startswith("gps:"))
async def gps_choice(c: CallbackQuery, state: FSMContext):
    u=await get_user(c.from_user.id); choice=c.data.split(":")[1]
    if choice == "yes":
        await state.update_data(gps_needed=True)
        await state.set_state(Order.gps)
        kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=tr(u["lang"],"gps_yes"), request_location=True)], [KeyboardButton(text=tr(u["lang"],"gps_no"))]], resize_keyboard=True, one_time_keyboard=True)
        await c.message.edit_text("📍 GPS yuborish uchun tugmani bosing.")
        await bot.send_message(c.from_user.id, "📍 GPS yuboring yoki GPSsiz davom eting.", reply_markup=kb)
    else:
        await state.update_data(gps_lat=None, gps_lon=None, gps_needed=False)
        await state.set_state(Order.price)
        await c.message.edit_text(tr(u["lang"],"price"), reply_markup=price_kb(u["lang"]))
    await c.answer()

@dp.message(Order.gps, F.location)
async def gps_location(m: Message, state: FSMContext):
    u=await get_user(m.from_user.id); await state.update_data(gps_lat=m.location.latitude,gps_lon=m.location.longitude); await state.set_state(Order.price); await m.answer(tr(u["lang"],"price"),reply_markup=price_kb(u["lang"]))

@dp.callback_query(F.data.startswith("price:"))
async def price(c: CallbackQuery, state: FSMContext):
    u=await get_user(c.from_user.id); val=c.data.split(":",1)[1]
    if val=="other": await state.set_state(Order.custom_price); await c.message.edit_text("✍️ Narxni so‘mda kiriting:"); return await c.answer()
    await state.update_data(price=int(val)); await show_confirm(c.message,c.from_user.id,state); await c.answer()

@dp.message(Order.custom_price)
async def custom_price(m: Message, state: FSMContext):
    u=await get_user(m.from_user.id)
    try: p=int(re.sub(r"\D","",m.text or ""))
    except: p=0
    if p<MIN_PRICE or p>MAX_PRICE: return await m.answer(tr(u["lang"],"bad_price"))
    await state.update_data(price=p); await show_confirm(m,m.from_user.id,state)

async def show_confirm(target, uid, state):
    u=await get_user(uid); data=await state.get_data(); service=data.get("service"); service_text="📦 DASTAVKA" if service=="delivery" else f"👥 {data['passengers']} kishi"; gps="✅" if data.get("gps_lat") is not None else "❌"
    text=f"🚕 BUYURTMA\n\n{service_text}\n📍 {data['route']}\n📍 GPS: {gps}\n💰 {fmt_money(data['price'])}"
    if isinstance(target, CallbackQuery): await target.message.edit_text(text,reply_markup=confirm_kb(u["lang"]))
    else: await target.answer(text,reply_markup=confirm_kb(u["lang"]))

@dp.callback_query(F.data == "order:cancel")
async def cancel_order(c: CallbackQuery,state:FSMContext):
    u=await get_user(c.from_user.id); await state.clear(); await c.message.edit_text(tr(u["lang"],"cancelled")); await c.answer()

@dp.callback_query(F.data == "order:send")
async def send_order(c: CallbackQuery,state:FSMContext):
    u=await get_user(c.from_user.id); data=await state.get_data()
    cur=await db_exec("INSERT INTO orders(customer_id,service,passengers,route_text,gps_lat,gps_lon,price,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",(c.from_user.id,data["service"],data["passengers"],data["route"],data.get("gps_lat"),data.get("gps_lon"),data["price"],"SEARCHING",now()))
    oid=cur; sent=await send_order_to_drivers(oid); await state.clear()
    if sent==0:
        await db_exec("UPDATE orders SET status='NO_DRIVER' WHERE id=?",(oid,)); await c.message.edit_text(tr(u["lang"],"no_drivers"))
    else:
        await c.message.edit_text(f"{tr(u['lang'],'sent')}\n\n🚕 Buyurtma #{oid}")
    await c.answer()

# ------------------------- driver actions -------------------------
@dp.callback_query(F.data.startswith("reject:"))
async def reject(c: CallbackQuery): await c.message.edit_reply_markup(reply_markup=None); await c.answer("❌ Rad etildi.")

@dp.callback_query(F.data.startswith("take:"))
async def take(c: CallbackQuery):
    oid=int(c.data.split(":")[1]); d=await get_driver(c.from_user.id); u=await get_user(c.from_user.id)
    if not d or not d["approved"] or d["blocked"]: return await c.answer("⛔ Ruxsat yo‘q.",show_alert=True)
    async with DB_LOCK:
        dbx=conn(); cur=dbx.execute("SELECT * FROM orders WHERE id=?",(oid,)); r=cur.fetchone()
        if not r or r["status"]!="SEARCHING": dbx.close(); return await c.answer("❌ Buyurtma allaqachon olingan.",show_alert=True)
        cur=dbx.execute("UPDATE drivers SET active_orders=active_orders+1 WHERE tg_id=? AND approved=1 AND blocked=0 AND online=1 AND active_orders<?",(c.from_user.id,MAX_ACTIVE))
        if cur.rowcount!=1: dbx.close(); return await c.answer("❌ Faol buyurtmalar limiti to‘lgan yoki offline.",show_alert=True)
        dbx.execute("UPDATE orders SET driver_id=?,status='ACCEPTED',accepted_at=? WHERE id=? AND status='SEARCHING'",(c.from_user.id,now(),oid)); dbx.commit(); dbx.close()
    r=await db_exec("SELECT * FROM orders WHERE id=?",(oid,),True); customer=await get_user(r["customer_id"])
    await c.message.edit_text(order_text(r)+f"\n\n👤 Mijoz: {customer['name']}\n📞 {customer['phone']}",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr(u['lang'],'contact'),callback_data=f"noop:{oid}"),InlineKeyboardButton(text=tr(u['lang'],'no_answer'),callback_data=f"noanswer:{oid}")],[InlineKeyboardButton(text=tr(u['lang'],'finish'),callback_data=f"finish:{oid}")]]))
    if r["gps_lat"] is not None: await bot.send_location(c.from_user.id,r["gps_lat"],r["gps_lon"])
    cu=await get_user(r["customer_id"]); await bot.send_message(r["customer_id"],f"🚕 HAYDOVCHI TOPILDI\n\n👤 {d['name']}\n📞 {d['phone']}\n🚗 {d['car_model']}\n🔢 {d['plate']}")
    await c.answer(tr(u["lang"],"taken"))

@dp.callback_query(F.data.startswith("noop:"))
async def noop(c: CallbackQuery): await c.answer("📞 Telegram orqali qo‘ng‘iroq qilish uchun yuqoridagi telefon raqamidan foydalaning.",show_alert=True)

@dp.callback_query(F.data.startswith("finish:"))
async def finish(c: CallbackQuery):
    oid=int(c.data.split(":")[1]); r=await db_exec("SELECT * FROM orders WHERE id=?",(oid,),True)
    if not r or r["driver_id"]!=c.from_user.id or r["status"]!="ACCEPTED": return await c.answer("❌ Buyurtma faol emas.",show_alert=True)
    await db_exec("UPDATE orders SET status='COMPLETED',completed_at=? WHERE id=?",(now(),oid)); await db_exec("UPDATE drivers SET active_orders=CASE WHEN active_orders>0 THEN active_orders-1 ELSE 0 END WHERE tg_id=?",(c.from_user.id,))
    await c.message.edit_text("🏁 Buyurtma yakunlandi.")
    await bot.send_message(r["customer_id"],tr((await get_user(r["customer_id"]))["lang"],"rate"),reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"{i} ⭐",callback_data=f"rate:{oid}:{i}") for i in range(1,6)]]))
    await c.answer()

@dp.callback_query(F.data.startswith("noanswer:"))
async def no_answer(c: CallbackQuery):
    oid=int(c.data.split(":")[1]); r=await db_exec("SELECT * FROM orders WHERE id=?",(oid,),True)
    if not r or r["driver_id"]!=c.from_user.id or r["status"]!="ACCEPTED": return await c.answer("❌ Buyurtma faol emas.",show_alert=True)
    await db_exec("UPDATE orders SET status='WAITING_CUSTOMER',previous_driver_id=? WHERE id=?",(c.from_user.id,oid)); await db_exec("UPDATE drivers SET active_orders=CASE WHEN active_orders>0 THEN active_orders-1 ELSE 0 END WHERE tg_id=?",(c.from_user.id,))
    cu=await get_user(r["customer_id"]); kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr(cu['lang'],'yes_need'),callback_data=f"need:yes:{oid}"),InlineKeyboardButton(text=tr(cu['lang'],'no_need'),callback_data=f"need:no:{oid}")]])
    await bot.send_message(r["customer_id"],tr(cu["lang"],"need"),reply_markup=kb)
    await c.message.edit_text("⏳ Mijozdan javob kutilmoqda: 1 daqiqa.")
    asyncio.create_task(no_answer_timeout(oid))
    await c.answer()

async def no_answer_timeout(oid):
    await asyncio.sleep(NO_ANSWER_SECONDS)
    r=await db_exec("SELECT * FROM orders WHERE id=?",(oid,),True)
    if r and r["status"]=="WAITING_CUSTOMER":
        await db_exec("UPDATE orders SET status='CANCELLED' WHERE id=?",(oid,)); u=await get_user(r["customer_id"]); await bot.send_message(r["customer_id"],tr(u["lang"],"expired"))

@dp.callback_query(F.data.startswith("need:"))
async def need_answer(c: CallbackQuery):
    _,ans,oid=c.data.split(":"); oid=int(oid); r=await db_exec("SELECT * FROM orders WHERE id=?",(oid,),True); u=await get_user(c.from_user.id)
    if not r or r["status"]!="WAITING_CUSTOMER" or r["customer_id"]!=c.from_user.id: return await c.answer("❌ Buyurtma faol emas.",show_alert=True)
    if ans=="no": await db_exec("UPDATE orders SET status='CANCELLED' WHERE id=?",(oid,)); await c.message.edit_text(tr(u["lang"],"cancelled")); return await c.answer()
    await db_exec("UPDATE orders SET status='SEARCHING',driver_id=NULL WHERE id=?",(oid,)); sent=await send_order_to_drivers(oid,exclude_driver=r["previous_driver_id"])
    if sent: await c.message.edit_text(tr(u["lang"],"reopened"))
    else: await db_exec("UPDATE orders SET status='NO_DRIVER' WHERE id=?",(oid,)); await c.message.edit_text(tr(u["lang"],"no_drivers"))
    await c.answer()

@dp.callback_query(F.data.startswith("rate:"))
async def rate(c: CallbackQuery):
    _,oid_s,score_s=c.data.split(":"); oid=int(oid_s); score=int(score_s); r=await db_exec("SELECT * FROM orders WHERE id=?",(oid,),True)
    if not r or r["customer_id"]!=c.from_user.id or not r["driver_id"]: return await c.answer("❌ Ruxsat yo‘q.",show_alert=True)
    try:
        await db_exec("INSERT INTO ratings(order_id,driver_id,customer_id,rating,created_at) VALUES(?,?,?,?,?)",(oid,r["driver_id"],c.from_user.id,score,now()))
    except sqlite3.IntegrityError: return await c.answer("⭐ Reyting allaqachon berilgan.",show_alert=True)
    await db_exec("UPDATE drivers SET rating=((rating*rating_count)+?)/(rating_count+1),rating_count=rating_count+1 WHERE tg_id=?",(score,r["driver_id"])); await c.message.edit_text(tr((await get_user(c.from_user.id))["lang"],"rated")); await c.answer()

# ------------------------- support -------------------------
@dp.message(Support.text)
async def support_text(m: Message, state: FSMContext):
    u=await get_user(m.from_user.id); await db_exec("INSERT INTO support(user_id,text,created_at) VALUES(?,?,?)",(m.from_user.id,m.text or "",now())); await state.clear(); await m.answer(tr(u["lang"],"support_sent"),reply_markup=customer_kb(u["lang"])); await bot.send_message(ADMIN_ID,f"📩 MUROJAAT\nID: <code>{m.from_user.id}</code>\n{m.text}")

# ------------------------- admin -------------------------
def admin_check(uid): return uid==ADMIN_ID

@dp.message(Command("admin"))
async def admin(m: Message):
    if not admin_check(m.from_user.id): return await m.answer("⛔")
    await m.answer("👨‍💼 TAXI BOR MI? — ADMIN PANEL",reply_markup=admin_kb())

@dp.callback_query(F.data.startswith("approve:"))
async def approve(c: CallbackQuery):
    if not admin_check(c.from_user.id): return await c.answer("⛔",show_alert=True)
    uid=int(c.data.split(":")[1]); await db_exec("UPDATE drivers SET approved=1 WHERE tg_id=?",(uid,)); await bot.send_message(uid,"✅ Siz tasdiqlandingiz! Endi /start bosing va ONLINE bo‘ling."); await c.message.edit_text(c.message.text+"\n\n✅ TASDIQLANDI"); await c.answer()

@dp.callback_query(F.data.startswith("deny:"))
async def deny(c: CallbackQuery):
    if not admin_check(c.from_user.id): return await c.answer("⛔",show_alert=True)
    uid=int(c.data.split(":")[1]); await db_exec("DELETE FROM drivers WHERE tg_id=?",(uid,)); await db_exec("UPDATE users SET role='customer' WHERE tg_id=?",(uid,)); await bot.send_message(uid,"❌ Arizangiz rad etildi."); await c.message.edit_text(c.message.text+"\n\n❌ RAD ETILDI"); await c.answer()

@dp.callback_query(F.data.startswith("admin:"))
async def admin_menu(c: CallbackQuery):
    if not admin_check(c.from_user.id): return await c.answer("⛔",show_alert=True)
    key=c.data.split(":")[1]
    if key=="stats":
        vals={}
        for name,sql in [("Mijozlar","SELECT COUNT(*) n FROM users WHERE role='customer'"),("Haydovchilar","SELECT COUNT(*) n FROM drivers"),("Online","SELECT COUNT(*) n FROM drivers WHERE online=1 AND approved=1"),("Kutilmoqda","SELECT COUNT(*) n FROM drivers WHERE approved=0"),("Buyurtmalar","SELECT COUNT(*) n FROM orders"),("Faol","SELECT COUNT(*) n FROM orders WHERE status='ACCEPTED'")]: vals[name]=(await db_exec(sql,fetchone=True))["n"]
        return await c.message.edit_text("📊 STATISTIKA\n\n"+"\n".join(f"• {k}: {v}" for k,v in vals.items()),reply_markup=admin_kb())
    if key=="pending":
        rows=await db_exec("SELECT * FROM drivers WHERE approved=0 ORDER BY created_at DESC",fetchall=True)
        if not rows:return await c.message.edit_text("⏳ Kutilayotgan haydovchi yo‘q.",reply_markup=admin_kb())
        text="⏳ TASDIQLASH\n\n"+"\n".join(f"ID: <code>{r['tg_id']}</code> — {r['name']} — {r['car_model']} — {r['plate']}" for r in rows[:20])
        return await c.message.edit_text(text,reply_markup=admin_kb())
    if key=="drivers":
        rows=await db_exec("SELECT * FROM drivers ORDER BY created_at DESC LIMIT 30",fetchall=True)
        return await c.message.edit_text("🚗 HAYDOVCHILAR\n\n"+("\n".join(f"{r['name']} | {r['car_model']} | {r['plate']} | {'🟢' if r['online'] else '🔴'} | ⭐{r['rating']:.1f}" for r in rows) or "Yo‘q"),reply_markup=admin_kb())
    if key=="customers":
        rows=await db_exec("SELECT * FROM users WHERE role='customer' ORDER BY created_at DESC LIMIT 30",fetchall=True)
        return await c.message.edit_text("👥 MIJOZLAR\n\n"+("\n".join(f"{r['name']} | {r['phone']} | ID {r['tg_id']}" for r in rows) or "Yo‘q"),reply_markup=admin_kb())
    if key=="orders":
        rows=await db_exec("SELECT * FROM orders ORDER BY id DESC LIMIT 30",fetchall=True)
        return await c.message.edit_text("📦 BUYURTMALAR\n\n"+("\n".join(f"#{r['id']} | {r['status']} | {fmt_money(r['price'])}" for r in rows) or "Yo‘q"),reply_markup=admin_kb())
    if key=="online":
        rows=await db_exec("SELECT * FROM drivers WHERE online=1 AND approved=1",fetchall=True)
        return await c.message.edit_text("🟢 ONLINE\n\n"+("\n".join(f"{r['name']} | {r['phone']} | {r['car_model']}" for r in rows) or "Yo‘q"),reply_markup=admin_kb())
    if key=="blocked":
        rows=await db_exec("SELECT * FROM users WHERE blocked=1",fetchall=True)
        return await c.message.edit_text("🚫 BLOKLANGANLAR\n\n"+("\n".join(f"{r['name']} | {r['tg_id']}" for r in rows) or "Yo‘q"),reply_markup=admin_kb())
    if key=="support":
        rows=await db_exec("SELECT * FROM support WHERE status='OPEN' ORDER BY id DESC LIMIT 20",fetchall=True)
        return await c.message.edit_text("📩 MUROJAATLAR\n\n"+("\n".join(f"#{r['id']} | ID {r['user_id']} | {r['text'][:100]}" for r in rows) or "Yo‘q"),reply_markup=admin_kb())
    await c.answer()

# ------------------------- main -------------------------
async def main():
    init_db()
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
