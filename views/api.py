"""HTTP API кабинета для мастера заказов (Wizard).

Авторизация: заголовок `X-API-Token: <токен>`. Токен выдаётся клиенту в
кабинете, хранится в БД только в виде sha256 — сам токен не сохраняется.

CSRF здесь выключен намеренно: эндпоинты не используют cookie-сессию,
а токен передаётся в заголовке, недоступном для кросс-сайтовой формы.
"""

from __future__ import annotations

import json
import secrets
from functools import wraps
from typing import Any, Callable

from flask import Blueprint, current_app, g, jsonify, request, send_file

from config import Config
from models import (
    FILE_CATEGORIES, STATUS_LABELS, STATUS_TRANSITIONS, add_file, add_notification,
    create_order, get_order, list_files, list_notifications, list_orders, list_orders_for_user,
    list_tokens, order_json, revoke_token, update_order, update_user,
)
from security import hash_password
from services_catalog import all_services, get_service
from storage import FileRejected, absolute_path, save_upload

bp = Blueprint("api", __name__, url_prefix="/api")


# ─────────────────────────────── авторизация ────────────────────────────

def api_error(message: str, status: int = 400, **extra: Any):
    payload = {"ok": False, "error": message}
    payload.update(extra)
    response = jsonify(payload)
    response.status_code = status
    return response


def api_ok(payload: dict | None = None, status: int = 200):
    body = {"ok": True}
    if payload:
        body.update(payload)
    response = jsonify(body)
    response.status_code = status
    return response


def token_required(view: Callable) -> Callable:
    """Проверка API-токена. Роль админа даёт доступ ко всем заявкам."""

    @wraps(view)
    def wrapped(*args: Any, **kwargs: Any):
        header = request.headers.get(Config.API_TOKEN_HEADER, "")
        token = (header or "").strip()
        if not token:
            auth = request.headers.get("Authorization", "")
            if auth.lower().startswith("bearer "):
                token = auth[7:].strip()
        if not token:
            return api_error(
                f"Нет токена. Передайте его в заголовке {Config.API_TOKEN_HEADER}.",
                401)

        from models import get_user_by_token

        record = get_user_by_token(token)
        if record is None:
            return api_error("Токен не найден или отозван.", 401)
        if record["revoked_at"]:
            return api_error("Токен отозван.", 401)
        if _expired(record["expires_at"]):
            return api_error("Срок действия токена истёк.", 401)

        g.api_user = record["user"]
        g.api_token = record
        return view(*args, **kwargs)

    return wrapped


def _expired(expires_at: str | None) -> bool:
    if not expires_at:
        return False
    from datetime import datetime

    try:
        return datetime.strptime(expires_at, "%Y-%m-%d %H:%M:%S") < datetime.utcnow()
    except ValueError:
        return False


def is_admin() -> bool:
    return bool(getattr(g, "api_user", None) and g.api_user["role"] == "admin")


def body() -> dict:
    """Тело запроса как словарь: JSON или form-data."""
    if request.is_json:
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}
    return {k: v for k, v in request.form.items()}


# ────────────────────────────────── справочник ────────────────────────────

@bp.route("/health")
def health():
    return api_ok({"service": "market", "version": Config.VERSION})


@bp.route("/services")
@token_required
def services():
    return api_ok({"services": [s.to_dict() for s in all_services()]})


@bp.route("/me")
@token_required
def me():
    user = g.api_user
    return api_ok({
        "user": {
            "id": user["id"], "email": user["email"], "name": user["name"],
            "role": user["role"], "company": user["company"],
        },
        "orders": len(list_orders_for_user(user["id"])),
    })


# ──────────────────────────────────── заявки ─────────────────────────────

@bp.route("/orders", methods=["GET", "POST"])
@token_required
def orders():
    if request.method == "GET":
        return _list_orders()
    return _create_order()


def _list_orders():
    status = request.args.get("status", "")
    service_id = request.args.get("service", "")
    if is_admin():
        items = list_orders(status=status, service_id=service_id)
    else:
        items = list_orders_for_user(g.api_user["id"], status=status, service_id=service_id)
    return api_ok({"orders": [_order_payload(o) for o in items], "total": len(items)})


