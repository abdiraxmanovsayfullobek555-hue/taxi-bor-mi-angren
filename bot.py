import os, re, sqlite3, asyncio, logging
from datetime import datetime, timedelta
from typing import Optional
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import Message, CallbackQuery, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0
if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN Railway Variables ichida topilmadi.")

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("taxi_bor_mi")
bot = Bot(BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())
DB_PATH = os.getenv("DB_PATH", "taxi_bor_mi.db")
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row
lock = asyncio.Lock()

def now(): return datetime.utcnow().replace(microsecond=0).isoformat()
def money(n): return f"{int(n):,}".replace(",", " ") + " so‘m"
async def q(sql, params=(), fetch=False, one=False):
    async with lock:
        cur=db.execute(sql,params); db.commit()
        return cur.fetchone() if one else (cur.fetchall() if fetch else cur.lastrowid)

def init_db():
    db.executescript('''
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY AUTOINCREMENT,tg_id INTEGER UNIQUE NOT NULL,role TEXT NOT NULL DEFAULT 'customer',lang TEXT NOT NULL DEFAULT 'uz',name TEXT DEFAULT '',phone TEXT DEFAULT '',blocked INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS drivers(id INTEGER PRIMARY KEY AUTOINCREMENT,tg_id INTEGER UNIQUE NOT NULL,full_name TEXT NOT NULL,phone TEXT NOT NULL,car_model TEXT NOT NULL,plate TEXT UNIQUE NOT NULL,license_file_id TEXT DEFAULT '',tech_file_id TEXT DEFAULT '',car_photo_file_id TEXT DEFAULT '',route TEXT NOT NULL DEFAULT 'OBLIQ_ANGREN',approved INTEGER NOT NULL DEFAULT 0,online INTEGER NOT NULL DEFAULT 0,active_orders INTEGER NOT NULL DEFAULT 0,rating REAL NOT NULL DEFAULT 5.0,rating_count INTEGER NOT NULL DEFAULT 0,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS orders(id INTEGER PRIMARY KEY AUTOINCREMENT,customer_tg_id INTEGER NOT NULL,driver_tg_id INTEGER,service TEXT NOT NULL,origin TEXT NOT NULL,destination TEXT NOT NULL,origin_lat REAL,origin_lon REAL,passengers INTEGER DEFAULT 1,price INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'SEARCHING',created_at TEXT NOT NULL,accepted_at TEXT,finished_at TEXT);
    CREATE TABLE IF NOT EXISTS offers(id INTEGER PRIMARY KEY AUTOINCREMENT,order_id INTEGER NOT NULL,driver_tg_id INTEGER NOT NULL,status TEXT NOT NULL DEFAULT 'SENT',created_at TEXT NOT NULL,UNIQUE(order_id,driver_tg_id));
    CREATE TABLE IF NOT EXISTS ratings(id INTEGER PRIMARY KEY AUTOINCREMENT,order_id INTEGER UNIQUE NOT NULL,from_tg_id INTEGER NOT NULL,to_tg_id INTEGER NOT NULL,score INTEGER NOT NULL,created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS support_tickets(id INTEGER PRIMARY KEY AUTOINCREMENT,tg_id INTEGER NOT NULL,text TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'OPEN',created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS complaints(id INTEGER PRIMARY KEY AUTOINCREMENT,order_id INTEGER,reporter_tg_id INTEGER NOT NULL,text TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'NEW',created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT,actor_tg_id INTEGER,action TEXT NOT NULL,details TEXT DEFAULT '',created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
    CREATE INDEX IF NOT EXISTS idx_drivers_online ON drivers(approved,online,active_orders);
    '''); db.commit()

