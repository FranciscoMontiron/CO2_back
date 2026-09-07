"""Configuracion comun a todos los entornos de CO2_back.

Las decisiones que explican esta configuracion estan documentadas en
``docs/adr/``. Cuando algo aca parezca arbitrario, el ADR correspondiente
tiene el porque.
"""

from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env()

# ─────────────────────────────────────────────────────────────────────────────
#  Seguridad
# ─────────────────────────────────────────────────────────────────────────────
SECRET_KEY = env("DJANGO_SECRET_KEY", default="inseguro-solo-para-desarrollo")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])
CSRF_TRUSTED_ORIGINS = env.list("DJANGO_CSRF_TRUSTED_ORIGINS", default=[])

# nginx termina TLS (RNF003); sin esto Django no se entera de que la peticion
# original venia por https y genera URLs absolutas con el esquema equivocado.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True

# ─────────────────────────────────────────────────────────────────────────────
#  Aplicaciones
# ─────────────────────────────────────────────────────────────────────────────
DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "corsheaders",
    "drf_spectacular",
    # NOTA (ADR-0003): se instala `channels` pero deliberadamente NO `daphne`.
    # Con `daphne` en INSTALLED_APPS, Django reemplaza `runserver` por su
    # version ASGI y el servicio `api` dejaria de ser WSGI puro, que es
    # justamente lo que el split evita. Daphne se invoca como binario en el
    # servicio `ws`, no como app de Django.
    "channels",
]

LOCAL_APPS: list[str] = [
    # El modelo de dominio se agrega cuando se cierre el Diagrama de Clases v4.
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# ─────────────────────────────────────────────────────────────────────────────
#  Base de datos (RNF008, ADR-0001)
# ─────────────────────────────────────────────────────────────────────────────
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": env("MYSQL_DATABASE", default="co2"),
        "USER": env("MYSQL_USER", default="co2"),
        "PASSWORD": env("MYSQL_PASSWORD", default=""),
        "HOST": env("MYSQL_HOST", default="mysql"),
        "PORT": env.int("MYSQL_PORT", default=3306),
        "CONN_MAX_AGE": 60,
        "OPTIONS": {
            "charset": "utf8mb4",
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
        },
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ─────────────────────────────────────────────────────────────────────────────
#  Redis: bus unico con bases logicas separadas (ADR-0004)
# ─────────────────────────────────────────────────────────────────────────────
REDIS_URL = env("REDIS_URL", default="redis://redis:6379")
REDIS_DB_PUBSUB = env.int("REDIS_DB_PUBSUB", default=0)
REDIS_DB_CHANNELS = env.int("REDIS_DB_CHANNELS", default=1)
REDIS_DB_HEARTBEAT = env.int("REDIS_DB_HEARTBEAT", default=2)
REDIS_DB_CACHE = env.int("REDIS_DB_CACHE", default=3)

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [f"{REDIS_URL}/{REDIS_DB_CHANNELS}"]},
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": f"{REDIS_URL}/{REDIS_DB_CACHE}",
    }
}

# TTL del heartbeat del controlador (ADR-0002). Si vence, el front debe mostrar
# SIN CONEXION AL CONTROLADOR en vez de telemetria congelada.
CONTROLLER_HEARTBEAT_TTL = env.int("CONTROLLER_HEARTBEAT_TTL", default=3)
CONTROLLER_HEARTBEAT_KEY = "controller:heartbeat"

# ─────────────────────────────────────────────────────────────────────────────
#  API
# ─────────────────────────────────────────────────────────────────────────────
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}

SPECTACULAR_SETTINGS = {
    "TITLE": "CO2_back - API del sistema de grabado laser",
    "DESCRIPTION": "Backend del SCADA para el grabado de LPGs con laser de CO2.",
    "VERSION": "0.1.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

# ─────────────────────────────────────────────────────────────────────────────
#  Internacionalizacion
# ─────────────────────────────────────────────────────────────────────────────
LANGUAGE_CODE = "es-ar"
TIME_ZONE = "America/Argentina/Buenos_Aires"
USE_I18N = True
# Los timestamps se guardan en UTC. La telemetria correlaciona con eventos de
# hardware, y un cambio de horario no puede introducir ambiguedad ni saltos.
USE_TZ = True

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ─────────────────────────────────────────────────────────────────────────────
#  Logging
# ─────────────────────────────────────────────────────────────────────────────
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "estandar": {
            "format": "{asctime} {levelname} {name} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "consola": {
            "class": "logging.StreamHandler",
            "formatter": "estandar",
        },
    },
    "root": {"handlers": ["consola"], "level": "INFO"},
    "loggers": {
        "django.db.backends": {"level": "WARNING"},
    },
}
