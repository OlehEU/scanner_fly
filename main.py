# main.py — OZ 2026 СКАНЕР | ПАРОЛЬ 777 | 45m | ВСЁ НА ОДНОЙ СТРАНИЦЕ
import os
import json
import time
import logging
import httpx
from fastapi import FastAPI, Request, Response, HTTPException

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("SCANNER")

# ==================== КОНФИГ ====================
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://bot-fly-oz.fly.dev/webhook")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "supersecret123")

COINS = ["XRP", "SOL", "ETH", "BTC", "DOGE"]
TF_LIST = ["1m", "3m", "5m", "15m", "30m", "45m", "1h"]  # ← 45m добавлен

CONFIG_FILE = "config.json"
SIGNALS_FILE = "signals.json"

PASSWORD = "777"  # ← ПАРОЛЬ ДЛЯ ВХОДА

# ==================== ДАННЫЕ ====================
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

# ==================== ОТПРАВКА СИГНАЛА ====================
async def send_signal(coin: str, action: str):
    if not config["enabled"] or not config["coins"].get(coin, False):
        return
    payload = {"coin": coin, "signal": "buy" if action == "LONG" else "close"}
    headers = {"Authorization": f"Bearer {WEBHOOK_SECRET}", "Content-Type": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(WEBHOOK_URL, json=payload, headers=headers)
        signals.append({
            "time": time.strftime("%H:%M:%S"),
            "date": time.strftime("%Y-%m-%d"),
            "coin": coin,
            "action": action
        })
        if len(signals) > 1000:
            signals.pop(0)
        save(SIGNALS_FILE, signals)
        log.info(f"СИГНАЛ: {action} {coin}")
    except Exception as e:
        log.error(f"Ошибка: {e}")

# ==================== FASTAPI ====================
app = FastAPI()

# === СТРАНИЦА АВТОРИЗАЦИИ ===
LOGIN_PAGE = """
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>СКАНЕР OZ 2026</title>
<style>body{background:#0d1117;color:#c9d1d9;font-family:Arial;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;}
.box{background:#161b22;padding:40px;border-radius:16px;border:2px solid #30363d;text-align:center;}
input{padding:16px;font-size:20px;width:200px;border-radius:8px;border:none;margin:10px;}
button{padding:16px 32px;font-size:20px;background:#238636;color:white;border:none;border-radius:8px;cursor:pointer;}
h1{color:#58a6ff;}
</style></head>
<body>
<div class="box">
<h1>СКАНЕР OZ 2026</h1>
<p>Введи пароль:</p>
<input type="password" id="pass" placeholder="пароль">
<button onclick="login()">ВОЙТИ</button>
<script>
function login() {
    if(document.getElementById('pass').value === '777') {
        document.cookie = "auth=777; path=/";
        location.reload();
    } else {
        alert('Неверный пароль');
    }
}
</script>
</div>
</body></html>
"""

# === ГЛАВНАЯ СТРАНИЦА ===
MAIN_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>СКАНЕР OZ 2026</title>
    <style>
        body{background:#0d1117;color:#c9d1d9;font-family:Arial;margin:0;padding:20px;text-align:center;}
        h1{color:#58a6ff;font-size:3em;}
        .btn{padding:16px 32px;margin:10px;font-size:20px;border:none;border-radius:12px;cursor:pointer;}
        .on{background:#238636;color:white;}
        .off{background:#f85149;color:white;}
        .big{font-size:28px;padding:20px 50px;}
        .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:20px;margin:40px 0;}
        .card{background:#161b22;padding:25px;border-radius:16px;border:2px solid #30363d;}
        select, button{width:100%;padding:14px;margin:8px 0;font-size:18px;border-radius:8px;}
        table{width:100%;border-collapse:collapse;margin:30px 0;}
        th,td{border:1px solid #30363d;padding:12px;}
        th{background:#21262d;color:#58a6ff;}
        .LONG{color:#7ce38b;font-weight:bold;}
        .CLOSE{color:#f85149;font-weight:bold;}
        .stats{margin:40px 0;font-size:22px;}
        .logout{position:fixed;top:20px;right:20px;background:#f85149;padding:10px 20px;border-radius:8px;cursor:pointer;}
    </style>
</head>
<body>
    <div class="logout" onclick="document.cookie='auth=;expires=Thu,01 Jan 1970';location.reload()">ВЫХОД</div>
    <h1>СКАНЕР OZ 2026</h1>
    <div class="stats">
        Сигналов: <b>{{ total }}</b> | LONG: <b>{{ longs }}</b> | CLOSE: <b>{{ closes }}</b>
    </div>

    <button onclick="toggleScanner()" class="btn big {{ 'off' if not enabled else 'on' }}">
        {{ "ВКЛЮЧИТЬ" if not enabled else "ВЫКЛЮЧИТЬ СКАНЕР" }}
    </button>

    <div class="grid">
        {% for coin in coins %}
        <div class="card">
            <h2>{{ coin }}</h2>
            <button onclick="toggle('{{ coin }}')" class="btn {{ 'on' if coins[coin] else 'off' }}">
                {{ "ВКЛ" if coins[coin] else "ВЫКЛ" }}
            </button>
            <select onchange="settf('{{ coin }}', this.value)">
                {% for tf in tfs %}
                <option value="{{ tf }}" {{ "selected" if tf == tf_config[coin] }}> {{ "CURRENT " if tf == tf_config[coin] else "" }}{{ tf }}</option>
                {% endfor %}
            </select>
        </div>
        {% endfor %}
    </div>

    <h2>Последние сигналы</h2>
    <table>
        <tr><th>Время</th><th>Дата</th><th>Монета</th><th>Сигнал</th></tr>
        {{ signals_table }}
    </table>

    <script>
        async function toggleScanner() { await fetch("/toggle", {method:"POST"}); location.reload(); }
        async function toggle(coin) { await fetch("/toggle_coin", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({coin})}); location.reload(); }
        async function settf(coin, tf) { await fetch("/set_tf", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({coin, tf})); }
    </script>
</body>
</html>
"""

# ==================== ЗАЩИТА ПАРОЛЕМ ====================
def check_auth(request: Request):
    cookie = request.headers.get("cookie", "")
    return "auth=777" in cookie

@app.get("/")
async def root(request: Request):
    if not check_auth(request):
        return Response(LOGIN_PAGE, media_type="text/html")

    total = len(signals)
    longs = sum(1 for s in signals if s["action"] == "LONG")
    closes = total - longs
    recent = signals[-50:]

    table_rows = ""
    for s in recent:
        table_rows += f"<tr><td>{s['time']}</td><td>{s['date']}</td><td><b>{s['coin']}</b></td><td class='{s['action']}'>{s['action']}</td></tr>"

    html = MAIN_PAGE
    html = html.replace("{{ total }}", str(total))
    html = html.replace("{{ longs }}", str(longs))
    html = html.replace("{{ closes }}", str(closes))
    html = html.replace("{{ enabled }}", str(config["enabled"]).lower())
    html = html.replace("{{ coins }}", json.dumps(config["coins"]))
    html = html.replace("{{ tf_config }}", json.dumps(config["tf"]))
    html = html.replace("{{ tfs }}", json.dumps(TF_LIST))
    html = html.replace("{{ signals_table }}", table_rows)

    return Response(html, media_type="text/html")

# ==================== API ====================
@app.post("/toggle")
async def toggle(request: Request):
    if not check_auth(request): raise HTTPException(403)
    config["enabled"] = not config["enabled"]
    save(CONFIG_FILE, config)
    return {"ok": True}

@app.post("/toggle_coin")
async def toggle_coin(request: Request):
    if not check_auth(request): raise HTTPException(403)
    data = await request.json()
    coin = data.get("coin")
    if coin in config["coins"]:
        config["coins"][coin] = not config["coins"][coin]
        save(CONFIG_FILE, config)
    return {"ok": True}

@app.post("/set_tf")
async def set_tf(request: Request):
    if not check_auth(request): raise HTTPException(403)
    data = await request.json()
    coin = data.get("coin")
    tf = data.get("tf")
    if coin in COINS and tf in TF_LIST:
        config["tf"][coin] = tf
        save(CONFIG_FILE, config)
    return {"ok": True}

# ==================== ТЕСТЫ ====================
@app.get("/test_long/{coin}")
async def test_long(coin: str, request: Request):
    if not check_auth(request): raise HTTPException(403)
    if coin.upper() in COINS:
        await send_signal(coin.upper(), "LONG")
        return {"ok": True}
    return {"error": "no"}

@app.get("/test_close/{coin}")
async def test_close(coin: str, request: Request):
    if not check_auth(request): raise HTTPException(403)
    if coin.upper() in COINS:
        await send_signal(coin.upper(), "CLOSE")
        return {"ok": True}
    return {"error": "no"}

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    import uvicorn
    print("СКАНЕР OZ 2026 ЗАЩИЩЁН ПАРОЛЕМ 777 → https://scanner-fly-oz.fly.dev")
    uvicorn.run(app, host="0.0.0.0", port=8000)
