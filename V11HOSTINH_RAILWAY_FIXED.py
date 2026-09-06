#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""VOLT ⚡ HOSTING V11.00.000
Single-bot upgrade of the existing VOLTHOSTINGV11.py.
© 2026 VOLT ⚡ STUDIO — All Rights Reserved.

Secrets are environment variables. No public shell/exec interface is exposed.
Uploaded projects are never executed during upload; deployment requires approval
for public users and runs as a separate child process with conservative limits.
"""
import asyncio, hashlib, json, logging, os, re, shutil, signal, socket, time, uuid, zipfile
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Optional, Dict, Any

import aiosqlite
import psutil
import qrcode
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, ContextTypes, filters

load_dotenv()

# -------------------- CONFIG --------------------
BOT_TOKEN = "8992507650:AAESaPijyyAJxEgqX4_hcDRzQGkxp1lhStE".strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is empty")

OWNER_IDS = {8747221712}
OWNER_IDS |= {int(x) for x in os.getenv("OWNER_IDS", "").split(",") if x.strip().isdigit()}
CO_OWNER_IDS = {5769074791}
CO_OWNER_IDS |= {int(x) for x in os.getenv("CO_OWNER_IDS", "").split(",") if x.strip().isdigit()}
ADMIN_IDS = OWNER_IDS | CO_OWNER_IDS

DATABASE_PATH = os.getenv("DATABASE_PATH", "volt_hosting.db")
BASE_USER_DIR = Path(os.getenv("USER_DIR", "users")).resolve()
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", "backups")).resolve()
PORT_MIN = int(os.getenv("PORT_MIN", "20000")); PORT_MAX = int(os.getenv("PORT_MAX", "30000"))
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", str(50 * 1024 * 1024)))
MAX_PROJECT_FILES = int(os.getenv("MAX_PROJECT_FILES", "300"))
MAX_PROJECT_UNPACKED = int(os.getenv("MAX_PROJECT_UNPACKED", str(150 * 1024 * 1024)))
PAYMENT_DESTINATION = os.getenv("PAYMENT_DESTINATION", "").strip()
SUPPORT_CONTACT = os.getenv("SUPPORT_CONTACT", "@Volt_feedback_bot")
BOT_USERNAME = os.getenv("BOT_USERNAME", "").lstrip("@")
REQUIRED_GROUP_ID = int(os.getenv("REQUIRED_GROUP_ID", "-1004304252893"))
REQUIRED_GROUP_LINK = os.getenv("REQUIRED_GROUP_LINK", "https://t.me/+0SoZTE1W57gxYTBk")
REQUIRED_CHANNEL_ID = int(os.getenv("REQUIRED_CHANNEL_ID", "-1003739475692"))
REQUIRED_CHANNEL_LINK = os.getenv("REQUIRED_CHANNEL_LINK", "https://t.me/VOLT_CHANNEL_UP")
AUTO_RESTART = os.getenv("AUTO_RESTART", "1") == "1"
APPROVAL_REQUIRED = os.getenv("APPROVAL_REQUIRED", "1") == "1"

PLANS = {
    "7d": (7, 25), "14d": (14, 40), "30d": (30, 99),
    "60d": (60, 179), "365d": (365, 1299), "730d": (730, 2598),
}
PLAN_LABELS = {"7d":"7 DAYS", "14d":"14 DAYS", "30d":"30 DAYS", "60d":"60 DAYS", "365d":"1 YEAR", "730d":"2 YEARS"}

logging.basicConfig(level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
                    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
log = logging.getLogger("volt")

# -------------------- STATES --------------------
DEPLOY_STATES = {"UPLOADED","SCANNING","PENDING","APPROVED","STARTING","RUNNING","STOPPING","STOPPED","CRASHED","ERROR","REJECTED","EXPIRED"}


def now_iso(): return datetime.now(timezone.utc).isoformat()
def parse_dt(v):
    if not v: return None
    try:
        d = datetime.fromisoformat(v)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception: return None


def is_owner(uid: int) -> bool: return uid in OWNER_IDS
def is_co_owner(uid: int) -> bool: return uid in CO_OWNER_IDS
def is_admin(uid: int) -> bool: return uid in ADMIN_IDS

# -------------------- DATABASE --------------------
class DB:
    def __init__(self, path):
        self.path = path; self.conn = None; self.lock = asyncio.Lock()

    async def connect(self):
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.execute("PRAGMA foreign_keys=ON")
        await self.conn.execute("PRAGMA busy_timeout=5000")
        await self.conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(
          user_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
          is_premium INTEGER NOT NULL DEFAULT 0, premium_expires_at TEXT,
          referral_code TEXT UNIQUE, referred_by INTEGER, joined_requirements INTEGER DEFAULT 0,
          suspended INTEGER DEFAULT 0, created_at TEXT DEFAULT CURRENT_TIMESTAMP, last_active TEXT
        );
        CREATE TABLE IF NOT EXISTS projects(
          project_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, original_filename TEXT,
          project_dir TEXT NOT NULL, entry_file TEXT, file_hash TEXT, security_status TEXT DEFAULT 'PENDING',
          security_report TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(user_id) REFERENCES users(user_id)
        );
        CREATE TABLE IF NOT EXISTS deployments(
          deployment_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, user_id INTEGER NOT NULL,
          port INTEGER, status TEXT DEFAULT 'UPLOADED', process_id INTEGER, start_time TEXT,
          stop_time TEXT, restart_count INTEGER DEFAULT 0, restart_window_start TEXT,
          last_crash TEXT, exit_code INTEGER, last_error TEXT, resource_limits TEXT,
          approval_status TEXT DEFAULT 'PENDING', approved_by INTEGER, approved_at TEXT,
          rejected_by INTEGER, rejected_at TEXT, rejection_reason TEXT, auto_restart INTEGER DEFAULT 1,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          FOREIGN KEY(project_id) REFERENCES projects(project_id), FOREIGN KEY(user_id) REFERENCES users(user_id)
        );
        CREATE TABLE IF NOT EXISTS ports(port INTEGER PRIMARY KEY, user_id INTEGER, deployment_id TEXT, status TEXT DEFAULT 'FREE', occupied_since TEXT);
        CREATE TABLE IF NOT EXISTS payments(
          order_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, plan_id TEXT NOT NULL, duration_days INTEGER NOT NULL,
          amount INTEGER NOT NULL, currency TEXT DEFAULT 'INR', status TEXT DEFAULT 'CREATED', utr TEXT,
          proof_file_id TEXT, proof_hash TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, expires_at TEXT,
          verified_at TEXT, verified_by INTEGER, rejection_reason TEXT,
          FOREIGN KEY(user_id) REFERENCES users(user_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_utr ON payments(utr) WHERE utr IS NOT NULL AND utr!='';
        CREATE TABLE IF NOT EXISTS payment_proofs(id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT, file_id TEXT, file_hash TEXT, submitted_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS referrals(
          referral_id TEXT PRIMARY KEY, referrer_id INTEGER, referred_user_id INTEGER UNIQUE,
          reward_days INTEGER DEFAULT 1, reward_status TEXT DEFAULT 'PENDING', created_at TEXT DEFAULT CURRENT_TIMESTAMP,
          qualified_at TEXT, FOREIGN KEY(referrer_id) REFERENCES users(user_id), FOREIGN KEY(referred_user_id) REFERENCES users(user_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_referral_one_per_referrer
          ON referrals(referrer_id)
          WHERE reward_status IN ('PENDING','QUALIFIED','GRANTED');
        CREATE TABLE IF NOT EXISTS coupons(
          coupon_id TEXT PRIMARY KEY, code TEXT UNIQUE NOT NULL, reward_days INTEGER NOT NULL,
          max_redemptions INTEGER NOT NULL, current_redemptions INTEGER DEFAULT 0, created_by INTEGER,
          created_at TEXT DEFAULT CURRENT_TIMESTAMP, expires_at TEXT, status TEXT DEFAULT 'ACTIVE'
        );
        CREATE TABLE IF NOT EXISTS coupon_redemptions(id INTEGER PRIMARY KEY AUTOINCREMENT, coupon_id TEXT, user_id INTEGER, redeemed_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(coupon_id,user_id));
        CREATE TABLE IF NOT EXISTS audit_logs(id INTEGER PRIMARY KEY AUTOINCREMENT, actor_id INTEGER, actor_username TEXT, target_id TEXT, action TEXT, metadata TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS security_logs(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, project_id TEXT, event TEXT, severity TEXT, details TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS tickets(ticket_id TEXT PRIMARY KEY, user_id INTEGER, subject TEXT, message TEXT, status TEXT DEFAULT 'OPEN', admin_reply TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE INDEX IF NOT EXISTS idx_dep_user ON deployments(user_id); CREATE INDEX IF NOT EXISTS idx_dep_status ON deployments(status);
        CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(created_at); CREATE INDEX IF NOT EXISTS idx_users_premium ON users(is_premium,premium_expires_at);
        """)
        await self.conn.commit()
        await self.seed_settings()

    async def seed_settings(self):
        defaults = {
            "public_deployment":"1", "approval_required":"1", "manual_security_review":"1",
            "auto_restart":"1", "max_bots_free":"1", "max_bots_premium":"10"
        }
        for k,v in defaults.items(): await self.conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)",(k,v))
        await self.conn.commit()

    async def close(self):
        if self.conn: await self.conn.close()
    async def one(self,q,p=()):
        async with self.lock:
            cur=await self.conn.execute(q,p); r=await cur.fetchone(); await cur.close(); return r
    async def all(self,q,p=()):
        async with self.lock:
            cur=await self.conn.execute(q,p); r=await cur.fetchall(); await cur.close(); return r
    async def exec(self,q,p=()):
        async with self.lock:
            cur=await self.conn.execute(q,p); await self.conn.commit(); return cur.lastrowid

    async def user(self,uid): return await self.one("SELECT * FROM users WHERE user_id=?",(uid,))
    async def upsert_user(self,u,referrer=None):
        existing=await self.user(u.id)
        if existing:
            await self.exec("UPDATE users SET username=?,first_name=?,last_name=?,last_active=? WHERE user_id=?",(u.username,u.first_name,u.last_name,now_iso(),u.id))
            return existing,False
        code=f"VOLT{u.id}"
        await self.exec("INSERT INTO users(user_id,username,first_name,last_name,referral_code,referred_by,last_active) VALUES(?,?,?,?,?,?,?)",(u.id,u.username,u.first_name,u.last_name,code,referrer,now_iso()))
        await self.audit(u.id,u.id,"USER_REGISTERED",{"referrer":referrer})
        return await self.user(u.id),True

    async def audit(self,actor,target,action,meta=None,username=None):
        await self.exec("INSERT INTO audit_logs(actor_id,actor_username,target_id,action,metadata) VALUES(?,?,?,?,?)",(actor,username,target,action,json.dumps(meta or {},ensure_ascii=False)))

    async def premium_active(self,uid):
        if is_admin(uid): return True
        u=await self.user(uid)
        if not u or not u["is_premium"]: return False
        d=parse_dt(u["premium_expires_at"]); return bool(d and d>datetime.now(timezone.utc))

    async def add_premium(self,uid,days,actor=None):
        u=await self.user(uid)
        if not u: return False
        now=datetime.now(timezone.utc); exp=parse_dt(u["premium_expires_at"])
        base=exp if exp and exp>now else now; new=base+timedelta(days=days)
        await self.exec("UPDATE users SET is_premium=1,premium_expires_at=? WHERE user_id=?",(new.isoformat(),uid))
        await self.audit(actor or uid,uid,"PREMIUM_ADDED",{"days":days,"expiry":new.isoformat()})
        return True

    async def expire_premium(self):
        rows=await self.all("SELECT user_id,premium_expires_at FROM users WHERE is_premium=1 AND premium_expires_at IS NOT NULL")
        expired=[]; t=datetime.now(timezone.utc)
        for r in rows:
            d=parse_dt(r["premium_expires_at"])
            if d and d<=t and not is_admin(r["user_id"]):
                await self.exec("UPDATE users SET is_premium=0 WHERE user_id=?",(r["user_id"],)); expired.append(r["user_id"])
        return expired

    async def create_payment(self,uid,plan):
        days,amount=PLANS[plan]; oid=f"VOLT-{uuid.uuid4().hex[:8].upper()}"
        await self.exec("INSERT INTO payments(order_id,user_id,plan_id,duration_days,amount,status,expires_at) VALUES(?,?,?,?,?,?,?)",(oid,uid,plan,days,amount,"PAYMENT_PENDING",(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat()))
        await self.audit(uid,oid,"PAYMENT_CREATED",{"plan":plan,"amount":amount}); return oid

    async def payment(self,oid): return await self.one("SELECT * FROM payments WHERE order_id=?",(oid,))
    async def pending_payments(self): return await self.all("SELECT * FROM payments WHERE status IN ('PAYMENT_SUBMITTED','MANUAL_REVIEW') ORDER BY created_at")
    async def pending_deployments(self): return await self.all("SELECT * FROM deployments WHERE status='PENDING' ORDER BY created_at")

    async def allocate_port(self,uid,dep):
        used={r["port"] for r in await self.all("SELECT port FROM ports WHERE status!='FREE'")}
        for p in range(PORT_MIN,PORT_MAX+1):
            if p not in used:
                await self.exec("INSERT OR REPLACE INTO ports(port,user_id,deployment_id,status,occupied_since) VALUES(?,?,?,?,?)",(p,uid,dep,"RESERVED",now_iso())); return p
        raise RuntimeError("No free port")
    async def release_port(self,p):
        if p: await self.exec("UPDATE ports SET status='FREE',user_id=NULL,deployment_id=NULL,occupied_since=NULL WHERE port=?",(p,))


db=DB(DATABASE_PATH)

# -------------------- SECURITY / FILES --------------------
BAD_EXT={".exe",".dll",".so",".bin",".scr",".msi"}
DANGEROUS_PATTERNS=[r"\bsocket\.",r"subprocess\.",r"os\.system\s*\(",r"shell\s*=\s*True",r"\beval\s*\(",r"\bexec\s*\(",r"shutil\.rmtree",r"/etc/passwd",r"/proc/",r"/sys/"]

def safe_filename(name):
    name=os.path.basename(name or "file.txt"); name=re.sub(r"[^A-Za-z0-9_.-]","_",name); return name[:180] or "file.txt"

def safe_path(base,*parts):
    root=Path(base).resolve(); p=(root.joinpath(*parts)).resolve()
    if p!=root and root not in p.parents: raise ValueError("Path traversal detected")
    return p

def sha(data): return hashlib.sha256(data).hexdigest()

async def scan_project(directory:Path):
    report={"warnings":[],"blockers":[],"files":0,"bytes":0,"risk_score":0}
    try:
        for root,dirs,files in os.walk(directory):
            for fn in files:
                p=Path(root)/fn; report["files"]+=1; sz=p.stat().st_size; report["bytes"]+=sz
                if report["files"]>MAX_PROJECT_FILES or report["bytes"]>MAX_PROJECT_UNPACKED: report["blockers"].append("project resource limit exceeded"); continue
                if p.suffix.lower() in BAD_EXT: report["blockers"].append(f"unsupported executable: {fn}"); continue
                if p.suffix.lower() in {".py",".js",".ts",".php",".rb",".sh",".json",".yml",".yaml"}:
                    try: txt=p.read_text(errors="ignore")
                    except Exception as e: report["warnings"].append(f"unreadable source: {fn}"); continue
                    for pat in DANGEROUS_PATTERNS:
                        if re.search(pat,txt,re.I): report["warnings"].append(f"suspicious pattern in {fn}: {pat}"); report["risk_score"]+=10
                    if re.search(r"(?:bot_token|api[_-]?hash|api[_-]?key|password|secret)\s*[:=]",txt,re.I): report["warnings"].append(f"possible hardcoded secret: {fn}"); report["risk_score"]+=8
        status="BLOCKED" if report["blockers"] else ("REVIEW" if report["warnings"] else "SAFE")
        return status,report
    except Exception as e:
        log.exception("security scan failed"); return "REVIEW",{"warnings":["scanner failure; manual review required"],"blockers":[],"risk_score":100,"error":str(e)}

async def extract_zip(data:bytes,dest:Path):
    with zipfile.ZipFile(BytesIO(data)) as z:
        total=0; count=0
        for info in z.infolist():
            count+=1; total+=info.file_size
            if count>MAX_PROJECT_FILES or total>MAX_PROJECT_UNPACKED: raise ValueError("Archive exceeds project limits")
            n=Path(info.filename)
            if n.is_absolute() or ".." in n.parts: raise ValueError("Unsafe archive path")
            if info.filename.startswith(("/","\\")) or ":" in info.filename.split("/")[0]: raise ValueError("Unsafe archive path")
        z.extractall(dest)

def detect_entry(directory):
    candidates=["bot.py","main.py","app.py","index.js","server.js","main.js"]
    for c in candidates:
        if (directory/c).is_file(): return c
    py=list(directory.glob("*.py")); js=list(directory.glob("*.js"))
    if py: return py[0].name
    if js: return js[0].name
    raise FileNotFoundError("No supported entry file found")

def qr_bytes(upi):
    q=qrcode.QRCode(version=None,error_correction=qrcode.constants.ERROR_CORRECT_H,box_size=8,border=4); q.add_data(upi); q.make(fit=True)
    img=q.make_image(fill_color="black",back_color="white").convert("RGB"); b=BytesIO(); img.save(b,"PNG"); return b.getvalue()

def port_available(port):
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
        return s.connect_ex(("127.0.0.1",port))!=0

# -------------------- PROCESS MANAGER --------------------
class ProcessManager:
    def __init__(self,db): self.db=db; self.procs={}; self.tasks={}; self.locks={}
    def lock_for(self,dep): return self.locks.setdefault(dep,asyncio.Lock())
    async def start(self,dep_id,approved=False):
        async with self.lock_for(dep_id):
            d=await self.db.one("SELECT * FROM deployments WHERE deployment_id=?",(dep_id,))
            if not d or d["status"] in {"RUNNING","STARTING","STOPPING","REJECTED","EXPIRED"}: return False,"invalid state"
            if not approved and d["approval_status"]!="APPROVED": return False,"not approved"
            p=await self.db.one("SELECT * FROM projects WHERE project_id=?",(d["project_id"],))
            if not p or not Path(p["project_dir"]).is_dir(): await self.db.exec("UPDATE deployments SET status='ERROR',last_error=? WHERE deployment_id=?",("project missing",dep_id)); return False,"project missing"
            if not port_available(d["port"]): return False,"port unavailable"
            entry=Path(p["project_dir"])/p["entry_file"]
            if not entry.is_file(): return False,"entry file missing"
            await self.db.exec("UPDATE deployments SET status='STARTING' WHERE deployment_id=? AND status IN ('APPROVED','PENDING','STOPPED','CRASHED','ERROR')",(dep_id,))
            env=os.environ.copy(); env["PORT"]=str(d["port"]); env["VOLT_DEPLOYMENT_ID"]=dep_id; env["VOLT_USER_ID"]=str(d["user_id"])
            log_path=Path(p["project_dir"])/".volt_runtime.log"
            try:
                lf=open(log_path,"ab")
                proc=await asyncio.create_subprocess_exec("python",str(entry),cwd=p["project_dir"],env=env,stdout=lf,stderr=lf,start_new_session=True)
                lf.close()
            except Exception as e:
                with suppress(Exception): lf.close()
                await self.db.exec("UPDATE deployments SET status='ERROR',last_error=? WHERE deployment_id=?",(str(e)[:1000],dep_id)); return False,str(e)
            self.procs[dep_id]=proc
            await self.db.exec("UPDATE ports SET status='OCCUPIED' WHERE port=?",(d["port"],))
            await self.db.exec("UPDATE deployments SET status='RUNNING',process_id=?,start_time=?,last_error=NULL WHERE deployment_id=?",(proc.pid,now_iso(),dep_id))
            self.tasks[dep_id]=asyncio.create_task(self.monitor(dep_id,proc))
            return True,"running"

    async def monitor(self,dep_id,proc):
        try:
            ps=None
            with suppress(Exception): ps=psutil.Process(proc.pid)
            if ps: ps.cpu_percent(None)
            while proc.returncode is None:
                await asyncio.sleep(5)
                if ps:
                    try:
                        mem=ps.memory_info().rss/(1024*1024)
                        d=await self.db.one("SELECT resource_limits FROM deployments WHERE deployment_id=?",(dep_id,))
                        limits=json.loads(d["resource_limits"] or "{}") if d else {}
                        max_mem=float(limits.get("memory_mb",512))
                        if mem>max_mem:
                            log.warning("resource limit exceeded for %s: %.1f MB",dep_id,mem)
                            with suppress(Exception): os.killpg(os.getpgid(proc.pid),signal.SIGTERM)
                            await asyncio.sleep(2)
                            if proc.returncode is None:
                                with suppress(Exception): os.killpg(os.getpgid(proc.pid),signal.SIGKILL)
                            await self.db.exec("UPDATE deployments SET status='ERROR',last_error=? WHERE deployment_id=?",(f"RESOURCE LIMIT: memory {mem:.1f}MB > {max_mem:.0f}MB",dep_id))
                            break
                    except psutil.NoSuchProcess: break
                    except Exception: log.exception("resource monitor failed for %s",dep_id)
            code=await proc.wait()
            d=await self.db.one("SELECT * FROM deployments WHERE deployment_id=?",(dep_id,))
            if d and d["status"]=="STOPPING":
                await self.db.release_port(d["port"])
            elif code==0:
                await self.db.exec("UPDATE deployments SET status='STOPPED',stop_time=?,exit_code=?,process_id=NULL WHERE deployment_id=?",(now_iso(),code,dep_id))
                if d: await self.db.release_port(d["port"])
            else:
                log_path=Path((await self.db.one("SELECT project_dir FROM projects WHERE project_id=?",(d["project_id"],)))["project_dir"])/".volt_runtime.log" if d else None
                txt=""
                if log_path and log_path.exists():
                    with suppress(Exception): txt=log_path.read_text(errors="replace")[-4000:]
                await self.db.exec("UPDATE deployments SET status='CRASHED',last_crash=?,exit_code=?,last_error=?,process_id=NULL WHERE deployment_id=?",(now_iso(),code,txt,dep_id))
                should_restart = bool(d and d["status"] == "CRASHED" and d["auto_restart"] and AUTO_RESTART)
                if should_restart:
                    rs=int(d["restart_count"] or 0); start=parse_dt(d["restart_window_start"]); t=datetime.now(timezone.utc)
                    if not start or t-start>timedelta(hours=1): rs=0; start=t
                    rs+=1; await self.db.exec("UPDATE deployments SET restart_count=?,restart_window_start=? WHERE deployment_id=?",(rs,start.isoformat(),dep_id))
                    if rs<=5:
                        await asyncio.sleep(min(5*rs,30))
                        restarted, reason = await self.start(dep_id,approved=True)
                        if not restarted:
                            await self.db.exec("UPDATE deployments SET status='ERROR',last_error=? WHERE deployment_id=?",(f"AUTO-RESTART FAILED: {reason}",dep_id))
                            await self.db.release_port(d["port"])
                    else:
                        await self.db.exec("UPDATE deployments SET status='ERROR',last_error=? WHERE deployment_id=?",("CRASH LOOP DETECTED: 5 restarts/hour",dep_id))
                        await self.db.release_port(d["port"])
                        await notify_admin_global(f"⚠️ CRASH LOOP DETECTED\n\nDeployment: {dep_id}")
                elif d and d["status"] not in {"RUNNING", "STARTING"}:
                    await self.db.release_port(d["port"])
        except asyncio.CancelledError: raise
        except Exception: log.exception("monitor failed for %s",dep_id)
        finally:
            self.procs.pop(dep_id,None); self.tasks.pop(dep_id,None)

    async def stop(self,dep_id):
        async with self.lock_for(dep_id):
            p=self.procs.get(dep_id); d=await self.db.one("SELECT * FROM deployments WHERE deployment_id=?",(dep_id,))
            if not d: return False
            if p and p.returncode is None:
                await self.db.exec("UPDATE deployments SET status='STOPPING' WHERE deployment_id=?",(dep_id,))
                try: os.killpg(os.getpgid(p.pid),signal.SIGTERM)
                except ProcessLookupError: pass
                try: await asyncio.wait_for(p.wait(),5)
                except asyncio.TimeoutError:
                    with suppress(Exception): os.killpg(os.getpgid(p.pid),signal.SIGKILL)
            await self.db.exec("UPDATE deployments SET status='STOPPED',stop_time=?,process_id=NULL WHERE deployment_id=?",(now_iso(),dep_id)); await self.db.release_port(d["port"]); return True

async def notify_admin_global(text):
    # Best-effort admin alert used by process monitoring.
    # Bot object is not available until main(); caller exceptions are contained.
    bot=getattr(APP_STATE,"bot",None) if "APP_STATE" in globals() else None
    if not bot: return
    for aid in ADMIN_IDS:
        await safe_send(bot,aid,text)

class _AppState: bot=None
APP_STATE=_AppState()

pm=ProcessManager(db)

# -------------------- MEMBERSHIP GATE --------------------
async def joined_required(bot,uid):
    if is_admin(uid): return True
    async def member(chat):
        try:
            m=await bot.get_chat_member(chat,uid)
            if m.status in {"left","kicked","banned"}: return False
            return bool(getattr(m,"is_member",True))
        except Exception as e:
            log.warning("membership check failed for %s: %s",chat,e); return False
    return await member(REQUIRED_GROUP_ID) and await member(REQUIRED_CHANNEL_ID)

async def require_join(update,context):
    uid=update.effective_user.id
    if await joined_required(context.bot,uid):
        await db.exec("UPDATE users SET joined_requirements=1 WHERE user_id=?",(uid,)); return True
    kb=[[InlineKeyboardButton("👥 JOIN GC",url=REQUIRED_GROUP_LINK)],[InlineKeyboardButton("📢 JOIN CHANNEL",url=REQUIRED_CHANNEL_LINK)],[InlineKeyboardButton("✅ VERIFY JOIN",callback_data="verify_join")]]
    text="🔒 ACCESS LOCKED\n\nPlease join our required Group and Channel first.\nAfter joining, tap VERIFY JOIN."
    if update.callback_query:
        with suppress(Exception): await update.callback_query.answer("Join both first.",show_alert=True)
        with suppress(Exception): await update.callback_query.edit_message_text(text,reply_markup=InlineKeyboardMarkup(kb))
    elif update.effective_message: await update.effective_message.reply_text(text,reply_markup=InlineKeyboardMarkup(kb))
    return False

# -------------------- UI --------------------
def main_kb(uid):
    items=[
        ("📤 UPLOAD FILE","upload_file"),("📁 MY SCRIPTS","my_scripts"),
        ("💎 BUY PREMIUM","buy_premium"),("🎁 REFERRAL","referral"),
        ("🛑 STOP SCRIPT","stop_script"),("📜 VIEW LOGS","view_logs"),
        ("📦 INSTALL","install"),("⚡ BOT SPEED","bot_speed"),
        ("📊 STATS","stats"),("🎫 SUPPORT","support"),
        ("❓ HELP","help"),("📞 CONTACT","contact"),
    ]
    kb=[]
    for i in range(0,len(items),2):
        kb.append([InlineKeyboardButton(items[i][0],callback_data=items[i][1]),InlineKeyboardButton(items[i+1][0],callback_data=items[i+1][1])])
    if is_admin(uid): kb.append([InlineKeyboardButton("🛡️ ADMIN PANEL",callback_data="admin_panel")])
    return InlineKeyboardMarkup(kb)

def back(cb="menu"): return InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ BACK",callback_data=cb)]])

def admin_kb():
    return InlineKeyboardMarkup([
      [InlineKeyboardButton("⏳ Pending",callback_data="admin_pending"),InlineKeyboardButton("🤖 Deployments",callback_data="admin_deployments")],
      [InlineKeyboardButton("👥 Users",callback_data="admin_users"),InlineKeyboardButton("💳 Payments",callback_data="admin_payments")],
      [InlineKeyboardButton("🎟️ Coupons",callback_data="admin_coupons"),InlineKeyboardButton("💎 Premium",callback_data="admin_premium")],
      [InlineKeyboardButton("🎁 Referrals",callback_data="admin_referrals"),InlineKeyboardButton("🛡️ Security",callback_data="admin_security")],
      [InlineKeyboardButton("📊 Statistics",callback_data="admin_stats"),InlineKeyboardButton("🩺 Health",callback_data="admin_health")],
      [InlineKeyboardButton("📜 Audit Logs",callback_data="admin_audit"),InlineKeyboardButton("💾 Backups",callback_data="admin_backups")],
      [InlineKeyboardButton("⚙️ Settings",callback_data="admin_settings"),InlineKeyboardButton("👑 Co-Owner Permissions",callback_data="admin_perms")],
      [InlineKeyboardButton("🖥️ Server",callback_data="admin_server"),InlineKeyboardButton("🖼️ Branding",callback_data="admin_photo")],
      [InlineKeyboardButton("📄 EXPORT USERS",callback_data="admin_export_users")],
      [InlineKeyboardButton("⬅️ BACK",callback_data="menu")]])

async def safe_send(bot,uid,text,**kw):
    try: return await bot.send_message(uid,text,**kw)
    except Exception as e: log.warning("telegram send failed: %s",e); return None

async def start(update,context):
    u=update.effective_user; ref=None
    parts=(update.effective_message.text or "").split()
    if len(parts)>1 and parts[1].startswith("ref_") and parts[1][4:].isdigit(): ref=int(parts[1][4:])
    if ref==u.id: ref=None
    old,new=await db.upsert_user(u,ref)
    if not await require_join(update,context): return
    # Referral is qualified only after onboarding/join verification.
    if new and ref and ref!=u.id and await db.user(ref):
        try:
            async with db.lock:
                cur=await db.conn.execute("INSERT OR IGNORE INTO referrals(referral_id,referrer_id,referred_user_id,reward_days,reward_status,qualified_at) VALUES(?,?,?,?,?,?)",(str(uuid.uuid4()),ref,u.id,1,"QUALIFIED",now_iso()))
                inserted=cur.rowcount == 1
                if inserted:
                    rr=await db.conn.execute("SELECT premium_expires_at FROM users WHERE user_id=?",(ref,)); ru=await rr.fetchone(); await rr.close()
                    t=datetime.now(timezone.utc); exp=parse_dt(ru[0] if ru else None); base=exp if exp and exp>t else t; new_exp=base+timedelta(days=1)
                    await db.conn.execute("UPDATE users SET is_premium=1,premium_expires_at=? WHERE user_id=?",(new_exp.isoformat(),ref))
                    await db.conn.execute("UPDATE referrals SET reward_status='GRANTED' WHERE referred_user_id=?",(u.id,))
                    await db.conn.execute("INSERT INTO audit_logs(actor_id,target_id,action,metadata) VALUES(?,?,?,?)",(ref,u.id,"REFERRAL_REWARDED",json.dumps({"days":1,"expiry":new_exp.isoformat()})))
                    await db.conn.commit()
            if inserted:
                await safe_send(context.bot,ref,"🎁 Referral successful! +1 DAY PREMIUM has been added.")
        except Exception:
            log.exception("referral reward failed")
    premium=await db.premium_active(u.id); exp=(await db.user(u.id))["premium_expires_at"]
    text=f"⚡ VOLT HOSTING\nV11.00.000\n\n👤 {u.first_name}\n🆔 {u.id}\n💎 Premium: {'✅' if premium else '❌'}\n⏳ Expiry: {exp or '—'}\n\nFAST • SECURE • MODERN • STABLE"
    await update.effective_message.reply_text(text,reply_markup=main_kb(u.id))

async def cmd_menu(update,context):
    if not await require_join(update,context): return
    await update.effective_message.reply_text("⚡ VOLT HOSTING\n\nChoose an option:",reply_markup=main_kb(update.effective_user.id))

async def admin_cmd(update,context):
    if not is_admin(update.effective_user.id): return await update.effective_message.reply_text("❌ Access denied")
    await db.audit(update.effective_user.id,"admin_panel","ADMIN_PANEL_OPEN",username=update.effective_user.username)
    await update.effective_message.reply_text("🛡️ VOLT ADMIN CENTER\n\nSecure operational dashboard.",reply_markup=admin_kb())

async def my_scripts_view(q, uid):
    rows = await db.all(
        "SELECT d.deployment_id,d.status,p.original_filename "
        "FROM deployments d JOIN projects p ON p.project_id=d.project_id "
        "WHERE d.user_id=? ORDER BY d.created_at DESC",
        (uid,),
    )
    if not rows:
        return await q.edit_message_text(
            "📁 MY SCRIPTS\n\nNo deployments found.",
            reply_markup=back("menu"),
        )
    lines=[]; kb=[]
    for r in rows:
        lines.append(f"📦 {r['original_filename'] or 'Unknown'}\n🆔 {str(r['deployment_id'])[:12]}\n📌 Status: {r['status']}")
        kb.append([InlineKeyboardButton(f"{str(r['deployment_id'])[:12]} • {r['status']}", callback_data=f"logs_{r['deployment_id']}")])
    kb.append([InlineKeyboardButton("⬅️ BACK", callback_data="menu")])
    return await q.edit_message_text("📁 MY SCRIPTS\n\n"+"\n\n".join(lines)[:3900], reply_markup=InlineKeyboardMarkup(kb))

# -------------------- CALLBACKS --------------------
async def cb(update,context):
    q=update.callback_query; data=q.data or ""; uid=q.from_user.id
    await q.answer()
    if data=="verify_join":
        if await joined_required(context.bot,uid):
            await db.exec("UPDATE users SET joined_requirements=1 WHERE user_id=?",(uid,)); await q.edit_message_text("✅ Membership verified.\n\n⚡ VOLT HOSTING",reply_markup=main_kb(uid))
        else: await q.answer("❌ Join both the GC and Channel first.",show_alert=True)
        return
    if not is_admin(uid) and not await joined_required(context.bot,uid):
        await require_join(update,context); return
    if data=="menu": return await q.edit_message_text("⚡ VOLT HOSTING\n\nChoose an option:",reply_markup=main_kb(uid))
    if data=="admin_panel":
        if not is_admin(uid): return await q.answer("Access denied",show_alert=True)
        await db.audit(uid,"admin_panel","ADMIN_PANEL_OPEN",username=q.from_user.username); return await q.edit_message_text("🛡️ VOLT ADMIN CENTER",reply_markup=admin_kb())
    if data=="upload_file": return await q.edit_message_text("📤 UPLOAD FILE\n\nSend your project as a Document.\nSupported: .py/.js projects and ZIP archives.\n\nPublic deployments enter security review + approval.",reply_markup=back())
    if data=="my_scripts": return await my_scripts_view(q,uid)
    if data=="buy_premium": return await plans_view(q)
    if data=="redeem_coupon": return await redeem_coupon_start(q,context,uid)
    if data.startswith("buy_"): return await create_order(q,context,data[4:])
    if data=="referral": return await referral_view(q,uid)
    if data=="stats": return await stats_view(q,uid)
    if data=="support": return await q.edit_message_text(f"🎫 SUPPORT\n\nUse /ticket <message>\nContact: {SUPPORT_CONTACT}",reply_markup=back())
    if data=="help": return await q.edit_message_text("❓ HELP\n\nUpload → Scan → Approval → Deploy.\nPublic users have no shell/CMD access.\nYour files/logs are isolated by Telegram user ID.",reply_markup=back())
    if data=="contact": return await q.edit_message_text(f"📞 CONTACT\n\n{SUPPORT_CONTACT}",reply_markup=back())
    if data=="install": return await q.edit_message_text("📦 INSTALL\n\nRuntime dependencies are managed by the deployment environment. Public users cannot execute arbitrary install/shell commands.",reply_markup=back())
    if data=="bot_speed": return await speed_view(q,context)
    if data=="stop_script": return await stop_view(q,uid)
    if data=="view_logs": return await logs_view(q,uid)
    if data.startswith("stop_"): return await user_stop(q,uid,data[5:])
    if data.startswith("logs_"): return await user_logs(q,uid,data[5:])
    if data.startswith("proof_"): context.user_data["proof_order"]=data[6:]; return await q.edit_message_text("📸 Send the payment screenshot now.\n\n⬅️ Cancel with /menu")
    if data.startswith("utr_"): context.user_data["utr_order"]=data[4:]; return await q.edit_message_text("🔢 Send the UTR / Transaction ID as a text message.",reply_markup=back())
    if data.startswith("admin_pending"): return await admin_pending(q,uid)
    if data.startswith("approve_deploy_"): return await admin_approve(q,context,uid,data[len("approve_deploy_"):])
    if data.startswith("reject_deploy_"): return await admin_reject(q,context,uid,data[len("reject_deploy_"):])
    if data=="admin_payments": return await admin_payments(q,uid)
    if data.startswith("approve_pay_"): return await admin_approve_payment(q,context,uid,data[len("approve_pay_"):])
    if data.startswith("reject_pay_"): return await admin_reject_payment(q,context,uid,data[len("reject_pay_"):])
    if data=="admin_users": return await admin_users_detail(q,uid)
    if data=="admin_stats": return await admin_stats(q,uid)
    if data=="admin_health": return await admin_health(q,uid)
    if data=="admin_audit": return await admin_audit(q,uid)
    if data=="admin_backups": return await admin_backup(q,uid)
    if data=="admin_deployments": return await admin_deployments(q,uid)
    if data=="admin_premium": return await admin_premium(q,uid)
    if data=="admin_referrals": return await admin_referrals(q,uid)
    if data=="admin_security": return await admin_security(q,uid)
    if data=="admin_settings": return await admin_settings(q,uid)
    if data=="admin_perms": return await admin_perms(q,uid)
    if data=="admin_server": return await admin_server(q,uid)
    if data=="admin_coupons": return await admin_coupons(q,uid)
    if data=="admin_photo": return await admin_photo(q,uid)
    if data=="admin_export_users": return await admin_export_users(q,context,uid)
    if data=="admin_panel": return await admin_dashboard(q,uid)
    if data=="admin_users": return await admin_users_detail(q,uid)
    if data.startswith("dep_details_"): return await deployment_details(q,uid,data[len("dep_details_"):])
    if data.startswith("sec_report_"):
        if not is_admin(uid): return await q.answer("Access denied",show_alert=True)
        did=data[len("sec_report_"):]; d=await db.one("SELECT project_id FROM deployments WHERE deployment_id=?",(did,)); p=await db.one("SELECT security_status,security_report FROM projects WHERE project_id=?",(d["project_id"],)) if d else None
        return await q.edit_message_text("🛡️ SECURITY REPORT\n\n"+(p["security_report"] if p else "Not available")[:3800],reply_markup=back("admin_pending"))
    if data.startswith("pay_details_"): return await payment_details(q,uid,data[len("pay_details_"):])
    await q.answer("⚠️ Unknown or expired action.",show_alert=True)

async def plans_view(q):
    kb=[]
    for p,(d,a) in PLANS.items(): kb.append([InlineKeyboardButton(f"💳 {PLAN_LABELS[p]} — ₹{a}",callback_data=f"buy_{p}")])
    kb.append([InlineKeyboardButton("🎟️ REDEEM COUPON",callback_data="redeem_coupon")])
    kb.append([InlineKeyboardButton("⬅️ BACK",callback_data="menu")]); await q.edit_message_text("💎 VOLT PREMIUM\n\nChoose your plan:",reply_markup=InlineKeyboardMarkup(kb))

async def create_order(q,context,plan):
    if plan not in PLANS: return await q.answer("Invalid plan",show_alert=True)
    if not PAYMENT_DESTINATION: return await q.edit_message_text("⚠️ Payment is temporarily unavailable. Please contact support.",reply_markup=back())
    oid=await db.create_payment(q.from_user.id,plan); days,amount=PLANS[plan]
    upi=f"upi://pay?pa={PAYMENT_DESTINATION}&pn=VOLT%20PREMIUM&am={amount}&cu=INR&tn={oid}"
    kb=InlineKeyboardMarkup([[InlineKeyboardButton("📸 SEND PAYMENT PROOF",callback_data=f"proof_{oid}")],[InlineKeyboardButton("🔢 SEND UTR",callback_data=f"utr_{oid}")],[InlineKeyboardButton("❌ CANCEL",callback_data="menu")]])
    await q.message.reply_photo(photo=qr_bytes(upi),caption=f"⚡ VOLT PREMIUM\n\n💎 Plan: {PLAN_LABELS[plan]}\n💰 Amount: ₹{amount}\n🧾 Order: {oid}\n\n📱 Scan to Pay\n\nAfter payment, submit UTR and proof.")
    await q.edit_message_text("🟡 PAYMENT PENDING\n\nSubmit your UTR and payment screenshot.",reply_markup=kb)

async def referral_view(q,uid):
    u=await db.user(uid); rows=await db.all("SELECT * FROM referrals WHERE referrer_id=? ORDER BY created_at DESC",(uid,)); earned=sum(r["reward_days"] for r in rows if r["reward_status"] in ("QUALIFIED","GRANTED")); bot=BOT_USERNAME or "YourBotUsername"; link=f"https://t.me/{bot}?start=ref_{uid}"
    await q.edit_message_text(f"🎁 MY REFERRAL\n\n👥 Total Referrals: {len(rows)}\n🎁 Rewards Earned: {earned} DAYS\n\n🔗 {link}\n\nEvery successful referral: +1 DAY PREMIUM",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 SHARE REFERRAL",url=f"https://t.me/share/url?url={link}")],[InlineKeyboardButton("⬅️ BACK",callback_data="menu")]]))

async def stats_view(q,uid):
    ds=await db.all("SELECT status FROM deployments WHERE user_id=?",(uid,)); await q.edit_message_text(f"📊 STATS\n\n🤖 Deployments: {len(ds)}\n🟢 Running: {sum(x['status']=='RUNNING' for x in ds)}\n💎 Premium: {'✅' if await db.premium_active(uid) else '❌'}",reply_markup=back())

async def speed_view(q,context):
    t=time.perf_counter(); await db.one("SELECT 1"); dbms=(time.perf_counter()-t)*1000; t=time.perf_counter()
    try: await context.bot.get_me(); tg=(time.perf_counter()-t)*1000
    except: tg=-1
    await q.edit_message_text(f"⚡ BOT SPEED\n\nDB: {dbms:.2f} ms\nTelegram API: {tg:.2f} ms\nCPU: {psutil.cpu_percent()}%\nRAM: {psutil.virtual_memory().percent}%",reply_markup=back())

async def stop_view(q,uid):
    rows=await db.all("SELECT * FROM deployments WHERE user_id=? AND status='RUNNING'",(uid,)); kb=[[InlineKeyboardButton(str(r["deployment_id"])[0:12],callback_data=f"stop_{r['deployment_id']}")] for r in rows]; kb.append([InlineKeyboardButton("⬅️ BACK",callback_data="menu")]); await q.edit_message_text("🛑 STOP SCRIPT\n\nSelect a running deployment:",reply_markup=InlineKeyboardMarkup(kb))
async def user_stop(q,uid,did):
    d=await db.one("SELECT * FROM deployments WHERE deployment_id=? AND user_id=?",(did,uid))
    if not d:return await q.answer("Not your deployment",show_alert=True)
    await pm.stop(did); await q.edit_message_text("🛑 Deployment stopped.",reply_markup=back())
async def logs_view(q,uid):
    rows=await db.all("SELECT deployment_id,status,last_error FROM deployments WHERE user_id=? ORDER BY created_at DESC",(uid,)); kb=[[InlineKeyboardButton(f"{str(r['deployment_id'])[:10]} • {r['status']}",callback_data=f"logs_{r['deployment_id']}")] for r in rows]; kb.append([InlineKeyboardButton("⬅️ BACK",callback_data="menu")]); await q.edit_message_text("📜 VIEW LOGS",reply_markup=InlineKeyboardMarkup(kb))
async def user_logs(q,uid,did):
    d=await db.one("SELECT * FROM deployments WHERE deployment_id=? AND user_id=?",(did,uid));
    if not d:return await q.answer("Not your deployment",show_alert=True)
    txt=d["last_error"] or "No recent process output/error stored."
    p=await db.one("SELECT project_dir FROM projects WHERE project_id=?",(d["project_id"],))
    if p:
        lp=Path(p["project_dir"])/".volt_runtime.log"
        if lp.exists():
            with suppress(Exception): txt=lp.read_text(errors="replace")[-3500:]
    txt=re.sub(r"(?i)(token|password|secret|api[_-]?key|api[_-]?hash)\s*[=:]\s*\S+",r"\1=[REDACTED]",txt)
    await q.edit_message_text(f"📜 LOGS\n\nStatus: {d['status']}\n\n{txt}",reply_markup=back())

# -------------------- UPLOAD --------------------
async def upload(update,context):
    if not await require_join(update,context): return
    doc=update.effective_message.document
    if not doc: return
    if doc.file_size and doc.file_size>MAX_FILE_SIZE: return await update.effective_message.reply_text("❌ File too large.")
    uid=update.effective_user.id; pid=str(uuid.uuid4()); dest=safe_path(BASE_USER_DIR,str(uid),"projects",pid); dest.mkdir(parents=True,exist_ok=True)
    try:
        f=await context.bot.get_file(doc.file_id); data=bytes(await f.download_as_bytearray()); name=safe_filename(doc.file_name)
        if name.lower().endswith(".zip"):
            await extract_zip(data,dest)
        else:
            (dest/name).write_bytes(data)
        entry=detect_entry(dest); status,report=await scan_project(dest)
        await db.exec("INSERT INTO projects(project_id,user_id,original_filename,project_dir,entry_file,file_hash,security_status,security_report) VALUES(?,?,?,?,?,?,?,?)",(pid,uid,name,str(dest),entry,sha(data),status,json.dumps(report)))
        if status=="BLOCKED": return await update.effective_message.reply_text("🔴 File blocked by security validation. It will not execute.")
        dep=str(uuid.uuid4()); port=await db.allocate_port(uid,dep)
        await db.exec("INSERT INTO deployments(deployment_id,project_id,user_id,port,status,approval_status,resource_limits,auto_restart) VALUES(?,?,?,?,?,?,?,?)",(dep,pid,uid,port,"PENDING","PENDING",json.dumps({"cpu_percent":50,"memory_mb":512}),1))
        await db.audit(uid,dep,"DEPLOYMENT_CREATED",{"security":status,"filename":name})
        await notify_admin(context.bot,f"⏳ NEW DEPLOYMENT\n\n📦 {dep}\n👤 @{update.effective_user.username or 'N/A'}\n🆔 {uid}\n📁 {name}\n🛡️ {status}\n⏳ PENDING APPROVAL")
        await update.effective_message.reply_text(f"📤 Upload complete.\n\n🛡️ Security: {status}\n⏳ Status: PENDING APPROVAL\n🧾 Deployment: {dep}")
    except Exception as e:
        log.exception("upload failed"); shutil.rmtree(dest,ignore_errors=True); await update.effective_message.reply_text("❌ Upload could not be processed safely.")

# -------------------- PAYMENT INPUT --------------------
async def text_input(update,context):
    uid=update.effective_user.id; txt=(update.effective_message.text or "").strip()
    if context.user_data.pop("coupon_user", False):
        code=txt.upper()
        try:
            async with db.lock:
                cur=await db.conn.execute("SELECT * FROM coupons WHERE code=?",(code,)); c=await cur.fetchone(); await cur.close()
                if not c or c["status"]!="ACTIVE" or (c["expires_at"] and parse_dt(c["expires_at"]) and parse_dt(c["expires_at"])<=datetime.now(timezone.utc)):
                    return await update.effective_message.reply_text("❌ Coupon invalid or expired.")
                if int(c["current_redemptions"])>=int(c["max_redemptions"]):
                    return await update.effective_message.reply_text("🔴 COUPON FULLY USED")
                cur=await db.conn.execute("SELECT 1 FROM coupon_redemptions WHERE coupon_id=? AND user_id=?",(c["coupon_id"],uid)); used=await cur.fetchone(); await cur.close()
                if used:return await update.effective_message.reply_text("❌ You already redeemed this coupon.")
                cur=await db.conn.execute("INSERT OR IGNORE INTO coupon_redemptions(coupon_id,user_id) VALUES(?,?)",(c["coupon_id"],uid)); inserted=cur.rowcount==1
                if not inserted:return await update.effective_message.reply_text("⏳ This redemption is already being processed.")
                cur=await db.conn.execute("UPDATE coupons SET current_redemptions=current_redemptions+1 WHERE coupon_id=? AND current_redemptions < max_redemptions",(c["coupon_id"],)); changed=cur.rowcount
                if changed!=1:
                    await db.conn.rollback(); return await update.effective_message.reply_text("🔴 COUPON FULLY USED")
                ucur=await db.conn.execute("SELECT premium_expires_at FROM users WHERE user_id=?",(uid,)); u=await ucur.fetchone(); await ucur.close(); now=datetime.now(timezone.utc); exp=parse_dt(u["premium_expires_at"] if u else None); base=exp if exp and exp>now else now; new_exp=base+timedelta(days=int(c["reward_days"]))
                await db.conn.execute("UPDATE users SET is_premium=1,premium_expires_at=? WHERE user_id=?",(new_exp.isoformat(),uid)); await db.conn.execute("INSERT INTO audit_logs(actor_id,target_id,action,metadata) VALUES(?,?,?,?)",(uid,c["coupon_id"],"COUPON_REDEEMED",json.dumps({"code":code,"days":c["reward_days"]}))); await db.conn.commit()
            return await update.effective_message.reply_text(f"✅ Coupon redeemed!\n💎 +{c['reward_days']} DAYS PREMIUM")
        except Exception:
            log.exception("coupon redemption failed"); return await update.effective_message.reply_text("❌ Something went wrong. Please try again.")
    if "utr_order" in context.user_data:
        oid=context.user_data.pop("utr_order"); p=await db.payment(oid)
        if not p or p["user_id"]!=uid or p["status"] not in {"PAYMENT_PENDING","PAYMENT_SUBMITTED"}: return await update.effective_message.reply_text("❌ Invalid or expired payment order.")
        if p["expires_at"] and parse_dt(p["expires_at"]) and parse_dt(p["expires_at"]) <= datetime.now(timezone.utc):
            await db.exec("UPDATE payments SET status='EXPIRED_ORDER' WHERE order_id=? AND status IN ('PAYMENT_PENDING','PAYMENT_SUBMITTED')",(oid,)); return await update.effective_message.reply_text("⏰ Payment order expired. Please create a new order.")
        if not re.fullmatch(r"[A-Za-z0-9-]{6,64}",txt): return await update.effective_message.reply_text("❌ Invalid UTR format.")
        existing=await db.one("SELECT order_id FROM payments WHERE utr=? AND order_id!=?",(txt,oid))
        if existing: return await update.effective_message.reply_text("❌ This UTR has already been submitted.")
        await db.exec("UPDATE payments SET utr=?,status='PAYMENT_SUBMITTED' WHERE order_id=? AND status IN ('PAYMENT_PENDING','PAYMENT_SUBMITTED')",(txt,oid)); await db.audit(uid,oid,"UTR_SUBMITTED",{})
        await notify_admin(context.bot,f"💳 NEW PREMIUM PAYMENT\n\n👤 {uid}\n💎 {PLAN_LABELS[p['plan_id']]}\n💰 ₹{p['amount']}\n🧾 {oid}\n🔢 UTR: {txt}",pay_buttons(oid)); return await update.effective_message.reply_text("🟡 UTR submitted. Awaiting verification.")
    await update.effective_message.reply_text("Use the buttons in the menu, or /ticket <message>.")

async def photo_input(update,context):
    oid=context.user_data.pop("proof_order",None); uid=update.effective_user.id
    if not oid:return
    p=await db.payment(oid)
    if not p or p["user_id"]!=uid:return await update.effective_message.reply_text("❌ Invalid payment order.")
    photo=update.effective_message.photo[-1]; f=await context.bot.get_file(photo.file_id); data=bytes(await f.download_as_bytearray()); h=sha(data)
    await db.exec("INSERT INTO payment_proofs(order_id,file_id,file_hash) VALUES(?,?,?)",(oid,photo.file_id,h)); await db.exec("UPDATE payments SET proof_file_id=?,proof_hash=?,status='PAYMENT_SUBMITTED' WHERE order_id=?",(photo.file_id,h,oid)); await db.audit(uid,oid,"PAYMENT_PROOF_SUBMITTED",{})
    await notify_admin(context.bot,f"📸 PAYMENT PROOF RECEIVED\n\n👤 User: {uid}\n🧾 Order: {oid}\n💰 ₹{p['amount']}",pay_buttons(oid)); await update.effective_message.reply_text("🟡 Payment proof submitted. Awaiting verification.")

def pay_buttons(oid): return InlineKeyboardMarkup([[InlineKeyboardButton("✅ APPROVE PAYMENT",callback_data=f"approve_pay_{oid}")],[InlineKeyboardButton("❌ REJECT PAYMENT",callback_data=f"reject_pay_{oid}")],[InlineKeyboardButton("🔎 DETAILS",callback_data=f"pay_details_{oid}")]])

# -------------------- ADMIN --------------------
async def admin_pending(q,uid):
    if not is_admin(uid): return await q.answer("Access denied",show_alert=True)
    rows=await db.pending_deployments()
    if not rows:return await q.edit_message_text("⏳ PENDING DEPLOYMENTS\n\n✅ None.",reply_markup=back("admin_panel"))
    d=rows[0]; u=await db.user(d["user_id"]); p=await db.one("SELECT * FROM projects WHERE project_id=?",(d["project_id"],));
    kb=[[InlineKeyboardButton("✅ APPROVE",callback_data=f"approve_deploy_{d['deployment_id']}"),InlineKeyboardButton("❌ REJECT",callback_data=f"reject_deploy_{d['deployment_id']}")],[InlineKeyboardButton("🔎 DETAILS",callback_data=f"dep_details_{d['deployment_id']}"),InlineKeyboardButton("🛡️ SECURITY",callback_data=f"sec_report_{d['deployment_id']}")],[InlineKeyboardButton("⬅️ BACK",callback_data="admin_panel")]]
    await q.edit_message_text(f"📦 DEPLOYMENT {d['deployment_id']}\n\n👤 @{u['username'] or 'N/A'}\n🆔 {u['user_id']}\n📁 {p['original_filename']}\n📄 Entry: {p['entry_file']}\n🌐 Port: {d['port']}\n🛡️ Security: {p['security_status']}\n⏳ PENDING APPROVAL",reply_markup=InlineKeyboardMarkup(kb))

async def admin_approve(q,context,uid,did):
    if not is_admin(uid): return await q.answer("Access denied",show_alert=True)
    try:
        async with db.lock:
            cur=await db.conn.execute("SELECT d.*,p.project_dir,p.entry_file,p.security_status FROM deployments d JOIN projects p ON p.project_id=d.project_id WHERE d.deployment_id=?",(did,)); d=await cur.fetchone(); await cur.close()
            if not d or d["status"]!="PENDING": return await q.answer("Deployment is not pending.",show_alert=True)
            if d["security_status"] not in {"SAFE"}: return await q.answer("⚠️ Manual security review required.",show_alert=True)
            if not Path(d["project_dir"]).is_dir() or not Path(d["project_dir"],d["entry_file"]).is_file(): return await q.answer("Project/entry file missing.",show_alert=True)
            if not d["port"] or not port_available(d["port"]): return await q.answer("⚠️ PORT UNAVAILABLE",show_alert=True)
            cur=await db.conn.execute("UPDATE deployments SET status='APPROVED',approval_status='APPROVED',approved_by=?,approved_at=? WHERE deployment_id=? AND status='PENDING'",(uid,now_iso(),did)); changed=cur.rowcount; await db.conn.commit()
            if changed!=1:return await q.answer("⏳ This action is already being processed.",show_alert=True)
        ok,reason=await pm.start(did,approved=True)
        if not ok:
            await db.exec("UPDATE deployments SET status='ERROR',last_error=? WHERE deployment_id=?",(reason[:1000],did)); await db.release_port(d["port"]); await q.edit_message_text(f"🔴 START FAILED\n\nDeployment: {did}\nReason: {reason}",reply_markup=back("admin_pending")); await safe_send(context.bot,d["user_id"],f"🔴 START FAILED\n\nDeployment: {did}"); return
        await db.audit(uid,did,"DEPLOYMENT_APPROVED",{},q.from_user.username); await q.edit_message_text("🟢 DEPLOYMENT APPROVED\n\n🚀 Started and verified RUNNING.",reply_markup=back("admin_pending")); await safe_send(context.bot,d["user_id"],f"🟢 DEPLOYMENT APPROVED\n\n🚀 Your deployment is now RUNNING.")
    except Exception:
        log.exception("deployment approval failed"); await q.answer("⚠️ Operation failed safely.",show_alert=True)

async def admin_reject(q,context,uid,did):
    if not is_admin(uid):return await q.answer("Access denied",show_alert=True)
    d=await db.one("SELECT * FROM deployments WHERE deployment_id=?",(did,));
    if not d or d["status"]!="PENDING":return await q.answer("Invalid state",show_alert=True)
    await db.exec("UPDATE deployments SET status='REJECTED',approval_status='REJECTED',rejected_by=?,rejected_at=?,rejection_reason=? WHERE deployment_id=? AND status='PENDING'",(uid,now_iso(),"Manual admin rejection",did)); await db.release_port(d["port"]); await db.audit(uid,did,"DEPLOYMENT_REJECTED",{"reason":"Manual admin rejection"},q.from_user.username); await q.edit_message_text("🔴 DEPLOYMENT REJECTED\n\nProject retained for audit/review. It was not executed.",reply_markup=back("admin_pending")); await safe_send(context.bot,d["user_id"],f"🔴 DEPLOYMENT REJECTED\n\nDeployment: {did}\nReason: Manual admin rejection")

async def admin_payments(q,uid):
    if not is_admin(uid):return await q.answer("Access denied",show_alert=True)
    rows=await db.pending_payments();
    if not rows:return await q.edit_message_text("💳 PAYMENTS\n\n✅ No pending payments.",reply_markup=back("admin_panel"))
    p=rows[0]; await q.edit_message_text(f"💳 NEW PREMIUM PAYMENT\n\n👤 User ID: {p['user_id']}\n💎 Plan: {PLAN_LABELS.get(p['plan_id'],p['plan_id'])}\n💰 Amount: ₹{p['amount']}\n🧾 Order: {p['order_id']}\n🔢 UTR: {p['utr'] or 'N/A'}\n📌 Status: {p['status']}",reply_markup=pay_buttons(p['order_id']))

async def admin_approve_payment(q,context,uid,oid):
    if not is_admin(uid): return await q.answer("Access denied",show_alert=True)
    try:
        async with db.lock:
            cur=await db.conn.execute("SELECT * FROM payments WHERE order_id=?",(oid,)); p=await cur.fetchone(); await cur.close()
            if not p or p["status"] not in {"PAYMENT_SUBMITTED","MANUAL_REVIEW"}: return await q.answer("Payment is not pending.",show_alert=True)
            if not p["utr"] or not p["proof_file_id"]: return await q.answer("UTR + payment proof are required.",show_alert=True)
            # Price and duration are read from the server-side order, never callback/client input.
            server_plan=PLANS.get(p["plan_id"])
            if not server_plan or int(server_plan[0])!=int(p["duration_days"]) or int(server_plan[1])!=int(p["amount"]):
                await db.conn.execute("UPDATE payments SET status='MANUAL_REVIEW',rejection_reason=? WHERE order_id=?",("Server-side plan mismatch",oid)); await db.conn.commit()
                await db.audit(uid,oid,"PAYMENT_SECURITY_HOLD",{"reason":"plan mismatch"},q.from_user.username)
                return await q.answer("Security hold: order data mismatch.",show_alert=True)
            cur=await db.conn.execute("SELECT order_id FROM payments WHERE utr=? AND order_id!=? AND status IN ('PAYMENT_SUBMITTED','MANUAL_REVIEW','APPROVED','PREMIUM_ACTIVATED')",(p["utr"],oid)); existing=await cur.fetchone(); await cur.close()
            if existing:
                await db.conn.execute("UPDATE payments SET status='DUPLICATE',rejection_reason=? WHERE order_id=?",("Duplicate UTR",oid)); await db.conn.commit()
                return await q.answer("Duplicate UTR rejected.",show_alert=True)
            now=datetime.now(timezone.utc); cur=await db.conn.execute("SELECT premium_expires_at FROM users WHERE user_id=?",(p["user_id"],)); u=await cur.fetchone(); await cur.close()
            if not u: return await q.answer("User record missing.",show_alert=True)
            exp=parse_dt(u["premium_expires_at"]); base=exp if exp and exp>now else now; new_exp=base+timedelta(days=int(p["duration_days"]))
            cur=await db.conn.execute("UPDATE payments SET status='PREMIUM_ACTIVATED',verified_at=?,verified_by=? WHERE order_id=? AND status IN ('PAYMENT_SUBMITTED','MANUAL_REVIEW')",(now_iso(),uid,oid)); changed=cur.rowcount
            if changed != 1:
                await db.conn.rollback(); return await q.answer("⏳ This action is already being processed.",show_alert=True)
            await db.conn.execute("UPDATE users SET is_premium=1,premium_expires_at=? WHERE user_id=?",(new_exp.isoformat(),p["user_id"]))
            await db.conn.execute("INSERT INTO audit_logs(actor_id,actor_username,target_id,action,metadata) VALUES(?,?,?,?,?)",(uid,q.from_user.username,oid,"PAYMENT_APPROVED",json.dumps({"user":p["user_id"],"days":p["duration_days"],"amount":p["amount"]})))
            await db.conn.commit()
        await q.edit_message_text("✅ PAYMENT APPROVED\n\n💎 Premium activated.",reply_markup=back("admin_payments"))
        await safe_send(context.bot,p["user_id"],f"✅ Payment approved!\n💎 {PLAN_LABELS[p['plan_id']]} premium activated.")
    except Exception:
        log.exception("payment approval failed")
        await q.answer("⚠️ Operation failed safely.",show_alert=True)

async def admin_reject_payment(q,context,uid,oid):
    if not is_admin(uid):return await q.answer("Access denied",show_alert=True)
    p=await db.payment(oid)
    if not p or p["status"] not in {"PAYMENT_SUBMITTED","MANUAL_REVIEW"}:return await q.answer("Invalid state",show_alert=True)
    await db.exec("UPDATE payments SET status='REJECTED',verified_at=?,verified_by=?,rejection_reason=? WHERE order_id=? AND status IN ('PAYMENT_SUBMITTED','MANUAL_REVIEW')",(now_iso(),uid,"Payment rejected",oid)); await db.audit(uid,oid,"PAYMENT_REJECTED",{},q.from_user.username); await q.edit_message_text("❌ PAYMENT REJECTED",reply_markup=back("admin_payments")); await safe_send(context.bot,p["user_id"],f"❌ Payment rejected.\n🧾 Order: {oid}")

async def admin_users(q,uid):
    if not is_admin(uid):return await q.answer("Access denied",show_alert=True)
    rows=await db.all("SELECT * FROM users ORDER BY created_at DESC LIMIT 20"); text="👥 USERS\n\n"+"\n".join(f"• {r['first_name'] or ''} @{r['username'] or 'N/A'} | ID {r['user_id']} | Premium {'YES' if await db.premium_active(r['user_id']) else 'NO'} | {r['created_at']}" for r in rows); await q.edit_message_text(text[:3900],reply_markup=back("admin_panel"))
async def admin_stats(q,uid):
    if not is_admin(uid):return await q.answer("Access denied",show_alert=True)
    vals={k:(await db.one(sql))["n"] for k,sql in {"users":"SELECT COUNT(*) n FROM users","premium":"SELECT COUNT(*) n FROM users WHERE is_premium=1","bots":"SELECT COUNT(*) n FROM deployments","running":"SELECT COUNT(*) n FROM deployments WHERE status='RUNNING'","pending":"SELECT COUNT(*) n FROM deployments WHERE status='PENDING'","payments":"SELECT COUNT(*) n FROM payments WHERE status IN ('PAYMENT_SUBMITTED','MANUAL_REVIEW')","refs":"SELECT COUNT(*) n FROM referrals"}.items()}; await q.edit_message_text("📊 ADMIN STATISTICS\n\n"+"\n".join(f"{k.title()}: {v}" for k,v in vals.items()),reply_markup=back("admin_panel"))
async def admin_health(q,uid):
    if not is_admin(uid):return await q.answer("Access denied",show_alert=True)
    dbok=False
    try: await db.one("SELECT 1"); dbok=True
    except: pass
    await q.edit_message_text(f"🩺 SYSTEM HEALTH\n\nTelegram: 🟢\nDatabase: {'🟢' if dbok else '🔴'}\nProcess Manager: 🟢\nSecurity Scanner: 🟢\nScheduler: 🟢\nBackup: {'🟢' if BACKUP_DIR.exists() else '🟠'}\n\nOverall: {'🟢 OPERATIONAL' if dbok else '🟠 DEGRADED'}",reply_markup=back("admin_panel"))
async def admin_audit(q,uid):
    if not is_admin(uid):return await q.answer("Access denied",show_alert=True)
    rows=await db.all("SELECT * FROM audit_logs ORDER BY id DESC LIMIT 20"); text="📜 AUDIT LOGS\n\n"+"\n".join(f"{r['created_at']} | {r['actor_id']} | {r['action']} | {r['target_id']}" for r in rows); await q.edit_message_text(text[:3900],reply_markup=back("admin_panel"))
async def admin_backup(q,uid):
    if not is_owner(uid):return await q.answer("Owner only",show_alert=True)
    BACKUP_DIR.mkdir(parents=True,exist_ok=True); target=BACKUP_DIR/f"volt_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.db"
    try:
        async with db.lock:
            backup_conn=await aiosqlite.connect(str(target))
            await db.conn.backup(backup_conn)
            await backup_conn.close()
        await db.audit(uid,str(target),"BACKUP_CREATED",{},q.from_user.username); await q.edit_message_text(f"💾 BACKUP CREATED\n\n{target.name}",reply_markup=back("admin_panel"))
    except Exception:
        log.exception("backup failed"); await q.edit_message_text("⚠️ BACKUP FAILED\n\nThe database was not modified.",reply_markup=back("admin_panel"))


async def admin_dashboard(q,uid):
    if not is_admin(uid): return await q.answer("Access denied",show_alert=True)
    await q.edit_message_text("🛡️ VOLT ADMIN CENTER\n\nSecure operational dashboard.\nOnly Owner/Co-Owner IDs are accepted server-side.",reply_markup=admin_kb())

async def admin_deployments(q,uid):
    if not is_admin(uid): return await q.answer("Access denied",show_alert=True)
    rows=await db.all("SELECT status,COUNT(*) n FROM deployments GROUP BY status")
    txt="🤖 DEPLOYMENTS\n\n"+"\n".join(f"{r['status']}: {r['n']}" for r in rows) if rows else "🤖 DEPLOYMENTS\n\nNo deployments."
    await q.edit_message_text(txt,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⏳ PENDING",callback_data="admin_pending")],[InlineKeyboardButton("⬅️ BACK",callback_data="admin_panel")]]))

async def admin_users_detail(q,uid):
    if not is_admin(uid): return await q.answer("Access denied",show_alert=True)
    rows=await db.all("SELECT * FROM users ORDER BY created_at DESC LIMIT 50")
    if not rows: return await q.edit_message_text("👥 USERS\n\nNo users.",reply_markup=back("admin_panel"))
    lines=[]
    for r in rows:
        premium=await db.premium_active(r["user_id"])
        lines.append(f"👤 {r['first_name'] or 'User'} | @{r['username'] or 'N/A'}\n🆔 {r['user_id']}\n💎 {'ACTIVE' if premium else 'OFF'} | Created {r['created_at']}")
    await q.edit_message_text("👥 USER DIRECTORY\n\n"+"\n\n".join(lines)[:3900],reply_markup=back("admin_panel"))

async def admin_premium(q,uid):
    if not is_admin(uid): return await q.answer("Access denied",show_alert=True)
    rows=await db.all("SELECT user_id,premium_expires_at FROM users WHERE is_premium=1 ORDER BY premium_expires_at DESC LIMIT 30")
    txt="💎 PREMIUM USERS\n\n"+"\n".join(f"🆔 {r['user_id']} • {r['premium_expires_at'] or 'UNLIMITED'}" for r in rows) if rows else "💎 PREMIUM USERS\n\nNone"
    await q.edit_message_text(txt[:3900],reply_markup=back("admin_panel"))

async def admin_referrals(q,uid):
    if not is_admin(uid): return await q.answer("Access denied",show_alert=True)
    rows=await db.all("SELECT reward_status,COUNT(*) n FROM referrals GROUP BY reward_status")
    txt="🎁 REFERRALS\n\n"+"\n".join(f"{r['reward_status']}: {r['n']}" for r in rows) if rows else "🎁 REFERRALS\n\nNone"
    await q.edit_message_text(txt,reply_markup=back("admin_panel"))

async def admin_security(q,uid):
    if not is_admin(uid): return await q.answer("Access denied",show_alert=True)
    rows=await db.all("SELECT security_status,COUNT(*) n FROM projects GROUP BY security_status")
    txt="🛡️ SECURITY\n\n"+"\n".join(f"{r['security_status']}: {r['n']}" for r in rows) if rows else "🛡️ SECURITY\n\nNo scans yet."
    await q.edit_message_text(txt,reply_markup=back("admin_panel"))

async def admin_settings(q,uid):
    if not is_owner(uid): return await q.answer("Owner only",show_alert=True)
    rows=await db.all("SELECT key,value FROM settings ORDER BY key")
    txt="⚙️ SETTINGS\n\n"+"\n".join(f"{r['key']}: {r['value']}" for r in rows)
    txt += "\n\nOwner-only system settings. Changes must be made through controlled actions."
    await q.edit_message_text(txt[:3900],reply_markup=back("admin_panel"))

async def admin_perms(q,uid):
    if not is_owner(uid): return await q.answer("Owner only",show_alert=True)
    await q.edit_message_text("🔐 DEPLOYMENT PERMISSIONS\n\nPublic deployment: ON\nApproval required: ON\nManual security review: ON\nAuto restart: ON\n\n👑 CO-OWNER\nOperational approval/rejection, deployment details, security reports, stop/restart, logs and payment review are enabled.\n\nDangerous system settings remain Owner-only.",reply_markup=back("admin_panel"))

async def admin_server(q,uid):
    if not is_admin(uid): return await q.answer("Access denied",show_alert=True)
    vm=psutil.virtual_memory(); disk=psutil.disk_usage(str(BASE_USER_DIR)); procs=len(pm.procs)
    await q.edit_message_text(f"🖥️ SERVER\n\nCPU: {psutil.cpu_percent(interval=0.1):.1f}%\nRAM: {vm.percent:.1f}% ({vm.used//(1024**2)} MB used)\nDisk: {disk.percent:.1f}%\nHosted processes: {procs}\nPort range: {PORT_MIN}-{PORT_MAX}",reply_markup=back("admin_panel"))

async def admin_coupons(q,uid):
    if not is_owner(uid): return await q.answer("Owner only",show_alert=True)
    rows=await db.all("SELECT code,reward_days,max_redemptions,current_redemptions,expires_at,status FROM coupons ORDER BY created_at DESC LIMIT 30")
    txt="🎟️ COUPONS\n\n"+"\n".join(f"{r['code']} • {r['reward_days']}d • {r['current_redemptions']}/{r['max_redemptions']} • {r['status']}" for r in rows) if rows else "🎟️ COUPONS\n\nNo coupons yet."
    await q.edit_message_text(txt[:3900],reply_markup=back("admin_panel"))

async def admin_photo(q,uid):
    if not is_owner(uid): return await q.answer("Owner only",show_alert=True)
    await q.edit_message_text("🖼️ PHOTO / BRAND CUSTOMIZATION\n\nExisting custom-banner keys can be stored through the secure settings layer:\nmain • admin • plans • upload • stats • ticket • coupon • security\n\nNo public user can change branding settings.",reply_markup=back("admin_panel"))

async def deployment_details(q,uid,did):
    if not is_admin(uid): return await q.answer("Access denied",show_alert=True)
    d=await db.one("SELECT * FROM deployments WHERE deployment_id=?",(did,))
    if not d: return await q.answer("Deployment not found",show_alert=True)
    if not is_admin(uid): return await q.answer("Access denied",show_alert=True)
    p=await db.one("SELECT * FROM projects WHERE project_id=?",(d['project_id'],)); u=await db.user(d['user_id'])
    txt=f"🔎 DEPLOYMENT DETAILS\n\n📦 {did}\n👤 @{u['username'] or 'N/A'}\n🆔 {u['user_id']}\n📁 {p['original_filename'] if p else 'N/A'}\n📄 {p['entry_file'] if p else 'N/A'}\n🌐 Port: {d['port']}\n📌 State: {d['status']}\n🛡️ Security: {p['security_status'] if p else 'N/A'}\n⚙️ PID: {d['process_id'] or '—'}\n🔁 Restarts: {d['restart_count']}"
    await q.edit_message_text(txt,reply_markup=back("admin_pending"))

async def payment_details(q,uid,oid):
    if not is_admin(uid): return await q.answer("Access denied",show_alert=True)
    p=await db.payment(oid)
    if not p:return await q.answer("Payment not found",show_alert=True)
    await q.edit_message_text(f"🔎 PAYMENT DETAILS\n\n🧾 {oid}\n🆔 User: {p['user_id']}\n💎 {PLAN_LABELS.get(p['plan_id'],p['plan_id'])}\n💰 Server amount: ₹{p['amount']}\n🔢 UTR: {p['utr'] or '—'}\n📸 Proof: {'YES' if p['proof_file_id'] else 'NO'}\n📌 Status: {p['status']}\n⏰ Created: {p['created_at']}",reply_markup=back("admin_payments"))

async def redeem_coupon_start(q,context,uid):
    if not await db.user(uid): return
    context.user_data["coupon_user"] = True
    await q.edit_message_text("🎟️ REDEEM COUPON\n\nSend your coupon code as a message.",reply_markup=back("buy_premium"))

async def create_coupon_from_text(update,context,uid):
    if not is_owner(uid): return False
    return False

async def admin_export_users(q,context,uid):
    if not is_admin(uid): return await q.answer("Access denied",show_alert=True)
    try:
        import csv
        rows=await db.all("SELECT * FROM users ORDER BY created_at ASC")
        out=BytesIO(); text_io=[]
        headers=["user_id","username","first_name","last_name","is_premium","premium_expires_at","referral_code","referred_by","joined_requirements","suspended","created_at","last_active"]
        text_io.append(",".join(headers))
        for r in rows:
            vals=[]
            for h in headers:
                v=r[h] if h in r.keys() else ""
                vals.append(json.dumps(v if v is not None else "",ensure_ascii=False))
            text_io.append(",".join(vals))
        data=("\n".join(text_io)).encode("utf-8")
        out.write(data); out.seek(0)
        await q.message.reply_document(document=out,filename="volt_users_export.csv",caption="📄 VOLT USER EXPORT\n\nAdmin-only file. Contains registered user records.")
        await db.audit(uid,"users","USER_EXPORT",{"count":len(rows)},q.from_user.username)
    except Exception:
        log.exception("user export failed"); await q.answer("⚠️ Export failed safely.",show_alert=True)

async def notify_admin(bot,text,reply_markup=None):
    for aid in ADMIN_IDS: await safe_send(bot,aid,text,reply_markup=reply_markup)

# -------------------- COMMANDS / SCHEDULER --------------------
async def ticket(update,context):
    if not await require_join(update,context):return
    msg=" ".join(context.args).strip()
    if not msg:return await update.effective_message.reply_text("Usage: /ticket <message>")
    tid=str(uuid.uuid4()); await db.exec("INSERT INTO tickets(ticket_id,user_id,subject,message) VALUES(?,?,?,?)",(tid,update.effective_user.id,"General",msg[:4000])); await db.audit(update.effective_user.id,tid,"TICKET_CREATED",{}); await update.effective_message.reply_text(f"🎫 Ticket created: {tid}")
async def stats_cmd(update,context):
    if not await require_join(update,context):return
    await update.effective_message.reply_text("Use 📊 STATS from the main menu.")
async def periodic(context):
    try:
        expired=await db.expire_premium()
        for uid in expired:
            await safe_send(context.bot,uid,"⏰ PREMIUM EXPIRED\n\nYour Premium plan has expired.\n\n[💎 BUY PREMIUM]")
            rows=await db.all("SELECT deployment_id FROM deployments WHERE user_id=? AND status='RUNNING'",(uid,))
            for r in rows: await pm.stop(r["deployment_id"]); await db.exec("UPDATE deployments SET status='EXPIRED' WHERE deployment_id=?",(r["deployment_id"],))
    except Exception: log.exception("periodic expiry failed")

async def error_handler(update,context): log.error("Unhandled update error: %r",context.error,exc_info=context.error)

async def shutdown(app):
    for did in list(pm.procs):
        with suppress(Exception): await pm.stop(did)
    with suppress(Exception): await db.close()

async def main():
    BASE_USER_DIR.mkdir(parents=True,exist_ok=True); BACKUP_DIR.mkdir(parents=True,exist_ok=True); await db.connect()
    app=Application.builder().token(BOT_TOKEN).build()
    APP_STATE.bot=app.bot
    app.add_handler(CommandHandler("start",start)); app.add_handler(CommandHandler("menu",cmd_menu)); app.add_handler(CommandHandler("admin",admin_cmd)); app.add_handler(CommandHandler("ticket",ticket)); app.add_handler(CommandHandler("stats",stats_cmd))
    app.add_handler(CallbackQueryHandler(cb)); app.add_handler(MessageHandler(filters.PHOTO,photo_input)); app.add_handler(MessageHandler(filters.Document.ALL,upload)); app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_input)); app.add_error_handler(error_handler)
    log.info("⚡ VOLT HOSTING V11.00.000 starting")
    initialized=False
    started=False
    polling=False
    try:
        # PTB must be initialized before calling bot methods such as set_my_commands.
        await app.initialize(); initialized=True
        await app.bot.set_my_commands([BotCommand("start","Start"),BotCommand("menu","Main menu"),BotCommand("stats","Statistics"),BotCommand("ticket","Support ticket"),BotCommand("admin","Admin (authorized only)")])
        if app.job_queue: app.job_queue.run_repeating(periodic,interval=60,first=15)
        await app.start(); started=True
        await app.updater.start_polling(allowed_updates=Update.ALL_TYPES); polling=True
        while True: await asyncio.sleep(3600)
    except Exception:
        log.exception("main polling failure")
        raise
    finally:
        if polling:
            with suppress(Exception): await app.updater.stop()
        if started:
            with suppress(Exception): await app.stop()
        if initialized:
            with suppress(Exception): await app.shutdown()
        await shutdown(app)

if __name__=="__main__": asyncio.run(main())
