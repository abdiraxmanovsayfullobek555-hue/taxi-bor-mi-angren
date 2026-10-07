import os
import re
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
    Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
    InlineKeyboardMarkup, InlineKeyboardButton
)

# ============================================================
# TAXI BOR MI? — ALBATTA BOR! | OBLIQ ↔ ANGREN
# Clean V1 — customer + driver + admin
# Railway variables: BOT_TOKEN, ADMIN_ID
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
DB_PATH = os.getenv("DB_PATH", "taxi_bor_mi.db")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN Railway Variables ichida topilmadi.")
if not ADMIN_ID:
    raise RuntimeError("ADMIN_ID Railway Variables ichida topilmadi.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("taxi_bor_mi")

bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

DB_LOCK = asyncio.Lock()
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row

# ---------------- DATABASE ----------------

def init_db():
    db.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER UNIQUE NOT NULL,
        role TEXT NOT NULL DEFAULT 'customer',
        lang TEXT NOT NULL DEFAULT 'uz',
        name TEXT DEFAULT '',
        phone TEXT DEFAULT '',
        blocked INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS drivers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER UNIQUE NOT NULL,
        full_name TEXT NOT NULL,
        phone TEXT NOT NULL,
        car_model TEXT NOT NULL,
        plate TEXT NOT NULL UNIQUE,
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
    CREATE TABLE IF NOT EXISTS orders(
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
    CREATE TABLE IF NOT EXISTS order_declines(
        order_id INTEGER NOT NULL,
        driver_tg_id INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY(order_id, driver_tg_id)
    );
    CREATE TABLE IF NOT EXISTS ratings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_id INTEGER NOT NULL UNIQUE,
        from_tg_id INTEGER NOT NULL,
        to_tg_id INTEGER NOT NULL,
        score INTEGER NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS support_tickets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tg_id INTEGER NOT NULL,
        text TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN',
        created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS audit_logs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_tg_id INTEGER,
        action TEXT NOT NULL,
        details TEXT DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_driver_online ON drivers(approved, online);
    CREATE INDEX IF NOT EXISTS idx_order_status ON orders(status);
    """)
    db.commit()


def now():
    return datetime.utcnow().replace(microsecond=0).isoformat()

async def q(sql, params=(), *, one=False, many=False, commit=True):
    async with DB_LOCK:
        cur = db.execute(sql, params)
        if commit:
            db.commit()
        if one:
            return cur.fetchone()
        if many:
            return cur.fetchall()
        return cur.lastrowid

async def audit(action, details=""):
    await q("INSERT INTO audit_logs(actor_tg_id,action,details,created_at) VALUES(?,?,?,?)",
            (ADMIN_ID, action, details, now()))

async def user(tg_id):
    return await q("SELECT * FROM users WHERE tg_id=?", (tg_id,), one=True)

async def driver(tg_id):
    return await q("SELECT * FROM drivers WHERE tg_id=?", (tg_id,), one=True)

async def ensure_user(message):
    u = await user(message.from_user.id)
    if not u:
        await message.answer("Avval /start bosing.")
        return None
    if u["blocked"]:
        await message.answer("🚫 Akkauntingiz bloklangan.")
        return None
    return u

def money(n):
    return f"{int(n):,}".replace(",", " ") + " so‘m"

# ---------------- LANGUAGES ----------------

LANG_BUTTONS = {
    "🇺🇿 O‘zbekcha": "uz", "🇺🇿 Ўзбекча": "uzc",
    "🇷🇺 Русский": "ru", "🇬🇧 English": "en"
}

T = {
    "uz": {
        "passenger":"👤 Yo‘lovchi", "profile":"👤 Profil", "history":"📜 Tarix",
        "support":"📩 Murojaat", "driver":"🚕 Haydovchi bo‘lish", "back":"⬅️ Orqaga",
        "name":"👤 Ism-familiyangizni kiriting:", "phone":"📱 Telefon raqamingizni yuboring:",
        "registered":"✅ Ro‘yxatdan o‘tish yakunlandi!", "service":"Xizmatni tanlang:",
        "from":"📍 QAYERDAN?", "to":"🏁 QAYERGA?", "gps":"📍 GPS yuborish",
        "nogps":"⏭ GPSsiz davom etish", "people":"👥 Necha kishi?", "price":"💰 Narxni tanlang:",
        "other":"✍️ Boshqa narx", "confirm":"✅ BUYURTMA BERISH", "edit":"✏️ O‘ZGARTIRISH",
        "cancel":"❌ BEKOR QILISH", "yes":"HA", "no":"YO‘Q"
    },
    "uzc": {
        "passenger":"👤 Йўловчи", "profile":"👤 Профиль", "history":"📜 Тарих",
        "support":"📩 Мурожаат", "driver":"🚕 Ҳайдовчи бўлиш", "back":"⬅️ Орқага",
        "name":"👤 Исм-фамилиянгизни киритинг:", "phone":"📱 Телефон рақамингизни юборинг:",
        "registered":"✅ Рўйхатдан ўтиш якунланди!", "service":"Хизматни танланг:",
        "from":"📍 ҚАЕРДАН?", "to":"🏁 ҚАЕРГА?", "gps":"📍 GPS юбориш",
        "nogps":"⏭ GPSсиз давом этиш", "people":"👥 Неча киши?", "price":"💰 Нарҳни танланг:",
        "other":"✍️ Бошқа нарҳ", "confirm":"✅ БУЮРТМА БЕРИШ", "edit":"✏️ ЎЗГАРТИРИШ",
        "cancel":"❌ БЕКОР ҚИЛИШ", "yes":"ҲА", "no":"ЙЎҚ"
    },
    "ru": {
        "passenger":"👤 Пассажир", "profile":"👤 Профиль", "history":"📜 История",
        "support":"📩 Поддержка", "driver":"🚕 Стать водителем", "back":"⬅️ Назад",
        "name":"👤 Введите имя и фамилию:", "phone":"📱 Отправьте номер телефона:",
        "registered":"✅ Регистрация завершена!", "service":"Выберите услугу:",
        "from":"📍 ОТКУДА?", "to":"🏁 КУДА?", "gps":"📍 Отправить GPS",
        "nogps":"⏭ Продолжить без GPS", "people":"👥 Сколько человек?", "price":"💰 Выберите цену:",
        "other":"✍️ Другая цена", "confirm":"✅ ЗАКАЗАТЬ", "edit":"✏️ ИЗМЕНИТЬ",
        "cancel":"❌ ОТМЕНА", "yes":"ДА", "no":"НЕТ"
    },
    "en": {
        "passenger":"👤 Passenger", "profile":"👤 Profile", "history":"📜 History",
        "support":"📩 Support", "driver":"🚕 Become a driver", "back":"⬅️ Back",
        "name":"👤 Enter your full name:", "phone":"📱 Send your phone number:",
        "registered":"✅ Registration completed!", "service":"Choose a service:",
        "from":"📍 FROM?", "to":"🏁 TO?", "gps":"📍 Send GPS",
        "nogps":"⏭ Continue without GPS", "people":"👥 How many people?", "price":"💰 Choose price:",
        "other":"✍️ Other price", "confirm":"✅ PLACE ORDER", "edit":"✏️ EDIT",
        "cancel":"❌ CANCEL", "yes":"YES", "no":"NO"
    }
}

def lang_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🇺🇿 O‘zbekcha"), KeyboardButton(text="🇺🇿 Ўзбекча")],
        [KeyboardButton(text="🇷🇺 Русский"), KeyboardButton(text="🇬🇧 English")]
    ], resize_keyboard=True)

def main_kb(lang):
    t=T.get(lang,T["uz"])
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=t["passenger"])],
        [KeyboardButton(text=t["driver"])],
        [KeyboardButton(text=t["profile"]), KeyboardButton(text=t["history"])],
        [KeyboardButton(text=t["support"])]
    ], resize_keyboard=True)

def contact_kb(lang):
    label = {"uz":"📱 Raqamni yuborish","uzc":"📱 Рақамни юбориш","ru":"📱 Отправить номер","en":"📱 Send phone"}.get(lang,"📱 Raqamni yuborish")
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=label, request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)

def gps_kb(lang):
    t=T.get(lang,T["uz"])
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text=t["gps"], request_location=True)],
        [KeyboardButton(text=t["nogps"])]
    ], resize_keyboard=True, one_time_keyboard=True)

def service_kb(lang):
    t=T.get(lang,T["uz"])
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="1️⃣ 1 kishi"), KeyboardButton(text="2️⃣ 2 kishi")],
        [KeyboardButton(text="3️⃣ 3 kishi"), KeyboardButton(text="4️⃣ 4 kishi")],
        [KeyboardButton(text="📦 DASTAVKA")]
    ], resize_keyboard=True, one_time_keyboard=True)

def price_kb(lang):
    t=T.get(lang,T["uz"])
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="5 000 so‘m"), KeyboardButton(text="10 000 so‘m")],
        [KeyboardButton(text="15 000 so‘m"), KeyboardButton(text="20 000 so‘m")],
        [KeyboardButton(text=t["other"])]
    ], resize_keyboard=True, one_time_keyboard=True)

def confirm_kb(lang):
    t=T.get(lang,T["uz"])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t["confirm"], callback_data="order:confirm")],
        [InlineKeyboardButton(text=t["edit"], callback_data="order:edit"), InlineKeyboardButton(text=t["cancel"], callback_data="order:cancel")]
    ])

def driver_offer_kb(oid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ BUYURTMANI OLISH", callback_data=f"claim:{oid}")],
        [InlineKeyboardButton(text="❌ RAD ETISH", callback_data=f"decline:{oid}")]
    ])

def driver_panel_kb(d):
    online = bool(d["online"])
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🔴 OFFLINE" if online else "🟢 ONLINE")],
        [KeyboardButton(text=f"🚕 FAOL BUYURTMALAR ({d['active_orders']}/4)")],
        [KeyboardButton(text="📜 BUYURTMALAR TARIXI"), KeyboardButton(text="💰 DAROMAD")],
        [KeyboardButton(text="⭐ REYTING"), KeyboardButton(text="👤 PROFIL")],
        [KeyboardButton(text="📩 MUROJAAT"), KeyboardButton(text="⬅️ Orqaga")]
    ], resize_keyboard=True)

def admin_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 STATISTIKA", callback_data="adm:stats")],
        [InlineKeyboardButton(text="🚕 HAYDOVCHILAR", callback_data="adm:drivers"), InlineKeyboardButton(text="⏳ TASDIQLASH", callback_data="adm:pending")],
        [InlineKeyboardButton(text="👥 MIJOZLAR", callback_data="adm:users"), InlineKeyboardButton(text="📦 BUYURTMALAR", callback_data="adm:orders")],
        [InlineKeyboardButton(text="🟢 ONLINE", callback_data="adm:online"), InlineKeyboardButton(text="🚫 BLOKLANGAN", callback_data="adm:blocked")],
        [InlineKeyboardButton(text="⭐ REYTINGLAR", callback_data="adm:ratings"), InlineKeyboardButton(text="📩 MUROJAATLAR", callback_data="adm:support")]
    ])

# ---------------- STATES ----------------

class Reg(StatesGroup):
    lang=State(); name=State(); phone=State()
class Order(StatesGroup):
    service=State(); origin=State(); destination=State(); gps=State(); price=State(); custom_price=State(); confirm=State()
class DriverReg(StatesGroup):
    name=State(); phone=State(); car=State(); plate=State(); license=State(); tech=State(); photo=State(); rules=State()
class Support(StatesGroup):
    text=State()
class NoAnswer(StatesGroup):
    answer=State()

# ---------------- START / REGISTRATION ----------------

@dp.message(CommandStart())
async def start(message: Message, state: FSMContext):
    u=await user(message.from_user.id)
    if u:
        if u["blocked"]:
            await message.answer("🚫 Akkauntingiz bloklangan."); return
        await state.clear()
        await message.answer("🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n📍 OBLIQ ↔ ANGREN\n\n"+T[u["lang"]]["service"], reply_markup=main_kb(u["lang"]))
        return
    await state.clear(); await state.set_state(Reg.lang)
    await message.answer("🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\nTilni tanlang / Выберите язык / Choose language:", reply_markup=lang_kb())

@dp.message(Reg.lang)
async def reg_lang(message: Message, state: FSMContext):
    lang=LANG_BUTTONS.get((message.text or "").strip())
    if not lang:
        await message.answer("Iltimos, tilni tugmadan tanlang.", reply_markup=lang_kb()); return
    await state.update_data(lang=lang); await state.set_state(Reg.name)
    await message.answer(T[lang]["name"])

@dp.message(Reg.name)
async def reg_name(message: Message, state: FSMContext):
    name=(message.text or "").strip()
    if len(name)<2: await message.answer("❗ Ism-familiyani kiriting."); return
    await state.update_data(name=name); data=await state.get_data(); await state.set_state(Reg.phone)
    await message.answer(T[data["lang"]]["phone"], reply_markup=contact_kb(data["lang"]))

@dp.message(Reg.phone, F.contact)
async def reg_phone_contact(message: Message, state: FSMContext):
    data=await state.get_data(); phone=message.contact.phone_number
    await q("INSERT INTO users(tg_id,role,lang,name,phone,created_at) VALUES(?,?,?,?,?,?)",
            (message.from_user.id,"customer",data["lang"],data["name"],phone,now()))
    await state.clear(); await message.answer(T[data["lang"]]["registered"]+"\n\n📍 OBLIQ ↔ ANGREN\n\n"+T[data["lang"]]["service"], reply_markup=main_kb(data["lang"]))

@dp.message(Reg.phone)
async def reg_phone_text(message: Message, state: FSMContext):
    p=(message.text or "").strip()
    if len(re.sub(r"\D","",p))<7: await message.answer("📱 To‘g‘ri telefon raqam yuboring."); return
    data=await state.get_data()
    await q("INSERT INTO users(tg_id,role,lang,name,phone,created_at) VALUES(?,?,?,?,?,?)",
            (message.from_user.id,"customer",data["lang"],data["name"],p,now()))
    await state.clear(); await message.answer(T[data["lang"]]["registered"]+"\n\n📍 OBLIQ ↔ ANGREN\n\n"+T[data["lang"]]["service"], reply_markup=main_kb(data["lang"]))

# ---------------- CUSTOMER ORDER ----------------

PASSENGER_BUTTONS={"1️⃣ 1 kishi":1,"2️⃣ 2 kishi":2,"3️⃣ 3 kishi":3,"4️⃣ 4 kishi":4}

async def start_order(message, state, service, passengers=None):
    u=await ensure_user(message)
    if not u: return
    await state.clear(); await state.update_data(lang=u["lang"],service=service,passengers=passengers)
    await state.set_state(Order.origin)
    await message.answer("📍 <b>QAYERDAN?</b>\nMasalan: <b>5/5 dan</b> yoki <b>Obliqdan</b>", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=T[u["lang"]]["back"])]],resize_keyboard=True))

@dp.message(F.text.in_({"👤 Yo‘lovchi","👤 Йўловчи","👤 Пассажир","👤 Passenger"}))
async def passenger(message: Message,state:FSMContext):
    u=await ensure_user(message)
    if not u:return
    await state.clear(); await state.update_data(lang=u["lang"]); await state.set_state(Order.service)
    await message.answer("👥 <b>Necha kishi?</b>\n\n1–4 kishi yoki DASTAVKA tanlang:",reply_markup=service_kb(u["lang"]))

@dp.message(Order.service)
async def service_choice(message: Message,state:FSMContext):
    text=(message.text or "").strip(); u=await ensure_user(message)
    if not u:return
    if text=="📦 DASTAVKA": await start_order(message,state,"DELIVERY",None); return
    if text in PASSENGER_BUTTONS: await start_order(message,state,"PASSENGER",PASSENGER_BUTTONS[text]); return
    await message.answer("Iltimos, 1–4 kishi yoki DASTAVKA ni tanlang.",reply_markup=service_kb(u["lang"]))

@dp.message(Order.origin)
async def order_origin(message: Message,state:FSMContext):
    text=(message.text or "").strip()
    if text.startswith("⬅️"):
        u=await user(message.from_user.id); await state.clear(); await message.answer("Xizmatni tanlang:",reply_markup=main_kb(u["lang"])); return
    if len(text)<2: await message.answer("📍 Qayerdan manzilini yozing."); return
    await state.update_data(origin=text); await state.set_state(Order.destination)
    u=await user(message.from_user.id)
    await message.answer("🏁 <b>QAYERGA?</b>\nMasalan: <b>Kaltsoga</b>, <b>Angren markaziga</b> yoki <b>Hokimiyatga</b>",reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=T[u["lang"]]["back"])]],resize_keyboard=True))

@dp.message(Order.destination)
async def order_destination(message: Message,state:FSMContext):
    text=(message.text or "").strip(); u=await user(message.from_user.id)
    if text.startswith("⬅️"):
        await state.set_state(Order.origin); await message.answer("📍 <b>QAYERDAN?</b>"); return
    if len(text)<2: await message.answer("🏁 Qayerga manzilini yozing."); return
    await state.update_data(destination=text); await state.set_state(Order.gps)
    await message.answer("📍 GPS yuborish ixtiyoriy:",reply_markup=gps_kb(u["lang"]))

@dp.message(Order.gps,F.location)
async def order_location(message: Message,state:FSMContext):
    await state.update_data(lat=message.location.latitude,lon=message.location.longitude,gps=True)
    await choose_price(message,state)

@dp.message(Order.gps)
async def order_gps_choice(message: Message,state:FSMContext):
    u=await user(message.from_user.id); t=T[u["lang"]]
    if (message.text or "").strip()==t["nogps"]:
        await state.update_data(lat=None,lon=None,gps=False); await choose_price(message,state); return
    await message.answer("📍 GPS yuborish uchun tugmani bosing yoki GPSsiz davom etish tugmasini tanlang.",reply_markup=gps_kb(u["lang"]))

async def choose_price(message,state):
    u=await user(message.from_user.id); await state.set_state(Order.price)
    await message.answer(T[u["lang"]]["price"],reply_markup=price_kb(u["lang"]))

@dp.message(Order.price)
async def order_price(message: Message,state:FSMContext):
    u=await user(message.from_user.id); text=(message.text or "").strip(); t=T[u["lang"]]
    mapping={"5 000 so‘m":5000,"10 000 so‘m":10000,"15 000 so‘m":15000,"20 000 so‘m":20000}
    if text in mapping:
        await state.update_data(price=mapping[text]); await show_confirm(message,state); return
    if text==t["other"]:
        await state.set_state(Order.custom_price); await message.answer("✍️ Narxni kiriting. Minimum 5 000 so‘m:",reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t["back"])]],resize_keyboard=True)); return
    await message.answer("Narxni tugmadan tanlang.",reply_markup=price_kb(u["lang"]))

@dp.message(Order.custom_price)
async def custom_price(message: Message,state:FSMContext):
    u=await user(message.from_user.id); text=(message.text or "").strip()
    if text.startswith("⬅️"):
        await state.set_state(Order.price); await message.answer(T[u["lang"]]["price"],reply_markup=price_kb(u["lang"])); return
    raw=re.sub(r"\D","",text)
    if not raw or int(raw)<5000 or int(raw)>1000000:
        await message.answer("❗ Narx 5 000 dan 1 000 000 so‘mgacha bo‘lishi kerak."); return
    await state.update_data(price=int(raw)); await show_confirm(message,state)

async def show_confirm(message,state):
    u=await user(message.from_user.id); d=await state.get_data(); await state.set_state(Order.confirm)
    service="📦 DASTAVKA" if d["service"]=="DELIVERY" else "👤 YO‘LOVCHI"
    gps="✅ Yuborilgan" if d.get("gps") else "❌ Yuborilmagan"
    text=f"🚕 <b>BUYURTMA TASDIG‘I</b>\n\n📌 Xizmat: <b>{service}</b>\n"
    if d.get("passengers"): text+=f"👥 Yo‘lovchilar: <b>{d['passengers']} kishi</b>\n"
    text+=f"\n📍 QAYERDAN:\n<b>{escape(d['origin'])}</b>\n\n🏁 QAYERGA:\n<b>{escape(d['destination'])}</b>\n\n📍 GPS: {gps}\n💰 NARX: <b>{money(d['price'])}</b>"
    await message.answer(text,reply_markup=confirm_kb(u["lang"]))

@dp.callback_query(F.data=="order:edit")
async def order_edit(c:CallbackQuery,state:FSMContext):
    await c.answer(); await state.set_state(Order.origin); await c.message.answer("📍 <b>QAYERDAN?</b>")

@dp.callback_query(F.data=="order:cancel")
async def order_cancel(c:CallbackQuery,state:FSMContext):
    await c.answer(); await state.clear(); u=await user(c.from_user.id); await c.message.edit_text("❌ Buyurtma bekor qilindi."); await c.message.answer("Xizmatni tanlang:",reply_markup=main_kb(u["lang"]))

@dp.callback_query(F.data=="order:confirm")
async def order_confirm(c:CallbackQuery,state:FSMContext):
    d=await state.get_data(); u=await user(c.from_user.id)
    if not d.get("origin") or not d.get("destination") or not d.get("price"):
        await c.answer("Ma’lumotlar to‘liq emas.",show_alert=True); return
    deadline=(datetime.utcnow()+timedelta(seconds=120)).isoformat()
    oid=await q("""INSERT INTO orders(customer_tg_id,service,origin,destination,origin_lat,origin_lon,passengers,price,status,created_at,claim_deadline) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (c.from_user.id,d["service"],d["origin"],d["destination"],d.get("lat"),d.get("lon"),d.get("passengers") or 0,d["price"],"SEARCHING",now(),deadline))
    await state.clear(); await c.answer("Buyurtma yuborildi!")
    await c.message.edit_text(f"🔎 <b>BUYURTMA #{oid}</b>\n\nHaydovchi qidirilmoqda...\n📍 {escape(d['origin'])} → {escape(d['destination'])}\n💰 {money(d['price'])}")
    asyncio.create_task(dispatch(oid))

# ---------------- DISPATCH / DRIVER CLAIM ----------------

async def eligible(oid):
    return await q("""SELECT d.* FROM drivers d WHERE d.approved=1 AND d.online=1 AND d.active_orders<4 AND d.route='OBLIQ_ANGREN' AND NOT EXISTS(SELECT 1 FROM order_declines x WHERE x.order_id=? AND x.driver_tg_id=d.tg_id) ORDER BY d.active_orders ASC,d.id ASC""",(oid,),many=True)

async def dispatch(oid):
    await asyncio.sleep(1)
    sent=set()
    for round_no in range(3):
        o=await q("SELECT * FROM orders WHERE id=?",(oid,),one=True)
        if not o or o["status"]!="SEARCHING": return
        if o["claim_deadline"] and o["claim_deadline"]<=now(): break
        ds=await eligible(oid)
        for d in ds:
            if d["tg_id"] in sent: continue
            sent.add(d["tg_id"])
            try:
                gps="✅ Yuborilgan" if o["origin_lat"] is not None else "❌ Yuborilmagan"
                typ="📦 DASTAVKA" if o["service"]=="DELIVERY" else "👤 YO‘LOVCHI"
                people=f"👥 {o['passengers']} kishi\n" if o["service"]=="PASSENGER" else ""
                text=f"🚕 <b>YANGI BUYURTMA #{oid}</b>\n\n{typ}\n{people}📍 QAYERDAN: <b>{escape(o['origin'])}</b>\n🏁 QAYERGA: <b>{escape(o['destination'])}</b>\n💰 NARX: <b>{money(o['price'])}</b>\n📍 GPS: {gps}"
                await bot.send_message(d["tg_id"],text,reply_markup=driver_offer_kb(oid))
            except Exception as e: log.warning("dispatch: %s",e)
        await asyncio.sleep(30)
    o=await q("SELECT * FROM orders WHERE id=?",(oid,),one=True)
    if o and o["status"]=="SEARCHING":
        await q("UPDATE orders SET status='NO_DRIVER' WHERE id=? AND status='SEARCHING'",(oid,))
        try: await bot.send_message(o["customer_tg_id"],f"⚠️ <b>Buyurtma #{oid}</b> uchun hozircha haydovchi topilmadi.")
        except: pass

@dp.callback_query(F.data.startswith("decline:"))
async def decline(c:CallbackQuery):
    oid=int(c.data.split(":")[1]); d=await driver(c.from_user.id)
    o=await q("SELECT status FROM orders WHERE id=?",(oid,),one=True)
    if not d or not d["approved"] or not o or o["status"]!="SEARCHING":
        await c.answer("Buyurtma endi mavjud emas.",show_alert=True); return
    await q("INSERT OR IGNORE INTO order_declines(order_id,driver_tg_id,created_at) VALUES(?,?,?)",(oid,c.from_user.id,now()))
    await c.answer("Rad etildi")
    try: await c.message.edit_reply_markup(reply_markup=None)
    except: pass

@dp.callback_query(F.data.startswith("claim:"))
async def claim(c:CallbackQuery):
    oid=int(c.data.split(":")[1]); tg=c.from_user.id
    async with DB_LOCK:
        d=db.execute("SELECT * FROM drivers WHERE tg_id=?",(tg,)).fetchone()
        o=db.execute("SELECT * FROM orders WHERE id=?",(oid,)).fetchone()
        if not d or not d["approved"] or not d["online"]: ok=False; reason="Avval admin tasdiqlashi va ONLINE bo‘lishingiz kerak."
        elif not o or o["status"]!="SEARCHING": ok=False; reason="Bu buyurtma boshqa haydovchi tomonidan olindi."
        elif o["claim_deadline"] and o["claim_deadline"]<=now(): ok=False; reason="Qabul qilish vaqti tugagan."
        elif d["active_orders"]>=4: ok=False; reason="Sizda 4 ta faol buyurtma bor."
        elif db.execute("SELECT 1 FROM order_declines WHERE order_id=? AND driver_tg_id=?",(oid,tg)).fetchone(): ok=False; reason="Siz bu buyurtmani oldin rad etgansiz."
        else:
            cur=db.execute("UPDATE orders SET driver_tg_id=?,status='ACCEPTED',accepted_at=? WHERE id=? AND status='SEARCHING'",(tg,now(),oid))
            if cur.rowcount!=1: ok=False; reason="Buyurtma allaqachon olindi."
            else:
                cur2=db.execute("UPDATE drivers SET active_orders=active_orders+1 WHERE tg_id=? AND active_orders<4 AND approved=1 AND online=1",(tg,))
                if cur2.rowcount!=1:
                    db.execute("UPDATE orders SET driver_tg_id=NULL,status='SEARCHING',accepted_at=NULL WHERE id=?",(oid,)); ok=False; reason="Faol buyurtma limiti tugadi."
                else: ok=True; reason=""
            db.commit()
    if not ok:
        await c.answer(reason,show_alert=True); return
    o=await q("SELECT * FROM orders WHERE id=?",(oid,),one=True); cu=await user(o["customer_tg_id"]); d=await driver(tg)
    await c.answer("Buyurtma sizniki!")
    try: await c.message.edit_reply_markup(reply_markup=None)
    except: pass
    gps="" if o["origin_lat"] is None else "\n📍 GPS: Telegram Location yuborilgan"
    await c.message.answer(f"✅ <b>BUYURTMA #{oid} QABUL QILINDI</b>\n\n👤 Mijoz: <b>{escape(cu['name'])}</b>\n📞 {escape(cu['phone'])}\n📍 {escape(o['origin'])}\n🏁 {escape(o['destination'])}{gps}\n💰 {money(o['price'])}")
    try:
        await bot.send_message(o["customer_tg_id"],f"🚕 <b>Haydovchi topildi!</b>\n\n👤 {escape(d['full_name'])}\n📞 {escape(d['phone'])}\n🚗 {escape(d['car_model'])}\n🔢 {escape(d['plate'])}\n💰 {money(o['price'])}\n\n📍 {escape(o['origin'])} → {escape(o['destination'])}")
        if o["origin_lat"] is not None and o["origin_lon"] is not None:
            await bot.send_location(d["tg_id"], latitude=o["origin_lat"], longitude=o["origin_lon"])
    except Exception as e: log.warning("claim notification: %s", e)

# ---------------- DRIVER REGISTRATION ----------------

@dp.message(Command("driver"))
async def driver_cmd(message:Message,state:FSMContext): await driver_start(message,state)

@dp.message(F.text.in_({"🚕 Haydovchi bo‘lish","🚕 Ҳайдовчи бўлиш","🚕 Стать водителем","🚕 Become a driver"}))
async def driver_start(message:Message,state:FSMContext):
    u=await user(message.from_user.id)
    if u and u["blocked"]: await message.answer("🚫 Akkauntingiz bloklangan."); return
    d=await driver(message.from_user.id)
    if d:
        if d["approved"]: await message.answer("🚕 Siz tasdiqlangan haydovchisiz.",reply_markup=driver_panel_kb(d))
        else: await message.answer("⏳ Arizangiz admin tasdiqlashini kutmoqda.")
        return
    if not u:
        await q("INSERT INTO users(tg_id,role,lang,name,phone,created_at) VALUES(?,?,?,?,?,?)",(message.from_user.id,"driver","uz",message.from_user.full_name or "","",now()))
    await state.clear(); await state.set_state(DriverReg.name); await message.answer("1/8 👤 F.I.Sh.:")

@dp.message(DriverReg.name)
async def dr_name(m:Message,s:FSMContext):
    v=(m.text or "").strip()
    if len(v)<3: await m.answer("F.I.Sh. ni to‘liq kiriting."); return
    await s.update_data(full_name=v); await s.set_state(DriverReg.phone); await m.answer("2/8 📱 Telefon:",reply_markup=contact_kb("uz"))

@dp.message(DriverReg.phone,F.contact)
async def dr_phone_c(m:Message,s:FSMContext): await s.update_data(phone=m.contact.phone_number); await s.set_state(DriverReg.car); await m.answer("3/8 🚗 Avtomobil rusmi/modeli:")

@dp.message(DriverReg.phone)
async def dr_phone(m:Message,s:FSMContext):
    v=(m.text or "").strip()
    if len(re.sub(r"\D","",v))<7: await m.answer("To‘g‘ri telefon yuboring."); return
    await s.update_data(phone=v); await s.set_state(DriverReg.car); await m.answer("3/8 🚗 Avtomobil rusmi/modeli:")

@dp.message(DriverReg.car)
async def dr_car(m:Message,s:FSMContext):
    v=(m.text or "").strip()
    if len(v)<2: await m.answer("Avtomobil modelini kiriting."); return
    await s.update_data(car=v); await s.set_state(DriverReg.plate); await m.answer("4/8 🔢 Davlat raqami:")

@dp.message(DriverReg.plate)
async def dr_plate(m:Message,s:FSMContext):
    v=(m.text or "").strip().upper().replace(" ","")
    if len(v)<4: await m.answer("Davlat raqamini to‘g‘ri kiriting."); return
    x=await q("SELECT 1 FROM drivers WHERE plate=?",(v,),one=True)
    if x: await m.answer("❌ Bu raqam allaqachon ro‘yxatdan o‘tgan."); return
    await s.update_data(plate=v); await s.set_state(DriverReg.license); await m.answer("5/8 📄 Prava rasmini yuboring:")

@dp.message(DriverReg.license,F.photo)
async def dr_license(m:Message,s:FSMContext): await s.update_data(license=m.photo[-1].file_id); await s.set_state(DriverReg.tech); await m.answer("6/8 📄 Texpasport rasmini yuboring:")
@dp.message(DriverReg.license)
async def dr_license_bad(m:Message,s:FSMContext): await m.answer("📄 Prava rasmini yuboring.")
@dp.message(DriverReg.tech,F.photo)
async def dr_tech(m:Message,s:FSMContext): await s.update_data(tech=m.photo[-1].file_id); await s.set_state(DriverReg.photo); await m.answer("7/8 🚗 Avtomobil rasmini yuboring:")
@dp.message(DriverReg.tech)
async def dr_tech_bad(m:Message,s:FSMContext): await m.answer("📄 Texpasport rasmini yuboring.")
@dp.message(DriverReg.photo,F.photo)
async def dr_photo(m:Message,s:FSMContext):
    await s.update_data(car_photo=m.photo[-1].file_id); await s.set_state(DriverReg.rules)
    await m.answer("8/8 📋 Qoidalar:\n\n• Mijoz bilan odobli muomala.\n• Buyurtma qabul qilingach bog‘lanish.\n• Mijoz ma’lumotlarini tarqatmaslik.\n• Platforma qoidalariga rioya qilish.\n\nQabul qilsangiz <b>HA</b> deb yozing.")
@dp.message(DriverReg.photo)
async def dr_photo_bad(m:Message,s:FSMContext): await m.answer("🚗 Avtomobil rasmini yuboring.")

@dp.message(DriverReg.rules)
async def dr_rules(m:Message,s:FSMContext):
    if (m.text or "").strip().lower() not in {"ha","xa","yes","да","ҳа"}: await m.answer("Qoidalarni qabul qilish uchun HA deb yozing."); return
    d=await s.get_data(); tg=m.from_user.id
    if await driver(tg): await s.clear(); await m.answer("Ariza allaqachon mavjud."); return
    await q("""INSERT INTO drivers(tg_id,full_name,phone,car_model,plate,license_file_id,tech_file_id,car_photo_file_id,created_at) VALUES(?,?,?,?,?,?,?,?,?)""",
            (tg,d["full_name"],d["phone"],d["car"],d["plate"],d["license"],d["tech"],d["car_photo"],now()))
    await q("UPDATE users SET role='driver',name=?,phone=? WHERE tg_id=?",(d["full_name"],d["phone"],tg))
    await s.clear(); await m.answer("✅ <b>Arizangiz admin'ga yuborildi.</b>\n⏳ Tasdiqlangandan keyin ONLINE bo‘lasiz.")
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ TASDIQLASH",callback_data=f"approve:{tg}"),InlineKeyboardButton(text="❌ RAD ETISH",callback_data=f"reject:{tg}")]])
    try:
        await bot.send_message(ADMIN_ID,f"🚕 <b>YANGI HAYDOVCHI</b>\n\n👤 {escape(d['full_name'])}\n📞 {escape(d['phone'])}\n🚗 {escape(d['car'])}\n🔢 {escape(d['plate'])}\n📍 OBLIQ ↔ ANGREN\n🆔 {tg}",reply_markup=kb)
        for cap,fid in [("📄 PRAVA",d["license"]),("📄 TEX PASPORT",d["tech"]),("🚗 AVTOMOBIL",d["car_photo"])]: await bot.send_photo(ADMIN_ID,fid,caption=cap)
    except Exception as e: log.warning("admin driver notify: %s",e)

@dp.callback_query(F.data.startswith("approve:"))
async def approve(c:CallbackQuery):
    if c.from_user.id!=ADMIN_ID: await c.answer("Ruxsat yo‘q",show_alert=True); return
    tg=int(c.data.split(":")[1]); d=await driver(tg)
    if not d: await c.answer("Haydovchi topilmadi",show_alert=True); return
    await q("UPDATE drivers SET approved=1,online=0 WHERE tg_id=?",(tg,)); await q("UPDATE users SET role='driver' WHERE tg_id=?",(tg,)); await audit("DRIVER_APPROVED",str(tg))
    await c.answer("Tasdiqlandi");
    try: await c.message.edit_reply_markup(reply_markup=None)
    except: pass
    try: await bot.send_message(tg,"🎉 <b>Arizangiz tasdiqlandi!</b>\nEndi ONLINE bo‘lib buyurtma olishingiz mumkin.",reply_markup=driver_panel_kb(await driver(tg)))
    except: pass

@dp.callback_query(F.data.startswith("reject:"))
async def reject(c:CallbackQuery):
    if c.from_user.id!=ADMIN_ID: await c.answer("Ruxsat yo‘q",show_alert=True); return
    tg=int(c.data.split(":")[1]); d=await driver(tg)
    if not d: await c.answer("Topilmadi",show_alert=True); return
    await q("DELETE FROM drivers WHERE tg_id=? AND approved=0",(tg,)); await audit("DRIVER_REJECTED",str(tg)); await c.answer("Rad etildi")
    try: await c.message.edit_reply_markup(reply_markup=None); await bot.send_message(tg,"❌ Haydovchi arizasi rad etildi.")
    except: pass

# ---------------- DRIVER PANEL ----------------

@dp.message(F.text.in_({"🟢 ONLINE","🔴 OFFLINE"}))
async def toggle(m:Message):
    d=await driver(m.from_user.id)
    if not d: return
    if not d["approved"]: await m.answer("⏳ Admin tasdiqlashi kerak."); return
    val=0 if d["online"] else 1
    await q("UPDATE drivers SET online=? WHERE tg_id=?",(val,m.from_user.id)); d=await driver(m.from_user.id)
    await m.answer("🟢 ONLINE" if val else "🔴 OFFLINE",reply_markup=driver_panel_kb(d))

@dp.message(F.text.startswith("🚕 FAOL BUYURTMALAR"))
async def active_orders(m:Message):
    d=await driver(m.from_user.id)
    if not d or not d["approved"]: return
    rows=await q("SELECT * FROM orders WHERE driver_tg_id=? AND status='ACCEPTED' ORDER BY id DESC",(m.from_user.id,),many=True)
    if not rows: await m.answer("📭 Faol buyurtma yo‘q."); return
    for o in rows:
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📞 BOG‘LANISH",callback_data=f"contact:{o['id']}")],[InlineKeyboardButton(text="📵 MIJOZ JAVOB BERMADI",callback_data=f"noanswer:{o['id']}")],[InlineKeyboardButton(text="🏁 BUYURTMANI YAKUNLASH",callback_data=f"finish:{o['id']}")]])
        cu=await user(o["customer_tg_id"])
        await m.answer(f"🚕 <b>BUYURTMA #{o['id']}</b>\n👤 {escape(cu['name'])}\n📞 {escape(cu['phone'])}\n📍 {escape(o['origin'])}\n🏁 {escape(o['destination'])}\n💰 {money(o['price'])}",reply_markup=kb)

@dp.callback_query(F.data.startswith("contact:"))
async def contact(c:CallbackQuery):
    oid=int(c.data.split(":")[1]); o=await q("SELECT * FROM orders WHERE id=?",(oid,),one=True)
    if not o or o["driver_tg_id"]!=c.from_user.id: await c.answer("Ruxsat yo‘q",show_alert=True); return
    await c.answer("Mijoz bilan bog‘laning."); await c.message.answer("📞 Mijoz raqami: "+escape((await user(o["customer_tg_id"]))["phone"]))

@dp.callback_query(F.data.startswith("noanswer:"))
async def noanswer(c:CallbackQuery,state:FSMContext):
    oid=int(c.data.split(":")[1]); o=await q("SELECT * FROM orders WHERE id=?",(oid,),one=True)
    if not o or o["driver_tg_id"]!=c.from_user.id or o["status"]!="ACCEPTED": await c.answer("Ruxsat yo‘q",show_alert=True); return
    await state.update_data(order_id=oid,driver_id=c.from_user.id); await state.set_state(NoAnswer.answer); await c.answer()
    await bot.send_message(o["customer_tg_id"],f"🚕 <b>Haydovchi siz bilan bog‘lana olmadi.</b>\n\nSizga hali ham mashina kerakmi?\n⏱ 1 daqiqa ichida javob bering.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ HA, KERAK",callback_data=f"needyes:{oid}"),InlineKeyboardButton(text="❌ YO‘Q, KERAK EMAS",callback_data=f"needno:{oid}")]]))
    asyncio.create_task(noanswer_timeout(oid,c.from_user.id))
    await c.message.edit_reply_markup(reply_markup=None)

