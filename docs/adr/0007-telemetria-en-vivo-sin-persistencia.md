# ADR-0007 — La telemetría es efímera: se transmite, no se persiste

**Estado:** Aceptada
**Fecha:** 2026-10-03
**Reemplaza a:** [ADR-0006](0006-almacenamiento-nvme-retencion.md)

## Contexto

[ADR-0006](0006-almacenamiento-nvme-retencion.md) asumió que la telemetría de RF009
debía **persistirse** muestra a muestra, y dimensionó el almacenamiento para eso:

```
1 Hz × 10 variables × 86.400 s/día = 864.000 muestras/día  →  4,4–31 GB/año
```

De ese supuesto salía todo lo demás: el modelo `MuestraTelemetria`, la política de
retención en dos niveles, el consumidor de escritura en lote y la exigencia de
arrancar desde NVMe — que a su vez quedó bloqueada esperando presupuesto.

Al cerrar el diagrama de clases se revisó el supuesto y **no se sostiene**:

1. **RF009 habla del dashboard, no del almacenamiento.** Pide telemetría a ≥1 Hz
   *en pantalla*. El canal en vivo (ADR-0003 + ADR-0004) ya lo cumple entero sin
   tocar MySQL.
2. **No es lo que el laboratorio conserva.** Lo que se registra de cada fabricación
   es el **resultado analizado** — espectro, resonancias, marcas, parámetros — el
   mismo conjunto que hoy se lleva a mano. Nadie consulta la serie de temperatura
   segundo a segundo de un ensayo de hace tres meses.
3. **El diagrama de clases no tiene ninguna entidad de telemetría**, y no por olvido:
   persistir la serie era una decisión de infraestructura que el modelo de dominio
   nunca pidió.

## Decisión

### 1. La telemetría es efímera

`controller` → Redis pub/sub → consumer → WebSocket → front. **Nunca toca MySQL.**
No existe `MuestraTelemetria` ni ninguna tabla equivalente. Si nadie está mirando el
dashboard, la muestra se descarta: es correcto, el dato viejo no sirve (ADR-0004).

### 2. Lo que se persiste es el resultado analizado

Las entidades del diagrama de clases: `Lote`, `Red` (espectro y curva),
`PicoDeAtenuacion`, `Marca`, `RegistroDeFabricacion`. Es el registro de trazabilidad
de RF006/RF007 y se conserva íntegro.

### 3. La trazabilidad del proceso va por `Checkpoint`, no por la serie

`Checkpoint` ya registra `estadoSistema` en los puntos de control del ensayo. Se le
suman **los valores medidos en ese instante**. Eso responde *"¿a qué temperatura y
potencia se grabó esta red?"* con **un puñado de filas por ensayo** en lugar de
86.400 por día.

Es el punto medio deliberado: analizable, sin ser masivo.

### 4. Las variables monitoreadas quedan fijadas

Lo que ADR-0006 dejó pendiente ("el diagrama de clases lo debe fijar").

**Analógicas** — son las que admiten `Umbral` (`valorMin`/`valorMax`):

| Variable | Unidad | Origen | Por qué se monitorea |
|---|---|---|---|
| `TEMPERATURA_AGUA` | °C | Refrigeración | Interlock del tubo de CO₂ |
| `CAUDAL_REFRIGERANTE` | L/min | Flow switch | Sin caudal el tubo se destruye |
| `POTENCIA_LASER` | W | `Laser` | Contraste contra `Programa.potenciaObjetivo` |
| `TENSION_AT` | kV | `FuenteAt.tension` | Ya existe como atributo |
| `CORRIENTE_AT` | mA | `FuenteAt` | Indicador real de descarga: la tensión sola no dice si conduce |
| `POSICION_MOTOR` | mm | `Motor` | La necesitan el front y el lazo para la marca siguiente |
| `TEMPERATURA_AMBIENTE` | °C | Ambiente | Contexto; sale gratis |

**Digitales** — interlocks booleanos, **no** llevan `Umbral`:

| Señal | Origen |
|---|---|
| `FIBRA_ALINEADA` | `SensorSombra.alineado()` |
| `SHUTTER_ABIERTO` | `ISensorMagnetico` #1 |
| `SHUTTER_CERRADO` | `ISensorMagnetico` #2 |

Son 7 + 3 = **10**, que confirma la estimación de ADR-0006.

La distinción importa: `Umbral.evaluar(valor: float)` solo tiene sentido sobre las
analógicas. Las digitales son condiciones de seguridad y se evalúan como checklist
de armado, no como rango.

### 5. `Espectro` pasa a ser una clase, y el volumen deja de estar en la fila de `Red`

> **Parcialmente reemplazada por [ADR-0008](0008-espectro-guarda-longitudes-de-onda.md).**
> Que `Espectro` sea una clase en tabla propia sigue vigente. Lo que cambió es que
> ahora **sí guarda las longitudes de onda**: el primer CSV real del laboratorio vino
> con paso variable y la reconstrucción por grilla regular de abajo quedó inválida.

Sacada la telemetría, el volumen dominante pasa a ser el espectro del OSA. Hoy vive
como `Red.espectro: String`, y eso tiene dos problemas: uno de modelado y uno de
rendimiento.

**El de modelado es el que manda.** El diagrama ya usa un tipo `Espectro` que nunca
define — `Ploteador.graficarEspectro(e: Espectro): Imagen`. O sea que el modelo ya
trata al espectro como objeto, pero `Red` lo guarda aplastado en un campo de texto.
Promoverlo a clase no agrega complejidad: hace explícito lo que el diagrama ya asume.

