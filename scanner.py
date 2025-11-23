# scanner.py — УЛЬТИМАТИВНАЯ ВЕРСИЯ 2026 + 45m + все таймфреймы + управление из ТГ
import asyncio
import httpx
import ccxt.async_support as ccxt
import pandas as pd
from datetime import datetime
import json
import os

WEBHOOK = "https://bot-fly-oz.fly.dev/webhook"
SECRET = "supersecret123"
PING_URL = "https://bot-fly-oz.fly.dev/scanner_ping"
STATUS_URL = "https://bot-fly-oz.fly.dev/scanner_status"
CONFIG_FILE = "scanner_config.json"

# Дефолтные настройки (можно менять из Telegram)
DEFAULT_CONFIG = {
    "XRP":  {"tf": "3m",  "interval": 25},
    "SOL":  {"tf": "5m",  "interval": 30},
    "ETH":  {"tf": "15m", "interval": 60},
    "BTC":  {"tf": "15m", "interval": 60},
    "DOGE": {"tf": "1m",  "interval": 15},
}

def load_config():
    try:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        # Проверяем, что все коины есть и tf валидный
        for coin in DEFAULT_CONFIG:
            if coin not in data:
                data[coin] = DEFAULT_CONFIG[coin].copy()
            elif data[coin]["tf"] not in ["1m","3m","5m","15m","30m","45m","1h"]:
                data[coin]["tf"] = DEFAULT_CONFIG[coin]["tf"]
        return data
    except:
        save_config(DEFAULT_CONFIG.copy())
        return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

CONFIG = load_config()
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

def log_signal(coin: str, action: str, price: float):
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "date": datetime.now().strftime("%d.%m"),
        "coin": coin,
        "action": action,
        "price": round(price, 6)
    }
    try:
        logs = json.load(open("signal_log.json")) if os.path.exists("signal_log.json") else []
        logs.append(entry)
        json.dump(logs[-100:], open("signal_log.json", "w"), ensure_ascii=False, indent=2)
    except: pass

async def send_signal(coin: str, signal: str, extra=None):
    payload = {"secret": SECRET, "signal": signal, "coin": coin}
    if extra: payload.update(extra)
    async with httpx.AsyncClient() as client:
        try:
            await client.post(WEBHOOK, json=payload, timeout=10)
            price = (await exchange.fetch_ticker(f"{coin}/USDT"))["last"]
            action = "BUY" if signal == "buy" else "SELL"
            print(f"{datetime.now().strftime('%H:%M:%S')} → {action} {coin} @ {price}")
            log_signal(coin, action, price)
        except Exception as e:
            print(f"Ошибка отправки сигнала {coin}: {e}")

async def check_coin(coin: str):
    cfg = CONFIG.get(coin, DEFAULT_CONFIG[coin])
    tf = cfg["tf"]
    try:
        ohlcv = await exchange.fetch_ohlcv(f"{coin}/USDT", timeframe=tf, limit=100)
        df = pd.DataFrame(ohlcv, columns=['ts','o','h','l','c','v'])
        df['ema'] = df['c'].ewm(span=5).mean()
        delta = df['c'].diff()
        gain = delta.where(delta > 0, 0).rolling(7).mean()
        loss = -delta.where(delta < 0, 0).rolling(7).mean()
        df['rsi'] = 100 - (100 / (1 + gain/loss))
        df['vol20'] = df['v'].rolling(20).mean()

        close = df['c'].iloc[-1]
        ema = df['ema'].iloc[-1]
        rsi_val = df['rsi'].iloc[-1]
        vol_spike = df['v'].iloc[-1] > df['vol20'].iloc[-1] * 1.5

        positions = await exchange.fetch_positions([f"{coin}/USDT"])
        has_position = positions and positions[0].get('contracts', 0) > 0

        if close > ema and rsi_val > 40 and vol_spike and not has_position:
            await send_signal(coin, "buy", {"tp": 1.5, "sl": 1.0, "trail": 0.5})
        elif close < ema and has_position:
            await send_signal(coin, "close_all")
    except Exception as e:
        print(f"Ошибка {coin} [{tf}]: {e}")

async def heartbeat():
    async with httpx.AsyncClient() as c:
        while True:
            try:
                await c.post(PING_URL, timeout=10)
            except:
                pass
            await asyncio.sleep(45)

async def scanner_loop():
    while True:
        try:
            async with httpx.AsyncClient() as c:
                resp = await c.get(STATUS_URL, timeout=10)
                if resp.json().get("enabled", True):
                    tasks = []
                    for coin in CONFIG.keys():
                        tasks.append(check_coin(coin))
                        await asyncio.sleep(CONFIG[coin]["interval"])  # индивидуальный интервал
                    await asyncio.gather(*tasks)
        except:
            # Если нет связи — всё равно проверяем (на всякий случай)
            await asyncio.gather(*[check_coin(coin) for coin in CONFIG.keys()])
        await asyncio.sleep(3)

# === НОВАЯ ФУНКЦИЯ: изменение таймфрейма из Telegram ===
async def set_timeframe(coin: str, tf: str):
    if tf not in ["1m","3m","5m","15m","30m","45m","1h"]:
        return False
    if coin not in CONFIG:
        return False
    CONFIG[coin]["tf"] = tf
    save_config(CONFIG)
    print(f"Таймфрейм {coin} изменён на {tf}")
    return True

# === FastAPI роут для бота (обязательно!) ===
from fastapi import FastAPI, Request

app = FastAPI()

@app.post("/set_tf")
async def api_set_tf(req: Request):
    try:
        data = await req.json()
        coin = data.get("coin")
        tf = data.get("tf")
        if await set_timeframe(coin, tf):
            return {"status": "ok", "coin": coin, "tf": tf}
        else:
            return {"status": "error", "message": "invalid coin or tf"}
    except:
        return {"status": "error"}

# === Запуск ===
async def main():
    print("СКАНЕР OZ 2026 УЛЬТИМА — ЗАПУЩЕН")
    print("Поддержка: 1m, 3m, 5m, 15m, 30m, 45m, 1h")
    print("Управление таймфреймами — из Telegram!")
    await asyncio.gather(heartbeat(), scanner_loop())

if __name__ == "__main__":
    asyncio.run(main())
