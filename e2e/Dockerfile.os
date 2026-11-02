FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        bash \
        curl \
        mosquitto-clients \
        procps \
        psmisc \
    && rm -rf /var/lib/apt/lists/*

# Copy ONLY the genuine gaming OS setup scripts & hooks (zero private files)
COPY --from=arcadematrix tools /repos/ArcadeMatrix/tools
COPY --from=arcadematrix_rpi tools /repos/ArcadeMatrix_RPi/tools

COPY e2e/os_simulators/os_agent.py /opt/e2e/os_agent.py

EXPOSE 9000

HEALTHCHECK --interval=2s --timeout=3s --start-period=2s --retries=15 \
    CMD curl -fsS --max-time 2 http://127.0.0.1:9000/health || exit 1

CMD ["python3", "/opt/e2e/os_agent.py"]
