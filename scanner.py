# scanner.py — УЛЬТИМАТИВНАЯ ВЕРСИЯ 2026 с управлением ТФ из Telegram
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

# === НАСТРОЙКИ ПО КОИНАМ (меняются из Telegram!) ===
CONFIG_FILE = "scanner_config.json"

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
            return json.load(f)
    except:
        save_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG.copy()

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

CONFIG = load_config()

# === Остальной код без изменений ===
exchange = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})

def log_signal(coin: str, action: str, price: float):
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "date": datetime.now().strftime("%d.%m"),
        "coin": coin,
        "action": action,
        "price": round(price, 6)
    }
    try:
        if os.path.exists("signal_log.json"):
            with open("signal_log.json") as f:
                logs = json.load(f)
        else:
            logs = []
        logs.append(entry)
        logs = logs[-100:]
        with open("signal_log.json", "w") as f:
            json.dump(logs, f, ensure_ascii=False)
    except: pass

async def send_signal(coin: str, signal: str, extra=None):
    payload = {"secret": SECRET, "signal": signal, "coin": coin}
    if extra: payload.update(extra)
    async with httpx.AsyncClient() as c:
        try:
            await c.post(WEBHOOK, json=payload, timeout=10)
            price = (await exchange.fetch_ticker(f"{coin}/USDT"))["last"]
            action = "BUY" if signal == "buy" else "SELL"
            print(f"{datetime.now().strftime('%H:%M:%S')} → {action} {coin} @ {price}")
            log_signal(coin, action, price)
        except Exception as e:
            print(f"Ошибка сигнала {coin}: {e}")

async def check_coin(coin: str):
    cfg = CONFIG.get(coin, DEFAULT_CONFIG[coin])
    try:
        ohlcv = await exchange.fetch_ohlcv(f"{coin}/USDT", cfg["tf"], limit=100)
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
        has_position = positions[0]['contracts'] > 0

        if close > ema and rsi_val > 40 and vol_spike and not has_position:
            await send_signal(coin, "buy", {"tp": 1.5, "sl": 1.0, "trail": 0.5})
        elif close < ema and has_position:
            await send_signal(coin, "close_all")
    except Exception as e:
        print(f"Ошибка {coin}: {e}")

async def heartbeat():
    async with httpx.AsyncClient() as c:
        while True:
            try:
                await c.post(PING_URL, timeout=10)
            except: pass
            await asyncio.sleep(45)

async def scanner_loop():
    async with httpx.AsyncClient() as c:
        while True:
            try:
                resp = await c.get(STATUS_URL, timeout=10)
                if resp.json().get("enabled", True):
                    tasks = [check_coin(coin) for coin in CONFIG.keys()]
                    await asyncio.gather(*tasks)
            except:
                await asyncio.gather(*(check_coin(coin) for coin in CONFIG.keys()))
            await asyncio.sleep(1)  # чтобы не спамить при ошибке

async def main():
    print("СКАНЕР OZ 2026 УЛЬТИМА — ЗАПУЩЕН")
    print("Управление таймфреймами — из Telegram!")
    await asyncio.gather(heartbeat(), scanner_loop())

if __name__ == "__main__":
    asyncio.run(main())