async def noanswer_timeout(oid,old_driver):
    await asyncio.sleep(60)
    o=await q("SELECT * FROM orders WHERE id=?",(oid,),one=True)
    if o and o["status"]=="ACCEPTED" and o["driver_tg_id"]==old_driver:
        await q("UPDATE orders SET status='SEARCHING',driver_tg_id=NULL,accepted_at=NULL WHERE id=?",(oid,))
        await q("UPDATE drivers SET active_orders=CASE WHEN active_orders>0 THEN active_orders-1 ELSE 0 END WHERE tg_id=?",(old_driver,))
        try: await bot.send_message(o["customer_tg_id"],"⏱ Javob kelmadi. Buyurtma bekor qilindi.")
        except: pass
        asyncio.create_task(dispatch_excluding(oid,old_driver))

@dp.callback_query(F.data.startswith("needyes:"))
async def need_yes(c:CallbackQuery):
    oid=int(c.data.split(":")[1]); o=await q("SELECT * FROM orders WHERE id=?",(oid,),one=True)
    if not o: await c.answer("Buyurtma topilmadi",show_alert=True); return
    if o["status"]=="ACCEPTED":
        old=o["driver_tg_id"]; await q("UPDATE orders SET status='SEARCHING',driver_tg_id=NULL,accepted_at=NULL WHERE id=?",(oid,)); await q("UPDATE drivers SET active_orders=CASE WHEN active_orders>0 THEN active_orders-1 ELSE 0 END WHERE tg_id=?",(old,))
        await c.answer("Qayta qidirilmoqda"); asyncio.create_task(dispatch_excluding(oid,old))
    else: await c.answer("Buyurtma allaqachon qayta ishlanmoqda.")

