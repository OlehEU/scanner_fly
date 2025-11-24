# main.py — OZ 2026 УЛЬТИМАТ | СТРАТЕГИЯ + ГРАФИК + ПАНЕЛЬ + ПАРОЛЬ 777
import os
import json
import time
import logging
import httpx
import asyncio
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse
import uvicorn

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("OZ2026")

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://bot-fly-oz.fly.dev/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "supersecret123")

COINS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
TF_LIST = ["1m", "3m", "5m", "15m", "30m", "45m", "1h", "4h"]

CONFIG_FILE = "config.json"
SIGNALS_FILE = "signals.json"
PASSWORD = "777"

# Загрузка/сохранение
def load(file, default):
    try:
        with open(file, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        with open(file, "w", encoding="utf-8") as f:
            json.dump(default, f, indent=2)
        return default

config = load(CONFIG_FILE, {
    "enabled": True,
    "coins": {c: True for c in COINS},
    "tf": {c: "5m" for c in COINS}
})
signals = load(SIGNALS_FILE, [])

def save(file, data):
    with open(file, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

async def send_signal(coin: str, action: str):
    if not config["enabled"] or not config["coins"].get(coin, False):
        return
    payload = {"coin": coin.replace("USDT",""), "signal": "buy" if action=="LONG" else "close"}
    headers = {"Authorization": f"Bearer {WEBHOOK_SECRET}"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(WEBHOOK_URL, json=payload, headers=headers)
        signals.append({
            "ts": int(time.time()),
            "time": datetime.now().strftime("%H:%M:%S"),
            "date": datetime.now().strftime("%d.%m"),
            "coin": coin,
            "tf": config["tf"][coin],
            "action": action
        })
        if len(signals)>2000: signals.pop(0)
        save(SIGNALS_FILE, signals)
        log.info(f"СИГНАЛ → {action} {coin} {config['tf'][coin]}")
    except Exception as e:
        log.error(f"Webhook error: {e}")

app = FastAPI()

# ====================== ТВОЯ СТРАТЕГИЯ ======================
from binance.client import Client
client = Client()  # без ключей — только публичные данные

async def get_klines(symbol: str, interval: str, limit=500):
    try:
        klines = client.get_klines(symbol=symbol, interval=interval, limit=limit)
        return [[int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5])] for k in klines]
    except:
        return []

def rsi(data, period=14):
    import numpy as np
    close = np.array([x[4] for x in data])
    delta = np.diff(close)
    gain = np.maximum(delta, 0)
    loss = np.maximum(-delta, 0)
    avg_gain = np.convolve(gain, np.ones(period)/period, mode='valid')
    avg_loss = np.convolve(loss, np.ones(period)/period, mode='valid')
    rs = avg_gain / (avg_loss + 1e-10)
    rsi = 100 - (100 / (1 + rs))
    return rsi[-period:] if len(rsi)>0 else [50]
    return rsi[-1] if len(rsi)>0 else 50

async def check_coin(coin: str):
    if not config["coins"].get(coin, False): return
    tf = config["tf"][coin]
    try:
        df = await get_klines(coin, tf, 300)
        if len(df)<100: return

        close = [x[4] for x in df]
        high  = [x[2] for x in df]
        low   = [x[3] for x in df]
        volume = [x[5] for x in df]

        ema20 = sum(close[-20:])/20
        ema50 = sum(close[-50:])/50
        rsi_val = rsi(df[-100:])

        # Твои условия из TradingView (адаптированные)
        bull_div = low[-3] < low[-10] and rsi_val > rsi(df[-100:][-10]) and rsi_val < 35
        bear_div = high[-3] > high[-10] and rsi_val < rsi(df[-100:][-10]) and rsi_val > 65

        trend_up = ema20 > ema50 and close[-1] > ema20
        trend_down = ema20 < ema50 and close[-1] < ema20

        vol_spike = volume[-1] > sum(volume[-10:-1])/9 * 2

        if bull_div and trend_up and vol_spike and rsi_val < 30:
            await send_signal(coin, "LONG")
        elif bear_div and trend_down and vol_spike and rsi_val > 70:
            await send_signal(coin, "CLOSE")
    except Exception as e:
        log.error(f"Ошибка {coin}: {e}")

async def scanner_loop():
    while True:
        if config["enabled"]:
            tasks = [check_coin(c) for c in COINS]
            await asyncio.gather(*tasks)
        await asyncio.sleep(15)  # каждые 15 сек

@app.on_event("startup")
async def start_scanner():
    asyncio.create_task(scanner_loop())

# ====================== ВЕБ-ПАНЕЛЬ ======================
LOGIN_HTML = """<html><head><meta charset="utf-8"><title>OZ2026</title><style>body{background:#0d1117;color:#c9d1d9;font-family:Arial;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:linear-gradient(135deg,#0d1117,#1a1f2e);} .b{background:#161b22;padding:50px;border-radius:20px;box-shadow:0 0 30px rgba(88,166,255,0.3);text-align:center;} input,button{padding:16px 32px;font-size:22px;margin:10px;border-radius:12px;border:none;} button{background:#238636;color:white;cursor:pointer;} h1{color:#58a6ff;}</style></head><body><div class="b"><h1>OZ 2026 СКАНЕР</h1><p>Пароль:</p><input type="password" id="p" placeholder="777"><br><button onclick="if(document.getElementById('p').value==='777'){document.cookie='auth=777;path=/';location.reload()}else alert('Нет')">ВОЙТИ</button></div></body></html>"""

def auth(r: Request): return "auth=777" in r.headers.get("cookie","")

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    if not auth(request):
        return HTMLResponse(LOGIN_HTML)

    total = len(signals)
    longs = len([s for s in signals if s["action"]=="LONG"])
    closes = total - longs

    # Генерация аннотаций для графика
    annotations = ""
    for s in signals[-100:]:
        ts = s["ts"] * 1000
        color = "#00ff00" if s["action"]=="LONG" else "#ff0000"
        annotations += f"{{x:{ts}, borderColor:'{color}', label:{{content:'{s['action']} {s['coin']}', style:{{background:'{color}'}}}}},"

    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>OZ 2026 УЛЬТИМА</title>
    <script src="https://unpkg.com/lightweight-charts/dist/lightweight.min.js"></script>
    <style>body{{background:#0d1117;color:#c9d1d9;font-family:Arial;margin:0;padding:20px;}}
    .grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:20px;margin:30px 0;}}
    .card{{background:#161b22;padding:25px;border-radius:16px;border:2px solid #30363d;}}
    button{{padding:14px 28px;margin:5px;font-size:18px;border:none;border-radius:10px;cursor:pointer;}}
    .on{{background:#238636;color:white;}} .off{{background:#f85149;color:white;}}
    table{{width:100%;border-collapse:collapse;margin:20px 0;}} th,td{{border:1px solid #30363d;padding:12px;}}
    .LONG{{color:#7ce38b;}} .CLOSE{{color:#f85149;}}
    </style></head><body>
    <h1 style="text-align:center;color:#58a6ff">OZ 2026 УЛЬТИМА</h1>
    <div style="text-align:center;font-size:24px;margin:20px;">
        Сигналов: <b>{total}</b> | LONG: <b>{longs}</b> | CLOSE: <b>{closes}</b>
    </div>
    <button onclick="fetch('/toggle',{{method:'POST'}}).then(()=>location.reload())" class="{'on' if config['enabled'] else 'off'}">
        {'ВЫКЛЮЧИТЬ' if config['enabled'] else 'ВКЛЮЧИТЬ'} СКАНЕР
    </button>

    <div class="grid">
    {"".join([f'<div class="card"><h3>{c.replace("USDT","")}</h3><button onclick="t(\'{c}\')" class="{'on' if config["coins"][c] else 'off'}">{"ВКЛ" if config["coins"][c] else "ВЫКЛ"}</button><select onchange="s(\'{c}\',this.value)">' + "".join([f'<option value="{t}" {"selected" if t==config["tf"][c] else ""}>{t}</option>' for t in TF_LIST]) + f'</select></div>' for c in COINS])}
    </div>

    <h2>ГРАФИК СИГНАЛОВ</h2>
    <div id="chart" style="width:100%;height:500px;"></div>

    <h2>Последние 50 сигналов</h2>
    <table><tr><th>Дата</th><th>Время</th><th>Монета</th><th>ТФ</th><th>Сигнал</th></tr>
    {"".join([f"<tr><td>{s['date']}</td><td>{s['time']}</td><td><b>{s['coin'].replace('USDT','')}</b></td><td>{s['tf']}</td><td class='{s['action']}'>{s['action']}</td></tr>" for s in signals[-50:]])}
    </table>

    <script>
    async function t(c){{await fetch('/toggle_coin',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{coin:c}})})});location.reload()}}
    async function s(c,tf){{await fetch('/set_tf',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{coin:c,tf}})})}}
    const chart = LightweightCharts.createChart(document.getElementById('chart'), {{width:1200,height:500,layout:{{backgroundColor:'#0d1117',textColor:'#c9d1d9'}}}});
    const candleSeries = chart.addCandlestickSeries();
    fetch('https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=15m&limit=500')
      .then(r=>r.json()).then(d=>candleSeries.setData(d.map(k=>{{time:k[0]/1000,open:+k[1],high:+k[2],low:+k[3],close:+k[4]}})));
    chart.timeScale().fitContent();
    candleSeries.setMarkers([{annotations}]);
    </script>
    </body></html>"""
    return HTMLResponse(html)

# API
@app.post("/toggle") 
async def t(r: Request): 
    if not auth(r): raise HTTPException(403)
    config["enabled"]=not config["enabled"]; save(CONFIG_FILE,config); return {"ok":1}

@app.post("/toggle_coin")
async def tc(r: Request):
    if not auth(r): raise HTTPException(403)
    d=await r.json(); c=d["coin"]
    config["coins"][c]=not config["coins"][c]; save(CONFIG_FILE,config); return {"ok":1}

@app.post("/set_tf")
async def stf(r: Request):
    if not auth(r): raise HTTPException(403)
    d=await r.json(); config["tf"][d["coin"]]=d["tf"]; save(CONFIG_FILE,config); return {"ok":1}

@app.get("/test_long/{coin}")
async def tl(coin:str, r: Request):
    if not auth(r): raise HTTPException(403)
    await send_signal(coin.upper()+"USDT","LONG"); return {"ok":1}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
