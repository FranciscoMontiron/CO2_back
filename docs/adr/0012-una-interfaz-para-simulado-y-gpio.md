# ADR-0012 — Una sola interfaz para el hardware simulado y el GPIO: el modo cambia los datos, no las pantallas

**Estado:** Propuesta
**Fecha:** 2026-11-20
**Rama:** `develop` (front para el código, back para este ADR)
**Relacionada con:** ADR-0002 (proceso controlador), ADR-0005 (simulación), ADR-0011 (HMI de alto rendimiento).

## Resumen para quien (o qué IA) llegue sin contexto

- El controlador corre con uno de dos HAL: **simulado** (modelo físico con componentes fijos) o **GPIO** (Raspberry Pi 5 o su simulador `pi-sim`, con los componentes que se configuran en Administración).
- Al integrar los componentes GPIO, el front mostraba **otras pantallas** en modo GPIO: el dashboard y Control manual se reemplazaban por una pantalla genérica sin el diseño del resto ni los criterios de ADR-0011.
- Se decide que **el front tiene una sola interfaz**. El modo del HAL solo cambia **de dónde salen los datos** y **qué filas se arman**, nunca el diseño ni la estructura de la pantalla.

## Contexto

El back ya estaba pensado así. En modo GPIO, el controlador publica **el mismo mensaje de telemetría** que en modo simulado (`variables`, `actuadores`, `senales`, `extra`, `condiciones`), con el campo `modo_hal` para distinguirlo. Los valores salen de los **roles** de los componentes (`controller/roles.py`): el componente con rol `TEMPERATURA_AGUA` alimenta `variables.TEMPERATURA_AGUA`, el de rol `LASER` alimenta `actuadores.laser`, y así. Lo que ningún componente alimenta llega en `null`.

El front, en cambio, resolvía el modo GPIO con desvíos del tipo `if (modo_hal === 'gpio') return <OtraPantalla />`. Eso:

- Duplicaba pantallas y dejaba la del modo GPIO sin el diseño del sistema: títulos HTML sueltos, estilos en línea, colores fijos.
- Rompía ADR-0011 justo en el modo que se usa con el equipo real: el operador veía una interfaz distinta a la que practicó con el simulador.
- Ocultaba que los dos modos comparten el mismo contrato de datos.

## Decisión

1. **No hay pantallas por modo.** Una pantalla no hace `return` de otra según `modo_hal`. Si un modo necesita algo distinto, se resuelve **dentro** de la misma pantalla y con sus mismos componentes visuales.
2. **Dashboard: mismo para los dos modos.** Cada tarjeta declara de qué datos se alimenta (`fuentes` en `canalesTelemetria.tsx`). En modo GPIO, si todas sus fuentes llegan en `null` (ningún componente con ese rol), la tarjeta muestra **«—» y el estado «Sin asignar»**, en gris. **No se inventa un estado**: una posición ausente no es «0», un sensor de sombra ausente no es «No alineada» (que además dispararía una advertencia y forzaría la tarjeta a mostrarse). Asignar el rol en Administración hace que la tarjeta empiece a mostrar el dato.
3. **Control manual: el mismo diseño, filas desde los componentes.** En modo GPIO las filas y paneles se arman desde los componentes vigentes y las **operaciones que declara la ficha de su driver**, sin un caso por driver:
   - encender/apagar y armar/desarmar → fila con interruptor;
   - abrir/cerrar → fila «Apertura»; pulso → fila «Pulso»;
   - mover/mover_a → un panel por motor con el diseño del desplazador lineal;
   - sin operaciones → «Sensores»; operaciones no reconocidas → formulario genérico en la misma fila.

   Los nombres salen del rol (`LASER` → «Láser CO₂»); sin rol, el del componente.
4. **La lógica del modo vive aparte de la vista.** El estado del hardware GPIO, la orden pendiente por componente y los bloqueos están en el hook `useHardwareGpio`. La vista recibe ese estado y se puede probar sin red.
5. **Seguridad igual en los dos modos (ADR-0002, ADR-0011):**
   - la parada de emergencia y el rearme quedan **en la cabecera**, no se duplican en la pantalla;
   - una orden por componente hasta que el controlador informa su resultado;
   - no se opera a mano en emergencia, sin conexión, aplicando configuración ni con un programa grabando;
   - un eje no se mueve sin origen declarado, y el motivo de un rechazo se muestra en la fila del componente.
6. **Lo que el modo simulado no puede hacer, no se ofrece.** La configuración de componentes solo la aplica el controlador GPIO: con el HAL simulado, Administración oculta «Reintentar aplicación» y lo explica, y `/api/hardware/aplicar/` responde 409 sin enviar nada.

## Consecuencias

- Se elimina `HardwareManual.tsx`. Una pantalla nueva que dependa del modo tiene que seguir este ADR: misma estructura y mismas clases, con los datos adaptados.
- Las pantallas nuevas de la integración (formulario de componentes, editor de pasos) usan las clases de formulario del sistema (`form-panel`, `form-grid`, `field`, `form-actions`), como las demás.
- `tests/tarjetas.cjs` prueba el dashboard con la telemetría que publica la Pi simulada, y `tests/hardware.cjs` prueba el hook y Control manual armado desde los componentes.
- Para probar el modo GPIO en la PC: `docs/simulador-pi5.md`.

## Fuera de alcance (pendiente)

- **Datos de ejemplo en modo GPIO.** `cargar_datos_iniciales --con-ejemplo` arma los programas contra el inventario simulado (`laser`, `shutter`, `motor_x`), pero en modo GPIO la configuración arranca vacía: la carga falla y, como es una transacción, no deja ni los usuarios. Hay que cargar un inventario de ejemplo antes o validar los programas de ejemplo contra el inventario simulado.
- **ADR-0010 quedó desactualizado.** Los programas ya no tienen perfil de período: son un árbol de pasos. Falta el ADR que lo reemplace.
- **Componentes sin rol en el dashboard** (por ejemplo, un segundo motor): hoy se ven solo en Control manual. Podrían sumarse como tarjetas extra, usando la edición de tarjetas.

## Alternativas descartadas

- **Una pantalla por modo.** Es lo que había: duplica código y diseño, y el modo que se usa con el equipo real queda con la peor interfaz.
- **Ocultar en GPIO las tarjetas sin componente.** Cambiaría la grilla según la configuración sin que el operador lo pida. Se prefiere mostrarlas como «Sin asignar»: el operador las oculta, si quiere, con la edición de tarjetas (ADR-0011).
- **Un caso por driver en Control manual.** Cada driver nuevo obligaría a tocar el front. Reconocer las operaciones de la ficha alcanza para los drivers actuales y para los que sigan el mismo contrato.
