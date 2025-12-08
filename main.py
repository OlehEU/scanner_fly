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
# КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ: ЗАМЕНА "SHIB/USDT" НА "1000SHIB/USDT" + Обновление списка мемов
ALL_SYMBOLS = [
    # Мемкоины и токены с высокой точностью (0 знаков после запятой)
    "DOGE/USDT", "1000SHIB/USDT", "1000PEPE/USDT", "1000BONK/USDT", 
    "1000FLOKI/USDT", "1000SATS/USDT", "FARTCOIN/USDT", "PIPPIN/USDT", 
    "BTT/USDT", "MASK/USDT",
    # Основные и Layer 1/2 монеты
    "ETH/USDT", "BNB/USDT", "SOL/USDT", "ADA/USDT", "TRX/USDT", "MATIC/USDT", 
    "DOT/USDT", "ATOM/USDT", "LINK/USDT", "AVAX/USDT", "NEAR/USDT", 
    "XRP/USDT" 
]

# АКТУАЛЬНЫЕ ТФ: Удален '45m'
ALL_TFS = ['1m', '5m', '30m', '1h', '4h']
DB_PATH = "oz_ultra.db"

# Кулдауны под каждый таймфрейм (в секундах)
# ДОБАВЛЕНЫ: 'short' и 'close_short' для контроля частоты шортовых сигналов
COOLDOWNS = {
    '1m': {'long': 240, 'close': 180, 'short': 240, 'close_short': 180},
    '5m': {'long': 480, 'close': 300, 'short': 480, 'close_short': 300},
    '30m': {'long': 1200, 'close': 600, 'short': 1200, 'close_short': 600},
    '1h': {'long': 3600, 'close': 1800, 'short': 3600, 'close_short': 1800},
    '4h': {'long': 10800, 'close': 5400, 'short': 10800, 'close_short': 5400},
}

LAST_SIGNAL = {} # {"LONG_DOGE/USDT_45m": timestamp, ...}

# ========================= 1.5. ЛОГИКА ОКРУГЛЕНИЯ ЦЕНЫ (ИСПРАВЛЕНИЕ ОШИБКИ ТОЧНОСТИ) =========================

def get_rounded_price(price: float) -> float:
    """
    Применяет логику динамического округления к цене, 
    чтобы соответствовать требованиям точности биржи Binance (фьючерсы).
    
    Эта функция устраняет ошибку 'Precision Error' при отправке вебхука, 
    выбирая нужную точность для каждой ценовой категории монеты.

    :param price: Цена монеты, полученная от сканера.
    :return: Округленная цена, готовая для отправки на биржу.
    """
    # 1. Для очень маленьких цен (например, 1000SHIB, 1000SATS)
    if price < 0.05:
        # Высокая точность (до 8 знаков после запятой)
        precision = 8
    # 2. Для цен менее $1 (например, DOGE, ADA, 1000PEPE)
    elif price < 1.0:
        # Средняя точность (до 6 знаков после запятой)
        precision = 6
    # 3. Для цен больше $1 (например, NEAR, SOL, ETH)
    else:
        # Низкая точность (до 3 знаков после запятой)
        precision = 3
    
    # Округляем цену
    return round(price, precision)

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
        
        # ИЗМЕНЕНИЕ: Установка по умолчанию для всех глобальных сигналов на '0' (ВЫКЛЮЧЕНЫ)
        await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('long_entry_enabled','0')")
        await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('short_entry_enabled','0')")
        await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('close_long_enabled','0')")
        await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('close_short_enabled','0')")
        
        # ИЗМЕНЕНИЕ: Установка по умолчанию для каждой монеты на 0 (ВЫКЛЮЧЕНА)
        for s in ALL_SYMBOLS:
            # Используем INSERT OR IGNORE, чтобы не перезаписывать настройки существующих монет,
            # но добавить 1000SHIB/USDT, если его не было.
            await db.execute(
                "INSERT OR IGNORE INTO coin_settings (symbol, tf, enabled) VALUES (?, '1h', 0)",
                (s,)
            )
        await db.commit()

