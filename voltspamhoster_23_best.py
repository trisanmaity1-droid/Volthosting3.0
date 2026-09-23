# VOLT_BOT_BUILDER_V71_PERFECT_STABILITY.py
# Fully Integrated Build: Original VOLT Builder preserved + All Fixedv68 Raid/Spam/Utility commands added to Generated Bot Runtime.
# Updated with new Main Bot Token, Force Join Verification, Referral System, Admin Management, /deladmin, /broadcast, and Direct Sudo Addition (No Pending Approvals).
# Enhanced with 100% Crash-Proof stability fixes, queue overflow protection, and robust exception handling.

import os
import sys
os.environ.setdefault("TZ", "UTC")
os.environ["TZDIR"] = ""
try:
    import time as _t; _t.tzset()
except AttributeError:
    pass

import subprocess
try:
    import PIL
except ImportError:
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pillow"])
    except Exception:
        pass

try:
    import importlib
    _pytz_mod = importlib.import_module("pytz")
    _orig_tz = _pytz_mod.timezone
    def _safe_timezone(zone):
        try:
            return _orig_tz(zone)
        except Exception:
            return _orig_tz("UTC")
    _pytz_mod.timezone = _safe_timezone
except Exception:
    pass

import asyncio
from functools import wraps
import json
import random
import time
import re
import itertools
import io
import secrets
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict
from urllib.parse import quote

try:
    import importlib
    Image = importlib.import_module("PIL.Image")
    ImageDraw = importlib.import_module("PIL.ImageDraw")
    ImageFont = importlib.import_module("PIL.ImageFont")
except Exception:
    import PIL.Image as Image
    import PIL.ImageDraw as ImageDraw
    import PIL.ImageFont as ImageFont

try:
    import telegram
except ImportError:
    try:
        subprocess.check_call([
            sys.executable, "-m", "pip", "install",
            "--disable-pip-version-check", "--no-input",
            "python-telegram-bot>=20,<23"
        ])
        import telegram
    except Exception as _telegram_install_error:
        raise RuntimeError(
            "python-telegram-bot is required but could not be installed."
        ) from _telegram_install_error

from telegram import (
    Update, ChatPermissions, InputMediaPhoto,
    InlineKeyboardMarkup, InlineKeyboardButton, BotCommand, Bot
)
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    MessageHandler, filters, CallbackQueryHandler, ConversationHandler
)
from telegram.constants import ParseMode, ChatAction
from telegram.error import NetworkError, TimedOut, RetryAfter, TelegramError
from telegram.request import HTTPXRequest
import logging

try:
    from gtts import gTTS
    HAS_GTTS = True
except ImportError:
    HAS_GTTS = False

# ============================================================
# VOLT BOT BUILDER & NEW TOKEN CONFIGURATION
# ============================================================

MAIN_BOT_TOKEN = '8612477502:AAEuDUZdBBjDMBDX-YE102eSSpPAd9-5AAY'

OWNER_ID = 8747221712
OWNER_USERNAME = "@Harukasakura01"
COOWNER_ID = 5769074791
OWNER_NAME = "VOLT OWNER"
MAIN_BOT_USERNAME = "Voltspamhoster_bot"

DEFAULT_BOT_LIMIT = 5
DB_FILE = Path(os.getenv("VOLT_DB_FILE", "volt_builder_data.json"))

WAITING_TOKEN = 1

logging.basicConfig(
    level=os.getenv("VOLT_LOG_LEVEL", "WARNING"),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("volt-builder")

db_lock = asyncio.Lock()

MAX_BACKGROUND_TASKS = int(os.getenv("VOLT_MAX_BACKGROUND_TASKS", "200"))
_background_tasks = set()

def _track_task(task):
    _background_tasks.add(task)
    def _done(t):
        _background_tasks.discard(t)
        try:
            exc = t.exception()
            if exc:
                log.error("Background task failed: %r", exc)
        except (asyncio.CancelledError, Exception):
            pass
    task.add_done_callback(_done)
    return task

def _create_tracked_task(coro):
    if len(_background_tasks) >= MAX_BACKGROUND_TASKS:
        coro.close()
        raise RuntimeError("Background task limit reached")
    return _track_task(asyncio.create_task(coro))

# Force Join Configurations
FORCE_CHATS = [
    (-1004304252893, "https://t.me/+2Ynr7yEH5mljM2U0"),
    (-1004355278778, "https://t.me/VOLT_SPAM_UP"),
    (-1004395387264, "https://t.me/+hGp08wVSVhRjZTQ1"),
    (-1003739475692, "https://t.me/VOLT_CHANNEL_UP")
]

def default_db():
    return {
        "db_version": "volt_v71_perfect_stability",
        "users": {},
        "bots": {},
        "settings": {"builder_enabled": True, "bot_username": MAIN_BOT_USERNAME},
    }

def load_db():
    if not DB_FILE.exists():
        return default_db()
    try:
        data = json.loads(DB_FILE.read_text(encoding="utf-8"))
        base = default_db()
        for k, v in base.items():
            data.setdefault(k, v)
        data["db_version"] = "volt_v71_perfect_stability"
        return data
    except Exception:
        log.exception("Database read failed; using a fresh database.")
        return default_db()

DB = load_db()

def save_db():
    try:
        tmp = DB_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(DB, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(DB_FILE)
    except Exception:
        log.exception("Database save failed.")

def escape_md(text):
    if not text:
        return ""
    return str(text).replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")

def user_record(uid, username=""):
    key = str(uid)
    rec = DB["users"].setdefault(key, {
        "username": username or "",
        "role": "user",
        "authorized": True,
        "default_limit": DEFAULT_BOT_LIMIT,
        "extra_quota": 0,
        "created_bots": 0,
        "active": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "referred_by": None,
        "referred_users": [],
        "referral_rewards_claimed": 0,
    })
    if username:
        rec["username"] = username
    return rec

def role_of(uid):
    if uid == OWNER_ID:
        return "owner"
    if uid == COOWNER_ID:
        return "co_owner"
    return user_record(uid).get("role", "user")

def is_management(uid):
    return role_of(uid) in {"owner", "co_owner", "admin"}

def remaining_quota(uid):
    rec = user_record(uid)
    return max(0, int(rec.get("default_limit", DEFAULT_BOT_LIMIT))
               + int(rec.get("extra_quota", 0))
               - int(rec.get("created_bots", 0)))

def mention(user):
    if getattr(user, "username", None):
        return "@" + user.username
    return user.first_name or str(user.id)

async def check_user_joined(bot, user_id):
    if user_id in {OWNER_ID, COOWNER_ID} or is_management(user_id):
        return True
    for chat_id, _ in FORCE_CHATS:
        try:
            member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
            if member.status in ["left", "kicked"]:
                return False
        except Exception:
            pass
    return True

def builder_keyboard(uid):
    if is_management(uid):
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("🤖 CREATE BOT", callback_data="create_bot")],
            [InlineKeyboardButton("📊 MY QUOTA", callback_data="my_quota"),
             InlineKeyboardButton("🎁 REFERRALS", callback_data="referral_menu")],
            [InlineKeyboardButton("👑 OWNER", callback_data="owner_info"),
             InlineKeyboardButton("👑 CO-OWNER", callback_data="coowner_info")],
            [InlineKeyboardButton("🛠️ ADMIN PANEL", callback_data="admin_panel")]
        ])
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 CREATE BOT", callback_data="create_bot")],
        [InlineKeyboardButton("📊 MY QUOTA", callback_data="my_quota"),
         InlineKeyboardButton("🎁 REFERRALS", callback_data="referral_menu")],
        [InlineKeyboardButton("👑 OWNER", callback_data="owner_info"),
         InlineKeyboardButton("👑 CO-OWNER", callback_data="coowner_info")]
    ])

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    bot = context.bot

    is_joined = await check_user_joined(bot, u.id)
    if not is_joined:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Join Group 1", url="https://t.me/+2Ynr7yEH5mljM2U0")],
            [InlineKeyboardButton("💬 Join Group 2", url="https://t.me/VOLT_SPAM_UP")],
            [InlineKeyboardButton("📢 Join Group 3", url="https://t.me/+hGp08wVSVhRjZTQ1")],
            [InlineKeyboardButton("📢 Join Channel", url="https://t.me/VOLT_CHANNEL_UP")],
            [InlineKeyboardButton("🔄 Check Join", callback_data="check_join")]
        ])
        await update.message.reply_text(
            "❌ **FORCE JOIN REQUIRED**\n\n"
            "To use VOLT Bot Builder, you must join our official Groups and Channel first!\n\n"
            "Please join all required chats and click **🔄 Check Join**.",
            reply_markup=kb,
            parse_mode=ParseMode.MARKDOWN
        )
        return

    args = context.args
    rec = user_record(u.id, u.username or "")
    
    if args and args[0].startswith("ref_"):
        try:
            ref_id_str = args[0].replace("ref_", "")
            ref_id = int(ref_id_str)
            if ref_id != u.id and not rec.get("referred_by"):
                ref_rec = DB["users"].get(str(ref_id))
                if ref_rec:
                    rec["referred_by"] = ref_id
                    ref_referred_list = ref_rec.setdefault("referred_users", [])
                    if u.id not in ref_referred_list:
                        ref_referred_list.append(u.id)
                        current_unique = len(ref_referred_list)
                        claimed = ref_rec.get("referral_rewards_claimed", 0)
                        earned_rewards = current_unique // 2
                        if earned_rewards > claimed:
                            diff = earned_rewards - claimed
                            ref_rec["extra_quota"] = int(ref_rec.get("extra_quota", 0)) + diff
                            ref_rec["referral_rewards_claimed"] = earned_rewards
                            try:
                                await context.bot.send_message(
                                    ref_id,
                                    "🎉 **REFERRAL REWARD UNLOCKED!**\n\n"
                                    f"✅ +{diff} FREE BOT CREDIT(S) added for inviting successful users!"
                                )
                            except Exception:
                                pass
        except Exception:
            pass

    save_db()
    text = (
        "⚡ VOLT BOT BUILDER ⚡\n\n"
        "╭────────────────────────╮\n"
        "   🤖 BOT CREATION SYSTEM\n"
        "   Made by VOLT Community ⚡\n"
        "╰────────────────────────╯\n\n"
        f"👑 Owner: {OWNER_USERNAME}\n"
        f"👑 Co-Owner: @ix_aura\n\n"
        "✅ Verified & Authorized via Force Join."
    )
    await update.message.reply_text(text, reply_markup=builder_keyboard(u.id))


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚡ VOLT BOT BUILDER — HELP\n\n"
        "/start — Builder home\n"
        "/token — Create a bot from a BotFather token\n"
        "/addadmin <userid> — Add Main Admin (Owner/Co-owner)\n"
        "/deladmin <userid> — Remove Main Admin\n"
        "/freebot <userid> <count> — Add extra quota (Owner/Co-owner)\n"
        "/addsudo <userid> — Directly add sudo to your generated bot\n"
        "/broadcast <msg> — Broadcast message to all users (Admin/Management)\n"
        "/panel — Main admin panel\n"
        "/id — Show your Telegram ID\n"
        "/help — Show this help\n\n"
        "Generated bots contain full Fixedv71 safe & attack feature sets."
    )


