# Programas por pasos y drivers en un solo archivo — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Objetivo:** que un programa de grabado sea un árbol de pasos *componente + comando + datos* (con bucles y bloques en paralelo) que el controlador ejecuta igual en simulado y en GPIO, y que un tipo de componente nuevo se agregue tocando solo `controller/drivers.py`.

**Arquitectura:** cada clase de driver declara su ficha (pines, parámetros, comandos, campos). `hardware_config` arma el catálogo a partir del registro. Un módulo puro, `controller/programa.py`, valida el árbol y lo comparten la API y el controlador. Un `Ejecutor` asyncio en el controlador recorre el árbol y llama a `hal.operar(...)`. En GPIO, `operar` espera el resultado de la orden en el `GestorHardware`. En simulado, `operar` traduce a la física de `HalSimulado`.

**Stack:** Python 3.12, asyncio, gpiozero 2.0.1 + lgpio, Redis, Django/DRF, React + TypeScript + Vite.

**Spec:** `CO2_back/docs/superpowers/specs/2026-11-17-programas-por-pasos-design.md`. Quien ejecute lee los dos documentos.

**Requisito previo:** la Tarea 1 de `desarrollo/PLAN_LABORATORIO_GPIO.md` (unificar ramas con `origin/develop`). En front, `Programas.tsx` y `NuevoEnsayo.tsx` cambiaron en develop, y este plan se escribe sobre esa versión. Este plan reemplaza las Tareas 2, 4B, 4C, 7 y 9 de ese documento. Las Tareas 5, 6, 8, 10 y 11 siguen vigentes.

## Restricciones globales

- Rama `feature-componentes-v2` en los dos repos. **Los commits los hace el usuario:** cada paso «Commit» deja los cambios preparados y sugiere el mensaje. Sin push.
- ADR-0002: el controlador no importa Django. `controller/hardware_config.py`, `controller/programa.py` y `controller/roles.py` son puros: sin Django y sin GPIO al importarse.
- Ningún I/O físico desde tareas asyncio: todo pasa por `GestorHardware._llamar`.
- Un dato ausente es `None`/`null` de punta a punta. Nunca un valor nominal.
- ISA-101 (ADR-0011): pantallas neutras, color solo para lo anormal.
- Pines BCM, lógica de 3,3 V.
- Pruebas back: `python -m pytest --ds=config.settings.test` desde `CO2_back` (venv `desarrollo/.venv-componentes`). Front: `npm run build` y todos los `npm run test:*` desde `CO2_front`.
- Mensajes de commit en español, en el estilo del repo.
- Valores fijados por el spec:
  - pulso de ~400 ms con error del orden de 1 ms;
  - advertencia si el desvío supera ±2 %;
  - plazo por defecto de 5 s;
  - plazo del shutter: `duracion_ms/1000 + 2`;
  - plazo del motor: `distancia/velocidad + 5`;
  - `BUCLE` de 1 a 10 000 repeticiones;
  - hasta 3 niveles de bloque.

## Foco de revisión

1. **Componente dado de baja después de guardar un programa.** Al iniciar se rechaza con un mensaje que nombra el componente, se emite `abortado` y el registro no queda «en curso». Lo prueba la Tarea 7.
2. **Emergencia durante un bloque en paralelo con un pulso y un motor andando.** El shutter cierra antes de que termine el pulso, el motor frena y queda sin referenciar, y no se vuelve a abrir nada. Lo prueba la Tarea 7.
3. **Driver de un tercero con la ficha incompleta** (falta `CAMPOS`, o un tipo de argumento desconocido). Falla al importar o en la prueba de fichas, no en el laboratorio. Lo prueba la Tarea 1.
4. **Abortar con 10 000 repeticiones pendientes.** Termina después de la acción en curso y no recorre el resto del bucle. Lo prueba la Tarea 7.
5. **Programa guardado en simulado que nombra `motor_rotacion` y se inicia en GPIO**, donde ese componente no existe. Se rechaza al iniciar con un mensaje claro. Lo prueba la Tarea 8.

---

## Estructura de archivos

**CO2_back**

| Archivo | Cambio | Responsabilidad |
|---|---|---|
| `controller/factory.py` | modificar | `BaseComponente`: ficha por defecto, `RECURSO`, `plazo()`, `validar_parametros()` |
| `controller/drivers.py` | modificar | Fichas en cada clase; `pulso` del shutter; motor con contrato genérico |
| `controller/hardware_config.py` | modificar | `catalogo()` derivado del registro; `rol`, `ganancia`, `offset`, `inhibir_en_prueba` por componente |
| `controller/hardware.py` | modificar | `salida` en el resultado, plazo del driver, `ejecutar_y_esperar` |
| `controller/hal_base.py` | modificar | `OperacionRechazada`, `operar`, `inventario` |
| `controller/rpi.py` | modificar | `HalGpio.operar`, lecturas desde `roles.resolver` |
| `controller/simulador.py` | modificar | Componentes virtuales y `operar` |
| `controller/programa.py` | **crear** | `validar_programa`, `resumen`, `INVENTARIO_SIMULADO` |
| `controller/roles.py` | **crear** | `ROLES`, `resolver` |
| `controller/ejecutor.py` | **crear** | Recorre el árbol; ruta única de salida |
| `controller/maquina.py` | modificar | `_iniciar` y aborto con `Ejecutor`; GPIO acepta `iniciar`/`abortar` |
| `programas/models.py` + migración | modificar | `Paso`: `tipo`, `padre`, `componente`, `comando`, `argumentos` |
| `programas/serializers.py` | modificar | Representación por árbol |
| `operacion/views.py` | modificar | Iniciar ensayo con el árbol, también en GPIO |
| `procedimientos/semilla.py`, `procedimientos/catalogo.py` | modificar | Quitar «Habilitar alta tensión» |
| `pi_sim/tablero.py`, `docker/pi_sim/Dockerfile` | modificar | Nombres nuevos del motor; copiar `factory.py` y `drivers.py` |