```
Red       1 ──── 0..1  Espectro
Espectro  *  ──── 1    Procedimiento
```

El `0..1` es deliberado: una red recién fabricada **todavía no fue caracterizada**.
Fabricación y medición son momentos distintos, y forzar `1` haría imposible registrar
una red escrita pero aún sin medir.

La asociación con `Procedimiento` cae sola: `span`, `resolucion`, `sensibilidad`,
`escalaVertical` y `corrienteSLD` ya describen la receta de medición OSA + SLD. El
`Espectro` es su resultado, y `Procedimiento.importar(csv)` pasa a tener un retorno
natural.

**Atributos de `Espectro`:** `fechaCaptura: DateTime`, `longitudOndaInicial: float [nm]`
y el arreglo ordenado de transmitancias.

**No se guardan las longitudes de onda.** El OSA barre en grilla regular, así que
`λ_i = longitudOndaInicial + i × Procedimiento.resolucion`. Es exacto, no una
aproximación, y ahorra la mitad del volumen.

**Al ser clase propia, los datos viven en su propia tabla.** La fila de `Red` queda
flaca y ninguna consulta sobre `Red` arrastra el espectro. Eso resuelve el problema de
rendimiento sin `FileField` ni directorio de media — y sin partir el backup en dos,
que para RF006/RF007 es importante: restaurar la base sin la carpeta de archivos
dejaría la trazabilidad rota.

`PicoDeAtenuacion` **se queda colgando de `Red`**: con una sola captura por red no hay
ambigüedad sobre de qué espectro salió cada pico.

Volumen resultante, con `puntos = span / resolucion`:

| Configuración | Puntos | Por espectro | × 10.000 redes |
|---|---|---|---|
| span 100 nm, res 0,05 nm | 2.000 | ~36 KB | ~360 MB |
| span 200 nm, res 0,01 nm | 20.000 | ~360 KB | ~3,6 GB |

Guardando solo transmitancias y comprimido, baja entre 5× y 10×.

## Consecuencias

### El NVMe deja de ser bloqueante

Era la consecuencia cara de ADR-0006: la escritura continua 24/7 con `fsync` destruye
las microSD de consumo en meses. **Sin persistencia de telemetría esa carga
desaparece**: quedan las escrituras de fabricación, que son esporádicas y acotadas.

El NVMe sigue siendo **deseable** por rendimiento de MySQL, pero ya no es un requisito
de vida útil del hardware. El punto 1 de "Pendiente de confirmar" de ADR-0006 —
*¿entra la HAT NVMe en el presupuesto de la Actividad 7?* — **deja de bloquear**.

### RNF008 deja de estar dominado por la telemetría

Los ≥10.000 ensayos pasan a medirse en metadatos y espectros, no en 31 GB/año de
muestras. El orden de magnitud baja de decenas de GB a cientos de MB.

### Se pierde el post-mortem de la serie completa

Es la contrapartida honesta: **no se puede reconstruir qué pasó segundo a segundo
durante un ensayo pasado**. Se mitiga con `Checkpoint` (punto 3), que cubre el caso
de uso real de trazabilidad.

Si en el futuro hiciera falta, la vuelta atrás es barata: el bus ya publica todo, así
que alcanza con un consumidor que grabe **solo durante la ventana del ensayo activo**.
No se pierde información que el sistema no esté produciendo ya.

### La política de retención desaparece

Sin serie continua no hay nada que agregar ni que podar. Los eventos, alarmas y
metadatos de ensayo se conservan íntegros y para siempre, que es lo que ADR-0006 ya
decía de ellos.

## Alternativas descartadas

**Persistir a 1 Hz con NVMe (ADR-0006).** Arrastra costo de hardware, el modelo ancho,
el consumidor de escritura en lote y una política de retención en dos niveles con
agregación — toda esa maquinaria para un dato que el laboratorio no consulta.

**Persistir solo durante ensayos activos.** Reduce el volumen, pero **no** reduce la
complejidad: sigue haciendo falta el modelo, el consumidor y la decisión de retención.
Se paga casi todo el costo para la mitad del beneficio. Queda registrada como la
vuelta atrás natural si el requisito aparece.

**Bajar la persistencia a 0,1 Hz.** Conserva lo peor de los dos mundos: una serie
demasiado gruesa para diagnosticar y demasiado fina para ignorar.

## Pendiente de confirmar

1. **¿Se mide el espectro una sola vez por red?** Se asumió que sí. Si el laboratorio
   vuelve a caracterizar redes viejas, la multiplicidad pasa a `0..*` y
   `PicoDeAtenuacion` debe colgar de `Espectro` y no de `Red` — los picos salen de una
   captura concreta. **Haberlo modelado como clase hace que ese cambio sea quitar una
   restricción de unicidad, no rehacer el esquema.**
2. **`Red.curva: String`** — si es la curva suavizada con la que `detectarResonancias()`
   encuentra los picos, es **derivada** y no debería persistirse: se recalcula. Marcarla
   `/curva` o eliminarla. Si es otra cosa, el nombre no lo dice.
3. **`Resonancia` vs `PicoDeAtenuacion`** — `Red.resonanciaPrincipal(): Resonancia`
   devuelve un tipo que no existe como clase. Es casi seguro el `PicoDeAtenuacion` con
   `principal = true`. Unificar el nombre.
4. **¿Se conserva el CSV original del OSA** como evidencia, además de los valores
   parseados? Depende de si el CIOp lo considera parte del registro de trazabilidad.
5. **Qué valores exactos guarda cada `Checkpoint`** — ¿las 7 analógicas, o un
   subconjunto?