async def id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"🆔 User ID: {update.effective_user.id}\n"
        f"👤 Username: {mention(update.effective_user)}"
    )

# ============================================================
# FIXEDV71 COMPONENTS INTEGRATED INTO GENERATED BOT RUNTIME
# ============================================================

GAALI_PREFIXES = [
    "MADARCHOD", "BHOSDIWALE", "RANDI KE BACHHE", "CHUTMARIKE", "GAANDU", 
    "LUND KE PILLE", "SUAR KE PILLE", "CHUT KE DHAKKAN", "BHADWA", "HIJDE KI AULAAD",
    "CHHAKKE KI AULAAD", "LAVDE KE BAAL", "HARAMKHOR", "KUTTE KI AULAD", "CHUTIYA",
    "MAA KE LUND", "GANDU KE PILLE", "TERI MAA KA BHOSDA", "RANDIKE", "CHUTMARIKA",
    "LODU KE BAAL", "BHOSDIWALA", "HIJDA", "CHHAKKA", "KUTTA", "KAMINA", "BESHARM",
    "LUNDWALE", "CHUTWALE", "GAANDWALE", "BEGHARAT", "ULLU KE PILLE", "GADHEDO",
    "KHAJUR KE PILLE", "CHALIS BAP KA AULAAD", "TAPORI", "CHHINAR KE BACHHE",
    "RANDI KI AULAAD", "MAA KE LAUDE", "BAWLI BOOCH", "CHHOTE LUND KE PILLE"
]
GAALI_BODIES = [
    "TERI MAA KI CHUT ME BOMB PHOD DUNGA", "TERI MAA KA BHOSDA FAAD DUNGA",
    "TERI BHN KI CHUT ME TRACTOR CHALA DUNGA", "TERI MAA KO KOTHE PE BIKWATA HU",
    "TERI GAAND ME SARIYA DAL DUNGA", "TERI KHANDAN KHATAM KAR DUNGA",
    "TERI SHAKAL DEKHI HAI AINE ME KABHI", "TERI MAA KA BHAROSA TOD DUNGA",
    "TERI GAAND ME KANKAR BHAR DUNGA", "TERI AUKAT 2 KODI KI BHI NAHI",
    "TERI MAA KO SADAK PE NACHAUNGA", "TERI MAA KI CHUT ME DANDA DAL DUNGA",
    "TERI MAA KA BHOOL BALYA NIKAL DUNGA", "TERI GAAND ME AGG LAGADUNGA",
    "TERI MAA KO RATING 1 STAR DUNGA", "TERI CHUT ME RAGRA DAL DUNGA",
    "TERA BAAP GAANDU HAI AUR TU USKA NAYAK", "TERI PURI FAMILY KA ACCIDENT KAR DUNGA",
    "TERI MAA KE BHOSDE ME TRAIN CHALA DUNGA", "TERI BAHAN KE SATHMANDU TRIP KARUNGA"
]
GAALI_SUFFIXES = [
    "BSDK", "MADARCHOD", "LUND KE BAAL", "CHUTMARIKE", "GAANDU", 
    "SUAR", "RANDI KI AULAAD", "CHHAKKA", "BHOSDI KE", "LODU", "CHUTIYA",
    "HARAMKHOR", "HIJDA", "LUND", "CHUT", "GAAND", "BHADWA", "LAVDE",
    "CHUTMARIKA", "MAA KE LAWDE", "GANDU SALA"
]

def generate_dynamic_gaali():
    p = random.choice(GAALI_PREFIXES)
    b = random.choice(GAALI_BODIES)
    s = random.choice(GAALI_SUFFIXES)
    return f"{p} {b} {s}"

RAID_TEXTS = [generate_dynamic_gaali() for _ in range(1500)]
STRETCH_PHRASES = [
    "TERI MAA KI CHUT", "GAANDU SALA BSDK", "MADARCHOD KI AULAAD", "TERI AUKAT KYA HAI", 
    "LODE KE BAAL", "CHUT KE DHAKKAN", "TERI MAA KA BHOSDA", "LUND PE NACH", 
] + [f"TERI MAA KI CHUT {random.choice(GAALI_PREFIXES)}" for _ in range(800)]

MATRIX_RAID_TEXTS = [f"MATRIX PROTOCOL OVERLOAD: {generate_dynamic_gaali()}" for _ in range(800)]
MATH_RAID_TEXTS = [f"CALCULATING ERROR 999: {generate_dynamic_gaali()}" for _ in range(800)]
ANIMAL_RAID_TEXTS = [f"ANIMAL ZOO DESTROYED BY {generate_dynamic_gaali()}" for _ in range(800)]
THARKIBAAZ_RAID_TEXTS = [f"🔥 VOLT_BOT {generate_dynamic_gaali()} 🥵" for _ in range(800)]
EMOJI_RAID_TEXTS = [f"🔥🤣 {generate_dynamic_gaali()} 🍑💦🗿" for _ in range(800)]
ONE_WORD_RAID = [random.choice(GAALI_PREFIXES) for _ in range(1000)]
NCEMO_EMOJIS = ["🗿","👑","🩵","🔱","🌷","❤️‍🩹","🎀","👽","🤣","😭","💔","🥺","😁","👿","🚀","🔥"]
saykobot_TEXTS = ["💀","🔥","⚡","🎯","💥","🎪","🎭","👑","🔱","⚜️"]
BOOM_SPAM_TEMPLATES = [f"BOOM!! {generate_dynamic_gaali()}" for _ in range(800)]
NUKE_TEMPLATES = [f"☢️ NUKE LAUNCHED: {generate_dynamic_gaali()}" for _ in range(800)]
JOOTI_SPAM_TEXTS = [f"👞 JOOTI KA HAAR: {generate_dynamic_gaali()} 🥾" for _ in range(800)]
GULAM_SPAM_TEXTS = [f"😈 GULAM BANAA DIYA: {generate_dynamic_gaali()} 👟" for _ in range(800)]
TITAN_TEXTS = [f"🗻 TITAN SMASH: {generate_dynamic_gaali()} 💀" for _ in range(800)]
OMEGA_TEXTS = [f"Ω OMEGA DESTRUCTION: {generate_dynamic_gaali()} ⚡" for _ in range(800)]
NEMESIS_TEXTS = [f"⚖️ NEMESIS VERDICT: {generate_dynamic_gaali()} 💀" for _ in range(800)]
REAPER_TEXTS = [f"💀 REAPER SOUL SNATCH: {generate_dynamic_gaali()} 🩸" for _ in range(800)]
APOCALYPSE_TEXTS = [f"☄️ APOCALYPSE DESTRUCTION: {generate_dynamic_gaali()} 💀" for _ in range(800)]
PHANTOM_TEXTS = [f"👻 PHANTOM SHADOW ATTACK: {generate_dynamic_gaali()} 🌑" for _ in range(800)]
INFERNO_TEXTS = [f"🔥 INFERNO BURN: {generate_dynamic_gaali()} 💀" for _ in range(800)]
QUANTUM_TEXTS = [f"⚛️ QUANTUM DIMENSION COLLAPSE: {generate_dynamic_gaali()} 💀" for _ in range(800)]
DEATH_RAID_TEXTS = [f"☠️ DEATH EXECUTION: {generate_dynamic_gaali()} 💀" for _ in range(800)]
STORM_TEMPLATES = [f"🌪️ STORM HELLSTORM: {generate_dynamic_gaali()} 💀" for _ in range(800)]
CHAIN_MESSAGES = [f"🔗 CHAIN LOCK ATTACK: {generate_dynamic_gaali()} 🔗" for _ in range(800)]
PIC_RAID_TEXTS = ["TERI MAA KI CHUTH 🖕", "TERI DIDI KA BHOSDA 💥", "MADARCHOD SALA BSDK 🗿", "RANDI KE BACHHE 👑"]
HINGLISH_SAYARI_LIST = [
    "Zindagi jeena hai toh shaan se jiyo, warna kisi ke baap ke naam se jiyo! 😈",
    "Aukat ki baat mat kar pagle, hum toh aaina bhi dekhte hain toh woh bhi hil jata hai! 🔥",
    "Sher apna raasta khud banata hai, kisi ke sahare nahi jeeta! 🗿",
    "Pehle apni aukaat banao, phir humko aankh dikhao! 🖕",
    "Baap baap hota hai, aur beta hamesha beta hi rehta hai! 👑"
]
GHOST_EMOJIS = ["👻","💀","🔮","🌑","⬛","🖤","🕷️","🦇","☠️","🩸"]
ZALGO_CHARS = ['\u0300','\u0301','\u0302','\u0303','\u0304','\u0305','\u0306','\u0307']

