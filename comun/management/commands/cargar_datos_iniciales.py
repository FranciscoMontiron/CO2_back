"""Carga el minimo necesario para que el sistema arranque usable.

Sin opciones carga los roles y la configuracion de operacion: es lo que necesita
cualquier despliegue. Con ``--con-ejemplo`` agrega datos de demostracion para el
front: un usuario por rol, programas, ensayos, alertas y una emergencia.

    docker compose exec api python manage.py cargar_datos_iniciales --con-ejemplo

Es idempotente: se puede volver a correr sin duplicar.
"""

import datetime
import math
from pathlib import Path
from types import SimpleNamespace

from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from comun.enums import (
    AccionUmbral,
    CriterioFin,
    EstadoEjecucion,
    EstadoRed,
    EstadoSistema,
    OrigenEmergencia,
    Severidad,
    TipoCheckpoint,
    TipoRol,
)
from comun.enums import VariableMonitoreada as Var
from fabricacion.models import Lote, Marca, Procedimiento, Red
from operacion.models import Configuracion, EventoEmergencia, Umbral
from programas.models import Programa
from programas.serializers import ProgramaSerializer
from trazabilidad.models import RegistroDeFabricacion
from usuarios.models import Rol, Usuario

# Rangos de operacion de arranque. Son un punto de partida razonable para un
# tubo de CO2 de laboratorio, NO valores validados por el CIOp: hay que
# revisarlos antes de operar sobre hardware real.
UMBRALES = [
    (Var.TEMPERATURA_AGUA, 15.0, 28.0, Severidad.CRITICA, AccionUmbral.PARADA_EMERGENCIA),
    (Var.CAUDAL_REFRIGERANTE, 2.0, 10.0, Severidad.CRITICA, AccionUmbral.PARADA_EMERGENCIA),
    (Var.POTENCIA_LASER, 0.0, 30.0, Severidad.ADVERTENCIA, AccionUmbral.ALERTA),
    (Var.TENSION_AT, 0.0, 30.0, Severidad.CRITICA, AccionUmbral.BLOQUEAR_COMANDO),
    (Var.CORRIENTE_AT, 0.0, 25.0, Severidad.CRITICA, AccionUmbral.PARADA_EMERGENCIA),
    (Var.POSICION_MOTOR, 0.0, 300.0, Severidad.ADVERTENCIA, AccionUmbral.ALERTA),
    (Var.TEMPERATURA_AMBIENTE, 10.0, 35.0, Severidad.INFO, AccionUmbral.ALERTA),
]

ROLES = [
    (TipoRol.ADMINISTRADOR, "Administrador", "Gestiona usuarios, roles y configuracion."),
    (TipoRol.INVESTIGADOR, "Investigador", "Disena programas y analiza resultados."),
    (TipoRol.OPERADOR, "Operador", "Ejecuta programas y atiende alertas."),
]

# CSV real exportado por el Yokogawa AQ6370B del laboratorio. El nombre dice
# lote 05, LPG 01: es el espectro del primer ensayo del lote 5 de ejemplo.
CSV_REAL = Path(__file__).resolve().parents[2] / "datos_ejemplo" / "L05LPG01.csv"

# Usuarios de ejemplo, uno por rol. La contrasena es el nombre de usuario: sirven
# para desarrollo y para la demo, NUNCA para un despliegue.
USUARIOS_EJEMPLO = [
    ("admin", "Admin", "CIOp", TipoRol.ADMINISTRADOR),
    ("investigador", "M.", "Alvarez", TipoRol.INVESTIGADOR),
    ("operador", "L.", "Perez", TipoRol.OPERADOR),
]

# Los tres programas del mockup del front, con su distancia y su desplazamiento.
# Los pulsos se derivan del invariante longitud = marcas x periodo.
PROGRAMAS_EJEMPLO = [
    ("Programa A", 10.0, 400, 550.0, CriterioFin.LONGITUD, 10.0, None),
    ("Programa B", 8.0, 350, 400.0, CriterioFin.LONGITUD, 8.0, None),
    ("Programa C", 12.0, 500, 600.0, CriterioFin.CANT_MARCAS, None, 25),
]

