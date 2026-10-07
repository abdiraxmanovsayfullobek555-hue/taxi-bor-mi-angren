
import os
import re
import asyncio
import logging
import sqlite3
import time
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# ============================================================
# CONFIG
# Railway Variables:
# BOT_TOKEN=...
# ADMIN_ID=...
# DB_PATH=taxi_bor_mi.db
# ============================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
DB_PATH = os.getenv("DB_PATH", "taxi_bor_mi.db")

ROUTE = "OBLIQ ↔ ANGREN"
MAX_ACTIVE = 4
SEARCH_TIMEOUT = 120
NO_ANSWER_TIMEOUT = 60
MIN_PRICE = 5000
MAX_PRICE = 1_000_000
FIXED_PRICES = {5000, 10000, 15000, 20000}

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("taxi_bor_mi")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
DB_LOCK = asyncio.Lock()


# ============================================================
# HELPERS
# ============================================================
def now():
    return datetime.utcnow().isoformat(timespec="seconds")


def money(value):
    return f"{int(value):,}".replace(",", " ") + " so‘m"


def db():
    c = sqlite3.connect(DB_PATH, timeout=30)
    c.row_factory = sqlite3.Row
    return c


async def q(sql, params=(), one=False, all_rows=False):
    async with DB_LOCK:
        c = db()
        cur = c.execute(sql, params)
        result = cur.fetchone() if one else cur.fetchall() if all_rows else cur.lastrowid
        c.commit()
        c.close()
        return result


async def transaction(fn):
    async with DB_LOCK:
        c = db()
        c.execute("BEGIN IMMEDIATE")
        try:
            result = fn(c)
            c.commit()
            return result
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


