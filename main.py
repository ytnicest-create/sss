# -*- coding: utf-8 -*-
import asyncio
import threading
import os
import subprocess
import uuid
import json
import requests
import yt_dlp

from pyrubi import Client
from pyrubi.types import Message

# -----------------------------
# تنظیمات
# -----------------------------
client = Client("x")

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

DOWNLOAD_DIR = "downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

queue = asyncio.Queue()
is_playing = False
chat_id_global = None

FFMPEG = "ffmpeg"  # اگر تو PATH نیست: r"C:\ffmpeg\bin\ffmpeg.exe"

# 👑 GUID سازنده
OWNER_GUID = "u0I9mnV08612ec7459f3c08a94389255"
ADMINS_FILE = os.path.join(DOWNLOAD_DIR, "admins.json")

MY_GUID = None
stop_now = False

# -----------------------------
# 🔊 کنترل صدا (پایدار + اعمال فوری با ری‌استارت)
# -----------------------------
VOLUME_PERCENT = 100  # 0..200
VOLUME_LOCK = threading.Lock()

current_item = None
restart_after_volume_change = False

def clamp_int(x: int, lo: int, hi: int) -> int:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x

def get_volume_gain() -> float:
    with VOLUME_LOCK:
        vp = VOLUME_PERCENT
    vp = clamp_int(vp, 0, 300)
    return vp / 100.0

def apply_volume_now_if_playing():
    """برای اینکه کاربر وسط پخش حس کنه صدا تغییر کرد:
    پخش جاری رو با empty قطع می‌کنیم و همون آیتم رو دوباره تو صف می‌ذاریم تا با ولوم جدید تبدیل/پخش بشه.
    """
    global stop_now, restart_after_volume_change
    if is_playing and chat_id_global:
        restart_after_volume_change = True
        stop_now = True
        try:
            empty_ogg = ensure_empty_ogg()
            client.play_voice(chat_id_global, empty_ogg)
        except:
            pass


# -----------------------------
# ✅ استخراج‌های امن از message.data
# -----------------------------
def safe_chat_id(message: Message):
    try:
        data = getattr(message, "data", None) or {}
        mus = data.get("message_updates") or []
        if mus:
            return (mus[0] or {}).get("object_guid")
        cus = data.get("chat_updates") or []
        if cus:
            return (cus[0] or {}).get("object_guid")
    except:
        pass
    return None


def safe_author_guid(message: Message):
    try:
        data = getattr(message, "data", None) or {}
        mus = data.get("message_updates") or []
        if mus:
            msg = (mus[0] or {}).get("message") or {}
            return msg.get("author_object_guid")
        cus = data.get("chat_updates") or []
        if cus:
            last = (cus[0] or {}).get("chat", {}).get("last_message", {}) or {}
            return last.get("author_object_guid")
    except:
        pass
    return None


def safe_reply_message_id(message: Message):
    try:
        data = getattr(message, "data", None) or {}
        mus = data.get("message_updates") or []
        if not mus:
            return None
        msg = (mus[0] or {}).get("message") or {}
        rid = msg.get("reply_to_message_id") or msg.get("replied_to_message_id") or msg.get("reply_to_id")
        if isinstance(rid, dict):
            rid = rid.get("message_id")
        return rid
    except:
        return None


def update_my_guid(message: Message):
    global MY_GUID
    try:
        data = getattr(message, "data", None) or {}
        ug = data.get("user_guid")
        if isinstance(ug, str) and ug.startswith("u0"):
            MY_GUID = ug
    except:
        pass


