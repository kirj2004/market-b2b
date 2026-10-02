"""Регистрация, вход, выход, профиль клиента."""

from __future__ import annotations

import secrets

from flask import (
    Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for,
)

from forms import (
    ApiKeyForm, MessageForm, PasswordChangeForm, ProfileForm, RegisterForm, LoginForm,
    TokenIssueForm,
)
from models import (
    add_notification, create_user, email_exists, get_user_by_email, issue_api_token,
    list_files, list_notifications, list_orders_for_user, list_result_files,
    list_tokens, mark_notifications_read, revoke_token, update_user,
)
from security import (
    encrypt_secret, hash_password, mask_secret, decrypt_secret, normalize_email,
    valid_email, valid_phone, verify_password,
)
from services_catalog import all_services, get_service
from views.helpers import current_user, flash_errors, login_required

bp = Blueprint("auth", __name__)


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("client.dashboard"))

    form = RegisterForm()
    if form.validate_on_submit():
        email = normalize_email(form.email.data)
        if email_exists(email):
            flash("Такой email уже зарегистрирован.", "error")
        elif not valid_phone(form.phone.data):
            flash("Телефон выглядит некорректно.", "error")
        else:
            user_id = create_user(
                email=email,
                password_hash=hash_password(form.password.data),
                name=form.name.data,
                role="client",
                phone=(form.phone.data or "").strip() or None,
                company=(form.company.data or "").strip() or None,
            )
            token, _ = issue_api_token(user_id, label="при регистрации")
            session.clear()
            session["uid"] = user_id
            session.permanent = True
            add_notification(
                user_id,
                f"Кабинет создан. API-токен для мастера заказов: {token}",
                type="message",
            )
            flash("Кабинет создан. Токен для мастера заказов — в уведомлениях.", "success")
            return redirect(url_for("client.dashboard"))

    return render_template("auth/register.html", form=form)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("client.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        user = get_user_by_email(normalize_email(form.email.data))
        if user and verify_password(user["password_hash"], form.password.data):
            session.clear()
            session["uid"] = user["id"]
            session.permanent = bool(form.remember.data)
            target = request.args.get("next", "")
            # Редирект только внутрь приложения: открытый redirect через
            # ?next=https://злой.сайт не проходит.
            if not target.startswith("/") or target.startswith("//"):
                target = ""
            flash(f"С возвращением, {user['name']}.", "success")
            if user["role"] == "admin":
                return redirect(target or url_for("admin.dashboard"))
            return redirect(target or url_for("client.dashboard"))
        flash("Email или пароль не подходят.", "error")

    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    session.clear()
    flash("Вы вышли из кабинета.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    form = ProfileForm(obj=user)
    key_form = ApiKeyForm()
    pass_form = PasswordChangeForm()
    token_form = TokenIssueForm()
    message_form = MessageForm()

    if request.method == "POST":
        if "save_profile" in request.form:
            if form.validate_on_submit():
                if not valid_phone(form.phone.data):
                    flash("Телефон выглядит некорректно.", "error")
                else:
                    update_user(
                        user["id"],
                        name=form.name.data,
                        phone=(form.phone.data or "").strip() or None,
                        company=(form.company.data or "").strip() or None,
                    )
                    flash("Профиль сохранён.", "success")
                    return redirect(url_for("auth.profile"))
            flash_errors(form)
        elif "save_key" in request.form:
            if key_form.validate_on_submit():
                raw = (key_form.api_key_wb.data or "").strip()
                if key_form.clear.data == "1":
                    update_user(user["id"], api_token_wb="")
                    flash("API-ключ удалён.", "info")
                elif raw:
                    update_user(user["id"], api_token_wb=encrypt_secret(raw))
                    flash("API-ключ сохранён и зашифрован.", "success")
                else:
                    flash("Ключ пустой — ничего не менялось.", "warning")
                return redirect(url_for("auth.profile"))
            flash_errors(key_form)
        elif "change_password" in request.form:
            if pass_form.validate_on_submit():
                if not verify_password(user["password_hash"], pass_form.current.data):
                    flash("Текущий пароль неверный.", "error")
                else:
                    update_user(user["id"], password_hash=hash_password(pass_form.new.data))
                    flash("Пароль изменён.", "success")
                    return redirect(url_for("auth.profile"))
            flash_errors(pass_form)
        elif "issue_token" in request.form:
            if token_form.validate_on_submit():
                label = (token_form.label.data or "").strip() or "мастер заказов"
                token, _ = issue_api_token(user["id"], label=label)
                add_notification(user["id"],
                                 f"Новый API-токен ({label}): {token}", type="message")
                flash("Токен выдан — он показан один раз, в уведомлениях.", "success")
                return redirect(url_for("auth.profile"))
            flash_errors(token_form)
        elif request.form.get("revoke_token"):
            token_id = int(request.form["revoke_token"])
            if revoke_token(user["id"], token_id):
                flash("Токен отозван.", "info")
            else:
                flash("Токен не найден или уже отозван.", "warning")
            return redirect(url_for("auth.profile"))
        elif "send_message" in request.form:
            if message_form.validate_on_submit():
                add_notification(user["id"], message_form.message.data, type="message")
                flash("Сообщение отправлено в поддержку.", "success")
                return redirect(url_for("auth.profile"))
            flash_errors(message_form)

    wb_key = decrypt_secret(user["api_token_wb"] or "")
    context = {
        "form": form,
        "key_form": key_form,
        "pass_form": pass_form,
        "message_form": message_form,
        "token_form": token_form,
        "tokens": list_tokens(user["id"]),
        "wb_key_masked": mask_secret(wb_key),
        "wb_key_set": bool(wb_key),
        "orders_count": len(list_orders_for_user(user["id"])),
    }
    return render_template("client/profile.html", **context)


@bp.route("/notifications", methods=["GET", "POST"])
@login_required
def notifications():
    user = current_user()
    if request.method == "POST":
        raw = request.form.get("mark_read", "")
        ids = [int(x) for x in raw.split(",") if x.isdigit()] if raw else None
        mark_notifications_read(user["id"], ids)
        return redirect(url_for("auth.notifications"))
    return render_template(
        "client/notifications.html",
        items=list_notifications(user["id"]),
    )


@bp.route("/results")
@login_required
def results():
    user = current_user()
    return render_template(
        "client/results.html",
        files=list_result_files(user["id"]),
    )
