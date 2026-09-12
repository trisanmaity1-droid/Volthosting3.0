#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VOLT ⚡ HOSTING - Professional Telegram Hosting Platform
Version: V12.09.000
Powered by VOLT ⚡ STUDIO
© 2026 VOLT ⚡ STUDIO — All Rights Reserved.

Production‑ready single‑file implementation.
"""
import os
import sys
import json
import sqlite3
import threading
import subprocess
import time
import datetime
import random
import string
import secrets
import shutil
import signal
import logging
import traceback
import re
import shlex
import io
import html
import tempfile
import resource
import errno
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from functools import wraps

import telebot
from telebot import types
import qrcode
from PIL import Image, ImageDraw

# ==========================
#  CONFIGURATION (RAILWAY ENVIRONMENT VARIABLES)
# ==========================
# SECURITY NOTE:
# Set these in the runtime environment:
#   BOT_TOKEN      = VOLT ⚡ HOSTING token
#   PAY_BOT_TOKEN  = VOLT ⚡ PAYMENT DATABASE token
#   DB_BOT_TOKEN   = VOLT ⚡ DATABASE token
# Do NOT hardcode Telegram bot tokens into this source file.

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
DB_BOT_TOKEN = os.environ.get("DB_BOT_TOKEN", "").strip()
PAY_BOT_TOKEN = os.environ.get("PAY_BOT_TOKEN", "").strip()
def _env_int(name, default=0):
    """Read an integer setting from Railway/environment safely."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        logger.warning("Invalid integer environment variable %s; using %s", name, default)
        return default

OWNER_ID = _env_int("OWNER_ID")
CO_OWNER_ID = _env_int("CO_OWNER_ID")
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "").strip().lstrip("@")
CO_OWNER_USERNAME = os.environ.get("CO_OWNER_USERNAME", "").strip().lstrip("@")
UPI_ID = os.environ.get("UPI_ID", "abhirajkathole60@okicici").strip()
UPI_LOGO = ""  # optional path to logo

# Branding
BRAND = "VOLT ⚡ HOSTING"
BRAND_VER = "V12.09.000"
STUDIO = "VOLT ⚡ STUDIO"
FOOTER = f"\n© 2026 {STUDIO}\nAll Rights Reserved."

# Database
DB_PATH = os.environ.get("DATABASE_URL", "volthosting.db")
if DB_PATH.startswith("sqlite:///"):
    DB_PATH = DB_PATH.replace("sqlite:///", "")
try:
    _db_parent = os.path.dirname(os.path.abspath(DB_PATH))
    if _db_parent:
        os.makedirs(_db_parent, exist_ok=True)
except Exception:
    pass

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("VOLT")

# Limits
MAX_FILE_SIZE = None  # No application-level upload size limit
ALLOWED_EXTENSIONS = {'.py', '.js', '.java', '.cpp', '.c', '.sh', '.txt', '.json', '.html', '.css', '.zip'}
RATE_LIMITS = {
    'upload': (3, 60),        # 3 per minute
    'deploy': (2, 60),
    'payment': (3, 300),
    'ticket': (2, 300),
    'callback': (30, 60),
}
SANDBOX_ROOT = Path("./sandbox").resolve()
HOSTING_MAX_CPU_SECONDS = int(os.environ.get("HOSTING_MAX_CPU_SECONDS", "300"))
HOSTING_MAX_MEMORY_MB = int(os.environ.get("HOSTING_MAX_MEMORY_MB", "512"))
HOSTING_MAX_PROCESSES = int(os.environ.get("HOSTING_MAX_PROCESSES", "64"))
HOSTING_MAX_OPEN_FILES = int(os.environ.get("HOSTING_MAX_OPEN_FILES", "128"))
HOSTING_MAX_OUTPUT_MB = None  # No application-level output file size limit
HOSTING_MAX_ZIP_FILES = int(os.environ.get("HOSTING_MAX_ZIP_FILES", "2000"))
HOSTING_MAX_ZIP_UNCOMPRESSED_MB = None  # No application-level ZIP expansion limit
HOSTING_MAX_COMMAND_ARGS = 32

# Defense-in-depth: hosted processes must never inherit these host secrets.
HOST_SECRET_ENV_KEYS = {
    "BOT_TOKEN", "DB_BOT_TOKEN", "PAY_BOT_TOKEN", "UPI_ID",
    "OWNER_ID", "CO_OWNER_ID", "DATABASE_URL", "SECRET_KEY",
    "API_KEY", "TOKEN"
}

# ==========================
#  DATABASE HELPERS
# ==========================
_DB_WRITE_LOCK = threading.RLock()
_DB_RETRY_DELAYS = (0.02, 0.05, 0.1, 0.2, 0.4, 0.8, 1.2)

class _RetryingCursor(sqlite3.Cursor):
    _WRITE_SQL = ("INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "DROP", "ALTER", "PRAGMA")

    def _is_write(self, sql):
        return str(sql).lstrip().upper().startswith(self._WRITE_SQL)

    def execute(self, sql, parameters=()):
        if self._is_write(sql):
            self.connection._acquire_write_lock()
        last_exc = None
        for delay in (0.0,) + _DB_RETRY_DELAYS:
            try:
                return super().execute(sql, parameters)
            except sqlite3.OperationalError as exc:
                last_exc = exc
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                if delay:
                    time.sleep(delay)
        raise last_exc

    def executemany(self, sql, parameters):
        if self._is_write(sql):
            self.connection._acquire_write_lock()
        last_exc = None
        for delay in (0.0,) + _DB_RETRY_DELAYS:
            try:
                return super().executemany(sql, parameters)
            except sqlite3.OperationalError as exc:
                last_exc = exc
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    raise
                if delay:
                    time.sleep(delay)
        raise last_exc

class _RetryingConnection(sqlite3.Connection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._write_lock_held = False

    def _acquire_write_lock(self):
        if not self._write_lock_held:
            _DB_WRITE_LOCK.acquire()
            self._write_lock_held = True

    def _release_write_lock(self):
        if self._write_lock_held:
            self._write_lock_held = False
            _DB_WRITE_LOCK.release()

    def cursor(self, factory=None):
        return super().cursor(factory or _RetryingCursor)

    def commit(self):
        last_exc = None
        for delay in (0.0,) + _DB_RETRY_DELAYS:
            try:
                result = super().commit()
                self._release_write_lock()
                return result
            except sqlite3.OperationalError as exc:
                last_exc = exc
                if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                    self._release_write_lock()
                    raise
                if delay:
                    time.sleep(delay)
        self._release_write_lock()
        raise last_exc

    def rollback(self):
        try:
            return super().rollback()
        finally:
            self._release_write_lock()

    def close(self):
        try:
            return super().close()
        finally:
            self._release_write_lock()

def get_db():
    # Fast, resilient SQLite connection. WAL is configured once during init_db(),
    # not on every request (PRAGMA journal_mode=WAL itself can briefly lock SQLite).
    conn = sqlite3.connect(
        DB_PATH, timeout=8, check_same_thread=False,
        isolation_level="DEFERRED", factory=_RetryingConnection
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 8000")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -32000")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    # Configure WAL once at startup; never toggle journal mode per request.
    c.execute("PRAGMA journal_mode = WAL")
    c.execute("PRAGMA synchronous = NORMAL")
    c.executescript('''
        PRAGMA foreign_keys = ON;
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_banned INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            name TEXT,
            path TEXT,
            size INTEGER,
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS plans (
            id TEXT PRIMARY KEY,
            name TEXT,
            price INTEGER,
            duration_days INTEGER
        );
        CREATE TABLE IF NOT EXISTS deployments (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            file_id INTEGER,
            runtime TEXT,
            start_command TEXT,
            port INTEGER,
            status TEXT DEFAULT 'PENDING_APPROVAL',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved_by INTEGER,
            approved_at TIMESTAMP,
            rejected_reason TEXT,
            started_at TIMESTAMP,
            stopped_at TIMESTAMP,
            restart_count INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (file_id) REFERENCES files(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS hosting (
            id TEXT PRIMARY KEY,
            deployment_id TEXT,
            user_id INTEGER,
            process_id INTEGER,
            status TEXT DEFAULT 'ONLINE',
            started_at TIMESTAMP,
            stopped_at TIMESTAMP,
            restart_count INTEGER DEFAULT 0,
            FOREIGN KEY (deployment_id) REFERENCES deployments(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS payments (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            plan_id TEXT,
            amount INTEGER,
            utr TEXT,
            proof_file_id TEXT,
            status TEXT DEFAULT 'PENDING',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved_by INTEGER,
            approved_at TIMESTAMP,
            rejected_reason TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS receipts (
            id TEXT PRIMARY KEY,
            payment_id TEXT,
            customer_name TEXT,
            plan_name TEXT,
            amount INTEGER,
            utr TEXT,
            purchase_date TIMESTAMP,
            activation_date TIMESTAMP,
            expiry_date TIMESTAMP,
            status TEXT,
            approved_by INTEGER,
            FOREIGN KEY (payment_id) REFERENCES payments(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS tickets (
            id TEXT PRIMARY KEY,
            user_id INTEGER,
            subject TEXT,
            message TEXT,
            status TEXT DEFAULT 'OPEN',
            priority TEXT DEFAULT 'NORMAL',
            assigned_admin INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP,
            closed_at TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS ticket_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticket_id TEXT,
            sender_id INTEGER,
            sender_type TEXT,
            text TEXT,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (ticket_id) REFERENCES tickets(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            admin_name TEXT,
            action TEXT,
            details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS user_subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            plan_id TEXT,
            start_date TIMESTAMP,
            expiry_date TIMESTAMP,
            status TEXT DEFAULT 'ACTIVE',
            payment_id TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (plan_id) REFERENCES plans(id),
            FOREIGN KEY (payment_id) REFERENCES payments(id)
        );
        CREATE INDEX IF NOT EXISTS idx_user_subscriptions_user_id ON user_subscriptions(user_id);
        CREATE INDEX IF NOT EXISTS idx_user_subscriptions_expiry ON user_subscriptions(expiry_date);
        CREATE INDEX IF NOT EXISTS idx_deployments_user_id ON deployments(user_id);
        CREATE INDEX IF NOT EXISTS idx_hosting_user_id ON hosting(user_id);
        CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id);
        CREATE INDEX IF NOT EXISTS idx_payments_utr ON payments(utr);
        CREATE INDEX IF NOT EXISTS idx_payments_status ON payments(status);
        CREATE INDEX IF NOT EXISTS idx_files_user_id ON files(user_id);
        CREATE INDEX IF NOT EXISTS idx_hosting_status ON hosting(status);
        CREATE INDEX IF NOT EXISTS idx_deployments_status ON deployments(status);
        CREATE INDEX IF NOT EXISTS idx_tickets_user_status ON tickets(user_id, status);
        CREATE INDEX IF NOT EXISTS idx_ticket_messages_ticket_id ON ticket_messages(ticket_id);
        CREATE INDEX IF NOT EXISTS idx_audit_logs_admin_id ON audit_logs(admin_id);
        CREATE INDEX IF NOT EXISTS idx_receipts_payment_id ON receipts(payment_id);
        CREATE INDEX IF NOT EXISTS idx_receipts_utr ON receipts(utr);
    ''')

    # Insert/update plans (exact list)
    plans = [
        ("7_days", "7 DAYS", 25, 7),
        ("2_weeks", "2 WEEKS", 50, 14),
        ("1_month", "1 MONTH", 99, 30),
        ("2_months", "2 MONTHS", 198, 60),
        ("1_year", "1 YEAR", 1299, 365),
        ("2_years", "2 YEARS", 2598, 730),
    ]
    for plan in plans:
        c.execute("INSERT OR REPLACE INTO plans (id, name, price, duration_days) VALUES (?,?,?,?)", plan)
    conn.commit()
    conn.close()
    logger.info("Database initialized.")

# ==========================
#  UTILITY FUNCTIONS
# ==========================
def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)

def format_ist(ts):
    if ts is None:
        return "—"
    if isinstance(ts, str):
        ts = datetime.datetime.fromisoformat(ts.replace('Z', '+00:00'))
    # Convert to IST (UTC+5:30)
    ist = ts.astimezone(datetime.timezone(datetime.timedelta(hours=5, minutes=30)))
    return ist.strftime("%Y-%m-%d %I:%M %p IST")

def fmt_ts(ts):
    return format_ist(ts)

def generate_id(prefix):
    return f"{prefix}-{secrets.token_hex(4).upper()}"

def safe_filename(filename: str) -> str:
    """Return a single safe basename; reject hidden/path-like names."""
    raw = os.path.basename(str(filename or ""))
    name = re.sub(r"[^a-zA-Z0-9_.-]", "_", raw)
    name = name.lstrip(".")
    if not name or name in {".", ".."}:
        raise ValueError("Invalid filename")
    if len(name) > 180:
        stem, ext = os.path.splitext(name)
        name = stem[:160] + ext[:20]
    return name

def safe_path(root: Path, filename: str) -> Path:
    """Resolve a path and guarantee it stays inside root."""
    root = root.resolve()
    final = (root / filename).resolve()
    if final == root or root not in final.parents:
        raise ValueError("Path traversal detected")
    return final

def user_file_path(user_id: int, filename: str) -> Path:
    return safe_path(SANDBOX_ROOT / str(int(user_id)) / "files", safe_filename(filename))

def _validate_user_file_access(user_id: int, file_id: int):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT id, user_id, name, path, size FROM files WHERE id=? AND user_id=?",
            (int(file_id), int(user_id))
        ).fetchone()
        if not row:
            return None
        try:
            path = Path(row["path"]).resolve()
            expected_root = (SANDBOX_ROOT / str(int(user_id)) / "files").resolve()
            if expected_root not in path.parents:
                logger.warning("Blocked DB path escape for user=%s file=%s", user_id, file_id)
                return None
        except Exception:
            return None
        return row
    finally:
        conn.close()

def _sanitize_host_env():
    env = {
        "PATH": os.environ.get("HOSTING_PATH", "/usr/local/bin:/usr/bin:/bin"),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(SANDBOX_ROOT),
    }
    # Explicitly allow only harmless runtime configuration.
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
        if key in os.environ:
            env[key] = os.environ[key]
    return env