LANG={
'uz':{'name':'O‘zbekcha','askname':'👤 Ismingizni kiriting:','phone':'📞 Telefon raqamingizni yuboring:','sendphone':'📱 Telefon raqamimni yuborish','order':'🚕 BUYURTMA BERISH','orders':'📋 BUYURTMALARIM','profile':'👤 PROFIL','lang':'🌐 TIL','help':'☎️ YORDAM','service':'🚕 BUYURTMA BERISH\n\nNecha kishi?','one':'1️⃣ 1 kishi','two':'2️⃣ 2 kishi','three':'3️⃣ 3 kishi','four':'4️⃣ 4 kishi','delivery':'📦 DASTAVKA','from':'📍 QAYERDAN YO‘LGA CHIQASIZ?','fromex':'Masalan: 5/5 dan\nObliqdan\nObliq 5/5 dan','to':'🏁 QAYERGA BORASIZ?','toex':'Masalan: Kaltsoga\nAngren markaziga\nAngren hokimiyatiga','gps':'📍 Olib ketish joyingizni GPS orqali yuborishingiz mumkin.\n\nGPS yuborish shart emas.','gpssend':'📍 GPS YUBORISH','gpsskip':'⏭ GPSSIZ DAVOM ETISH','price':'💰 SAFAR NARXINI TANLANG:','other':'✍️ Boshqa narx','confirm':'[unused]','edit':'✏️ O‘ZGARTIRISH','cancel':'❌ BEKOR QILISH','yes':'✅ HA, KERAK','no':'❌ YO‘Q, KERAK EMAS','rating':'⭐ Haydovchini baholang:','online':'🟢 ONLINE','offline':'🔴 OFFLINE'},
'uzc':{'name':'Ўзбекча','askname':'👤 Исмингизни киритинг:','phone':'📞 Телефон рақамингизни юборинг:','sendphone':'📱 Телефон рақамимни юбориш','order':'🚕 БУЮРТМА БЕРИШ','orders':'📋 БУЮРТМАЛАРИМ','profile':'👤 ПРОФИЛ','lang':'🌐 ТИЛ','help':'☎️ ЁРДАМ','service':'🚕 БУЮРТМА БЕРИШ\n\nНеча киши?','one':'1️⃣ 1 киши','two':'2️⃣ 2 киши','three':'3️⃣ 3 киши','four':'4️⃣ 4 киши','delivery':'📦 ДАСТАВКА','from':'📍 ҚАЕРДАН ЙЎЛГА ЧИҚАСИЗ?','fromex':'Масалан: 5/5 дан\nОблиқдан','to':'🏁 ҚАЕРГА БОРАСИЗ?','toex':'Масалан: Калцога\nАнгрен марказига\nАнгрен ҳокимиятига','gps':'📍 Олиб кетиш жойингизни GPS орқали юборишингиз мумкин.\n\nGPS шарт эмас.','gpssend':'📍 GPS ЮБОРИШ','gpsskip':'⏭ GPSСИЗ ДАВОМ ЭТИШ','price':'💰 САФАР НАРХИНИ ТАНЛАНГ:','other':'✍️ Бошқа нарх','edit':'✏️ ЎЗГАРТИРИШ','cancel':'❌ БЕКОР ҚИЛИШ','yes':'✅ ҲА, КЕРАК','no':'❌ ЙЎҚ, КЕРАК ЭМАС','rating':'⭐ Ҳайдовчини баҳоланг:','online':'🟢 ONLINE','offline':'🔴 OFFLINE'},
'ru':{'name':'Русский','askname':'👤 Введите ваше имя:','phone':'📞 Отправьте номер телефона:','sendphone':'📱 Отправить мой номер','order':'🚕 ЗАКАЗАТЬ','orders':'📋 МОИ ЗАКАЗЫ','profile':'👤 ПРОФИЛЬ','lang':'🌐 ЯЗЫК','help':'☎️ ПОМОЩЬ','service':'🚕 ЗАКАЗ\n\nСколько пассажиров?','one':'1️⃣ 1 пассажир','two':'2️⃣ 2 пассажира','three':'3️⃣ 3 пассажира','four':'4️⃣ 4 пассажира','delivery':'📦 ДОСТАВКА','from':'📍 ОТКУДА?','fromex':'Например: 5/5 дан\nОблик','to':'🏁 КУДА?','toex':'Например: Кальцога\nцентр Ангрена','gps':'📍 Можно отправить геолокацию места посадки.\n\nGPS не обязателен.','gpssend':'📍 ОТПРАВИТЬ GPS','gpsskip':'⏭ ПРОДОЛЖИТЬ БЕЗ GPS','price':'💰 ВЫБЕРИТЕ ЦЕНУ:','other':'✍️ Другая цена','edit':'✏️ ИЗМЕНИТЬ','cancel':'❌ ОТМЕНА','yes':'✅ ДА, НУЖНА','no':'❌ НЕТ, НЕ НУЖНА','rating':'⭐ Оцените водителя:','online':'🟢 ONLINE','offline':'🔴 OFFLINE'},
'en':{'name':'English','askname':'👤 Enter your name:','phone':'📞 Send your phone number:','sendphone':'📱 Send my phone number','order':'🚕 ORDER A TAXI','orders':'📋 MY ORDERS','profile':'👤 PROFILE','lang':'🌐 LANGUAGE','help':'☎️ HELP','service':'🚕 ORDER\n\nHow many passengers?','one':'1️⃣ 1 passenger','two':'2️⃣ 2 passengers','three':'3️⃣ 3 passengers','four':'4️⃣ 4 passengers','delivery':'📦 DELIVERY','from':'📍 WHERE FROM?','fromex':'Example: 5/5 dan\nObliq','to':'🏁 WHERE TO?','toex':'Example: Kaltsoga\nAngren center','gps':'📍 You can send your pickup location by GPS.\n\nGPS is optional.','gpssend':'📍 SEND GPS','gpsskip':'⏭ CONTINUE WITHOUT GPS','price':'💰 CHOOSE PRICE:','other':'✍️ Other price','edit':'✏️ EDIT','cancel':'❌ CANCEL','yes':'✅ YES, NEEDED','no':'❌ NO, NOT NEEDED','rating':'⭐ Rate the driver:','online':'🟢 ONLINE','offline':'🔴 OFFLINE'}}
LANG_NAMES={'🇺🇿 O‘zbekcha':'uz','🇺🇿 Ўзбекча':'uzc','🇷🇺 Русский':'ru','🇬🇧 English':'en'}

def kb(rows, resize=True): return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=x,request_location=(x.startswith('📍 GPS') or x.startswith('📍 ЖПС') or x.startswith('📍 ОТПРАВИТЬ'))) for x in row] for row in rows],resize_keyboard=resize)
def lang_kb(): return kb([['🇺🇿 O‘zbekcha','🇺🇿 Ўзбекча'],['🇷🇺 Русский','🇬🇧 English']])
def main_kb(l):
 t=LANG[l]; return kb([[t['order']],[t['orders'],t['profile']],[t['lang'],t['help']]])
def service_kb(l):
 t=LANG[l]; return kb([[t['one'],t['two']],[t['three'],t['four']],[t['delivery']]])
def gps_kb(l):
 t=LANG[l]; return kb([[t['gpssend']],[t['gpsskip']]])
def price_kb(l):
 t=LANG[l]; return kb([['5 000 so‘m','10 000 so‘m'],['15 000 so‘m','20 000 so‘m'],[t['other']]])
def confirm_kb(l):
 t=LANG[l]; return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='✅ BUYURTMANI TASDIQLASH',callback_data='oc')],[InlineKeyboardButton(text=t['edit'],callback_data='oe'),InlineKeyboardButton(text=t['cancel'],callback_data='ox')]])

def get_lang(u): return u['lang'] if u and u['lang'] in LANG else 'uz'
async def user(tg): return await q('SELECT * FROM users WHERE tg_id=?',(tg,),one=True)
async def driver(tg): return await q('SELECT * FROM drivers WHERE tg_id=?',(tg,),one=True)
async def audit(actor,action,details=''): await q('INSERT INTO audit_logs(actor_tg_id,action,details,created_at) VALUES(?,?,?,?)',(actor,action,details,now()))
async def admin_send(text,markup=None):
 if ADMIN_ID:
  try: await bot.send_message(ADMIN_ID,text,reply_markup=markup)
  except Exception as e: log.warning('admin send: %s',e)

class Reg(StatesGroup): lang=State(); name=State(); phone=State()
class Order(StatesGroup): service=State(); origin=State(); destination=State(); gps=State(); price=State(); confirm=State(); other_price=State()
class DriverReg(StatesGroup): name=State(); phone=State(); car=State(); plate=State(); license=State(); tech=State(); photo=State(); rules=State()
class Support(StatesGroup): text=State()

@dp.message(CommandStart())
async def start(m:Message,s:FSMContext):
 u=await user(m.from_user.id)
 if u and u['blocked']: return await m.answer('🚫 Akkauntingiz bloklangan.')
 if u:
  await m.answer('🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n📍 OBLIQ ↔ ANGREN\n\nXizmatni tanlang:',reply_markup=main_kb(get_lang(u))); return
 await s.clear(); await s.set_state(Reg.lang); await m.answer('🚕 <b>TAXI BOR MI? — ALBATTA BOR!</b>\n\nTilni tanlang / Выберите язык / Choose language:',reply_markup=lang_kb())

