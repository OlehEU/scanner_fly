"""
=============================================================================
🚀 OZ SCANNER ULTRA PRO v3.5.2 | UI 3.2.1 RESTORED
=============================================================================
- UI: Полностью возвращен интерфейс версии 3.2.1.
- Logic: Исправлена ошибка Series Ambiguity (iloc[-1]).
- Stability: Сохранение настроек в БД, кулдауны, фикс точности цен.
=============================================================================
"""

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
import aiohttp
from contextlib import asynccontextmanager

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("OZ_ULTRA")

# ========================= КОНФИГУРАЦИЯ =========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://bot-fly-oz.fly.dev/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "oz_secret_key")

ALL_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT", 
    "DOGE/USDT", "1000SHIB/USDT", "1000PEPE/USDT", "1000BONK/USDT", 
    "1000FLOKI/USDT", "1000SATS/USDT", "NEAR/USDT", "SUI/USDT", "TIA/USDT"
]

DB_PATH = "oz_ultra_v3.db"
COOLDOWNS = {'1m': 240, '5m': 480, '15m': 720, '30m': 1200, '1h': 3600, '4h': 10800}
LAST_SIGNALS = {} 

# ========================= СЛУЖЕБНЫЕ ФУНКЦИИ =========================

def get_rounded_price(price: float) -> float:
    if price < 0.0001: return round(price, 8)
    elif price < 0.05: return round(price, 7)
    elif price < 1.0: return round(price, 6)
    elif price < 100.0: return round(price, 4)
    else: return round(price, 2)

