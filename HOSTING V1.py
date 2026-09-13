#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VOLT ⚡ HOSTING - Professional Telegram Hosting Platform
Version: V13.00.000 · V6 ULTRA
Powered by VOLT ⚡ STUDIO
© 2026 VOLT ⚡ STUDIO — All Rights Reserved.

Production‑ready single‑file implementation with private admin dashboard/logs and reliable hosted-process environment handling.
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
BRAND_VER = "V13.00.000 · V6 ULTRA"
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
HOSTING_MAX_ZIP_FILES = None  # No application-level ZIP file-count limit
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

def sanitize_legacy_start_commands():
    """Normalize legacy deployment records without asking users for startup settings."""
    conn = get_db()
    rows = conn.execute(
        "SELECT d.id, d.start_command, f.path, f.name "
        "FROM deployments d LEFT JOIN files f ON f.id=d.file_id"
    ).fetchall()
    changed = 0
    for row in rows:
        if not row["path"] or not row["name"]:
            continue
        try:
            cmd = _infer_start_command(Path(row["path"]), row["name"])
            if "$PORT" in cmd:
                cmd = cmd.replace("$PORT", str(int(os.environ.get("PORT", "8080"))))
            if str(row["start_command"] or "") != cmd:
                conn.execute(
                    "UPDATE deployments SET start_command=?, runtime='auto' WHERE id=?",
                    (cmd, row["id"]),
                )
                changed += 1
        except Exception:
            # Unsupported/missing legacy files remain untouched; start_hosting
            # will report a clean automatic-detection error if the user retries.
            continue
    conn.commit()
    conn.close()
    if changed:
        logger.info("Normalized %s legacy deployment startup records to automatic detection", changed)

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
        CREATE TABLE IF NOT EXISTS coupons (
            code TEXT PRIMARY KEY,
            discount_percent INTEGER NOT NULL DEFAULT 0,
            max_uses INTEGER NOT NULL DEFAULT 0,
            used_count INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER NOT NULL,
            referred_id INTEGER NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            reward REAL NOT NULL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS admin_users (
            user_id INTEGER PRIMARY KEY,
            role TEXT NOT NULL DEFAULT 'admin',
            enabled INTEGER NOT NULL DEFAULT 1,
            added_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    """Build the hosted-bot environment without leaking VOLT platform secrets.

    User/runtime variables configured on Railway remain available to hosted apps
    (for example API_ID, API_HASH, PORT, custom *_TOKEN variables). VOLT's own
    control-plane secrets are explicitly removed.
    """
    env = dict(os.environ)
    for key in HOST_SECRET_ENV_KEYS:
        env.pop(key, None)
    env.update({
        "PATH": os.environ.get("HOSTING_PATH", os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "HOME": str(SANDBOX_ROOT),
    })
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
    if user_id in (OWNER_ID, CO_OWNER_ID):
        return True
    try:
        conn=get_db()
        row=conn.execute("SELECT enabled FROM admin_users WHERE user_id=?", (user_id,)).fetchone()
        conn.close()
        return bool(row and row["enabled"])
    except Exception:
        return False

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


# ============================================================================
# VOLT HOSTING V5 ULTRA — UI COMPATIBILITY LAYER
# ============================================================================
# Presentation layer copied/adapted from the supplied V5 UI. Business logic,
# database schema, deployment engine and existing callback IDs remain intact.
VOLT_ULTRA_VERSION = "5.0.0 ULTRA"
VOLT_UI = {
    "brand": "⚡ VOLT HOSTING",
    "tagline": "Deploy Anything. Anywhere. Anytime.",
    "line": "━━━━━━━━━━━━━━━━━━━━",
    "thin": "────────────────────",
    "online": "● ONLINE",
    "secure": "● SECURE",
    "running": "● RUNNING",
    "offline": "● OFFLINE",
    "processing": "● PROCESSING",
    "deploying": "● DEPLOYING",
}

def volt_header(title=None, status="● ONLINE"):
    title = title or VOLT_UI["brand"]
    return f"⚡ <b>{html.escape(str(title))}</b>\n<code>{html.escape(str(status))}</code>\n{VOLT_UI['line']}"

def volt_footer():
    return f"\n{VOLT_UI['thin']}\n<b>{BRAND} • {BRAND_VER}</b>"

def volt_card(title, body="", status=None):
    head = f"⚡ <b>{html.escape(str(title))}</b>"
    if status:
        head += f"\n<code>{html.escape(str(status))}</code>"
    return head + f"\n{VOLT_UI['line']}\n" + str(body) + volt_footer()

def volt_status(label, value=True):
    return f"{html.escape(str(label)):<22} <code>{'● ENABLED' if value else '○ DISABLED'}</code>"

def volt_progress(percent):
    try:
        percent = max(0, min(100, int(percent)))
    except Exception:
        percent = 0
    filled = int(percent / 10)
    return f"[{'█' * filled}{'░' * (10 - filled)}] {percent}%"

def volt_button(text, callback):
    return types.InlineKeyboardButton(text, callback_data=callback)

def volt_back_button():
    return volt_button("↩️ BACK", "menu_main")

def volt_dashboard_markup(uid):
    """V5 ULTRA user dashboard. No legacy V12 menu buttons."""
    rows = [
        [volt_button("📤 UPLOAD FILE", "text_upload"),
         volt_button("📁 MY FILES", "menu_files")],
        [volt_button("📊 ANALYTICS", "menu_stats"),
         volt_button("👤 ACCOUNT", "menu_account")],
        [volt_button("💎 PREMIUM", "menu_buy"),
         volt_button("🎫 SUPPORT", "menu_support")],
        [volt_button("ℹ️ ABOUT VOLT", "menu_about"),
         volt_button("⚡ BOT SPEED", "menu_speed")],
    ]
    if is_admin(uid):
        rows.append([volt_button("👑 V5 CONTROL CENTER", "admin_dashboard")])
    return types.InlineKeyboardMarkup(rows)

def volt_owner_markup():
    """Clean V5 ULTRA admin UI; legacy V12 admin controls are intentionally excluded."""
    return types.InlineKeyboardMarkup([
        [volt_button("📊 OVERVIEW", "admin_refresh"),
         volt_button("👥 USERS", "admin_users")],
        [volt_button("📁 FILES", "admin_files"),
         volt_button("🚀 DEPLOYMENTS", "admin_deployments")],
        [volt_button("💳 PAYMENTS", "admin_payments"),
         volt_button("📜 AUDIT LOGS", "admin_logs")],
        [volt_button("🔄 REFRESH", "admin_refresh"),
         volt_button("🏠 MAIN MENU", "menu_main")],
    ])


# ==========================
#  KEYBOARDS
# ==========================
def main_menu_kb(user_id=None):
    """V5 ULTRA premium reply keyboard matching the inline dashboard UI."""
    kb = types.ReplyKeyboardMarkup(
        resize_keyboard=True,
        row_width=2,
        selective=False,
        input_field_placeholder="⚡ Choose VOLT option…",
    )
    rows = [
        ("📤 UPLOAD FILE", "📁 MY FILES"),
        ("📊 ANALYTICS", "👤 ACCOUNT"),
        ("💎 PREMIUM", "🎫 SUPPORT"),
        ("🎁 REFERRAL", "ℹ️ ABOUT VOLT"),
        ("⚡ BOT SPEED",),
    ]
    if user_id and is_admin(user_id):
        rows.append(("👑 V5 CONTROL CENTER",))
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
    kb.add(types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main"))
    return kb

def upload_prompt_kb():
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📁 MY SCRIPTS", callback_data="menu_files"))
    kb.add(types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main"))
    return kb


def file_detail_kb(file_id, hosting_row=None):
    """Screenshot-style per-file control panel. Logs are owner-scoped to the file."""
    kb = types.InlineKeyboardMarkup(row_width=2)
    status = str(hosting_row["status"]).upper() if hosting_row else "STOPPED"
    if status == "ONLINE":
        kb.add(
            types.InlineKeyboardButton("⏹️ STOP", callback_data=f"host_stop_{hosting_row['id']}"),
            types.InlineKeyboardButton("🔄 RESTART", callback_data=f"host_restart_{hosting_row['id']}")
        )
    else:
        kb.add(
            types.InlineKeyboardButton("▶️ START", callback_data=f"file_start_{file_id}"),
            types.InlineKeyboardButton("🔄 RESTART", callback_data=f"file_start_{file_id}")
        )
    kb.add(
        types.InlineKeyboardButton("🗑️ DELETE", callback_data=f"file_delete_{file_id}"),
        types.InlineKeyboardButton("📜 LOGS", callback_data=f"file_logs_{file_id}")
    )
    kb.add(types.InlineKeyboardButton("🔄 REFRESH", callback_data=f"file_open_{file_id}"))
    kb.add(types.InlineKeyboardButton("⬅️ BACK TO FILES", callback_data="menu_files"))
    return kb


def get_process_metrics(pid):
    """Return best-effort CPU percent and RSS memory for a hosted process."""
    if not pid:
        return "—", "—"
    try:
        import psutil
        proc = psutil.Process(int(pid))
        mem_mb = proc.memory_info().rss / (1024 * 1024)
        cpu = proc.cpu_percent(interval=0.05)
        return f"{cpu:.1f}%", f"{mem_mb:.1f} MB"
    except Exception:
        pass
    # Linux fallback without an extra dependency.
    try:
        status = Path(f"/proc/{int(pid)}/status")
        rss = "—"
        for line in status.read_text(errors="ignore").splitlines():
            if line.startswith("VmRSS:"):
                kb = int(line.split()[1])
                rss = f"{kb/1024:.1f} MB"
                break
        return "—", rss
    except Exception:
        return "—", "—"


def recover_stale_hosting_records():
    """Mark stale ONLINE records as CRASHED after a Railway/container restart.
    This never auto-restarts user applications.
    """
    conn = get_db()
    rows = conn.execute("SELECT id, process_id, deployment_id FROM hosting WHERE status='ONLINE'").fetchall()
    changed = 0
    for row in rows:
        pid = row["process_id"]
        alive = False
        if pid:
            try:
                os.kill(int(pid), 0)
                alive = True
            except OSError:
                alive = False
        if not alive:
            conn.execute("UPDATE hosting SET status='CRASHED', stopped_at=? WHERE id=? AND status='ONLINE'",
                         (now_utc(), row["id"]))
            conn.execute("UPDATE deployments SET status='CRASHED', stopped_at=? WHERE id=? AND status='ONLINE'",
                         (now_utc(), row["deployment_id"]))
            changed += 1
    conn.commit(); conn.close()
    if changed:
        logger.warning("Recovered %s stale hosting records after startup", changed)

def show_file_detail(call, file_id):
    """Render a premium single-file dashboard matching the requested UI."""
    user = call.from_user
    conn = get_db()
    file_row = conn.execute(
        "SELECT * FROM files WHERE id=? AND user_id=?", (file_id, user.id)
    ).fetchone()
    if not file_row:
        conn.close()
        main_bot.answer_callback_query(call.id, "⛔ Not your file")
        return

    host = conn.execute(
        "SELECT * FROM hosting WHERE user_id=? AND deployment_id IN "
        "(SELECT id FROM deployments WHERE file_id=?) "
        "ORDER BY started_at DESC LIMIT 1",
        (user.id, file_id)
    ).fetchone()
    dep = None
    if host:
        dep = conn.execute(
            "SELECT start_command, status FROM deployments WHERE id=?",
            (host["deployment_id"],)
        ).fetchone()
    conn.close()

    status = str(host["status"]).upper() if host else (str(dep["status"]).upper() if dep else "STOPPED")
    emoji = "🟢" if status == "ONLINE" else "⚪"
    uptime = "—"
    if host and host["started_at"] and status == "ONLINE":
        try:
            delta = now_utc() - datetime.datetime.fromisoformat(
                str(host["started_at"]).replace("Z", "+00:00")
            )
            uptime = str(delta).split(".")[0]
        except Exception:
            uptime = "—"

    size = int(file_row["size"] or 0)
    size_mb = size / (1024 * 1024)
    command = "Automatic detection"
    cpu_text, mem_text = get_process_metrics(host["process_id"] if host else None)
    restarts = int(host["restart_count"] or 0) if host else 0

    text = (
        f"📁 <b>{html.escape(str(file_row['name']))}</b>\n\n"
        f"📌 <b>File #{file_id}</b>\n"
        f"📊 <b>Status:</b> {emoji} <b>{status.title()}</b>\n"
        f"📦 <b>Size:</b> {size_mb:.2f} MB\n"
        f"🖥️ <b>Memory:</b> {mem_text}\n"
        f"📈 <b>CPU:</b> {cpu_text}\n"
        f"⏱️ <b>Uptime:</b> {uptime}\n"
        f"🔄 <b>Restarts:</b> {restarts}\n"
        "🤖 <b>Startup:</b> ⚡ Automatic\n\n"
        "👇 <b>Choose an action below:</b>"
    )

    kb = file_detail_kb(file_id, host)
    try:
        main_bot.edit_message_text(
            text, reply_markup=kb,
            chat_id=call.message.chat.id, message_id=call.message.message_id
        )
    except Exception:
        main_bot.send_message(call.message.chat.id, text, reply_markup=kb)


def _log_view_kb(file_id: int, offset: int, total: int):
    """Build a compact public-user log viewer navigation keyboard."""
    kb = types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    if offset > 0:
        buttons.append(types.InlineKeyboardButton("⬅️ PREVIOUS", callback_data=f"file_logs_{file_id}_{max(0, offset-3200)}"))
    if offset + 3200 < total:
        buttons.append(types.InlineKeyboardButton("NEXT ➡️", callback_data=f"file_logs_{file_id}_{offset+3200}"))
    if buttons:
        kb.add(*buttons)
    kb.add(
        types.InlineKeyboardButton("🔄 REFRESH LOGS", callback_data=f"file_logs_{file_id}_{offset}"),
        types.InlineKeyboardButton("⬅️ BACK TO FILE", callback_data=f"file_open_{file_id}"),
    )
    return kb


def show_user_file_logs(call, file_id, offset=None):
    """Premium public-user log viewer.

    Important: logs are keyed to the latest deployment for the user's file,
    not only to an existing hosting row. This means a process that crashes
    during its first 0.8s startup window still leaves readable logs for the
    public user.
    """
    user = call.from_user
    try:
        file_id = int(file_id)
    except (TypeError, ValueError):
        main_bot.answer_callback_query(call.id, "⛔ Invalid file")
        return

    conn = get_db()
    file_row = conn.execute(
        "SELECT id, name, user_id, size FROM files WHERE id=? AND user_id=?",
        (file_id, user.id)
    ).fetchone()
    if not file_row:
        conn.close()
        main_bot.answer_callback_query(call.id, "⛔ Not your file")
        return

    # Prefer the latest hosting row, but always fall back to the latest deployment.
    host = conn.execute(
        "SELECT id, deployment_id, status, process_id FROM hosting "
        "WHERE user_id=? AND deployment_id IN (SELECT id FROM deployments WHERE file_id=?) "
        "ORDER BY started_at DESC LIMIT 1",
        (user.id, file_id)
    ).fetchone()
    dep = conn.execute(
        "SELECT id, status, runtime, start_command, created_at FROM deployments "
        "WHERE file_id=? AND user_id=? ORDER BY created_at DESC LIMIT 1",
        (file_id, user.id)
    ).fetchone()
    conn.close()

    deployment_id = host["deployment_id"] if host else (dep["id"] if dep else None)
    if not deployment_id:
        main_bot.answer_callback_query(call.id, "📜 No deployment logs yet")
        main_bot.send_message(
            call.message.chat.id,
            f"📜 <b>LOGS — {html.escape(str(file_row['name']))}</b>\n\n"
            "No deployment has been created for this file yet."
        )
        return

    log_path = SANDBOX_ROOT / str(user.id) / f"deploy_{deployment_id}" / "output.log"
    if not log_path.exists():
        main_bot.answer_callback_query(call.id, "📜 Logs not available yet")
        main_bot.send_message(
            call.message.chat.id,
            f"📜 <b>LOGS — {html.escape(str(file_row['name']))}</b>\n\n"
            f"🆔 Deployment: <code>{html.escape(str(deployment_id))}</code>\n"
            "⏳ The log file has not been created yet. Try Refresh Logs in a moment."
        )
        return

    try:
        raw = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        main_bot.answer_callback_query(call.id, "❌ Cannot read logs")
        main_bot.send_message(
            call.message.chat.id,
            f"❌ <b>LOG READ ERROR</b>\n\n<code>{html.escape(str(exc))[:900]}</code>"
        )
        return

    if not raw.strip():
        main_bot.answer_callback_query(call.id, "📜 Logs are empty")
        main_bot.send_message(
            call.message.chat.id,
            f"📜 <b>LOGS — {html.escape(str(file_row['name']))}</b>\n\n"
            "The deployment has started, but no output has been written yet."
        )
        return

    total = len(raw)
    page_size = 3200
    # Default to the newest page so users immediately see the latest error/output.
    if offset is None:
        offset = max(0, total - page_size)
    try:
        offset = max(0, min(int(offset), max(0, total - 1)))
    except (TypeError, ValueError):
        offset = max(0, total - page_size)
    chunk = raw[offset:offset + page_size]

    status = str(host["status"] if host else (dep["status"] if dep else "UNKNOWN")).upper()
    status_icon = {
        "ONLINE": "🟢", "STOPPED": "⚪", "CRASHED": "🔴",
        "FAILED": "🔴", "APPROVED": "🟡", "PENDING_APPROVAL": "🟡",
    }.get(status, "⚪")

    header = (
        f"📜 <b>LOGS — {html.escape(str(file_row['name']))}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 <b>Deployment:</b> <code>{html.escape(str(deployment_id))}</code>\n"
        f"📊 <b>Status:</b> {status_icon} <b>{html.escape(status.title())}</b>\n"
        f"📄 <b>Log size:</b> {total:,} chars\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Latest stdout / stderr / traceback:</i>\n\n"
        f"<pre>{html.escape(chunk)}</pre>"
    )

    main_bot.answer_callback_query(call.id, "📜 Logs refreshed")
    try:
        main_bot.edit_message_text(
            header,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            reply_markup=_log_view_kb(file_id, offset, total),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )
    except Exception:
        # Callback may originate from a non-editable/old message.
        main_bot.send_message(
            call.message.chat.id,
            header,
            reply_markup=_log_view_kb(file_id, offset, total),
            parse_mode="HTML",
            disable_web_page_preview=True,
        )


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
        text += f"📦 <b>{html.escape(str(f['name']))}</b> — {size:,} bytes\n"
        kb.add(types.InlineKeyboardButton(
            f"📁 {f['name'][:32]}",
            callback_data=f"file_open_{f['id']}"
        ))
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

def _record_referral_from_start(message):
    """Record a referral from /start ref_<user_id> once per referred user."""
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    payload = parts[1].strip()
    if not payload.startswith("ref_"):
        return None
    try:
        referrer_id = int(payload[4:])
    except (TypeError, ValueError):
        return None
    referred_id = int(message.from_user.id)
    if referrer_id <= 0 or referrer_id == referred_id:
        return None
    conn = get_db()
    try:
        existing = conn.execute("SELECT id FROM referrals WHERE referred_id=?", (referred_id,)).fetchone()
        if existing:
            return None
        referrer = conn.execute("SELECT id FROM users WHERE id=? AND banned=0", (referrer_id,)).fetchone()
        if not referrer:
            return None
        conn.execute(
            "INSERT INTO referrals(referrer_id,referred_id,status,reward) VALUES(?,?,?,?)",
            (referrer_id, referred_id, "ACTIVE", 0.0),
        )
        conn.commit()
        return referrer_id
    except Exception:
        logger.exception("Failed to record referral")
        return None
    finally:
        conn.close()

def _referral_stats(user_id):
    conn = get_db()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM referrals WHERE referrer_id=?", (user_id,)
        ).fetchone()[0]
        rewards = conn.execute(
            "SELECT COALESCE(SUM(reward),0) FROM referrals WHERE referrer_id=?", (user_id,)
        ).fetchone()[0]
    finally:
        conn.close()
    return int(count), float(rewards or 0)

def _send_referral_message(message):
    user_id = int(message.from_user.id)
    count, rewards = _referral_stats(user_id)
    try:
        bot_username = main_bot.get_me().username
    except Exception:
        bot_username = None
    link = f"https://t.me/{bot_username}?start=ref_{user_id}" if bot_username else "Referral link temporarily unavailable."
    text = (
        "🎁 <b>VOLT ⚡ REFERRAL</b>\n\n"
        "Invite your friends to VOLT ⚡ HOSTING using your personal referral link.\n\n"
        f"👥 Successful referrals: <b>{count}</b>\n"
        f"💰 Recorded rewards: <b>₹{rewards:.2f}</b>\n\n"
        "🔗 <b>Your Referral Link</b>\n"
        f"<code>{html.escape(link)}</code>\n\n"
        "⚡ Share the link and ask your friend to open the bot through it."
    )
    kb = types.InlineKeyboardMarkup(row_width=1)
    if bot_username:
        kb.add(types.InlineKeyboardButton("🔗 OPEN REFERRAL LINK", url=link))
    kb.add(types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main"))
    main_bot.send_message(message.chat.id, text, reply_markup=kb)

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
    _record_referral_from_start(message)
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
        f"📁 <b>FILE UPLOADED</b>\n\n"
        f"📦 Project: <code>{html.escape(final_path.name)}</code>\n"
        f"📏 Size: <b>{len(downloaded):,} bytes</b>\n"
        "🟢 Status: <b>STORED</b>\n\n"
        "Open <b>📁 MY FILES</b> and select the file to deploy/start it.",
        reply_markup=types.InlineKeyboardMarkup(row_width=2).add(
            types.InlineKeyboardButton("📁 MY FILES", callback_data="menu_files"),
            types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main")
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
        c2.execute("SELECT start_command FROM deployments WHERE id=?", (h["deployment_id"],))
        dep = c2.fetchone()
        conn2.close()
        emoji = "🟢" if h["status"] == "ONLINE" else "🔴"
        text += f"{emoji} <b>{h['id']}</b>\n"
        text += f"Status: {h['status']}\n"
        text += f"Command: {dep['start_command'] if dep else 'N/A'}\n\n"
        if h["status"] == "ONLINE":
            kb.add(types.InlineKeyboardButton(f"⏹️ STOP • {h['id']}", callback_data=f"host_stop_{h['id']}"))
            kb.add(types.InlineKeyboardButton(f"🔄 RESTART • {h['id']}", callback_data=f"host_restart_{h['id']}"))
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
            "SELECT start_command FROM deployments WHERE id=?",
            (h["deployment_id"],)
        ).fetchone()
        conn.close()
        emoji = "🟢" if h["status"] == "ONLINE" else "🔴"
        text += (
            f"{emoji} <b>{h['id']}</b>\n"
            f"Status: <b>{h['status']}</b>\n"
            "Startup: Automatic\n\n"
        )
        if h["status"] == "ONLINE":
            kb.add(types.InlineKeyboardButton(
                f"⏹️ STOP • {h['id']}", callback_data=f"host_stop_{h['id']}"
            ))
            kb.add(types.InlineKeyboardButton(
                f"🔄 RESTART • {h['id']}", callback_data=f"host_restart_{h['id']}"
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

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"🚀 MY HOSTING", "MY HOSTING", "🚀 𝐌𝐘 𝐇𝐎𝐒𝐓𝐈𝐍𝐆"})
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

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"📊 STATISTICS", "STATISTICS", "📈 STATISTICS", "📊 𝐒𝐓𝐀𝐓𝐒"})
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

# ---------- V5 ULTRA REPLY KEYBOARD ----------
@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"🚀 DEPLOYMENT"})
def v5_keyboard_deployment(message):
    if _require_private_user(message): _send_deploy_menu(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"📊 ANALYTICS"})
