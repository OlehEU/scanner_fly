# main.py — OZ SCANNER ULTRA PRO 2026 | EMA55 + ВКЛ/ВЫКЛ ПО МОНЕТАМ + ТФ
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

app = FastAPI()
START_TIME = datetime.now().timestamp()
LAST_SIGNAL = {}
ALL_SYMBOLS = ["DOGE/USDT", "XRP/USDT", "SOL/USDT"]
ALL_TFS = ['1m', '5m', '30m', '1h', '4h']
DB_PATH = "oz_ultra.db"

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
        await db.execute("INSERT OR REPLACE INTO settings (key,value) VALUES ('password','777')")
        for s in ALL_SYMBOLS:
            await db.execute("INSERT OR IGNORE INTO coin_settings (symbol, tf, enabled) VALUES (?, '1h', 1)", (s,))
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

async def send_telegram(text: str):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id: return
    async with aiohttp.ClientSession() as session:
        await session.post(f"https://api.telegram.org/bot{token}/sendMessage",
                           json={"chat_id": int(chat_id), "text": text, "parse_mode": "HTML", "disable_web_page_preview": True})

async def send_signal(symbol, tf, direction, price, reason):
    ts = int(datetime.now().timestamp() * 1000)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("INSERT INTO signals (symbol,tf,direction,price,reason,ts) VALUES (?,?,?,?,?,?)",
                        (symbol, tf, direction, price, reason, ts))
        await db.commit()
    text = (f"OZ ULTRA PRO 2026\n"
            f"<b>{direction}</b>\n"
            f"<code>{symbol}</code> | <code>{tf}</code>\n"
            f"Цена: <code>{price:.6f}</code>\n"
            f"<b>{reason}</b>\n"
            f"<a href='https://www.tradingview.com/chart/?symbol=BINANCE:{symbol.replace('/', '')}&interval={tf}'>ГРАФИК</a>")
    await send_telegram(text)

async def check_pair(exchange, symbol, tf):
    if not await is_coin_enabled(symbol):
        return
    try:
        if datetime.now().timestamp() - START_TIME < 300: return
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe=tf, limit=500)
        if len(ohlcv) < 300: return
        df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
        
        df['ema34'] = talib.EMA(df['close'], 34)
        df['ema55'] = talib.EMA(df['close'], 55)    # КЛЮЧ К LONG!
        df['ema200'] = talib.EMA(df['close'], 200)
        df['rsi'] = talib.RSI(df['close'], 14)
        df['atr'] = talib.ATR(df['high'], df['low'], df['close'], 14)
        df['vol_ma'] = df['volume'].rolling(20).mean()

        c = df['close'].iloc[-1]
        prev = df['close'].iloc[-2]
        rsi = df['rsi'].iloc[-1]
        vol = df['volume'].iloc[-1]
        vol_avg = df['vol_ma'].iloc[-1]
        atr = df['atr'].iloc[-1]
        key = f"{symbol}_{tf}"
        now = datetime.now().timestamp()

        long_cond = (
            c > df['ema34'].iloc[-1] > df['ema55'].iloc[-1] > df['ema200'].iloc[-1] and
            df['ema34'].iloc[-1] > df['ema34'].iloc[-5] and
            50 < rsi < 70 and 
            vol > vol_avg * 2.0 and
            c > prev and 
            (c - prev) > atr * 0.5 and
            df['low'].iloc[-1] > df['ema34'].iloc[-1]
        )

        close_cond = (
            c < df['ema34'].iloc[-1] or 
            rsi > 78 or 
            (c < prev and (prev - c) > atr * 1.5)
        )

        long_cooldown = 3600 if tf in ['1h','4h'] else 900
        close_cooldown = 1800 if tf in ['1h','4h'] else 600

        if long_cond and now - LAST_SIGNAL.get(f"LONG_{key}", 0) > long_cooldown:
            LAST_SIGNAL[f"LONG_{key}"] = now
            await send_signal(symbol, tf, "LONG", c, "МОЩНЫЙ ТРЕНДОВЫЙ ВХОД")

        if close_cond and now - LAST_SIGNAL.get(f"CLOSE_{key}", 0) > close_cooldown:
            LAST_SIGNAL[f"CLOSE_{key}"] = now
            await send_signal(symbol, tf, "CLOSE", c, "ТРЕНД СЛОМАН — ФИКСИРУЕМ")

    except Exception as e:
        pass

