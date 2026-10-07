# TAXI BOR MI? — ALBATTA BOR! | OBLIQ ↔ ANGREN

Telegram taxi/delivery bot V1.

## Papka tarkibi
- `bot.py` — asosiy to'liq bot kodi
- `requirements.txt` — Python paketlari
- `.env.example` — Railway Variables namunasi
- `Procfile` — worker start komandasi
- `railway.toml` — Railway deploy sozlamasi

## Railway Variables
Majburiy:
- `BOT_TOKEN`
- `ADMIN_ID`

Ixtiyoriy:
- `OPENAI_API_KEY`
- `OPENAI_MODEL`
- `DB_PATH`

OpenAI kalitini berish shart emas: kod AI mavjud bo'lmasa ham ishlashga mo'ljallangan.

## Ishga tushirish
1. GitHub'ga shu papkadagi fayllarni yuklang.
2. Railway'da repo'ni Deploy qiling.
3. Variables'ga `BOT_TOKEN` va `ADMIN_ID` kiriting.
4. Deploy/Restart qiling.
5. Telegram'da `/start`.
6. Admin akkauntida `/admin`.

## Admin tasdiqlash
`/admin` → `⏳ TASDIQLASH KUTILMOQDA` → haydovchini ochish → hujjatlarni ko'rish → `✅ TASDIQLASH`.

Tasdiqlangandan keyin haydovchiga bot orqali tasdiq xabari boradi va u ONLINE bo'la oladi.

## Muhim
- Telegram bot tokenini kodga yozmang; faqat Railway Variables ishlating.
- SQLite Railway'da persistent volume'siz ishlatilsa, redeploy/restartlarda ma'lumot yo'qolishi mumkin. Production uchun Railway Volume qo'shish tavsiya qilinadi.
