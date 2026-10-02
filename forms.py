"""Формы клиентского кабинета и админки (Flask-WTF + CSRF)."""

from __future__ import annotations

from flask_wtf import FlaskForm
from flask_wtf.file import FileAllowed, FileField, FileRequired
from wtforms import (
    BooleanField,
    HiddenField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
from wtforms.validators import DataRequired, Email, EqualTo, Length, Optional

from config import Config
from models import STATUSES, STATUS_LABELS

ALLOWED_EXT = ",".join(sorted(Config.ALLOWED_EXTENSIONS))


class LoginForm(FlaskForm):
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Пароль", validators=[DataRequired()])
    remember = BooleanField("Не выходить")
    submit = SubmitField("Войти")


class RegisterForm(FlaskForm):
    name = StringField("Имя", validators=[DataRequired(), Length(min=2, max=80)])
    email = StringField("Email", validators=[DataRequired(), Email(), Length(max=160)])
    phone = StringField("MAX", validators=[Optional(), Length(max=20)])
    company = StringField("Компания", validators=[Optional(), Length(max=120)])
    password = PasswordField(
        "Пароль", validators=[DataRequired(), Length(min=8, max=128)])
    password2 = PasswordField(
        "Пароль ещё раз",
        validators=[DataRequired(), EqualTo("password", message="Пароли не совпадают")])
    submit = SubmitField("Создать кабинет")


class NewOrderForm(FlaskForm):
    """Выбор услуги, бриф, файлы и API-ключ WB."""

    service_id = SelectField("Услуга", validators=[DataRequired()])
    title = StringField("Тема заявки", validators=[Optional(), Length(max=140)])
    brief = TextAreaField(
        "Бриф: что нужно и в каком виде",
        validators=[DataRequired(), Length(min=10, max=4000)])
    shop = StringField("Магазин / площадка", validators=[Optional(), Length(max=120)])
    urgent = BooleanField("Срочная заявка")
    api_key_wb = PasswordField(
        "API-ключ Wildberries", validators=[Optional(), Length(max=400)],
        description="Ключ шифруется и хранится только у вас. Поле можно оставить пустым.")
    files = FileField(
        "Файлы",
        validators=[FileAllowed(ALLOWED_EXT.split(","), "Недопустимый тип файла")])
    submit = SubmitField("Отправить заявку")


class UploadFilesForm(FlaskForm):
    category = SelectField("Категория", choices=[
        ("data", "Данные"), ("brief", "Файл брифа"), ("other", "Прочее")])
    files = FileField(
        "Файлы",
        validators=[FileAllowed(ALLOWED_EXT.split(","), "Недопустимый тип файла")])
    submit = SubmitField("Загрузить")


class ProfileForm(FlaskForm):
    name = StringField("Имя", validators=[DataRequired(), Length(min=2, max=80)])
    phone = StringField("MAX", validators=[Optional(), Length(max=20)])
    company = StringField("Компания", validators=[Optional(), Length(max=120)])
    submit = SubmitField("Сохранить")


class PasswordChangeForm(FlaskForm):
    current = PasswordField("Текущий пароль", validators=[DataRequired()])
    new = PasswordField("Новый пароль",
                        validators=[DataRequired(), Length(min=8, max=128)])
    new2 = PasswordField("Новый пароль ещё раз",
                         validators=[DataRequired(), EqualTo("new", message="Пароли не совпадают")])
    submit = SubmitField("Сменить пароль")


class ApiKeyForm(FlaskForm):
    api_key_wb = PasswordField("API-ключ Wildberries", validators=[Optional()])
    submit = SubmitField("Сохранить ключ")
    clear = HiddenField("clear")


class MessageForm(FlaskForm):
    """Обращение в поддержку."""

    message = TextAreaField("Сообщение", validators=[DataRequired(), Length(max=2000)])
    submit = SubmitField("Отправить")


class TokenIssueForm(FlaskForm):
    """Выпуск API-токена: метка нужна, чтобы отличать токены друг от друга."""

    label = StringField("Метка токена", validators=[Optional(), Length(max=60)],
                        default="мастер заказов")
    submit = SubmitField("Выпустить токен")


class AdminOrderActionForm(FlaskForm):
    """Смена статуса заявки администратором."""

    order_id = HiddenField(validators=[DataRequired()])
    status = SelectField("Статус", choices=[(s, STATUS_LABELS[s]) for s in STATUSES])
    comment = TextAreaField("Комментарий клиенту", validators=[Optional(), Length(max=2000)])
    price = StringField("Цена", validators=[Optional(), Length(max=40)])
    urgent = BooleanField("Срочная")
    submit = SubmitField("Применить")


class AdminUploadForm(FlaskForm):
    order_id = HiddenField(validators=[DataRequired()])
    category = SelectField("Категория", choices=[
        ("result", "Результат"), ("data", "Данные"), ("brief", "Файл брифа"),
        ("other", "Прочее")])
    files = FileField("Файл результата",
                      validators=[FileAllowed(ALLOWED_EXT.split(","), "Недопустимый тип файла")])
    submit = SubmitField("Прикрепить результат")


class AdminClientSearchForm(FlaskForm):
    """Поиск клиентов в админке — GET-форма, CSRF в ней не нужен."""

    class Meta:
        csrf = False

    q = StringField("Поиск", validators=[Optional(), Length(max=80)])


class ConfirmForm(FlaskForm):
    """Подтверждение деструктивного действия."""

    action = HiddenField(validators=[DataRequired()])
    submit = SubmitField("Подтвердить")