@dp.message(Reg.lang)
async def reg_lang(m:Message,s:FSMContext):
 l=LANG_NAMES.get(m.text)
 if not l: return await m.answer('Iltimos, tilni tanlang.',reply_markup=lang_kb())
 existing=await user(m.from_user.id)
 if existing:
  await q('UPDATE users SET lang=? WHERE tg_id=?',(l,m.from_user.id)); await s.clear(); await m.answer('✅ Til o‘zgartirildi.',reply_markup=main_kb(l)); return
 await s.update_data(lang=l); await s.set_state(Reg.name); await m.answer(LANG[l]['askname'])
@dp.message(Reg.name)
async def reg_name(m:Message,s:FSMContext):
 if len((m.text or '').strip())<2: return await m.answer('❗ Ismni to‘liqroq kiriting.')
 await s.update_data(name=m.text.strip()); await s.set_state(Reg.phone); l=(await s.get_data())['lang']; await m.answer(LANG[l]['phone'],reply_markup=kb([[LANG[l]['sendphone']]]))
@dp.message(Reg.phone,F.contact)
async def reg_contact(m:Message,s:FSMContext): await finish_reg(m,s,m.contact.phone_number)
@dp.message(Reg.phone)
async def reg_phone(m:Message,s:FSMContext):
 p=(m.text or '').strip()
 if len(re.sub(r'\D','',p))<7: return await m.answer('📞 Telefon raqamini to‘g‘ri yuboring.')
 await finish_reg(m,s,p)
async def finish_reg(m,s,p):
 d=await s.get_data(); await q('INSERT INTO users(tg_id,role,lang,name,phone,created_at) VALUES(?,?,?,?,?,?)',(m.from_user.id,'customer',d['lang'],d['name'],p,now())); await s.clear(); await m.answer('✅ Ro‘yxatdan o‘tish yakunlandi.\n\n🚕 OBLIQ ↔ ANGREN',reply_markup=main_kb(d['lang']))

async def begin_order(m,s):
 u=await user(m.from_user.id)
 if not u: return await m.answer('Avval /start bosing.')
 if u['blocked']: return await m.answer('🚫 Akkauntingiz bloklangan.')
 await s.clear(); await s.set_state(Order.service); await s.update_data(lang=u['lang']); await m.answer(LANG[u['lang']]['service'],reply_markup=service_kb(u['lang']))
@dp.message(F.text.func(lambda x: x in {LANG['uz']['order'],LANG['uzc']['order'],LANG['ru']['order'],LANG['en']['order']}))
async def order_start(m:Message,s:FSMContext): await begin_order(m,s)
@dp.message(Order.service)
async def service(m:Message,s:FSMContext):
 d=await s.get_data(); l=d['lang']; t=LANG[l]; mp={t['one']:1,t['two']:2,t['three']:3,t['four']:4,t['delivery']:'DELIVERY'}
 if m.text not in mp: return await m.answer(t['service'],reply_markup=service_kb(l))
 v=mp[m.text]; await s.update_data(service=v,passengers=(v if isinstance(v,int) else 1)); await s.set_state(Order.origin); await m.answer(t['from']+'\n\n✍️ '+t['fromex'])
@dp.message(Order.origin)
async def origin(m:Message,s:FSMContext):
 v=(m.text or '').strip()
 if len(v)<2: return await m.answer('❗ Manzilni kiriting.')
 d=await s.get_data(); await s.update_data(origin=v); await s.set_state(Order.destination); await m.answer(LANG[d['lang']]['to']+'\n\n✍️ '+LANG[d['lang']]['toex'])
@dp.message(Order.destination)
async def destination(m:Message,s:FSMContext):
 v=(m.text or '').strip()
 if len(v)<2: return await m.answer('❗ Manzilni kiriting.')
 d=await s.get_data(); await s.update_data(destination=v); await s.set_state(Order.gps); await m.answer(LANG[d['lang']]['gps'],reply_markup=gps_kb(d['lang']))
@dp.message(Order.gps,F.location)
async def gps(m:Message,s:FSMContext):
 await s.update_data(lat=m.location.latitude,lon=m.location.longitude); await ask_price(m,s)
@dp.message(Order.gps)
async def gps_skip(m:Message,s:FSMContext):
 d=await s.get_data(); t=LANG[d['lang']]
 if m.text==t['gpsskip']: await ask_price(m,s)
 else: await m.answer(t['gps'],reply_markup=gps_kb(d['lang']))
async def ask_price(m,s):
 d=await s.get_data(); await s.set_state(Order.price); await m.answer(LANG[d['lang']]['price'],reply_markup=price_kb(d['lang']))
@dp.message(Order.price)
async def price(m:Message,s:FSMContext):
 d=await s.get_data(); t=LANG[d['lang']]; mp={'5 000 so‘m':5000,'10 000 so‘m':10000,'15 000 so‘m':15000,'20 000 so‘m':20000}
 if m.text==t['other']: await s.set_state(Order.other_price); return await m.answer('💰 Narxni so‘mda kiriting:')
 if m.text not in mp: return await m.answer(t['price'],reply_markup=price_kb(d['lang']))
 await build_confirm(m,s,mp[m.text])
@dp.message(Order.other_price)
async def other_price(m:Message,s:FSMContext):
 raw=re.sub(r'\D','',m.text or '')
 if not raw: return await m.answer('💰 Narxni raqamda kiriting.')
 p=int(raw)
 if p<5000 or p>1000000: return await m.answer('❗ Minimal narx 5 000 so‘m, maksimal 1 000 000 so‘m.')
 await build_confirm(m,s,p)
async def build_confirm(m,s,p):
 d=await s.get_data(); await s.update_data(price=p); await s.set_state(Order.confirm); typ='📦 DASTAVKA' if d['service']=='DELIVERY' else '👤 Xizmat: Yo‘lovchi\n👥 Yo‘lovchilar: %s kishi'%d['passengers']; gps='lat' in d
 text=f"{'📦 DASTAVKA' if d['service']=='DELIVERY' else '🚕 BUYURTMA'}\n\n{typ}\n\n📍 QAYERDAN:\n{d['origin']}\n\n🏁 QAYERGA:\n{d['destination']}\n\n📍 GPS: {'✅ Yuborilgan' if gps else '❌ Yuborilmagan'}\n\n💰 NARX:\n{money(p)}"
 await m.answer(text,reply_markup=confirm_kb(d['lang']))
