# ADR-0003 — Telemetría por WebSocket con servidor WSGI/ASGI separado

**Estado:** Aceptada
**Fecha:** 2026-09-07

## Contexto

RF009 pide telemetría a **≥1 Hz** en el dashboard. Django síncrono no hace push, así
que había tres caminos: polling REST, SSE o WebSocket (Django Channels).

Dato que condiciona el análisis: por ADR-0004 **Redis ya está en el proyecto** como
IPC del controlador. Eso elimina el mayor costo de infraestructura de Channels, que
era justamente traer Redis.

## Decisión

**WebSocket con Django Channels**, pero con el servidor **partido en dos**:

```
nginx (TLS, RNF003)
 ├─ /api/*  → gunicorn WSGI  → DRF sync (comandos, CRUD, ensayos, auth)
 └─ /ws/*   → daphne  ASGI   → 1 consumer (telemetría + alarmas + estado)
        ↕
      Redis  ← pub/sub ← proceso controlador (ADR-0002)
```

## Justificación del split

El riesgo más caro de Channels no es Redis ni el consumo: es la **migración
WSGI → ASGI de la API existente**. Las vistas DRF sync pasarían a correr en el
threadpool de ASGI, lo que cambia el manejo de conexiones a MySQL (thread-locals),
el comportamiento de `CONN_MAX_AGE` y el agotamiento del pool con transacciones largas.

Partiendo el servidor, **esa migración simplemente no ocurre**:

1. La API REST nunca toca ASGI. Sigue siendo Django sync de manual: fácil de testear,
   PEP8 limpio, cobertura barata (RNF007).
2. La superficie async queda de ~150 líneas en un solo consumer. Es lo que se audita
   con cuidado; el resto del backend no cambia.
3. Costo marginal de infraestructura: **cero**, Redis ya estaba.
4. La API REST igual puede empujar al canal con
   `async_to_sync(get_channel_layer().group_send)(...)` desde código sync.
   Es el patrón estándar de Channels, no un workaround.

## Lo que WebSocket NO aporta

Conviene dejarlo escrito para que nadie lo argumente mal en la defensa:

- **No mejora la latencia del E-Stop.** RNF001 se cumple por el interlock de hardware
  más el controlador (ADR-0002). El canal web no está en el camino crítico con
  ninguna de las tres opciones.
- **No mejora la latencia de comandos de forma perceptible.** El Control Manual del
  front son toggles discretos (chiller, HV, shutter, láser) más una posición: un
  `POST /api/comandos` sobre HTTP/2 keep-alive en LAN da 5–15 ms. WS ahorraría ~3 ms.

## Lo que sí aporta

- Es el estándar de facto en SCADA: no requiere justificación ante el CIOp.
- Un solo canal multiplexa telemetría, alarmas, cambios de estado y progreso de pasos,
  en vez de SSE para uno y polling para el resto.
- Deja el camino abierto a jog continuo o ajuste de potencia en vivo si aparecen.

## El costo en la Pi 5 no decide

Pi 5 = Cortex-A76 quad @ 2.4 GHz. Con `1 Hz × 10 vars × 5 clientes ≈ 8 KB/s`:

| | RAM | CPU en régimen |
|---|---|---|
| Redis | ~15 MB | <1 % |
| Daphne + Channels | ~110 MB | 2–4 % |
| Gunicorn sync | ~90 MB | 2–3 % |

La diferencia entre WS y SSE es ~20 MB y ~1 % de CPU. **El hardware no decide esta
elección**; la deciden el presupuesto de tiempo y de riesgo.

## Cláusula de reversibilidad

La decisión se diseña para poder revertirse en **~1 día**:

- **Backend:** el consumer solo se suscribe a Redis y serializa. Toda la lógica vive
  en el controlador. Cambiar WS por SSE = reescribir un archivo.
- **Front:** un hook `useTelemetria()` que reemplaza el `setInterval` de
  `src/context/SystemContext.tsx`. El resto de la app consume `telemetria` del
  contexto y no se entera del transporte.

**Timebox: 3 días.** Si al día 3 el handshake con auth o `wss://` detrás de nginx
siguen sin cerrar, se implementa SSE (`StreamingHttpResponse` sobre el mismo Redis,
~40 líneas) y se sigue. No se arriesga el cronograma por una decisión de transporte.

## Costos asumidos

| Costo | Detalle |
|---|---|
| Auth en el handshake | El browser no manda headers en un WebSocket. Hace falta middleware propio (token por query-string o subprotocolo). Cuidar que nginx no logee la URL con el token. |
| Reverse proxy | `wss://` necesita `Upgrade`/`Connection` explícitos y `proxy_read_timeout` alto, o nginx corta a los 60 s. |
| Reconexión a mano | `EventSource` reconecta solo; en WS hay que escribir backoff exponencial + ping/pong. Sin heartbeat, un NAT deja conexiones zombie "abiertas" por las que no llega nada. |
| Tests | `WebsocketCommunicator` + `pytest-asyncio`. Más trabajo que testear un generador SSE. |
| Deploy | Un contenedor más (daphne) y un healthcheck más. |

## Alternativas descartadas

- **Polling REST a 1 Hz.** Lo más literal respecto de RF009 y lo más simple, pero
  desperdicia CPU y ancho de banda, y no escala a los otros tipos de evento.
- **SSE.** Muy buena opción y la de menor riesgo; queda como **plan de contingencia
  explícito** del timebox de arriba, no descartada del todo.