def _apply_process_sandbox():
    """Best-effort OS resource isolation for POSIX hosted processes."""
    if os.name != "posix":
        return
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (HOSTING_MAX_CPU_SECONDS, HOSTING_MAX_CPU_SECONDS))
    except Exception:
        pass
    try:
        mem = HOSTING_MAX_MEMORY_MB * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NPROC, (HOSTING_MAX_PROCESSES, HOSTING_MAX_PROCESSES))
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (HOSTING_MAX_OPEN_FILES, HOSTING_MAX_OPEN_FILES))
    except Exception:
        pass
    try:
        if HOSTING_MAX_OUTPUT_MB is not None:
            max_bytes = HOSTING_MAX_OUTPUT_MB * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_FSIZE, (max_bytes, max_bytes))
    except Exception:
        pass
    try:
        os.umask(0o077)
    except Exception:
        pass
    # Linux: prevent gaining new privileges where supported.
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        PR_SET_NO_NEW_PRIVS = 38
        if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            logger.debug("PR_SET_NO_NEW_PRIVS unavailable: errno=%s", ctypes.get_errno())
    except Exception:
        pass

def _validate_command(parts):
    if not parts or len(parts) > HOSTING_MAX_COMMAND_ARGS:
        raise ValueError("Invalid command")
    for arg in parts:
        if len(arg) > 512 or "\x00" in arg:
            raise ValueError("Invalid command argument")
    # Never permit shell-control tokens or command wrappers.
    forbidden = {"sudo", "su", "doas", "pkexec", "systemctl", "service", "mount",
                 "umount", "nsenter", "unshare", "chroot", "docker", "podman"}
    if any(Path(x).name in forbidden for x in parts):
        raise ValueError("Privileged/system command is not allowed")

_LAST_ACTIVE_CACHE = {}
_LAST_ACTIVE_LOCK = threading.Lock()
_LAST_ACTIVE_TTL = 60.0

def create_user(user: types.User):
    # Single atomic upsert: avoids SELECT-then-INSERT races and one extra DB round trip.
    with _DB_WRITE_LOCK:
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO users (id, username, first_name, last_name) VALUES (?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name, last_name=excluded.last_name",
                (user.id, user.username, user.first_name, user.last_name)
            )
            conn.commit()
        finally:
            conn.close()

def update_last_active(user_id):
    # Do not write SQLite on every Telegram update. This removes a major source of
    # contention/latency while keeping activity timestamps reasonably fresh.
    now = time.monotonic()
    uid = int(user_id)
    with _LAST_ACTIVE_LOCK:
        previous = _LAST_ACTIVE_CACHE.get(uid, 0.0)
        if now - previous < _LAST_ACTIVE_TTL:
            return
        _LAST_ACTIVE_CACHE[uid] = now
    with _DB_WRITE_LOCK:
        conn = get_db()
        try:
            conn.execute("UPDATE users SET last_active=? WHERE id=?", (now_utc(), uid))
            conn.commit()
        finally:
            conn.close()

def is_admin(user_id):
    return user_id in (OWNER_ID, CO_OWNER_ID)

def is_banned(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT is_banned FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    return bool(row and row["is_banned"])

def get_username_display(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT username, first_name FROM users WHERE id=?", (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        if row["username"]:
            return f"@{row['username']}"
        else:
            return row["first_name"] or "Not Set"
    return "Not Set"

def log_audit(admin_id, admin_name, action, details=""):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO audit_logs (admin_id, admin_name, action, details) VALUES (?,?,?,?)",
        (admin_id, admin_name, action, details)
    )
    conn.commit()
    conn.close()

def get_user_plan(user_id):
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT p.name, s.start_date, s.expiry_date, s.status, s.plan_id "
        "FROM user_subscriptions s "
        "JOIN plans p ON s.plan_id = p.id "
        "WHERE s.user_id = ? AND s.status = 'ACTIVE' AND s.expiry_date > ? "
        "ORDER BY s.expiry_date DESC LIMIT 1",
        (user_id, now_utc())
    )
    row = c.fetchone()
    conn.close()
    return row

# ==========================
#  RATE LIMITING
# ==========================
rate_limit_store = {}

def rate_limit(action: str, user_id: int) -> bool:
    """Returns True if rate limit exceeded."""
    if action not in RATE_LIMITS:
        return False
    limit, period = RATE_LIMITS[action]
    key = f"{action}:{user_id}"
    now = time.time()
    if key not in rate_limit_store:
        rate_limit_store[key] = []
    # Clean old entries
    rate_limit_store[key] = [t for t in rate_limit_store[key] if now - t < period]
    if len(rate_limit_store[key]) >= limit:
        return True
    rate_limit_store[key].append(now)
    return False

def rate_limit_decorator(action: str):
    def decorator(func):
        @wraps(func)
        def wrapper(message, *args, **kwargs):
            user_id = message.from_user.id
            if rate_limit(action, user_id):
                message.reply_text("⏳ Too many requests. Please slow down.")
                return
            return func(message, *args, **kwargs)
        return wrapper
    return decorator

# ==========================
#  KEYBOARDS
# ==========================
def main_menu_kb(user_id=None):
    """Screenshot-1 options rendered in screenshot-2 style: a compact 2-column reply keyboard."""
    kb = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2,
        selective=False,
        input_field_placeholder="Choose an option…",
    )
    rows = [
        ("🚀 MY HOSTING", "📁 MY FILES"),
        ("🚀 DEPLOY", "💳 BUY HOSTING"),
        ("🧾 MY RECEIPTS", "📊 STATISTICS"),
        ("🎫 SUPPORT", "👤 MY ACCOUNT"),
        ("ℹ️ ABOUT VOLT",),
    ]
    # Owner/Co-Owner only: expose Admin Panel in the reply keyboard.
    # Authorization remains ID-based and uses Railway Variables above.
    # `current_user_id` is attached by the start/menu handlers when available.
    current_user_id = user_id or 0
    if current_user_id and is_admin(current_user_id):
        rows.append(("🛠️ ADMIN PANEL",))
    for row in rows:
        kb.row(*(types.KeyboardButton(label) for label in row))
    return kb

# Alias kept for existing code that already uses the screenshot-style menu.
def user_reply_menu_kb(user_id=None):
    return main_menu_kb(user_id)

def back_main_kb():
    return types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main")
    )

# ==========================
#  USER REPLY MENU + BOT SPEED
# ==========================
def files_inline_kb():
    """Inline controls shown after MY SCRIPTS."""
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📤 UPLOAD FILE", callback_data="text_upload"))
    kb.add(types.InlineKeyboardButton("🚀 MY HOSTING", callback_data="menu_hosting"))
    kb.add(types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main"))
    return kb

def upload_prompt_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📁 MY SCRIPTS", callback_data="menu_files"))
    kb.add(types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main"))
    return kb

def show_my_files_message(message):
    """Render the files page when the user presses the reply-keyboard button."""
    user = message.from_user
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM files WHERE user_id=? ORDER BY uploaded_at DESC", (user.id,))
    files = c.fetchall()
    conn.close()

    if not files:
        text = (
            "📁 <b>MY SCRIPTS</b>\n\n"
            "No scripts uploaded yet.\n\n"
            "Tap <b>📤 UPLOAD FILE</b> and send your project file."
        )
        main_bot.send_message(message.chat.id, text, reply_markup=files_inline_kb())
        return

    text = "📁 <b>MY SCRIPTS</b>\n\n"
    kb = types.InlineKeyboardMarkup(row_width=1)
    for f in files:
        size = f["size"] or 0
        text += f"📦 <b>{f['name']}</b> — {size:,} bytes\n"
        kb.add(
            types.InlineKeyboardButton(
                f"🚀 DEPLOY • {f['name'][:24]}",
                callback_data=f"deploy_start_{f['id']}"
            )
        )
        kb.add(
            types.InlineKeyboardButton(
                f"🗑️ DELETE • {f['name'][:24]}",
                callback_data=f"file_delete_{f['id']}"
            )
        )
    kb.add(types.InlineKeyboardButton("📤 UPLOAD FILE", callback_data="text_upload"))
    kb.add(types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main"))
    main_bot.send_message(message.chat.id, text, reply_markup=kb)

def show_bot_speed(message):
    """Measure a real Telegram Bot API round-trip."""
    try:
        started = time.perf_counter()
        me = main_bot.get_me()
        elapsed_ms = max(0.1, (time.perf_counter() - started) * 1000.0)
        latency = int(round(elapsed_ms))

        if latency <= 100:
            health = "🟢 Healthy"
        elif latency <= 300:
            health = "🟡 Stable"
        else:
            health = "🔴 Slow"

        text = (
            "⚡ <b>VOLT BOT SPEED</b>\n\n"
            f"{health}\n"
            f"📡 Telegram API latency: <b>{latency} ms</b>\n"
            f"🤖 Bot: @{me.username or 'unknown'}\n\n"
            "🚀 Bot is responding normally."
        )
    except Exception as exc:
        logger.error("Bot speed check failed: %s", exc)
        text = (
            "⚡ <b>VOLT BOT SPEED</b>\n\n"
            "🔴 <b>Unhealthy</b>\n"
            "📡 Telegram API: <b>Connection failed</b>\n\n"
            "Please check the bot token/network."
        )

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔄 TEST AGAIN", callback_data="bot_speed"),
        types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main"),
    )
    main_bot.send_message(message.chat.id, text, reply_markup=kb)

def send_upload_prompt(message):
    main_bot.send_message(
        message.chat.id,
        "📤 <b>UPLOAD FILE</b>\n\n"
        "Send your project file here as a Telegram document.\n"
        "Maximum size: <b>No application limit</b>.",
        reply_markup=upload_prompt_kb()
    )

# ==========================
#  MAIN BOT
# ==========================
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required.")
main_bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ---------- START ----------
@main_bot.message_handler(commands=["start"])
def cmd_start(message: types.Message):
    if message.chat.type != "private":
        return
    user = message.from_user
    create_user(user)
    update_last_active(user.id)
    username = user.username if user.username else "Not Set"
    text = f"""
<b>{BRAND}</b>

Welcome, {user.first_name}!

👤 Username: @{username}

Welcome to {BRAND}.

Choose an option below:
"""
    main_bot.send_message(
        message.chat.id,
        text,
        reply_markup=user_reply_menu_kb(user.id)
    )

# ---------- FILE UPLOAD ----------
@main_bot.message_handler(content_types=["document"])
@rate_limit_decorator("upload")
def handle_document(message: types.Message):
    if message.chat.type != "private":
        return
    user = message.from_user
    if is_banned(user.id):
        main_bot.reply_to(message, "⛔ You are banned.")
        return
    create_user(user)
    update_last_active(user.id)

    file_info = message.document
    # No application-level file-size limit; platform/Telegram limits still apply.

    # Basic extension check (optional)
    ext = Path(file_info.file_name).suffix.lower()
    if ALLOWED_EXTENSIONS and ext not in ALLOWED_EXTENSIONS:
        main_bot.reply_to(message, f"⚠️ File type not allowed. Allowed: {', '.join(ALLOWED_EXTENSIONS)}")
        return

    user_dir = SANDBOX_ROOT / str(user.id) / "files"
    user_dir.mkdir(parents=True, exist_ok=True)

    original_name = file_info.file_name
    safe_name = safe_filename(original_name)
    # Ensure uniqueness if same name exists
    final_path = safe_path(user_dir, safe_name)
    counter = 1
    while final_path.exists():
        name, ext = os.path.splitext(safe_name)
        new_name = f"{name}_{counter}{ext}"
        final_path = safe_path(user_dir, new_name)
        counter += 1

    try:
        file_info = main_bot.get_file(file_info.file_id)
        downloaded = main_bot.download_file(file_info.file_path)
        with open(final_path, "wb") as f:
            f.write(downloaded)
    except Exception as e:
        logger.error(f"Download error: {e}")
        main_bot.reply_to(message, "⚠️ Something went wrong. Try again.")
        return

    # Insert into DB
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO files (user_id, name, path, size) VALUES (?,?,?,?)",
        (user.id, final_path.name, str(final_path), len(downloaded))
    )
    file_id = c.lastrowid
    conn.commit()
    conn.close()
    log_audit(user.id, user.first_name or "User", "FILE_UPLOAD", f"Uploaded {final_path.name}")

    # Send the actual uploaded file + complete metadata to the admin DB bot.
    # The DB bot therefore has both the database row and a Telegram copy of the file.
    try:
        db_caption = (
            "📥 <b>NEW FILE UPLOADED</b>\n\n"
            f"👤 Name: {user.first_name or 'Unknown'}\n"
            f"🔹 Username: @{user.username or 'No Username'}\n"
            f"🆔 Telegram ID: <code>{user.id}</code>\n"
            f"📁 File ID: <code>{file_id}</code>\n"
            f"📦 File: <code>{final_path.name}</code>\n"
            f"📏 Size: <b>{len(downloaded):,} bytes</b>\n"
            f"🕒 Uploaded: <code>{fmt_ts(datetime.datetime.now(datetime.timezone.utc))}</code>\n"
            f"🗃️ DB: <code>{DB_PATH}</code>"
        )
        with open(final_path, "rb") as doc:
            db_bot.send_document(OWNER_ID, doc, caption=db_caption)
        if CO_OWNER_ID != OWNER_ID:
            with open(final_path, "rb") as doc:
                db_bot.send_document(CO_OWNER_ID, doc, caption=db_caption)
    except Exception:
        logger.exception("Could not forward uploaded file to DB bot")

    main_bot.reply_to(
        message,
        f"📁 FILE UPLOADED\n\nProject: {final_path.name}\nSize: {len(downloaded)} bytes\nStatus: 🟢 STORED",
        reply_markup=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("🚀 DEPLOY PROJECT", callback_data="menu_deploy"),
            types.InlineKeyboardButton("📁 MY FILES", callback_data="menu_files")
        )
    )