@dp.callback_query(F.data=='oe')
async def edit_order(c:CallbackQuery,s:FSMContext):
 d=await s.get_data(); await c.answer(); await s.set_state(Order.origin); await c.message.answer(LANG[d['lang']]['from']+'\n\n✍️ '+LANG[d['lang']]['fromex'])
@dp.callback_query(F.data=='ox')
async def cancel_order(c:CallbackQuery,s:FSMContext): await s.clear(); await c.answer(); await c.message.edit_text('❌ Buyurtma bekor qilindi.')
@dp.callback_query(F.data=='oc')
async def confirm_order(c:CallbackQuery,s:FSMContext):
 d=await s.get_data()
 if not all(k in d for k in ('service','origin','destination','price')): return await c.answer('Ma’lumotlar to‘liq emas.',show_alert=True)
 oid=await q('INSERT INTO orders(customer_tg_id,service,origin,destination,origin_lat,origin_lon,passengers,price,status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(c.from_user.id,'DELIVERY' if d['service']=='DELIVERY' else 'PASSENGER',d['origin'],d['destination'],d.get('lat'),d.get('lon'),d.get('passengers',1),d['price'],'SEARCHING',now())); await s.clear(); await c.answer('Buyurtma qabul qilindi'); await c.message.edit_text(f'🔎 <b>Buyurtma #{oid}</b>\n\n🚕 Haydovchi qidirilmoqda...'); asyncio.create_task(dispatch(oid))

async def eligible(oid,excluded=None):
 ex=excluded or []
 rows=await q("SELECT * FROM drivers WHERE approved=1 AND online=1 AND active_orders<4 AND route='OBLIQ_ANGREN'",fetch=True)
 return [r for r in rows if r['tg_id'] not in ex]
async def dispatch(oid,excluded=None):
 await asyncio.sleep(.5); o=await q('SELECT * FROM orders WHERE id=?',(oid,),one=True)
 if not o or o['status']!='SEARCHING': return
 ds=await eligible(oid,excluded)
 for d in ds:
  try:
   typ='📦 DASTAVKA' if o['service']=='DELIVERY' else f"👤 YO‘LOVCHI\n👥 {o['passengers']} kishi"
   text=f"🚕 <b>YANGI BUYURTMA #{oid}</b>\n\n{typ}\n\n📍 QAYERDAN:\n{o['origin']}\n\n🏁 QAYERGA:\n{o['destination']}\n\n💰 NARX: {money(o['price'])}\n\n📍 GPS: {'✅ Yuborilgan' if o['origin_lat'] is not None else '❌ Yuborilmagan'}"
   await bot.send_message(d['tg_id'],text,reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='✅ BUYURTMANI OLISH',callback_data=f'claim:{oid}')],[InlineKeyboardButton(text='❌ RAD ETISH',callback_data=f'decline:{oid}')]]))
   if o['origin_lat'] is not None: await bot.send_location(d['tg_id'],o['origin_lat'],o['origin_lon'])
   await q('INSERT OR IGNORE INTO offers(order_id,driver_tg_id,status,created_at) VALUES(?,?,?,?)',(oid,d['tg_id'],'SENT',now()))
  except Exception: pass
 await asyncio.sleep(90)
 o=await q('SELECT * FROM orders WHERE id=?',(oid,),one=True)
 if o and o['status']=='SEARCHING':
  ds=await eligible(oid,excluded)
  if ds: return await dispatch(oid,excluded)
  await q("UPDATE orders SET status='NO_DRIVER' WHERE id=? AND status='SEARCHING'",(oid,)); await bot.send_message(o['customer_tg_id'],f'⚠️ Buyurtma #{oid}: hozircha haydovchi topilmadi.')

@dp.callback_query(F.data.startswith('decline:'))
async def decline(c:CallbackQuery):
 oid=int(c.data.split(':')[1]); d=await driver(c.from_user.id); o=await q('SELECT * FROM orders WHERE id=?',(oid,),one=True)
 if not d or not d['approved'] or not o or o['status']!='SEARCHING': return await c.answer('Buyurtma endi mavjud emas.',show_alert=True)
 await q('UPDATE offers SET status="DECLINED" WHERE order_id=? AND driver_tg_id=?',(oid,c.from_user.id)); await c.answer('Rad etildi'); await c.message.edit_reply_markup(reply_markup=None)
@dp.callback_query(F.data.startswith('claim:'))
async def claim(c:CallbackQuery):
 oid=int(c.data.split(':')[1]); o=await q('SELECT * FROM orders WHERE id=?',(oid,),one=True); d=await driver(c.from_user.id)
 if not d or not d['approved'] or not d['online'] or d['active_orders']>=4: return await c.answer('Siz hozir buyurtma ola olmaysiz.',show_alert=True)
 async with lock:
  o=db.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
  if not o or o['status']!='SEARCHING': ok=False
  else:
   cur=db.execute("UPDATE orders SET driver_tg_id=?,status='ACCEPTED',accepted_at=? WHERE id=? AND status='SEARCHING'",(c.from_user.id,now(),oid)); ok=cur.rowcount==1
   if ok: db.execute('UPDATE drivers SET active_orders=active_orders+1 WHERE tg_id=? AND active_orders<4',(c.from_user.id,)); db.execute('UPDATE offers SET status="ACCEPTED" WHERE order_id=? AND driver_tg_id=?',(oid,c.from_user.id))
  db.commit()
 if not ok: return await c.answer('❌ Bu buyurtmani boshqa haydovchi qabul qildi.',show_alert=True)
 cust=await user(o['customer_tg_id']); await c.answer('Buyurtma sizniki!'); await c.message.edit_reply_markup(reply_markup=None)
 await c.message.answer(f"✅ BUYURTMA QABUL QILINDI\n\n👤 Mijoz: {cust['name']}\n📞 {cust['phone']}\n📍 {o['origin']}\n🏁 {o['destination']}\n💰 {money(o['price'])}",reply_markup=driver_active_kb(oid))
 await bot.send_message(o['customer_tg_id'],f"✅ <b>HAYDOVCHI TOPILDI!</b>\n\n🚕 Mashina: {d['car_model']}\n🔢 Davlat raqami: {d['plate']}\n👤 {d['full_name']}\n📞 {d['phone']}\n\n📍 Qayerdan: {o['origin']}\n🏁 Qayerga: {o['destination']}")
 if o['origin_lat'] is not None: await bot.send_location(c.from_user.id,o['origin_lat'],o['origin_lon'])

