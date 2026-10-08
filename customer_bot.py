import os
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from database import init_db,get_customer,save_customer,create_order,set_order_location,latest_open_order,get_order,reopen_order,cancel_order,rate_order,eligible_drivers

TOKEN=os.getenv("CUSTOMER_BOT_TOKEN","").strip()
DRIVER_TOKEN=os.getenv("DRIVER_BOT_TOKEN","").strip()
if not TOKEN: raise RuntimeError("CUSTOMER_BOT_TOKEN kerak")
if not DRIVER_TOKEN: raise RuntimeError("DRIVER_BOT_TOKEN kerak")

bot=Bot(TOKEN,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
driver_sender=Bot(DRIVER_TOKEN,default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp=Dispatcher(storage=MemoryStorage())

LANGS={"🇺🇿 O‘zbekcha":"uz","🇺🇿 Ўзбекча":"uzc"}
TXT={
 "uz":{"name":"👤 Ismingizni yozing:","phone":"📞 Telefon raqamingizni yuboring:","route":"📍 Yo‘nalishingizni tanlang:","welcome":"✅ Tayyor. Endi buyurtmangizni oddiy xabar qilib yozing.","order":"🚕 Buyurtmangiz qabul qilindi.","gps":"📍 GPS buyurtmaga qo‘shildi.","blocked":"🚫 Akkauntingiz bloklangan."},
 "uzc":{"name":"👤 Исмингизни ёзинг:","phone":"📞 Телефон рақамингизни юборинг:","route":"📍 Йўналишингизни танланг:","welcome":"✅ Тайёр. Энди буюртмангизни оддий хабар қилиб ёзинг.","order":"🚕 Буюртмангиз қабул қилинди.","gps":"📍 GPS буюртмага қўшилди.","blocked":"🚫 Аккаунтингиз блокланган."}
}
class Reg(StatesGroup):
    lang=State(); name=State(); phone=State(); route=State()

def empty_kb(): return ReplyKeyboardMarkup(keyboard=[],resize_keyboard=True)
def lang_kb(): return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🇺🇿 O‘zbekcha"),KeyboardButton(text="🇺🇿 Ўзбекча")]],resize_keyboard=True)
def phone_kb(): return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="📞 Telefon raqamim",request_contact=True)]],resize_keyboard=True,one_time_keyboard=True)
def route_kb(): return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="OBLIQ → ANGREN")],[KeyboardButton(text="ANGREN → OBLIQ")]],resize_keyboard=True,one_time_keyboard=True)
def answer_kb(oid): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ HA, KERAK",callback_data=f"need:{oid}"),InlineKeyboardButton(text="❌ YO‘Q, KERAK EMAS",callback_data=f"cancel:{oid}")]])
def rating_kb(oid): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f"{i} ⭐",callback_data=f"rate:{oid}:{i}") for i in range(1,6)]])

@dp.message(CommandStart())
async def start(m:Message,state:FSMContext):
    u=get_customer(m.from_user.id)
    if u:
        if u["blocked"]: return await m.answer(TXT[u["lang"]]["blocked"])
        return await m.answer(TXT[u["lang"]]["welcome"],reply_markup=empty_kb())
    await state.set_state(Reg.lang)
    await m.answer("Tilni tanlang / Тилни танланг:",reply_markup=lang_kb())

@dp.message(Reg.lang)
async def reg_lang(m:Message,state:FSMContext):
    if m.text not in LANGS: return await m.answer("Tilni tanlang.",reply_markup=lang_kb())
    lang=LANGS[m.text]; await state.update_data(lang=lang); await state.set_state(Reg.name)
    await m.answer(TXT[lang]["name"],reply_markup=empty_kb())

@dp.message(Reg.name)
async def reg_name(m:Message,state:FSMContext):
    d=await state.get_data(); name=(m.text or "").strip()
    if len(name)<2: return await m.answer(TXT[d["lang"]]["name"])
    await state.update_data(name=name); await state.set_state(Reg.phone); await m.answer(TXT[d["lang"]]["phone"],reply_markup=phone_kb())

@dp.message(Reg.phone)
async def reg_phone(m:Message,state:FSMContext):
    d=await state.get_data(); phone=(m.contact.phone_number if m.contact else (m.text or "").strip())
    if len(phone)<5: return await m.answer(TXT[d["lang"]]["phone"],reply_markup=phone_kb())
    await state.update_data(phone=phone); await state.set_state(Reg.route); await m.answer(TXT[d["lang"]]["route"],reply_markup=route_kb())

