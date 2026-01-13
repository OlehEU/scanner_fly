"""
=============================================================================
🚀 OZ SCANNER ULTRA PRO v3.5.2 | FINAL STABLE
=============================================================================
- UI: Интерфейс 3.2.1 (Черно-зеленый, адаптированный под мобильные).
- Core: Исправлены ошибки Ambiguity (iloc[-1]) для стабильных расчетов.
- DB: Сохранение состояния монет, таймфреймов и статистики.
- Network: Уведомления при старте, обработка ошибок, кулдауны.
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

# Список монет, включая актуальные хайп-активы
ALL_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT", 
    "DOGE/USDT", "1000SHIB/USDT", "1000PEPE/USDT", "1000BONK/USDT", 
    "1000FLOKI/USDT", "1000SATS/USDT", "NEAR/USDT", "SUI/USDT", "TIA/USDT",
    "FARTCOIN/USDT", "PNUT/USDT", "ACT/USDT", "RENDER/USDT", "AVAX/USDT"
]

DB_PATH = "oz_ultra_v3.db"
# Кулдауны для предотвращения спама сигналами
COOLDOWNS = {'1m': 240, '5m': 480, '15m': 720, '30m': 1200, '1h': 3600, '4h': 10800}
LAST_SIGNALS = {} 

# ========================= СЕРВИСНЫЕ ФУНКЦИИ =========================

def get_rounded_price(price: float) -> float:
    """Округление цены согласно правилам точности бирж."""
    if price < 0.0001: return round(price, 8)
    elif price < 0.05: return round(price, 7)
    elif price < 1.0: return round(price, 6)
    elif price < 100.0: return round(price, 4)
    else: return round(price, 2)

async def send_to_webhook(payload: dict):
    """Отправка сигнала на торговый бот."""
    headers = {"X-Webhook-Secret": WEBHOOK_SECRET, "Content-Type": "application/json"}
    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(WEBHOOK_URL, json=payload, headers=headers, timeout=10) as resp:
                if resp.status == 200: 
                    await update_stat('signals_sent')
                    logger.info(f"Webhook sent: {payload['symbol']} {payload['signal']}")
        except Exception as e:
            logger.error(f"Webhook error: {e}")

async def send_tg(text: str):
    """Отправка уведомления в Telegram."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with aiohttp.ClientSession() as session:
        try:
            await session.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"})
        except Exception as e:
            logger.error(f"Telegram send error: {e}")

# ========================= БАЗА ДАННЫХ =========================

