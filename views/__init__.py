"""Пакет представлений кабинета.

`api` экспортируется отдельно: blueprint API нужно освободить от CSRF
в app.py, а BLUEPRINTS содержит его же для регистрации.
"""

from views import admin, api, auth, client  # noqa: F401

BLUEPRINTS = (auth.bp, client.bp, admin.bp, api.bp)

__all__ = ["BLUEPRINTS", "admin", "api", "auth", "client"]