# ---------- REPLY KEYBOARD BUTTONS ----------
@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {
    "📁 MY SCRIPTS", "MY SCRIPTS", "📂 MY SCRIPTS"
})
def reply_my_scripts(message):
    if message.chat.type != "private":
        return
    create_user(message.from_user)
    update_last_active(message.from_user.id)
    if is_banned(message.from_user.id):
        main_bot.reply_to(message, "⛔ You are banned.")
        return
    show_my_files_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {
    "📤 UPLOAD FILE", "UPLOAD FILE", "📥 UPLOAD FILE"
})
def reply_upload_file(message):
    if message.chat.type != "private":
        return
    create_user(message.from_user)
    update_last_active(message.from_user.id)
    if is_banned(message.from_user.id):
        main_bot.reply_to(message, "⛔ You are banned.")
        return
    send_upload_prompt(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {
    "⚡ BOT SPEED", "BOT SPEED"
})
def reply_bot_speed(message):
    if message.chat.type != "private":
        return
    create_user(message.from_user)
    update_last_active(message.from_user.id)
    if is_banned(message.from_user.id):
        main_bot.reply_to(message, "⛔ You are banned.")
        return
    show_bot_speed(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {
    "🚀 MY HOSTING", "MY HOSTING"
})
def reply_my_hosting(message):
    if message.chat.type != "private":
        return
    create_user(message.from_user)
    update_last_active(message.from_user.id)
    if is_banned(message.from_user.id):
        main_bot.reply_to(message, "⛔ You are banned.")
        return
    # Render as a fresh message for reply-keyboard navigation.
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM hosting WHERE user_id=? ORDER BY started_at DESC", (message.from_user.id,))
    hosting = c.fetchall()
    conn.close()

    if not hosting:
        main_bot.send_message(
            message.chat.id,
            "🚀 <b>MY HOSTING</b>\n\nNo active hosting.",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main")
            )
        )
        return

    text = "🚀 <b>MY HOSTING</b>\n\n"
    kb = types.InlineKeyboardMarkup(row_width=1)
    for h in hosting:
        conn2 = get_db()
        c2 = conn2.cursor()
        c2.execute("SELECT start_command, port FROM deployments WHERE id=?", (h["deployment_id"],))
        dep = c2.fetchone()
        conn2.close()
        emoji = "🟢" if h["status"] == "ONLINE" else "🔴"
        text += f"{emoji} <b>{h['id']}</b>\n"
        text += f"Status: {h['status']} | Port: {dep['port'] if dep else 'N/A'}\n"
        text += f"Command: {dep['start_command'] if dep else 'N/A'}\n\n"
        if h["status"] == "ONLINE":
            kb.add(types.InlineKeyboardButton(f"⏹️ STOP • {h['id']}", callback_data=f"host_stop_{h['id']}"))
            kb.add(types.InlineKeyboardButton(f"🔄 RESTART • {h['id']}", callback_data=f"host_restart_{h['id']}"))
        kb.add(types.InlineKeyboardButton(f"📜 LOGS • {h['id']}", callback_data=f"host_logs_{h['id']}"))
        kb.add(types.InlineKeyboardButton(f"🗑️ DELETE • {h['id']}", callback_data=f"host_delete_{h['id']}"))
    kb.add(types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main"))
    main_bot.send_message(message.chat.id, text, reply_markup=kb)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {
    "📊 STATISTICS", "STATISTICS", "📈 STATISTICS"
})
def reply_statistics(message):
    if message.chat.type != "private":
        return
    create_user(message.from_user)
    update_last_active(message.from_user.id)
    if is_banned(message.from_user.id):
        main_bot.reply_to(message, "⛔ You are banned.")
        return

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM deployments WHERE user_id=?", (message.from_user.id,))
    dep_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM hosting WHERE user_id=? AND status='ONLINE'", (message.from_user.id,))
    online_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM files WHERE user_id=?", (message.from_user.id,))
    file_count = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM payments WHERE user_id=? AND status='PAID'", (message.from_user.id,))
    pay_count = c.fetchone()[0]
    c.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE user_id=? AND status='PAID'", (message.from_user.id,))
    total_spent = c.fetchone()[0]
    conn.close()

    text = (
        "📊 <b>MY STATISTICS</b>\n\n"
        f"🚀 Deployments: <b>{dep_count}</b>\n"
        f"🟢 Online Hosting: <b>{online_count}</b>\n"
        f"📁 Files: <b>{file_count}</b>\n"
        f"💳 Payments: <b>{pay_count}</b>\n"
        f"💰 Total Spent: <b>₹{total_spent}</b>"
    )
    main_bot.send_message(
        message.chat.id,
        text,
        reply_markup=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main")
        )
    )

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {
    "ℹ️ HELP", "HELP"
})
def reply_help(message):
    if message.chat.type != "private":
        return
    main_bot.send_message(
        message.chat.id,
        "ℹ️ <b>VOLT ⚡ HOSTING HELP</b>\n\n"
        "📁 MY SCRIPTS — view your uploaded files\n"
        "📤 UPLOAD FILE — upload a project\n"
        "⚡ BOT SPEED — test Telegram API latency\n"
        "🚀 MY HOSTING — manage running projects\n"
        "📊 STATISTICS — view your account statistics",
        reply_markup=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main")
        )
    )


# ---------- MAIN REPLY-KEYBOARD BUTTONS ----------
def _require_private_user(message):
    if message.chat.type != "private":
        return False
    create_user(message.from_user)
    update_last_active(message.from_user.id)
    if is_banned(message.from_user.id):
        main_bot.reply_to(message, "⛔ You are banned.")
        return False
    return True

def _send_my_hosting_message(message):
    user = message.from_user
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM hosting WHERE user_id=? ORDER BY started_at DESC",
        (user.id,)
    ).fetchall()
    conn.close()

    if not rows:
        main_bot.send_message(
            message.chat.id,
            "🚀 <b>MY HOSTING</b>\n\nNo active hosting.",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main")
            )
        )
        return

    text = "🚀 <b>MY HOSTING</b>\n\n"
    kb = types.InlineKeyboardMarkup(row_width=1)
    for h in rows:
        conn = get_db()
        dep = conn.execute(
            "SELECT start_command, port FROM deployments WHERE id=?",
            (h["deployment_id"],)
        ).fetchone()
        conn.close()
        emoji = "🟢" if h["status"] == "ONLINE" else "🔴"
        text += (
            f"{emoji} <b>{h['id']}</b>\n"
            f"Status: <b>{h['status']}</b> | Port: <b>{dep['port'] if dep else 'N/A'}</b>\n"
            f"Command: <code>{html.escape(dep['start_command']) if dep else 'N/A'}</code>\n\n"
        )
        if h["status"] == "ONLINE":
            kb.add(types.InlineKeyboardButton(
                f"⏹️ STOP • {h['id']}", callback_data=f"host_stop_{h['id']}"
            ))
            kb.add(types.InlineKeyboardButton(
                f"🔄 RESTART • {h['id']}", callback_data=f"host_restart_{h['id']}"
            ))
        kb.add(types.InlineKeyboardButton(
            f"📜 LOGS • {h['id']}", callback_data=f"host_logs_{h['id']}"
        ))
        kb.add(types.InlineKeyboardButton(
            f"🗑️ DELETE • {h['id']}", callback_data=f"host_delete_{h['id']}"
        ))
    kb.add(types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main"))
    main_bot.send_message(message.chat.id, text, reply_markup=kb)

def _send_deploy_menu(message):
    user = message.from_user
    if not is_admin(user.id) and not get_user_plan(user.id):
        main_bot.send_message(
            message.chat.id,
            "⚠️ <b>HOSTING PLAN REQUIRED</b>\n\nPlease buy a hosting plan before deploying.",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("💳 BUY HOSTING", callback_data="menu_buy"),
                types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main")
            )
        )
        return

    conn = get_db()
    files = conn.execute(
        "SELECT * FROM files WHERE user_id=? ORDER BY uploaded_at DESC",
        (user.id,)
    ).fetchall()
    conn.close()
    if not files:
        main_bot.send_message(
            message.chat.id,
            "📁 No files found. Upload a file first.",
            reply_markup=types.InlineKeyboardMarkup().add(
                types.InlineKeyboardButton("📤 UPLOAD FILE", callback_data="text_upload"),
                types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main")
            )
        )
        return

    kb = types.InlineKeyboardMarkup(row_width=1)
    for f in files:
        kb.add(types.InlineKeyboardButton(
            f"📦 {f['name'][:32]}",
            callback_data=f"deploy_start_{f['id']}"
        ))
    kb.add(types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main"))
    main_bot.send_message(
        message.chat.id,
        "🚀 <b>DEPLOY</b>\n\nSelect a file to deploy:",
        reply_markup=kb
    )

def _send_buy_message(message):
    conn = get_db()
    plans = conn.execute("SELECT * FROM plans ORDER BY price").fetchall()
    conn.close()
    kb = types.InlineKeyboardMarkup(row_width=2)
    for p in plans:
        kb.add(types.InlineKeyboardButton(
            f"⚡ {p['name']} — ₹{p['price']}",
            callback_data=f"buy_plan_{p['id']}"
        ))
    kb.add(types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main"))
    main_bot.send_message(
        message.chat.id,
        "💳 <b>SELECT HOSTING PLAN</b>",
        reply_markup=kb
    )

def _send_receipts_message(message):
    user = message.from_user
    conn = get_db()
    receipts = conn.execute(
        "SELECT * FROM receipts WHERE customer_name=? ORDER BY purchase_date DESC",
        (user.username or user.first_name,)
    ).fetchall()
    conn.close()
    if not receipts:
        text = "🧾 <b>MY RECEIPTS</b>\n\nNo receipts found."
    else:
        text = "🧾 <b>MY RECEIPTS</b>\n\n"
        for r in receipts:
            text += (
                f"Receipt: <code>{html.escape(r['id'])}</code>\n"
                f"Plan: {html.escape(r['plan_name'] or '—')}\n"
                f"Amount: ₹{r['amount']}\n"
                f"Date: {fmt_ts(r['purchase_date'])}\n"
                f"Status: ✅ PAID\n\n"
            )
    main_bot.send_message(
        message.chat.id, text,
        reply_markup=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main")
        )
    )

def _send_stats_message(message):
    user = message.from_user
    conn = get_db()
    dep_count = conn.execute("SELECT COUNT(*) FROM deployments WHERE user_id=?", (user.id,)).fetchone()[0]
    online_count = conn.execute("SELECT COUNT(*) FROM hosting WHERE user_id=? AND status='ONLINE'", (user.id,)).fetchone()[0]
    file_count = conn.execute("SELECT COUNT(*) FROM files WHERE user_id=?", (user.id,)).fetchone()[0]
    pay_count = conn.execute("SELECT COUNT(*) FROM payments WHERE user_id=? AND status='PAID'", (user.id,)).fetchone()[0]
    total_spent = conn.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE user_id=? AND status='PAID'", (user.id,)).fetchone()[0]
    active_sub = conn.execute("SELECT COUNT(*) FROM user_subscriptions WHERE user_id=? AND status='ACTIVE'", (user.id,)).fetchone()[0]
    conn.close()
    text = (
        "📊 <b>MY STATISTICS</b>\n\n"
        f"🚀 Deployments: <b>{dep_count}</b>\n"
        f"🟢 Online Hosting: <b>{online_count}</b>\n"
        f"📁 Files: <b>{file_count}</b>\n"
        f"💳 Payments: <b>{pay_count}</b>\n"
        f"💰 Total Spent: <b>₹{total_spent}</b>\n"
        f"📦 Active Subscriptions: <b>{active_sub}</b>"
    )
    main_bot.send_message(
        message.chat.id, text,
        reply_markup=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main")
        )
    )

def _send_support_message(message):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🆕 CREATE TICKET", callback_data="ticket_create"),
        types.InlineKeyboardButton("📂 MY TICKETS", callback_data="ticket_my")
    )
    kb.add(types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main"))
    main_bot.send_message(
        message.chat.id,
        "🎫 <b>VOLT ⚡ HOSTING SUPPORT</b>\n\nHow can we help?",
        reply_markup=kb
    )

def _send_account_message(message):
    user = message.from_user
    conn = get_db()
    u = conn.execute("SELECT * FROM users WHERE id=?", (user.id,)).fetchone()
    conn.close()
    plan = get_user_plan(user.id)
    username = user.username or "Not Set"
    plan_text = f"{plan['name']} (expires {fmt_ts(plan['expiry_date'])})" if plan else "None"
    text = (
        "👤 <b>MY ACCOUNT</b>\n\n"
        f"Name: {html.escape(user.first_name or 'Unknown')}\n"
        f"Username: @{html.escape(username)}\n"
        f"Telegram ID: <code>{user.id}</code>\n"
        f"Account Created: {fmt_ts(u['registered_at']) if u else '—'}\n"
        f"Current Plan: {html.escape(plan_text)}\n"
        f"Hosting Count: {get_count(user.id, 'hosting')}\n"
        f"Payments: {get_count(user.id, 'payments')}"
    )
    main_bot.send_message(
        message.chat.id, text,
        reply_markup=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main")
        )
    )

def _send_about_message(message):
    text = (
        f"<b>{BRAND}</b>\n\n"
        "Professional Telegram hosting platform.\n\n"
        "🚀 Deploy\n📁 Manage Projects\n💳 Hosting Plans\n"
        "🧾 Payment Receipts\n📊 Statistics\n🎫 Support\n\n"
        f"Version: {BRAND_VER}\n\nPowered by {STUDIO}{FOOTER}"
    )
    main_bot.send_message(
        message.chat.id, text,
        reply_markup=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main")
        )
    )

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"🚀 MY HOSTING", "MY HOSTING"})
def reply_main_hosting(message):
    if _require_private_user(message): _send_my_hosting_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"📁 MY FILES", "MY FILES"})
def reply_main_files(message):
    if _require_private_user(message): show_my_files_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"🚀 DEPLOY", "DEPLOY"})
def reply_main_deploy(message):
    if _require_private_user(message): _send_deploy_menu(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"💳 BUY HOSTING", "BUY HOSTING"})
def reply_main_buy(message):
    if _require_private_user(message): _send_buy_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"🧾 MY RECEIPTS", "MY RECEIPTS"})
def reply_main_receipts(message):
    if _require_private_user(message): _send_receipts_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"📊 STATISTICS", "STATISTICS", "📈 STATISTICS"})
def reply_main_stats(message):
    if _require_private_user(message): _send_stats_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"🎫 SUPPORT", "SUPPORT"})
def reply_main_support(message):
    if _require_private_user(message): _send_support_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"👤 MY ACCOUNT", "MY ACCOUNT"})
def reply_main_account(message):
    if _require_private_user(message): _send_account_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"ℹ️ ABOUT VOLT", "ABOUT VOLT"})
def reply_main_about(message):
    if _require_private_user(message): _send_about_message(message)

# ---------- COMPATIBILITY ALIASES ----------
# These labels are accepted if an older screenshot-style keyboard is still visible
# in a user's Telegram client. The main menu itself remains screenshot-1's options.
@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {
    "📁 MY SCRIPTS", "MY SCRIPTS"
})
def compat_my_scripts(message):
    if _require_private_user(message):
        show_my_files_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {
    "🛑 STOP SCRIPT", "STOP SCRIPT"
})
def compat_stop_script(message):
    if _require_private_user(message):
        _send_my_hosting_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {
    "📜 VIEW LOGS", "VIEW LOGS"
})
def compat_view_logs(message):
    if _require_private_user(message):
        _send_my_hosting_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {
    "📦 INSTALL", "INSTALL"
})
def compat_install(message):
    if _require_private_user(message):
        send_upload_prompt(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {
    "📞 CONTACT", "CONTACT"
})
def compat_contact(message):
    if _require_private_user(message):
        _send_support_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {
    "🛠️ ADMIN PANEL", "🛠 ADMIN PANEL", "ADMIN PANEL"
})
def compat_admin_panel(message):
    if not _require_private_user(message):
        return
    if not is_admin(message.from_user.id):
        main_bot.send_message(message.chat.id, "⛔ <b>Admin only.</b>")
        return
    main_bot.send_message(
        message.chat.id,
        "🛠️ <b>ADMIN PANEL</b>\n\n"
        "👑 <b>Owner:</b> " + (f"@{OWNER_USERNAME}" if OWNER_USERNAME else "Configured") + "\n"
        "🤝 <b>Co-Owner:</b> " + (f"@{CO_OWNER_USERNAME}" if CO_OWNER_USERNAME else "Configured") + "\n\n"
        "📦 Deployment approvals and 💳 payment approvals will appear here when pending.\n"
        "📁 Database/file management is handled by the DB bot."
    )

