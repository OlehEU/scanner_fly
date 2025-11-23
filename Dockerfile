FROM python:3.11-slim

WORKDIR /app

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY . .

# Экспорт порта для Fly
EXPOSE 8080

# Запуск FastAPI через Uvicorn на 0.0.0.0:8080
CMD ["uvicorn", "scanner:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