**CO2_front**

| Archivo | Cambio |
|---|---|
| `src/api/hardware.ts` | `comandos` y `campos` en `DriverInfo`; `rol`, `ganancia`, `offset`, `inhibir_en_prueba` |
| `src/api/client.ts`, `src/mock/types.ts` | `Programa` con `pasos` en árbol |
| `src/components/FormularioComando.tsx` | **crear**: campos de argumentos según el tipo, compartido por Control manual y el editor |
| `src/components/HardwareManual.tsx` | Usar `FormularioComando` en lugar de `posicion_mm` fijo |
| `src/components/EditorPasos.tsx` | **crear**: árbol de pasos |
| `src/pages/Programas.tsx` | Formulario nuevo y resumen |
| `src/pages/Administracion.tsx` | Columnas Rol, escala e «Inhibir en prueba» |
| `tests/pasos.cjs` | **crear**: resumen y cuerpo enviado |

---

## Tareas

### Task 1: La ficha vive en la clase del driver

**Archivos:**
- Modificar: `controller/factory.py`, `controller/drivers.py`, `controller/hardware_config.py:11-43,140-215`, `controller/hardware.py:407`, `operacion/hardware_views.py:16,40`, `docker/pi_sim/Dockerfile:18`.
- Pruebas: `tests/test_hardware.py`.

**Interfaces:**
- Produce, en `BaseComponente`:
  - atributos de clase `DESCRIPCION: str = ""`, `PINES: list[str] = []`, `PARAMETROS: dict = {}`, `COMANDOS: dict[str, dict[str, str]] = {}`, `CAMPOS: dict[str, str] = {}`, `RECURSO: str | None = None`, `CONTRATO: str | None = None`, `AYUDA: str = ""`;
  - `@classmethod validar_parametros(cls, nombre: str, pines: dict, params: dict, contexto: dict) -> tuple[list[int], str]`, que devuelve los GPIO usados y el recurso. Por defecto, `(list(pines.values()), f"gpio:{nombre}")`. `contexto` es un dict compartido durante una misma validación (lo usa el ADC para los canales).
- Produce, en `controller/hardware_config.py`:
  - `catalogo() -> dict[str, dict]`, con la forma `{tipo: {"descripcion", "pines", "parametros", "operaciones", "campos", "contrato", "ayuda"}}`. Se mantiene la clave `operaciones` (desde `COMANDOS`) para no romper la API ni el front.
  - **Desaparece la constante `CATALOGO`.**
- Tipos de argumento admitidos: `"numero"`, `"entero"`, `"booleano"`. Tipos de campo: `"numero"`, `"booleano"`.

- [ ] **Paso 1 — pruebas que fallan** en `tests/test_hardware.py`:

```python
def test_catalogo_sale_de_las_fichas_de_los_drivers():
    cat = catalogo()
    assert cat["SHUTTER_GPIO"]["pines"] == ["pin_señal"]
    assert cat["LM35"]["campos"] == {"temperatura_c": "numero", "tension_v": "numero"}
    assert set(cat) == set(REGISTRO_COMPONENTES)

def test_cada_ficha_esta_completa_y_usa_tipos_conocidos():
    for tipo, clase in REGISTRO_COMPONENTES.items():
        assert clase.CAMPOS, tipo
        for args in clase.COMANDOS.values():
            assert set(args.values()) <= {"numero", "entero", "booleano"}, tipo
        assert set(clase.CAMPOS.values()) <= {"numero", "booleano"}, tipo

def test_driver_nuevo_se_agrega_en_un_solo_lugar(tmp_path):
    @registrar_componente("PRUEBA_RELE")
    class Rele(SalidaGPIO):
        COMANDOS = {"encender": {}, "apagar": {}, "pulsar": {"ms": "entero"}}
    try:
        cfg = validar_configuracion({"componentes": {"r": componente("PRUEBA_RELE", 5)}})
        assert cfg["componentes"]["r"]["tipo"] == "PRUEBA_RELE"
        assert "pulsar" in catalogo()["PRUEBA_RELE"]["operaciones"]
    finally:
        REGISTRO_COMPONENTES.pop("PRUEBA_RELE")
```

- [ ] **Paso 2 — correr y ver que fallan.** `python -m pytest tests/test_hardware.py -k "catalogo or ficha or driver_nuevo" --ds=config.settings.test --no-cov -q`. Esperado: `ImportError` de `catalogo`.
- [ ] **Paso 3 — implementar.**
  - Mover cada entrada de `CATALOGO` a su clase en `drivers.py`, sumándole `CAMPOS`:
    - `SHUTTER_GPIO`: `armado`, `salida_apertura` (booleano);
    - `SALIDA_GPIO`: `salida_activa`;
    - `ENTRADA_GPIO`: `activo`;
    - `LM35`: `temperatura_c`, `tension_v`;
    - `STEPPER_DRV8825`: se deja como está hasta la Tarea 4, con `CAMPOS` = `posicion_estimada_mm` (numero), `referenciado`, `habilitado`.
  - Las validaciones de `STEPPER_DRV8825` y de `LM35` que hoy están en `validar_configuracion` pasan a sus `validar_parametros`. El LM35 usa `contexto.setdefault("canales", set())`, devuelve `RECURSO = "SPI0"` y conserva los mensajes actuales.
  - `factory.py`: el recurso se calcula como `clase.RECURSO or f"gpio:{nombre}"`. Se borra la comparación con `"LM35"`.
  - `catalogo()` importa `controller.drivers` dentro de la función, para evitar el import circular.
  - Reemplazar todos los usos de `CATALOGO` (encontrarlos con `grep -rn CATALOGO`).
  - En el `Dockerfile` de pi-sim, copiar también `controller/factory.py` y `controller/drivers.py`.