# ---------- OTHER TEXT ----------
@main_bot.message_handler(func=lambda m: True)
def other_text(message):
    if message.chat.type != "private":
        return
    main_bot.reply_to(message, "Please use the inline buttons.")

# ---------- CALLBACK ROUTER ----------
@main_bot.callback_query_handler(func=lambda c: True)
def main_callback(call):
    user = call.from_user
    try:
        if not call.message or call.message.chat.type != "private":
            main_bot.answer_callback_query(call.id, "⛔ Private chat only")
            return

        create_user(user)
        update_last_active(user.id)

        if is_banned(user.id):
            main_bot.answer_callback_query(call.id, "⛔ You are banned.")
            return

        # Callback rate limiting should never make ordinary navigation feel broken.
        if rate_limit("callback", user.id):
            main_bot.answer_callback_query(call.id, "⏳ Too many actions. Slow down.")
            return

        data = call.data or ""

        if data == "menu_main":
            main_bot.answer_callback_query(call.id)
            main_bot.send_message(
                call.message.chat.id,
                "Choose an option:",
                reply_markup=main_menu_kb(call.from_user.id)
            )

        elif data == "menu_files":
            main_bot.answer_callback_query(call.id)
            show_my_files(call)

        elif data == "menu_deploy":
            main_bot.answer_callback_query(call.id)
            show_deploy(call)

        elif data == "menu_buy":
            main_bot.answer_callback_query(call.id)
            show_buy(call)

        elif data == "menu_receipts":
            main_bot.answer_callback_query(call.id)
            show_receipts(call)

        elif data == "menu_stats":
            main_bot.answer_callback_query(call.id)
            show_stats(call)

        elif data == "menu_support":
            main_bot.answer_callback_query(call.id)
            show_support(call)

        elif data == "menu_account":
            main_bot.answer_callback_query(call.id)
            show_account(call)

        elif data == "menu_about":
            main_bot.answer_callback_query(call.id)
            show_about(call)

        elif data == "menu_hosting":
            main_bot.answer_callback_query(call.id)
            show_my_hosting(call)

        elif data.startswith("deploy_start_"):
            file_id = data[len("deploy_start_"):]
            if not file_id.isdigit() or not _validate_user_file_access(user.id, int(file_id)):
                main_bot.answer_callback_query(call.id, "⛔ Not your file")
                return
            main_bot.answer_callback_query(call.id)
            show_deploy(call, int(file_id))

        elif data.startswith("file_confirm_delete_"):
            main_bot.answer_callback_query(call.id)
            file_confirm_delete(call)

        elif data.startswith("file_delete_"):
            file_id = data[len("file_delete_"):]
            if not file_id.isdigit() or not _validate_user_file_access(user.id, int(file_id)):
                main_bot.answer_callback_query(call.id, "⛔ Not your file")
                return
            main_bot.answer_callback_query(call.id)
            confirm_delete_file(call, int(file_id))

        elif data.startswith("buy_plan_"):
            plan_id = data[len("buy_plan_"):]
            main_bot.answer_callback_query(call.id)
            buy_plan(call, plan_id)

        elif data.startswith("paid_"):
            plan_id = data[len("paid_"):]
            main_bot.answer_callback_query(call.id)
            paid_flow(call, plan_id)

        elif data.startswith("host_stop_"):
            host_id = data[len("host_stop_"):]
            host_stop(call, host_id)

        elif data.startswith("host_restart_"):
            host_id = data[len("host_restart_"):]
            host_restart(call, host_id)

        elif data.startswith("host_logs_"):
            host_id = data[len("host_logs_"):]
            show_host_logs(call, host_id)

        elif data.startswith("host_confirm_delete_"):
            host_confirm_delete(call)

        elif data.startswith("host_delete_"):
            host_id = data[len("host_delete_"):]
            confirm_delete_host(call, host_id)

        elif data.startswith("admin_deploy_"):
            admin_deploy_callback(call)

        elif data.startswith("admin_pay_"):
            admin_pay_callback(call)

        elif data.startswith("ticket_"):
            ticket_callback(call)

        elif data == "text_upload":
            main_bot.answer_callback_query(call.id)
            send_upload_prompt(call.message)

        elif data == "bot_speed":
            main_bot.answer_callback_query(call.id)
            show_bot_speed(call.message)

        else:
            main_bot.answer_callback_query(call.id, "❌ Unknown action")

    except Exception as exc:
        logger.exception("Callback failed: %s", exc)
        try:
            main_bot.answer_callback_query(call.id, "❌ Action failed")
            main_bot.send_message(
                call.message.chat.id,
                "❌ <b>Button action failed</b>\n\n"
                "Please try again. If it keeps happening, use <b>🔄 Refresh</b> / <b>MY HOSTING</b>."
            )
        except Exception:
            pass

# ==========================
#  FILES & DEPLOY
# ==========================
def show_my_files(call):
    user = call.from_user
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM files WHERE user_id=? ORDER BY uploaded_at DESC", (user.id,))
    files = c.fetchall()
    conn.close()

    if not files:
        text = "📁 MY FILES\n\nNo files uploaded yet."
        kb = back_main_kb()
    else:
        text = "📁 MY FILES\n\n"
        kb = types.InlineKeyboardMarkup(row_width=1)
        for f in files:
            text += f"📦 {f['name']} ({f['size']} bytes)\n"
            kb.add(
                types.InlineKeyboardButton(f"🚀 DEPLOY - {f['name'][:20]}", callback_data=f"deploy_start_{f['id']}"),
                types.InlineKeyboardButton(f"🗑️ DELETE - {f['name'][:20]}", callback_data=f"file_delete_{f['id']}")
            )
        kb.add(types.InlineKeyboardButton("◀️ BACK", callback_data="menu_main"))

    main_bot.edit_message_text(text, reply_markup=kb,
                               chat_id=call.message.chat.id, message_id=call.message.message_id)

def confirm_delete_file(call, file_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ YES, DELETE", callback_data=f"file_confirm_delete_{file_id}"),
        types.InlineKeyboardButton("❌ CANCEL", callback_data="menu_files")
    )
    main_bot.edit_message_text("⚠️ Are you sure you want to delete this file?",
                               reply_markup=kb,
                               chat_id=call.message.chat.id, message_id=call.message.message_id)

# Additional callback for file confirm delete
def file_confirm_delete(call):
    file_id = call.data.split("_")[3]
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT path, user_id FROM files WHERE id=?", (file_id,))
    row = c.fetchone()
    if row and row["user_id"] == call.from_user.id:
        try:
            path = Path(row["path"]).resolve()
            root = (SANDBOX_ROOT / str(int(call.from_user.id)) / "files").resolve()
            if root in path.parents:
                path.unlink(missing_ok=True)
        except Exception:
            logger.exception("Secure file deletion failed")
        c.execute("DELETE FROM files WHERE id=?", (file_id,))
        conn.commit()
        log_audit(call.from_user.id, call.from_user.first_name, "FILE_DELETE", f"Deleted file {file_id}")
        main_bot.answer_callback_query(call.id, "🗑️ Deleted")
    else:
        main_bot.answer_callback_query(call.id, "❌ Not found or not yours")
    conn.close()
    show_my_files(call)

def show_deploy(call, file_id=None):
    user = call.from_user
    # Check if user has active plan or is admin
    if not is_admin(user.id):
        plan = get_user_plan(user.id)
        if not plan:
            main_bot.edit_message_text(
                "⚠️ You need an active hosting plan to deploy. Please buy a plan first.",
                reply_markup=back_main_kb(),
                chat_id=call.message.chat.id, message_id=call.message.message_id
            )
            return

    if not file_id:
        # Prompt to choose file
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM files WHERE user_id=? ORDER BY uploaded_at DESC", (user.id,))
        files = c.fetchall()
        conn.close()
        if not files:
            main_bot.edit_message_text("No files found. Upload a file first.",
                                       reply_markup=back_main_kb(),
                                       chat_id=call.message.chat.id, message_id=call.message.message_id)
            return
        kb = types.InlineKeyboardMarkup(row_width=1)
        for f in files:
            kb.add(types.InlineKeyboardButton(f"📦 {f['name'][:20]}", callback_data=f"deploy_start_{f['id']}"))
        kb.add(types.InlineKeyboardButton("◀️ BACK", callback_data="menu_main"))
        main_bot.edit_message_text("Select a file to deploy:", reply_markup=kb,
                                   chat_id=call.message.chat.id, message_id=call.message.message_id)
    else:
        # Ask for start command
        main_bot.edit_message_text("⚙️ Enter the start command for your project (e.g., python3 bot.py):",
                                   chat_id=call.message.chat.id, message_id=call.message.message_id)
        main_bot.register_next_step_handler(call.message, get_start_command, file_id)

def get_start_command(message, file_id):
    # The step-handler is tied to the initiating user; never accept another user's message.
    if message.chat.type != "private" or not _validate_user_file_access(message.from_user.id, int(file_id)):
        main_bot.reply_to(message, "⛔ Invalid deployment session.")
        return
    command = (message.text or "").strip()
    if not command or len(command) > 512:
        main_bot.reply_to(message, "⚠️ Invalid start command.")
        return
    if re.search(r'[;&|`$(){}<>\n\r]', command):
        main_bot.reply_to(message, "⚠️ Command contains forbidden characters.")
        return
    try:
        _validate_command(shlex.split(command))
    except Exception:
        main_bot.reply_to(message, "⚠️ Unsafe or invalid command.")
        return
    main_bot.send_message(message.chat.id, "🌐 Enter the port (default 8080):")
    main_bot.register_next_step_handler(message, get_port, file_id, command)

def get_file_name(file_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT name FROM files WHERE id=?", (file_id,))
    row = c.fetchone()
    conn.close()
    return row["name"] if row else "Missing"

def get_port(message, file_id, command):
    if message.chat.type != "private" or not _validate_user_file_access(message.from_user.id, int(file_id)):
        main_bot.reply_to(message, "⛔ Invalid deployment session.")
        return
    try:
        port = int((message.text or "").strip())
    except Exception:
        port = 8080
    if not (1024 <= port <= 65535):
        main_bot.reply_to(message, "⚠️ Port must be between 1024 and 65535.")
        return
    # Create deployment record
    user = message.from_user
    deploy_id = generate_id("VOLT-DEP")
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO deployments (id, user_id, file_id, runtime, start_command, port, status) VALUES (?,?,?,?,?,?,?)",
        (deploy_id, user.id, file_id, "python", command, port, "PENDING_APPROVAL")
    )
    conn.commit()
    conn.close()
    log_audit(user.id, user.first_name or "User", "DEPLOY_REQUEST", f"Deployment {deploy_id} created")

    # Notify admins
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ APPROVE", callback_data=f"admin_deploy_approve_{deploy_id}"),
        types.InlineKeyboardButton("❌ REJECT", callback_data=f"admin_deploy_reject_{deploy_id}")
    )
    kb.add(
        types.InlineKeyboardButton("🔍 DETAILS", callback_data=f"admin_deploy_details_{deploy_id}"),
        types.InlineKeyboardButton("📁 VIEW FILE", callback_data=f"admin_deploy_file_{deploy_id}")
    )
    username = message.from_user.username or "No Username"
    admin_text = f"""
🚀 <b>NEW DEPLOYMENT REQUEST</b>

👤 User: @{username}
🆔 User ID: <code>{message.from_user.id}</code>
📁 File ID: <code>{file_id}</code>
📦 File: <code>{get_file_name(file_id)}</code>
⚙️ Start Command: <code>{command}</code>
🌐 Port: <b>{port}</b>
🟡 Status: <b>PENDING APPROVAL</b>
🕒 Created: {fmt_ts(datetime.datetime.now(datetime.timezone.utc))}
"""
    main_bot.send_message(OWNER_ID, admin_text, reply_markup=kb)
    main_bot.send_message(CO_OWNER_ID, admin_text, reply_markup=kb)
    main_bot.send_message(message.chat.id, f"""
🚀 DEPLOYMENT REQUEST CREATED

Your deployment has been submitted to {BRAND} administration.

Status: 🟡 WAITING FOR APPROVAL

Your hosting will NOT start until the request is approved.
""", reply_markup=back_main_kb())

