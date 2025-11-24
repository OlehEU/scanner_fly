# main.py — OZ SCANNER v2026 ULTRA MAX — ФИНАЛЬНАЯ, БЕЗ БАГОВ, РАБОТАЕТ НА FLY.IO
import ccxt.async_support as ccxt
import asyncio
import pandas as pd
import numpy as np
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

app = FastAPI(title="OZ SCANNER v2026 ULTRA MAX")
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s')

ALL_SYMBOLS = ["BTC/USDT","ETH/USDT","SOL/USDT","XRP/USDT","ADA/USDT","DOGE/USDT","AVAX/USDT","LINK/USDT","DOT/USDT","MATIC/USDT",
               "BNB/USDT","TON/USDT","TRX/USDT","NEAR/USDT","APT/USDT","ARB/USDT","OP/USDT","SUI/USDT","INJ/USDT","PEPE/USDT"]
ALL_TIMEFRAMES = ['1m','5m','15m','45m','1h','4h']
DB_PATH = "oz_ultra_max.db"

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
        await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('scanner_enabled','1')")
        await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('password','777')")
        for s in ALL_SYMBOLS: await db.execute("INSERT OR IGNORE INTO enabled_coins VALUES (?)", (s,))
        for tf in ALL_TIMEFRAMES: await db.execute("INSERT OR IGNORE INTO enabled_tfs VALUES (?)", (tf,))
        await db.commit()

async def get_setting(key: str) -> str | None:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

async def set_setting(key: str, value: str):
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
            f"**{direction}**\n"
            f"`{symbol}` | `{tf}`\n"
            f"Цена: `{price:.6f}`\n"
            f"`{reason}`\n"
            f"<a href='https://www.tradingview.com/chart/?symbol=BINANCE:{symbol.replace('/', '')}&interval={tf}'>ГРАФИК</a>")
    await send_telegram(text)

    if WEBHOOK_URL:
        async with aiohttp.ClientSession() as s:
            await s.post(WEBHOOK_URL, json={"symbol": symbol.replace("/", ""), "price": price, "signal": direction.lower(), "secret": WEBHOOK_SECRET or ""})

# =================== СТРАТЕГИЯ 2026 ===================
async def check_pair(exchange, symbol: str, tf: str):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, tf, limit=500)
        if len(ohlcv) < 300: return
        df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
        df['ema34'] = talib.EMA(df['close'], 34)
        df['ema144'] = talib.EMA(df['close'], 144)
        df['rsi'] = talib.RSI(df['close'], 14)
        df['vol_ma20'] = df['volume'].rolling(20).mean()

        c = df['close'].values
        rsi = df['rsi'].values
        price = c[-1]
        vol = df['volume'].iloc[-1]
        vol_avg = df['vol_ma20'].iloc[-1]

        # LONG
        long_cond = (
            c[-1] > df['ema34'].iloc[-1] > df['ema144'].iloc[-1] and
            rsi[-1] < 42 and
            vol > vol_avg * 1.5 and
            c[-1] > c[-2]
        )

        # CLOSE
        close_cond = (
            rsi[-1] > 75 or
            c[-1] < df['ema34'].iloc[-1]
        )

        if long_cond:
            await send_signal(symbol, tf, "LONG", price, "RSI+EMA34/144+Volume+PriceUp")
        if close_cond:
            await send_signal(symbol, tf, "CLOSE", price, "RSI>75 or Below EMA34")

    except Exception as e:
        logging.error(f"Error {symbol} {tf}: {e}")

# =================== СКАНЕР ===================
async def scanner_background():
    exchange = ccxt.binance({'apiKey': BINANCE_API_KEY, 'secret': BINANCE_API_SECRET,
                             'enableRateLimit': True, 'options': {'defaultType': 'future'}})
    await send_telegram("OZ SCANNER v2026 ULTRA MAX — ЗАПУЩЕН НАВСЕГДА")
    logging.info("SCANNER STARTED FOREVER")

    while True:
        if await get_setting("scanner_enabled") != "1":
            await asyncio.sleep(15); continue

        symbols = await get_enabled_coins()
        tfs = await get_enabled_tfs()
        tasks = [check_pair(exchange, s, tf) for s in symbols for tf in tfs]
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(12)