@dp.callback_query(F.data.startswith("needno:"))
async def need_no(c:CallbackQuery):
    oid=int(c.data.split(":")[1]); o=await q("SELECT * FROM orders WHERE id=?",(oid,),one=True)
    if o and o["status"]=="ACCEPTED":
        old=o["driver_tg_id"]; await q("UPDATE orders SET status='CANCELLED',driver_tg_id=NULL WHERE id=?",(oid,)); await q("UPDATE drivers SET active_orders=CASE WHEN active_orders>0 THEN active_orders-1 ELSE 0 END WHERE tg_id=?",(old,))
    await c.answer("Bekor qilindi"); await c.message.edit_reply_markup(reply_markup=None)

async def dispatch_excluding(oid,excluded):
    for _ in range(2):
        o=await q("SELECT * FROM orders WHERE id=?",(oid,),one=True)
        if not o or o["status"]!="SEARCHING": return
        ds=await q("SELECT d.* FROM drivers d WHERE d.approved=1 AND d.online=1 AND d.active_orders<4 AND d.tg_id!=? AND NOT EXISTS(SELECT 1 FROM order_declines x WHERE x.order_id=? AND x.driver_tg_id=d.tg_id) ORDER BY d.active_orders,d.id",(excluded,oid),many=True)
        for d in ds:
            try: await bot.send_message(d["tg_id"],f"🚕 <b>YANGI BUYURTMA #{oid}</b>\n📍 {escape(o['origin'])}\n🏁 {escape(o['destination'])}\n💰 {money(o['price'])}",reply_markup=driver_offer_kb(oid))
            except: pass
        await asyncio.sleep(30)
    o=await q("SELECT * FROM orders WHERE id=?",(oid,),one=True)
    if o and o["status"]=="SEARCHING": await q("UPDATE orders SET status='NO_DRIVER' WHERE id=?",(oid,)); await bot.send_message(o["customer_tg_id"],f"⚠️ Buyurtma #{oid} uchun boshqa haydovchi topilmadi.")