- [ ] **Paso 4 — suite de hardware.** `python -m pytest tests/test_hardware.py tests/test_hardware_api.py tests/test_pi_sim.py --ds=config.settings.test --no-cov -q`. Esperado: todo pasa, con el mismo xfail de antes.
- [ ] **Paso 5 — commit** «Drivers: la ficha de cada tipo vive en su clase; el catálogo sale del registro».

---

### Task 2: El gestor devuelve lo que hizo el driver y permite esperar una orden

**Archivos:**
- Modificar: `controller/factory.py`, `controller/hardware.py:91-107,279-333`, `controller/hal_base.py`.
- Pruebas: `tests/test_hardware.py`.

**Interfaces:**
- Produce:
  - `BaseComponente.plazo(self, comando: str, argumentos: dict) -> float`, que por defecto devuelve `5.0`;
  - `GestorHardware._resultado(cmd, estado, detalle=None, salida=None)`, donde el resultado suma la clave `salida` (lo que devolvió `driver.ejecutar`, o `None`);
  - `async GestorHardware.ejecutar_y_esperar(self, componente: str, comando: str, argumentos: dict, usuario: str | None = None) -> dict`, que devuelve `salida or {}`;
  - en `controller/hal_base.py`, `class OperacionRechazada(Exception)`, cuyo mensaje es para el operador.
- `ejecutar_y_esperar`:
  - arma un `cmd` con `id = uuid4()`, `revision = self.revision_aplicada` y `vence_en = time.time() + driver.plazo(...) + 1`;
  - lo encola y espera un `asyncio.Future` guardado en `self._esperas[id]`, que `_resultado` resuelve en cualquier estado terminal;
  - si el estado es `RECHAZADO`, `VENCIDO`, `CANCELADO` o `FALLIDO`, lanza `OperacionRechazada(f"{componente}.{comando}: {detalle}")`.
- `_escribir`: el timeout de `_llamar` pasa a ser `driver.plazo(op, args)`, y se borra el caso especial `if cmd["operacion"] == "mover"`. `ejecutar()` devuelve `(salida, driver.leer(), driver.seguro())`.

- [ ] **Paso 1 — pruebas que fallan:**
  - `test_resultado_aplicado_trae_la_salida_del_driver`: con un driver de prueba cuyo `ejecutar` devuelve `{"x": 1}`, `gestor.resultados[id]["salida"] == {"x": 1}`.
  - `test_ejecutar_y_esperar_devuelve_la_salida_cuando_aplica`.
  - `test_ejecutar_y_esperar_lanza_con_el_motivo_del_driver`: `abrir` sobre un shutter sin armar lanza `OperacionRechazada` y `"Arme el shutter" in str(exc)`.
  - `test_ejecutar_y_esperar_lanza_si_hay_emergencia`: con `gestor.detener()` previo, lanza.
  - `test_el_plazo_lo_define_el_driver`: un driver con `plazo` de 0,1 s que duerme 0,5 s termina `FALLIDO` en menos de 0,4 s.
- [ ] **Paso 2 — correr y ver que fallan.**
- [ ] **Paso 3 — implementar** según las interfaces.
- [ ] **Paso 4 — `python -m pytest tests/test_hardware.py tests/test_pi_sim.py --ds=config.settings.test --no-cov -q`.** Esperado: todo pasa.
- [ ] **Paso 5 — commit** «Gestor: el resultado trae la salida del driver y se puede esperar una orden».

---

### Task 3: Pulso de grabado dentro del driver del shutter

**Archivos:**
- Modificar: `controller/drivers.py` (`ShutterGPIO`).
- Pruebas: `tests/test_hardware.py` y `tests/test_pi_sim.py`.

**Interfaces:**
- Produce el comando `pulso` con `COMANDOS["pulso"] = {"duracion_ms": "numero"}`, en el rango 1–10 000 (se valida en `validar_operacion`, con el mensaje «duracion_ms debe estar entre 1 y 10000»).
- Devuelve `{"duracion_real_ms": float, "cancelado": bool}`.
- `ShutterGPIO.plazo("pulso", a) = a["duracion_ms"] / 1000 + 2`.
- Algoritmo, el que fija el spec en 5.3:
  1. Exige que el shutter esté armado; si no, lanza `ValueError`.
  2. `on()` y toma `t0 = perf_counter()`.
  3. Espera con `cancelar.wait(max(0, resto - 0.002))`.
  4. Completa los últimos 2 ms en espera activa.
  5. `off()` en un `finally`.

- [ ] **Paso 1 — pruebas que fallan:**
  - `test_pulso_dura_lo_pedido_y_deja_el_shutter_cerrado`: armar, `pulso(duracion_ms=50)`; `abs(salida["duracion_real_ms"] - 50) < 5`, el pin queda en `False` y `cancelado is False`.
  - `test_pulso_sin_armar_se_rechaza`.
  - `test_emergencia_corta_el_pulso_y_cierra`: `pulso(2000)`, `gestor.detener()` a los 0,1 s. El pin vuelve a `False` en menos de 0,3 s y el resultado es `CANCELADO`.
  - `test_pulso_fuera_de_rango_se_rechaza`: 0 y 10 001.
  - En `tests/test_pi_sim.py`, `test_pulso_real_por_gpiozero_abre_y_cierra_en_el_tablero`: el tablero registra el flanco de subida y el de bajada, con una diferencia de 400 ± 20 ms.
- [ ] **Paso 2 — correr y ver que fallan.**
- [ ] **Paso 3 — implementar.**
- [ ] **Paso 4 — correr las dos suites.** Esperado: todo pasa.
- [ ] **Paso 5 — commit** «Shutter: pulso de grabado medido dentro del driver».

---

### Task 4: Contrato genérico de motor

**Archivos:**
- Modificar: `controller/drivers.py` (`StepperDRV8825`), `controller/hardware_config.py` (`validar_operacion`: quitar el caso `posicion_mm`), `pi_sim/tablero.py:437`.
- Pruebas: `tests/test_hardware.py` (motor), `tests/test_pi_sim.py`, crear `tests/test_contrato_motor.py`.

