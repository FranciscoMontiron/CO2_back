# Componentes v2: puesta en marcha manual

`feature-componentes-v2` integra las ramas anteriores de develop. Esta entrega
implementa operación manual por instancia física, con el mismo contrato JSON que
la administración existente. El paso siguiente es enlazar roles y capacidades
del programa con estas instancias y sus interlocks. El grabado automático GPIO
queda explícitamente deshabilitado hasta esa integración. La simulación de
programas existente sigue disponible con `CONTROLLER_HAL=simulado`.

## Configuración

```json
{
  "componentes": {
    "shutter_principal": {
      "tipo": "SHUTTER_GPIO",
      "pines": {"pin_señal": 17},
      "estado": "Vigente"
    },
    "otro shutter": {
      "tipo": "SHUTTER_GPIO",
      "pines": {"pin_señal": "GPIO27"},
      "parametros": {"activo_alto": true},
      "estado": "Vigente"
    }
  }
}
```

Los nombres identifican instancias: ninguno es obligatorio ni tiene un
comportamiento especial. Se admiten hasta 64 definiciones. `Eliminado` conserva
los datos pero no reserva pines ni crea un driver. Las definiciones vigentes se
validan antes de guardar y nuevamente en el controlador. Un tipo nuevo requiere
implementar su driver y registrarlo junto con su contrato en el catálogo.

Los números de pin son BCM. `17`, `GPIO17` y `PIN11` se normalizan a BCM17.
Los contactos de alimentación o masa, como `PIN2`, no son señales controlables.
No se permite asignar un GPIO a dos señales diferentes. Los canales de un mismo
ADC comparten SPI0 de forma coordinada.

| Tipo | Operaciones manuales | Información disponible |
|---|---|---|
| SHUTTER_GPIO | armar, abrir, cerrar, desarmar | Armado y salida de apertura aplicada |
| SALIDA_GPIO | encender, apagar | Estado lógico de la salida; polaridad configurable |
| ENTRADA_GPIO | Muestreo automático | Entrada digital, pull-up configurable |
| STEPPER_DRV8825 | referenciar, mover, deshabilitar | Posición estimada por pasos y habilitación |
| LM35 | Muestreo automático mediante MCP3008 | Tensión y temperatura |

El ejemplo completo está en `controller/hardware_config_example.json`. No se
copia automáticamente a una instalación ni se habilitan componentes al actualizar.

### LM35

El LM35 produce una señal analógica de 10 mV/°C. La definición original con
`Datos: GPIO17` se rechaza: no proporciona una conversión analógica válida y en
el ejemplo también reutiliza el pin del shutter.

El driver implementado usa un MCP3008:

```json
{
  "tipo": "LM35",
  "pines": {},
  "parametros": {"adc": "MCP3008", "canal": 0, "dispositivo_spi": 0, "vref": 3.3},
  "estado": "Vigente"
}
```

Conectar la salida analógica del sensor al canal indicado del ADC, no directamente
a un GPIO. La referencia declarada debe corresponder a la del circuito. SPI0 usa
GPIO9/10/11 y GPIO8 para CS0, o GPIO7 para CS1. gpiozero puede usar SPI por software
con esos pines, por lo que esta entrega no requiere mapear `/dev/spidev*`.
Otros ADC necesitan un driver adicional; no se asumen equivalentes al MCP3008.

### Motor DRV8825

Declarar `pin_step`, `pin_dir` y `pin_enable`, además de `unidad` (`mm` o `°`),
`pasos_por_unidad`, `minimo`, `maximo` y `velocidad_maxima` (por defecto 1 unidad/s).
`pin_enable` es activo en bajo. Los pasos por unidad deben incluir el microstepping
configurado físicamente. El driver limita la frecuencia a 2000 pasos/s
(`pasos_por_unidad × velocidad_maxima ≤ 2000`).

Cumple el contrato genérico de motor: `referenciar(posicion)`, `mover_a(posicion, velocidad)`,
`mover(delta, velocidad)` y `detener()`; campos `posicion`, `en_movimiento`, `referenciado`
y `habilitado`. No mueve sin referenciar, rechaza destinos fuera de `[minimo, maximo]` y
velocidades fuera de `(0, velocidad_maxima]`. El plazo de cada movimiento es
`|distancia| / velocidad + 5` segundos.

Al arrancar, deshabilitar o interrumpir un movimiento se pierde la referencia.
Para mover, el operador debe situar físicamente el eje y declarar su posición
con `referenciar`. Esta orden no mueve ni hace homing. La posición resultante es
estimada, sin encoder. Los pulsos desde Python sirven para puesta en marcha a
baja velocidad; no garantizan los tiempos de un proceso de grabado.

## Arranque en la Pi

1. Configurar `.env` con `CONTROLLER_HAL=gpio`, `TARGET_PLATFORM=linux/arm64`,
   `GPIO_CHIP_HOST` y `GPIO_GID` adecuados a la Pi.
2. Levantar con los overrides existentes:

   ```sh
   docker compose -f docker-compose.yml -f docker-compose.pi.yml up -d --build
   ```

3. Ingresar como administrador a **Administración → Configuración de hardware**.
   Crear o editar los componentes, elegir un driver del catálogo y guardar.
4. En **Control manual**, comprobar la revisión aplicada y la calidad de lectura
   antes de enviar una orden. Para shutter: armar → abrir → cerrar → desarmar.
5. Verificar físicamente el circuito de prueba. `APLICADO` confirma que el driver
   ejecutó la operación, no que un sensor haya confirmado movimiento mecánico.