@dp.callback_query(F.data.startswith("finish:"))
async def finish(c:CallbackQuery):
    oid=int(c.data.split(":")[1]); o=await q("SELECT * FROM orders WHERE id=?",(oid,),one=True)
    if not o or o["driver_tg_id"]!=c.from_user.id: await c.answer("Ruxsat yo‘q",show_alert=True); return
    if o["status"]!="ACCEPTED": await c.answer("Buyurtma allaqachon yakunlangan."); return
    await q("UPDATE orders SET status='FINISHED',finished_at=? WHERE id=? AND status='ACCEPTED'",(now(),oid)); await q("UPDATE drivers SET active_orders=CASE WHEN active_orders>0 THEN active_orders-1 ELSE 0 END WHERE tg_id=?",(c.from_user.id,))
    await c.answer("Yakunlandi"); await c.message.edit_reply_markup(reply_markup=None)
    await bot.send_message(o["customer_tg_id"],f"✅ <b>Buyurtma #{oid} yakunlandi.</b>\n\n⭐ Haydovchini baholang:",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=str(i),callback_data=f"rate:{oid}:{i}") for i in range(1,6)]]))

@dp.callback_query(F.data.startswith("rate:"))
async def rate(c:CallbackQuery):
    _,oid_s,score_s=c.data.split(":"); oid=int(oid_s); score=int(score_s); o=await q("SELECT * FROM orders WHERE id=?",(oid,),one=True)
    if not o or o["customer_tg_id"]!=c.from_user.id or not o["driver_tg_id"]: await c.answer("Ruxsat yo‘q",show_alert=True); return
    try: await q("INSERT INTO ratings(order_id,from_tg_id,to_tg_id,score,created_at) VALUES(?,?,?,?,?)",(oid,c.from_user.id,o["driver_tg_id"],score,now()))
    except sqlite3.IntegrityError: await c.answer("Allaqachon baholangan."); return
    d=await driver(o["driver_tg_id"]); count=d["rating_count"]+1; avg=((d["rating"]*d["rating_count"])+score)/count
    await q("UPDATE drivers SET rating=?,rating_count=? WHERE tg_id=?",(avg,count,o["driver_tg_id"])); await c.answer("Rahmat!"); await c.message.edit_text(f"⭐ Baho: {score}/5")

