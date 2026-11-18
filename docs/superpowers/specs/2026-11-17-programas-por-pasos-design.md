# Programas por pasos y componentes definidos en un solo archivo

Fecha: 17 de noviembre de 2026. Repos: `CO2_back` y `CO2_front`, rama `feature-componentes-v2`.
Reemplaza las Tareas 4B y 4C de `desarrollo/PLAN_LABORATORIO_GPIO.md` y ajusta las Tareas 2, 7 y 9.

## 1. Intención

En el laboratorio, la Pi tiene que ejecutar grabados con los componentes reales. Cómo se
graba cambia de un ensayo a otro: pulso y después avance, avance mientras el shutter está
abierto, o varios motores a la vez (redes helicoidales). Por eso el programa no puede ser
una secuencia fija con potencia, período y criterio de fin. Tiene que ser una **lista de
pasos** que arma quien diseña el ensayo, donde cada paso le pide algo a un componente.

El compañero que maneja el hardware va a ir sumando componentes. Tiene que poder hacerlo
**tocando un solo archivo**, `controller/drivers.py`. El SCADA (alta en Administración,
Control manual y el editor de programas) toma lo que declara el driver sin más cambios.

## 2. Qué controla la Pi (relevamiento del 16/11)

| Componente | La Pi | Notas |
|---|---|---|
| Láser | Prende y apaga | |
| Shutter | Arma, abre y cierra; hace el pulso de grabado | El grabado ocurre mientras está abierto |
| Motor (uno o más) | Mueve | El modelo se conoce el 18/11. Puede tener controlador propio o ir por STEP/DIR |
| Temperatura del láser | Lee | |
| Temperatura de la refrigeración | Lee | |
| Caudalímetro | Lee | Entrega un número |
| Voltímetro (fuente de alta tensión) | Lee | |
| Sensor de sombra | Lee | Tentativo |

Fuera de la Pi: **la fuente de alta tensión se habilita solo con su botón físico** y el
interlock es físico. **La refrigeración la enciende el operador.** El SCADA solo muestra sus
lecturas.

## 3. Objetivos, no objetivos y antiobjetivos

**Objetivos**

1. Un tipo de componente nuevo se agrega escribiendo una clase en `drivers.py`. No hace falta
   tocar `hardware_config.py`, la API ni el front.
2. Un programa se arma con pasos *componente + comando + datos*, agrupables en **bucles** y
   en bloques **en paralelo**.
3. El pulso de grabado dura lo pedido con error del orden de 1 ms para pulsos de ~400 ms, y
   cada pulso registra cuánto duró de verdad.
4. Los motores se manejan con un contrato genérico que hoy cumple el DRV8825 y mañana puede
   cumplir un driver de controlador propio.
5. Ante emergencia, fallo o aborto, el programa termina siempre igual: shutter cerrado,
   láser apagado, motores detenidos, motivo registrado.
6. El mismo programa corre en modo simulado, en el simulador de Pi 5 y en la Pi.

**No objetivos**

- Sincronización exacta entre ejes. En un bloque en paralelo los arranques pueden diferir
  unos milisegundos. Si una red helicoidal necesita más, la coordinación la tiene que dar el
  controlador del motor.
- Generar los pulsos del motor con el temporizador de lgpio o con hardware externo.
- Controlar la fuente o la refrigeración desde la Pi.
- Homing automático e interrogador óptico.

**Antiobjetivos** (cualquiera de estos invalida el trabajo aunque funcione)

- Que un pulso pase por Redis o por la cola entre abrir y cerrar.
- Que un fallo a mitad de programa deje el shutter abierto o el láser encendido.
- Que sumar un driver obligue a tocar más de un archivo.
- Que el modo simulado deje de servir para practicar.

## 4. Enfoques considerados

| | Enfoque | Resultado |
|---|---|---|
| **A** | El controlador recorre los pasos y pide cada orden al gestor de hardware | **Elegido** |
| B | Traducir el programa a código G (el campo `etiqueta` de `Paso`) e interpretarlo | Descartado: el código G no expresa bien el paralelo ni comandos arbitrarios de drivers nuevos |
| C | Que la API mande las órdenes una por una | Descartado: viola ADR-0002 (el controlador es el único dueño del hardware), agrega latencia por paso y, si se cae la API, nadie cierra el shutter |

El usuario eligió A el 17/11.

## 5. Diseño

### 5.1 El driver declara todo

Cada clase registrada con `@registrar_componente` declara su ficha como atributos de clase.
Hoy esa ficha vive en `CATALOGO`, dentro de `hardware_config.py`.