def v5_keyboard_analytics(message):
    if _require_private_user(message): _send_stats_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"👤 ACCOUNT"})
def v5_keyboard_account(message):
    if _require_private_user(message): _send_account_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"💎 PREMIUM"})
def v5_keyboard_premium(message):
    # Reply-keyboard events are normal Message objects. Do not pass them to
    # the callback-only show_buy(call) function.
    if _require_private_user(message):
        _send_buy_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"📤 UPLOAD FILE"})
def v5_keyboard_upload(message):
    if _require_private_user(message): send_upload_prompt(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"🚀 MY HOSTING"})
def v5_keyboard_hosting(message):
    if _require_private_user(message): _send_my_hosting_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"📁 MY FILES"})
def v5_keyboard_files(message):
    if _require_private_user(message): show_my_files_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"🎫 SUPPORT"})
def v5_keyboard_support(message):
    if _require_private_user(message): _send_support_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"🎁 REFERRAL", "🎁 REFERRALS"})
def v5_keyboard_referral(message):
    if _require_private_user(message): _send_referral_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"ℹ️ ABOUT VOLT", "ABOUT VOLT"})
def v5_keyboard_about(message):
    if _require_private_user(message): _send_about_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"⚡ BOT SPEED"})
def v5_keyboard_speed(message):
    if _require_private_user(message): _send_speed_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"👑 V5 CONTROL CENTER", "👑 CONTROL CENTER"})
