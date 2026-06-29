# Build wheels in a disposable stage so compiler toolchains and source-control
# clients are not present in the final Django/Gunicorn runtime image.
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    git \
    libldap2-dev \
    libsasl2-dev \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /build/requirements.txt
COPY OpenKB-main /build/OpenKB-main

RUN python -m pip install --upgrade pip setuptools wheel && \
    python -m pip wheel --wheel-dir /wheels -r /build/requirements.txt /build/OpenKB-main


FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    HOME=/tmp \
    TMPDIR=/tmp

WORKDIR /app

# Runtime-only libraries. No compiler, build-essential package, or Git is kept
# in the web/worker/scheduler image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libmagic1 \
    libldap2-dev \
    libsasl2-dev \
    libpq5 \
    poppler-utils \
    postgresql-client \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /wheels /wheels
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels -r /app/requirements.txt && \
    python -m pip install --no-cache-dir --no-index --find-links=/wheels openkb==0.1.3 && \
    rm -rf /wheels

RUN groupadd --gid 10001 djopenkb && \
    useradd --uid 10001 --gid 10001 --create-home --home-dir /home/djopenkb --shell /usr/sbin/nologin djopenkb

COPY --chown=10001:10001 . /app/
RUN mkdir -p /app/staticfiles /app/openkb-data /app/openkb-data-internal && \
    chown -R 10001:10001 /app /home/djopenkb

USER 10001:10001

EXPOSE 8000

CMD ["gunicorn", "djopenkb.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "300"]
