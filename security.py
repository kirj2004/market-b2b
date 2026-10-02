"""Шифрование секретов клиента (API-ключ WB) и служебные проверки.

Ключ Fernet хранится в data/FERNET_KEY с правами 0600 либо приходит из
переменной окружения. Показанные наружу значения — только маска вида
«wb_…1234», сам ключ в интерфейс не попадает никогда.
"""

from __future__ import annotations

import hmac
import re
import secrets
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken
from werkzeug.security import check_password_hash, generate_password_hash

from config import Config, _secret_file


def hash_password(password: str) -> str:
    return generate_password_hash(password, method="pbkdf2:sha256:600000")


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return check_password_hash(password_hash, password)
    except (ValueError, TypeError):
        return False


def validate_password_strength(password: str) -> tuple[bool, str]:
    """Минимум 8 символов. Возвращает (ок, сообщение)."""
    if len(password) < 8:
        return False, "Пароль должен быть не короче 8 символов"
    return True, ""


@lru_cache(maxsize=1)
def _fernet() -> Fernet:
    key = _secret_file("MARKET_FERNET_KEY", Fernet.generate_key)
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    """Зашифровать значение. Пустая строка остаётся пустой."""
    if not value:
        return ""
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str) -> str:
    """Расшифровать. При повреждённых данных возвращаем пустую строку,
    а не падаем: ключ мог быть выведен из эксплуатации."""
    if not value:
        return ""
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return ""


def mask_secret(value: str, keep: int = 4) -> str:
    """Маска для показа в интерфейсе: первые 6 символов, хвост, длина."""
    if not value:
        return ""
    value = value.strip()
    if len(value) <= keep + 6:
        return "•" * len(value)
    return f"{value[:6]}…{value[-keep:]} ({len(value)} симв.)"


# ──────────────────────────── проверка входа ────────────────────────────

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]{2,}$")
PHONE_RE = re.compile(r"^[0-9+()\-\s]{6,20}$")


def valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match((email or "").strip()))


def valid_phone(phone: str) -> bool:
    phone = (phone or "").strip()
    if not phone:
        return True  # необязательное поле
    return bool(PHONE_RE.match(phone))


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def secrets_equal(a: str, b: str) -> bool:
    """Сравнение строк без утечки длины по времени."""
    return hmac.compare_digest(a.encode(), b.encode())


def new_upload_name(original: str) -> str:
    """Уникальное имя файла на диске: префикс из времени + случайные байты.

    Имя, которое видит клиент, хранится отдельно в БД — на диск попадает
    только безопасная строка."""
    from models import safe_filename
    safe = safe_filename(original)
    stem, dot, ext = safe.rpartition(".")
    if not dot:
        stem, ext = safe, ""
    token = secrets.token_hex(8)
    return f"{stem[:60]}_{token}.{ext}" if ext else f"{stem[:60]}_{token}"


def token_header_name() -> str:
    return Config.API_TOKEN_HEADER