def init_db():
    c = db()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        tg_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        phone TEXT NOT NULL,
        lang TEXT NOT NULL DEFAULT 'uz',
        blocked INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS drivers(
        tg_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        phone TEXT NOT NULL,
        car_model TEXT NOT NULL,
        plate TEXT NOT NULL,
        route TEXT NOT NULL,
        license_file TEXT NOT NULL,
        tech_file TEXT NOT NULL,
        car_file TEXT NOT NULL,
        approved INTEGER NOT NULL DEFAULT 0,
        online INTEGER NOT NULL DEFAULT 0,
        blocked INTEGER NOT NULL DEFAULT 0,
        active_orders INTEGER NOT NULL DEFAULT 0,
        rating_sum INTEGER NOT NULL DEFAULT 0,
        rating_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customer_id INTEGER NOT NULL,
        driver_id INTEGER,
        service TEXT NOT NULL,
        passengers INTEGER NOT NULL DEFAULT 0,
        origin TEXT NOT NULL,
        destination TEXT NOT NULL,
        lat REAL,
        lon REAL,
        price INTEGER NOT NULL,
        status TEXT NOT NULL,
        excluded_driver INTEGER,
        no_answer_started REAL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS offers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL,
        driver_id INTEGER NOT NULL,
        status TEXT NOT NULL,
        message_id INTEGER,
        chat_id INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(order_id, driver_id)
    );

    CREATE TABLE IF NOT EXISTS ratings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER UNIQUE NOT NULL,
        rating INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS support_tickets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER,
        actor_id INTEGER,
        event TEXT NOT NULL,
        details TEXT,
        created_at TEXT NOT NULL
    );
    """)
    c.commit()
    c.close()


async def get_user(uid):
    return await q("SELECT * FROM users WHERE tg_id=?", (uid,), one=True)


async def get_driver(uid):
    return await q("SELECT * FROM drivers WHERE tg_id=?", (uid,), one=True)


async def event(order_id, actor_id, name, details=""):
    await q(
        "INSERT INTO events(order_id,actor_id,event,details,created_at) VALUES(?,?,?,?,?)",
        (order_id, actor_id, name, details, now())
    )


# ============================================================
# I18N
# ============================================================
T = {
"uz": {
    "lang_name":"O‘zbekcha","passenger":"👤 Yo‘lovchi","delivery":"📦 Dastavka",
    "driver":"🚕 Haydovchi","profile":"👤 Profil","history":"📜 Tarix","driver_orders":"📦 Buyurtmalar","income":"💰 Daromad",
    "support":"📩 Murojaat","change_lang":"🌐 Tilni o‘zgartirish",
    "main":"🏠 Asosiy menyu","cancel":"❌ Bekor qilish",
    "name":"👤 Ism-familiyangizni kiriting:","phone":"📱 Telefon raqamingizni yuboring:",
    "phone_btn":"📱 Telefon raqamimni yuborish",
    "people":"👥 Necha kishi?","origin":"📍 QAYERDAN?\nMasalan: 5/5 dan, Obliqdan\n\nQayerdan chiqishingizni yozing:",
    "destination":"🏁 QAYERGA?\nMasalan: Kaltsoga, Angren markaziga\n\nQayerga borishingizni yozing:",
    "gps":"📍 Olib ketish joyining GPS lokatsiyasini yuborishingiz mumkin.\n\nGPS yuborish majburiy emas.",
    "gps_btn":"📍 GPS yuborish","no_gps":"⏭ GPSsiz davom etish",
    "gps_bad":"📍 GPS yuboring yoki «GPSsiz davom etish» tugmasini bosing.",
    "price":"💰 Narxni tanlang:","other_price":"✍️ Boshqa narx",
    "custom_price":"💰 Narxni so‘mda kiriting:","bad_price":"❗ Narx 5 000–1 000 000 so‘m oralig‘ida bo‘lishi kerak.",
    "confirm":"📋 Buyurtmani tekshiring:","confirm_btn":"✅ BUYURTMANI TASDIQLASH",
    "edit_btn":"✏️ O‘ZGARTIRISH","created":"🔎 Buyurtma #{id} yaratildi.\n🚕 Haydovchi qidirilmoqda...",
    "no_driver":"⚠️ Hozircha mos haydovchi topilmadi. Keyinroq qayta urinib ko‘ring.",
    "order_claim":"✅ BUYURTMANI OLISH","decline":"❌ RAD ETISH",
    "noanswer_btn":"📵 MIJOZ JAVOB BERMADI","finish":"🏁 BUYURTMANI YAKUNLASH",
    "accepted":"✅ HAYDOVCHI TOPILDI!","done":"🏁 Buyurtma yakunlandi.",
    "noans":"📵 Haydovchi siz bilan bog‘lana olmadi.\nSizga hali ham mashina kerakmi?\n⏱ 1 daqiqa ichida javob bering.",
    "yes":"✅ HA, KERAK","no":"❌ YO‘Q, KERAK EMAS",
    "rating":"⭐ Haydovchini baholang:","blocked":"⛔ Akkauntingiz bloklangan.",
    "approval":"⏳ Ma’lumotlaringiz admin tasdig‘iga yuborildi.",
    "go_online":"🟢 ONLINE BO‘LISH","go_offline":"🔴 OFFLINE BO‘LISH",
    "active":"Faol buyurtmalar: {n}/4","support_prompt":"📩 Murojaatingizni yozing:",
    "saved":"✅ Murojaat yuborildi.","driver_rules":"⚠️ Qabul qilingan buyurtmani o‘zboshimchalik bilan bekor qilish mumkin emas. Mijoz javob bermasa 60 soniyalik tartib ishlaydi.\n\nQabul qilasizmi?",
    "accept_rules":"✅ Qabul qilaman","reject_rules":"❌ Qabul qilmayman",
    "driver_reg":"🚕 Haydovchi ro‘yxatdan o‘tishi","car":"🚗 Mashina modeli:","plate":"🔢 Davlat raqami:",
    "route_driver":"📍 Yo‘nalish: OBLIQ ↔ ANGREN","license":"🪪 Haydovchilik guvohnomasi rasmini yuboring:",
    "tech":"📄 Texpasport rasmini yuboring:","car_photo":"🚗 Mashinangiz rasmini yuboring:","photo_bad":"📸 Iltimos, rasm yuboring.",
    "driver_profile":"👤 {name}\n📱 {phone}\n🚗 {car}\n🔢 {plate}\n⭐ {rating}\n📊 {active}/4",
    "no_history":"📜 Tarix hozircha bo‘sh.","admin_only":"⛔ Faqat admin.",
},
"uzc": {
    "lang_name":"Ўзбекча","passenger":"👤 Йўловчи","delivery":"📦 Даставка",
    "driver":"🚕 Ҳайдовчи","profile":"👤 Профиль","history":"📜 Тарих","driver_orders":"📦 Буюртмалар","income":"💰 Даромад",
    "support":"📩 Мурожаат","change_lang":"🌐 Тилни ўзгартириш","main":"🏠 Асосий меню",
    "cancel":"❌ Бекор қилиш","name":"👤 Исм-фамилиянгизни киритинг:","phone":"📱 Телефон рақамингизни юборинг:",
    "phone_btn":"📱 Телефон рақамимни юбориш","people":"👥 Неча киши?",
    "origin":"📍 ҚАЕРДАН?\nМасалан: 5/5 дан, Облиқдан\n\nҚаердан чиқишингизни ёзинг:",
    "destination":"🏁 ҚАЕРГА?\nМасалан: Калцога, Ангрен марказига\n\nҚаерга боришингизни ёзинг:",
    "gps":"📍 Олиб кетиш жойининг GPS локатсиясини юборишингиз мумкин.\n\nGPS мажбурий эмас.",
    "gps_btn":"📍 GPS юбориш","no_gps":"⏭ GPSсиз давом этиш","gps_bad":"📍 GPS юборинг ёки «GPSсиз давом этиш» тугмасини босинг.",
    "price":"💰 Нархни танланг:","other_price":"✍️ Бошқа нарх","custom_price":"💰 Нархни сўмда киритинг:",
    "bad_price":"❗ Нарх 5 000–1 000 000 сўм оралиғида бўлиши керак.","confirm":"📋 Буюртмани текширинг:",
    "confirm_btn":"✅ БУЮРТМАНИ ТАСДИҚЛАШ","edit_btn":"✏️ ЎЗГАРТИРИШ",
    "created":"🔎 Буюртма #{id} яратилди.\n🚕 Ҳайдовчи қидирилмоқда...","no_driver":"⚠️ Ҳозирча мос ҳайдовчи топилмади.",
    "order_claim":"✅ БУЮРТМАНИ ОЛИШ","decline":"❌ РАД ЭТИШ","noanswer_btn":"📵 МИЖОЗ ЖАВОБ БЕРМАДИ",
    "finish":"🏁 БУЮРТМАНИ ЯКУНЛАШ","accepted":"✅ ҲАЙДОВЧИ ТОПИЛДИ!","done":"🏁 Буюртма якунланди.",
    "noans":"📵 Ҳайдовчи сиз билан боғлана олмади.\nСизга ҳали ҳам машина керакми?\n⏱ 1 дақиқа ичида жавоб беринг.",
    "yes":"✅ ҲА, КЕРАК","no":"❌ ЙЎҚ, КЕРАК ЭМАС","rating":"⭐ Ҳайдовчини баҳоланг:",
    "blocked":"⛔ Аккаунтингиз блокланган.","approval":"⏳ Маълумотларингиз админ тасдиғига юборилди.",
    "go_online":"🟢 ONLINE БЎЛИШ","go_offline":"🔴 OFFLINE БЎЛИШ","active":"Фаол буюртмалар: {n}/4",
    "support_prompt":"📩 Мурожаатингизни ёзинг:","saved":"✅ Мурожаат юборилди.",
    "driver_rules":"⚠️ Қабул қилинган буюртмани ўзбошимчалик билан бекор қилиш мумкин эмас. Мижоз жавоб бермаса 60 сониялик тартиб ишлайди.\n\nҚабул қиласизми?",
    "accept_rules":"✅ Қабул қиламан","reject_rules":"❌ Қабул қилмайман","driver_reg":"🚕 Ҳайдовчи рўйхатдан ўтиши",
    "car":"🚗 Машина модели:","plate":"🔢 Давлат рақами:","route_driver":"📍 Йўналиш: ОБЛИҚ ↔ АНГРЕН",
    "license":"🪪 Ҳайдовчилик гувоҳномаси расмини юборинг:","tech":"📄 Техпаспорт расмини юборинг:","car_photo":"🚗 Машинангиз расмини юборинг:",
    "photo_bad":"📸 Илтимос, расм юборинг.","driver_profile":"👤 {name}\n📱 {phone}\n🚗 {car}\n🔢 {plate}\n⭐ {rating}\n📊 {active}/4",
    "no_history":"📜 Тарих ҳозирча бўш.","admin_only":"⛔ Фақат админ."
},
"ru": {
    "lang_name":"Русский","passenger":"👤 Пассажир","delivery":"📦 Доставка","driver":"🚕 Водитель",
    "profile":"👤 Профиль","history":"📜 История","driver_orders":"📦 Заказы","income":"💰 Доход","support":"📩 Поддержка","change_lang":"🌐 Изменить язык",
    "main":"🏠 Главное меню","cancel":"❌ Отмена","name":"👤 Введите имя и фамилию:","phone":"📱 Отправьте номер телефона:",
    "phone_btn":"📱 Отправить мой номер","people":"👥 Сколько человек?","origin":"📍 ОТКУДА?\nНапример: 5/5, Облик\n\nНапишите место отправления:",
    "destination":"🏁 КУДА?\nНапример: Кальц, центр Ангрена\n\nНапишите место назначения:","gps":"📍 Можно отправить GPS точки посадки.\n\nGPS не обязателен.",
    "gps_btn":"📍 Отправить GPS","no_gps":"⏭ Продолжить без GPS","gps_bad":"📍 Отправьте GPS или нажмите «Продолжить без GPS».",
    "price":"💰 Выберите цену:","other_price":"✍️ Другая цена","custom_price":"💰 Введите цену в сумах:",
    "bad_price":"❗ Цена должна быть от 5 000 до 1 000 000 сум.","confirm":"📋 Проверьте заказ:",
    "confirm_btn":"✅ ПОДТВЕРДИТЬ ЗАКАЗ","edit_btn":"✏️ ИЗМЕНИТЬ","created":"🔎 Заказ #{id} создан.\n🚕 Ищем водителя...",
    "no_driver":"⚠️ Подходящий водитель пока не найден.","order_claim":"✅ ПРИНЯТЬ ЗАКАЗ","decline":"❌ ОТКАЗАТЬСЯ",
    "noanswer_btn":"📵 КЛИЕНТ НЕ ОТВЕЧАЕТ","finish":"🏁 ЗАВЕРШИТЬ ЗАКАЗ","accepted":"✅ ВОДИТЕЛЬ НАЙДЕН!","done":"🏁 Заказ завершён.",
    "noans":"📵 Водитель не смог связаться с вами.\nМашина вам ещё нужна?\n⏱ Ответьте за 1 минуту.","yes":"✅ ДА, НУЖНА","no":"❌ НЕТ, НЕ НУЖНА",
    "rating":"⭐ Оцените водителя:","blocked":"⛔ Ваш аккаунт заблокирован.","approval":"⏳ Данные отправлены администратору.",
    "go_online":"🟢 ВЫЙТИ ONLINE","go_offline":"🔴 ВЫЙТИ OFFLINE","active":"Активные заказы: {n}/4",
    "support_prompt":"📩 Напишите сообщение:","saved":"✅ Сообщение отправлено.",
    "driver_rules":"⚠️ После принятия заказа водитель не может отменить его произвольно. При отсутствии ответа клиента действует 60-секундная процедура.\n\nПринять?",
    "accept_rules":"✅ Принимаю","reject_rules":"❌ Не принимаю","driver_reg":"🚕 Регистрация водителя",
    "car":"🚗 Модель автомобиля:","plate":"🔢 Госномер:","route_driver":"📍 Маршрут: ОБЛИК ↔ АНГРЕН",
    "license":"🪪 Отправьте фото водительского удостоверения:","tech":"📄 Отправьте фото техпаспорта:","car_photo":"🚗 Отправьте фото автомобиля:",
    "photo_bad":"📸 Отправьте фото.","driver_profile":"👤 {name}\n📱 {phone}\n🚗 {car}\n🔢 {plate}\n⭐ {rating}\n📊 {active}/4",
    "no_history":"📜 История пока пуста.","admin_only":"⛔ Только администратор."
},
"en": {
    "lang_name":"English","passenger":"👤 Passenger","delivery":"📦 Delivery","driver":"🚕 Driver","profile":"👤 Profile",
    "history":"📜 History","driver_orders":"📦 Orders","income":"💰 Income","support":"📩 Support","change_lang":"🌐 Change language","main":"🏠 Main menu","cancel":"❌ Cancel",
    "name":"👤 Enter your full name:","phone":"📱 Send your phone number:","phone_btn":"📱 Send my phone",
    "people":"👥 How many people?","origin":"📍 FROM?\nExample: 5/5, Obliq\n\nEnter pickup area:","destination":"🏁 TO?\nExample: Kalts, Angren center\n\nEnter destination:",
    "gps":"📍 You may send the pickup GPS location.\n\nGPS is optional.","gps_btn":"📍 Send GPS","no_gps":"⏭ Continue without GPS",
    "gps_bad":"📍 Send GPS or press “Continue without GPS”.","price":"💰 Choose price:","other_price":"✍️ Other price",
    "custom_price":"💰 Enter price in UZS:","bad_price":"❗ Price must be between 5,000 and 1,000,000 UZS.",
    "confirm":"📋 Check your order:","confirm_btn":"✅ CONFIRM ORDER","edit_btn":"✏️ EDIT",
    "created":"🔎 Order #{id} created.\n🚕 Searching for a driver...","no_driver":"⚠️ No suitable driver found yet.",
    "order_claim":"✅ ACCEPT ORDER","decline":"❌ DECLINE","noanswer_btn":"📵 CUSTOMER DOES NOT ANSWER","finish":"🏁 FINISH ORDER",
    "accepted":"✅ DRIVER FOUND!","done":"🏁 Order completed.","noans":"📵 The driver could not reach you.\nDo you still need a car?\n⏱ Reply within 1 minute.",
    "yes":"✅ YES, NEEDED","no":"❌ NO, NOT NEEDED","rating":"⭐ Rate the driver:","blocked":"⛔ Your account is blocked.",
    "approval":"⏳ Your data was sent to the administrator.","go_online":"🟢 GO ONLINE","go_offline":"🔴 GO OFFLINE","active":"Active orders: {n}/4",
    "support_prompt":"📩 Write your message:","saved":"✅ Message sent.",
    "driver_rules":"⚠️ After accepting an order, the driver cannot cancel it arbitrarily. If the customer does not answer, a 60-second procedure applies.\n\nAccept?",
    "accept_rules":"✅ I accept","reject_rules":"❌ I do not accept","driver_reg":"🚕 Driver registration","car":"🚗 Car model:",
    "plate":"🔢 Plate number:","route_driver":"📍 Route: OBLIQ ↔ ANGREN","license":"🪪 Send driving license photo:",
    "tech":"📄 Send vehicle registration photo:","car_photo":"🚗 Send car photo:","photo_bad":"📸 Please send a photo.",
    "driver_profile":"👤 {name}\n📱 {phone}\n🚗 {car}\n🔢 {plate}\n⭐ {rating}\n📊 {active}/4",
    "no_history":"📜 History is empty.","admin_only":"⛔ Admin only."
}}


def tr(lang, key, **kwargs):
    text = T.get(lang, T["uz"]).get(key, T["uz"].get(key, key))
    return text.format(**kwargs)


def lang_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🇺🇿 O‘zbekcha"), KeyboardButton(text="🇺🇿 Ўзбекча")],
        [KeyboardButton(text="🇷🇺 Русский"), KeyboardButton(text="🇬🇧 English")]
    ], resize_keyboard=True)


def main_kb(lang):
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=tr(lang,"passenger")), KeyboardButton(text=tr(lang,"driver"))],
        [KeyboardButton(text=tr(lang,"profile")), KeyboardButton(text=tr(lang,"history"))],
        [KeyboardButton(text=tr(lang,"support")), KeyboardButton(text=tr(lang,"change_lang"))],
    ], resize_keyboard=True)


def cancel_kb(lang):
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=tr(lang,"cancel"))]], resize_keyboard=True)


def phone_kb(lang):
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=tr(lang,"phone_btn"), request_contact=True)],
        [KeyboardButton(text=tr(lang,"cancel"))]
    ], resize_keyboard=True)


def people_kb(lang):
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="1️⃣ 1"), KeyboardButton(text="2️⃣ 2")],
        [KeyboardButton(text="3️⃣ 3"), KeyboardButton(text="4️⃣ 4")],
        [KeyboardButton(text=tr(lang,"delivery")), KeyboardButton(text=tr(lang,"cancel"))]
    ], resize_keyboard=True)


def gps_kb(lang):
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=tr(lang,"gps_btn"), request_location=True)],
        [KeyboardButton(text=tr(lang,"no_gps"))],
        [KeyboardButton(text=tr(lang,"cancel"))]
    ], resize_keyboard=True)


def price_kb(lang):
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="5 000"), KeyboardButton(text="10 000")],
        [KeyboardButton(text="15 000"), KeyboardButton(text="20 000")],
        [KeyboardButton(text=tr(lang,"other_price")), KeyboardButton(text=tr(lang,"cancel"))]
    ], resize_keyboard=True)


def driver_panel_kb(lang, online):
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=tr(lang,"go_offline") if online else tr(lang,"go_online"))],
        [KeyboardButton(text=tr(lang,"driver_orders")), KeyboardButton(text=tr(lang,"income"))],
        [KeyboardButton(text=tr(lang,"profile")), KeyboardButton(text=tr(lang,"main"))]
    ], resize_keyboard=True)


def offer_kb(lang, oid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=tr(lang,"order_claim"), callback_data=f"claim:{oid}"),
        InlineKeyboardButton(text=tr(lang,"decline"), callback_data=f"decline:{oid}")
    ]])


def accepted_kb(lang, oid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=tr(lang,"noanswer_btn"), callback_data=f"noans:{oid}")],
        [InlineKeyboardButton(text=tr(lang,"finish"), callback_data=f"finish:{oid}")]
    ])


def customer_confirm_kb(lang, oid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=tr(lang,"confirm_btn"), callback_data=f"confirm:{oid}")],
        [InlineKeyboardButton(text=tr(lang,"edit_btn"), callback_data=f"edit:{oid}")],
        [InlineKeyboardButton(text=tr(lang,"cancel"), callback_data=f"cancelorder:{oid}")]
    ])


def yesno_kb(lang, oid):
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text=tr(lang,"yes"), callback_data=f"needyes:{oid}"),
        InlineKeyboardButton(text=tr(lang,"no"), callback_data=f"needno:{oid}")
    ]])


# ============================================================
# FSM
# ============================================================
class Registration(StatesGroup):
    language = State()
    name = State()
    phone = State()


class OrderFlow(StatesGroup):
    people = State()
    origin = State()
    destination = State()
    gps = State()
    price = State()
    custom_price = State()
    confirm = State()


class DriverReg(StatesGroup):
    name = State()
    phone = State()
    car = State()
    plate = State()
    route = State()
    license = State()
    tech = State()
    car_photo = State()
    rules = State()


class Support(StatesGroup):
    text = State()


# ============================================================
# GENERAL
# ============================================================
async def ensure_user(message):
    u = await get_user(message.from_user.id)
    if not u:
        await message.answer("Avval /start ni bosing.")
        return None
    if u["blocked"]:
        await message.answer(tr(u["lang"],"blocked"))
        return None
    return u


@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    u = await get_user(message.from_user.id)
    await state.clear()
    if u:
        if u["blocked"]:
            return await message.answer(tr(u["lang"],"blocked"))
        await message.answer(
            "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n📍 OBLIQ ↔ ANGREN",
            reply_markup=main_kb(u["lang"])
        )
        return
    await state.set_state(Registration.language)
    await message.answer(
        "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
        "Tilni tanlang / Выберите язык / Choose language:",
        reply_markup=lang_kb()
    )


@dp.message(Registration.language)
async def registration_language(message: Message, state: FSMContext):
    mp = {"🇺🇿 O‘zbekcha":"uz","🇺🇿 Ўзбекча":"uzc","🇷🇺 Русский":"ru","🇬🇧 English":"en"}
    lang = mp.get(message.text)
    if not lang:
        return await message.answer("Tilni tugmadan tanlang.", reply_markup=lang_kb())
    u = await get_user(message.from_user.id)
    if u:
        await q("UPDATE users SET lang=?,updated_at=? WHERE tg_id=?", (lang,now(),message.from_user.id))
        await state.clear()
        return await message.answer("🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n📍 OBLIQ ↔ ANGREN", reply_markup=main_kb(lang))
    await state.update_data(lang=lang)
    await state.set_state(Registration.name)
    await message.answer(tr(lang,"name"), reply_markup=cancel_kb(lang))


@dp.message(Registration.name)
async def registration_name(message: Message, state: FSMContext):
    data = await state.get_data()
    name = (message.text or "").strip()
    if len(name) < 2:
        return await message.answer(tr(data["lang"],"name"))
    await state.update_data(name=name)
    await state.set_state(Registration.phone)
    await message.answer(tr(data["lang"],"phone"), reply_markup=phone_kb(data["lang"]))


@dp.message(Registration.phone)
async def registration_phone(message: Message, state: FSMContext):
    data = await state.get_data()
    phone = message.contact.phone_number if message.contact else (message.text or "").strip()
    if len(re.sub(r"\D","",phone)) < 7:
        return await message.answer(tr(data["lang"],"phone"), reply_markup=phone_kb(data["lang"]))
    stamp = now()
    await q(
        """INSERT INTO users(tg_id,name,phone,lang,created_at,updated_at)
           VALUES(?,?,?,?,?,?)
           ON CONFLICT(tg_id) DO UPDATE SET
             name=excluded.name,phone=excluded.phone,lang=excluded.lang,updated_at=excluded.updated_at""",
        (message.from_user.id,data["name"],phone,data["lang"],stamp,stamp)
    )
    await state.clear()
    await message.answer("🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n📍 OBLIQ ↔ ANGREN", reply_markup=main_kb(data["lang"]))


@dp.message(F.text.in_({"❌ Bekor qilish","❌ Бекор қилиш","❌ Отмена","❌ Cancel"}))
async def cancel_text(message: Message, state: FSMContext):
    u = await get_user(message.from_user.id)
    await state.clear()
    if u:
        await message.answer(tr(u["lang"],"main"), reply_markup=main_kb(u["lang"]))


# ============================================================
# CUSTOMER MENU — ONE TEXT ROUTER ONLY
# ============================================================
@dp.message(StateFilter(None), F.text)
async def main_router(message: Message, state: FSMContext):
    u = await ensure_user(message)
    if not u:
        return
    lang = u["lang"]
    text = message.text

    # Driver online/offline controls are handled here so there is only ONE
    # broad text router. This prevents duplicate replies.
    d = await get_driver(message.from_user.id)
    if d and d["approved"] and not d["blocked"]:
        if text == tr(lang, "go_online"):
            if d["active_orders"] >= MAX_ACTIVE:
                return await message.answer(tr(lang, "active", n=d["active_orders"]))
            await q("UPDATE drivers SET online=1,updated_at=? WHERE tg_id=?", (now(), message.from_user.id))
            return await message.answer(tr(lang, "go_offline"), reply_markup=driver_panel_kb(lang, True))
        if text == tr(lang, "go_offline"):
            await q("UPDATE drivers SET online=0,updated_at=? WHERE tg_id=?", (now(), message.from_user.id))
            return await message.answer(tr(lang, "go_online"), reply_markup=driver_panel_kb(lang, False))
        if text == tr(lang, "driver_orders"):
            rows = await q("SELECT id,status,price,origin,destination,created_at FROM orders WHERE driver_id=? ORDER BY id DESC LIMIT 20", (message.from_user.id,), all_rows=True)
            if not rows:
                return await message.answer(tr(lang,"no_history"), reply_markup=driver_panel_kb(lang,bool(d["online"])))
            lines=[tr(lang,"driver_orders"),""]
            for r in rows:
                lines.append(f"#{r['id']} • {r['status']} • {r['origin']} → {r['destination']} • {money(r['price'])}")
            return await message.answer("\n".join(lines), reply_markup=driver_panel_kb(lang,bool(d["online"])))
        if text == tr(lang, "income"):
            r = await q("SELECT COALESCE(SUM(price),0) total, COUNT(*) n FROM orders WHERE driver_id=? AND status='COMPLETED'", (message.from_user.id,), one=True)
            return await message.answer(f"💰 {tr(lang,'income')}: {money(r['total'])}\n📦 {r['n']}", reply_markup=driver_panel_kb(lang,bool(d["online"])))

    if text == tr(lang,"passenger"):
        await state.set_state(OrderFlow.people)
        return await message.answer(tr(lang,"people"), reply_markup=people_kb(lang))

    if text == tr(lang,"driver"):
        return await open_driver(message, state, u)

    if text == tr(lang,"profile"):
        d = await get_driver(message.from_user.id)
        if d:
            rating = d["rating_sum"]/d["rating_count"] if d["rating_count"] else 0
            txt = tr(lang,"driver_profile",name=d["name"],phone=d["phone"],car=d["car_model"],plate=d["plate"],rating=f"{rating:.1f}",active=d["active_orders"])
        else:
            txt = f"👤 {u['name']}\n📱 {u['phone']}"
        return await message.answer(txt, reply_markup=main_kb(lang))

    if text == tr(lang,"history"):
        rows = await q("SELECT id,status,price,service,created_at FROM orders WHERE customer_id=? ORDER BY id DESC LIMIT 20",(message.from_user.id,),all_rows=True)
        if not rows:
            return await message.answer(tr(lang,"no_history"))
        return await message.answer("\n".join([tr(lang,"history"),""] + [
            f"#{r['id']} • {r['status']} • {money(r['price'])}" for r in rows
        ]))

    if text == tr(lang,"support"):
        await state.set_state(Support.text)
        return await message.answer(tr(lang,"support_prompt"), reply_markup=cancel_kb(lang))

    if text == tr(lang,"change_lang"):
        await state.set_state(Registration.language)
        return await message.answer("Tilni tanlang:", reply_markup=lang_kb())


# ============================================================
# CUSTOMER ORDER
# ============================================================
@dp.message(OrderFlow.people)
async def order_people(message: Message, state: FSMContext):
    u = await get_user(message.from_user.id)
    if not u: return
    lang = u["lang"]
    if message.text == tr(lang,"delivery"):
        service, passengers = "delivery", 0
    else:
        passengers = {"1️⃣ 1":1,"2️⃣ 2":2,"3️⃣ 3":3,"4️⃣ 4":4}.get(message.text)
        if not passengers:
            return await message.answer(tr(lang,"people"), reply_markup=people_kb(lang))
        service = "passenger"
    await state.update_data(service=service,passengers=passengers)
    await state.set_state(OrderFlow.origin)
    await message.answer(tr(lang,"origin"), reply_markup=cancel_kb(lang))


@dp.message(OrderFlow.origin)
async def order_origin(message: Message, state: FSMContext):
    u = await get_user(message.from_user.id)
    if not u: return
    value = (message.text or "").strip()
    if not value:
        return await message.answer(tr(u["lang"],"origin"))
    await state.update_data(origin=value)
    await state.set_state(OrderFlow.destination)
    await message.answer(tr(u["lang"],"destination"), reply_markup=cancel_kb(u["lang"]))


@dp.message(OrderFlow.destination)
async def order_destination(message: Message, state: FSMContext):
    u = await get_user(message.from_user.id)
    if not u: return
    value = (message.text or "").strip()
    if not value:
        return await message.answer(tr(u["lang"],"destination"))
    await state.update_data(destination=value)
    await state.set_state(OrderFlow.gps)
    await message.answer(tr(u["lang"],"gps"), reply_markup=gps_kb(u["lang"]))


@dp.message(OrderFlow.gps)
async def order_gps(message: Message, state: FSMContext):
    u = await get_user(message.from_user.id)
    if not u: return
    if message.location:
        await state.update_data(lat=message.location.latitude,lon=message.location.longitude)
    elif message.text == tr(u["lang"],"no_gps"):
        await state.update_data(lat=None,lon=None)
    else:
        return await message.answer(tr(u["lang"],"gps_bad"), reply_markup=gps_kb(u["lang"]))
    await state.set_state(OrderFlow.price)
    await message.answer(tr(u["lang"],"price"), reply_markup=price_kb(u["lang"]))


@dp.message(OrderFlow.price)
async def order_price(message: Message, state: FSMContext):
    u = await get_user(message.from_user.id)
    if not u: return
    prices = {"5 000":5000,"10 000":10000,"15 000":15000,"20 000":20000}
    if message.text == tr(u["lang"],"other_price"):
        await state.set_state(OrderFlow.custom_price)
        return await message.answer(tr(u["lang"],"custom_price"), reply_markup=cancel_kb(u["lang"]))
    price = prices.get(message.text)
    if price is None:
        return await message.answer(tr(u["lang"],"price"), reply_markup=price_kb(u["lang"]))
    await state.update_data(price=price)
    await show_order_confirm(message,state)


@dp.message(OrderFlow.custom_price)
async def order_custom_price(message: Message, state: FSMContext):
    u = await get_user(message.from_user.id)
    if not u: return
    digits = "".join(ch for ch in (message.text or "") if ch.isdigit())
    price = int(digits) if digits else 0
    if not MIN_PRICE <= price <= MAX_PRICE:
        return await message.answer(tr(u["lang"],"bad_price"))
    await state.update_data(price=price)
    await show_order_confirm(message,state)


async def show_order_confirm(message: Message, state: FSMContext):
    u = await get_user(message.from_user.id)
    d = await state.get_data()
    service = tr(u["lang"],"delivery") if d["service"] == "delivery" else tr(u["lang"],"passenger") + f"\n👥 {d['passengers']} kishi"
    gps = "✅" if d.get("lat") is not None else "❌"
    text = (
        f"{tr(u['lang'],'confirm')}\n\n"
        f"🚕 {service}\n\n"
        f"📍 <b>QAYERDAN:</b> {d['origin']}\n"
        f"🏁 <b>QAYERGA:</b> {d['destination']}\n"
        f"📍 GPS: {gps}\n"
        f"💰 <b>{money(d['price'])}</b>"
    )
    await state.set_state(OrderFlow.confirm)
    await message.answer(text, reply_markup=customer_confirm_kb(u["lang"],0))


@dp.callback_query(F.data.startswith("confirm:"))
async def confirm_order(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != callback.message.chat.id:
        pass
    u = await get_user(callback.from_user.id)
    if not u: return await callback.answer("User not found",show_alert=True)
    if not await state.get_state() == OrderFlow.confirm.state:
        return await callback.answer("Bu buyurtma oynasi eskirgan.",show_alert=True)
    d = await state.get_data()
    active = await q("""SELECT id FROM orders WHERE customer_id=? AND status IN ('SEARCHING','ACCEPTED','NO_ANSWER_WAIT')""",(callback.from_user.id,),one=True)
    if active:
        await state.clear()
        return await callback.answer("Sizda faol buyurtma bor.",show_alert=True)
    stamp = now()
    oid = await q(
        """INSERT INTO orders(customer_id,service,passengers,origin,destination,lat,lon,price,status,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (callback.from_user.id,d["service"],d["passengers"],d["origin"],d["destination"],d.get("lat"),d.get("lon"),d["price"],"SEARCHING",stamp,stamp)
    )
    await event(oid,callback.from_user.id,"CREATED",f"{d['origin']} -> {d['destination']}")
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(tr(u["lang"],"created",id=oid),reply_markup=main_kb(u["lang"]))
    asyncio.create_task(dispatch_order(oid))
    asyncio.create_task(search_timeout(oid))
    await callback.answer("OK")


