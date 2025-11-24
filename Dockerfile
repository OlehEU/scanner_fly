# Dockerfile — OZ SCANNER v2026 ULTRA SERVER
FROM python:3.11-slim

# Устанавливаем системные зависимости для TA-Lib
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libta-lib0 \
    libta-lib-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
