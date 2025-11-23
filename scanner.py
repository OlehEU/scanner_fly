# scanner.py — отдельный сканер 2026
import asyncio
import httpx
import ccxt.async_support as ccxt
import pandas as pd
from datetime import datetime

WEBHOOK = "https://bot-fly-oz.fly.dev/webhook"  # твой основной бот
SECRET = "supersecret123"

COINS = ["XRP", "SOL", "ETH", "BTC", "DOGE"]
TIMEFRAME = "5m"
INTERVAL = 30

exchange = ccxt.binance({'enableRateLimit': True, 'options': {'defaultType': 'future'}})

def rsi(s, p=7): 
    d = s.diff()
    g = d.where(d>0,0).rolling(p).mean()
    l = -d.where(d<0,0).rolling(p).mean()
    return 100 - 100/(1 + g/l)

async def send(coin, signal, extra=None):
    p = {"secret": SECRET, "signal": signal, "coin": coin}
    if extra: p.update(extra)
    async with httpx.AsyncClient() as c:
        await c.post(WEBHOOK, json=p, timeout=10)
    print(f"{datetime.now().strftime('%H:%M:%S')} → {signal.upper()} {coin}")

async def check(coin):
    try:
        o = await exchange.fetch_ohlcv(f"{coin}/USDT", TIMEFRAME, 100)
        df = pd.DataFrame(o, columns=['ts','o','h','l','c','v'])
        df['ema'] = df['c'].ewm(span=5).mean()
        df['rsi'] = rsi(df['c'])
        df['vol20'] = df['v'].rolling(20).mean()

        pos = await exchange.fetch_positions([f"{coin}/USDT"])
        has = pos[0]['contracts'] > 0

        buy = df['c'].iloc[-1] > df['ema'].iloc[-1] and df['rsi'].iloc[-1] > 40 and df['v'].iloc[-1] > df['vol20'].iloc[-1]*1.5 and not has
        sell = df['c'].iloc[-1] < df['ema'].iloc[-1] and has

        if buy:  await send(coin, "buy", {"tp":1.5,"sl":1.0,"trail":0.5})
        if sell: await send(coin, "close_all")
    except: pass

async def main():
    print("СКАНЕР OZ 2026 ЗАПУЩЕН — ОТДЕЛЬНЫЙ ПРОЕКТ")
    while True:
        await asyncio.gather(*(check(c) for c in COINS))
        await asyncio.sleep(INTERVAL)

# Каждые 45 сек шлём пинг в основной бот, чтобы он знал, что мы живы
async def heartbeat():
    while True:
        try:
            async with httpx.AsyncClient() as c:
                await c.post("https://bot-fly-oz.fly.dev/scanner_ping", timeout=10)
        except:
            pass
        await asyncio.sleep(45)

# Запускаем пинг параллельно со сканером
async def main():
    print("СКАНЕР OZ 2026 ЗАПУЩЕН — ОТДЕЛЬНЫЙ ПРОЕКТ")
    # Запускаем пинг и сканер одновременно
    await asyncio.gather(heartbeat(), real_scanner_loop())  # переименуй свой main() в real_scanner_loop()

# Переименуй свой старый main() в real_scanner_loop() или просто вставь это:
async def real_scanner_loop():
    while True:
        if scanner_status.get("enabled", True):  # будет работать только если включён
            await asyncio.gather(*(check(c) for c in COINS))
        await asyncio.sleep(INTERVAL)

if __name__ == "__main__":
    asyncio.run(main())
