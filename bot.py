import os
import asyncio
import logging
import sqlite3
import time
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart, StateFilter
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
)


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "0") or 0)
DB_PATH = os.getenv("DB_PATH", "taxi_bor_mi.db")

MAX_ACTIVE_ORDERS = 4
SEARCH_TIMEOUT = 120
NO_ANSWER_TIMEOUT = 60

MIN_PRICE = 5000
MAX_PRICE = 1_000_000

ROUTE_CODE = "OBLIQ_ANGREN"


if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN env variable is required")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)

log = logging.getLogger("taxi_bor_mi")


bot = Bot(
    BOT_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML
    ),
)

dp = Dispatcher(
    storage=MemoryStorage()
)

DB_LOCK = asyncio.Lock()


# ============================================================
# TRANSLATIONS
# ============================================================

T = {

    # ========================================================
    # UZBEK LATIN
    # ========================================================

    "uz": {

        "passenger": "👤 Yo‘lovchi",
        "driver": "🚕 Haydovchi",
        "profile": "👤 Profil",
        "history": "📜 Tarix",
        "support": "📩 Murojaat",
        "change_lang": "🌐 Tilni o‘zgartirish",
        "home": "🏠 Asosiy menyu",
        "cancel": "❌ Bekor qilish",

        "name": "👤 Ism-familiyangizni kiriting:",

        "phone":
            "📱 Telefon raqamingizni yuboring yoki "
            "kontakt tugmasini bosing:",

        "phone_btn":
            "📱 Telefon raqamim",

        "route":
            "📝 Yo‘nalishingizni yozing.\n"
            "Istalgancha yozishingiz mumkin. "
            "Bot matningizni o‘zgartirmaydi.",

        "gps":
            "📍 Endi olib ketish joyingizning "
            "GPS lokatsiyasini yuboring.",

        "gps_btn":
            "📍 Joylashuvimni yuborish",

        "gps_bad":
            "📍 Iltimos, pastdagi tugma orqali "
            "haqiqiy GPS lokatsiyani yuboring.",

        "people":
            "👥 Necha kishi?",

        "delivery":
            "📦 Dastavka",

        "price":
            "💰 Narxni tanlang:",

        "other_price":
            "✍️ Boshqa narx",

        "custom_price":
            "✍️ Boshqa narxni raqamda kiriting:",

        "bad_price":
            "❗ Narx 5 000 dan 1 000 000 so‘mgacha "
            "bo‘lishi kerak.",

        "created":
            "🔎 <b>Buyurtma #{id}</b> yaratildi.\n"
            "🚕 Haydovchi qidirilmoqda...",

        "no_driver":
            "⚠️ Hozircha haydovchi topilmadi. "
            "Buyurtma yopildi.",

        "blocked":
            "⛔ Akkauntingiz bloklangan.",

        "not_registered":
            "Avval /start ni bosing.",

        "accepted":
            "✅ Haydovchi topildi!",

        "no_answer":
            "📵 Haydovchi siz bilan bog‘lana olmadi.\n\n"
            "Sizga hali ham mashina kerakmi?\n"
            "⏱ 1 daqiqa ichida javob bering.",

        "yes_needed":
            "✅ HA, KERAK",

        "no_needed":
            "❌ YO‘Q, KERAK EMAS",

        "expired":
            "❌ Javob berish vaqti tugadi. "
            "Buyurtma yopildi.",

        "completed":
            "🏁 Buyurtma yakunlandi.",

        "rate":
            "⭐ Haydovchini 1–5 baholang:",

        "thanks":
            "🙏 Rahmat!",

        "driver_reg":
            "🚕 Haydovchi ro‘yxatdan o‘tishi",

        "car":
            "🚗 Mashina modeli:",

        "plate":
            "🔢 Davlat raqami:",

        "route_driver":
            "📍 Yo‘nalish: OBLIQ ↔ ANGREN",

        "license":
            "🪪 Haydovchilik guvohnomasi "
            "rasmini yuboring:",

        "tech":
            "📄 Texpasport rasmini yuboring:",

        "car_photo":
            "🚗 Mashinangiz rasmini yuboring:",

        "rules":
            "⚠️ Qoidalar:\n"
            "Buyurtmani qabul qilgandan keyin "
            "o‘zboshimchalik bilan bekor qilish mumkin emas.\n"
            "Mijoz javob bermasa, 1 daqiqalik tartib ishlaydi.\n\n"
            "Qabul qilasizmi?",

        "accept_rules":
            "✅ Qabul qilaman",

        "reject_rules":
            "❌ Qabul qilmayman",

        "approval":
            "⏳ Ma’lumotlaringiz admin tasdig‘iga yuborildi.",

        "online":
            "🟢 ONLINE",

        "offline":
            "🔴 OFFLINE",

        "active":
            "Faol buyurtmalar: {n}/4",

        "go_online":
            "🟢 ONLINE bo‘lish",

        "go_offline":
            "🔴 OFFLINE bo‘lish",

        "order_claim":
            "✅ BUYURTMANI OLISH",

        "decline":
            "❌ RAD ETISH",

        "no_answer_btn":
            "📵 MIJOZ JAVOB BERMADI",

        "finish":
            "🏁 BUYURTMANI YAKUNLASH",

        "driver_profile":
            "👤 {name}\n"
            "📱 {phone}\n"
            "🚗 {car}\n"
            "🔢 {plate}\n"
            "⭐ {rating}\n"
            "📊 Faol: {active}/4",

        "customer_info":
            "👤 Mijoz: {name}\n"
            "📱 Telefon: {phone}\n"
            "📝 Yo‘nalish:\n{route}",

        "customer_found":
            "🚕 <b>Haydovchi topildi!</b>\n\n"
            "🚗 Moshina: {car}\n"
            "🔢 Raqami: {plate}\n"
            "📱 Telefon: {phone}",

        "support_prompt":
            "📩 Murojaatingizni yozing:",

        "saved":
            "✅ Qabul qilindi.",

        "no_active":
            "Faol buyurtma yo‘q.",

        "offer_text":
            "🚕 <b>YANGI BUYURTMA #{id}</b>\n\n"
            "{service}\n"
            "💰 {price}\n\n"
            "📝 <b>MIJOZ YOZGAN:</b>\n"
            "{route}\n\n"
            "📍 GPS lokatsiya yuborildi.",

        "passenger_service":
            "👤 Yo‘lovchi\n👥 {n} kishi",

        "delivery_service":
            "📦 Dastavka",

        "claim_fail":
            "❌ Buyurtma allaqachon olingan, yopilgan "
            "yoki sizga mos emas.",

        "declined":
            "❌ Buyurtma rad etildi.",

        "noanswer_started":
            "⏱ 1 daqiqalik mijoz javobi vaqti boshlandi.",

        "language_select":
            "Tilni tanlang:",

        "main":
            "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n"
            "📍 <b>OBLIQ ↔ ANGREN</b>\n\n"
            "Xizmatni tanlang:",
    },


    # ========================================================
    # UZBEK CYRILLIC
    # ========================================================

    "uzc": {

        "passenger": "👤 Йўловчи",
        "driver": "🚕 Ҳайдовчи",
        "profile": "👤 Профиль",
        "history": "📜 Тарих",
        "support": "📩 Мурожаат",
        "change_lang": "🌐 Тилни ўзгартириш",
        "home": "🏠 Асосий меню",
        "cancel": "❌ Бекор қилиш",

        "name":
            "👤 Исм-фамилиянгизни киритинг:",

        "phone":
            "📱 Телефон рақамингизни юборинг ёки "
            "контакт тугмасини босинг:",

        "phone_btn":
            "📱 Телефон рақамим",

        "route":
            "📝 Йўналишингизни ёзинг.\n"
            "Истаганча ёзишингиз мумкин. "
            "Бот матнингизни ўзгартирмайди.",

        "gps":
            "📍 Энди олиб кетиш жойининг "
            "GPS локатсиясини юборинг.",

        "gps_btn":
            "📍 Жойлашувимни юбориш",

        "gps_bad":
            "📍 Илтимос, пастдаги тугма орқали "
            "ҳақиқий GPS юборинг.",

        "people":
            "👥 Неча киши?",

        "delivery":
            "📦 Доставка",

        "price":
            "💰 Нархни танланг:",

        "other_price":
            "✍️ Бошқа нарх",

        "custom_price":
            "✍️ Бошқа нархни рақамда киритинг:",

        "bad_price":
            "❗ Нарх 5 000 дан 1 000 000 сўмгача "
            "бўлиши керак.",

        "created":
            "🔎 <b>Буюртма #{id}</b> яратилди.\n"
            "🚕 Ҳайдовчи қидирилмоқда...",

        "no_driver":
            "⚠️ Ҳозирча ҳайдовчи топилмади. "
            "Буюртма ёпилди.",

        "blocked":
            "⛔ Аккаунтингиз блокланган.",

        "not_registered":
            "Аввал /start ни босинг.",

        "accepted":
            "✅ Ҳайдовчи топилди!",

        "no_answer":
            "📵 Ҳайдовчи сиз билан боғлана олмади.\n\n"
            "Сизга ҳали ҳам машина керакми?\n"
            "⏱ 1 дақиқа ичида жавоб беринг.",

        "yes_needed":
            "✅ ҲА, КЕРАК",

        "no_needed":
            "❌ ЙЎҚ, КЕРАК ЭМАС",

        "expired":
            "❌ Жавоб бериш вақти тугади. "
            "Буюртма ёпилди.",

        "completed":
            "🏁 Буюртма якунланди.",

        "rate":
            "⭐ Ҳайдовчини 1–5 баҳоланг:",

        "thanks":
            "🙏 Раҳмат!",

        "driver_reg":
            "🚕 Ҳайдовчи рўйхатдан ўтиши",

        "car":
            "🚗 Машина модели:",

        "plate":
            "🔢 Давлат рақами:",

        "route_driver":
            "📍 Йўналиш: ОБЛИҚ ↔ АНГРЕН",

        "license":
            "🪪 Ҳайдовчилик гувоҳномаси "
            "расмини юборинг:",

        "tech":
            "📄 Техпаспорт расмини юборинг:",

        "car_photo":
            "🚗 Машинангиз расмини юборинг:",

        "rules":
            "⚠️ Қоидалар:\n"
            "Буюртмани қабул қилгандан кейин "
            "ўзбошимчалик билан бекор қилиш мумкин эмас.\n"
            "Мижоз жавоб бермаса, 1 дақиқалик тартиб ишлайди.\n\n"
            "Қабул қиласизми?",

        "accept_rules":
            "✅ Қабул қиламан",

        "reject_rules":
            "❌ Қабул қилмайман",

        "approval":
            "⏳ Маълумотларингиз админ тасдиғига юборилди.",

        "online":
            "🟢 ONLINE",

        "offline":
            "🔴 OFFLINE",

        "active":
            "Фаол буюртмалар: {n}/4",

        "go_online":
            "🟢 ONLINE бўлиш",

        "go_offline":
            "🔴 OFFLINE бўлиш",

        "order_claim":
            "✅ БУЮРТМАНИ ОЛИШ",

        "decline":
            "❌ РАД ЭТИШ",

        "no_answer_btn":
            "📵 МИЖОЗ ЖАВОБ БЕРМАДИ",

        "finish":
            "🏁 БУЮРТМАНИ ЯКУНЛАШ",

        "driver_profile":
            "👤 {name}\n"
            "📱 {phone}\n"
            "🚗 {car}\n"
            "🔢 {plate}\n"
            "⭐ {rating}\n"
            "📊 Фаол: {active}/4",

        "customer_info":
            "👤 Мижоз: {name}\n"
            "📱 Телефон: {phone}\n"
            "📝 Йўналиш:\n{route}",

        "customer_found":
            "🚕 <b>Ҳайдовчи топилди!</b>\n\n"
            "🚗 Машина: {car}\n"
            "🔢 Рақами: {plate}\n"
            "📱 Телефон: {phone}",

        "support_prompt":
            "📩 Мурожаатингизни ёзинг:",

        "saved":
            "✅ Қабул қилинди.",

        "no_active":
            "Фаол буюртма йўқ.",

        "offer_text":
            "🚕 <b>ЯНГИ БУЮРТМА #{id}</b>\n\n"
            "{service}\n"
            "💰 {price}\n\n"
            "📝 <b>МИЖОЗ ЁЗГАН:</b>\n"
            "{route}\n\n"
            "📍 GPS локатсия юборилди.",

        "passenger_service":
            "👤 Йўловчи\n👥 {n} киши",

        "delivery_service":
            "📦 Доставка",

        "claim_fail":
            "❌ Буюртма аллақачон олинган, ёпилган "
            "ёки сизга мос эмас.",

        "declined":
            "❌ Буюртма рад этилди.",

        "noanswer_started":
            "⏱ 1 дақиқалик мижоз жавоби вақти бошланди.",

        "language_select":
            "Тилни танланг:",

        "main":
            "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n"
            "📍 <b>ОБЛИҚ ↔ АНГРЕН</b>\n\n"
            "Хизматни танланг:",
    },


    # ========================================================
    # RUSSIAN
    # ========================================================

    "ru": {

        "passenger": "👤 Пассажир",
        "driver": "🚕 Водитель",
        "profile": "👤 Профиль",
        "history": "📜 История",
        "support": "📩 Поддержка",
        "change_lang": "🌐 Изменить язык",
        "home": "🏠 Главное меню",
        "cancel": "❌ Отмена",

        "name":
            "👤 Введите имя и фамилию:",

        "phone":
            "📱 Отправьте номер телефона или "
            "нажмите кнопку контакта:",

        "phone_btn":
            "📱 Мой номер",

        "route":
            "📝 Напишите маршрут.\n"
            "Можно писать свободным текстом. "
            "Бот не изменяет ваш текст.",

        "gps":
            "📍 Теперь отправьте GPS-локацию "
            "места посадки.",

        "gps_btn":
            "📍 Отправить геолокацию",

        "gps_bad":
            "📍 Пожалуйста, отправьте настоящую "
            "GPS-локацию кнопкой ниже.",

        "people":
            "👥 Сколько человек?",

        "delivery":
            "📦 Доставка",

        "price":
            "💰 Выберите цену:",

        "other_price":
            "✍️ Другая цена",

        "custom_price":
            "✍️ Введите другую цену цифрами:",

        "bad_price":
            "❗ Цена должна быть от 5 000 до "
            "1 000 000 сум.",

        "created":
            "🔎 <b>Заказ #{id}</b> создан.\n"
            "🚕 Ищем водителя...",

        "no_driver":
            "⚠️ Водитель пока не найден. "
            "Заказ закрыт.",

        "blocked":
            "⛔ Ваш аккаунт заблокирован.",

        "not_registered":
            "Сначала нажмите /start.",

        "accepted":
            "✅ Водитель найден!",

        "no_answer":
            "📵 Водитель не смог связаться с вами.\n\n"
            "Машина вам ещё нужна?\n"
            "⏱ Ответьте в течение 1 минуты.",

        "yes_needed":
            "✅ ДА, НУЖНА",

        "no_needed":
            "❌ НЕТ, НЕ НУЖНА",

        "expired":
            "❌ Время ответа истекло. "
            "Заказ закрыт.",

        "completed":
            "🏁 Заказ завершён.",

        "rate":
            "⭐ Оцените водителя от 1 до 5:",

        "thanks":
            "🙏 Спасибо!",

        "driver_reg":
            "🚕 Регистрация водителя",

        "car":
            "🚗 Модель автомобиля:",

        "plate":
            "🔢 Госномер:",

        "route_driver":
            "📍 Маршрут: ОБЛИК ↔ АНГРЕН",

        "license":
            "🪪 Отправьте фото водительского удостоверения:",

        "tech":
            "📄 Отправьте фото техпаспорта:",

        "car_photo":
            "🚗 Отправьте фото автомобиля:",

        "rules":
            "⚠️ Правила:\n"
            "После принятия заказа нельзя отменять его самостоятельно.\n"
            "Если клиент не отвечает, действует 1-минутная процедура.\n\n"
            "Принимаете?",

        "accept_rules":
            "✅ Принимаю",

        "reject_rules":
            "❌ Не принимаю",

        "approval":
            "⏳ Данные отправлены администратору на проверку.",

        "online":
            "🟢 ONLINE",

        "offline":
            "🔴 OFFLINE",

        "active":
            "Активные заказы: {n}/4",

        "go_online":
            "🟢 Включить ONLINE",

        "go_offline":
            "🔴 Выключить OFFLINE",

        "order_claim":
            "✅ ПРИНЯТЬ ЗАКАЗ",

        "decline":
            "❌ ОТКАЗАТЬСЯ",

        "no_answer_btn":
            "📵 КЛИЕНТ НЕ ОТВЕЧАЕТ",

        "finish":
            "🏁 ЗАВЕРШИТЬ ЗАКАЗ",

        "driver_profile":
            "👤 {name}\n"
            "📱 {phone}\n"
            "🚗 {car}\n"
            "🔢 {plate}\n"
            "⭐ {rating}\n"
            "📊 Активные: {active}/4",

        "customer_info":
            "👤 Клиент: {name}\n"
            "📱 Телефон: {phone}\n"
            "📝 Маршрут:\n{route}",

        "customer_found":
            "🚕 <b>Водитель найден!</b>\n\n"
            "🚗 Машина: {car}\n"
            "🔢 Номер: {plate}\n"
            "📱 Телефон: {phone}",

        "support_prompt":
            "📩 Напишите ваше обращение:",

        "saved":
            "✅ Принято.",

        "no_active":
            "Активных заказов нет.",

        "offer_text":
            "🚕 <b>НОВЫЙ ЗАКАЗ #{id}</b>\n\n"
            "{service}\n"
            "💰 {price}\n\n"
            "📝 <b>ТЕКСТ КЛИЕНТА:</b>\n"
            "{route}\n\n"
            "📍 GPS-локация отправлена.",

        "passenger_service":
            "👤 Пассажир\n👥 {n} чел.",

        "delivery_service":
            "📦 Доставка",

        "claim_fail":
            "❌ Заказ уже принят, закрыт или недоступен вам.",

        "declined":
            "❌ Заказ отклонён.",

        "noanswer_started":
            "⏱ Начался 1-минутный срок ожидания "
            "ответа клиента.",

        "language_select":
            "Выберите язык:",

        "main":
            "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n"
            "📍 <b>ОБЛИК ↔ АНГРЕН</b>\n\n"
            "Выберите услугу:",
    },


    # ========================================================
    # ENGLISH
    # ========================================================

    "en": {

        "passenger": "👤 Passenger",
        "driver": "🚕 Driver",
        "profile": "👤 Profile",
        "history": "📜 History",
        "support": "📩 Support",
        "change_lang": "🌐 Change language",
        "home": "🏠 Main menu",
        "cancel": "❌ Cancel",

        "name":
            "👤 Enter your full name:",

        "phone":
            "📱 Send your phone number or "
            "tap the contact button:",

        "phone_btn":
            "📱 My phone",

        "route":
            "📝 Write your route.\n"
            "You can use any wording. "
            "The bot will not change your text.",

        "gps":
            "📍 Now send the GPS location "
            "of the pickup point.",

        "gps_btn":
            "📍 Send my location",

        "gps_bad":
            "📍 Please send the real GPS location "
            "using the button below.",

        "people":
            "👥 How many people?",

        "delivery":
            "📦 Delivery",

        "price":
            "💰 Choose a price:",

        "other_price":
            "✍️ Other price",

        "custom_price":
            "✍️ Enter another price using digits:",

        "bad_price":
            "❗ Price must be from 5,000 to "
            "1,000,000 UZS.",

        "created":
            "🔎 <b>Order #{id}</b> created.\n"
            "🚕 Searching for a driver...",

        "no_driver":
            "⚠️ No driver found. Order closed.",

        "blocked":
            "⛔ Your account is blocked.",

        "not_registered":
            "Press /start first.",

        "accepted":
            "✅ Driver found!",

        "no_answer":
            "📵 The driver could not reach you.\n\n"
            "Do you still need a car?\n"
            "⏱ Reply within 1 minute.",

        "yes_needed":
            "✅ YES, NEEDED",

        "no_needed":
            "❌ NO, NOT NEEDED",

        "expired":
            "❌ Response time expired. Order closed.",

        "completed":
            "🏁 Order completed.",

        "rate":
            "⭐ Rate the driver from 1 to 5:",

        "thanks":
            "🙏 Thank you!",

        "driver_reg":
            "🚕 Driver registration",

        "car":
            "🚗 Car model:",

        "plate":
            "🔢 License plate:",

        "route_driver":
            "📍 Route: OBLIQ ↔ ANGREN",

        "license":
            "🪪 Send a photo of your driving license:",

        "tech":
            "📄 Send a photo of the vehicle registration:",

        "car_photo":
            "🚗 Send a photo of your car:",

        "rules":
            "⚠️ Rules:\n"
            "After accepting an order, "
            "you cannot cancel it arbitrarily.\n"
            "If the customer does not answer, "
            "a 1-minute procedure applies.\n\n"
            "Do you accept?",

        "accept_rules":
            "✅ I accept",

        "reject_rules":
            "❌ I do not accept",

        "approval":
            "⏳ Your data was sent to the administrator for approval.",

        "online":
            "🟢 ONLINE",

        "offline":
            "🔴 OFFLINE",

        "active":
            "Active orders: {n}/4",

        "go_online":
            "🟢 Go ONLINE",

        "go_offline":
            "🔴 Go OFFLINE",

        "order_claim":
            "✅ ACCEPT ORDER",

        "decline":
            "❌ DECLINE",

        "no_answer_btn":
            "📵 CUSTOMER DOES NOT ANSWER",

        "finish":
            "🏁 FINISH ORDER",

        "driver_profile":
            "👤 {name}\n"
            "📱 {phone}\n"
            "🚗 {car}\n"
            "🔢 {plate}\n"
            "⭐ {rating}\n"
            "📊 Active: {active}/4",

        "customer_info":
            "👤 Customer: {name}\n"
            "📱 Phone: {phone}\n"
            "📝 Route:\n{route}",

        "customer_found":
            "🚕 <b>Driver found!</b>\n\n"
            "🚗 Car: {car}\n"
            "🔢 Plate: {plate}\n"
            "📱 Phone: {phone}",

        "support_prompt":
            "📩 Write your message:",

        "saved":
            "✅ Received.",

        "no_active":
            "No active orders.",

        "offer_text":
            "🚕 <b>NEW ORDER #{id}</b>\n\n"
            "{service}\n"
            "💰 {price}\n\n"
            "📝 <b>CUSTOMER TEXT:</b>\n"
            "{route}\n\n"
            "📍 GPS location sent.",

        "passenger_service":
            "👤 Passenger\n👥 {n} people",

        "delivery_service":
            "📦 Delivery",

        "claim_fail":
            "❌ The order was already accepted, closed, "
            "or is unavailable to you.",

        "declined":
            "❌ Order declined.",

        "noanswer_started":
            "⏱ The 1-minute customer response period has started.",

        "language_select":
            "Choose a language:",

        "main":
            "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n"
            "📍 <b>OBLIQ ↔ ANGREN</b>\n\n"
            "Choose a service:",
    },
}


