# ─────────────────────────────────────────────────────────────────────────────
#  Controlador para el simulador: igual a docker/controller/Dockerfile, más
#  gpiozero en cualquier arquitectura y el doble de lgpio en PYTHONPATH.
#
#  El doble queda fuera de /app para que el override de desarrollo no lo tape.
#  Esta imagen nunca se usa en la Pi real.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LG_WD=/tmp \
    PYTHONPATH=/opt/pi_sim_shim

WORKDIR /app

COPY requirements/controller.txt requirements/controller.txt
RUN pip install --upgrade pip \
    && pip install -r requirements/controller.txt \
    && pip install gpiozero==2.0.1

RUN groupadd --system --gid 1000 co2 \
    && useradd --system --uid 1000 --gid co2 --create-home co2

COPY pi_sim/shim/ /opt/pi_sim_shim/
COPY --chown=co2:co2 controller/ /app/controller/
RUN mkdir -p /app/hardware && chown co2:co2 /app/hardware

USER co2

CMD ["python", "-u", "-m", "controller.main"]
