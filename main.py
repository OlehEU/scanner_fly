# main.py — OZ SCANNER v2026 ULTRA SERVER EDITION (Fly.io НЕ УБЬЁТ!)
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
from fastapi.responses import HTMLResponse
import aiohttp

# =================== СЕКРЕТЫ ===================
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))

# =================== FASTAPI ===================
app = FastAPI(title="OZ SCANNER v2026 ULTRA SERVER")
logging.basicConfig(level=logging.INFO)

ALL_SYMBOLS = ["BTC/USDT","ETH/USDT","SOL/USDT","XRP/USDT","ADA/USDT","DOGE/USDT","AVAX/USDT","LINK/USDT","DOT/USDT","MATIC/USDT",
               "BNB/USDT","TON/USDT","TRX/USDT","NEAR/USDT","APT/USDT","ARB/USDT","OP/USDT","SUI/USDT","INJ/USDT","PEPE/USDT"]
ALL_TIMEFRAMES = ['1m','5m','15m','45m','1h','4h']

# =================== БАЗА ===================
async def init_db():
    async with aiosqlite.connect("oz_server.db") as db:
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

# =================== ПОМОЩНИКИ ===================
async def get_setting(key): 
    async with aiosqlite.connect("oz_server.db") as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else None

async def set_setting(key, value):
    async with aiosqlite.connect("oz_server.db") as db:
        await db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key,value))
        await db.commit()

async def get_enabled_coins():
    async with aiosqlite.connect("oz_server.db") as db:
        async with db.execute("SELECT symbol FROM enabled_coins") as cur:
            return [r[0] async for r in cur]

async def get_enabled_tfs():
    async with aiosqlite.connect("oz_server.db") as db:
        async with db.execute("SELECT tf FROM enabled_tfs") as cur:
            return [r[0] async for r in cur]

# =================== ОТПРАВКА СИГНАЛОВ ===================
async def send_telegram(text):
    async with aiohttp.ClientSession() as s:
        await s.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                    json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})

async def send_signal(symbol, tf, direction, price, reason=""):
    ts = int(datetime.now().timestamp() * 1000)
    async with aiosqlite.connect("oz_server.db") as db:
        await db.execute("INSERT INTO signals (symbol,tf,direction,price,reason,ts) VALUES (?,?,?,?,?,?)",
                        (symbol,tf,direction,price,reason,ts))
        await db.commit()

    text = (f"OZ 2026 SERVER\n"
            f"{direction}\n"
            f"{symbol} | {tf}\n"
            f"Цена: {price:.6f}\n"
            f"{reason}\n"
            f"<a href='https://www.tradingview.com/chart/?symbol=BINANCE:{symbol.replace('/','')}&interval={tf}'>ГРАФИК</a>")
    
    await send_telegram(text)
    if WEBHOOK_URL:
        async with aiohttp.ClientSession() as s:
            await s.post(WEBHOOK_URL, json={"symbol": symbol.replace("/",""), "price": price, "signal": direction.lower(), "secret": WEBHOOK_SECRET or ""})

# =================== СТРАТЕГИЯ (вставь свою точную) ===================
async def check_pair(exchange, symbol, tf):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, tf, limit=500)
        df = pd.DataFrame(ohlcv, columns=['ts','o','h','l','c','v'])
        df['ema34'] = talib.EMA(df['c'], 34)
        df['ema144'] = talib.EMA(df['c'], 144)
        df['rsi'] = talib.RSI(df['c'], 14)
        price = df['c'].iloc[-1]

        # ←←← ВСТАВЬ СВОЮ ТОЧНУЮ СТРАТЕГИЮ СЮДА (дивергенции и т.д.)
        # Пока тестовый сигнал каждые ~2 минуты
        if hash(str(ohlcv[-10:])) % 120 == 0:
            await send_signal(symbol, tf, "LONG", price, "OZ 2026 SERVER — ТЕСТ/РАБОЧИЙ СИГНАЛ")
    except Exception as e:
        logging.error(f"{symbol} {tf}: {e}")

# =================== СКАНЕР В ФОНЕ ===================
async def scanner_background():
    exchange = ccxt.binance({
        'apiKey': BINANCE_API_KEY, 'secret': BINANCE_API_SECRET,
        'enableRateLimit': True, 'options': {'defaultType': 'future'}
    })
    while True:
        if await get_setting("scanner_enabled") != "1":
            await asyncio.sleep(15)
            continue
        symbols = await get_enabled_coins()
        tfs = await get_enabled_tfs()
        tasks = [check_pair(exchange, s, tf) for s in symbols for tf in tfs]
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(12)

# =================== HTML АДМИНКА ===================
admin_html = """
<!DOCTYPE html>
<html>
<head><title>OZ SCANNER v2026 ULTRA</title><meta charset="utf-8"></head>
<body style="font-family:Arial;background:#000;color:#0f0;text-align:center;padding:20px;">
<h1>OZ SCANNER v2026 ULTRA SERVER</h1>
<div id="content"></div>
<script>
async function load(){fetch('/api/panel').then(r=>r.text()).then(t=>document.getElementById('content').innerHTML=t)}
load(); setInterval(load,10000);
</script>
</body></html>
"""