def driver_active_kb(oid): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='📵 MIJOZ JAVOB BERMADI',callback_data=f'na:{oid}')],[InlineKeyboardButton(text='✅ BUYURTMANI YAKUNLASH',callback_data=f'fin:{oid}')]])
@dp.callback_query(F.data.startswith('na:'))
async def no_answer(c:CallbackQuery):
 oid=int(c.data.split(':')[1]); o=await q('SELECT * FROM orders WHERE id=?',(oid,),one=True)
 if not o or o['driver_tg_id']!=c.from_user.id or o['status']!='ACCEPTED': return await c.answer('Ruxsat yo‘q.',show_alert=True)
 await q("UPDATE orders SET status='NO_ANSWER_WAIT' WHERE id=? AND driver_tg_id=? AND status='ACCEPTED'",(oid,c.from_user.id)); await c.answer('60 soniyalik javob kutish boshlandi')
 await bot.send_message(o['customer_tg_id'],'🚕 Haydovchi siz bilan bog‘lana olmadi.\n\nSizga hali ham mashina kerakmi?\n⏱ 1 daqiqa ichida javob bering.',reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='✅ HA, KERAK',callback_data=f'needyes:{oid}')],[InlineKeyboardButton(text='❌ YO‘Q, KERAK EMAS',callback_data=f'needno:{oid}')]]))
 asyncio.create_task(no_answer_timeout(oid,c.from_user.id))
async def no_answer_timeout(oid,old):
 await asyncio.sleep(60); o=await q('SELECT * FROM orders WHERE id=?',(oid,),one=True)
 if o and o['status']=='NO_ANSWER_WAIT':
  await q("UPDATE orders SET status='CANCELLED',driver_tg_id=NULL WHERE id=?",(oid,)); await q('UPDATE drivers SET active_orders=CASE WHEN active_orders>0 THEN active_orders-1 ELSE 0 END WHERE tg_id=?',(old,)); await bot.send_message(o['customer_tg_id'],'⏱ Vaqt tugadi. Buyurtma bekor qilindi.')
@dp.callback_query(F.data.startswith('needno:'))
async def need_no(c:CallbackQuery):
 oid=int(c.data.split(':')[1]); o=await q('SELECT * FROM orders WHERE id=?',(oid,),one=True)
 if not o or o['customer_tg_id']!=c.from_user.id or o['status']!='NO_ANSWER_WAIT': return await c.answer('Buyurtma holati o‘zgargan.',show_alert=True)
 old=o['driver_tg_id']; await q("UPDATE orders SET status='CANCELLED',driver_tg_id=NULL WHERE id=?",(oid,)); await q('UPDATE drivers SET active_orders=CASE WHEN active_orders>0 THEN active_orders-1 ELSE 0 END WHERE tg_id=?',(old,)); await c.answer(); await c.message.edit_text('❌ Buyurtma bekor qilindi.')
@dp.callback_query(F.data.startswith('needyes:'))
async def need_yes(c:CallbackQuery):
 oid=int(c.data.split(':')[1]); o=await q('SELECT * FROM orders WHERE id=?',(oid,),one=True)
 if not o or o['customer_tg_id']!=c.from_user.id or o['status']!='NO_ANSWER_WAIT': return await c.answer('Buyurtma holati o‘zgargan.',show_alert=True)
 old=o['driver_tg_id']; await q("UPDATE orders SET status='SEARCHING',driver_tg_id=NULL WHERE id=?",(oid,)); await q('UPDATE drivers SET active_orders=CASE WHEN active_orders>0 THEN active_orders-1 ELSE 0 END WHERE tg_id=?',(old,)); await c.answer(); await c.message.edit_text('🔎 Yangi haydovchi qidirilmoqda...'); asyncio.create_task(dispatch(oid,[old]))
@dp.callback_query(F.data.startswith('fin:'))
async def finish(c:CallbackQuery):
 oid=int(c.data.split(':')[1]); o=await q('SELECT * FROM orders WHERE id=?',(oid,),one=True)
 if not o or o['driver_tg_id']!=c.from_user.id or o['status']!='ACCEPTED': return await c.answer('Ruxsat yo‘q.',show_alert=True)
 await q("UPDATE orders SET status='COMPLETED',finished_at=? WHERE id=? AND status='ACCEPTED'",(now(),oid)); await q('UPDATE drivers SET active_orders=CASE WHEN active_orders>0 THEN active_orders-1 ELSE 0 END WHERE tg_id=?',(c.from_user.id,)); await c.answer('Yakunlandi'); await c.message.edit_reply_markup(reply_markup=None); await bot.send_message(o['customer_tg_id'],'✅ Buyurtma yakunlandi.\n\n⭐ Haydovchini baholang:',reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=f'{i} ⭐',callback_data=f'rate:{oid}:{i}') for i in range(1,6)]]))
@dp.callback_query(F.data.startswith('rate:'))
async def rate(c:CallbackQuery):
 _,oid,sc=c.data.split(':'); oid=int(oid); sc=int(sc); o=await q('SELECT * FROM orders WHERE id=?',(oid,),one=True)
 if not o or o['customer_tg_id']!=c.from_user.id or o['status']!='COMPLETED': return await c.answer('Ruxsat yo‘q.',show_alert=True)
 try: await q('INSERT INTO ratings(order_id,from_tg_id,to_tg_id,score,created_at) VALUES(?,?,?,?,?)',(oid,c.from_user.id,o['driver_tg_id'],sc,now()))
 except sqlite3.IntegrityError: return await c.answer('Siz allaqachon baholagansiz.')
 d=await driver(o['driver_tg_id']); cnt=d['rating_count']+1; avg=((d['rating']*d['rating_count'])+sc)/cnt; await q('UPDATE drivers SET rating=?,rating_count=? WHERE tg_id=?',(avg,cnt,d['tg_id'])); await c.answer('Rahmat!'); await c.message.edit_text(f'⭐ Baho: {sc}/5')

@dp.message(F.text.func(lambda x: x in {LANG['uz']['lang'],LANG['uzc']['lang'],LANG['ru']['lang'],LANG['en']['lang']}))
async def change_lang(m:Message,s:FSMContext): await s.clear(); await s.set_state(Reg.lang); await m.answer('🌐 Tilni tanlang:',reply_markup=lang_kb())
@dp.message(Reg.lang)
async def _dummy(m:Message,s:FSMContext): pass

