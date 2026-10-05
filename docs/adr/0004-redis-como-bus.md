# ADR-0004 — Redis como bus único

**Estado:** Aceptada
**Fecha:** 2026-09-07

## Contexto

Tres necesidades distintas aparecieron por separado:

1. IPC entre el proceso `controller` y Django (ADR-0002).
2. Channel layer para Django Channels (ADR-0003).
3. Detección de caída del controlador (heartbeat).

## Decisión

**Una sola instancia de Redis** cubre las tres, con bases lógicas separadas:

| DB | Uso |
|---|---|
| `0` | Pub/sub de telemetría y eventos (controlador → consumer) |
| `1` | Channel layer de Django Channels |
| `2` | Heartbeat del controlador y estado efímero |
| `3` | Caché de Django |

## Flujo de datos

```
controller  --publish-->  redis/0  --subscribe-->  consumer WS  -->  front
     |                                                  ^
     |                                                  |
     +--> heartbeat (SETEX ttl=3s) --> redis/2 ---------+
     ^
     |
   api REST --publish comandos--> redis/0
```

**La telemetría no pasa por MySQL.** El controlador publica a pub/sub y el consumer
reenvía al front. No hay consumidor de persistencia: la telemetría es efímera y no se
guarda ninguna muestra ([ADR-0007](0007-telemetria-en-vivo-sin-persistencia.md)).

Eso convierte a Redis en el **único** camino de la telemetría, y por lo tanto en una
pieza más crítica que antes, no menos: sin él no hay dato en pantalla. Lo que no
cambia es la seguridad — el interlock de hardware no pasa por acá (ADR-0002).

## Consecuencias

- Redis se paga una vez y sirve a tres propósitos. Costo real medido: ~15 MB de RAM.
- Es una **dependencia dura**: si Redis cae, no hay telemetría ni comandos por la web.
  Aceptable porque el interlock de hardware no depende de él (ADR-0002).
- `appendonly no` y `save ""`: **Redis acá es volátil a propósito.** Nada de lo que
  guarda debe sobrevivir un reinicio, y desactivar la persistencia evita escrituras
  innecesarias sobre el almacenamiento.
- Pub/sub de Redis es *fire-and-forget*: un consumer desconectado pierde los mensajes
  de ese intervalo. Es correcto para telemetría (el dato viejo no sirve) e **incorrecto
  para comandos y alarmas**, que necesitan confirmación por otra vía.

> **Resuelto en [ADR-0009](0009-protocolo-del-bus-y-servicio-de-eventos.md):** los comandos
> y los eventos viajan por **streams** de Redis, que conservan el mensaje hasta que se lee.
> El pub/sub queda solo para la telemetría.

## Alternativas descartadas

- **ZeroMQ / nanomsg.** Menor latencia, pero no sirve como channel layer de Channels:
  habría que mantener dos infraestructuras de mensajería.
- **MQTT (Mosquitto).** Es el estándar del mundo IoT y sería defendible, pero suma un
  broker que no aporta sobre Redis en un despliegue de un solo nodo, y tampoco resuelve
  el channel layer.
- **Archivo/socket Unix compartido.** Sin channel layer, sin pub/sub multi-consumidor,
  y hay que escribir el protocolo a mano.
