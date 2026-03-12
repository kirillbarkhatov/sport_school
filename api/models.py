from django.db import models
from django.utils import timezone
import re
from urllib import parse

GOOGLE_ID_PATTERN = re.compile(r"/d/([a-zA-Z0-9_-]{20,})")
PLAIN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{20,}$")


def extract_source_id(link_or_id: str) -> str:
    raw = (link_or_id or "").strip()
    if not raw:
        return ""
    if PLAIN_ID_PATTERN.match(raw):
        return raw
    match = GOOGLE_ID_PATTERN.search(raw)
    if match:
        return match.group(1)
    parsed_url = parse.urlparse(raw)
    query = parse.parse_qs(parsed_url.query)
    query_id = (query.get("id") or [""])[0]
    if query_id and PLAIN_ID_PATTERN.match(query_id):
        return query_id
    return raw.lower()


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
    source_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
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
    telegram_publish_enabled = models.BooleanField(default=False, db_index=True)
    telegram_channel = models.ForeignKey(
        "bot.TelegramChat",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="online_results_stream_runs",
    )
    telegram_resume_from_event_id = models.PositiveBigIntegerField(null=True, blank=True)
    telegram_active_message_id = models.BigIntegerField(null=True, blank=True)
    telegram_active_group_key = models.CharField(max_length=255, blank=True, default="")
    telegram_active_run_stage = models.PositiveSmallIntegerField(null=True, blank=True)
    telegram_last_message_hash = models.CharField(max_length=64, blank=True, default="")
    telegram_finisher_message_id = models.BigIntegerField(null=True, blank=True)
    telegram_finisher_last_hash = models.CharField(max_length=64, blank=True, default="")
    telegram_link_message_id = models.BigIntegerField(null=True, blank=True)
    telegram_link_last_hash = models.CharField(max_length=64, blank=True, default="")
    telegram_last_error = models.TextField(blank=True, default="")
    competition = models.ForeignKey(
        "school.Competition",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="online_result_stream_runs",
    )
    last_requested_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-last_requested_at", "-created_at")

    def __str__(self) -> str:
        return f"{self.stream_id}:{self.status}"

    def save(self, *args, **kwargs):
        self.protocol_link = (self.protocol_link or "").strip()
        if self.protocol_link and not self.source_id:
            self.source_id = extract_source_id(self.protocol_link)
        self.source_id = (self.source_id or "").strip()
        super().save(*args, **kwargs)


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