async def init_db():
    """Инициализация таблиц и начальных настроек."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS stats (key TEXT PRIMARY KEY, value INTEGER DEFAULT 0);
            CREATE TABLE IF NOT EXISTS coin_settings (
                symbol TEXT PRIMARY KEY, tf TEXT DEFAULT '1h', enabled INTEGER DEFAULT 0
            );
        ''')
        # Глобальные настройки
        for key in ['long_enabled', 'short_enabled', 'close_enabled', 'password']:
            val = '777' if key == 'password' else '1'
            await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES (?,?)", (key, val))
        # Статистика
        for s_key in ['total_scans', 'signals_sent', 'errors']:
            await db.execute("INSERT OR IGNORE INTO stats (key,value) VALUES (?, 0)", (s_key,))
        # Монеты
        for s in ALL_SYMBOLS:
            await db.execute("INSERT OR IGNORE INTO coin_settings (symbol) VALUES (?)", (s,))
        await db.commit()
    logger.info("Database initialized.")

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
    """Анализ конкретной пары."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT enabled, tf FROM coin_settings WHERE symbol=?", (symbol,)) as cur:
            row = await cur.fetchone()
    
    if not row or not row[0]: return 
    tf = row[1]
    
    try:
        # Запрос свечей
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=tf, limit=210)
        if not ohlcv or len(ohlcv) < 201: return
        
        df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
        
        # Индикаторы
        df['ema34'] = talib.EMA(df['close'], 34)
        df['ema55'] = talib.EMA(df['close'], 55)
        df['ema200'] = talib.EMA(df['close'], 200)
        df['rsi'] = talib.RSI(df['close'], 14)
        df['atr'] = talib.ATR(df['high'], df['low'], df['close'], 14)
        
        # Последние значения (iloc[-1] для безопасности)
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

        # Логика LONG
        if (c > e34) and (e34 > e55) and (e55 > e200) and (48 < rsi < 78) and ((c - p) > (atr * 0.2)):
            if await get_setting('long_enabled'):
                signal, reason = "LONG", "BULL TREND + MOMENTUM"

        # Логика SHORT
        elif (c < e34) and (e34 < e55) and (e55 < e200) and (22 < rsi < 52) and ((p - c) > (atr * 0.2)):
            if await get_setting('short_enabled'):
                signal, reason = "SHORT", "BEAR TREND + MOMENTUM"

        if signal:
            full_sig_key = f"{signal}_{symbol}_{tf}"
            if now_ts - LAST_SIGNALS.get(full_sig_key, 0) > COOLDOWNS.get(tf, 3600):
                LAST_SIGNALS[full_sig_key] = now_ts
                rp = get_rounded_price(c)
                
                # Отправка
                await send_to_webhook({
                    "symbol": symbol.replace("/", ""), 
                    "signal": signal, 
                    "timeframe": tf, 
                    "price": rp, 
                    "reason": reason,
                    "source": "OZ_ULTRA_3.5.2"
                })
                
                icon = "🟢" if "LONG" in signal else "🔴"
                await send_tg(f"{icon} <b>{signal}</b> | {symbol} [{tf}]\nЦена: <code>{rp}</code>\nПричина: {reason}")

        await update_stat('total_scans')
    except Exception as e:
        logger.error(f"Error scanning {symbol}: {e}")
        await update_stat('errors')

async def scanner_worker():
    """Фоновый воркер сканера."""
    ex = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
    await send_tg("✅ <b>OZ SCANNER v3.5.2</b>: Система запущена и готова к работе.")
    
    while True:
        try:
            async with aiosqlite.connect(DB_PATH) as db:
                async with db.execute("SELECT symbol FROM coin_settings WHERE enabled=1") as cur:
                    active = [r[0] for r in await cur.fetchall()]
            
            if active:
                # Группировка по 5 монет для соблюдения лимитов API
                for i in range(0, len(active), 5):
                    batch = active[i:i+5]
                    await asyncio.gather(*[check_pair(ex, s) for s in batch], return_exceptions=True)
                    await asyncio.sleep(1)
            
            await asyncio.sleep(20)
        except Exception as e:
            logger.error(f"Worker main loop error: {e}")
            await asyncio.sleep(10)

# ========================= WEB APP (ИНТЕРФЕЙС) =========================

@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(scanner_worker())
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def login_page():
    """Страница входа (Тихий режим для Fly.io)."""
    return '''
    <html>
    <body style="background:#000;color:#0f0;font-family:monospace;display:flex;flex-direction:column;justify-content:center;align-items:center;height:100vh;margin:0">
        <h1 style="letter-spacing:5px">OZ ULTRA PRO</h1>
        <form action="/login" method="post" style="background:#111;padding:30px;border:1px solid #0f0;border-radius:10px">
            <input type="password" name="password" placeholder="PASSWORD" autofocus style="font-size:20px;padding:10px;background:#000;color:#0f0;border:1px solid #0f0;outline:none;width:200px"><br><br>
            <button type="submit" style="width:100%;padding:10px;background:#0f0;color:#000;border:none;cursor:pointer;font-weight:bold;font-family:monospace">ВХОД</button>
        </form>
    </body>
    </html>
    '''

