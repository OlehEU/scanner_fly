# ========================= OZ SCANNER ULTRA PRO (4x) =========================
# ВЕРСИЯ: 3.5.2 (Full Control Edition)
# ОБНОВЛЕНИЕ: Актуализирован список монет (68 торговых пар) + Статистика в UI.
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
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("OZ_SCANNER")

# Конфигурация
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://bot-fly-oz.fly.dev/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

# Обновленный список монет
ALL_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "ADA/USDT",
    "1000SHIB/USDT", "1000PEPE/USDT", "1000BONK/USDT", "1000FLOKI/USDT", "1000SATS/USDT",
    "FARTCOIN/USDT", "PIPPIN/USDT", "BTT/USDT", "MASK/USDT",
    "TRX/USDT", "TON/USDT", "DOT/USDT", "AVAX/USDT", "NEAR/USDT", "LINK/USDT",
    "SUI/USDT", "WIF/USDT", "APT/USDT", "ARB/USDT", "OP/USDT", "INJ/USDT",
    "FET/USDT", "FIL/USDT", "SEI/USDT", "RUNE/USDT", "JUP/USDT", "PYTH/USDT",
    "ONDO/USDT", "RENDER/USDT", "JASMY/USDT", "LDO/USDT", "IMX/USDT", "ORDI/USDT",
    "STX/USDT", "TIA/USDT", "UNI/USDT", "AAVE/USDT", "ICP/USDT", "HBAR/USDT",
    "1000CAT/USDT", "1000RAT/USDT", "GOAT/USDT", "TURBO/USDT", "MOG/USDT",
    "MEW/USDT", "POPCAT/USDT", "BRETT/USDT", "MOTHER/USDT", "GIGA/USDT",
    "ATOM/USDT", "POL/USDT", "ALGO/USDT", "XLM/USDT", "BCH/USDT", "LTC/USDT", "EOS/USDT", "ENA/USDT",
    "MATIC/USDT"
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

STATE = {"cycles": 0, "last_heartbeat": 0, "start_time": datetime.now()}
LAST_SIGNAL = {}

# --- Вспомогательные функции ---
def get_rounded_price(price: float) -> float:
    if price < 0.05: return round(price, 8)
    elif price < 1.0: return round(price, 6)
    else: return round(price, 3)

