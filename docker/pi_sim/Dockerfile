# ─────────────────────────────────────────────────────────────────────────────
#  Pi 5 simulada: tablero, protocolo de lgpio y página. Solo biblioteca estándar.
#
#  Usa el UID del resto de los servicios porque la API guarda la configuración
#  con permisos 0600 y este proceso debe poder leerla.
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN groupadd --system --gid 1000 co2 \
    && useradd --system --uid 1000 --gid co2 --create-home co2

COPY --chown=co2:co2 controller/__init__.py controller/hardware_config.py controller/factory.py controller/drivers.py controller/roles.py /app/controller/
COPY --chown=co2:co2 pi_sim/__init__.py pi_sim/tablero.py pi_sim/servidor.py pi_sim/pagina.html /app/pi_sim/

USER co2

CMD ["python", "-m", "pi_sim.servidor"]
