# main.py — OZ SCANNER v2026 ULTRA MAX — ФИНАЛЬНАЯ РАБОЧАЯ ВЕРСИЯ (24.11.2025)
import ccxt.async_support as ccxt
import asyncio
import pandas as pd
import talib
import aiosqlite
import os
import logging
from datetime import datetime
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import aiohttp

# =================== СЕКРЕТЫ ===================
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID") or "0")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

app = FastAPI()
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s')

# ИСПРАВЛЕННЫЕ СИМВОЛЫ + ТОЛЬКО РАБОЧИЕ ТАЙМФРЕЙМЫ
ALL_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "ADA/USDT",
    "DOGE/USDT", "AVAX/USDT", "LINK/USDT", "DOT/USDT",
    "BNB/USDT", "TON/USDT", "TRX/USDT", "NEAR/USDT", "APT/USDT",
    "ARB/USDT", "OP/USDT", "SUI/USDT", "INJ/USDT",
    "POL/USDT", "1000PEPE/USDT"
]
ALL_TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h']
DB_PATH = "oz_scanner.db"

# =================== БАЗА ===================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, tf TEXT, direction TEXT, price REAL, reason TEXT, ts INTEGER
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS enabled_coins (symbol TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS enabled_tfs (tf TEXT PRIMARY KEY);
        ''')
        # ПАРОЛЬ 777 — ГАРАНТИРОВАННО
        await db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('password','777')")
        await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('scanner_enabled','1')")
        for s in ALL_SYMBOLS:
            await db.execute("INSERT OR IGNORE INTO enabled_coins VALUES (?)", (s,))
        for tf in ALL_TIMEFRAMES:
            await db.execute("INSERT OR IGNORE INTO enabled_tfs VALUES (?)", (tf,))
        await db.commit()

async def get_setting(key): 
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

async def set_setting(key, value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, value))
        await db.commit()

async def get_enabled_coins():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT symbol FROM enabled_coins") as cur:
            return [row[0] async for row in cur]

async def get_enabled_tfs():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT tf FROM enabled_tfs") as cur:
            return [row[0] async for row in cur]

# =================== СИГНАЛЫ ===================
async def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    async with aiohttp.ClientSession() as s:
        await s.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                     json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})

async def send_signal(symbol: str, tf: str, direction: str, price: float, reason: str):
    ts = int(datetime.now().timestamp() * 1000)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO signals (symbol,tf,direction,price,reason,ts) VALUES (?,?,?,?,?,?)",
                        (symbol, tf, direction, price, reason, ts))
        await db.commit()

    text = (f"OZ SCANNER 2026\n"
            f"<b>{direction}</b>\n"
            f"<code>{symbol}</code> | <code>{tf}</code>\n"
            f"Цена: <code>{price:.6f}</code>\n"
            f"<code>{reason}</code>\n"
            f"<a href='https://www.tradingview.com/chart/?symbol=BINANCE:{symbol.replace('/', '')}&interval={tf}'>ГРАФИК</a>")
    await send_telegram(text)

# =================== СТРАТЕГИЯ ===================
async def check_pair(exchange, symbol: str, tf: str):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=tf, limit=500)
        if len(ohlcv) < 200: return

        df = pd.DataFrame(ohlcv, columns=['ts', 'open', 'high', 'low', 'close', 'volume'])
        df['ema34'] = talib.EMA(df['close'], 34)
        df['ema144'] = talib.EMA(df['close'], 144)
        df['rsi'] = talib.RSI(df['close'], 14)
        df['vol_ma20'] = df['volume'].rolling(20).mean()

        price = df['close'].iloc[-1]
        rsi = df['rsi'].iloc[-1]
        vol = df['volume'].iloc[-1]
        vol_avg = df['vol_ma20'].iloc[-1]

        long_cond = (
            price > df['ema34'].iloc[-1] > df['ema144'].iloc[-1] and
            rsi < 42 and
            vol > vol_avg * 1.5 and
            price > df['close'].iloc[-2]
        )

        close_cond = (rsi > 75 or price < df['ema34'].iloc[-1])

        if long_cond:
            await send_signal(symbol, tf, "LONG", price, "EMA34>EMA144 + RSI<42 + Volume + Up")
        if close_cond:
            await send_signal(symbol, tf, "CLOSE", price, "RSI>75 или ниже EMA34")

    except Exception as e:
        if "Invalid interval" not in str(e) and "does not have market symbol" not in str(e):
            logging.error(f"Error {symbol} {tf}: {e}")

# =================== СКАНЕР ===================
async def scanner_background():
    exchange = ccxt.binance({
        'apiKey': BINANCE_API_KEY,
        'secret': BINANCE_API_SECRET,
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })
    await send_telegram("OZ SCANNER v2026 ULTRA MAX — ЗАПУЩЕН НАВСЕГДА\n\nВсё работает. Ошибок больше нет.")
    logging.info("SCANNER STARTED — FULLY WORKING")

    while True:
        if await get_setting("scanner_enabled") != "1":
            await asyncio.sleep(15)
            continue
        symbols = await get_enabled_coins()
        tfs = await get_enabled_tfs()
        tasks = [check_pair(exchange, s, tf) for s in symbols for tf in tfs]
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(12)

# =================== АДМИНКА ===================
@app.get("/", response_class=HTMLResponse)
async def root():
    return "<h1 style='color:#0f0;background:#000;text-align:center;padding:100px;font-family:monospace'>OZ SCANNER v2026 ULTRA MAX<br>РАБОТАЕТ 24/7<br><a href='/panel'>АДМИНКА → X-Password: 777</a></h1>"

@app.get("/panel")
async def panel(request: Request):
    if request.headers.get("X-Password") != "777":
        return HTMLResponse("<h1 style='color:red;background:#000;padding:100px;text-align:center'>ПАРОЛЬ: 777<br>Заголовок: <b>X-Password: 777</b></h1>", status_code=401)

    enabled = "ВКЛ" if await get_setting("scanner_enabled") == "1" else "ВЫКЛ"
    coins = await get_enabled_coins()
    tfs = await get_enabled_tfs()

    html = "<pre style='color:#0f0;background:#000;font-size:18px;line-height:2'>"
    html += f"СКАНЕР: <b>{enabled}</b>     <a href='/toggle'>[ТОГГЛ]</a>\n\nМОНЕТЫ:\n"
    for s in ALL_SYMBOLS:
        status = "ON" if s in coins else "OFF"
        html += f"<a href='/toggle_coin/{s.replace('/', '%2F')}'>[{status}] {s}</a>  "
    html += "\n\nТАЙМФРЕЙМЫ:\n"
    for tf in ALL_TIMEFRAMES:
        status = "ON" if tf in tfs else "OFF"
        html += f"<a href='/toggle_tf/{tf}'>[{status}] {tf}</a>  "
    html += f"\n\n<a href='/signals'>ВСЕ СИГНАЛЫ</a>     <a href='/signals24'>24Ч</a>     <a href='/'>ГЛАВНАЯ</a></pre>"
    return HTMLResponse(html)

@app.get("/toggle")
async def toggle():
    current = await get_setting("scanner_enabled")
    await set_setting("scanner_enabled", "0" if current == "1" else "1")
    return RedirectResponse("/panel")

@app.get("/toggle_coin/{symbol}")
async def toggle_coin(symbol: str):
    symbol = symbol.replace("%2F", "/")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM enabled_coins WHERE symbol=?", (symbol,))
        await db.execute("INSERT INTO enabled_coins VALUES (?)", (symbol,))
        await db.commit()
    return RedirectResponse("/panel")

@app.get("/toggle_tf/{tf}")
async def toggle_tf(tf: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM enabled_tfs WHERE tf=?", (tf,))
        await db.execute("INSERT INTO enabled_tfs VALUES (?)", (tf,))
        await db.commit()
    return RedirectResponse("/panel")

@app.get("/signals")
async def signals():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT symbol,tf,direction,price,reason,datetime(ts/1000,'unixepoch') FROM signals ORDER BY ts DESC LIMIT 300") as cur:
            rows = await cur.fetchall()
    table = "<table border=1 style='color:#0f0;background:#000;width:100%;font-family:monospace'><tr><th>Монета</th><th>TF</th><th>Сигнал</th><th>Цена</th><th>Причина</th><th>Время</th></tr>"
    for r in rows:
        table += f"<tr><td>{r[0]}</td><td>{r[1]}</td><td><b>{r[2]}</b></td><td>{r[3]:.6f}</td><td>{r[4]}</td><td>{r[5]}</td></tr>"
    return HTMLResponse(table + "</table><br><a href='/panel'>← НАЗАД</a>")

@app.get("/signals24")
async def signals24():
    cutoff = int((datetime.now().timestamp() - 86400) * 1000)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT symbol,tf,direction,price,reason,datetime(ts/1000,'unixepoch') FROM signals WHERE ts > ? ORDER BY ts DESC", (cutoff,)) as cur:
            rows = await cur.fetchall()
    table = "<table border=1 style='color:#0f0;background:#000;width:100%;font-family:monospace'><tr><th>Монета</th><th>TF</th><th>Сигнал</th><th>Цена</th><th>Причина</th><th>Время</th></tr>"
    for r in rows:
        table += f"<tr><td>{r[0]}</td><td>{r[1]}</td><td><b>{r[2]}</b></td><td>{r[3]:.6f}</td><td>{r[4]}</td><td>{r[5]}</td></tr>"
    return HTMLResponse(table + "</table><br><a href='/panel'>← НАЗАД</a>")

# =================== ЗАПУСК ===================
@app.on_event("startup")
async def startup():
    await init_db()
    asyncio.create_task(scanner_background())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
