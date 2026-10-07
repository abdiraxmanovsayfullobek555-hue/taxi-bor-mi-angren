import asyncio
import json
import logging
import os
import re
import sqlite3
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
DB_PATH = os.getenv("DB_PATH", "taxi.db")
MAX_ACTIVE_ORDERS = 4
NO_ANSWER_TIMEOUT = 60

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("taxi_bot")


def now():
    return datetime.utcnow().isoformat(timespec="seconds")


class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.init()

    def init(self):
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                tg_id INTEGER PRIMARY KEY,
                role TEXT NOT NULL DEFAULT 'customer',
                lang TEXT NOT NULL DEFAULT 'uz',
                name TEXT,
                phone TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS drivers (
                tg_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                phone TEXT NOT NULL,
                car_model TEXT NOT NULL,
                plate TEXT NOT NULL,
                car_photo TEXT NOT NULL,
                passport_photo TEXT NOT NULL,
                license_photo TEXT NOT NULL,
                approved INTEGER NOT NULL DEFAULT 0,
                blocked INTEGER NOT NULL DEFAULT 0,
                online INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL,
                driver_id INTEGER,
                origin TEXT NOT NULL,
                destination TEXT NOT NULL,
                latitude REAL,
                longitude REAL,
                service TEXT NOT NULL,
                passengers INTEGER,
                price INTEGER NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                accepted_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS offers (
                order_id INTEGER NOT NULL,
                driver_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'sent',
                sent_at TEXT NOT NULL,
                PRIMARY KEY(order_id, driver_id),
                FOREIGN KEY(order_id) REFERENCES orders(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
            CREATE INDEX IF NOT EXISTS idx_orders_driver_status ON orders(driver_id, status);
            CREATE INDEX IF NOT EXISTS idx_offers_driver_status ON offers(driver_id, status);
            """
        )
        self.conn.commit()

    def user(self, tg_id):
        return self.conn.execute("SELECT * FROM users WHERE tg_id=?", (tg_id,)).fetchone()

    def ensure_user(self, tg_id, role="customer"):
        self.conn.execute(
            "INSERT OR IGNORE INTO users(tg_id, role, created_at) VALUES(?,?,?)",
            (tg_id, role, now()),
        )
        self.conn.commit()

    def set_user(self, tg_id, **fields):
        if not fields:
            return
        self.ensure_user(tg_id)
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = list(fields.values()) + [tg_id]
        self.conn.execute(f"UPDATE users SET {cols} WHERE tg_id=?", vals)
        self.conn.commit()

    def driver(self, tg_id):
        return self.conn.execute("SELECT * FROM drivers WHERE tg_id=?", (tg_id,)).fetchone()

    def save_driver(self, tg_id, **d):
        keys = ["name", "phone", "car_model", "plate", "car_photo", "passport_photo", "license_photo"]
        vals = [d[k] for k in keys]
        self.conn.execute(
            """INSERT INTO drivers(tg_id,name,phone,car_model,plate,car_photo,passport_photo,license_photo,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(tg_id) DO UPDATE SET
               name=excluded.name, phone=excluded.phone, car_model=excluded.car_model,
               plate=excluded.plate, car_photo=excluded.car_photo,
               passport_photo=excluded.passport_photo, license_photo=excluded.license_photo,
               approved=0, blocked=0, online=0""",
            [tg_id] + vals + [now()],
        )
        self.conn.commit()

    def create_order(self, **o):
        cur = self.conn.execute(
            """INSERT INTO orders(customer_id,origin,destination,latitude,longitude,service,passengers,price,status,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (o["customer_id"], o["origin"], o["destination"], o["latitude"], o["longitude"],
             o["service"], o["passengers"], o["price"], "searching", now(), now()),
        )
        self.conn.commit()
        return cur.lastrowid

    def order(self, order_id):
        return self.conn.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()

    def active_driver_count(self, driver_id):
        row = self.conn.execute(
            "SELECT COUNT(*) c FROM orders WHERE driver_id=? AND status IN ('accepted','in_progress')",
            (driver_id,),
        ).fetchone()
        return row["c"]

    def available_drivers(self):
        return self.conn.execute(
            "SELECT * FROM drivers WHERE approved=1 AND blocked=0 AND online=1"
        ).fetchall()

    def add_offer(self, order_id, driver_id):
        self.conn.execute(
            "INSERT OR IGNORE INTO offers(order_id,driver_id,status,sent_at) VALUES(?,?,?,?)",
            (order_id, driver_id, "sent", now()),
        )
        self.conn.commit()

    def offer(self, order_id, driver_id):
        return self.conn.execute(
            "SELECT * FROM offers WHERE order_id=? AND driver_id=?", (order_id, driver_id)
        ).fetchone()

    def set_offer(self, order_id, driver_id, status):
        self.conn.execute(
            "UPDATE offers SET status=? WHERE order_id=? AND driver_id=?",
            (status, order_id, driver_id),
        )
        self.conn.commit()

    def accept_order(self, order_id, driver_id):
        # SQLite transaction prevents two drivers from accepting the same order.
        cur = self.conn.cursor()
        try:
            cur.execute("BEGIN IMMEDIATE")
            order = cur.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
            driver = cur.execute("SELECT * FROM drivers WHERE tg_id=?", (driver_id,)).fetchone()
            if not order or order["status"] != "searching":
                self.conn.rollback()
                return False, "closed"
            if not driver or not driver["approved"] or driver["blocked"] or not driver["online"]:
                self.conn.rollback()
                return False, "driver"
            count = cur.execute(
                "SELECT COUNT(*) c FROM orders WHERE driver_id=? AND status IN ('accepted','in_progress')",
                (driver_id,),
            ).fetchone()["c"]
            if count >= MAX_ACTIVE_ORDERS:
                self.conn.rollback()
                return False, "limit"
            cur.execute(
                "UPDATE orders SET driver_id=?, status='accepted', accepted_at=?, updated_at=? WHERE id=? AND status='searching'",
                (driver_id, now(), now(), order_id),
            )
            if cur.rowcount != 1:
                self.conn.rollback()
                return False, "closed"
            cur.execute("UPDATE offers SET status='closed' WHERE order_id=?", (order_id,))
            cur.execute(
                "UPDATE offers SET status='accepted' WHERE order_id=? AND driver_id=?",
                (order_id, driver_id),
            )
            self.conn.commit()
            return True, "ok"
        except Exception:
            self.conn.rollback()
            raise

    def mark_offer_no_answer(self, order_id, driver_id):
        self.set_offer(order_id, driver_id, "no_answer")

    def finish_order(self, order_id, driver_id):
        cur = self.conn.execute(
            "UPDATE orders SET status='completed', updated_at=? WHERE id=? AND driver_id=? AND status IN ('accepted','in_progress')",
            (now(), order_id, driver_id),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def cancel_order(self, order_id, customer_id=None):
        if customer_id is None:
            cur = self.conn.execute(
                "UPDATE orders SET status='cancelled', updated_at=? WHERE id=? AND status NOT IN ('completed','cancelled')",
                (now(), order_id),
            )
        else:
            cur = self.conn.execute(
                "UPDATE orders SET status='cancelled', updated_at=? WHERE id=? AND customer_id=? AND status NOT IN ('completed','cancelled')",
                (now(), order_id, customer_id),
            )
        self.conn.commit()
        return cur.rowcount == 1

    def reopen_order(self, order_id):
        cur = self.conn.execute(
            "UPDATE orders SET status='searching', driver_id=NULL, accepted_at=NULL, updated_at=? WHERE id=? AND status='waiting_customer'",
            (now(), order_id),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def set_waiting_customer(self, order_id):
        cur = self.conn.execute(
            "UPDATE orders SET status='waiting_customer', updated_at=? WHERE id=? AND status='accepted'",
            (now(), order_id),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def list_orders(self, limit=30):
        return self.conn.execute("SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def stats(self):
        q = lambda sql: self.conn.execute(sql).fetchone()[0]
        return {
            "customers": q("SELECT COUNT(*) FROM users WHERE role='customer'"),
            "drivers": q("SELECT COUNT(*) FROM drivers"),
            "online": q("SELECT COUNT(*) FROM drivers WHERE approved=1 AND blocked=0 AND online=1"),
            "today": q("SELECT COUNT(*) FROM orders WHERE date(created_at)=date('now')"),
            "active": q("SELECT COUNT(*) FROM orders WHERE status IN ('searching','accepted','in_progress','waiting_customer')"),
            "completed": q("SELECT COUNT(*) FROM orders WHERE status='completed'"),
            "cancelled": q("SELECT COUNT(*) FROM orders WHERE status='cancelled'"),
        }

    def pending_drivers(self):
        return self.conn.execute("SELECT * FROM drivers WHERE approved=0 AND blocked=0 ORDER BY created_at").fetchall()

    def set_driver_approval(self, tg_id, approved, blocked=False):
        self.conn.execute("UPDATE drivers SET approved=?, blocked=?, online=0 WHERE tg_id=?", (int(approved), int(blocked), tg_id))
        self.conn.commit()

    def set_online(self, tg_id, value):
        self.conn.execute("UPDATE drivers SET online=? WHERE tg_id=? AND approved=1 AND blocked=0", (int(value), tg_id))
        self.conn.commit()

    def active_order_for_driver(self, order_id, driver_id):
        return self.conn.execute(
            "SELECT * FROM orders WHERE id=? AND driver_id=? AND status IN ('accepted','in_progress','waiting_customer')",
            (order_id, driver_id),
        ).fetchone()


db = DB(DB_PATH)

LANGS = {"uz": "🇺🇿 O‘zbekcha", "uzc": "🇺🇿 Ўзбекча", "ru": "🇷🇺 Русский", "en": "🇬🇧 English"}

T = {
    "uz": {
        "welcome": "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\nTilni tanlang:",
        "name": "Ismingizni kiriting:", "phone": "📞 Telefon raqamingizni yuboring:",
        "route": "📍 Qayerdan → Qayerga borasiz?\nMasalan: <b>Obliq → Angren hokimiyati</b>",
        "gps": "📍 Endi qayerdan jo‘nashingizni aniq ko‘rsatish uchun joylashuvingizni yuboring.",
        "people": "👥 Necha kishi?", "service": "Xizmat turini tanlang:",
        "price": "💰 Taklif qilinayotgan summani tanlang:",
        "confirm": "Buyurtmani tasdiqlaysizmi?", "created": "🚕 <b>Buyurtma #{}</b> tayyorlandi.\n\n⏳ Haydovchi qidirilmoqda...",
        "yes": "✅ HA", "no": "❌ YO‘Q", "cancel": "❌ Bekor qilish", "edit": "✏️ O‘zgartirish",
        "driver_found": "🚕 <b>Haydovchi topildi!</b>", "no_answer": "📵 JAVOB YO‘Q",
        "still_need": "🚕 Haydovchi siz bilan bog‘lana olmadi.\n\nSizga hali ham taksi kerakmi?\n\n⏱ 1 daqiqa ichida javob bering.",
        "expired": "⏰ Javob olinmadi. Buyurtma bekor qilindi.", "need_yes": "Buyurtma yana haydovchi qidirishga qaytarildi.",
        "need_no": "Buyurtma bekor qilindi.", "online": "🟢 ONLINE", "offline": "🔴 OFFLINE",
        "driver_menu": "🚕 Haydovchi paneli", "customer_menu": "👤 Mijoz paneli",
        "register_driver": "🚕 Haydovchi bo‘lish", "orders": "📋 Mening buyurtmalarim",
        "profile": "👤 Profil", "settings": "⚙️ Sozlamalar", "support": "📞 Yordam",
        "car_model": "🚗 Mashina rusumini kiriting:", "plate": "🔢 Mashina davlat raqamini kiriting:",
        "car_photo": "📸 Mashinaning rasmini yuboring:", "passport": "📄 Texpasport rasmini yuboring:",
        "license": "🪪 Prava rasmini yuboring:", "submitted": "✅ Ma’lumotlaringiz yuborildi. Admin tasdiqlashini kuting.",
        "approved": "✅ Siz tasdiqlandingiz. Haydovchi panelidan ONLINE bo‘lishingiz mumkin.",
        "not_approved": "⏳ Hali admin tasdiqlamagan.", "blocked": "🚫 Siz bloklangansiz.",
        "too_many": "⚠️ Sizda 4 ta faol buyurtma bor. Yangi buyurtma berilmaydi.",
        "accepted": "✅ Buyurtma #{}, sizga biriktirildi.", "rejected": "❌ Buyurtma rad etildi.",
        "completed": "✅ Buyurtma yakunlandi.", "not_found": "Buyurtma mavjud emas yoki allaqachon yopilgan.",
        "admin_only": "⛔ Admin uchun.", "route_bad": "Iltimos, <b>Qayerdan → Qayerga</b> ko‘rinishida yozing.",
    },
    "ru": {
        "welcome": "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\nВыберите язык:",
        "name": "Введите ваше имя:", "phone": "📞 Отправьте номер телефона:",
        "route": "📍 Откуда → Куда едете?\nНапример: <b>Облик → Ангрен хокимияти</b>",
        "gps": "📍 Теперь отправьте геолокацию точки отправления.", "people": "👥 Сколько пассажиров?",
        "service": "Выберите услугу:", "price": "💰 Выберите предложенную сумму:",
        "confirm": "Подтвердить заказ?", "created": "🚕 <b>Заказ #{}</b> создан.\n\n⏳ Ищем водителя...",
        "yes": "✅ ДА", "no": "❌ НЕТ", "cancel": "❌ Отмена", "edit": "✏️ Изменить",
        "driver_found": "🚕 <b>Водитель найден!</b>", "no_answer": "📵 НЕТ ОТВЕТА",
        "still_need": "🚕 Водитель не смог связаться с вами.\n\nВам ещё нужна машина?\n\n⏱ Ответьте в течение 1 минуты.",
        "expired": "⏰ Ответ не получен. Заказ отменён.", "need_yes": "Заказ снова отправлен на поиск водителя.",
        "need_no": "Заказ отменён.", "online": "🟢 ONLINE", "offline": "🔴 OFFLINE",
        "driver_menu": "🚕 Панель водителя", "customer_menu": "👤 Панель клиента", "register_driver": "🚕 Стать водителем",
        "orders": "📋 Мои заказы", "profile": "👤 Профиль", "settings": "⚙️ Настройки", "support": "📞 Помощь",
        "car_model": "🚗 Введите модель автомобиля:", "plate": "🔢 Введите госномер:",
        "car_photo": "📸 Отправьте фото автомобиля:", "passport": "📄 Отправьте фото техпаспорта:",
        "license": "🪪 Отправьте фото водительских прав:", "submitted": "✅ Данные отправлены. Ждите подтверждения администратора.",
        "approved": "✅ Вы подтверждены. Можно выйти ONLINE.", "not_approved": "⏳ Администратор ещё не подтвердил.",
        "blocked": "🚫 Вы заблокированы.", "too_many": "⚠️ У вас 4 активных заказа. Новые заказы не отправляются.",
        "accepted": "✅ Заказ #{} назначен вам.", "rejected": "❌ Заказ отклонён.", "completed": "✅ Заказ завершён.",
        "not_found": "Заказ не найден или уже закрыт.", "admin_only": "⛔ Только для администратора.",
        "route_bad": "Напишите в формате <b>Откуда → Куда</b>.",
    },
    "en": {
        "welcome": "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\nChoose language:",
        "name": "Enter your name:", "phone": "📞 Send your phone number:",
        "route": "📍 From → To?\nExample: <b>Obliq → Angren Hokimiyat</b>", "gps": "📍 Now send your pickup location.",
        "people": "👥 How many passengers?", "service": "Choose a service:", "price": "💰 Choose the offered price:",
        "confirm": "Confirm the order?", "created": "🚕 <b>Order #{}</b> created.\n\n⏳ Looking for a driver...",
        "yes": "✅ YES", "no": "❌ NO", "cancel": "❌ Cancel", "edit": "✏️ Edit",
        "driver_found": "🚕 <b>Driver found!</b>", "no_answer": "📵 NO ANSWER",
        "still_need": "🚕 The driver could not reach you.\n\nDo you still need a taxi?\n\n⏱ Reply within 1 minute.",
        "expired": "⏰ No reply received. The order was cancelled.", "need_yes": "The order is searching for another driver again.",
        "need_no": "The order was cancelled.", "online": "🟢 ONLINE", "offline": "🔴 OFFLINE",
        "driver_menu": "🚕 Driver panel", "customer_menu": "👤 Customer panel", "register_driver": "🚕 Become a driver",
        "orders": "📋 My orders", "profile": "👤 Profile", "settings": "⚙️ Settings", "support": "📞 Support",
        "car_model": "🚗 Enter the car model:", "plate": "🔢 Enter the plate number:",
        "car_photo": "📸 Send a photo of the car:", "passport": "📄 Send a photo of the vehicle registration:",
        "license": "🪪 Send a photo of the driving licence:", "submitted": "✅ Your data was sent. Wait for admin approval.",
        "approved": "✅ You are approved. You can go ONLINE.", "not_approved": "⏳ Admin has not approved you yet.",
        "blocked": "🚫 You are blocked.", "too_many": "⚠️ You have 4 active orders. No new orders will be sent.",
        "accepted": "✅ Order #{} was assigned to you.", "rejected": "❌ Order rejected.", "completed": "✅ Order completed.",
        "not_found": "Order not found or already closed.", "admin_only": "⛔ Admin only.", "route_bad": "Please use <b>From → To</b> format.",
    },
    "uzc": {
        "welcome": "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\nТилни танланг:",
        "name": "Исмингизни киритинг:", "phone": "📞 Телефон рақамингизни юборинг:",
        "route": "📍 Қаердан → Қаерга борасиз?\nМасалан: <b>Облиқ → Ангрен ҳокимияти</b>",
        "gps": "📍 Энди қаердан жўнашингизни аниқ кўрсатиш учун жойлашувингизни юборинг.", "people": "👥 Неча киши?",
        "service": "Хизматни танланг:", "price": "💰 Таклиф қилинган суммани танланг:",
        "confirm": "Буюртмани тасдиқлайсизми?", "created": "🚕 <b>Буюртма #{}</b> тайёрланди.\n\n⏳ Ҳайдовчи қидирилмоқда...",
        "yes": "✅ ҲА", "no": "❌ ЙЎҚ", "cancel": "❌ Бекор қилиш", "edit": "✏️ Ўзгартириш",
        "driver_found": "🚕 <b>Ҳайдовчи топилди!</b>", "no_answer": "📵 ЖАВОБ ЙЎҚ",
        "still_need": "🚕 Ҳайдовчи сиз билан боғлана олмади.\n\nСизга ҳали ҳам такси керакми?\n\n⏱ 1 дақиқа ичида жавоб беринг.",
        "expired": "⏰ Жавоб олинмади. Буюртма бекор қилинди.", "need_yes": "Буюртма яна ҳайдовчи қидиришга қайтарилди.",
        "need_no": "Буюртма бекор қилинди.", "online": "🟢 ONLINE", "offline": "🔴 OFFLINE",
        "driver_menu": "🚕 Ҳайдовчи панели", "customer_menu": "👤 Мижоз панели", "register_driver": "🚕 Ҳайдовчи бўлиш",
        "orders": "📋 Менинг буюртмаларим", "profile": "👤 Профиль", "settings": "⚙️ Созламалар", "support": "📞 Ёрдам",
        "car_model": "🚗 Машина русумини киритинг:", "plate": "🔢 Машина давлат рақамини киритинг:",
        "car_photo": "📸 Машина расмини юборинг:", "passport": "📄 Техпаспорт расмини юборинг:",
        "license": "🪪 Права расмини юборинг:", "submitted": "✅ Маълумотлар юборилди. Админ тасдиғини кутинг.",
        "approved": "✅ Сиз тасдиқландингиз. Ҳайдовчи панелида ONLINE бўлишингиз мумкин.", "not_approved": "⏳ Админ ҳали тасдиқламаган.",
        "blocked": "🚫 Сиз блоклангансиз.", "too_many": "⚠️ Сизда 4 та фаол буюртма бор. Янги буюртма берилмайди.",
        "accepted": "✅ Буюртма #{} сизга бириктирилди.", "rejected": "❌ Буюртма рад этилди.", "completed": "✅ Буюртма якунланди.",
        "not_found": "Буюртма мавжуд эмас ёки ёпилган.", "admin_only": "⛔ Фақат админ учун.", "route_bad": "Илтимос, <b>Қаердан → Қаерга</b> кўринишида ёзинг.",
    },
}


def lang(uid):
    row = db.user(uid)
    return row["lang"] if row and row["lang"] in T else "uz"


def tr(uid, key, *args):
    s = T[lang(uid)].get(key, T["uz"].get(key, key))
    return s.format(*args)


def main_menu(uid):
    l = lang(uid)
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text={"uz":"🚕 Taksi buyurtma qilish","ru":"🚕 Заказать такси","en":"🚕 Order Taxi","uzc":"🚕 Такси буюртма қилиш"}[l])],
            [KeyboardButton(text={"uz":"📦 Dastavka","ru":"📦 Доставка","en":"📦 Delivery","uzc":"📦 Даставка"}[l])],
            [KeyboardButton(text=T[l]["orders"]), KeyboardButton(text=T[l]["profile"])],
            [KeyboardButton(text=T[l]["settings"]), KeyboardButton(text=T[l]["support"])],
            [KeyboardButton(text=T[l]["register_driver"])],
        ], resize_keyboard=True,
    )


def lang_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=LANGS["uz"], callback_data="lang:uz")],
        [InlineKeyboardButton(text=LANGS["uzc"], callback_data="lang:uzc")],
        [InlineKeyboardButton(text=LANGS["ru"], callback_data="lang:ru")],
        [InlineKeyboardButton(text=LANGS["en"], callback_data="lang:en")],
    ])


