FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY tariffs.json .

RUN useradd --create-home --uid 10001 bot && mkdir -p /data && chown bot /data
USER bot

CMD ["python", "-m", "app"]
