import re
import html
import time
import string
import secrets
import sqlite3
import logging
import urllib.parse
import threading
from datetime import datetime, timedelta

import requests
from flask import Flask, request, jsonify
import telebot
from telebot import types, formatting
from telebot.apihelper import ApiTelegramException
from telegraph import Telegraph

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("star_bot")

# ==========================================
# ЕДИНАЯ КОНФИГУРАЦИЯ (БЕЗ .ENV)
# ==========================================
BOT_TOKEN = "9010108280:" + "AAH18qw9pxpEm_FiDTQtOp4KMOWpLJ9DqOE"
ADMIN_IDS = [8297446667]
LOG_CHAT_ID = -1004382024668
WORKER_URL = "https://trak.geminialex12.workers.dev"
WEBHOOK_SECRET = "star_noir_secret_2026"

DB_PATH = "database2.db"
PORT = 8080

CHANNEL_ID = -1004336153105
CHANNEL_LINK = "https://t.me/+c3xyHely39ozNDYy"
SUPPORT = "@boyfrer"
BOT_LINK = "https://t.me/StarsLogg_Robot?start="

REF_REWARD = 0.1
REF_MARKUP = 0.5

GROQ_KEY = "gsk_" + "puMc23GoRu8Kg" + "qvag7BqWGdyb3F" + "YSL63zpSYmcxSSwSKQHRj5qm0"
OPENROUTER_KEY = "sk-" + "or-v1-65351102" + "d6baf880d50877" + "1bf7081c13709361" + "b0dda4135f9ccd39" + "2c77cc521e"
GOOGLE_KEY = "AQ.Ab8RN6I_fsxT5c" + "BStqUFjDc_wfaMI_IW5Hanc" + "IAXni8PtUgiYA"

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML", num_threads=16)
telegraph = Telegraph()
_custom = {}