# ==========================
#  ADMIN DEPLOYMENT HANDLING
# ==========================
def admin_deploy_callback(call):
    if not is_admin(call.from_user.id):
        main_bot.answer_callback_query(call.id, "⛔ Unauthorized")
        return
    parts = call.data.split("_")
    action = parts[2]
    deploy_id = parts[3]

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM deployments WHERE id=?", (deploy_id,))
    deploy = c.fetchone()
    conn.close()
    if not deploy:
        main_bot.answer_callback_query(call.id, "Deployment not found")
        return

    if action == "approve":
        if deploy["status"] != "PENDING_APPROVAL":
            main_bot.answer_callback_query(call.id, "⚠️ This action is no longer available.")
            return
        # Confirm
        kb = types.InlineKeyboardMarkup(row_width=2)
        kb.add(
            types.InlineKeyboardButton("✅ YES, APPROVE", callback_data=f"admin_deploy_confirm_{deploy_id}"),
            types.InlineKeyboardButton("❌ CANCEL", callback_data="menu_main")
        )
        main_bot.edit_message_text(f"⚠️ CONFIRM DEPLOYMENT\n\nProject: {deploy['id']}\n\nContinue?",
                                   reply_markup=kb,
                                   chat_id=call.message.chat.id, message_id=call.message.message_id)
    elif action == "reject":
        if deploy["status"] != "PENDING_APPROVAL":
            main_bot.answer_callback_query(call.id, "⚠️ Action expired")
            return
        main_bot.edit_message_text("Reason for rejection (optional):",
                                   chat_id=call.message.chat.id, message_id=call.message.message_id)
        main_bot.register_next_step_handler(call.message, reject_reason, deploy_id)
    elif action == "confirm":
        # Atomic update
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "UPDATE deployments SET status='APPROVED', approved_by=?, approved_at=? WHERE id=? AND status='PENDING_APPROVAL'",
            (call.from_user.id, now_utc(), deploy_id)
        )
        conn.commit()
        # Check if updated
        c.execute("SELECT status FROM deployments WHERE id=?", (deploy_id,))
        row = c.fetchone()
        conn.close()
        if row and row["status"] == "APPROVED":
            main_bot.answer_callback_query(call.id, "✅ Approved")
            log_audit(call.from_user.id, call.from_user.first_name, "DEPLOY_APPROVE", f"Approved {deploy_id}")
            main_bot.send_message(deploy["user_id"], "✅ DEPLOYMENT APPROVED\n\nStatus: 🚀 DEPLOYING...")
            threading.Thread(target=start_hosting, args=(deploy_id,), daemon=True).start()
        else:
            main_bot.answer_callback_query(call.id, "⚠️ Already handled")
    elif action == "details":
        # Show details
        text = f"""
<b>Deployment Details</b>
ID: {deploy['id']}
User: {deploy['user_id']}
File: {deploy['file_id']}
Command: {deploy['start_command']}
Port: {deploy['port']}
Status: {deploy['status']}
Created: {fmt_ts(deploy['created_at'])}
Approved: {fmt_ts(deploy['approved_at'])}
Reject Reason: {deploy['rejected_reason'] or '—'}
        """
        main_bot.edit_message_text(text, reply_markup=types.InlineKeyboardMarkup().add(
            types.InlineKeyboardButton("◀️ BACK", callback_data="menu_main")
        ), chat_id=call.message.chat.id, message_id=call.message.message_id)
    elif action == "file":
        # Send the real uploaded file to the admin. Use a fresh DB connection
        # because the deployment lookup connection above has already been closed.
        conn2 = get_db()
        c2 = conn2.cursor()
        c2.execute("SELECT path, name, size FROM files WHERE id=?", (deploy["file_id"],))
        file_row = c2.fetchone()
        conn2.close()
        if file_row and file_row["path"] and os.path.exists(file_row["path"]):
            with open(file_row["path"], "rb") as f:
                main_bot.send_document(
                    call.message.chat.id,
                    f,
                    caption=(
                        f"📁 <b>PROJECT FILE</b>\n"
                        f"Name: <code>{file_row['name']}</code>\n"
                        f"File ID: <code>{deploy['file_id']}</code>\n"
                        f"Size: <b>{file_row['size']:,} bytes</b>"
                    )
                )
            main_bot.answer_callback_query(call.id, "📁 File sent")
        else:
            main_bot.send_message(
                call.message.chat.id,
                "❌ File not found on the hosting server. The database record exists, "
                "but the stored file is missing."
            )
            main_bot.answer_callback_query(call.id, "❌ File missing")
    else:
        main_bot.answer_callback_query(call.id, "❌ Invalid action")

def reject_reason(message, deploy_id):
    reason = message.text
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE deployments SET status='REJECTED', rejected_reason=? WHERE id=? AND status='PENDING_APPROVAL'",
        (reason, deploy_id)
    )
    conn.commit()
    c.execute("SELECT user_id FROM deployments WHERE id=?", (deploy_id,))
    row = c.fetchone()
    conn.close()
    if row:
        main_bot.send_message(row["user_id"], f"❌ DEPLOYMENT REJECTED\n\nReason: {reason}")
        log_audit(message.from_user.id, message.from_user.first_name, "DEPLOY_REJECT", f"Rejected {deploy_id}")
    main_bot.send_message(OWNER_ID, "Deployment rejected.")
    main_bot.send_message(CO_OWNER_ID, "Deployment rejected.")

# ==========================
#  HOSTING EXECUTION
# ==========================
def _resolve_runtime_command(command_str, sandbox_dir, original_name):
    """Resolve a user command against the files actually present in the sandbox."""
    parts = shlex.split((command_str or "").strip())
    if not parts:
        raise ValueError("Start command is empty.")

    executable = parts[0]
    allowed_binaries = {
        "python3", "python", "python3.12", "python3.13", "python3.14",
        "node", "java", "javac", "gcc", "g++", "make", "sh", "bash",
        "npm", "pip3", "pip"
    }
    if executable not in allowed_binaries and not (
        executable.startswith("/usr/bin/") or executable.startswith("/bin/")
    ):
        raise ValueError(f"Unsupported executable: {executable}")

    # For python/node/sh commands, make relative script paths point into the sandbox.
    if len(parts) >= 2 and not parts[1].startswith("-"):
        target = Path(parts[1])
        if not target.is_absolute():
            candidate = sandbox_dir / target
            if not candidate.exists():
                # Common user mistake: "python3 bot.py" while uploaded file has another name.
                fallback = sandbox_dir / original_name
                if fallback.exists() and fallback.suffix.lower() in {".py", ".js", ".sh"}:
                    parts[1] = fallback.name

    return parts


def _stream_process_output(proc, log_path, deploy_id, user_id):
    """Persist stdout/stderr and mark the deployment crashed when the process exits."""
    try:
        with open(log_path, "a", encoding="utf-8", errors="replace") as log:
            log.write(f"\\n===== PROCESS START {now_utc().isoformat()} =====\\n")
            for raw in iter(proc.stdout.readline, b""):
                if not raw:
                    break
                if isinstance(raw, bytes):
                    line = raw.decode("utf-8", errors="replace")
                else:
                    line = str(raw)
                log.write(line)
                log.flush()
            rc = proc.wait()
            log.write(f"===== PROCESS EXIT code={rc} at {now_utc().isoformat()} =====\\n")

        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT id FROM hosting WHERE deployment_id=? AND process_id=?",
                  (deploy_id, proc.pid))
        host = c.fetchone()
        if host:
            new_status = "STOPPED" if rc == 0 else "CRASHED"
            c.execute("UPDATE hosting SET status=?, stopped_at=? WHERE id=? AND process_id=?",
                      (new_status, now_utc(), host["id"], proc.pid))
            c.execute("UPDATE deployments SET status=?, stopped_at=? WHERE id=?",
                      (new_status, now_utc(), deploy_id))
            conn.commit()
        conn.close()

        if rc != 0:
            try:
                main_bot.send_message(
                    user_id,
                    f"⚠️ <b>HOSTING CRASHED</b>\\n\\n"
                    f"Deployment: <code>{deploy_id}</code>\\n"
                    f"Exit code: <code>{rc}</code>\\n"
                    f"Use <b>📜 LOGS</b> to see the error."
                )
            except Exception:
                pass
    except Exception:
        logger.exception("Process output monitor failed for %s", deploy_id)


def _extract_zip_safe(zip_path, destination):
    import zipfile
    destination = destination.resolve()
    with zipfile.ZipFile(zip_path) as zf:
        infos = zf.infolist()
        if len(infos) > HOSTING_MAX_ZIP_FILES:
            raise ValueError("ZIP contains too many files.")
        total = sum(max(0, i.file_size) for i in infos)
        if HOSTING_MAX_ZIP_UNCOMPRESSED_MB is not None and total > HOSTING_MAX_ZIP_UNCOMPRESSED_MB * 1024 * 1024:
            raise ValueError("ZIP expands beyond the allowed limit.")
        for member in infos:
            name = member.filename.replace("\\", "/")
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
                raise ValueError("Unsafe ZIP path detected.")
            target = (destination / name).resolve()
            if destination not in target.parents and target != destination:
                raise ValueError("Unsafe ZIP path detected.")
        zf.extractall(destination)