# Ensayos de ejemplo: (lote, red, programa, operador, estado, estado de la red,
# hace cuantas horas, minutos que duro, notas, espectro). El espectro es "real"
# para el CSV del laboratorio, (lambda, profundidad) para uno sintetico, o None.
ENSAYOS_EJEMPLO = [
    (5, 1, "Programa A", "operador", EstadoEjecucion.COMPLETADO, EstadoRed.VIABLE,
     72, 14, "Ensayo nominal", "real"),
    (5, 2, "Programa A", "operador", EstadoEjecucion.COMPLETADO, EstadoRed.VIABLE,
     50, 13, "Ensayo nominal", (1585.0, 9.0)),
    (5, 3, "Programa B", "investigador", EstadoEjecucion.COMPLETADO, EstadoRed.INVIABLE,
     30, 11, "Resonancia por debajo de lo esperado", (1530.0, 2.5)),
    (6, 1, "Programa C", "operador", EstadoEjecucion.INTERRUMPIDO, EstadoRed.ROTA,
     20, 6, "Parada de emergencia: temperatura del agua", None),
    (6, 2, "Programa B", "investigador", EstadoEjecucion.ABORTADO, EstadoRed.ROTA,
     8, 3, "Fallo lazo laser", None),
    (6, 3, "Programa A", "operador", EstadoEjecucion.COMPLETADO, EstadoRed.VIABLE,
     2, 14, "Ensayo nominal", (1622.0, 15.0)),
]  # fmt: skip


