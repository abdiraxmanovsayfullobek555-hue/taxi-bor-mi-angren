import os
import asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from database import init_db,stats,list_pending_drivers,get_driver,set_driver_approved,set_driver_blocked

TOKEN=os.getenv("ADMIN_BOT_TOKEN","").strip(); ADMIN_ID=int(os.getenv("ADMIN_ID","0") or 0); DRIVER_TOKEN=os.getenv("DRIVER_BOT_TOKEN","").strip()
if not TOKEN: raise RuntimeError("ADMIN_BOT_TOKEN kerak")
if not ADMIN_ID: raise RuntimeError("ADMIN_ID kerak")
if not DRIVER_TOKEN: raise RuntimeError("DRIVER_BOT_TOKEN kerak")

bot=Bot(TOKEN,default=DefaultBotProperties(parse_mode=ParseMode.HTML)); driver_sender=Bot(DRIVER_TOKEN,default=DefaultBotProperties(parse_mode=ParseMode.HTML)); dp=Dispatcher()

def admin_only(uid): return uid==ADMIN_ID
def ok_kb(uid): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ TASDIQLASH",callback_data=f"ok:{uid}"),InlineKeyboardButton(text="❌ RAD ETISH",callback_data=f"no:{uid}")],[InlineKeyboardButton(text="🚫 BLOKLASH",callback_data=f"block:{uid}")]])
def panel(): return "👨‍💼 <b>TAXI BOR MI? — ADMIN</b>\n\n📊 /stat\n🚗 /pending\n👤 /driver ID\n🚫 /block ID\n✅ /unblock ID"

@dp.message(CommandStart())
async def start(m:Message):
    if not admin_only(m.from_user.id): return await m.answer("🚫 Ruxsat yo‘q.")
    await m.answer(panel())

@dp.message(Command("stat"))
async def stat(m:Message):
    if not admin_only(m.from_user.id): return
    s=stats(); await m.answer(f"📊 <b>STATISTIKA</b>\n\n👥 Mijozlar: {s['customers']}\n🚗 Haydovchilar: {s['drivers']}\n🟢 Online: {s['online']}\n⏳ Kutilmoqda: {s['pending']}\n🚕 Buyurtmalar: {s['orders']}\n🔄 Faol: {s['active']}\n🏁 Yakunlangan: {s['finished']}\n❌ Bekor: {s['cancelled']}")

@dp.message(Command("pending"))
async def pending(m:Message):
    if not admin_only(m.from_user.id): return
    rows=list_pending_drivers()
    if not rows: return await m.answer("⏳ Tasdiqlash kutilayotgan haydovchi yo‘q.")
    for d in rows:
        await m.answer(f"🚗 <b>HAYDOVCHI</b> #{d['tg_id']}\n\n👤 {d['name']}\n📞 {d['phone']}\n🚗 {d['car_model']}\n🔢 {d['plate']}\n📍 {d['route']}",reply_markup=ok_kb(d['tg_id']))
        for fid,cap in ((d['license_file'],"🪪 Prava"),(d['tech_file'],"📄 Texpasport"),(d['car_file'],"🚗 Mashina")):
            if fid:
                try: await bot.send_photo(m.chat.id,fid,caption=cap)
                except Exception: pass

async def notify_driver(uid,text):
    try: await driver_sender.send_message(uid,text)
    except Exception: pass

@dp.callback_query(F.data.startswith("ok:"))
async def approve(c:CallbackQuery):
    if not admin_only(c.from_user.id): return await c.answer("Ruxsat yo‘q",show_alert=True)
    uid=int(c.data.split(":")[1]); d=get_driver(uid)
    if not d: return await c.answer("Haydovchi topilmadi.",show_alert=True)
    set_driver_approved(uid,1); await c.message.edit_reply_markup(reply_markup=None); await c.message.answer("✅ Haydovchi tasdiqlandi."); await notify_driver(uid,"✅ Siz admin tomonidan tasdiqlandingiz. /start ni bosing va ONLINE bo‘ling."); await c.answer()

@dp.callback_query(F.data.startswith("no:"))
async def reject(c:CallbackQuery):
    if not admin_only(c.from_user.id): return await c.answer("Ruxsat yo‘q",show_alert=True)
    uid=int(c.data.split(":")[1]); d=get_driver(uid)
    if not d: return await c.answer("Haydovchi topilmadi.",show_alert=True)
    set_driver_approved(uid,0); await c.message.edit_reply_markup(reply_markup=None); await c.message.answer("❌ Haydovchi arizasi rad etildi."); await notify_driver(uid,"❌ Haydovchi arizangiz admin tomonidan rad etildi."); await c.answer()

@dp.callback_query(F.data.startswith("block:"))
async def block(c:CallbackQuery):
    if not admin_only(c.from_user.id): return await c.answer("Ruxsat yo‘q",show_alert=True)
    uid=int(c.data.split(":")[1]); set_driver_blocked(uid,1); await c.message.edit_reply_markup(reply_markup=None); await c.message.answer("🚫 Haydovchi bloklandi."); await notify_driver(uid,"🚫 Siz admin tomonidan bloklandingiz."); await c.answer()

@dp.message(Command("driver"))
async def driver_cmd(m:Message):
    if not admin_only(m.from_user.id): return
    parts=(m.text or "").split()
    if len(parts)!=2 or not parts[1].isdigit(): return await m.answer("/driver TELEGRAM_ID")
    d=get_driver(int(parts[1]))
    if not d: return await m.answer("Topilmadi.")
    avg=(d["rating_sum"]/d["rating_count"]) if d["rating_count"] else 0
    await m.answer(f"🚗 <b>{d['name']}</b>\n📞 {d['phone']}\n🚗 {d['car_model']}\n🔢 {d['plate']}\n📍 {d['route']}\n🟢 Online: {bool(d['online'])}\n📋 Faol: {d['active_orders']}/4\n⭐ Reyting: {avg:.2f}")

@dp.message(Command("block"))
async def block_cmd(m:Message):
    if not admin_only(m.from_user.id): return
    parts=(m.text or "").split()
    if len(parts)!=2 or not parts[1].isdigit(): return await m.answer("/block ID")
    set_driver_blocked(int(parts[1]),1); await m.answer("🚫 Bloklandi.")

@dp.message(Command("unblock"))
async def unblock_cmd(m:Message):
    if not admin_only(m.from_user.id): return
    parts=(m.text or "").split()
    if len(parts)!=2 or not parts[1].isdigit(): return await m.answer("/unblock ID")
    set_driver_blocked(int(parts[1]),0); await m.answer("✅ Blokdan chiqarildi.")

async def main():
    init_db(); print("ADMIN BOT RUNNING"); await dp.start_polling(bot)

if __name__=="__main__": asyncio.run(main())
