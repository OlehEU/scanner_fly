# scanner.py — ФИНАЛЬНЫЙ РАБОЧИЙ ВАРИАНТ 2026 (с 45m, управлением ТФ и пингом)
import asyncio
import httpx
import ccxt.async_support as ccxt
import pandas as pd
from datetime import datetime
import json
import os
from fastapi import FastAPI, Request

# === КОНФИГ ===
WEBHOOK = "https://bot-fly-oz.fly.dev/webhook"
SECRET = "supersecret123"
PING_URL = "https://bot-fly-oz.fly.dev/scanner_ping"      # ← ЭТО ВАЖНО!
STATUS_URL = "https://bot-fly-oz.fly.dev/scanner_status"
CONFIG_FILE = "scanner_config.json"

# === ЗАГРУЗКА КОНФИГА ===
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

# === ЛОГИ И СИГНАЛЫ ===
def log_signal(coin,action,price):
    try:
        logs = json.load(open("signal_log.json")) if os.path.exists("signal_log.json") else []
        logs.append({" tincture":datetime.now().strftime("%H:%M:%S"),"date":datetime.now().strftime("%d.%m"),"coin":coin,"action":action,"price":round(price,6)})
        json.dump(logs[-100:],open("signal_log.json","w"),ensure_ascii=False,indent=2)
    except:pass

async def send_signal(coin,signal,extra=None):
    payload = {"secret":SECRET,"signal":signal,"coin":coin}
    if extra: payload.update(extra)
    async with httpx.AsyncClient() as c:
        try:
            await c.post(WEBHOOK,json=payload,timeout=10)
            price = (await exchange.fetch_ticker(f"{coin}/USDT"))["last"]
            print(f"{datetime.now().strftime('%H:%M:%S')} → {signal.upper()} {coin} @ {price}")
            log_signal(coin,signal.upper(),price)
        except Exception as e: print("Ошибка сигнала:",e)

# === ПРОВЕРКА КОИНА ===
async def check_coin(coin):
    tf = CONFIG.get(coin,"5m")
    try:
        ohlcv = await exchange.fetch_ohlcv(f"{coin}/USDT",tf,limit=100)
        df = pd.DataFrame(ohlcv,columns=['ts','o','h','l','c','v'])
        df['ema'] = df['c'].ewm(span=5).mean()
        df['rsi'] = 100 - (100 / (1 + (df['c'].diff().where(lambda x:x>0,0).rolling(7).mean() /
                                   (-df['c'].diff().where(lambda x:x<0,0).rolling(7).mean()))))
        df['vol20'] = df['v'].rolling(20).mean()

        close, ema, rsi_val = df['c'].iloc[-1], df['ema'].iloc[-1], df['rsi'].iloc[-1]
        vol_spike = df['v'].iloc[-1] > df['vol20'].iloc[-1] * 1.5
        pos = await exchange.fetch_positions([f"{coin}/USDT"])
        has_pos = pos and pos[0].get('contracts',0) > 0

        if close > ema and rsi_val > 40 and vol_spike and not has_pos:
            await send_signal(coin,"buy",{"tp":1.5,"sl":1.0,"trail":0.5})
        elif close < ema and has_pos:
            await send_signal(coin,"close_all")
    except Exception as e: print(f"Ошибка {coin} [{tf}]: {e}")

# === ПИНГ И СКАНИРОВАНИЕ ===
async def heartbeat():
    async with httpx.AsyncClient() as c:
        while True:
            try:
                await c.post(PING_URL,timeout=10)
            except: pass
            await asyncio.sleep(45)

async def scanner_loop():
    while True:
        try:
            async with httpx.AsyncClient() as c:
                resp = await c.get(STATUS_URL,timeout=10)
                if resp.json().get("enabled",True):
                    await asyncio.gather(*[check_coin(coin) for coin in CONFIG.keys()])
        except:
            await asyncio.gather(*[check_coin(coin) for coin in CONFIG.keys()])
        await asyncio.sleep(5)

# === FastAPI ===
app = FastAPI()

@app.post("/set_tf")
async def set_tf(req: Request):
    data = await req.json()
    coin = data.get("coin")
    tf = data.get("tf")
    if coin in CONFIG and tf in ["1m","3m","5m","15m","30m","45m","1h"]:
        CONFIG[coin] = tf
        with open(CONFIG_FILE,"w") as f:
            json.dump(CONFIG,f,indent=2)
        return {"ok":True}
    return {"error":"invalid"}

# === ЗАПУСК ===
async def main():
    print("СКАНЕР OZ 2026 — ЗАПУЩЕН И ПИНГУЕТ!")
    await asyncio.gather(heartbeat(), scanner_loop())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