class Command(BaseCommand):
    """Carga roles, configuracion de operacion y, opcionalmente, datos de demo."""

    help = "Carga los datos minimos para que el sistema arranque usable."

    def add_arguments(self, parser) -> None:
        """Declara las opciones del comando.

        Args:
            parser: El parser de argumentos del comando.
        """
        parser.add_argument(
            "--con-ejemplo",
            action="store_true",
            help="Ademas de roles y umbrales, carga usuarios, programas y ensayos de demo.",
        )

    @transaction.atomic
    def handle(self, *args, **opciones) -> None:
        """Ejecuta la carga.

        Args:
            *args: Sin uso.
            **opciones: Opciones de linea de comandos.
        """
        self._cargar_roles()
        self._cargar_configuracion()
        if opciones["con_ejemplo"]:
            self._cargar_ejemplo()
        self.stdout.write(self.style.SUCCESS("Carga inicial completa."))

    def _cargar_roles(self) -> None:
        """Crea los tres roles y le da al administrador todos los permisos."""
        for codigo, nombre, descripcion in ROLES:
            _, creado = Rol.objects.get_or_create(
                codigo=codigo, defaults={"name": nombre, "descripcion": descripcion}
            )
            if creado:
                self.stdout.write(f"  rol creado: {nombre}")
        admin = Rol.objects.get(codigo=TipoRol.ADMINISTRADOR)
        admin.permissions.set(Permission.objects.all())

    def _cargar_configuracion(self) -> None:
        """Crea la configuracion de arranque con un umbral por variable."""
        config, creada = Configuracion.objects.get_or_create(
            nombre="Operacion de arranque",
            version=1,
            defaults={
                "descripcion": (
                    "Rangos de partida. PENDIENTE de validacion con el CIOp antes "
                    "de operar sobre hardware real."
                )
            },
        )
        for variable, minimo, maximo, severidad, accion in UMBRALES:
            Umbral.objects.get_or_create(
                configuracion=config,
                variable=variable,
                defaults={
                    "valor_min": minimo,
                    "valor_max": maximo,
                    "severidad": severidad,
                    "accion": accion,
                },
            )
        if not Configuracion.objects.filter(activa=True).exists():
            config.activar()
        if creada:
            self.stdout.write(f"  configuracion creada con {len(UMBRALES)} umbrales")

    # ── datos de demostracion ───────────────────────────────────────────────

    def _cargar_ejemplo(self) -> None:
        """Carga usuarios, programas, ensayos, alertas y una emergencia de demo."""
        if RegistroDeFabricacion.objects.filter(codigo="ENS-2026-00001").exists():
            self.stdout.write("  ya hay datos de ejemplo, no se duplican")
            return

        usuarios = self._cargar_usuarios()
        programas = self._cargar_programas(usuarios["admin"])
        procedimiento, _ = Procedimiento.objects.get_or_create(
            titulo="Barrido AQ6370B",
            version="1.0",
            defaults={
                "span": 500.0,
                "resolucion": 0.1,
                "sensibilidad": "HIGH1",
                "escala_vertical": 3.0,
                "corriente_sld": 150.0,
            },
        )
        configuracion = Configuracion.objects.get(activa=True)
        ahora = timezone.now()
        interrumpido = None

        for n, fila in enumerate(ENSAYOS_EJEMPLO, start=1):
            num_lote, num_red, prog, oper, estado, est_red, horas, mins, notas, esp = fila
            inicio = ahora - datetime.timedelta(hours=horas)
            lote, _ = Lote.objects.get_or_create(
                numero=num_lote,
                defaults={"descripcion": "Lote de ejemplo", "fecha_inicio": inicio.date()},
            )
            programa = programas[prog]
            red, _ = Red.objects.get_or_create(
                lote=lote,
                numero=num_red,
                defaults={
                    "tipo": programa.tipo_red,
                    "estado": est_red,
                    "fecha_fabricacion": inicio.date(),
                },
            )
            self._grabar_marcas(red, programa, completo=estado == EstadoEjecucion.COMPLETADO)
            registro = RegistroDeFabricacion.objects.create(
                codigo=f"ENS-2026-{n:05d}",
                programa=programa,
                red=red,
                configuracion=configuracion,
                procedimiento=procedimiento,
                estado=estado,
                inicio=inicio,
                fin=inicio + datetime.timedelta(minutes=mins, seconds=n * 7),
                observaciones=notas,
            )
            registro.operadores.add(usuarios[oper])
            self._checkpoints(registro, programa)
            if esp is not None:
                self._caracterizar(red, procedimiento, esp)
            if estado == EstadoEjecucion.INTERRUMPIDO:
                interrumpido = registro

        self._cargar_eventos(configuracion, usuarios, interrumpido)
        self.stdout.write(
            f"  ejemplo cargado: {len(ENSAYOS_EJEMPLO)} ensayos, {len(programas)} programas"
        )
        self.stdout.write(
            "  usuarios de prueba (contrasena = usuario): "
            + ", ".join(u for u, *_ in USUARIOS_EJEMPLO)
        )

    def _cargar_usuarios(self) -> dict:
        """Crea un usuario por rol. El admin ademas entra al admin de Django."""
        usuarios = {}
        for username, nombre, apellido, rol in USUARIOS_EJEMPLO:
            usuario, creado = Usuario.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": nombre,
                    "last_name": apellido,
                    "rol": Rol.objects.get(codigo=rol),
                    "is_staff": rol == TipoRol.ADMINISTRADOR,
                    "is_superuser": rol == TipoRol.ADMINISTRADOR,
                },
            )
            if creado:
                usuario.set_password(username)
                usuario.save()
            usuarios[username] = usuario
        return usuarios

    def _cargar_programas(self, autor) -> dict:
        """Crea los programas del mockup a traves del serializador de la API.

        Se usa el serializador y no los modelos directamente para que los datos
        de ejemplo pasen por la misma traduccion que los que cargue el front.
        """
        contexto = {"request": SimpleNamespace(user=autor)}
        programas = {}
        for nombre, mw, pulso, despl, criterio, distancia, pulsos in PROGRAMAS_EJEMPLO:
            existente = Programa.objects.filter(nombre=nombre, activo=True).first()
            if existente:
                programas[nombre] = existente
                continue
            serializador = ProgramaSerializer(
                data={
                    "nombre": nombre,
                    "potencia_objetivo_mw": mw,
                    "duracion_pulso_ms": pulso,
                    "desplazamiento_um": despl,
                    "criterio_fin": criterio,
                    "distancia_mm": distancia,
                    "pulsos": pulsos,
                },
                context=contexto,
            )
            serializador.is_valid(raise_exception=True)
            programas[nombre] = serializador.save()
        return programas

    def _grabar_marcas(self, red: Red, programa: Programa, completo: bool) -> None:
        """Graba las marcas que indica el programa, o la mitad si no termino."""
        if red.marcas.exists():
            return
        datos = ProgramaSerializer(programa).data
        total = datos["pulsos"] if completo else datos["pulsos"] // 2
        Marca.objects.bulk_create(
            [
                Marca(
                    red=red,
                    numero=i,
                    posicion=i * datos["desplazamiento_um"],
                    cant_pulsos=1,
                    ciclo_trabajo=50.0,
                    tiempo_de_pulso=datos["duracion_pulso_ms"],
                )
                for i in range(1, total + 1)
            ]
        )

    def _checkpoints(self, registro: RegistroDeFabricacion, programa: Programa) -> None:
        """Asienta un checkpoint al armar y otro durante el grabado."""
        paso = programa.pasos.first()
        for estado, temperatura in [(EstadoSistema.LISTO, 19.8), (EstadoSistema.GRABANDO, 21.4)]:
            registro.registrar_checkpoint(
                tipo=TipoCheckpoint.MARCA,
                estado_sistema=estado,
                paso=paso,
                valores={
                    Var.TEMPERATURA_AGUA: temperatura,
                    Var.CAUDAL_REFRIGERANTE: 4.2,
                    Var.POTENCIA_LASER: programa.potencia_objetivo,
                },
            )

    def _caracterizar(self, red: Red, procedimiento: Procedimiento, espectro) -> None:
        """Carga el espectro de la red y detecta sus resonancias.

        Args:
            red: La red caracterizada.
            procedimiento: La receta de medicion.
            espectro: ``"real"`` para el CSV del laboratorio, o
                ``(lambda, profundidad)`` para uno sintetico.
        """
        if espectro == "real":
            contenido = CSV_REAL.read_text(encoding="latin-1")
        else:
            centro, profundidad = espectro
            filas = []
            for nm in range(1170, 1672, 2):
                # Valle lorentziano de unos 8 nm de ancho sobre una base de -2 dB,
                # con una ondulacion leve para que no parezca dibujado a mano.
                valle = profundidad / (1 + ((nm - centro) / 4.0) ** 2)
                filas.append(f"{nm}.0,{-2.0 - valle - 0.2 * math.sin(nm / 7):.3f}")
            contenido = "\n".join(filas)
        procedimiento.importar(red, contenido).detectar_resonancias()

    def _cargar_eventos(self, configuracion, usuarios, interrumpido) -> None:
        """Genera alertas desde los umbrales y una parada de emergencia."""
        temperatura = configuracion.umbrales.get(variable=Var.TEMPERATURA_AGUA)
        caudal = configuracion.umbrales.get(variable=Var.CAUDAL_REFRIGERANTE)
        potencia = configuracion.umbrales.get(variable=Var.POTENCIA_LASER)

        potencia.disparar(31.2)
        caudal.disparar(1.6).reconocer(usuarios["operador"])
        temperatura.disparar(29.4).reconocer(usuarios["operador"])

        evento = EventoEmergencia.objects.create(
            origen=OrigenEmergencia.UMBRAL,
            descripcion="Temperatura del agua por encima del maximo (29,4 C).",
            tiempo_respuesta_ms=140,
            registro=interrumpido,
        )
        evento.rearmar(usuarios["operador"])