# ---------------- DRIVER INFO ----------------

@dp.message(F.text=="📜 BUYURTMALAR TARIXI")
async def driver_history(m:Message):
    rows=await q("SELECT * FROM orders WHERE driver_tg_id=? ORDER BY id DESC LIMIT 20",(m.from_user.id,),many=True)
    await m.answer("📜 <b>BUYURTMALAR TARIXI</b>\n\n"+"\n".join(f"#{x['id']} | {x['origin']} → {x['destination']} | {money(x['price'])} | {x['status']}" for x in rows) if rows else "📭 Tarix bo‘sh.")

@dp.message(F.text=="💰 DAROMAD")
async def income(m:Message):
    r=await q("SELECT COUNT(*) c,COALESCE(SUM(price),0) total FROM orders WHERE driver_tg_id=? AND status='FINISHED'",(m.from_user.id,),one=True)
    await m.answer(f"💰 <b>DAROMAD</b>\n\n🚕 Buyurtmalar: {r['c']}\n💵 Jami: <b>{money(r['total'])}</b>")

@dp.message(F.text=="⭐ REYTING")
async def rating_info(m:Message):
    d=await driver(m.from_user.id)
    if d: await m.answer(f"⭐ Reyting: <b>{d['rating']:.2f}/5</b>\nBaholar: {d['rating_count']}")

