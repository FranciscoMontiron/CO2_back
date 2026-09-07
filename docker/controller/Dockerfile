# ─────────────────────────────────────────────────────────────────────────────
#  Proceso controlador (ADR-0002)
#
#  Dueño exclusivo del GPIO. Corre el lazo de control y el E-Stop de software
#  fuera del ciclo HTTP. Imagen deliberadamente minima: sin Django, sin ORM,
#  sin stack web — todo eso es latencia y jitter que este proceso no puede
#  permitirse.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements/controller.txt requirements/controller.txt
RUN pip install --upgrade pip && pip install -r requirements/controller.txt

RUN groupadd --system --gid 1000 co2 \
    && useradd --system --uid 1000 --gid co2 --create-home co2
# Sobre la Pi 5 real este usuario ademas debe pertenecer al grupo `gpio`
# para poder abrir /dev/gpiochip*.

COPY --chown=co2:co2 controller/ /app/controller/

USER co2

CMD ["python", "-u", "-m", "controller.main"]
