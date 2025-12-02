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
        
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://bot-fly-oz.fly.dev/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

# ========================= НАСТРОЙКИ =========================
# Более волатильные, высоколиквидные монеты для лучшей работы сканера
ALL_SYMBOLS = ["SOL/USDT", "LINK/USDT", "MATIC/USDT", "DOGE/USDT", "XRP/USDT", "SHIB/USDT"]
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

LAST_SIGNAL = {} # {"LONG_DOGE/USDT_45m": timestamp, ...}

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
        await db.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('close_signals_enabled','1')")
        
        # Обновляем или добавляем настройки для новых монет
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

async def get_close_enabled() -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key='close_signals_enabled'") as cur:
            row = await cur.fetchone()
            return row and row[0] == '1'
        
async def set_close_enabled(enabled: int):
    status = '1' if enabled else '0'
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE settings SET value=? WHERE key='close_signals_enabled'", (status,))
        await db.commit()

# ========================= ОТПРАВКА =========================
async def send_telegram(text: str):
    # Логирование: уведомляем о попытке отправки в Телеграм
    print(f"[LOG] Попытка отправить в Telegram: {text[:50]}...")
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID: 
        print("[WARNING] Пропуск Telegram: Токен или Chat ID не заданы.")
        return
    try:
        async with aiohttp.ClientSession() as session:
            await session.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                               json={"chat_id": int(TELEGRAM_CHAT_ID), "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})
            print("[LOG] Успешно отправлено в Telegram.")
    except Exception as e:
        print(f"[ERROR] Сбой отправки в Telegram: {e}")


async def send_to_oz_webhook(symbol: str, tf: str, direction: str, price: float, reason: str):
    # Логирование: уведомляем о попытке отправки на Webhook
    print(f"[LOG] Попытка отправить на Webhook: {direction} {symbol}")
    if not WEBHOOK_SECRET:
        print("[WARNING] WEBHOOK_SECRET не установлен. Пропуск отправки на бот.")
        return
        
    payload = {
        "symbol": symbol.split('/')[0] + 'USDT',
        "signal": direction.upper(), 
        "timeframe": tf,
        "price": round(price, 8),
        "reason": reason,
        "source": "OZ SCANNER ULTRA PRO 2026 v3.0" # Обновление версии
    }
    
    headers = {
        "X-Webhook-Secret": WEBHOOK_SECRET
    }
    
    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.post(WEBHOOK_URL, json=payload, timeout=10) as response:
                    if response.status != 200:
                        print(f"[ERROR] Webhook не сработал для {symbol}: {response.status} - {await response.text()}")
                    else:
                        print(f"[LOG] Успешно отправлено на Webhook для {symbol}.")
        except Exception as e:
            print(f"[ERROR] Сбой подключения Webhook для {symbol}: {e}")


async def send_signal(symbol, tf, direction, price, reason):
    # Логирование: Подтверждаем, что функция сработала
    print(f"[LOG] >>> СИГНАЛ: {direction} {symbol} {tf} @ {price:.6f} <<<")
    ts = int(datetime.now().timestamp() * 1000)
    
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute("INSERT INTO signals (symbol,tf,direction,price,reason,ts) VALUES (?,?,?,?,?,?)",
                                (symbol, tf, direction, price, reason, ts))
            await db.commit()
            print(f"[LOG] Успешно записано в базу данных ({DB_PATH}).")
    except Exception as db_e:
        print(f"[CRITICAL ERROR] Сбой записи в базу данных: {db_e}")
        return # Останавливаем отправку, если запись в БД не удалась

    text = (f"OZ ULTRA PRO 2026 v3.0\n" # Обновление версии
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
        print(f"[INFO] {symbol} {tf}: Пропуск (отключен в настройках).")
        return
    
    # Логирование: начало проверки
    print(f"[INFO] Начало проверки: {symbol} {tf}")
    
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=tf, limit=500)
        if len(ohlcv) < 300: 
            print(f"[WARNING] {symbol} {tf}: Недостаточно данных ({len(ohlcv)} свечей).")
            return

        df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
        
        # === ДОБАВЛЕНИЕ НОВЫХ ИНДИКАТОРОВ ===
        df['ema34'] = talib.EMA(df['close'], 34)
        df['ema55'] = talib.EMA(df['close'], 55)
        df['ema200'] = talib.EMA(df['close'], 200)
        df['rsi'] = talib.RSI(df['close'], 14)
        df['vol_ma20'] = df['volume'].rolling(20).mean()
        
        # 1. ADX (Средний Индекс Направления) для силы тренда
        df['adx'] = talib.ADX(df['high'], df['low'], df['close'], 14)
        # 2. MFI (Индекс Денежного Потока) для подтверждения импульса
        df['mfi'] = talib.MFI(df['high'], df['low'], df['close'], df['volume'], 14)

        c = df['close'].iloc[-1]
        
        key = f"{symbol}_{tf}"
        now = datetime.now().timestamp()

        # === УЛУЧШЕННОЕ УСЛОВИЕ LONG (Требуется ADX > 25 и MFI фильтр) ===
        long_cond = (
            # Основной тренд (Цена выше EMA55)
            df['close'].iloc[-1] > df['ema55'].iloc[-1] and 
            # Подтверждение кроссовера (EMA34 выше EMA55)
            df['ema34'].iloc[-1] > df['ema55'].iloc[-1] and
            # ФИЛЬТР 1: ADX > 25 (Подтверждение СИЛЫ тренда)
            df['adx'].iloc[-1] > 25 and
            # ФИЛЬТР 2: RSI не перекуплен
            df['rsi'].iloc[-1] < 70 and
            # ФИЛЬТР 3: MFI не перекуплен (деньги все еще заходят, но не в пике)
            df['mfi'].iloc[-1] < 70 and
            # Подтверждение объема
            df['volume'].iloc[-1] > df['vol_ma20'].iloc[-1] * 1.5
        )

        # === УЛУЧШЕННОЕ УСЛОВИЕ CLOSE (Требуется слабость тренда + слом EMA) ===
        close_cond = (
            # Цена опустилась ниже EMA34
            df['close'].iloc[-1] < df['ema34'].iloc[-1] and 
            # EMA34 пересекла EMA55 сверху вниз (Слом тренда)
            df['ema34'].iloc[-1] < df['ema55'].iloc[-1] and
            # ФИЛЬТР: ADX < 20 (Тренд ослаб) ИЛИ MFI < 30 (Денежный поток ушел)
            (df['adx'].iloc[-1] < 20 or df['mfi'].iloc[-1] < 30)
        )


        cd = COOLDOWNS.get(tf, {'long': 3600, 'close': 1800})

        # === ПРОВЕРКА LONG ===
        if long_cond:
            if now - LAST_SIGNAL.get(f"LONG_{key}", 0) > cd['long']:
                LAST_SIGNAL[f"LONG_{key}"] = now
                await send_signal(symbol, tf, "LONG", c, "СИЛЬНЫЙ ТРЕНД (ADX>25) + EMA55 + ОБЪЁМ")
            else:
                print(f"[INFO] {symbol} {tf}: LONG условие выполнено, но сработал КУЛДОУН ({cd['long']}с).")
        # else:
            # print(f"[DEBUG] {symbol} {tf}: LONG условие не выполнено.")


        # === ПРОВЕРКА CLOSE ===
        is_close_enabled = await get_close_enabled()
        
        if close_cond:
            if not is_close_enabled:
                print(f"[WARNING] {symbol} {tf}: CLOSE условие выполнено, но ГЛОБАЛЬНО ОТКЛЮЧЕНО в панели.")
                # Отправляем только в телегу для уведомления, не пишем в БД и не отправляем на Webhook
                await send_telegram(f"<b>CLOSE {symbol}</b> — Игнорируется по настройке админ-панели.")
                return

            if now - LAST_SIGNAL.get(f"CLOSE_{key}", 0) > cd['close']:
                LAST_SIGNAL[f"CLOSE_{key}"] = now
                await send_signal(symbol, tf, "CLOSE", c, "ТРЕНД СЛОМАН (ADX/MFI) — ФИКСИРУЕМ")
            else:
                print(f"[INFO] {symbol} {tf}: CLOSE условие выполнено, но сработал КУЛДОУН ({cd['close']}с).")
        # else:
            # print(f"[DEBUG] {symbol} {tf}: CLOSE условие не выполнено.")

    except Exception as e:
        # Логирование: Вывод ошибки при обработке
        print(f"[ОШИБКА] {symbol} {tf}: {e}")

async def scanner_background():
    ex = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
    await send_telegram("OZ SCANNER ULTRA PRO 2026 v3.0 — ЗАПУЩЕН. УЛУЧШЕННЫЕ ФИЛЬТРЫ.") # Обновление версии
    
    while True:
        # Логирование: Сердцебиение сканера
        current_time = datetime.now().strftime("%H:%M:%S")
        print(f"\n[SCANNER HEARTBEAT] Запуск цикла проверки. Время: {current_time}")
        
        tasks = []
        for s in ALL_SYMBOLS:
            tf = await get_tf_for_coin(s)
            tasks.append(check_pair(ex, s, tf))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
            
        await asyncio.sleep(18)

# ========================= ВЕБ-ПАНЕЛЬ =========================
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    asyncio.create_task(scanner_background())
    yield

app = FastAPI(lifespan=lifespan)

@app.get("/", response_class=HTMLResponse)
async def root():
    return '<html><body style="background:#000;color:#0f0;font-family:monospace;text-align:center;padding-top:15%"><h1>OZ ULTRA PRO 2026 v3.0</h1><form action="/login" method="post"><input type="password" name="password" placeholder="Пароль" style="font-size:24px;padding:12px;width:300px"><br><br><button type="submit" style="font-size:24px;padding:12px 40px">ВОЙТИ</button></form></body></html>'

@app.post("/login")
async def login(password: str = Form(...)):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT value FROM settings WHERE key='password'") as cur:
            row = await cur.fetchone()
            correct_password = row[0] if row else "777"
            
    if password == correct_password:
        return RedirectResponse("/panel", status_code=303)
    return HTMLResponse("<h1 style='color:red; background:#000'>НЕПРАВИЛЬНЫЙ ПАРОЛЬ</h1>")

@app.get("/panel", response_class=HTMLResponse)
async def panel():
    html = "<pre style='color:#0f0;background:#000;font-size:22px;line-height:3;text-align:center'>OZ ULTRA PRO 2026 v3.0 — УПРАВЛЕНИЕ\n\n" # Обновление версии
    
    # Секция управления монетами
    for symbol in ALL_SYMBOLS:
        is_enabled = await is_coin_enabled(symbol)
        enabled_status = "ВКЛ" if is_enabled else "ВЫКЛ"
        color = "#0f0" if is_enabled else "#800"
        current_tf = await get_tf_for_coin(symbol)
        safe = symbol.replace("/", "_")
        
        toggle_label = "ОТКЛЮЧИТЬ" if is_enabled else "ВКЛЮЧИТЬ"
        
        html += f"<b style='color:{color}'>{symbol}</b> — <b>{enabled_status}</b> <a href='/toggle/{safe}'>[{toggle_label}]</a> ТФ: <b>{current_tf}</b>\n"
        for tf in ALL_TFS:
            if tf == current_tf:
                html += f" <u>[{tf}]</u>"
            else:
                html += f" <a href='/set/{safe}/{tf}'>[{tf}]</a>"
        html += "\n\n"
        
    # НОВАЯ СЕКЦИЯ: Глобальное управление CLOSE-сигналами
    is_close_enabled = await get_close_enabled()
    close_status_text = "ВКЛЮЧЕН" if is_close_enabled else "ОТКЛЮЧЕН"
    close_toggle_label = "ОТКЛЮЧИТЬ" if is_close_enabled else "ВКЛЮЧИТЬ"
    close_color = "#0f0" if is_close_enabled else "#f00"
    
    html += f"\n<b>ГЛОБАЛЬНЫЕ НАСТРОЙКИ:</b>\n"
    html += f"Обработка CLOSE: <b style='color:{close_color}'>{close_status_text}</b> <a href='/toggle-close'>[{close_toggle_label}]</a>\n\n"
    
    html += f"<a href='/signals'>СИГНАЛЫ</a>  <a href='/'>ВЫХОД</a></pre>"
    return HTMLResponse(html)

@app.get("/toggle/{symbol}")
async def toggle_coin(symbol: str):
    symbol = symbol.replace("_", "/")
    cur = await is_coin_enabled(symbol)
    await set_coin_enabled(symbol, 0 if cur else 1)
    return RedirectResponse("/panel", status_code=303)

@app.get("/toggle-close")
async def toggle_close():
    cur = await get_close_enabled()
    await set_close_enabled(0 if cur else 1)
    return RedirectResponse("/panel", status_code=303)

@app.get("/set/{symbol}/{tf}")
async def confirm(symbol: str, tf: str):
    symbol = symbol.replace("_", "/")
    if tf not in ALL_TFS: return HTMLResponse("<h1 style='color:red; background:#000'>НЕВЕРНЫЙ ТАЙМФРЕЙМ</h1>")
    return HTMLResponse(f"<body style='background:#000;color:#0f0;font-family:monospace;text-align:center;padding-top:15%'><h1>СМЕНИТЬ ТФ {symbol} → {tf}?</h1><br><a href='/do/{symbol.replace('/', '_')}/{tf}' style='background:#0f0;color:#000;padding:20px 60px;font-size:32px;text-decoration:none'>ДА</a> <a href='/panel'>НЕТ</a></body>")

@app.get("/do/{symbol}/{tf}")
async def do_set(symbol: str, tf: str):
    symbol = symbol.replace("_", "/")
    await set_tf_for_coin(symbol, tf)
    return RedirectResponse("/panel", status_code=303)

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