@dp.callback_query(F.data.startswith("edit:"))
async def edit_order(callback: CallbackQuery, state: FSMContext):
    u = await get_user(callback.from_user.id)
    if not u: return
    await state.set_state(OrderFlow.origin)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(tr(u["lang"],"origin"),reply_markup=cancel_kb(u["lang"]))
    await callback.answer()


@dp.callback_query(F.data.startswith("cancelorder:"))
async def cancel_draft(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_reply_markup(reply_markup=None)
    u = await get_user(callback.from_user.id)
    if u: await callback.message.answer(tr(u["lang"],"main"),reply_markup=main_kb(u["lang"]))
    await callback.answer()


# ============================================================
# DISPATCH
# ============================================================
def offer_text(lang, o):
    service = tr(lang,"delivery") if o["service"]=="delivery" else f"{tr(lang,'passenger')} • {o['passengers']}"
    gps = "✅" if o["lat"] is not None else "❌"
    return (
        f"🚕 <b>BUYURTMA #{o['id']}</b>\n\n"
        f"{service}\n"
        f"📍 <b>QAYERDAN:</b> {o['origin']}\n"
        f"🏁 <b>QAYERGA:</b> {o['destination']}\n"
        f"📍 GPS: {gps}\n"
        f"💰 {money(o['price'])}\n\n"
        f"[{tr(lang,'order_claim')}]"
    )


async def dispatch_order(order_id):
    o = await q("SELECT * FROM orders WHERE id=?",(order_id,),one=True)
    if not o or o["status"]!="SEARCHING": return
    drivers = await q(
        """SELECT * FROM drivers
           WHERE approved=1 AND blocked=0 AND online=1
             AND route=? AND active_orders < ?
           ORDER BY active_orders ASC, tg_id ASC""",
        (ROUTE,MAX_ACTIVE),all_rows=True
    )
    sent = 0
    for d in drivers:
        if o["excluded_driver"] and d["tg_id"] == o["excluded_driver"]:
            continue
        existing = await q("SELECT status FROM offers WHERE order_id=? AND driver_id=?",(order_id,d["tg_id"]),one=True)
        # On a reopened order, allow a previously DECLINED/EXPIRED/CLOSED driver to be offered again.
        if existing and existing["status"] in ("SENT","PENDING","ACCEPTED"):
            continue
        if existing:
            await q("UPDATE offers SET status='PENDING',message_id=NULL,chat_id=?,updated_at=? WHERE order_id=? AND driver_id=?",(d["tg_id"],now(),order_id,d["tg_id"]))
        else:
            await q("INSERT OR IGNORE INTO offers(order_id,driver_id,status,chat_id,created_at,updated_at) VALUES(?,?,?,?,?,?)",(order_id,d["tg_id"],"PENDING",d["tg_id"],now(),now()))
        du = await get_user(d["tg_id"])
        lang = du["lang"] if du else "uz"
        try:
            msg = await bot.send_message(d["tg_id"],offer_text(lang,o),reply_markup=offer_kb(lang,order_id))
            if o["lat"] is not None:
                await bot.send_location(d["tg_id"],o["lat"],o["lon"])
            await q("UPDATE offers SET status='SENT',message_id=?,updated_at=? WHERE order_id=? AND driver_id=? AND status='PENDING'",(msg.message_id,now(),order_id,d["tg_id"]))
            sent += 1
        except Exception as exc:
            log.warning("Offer failed %s: %s",d["tg_id"],exc)
            await q("DELETE FROM offers WHERE order_id=? AND driver_id=? AND status='PENDING'",(order_id,d["tg_id"]))
    if sent == 0:
        await mark_no_driver(order_id)


async def mark_no_driver(order_id):
    changed = await q("UPDATE orders SET status='NO_DRIVER',updated_at=? WHERE id=? AND status='SEARCHING'",(now(),order_id))
    if changed:
        o = await q("SELECT * FROM orders WHERE id=?",(order_id,),one=True)
        if o:
            u = await get_user(o["customer_id"])
            if u: await bot.send_message(o["customer_id"],tr(u["lang"],"no_driver"),reply_markup=main_kb(u["lang"]))
            await q("UPDATE offers SET status='EXPIRED',updated_at=? WHERE order_id=? AND status IN ('SENT','PENDING')",(now(),order_id))


async def search_timeout(order_id):
    while True:
        o = await q("SELECT status,updated_at FROM orders WHERE id=?", (order_id,), one=True)
        if not o or o["status"] != "SEARCHING":
            return
        try:
            started = datetime.fromisoformat(o["updated_at"]).timestamp()
        except Exception:
            started = time.time()
        remaining = SEARCH_TIMEOUT - max(0, time.time() - started)
        if remaining > 0:
            await asyncio.sleep(remaining)
            continue
        o = await q("SELECT status,updated_at FROM orders WHERE id=?", (order_id,), one=True)
        if not o or o["status"] != "SEARCHING":
            return
        await mark_no_driver(order_id)
        await close_offer_buttons(order_id)
        return


async def close_offer_buttons(order_id, except_driver=None):
    offers = await q("SELECT * FROM offers WHERE order_id=?",(order_id,),all_rows=True)
    for off in offers:
        if except_driver and off["driver_id"] == except_driver:
            continue
        if off["message_id"]:
            try:
                await bot.edit_message_reply_markup(off["chat_id"],off["message_id"],reply_markup=None)
            except Exception:
                pass


# ============================================================
# DRIVER CLAIM / DECLINE
# ============================================================
@dp.callback_query(F.data.startswith("decline:"))
async def decline(callback: CallbackQuery):
    oid = int(callback.data.split(":")[1])
    uid = callback.from_user.id
    changed = await q(
        "UPDATE offers SET status='DECLINED',updated_at=? WHERE order_id=? AND driver_id=? AND status='SENT'",
        (now(),oid,uid)
    )
    await callback.answer("❌ Rad etildi" if changed else "Buyurtma endi mavjud emas.")
    o = await q("SELECT * FROM orders WHERE id=?",(oid,),one=True)
    if o and o["status"]=="SEARCHING":
        remaining = await q("SELECT id FROM offers WHERE order_id=? AND status='SENT'",(oid,),one=True)
        if not remaining:
            # If there are still eligible drivers not offered, dispatch again.
            await dispatch_order(oid)


@dp.callback_query(F.data.startswith("claim:"))
async def claim(callback: CallbackQuery):
    oid = int(callback.data.split(":")[1])
    uid = callback.from_user.id

    def tx_claim(c):
        o = c.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
        d = c.execute("SELECT * FROM drivers WHERE tg_id=?",(uid,)).fetchone()
        off = c.execute("SELECT * FROM offers WHERE order_id=? AND driver_id=?",(oid,uid)).fetchone()
        if not o or not d or not off:
            return None
        if o["status"]!="SEARCHING" or off["status"]!="SENT":
            return None
        if not d["approved"] or d["blocked"] or not d["online"] or d["active_orders"]>=MAX_ACTIVE:
            return None
        c.execute("UPDATE orders SET status='ACCEPTED',driver_id=?,updated_at=? WHERE id=? AND status='SEARCHING'",(uid,now(),oid))
        c.execute("UPDATE drivers SET active_orders=active_orders+1,updated_at=? WHERE tg_id=?",(now(),uid))
        c.execute("UPDATE offers SET status=CASE WHEN driver_id=? THEN 'ACCEPTED' ELSE 'CLOSED' END,updated_at=? WHERE order_id=? AND status='SENT'",(uid,now(),oid))
        return dict(o),dict(d)

    result = await transaction(tx_claim)
    if not result:
        return await callback.answer("❌ Buyurtma allaqachon olingan yoki sizga mos emas.",show_alert=True)

    o,d = result
    offers = await q("SELECT * FROM offers WHERE order_id=?",(oid,),all_rows=True)
    du = await get_user(uid)
    for off in offers:
        try:
            if off["driver_id"] == uid:
                await bot.edit_message_reply_markup(off["chat_id"],off["message_id"],reply_markup=accepted_kb(du["lang"],oid))
            else:
                await bot.edit_message_reply_markup(off["chat_id"],off["message_id"],reply_markup=None)
        except Exception:
            pass

    cu = await get_user(o["customer_id"])
    clang = cu["lang"]
    # Customer gets NO car photo. Only model + plate + phone.
    await bot.send_message(
        o["customer_id"],
        f"{tr(clang,'accepted')}\n\n"
        f"🚗 {d['car_model']}\n"
        f"🔢 {d['plate']}\n"
        f"📞 {d['phone']}\n\n"
        f"📍 {o['origin']} → {o['destination']}"
    )
    if o["lat"] is not None:
        await bot.send_location(uid,o["lat"],o["lon"])

    await bot.send_message(
        uid,
        f"📦 <b>BUYURTMA #{oid}</b>\n"
        f"👤 {cu['name']}\n📞 {cu['phone']}\n"
        f"📍 {o['origin']}\n🏁 {o['destination']}\n💰 {money(o['price'])}",
        reply_markup=accepted_kb(du["lang"],oid)
    )
    await callback.answer("✅ Buyurtma qabul qilindi.")


# ============================================================
# NO ANSWER / FINISH / RATING
# ============================================================
@dp.callback_query(F.data.startswith("noans:"))
async def no_answer(callback: CallbackQuery):
    oid = int(callback.data.split(":")[1]); uid = callback.from_user.id
    def tx_noans(c):
        o = c.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
        if not o or o["status"]!="ACCEPTED" or o["driver_id"]!=uid:
            return None
        c.execute("UPDATE orders SET status='NO_ANSWER_WAIT',driver_id=NULL,excluded_driver=?,no_answer_started=?,updated_at=? WHERE id=? AND status='ACCEPTED'",(uid,time.time(),now(),oid))
        c.execute("UPDATE drivers SET active_orders=CASE WHEN active_orders>0 THEN active_orders-1 ELSE 0 END,updated_at=? WHERE tg_id=?",(now(),uid))
        return dict(o)
    o = await transaction(tx_noans)
    if not o:
        return await callback.answer("Bu amal mumkin emas.",show_alert=True)
    await callback.message.edit_reply_markup(reply_markup=None)
    u = await get_user(o["customer_id"])
    await bot.send_message(o["customer_id"],tr(u["lang"],"noans"),reply_markup=yesno_kb(u["lang"],oid))
    await callback.answer("⏱ 60 soniyalik javob vaqti boshlandi.")
    asyncio.create_task(no_answer_timeout(oid))


async def no_answer_timeout(oid):
    await asyncio.sleep(NO_ANSWER_TIMEOUT)
    o = await q("SELECT * FROM orders WHERE id=?",(oid,),one=True)
    if not o or o["status"]!="NO_ANSWER_WAIT":
        return
    await q("UPDATE orders SET status='CANCELLED',updated_at=? WHERE id=? AND status='NO_ANSWER_WAIT'",(now(),oid))
    u = await get_user(o["customer_id"])
    if u:
        await bot.send_message(o["customer_id"],"❌ Buyurtma 1 daqiqada javob bo‘lmagani uchun bekor qilindi.",reply_markup=main_kb(u["lang"]))


@dp.callback_query(F.data.startswith("needyes:"))
async def need_yes(callback: CallbackQuery):
    oid = int(callback.data.split(":")[1])
    o = await q("SELECT * FROM orders WHERE id=?",(oid,),one=True)
    if not o or o["status"]!="NO_ANSWER_WAIT":
        return await callback.answer("Vaqt tugagan.",show_alert=True)
    await q("UPDATE orders SET status='SEARCHING',updated_at=? WHERE id=? AND status='NO_ANSWER_WAIT'",(now(),oid))
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("🔎 Qayta haydovchi qidirilmoqda.")
    asyncio.create_task(dispatch_order(oid))
    asyncio.create_task(search_timeout(oid))


@dp.callback_query(F.data.startswith("needno:"))
async def need_no(callback: CallbackQuery):
    oid = int(callback.data.split(":")[1])
    o = await q("SELECT * FROM orders WHERE id=?",(oid,),one=True)
    if not o or o["status"]!="NO_ANSWER_WAIT":
        return await callback.answer("Vaqt tugagan.",show_alert=True)
    await q("UPDATE orders SET status='CANCELLED',updated_at=? WHERE id=? AND status='NO_ANSWER_WAIT'",(now(),oid))
    await callback.message.edit_reply_markup(reply_markup=None)
    u = await get_user(o["customer_id"])
    if u: await callback.message.answer(tr(u["lang"],"main"),reply_markup=main_kb(u["lang"]))
    await callback.answer("Bekor qilindi.")


@dp.callback_query(F.data.startswith("finish:"))
async def finish(callback: CallbackQuery):
    oid = int(callback.data.split(":")[1]); uid = callback.from_user.id
    def tx_finish(c):
        o = c.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
        if not o or o["status"]!="ACCEPTED" or o["driver_id"]!=uid:
            return None
        c.execute("UPDATE orders SET status='COMPLETED',updated_at=? WHERE id=? AND status='ACCEPTED'",(now(),oid))
        c.execute("UPDATE drivers SET active_orders=CASE WHEN active_orders>0 THEN active_orders-1 ELSE 0 END,updated_at=? WHERE tg_id=?",(now(),uid))
        return dict(o)
    o = await transaction(tx_finish)
    if not o:
        return await callback.answer("Bu buyurtmani yakunlay olmaysiz.",show_alert=True)
    u = await get_user(o["customer_id"])
    await bot.send_message(o["customer_id"],tr(u["lang"],"done"),reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="⭐1",callback_data=f"rate:{oid}:1"),
        InlineKeyboardButton(text="⭐2",callback_data=f"rate:{oid}:2"),
        InlineKeyboardButton(text="⭐3",callback_data=f"rate:{oid}:3"),
        InlineKeyboardButton(text="⭐4",callback_data=f"rate:{oid}:4"),
        InlineKeyboardButton(text="⭐5",callback_data=f"rate:{oid}:5"),
    ]]))
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("✅ Yakunlandi.")


