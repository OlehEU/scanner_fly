# ========================= OZ SCANNER ULTRA PRO (4x) =========================
# ВЕРСИЯ: 3.5 (Visual & Diagnostic Edition)
# ОПИСАНИЕ: Улучшенный UI, диагностика Heartbeat и оптимизация для 1m ТФ.
# =============================================================================
import ccxt.async_support as ccxt
import asyncio
import pandas as pd
import talib
import aiosqlite
import os
import logging
from datetime import datetime
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import aiohttp
from contextlib import asynccontextmanager

# Настройки логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("OZ_SCANNER")

# Конфигурация из окружения
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://bot-fly-oz.fly.dev/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

ALL_SYMBOLS = [
    "DOGE/USDT", "1000SHIB/USDT", "1000PEPE/USDT", "1000BONK/USDT", 
    "1000FLOKI/USDT", "1000SATS/USDT", "FARTCOIN/USDT", "PIPPIN/USDT", 
    "BTT/USDT", "MASK/USDT", "ETH/USDT", "BNB/USDT", "SOL/USDT", 
    "ADA/USDT", "TRX/USDT", "MATIC/USDT", "DOT/USDT", "ATOM/USDT", 
    "LINK/USDT", "AVAX/USDT", "NEAR/USDT", "XRP/USDT" 
]

ALL_TFS = ['1m', '5m', '15m', '30m', '1h', '4h']
DB_PATH = "oz_ultra_v3.db"

COOLDOWNS = {
    '1m': {'long': 180, 'close': 120, 'short': 180, 'close_short': 120},
    '5m': {'long': 480, 'close': 300, 'short': 480, 'close_short': 300},
    '15m': {'long': 720, 'close': 450, 'short': 720, 'close_short': 450},
    '30m': {'long': 1200, 'close': 600, 'short': 1200, 'close_short': 600},
    '1h': {'long': 3600, 'close': 1800, 'short': 3600, 'close_short': 1800},
}

# Глобальные переменные состояния
LAST_SIGNAL = {}
STATE = {"cycles": 0, "last_heartbeat": 0, "start_time": datetime.now()}

# --- UTILS ---
def get_rounded_price(price: float) -> float:
    if price < 0.05: return round(price, 8)
    elif price < 1.0: return round(price, 6)
    else: return round(price, 3)