**Interfaces:**
- Produce, en `StepperDRV8825`:
  - `CONTRATO = "MOTOR"`;
  - `PINES` sin cambios;
  - `PARAMETROS = {"unidad": "mm", "pasos_por_unidad": 200, "minimo": 0, "maximo": 300, "velocidad_maxima": 1}`;
  - `COMANDOS = {"referenciar": {"posicion": "numero"}, "mover_a": {"posicion": "numero", "velocidad": "numero"}, "mover": {"delta": "numero", "velocidad": "numero"}, "detener": {}}`;
  - `CAMPOS = {"posicion": "numero", "en_movimiento": "booleano", "referenciado": "booleano", "habilitado": "booleano"}`.
- Reglas:
  - `unidad` debe ser `"mm"` o `"°"`;
  - `pasos_por_unidad × velocidad_maxima ≤ 2000`;
  - `mover` y `mover_a` rechazan con `ValueError` si el eje no está referenciado, si el destino cae fuera de `[minimo, maximo]` o si `velocidad` no está en `(0, velocidad_maxima]`;
  - `plazo` = `|distancia| / velocidad + 5`.
- `MOTOR_CONTRATO = {"comandos": {...}, "campos": {...}}` vive en `controller/factory.py`. La prueba de contrato lo usa.

- [ ] **Paso 1 — pruebas que fallan,** en `tests/test_contrato_motor.py`, parametrizadas con `[t for t, c in REGISTRO_COMPONENTES.items() if c.CONTRATO == "MOTOR"]` y `BackendFalso`:
  - `test_declara_comandos_y_campos_del_contrato`;
  - `test_no_mueve_sin_referenciar`;
  - `test_mover_a_y_mover_llegan_y_actualizan_posicion`: referenciar 0, `mover_a(1, 1)` lleva a `posicion == 1`, `mover(-0.5, 1)` lleva a `0.5`;
  - `test_rechaza_fuera_de_limites_y_sobre_velocidad`;
  - `test_detener_durante_movimiento_frena_y_pide_referenciar`;
  - `test_plazo_es_distancia_sobre_velocidad_mas_cinco`.
- [ ] **Paso 2 — correr y ver que fallan.**
- [ ] **Paso 3 — implementar.**
  - Actualizar el helper `motor()` y las pruebas de motor de `tests/test_hardware.py` a los nombres nuevos.
  - En `pi_sim/tablero.py`, la posición se informa como `posicion` usando `pasos_por_unidad`.
- [ ] **Paso 4 — `python -m pytest tests/test_hardware.py tests/test_contrato_motor.py tests/test_pi_sim.py --ds=config.settings.test --no-cov -q`.** Esperado: todo pasa.
- [ ] **Paso 5 — commit** «Motor: contrato genérico que cumple el DRV8825 y cualquier driver futuro».

---

### Task 5: Roles para el dashboard y opciones por componente

**Archivos:**
- Modificar: `controller/hardware_config.py` (`validar_configuracion`), `controller/rpi.py`.
- Crear: `controller/roles.py`.
- Pruebas: crear `tests/test_roles.py`.

**Interfaces:**
- Claves opcionales nuevas en cada componente: `rol: str`, `ganancia: float` (por defecto 1.0), `offset: float` (por defecto 0.0) e `inhibir_en_prueba: bool` (por defecto `False`).
  - Tomo `ganancia` y `offset` como claves del componente y no como parámetros del driver, para que ningún driver tenga que declararlos.
  - Se aceptan solo con un rol numérico.
- `controller/roles.py`:

```python
ROLES = {
  "TEMPERATURA_AGUA":    {"grupo": "variables",  "campos": ["temperatura_c"], "clase": "numero"},
  "CAUDAL_REFRIGERANTE": {"grupo": "variables",  "campos": ["caudal", "tension_v"], "clase": "numero"},
  "TENSION_AT":          {"grupo": "variables",  "campos": ["tension_kv", "tension_v"], "clase": "numero"},
  "POSICION_MOTOR":      {"grupo": "variables",  "campos": ["posicion"], "clase": "numero"},
  "TEMPERATURA_LASER":   {"grupo": "extra", "clave": "temperatura_laser", "campos": ["temperatura_c"], "clase": "numero"},
  "SENSOR_SOMBRA":       {"grupo": "extra", "clave": "sensor_sombra_mw", "campos": ["potencia_mw", "tension_v"], "clase": "numero"},
  "LASER":   {"grupo": "actuadores", "campos": ["salida_activa"], "clase": "booleano"},
  "SHUTTER": {"grupo": "actuadores", "campos": ["armado", "salida_apertura"], "clase": "booleano"},
}
def campo_de(rol: str, campos_driver: dict) -> str | None   # primer campo de ROLES[rol]["campos"] que el driver declara
def resolver(config: dict, muestras: dict, ahora: float) -> dict  # {"variables", "actuadores", "senales", "extra"}
```

- Validación:
  - el rol existe;
  - el driver declara un campo aceptado para ese rol (para `SHUTTER`, los dos);
  - un rol aparece en un solo componente `Vigente`.
- `resolver`:
  - devuelve `None` si el rol no está asignado, si la muestra no tiene `calidad == "OK"` o si tiene más de 3 s;
  - para los numéricos aplica `valor × ganancia + offset`;
  - `SHUTTER` publica `actuadores.shutter_armado`, `actuadores.shutter_abierto` y `senales.SHUTTER_ABIERTO`;
  - `LASER` publica `actuadores.laser`.
- `HalGpio.lecturas()` y `senales()` delegan en `resolver(self.hardware.config, self.hardware.muestras, time.monotonic())`.