def v5_keyboard_admin(message):
    if not _require_private_user(message): return
    if not is_admin(message.from_user.id):
        main_bot.send_message(message.chat.id, "⛔ <b>Admin only.</b>")
        return
    show_admin_panel(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() in {"🏠 MAIN MENU"})
def v5_keyboard_main(message):
    if not _require_private_user(message): return
    main_bot.send_message(message.chat.id, "⚡ <b>VOLT HOSTING V5 ULTRA</b>\n\nChoose an option below:", reply_markup=main_menu_kb(message.from_user.id))

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

def admin_panel_kb():
    """V5 ULTRA real admin control center. Every button maps to a backend action."""
    kb = types.InlineKeyboardMarkup(row_width=2)
    rows = [
        (("⏳ PENDING", "admin_pending"), ("🤖 DEPLOYMENTS", "admin_deployments")),
        (("👥 USERS", "admin_users"), ("💳 PAYMENTS", "admin_payments")),
        (("🎟️ COUPONS", "admin_coupons"), ("💎 PREMIUM", "admin_premium")),
        (("🎁 REFERRALS", "admin_referrals"), ("🛡️ SECURITY", "admin_security")),
        (("📊 STATISTICS", "admin_statistics"), ("🩺 HEALTH", "admin_health")),
        (("📜 AUDIT LOGS", "admin_logs"), ("💾 BACKUPS", "admin_backups")),
        (("⚙️ SETTINGS", "admin_settings"), ("👑 ADMIN PERMS", "admin_perms")),
        (("🖥️ SERVER", "admin_server"), ("🖼️ BRANDING", "admin_branding")),
    ]
    for left, right in rows:
        kb.row(types.InlineKeyboardButton(left[0], callback_data=left[1]),
               types.InlineKeyboardButton(right[0], callback_data=right[1]))
    kb.row(types.InlineKeyboardButton("📄 EXPORT USERS", callback_data="admin_export_users"))
    kb.row(types.InlineKeyboardButton("➕ ADD ADMIN", callback_data="admin_add_admin"))
    kb.row(types.InlineKeyboardButton("🔄 REFRESH", callback_data="admin_refresh"),
           types.InlineKeyboardButton("🏠 MAIN MENU", callback_data="menu_main"))
    return kb

def _admin_display_name(username, fallback):
    value = (username or "").strip().lstrip("@")
    return f"@{html.escape(value)}" if value else fallback

def _admin_panel_text(users, online, pending_deployments, pending_payments):
    return (
        "⚡ <b>VOLT CONTROL CENTER · V5 ULTRA</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Private owner control • V5 ULTRA interface</i>\n\n"
        "📊 <b>LIVE SYSTEM</b>\n"
        "┌────────────────────────┐\n"
        f"│ 👥 Users              <b>{users}</b>\n"
        f"│ 🟢 Online Hosting     <b>{online}</b>\n"
        f"│ 🚀 Deploy Queue       <b>{pending_deployments}</b>\n"
        f"│ 💳 Payment Queue      <b>{pending_payments}</b>\n"
        "└────────────────────────┘\n\n"
        "🛡️ <b>SECURE ADMIN ACCESS</b>\n"
        "Owner &amp; Co-Owner only · ID authorized\n\n"
        "⚡ <b>V5 ULTRA</b> · Clean control interface"
    )

def _admin_nav_kb(refresh_cb):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.row(types.InlineKeyboardButton("🔄 REFRESH", callback_data=refresh_cb),
           types.InlineKeyboardButton("🔙 BACK", callback_data="admin_dashboard"))
    kb.row(types.InlineKeyboardButton("🏠 MAIN", callback_data="menu_main"))
    return kb

def _admin_simple_page(call, title, body, refresh_cb):
    main_bot.answer_callback_query(call.id)
    main_bot.edit_message_text(f"{title}\n\n{body}", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=_admin_nav_kb(refresh_cb))

def _admin_count(conn, sql, args=()):
    return conn.execute(sql, args).fetchone()[0]

