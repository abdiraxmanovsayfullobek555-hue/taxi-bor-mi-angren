# TAXI BOR MI? — ALBATTA BOR!

3 ta alohida Telegram bot, bitta umumiy SQLite baza.

## Botlar
- Customer bot: `customer_bot.py`
- Driver bot: `driver_bot.py`
- Admin bot: `admin_bot.py`
- Birgalikda ishga tushirish: `run_all.py`

## Hozirgi V1
- Faqat `OBLIQ → ANGREN` va `ANGREN → OBLIQ`
- Mijoz yo‘nalishni ro‘yxatdan o‘tishda 1 marta tanlaydi
- Keyin mijoz istalgan oddiy matnni buyurtma sifatida yuboradi
- GPS majburiy emas
- AI ishlatilmaydi
- Haydovchi yo‘nalishni ro‘yxatdan o‘tishda 1 marta tanlaydi
- Haydovchi admin tasdig‘idan keyin ONLINE bo‘ladi
- Har haydovchida maksimum 4 ta faol buyurtma
- Buyurtmani faqat bir haydovchi oladi
- Yakunlash tugmasi bor
- Mijoz javob bermasa 60 soniyalik qayta tasdiqlash oqimi bor
- Buyurtma qayta ochilganda oldingi haydovchi chiqarib tashlanadi
- Yakunlangan buyurtmadan keyin 1–5 baho
- Haydovchi hujjatlari admin botga yuboriladi
- Uchala bot bitta SQLite bazadan foydalanadi

## Muhim
SQLite umumiy bo‘lishi uchun Railway’da **bitta service** ishlating va `python run_all.py` bilan ishga tushiring.

Agar Railway’da doimiy disk/volume mavjud bo‘lsa, `DB_PATH` ni o‘sha joyga bering. Aks holda deploy/restart paytida SQLite fayli yo‘qolishi mumkin.

## Environment Variables
- `CUSTOMER_BOT_TOKEN`
- `DRIVER_BOT_TOKEN`
- `ADMIN_BOT_TOKEN`
- `ADMIN_ID`
- `DB_PATH` (ixtiyoriy; default: `taxi_bor_mi.db`)

Tokenlarni kodga yozmang va hech kimga yubormang.

## Ishga tushirish
```bash
pip install -r requirements.txt
python run_all.py
```

Railway Start Command:
```bash
python run_all.py
```