```python
@registrar_componente("SHUTTER_GPIO")
class ShutterGPIO(BaseComponente):
    DESCRIPCION = "Shutter con orden eléctrica, sin sensor de posición"
    PINES = ["pin_señal"]
    PARAMETROS = {"activo_alto": True}
    COMANDOS = {
        "armar": {}, "desarmar": {}, "abrir": {}, "cerrar": {},
        "pulso": {"duracion_ms": "numero"},
    }
    CAMPOS = {"armado": "booleano", "salida_apertura": "booleano"}
    RECURSO = None              # None: un recurso por componente. "SPI0" para el MCP3008.
```

- `hardware_config.CATALOGO` pasa a armarse a partir de `REGISTRO_COMPONENTES`. La validación
  y la API `GET /api/hardware/catalogo/` siguen funcionando igual y suman `COMANDOS` y `CAMPOS`.
- `hardware_config.py` sigue sin depender de Django ni de GPIO. `drivers.py` ya importa
  gpiozero solo dentro de `GpioBackend`, así que la API puede importarlo.
- El recurso deja de calcularse con `config["tipo"] == "LM35"` en `factory.py` y sale de
  `RECURSO`.
- Las validaciones propias de un tipo (por ejemplo, el canal del ADC duplicado) pasan a un
  método de clase opcional `validar(config, todos)`.
- Una prueba garantiza que cada tipo registrado tenga su ficha completa.
- La imagen de `pi-sim` copia hoy solo `hardware_config.py`. Tiene que copiar también
  `factory.py` y `drivers.py`.

Tipos de argumento admitidos en `COMANDOS`: `"numero"`, `"entero"`, `"booleano"`. Esto
alcanza para el editor de pasos y para Control manual.

### 5.2 Plazo de cada orden

`BaseComponente.plazo(comando, argumentos) -> float` dice cuántos segundos puede tardar una
orden antes de considerarla vencida. Por defecto son 5 s. El shutter devuelve
`duracion_ms/1000 + 2`. Los motores devuelven `distancia/velocidad + 5`. El ejecutor usa este
valor. No hay plazos fijos en el ejecutor.

### 5.3 Pulso del shutter

Comando `pulso(duracion_ms)` dentro del driver, en un solo hilo del gestor:

1. Exige que el shutter esté armado; si no, lanza `ValueError`.
2. Abre y toma `t0 = time.perf_counter()`.
3. Espera con `self.cancelar.wait(resto - 0.002)` y completa los últimos 2 ms en espera
   activa sobre `perf_counter()`.
4. Cierra y devuelve `{"duracion_real_ms": ...}`.
5. Si `cancelar` se activa, cierra de inmediato y devuelve la duración parcial marcada como
   cancelada.

El gestor hoy descarta lo que devuelve `ejecutar`. Tiene que pasarlo como `detalle` del
resultado `APLICADO`, para que llegue al ejecutor y al registro del ensayo.

Con 400 ms el error esperado en la Pi es de ~1 ms. Es una estimación: se mide con
osciloscopio en la prueba L11C. Si el desvío supera ±2 % (configurable en
`config/umbrales`), el registro lleva una advertencia.

### 5.4 Contrato genérico de motor

Cualquier driver de motor (hoy `STEPPER_DRV8825`; mañana el del controlador que corresponda)
declara al menos:

| Comandos | Campos |
|---|---|
| `referenciar(posicion)`, `mover_a(posicion, velocidad)`, `mover(delta, velocidad)`, `detener()` | `posicion` (numero), `en_movimiento` (booleano), `referenciado` (booleano) |

- Parámetros comunes: `unidad` (`"mm"` o `"°"`), `minimo`, `maximo` y `velocidad_maxima`.
- `mover` y `mover_a` rechazan si el eje no está referenciado o si el destino queda fuera de
  límites.
- Los dos vuelven recién cuando el eje llegó, o cuando `cancelar` lo frena.
- `detener()` y `apagar()` frenan el eje y lo marcan como no referenciado si se perdió
  posición.

El `STEPPER_DRV8825` actual se adapta a estos nombres. Sus comandos de hoy (`referenciar`,
`mover(posicion_mm)`, `deshabilitar`) se reemplazan. Sigue generando los pasos con Python
(riesgo R9 del plan).

Una prueba de contrato, parametrizada sobre todos los tipos que declaren
`CONTRATO = "MOTOR"`, verifica comandos, campos y comportamiento con `BackendFalso`. Un driver
nuevo de motor queda probado sin escribir pruebas propias.