- [ ] **Paso 1 — pruebas que fallan,** en `tests/test_roles.py`:
  - `test_configuracion_sin_roles_sigue_valida`;
  - `test_rol_invalido_se_rechaza` (parametrizada):
    - rol desconocido;
    - rol repetido;
    - `SHUTTER` sobre una `SALIDA_GPIO`;
    - `ganancia` sin rol;
    - `ganancia` en el rol `LASER`;
    - `inhibir_en_prueba` no booleano;
  - `test_resolver_publica_el_valor_con_escala`: LM35 con rol `TEMPERATURA_AGUA`, `ganancia=2`, `offset=1` y muestra 23,5 devuelve `48.0`;
  - `test_resolver_da_none_sin_rol_vencido_o_con_error`;
  - `test_resolver_shutter`;
  - `test_cambiar_rol_cambia_la_revision`.
- [ ] **Paso 2 — correr y ver que fallan.**
- [ ] **Paso 3 — implementar.**
- [ ] **Paso 4 — `python -m pytest tests/test_roles.py tests/test_hardware*.py --ds=config.settings.test --no-cov -q`.** Esperado: todo pasa. Ajustar `test_maquina_gpio_usa_gestor_y_no_publica_sensores_inventados`: sin roles todo sigue en `None`.
- [ ] **Paso 5 — commit** «Roles: cada componente puede alimentar una tarjeta del dashboard».

---

### Task 6: Validación del árbol de pasos y operar en los dos HAL

**Archivos:**
- Crear: `controller/programa.py`, `tests/test_programa_arbol.py`, `tests/test_contrato_hal.py`.
- Modificar: `controller/hal_base.py`, `controller/rpi.py`, `controller/simulador.py`.

**Interfaces:**
- Forma de un nodo (JSON):
  - `{"tipo": "ACCION", "componente": str, "comando": str, "argumentos": dict}`
  - `{"tipo": "ESPERA", "duracion_ms": int}`
  - `{"tipo": "BUCLE", "repeticiones": int, "pasos": [...]}`
  - `{"tipo": "PARALELO", "pasos": [...]}`
- `controller/programa.py`:
  - `validar_programa(pasos: list, config: dict) -> list`: normaliza o lanza `ValueError`. El mensaje antepone la ubicación, por ejemplo «Paso 3.1: …».
  - `resumen(pasos: list, config: dict) -> {"acciones": int, "pulsos": int, "recorrido": {componente: float}, "duracion_minima_ms": int}`: expande los bucles. `recorrido` suma el valor absoluto de `delta` (en `mover`) o la distancia desde la última posición conocida (en `mover_a`). `duracion_minima_ms` suma pulsos y esperas, y para un `PARALELO` toma el máximo de sus hijos.
  - `INVENTARIO_SIMULADO: dict`: una configuración válida para `validar_configuracion` con
    - `laser` (`SALIDA_GPIO`, `inhibir_en_prueba: True`, rol `LASER`);
    - `shutter` (`SHUTTER_GPIO`, rol `SHUTTER`);
    - `motor_x` (`STEPPER_DRV8825`, `unidad: "mm"`, rol `POSICION_MOTOR`);
    - `motor_rotacion` (`STEPPER_DRV8825`, `unidad: "°"`, `maximo: 360`).
- Reglas de validación (spec 5.5):
  - el componente existe y está `Vigente`;
  - el comando y los argumentos coinciden exactamente con la ficha, con su tipo;
  - un `PARALELO` admite solo `ACCION` y `ESPERA`, sin dos acciones sobre el mismo componente;
  - hasta 3 niveles de bloque;
  - `repeticiones` entre 1 y 10 000;
  - al menos una `ACCION`.
- `HalBase`:
  - `async operar(self, componente: str, comando: str, argumentos: dict) -> dict`;
  - `inventario(self) -> dict`, que devuelve la configuración contra la que se valida.
- `HalGpio`:
  - `operar` delega en `self.hardware.ejecutar_y_esperar`;
  - `inventario()` devuelve `self.hardware.config`.
- `HalSimulado`:
  - `inventario()` devuelve `validar_configuracion(INVENTARIO_SIMULADO)`.
  - `operar` traduce a los atributos actuales: `laser`, `shutter_armado` y `shutter_abierto`.
  - `pulso` hace `await asyncio.sleep(d / 1000 / self.velocidad)`, entre abrir y cerrar, y devuelve `duracion_real_ms = d`. `Maquina` asigna `hal.velocidad` en su `__init__`.
  - `motor_x` usa `mover_a` y espera `motor_en_destino`.
  - `motor_rotacion` lleva el ángulo en un atributo, a la velocidad pedida.
  - Mismos rechazos que los drivers: sin armar, sin referenciar, fuera de límites. Lanza `OperacionRechazada`.

- [ ] **Paso 1 — pruebas que fallan:**
  - `tests/test_programa_arbol.py`:
    - `test_programa_valido_se_normaliza`;
    - `test_programa_invalido_se_rechaza` (parametrizada, un caso por regla, verificando que el mensaje nombra la ubicación);
    - `test_resumen_expande_bucles_y_paralelos`: con el ejemplo helicoidal del spec (bucle × 200 de un paralelo con pulso de 400, `mover` +0,55 y girar +3,6), da `pulsos == 200`, `recorrido == {"motor_x": 110.0, "motor_rotacion": 720.0}` y `duracion_minima_ms >= 80000`.
  - `tests/test_contrato_hal.py`, parametrizado sobre `HalSimulado` y `HalGpio` (con `BackendFalso` y `INVENTARIO_SIMULADO` aplicado):
    - `test_operar_shutter_pulso_y_motor_en_los_dos_hal`;
    - `test_operar_rechaza_abrir_sin_armar_en_los_dos_hal`;
    - `test_inventario_valida_el_programa_de_ejemplo_en_los_dos_hal`.