@dp.message(F.text.func(lambda x: x in {LANG['uz']['orders'],LANG['uzc']['orders'],LANG['ru']['orders'],LANG['en']['orders']}))
async def history(m:Message):
 rows=await q('SELECT * FROM orders WHERE customer_tg_id=? ORDER BY id DESC LIMIT 20',(m.from_user.id,),fetch=True)
 await m.answer('📋 <b>BUYURTMALARIM</b>\n\n'+(''.join(f"#{o['id']} | {o['origin']} → {o['destination']} | {money(o['price'])} | {o['status']}\n" for o in rows) or 'Tarix bo‘sh.'))
@dp.message(F.text.func(lambda x: x in {LANG['uz']['profile'],LANG['uzc']['profile'],LANG['ru']['profile'],LANG['en']['profile']}))
async def profile(m:Message):
 u=await user(m.from_user.id); l=get_lang(u); await m.answer(f"👤 <b>PROFIL</b>\n\n{u['name']}\n📞 {u['phone']}\n🌐 {LANG[l]['name']}")
@dp.message(F.text.func(lambda x: x in {LANG['uz']['help'],LANG['uzc']['help'],LANG['ru']['help'],LANG['en']['help']}))
async def help_(m:Message): await m.answer('☎️ Yordam: +998 94 422 08 09\n📍 OBLIQ ↔ ANGREN')

@dp.message(Command('driver'))
async def driver_command(m:Message,s:FSMContext): await driver_start(m,s)
def driver_kb(d):
 return kb([[('🟢 ONLINE' if d['online'] else '🔴 OFFLINE')],["📦 BUYURTMALAR","📊 DAROMAD"],["⭐ REYTING","👤 PROFIL"],["📜 QOIDALAR","☎️ YORDAM"]])

@dp.message(Command('driver'))
async def driver_command(m:Message,s:FSMContext): await driver_start(m,s)

@dp.message(F.text.in_({'🚕 HAYDOVCHI BO‘LISH','🚕 ҲАЙДОВЧИ БЎЛИШ','🚕 СТАТЬ ВОДИТЕЛЕМ','🚕 BECOME A DRIVER'}))
async def driver_start(m:Message,s:FSMContext):
 d=await driver(m.from_user.id)
 if d:
  if not d['approved']: return await m.answer('⏳ Haydovchilik arizangiz admin tomonidan ko‘rib chiqilmoqda.')
  return await m.answer('🚕 <b>HAYDOVCHI PANELI</b>\n\n🟢 Holat: '+('ONLINE' if d['online'] else 'OFFLINE')+f"\n📦 Faol buyurtmalar: {d['active_orders']}/4",reply_markup=driver_kb(d))
 await s.clear(); await s.set_state(DriverReg.name); await m.answer('1. F.I.Sh. ni kiriting:')

@dp.message(DriverReg.name)
async def drname(m:Message,s:FSMContext): await s.update_data(full_name=m.text.strip()); await s.set_state(DriverReg.phone); await m.answer('2. Telefon raqamingiz:',reply_markup=kb([['📱 Raqamni yuborish']]))
@dp.message(DriverReg.phone,F.contact)
async def drphonec(m:Message,s:FSMContext): await s.update_data(phone=m.contact.phone_number); await s.set_state(DriverReg.car); await m.answer('3. Mashina modeli:')
@dp.message(DriverReg.phone)
async def drphone(m:Message,s:FSMContext): await s.update_data(phone=m.text.strip()); await s.set_state(DriverReg.car); await m.answer('3. Mashina modeli:')
@dp.message(DriverReg.car)
async def drcar(m:Message,s:FSMContext): await s.update_data(car_model=m.text.strip()); await s.set_state(DriverReg.plate); await m.answer('4. Davlat raqami:')
@dp.message(DriverReg.plate)
async def drplate(m:Message,s:FSMContext): await s.update_data(plate=m.text.strip().upper()); await s.set_state(DriverReg.license); await m.answer('5. Prava rasmini yuboring:')
@dp.message(DriverReg.license,F.photo)
async def drlic(m:Message,s:FSMContext): await s.update_data(license_file_id=m.photo[-1].file_id); await s.set_state(DriverReg.tech); await m.answer('6. Texpasport rasmini yuboring:')
@dp.message(DriverReg.tech,F.photo)
async def drtech(m:Message,s:FSMContext): await s.update_data(tech_file_id=m.photo[-1].file_id); await s.set_state(DriverReg.photo); await m.answer('7. Mashina rasmini yuboring:')
@dp.message(DriverReg.photo,F.photo)
async def drphoto(m:Message,s:FSMContext): await s.update_data(car_photo_file_id=m.photo[-1].file_id); await s.set_state(DriverReg.rules); await m.answer('📜 Qoidalarni qabul qilsangiz HA deb yozing.')
@dp.message(DriverReg.rules)
async def drrules(m:Message,s:FSMContext):
 if (m.text or '').lower() not in {'ha','xa','yes','да','ҳа'}: return await m.answer('Qoidalarni qabul qilish uchun HA deb yozing.')
 d=await s.get_data(); tg=m.from_user.id
 await q('INSERT INTO drivers(tg_id,full_name,phone,car_model,plate,license_file_id,tech_file_id,car_photo_file_id,route,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(tg,d['full_name'],d['phone'],d['car_model'],d['plate'],d.get('license_file_id',''),d.get('tech_file_id',''),d.get('car_photo_file_id',''),'OBLIQ_ANGREN',now())); await s.clear(); await m.answer('✅ Ariza yuborildi. Admin tasdiqlashini kuting.')
 mark=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='✅ TASDIQLASH',callback_data=f'ad:{tg}'),InlineKeyboardButton(text='❌ RAD ETISH',callback_data=f'ar:{tg}')]])
 await admin_send(f"🚕 <b>YANGI HAYDOVCHI</b>\n\n👤 {d['full_name']}\n📞 {d['phone']}\n🚗 {d['car_model']}\n🔢 {d['plate']}\n📍 OBLIQ ↔ ANGREN",mark)
 for label,key in [('📄 PRAVA','license_file_id'),('📄 TEX PASPORT','tech_file_id'),('📷 MASHINA','car_photo_file_id')]:
  if d.get(key):
   try: await bot.send_photo(ADMIN_ID,d[key],caption=label)
   except: pass