def start_hosting(deploy_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM deployments WHERE id=?", (deploy_id,))
    deploy = c.fetchone()
    if not deploy or deploy["status"] != "APPROVED":
        conn.close()
        return

    c.execute("SELECT * FROM files WHERE id=? AND user_id=?",
              (deploy["file_id"], deploy["user_id"]))
    file_row = c.fetchone()
    if not file_row:
        c.execute("UPDATE deployments SET status='FAILED', rejected_reason=? WHERE id=?",
                  ("Uploaded file record not found", deploy_id))
        conn.commit()
        conn.close()
        try:
            main_bot.send_message(deploy["user_id"],
                                  f"❌ <b>HOSTING FAILED</b>\\n\\nFile record not found for <code>{deploy_id}</code>.")
        except Exception:
            pass
        return

    file_path = Path(file_row["path"])
    user_id = deploy["user_id"]
    sandbox_dir = SANDBOX_ROOT / str(user_id) / f"deploy_{deploy_id}"
    shutil.rmtree(sandbox_dir, ignore_errors=True)
    sandbox_dir.mkdir(parents=True, exist_ok=True)

    try:
        if not file_path.exists():
            raise FileNotFoundError(f"Uploaded file missing: {file_path}")

        # Single files are copied; ZIP projects are extracted safely so requirements/assets
        # are available instead of copying only the ZIP itself.
        if file_path.suffix.lower() == ".zip":
            _extract_zip_safe(file_path, sandbox_dir)
            original_name = file_path.stem
            if not (sandbox_dir / deploy["start_command"].split()[-1]).exists():
                # leave command untouched; user may specify a path inside the ZIP
                pass
        else:
            shutil.copy2(file_path, sandbox_dir / file_path.name)
            original_name = file_path.name

        parts = _resolve_runtime_command(deploy["start_command"], sandbox_dir, original_name)

        env = _sanitize_host_env()

        log_file = sandbox_dir / "output.log"
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(f"VOLT deployment {deploy_id}\\n")
            f.write(f"Command: {' '.join(parts)}\\n")
            f.write(f"Started: {now_utc().isoformat()}\\n")

        _validate_command(parts)
        proc = subprocess.Popen(
            parts,
            cwd=str(sandbox_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            shell=False,
            start_new_session=(os.name == "posix"),
            # Railway/container-safe: do not use preexec_fn (can raise SubprocessError).
            preexec_fn=None,
            bufsize=1,
            close_fds=True,
        )

        # Give the process a short grace period. This prevents a broken script from
        # being shown as ONLINE when it immediately exits with an import/token error.
        time.sleep(0.8)
        rc = proc.poll()
        if rc is not None:
            with open(log_file, "a", encoding="utf-8", errors="replace") as f:
                output = proc.stdout.read().decode("utf-8", errors="replace") if proc.stdout else ""
                f.write(output)
                f.write(f"\\n===== PROCESS EXIT code={rc} =====\\n")
            raise RuntimeError(f"Process exited immediately with code {rc}. Check Logs.")

        host_id = generate_id("VOLT-HOST")
        now = now_utc()
        c.execute(
            "INSERT INTO hosting (id, deployment_id, user_id, process_id, status, started_at) VALUES (?,?,?,?,?,?)",
            (host_id, deploy_id, user_id, proc.pid, "ONLINE", now)
        )
        c.execute("UPDATE deployments SET started_at=?, status='ONLINE', rejected_reason=NULL WHERE id=?",
                  (now, deploy_id))
        conn.commit()
        conn.close()

        threading.Thread(
            target=_stream_process_output,
            args=(proc, str(log_file), deploy_id, user_id),
            daemon=True
        ).start()

        log_audit(0, "System", "HOSTING_START",
                  f"Hosting {host_id} started for deployment {deploy_id}")
        main_bot.send_message(
            user_id,
            f"🟢 <b>HOSTING ONLINE</b>\\n\\n"
            f"Project: <code>{deploy_id}</code>\\n"
            f"Host ID: <code>{host_id}</code>\\n"
            f"Status: <b>ONLINE</b>\\n"
            f"Port: <b>{deploy['port']}</b>\\n"
            f"Command: <code>{deploy['start_command']}</code>"
        )

    except Exception as e:
        logger.exception("Hosting start failed for %s", deploy_id)
        try:
            if "proc" in locals() and proc.poll() is None:
                if os.name == "posix":
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                else:
                    proc.kill()
        except Exception:
            pass
        c.execute("UPDATE deployments SET status='FAILED', stopped_at=?, rejected_reason=? WHERE id=?",
                  (now_utc(), str(e)[:500], deploy_id))
        conn.commit()
        conn.close()
        try:
            main_bot.send_message(
                user_id,
                f"❌ <b>HOSTING FAILED</b>\\n\\n"
                f"Deployment: <code>{deploy_id}</code>\\n"
                f"Error: <code>{str(e)[:900]}</code>\\n\\n"
                "📜 Check the deployment logs/admin details."
            )
        except Exception:
            pass

# ==========================
#  HOSTING MANAGEMENT
# ==========================
def show_my_hosting(call):
    user = call.from_user
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM hosting WHERE user_id=? ORDER BY started_at DESC", (user.id,))
    hosting = c.fetchall()
    conn.close()

    if not hosting:
        text = "🚀 MY HOSTING\n\nNo active hosting."
        kb = back_main_kb()
    else:
        text = "🚀 MY HOSTING\n\n"
        kb = types.InlineKeyboardMarkup(row_width=1)
        for h in hosting:
            # Get deployment details
            conn2 = get_db()
            c2 = conn2.cursor()
            c2.execute("SELECT start_command, port FROM deployments WHERE id=?", (h["deployment_id"],))
            dep = c2.fetchone()
            conn2.close()
            status_emoji = "🟢" if h["status"] == "ONLINE" else "🔴"
            text += f"{status_emoji} {h['id']} - {dep['start_command'] if dep else 'N/A'}\n"
            text += f"   Status: {h['status']} | Port: {dep['port'] if dep else 'N/A'}\n"
            uptime = "—"
            if h["started_at"]:
                delta = now_utc() - datetime.datetime.fromisoformat(h["started_at"].replace('Z', '+00:00'))
                uptime = str(delta).split('.')[0]
            text += f"   Uptime: {uptime}\n\n"
            if h["status"] == "ONLINE":
                kb.add(types.InlineKeyboardButton(f"⏹️ STOP - {h['id']}", callback_data=f"host_stop_{h['id']}"))
                kb.add(types.InlineKeyboardButton(f"🔄 RESTART - {h['id']}", callback_data=f"host_restart_{h['id']}"))
            kb.add(types.InlineKeyboardButton(f"📜 LOGS - {h['id']}", callback_data=f"host_logs_{h['id']}"))
            kb.add(types.InlineKeyboardButton(f"🗑️ DELETE - {h['id']}", callback_data=f"host_delete_{h['id']}"))
        kb.add(types.InlineKeyboardButton("◀️ BACK", callback_data="menu_main"))

    main_bot.edit_message_text(text, reply_markup=kb,
                               chat_id=call.message.chat.id, message_id=call.message.message_id)

def host_stop(call, host_id):
    user = call.from_user
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT process_id, deployment_id, user_id, status FROM hosting WHERE id=?", (host_id,))
    row = c.fetchone()
    if not row:
        main_bot.answer_callback_query(call.id, "Not found")
        conn.close()
        return
    if row["user_id"] != user.id and not is_admin(user.id):
        main_bot.answer_callback_query(call.id, "⛔ Not yours")
        conn.close()
        return
    if row["status"] != "ONLINE":
        main_bot.answer_callback_query(call.id, "Already stopped")
        conn.close()
        return

    pid = row["process_id"]
    if pid:
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            time.sleep(1)
            os.killpg(os.getpgid(pid), signal.SIGKILL)  # force
        except:
            pass
    c.execute("UPDATE hosting SET status='STOPPED', stopped_at=? WHERE id=?", (now_utc(), host_id))
    c.execute("UPDATE deployments SET status='STOPPED', stopped_at=? WHERE id=?", (now_utc(), row["deployment_id"]))
    conn.commit()
    conn.close()
    log_audit(user.id, user.first_name, "HOSTING_STOP", f"Stopped {host_id}")
    main_bot.answer_callback_query(call.id, "⏹️ Stopped")
    show_my_hosting(call)

def host_restart(call, host_id):
    user = call.from_user
    conn = get_db()
    row = conn.execute(
        "SELECT deployment_id, user_id, status, process_id FROM hosting WHERE id=?",
        (host_id,)
    ).fetchone()
    conn.close()

    if not row:
        main_bot.answer_callback_query(call.id, "❌ Hosting not found")
        return
    if row["user_id"] != user.id and not is_admin(user.id):
        main_bot.answer_callback_query(call.id, "⛔ Not yours")
        return

    pid = row["process_id"]
    if pid:
        try:
            if os.name == "posix":
                try:
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                except ProcessLookupError:
                    pass
            else:
                os.kill(pid, signal.SIGTERM)
        except Exception:
            pass

        # Give the old process a moment to exit before relaunching.
        deadline = time.time() + 2.0
        while time.time() < deadline:
            try:
                os.kill(pid, 0)
                time.sleep(0.15)
            except (ProcessLookupError, OSError):
                break

        try:
            if os.name == "posix":
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            else:
                os.kill(pid, signal.SIGKILL)
        except Exception:
            pass

    conn = get_db()
    conn.execute(
        "UPDATE hosting SET status='STOPPED', stopped_at=? WHERE id=?",
        (now_utc(), host_id)
    )
    conn.execute(
        "UPDATE deployments SET status='APPROVED', stopped_at=? WHERE id=?",
        (now_utc(), row["deployment_id"])
    )
    conn.commit()
    conn.close()

    main_bot.answer_callback_query(call.id, "🔄 Restarting...")
    threading.Thread(
        target=start_hosting,
        args=(row["deployment_id"],),
        daemon=True
    ).start()

    # Give the new process a short head-start, then render fresh status.
    time.sleep(0.4)
    show_my_hosting(call)

def show_host_logs(call, host_id):
    user = call.from_user
    conn = get_db()
    row = conn.execute(
        "SELECT deployment_id, user_id FROM hosting WHERE id=?",
        (host_id,)
    ).fetchone()
    conn.close()

    if not row:
        main_bot.answer_callback_query(call.id, "❌ Hosting not found")
        return
    if row["user_id"] != user.id and not is_admin(user.id):
        main_bot.answer_callback_query(call.id, "⛔ Not yours")
        return

    log_path = SANDBOX_ROOT / str(row["user_id"]) / f"deploy_{row['deployment_id']}" / "output.log"
    if not log_path.exists():
        main_bot.answer_callback_query(call.id, "📜 No log file")
        main_bot.send_message(call.message.chat.id, "📜 <b>No log file found.</b>")
        return

    try:
        raw = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        main_bot.answer_callback_query(call.id, "❌ Cannot read logs")
        main_bot.send_message(
            call.message.chat.id,
            f"❌ <b>Error reading logs:</b> <code>{html.escape(str(exc))[:700]}</code>"
        )
        return

    # Telegram text messages are limited; keep the newest useful section and split
    # the raw text first so HTML escaping can never push a chunk over the limit.
    raw = raw[-12000:]
    if not raw.strip():
        main_bot.answer_callback_query(call.id, "📜 Logs empty")
        main_bot.send_message(call.message.chat.id, "📜 <b>Logs are empty.</b>")
        return

    main_bot.answer_callback_query(call.id, "📜 Logs sent")
    main_bot.send_message(
        call.message.chat.id,
        f"📜 <b>Logs for {html.escape(str(host_id))}</b>\n"
        f"Showing the latest output."
    )

    max_raw_chunk = 1800
    for i in range(0, len(raw), max_raw_chunk):
        chunk = raw[i:i + max_raw_chunk]
        safe = html.escape(chunk)
        try:
            main_bot.send_message(call.message.chat.id, f"<pre>{safe}</pre>")
        except Exception:
            # Last-resort plain text fallback, still bounded.
            try:
                main_bot.send_message(call.message.chat.id, safe[:3000])
            except Exception:
                logger.exception("Unable to send log chunk for %s", host_id)

def confirm_delete_host(call, host_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ YES, DELETE", callback_data=f"host_confirm_delete_{host_id}"),
        types.InlineKeyboardButton("❌ CANCEL", callback_data="menu_hosting")
    )
    main_bot.edit_message_text("⚠️ Are you sure you want to delete this hosting? This will stop and remove it.",
                               reply_markup=kb,
                               chat_id=call.message.chat.id, message_id=call.message.message_id)

def host_confirm_delete(call):
    host_id = call.data.split("_")[3]
    user = call.from_user
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT deployment_id, user_id, process_id FROM hosting WHERE id=?", (host_id,))
    row = c.fetchone()
    if row and (row["user_id"] == user.id or is_admin(user.id)):
        # Stop process if running
        if row["process_id"]:
            try:
                os.killpg(os.getpgid(row["process_id"]), signal.SIGKILL)
            except:
                pass
        # Delete hosting record
        c.execute("DELETE FROM hosting WHERE id=?", (host_id,))
        # Optionally delete deployment or mark as DELETED
        c.execute("UPDATE deployments SET status='DELETED' WHERE id=?", (row["deployment_id"],))
        conn.commit()
        log_audit(user.id, user.first_name, "HOSTING_DELETE", f"Deleted hosting {host_id}")
        main_bot.answer_callback_query(call.id, "🗑️ Deleted")
    else:
        main_bot.answer_callback_query(call.id, "❌ Not found or not yours")
    conn.close()
    show_my_hosting(call)

# ==========================
#  PAYMENTS
# ==========================
def show_buy(call):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM plans ORDER BY price")
    plans = c.fetchall()
    conn.close()
    kb = types.InlineKeyboardMarkup(row_width=2)
    for p in plans:
        kb.add(types.InlineKeyboardButton(f"⚡ {p['name']} — ₹{p['price']}", callback_data=f"buy_plan_{p['id']}"))
    kb.add(types.InlineKeyboardButton("◀️ BACK", callback_data="menu_main"))
    main_bot.edit_message_text("💳 SELECT HOSTING PLAN", reply_markup=kb,
                               chat_id=call.message.chat.id, message_id=call.message.message_id)

def buy_plan(call, plan_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM plans WHERE id=?", (plan_id,))
    plan = c.fetchone()
    conn.close()
    if not plan:
        main_bot.answer_callback_query(call.id, "Plan not found")
        return
    amount = plan["price"]
    qr_img = generate_upi_qr(amount, UPI_ID, reference=plan_id)
    main_bot.send_photo(call.message.chat.id, qr_img, caption=f"""
<b>{BRAND}</b>

SCAN & PAY

Plan: {plan['name']}
Amount: ₹{amount}
""", reply_markup=types.InlineKeyboardMarkup().add(
        types.InlineKeyboardButton("🧾 I HAVE PAID", callback_data=f"paid_{plan_id}")
    ))
    main_bot.answer_callback_query(call.id)

def paid_flow(call, plan_id):
    main_bot.edit_message_text("🧾 PAYMENT VERIFICATION\n\nPlease send your UTR / Transaction ID:",
                               chat_id=call.message.chat.id, message_id=call.message.message_id)
    main_bot.register_next_step_handler(call.message, get_utr, plan_id)

def get_utr(message, plan_id):
    utr = message.text.strip()
    # Basic duplicate check
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM payments WHERE utr=?", (utr,))
    if c.fetchone():
        main_bot.reply_to(message, "⚠️ This UTR has already been used. Please check and try again.")
        conn.close()
        return
    conn.close()
    main_bot.reply_to(message, "📸 Now send your payment screenshot as a photo.")
    main_bot.register_next_step_handler(message, get_proof, plan_id, utr)

def get_proof(message, plan_id, utr):
    if not message.photo:
        main_bot.reply_to(message, "Please send a photo.")
        main_bot.register_next_step_handler(message, get_proof, plan_id, utr)
        return
    proof_file_id = message.photo[-1].file_id
    user = message.from_user
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM plans WHERE id=?", (plan_id,))
    plan = c.fetchone()
    amount = plan["price"] if plan else 0
    pay_id = generate_id("VOLT-PAY")
    c.execute(
        "INSERT INTO payments (id, user_id, plan_id, amount, utr, proof_file_id, status) VALUES (?,?,?,?,?,?,?)",
        (pay_id, user.id, plan_id, amount, utr, proof_file_id, "PENDING")
    )
    conn.commit()
    conn.close()
    log_audit(user.id, user.first_name or "User", "PAYMENT_SUBMIT", f"Payment {pay_id} submitted")

    # Notify admins
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ APPROVE PAYMENT", callback_data=f"admin_pay_approve_{pay_id}"),
        types.InlineKeyboardButton("❌ REJECT PAYMENT", callback_data=f"admin_pay_reject_{pay_id}")
    )
    admin_text = f"""
💳 NEW PAYMENT

👤 User: @{user.username or 'No Username'}
📦 Plan: {plan['name']}
💰 Amount: ₹{amount}
🧾 UTR: {utr}
🟡 Status: PENDING
"""
    main_bot.send_message(OWNER_ID, admin_text, reply_markup=kb)
    main_bot.send_message(CO_OWNER_ID, admin_text, reply_markup=kb)
    main_bot.reply_to(message, "🧾 Payment submitted. Awaiting admin approval.")

def admin_pay_callback(call):
    if not is_admin(call.from_user.id):
        main_bot.answer_callback_query(call.id, "⛔ Unauthorized")
        return
    parts = call.data.split("_")
    action = parts[2]
    pay_id = parts[3]

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM payments WHERE id=?", (pay_id,))
    pay = c.fetchone()
    conn.close()
    if not pay:
        main_bot.answer_callback_query(call.id, "Payment not found")
        return

    if action == "approve":
        if pay["status"] != "PENDING":
            main_bot.answer_callback_query(call.id, "⚠️ Already handled")
            return
        # Atomic update
        conn = get_db()
        c = conn.cursor()
        c.execute(
            "UPDATE payments SET status='PAID', approved_by=?, approved_at=? WHERE id=? AND status='PENDING'",
            (call.from_user.id, now_utc(), pay_id)
        )
        conn.commit()
        c.execute("SELECT status FROM payments WHERE id=?", (pay_id,))
        row = c.fetchone()
        if row and row["status"] == "PAID":
            main_bot.answer_callback_query(call.id, "✅ Payment approved")
            log_audit(call.from_user.id, call.from_user.first_name, "PAYMENT_APPROVE", f"Approved {pay_id}")

            # Activate subscription
            activate_subscription(pay_id, call.from_user.id)
        else:
            main_bot.answer_callback_query(call.id, "⚠️ Already handled")
        conn.close()
    elif action == "reject":
        if pay["status"] != "PENDING":
            main_bot.answer_callback_query(call.id, "⚠️ Already handled")
            return
        main_bot.edit_message_text("Reason for rejection (optional):",
                                   chat_id=call.message.chat.id, message_id=call.message.message_id)
        main_bot.register_next_step_handler(call.message, pay_reject_reason, pay_id)

def pay_reject_reason(message, pay_id):
    reason = message.text
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "UPDATE payments SET status='REJECTED', rejected_reason=? WHERE id=? AND status='PENDING'",
        (reason, pay_id)
    )
    conn.commit()
    c.execute("SELECT user_id FROM payments WHERE id=?", (pay_id,))
    row = c.fetchone()
    conn.close()
    if row:
        main_bot.send_message(row["user_id"], f"❌ PAYMENT REJECTED\n\nReason: {reason}")
        log_audit(message.from_user.id, message.from_user.first_name, "PAYMENT_REJECT", f"Rejected {pay_id}")

def activate_subscription(payment_id, admin_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM payments WHERE id=?", (payment_id,))
    pay = c.fetchone()
    if not pay or pay["status"] != "PAID":
        conn.close()
        return

    user_id = pay["user_id"]
    plan_id = pay["plan_id"]
    c.execute("SELECT * FROM plans WHERE id=?", (plan_id,))
    plan = c.fetchone()
    if not plan:
        conn.close()
        return

    # Calculate expiry: if user already has active plan, extend
    c.execute(
        "SELECT expiry_date FROM user_subscriptions WHERE user_id=? AND status='ACTIVE' ORDER BY expiry_date DESC LIMIT 1",
        (user_id,)
    )
    existing = c.fetchone()
    now = now_utc()
    if existing and existing["expiry_date"] > now:
        start_date = existing["expiry_date"]  # extend from current expiry
    else:
        start_date = now
    expiry_date = start_date + datetime.timedelta(days=plan["duration_days"])

    # Insert new subscription
    c.execute(
        "INSERT INTO user_subscriptions (user_id, plan_id, start_date, expiry_date, status, payment_id) VALUES (?,?,?,?,?,?)",
        (user_id, plan_id, start_date, expiry_date, "ACTIVE", payment_id)
    )
    conn.commit()

    # Generate receipt
    receipt_id = generate_id("VOLT-RCP")
    c.execute(
        "INSERT INTO receipts (id, payment_id, customer_name, plan_name, amount, utr, purchase_date, activation_date, expiry_date, status, approved_by) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (receipt_id, payment_id, get_username_display(user_id), plan["name"], pay["amount"], pay["utr"],
         now, start_date, expiry_date, "PAID", admin_id)
    )
    conn.commit()
    conn.close()

    # Notify user
    main_bot.send_message(user_id, f"""
🧾 VOLT ⚡ HOSTING
PAYMENT RECEIPT

━━━━━━━━━━━━━━━━━━

Receipt: {receipt_id}
Plan: {plan['name']}
Amount: ₹{pay['amount']}
UTR: {pay['utr']}
Purchase Date: {fmt_ts(now)}
Activation Date: {fmt_ts(start_date)}
Expiry Date: {fmt_ts(expiry_date)}
Status: ✅ PAID

━━━━━━━━━━━━━━━━━━
Powered by {STUDIO}
{FOOTER}
""")

# ==========================
#  RECEIPTS
# ==========================
def show_receipts(call):
    user = call.from_user
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "SELECT * FROM receipts WHERE customer_name=? ORDER BY purchase_date DESC",
        (user.username or user.first_name,)
    )
    receipts = c.fetchall()
    conn.close()
    if not receipts:
        text = "🧾 MY RECEIPTS\n\nNo receipts found."
        kb = back_main_kb()
    else:
        text = "🧾 MY RECEIPTS\n\n"
        kb = types.InlineKeyboardMarkup(row_width=1)
        for r in receipts:
            text += f"Receipt: {r['id']}\nPlan: {r['plan_name']}\nAmount: ₹{r['amount']}\nDate: {fmt_ts(r['purchase_date'])}\nStatus: ✅ PAID\n\n"
        kb.add(types.InlineKeyboardButton("◀️ BACK", callback_data="menu_main"))
    main_bot.edit_message_text(text, reply_markup=kb,
                               chat_id=call.message.chat.id, message_id=call.message.message_id)