def phone_kb(uid):
    l = lang(uid)
    text = {"uz":"📞 Raqamimni yuborish","ru":"📞 Отправить номер","en":"📞 Send my phone","uzc":"📞 Рақамимни юбориш"}[l]
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=text, request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)


def gps_kb(uid):
    l = lang(uid)
    text = {"uz":"📍 Joylashuvni yuborish","ru":"📍 Отправить геолокацию","en":"📍 Send location","uzc":"📍 Жойлашувни юбориш"}[l]
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=text, request_location=True)]], resize_keyboard=True, one_time_keyboard=True)


def people_kb(uid):
    l=lang(uid); labels={"uz":["1️⃣ 1 kishi","2️⃣ 2 kishi","3️⃣ 3 kishi","4️⃣ 4 kishi"],"ru":["1️⃣ 1 человек","2️⃣ 2 человека","3️⃣ 3 человека","4️⃣ 4 человека"],"en":["1️⃣ 1 passenger","2️⃣ 2 passengers","3️⃣ 3 passengers","4️⃣ 4 passengers"],"uzc":["1️⃣ 1 киши","2️⃣ 2 киши","3️⃣ 3 киши","4️⃣ 4 киши"]}[l]
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=labels[0]),KeyboardButton(text=labels[1])],[KeyboardButton(text=labels[2]),KeyboardButton(text=labels[3])],[KeyboardButton(text={"uz":"📦 Dastavka","ru":"📦 Доставка","en":"📦 Delivery","uzc":"📦 Даставка"}[l])]],resize_keyboard=True)


