from django.db import models
from django.utils import timezone


class WebhookEvent(models.Model):
    stream_id = models.CharField(max_length=255, blank=True)
    event_type = models.CharField(max_length=255, blank=True)
    event_time = models.DateTimeField(null=True, blank=True)
    payload_json = models.JSONField()
    received_at = models.DateTimeField(auto_now_add=True)
    payload_hash = models.CharField(max_length=64, unique=True, db_index=True)

    class Meta:
        ordering = ("-received_at",)

    def __str__(self) -> str:
        return f"{self.event_type or 'unknown'}:{self.payload_hash[:12]}"


class StreamRun(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает запуска"
        RUNNING = "running", "Выполняется"
        SUCCESS = "success", "Завершен"
        STOPPED = "stopped", "Остановлен"
        FAILED = "failed", "Ошибка"

    stream_id = models.CharField(max_length=255, db_index=True)
    protocol_link = models.CharField(max_length=2048, blank=True, default="")
    stream_type = models.CharField(max_length=128, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    launch_payload_json = models.JSONField(default=dict, blank=True)
    callback_url = models.URLField(max_length=1024)
    external_run_id = models.CharField(max_length=255, blank=True)
    external_response_json = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="online_results_stream_runs",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.stream_id}:{self.status}"


class PublicStreamAccess(models.Model):
    stream_run = models.ForeignKey(
        StreamRun,
        on_delete=models.CASCADE,
        related_name="public_access_links",
    )
    token = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return f"{self.stream_run_id}:{self.token[:8]}"

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at