@dp.message(F.text=="👤 PROFIL")
async def driver_profile(m:Message):
    d=await driver(m.from_user.id)
    if d: await m.answer(f"👤 <b>HAYDOVCHI</b>\n\n{escape(d['full_name'])}\n📞 {escape(d['phone'])}\n🚗 {escape(d['car_model'])}\n🔢 {escape(d['plate'])}\n📍 OBLIQ ↔ ANGREN\n⭐ {d['rating']:.2f}")

# ---------------- CUSTOMER PROFILE / HISTORY / SUPPORT ----------------

@dp.message(F.text.in_({"👤 Profil","👤 Профиль","👤 Profile"}))
async def profile(m:Message):
    u=await user(m.from_user.id)
    if u: await m.answer(f"👤 <b>PROFIL</b>\n\n{escape(u['name'])}\n📞 {escape(u['phone'])}\n🌐 {u['lang']}")

@dp.message(F.text.in_({"📜 Tarix","📜 Тарих","📜 История","📜 History"}))
async def history(m:Message):
    rows=await q("SELECT * FROM orders WHERE customer_tg_id=? ORDER BY id DESC LIMIT 20",(m.from_user.id,),many=True)
    if not rows: await m.answer("📭 Tarix bo‘sh."); return
    await m.answer("📜 <b>TARIX</b>\n\n"+"\n".join(f"#{x['id']} | {x['origin']} → {x['destination']} | {money(x['price'])} | {x['status']}" for x in rows))

