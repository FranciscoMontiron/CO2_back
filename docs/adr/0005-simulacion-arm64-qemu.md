# ADR-0005 — Simulación de la Pi 5 sobre arm64 emulado

**Estado:** Aceptada
**Fecha:** 2026-09-07

## Contexto

El despliegue final es una **Raspberry Pi 5** (BCM2712, quad Cortex-A76 @ 2.4 GHz,
8 GB LPDDR4X). El desarrollo ocurre en Windows 11 sobre x86_64. Se quiere que las
incompatibilidades de arquitectura aparezcan **ahora** y no frente al equipo del CIOp.

## Decisión

Todos los servicios se construyen y ejecutan como **`linux/arm64`** vía buildx + QEMU,
con límites de CPU y memoria que espejan la Pi 5, y una capa de abstracción de
hardware (HAL) con driver simulado para que el stack levante sin GPIO real.

Presupuesto de recursos declarado en el compose (total 4 CPU / 8 GB):

| Servicio | CPU | RAM |
|---|---|---|
| `controller` | 1.0 | 512 MB |
| `api` (gunicorn) | 1.0 | 1 GB |
| `ws` (daphne) | 0.5 | 768 MB |
| `mysql` | 1.0 | 2 GB |
| `redis` | 0.25 | 256 MB |
| `nginx` | 0.25 | 128 MB |

## Lo que esta simulación SÍ valida

- **Disponibilidad de wheels arm64.** Es el hallazgo más valioso: paquetes sin wheel
  precompilado para `aarch64` que obligan a compilar desde fuente (`mysqlclient`
  necesita `gcc` y `default-libmysqlclient-dev`; `cryptography` necesita Rust).
  Descubrirlo acá cuesta una tarde; descubrirlo en la Pi cuesta una visita.
- Existencia de las imágenes base para arm64 (mysql, redis, nginx, python).
- Corrección del `docker-compose.yml`: red, orden de arranque, healthchecks, volúmenes.
- Comportamiento de la aplicación bajo **límites de memoria** reales (OOM kills,
  tuning de `innodb_buffer_pool_size`).
- Que el sistema levanta sin GPIO, gracias al HAL simulado.

## Lo que esta simulación NO valida

> ⚠️ **Crítico: bajo emulación QEMU las mediciones de tiempo no son válidas.**

- **RNF001 (≤500 ms) no se puede verificar acá.** QEMU en modo usuario emula
  instrucción por instrucción: el costo típico para trabajo CPU-bound es de un orden
  de magnitud o más, y es irregular. Un test de latencia que pase bajo emulación **no
  prueba nada**, y uno que falle tampoco condena al diseño.
  **RNF001 se mide exclusivamente sobre hardware real.**
- Temporización del lazo de control y jitter del scheduler.
- GPIO, interrupciones, y el comportamiento del chip RP1 de la Pi 5.
- *Thermal throttling* — la Pi 5 lo hace bajo carga sostenida sin disipación activa.
- Características de I/O del almacenamiento (ver ADR-0006).
- Rendimiento real de la red y del stack TLS.

## Consecuencias

- Los builds son notablemente más lentos en el entorno de desarrollo. Se mitiga con
  caché de buildx y con un `docker-compose.override.yml` que permite correr en
  arquitectura nativa para el ciclo rápido de desarrollo.
- **Los tests se dividen en dos suites:**
  - `tests/` — funcionales y de contrato, corren en cualquier arquitectura y son las
    que cuentan para la cobertura de RNF007.
  - `tests/hardware/` — marcados `@pytest.mark.hardware`, **excluidos por defecto**,
    se corren solo sobre la Pi. Acá vive la verificación de RNF001.
- El plan de pruebas de aceptación debe indicar explícitamente que las mediciones de
  RNF001 se ejecutan sobre la Pi 5 de destino, con su disipación y almacenamiento
  definitivos.

## Requisito de entorno

Docker Desktop debe estar **corriendo** (no solo instalado) y con emulación binfmt
habilitada. Ver la sección de troubleshooting del README.