def tr(lang: str, key: str, **kwargs) -> str:
    if lang not in T:
        lang = "uz"

    text = T[lang].get(
        key,
        T["uz"].get(key, key),
    )

    return text.format(**kwargs)


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    connection = sqlite3.connect(
        DB_PATH,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA foreign_keys=ON")

    return connection


def now() -> str:
    return datetime.utcnow().isoformat(
        timespec="seconds"
    )


def init_db():

    connection = db_connect()

    connection.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        tg_id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        phone TEXT NOT NULL,
        lang TEXT NOT NULL DEFAULT 'uz',
        blocked INTEGER NOT NULL DEFAULT 0,
        created_at TEXT,
        updated_at TEXT
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

        created_at TEXT,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        customer_id INTEGER NOT NULL,
        driver_id INTEGER,

        service TEXT NOT NULL,
        passengers INTEGER,

        route_text TEXT NOT NULL,

        lat REAL NOT NULL,
        lon REAL NOT NULL,

        price INTEGER NOT NULL,

        status TEXT NOT NULL,

        excluded_driver INTEGER,

        no_answer_started REAL,

        created_at TEXT,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS offers(
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        order_id INTEGER NOT NULL,
        driver_id INTEGER NOT NULL,

        status TEXT NOT NULL,

        message_id INTEGER DEFAULT 0,
        chat_id INTEGER DEFAULT 0,

        created_at TEXT,
        updated_at TEXT,

        UNIQUE(order_id,driver_id)
    );

    CREATE TABLE IF NOT EXISTS ratings(
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        order_id INTEGER UNIQUE NOT NULL,
        rating INTEGER NOT NULL,

        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS support_tickets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        user_id INTEGER NOT NULL,
        text TEXT NOT NULL,

        created_at TEXT
    );

    CREATE TABLE IF NOT EXISTS events(
        id INTEGER PRIMARY KEY AUTOINCREMENT,

        order_id INTEGER,
        actor_id INTEGER,

        event TEXT NOT NULL,
        details TEXT,

        created_at TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_orders_customer_status
    ON orders(customer_id,status);

    CREATE INDEX IF NOT EXISTS idx_orders_driver_status
    ON orders(driver_id,status);

    CREATE INDEX IF NOT EXISTS idx_offers_order_status
    ON offers(order_id,status);
    """)

    connection.commit()
    connection.close()


async def db_query(
    sql,
    params=(),
    *,
    one=False,
    all_rows=False,
    commit=True,
):

    async with DB_LOCK:

        connection = db_connect()

        try:

            cursor = connection.execute(
                sql,
                params,
            )

            if one:
                result = cursor.fetchone()

            elif all_rows:
                result = cursor.fetchall()

            else:
                result = cursor.lastrowid

            if commit:
                connection.commit()

            return result

        finally:
            connection.close()


async def db_transaction(callback):

    async with DB_LOCK:

        connection = db_connect()

        connection.execute(
            "BEGIN IMMEDIATE"
        )

        try:

            result = callback(connection)

            connection.commit()

            return result

        except Exception:

            connection.rollback()

            raise

        finally:

            connection.close()


async def log_event(
    order_id,
    actor_id,
    event_name,
    details="",
):

    await db_query(
        """
        INSERT INTO events(
            order_id,
            actor_id,
            event,
            details,
            created_at
        )
        VALUES(?,?,?,?,?)
        """,
        (
            order_id,
            actor_id,
            event_name,
            details,
            now(),
        ),
    )


async def get_user(tg_id):

    return await db_query(
        """
        SELECT *
        FROM users
        WHERE tg_id=?
        """,
        (tg_id,),
        one=True,
    )


async def get_driver(tg_id):

    return await db_query(
        """
        SELECT *
        FROM drivers
        WHERE tg_id=?
        """,
        (tg_id,),
        one=True,
    )


def money(value):

    return (
        f"{int(value):,}"
        .replace(",", " ")
        + " so‘m"
    )


# ============================================================
# KEYBOARDS
# ============================================================

def reply_kb(rows):

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        one_time_keyboard=False,
    )


def language_kb():

    return reply_kb([
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
    ])


def main_kb(lang):

    return reply_kb([
        [
            KeyboardButton(
                text=tr(lang, "passenger")
            ),
            KeyboardButton(
                text=tr(lang, "driver")
            ),
        ],
        [
            KeyboardButton(
                text=tr(lang, "profile")
            ),
            KeyboardButton(
                text=tr(lang, "history")
            ),
        ],
        [
            KeyboardButton(
                text=tr(lang, "support")
            ),
            KeyboardButton(
                text=tr(lang, "change_lang")
            ),
        ],
    ])


def cancel_kb(lang):

    return reply_kb([
        [
            KeyboardButton(
                text=tr(lang, "cancel")
            )
        ]
    ])


def phone_kb(lang):

    return reply_kb([
        [
            KeyboardButton(
                text=tr(lang, "phone_btn"),
                request_contact=True,
            )
        ],
        [
            KeyboardButton(
                text=tr(lang, "cancel")
            )
        ],
    ])


def people_kb(lang):

    return reply_kb([
        [
            KeyboardButton(text="1️⃣ 1"),
            KeyboardButton(text="2️⃣ 2"),
        ],
        [
            KeyboardButton(text="3️⃣ 3"),
            KeyboardButton(text="4️⃣ 4"),
        ],
        [
            KeyboardButton(
                text=tr(lang, "delivery")
            ),
            KeyboardButton(
                text=tr(lang, "cancel")
            ),
        ],
    ])


def gps_kb(lang):

    return reply_kb([
        [
            KeyboardButton(
                text=tr(lang, "gps_btn"),
                request_location=True,
            )
        ],
        [
            KeyboardButton(
                text=tr(lang, "cancel")
            )
        ],
    ])


def price_kb(lang):

    return reply_kb([
        [
            KeyboardButton(text="5 000"),
            KeyboardButton(text="10 000"),
        ],
        [
            KeyboardButton(text="15 000"),
            KeyboardButton(text="20 000"),
        ],
        [
            KeyboardButton(
                text=tr(lang, "other_price")
            ),
            KeyboardButton(
                text=tr(lang, "cancel")
            ),
        ],
    ])


def driver_panel_kb(lang, online):

    toggle = (
        tr(lang, "go_offline")
        if online
        else tr(lang, "go_online")
    )

    return reply_kb([
        [
            KeyboardButton(
                text=toggle
            )
        ],
        [
            KeyboardButton(
                text=tr(lang, "profile")
            ),
            KeyboardButton(
                text=tr(lang, "history")
            ),
        ],
        [
            KeyboardButton(
                text=tr(lang, "home")
            )
        ],
    ])


def offer_kb(lang, order_id):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tr(
                        lang,
                        "order_claim"
                    ),
                    callback_data=f"claim:{order_id}",
                ),
                InlineKeyboardButton(
                    text=tr(
                        lang,
                        "decline"
                    ),
                    callback_data=f"decline:{order_id}",
                ),
            ]
        ]
    )


def accepted_kb(lang, order_id):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tr(
                        lang,
                        "no_answer_btn"
                    ),
                    callback_data=f"noans:{order_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=tr(
                        lang,
                        "finish"
                    ),
                    callback_data=f"finish:{order_id}",
                )
            ],
        ]
    )


def need_customer_kb(lang, order_id):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tr(
                        lang,
                        "yes_needed"
                    ),
                    callback_data=f"needyes:{order_id}",
                ),
                InlineKeyboardButton(
                    text=tr(
                        lang,
                        "no_needed"
                    ),
                    callback_data=f"needno:{order_id}",
                ),
            ]
        ]
    )


def rating_kb(order_id):

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⭐ 1",
                    callback_data=f"rate:{order_id}:1",
                ),
                InlineKeyboardButton(
                    text="⭐ 2",
                    callback_data=f"rate:{order_id}:2",
                ),
                InlineKeyboardButton(
                    text="⭐ 3",
                    callback_data=f"rate:{order_id}:3",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⭐ 4",
                    callback_data=f"rate:{order_id}:4",
                ),
                InlineKeyboardButton(
                    text="⭐ 5",
                    callback_data=f"rate:{order_id}:5",
                ),
            ],
        ]
    )


# ============================================================
# STATES
# ============================================================

class Registration(StatesGroup):

    language = State()
    name = State()
    phone = State()


class OrderState(StatesGroup):

    people = State()
    route = State()
    gps = State()
    price = State()
    custom_price = State()


class DriverRegistration(StatesGroup):

    name = State()
    phone = State()
    car = State()
    plate = State()
    route = State()
    license = State()
    tech = State()
    car_photo = State()
    rules = State()


class SupportState(StatesGroup):

    text = State()


# ============================================================
# COMMON HELPERS
# ============================================================

async def ensure_user(message):

    user = await get_user(
        message.from_user.id
    )

    if not user:

        await message.answer(
            "Avval /start ni bosing."
        )

        return None

    if user["blocked"]:

        await message.answer(
            tr(
                user["lang"],
                "blocked"
            )
        )

        return None

    return user


def language_from_button(text):

    return {
        "🇺🇿 O‘zbekcha": "uz",
        "🇺🇿 Ўзбекча": "uzc",
        "🇷🇺 Русский": "ru",
        "🇬🇧 English": "en",
    }.get(text)


# ============================================================
# START
# ============================================================

@dp.message(CommandStart())
async def start(
    message: Message,
    state: FSMContext,
):

    user = await get_user(
        message.from_user.id
    )

    if user:

        if user["blocked"]:

            await message.answer(
                tr(
                    user["lang"],
                    "blocked"
                )
            )

            return

        await state.clear()

        await message.answer(
            tr(
                user["lang"],
                "main"
            ),
            reply_markup=main_kb(
                user["lang"]
            ),
        )

        return

    await state.clear()

    await state.set_state(
        Registration.language
    )

    await message.answer(
        "🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\n"
        "Tilni tanlang / Выберите язык / Choose language:",
        reply_markup=language_kb(),
    )


@dp.message(Registration.language)
async def registration_language(
    message: Message,
    state: FSMContext,
):

    lang = language_from_button(
        message.text
    )

    if not lang:

        await message.answer(
            "Iltimos, tilni tugmadan tanlang.",
            reply_markup=language_kb(),
        )

        return

    existing = await get_user(
        message.from_user.id
    )

    if existing:

        await db_query(
            """
            UPDATE users
            SET lang=?,
                updated_at=?
            WHERE tg_id=?
            """,
            (
                lang,
                now(),
                message.from_user.id,
            ),
        )

        await state.clear()

        await message.answer(
            tr(lang, "main"),
            reply_markup=main_kb(lang),
        )

        return

    await state.update_data(
        lang=lang
    )

    await state.set_state(
        Registration.name
    )

    await message.answer(
        tr(lang, "name"),
        reply_markup=cancel_kb(lang),
    )


@dp.message(Registration.name)
async def registration_name(
    message: Message,
    state: FSMContext,
):

    data = await state.get_data()

    lang = data["lang"]

    name = (
        message.text or ""
    ).strip()

    if len(name) < 2:

        await message.answer(
            tr(lang, "name")
        )

        return

    await state.update_data(
        name=name
    )

    await state.set_state(
        Registration.phone
    )

    await message.answer(
        tr(lang, "phone"),
        reply_markup=phone_kb(lang),
    )


@dp.message(Registration.phone)
async def registration_phone(
    message: Message,
    state: FSMContext,
):

    data = await state.get_data()

    lang = data["lang"]

    phone = (
        message.contact.phone_number
        if message.contact
        else (message.text or "").strip()
    )

    if len(phone) < 5:

        await message.answer(
            tr(lang, "phone"),
            reply_markup=phone_kb(lang),
        )

        return

    stamp = now()

    await db_query(
        """
        INSERT INTO users(
            tg_id,
            name,
            phone,
            lang,
            created_at,
            updated_at
        )
        VALUES(?,?,?,?,?,?)

        ON CONFLICT(tg_id)
        DO UPDATE SET
            name=excluded.name,
            phone=excluded.phone,
            lang=excluded.lang,
            updated_at=excluded.updated_at
        """,
        (
            message.from_user.id,
            data["name"],
            phone,
            lang,
            stamp,
            stamp,
        ),
    )

    await state.clear()

    await message.answer(
        tr(lang, "main"),
        reply_markup=main_kb(lang),
    )


# ============================================================
# CANCEL
# ============================================================

@dp.message(
    F.text.in_({
        "❌ Bekor qilish",
        "❌ Бекор қилиш",
        "❌ Отмена",
        "❌ Cancel",
    })
)
async def global_cancel(
    message: Message,
    state: FSMContext,
):

    user = await get_user(
        message.from_user.id
    )

    await state.clear()

    if user:

        await message.answer(
            tr(
                user["lang"],
                "main"
            ),
            reply_markup=main_kb(
                user["lang"]
            ),
        )

    else:

        await message.answer(
            "Bekor qilindi."
        )


# ============================================================
# MAIN MENU
# ============================================================

@dp.message(
    StateFilter(None),
    F.text
)
async def main_menu_router(
    message: Message,
    state: FSMContext,
):

    user = await ensure_user(
        message
    )

    if not user:
        return

    lang = user["lang"]

    text = message.text

    # --------------------------------------------------------
    # PASSENGER
    # --------------------------------------------------------

    if text == tr(lang, "passenger"):

        await state.set_state(
            OrderState.people
        )

        await message.answer(
            tr(lang, "people"),
            reply_markup=people_kb(lang),
        )

        return

    # --------------------------------------------------------
    # DRIVER
    # --------------------------------------------------------

    if text == tr(lang, "driver"):

        await open_driver_panel(
            message,
            state,
            user,
        )

        return

    # --------------------------------------------------------
    # PROFILE
    # --------------------------------------------------------

    if text == tr(lang, "profile"):

        driver = await get_driver(
            message.from_user.id
        )

        if driver:

            rating = (
                driver["rating_sum"]
                /
                driver["rating_count"]
                if driver["rating_count"]
                else 0
            )

            await message.answer(
                tr(
                    lang,
                    "driver_profile",
                    name=driver["name"],
                    phone=driver["phone"],
                    car=driver["car_model"],
                    plate=driver["plate"],
                    rating=f"{rating:.1f}",
                    active=driver["active_orders"],
                ),
                reply_markup=main_kb(lang),
            )

        else:

            await message.answer(
                f"👤 {user['name']}\n"
                f"📱 {user['phone']}",
                reply_markup=main_kb(lang),
            )

        return

    # --------------------------------------------------------
    # HISTORY
    # --------------------------------------------------------

    if text == tr(lang, "history"):

        rows = await db_query(
            """
            SELECT id,status,price,
                   service,created_at
            FROM orders
            WHERE customer_id=?
            ORDER BY id DESC
            LIMIT 20
            """,
            (
                message.from_user.id,
            ),
            all_rows=True,
        )

        if not rows:

            await message.answer(
                tr(lang, "no_active")
            )

            return

        lines = [
            "📜 <b>Tarix</b>",
            "",
        ]

        for row in rows:

            lines.append(
                f"#{row['id']} • "
                f"{row['status']} • "
                f"{money(row['price'])}"
            )

        await message.answer(
            "\n".join(lines)
        )

        return

    # --------------------------------------------------------
    # SUPPORT
    # --------------------------------------------------------

    if text == tr(lang, "support"):

        await state.set_state(
            SupportState.text
        )

        await message.answer(
            tr(lang, "support_prompt"),
            reply_markup=cancel_kb(lang),
        )

        return

    # --------------------------------------------------------
    # LANGUAGE
    # --------------------------------------------------------

    if text == tr(lang, "change_lang"):

        await state.set_state(
            Registration.language
        )

        await message.answer(
            tr(
                lang,
                "language_select"
            ),
            reply_markup=language_kb(),
        )

        return


# ============================================================
# CUSTOMER ORDER
# ============================================================

@dp.message(OrderState.people)
async def order_people(
    message: Message,
    state: FSMContext,
):

    user = await get_user(
        message.from_user.id
    )

    if not user:
        return

    lang = user["lang"]

    # DELIVERY
    if message.text == tr(
        lang,
        "delivery"
    ):

        service = "delivery"
        passengers = 0

    else:

        mapping = {
            "1️⃣ 1": 1,
            "2️⃣ 2": 2,
            "3️⃣ 3": 3,
            "4️⃣ 4": 4,
        }

        passengers = mapping.get(
            message.text
        )

        if not passengers:

            await message.answer(
                tr(lang, "people"),
                reply_markup=people_kb(lang),
            )

            return

        service = "passenger"

    await state.update_data(
        service=service,
        passengers=passengers,
    )

    await state.set_state(
        OrderState.route
    )

    await message.answer(
        tr(lang, "route"),
        reply_markup=cancel_kb(lang),
    )


@dp.message(OrderState.route)
async def order_route(
    message: Message,
    state: FSMContext,
):

    user = await get_user(
        message.from_user.id
    )

    if not user:
        return

    route_text = (
        message.text or ""
    ).strip()

    if not route_text:

        await message.answer(
            tr(user["lang"], "route")
        )

        return

    # MUHIM:
    # YO‘NALISH MATNI UMUMAN PARSE QILINMAYDI.
    # BOT MIJOZNI "TO‘G‘RI FORMAT"GA MAJBURLAMAYDI.

    await state.update_data(
        route_text=route_text
    )

    await state.set_state(
        OrderState.gps
    )

    await message.answer(
        tr(user["lang"], "gps"),
        reply_markup=gps_kb(
            user["lang"]
        ),
    )


@dp.message(OrderState.gps)
async def order_gps(
    message: Message,
    state: FSMContext,
):

    user = await get_user(
        message.from_user.id
    )

    if not user:
        return

    if not message.location:

        await message.answer(
            tr(
                user["lang"],
                "gps_bad"
            ),
            reply_markup=gps_kb(
                user["lang"]
            ),
        )

        return

    await state.update_data(
        lat=message.location.latitude,
        lon=message.location.longitude,
    )

    await state.set_state(
        OrderState.price
    )

    await message.answer(
        tr(user["lang"], "price"),
        reply_markup=price_kb(
            user["lang"]
        ),
    )


@dp.message(OrderState.price)
async def order_price(
    message: Message,
    state: FSMContext,
):

    user = await get_user(
        message.from_user.id
    )

    if not user:
        return

    prices = {
        "5 000": 5000,
        "10 000": 10000,
        "15 000": 15000,
        "20 000": 20000,
    }

    if message.text == tr(
        user["lang"],
        "other_price"
    ):

        await state.set_state(
            OrderState.custom_price
        )

        await message.answer(
            tr(
                user["lang"],
                "custom_price"
            ),
            reply_markup=cancel_kb(
                user["lang"]
            ),
        )

        return

    price = prices.get(
        message.text
    )

    if price is None:

        await message.answer(
            tr(
                user["lang"],
                "price"
            ),
            reply_markup=price_kb(
                user["lang"]
            ),
        )

        return

    await create_customer_order(
        message,
        state,
        price,
    )


@dp.message(OrderState.custom_price)
async def order_custom_price(
    message: Message,
    state: FSMContext,
):

    user = await get_user(
        message.from_user.id
    )

    if not user:
        return

    raw = message.text or ""

    digits = "".join(
        char
        for char in raw
        if char.isdigit()
    )

    if not digits:

        await message.answer(
            tr(
                user["lang"],
                "bad_price"
            )
        )

        return

    price = int(digits)

    if (
        price < MIN_PRICE
        or
        price > MAX_PRICE
    ):

        await message.answer(
            tr(
                user["lang"],
                "bad_price"
            )
        )

        return

    await create_customer_order(
        message,
        state,
        price,
    )


async def create_customer_order(
    message: Message,
    state: FSMContext,
    price: int,
):

    user = await get_user(
        message.from_user.id
    )

    if not user:
        return

    data = await state.get_data()

    # Bitta mijozga bitta faol buyurtma.
    active = await db_query(
        """
        SELECT id
        FROM orders
        WHERE customer_id=?
          AND status IN(
              'SEARCHING',
              'ACCEPTED',
              'NO_ANSWER_WAIT'
          )
        """,
        (
            message.from_user.id,
        ),
        one=True,
    )

    if active:

        await state.clear()

        await message.answer(
            "⚠️ Sizda allaqachon faol buyurtma bor.",
            reply_markup=main_kb(
                user["lang"]
            ),
        )

        return

    stamp = now()

    order_id = await db_query(
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
            created_at,
            updated_at
        )
        VALUES(?,?,?,?,?,?,?,?,?,?)
        """,
        (
            message.from_user.id,
            data["service"],
            data["passengers"],
            data["route_text"],
            data["lat"],
            data["lon"],
            price,
            "SEARCHING",
            stamp,
            stamp,
        ),
    )

    await log_event(
        order_id,
        message.from_user.id,
        "CREATED",
    )

    await state.clear()

    await message.answer(
        tr(
            user["lang"],
            "created",
            id=order_id,
        ),
        reply_markup=main_kb(
            user["lang"]
        ),
    )

    asyncio.create_task(
        dispatch_order(order_id)
    )

    asyncio.create_task(
        search_timeout(order_id)
    )


# ============================================================
# DISPATCH
# ============================================================

async def dispatch_order(
    order_id: int
):

    order = await db_query(
        """
        SELECT *
        FROM orders
        WHERE id=?
        """,
        (order_id,),
        one=True,
    )

    if not order:
        return

    if order["status"] != "SEARCHING":
        return

    drivers = await db_query(
        """
        SELECT *
        FROM drivers
        WHERE approved=1
          AND blocked=0
          AND online=1
          AND route=?
          AND active_orders < ?
        ORDER BY
            active_orders ASC,
            tg_id ASC
        """,
        (
            ROUTE_CODE,
            MAX_ACTIVE_ORDERS,
        ),
        all_rows=True,
    )

    sent_count = 0

    for driver in drivers:

        if (
            order["excluded_driver"]
            is not None
            and
            driver["tg_id"]
            ==
            order["excluded_driver"]
        ):
            continue

        existing = await db_query(
            """
            SELECT id
            FROM offers
            WHERE order_id=?
              AND driver_id=?
            """,
            (
                order_id,
                driver["tg_id"],
            ),
            one=True,
        )

        if existing:
            continue

        driver_user = await get_user(
            driver["tg_id"]
        )

        if not driver_user:
            continue

        lang = driver_user["lang"]

        if order["service"] == "delivery":

            service_text = tr(
                lang,
                "delivery_service"
            )

        else:

            service_text = tr(
                lang,
                "passenger_service",
                n=order["passengers"],
            )

        text = tr(
            lang,
            "offer_text",
            id=order_id,
            service=service_text,
            price=money(
                order["price"]
            ),
            route=order["route_text"],
        )

        stamp = now()

        try:

            # PENDING holatda yaratamiz.
            # SENT bo‘lmaguncha claim qilish mumkin emas.
            await db_query(
                """
                INSERT OR IGNORE INTO offers(
                    order_id,
                    driver_id,
                    status,
                    message_id,
                    chat_id,
                    created_at,
                    updated_at
                )
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    order_id,
                    driver["tg_id"],
                    "PENDING",
                    0,
                    driver["tg_id"],
                    stamp,
                    stamp,
                ),
            )

            sent_message = await bot.send_message(
                driver["tg_id"],
                text,
                reply_markup=offer_kb(
                    lang,
                    order_id,
                ),
            )

            # HAQIQIY GPS
            await bot.send_location(
                driver["tg_id"],
                order["lat"],
                order["lon"],
            )

            await db_query(
                """
                UPDATE offers
                SET
                    status='SENT',
                    message_id=?,
                    updated_at=?
                WHERE order_id=?
                  AND driver_id=?
                  AND status='PENDING'
                """,
                (
                    sent_message.message_id,
                    now(),
                    order_id,
                    driver["tg_id"],
                ),
            )

            sent_count += 1

        except Exception as exc:

            log.warning(
                "Offer error: order=%s driver=%s error=%s",
                order_id,
                driver["tg_id"],
                exc,
            )

            await db_query(
                """
                DELETE FROM offers
                WHERE order_id=?
                  AND driver_id=?
                  AND status='PENDING'
                """,
                (
                    order_id,
                    driver["tg_id"],
                ),
            )

    if sent_count == 0:

        await db_query(
            """
            UPDATE orders
            SET
                status='NO_DRIVER',
                updated_at=?
            WHERE id=?
              AND status='SEARCHING'
            """,
            (
                now(),
                order_id,
            ),
        )

        customer = await get_user(
            order["customer_id"]
        )

        if customer:

            await bot.send_message(
                order["customer_id"],
                tr(
                    customer["lang"],
                    "no_driver"
                ),
            )


async def search_timeout(
    order_id: int
):

    await asyncio.sleep(
        SEARCH_TIMEOUT
    )

    order = await db_query(
        """
        SELECT *
        FROM orders
        WHERE id=?
        """,
        (order_id,),
        one=True,
    )

    if not order:
        return

    if order["status"] != "SEARCHING":
        return

    await db_query(
        """
        UPDATE orders
        SET
            status='NO_DRIVER',
            updated_at=?
        WHERE id=?
          AND status='SEARCHING'
        """,
        (
            now(),
            order_id,
        ),
    )

    await db_query(
        """
        UPDATE offers
        SET
            status='EXPIRED',
            updated_at=?
        WHERE order_id=?
          AND status IN(
              'SENT',
              'PENDING'
          )
        """,
        (
            now(),
            order_id,
        ),
    )

    await close_offer_buttons(
        order_id
    )

    customer = await get_user(
        order["customer_id"]
    )

    if customer:

        await bot.send_message(
            order["customer_id"],
            tr(
                customer["lang"],
                "no_driver"
            ),
        )


async def close_offer_buttons(
    order_id: int,
    except_driver: Optional[int] = None,
):

    offers = await db_query(
        """
        SELECT *
        FROM offers
        WHERE order_id=?
        """,
        (order_id,),
        all_rows=True,
    )

    for offer in offers:

        if (
            except_driver is not None
            and
            offer["driver_id"]
            ==
            except_driver
        ):
            continue

        if not offer["message_id"]:
            continue

        try:

            await bot.edit_message_reply_markup(
                chat_id=offer["chat_id"],
                message_id=offer["message_id"],
                reply_markup=None,
            )

        except Exception as exc:

            log.debug(
                "Could not close offer: %s",
                exc,
            )


# ============================================================
# DRIVER DECLINE
# ============================================================

@dp.callback_query(
    F.data.startswith("decline:")
)
async def driver_decline(
    callback: CallbackQuery
):

    order_id = int(
        callback.data.split(":")[1]
    )

    driver_id = callback.from_user.id

    changed = await db_query(
        """
        UPDATE offers
        SET
            status='DECLINED',
            updated_at=?
        WHERE order_id=?
          AND driver_id=?
          AND status='SENT'
        """,
        (
            now(),
            order_id,
            driver_id,
        ),
    )

    if changed:

        await callback.answer(
            "❌ Rad etildi."
        )

    else:

        await callback.answer(
            "❌ Bu taklif endi faol emas.",
            show_alert=True,
        )


# ============================================================
# DRIVER CLAIM
# ============================================================

@dp.callback_query(
    F.data.startswith("claim:")
)
async def driver_claim(
    callback: CallbackQuery
):

    order_id = int(
        callback.data.split(":")[1]
    )

    driver_id = callback.from_user.id

    def claim_transaction(connection):

        order = connection.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,),
        ).fetchone()

        driver = connection.execute(
            """
            SELECT *
            FROM drivers
            WHERE tg_id=?
            """,
            (driver_id,),
        ).fetchone()

        if not order or not driver:
            return None

        if order["status"] != "SEARCHING":
            return None

        if (
            order["excluded_driver"]
            ==
            driver_id
        ):
            return None

        if driver["approved"] != 1:
            return None

        if driver["blocked"] != 0:
            return None

        if driver["online"] != 1:
            return None

        if driver["route"] != ROUTE_CODE:
            return None

        if (
            driver["active_orders"]
            >=
            MAX_ACTIVE_ORDERS
        ):
            return None

        offer = connection.execute(
            """
            SELECT *
            FROM offers
            WHERE order_id=?
              AND driver_id=?
              AND status='SENT'
            """,
            (
                order_id,
                driver_id,
            ),
        ).fetchone()

        if not offer:
            return None

        # ATOMIK QABUL
        updated = connection.execute(
            """
            UPDATE orders
            SET
                status='ACCEPTED',
                driver_id=?,
                updated_at=?
            WHERE id=?
              AND status='SEARCHING'
            """,
            (
                driver_id,
                now(),
                order_id,
            ),
        )

        if updated.rowcount != 1:
            return None

        driver_updated = connection.execute(
            """
            UPDATE drivers
            SET
                active_orders=active_orders+1,
                updated_at=?
            WHERE tg_id=?
              AND active_orders < ?
              AND approved=1
              AND blocked=0
              AND online=1
            """,
            (
                now(),
                driver_id,
                MAX_ACTIVE_ORDERS,
            ),
        )

        if driver_updated.rowcount != 1:

            raise RuntimeError(
                "Driver active order update failed"
            )

        # Shu orderdagi barcha boshqa takliflarni yopamiz.
        connection.execute(
            """
            UPDATE offers
            SET
                status=CASE
                    WHEN driver_id=?
                    THEN 'ACCEPTED'
                    ELSE 'CLOSED'
                END,
                updated_at=?
            WHERE order_id=?
              AND status IN(
                  'SENT',
                  'PENDING'
              )
            """,
            (
                driver_id,
                now(),
                order_id,
            ),
        )

        return (
            dict(order),
            dict(driver),
        )

    try:

        result = await db_transaction(
            claim_transaction
        )

    except Exception as exc:

        log.exception(
            "Claim failed: %s",
            exc,
        )

        result = None

    if not result:

        driver_user = await get_user(
            driver_id
        )

        lang = (
            driver_user["lang"]
            if driver_user
            else "uz"
        )

        await callback.answer(
            tr(
                lang,
                "claim_fail"
            ),
            show_alert=True,
        )

        return

    order, driver = result

    # Boshqa haydovchilarning tugmalarini yopamiz.
    await close_offer_buttons(
        order_id,
        except_driver=driver_id,
    )

    customer = await get_user(
        order["customer_id"]
    )

    driver_user = await get_user(
        driver_id
    )

    if not customer or not driver_user:
        await callback.answer(
            "✅ Qabul qilindi."
        )
        return

    # ========================================================
    # MUHIM:
    # MIJOZGA FAQAT:
    #   MASHINA MODELI
    #   DAVLAT RAQAMI
    #   TELEFON
    #
    # MASHINA RASMI YUBORILMAYDI.
    # ========================================================

    await bot.send_message(
        order["customer_id"],
        tr(
            customer["lang"],
            "customer_found",
            car=driver["car_model"],
            plate=driver["plate"],
            phone=driver["phone"],
        ),
    )

    # Haydovchiga mijoz ma'lumotlari.
    await bot.send_message(
        driver_id,
        tr(
            driver_user["lang"],
            "customer_info",
            name=customer["name"],
            phone=customer["phone"],
            route=order["route_text"],
        ),
        reply_markup=accepted_kb(
            driver_user["lang"],
            order_id,
        ),
    )

    # Haydovchiga haqiqiy GPS.
    await bot.send_location(
        driver_id,
        order["lat"],
        order["lon"],
    )

    await log_event(
        order_id,
        driver_id,
        "ACCEPTED",
    )

    await callback.answer(
        "✅ Buyurtma qabul qilindi. "
        "Boshqa haydovchilardagi tugmalar yopildi."
    )

    # Eski offer xabarining tugmasini ham olib tashlaymiz.
    try:

        offer = await db_query(
            """
            SELECT *
            FROM offers
            WHERE order_id=?
              AND driver_id=?
            """,
            (
                order_id,
                driver_id,
            ),
            one=True,
        )

        if offer and offer["message_id"]:

            await bot.edit_message_reply_markup(
                chat_id=offer["chat_id"],
                message_id=offer["message_id"],
                reply_markup=None,
            )

    except Exception as exc:

        log.debug(
            "Could not close accepted offer: %s",
            exc,
        )


# ============================================================
# NO ANSWER
# ============================================================

@dp.callback_query(
    F.data.startswith("noans:")
)
async def driver_no_answer(
    callback: CallbackQuery
):

    order_id = int(
        callback.data.split(":")[1]
    )

    driver_id = callback.from_user.id

    def transaction(connection):

        order = connection.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,),
        ).fetchone()

        if not order:
            return None

        if order["status"] != "ACCEPTED":
            return None

        if order["driver_id"] != driver_id:
            return None

        changed = connection.execute(
            """
            UPDATE orders
            SET
                status='NO_ANSWER_WAIT',
                no_answer_started=?,
                excluded_driver=?,
                updated_at=?
            WHERE id=?
              AND status='ACCEPTED'
              AND driver_id=?
            """,
            (
                time.time(),
                driver_id,
                now(),
                order_id,
                driver_id,
            ),
        )

        if changed.rowcount != 1:
            return None

        # Faol slot bo'shaydi.
        connection.execute(
            """
            UPDATE drivers
            SET
                active_orders=
                    CASE
                        WHEN active_orders>0
                        THEN active_orders-1
                        ELSE 0
                    END,
                updated_at=?
            WHERE tg_id=?
            """,
            (
                now(),
                driver_id,
            ),
        )

        return dict(order)

    order = await db_transaction(
        transaction
    )

    if not order:

        await callback.answer(
            "❌ Bu amal hozir mumkin emas.",
            show_alert=True,
        )

        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    customer = await get_user(
        order["customer_id"]
    )

    if not customer:
        return

    await bot.send_message(
        order["customer_id"],
        tr(
            customer["lang"],
            "no_answer"
        ),
        reply_markup=need_customer_kb(
            customer["lang"],
            order_id,
        ),
    )

    driver_user = await get_user(
        driver_id
    )

    if driver_user:

        await callback.answer(
            tr(
                driver_user["lang"],
                "noanswer_started"
            )
        )

    await log_event(
        order_id,
        driver_id,
        "NO_ANSWER_WAIT",
    )

    asyncio.create_task(
        no_answer_timeout(order_id)
    )


async def no_answer_timeout(
    order_id: int
):

    await asyncio.sleep(
        NO_ANSWER_TIMEOUT
    )

    def transaction(connection):

        order = connection.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,),
        ).fetchone()

        if not order:
            return None

        if order["status"] != "NO_ANSWER_WAIT":
            return None

        started = order["no_answer_started"]

        if not started:
            return None

        if (
            time.time() - started
            <
            NO_ANSWER_TIMEOUT
        ):
            return None

        changed = connection.execute(
            """
            UPDATE orders
            SET
                status='CANCELLED',
                updated_at=?
            WHERE id=?
              AND status='NO_ANSWER_WAIT'
            """,
            (
                now(),
                order_id,
            ),
        )

        if changed.rowcount != 1:
            return None

        return dict(order)

    order = await db_transaction(
        transaction
    )

    if not order:
        return

    customer = await get_user(
        order["customer_id"]
    )

    if customer:

        await bot.send_message(
            order["customer_id"],
            tr(
                customer["lang"],
                "expired"
            ),
            reply_markup=main_kb(
                customer["lang"]
            ),
        )

    await log_event(
        order_id,
        0,
        "NO_ANSWER_TIMEOUT",
    )


# ============================================================
# CUSTOMER YES - SEARCH AGAIN
# ============================================================

@dp.callback_query(
    F.data.startswith("needyes:")
)
async def customer_needs_again(
    callback: CallbackQuery
):

    order_id = int(
        callback.data.split(":")[1]
    )

    def transaction(connection):

        order = connection.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,),
        ).fetchone()

        if not order:
            return None

        if order["status"] != "NO_ANSWER_WAIT":
            return None

        connection.execute(
            """
            UPDATE orders
            SET
                status='SEARCHING',
                driver_id=NULL,
                no_answer_started=NULL,
                updated_at=?
            WHERE id=?
              AND status='NO_ANSWER_WAIT'
            """,
            (
                now(),
                order_id,
            ),
        )

        return dict(order)

    order = await db_transaction(
        transaction
    )

    if not order:

        await callback.answer(
            "❌ Vaqt tugagan yoki buyurtma yopilgan.",
            show_alert=True,
        )

        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.answer(
        "🔎 Qayta haydovchi qidirilmoqda..."
    )

    await log_event(
        order_id,
        callback.from_user.id,
        "REOPENED",
    )

    asyncio.create_task(
        dispatch_order(order_id)
    )

    asyncio.create_task(
        search_timeout(order_id)
    )


# ============================================================
# CUSTOMER NO
# ============================================================

@dp.callback_query(
    F.data.startswith("needno:")
)
async def customer_does_not_need(
    callback: CallbackQuery
):

    order_id = int(
        callback.data.split(":")[1]
    )

    changed = await db_query(
        """
        UPDATE orders
        SET
            status='CANCELLED',
            updated_at=?
        WHERE id=?
          AND status='NO_ANSWER_WAIT'
        """,
        (
            now(),
            order_id,
        ),
    )

    if not changed:

        await callback.answer(
            "❌ Buyurtma allaqachon yopilgan.",
            show_alert=True,
        )

        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.answer(
        "❌ Buyurtma bekor qilindi."
    )

    await log_event(
        order_id,
        callback.from_user.id,
        "CUSTOMER_CANCELLED",
    )


# ============================================================
# FINISH
# ============================================================

@dp.callback_query(
    F.data.startswith("finish:")
)
async def driver_finish(
    callback: CallbackQuery
):

    order_id = int(
        callback.data.split(":")[1]
    )

    driver_id = callback.from_user.id

    def transaction(connection):

        order = connection.execute(
            """
            SELECT *
            FROM orders
            WHERE id=?
            """,
            (order_id,),
        ).fetchone()

        if not order:
            return None

        if order["status"] != "ACCEPTED":
            return None

        if order["driver_id"] != driver_id:
            return None

        changed = connection.execute(
            """
            UPDATE orders
            SET
                status='COMPLETED',
                updated_at=?
            WHERE id=?
              AND status='ACCEPTED'
              AND driver_id=?
            """,
            (
                now(),
                order_id,
                driver_id,
            ),
        )

        if changed.rowcount != 1:
            return None

        connection.execute(
            """
            UPDATE drivers
            SET
                active_orders=
                    CASE
                        WHEN active_orders>0
                        THEN active_orders-1
                        ELSE 0
                    END,
                updated_at=?
            WHERE tg_id=?
            """,
            (
                now(),
                driver_id,
            ),
        )

        return dict(order)

    order = await db_transaction(
        transaction
    )

    if not order:

        await callback.answer(
            "❌ Bu buyurtmani yakunlash mumkin emas.",
            show_alert=True,
        )

        return

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    customer = await get_user(
        order["customer_id"]
    )

    if customer:

        await bot.send_message(
            order["customer_id"],
            tr(
                customer["lang"],
                "completed"
            ),
        )

        await bot.send_message(
            order["customer_id"],
            tr(
                customer["lang"],
                "rate"
            ),
            reply_markup=rating_kb(
                order_id
            ),
        )

    await log_event(
        order_id,
        driver_id,
        "COMPLETED",
    )

    await callback.answer(
        "🏁 Buyurtma yakunlandi."
    )


# ============================================================
# RATING
# ============================================================

@dp.callback_query(
    F.data.startswith("rate:")
)
async def customer_rate(
    callback: CallbackQuery
):

    _, order_id_text, rating_text = (
        callback.data.split(":")
    )

    order_id = int(
        order_id_text
    )

    rating = int(
        rating_text
    )

    if rating < 1 or rating > 5:

        await callback.answer(
            "❌",
            show_alert=True,
        )

        return

    order = await db_query(
        """
        SELECT *
        FROM orders
        WHERE id=?
        """,
        (order_id,),
        one=True,
    )

    if not order:

        await callback.answer(
            "❌ Ruxsat yo‘q.",
            show_alert=True,
        )

        return

    if (
        order["customer_id"]
        !=
        callback.from_user.id
    ):

        await callback.answer(
            "❌ Ruxsat yo‘q.",
            show_alert=True,
        )

        return

    try:

        await db_query(
            """
            INSERT INTO ratings(
                order_id,
                rating,
                created_at
            )
            VALUES(?,?,?)
            """,
            (
                order_id,
                rating,
                now(),
            ),
        )

    except sqlite3.IntegrityError:

        await callback.answer(
            "⭐ Bu buyurtma allaqachon baholangan.",
            show_alert=True,
        )

        return

    await db_query(
        """
        UPDATE drivers
        SET
            rating_sum=rating_sum+?,
            rating_count=rating_count+1,
            updated_at=?
        WHERE tg_id=?
        """,
        (
            rating,
            now(),
            order["driver_id"],
        ),
    )

    await callback.message.edit_reply_markup(
        reply_markup=None
    )

    await callback.answer(
        "🙏 Rahmat!"
    )


# ============================================================
# DRIVER PANEL
# ============================================================

async def open_driver_panel(
    message: Message,
    state: FSMContext,
    user,
):

    lang = user["lang"]

    driver = await get_driver(
        message.from_user.id
    )

    if driver and driver["blocked"]:

        await message.answer(
            tr(
                lang,
                "blocked"
            )
        )

        return

    if driver and driver["approved"]:

        await state.clear()

        await message.answer(
            (
                tr(
                    lang,
                    "online"
                )
                if driver["online"]
                else
                tr(
                    lang,
                    "offline"
                )
            )
            + "\n"
            +
            tr(
                lang,
                "active",
                n=driver["active_orders"],
            ),
            reply_markup=driver_panel_kb(
                lang,
                bool(driver["online"]),
            ),
        )

        return

    await state.clear()

    await state.set_state(
        DriverRegistration.name
    )

    await message.answer(
        tr(
            lang,
            "driver_reg"
        )
        + "\n\n"
        +
        tr(
            lang,
            "name"
        ),
        reply_markup=cancel_kb(lang),
    )


# ============================================================
# DRIVER REGISTRATION
# ============================================================

@dp.message(
    DriverRegistration.name
)
async def driver_reg_name(
    message: Message,
    state: FSMContext,
):

    user = await get_user(
        message.from_user.id
    )

    if not user:
        return

    name = (
        message.text or ""
    ).strip()

    if len(name) < 2:

        await message.answer(
            tr(
                user["lang"],
                "name"
            )
        )

        return

    await state.update_data(
        name=name
    )

    await state.set_state(
        DriverRegistration.phone
    )

    await message.answer(
        tr(
            user["lang"],
            "phone"
        ),
        reply_markup=phone_kb(
            user["lang"]
        ),
    )


@dp.message(
    DriverRegistration.phone
)
async def driver_reg_phone(
    message: Message,
    state: FSMContext,
):

    user = await get_user(
        message.from_user.id
    )

    if not user:
        return

    phone = (
        message.contact.phone_number
        if message.contact
        else (message.text or "").strip()
    )

    if len(phone) < 5:

        await message.answer(
            tr(
                user["lang"],
                "phone"
            ),
            reply_markup=phone_kb(
                user["lang"]
            ),
        )

        return

    await state.update_data(
        phone=phone
    )

    await state.set_state(
        DriverRegistration.car
    )

    await message.answer(
        tr(
            user["lang"],
            "car"
        ),
        reply_markup=cancel_kb(
            user["lang"]
        ),
    )


@dp.message(
    DriverRegistration.car
)
async def driver_reg_car(
    message: Message,
    state: FSMContext,
):

    user = await get_user(
        message.from_user.id
    )

    if not user:
        return

    car = (
        message.text or ""
    ).strip()

    if len(car) < 2:

        await message.answer(
            tr(
                user["lang"],
                "car"
            )
        )

        return

    await state.update_data(
        car=car
    )

    await state.set_state(
        DriverRegistration.plate
    )

    await message.answer(
        tr(
            user["lang"],
            "plate"
        ),
        reply_markup=cancel_kb(
            user["lang"]
        ),
    )


@dp.message(
    DriverRegistration.plate
)
async def driver_reg_plate(
    message: Message,
    state: FSMContext,
):

    user = await get_user(
        message.from_user.id
    )

    if not user:
        return

    plate = (
        message.text or ""
    ).strip()

    if len(plate) < 3:

        await message.answer(
            tr(
                user["lang"],
                "plate"
            )
        )

        return

    await state.update_data(
        plate=plate
    )

    await state.set_state(
        DriverRegistration.route
    )

    await message.answer(
        tr(
            user["lang"],
            "route_driver"
        ),
        reply_markup=reply_kb([
            [
                KeyboardButton(
                    text="OBLIQ ↔ ANGREN"
                )
            ],
            [
                KeyboardButton(
                    text=tr(
                        user["lang"],
                        "cancel"
                    )
                )
            ],
        ]),
    )


@dp.message(
    DriverRegistration.route
)
async def driver_reg_route(
    message: Message,
    state: FSMContext,
):

    user = await get_user(
        message.from_user.id
    )

    if not user:
        return

    # V1 faqat OBLIQ ↔ ANGREN.
    await state.update_data(
        route=ROUTE_CODE
    )

    await state.set_state(
        DriverRegistration.license
    )

    await message.answer(
        tr(
            user["lang"],
            "license"
        ),
        reply_markup=cancel_kb(
            user["lang"]
        ),
    )


@dp.message(
    DriverRegistration.license
)
async def driver_reg_license(
    message: Message,
    state: FSMContext,
):

    user = await get_user(
        message.from_user.id
    )

    if not user:
        return

    if not message.photo:

        await message.answer(
            tr(
                user["lang"],
                "license"
            )
        )

        return

    await state.update_data(
        license_file=message.photo[-1].file_id
    )

    await state.set_state(
        DriverRegistration.tech
    )

    await message.answer(
        tr(
            user["lang"],
            "tech"
        )
    )


@dp.message(
    DriverRegistration.tech
)
async def driver_reg_tech(
    message: Message,
    state: FSMContext,
):

    user = await get_user(
        message.from_user.id
    )

    if not user:
        return

    if not message.photo:

        await message.answer(
            tr(
                user["lang"],
                "tech"
            )
        )

        return

    await state.update_data(
        tech_file=message.photo[-1].file_id
    )

    await state.set_state(
        DriverRegistration.car_photo
    )

    await message.answer(
        tr(
            user["lang"],
            "car_photo"
        )
    )


@dp.message(
    DriverRegistration.car_photo
)
async def driver_reg_car_photo(
    message: Message,
    state: FSMContext,
):

    user = await get_user(
        message.from_user.id
    )

    if not user:
        return

    if not message.photo:

        await message.answer(
            tr(
                user["lang"],
                "car_photo"
            )
        )

        return

    await state.update_data(
        car_file=message.photo[-1].file_id
    )

    await state.set_state(
        DriverRegistration.rules
    )

    await message.answer(
        tr(
            user["lang"],
            "rules"
        ),
        reply_markup=reply_kb([
            [
                KeyboardButton(
                    text=tr(
                        user["lang"],
                        "accept_rules"
                    )
                ),
                KeyboardButton(
                    text=tr(
                        user["lang"],
                        "reject_rules"
                    )
                ),
            ]
        ]),
    )


@dp.message(
    DriverRegistration.rules
)
async def driver_reg_rules(
    message: Message,
    state: FSMContext,
):

    user = await get_user(
        message.from_user.id
    )

    if not user:
        return

    if message.text != tr(
        user["lang"],
        "accept_rules"
    ):

        await state.clear()

        await message.answer(
            "❌ Ro‘yxatdan o‘tish bekor qilindi.",
            reply_markup=main_kb(
                user["lang"]
            ),
        )

        return

    data = await state.get_data()

    stamp = now()

    await db_query(
        """
        INSERT INTO drivers(
            tg_id,
            name,
            phone,
            car_model,
            plate,
            route,
            license_file,
            tech_file,
            car_file,

            approved,
            online,
            blocked,
            active_orders,

            rating_sum,
            rating_count,

            created_at,
            updated_at
        )
        VALUES(
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,
            ?,

            0,
            0,
            0,
            0,

            0,
            0,

            ?,
            ?
        )

        ON CONFLICT(tg_id)
        DO UPDATE SET

            name=excluded.name,
            phone=excluded.phone,
            car_model=excluded.car_model,
            plate=excluded.plate,
            route=excluded.route,

            license_file=excluded.license_file,
            tech_file=excluded.tech_file,
            car_file=excluded.car_file,

            approved=0,
            online=0,
            blocked=0,

            updated_at=excluded.updated_at
        """,
        (
            message.from_user.id,
            data["name"],
            data["phone"],
            data["car"],
            data["plate"],
            ROUTE_CODE,
            data["license_file"],
            data["tech_file"],
            data["car_file"],
            stamp,
            stamp,
        ),
    )

    await state.clear()

    await message.answer(
        tr(
            user["lang"],
            "approval"
        ),
        reply_markup=main_kb(
            user["lang"]
        ),
    )

    if ADMIN_ID:

        await send_driver_application_to_admin(
            message.from_user.id,
            data,
        )


async def send_driver_application_to_admin(
    driver_id,
    data,
):

    try:

        await bot.send_message(
            ADMIN_ID,
            f"🚕 <b>YANGI HAYDOVCHI</b>\n\n"
            f"ID: <code>{driver_id}</code>\n"
            f"👤 {data['name']}\n"
            f"📱 {data['phone']}\n"
            f"🚗 {data['car']}\n"
            f"🔢 {data['plate']}\n"
            f"📍 OBLIQ ↔ ANGREN\n\n"
            f"/admin_approve_{driver_id}\n"
            f"/admin_reject_{driver_id}",
        )

        await bot.send_photo(
            ADMIN_ID,
            data["license_file"],
            caption="🪪 Haydovchilik guvohnomasi",
        )

        await bot.send_photo(
            ADMIN_ID,
            data["tech_file"],
            caption="📄 Texpasport",
        )

        await bot.send_photo(
            ADMIN_ID,
            data["car_file"],
            caption="🚗 Mashina rasmi",
        )

    except Exception as exc:

        log.exception(
            "Admin application error: %s",
            exc,
        )


# ============================================================
# DRIVER ONLINE / OFFLINE
# ============================================================

@dp.message(
    StateFilter(None),
    F.text
)
async def driver_panel_action(
    message: Message,
    state: FSMContext,
):

    user = await get_user(
        message.from_user.id
    )

    if not user:
        return

    driver = await get_driver(
        message.from_user.id
    )

    if not driver:
        return

    if not driver["approved"]:
        return

    lang = user["lang"]

    if message.text == tr(
        lang,
        "go_online"
    ):

        if driver["active_orders"] >= MAX_ACTIVE_ORDERS:

            await message.answer(
                tr(
                    lang,
                    "active",
                    n=driver["active_orders"],
                )
            )

            return

        await db_query(
            """
            UPDATE drivers
            SET
                online=1,
                updated_at=?
            WHERE tg_id=?
              AND approved=1
              AND blocked=0
            """,
            (
                now(),
                message.from_user.id,
            ),
        )

        await message.answer(
            tr(lang, "online")
            + "\n"
            +
            tr(
                lang,
                "active",
                n=driver["active_orders"],
            ),
            reply_markup=driver_panel_kb(
                lang,
                True,
            ),
        )

        return

    if message.text == tr(
        lang,
        "go_offline"
    ):

        await db_query(
            """
            UPDATE drivers
            SET
                online=0,
                updated_at=?
            WHERE tg_id=?
            """,
            (
                now(),
                message.from_user.id,
            ),
        )

        await message.answer(
            tr(lang, "offline")
            + "\n"
            +
            tr(
                lang,
                "active",
                n=driver["active_orders"],
            ),
            reply_markup=driver_panel_kb(
                lang,
                False,
            ),
        )

        return


# ============================================================
# SUPPORT
# ============================================================

@dp.message(
    SupportState.text
)
async def support_message(
    message: Message,
    state: FSMContext,
):

    user = await get_user(
        message.from_user.id
    )

    if not user:
        return

    text = (
        message.text or ""
    ).strip()

    if not text:

        await message.answer(
            tr(
                user["lang"],
                "support_prompt"
            )
        )

        return

    ticket_id = await db_query(
        """
        INSERT INTO support_tickets(
            user_id,
            text,
            created_at
        )
        VALUES(?,?,?)
        """,
        (
            message.from_user.id,
            text,
            now(),
        ),
    )

    await state.clear()

    await message.answer(
        f"{tr(user['lang'], 'saved')}\n"
        f"ID: #{ticket_id}",
        reply_markup=main_kb(
            user["lang"]
        ),
    )

    if ADMIN_ID:

        await bot.send_message(
            ADMIN_ID,
            f"📩 <b>SUPPORT #{ticket_id}</b>\n"
            f"User: <code>{message.from_user.id}</code>\n\n"
            f"{text}",
        )


# ============================================================
# ADMIN
# ============================================================

def is_admin(message: Message):

    return (
        ADMIN_ID != 0
        and
        message.from_user.id == ADMIN_ID
    )


@dp.message(Command("admin"))
async def admin_command(
    message: Message
):

    if not is_admin(message):
        return

    users_count = (
        await db_query(
            """
            SELECT COUNT(*) n
            FROM users
            """,
            one=True,
        )
    )["n"]

    drivers_count = (
        await db_query(
            """
            SELECT COUNT(*) n
            FROM drivers
            """,
            one=True,
        )
    )["n"]

    online_count = (
        await db_query(
            """
            SELECT COUNT(*) n
            FROM drivers
            WHERE online=1
              AND approved=1
              AND blocked=0
            """,
            one=True,
        )
    )["n"]

    today_orders = (
        await db_query(
            """
            SELECT COUNT(*) n
            FROM orders
            WHERE date(created_at)=date('now')
            """,
            one=True,
        )
    )["n"]

    active_orders = (
        await db_query(
            """
            SELECT COUNT(*) n
            FROM orders
            WHERE status IN(
                'SEARCHING',
                'ACCEPTED',
                'NO_ANSWER_WAIT'
            )
            """,
            one=True,
        )
    )["n"]

    await message.answer(
        "🛠 <b>TAXI BOR MI? — ADMIN</b>\n\n"
        f"👤 Users: {users_count}\n"
        f"🚕 Drivers: {drivers_count}\n"
        f"🟢 Online: {online_count}\n"
        f"📦 Bugungi buyurtmalar: {today_orders}\n"
        f"🔥 Faol buyurtmalar: {active_orders}\n\n"
        "/admin_pending\n"
        "/admin_drivers\n"
        "/admin_orders"
    )


@dp.message(
    Command("admin_pending")
)
async def admin_pending(
    message: Message
):

    if not is_admin(message):
        return

    rows = await db_query(
        """
        SELECT *
        FROM drivers
        WHERE approved=0
        ORDER BY created_at DESC
        """,
        all_rows=True,
    )

    if not rows:

        await message.answer(
            "⏳ Pending haydovchi yo‘q."
        )

        return

    for driver in rows:

        await message.answer(
            f"🚕 <b>HAYDOVCHI</b>\n\n"
            f"ID: <code>{driver['tg_id']}</code>\n"
            f"👤 {driver['name']}\n"
            f"📱 {driver['phone']}\n"
            f"🚗 {driver['car_model']}\n"
            f"🔢 {driver['plate']}\n"
            f"📍 OBLIQ ↔ ANGREN\n\n"
            f"/admin_approve_{driver['tg_id']}\n"
            f"/admin_reject_{driver['tg_id']}",
        )

        for file_id, caption in (
            (
                driver["license_file"],
                "🪪 Prava",
            ),
            (
                driver["tech_file"],
                "📄 Texpasport",
            ),
            (
                driver["car_file"],
                "🚗 Mashina",
            ),
        ):

            try:

                await bot.send_photo(
                    ADMIN_ID,
                    file_id,
                    caption=caption,
                )

            except Exception as exc:

                log.warning(
                    "Admin photo error: %s",
                    exc,
                )


@dp.message(
    F.text.regexp(
        r"^/admin_(approve|reject)_\d+$"
    )
)
async def admin_decision(
    message: Message
):

    if not is_admin(message):
        return

    parts = message.text.split("_")

    action = parts[1]

    driver_id = int(
        parts[2]
    )

    driver = await get_driver(
        driver_id
    )

    if not driver:

        await message.answer(
            "❌ Haydovchi topilmadi."
        )

        return

    if action == "approve":

        await db_query(
            """
            UPDATE drivers
            SET
                approved=1,
                blocked=0,
                online=0,
                updated_at=?
            WHERE tg_id=?
            """,
            (
                now(),
                driver_id,
            ),
        )

        await bot.send_message(
            driver_id,
            "✅ Sizning haydovchi arizangiz tasdiqlandi.\n"
            "Botga /start yuboring va Haydovchi bo‘limiga kiring.",
        )

        await message.answer(
            "✅ Haydovchi tasdiqlandi."
        )

    else:

        # Rad etilgan haydovchi qayta ro‘yxatdan o‘ta oladi.
        await db_query(
            """
            DELETE FROM drivers
            WHERE tg_id=?
            """,
            (driver_id,),
        )

        await bot.send_message(
            driver_id,
            "❌ Arizangiz rad etildi.\n"
            "Qaytadan ro‘yxatdan o‘tishingiz mumkin.",
        )

        await message.answer(
            "❌ Ariza rad etildi va o‘chirildi."
        )


@dp.message(
    Command("admin_drivers")
)
async def admin_drivers(
    message: Message
):

    if not is_admin(message):
        return

    rows = await db_query(
        """
        SELECT
            tg_id,
            name,
            phone,
            car_model,
            plate,
            approved,
            online,
            blocked,
            active_orders
        FROM drivers
        ORDER BY created_at DESC
        LIMIT 100
        """,
        all_rows=True,
    )

    if not rows:

        await message.answer(
            "Haydovchilar yo‘q."
        )

        return

    lines = [
        "🚕 <b>HAYDOVCHILAR</b>",
        "",
    ]

    for driver in rows:

        lines.append(
            f"ID: {driver['tg_id']} | "
            f"{driver['name']} | "
            f"{'APPROVED' if driver['approved'] else 'PENDING'} | "
            f"{'ONLINE' if driver['online'] else 'OFFLINE'} | "
            f"{driver['active_orders']}/4"
        )

    await message.answer(
        "\n".join(lines)
    )


@dp.message(
    Command("admin_orders")
)
async def admin_orders(
    message: Message
):

    if not is_admin(message):
        return

    rows = await db_query(
        """
        SELECT *
        FROM orders
        ORDER BY id DESC
        LIMIT 50
        """,
        all_rows=True,
    )

    if not rows:

        await message.answer(
            "📦 Buyurtmalar yo‘q."
        )

        return

    lines = [
        "📦 <b>BUYURTMALAR</b>",
        "",
    ]

    for order in rows:

        lines.append(
            f"#{order['id']} | "
            f"{order['status']} | "
            f"{money(order['price'])} | "
            f"C:{order['customer_id']} | "
            f"D:{order['driver_id'] or '-'}"
        )

    await message.answer(
        "\n".join(lines)
    )


@dp.message(
    Command("admin_block")
)
async def admin_block(
    message: Message
):

    if not is_admin(message):
        return

    parts = message.text.split()

    if len(parts) != 2:

        await message.answer(
            "Format: /admin_block TELEGRAM_ID"
        )

        return

    try:

        tg_id = int(
            parts[1]
        )

    except ValueError:

        await message.answer(
            "❌ ID noto‘g‘ri."
        )

        return

    await db_query(
        """
        UPDATE users
        SET
            blocked=1,
            updated_at=?
        WHERE tg_id=?
        """,
        (
            now(),
            tg_id,
        ),
    )

    await db_query(
        """
        UPDATE drivers
        SET
            blocked=1,
            online=0,
            updated_at=?
        WHERE tg_id=?
        """,
        (
            now(),
            tg_id,
        ),
    )

    await message.answer(
        "🚫 Bloklandi."
    )


@dp.message(
    Command("admin_unblock")
)
async def admin_unblock(
    message: Message
):

    if not is_admin(message):
        return

    parts = message.text.split()

    if len(parts) != 2:

        await message.answer(
            "Format: /admin_unblock TELEGRAM_ID"
        )

        return

    try:

        tg_id = int(
            parts[1]
        )

    except ValueError:

        await message.answer(
            "❌ ID noto‘g‘ri."
        )

        return

    await db_query(
        """
        UPDATE users
        SET
            blocked=0,
            updated_at=?
        WHERE tg_id=?
        """,
        (
            now(),
            tg_id,
        ),
    )

    await db_query(
        """
        UPDATE drivers
        SET
            blocked=0,
            updated_at=?
        WHERE tg_id=?
        """,
        (
            now(),
            tg_id,
        ),
    )

    await message.answer(
        "✅ Blokdan chiqarildi."
    )


@dp.message(
    Command("admin_order")
)
async def admin_order(
    message: Message
):

    if not is_admin(message):
        return

    parts = message.text.split()

    if len(parts) != 2:

        await message.answer(
            "Format: /admin_order ORDER_ID"
        )

        return

    try:

        order_id = int(
            parts[1]
        )

    except ValueError:

        await message.answer(
            "❌ Order ID noto‘g‘ri."
        )

        return

    order = await db_query(
        """
        SELECT *
        FROM orders
        WHERE id=?
        """,
        (
            order_id,
        ),
        one=True,
    )

    if not order:

        await message.answer(
            "❌ Buyurtma topilmadi."
        )

        return

    await message.answer(
        f"📦 <b>ORDER #{order['id']}</b>\n\n"
        f"Status: {order['status']}\n"
        f"Customer: {order['customer_id']}\n"
        f"Driver: {order['driver_id'] or '-'}\n"
        f"Service: {order['service']}\n"
        f"Passengers: {order['passengers']}\n"
        f"Price: {money(order['price'])}\n\n"
        f"Route:\n{order['route_text']}\n\n"
        f"GPS: {order['lat']}, {order['lon']}"
    )


# ============================================================
# FALLBACK
# ============================================================

@dp.message()
async def fallback(
    message: Message,
    state: FSMContext,
):

    if await state.get_state():
        return

    user = await get_user(
        message.from_user.id
    )

    if not user:

        await message.answer(
            "Avval /start ni bosing."
        )

        return

    if user["blocked"]:

        await message.answer(
            tr(
                user["lang"],
                "blocked"
            )
        )

        return

    await message.answer(
        tr(
            user["lang"],
            "main"
        ),
        reply_markup=main_kb(
            user["lang"]
        ),
    )


# ============================================================
# STARTUP
# ============================================================

async def main():

    init_db()

    log.info(
        "TAXI BOR MI? bot started"
    )

    log.info(
        "Route: %s",
        ROUTE_CODE
    )

    log.info(
        "Max active driver orders: %s",
        MAX_ACTIVE_ORDERS
    )

    log.info(
        "Admin configured: %s",
        bool(ADMIN_ID)
    )

    await dp.start_polling(
        bot
    )


if __name__ == "__main__":
    asyncio.run(main())