@dp.callback_query(F.data.startswith("rate:"))
async def rate(callback: CallbackQuery):
    _,oid_s,r_s = callback.data.split(":")
    oid,r = int(oid_s),int(r_s)
    o = await q("SELECT * FROM orders WHERE id=?",(oid,),one=True)
    if not o or o["customer_id"]!=callback.from_user.id or o["status"]!="COMPLETED":
        return await callback.answer("Mumkin emas.",show_alert=True)
    try:
        await q("INSERT INTO ratings(order_id,rating,created_at) VALUES(?,?,?)",(oid,r,now()))
    except sqlite3.IntegrityError:
        return await callback.answer("Allaqachon baholangan.")
    await q("UPDATE drivers SET rating_sum=rating_sum+?,rating_count=rating_count+1 WHERE tg_id=?",(r,o["driver_id"]))
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("⭐ Rahmat!")


# ============================================================
# DRIVER REGISTRATION / PANEL
# ============================================================
async def open_driver(message,state,u):
    d = await get_driver(message.from_user.id)
    if d and d["approved"]:
        await state.clear()
        return await message.answer(
            f"{tr(u['lang'],'go_online') if not d['online'] else tr(u['lang'],'go_offline')}\n"
            f"{tr(u['lang'],'active',n=d['active_orders'])}",
            reply_markup=driver_panel_kb(u["lang"],bool(d["online"]))
        )
    if d and not d["approved"]:
        await state.clear()
        return await message.answer("⏳ Haydovchi arizangiz hali admin tomonidan ko‘rib chiqilmoqda.", reply_markup=main_kb(u["lang"]))
    # If a rejected application was deleted, registration starts cleanly.
    await state.clear()
    await state.set_state(DriverReg.name)
    await message.answer(tr(u["lang"],"driver_reg")+"\n\n"+tr(u["lang"],"name"),reply_markup=cancel_kb(u["lang"]))


