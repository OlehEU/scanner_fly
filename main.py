# main.py — OZ SCANNER v2026 ULTRA — ПОЛНАЯ ВЕРСИЯ С АДМИНКОЙ 777
import ccxt.async_support as ccxt
import asyncio
import pandas as pd
import numpy as np
import talib
import aiohttp
import aiosqlite
import os
from datetime import datetime
import logging

# =================== СЕКРЕТЫ ИЗ FLY.IO ===================
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))

# =================== КОНФИГ ===================
ALL_SYMBOLS = ["BTC/USDT","ETH/USDT","SOL/USDT","XRP/USDT","ADA/USDT","DOGE/USDT","AVAX/USDT","LINK/USDT","DOT/USDT","MATIC/USDT",
               "BNB/USDT","TON/USDT","TRX/USDT","NEAR/USDT","APT/USDT","ARB/USDT","OP/USDT","SUI/USDT","INJ/USDT","PEPE/USDT"]
ALL_TIMEFRAMES = ['1m','5m','15m','45m','1h','4h']

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s')

# =================== БАЗА ДАННЫХ ===================
async def init_db():
    async with aiosqlite.connect("oz_ultra.db") as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, tf TEXT, direction TEXT, price REAL, reason TEXT, ts INTEGER
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS enabled_coins (symbol TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS enabled_tfs (tf TEXT PRIMARY KEY);
        ''')
        # дефолты
        await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('scanner_enabled','1')")
        await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('password','777')")
        for s in ALL_SYMBOLS:
            await db.execute("INSERT OR IGNORE INTO enabled_coins VALUES (?)", (s,))
        for tf in ALL_TIMEFRAMES:
            await db.execute("INSERT OR IGNORE INTO enabled_tfs VALUES (?)", (tf,))
        await db.commit()

# =================== ПОМОЩНИКИ БАЗЫ ===================
async def get_enabled_coins(): 
    async with aiosqlite.connect("oz_ultra.db") as db:
        async with db.execute("SELECT symbol FROM enabled_coins") as cur:
            return [row[0] async for row in cur]

async def get_enabled_tfs(): 
    async with aiosqlite.connect("oz_ultra.db") as db:
        async with db.execute("SELECT tf FROM enabled_tfs") as cur:
            return [row[0] async for row in cur]

async def is_scanner_enabled():
    async with aiosqlite.connect("oz_ultra.db") as db:
        async with db.execute("SELECT value FROM settings WHERE key='scanner_enabled'") as cur:
            row = await cur.fetchone()
            return row and row[0] == '1'

# =================== ТЕЛЕГРАМ БОТ ===================
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, ContextTypes, filters

user_states = {}  # {user_id: "waiting_password" or "main"}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_CHAT_ID:
        await update.message.reply_text("Доступ запрещён.")
        return
    user_states[update.effective_user.id] = None
    keyboard = [[InlineKeyboardButton("Войти в админку (пароль 777)", callback_data="enter_password")]]
    await update.message.reply_text("OZ SCANNER v2026 ULTRA\n\nСтатус: ONLINE", reply_markup=InlineKeyboardMarkup(keyboard))

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "enter_password":
        user_states[query.from_user.id] = "waiting_password"
        await query.edit_message_text("Введи пароль:")

    elif data == "main_panel":
        await show_main_panel(query)

    elif data == "toggle_scanner":
        async with aiosqlite.connect("oz_ultra.db") as db:
            cur = await db.execute("SELECT value FROM settings WHERE key='scanner_enabled'")
            val = await cur.fetchone()
            new_val = '0' if val[0] == '1' else '1'
            await db.execute("UPDATE settings SET value=? WHERE key='scanner_enabled'", (new_val,))
            await db.commit()
        await show_main_panel(query)

    elif data == "coins":
        await show_coins_panel(query)
    elif data.startswith("toggle_coin_"):
        symbol = data.replace("toggle_coin_", "")
        async with aiosqlite.connect("oz_ultra.db") as db:
            await db.execute("DELETE FROM enabled_coins WHERE symbol=?", (symbol,))
            await db.execute("INSERT INTO enabled_coins VALUES (?)", (symbol,))
            await db.commit()
        await show_coins_panel(query)

    elif data == "tfs":
        await show_tfs_panel(query)
    elif data.startswith("toggle_tf_"):
        tf = data.replace("toggle_tf_", "")
        async with aiosqlite.connect("oz_ultra.db") as db:
            await db.execute("DELETE FROM enabled_tfs WHERE tf=?", (tf,))
            await db.execute("INSERT INTO enabled_tfs VALUES (?)", (tf,))
            await db.commit()
        await show_tfs_panel(query)

    elif data in ["stats_24h", "stats_7d", "stats_30d", "stats_all"]:
        await show_stats(query, data)

async def show_main_panel(query):
    enabled = "ВКЛЮЧЁН" if await is_scanner_enabled() else "ВЫКЛЮЧЕН"
    keyboard = [
        [InlineKeyboardButton(f"Сканер: {enabled}", callback_data="toggle_scanner")],
        [InlineKeyboardButton("Монеты", callback_data="coins")],
        [InlineKeyboardButton("Таймфреймы", callback_data="tfs")],
        [InlineKeyboardButton("Статистика", callback_data="stats_24h")],
        [InlineKeyboardButton("Назад", callback_data="enter_password")]
    ]
    await query.edit_message_text("ПАНЕЛЬ УПРАВЛЕНИЯ OZ 2026\nПароль: 777", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_coins_panel(query):
    coins = await get_enabled_coins()
    keyboard = []
    for s in ALL_SYMBOLS:
        status = "ON" if s in coins else "OFF"
        keyboard.append([InlineKeyboardButton(f"{status} {s}", callback_data=f"toggle_coin_{s}")])
    keyboard.append([InlineKeyboardButton("Назад", callback_data="main_panel")])
    await query.edit_message_text("Выбор монет:", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_tfs_panel(query):
    tfs = await get_enabled_tfs()
    keyboard = []
    for tf in ALL_TIMEFRAMES:
        status = "ON" if tf in tfs else "OFF"
        keyboard.append([InlineKeyboardButton(f"{status} {tf}", callback_data=f"toggle_tf_{tf}")])
    keyboard.append([InlineKeyboardButton("Назад", callback_data="main_panel")])
    await query.edit_message_text("Выбор таймфреймов:", reply_markup=InlineKeyboardMarkup(keyboard))

async def show_stats(query, period):
    async with aiosqlite.connect("oz_ultra.db") as db:
        if period == "stats_24h":
            cutoff = int((datetime.now().timestamp() - 86400) * 1000)
            rows = await db.execute("SELECT symbol,tf,direction,price,reason,datetime(ts/1000,'unixepoch','localtime') FROM signals WHERE ts > ? ORDER BY ts DESC", (cutoff,))
        elif period == "stats_7d":
            cutoff = int((datetime.now().timestamp() - 7*86400) * 1000)
            rows = await db.execute("SELECT symbol,tf,direction,price,reason,datetime(ts/1000,'unixepoch','localtime') FROM signals WHERE ts > ? ORDER BY ts DESC", (cutoff,))
        elif period == "stats_30d":
            cutoff = int((datetime.now().timestamp() - 30*86400) * 1000)
            rows = await db.execute("SELECT symbol,tf,direction,price,reason,datetime(ts/1000,'unixepoch','localtime') FROM signals WHERE ts > ? ORDER BY ts DESC", (cutoff,))
        else:
            rows = await db.execute("SELECT symbol,tf,direction,price,reason,datetime(ts/1000,'unixepoch','localtime') FROM signals ORDER BY ts DESC LIMIT 100")

        async with rows as cur:
            data = await cur.fetchall()

    if not data:
        text = "Нет сигналов за этот период"
    else:
        text = "СИГНАЛЫ:\n\n"
        for row in data[:50]:
            text += f"{row[2]} {row[0]} {row[1]} | {row[3]:.6f} | {row[4]} | {row[5]}\n"
        if len(data) > 50:
            text += f"\n... и ещё {len(data)-50} сигналов"

    keyboard = [
        [InlineKeyboardButton("24ч", callback_data="stats_24h"), InlineKeyboardButton("7д", callback_data="stats_7d"), InlineKeyboardButton("30д", callback_data="stats_30d")],
        [InlineKeyboardButton("Назад", callback_data="main_panel")]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_CHAT_ID:
        return
    state = user_states.get(update.effective_user.id)
    if state == "waiting_password":
        if update.message.text == "777":
            user_states[update.effective_user.id] = "main"
            await update.message.reply_text("Пароль верный! Добро пожаловать.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Панель управления", callback_data="main_panel")]]))
        else:
            await update.message.reply_text("Неверный пароль")

# =================== СИГНАЛЫ ===================
async def send_signal(symbol, tf, direction, price, reason):
    ts = int(datetime.now().timestamp() * 1000)
    async with aiosqlite.connect("oz_ultra.db") as db:
        await db.execute("INSERT INTO signals (symbol,tf,direction,price,reason,ts) VALUES (?,?,?,?,?,?)",
                        (symbol, tf, direction, price, reason, ts))
        await db.commit()

    text = (f"OZ 2026\n"
            f"{direction}\n"
            f"{symbol} | {tf}\n"
            f"Цена: {price:.6f}\n"
            f"{reason}\n"
            f"{datetime.now().strftime('%d.%m %H:%M:%S')}\n\n"
            f"<a href='https://www.tradingview.com/chart/?symbol=BINANCE:{symbol.replace('/','')}&interval={tf}'>ГРАФИК</a>")

    async with aiohttp.ClientSession() as session:
        await session.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                          json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False})
        if WEBHOOK_URL:
            await session.post(WEBHOOK_URL, json={"symbol": symbol.replace("/",""), "price": price, "signal": direction.lower(), "secret": WEBHOOK_SECRET or ""})

# =================== СТРАТЕГИЯ (ТОЧНАЯ) ===================
def bullish_div(price: np.ndarray, rsi: np.ndarray) -> bool:
    recent_low_price = np.min(price[-12:])
    prev_low_price = np.min(price[-24:-12])
    recent_low_rsi = rsi[np.argmin(price[-12:])]
    prev_low_rsi = rsi[-24:-12][np.argmin(price[-24:-12])]
    return recent_low_price > prev_low_price and recent_low_rsi < prev_low_rsi

async def check_pair(exchange, symbol, tf):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, tf, limit=500)
        df = pd.DataFrame(ohlcv, columns=['ts','o','h','l','c','v'])
        df['ema34'] = talib.EMA(df['c'], 34)
        df['ema144'] = talib.EMA(df['c'], 144)
        df['rsi'] = talib.RSI(df['c'], 14)
        df['vol_ma'] = df['v'].rolling(20).mean()

        c = df['c'].values
        rsi = df['rsi'].values
        price = c[-1]
        vol = df['v'].iloc[-1]
        vol_avg = df['vol_ma'].iloc[-1]

        long = (
            c[-1] > df['ema34'].iloc[-1] > df['ema144'].iloc[-1] and
            rsi[-1] < 43 and
            vol > vol_avg * 1.5 and
            (bullish_div(c, rsi) or (rsi[-2] < 30 and rsi[-1] > rsi[-2])) and
            c[-1] > c[-2]
        )
        close = (
            rsi[-1] > 73 or
            c[-1] < df['ema34'].iloc[-1] or
            (c[-1] < c[-2] and rsi[-1] > 70)
        )

        if long: await send_signal(symbol, tf, "LONG", price, "RSI+Дивергенция+EMA+Объём")
        if close: await send_signal(symbol, tf, "CLOSE", price, "Перегрев/Медведь")

    except Exception as e:
        logging.error(f"{symbol} {tf}: {e}")

# =================== ОСНОВНОЙ ЦИКЛ ===================
async def scanner_loop():
    exchange = ccxt.binance({
        'apiKey': BINANCE_API_KEY,
        'secret': BINANCE_API_SECRET,
        'enableRateLimit': True,
        'options': {'defaultType': 'future'},
        'timeout': 30000
    })
    while True:
        if not await is_scanner_enabled():
            await asyncio.sleep(15)
            continue

        symbols = await get_enabled_coins()
        tfs = await get_enabled_tfs()
        tasks = [check_pair(exchange, s, tf) for s in symbols for tf in tfs]
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(12)

async def main():
    await init_db()
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    logging.info("OZ SCANNER v2026 ULTRA — ЗАПУЩЕН ПОЛНОСТЬЮ")
    await scanner_loop()

if __name__ == "__main__":
    asyncio.run(main())
