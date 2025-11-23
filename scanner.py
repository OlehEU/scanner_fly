# scanner.py — OZ 2026 ULTIMATE PATCHED FLY-READY
# Требует: BINANCE_API_KEY, BINANCE_API_SECRET, WEBHOOK, WEBHOOK_SECRET
# Устанавливай переменные окружения в Fly secrets

import asyncio
import os
import json
import logging
import tempfile
from datetime import datetime
from typing import Optional, Dict, Any, List
import pandas as pd
import httpx
import ccxt.async_support as ccxt
from fastapi import FastAPI, Request, HTTPException
from contextlib import asynccontextmanager

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("scanner")

# ======= Конфиг =======
WEBHOOK = os.getenv("WEBHOOK", "https://bot-fly-oz.fly.dev/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "supersecret123")
STATUS_URL = os.getenv("STATUS_URL", "https://bot-fly-oz.fly.dev/scanner_status")
PING_URL = os.getenv("PING_URL", "https://bot-fly-oz.fly.dev/scanner_ping")
CONFIG_FILE = os.getenv("CONFIG_FILE", "scanner_config.json")
SIGNAL_LOG = os.getenv("SIGNAL_LOG", "signal_log.json")

# ======= Глобальные объекты =======
CONFIG: Dict[str, str] = {}
http_client: Optional[httpx.AsyncClient] = None
ccxt_exchange: Optional[ccxt.binance] = None
config_lock = asyncio.Lock()
log_lock = asyncio.Lock()
sem = asyncio.Semaphore(4)  # limit concurrent check_coin
background_tasks: List[asyncio.Task] = []

# ======= Helper: atomic write =======
def atomic_write_json(path: str, data: Any):
    dirpath = os.path.dirname(path) or "."
    with tempfile.NamedTemporaryFile("w", dir=dirpath, delete=False, encoding="utf-8") as tf:
        json.dump(data, tf, ensure_ascii=False, indent=2)
        tmp = tf.name
    os.replace(tmp, path)

# ======= Load config =======
def load_config() -> Dict[str, str]:
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        default = {"XRP":"3m","SOL":"5m","ETH":"15m","BTC":"15m","DOGE":"1m"}
        atomic_write_json(CONFIG_FILE, default)
        return default
    except Exception:
        log.exception("Не удалось загрузить конфиг, используем дефолт")
        return {}

CONFIG = load_config()

# ======= Signal log =======
async def log_signal(coin: str, action: str, price: float):
    entry = {
        "time": datetime.utcnow().strftime("%H:%M:%S"),
        "date": datetime.utcnow().strftime("%d.%m"),
        "coin": coin,
        "action": action,
        "price": round(price, 8)
    }
    async with log_lock:
        try:
            logs = []
            if os.path.exists(SIGNAL_LOG):
                with open(SIGNAL_LOG, "r", encoding="utf-8") as f:
                    logs = json.load(f)
            logs.append(entry)
            atomic_write_json(SIGNAL_LOG, logs[-200:])
        except Exception:
            log.exception("Ошибка логирования сигнала")

# ======= Send signal (webhook) =======
async def send_signal(coin: str, signal: str, extra: Optional[Dict] = None):
    global http_client, ccxt_exchange
    payload = {"signal": signal, "coin": coin}
    if extra:
        payload.update(extra)
    headers = {"Authorization": f"Bearer {WEBHOOK_SECRET}"}
    backoff = [0, 1, 2]
    for wait in backoff:
        try:
            r = await http_client.post(WEBHOOK, json=payload, headers=headers, timeout=10)
            if r.status_code >= 400:
                log.error("Webhook returned %s: %s", r.status_code, r.text)
                if wait:
                    await asyncio.sleep(wait)
                continue
            # fetch price AFTER webhook accepted
            try:
                ticker = await ccxt_exchange.fetch_ticker(f"{coin}/USDT")
                price = ticker.get("last", None)
                if price:
                    log.info("%s %s @ %s", signal.upper(), coin, price)
                    await log_signal(coin, signal.upper(), price)
            except Exception:
                log.exception("Не удалось получить цену после сигнала")
            return
        except Exception:
            log.exception("Ошибка отправки webhook, retrying")
            if wait:
                await asyncio.sleep(wait)
    log.error("Не удалось отправить сигнал %s %s после попыток", signal, coin)

# ======= Data processing =======
def build_indicators(ohlcv: List[List[float]]):
    df = pd.DataFrame(ohlcv, columns=['ts','open','high','low','close','volume'])
    df['ema5'] = df['close'].ewm(span=5, adjust=False).mean()
    delta = df['close'].diff()
    gain = delta.clip(lower=0).rolling(7).mean()
    loss = -delta.clip(upper=0).rolling(7).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    df['vol_ma20'] = df['volume'].rolling(20).mean()
    return df

# ======= Check positions =======
async def has_open_position_for(symbol: str) -> bool:
    global ccxt_exchange
    try:
        positions = await ccxt_exchange.fetch_positions([f"{symbol}/USDT"])
    except Exception:
        log.exception("fetch_positions failed")
        return False
    if not positions:
        return False
    pos = positions[0]
    for key in ("contracts","contract","size","positionAmt","amount"):
        val = pos.get(key) if isinstance(pos, dict) else None
        if val is None:
            continue
        try:
            if float(val) != 0:
                return True
        except Exception:
            continue
    info = pos.get("info") if isinstance(pos, dict) else None
    if isinstance(info, dict):
        for k in ("positionAmt","contracts","amount"):
            try:
                if float(info.get(k,0)) != 0:
                    return True
            except Exception:
                pass
    return False

# ======= Check coin =======
async def check_coin(coin: str):
    global CONFIG
    tf = CONFIG.get(coin, "5m")
    async with sem:
        try:
            ohlcv = await ccxt_exchange.fetch_ohlcv(f"{coin}/USDT", tf, limit=120)
            if not ohlcv or len(ohlcv) < 30:
                log.warning("Недостаточно ohlcv для %s (%s bars)", coin, len(ohlcv) if ohlcv else 0)
                return
            loop = asyncio.get_running_loop()
            df = await loop.run_in_executor(None, build_indicators, ohlcv)
            if df['rsi'].isna().all():
                log.warning("RSI пустой для %s", coin)
                return
            close = float(df['close'].iloc[-1])
            ema5 = float(df['ema5'].iloc[-1])
            rsi = float(df['rsi'].iloc[-1])
            vol_spike = float(df['volume'].iloc[-1]) > float(df['vol_ma20'].iloc[-1])*1.65 if not pd.isna(df['vol_ma20'].iloc[-1]) else False
            has_position = await has_open_position_for(coin)
            if close > ema5 and rsi > 42 and vol_spike and not has_position:
                await send_signal(coin, "buy", {"tp":1.5,"sl":1.0,"trail":0.5})
            elif close < ema5 and has_position:
                await send_signal(coin, "close_all")
        except Exception:
            log.exception("Ошибка в check_coin %s", coin)

# ======= Heartbeat =======
async def heartbeat():
    global http_client
    backoff = [0,1,2,5]
    while True:
        try:
            headers = {"Authorization": f"Bearer {WEBHOOK_SECRET}"}
            r = await http_client.post(PING_URL, headers=headers, timeout=10)
            if r.status_code == 200:
                log.info("%s → ПИНГ → 200 OK", datetime.utcnow().strftime("%H:%M:%S"))
            else:
                log.warning("ПИНГ вернул %s", r.status_code)
        except Exception:
            log.exception("Пинг не прошёл")
        await asyncio.sleep(25)

# ======= Scanner loop =======
async def scanner_loop():
    global CONFIG, http_client
    while True:
        try:
            try:
                resp = await http_client.get(STATUS_URL, timeout=8)
                enabled = resp.json().get("enabled", True)
            except Exception:
                enabled = True
            if enabled:
                tasks = [asyncio.create_task(check_coin(coin)) for coin in list(CONFIG.keys())]
                await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(8)
        except Exception:
            log.exception("Главный цикл сканера упал, продолжаем")
            await asyncio.sleep(5)

# ======= FastAPI lifespan =======
@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client, ccxt_exchange, background_tasks
    http_client = httpx.AsyncClient(timeout=15.0)
    ccxt_exchange = ccxt.binance({
        'apiKey': os.getenv('BINANCE_API_KEY'),
        'secret': os.getenv('BINANCE_API_SECRET'),
        'enableRateLimit': True,
        'options': {'defaultType': 'future'},
        'timeout': 15000,
    })
    background_tasks = [
        asyncio.create_task(heartbeat()),
        asyncio.create_task(scanner_loop())
    ]
    yield
    for task in background_tasks:
        task.cancel()
    await asyncio.gather(*background_tasks, return_exceptions=True)
    await http_client.aclose()
    await ccxt_exchange.close()

app = FastAPI(title="OZ 2026 Scanner (patched)", lifespan=lifespan)

@app.post("/scanner_ping")
async def ping(request: Request):
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {WEBHOOK_SECRET}":
        raise HTTPException(status_code=403, detail="forbidden")
    return {"status": "alive", "ts": datetime.utcnow().isoformat()}

@app.post("/set_tf")
async def set_tf(request: Request):
    auth = request.headers.get("X-Scanner-Secret") or request.headers.get("Authorization")
    if auth not in (f"Bearer {WEBHOOK_SECRET}", os.getenv("X_SCANNER_SECRET", f"Bearer {WEBHOOK_SECRET}")):
        raise HTTPException(status_code=401, detail="unauthorized")
    data = await request.json()
    coin = data.get("coin")
    tf = data.get("tf")
    allowed = {"1m","3m","5m","15m","30m","45m","1h"}
    if coin and tf and coin in CONFIG and tf in allowed:
        async with config_lock:
            CONFIG[coin] = tf
            atomic_write_json(CONFIG_FILE, CONFIG)
        log.info("Таймфрейм %s -> %s", coin, tf)
        return {"ok": True}
    raise HTTPException(status_code=400, detail="invalid")
