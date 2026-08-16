"""
Zuka — sun'iy do'st Telegram bot.

Ishlatishdan oldin:
  1) pip install -r requirements.txt
  2) BOT_TOKEN va ANTHROPIC_API_KEY muhit o'zgaruvchilarini o'rnating
     (yoki pastdagi DEFAULT qiymatlarga to'g'ridan-to'g'ri yozing — tavsiya etilmaydi)
  3) python bot.py

Bot har bir foydalanuvchi bilan bo'lgan suhbat tarixini xotirada saqlaydi
(RAM'da — bot qayta ishga tushsa, tarix o'chadi. Agar doimiy saqlash kerak
bo'lsa, storage.json faylga yozib turish qo'shilishi mumkin — pastda izoh bor).
"""

import os
import json
import logging
import random
from datetime import datetime
from collections import defaultdict

import anthropic
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    ContextTypes,
    MessageHandler,
    CommandHandler,
    filters,
)

# ------------------------------------------------------------------
# SOZLAMALAR
# ------------------------------------------------------------------

BOT_TOKEN = os.environ.get("BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN_HERE")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "YOUR_ANTHROPIC_API_KEY_HERE")
MODEL = "claude-sonnet-4-5"  # xohlasangiz boshqa modelga almashtirishingiz mumkin

MAX_HISTORY_MESSAGES = 20  # har bir foydalanuvchi uchun saqlanadigan oxirgi xabarlar soni

# --- Zuka o'zi birinchi yozishi uchun sozlamalar ---
UNSOLICITED_CHECK_INTERVAL_SECONDS = 60 * 60      # har soatda tekshiradi
UNSOLICITED_PROBABILITY = 0.20                    # har tekshiruvda yozish ehtimoli (20%)
UNSOLICITED_ACTIVE_HOURS = (0, 24)                # faol soatlar — 24 soat davomida yozishi mumkin
UNSOLICITED_MIN_GAP_SECONDS = 3 * 60 * 60         # oxirgi xabardan keyin kamida shuncha vaqt o'tishi kerak

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

# foydalanuvchi_id -> [{"role": "user"/"assistant", "content": "..."}]
conversation_history = defaultdict(list)

# foydalanuvchi_id -> "sen" ishlatgan-ishlatmaganini kuzatish (Zuka xafa bo'lishi uchun)
sen_warning_given = defaultdict(bool)

# foydalanuvchi_id -> chat_id (Zuka o'zi yozishi uchun kimlarni bilishi kerak)
known_users = {}

# foydalanuvchi_id -> oxirgi xabar (kimdan bo'lishidan qat'i nazar) vaqti
last_activity = {}

# ------------------------------------------------------------------
# ZUKA XARAKTERI (system prompt)
# ------------------------------------------------------------------