- [ ] **Paso 2 — correr y ver que fallan.**
- [ ] **Paso 3 — implementar.**
- [ ] **Paso 4 — `python -m pytest tests/test_programa_arbol.py tests/test_contrato_hal.py tests/test_controlador.py --ds=config.settings.test --no-cov -q`.** Esperado: todo pasa y `test_controlador.py` no cambia.
- [ ] **Paso 5 — commit** «Programas: árbol de pasos validado y operar igual en simulado y en GPIO».

---

### Task 7: Ejecutor de programas en el controlador

**Archivos:**
- Crear: `controller/ejecutor.py`, `tests/test_ejecutor.py`.
- Modificar: `controller/maquina.py` (`manejar`, `_iniciar`, `_pedir_aborto`, `_parada`, `_ejecutar`, `telemetria`).

**Interfaces:**
- Consume: `hal.operar`, `hal.inventario`, `validar_programa`, `resumen`, `roles.resolver` y `OperacionRechazada`.

```python
class Ejecutor:
    def __init__(self, hal, emitir, avisar, *, registro: str, modo: str, inhibidos: set[str]): ...
    progreso: dict   # {"acciones": int, "total": int, "pulsos": int, "ubicacion": str, "ultimo_pulso_ms": float | None}
    def abortar(self) -> None          # termina después de la acción en curso
    async def correr(self, pasos: list) -> None
```

- `correr`:
  - En una `ACCION` llama a `hal.operar`. Si el componente está en `inhibidos` y `modo == "PRUEBA"`, no la envía y la registra como omitida.
  - Un `PARALELO` usa `asyncio.gather`. Si un hijo falla, cancela al resto.
  - Antes de cada acción y de cada iteración, revisa si se pidió abortar.
  - Cada `pulso` completado emite `EV_MARCA` con `registro`, `numero`, `tiempo_de_pulso` (la duración real), `cant_pulsos: 1` y `posicion_um`. La posición es la del primer componente con `CONTRATO == "MOTOR"` y `unidad == "mm"`, × 1000, o `None` si no hay uno.
  - Si el desvío del pulso supera el 2 %, avisa con nivel `"warn"`.
  - Termina normalmente emitiendo `EV_FIN` con `marcas` y `espectro`. El `espectro` es `None` en GPIO. En simulado se calcula con `hal.espectro`, usando la separación media entre marcas como período base, si hay al menos 2 marcas.
- **Ruta única de salida**, para cualquier excepción y para el aborto:
  1. parada segura (en GPIO, `hal.hardware.detener()`; en simulado, `_apagar_salidas_simuladas()`: shutter cerrado y desarmado, láser apagado, motores frenados);
  2. `EV_ABORTADO` con `registro`, `marcas` y `motivo`;
  3. `Maquina.ejecucion = None`.
  - Una emergencia cancela la tarea del ejecutor. En ese caso `_parada` ya detuvo el hardware y no se emite `fin`, como hoy.
- **`Maquina._iniciar`**:
  - en los dos modos: revalida `validar_programa(cmd["programa"]["pasos"], hal.inventario())` y rechaza con el mensaje;
  - en simulado: sigue exigiendo `LISTO` y deja de exigir el láser encendido;
  - en GPIO: exige que no haya emergencia ni bloqueo. Además, para cada rol de `("TEMPERATURA_AGUA", "CAUDAL_REFRIGERANTE", "TENSION_AT")` **asignado**, exige que `resolver` dé un valor y que no haya un umbral excedido. Si un rol no está asignado, avisa con `"warn"` y permite arrancar. Esta regla es provisoria: es la pregunta abierta 1 del spec.
- `manejar` en GPIO acepta además `INICIAR` y `ABORTAR`.
- `telemetria()["ejecucion"]` publica `Ejecutor.progreso` junto con `registro`, `programa` y `modo`.
- Se borran `distancia_de_marca` y el `_ejecutar` viejo, si ya nada los usa.

- [ ] **Paso 1 — pruebas que fallan,** en `tests/test_ejecutor.py`, sobre los dos HAL donde aplique:
  - `test_programa_secuencial_emite_marcas_y_fin`: bucle × 3 de {pulso 20, `mover` +0,1} da 3 `marca` con `tiempo_de_pulso` cerca de 20, `posicion_um` igual a 100, 200 y 300, y después `fin`.
  - `test_paralelo_corre_a_la_vez`: un pulso de 200 en paralelo con un `mover` de 0,2 s tarda menos de 0,35 s.
  - `test_fallo_a_mitad_cierra_todo_y_emite_abortado`: el shutter lanza en el pulso 2. Las salidas quedan apagadas, se emite `abortado` con motivo y `ejecucion is None`.
  - `test_abortar_espera_la_accion_en_curso_y_no_sigue_el_bucle`: bucle × 10 000; abortar durante el primer pulso. El pulso termina completo (la duración real es la pedida) y hay una sola marca.
  - `test_emergencia_en_paralelo_cierra_el_shutter_y_frena_el_motor` (GPIO con `BackendFalso`): el pin del shutter vuelve a `False` antes de que termine el pulso, el motor queda `referenciado is False` y no hay `fin`.
  - `test_componente_dado_de_baja_rechaza_al_iniciar`: emite `abortado` con `"motor_x"` en el motivo.
  - `test_modo_prueba_omite_el_laser`: en PRUEBA, `laser.encender` no llega al HAL y el programa termina.
  - `test_gpio_no_arranca_con_rol_critico_fuera_de_umbral` y `test_gpio_arranca_sin_roles_criticos_con_aviso`.
- [ ] **Paso 2 — correr y ver que fallan.**
- [ ] **Paso 3 — implementar `controller/ejecutor.py`.**
- [ ] **Paso 4 — integrarlo en `Maquina`.** Actualizar las pruebas de `tests/test_controlador.py` y `tests/test_control_y_eventos.py` que mandan `INICIAR` con el formato viejo, pasándolas al árbol equivalente: bucle × N de {pulso, `mover`}. Lo que afirman sobre estados y eventos no cambia.
- [ ] **Paso 5 — suite completa.** `python -m pytest --ds=config.settings.test`. Esperado: sin fallos nuevos.
- [ ] **Paso 6 — commit** «Controlador: ejecutor de programas por pasos con una sola ruta de salida».