@dp.message(DriverReg.name)
async def dr_name(message,state):
    await state.update_data(name=(message.text or "").strip())
    u=await get_user(message.from_user.id)
    await state.set_state(DriverReg.phone)
    await message.answer(tr(u["lang"],"phone"),reply_markup=phone_kb(u["lang"]))


@dp.message(DriverReg.phone)
async def dr_phone(message,state):
    u=await get_user(message.from_user.id)
    phone=message.contact.phone_number if message.contact else (message.text or "").strip()
    if len(re.sub(r"\D","",phone))<7:return await message.answer(tr(u["lang"],"phone"),reply_markup=phone_kb(u["lang"]))
    await state.update_data(phone=phone)
    await state.set_state(DriverReg.car)
    await message.answer(tr(u["lang"],"car"))


@dp.message(DriverReg.car)
async def dr_car(message,state):
    await state.update_data(car=(message.text or "").strip())
    u=await get_user(message.from_user.id)
    await state.set_state(DriverReg.plate)
    await message.answer(tr(u["lang"],"plate"))


@dp.message(DriverReg.plate)
async def dr_plate(message,state):
    await state.update_data(plate=(message.text or "").strip())
    u=await get_user(message.from_user.id)
    await state.set_state(DriverReg.route)
    await message.answer(tr(u["lang"],"route_driver"),reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=ROUTE)],[KeyboardButton(text=tr(u["lang"],"cancel"))]],resize_keyboard=True))


@dp.message(DriverReg.route)
async def dr_route(message,state):
    u=await get_user(message.from_user.id)
    if message.text!=ROUTE:return await message.answer(tr(u["lang"],"route_driver"))
    await state.update_data(route=ROUTE)
    await state.set_state(DriverReg.license)
    await message.answer(tr(u["lang"],"license"),reply_markup=cancel_kb(u["lang"]))


@dp.message(DriverReg.license)
async def dr_license(message,state):
    u=await get_user(message.from_user.id)
    if not message.photo:return await message.answer(tr(u["lang"],"photo_bad"))
    await state.update_data(license_file=message.photo[-1].file_id)
    await state.set_state(DriverReg.tech)
    await message.answer(tr(u["lang"],"tech"))


@dp.message(DriverReg.tech)
async def dr_tech(message,state):
    u=await get_user(message.from_user.id)
    if not message.photo:return await message.answer(tr(u["lang"],"photo_bad"))
    await state.update_data(tech_file=message.photo[-1].file_id)
    await state.set_state(DriverReg.car_photo)
    await message.answer(tr(u["lang"],"car_photo"))


@dp.message(DriverReg.car_photo)
async def dr_car_photo(message,state):
    u=await get_user(message.from_user.id)
    if not message.photo:return await message.answer(tr(u["lang"],"photo_bad"))
    await state.update_data(car_file=message.photo[-1].file_id)
    await state.set_state(DriverReg.rules)
    await message.answer(tr(u["lang"],"driver_rules"),reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=tr(u["lang"],"accept_rules")),KeyboardButton(text=tr(u["lang"],"reject_rules"))]],resize_keyboard=True))


@dp.message(DriverReg.rules)
async def dr_rules(message,state):
    u=await get_user(message.from_user.id)
    if message.text!=tr(u["lang"],"accept_rules"):
        await state.clear()
        return await message.answer(tr(u["lang"],"main"),reply_markup=main_kb(u["lang"]))
    d=await state.get_data(); stamp=now()
    await q(
        """INSERT INTO drivers(tg_id,name,phone,car_model,plate,route,license_file,tech_file,car_file,approved,online,blocked,active_orders,rating_sum,rating_count,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?, ?,?,0,0,0,0,0,0,?,?)
           ON CONFLICT(tg_id) DO UPDATE SET
             name=excluded.name,phone=excluded.phone,car_model=excluded.car_model,plate=excluded.plate,route=excluded.route,
             license_file=excluded.license_file,tech_file=excluded.tech_file,car_file=excluded.car_file,
             approved=0,online=0,blocked=0,active_orders=0,updated_at=excluded.updated_at""",
        (message.from_user.id,d["name"],d["phone"],d["car"],d["plate"],ROUTE,d["license_file"],d["tech_file"],d["car_file"],stamp,stamp)
    )
    await state.clear()
    await message.answer(tr(u["lang"],"approval"),reply_markup=main_kb(u["lang"]))
    await notify_admin_driver(message.from_user.id,d)