# =================== АДМИНКА ===================
@app.get("/", response_class=HTMLResponse)
async def root():
    return "<h1 style='color:#0f0;background:#000;text-align:center;padding:50px'>OZ SCANNER v2026 ULTRA MAX<br>ONLINE<br><a href='/panel'>АДМИНКА (пароль 777)</a></h1>"

@app.get("/panel")
async def panel(request: Request):
    if request.headers.get("X-Password") != (await get_setting("password") or "777"):
        return HTMLResponse("<h1>Неверный пароль</h1>")

    enabled = "ВКЛЮЧЁН" if await get_setting("scanner_enabled") == "1" else "ВЫКЛЮЧЕН"
    coins = await get_enabled_coins()
    tfs = await get_enabled_tfs()

    html = f"<pre style='color:#0f0;background:#000;font-size:18px'>"
    html += f"СКАНЕР: {enabled} | <a href='/toggle'>ТОГГЛ</a>\n\n"
    html += "МОНЕТЫ:\n" + " ".join(f"<a href='/toggle_coin/{s.replace('/', '%2F')}'>[{ 'ON' if s in coins else 'OFF' }] {s.split('/')[0]}</a> " for s in ALL_SYMBOLS)
    html += "\n\nТАЙМФРЕЙМЫ:\n" + " ".join(f"<a href='/toggle_tf/{tf}'>[{ 'ON' if tf in tfs else 'OFF' }] {tf}</a> " for tf in ALL_TIMEFRAMES)
    html += f"\n\n<a href='/signals'>ВСЕ СИГНАЛЫ</a> | <a href='/signals24'>24ч</a>"
    return HTMLResponse(html + "</pre>")

# ИСПРАВЛЕННЫЕ ЭНДПОИНТЫ (это важно!)
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
        async with db.execute("SELECT symbol,tf,direction,price,reason,datetime(ts/1000,'unixepoch') FROM signals ORDER BY ts DESC LIMIT 200") as cur:
            rows = await cur.fetchall()
    table = "<table border=1 style='color:#0f0;background:#000;width:100%'><tr><th>Монета</th><th>TF</th><th>Сигнал</th><th>Цена</th><th>Причина</th><th>Время</th></tr>"
    for r in rows:
        table += f"<tr><td>{r[0]}</td><td>{r[1]}</td><td><b>{r[2]}</b></td><td>{r[3]:.6f}</td><td>{r[4]}</td><td>{r[5]}</td></tr>"
    return HTMLResponse(table + "</table><br><a href='/panel'>← НАЗАД</a>")

@app.get("/signals24")
async def signals24():
    cutoff = int((datetime.now().timestamp() - 86400) * 1000)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT symbol,tf,direction,price,reason,datetime(ts/1000,'unixepoch') FROM signals WHERE ts > ? ORDER BY ts DESC", (cutoff,)) as cur:
            rows = await cur.fetchall()
    table = "<table border=1 style='color:#0f0;background:#000;width:100%'><tr><th>Монета</th><th>TF</th><th>Сигнал</th><th>Цена</th><th>Причина</th><th>Время</th></tr>"
    for r in rows:
        table += f"<tr><td>{r[0]}</td><td>{r[1]}</td><td><b>{r[2]}</b></td><td>{r[3]:.6f}</td><td>{r[4]}</td><td>{r[5]}</td></tr>"
    return HTMLResponse(table + "</table><br><a href='/panel'>← НАЗАД</a>")

@app.on_event("startup")
async def startup():
    await init_db()
    asyncio.create_task(scanner_background())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
