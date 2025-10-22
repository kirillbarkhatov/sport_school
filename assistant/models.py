from __future__ import annotations

import secrets
from datetime import timedelta
from typing import Any, Dict, Optional

import jwt
from django.db import models
from django.utils import timezone as dj_timezone


class ServiceAccount(models.Model):
    """Сервисные аккаунты для интеграций через JWT."""

    name = models.CharField(max_length=100, verbose_name="Название")
    slug = models.SlugField(max_length=50, unique=True, verbose_name="Идентификатор")
    key = models.CharField(
        max_length=64,
        unique=True,
        default="",
        verbose_name="Ключ (kid)",
        help_text="Используется в заголовке JWT для идентификации секрета.",
    )
    secret = models.CharField(
        max_length=128,
        verbose_name="Секрет",
        help_text="Используется для подписи JWT (HS256).",
    )
    issuer = models.CharField(
        max_length=100,
        default="sport-school",
        verbose_name="Issuer",
    )
    audience = models.CharField(
        max_length=100,
        default="ai-assistant",
        verbose_name="Audience",
    )
    lifetime_seconds = models.PositiveIntegerField(
        default=300,
        verbose_name="Срок действия токена (сек.)",
    )
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлён")

    class Meta:
        verbose_name = "Сервисный аккаунт"
        verbose_name_plural = "Сервисные аккаунты"
        ordering = ("name",)

    def __str__(self) -> str:
        return f"{self.name} ({self.slug})"

    @property
    def is_authenticated(self) -> bool:
        return True

    def ensure_key(self) -> None:
        if not self.key:
            self.key = secrets.token_hex(24)

    def save(self, *args, **kwargs):
        self.ensure_key()
        super().save(*args, **kwargs)

    def rotate_secret(self) -> str:
        """Генерирует новый секрет и сохраняет модель."""
        self.secret = secrets.token_urlsafe(64)
        self.save(update_fields=["secret", "updated_at"])
        return self.secret

    def issue_token(self, *, additional_claims: Optional[Dict[str, Any]] = None) -> str:
        """Формирует JWT для авторизации при исходящих запросах."""
        if not self.is_active:
            raise ValueError("Service account is not active")

        now = dj_timezone.now()
        expires_at = now + timedelta(seconds=self.lifetime_seconds)

        payload: Dict[str, Any] = {
            "iss": self.issuer,
            "sub": self.slug,
            "aud": self.audience,
            "iat": int(now.timestamp()),
            "exp": int(expires_at.timestamp()),
        }
        if additional_claims:
            payload.update(additional_claims)

        headers = {"kid": self.key}
        token = jwt.encode(payload, self.secret, algorithm="HS256", headers=headers)
        if isinstance(token, bytes):
            token = token.decode("utf-8")
        return token


class AssistantSyncLog(models.Model):
    """История обмена данными с AI-помощником."""

    class Direction(models.TextChoices):
        OUTGOING = "outgoing", "Исходящий"
        INCOMING = "incoming", "Входящий"

    service_account = models.ForeignKey(
        ServiceAccount,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="sync_logs",
        verbose_name="Сервисный аккаунт",
    )
    direction = models.CharField(
        max_length=16,
        choices=Direction.choices,
        verbose_name="Направление",
    )
    event_type = models.CharField(max_length=64, verbose_name="Тип события")
    request_url = models.URLField(
        max_length=500,
        blank=True,
        verbose_name="URL запроса",
    )
    status_code = models.PositiveIntegerField(
        blank=True,
        null=True,
        verbose_name="HTTP статус",
    )
    request_payload = models.JSONField(
        blank=True,
        null=True,
        verbose_name="Отправленные данные",
    )
    response_payload = models.JSONField(
        blank=True,
        null=True,
        verbose_name="Полученные данные",
    )
    error_message = models.TextField(
        blank=True,
        verbose_name="Ошибка",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создан")

    class Meta:
        verbose_name = "Лог обмена с ассистентом"
        verbose_name_plural = "Логи обмена с ассистентом"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.get_direction_display()} · {self.event_type} ({self.created_at:%Y-%m-%d %H:%M})"


class AssistantUnmatchedParticipant(models.Model):
    """Участники, которых не удалось сопоставить с локальными данными."""

    service_account = models.ForeignKey(
        ServiceAccount,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="unmatched_participants",
        verbose_name="Сервисный аккаунт",
    )
    training = models.ForeignKey(
        "school.Class",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="assistant_unmatched_participants",
        verbose_name="Занятие",
    )
    full_name = models.CharField(max_length=255, blank=True, verbose_name="ФИО")
    phone = models.CharField(max_length=32, blank=True, verbose_name="Телефон")
    comment = models.TextField(blank=True, verbose_name="Комментарий")
    raw_payload = models.JSONField(
        blank=True,
        null=True,
        verbose_name="Исходные данные",
    )
    received_at = models.DateTimeField(auto_now_add=True, verbose_name="Получено")

    class Meta:
        verbose_name = "Не сопоставленный участник"
        verbose_name_plural = "Не сопоставленные участники"
        ordering = ("-received_at",)

    def __str__(self) -> str:
        base = self.full_name or "Неизвестный участник"
        if self.training_id:
            return f"{base} · занятие #{self.training_id}"
        return base
