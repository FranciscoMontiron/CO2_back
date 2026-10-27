# ADR-0010 — Programa de grabado: perfil de período editable y versión por edición

**Estado:** Aceptada
**Fecha:** 2026-10-26
**Rama:** `feature/procedimiento-grabado` (back y front)
**Relacionada con:** RF010 (registrar programas sin tocar código), RN006 (ningún programa corre sin validar), RN011/RN012 (permisos).

## Resumen para quien (o qué IA) llegue sin contexto

- "Procedimiento de grabado" en la tarjeta del backlog significa **`Programa`** (`programas/models.py`): la receta de grabado.
  **No** es `Procedimiento` de `fabricacion/`, que es la receta de *medición en el OSA* (span, resolución, corriente del SLD). Ese queda **fuera**.
- Antes de este cambio ya existían alta y edición de programas (back, front y tests). La brecha era de **expresividad**, no de CRUD.
- Se cerraron dos brechas: el **perfil de período** y el **versionado**. La **secuencia multipaso** NO se hizo (ver "Fuera de alcance").

## Contexto

La API de programas hablaba "lenguaje de laboratorio" con cuatro números (potencia, duración de pulso, desplazamiento, criterio de fin) y los repartía en `Programa`, `Periodo` y un único `Paso` G01.
El `Periodo` quedaba **siempre CONSTANTE**, aunque:

- el modelo (`Periodo.tipo`: CONSTANTE / LINEAL / EXPONENCIAL, con `incremento` y `factor`) ya lo soportaba;
- el controlador (`controller/maquina.py: distancia_de_marca`) ya calculaba los tres perfiles.

Resultado: las redes con chirp (período variable) solo se podían cargar por el admin de Django, contra el espíritu de RF010.
Además, editar un programa ya usado lo pisaba sin dejar rastro y `Programa.version` nunca cambiaba.

## Decisión

1. **La API expone el perfil de período**: `perfil_periodo`, `incremento_um`, `factor` (junto a `desplazamiento_um`, que pasa a ser el período de la primera marca).
   - El serializador arma un `Periodo` sin guardar y llama a `Periodo.clean()`: **la regla de qué parámetros exige cada perfil vive en un solo lugar** (el modelo).
2. **La longitud y los pulsos se derivan acumulando períodos** (`_marcas_hasta` y `Periodo.distancia_acumulada`), no con `marcas × período`. Para CONSTANTE el resultado es idéntico al anterior.
   - Se rechaza un perfil cuyo período se hace ≤ 0 antes de la última marca.
   - Tope `MAXIMO_MARCAS = 100_000` al derivar pulsos desde una longitud con perfil variable, para no iterar sin fin.
3. **Cada edición suma 1 a `Programa.version`** (read-only en la API).
4. **El front replica el cálculo** en `derivar()` (`Programas.tsx`) solo para mostrar el campo calculado en vivo. **El backend es la fuente de verdad**: si difieren, manda el backend.
5. Al volver a CONSTANTE se limpian `incremento` y `factor` (el modelo rechaza basura de la versión anterior).

## Consecuencias

- Tests nuevos en `tests/test_api.py` (sección "Programas: perfiles de periodo y versiones").
- `views.iniciar` y el controlador **no cambian**: ya consumían el período por perfil.
- **Límite conocido:** `Programa.version` sube, pero el registro del ensayo (`RegistroDeFabricacion`) guarda solo la FK al programa, no la versión ni una copia de la receta. Un ensayo viejo apunta al programa *actual*, no al que corrió.
  Si el CIOp necesita trazabilidad exacta, hace falta una decisión nueva: guardar la versión (o un snapshot de la receta) en el registro al iniciar.
- El espectro simulado usa `periodo.base_um`; para perfiles variables es una aproximación (el simulador no modela el chirp).

## Alternativas descartadas

| Alternativa | Por qué no |
|---|---|
| Duplicar en el serializador las reglas de `Periodo.clean()` | Dos fuentes de verdad para lo mismo. |
| Crear una fila nueva de `Programa` por edición (versionado inmutable) | `codigo` es único y es la clave de la URL y del front; romperlo toca ensayos, front y controlador. Se deja para la decisión de trazabilidad exacta. |
| Aceptar `periodo` como objeto anidado en la API | Rompe el contrato plano que ya consume el front; el cambio actual es aditivo. |
| Resolver ahora el editor de pasos | Ver abajo. |

## Fuera de alcance (decisión pendiente)

**Editor de secuencia multipaso.** El modelo permite varios `Paso` por programa, pero el controlador (`_ejecutar`) lee solo `pulsos`, `duracion_pulso_ms` y `periodo` y repite un único pulso+desplazamiento.
Un editor de pasos requiere cambiar el mensaje `programa` del bus (ADR-0009), el loop del controlador y la validación de RN006 (orden lógico de pasos).
Es un cambio de otro tamaño: conviene abrirlo como tarjeta/ADR propio y acordar con el CIOp qué instrucciones (códigos G) hacen falta.

## Archivos tocados

- Back: `programas/serializers.py`, `tests/test_api.py`.
- Front: `src/mock/types.ts`, `src/api/client.ts`, `src/pages/Programas.tsx`.