---

### Task 8: Guardar programas como árbol e iniciar ensayos con él

**Archivos:**
- Modificar: `programas/models.py`, `programas/serializers.py`, `operacion/views.py:240-300`.
- Crear: la migración `programas/migrations/00XX_pasos_en_arbol.py`.
- Pruebas: `tests/test_programas.py`, `tests/test_operacion.py`.

**Interfaces:**
- `Paso` suma:
  - `tipo` (`ACCION`, `ESPERA`, `BUCLE`, `PARALELO`; por defecto `ACCION`);
  - `padre` (FK a sí mismo, nulo, `related_name="hijos"`, `on_delete=CASCADE`);
  - `componente` (`CharField` de 100, en blanco);
  - `comando` (`CharField` de 40, en blanco);
  - `argumentos` (`JSONField`, por defecto `dict`).
- `repeticiones` se reutiliza para `BUCLE`. `tiempo_de_pulso` guarda `duracion_ms` de `ESPERA`. `etiqueta` y `parametro` quedan opcionales (`blank=True`).
- La unicidad pasa a `(programa, padre, orden)`.
- `Programa.potencia_objetivo`, `criterio_fin` y `periodo` pasan a `null=True, blank=True`.
- La migración de datos deja los programas existentes en `validado=False`.
- API (`ProgramaSerializer`):
  - lee y escribe `{"codigo", "nombre", "descripcion", "procedimiento", "pasos": [árbol]}`;
  - suma un campo de solo lectura `resumen` (de `controller.programa.resumen`);
  - valida con `validar_programa(pasos, inventario_api())`;
  - `inventario_api()` devuelve el archivo de hardware (`leer_archivo` + `validar_configuracion`) si `settings.CONTROLLER_HAL == "gpio"`, o `INVENTARIO_SIMULADO` si no;
  - un programa guardado sin errores queda `validado=True`.
- Iniciar ensayo:
  - se borra el 409 de GPIO;
  - el requisito pasa a `telemetria["estado"] not in ("EMERGENCIA", "GRABANDO")` y no `bloqueado`;
  - envía `{"programa": {"codigo", "nombre", "pasos"}}`.
  - El resto de las condiciones las decide el controlador.

- [ ] **Paso 1 — pruebas que fallan:**
  - `test_guardar_programa_con_arbol_y_leerlo_igual`;
  - `test_guardar_programa_invalido_devuelve_400_con_la_ubicacion`;
  - `test_resumen_en_la_respuesta`;
  - `test_programas_viejos_quedan_sin_validar` (crear con el modelo viejo y correr la migración de datos con `MigrationExecutor`);
  - `test_iniciar_envia_el_arbol_al_controlador`;
  - `test_iniciar_en_gpio_ya_no_devuelve_409`;
  - `test_programa_de_simulado_en_gpio_se_rechaza_al_iniciar`: guardado con `motor_rotacion` y `CONTROLLER_HAL="gpio"` sin ese componente. Al iniciar, el controlador lo rechaza y el registro queda abortado. Probarlo con el controlador falso de `test_operacion.py`.
- [ ] **Paso 2 — correr y ver que fallan.**
- [ ] **Paso 3 — implementar el modelo, la migración (`makemigrations` más la parte de datos), el serializador y la vista.**
- [ ] **Paso 4 — suite completa back.** Esperado: sin fallos nuevos.
- [ ] **Paso 5 — commit** «Programas: se guardan como árbol de pasos y se inician también en GPIO».

---

### Task 9: Procedimiento guiado sin el botón de alta tensión

**Archivos:**
- Modificar: `procedimientos/semilla.py:213,241`, `procedimientos/catalogo.py:99`.
- Pruebas: `tests/test_procedimientos.py`.

- [ ] **Paso 1 — prueba que falla:** `test_paso_fuente_no_ofrece_habilitar_at`. En la semilla, el paso «Fuente de alta tensión» no tiene la acción `habilitar_at`. «Interlock físico cerrado» es de tipo manual y «Fuente HV habilitada» se verifica con la condición `TENSION_AT >= 15`.
- [ ] **Paso 2 — correr y ver que falla.**
- [ ] **Paso 3 — implementar.** Quitar la acción y el texto «Confirmar el encendido desde software…», y ajustar la validación.
- [ ] **Paso 4 — `python -m pytest tests/test_procedimientos.py --ds=config.settings.test --no-cov -q`.**
- [ ] **Paso 5 — commit** «Procedimiento: la alta tensión se habilita solo con el botón físico».

---

### Task 10: Front — fichas, Control manual y Administración

**Archivos:**
- Modificar: `src/api/hardware.ts`, `src/components/HardwareManual.tsx:60-115`, `src/pages/Administracion.tsx`, `tests/hardware.cjs`.
- Crear: `src/components/FormularioComando.tsx`.

**Interfaces:**
- `DriverInfo`: `operaciones: Record<string, Record<string, 'numero' | 'entero' | 'booleano'>>`, `campos: Record<string, 'numero' | 'booleano'>`, `contrato?: string | null`, `descripcion?: string`.
- `ComponenteHardware` suma `rol?: string`, `ganancia?: number`, `offset?: number` e `inhibir_en_prueba?: boolean`.
- `FormularioComando({ argumentos, onEnviar, deshabilitado })`:
  - un campo por argumento: `number` para `numero` y `entero` (este último con `step=1`), casilla para `booleano`;
  - con argumentos, el botón envía los valores tipados;
  - sin argumentos, el botón envía `{}`.
