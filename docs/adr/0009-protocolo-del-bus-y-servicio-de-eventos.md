# ADR-0009 — Protocolo del bus y servicio de eventos

**Estado:** Aceptada
**Fecha:** 2026-10-04
**Completa a:** [ADR-0004](0004-redis-como-bus.md), que sigue vigente.

## Contexto

Con el controlador ejecutando programas (lazo, E-Stop, HAL simulado), aparecieron dos
necesidades que ADR-0004 dejaba abiertas:

1. **Comandos que no se pueden perder.** ADR-0004 ya advertía que el pub/sub de Redis es
   *fire-and-forget* y que eso *"es incorrecto para comandos y alarmas"*. Un comando de
   emergencia que se pierde porque el controlador estaba reconectando no es aceptable.
2. **Persistir lo que pasa en el arreglo sin que el controlador toque MySQL.** Una corrida
   genera marcas, checkpoints, alertas, una emergencia o un espectro final, y todo eso es
   trazabilidad de RF006/RF007. Pero ADR-0002 prohíbe que el controlador dependa del ORM o
   de la base: cada dependencia es latencia y jitter que RNF001 no puede pagar.

## Decisión

### 1. Cada cosa por el mecanismo que le corresponde

| Qué | Mecanismo | Por qué |
|---|---|---|
| **Comandos** (API → controlador) | Stream `co2:comandos` | El stream conserva el comando hasta que se lee. |
| **Eventos** (controlador → Django) | Stream `co2:eventos` + grupo de consumo | Si el servicio que persiste se reinicia, retoma los eventos que no confirmó. |
| **Telemetría** (controlador → front) | Pub/sub `co2:telemetria` | Efímera ([ADR-0007](0007-telemetria-en-vivo-sin-persistencia.md)): el dato viejo no sirve. |
| **Última telemetría** | Clave con TTL de 5 s | La API valida comandos contra ella; si el controlador muere, vence sola. |
| **Umbrales vigentes** | Clave `co2:configuracion` | Django los publica; el controlador los lee sin tocar MySQL. |

Los nombres viven en **un solo archivo**, `controller/protocolo.py`, sin dependencias: lo
usa el controlador y lo importa Django. No hay dos listas que puedan desincronizarse.

### 2. Un servicio de eventos, aparte

`python manage.py escuchar_controlador` corre como su propio contenedor (`eventos`): lee el
stream de eventos y los guarda con el ORM. Es el único lugar donde lo que pasa en el arreglo
se convierte en filas. Además publica los umbrales vigentes cada 5 s.

### 3. El controlador arranca leyendo solo comandos nuevos

Lee el stream de comandos desde `$`. Un controlador que se reinicia **no ejecuta órdenes que
quedaron en el stream**: encender un láser que alguien pidió hace una hora sería peor que
perder el comando.

### 4. La API valida antes de encolar

Un comando a un controlador sin heartbeat se rechaza con `503` en el momento, y un `iniciar`
se valida contra la última telemetría (sistema LISTO, láser encendido, sin bloqueos). Si
igual el controlador lo rechaza —el estado cambió en el medio—, emite un evento `abortado`
para que el registro del ensayo no quede «en curso» para siempre.

## Consecuencias

- **Ningún evento de trazabilidad depende de que haya un navegador abierto.** El WebSocket
  es solo para mirar; la persistencia la hace el servicio de eventos.
- **Un contenedor más** (`eventos`, 0,25 CPU / 256 MB). Se paga una vez y aísla la escritura
  en MySQL del lazo de control.
- **La persistencia es asíncrona.** Entre que el controlador termina un programa y que el
  ensayo figura `COMPLETADO` pasan unos cientos de milisegundos. El front lo contempla:
  reintenta la consulta del ensayo hasta que deja de estar «en curso».
- **Entrega al menos una vez.** Si el servicio cae entre guardar y confirmar, el evento se
  reprocesa. Los manejadores que lo necesitan son idempotentes (una marca se identifica por
  red y número).
- **RNF001 se mide de punta a punta.** La API sella `t_envio` en cada comando, y el
  controlador calcula `tiempo_respuesta_ms` desde ahí: incluye API, Redis y controlador, más
  la latencia nominal de los actuadores. En la simulación da ~55–60 ms; la medición que
  cuenta sigue siendo la de la Pi real ([ADR-0005](0005-simulacion-arm64-qemu.md)).

## Alternativas descartadas

**Pub/sub para todo.** Es lo que proponía ADR-0004 y lo que el propio ADR marcaba como
incorrecto para comandos: un consumidor desconectado pierde los mensajes de ese intervalo.

**Que el controlador escriba en MySQL.** Rompe ADR-0002 y mete el `fsync` de InnoDB en el
proceso que tiene que responder en 500 ms.

**Persistir desde el consumer del WebSocket.** Solo corre si hay alguien mirando: un ensayo
sin pestañas abiertas no quedaría registrado.