def admin_pending_page(call):
    """Show an actionable pending queue so approvals/rejections are always visible."""
    if not is_admin(call.from_user.id):
        main_bot.answer_callback_query(call.id, "⛔ Admin access required")
        return
    conn = get_db()
    try:
        deployments = conn.execute(
            "SELECT id, user_id, file_id, status, created_at FROM deployments "
            "WHERE status='PENDING_APPROVAL' ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
        payments = conn.execute(
            "SELECT id, user_id, plan_id, amount, utr, status, created_at FROM payments "
            "WHERE status='PENDING' ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    finally:
        conn.close()

    kb = types.InlineKeyboardMarkup(row_width=2)
    blocks = []
    for r in deployments:
        fname = html.escape(get_file_name(r["file_id"]))
        blocks.append(
            f"🚀 <b>DEPLOYMENT {html.escape(str(r['id']))}</b>\n"
            f"👤 User: <code>{r['user_id']}</code>\n"
            f"📁 File: <code>{fname}</code>\n"
            f"🕐 {fmt_ts(r['created_at'])}"
        )
        kb.add(
            types.InlineKeyboardButton("✅ APPROVE", callback_data=f"admin_deploy_approve_{r['id']}"),
            types.InlineKeyboardButton("❌ REJECT", callback_data=f"admin_deploy_reject_{r['id']}")
        )

    for r in payments:
        blocks.append(
            f"💳 <b>PAYMENT {html.escape(str(r['id']))}</b>\n"
            f"👤 User: <code>{r['user_id']}</code>\n"
            f"📦 Plan: {html.escape(str(r['plan_id']))} · ₹{html.escape(str(r['amount']))}\n"
            f"🔢 UTR: <code>{html.escape(str(r['utr']))}</code>\n"
            f"🕐 {fmt_ts(r['created_at'])}"
        )
        kb.add(
            types.InlineKeyboardButton("✅ APPROVE PAYMENT", callback_data=f"admin_pay_approve_{r['id']}"),
            types.InlineKeyboardButton("❌ REJECT PAYMENT", callback_data=f"admin_pay_reject_{r['id']}")
        )

    body = "\n\n━━━━━━━━━━━━━━━━━━━━\n\n".join(blocks) if blocks else "<i>Nothing is waiting for approval.</i>\n\n✅ All clear."
    kb.add(
        types.InlineKeyboardButton("🚀 DEPLOYMENTS", callback_data="admin_deployments"),
        types.InlineKeyboardButton("💳 PAYMENTS", callback_data="admin_payments"),
    )
    kb.add(
        types.InlineKeyboardButton("🔄 REFRESH", callback_data="admin_pending"),
        types.InlineKeyboardButton("◀️ ADMIN CENTER", callback_data="admin_dashboard"),
    )
    text = "⏳ <b>PENDING APPROVALS</b>\n━━━━━━━━━━━━━━━━━━━━\n\n" + body
    try:
        main_bot.edit_message_text(text, chat_id=call.message.chat.id, message_id=call.message.message_id,
                                   reply_markup=kb, parse_mode="HTML")
    except Exception:
        main_bot.send_message(call.message.chat.id, text, reply_markup=kb, parse_mode="HTML")
    main_bot.answer_callback_query(call.id, "⏳ Pending approvals loaded")

def admin_statistics_page(call):
    conn=get_db()
    try:
        vals={
          'Users':_admin_count(conn,'SELECT COUNT(*) FROM users'),
          'Files':_admin_count(conn,'SELECT COUNT(*) FROM files'),
          'Deployments':_admin_count(conn,'SELECT COUNT(*) FROM deployments'),
          'Online':_admin_count(conn,"SELECT COUNT(*) FROM hosting WHERE status='ONLINE'"),
          'Premium':_admin_count(conn,"SELECT COUNT(*) FROM users WHERE is_premium=1"),
          'Payments':_admin_count(conn,'SELECT COUNT(*) FROM payments'),
        }
    finally: conn.close()
    _admin_simple_page(call,"📊 <b>STATISTICS</b>","\n".join(f"{k}: <b>{v}</b>" for k,v in vals.items()),"admin_statistics")

def admin_health_page(call):
    conn=get_db()
    try: conn.execute('SELECT 1'); db='🟢 ONLINE'
    except Exception: db='🔴 ERROR'
    finally: conn.close()
    import shutil as _shutil
    total,used,free=_shutil.disk_usage(SANDBOX_ROOT)
    _admin_simple_page(call,"🩺 <b>SYSTEM HEALTH</b>",f"Telegram Bot: 🟢\nDatabase: {db}\nDisk Free: <b>{free/1024**3:.2f} GB</b>\nSandbox: <code>{html.escape(str(SANDBOX_ROOT))}</code>","admin_health")

def admin_server_page(call):
    try:
        import shutil as _shutil
        total,used,free=_shutil.disk_usage(SANDBOX_ROOT)
        body=f"Sandbox: <code>{html.escape(str(SANDBOX_ROOT))}</code>\nDisk: <b>{used/1024**3:.2f} / {total/1024**3:.2f} GB</b>\nFree: <b>{free/1024**3:.2f} GB</b>\nPython: <code>{html.escape(sys.version.split()[0])}</code>"
    except Exception as exc: body=f"Server metrics unavailable: <code>{html.escape(str(exc))}</code>"
    _admin_simple_page(call,"🖥️ <b>SERVER</b>",body,"admin_server")

def admin_security_page(call):
    conn=get_db()
    try: logs=_admin_count(conn,"SELECT COUNT(*) FROM audit_logs WHERE action LIKE '%SECURITY%' OR action LIKE '%REJECT%'")
    finally: conn.close()
    _admin_simple_page(call,"🛡️ <b>SECURITY</b>",f"Security/rejection audit events: <b>{logs}</b>\n\nControl-plane secrets are not passed to hosted user processes.","admin_security")

def admin_premium_page(call):
    conn=get_db()
    try: active=_admin_count(conn,"SELECT COUNT(*) FROM user_subscriptions WHERE status='ACTIVE'"); plans=conn.execute('SELECT name,price,duration_days FROM plans ORDER BY duration_days').fetchall()
    finally: conn.close()
    body=f"Active subscriptions: <b>{active}</b>\n\n"+"\n".join(f"💎 {html.escape(str(r['name']))}: ₹{r['price']} / {r['duration_days']} days" for r in plans)
    _admin_simple_page(call,"💎 <b>PREMIUM</b>",body,"admin_premium")

def admin_coupons_page(call):
    conn=get_db()
    try: rows=conn.execute('SELECT code,discount_percent,max_uses,used_count,enabled FROM coupons ORDER BY created_at DESC LIMIT 20').fetchall()
    finally: conn.close()
    body=("No coupons created yet." if not rows else "\n".join(f"🎟️ <code>{html.escape(str(r['code']))}</code> — {r['discount_percent']}% · {r['used_count']}/{r['max_uses'] or '∞'} · {'ON' if r['enabled'] else 'OFF'}" for r in rows))
    body += "\n\nUse /addcoupon CODE PERCENT MAX_USES to create one."
    _admin_simple_page(call,"🎟️ <b>COUPONS</b>",body,"admin_coupons")

def admin_referrals_page(call):
    conn=get_db()
    try: total=_admin_count(conn,'SELECT COUNT(*) FROM referrals'); rewards=conn.execute('SELECT COALESCE(SUM(reward),0) FROM referrals').fetchone()[0]
    finally: conn.close()
    _admin_simple_page(call,"🎁 <b>REFERRALS</b>",f"Referral records: <b>{total}</b>\nRewards recorded: <b>₹{float(rewards):.2f}</b>","admin_referrals")

def admin_settings_page(call):
    conn=get_db()
    try: rows=conn.execute('SELECT key,value FROM settings ORDER BY key').fetchall()
    finally: conn.close()
    body='\n'.join(f"⚙️ <code>{html.escape(str(r['key']))}</code> = <code>{html.escape(str(r['value']))}</code>" for r in rows) or 'No custom settings stored.'
    _admin_simple_page(call,"⚙️ <b>SETTINGS</b>",body,"admin_settings")

def admin_perms_page(call):
    conn=get_db()
    try: rows=conn.execute('SELECT user_id,role,enabled FROM admin_users ORDER BY created_at').fetchall()
    finally: conn.close()
    body='\n'.join(f"👑 <code>{r['user_id']}</code> — {html.escape(str(r['role']))} — {'ON' if r['enabled'] else 'OFF'}" for r in rows) or 'Owner/Co-Owner are configured through secure environment variables.'
    body += "\n\nUse /addadmin TELEGRAM_ID to add an admin."
    _admin_simple_page(call,"👑 <b>ADMIN PERMISSIONS</b>",body,"admin_perms")

def admin_backups_page(call):
    backup_dir=Path(os.environ.get('BACKUP_DIR','/data/backups' if Path('/data').exists() else './backups'))
    backup_dir.mkdir(parents=True,exist_ok=True)
    files=sorted(backup_dir.glob('*.db'), key=lambda x:x.stat().st_mtime, reverse=True)[:10]
    body='\n'.join(f"💾 {html.escape(f.name)} — {f.stat().st_size/1024:.1f} KB" for f in files) or 'No database backups yet.'
    body += "\n\nOwner/Co-Owner can create a fresh backup with the button below."
    kb=_admin_nav_kb('admin_backups')
    kb.row(types.InlineKeyboardButton('💾 CREATE BACKUP',callback_data='admin_create_backup'))
    main_bot.answer_callback_query(call.id)
    main_bot.edit_message_text('💾 <b>BACKUPS</b>\n\n'+body,chat_id=call.message.chat.id,message_id=call.message.message_id,parse_mode='HTML',reply_markup=kb)

def admin_branding_page(call):
    _admin_simple_page(call,'🖼️ <b>BRANDING</b>',f'Brand: <b>{html.escape(BRAND)}</b>\nVersion: <b>V13.00.000 · V6 ULTRA</b>\nUI: <b>2-column Admin Control Center</b>', 'admin_branding')

def show_admin_panel(message_or_call):
    user = message_or_call.from_user
    if not is_admin(user.id):
        if hasattr(message_or_call, "id"):
            try:
                main_bot.answer_callback_query(message_or_call.id, "⛔ Admin access required")
            except Exception:
                pass
        else:
            main_bot.send_message(message_or_call.chat.id, "⛔ <b>Admin access required.</b>")
        return

    conn = get_db()
    try:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) AS cnt FROM deployments WHERE status='PENDING_APPROVAL'")
        pending_deployments = c.fetchone()["cnt"]
        c.execute("SELECT COUNT(*) AS cnt FROM payments WHERE status='PENDING'")
        pending_payments = c.fetchone()["cnt"]
        c.execute("SELECT COUNT(*) AS cnt FROM users")
        users = c.fetchone()["cnt"]
        c.execute("SELECT COUNT(*) AS cnt FROM hosting WHERE status='ONLINE'")
        online = c.fetchone()["cnt"]
    finally:
        conn.close()

    text = _admin_panel_text(users, online, pending_deployments, pending_payments)
    kb = admin_panel_kb()
    try:
        if hasattr(message_or_call, "message"):
            main_bot.edit_message_text(
                text, reply_markup=kb,
                chat_id=message_or_call.message.chat.id,
                message_id=message_or_call.message.message_id,
                parse_mode="HTML",
            )
        else:
            main_bot.send_message(message_or_call.chat.id, text, reply_markup=kb, parse_mode="HTML")
    except Exception as exc:
        logger.exception("Admin panel render failed: %s", exc)
        if hasattr(message_or_call, "message"):
            main_bot.send_message(message_or_call.message.chat.id, text, reply_markup=kb, parse_mode="HTML")
        else:
            raise

def show_admin_logs(call):

    """Show private audit logs to admins only; never expose them to normal users."""
    if not is_admin(call.from_user.id):
        main_bot.answer_callback_query(call.id, "⛔ Unauthorized")
        return

    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, admin_id, admin_name, action, details, timestamp FROM audit_logs ORDER BY id DESC LIMIT 10")
    rows = c.fetchall()
    conn.close()

    lines = ["📜 <b>VOLT ADMIN AUDIT LOGS</b>", "", "Latest 10 admin/system actions:", ""]
    if not rows:
        lines.append("No audit logs found yet.")
    else:
        for r in rows:
            admin = html.escape(str(r["admin_name"] or r["admin_id"]))
            details = html.escape(str(r["details"] or "—"))
            if len(details) > 180:
                details = details[:177] + "..."
            lines.append(
                f"<b>#{r['id']}</b> • <b>{html.escape(str(r['action']))}</b>\n"
                f"👤 {admin} (<code>{r['admin_id']}</code>)\n"
                f"📝 {details}\n"
                f"🕐 {fmt_ts(r['timestamp'])}\n"
            )

    text = "\n".join(lines)
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔄 REFRESH LOGS", callback_data="admin_logs"),
        types.InlineKeyboardButton("◀️ ADMIN PANEL", callback_data="admin_dashboard"),
    )
    try:
        main_bot.edit_message_text(text, reply_markup=kb, chat_id=call.message.chat.id, message_id=call.message.message_id)
    except Exception:
        # Avoid the generic public-looking "button failed" message when an edit is impossible.
        main_bot.send_message(call.message.chat.id, text, reply_markup=kb)
    main_bot.answer_callback_query(call.id)

def show_admin_queue(call, kind):
    """Render pending queues privately with direct approve/reject controls."""
    if not is_admin(call.from_user.id):
        main_bot.answer_callback_query(call.id, "⛔ Admin access required")
        return

    conn = get_db()
    try:
        c = conn.cursor()
        if kind == "deployments":
            c.execute(
                "SELECT id, user_id, file_id, status, created_at "
                "FROM deployments WHERE status='PENDING_APPROVAL' "
                "ORDER BY created_at DESC LIMIT 20"
            )
            rows = c.fetchall()
            title = "🚀 <b>PENDING DEPLOYMENTS</b>"
        else:
            c.execute(
                "SELECT id, user_id, plan_id, amount, utr, status, created_at "
                "FROM payments WHERE status='PENDING' "
                "ORDER BY created_at DESC LIMIT 20"
            )
            rows = c.fetchall()
            title = "💳 <b>PENDING PAYMENTS</b>"
    finally:
        conn.close()

    kb = types.InlineKeyboardMarkup(row_width=2)
    if not rows:
        body = "<i>Nothing is waiting for approval right now.</i>\n\n✅ All clear."
    else:
        blocks = []
        for r in rows:
            if kind == "deployments":
                blocks.append(
                    f"🚀 <b>Deployment #{html.escape(str(r['id']))}</b>\n"
                    f"👤 User: <code>{r['user_id']}</code>\n"
                    f"📁 File: <code>{r['file_id']}</code>\n"
                    f"🕐 {fmt_ts(r['created_at'])}"
                )
                kb.add(
                    types.InlineKeyboardButton("✅ Approve", callback_data=f"admin_deploy_approve_{r['id']}"),
                    types.InlineKeyboardButton("❌ Reject", callback_data=f"admin_deploy_reject_{r['id']}"),
                )
            else:
                blocks.append(
                    f"💳 <b>Payment #{html.escape(str(r['id']))}</b>\n"
                    f"👤 User: <code>{r['user_id']}</code>\n"
                    f"📦 Plan: {html.escape(str(r['plan_id']))}\n"
                    f"💰 Amount: ₹{html.escape(str(r['amount']))}\n"
                    f"🔢 UTR: <code>{html.escape(str(r['utr']))}</code>\n"
                    f"🕐 {fmt_ts(r['created_at'])}"
                )
                kb.add(
                    types.InlineKeyboardButton("✅ Approve", callback_data=f"admin_pay_approve_{r['id']}"),
                    types.InlineKeyboardButton("❌ Reject", callback_data=f"admin_pay_reject_{r['id']}"),
                )
        body = "\n\n━━━━━━━━━━━━━━━━━━━━\n\n".join(blocks)

    kb.add(
        types.InlineKeyboardButton("🔄 Refresh", callback_data=f"admin_{kind}"),
        types.InlineKeyboardButton("◀️ Admin Center", callback_data="admin_dashboard"),
    )
    try:
        main_bot.edit_message_text(
            title + "\n━━━━━━━━━━━━━━━━━━━━\n\n" + body,
            reply_markup=kb,
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.exception("Admin queue render failed: %s", exc)
        main_bot.send_message(
            call.message.chat.id,
            title + "\n━━━━━━━━━━━━━━━━━━━━\n\n" + body,
            reply_markup=kb,
            parse_mode="HTML",
        )
    main_bot.answer_callback_query(call.id)


# V5 ULTRA reply-keyboard aliases. These call the existing production handlers,
# so the new UI does not duplicate deployment/payment/database logic.
@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() == "💎 𝐁𝐔𝐘 𝐏𝐑𝐄𝐌𝐈𝐔𝐌")
def v5_buy_premium_alias(message):
    if _require_private_user(message):
        _send_buy_message(message)