def to_zalgo(text, intensity=2):
    result = []
    for char in text:
        result.append(char)
        for _ in range(random.randint(intensity, intensity * 2)):
            result.append(random.choice(ZALGO_CHARS))
    return "".join(result)

BOT_QUEUES: dict = {}
BOT_WORKERS: dict = {}
WORKERS_PER_BOT = max(2, min(4, int(os.getenv("VOLT_WORKERS_PER_BOT", "3"))))

CHAT_ACTIVE_TASKS = defaultdict(list)

async def _bot_worker(bot):
    bid = id(bot)
    q = BOT_QUEUES[bid]
    while True:
        try:
            chat_id, text, reply_to_msg_id = await q.get()
            try:
                await bot.send_message(
                    chat_id=chat_id, 
                    text=text, 
                    reply_to_message_id=reply_to_msg_id if reply_to_msg_id else None
                )
            except RetryAfter as e:
                await asyncio.sleep(min(float(e.retry_after) + 0.5, 30.0))
                try:
                    q.put_nowait((chat_id, text, reply_to_msg_id))
                except asyncio.QueueFull:
                    pass
            except Exception as e:
                err = str(e).lower()
                if "flood control" in err or "too many requests" in err or "429" in err:
                    await asyncio.sleep(2.0)
                    try:
                        q.put_nowait((chat_id, text, reply_to_msg_id))
                    except asyncio.QueueFull:
                        pass
            q.task_done()
        except asyncio.CancelledError:
            return
        except Exception:
            await asyncio.sleep(0.05)

async def _ensure_workers(bot):
    bid = id(bot)
    if bid not in BOT_QUEUES:
        BOT_QUEUES[bid] = asyncio.Queue(maxsize=int(os.getenv("VOLT_QUEUE_MAXSIZE", "500")))
        BOT_WORKERS[bid] = [_create_tracked_task(_bot_worker(bot)) for _ in range(WORKERS_PER_BOT)]

async def _safe_send(bot, chat_id, text, reply_to_msg_id=None):
    await _ensure_workers(bot)
    q = BOT_QUEUES[id(bot)]
    try:
        q.put_nowait((chat_id, text, reply_to_msg_id))
    except asyncio.QueueFull:
        try:
            await asyncio.wait_for(q.put((chat_id, text, reply_to_msg_id)), timeout=1.0)
        except Exception:
            pass

async def _set_title_safe(bot, chat_id, text):
    try:
        await bot.set_chat_title(chat_id, text[:255])
        return True
    except Exception:
        return False

def get_target_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    target = ""
    count = 0
    reply_to_id = None
    if update.message and update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user
        reply_to_id = update.message.reply_to_message.message_id
        if target_user:
            target = f"@{target_user.username}" if target_user.username else target_user.first_name
    if args:
        for arg in args:
            if arg.isdigit():
                count = int(arg)
            else:
                target = arg
    if not target:
        target = "@" + update.effective_user.username if update.effective_user and update.effective_user.username else "TARGET"
    if target.startswith("@"):
        target = "@" + target.lstrip("@")
    return target, count, reply_to_id

def generate_stretch_text():
    phrase = random.choice(STRETCH_PHRASES)
    result = []
    for char in phrase:
        if char == " ":
            result.append(" ")
        else:
            rep = random.randint(3, 6)
            chunk = char.upper() * rep
            result.append(chunk)
    return "".join(result)

def generate_black_raid_image(text):
    img = Image.new('RGB', (800, 500), color=(0, 0, 0))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 36)
    except Exception:
        font = ImageFont.load_default()
    d.text((60, 200), text, fill=(255, 255, 255), font=font)
    output = io.BytesIO()
    img.save(output, format='JPEG')
    output.seek(0)
    return output

async def list_spam_loop(bot, chat_id, target, templates, reply_to_id=None):
    i = 0
    while True:
        try:
            msg = f"{target} {templates[i % len(templates)]}"
            await _safe_send(bot, chat_id, msg, reply_to_id)
            i += 1
            await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            return
        except Exception:
            await asyncio.sleep(0.05)

async def limited_list_loop(bot, chat_id, target, templates, count, reply_to_id=None):
    for i in range(count):
        try:
            msg = f"{target} {templates[i % len(templates)]}"
            await _safe_send(bot, chat_id, msg, reply_to_id)
            await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            return
        except Exception:
            pass

async def stretch_raid_loop(bot, chat_id, target, reply_to_id=None):
    while True:
        try:
            msg = f"{target} {generate_stretch_text()}"
            await _safe_send(bot, chat_id, msg, reply_to_id)
            await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            return
        except Exception:
            await asyncio.sleep(0.05)

async def limited_stretch_loop(bot, chat_id, target, count, reply_to_id=None):
    for _ in range(count):
        try:
            msg = f"{target} {generate_stretch_text()}"
            await _safe_send(bot, chat_id, msg, reply_to_id)
            await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            return
        except Exception:
            pass

async def custom_spam_loop(bot, chat_id, target, text, count, reply_to_id=None):
    if count > 0:
        for _ in range(count):
            try:
                await _safe_send(bot, chat_id, f"{target} {text}", reply_to_id)
                await asyncio.sleep(0.02)
            except asyncio.CancelledError:
                return
            except Exception:
                pass
    else:
        while True:
            try:
                await _safe_send(bot, chat_id, f"{target} {text}", reply_to_id)
                await asyncio.sleep(0.01)
            except asyncio.CancelledError:
                return
            except Exception:
                await asyncio.sleep(0.05)

async def ghost_ping_loop(bot, chat_id, uid, reply_to_id=None):
    while True:
        try:
            emoji = random.choice(GHOST_EMOJIS)
            msg = await bot.send_message(chat_id, f"[​](tg://user?id={uid}) {emoji}", reply_to_message_id=reply_to_id)
            await asyncio.sleep(0.1)
            try:
                await bot.delete_message(chat_id, msg.message_id)
            except Exception:
                pass
        except asyncio.CancelledError:
            return
        except Exception:
            await asyncio.sleep(0.2)

async def warcry_loop(bot, chat_id, target, reply_to_id=None):
    cries = [f"⚔️ {target} VOLT ATTACK ⚔️", f"🩸 {target} {to_zalgo('DESTROYED', 1)} 🩸"]
    i = 0
    while True:
        try:
            await _safe_send(bot, chat_id, cries[i % len(cries)], reply_to_id)
            i += 1
            await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            return
        except Exception:
            await asyncio.sleep(0.05)

async def gcnc_loop(bot, chat_id, base, mode="gcnc"):
    i = 0
    while True:
        try:
            if mode == "ncemo":
                text = f"{base} {NCEMO_EMOJIS[i % len(NCEMO_EMOJIS)]}"
            elif mode == "godspeed":
                text = f"{base} {saykobot_TEXTS[i % len(saykobot_TEXTS)]}"
            else:
                text = f"{base} {RAID_TEXTS[i % len(RAID_TEXTS)]}"
            await _set_title_safe(bot, chat_id, text)
            i += 1
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return
        except Exception:
            await asyncio.sleep(1.5)

GENERATED_TASKS = {}

def gen_bot_role(bot_id, user_id):
    item = DB["bots"].get(str(bot_id))
    if not item:
        return "none"
    if user_id in {OWNER_ID, COOWNER_ID} or is_management(user_id):
        return "main"
    if user_id == item.get("owner_id"):
        return "owner"
    if user_id in item.get("sudos", []):
        return "sudo"
    return "user"

def gen_role_allows_safe(bot_id, user_id):
    return gen_bot_role(bot_id, user_id) in {"main", "owner", "sudo"}

async def generated_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    me = await _cached_get_me(update.get_bot())
    uid = update.effective_user.id
    role = gen_bot_role(me.id, uid)
    role_tag = {"main": "👑 MAIN OWNER", "owner": "👑 BOT OWNER", "sudo": "🛡️ SUDO USER", "user": "👤 USER"}.get(role, "👤 USER")

    text = (
        "⚡ VOLT GENERATED BOT ⚡\n\n"
        f"🤖 Status: ONLINE\n"
        f"🎭 Your Role: {role_tag}\n\n"
        "Choose an option below:\n\n"
        "⚡ Powered by @Voltspamhoster_bot\n"
        "💡 Make ur own spam bot"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📚 COMMANDS", callback_data="gen_help")],
        [InlineKeyboardButton("👑 BOT OWNER", callback_data="gen_owner"),
         InlineKeyboardButton("📊 STATUS", callback_data="gen_status")],
        [InlineKeyboardButton("🆔 MY ID", callback_data="gen_id")],
        [InlineKeyboardButton("🤖 Make ur own spam bot", url="https://t.me/Voltspamhoster_bot")]
    ])
    await update.message.reply_text(text, reply_markup=kb)

