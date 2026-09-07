# ADR-0002 — El lazo de control y el E-Stop viven fuera de Django

**Estado:** Aceptada
**Fecha:** 2026-09-07

## Contexto

RNF001 exige que los comandos críticos (parada de emergencia, actuadores) respondan
en **≤500 ms, medido en 10 pruebas consecutivas**. El camino del diagrama de clases es
`Umbral.evaluar() → ParadaEmergencia.ejecutar() → Componente.apagadoSeguro()`.

Además, el sistema necesita un lazo **continuo**: leer temperatura, caudal y potencia
y evaluar umbrales sin depender de que llegue un request.

Se evaluó implementarlo dentro de Django como una vista DRF.

## Decisión

Un **proceso `controller` independiente**, dueño exclusivo del GPIO, con un lazo
asyncio propio. Django nunca accede al hardware: se comunica con el controlador
por Redis (ADR-0004).

**Regla verificable:** `grep -ri gpio` sobre el código de Django debe dar cero
resultados. Es criterio de rechazo en code review.

El E-Stop de software es una capa **supervisora**. La parada de emergencia real es
un **interlock por hardware**: el botón físico en serie cortando el enable de la
fuente HV y el shutter, eléctricamente, sin software en el camino.

## Justificación

### 1. Gunicorn forkea workers y las líneas GPIO son exclusivas por proceso

En libgpiod, la línea la toma el primer proceso que la pide; el resto recibe `EBUSY`.
Con N workers los actuadores responderían o no según qué worker atendió el request.
No es una cuestión de estilo, es un **bug de corrección**. La única salida sería
correr un solo worker, lo que serializa toda la API: un reporte pesado bloquearía
el E-Stop, exactamente lo que se quiere evitar.

> Nota de hardware: en la **Pi 5** el `RPi.GPIO` clásico no funciona (cambió el chip
> a RP1). Hay que ir por `lgpio` / `gpiod` / `gpiozero` con backend lgpio.

### 2. Django no tiene dónde correr un lazo continuo

Es request-driven. Las alternativas serían un thread dentro del worker (se duplica
por worker, muere con `--max-requests`, no sobrevive el reload) o un management
command aparte — que ya *es* este ADR, sin diseñarlo.

### 3. Los 500 ms se miden en la cola, no en la mediana

El criterio son 10 pruebas consecutivas: lo que importa es el peor caso. Dentro de
Django la cola la arman:

| Fuente de latencia | Aporte a la cola |
|---|---|
| Worker ocupado (sync workers, request encolado) | **100 ms – varios segundos** |
| `fsync` de MySQL sobre microSD | 50–200 ms |
| Lock wait / flush de InnoDB | 100 ms+ |
| Auth de DRF (sesión o token = roundtrip a MySQL) | 5–40 ms |
| Pausa de GC gen2 | 10–50 ms |
| Contención del GIL con otro request en ORM | 5–50 ms |
| Handshake TLS sin keep-alive | 20–80 ms |

La mediana daría ~40 ms y parecería resuelto; el p99 bajo carga se va a 1–3 s. Es la
peor forma de fallar: pasa en las pruebas de escritorio y falla en la demo.

## Presupuesto de latencia con el controlador aparte

```
poll de GPIO a 100 Hz ................ ≤10 ms
evaluación de umbral .................  <1 ms
apagado ordenado (shutter→láser→HV) .. ~80 ms
─────────────────────────────────────────────
total software ....................... <150 ms   (margen 3× sobre RNF001)
```

Este presupuesto es **argumentable**, no solo medible: el lazo corre en un proceso
dedicado, sin ORM, sin GC de un stack web y sin competencia por el GIL.

## Consecuencias

### Positivas
- El presupuesto de latencia es defendible ante el CIOp.
- El controlador con un driver de hardware abstracto (HAL) se testea con un *fake*,
  de forma determinística, en CI, sobre x86 y sin la Pi. Abarata RNF007 en la parte
  crítica. Testear el E-Stop atravesando una vista DRF requiere levantar el stack web
  entero y no prueba lo que importa.
- Django puede escalar workers libremente sin tocar el hardware.

### Negativas y su mitigación
Se suma un proceso, un protocolo de IPC y un modo de falla nuevo: **si el controlador
muere, Django serviría telemetría vieja como si nada.** Mitigaciones, obligatorias
desde el día uno:

- Heartbeat del controlador en Redis con **TTL de 3 s**.
- El consumer verifica el heartbeat; si venció, el front muestra
  **SIN CONEXIÓN AL CONTROLADOR** y bloquea los comandos, en vez de mostrar
  números congelados.
- `restart: unless-stopped` en el compose.
- Y el punto de fondo: **si el controlador muere, el interlock por hardware sigue
  funcionando.** La seguridad no depende del proceso que se cayó.

## Nota normativa

Un E-Stop puramente por software no se sostiene como única capa de seguridad.
Las referencias son **ISO 13850** (función de parada de emergencia) e
**IEC 60204-1** (categorías de parada 0/1/2; el E-Stop debe ser categoría 0 o 1).

> ⚠️ Verificar el detalle de ambas normas antes de citarlas en un entregable formal.
> El punto de fondo es firme, la redacción exacta de las cláusulas hay que confirmarla.

Esto no contradice RNF001 — es **cómo** se cumple.

## Alternativa descartada

**Todo dentro de Django (E-Stop como vista DRF).** Menos piezas, pero se rompe en el
punto 1 antes de llegar siquiera a la discusión de latencia.
