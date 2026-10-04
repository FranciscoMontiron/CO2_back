# CO2_back — Backend del sistema de grabado láser

Backend del SCADA para el **grabado de LPGs (Long Period Gratings) con láser de CO₂**.
Despliegue de destino: **Raspberry Pi 5**.

Frontend: [`CO2_front`](https://github.com/Fedebravo12/CO2_front) — mockup React + Vite,
con la capa de API aislada en `src/mock/api.ts` lista para conectar.

---

## Estado actual

> **Back y front integrados.** Con un `docker compose up` se ve el sistema completo en
> `https://localhost:8443`: login, historial de ensayos, detalle con el espectro real del
> OSA, programas, alertas y administración.

| | |
|---|---|
| ✅ | Infra: 7 contenedores `linux/arm64` con límites que espejan la Pi 5, front incluido |
| ✅ | Modelo de dominio completo (M1), con auditoría automática (RN010) |
| ✅ | API REST con sesión JWT y permisos por rol — contrato en `/api/docs/` |
| ✅ | Front conectado: todo lo que muestra sale de la API, salvo lo que se indica abajo |
| ✅ | Decisiones de arquitectura en [`docs/adr/`](docs/adr/) |
| ⏳ | **Simulado en el front:** telemetría en vivo, E-Stop y el procedimiento de «Nuevo ensayo» |
| ⏳ | Lazo de control, HAL de hardware y E-Stop real (M3/M4) |
| ⏳ | Canal WebSocket de telemetría (M4) |

Lo simulado depende del proceso controlador, que todavía solo publica su heartbeat. El
indicador de conexión del front sí es real: consulta `/api/healthz/`.

---

## Contexto del proyecto

### Requisitos que condicionan la arquitectura

| Requisito | Contenido | Dónde se resuelve |
|---|---|---|
| **RNF001** | Comandos críticos (E-Stop, actuadores) ≤ **500 ms** | Interlock por hardware + proceso `controller` — [ADR-0002](docs/adr/0002-proceso-controlador-separado.md) |
| **RF009** | Telemetría ≥ **1 Hz** | WebSocket sobre Redis pub/sub — [ADR-0003](docs/adr/0003-telemetria-websocket-split-wsgi-asgi.md) |
| **RNF003** | HTTPS entre front y back | Terminación TLS en nginx |
| **RNF007** | PEP8, cobertura ≥ **70 %**, docstrings en toda función pública | `ruff` + `pytest-cov` con el umbral en `pyproject.toml` |
| **RNF008** | MySQL, ≥ **10.000 ensayos** sin degradación | MySQL 8.4 + [ADR-0007](docs/adr/0007-telemetria-en-vivo-sin-persistencia.md) |

Trazabilidad completa en `Proyecto Final Nosotros/Matriz de Trazabilidad de Requisitos.xlsx`.

### Documentos relacionados

| Documento | Aporta |
|---|---|
| Actividad 4 — Alcance y Requisitos | Los RF/RNF y la fijación del stack |
| Actividad 4 — Entregables y EDT | El entregable 1.4 (backend) y su ubicación en el cronograma |
| Actividad 7 — Plan de Gestión del Presupuesto | Restricción de hardware. El NVMe dejó de ser bloqueante — ver [ADR-0007](docs/adr/0007-telemetria-en-vivo-sin-persistencia.md) |
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
- **El repo del front clonado al lado de este**: `../CO2_front`. El compose lo construye
  desde ahí. Si está en otro lado, fijar `FRONT_DIR` en `.env`.
- Git Bash u otro shell POSIX, para `gen-certs.sh`
- Para desarrollo fuera de contenedores: Python 3.12+

---

## Puesta en marcha

```bash
# Los dos repos, uno al lado del otro
git clone https://github.com/FranciscoMontiron/CO2_back.git
git clone https://github.com/Fedebravo12/CO2_front.git
cd CO2_back

# 1. Entorno. Editar .env y cambiar TODAS las contraseñas.
cp .env.example .env

# 2. Certificado autofirmado para desarrollo (RNF003) (terminal bash)
sh docker/nginx/gen-certs.sh

# 3. Levantar la simulación arm64 de la Pi 5 (back + front)
docker compose up -d --build

# 4. Base de datos y datos de demostración
docker compose exec api python manage.py migrate
docker compose exec api python manage.py cargar_datos_iniciales --con-ejemplo
```

Abrir **https://localhost:8443** e ingresar con `admin`, `investigador` u `operador`
(la contraseña es igual al usuario). Cada uno ve lo que su rol permite.

> Los usuarios de demo los crea `--con-ejemplo`. **No usar esa opción en un despliegue
> real**: las contraseñas son públicas.

El primer build es **lento**: bajo emulación QEMU, `mysqlclient` se compila desde
fuente para `aarch64`. Es esperable, y es justamente el hallazgo que
[ADR-0005](docs/adr/0005-simulacion-arm64-qemu.md) busca provocar temprano.

### Verificación

```bash
docker compose ps                                   # los 7 servicios healthy
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
| `https://localhost:8443/` | **El sistema** (front) |
| `https://localhost:8443/api/healthz/` | Estado de las dependencias |
| `https://localhost:8443/api/docs/` | Documentación interactiva de la API |
| `https://localhost:8443/api/schema/` | OpenAPI en crudo — el contrato para el front |
| `https://localhost:8443/admin/` | Admin de Django |

El browser va a advertir que el certificado no es de confianza: es lo esperable en un
autofirmado. Si un antivirus bloquea la página, ver troubleshooting.

### Desarrollo rápido (arquitectura nativa)

QEMU hace lento el ciclo de desarrollo. Para iterar:

```bash
TARGET_PLATFORM=linux/amd64 docker compose \
  -f docker-compose.yml -f docker-compose.dev.yml up --build
```

En PowerShell: `$env:TARGET_PLATFORM="linux/amd64"` antes, o fijarlo en `.env`.

> Este modo **no valida compatibilidad arm64**. Antes de cerrar cada entrega hay que
> correr la simulación arm64.

Para el front con recarga en caliente, en el repo `CO2_front`: `npm run dev`. Vite sirve
en `http://localhost:5173` y reenvía `/api` a este nginx, así que necesita el backend
levantado.

---

## Servicios

| Servicio | Imagen / base | CPU | RAM | Rol |
|---|---|---|---|---|
| `nginx` | nginx 1.27-alpine | 0.25 | 128 MB | TLS (RNF003), ruteo `/api`, `/ws`, `/admin` y front |
| `front` | nginx 1.27-alpine | 0.25 | 64 MB | Estáticos de la SPA de React (repo `CO2_front`) |
| `api` | python 3.12-slim | 1.0 | 1 GB | gunicorn/WSGI — REST síncrona |
| `ws` | *(misma imagen)* | 0.5 | 768 MB | daphne/ASGI — canal WebSocket |
| `controller` | python 3.12-slim | 1.0 | 512 MB | Lazo de control, E-Stop, GPIO |
| `mysql` | mysql 8.4 | 1.0 | 2 GB | Persistencia (RNF008) |
| `redis` | redis 7.4-alpine | 0.25 | 256 MB | Bus único (ADR-0004) |

Total: **4,25 CPU / ~4,7 GB**, dentro del presupuesto de una Pi 5 de 8 GB.

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

## Integración con el front

El front vive en [`CO2_front`](https://github.com/Fedebravo12/CO2_front). El nginx de este
repo lo sirve en `/` y la API en `/api/`, **desde el mismo origen**: no hay CORS, y el
front usa rutas relativas igual que detrás del proxy de Vite.

- **Capa de API** — `src/api/client.ts` en el front. Traduce el contrato de este backend
  (snake_case, fechas ISO, unidades en el nombre del campo) a los tipos que ya usaban las
  pantallas. Si la API cambia, se toca ese archivo y no las páginas.
- **Sesión** — JWT. El token de acceso dura 30 min y el front lo renueva solo; el de
  refresco dura 12 h. Van en `sessionStorage`: la PC del laboratorio es compartida y
  cerrar el navegador tiene que cerrar la sesión.
- **Telemetría** — sigue simulada en `src/context/SystemContext.tsx`. Cuando exista el
  canal WebSocket, se reemplaza el `setInterval` por un hook y el resto de la app no se
  entera del transporte ([ADR-0003](docs/adr/0003-telemetria-websocket-split-wsgi-asgi.md)).

Cuando el controlador esté caído (heartbeat vencido), el front lo muestra en la barra
lateral. Al conectar los comandos reales, además tiene que **bloquearlos** — nunca
telemetría congelada como si fuera actual.

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

**El antivirus bloquea `https://localhost:8443`** (por ejemplo, Kaspersky: *«Se evitó la
visita a un sitio web no confiable»*)
El antivirus intercepta el HTTPS y rechaza el certificado autofirmado. `curl -k` funciona
porque no pasa por ese filtro. Dos salidas:
- Aceptar el riesgo desde la página del antivirus, o agregar `localhost` a sus exclusiones.
- Confiar en el certificado de desarrollo para tu usuario de Windows (PowerShell):
  `certutil -addstore -user Root docker\nginx\certs\co2.crt`. Ojo: la clave privada queda
  en el disco, así que conviene quitarlo cuando termines (`certmgr.msc` → *Entidades de
  certificación raíz de confianza*).

**`pytest` falla con `Access denied ... to database 'test_co2'`**
El volumen de MySQL es anterior al script de `docker/mysql/initdb/`, que solo corre al
inicializar un volumen vacío. `docker compose down -v` y volver a levantar (borra la base).

**El front muestra pantallas vacías o «No se pudo conectar con el servidor»**
Revisar `docker compose ps`: `api` tiene que estar healthy. Si se levantó la base desde
cero, faltan `migrate` y `cargar_datos_iniciales`.

**`mysql` no pasa a healthy**
El primer arranque inicializa la base y bajo emulación puede tardar varios minutos. El
`start_period` es de 60 s. Revisar con `docker compose logs -f mysql`.

**El healthcheck reporta `controlador: false`**
El heartbeat venció (TTL 3 s). Revisar `docker compose logs controller`. No tumba la API
a propósito: [ADR-0002](docs/adr/0002-proceso-controlador-separado.md).

---

## Próximos pasos

1. **HAL de hardware** con driver simulado y driver GPIO tras la misma interfaz (#21, #22).
2. **Lazo de control y E-Stop** en `controller/`, con el presupuesto de latencia medido
   sobre la Pi real (#23-#25, #28).
3. **Canal WebSocket de telemetría** y el hook del front que reemplaza la simulación
   (#26, #27, #29).
4. **Confirmar con el CIOp** si una red se caracteriza una sola vez y en qué unidad se
   expresa la potencia objetivo (mW en el front, W en el modelo).