async def generated_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    me = await _cached_get_me(update.get_bot())
    uid = update.effective_user.id
    role = gen_bot_role(me.id, uid)

    lines = [
        "👑 VOLT GENERATED BOT — ALL COMMANDS 👑",
        "",
        "👤 NORMAL COMMANDS:",
        "/start — Bot home",
        "/help — Show commands",
        "/id — Show your Telegram ID",
        "/owner — Show bot owner",
        "/status — Show bot status",
        "/ping — Check response time",
        "/sayari — Random attitude shayari",
        "",
    ]
    if role in {"main", "owner", "sudo"}:
        lines += [
            "🔥 ATTACK & RAID COMMANDS:",
            "/raid <target> [count]",
            "/stretchraid <target> [count]",
            "/oneword <target> [count]",
            "/matrixraid <target> [count]",
            "/mathraid <target> [count]",
            "/animalraid <target> [count]",
            "/tharkibaazraid <target> [count]",
            "/emojiraid <target> [count]",
            "/spam <target> [count] <text>",
            "/boom <target>",
            "/nuke <target> [count]",
            "/jooti <target> [count]",
            "/gulam <target> [count]",
            "/titan <target> [count]",
            "/omega <target> [count]",
            "/nemesis <target> [count]",
            "/reaper <target> [count]",
            "/apocalypse <target> [count]",
            "/phantom <target> [count]",
            "/inferno <target> [count]",
            "/quantum <target> [count]",
            "/deathraid <target> [count]",
            "/storm <target> [count]",
            "/warcry <target> [count]",
            "/chain <target> [count]",
            "/ghost <user_id>",
            "",
            "👑 GC TITLE RAID COMMANDS:",
            "/gcnc <title> /ncgc <title>",
            "/ncemo <title>",
            "/godspeed <title>",
            "",
            "🎯 MEDIA & TOOLS:",
            "/picraid [count]",
            "/hack <target>",
            "/voice <count> <text>",
            "/stop — Instantly stop active attacks & queue (0.1s ultra-powerful)",
            ""
        ]
    if role in {"main", "owner"}:
        lines += [
            "👑 OWNER COMMANDS:",
            "/addsudo <userid> — Directly add sudo user",
            "/sudolist — List approved sudo users",
            "/delsudo <userid> — Remove a sudo user",
            ""
        ]
    await update.message.reply_text("\n".join(lines))

async def generated_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await update.message.reply_text(f"🆔 User ID: {u.id}\n👤 Username: {mention(u)}")

async def generated_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    item = DB["bots"].get(str(bot_id), {})
    owner_id = item.get("owner_id")
    owner_rec = DB["users"].get(str(owner_id), {})
    username = owner_rec.get("username") or "not set"
    await update.message.reply_text(
        "👑 BOT OWNER\n\n"
        f"🆔 {owner_id or 'unknown'}\n"
        f"👤 {('@' + username.lstrip('@')) if username != 'not set' else username}"
    )

async def generated_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    item = DB["bots"].get(str(bot_id), {})
    await update.message.reply_text(
        "📊 BOT STATUS\n\n"
        f"🤖 @{item.get('username') or 'unknown'}\n"
        f"🟢 Status: {item.get('status', 'ON')}\n"
        f"👥 Sudo users: {len(item.get('sudos', []))}"
    )

async def generated_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    t0 = time.monotonic()
    msg = await update.message.reply_text("🏓 PONG")
    t1 = time.monotonic()
    ms = round((t1 - t0) * 1000)
    try:
        await msg.edit_text(f"🏓 BOT ONLINE [ {ms}ms ]")
    except TelegramError:
        pass

async def generated_sayari(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"📜 **ATTITUDE SHAYARI** 📜\n\n{random.choice(HINGLISH_SAYARI_LIST)}", parse_mode=ParseMode.MARKDOWN)

async def generated_hack(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    uid = update.effective_user.id
    if not gen_role_allows_safe(bot_id, uid):
        await update.message.reply_text("❌ Owner/Sudo only command.")
        return
    target, _, _ = get_target_info(update, context)
    msg = await update.message.reply_text(f"💻 Initiating Fake Hack on {target}...")
    steps = [
        f"💻 Connecting to secure mainframes for {target}...\n[□□□□□□□□□□] 10%",
        f"🔓 Bypassing firewall & security protocols...\n[████□□□□□□] 40%",
        f"📂 Extracting personal metadata & logs...\n[████████□□] 80%",
        f"🔥 SYSTEM BREACHED SUCCESSFULLY!\n[██████████] 100%\n\nTarget {target} completely compromised!"
    ]
    for step in steps:
        await asyncio.sleep(0.8)
        try:
            await msg.edit_text(step)
        except TelegramError:
            pass

async def generated_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    uid = update.effective_user.id
    if not gen_role_allows_safe(bot_id, uid):
        await update.message.reply_text("❌ Owner/Sudo only command.")
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /voice [count] <text>")
        return
    count = 1
    text_args = args
    if args[0].isdigit():
        count = max(1, min(5, int(args[0])))
        text_args = args[1:]
    text = " ".join(text_args).strip() or "hello"
    chat_id = update.effective_chat.id
    if not HAS_GTTS:
        await update.message.reply_text("🎙️ TTS unavailable (gTTS missing).")
        return
    for _ in range(count):
        try:
            tts = gTTS(text=text, lang="hi", tld="co.in", slow=False)
            fp = io.BytesIO()
            tts.write_to_fp(fp)
            fp.seek(0)
            await context.bot.send_voice(chat_id=chat_id, voice=fp, caption="🎙️ Real Voice Note")
            await asyncio.sleep(0.4)
        except Exception:
            break

def check_gen_permission(fn):
    @wraps(fn)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        bot_id = update.get_bot().id
        uid = update.effective_user.id
        if not gen_role_allows_safe(bot_id, uid):
            await update.message.reply_text("❌ Owner or Sudo User only command.")
            return
        return await fn(update, context)
    return wrapper

def launch_tracked_task(bot_id, chat_id, coro):
    if len(_background_tasks) >= MAX_BACKGROUND_TASKS:
        coro.close()
        raise RuntimeError("Background task limit reached")
    task = _track_task(asyncio.create_task(coro))
    CHAT_ACTIVE_TASKS[(bot_id, chat_id)].append(task)
    def _cleanup(t):
        if t in CHAT_ACTIVE_TASKS[(bot_id, chat_id)]:
            CHAT_ACTIVE_TASKS[(bot_id, chat_id)].remove(t)
    task.add_done_callback(_cleanup)
    return task

@check_gen_permission
async def gen_raid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    if count > 0:
        launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, RAID_TEXTS, count, reply_to_id))
    else:
        launch_tracked_task(bot_id, chat_id, list_spam_loop(context.bot, chat_id, target, RAID_TEXTS, reply_to_id))
    await update.message.reply_text("🔥 Raid Started!")

@check_gen_permission
async def gen_stretchraid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    if count > 0:
        launch_tracked_task(bot_id, chat_id, limited_stretch_loop(context.bot, chat_id, target, count, reply_to_id))
    else:
        launch_tracked_task(bot_id, chat_id, stretch_raid_loop(context.bot, chat_id, target, reply_to_id))
    await update.message.reply_text("💥 Stretch Raid Started!")

@check_gen_permission
async def gen_matrixraid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    if count > 0:
        launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, MATRIX_RAID_TEXTS, count, reply_to_id))
    else:
        launch_tracked_task(bot_id, chat_id, list_spam_loop(context.bot, chat_id, target, MATRIX_RAID_TEXTS, reply_to_id))
    await update.message.reply_text("💻 Matrix Raid Started!")

@check_gen_permission
async def gen_mathraid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    if count > 0:
        launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, MATH_RAID_TEXTS, count, reply_to_id))
    else:
        launch_tracked_task(bot_id, chat_id, list_spam_loop(context.bot, chat_id, target, MATH_RAID_TEXTS, reply_to_id))
    await update.message.reply_text("📐 Math Raid Started!")

@check_gen_permission
async def gen_animalraid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    if count > 0:
        launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, ANIMAL_RAID_TEXTS, count, reply_to_id))
    else:
        launch_tracked_task(bot_id, chat_id, list_spam_loop(context.bot, chat_id, target, ANIMAL_RAID_TEXTS, reply_to_id))
    await update.message.reply_text("🦁 Animal Raid Started!")

@check_gen_permission
async def gen_tharkibaazraid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    if count > 0:
        launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, THARKIBAAZ_RAID_TEXTS, count, reply_to_id))
    else:
        launch_tracked_task(bot_id, chat_id, list_spam_loop(context.bot, chat_id, target, THARKIBAAZ_RAID_TEXTS, reply_to_id))
    await update.message.reply_text("⚡ VOLT Raid Started!")

@check_gen_permission
async def gen_emojiraid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    if count > 0:
        launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, EMOJI_RAID_TEXTS, count, reply_to_id))
    else:
        launch_tracked_task(bot_id, chat_id, list_spam_loop(context.bot, chat_id, target, EMOJI_RAID_TEXTS, reply_to_id))
    await update.message.reply_text("😂 Emoji Raid Started!")

@check_gen_permission
async def gen_oneword_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    if count > 0:
        launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, ONE_WORD_RAID, count, reply_to_id))
    else:
        launch_tracked_task(bot_id, chat_id, list_spam_loop(context.bot, chat_id, target, ONE_WORD_RAID, reply_to_id))
    await update.message.reply_text("⚡ OneWord Raid Started!")

@check_gen_permission
async def gen_spam_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    args = context.args
    target, count, reply_to_id = get_target_info(update, context)
    text = " ".join([a for a in args if not a.isdigit() and not a.startswith("@")]) or "SPAM"
    launch_tracked_task(bot_id, chat_id, custom_spam_loop(context.bot, chat_id, target, text, count if count else 0, reply_to_id))
    await update.message.reply_text("🔥 Custom Spam Started!")

@check_gen_permission
async def gen_boom_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, _, reply_to_id = get_target_info(update, context)
    launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, BOOM_SPAM_TEMPLATES, 50, reply_to_id))
    await update.message.reply_text("💥 BOOM Attack Triggered!")

@check_gen_permission
async def gen_nuke_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, NUKE_TEMPLATES, count if count else 100, reply_to_id))
    await update.message.reply_text("☢️ NUKE Strike Deployed!")

