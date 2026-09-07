# ADR-0006 — Arranque desde NVMe y política de retención de telemetría

**Estado:** **Propuesta** — requiere confirmación de presupuesto de hardware
**Fecha:** 2026-09-07

## Contexto

RF009 pide telemetría a 1 Hz. Con ~10 variables monitoreadas:

```
1 Hz × 10 variables × 86.400 s/día = 864.000 muestras/día
```

RNF008 exige además soportar **≥10.000 ensayos sin degradación**. El volumen dominante
no son los ensayos: es la telemetría.

## Estimación de volumen

> Valores estimados, a confirmar con el esquema definitivo del diagrama de clases v4.

Con un modelo **angosto** (una fila por muestra: `ensayo_id`, `timestamp`,
`variable_id`, `valor`), contando overhead de InnoDB e índices, unos ~100 B efectivos
por fila:

| Modelo | Filas/día | Volumen/día | Volumen/año |
|---|---|---|---|
| Angosto (1 fila por variable) | 864.000 | ~86 MB | ~31 GB |
| **Ancho** (1 fila por instante, 10 columnas) | **86.400** | ~12 MB | ~4,4 GB |

## Decisión propuesta

### 1. La Pi 5 arranca desde **NVMe/SSD**, no desde microSD

Las microSD de consumo tienen resistencia de escritura acotada, y las escrituras
pequeñas y aleatorias con `fsync` (exactamente el patrón de un `INSERT` de InnoDB)
sufren amplificación de escritura severa. Una carga de escritura continua 24/7 sobre
microSD tiene una vida útil que se mide en meses, y **falla de forma silenciosa y
progresiva**: primero se corrompen sectores, después la base.

La Pi 5 tiene conector PCIe; con una HAT NVMe el problema desaparece y además mejora
mucho el rendimiento de MySQL.

**Esto cambia el presupuesto de hardware, por eso el ADR está en estado Propuesta.**

### 2. Modelo **ancho** para `MuestraTelemetria`

Una fila por instante con una columna por variable. Reduce 10× el conteo de filas,
el overhead de índices y el volumen. Se pierde flexibilidad para agregar variables
(requiere migración), lo cual es aceptable: el conjunto de variables lo fija el
hardware y no cambia seguido.

### 3. Escritura **en lote**, fuera del camino caliente

El controlador publica a Redis a 1 Hz; un consumidor separado acumula y hace
`bulk_create` cada N segundos. La telemetría en vivo del front **nunca** pasa por
MySQL (ADR-0004). Esto reduce los `fsync` en un orden de magnitud.

### 4. Política de retención en dos niveles

| Antigüedad | Resolución conservada |
|---|---|
| 0 – 90 días | Completa, 1 Hz |
| > 90 días | Agregados por minuto (mín/máx/promedio) + eventos y alarmas completos |

Los **eventos, alarmas y metadatos de ensayo no se agregan nunca**: son el registro de
trazabilidad y se conservan íntegros.

## Pendiente de confirmar

1. **Presupuesto**: ¿entra la HAT NVMe + el SSD en el presupuesto de hardware
   (Actividad 7)? Si no entra, hay que re-discutir 1 Hz de persistencia continua.
2. **Retención**: ¿90 días de resolución completa satisface al CIOp, o hay un
   requisito regulatorio o de trazabilidad que exija más?
3. **Variables**: el conteo de 10 es una estimación tomada del mockup del front. El
   diagrama de clases v4 lo debe fijar.

## Consecuencias si se rechaza la propuesta

Si se mantiene el arranque desde microSD, hay que bajar la frecuencia de
**persistencia** (no la de visualización) a 0,1 Hz o registrar solo durante ensayos
activos. RF009 habla de telemetría a 1 Hz en el dashboard, lo cual se sigue cumpliendo
por el canal en vivo: no exige persistir cada muestra.
