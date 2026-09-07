# CO2_back — Backend del sistema de grabado láser

Backend del SCADA para el **grabado de LPGs (Long Period Gratings) con láser de CO₂**.
Despliegue de destino: **Raspberry Pi 5**.

Frontend: [`CO2_front`](https://github.com/Fedebravo12/CO2_front) — mockup React + Vite,
con la capa de API aislada en `src/mock/api.ts` lista para conectar.

---

## Estado actual

> **Esta entrega es infraestructura y decisiones. No hay lógica de dominio todavía.**

| | |
|---|---|
| ✅ | Infra completa: 6 contenedores `linux/arm64` con límites que espejan la Pi 5 |
| ✅ | Django + DRF arrancando, con `/api/healthz/` verificando MySQL, Redis y el controlador |
| ✅ | Terminación TLS y ruteo del split WSGI/ASGI en nginx |
| ✅ | Esqueleto del proceso controlador publicando heartbeat |
| ✅ | Decisiones de arquitectura documentadas en [`docs/adr/`](docs/adr/) |
| ⏳ | Modelo de dominio — **espera el Diagrama de Clases v4** |
| ⏳ | Lazo de control, HAL de hardware y E-Stop |
| ⏳ | Consumer de telemetría y endpoints que consume el front |

El modelo de dominio se dejó fuera a propósito: depende del diagrama de clases v4.
Escribirlo ahora contra una suposición significa reescribirlo después.

---

## Contexto del proyecto

### Requisitos que condicionan la arquitectura

| Requisito | Contenido | Dónde se resuelve |
|---|---|---|
| **RNF001** | Comandos críticos (E-Stop, actuadores) ≤ **500 ms** | Interlock por hardware + proceso `controller` — [ADR-0002](docs/adr/0002-proceso-controlador-separado.md) |
| **RF009** | Telemetría ≥ **1 Hz** | WebSocket sobre Redis pub/sub — [ADR-0003](docs/adr/0003-telemetria-websocket-split-wsgi-asgi.md) |
| **RNF003** | HTTPS entre front y back | Terminación TLS en nginx |
| **RNF007** | PEP8, cobertura ≥ **70 %**, docstrings en toda función pública | `ruff` + `pytest-cov` con el umbral en `pyproject.toml` |
| **RNF008** | MySQL, ≥ **10.000 ensayos** sin degradación | MySQL 8.4 + [ADR-0006](docs/adr/0006-almacenamiento-nvme-retencion.md) |

Trazabilidad completa en `Proyecto Final Nosotros/Matriz de Trazabilidad de Requisitos.xlsx`.

### Documentos relacionados

| Documento | Aporta |
|---|---|
| Actividad 4 — Alcance y Requisitos | Los RF/RNF y la fijación del stack |
| Actividad 4 — Entregables y EDT | El entregable 1.4 (backend) y su ubicación en el cronograma |
| Actividad 7 — Plan de Gestión del Presupuesto | Restricción de hardware — relevante para [ADR-0006](docs/adr/0006-almacenamiento-nvme-retencion.md) |
| Diagrama de Clases v4 | **Bloqueante** del modelo de dominio |

---

## Arquitectura

```
                          ┌──────────────┐
                          │  CO2_front   │  React + Vite
                          └──────┬───────┘
                                 │ HTTPS / WSS   (RNF003)
                          ┌──────▼───────┐
                          │    nginx     │  terminación TLS
                          └──┬────────┬──┘
                 /api/*      │        │   /ws/*
                  ┌──────────▼─┐   ┌──▼───────────┐
                  │  gunicorn  │   │    daphne    │
                  │   (WSGI)   │   │    (ASGI)    │
                  │ DRF síncr. │   │ 1 consumer   │
                  └──┬──────┬──┘   └──────┬───────┘
                     │      │             │
              ┌──────▼──┐   └──────┬──────┘
              │  MySQL  │          │
              └─────────┘   ┌──────▼──────┐
                            │    Redis    │  pub/sub · channel layer · heartbeat
                            └──────▲──────┘
                                   │
                          ┌────────┴─────────┐
                          │   controller     │  lazo de control · E-Stop
                          │   (asyncio)      │  dueño exclusivo del GPIO
                          └────────┬─────────┘
                                   │
                          ┌────────▼─────────┐
                          │   GPIO / RP1     │
                          └──────────────────┘
                                   ║
                          ═════════╩═════════
                           INTERLOCK HARDWARE
                        (botón E-Stop cableado,
                         independiente del software)
```

### Las tres decisiones que explican este diagrama

**1. El E-Stop no vive en Django** ([ADR-0002](docs/adr/0002-proceso-controlador-separado.md)).
Gunicorn forkea workers y las líneas GPIO son exclusivas por proceso: con N workers los
actuadores responderían o no según quién atendió el request. Además, la cola de latencia
de Django (worker ocupado, `fsync` de MySQL, GC, GIL) se va a segundos aunque la mediana
dé 40 ms. El presupuesto con el controlador aparte es **<150 ms**, con margen 3×.

**Regla verificable:** `grep -ri gpio` sobre el código de Django debe dar cero. Es criterio
de rechazo en code review.

**2. WebSocket, con el servidor partido en dos** ([ADR-0003](docs/adr/0003-telemetria-websocket-split-wsgi-asgi.md)).
El riesgo caro de Channels no es Redis, es migrar las vistas DRF sync a ASGI. Partiendo
el servidor por proceso, esa migración no ocurre: la API REST sigue siendo Django sync
de manual y la superficie async queda en un solo consumer.

**3. Redis es un bus único** ([ADR-0004](docs/adr/0004-redis-como-bus.md)).
Sirve al IPC del controlador, al channel layer de Channels y al heartbeat. Se paga una
vez. Bases lógicas separadas: `0` pub/sub, `1` channels, `2` heartbeat, `3` caché.

**El resto está en [`docs/adr/`](docs/adr/).** Antes de discutir una decisión de
arquitectura, leer el ADR correspondiente: el contexto y las alternativas descartadas
ya están escritos.

---

## Requisitos previos

- **Docker Desktop corriendo** (no solo instalado — ver troubleshooting)
- Git Bash u otro shell POSIX, para `gen-certs.sh`
- Para desarrollo fuera de contenedores: Python 3.12+

---

## Puesta en marcha

```bash
git clone https://github.com/FranciscoMontiron/CO2_back.git
cd CO2_back

# 1. Entorno. Editar .env y cambiar TODAS las contraseñas.
cp .env.example .env

# 2. Certificado autofirmado para desarrollo (RNF003) (terminal bash)
sh docker/nginx/gen-certs.sh

# 3. Levantar la simulación arm64 de la Pi 5
docker compose up -d --build
```

El primer build es **lento**: bajo emulación QEMU, `mysqlclient` se compila desde
fuente para `aarch64`. Es esperable, y es justamente el hallazgo que
[ADR-0005](docs/adr/0005-simulacion-arm64-qemu.md) busca provocar temprano.

### Verificación

```bash
docker compose ps                                   # los 6 servicios healthy
curl -k https://localhost:8443/api/healthz/         # estado de dependencias
```

Respuesta esperada:

```json
{
  "estado": "ok",
  "dependencias": {
    "mysql":       {"ok": true, "detalle": "ok"},
    "redis":       {"ok": true, "detalle": "ok"},
    "controlador": {"ok": true, "detalle": "ok"}
  }
}
```

| URL | Qué es |
|---|---|
| `https://localhost:8443/api/healthz/` | Estado de las dependencias |
| `https://localhost:8443/api/docs/` | Documentación interactiva de la API |
| `https://localhost:8443/api/schema/` | OpenAPI en crudo — el contrato para el front |
| `https://localhost:8443/admin/` | Admin de Django |

El browser va a advertir que el certificado no es de confianza: es lo esperable en un
autofirmado.

### Desarrollo rápido (arquitectura nativa)

QEMU hace lento el ciclo de desarrollo. Para iterar:

```bash
TARGET_PLATFORM=linux/amd64 docker compose \
  -f docker-compose.yml -f docker-compose.dev.yml up --build
```

En PowerShell: `$env:TARGET_PLATFORM="linux/amd64"` antes, o fijarlo en `.env`.

> Este modo **no valida compatibilidad arm64**. Antes de cerrar cada entrega hay que
> correr la simulación arm64.

---

## Servicios

| Servicio | Imagen / base | CPU | RAM | Rol |
|---|---|---|---|---|
| `nginx` | nginx 1.27-alpine | 0.25 | 128 MB | TLS (RNF003), ruteo `/api` vs `/ws` |
| `api` | python 3.12-slim | 1.0 | 1 GB | gunicorn/WSGI — REST síncrona |
| `ws` | *(misma imagen)* | 0.5 | 768 MB | daphne/ASGI — canal WebSocket |
| `controller` | python 3.12-slim | 1.0 | 512 MB | Lazo de control, E-Stop, GPIO |
| `mysql` | mysql 8.4 | 1.0 | 2 GB | Persistencia (RNF008) |
| `redis` | redis 7.4-alpine | 0.25 | 256 MB | Bus único (ADR-0004) |

Total: **4 CPU / ~4,6 GB**, dentro del presupuesto de una Pi 5 de 8 GB.

`api` y `ws` comparten la misma imagen y cambian solo el comando: la separación de
[ADR-0003](docs/adr/0003-telemetria-websocket-split-wsgi-asgi.md) es de **proceso**,
no de código.

---

## Estructura

```
CO2_back/
├── config/                      Proyecto Django
│   ├── settings/                base · dev · prod
│   ├── health.py                Healthcheck de dependencias
│   ├── urls.py                  Ruteo raíz (todo bajo /api/)
│   ├── wsgi.py                  → gunicorn, servicio `api`
│   └── asgi.py                  → daphne,   servicio `ws`
├── controller/                  Proceso controlador (ADR-0002)
│   └── main.py                  Esqueleto: solo heartbeat
├── docker/
│   ├── api/Dockerfile           Imagen compartida api + ws
│   ├── controller/Dockerfile    Imagen mínima del controlador
│   └── nginx/                   Plantilla de config + certificados
├── docs/adr/                    Decisiones de arquitectura
├── requirements/                base · dev · controller
├── tests/                       Suite funcional (RNF007)
├── docker-compose.yml           Simulación arm64 de la Pi 5
├── docker-compose.dev.yml       Override nativo para desarrollo
└── pyproject.toml               ruff · black · pytest · coverage
```

---

## Calidad (RNF007)

El requisito está codificado en `pyproject.toml`, no delegado a la disciplina del equipo.

```bash
docker compose exec api ruff check .          # PEP8 + docstrings
docker compose exec api black --check .       # formato
docker compose exec api pytest                # tests + cobertura >= 70%
```

`ruff` corre con la regla `D` (pydocstyle, convención Google): la ausencia de un docstring
en una función pública **falla el lint**. Es la traducción literal de RNF007.

### Las dos suites de tests

| Suite | Dónde corre | Qué verifica |
|---|---|---|
| `tests/` | Cualquier arquitectura, en CI | Contratos y comportamiento. Cuenta para la cobertura de RNF007. |
| `tests/hardware/` | **Solo la Pi 5 real** | RNF001 y el GPIO. Marcada `@pytest.mark.hardware`, excluida por defecto. |

> ⚠️ **RNF001 no se puede verificar bajo emulación QEMU.** La emulación distorsiona los
> tiempos de forma irregular: un test de latencia que pase ahí no prueba nada, y uno que
> falle tampoco condena al diseño. **Las mediciones de RNF001 se hacen sobre la Pi 5 de
> destino**, con su disipación y almacenamiento definitivos.
> Detalle en [ADR-0005](docs/adr/0005-simulacion-arm64-qemu.md).

---

## Conectar el front

El front tiene la capa de API aislada en `src/mock/api.ts` y el estado en vivo en
`src/context/SystemContext.tsx`. La conexión se hace en dos lugares:

1. **REST** — reemplazar el cuerpo de las funciones de `src/mock/api.ts` por `fetch('/api/...')`,
   manteniendo las mismas firmas. El contrato está en `/api/schema/`.
2. **Telemetría** — un hook `useTelemetria()` que reemplaza el `setInterval` de
   `SystemContext.tsx`. El resto de la app consume `telemetria` del contexto y no se
   entera del transporte.

Ese aislamiento es deliberado: es lo que hace que
[la decisión de transporte sea reversible en ~1 día](docs/adr/0003-telemetria-websocket-split-wsgi-asgi.md).

Cuando el controlador esté caído (heartbeat vencido), el front debe mostrar
**SIN CONEXIÓN AL CONTROLADOR** y bloquear los comandos — nunca telemetría congelada
como si fuera actual.

---

## Troubleshooting

**`error during connect: open //./pipe/dockerDesktopLinuxEngine`**
Docker Desktop está instalado pero el engine no corre. Abrir Docker Desktop y esperar a
que el ícono quede en verde.

**El build de `mysqlclient` falla o tarda muchísimo**
Esperable en arm64 emulado: no hay wheel para `aarch64` y se compila desde fuente. Si
falla, verificar que `build-essential`, `pkg-config` y `default-libmysqlclient-dev` estén
en la etapa `builder` de `docker/api/Dockerfile`.

**`nginx: [emerg] cannot load certificate`**
Falta generar los certificados: `sh docker/nginx/gen-certs.sh`.

**`mysql` no pasa a healthy**
El primer arranque inicializa la base y bajo emulación puede tardar varios minutos. El
`start_period` es de 60 s. Revisar con `docker compose logs -f mysql`.

**El healthcheck reporta `controlador: false`**
El heartbeat venció (TTL 3 s). Revisar `docker compose logs controller`. No tumba la API
a propósito: [ADR-0002](docs/adr/0002-proceso-controlador-separado.md).

---

## Próximos pasos

1. **Diagrama de Clases** → desbloquea el modelo de dominio y las migraciones.
2. **HAL de hardware** con driver simulado y driver GPIO tras la misma interfaz.
3. **Lazo de control y E-Stop** en `controller/`, con el presupuesto de latencia medido
   sobre la Pi real.
4. **Consumer de telemetría** y el hook del front.
5. **Endpoints que consume `src/mock/api.ts`**, contra el contrato de `/api/schema/`.
6. **Confirmar [ADR-0006](docs/adr/0006-almacenamiento-nvme-retencion.md)** — NVMe y
   política de retención. 
