"""
=============================================================================
🚀 OZ SCANNER ULTRA PRO v3.5.2 | UI RESTORED & PANDAS FIXED
=============================================================================
- Исправлено: Ошибка "The truth value of a Series is ambiguous" (iloc[-1] fix).
- Исправлено: Округление цен для 1000PEPE, 1000SHIB и др.
- Добавлено: Сохранение настроек в БД и расширенная статистика.
- UI: Возвращен классический дизайн с кнопками управления режимами.
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

ALL_TFS = ['1m', '5m', '15m', '30m', '1h', '4h']
DB_PATH = "oz_ultra_v3.db"

# Кулдауны (в секундах)
COOLDOWNS = {
    '1m': 240, '5m': 480, '15m': 720, '30m': 1200, '1h': 3600, '4h': 10800
}
LAST_SIGNALS = {} 

# ========================= СЛУЖЕБНЫЕ ФУНКЦИИ =========================

def get_rounded_price(price: float) -> float:
    """Динамическое округление для соответствия точности Binance"""
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
                if resp.status == 200:
                    logger.info(f"✅ Webhook success: {payload['symbol']}")
                    await update_stat('signals_sent')
                else:
                    logger.error(f"❌ Webhook {resp.status}")
        except Exception:
            logger.error("❌ Webhook failed")

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
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, tf TEXT, direction TEXT, price REAL, reason TEXT, ts INTEGER
            );
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
        
        # Индикаторы
        df['ema34'] = talib.EMA(df['close'], 34)
        df['ema55'] = talib.EMA(df['close'], 55)
        df['ema200'] = talib.EMA(df['close'], 200)
        df['rsi'] = talib.RSI(df['close'], 14)
        df['atr'] = talib.ATR(df['high'], df['low'], df['close'], 14)
        
        # Берем последние значения (.iloc[-1] fix)
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

        # 1. ЛОГИКА LONG
        trend_bull = (c > e34) and (e34 > e55) and (e55 > e200)
        if trend_bull and 48 < rsi < 78 and (c - p) > (atr * 0.25):
            if await get_setting('long_enabled'):
                signal = "LONG"
                reason = "Strong BULL Trend + RSI"

        # 2. ЛОГИКА SHORT
        trend_bear = (c < e34) and (e34 < e55) and (e55 < e200)
        if trend_bear and 22 < rsi < 52 and (p - c) > (atr * 0.25):
            if await get_setting('short_enabled'):
                signal = "SHORT"
                reason = "Strong BEAR Trend + RSI"

        # 3. ЛОГИКА ЗАКРЫТИЯ
        if await get_setting('close_enabled'):
            if (c < e55 and rsi > 70): # Пример закрытия лонга
                signal = "CLOSE_LONG"
                reason = "Long SL/TP (EMA55 Break)"
            elif (c > e55 and rsi < 30): # Пример закрытия шорта
                signal = "CLOSE_SHORT"
                reason = "Short SL/TP (EMA55 Break)"

        # ПРОВЕРКА КУЛДАУНА И ОТПРАВКА
        if signal:
            full_sig_key = f"{signal}_{symbol}_{tf}"
            cd = COOLDOWNS.get(tf, 3600)
            
            if now_ts - LAST_SIGNALS.get(full_sig_key, 0) > cd:
                LAST_SIGNALS[full_sig_key] = now_ts
                rounded_p = get_rounded_price(c)
                
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "INSERT INTO signals (symbol, tf, direction, price, reason, ts) VALUES (?,?,?,?,?,?)",
                        (symbol, tf, signal, rounded_p, reason, int(now_ts))
                    )
                    await db.commit()
                
                await send_to_webhook({
                    "symbol": symbol.replace("/", ""),
                    "signal": signal, "timeframe": tf, "price": rounded_p, "reason": reason
                })
                
                icon = "🟢" if "LONG" in signal else "🔴" if "SHORT" in signal else "⚪"
                await send_tg(f"{icon} <b>{signal}</b> | {symbol} [{tf}]\nЦена: <code>{rounded_p}</code>\n{reason}")

        await update_stat('total_scans')

    except Exception as e:
        await update_stat('errors')
        logger.error(f"Error {symbol}: {e}")

async def scanner_worker():
    ex = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
    while True:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT symbol FROM coin_settings WHERE enabled=1") as cur:
                    active_pairs = [r[0] for r in await cur.fetchall()]
            
            if active_pairs:
                for i in range(0, len(active_pairs), 5):
                    batch = active_pairs[i:i+5]
                    await asyncio.gather(*[check_pair(ex, s) for s in batch])
                    await asyncio.sleep(0.5)
            
            await asyncio.sleep(15)
        except Exception as e:
            logger.error(f"Worker Loop Error: {e}")
            await asyncio.sleep(10)

# ========================= WEB APP =========================

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(scanner_worker())
    await send_tg("🚀 <b>OZ SCANNER v3.5.2</b> Запущен.")
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
            settings = {k: v for k, v in await cur.fetchall()}
        async with db.execute("SELECT key, value FROM stats") as cur:
            stats = {k: v for k, v in await cur.fetchall()}
        async with db.execute("SELECT symbol, enabled, tf FROM coin_settings ORDER BY symbol ASC") as cur:
            coins = await cur.fetchall()

    def btn_style(val):
        return "background:#0f0;color:#000" if val == '1' else "background:#333;color:#888"

    # Глобальные кнопки управления
    controls = f'''
    <div style="display:flex;gap:10px;margin-bottom:20px;flex-wrap:wrap">
        <a href="/toggle_s/long_enabled" style="text-decoration:none;padding:15px 25px;border-radius:5px;font-weight:bold;{btn_style(settings.get('long_enabled'))}">LONG SIGNALS</a>
        <a href="/toggle_s/short_enabled" style="text-decoration:none;padding:15px 25px;border-radius:5px;font-weight:bold;{btn_style(settings.get('short_enabled'))}">SHORT SIGNALS</a>
        <a href="/toggle_s/close_enabled" style="text-decoration:none;padding:15px 25px;border-radius:5px;font-weight:bold;{btn_style(settings.get('close_enabled'))}">CLOSE ALERTS</a>
        <a href="/panel" style="text-decoration:none;padding:15px 25px;border-radius:5px;font-weight:bold;background:#555;color:#fff">REFRESH</a>
    </div>
    '''

    # Сетка монет
    grid = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px">'
    for s, en, tf in coins:
        safe_s = s.replace("/", "---")
        status_color = "#0f0" if en else "#555"
        grid += f'''
        <div style="background:#111;padding:15px;border:1px solid #333;border-radius:8px;text-align:center">
            <b style="font-size:1.1em;color:#fff">{s}</b><br>
            <span style="color:#888">{tf}</span><br><br>
            <a href="/toggle_c/{safe_s}" style="text-decoration:none;color:{status_color};border:1px solid {status_color};padding:5px 15px;border-radius:3px;font-size:0.8em">
                {"ENABLED" if en else "DISABLED"}
            </a>
        </div>'''
    grid += '</div>'

    html = f'''
    <html>
    <head><title>OZ ULTRA PANEL</title><meta name="viewport" content="width=device-width, initial-scale=1"></head>
    <body style="background:#050505;color:#0f0;font-family:sans-serif;padding:20px;margin:0">
        <div style="max-width:1200px;margin:auto">
            <h1 style="color:#0f0;margin-bottom:5px">OZ ULTRA PRO <small style="color:#555;font-size:0.4em">v3.5.2</small></h1>
            <p style="background:#111;padding:10px;border-radius:5px;border-left:4px solid #0f0">
                🚀 SCANS: <b>{stats.get('total_scans', 0)}</b> | 
                📡 SIGNALS: <b>{stats.get('signals_sent', 0)}</b> | 
                ⚠️ ERRORS: <span style="color:red">{stats.get('errors', 0)}</span>
            </p>
            {controls}
            <h3 style="border-bottom:1px solid #222;padding-bottom:10px;color:#888">COIN MONITORING</h3>
            {grid}
        </div>
    </body>
    </html>
    '''
    return html

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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
