# ─────────────────────────────────────────────────────────────────────────────
#  Imagen unica para `api` (gunicorn/WSGI) y `ws` (daphne/ASGI).
#  La separacion de ADR-0003 es de PROCESO, no de codigo: mismo artefacto,
#  distinto comando. Se construye una vez y se despliega dos veces.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH="/venv/bin:$PATH"
WORKDIR /app


# ── Etapa de build ───────────────────────────────────────────────────────────
#  Aca es donde arm64 duele: mysqlclient no tiene wheel para aarch64 y se
#  compila desde fuente. Aislarlo en una etapa aparte evita arrastrar el
#  toolchain de compilacion (~250 MB) a la imagen final.
FROM base AS builder
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        pkg-config \
        default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements/ requirements/
RUN python -m venv /venv \
    && pip install --upgrade pip setuptools wheel \
    && pip install -r requirements/base.txt


# ── Etapa final ──────────────────────────────────────────────────────────────
FROM base AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        libmariadb3 \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 1000 co2 \
    && useradd --system --uid 1000 --gid co2 --create-home co2

COPY --from=builder /venv /venv
COPY --chown=co2:co2 . /app

RUN mkdir -p /app/staticfiles && chown -R co2:co2 /app/staticfiles

USER co2
EXPOSE 8000 9000

# El comando lo define docker-compose:
#   api → gunicorn config.wsgi:application
#   ws  → daphne  config.asgi:application
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
