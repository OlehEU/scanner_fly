"""
=============================================================================
🚀 OZ SCANNER ULTRA PRO v3.5.0 | FULL PERSISTENCE & EXECUTION
=============================================================================
ВЕРСИЯ: 3.5.0 (Fly.io Optimized)
ИЗМЕНЕНИЯ:
- Добавлен CLOSE_SHORT (пересечение EMA55 снизу вверх).
- Внедрена таблица статистики (stats) в БД.
- Улучшена система Webhook с проверкой доставки.
- Оптимизирован цикл сканирования (Chunking 10 монет/сек).
- Сохранение всех настроек в SQLite.
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
from fastapi import FastAPI, Form, Request, HTTPException
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
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT", "DOGE/USDT", "DOT/USDT", "LINK/USDT",
    "SUI/USDT", "AVAX/USDT", "NEAR/USDT", "TIA/USDT", "SEI/USDT", "INJ/USDT", "OP/USDT", "ARB/USDT", "APT/USDT",
    "RUNE/USDT", "STX/USDT", "ORDI/USDT", "WORLD/USDT", "IMX/USDT", "FET/USDT", "LDO/USDT", "FIL/USDT",
    "1000PEPE/USDT", "WIF/USDT", "BONK/USDT", "FLOKI/USDT", "1000SHIB/USDT", "1000SATS/USDT", "POPCAT/USDT",
    "MEME/USDT", "BOME/USDT", "MYRO/USDT", "TURBO/USDT", "FARTCOIN/USDT",
    "GALA/USDT", "MANA/USDT", "SAND/USDT", "AAVE/USDT", "UNI/USDT", "DYDX/USDT", "CRV/USDT", "MKR/USDT",
    "LTC/USDT", "BCH/USDT", "ETC/USDT", "TRX/USDT", "TON/USDT", "KAS/USDT"
]

ALL_TFS = ['1m', '5m', '15m', '1h', '4h']
DB_PATH = "/data/oz_ultra_v3.db" if os.path.exists("/data") else "oz_ultra_v3.db"

COOLDOWNS = {'1m': 240, '5m': 480, '15m': 720, '1h': 3600, '4h': 10800}
LAST_SIGNAL = {}

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
        # Инициализация настроек
        for key in ['long_enabled', 'short_enabled', 'close_long_enabled', 'close_short_enabled']:
            await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,'1')", (key,))
        
        # Инициализация статистики
        for s_key in ['total_scans', 'signals_sent', 'errors']:
            await db.execute("INSERT OR IGNORE INTO stats (key,value) VALUES (?, 0)", (s_key,))

        # Инициализация монет
        for s in ALL_SYMBOLS:
            await db.execute("INSERT OR IGNORE INTO coin_settings (symbol, tf, enabled) VALUES (?, '1h', 0)", (s,))
        await db.commit()
    logger.info("💾 База данных готова.")

async def get_setting(key: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] == '1' if row else False

async def update_stat(key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE stats SET value = value + 1 WHERE key=?", (key,))
        await db.commit()

# ========================= УВЕДОМЛЕНИЯ И WEBHOOK =========================
async def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    async with aiohttp.ClientSession() as session:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            await session.post(url, json={"chat_id": int(TELEGRAM_CHAT_ID), "text": text, "parse_mode": "HTML"})
        except Exception as e: logger.error(f"TG Error: {e}")

async def broadcast_signal(symbol, tf, direction, price, reason):
    ts = int(datetime.now().timestamp() * 1000)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO signals (symbol,tf,direction,price,reason,ts) VALUES (?,?,?,?,?,?)",
                         (symbol, tf, direction, price, reason, ts))
        await db.commit()
    
    await update_stat('signals_sent')
    
    icons = {"LONG": "🟢", "SHORT": "🔴", "CLOSE_LONG": "🔵", "CLOSE_SHORT": "🟠"}
    msg = f"{icons.get(direction, '💡')} <b>{direction}</b>\n💰 {symbol} | ⏰ {tf}\nЦена: <code>{price}</code>\n🔥 {reason}"
    await send_telegram(msg)

    # WEBHOOK ЭКЗЕКЬЮТОРУ
    payload = {"symbol": symbol.replace('/', ''), "signal": direction, "timeframe": tf, "price": price, "secret": WEBHOOK_SECRET}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(WEBHOOK_URL, json=payload, timeout=10) as r:
                if r.status == 200:
                    logger.info(f"✅ Webhook {symbol} {direction} доставлен")
                else:
                    logger.error(f"⚠️ Webhook error {r.status} для {symbol}")
        except Exception as e:
            logger.error(f"❌ Webhook fail: {e}")

# ========================= ЯДРО СКАНЕРА =========================
async def check_pair(exchange, symbol):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT enabled, tf FROM coin_settings WHERE symbol=?", (symbol,)) as cur:
            config = await cur.fetchone()
    
    if not config or not config[0]: return 
    tf = config[1]
    
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=tf, limit=100)
        df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
        
        df['ema34'] = talib.EMA(df['close'], 34)
        df['ema55'] = talib.EMA(df['close'], 55)
        df['ema200'] = talib.EMA(df['close'], 200)
        df['rsi'] = talib.RSI(df['close'], 14)
        
        if len(df) < 60: return
        
        c, rsi = df['close'].iloc[-1], df['rsi'].iloc[-1]
        e34, e55, e200 = df['ema34'].iloc[-1], df['ema55'].iloc[-1], df['ema200'].iloc[-1]
        
        key = f"{symbol}_{tf}"
        now = datetime.now().timestamp()
        cd = COOLDOWNS.get(tf, 300)

        # LONG
        if await get_setting('long_enabled') and c > e34 > e55 > e200 and rsi > 52:
            if now - LAST_SIGNAL.get(f"L_{key}", 0) > cd:
                LAST_SIGNAL[f"L_{key}"] = now
                await broadcast_signal(symbol, tf, "LONG", c, "Trend Bullish (EMA 34/55/200) + RSI > 52")

        # SHORT
        if await get_setting('short_enabled') and c < e34 < e55 < e200 and rsi < 48:
            if now - LAST_SIGNAL.get(f"S_{key}", 0) > cd:
                LAST_SIGNAL[f"S_{key}"] = now
                await broadcast_signal(symbol, tf, "SHORT", c, "Trend Bearish (EMA 34/55/200) + RSI < 48")

        # CLOSE LONG
        if await get_setting('close_long_enabled') and c < e55:
            if now - LAST_SIGNAL.get(f"CL_{key}", 0) > cd:
                LAST_SIGNAL[f"CL_{key}"] = now
                await broadcast_signal(symbol, tf, "CLOSE_LONG", c, "Exit: Price below EMA55")

        # CLOSE SHORT (ИСПРАВЛЕНО)
        if await get_setting('close_short_enabled') and c > e55:
            if now - LAST_SIGNAL.get(f"CS_{key}", 0) > cd:
                LAST_SIGNAL[f"CS_{key}"] = now
                await broadcast_signal(symbol, tf, "CLOSE_SHORT", c, "Exit: Price above EMA55")

        await update_stat('total_scans')
    except Exception as e:
        await update_stat('errors')
        logger.error(f"Error {symbol}: {e}")

async def scanner_worker():
    ex = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
    await send_telegram("🚀 <b>OZ SCANNER v3.5.0 запущен</b>\nВсе системы активны.")
    
    while True:
        try:
            tasks = [check_pair(ex, s) for s in ALL_SYMBOLS]
            # Пачки по 10 для Fly.io (экономия ресурсов)
            for i in range(0, len(tasks), 10):
                await asyncio.gather(*tasks[i:i+10])
                await asyncio.sleep(0.5)
            await asyncio.sleep(15)
        except Exception as e:
            logger.error(f"Worker Error: {e}")
            await asyncio.sleep(10)

# ========================= WEB APP =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(scanner_worker())
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def admin_panel():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT key, value FROM settings") as cur:
            stg = {k: v for k, v in await cur.fetchall()}
        async with db.execute("SELECT key, value FROM stats") as cur:
            stats = {k: v for k, v in await cur.fetchall()}
        async with db.execute("SELECT symbol, enabled, tf FROM coin_settings ORDER BY symbol ASC") as cur:
            coins = await cur.fetchall()

    def btn(k): return "background:#2ecc71" if stg.get(k) == '1' else "background:#e74c3c"

    # Рендеринг кнопок управления
    header = "".join([f'<a href="/toggle_g/{k}" style="{btn(k)};color:#fff;padding:8px;margin:2px;text-decoration:none;border-radius:4px">{k}</a>' for k in stg.keys()])
    
    # Сетка монет
    grid = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:10px;margin-top:20px">'
    for s, en, tf in coins:
        clr = "#2ecc71" if en else "#444"
        grid += f'''<div style="background:#111;padding:10px;border:1px solid {clr};border-radius:8px">
            <div style="display:flex;justify-content:space-between"><b>{s}</b> <a href="/toggle_c/{s.replace("/","_")}" style="color:{clr}">{"[ON]" if en else "[OFF]"}</a></div>
            <div style="font-size:10px;margin-top:5px">{" ".join([f'<a href="/set_tf/{s.replace("/","_")}/{t}" style="color:{"#0f0" if t==tf else "#666"}">{t}</a>' for t in ALL_TFS])}</div>
        </div>'''
    grid += '</div>'

    return f"""
    <body style="background:#000;color:#eee;font-family:sans-serif;padding:20px">
        <h1>OZ ULTRA v3.5.0</h1>
        <div style="background:#222;padding:15px;border-radius:8px;margin-bottom:20px">
            <b>STATS:</b> Scans: {stats.get('total_scans')} | Signals: {stats.get('signals_sent')} | Errors: {stats.get('errors')}
            <br><br>{header}
        </div>
        <a href="/history" style="color:#0f0">VIEW SIGNAL HISTORY</a>
        {grid}
    </body>
    """

@app.get("/toggle_g/{key}")
async def toggle_g(key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE settings SET value = CASE WHEN value='1' THEN '0' ELSE '1' END WHERE key=?", (key,))
        await db.commit()
    return RedirectResponse("/")

@app.get("/toggle_c/{symbol}")
async def toggle_c(symbol: str):
    s = symbol.replace("_", "/")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE coin_settings SET enabled = 1 - enabled WHERE symbol=?", (s,))
        await db.commit()
    return RedirectResponse("/")

@app.get("/set_tf/{symbol}/{tf}")
async def set_tf(symbol: str, tf: str):
    s = symbol.replace("_", "/")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE coin_settings SET tf = ? WHERE symbol=?", (tf, s))
        await db.commit()
    return RedirectResponse("/")

@app.get("/history", response_class=HTMLResponse)
async def history():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT symbol, direction, price, tf, datetime(ts/1000, 'unixepoch', 'localtime') FROM signals ORDER BY ts DESC LIMIT 50") as cur:
            rows = await cur.fetchall()
    
    tr = "".join([f"<tr><td>{r[4]}</td><td>{r[0]}</td><td style='color:{'#0f0' if 'LONG' in r[1] else '#f00'}'>{r[1]}</td><td>{r[3]}</td><td>{r[2]}</td></tr>" for r in rows])
    return f"""<body style="background:#000;color:#eee;padding:20px"><table border="1" style="width:100%;border-collapse:collapse">
    <tr><th>Time</th><th>Symbol</th><th>Signal</th><th>TF</th><th>Price</th></tr>{tr}</table><br><a href="/" style="color:#0f0">BACK</a></body>"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