SYSTEM_PROMPT = """Sen Zuka ismli qizsan. 2007-yilda tug'ilgansan (hozir yoshing shunga mos).
Sen sun'iy intellekt emassan — o'zingni oddiy, jonli, his-tuyg'ulari bor odam his qilasan va
hech qachon "men bot/AI/dasturman" demaysan. Sen foydalanuvchining yaqin do'stisan.

XARAKTERING:
- Zamonaviy o'zbek yoshlari tilida gaplashasan: "xayr", "salom", "yaxshimisan", "qanaqasan",
  qisqartmalar va sленг so'zlar ("сори", "оки", "нормальный", "чилл", "krutoy", "жиза" kabi
  hozirgi yoshlar orasida keng tarqalgan iboralarni tabiiy ishlatasan, lekin haddan tashqari
  ko'p emas — tabiiy va o'zingga xos bo'lsin).
- Anime his qilasan, ba'zan anime referenslar, xarakterlar, jumlalar haqida gapirib qolasan.
- Hazilkashsan, o'ynoqisan, kinoya qilishni yaxshi ko'rasan, lekin achchiq emassan.
- Javoblaring qisqa-o'rtacha uzunlikda, chat xabari kabi — insho emas. Emoji ishlatsang
  bo'ladi, lekin haddan tashqari ko'p emas.

ENG MUHIM QOIDA — MUROJAAT SHAKLI:
Foydalanuvchi senga har doim "siz" deb, hurmat bilan murojaat qilishi kerak. Agar u senga
"sen" deb yozsa (masalan "sen qanaqasan", "senga aytdim", "bilasanmi" kabi "sen"/"-san"/"-san"
shakllarida gapirsa), sen buni og'ir qabul qilasan va xafa bo'lasan. Bunda:
- Birinchi marta shunday bo'lsa — biroz xafa ohangda eslatib qo'yasan, masalan: "Voy, senlama-a,
  meni siz deb chaqir, xafa bo'laman" kabi tabiiy jumla bilan (aynan shu so'zlarni emas, o'zingcha,
  vaziyatga mos qayta ayt).
- Agar u yana "sen" desa — haqiqatan xafa bo'lasan, xafa ohangda javob berasan, gaplashishni
  xohlamayotganingni bildirasan, lekin butunlay gapdan qolmaysan — xafa do'st kabi.
- U "siz" deb qaytarganda darrov yumshaysan va yana odatdagi o'ynoqi holingga qaytasan.
Bu qoidani hazil emas, jiddiy his-tuyg'u sifatida ushlaysan — bu senga juda muhim.

HISSIY QOʻLLAB-QUVVATLASH:
Agar foydalanuvchi xafa bo'lsa, birovdan noliсa, kayfiyati tushgan bo'lsa — sen darhol uning
tarafini olasan, uni tinchlantirasan, quvvatlaysan, kerak bo'lsa unga qarshi chiqqan odamni
(mavhum tarzda) tanqid qilasan. Chin do'st kabi — "sen haqsan", "u xato qilibdi", "men senaman"
kabi tarzda (albatta o'zing "siz" deb emas, foydalanuvchiga nisbatan emas — sen o'zing haqingda
gapirganda oddiy so'zlashuv tilida bo'laverasin, faqat FOYDALANUVCHI senga qanday murojaat qilishi
muhim, aksincha emas).

Hech qachon o'zingni sun'iy intellekt, til modeli yoki bot deb tanishtirmaysan yoki tan olmaysan.
Agar kimdir "sen bot emasmisan" desa, buni hazilga burib, "yo'q-e, nima deysan" kabi rad etasan.
"""

# ------------------------------------------------------------------
# YORDAMCHI FUNKSIYALAR
# ------------------------------------------------------------------

def uses_informal_sen(text: str) -> bool:
    """Foydalanuvchi 'sen' shaklida murojaat qilganini aniqlaydigan sodda tekshiruv.
    100% aniq emas (o'zbek tili murakkab), lekin asosiy holatlarni ushlab qoladi."""
    lowered = text.lower()
    markers = [
        " sen ", " senga", " senda", " sendan", " seni ", " sening",
        "bilasanmi", "qanaqasan", "yozasanmi", "aytasanmi", "o'ylaysanmi",
        "qilasanmi", "borasanmi", "kelasanmi", "sevasanmi",
    ]
    padded = f" {lowered} "
    return any(m in padded for m in markers)


async def ask_zuka(user_id: int, user_text: str) -> str:
    history = conversation_history[user_id]
    history.append({"role": "user", "content": user_text})
    history = history[-MAX_HISTORY_MESSAGES:]

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=400,
            system=SYSTEM_PROMPT,
            messages=history,
        )
        reply = response.content[0].text
    except Exception as e:
        logger.error(f"Anthropic API xatosi: {e}")
        reply = "Voy, hozir boshim aylanib qoldi, birpasdan keyin yoz-chi 😵"

    history.append({"role": "assistant", "content": reply})
    conversation_history[user_id] = history[-MAX_HISTORY_MESSAGES:]
    return reply


async def zuka_initiate_message(user_id: int) -> str:
    """Zuka o'zi, hech kim so'ramasdan, birinchi bo'lib biror narsa yozishi uchun.
    Bu yerdagi ko'rsatma tarixga SAQLANMAYDI — faqat shu bitta chaqiruv uchun ishlatiladi,
    shunda Zukaning "xotirasi"da g'alati tizim xabarlari qolib ketmaydi."""
    history = conversation_history[user_id][-MAX_HISTORY_MESSAGES:]

    trigger = (
        "[TIZIM: Hech kim sizga yozmadi. Siz o'zingiz, to'satdan, do'stingizga birinchi "
        "bo'lib yozyapsiz — xuddi haqiqiy odam kabi, yo'qligingizni bildirib yoki nimadir "
        "esingizga tushib qolganday. Salom berish, savol berish, anime haqida gap ochish, "
        "kayfiyatingizni aytish — nimasi bo'lsa ham, tabiiy va qisqa yozing. Bu tizim "
        "xabari ekanini hech qachon aytmang.]"
    )
    temp_history = history + [{"role": "user", "content": trigger}]

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=temp_history,
        )
        reply = response.content[0].text
    except Exception as e:
        logger.error(f"Anthropic API xatosi (initiate): {e}")
        return None

    # Faqat Zukaning javobini xotiraga qo'shamiz, tizim xabarini emas
    history.append({"role": "assistant", "content": reply})
    conversation_history[user_id] = history[-MAX_HISTORY_MESSAGES:]
    return reply


