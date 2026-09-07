# Registro de Decisiones de Arquitectura (ADR)

Cada archivo documenta **una** decisión: el contexto en que se tomó, qué se decidió,
qué consecuencias acarrea y qué alternativas se descartaron y por qué.

Un ADR no se edita cuando la decisión cambia: se marca como `Reemplazada por ADR-XXXX`
y se escribe uno nuevo. El historial de por qué el sistema es como es tiene que
sobrevivir a los cambios de opinión.

| ADR | Título | Estado |
|---|---|---|
| [0001](0001-stack-django-drf-mysql.md) | Stack: Django + DRF + MySQL | Aceptada |
| [0002](0002-proceso-controlador-separado.md) | El lazo de control y el E-Stop viven fuera de Django | Aceptada |
| [0003](0003-telemetria-websocket-split-wsgi-asgi.md) | Telemetría por WebSocket con servidor WSGI/ASGI separado | Aceptada |
| [0004](0004-redis-como-bus.md) | Redis como bus único (pub/sub + channel layer + heartbeat) | Aceptada |
| [0005](0005-simulacion-arm64-qemu.md) | Simulación de la Pi 5 sobre arm64 emulado | Aceptada |
| [0006](0006-almacenamiento-nvme-retencion.md) | Arranque desde NVMe y política de retención de telemetría | **Propuesta** |

## Estados

- **Propuesta** — escrita, pendiente de confirmación (del equipo, del CIOp o del presupuesto).
- **Aceptada** — vigente, el código debe respetarla.
- **Reemplazada** — histórica, apunta al ADR que la sustituye.