@dp.message(Reg.route)
async def reg_route(m:Message,state:FSMContext):
    if m.text not in ("OBLIQ → ANGREN","ANGREN → OBLIQ"): return await m.answer("Yo‘nalishni tanlang.",reply_markup=route_kb())
    d=await state.get_data(); save_customer(m.from_user.id,d["name"],d["phone"],d["lang"],m.text); await state.clear()
    await m.answer(TXT[d["lang"]]["welcome"],reply_markup=empty_kb())

@dp.message(F.location)
async def location(m:Message):
    u=get_customer(m.from_user.id)
    if not u: return
    if u["blocked"]: return await m.answer(TXT[u["lang"]]["blocked"])
    o=latest_open_order(m.from_user.id)
    if not o: return await m.answer("Hozir ochiq buyurtma yo‘q.")
    set_order_location(o["id"],m.location.latitude,m.location.longitude)
    await m.answer(TXT[u["lang"]]["gps"])

@dp.message(F.text)
async def order(m:Message):
    u=get_customer(m.from_user.id)
    if not u or u["blocked"]: return
    text=(m.text or "").strip()
    if not text: return
    oid=create_order(m.from_user.id,u["route"],text)
    await m.answer(f"{TXT[u['lang']]['order']}\n\n🚕 Buyurtma #{oid}\n📍 {u['route']}\n\nHaydovchi qidirilmoqda...")
    await send_order_to_drivers(oid)

async def send_order_to_drivers(oid):
    o=get_order(oid)
    if not o: return
    rows=eligible_drivers(o["route"],o["excluded_driver"])
    if not rows:
        u=get_customer(o["customer_id"])
        if u:
            await bot.send_message(u["tg_id"],"⚠️ Hozircha online va mos haydovchi topilmadi. Buyurtma qidiruvda qoladi.")
        return
    text=f"🚕 <b>YANGI BUYURTMA #{oid}</b>\n\n📝 {o['text']}\n📍 {o['route']}"
    if o["lat"] is not None: text+=f"\n📍 GPS: https://maps.google.com/?q={o['lat']},{o['lon']}"
    markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ BUYURTMANI QABUL QILISH",callback_data=f"claim:{oid}")]])
    async def send(d):
        try: await driver_sender.send_message(d["tg_id"],text,reply_markup=markup)
        except Exception: pass
    await asyncio.gather(*(send(d) for d in rows))

@dp.callback_query(F.data.startswith("need:"))
async def need(c:CallbackQuery):
    oid=int(c.data.split(":")[1]); o=get_order(oid)
    if not o or o["customer_id"]!=c.from_user.id or o["status"]!="no_answer": return await c.answer("Buyurtma faol emas.",show_alert=True)
    if not reopen_order(oid): return await c.answer("Buyurtma faol emas.",show_alert=True)
    await c.message.edit_reply_markup(reply_markup=None); await c.message.answer("🔎 Buyurtma qayta qidirilmoqda."); await send_order_to_drivers(oid); await c.answer()

@dp.callback_query(F.data.startswith("cancel:"))
async def cancel(c:CallbackQuery):
    oid=int(c.data.split(":")[1]); o=get_order(oid)
    if not o or o["customer_id"]!=c.from_user.id or o["status"]!="no_answer": return await c.answer("Buyurtma faol emas.",show_alert=True)
    cancel_order(oid); await c.message.edit_reply_markup(reply_markup=None); await c.message.answer("❌ Buyurtma bekor qilindi."); await c.answer()

@dp.callback_query(F.data.startswith("rate:"))
async def rate(c:CallbackQuery):
    parts=c.data.split(":")
    if len(parts)!=3: return await c.answer("Xato.",show_alert=True)
    oid,r=int(parts[1]),int(parts[2])
    if rate_order(oid,c.from_user.id,r):
        await c.message.edit_reply_markup(reply_markup=None); await c.message.answer("⭐ Rahmat! Bahongiz saqlandi.")
    else: await c.answer("Bahoni saqlab bo‘lmadi.",show_alert=True)
    await c.answer()

async def main():
    init_db(); print("CUSTOMER BOT RUNNING"); await dp.start_polling(bot)

if __name__=="__main__": asyncio.run(main())
