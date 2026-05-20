FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    git \
    curl \
    gettext \
    libmagic1 \
    libldap2-dev \
    libsasl2-dev \
    ldap-utils \
    postgresql-client \
    poppler-utils \
    tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY . /app/

RUN python -m pip install --upgrade pip setuptools wheel

RUN python -m pip install \
    django \
    gunicorn \
    markdown \
    bleach \
    pillow \
    python-dotenv \
    django-auth-ldap \
    "psycopg[binary]"

RUN python -m pip install -e /app/OpenKB-main

EXPOSE 8000

CMD ["gunicorn", "djopenkb.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "300"]