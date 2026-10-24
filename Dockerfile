FROM python:3.12-slim

LABEL org.opencontainers.image.title="dmd-media-showcase" \
      org.opencontainers.image.description="V3.0 DDD Media Showcase & Real-Time UDP/MQTT Video Streaming Server for HUB75 LED Matrices (ArcadeMatrix ESP32 / RPi)" \
      org.opencontainers.image.source="https://github.com/red77290/dmd_media_showcase" \
      org.opencontainers.image.licenses="MIT"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOST=0.0.0.0 \
    PORT=8890 \
    MQTT_PORT=1883 \
    DMD_DATA_DIR=/app/data \
    CACHE_DIR=/tmp/dmd_media_showcase_cache

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        curl \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app

ARG DMD_GIF_CONVERTER_REV=1d80df7

RUN pip install --upgrade pip && \
    pip install . && \
    python3 -c "from src.plugins.detectors.detector import _ensure_yolo_model; _ensure_yolo_model()" && \
    mkdir -p /app/data /tmp/dmd_media_showcase_cache

EXPOSE 8890 1883 5353/udp

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8890/api/v1/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8890", "--proxy-headers", "--forwarded-allow-ips", "*"]