@main_bot.message_handler(func=lambda m: (m.text or "").strip().upper() == "📜 𝐕𝐈𝐄𝐖 𝐋𝐎𝐆𝐒")
def v5_logs_alias(message):
    if not _require_private_user(message):
        return
    # Logs remain private: show the user's own file list first.
    show_my_files_message(message)

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

        elif data == "menu_referral":
            main_bot.answer_callback_query(call.id)
            _send_referral_message(call.message)

        elif data == "menu_account":
            main_bot.answer_callback_query(call.id)
            show_account(call)

        elif data == "menu_about":
            main_bot.answer_callback_query(call.id)
            show_about(call)

        elif data == "menu_speed":
            main_bot.answer_callback_query(call.id)
            show_bot_speed(call.message)

        elif data == "admin_dashboard":
            if not is_admin(user.id):
                main_bot.answer_callback_query(call.id, "⛔ Unauthorized")
                return
            main_bot.answer_callback_query(call.id)
            show_admin_panel(call)

        elif data == "admin_refresh":
            if not is_admin(user.id):
                main_bot.answer_callback_query(call.id, "⛔ Unauthorized")
                return
            main_bot.answer_callback_query(call.id, "🔄 Refreshed")
            show_admin_panel(call)

        elif data == "admin_users":
            if not is_admin(user.id):
                main_bot.answer_callback_query(call.id, "⛔ Unauthorized")
                return
            conn = get_db()
            try:
                total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                active = conn.execute("SELECT COUNT(*) FROM users WHERE is_banned=0").fetchone()[0]
                rows = conn.execute("SELECT id, username, first_name, is_banned, last_active FROM users ORDER BY last_active DESC LIMIT 12").fetchall()
            finally:
                conn.close()
            kb = types.InlineKeyboardMarkup(row_width=2)
            body = [f"👥 <b>USERS · V6 ULTRA</b>", f"Total: <b>{total}</b>  •  Active: <b>{active}</b>", ""]
            for r in rows:
                name = html.escape((r["first_name"] or r["username"] or str(r["id"]))[:24])
                state = "🚫 BANNED" if r["is_banned"] else "🟢 ACTIVE"
                body.append(f"👤 <b>{name}</b> · <code>{r['id']}</code> · {state}")
                if int(r["id"]) not in (OWNER_ID, CO_OWNER_ID):
                    if r["is_banned"]:
                        kb.add(types.InlineKeyboardButton(f"♻️ Unban {r['id']}", callback_data=f"admin_unban_{r['id']}"))
                    else:
                        kb.add(types.InlineKeyboardButton(f"🚫 Ban {r['id']}", callback_data=f"admin_ban_{r['id']}"))
            kb.add(types.InlineKeyboardButton("🔄 REFRESH", callback_data="admin_users"), types.InlineKeyboardButton("↩️ BACK", callback_data="admin_dashboard"))
            main_bot.answer_callback_query(call.id)
            try:
                main_bot.edit_message_text("\n".join(body), call.message.chat.id, call.message.message_id, reply_markup=kb)
            except Exception:
                main_bot.send_message(call.message.chat.id, "\n".join(body), reply_markup=kb)

        elif data.startswith("admin_ban_") or data.startswith("admin_unban_"):
            if not is_admin(user.id):
                main_bot.answer_callback_query(call.id, "⛔ Unauthorized")
                return
            target = int(data.rsplit("_", 1)[1])
            if target in (OWNER_ID, CO_OWNER_ID):
                main_bot.answer_callback_query(call.id, "⛔ Protected admin", show_alert=True)
                return
            banned = data.startswith("admin_ban_")
            conn = get_db()
            conn.execute("UPDATE users SET is_banned=? WHERE id=?", (1 if banned else 0, target))
            conn.commit(); conn.close()
            log_audit(user.id, user.first_name or "Admin", "USER_BAN" if banned else "USER_UNBAN", str(target))
            main_bot.answer_callback_query(call.id, "🚫 Banned" if banned else "♻️ Unbanned")
            if banned:
                # Stop active hosting safely when access is revoked.
                conn = get_db()
                hosts = conn.execute("SELECT id, process_id, deployment_id FROM hosting WHERE user_id=? AND status='ONLINE'", (target,)).fetchall()
                for h in hosts:
                    try:
                        if h["process_id"]:
                            os.killpg(os.getpgid(h["process_id"]), signal.SIGKILL) if os.name == "posix" else os.kill(h["process_id"], signal.SIGKILL)
                    except Exception:
                        pass
                    conn.execute("UPDATE hosting SET status='STOPPED', stopped_at=? WHERE id=?", (now_utc(), h["id"]))
                    conn.execute("UPDATE deployments SET status='STOPPED', stopped_at=? WHERE id=?", (now_utc(), h["deployment_id"]))
                conn.commit(); conn.close()
            # Re-render the users page.
            main_callback(types.SimpleNamespace(from_user=user, message=call.message, data="admin_users", id=call.id))

        elif data == "admin_files":
            if not is_admin(user.id):
                main_bot.answer_callback_query(call.id, "⛔ Unauthorized")
                return
            conn = get_db()
            try:
                total_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            finally:
                conn.close()
            main_bot.answer_callback_query(call.id)
            main_bot.send_message(
                call.message.chat.id,
                f"📁 <b>FILES · V5 ULTRA</b>\n\nStored files: <b>{total_files}</b>",
                reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("↩️ BACK", callback_data="admin_dashboard")),
            )

        elif data == "admin_pending":
            if not is_admin(user.id): main_bot.answer_callback_query(call.id,"⛔ Unauthorized"); return
            admin_pending_page(call)

        elif data == "admin_statistics":
            if not is_admin(user.id): main_bot.answer_callback_query(call.id,"⛔ Unauthorized"); return
            admin_statistics_page(call)

        elif data == "admin_health":
            if not is_admin(user.id): main_bot.answer_callback_query(call.id,"⛔ Unauthorized"); return
            admin_health_page(call)

        elif data == "admin_server":
            if not is_admin(user.id): main_bot.answer_callback_query(call.id,"⛔ Unauthorized"); return
            admin_server_page(call)

        elif data == "admin_security":
            if not is_admin(user.id): main_bot.answer_callback_query(call.id,"⛔ Unauthorized"); return
            admin_security_page(call)

        elif data == "admin_premium":
            if not is_admin(user.id): main_bot.answer_callback_query(call.id,"⛔ Unauthorized"); return
            admin_premium_page(call)

        elif data == "admin_coupons":
            if not is_admin(user.id): main_bot.answer_callback_query(call.id,"⛔ Unauthorized"); return
            admin_coupons_page(call)

        elif data == "admin_referrals":
            if not is_admin(user.id): main_bot.answer_callback_query(call.id,"⛔ Unauthorized"); return
            admin_referrals_page(call)

        elif data == "admin_settings":
            if not is_admin(user.id): main_bot.answer_callback_query(call.id,"⛔ Unauthorized"); return
            admin_settings_page(call)

        elif data == "admin_perms":
            if not is_admin(user.id): main_bot.answer_callback_query(call.id,"⛔ Unauthorized"); return
            admin_perms_page(call)

        elif data == "admin_backups":
            if not is_admin(user.id): main_bot.answer_callback_query(call.id,"⛔ Unauthorized"); return
            admin_backups_page(call)

        elif data == "admin_branding":
            if not is_admin(user.id): main_bot.answer_callback_query(call.id,"⛔ Unauthorized"); return
            admin_branding_page(call)

        elif data == "admin_create_backup":
            if not is_admin(user.id): main_bot.answer_callback_query(call.id,"⛔ Unauthorized"); return
            try:
                import shutil as _shutil
                backup_dir=Path(os.environ.get('BACKUP_DIR','/data/backups' if Path('/data').exists() else './backups')); backup_dir.mkdir(parents=True,exist_ok=True)
                target=backup_dir/f"volt_{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M%S')}.db"
                conn=get_db(); conn.commit(); _shutil.copy2(DB_PATH,target); conn.close()
                log_audit(user.id, user.first_name or 'Admin', 'BACKUP_CREATED', str(target))
                main_bot.answer_callback_query(call.id,'💾 Backup created')
                admin_backups_page(call)
            except Exception as exc:
                logger.exception('backup failed'); main_bot.answer_callback_query(call.id,'❌ Backup failed',show_alert=True)

        elif data == "admin_export_users":
            if not is_admin(user.id): main_bot.answer_callback_query(call.id,"⛔ Unauthorized"); return
            conn=get_db()
            try: rows=conn.execute('SELECT id,username,first_name,is_premium,is_banned FROM users ORDER BY id').fetchall()
            finally: conn.close()
            export_path=Path('/tmp')/f"volt_users_{user.id}.csv"
            import csv as _csv
            with open(export_path,'w',newline='',encoding='utf-8') as fh:
                w=_csv.writer(fh); w.writerow(['id','username','first_name','is_premium','banned']); w.writerows(rows)
            with open(export_path,'rb') as fh: main_bot.send_document(call.message.chat.id,fh,caption='📄 VOLT USERS EXPORT')
            main_bot.answer_callback_query(call.id,'📄 Export sent')

        elif data == "admin_add_admin":
            if not is_admin(user.id): main_bot.answer_callback_query(call.id,"⛔ Unauthorized"); return
            main_bot.answer_callback_query(call.id)
            main_bot.send_message(call.message.chat.id,'➕ <b>ADD ADMIN</b>\n\nSend <code>/addadmin TELEGRAM_ID</code>.')

        elif data == "admin_logs":
            if not is_admin(user.id):
                main_bot.answer_callback_query(call.id, "⛔ Unauthorized")
                return
            show_admin_logs(call)

        elif data == "admin_deployments":
            show_admin_queue(call, "deployments")

        elif data == "admin_payments":
            show_admin_queue(call, "payments")

        elif data == "menu_hosting":
            main_bot.answer_callback_query(call.id)
            show_my_hosting(call)

        elif data.startswith("file_open_"):
            file_id = data[len("file_open_"):]
            if not file_id.isdigit() or not _validate_user_file_access(user.id, int(file_id)):
                main_bot.answer_callback_query(call.id, "⛔ Not your file")
                return
            main_bot.answer_callback_query(call.id)
            show_file_detail(call, int(file_id))

        elif data.startswith("file_logs_"):
            parts = data.split("_")
            file_id = parts[2] if len(parts) > 2 else ""
            offset = parts[3] if len(parts) > 3 and parts[3].isdigit() else None
            if not file_id.isdigit() or not _validate_user_file_access(user.id, int(file_id)):
                main_bot.answer_callback_query(call.id, "⛔ Not your file")
                return
            show_user_file_logs(call, int(file_id), int(offset) if offset is not None else None)

        elif data.startswith("file_start_"):
            file_id = data[len("file_start_"):]
            if not file_id.isdigit() or not _validate_user_file_access(user.id, int(file_id)):
                main_bot.answer_callback_query(call.id, "⛔ Not your file")
                return
            conn = get_db()
            dep = conn.execute(
                "SELECT id, status FROM deployments WHERE file_id=? AND user_id=? "
                "ORDER BY created_at DESC LIMIT 1",
                (int(file_id), user.id)
            ).fetchone()
            conn.close()
            if dep and dep["status"] in ("APPROVED", "STOPPED", "CRASHED"):
                main_bot.answer_callback_query(call.id, "▶️ Starting...")
                threading.Thread(target=start_hosting, args=(dep["id"],), daemon=True).start()
                time.sleep(0.4)
                show_file_detail(call, int(file_id))
            else:
                main_bot.answer_callback_query(call.id, "⚡ Creating automatic deployment...")
                try:
                    _create_deployment_request(user, int(file_id))
                except Exception as e:
                    main_bot.send_message(call.message.chat.id, f"❌ <b>START FAILED</b>\n\n<code>{html.escape(str(e)[:900])}</code>")

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
                "Please try again or use <b>🔄 Refresh</b>."
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
            kb.add(types.InlineKeyboardButton(f"📁 {f['name'][:32]}", callback_data=f"file_open_{f['id']}"))
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