@dp.message(F.text.in_({"📩 Murojaat","📩 Мурожаат","📩 Поддержка","📩 Support"}))
async def support_start(m:Message,s:FSMContext): await s.set_state(Support.text); await m.answer("📩 Murojaatni yozing:")

@dp.message(Support.text)
async def support_save(m:Message,s:FSMContext):
    text=(m.text or "").strip()
    if len(text)<3: await m.answer("Murojaatni batafsilroq yozing."); return
    tid=await q("INSERT INTO support_tickets(tg_id,text,created_at) VALUES(?,?,?)",(m.from_user.id,text,now())); await s.clear(); await m.answer(f"✅ Murojaat qabul qilindi. №{tid}")
    try: await bot.send_message(ADMIN_ID,f"📩 <b>MUROJAAT #{tid}</b>\n👤 {m.from_user.id}\n\n{escape(text)}")
    except: pass

# ---------------- ADMIN ----------------

async def admin_stats_text():
    u=await q("SELECT COUNT(*) c FROM users",one=True); d=await q("SELECT COUNT(*) c FROM drivers",one=True); p=await q("SELECT COUNT(*) c FROM drivers WHERE approved=0",one=True); on=await q("SELECT COUNT(*) c FROM drivers WHERE approved=1 AND online=1",one=True); bl=await q("SELECT COUNT(*) c FROM users WHERE blocked=1",one=True); o=await q("SELECT COUNT(*) c FROM orders",one=True); active=await q("SELECT COUNT(*) c FROM orders WHERE status IN ('SEARCHING','ACCEPTED')",one=True); fin=await q("SELECT COUNT(*) c FROM orders WHERE status='FINISHED'",one=True); rev=await q("SELECT COALESCE(SUM(price),0) s FROM orders WHERE status='FINISHED' AND date(created_at)=date('now')",one=True)
    return f"👨‍💼 <b>TAXI BOR MI? — ADMIN PANEL</b>\n\n📊 <b>STATISTIKA</b>\n👥 Mijozlar: {u['c']}\n🚕 Haydovchilar: {d['c']}\n⏳ Tasdiqlash kutilmoqda: {p['c']}\n🟢 Online: {on['c']}\n🚫 Bloklangan: {bl['c']}\n📦 Jami buyurtmalar: {o['c']}\n🔥 Faol: {active['c']}\n✅ Yakunlangan: {fin['c']}\n💰 Bugungi aylanma: {money(rev['s'])}"