@check_gen_permission
async def gen_jooti_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, JOOTI_SPAM_TEXTS, count if count else 50, reply_to_id))
    await update.message.reply_text("👞 Jooti Raid Started!")

@check_gen_permission
async def gen_gulam_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, GULAM_SPAM_TEXTS, count if count else 50, reply_to_id))
    await update.message.reply_text("😈 Gulam Raid Started!")

@check_gen_permission
async def gen_titan_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, TITAN_TEXTS, count if count else 50, reply_to_id))
    await update.message.reply_text("🗻 Titan Raid Started!")

@check_gen_permission
async def gen_omega_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, OMEGA_TEXTS, count if count else 50, reply_to_id))
    await update.message.reply_text("Ω Omega Raid Started!")

@check_gen_permission
async def gen_nemesis_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, NEMESIS_TEXTS, count if count else 50, reply_to_id))
    await update.message.reply_text("⚖️ Nemesis Raid Started!")

@check_gen_permission
async def gen_reaper_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, REAPER_TEXTS, count if count else 50, reply_to_id))
    await update.message.reply_text("💀 Reaper Raid Started!")

@check_gen_permission
async def gen_apocalypse_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, APOCALYPSE_TEXTS, count if count else 50, reply_to_id))
    await update.message.reply_text("☄️ Apocalypse Raid Started!")

@check_gen_permission
async def gen_phantom_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, PHANTOM_TEXTS, count if count else 50, reply_to_id))
    await update.message.reply_text("👻 Phantom Raid Started!")

@check_gen_permission
async def gen_inferno_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, INFERNO_TEXTS, count if count else 50, reply_to_id))
    await update.message.reply_text("🔥 Inferno Raid Started!")

@check_gen_permission
async def gen_quantum_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, QUANTUM_TEXTS, count if count else 50, reply_to_id))
    await update.message.reply_text("⚛️ Quantum Raid Started!")

@check_gen_permission
async def gen_deathraid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, DEATH_RAID_TEXTS, count if count else 50, reply_to_id))
    await update.message.reply_text("☠️ Death Raid Started!")

@check_gen_permission
async def gen_storm_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, STORM_TEMPLATES, count if count else 50, reply_to_id))
    await update.message.reply_text("🌪️ Storm Raid Started!")

@check_gen_permission
async def gen_warcry_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, _, reply_to_id = get_target_info(update, context)
    launch_tracked_task(bot_id, chat_id, warcry_loop(context.bot, chat_id, target, reply_to_id))
    await update.message.reply_text("⚔️ Warcry Started!")

@check_gen_permission
async def gen_chain_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    target, count, reply_to_id = get_target_info(update, context)
    launch_tracked_task(bot_id, chat_id, limited_list_loop(context.bot, chat_id, target, CHAIN_MESSAGES, count if count else 50, reply_to_id))
    await update.message.reply_text("🔗 Chain Attack Started!")

@check_gen_permission
async def gen_ghost_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /ghost <user_id>")
        return
    uid = int(args[0])
    launch_tracked_task(bot_id, chat_id, ghost_ping_loop(context.bot, chat_id, uid))
    await update.message.reply_text(f"👻 Ghost Ping started for {uid}")

@check_gen_permission
async def gen_gcnc_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /gcnc <title> (or /ncgc <title>)")
        return
    title = " ".join(args)
    launch_tracked_task(bot_id, chat_id, gcnc_loop(context.bot, chat_id, title, "gcnc"))
    await update.message.reply_text("👑 GC Title Raid Started!")

@check_gen_permission
async def gen_ncemo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /ncemo <title>")
        return
    title = " ".join(args)
    launch_tracked_task(bot_id, chat_id, gcnc_loop(context.bot, chat_id, title, "ncemo"))
    await update.message.reply_text("👑 Emoji GC Title Raid Started!")

@check_gen_permission
async def gen_godspeed_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id
    args = context.args
    if not args:
        await update.message.reply_text("Usage: /godspeed <title>")
        return
    title = " ".join(args)
    launch_tracked_task(bot_id, chat_id, gcnc_loop(context.bot, chat_id, title, "godspeed"))
    await update.message.reply_text("⚡ Godspeed GC Title Raid Started!")

@check_gen_permission
async def gen_picraid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target, count, reply_to_id = get_target_info(update, context)
    if count <= 0: count = 1
    if count > 5: count = 5
    chat_id = update.effective_chat.id
    if not reply_to_id and update.message:
        reply_to_id = update.message.message_id
    await update.message.reply_text(f"📸 Pic Raid Started! Sending {count} custom images...")
    for _ in range(count):
        try:
            img_io = generate_black_raid_image(random.choice(PIC_RAID_TEXTS))
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=img_io,
                caption="🔥 PIC RAID 🔥",
                reply_to_message_id=reply_to_id,
                parse_mode=ParseMode.MARKDOWN
            )
            await asyncio.sleep(0.5)
        except Exception:
            pass

@check_gen_permission
async def gen_stop_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    chat_id = update.effective_chat.id

    tasks = CHAT_ACTIVE_TASKS.get((bot_id, chat_id), [])
    for t in tasks:
        if not t.done():
            t.cancel()
    CHAT_ACTIVE_TASKS[(bot_id, chat_id)] = []

    bid = id(context.bot)
    if bid in BOT_QUEUES:
        q = BOT_QUEUES[bid]
        while not q.empty():
            try:
                q.get_nowait()
                q.task_done()
            except Exception:
                break

    await update.message.reply_text("🛑 **ULTRA-POWERFUL STOP (0.1s):** All active raids, loops, and queued messages have been successfully terminated instantly!")

async def generated_addsudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    uid = update.effective_user.id
    item = DB["bots"].get(str(bot_id))
    if not item:
        await update.message.reply_text("❌ Generated bot is not registered.")
        return
    if gen_bot_role(bot_id, uid) not in {"owner", "main"}:
        await update.message.reply_text("❌ Bot Owner only command.")
        return
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /addsudo <userid>")
        return
    target = int(args[0])
    if target <= 0:
        await update.message.reply_text("❌ Invalid Telegram user ID.")
        return
    if target == item.get("owner_id"):
        await update.message.reply_text("ℹ️ The Bot Owner is already the owner.")
        return

    sudos = item.setdefault("sudos", [])
    if target in sudos:
        await update.message.reply_text("ℹ️ This user is already a sudo user on this bot.")
        return

    sudos.append(target)
    save_db()
    await update.message.reply_text(
        f"✅ **SUDO ADDED SUCCESSFULLY**\n\n"
        f"🆔 User ID: `{target}`\n"
        "🛡️ Status: Approved Sudo User",
        parse_mode=ParseMode.MARKDOWN
    )

async def generated_sudolist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    uid = update.effective_user.id
    if gen_bot_role(bot_id, uid) not in {"main", "owner"}:
        await update.message.reply_text("❌ Bot Owner only command.")
        return
    item = DB["bots"].get(str(bot_id), {})
    sudos = item.get("sudos", [])
    if not sudos:
        await update.message.reply_text("👥 No approved sudo users yet.")
        return
    lines = ["👥 APPROVED SUDO USERS", ""]
    for s in sudos:
        lines.append(f"• {s}")
    await update.message.reply_text("\n".join(lines))

async def generated_delsudo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_id = update.get_bot().id
    uid = update.effective_user.id
    if gen_bot_role(bot_id, uid) not in {"main", "owner"}:
        await update.message.reply_text("❌ Bot Owner only command.")
        return
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /delsudo <userid>")
        return
    target = int(args[0])
    item = DB["bots"].get(str(bot_id))
    if not item:
        return
    sudos = item.setdefault("sudos", [])
    if target in sudos:
        sudos.remove(target)
        save_db()
        await update.message.reply_text(f"✅ Removed sudo user {target}.")
    else:
        await update.message.reply_text("ℹ️ That user is not a sudo on this bot.")

async def generated_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not q:
        return
    data = q.data or ""
    try:
        await q.answer()
    except Exception:
        pass

    try:
        me = await _cached_get_me(context.bot)
        uid = q.from_user.id
        role = gen_bot_role(me.id, uid)

        if data == "gen_help":
            role_tag = gen_bot_role(me.id, uid)
            lines = [
                "👑 VOLT GENERATED BOT — COMMANDS 👑", "",
                "👤 NORMAL COMMANDS:",
                "/start — Bot home",
                "/help — Show commands",
                "/commands — Show commands",
                "/id — Show your Telegram ID",
                "/owner — Show bot owner",
                "/status — Show bot status",
                "/ping — Check response time",
                "/sayari — Random attitude shayari",
                ""
            ]
            if role_tag in {"main", "owner", "sudo"}:
                lines += [
                    "ℹ️ Additional owner-only features are available to authorized users.",
                    ""
                ]
            await q.edit_message_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ BACK", callback_data="gen_home")]]
                )
            )
            return

        if data == "gen_home":
            role_tag = {"main": "👑 MAIN OWNER", "owner": "👑 BOT OWNER",
                        "sudo": "🛡️ SUDO USER", "user": "👤 USER"}.get(role, "👤 USER")
            await q.edit_message_text(
                "⚡ VOLT GENERATED BOT ⚡\n\n"
                f"🤖 Status: ONLINE\n"
                f"🎭 Your Role: {role_tag}\n\n"
                "Choose an option below:",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📚 COMMANDS", callback_data="gen_help")],
                    [InlineKeyboardButton("👑 BOT OWNER", callback_data="gen_owner"),
                     InlineKeyboardButton("📊 STATUS", callback_data="gen_status")],
                    [InlineKeyboardButton("🆔 MY ID", callback_data="gen_id")]
                ])
            )
            return

        if data == "gen_owner":
            item = DB["bots"].get(str(me.id), {})
            owner_id = item.get("owner_id")
            owner_rec = DB["users"].get(str(owner_id), {})
            username = owner_rec.get("username") or "not set"
            await q.edit_message_text(
                "👑 BOT OWNER\n\n"
                f"🆔 {owner_id or 'unknown'}\n"
                f"👤 {('@' + username.lstrip('@')) if username != 'not set' else username}",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ BACK", callback_data="gen_home")]]
                )
            )
            return

        if data == "gen_status":
            item = DB["bots"].get(str(me.id), {})
            await q.edit_message_text(
                "📊 BOT STATUS\n\n"
                f"🤖 @{item.get('username') or 'unknown'}\n"
                f"🟢 Status: {item.get('status', 'ON')}\n"
                f"👥 Sudo users: {len(item.get('sudos', []))}",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ BACK", callback_data="gen_home")]]
                )
            )
            return

        if data == "gen_id":
            u = q.from_user
            await q.edit_message_text(
                f"🆔 User ID: {u.id}\n"
                f"👤 Username: {('@' + u.username) if u.username else (u.first_name or str(u.id))}",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("⬅️ BACK", callback_data="gen_home")]]
                )
            )
            return

        await q.answer("⚠️ Unknown button.", show_alert=True)
    except Exception as e:
        log.exception("Generated bot button error: %s", e)
        try:
            await q.answer("⚠️ Button error. Please try again.", show_alert=True)
        except Exception:
            pass

