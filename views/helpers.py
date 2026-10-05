"""Декораторы доступа и общие помощники представлений."""

from __future__ import annotations

import functools
from typing import Any, Callable

from flask import abort, current_app, flash, redirect, request, session, url_for

from models import get_user


def current_user() -> dict[str, Any] | None:
    """Пользователь текущей сессии. Загружается из БД, а не из session:
    смена роли применяется сразу, без ожидания истечения cookie."""
    uid = session.get("uid")
    if not uid:
        return None
    user = get_user(int(uid))
    if user is None:
        session.clear()
        return None
    return user


def login_required(view: Callable) -> Callable:
    @functools.wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        if current_user() is None:
            flash("Нужно войти в кабинет.", "warning")
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view: Callable) -> Callable:
    @functools.wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        user = current_user()
        if user is None:
            flash("Нужно войти в кабинет.", "warning")
            return redirect(url_for("auth.login", next=request.path))
        if user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def wants_json() -> bool:
    """Запрос считаем API-вызовом, если так его назвал клиент."""
    return (request.path.startswith("/api/")
            or request.headers.get("X-Requested-With") == "XMLHttpRequest")


def format_dt(value: str | None, fmt: str = "%d.%m.%Y %H:%M") -> str:
    """Дата из базы в вид «01.10.2026 14:30»."""
    if not value:
        return "—"
    try:
        from datetime import datetime
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").strftime(fmt)
    except ValueError:
        return value


def format_size(size: int) -> str:
    """Размер файла человеческим языком."""
    value = float(size or 0)
    for unit in ("Б", "КБ", "МБ"):
        if value < 1024 or unit == "МБ":
            return f"{value:.0f} {unit}" if unit == "Б" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} МБ"


def flash_errors(form: Any) -> None:
    """Показать ошибки формы одним списком, а не по одной."""
    # В wtforms 3.x `form_errors` — свойство, а не метод. Раньше здесь стояло
    # `form.form_errors()`, из-за чего любая форма с ошибками падала в 500
    # вместо показа ошибок. Поддерживаем оба варианта.
    raw = getattr(form, "form_errors", None)
    messages = [str(e) for e in (raw() if callable(raw) else raw or [])]
    for field_errors in form.errors.values():
        messages.extend(str(e) for e in field_errors)
    for message in dict.fromkeys(messages):
        flash(message, "error")


def register_jinja(app: Any) -> None:
    """Общие переменные и фильтры шаблонов."""
    import json as _json

    from markupsafe import Markup

    from config import Config
    from models import (
        FILE_CATEGORIES, NOTIFICATION_TYPES, STATUS_LABELS, STATUSES,
        STATUS_TRANSITIONS,
    )
    from services_catalog import all_services, services_by_level

    @app.context_processor
    def inject() -> dict[str, Any]:
        user = current_user()
        unread = 0
        if user:
            from models import unread_count
            unread = unread_count(user["id"])
        from flask_wtf.csrf import generate_csrf
        return {
            "current_user": user,
            "unread_notifications": unread,
            # Форма выхода в шапке — вне объекта формы, токен генерируем здесь.
            "logout_csrf": Markup('<input type="hidden" name="csrf_token" value="{}">'
                                  .format(generate_csrf())),
            "brand_name": Config.BRAND_NAME,
            "brand_sub": Config.BRAND_SUB,
            "status_labels": STATUS_LABELS,
            "statuses": STATUSES,
            "status_transitions": STATUS_TRANSITIONS,
            "file_categories": FILE_CATEGORIES,
            "notification_types": NOTIFICATION_TYPES,
            "services": all_services(),
            "services_grouped": services_by_level(),
        }

    @app.template_filter("dt")
    def _dt(value: str | None) -> str:
        return format_dt(value)

    @app.template_filter("dts")
    def _dts(value: str | None) -> str:
        return format_dt(value, "%d.%m.%Y")

    @app.template_filter("size")
    def _size(value: int) -> str:
        return format_size(value)

    @app.template_filter("json_pretty")
    def _json_pretty(value: Any) -> str:
        try:
            data = _json.loads(value) if isinstance(value, str) else value
            return _json.dumps(data, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return "{}"

    @app.template_filter("nl2br")
    def _nl2br(value: str | None) -> str:
        from markupsafe import Markup, escape
        text = escape(value or "")
        return Markup("<br>".join(str(text).split("\n")))