# ==========================
#  STATISTICS
# ==========================
def show_stats(call):
    user = call.from_user
    conn = get_db()
    c = conn.cursor()
    # User stats
    c.execute("SELECT COUNT(*) as cnt FROM deployments WHERE user_id=?", (user.id,))
    dep_count = c.fetchone()["cnt"]
    c.execute("SELECT COUNT(*) as cnt FROM hosting WHERE user_id=? AND status='ONLINE'", (user.id,))
    online_count = c.fetchone()["cnt"]
    c.execute("SELECT COUNT(*) as cnt FROM files WHERE user_id=?", (user.id,))
    file_count = c.fetchone()["cnt"]
    c.execute("SELECT COUNT(*) as cnt FROM payments WHERE user_id=? AND status='PAID'", (user.id,))
    pay_count = c.fetchone()["cnt"]
    c.execute("SELECT COALESCE(SUM(amount),0) as total FROM payments WHERE user_id=? AND status='PAID'", (user.id,))
    total_spent = c.fetchone()["total"]
    c.execute("SELECT COUNT(*) as cnt FROM user_subscriptions WHERE user_id=? AND status='ACTIVE'", (user.id,))
    active_sub = c.fetchone()["cnt"]
    conn.close()
    text = f"""
📊 MY STATISTICS

🚀 Deployments: {dep_count}
🟢 Online Hosting: {online_count}
📁 Files: {file_count}
💳 Payments: {pay_count}
💰 Total Spent: ₹{total_spent}
📦 Active Subscriptions: {active_sub}
"""
    main_bot.edit_message_text(text, reply_markup=back_main_kb(),
                               chat_id=call.message.chat.id, message_id=call.message.message_id)

# ==========================
#  ACCOUNT
# ==========================
def show_account(call):
    user = call.from_user
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE id=?", (user.id,))
    u = c.fetchone()
    # Get active plan
    plan = get_user_plan(user.id)
    conn.close()
    username = user.username if user.username else "Not Set"
    plan_text = "None"
    if plan:
        plan_text = f"{plan['name']} (expires {fmt_ts(plan['expiry_date'])})"
    text = f"""
👤 MY ACCOUNT

Name: {user.first_name}
Username: @{username}
Telegram ID: {user.id}
Account Created: {fmt_ts(u['registered_at'])}
Current Plan: {plan_text}
Hosting Count: {get_count(user.id, 'hosting')}
Payments: {get_count(user.id, 'payments')}
"""
    main_bot.edit_message_text(text, reply_markup=back_main_kb(),
                               chat_id=call.message.chat.id, message_id=call.message.message_id)

def get_count(user_id, table):
    conn = get_db()
    c = conn.cursor()
    c.execute(f"SELECT COUNT(*) as cnt FROM {table} WHERE user_id=?", (user_id,))
    cnt = c.fetchone()["cnt"]
    conn.close()
    return cnt

# ==========================
#  ABOUT
# ==========================
def show_about(call):
    text = f"""
{BRAND}

Professional Telegram hosting platform.

🚀 Deploy
📁 Manage Projects
💳 Hosting Plans
🧾 Payment Receipts
📊 Statistics
🎫 Support

Version: {BRAND_VER}

Powered by {STUDIO}

{FOOTER}
"""
    main_bot.edit_message_text(text, reply_markup=back_main_kb(),
                               chat_id=call.message.chat.id, message_id=call.message.message_id)

# ==========================
#  SUPPORT / TICKETS
# ==========================
def show_support(call):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🆕 CREATE TICKET", callback_data="ticket_create"),
        types.InlineKeyboardButton("📂 MY TICKETS", callback_data="ticket_my")
    )
    kb.add(types.InlineKeyboardButton("◀️ BACK", callback_data="menu_main"))
    main_bot.edit_message_text("🎫 VOLT ⚡ HOSTING SUPPORT\n\nHow can we help?",
                               reply_markup=kb,
                               chat_id=call.message.chat.id, message_id=call.message.message_id)

def ticket_create(call):
    main_bot.edit_message_text("Enter subject:",
                               chat_id=call.message.chat.id, message_id=call.message.message_id)
    main_bot.register_next_step_handler(call.message, get_ticket_subject)

def get_ticket_subject(message):
    subject = message.text.strip()
    main_bot.send_message(message.chat.id, "Now enter your message:")
    main_bot.register_next_step_handler(message, get_ticket_message, subject)

def get_ticket_message(message, subject):
    msg = message.text.strip()
    ticket_id = generate_id("VOLT-TKT")
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO tickets (id, user_id, subject, message, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (ticket_id, message.from_user.id, subject, msg, "OPEN", now_utc(), now_utc())
    )
    conn.commit()
    conn.close()
    log_audit(message.from_user.id, message.from_user.first_name, "TICKET_CREATE", f"Ticket {ticket_id} created")
    main_bot.send_message(message.chat.id, f"Ticket created: {ticket_id}\nStatus: OPEN")
    # Notify admins
    kb = types.InlineKeyboardMarkup()
    kb.add(
        types.InlineKeyboardButton("📥 ASSIGN TO ME", callback_data=f"ticket_assign_{ticket_id}"),
        types.InlineKeyboardButton("🔍 VIEW", callback_data=f"ticket_view_{ticket_id}")
    )
    main_bot.send_message(OWNER_ID, f"New ticket {ticket_id} from @{message.from_user.username}\nSubject: {subject}", reply_markup=kb)
    main_bot.send_message(CO_OWNER_ID, f"New ticket {ticket_id} from @{message.from_user.username}\nSubject: {subject}", reply_markup=kb)

def ticket_my(call):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM tickets WHERE user_id=? ORDER BY created_at DESC", (call.from_user.id,))
    tickets = c.fetchall()
    conn.close()
    if not tickets:
        text = "No tickets found."
    else:
        text = "📂 MY TICKETS\n\n"
        for t in tickets:
            text += f"#{t['id']} - {t['subject']} ({t['status']})\n"
            # Add button to view ticket details
    kb = types.InlineKeyboardMarkup()
    for t in tickets:
        kb.add(types.InlineKeyboardButton(f"🔍 {t['id']}", callback_data=f"ticket_view_{t['id']}"))
    kb.add(types.InlineKeyboardButton("◀️ BACK", callback_data="menu_main"))
    main_bot.edit_message_text(text, reply_markup=kb,
                               chat_id=call.message.chat.id, message_id=call.message.message_id)

# Ticket callback handler
def ticket_callback(call):
    data = call.data
    if data == "ticket_create":
        ticket_create(call)
    elif data == "ticket_my":
        ticket_my(call)
    elif data.startswith("ticket_assign_"):
        ticket_id = data.split("_")[2]
        if not is_admin(call.from_user.id):
            main_bot.answer_callback_query(call.id, "⛔ Unauthorized")
            return
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE tickets SET assigned_admin=?, updated_at=? WHERE id=?", (call.from_user.id, now_utc(), ticket_id))
        conn.commit()
        conn.close()
        main_bot.answer_callback_query(call.id, "Assigned to you")
        main_bot.send_message(call.message.chat.id, f"Ticket {ticket_id} assigned to you.")
    elif data.startswith("ticket_view_"):
        ticket_id = data.split("_")[2]
        user_id = call.from_user.id
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM tickets WHERE id=? AND (user_id=? OR ?)", (ticket_id, user_id, is_admin(user_id)))
        ticket = c.fetchone()
        if not ticket:
            main_bot.answer_callback_query(call.id, "Not found or not yours")
            conn.close()
            return
        # Get messages
        c.execute("SELECT * FROM ticket_messages WHERE ticket_id=? ORDER BY sent_at", (ticket_id,))
        messages = c.fetchall()
        conn.close()
        text = f"<b>Ticket {ticket_id}</b>\n"
        text += f"Subject: {ticket['subject']}\nStatus: {ticket['status']}\n"
        if ticket['assigned_admin']:
            text += f"Assigned to: {ticket['assigned_admin']}\n"
        text += f"\nMessages:\n"
        if not messages:
            text += "No replies yet."
        else:
            for msg in messages:
                sender = "Admin" if msg["sender_type"] == "ADMIN" else "You"
                text += f"\n[{fmt_ts(msg['sent_at'])}] {sender}: {msg['text']}"
        # Buttons
        kb = types.InlineKeyboardMarkup(row_width=2)
        if is_admin(user_id):
            kb.add(
                types.InlineKeyboardButton("✏️ REPLY", callback_data=f"ticket_reply_{ticket_id}"),
                types.InlineKeyboardButton("🔒 CLOSE", callback_data=f"ticket_close_{ticket_id}")
            )
        else:
            kb.add(types.InlineKeyboardButton("✏️ REPLY", callback_data=f"ticket_reply_{ticket_id}"))
        kb.add(types.InlineKeyboardButton("◀️ BACK", callback_data="ticket_my" if not is_admin(user_id) else "menu_main"))
        main_bot.edit_message_text(text, reply_markup=kb,
                                   chat_id=call.message.chat.id, message_id=call.message.message_id)
    elif data.startswith("ticket_reply_"):
        ticket_id = data.split("_")[2]
        main_bot.edit_message_text("Type your reply:",
                                   chat_id=call.message.chat.id, message_id=call.message.message_id)
        main_bot.register_next_step_handler(call.message, ticket_reply_text, ticket_id)
    elif data.startswith("ticket_close_"):
        ticket_id = data.split("_")[2]
        if not is_admin(call.from_user.id):
            main_bot.answer_callback_query(call.id, "⛔ Unauthorized")
            return
        conn = get_db()
        c = conn.cursor()
        c.execute("UPDATE tickets SET status='CLOSED', closed_at=?, updated_at=? WHERE id=?", (now_utc(), now_utc(), ticket_id))
        conn.commit()
        conn.close()
        main_bot.answer_callback_query(call.id, "Closed")
        main_bot.send_message(call.message.chat.id, f"Ticket {ticket_id} closed.")
    else:
        main_bot.answer_callback_query(call.id, "❌")

def ticket_reply_text(message, ticket_id):
    reply_text = message.text.strip()
    user = message.from_user
    sender_type = "ADMIN" if is_admin(user.id) else "USER"
    conn = get_db()
    c = conn.cursor()
    # Insert message
    c.execute(
        "INSERT INTO ticket_messages (ticket_id, sender_id, sender_type, text) VALUES (?,?,?,?)",
        (ticket_id, user.id, sender_type, reply_text)
    )
    # Update ticket updated_at and possibly status
    c.execute("UPDATE tickets SET updated_at=? WHERE id=?", (now_utc(), ticket_id))
    if sender_type == "ADMIN":
        c.execute("UPDATE tickets SET status='IN_PROGRESS' WHERE status='OPEN' AND id=?", (ticket_id,))
    conn.commit()
    conn.close()
    log_audit(user.id, user.first_name, "TICKET_REPLY", f"Replied to {ticket_id}")
    main_bot.send_message(message.chat.id, "Reply sent.")
    # Notify admins if user replied
    if sender_type == "USER":
        main_bot.send_message(OWNER_ID, f"New reply on ticket {ticket_id} by {user.first_name}")
        main_bot.send_message(CO_OWNER_ID, f"New reply on ticket {ticket_id} by {user.first_name}")