async def is_coin_enabled(symbol: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT enabled FROM coin_settings WHERE symbol=?", (symbol,)) as cur:
            row = await cur.fetchone()
            # ПРОВЕРКА: Если запись существует, возвращаем ее статус (0 или 1). 
            # Если по какой-то причине записи нет, по умолчанию True, чтобы избежать ошибок.
            return bool(row[0]) if row and row[0] in (0, 1) else True 

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

# УНИВЕРСАЛЬНЫЕ ФУНКЦИИ ДЛЯ ПОЛУЧЕНИЯ И ПЕРЕКЛЮЧЕНИЯ НАСТРОЕК
async def get_setting(key: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
            # ПРОВЕРКА: По умолчанию ВЫКЛЮЧЕНЫ ('0') после обновления init_db
            return row[0] == '1' if row and row[0] in ('0', '1') else False 

async def toggle_setting(key: str):
    current_status = await get_setting(key)
    new_status = '0' if current_status else '1'
    async with aiosqlite.connect(DB_PATH) as db:
        # Используем INSERT OR IGNORE на случай, если ключа не было (хотя init_db должен был его добавить)
        await db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, new_status))
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
        
    # КРИТИЧЕСКОЕ ИЗМЕНЕНИЕ: Динамическое округление цены для избежания ошибки Precision Error
    rounded_price = get_rounded_price(price)
    
    payload = {
        "symbol": symbol.split('/')[0] + 'USDT', # Отправка в формате DOGEUSDT
        "signal": direction.upper(), 
        "timeframe": tf,
        "price": rounded_price, # Используем динамически округленную цену
        "reason": reason,
        "source": "OZ SCANNER ULTRA PRO 2026 v3.0"
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
                    print(f"[INFO] Webhook успешно отправлен для {symbol} по цене {rounded_price}.")
        except Exception as e:
            print(f"[ERROR] Webhook connection failed for {symbol}: {e}")


async def send_signal(symbol, tf, direction, price, reason):
    ts = int(datetime.now().timestamp() * 1000)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO signals (symbol,tf,direction,price,reason,ts) VALUES (?,?,?,?,?,?)",
                             (symbol, tf, direction, price, reason, ts))
        await db.commit()

    text = (f"OZ ULTRA PRO 2026 v3.0\n"
            f"<b>{direction.upper()}</b>\n"
            f"<code>{symbol}</code> | <code>{tf}</code>\n"
            f"Цена: <code>{price:.6f}</code>\n"
            f"<b>{reason}</b>\n"
            f"<a href='https://www.tradingview.com/chart/?symbol=BINANCE:{symbol.replace('/', '')}&interval={tf}'>ГРАФИК</a>")

    await send_telegram(text)
    # ПЕРЕДАЕМ ИСХОДНУЮ ЦЕНУ, ОНА БУДЕТ ОКРУГЛЕНА ВНУТРИ send_to_oz_webhook
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

        # --- ТРЕНДОВЫЕ ФИЛЬТРЫ ---
        trend_bull = (
            c > df['ema34'].iloc[-1] > df['ema55'].iloc[-1] > df['ema200'].iloc[-1] and
            df['ema34'].iloc[-1] > df['ema34'].iloc[-3] and
            df['ema55'].iloc[-1] > df['ema55'].iloc[-8]
        )
        
        # НОВОЕ: МЕДВЕЖИЙ ТРЕНД (зеркальное отражение бычьего)
        trend_bear = (
            c < df['ema34'].iloc[-1] < df['ema55'].iloc[-1] < df['ema200'].iloc[-1] and
            df['ema34'].iloc[-1] < df['ema34'].iloc[-3] and
            df['ema55'].iloc[-1] < df['ema55'].iloc[-8]
        )

        # --- УСЛОВИЯ LONG-СИГНАЛОВ ---
        long_cond = trend_bull and \
                     40 < rsi < 82 and \
                     vol > vol_avg * (1.7 if tf in ['1h','4h'] else 2.4) and \
                     c > prev and \
                     (c - prev) > atr * 0.4 and \
                     df['low'].iloc[-1] > df['ema34'].iloc[-1] * 0.997

        # --- УСЛОВИЯ SHORT-СИГНАЛОВ (НОВОЕ) ---
        short_cond = trend_bear and \
                     18 < rsi < 60 and \
                     vol > vol_avg * (1.7 if tf in ['1h','4h'] else 2.4) and \
                     c < prev and \
                     (prev - c) > atr * 0.4 and \
                     df['high'].iloc[-1] < df['ema34'].iloc[-1] * 1.003
        
        # --- УСЛОВИЯ CLOSE-LONG-СИГНАЛОВ ---
        close_long_cond = (
            c < df['ema55'].iloc[-1] or
            (c < df['ema34'].iloc[-1] and rsi > 80) or
            (c < prev and (prev - c) > atr * 2.2)
        )
        
        # --- УСЛОВИЯ CLOSE-SHORT-СИГНАЛОВ (НОВОЕ) ---
        close_short_cond = (
            c > df['ema55'].iloc[-1] or
            (c > df['ema34'].iloc[-1] and rsi < 20) or
            (c > prev and (c - prev) > atr * 2.2)
        )

        cd = COOLDOWNS.get(tf, {'long': 3600, 'close': 1800, 'short': 3600, 'close_short': 1800})

        # 1. LONG ENTRY SIGNAL
        if await get_setting('long_entry_enabled') and long_cond and \
            now - LAST_SIGNAL.get(f"LONG_{key}", 0) > cd['long']:
            LAST_SIGNAL[f"LONG_{key}"] = now
            await send_signal(symbol, tf, "LONG", c, "МОЩНЫЙ ТРЕНД + ОБЪЁМ + EMA55")
            
        # 2. SHORT ENTRY SIGNAL
        if await get_setting('short_entry_enabled') and short_cond and \
            now - LAST_SIGNAL.get(f"SHORT_{key}", 0) > cd['short']:
            LAST_SIGNAL[f"SHORT_{key}"] = now
            await send_signal(symbol, tf, "SHORT", c, "СЛАБЫЙ ТРЕНД + ОБЪЁМ + EMA55")

        # 3. CLOSE LONG SIGNAL
        if await get_setting('close_long_enabled') and close_long_cond and \
            now - LAST_SIGNAL.get(f"CLOSE_LONG_{key}", 0) > cd['close']:
            LAST_SIGNAL[f"CLOSE_LONG_{key}"] = now
            await send_signal(symbol, tf, "CLOSE_LONG", c, "ТРЕНД LONG СЛОМАН — ФИКСИРУЕМ")
            
        # 4. CLOSE SHORT SIGNAL
        if await get_setting('close_short_enabled') and close_short_cond and \
            now - LAST_SIGNAL.get(f"CLOSE_SHORT_{key}", 0) > cd['close_short']:
            LAST_SIGNAL[f"CLOSE_SHORT_{key}"] = now
            await send_signal(symbol, tf, "CLOSE_SHORT", c, "ТРЕНД SHORT СЛОМАН — ФИКСИРУЕМ")

    except Exception as e:
        print(f"[Ошибка] {symbol} {tf}: {e}")