def price_kb(uid):
    l=lang(uid); return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="5 000 so‘m" if l in ("uz","uzc") else "5,000 UZS"),KeyboardButton(text="10 000 so‘m" if l in ("uz","uzc") else "10,000 UZS")],[KeyboardButton(text="15 000 so‘m" if l in ("uz","uzc") else "15,000 UZS"),KeyboardButton(text="20 000 so‘m" if l in ("uz","uzc") else "20,000 UZS")]],resize_keyboard=True)


def confirm_kb(uid, order_id):
    l=lang(uid); yes={"uz":"✅ BUYURTMA BERISH","ru":"✅ ЗАКАЗАТЬ","en":"✅ PLACE ORDER","uzc":"✅ БУЮРТМА БЕРИШ"}[l]; no=T[l]["cancel"]
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=yes,callback_data=f"confirm:{order_id}")],[InlineKeyboardButton(text=no,callback_data=f"cancel:{order_id}")]])


def driver_offer_kb(order_id, uid):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ BUYURTMANI OLISH", callback_data=f"accept:{order_id}")],
        [InlineKeyboardButton(text="❌ RAD ETISH", callback_data=f"reject:{order_id}")],
    ])


def accepted_kb(order_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📵 JAVOB YO‘Q", callback_data=f"noanswer:{order_id}")],
        [InlineKeyboardButton(text="✅ BUYURTMA YAKUNLANDI", callback_data=f"complete:{order_id}")],
    ])


