# ========================= OZ SCANNER ULTRA PRO (4x) =========================
# ВЕРСИЯ: 3.2.2 (Extended Assets Edition)
# ОПИСАНИЕ: Асинхронный сканер + Расширенный список монет + SQLite + TA-Lib
# =============================================================================
import ccxt.async_support as ccxt
import asyncio
import pandas as pd
import talib
import aiosqlite
import os
import logging
import json
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
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

# РАСШИРЕННЫЙ СПИСОК МОНЕТ (Топ ликвидности + Волатильность)
ALL_SYMBOLS = [
    # Major
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT", "DOGE/USDT", "DOT/USDT", "LINK/USDT",
    # High Volatility / Trending
    "SUI/USDT", "AVAX/USDT", "NEAR/USDT", "TIA/USDT", "SEI/USDT", "INJ/USDT", "OP/USDT", "ARB/USDT", "APT/USDT",
    "RUNE/USDT", "STX/USDT", "ORDI/USDT", "WORLD/USDT", "IMX/USDT", "FET/USDT", "LDO/USDT", "FIL/USDT",
    # Memes & Community
    "1000PEPE/USDT", "WIF/USDT", "BONK/USDT", "FLOKI/USDT", "1000SHIB/USDT", "1000SATS/USDT", "POPCAT/USDT",
    "MEME/USDT", "BOME/USDT", "MYRO/USDT", "TURBO/USDT", "FARTCOIN/USDT",
    # Ecosystems & L1/L2
    "GALA/USDT", "MANA/USDT", "SAND/USDT", "AAVE/USDT", "UNI/USDT", "DYDX/USDT", "CRV/USDT", "MKR/USDT",
    "LTC/USDT", "BCH/USDT", "ETC/USDT", "TRX/USDT", "TON/USDT", "KAS/USDT"
]

ALL_TFS = ['1m', '5m', '15m', '1h', '4h']
DB_PATH = "oz_ultra_v3.db"