async def scanner_background():
    # Инициализация ccxt с ограничением скорости
    ex = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
    # Обновленное сообщение при запуске
    await send_telegram("OZ SCANNER ULTRA PRO 2026 v3.0 (4x) — ЗАПУЩЕН\nКонфигурация: ВСЕ ПАРЫ + ТЕЛЕГА + ХУК\nК миллиардам!")
    
    while True:
        tasks = []
        for s in ALL_SYMBOLS:
            # ПРОВЕРКА: is_coin_enabled теперь вернет False, если монета отключена в DB.
            if await is_coin_enabled(s):
                tf = await get_tf_for_coin(s)
                tasks.append(check_pair(ex, s, tf))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            
        await asyncio.sleep(18)

# ========================= ВЕБ-ПАНЕЛЬ =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    # При запуске сервера (или деплое) инициализируется БД с ВЫКЛЮЧЕННЫМИ настройками
    await init_db()
    # Запуск фонового сканера при старте приложения
    asyncio.create_task(scanner_background())
    yield 

# КОРРЕКЦИЯ: Инициализация 'app' должна быть ДО использования декораторов @app.get!
app = FastAPI(lifespan=lifespan)

# НОВЫЙ УНИВЕРСАЛЬНЫЙ ЭНДПОИНТ ДЛЯ ПЕРЕКЛЮЧЕНИЯ ГЛОБАЛЬНЫХ НАСТРОЕК
@app.get("/toggle_setting/{key}")
async def toggle_setting_endpoint(key: str):
    if key in ['long_entry_enabled', 'short_entry_enabled', 'close_long_enabled', 'close_short_enabled']:
        await toggle_setting(key)
    return RedirectResponse("/panel")