async def scanner_background():
    ex = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})
    await send_telegram("OZ SCANNER ULTRA PRO 2026 — ЗАПУЩЕН\nEMA55 + ВКЛ/ВЫКЛ по монетам\nРазогрев 5 мин...")
    while True:
        tasks = []
        for s in ALL_SYMBOLS:
            if await is_coin_enabled(s):
                tasks.append(check_pair(ex, s, await get_tf_for_coin(s)))
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.sleep(20)

# =================== ВЕБ-ПАНЕЛЬ С КНОПКАМИ ВКЛ/ВЫКЛ ===================
@app.get("/", response_class=HTMLResponse)
async def root():
    return '<html><body style="background:#000;color:#0f0;font-family:monospace;text-align:center;padding-top:15%"><h1>OZ ULTRA PRO 2026</h1><form action="/login" method="post"><input type="password" name="password" placeholder="Пароль" style="font-size:24px;padding:12px;width:300px"><br><br><button type="submit" style="font-size:24px;padding:12px 40px">ВОЙТИ</button></form></body></html>'

@app.post("/login")
async def login(password: str = Form(...)):
    if password == "777":
        return RedirectResponse("/panel", status_code=303)
    return HTMLResponse("<h1 style='color:red'>НЕТ</h1>")

@app.get("/panel", response_class=HTMLResponse)
async def panel():
    html = "<pre style='color:#0f0;background:#000;font-size:22px;line-height:3;text-align:center'>"
    html += "OZ ULTRA PRO 2026 — УПРАВЛЕНИЕ\n\n"
    for symbol in ALL_SYMBOLS:
        enabled = "ВКЛ" if await is_coin_enabled(symbol) else "ВЫКЛ"
        color = "#0f0" if await is_coin_enabled(symbol) else "#800"
        current_tf = await get_tf_for_coin(symbol)
        safe = symbol.replace("/", "_")
        html += f"<b style='color:{color}'>{symbol}</b> — <b>{enabled}</b> <a href='/toggle/{safe}'>[ТОГГЛ]</a>   ТФ: <b>{current_tf}</b>\n"
        for tf in ALL_TFS:
            if tf == current_tf:
                html += f" <u>[{tf}]</u> "
            else:
                html += f" <a href='/set/{safe}/{tf}'>[{tf}]</a> "
        html += "\n\n"
    html += f"<a href='/signals'>СИГНАЛЫ</a>   <a href='/'>ВЫХОД</a></pre>"
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
    if tf not in ALL_TFS: return "Ошибка"
    return HTMLResponse(f"""
    <body style="background:#000;color:#0f0;font-family:monospace;text-align:center;padding-top:15%">
    <h1>СМЕНИТЬ ТФ?</h1>
    <h2>{symbol} → {tf}</h2>
    <br><br>
    <a href="/do/{symbol.replace('/', '_')}/{tf}" style="background:#0f0;color:#000;padding:20px 60px;font-size:32px;text-decoration:none">ДА</a>
    <br><br><a href="/panel">НЕТ</a>
    </body>
    """)

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
    t += "</table><br><a href='/panel'>НАЗАД</a>"
    return HTMLResponse(f"<body style='background:#000;color:#0f0;font-family:monospace'>{t}</body>")

@app.on_event("startup")
async def startup():
    await init_db()
    asyncio.create_task(scanner_background())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