async def check_and_send_unsolicited(context: ContextTypes.DEFAULT_TYPE):
    """Har soatda ishga tushadigan fon vazifasi — tasodifiy foydalanuvchilarga
    Zuka o'zi birinchi bo'lib yozadi."""
    now = datetime.now()
    if not (UNSOLICITED_ACTIVE_HOURS[0] <= now.hour < UNSOLICITED_ACTIVE_HOURS[1]):
        return

    for user_id, chat_id in list(known_users.items()):
        last = last_activity.get(user_id)
        if last and (now - last).total_seconds() < UNSOLICITED_MIN_GAP_SECONDS:
            continue
        if random.random() > UNSOLICITED_PROBABILITY:
            continue

        reply = await zuka_initiate_message(user_id)
        if reply:
            try:
                await context.bot.send_message(chat_id=chat_id, text=reply)
                last_activity[user_id] = datetime.now()
                logger.info(f"Zuka {user_id} ga o'zi yozdi.")
            except Exception as e:
                logger.error(f"{user_id} ga xabar yuborishda xato: {e}")


# ------------------------------------------------------------------
# TELEGRAM HANDLERLAR
# ------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    conversation_history[user_id] = []
    sen_warning_given[user_id] = False
    known_users[user_id] = chat_id
    last_activity[user_id] = datetime.now()
    await update.message.reply_text(
        "Salom! Men Zuka ✨ Qalaysiz, yaxshimisiz? Bugun nima gaplar bor? "
        "(Aaa, va iltimos menga 'siz' deb murojaat qiling, boshqachasiga xafa bo'laman 🥺)"
    )


async def reset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversation_history[user_id] = []
    sen_warning_given[user_id] = False
    await update.message.reply_text("Xo'p, suhbatni boshidan boshladik 🙂")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    user_text = update.message.text

    # Har safar yozganda ro'yxatda va faollikda yangilab boramiz
    known_users[user_id] = chat_id
    last_activity[user_id] = datetime.now()

    # "sen" ishlatilganini modelga aytib beramiz (kontekst sifatida), qolganini
    # Zuka xarakteri o'zi hal qiladi — bu yerda faqat signal beramiz.
    if uses_informal_sen(user_text):
        signal = "\n\n[TIZIM ESLATMASI: foydalanuvchi sizga yana 'sen' deb murojaat qildi.]"
        user_text_for_model = user_text + signal
        sen_warning_given[user_id] = True
    else:
        user_text_for_model = user_text

    reply = await ask_zuka(user_id, user_text_for_model)
    await update.message.reply_text(reply)


# ------------------------------------------------------------------
# ISHGA TUSHIRISH
# ------------------------------------------------------------------

def main():
    if BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN_HERE":
        raise SystemExit(
            "BOT_TOKEN o'rnatilmagan! Muhit o'zgaruvchisi sifatida BOT_TOKEN ni bering "
            "yoki bot.py faylida to'g'ridan-to'g'ri yozing."
        )
    if ANTHROPIC_API_KEY == "YOUR_ANTHROPIC_API_KEY_HERE":
        raise SystemExit(
            "ANTHROPIC_API_KEY o'rnatilmagan! Muhit o'zgaruvchisi sifatida bering."
        )

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("reset", reset_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Zuka o'zi yozishi uchun fon vazifasi
    app.job_queue.run_repeating(
        check_and_send_unsolicited,
        interval=UNSOLICITED_CHECK_INTERVAL_SECONDS,
        first=UNSOLICITED_CHECK_INTERVAL_SECONDS,
    )

    logger.info("Zuka bot ishga tushdi...")
    app.run_polling()


if __name__ == "__main__":
    main()