# --- DATABASE ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, tf TEXT, direction TEXT, price REAL, reason TEXT, ts INTEGER);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS coin_settings (symbol TEXT PRIMARY KEY, tf TEXT DEFAULT '1h', enabled INTEGER DEFAULT 0);
        ''')
        defaults = [('password','777'), ('long_entry_enabled','0'), ('short_entry_enabled','0'), ('close_long_enabled','0'), ('close_short_enabled','0')]
        for k, v in defaults:
            await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (k, v))
        for s in ALL_SYMBOLS:
            await db.execute("INSERT OR IGNORE INTO coin_settings (symbol, tf, enabled) VALUES (?, '1h', 0)", (s,))
        await db.commit()

async def get_setting(key: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] == '1' if row else False

async def is_coin_enabled(symbol: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT enabled FROM coin_settings WHERE symbol=?", (symbol,)) as cur:
            row = await cur.fetchone()
            return bool(row[0]) if row else False

async def get_tf_for_coin(symbol: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT tf FROM coin_settings WHERE symbol=?", (symbol,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else "1h"

# --- NETWORKING ---
async def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    async with aiohttp.ClientSession() as session:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            await session.post(url, json={"chat_id": str(TELEGRAM_CHAT_ID), "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
        except Exception as e: logger.error(f"Telegram error: {e}")

async def send_webhook(symbol, tf, direction, price, reason):
    if not WEBHOOK_SECRET: return
    p = get_rounded_price(price)
    payload = {"symbol": symbol.replace('/', ''), "signal": direction, "timeframe": tf, "price": p, "reason": reason, "source": "OZ_ULTRA_3.5"}
    async with aiohttp.ClientSession(headers={"X-Webhook-Secret": WEBHOOK_SECRET}) as session:
        try:
            async with session.post(WEBHOOK_URL, json=payload, timeout=10) as resp:
                if resp.status != 200: logger.error(f"Webhook {resp.status}")
        except Exception as e: logger.error(f"Webhook fail: {e}")

async def broadcast_signal(symbol, tf, direction, price, reason):
    ts = int(datetime.now().timestamp() * 1000)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO signals (symbol,tf,direction,price,reason,ts) VALUES (?,?,?,?,?,?)", (symbol, tf, direction, price, reason, ts))
        await db.commit()
    
    icon = "🟢" if "LONG" in direction else "🔴"
    if "CLOSE" in direction: icon = "⚪"
    msg = f"🚀 <b>OZ {direction}</b>\n<code>{symbol}</code> | {tf}\nЦена: <b>{price}</b>\n{reason}"
    await send_telegram(msg)
    await send_webhook(symbol, tf, direction, price, reason)

# --- ENGINE ---
async def check_pair(exchange, symbol, tf):
    try:
        limit = 100 if tf == '1m' else 300
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
        df['ema34'] = talib.EMA(df['close'], 34)
        df['ema55'] = talib.EMA(df['close'], 55)
        df['ema200'] = talib.EMA(df['close'], 200)
        df['rsi'] = talib.RSI(df['close'], 14)
        df['atr'] = talib.ATR(df['high'], df['low'], df['close'], 14)
        
        last = df.iloc[-1]
        c, rsi = last['close'], last['rsi']
        e34, e55, e200 = last['ema34'], last['ema55'], last['ema200']
        atr = last['atr'] or 0.0001
        
        if pd.isna(e200): return

        # Логика тренда
        bullish = c > e34 > e55 > e200
        bearish = c < e34 < e55 < e200
        
        key = f"{symbol}_{tf}"
        now = datetime.now().timestamp()
        cd = COOLDOWNS.get(tf, {'long': 300, 'close': 300, 'short': 300, 'close_short': 300})

        # LONG
        if await get_setting('long_entry_enabled') and bullish and 45 < rsi < 75:
            if now - LAST_SIGNAL.get(f"L_{key}", 0) > cd['long']:
                LAST_SIGNAL[f"L_{key}"] = now
                await broadcast_signal(symbol, tf, "LONG", c, "Trend & RSI OK")

        # SHORT
        if await get_setting('short_entry_enabled') and bearish and 25 < rsi < 55:
            if now - LAST_SIGNAL.get(f"S_{key}", 0) > cd['short']:
                LAST_SIGNAL[f"S_{key}"] = now
                await broadcast_signal(symbol, tf, "SHORT", c, "Trend & RSI OK")

    except Exception as e:
        logger.error(f"Engine error {symbol}: {e}")

async def scanner_worker():
    ex = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
    await send_telegram("🛡 <b>OZ Scanner v3.5</b> активирован.\nСистема мониторинга запущена.")
    
    while True:
        try:
            active = [s for s in ALL_SYMBOLS if await is_coin_enabled(s)]
            
            # Heartbeat (Диагностика)
            now = datetime.now().timestamp()
            if now - STATE["last_heartbeat"] > 600:
                await send_telegram(f"🛰 <b>Heartbeat</b>\nЦиклов: {STATE['cycles']}\nАктивных пар: {len(active)}")
                STATE["last_heartbeat"] = now

            if active:
                tasks = [check_pair(ex, s, await get_tf_for_coin(s)) for s in active]
                await asyncio.gather(*tasks)
            
            STATE["cycles"] += 1
            await asyncio.sleep(20) # Пауза между сканами
        except Exception as e:
            logger.error(f"Worker crash: {e}")
            await asyncio.sleep(10)

# --- WEB UI ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(scanner_worker())
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def login_page():
    return """
    <html>
    <head><title>OZ LOGIN</title><style>
        body { background: #0a0a0a; color: #00ff41; font-family: 'Courier New', monospace; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }
        .box { border: 1px solid #00ff41; padding: 40px; border-radius: 8px; box-shadow: 0 0 20px rgba(0,255,65,0.2); text-align: center; }
        input { background: #000; border: 1px solid #00ff41; color: #00ff41; padding: 10px; font-size: 18px; outline: none; }
        button { background: #00ff41; color: #000; border: none; padding: 10px 30px; margin-top: 20px; cursor: pointer; font-weight: bold; }
    </style></head>
    <body>
        <div class="box">
            <h1>ACCESS PROTOCOL</h1>
            <form action="/login" method="post">
                <input type="password" name="password" placeholder="ENTER SECRET KEY" required><br>
                <button type="submit">DECRYPT</button>
            </form>
        </div>
    </body>
    </html>
    """

@app.post("/login")
async def login(password: str = Form(...)):
    if password == "777": return RedirectResponse("/panel", status_code=303)
    return "ACCESS DENIED"

@app.get("/panel", response_class=HTMLResponse)
async def admin_panel():
    # Собираем данные для панели
    active_count = 0
    coin_rows = ""
    for s in ALL_SYMBOLS:
        enabled = await is_coin_enabled(s)
        tf = await get_tf_for_coin(s)
        if enabled: active_count += 1
        
        status_color = "#00ff41" if enabled else "#555"
        btn_text = "OFF" if enabled else "ON"
        
        tf_btns = "".join([f"<a href='/set_tf/{s.replace('/','_')}/{t}' style='color:{('#00ff41' if t==tf else '#555')}'>[{t}]</a> " for t in ALL_TFS])
        
        coin_rows += f"""
        <div style="display:flex; justify-content:space-between; border-bottom:1px solid #222; padding:10px 0;">
            <span style="color:{status_color}; width:150px;">{s}</span>
            <span style="width:250px;">{tf_btns}</span>
            <a href="/toggle/{s.replace('/','_')}" style="color:#00ff41; text-decoration:none;">[{btn_text}]</a>
        </div>
        """

    long_en = await get_setting('long_entry_enabled')
    short_en = await get_setting('short_entry_enabled')

    return f"""
    <html>
    <head><title>OZ PANEL</title>
    <style>
        body {{ background: #050505; color: #00ff41; font-family: monospace; padding: 20px; }}
        .header {{ border: 1px solid #00ff41; padding: 15px; margin-bottom: 20px; display: flex; justify-content: space-between; }}
        .stat {{ color: #0ff; }}
        .btn-global {{ padding: 10px; border: 1px solid #00ff41; text-decoration: none; color: #00ff41; margin-right: 10px; }}
        .btn-active {{ background: #00ff41; color: #000; }}
    </style>
    </head>
    <body>
        <div class="header">
            <div>SYSTEM: ONLINE | CYCLES: <span class="stat">{STATE['cycles']}</span></div>
            <div>ACTIVE PAIRS: <span class="stat">{active_count}</span></div>
            <div>UPTIME: <span class="stat">{(datetime.now() - STATE['start_time'])}</span></div>
        </div>

        <div style="margin-bottom: 30px;">
            <a href="/toggle_g/long_entry_enabled" class="btn-global {'btn-active' if long_en else ''}">LONG SIGNALS: {'ON' if long_en else 'OFF'}</a>
            <a href="/toggle_g/short_entry_enabled" class="btn-global {'btn-active' if short_en else ''}">SHORT SIGNALS: {'ON' if short_en else 'OFF'}</a>
            <a href="/signals" class="btn-global">VIEW LOGS</a>
        </div>

        <div style="max-width: 800px; margin: auto;">
            {coin_rows}
        </div>
    </body>
    </html>
    """

@app.get("/toggle/{symbol}")
async def toggle_coin(symbol: str):
    s = symbol.replace("_", "/")
    cur = await is_coin_enabled(s)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE coin_settings SET enabled=? WHERE symbol=?", (0 if cur else 1, s))
        await db.commit()
    return RedirectResponse("/panel")

@app.get("/set_tf/{symbol}/{tf}")
async def set_tf(symbol: str, tf: str):
    s = symbol.replace("_", "/")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE coin_settings SET tf=? WHERE symbol=?", (tf, s))
        await db.commit()
    return RedirectResponse("/panel")

@app.get("/toggle_g/{key}")
async def toggle_global(key: str):
    cur = await get_setting(key)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE settings SET value=? WHERE key=?", ('0' if cur else '1', key))
        await db.commit()
    return RedirectResponse("/panel")

@app.get("/signals", response_class=HTMLResponse)
async def view_signals():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT symbol, tf, direction, price, datetime(ts/1000, 'unixepoch', 'localtime') FROM signals ORDER BY ts DESC LIMIT 50") as cur:
            rows = await cur.fetchall()
    
    table = "".join([f"<tr><td>{r[4]}</td><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td></tr>" for r in rows])
    return f"<html><body style='background:#000; color:#0f0;'><table border=1>{table}</table><br><a href='/panel' style='color:#0f0'>BACK</a></body></html>"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