# ============================================================
# CRASH-PROOF RUNTIME LAYER WITH AUTO-RETRY & POLLING PROTECTION
# ============================================================

VOLT_CONCURRENT_UPDATES = max(1, min(64, int(os.getenv("VOLT_CONCURRENT_UPDATES", "16"))))
VOLT_CONNECTION_POOL = max(VOLT_CONCURRENT_UPDATES, min(128, int(os.getenv("VOLT_CONNECTION_POOL", "32"))))

def _volt_request():
    return HTTPXRequest(
        connection_pool_size=VOLT_CONNECTION_POOL,
        pool_timeout=30.0,
        connect_timeout=20.0,
        read_timeout=60.0,
        write_timeout=30.0,
    )

BOT_ME_CACHE = {}
BOT_ME_CACHE_TTL = 300.0
BOT_ME_CACHE_MAX_ENTRIES = 512
_BOT_ME_CACHE_LOCK = asyncio.Lock()

async def _cached_get_me(bot):
    key = id(bot)
    now = time.monotonic()
    cached = BOT_ME_CACHE.get(key)
    if cached and now - cached[1] < BOT_ME_CACHE_TTL:
        return cached[0]

    async with _BOT_ME_CACHE_LOCK:
        now = time.monotonic()
        cached = BOT_ME_CACHE.get(key)
        if cached and now - cached[1] < BOT_ME_CACHE_TTL:
            return cached[0]
        me = await bot.get_me()
        if len(BOT_ME_CACHE) >= BOT_ME_CACHE_MAX_ENTRIES:
            expired = [k for k, (_, stamp) in BOT_ME_CACHE.items() if now - stamp >= BOT_ME_CACHE_TTL]
            for old_key in expired:
                BOT_ME_CACHE.pop(old_key, None)
            if len(BOT_ME_CACHE) >= BOT_ME_CACHE_MAX_ENTRIES:
                oldest_key = min(BOT_ME_CACHE, key=lambda k: BOT_ME_CACHE[k][1])
                BOT_ME_CACHE.pop(oldest_key, None)
        BOT_ME_CACHE[key] = (me, now)
        return me

def build_generated_application(token):
    app = Application.builder().token(token).request(_volt_request()).concurrent_updates(VOLT_CONCURRENT_UPDATES).build()

    app.add_handler(CommandHandler("start", generated_start))
    app.add_handler(CommandHandler("help", generated_help))
    app.add_handler(CommandHandler("commands", generated_help))
    app.add_handler(CommandHandler("id", generated_id))
    app.add_handler(CommandHandler("owner", generated_owner))
    app.add_handler(CommandHandler("status", generated_status))
    app.add_handler(CommandHandler("ping", generated_ping))
    
    app.add_handler(CommandHandler("sayari", generated_sayari))
    app.add_handler(CommandHandler("hack", generated_hack))
    app.add_handler(CommandHandler("voice", generated_voice))
    
    app.add_handler(CommandHandler("raid", gen_raid_cmd))
    app.add_handler(CommandHandler("stretchraid", gen_stretchraid_cmd))
    app.add_handler(CommandHandler("matrixraid", gen_matrixraid_cmd))
    app.add_handler(CommandHandler("mathraid", gen_mathraid_cmd))
    app.add_handler(CommandHandler("animalraid", gen_animalraid_cmd))
    app.add_handler(CommandHandler("tharkibaazraid", gen_tharkibaazraid_cmd))
    app.add_handler(CommandHandler("emojiraid", gen_emojiraid_cmd))
    app.add_handler(CommandHandler("oneword", gen_oneword_cmd))
    app.add_handler(CommandHandler("spam", gen_spam_cmd))
    app.add_handler(CommandHandler("boom", gen_boom_cmd))
    app.add_handler(CommandHandler("nuke", gen_nuke_cmd))
    app.add_handler(CommandHandler("jooti", gen_jooti_cmd))
    app.add_handler(CommandHandler("gulam", gen_gulam_cmd))
    app.add_handler(CommandHandler("titan", gen_titan_cmd))
    app.add_handler(CommandHandler("omega", gen_omega_cmd))
    app.add_handler(CommandHandler("nemesis", gen_nemesis_cmd))
    app.add_handler(CommandHandler("reaper", gen_reaper_cmd))
    app.add_handler(CommandHandler("apocalypse", gen_apocalypse_cmd))
    app.add_handler(CommandHandler("phantom", gen_phantom_cmd))
    app.add_handler(CommandHandler("inferno", gen_inferno_cmd))
    app.add_handler(CommandHandler("quantum", gen_quantum_cmd))
    app.add_handler(CommandHandler("deathraid", gen_deathraid_cmd))
    app.add_handler(CommandHandler("storm", gen_storm_cmd))
    app.add_handler(CommandHandler("warcry", gen_warcry_cmd))
    app.add_handler(CommandHandler("chain", gen_chain_cmd))
    app.add_handler(CommandHandler("ghost", gen_ghost_cmd))
    
    app.add_handler(CommandHandler("gcnc", gen_gcnc_cmd))
    app.add_handler(CommandHandler("ncgc", gen_gcnc_cmd))
    app.add_handler(CommandHandler("ncemo", gen_ncemo_cmd))
    app.add_handler(CommandHandler("godspeed", gen_godspeed_cmd))
    
    app.add_handler(CommandHandler("picraid", gen_picraid_cmd))
    app.add_handler(CommandHandler("stop", gen_stop_cmd))

    app.add_handler(CommandHandler("addsudo", generated_addsudo))
    app.add_handler(CommandHandler("sudolist", generated_sudolist))
    app.add_handler(CommandHandler("delsudo", generated_delsudo))

    app.add_handler(CallbackQueryHandler(generated_callback))
    return app

async def run_generated_bot(bot_id, token):
    while True:
        item = DB["bots"].get(str(bot_id))
        if not item or item.get("status") != "ON":
            return
        app = build_generated_application(token)
        try:
            await app.initialize()
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
            DB["bots"][str(bot_id)]["runtime"] = "running"
            save_db()
            
            await asyncio.Event().wait()
        except (NetworkError, TimedOut, TelegramError) as net_err:
            log.warning("Generated bot %s network/gateway issue (%s). Reconnecting in 5s...", bot_id, net_err)
            await asyncio.sleep(5.0)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("Generated bot %s exception: %s. Reconnecting in 5s...", bot_id, e)
            await asyncio.sleep(5.0)
        finally:
            try:
                if app.updater and app.updater.running:
                    await app.updater.stop()
                if app.running:
                    await app.stop()
                await app.shutdown()
            except Exception:
                pass

async def token_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    message = update.effective_message

    if not DB["settings"].get("builder_enabled", True):
        target = message or (update.callback_query.message if update.callback_query else None)
        if target:
            await target.reply_text("🔴 Bot creation is currently OFF.")
        return ConversationHandler.END

    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass

    rec = user_record(uid, update.effective_user.username or "")
    if remaining_quota(uid) <= 0:
        rec["default_limit"] = max(DEFAULT_BOT_LIMIT, rec.get("default_limit", DEFAULT_BOT_LIMIT))
        save_db()

    prompt = (
        "🤖 Send your BotFather token now.\n\n"
        f"Remaining quota: {remaining_quota(uid)}\n\n"
        "⚠️ Send it only in this private chat."
    )
    if message:
        await message.reply_text(prompt)
    elif update.callback_query:
        await update.callback_query.message.reply_text(prompt)
    else:
        await context.bot.send_message(chat_id=update.effective_chat.id, text=prompt)
    return WAITING_TOKEN

