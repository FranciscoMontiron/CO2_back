"""Fixtures compartidas por la suite.

Construyen el minimo necesario para que cada prueba sea legible: si una prueba
necesita una red, pide `red` y no arma el lote, el tipo y la fecha a mano.
"""

import datetime

import pytest

from comun.enums import AccionUmbral, CriterioFin, Severidad, TipoPeriodo, TipoRed
from comun.enums import VariableMonitoreada as Var


@pytest.fixture
def rol(db):
    """Devuelve el rol de operador."""
    from comun.enums import TipoRol
    from usuarios.models import Rol

    return Rol.objects.create(name="Operador", codigo=TipoRol.OPERADOR)


@pytest.fixture
def usuario(db, rol):
    """Devuelve un usuario activo con rol de operador y contrasena conocida."""
    from usuarios.models import Usuario

    u = Usuario(username="mcurie", first_name="Marie", last_name="Curie", rol=rol)
    u.set_password("radio-1898")
    u.save()
    return u


@pytest.fixture
def lote(db):
    """Devuelve un lote vacio."""
    from fabricacion.models import Lote

    return Lote.objects.create(
        numero=3, descripcion="Lote de prueba", fecha_inicio=datetime.date(2026, 10, 1)
    )


@pytest.fixture
def red(db, lote):
    """Devuelve una red LPG del lote de prueba, sin marcas ni espectro."""
    from fabricacion.models import Red

    return Red.objects.create(
        numero=12, lote=lote, tipo=TipoRed.LPG, fecha_fabricacion=datetime.date(2026, 10, 2)
    )


@pytest.fixture
def procedimiento(db):
    """Devuelve una receta de medicion de 100 nm con 0,05 nm de resolucion."""
    from fabricacion.models import Procedimiento

    return Procedimiento.objects.create(
        titulo="Barrido estandar", version="1.0", span=100.0, resolucion=0.05
    )


@pytest.fixture
def periodo_constante(db):
    """Devuelve un perfil de periodo constante de 500 um."""
    from programas.models import Periodo

    return Periodo.objects.create(tipo=TipoPeriodo.CONSTANTE, periodo_base=500.0)


@pytest.fixture
def programa(db, periodo_constante, usuario):
    """Devuelve un programa con un paso, valido y listo para validar."""
    from programas.models import Paso, Programa

    p = Programa.objects.create(
        codigo="PRG-001",
        nombre="Grabado LPG estandar",
        tipo_red=TipoRed.LPG,
        potencia_objetivo=12.5,
        criterio_fin=CriterioFin.CANT_MARCAS,
        valor_criterio_fin=40,
        periodo=periodo_constante,
        creado_por=usuario,
    )
    Paso.objects.create(programa=p, orden=1, etiqueta="G01", parametro=500.0, tiempo_de_pulso=120)
    return p


@pytest.fixture
def configuracion(db, usuario):
    """Devuelve una configuracion con un umbral de temperatura de agua."""
    from operacion.models import Configuracion, Umbral

    c = Configuracion.objects.create(nombre="Operacion normal", definida_por=usuario)
    Umbral.objects.create(
        configuracion=c,
        variable=Var.TEMPERATURA_AGUA,
        valor_min=15.0,
        valor_max=28.0,
        severidad=Severidad.CRITICA,
        accion=AccionUmbral.PARADA_EMERGENCIA,
    )
    return c


@pytest.fixture
def registro(db, programa, red, configuracion, usuario):
    """Devuelve una corrida real en curso, con un operador asignado."""
    from trazabilidad.models import RegistroDeFabricacion

    r = RegistroDeFabricacion.objects.create(
        codigo="ENS-0001", programa=programa, red=red, configuracion=configuracion
    )
    r.operadores.add(usuario)
    return r