def get_file_name(file_id: int) -> str:
    """Resolve a stored file display name safely for admin/user notifications."""
    try:
        conn = get_db()
        row = conn.execute("SELECT name FROM files WHERE id=?", (int(file_id),)).fetchone()
        conn.close()
        if row and row["name"]:
            return str(row["name"])
    except Exception:
        logger.exception("Failed to resolve file name for file_id=%s", file_id)
    return f"file_{int(file_id)}"

def _infer_start_command(file_path: Path, original_name: str) -> str:
    """Automatically choose a safe start command; users do not enter startup settings."""
    ext = file_path.suffix.lower()
    name = Path(original_name).name
    if ext == ".py":
        return f"python3 {shlex.quote(name)}"
    if ext == ".js":
        return f"node {shlex.quote(name)}"
    if ext == ".sh":
        return f"bash {shlex.quote(name)}"
    if ext == ".java":
        # Java source is compiled automatically by start_hosting before launch.
        return f"java {shlex.quote(Path(name).stem)}"
    if ext == ".c":
        return f"./volt_app"
    if ext == ".cpp":
        return f"./volt_app"
    if ext in {".html", ".txt", ".json", ".css"}:
        # Static/text projects are served automatically on Railway's PORT.
        return "python3 -m http.server $PORT"
    if ext == ".zip":
        # Inspect the archive before approval so valid projects reach the admin queue.
        import zipfile
        try:
            with zipfile.ZipFile(file_path) as zf:
                files = [n for n in zf.namelist() if not n.endswith("/")]
                normalized = {Path(n).name.lower(): n for n in files}
                if "package.json" in normalized:
                    try:
                        pkg = json.loads(zf.read(normalized["package.json"]).decode("utf-8", errors="replace"))
                        scripts = pkg.get("scripts") or {}
                        if scripts.get("start"):
                            return "npm start"
                        if pkg.get("main"):
                            return f"node {shlex.quote(str(pkg['main']))}"
                    except Exception:
                        pass
                preferred = ["bot.py", "main.py", "app.py", "run.py", "index.py", "server.py",
                             "bot.js", "main.js", "app.js", "index.js", "server.js", "start.sh"]
                for candidate in preferred:
                    if candidate in normalized:
                        chosen = normalized[candidate]
                        if chosen.lower().endswith(".py"):
                            return f"python3 {shlex.quote(chosen)}"
                        if chosen.lower().endswith(".js"):
                            return f"node {shlex.quote(chosen)}"
                        if chosen.lower().endswith(".sh"):
                            return f"bash {shlex.quote(chosen)}"
                code_files = [n for n in files if Path(n).suffix.lower() in {".py", ".js", ".sh"}]
                if len(code_files) == 1:
                    chosen = code_files[0]
                    if chosen.lower().endswith(".py"):
                        return f"python3 {shlex.quote(chosen)}"
                    if chosen.lower().endswith(".js"):
                        return f"node {shlex.quote(chosen)}"
                    return f"bash {shlex.quote(chosen)}"
        except Exception:
            logger.exception("ZIP startup inspection failed for %s", file_path)
        raise ValueError("Could not automatically detect a startup file inside the ZIP. Include bot.py/main.py, package.json, or one Python/Node/Shell entry file.")
    raise ValueError(f"Automatic startup is not supported for {ext or 'this file type'}.")


def _notify_deployment_admins(deploy_id: str) -> int:
    """Send an actionable deployment approval notification to every configured admin.

    Returns the number of admins successfully notified. Failures are logged instead of
    being silently swallowed, while the pending queue remains available as a fallback.
    """
    conn = get_db()
    row = conn.execute(
        "SELECT d.*, f.name AS file_name, f.size AS file_size FROM deployments d "
        "LEFT JOIN files f ON f.id=d.file_id WHERE d.id=?", (deploy_id,)
    ).fetchone()
    conn.close()
    if not row:
        logger.error("Cannot notify admins: deployment %s not found", deploy_id)
        return 0

    username = "No Username"
    try:
        conn = get_db()
        u = conn.execute("SELECT username, first_name FROM users WHERE id=?", (row["user_id"],)).fetchone()
        conn.close()
        if u and u["username"]:
            username = str(u["username"])
    except Exception:
        logger.exception("Could not resolve deployment user for %s", deploy_id)

    admin_text = (
        "🚨 <b>NEW DEPLOYMENT — APPROVAL REQUIRED</b>\n\n"
        f"👤 User: @{html.escape(username)}\n"
        f"🆔 User ID: <code>{row['user_id']}</code>\n"
        f"📁 File: <code>{html.escape(str(row['file_name'] or get_file_name(row['file_id'])))}</code>\n"
        f"📦 File ID: <code>{row['file_id']}</code>\n"
        f"🆔 Deployment: <code>{html.escape(str(deploy_id))}</code>\n"
        "🤖 Startup: <b>Automatic detection</b>\n"
        "🟡 Status: <b>PENDING APPROVAL</b>\n\n"
        "👇 <b>Choose an action:</b>"
    )
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("✅ APPROVE", callback_data=f"admin_deploy_approve_{deploy_id}"),
        types.InlineKeyboardButton("❌ REJECT", callback_data=f"admin_deploy_reject_{deploy_id}"),
    )
    kb.add(
        types.InlineKeyboardButton("🔍 DETAILS", callback_data=f"admin_deploy_details_{deploy_id}"),
        types.InlineKeyboardButton("📁 VIEW FILE", callback_data=f"admin_deploy_file_{deploy_id}"),
    )
    kb.add(types.InlineKeyboardButton("⏳ PENDING QUEUE", callback_data="admin_pending"))

    notified = 0
    admin_ids = []
    for aid in (OWNER_ID, CO_OWNER_ID):
        try:
            aid = int(aid)
        except (TypeError, ValueError):
            continue
        if aid > 0 and aid not in admin_ids:
            admin_ids.append(aid)

    if not admin_ids:
        logger.error("No valid OWNER_ID/CO_OWNER_ID configured; deployment %s cannot notify admins", deploy_id)
        return 0

    for admin_id in admin_ids:
        try:
            main_bot.send_message(admin_id, admin_text, reply_markup=kb, parse_mode="HTML")
            notified += 1
            logger.info("Deployment approval notification sent: deployment=%s admin=%s", deploy_id, admin_id)
        except Exception as exc:
            logger.error("Deployment approval notification FAILED: deployment=%s admin=%s error=%s",
                         deploy_id, admin_id, exc)
    return notified

def _create_deployment_request(user, file_id):
    """Create a deployment request using automatic runtime detection."""
    conn = get_db()
    file_row = conn.execute(
        "SELECT * FROM files WHERE id=? AND user_id=?", (int(file_id), user.id)
    ).fetchone()
    conn.close()
    if not file_row:
        raise ValueError("File not found.")

    command = _infer_start_command(Path(file_row["path"]), file_row["name"])
    # Validate only the executable/arguments that can be safely validated here.
    if "$PORT" in command:
        command = command.replace("$PORT", str(int(os.environ.get("PORT", "8080"))))
    parts = shlex.split(command)
    _validate_command(parts)

    deploy_id = generate_id("VOLT-DEP")
    conn = get_db()
    c = conn.cursor()
    c.execute(
        "INSERT INTO deployments (id, user_id, file_id, runtime, start_command, port, status) VALUES (?,?,?,?,?,?,?)",
        (deploy_id, user.id, file_id, "auto", command, None, "PENDING_APPROVAL")
    )
    conn.commit()
    conn.close()
    log_audit(user.id, user.first_name or "User", "DEPLOY_REQUEST", f"Deployment {deploy_id} created with auto command")

    notified = _notify_deployment_admins(deploy_id)

    main_bot.send_message(
        user.id,
        f"📁 <b>{html.escape(get_file_name(file_id))}</b>\n\n"
        f"📌 <b>File #{file_id}</b>\n"
        "📊 <b>Status:</b> 🟡 <b>Pending Approval</b>\n"
        "🤖 <b>Startup:</b> ⚡ Automatic detection\n"
        "⏳ <b>Waiting for Owner / Co-Owner approval.</b>",
        reply_markup=types.InlineKeyboardMarkup(row_width=2).add(
            types.InlineKeyboardButton("🔄 REFRESH", callback_data=f"file_open_{file_id}"),
            types.InlineKeyboardButton("⬅️ BACK TO FILES", callback_data="menu_files")
        )
    )


