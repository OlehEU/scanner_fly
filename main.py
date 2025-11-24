import os
import logging
import time
import threading
import numpy as np
import requests
from binance.client import Client
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse

# --------------------------
# Logging
# --------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("scanner-fly-oz")

# --------------------------
# Config
# --------------------------
LIST_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
TF_LIST = ["1m", "3m", "5m", "15m", "30m", "45m", "1h", "4h"]

config = {
    "tf": {c: "1h" for c in LIST_SYMBOLS},
    "tg": {c: "" for c in LIST_SYMBOLS},  # optional webhook per coin
}

# --------------------------
# Binance client
# --------------------------
BINANCE_API_KEY = os.environ.get("BINANCE_API_KEY")
BINANCE_API_SECRET = os.environ.get("BINANCE_API_SECRET")
WEBHOOK = os.environ.get("WEBHOOK")  # main webhook
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET")  # optional

client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

# --------------------------
# Indicators
# --------------------------
def rsi(data, period=14):
    data = np.array(data, dtype=float)
    if len(data) < period + 1:
        return 50
    delta = np.diff(data)
    up = delta.clip(min=0)
    down = -delta.clip(max=0)
    ma_up = up[-period:].mean()
    ma_down = down[-period:].mean()
    if ma_down == 0:
        return 100
    rs = ma_up / ma_down
    return 100 - (100 / (1 + rs))


def stdevup(data):
    d = np.array(data, dtype=float)
    return d.mean() + d.std() * 2


def stdevdown(data):
    d = np.array(data, dtype=float)
    return d.mean() - d.std() * 2


# --------------------------
# Notification
# --------------------------
def send_webhook(message):
    if WEBHOOK:
        try:
            requests.post(WEBHOOK, json={"message": message, "secret": WEBHOOK_SECRET})
        except Exception as e:
            logger.error(f"Ошибка отправки вебхука: {e}")


# --------------------------
# Check coin logic
# --------------------------
def check_coin(symbol):
    try:
        tf = config["tf"][symbol]
        # get klines
        klines = client.get_klines(symbol=symbol, interval=tf, limit=500)
        closes = [float(k[4]) for k in klines]
        if len(closes) < 200:
            return

        price = closes[-1]
        rsi_prev = rsi(closes[-110:-10]) if len(closes) > 110 else 50

        sup = stdevdown(closes[-100:])
        res = stdevup(closes[-100:])

        msg = []
        cond = False

        if price < sup:
            cond = True
            msg.append("Цена ниже поддержки 📉")
        if price > res:
            cond = True
            msg.append("Цена выше сопротивления 📈")
        if rsi_prev < 30:
            cond = True
            msg.append(f"RSI перепродан ({rsi_prev:.1f}) 🔵")
        if rsi_prev > 70:
            cond = True
            msg.append(f"RSI перекуплен ({rsi_prev:.1f}) 🔴")

        if cond:
            text = f"🔔 Сигнал {symbol}\n⏱ TF: {tf}\n💰 Цена: {price}\n{chr(10).join(msg)}"
            send_webhook(text)
            logger.info(f"Сигнал отправлен: {symbol}")

    except Exception as e:
        logger.error(f"Ошибка {symbol}: {e}")


# --------------------------
# Background worker
# --------------------------
def background_worker():
    while True:
        for s in LIST_SYMBOLS:
            check_coin(s)
            time.sleep(1)
        time.sleep(3)


threading.Thread(target=background_worker, daemon=True).start()


# --------------------------
# FastAPI
# --------------------------
app = FastAPI()


@app.get("/", response_class=HTMLResponse)
def root():
    html = "<h2>Scanner Fly OZ — Трейдер Панель</h2>"
    html += "<form method='post' action='/save'><table border='1' cellpadding='5'>"
    html += "<tr><th>Coin</th><th>TF</th><th>Webhook</th></tr>"
    for c in LIST_SYMBOLS:
        options = "".join(f'<option value="{t}" {"selected" if t==config["tf"][c] else ""}>{t}</option>' for t in TF_LIST)
        html += f"<tr><td>{c}</td><td><select name='{c}_tf'>{options}</select></td>"
        html += f"<td><input name='{c}_tg' value='{config['tg'][c]}'/></td></tr>"
    html += "</table><br><button type='submit'>💾 Сохранить</button></form>"
    return html


@app.post("/save")
def save(**data):
    for c in LIST_SYMBOLS:
        config["tf"][c] = data.get(f"{c}_tf", config["tf"][c])
        config["tg"][c] = data.get(f"{c}_tg", config["tg"][c])
    return {"status": "saved", "config": config}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
