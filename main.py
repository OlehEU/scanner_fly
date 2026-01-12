# ========================= OZ SCANNER ULTRA PRO (4x) =========================
# ВЕРСИЯ: 3.2.1 (Stable + Fixes)
# ОПИСАНИЕ: Асинхронный фьючерсный сканер. Компактная админка.
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

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OZ_ULTRA")

# ========================= КОНФИГУРАЦИЯ =========================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://bot-fly-oz.fly.dev/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

ALL_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "SUI/USDT", "AVAX/USDT",
    "NEAR/USDT", "LINK/USDT", "TIA/USDT", "1000PEPE/USDT", "WIF/USDT",
    "DOGE/USDT", "XRP/USDT", "BNB/USDT", "DOT/USDT", "ADA/USDT",
    "1000SHIB/USDT", "BCH/USDT", "OP/USDT", "ARB/USDT", "RENDER/USDT",
    "1000BONK/USDT", "1000FLOKI/USDT", "1000SATS/USDT", "FARTCOIN/USDT"
]

ALL_TFS = ['1m', '5m', '15m', '30m', '1h', '4h']
DB_PATH = "oz_ultra_v3.db"

# Кулдауны (сек)
COOLDOWNS = {
    '1m': {'long': 240, 'close': 180, 'short': 240, 'close_short': 180},
    '5m': {'long': 480, 'close': 300, 'short': 480, 'close_short': 300},
    '15m': {'long': 720, 'close': 450, 'short': 720, 'close_short': 450}, 
    '30m': {'long': 1200, 'close': 600, 'short': 1200, 'close_short': 600},
    '1h': {'long': 3600, 'close': 1800, 'short': 3600, 'close_short': 1800},
    '4h': {'long': 10800, 'close': 5400, 'short': 10800, 'close_short': 5400},
}

LAST_SIGNAL = {}

# ========================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ =========================
def get_rounded_price(price: float) -> float:
    """Динамическое округление для соответствия шагу цены биржи."""
    if price < 0.001: return round(price, 8)
    elif price < 0.1: return round(price, 6)
    elif price < 1.0: return round(price, 5)
    elif price < 100: return round(price, 3)
    else: return round(price, 2)

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
        # Инициализация глобальных настроек
        for key in ['long_entry_enabled', 'short_entry_enabled', 'close_long_enabled', 'close_short_enabled']:
            await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,'0')", (key,))
        # Инициализация настроек для каждой монеты
        for s in ALL_SYMBOLS:
            await db.execute("INSERT OR IGNORE INTO coin_settings (symbol, tf, enabled) VALUES (?, '1h', 0)", (s,))
        await db.commit()

async def get_setting(key: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            return row[0] == '1' if row else False

async def toggle_setting(key: str):
    curr = await get_setting(key)
    new_val = '0' if curr else '1'
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, new_val))
        await db.commit()

async def is_coin_enabled(symbol: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT enabled FROM coin_settings WHERE symbol=?", (symbol,)) as cur:
            row = await cur.fetchone()
            return bool(row[0]) if row else False

async def update_coin(symbol: str, enabled: int = None, tf: str = None):
    async with aiosqlite.connect(DB_PATH) as db:
        if enabled is not None:
            await db.execute("UPDATE coin_settings SET enabled=? WHERE symbol=?", (enabled, symbol))
        if tf is not None:
            await db.execute("UPDATE coin_settings SET tf=? WHERE symbol=?", (tf, symbol))
        await db.commit()

async def get_tf_for_coin(symbol: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT tf FROM coin_settings WHERE symbol=?", (symbol,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else "1h"

# ========================= СЕТЕВЫЕ ОПЕРАЦИИ =========================
async def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                                   json={"chat_id": int(TELEGRAM_CHAT_ID), "text": text, "parse_mode": "HTML"}) as r:
                if r.status != 200:
                    logger.error(f"TG Error Status: {r.status}")
        except Exception as e: logger.error(f"TG Fail: {e}")

async def send_to_webhook(symbol, tf, direction, price, reason):
    if not WEBHOOK_SECRET: return
    payload = {
        "symbol": symbol.replace('/', ''),
        "signal": direction.upper(),
        "timeframe": tf,
        "price": get_rounded_price(price),
        "reason": reason,
        "source": "OZ SCANNER ULTRA 3.2.1"
    }
    headers = {"X-Webhook-Secret": WEBHOOK_SECRET}
    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.post(WEBHOOK_URL, json=payload, timeout=10) as r:
                logger.info(f"Webhook {symbol} {direction} Status: {r.status}")
        except Exception as e: logger.error(f"Webhook error: {e}")

async def broadcast_signal(symbol, tf, direction, price, reason):
    rounded_p = get_rounded_price(price)
    ts = int(datetime.now().timestamp() * 1000)
    
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO signals (symbol,tf,direction,price,reason,ts) VALUES (?,?,?,?,?,?)",
                         (symbol, tf, direction, rounded_p, reason, ts))
        await db.commit()

    icons = {"LONG": "🟢", "SHORT": "🔴", "CLOSE_LONG": "✅", "CLOSE_SHORT": "✅"}
    icon = icons.get(direction, "💡")
    msg = (f"🚀 <b>OZ SCANNER v3.2.1</b>\n{icon} <b>{direction}</b>\n💰 {symbol} | ⏰ {tf}\n"
           f"💲 Цена: <code>{rounded_p}</code>\n🔥 {reason}")
    
    await send_telegram(msg)
    await send_to_webhook(symbol, tf, direction, price, reason)

