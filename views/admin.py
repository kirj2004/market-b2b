"""Админка: заявки, клиенты, ключи."""

from __future__ import annotations

from typing import Any

from flask import (
    Blueprint, abort, flash, redirect, render_template, request, send_file,
    url_for,
)

from forms import AdminClientSearchForm, ConfirmForm, TokenIssueForm
from models import (
    FILE_CATEGORIES, STATUS_LABELS, STATUS_TRANSITIONS, add_file, add_notification,
    get_admin, get_order, get_token, get_user, issue_api_token,
    list_files, list_orders, list_orders_for_user, list_tokens, list_users, order_json,
    revoke_token, status_counts, update_order,
)
from security import decrypt_secret, mask_secret
from services_catalog import all_services, get_service
from storage import FileRejected, absolute_path, save_upload
from views.helpers import admin_required

bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/")
@admin_required
def dashboard():
    counts = status_counts()
    orders = list_orders()
    recent = orders[:8]
    revenue = 0.0
    for order in orders:
        if order["status"] == "paid":
            revenue += _price_to_number(order["price"])
    return render_template(
        "admin/dashboard.html",
        counts=counts,
        total=len(orders),
        recent=recent,
        new_count=counts.get("new", 0),
        active_count=counts.get("in_progress", 0) + counts.get("brief", 0),
        done_count=counts.get("delivered", 0),
        paid_count=counts.get("paid", 0),
        revenue=revenue,
        clients_count=len(list_users()),
    )


def _price_to_number(price: str) -> float:
    """«9 900» / «от 29 900» → число. Для сводки выручки."""
    digits = "".join(ch for ch in (price or "") if ch.isdigit() or ch == " ")
    try:
        return float(digits.replace(" ", "") or 0)
    except ValueError:
        return 0.0


@bp.route("/orders", methods=["GET", "POST"])
@admin_required
def orders():
    """Список заявок с фильтрами и действиями.

    Формы в таблице собираются в разметке (csrf_token() в шаблоне), а не
    объектами WTForms: на одну страницу приходится N одинаковых форм,
    и один объект формы на все строки означал бы один общий обработчик
    на все заявки. CSRF защищает глобальный CSRFProtect в app.py.
    """
    status = request.args.get("status", "")
    service_id = request.args.get("service", "")
    search = request.args.get("q", "").strip()

    if request.method == "POST":
        dispatch = request.form.get("dispatch", "")
        order_id = _int_or_zero(request.form.get("order_id"))
        order = get_order(order_id) if order_id else None
        if order is None:
            flash("Заявка не найдена.", "error")
        elif dispatch == "action":
            _apply_status_raw(order)
        elif dispatch == "upload":
            _attach_result(order)
        elif dispatch == "message":
            message = (request.form.get("message") or "").strip()
            if not message:
                flash("Пустое сообщение не отправлено.", "warning")
            elif len(message) > 2000:
                flash("Сообщение длиннее 2000 символов.", "error")
            else:
                add_notification(order["user_id"], message, order_id=order["id"],
                                 type="message")
                flash("Сообщение клиенту отправлено.", "success")
        else:
            flash("Неизвестное действие.", "error")
        back = request.form.get("back") or request.referrer or url_for("admin.orders")
        return redirect(back)

    items = list_orders(status=status, service_id=service_id, search=search)
    return render_template(
        "admin/orders.html",
        orders=items,
        services=all_services(),
        counts=status_counts(),
        filters={"status": status, "service": service_id, "q": search},
    )


