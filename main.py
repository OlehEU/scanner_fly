# main.py — OZ Scanner Ultra Pro 2026 v3 | Финальная версия 2025
import ccxt.async_support as ccxt
import asyncio
import pandas as pd
import talib
import aiosqlite
import os
from datetime import datetime
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse
import aiohttp # Используется aiohttp для асинхронных HTTP-запросов (включая вебхуки)
from contextlib import asynccontextmanager

# ========================= КОНФИГУРАЦИЯ БЕЗОПАСНОСТИ И ЭНДПОИНТОВ =========================
# ПРОВЕРКА: СЕКРЕТ И ТОКЕН БОТА ДОЛЖНЫ БЫТЬ ОПРЕДЕЛЕНЫ
required_env = ["TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID", "WEBHOOK_URL", "WEBHOOK_SECRET"]
for v in required_env:
    if not os.getenv(v):
        print(f"ОШИБКА: Не определена переменная окружения {v}. СКАНИРОВАНИЕ НЕВОЗМОЖНО.")
        
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
# Если WEBHOOK_URL не задан, используется значение по умолчанию.
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://bot-fly-oz.fly.dev/webhook") 
# КРИТИЧНО: Секрет для авторизации вебхука. Должен быть установлен через Fly.io secrets!
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET") 

# ========================= НАСТРОЙКИ =========================
# АКТУАЛЬНЫЙ СПИСОК: ETH/USDT, BNB/USDT, DOGE/USDT, XRP/USDT, SOL/USDT, FARTCOIN/USDT
ALL_SYMBOLS = ["ETH/USDT", "BNB/USDT", "DOGE/USDT", "XRP/USDT", "SOL/USDT", "FARTCOIN/USDT"]
# АКТУАЛЬНЫЕ ТФ: Удален '45m'
ALL_TFS = ['1m', '5m', '30m', '1h', '4h']
DB_PATH = "oz_ultra.db"

