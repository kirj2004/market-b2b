"""Слой данных кабинета: SQLite-схема и CRUD.

Одна база — `data/market.db`. Доступ только через функции этого модуля:
raw SQL в шаблонах и views запрещён, иначе правила доступа разъезжаются.
"""

from __future__ import annotations

import json
import sqlite3
import secrets
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from config import Config

STATUSES: tuple[str, ...] = ("new", "brief", "in_progress", "delivered", "paid")

STATUS_LABELS: dict[str, str] = {
    "new": "Новая",
    "brief": "Бриф принят",
    "in_progress": "В работе",
    "delivered": "Готово",
    "paid": "Оплачено",
}

# Куда разрешено переходить из каждого статуса. Админ может откатить заявку
# на шаг назад (клиент попросил доработку), но не перепрыгнуть «в работу»
# мимо брифа.
STATUS_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "new": ("brief", "in_progress"),
    "brief": ("in_progress",),
    "in_progress": ("delivered",),
    "delivered": ("in_progress", "paid"),
    "paid": (),
}

FILE_CATEGORIES: dict[str, str] = {
    "brief": "Файл брифа",
    "data": "Данные",
    "result": "Результат",
    "other": "Прочее",
}

NOTIFICATION_TYPES: dict[str, str] = {
    "order_created": "Заявка создана",
    "status_changed": "Статус заявки изменён",
    "file_added": "Файл добавлен",
    "message": "Сообщение",
}


def utcnow() -> str:
    """Текущее время UTC в ISO-формате с 'Z' — формат, который понимает SQLite."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def connect() -> sqlite3.Connection:
    """Соединение с базой: строки как dict, внешние ключи включены."""
    Path(Config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(Config.DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Соединение с коммитом при успехе и откатом при исключении."""
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


# ──────────────────────────────── схема ────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    name          TEXT NOT NULL,
    phone         TEXT,
    company       TEXT,
    role          TEXT NOT NULL DEFAULT 'client' CHECK (role IN ('client','admin')),
    api_token_wb  TEXT,               -- API-ключ WB, шифрован Fernet
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);

-- Токены для машинного доступа (мастер заказов). Хранится только хеш:
-- сам токен клиент видит один раз при выдаче.
CREATE TABLE IF NOT EXISTS api_tokens (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash TEXT NOT NULL UNIQUE,
    prefix     TEXT NOT NULL,
    label      TEXT,
    created_at TEXT NOT NULL,
    last_used_at TEXT,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_tokens_hash ON api_tokens(token_hash);

CREATE TABLE IF NOT EXISTS orders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    service_id TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'new'
               CHECK (status IN ('new','brief','in_progress','delivered','paid')),
    source     TEXT NOT NULL DEFAULT 'web',
    input_data TEXT NOT NULL DEFAULT '{}',   -- JSON: ответы брифа
    price      TEXT NOT NULL DEFAULT '',      -- строка как в каталоге
    urgent     INTEGER NOT NULL DEFAULT 0,
    comment    TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_user ON orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);

CREATE TABLE IF NOT EXISTS files (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id   INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    category   TEXT NOT NULL DEFAULT 'other'
               CHECK (category IN ('brief','data','result','other')),
    filename   TEXT NOT NULL,       -- имя, показываемое клиенту
    path       TEXT NOT NULL,       -- путь относительно UPLOAD_DIR
    size       INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_order ON files(order_id);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    order_id   INTEGER REFERENCES orders(id) ON DELETE CASCADE,
    type       TEXT NOT NULL,
    message    TEXT NOT NULL,
    read_at    TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, created_at DESC);