# -----------------------------
# ادمین‌ها
# -----------------------------
def load_admins():
    try:
        if os.path.exists(ADMINS_FILE):
            with open(ADMINS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return set([x for x in data if isinstance(x, str) and x])
    except:
        pass
    return set()


def save_admins(admins_set: set):
    try:
        with open(ADMINS_FILE, "w", encoding="utf-8") as f:
            json.dump(sorted(list(admins_set)), f, ensure_ascii=False, indent=2)
    except:
        pass


admins = load_admins()


def is_admin(user_guid: str) -> bool:
    if not user_guid:
        return False
    return user_guid == OWNER_GUID or user_guid in admins


def extract_author_from_get_messages_by_id(res):
    if not isinstance(res, dict):
        return None
    data = res.get("data")
    if isinstance(data, dict):
        for key in ("messages", "chat_messages", "message_list"):
            msgs = data.get(key)
            if isinstance(msgs, list) and msgs:
                return (msgs[0] or {}).get("author_object_guid")
        m = data.get("message")
        if isinstance(m, dict):
            return m.get("author_object_guid")
    msgs = res.get("messages")
    if isinstance(msgs, list) and msgs:
        return (msgs[0] or {}).get("author_object_guid")
    return None


def fetch_reply_author_guid(chat_id: str, reply_msg_id):
    try:
        res = client.get_messages_by_id(chat_id, [reply_msg_id])
        a = extract_author_from_get_messages_by_id(res)
        if a:
            return a
    except:
        pass
    try:
        rid_int = int(reply_msg_id)
        res = client.get_messages_by_id(chat_id, [rid_int])
        a = extract_author_from_get_messages_by_id(res)
        if a:
            return a
    except:
        pass
    return None


# -----------------------------
# ابزارهای پاکسازی
# -----------------------------
def safe_remove(path: str):
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except:
        pass


def cleanup_downloads(keep_admins=True):
    try:
        for name in os.listdir(DOWNLOAD_DIR):
            p = os.path.join(DOWNLOAD_DIR, name)
            if os.path.isfile(p):
                if keep_admins and name.lower() == "admins.json":
                    continue
                safe_remove(p)
    except:
        pass


# -----------------------------
# ساخت فایل سکوت برای توقف سریع
# -----------------------------
def ensure_empty_ogg() -> str:
    path = os.path.join(DOWNLOAD_DIR, "empty.ogg")
    if os.path.exists(path):
        return path

    cmd = [
        FFMPEG, "-y",
        "-f", "lavfi",
        "-i", "anullsrc=r=48000:cl=mono",
        "-t", "1",
        "-c:a", "libopus",
        "-b:a", "64k",
        path
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return path


# -----------------------------
# تبدیل فایل به ogg/opus با ffmpeg + کنترل صدا (اثر خیلی محسوس)
# -----------------------------
def convert_to_ogg_ffmpeg(input_path: str) -> str:
    out_path = os.path.join(DOWNLOAD_DIR, f"voice_{uuid.uuid4().hex}.ogg")
    gain = get_volume_gain()

    # volume -> limiter (برای اینکه زیاد کردن صدا خراب/کلیپ نشه)
    af = f"volume={gain},alimiter=limit=0.95"

    cmd = [
        FFMPEG, "-y",
        "-i", input_path,
        "-vn",
        "-ac", "1",
        "-ar", "48000",
        "-af", af,
        "-c:a", "libopus",
        "-b:a", "96k",
        out_path
    ]

    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode("utf-8", errors="ignore")
        raise Exception("FFmpeg convert error: " + (err[-600:] if err else "unknown"))

    return out_path


# -----------------------------
# دانلود از یوتیوب
# -----------------------------
async def download_media_from_youtube(query: str) -> tuple[str, str]:
    loop_run = asyncio.get_running_loop()
    outtmpl = os.path.join(DOWNLOAD_DIR, "%(id)s.%(ext)s")

    ydl_opts = {
        "format": "bestaudio/best",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "default_search": "ytsearch1",
        "outtmpl": outtmpl,
        "retries": 5,
        "fragment_retries": 5,
        "concurrent_fragment_downloads": 1,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36"
        }
    }

    def run_ydl():
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f'ytsearch1:"{query}"', download=True)
            if isinstance(info, dict) and "entries" in info:
                entries = [e for e in (info.get("entries") or []) if e]
                if not entries:
                    raise Exception("❌ چیزی پیدا نشد.")
                info = entries[0]
            if not info or not info.get("id"):
                raise Exception("❌ آهنگی پیدا نشد.")
            title = info.get("title") or query
            vid = info["id"]
            candidates = [f for f in os.listdir(DOWNLOAD_DIR) if f.startswith(vid + ".")]
            if not candidates:
                raise Exception("❌ دانلود شد ولی فایل پیدا نشد.")
            return os.path.join(DOWNLOAD_DIR, candidates[0]), title

    return await loop_run.run_in_executor(None, run_ydl)


# -----------------------------
# دانلود فایل ریپلای
# -----------------------------
def download_reply_file(chat_id: str, reply_mid: str) -> str:
    link_info = client.get_download_link(object_guid=chat_id, message_id=reply_mid)
    url = link_info if isinstance(link_info, str) else (link_info.get("download_url") or link_info.get("url"))
    if not url:
        raise Exception("❌ get_download_link لینک نداد")

    out_path = os.path.join(DOWNLOAD_DIR, f"dl_{uuid.uuid4().hex}.bin")
    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()
    with open(out_path, "wb") as f:
        for chunk in r.iter_content(1024 * 256):
            if chunk:
                f.write(chunk)
    return out_path


# -----------------------------
# گرفتن / ساخت voice_chat_id
# -----------------------------
def get_or_create_voice_chat_id(chat_id: str) -> str | None:
    info = None
    try:
        info = client.get_chat_info(object_guid=chat_id)
        vcid = (info or {}).get("chat", {}).get("group_voice_chat_id")
        if vcid:
            return str(vcid)
    except:
        pass

    try:
        client.create_voice_chat(object_guid=chat_id)
        info = client.get_chat_info(object_guid=chat_id)
        vcid = (info or {}).get("chat", {}).get("group_voice_chat_id")
        return str(vcid) if vcid else None
    except:
        return None


