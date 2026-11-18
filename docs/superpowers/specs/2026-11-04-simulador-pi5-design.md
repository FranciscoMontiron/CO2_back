# Simulador de Raspberry Pi 5: diseño

## Para qué

Probar los componentes GPIO desde la PC, con el mismo camino de código que en el
laboratorio, para que un error de software aparezca antes de ir a la Pi. También
sirve para forzar fallas de pines y ver cómo reacciona el SCADA.

## Objetivos

- Contenedor aparte con página propia, separado del SCADA.
- Los componentes dados de alta en el SCADA aparecen solos en la página.
- Cada componente se conecta o desconecta desde la página, y empieza desconectado.
- El controlador corre en modo `gpio` con gpiozero y los drivers reales.
- Fallas a pedido: pin ocupado, pin colgado y chip colgado.

## Fuera de alcance

- Electricidad: caídas de tensión, ruido, consumo, contactos reales.
- Tiempos reales: las latencias no valen como medición.
- Grabado automático: sigue deshabilitado en modo GPIO, igual que en la Pi.
- Autenticación de la página: es solo local, publicada en `127.0.0.1`.

## Cómo funciona

```text
SCADA → API → Redis → controlador → drivers → gpiozero → LGPIOFactory
                                                              ↓
                                     doble de lgpio  (reemplaza a la librería C)
                                                              ↓ TCP
                                     contenedor pi-sim: tablero + modelos + página
```

Solo se reemplaza el módulo `lgpio`, por `PYTHONPATH`, en una imagen exclusiva del
simulador. No cambia el código del controlador ni la imagen que corre en la Pi.

## Reglas de fidelidad

- Errores de `lgpio`: `GPIO busy`, línea no reservada y escritura sobre una entrada.
- `spi_open` falla, igual que hoy en la Pi, que no monta `/dev/spidev*`. gpiozero cae
  a SPI por software y el simulador modela el MCP3008 a nivel de pines.
- Si el proceso muere, se liberan sus líneas y la salida conserva su último nivel.
- Un componente desconectado no reacciona, y el controlador no se entera si el driver
  no tiene realimentación. Es lo que pasaría con un cable suelto.

## Supuestos a confirmar en el laboratorio

- El nivel que conserva una línea liberada.
- El shutter tarda 0,2 s y un resorte lo cierra cuando el pin baja.
- Una entrada flotante sin pull se lee como 0.
- El `ENTRADA_GPIO` es un contacto seco: a masa con pull-up, a 3,3 V sin él.

## Limitaciones

- "Armado" es memoria del driver: no existe en el pin, por eso se ve solo en el SCADA.
- Una llamada nativa colgada real podría retener el GIL y frenar todo el proceso. El
  simulador solo bloquea el hilo que la hizo, así que podría ser más benigno que la Pi.

## Restricciones

Rama `feature-componentes-v2`, sin commits ni push. El usuario hace los commits.