# --- База данных ---
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS signals (id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT, tf TEXT, direction TEXT, price REAL, reason TEXT, ts INTEGER);
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS coin_settings (symbol TEXT PRIMARY KEY, tf TEXT DEFAULT '1h', enabled INTEGER DEFAULT 0);
        ''')
        defaults = [
            ('password','777'), 
            ('long_entry_enabled','0'), 
            ('short_entry_enabled','0'), 
            ('close_long_enabled','0'), 
            ('close_short_enabled','0')
        ]
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

# --- Сеть и уведомления ---
async def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    async with aiohttp.ClientSession() as session:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            await session.post(url, json={"chat_id": str(TELEGRAM_CHAT_ID), "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
        except Exception as e: logger.error(f"Telegram fail: {e}")

async def send_webhook(symbol, tf, direction, price, reason):
    if not WEBHOOK_SECRET: return
    p = get_rounded_price(price)
    payload = {"symbol": symbol.replace('/', ''), "signal": direction, "timeframe": tf, "price": p, "reason": reason, "source": "OZ_ULTRA_3.5.1"}
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
    
    icon = "🟢" if "LONG" in direction and "CLOSE" not in direction else "🔴"
    if "CLOSE" in direction: icon = "✅"
    
    msg = f"🚀 <b>OZ {direction}</b>\n<code>{symbol}</code> | {tf}\nЦена: <b>{price}</b>\n{reason}"
    await send_telegram(msg)
    await send_webhook(symbol, tf, direction, price, reason)

# --- Логика сканера ---
async def check_pair(exchange, symbol, tf):
    try:
        limit = 100 if tf == '1m' else 300
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
        df['ema34'] = talib.EMA(df['close'], 34)
        df['ema55'] = talib.EMA(df['close'], 55)
        df['ema200'] = talib.EMA(df['close'], 200)
        df['rsi'] = talib.RSI(df['close'], 14)
        
        last = df.iloc[-1]
        c, rsi = last['close'], last['rsi']
        e34, e55, e200 = last['ema34'], last['ema55'], last['ema200']
        
        if pd.isna(e200): return

        key = f"{symbol}_{tf}"
        now = datetime.now().timestamp()
        cd = COOLDOWNS.get(tf, {'long': 300, 'close': 300, 'short': 300, 'close_short': 300})

        # --- Сигналы ---
        if await get_setting('long_entry_enabled') and (c > e34 > e55 > e200) and 45 < rsi < 75:
            if now - LAST_SIGNAL.get(f"L_{key}", 0) > cd['long']:
                LAST_SIGNAL[f"L_{key}"] = now
                await broadcast_signal(symbol, tf, "LONG", c, "Strong Trend")

        if await get_setting('short_entry_enabled') and (c < e34 < e55 < e200) and 25 < rsi < 55:
            if now - LAST_SIGNAL.get(f"S_{key}", 0) > cd['short']:
                LAST_SIGNAL[f"S_{key}"] = now
                await broadcast_signal(symbol, tf, "SHORT", c, "Strong Trend")

        if await get_setting('close_long_enabled') and (c < e55) and (now - LAST_SIGNAL.get(f"L_{key}", 0) < 86400):
             if now - LAST_SIGNAL.get(f"CL_{key}", 0) > cd['close']:
                LAST_SIGNAL[f"CL_{key}"] = now
                await broadcast_signal(symbol, tf, "CLOSE_LONG", c, "Trend break EMA55")

        if await get_setting('close_short_enabled') and (c > e55) and (now - LAST_SIGNAL.get(f"S_{key}", 0) < 86400):
             if now - LAST_SIGNAL.get(f"CS_{key}", 0) > cd['close_short']:
                LAST_SIGNAL[f"CS_{key}"] = now
                await broadcast_signal(symbol, tf, "CLOSE_SHORT", c, "Trend break EMA55")

    except Exception as e:
        logger.error(f"Engine error {symbol}: {e}")

async def scanner_worker():
    ex = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
    await send_telegram("🛡 <b>OZ Scanner v3.5.1</b> активирован.")
    
    while True:
        try:
            active = []
            for s in ALL_SYMBOLS:
                if await is_coin_enabled(s): active.append(s)
            
            now = datetime.now().timestamp()
            if now - STATE["last_heartbeat"] > 900:
                await send_telegram(f"🛰 <b>Статус Сканера</b>\nЦиклов: {STATE['cycles']}\nАктивных пар: {len(active)}")
                STATE["last_heartbeat"] = now

            if active:
                async with aiosqlite.connect(DB_PATH) as db:
                    tasks = []
                    for s in active:
                        async with db.execute("SELECT tf FROM coin_settings WHERE symbol=?", (s,)) as cur:
                            row = await cur.fetchone()
                            tf = row[0] if row else "1h"
                            tasks.append(check_pair(ex, s, tf))
                    await asyncio.gather(*tasks)
            
            STATE["cycles"] += 1
            await asyncio.sleep(20)
        except Exception as e:
            logger.error(f"Worker crash: {e}")
            await asyncio.sleep(10)

# --- Web UI ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(scanner_worker())
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def login_page():
    return """<html><body style="background:#0a0a0a;color:#0f4;font-family:monospace;display:flex;justify-content:center;align-items:center;height:100vh;">
    <form action="/login" method="post" style="border:1px solid #0f4;padding:40px;text-align:center;">
    <h2>OZ PROTOCOL</h2><input type="password" name="password" style="background:#000;color:#0f4;border:1px solid #0f4;padding:10px;"><br>
    <button type="submit" style="margin-top:20px;background:#0f4;color:#000;padding:10px 20px;border:none;cursor:pointer;">DECRYPT</button>
    </form></body></html>"""

@app.post("/login")
async def login(password: str = Form(...)):
    if password == "777": return RedirectResponse("/panel", status_code=303)
    return "ACCESS DENIED"

@app.get("/panel", response_class=HTMLResponse)
async def admin_panel():
    coin_rows = ""
    active_count = 0
    async with aiosqlite.connect(DB_PATH) as db:
        for s in ALL_SYMBOLS:
            async with db.execute("SELECT enabled, tf FROM coin_settings WHERE symbol=?", (s,)) as cur:
                row = await cur.fetchone()
                enabled, tf = (row[0], row[1]) if row else (0, "1h")
                if enabled: active_count += 1
                status_color = "#0f4" if enabled else "#555"
                btn_text = "OFF" if enabled else "ON"
                tf_btns = "".join([f"<a href='/set_tf/{s.replace('/','_')}/{t}' style='color:{('#0f4' if t==tf else '#555')};text-decoration:none;'> [{t}] </a>" for t in ALL_TFS])
                coin_rows += f"""<div style="display:flex;justify-content:space-between;padding:8px;border-bottom:1px solid #222;">
                <span style="color:{status_color};width:120px;">{s}</span><span>{tf_btns}</span>
                <a href="/toggle/{s.replace('/','_')}" style="color:#0f4;">[{btn_text}]</a></div>"""

    l_en = await get_setting('long_entry_enabled')
    s_en = await get_setting('short_entry_enabled')
    cl_en = await get_setting('close_long_enabled')
    cs_en = await get_setting('close_short_enabled')

    def get_btn_style(active): return "background:#0f4;color:#000;" if active else "border:1px solid #0f4;color:#0f4;"

    return f"""<html><head><style>.btn {{ padding:10px;text-decoration:none;margin:5px;display:inline-block;font-weight:bold; }}</style></head>
    <body style="background:#050505;color:#0f4;font-family:monospace;padding:20px;">
        <div style="border:1px solid #0f4;padding:15px;margin-bottom:20px; display: flex; justify-content: space-between;">
            <span>SYSTEM: <b>ONLINE</b> | CYCLES: {STATE['cycles']} | UPTIME: {str(datetime.now()-STATE['start_time']).split('.')[0]}</span>
            <span style="color: #0ff;">ACTIVE COINS: <b>{active_count} / {len(ALL_SYMBOLS)}</b></span>
        </div>
        <div style="margin-bottom:25px; text-align: center;">
            <a href="/tg/long_entry_enabled" class="btn" style="{get_btn_style(l_en)}">ENTRY LONG</a>
            <a href="/tg/short_entry_enabled" class="btn" style="{get_btn_style(s_en)}">ENTRY SHORT</a>
            <a href="/tg/close_long_enabled" class="btn" style="{get_btn_style(cl_en)}">CLOSE LONG</a>
            <a href="/tg/close_short_enabled" class="btn" style="{get_btn_style(cs_en)}">CLOSE SHORT</a>
            <a href="/signals" class="btn" style="border:1px solid #0ff;color:#0ff;">LOGS</a>
        </div>
        <div style="max-width:800px;margin:auto; border: 1px solid #333; padding: 10px;">{coin_rows}</div>
    </body></html>"""

@app.get("/tg/{key}")
async def toggle_global(key: str):
    cur = await get_setting(key)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE settings SET value=? WHERE key=?", ('0' if cur else '1', key))
        await db.commit()
    return RedirectResponse("/panel")

@app.get("/toggle/{symbol}")
async def toggle_coin(symbol: str):
    s = symbol.replace("_", "/")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT enabled FROM coin_settings WHERE symbol=?", (s,)) as cur:
            row = await cur.fetchone()
            new_val = 0 if row and row[0] else 1
            await db.execute("UPDATE coin_settings SET enabled=? WHERE symbol=?", (new_val, s))
            await db.commit()
    return RedirectResponse("/panel")

@app.get("/set_tf/{symbol}/{tf}")
async def set_tf(symbol: str, tf: str):
    s = symbol.replace("_", "/")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE coin_settings SET tf=? WHERE symbol=?", (tf, s))
        await db.commit()
    return RedirectResponse("/panel")

@app.get("/signals", response_class=HTMLResponse)
async def view_signals():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT symbol, tf, direction, price, datetime(ts/1000, 'unixepoch', 'localtime') FROM signals ORDER BY ts DESC LIMIT 50") as cur:
            rows = await cur.fetchall()
    table = "".join([f"<tr><td style='padding:5px;'>{r[4]}</td><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td></tr>" for r in rows])
    return f"<html><body style='background:#000;color:#0f4;padding:20px;'><table border=1 style='width:100%;text-align:center; border-collapse: collapse;'>{table}</table><br><a href='/panel' style='color:#0f4; text-decoration: none;'>[ BACK TO PANEL ]</a></body></html>"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