# -----------------------------
# ✅ جوین بی‌صدا (هیچ اروری نشون نده)
# -----------------------------
def try_join_call_silent(chat_id: str):
    try:
        if not MY_GUID:
            return
        vcid = get_or_create_voice_chat_id(chat_id)
        if not vcid:
            return
        client.join_voice_chat(chat_id, MY_GUID, vcid)
    except:
        return


# -----------------------------
# پخش از صف
# -----------------------------
async def play_next():
    global is_playing, chat_id_global, stop_now
    global current_item, restart_after_volume_change

    if is_playing:
        return

    is_playing = True
    stop_now = False

    while is_playing:
        if queue.empty() or stop_now:
            break

        item = await queue.get()
        current_item = item
        restart_after_volume_change = False

        src_path = None
        ogg_path = None

        try:
            kind = item[0]

            if kind == "file":
                src_path = item[1]
                title = os.path.basename(src_path)

            elif kind == "reply":
                chat_id, reply_mid = item[1], item[2]
                src_path = await asyncio.to_thread(download_reply_file, chat_id, reply_mid)
                title = "اهنگ"

            elif kind == "yt":
                src_path, title = await download_media_from_youtube(item[1])

            else:
                continue

            if stop_now:
                continue

            ogg_path = await asyncio.to_thread(convert_to_ogg_ffmpeg, src_path)

            if chat_id_global:
                with VOLUME_LOCK:
                    vp = VOLUME_PERCENT
                await asyncio.to_thread(client.send_text, chat_id_global, f"🎶 در حال پخش: {title}  |  🔊 {vp}%")

                await asyncio.to_thread(try_join_call_silent, chat_id_global)
                await asyncio.to_thread(client.play_voice, chat_id_global, ogg_path)

        except Exception as e:
            try:
                if chat_id_global:
                    await asyncio.to_thread(client.send_text, chat_id_global, f"❌ خطا: {e}")
            except:
                pass

        finally:
            # اگر وسط پخش "صدا" زده شد: همین آیتم رو برگردون به صف تا با ولوم جدید دوباره تبدیل/پخش بشه
            if restart_after_volume_change and current_item and chat_id_global:
                try:
                    await queue.put(current_item)
                except:
                    pass
                stop_now = False

            try:
                if ogg_path:
                    safe_remove(ogg_path)
                if src_path:
                    safe_remove(src_path)
                cleanup_downloads(keep_admins=True)
            except:
                pass

            try:
                queue.task_done()
            except:
                pass

    is_playing = False


