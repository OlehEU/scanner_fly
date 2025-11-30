# main.py — OZ SCANNER ULTRA PRO 2026 v2.8 | Финальная версия 2025
import ccxt.async_support as ccxt
import asyncio
import pandas as pd
import talib
import aiosqlite
import os
from datetime import datetime
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import aiohttp
from contextlib import asynccontextmanager

# ========================= КОНФИГУРАЦИЯ БЕЗОПАСНОСТИ И ЭНДПОИНТОВ =========================
# ПРОВЕРКА: СЕКРЕТ И ТОКЕН БОТА ДОЛЖНЫ БЫТЬ ОПРЕДЕЛЕНЫ
required_env = ["TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "WEBHOOK_URL", "WEBHOOK_SECRET"]
for v in required_env:
    if not os.getenv(v):
        # В случае запуска на сервере, эта ошибка будет видна в логах
        print(f"ОШИБКА: Не определена переменная окружения {v}. СКАНИРОВАНИЕ НЕВОЗМОЖНО.")
        # Для безопасности оставляем пустыми, но приложение не сможет отправлять сигналы
        
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://bot-fly-oz.fly.dev/webhook") # Используйте ENV, или оставьте по умолчанию
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET") # Секрет, который должен совпадать с секретом торгового бота

# ========================= НАСТРОЙКИ =========================
ALL_SYMBOLS = ["DOGE/USDT", "XRP/USDT", "SOL/USDT", "FARTCOIN/USDT"]
ALL_TFS = ['1m', '5m', '30m', '45m', '1h', '4h']
DB_PATH = "oz_ultra.db"

# Кулдауны под каждый таймфрейм (в секундах)
COOLDOWNS = {
    '1m': {'long': 240, 'close': 180},
    '5m': {'long': 480, 'close': 300},
    '30m': {'long': 1200, 'close': 600},
    '45m': {'long': 1800, 'close': 900},
    '1h': {'long': 3600, 'close': 1800},
    '4h': {'long': 10800, 'close': 5400},
}

LAST_SIGNAL = {}  # {"LONG_DOGE/USDT_45m": timestamp, ...}

# ========================= БАЗА =========================
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript('''
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, tf TEXT, direction TEXT, price REAL, reason TEXT, ts INTEGER
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS coin_settings (
                symbol TEXT PRIMARY KEY,
                tf TEXT DEFAULT '1h',
                enabled INTEGER DEFAULT 1
            );
        ''')
        # Гарантируем, что пароль установлен
        await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('password','777')") 
        
        # Всегда добавляем все монеты из ALL_SYMBOLS
        for s in ALL_SYMBOLS:
            await db.execute(
                "INSERT OR IGNORE INTO coin_settings (symbol, tf, enabled) VALUES (?, '1h', 1)",
                (s,)
            )
        await db.commit()

async def is_coin_enabled(symbol: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT enabled FROM coin_settings WHERE symbol=?", (symbol,)) as cur:
            row = await cur.fetchone()
            return bool(row[0]) if row else True

async def get_tf_for_coin(symbol: str) -> str:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT tf FROM coin_settings WHERE symbol=?", (symbol,)) as cur:
            row = await cur.fetchone()
            return row[0] if row else "1h"

async def set_coin_enabled(symbol: str, enabled: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE coin_settings SET enabled=? WHERE symbol=?", (enabled, symbol))
        await db.commit()

async def set_tf_for_coin(symbol: str, tf: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE coin_settings SET tf=? WHERE symbol=?", (tf, symbol))
        await db.commit()

# ========================= ОТПРАВКА =========================
async def send_telegram(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: return
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                               json={"chat_id": int(TELEGRAM_CHAT_ID), "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
    except Exception as e:
        print(f"[ERROR] Telegram send failed: {e}")


async def send_to_oz_webhook(symbol: str, tf: str, direction: str, price: float, reason: str):
    if not WEBHOOK_SECRET:
        print("[WARNING] WEBHOOK_SECRET не установлен. Пропуск отправки на бот.")
        return
        
    payload = {
        "symbol": symbol.split('/')[0] + 'USDT', # Отправляем DOGEUSDT вместо DOGE/USDT, как ожидает бот
        "signal": direction.upper(), 
        "timeframe": tf,
        "price": round(price, 8),
        "reason": reason,
        "source": "OZ SCANNER ULTRA PRO 2026 v2.8"
    }
    
    # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: Отправка секрета в заголовке
    headers = {
        "X-Webhook-Secret": WEBHOOK_SECRET
    }
    
    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.post(WEBHOOK_URL, json=payload, timeout=10) as response:
                 # Выводим ошибку, если статус не 200 (OK)
                 if response.status != 200:
                    print(f"[ERROR] Webhook failed for {symbol}: {response.status} - {await response.text()}")
        except Exception as e:
            print(f"[ERROR] Webhook connection failed for {symbol}: {e}")


async def send_signal(symbol, tf, direction, price, reason):
    ts = int(datetime.now().timestamp() * 1000)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO signals (symbol,tf,direction,price,reason,ts) VALUES (?,?,?,?,?,?)",
                         (symbol, tf, direction, price, reason, ts))
        await db.commit()

    text = (f"OZ ULTRA PRO 2026 v2.8\n"
            f"<b>{direction.upper()}</b>\n"
            f"<code>{symbol}</code> | <code>{tf}</code>\n"
            f"Цена: <code>{price:.6f}</code>\n"
            f"<b>{reason}</b>\n"
            f"<a href='https://www.tradingview.com/chart/?symbol=BINANCE:{symbol.replace('/', '')}&interval={tf}'>ГРАФИК</a>")

    await send_telegram(text)
    await send_to_oz_webhook(symbol, tf, direction, price, reason)

# ========================= СКАНЕР =========================
async def check_pair(exchange, symbol, tf):
    if not await is_coin_enabled(symbol):
        return
    try:
        # Убедитесь, что лимит данных достаточен для всех EMA (300+ свечей)
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=tf, limit=500)
        if len(ohlcv) < 300: return

        df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
        df['ema34'] = talib.EMA(df['close'], 34)
        df['ema55'] = talib.EMA(df['close'], 55)
        df['ema200'] = talib.EMA(df['close'], 200)
        df['rsi'] = talib.RSI(df['close'], 14)
        df['atr'] = talib.ATR(df['high'], df['low'], df['close'], 14)
        df['vol_ma20'] = df['volume'].rolling(20).mean()

        c = df['close'].iloc[-1]
        prev = df['close'].iloc[-2]
        rsi = df['rsi'].iloc[-1]
        vol = df['volume'].iloc[-1]
        vol_avg = df['vol_ma20'].iloc[-1]
        atr = df['atr'].iloc[-1] or 0.000001 # Защита от деления на ноль

        key = f"{symbol}_{tf}"
        now = datetime.now().timestamp()

        # Условия бычьего тренда
        trend_bull = (
            c > df['ema34'].iloc[-1] > df['ema55'].iloc[-1] > df['ema200'].iloc[-1] and
            df['ema34'].iloc[-1] > df['ema34'].iloc[-3] and # EMA34 растет
            df['ema55'].iloc[-1] > df['ema55'].iloc[-8] # EMA55 растет
        )

        # Условия на вход в LONG
        long_cond = trend_bull and \
                    40 < rsi < 82 and \
                    vol > vol_avg * (1.7 if tf in ['1h','4h','45m'] else 2.4) and \
                    c > prev and \
                    (c - prev) > atr * 0.4 and \
                    df['low'].iloc[-1] > df['ema34'].iloc[-1] * 0.997 # Защита от пробоя EMA

        # Условия на выход (CLOSE)
        close_cond = (
            c < df['ema55'].iloc[-1] or # Цена пробила главную линию поддержки
            (c < df['ema34'].iloc[-1] and rsi > 80) or # Пробила быструю EMA + RSI перегрет
            (c < prev and (prev - c) > atr * 2.2) # Резкое движение вниз (ATR)
        )

        cd = COOLDOWNS.get(tf, {'long': 3600, 'close': 1800})

        if long_cond and now - LAST_SIGNAL.get(f"LONG_{key}", 0) > cd['long']:
            LAST_SIGNAL[f"LONG_{key}"] = now
            await send_signal(symbol, tf, "LONG", c, "МОЩНЫЙ ТРЕНД + ОБЪЁМ + EMA55")

        if close_cond and now - LAST_SIGNAL.get(f"CLOSE_{key}", 0) > cd['close']:
            LAST_SIGNAL[f"CLOSE_{key}"] = now
            await send_signal(symbol, tf, "CLOSE", c, "ТРЕНД СЛОМАН — ФИКСИРУЕМ")

    except Exception as e:
        # Вывод ошибки в консоль, чтобы не прерывать сканер
        print(f"[Ошибка] {symbol} {tf}: {e}")

async def scanner_background():
    # Настройка CCXT для Futures
    ex = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
    await send_telegram("OZ SCANNER ULTRA PRO 2026 v2.8 — ЗАПУЩЕН\nКонфигурация: FARTCOIN + 45m + ТЕЛЕГА + ХУК\nК миллиардам!")
    
    while True:
        tasks = []
        for s in ALL_SYMBOLS:
            if await is_coin_enabled(s):
                tf = await get_tf_for_coin(s)
                tasks.append(check_pair(ex, s, tf))
        
        # Запускаем все проверки параллельно
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            
        await asyncio.sleep(18) # Пауза между циклами

# ========================= ВЕБ-ПАНЕЛЬ =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Инициализация базы данных и запуск фоновой задачи
    await init_db()
    asyncio.create_task(scanner_background())
    yield # Приложение готово к приему запросов

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def root():
    return '<html><body style="background:#000;color:#0f0;font-family:monospace;text-align:center;padding-top:15%"><h1>OZ ULTRA PRO 2026 v2.8</h1><form action="/login" method="post"><input type="password" name="password" placeholder="Пароль" style="font-size:24px;padding:12px;width:300px"><br><br><button type="submit" style="font-size:24px;padding:12px 40px">ВОЙТИ</button></form></body></html>'

@app.post("/login")
async def login(password: str = Form(...)):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key='password'") as cur:
            row = await cur.fetchone()
            correct_password = row[0] if row else "777" # Fallback
            
    if password == correct_password:
        # В реальной жизни здесь нужна сессия/токен, но для простоты перенаправляем
        return RedirectResponse("/panel", status_code=303)
    return HTMLResponse("<h1 style='color:red; background:#000'>НЕПРАВИЛЬНЫЙ ПАРОЛЬ</h1>")

@app.get("/panel", response_class=HTMLResponse)
async def panel():
    html = "<pre style='color:#0f0;background:#000;font-size:22px;line-height:3;text-align:center'>OZ ULTRA PRO 2026 v2.8 — УПРАВЛЕНИЕ\n\n"
    for symbol in ALL_SYMBOLS:
        enabled = "ВКЛ" if await is_coin_enabled(symbol) else "ВЫКЛ"
        color = "#0f0" if await is_coin_enabled(symbol) else "#800"
        current_tf = await get_tf_for_coin(symbol)
        safe = symbol.replace("/", "_")
        html += f"<b style='color:{color}'>{symbol}</b> — <b>{enabled}</b> <a href='/toggle/{safe}'>[ТОГГЛ]</a> ТФ: <b>{current_tf}</b>\n"
        for tf in ALL_TFS:
            if tf == current_tf:
                html += f" <u>[{tf}]</u>"
            else:
                html += f" <a href='/set/{safe}/{tf}'>[{tf}]</a>"
        html += "\n\n"
    html += f"<a href='/signals'>СИГНАЛЫ</a>   <a href='/'>ВЫХОД</a></pre>"
    return HTMLResponse(html)

@app.get("/toggle/{symbol}")
async def toggle_coin(symbol: str):
    symbol = symbol.replace("_", "/")
    cur = await is_coin_enabled(symbol)
    await set_coin_enabled(symbol, 0 if cur else 1)
    return RedirectResponse("/panel")

@app.get("/set/{symbol}/{tf}")
async def confirm(symbol: str, tf: str):
    symbol = symbol.replace("_", "/")
    if tf not in ALL_TFS: return HTMLResponse("<h1 style='color:red; background:#000'>НЕВЕРНЫЙ ТАЙМФРЕЙМ</h1>")
    return HTMLResponse(f"<body style='background:#000;color:#0f0;font-family:monospace;text-align:center;padding-top:15%'><h1>СМЕНИТЬ ТФ {symbol} → {tf}?</h1><br><a href='/do/{symbol.replace('/', '_')}/{tf}' style='background:#0f0;color:#000;padding:20px 60px;font-size:32px;text-decoration:none'>ДА</a> <a href='/panel'>НЕТ</a></body>")

@app.get("/do/{symbol}/{tf}")
async def do_set(symbol: str, tf: str):
    symbol = symbol.replace("_", "/")
    await set_tf_for_coin(symbol, tf)
    return RedirectResponse("/panel")

@app.get("/signals", response_class=HTMLResponse)
async def signals():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT symbol,tf,direction,price,reason,datetime(ts/1000,'unixepoch','localtime') FROM signals ORDER BY ts DESC LIMIT 100") as cur:
            rows = await cur.fetchall()
            
    t = "<table border=1 style='color:#0f0;background:#000;width:95%;margin:auto;font-size:18px;text-align:center'><tr><th>Монета</th><th>ТФ</th><th>Сигнал</th><th>Цена</th><th>Причина</th><th>Время</th></tr>"
    for r in rows:
        color = "#0f0" if r[2] == "LONG" else "#f00"
        t += f"<tr><td>{r[0]}</td><td>{r[1]}</td><td style='color:{color}'><b>{r[2]}</b></td><td>{r[3]:.6f}</td><td>{r[4]}</td><td>{r[5]}</td></tr>"
    t += "</table><br><a href='/panel' style='display:block;margin-top:20px;color:#0f0'>НАЗАД</a>"
    return HTMLResponse(f"<body style='background:#000;color:#0f0;font-family:monospace'>{t}</body>")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