async def token_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    token = (update.message.text or "").strip()
    
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", token):
        await update.message.reply_text("❌ Invalid BotFather token format. Please check and send a valid token.")
        return WAITING_TOKEN

    rec = user_record(uid, update.effective_user.username or "")
    if remaining_quota(uid) <= 0:
        rec["default_limit"] = max(DEFAULT_BOT_LIMIT, rec.get("default_limit", DEFAULT_BOT_LIMIT))
        save_db()

    msg = await update.message.reply_text("loading.... 1%")
    try:
        await asyncio.sleep(0.2)
        await msg.edit_text("loading.... 10%")
        test_bot = Bot(token=token)
        me = await _cached_get_me(test_bot)
        await asyncio.sleep(0.2)
        await msg.edit_text("loading.... 100%")
        await asyncio.sleep(0.2)
    except Exception as e:
        log.exception("Token validation exception.")
        await msg.edit_text(f"❌ Token validation failed: {e}\nPlease verify the token with BotFather.")
        return WAITING_TOKEN

    bot_id = str(me.id)
    if bot_id in DB["bots"]:
        await msg.edit_text("❌ This bot is already registered.")
        return ConversationHandler.END

    rec["created_bots"] += 1

    DB["bots"][bot_id] = {
        "bot_id": me.id,
        "username": me.username or "",
        "owner_id": uid,
        "status": "ON",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "token": token,
        "sudos": [],
        "runtime": "registered",
    }
    save_db()

    try:
        task = asyncio.create_task(run_generated_bot(bot_id, token))
        GENERATED_TASKS[bot_id] = task
        DB["bots"][bot_id]["runtime"] = "running"
        save_db()
    except Exception:
        DB["bots"][bot_id]["status"] = "OFF"
        DB["bots"][bot_id]["runtime"] = "failed_to_start"
        save_db()
        await msg.edit_text("❌ Bot was registered, but runtime failed to start.")
        return ConversationHandler.END

    await msg.edit_text(
        "✅ BOT CREATED & STARTED WITH FIXEDV71 ATTACK SUITE!\n\n"
        f"🤖 @{me.username or me.first_name}\n"
        f"🆔 Bot ID: {me.id}\n"
        "🟢 Status: ON\n\n"
        f"📊 Remaining quota: {remaining_quota(uid)}"
    )
    return ConversationHandler.END

async def cancel_token(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ Bot creation cancelled.")
    return ConversationHandler.END

async def addadmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in {OWNER_ID, COOWNER_ID}:
        await update.message.reply_text("❌ Owner/Co-owner only.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /addadmin <userid>")
        return
    uid = int(context.args[0])
    rec = user_record(uid)
    rec["role"] = "admin"
    rec["authorized"] = True
    save_db()
    await update.message.reply_text(f"✅ {uid} is now a Main Admin.")

async def deladmin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sender_id = update.effective_user.id
    if sender_id not in {OWNER_ID, COOWNER_ID} and role_of(sender_id) != "admin":
        await update.message.reply_text("❌ Insufficient permission. Owner/Co-owner only.")
        return
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.message.reply_text("Usage: /deladmin <userid>\nExample: /deladmin 7777957404")
        return
    target = int(args[0])
    
    if target in {OWNER_ID, COOWNER_ID}:
        await update.message.reply_text("⚠️ Cannot remove the protected Owner/Co-owner account.")
        return

    rec = DB["users"].get(str(target))
    if not rec or rec.get("role") != "admin":
        await update.message.reply_text("⚠️ USER IS NOT A MAIN ADMIN")
        return

    rec["role"] = "user"
    save_db()
    await update.message.reply_text(
        "✅ ADMIN REMOVED\n\n"
        f"🆔 User ID: {target}\n"
        "🛡️ Status: No longer Main Admin"
    )

async def freebot_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in {OWNER_ID, COOWNER_ID}:
        await update.message.reply_text("❌ Owner/Co-owner only.")
        return
    if len(context.args) != 2 or not all(x.isdigit() for x in context.args):
        await update.message.reply_text("Usage: /freebot <userid> <count>")
        return
    uid, count = map(int, context.args)
    rec = user_record(uid)
    rec["extra_quota"] += count
    rec["authorized"] = True
    save_db()
    await update.message.reply_text(f"✅ Added {count} extra quota to {uid}.")

async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_management(uid):
        await update.message.reply_text("❌ Management only command.")
        return

    broadcast_text = ""
    if update.message.reply_to_message:
        if update.message.reply_to_message.text:
            broadcast_text = update.message.reply_to_message.text
        elif update.message.reply_to_message.caption:
            broadcast_text = update.message.reply_to_message.caption

    if not broadcast_text and context.args:
        broadcast_text = " ".join(context.args)

    if not broadcast_text:
        await update.message.reply_text("Usage: /broadcast <message> (or reply to a message)")
        return

    users = DB.get("users", {})
    if not users:
        await update.message.reply_text("⚠️ No users found in database to broadcast.")
        return

    status_msg = await update.message.reply_text("📢 Broadcasting message to all users...")
    success = 0
    failed = 0

    for user_id_str in users.keys():
        try:
            target_id = int(user_id_str)
            await context.bot.send_message(chat_id=target_id, text=broadcast_text, parse_mode=ParseMode.MARKDOWN)
            success += 1
            await asyncio.sleep(0.05)
        except Exception:
            failed += 1

    try:
        await status_msg.edit_text(
            "📢 **BROADCAST COMPLETED**\n\n"
            f"✅ Successfully sent: {success}\n"
            f"❌ Failed / Blocked: {failed}",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        pass

async def panel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_management(update.effective_user.id):
        await update.message.reply_text("❌ Management only.")
        return
    await show_panel(update, context)

async def show_panel(update, context):
    users = DB["users"]
    bots = DB["bots"]
    auth_count = sum(1 for x in users.values() if x.get('authorized'))
    gen_bots_count = len(bots)
    on_count = sum(1 for b in bots.values() if b.get("status") == "ON")
    off_count = gen_bots_count - on_count

    text = (
        "⚡ VOLT MAIN ADMIN PANEL\n\n"
        f"👥 Authorized Users: {auth_count}\n"
        f"🤖 Generated Bots: {gen_bots_count}\n"
        f"🟢 Bots ON: {on_count}\n"
        f"🔴 Bots OFF: {off_count}"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🤖 BOT LIST (ON/OFF)", callback_data="bot_list"),
         InlineKeyboardButton("👥 USER LIST", callback_data="user_list")],
        [InlineKeyboardButton("🛡️ ADMIN MANAGEMENT", callback_data="admin_management")],
        [InlineKeyboardButton("🎁 REFERRALS", callback_data="admin_referrals")],
        [InlineKeyboardButton("🔄 REFRESH", callback_data="admin_panel")],
    ])
    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=kb)
        except Exception:
            await update.callback_query.message.reply_text(text, reply_markup=kb)
    else:
        await update.message.reply_text(text, reply_markup=kb)