# -----------------------------
# مدیریت پیام‌ها
# -----------------------------
@client.on_message()
def handle(message: Message):
    global chat_id_global, is_playing, admins, stop_now, VOLUME_PERCENT
    global restart_after_volume_change

    try:
        update_my_guid(message)

        cid = safe_chat_id(message)
        if not cid:
            return

        chat_id_global = cid
        sender = safe_author_guid(message)

        # =========================
        # 👮 دستورات ادمین (اد/حذف/ادمین‌ها)
        # =========================
        if message.text:
            cmd = message.text.strip().lower()

            if cmd == "اد":
                if not sender or not is_admin(sender):
                    try: message.reply("❌ فقط ادمین می‌تونه ادمین اضافه کنه.")
                    except: pass
                    return

                rid = safe_reply_message_id(message)
                if not rid:
                    try: message.reply("❌ باید روی پیام طرف ریپلای کنی و بنویسی: اد")
                    except: pass
                    return

                target = fetch_reply_author_guid(cid, rid)
                if not target:
                    try: message.reply("❌ صاحب پیام ریپلای‌شده پیدا نشد.")
                    except: pass
                    return

                if target == OWNER_GUID:
                    try: message.reply("✅ سازنده خودش ادمینه.")
                    except: pass
                    return

                admins.add(target)
                save_admins(admins)
                try: message.reply("✅ ادمین شد.")
                except: pass
                return

            if cmd == "حذف":
                if sender != OWNER_GUID:
                    try: message.reply("❌ فقط سازنده می‌تونه ادمین حذف کنه.")
                    except: pass
                    return

                rid = safe_reply_message_id(message)
                if not rid:
                    try: message.reply("❌ باید روی پیام طرف ریپلای کنی و بنویسی: حذف")
                    except: pass
                    return

                target = fetch_reply_author_guid(cid, rid)
                if not target:
                    try: message.reply("❌ صاحب پیام ریپلای‌شده پیدا نشد.")
                    except: pass
                    return

                if target in admins:
                    admins.remove(target)
                    save_admins(admins)
                    try: message.reply("✅ از ادمینی حذف شد.")
                    except: pass
                else:
                    try: message.reply("ℹ️ این شخص ادمین نبود.")
                    except: pass
                return

            if cmd == "ادمین‌ها":
                if not sender or not is_admin(sender):
                    try: message.reply("❌ فقط ادمین‌ها می‌تونن ببینن.")
                    except: pass
                    return
                txt = "👮‍♂️ لیست ادمین‌ها:\n" + ("\n".join(sorted(admins)) if admins else "هیچ ادمینی ثبت نشده.")
                try: message.reply(txt)
                except: pass
                return

        # =========================
        # 🔒 قفل همه قابلیت‌ها به ادمین‌ها
        # =========================
        if not sender or not is_admin(sender):
            return

        # =========================
        # 🔊 کنترل صدا
        # =========================
        if message.text:
            t = message.text.strip()
            tl = t.lower()

            if tl == "صدا":
                with VOLUME_LOCK:
                    p = VOLUME_PERCENT
                try: message.reply(f"🔊 صدای فعلی: {p}%")
                except: pass
                return

            if tl.startswith("صدا "):
                val = t.split(" ", 1)[1].strip()
                try:
                    p = int(val)
                except:
                    try: message.reply("❌ مثال درست: صدا 80")
                    except: pass
                    return

                p = clamp_int(p, 0, 200)
                with VOLUME_LOCK:
                    VOLUME_PERCENT = p

                apply_volume_now_if_playing()
                try: message.reply(f"🔊 صدا روی {p}% تنظیم شد")
                except: pass
                return

            if tl in ("بی‌صدا", "بی صدا", "mute"):
                with VOLUME_LOCK:
                    VOLUME_PERCENT = 0
                apply_volume_now_if_playing()
                try: message.reply("🔇 صدا بی‌صدا شد (0%)")
                except: pass
                return

            if tl in ("پیشفرض", "پیش فرض", "default"):
                with VOLUME_LOCK:
                    VOLUME_PERCENT = 100
                apply_volume_now_if_playing()
                try: message.reply("🔊 صدا روی پیشفرض (100%) تنظیم شد")
                except: pass
                return

        # 1) توقف
        if message.text and message.text.strip().lower() == "توقف":
            stop_now = True
            is_playing = False

            try:
                while not queue.empty():
                    queue.get_nowait()
                    queue.task_done()
            except:
                pass

            try:
                empty_ogg = ensure_empty_ogg()
                client.play_voice(chat_id_global, empty_ogg)
            except:
                pass

            cleanup_downloads(keep_admins=True)

            try: message.reply("⏹️ پخش متوقف شد")
            except: pass
            return

        # 2) ریپلای روی فایل + "پخش"
        if message.text and message.text.strip() == "پخش":
            rid = safe_reply_message_id(message)
            if not rid:
                try: message.reply("❌ روی فایل ریپلای کن و فقط بنویس: پخش")
                except: pass
                return

            asyncio.run_coroutine_threadsafe(queue.put(("reply", cid, rid)), loop)

            if not is_playing:
                asyncio.run_coroutine_threadsafe(play_next(), loop)

            try: message.reply("📥 فایل ریپلای به صف اضافه شد")
            except: pass
            return

        # 3) فایل مستقیم (media)
        if getattr(message, "media", None):
            try:
                path = client.download_media(message.media, save_path=DOWNLOAD_DIR)
                if not path or not os.path.exists(path):
                    try: message.reply("❌ دانلود فایل ناموفق بود.")
                    except: pass
                    return

                asyncio.run_coroutine_threadsafe(queue.put(("file", path)), loop)

                if not is_playing:
                    asyncio.run_coroutine_threadsafe(play_next(), loop)

                try: message.reply(f"✅ فایل به صف اضافه شد: {os.path.basename(path)}")
                except: pass
            except Exception as e:
                try: message.reply(f"❌ خطا در دریافت فایل: {e}")
                except: pass
            return

        # 4) "پخش اسم آهنگ" از یوتیوب
        if message.text:
            text = message.text.strip()
            if text.startswith("پخش "):
                query = text.split(" ", 1)[1].strip()
                if not query:
                    try: message.reply("❌ بعد از پخش، اسم آهنگ را بنویس.")
                    except: pass
                    return

                asyncio.run_coroutine_threadsafe(queue.put(("yt", query)), loop)

                if not is_playing:
                    asyncio.run_coroutine_threadsafe(play_next(), loop)

                try: message.reply(f"📥 به صف اضافه شد: {query}")
                except: pass

    except:
        return


# -----------------------------
# اجرای Event Loop در Thread
# -----------------------------
def start_loop():
    asyncio.set_event_loop(loop)
    loop.run_forever()

threading.Thread(target=start_loop, daemon=True).start()

# empty.ogg آماده
try:
    ensure_empty_ogg()
except:
    pass

client.run()