### 5.5 Programa como árbol de pasos

Un programa tiene nombre, procedimiento guiado opcional y una lista ordenada de nodos:

| Tipo | Datos | Qué hace |
|---|---|---|
| `ACCION` | `componente`, `comando`, `argumentos` | Pide la orden y espera a que termine |
| `ESPERA` | `duracion_ms` | Espera |
| `BUCLE` | `repeticiones` (1–10 000), hijos | Repite los hijos en orden |
| `PARALELO` | hijos (solo `ACCION` o `ESPERA`) | Larga todos juntos y termina cuando terminan todos |

Reglas de validación. Corren al guardar en la API y otra vez al iniciar en el controlador,
contra la configuración de hardware vigente:

- El componente existe, está `Vigente` y su tipo declara el comando. Los argumentos son
  exactamente los declarados, con su tipo.
- Un `PARALELO` no puede tener dos acciones sobre el mismo componente: se serializarían en su
  lock y el resultado no sería el que se escribió.
- Profundidad máxima de 3 niveles de bloque. Un programa sin ninguna `ACCION` no es válido.
- La validación vive en un módulo puro, `controller/programa.py`, con
  `validar_programa(pasos, config_hardware)`. Lo usan la API y el controlador.

**Persistencia:** se extiende el modelo `Paso` existente, así el diagrama de clases sigue
valiendo con más atributos:

- se suman `tipo`, `padre` (FK a `Paso`, nulo en la raíz), `componente`, `comando` y
  `argumentos` (JSON);
- `repeticiones` ya existe y se reutiliza para `BUCLE`;
- la unicidad pasa de `(programa, orden)` a `(programa, padre, orden)`.

De `Programa` salen del formulario `potencia_objetivo`, `criterio_fin`, `valor_criterio_fin`
y `periodo`. En el modelo quedan nulos u opcionales; quitarlos del todo queda a criterio de
quien construya, según qué dependa de ellos.

**Programas existentes:** no nombran componentes, así que no se pueden convertir solos.
Quedan legibles y pasan a `validado = False`, con un aviso para rearmarlos con pasos.

### 5.6 Ejecutor en el controlador

Reemplaza el `_ejecutar` actual de `Maquina`.

- `iniciar` recibe el árbol completo y el registro. Revalida contra el hardware vigente y
  rechaza con un mensaje que nombra lo que falta.
- Una `ACCION` llama a `hal.operar(componente, comando, argumentos)`, que vuelve cuando la
  orden queda `APLICADA` y devuelve su `detalle`. Si la orden fue rechazada, venció
  (`plazo`), se canceló o falló, lanza `OperacionRechazada` con el motivo.
- En GPIO, `operar` usa un nuevo `GestorHardware.ejecutar_y_esperar`, con un
  `asyncio.Future` por id de orden que se resuelve en `_resultado`.
- `PARALELO` usa `asyncio.gather`. Si un hijo falla, se cancelan los demás.
- **Única ruta de salida ante cualquier excepción:**
  1. parada segura del hardware (`gestor.detener()`, que llama a `apagar()` de todos los
     drivers: cierra el shutter, apaga el láser y frena los motores);
  2. evento `abortado` con motivo;
  3. registro cerrado y `ejecucion = None`.
- **Abortar desde el SCADA** espera a que termine la acción en curso (nunca corta un pulso a
  la mitad) y sale por la misma ruta.
- **Emergencia** cancela todo de inmediato, incluso un pulso a la mitad.
- **Modo PRUEBA:** las acciones sobre componentes marcados como «inhibir en prueba» no se
  envían, se registran como omitidas y el programa sigue. Es una casilla nueva por
  componente en Administración, pensada para el láser.
- **Progreso publicado en la telemetría:**
  - acciones hechas sobre el total, calculado expandiendo los bucles;
  - pulsos hechos, que son las acciones `pulso` completadas;
  - ubicación actual, por ejemplo «Paso 3.1 · iteración 45 de 200»;
  - duración real del último pulso.
- **Condiciones para iniciar** (sección 7, pregunta 1): sin emergencia, sin umbral
  bloqueante y con dato vigente en las lecturas críticas.
  - En simulado se mantiene la exigencia actual de estado `LISTO`, para que la práctica de la
    secuencia de armado siga teniendo sentido.
  - Ya no se exige el láser encendido antes de iniciar: encenderlo es un paso del programa.
- **Registro del ensayo:** cada `pulso` completado genera una marca con su duración real, por
  el mismo evento que hoy emite el bucle fijo.

