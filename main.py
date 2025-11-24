import logging
import time
import numpy as np
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse
from binance.client import Client
from jaticker import BinanceClient as JatickerClient
import threading
import uvicorn

# -------------------------------------------------------
#  LOGGING
# -------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("OZ2026")

# -------------------------------------------------------
#  CONFIG
# -------------------------------------------------------
LIST_SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]

TF_LIST = ["1m", "3m", "5m", "15m", "30m", "45m", "1h", "4h"]

config = {
    "tf": {c: "1h" for c in LIST_SYMBOLS},
    "tg": {c: "" for c in LIST_SYMBOLS},
}

# -------------------------------------------------------
#  SAFE BINANCE CLIENTS
# -------------------------------------------------------
# Binance API client (PUBLIC MODE — no crash)
client = Client("", "")

# Jaticker
jclient = JatickerClient()

# -------------------------------------------------------
#  INDICATORS
# -------------------------------------------------------
def rsi(data, period=14):
    data = np.array(data, dtype=float)
    if len(data) < period + 1:
        return 50  # fallback

    delta = np.diff(data)
    up = delta.clip(min=0)
    down = -delta.clip(max=0)

    ma_up = up[-period:].mean()
    ma_down = down[-period:].mean()

    if ma_down == 0:
        return 100

    rs = ma_up / ma_down
    return 100 - (100 / (1 + rs))


def _stdevup(data):
    d = np.array(data, dtype=float)
    return d.mean() + d.std() * 2


def _stdevdown(data):
    d = np.array(data, dtype=float)
    return d.mean() - d.std() * 2


# -------------------------------------------------------
#  CHECK COIN LOGIC
# -------------------------------------------------------
def check_coin(symbol):
    try:
        tf = config["tf"][symbol]
        tg = config["tg"][symbol]

        kl = jclient.klines(symbol=symbol, interval=tf, limit=500)
        closes = [float(x[4]) for x in kl]

        if len(closes) < 200:
            return

        price = closes[-1]

        rsi_prev = rsi(closes[-110:-10]) if len(closes) > 110 else 50

        sup = _stdevdown(closes[-100:])
        res = _stdevup(closes[-100:])

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
            text = f"""🔔 Сигнал {symbol}
⏱ TF: {tf}
💰 Цена: {price}
{chr(10).join(msg)}
"""

            if tg:
                jclient.send_telegram_message(tg, text)

            logger.info(f"Сигнал отправлен: {symbol}")

    except Exception as e:
        logger.error(f"Ошибка {symbol}: {e}")


# -------------------------------------------------------
#  BACKGROUND THREAD (SAFE)
# -------------------------------------------------------
def background_worker():
    while True:
        try:
            for s in LIST_SYMBOLS:
                check_coin(s)
                time.sleep(1)
        except Exception as e:
            logger.error(f"Background error: {e}")
        time.sleep(3)


threading.Thread(target=background_worker, daemon=True).start()


# -------------------------------------------------------
#  FASTAPI
# -------------------------------------------------------
app = FastAPI()


@app.get("/", response_class=HTMLResponse)
def root():
    html = """
    <h2>OZ2026 — Трейдер Панель</h2>
    <form method='post' action='/save'>
        <table border='1' cellpadding='5'>
            <tr><th>Coin</th><th>TF</th><th>Telegram Chat ID</th></tr>
    """

    for c in LIST_SYMBOLS:
        options = "".join(
            f'<option value="{t}" {"selected" if t == config["tf"][c] else ""}>{t}</option>'
            for t in TF_LIST
        )

        html += f"""
            <tr>
                <td>{c}</td>
                <td>
                    <select name='{c}_tf'>
                        {options}
                    </select>
                </td>
                <td>
                    <input name='{c}_tg' value='{config["tg"][c]}' />
                </td>
            </tr>
        """

    html += """
        </table>
        <br>
        <button type='submit'>💾 Сохранить</button>
    </form>
    """

    return html


@app.post("/save")
def save(
    BTCUSDT_tf: str = Form(...),
    ETHUSDT_tf: str = Form(...),
    BNBUSDT_tf: str = Form(...),
    BTCUSDT_tg: str = Form(""),
    ETHUSDT_tg: str = Form(""),
    BNBUSDT_tg: str = Form(""),
):
    config["tf"]["BTCUSDT"] = BTCUSDT_tf
    config["tf"]["ETHUSDT"] = ETHUSDT_tf
    config["tf"]["BNBUSDT"] = BNBUSDT_tf

    config["tg"]["BTCUSDT"] = BTCUSDT_tg
    config["tg"]["ETHUSDT"] = ETHUSDT_tg
    config["tg"]["BNBUSDT"] = BNBUSDT_tg

    return {"saved": True, "config": config}


# -------------------------------------------------------
#  UVICORN LAUNCH
# -------------------------------------------------------
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False
    )