- `HardwareManual` lo usa para cada comando. Desaparecen `posicion_mm` y `carrera_max_mm` fijos.
- Administración, por componente:
  - selector «Rol», que ofrece solo los roles que el driver puede cumplir según sus `campos`. La lista de roles es constante: copiarla de `ROLES` (back, `controller/roles.py`) en `src/api/hardware.ts`, con un comentario que apunte al back;
  - `ganancia` y `offset` visibles solo con un rol numérico;
  - casilla «Inhibir en modo prueba».

- [ ] **Paso 1 — prueba que falla,** extendiendo `tests/hardware.cjs`:
  - un comando `mover_a` con `{posicion: 'numero', velocidad: 'numero'}` envía `{posicion: 1.5, velocidad: 0.5}`;
  - un comando sin argumentos envía `{}`;
  - guardar un componente con rol envía `rol`, `ganancia` y `offset`.
- [ ] **Paso 2 — `npm run test:hardware`** y ver que falla.
- [ ] **Paso 3 — implementar.**
- [ ] **Paso 4 — `npm run build && npm run test:hardware && npm run test:telemetria && npm run test:emergencia`.** Esperado: todo pasa.
- [ ] **Paso 5 — commit** «Control manual y Administración se arman desde la ficha del driver».

---

### Task 11: Front — editor de pasos

**Archivos:**
- Modificar: `src/api/client.ts` (`ProgramaApi` y su conversión), `src/mock/types.ts` (`Programa`), `src/pages/Programas.tsx`, `package.json`.
- Crear: `src/components/EditorPasos.tsx`, `src/components/resumenPasos.ts`, `tests/pasos.cjs`.

**Interfaces:**
- `type NodoPaso` replica la forma de nodo de la Tarea 6.
- `Programa` queda con `codigo`, `nombre`, `descripcion`, `procedimiento`, `pasos: NodoPaso[]`, `resumen?`, `validado`. Se quitan `potenciaObjetivoMw`, `perfil`, `criterio`, `distancia`, `pulsos` y `duracionPulsoMs`.
- `resumenPasos(pasos: NodoPaso[]): { acciones: number; pulsos: number; recorrido: Record<string, number>; duracionMinimaMs: number }`, con las mismas reglas que `controller.programa.resumen`. Sirve para ver el resumen mientras se edita; al guardar manda el del back.
- `EditorPasos({ pasos, componentes, catalogo, onChange })`:
  - agrega acción, espera, bucle y en paralelo;
  - permite subir, bajar y borrar;
  - en una acción, el componente se elige entre los `Vigente` y el comando entre los de su ficha; los argumentos usan `FormularioComando` en modo edición;
  - dentro de un `PARALELO` solo ofrece acción y espera;
  - no deja anidar más de 3 niveles.
- `Programas.tsx` («Nuevo programa»):
  - campos: nombre, procedimiento guiado, `EditorPasos` y el resumen («N pulsos · recorrido por motor · duración mínima»);
  - los errores del back se muestran con su ubicación («Paso 3.1: …»);
  - los programas con `validado = false` muestran «Rearmar con pasos».
- El inventario para el editor sale de `/api/hardware/estado/` en GPIO, o del `INVENTARIO_SIMULADO` que expone el back. Sumar `GET /api/programas/inventario/`, que devuelve `inventario_api()` de la Tarea 8. Es un cambio chico de back que va en esta tarea, con su prueba en `tests/test_programas.py`: `test_inventario_para_el_editor`.

- [ ] **Paso 1 — prueba que falla,** en `tests/pasos.cjs` (mismo armado con esbuild que `tests/hardware.cjs`), con script `test:pasos` en `package.json`:
  - el ejemplo helicoidal da `pulsos === 200`, `recorrido.motor_x === 110` y `recorrido.motor_rotacion === 720`;
  - un `PARALELO` toma la duración del hijo más largo;
  - el cuerpo enviado al guardar tiene la forma de la Tarea 8.
- [ ] **Paso 2 — `npm run test:pasos`** y ver que falla.
- [ ] **Paso 3 — implementar** `resumenPasos`, `EditorPasos`, el formulario nuevo y el endpoint de inventario.
- [ ] **Paso 4 — `npm run build` y todos los `npm run test:*`**, más `python -m pytest tests/test_programas.py --ds=config.settings.test --no-cov -q`. Esperado: todo pasa. Corregir cada error de `tsc` que deje la quita de los campos viejos, incluido `NuevoEnsayo.tsx`.
- [ ] **Paso 5 — commit** en los dos repos: «Programas: editor de pasos con bucles y bloques en paralelo».

---

### Task 12: Prueba de punta a punta en el simulador de Pi 5

Sin código nuevo, salvo lo que aparezca al probar.

- [ ] **Paso 1 — `make sim`** en `CO2_back`, con `FRONT_DIR=../CO2_front`. En Administración, dar de alta `laser` (`SALIDA_GPIO`, GPIO13, «Inhibir en prueba»), `shutter` (GPIO17, rol `SHUTTER`), un motor (GPIO22/23/24, rol `POSICION_MOTOR`) y un LM35 con rol `TEMPERATURA_AGUA`. Conectar todo en la página de la Pi.
- [ ] **Paso 2 — Control manual.** Cada comando del motor y del shutter aparece con sus campos. `pulso` de 400 ms se ve en la página de la Pi.
- [ ] **Paso 3 — programa** «láser encender → shutter armar → bucle × 5 {pulso 400, motor mover +0,1 a 0,1}». Iniciarlo en PRUEBA (el láser no se enciende) y después en REAL. Esperado: 5 marcas con duración real cercana a 400 y el ensayo cerrado.
- [ ] **Paso 4 — fallo a mitad.** Activar «Pin colgado» en el shutter durante el bucle. Esperado: programa abortado con motivo, salidas apagadas y registro cerrado.
- [ ] **Paso 5 — suite completa** en los dos repos. Anotar en `docs/simulador-pi5.md` cómo correr un programa en el simulador.
- [ ] **Paso 6 — commit** «Simulador: guía para probar programas por pasos».