def _order_payload(order: dict) -> dict:
    payload = {
        "id": order["id"],
        "user_id": order["user_id"],
        "service_id": order["service_id"],
        "title": order["title"],
        "status": order["status"],
        "status_label": STATUS_LABELS.get(order["status"], order["status"]),
        "price": order["price"],
        "urgent": bool(order["urgent"]),
        "source": order["source"],
        "input_data": order_json(order),
        "files": [
            {"id": f["id"], "filename": f["filename"], "category": f["category"],
             "size": f["size"], "url": f"/api/orders/{order['id']}/files/{f['id']}"}
            for f in list_files(order["id"])
        ],
        "created_at": order["created_at"],
        "updated_at": order["updated_at"],
        "next_statuses": list(STATUS_TRANSITIONS.get(order["status"], ())),
    }
    return payload


def _create_order():
    data = body()
    service = get_service(str(data.get("service_id", "")))
    if service is None:
        return api_error(
            "Неизвестная услуга. Список допустимых значений: /api/services",
            400, services=[s.id for s in all_services()])

    # Кто заказчик: сам клиент по умолчанию, админ может указать user_id.
    user_id = g.api_user["id"]
    if data.get("user_id"):
        if not is_admin():
            return api_error("Указывать user_id может только администратор.", 403)
        try:
            user_id = int(data["user_id"])
        except (TypeError, ValueError):
            return api_error("user_id должен быть числом.", 400)

    brief = data.get("brief") or data.get("input_data")
    if isinstance(brief, dict):
        input_data = brief
    else:
        input_data = {
            "brief": (brief or "").strip(),
            "shop": (data.get("shop") or "").strip(),
            "service_name": service.name,
            "service_level": service.level_label,
        }
    if not input_data.get("brief"):
        return api_error("Поле brief обязательно: что нужно сделать.", 400)

    order_id = create_order(
        user_id=user_id,
        service_id=service.id,
        title=(data.get("title") or "").strip() or service.name,
        source=(data.get("source") or "api").strip()[:40],
        input_data=input_data,
        price=(data.get("price") or service.price_label),
        urgent=bool(data.get("urgent")),
    )

    # API-ключ WB можно передать сразу — сохраняется в зашифрованном виде.
    wb_key = (data.get("api_key_wb") or "").strip()
    if wb_key:
        from security import encrypt_secret
        update_user(user_id, api_token_wb=encrypt_secret(wb_key))

    add_notification(
        user_id, f"Заявка №{order_id} на услугу «{service.name}» создана.",
        order_id=order_id, type="order_created")

    return api_ok({"order_id": order_id,
                   "order": _order_payload(get_order(order_id))}, 201)


@bp.route("/orders/<int:order_id>", methods=["GET", "PATCH"])
@token_required
def order_detail(order_id: int):
    order = _owned_order(order_id)
    if isinstance(order, tuple):
        return order
    if request.method == "GET":
        return api_ok({"order": _order_payload(order)})
    return _patch_order(order)


def _owned_order(order_id: int):
    """Заявка по токену. Возвращает заявку либо готовый ответ с ошибкой."""
    order = get_order(order_id)
    if order is None:
        return api_error("Заявка не найдена.", 404)
    if not is_admin() and order["user_id"] != g.api_user["id"]:
        # Не отличаем «нет доступа» от «нет заявки» — иначе API перечисляет
        # чужие заказы по перебору id.
        return api_error("Заявка не найдена.", 404)
    return order


def _patch_order(order: dict):
    data = body()
    fields: dict[str, Any] = {}

    if "status" in data:
        new_status = str(data["status"]).strip()
        if new_status not in STATUS_LABELS:
            return api_error(
                f"Неизвестный статус. Допустимо: {', '.join(STATUS_LABELS)}", 400)
        allowed = STATUS_TRANSITIONS.get(order["status"], ())
        if new_status != order["status"] and new_status not in allowed:
            return api_error(
                f"Переход «{STATUS_LABELS[order['status']]}» → "
                f"«{STATUS_LABELS[new_status]}» не разрешён.", 409,
                allowed=list(allowed))
        fields["status"] = new_status

    for key in ("title", "price", "source"):
        if key in data:
            fields[key] = str(data[key]).strip()[:200]
    if "urgent" in data:
        fields["urgent"] = 1 if data["urgent"] in (True, 1, "1", "true", "yes") else 0
    if "input_data" in data:
        payload = data["input_data"]
        if isinstance(payload, dict):
            fields["input_data"] = json.dumps(payload, ensure_ascii=False)
        else:
            fields["input_data"] = str(payload)

    if not fields:
        return api_error("Нечего менять: пустой PATCH.", 400)

    update_order(order["id"], **fields)

    changed_status = ("status" in fields and fields["status"] != order["status"])
    if changed_status:
        message = (data.get("comment") or "").strip()
        text = (f"Заявка №{order['id']}: статус «{STATUS_LABELS[order['status']]}» → "
                f"«{STATUS_LABELS[fields['status']]}».")
        if message:
            text += f" {message}"
        add_notification(order["user_id"], text, order_id=order["id"],
                         type="status_changed")
    else:
        add_notification(order["user_id"], f"Заявка №{order['id']} обновлена.",
                         order_id=order["id"], type="message")

    return api_ok({"order": _order_payload(get_order(order["id"]))})