async def addsudo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    owned = [b for b in DB["bots"].values() if b.get("owner_id") == uid]
    if not owned:
        await update.message.reply_text("❌ You do not own a generated bot.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /addsudo <userid>")
        return
    target = int(context.args[0])
    if target <= 0:
        await update.message.reply_text("❌ Invalid user ID.")
        return

    added_count = 0
    for b in owned:
        sudos = b.setdefault("sudos", [])
        if target not in sudos and target != b.get("owner_id"):
            sudos.append(target)
            added_count += 1
    save_db()

    if added_count > 0:
        await update.message.reply_text(
            f"✅ **SUCCESSFULLY ADDED SUDO**\n\n"
            f"🆔 User ID: `{target}`\n"
            "🛡️ Added directly to your generated bot(s) without approval!",
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.message.reply_text("ℹ️ User is already a sudo or invalid target.")

async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data or ""

    if data == "check_join":
        is_joined = await check_user_joined(context.bot, uid)
        if not is_joined:
            await q.answer("❌ You have not joined all required Channels and Groups yet!", show_alert=True)
            return
        await q.answer("✅ Verified successfully!", show_alert=True)
        text = (
            "⚡ VOLT BOT BUILDER ⚡\n\n"
            "╭────────────────────────╮\n"
            "   🤖 BOT CREATION SYSTEM\n"
            "   Made by VOLT Community ⚡\n"
            "╰────────────────────────╯\n\n"
            f"👑 Owner: {OWNER_USERNAME}\n"
            f"👑 Co-Owner: @ix_aura\n\n"
            "✅ Verified & Authorized."
        )
        await q.edit_message_text(text, reply_markup=builder_keyboard(uid))
        return

    if data == "builder_home":
        await q.edit_message_text(
            "⚡ VOLT BOT BUILDER ⚡\n\n"
            "╭────────────────────────╮\n"
            "   🤖 BOT CREATION SYSTEM\n"
            "   Made by VOLT Community ⚡\n"
            "╰────────────────────────╯\n\n"
            f"👑 Owner: {OWNER_USERNAME}\n"
            f"👑 Co-Owner: @ix_aura\n\n"
            "✅ Verified & Authorized.",
            reply_markup=builder_keyboard(uid)
        )
        return

    if data == "owner_info":
        await q.edit_message_text(
            f"👑 MAIN OWNER\n\n🆔 {OWNER_ID}\n👤 {OWNER_USERNAME}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ BACK", callback_data="builder_home")]])
        )
        return

    if data == "coowner_info":
        await q.edit_message_text(
            f"👑 CO-OWNER\n\n🆔 {COOWNER_ID}\n👤 @ix_aura",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ BACK", callback_data="builder_home")]])
        )
        return

    if data == "my_quota":
        rec = user_record(uid)
        rem = remaining_quota(uid)
        await q.edit_message_text(
            "📊 **YOUR QUOTA & STATS**\n\n"
            f"🤖 Created Bots: {rec.get('created_bots', 0)}\n"
            f"📦 Default Limit: {rec.get('default_limit', DEFAULT_BOT_LIMIT)}\n"
            f"🎁 Extra Quota: {rec.get('extra_quota', 0)}\n"
            f"✨ Remaining Quota: {rem}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ BACK", callback_data="builder_home")]]),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "referral_menu":
        rec = user_record(uid)
        bot_uname = MAIN_BOT_USERNAME
        ref_link = f"https://t.me/{bot_uname}?start=ref_{uid}"
        ref_count = len(rec.get("referred_users", []))
        claimed = rec.get("referral_rewards_claimed", 0)
        await q.edit_message_text(
            "🎁 **REFERRAL SYSTEM**\n\n"
            "Invite your friends and earn +1 Free Bot Credit for every 2 successful referrals!\n\n"
            f"🔗 **Your Referral Link:**\n`{ref_link}`\n\n"
            f"👥 Total Referred Users: {ref_count}\n"
            f"🎁 Rewards Claimed: {claimed} Bot Credit(s)",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ BACK", callback_data="builder_home")]]),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "admin_panel":
        if not is_management(uid):
            await q.answer("❌ Management only.", show_alert=True)
            return
        await show_panel(update, context)
        return

    if data == "bot_list" or data.startswith("bot_list:"):
        if not is_management(uid):
            await q.answer("❌ Management only.", show_alert=True)
            return

        bots = DB.get("bots", {})
        if not bots:
            await q.edit_message_text(
                "🤖 BOT LIST\n\nNo generated bots found.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("⬅️ BACK", callback_data="admin_panel")]
                ])
            )
            return

        page = 0
        if ":" in data:
            try:
                page = max(0, int(data.split(":", 1)[1]))
            except (TypeError, ValueError):
                page = 0

        page_size = 10
        bot_items = list(bots.items())
        total_pages = max(1, (len(bot_items) + page_size - 1) // page_size)
        page = min(page, total_pages - 1)
        page_items = bot_items[page * page_size:(page + 1) * page_size]

        kb_rows = []
        for bid, bdata in page_items:
            uname = str(bdata.get("username") or bid).lstrip("@")
            status = bdata.get("status", "OFF")
            status_icon = "🟢" if status == "ON" else "🔴"
            toggle_action = "off_bot" if status == "ON" else "on_bot"
            toggle_label = "🔴 Turn OFF" if status == "ON" else "🟢 Turn ON"
            kb_rows.append([
                InlineKeyboardButton(f"{status_icon} @{uname}", callback_data=f"noop_{bid}"),
                InlineKeyboardButton(toggle_label, callback_data=f"{toggle_action}:{bid}")
            ])

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ PREV", callback_data=f"bot_list:{page - 1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("NEXT ➡️", callback_data=f"bot_list:{page + 1}"))
        if nav:
            kb_rows.append(nav)
        kb_rows.append([InlineKeyboardButton("🔄 REFRESH", callback_data=f"bot_list:{page}")])
        kb_rows.append([InlineKeyboardButton("⬅️ BACK", callback_data="admin_panel")])

        try:
            await q.edit_message_text(
                "🤖 ADMIN PANEL — GENERATED BOT LIST\n\n"
                f"Page {page + 1}/{total_pages}\n"
                "Owner, Co-owner & Admins can toggle bots ON/OFF:",
                reply_markup=InlineKeyboardMarkup(kb_rows)
            )
        except TelegramError:
            try:
                await q.message.reply_text(
                    "🤖 ADMIN PANEL — GENERATED BOT LIST",
                    reply_markup=InlineKeyboardMarkup(kb_rows)
                )
            except Exception:
                pass
        return

    if data.startswith("noop_"):
        return

    if data.startswith("on_bot:") or data.startswith("off_bot:"):
        if not is_management(uid):
            await q.answer("❌ Management only.", show_alert=True)
            return
        parts = data.split(":", 1)
        if len(parts) != 2:
            await q.answer("❌ Invalid bot action.", show_alert=True)
            return
        action, bid = parts
        bid = str(bid)
        bdata = DB["bots"].get(bid)
        if not bdata:
            await q.answer("❌ Bot not found.", show_alert=True)
            return

        if action == "on_bot":
            bdata["status"] = "ON"
            bdata["runtime"] = "starting"
            save_db()
            task = GENERATED_TASKS.get(bid)
            if task is None or task.done():
                try:
                    GENERATED_TASKS[bid] = asyncio.create_task(
                        run_generated_bot(bid, bdata["token"])
                    )
                except Exception as e:
                    bdata["status"] = "OFF"
                    bdata["runtime"] = "start_failed"
                    save_db()
                    log.exception("Failed to start bot %s", bid)
                    await q.answer("❌ Could not start bot.", show_alert=True)
                    return
            await q.answer("🟢 Bot turned ON.", show_alert=True)
        else:
            bdata["status"] = "OFF"
            bdata["runtime"] = "stopping"
            save_db()
            task = GENERATED_TASKS.pop(bid, None)
            if task is not None:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        log.exception("Error stopping bot %s", bid)
            bdata["runtime"] = "stopped"
            save_db()
            await q.answer("🔴 Bot turned OFF.", show_alert=True)

        kb_rows = []
        for list_bid, list_bdata in list(DB["bots"].items())[:10]:
            list_uname = list_bdata.get("username") or str(list_bid)
            list_status = list_bdata.get("status", "OFF")
            icon = "🟢" if list_status == "ON" else "🔴"
            toggle_action = "off_bot" if list_status == "ON" else "on_bot"
            toggle_label = "🔴 Turn OFF" if list_status == "ON" else "🟢 Turn ON"
            kb_rows.append([
                InlineKeyboardButton(f"{icon} @{list_uname}", callback_data=f"noop_{list_bid}"),
                InlineKeyboardButton(toggle_label, callback_data=f"{toggle_action}:{list_bid}")
            ])
        kb_rows.append([InlineKeyboardButton("🔄 REFRESH", callback_data="bot_list:0")])
        kb_rows.append([InlineKeyboardButton("⬅️ BACK", callback_data="admin_panel")])
        try:
            await q.edit_message_text(
                "🤖 ADMIN PANEL — GENERATED BOT LIST\n\n"
                "Owner, Co-owner & Admins can toggle bots ON/OFF:",
                reply_markup=InlineKeyboardMarkup(kb_rows)
            )
        except Exception:
            try:
                await q.message.reply_text(
                    "🤖 ADMIN PANEL — GENERATED BOT LIST",
                    reply_markup=InlineKeyboardMarkup(kb_rows)
                )
            except Exception:
                pass
        return

    if data == "user_list" or data.startswith("user_list:"):
        if not is_management(uid):
            await q.answer("❌ Management only.", show_alert=True)
            return

        users = DB.get("users", {})
        page = 0
        if ":" in data:
            try:
                page = max(0, int(data.split(":", 1)[1]))
            except (TypeError, ValueError):
                page = 0

        page_size = 15
        user_items = list(users.items())
        total_pages = max(1, (len(user_items) + page_size - 1) // page_size)
        page = min(page, total_pages - 1)
        start_i = page * page_size
        page_items = user_items[start_i:start_i + page_size]

        lines = [
            f"👥 REGISTERED USERS ({len(user_items)})",
            f"📄 Page {page + 1}/{total_pages}",
            ""
        ]
        for u_id, u_info in page_items:
            username = str(u_info.get("username") or "None").lstrip("@")
            role = str(u_info.get("role") or "user")
            status = "✅" if u_info.get("authorized", True) else "❌"
            lines.append(f"• {u_id} | @{username} | {role} | {status}")

        if not page_items:
            lines.append("No registered users found.")

        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton("⬅️ PREV", callback_data=f"user_list:{page - 1}"))
        if page < total_pages - 1:
            nav.append(InlineKeyboardButton("NEXT ➡️", callback_data=f"user_list:{page + 1}"))

        rows = []
        if nav:
            rows.append(nav)
        rows.append([InlineKeyboardButton("🔄 REFRESH", callback_data=f"user_list:{page}")])
        rows.append([InlineKeyboardButton("⬅️ BACK", callback_data="admin_panel")])

        try:
            await q.edit_message_text(
                "\n".join(lines),
                reply_markup=InlineKeyboardMarkup(rows)
            )
        except TelegramError:
            try:
                await q.message.reply_text(
                    "\n".join(lines),
                    reply_markup=InlineKeyboardMarkup(rows)
                )
            except Exception:
                pass
        return

    if data == "admin_management":
        if not is_management(uid):
            await q.answer("❌ Management only.", show_alert=True)
            return
        await q.edit_message_text(
            "🛡️ **ADMIN MANAGEMENT**\n\n"
            "Commands available:\n"
            "• `/addadmin <userid>` — Add Main Admin\n"
            "• `/deladmin <userid>` — Remove Main Admin\n"
            "• `/freebot <userid> <count>` — Add free quota\n"
            "• `/broadcast <msg>` — Broadcast message",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ BACK", callback_data="admin_panel")]]),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    if data == "admin_referrals":
        if not is_management(uid):
            await q.answer("❌ Management only.", show_alert=True)
            return
        total_refs = sum(len(u.get("referred_users", [])) for u in DB["users"].values())
        await q.edit_message_text(
            "🎁 **ADMIN REFERRAL STATS**\n\n"
            f"👥 Total Successful Referrals across platform: {total_refs}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ BACK", callback_data="admin_panel")]]),
            parse_mode=ParseMode.MARKDOWN
        )
        return

def main():
    if not MAIN_BOT_TOKEN:
        raise RuntimeError("MAIN_BOT_TOKEN is missing.")

    app = Application.builder().token(MAIN_BOT_TOKEN).request(_volt_request()).build()

    conv_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(token_start, pattern=r"^create_bot$"),
            CommandHandler("token", token_start),
        ],
        states={
            WAITING_TOKEN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, token_receive),
            ]
        },
        fallbacks=[
            CommandHandler("cancel", cancel_token),
            CallbackQueryHandler(token_start, pattern=r"^create_bot$"),
        ],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("id", id_cmd))
    app.add_handler(CommandHandler("panel", panel_cmd))
    app.add_handler(CommandHandler("addadmin", addadmin_cmd))
    app.add_handler(CommandHandler("deladmin", deladmin_cmd))
    app.add_handler(CommandHandler("freebot", freebot_cmd))
    app.add_handler(CommandHandler("broadcast", broadcast_cmd))
    app.add_handler(CommandHandler("addsudo", addsudo_cmd))
    app.add_handler(conv_handler)
    app.add_handler(CallbackQueryHandler(callback))

    log.info("VOLT Bot Builder is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
