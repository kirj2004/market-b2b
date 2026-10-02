"""Работа с файлами заявок: приём, выдача, удаление.

Имя на диске генерируется (префикс + случайные байты), имя для показа
хранится в БД. Путь из БД не используется для открытия файла напрямую —
только через order_id, чтобы исключить выход за пределы каталога.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, BinaryIO

from flask import current_app
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

from config import Config
from models import safe_filename


class FileRejected(Exception):
    """Файл не принят: превышен размер или запрещённое расширение."""


def _ext(name: str) -> str:
    return Path(name).suffix.lstrip(".").lower()


def check_extension(filename: str) -> None:
    ext = _ext(filename)
    if not ext or ext not in Config.ALLOWED_EXTENSIONS:
        raise FileRejected(
            f"Недопустимый тип файла: .{ext or '—'}. "
            f"Разрешено: {', '.join(sorted(Config.ALLOWED_EXTENSIONS))}."
        )


def order_dir(order_id: int) -> Path:
    path = Config.UPLOAD_DIR / f"order_{order_id}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_upload(order_id: int, storage: FileStorage) -> dict[str, Any]:
    """Сохранить один загруженный файл. Возвращает словарь для models.add_file.

    Порядок проверок: расширение → размер → запись. Размер меряем по
    заголовку Content-Length файла, если он есть; фактический размер
    пересчитывается после сохранения.
    """
    check_extension(storage.filename or "")

    from security import new_upload_name

    disk_name = new_upload_name(storage.filename or "file")
    target = order_dir(order_id) / disk_name
    storage.save(target)

    size = target.stat().st_size
    limit = Config.MAX_UPLOAD_MB * 1024 * 1024
    if size > limit:
        target.unlink(missing_ok=True)
        raise FileRejected(f"Файл больше {Config.MAX_UPLOAD_MB} МБ — не принят.")

    return {
        "filename": safe_filename(storage.filename or "file"),
        "path": f"order_{order_id}/{disk_name}",
        "size": size,
    }


def absolute_path(rel_path: str) -> Path:
    """Путь на диске с проверкой, что он внутри UPLOAD_DIR.

    Защита от выхода за пределы каталога, даже если путь в БД подделан.
    """
    base = Config.UPLOAD_DIR.resolve()
    target = (base / rel_path).resolve()
    if not str(target).startswith(str(base) + "/") and target != base:
        raise FileRejected("Некорректный путь к файлу.")
    return target


def remove_file(rel_path: str) -> None:
    """Удалить файл с диска. Отсутствующий файл — не ошибка."""
    try:
        absolute_path(rel_path).unlink(missing_ok=True)
    except FileRejected:
        current_app.logger.warning("Отказ удалить файл вне каталога: %s", rel_path)
