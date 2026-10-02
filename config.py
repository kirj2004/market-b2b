"""Конфигурация кабинета клиента.

Все параметры — из окружения с безопасными значениями по умолчанию.
Секреты (SECRET_KEY, FERNET_KEY) никогда не пишутся в git: если переменная
не задана, ключ создаётся один раз в каталоге data/ с правами 0600.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OPS_DIR = BASE_DIR.parent / "operations"


def _ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def _secret_file(name: str, generator) -> bytes:
    """Вернуть ключ из окружения, иначе создать постоянный в data/."""
    env_value = os.environ.get(name)
    if env_value:
        return env_value.strip().encode()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / name.lower()
    if not path.exists():
        path.write_bytes(generator())
        path.chmod(0o600)
    return path.read_bytes().strip()


class Config:
    """Базовая конфигурация."""

    VERSION = "1.0.0"
    # Генератор обязан отдать bytes — write_bytes не принимает str.
    SECRET_KEY = _secret_file("MARKET_SECRET_KEY", lambda: secrets.token_bytes(32))

    DATA_DIR = DATA_DIR
    DB_PATH = DATA_DIR / "market.db"
    UPLOAD_DIR = UPLOAD_DIR

    MAX_CONTENT_LENGTH = 64 * 1024 * 1024  # 64 МБ на запрос
    MAX_UPLOAD_MB = 32
    ALLOWED_EXTENSIONS = {
        "csv", "xlsx", "xls", "json", "txt", "pdf", "png", "jpg", "jpeg",
        "zip", "docx", "doc", "html", "htm",
    }

    UPLOADS_PER_ORDER = 10

    API_TOKEN_HEADER = "X-API-Token"
    SESSION_COOKIE = "market_session"
    # Secure имеет смысл только за HTTPS: за plain HTTP по 127.0.0.1 такой
    # флаг просто не даст браузеру прислать cookie обратно.
    SESSION_COOKIE_SECURE = os.environ.get("MARKET_HTTPS", "0") == "1"
    COOKIE_SECURE = SESSION_COOKIE_SECURE
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 30  # 30 дней
    SESSION_LIFETIME = PERMANENT_SESSION_LIFETIME
    # CSRF-токен живёт столько же, сколько сессия: ограничение в 1 час
    # выкидывало бы из формы на длинной загрузке файлов.
    CSRF_TIME_LIMIT = None

    DEBUG = os.environ.get("MARKET_DEBUG", "0") == "1"

    # Администратор, создаваемый командой `./run.sh init`.
    ADMIN_EMAIL = os.environ.get("MARKET_ADMIN_EMAIL", "").strip()
    ADMIN_PASSWORD = os.environ.get("MARKET_ADMIN_PASSWORD", "")

    PORT = int(os.environ.get("MARKET_PORT", "5000"))
    HOST = os.environ.get("MARKET_HOST", "127.0.0.1")

    # Название в шапке кабинета. В клиентских артефактах не должно быть
    # служебных названий проектов, поэтому значение вынесено в окружение.
    BRAND_NAME = os.environ.get("MARKET_BRAND_NAME", "ParaMagic")
    BRAND_SUB = "Кабинет заказов"

    # Заголовки безопасности на уровне приложения (nginx дублирует часть).
    SECURITY_HEADERS = {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "same-origin",
    }


def init_dirs() -> None:
    """Создать каталоги данных и загрузок. Вызывается при старте приложения."""
    _ensure_dir(DATA_DIR)
    _ensure_dir(UPLOAD_DIR)
