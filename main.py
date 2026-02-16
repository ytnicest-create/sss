import os
import asyncio
import yt_dlp
import re
from flask import Flask
from threading import Thread
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode, ChatAction
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters, CallbackQueryHandler, CommandHandler
from telegram.request import HTTPXRequest

# --- تنظیمات وب‌سرور برای زنده نگه داشتن ربات ---
flask_app = Flask('')

@flask_app.route('/')
def home():
    return "Bot is Alive!"

def run_flask():
    flask_app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# --- تنظیمات ربات ---
# در رندر، توکن را در بخش Environment Variables با نام TOKEN وارد کنید
TOKEN = "8034421093:AAGZtCS1xfSNxKSgs1Gnn6D_qJQTgc9oEgE"

class ProgressReader:
    def __init__(self, filename):
        self.file = open(filename, 'rb')
        self.total_size = os.path.getsize(filename)
        self.bytes_read = 0

    def read(self, size=-1):
        chunk = self.file.read(size)
        self.bytes_read += len(chunk)
        return chunk

    def close(self):
        self.file.close()

async def simple_end_notifier(status_msg, reader, stop_event):
    notified = False
    while not stop_event.is_set():
        await asyncio.sleep(2)
        if stop_event.is_set(): break
        if reader.total_size > 0:
            percent = (reader.bytes_read / reader.total_size) * 100
            if percent >= 90 and not notified:
                try:
                    await status_msg.edit_text("🏁 **آخرای آپلوده...**\n(الان فایل ارسال میشه ⚡️)", parse_mode=ParseMode.MARKDOWN)
                    notified = True
                except: pass

def sanitize_filename(name):
    name = str(name).replace('\n', ' ').strip()
    return re.sub(r'[\\/*?:"<>|]', "", name)[:100]

def get_info_sync(url):
    opts = {'quiet': True, 'no_warnings': True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text
    if not url.startswith("http"): return
    msg = await update.message.reply_text("🔎")
    try:
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, lambda: get_info_sync(url))
        title = info.get('title', 'Video')
        context.user_data['url'] = url
        context.user_data['title'] = title
        formats = info.get('formats', [])
        buttons = []
        seen_res = set()
        for f in formats:
            res = f.get('height')
            if res and res not in seen_res and res >= 360:
                seen_res.add(res)
                file_size = f.get('filesize') or f.get('filesize_approx') or 0
                size_mb = file_size / 1024 / 1024
                if size_mb < 500: # در رندر رایگان حجم را محدود نگه دارید
                    size_str = f"({int(size_mb)}MB)" if file_size else ""
                    buttons.append([InlineKeyboardButton(f"🎬 {res}p {size_str}", callback_data=f"video|{f['format_id']}")])
        buttons.reverse()
        buttons = buttons[:6]
        buttons.append([InlineKeyboardButton("🎵 موزیک (MP3)", callback_data="audio|best")])
        await msg.edit_text(f"🎬 **{title}**", reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await msg.edit_text(f"❌ خطا: {str(e)[:100]}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data.split("|")
    mode = data[0]
    url = context.user_data.get('url')
    safe_title = sanitize_filename(context.user_data.get('title', 'Video'))
    if not url:
        await query.edit_message_text("❌ لینک منقضی شده.")
        return
    await query.message.edit_reply_markup(None)
    status_msg = await context.bot.send_message(query.message.chat_id, "⏳ **در حال دانلود و پردازش...**")
    temp_id = f"{query.from_user.id}_{query.message.message_id}"
    ext = 'mp3' if mode == 'audio' else 'mp4'
    final_filename = f"{safe_title}.{ext}"
    ydl_opts = {'outtmpl': f'{temp_id}.%(ext)s', 'quiet': True, 'no_warnings': True}
    if mode == 'audio':
        ydl_opts['format'] = 'bestaudio/best'
        ydl_opts['postprocessors'] = [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3'}]
    else:
        ydl_opts['format'] = f"{data[1]}+bestaudio/best"
        ydl_opts['merge_output_format'] = 'mp4'

    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(ydl_opts).download([url]))
        temp_file = f"{temp_id}.{ext}"
        if os.path.exists(temp_file):
            if os.path.exists(final_filename): os.remove(final_filename)
            os.rename(temp_file, final_filename)
            await status_msg.edit_text("📤 **در حال آپلود به تلگرام...**")
            reader = ProgressReader(final_filename)
            stop_event = asyncio.Event()
            monitor_task = asyncio.create_task(simple_end_notifier(status_msg, reader, stop_event))
            await context.bot.send_chat_action(query.message.chat_id, ChatAction.UPLOAD_DOCUMENT)
            try:
                if mode == 'audio':
                    await context.bot.send_audio(query.message.chat_id, audio=reader, title=safe_title, performer="Bot")
                else:
                    await context.bot.send_video(query.message.chat_id, video=reader, caption=f"🎬 {safe_title}", supports_streaming=True)
            finally:
                stop_event.set()
                reader.close()
                await monitor_task
            await status_msg.delete()
            os.remove(final_filename)
        else:
            await status_msg.edit_text("❌ خطا در دانلود.")
    except Exception as e:
        await status_msg.edit_text(f"❌ ارور: {str(e)[:100]}")

if __name__ == '__main__':
    keep_alive() # بیدار نگه داشتن
    request_config = HTTPXRequest(connect_timeout=100, read_timeout=100)
    app = ApplicationBuilder().token(TOKEN).request(request_config).build()
    app.add_handler(CommandHandler("start", lambda u,c: u.message.reply_text("👋 لینک بفرست!")))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("Bot is running...")
    app.run_polling()
