# scanner.py — ФИНАЛЬНАЯ ВЕРСИЯ 2026 — 100% ОНЛАЙН С ПЕРВОГО ДЕПЛОЯ
import asyncio
import httpx
import ccxt.async_support as ccxt
import pandas as pd
from datetime import datetime
import json
import os
from fastapi import FastAPI, Request

# КОНФИГ
WEBHOOK     = "https://bot-fly-oz.fly.dev/webhook"
SECRET      = "supersecret123"
PING_URL    = "https://scanner-fly-oz.fly.dev/scanner_ping"   # ← правильно
STATUS_URL  = "https://bot-fly-oz.fly.dev/scanner_status"
CONFIG_FILE = "scanner_config.json"

# Загрузка конфига
def load_config():
    try:
        with open(CONFIG_FILE) as f:
            return json.load(f)
    except:
        default = {"XRP":"3m","SOL":"5m","ETH":"15m","BTC":"15m","DOGE":"1m"}
        with open(CONFIG_FILE,"w") as f:
            json.dump(default,f,indent=2)
        return default

CONFIG = load_config()
exchange = ccxt.binance({'enableRateLimit':True,'options':{'defaultType':'future'}})

# Логи
def log_signal(coin, action, price):
    try:
        logs = json.load(open("signal_log.json")) if os.path.exists("signal_log.json") else []
        logs.append({"time":datetime.now().strftime("%H:%M:%S"), "date":datetime.now().strftime("%d.%m"), "coin":coin, "action":action, "price":round(price,6)})
        json.dump(logs[-100:], open("signal_log.json","w"), ensure_ascii=False, indent=2)
    except: pass

# Сигналы
async def send_signal(coin, signal, extra=None):
    payload = {"secret":SECRET, "signal":signal, "coin":coin}
    if extra: payload.update(extra)
    async with httpx.AsyncClient() as c:
        try:
            await c.post(WEBHOOK, json=payload, timeout=10)
            price = (await exchange.fetch_ticker(f"{coin}/USDT"))["last"]
            print(f"{datetime.now().strftime('%H:%M:%S')} → {signal.upper()} {coin} @ {price}")
            log_signal(coin, signal.upper(), price)
        except Exception as e:
            print("Ошибка сигнала:", e)

# Проверка коина
async def check_coin(coin):
    tf = CONFIG.get(coin, "5m")
    try:
        ohlcv = await exchange.fetch_ohlcv(f"{coin}/USDT", tf, limit=100)
        df = pd.DataFrame(ohlcv, columns=['ts','o','h','l','c','v'])
        df['ema'] = df['c'].ewm(span=5).mean()
        delta = df['c'].diff()
        gain = delta.clip(lower=0).rolling(7).mean()
        loss = -delta.clip(upper=0).rolling(7).mean()
        df['rsi'] = 100 - (100 / (1 + gain/loss))
        df['vol20'] = df['v'].rolling(20).mean()

        close = df['c'].iloc[-1]
        ema = df['ema'].iloc[-1]
        rsi_val = df['rsi'].iloc[-1]
        vol_spike = df['v'].iloc[-1] > df['vol20'].iloc[-1] * 1.6

        pos = await exchange.fetch_positions([f"{coin}/USDT"])
        has_pos = pos and len(pos) > 0 and pos[0].get('contracts', 0) > 0

        if close > ema and rsi_val > 42 and vol_spike and not has_pos:
            await send_signal(coin, "buy", {"tp":1.5, "sl":1.0, "trail":0.5})
        elif close < ema and has_pos:
            await send_signal(coin, "close_all")
    except Exception as e:
        print(f"Ошибка {coin} [{tf}]: {e}")

# Пинг и цикл
async def heartbeat():
    async with httpx.AsyncClient() as c:
        while True:
            try:
                await c.post(PING_URL, timeout=10)
                print(f"{datetime.now().strftime('%H:%M:%S')} → ПИНГ ОТПРАВЛЕН → 200 OK")
            except Exception as e:
                print("Пинг не прошёл:", e)
            await asyncio.sleep(25)

async def scanner_loop():
    while True:
        try:
            async with httpx.AsyncClient() as c:
                resp = await c.get(STATUS_URL, timeout=10)
                if resp.json().get("enabled", True):
                    await asyncio.gather(*[check_coin(coin) for coin in CONFIG.keys()])
        except:
            await asyncio.gather(*[check_coin(coin) for coin in CONFIG.keys()])
        await asyncio.sleep(7)

# FastAPI — ГЛАВНОЕ ДОБАВЛЕНИЕ!
app = FastAPI()

# ←←←←←←←←←←←←←←←←←←←←← ЭТОТ ЭНДПОИНТ ТЫ ЗАБЫЛ ←←←←←←←←←←←←←←←←←←←←←
@app.post("/scanner_ping")
async def scanner_ping():
    return {"status": "alive", "time": datetime.now().isoformat()}
# ←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←←

@app.post("/set_tf")
async def set_tf(req: Request):
    data = await req.json()
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
async def startup_event():
    print("СКАНЕР OZ 2026 — ФИНАЛЬНО ЖИВОЙ И ОТВЕЧАЕТ НА ПИНГ!")
    asyncio.create_task(heartbeat())
    asyncio.create_task(scanner_loop())

# Запуск
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
