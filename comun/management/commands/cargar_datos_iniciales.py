"""Carga el minimo necesario para que el sistema arranque usable.

Deja el sistema en un estado desde el que se puede entrar al admin, ver un
ensayo completo y conectar el front, sin que nadie tenga que cargar nada a mano.

    docker compose exec api python manage.py cargar_datos_iniciales

Es idempotente: se puede volver a correr sin duplicar.
"""

import datetime

from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from comun.enums import (
    AccionUmbral,
    CriterioFin,
    EstadoSistema,
    Severidad,
    TipoCheckpoint,
    TipoPeriodo,
    TipoRed,
    TipoRol,
)
from comun.enums import VariableMonitoreada as Var
from fabricacion.models import Espectro, Lote, Marca, PicoDeAtenuacion, Procedimiento, Red
from operacion.models import Configuracion, Umbral
from programas.models import Paso, Periodo, Programa
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


class Command(BaseCommand):
    """Carga roles, configuracion de operacion y un ensayo de ejemplo."""

    help = "Carga los datos minimos para que el sistema arranque usable."

    def add_arguments(self, parser) -> None:
        """Declara las opciones del comando.

        Args:
            parser: El parser de argumentos del comando.
        """
        parser.add_argument(
            "--con-ejemplo",
            action="store_true",
            help="Ademas de los roles y umbrales, carga un ensayo completo de ejemplo.",
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
            rol, creado = Rol.objects.get_or_create(
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

    def _cargar_ejemplo(self) -> None:
        """Carga un ensayo completo: programa, corrida, red, marcas y espectro."""
        if Red.objects.exists():
            self.stdout.write("  ya hay datos de ejemplo, no se duplican")
            return

        operador = Usuario.objects.filter(rol__codigo=TipoRol.OPERADOR).first()
        if operador is None:
            operador = Usuario.objects.create_user(
                username="operador",
                password="operador",
                first_name="Operador",
                last_name="de ejemplo",
                rol=Rol.objects.get(codigo=TipoRol.OPERADOR),
            )

        periodo = Periodo.objects.create(tipo=TipoPeriodo.CONSTANTE, periodo_base=500.0)
        programa = Programa.objects.create(
            codigo="PRG-EJEMPLO-001",
            nombre="LPG 500 um - 40 marcas",
            descripcion="Programa de ejemplo cargado por la carga inicial.",
            tipo_red=TipoRed.LPG,
            potencia_objetivo=12.5,
            criterio_fin=CriterioFin.CANT_MARCAS,
            valor_criterio_fin=40,
            periodo=periodo,
            creado_por=operador,
        )
        Paso.objects.create(
            programa=programa, orden=1, etiqueta="G00", parametro=0.0, tiempo_de_pulso=0
        )
        Paso.objects.create(
            programa=programa,
            orden=2,
            etiqueta="G01",
            parametro=500.0,
            repeticiones=40,
            tiempo_de_pulso=120,
        )
        programa.validar()

        lote = Lote.objects.create(
            numero=1, descripcion="Lote de ejemplo", fecha_inicio=datetime.date(2026, 10, 1)
        )
        red = Red.objects.create(
            numero=1, lote=lote, tipo=TipoRed.LPG, fecha_fabricacion=datetime.date(2026, 10, 2)
        )
        for i in range(1, 41):
            Marca.objects.create(
                red=red,
                numero=i,
                posicion=i * 500.0,
                cant_pulsos=10,
                ciclo_trabajo=50.0,
                tiempo_de_pulso=120,
            )

        procedimiento = Procedimiento.objects.create(
            titulo="Barrido estandar OSA", version="1.0", span=100.0, resolucion=0.05
        )
        # Espectro sintetico: linea de base plana con una resonancia marcada en el
        # centro. Alcanza para ver el grafico del front sin un CSV real del OSA.
        transmitancias = [-1.0] * 2000
        for i in range(950, 1051):
            transmitancias[i] = -1.0 - 25.0 * (1 - abs(i - 1000) / 50)
        Espectro.objects.create(
            red=red,
            procedimiento=procedimiento,
            fecha_captura=timezone.now(),
            longitud_onda_inicial=1500.0,
            transmitancias=transmitancias,
        )
        PicoDeAtenuacion.objects.create(
            red=red, longitud_onda=1550.0, transmitancia=-26.0, principal=True
        )

        registro = RegistroDeFabricacion.objects.create(
            codigo="ENS-EJEMPLO-0001",
            programa=programa,
            red=red,
            configuracion=Configuracion.objects.get(activa=True),
            procedimiento=procedimiento,
            observaciones="Ensayo de ejemplo cargado por la carga inicial.",
        )
        registro.operadores.add(operador)
        registro.registrar_checkpoint(
            tipo=TipoCheckpoint.MARCA,
            estado_sistema=EstadoSistema.GRABANDO,
            paso=programa.pasos.last(),
            valores={
                Var.TEMPERATURA_AGUA: 21.4,
                Var.CAUDAL_REFRIGERANTE: 4.2,
                Var.POTENCIA_LASER: 12.3,
            },
        )
        registro.finalizar()

        self.stdout.write(
            f"  ejemplo cargado: {registro.codigo} -> {red.codigo} "
            f"({red.cantidad_marcas} marcas, {red.longitud} mm)"
        )