@app.get("/", response_class=HTMLResponse)
async def root():
    return '<html><body style="background:#000;color:#0f0;font-family:monospace;text-align:center;padding-top:15%"><h1>OZ ULTRA PRO 2026 v3.0 (4x)</h1><form action="/login" method="post"><input type="password" name="password" placeholder="Пароль" style="font-size:24px;padding:12px;width:300px"><br><br><button type="submit" style="font-size:24px;padding:12px 40px">ВОЙТИ</button></form></body></html>'

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
    
    settings_map = {
        'long_entry_enabled': 'СИГНАЛЫ LONG',
        'short_entry_enabled': 'СИГНАЛЫ SHORT',
        'close_long_enabled': 'СИГНАЛЫ CLOSE LONG',
        'close_short_enabled': 'СИГНАЛЫ CLOSE SHORT',
    }

    html = "<pre style='color:#0f0;background:#000;font-size:22px;line-height:1.8;text-align:center'>OZ ULTRA PRO 2026 v3.0 (4x) — УПРАВЛЕНИЕ\n\n"
    
    # БЛОК ГЛОБАЛЬНЫХ ПЕРЕКЛЮЧАТЕЛЕЙ
    html += "<b style='color:#0ff'>--- ГЛОБАЛЬНЫЙ КОНТРОЛЬ СИГНАЛОВ ---</b>\n"
    for key, label in settings_map.items():
        is_enabled = await get_setting(key)
        status_text = "ВКЛЮЧЕНЫ" if is_enabled else "ВЫКЛЮЧЕНЫ"
        color = "#0f0" if is_enabled else "#f00"
        
        html += f"{label}: <b style='color:{color}'>{status_text}</b> <a href='/toggle_setting/{key}'>[ПЕРЕКЛЮЧИТЬ]</a>\n"
    html += "<b style='color:#0ff'>-------------------------------------</b>\n\n"
    
    # БЛОК УПРАВЛЕНИЯ ПАРАМИ
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
        
    html += f"<a href='/signals'>СИГНАЛЫ</a>  <a href='/'>ВЫХОД</a></pre>"
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
        color = "#0f0" if r[2] == "LONG" else "#f00" if r[2] == "SHORT" else "#ccc"
        t += f"<tr><td>{r[0]}</td><td>{r[1]}</td><td style='color:{color}'><b>{r[2]}</b></td><td>{r[3]:.6f}</td><td>{r[4]}</td><td>{r[5]}</td></tr>"
    t += "</table><br><a href='/panel' style='display:block;margin-top:20px;color:#0f0'>НАЗАД</a>"
    return HTMLResponse(f"<body style='background:#000;color:#0f0;font-family:monospace'>{t}</body>")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
