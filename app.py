"""Точка входа кабинета клиента MARKET.

Запуск:
    gunicorn --bind 127.0.0.1:5000 'app:create_app()'
    python3 app.py            # только для отладки, не в проде

За nginx: статика, TLS и лимиты запроса — снаружи; приложение отдаёт
только HTML и JSON.
"""

from __future__ import annotations

import logging
import secrets
import sys
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from flask_wtf.csrf import CSRFProtect

from config import Config
from views import BLUEPRINTS, api
from views.helpers import register_jinja

log = logging.getLogger("market")


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.config.update(
        SECRET_KEY=Config.SECRET_KEY,
        MAX_CONTENT_LENGTH=Config.MAX_UPLOAD_MB * 1024 * 1024,
        SESSION_COOKIE_NAME=Config.SESSION_COOKIE,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=Config.COOKIE_SECURE,
        PERMANENT_SESSION_LIFETIME=Config.SESSION_LIFETIME,
        WTF_CSRF_TIME_LIMIT=Config.CSRF_TIME_LIMIT,
        JSON_AS_ASCII=False,
        MAX_CONTENT_LENGTH_MB=Config.MAX_UPLOAD_MB,
    )
    app.json.ensure_ascii = False

    # Заголовки безопасности. Nginx дублирует HSTS и HSTS-политику,
    # но приложение обязано быть защищено и при прямом обращении к порту.
    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy",
                                    "geolocation=(), microphone=(), camera=()")
        return response

    for bp in BLUEPRINTS:
        app.register_blueprint(bp)

    # CSRF для всех POST, а не только для форм WTForms: в админке формы
    # собраны в разметке (по одной на строку таблицы), и без глобальной
    # защиты они шли бы без проверки.
    csrf = CSRFProtect(app)
    # API работает по токену в заголовке, а не по cookie: кросс-сайтовая
    # форма не может подставить заголовок, CSRF-токен там не нужен.
    csrf.exempt(api.bp)

    register_jinja(app)
    _register_errors(app)
    _register_cli(app)

    Config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    from services_catalog import all_services

    log.info("Кабинет готов. Услуг в каталоге: %d", len(all_services()))
    return app


def _register_errors(app: Flask) -> None:
    @app.errorhandler(400)
    def bad_request(exc):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Некорректный запрос."}), 400
        return render_template("error.html", code=400,
                               title="Некорректный запрос",
                               text="Проверьте адрес и отправленную форму."), 400

    @app.errorhandler(403)
    def forbidden(_exc):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Доступ запрещён."}), 403
        return render_template("error.html", code=403,
                               title="Доступ запрещён",
                               text="У этой страницы нет публичного доступа."), 403

    @app.errorhandler(404)
    def not_found(_exc):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Не найдено."}), 404
        return render_template("error.html", code=404,
                               title="Страница не найдена",
                               text="Проверьте адрес или вернитесь в кабинет."), 404

    @app.errorhandler(413)
    def too_large(_exc):
        limit = Config.MAX_UPLOAD_MB
        if request.path.startswith("/api/"):
            return jsonify({"ok": False,
                            "error": f"Файл больше допустимых {limit} МБ."}), 413
        return render_template("error.html", code=413,
                               title="Файл слишком большой",
                               text=f"Максимальный размер одного файла — {limit} МБ."), 413

    @app.errorhandler(500)
    def server_error(exc):
        log.exception("Ошибка 500: %s", exc)
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Внутренняя ошибка сервера."}), 500
        return render_template("error.html", code=500,
                               title="Что-то пошло не так",
                               text="Мы уже знаем о проблеме. Попробуйте позже."), 500


def _register_cli(app: Flask) -> None:
    import click

    @app.cli.command("init-db")
    def init_db():
        """Создать схему и администратора по умолчанию."""
        from models import init_db, create_user
        from security import hash_password

        init_db()
        if Config.ADMIN_EMAIL:
            created = create_user(
                email=Config.ADMIN_EMAIL,
                password_hash=hash_password(Config.ADMIN_PASSWORD),
                name="Администратор", role="admin")
            click.echo(f"Администратор: {Config.ADMIN_EMAIL} ({created})")
        else:
            click.echo("Схема готова. MARKET_ADMIN_EMAIL не задан — админ не создан.")

    @app.cli.command("stats")
    def stats():
        """Сводка по заявкам."""
        from models import list_orders, status_counts

        counts = status_counts()
        click.echo(f"Заявок всего: {len(list_orders())}")
        for status, amount in counts.items():
            click.echo(f"  {status}: {amount}")

    @app.cli.command("token")
    @click.option("--user", "email_", help="email пользователя")
    def token_cmd(email_):
        """Выдать API-токен пользователю (показывается один раз)."""
        from models import get_user_by_email, issue_api_token

        if not email_:
            click.echo("Нужен --user email")
            return
        user = get_user_by_email(email_)
        if not user:
            click.echo(f"Пользователь {email_} не найден.")
            return
        value, _ = issue_api_token(user["id"], label="cli")
        click.echo(value)


app = create_app()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if not Config.DEBUG:
        log.warning(
            "Запуск через %s — это отладочный режим. В проде: gunicorn на 127.0.0.1:5000.",
            Path(sys.argv[0]).name)
    app.run(host=Config.HOST, port=Config.PORT, debug=Config.DEBUG,
            use_reloader=Config.DEBUG)