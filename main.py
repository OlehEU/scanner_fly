# main.py — OZ SCANNER v2026 ULTRA ФИНАЛЬНАЯ ВЕРСИЯ
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

app = FastAPI(title="OZ SCANNER v2026 ULTRA")
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(message)s')

ALL_SYMBOLS = ["BTC/USDT","ETH/USDT","SOL/USDT","XRP/USDT","ADA/USDT","DOGE/USDT","AVAX/USDT","LINK/USDT","DOT/USDT","MATIC/USDT",
               "BNB/USDT","TON/USDT","TRX/USDT","NEAR/USDT","APT/USDT","ARB/USDT","OP/USDT","SUI/USDT","INJ/USDT","PEPE/USDT"]
ALL_TIMEFRAMES = ['1m','5m','15m','45m','1h','4h']
DB_PATH = "oz_2026.db"

# =================== БАЗА ===================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS signals(id INTEGER PRIMARY KEY,symbol TEXT,tf TEXT,direction TEXT,price REAL,reason TEXT,ts INTEGER);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE IF NOT EXISTS enabled_coins(symbol TEXT PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS enabled_tfs(tf TEXT PRIMARY KEY);
        ''')
        await db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('scanner_enabled','1'),('password','777')")
        for s in ALL_SYMBOLS: await db.execute("INSERT OR IGNORE INTO enabled_coins VALUES(?)",(s,))
        for tf in ALL_TIMEFRAMES: await db.execute("INSERT OR IGNORE INTO enabled_tfs VALUES(?)",(tf,))
        await db.commit()

async def get_setting(key): 
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?",(key,)) as cur:
            r = await cur.fetchone(); return r[0] if r else None
async def set_setting(key,value):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)",(key,value))
        await db.commit()

async def get_enabled_coins(): 
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT symbol FROM enabled_coins") as cur:
            return [r[0] async for r in cur]
async def get_enabled_tfs():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT tf FROM enabled_tfs") as cur:
            return [r[0] async for r in cur]

# =================== ОТПРАВКА СИГНАЛОВ ===================
async def send_telegram(text, photo_url=None):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    async with aiohttp.ClientSession() as s:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        data = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if photo_url:
            await s.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto",
                        data={"chat_id": TELEGRAM_CHAT_ID, "photo": photo_url, "caption": text, "parse_mode": "HTML"})
        else:
            await s.post(url, json=data)

async def send_signal(symbol, tf, direction, price, reason):
    ts = int(datetime.now().timestamp()*1000)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO signals(symbol,tf,direction,price,reason,ts) VALUES(?,?,?,?,?,?)",
                        (symbol,tf,direction,price,reason,ts))
        await db.commit()

    tv_link = f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol.replace('/','')}&interval={tf}"
    snapshot = f"https://www.tradingview.com/chart/?symbol=BINANCE:{symbol.replace('/','')}&interval={tf}&snapshot=true"
    text = (f"OZ SCANNER 2026\n"
            f"<b>{direction}</b>\n"
            f"<code>{symbol}</code> | <b>{tf}</b>\n"
            f"Цена: <b>{price:.6f}</b>\n"
            f"{reason}\n\n"
            f"<a href='{tv_link}'>Открыть график</a>")
    
    await send_telegram(text, snapshot)
    if WEBHOOK_URL:
        async with aiohttp.ClientSession() as s:
            await s.post(WEBHOOK_URL, json={"symbol":symbol.replace("/",""),"price":price,
                                           "signal":direction.lower(),"secret":WEBHOOK_SECRET or ""})

# =================== ТОЧНАЯ СТРАТЕГИЯ OZ 2026 ===================
def bullish_divergence(price, rsi):
    lows_p = talib.MIN(price, 15)[-15:]
    lows_r = talib.MIN(rsi, 15)[-15:]
    return lows_p[-1] > lows_p[-10] and lows_r[-1] < lows_r[-10]

def bearish_divergence(price, rsi):
    highs_p = talib.MAX(price, 15)[-15:]
    highs_r = talib.MAX(rsi, 15)[-15:]
    return highs_p[-1] < highs_p[-10] and highs_r[-1] > highs_r[-10]

async def check_pair(exchange, symbol, tf):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, tf, limit=300)
        df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
        df['ema34'] = talib.EMA(df['close'], 34)
        df['ema144'] = talib.EMA(df['close'], 144)
        df['rsi'] = talib.RSI(df['close'], 14)
        df['vol_ma'] = df['volume'].rolling(20).mean()

        c = df['close'].values
        rsi = df['rsi'].values
        vol = df['volume'].iloc[-1]
        vol_avg = df['vol_ma'].iloc[-1]
        price = c[-1]
        prev_price = c[-2]

        # LONG
        long = (c[-1] > df['ema34'].iloc[-1] > df['ema144'].iloc[-1] and
                rsi[-1] < 42 and
                vol > vol_avg * 1.5 and
                (bullish_divergence(c, rsi) or rsi[-2] < 30) and
                c[-1] > prev_price)

        # CLOSE
        close = (bearish_divergence(c, rsi) or
                 rsi[-1] > 73 or
                 c[-1] < df['ema34'].iloc[-1])

        if long:
            await send_signal(symbol, tf, "LONG", price, "RSI+Дивергенция+EMA+Объём")
        if close:
            await send_signal(symbol, tf, "CLOSE", price, "Медвежий сигнал/Перегрев")
    except Exception as e:
        logging.error(f"{symbol} {tf}: {e}")

# =================== СКАНЕР ===================
async def scanner_background():
    exchange = ccxt.binance({'apiKey': BINANCE_API_KEY,'secret': BINANCE_API_SECRET,
                             'enableRateLimit': True,'options':{'defaultType':'future'}})
    await send_telegram("OZ SCANNER v2026 ULTRA — ЗАПУЩЕН НАВСЕГДА! 🔥")
    logging.info("OZ SCANNER v2026 ULTRA — РАБОТАЕТ!")
    while True:
        if await get_setting("scanner_enabled") != "1":
            await asyncio.sleep(15); continue
        symbols = await get_enabled_coins()
        tfs = await get_enabled_tfs()
        tasks = [check_pair(exchange, s, tf) for s in symbols for tf in tfs]
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(10)

# =================== АДМИНКА ===================
ADMIN_HTML = '<!DOCTYPE html><html><head><title>OZ 2026</title><meta charset="utf-8"><style>body{font-family:Arial;background:#000;color:#0f0;text-align:center;padding:20px;}</style></head><body><h1>OZ SCANNER v2026 ULTRA</h1><div id="c">Загрузка...</div><script>async function l(){fetch("/api/panel").then(r=>r.text()).then(t=>document.getElementById("c").innerHTML=t)}l();setInterval(l,10000);</script></body></html>'

@app.get("/", response_class=HTMLResponse) 
async def root(): return ADMIN_HTML
@app.get("/health") 
async def health(): return {"status":"ok"}

@app.get("/api/panel")
async def panel(request: Request):
    if request.headers.get("X-Password") != "777":
        return HTMLResponse("<h2>Пароль 777 (заголовок X-Password)</h2>")
    enabled = "ВКЛЮЧЁН" if await get_setting("scanner_enabled")=="1" else "ВЫКЛЮЧЕН"
    coins = await get_enabled_coins(); tfs = await get_enabled_tfs()
    html = f"<h2>СКАНЕР: {enabled} | <a href='/toggle'>ТОГГЛ</a></h2><hr>"
    html += "<h3>МОНЕТЫ:</h3>" + " ".join(f"<a href='/tc/{s.replace('/','%2F')}'>[{ 'ON' if s in coins else 'OFF' }] {s.split('/')[0]}</a>" for s in ALL_SYMBOLS) + "<hr>"
    html += "<h3>ТАЙМФРЕЙМЫ:</h3>" + " ".join(f"<a href='/tt/{tf}'>[{ 'ON' if tf in tfs else 'OFF' }] {tf}</a>" for tf in ALL_TIMEFRAMES) + "<hr>"
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT COUNT(*) FROM signals WHERE ts > strftime('%s','now','-1 day')*1000") as cur: day=(await cur.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM signals") as cur: total=(await cur.fetchone())[0]
    html += f"<h3>СИГНАЛЫ: 24ч — {day} | Всего — {total}</h3><hr>"
    html += "<a href='/s'>ВСЕ</a> | <a href='/s24'>24ч</a> | <a href='/s7'>7д</a> | <a href='/s30'>30д</a>"
    return HTMLResponse(html)

@app.get("/toggle") async def toggle(): await set_setting("scanner_enabled","0" if await get_setting("scanner_enabled")=="1" else "1"); return RedirectResponse("/")
@app.get("/tc/{symbol}") async def tc(symbol:str): symbol=symbol.replace("%2F","/"); async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM enabled_coins WHERE symbol=?",(symbol,)); await db.execute("INSERT INTO enabled_coins VALUES(?)",(symbol,)); await db.commit(); return RedirectResponse("/")
@app.get("/tt/{tf}") async def tt(tf:str): async with aiosqlite.connect(DB_PATH) as db: await db.execute("DELETE FROM enabled_tfs WHERE tf=?",(tf,)); await db.execute("INSERT INTO enabled_tfs VALUES(?)",(tf,)); await db.commit(); return RedirectResponse("/")

@app.get("/s") async def s(): return await signals_view(0)
@app.get("/s24") async def s24(): return await signals_view(86400)
@app.get("/s7") async def s7(): return await signals_view(7*86400)
@app.get("/s30") async def s30(): return await signals_view(30*86400)
async def signals_view(days=0):
    cutoff = int((datetime.now().timestamp() - days*86400)*1000) if days else 0
    async with aiosqlite.connect(DB_PATH) as db:
        query = "SELECT symbol,tf,direction,price,datetime(ts/1000,'unixepoch') FROM signals WHERE ts>? ORDER BY ts DESC LIMIT 200" if days else "SELECT symbol,tf,direction,price,datetime(ts/1000,'unixepoch') FROM signals ORDER BY ts DESC LIMIT 200"
        async with db.execute(query,(cutoff,) if days else ()) as cur:
            rows = await cur.fetchall()
    table = "<table border=1 style='margin:auto;color:#0f0;background:#111;width:90%'><tr><th>Сигнал</th><th>Монета</th><th>TF</th><th>Цена</th><th>Время</th></tr>"
    for r in rows: table += f"<tr><td>{r[2]}</td><td>{r[0]}</td><td>{r[1]}</td><td>{r[3]:.6f}</td><td>{r[4]}</td></tr>"
    return HTMLResponse(table + "</table><br><a href='/'>← НАЗАД</a>")

# =================== ЗАПУСК ===================
@app.on_event("startup")
async def startup():
    await init_db()
    asyncio.create_task(scanner_background())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
