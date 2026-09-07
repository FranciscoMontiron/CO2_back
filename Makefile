# ─────────────────────────────────────────────────────────────────────────────
#  Atajos de desarrollo y despliegue.
#
#  En Windows sin `make`, los comandos equivalentes estan en el README.
#  Sobre la Pi 5 (`apt install make`) esto es lo que se usa.
# ─────────────────────────────────────────────────────────────────────────────

COMPOSE     := docker compose
COMPOSE_DEV := docker compose -f docker-compose.yml -f docker-compose.dev.yml

.DEFAULT_GOAL := help
.PHONY: help certs up down restart logs ps build dev shell migrate superuser \
        lint fmt test test-hardware clean

help:  ## Muestra esta ayuda
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

certs:  ## Genera el certificado autofirmado de desarrollo (RNF003)
	sh docker/nginx/gen-certs.sh

up: certs  ## Levanta la simulacion arm64 de la Pi 5 (ADR-0005)
	$(COMPOSE) up -d --build

dev:  ## Levanta en arquitectura nativa, con recarga en caliente
	TARGET_PLATFORM=linux/amd64 $(COMPOSE_DEV) up --build

down:  ## Detiene los servicios (conserva los volumenes)
	$(COMPOSE) down

restart:  ## Reinicia los servicios
	$(COMPOSE) restart

ps:  ## Estado de los servicios
	$(COMPOSE) ps

logs:  ## Sigue los logs de todos los servicios
	$(COMPOSE) logs -f

build:  ## Reconstruye las imagenes sin cache
	$(COMPOSE) build --no-cache

shell:  ## Shell de Django dentro del contenedor api
	$(COMPOSE) exec api python manage.py shell

migrate:  ## Aplica las migraciones
	$(COMPOSE) exec api python manage.py migrate

superuser:  ## Crea un superusuario del admin
	$(COMPOSE) exec api python manage.py createsuperuser

lint:  ## PEP8 + docstrings (RNF007)
	$(COMPOSE) exec api ruff check .
	$(COMPOSE) exec api black --check .

fmt:  ## Formatea el codigo
	$(COMPOSE) exec api ruff check --fix .
	$(COMPOSE) exec api black .

test:  ## Tests + cobertura >= 70% (RNF007)
	$(COMPOSE) exec api pytest

test-hardware:  ## Pruebas de RNF001 — SOLO sobre la Pi 5 real
	@echo "⚠️  Bajo emulacion QEMU estas mediciones NO son validas (ADR-0005)."
	@echo "   Ejecutar unicamente sobre la Raspberry Pi 5 de destino."
	$(COMPOSE) exec api pytest -m hardware

clean:  ## Detiene y BORRA los volumenes (se pierde la base de datos)
	$(COMPOSE) down -v
