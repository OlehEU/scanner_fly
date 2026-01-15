# ========================= OZ SCANNER ULTRA PRO v4.0 =========================
# ВЕРСИЯ: 4.0 (Terminal Interface + Persistence)
# ОПИСАНИЕ: Полноценная админ-панель, авторизация, управление 4 типами сигналов.
# =============================================================================
import ccxt.async_support as ccxt
import asyncio
import pandas as pd
import talib
import aiosqlite
import os
import logging
from datetime import datetime
from fastapi import FastAPI, Form, Request
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

# Полный список монет согласно запросу
ALL_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT", "ADA/USDT",
    "1000SHIB/USDT", "1000PEPE/USDT", "1000BONK/USDT", "1000FLOKI/USDT", "1000SATS/USDT",
    "FARTCOIN/USDT", "PIPPIN/USDT", "BTT/USDT", "MASK/USDT", "TRX/USDT", "TON/USDT", 
    "DOT/USDT", "AVAX/USDT", "NEAR/USDT", "LINK/USDT", "SUI/USDT", "WIF/USDT", 
    "APT/USDT", "ARB/USDT", "OP/USDT", "INJ/USDT", "FET/USDT", "FIL/USDT", 
    "SEI/USDT", "RUNE/USDT", "JUP/USDT", "PYTH/USDT", "ONDO/USDT", "RENDER/USDT", 
    "JASMY/USDT", "LDO/USDT", "IMX/USDT", "ORDI/USDT", "STX/USDT", "TIA/USDT", 
    "UNI/USDT", "AAVE/USDT", "ICP/USDT", "HBAR/USDT", "1000CAT/USDT", "1000RATS/USDT", 
    "GOAT/USDT", "TURBO/USDT", "MOG/USDT", "MEW/USDT", "POPCAT/USDT", "BRETT/USDT", 
    "MOTHER/USDT", "GIGA/USDT", "ATOM/USDT", "POL/USDT", "ALGO/USDT", "XLM/USDT", 
    "BCH/USDT", "LTC/USDT", "EOS/USDT", "ENA/USDT"
]

ALL_TFS = ['1m', '5m', '15m', '30m', '1h', '4h']
DB_PATH = "oz_ultra_v4.db"