# ========================= ЯДРО СКАНЕРА =========================
async def check_pair(exchange, symbol, tf):
    if not await is_coin_enabled(symbol): return
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=tf, limit=100)
        if not ohlcv or len(ohlcv) < 60: return
        
        df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
        df['ema34'] = talib.EMA(df['close'], 34)
        df['ema55'] = talib.EMA(df['close'], 55)
        df['ema200'] = talib.EMA(df['close'], 200)
        df['rsi'] = talib.RSI(df['close'], 14)
        
        # Проверка на NaN значения в индикаторах
        if df['ema200'].isnull().iloc[-1]: return

        c, rsi = df['close'].iloc[-1], df['rsi'].iloc[-1]
        prev_c = df['close'].iloc[-2]
        
        ema34, ema55, ema200 = df['ema34'].iloc[-1], df['ema55'].iloc[-1], df['ema200'].iloc[-1]
        
        bullish = c > ema34 > ema55 > ema200
        bearish = c < ema34 < ema55 < ema200
        
        key = f"{symbol}_{tf}"
        now = datetime.now().timestamp()
        cd = COOLDOWNS.get(tf, COOLDOWNS['1h'])

        # LONG ENTRY
        if await get_setting('long_entry_enabled') and bullish and 45 < rsi < 75 and c > prev_c:
            if now - LAST_SIGNAL.get(f"L_{key}", 0) > cd['long']:
                LAST_SIGNAL[f"L_{key}"] = now
                await broadcast_signal(symbol, tf, "LONG", c, "BULLISH TREND")

        # SHORT ENTRY
        if await get_setting('short_entry_enabled') and bearish and 25 < rsi < 55 and c < prev_c:
            if now - LAST_SIGNAL.get(f"S_{key}", 0) > cd['short']:
                LAST_SIGNAL[f"S_{key}"] = now
                await broadcast_signal(symbol, tf, "SHORT", c, "BEARISH TREND")

        # CLOSE LONG
        if await get_setting('close_long_enabled') and c < ema55:
            if now - LAST_SIGNAL.get(f"CL_{key}", 0) > cd['close']:
                LAST_SIGNAL[f"CL_{key}"] = now
                await broadcast_signal(symbol, tf, "CLOSE_LONG", c, "EMA55 EXIT")

        # CLOSE SHORT
        if await get_setting('close_short_enabled') and c > ema55:
            if now - LAST_SIGNAL.get(f"CS_{key}", 0) > cd['close_short']:
                LAST_SIGNAL[f"CS_{key}"] = now
                await broadcast_signal(symbol, tf, "CLOSE_SHORT", c, "EMA55 EXIT")

    except Exception as e:
        logger.error(f"Error checking {symbol} {tf}: {e}")

async def scanner_worker():
    # Используем Binance Futures
    ex = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
    await send_telegram("✅ <b>OZ SCANNER v3.2.1</b>: Система онлайн. Режим Fly.io.")
    
    while True:
        try:
            tasks = []
            for s in ALL_SYMBOLS:
                if await is_coin_enabled(s):
                    tf = await get_tf_for_coin(s)
                    tasks.append(check_pair(ex, s, tf))
            
            if tasks:
                # return_exceptions=True чтобы один сбой не убил весь цикл
                await asyncio.gather(*tasks, return_exceptions=True)
            
            await asyncio.sleep(25) # Пауза между итерациями
        except Exception as e:
            logger.error(f"Worker main loop error: {e}")
            await asyncio.sleep(10)

# ========================= WEB UI (FASTAPI) =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(scanner_worker())
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def login():
    return '<body style="background:#000;color:#0f0;display:flex;justify-content:center;align-items:center;height:100vh;font-family:monospace"><div><h1>OZ ULTRA 3.2.1</h1><form action="/auth" method="post"><input type="password" name="pw" autofocus style="background:#111;color:#0f0;border:1px solid #0f0;padding:10px"><button type="submit" style="padding:10px;background:#0f0;color:#000;border:none;cursor:pointer">LOGIN</button></form></div></body>'

