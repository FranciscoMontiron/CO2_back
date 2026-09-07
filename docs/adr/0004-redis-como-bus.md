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

**La telemetría no pasa por MySQL en el camino al front.** El controlador publica a
pub/sub y el consumer reenvía. La persistencia en MySQL es un consumidor aparte que
escribe **en lote**, no en el camino caliente. Esto ataca directamente el desgaste
de escritura de la tarjeta (ver ADR-0006).

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

## Alternativas descartadas

- **ZeroMQ / nanomsg.** Menor latencia, pero no sirve como channel layer de Channels:
  habría que mantener dos infraestructuras de mensajería.
- **MQTT (Mosquitto).** Es el estándar del mundo IoT y sería defendible, pero suma un
  broker que no aporta sobre Redis en un despliegue de un solo nodo, y tampoco resuelve
  el channel layer.
- **Archivo/socket Unix compartido.** Sin channel layer, sin pub/sub multi-consumidor,
  y hay que escribir el protocolo a mano.