"""


def init_db() -> None:
    """Создать таблицы, если их ещё нет. Идемпотентно."""
    with get_conn() as conn:
        conn.executescript(SCHEMA)


# ──────────────────────────────── users ────────────────────────────────

def create_user(email: str, password_hash: str, name: str, role: str = "client",
                phone: str | None = None, company: str | None = None) -> int:
    now = utcnow()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, name, phone, company, role,"
            " created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (email.strip(), password_hash, name.strip(), phone, company, role, now, now),
        )
        return int(cur.lastrowid)


def get_user(user_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        return row_to_dict(conn.execute(
            "SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def get_user_by_email(email: str) -> dict[str, Any] | None:
    with get_conn() as conn:
        return row_to_dict(conn.execute(
            "SELECT * FROM users WHERE email = ? COLLATE NOCASE",
            (email.strip(),)).fetchone())


def email_exists(email: str) -> bool:
    return get_user_by_email(email) is not None


def update_user(user_id: int, **fields: Any) -> None:
    """Обновить произвольный набор колонок у пользователя."""
    allowed = {"name", "phone", "company", "password_hash", "api_token_wb"}
    data = {k: v for k, v in fields.items() if k in allowed}
    if not data:
        return
    data["updated_at"] = utcnow()
    assignments = ", ".join(f"{k} = ?" for k in data)
    with get_conn() as conn:
        conn.execute(f"UPDATE users SET {assignments} WHERE id = ?",
                     (*data.values(), user_id))


def list_users(search: str = "") -> list[dict[str, Any]]:
    sql = ("SELECT u.*, "
           "(SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id) AS orders_count, "
           "(SELECT COUNT(*) FROM orders o WHERE o.user_id = u.id AND o.status NOT IN"
           " ('delivered','paid')) AS orders_active "
           "FROM users u WHERE u.role = 'client'")
    params: tuple[Any, ...] = ()
    if search:
        sql += " AND (u.email LIKE ? OR u.name LIKE ? OR u.company LIKE ?)"
        like = f"%{search}%"
        params = (like, like, like)
    sql += " ORDER BY u.created_at DESC"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def get_admin() -> dict[str, Any] | None:
    with get_conn() as conn:
        return row_to_dict(conn.execute(
            "SELECT * FROM users WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone())


# ─────────────────────────── api-токены клиента ───────────────────────────

def hash_token(token: str) -> str:
    """sha256 токена — токен 32 байта энтропии, медленный хеш не нужен."""
    import hashlib
    return hashlib.sha256(token.encode()).hexdigest()


def issue_api_token(user_id: int, label: str = "") -> tuple[str, dict[str, Any]]:
    """Выдать новый токен. Возвращает (сам токен, запись) — токен больше
    нигде не хранится и клиент его увидит один раз."""
    token = "mk_" + secrets.token_urlsafe(32)
    prefix = token[:11]
    now = utcnow()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO api_tokens (user_id, token_hash, prefix, label, created_at)"
            " VALUES (?,?,?,?,?)",
            (user_id, hash_token(token), prefix, label or None, now),
        )
        row = dict(conn.execute(
            "SELECT * FROM api_tokens WHERE id = ?", (cur.lastrowid,)).fetchone())
    return token, row


def get_active_token(token: str) -> dict[str, Any] | None:
    """Найти действующий токен и обновить last_used_at."""
    digest = hash_token(token)
    now = utcnow()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM api_tokens WHERE token_hash = ? AND revoked_at IS NULL",
            (digest,)).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?",
                     (now, row["id"]))
        return dict(row)


def get_token(token_id: int) -> dict[str, Any] | None:
    """Один токен по id — нужно админке перед отзывом (id → user_id)."""
    with get_conn() as conn:
        return row_to_dict(conn.execute(
            "SELECT * FROM api_tokens WHERE id = ?", (token_id,)).fetchone())


def list_tokens(user_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT id, prefix, label, created_at, last_used_at, revoked_at"
            " FROM api_tokens WHERE user_id = ? ORDER BY id DESC", (user_id,)).fetchall()]


def revoke_token(user_id: int, token_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE api_tokens SET revoked_at = ? WHERE id = ? AND user_id = ?"
            " AND revoked_at IS NULL",
            (utcnow(), token_id, user_id),
        )
        return cur.rowcount > 0


# ──────────────────────────────── orders ────────────────────────────────

def create_order(user_id: int, service_id: str, *, title: str = "", source: str = "web",
                 input_data: dict[str, Any] | None = None, price: str = "",
                 urgent: bool = False, comment: str = "") -> int:
    now = utcnow()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO orders (user_id, service_id, title, status, source, input_data,"
            " price, urgent, comment, created_at, updated_at)"
            " VALUES (?,?,?,'new',?,?,?,?,?,?,?)",
            (user_id, service_id, title, source,
             json.dumps(input_data or {}, ensure_ascii=False),
             price, 1 if urgent else 0, comment, now, now),
        )
        return int(cur.lastrowid)


def get_order(order_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        return row_to_dict(conn.execute(
            "SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone())


def get_order_for_user(order_id: int, user_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        return row_to_dict(conn.execute(
            "SELECT * FROM orders WHERE id = ? AND user_id = ?",
            (order_id, user_id)).fetchone())


def list_orders_for_user(user_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT o.*, (SELECT COUNT(*) FROM files f WHERE f.order_id = o.id)"
            " AS files_count FROM orders o WHERE o.user_id = ?"
            " ORDER BY o.created_at DESC", (user_id,)).fetchall()]


def list_orders(status: str = "", service_id: str = "", search: str = "",
                user_id: int | None = None) -> list[dict[str, Any]]:
    """Список заявок для админки с фильтрами."""
    sql = ["SELECT o.*, u.email AS user_email, u.name AS user_name, u.company AS user_company,"
           " (SELECT COUNT(*) FROM files f WHERE f.order_id = o.id) AS files_count"
           " FROM orders o JOIN users u ON u.id = o.user_id WHERE 1=1"]
    params: list[Any] = []
    if status:
        sql.append("AND o.status = ?")
        params.append(status)
    if service_id:
        sql.append("AND o.service_id = ?")
        params.append(service_id)
    if user_id is not None:
        sql.append("AND o.user_id = ?")
        params.append(user_id)
    if search:
        sql.append("AND (o.title LIKE ? OR u.email LIKE ? OR u.name LIKE ?"
                   " OR u.company LIKE ? OR CAST(o.id AS TEXT) = ?)")
        like = f"%{search}%"
        params.extend([like, like, like, like, search])
    sql.append("ORDER BY o.created_at DESC")
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(" ".join(sql), params).fetchall()]


def update_order(order_id: int, **fields: Any) -> None:
    allowed = {"service_id", "title", "status", "source", "input_data", "price",
               "urgent", "comment"}
    data = {k: v for k, v in fields.items() if k in allowed}
    if not data:
        return
    if "input_data" in data and not isinstance(data["input_data"], str):
        data["input_data"] = json.dumps(data["input_data"] or {}, ensure_ascii=False)
    data["updated_at"] = utcnow()
    assignments = ", ".join(f"{k} = ?" for k in data)
    with get_conn() as conn:
        conn.execute(f"UPDATE orders SET {assignments} WHERE id = ?",
                     (*data.values(), order_id))


def order_json(order: dict[str, Any]) -> dict[str, Any]:
    """Разобрать input_data из JSON-строки в словарь."""
    raw = order.get("input_data") or "{}"
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def status_counts(user_id: int | None = None) -> dict[str, int]:
    """Счётчики заявок по статусам — для KPI на дашбордах."""
    sql = "SELECT status, COUNT(*) AS c FROM orders"
    params: tuple[Any, ...] = ()
    if user_id is not None:
        sql += " WHERE user_id = ?"
        params = (user_id,)
    sql += " GROUP BY status"
    counts = {s: 0 for s in STATUSES}
    with get_conn() as conn:
        for row in conn.execute(sql, params).fetchall():
            counts[row["status"]] = row["c"]
    return counts


# ──────────────────────────────── files ────────────────────────────────

def add_file(order_id: int, category: str, filename: str, path: str,
             size: int = 0) -> int:
    if category not in FILE_CATEGORIES:
        category = "other"
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO files (order_id, category, filename, path, size, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (order_id, category, filename, path, size, utcnow()),
        )
        return int(cur.lastrowid)


def list_files(order_id: int) -> list[dict[str, Any]]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM files WHERE order_id = ? ORDER BY id", (order_id,)).fetchall()]


def list_result_files(user_id: int) -> list[dict[str, Any]]:
    """Файлы-результаты клиента со всех заявок — раздел «Результаты»."""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT f.*, o.service_id, o.title, o.status FROM files f"
            " JOIN orders o ON o.id = f.order_id"
            " WHERE o.user_id = ? AND f.category = 'result'"
            " ORDER BY f.created_at DESC", (user_id,)).fetchall()]


def get_file(file_id: int) -> dict[str, Any] | None:
    with get_conn() as conn:
        return row_to_dict(conn.execute(
            "SELECT * FROM files WHERE id = ?", (file_id,)).fetchone())


def delete_file(file_id: int) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM files WHERE id = ?", (file_id,))


def count_files(order_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM files WHERE order_id = ?",
                           (order_id,)).fetchone()
        return int(row["c"]) if row else 0


# ────────────────────────────── notifications ──────────────────────────────

def add_notification(user_id: int, message: str, *, order_id: int | None = None,
                     type: str = "message") -> int:
    if type not in NOTIFICATION_TYPES:
        type = "message"
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO notifications (user_id, order_id, type, message, created_at)"
            " VALUES (?,?,?,?,?)",
            (user_id, order_id, type, message, utcnow()),
        )
        return int(cur.lastrowid)


def list_notifications(user_id: int, limit: int = 50, unread_only: bool = False
                       ) -> list[dict[str, Any]]:
    sql = "SELECT n.*, o.service_id, o.title AS order_title FROM notifications n"
    sql += " LEFT JOIN orders o ON o.id = n.order_id WHERE n.user_id = ?"
    if unread_only:
        sql += " AND n.read_at IS NULL"
    sql += " ORDER BY n.created_at DESC, n.id DESC LIMIT ?"
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, (user_id, limit)).fetchall()]


def unread_count(user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM notifications WHERE user_id = ? AND read_at IS NULL",
            (user_id,)).fetchone()
        return int(row["c"]) if row else 0


def mark_notifications_read(user_id: int, notification_ids: list[int] | None = None
                            ) -> int:
    now = utcnow()
    with get_conn() as conn:
        if notification_ids:
            placeholders = ",".join("?" * len(notification_ids))
            cur = conn.execute(
                f"UPDATE notifications SET read_at = ? WHERE user_id = ?"
                f" AND read_at IS NULL AND id IN ({placeholders})",
                (now, user_id, *notification_ids),
            )
        else:
            cur = conn.execute(
                "UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL",
                (now, user_id),
            )
        return cur.rowcount


# ─────────────────────────── безопасные имена ───────────────────────────

def safe_filename(name: str) -> str:
    """Имя файла, безопасное для диска и для заголовка Content-Disposition.

    Нормализуем Unicode (NFKC), режем путь и управляющие символы, оставляем
    расширение. Пустое имя после чистки заменяем на 'file'."""
    base = unicodedata.normalize("NFKC", name or "").replace("\\", "/").split("/")[-1]
    cleaned = "".join(ch for ch in base if ch.isprintable() and ch not in '<>:"|?*')
    cleaned = cleaned.strip(". ")
    if not cleaned:
        return "file"
    return cleaned[:120]