# Кулдауны под каждый таймфрейм (в секундах)
COOLDOWNS = {
    '1m': {'long': 240, 'close': 180},
    '5m': {'long': 480, 'close': 300},
    '30m': {'long': 1200, 'close': 600},
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
        await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('password','777')") 
        # НОВОЕ: Настройка для глобального включения/выключения сигналов CLOSE
        await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('close_signals_enabled','1')")
        
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

# НОВАЯ ФУНКЦИЯ: Получение статуса сигналов CLOSE
async def get_close_signals_status() -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key='close_signals_enabled'") as cur:
            row = await cur.fetchone()
            # По умолчанию включены, если нет записи
            return row[0] == '1' if row and row[0] in ('0', '1') else True 

# НОВАЯ ФУНКЦИЯ: Переключение статуса сигналов CLOSE
async def toggle_close_signals_status():
    current_status = await get_close_signals_status()
    new_status = '0' if current_status else '1'
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE settings SET value=? WHERE key='close_signals_enabled'", (new_status,))
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
    # Убеждаемся, что секретный ключ установлен, прежде чем отправлять
    if not WEBHOOK_SECRET:
        print("[WARNING] WEBHOOK_SECRET не установлен. Пропуск отправки на бот.")
        return
        
    payload = {
        "symbol": symbol.split('/')[0] + 'USDT', # Отправка в формате DOGEUSDT
        "signal": direction.upper(), 
        "timeframe": tf,
        "price": round(price, 8),
        "reason": reason,
        "source": "OZ SCANNER ULTRA PRO 2026 v2.8"
    }
    
    # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ ДЛЯ 403: Отправка секрета в заголовке X-Webhook-Secret
    headers = {
        "X-Webhook-Secret": WEBHOOK_SECRET
    }
    
    # Используем aiohttp.ClientSession с определенными заголовками
    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.post(WEBHOOK_URL, json=payload, timeout=10) as response:
                 # Проверка статуса ответа для диагностики ошибки 403
                 if response.status != 200:
                    print(f"[ERROR] Webhook failed for {symbol}: {response.status} - {await response.text()}")
                 else:
                    print(f"[INFO] Webhook успешно отправлен для {symbol}.")
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
        atr = df['atr'].iloc[-1] or 0.000001

        key = f"{symbol}_{tf}"
        now = datetime.now().timestamp()

        trend_bull = (
            c > df['ema34'].iloc[-1] > df['ema55'].iloc[-1] > df['ema200'].iloc[-1] and
            df['ema34'].iloc[-1] > df['ema34'].iloc[-3] and
            df['ema55'].iloc[-1] > df['ema55'].iloc[-8]
        )

        long_cond = trend_bull and \
                    40 < rsi < 82 and \
                    vol > vol_avg * (1.7 if tf in ['1h','4h'] else 2.4) and \
                    c > prev and \
                    (c - prev) > atr * 0.4 and \
                    df['low'].iloc[-1] > df['ema34'].iloc[-1] * 0.997

        close_cond = (
            c < df['ema55'].iloc[-1] or
            (c < df['ema34'].iloc[-1] and rsi > 80) or
            (c < prev and (prev - c) > atr * 2.2)
        )

        cd = COOLDOWNS.get(tf, {'long': 3600, 'close': 1800})

        if long_cond and now - LAST_SIGNAL.get(f"LONG_{key}", 0) > cd['long']:
            LAST_SIGNAL[f"LONG_{key}"] = now
            await send_signal(symbol, tf, "LONG", c, "МОЩНЫЙ ТРЕНД + ОБЪЁМ + EMA55")

        # ИСПОЛЬЗУЕМ НОВЫЙ ГЛОБАЛЬНЫЙ ПЕРЕКЛЮЧАТЕЛЬ ДЛЯ CLOSE
        if await get_close_signals_status() and \
           close_cond and now - LAST_SIGNAL.get(f"CLOSE_{key}", 0) > cd['close']:
            LAST_SIGNAL[f"CLOSE_{key}"] = now
            await send_signal(symbol, tf, "CLOSE", c, "ТРЕНД СЛОМАН — ФИКСИРУЕМ")

    except Exception as e:
        print(f"[Ошибка] {symbol} {tf}: {e}")

async def scanner_background():
    # Инициализация ccxt с ограничением скорости
    ex = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
    await send_telegram("OZ SCANNER ULTRA PRO 2026 v2.8 — ЗАПУЩЕН\nКонфигурация: FARTCOIN + 45m + ТЕЛЕГА + ХУК\nК миллиардам!")
    
    while True:
        tasks = []
        for s in ALL_SYMBOLS:
            if await is_coin_enabled(s):
                tf = await get_tf_for_coin(s)
                tasks.append(check_pair(ex, s, tf))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            
        await asyncio.sleep(18)

# ========================= ВЕБ-ПАНЕЛЬ =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Запуск фонового сканера при старте приложения
    asyncio.create_task(scanner_background())
    yield 

# КОРРЕКЦИЯ: Инициализация 'app' должна быть ДО использования декораторов @app.get!
app = FastAPI(lifespan=lifespan)

# НОВЫЙ ЭНДПОИНТ ДЛЯ ПЕРЕКЛЮЧЕНИЯ ГЛОБАЛЬНОГО CLOSE
@app.get("/toggle_close")
async def toggle_close():
    await toggle_close_signals_status()
    return RedirectResponse("/panel")

@app.get("/", response_class=HTMLResponse)
async def root():
    return '<html><body style="background:#000;color:#0f0;font-family:monospace;text-align:center;padding-top:15%"><h1>OZ ULTRA PRO 2026 v2.8</h1><form action="/login" method="post"><input type="password" name="password" placeholder="Пароль" style="font-size:24px;padding:12px;width:300px"><br><br><button type="submit" style="font-size:24px;padding:12px 40px">ВОЙТИ</button></form></body></html>'

@app.post("/login")
async def login(password: str = Form(...)):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key='password'") as cur:
            row = await cur.fetchone()
            correct_password = row[0] if row else "777"
            
    if password == correct_password:
        # Используем 303 Redirect, чтобы предотвратить повторную отправку формы
        return RedirectResponse("/panel", status_code=303)
    return HTMLResponse("<h1 style='color:red; background:#000'>НЕПРАВИЛЬНЫЙ ПАРОЛЬ</h1>")

@app.get("/panel", response_class=HTMLResponse)
async def panel():
    is_close_enabled = await get_close_signals_status()
    close_status_text = "ВКЛЮЧЕНЫ" if is_close_enabled else "ВЫКЛЮЧЕНЫ"
    close_color = "#0f0" if is_close_enabled else "#f00"

    html = "<pre style='color:#0f0;background:#000;font-size:22px;line-height:3;text-align:center'>OZ ULTRA PRO 2026 v2.8 — УПРАВЛЕНИЕ\n\n"
    
    # Глобальный переключатель для CLOSE сигналов: [ПЕРЕКЛЮЧИТЬ]
    html += f"СИГНАЛЫ CLOSE: <b style='color:{close_color}'>{close_status_text}</b> <a href='/toggle_close'>[ПЕРЕКЛЮЧИТЬ]</a>\n\n"

    for symbol in ALL_SYMBOLS:
        is_coin_enabled_status = await is_coin_enabled(symbol)
        enabled_text = "ВКЛЮЧЕНА" if is_coin_enabled_status else "ВЫКЛЮЧЕНА"
        color = "#0f0" if is_coin_enabled_status else "#800"
        current_tf = await get_tf_for_coin(symbol)
        safe = symbol.replace("/", "_")
        
        # Динамическая кнопка: [ВЫКЛЮЧИТЬ] или [ВКЛЮЧИТЬ]
        toggle_action_text = "ВЫКЛЮЧИТЬ" if is_coin_enabled_status else "ВКЛЮЧИТЬ"

        # Обновленный вывод для монет
        html += f"<b style='color:{color}'>{symbol}</b> — <b>{enabled_text}</b> <a href='/toggle/{safe}'>[{toggle_action_text}]</a> ТФ: <b>{current_tf}</b>\n"
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