def show_deploy(call, file_id=None):
    user = call.from_user
    if not is_admin(user.id) and not get_user_plan(user.id):
        main_bot.edit_message_text(
            "⚠️ You need an active hosting plan to deploy. Please buy a plan first.",
            reply_markup=back_main_kb(), chat_id=call.message.chat.id,
            message_id=call.message.message_id
        )
        return

    if not file_id:
        conn = get_db()
        files = conn.execute(
            "SELECT * FROM files WHERE user_id=? ORDER BY uploaded_at DESC", (user.id,)
        ).fetchall()
        conn.close()
        if not files:
            main_bot.edit_message_text(
                "📁 No files found. Upload a file first.",
                reply_markup=back_main_kb(), chat_id=call.message.chat.id,
                message_id=call.message.message_id
            )
            return
        kb = types.InlineKeyboardMarkup(row_width=1)
        for f in files:
            kb.add(types.InlineKeyboardButton(
                f"📦 {f['name'][:20]}", callback_data=f"deploy_start_{f['id']}"
            ))
        kb.add(types.InlineKeyboardButton("◀️ BACK", callback_data="menu_main"))
        main_bot.edit_message_text(
            "🚀 <b>SELECT PROJECT</b>\n\nChoose a file. Startup is detected automatically.",
            reply_markup=kb, chat_id=call.message.chat.id,
            message_id=call.message.message_id
        )
        return

    try:
        _create_deployment_request(user, int(file_id))
        main_bot.answer_callback_query(call.id, "⚡ Automatic deployment request created")
    except Exception as e:
        main_bot.answer_callback_query(call.id, "❌ Setup failed")
        main_bot.send_message(
            call.message.chat.id,
            f"❌ <b>AUTOMATIC DEPLOYMENT FAILED</b>\n\n<code>{html.escape(str(e)[:900])}</code>",
            reply_markup=back_main_kb()
        )



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
Startup: Automatic
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
    reason = (message.text or "").strip() or "No reason provided."
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
        raise ValueError("Automatic startup command is unavailable.")

    executable = parts[0]
    allowed_binaries = {
        "python3", "python", "python3.12", "python3.13", "python3.14",
        "node", "java", "javac", "gcc", "g++", "make", "sh", "bash",
        "npm", "pip3", "pip"
    }
    sandbox_resolved = sandbox_dir.resolve()
    try:
        executable_resolved = Path(executable).resolve()
    except Exception:
        executable_resolved = None
    sandbox_local = bool(executable_resolved and (executable_resolved == sandbox_resolved or sandbox_resolved in executable_resolved.parents))
    if executable not in allowed_binaries and not (
        executable.startswith("/usr/bin/") or executable.startswith("/bin/") or sandbox_local
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
            log.write(f"\n===== PROCESS START {now_utc().isoformat()} =====\n")
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
                    f"⚠️ <b>HOSTING CRASHED</b>\n\n"
                    f"Deployment: <code>{deploy_id}</code>\n"
                    f"Exit code: <code>{rc}</code>\n"
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
        if HOSTING_MAX_ZIP_FILES is not None and len(infos) > HOSTING_MAX_ZIP_FILES:
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

def send_v5_file_status(chat_id, user_id, file_id):
    """Render the screenshot-style V5 ULTRA per-file deployment status card."""
    conn = get_db()
    file_row = conn.execute(
        "SELECT * FROM files WHERE id=? AND user_id=?", (file_id, user_id)
    ).fetchone()
    host = conn.execute(
        "SELECT * FROM hosting WHERE user_id=? AND deployment_id IN "
        "(SELECT id FROM deployments WHERE file_id=? AND user_id=?) "
        "ORDER BY started_at DESC LIMIT 1", (user_id, file_id, user_id)
    ).fetchone()
    dep = None
    if host:
        dep = conn.execute("SELECT * FROM deployments WHERE id=?", (host["deployment_id"],)).fetchone()
    else:
        dep = conn.execute(
            "SELECT * FROM deployments WHERE file_id=? AND user_id=? ORDER BY created_at DESC LIMIT 1",
            (file_id, user_id)
        ).fetchone()
    conn.close()
    if not file_row:
        return
    status = str(host["status"] if host else (dep["status"] if dep else "STOPPED")).upper()
    status_map = {
        "ONLINE": "🟢 Online", "STOPPED": "⚪ Stopped", "CRASHED": "🔴 Crashed",
        "PENDING_APPROVAL": "🟡 Pending Approval", "APPROVED": "🟡 Approved",
        "FAILED": "🔴 Failed", "REJECTED": "🔴 Rejected",
    }
    size_mb = int(file_row["size"] or 0) / (1024 * 1024)
    uptime = "—"
    if host and host["started_at"] and status == "ONLINE":
        try:
            delta = now_utc() - datetime.datetime.fromisoformat(str(host["started_at"]).replace("Z", "+00:00"))
            uptime = str(delta).split(".")[0]
        except Exception:
            pass
    command = "Automatic detection"
    cpu_text, mem_text = get_process_metrics(host["process_id"] if host else None)
    restarts = int(host["restart_count"] or 0) if host else 0
    text = (
        f"📁 <b>{html.escape(str(file_row['name']))}</b>\n\n"
        f"📌 <b>File #{file_id}</b>\n"
        f"📊 <b>Status:</b> {status_map.get(status, '⚪ ' + status.title())}\n"
        f"📦 <b>Size:</b> {size_mb:.2f} MB\n"
        f"🖥️ <b>Memory:</b> {mem_text}\n"
        f"📈 <b>CPU:</b> {cpu_text}\n"
        f"⏱️ <b>Uptime:</b> {uptime}\n"
        f"🔄 <b>Restarts:</b> {restarts}\n"
        "🤖 <b>Startup:</b> ⚡ Automatic\n\n"
        "👇 <b>Choose an action below:</b>"
    )
    main_bot.send_message(chat_id, text, reply_markup=file_detail_kb(file_id, host))


def _find_project_file(root: Path, filename: str) -> Optional[Path]:
    """Find a project metadata file without leaving the deployment sandbox."""
    direct = root / filename
    if direct.is_file():
        return direct
    try:
        for candidate in root.rglob(filename):
            if candidate.is_file() and root in candidate.resolve().parents:
                return candidate
    except Exception:
        pass
    return None


def _auto_install_common_python_imports(sandbox_dir: Path, python_bin: Path, log_file: Path) -> None:
    """Best-effort bootstrap for common Python libraries when no requirements file exists.

    Import names are mapped only for well-known packages; unknown imports are left alone
    so local modules and private packages are never guessed.
    """
    import ast
    package_map = {
        "pyrogram": "pyrogram",
        "telebot": "pyTelegramBotAPI",
        "telegram": "python-telegram-bot",
        "discord": "discord.py",
        "requests": "requests",
        "aiohttp": "aiohttp",
        "bs4": "beautifulsoup4",
        "PIL": "Pillow",
        "dotenv": "python-dotenv",
        "flask": "Flask",
        "fastapi": "fastapi",
        "uvicorn": "uvicorn",
        "pymongo": "pymongo",
        "sqlalchemy": "SQLAlchemy",
        "numpy": "numpy",
        "pandas": "pandas",
        "yaml": "PyYAML",
        "cv2": "opencv-python",
    }
    imports = set()
    for source in sandbox_dir.rglob("*.py"):
        if ".volt_venv" in source.parts:
            continue
        try:
            tree = ast.parse(source.read_text(encoding="utf-8", errors="ignore"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split(".")[0])
        except Exception:
            continue

    missing_packages = []
    for module in sorted(imports):
        package = package_map.get(module)
        if not package:
            continue
        check = subprocess.run(
            [str(python_bin), "-c", f"import {module}"], cwd=str(sandbox_dir),
            env=_sanitize_host_env(), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=False
        )
        if check.returncode != 0 and package not in missing_packages:
            missing_packages.append(package)

    if not missing_packages:
        return

    with open(log_file, "a", encoding="utf-8", errors="replace") as lf:
        lf.write("\n===== AUTO-DETECT PYTHON DEPENDENCIES =====\n")
        lf.write("Installing: " + ", ".join(missing_packages) + "\n")

    result = subprocess.run(
        [str(python_bin), "-m", "pip", "install", "--disable-pip-version-check", *missing_packages],
        cwd=str(sandbox_dir), env=_sanitize_host_env(), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300, check=False
    )
    with open(log_file, "a", encoding="utf-8", errors="replace") as lf:
        lf.write(result.stdout or "")
        lf.write(f"\n===== AUTO-DETECT INSTALL EXIT code={result.returncode} =====\n")
    if result.returncode != 0:
        raise RuntimeError("Automatic Python dependency installation failed")


def _prepare_project_dependencies(sandbox_dir: Path, launch_command: str, log_file: Path) -> str:
    """Install common declared dependencies and return the adjusted launch command.

    This is a best-effort production bootstrap: Python requirements.txt and Node
    package.json are installed automatically when present. Installation failures are
    written to the deployment log and raised so users see the real reason instead of
    a generic exit-code-only failure.
    """
    env = _sanitize_host_env()
    setup_lines = []

    req = _find_project_file(sandbox_dir, "requirements.txt")
    pyproject = _find_project_file(sandbox_dir, "pyproject.toml")
    if req or pyproject:
        venv_dir = sandbox_dir / ".volt_venv"
        python_bin = venv_dir / "bin" / "python"
        try:
            if not python_bin.exists():
                setup_lines.append("[VOLT] Creating Python virtual environment...")
                result = subprocess.run(
                    ["python3", "-m", "venv", str(venv_dir)], cwd=str(sandbox_dir), env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, timeout=120, check=False
                )
                setup_lines.append(result.stdout or "")
                if result.returncode != 0:
                    raise RuntimeError("Python virtual environment creation failed")
            if req:
                setup_lines.append(f"[VOLT] Installing Python dependencies from {req.relative_to(sandbox_dir)}...")
                result = subprocess.run(
                    [str(python_bin), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(req)],
                    cwd=str(req.parent), env=env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300, check=False
                )
            else:
                setup_lines.append("[VOLT] Installing Python project from pyproject.toml...")
                result = subprocess.run(
                    [str(python_bin), "-m", "pip", "install", "--disable-pip-version-check", "."],
                    cwd=str(pyproject.parent), env=env, stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300, check=False
                )
            setup_lines.append(result.stdout or "")
            if result.returncode != 0:
                raise RuntimeError("Python dependency installation failed")

            parts = shlex.split(launch_command)
            if parts and Path(parts[0]).name in {"python", "python3"}:
                parts[0] = str(python_bin)
                launch_command = shlex.join(parts)
        except Exception as exc:
            with open(log_file, "a", encoding="utf-8", errors="replace") as lf:
                lf.write("\n===== DEPENDENCY SETUP ERROR =====\n")
                lf.write("\n".join(setup_lines)[-12000:])
                lf.write(f"\n{type(exc).__name__}: {exc}\n")
            raise

    # Even without a dependency manifest, use an isolated Python environment and
    # install only a small, well-known set of missing imports.
    if not req and not pyproject:
        py_files = list(sandbox_dir.rglob("*.py"))
        if py_files:
            venv_dir = sandbox_dir / ".volt_venv"
            python_bin = venv_dir / "bin" / "python"
            if not python_bin.exists():
                result = subprocess.run(
                    ["python3", "-m", "venv", str(venv_dir)], cwd=str(sandbox_dir), env=env,
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, timeout=120, check=False
                )
                setup_lines.append(result.stdout or "")
                if result.returncode != 0:
                    raise RuntimeError("Python virtual environment creation failed")
            _auto_install_common_python_imports(sandbox_dir, python_bin, log_file)
            parts = shlex.split(launch_command)
            if parts and Path(parts[0]).name in {"python", "python3"}:
                parts[0] = str(python_bin)
                launch_command = shlex.join(parts)

    package_json = _find_project_file(sandbox_dir, "package.json")
    if package_json:
        node_modules = package_json.parent / "node_modules"
        if not node_modules.exists():
            setup_lines.append(f"[VOLT] Installing Node dependencies from {package_json.relative_to(sandbox_dir)}...")
            result = subprocess.run(
                ["npm", "install", "--omit=dev", "--no-audit", "--no-fund"],
                cwd=str(package_json.parent), env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=300, check=False
            )
            setup_lines.append(result.stdout or "")
            if result.returncode != 0:
                with open(log_file, "a", encoding="utf-8", errors="replace") as lf:
                    lf.write("\n===== NODE DEPENDENCY SETUP ERROR =====\n")
                    lf.write("\n".join(setup_lines)[-12000:])
                raise RuntimeError("Node dependency installation failed")

    if setup_lines:
        with open(log_file, "a", encoding="utf-8", errors="replace") as lf:
            lf.write("\n===== AUTOMATIC DEPENDENCY SETUP =====\n")
            lf.write("\n".join(setup_lines)[-16000:])
            lf.write("\n===== DEPENDENCY SETUP COMPLETE =====\n")
    return launch_command


def _infer_sandbox_start_command(sandbox_dir: Path, fallback_name: str) -> str:
    """Infer an entry point from the extracted project itself."""
    package_json = _find_project_file(sandbox_dir, "package.json")
    if package_json:
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            scripts = data.get("scripts") or {}
            if scripts.get("start"):
                return f"npm start --prefix {shlex.quote(str(package_json.parent))}"
            main = data.get("main")
            if main:
                return f"node {shlex.quote(str(package_json.parent / main))}"
        except Exception:
            pass

    preferred = [
        "bot.py", "main.py", "app.py", "run.py", "index.py", "server.py",
        "bot.js", "main.js", "app.js", "index.js", "server.js", "start.sh"
    ]
    files = []
    try:
        files = [x for x in sandbox_dir.rglob("*") if x.is_file()]
    except Exception:
        pass
    by_name = {}
    for f in files:
        by_name.setdefault(f.name.lower(), f)
    for candidate in preferred:
        f = by_name.get(candidate.lower())
        if f:
            rel = f.relative_to(sandbox_dir)
            if f.suffix.lower() == ".py":
                return f"python3 {shlex.quote(str(rel))}"
            if f.suffix.lower() == ".js":
                return f"node {shlex.quote(str(rel))}"
            if f.suffix.lower() == ".sh":
                return f"bash {shlex.quote(str(rel))}"
    # If the original file is directly present, use its normal extension.
    return _infer_start_command(sandbox_dir / Path(fallback_name).name, fallback_name)


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
                                  f"❌ <b>HOSTING FAILED</b>\n\nFile record not found for <code>{deploy_id}</code>.")
        except Exception:
            pass
        return

    file_path = Path(file_row["path"])
    user_id = deploy["user_id"]
    sandbox_dir = SANDBOX_ROOT / str(user_id) / f"deploy_{deploy_id}"
    shutil.rmtree(sandbox_dir, ignore_errors=True)
    sandbox_dir.mkdir(parents=True, exist_ok=True)
    log_file = sandbox_dir / "output.log"
    with open(log_file, "w", encoding="utf-8", errors="replace") as f:
        f.write(f"VOLT deployment {deploy_id}\n")
        f.write(f"Project file: {file_row['name']}\n")
        f.write(f"Started: {now_utc().isoformat()}\n")
        f.write("Startup mode: AUTOMATIC\n")

    # Startup is ALWAYS automatic. Ignore any legacy value that may still be
    # stored in the database (for example /start, an emoji, or an old manual
    # command). This guarantees old deployments cannot execute a Telegram
    # command as an OS executable.
    try:
        stored_command = _infer_start_command(file_path, file_row["name"])
        if "$PORT" in stored_command:
            stored_command = stored_command.replace("$PORT", str(int(os.environ.get("PORT", "8080"))))
        c.execute(
            "UPDATE deployments SET start_command=?, runtime=? WHERE id=?",
            (stored_command, "auto", deploy_id),
        )
        conn.commit()
    except Exception as repair_exc:
        logger.exception("Automatic startup detection failed for %s: %s", deploy_id, repair_exc)
        stored_command = ""

    try:
        if not file_path.exists():
            raise FileNotFoundError(f"Uploaded file missing: {file_path}")

        # Single files are copied; ZIP projects are extracted safely so requirements/assets
        # are available instead of copying only the ZIP itself.
        if file_path.suffix.lower() == ".zip":
            _extract_zip_safe(file_path, sandbox_dir)
            original_name = file_path.stem
            if not (sandbox_dir / stored_command.split()[-1]).exists():
                # Auto-detected ZIP entry points are validated below.
                pass
        else:
            shutil.copy2(file_path, sandbox_dir / file_path.name)
            original_name = file_path.name

        # Never fall back to a user-entered/legacy command. Re-infer from the actual
        # extracted sandbox so ZIP projects can use package.json or nested entry files.
        if file_path.suffix.lower() == ".zip":
            launch_command = _infer_sandbox_start_command(sandbox_dir, file_row["name"])
        else:
            launch_command = _infer_start_command(file_path, file_row["name"])

        # Compile native/source projects automatically before launching them.
        # Compilation output stays inside the deployment sandbox.
        ext = file_path.suffix.lower()
        if ext == ".java":
            source_name = file_path.name
            result = subprocess.run(
                ["javac", source_name], cwd=str(sandbox_dir), env=_sanitize_host_env(),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=90, check=False
            )
            with open(log_file, "a", encoding="utf-8", errors="replace") as lf:
                lf.write("\n===== JAVA COMPILATION =====\n")
                lf.write(result.stdout or "")
            if result.returncode != 0:
                raise RuntimeError("Java compilation failed: " + (result.stdout or "")[-1200:])
        elif ext == ".c":
            source_name = file_path.name
            result = subprocess.run(
                ["gcc", source_name, "-O2", "-o", "volt_app"], cwd=str(sandbox_dir), env=_sanitize_host_env(),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=90, check=False
            )
            with open(log_file, "a", encoding="utf-8", errors="replace") as lf:
                lf.write("\n===== C COMPILATION =====\n")
                lf.write(result.stdout or "")
            if result.returncode != 0:
                raise RuntimeError("C compilation failed: " + (result.stdout or "")[-1200:])
        elif ext == ".cpp":
            source_name = file_path.name
            result = subprocess.run(
                ["g++", source_name, "-O2", "-o", "volt_app"], cwd=str(sandbox_dir), env=_sanitize_host_env(),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, timeout=90, check=False
            )
            with open(log_file, "a", encoding="utf-8", errors="replace") as lf:
                lf.write("\n===== C++ COMPILATION =====\n")
                lf.write(result.stdout or "")
            if result.returncode != 0:
                raise RuntimeError("C++ compilation failed: " + (result.stdout or "")[-1200:])

        # Resolve only the automatically generated command.
        parts = _resolve_runtime_command(launch_command, sandbox_dir, original_name)

        env = _sanitize_host_env()

        with open(log_file, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"Command (before dependency setup): {launch_command}\n")

        # Automatically prepare declared Python/Node dependencies before launch.
        launch_command = _prepare_project_dependencies(sandbox_dir, launch_command, log_file)
        parts = _resolve_runtime_command(launch_command, sandbox_dir, original_name)
        with open(log_file, "a", encoding="utf-8", errors="replace") as f:
            f.write(f"Command (final): {' '.join(parts)}\n")

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
            bufsize=1,
            close_fds=True,
        )

        # Give the process a short grace period. This prevents a broken script from
        # being shown as ONLINE when it immediately exits with an import/token error.
        time.sleep(0.8)
        rc = proc.poll()
        if rc is not None:
            output = ""
            try:
                if proc.stdout:
                    output = proc.stdout.read()
                    if isinstance(output, bytes):
                        output = output.decode("utf-8", errors="replace")
                    else:
                        output = str(output)
            except Exception as read_exc:
                output = f"<could not read process output: {read_exc}>"
            with open(log_file, "a", encoding="utf-8", errors="replace") as f:
                if output:
                    f.write("\n===== STDOUT / STDERR =====\n")
                    f.write(output)
                f.write(f"\n===== PROCESS EXIT code={rc} =====\n")
            raise RuntimeError(f"Process exited immediately with code {rc}. Open 📜 LOGS for the actual error.")

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
        send_v5_file_status(user_id, user_id, int(deploy["file_id"]))

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
            kb_fail = types.InlineKeyboardMarkup(row_width=2)
            kb_fail.add(
                types.InlineKeyboardButton("📜 VIEW LOGS", callback_data=f"file_logs_{deploy['file_id']}"),
                types.InlineKeyboardButton("📁 MY FILES", callback_data="menu_files")
            )
            main_bot.send_message(
                user_id,
                f"❌ <b>HOSTING FAILED</b>\n\n"
                f"Deployment: <code>{deploy_id}</code>\n"
                f"Error: <code>{html.escape(str(e)[:900])}</code>\n\n"
                "📜 Open Logs to see the complete startup/dependency error.",
                reply_markup=kb_fail
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
            c2.execute("SELECT start_command FROM deployments WHERE id=?", (h["deployment_id"],))
            dep = c2.fetchone()
            conn2.close()
            status_emoji = "🟢" if h["status"] == "ONLINE" else "🔴"
            text += f"{status_emoji} {h['id']} - {dep['start_command'] if dep else 'N/A'}\n"
            text += f"   Status: {h['status']}\n"
            uptime = "—"
            if h["started_at"]:
                delta = now_utc() - datetime.datetime.fromisoformat(h["started_at"].replace('Z', '+00:00'))
                uptime = str(delta).split('.')[0]
            text += f"   Uptime: {uptime}\n\n"
            if h["status"] == "ONLINE":
                kb.add(types.InlineKeyboardButton(f"⏹️ STOP - {h['id']}", callback_data=f"host_stop_{h['id']}"))
                kb.add(types.InlineKeyboardButton(f"🔄 RESTART - {h['id']}", callback_data=f"host_restart_{h['id']}"))
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
        "UPDATE hosting SET status='STOPPED', stopped_at=?, restart_count=restart_count+1 WHERE id=?",
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
    # Runtime logs are private to the file owner (admins may also inspect them).
    # Ownership is enforced below before reading the log path.
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
        "🤖 Startup: <b>Automatic detection</b>\\n"
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
    # Initialize DB and normalize old manual-start records.
    init_db()
    sanitize_legacy_start_commands()
    recover_stale_hosting_records()

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

    # Do not start two polling workers with the same token. If MAIN/DB/PAY
    # accidentally share a token, Telegram will return 409 conflicts forever.
    started_tokens = set()
    def start_unique(bot, name, token):
        token = (token or "").strip()
        if not token:
            return
        if token in started_tokens:
            logger.error("%s not started: duplicate Telegram bot token is already in use by another local worker", name)
            return
        started_tokens.add(token)
        threading.Thread(target=run_bot, args=(bot, name), daemon=True).start()

    start_unique(main_bot, "MAIN BOT", BOT_TOKEN)
    start_unique(db_bot, "DB BOT", DB_BOT_TOKEN)
    start_unique(pay_bot, "PAY BOT", PAY_BOT_TOKEN)

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

@main_bot.message_handler(commands=['addadmin'])
def command_addadmin(message):
    if not is_admin(message.from_user.id):
        main_bot.reply_to(message, '⛔ Admin only.')
        return
    parts=(message.text or '').split()
    if len(parts)!=2 or not parts[1].isdigit():
        main_bot.reply_to(message, 'Usage: <code>/addadmin TELEGRAM_ID</code>', parse_mode='HTML')
        return
    target=int(parts[1])
    conn=get_db()
    try:
        conn.execute('INSERT OR REPLACE INTO admin_users(user_id,role,enabled,added_by) VALUES(?,?,1,?)',(target,'admin',message.from_user.id))
        conn.commit()
    finally: conn.close()
    log_audit(message.from_user.id, message.from_user.first_name or 'Admin', 'ADMIN_ADD', str(target))
    main_bot.reply_to(message, f'✅ Admin <code>{target}</code> added.', parse_mode='HTML')

if __name__ == "__main__":
    harden_runtime_directories()
    try:
        main()
    except KeyboardInterrupt:
        shutdown()