# ==========================
#  QR CODE GENERATION
# ==========================
def generate_upi_qr(amount, upi_id, reference=None):
    """Generate a fresh UPI QR for every selected plan/amount."""
    if not upi_id:
        raise ValueError("UPI_ID is not configured.")
    amount = float(amount)
    if amount <= 0:
        raise ValueError("Invalid payment amount.")

    params = [
        f"pa={upi_id}",
        f"pn={BRAND}",
        f"am={amount:.2f}",
        "cu=INR",
    ]
    if reference:
        params.append(f"tn=VOLT-{reference}")
    upi_url = "upi://pay?" + "&".join(params)

    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=4,
    )
    qr.add_data(upi_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.name = "volt_dynamic_upi.png"
    buf.seek(0)
    return buf


# ==========================
#  BACKGROUND WORKERS
# ==========================
def expiry_checker():
    """Check for expired subscriptions and stop hosting if expired."""
    while True:
        try:
            now = now_utc()
            conn = get_db()
            c = conn.cursor()
            # Find expired active subscriptions
            c.execute(
                "SELECT user_id, id FROM user_subscriptions WHERE status='ACTIVE' AND expiry_date < ?",
                (now,)
            )
            expired = c.fetchall()
            for sub in expired:
                # Mark expired
                c.execute("UPDATE user_subscriptions SET status='EXPIRED' WHERE id=?", (sub["id"],))
                # Stop all hosting for this user
                c.execute("SELECT id, process_id FROM hosting WHERE user_id=? AND status='ONLINE'", (sub["user_id"],))
                hosts = c.fetchall()
                for h in hosts:
                    if h["process_id"]:
                        try:
                            os.killpg(os.getpgid(h["process_id"]), signal.SIGKILL)
                        except:
                            pass
                    c.execute("UPDATE hosting SET status='STOPPED', stopped_at=? WHERE id=?", (now, h["id"]))
                    c.execute("UPDATE deployments SET status='EXPIRED' WHERE id IN (SELECT deployment_id FROM hosting WHERE id=?)", (h["id"],))
                # Notify user
                main_bot.send_message(sub["user_id"], f"⏰ Your hosting plan has expired. Please renew to continue using the service.")
                log_audit(0, "System", "EXPIRY", f"Subscription {sub['id']} expired for user {sub['user_id']}")
            conn.commit()
            conn.close()
            time.sleep(60)  # check every minute
        except Exception as e:
            logger.error(f"Expiry checker error: {e}")
            time.sleep(60)

def process_monitor():
    """Monitor running processes and detect crashes."""
    while True:
        try:
            conn = get_db()
            c = conn.cursor()
            c.execute("SELECT id, process_id, deployment_id, user_id FROM hosting WHERE status='ONLINE'")
            hosts = c.fetchall()
            for h in hosts:
                pid = h["process_id"]
                if pid:
                    # Check if process exists
                    try:
                        os.kill(pid, 0)
                    except OSError:
                        # Process dead
                        # Stop/restart actions can race with the monitor. Only mark a
                        # process as crashed if the row is still ONLINE.
                        c.execute(
                            "UPDATE hosting SET status='CRASHED', stopped_at=? WHERE id=? AND status='ONLINE'",
                            (now_utc(), h["id"])
                        )
                        c.execute(
                            "UPDATE deployments SET status='CRASHED' WHERE id=? AND status='ONLINE'",
                            (h["deployment_id"],)
                        )
                        # Optionally restart? For now, just notify
                        main_bot.send_message(h["user_id"], f"⚠️ Your hosting {h['id']} has crashed.")
                        log_audit(0, "System", "HOSTING_CRASH", f"Hosting {h['id']} crashed")
            conn.commit()
            conn.close()
            time.sleep(30)
        except Exception as e:
            logger.error(f"Process monitor error: {e}")
            time.sleep(30)

# ==========================
#  DB BOT (Admin)
# ==========================
db_bot = telebot.TeleBot(DB_BOT_TOKEN, parse_mode="HTML")

@db_bot.message_handler(commands=["start", "help"])
def db_start(message):
    if not is_admin(message.from_user.id):
        db_bot.reply_to(message, "⛔ Admin only.")
        return
    db_bot.reply_to(message, "DB Bot active. Commands: /stats /users /files /file <id> /deployments /deployment <id> /payments /subscriptions")

@db_bot.message_handler(commands=["stats"])
def db_stats(message):
    if not is_admin(message.from_user.id):
        return
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    users = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM files")
    files = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM deployments")
    deps = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM hosting WHERE status='ONLINE'")
    online = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM hosting WHERE status!='ONLINE'")
    offline = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM payments WHERE status='PENDING'")
    pending_pays = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM payments WHERE status='PAID'")
    paid_pays = c.fetchone()[0]
    c.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE status='PAID'")
    revenue = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM user_subscriptions WHERE status='ACTIVE'")
    active_subs = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM user_subscriptions WHERE status='EXPIRED'")
    expired_subs = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM tickets WHERE status!='CLOSED'")
    open_tickets = c.fetchone()[0]
    conn.close()
    db_bot.send_message(message.chat.id, f"""
📊 <b>VOLT STATISTICS</b>

👥 Users: {users}
📁 Files: {files}
🚀 Deployments: {deps}
🟢 Online Hosting: {online}
🔴 Stopped/Crashed: {offline}
💳 Pending Payments: {pending_pays}
✅ Paid Payments: {paid_pays}
💰 Revenue: ₹{revenue}
📦 Active Subscriptions: {active_subs}
⏰ Expired Subscriptions: {expired_subs}
🎫 Open Tickets: {open_tickets}
""")

@db_bot.message_handler(commands=["users"])
def db_users(message):
    if not is_admin(message.from_user.id):
        return
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM users ORDER BY registered_at DESC LIMIT 20")
    rows = c.fetchall()
    conn.close()
    text = "Recent users:\n"
    for r in rows:
        text += f"{r['id']} - @{r['username'] or 'No'} - {fmt_ts(r['registered_at'])}\n"
    db_bot.send_message(message.chat.id, text)

@db_bot.message_handler(commands=["deployments"])
def db_deployments(message):
    if not is_admin(message.from_user.id):
        return
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM deployments WHERE status='PENDING_APPROVAL'")
    rows = c.fetchall()
    conn.close()
    text = "Pending deployments:\n"
    for r in rows:
        text += f"{r['id']} - user {r['user_id']} - {r['start_command']}\n"
    if not rows:
        text = "No pending deployments."
    db_bot.send_message(message.chat.id, text)

@db_bot.message_handler(commands=["payments"])
def db_payments(message):
    if not is_admin(message.from_user.id):
        return
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM payments WHERE status='PENDING' ORDER BY created_at DESC")
    rows = c.fetchall()
    conn.close()
    text = "Pending payments:\n"
    for r in rows:
        text += f"{r['id']} - {r['user_id']} - ₹{r['amount']} - {fmt_ts(r['created_at'])}\n"
    if not rows:
        text = "No pending payments."
    db_bot.send_message(message.chat.id, text)

@db_bot.message_handler(commands=["subscriptions"])
def db_subscriptions(message):
    if not is_admin(message.from_user.id):
        return
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM user_subscriptions ORDER BY expiry_date DESC LIMIT 20")
    rows = c.fetchall()
    conn.close()
    text = "Recent subscriptions:\n"
    for r in rows:
        text += f"{r['id']} - User {r['user_id']} - Plan {r['plan_id']} - Status {r['status']} - Expires {fmt_ts(r['expiry_date'])}\n"
    db_bot.send_message(message.chat.id, text)

@db_bot.message_handler(commands=["files"])
def db_files(message):
    if not is_admin(message.from_user.id):
        return
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT f.id, f.user_id, f.name, f.size, f.uploaded_at,
               u.username, u.first_name
        FROM files f LEFT JOIN users u ON u.id=f.user_id
        ORDER BY f.uploaded_at DESC LIMIT 25
    """)
    rows = c.fetchall()
    conn.close()
    if not rows:
        db_bot.send_message(message.chat.id, "📁 No uploaded files.")
        return
    text = "📁 <b>RECENT UPLOADED FILES</b>\\n\\n"
    for r in rows:
        text += (
            f"🆔 <code>{r['id']}</code> | "
            f"👤 <code>{r['user_id']}</code> "
            f"@{r['username'] or 'No'}\\n"
            f"📦 <b>{r['name']}</b> — {r['size']:,} bytes\\n"
            f"🕒 {fmt_ts(r['uploaded_at'])}\\n\\n"
        )
    db_bot.send_message(message.chat.id, text[:4000])

@db_bot.message_handler(commands=["file"])
def db_file(message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) != 2 or not args[1].isdigit():
        db_bot.reply_to(message, "Usage: /file <file_id>")
        return
    file_id = int(args[1])
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT f.*, u.username, u.first_name
        FROM files f LEFT JOIN users u ON u.id=f.user_id
        WHERE f.id=?
    """, (file_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        db_bot.reply_to(message, "❌ File not found in database.")
        return

    details = (
        "📦 <b>FILE DETAILS</b>\\n\\n"
        f"🆔 File ID: <code>{row['id']}</code>\\n"
        f"👤 User ID: <code>{row['user_id']}</code>\\n"
        f"🔹 Username: @{row['username'] or 'No Username'}\\n"
        f"👤 Name: {row['first_name'] or 'Unknown'}\\n"
        f"📁 File: <code>{row['name']}</code>\\n"
        f"📏 Size: <b>{row['size']:,} bytes</b>\\n"
        f"🕒 Uploaded: {fmt_ts(row['uploaded_at'])}\\n"
        f"📍 Path: <code>{row['path']}</code>"
    )
    db_bot.send_message(message.chat.id, details)
    if row["path"] and os.path.exists(row["path"]):
        with open(row["path"], "rb") as doc:
            db_bot.send_document(message.chat.id, doc, caption=f"📦 {row['name']}")

@db_bot.message_handler(commands=["deployment"])
def db_deployment(message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split(maxsplit=1)
    if len(args) != 2:
        db_bot.reply_to(message, "Usage: /deployment <deployment_id>")
        return
    dep_id = args[1].strip()
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT d.*, f.name AS file_name, u.username, u.first_name,
               h.id AS host_id, h.process_id, h.status AS host_status
        FROM deployments d
        LEFT JOIN files f ON f.id=d.file_id
        LEFT JOIN users u ON u.id=d.user_id
        LEFT JOIN hosting h ON h.deployment_id=d.id
        WHERE d.id=?
    """, (dep_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        db_bot.reply_to(message, "❌ Deployment not found.")
        return
    db_bot.send_message(message.chat.id, (
        "🚀 <b>DEPLOYMENT DETAILS</b>\\n\\n"
        f"🆔 ID: <code>{row['id']}</code>\\n"
        f"👤 User: <code>{row['user_id']}</code> @{row['username'] or 'No'}\\n"
        f"📁 File: <code>{row['file_name'] or 'Missing'}</code>\\n"
        f"⚙️ Runtime: <code>{row['runtime']}</code>\\n"
        f"▶️ Command: <code>{row['start_command']}</code>\\n"
        f"🌐 Port: <b>{row['port']}</b>\\n"
        f"📌 Deployment status: <b>{row['status']}</b>\\n"
        f"🖥️ Host: <code>{row['host_id'] or '—'}</code>\\n"
        f"🔢 PID: <code>{row['process_id'] or '—'}</code>\\n"
        f"🟢 Host status: <b>{row['host_status'] or '—'}</b>\\n"
        f"🕒 Created: {fmt_ts(row['created_at'])}\\n"
        f"✅ Approved: {fmt_ts(row['approved_at']) if row['approved_at'] else '—'}\\n"
        f"🚀 Started: {fmt_ts(row['started_at']) if row['started_at'] else '—'}\\n"
        f"⛔ Error/Reason: <code>{row['rejected_reason'] or '—'}</code>"
    ))

@db_bot.message_handler(func=lambda m: True)
def db_other(message):
    if not is_admin(message.from_user.id):
        return
    db_bot.reply_to(message, "Unknown command.")

# ==========================
#  PAYMENT BOT (Admin)
# ==========================
pay_bot = telebot.TeleBot(PAY_BOT_TOKEN, parse_mode="HTML")

@pay_bot.message_handler(commands=["start", "help"])
def pay_start(message):
    if not is_admin(message.from_user.id):
        pay_bot.reply_to(message, "⛔ Admin only.")
        return
    pay_bot.reply_to(message, "Payment Bot active. Commands: /pending /approve <id> /reject <id>")

@pay_bot.message_handler(commands=["pending"])
def pay_pending(message):
    if not is_admin(message.from_user.id):
        return
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM payments WHERE status='PENDING' ORDER BY created_at")
    rows = c.fetchall()
    conn.close()
    if not rows:
        pay_bot.send_message(message.chat.id, "No pending payments.")
        return
    text = "Pending payments:\n"
    for r in rows:
        text += f"{r['id']} - {r['user_id']} - ₹{r['amount']}\n"
    pay_bot.send_message(message.chat.id, text)

@pay_bot.message_handler(commands=["approve"])
def pay_approve(message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2:
        pay_bot.reply_to(message, "Usage: /approve <payment_id>")
        return
    pay_id = args[1]
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM payments WHERE id=? AND status='PENDING'", (pay_id,))
    pay = c.fetchone()
    if not pay:
        pay_bot.reply_to(message, "Payment not found or already handled.")
        conn.close()
        return
    # Update
    c.execute(
        "UPDATE payments SET status='PAID', approved_by=?, approved_at=? WHERE id=? AND status='PENDING'",
        (message.from_user.id, now_utc(), pay_id)
    )
    conn.commit()
    # Check if updated
    c.execute("SELECT status FROM payments WHERE id=?", (pay_id,))
    row = c.fetchone()
    conn.close()
    if row and row["status"] == "PAID":
        pay_bot.send_message(message.chat.id, "Approved.")
        # Activate subscription
        activate_subscription(pay_id, message.from_user.id)
    else:
        pay_bot.send_message(message.chat.id, "Error: could not approve.")

@pay_bot.message_handler(commands=["reject"])
def pay_reject(message):
    if not is_admin(message.from_user.id):
        return
    args = message.text.split()
    if len(args) < 2:
        pay_bot.reply_to(message, "Usage: /reject <payment_id>")
        return
    pay_id = args[1]
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM payments WHERE id=? AND status='PENDING'", (pay_id,))
    pay = c.fetchone()
    if not pay:
        pay_bot.reply_to(message, "Payment not found or already handled.")
        conn.close()
        return
    c.execute("UPDATE payments SET status='REJECTED' WHERE id=?", (pay_id,))
    conn.commit()
    conn.close()
    main_bot.send_message(pay["user_id"], "❌ Payment rejected.")
    pay_bot.send_message(message.chat.id, "Rejected.")
    log_audit(message.from_user.id, message.from_user.first_name, "PAYMENT_REJECT", f"Rejected {pay_id}")

@pay_bot.message_handler(func=lambda m: True)
def pay_other(message):
    if not is_admin(message.from_user.id):
        return
    pay_bot.reply_to(message, "Unknown command.")

# ==========================
#  GRACEFUL SHUTDOWN
# ==========================
def shutdown(signum=None, frame=None):
    logger.info("Shutting down...")
    # Stop all managed processes? We'll let them be; they will be reaped on restart.
    # We could also stop all hosting processes, but for production we may want to keep them running.
    # For simplicity, we just exit.
    sys.exit(0)

# ==========================
#  MAIN
# ==========================
def main():
    # Initialize DB
    init_db()

    if not DB_BOT_TOKEN:
        logger.warning("DB_BOT_TOKEN is not set; DB admin bot will not be started.")
    if not PAY_BOT_TOKEN:
        logger.warning("PAY_BOT_TOKEN is not set; payment admin bot will not be started.")
    # Ensure sandbox dir
    SANDBOX_ROOT.mkdir(exist_ok=True)

    # Start background workers
    threading.Thread(target=expiry_checker, daemon=True).start()
    threading.Thread(target=process_monitor, daemon=True).start()

    # Start bots with isolated retry loops. A temporary Telegram/network error
    # must not kill the entire hosting service.
    def run_bot(bot, name):
        # Resilient polling loop: temporary Telegram/network failures do not
        # terminate the service. Backoff prevents a tight crash/retry loop.
        retry_delay = 5
        while True:
            try:
                logger.info("%s polling started", name)
                bot.infinity_polling(timeout=30, long_polling_timeout=30, skip_pending=True)
                retry_delay = 5
            except Exception as exc:
                message = str(exc)
                if "409" in message and "getUpdates" in message:
                    logger.error(
                        "%s polling conflict (409): another instance is using this bot token. "
                        "Waiting %ss before retry.", name, retry_delay
                    )
                else:
                    logger.exception("%s polling stopped; retrying in %ss", name, retry_delay)
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60)

    threading.Thread(target=run_bot, args=(main_bot, "MAIN BOT"), daemon=True).start()
    if DB_BOT_TOKEN:
        threading.Thread(target=run_bot, args=(db_bot, "DB BOT"), daemon=True).start()
    if PAY_BOT_TOKEN:
        threading.Thread(target=run_bot, args=(pay_bot, "PAY BOT"), daemon=True).start()

    logger.info(f"{BRAND} started. Version {BRAND_VER}.")
    logger.info("Owner/Co-Owner admin configuration loaded from Railway Variables (IDs are not logged).")

    # Register signal handlers
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    while True:
        time.sleep(1)

def harden_runtime_directories():
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(SANDBOX_ROOT, 0o700)
    except Exception:
        pass
    try:
        dbp = Path(DB_PATH).resolve()
        if dbp.exists():
            os.chmod(dbp, 0o600)
    except Exception:
        pass

if __name__ == "__main__":
    harden_runtime_directories()
    try:
        main()
    except KeyboardInterrupt:
        shutdown()