@app.post("/auth")
async def auth(pw: str = Form(...)):
    if pw == "777": return RedirectResponse("/admin", status_code=303)
    return "DENIED"

@app.get("/admin", response_class=HTMLResponse)
async def admin():
    # Глобальные настройки
    g_row = '<div style="display:flex;gap:15px;margin-bottom:20px;flex-wrap:wrap;background:#1a1a1a;padding:15px;border-radius:8px;border:1px solid #333">'
    for k in ['long_entry_enabled', 'short_entry_enabled', 'close_long_enabled', 'close_short_enabled']:
        val = await get_setting(k)
        label = k.replace('_enabled', '').replace('_', ' ').upper()
        btn_color = "#2ecc71" if val else "#e74c3c"
        g_row += f'<a href="/toggle_g/{k}" style="text-decoration:none;background:{btn_color};color:white;padding:5px 12px;border-radius:4px;font-size:12px;font-weight:bold">{label}: {"ON" if val else "OFF"}</a>'
    g_row += '</div>'

    # Сетка монет
    grid = '<div style="display:grid;grid-template-columns:repeat(auto-fill, minmax(280px, 1fr));gap:12px;">'
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT symbol, enabled, tf FROM coin_settings") as cur:
            rows = await cur.fetchall()
            for r in rows:
                sym, en, cur_tf = r
                safe = sym.replace("/", "_")
                bg = "#1a2a1a" if en else "#1a1a1a"
                border = "#2ecc71" if en else "#333"
                
                grid += f'''
                <div style="background:{bg}; border:1px solid {border}; padding:12px; border-radius:8px; display:flex; flex-direction:column; gap:8px">
                    <div style="display:flex; justify-content:space-between; align-items:center">
                        <span style="font-weight:bold; color:{"#2ecc71" if en else "#888"}">{sym}</span>
                        <a href="/toggle_c/{safe}" style="text-decoration:none; background:{"#2ecc71" if en else "#444"}; color:white; padding:3px 10px; border-radius:15px; font-size:11px">{"ACTIVE" if en else "OFF"}</a>
                    </div>
                    <div style="display:flex; gap:4px; flex-wrap:wrap">
                '''
                for t in ALL_TFS:
                    is_active = (t == cur_tf)
                    t_style = "background:#2ecc71; color:black;" if is_active else "background:#333; color:#aaa;"
                    grid += f'<a href="/set_tf/{safe}/{t}" style="text-decoration:none; {t_style} padding:2px 6px; border-radius:3px; font-size:10px">{t}</a>'
                grid += '</div></div>'
    grid += '</div>'

    return f"""
    <body style="background:#0a0a0a; color:#eee; font-family: -apple-system, sans-serif; padding:15px; max-width:1200px; margin:auto">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px">
            <h2 style="margin:0; color:#2ecc71">OZ SCANNER PRO 3.2.1</h2>
            <div style="display:flex; gap:15px">
                <a href="/history" style="color:#aaa; text-decoration:none; font-size:13px">История сигналов</a>
                <a href="/admin" style="color:#2ecc71; text-decoration:none; font-size:13px">Обновить</a>
            </div>
        </div>
        {g_row}
        {grid}
    </body>
    """

@app.get("/toggle_g/{key}")
async def tg_g(key: str):
    await toggle_setting(key)
    return RedirectResponse("/admin")

@app.get("/toggle_c/{sym}")
async def tg_c(sym: str):
    sym = sym.replace("_", "/")
    curr = await is_coin_enabled(sym)
    await update_coin(sym, enabled=(0 if curr else 1))
    return RedirectResponse("/admin")

@app.get("/set_tf/{sym}/{tf}")
async def st_tf(sym: str, tf: str):
    await update_coin(sym.replace("_", "/"), tf=tf)
    return RedirectResponse("/admin")

@app.get("/history", response_class=HTMLResponse)
async def history():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT symbol, direction, price, tf, datetime(ts/1000,'unixepoch','localtime') FROM signals ORDER BY ts DESC LIMIT 50") as cur:
            rows = await cur.fetchall()
    h = '<table style="width:100%; border-collapse:collapse; color:#2ecc71; font-family:monospace">'
    h += '<tr style="background:#111"><th>Дата</th><th>Монета</th><th>Тип</th><th>ТФ</th><th>Цена</th></tr>'
    for r in rows:
        h += f'<tr style="border-bottom:1px solid #222"><td style="padding:8px">{r[4]}</td><td>{r[0]}</td><td>{r[1]}</td><td>{r[3]}</td><td>{r[2]}</td></tr>'
    return f'<body style="background:#000; color:white; padding:20px; font-family:sans-serif"><h2 style="color:#2ecc71">HISTORY</h2>{h}</table><br><a href="/admin" style="color:#aaa">BACK</a></body>'

if __name__ == "__main__":
    import uvicorn
    # Запуск на порту 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)
