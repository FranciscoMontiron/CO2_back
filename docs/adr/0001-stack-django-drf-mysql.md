# ADR-0001 — Stack: Django + DRF + MySQL

**Estado:** Aceptada
**Fecha:** 2026-09-07

## Contexto

La Actividad 4 (Alcance y Requisitos) y la Matriz de Trazabilidad de Requisitos ya
fijaron el stack. Los requisitos que lo condicionan:

| Requisito | Contenido |
|---|---|
| RNF008 | Base de datos **MySQL**, ≥10.000 ensayos sin degradación |
| RNF007 | PEP8, cobertura de tests ≥70%, docstrings en toda función pública |
| RNF003 | HTTPS entre front y back |
| RF009 | Telemetría ≥ 1 Hz |
| RNF001 | Comandos críticos (E-Stop, actuadores) ≤ 500 ms |

## Decisión

Backend en **Django 5.2 LTS + Django REST Framework**, persistencia en **MySQL 8.4 LTS**.

## Consecuencias

- El ORM y el sistema de migraciones de Django cubren el requisito de evolución del
  esquema sin scripts SQL a mano.
- El admin de Django da gestión de usuarios y roles sin desarrollo propio, lo que
  descarga la pantalla de Administración del front.
- PEP8 y cobertura (RNF007) se hacen verificables en CI: `ruff` + `pytest-cov`
  con `fail_under = 70` configurado en `pyproject.toml`.
- Django es síncrono y request-driven. **No resuelve por sí solo RNF001 ni RF009** —
  ver ADR-0002 y ADR-0003.

## Alternativas descartadas

- **FastAPI / Node.js.** Técnicamente viables y con mejor perfil async, pero desviarse
  obliga a re-versionar la Actividad 4 y la Matriz de Trazabilidad. El costo de
  proceso supera la ganancia técnica, sobre todo porque las partes donde ese perfil
  async importaría (lazo de control, E-Stop) salen de Django igual por ADR-0002.
- **PostgreSQL / SQLite.** RNF008 dice MySQL de forma explícita.
