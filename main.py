import asyncio
import os
import time
from typing import List

import ccxt.async_support as ccxt
from fastapi import FastAPI, BackgroundTasks
import httpx # Используем httpx для отправки вебхуков

# --- КОНФИГУРАЦИЯ ---

# 1. Исправленный список интервалов Binance (удален '45m' и оставлены только поддерживаемые)
# Поддерживаемые интервалы: '1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '8h', '12h', '1d', '3d', '1w', '1M'
KLINE_INTERVALS = ['1m', '5m', '30m', '1h', '4h'] 

# Пары для сканирования
SYMBOLS = ['DOGE/USDT', 'BTC/USDT', 'ETH/USDT']

# Время ожидания между циклами сканирования (секунды)
SCAN_INTERVAL_SECONDS = 60

# --- ИНИЦИАЛИЗАЦИЯ FASTAPI И ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ---

app = FastAPI(title="Binance Crypto Scanner")
# Глобальная переменная для асинхронного клиента биржи
exchange: ccxt.binance = None
# Флаг для управления основным циклом сканирования
is_scanning_running = False

# Получение переменных окружения
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
# Для сканирования требуются API KEY и SECRET
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_SECRET")

# --- СЛУЖЕБНЫЕ ФУНКЦИИ ---

async def send_webhook_notification(message: str):
    """
    Отправляет уведомление на Webhook URL.
    """
    if not WEBHOOK_URL:
        print(f"[ПРЕДУПРЕЖДЕНИЕ] WEBHOOK_URL не настроен. Сообщение проигнорировано: {message}")
        return

    payload = {"text": f"[Сканер] {message}"}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(WEBHOOK_URL, json=payload, timeout=5)
            response.raise_for_status()
    except Exception as e:
        print(f"[ОШИБКА Webhook] Не удалось отправить уведомление: {e}")

async def fetch_klines_with_retry(symbol: str, interval: str, limit: int = 100):
    """
    Получает данные klines с биржи с логикой повторных попыток.
    """
    # Проверка на наличие инициализированного exchange
    if not exchange:
        print("[ОШИБКА] Клиент биржи не инициализирован.")
        return None

    for i in range(3): # 3 попытки
        try:
            # Для надежности используем 100 свечей, а не дефолтное значение
            klines = await exchange.fetch_ohlcv(symbol, interval, limit=limit)
            return klines
        except ccxt.ExchangeError as e:
            # Перехват ошибок, связанных с биржей (например, Invalid interval)
            print(f"[ОШИБКА CCXT] {symbol} {interval}: {e}")
            # Отправка уведомления о критической ошибке API
            await send_webhook_notification(f"Критическая ошибка API на {symbol} {interval}: {e}")
            return None # Прекращаем попытки при ошибке API
        except ccxt.NetworkError as e:
            # Перехват ошибок сети
            print(f"[ОШИБКА СЕТИ] Попытка {i+1} для {symbol} {interval}: {e}")
            await asyncio.sleep(2 ** i) # Экспоненциальная задержка
            continue
        except Exception as e:
            print(f"[НЕПРЕДВИДЕННАЯ ОШИБКА] {symbol} {interval}: {e}")
            return None
    return None

# --- ГЛАВНАЯ ЛОГИКА СКАНЕРА ---

async def run_scanner():
    """
    Основной цикл сканера.
    """
    global is_scanning_running
    is_scanning_running = True
    print(f"--- Сканер запущен. Интервалы: {', '.join(KLINE_INTERVALS)} ---")

    while is_scanning_running:
        start_time = time.time()
        tasks = []

        # Создание задач для всех пар и интервалов
        for symbol in SYMBOLS:
            for interval in KLINE_INTERVALS:
                # В этом месте вызывается ваша основная логика сканирования
                tasks.append(scan_symbol_and_check(symbol, interval))

        # Ожидание завершения всех задач
        await asyncio.gather(*tasks)

        elapsed_time = time.time() - start_time
        sleep_duration = SCAN_INTERVAL_SECONDS - elapsed_time

        if sleep_duration > 0:
            # Ожидание до следующего цикла
            await asyncio.sleep(sleep_duration)
            
    print("--- Сканер остановлен. ---")


