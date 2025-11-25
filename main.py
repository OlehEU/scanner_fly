# main.py — OZ SCANNER 2026 — КРАСИВАЯ ФОРМА ПАРОЛЯ (без расширений)
import ccxt.async_support as ccxt
import asyncio
import pandas as pd
import talib
import aiosqlite
import os
import logging
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
import aiohttp

app = FastAPI()
templates = Jinja2Templates(directory=".")  # шаблоны в корне

# Твои 3 монеты + только 1h по умолчанию
ALL_SYMBOLS = ["XRP/USDT", "SOL/USDT", "DOGE/USDT"]
ALL_TIMEFRAMES = ['1m', '5m', '15m', '1h', '4h']
DB_PATH = "oz.db"

# =================== БАЗА ===================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, tf TEXT, direction TEXT, price REAL, reason TEXT, ts INTEGER);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS enabled_coins (symbol TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS enabled_tfs (tf TEXT PRIMARY KEY);
        ''')
        await db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('password','777')")
        await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('scanner_enabled','1')")
        for s in ALL_SYMBOLS:
            await db.execute("INSERT OR IGNORE INTO enabled_coins VALUES (?)", (s,))
        await db.execute("DELETE FROM enabled_tfs")
        await db.execute("INSERT INTO enabled_tfs VALUES ('1h')")
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

# =================== СИГНАЛЫ И СКАНЕР (без изменений) ===================
import aiohttp
async def send_telegram(text: str):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id: return
    async with aiohttp.ClientSession() as s:
        await s.post(f"https://api.telegram.org/bot{token}/sendMessage",
                     json={"chat_id": int(chat_id), "text": text, "parse_mode": "HTML", "disable_web_page_preview=True})

async def send_signal(symbol, tf, direction, price, reason):
    ts = int(datetime.now().timestamp() * 1000)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO signals (symbol,tf,direction,price,reason,ts) VALUES (?,?,?,?,?,?)",
                        (symbol, tf, direction, price, reason, ts))
        await db.commit()
    text = f"<b>{direction}</b> {symbol} {tf}\nЦена: <code>{price:.6f}</code>\n{reason}\nhttps://www.tradingview.com/chart/?symbol=BINANCE:{symbol.replace('/', '')}&interval={tf}"
    await send_telegram(text)

async def check_pair(exchange, symbol, tf):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, tf, limit=500)
        if len(ohlcv) < 200: return
        df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
        df['ema34'] = talib.EMA(df['close'], 34)
        df['ema144'] = talib.EMA(df['close'], 144)
        df['rsi'] = talib.RSI(df['close'], 14)
        df['vol_ma20'] = df['volume'].rolling(20).mean()

        price = df['close'].iloc[-1]
        rsi = df['rsi'].iloc[-1]
        vol = df['volume'].iloc[-1]
        vol_avg = df['vol_ma20'].iloc[-1]

        long = price > df['ema34'].iloc[-1] > df['ema144'].iloc[-1] and rsi < 42 and vol > vol_avg * 1.5 and price > df['close'].iloc[-2]
        close = rsi > 75 or price < df['ema34'].iloc[-1]

        if long: await send_signal(symbol, tf, "LONG", price, "СИЛАЧ")
        if close: await send_signal(symbol, tf, "CLOSE", price, "Фиксируем")
    except: pass

async def scanner_background():
    ex = ccxt.binance({'apiKey': os.getenv("BINANCE_API_KEY"), 'secret': os.getenv("BINANCE_API_SECRET"),
                       'enableRateLimit': True, 'options': {'defaultType': 'future'}})
    await send_telegram("OZ SCANNER 2026 ЗАПУЩЕН\nТолько XRP • SOL • DOGE | 1h по умолчанию")
    while True:
        if await get_setting("scanner_enabled") != "1":
            await asyncio.sleep(15); continue
        tasks = [check_pair(ex, s, tf) for s in await get_enabled_coins() for tf in await get_enabled_tfs()]
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(15)

# =================== ГЛАВНАЯ + ФОРМА ПАРОЛЯ ===================
@app.get("/", response_class=HTMLResponse)
async def root():
    return """
    <html><head><title>OZ SCANNER 2026</title>
    <style>body{background:#000;color:#0f0;font-family:monospace;text-align:center;padding-top:15%}</style>
    </head><body>
    <h1>OZ SCANNER 2026</h1>
    <h2>Только XRP • SOL • DOGE</h2>
    <form action="/login" method="post">
      <input type="password" name="password" placeholder="Пароль" style="font-size:20px;padding:10px" required>
      <button type="submit" style="font-size:20px;padding:10px 20px">ВОЙТИ</button>
    </form>
    </body></html>
    """

@app.post("/login")
async def login(password: str = Form(...)):
    if password == "777":
        return RedirectResponse("/panel", status_code=303)
    else:
        return HTMLResponse("<h1 style='color:red;background:#000;padding:100px'>Неверный пароль</h1>")

# =================== АДМИНКА (теперь без заголовков) ==================
@app.get("/panel", response_class=HTMLResponse)
async def panel(request: Request):
    enabled = "ВКЛ" if await get_setting("scanner_enabled") == "1" else "ВЫКЛ"
    coins = await get_enabled_coins()
    tfs = await get_enabled_tfs()

    html = "<pre style='color:#0f0;background:#000;font-size:20px;line-height:2.5;text-align:center'>"
    html += f"СКАНЕР: <b>{enabled}</b>    <a href='/toggle'>[ВКЛ/ВЫКЛ]</a>\n\nМОНЕТЫ:\n"
    for s in ALL_SYMBOLS:
        status = "ON" if s in coins else "OFF"
        html += f"<a href='/toggle_coin/{s.replace('/', '%2F')}'>[{status}] {s}</a>   "
    html += "\n\nТАЙМФРЕЙМЫ:\n"
    for tf in ALL_TIMEFRAMES:
        status = "ON" if tf in tfs else "OFF"
        html += f"<a href='/toggle_tf/{tf}'>[{status}] {tf}</a>   "
    html += f"\n\n<a href='/signals'>СИГНАЛЫ</a>    <a href='/logout'>ВЫХОД</a></pre>"
    return HTMLResponse(html)

@app.get("/toggle")
async def toggle():
    cur = await get_setting("scanner_enabled")
    await set_setting("scanner_enabled", "0" if cur == "1" else "1")
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
        await db.execute("INSERT INTO enabled_tfs VALUES (?)", (tf,)
        await db.commit()
    return RedirectResponse("/panel")

@app.get("/signals", response_class=HTMLResponse)
async def signals():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT symbol,tf,direction,price,datetime(ts/1000,'unixepoch') FROM signals ORDER BY ts DESC LIMIT 200") as cur:
            rows = await cur.fetchall()
    t = "<table border=1 style='color:#0f0;background:#000;width:100%;margin:20px auto'><tr><th>Монета</th><th>TF</th><th>Сигнал</th><th>Цена</th><th>Время</th></tr>"
    for r in rows:
        t += f"<tr><td>{r[0]}</td><td>{r[1]}</td><td><b>{r[2]}</b></td><td>{r[3]:.6f}</td><td>{r[4]}</td></tr>"
    return HTMLResponse(t + "</table><br><a href='/panel'>НАЗАД</a>")

@app.get("/logout")
async def logout():
    return RedirectResponse("/")

@app.on_event("startup")
async def startup():
    await init_db()
    asyncio.create_task(scanner_background())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