@dp.callback_query(F.data.startswith('ad:'))
async def approve(c:CallbackQuery):
 if c.from_user.id!=ADMIN_ID:return await c.answer('Ruxsat yo‘q',show_alert=True)
 tg=int(c.data.split(':')[1]); d=await driver(tg)
 if not d:return await c.answer('Topilmadi',show_alert=True)
 await q('UPDATE drivers SET approved=1 WHERE tg_id=?',(tg,)); await q('UPDATE users SET role="driver" WHERE tg_id=?',(tg,)); await c.answer('Tasdiqlandi'); await c.message.edit_reply_markup(reply_markup=None); await bot.send_message(tg,'🎉 Haydovchilik arizangiz tasdiqlandi!')
@dp.callback_query(F.data.startswith('ar:'))
async def reject(c:CallbackQuery):
 if c.from_user.id!=ADMIN_ID:return await c.answer('Ruxsat yo‘q',show_alert=True)
 tg=int(c.data.split(':')[1]); await q('DELETE FROM drivers WHERE tg_id=? AND approved=0',(tg,)); await c.answer('Rad etildi'); await c.message.edit_reply_markup(reply_markup=None)

@dp.message(F.text.in_({'🟢 ONLINE','🔴 OFFLINE'}))
async def toggle(m:Message):
 d=await driver(m.from_user.id)
 if not d:return
 if not d['approved'] or (await user(m.from_user.id))['blocked']: return await m.answer('🚫 Sizga ONLINE ruxsati yo‘q.')
 v=0 if d['online'] else 1; await q('UPDATE drivers SET online=? WHERE tg_id=?',(v,m.from_user.id)); await m.answer('🟢 ONLINE' if v else '🔴 OFFLINE')

@dp.message(F.text=='📦 BUYURTMALAR')
async def driver_orders_menu(m:Message):
 d=await driver(m.from_user.id)
 if not d or not d['approved']: return
 rows=await q("SELECT * FROM orders WHERE driver_tg_id=? AND status='ACCEPTED' ORDER BY id DESC",(m.from_user.id,),fetch=True)
 if not rows: return await m.answer('📭 Faol buyurtma yo‘q.',reply_markup=driver_kb(d))
 for o in rows: await m.answer(f"🚕 BUYURTMA #{o['id']}\n\n📍 {o['origin']}\n🏁 {o['destination']}\n💰 {money(o['price'])}",reply_markup=driver_active_kb(o['id']))
@dp.message(F.text=='📊 DAROMAD')
async def driver_income(m:Message):
 d=await driver(m.from_user.id)
 if not d: return
 r=await q("SELECT COUNT(*) c,COALESCE(SUM(price),0) total FROM orders WHERE driver_tg_id=? AND status='COMPLETED'",(m.from_user.id,),one=True)
 await m.answer(f"📊 <b>DAROMAD</b>\n\n🚕 Yakunlangan: {r['c']}\n💰 Jami: {money(r['total'])}",reply_markup=driver_kb(d))
@dp.message(F.text=='⭐ REYTING')
async def driver_rating(m:Message):
 d=await driver(m.from_user.id)
 if d: await m.answer(f"⭐ <b>REYTING</b>\n\n{d['rating']:.2f}/5\nBaholar: {d['rating_count']}",reply_markup=driver_kb(d))
@dp.message(F.text=='👤 PROFIL')
async def driver_profile(m:Message):
 d=await driver(m.from_user.id)
 if d: await m.answer(f"👤 <b>PROFIL</b>\n\n{d['full_name']}\n📞 {d['phone']}\n🚗 {d['car_model']}\n🔢 {d['plate']}\n📍 OBLIQ ↔ ANGREN",reply_markup=driver_kb(d))
@dp.message(F.text=='📜 QOIDALAR')
async def driver_rules(m:Message): await m.answer('📜 Buyurtmani qabul qilgach mijoz bilan bog‘laning.\nMijoz ma’lumotlarini tarqatmang.\nNarxni o‘zboshimchalik bilan o‘zgartirmang.')

# ADMIN

def admin_kb(): return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='📊 STATISTIKA',callback_data='ast')],[InlineKeyboardButton(text='🚕 HAYDOVCHILAR',callback_data='adrv'),InlineKeyboardButton(text='⏳ TASDIQLASHLAR',callback_data='apen')],[InlineKeyboardButton(text='👥 MIJOZLAR',callback_data='ausr'),InlineKeyboardButton(text='📦 BUYURTMALAR',callback_data='aord')],[InlineKeyboardButton(text='🟢 ONLINE',callback_data='aon'),InlineKeyboardButton(text='🚫 BLOKLANGANLAR',callback_data='ablk')],[InlineKeyboardButton(text='⚙️ SOZLAMALAR',callback_data='aset')]])
@dp.message(Command('admin'))
async def admin(m:Message):
 if m.from_user.id!=ADMIN_ID:return await m.answer('🚫 Ruxsat yo‘q.')
 await m.answer('👨‍💼 <b>TAXI BOR MI? — ADMIN PANEL</b>',reply_markup=admin_kb())
@dp.callback_query(F.data=='ast')
async def ast(c:CallbackQuery):
 if c.from_user.id!=ADMIN_ID:return
 def C(sql): return db.execute(sql).fetchone()[0]
 text=("📊 <b>STATISTIKA</b>\n\n"
 f"👥 Mijozlar: {C("SELECT COUNT(*) FROM users WHERE role!='driver'")}\n"
 f"🚕 Haydovchilar: {C('SELECT COUNT(*) FROM drivers')}\n"
 f"🟢 Online: {C('SELECT COUNT(*) FROM drivers WHERE approved=1 AND online=1')}\n"
 f"⏳ Tasdiqlash: {C('SELECT COUNT(*) FROM drivers WHERE approved=0')}\n"
 f"🚫 Bloklangan: {C('SELECT COUNT(*) FROM users WHERE blocked=1')}\n\n"
 f"📦 Bugungi buyurtmalar: {C("SELECT COUNT(*) FROM orders WHERE date(created_at)=date('now')")}\n"
 f"✅ Yakunlangan: {C("SELECT COUNT(*) FROM orders WHERE status='COMPLETED'")}\n"
 f"⏳ Faol: {C("SELECT COUNT(*) FROM orders WHERE status IN ('SEARCHING','ACCEPTED','NO_ANSWER_WAIT')")}\n"
 f"❌ Bekor: {C("SELECT COUNT(*) FROM orders WHERE status='CANCELLED'")}")
 await c.message.edit_text(text,reply_markup=admin_kb())