@bp.route("/orders/<int:order_id>/files", methods=["GET", "POST"])
@token_required
def order_files(order_id: int):
    order = _owned_order(order_id)
    if isinstance(order, tuple):
        return order

    if request.method == "GET":
        items = list_files(order_id)
        return api_ok({"files": [
            {"id": f["id"], "filename": f["filename"], "category": f["category"],
             "size": f["size"], "url": f"/api/orders/{order_id}/files/{f['id']}"}
            for f in items]})

    category = (request.form.get("category") or "other").strip()
    if category not in FILE_CATEGORIES:
        return api_error(
            f"Неизвестная категория. Допустимо: {', '.join(sorted(FILE_CATEGORIES))}",
            400)
    uploads = [f for f in request.files.getlist("files") if f and f.filename]
    if not uploads:
        return api_error("Файл не передан (поле files).", 400)

    saved, errors = [], []
    for upload in uploads:
        try:
            info = save_upload(order_id, upload)
            file_id = add_file(order_id, category, info["filename"], info["path"],
                               info["size"])
            saved.append({"id": file_id, "filename": info["filename"],
                          "size": info["size"]})
        except FileRejected as exc:
            errors.append(str(exc))

    if saved:
        add_notification(
            order["user_id"],
            f"К заявке №{order_id} добавлено файлов: {len(saved)} ({category}).",
            order_id=order_id, type="file_added")
    return api_ok({"files": saved, "errors": errors}, 201 if saved else 400)


@bp.route("/orders/<int:order_id>/files/<int:file_id>", methods=["GET", "DELETE"])
@token_required
def file_item(order_id: int, file_id: int):
    order = _owned_order(order_id)
    if isinstance(order, tuple):
        return order
    row = next((f for f in list_files(order_id) if f["id"] == file_id), None)
    if row is None:
        return api_error("Файл не найден.", 404)

    if request.method == "DELETE":
        if row["category"] == "result" and not is_admin():
            return api_error("Результат работы может удалить только администратор.", 403)
        from models import delete_file as db_delete
        from storage import remove_file
        remove_file(row["path"])
        db_delete(file_id)
        return api_ok({"deleted": file_id})

    try:
        path = absolute_path(row["path"])
    except FileRejected:
        return api_error("Файл не найден.", 404)
    if not path.exists():
        return api_error("Файл отсутствует на диске.", 404)
    return send_file(path, as_attachment=True, download_name=row["filename"])


# ───────────────────────────────── уведомления ───────────────────────────

@bp.route("/notifications", methods=["GET"])
@token_required
def notifications():
    user_id = g.api_user["id"]
    if request.args.get("all") == "1" and is_admin():
        # Админ видит ленту по всем клиентам через /api/orders.
        return api_ok({"notifications": [], "hint": "Используйте /api/orders."})
    items = list_notifications(user_id, limit=int(request.args.get("limit", 50) or 50))
    return api_ok({"notifications": [
        {"id": n["id"], "order_id": n["order_id"], "type": n["type"],
         "message": n["message"], "read_at": n["read_at"],
         "created_at": n["created_at"]}
        for n in items]})


@bp.route("/notifications/read", methods=["POST"])
@token_required
def mark_read():
    data = body()
    ids = data.get("ids")
    from models import mark_notifications_read
    mark_notifications_read(
        g.api_user["id"],
        [int(i) for i in ids] if isinstance(ids, list) else None)
    return api_ok()


@bp.errorhandler(413)
def too_large(_exc):
    return api_error(
        f"Файл больше допустимых {Config.MAX_UPLOAD_MB} МБ.", 413)