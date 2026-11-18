# Simulador de Raspberry Pi 5: plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un contenedor `pi-sim` con página propia que hace de Pi 5 para el controlador real en modo `gpio`.

**Architecture:** Un doble del módulo `lgpio` envía cada llamada por TCP al contenedor `pi-sim`, que modela líneas y componentes. gpiozero, `LGPIOFactory`, drivers y gestor corren sin cambios.

**Tech Stack:** Python 3.12 con biblioteca estándar, gpiozero 2.0.1, Docker Compose, HTML y JS sin dependencias.

**Spec:** `docs/superpowers/specs/2026-11-04-simulador-pi5-design.md`

## Global Constraints

- Rama `feature-componentes-v2`. Sin commits ni push: los hace el usuario.
- No modificar `controller/`, `docker-compose.yml`, `docker-compose.pi.yml` ni `docker/controller/Dockerfile`.
- La página se publica solo en `127.0.0.1`.
- El doble se niega a arrancar si existe `/dev/gpiochip0`.

## Review Focus

1. Pin ocupado por otro proceso: `GPIO busy` y emergencia en el gestor.
2. El controlador muere con una salida activa: la línea se libera y conserva su nivel.
3. Pin colgado: solo se bloquea el hilo que llama, y se recupera al quitar la falla.
4. Nombres de componente con HTML: la página los muestra como texto.
5. JSON de configuración inválido: la página informa el error y conserva el último inventario.

---

### Task 1: Tablero

**Files:**
- Create: `pi_sim/__init__.py`, `pi_sim/tablero.py`
- Test: `tests/test_pi_sim.py`

**Interfaces:**
- Produces: `Tablero` con `abrir_chip`, `cerrar_chip`, `reclamar_entrada`, `reclamar_salida`, `reclamar_alerta`, `liberar`, `leer`, `escribir`, `modo`, `debounce`, `sincronizar(config)`, `conectar`, `accionar_contacto`, `fijar_temperatura`, `fijar_falla`, `estado()`; y `ErrorLgpio`.

- [ ] Tests de reserva, modos, conexión, modelos, fallas y MCP3008; verlos fallar.
- [ ] Implementar y verlos pasar.

### Task 2: Doble de lgpio y servidor

**Files:**
- Create: `pi_sim/shim/lgpio.py`, `pi_sim/servidor.py`
- Test: `tests/test_pi_sim.py`

**Interfaces:**
- Consumes: `Tablero`.
- Produces: `Servidor(tablero, host, puerto_gpio, puerto_http, ruta_config)` con `iniciar()` y `detener()`; variables `PI_SIM_HOST` y `PI_SIM_PUERTO`.

- [ ] Tests con gpiozero real y `controller.drivers`: los cinco tipos del catálogo, eventos de entrada, liberación por desconexión y bloqueo; verlos fallar.
- [ ] Tests con `GestorHardware` real: alta, orden aplicada, pin ocupado y pin colgado.
- [ ] Implementar y verlos pasar.

### Task 3: Página

**Files:**
- Create: `pi_sim/pagina.html`
- Test: `tests/test_pi_sim.py`

- [ ] Test HTTP: la página responde, `/api/estado` lista el inventario y un POST sin JSON se rechaza.
- [ ] Implementar el conector de 40 pines, tarjetas, fallas y registro, usando `textContent` para todo dato del usuario.

### Task 4: Contenedores

**Files:**
- Create: `docker/pi_sim/Dockerfile`, `docker/pi_sim/controller.Dockerfile`, `docker-compose.sim.yml`, `docs/simulador-pi5.md`
- Modify: `Makefile`

- [ ] `docker compose config` válido con los tres archivos.
- [ ] Levantar el stack aislado y recorrer el flujo: alta en la API, aparece en la página, orden, nivel del pin, desconexión y falla.
- [ ] Correr toda la suite y `ruff` sobre los archivos nuevos.
