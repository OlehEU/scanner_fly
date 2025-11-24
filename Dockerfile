# Dockerfile — OZ 2026 СКАНЕР | РАБОТАЕТ НА FLY.IO
FROM python:3.11-slim

WORKDIR /app

# Копируем только requirements сначала (для кэша)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь код
COPY . .

# ОТКРЫВАЕМ ПРАВИЛЬНЫЙ ПОРТ (важно для fly.io)
EXPOSE 8000

# ЗАПУСКАЕМ ПРАВИЛЬНОЕ ИМЯ ФАЙЛА — main.py, а не scanner.py
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