@app.post("/login")
async def login(password: str = Form(...)):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key='password'") as cur:
            row = await cur.fetchone()
            if row and password == row[0]: return RedirectResponse("/admin", status_code=303)
    return HTMLResponse("<h2>ACCESS DENIED</h2><a href='/'>Back</a>", status_code=403)

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel():
    """Панель управления (UI 3.2.1)."""
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
        return f'<a href="/toggle_s/{key}" style="text-decoration:none;color:{color};border:1px solid {color};padding:8px 15px;border-radius:4px;background:{bg};font-weight:bold;font-size:12px">{label}</a>'

    # Генерация строк таблицы
    rows = ""
    for s, en, tf in coins:
        safe_s = s.replace("/", "_")
        status_color = "#0f0" if en else "#444"
        btn = f'<a href="/toggle_c/{safe_s}" style="color:{status_color};text-decoration:none;font-weight:bold">[{ "ACTIVE" if en else "OFF" }]</a>'
        
        tf_links = ""
        for t in ['1m','5m','15m','30m','1h','4h']:
            is_active = (tf == t)
            tf_color = "#0f0" if is_active else "#555"
            tf_weight = "bold" if is_active else "normal"
            tf_links += f'<a href="/set_tf/{safe_s}/{t}" style="color:{tf_color};text-decoration:none;margin-right:10px;font-weight:{tf_weight}">{t}</a>'
            
        rows += f'<tr style="border-bottom:1px solid #222"><td style="padding:12px">{s}</td><td style="padding:12px">{btn}</td><td style="padding:12px">{tf_links}</td></tr>'

    return f'''
    <html>
    <head>
        <title>OZ ADMIN v3.5.2</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ background:#000; color:#eee; font-family:monospace; padding:15px; margin:0; }}
            .container {{ max-width:900px; margin:auto; }}
            th {{ text-align:left; color:#888; padding:10px; font-size:12px; border-bottom:1px solid #333; }}
            .stats-bar {{ color:#888; font-size:11px; margin-bottom:20px; border-top:1px solid #222; padding-top:10px; }}
            @media (max-width: 600px) {{ 
                td, th {{ font-size: 11px; padding: 8px 4px !important; }} 
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px">
                <h2 style="color:#0f0; margin:0">OZ ULTRA v3.5.2</h2>
                <a href="/admin" style="color:#fff; text-decoration:none; font-size:12px; border:1px solid #555; padding:5px 10px; border-radius:4px">UPDATE</a>
            </div>
            
            <div style="display:flex; gap:10px; flex-wrap:wrap; margin-bottom:25px">
                {get_btn('long_enabled', 'LONG ENTRY')}
                {get_btn('short_enabled', 'SHORT ENTRY')}
                {get_btn('close_enabled', 'EXIT LOGIC')}
            </div>

            <div class="stats-bar">
                SCANS: {stats.get('total_scans')} | SIGNALS: {stats.get('signals_sent')} | ERRORS: {stats.get('errors')}
            </div>

            <table style="width:100%; border-collapse:collapse; background:#080808; border-radius:8px; overflow:hidden">
                <tr style="background:#111">
                    <th>ASSET</th><th>STATUS</th><th>TIMEFRAMES</th>
                </tr>
                {rows}
            </table>
            <div style="margin-top:30px; text-align:center; color:#444; font-size:10px">OZ SYSTEM © 2026 - FLY.IO STABLE</div>
        </div>
    </body>
    </html>
    '''

@app.get("/toggle_s/{key}")
async def toggle_setting_route(key: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE settings SET value = CASE WHEN value='1' THEN '0' ELSE '1' END WHERE key=?", (key,))
        await db.commit()
    return RedirectResponse("/admin")

@app.get("/toggle_c/{symbol}")
async def toggle_coin_route(symbol: str):
    real_symbol = symbol.replace("_", "/")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE coin_settings SET enabled = 1 - enabled WHERE symbol=?", (real_symbol,))
        await db.commit()
    return RedirectResponse("/admin")

@app.get("/set_tf/{symbol}/{tf}")
async def set_tf_route(symbol: str, tf: str):
    real_symbol = symbol.replace("_", "/")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE coin_settings SET tf = ? WHERE symbol=?", (tf, real_symbol))
        await db.commit()
    return RedirectResponse("/admin")

if __name__ == "__main__":
    import uvicorn
    # Запуск на порту 8000 для Fly.io
    uvicorn.run(app, host="0.0.0.0", port=8000)
