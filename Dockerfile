FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MEDINTAKE_DATA_DIR=/data \
    MEDINTAKE_INBOX_ROOT=/data/inbox \
    MEDINTAKE_HOST=0.0.0.0 \
    MEDINTAKE_PORT=8080 \
    MEDINTAKE_TIMEZONE=Europe/Moscow

COPY pyproject.toml README.md requirements.txt ./
COPY medintake ./medintake
RUN pip install --no-cache-dir -c requirements.txt . \
    && useradd --system --uid 10001 --home-dir /data --shell /usr/sbin/nologin medintake \
    && mkdir -p /data/inbox \
    && chown -R medintake /data

USER medintake
EXPOSE 8080
VOLUME ["/data"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ['MEDINTAKE_PORT'] + '/health', timeout=4)"]
CMD ["medintake-api"]
