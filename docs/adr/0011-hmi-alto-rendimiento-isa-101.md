# ADR-0011 — La interfaz sigue los principios de HMI de alto rendimiento (ISA-101)

**Estado:** Propuesta
**Fecha:** 2026-11-03
**Rama:** `feature/estandar-hmi` (front para el código, back para este ADR)
**Relacionada con:** ADR-0002 (E-Stop, ISO 13850 / IEC 60204-1).

## Resumen para quien (o qué IA) llegue sin contexto

- El diseño visual del dashboard **no se había basado en ningún estándar de HMI**. Venía de un estilo de dashboard moderno: cada variable con su color, verde para "todo bien", brillos neón.
- Se adopta como referencia **ANSI/ISA-101.01** ("High Performance HMI"). Su idea central: **en operación normal la pantalla es neutra y el color queda reservado para lo anormal**, siempre con forma y texto además del color.
- Alcance de esta etapa: **dashboard de telemetría y cabecera** (estado del sistema). El resto de las pantallas queda pendiente (ver "Fuera de alcance").

## Contexto

Con colores permanentes por variable (potencia azul, alta tensión naranja, sensor de sombra amarillo), el color dejaba de significar algo. El amarillo y el naranja, que en cualquier HMI industrial indican advertencia, aparecían con valores normales. El rojo de las líneas de límite estaba siempre visible aunque nada estuviera fuera de rango. Un operador no podía detectar de un vistazo qué estaba mal.

### Fuentes y advertencia

ISA-101.01 es un documento pago y **no se leyó el texto oficial**. Los principios aplicados salen de fuentes secundarias que coinciden entre sí:

- Control.com, "Going Gray: A New HMI Standard": https://control.com/technical-articles/going-gray/
- ISA Interchange, "The High Performance HMI": https://blog.isa.org/the-high-performance-hmi (no se pudo leer: redirige a automation.com, que bloquea el acceso automático).
- Guías de fabricantes e integradores (Tatsoft, IPD, Industrial Monitor Direct), que coinciden en: fondo gris, color solo para lo anormal, codificación redundante (color + forma + texto) y animación reservada para alarmas.

> ⚠️ Antes de citar ISA-101 o ISA-18.2 en un entregable formal, verificar contra el texto oficial. La paleta concreta (valores hex) es decisión nuestra: las fuentes coinciden en que **ISA-101 no impone una paleta fija**.

## Decisión

1. **Valores y tendencias en neutro.** Los valores en operación normal se muestran en el color de texto. Las tendencias usan grises (`--trend`, `--trend-strong`). Los límites se dibujan como líneas grises discontinuas.
2. **Cinco tonos de estado** (`Tone` en `Telemetria.tsx`):
   - `normal`: estado esperado. Badge con borde gris, sin relleno.
   - `ok`: condición confirmada como correcta (fibra alineada, refrigeración estable). Verde **+ check**, **sin relleno**, para que quede por debajo de `warn` y `alarm`, que sí llevan relleno. Ver "Ajuste: verde para lo correcto".
   - `activo`: equipo energizado o en marcha (alta tensión activa, láser encendido, shutter abierto, desplazador en curso). **Borde de tarjeta verde y badge con relleno verde.** El relleno lo distingue de `ok`.
   - `warn`: condición anormal que requiere atención. Amarillo **+ triángulo**.
   - `alarm`: variable fuera de su límite configurado. Rojo **+ octógono** en el badge y junto al valor. Pisa al estado del equipo.
3. **Codificación redundante.** Ningún estado depende solo del color. Los badges llevan texto y, si son anormales, ícono. En la cabecera, cada estado del sistema tiene su propio ícono (reposo, preparación, listo, grabando, sin conexión) y el texto queda en neutro, salvo **LISTO, que va en verde**. El rojo se reserva para emergencia y sin conexión.
4. **Sin brillos ni animación en estados normales.** Se quitaron los resplandores y el pulso del estado "preparación".
5. **La parada de emergencia mantiene su rojo** (ISO 13850, ADR-0002): es un mando de seguridad, no un indicador de proceso.
6. **Dos temas.** El claro es el más cercano a la recomendación de fondo gris claro. El oscuro se mantiene para ambientes con poca luz, con la misma regla de color.

### Ajuste: verde para lo correcto

Con la pantalla totalmente neutra costaba confirmar de un vistazo que el sistema estaba en condiciones. Se agrega un verde acotado, siguiendo IEC 60073 (verde = condición normal / segura):

- **Condición correcta (`ok`)**: LISTO, Alineada, Estable. Verde sin relleno + ícono de check.
- **Equipo encendido o en marcha (`activo`)**: láser encendido, alta tensión activa, shutter abierto, desplazador en curso. Borde de tarjeta verde y badge con relleno verde. Fue una decisión explícita del equipo: se prioriza que el operador vea de un vistazo qué está en uso.
- Ninguno de los dos lleva relleno en la tarjeta ni animación, para no restarle peso al amarillo y al rojo.

## Consecuencias

- `Telemetria.tsx`: los canales ya no tienen campo `color`. El color del valor depende solo de si está fuera de límite.
- `Telemetria.css` e `index.css`: tokens nuevos de tendencia y clases `activo`, `warn`, `alarm`. Desaparece `info` del dashboard; `ok` vuelve solo para condiciones confirmadas (ver "Ajuste: verde para lo correcto").
- Si se agrega un canal nuevo, **no se le asigna color**: hereda la regla neutra.
- La pantalla en normal se ve "apagada" a propósito. Es lo que hace que una advertencia salte a la vista.

## Fuera de alcance (pendiente)

- **Página de Alertas / Eventos según ISA-18.2 / IEC 62682**: prioridades, estados de alarma y reconocimiento.
- **Resto de pantallas** (nuevo ensayo, control manual, simulador, barra lateral): todavía usan colores de acento. El verde de la navegación activa es decorativo y conviene neutralizarlo.
- **Advertencia por cercanía al límite** (por ejemplo, al 90 %): requiere definir umbrales de pre-alarma en el back.

## Alternativas descartadas

- **IEC 60073 como regla principal** (verde = normal). Es válida para indicadores luminosos, pero llenar la pantalla de verde le quita peso a los colores de alarma. Se usa como respaldo para el rojo de emergencia y para el verde acotado de lo correcto.
- **Mantener el estilo anterior y solo mejorar el contraste.** Resolvía la legibilidad, no el problema de fondo: el color no comunicaba estado.
