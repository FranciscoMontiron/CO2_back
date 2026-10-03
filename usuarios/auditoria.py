"""Auditoria automatica por senales (RN010).

El objetivo de #12 es que **una accion critica quede registrada aunque el
programador se olvide de registrarla**. Por eso la auditoria no se invoca a
mano desde las vistas: se engancha a las senales de guardado y borrado de los
modelos, y a la de inicio de sesion.

Quien hizo la accion y desde que IP no viajan en la senal; los captura
:class:`AuditoriaMiddleware` al entrar la peticion y los deja en una
``ContextVar``. Se usa ``ContextVar`` y no ``threading.local`` porque el
proyecto tiene un proceso ASGI (ADR-0003), donde varias peticiones comparten
hilo y un ``local`` mezclaria los usuarios entre si.

**Una falla al auditar NO se traga.** Si el asiento no se puede escribir, la
excepcion se propaga y, dentro de la transaccion, la accion se revierte. Es
deliberado: RN010 dice que lo critico se audita *siempre*, y la unica forma de
garantizarlo es que sin asiento no haya accion.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass

from django.contrib.auth.signals import user_logged_in
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models.signals import post_delete, post_save, pre_save
from django.forms.models import model_to_dict

from comun.enums import AccionAuditoria


@dataclass(frozen=True)
class _Contexto:
    """Quien esta actuando y desde donde."""

    usuario_id: int | None
    direccion_ip: str | None


_contexto: ContextVar[_Contexto | None] = ContextVar("auditoria", default=None)

# Que se audita, y si cada modelo es critico. Se excluyen a proposito los de alto
# volumen escritos por la maquina (Checkpoint, Marca, PicoDeAtenuacion, Espectro,
# Alerta): auditar cada checkpoint del controlador inflaria la bitacora hasta
# volverla inutil, y esos datos ya son trazables por si mismos.
MODELOS_AUDITADOS: dict[str, bool] = {
    # Seguridad y operacion: siempre criticos.
    "usuarios.Usuario": True,
    "usuarios.Rol": True,
    "operacion.Configuracion": True,
    "operacion.Umbral": True,
    "operacion.EventoEmergencia": True,
    # Dominio: se audita, pero no es critico.
    "programas.Programa": False,
    "programas.Paso": False,
    "programas.Periodo": False,
    "fabricacion.Lote": False,
    "fabricacion.Red": False,
    "fabricacion.Procedimiento": False,
    "trazabilidad.RegistroDeFabricacion": False,
}

# Campos que nunca se copian a la bitacora. La contrasena va hasheada, pero un
# hash en un registro que leen muchos sigue siendo material para fuerza bruta.
_CAMPOS_SENSIBLES = {"password"}


class AuditoriaMiddleware:
    """Captura usuario e IP de cada peticion para que los vean las senales."""

    def __init__(self, get_response):
        """Guarda el siguiente eslabon de la cadena de middleware.

        Args:
            get_response: El callable que procesa la peticion.
        """
        self.get_response = get_response

    def __call__(self, request):
        """Procesa la peticion con el contexto de auditoria cargado.

        Args:
            request: La peticion HTTP.

        Returns:
            La respuesta del siguiente eslabon.
        """
        usuario = getattr(request, "user", None)
        token = _contexto.set(
            _Contexto(
                usuario_id=usuario.pk if usuario is not None and usuario.is_authenticated else None,
                direccion_ip=_ip_de(request),
            )
        )
        try:
            return self.get_response(request)
        finally:
            _contexto.reset(token)


def _ip_de(request) -> str | None:
    """Obtiene la IP del cliente.

    nginx termina TLS y reenvia (RNF003), asi que ``REMOTE_ADDR`` es la IP de
    nginx. La del cliente real viene en ``X-Forwarded-For``.

    Args:
        request: La peticion HTTP.

    Returns:
        La IP del cliente, o ``None`` si no se puede determinar.
    """
    reenviada = request.META.get("HTTP_X_FORWARDED_FOR")
    if reenviada:
        return reenviada.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def _etiqueta(instancia) -> str:
    """Devuelve ``app_label.Modelo`` de una instancia."""
    return f"{instancia._meta.app_label}.{instancia.__class__.__name__}"


def _foto(instancia) -> dict:
    """Serializa el estado de una instancia, sin campos sensibles.

    Args:
        instancia: La instancia del modelo.

    Returns:
        Un diccionario serializable a JSON.
    """
    datos = model_to_dict(instancia)
    return {k: v for k, v in datos.items() if k not in _CAMPOS_SENSIBLES}


def _json(datos: dict) -> str:
    """Serializa a JSON tolerando fechas, decimales y relaciones."""
    return json.dumps(datos, cls=DjangoJSONEncoder, ensure_ascii=False, default=str)


def _asentar(accion: str, instancia, anterior: str = "", nuevo: str = "") -> None:
    """Escribe un asiento de auditoria.

    Args:
        accion: El tipo de accion (:class:`AccionAuditoria`).
        instancia: La instancia afectada.
        anterior: Estado previo serializado.
        nuevo: Estado posterior serializado.
    """
    from .models import RegistroAuditoria

    ctx = _contexto.get()
    RegistroAuditoria.objects.create(
        usuario_id=ctx.usuario_id if ctx else None,
        direccion_ip=ctx.direccion_ip if ctx else None,
        accion=accion,
        entidad=_etiqueta(instancia),
        codigo_entidad=str(instancia.pk),
        valor_anterior=anterior,
        valor_nuevo=nuevo,
        critica=MODELOS_AUDITADOS.get(_etiqueta(instancia), False),
    )


def _antes_de_guardar(sender, instance, **kwargs) -> None:
    """Toma la foto del estado previo, para poder registrar que cambio."""
    if _etiqueta(instance) not in MODELOS_AUDITADOS or instance.pk is None:
        return
    previo = sender._default_manager.filter(pk=instance.pk).first()
    instance._auditoria_previo = _foto(previo) if previo is not None else None


def _despues_de_guardar(sender, instance, created, **kwargs) -> None:
    """Registra la creacion o la modificacion.

    En las modificaciones solo se asientan los campos que cambiaron: la bitacora
    tiene que poder leerse, y un volcado completo por cada cambio de un campo la
    vuelve ilegible.
    """
    if _etiqueta(instance) not in MODELOS_AUDITADOS:
        return
    actual = _foto(instance)
    if created:
        _asentar(AccionAuditoria.CREAR, instance, nuevo=_json(actual))
        return

    previo = getattr(instance, "_auditoria_previo", None) or {}
    cambios = {k: v for k, v in actual.items() if previo.get(k) != v}
    if not cambios:
        return  # un save() sin cambios reales no es una accion
    _asentar(
        AccionAuditoria.MODIFICAR,
        instance,
        anterior=_json({k: previo.get(k) for k in cambios}),
        nuevo=_json(cambios),
    )


def _despues_de_borrar(sender, instance, **kwargs) -> None:
    """Registra el borrado, con el estado que tenia la instancia."""
    if _etiqueta(instance) not in MODELOS_AUDITADOS:
        return
    _asentar(AccionAuditoria.ELIMINAR, instance, anterior=_json(_foto(instance)))


def _al_iniciar_sesion(sender, request, user, **kwargs) -> None:
    """Registra los inicios de sesion."""
    from .models import RegistroAuditoria

    RegistroAuditoria.objects.create(
        usuario=user,
        accion=AccionAuditoria.LOGIN,
        entidad="usuarios.Usuario",
        codigo_entidad=str(user.pk),
        direccion_ip=_ip_de(request) if request is not None else None,
    )


def conectar() -> None:
    """Engancha los receptores de auditoria. Se llama desde ``ready()``."""
    pre_save.connect(_antes_de_guardar, dispatch_uid="auditoria_pre_save")
    post_save.connect(_despues_de_guardar, dispatch_uid="auditoria_post_save")
    post_delete.connect(_despues_de_borrar, dispatch_uid="auditoria_post_delete")
    user_logged_in.connect(_al_iniciar_sesion, dispatch_uid="auditoria_login")