async def notify_admin_driver(uid,data):
    if not ADMIN_ID:return
    try:
        await bot.send_message(ADMIN_ID,
            f"🚕 <b>YANGI HAYDOVCHI</b>\n\n"
            f"ID: <code>{uid}</code>\n👤 {data['name']}\n📞 {data['phone']}\n"
            f"🚗 {data['car']}\n🔢 {data['plate']}\n📍 {ROUTE}\n\n"
            f"/admin_approve_{uid}\n/admin_reject_{uid}")
        await bot.send_photo(ADMIN_ID,data["license_file"],caption="🪪 Prava")
        await bot.send_photo(ADMIN_ID,data["tech_file"],caption="📄 Texpasport")
        await bot.send_photo(ADMIN_ID,data["car_file"],caption="🚗 Mashina rasmi")
    except Exception as exc:
        log.exception("Admin driver notification failed: %s",exc)


# ============================================================
# SUPPORT
# ============================================================
@dp.message(Support.text)
async def support_text(message,state):
    u=await get_user(message.from_user.id)
    if not u:return
    text=(message.text or "").strip()
    if not text:return await message.answer(tr(u["lang"],"support_prompt"))
    await q("INSERT INTO support_tickets(user_id,text,status,created_at) VALUES(?,?,?,?)",(message.from_user.id,text,"OPEN",now()))
    await state.clear()
    await message.answer(tr(u["lang"],"saved"),reply_markup=main_kb(u["lang"]))
    if ADMIN_ID:
        await bot.send_message(ADMIN_ID,f"📩 SUPPORT\nUser: <code>{message.from_user.id}</code>\n\n{text}")


# ============================================================
# ADMIN PANEL — FULL CONTROL CENTER
# ============================================================
def admin_only(message):
    return ADMIN_ID != 0 and message.from_user.id == ADMIN_ID

def admin_cb_kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_home_kb():
    return admin_cb_kb([
        [InlineKeyboardButton(text="📊 Statistika", callback_data="adm:stats"), InlineKeyboardButton(text="⏳ Tasdiqlash", callback_data="adm:pending")],
        [InlineKeyboardButton(text="🚕 Haydovchilar", callback_data="adm:drivers:0"), InlineKeyboardButton(text="👥 Mijozlar", callback_data="adm:users:0")],
        [InlineKeyboardButton(text="📦 Buyurtmalar", callback_data="adm:orders:0"), InlineKeyboardButton(text="🟢 Online", callback_data="adm:online")],
        [InlineKeyboardButton(text="🚫 Bloklangan", callback_data="adm:blocked"), InlineKeyboardButton(text="📩 Murojaatlar", callback_data="adm:support")],
        [InlineKeyboardButton(text="🔄 Yangilash", callback_data="adm:home")]
    ])

def admin_back_kb():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Admin panel", callback_data="adm:home")]])

def admin_driver_detail_kb(uid, approved, blocked):
    rows=[]
    if not approved:
        rows.append([InlineKeyboardButton(text="✅ TASDIQLASH", callback_data=f"adm:approve:{uid}"), InlineKeyboardButton(text="❌ RAD ETISH", callback_data=f"adm:reject:{uid}")])
    rows.append([InlineKeyboardButton(text="🪪 Prava", callback_data=f"adm:doc:{uid}:license"), InlineKeyboardButton(text="📄 Texpasport", callback_data=f"adm:doc:{uid}:tech")])
    rows.append([InlineKeyboardButton(text="🚗 Mashina rasmi", callback_data=f"adm:doc:{uid}:car")])
    rows.append([InlineKeyboardButton(text="📦 Buyurtmalari", callback_data=f"adm:driverorders:{uid}:0"), InlineKeyboardButton(text="📊 Daromadi", callback_data=f"adm:driverincome:{uid}")])
    rows.append([InlineKeyboardButton(text="🚫 Bloklash" if not blocked else "✅ Blokdan chiqarish", callback_data=f"adm:toggleblock:{uid}:driver")])
    rows.append([InlineKeyboardButton(text="⬅️ Haydovchilar", callback_data="adm:drivers:0")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_user_detail_kb(uid, blocked):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦 Buyurtmalari", callback_data=f"adm:userorders:{uid}:0")],
        [InlineKeyboardButton(text="🚫 Bloklash" if not blocked else "✅ Blokdan chiqarish", callback_data=f"adm:toggleblock:{uid}:user")],
        [InlineKeyboardButton(text="⬅️ Mijozlar", callback_data="adm:users:0")]
    ])

def admin_order_detail_kb(oid):
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Buyurtmalar", callback_data="adm:orders:0")], [InlineKeyboardButton(text="🔄 Yangilash", callback_data=f"adm:order:{oid}")]])

async def admin_stats_data():
    out={}
    for k,sql in [
        ("users","SELECT COUNT(*) n FROM users"),
        ("drivers","SELECT COUNT(*) n FROM drivers"),
        ("online","SELECT COUNT(*) n FROM drivers WHERE approved=1 AND blocked=0 AND online=1"),
        ("pending","SELECT COUNT(*) n FROM drivers WHERE approved=0"),
        ("blocked_users","SELECT COUNT(*) n FROM users WHERE blocked=1"),
        ("blocked_drivers","SELECT COUNT(*) n FROM drivers WHERE blocked=1"),
        ("today","SELECT COUNT(*) n FROM orders WHERE date(created_at)=date('now')"),
        ("active","SELECT COUNT(*) n FROM orders WHERE status IN ('SEARCHING','ACCEPTED','NO_ANSWER_WAIT')"),
        ("completed","SELECT COUNT(*) n FROM orders WHERE status='COMPLETED'"),
        ("cancelled","SELECT COUNT(*) n FROM orders WHERE status='CANCELLED'"),
        ("turnover","SELECT COALESCE(SUM(price),0) n FROM orders WHERE status='COMPLETED' AND date(created_at)=date('now')"),
        ("rating","SELECT COALESCE(AVG(rating),0) n FROM ratings")
    ]:
        out[k]=(await q(sql,one=True))["n"]
    return out

async def admin_home_text():
    s=await admin_stats_data()
    return ("👨‍💼 <b>TAXI BOR MI? — ADMIN PANEL</b>\n\n"
            f"📍 Yo‘nalish: <b>{ROUTE}</b>\n\n"
            f"👥 Mijozlar: <b>{s['users']}</b>\n🚕 Haydovchilar: <b>{s['drivers']}</b>\n"
            f"🟢 Online: <b>{s['online']}</b>\n⏳ Tasdiqlash kutilmoqda: <b>{s['pending']}</b>\n"
            f"🚫 Bloklangan mijozlar: <b>{s['blocked_users']}</b>\n🚫 Bloklangan haydovchilar: <b>{s['blocked_drivers']}</b>\n\n"
            f"📦 Bugungi buyurtmalar: <b>{s['today']}</b>\n🔎 Faol: <b>{s['active']}</b>\n"
            f"✅ Yakunlangan: <b>{s['completed']}</b>\n❌ Bekor qilingan: <b>{s['cancelled']}</b>\n"
            f"💰 Bugungi yakunlangan aylanma: <b>{money(s['turnover'])}</b>\n⭐ O‘rtacha reyting: <b>{float(s['rating']):.1f}</b>")

@dp.message(Command("admin"))
async def admin_home(message):
    if not admin_only(message): return
    await message.answer(await admin_home_text(), reply_markup=admin_home_kb())

@dp.callback_query(F.data == "adm:home")
async def adm_home_cb(callback):
    if callback.from_user.id != ADMIN_ID: return await callback.answer("⛔", show_alert=True)
    await callback.message.edit_text(await admin_home_text(), reply_markup=admin_home_kb())
    await callback.answer()

@dp.callback_query(F.data == "adm:stats")
async def adm_stats_cb(callback):
    if callback.from_user.id != ADMIN_ID: return await callback.answer("⛔", show_alert=True)
    s=await admin_stats_data()
    text=(f"📊 <b>TO‘LIQ STATISTIKA</b>\n\n👥 Mijozlar: {s['users']}\n🚕 Haydovchilar: {s['drivers']}\n"
          f"🟢 Online: {s['online']}\n⏳ Pending: {s['pending']}\n🚫 Bloklangan mijoz: {s['blocked_users']}\n🚫 Bloklangan haydovchi: {s['blocked_drivers']}\n\n"
          f"📦 Bugungi buyurtma: {s['today']}\n🔎 Faol: {s['active']}\n✅ Yakunlangan: {s['completed']}\n❌ Bekor: {s['cancelled']}\n"
          f"💰 Bugungi aylanma: {money(s['turnover'])}\n⭐ O‘rtacha reyting: {float(s['rating']):.1f}")
    await callback.message.edit_text(text, reply_markup=admin_back_kb()); await callback.answer()

@dp.callback_query(F.data == "adm:pending")
async def adm_pending_cb(callback):
    if callback.from_user.id != ADMIN_ID: return await callback.answer("⛔", show_alert=True)
    rows=await q("SELECT tg_id,name,phone,car_model,plate,route,created_at FROM drivers WHERE approved=0 ORDER BY created_at DESC LIMIT 50",all_rows=True)
    if not rows:
        return await callback.message.edit_text("⏳ <b>TASDIQLASH KUTILAYOTGAN HAYDOVCHILAR</b>\n\nHozircha ariza yo‘q.",reply_markup=admin_back_kb())
    kb=[]
    text="⏳ <b>TASDIQLASH KUTILAYOTGAN HAYDOVCHILAR</b>\n\n"
    for d in rows:
        text += f"🚕 <b>{d['name']}</b>\n📞 {d['phone']}\n🚗 {d['car_model']} | {d['plate']}\n📍 {d['route']}\n\n"
        kb.append([InlineKeyboardButton(text=f"👤 {d['name']}",callback_data=f"adm:driver:{d['tg_id']}")])
    kb.append([InlineKeyboardButton(text="⬅️ Admin panel",callback_data="adm:home")])
    await callback.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)); await callback.answer()

@dp.callback_query(F.data.startswith("adm:driver:"))
async def adm_driver_cb(callback):
    if callback.from_user.id != ADMIN_ID:return await callback.answer("⛔",show_alert=True)
    uid=int(callback.data.split(":")[2]); d=await get_driver(uid)
    if not d:return await callback.answer("Haydovchi topilmadi",show_alert=True)
    rating=(d["rating_sum"]/d["rating_count"]) if d["rating_count"] else 0
    status="⏳ KUTILMOQDA" if not d["approved"] else ("🚫 BLOKLANGAN" if d["blocked"] else ("🟢 ONLINE" if d["online"] else "🔴 OFFLINE"))
    text=(f"🚕 <b>HAYDOVCHI PROFILI</b>\n\n👤 {d['name']}\n🆔 <code>{uid}</code>\n📞 {d['phone']}\n"
          f"🚗 {d['car_model']}\n🔢 {d['plate']}\n📍 {d['route']}\n\n📌 Holati: {status}\n📦 Faol: {d['active_orders']}/{MAX_ACTIVE}\n"
          f"⭐ Reyting: {rating:.1f} ({d['rating_count']} ta)\n🕐 Ro‘yxatdan: {d['created_at']}")
    await callback.message.edit_text(text,reply_markup=admin_driver_detail_kb(uid,d["approved"],d["blocked"])); await callback.answer()

