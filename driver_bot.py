import os, asyncio
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from database import init_db,get_driver,save_driver,set_driver_online,eligible_drivers,claim_order,get_order,finish_order,start_no_answer,get_customer,reopen_order,cancel_order

TOKEN=os.getenv('DRIVER_BOT_TOKEN','').strip(); CUSTOMER_TOKEN=os.getenv('CUSTOMER_BOT_TOKEN','').strip(); ADMIN_CHAT_ID=int(os.getenv('ADMIN_ID','0') or 0)
if not TOKEN: raise RuntimeError('DRIVER_BOT_TOKEN kerak')
bot=Bot(TOKEN,default=DefaultBotProperties(parse_mode=ParseMode.HTML)); customer_sender=Bot(CUSTOMER_TOKEN,default=DefaultBotProperties(parse_mode=ParseMode.HTML)); dp=Dispatcher(storage=MemoryStorage())
class Reg(StatesGroup): name=State(); phone=State(); car=State(); plate=State(); route=State(); license=State(); tech=State(); photo=State()

def route_kb(): return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='OBLIQ → ANGREN')],[KeyboardButton(text='ANGREN → OBLIQ')]],resize_keyboard=True,one_time_keyboard=True)
def main_kb(online): return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='🔴 OFFLINE' if online else '🟢 ONLINE')]],resize_keyboard=True)
def claim_kb(oid): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='✅ BUYURTMANI QABUL QILISH',callback_data=f'claim:{oid}')]])
def accepted_kb(oid): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='📵 MIJOZ JAVOB BERMADI',callback_data=f'noans:{oid}')],[InlineKeyboardButton(text='🏁 BUYURTMANI YAKUNLASH',callback_data=f'finish:{oid}')]])

@dp.message(CommandStart())
async def start(m:Message,state:FSMContext):
    d=get_driver(m.from_user.id)
    if d:
        if d['blocked']: return await m.answer('🚫 Siz bloklangansiz.')
        if not d['approved']: return await m.answer('⏳ Admin tasdig‘i kutilmoqda.')
        return await m.answer(f"🚗 HAYDOVCHI PANELI\n\n📍 {d['route']}\n📋 Faol buyurtmalar: {d['active_orders']}/4",reply_markup=main_kb(bool(d['online'])))
    await state.set_state(Reg.name); await m.answer('👤 F.I.Sh.ingizni yozing:')

@dp.message(Reg.name)
async def r1(m,state): await state.update_data(name=m.text.strip()); await state.set_state(Reg.phone); await m.answer('📞 Telefon raqamingiz:')
@dp.message(Reg.phone)
async def r2(m,state): await state.update_data(phone=m.contact.phone_number if m.contact else m.text.strip()); await state.set_state(Reg.car); await m.answer('🚗 Mashina modeli:')
@dp.message(Reg.car)
async def r3(m,state): await state.update_data(car_model=m.text.strip()); await state.set_state(Reg.plate); await m.answer('🔢 Davlat raqami:')
@dp.message(Reg.plate)
async def r4(m,state): await state.update_data(plate=m.text.strip()); await state.set_state(Reg.route); await m.answer('📍 Ish yo‘nalishingizni bir marta tanlang:',reply_markup=route_kb())
@dp.message(Reg.route)
async def r5(m,state):
    if m.text not in ('OBLIQ → ANGREN','ANGREN → OBLIQ'): return await m.answer('Yo‘nalishni tanlang.',reply_markup=route_kb())
    await state.update_data(route=m.text); await state.set_state(Reg.license); await m.answer('🪪 Prava rasmini yuboring:')
@dp.message(Reg.license,F.photo)
async def r6(m,state): await state.update_data(license_file=m.photo[-1].file_id); await state.set_state(Reg.tech); await m.answer('📄 Texpasport rasmini yuboring:')
@dp.message(Reg.tech,F.photo)
async def r7(m,state): await state.update_data(tech_file=m.photo[-1].file_id); await state.set_state(Reg.photo); await m.answer('🚗 Mashina rasmini yuboring:')
@dp.message(Reg.photo,F.photo)
async def r8(m,state):
    d=await state.get_data(); d.update(tg_id=m.from_user.id,car_file=m.photo[-1].file_id); save_driver(d); await state.clear(); await m.answer('⏳ Ma’lumotlaringiz admin tasdig‘iga yuborildi.',reply_markup=ReplyKeyboardMarkup(keyboard=[],resize_keyboard=True))
    if ADMIN_CHAT_ID:
        await bot.send_message(ADMIN_CHAT_ID,f"🚗 YANGI HAYDOVCHI\n\n👤 {d['name']}\n📞 {d['phone']}\n🚗 {d['car_model']}\n🔢 {d['plate']}\n📍 {d['route']}\n\n/tasdiq_{m.from_user.id} — tasdiqlash")
        await bot.send_photo(ADMIN_CHAT_ID,d['license_file'],caption='🪪 Prava')
        await bot.send_photo(ADMIN_CHAT_ID,d['tech_file'],caption='📄 Texpasport')
        await bot.send_photo(ADMIN_CHAT_ID,d['car_file'],caption='🚗 Mashina')