@dp.message(Command("admin"))
async def admin(m:Message):
    if m.from_user.id!=ADMIN_ID: await m.answer("🚫 Ruxsat yo‘q."); return
    await m.answer(await admin_stats_text(),reply_markup=admin_kb())

@dp.callback_query(F.data.startswith("adm:"))
async def admin_menu(c:CallbackQuery):
    if c.from_user.id!=ADMIN_ID: await c.answer("Ruxsat yo‘q",show_alert=True); return
    act=c.data.split(":")[1]; await c.answer()
    if act=="stats": text=await admin_stats_text(); await c.message.edit_text(text,reply_markup=admin_kb()); return
    if act=="pending":
        rows=await q("SELECT * FROM drivers WHERE approved=0 ORDER BY id DESC LIMIT 30",many=True)
        if not rows: await c.message.edit_text("⏳ Tasdiqlash kutilayotgan haydovchi yo‘q.",reply_markup=admin_kb()); return
        for d in rows:
            kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ TASDIQLASH",callback_data=f"approve:{d['tg_id']}"),InlineKeyboardButton(text="❌ RAD ETISH",callback_data=f"reject:{d['tg_id']}")]])
            await c.message.answer(f"🚕 <b>{escape(d['full_name'])}</b>\n📞 {escape(d['phone'])}\n🚗 {escape(d['car_model'])}\n🔢 {escape(d['plate'])}\n🆔 {d['tg_id']}",reply_markup=kb)
        return
    if act=="drivers":
        rows=await q("SELECT * FROM drivers ORDER BY id DESC LIMIT 30",many=True); text="🚕 <b>HAYDOVCHILAR</b>\n\n"+"\n".join(f"{x['full_name']} | {x['plate']} | {'✅' if x['approved'] else '⏳'} | {'🟢' if x['online'] else '⚪'}" for x in rows); await c.message.edit_text(text or "Haydovchilar yo‘q.",reply_markup=admin_kb()); return
    if act=="users":
        rows=await q("SELECT * FROM users ORDER BY id DESC LIMIT 30",many=True); text="👥 <b>MIJOZLAR</b>\n\n"+"\n".join(f"{x['name']} | {x['phone']} | {'🚫' if x['blocked'] else '✅'}" for x in rows); await c.message.edit_text(text or "Mijozlar yo‘q.",reply_markup=admin_kb()); return
    if act=="orders":
        rows=await q("SELECT * FROM orders ORDER BY id DESC LIMIT 30",many=True); text="📦 <b>BUYURTMALAR</b>\n\n"+"\n".join(f"#{x['id']} | {x['origin']} → {x['destination']} | {money(x['price'])} | {x['status']}" for x in rows); await c.message.edit_text(text or "Buyurtmalar yo‘q.",reply_markup=admin_kb()); return
    if act=="online":
        rows=await q("SELECT * FROM drivers WHERE approved=1 AND online=1",many=True); text="🟢 <b>ONLINE HAYDOVCHILAR</b>\n\n"+"\n".join(f"{x['full_name']} | {x['plate']} | {x['active_orders']}/4" for x in rows); await c.message.edit_text(text or "Online haydovchi yo‘q.",reply_markup=admin_kb()); return
    if act=="blocked":
        rows=await q("SELECT * FROM users WHERE blocked=1",many=True); text="🚫 <b>BLOKLANGANLAR</b>\n\n"+"\n".join(f"{x['name']} | {x['tg_id']}" for x in rows); await c.message.edit_text(text or "Bloklanganlar yo‘q.",reply_markup=admin_kb()); return
    if act=="ratings":
        rows=await q("SELECT full_name,rating,rating_count FROM drivers WHERE rating_count>0 ORDER BY rating DESC",many=True); text="⭐ <b>REYTINGLAR</b>\n\n"+"\n".join(f"{x['full_name']} — {x['rating']:.2f} ({x['rating_count']})" for x in rows); await c.message.edit_text(text or "Reyting yo‘q.",reply_markup=admin_kb()); return
    if act=="support":
        rows=await q("SELECT * FROM support_tickets WHERE status='OPEN' ORDER BY id DESC LIMIT 30",many=True); text="📩 <b>OCHIQ MUROJAATLAR</b>\n\n"+"\n".join(f"#{x['id']} | {x['tg_id']} | {x['text']}" for x in rows); await c.message.edit_text(text or "Ochiq murojaat yo‘q.",reply_markup=admin_kb()); return

@dp.message(Command("admin_pending"))
async def admin_pending_cmd(m:Message):
    if m.from_user.id!=ADMIN_ID:return
    rows=await q("SELECT * FROM drivers WHERE approved=0 ORDER BY id DESC",many=True)
    if not rows: await m.answer("⏳ Kutilayotgan ariza yo‘q."); return
    for d in rows: await m.answer(f"🚕 {d['full_name']} | {d['plate']} | {d['tg_id']}",reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ TASDIQLASH",callback_data=f"approve:{d['tg_id']}"),InlineKeyboardButton(text="❌ RAD ETISH",callback_data=f"reject:{d['tg_id']}")]]))

@dp.message(Command("admin_block"))
async def admin_block(m:Message):
    if m.from_user.id!=ADMIN_ID:return
    p=(m.text or "").split();
    if len(p)!=2 or not p[1].isdigit(): await m.answer("/admin_block TELEGRAM_ID"); return
    tg=int(p[1]); await q("UPDATE users SET blocked=1 WHERE tg_id=?",(tg,)); await q("UPDATE drivers SET online=0 WHERE tg_id=?",(tg,)); await audit("BLOCK",str(tg)); await m.answer("🚫 Bloklandi.")

@dp.message(Command("admin_unblock"))
async def admin_unblock(m:Message):
    if m.from_user.id!=ADMIN_ID:return
    p=(m.text or "").split();
    if len(p)!=2 or not p[1].isdigit(): await m.answer("/admin_unblock TELEGRAM_ID"); return
    tg=int(p[1]); await q("UPDATE users SET blocked=0 WHERE tg_id=?",(tg,)); await audit("UNBLOCK",str(tg)); await m.answer("✅ Blokdan chiqarildi.")

# ---------------- FALLBACK / MAIN ----------------

@dp.message(F.text=="⬅️ Orqaga")
async def back(m:Message,s:FSMContext):
    await s.clear(); u=await user(m.from_user.id)
    if u: await m.answer("Xizmatni tanlang:",reply_markup=main_kb(u["lang"]))

@dp.message()
async def fallback(m:Message):
    u=await user(m.from_user.id)
    if not u: await m.answer("Avval /start bosing."); return
    await m.answer("🤖 Menyudan foydalaning.",reply_markup=main_kb(u["lang"]))

# ---------------- START ----------------

async def main():
    init_db()
    log.info("TAXI BOR MI? starting...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__=="__main__":
    asyncio.run(main())