@dp.callback_query(F.data.startswith("adm:doc:"))
async def adm_doc_cb(callback):
    if callback.from_user.id != ADMIN_ID:return await callback.answer("⛔",show_alert=True)
    _,_,uid_s,kind=callback.data.split(":"); d=await get_driver(int(uid_s))
    if not d:return await callback.answer("Topilmadi",show_alert=True)
    fid={"license":d["license_file"],"tech":d["tech_file"],"car":d["car_file"]}.get(kind)
    cap={"license":"🪪 Haydovchilik guvohnomasi","tech":"📄 Texpasport","car":"🚗 Mashina rasmi"}.get(kind,"Hujjat")
    try: await bot.send_photo(ADMIN_ID,fid,caption=cap)
    except Exception: await callback.answer("Rasmni yuborib bo‘lmadi",show_alert=True); return
    await callback.answer("Yuborildi")

@dp.callback_query(F.data.startswith("adm:approve:"))
async def adm_approve_cb(callback):
    if callback.from_user.id != ADMIN_ID:return await callback.answer("⛔",show_alert=True)
    uid=int(callback.data.split(":")[2]); d=await get_driver(uid)
    if not d:return await callback.answer("Topilmadi",show_alert=True)
    await q("UPDATE drivers SET approved=1,blocked=0,online=0,updated_at=? WHERE tg_id=?",(now(),uid))
    try: await bot.send_message(uid,"✅ Siz tasdiqlandingiz. /start orqali Haydovchi bo‘limiga kiring.")
    except Exception: pass
    await callback.answer("Tasdiqlandi",show_alert=True); await adm_driver_cb(callback)

@dp.callback_query(F.data.startswith("adm:reject:"))
async def adm_reject_cb(callback):
    if callback.from_user.id != ADMIN_ID:return await callback.answer("⛔",show_alert=True)
    uid=int(callback.data.split(":")[2]); d=await get_driver(uid)
    if not d:return await callback.answer("Topilmadi",show_alert=True)
    await q("DELETE FROM drivers WHERE tg_id=?",(uid,))
    try: await bot.send_message(uid,"❌ Arizangiz rad etildi. Istasangiz qayta ro‘yxatdan o‘tishingiz mumkin.")
    except Exception: pass
    await callback.answer("Rad etildi",show_alert=True); await adm_pending_cb(callback)

@dp.callback_query(F.data.startswith("adm:toggleblock:"))
async def adm_toggleblock_cb(callback):
    if callback.from_user.id != ADMIN_ID:return await callback.answer("⛔",show_alert=True)
    _,_,uid_s,kind=callback.data.split(":"); uid=int(uid_s)
    if kind=="driver":
        d=await get_driver(uid)
        if not d:return await callback.answer("Topilmadi",show_alert=True)
        new=0 if d["blocked"] else 1
        await q("UPDATE drivers SET blocked=?,online=0,updated_at=? WHERE tg_id=?",(new,now(),uid))
        await callback.answer("Blokdan chiqarildi" if not new else "Bloklandi",show_alert=True); return await adm_driver_cb(callback)
    u=await get_user(uid)
    if not u:return await callback.answer("Topilmadi",show_alert=True)
    new=0 if u["blocked"] else 1
    await q("UPDATE users SET blocked=?,updated_at=? WHERE tg_id=?",(new,now(),uid))
    await callback.answer("Blokdan chiqarildi" if not new else "Bloklandi",show_alert=True); return await adm_user_cb(callback)

@dp.callback_query(F.data.startswith("adm:drivers:"))
async def adm_drivers_cb(callback):
    if callback.from_user.id != ADMIN_ID:return await callback.answer("⛔",show_alert=True)
    page=int(callback.data.split(":")[2]); limit=10; off=page*limit
    rows=await q("SELECT tg_id,name,car_model,plate,approved,online,blocked,active_orders FROM drivers ORDER BY created_at DESC LIMIT ? OFFSET ?",(limit,off),all_rows=True)
    text=f"🚕 <b>HAYDOVCHILAR</b> — {page+1}-sahifa\n\n"; kb=[]
    if not rows:text+="Haydovchilar yo‘q."
    for d in rows:
        st="⏳" if not d["approved"] else ("🚫" if d["blocked"] else ("🟢" if d["online"] else "🔴"))
        text+=f"{st} <b>{d['name']}</b> | {d['car_model']} | {d['plate']} | {d['active_orders']}/{MAX_ACTIVE}\n"
        kb.append([InlineKeyboardButton(text=f"👤 {d['name']}",callback_data=f"adm:driver:{d['tg_id']}")])
    nav=[]
    if page>0:nav.append(InlineKeyboardButton(text="⬅️",callback_data=f"adm:drivers:{page-1}"))
    if len(rows)==limit:nav.append(InlineKeyboardButton(text="➡️",callback_data=f"adm:drivers:{page+1}"))
    if nav:kb.append(nav)
    kb.append([InlineKeyboardButton(text="⬅️ Admin panel",callback_data="adm:home")])
    await callback.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)); await callback.answer()

@dp.callback_query(F.data.startswith("adm:users:"))
async def adm_users_cb(callback):
    if callback.from_user.id != ADMIN_ID:return await callback.answer("⛔",show_alert=True)
    page=int(callback.data.split(":")[2]); limit=10; off=page*limit
    rows=await q("SELECT tg_id,name,phone,blocked FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",(limit,off),all_rows=True)
    text=f"👥 <b>MIJOZLAR</b> — {page+1}-sahifa\n\n"; kb=[]
    for u in rows:
        text+=f"{'🚫' if u['blocked'] else '👤'} <b>{u['name']}</b> | {u['phone']}\n"; kb.append([InlineKeyboardButton(text=f"👤 {u['name']}",callback_data=f"adm:user:{u['tg_id']}")])
    if not rows:text+="Mijozlar yo‘q."
    nav=[]
    if page>0:nav.append(InlineKeyboardButton(text="⬅️",callback_data=f"adm:users:{page-1}"))
    if len(rows)==limit:nav.append(InlineKeyboardButton(text="➡️",callback_data=f"adm:users:{page+1}"))
    if nav:kb.append(nav)
    kb.append([InlineKeyboardButton(text="⬅️ Admin panel",callback_data="adm:home")])
    await callback.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)); await callback.answer()

@dp.callback_query(F.data.startswith("adm:user:"))
async def adm_user_cb(callback):
    if callback.from_user.id != ADMIN_ID:return await callback.answer("⛔",show_alert=True)
    uid=int(callback.data.split(":")[2]); u=await get_user(uid)
    if not u:return await callback.answer("Mijoz topilmadi",show_alert=True)
    stats=await q("SELECT COUNT(*) n, SUM(CASE WHEN status='COMPLETED' THEN 1 ELSE 0 END) completed, SUM(CASE WHEN status='CANCELLED' THEN 1 ELSE 0 END) cancelled FROM orders WHERE customer_id=?",(uid,),one=True)
    text=(f"👤 <b>MIJOZ PROFILI</b>\n\n👤 {u['name']}\n🆔 <code>{uid}</code>\n📞 {u['phone']}\n🌐 Til: {u['lang']}\n"
          f"📦 Buyurtmalar: {stats['n'] or 0}\n✅ Yakunlangan: {stats['completed'] or 0}\n❌ Bekor: {stats['cancelled'] or 0}\n"
          f"📌 Holati: {'🚫 BLOK' if u['blocked'] else '✅ FAOL'}")
    await callback.message.edit_text(text,reply_markup=admin_user_detail_kb(uid,u['blocked'])); await callback.answer()

@dp.callback_query(F.data.startswith("adm:orders:"))
async def adm_orders_cb(callback):
    if callback.from_user.id != ADMIN_ID:return await callback.answer("⛔",show_alert=True)
    page=int(callback.data.split(":")[2]); limit=10; off=page*limit
    rows=await q("SELECT id,status,service,price,customer_id,driver_id,origin,destination FROM orders ORDER BY id DESC LIMIT ? OFFSET ?",(limit,off),all_rows=True)
    text=f"📦 <b>BUYURTMALAR</b> — {page+1}-sahifa\n\n"; kb=[]
    for o in rows:
        text+=f"#{o['id']} | {o['status']} | {money(o['price'])}\n📍 {o['origin']} → {o['destination']}\n\n"
        kb.append([InlineKeyboardButton(text=f"📦 #{o['id']}",callback_data=f"adm:order:{o['id']}")])
    if not rows:text+="Buyurtmalar yo‘q."
    nav=[]
    if page>0:nav.append(InlineKeyboardButton(text="⬅️",callback_data=f"adm:orders:{page-1}"))
    if len(rows)==limit:nav.append(InlineKeyboardButton(text="➡️",callback_data=f"adm:orders:{page+1}"))
    if nav:kb.append(nav)
    kb.append([InlineKeyboardButton(text="⬅️ Admin panel",callback_data="adm:home")])
    await callback.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)); await callback.answer()

@dp.callback_query(F.data.startswith("adm:order:"))
async def adm_order_cb(callback):
    if callback.from_user.id != ADMIN_ID:return await callback.answer("⛔",show_alert=True)
    oid=int(callback.data.split(":")[2]); o=await q("SELECT * FROM orders WHERE id=?",(oid,),one=True)
    if not o:return await callback.answer("Buyurtma topilmadi",show_alert=True)
    u=await get_user(o["customer_id"]); d=await get_driver(o["driver_id"]) if o["driver_id"] else None
    gps=f"{o['lat']}, {o['lon']}" if o['lat'] is not None else "❌ Yuborilmagan"
    text=(f"📦 <b>BUYURTMA #{oid}</b>\n\n📌 Status: <b>{o['status']}</b>\n📦 Xizmat: {'📦 Dastavka' if o['service']=='delivery' else '👤 Yo‘lovchi'}\n"
          f"👥 Yo‘lovchi: {o['passengers'] or '-'}\n\n📍 <b>QAYERDAN:</b> {o['origin']}\n🏁 <b>QAYERGA:</b> {o['destination']}\n"
          f"📍 GPS: {gps}\n💰 Narx: {money(o['price'])}\n\n👤 Mijoz: {u['name'] if u else o['customer_id']}\n📞 {u['phone'] if u else '-'}\n"
          f"🚕 Haydovchi: {d['name'] if d else '—'}\n📞 {d['phone'] if d else '—'}\n🕐 {o['created_at']}")
    if o["lat"] is not None:
        try: await bot.send_location(ADMIN_ID,o["lat"],o["lon"])
        except Exception: pass
    await callback.message.edit_text(text,reply_markup=admin_order_detail_kb(oid)); await callback.answer()

@dp.callback_query(F.data == "adm:online")
async def adm_online_cb(callback):
    if callback.from_user.id != ADMIN_ID:return await callback.answer("⛔",show_alert=True)
    rows=await q("SELECT tg_id,name,phone,car_model,plate,active_orders FROM drivers WHERE approved=1 AND blocked=0 AND online=1 ORDER BY name",all_rows=True)
    text="🟢 <b>ONLINE HAYDOVCHILAR</b>\n\n"; kb=[]
    for d in rows:
        text+=f"🟢 {d['name']} | {d['car_model']} | {d['plate']} | {d['active_orders']}/{MAX_ACTIVE}\n"; kb.append([InlineKeyboardButton(text=d['name'],callback_data=f"adm:driver:{d['tg_id']}")])
    if not rows:text+="Hozir online haydovchi yo‘q."
    kb.append([InlineKeyboardButton(text="⬅️ Admin panel",callback_data="adm:home")]); await callback.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)); await callback.answer()