API y controller usan el volumen persistente `hardware_config` en `/app/hardware`.
La API escribe; el controlador monta ese volumen solo para lectura. También
funciona con el override de desarrollo. El directorio se prepara con propietario
`co2` al construir las imágenes. Un JSON previo ubicado en `controller/` debe
importarse mediante la administración/API o migrarse al volumen después de
validarlo; no se borra ni se sobrescribe automáticamente.

Fuera de Docker, ambos procesos deben apuntar al mismo archivo usando
`HARDWARE_CONFIG_PATH`. `GPIO_CHIP` selecciona el chip dentro del contenedor
(por defecto 0). Se utiliza explícitamente lgpio para la Pi 5.

## Contrato HTTP

Todas las rutas requieren autenticación JWT.

| Método y ruta | Uso |
|---|---|
| GET /api/hardware/catalogo/ | Drivers, pines, parámetros y operaciones disponibles |
| GET /api/hardware/config/ | JSON deseado; devuelve ETag |
| POST /api/hardware/config/ | Guardar con If-Match del GET; solo administrador |
| POST /api/hardware/aplicar/ | Reintentar aplicación de configuración; solo administrador |
| GET /api/hardware/estado/ | Revisión aplicada, componentes, calidad y últimas órdenes |
| POST /api/hardware/comandos/ | Orden manual por instancia |

Ejemplo del cuerpo de una orden:

```json
{
  "componente": "shutter_principal",
  "operacion": "armar",
  "argumentos": {},
  "revision": "revision_aplicada_obtenida_del_estado"
}
```

La respuesta 202 devuelve `id` y estado `RECIBIDO`. Consultar ese ID en
`resultados` del estado: `EN_COLA`, `EJECUTANDO`, `APLICADO`, `RECHAZADO`,
`VENCIDO`, `CANCELADO` o `FALLIDO`. La caché conserva las últimas 256 órdenes;
los resultados terminales se envían al servicio de eventos y quedan en auditoría.
El encolado también se audita con el usuario. La persistencia de eventos conserva
las limitaciones del bus existente: una caída de Redis puede impedir despacharlos.

Para detener todos los componentes usar el comando existente
`POST /api/control/comandos/` con `{"accion":"emergencia"}`; después del
apagado, `{"accion":"rearmar"}`. Una parada cancela órdenes pendientes y se
atiende fuera de la cola normal. Su resultado eléctrico se registra sin inventar
una medición de respuesta física ni certificar el requisito de latencia.

## Concurrencia y fallos

- Una cola de 32 escrituras limita el trabajo pendiente. Las órdenes vencen si
  no comienzan en diez segundos y se verifican nuevamente al salir de la cola.
- Cada recurso físico tiene exclusión mutua; las transacciones del mismo bus
  no se solapan. Las lecturas de recursos independientes trabajan en paralelo.
- El lazo y las pantallas consultan caché. Las muestras mayores a tres segundos
  se identifican como vencidas; un movimiento no congela el muestreo de otro recurso.
- Un timeout cancela cooperativamente, enclava parada y pone el recurso en
  cuarentena. No se reutiliza tras rearmar: debe revisarse y reiniciarse el
  controlador. Un hilo bloqueado no se mata con asyncio; el lock físico permanece
  tomado, y si la escritura termina tarde solicita apagado antes de liberarlo.
- Los cambios de configuración esperan salidas seguras, cola vacía y ausencia de
  operaciones activas. El guardado es atómico y If-Match impide sobrescribir una
  edición concurrente. Guardado y aplicado se muestran por separado.
- Un fallo de inicialización mantiene la revisión sin aplicar y la operación
  bloqueada. Corregir la configuración y usar reintentar; los recursos en
  cuarentena requieren reinicio.
- La supervisión termina el servicio si muere una tarea esencial o el ciclo de
  hardware deja de avanzar. Una pérdida de comunicación con Redis solicita parada.
- Los interlocks físicos siguen siendo necesarios: una cola prioritaria no
  interrumpe una llamada nativa que quedó bloqueada ni mide un cierre mecánico.

## Verificación y alcance

Las pruebas usan un backend de pines controlado para reproducir colas llenas,
varias instancias, exclusión de bus, lecturas durante movimiento, vencimiento,
timeouts, cambios de configuración y cancelación de reaperturas. Las pruebas API
verifican permisos, concurrencia, auditoría y aceptación sin falsa confirmación.

```sh
python -m pytest --ds=config.settings.test
```

Ese perfil usa SQLite en memoria para pruebas locales; no sustituye validación
de despliegue con MySQL. En CO2_front:

```sh
npm ci
npm run build
npm run test:hardware
npm run test:telemetria
npm run test:emergencia
```

No se realizaron pruebas eléctricas en una Raspberry Pi durante esta implementación.
Faltan para habilitar grabado físico automático: enlaces de roles del programa,
realimentación e interlocks del arreglo, control de potencia, temporización de
pulsos adecuada, homing/posición verificada e interrogador óptico. El modo manual
entregado permite comenzar esa integración componente por componente.

Fuentes de los drivers: [gpiozero SPI/MCP3008](https://gpiozero.readthedocs.io/en/stable/api_spi.html),
[gpiozero y lgpio](https://gpiozero.readthedocs.io/en/stable/api_pins.html),
[LM35 de Texas Instruments](https://www.ti.com/product/LM35),
[DRV8825 de Pololu](https://www.pololu.com/product/2133/).
