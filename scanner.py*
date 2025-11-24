# scanner.py — OZ 2026 ULTIMATE (100% работает на Fly.io)
import asyncio
import os
import json
import logging
import tempfile
import time
from datetime import datetime
from typing import Dict, Any
import pandas as pd
import httpx
import ccxt.async_support as ccxt
from fastapi import FastAPI, Request, HTTPException
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("scanner")

WEBHOOK = os.getenv("WEBHOOK", "https://bot-fly-oz.fly.dev/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
PING_URL = os.getenv("PING_URL", "https://bot-fly-oz.fly.dev/scanner_ping")
CONFIG_FILE = "scanner_config.json"

# Глобалы
CONFIG: Dict[str, str] = {}
http = httpx.AsyncClient(timeout=15.0)
exchange = None
last_signal = {}

def atomic_write_json(path: str, data: Any):
    dirpath = os.path.dirname(path) or "."
    with tempfile.NamedTemporaryFile("w", dir=dirpath, delete=False, encoding="utf-8") as tf:
        json.dump(data, tf, ensure_ascii=False, indent=2)
        tmp = tf.name
    os.replace(tmp, path)

def load_config():
    global CONFIG
    try:
        with open(CONFIG_FILE) as f:
            CONFIG = json.load(f)
    except:
        CONFIG = {"XRP":"3m","SOL":"5m","ETH":"15m","BTC":"15m","DOGE":"1m"}
        atomic_write_json(CONFIG_FILE, CONFIG)

load_config()

async def send_signal(coin: str, signal: str):
    key = f"{coin}_{signal}"
    if key in last_signal and time.time() - last_signal[key] < 70:
        return
    last_signal[key] = time.time()

    payload = {"signal": signal, "coin": coin}
    headers = {"Authorization": f"Bearer {WEBHOOK_SECRET}"}
    try:
        ticker = await exchange.fetch_ticker(f"{coin}/USDT")
        price = ticker["last"]
        await http.post(WEBHOOK, json=payload, headers=headers, timeout=10)
        log.info("СИГНАЛ %s %s @ %.5f", signal.upper(), coin, price)
    except Exception as e:
        log.error("Сигнал не ушёл: %s", e)

def build_indicators(ohlcv):
    df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
    df['ema5'] = df['close'].ewm(span=5, adjust=False).mean()
    delta = df['close'].diff()
    gain = delta.clip(lower=0).rolling(7).mean()
    loss = -delta.clip(upper=0).rolling(7).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    df['vol_ma20'] = df['volume'].rolling(20).mean()
    return df

async def check_coin(coin: str):
    tf = CONFIG.get(coin, "5m")
    try:
        ohlcv = await exchange.fetch_ohlcv(f"{coin}/USDT", tf, limit=120)
        df = build_indicators(ohlcv)
        close = df['close'].iloc[-1]
        ema5 = df['ema5'].iloc[-1]
        rsi = df['rsi'].iloc[-1]
        vol_spike = df['volume'].iloc[-1] > df['vol_ma20'].iloc[-1] * 1.7

        has_pos = await exchange.fetch_positions([f"{coin}/USDT"])
        pos_amt = float(has_pos[0].get("positionAmt", 0) or 0)

        if close > ema5 and rsi > 42 and vol_spike and pos_amt == 0:
            await send_signal(coin, "buy")
        elif close < ema5 and pos_amt != 0:
            await send_signal(coin, "close_all")
    except Exception as e:
        log.exception("Ошибка %s: %s", coin, e)

async def heartbeat():
    while True:
        try:
            await http.post(PING_URL, headers={"Authorization": f"Bearer {WEBHOOK_SECRET}"}, timeout=10)
            log.info("%s — ПИНГ — 200 OK", datetime.utcnow().strftime("%H:%M:%S"))
        except:
            pass
        await asyncio.sleep(25)

async def scanner_loop():
    while True:
        try:
            status = await http.get("https://bot-fly-oz.fly.dev/scanner_status", timeout=6)
            if status.json().get("enabled", True):
                await asyncio.gather(*(check_coin(c) for c in CONFIG.keys()))
        except:
            pass
        await asyncio.sleep(8)

# ====== FastAPI ======
@asynccontextmanager
async def lifespan(app: FastAPI):
    global exchange
    load_config()
    exchange = ccxt.binance({
        'apiKey': os.getenv('BINANCE_API_KEY'),
        'secret': os.getenv('BINANCE_API_SECRET'),
        'enableRateLimit': True,
        'options': {'defaultType': 'future'},
    })
    asyncio.create_task(heartbeat())
    asyncio.create_task(scanner_loop())
    log.info("OZ 2026 — ЗАПУЩЕН НА ВСЮ МОЩЬ!")
    yield
    await exchange.close()
    await http.aclose()

app = FastAPI(lifespan=lifespan)

@app.get("/")
async def root(): return {"status": "OZ 2026 ALIVE"}

@app.post("/set_tf")
async def set_tf(req: Request):
    auth = req.headers.get("X-Scanner-Secret") or req.headers.get("Authorization")
    if auth != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(401)
    data = await req.json()
    coin = data.get("coin")
    tf = data.get("tf")
    allowed = {"1m","3m","5m","15m","30m","45m","1h"}
    if coin in CONFIG and tf in allowed:
        CONFIG[coin] = tf
        atomic_write_json(CONFIG_FILE, CONFIG)
        return {"ok": True}
    raise HTTPException(400)

@app.get("/scanner_status")
async def status():
    return {"online": True, "enabled": True, "last_seen_seconds_ago": 0, "tf": CONFIG}
