FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

EXPOSE 8000

# HF_API_KEY must be provided at runtime (docker run -e HF_API_KEY=... or via
# your platform's secret/env manager). The app refuses to start without it.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
