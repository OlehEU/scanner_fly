# scanner.py — ФИНАЛЬНАЯ ВЕРСИЯ 2026 (отдельный сканер)
import asyncio
import httpx
import ccxt.async_support as ccxt
import pandas as pd
from datetime import datetime
import json
import os

# === НАСТРОЙКИ ===
WEBHOOK = "https://bot-fly-oz.fly.dev/webhook"
SECRET = "supersecret123"
PING_URL = "https://bot-fly-oz.fly.dev/scanner_ping"   # для статуса ОНЛАЙН
STATUS_URL = "https://bot-fly-oz.fly.dev/scanner_status"  # проверяем, включён ли

COINS = ["XRP", "SOL", "ETH", "BTC", "DOGE"]
TIMEFRAME = "5m"
INTERVAL = 30          # проверка каждые 30 сек
PING_INTERVAL = 45     # пинг каждые 45 сек

# Логи сигналов (для /logs в боте)
LOG_FILE = "signal_log.json"

exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

# === Логирование сигнала в файл ===
def log_signal(coin: str, action: str, price: float):
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "date": datetime.now().strftime("%d.%m"),
        "coin": coin,
        "action": action,
        "price": round(price, 6)
    }
    try:
        if os.path.exists(LOG_FILE):
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                logs = json.load(f)
        else:
            logs = []
        logs.append(entry)
        logs = logs[-100:]  # держим только последние 100
        with open(LOG_FILE, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
    except:
        pass  # если не получилось — не страшно

# === Отправка сигнала в основной бот ===
async def send_signal(coin: str, signal: str, extra=None):
    payload = {"secret": SECRET, "signal": signal, "coin": coin}
    if extra:
        payload.update(extra)

    async with httpx.AsyncClient() as client:
        try:
            await client.post(WEBHOOK, json=payload, timeout=10)
            price = (await exchange.fetch_ticker(f"{coin}/USDT"))["last"]
            action = "BUY" if signal == "buy" else "SELL"
            print(f"{datetime.now().strftime('%H:%M:%S')} → {action} {coin} @ {price}")
            log_signal(coin, action, price)
        except Exception as e:
            print(f"Ошибка отправки сигнала {coin}: {e}")

# === Проверка одного коина ===
async def check_coin(coin: str):
    try:
        ohlcv = await exchange.fetch_ohlcv(f"{coin}/USDT", TIMEFRAME, limit=100)
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
        print(f"Ошибка проверки {coin}: {e}")

# === Пинг — чтобы бот знал, что мы живы ===
async def heartbeat():
    async with httpx.AsyncClient() as client:
        while True:
            try:
                await client.post(PING_URL, timeout=10)
            except:
                pass
            await asyncio.sleep(PING_INTERVAL)

# === Основной цикл сканера (с уважением к кнопке ВКЛ/ВЫКЛ) ===
async def scanner_loop():
    async with httpx.AsyncClient() as client:
        while True:
            try:
                # Проверяем, включён ли сканер
                resp = await client.get(STATUS_URL, timeout=10)
                status = resp.json()
                if status.get("enabled", True) and status.get("online", True):
                    await asyncio.gather(*(check_coin(c) for c in COINS))
            except:
                # Если не смогли связаться — всё равно проверяем (на всякий)
                await asyncio.gather(*(check_coin(c) for c in COINS))
            await asyncio.sleep(INTERVAL)

# === ЗАПУСК ===
async def main():
    print("СКАНЕР OZ 2026 ЗАПУЩЕН — ОТДЕЛЬНЫЙ ПРОЕКТ")
    print(f"Мониторим: {', '.join(COINS)} | {TIMEFRAME} | Каждые {INTERVAL}с")
    # Запускаем пинг и сканер параллельно
    await asyncio.gather(heartbeat(), scanner_loop())

if __name__ == "__main__":
    asyncio.run(main())