class Customer(StatesGroup):
    name=State(); phone=State(); route=State(); gps=State(); service=State(); price=State()

class Driver(StatesGroup):
    name=State(); phone=State(); car_model=State(); plate=State(); car_photo=State(); passport=State(); license=State()


def parse_route(text):
    text=text.strip()
    for sep in ("→","->","—"):
        if sep in text:
            a,b=[x.strip() for x in text.split(sep,1)]
            if a and b: return a,b
    m=re.match(r"^(.+?)\s+dan\s+(.+?)\s+ga\s*$",text,re.I)
    if m: return m.group(1).strip(),m.group(2).strip()
    return None


def price_value(text):
    digits=re.sub(r"\D","",text)
    return int(digits) if digits in {"5000","10000","15000","20000"} else None


def people_value(uid,text):
    l=lang(uid)
    if "Dastavka" in text or "Доставка" in text or "Delivery" in text or "Даставка" in text: return "delivery",None
    m=re.match(r"([1-4])",text.strip())
    if m: return "passenger",int(m.group(1))
    return None,None

async def notify_admin(bot, text, kb=None):
    if ADMIN_ID:
        try: await bot.send_message(ADMIN_ID,text,reply_markup=kb)
        except Exception: log.exception("admin notify failed")

async def dispatch(bot, order_id, exclude=None):
    order=db.order(order_id)
    if not order or order["status"]!="searching": return 0
    sent=0
    for d in db.available_drivers():
        if exclude and d["tg_id"]==exclude: continue
        if db.active_driver_count(d["tg_id"])>=MAX_ACTIVE_ORDERS: continue
        if db.offer(order_id,d["tg_id"]): continue
        text=(f"🚕 <b>YANGI BUYURTMA #{order_id}</b>\n\n📍 {order['origin']}\n🏁 {order['destination']}\n"
              f"🗺 GPS: mavjud\n" + (f"👥 {order['passengers']} kishi" if order['service']=="passenger" else "📦 Dastavka") + f"\n💰 {order['price']:,} so‘m")
        try:
            await bot.send_message(d["tg_id"],text,reply_markup=driver_offer_kb(order_id,d["tg_id"]))
            db.add_offer(order_id,d["tg_id"]); sent+=1
        except Exception as e:
            log.warning("cannot send order %s to driver %s: %s",order_id,d["tg_id"],e)
    return sent