@app.get("/", response_class=HTMLResponse)
async def root(): 
    return admin_html

@app.get("/api/panel")
async def panel(request: Request):
    password = await get_setting("password")
    if request.headers.get("X-Password","") != password:
        return "<h2>Введи пароль 777 в заголовке X-Password (или просто зайди через телегу)</h2>"

    enabled = "ВКЛЮЧЁН" if await get_setting("scanner_enabled") == "1" else "ВЫКЛЮЧЁН"
    coins = await get_enabled_coins()
    tfs = await get_enabled_tfs()

    html = f"<h2>СКАНЕР: {enabled} | <a href='/toggle'>ТОГГЛ</a></h2><hr>"
    html += "<h3>МОНЕТЫ:</h3>" + " ".join([f"<a href='/toggle_coin/{s}'>[{ 'ON' if s in coins else 'OFF' }] {s.split('/')[0]}</a>" for s in ALL_SYMBOLS]) + "<hr>"
    html += "<h3>ТАЙМФРЕЙМЫ:</h3>" + " ".join([f"<a href='/toggle_tf/{tf}'>[{ 'ON' if tf in tfs else 'OFF' }] {tf}</a>" for tf in ALL_TIMEFRAMES]) + "<hr>"
    
    # Статистика
    async with aiosqlite.connect("oz_server.db") as db:
        async with db.execute("SELECT COUNT(*) FROM signals WHERE ts > strftime('%s','now','-1 day')*1000") as cur:
            day = (await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM signals") as cur:
            total = (await cur.fetchone())[0]
    html += f"<h3>СИГНАЛЫ: 24ч — {day} | Всего — {total}</h3><hr>"
    html += "<a href='/signals'>ВСЕ СИГНАЛЫ</a> | <a href='/signals24'>24ч</a>"

    return HTMLResponse(html)

@app.get("/toggle") async def toggle(): await set_setting("scanner_enabled", "0" if await get_setting("scanner_enabled")=="1" else "1"); return "<script>location='/'</script>"
@app.get("/toggle_coin/{symbol}") async def tc(symbol:str):
    async with aiosqlite.connect("oz_server.db") as db:
        await db.execute("DELETE FROM enabled_coins WHERE symbol=?", (symbol,))
        await db.execute("INSERT INTO enabled_coins VALUES (?)", (symbol,))
        await db.commit()
    return "<script>location='/'</script>"

@app.get("/toggle_tf/{tf}") async def tt(tf:str):
    async with aiosqlite.connect("oz_server.db") as db:
        await db.execute("DELETE FROM enabled_tfs WHERE tf=?", (tf,))
        await db.execute("INSERT INTO enabled_tfs VALUES (?)", (tf,))
        await db.commit()
    return "<script>location='/'</script>"

@app.get("/signals") async def signals():
    async with aiosqlite.connect("oz_server.db") as db:
        async with db.execute("SELECT symbol,tf,direction,price,datetime(ts/1000,'unixepoch') FROM signals ORDER BY ts DESC LIMIT 100") as cur:
            rows = await cur.fetchall()
    table = "<table border=1 style='margin:auto;color:#0f0;background:#000'><tr><th>СИГНАЛ</th><th>МОНЕТА</th><th>TF</th><th>ЦЕНА</th><th>ВРЕМЯ</th></tr>"
    for r in rows: table += f"<tr><td>{r[2]}</td><td>{r[0]}</td><td>{r[1]}</td><td>{r[3]:.6f}</td><td>{r[4]}</td></tr>"
    return HTMLResponse(table + "</table><br><a href='/'>НАЗАД</a>")

@app.get("/signals24") async def signals24():
    cutoff = int((datetime.now().timestamp() - 86400)*1000)
    async with aiosqlite.connect("oz_server.db") as db:
        async with db.execute("SELECT symbol,tf,direction,price,datetime(ts/1000,'unixepoch') FROM signals WHERE ts>? ORDER BY ts DESC", (cutoff,)) as cur:
            rows = await cur.fetchall()
    # тот же вывод таблицы
    table = "<table border=1 style='margin:auto;color:#0f0;background:#000'><tr><th>СИГНАЛ</th><th>МОНЕТА</th><th>TF</th><th>ЦЕНА</th><th>ВРЕМЯ</th></tr>"
    for r in rows: table += f"<tr><td>{r[2]}</td><td>{r[0]}</td><td>{r[1]}</td><td>{r[3]:.6f}</td><td>{r[4]}</td></tr>"
    return HTMLResponse(table + "</table><br><a href='/'>НАЗАД</a>")

# =================== ЗАПУСК ===================
@app.on_event("startup")
async def startup():
    await init_db()
    asyncio.create_task(scanner_background())
    await send_telegram("OZ SCANNER v2026 SERVER EDITION — ЗАПУЩЕН НАВСЕГДА")
    logging.info("OZ SCANNER v2026 ULTRA SERVER — ЖИВЁТ ВЕЧНО")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
