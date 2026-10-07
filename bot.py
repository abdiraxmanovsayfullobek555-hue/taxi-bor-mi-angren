import os
import json
import time
import urllib.request
import urllib.parse


TOKEN = os.getenv("BOT_TOKEN")

if not TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi")


API = f"https://api.telegram.org/bot{TOKEN}"


def telegram(method, data=None):
    if data is None:
        data = {}

    encoded = urllib.parse.urlencode(data).encode("utf-8")

    request = urllib.request.Request(
        f"{API}/{method}",
        data=encoded,
        method="POST"
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def send_message(chat_id, text):
    return telegram(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text
        }
    )


def main_menu():
    return (
        "🚕 TAXI BOR MI? — ALBATTA BOR!\n\n"
        "📍 OBLIQ ↔ ANGREN\n\n"
        "Kerakli bo‘limni tanlang:\n\n"
        "👤 YO‘LOVCHI\n"
        "🚕 HAYDOVCHI\n"
        "📦 DASTAVKA\n"
        "📩 TAKLIF VA MUROJAATLAR"
    )


def handle_message(message):
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    text = message.get("text", "").strip()

    if not chat_id:
        return

    if text == "/start":
        send_message(chat_id, main_menu())
        return

    if text == "👤 YO‘LOVCHI":
        send_message(
            chat_id,
            "👤 YO‘LOVCHI\n\n"
            "🚕 Taksi buyurtma qilish bo‘limi.\n\n"
            "Hozircha tizim tayyorlanmoqda."
        )
        return

    if text == "🚕 HAYDOVCHI":
        send_message(
            chat_id,
            "🚕 HAYDOVCHI\n\n"
            "📍 Yo‘nalish: OBLIQ ↔ ANGREN\n\n"
            "Haydovchi tizimi tayyorlanmoqda."
        )
        return

    if text == "📦 DASTAVKA":
        send_message(
            chat_id,
            "📦 DASTAVKA\n\n"
            "Yuk yuborish xizmati.\n\n"
            "Hozircha tizim tayyorlanmoqda."
        )
        return

    if text == "📩 TAKLIF VA MUROJAATLAR":
        send_message(
            chat_id,
            "📩 TAKLIF VA MUROJAATLAR\n\n"
            "Taklifingiz, savolingiz yoki muammoingizni "
            "shu yerga yozing."
        )
        return

    send_message(
        chat_id,
        "🚕 TAXI BOR MI? — ALBATTA BOR!\n\n"
        "Iltimos, /start ni bosing."
    )


def run():
    offset = 0

    print("🚕 TAXI BOR MI? bot ishga tushdi!")

    while True:
        try:
            result = telegram(
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 30
                }
            )

            if not result.get("ok"):
                time.sleep(3)
                continue

            for update in result.get("result", []):
                offset = update["update_id"] + 1

                message = update.get("message")

                if message:
                    handle_message(message)

        except Exception as error:
            print("Xatolik:", error)
            time.sleep(5)


if __name__ == "__main__":
    run()