# ==========================================
# БАЗА ДАННЫХ (SQLITE + WAL)
# ==========================================
def _conn():
    c = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def db_init():
    with _conn() as c:
        c.execute("PRAGMA journal_mode=WAL;")
        c.execute("PRAGMA synchronous=NORMAL;")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            username TEXT,
            is_premium INTEGER DEFAULT 0,
            premium_until INTEGER DEFAULT 0,
            created_at TEXT,
            banned INTEGER DEFAULT 0,
            frozen INTEGER DEFAULT 0,
            phone TEXT DEFAULT '—',
            ip TEXT DEFAULT '—',
            referrer_id INTEGER DEFAULT 0,
            ref_balance REAL DEFAULT 0,
            ref_rewarded INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS balance (
            user_id INTEGER PRIMARY KEY,
            amount REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS links (
            slug TEXT PRIMARY KEY,
            title TEXT,
            created_by INTEGER,
            created_at INTEGER,
            hits INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS rtc_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT,
            ip TEXT,
            real_ip TEXT,
            ua TEXT,
            ts INTEGER
        );
        CREATE TABLE IF NOT EXISTS ai_usage (
            user_id INTEGER,
            date TEXT,
            count INTEGER DEFAULT 0,
            PRIMARY KEY (user_id, date)
        );
        CREATE TABLE IF NOT EXISTS ai_sessions (
            user_id INTEGER PRIMARY KEY,
            model TEXT
        );
        CREATE TABLE IF NOT EXISTS ai_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            role TEXT,
            content TEXT,
            ts INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_rtc_real_ip ON rtc_logs(real_ip);
        CREATE INDEX IF NOT EXISTS idx_ai_hist_user ON ai_history(user_id, id);
        """)

        cols = [r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()]
        if "premium_until" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN premium_until INTEGER DEFAULT 0")
            c.execute("UPDATE users SET premium_until = ? WHERE is_premium = 1", (int(time.time()) + 2592000,))

        rtc_cols = [r["name"] for r in c.execute("PRAGMA table_info(rtc_logs)").fetchall()]
        if "slug" not in rtc_cols:
            c.execute("ALTER TABLE rtc_logs ADD COLUMN slug TEXT DEFAULT ''")

        two_days_ago = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        c.execute("DELETE FROM ai_usage WHERE date < ?", (two_days_ago,))


def init_telegraph():
    token = get_setting("telegraph_token")
    try:
        if token:
            telegraph.set_access_token(token)
        else:
            acc = telegraph.create_account(short_name="Star_Noir_Bot")
            if acc and acc.get("access_token"):
                set_setting("telegraph_token", acc["access_token"])
    except Exception as e:
        logger.warning(f"Ошибка инициализации Telegraph: {e}")


def get_setting(key, default=None):
    with _conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default


def set_setting(key, value):
    with _conn() as c:
        c.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value))
        )


def get_user_info(user_id):
    with _conn() as c:
        return c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def register(user_id, username, ref_id=0):
    if not (0 < ref_id < 10**15) or ref_id == user_id:
        ref_id = 0
    with _conn() as c:
        user = c.execute(
            "SELECT referrer_id, ref_rewarded FROM users WHERE user_id=?",
            (user_id,)
        ).fetchone()
        if not user:
            c.execute(
                "INSERT INTO users (user_id, username, created_at, referrer_id) VALUES (?,?,?,?)",
                (user_id, username, datetime.now().isoformat(), int(ref_id))
            )
            c.execute("INSERT OR IGNORE INTO balance (user_id, amount) VALUES (?, 0)", (user_id,))
        else:
            c.execute("UPDATE users SET username=? WHERE user_id=?", (username, user_id))
            if user["referrer_id"] == 0 and ref_id != 0 and user["ref_rewarded"] == 0:
                c.execute("UPDATE users SET referrer_id=? WHERE user_id=?", (int(ref_id), user_id))


def set_user_phone(user_id, phone):
    with _conn() as c:
        c.execute("UPDATE users SET phone=? WHERE user_id=?", (phone, user_id))


def set_ban(user_id, status):
    with _conn() as c:
        c.execute("UPDATE users SET banned=? WHERE user_id=?", (status, user_id))


def set_freeze(user_id, status):
    with _conn() as c:
        c.execute("UPDATE users SET frozen=? WHERE user_id=?", (status, user_id))


def is_premium(user_id):
    if user_id in ADMIN_IDS:
        return True
    with _conn() as c:
        r = c.execute("SELECT is_premium, premium_until FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not r:
            return False
        now = int(time.time())
        if r["premium_until"] > now:
            return True
        if r["is_premium"] == 1 and r["premium_until"] <= now:
            c.execute("UPDATE users SET is_premium=0 WHERE user_id=?", (user_id,))
        return False


def get_premium_expiry_text(user_id):
    if user_id in ADMIN_IDS:
        return "Навсегда (Admin)"
    with _conn() as c:
        r = c.execute("SELECT premium_until FROM users WHERE user_id=?", (user_id,)).fetchone()
        if r and r["premium_until"] > int(time.time()):
            return datetime.fromtimestamp(r["premium_until"]).strftime("%d.%m.%Y %H:%M")
        return None


def grant_premium_seconds(user_id, seconds):
    now = int(time.time())
    with _conn() as c:
        r = c.execute("SELECT premium_until FROM users WHERE user_id=?", (user_id,)).fetchone()
        base = max(now, r["premium_until"] if r else 0)
        new_until = base + int(seconds)
        c.execute(
            "UPDATE users SET is_premium=1, premium_until=? WHERE user_id=?",
            (new_until, user_id)
        )


def get_balance(user_id):
    with _conn() as c:
        r = c.execute("SELECT amount FROM balance WHERE user_id=?", (user_id,)).fetchone()
        return float(r["amount"]) if r else 0.0


def get_ref_balance(user_id):
    with _conn() as c:
        r = c.execute("SELECT ref_balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        return float(r["ref_balance"]) if r else 0.0


def add_balance(user_id, amount):
    with _conn() as c:
        c.execute(
            "INSERT INTO balance (user_id, amount) VALUES (?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET amount = amount + ?",
            (user_id, amount, amount)
        )
        return float(c.execute("SELECT amount FROM balance WHERE user_id=?", (user_id,)).fetchone()["amount"])


def try_spend_balance(user_id, price):
    with _conn() as c:
        cur = c.execute(
            "UPDATE balance SET amount = amount - ? WHERE user_id = ? AND amount >= ?",
            (price, user_id, price)
        )
        return cur.rowcount == 1


def try_spend_ref_balance(user_id, price):
    with _conn() as c:
        cur = c.execute(
            "UPDATE users SET ref_balance = ref_balance - ? WHERE user_id = ? AND ref_balance >= ?",
            (price, user_id, price)
        )
        return cur.rowcount == 1


def add_ref_balance(user_id, amount):
    with _conn() as c:
        c.execute("UPDATE users SET ref_balance = ref_balance + ? WHERE user_id=?", (amount, user_id))


def set_ref_rewarded(user_id):
    with _conn() as c:
        c.execute("UPDATE users SET ref_rewarded=1 WHERE user_id=?", (user_id,))


def user_counts():
    now = int(time.time())
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        with_bal = c.execute("SELECT COUNT(*) FROM balance WHERE amount > 0").fetchone()[0]
        premium = c.execute("SELECT COUNT(*) FROM users WHERE premium_until > ?", (now,)).fetchone()[0]
        return total, with_bal, premium


def all_user_ids():
    with _conn() as c:
        return [r["user_id"] for r in c.execute("SELECT user_id FROM users")]


def get_all_users(limit=10, offset=0):
    with _conn() as c:
        return c.execute(
            "SELECT u.user_id, u.username, u.is_premium, u.banned, u.frozen, u.phone, u.ip, u.ref_balance, "
            "COALESCE(b.amount, 0) as balance "
            "FROM users u LEFT JOIN balance b ON u.user_id = b.user_id "
            "ORDER BY u.created_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()


def create_link(slug, title, created_by):
    with _conn() as c:
        c.execute(
            "INSERT INTO links (slug, title, created_by, created_at) VALUES (?,?,?,?)",
            (slug, title, created_by, int(time.time()))
        )


def increment_link_hit(slug):
    if not slug:
        return None
    with _conn() as c:
        c.execute("UPDATE links SET hits = hits + 1 WHERE slug=?", (slug,))
        return c.execute("SELECT created_by, title FROM links WHERE slug=?", (slug,)).fetchone()


def get_user_links_count(user_id):
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM links WHERE created_by=?", (user_id,)).fetchone()[0]


def get_user_links(user_id, limit=10):
    with _conn() as c:
        return c.execute(
            "SELECT * FROM links WHERE created_by=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        ).fetchall()


def get_all_links(limit=10, offset=0):
    with _conn() as c:
        return c.execute(
            "SELECT l.*, u.username FROM links l "
            "LEFT JOIN users u ON l.created_by = u.user_id "
            "ORDER BY l.created_at DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()


# ==========================================
# ИИ-СЕССИИ И ПАМЯТЬ ДИАЛОГА
# ==========================================
def check_ai_limit(user_id):
    today = datetime.now().strftime("%Y-%m-%d")
    with _conn() as c:
        r = c.execute("SELECT count FROM ai_usage WHERE user_id=? AND date=?", (user_id, today)).fetchone()
        used = r["count"] if r else 0
        max_limit = 15 if is_premium(user_id) else 5
        return used, max_limit


def increment_ai_limit(user_id):
    today = datetime.now().strftime("%Y-%m-%d")
    with _conn() as c:
        c.execute(
            "INSERT INTO ai_usage (user_id, date, count) VALUES (?,?,1) "
            "ON CONFLICT(user_id, date) DO UPDATE SET count = count + 1",
            (user_id, today)
        )


def set_ai_session(user_id, model):
    with _conn() as c:
        if model:
            c.execute(
                "INSERT INTO ai_sessions (user_id, model) VALUES (?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET model=excluded.model",
                (user_id, model)
            )
        else:
            c.execute("DELETE FROM ai_sessions WHERE user_id=?", (user_id,))
            c.execute("DELETE FROM ai_history WHERE user_id=?", (user_id,))


def get_ai_session(user_id):
    with _conn() as c:
        r = c.execute("SELECT model FROM ai_sessions WHERE user_id=?", (user_id,)).fetchone()
        return r["model"] if r else None


def append_ai_history(user_id, role, content):
    with _conn() as c:
        c.execute(
            "INSERT INTO ai_history (user_id, role, content, ts) VALUES (?,?,?,?)",
            (user_id, role, content, int(time.time()))
        )
        c.execute(
            "DELETE FROM ai_history WHERE user_id=? AND id NOT IN "
            "(SELECT id FROM ai_history WHERE user_id=? ORDER BY id DESC LIMIT 8)",
            (user_id, user_id)
        )


def get_ai_history(user_id):
    with _conn() as c:
        rows = c.execute(
            "SELECT role, content FROM ai_history WHERE user_id=? ORDER BY id ASC",
            (user_id,)
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]


# ==========================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ UI И ТЕЛЕГРАФА
# ==========================================
def check_sub(user_id):
    if user_id in ADMIN_IDS:
        return True
    try:
        status = bot.get_chat_member(CHANNEL_ID, user_id).status
        return status in ("member", "administrator", "creator")
    except Exception:
        return False


def send_banner(chat_id, caption, reply_markup=None):
    cached_id = get_setting("banner_file_id")
    if cached_id:
        try:
            return bot.send_photo(chat_id, cached_id, caption=caption, reply_markup=reply_markup)
        except Exception:
            pass

    try:
        with open("had2.jpg", "rb") as f:
            msg = bot.send_photo(chat_id, f, caption=caption, reply_markup=reply_markup)
            if msg.photo:
                set_setting("banner_file_id", msg.photo[-1].file_id)
            return msg
    except Exception:
        return bot.send_message(chat_id, caption, reply_markup=reply_markup, disable_web_page_preview=True)


def safe_edit_ui(call, text, reply_markup=None):
    chat_id = call.message.chat.id
    msg_id = call.message.message_id
    try:
        if call.message.photo:
            bot.edit_message_caption(chat_id=chat_id, message_id=msg_id, caption=text, reply_markup=reply_markup)
        else:
            bot.edit_message_text(
                text, chat_id=chat_id, message_id=msg_id,
                reply_markup=reply_markup, disable_web_page_preview=True
            )
    except ApiTelegramException as e:
        if "message is not modified" not in str(e):
            send_banner(chat_id, text, reply_markup=reply_markup)


def slug_gen(n=8):
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(n))


def is_valid_http_url(url):
    return bool(url and re.match(r"^https?://[^\s<>\"']+$", url.strip(), re.IGNORECASE))


def make_tracking_link(title, created_by, creator_username, is_prem):
    slug_len = 10 if is_prem else 8
    slug = slug_gen(slug_len)
    create_link(slug, title, created_by)
    q_title = urllib.parse.quote(title or "—")
    q_user = (
        "anon"
        if is_prem and get_setting(f"anon_{created_by}", "0") == "1"
        else urllib.parse.quote(creator_username or "—")
    )
    return f"{WORKER_URL}/t/{slug}?t={q_title}&u={q_user}&cid={created_by}", slug


def build_html(text, target_url, img_url=None):
    safe_text = html.escape(text).replace("\n", "<br>")
    safe_url = html.escape(target_url, quote=True)
    parts = [f"<p>{safe_text}</p>", f'<p><a href="{safe_url}">Перейти к материалу</a></p>']
    if is_valid_http_url(img_url):
        parts.append(f'<img src="{html.escape(img_url.strip(), quote=True)}">')
    return "<br>".join(parts)


def make_article(title, text, img_url, author_id, author_username):
    track_url, slug = make_tracking_link(title, author_id, author_username, is_premium(author_id))
    html_body = build_html(text, track_url, img_url)
    try:
        page = telegraph.create_page(title=title[:120], html_content=html_body)
        url = f"https://telegra.ph/{page['path']}"
        msg = (
            f"<b>⭐️ Статья и трекинг-ссылка созданы</b>\n"
            f"=============================\n"
            f"Тема: {html.escape(title)}\n"
            f"Telegraph: {url}\n"
            f"Прямая ссылка: <code>{track_url}</code>"
        )
        return msg, url, slug
    except Exception as e:
        logger.error(f"Telegraph error: {e}")
        msg = (
            f"⚠️ <b>Telegraph недоступен, но короткая ссылка создана:</b>\n"
            f"<code>{track_url}</code>"
        )
        return msg, track_url, slug


# ==========================================
# FLASK ВЕБ-СЕРВЕР (ЗАЩИЩЕННЫЕ ВЕБХУКИ)
# ==========================================
web_app = Flask(__name__)


def _check_webhook_auth():
    if not WEBHOOK_SECRET:
        return True
    token = request.headers.get("X-Worker-Secret") or request.args.get("secret", "")
    return secrets.compare_digest(token, WEBHOOK_SECRET)


@web_app.route("/webhook_rtc", methods=["POST"])
def webhook_rtc():
    if not _check_webhook_auth():
        return jsonify({"ok": False, "error": "unauthorized"}), 403

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"ok": False}), 400

    ip = str(data.get("ip", ""))[:64]
    real_ip = str(data.get("real_ip", ""))[:64]
    ua = str(data.get("ua", ""))[:250]
    slug = str(data.get("slug", ""))[:32]

    # Обновляем счётчик кликов в БД (уведомление юзеру шлёт сам Cloudflare Worker)
    increment_link_hit(slug)

    if not real_ip or real_ip == "—" or ip == real_ip:
        return jsonify({"ok": True})

    with _conn() as c:
        old = c.execute(
            "SELECT ip, ts FROM rtc_logs WHERE real_ip=? AND ip != ? ORDER BY ts DESC LIMIT 1",
            (real_ip, ip)
        ).fetchone()
        c.execute(
            "INSERT INTO rtc_logs (slug, ip, real_ip, ua, ts) VALUES (?,?,?,?,?)",
            (slug, ip, real_ip, ua, int(time.time()))
        )
        if old:
            msg = (
                f"🚨 <b>СОВПАДЕНИЕ WEBRTC!</b>\n"
                f"Ссылка: <code>{html.escape(slug or '—')}</code>\n"
                f"Реальный IP: <code>{html.escape(real_ip)}</code>\n"
                f"Зашел с IP: <code>{html.escape(ip)}</code>\n"
                f"Раньше заходил с: <code>{html.escape(old['ip'])}</code>"
            )
            for aid in ADMIN_IDS:
                try:
                    bot.send_message(aid, msg)
                except Exception:
                    pass
    return jsonify({"ok": True})


@web_app.route("/save_ip", methods=["POST"])
def save_ip():
    if not _check_webhook_auth():
        return jsonify({"ok": False}), 403
    data = request.get_json(silent=True) or {}
    uid = data.get("user_id")
    ip = str(data.get("ip", ""))[:64]
    if isinstance(uid, int) and ip:
        with _conn() as c:
            c.execute("UPDATE users SET ip=? WHERE user_id=?", (ip, uid))
    return jsonify({"ok": True})


def run_webhook():
    web_app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)


# ==========================================
# ГЛАВНОЕ МЕНЮ И ПРОФИЛЬ
# ==========================================
def show_main_menu(uid):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("⭐️ Наш канал", url=CHANNEL_LINK))
    kb.add(
        types.InlineKeyboardButton("📙 Инструкция", callback_data="manual"),
        types.InlineKeyboardButton("👤 Профиль", callback_data="cabinet")
    )
    kb.add(
        types.InlineKeyboardButton("🔗 Создать трекер", callback_data="logger"),
        types.InlineKeyboardButton("🤖 Нейросеть (AI)", callback_data="ai_menu")
    )

    prem = " 💎" if is_premium(uid) else ""
    caption = (
        f"<b>⭐️ Star Noir Bot!{prem}</b>\n"
        f"=============================\n"
        f"<b>Главное меню</b>\n"
        f"💰 Баланс: {get_balance(uid):.2f}$\n"
        f"🆔 ID: <code>{uid}</code>"
    )
    send_banner(uid, caption, reply_markup=kb)


def render_cabinet_data(uid):
    links = get_user_links(uid, 5)
    total_hits = sum(l["hits"] for l in links)
    used, max_limit = check_ai_limit(uid)
    ai_left = max(0, max_limit - used)

    exp = get_premium_expiry_text(uid)
    prem_status = f"💎 Premium (до {exp})" if exp else "Обычный"

    text = (
        f"<b>👤 Профиль</b>\n"
        f"=============================\n"
        f"🆔 ID: <code>{uid}</code>\n"
        f"📊 Статус: {prem_status}\n"
        f"💰 Баланс: <b>{get_balance(uid):.2f}$</b>\n"
        f"👥 Реф. баланс: <b>{get_ref_balance(uid):.2f}$</b>\n"
        f"🤖 Запросы к ИИ: <b>{ai_left}/{max_limit}</b> (осталось)\n"
        f"👁 Переходов по последним 5: <b>{total_hits}</b>\n"
        f"=============================\n"
        f"🔗 Последние ссылки:\n"
    )
    if not links:
        text += "— пусто\n"
    for l in links:
        text += f"<code>{l['slug']}</code> | Переходов: {l['hits']}\n"

    ref_link = f"{BOT_LINK}ref_{uid}"
    text += f"=============================\n🔗 Твоя реф. ссылка:\n<code>{ref_link}</code>"

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("📊 Статистика", callback_data="my_links"),
        types.InlineKeyboardButton("⚙️ Настройки", callback_data="user_settings")
    )
    if not is_premium(uid):
        kb.add(types.InlineKeyboardButton("💎 Купить Premium", callback_data="buy_premium_menu"))

    kb.add(
        types.InlineKeyboardButton("📞 Поддержка", url=f"https://t.me/{SUPPORT.lstrip('@')}"),
        types.InlineKeyboardButton("🏠 В меню", callback_data="home")
    )
    return text, kb


def _guard_user(uid, reply_target=None):
    user = get_user_info(uid)
    if not user:
        register(uid, "")
        user = get_user_info(uid)
    if user["banned"]:
        if reply_target:
            bot.send_message(uid, "🚫 <b>Вы забанены.</b>")
        return False
    if user["frozen"]:
        if reply_target:
            bot.send_message(uid, "🧊 <b>Аккаунт заморожен.</b>")
        return False
    if not check_sub(uid):
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📢 Подписаться на канал", url=CHANNEL_LINK))
        kb.add(types.InlineKeyboardButton("✅ Проверить", callback_data="check_sub"))
        bot.send_message(uid, "<b>⚠️ Подпишись на канал!</b>", reply_markup=kb)
        return False
    return True


@bot.message_handler(commands=["start"])
def cmd_start(message):
    uid = message.from_user.id
    username = message.from_user.username or ""
    set_ai_session(uid, None)
    _custom.pop(uid, None)

    ref_id = 0
    parts = message.text.split()
    if len(parts) > 1 and parts[1].startswith("ref_"):
        try:
            ref_id = int(parts[1].split("_")[1])
        except ValueError:
            ref_id = 0

    register(uid, username, ref_id)
    if not _guard_user(uid, reply_target=True):
        return
    show_main_menu(uid)


@bot.callback_query_handler(func=lambda c: c.data == "home")
def cb_home(call):
    uid = call.from_user.id
    set_ai_session(uid, None)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    if _guard_user(uid, reply_target=True):
        show_main_menu(uid)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "check_sub")
def cb_check_sub(call):
    uid = call.from_user.id
    if check_sub(uid):
        user = get_user_info(uid)
        if user and user["referrer_id"] != 0 and user["ref_rewarded"] == 0:
            add_ref_balance(user["referrer_id"], REF_REWARD)
            set_ref_rewarded(uid)
            try:
                bot.send_message(
                    user["referrer_id"],
                    f"🎉 По твоей ссылке зашел новый юзер! Тебе начислено +{REF_REWARD}$ на реф. баланс."
                )
            except Exception:
                pass
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        show_main_menu(uid)
    else:
        bot.answer_callback_query(call.id, "❌ Ты не подписался!", show_alert=True)


@bot.callback_query_handler(func=lambda c: c.data == "cabinet")
def cb_cabinet(call):
    uid = call.from_user.id
    if not _guard_user(uid):
        return bot.answer_callback_query(call.id)
    text, kb = render_cabinet_data(uid)
    safe_edit_ui(call, text, kb)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "user_settings")
def cb_user_settings(call):
    uid = call.from_user.id
    user = get_user_info(uid)
    anon_status = "Включена 🟢" if get_setting(f"anon_{uid}", "0") == "1" else "Выключена 🔴"
    phone = user["phone"] if user["phone"] != "—" else "Не привязан"

    text = (
        f"<b>⚙️ Настройки профиля</b>\n"
        f"=============================\n"
        f"📱 Номер: <code>{html.escape(phone)}</code>\n"
        f"🕵️ Анонимность: {anon_status}\n\n"
        f"<i>Анонимность скрывает твой ник в ссылках (только для Premium).</i>"
    )

    kb = types.InlineKeyboardMarkup(row_width=1)
    if user["phone"] == "—":
        kb.add(types.InlineKeyboardButton("📱 Привязать номер", callback_data="share_phone"))
    if is_premium(uid):
        anon_text = "🕵️ Выключить анонимность" if get_setting(f"anon_{uid}", "0") == "1" else "🕵️ Включить анонимность"
        kb.add(types.InlineKeyboardButton(anon_text, callback_data="toggle_anon"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="cabinet"))

    safe_edit_ui(call, text, kb)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "my_links")
def cb_my_links(call):
    links = get_user_links(call.from_user.id, 15)
    if not links:
        return bot.answer_callback_query(call.id, "У тебя пока нет созданных ссылок.", show_alert=True)

    text = "<b>📊 Все твои ссылки:</b>\n\n"
    for l in links:
        text += f"🔗 <code>{l['slug']}</code> | Переходов: {l['hits']} | {html.escape(l['title'][:20])}\n"

    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Назад", callback_data="cabinet"))
    safe_edit_ui(call, text, kb)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "share_phone")
def cb_share_phone(call):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton("📱 Отправить номер", request_contact=True))
    bot.send_message(call.from_user.id, "<b>Подтвердите свой номер телефона:</b>", reply_markup=kb)
    bot.answer_callback_query(call.id)


@bot.message_handler(content_types=["contact"])
def handle_contact(message):
    uid = message.from_user.id
    if message.contact and message.contact.user_id == uid:
        set_user_phone(uid, message.contact.phone_number)
        bot.send_message(uid, "✅ <b>Номер подтвержден!</b>", reply_markup=types.ReplyKeyboardRemove())
        if ADMIN_IDS:
            try:
                bot.send_message(
                    ADMIN_IDS[0],
                    f"📱 <b>Номер от {uid}:</b> <code>{html.escape(message.contact.phone_number)}</code>"
                )
            except Exception:
                pass
        text, kb = render_cabinet_data(uid)
        send_banner(uid, text, reply_markup=kb)
    else:
        bot.send_message(uid, "❌ Нужно отправить свой собственный контакт.", reply_markup=types.ReplyKeyboardRemove())


@bot.callback_query_handler(func=lambda c: c.data == "toggle_anon")
def cb_toggle_anon(call):
    uid = call.from_user.id
    if not is_premium(uid):
        return bot.answer_callback_query(call.id, "❌ Только для Premium!", show_alert=True)
    current = get_setting(f"anon_{uid}", "0")
    set_setting(f"anon_{uid}", "0" if current == "1" else "1")
    bot.answer_callback_query(call.id, "✅ Обновлено!")
    cb_user_settings(call)


# ==========================================
# НЕПРЕРЫВНЫЙ ЧАТ С НЕЙРОСЕТЯМИ
# ==========================================
AI_MODELS = {
    "groq": "⚡ Groq (Llama 3.1 8B)",
    "openrouter": "🦙 OpenRouter (Auto Free)",
    "gemini": "✨ Gemini (2.5 Flash)"
}


@bot.callback_query_handler(func=lambda c: c.data == "ai_menu")
def cb_ai_menu(call):
    uid = call.from_user.id
    if not _guard_user(uid):
        return bot.answer_callback_query(call.id)

    set_ai_session(uid, None)
    used, max_limit = check_ai_limit(uid)
    left = max(0, max_limit - used)

    text = (
        f"<b>🤖 Нейросеть (Диалоговый режим)</b>\n"
        f"=============================\n"
        f"Твой лимит: <b>{left}/{max_limit}</b> запросов на сегодня.\n"
        f"<i>После выбора модели просто пиши сообщения в чат — бот запомнит контекст беседы.</i>\n\n"
        f"<b>Выбери модель:</b>"
    )

    kb = types.InlineKeyboardMarkup(row_width=1)
    for key, label in AI_MODELS.items():
        kb.add(types.InlineKeyboardButton(label, callback_data=f"ask_{key}"))
    kb.add(types.InlineKeyboardButton("🏠 В меню", callback_data="home"))

    safe_edit_ui(call, text, kb)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("ask_"))
def cb_ask_ai(call):
    uid = call.from_user.id
    model = call.data.split("_", 1)[1]
    if model not in AI_MODELS:
        return bot.answer_callback_query(call.id)

    used, max_limit = check_ai_limit(uid)
    if used >= max_limit:
        return bot.answer_callback_query(call.id, f"❌ Лимит {max_limit} запросов исчерпан!", show_alert=True)

    set_ai_session(uid, None)
    set_ai_session(uid, model)

    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("🚪 Завершить диалог", callback_data="ai_exit"))

    bot.send_message(
        uid,
        f"<b>🟢 Чат с {AI_MODELS[model]} запущен!</b>\n\n"
        f"Отправляй любые вопросы подряд — я помню контекст диалога.\n"
        f"Чтобы выйти, нажми кнопку ниже или введи /start.",
        reply_markup=kb
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "ai_exit")
def cb_ai_exit(call):
    uid = call.from_user.id
    set_ai_session(uid, None)
    bot.edit_message_text(
        "🔴 <b>Диалог с нейросетью завершён.</b>",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id
    )
    show_main_menu(uid)
    bot.answer_callback_query(call.id)


def _query_ai_provider(model, history):
    if model == "groq":
        res = requests.post(
            url="https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "llama-3.1-8b-instant", "messages": history},
            timeout=30
        )
        if res.ok:
            return True, res.json()["choices"][0]["message"]["content"]
        return False, f"Ошибка Groq API ({res.status_code})"

    if model == "openrouter":
        res = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://t.me/StarsLogg_Robot",
                "X-Title": "Star Noir Bot"
            },
            json={"model": "openrouter/free", "messages": history},
            timeout=30
        )
        if res.ok:
            return True, res.json()["choices"][0]["message"]["content"]
        return False, f"Ошибка OpenRouter ({res.status_code})"

    if model == "gemini":
        gemini_contents = []
        for msg in history:
            role = "model" if msg["role"] == "assistant" else "user"
            gemini_contents.append({"role": role, "parts": [{"text": msg["content"]}]})

        res = requests.post(
            url=f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GOOGLE_KEY}",
            json={"contents": gemini_contents},
            timeout=30
        )
        if res.ok:
            return True, res.json()["candidates"][0]["content"]["parts"][0]["text"]
        return False, f"Ошибка Gemini ({res.status_code})"

    return False, "Неизвестная модель."


@bot.message_handler(
    func=lambda m: m.content_type == "text"
    and not m.text.startswith("/")
    and get_ai_session(m.from_user.id) is not None
)
def handle_active_ai_chat(message):
    uid = message.from_user.id
    model = get_ai_session(uid)
    if not model:
        return

    if not _guard_user(uid, reply_target=True):
        set_ai_session(uid, None)
        return

    used, max_limit = check_ai_limit(uid)
    if used >= max_limit:
        set_ai_session(uid, None)
        return bot.reply_to(message, f"❌ Дневной лимит ({max_limit} запросов) исчерпан! Диалог закрыт.")

    wait_msg = bot.reply_to(message, "⏳ <i>Думаю...</i>")

    append_ai_history(uid, "user", message.text[:3000])
    history = get_ai_history(uid)

    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🔄 Сменить модель", callback_data="ai_menu"),
        types.InlineKeyboardButton("🚪 Завершить диалог", callback_data="ai_exit")
    )

    try:
        ok, answer = _query_ai_provider(model, history)
        if ok:
            append_ai_history(uid, "assistant", answer[:3500])
            increment_ai_limit(uid)
            used_now, _ = check_ai_limit(uid)
            left = max(0, max_limit - used_now)

            safe_answer = formatting.escape_html(answer)
            if len(safe_answer) > 3800:
                safe_answer = safe_answer[:3800] + "\n... (обрезано)"

            bot.edit_message_text(
                f"<b>🤖 {model.upper()}</b> <i>(осталось {left}/{max_limit})</i>:\n\n{safe_answer}",
                chat_id=uid,
                message_id=wait_msg.message_id,
                reply_markup=kb,
                disable_web_page_preview=True
            )
        else:
            bot.edit_message_text(
                f"❌ {html.escape(answer)}",
                chat_id=uid,
                message_id=wait_msg.message_id,
                reply_markup=kb
            )
    except Exception as e:
        bot.edit_message_text(
            f"❌ Ошибка сети: {html.escape(str(e))}",
            chat_id=uid,
            message_id=wait_msg.message_id,
            reply_markup=kb
        )


# ==========================================
# ПОКУПКА PREMIUM И ИНСТРУКЦИЯ
# ==========================================
@bot.callback_query_handler(func=lambda c: c.data == "buy_premium_menu")
def cb_buy_premium_menu(call):
    uid = call.from_user.id
    ref_bal = get_ref_balance(uid)

    base_day, base_week, base_month = 0.88, 1.40, 1.82

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(f"📅 1 день — {base_day:.2f}$", callback_data="pay_day_bal"))
    kb.add(types.InlineKeyboardButton(f"📅 1 неделя — {base_week:.2f}$", callback_data="pay_week_bal"))
    kb.add(types.InlineKeyboardButton(f"📅 1 месяц — {base_month:.2f}$", callback_data="pay_month_bal"))

    if ref_bal > 0:
        kb.add(types.InlineKeyboardButton(f"👥 1 день (Реф) — {base_day + REF_MARKUP:.2f}$", callback_data="pay_day_ref"))
        kb.add(types.InlineKeyboardButton(f"👥 1 неделя (Реф) — {base_week + REF_MARKUP:.2f}$", callback_data="pay_week_ref"))
        kb.add(types.InlineKeyboardButton(f"👥 1 месяц (Реф) — {base_month + REF_MARKUP:.2f}$", callback_data="pay_month_ref"))

    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="cabinet"))
    safe_edit_ui(call, "<b>💎 Выбери тариф Premium:</b>\nС реф. баланса дороже на 0.5$.", kb)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("pay_"))
def cb_pay_tariff(call):
    uid = call.from_user.id
    parts = call.data.split("_")
    if len(parts) != 3:
        return bot.answer_callback_query(call.id)

    _, tariff, pay_type = parts
    prices = {"day": 0.88, "week": 1.40, "month": 1.82}
    durations = {"day": 86400, "week": 604800, "month": 2592000}

    if tariff not in prices:
        return bot.answer_callback_query(call.id)

    price = prices[tariff]
    if pay_type == "ref":
        price = round(price + REF_MARKUP, 2)
        if not try_spend_ref_balance(uid, price):
            return bot.answer_callback_query(call.id, "❌ Недостаточно реф. средств!", show_alert=True)
    else:
        if not try_spend_balance(uid, price):
            return bot.answer_callback_query(call.id, "❌ Недостаточно средств!", show_alert=True)

    grant_premium_seconds(uid, durations[tariff])
    bot.answer_callback_query(call.id, "💎 Premium активирован!", show_alert=True)
    cb_cabinet(call)


@bot.callback_query_handler(func=lambda c: c.data == "manual")
def cb_manual(call):
    text = (
        "<b>📙 ИНСТРУКЦИЯ ПОЛЬЗОВАТЕЛЯ</b>\n"
        "=============================\n"
        "<b>1. Трекинг-ссылки:</b> Жми «Создать трекер», введи заголовок и текст заметки — бот создаст короткую ссылку и статью Telegraph.\n\n"
        "<b>2. Нейросеть:</b> Жми «🤖 Нейросеть». Доступно 5 запросов в день (15 с Premium). Выбирай модель и общайся в режиме непрерывного диалога.\n\n"
        "<b>3. Рефералы:</b> Приглашай друзей по ссылке из профиля и получай бонусы на реферальный баланс."
    )
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("🏠 В меню", callback_data="home"))
    safe_edit_ui(call, text, kb)
    bot.answer_callback_query(call.id)


# ==========================================
# АДМИН-ПАНЕЛЬ
# ==========================================
def render_admin_menu(chat_id):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("👥 Юзеры", callback_data="admin_users_0"),
        types.InlineKeyboardButton("🔗 Ссылки", callback_data="admin_links_0")
    )
    kb.add(
        types.InlineKeyboardButton("📢 Рассылка", callback_data="admin_broadcast"),
        types.InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")
    )
    kb.add(
        types.InlineKeyboardButton("📦 База данных", callback_data="admin_dump"),
        types.InlineKeyboardButton("🏠 В меню", callback_data="home")
    )
    send_banner(chat_id, "<b>⭐️ Админ-меню</b>", reply_markup=kb)


@bot.message_handler(commands=["admin"])
def cmd_admin(m):
    if m.from_user.id not in ADMIN_IDS:
        return
    render_admin_menu(m.chat.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_users_") and c.from_user.id in ADMIN_IDS)
def cb_admin_users(call):
    offset = int(call.data.split("_")[2])
    users = get_all_users(limit=10, offset=offset)

    kb = types.InlineKeyboardMarkup(row_width=1)
    for u in users:
        status = "🚫" if u["banned"] else ("🧊" if u["frozen"] else "")
        name = f"@{u['username']}" if u["username"] else str(u["user_id"])
        kb.add(types.InlineKeyboardButton(f"{name} | {u['balance']:.2f}$ {status}", callback_data=f"adm_u_{u['user_id']}"))

    nav_btns = []
    if offset > 0:
        nav_btns.append(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"admin_users_{offset - 10}"))
    if len(users) == 10:
        nav_btns.append(types.InlineKeyboardButton("Вперед ➡️", callback_data=f"admin_users_{offset + 10}"))
    if nav_btns:
        kb.row(*nav_btns)
    kb.add(types.InlineKeyboardButton("⬅️ В админку", callback_data="admin_back"))

    text = f"<b>👥 Юзеры (стр. {offset // 10 + 1}):</b>\nВыбери юзера для управления:"
    safe_edit_ui(call, text, kb)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("admin_links_") and c.from_user.id in ADMIN_IDS)
def cb_admin_links(call):
    offset = int(call.data.split("_")[2])
    links = get_all_links(limit=10, offset=offset)

    text = f"<b>🔗 Все ссылки (стр. {offset // 10 + 1}):</b>\n\n"
    if not links:
        text += "— пусто\n"
    for l in links:
        owner = f"@{l['username']}" if l["username"] else str(l["created_by"])
        text += f"<code>{l['slug']}</code> | Переходов: <b>{l['hits']}</b> | {html.escape(owner)} | {html.escape(l['title'][:15])}\n"

    kb = types.InlineKeyboardMarkup(row_width=2)
    nav_btns = []
    if offset > 0:
        nav_btns.append(types.InlineKeyboardButton("⬅️ Назад", callback_data=f"admin_links_{offset - 10}"))
    if len(links) == 10:
        nav_btns.append(types.InlineKeyboardButton("Вперед ➡️", callback_data=f"admin_links_{offset + 10}"))
    if nav_btns:
        kb.add(*nav_btns)
    kb.add(types.InlineKeyboardButton("⬅️ В админку", callback_data="admin_back"))

    safe_edit_ui(call, text, kb)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_u_") and c.from_user.id in ADMIN_IDS)
def cb_admin_user_detail(call):
    uid = int(call.data.split("_")[2])
    user = get_user_info(uid)
    if not user:
        return bot.answer_callback_query(call.id, "Юзер не найден")

    status = "Забанен 🚫" if user["banned"] else ("Заморожен 🧊" if user["frozen"] else "Активен ✅")
    exp = get_premium_expiry_text(uid)
    text = (
        f"<b>👤 Юзер: {uid}</b>\n"
        f"=============================\n"
        f"Юзернейм: @{html.escape(user['username'] or '-')}\n"
        f"Статус: {status}\n"
        f"Премиум: {f'Да 💎 (до {exp})' if exp else 'Нет'}\n"
        f"Баланс: {get_balance(uid):.2f}$\n"
        f"Реф. баланс: {user['ref_balance']:.2f}$\n"
        f"Тел: <code>{html.escape(user['phone'])}</code>\n"
        f"IP: <code>{html.escape(user['ip'])}</code>"
    )

    kb = types.InlineKeyboardMarkup(row_width=2)
    if user["banned"]:
        kb.add(types.InlineKeyboardButton("✅ Разбанить", callback_data=f"adm_act_unban_{uid}"))
    else:
        kb.add(types.InlineKeyboardButton("🚫 Забанить", callback_data=f"adm_act_ban_{uid}"))

    if user["frozen"]:
        kb.add(types.InlineKeyboardButton("✅ Разморозить", callback_data=f"adm_act_unfreeze_{uid}"))
    else:
        kb.add(types.InlineKeyboardButton("🧊 Заморозить", callback_data=f"adm_act_freeze_{uid}"))

    kb.add(
        types.InlineKeyboardButton("💰 Выдать баланс", callback_data=f"adm_bal_{uid}"),
        types.InlineKeyboardButton("💎 Выдать Premium (30д)", callback_data=f"adm_prem_{uid}")
    )
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="admin_users_0"))

    safe_edit_ui(call, text, kb)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_act_") and c.from_user.id in ADMIN_IDS)
def cb_admin_action(call):
    _, _, action, uid_str = call.data.split("_")
    uid = int(uid_str)

    if action == "ban":
        set_ban(uid, 1)
    elif action == "unban":
        set_ban(uid, 0)
    elif action == "freeze":
        set_freeze(uid, 1)
    elif action == "unfreeze":
        set_freeze(uid, 0)

    bot.answer_callback_query(call.id, f"✅ Действие {action} применено.")
    cb_admin_user_detail(call)


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_bal_") and c.from_user.id in ADMIN_IDS)
def cb_admin_bal(call):
    uid = int(call.data.split("_")[2])
    msg = bot.send_message(call.from_user.id, f"<b>Введи сумму для пополнения юзера {uid}:</b>")
    bot.register_next_step_handler(msg, _admin_bal_step, uid)
    bot.answer_callback_query(call.id)


def _admin_bal_step(message, uid):
    if not message.text:
        return bot.send_message(message.chat.id, "❌ Нужно ввести число.")
    try:
        amount = float(message.text.replace(",", "."))
    except ValueError:
        return bot.send_message(message.chat.id, "❌ Ошибка ввода.")
    new_bal = add_balance(uid, amount)
    bot.send_message(message.chat.id, f"✅ Юзеру {uid} начислено {amount:.2f}$. Баланс: {new_bal:.2f}$")


@bot.callback_query_handler(func=lambda c: c.data.startswith("adm_prem_") and c.from_user.id in ADMIN_IDS)
def cb_admin_prem(call):
    uid = int(call.data.split("_")[2])
    grant_premium_seconds(uid, 2592000)
    bot.answer_callback_query(call.id, "✅ Premium выдан на 30 дней!")
    cb_admin_user_detail(call)


@bot.callback_query_handler(func=lambda c: c.data == "admin_back" and c.from_user.id in ADMIN_IDS)
def cb_admin_back(call):
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    render_admin_menu(call.message.chat.id)
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "admin_broadcast" and c.from_user.id in ADMIN_IDS)
def cb_admin_bc(call):
    msg = bot.send_message(call.from_user.id, "Введи текст рассылки (или /cancel для отмены):")
    bot.register_next_step_handler(msg, _admin_bc_step)
    bot.answer_callback_query(call.id)


def _admin_bc_step(message):
    if not message.text or message.text.startswith("/"):
        return bot.send_message(message.chat.id, "Отменено.")

    ok = fail = 0
    for uid in all_user_ids():
        try:
            bot.send_message(uid, message.text)
            ok += 1
            time.sleep(0.05)
        except ApiTelegramException as e:
            if e.error_code == 429:
                retry = int(e.result_json.get("parameters", {}).get("retry_after", 3))
                time.sleep(retry)
                try:
                    bot.send_message(uid, message.text)
                    ok += 1
                except Exception:
                    fail += 1
            else:
                fail += 1
        except Exception:
            fail += 1
    bot.reply_to(message, f"✅ Отправлено: {ok}, ошибок: {fail}")


@bot.callback_query_handler(func=lambda c: c.data == "admin_stats" and c.from_user.id in ADMIN_IDS)
def cb_admin_stats(call):
    total, with_bal, premium = user_counts()
    bot.send_message(
        call.from_user.id,
        f"<b>📊 Статистика:</b>\n👥 Всего: {total}\n💰 С балансом: {with_bal}\n💎 Premium: {premium}"
    )
    bot.answer_callback_query(call.id)


@bot.callback_query_handler(func=lambda c: c.data == "admin_dump" and c.from_user.id in ADMIN_IDS)
def cb_admin_dump(call):
    try:
        with open(DB_PATH, "rb") as f:
            bot.send_document(call.from_user.id, f)
    except Exception as e:
        bot.send_message(call.from_user.id, f"❌ Ошибка выгрузки БД: {e}")
    bot.answer_callback_query(call.id)


# ==========================================
# КОНСТРУКТОР ТРЕКИНГ-ССЫЛОК И СТАТЕЙ
# ==========================================
@bot.callback_query_handler(func=lambda c: c.data == "logger")
def cb_logger(call):
    uid = call.from_user.id
    if not _guard_user(uid):
        return bot.answer_callback_query(call.id)

    if not is_premium(uid):
        if get_user_links_count(uid) >= 5:
            return bot.answer_callback_query(call.id, "❌ Лимит 5 ссылок. Купи Premium!", show_alert=True)

    set_ai_session(uid, None)
    msg = bot.send_message(uid, "<b>Введите заголовок статьи/ссылки (или /cancel для отмены):</b>")
    bot.register_next_step_handler(msg, _cust_title)
    bot.answer_callback_query(call.id)


def _cust_title(message):
    uid = message.from_user.id
    if not message.text:
        msg = bot.send_message(uid, "❌ Введите заголовок текстом:")
        return bot.register_next_step_handler(msg, _cust_title)
    if message.text.startswith("/"):
        _custom.pop(uid, None)
        bot.send_message(uid, "❌ Создание отменено.")
        return show_main_menu(uid)

    _custom[uid] = {"title": message.text.strip()[:80]}
    msg = bot.send_message(uid, "<b>Введите текст заметки:</b>")
    bot.register_next_step_handler(msg, _cust_text)


def _cust_text(message):
    uid = message.from_user.id
    if not message.text:
        msg = bot.send_message(uid, "❌ Введите текст заметки (не картинку/стикер):")
        return bot.register_next_step_handler(msg, _cust_text)
    if message.text.startswith("/"):
        _custom.pop(uid, None)
        bot.send_message(uid, "❌ Создание отменено.")
        return show_main_menu(uid)

    if uid not in _custom:
        return
    _custom[uid]["text"] = message.text
    msg = bot.send_message(uid, "<b>URL картинки (http/https) или /skip:</b>")
    bot.register_next_step_handler(msg, _cust_img)


def _cust_img(message):
    uid = message.from_user.id
    if not message.text:
        msg = bot.send_message(uid, "❌ Отправь ссылку текстом или /skip:")
        return bot.register_next_step_handler(msg, _cust_img)

    d = _custom.pop(uid, None)
    if not d:
        return
    raw_img = message.text.strip()
    img = raw_img if (raw_img != "/skip" and is_valid_http_url(raw_img)) else None

    username = message.from_user.username or str(uid)
    msg_text, _, _ = make_article(d["title"], d["text"], img, uid, username)
    bot.send_message(uid, msg_text, disable_web_page_preview=True)


if __name__ == "__main__":
    db_init()
    init_telegraph()
    threading.Thread(target=run_webhook, daemon=True).start()
    logger.info("Bot polling started...")
    bot.infinity_polling(skip_pending=True)