async def scan_symbol_and_check(symbol: str, interval: str):
    """
    Логика получения данных и проверки условий для одной пары/интервала.
    Здесь вы должны вставить вашу торговую логику (например, RSI, MACD и т.д.).
    """
    klines = await fetch_klines_with_retry(symbol, interval)

    if not klines or len(klines) < 20: # Проверка минимального количества свечей
        return

    # --- ПРИМЕР ЛОГИКИ ПРОВЕРКИ ---
    # Получаем последнюю закрытую цену и предыдущую
    # Формат свечи: [timestamp, open, high, low, close, volume, ...]
    last_close = klines[-1][4]
    prev_close = klines[-2][4]

    # Условие: Цена выросла более чем на 1% за последнюю свечу
    if (last_close / prev_close - 1) * 100 > 1.0:
        message = f"БОЛЬШОЙ РОСТ! 📈 {symbol} ({interval}). Цена: {last_close}. Рост > 1%"
        await send_webhook_notification(message)


# --- ОБРАБОТЧИКИ СОБЫТИЙ FASTAPI ---

@app.on_event("startup")
async def startup_event():
    """
    Вызывается при запуске Uvicorn. Инициализирует обменник и проверяет конфиг.
    """
    global exchange

    if not WEBHOOK_URL:
        # Критическая ошибка 1: Нет WEBHOOK_URL. 
        print("❌ КРИТИЧЕСКАЯ ОШИБКА: Не определена переменная окружения WEBHOOK_URL. СКАНИРОВАНИЕ НЕВОЗМОЖНО.")
        
    if not API_KEY or not API_SECRET:
        print("❌ ПРЕДУПРЕЖДЕНИЕ: Не определены API_KEY или API_SECRET. Функции, требующие авторизации, будут недоступны.")

    # Инициализация асинхронного клиента ccxt
    try:
        exchange = ccxt.binance({
            'apiKey': API_KEY,
            'secret': API_SECRET,
            'options': {'defaultType': 'future'} # Или 'spot', в зависимости от ваших нужд
        })
        print("✅ Асинхронный клиент Binance успешно инициализирован.")
    except Exception as e:
        print(f"❌ ОШИБКА инициализации CCXT: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    """
    Вызывается при остановке Uvicorn. Корректно закрывает клиент ccxt.
    (Решение ошибки 3: Незакрытые Ресурсы)
    """
    global is_scanning_running
    is_scanning_running = False # Останавливаем цикл сканирования

    if exchange:
        try:
            # ЭТО ИСПРАВЛЕНИЕ: явный вызов await exchange.close()
            await exchange.close() 
            print("✅ Асинхронный клиент Binance корректно закрыт.")
        except Exception as e:
            print(f"❌ ОШИБКА при закрытии клиента Binance: {e}")


# --- ЭНДПОИНТЫ FASTAPI ---

@app.get("/")
async def root():
    """
    Простой эндпоинт для проверки статуса сервиса.
    """
    status_message = "Сканирование активно." if is_scanning_running and WEBHOOK_URL else "Сканер ожидает."
    
    return {
        "status": status_message,
        "is_scanner_running": is_scanning_running,
        "webhook_configured": bool(WEBHOOK_URL)
    }

@app.post("/start_scan")
async def start_scan(background_tasks: BackgroundTasks):
    """
    Запускает фоновую задачу сканирования, если она еще не запущена.
    """
    global is_scanning_running
    
    if not WEBHOOK_URL:
        # Сначала нужно настроить секрет через Fly.io
        return {"error": "Невозможно запустить сканер.", "details": "Сначала установите секрет WEBHOOK_URL через Fly.io."}

    if is_scanning_running:
        return {"message": "Сканирование уже запущено."}
    
    # Запуск основного цикла сканирования в фоновом режиме
    background_tasks.add_task(run_scanner)
    
    return {"message": "Сканирование запущено в фоновом режиме."}

# Запуск сканера после инициализации (если WEBHOOK_URL доступен)
@app.on_event("startup")
async def start_scanner_after_init():
    """Запускает сканер автоматически, если все настроено."""
    if WEBHOOK_URL and exchange and not is_scanning_running:
        print("Автоматический запуск сканера...")
        
        # Используем asyncio.create_task для запуска run_scanner в фоновом режиме
        # Это предотвращает блокировку Uvicorn на этапе запуска.
        async def delayed_start():
            await asyncio.sleep(1) # Небольшая задержка для завершения startup
            # Важно: вызываем run_scanner напрямую, а не через start_scan, чтобы избежать ошибок
            # с BackgroundTasks на этапе startup, и контролировать флаг is_scanning_running
            if not is_scanning_running:
                await run_scanner()

        asyncio.create_task(delayed_start())

    elif not WEBHOOK_URL:
        print("Ожидание настройки переменной окружения WEBHOOK_URL для запуска сканера.")