@dp.callback_query(F.data=='apen')
async def apen(c:CallbackQuery):
 if c.from_user.id!=ADMIN_ID:return
 rows=await q('SELECT * FROM drivers WHERE approved=0 ORDER BY id DESC',fetch=True)
 if not rows:return await c.answer('Tasdiqlash kutilayotgan haydovchi yo‘q',show_alert=True)
 for d in rows:
  mark=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text='✅ TASDIQLASH',callback_data=f"ad:{d['tg_id']}"),InlineKeyboardButton(text='❌ RAD ETISH',callback_data=f"ar:{d['tg_id']}")]])
  await c.message.answer(f"🚕 <b>YANGI HAYDOVCHI</b>\n\n👤 {d['full_name']}\n📞 {d['phone']}\n🚗 {d['car_model']}\n🔢 {d['plate']}\n📍 OBLIQ ↔ ANGREN",reply_markup=mark)

@dp.callback_query(F.data=='adrv')
async def adrv(c:CallbackQuery):
 if c.from_user.id!=ADMIN_ID:return
 rows=await q('SELECT * FROM drivers ORDER BY id DESC LIMIT 50',fetch=True); await c.message.answer('🚕 <b>HAYDOVCHILAR</b>\n\n'+('\n'.join(f"{d['id']}. {d['full_name']} | {d['car_model']} | {d['plate']} | {'ONLINE' if d['online'] else 'OFFLINE'} | {'TASDIQLANGAN' if d['approved'] else 'KUTILMOQDA'} | ⭐{d['rating']:.2f}" for d in rows) or 'Yo‘q'))
@dp.callback_query(F.data=='ausr')
async def ausr(c:CallbackQuery):
 if c.from_user.id!=ADMIN_ID:return
 rows=await q('SELECT * FROM users ORDER BY id DESC LIMIT 50',fetch=True); await c.message.answer('👥 <b>MIJOZLAR</b>\n\n'+('\n'.join(f"{u['id']}. {u['name']} | {u['phone']} | {'🚫' if u['blocked'] else '✅'}" for u in rows) or 'Yo‘q'))
@dp.callback_query(F.data=='aord')
async def aord(c:CallbackQuery):
 if c.from_user.id!=ADMIN_ID:return
 rows=await q('SELECT * FROM orders ORDER BY id DESC LIMIT 50',fetch=True); await c.message.answer('📦 <b>BUYURTMALAR</b>\n\n'+('\n'.join(f"#{o['id']} | {o['origin']} → {o['destination']} | {money(o['price'])} | {o['status']}" for o in rows) or 'Yo‘q'))
@dp.callback_query(F.data=='aon')
async def aon(c:CallbackQuery):
 if c.from_user.id!=ADMIN_ID:return
 rows=await q('SELECT * FROM drivers WHERE approved=1 AND online=1',fetch=True); await c.message.answer('🟢 <b>ONLINE HAYDOVCHILAR</b>\n\n'+('\n'.join(f"{d['full_name']} | {d['phone']} | {d['active_orders']}/4" for d in rows) or 'Hozir online haydovchi yo‘q.'))
@dp.callback_query(F.data=='ablk')
async def ablk(c:CallbackQuery):
 if c.from_user.id!=ADMIN_ID:return
 rows=await q('SELECT * FROM users WHERE blocked=1',fetch=True); await c.message.answer('🚫 <b>BLOKLANGANLAR</b>\n\n'+('\n'.join(f"{u['name']} | {u['tg_id']} | {u['phone']}" for u in rows) or 'Bloklanganlar yo‘q.'))
@dp.callback_query(F.data=='aset')
async def aset(c:CallbackQuery):
 if c.from_user.id==ADMIN_ID: await c.message.answer('⚙️ SOZLAMALAR\n\n📍 Yo‘nalish: OBLIQ ↔ ANGREN\n👨‍✈️ Faol buyurtma limiti: 4\n📵 No-answer kutish: 60 soniya\n💰 Minimal narx: 5 000 so‘m')

@dp.message(Command('block'))
async def block(m:Message):
 if m.from_user.id!=ADMIN_ID:return
 p=(m.text or '').split();
 if len(p)!=2 or not p[1].isdigit(): return await m.answer('/block TELEGRAM_ID')
 tg=int(p[1]); await q('UPDATE users SET blocked=1 WHERE tg_id=?',(tg,)); await q('UPDATE drivers SET online=0 WHERE tg_id=?',(tg,)); await m.answer('🚫 Bloklandi.')
@dp.message(Command('unblock'))
async def unblock(m:Message):
 if m.from_user.id!=ADMIN_ID:return
 p=(m.text or '').split();
 if len(p)!=2 or not p[1].isdigit(): return await m.answer('/unblock TELEGRAM_ID')
 await q('UPDATE users SET blocked=0 WHERE tg_id=?',(int(p[1]),)); await m.answer('✅ Blokdan chiqarildi.')

@dp.message(F.text.func(lambda x: x in {'☎️ YORDAM','☎️ ЁРДАМ','☎️ ПОМОЩЬ','☎️ HELP'}))
async def support_menu(m:Message,s:FSMContext): await s.set_state(Support.text); await m.answer('☎️ Murojaat yoki yordam so‘rovingizni yozing:')
@dp.message(Support.text)
async def support_save(m:Message,s:FSMContext):
 text=(m.text or '').strip()
 if len(text)<3: return await m.answer('Iltimos, batafsilroq yozing.')
 tid=await q('INSERT INTO support_tickets(tg_id,text,created_at) VALUES(?,?,?)',(m.from_user.id,text,now())); await s.clear(); await m.answer(f'✅ Murojaat qabul qilindi. №{tid}')
 await admin_send(f'☎️ <b>YANGI MUROJAAT #{tid}</b>\n👤 {m.from_user.id}\n\n{text}')

@dp.message()
async def fallback(m:Message,s:FSMContext):
 u=await user(m.from_user.id)
 if u: await m.answer('🤖 Menyudan foydalaning.',reply_markup=main_kb(u['lang']))
 else: await m.answer('Avval /start bosing.')

async def main():
 init_db(); await bot.delete_webhook(drop_pending_updates=True); await dp.start_polling(bot)
if __name__=='__main__': asyncio.run(main())