@dp.callback_query(F.data == "adm:blocked")
async def adm_blocked_cb(callback):
    if callback.from_user.id != ADMIN_ID:return await callback.answer("⛔",show_alert=True)
    ds=await q("SELECT tg_id,name FROM drivers WHERE blocked=1 ORDER BY name",all_rows=True); us=await q("SELECT tg_id,name FROM users WHERE blocked=1 ORDER BY name",all_rows=True)
    text="🚫 <b>BLOKLANGANLAR</b>\n\n🚕 Haydovchilar:\n"+("\n".join(f"• {d['name']}" for d in ds) or "Yo‘q")+"\n\n👥 Mijozlar:\n"+("\n".join(f"• {u['name']}" for u in us) or "Yo‘q")
    await callback.message.edit_text(text,reply_markup=admin_back_kb()); await callback.answer()

@dp.callback_query(F.data == "adm:support")
async def adm_support_cb(callback):
    if callback.from_user.id != ADMIN_ID:return await callback.answer("⛔",show_alert=True)
    rows=await q("SELECT * FROM support_tickets WHERE status='OPEN' ORDER BY id DESC LIMIT 30",all_rows=True)
    text="📩 <b>OCHIQ MUROJAATLAR</b>\n\n"; kb=[]
    for tkt in rows:
        text+=f"#{tkt['id']} | User: {tkt['user_id']}\n{tkt['text'][:150]}\n\n"
        kb.append([InlineKeyboardButton(text=f"📩 #{tkt['id']}",callback_data=f"adm:ticket:{tkt['id']}")])
    if not rows:text+="Ochiq murojaat yo‘q."
    kb.append([InlineKeyboardButton(text="⬅️ Admin panel",callback_data="adm:home")]); await callback.message.edit_text(text,reply_markup=InlineKeyboardMarkup(inline_keyboard=kb)); await callback.answer()

@dp.callback_query(F.data.startswith("adm:ticket:"))
async def adm_ticket_cb(callback):
    if callback.from_user.id != ADMIN_ID:return await callback.answer("⛔",show_alert=True)
    tid=int(callback.data.split(":")[2]); tkt=await q("SELECT * FROM support_tickets WHERE id=?",(tid,),one=True)
    if not tkt:return await callback.answer("Murojaat topilmadi",show_alert=True)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Yopish",callback_data=f"adm:ticketclose:{tid}")],[InlineKeyboardButton(text="⬅️ Murojaatlar",callback_data="adm:support")]])
    await callback.message.edit_text(f"📩 <b>MUROJAAT #{tid}</b>\n\n👤 User: <code>{tkt['user_id']}</code>\n🕐 {tkt['created_at']}\n\n{tkt['text']}",reply_markup=kb); await callback.answer()

@dp.callback_query(F.data.startswith("adm:ticketclose:"))
async def adm_ticketclose_cb(callback):
    if callback.from_user.id != ADMIN_ID:return await callback.answer("⛔",show_alert=True)
    tid=int(callback.data.split(":")[2]); await q("UPDATE support_tickets SET status='CLOSED' WHERE id=?",(tid,)); await callback.answer("Yopildi",show_alert=True); await adm_support_cb(callback)

@dp.callback_query(F.data.startswith("adm:userorders:"))
async def adm_userorders_cb(callback):
    if callback.from_user.id != ADMIN_ID:return await callback.answer("⛔",show_alert=True)
    _,_,uid_s,page_s=callback.data.split(":"); uid=int(uid_s); page=int(page_s); rows=await q("SELECT id,status,origin,destination,price FROM orders WHERE customer_id=? ORDER BY id DESC LIMIT 10 OFFSET ?",(uid,page*10),all_rows=True)
    text=f"📦 <b>MIJOZ BUYURTMALARI</b>\n\n"+"\n".join(f"#{o['id']} | {o['status']} | {money(o['price'])}\n{o['origin']} → {o['destination']}" for o in rows) or "Tarix bo‘sh."
    await callback.message.edit_text(text,reply_markup=admin_user_detail_kb(uid,(await get_user(uid))["blocked"])); await callback.answer()

@dp.callback_query(F.data.startswith("adm:driverorders:"))
async def adm_driverorders_cb(callback):
    if callback.from_user.id != ADMIN_ID:return await callback.answer("⛔",show_alert=True)
    _,_,uid_s,page_s=callback.data.split(":"); uid=int(uid_s); page=int(page_s); rows=await q("SELECT id,status,origin,destination,price FROM orders WHERE driver_id=? ORDER BY id DESC LIMIT 10 OFFSET ?",(uid,page*10),all_rows=True)
    text=f"📦 <b>HAYDOVCHI BUYURTMALARI</b>\n\n"+"\n".join(f"#{o['id']} | {o['status']} | {money(o['price'])}\n{o['origin']} → {o['destination']}" for o in rows) or "Tarix bo‘sh."
    d=await get_driver(uid); await callback.message.edit_text(text,reply_markup=admin_driver_detail_kb(uid,d["approved"],d["blocked"])); await callback.answer()

@dp.callback_query(F.data.startswith("adm:driverincome:"))
async def adm_driverincome_cb(callback):
    if callback.from_user.id != ADMIN_ID:return await callback.answer("⛔",show_alert=True)
    uid=int(callback.data.split(":")[2]); r=await q("SELECT COUNT(*) n,COALESCE(SUM(price),0) total FROM orders WHERE driver_id=? AND status='COMPLETED'",(uid,),one=True); d=await get_driver(uid)
    text=f"💰 <b>HAYDOVCHI DAROMADI</b>\n\n👤 {d['name']}\n📦 Yakunlangan: {r['n']}\n💰 Jami: {money(r['total'])}"
    await callback.message.edit_text(text,reply_markup=admin_driver_detail_kb(uid,d["approved"],d["blocked"])); await callback.answer()

# Compatibility commands
@dp.message(Command("admin_pending"))
async def admin_pending_cmd(message):
    if not admin_only(message):return
    await message.answer(await admin_home_text(),reply_markup=admin_home_kb()); await message.answer("⏳ Tasdiqlash bo‘limini ochish uchun yuqoridagi tugmani bosing.")

@dp.message(F.text.regexp(r"^/admin_(approve|reject)_\d+$"))
async def admin_decision_cmd(message):
    if not admin_only(message):return
    _,action,uid_s=message.text.split("_"); uid=int(uid_s); d=await get_driver(uid)
    if not d:return await message.answer("❌ Haydovchi topilmadi.")
    if action=="approve":
        await q("UPDATE drivers SET approved=1,blocked=0,online=0,updated_at=? WHERE tg_id=?",(now(),uid))
        try: await bot.send_message(uid,"✅ Siz tasdiqlandingiz. /start orqali Haydovchi bo‘limiga kiring.")
        except Exception: pass
        return await message.answer("✅ Haydovchi tasdiqlandi.")
    await q("DELETE FROM drivers WHERE tg_id=?",(uid,)); await message.answer("❌ Ariza rad etildi. Qayta ro‘yxatdan o‘tish mumkin.")

@dp.message(Command("admin_drivers"))
async def admin_drivers_cmd(message):
    if not admin_only(message):return
    await message.answer("🚕 Haydovchilar bo‘limi:",reply_markup=admin_home_kb())

@dp.message(Command("admin_users"))
async def admin_users_cmd(message):
    if not admin_only(message):return
    await message.answer("👥 Mijozlar bo‘limi:",reply_markup=admin_home_kb())

@dp.message(Command("admin_orders"))
async def admin_orders_cmd(message):
    if not admin_only(message):return
    await message.answer("📦 Buyurtmalar bo‘limi:",reply_markup=admin_home_kb())

@dp.message(Command("admin_order"))
async def admin_order_cmd(message):
    if not admin_only(message):return
    p=message.text.split()
    if len(p)!=2:return await message.answer("Format: /admin_order ORDER_ID")
    try: oid=int(p[1])
    except ValueError:return await message.answer("❌ ID noto‘g‘ri.")
    o=await q("SELECT id FROM orders WHERE id=?",(oid,),one=True)
    if not o:return await message.answer("❌ Buyurtma topilmadi.")
    # Reuse detail logic by sending a compact direct view.
    full=await q("SELECT * FROM orders WHERE id=?",(oid,),one=True); u=await get_user(full["customer_id"]); d=await get_driver(full["driver_id"]) if full["driver_id"] else None
    gps=f"{full['lat']}, {full['lon']}" if full['lat'] is not None else "❌ Yuborilmagan"
    await message.answer(f"📦 <b>BUYURTMA #{oid}</b>\n\n📌 {full['status']}\n📍 QAYERDAN: {full['origin']}\n🏁 QAYERGA: {full['destination']}\n💰 {money(full['price'])}\n📍 GPS: {gps}\n\n👤 {u['name'] if u else '-'} | 📞 {u['phone'] if u else '-'}\n🚕 {d['name'] if d else '-'} | 📞 {d['phone'] if d else '-'}",reply_markup=admin_order_detail_kb(oid))

@dp.message(Command("admin_block"))
async def admin_block_cmd(message):
    if not admin_only(message):return
    p=message.text.split()
    if len(p)!=2:return await message.answer("Format: /admin_block TELEGRAM_ID")
    try:uid=int(p[1])
    except ValueError:return await message.answer("❌ ID noto‘g‘ri.")
    await q("UPDATE users SET blocked=1,updated_at=? WHERE tg_id=?",(now(),uid)); await q("UPDATE drivers SET blocked=1,online=0,updated_at=? WHERE tg_id=?",(now(),uid)); await message.answer("🚫 Bloklandi.")

@dp.message(Command("admin_unblock"))
async def admin_unblock_cmd(message):
    if not admin_only(message):return
    p=message.text.split()
    if len(p)!=2:return await message.answer("Format: /admin_unblock TELEGRAM_ID")
    try:uid=int(p[1])
    except ValueError:return await message.answer("❌ ID noto‘g‘ri.")
    await q("UPDATE users SET blocked=0,updated_at=? WHERE tg_id=?",(now(),uid)); await q("UPDATE drivers SET blocked=0,updated_at=? WHERE tg_id=?",(now(),uid)); await message.answer("✅ Blokdan chiqarildi.")

# ============================================================
# FALLBACK
# ============================================================
@dp.message()
async def fallback(message: Message, state: FSMContext):
    if await state.get_state(): return
    u=await get_user(message.from_user.id)
    if not u:return await message.answer("Avval /start ni bosing.")
    if u["blocked"]:return await message.answer(tr(u["lang"],"blocked"))
    await message.answer(tr(u["lang"],"main"),reply_markup=main_kb(u["lang"]))


async def main():
    init_db()
    log.info("TAXI BOR MI started | route=%s | max_active=%s",ROUTE,MAX_ACTIVE)
    await dp.start_polling(bot)


if __name__=="__main__":
    asyncio.run(main())