@dp.message(F.text.in_({'🟢 ONLINE','🔴 OFFLINE'}))
async def toggle(m):
    d=get_driver(m.from_user.id)
    if not d or not d['approved'] or d['blocked']: return
    online=not bool(d['online']); set_driver_online(m.from_user.id,online); await m.answer(('🟢 ONLINE' if online else '🔴 OFFLINE')+f"\n📋 Faol buyurtmalar: {d['active_orders']}/4",reply_markup=main_kb(online))

@dp.callback_query(F.data.startswith('claim:'))
async def claim(c:CallbackQuery):
    oid=int(c.data.split(':')[1]); d=get_driver(c.from_user.id)
    if not d or not d['approved'] or d['blocked']: return await c.answer('Ruxsat yo‘q',show_alert=True)
    o=claim_order(oid,c.from_user.id)
    if not o: return await c.answer('Bu buyurtma allaqachon olingan.',show_alert=True)
    await c.message.edit_reply_markup(reply_markup=None); await c.message.answer(f"✅ BUYURTMA QABUL QILINDI\n\n🚕 #{oid}\n👤 Mijoz ID: {o['customer_id']}\n📍 {o['route']}\n📝 {o['text']}\n"+(f"📍 GPS: https://maps.google.com/?q={o['lat']},{o['lon']}" if o['lat'] else ''),reply_markup=accepted_kb(oid)); await c.answer('Qabul qilindi')
    cust=get_customer(o['customer_id'])
    if cust:
        await customer_sender.send_message(cust['tg_id'],f"🚕 <b>HAYDOVCHI TOPILDI</b>\n\n👤 Ism: {d['name']}\n🚗 Mashina: {d['car_model']}\n🔢 Raqam: {d['plate']}\n📞 Telefon: {d['phone']}")

@dp.callback_query(F.data.startswith('finish:'))
async def finish(c):
    oid=int(c.data.split(':')[1]);
    if not finish_order(oid,c.from_user.id): return await c.answer('Buyurtma faol emas.',show_alert=True)
    await c.message.edit_reply_markup(reply_markup=None); await c.message.answer('🏁 BUYURTMA YAKUNLANDI');
    o=get_order(oid); cust=get_customer(o['customer_id']) if o else None
    if cust:
        kb=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='1 ⭐',callback_data=f'rate:{oid}:1'),InlineKeyboardButton(text='2 ⭐',callback_data=f'rate:{oid}:2'),InlineKeyboardButton(text='3 ⭐',callback_data=f'rate:{oid}:3'),InlineKeyboardButton(text='4 ⭐',callback_data=f'rate:{oid}:4'),InlineKeyboardButton(text='5 ⭐',callback_data=f'rate:{oid}:5')]])
        await customer_sender.send_message(cust['tg_id'],'⭐ Haydovchini 1–5 baholang:',reply_markup=kb)
    await c.answer()

@dp.callback_query(F.data.startswith('noans:'))
async def noans(c):
    oid=int(c.data.split(':')[1]); until=start_no_answer(oid,c.from_user.id)
    if not until: return await c.answer('Buyurtma faol emas.',show_alert=True)
    await c.message.edit_reply_markup(reply_markup=None); await c.message.answer('📵 Mijoz javob bermadi.\n⏱ Mijozga 1 daqiqa berildi.');
    o=get_order(oid); cust=get_customer(o['customer_id']) if o else None
    if cust:
        await customer_sender.send_message(cust['tg_id'],'📞 Haydovchi siz bilan bog‘lana olmadi.\n\nSizga hali ham mashina kerakmi?\n⏱ 1 daqiqa ichida javob bering.',reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='✅ HA, KERAK',callback_data=f'need:{oid}'),InlineKeyboardButton(text='❌ YO‘Q, KERAK EMAS',callback_data=f'cancel:{oid}')]]))
    asyncio.create_task(auto_cancel(oid))
    await c.answer()

async def auto_cancel(oid):
    await asyncio.sleep(60)
    o=get_order(oid)
    if o and o['status']=='no_answer' and o['no_answer_until'] and o['no_answer_until']<=__import__('time').time():
        cancel_order(oid)
        cust=get_customer(o['customer_id'])
        if cust:
            try: await customer_sender.send_message(cust['tg_id'],'❌ 1 daqiqa ichida javob bo‘lmagani uchun buyurtma bekor qilindi.')
            except Exception: pass

async def main(): init_db(); print('DRIVER BOT RUNNING'); await dp.start_polling(bot)
if __name__=='__main__': asyncio.run(main())