**Modo simulado.** `HalSimulado` ofrece un inventario fijo de componentes virtuales que
cumplen las mismas fichas: `laser` (salida), `shutter`, `motor_x` y `motor_rotacion`
(contrato de motor). Su `operar` traduce a la física simulada que ya existe. Así el mismo
programa corre en simulado y la práctica de operadores no se pierde. Cómo se integra con el
resto del simulador queda a criterio de quien construya.

### 5.7 Lo que muestra el dashboard

Cada componente lleva un campo opcional `rol`. Es la variante elegida el 16/11 en lugar de
una sección `enlaces` aparte. El rol dice qué tarjeta del dashboard alimenta:
`TEMPERATURA_AGUA`, `TEMPERATURA_LASER`, `CAUDAL_REFRIGERANTE`, `TENSION_AT`, `SENSOR_SOMBRA`,
`LASER`, `SHUTTER` y `POSICION_MOTOR`.

- Un rol se asigna a un solo componente. El campo que se lee sale de la ficha del rol.
- Para señales analógicas, `ganancia` y `offset` van en los parámetros del componente.
- Los roles solo sirven para mostrar datos. **Los programas no usan roles:** nombran el
  componente.
- Lo que no tiene rol asignado se ve «Sin datos», según la Tarea 8 del plan.

### 5.8 Front

- **Programas → Nuevo/Editar:**
  - campos: nombre, procedimiento guiado y el editor de pasos;
  - el editor permite agregar acción, espera, bucle y en paralelo, reordenar y borrar;
  - en una acción se elige el componente (solo `Vigente`), después el comando (de su ficha) y
    los argumentos aparecen como campos según su tipo;
  - un resumen calculado muestra pulsos totales, recorrido por motor y duración estimada (cota
    inferior);
  - se quitan potencia objetivo, perfil de período, criterio de finalización y distancia
    total.
- **Control manual por componente:** se arma desde la ficha, sin cambios de concepto.
- **Procedimiento guiado, paso «Fuente de alta tensión»:**
  - se quita el botón «Habilitar alta tensión» (`procedimientos/semilla.py` y
    `catalogo.py`);
  - la validación «Interlock físico cerrado» queda como verificación manual del operador;
  - «Fuente HV habilitada» se verifica con la lectura del voltímetro, si tiene rol asignado.
- **Administración:** columna «Rol» y casilla «Inhibir en modo prueba» por componente.

## 6. Restricciones

- ADR-0002: el controlador no importa Django. `controller/programa.py` y `hardware_config.py`
  son puros.
- Ningún I/O físico desde tareas asyncio: todo pasa por `GestorHardware`.
- Un dato ausente es `None`/`null` de punta a punta.
- ISA-101 (ADR-0011) en las pantallas nuevas: neutras, color solo para lo anormal.
- Pruebas back: `python -m pytest --ds=config.settings.test`. Front: `npm run build` y
  `npm run test:*`.
- Los commits los hace el usuario. Sin push.

## 7. Abierto

1. **Condiciones para iniciar un programa en GPIO.** Propuesta: temperatura de refrigeración
   y caudal dentro de umbrales, y voltímetro por encima del mínimo, si esos roles están
   asignados. Hay que confirmarlo con el laboratorio.
2. **Modelo del motor** (se sabe el 18/11). Si tiene controlador propio, se escribe su driver
   contra el contrato de 5.4. Si va por STEP/DIR, sigue el DRV8825.
3. **Tiempo de apertura y cierre del shutter** según su hoja de datos.
4. **¿Apagar el láser al terminar el programa?** Hoy el fin normal cierra y desarma el
   shutter (en simulado y en GPIO) y, si el láser quedó encendido, solo avisa. Decidirlo con
   el laboratorio (estabilidad térmica del tubo entre corridas). Nota: en simulado, al
   desarmar el shutter se pierde LISTO y el enclavamiento del simulador apaga el láser.
5. **Vigilancia de umbrales durante el programa en GPIO.** Hoy las lecturas críticas solo
   se verifican al iniciar (ver pregunta 1); durante la corrida no se vigilan.

## 8. A criterio de quien construya

- Forma exacta del JSON del árbol entre API y controlador.
- Cómo `HalSimulado` modela los componentes virtuales.
- Si `potencia_objetivo`, `criterio_fin` y `periodo` se quitan del modelo o quedan nulos.
- Diseño visual del editor de pasos, dentro de ISA-101.
- Cómo se ordena el trabajo en tareas y commits.
