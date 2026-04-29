import os
import uuid
import edge_tts
import requests
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# توکن واقعی ربات و API key
BOT_TOKEN = "8776420786:AAHZODfTx67hNRrbATR5KqWdFKNgE5Q_9Q0"
GROQCLOUD_API_KEY = "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"
GROQCLOUD_API_URL = "https://api.groq.com/openai/v1/chat/completions"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام! من ربات هوشمند هستم.\n"
        "🧠 پیام‌هایی که با / شروع بشن، با هوش مصنوعی پاسخ داده می‌شن.\n"
        "🔊 روی پیام ریپلای کن و /voice بفرست تا تبدیل به ویس شه."
    )

def is_ai_text_command(text: str) -> bool:
    return text.startswith("/") and not text.startswith("/voice") and len(text) > 1

async def handle_ai_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    text = msg.text.strip()
    if not is_ai_text_command(text):
        return

    prompt = text[1:]

    headers = {
        "Authorization": f"Bearer {GROQCLOUD_API_KEY}",
        "Content-Type": "application/json"
    }
    data = {
        "model": "meta-llama/llama-4-scout-17b-16e-instruct",
        "messages": [{"role": "user", "content": prompt}]
    }

    try:
        response = requests.post(GROQCLOUD_API_URL, json=data, headers=headers)
        if response.status_code == 200:
            result = response.json()
            answer = result['choices'][0]['message']['content']
        else:
            answer = f"❌ خطای هوش مصنوعی (کد {response.status_code})"
    except Exception as e:
        answer = f"❌ خطا در اتصال:\n{e}"

    await msg.reply_text(answer)

async def voice_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message

    if not msg:
        return await update.effective_chat.send_message("❗️ پیامی دریافت نشد.")

    reply = msg.reply_to_message
    if not reply:
        return await msg.reply_text("❗️ لطفا روی یک پیام ریپلای کن و بعد /voice رو بفرست.")

    text = reply.text or reply.caption
    if not text:
        return await msg.reply_text("❗️ متن قابل‌تبدیل پیدا نشد.")

    mp3_file = f"{uuid.uuid4()}.mp3"
    ogg_file = mp3_file.replace(".mp3", ".ogg")

    try:
        communicate = edge_tts.Communicate(text=text, voice="fa-IR-FaridNeural", rate="+0%")
        await communicate.save(mp3_file)

        os.system(f'ffmpeg -i "{mp3_file}" -acodec libopus -b:a 128k "{ogg_file}" -loglevel quiet')

        with open(ogg_file, 'rb') as voice:
            await msg.reply_voice(voice)
    except Exception as e:
        await msg.reply_text(f"❌ خطا در تبدیل:\n{e}")
    finally:
        for f in [mp3_file, ogg_file]:
            if os.path.exists(f):
                os.remove(f)

if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("voice", voice_command))
    app.add_handler(MessageHandler(filters.TEXT, handle_ai_message))

    print("🤖 ربات فعال شد...")
    app.run_polling()
