"""
=============================================================================
🚀 OZ SCANNER ULTRA PRO v3.5.1 | MERGED & STABLE
=============================================================================
- Исправлено: Округление цен (get_rounded_price) для Binance Futures.
- Исправлено: Формат символов для Webhook (BTCUSDT).
- Добавлено: 4 типа сигналов (Long/Short/Close).
- Добавлено: Управление таймфреймами для каждой монеты отдельно.
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
from fastapi import FastAPI, Request, HTTPException, Form
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
DB_PATH = "/data/oz_ultra_v3.db" if os.path.exists("/data") else "oz_ultra_v3.db"

# Кулдауны (в секундах)
COOLDOWNS = {
    '1m': 240, '5m': 480, '15m': 720, '30m': 1200, '1h': 3600, '4h': 10800
}
LAST_SIGNALS = {} # { "LONG_BTC/USDT_1h": timestamp }

# ========================= СЛУЖЕБНЫЕ ФУНКЦИИ =========================

def get_rounded_price(price: float) -> float:
    """Динамическое округление для соответствия точности Binance"""
    if price < 0.05: return round(price, 8)
    elif price < 1.0: return round(price, 6)
    else: return round(price, 3)

async def send_to_webhook(payload: dict):
    """Отправка сигнала в торговый бот с авторизацией"""
    headers = {"X-Webhook-Secret": WEBHOOK_SECRET, "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(WEBHOOK_URL, json=payload, headers=headers, timeout=10) as resp:
                if resp.status == 200:
                    logger.info(f"✅ Webhook sent: {payload['symbol']} {payload['signal']}")
                    await update_stat('signals_sent')
                else:
                    logger.error(f"❌ Webhook error {resp.status}: {await resp.text()}")
        except Exception as e:
            logger.error(f"❌ Webhook connection failed: {e}")

async def send_tg(text: str):
    """Отправка уведомления в Telegram"""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
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
        # Начальные настройки
        for key in ['long_enabled', 'short_enabled', 'close_enabled', 'password']:
            val = '777' if key == 'password' else '1'
            await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (key, val))
        for s_key in ['total_scans', 'signals_sent', 'errors']:
            await db.execute("INSERT OR IGNORE INTO stats (key,value) VALUES (?, 0)", (s_key,))
        for s in ALL_SYMBOLS:
            await db.execute("INSERT OR IGNORE INTO coin_settings (symbol) VALUES (?)", (s,))
        await db.commit()
    logger.info("💾 Database Ready.")

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
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=tf, limit=200)
        if not ohlcv: return
        
        df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
        
        # Индикаторы
        df['ema34'] = talib.EMA(df['close'], 34)
        df['ema55'] = talib.EMA(df['close'], 55)
        df['ema200'] = talib.EMA(df['close'], 200)
        df['rsi'] = talib.RSI(df['close'], 14)
        df['atr'] = talib.ATR(df['high'], df['low'], df['close'], 14)
        
        if len(df) < 100 or df['ema200'].isnull().iloc[-1]: return
        
        curr = df.iloc[-1]
        prev = df.iloc[-2]
        c = curr['close']
        rsi = curr['rsi']
        atr = curr['atr'] or 0.000001
        
        signal = None
        reason = ""
        now_ts = datetime.now().timestamp()
        sig_key_base = f"{symbol}_{tf}"

        # 1. ЛОГИКА LONG
        trend_bull = c > curr['ema34'] > curr['ema55'] > curr['ema200']
        if trend_bull and 45 < rsi < 80 and (c - prev) > (atr * 0.3):
            if await get_setting('long_enabled'):
                signal = "LONG"
                reason = "BULL Trend + RSI + Momentum"

        # 2. ЛОГИКА SHORT
        trend_bear = c < curr['ema34'] < curr['ema55'] < curr['ema200']
        if trend_bear and 20 < rsi < 55 and (prev - c) > (atr * 0.3):
            if await get_setting('short_enabled'):
                signal = "SHORT"
                reason = "BEAR Trend + RSI + Momentum"

        # 3. ЛОГИКА ЗАКРЫТИЯ (Простейшая)
        if (signal == "LONG" and c < curr['ema55']) or (signal == "SHORT" and c > curr['ema55']):
             if await get_setting('close_enabled'):
                 signal = f"CLOSE_{signal}"
                 reason = "Trend Break (EMA55)"

        # ПРОВЕРКА КУЛДАУНА И ОТПРАВКА
        if signal:
            full_sig_key = f"{signal}_{sig_key_base}"
            cd = COOLDOWNS.get(tf, 3600)
            
            if now_ts - LAST_SIGNALS.get(full_sig_key, 0) > cd:
                LAST_SIGNALS[full_sig_key] = now_ts
                rounded_p = get_rounded_price(c)
                
                # Запись в БД
                async with aiosqlite.connect(DB_PATH) as db:
                    await db.execute(
                        "INSERT INTO signals (symbol, tf, direction, price, reason, ts) VALUES (?,?,?,?,?,?)",
                        (symbol, tf, signal, rounded_p, reason, int(now_ts))
                    )
                    await db.commit()
                
                # Webhook & TG
                payload = {
                    "symbol": symbol.replace("/", ""),
                    "signal": signal,
                    "timeframe": tf,
                    "price": rounded_p,
                    "reason": reason,
                    "source": "OZ_ULTRA_V3"
                }
                await send_to_webhook(payload)
                
                icon = "🟢" if "LONG" in signal else "🔴"
                tg_text = (f"🚀 <b>OZ SCANNER v3.5</b>\n"
                           f"{icon} <b>{signal}</b> | {symbol} [{tf}]\n"
                           f"Цена: <code>{rounded_p}</code>\n"
                           f"Причина: {reason}")
                await send_tg(tg_text)

        await update_stat('total_scans')

    except Exception as e:
        await update_stat('errors')
        logger.error(f"Error {symbol}: {e}")

async def scanner_worker():
    ex = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
    logger.info("🚀 Scanner Started...")
    while True:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT symbol FROM coin_settings WHERE enabled=1") as cur:
                    active_pairs = [r[0] for r in await cur.fetchall()]
            
            if active_pairs:
                # Пачками по 5, чтобы не грузить CPU
                for i in range(0, len(active_pairs), 5):
                    batch = active_pairs[i:i+5]
                    await asyncio.gather(*[check_pair(ex, s) for s in batch])
                    await asyncio.sleep(0.2)
            
            await asyncio.sleep(20)
        except Exception as e:
            logger.error(f"Worker Error: {e}")
            await asyncio.sleep(10)

# ========================= WEB APP =========================

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(scanner_worker())
    await send_tg("✅ <b>OZ SCANNER v3.5.1</b> Запущен.\nВсе системы в норме.")
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def login_page():
    return """
    <html><body style="background:#000;color:#0f0;font-family:monospace;text-align:center;padding-top:15%">
    <h1>OZ ULTRA PRO v3.5</h1>
    <form action="/login" method="post">
        <input type="password" name="password" placeholder="Пароль" style="font-size:20px;padding:10px;background:#111;color:#0f0;border:1px solid #0f0"><br><br>
        <button type="submit" style="font-size:20px;padding:10px 30px;background:#0f0;color:#000;border:none;cursor:pointer">ВХОД</button>
    </form>
    </body></html>
    """

@app.post("/login")
async def login(password: str = Form(...)):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key='password'") as cur:
            row = await cur.fetchone()
            if row and password == row[0]:
                return RedirectResponse("/panel", status_code=303)
    return HTMLResponse("<h1 style='color:red;background:#000'>ОТКАЗАНО В ДОСТУПЕ</h1>")

@app.get("/panel", response_class=HTMLResponse)
async def admin_panel():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT key, value FROM stats") as cur:
            stats = {k: v for k, v in await cur.fetchall()}
        async with db.execute("SELECT symbol, enabled, tf FROM coin_settings ORDER BY symbol ASC") as cur:
            coins = await cur.fetchall()
        
        # Глобальные настройки
        global_opts = ""
        for k in ['long_enabled', 'short_enabled', 'close_enabled']:
            async with db.execute("SELECT value FROM settings WHERE key=?", (k,)) as c_cur:
                status = (await c_cur.fetchone())[0] == '1'
                color = "#0f0" if status else "#f00"
                global_opts += f'<span>{k.upper()}: <b style="color:{color}">{"ON" if status else "OFF"}</b> <a href="/toggle_opt/{k}" style="color:#555">[изменить]</a></span> '

    grid = '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:15px;margin-top:20px">'
    for s, en, tf in coins:
        safe_s = s.replace("/", "---")
        color = "#2ecc71" if en else "#444"
        grid += f'''
        <div style="background:#111;padding:15px;border:1px solid {color};border-radius:10px">
            <div style="display:flex;justify-content:space-between">
                <b style="color:#fff">{s}</b>
                <a href="/toggle_c/{safe_s}" style="color:{color};text-decoration:none">{"[ВКЛ]" if en else "[ВЫКЛ]"}</a>
            </div>
            <div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:4px">
                {" ".join([f'<a href="/set_tf/{safe_s}/{t}" style="font-size:10px;padding:4px;background:{"#222" if t==tf else "#000"};color:{"#0f0" if t==tf else "#888"};border:1px solid #333;text-decoration:none">{t}</a>' for t in ALL_TFS])}
            </div>
        </div>'''
    grid += '</div>'

    return f"""
    <html>
    <head><title>OZ ADMIN</title><meta name="viewport" content="width=device-width, initial-scale=1"></head>
    <body style="background:#0a0a0a;color:#eee;font-family:sans-serif;padding:20px">
        <div style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #333;padding-bottom:10px">
            <h2 style="color:#0f0;margin:0">OZ ULTRA PRO v3.5.1</h2>
            <div style="font-size:12px">{global_opts}</div>
        </div>
        <div style="background:#1a1a1a;padding:15px;border-radius:10px;margin:20px 0;display:flex;justify-content:space-around;font-family:monospace">
            <span>SCANS: <b style="color:#0f0">{stats.get('total_scans', 0)}</b></span>
            <span>SIGNALS: <b style="color:#0f0">{stats.get('signals_sent', 0)}</b></span>
            <span>ERRORS: <b style="color:red">{stats.get('errors', 0)}</b></span>
        </div>
        {grid}
        <div style="margin-top:30px;text-align:center"><a href="/signals" style="color:#0f0">Просмотр последних 50 сигналов</a></div>
    </body>
    </html>
    """

@app.get("/toggle_opt/{key}")
async def toggle_opt(key: str):
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

@app.get("/signals", response_class=HTMLResponse)
async def view_signals():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT symbol, direction, tf, price, reason, ts FROM signals ORDER BY ts DESC LIMIT 50") as cur:
            rows = await cur.fetchall()
    
    table = "<table border=1 style='width:100%;color:#eee;border-collapse:collapse;text-align:left'><tr><th>Время</th><th>Пара</th><th>Сигнал</th><th>ТФ</th><th>Цена</th><th>Причина</th></tr>"
    for r in rows:
        dt = datetime.fromtimestamp(r[5]).strftime('%H:%M:%S')
        color = "#2ecc71" if "LONG" in r[1] else "#e74c3c"
        table += f"<tr><td>{dt}</td><td>{r[0]}</td><td style='color:{color}'>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td><td>{r[4]}</td></tr>"
    table += "</table>"
    
    return f"<html><body style='background:#000;color:#0f0;padding:20px;font-family:monospace'><h2>Последние сигналы</h2>{table}<br><a href='/panel' style='color:#fff'>Назад</a></body></html>"

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
