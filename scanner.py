# scanner.py — OZ 2026 ULTIMATE FINAL (исправлена опечатка в default config)
import asyncio
import os
import json
from datetime import datetime

import httpx
import ccxt.async_support as ccxt
import pandas as pd
from fastapi import FastAPI, Request

# ====================== КОНФИГ ======================
WEBHOOK     = "https://bot-fly-oz.fly.dev/webhook"
SECRET      = "supersecret123"
PING_URL    = "https://scanner-fly-oz.fly.dev/scanner_ping"
STATUS_URL  = "https://bot-fly-oz.fly.dev/scanner_status"
CONFIG_FILE = "scanner_config.json"

# ====================== ЗАГРУЗКА КОНФИГА ======================
def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        default = {
            "XRP": "3m",
            "SOL": "5m",
            "ETH": "15m",
            "BTC": "15m",
            "DOGE": "1m"
        }
        with open(CONFIG_FILE, "w") as f:
            json.dump(default, f, indent=2)
        return default

CONFIG = load_config()

# ====================== БИРЖА С КЛЮЧАМИ ИЗ SECRETS ======================
exchange = ccxt.binance({
    'apiKey': os.getenv('BINANCE_API_KEY'),
    'secret': os.getenv('BINANCE_API_SECRET'),
    'enableRateLimit': True,
    'options': {'defaultType': 'future'},
    'timeout': 15000,
})

# ====================== ЛОГИ СИГНАЛОВ ======================
def log_signal(coin: str, action: str, price: float):
    try:
        path = "signal_log.json"
        logs = json.load(open(path)) if os.path.exists(path) else []
        logs.append({
            "time": datetime.now().strftime("%H:%M:%S"),
            "date": datetime.now().strftime("%d.%m"),
            "coin": coin,
            "action": action,
            "price": round(price, 8)
        })
        json.dump(logs[-200:], open(path, "w"), ensure_ascii=False, indent=2)
    except Exception:
        pass

# ====================== ОТПРАВКА СИГНАЛА ======================
async def send_signal(coin: str, signal: str, extra: dict | None = None):
    payload = {"secret": SECRET, "signal": signal, "coin": coin}
    if extra:
        payload.update(extra)
    async with httpx.AsyncClient() as client:
        try:
            await client.post(WEBHOOK, json=payload, timeout=10)
            ticker = await exchange.fetch_ticker(f"{coin}/USDT")
            price = ticker["last"]
            print(f"{datetime.now():%H:%M:%S} → {signal.upper()} {coin} @ {price}")
            log_signal(coin, signal.upper(), price)
        except Exception as e:
            print("Ошибка сигнала:", e)

# ====================== СКАНИРОВАНИЕ ======================
async def check_coin(coin: str):
    tf = CONFIG.get(coin, "5m")
    try:
        ohlcv = await exchange.fetch_ohlcv(f"{coin}/USDT", tf, limit=120)
        df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
        df['ema5'] = df['close'].ewm(span=5, adjust=False).mean()
        delta = df['close'].diff()
        gain = delta.clip(lower=0).rolling(7).mean()
        loss = -delta.clip(upper=0).rolling(7).mean()
        rs = gain / loss
        df['rsi'] = 100 - (100 / (1 + rs))
        df['vol_ma20'] = df['volume'].rolling(20).mean()

        close = df['close'].iloc[-1]
        ema5 = df['ema5'].iloc[-1]
        rsi = df['rsi'].iloc[-1]
        vol_spike = df['volume'].iloc[-1] > df['vol_ma20'].iloc[-1] * 1.65

        positions = await exchange.fetch_positions([f"{coin}/USDT"])
        has_position = bool(positions and positions[0].get('contracts', 0) > 0)

        if close > ema5 and rsi > 42 and vol_spike and not has_position:
            await send_signal(coin, "buy", {"tp": 1.5, "sl": 1.0, "trail": 0.5})
        elif close < ema5 and has_position:
            await send_signal(coin, "close_all")

    except Exception as e:
        print(f"Ошибка {coin} [{tf}]: {e}")

# ====================== ПИНГ И ЦИКЛ ======================
async def heartbeat():
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await client.post(PING_URL, timeout=10)
                print(f"{datetime.now():%H:%M:%S} → ПИНГ → 200 OK")
            except Exception as e:
                print("Пинг не прошёл:", e)
            await asyncio.sleep(25)

async def scanner_loop():
    while True:
        try:
            async with httpx.AsyncClient() as c:
                r = await c.get(STATUS_URL, timeout=8)
                enabled = r.json().get("enabled", True)
        except:
            enabled = True

        if enabled:
            await asyncio.gather(*(check_coin(coin) for coin in CONFIG.keys()))

        await asyncio.sleep(8)

# ====================== FASTAPI ======================
app = FastAPI(title="OZ 2026 Scanner")

@app.post("/scanner_ping")
async def ping():
    return {"status": "alive", "ts": datetime.utcnow().isoformat()}

@app.post("/set_tf")
async def set_tf(request: Request):
    data = await request.json()
    coin = data.get("coin")
    tf = data.get("tf")
    if coin in CONFIG and tf in ["1m","3m","5m","15m","30m","45m","1h"]:
        CONFIG[coin] = tf
        with open(CONFIG_FILE, "w") as f:
            json.dump(CONFIG, f, indent=2)
        print(f"Таймфрейм {coin} → {tf}")
        return {"ok": True}
    return {"error": "invalid"}

@app.on_event("startup")
async def startup():
    print("OZ 2026 — ЗАПУЩЕН НА ВСЮ МОЩЬ!")
    asyncio.create_task(heartbeat())
    asyncio.create_task(scanner_loop())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("scanner:app", host="0.0.0.0", port=8080, reload=False)