def _int_or_zero(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _apply_status_raw(order: dict) -> None:
    """Смена статуса из списка: только статус, urgent и цена."""
    new_status = (request.form.get("status") or "").strip()
    old_status = order["status"]
    if new_status not in STATUS_LABELS:
        flash(f"Неизвестный статус: {new_status or '—'}.", "error")
        return
    if new_status != old_status and new_status not in STATUS_TRANSITIONS.get(old_status, ()):
        flash(f"Переход «{STATUS_LABELS[old_status]}» → «{STATUS_LABELS[new_status]}» "
              "не разрешён.", "error")
        return
    fields: dict[str, Any] = {"status": new_status}
    if request.form.get("urgent") is not None:
        fields["urgent"] = 1 if request.form.get("urgent") else 0
    price = (request.form.get("price") or "").strip()
    if price:
        fields["price"] = price[:40]
    update_order(order["id"], **fields)

    if new_status != old_status:
        text = (f"Заявка №{order['id']}: статус «{STATUS_LABELS[old_status]}» → "
                f"«{STATUS_LABELS[new_status]}».")
        comment = (request.form.get("comment") or "").strip()
        if comment:
            text += f" {comment}"
        add_notification(order["user_id"], text, order_id=order["id"],
                         type="status_changed")
    flash(f"Заявка №{order['id']}: {STATUS_LABELS[new_status]}.", "success")


def _attach_result(order: dict) -> None:
    """Прикрепление файла-результата админом."""
    category = (request.form.get("category") or "result").strip()
    if category not in FILE_CATEGORIES:
        flash(f"Неизвестная категория файла: {category}.", "error")
        return
    uploads = [f for f in request.files.getlist("files") if f and f.filename]
    if not uploads:
        flash("Файл не выбран.", "warning")
        return
    saved = 0
    for upload in uploads:
        try:
            info = save_upload(order["id"], upload)
            add_file(order["id"], category, info["filename"],
                     info["path"], info["size"])
            saved += 1
        except FileRejected as exc:
            flash(str(exc), "error")
    if saved:
        add_notification(
            order["user_id"],
            f"К заявке №{order['id']} добавлен файл: {FILE_CATEGORIES[category]}.",
            order_id=order["id"], type="file_added")
        flash(f"Файлов прикреплено: {saved}. Клиент увидит их в кабинете.", "success")


@bp.route("/orders/<int:order_id>", methods=["GET", "POST"])
@admin_required
def order_detail(order_id: int):
    """Карточка заявки в админке: бриф, файлы, смена статуса, результат."""
    order = get_order(order_id)
    if order is None:
        abort(404)

    if request.method == "POST":
        dispatch = request.form.get("dispatch", "")
        if dispatch == "action":
            _apply_status_raw(order)
        elif dispatch == "upload":
            _attach_result(order)
        elif dispatch == "message":
            message = (request.form.get("message") or "").strip()
            if message and len(message) <= 2000:
                add_notification(order["user_id"], message, order_id=order["id"],
                                 type="message")
                flash("Сообщение клиенту отправлено.", "success")
            else:
                flash("Сообщение пустое или длиннее 2000 символов.", "error")
        else:
            flash("Неизвестное действие.", "error")
        return redirect(url_for("admin.order_detail", order_id=order_id))

    return render_template(
        "admin/order_detail.html",
        order=order,
        brief=order_json(order),
        service=get_service(order["service_id"]),
        files=list_files(order_id),
        next_statuses=STATUS_TRANSITIONS.get(order["status"], ()),
    )


@bp.route("/orders/<int:order_id>/files/<int:file_id>/download")
@admin_required
def download_file(order_id: int, file_id: int):
    order = get_order(order_id)
    if order is None:
        abort(404)
    file_row = next((f for f in list_files(order_id) if f["id"] == file_id), None)
    if file_row is None:
        abort(404)
    try:
        path = absolute_path(file_row["path"])
    except FileRejected:
        abort(404)
    if not path.exists():
        abort(404)
    return send_file(path, as_attachment=True, download_name=file_row["filename"])


@bp.route("/clients")
@admin_required
def clients():
    form = AdminClientSearchForm()
    search = (request.args.get("q") or form.q.data or "").strip()
    items = list_users(search=search)
    return render_template("admin/clients.html", clients=items, search=search)


@bp.route("/clients/<int:user_id>")
@admin_required
def client_detail(user_id: int):
    user = get_user(user_id)
    if user is None:
        abort(404)
    wb_key = decrypt_secret(user["api_token_wb"] or "")
    return render_template(
        "admin/client_detail.html",
        client=user,
        orders=list_orders_for_user(user_id),
        tokens=list_tokens(user_id),
        wb_key_masked=mask_secret(wb_key),
        wb_key_set=bool(wb_key),
    )


@bp.route("/api-key", methods=["GET", "POST"])
@admin_required
def api_key():
    """Управление API-токенами: список по всем пользователям, отзыв, выпуск."""
    if request.method == "POST":
        if "revoke" in request.form:
            cform = ConfirmForm()
            if not cform.validate_on_submit():
                abort(400)
            token_id = _int_or_zero(request.form.get("revoke"))
            token = get_token(token_id)
            if token is None:
                flash("Токен не найден.", "warning")
            elif revoke_token(token["user_id"], token_id):
                flash("Токен отозван.", "info")
            else:
                flash("Токен не найден или уже отозван.", "warning")
            return redirect(url_for("admin.api_key"))

        if "issue" in request.form:
            form = TokenIssueForm()
            if not form.validate_on_submit():
                flash("Метка токена не прошла проверку.", "error")
                return redirect(url_for("admin.api_key"))
            admin = get_admin()
            if admin is None:
                flash("Администратор не найден.", "error")
                return redirect(url_for("admin.api_key"))
            label = (form.label.data or "").strip() or "мастер заказов"
            token, _ = issue_api_token(admin["id"], label=label)
            add_notification(admin["id"], f"Новый админский токен ({label}): {token}",
                             type="message")
            flash("Токен выпущен — он показан один раз, в уведомлениях.", "success")
            return redirect(url_for("admin.api_key"))

        flash("Неизвестное действие.", "error")
        return redirect(url_for("admin.api_key"))

    emails: dict[int, str] = {}
    tokens: list[dict] = []
    for user in list_users():
        emails[user["id"]] = user["email"]
    for user_id, email in emails.items():
        for token in list_tokens(user_id):
            token["user_email"] = email
            tokens.append(token)
    tokens.sort(key=lambda t: t["id"], reverse=True)
    return render_template("admin/api_key.html", tokens=tokens)
