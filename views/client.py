"""Клиентский кабинет: дашборд, создание заявки, детали, файлы."""

from __future__ import annotations

import json

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request,
    send_file, url_for,
)

from config import Config
from forms import NewOrderForm, UploadFilesForm
from models import (
    STATUS_LABELS, STATUS_TRANSITIONS, add_file, add_notification, count_files,
    create_order, get_order_for_user, list_files, list_orders_for_user,
    order_json, status_counts, update_order,
)
from services_catalog import all_services, get_service
from storage import FileRejected, remove_file, save_upload
from views.helpers import current_user, flash_errors, login_required

bp = Blueprint("client", __name__)


@bp.route("/")
def index():
    """Главная: клиента — в кабинет, гостя — на вход."""
    if current_user():
        return redirect(url_for("client.dashboard"))
    return render_template("landing.html")


@bp.route("/dashboard")
@login_required
def dashboard():
    user = current_user()
    orders = list_orders_for_user(user["id"])
    counts = status_counts(user["id"])
    active = [o for o in orders if o["status"] not in ("delivered", "paid")]
    done = [o for o in orders if o["status"] in ("delivered", "paid")]
    return render_template(
        "client/dashboard.html",
        orders=orders[:10],
        total=len(orders),
        counts=counts,
        active_orders=active[:5],
        done_orders=done[:5],
    )


@bp.route("/orders/new", methods=["GET", "POST"])
@login_required
def new_order():
    user = current_user()
    services = all_services()
    if not services:
        flash("Каталог услуг недоступен — заявку создать нельзя.", "error")
        return redirect(url_for("client.dashboard"))

    form = NewOrderForm()
    form.service_id.choices = [(s.id, f"№{s.n}. {s.name} — {s.price_label}") for s in services]
    requested = request.args.get("service", "")
    if requested and get_service(requested):
        form.service_id.data = requested
    elif services:
        form.service_id.data = form.service_id.data or services[0].id

    if form.validate_on_submit():
        service = get_service(form.service_id.data)
        if service is None:
            flash("Выбрана неизвестная услуга.", "error")
            return render_template("client/new_order.html", form=form, services=services,
                                   selected=service)

        brief_data = {
            "brief": (form.brief.data or "").strip(),
            "shop": (form.shop.data or "").strip(),
            "service_name": service.name,
            "service_level": service.level_label,
        }
        order_id = create_order(
            user_id=user["id"],
            service_id=service.id,
            title=(form.title.data or "").strip() or f"{service.name}",
            source="web",
            input_data=brief_data,
            price=service.price,
            urgent=bool(form.urgent.data),
        )

        uploaded = 0
        upload = form.files.data
        if upload and upload.filename:
            try:
                if count_files(order_id) >= Config.UPLOADS_PER_ORDER:
                    raise FileRejected(
                        f"К заявке можно прикрепить не больше {Config.UPLOADS_PER_ORDER} файлов.")
                info = save_upload(order_id, upload)
                add_file(order_id, "brief", info["filename"], info["path"], info["size"])
                uploaded += 1
            except FileRejected as exc:
                flash(str(exc), "error")

        wb_key = (form.api_key_wb.data or "").strip()
        if wb_key:
            update_secret(user["id"], wb_key)

        add_notification(
            user["id"],
            f"Заявка №{order_id} на услугу «{service.name}» принята в работу.",
            order_id=order_id, type="order_created")
        flash(f"Заявка №{order_id} создана. Мы свяжемся по деталям.", "success")
        return redirect(url_for("client.order_detail", order_id=order_id))

    flash_errors(form)
    selected = get_service(form.service_id.data or "")
    return render_template("client/new_order.html", form=form, services=services,
                           selected=selected)


def update_secret(user_id: int, wb_key: str) -> None:
    """Сохранить API-ключ WB клиента, зашифровав его."""
    from models import update_user
    from security import encrypt_secret

    update_user(user_id, api_token_wb=encrypt_secret(wb_key))


@bp.route("/orders/<int:order_id>")
@login_required
def order_detail(order_id: int):
    user = current_user()
    order = get_order_for_user(order_id, user["id"])
    if order is None:
        abort(404)
    upload_form = UploadFilesForm()
    if upload_form.validate_on_submit():
        _handle_client_upload(order, upload_form)
        return redirect(url_for("client.order_detail", order_id=order_id))
    return render_template(
        "client/order_detail.html",
        order=order,
        brief=order_json(order),
        service=get_service(order["service_id"]),
        files=list_files(order_id),
        upload_form=upload_form,
        next_statuses=STATUS_TRANSITIONS.get(order["status"], ()),
    )


def _handle_client_upload(order: dict, form: UploadFilesForm) -> None:
    """Загрузка файлов клиентом в существующую заявку."""
    uploads = [f for f in request.files.getlist("files") if f and f.filename]
    if not uploads:
        flash("Файл не выбран.", "warning")
        return
    existing = count_files(order["id"])
    if existing + len(uploads) > Config.UPLOADS_PER_ORDER:
        flash(f"К заявке можно прикрепить не больше {Config.UPLOADS_PER_ORDER} файлов.",
              "error")
        return
    for upload in uploads:
        try:
            info = save_upload(order["id"], upload)
            add_file(order["id"], form.category.data, info["filename"],
                     info["path"], info["size"])
            flash(f"Файл «{info['filename']}» загружен.", "success")
        except FileRejected as exc:
            flash(str(exc), "error")


@bp.route("/orders/<int:order_id>/files/<int:file_id>/delete", methods=["POST"])
@login_required
def delete_file(order_id: int, file_id: int):
    from forms import ConfirmForm
    user = current_user()
    order = get_order_for_user(order_id, user["id"])
    if order is None:
        abort(404)
    form = ConfirmForm()
    if not form.validate_on_submit():
        abort(400)
    file_row = next((f for f in list_files(order_id) if f["id"] == file_id), None)
    if file_row is None:
        abort(404)
    remove_file(file_row["path"])
    from models import delete_file as db_delete
    db_delete(file_id)
    flash("Файл удалён.", "info")
    return redirect(url_for("client.order_detail", order_id=order_id))


@bp.route("/orders/<int:order_id>/files/<int:file_id>/download")
@login_required
def download_file(order_id: int, file_id: int):
    """Скачивание файла заявки. Доступ — только у своего заказа."""
    user = current_user()
    order = get_order_for_user(order_id, user["id"])
    if order is None:
        abort(404)
    file_row = next((f for f in list_files(order_id) if f["id"] == file_id), None)
    if file_row is None:
        abort(404)
    from storage import absolute_path
    try:
        path = absolute_path(file_row["path"])
    except FileRejected:
        abort(404)
    if not path.exists():
        flash("Файл отсутствует на диске. Сообщите в поддержку.", "error")
        abort(404)
    return send_file(path, as_attachment=True, download_name=file_row["filename"])


@bp.route("/services")
@login_required
def services_list():
    from services_catalog import services_by_level
    return render_template("client/services.html", grouped=services_by_level())