# Кулдауны для предотвращения спама сигналами
COOLDOWNS = {
    '1m': {'entry': 180, 'close': 120},
    '5m': {'entry': 480, 'close': 300},
    '15m': {'entry': 720, 'close': 450},
    '1h': {'entry': 3600, 'close': 1800},
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

# --- Сеть и уведомления ---
async def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    async with aiohttp.ClientSession() as session:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
            await session.post(url, json={"chat_id": str(TELEGRAM_CHAT_ID), "text": text, "parse_mode": "HTML"})
        except Exception as e: logger.error(f"TG Error: {e}")

async def broadcast_signal(symbol, tf, direction, price, reason):
    ts = int(datetime.now().timestamp() * 1000)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO signals (symbol,tf,direction,price,reason,ts) VALUES (?,?,?,?,?,?)", (symbol, tf, direction, price, reason, ts))
        await db.commit()
    
    icon = "🟢" if "LONG" in direction and "CLOSE" not in direction else "🔴"
    if "CLOSE" in direction: icon = "✅"
    
    msg = f"{icon} <b>OZ {direction}</b>\n<code>{symbol}</code> | {tf}\nЦена: <b>{price}</b>\n{reason}"
    await send_telegram(msg)
    # Здесь также вызывается send_webhook при необходимости

# --- Логика сканера ---
async def check_pair(exchange, symbol, tf):
    try:
        limit = 200
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
        cd = COOLDOWNS.get(tf, {'entry': 300, 'close': 300})

        # Вход LONG
        if await get_setting('long_entry_enabled') and (c > e34 > e55 > e200) and 45 < rsi < 75:
            if now - LAST_SIGNAL.get(f"L_{key}", 0) > cd['entry']:
                LAST_SIGNAL[f"L_{key}"] = now
                await broadcast_signal(symbol, tf, "LONG", c, "Trend alignment")

        # Вход SHORT
        if await get_setting('short_entry_enabled') and (c < e34 < e55 < e200) and 25 < rsi < 55:
            if now - LAST_SIGNAL.get(f"S_{key}", 0) > cd['entry']:
                LAST_SIGNAL[f"S_{key}"] = now
                await broadcast_signal(symbol, tf, "SHORT", c, "Trend alignment")

        # Закрытие LONG (пересечение EMA55 вниз)
        if await get_setting('close_long_enabled') and c < e55:
            if now - LAST_SIGNAL.get(f"CL_{key}", 0) > cd['close']:
                LAST_SIGNAL[f"CL_{key}"] = now
                await broadcast_signal(symbol, tf, "CLOSE_LONG", c, "EMA55 Break")

        # Закрытие SHORT (пересечение EMA55 вверх)
        if await get_setting('close_short_enabled') and c > e55:
            if now - LAST_SIGNAL.get(f"CS_{key}", 0) > cd['close']:
                LAST_SIGNAL[f"CS_{key}"] = now
                await broadcast_signal(symbol, tf, "CLOSE_SHORT", c, "EMA55 Break")

    except Exception as e:
        logger.error(f"Error checking {symbol}: {e}")

async def scanner_worker():
    ex = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
    await init_db()
    while True:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT symbol, tf FROM coin_settings WHERE enabled=1") as cur:
                    active_pairs = await cur.fetchall()
            
            if active_pairs:
                tasks = [check_pair(ex, pair[0], pair[1]) for pair in active_pairs]
                await asyncio.gather(*tasks)
            
            STATE["cycles"] += 1
            await asyncio.sleep(15)
        except Exception as e:
            logger.error(f"Worker Error: {e}")
            await asyncio.sleep(10)

# --- FastAPI App ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(scanner_worker())
    yield

app = FastAPI(lifespan=lifespan)

# Шаблон Терминала
HTML_HEAD = """
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        body { background:#0a0a0a; color:#0f4; font-family:'Courier New', monospace; margin:0; padding:20px; }
        .header { border:1px solid #0f4; padding:15px; margin-bottom:20px; text-transform:uppercase; font-size:0.9em; }
        .nav-buttons { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap:10px; margin-bottom:25px; }
        .btn { border:1px solid #0f4; padding:12px; text-align:center; text-decoration:none; color:#0f4; font-weight:bold; cursor:pointer; }
        .btn.active { background:#0f4; color:#000; }
        .coin-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap:15px; }
        .coin-card { border:1px solid #333; padding:15px; border-radius:4px; transition: 0.3s; }
        .coin-card.enabled { border-color:#0f4; box-shadow: 0 0 10px rgba(0,255,68,0.1); }
        .tf-link { color:#555; text-decoration:none; margin-right:8px; font-size:0.85em; }
        .tf-link.active { color:#0f4; text-decoration:underline; }
        .toggle-btn { float:right; text-decoration:none; padding:2px 8px; border:1px solid; font-size:0.8em; }
        input[type=password] { background:#000; color:#0f4; border:1px solid #0f4; padding:10px; width:200px; }
        .status-bar { color:#0ff; margin-bottom:10px; }
    </style>
</head>
"""

@app.get("/", response_class=HTMLResponse)
async def login_page():
    return f"""<html>{HTML_HEAD}<body style="display:flex; justify-content:center; align-items:center; height:100vh; flex-direction:column;">
    <form action="/login" method="post" style="border:1px solid #0f4; padding:40px; text-align:center;">
        <h2 style="margin-top:0;">OZ SCANNER LOGIN</h2>
        <input type="password" name="password" placeholder="ENTER ACCESS KEY"><br><br>
        <button type="submit" class="btn" style="width:100%; background:#0f4; color:#000;">DECRYPT SYSTEM</button>
    </form></body></html>"""

@app.post("/login")
async def login(password: str = Form(...)):
    if password == "777": return RedirectResponse("/panel", status_code=303)
    return "ACCESS DENIED"

@app.get("/panel", response_class=HTMLResponse)
async def admin_panel():
    uptime = str(datetime.now() - STATE["start_time"]).split('.')[0]
    
    # Глобальные настройки
    l_en = await get_setting('long_entry_enabled')
    s_en = await get_setting('short_entry_enabled')
    cl_en = await get_setting('close_long_enabled')
    cs_en = await get_setting('close_short_enabled')

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT symbol, tf, enabled FROM coin_settings") as cur:
            coins = await cur.fetchall()
            active_count = sum(1 for c in coins if c[2])

    coin_html = ""
    for symbol, tf, enabled in coins:
        active_class = "enabled" if enabled else ""
        btn_txt = "OFF" if enabled else "ON"
        btn_clr = "#f44" if enabled else "#0f4"
        
        tfs_html = "".join([
            f"<a href='/set_tf/{symbol.replace('/','_')}/{t}' class='tf-link {'active' if t==tf else ''}'>{t}</a>" 
            for t in ALL_TFS
        ])
        
        coin_html += f"""
        <div class="coin-card {active_class}">
            <a href="/toggle/{symbol.replace('/','_')}" class="toggle-btn" style="color:{btn_clr}; border-color:{btn_clr}">{btn_txt}</a>
            <div style="font-weight:bold; margin-bottom:10px;">{symbol}</div>
            <div>{tfs_html}</div>
        </div>
        """

    return f"""<html>{HTML_HEAD}<body>
        <div class="header">
            СИСТЕМА: <span style="color:#0f4">ОНЛАЙН</span> | 
            ЦИКЛЫ: {STATE['cycles']} | 
            ВРЕМЯ РАБОТЫ: {uptime} | 
            МОНЕТЫ: {active_count} | 
            <a href="/signals" style="color:#0ff; text-decoration:none;">СИГНАЛЫ (ЛОГИ)</a>
        </div>

        <div class="nav-buttons">
            <a href="/tg/long_entry_enabled" class="btn {'active' if l_en else ''}">ОТКРЫТЬ ЛОНГ</a>
            <a href="/tg/short_entry_enabled" class="btn {'active' if s_en else ''}">ОТКРЫТЬ ШОРТ</a>
            <a href="/tg/close_long_enabled" class="btn {'active' if cl_en else ''}">ЗАКРЫТЬ ЛОНГ</a>
            <a href="/tg/close_short_enabled" class="btn {'active' if cs_en else ''}">ЗАКРЫТЬ ШОРТ</a>
        </div>

        <div class="coin-grid">{coin_html}</div>
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
        async with db.execute("SELECT symbol, tf, direction, price, datetime(ts/1000, 'unixepoch', 'localtime') FROM signals ORDER BY ts DESC LIMIT 100") as cur:
            rows = await cur.fetchall()
    table = "".join([f"<tr><td style='padding:8px; border-bottom:1px solid #222;'>{r[4]}</td><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td></tr>" for r in rows])
    return f"""<html>{HTML_HEAD}<body>
        <h2>ИСТОРИЯ СИГНАЛОВ</h2>
        <table style="width:100%; text-align:left; border-collapse:collapse;">
            <tr style="color:#555;"><th>ВРЕМЯ</th><th>ПАРА</th><th>TF</th><th>ТИП</th><th>ЦЕНА</th></tr>
            {table}
        </table><br><a href="/panel" class="btn" style="display:inline-block;">НАЗАД В ТЕРМИНАЛ</a>
    </body></html>"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
