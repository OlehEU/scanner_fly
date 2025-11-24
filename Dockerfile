FROM python:3.11-slim

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем код
COPY main.py .

# Открываем порт, который указан в fly.toml
EXPOSE 8000

# Запускаем правильно
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