# Кулдауны для сигналов (в секундах)
COOLDOWNS = {
    '1m': 240, '5m': 480, '15m': 720, '1h': 3600, '4h': 10800
}

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
            CREATE TABLE IF NOT EXISTS coin_settings (
                symbol TEXT PRIMARY KEY, tf TEXT DEFAULT '1h', enabled INTEGER DEFAULT 0
            );
        ''')
        # Глобальные тумблеры
        for key in ['long_enabled', 'short_enabled', 'close_long_enabled', 'close_short_enabled']:
            await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,'0')", (key,))
        # Настройки монет (добавляем новые монеты, если их нет в БД)
        for s in ALL_SYMBOLS:
            await db.execute("INSERT OR IGNORE INTO coin_settings (symbol, tf, enabled) VALUES (?, '1h', 0)", (s,))
        await db.commit()
    logger.info(f"💾 База данных инициализирована. Всего монет в списке: {len(ALL_SYMBOLS)}")

async def get_setting(key: str) -> bool:
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
                row = await cur.fetchone()
                return row[0] == '1' if row else False
    except: return False

async def toggle_setting(key: str):
    curr = await get_setting(key)
    new_val = '0' if curr else '1'
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, new_val))
        await db.commit()

async def get_coin_config(symbol: str):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT enabled, tf FROM coin_settings WHERE symbol=?", (symbol,)) as cur:
            return await cur.fetchone()

# ========================= ЛОГИКА ОКРУГЛЕНИЯ =========================
def get_rounded_price(price: float) -> float:
    if price < 0.001: return round(price, 8)
    elif price < 0.1: return round(price, 6)
    elif price < 1.0: return round(price, 4)
    else: return round(price, 2)

# ========================= УВЕДОМЛЕНИЯ =========================
async def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    async with aiohttp.ClientSession() as session:
        url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
        try:
            await session.post(url, json={"chat_id": int(TELEGRAM_CHAT_ID), "text": text, "parse_mode": "HTML"})
        except Exception as e: logger.error(f"TG Error: {e}")

async def broadcast_signal(symbol, tf, direction, price, reason):
    rounded_p = get_rounded_price(price)
    ts = int(datetime.now().timestamp() * 1000)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO signals (symbol,tf,direction,price,reason,ts) VALUES (?,?,?,?,?,?)",
                         (symbol, tf, direction, rounded_p, reason, ts))
        await db.commit()

    icons = {"LONG": "🟢", "SHORT": "🔴", "CLOSE_LONG": "🔵", "CLOSE_SHORT": "🟠"}
    msg = f"{icons.get(direction, '💡')} <b>{direction}</b>\n💰 {symbol} | ⏰ {tf}\nЦена: <code>{rounded_p}</code>\n🔥 {reason}"
    await send_telegram(msg)

    if WEBHOOK_SECRET:
        headers = {"X-Webhook-Secret": WEBHOOK_SECRET}
        payload = {"symbol": symbol.replace('/', ''), "signal": direction, "timeframe": tf, "price": rounded_p}
        async with aiohttp.ClientSession(headers=headers) as session:
            try:
                async with session.post(WEBHOOK_URL, json=payload, timeout=5) as r:
                    logger.info(f"Webhook {symbol} {direction} Status: {r.status}")
            except Exception as e: logger.error(f"Webhook fail: {e}")

# ========================= ЯДРО СКАНЕРА =========================
async def check_pair(exchange, symbol):
    config = await get_coin_config(symbol)
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
        
        c = df['close'].iloc[-1]
        rsi = df['rsi'].iloc[-1]
        ema34, ema55, ema200 = df['ema34'].iloc[-1], df['ema55'].iloc[-1], df['ema200'].iloc[-1]
        
        key = f"{symbol}_{tf}"
        now = datetime.now().timestamp()
        cd = COOLDOWNS.get(tf, 300)

        # LONG
        if await get_setting('long_enabled') and c > ema34 > ema55 > ema200 and rsi > 50:
            if now - LAST_SIGNAL.get(f"L_{key}", 0) > cd:
                LAST_SIGNAL[f"L_{key}"] = now
                await broadcast_signal(symbol, tf, "LONG", c, "Trend Bullish (EMA 34/55/200) + RSI > 50")

        # SHORT
        if await get_setting('short_enabled') and c < ema34 < ema55 < ema200 and rsi < 50:
            if now - LAST_SIGNAL.get(f"S_{key}", 0) > cd:
                LAST_SIGNAL[f"S_{key}"] = now
                await broadcast_signal(symbol, tf, "SHORT", c, "Trend Bearish (EMA 34/55/200) + RSI < 50")

        # CLOSE LONG (фильтр по EMA55)
        if await get_setting('close_long_enabled') and c < ema55:
            if now - LAST_SIGNAL.get(f"CL_{key}", 0) > cd:
                LAST_SIGNAL[f"CL_{key}"] = now
                await broadcast_signal(symbol, tf, "CLOSE_LONG", c, "Price crossed below EMA55")

    except Exception as e:
        logger.error(f"Error checking {symbol}: {e}")

async def scanner_worker():
    ex = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
    await send_telegram(f"🚀 <b>OZ SCANNER v3.2.2</b> запущен.\nВ списке: {len(ALL_SYMBOLS)} монет.\nРежим: Fly.io (SQLite)")
    
    while True:
        try:
            # Разделяем монеты на чанки, чтобы не "спамить" API одновременно
            active_tasks = [check_pair(ex, s) for s in ALL_SYMBOLS]
            # Выполняем проверки группами по 10
            for i in range(0, len(active_tasks), 10):
                chunk = active_tasks[i:i+10]
                await asyncio.gather(*chunk)
                await asyncio.sleep(1) # Пауза между группами запросов
            
            await asyncio.sleep(20) # Общий цикл проверки
        except Exception as e:
            logger.error(f"Worker Loop Error: {e}")
            await asyncio.sleep(10)

# ========================= WEB APP =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(scanner_worker())
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def login_page():
    return """
    <body style="background:#050505; color:#0f0; font-family:monospace; display:flex; justify-content:center; align-items:center; height:100vh; margin:0">
        <form action="/auth" method="post" style="border:1px solid #0f0; padding:30px; border-radius:10px; box-shadow: 0 0 20px rgba(0,255,0,0.2)">
            <h2 style="text-align:center">OZ ULTRA LOGIN</h2>
            <input type="password" name="pw" placeholder="Enter PIN" style="background:#000; color:#0f0; border:1px solid #0f0; padding:10px; width:200px; display:block; margin:20px auto; text-align:center">
            <button type="submit" style="background:#0f0; color:#000; border:none; padding:10px 20px; width:100%; cursor:pointer; font-weight:bold">ACCESS SYSTEM</button>
        </form>
    </body>
    """

@app.post("/auth")
async def auth(pw: str = Form(...)):
    if pw == "777": return RedirectResponse("/admin", status_code=303)
    return "ACCESS DENIED"

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT key, value FROM settings")
        settings = {k: v for k, v in await cur.fetchall()}
        cur_coins = await db.execute("SELECT symbol, enabled, tf FROM coin_settings ORDER BY symbol ASC")
        coins = await cur_coins.fetchall()

    def get_btn_color(key):
        return "#2ecc71" if settings.get(key) == '1' else "#e74c3c"

    header_btns = ""
    for k in ['long_enabled', 'short_enabled', 'close_long_enabled', 'close_short_enabled']:
        color = get_btn_color(k)
        header_btns += f'<a href="/toggle_g/{k}" style="background:{color}; color:white; padding:10px 15px; text-decoration:none; border-radius:5px; margin:5px; display:inline-block; font-size:12px; font-weight:bold">{k.upper()}</a>'

    grid = '<div style="display:grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap:12px; margin-top:20px">'
    for s, en, tf in coins:
        s_safe = s.replace("/", "_")
        card_bg = "#111" if en else "#050505"
        border_color = "#2ecc71" if en else "#333"
        grid += f'''
        <div style="background:{card_bg}; border:1px solid {border_color}; padding:12px; border-radius:8px; transition: 0.3s">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px">
                <span style="font-weight:bold; color:{"#fff" if en else "#666"}">{s}</span>
                <a href="/toggle_c/{s_safe}" style="text-decoration:none; font-size:11px; background:{"#2ecc71" if en else "#333"}; color:#fff; padding:2px 6px; border-radius:3px">{ "ON" if en else "OFF" }</a>
            </div>
            <div style="display:flex; gap:4px; flex-wrap:wrap">
        '''
        for t in ALL_TFS:
            active = "background:#0f0; color:#000" if t == tf else "background:#222; color:#777"
            grid += f'<a href="/set_tf/{s_safe}/{t}" style="text-decoration:none; padding:2px 5px; font-size:10px; border-radius:2px; {active}">{t}</a>'
        grid += '</div></div>'
    grid += '</div>'

    return f"""
    <body style="background:#000; color:#ddd; font-family:Segoe UI, sans-serif; padding:20px; margin:0">
        <div style="max-width:1200px; margin:0 auto">
            <div style="display:flex; justify-content:space-between; align-items:center">
                <h1 style="color:#0f0; margin:0">OZ ULTRA v3.2.2</h1>
                <div>
                    <a href="/history" style="color:#0f0; text-decoration:none; margin-right:20px">HISTORY</a>
                    <a href="/" style="color:#666; text-decoration:none">LOGOUT</a>
                </div>
            </div>
            <div style="margin-top:20px; background:#111; padding:15px; border-radius:10px">{header_btns}</div>
            {grid}
        </div>
    </body>
    """

@app.get("/toggle_g/{key}", strict_slashes=False)
async def toggle_global(key: str):
    await toggle_setting(key)
    return RedirectResponse("/admin", status_code=303)

@app.get("/toggle_c/{symbol}", strict_slashes=False)
async def toggle_coin(symbol: str):
    real_sym = symbol.replace("_", "/")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE coin_settings SET enabled = 1 - enabled WHERE symbol = ?", (real_sym,))
        await db.commit()
    return RedirectResponse("/admin", status_code=303)

@app.get("/set_tf/{symbol}/{tf}", strict_slashes=False)
async def set_tf(symbol: str, tf: str):
    real_sym = symbol.replace("_", "/")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE coin_settings SET tf = ? WHERE symbol = ?", (tf, real_sym))
        await db.commit()
    return RedirectResponse("/admin", status_code=303)

@app.get("/history", response_class=HTMLResponse)
async def history():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT symbol, direction, price, tf, datetime(ts/1000, 'unixepoch', 'localtime') FROM signals ORDER BY ts DESC LIMIT 100")
        rows = await cur.fetchall()
    
    table_rows = ""
    for r in rows:
        color = "#2ecc71" if "LONG" in r[1] else "#e74c3c"
        table_rows += f"<tr style='border-bottom:1px solid #222'><td style='padding:10px'>{r[4]}</td><td>{r[0]}</td><td style='color:{color}'>{r[1]}</td><td>{r[3]}</td><td>{r[2]}</td></tr>"
    
    return f"""
    <body style="background:#000; color:#ddd; font-family:monospace; padding:20px">
        <h2>Signal History (Last 100)</h2>
        <table style="width:100%; text-align:left; border-collapse:collapse">
            <thead style="background:#111; color:#0f0">
                <tr><th style="padding:10px">Time</th><th>Coin</th><th>Signal</th><th>TF</th><th>Price</th></tr>
            </thead>
            <tbody>{table_rows}</tbody>
        </table>
        <br><a href="/admin" style="color:#0f0; text-decoration:none">← BACK TO ADMIN</a>
    </body>
    """

if __name__ == "__main__":
    import uvicorn
    # Порт 8080 чаще используется на Fly.io по умолчанию
    uvicorn.run(app, host="0.0.0.0", port=8000)
