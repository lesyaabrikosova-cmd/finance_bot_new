FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ALLOCATOR_DATA_DIR=/data \
    HEALTH_PORT=8080

WORKDIR /app

RUN addgroup --system allocator \
    && adduser --system --ingroup allocator allocator \
    && mkdir -p /data \
    && chown allocator:allocator /data

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=allocator:allocator . .

USER allocator

EXPOSE 8080
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)" || exit 1

CMD ["python", "bot.py"]