async def send_to_webhook(payload: dict):
    headers = {"X-Webhook-Secret": WEBHOOK_SECRET, "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(WEBHOOK_URL, json=payload, headers=headers, timeout=10) as resp:
                if resp.status == 200: await update_stat('signals_sent')
        except: pass

async def send_tg(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"})
        except: pass

# ========================= БАЗА ДАННЫХ =========================

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS coin_settings (
                symbol TEXT PRIMARY KEY, tf TEXT DEFAULT '1h', enabled INTEGER DEFAULT 0
            );
        ''')
        for key in ['long_enabled', 'short_enabled', 'close_enabled', 'password']:
            val = '777' if key == 'password' else '1'
            await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (key, val))
        for s_key in ['total_scans', 'signals_sent', 'errors']:
            await db.execute("INSERT OR IGNORE INTO stats (key,value) VALUES (?, 0)", (s_key,))
        for s in ALL_SYMBOLS:
            await db.execute("INSERT OR IGNORE INTO coin_settings (symbol) VALUES (?)", (s,))
        await db.commit()

async def update_stat(key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE stats SET value = value + 1 WHERE key=?", (key,))
        await db.commit()

async def get_setting(key: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] == '1' if row else False

# ========================= ЯДРО СКАНЕРА =========================

async def check_pair(exchange, symbol):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT enabled, tf FROM coin_settings WHERE symbol=?", (symbol,)) as cur:
            row = await cur.fetchone()
    
    if not row or not row[0]: return 
    tf = row[1]
    
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=tf, limit=250)
        if not ohlcv or len(ohlcv) < 201: return
        
        df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
        
        df['ema34'] = talib.EMA(df['close'], 34)
        df['ema55'] = talib.EMA(df['close'], 55)
        df['ema200'] = talib.EMA(df['close'], 200)
        df['rsi'] = talib.RSI(df['close'], 14)
        df['atr'] = talib.ATR(df['high'], df['low'], df['close'], 14)
        
        # FIX: Явное извлечение последнего значения через .iloc[-1]
        c = df['close'].iloc[-1]
        p = df['close'].iloc[-2]
        rsi = df['rsi'].iloc[-1]
        atr = df['atr'].iloc[-1]
        e34 = df['ema34'].iloc[-1]
        e55 = df['ema55'].iloc[-1]
        e200 = df['ema200'].iloc[-1]
        
        signal = None
        reason = ""
        now_ts = datetime.now().timestamp()

        if (c > e34) and (e34 > e55) and (e55 > e200) and (48 < rsi < 78) and ((c - p) > (atr * 0.25)):
            if await get_setting('long_enabled'):
                signal, reason = "LONG", "Strong BULL Trend"

        elif (c < e34) and (e34 < e55) and (e55 < e200) and (22 < rsi < 52) and ((p - c) > (atr * 0.25)):
            if await get_setting('short_enabled'):
                signal, reason = "SHORT", "Strong BEAR Trend"

        if signal:
            full_sig_key = f"{signal}_{symbol}_{tf}"
            if now_ts - LAST_SIGNALS.get(full_sig_key, 0) > COOLDOWNS.get(tf, 3600):
                LAST_SIGNALS[full_sig_key] = now_ts
                rp = get_rounded_price(c)
                await send_to_webhook({"symbol": symbol.replace("/", ""), "signal": signal, "timeframe": tf, "price": rp, "reason": reason})
                icon = "🟢" if "LONG" in signal else "🔴"
                await send_tg(f"{icon} <b>{signal}</b> | {symbol} [{tf}]\nPrice: <code>{rp}</code>")

        await update_stat('total_scans')
    except Exception as e:
        await update_stat('errors')

async def scanner_worker():
    ex = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
    while True:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT symbol FROM coin_settings WHERE enabled=1") as cur:
                    active = [r[0] for r in await cur.fetchall()]
            if active:
                for i in range(0, len(active), 5):
                    await asyncio.gather(*[check_pair(ex, s) for s in active[i:i+5]])
                    await asyncio.sleep(0.5)
            await asyncio.sleep(15)
        except: await asyncio.sleep(10)

# ========================= WEB APP (UI 3.2.1) =========================

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(scanner_worker())
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def login_page():
    return '<html><body style="background:#000;color:#0f0;font-family:monospace;text-align:center;padding-top:15%"><h1>OZ ULTRA PRO</h1><form action="/login" method="post"><input type="password" name="password" style="font-size:20px;padding:10px;background:#111;color:#0f0;border:1px solid #0f0"><br><br><button type="submit" style="padding:10px 30px;background:#0f0;color:#000;border:none;cursor:pointer;font-weight:bold">ВХОД</button></form></body></html>'

@app.post("/login")
async def login(password: str = Form(...)):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key='password'") as cur:
            row = await cur.fetchone()
            if row and password == row[0]: return RedirectResponse("/panel", status_code=303)
    return "ОШИБКА"

@app.get("/panel", response_class=HTMLResponse)
async def admin_panel():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT key, value FROM settings") as cur:
            sets = {k: v for k, v in await cur.fetchall()}
        async with db.execute("SELECT key, value FROM stats") as cur:
            stats = {k: v for k, v in await cur.fetchall()}
        async with db.execute("SELECT symbol, enabled, tf FROM coin_settings ORDER BY symbol ASC") as cur:
            coins = await cur.fetchall()

    def get_btn(key, label):
        active = sets.get(key) == '1'
        color = "#0f0" if active else "#555"
        bg = "rgba(0,255,0,0.1)" if active else "transparent"
        return f'<a href="/toggle_s/{key}" style="text-decoration:none;color:{color};border:1px solid {color};padding:10px 20px;border-radius:5px;background:{bg};font-weight:bold">{label}</a>'

    # ТАБЛИЦА МОНЕТ (UI 3.2.1 Style)
    rows = ""
    for s, en, tf in coins:
        safe_s = s.replace("/", "---")
        btn = f'<a href="/toggle_c/{safe_s}" style="color:{"#0f0" if en else "#f00"};text-decoration:none"><b>[{"ON" if en else "OFF"}]</b></a>'
        tf_links = "".join([f'<a href="/set_tf/{safe_s}/{t}" style="color:{"#0f0" if tf==t else "#555"};text-decoration:none;margin-right:8px">{t}</a>' for t in ['1m','5m','15m','30m','1h','4h']])
        rows += f'<tr style="border-bottom:1px solid #222"><td style="padding:10px">{s}</td><td style="padding:10px">{btn}</td><td style="padding:10px">{tf_links}</td></tr>'

    return f'''
    <html>
    <head><title>OZ v3.2.1</title><meta name="viewport" content="width=device-width, initial-scale=1"></head>
    <body style="background:#000;color:#0f0;font-family:monospace;padding:20px">
        <div style="max-width:800px;margin:auto">
            <h2 style="color:#0f0;border-bottom:1px solid #0f0;padding-bottom:10px">OZ SCANNER PRO v3.2.1</h2>
            <div style="display:flex;gap:10px;margin-bottom:20px">
                {get_btn('long_enabled', 'LONG')} {get_btn('short_enabled', 'SHORT')} {get_btn('close_enabled', 'CLOSE')}
                <a href="/panel" style="text-decoration:none;color:#fff;border:1px solid #fff;padding:10px 20px;border-radius:5px">REFRESH</a>
            </div>
            <p style="color:#888">SCANS: {stats.get('total_scans')} | SIGNALS: {stats.get('signals_sent')} | ERRORS: {stats.get('errors')}</p>
            <table style="width:100%;border-collapse:collapse;background:#0a0a0a">
                <tr style="background:#111;text-align:left"><th style="padding:10px">COIN</th><th style="padding:10px">STATUS</th><th style="padding:10px">TIMEFRAMES</th></tr>
                {rows}
            </table>
        </div>
    </body>
    </html>
    '''

@app.get("/toggle_s/{key}")
async def toggle_setting(key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE settings SET value = CASE WHEN value='1' THEN '0' ELSE '1' END WHERE key=?", (key,))
        await db.commit()
    return RedirectResponse("/panel")

@app.get("/toggle_c/{symbol}")
async def toggle_c(symbol: str):
    real_symbol = symbol.replace("---", "/")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE coin_settings SET enabled = 1 - enabled WHERE symbol=?", (real_symbol,))
        await db.commit()
    return RedirectResponse("/panel")

@app.get("/set_tf/{symbol}/{tf}")
async def set_tf(symbol: str, tf: str):
    real_symbol = symbol.replace("---", "/")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE coin_settings SET tf = ? WHERE symbol=?", (tf, real_symbol))
        await db.commit()
    return RedirectResponse("/panel")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
