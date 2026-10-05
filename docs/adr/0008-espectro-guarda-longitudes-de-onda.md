# ADR-0008 — El espectro guarda sus longitudes de onda

**Estado:** Aceptada
**Fecha:** 2026-10-04
**Reemplaza a:** la decisión 5 de [ADR-0007](0007-telemetria-en-vivo-sin-persistencia.md), solo en lo que respecta al eje de longitudes de onda. El resto de ADR-0007 sigue vigente.

## Contexto

ADR-0007 decidió que `Espectro` **no** guardara las longitudes de onda: el OSA barre
en grilla regular, así que alcanzaba con la longitud inicial y la resolución del
procedimiento para reconstruir el eje (`λᵢ = inicial + i × resolución`). Ahorraba la
mitad del volumen, y una salvaguarda rechazaba cualquier CSV cuyo paso no coincidiera
con la resolución.

Al integrar con el front apareció el primer CSV real del laboratorio
(`L05LPG01.csv`, exportado de un **Yokogawa AQ6370B**). El encabezado declara 25.001
muestras a 0,02 nm, pero el trazo exportado trae **117 puntos con cinco pasos
distintos**:

| Paso | Veces |
|---|---|
| 10 nm | 41 |
| 5 nm | 3 |
| 3 nm | 1 |
| 2 nm | 1 |
| 1 nm | 70 |

Está diezmado de forma no uniforme: grueso donde la curva es plana, fino alrededor de
la resonancia (el tramo de 1 nm empieza en 1601 nm; la resonancia principal está en
1610 nm). Con ese archivo:

1. La reconstrucción del eje **es imposible**: no hay un paso único.
2. La salvaguarda de M1 **rechazaba el archivo real del laboratorio**.

## Decisión

`Espectro` guarda las longitudes de onda y las transmitancias como **dos arreglos
paralelos** (`longitudes_onda`, `transmitancias`). La importación ya no compara el paso
contra la resolución del procedimiento: solo exige que las longitudes de onda sean
estrictamente crecientes.

El detector de resonancias baja su filtro de ancho mínimo a **1 punto** por defecto. En
un CSV diezmado una resonancia real puede quedar representada por un único punto: el
valle secundario de 1200 nm del archivo de ejemplo cae en la zona de paso 10 nm y tiene
un solo punto bajo el umbral.

## Consecuencias

- **El volumen por espectro se duplica.** Sigue siendo irrelevante: el peor caso de
  ADR-0007 (20.000 puntos por red, 10.000 redes) pasa de ~3,6 GB a ~7 GB, y un CSV
  diezmado como el de ejemplo pesa unos pocos KB.
- **El dato guardado es exactamente el que exportó el instrumento.** No hay una fórmula
  intermedia que pueda quedar desincronizada de la medición.
- Se pierde el filtro de ruido por ancho en grillas finas. Sigue disponible como
  parámetro (`ancho_minimo`) para quien lo necesite.
- La migración `fabricacion.0003` convierte los espectros ya cargados reconstruyendo su
  eje con la fórmula de M1 **antes** de borrar la columna vieja.

## Lo que muestra este ADR

La decisión de ADR-0007 era razonable con la información que había: se apoyaba en cómo
*debería* exportar un OSA. Se cayó con el primer archivo real. Vale como regla para lo
que sigue: los supuestos sobre datos del laboratorio se validan contra un archivo del
laboratorio, no contra la hoja de datos del instrumento.

## Pendiente

- El encabezado del AQ6370B trae `CTRWL`, `SPAN`, `RESLN` y `SMPL`. Se podría completar
  el `Procedimiento` automáticamente al importar en vez de cargarlo a mano.
