# Simulador de Raspberry Pi 5

Hace de Pi 5 para probar los componentes GPIO desde la PC. El controlador corre en modo
`gpio` con gpiozero, `LGPIOFactory` y los drivers reales. Solo se reemplaza la biblioteca
`lgpio` por un doble que habla con el contenedor `pi-sim`.

No reemplaza la prueba en el laboratorio: no modela electricidad ni tiempos reales.

## Levantarlo

El proyecto `co2sim` usa su propia base y su propio inventario, separados de otras
instalaciones.

```sh
docker compose -p co2sim -f docker-compose.yml -f docker-compose.sim.yml up -d --build --wait mysql redis pi-sim controller api
docker compose -p co2sim -f docker-compose.yml -f docker-compose.sim.yml exec api python manage.py migrate
docker compose -p co2sim -f docker-compose.yml -f docker-compose.sim.yml exec api python manage.py cargar_datos_iniciales --con-ejemplo
docker compose -p co2sim -f docker-compose.yml -f docker-compose.sim.yml up -d --build --wait
```

Los últimos dos pasos de datos se hacen una sola vez. Después alcanza con `make sim`.

- SCADA: <https://localhost:8443>. Si ya hay otra instalación levantada, elegir otros puertos
  con `HOST_HTTP_PORT` y `HOST_HTTPS_PORT`.
- Página de la Pi: <http://127.0.0.1:8091>. Se cambia con `PI_SIM_PUERTO_PAGINA`.

Para bajarlo sin borrar datos: `docker compose -p co2sim -f docker-compose.yml -f docker-compose.sim.yml down`.

No combinar con `docker-compose.pi.yml`: el doble se niega a abrir el chip si encuentra
un `/dev/gpiochip0` real.

## Probar un componente

1. En **Administración → Configuración de hardware**, dar de alta el componente.
2. Aparece en la página de la Pi, desconectado.
3. Tocar **Conectar**.
4. Dar la orden en **Control manual** y mirar el pin y el componente en la página.

## Correr un programa por pasos

1. Dar de alta lo que use el programa, por ejemplo:
   - `laser`: `SALIDA_GPIO` en GPIO13, rol `LASER`, con «Inhibir en prueba»;
   - `shutter`: `SHUTTER_GPIO` en GPIO17, rol `SHUTTER`;
   - un motor `STEPPER_DRV8825` en GPIO22/23/24, rol `POSICION_MOTOR`;
   - un `LM35` con rol `TEMPERATURA_AGUA`.
2. Tocar **Conectar** en cada uno en la página de la Pi. Con el LM35 desconectado el canal
   lee 0 °C y el arranque se rechaza por el umbral de temperatura del agua.
3. En **Control manual**, referenciar el motor (`referenciar` con su posición actual). El
   programa no lo hace solo y `mover` falla sin referencia. Cualquier parada, incluido
   `detener`, descarta la referencia.
4. Armar el programa en **Programas → Nuevo programa**, por ejemplo: láser `encender`,
   shutter `armar`, `BUCLE` × 5 con shutter `pulso` 400 ms y motor `mover` `delta` 0,1 a
   `velocidad` 0,1.
5. Iniciarlo en **PRUEBA**: GPIO13 no se mueve y no quedan marcas. Iniciarlo en **REAL**:
   cada pulso deja una marca con su duración real, que en el simulador ronda los 400,5 ms.
6. Al terminar bien, el controlador cierra y desarma el shutter. El láser queda como lo dejó
   el programa: si no termina con láser `apagar`, sigue encendido y se avisa.

Al iniciar en GPIO se verifican las lecturas críticas con rol asignado (temperatura del
agua, caudal, alta tensión). Durante el programa no se vigilan umbrales: una pérdida de
caudal a mitad de la corrida no detiene nada. El aviso de arranque lo recuerda siempre.

Si el programa aborta, el gestor hace la parada segura y queda en emergencia. Para volver
a correr: rearmar desde la barra de emergencia y referenciar el motor otra vez. Para probar
un fallo a mitad, activar «Pin colgado» en el GPIO del shutter durante el bucle: el ensayo
se cierra como abortado y el motivo nombra al componente. Después hay que quitar la falla y
correr `restart controller`.

Si se reconstruye o reinicia `pi-sim`, reiniciar también `controller` y volver a tocar
**Conectar**: los componentes arrancan desconectados.

## Qué simula cada tipo

| Tipo | En la página | Con el cable suelto |
|---|---|---|
| `SALIDA_GPIO` | Carga activa o apagada, según la polaridad | El pin cambia y la carga no |
| `SHUTTER_GPIO` | Cerrado, abriendo, abierto, cerrando | El pin cambia y el shutter no se mueve |
| `ENTRADA_GPIO` | Botón para abrir o cerrar el contacto | Se lee solo el pull |
| `STEPPER_DRV8825` | Posición real y pasos | El driver estima, el eje no se mueve |
| `LM35` | Temperatura a elegir, leída por SPI por software | El canal del ADC lee 0 |

Los drivers sin realimentación informan `APLICADO` aunque el cable esté suelto. El SCADA
no puede enterarse, y en el laboratorio pasaría lo mismo.

## Fallas a pedido

Una falla de pin colgado deja el recurso en cuarentena: quitarla no alcanza, hay que reiniciar
el controlador con `restart controller`.

| Falla | Qué imita |
|---|---|
| Pin ocupado | Otro proceso usa la línea, por ejemplo el software del CIOp: `GPIO busy` |
| Pin colgado | Una llamada a esa línea no vuelve nunca |
| Chip colgado | `gpiochip_open` no vuelve nunca |

## Supuestos a confirmar en el laboratorio

- Una línea liberada conserva su último nivel.
- El shutter tarda 0,2 s y un resorte lo cierra al bajar el pin (`TIEMPO_SHUTTER`).
- Una entrada flotante sin pull se lee como 0.
- `ENTRADA_GPIO` es un contacto seco.

## Limitaciones conocidas

- Una llamada nativa colgada real podría retener el GIL y frenar todo el proceso. En el
  simulador se bloquea solo el hilo que la hizo, así que el resultado puede ser más benigno.
- PWM no está implementado: ningún driver del catálogo lo usa.

## Pruebas

```sh
pytest tests/test_pi_sim.py --no-cov --ds=config.settings.test
```