async def start_order(bot, order_id):
    sent=await dispatch(bot,order_id)
    if sent==0:
        order=db.order(order_id)
        if order:
            db.cancel_order(order_id)
            await bot.send_message(order["customer_id"], "❌ Hozircha bo‘sh haydovchi topilmadi. Buyurtma bekor qilindi.")

async def no_answer_flow(bot, order_id, driver_id):
    order=db.order(order_id)
    if not order or order["driver_id"]!=driver_id or order["status"] not in ("accepted","in_progress"): return
    db.set_waiting_customer(order_id)
    try:
        await bot.send_message(order["customer_id"],tr(order["customer_id"],"still_need"),reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=tr(order["customer_id"],"yes"),callback_data=f"needyes:{order_id}"),InlineKeyboardButton(text=tr(order["customer_id"],"no"),callback_data=f"needno:{order_id}")]]))
    except Exception: return
    await asyncio.sleep(NO_ANSWER_TIMEOUT)
    order2=db.order(order_id)
    if order2 and order2["status"]=="waiting_customer":
        db.cancel_order(order_id)
        try: await bot.send_message(order2["customer_id"],tr(order2["customer_id"],"expired"))
        except Exception: pass

bot=Bot(BOT_TOKEN,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp=Dispatcher(storage=MemoryStorage())

@dp.message(CommandStart())
async def cmd_start(m:Message,state:FSMContext):
    db.ensure_user(m.from_user.id)
    await state.clear()
    await m.answer(T["uz"]["welcome"],reply_markup=lang_kb())

@dp.callback_query(F.data.startswith("lang:"))
async def choose_lang(c:CallbackQuery,state:FSMContext):
    l=c.data.split(":",1)[1]; db.set_user(c.from_user.id,lang=l); await c.answer()
    await c.message.edit_text(T[l]["welcome"] .split("\n\n")[0])
    await c.message.answer(T[l]["name"],reply_markup=ReplyKeyboardRemove()); await state.set_state(Customer.name)

@dp.message(Customer.name)
async def customer_name(m:Message,state:FSMContext):
    if not m.text: return
    db.set_user(m.from_user.id,name=m.text.strip())
    await state.set_state(Customer.phone); await m.answer(tr(m.from_user.id,"phone"),reply_markup=phone_kb(m.from_user.id))

@dp.message(Customer.phone, F.contact)
async def customer_phone(m:Message,state:FSMContext):
    db.set_user(m.from_user.id,phone=m.contact.phone_number)
    await state.set_state(Customer.route); await m.answer(tr(m.from_user.id,"route"),reply_markup=ReplyKeyboardRemove())

@dp.message(Customer.phone)
async def customer_phone_bad(m:Message): await m.answer(tr(m.from_user.id,"phone"),reply_markup=phone_kb(m.from_user.id))

@dp.message(Customer.route)
async def customer_route(m:Message,state:FSMContext):
    r=parse_route(m.text or "")
    if not r: await m.answer(tr(m.from_user.id,"route_bad")); return
    await state.update_data(origin=r[0],destination=r[1]); await state.set_state(Customer.gps)
    await m.answer(tr(m.from_user.id,"gps"),reply_markup=gps_kb(m.from_user.id))

@dp.message(Customer.gps, F.location)
async def customer_gps(m:Message,state:FSMContext):
    await state.update_data(latitude=m.location.latitude,longitude=m.location.longitude)
    await state.set_state(Customer.service); await m.answer(tr(m.from_user.id,"people"),reply_markup=people_kb(m.from_user.id))

@dp.message(Customer.gps)
async def customer_gps_bad(m:Message): await m.answer(tr(m.from_user.id,"gps"),reply_markup=gps_kb(m.from_user.id))

@dp.message(Customer.service)
async def customer_service(m:Message,state:FSMContext):
    service,p=people_value(m.from_user.id,m.text or "")
    if not service: await m.answer(tr(m.from_user.id,"people"),reply_markup=people_kb(m.from_user.id)); return
    await state.update_data(service=service,passengers=p); await state.set_state(Customer.price)
    await m.answer(tr(m.from_user.id,"price"),reply_markup=price_kb(m.from_user.id))

@dp.message(Customer.price)
async def customer_price(m:Message,state:FSMContext):
    p=price_value(m.text or "")
    if not p: await m.answer(tr(m.from_user.id,"price"),reply_markup=price_kb(m.from_user.id)); return
    data=await state.get_data(); data["price"]=p
    order_id=db.create_order(customer_id=m.from_user.id,origin=data["origin"],destination=data["destination"],latitude=data["latitude"],longitude=data["longitude"],service=data["service"],passengers=data["passengers"],price=p)
    await state.clear()
    label=(f"👥 {data['passengers']} kishi" if data['service']=='passenger' else "📦 Dastavka")
    await m.answer(f"🎫 <b>BUYURTMA #{order_id}</b>\n\n📍 {data['origin']} → {data['destination']}\n{label}\n💰 {p:,} so‘m\n\n{tr(m.from_user.id,'confirm')}",reply_markup=confirm_kb(m.from_user.id,order_id))

@dp.callback_query(F.data.startswith("confirm:"))
async def confirm_order(c:CallbackQuery):
    oid=int(c.data.split(":")[1]); o=db.order(oid)
    if not o or o["customer_id"]!=c.from_user.id or o["status"]!="searching": await c.answer(tr(c.from_user.id,"not_found"),show_alert=True); return
    await c.answer(); await c.message.edit_reply_markup(reply_markup=None); await c.message.answer(tr(c.from_user.id,"created",oid),reply_markup=main_menu(c.from_user.id)); await start_order(bot,oid)

@dp.callback_query(F.data.startswith("cancel:"))
async def cancel_order_cb(c:CallbackQuery):
    oid=int(c.data.split(":")[1]); ok=db.cancel_order(oid,c.from_user.id); await c.answer()
    if ok: await c.message.edit_text("❌ Buyurtma bekor qilindi.")

@dp.message(F.text)
async def menu_router(m:Message,state:FSMContext):
    if await state.get_state(): return
    uid=m.from_user.id; text=m.text; l=lang(uid)
    mapping={
        "uz":{"🚕 Taksi buyurtma qilish":"taxi","📦 Dastavka":"delivery","🚕 Haydovchi bo‘lish":"driver","📋 Mening buyurtmalarim":"orders","👤 Profil":"profile","⚙️ Sozlamalar":"settings","📞 Yordam":"support"},
        "ru":{"🚕 Заказать такси":"taxi","📦 Доставка":"delivery","🚕 Стать водителем":"driver","📋 Мои заказы":"orders","👤 Профиль":"profile","⚙️ Настройки":"settings","📞 Помощь":"support"},
        "en":{"🚕 Order Taxi":"taxi","📦 Delivery":"delivery","🚕 Become a driver":"driver","📋 My orders":"orders","👤 Profile":"profile","⚙️ Settings":"settings","📞 Support":"support"},
        "uzc":{"🚕 Такси буюртма қилиш":"taxi","📦 Даставка":"delivery","🚕 Ҳайдовчи бўлиш":"driver","📋 Менинг буюртмаларим":"orders","👤 Профиль":"profile","⚙️ Созламалар":"settings","📞 Ёрдам":"support"},
    }
    action=mapping[l].get(text)
    if action in ("taxi","delivery"):
        await state.set_state(Customer.name); await m.answer(tr(uid,"name"),reply_markup=ReplyKeyboardRemove()); return
    if action=="driver":
        d=db.driver(uid)
        if d and d["blocked"]: await m.answer(tr(uid,"blocked")); return
        if d and d["approved"]: await driver_panel(m); return
        await state.set_state(Driver.name); await m.answer(tr(uid,"car_model")) if d else await m.answer(tr(uid,"name")); return
    if action=="profile":
        u=db.user(uid); await m.answer(f"👤 {u['name'] or '-'}\n📞 {u['phone'] or '-'}")
    elif action=="settings": await m.answer("🌐 Tilni o‘zgartirish uchun /start ni bosing.")
    elif action=="support": await m.answer("📞 Admin: +998 94 422 08 09")
    elif action=="orders":
        rows=db.conn.execute("SELECT * FROM orders WHERE customer_id=? ORDER BY id DESC LIMIT 10",(uid,)).fetchall()
        await m.answer("\n".join([f"#{r['id']} — {r['origin']} → {r['destination']} — {r['status']}" for r in rows]) or "Buyurtmalar yo‘q.")

@dp.message(Driver.name)
async def driver_name(m:Message,state:FSMContext):
    await state.update_data(name=m.text.strip()); await state.set_state(Driver.phone); await m.answer(tr(m.from_user.id,"phone"),reply_markup=phone_kb(m.from_user.id))

@dp.message(Driver.phone,F.contact)
async def driver_phone(m:Message,state:FSMContext):
    await state.update_data(phone=m.contact.phone_number); await state.set_state(Driver.car_model); await m.answer(tr(m.from_user.id,"car_model"),reply_markup=ReplyKeyboardRemove())

@dp.message(Driver.car_model)
async def driver_car(m:Message,state:FSMContext):
    await state.update_data(car_model=m.text.strip()); await state.set_state(Driver.plate); await m.answer(tr(m.from_user.id,"plate"))

@dp.message(Driver.plate)
async def driver_plate(m:Message,state:FSMContext):
    await state.update_data(plate=m.text.strip()); await state.set_state(Driver.car_photo); await m.answer(tr(m.from_user.id,"car_photo"))

@dp.message(Driver.car_photo,F.photo)
async def driver_car_photo(m:Message,state:FSMContext):
    await state.update_data(car_photo=m.photo[-1].file_id); await state.set_state(Driver.passport); await m.answer(tr(m.from_user.id,"passport"))

@dp.message(Driver.passport,F.photo)
async def driver_passport(m:Message,state:FSMContext):
    await state.update_data(passport_photo=m.photo[-1].file_id); await state.set_state(Driver.license); await m.answer(tr(m.from_user.id,"license"))

@dp.message(Driver.license,F.photo)
async def driver_license(m:Message,state:FSMContext):
    data=await state.get_data(); data["license_photo"]=m.photo[-1].file_id
    db.save_driver(m.from_user.id,**data); await state.clear(); await m.answer(tr(m.from_user.id,"submitted"),reply_markup=main_menu(m.from_user.id))
    d=db.driver(m.from_user.id)
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ TASDIQLASH",callback_data=f"admapp:{m.from_user.id}"),InlineKeyboardButton(text="❌ RAD ETISH",callback_data=f"admrej:{m.from_user.id}")]])
    await notify_admin(bot,f"🚕 <b>Yangi haydovchi</b>\n\n👤 {d['name']}\n📞 {d['phone']}\n🚗 {d['car_model']}\n🔢 {d['plate']}",kb)

@dp.message(Driver)
async def driver_photo_bad(m:Message,state:FSMContext): await m.answer("📸 Iltimos, rasm yuboring." )

async def driver_panel(m:Message):
    d=db.driver(m.from_user.id); active=db.active_driver_count(m.from_user.id)
    if not d: return
    if d["blocked"]: await m.answer(tr(m.from_user.id,"blocked")); return
    status=tr(m.from_user.id,"online") if d["online"] else tr(m.from_user.id,"offline")
    kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🟢 ONLINE",callback_data="drv:on"),InlineKeyboardButton(text="🔴 OFFLINE",callback_data="drv:off")],[InlineKeyboardButton(text=f"📋 FAOL BUYURTMALAR: {active}/4",callback_data="drv:noop")]])
    await m.answer(f"{tr(m.from_user.id,'driver_menu')}\n\n{status}\n📋 Faol buyurtmalar: {active}/4",reply_markup=kb)

@dp.callback_query(F.data.startswith("drv:"))
async def driver_toggle(c:CallbackQuery):
    d=db.driver(c.from_user.id)
    if not d or not d["approved"]: await c.answer(tr(c.from_user.id,"not_approved"),show_alert=True); return
    if d["blocked"]: await c.answer(tr(c.from_user.id,"blocked"),show_alert=True); return
    action=c.data.split(":")[1]
    if action=="noop": await c.answer(); return
    db.set_online(c.from_user.id,action=="on"); await c.answer(); await driver_panel(c.message)

@dp.callback_query(F.data.startswith("accept:"))
async def accept_order(c:CallbackQuery):
    oid=int(c.data.split(":")[1]); ok,reason=db.accept_order(oid,c.from_user.id)
    if not ok:
        await c.answer(tr(c.from_user.id,"too_many") if reason=="limit" else tr(c.from_user.id,"not_found"),show_alert=True); return
    o=db.order(oid); cust=db.user(o["customer_id"]); d=db.driver(c.from_user.id)
    await c.answer(); await c.message.edit_reply_markup(reply_markup=None)
    await c.message.answer(tr(c.from_user.id,"accepted",oid))
    await bot.send_message(o["customer_id"],f"{tr(o['customer_id'],'driver_found')}\n\n👤 {d['name']}\n📞 {d['phone']}\n🚗 {d['car_model']}\n🔢 {d['plate']}")
    try: await bot.send_photo(o["customer_id"],d["car_photo"],caption=f"🚗 {d['car_model']} — {d['plate']}")
    except Exception: pass
    await bot.send_message(c.from_user.id,f"👤 <b>Mijoz</b>\n\nIsm: {cust['name']}\n📞 {cust['phone']}\n📍 {o['origin']}\n🏁 {o['destination']}\n💰 {o['price']:,} so‘m\n🎫 #{oid}\n\n🗺 GPS lokatsiya quyida.",reply_markup=accepted_kb(oid))
    try: await bot.send_location(c.from_user.id,o["latitude"],o["longitude"])
    except Exception: pass

@dp.callback_query(F.data.startswith("reject:"))
async def reject_order(c:CallbackQuery):
    oid=int(c.data.split(":")[1]); db.set_offer(oid,c.from_user.id,"rejected"); await c.answer(tr(c.from_user.id,"rejected")); await c.message.edit_reply_markup(reply_markup=None)

@dp.callback_query(F.data.startswith("noanswer:"))
async def noanswer(c:CallbackQuery):
    oid=int(c.data.split(":")[1]); o=db.active_order_for_driver(oid,c.from_user.id)
    if not o: await c.answer(tr(c.from_user.id,"not_found"),show_alert=True); return
    db.mark_offer_no_answer(oid,c.from_user.id); await c.answer(); await c.message.edit_reply_markup(reply_markup=None); await no_answer_flow(bot,oid,c.from_user.id)

@dp.callback_query(F.data.startswith("needyes:"))
async def need_yes(c:CallbackQuery):
    oid=int(c.data.split(":")[1]); o=db.order(oid)
    if not o or o["customer_id"]!=c.from_user.id or o["status"]!="waiting_customer": await c.answer(tr(c.from_user.id,"not_found"),show_alert=True); return
    db.reopen_order(oid); await c.answer(); await c.message.edit_reply_markup(reply_markup=None); await c.message.answer(tr(c.from_user.id,"need_yes")); await start_order(bot,oid)

@dp.callback_query(F.data.startswith("needno:"))
async def need_no(c:CallbackQuery):
    oid=int(c.data.split(":")[1]); o=db.order(oid)
    if not o or o["customer_id"]!=c.from_user.id: await c.answer(tr(c.from_user.id,"not_found"),show_alert=True); return
    db.cancel_order(oid,c.from_user.id); await c.answer(); await c.message.edit_reply_markup(reply_markup=None); await c.message.answer(tr(c.from_user.id,"need_no"))

@dp.callback_query(F.data.startswith("complete:"))
async def complete(c:CallbackQuery):
    oid=int(c.data.split(":")[1]); ok=db.finish_order(oid,c.from_user.id); await c.answer(tr(c.from_user.id,"completed") if ok else tr(c.from_user.id,"not_found"),show_alert=not ok)
    if ok:
        await c.message.edit_reply_markup(reply_markup=None)
        try: await bot.send_message(db.order(oid)["customer_id"],tr(db.order(oid)["customer_id"],"completed"))
        except Exception: pass

@dp.message(Command("admin"))
async def admin_cmd(m:Message):
    if m.from_user.id!=ADMIN_ID: await m.answer(tr(m.from_user.id,"admin_only")); return
    s=db.stats(); kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚕 KUTILAYOTGAN HAYDOVCHILAR",callback_data="admin:pending")],[InlineKeyboardButton(text="📋 BUYURTMALAR",callback_data="admin:orders")]])
    await m.answer(f"🔐 <b>ADMIN PANEL</b>\n\n👤 Mijozlar: {s['customers']}\n🚕 Haydovchilar: {s['drivers']}\n🟢 Online: {s['online']}\n📋 Bugungi buyurtmalar: {s['today']}\n⏳ Faol: {s['active']}\n✅ Yakunlangan: {s['completed']}\n❌ Bekor: {s['cancelled']}",reply_markup=kb)

@dp.callback_query(F.data=="admin:pending")
async def admin_pending(c:CallbackQuery):
    if c.from_user.id!=ADMIN_ID: await c.answer("No",show_alert=True); return
    rows=db.pending_drivers(); await c.answer()
    if not rows: await c.message.answer("Yangi haydovchi yo‘q."); return
    for d in rows:
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ TASDIQLASH",callback_data=f"admapp:{d['tg_id']}"),InlineKeyboardButton(text="❌ RAD ETISH",callback_data=f"admrej:{d['tg_id']}")]])
        await c.message.answer(f"🚕 <b>{d['name']}</b>\n📞 {d['phone']}\n🚗 {d['car_model']}\n🔢 {d['plate']}",reply_markup=kb)
        for field,cap in [("car_photo","📸 Mashina"),("passport_photo","📄 Texpasport"),("license_photo","🪪 Prava")]:
            try: await bot.send_photo(ADMIN_ID,d[field],caption=cap)
            except Exception: pass

@dp.callback_query(F.data.startswith("admapp:"))
async def admin_approve(c:CallbackQuery):
    if c.from_user.id!=ADMIN_ID: await c.answer("No",show_alert=True); return
    did=int(c.data.split(":")[1]); db.set_driver_approval(did,True); await c.answer("Tasdiqlandi"); await c.message.edit_reply_markup(reply_markup=None)
    d=db.driver(did); await bot.send_message(did,tr(did,"approved"))

@dp.callback_query(F.data.startswith("admrej:"))
async def admin_reject(c:CallbackQuery):
    if c.from_user.id!=ADMIN_ID: await c.answer("No",show_alert=True); return
    did=int(c.data.split(":")[1]); db.set_driver_approval(did,False,True); await c.answer("Rad etildi"); await c.message.edit_reply_markup(reply_markup=None)
    await bot.send_message(did,"❌ Haydovchi ro‘yxatdan o‘tishingiz rad etildi.")

@dp.callback_query(F.data=="admin:orders")
async def admin_orders(c:CallbackQuery):
    if c.from_user.id!=ADMIN_ID: await c.answer("No",show_alert=True); return
    rows=db.list_orders(); await c.answer()
    text="📋 <b>Oxirgi buyurtmalar</b>\n\n"+"\n".join([f"#{r['id']} | {r['origin']} → {r['destination']} | {r['price']:,} | {r['status']}" for r in rows])
    await c.message.answer(text)

async def main():
    log.info("Bot starting")
    await dp.start_polling(bot)

if __name__=="__main__":
    asyncio.run(main()
