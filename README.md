# CO2_back — Backend del sistema de grabado láser

Backend del SCADA para el **grabado de LPGs (Long Period Gratings) con láser de CO₂**.
Despliegue de destino: **Raspberry Pi 5**.

Frontend: [`CO2_front`](https://github.com/Fedebravo12/CO2_front) — React + Vite,
conectado a la API REST y a la telemetría por WebSocket.

**Para levantar el proyecto completo desde cero, seguir [Puesta en marcha](#puesta-en-marcha).**
El Docker Compose de este repositorio construye y levanta tanto el back como el front.

---

## Estado actual

> **El circuito completo funciona contra un laboratorio simulado.** Desde la interfaz se
> arma el equipo paso a paso, se ejecuta un programa en tiempo real con telemetría en vivo,
> y el ensayo queda guardado con su red caracterizada. Las fallas provocadas disparan la
> parada de emergencia sola. Ver [Simular el laboratorio](#simular-el-laboratorio).

| | |
|---|---|
| ✅ | Infra: 8 contenedores `linux/arm64` con límites que espejan la Pi 5, front incluido |
| ✅ | Modelo de dominio completo (M1), con auditoría automática (RN010) |
| ✅ | API REST con sesión JWT y permisos por rol — contrato en `/api/docs/` |
| ✅ | Controlador: máquina de estados, ejecución de programas, umbrales y E-Stop de software |
| ✅ | Telemetría en vivo a 1 Hz por WebSocket (RF009), con detección de datos congelados |
| ✅ | Servicio de eventos que persiste marcas, checkpoints, alertas y emergencias ([ADR-0009](docs/adr/0009-protocolo-del-bus-y-servicio-de-eventos.md)) |
| ⏳ | **HAL de GPIO para la Pi 5** (#22): hoy el hardware es un modelo físico simulado |
| ⏳ | Medición de RNF001 sobre la Pi real (#31) |

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

- **Git** para clonar ambos repositorios.
- **Docker con Compose v2** (`docker compose version`). En Windows y macOS, Docker
  Desktop corriendo; en Windows, usar el motor WSL2 y contenedores Linux.
- **El repo del front clonado al lado de este**: `../CO2_front`. El compose lo construye
  desde ahí. Si está en otro lado, fijar `FRONT_DIR` en `.env`.
- Git Bash con OpenSSL en Windows, o un shell POSIX con OpenSSL en Linux/macOS,
  para `gen-certs.sh`.
- Para levantar todo con Docker **no hace falta instalar Python, Node.js, MySQL ni
  Redis en la computadora**: se ejecutan dentro de los contenedores.
- Para desarrollo fuera de contenedores: Python 3.12+

---

## Puesta en marcha

### 1. Descargar los dos repositorios

Ejecutar desde una carpeta vacía que contenga el proyecto (PowerShell, Git Bash o
terminal de Linux/macOS):

```bash
git clone --branch main https://github.com/FranciscoMontiron/CO2_back.git
git clone --branch main https://github.com/Fedebravo12/CO2_front.git
cd CO2_back
```

La estructura debe quedar así; no clonar el front dentro del back:

```text
proyecto/
├── CO2_back/
└── CO2_front/
```

Si se descargan ZIP de GitHub, renombrar `CO2_back-main` y `CO2_front-main` con los
nombres anteriores, o ajustar `FRONT_DIR` en `.env`.

### 2. Configurar el entorno del back

En Git Bash o Linux/macOS:

```bash
cp .env.example .env
```

En PowerShell:

```powershell
Copy-Item .env.example .env
```

Editar `.env`: cambiar `DJANGO_SECRET_KEY`, `MYSQL_PASSWORD` y
`MYSQL_ROOT_PASSWORD`. Mantener `CONTROLLER_HAL=simulado` para probar sin hardware.
Para una PC Intel/AMD, usar `TARGET_PLATFORM=linux/amd64` para evitar emulación;
para Raspberry Pi 5 o validar ARM64, usar `linux/arm64` (valor de la plantilla).
Mantener `FRONT_DIR=../CO2_front` si se respetó la estructura anterior.

El front servido por Docker no necesita un `.env` propio: usa `/api/` y `/ws/`
desde el mismo origen que la interfaz.

### 3. Generar el certificado de desarrollo

Desde `CO2_back`, en **Git Bash** (Windows) o terminal de Linux/macOS:

```bash
sh docker/nginx/gen-certs.sh
```

Si se venían ejecutando los pasos en PowerShell, abrir Git Bash en esa misma
carpeta para este comando y luego volver a PowerShell.

### 4. Inicializar la base y levantar el sistema

Desde `CO2_back`, en cualquiera de las terminales anteriores:

```bash
# Primero las dependencias y la API; esperar a que estén saludables.
docker compose up -d --build --wait mysql redis controller api

# Crear las tablas, los datos de demo y los estáticos del admin.
docker compose exec api python manage.py migrate
docker compose exec api python manage.py cargar_datos_iniciales --con-ejemplo
docker compose exec api python manage.py collectstatic --noinput

# Ahora levantar el resto, incluido el front y el servicio de eventos.
docker compose up -d --build --wait
```

Inicializar primero evita que el servicio `eventos` consulte tablas que todavía
no existen. Si un comando falla, revisar sus logs antes de continuar.

Abrir **https://localhost:8443** y aceptar el certificado autofirmado de desarrollo.
Cada usuario ve lo que su rol permite:

| Usuario | Contraseña | Rol |
|---|---|---|
| `admin` | `admin` | Administrador |
| `investigador` | `investigador` | Investigador |
| `operador` | `operador` | Operador |

> Los usuarios de demo los crea `--con-ejemplo`. **No usar esa opción en un despliegue
> real**: las contraseñas son públicas.

El primer build es **lento**: bajo emulación QEMU, `mysqlclient` se compila desde
fuente para `aarch64`. Es esperable, y es justamente el hallazgo que
[ADR-0005](docs/adr/0005-simulacion-arm64-qemu.md) busca provocar temprano.

### Verificación

```bash
docker compose ps                                  # los 8 servicios healthy
curl -k https://localhost:8443/api/healthz/          # estado de dependencias
```

En PowerShell usar `curl.exe -k https://localhost:8443/api/healthz/` para evitar
el alias de `Invoke-WebRequest`. El healthcheck verifica conectividad: las tablas
y los usuarios se crean con los comandos del paso 4.

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

Para el front con recarga en caliente, instalar Node.js 22.12+ y, en `CO2_front`,
ejecutar `npm ci` y luego `npx vite`. Abrir `http://localhost:5173`: Vite reenvía
`/api` y `/ws` al nginx del back, que debe seguir levantado. Ver la
[guía del front](https://github.com/Fedebravo12/CO2_front#readme) para los scripts
y la configuración de un backend en otro puerto.

### Detener y volver a levantar

Desde `CO2_back`:

```bash
docker compose stop       # detener sin borrar los datos
docker compose up -d --wait # volver a levantar con la configuración habitual
docker compose logs -f api ws eventos controller nginx
```

Si se usa el override de desarrollo, agregar `-f docker-compose.yml
-f docker-compose.dev.yml` a todos los comandos de Compose de esa sesión.
Después de actualizar el código de ambos repositorios, reconstruir con
`docker compose up -d --build --wait mysql redis controller api`, aplicar `migrate`
y `collectstatic --noinput`, y ejecutar `docker compose up -d --build --wait`.
No hace falta volver a copiar `.env` ni cargar los datos de demo.
`docker compose down` también conserva los datos; `docker compose down -v`
**borra los volúmenes y la base de datos**.

---

## Simular el laboratorio

Con `CONTROLLER_HAL=simulado` (el valor por defecto), el controlador maneja un **modelo
físico del arreglo** en lugar de los GPIO de la Pi: el agua tarda en enfriarse, la alta
tensión sube en rampa, el motor se desplaza a 2 mm/s, la fibra tarda unos segundos en
alinearse. Al terminar un programa, el interrogador óptico simulado mide el espectro de la
red a partir de la física de una LPG: la resonancia cae en `λ = Δn × período` y se hace más
profunda con más marcas. Todo lo demás —API, eventos, base, WebSocket, front— es el sistema
real.

### Un ensayo de principio a fin, desde la interfaz

1. Levantar el stack y cargar los datos de demo (ver [Puesta en marcha](#puesta-en-marcha)).
2. Entrar a `https://localhost:8443` como `operador` / `operador`.
3. Ir a **Nuevo ensayo** y seguir los 9 pasos. Cada uno tiene su botón de acción, y el
   estado del hardware y las validaciones que puede confirmar un sensor se actualizan solos:

   | Paso | Acción | Qué se ve |
   |---|---|---|
   | 2. Refrigeración | *Encender refrigeración* | El caudal sube y el agua se estabiliza (~1,5 s) |
   | 4. Alta tensión | *Habilitar alta tensión* | Rampa hasta ~18 kV (~2,5 s). Sin refrigeración, se rechaza |
   | 5. Sensor de sombra | *Alinear fibra* | La lectura baja de 2,4 a 0,12 mW (~3,5 s) |
   | 6. Shutter | *Armar shutter* | El sistema pasa a **LISTO** solo, al cumplirse las 4 condiciones |
   | 7. Láser y lazo | *Encender láser y cerrar lazo* | Solo se puede con el sistema LISTO (RN003) |
   | 8. Loop de grabado | *Iniciar grabado* | Pulso a pulso en tiempo real: el Programa A son 18 pulsos en ~12 s |
   | 9. Finalización | *Apagar en orden inverso* | Vuelve a **REPOSO** |

   Las validaciones que no puede confirmar un sensor (por ejemplo, el interlock físico de
   la fuente) las tilda el operador, como en el laboratorio.
4. Al terminar el paso 8 aparece el ensayo con su resultado y un enlace a su detalle: la
   curva del espectro, las marcas grabadas y la resonancia detectada.

Durante el grabado, **Telemetría** muestra los valores en vivo, y en el historial el ensayo
figura `En curso` hasta que termina.

### Probar la seguridad

- **E-Stop:** el botón rojo de la barra superior. El sistema apaga shutter, láser y alta
  tensión en ese orden, y el aviso muestra el tiempo de respuesta. *Rearmar sistema* lo
  devuelve a reposo; el rearme es siempre manual.
- **Fallas:** en **Control manual → Simulación de fallas**.
  - *Pérdida de caudal* con la alta tensión habilitada: la parada salta sola en ~2 s.
  - *Sobretemperatura* durante un grabado: el agua cruza 28 °C en ~7 s y el ensayo queda
    `Interrumpido`.

  Las dos quedan asentadas en **Alertas / Eventos**, con la alerta, la sugerencia de qué
  revisar y el tiempo de respuesta contra los 500 ms de RNF001.
- **Sin controlador:** `docker compose stop controller`. En menos de 4 s el front muestra
  **SIN CONEXIÓN AL CONTROLADOR** y bloquea los comandos. `docker compose start controller`
  lo recupera solo.

### Lo mismo, sin navegador

```bash
python scripts/simular_ensayo.py
python scripts/simular_ensayo.py --programa PRG-003 --falla sobretemperatura
```

Arma el equipo, graba, muestra la telemetría pulso a pulso y el ensayo resultante. Sirve para
una demo rápida y para verificar que el circuito anda después de un cambio.

### Para tener en cuenta

- El tiempo es **real**, como en el laboratorio. Para demos con programas largos,
  `CONTROLLER_SIM_VELOCIDAD=10` en `.env` acelera la simulación diez veces.
- Los tiempos de respuesta (~55 ms) son de la simulación. **RNF001 se mide sobre la Pi real**
  ([ADR-0005](docs/adr/0005-simulacion-arm64-qemu.md)).
- Que una red salga inviable puede ser física, no un error: con el Programa C (600 µm) la
  resonancia principal cae en ~1752 nm, fuera de la ventana del OSA (1170–1670 nm).

---

## Servicios

| Servicio | Imagen / base | CPU | RAM | Rol |
|---|---|---|---|---|
| `nginx` | nginx 1.27-alpine | 0.25 | 128 MB | TLS (RNF003), ruteo `/api`, `/ws`, `/admin` y front |
| `front` | nginx 1.27-alpine | 0.25 | 64 MB | Estáticos de la SPA de React (repo `CO2_front`) |
| `api` | python 3.12-slim | 1.0 | 1 GB | gunicorn/WSGI — REST síncrona |
| `ws` | *(misma imagen)* | 0.5 | 768 MB | daphne/ASGI — canal WebSocket |
| `controller` | python 3.12-slim | 1.0 | 512 MB | Lazo de control, E-Stop, HAL (simulado o GPIO) |
| `eventos` | *(imagen de `api`)* | 0.25 | 256 MB | Persiste los eventos del controlador ([ADR-0009](docs/adr/0009-protocolo-del-bus-y-servicio-de-eventos.md)) |
| `mysql` | mysql 8.4 | 1.0 | 2 GB | Persistencia (RNF008) |
| `redis` | redis 7.4-alpine | 0.25 | 256 MB | Bus único (ADR-0004) |

Total: **4,5 CPU / ~5 GB**, dentro del presupuesto de una Pi 5 de 8 GB.

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
│   └── main.py                  Lazo de control y laboratorio simulado
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
- **Telemetría** — `src/hooks/useTelemetriaControlador.ts`, por WebSocket en `/ws/telemetria/`
  ([ADR-0003](docs/adr/0003-telemetria-websocket-split-wsgi-asgi.md)). El JWT viaja en la
  query string porque el navegador no permite headers en el handshake. Se reconecta sola.
- **Comandos** — `src/api/control.ts`, por la API REST, que los valida y los audita. El
  resultado no vuelve en la respuesta: se ve en la telemetría.

Si la telemetría deja de llegar por más de 3,5 s, el front muestra **SIN CONEXIÓN AL
CONTROLADOR** y bloquea los comandos — nunca telemetría congelada como si fuera actual.

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

1. **HAL de GPIO para la Pi 5** (#22), detrás de la misma interfaz que el simulado.
2. **Medir RNF001 sobre la Pi real** (#31), con el E-Stop de punta a punta.
3. **Ajustar el simulador con datos del laboratorio**: tiempos de estabilización, Δn
   efectivo de los modos y criterio de viabilidad (hoy, ≥ 5 dB de profundidad).
4. **Confirmar con el CIOp** si una red se caracteriza una sola vez y en qué unidad se
   expresa la potencia objetivo (mW en el front, W en el modelo).
