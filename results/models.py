import hashlib
import json
import secrets
import zlib

from django.conf import settings
from django.db import models
from django.utils import timezone


def _public_token() -> str:
    return secrets.token_urlsafe(24)


class ResultsEvent(models.Model):
    """Трансляция результатов одного соревнования."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        RUNNING = "running", "Идёт трансляция"
        PAUSED = "paused", "Пауза"
        FINISHED = "finished", "Завершена"

    class SourceKind(models.TextChoices):
        GOOGLE_SHEET = "google_sheet", "Google Таблица"
        DRIVE_XLSX = "drive_xlsx", "XLSX на Google Drive"
        UPLOAD = "upload", "Загруженный файл XLSX"
        MANUAL = "manual", "Ручной ввод"

    title = models.CharField("Название", max_length=255)
    competition = models.ForeignKey(
        "school.Competition",
        verbose_name="Соревнование",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="results_events",
    )
    status = models.CharField("Статус", max_length=16, choices=Status.choices, default=Status.DRAFT, db_index=True)

    source_kind = models.CharField(
        "Источник", max_length=16, choices=SourceKind.choices, default=SourceKind.GOOGLE_SHEET
    )
    source_ref = models.CharField("Ссылка или ID файла", max_length=1024, blank=True)
    source_file = models.FileField("Файл протокола", upload_to="results/protocols/", blank=True)
    column_mappings = models.JSONField(
        "Разметка колонок по листам",
        default=dict,
        blank=True,
        help_text="{лист: {start_number, full_name|surname+first_name, club, run1, run2, total, birth_year, header_row}}",
    )

    poll_interval_sec = models.FloatField("Опрос во время заезда, с", default=2.0)
    idle_interval_sec = models.FloatField("Опрос в перерывах, с", default=15.0)
    finalize_timeout_sec = models.PositiveIntegerField("Автофинализация после паузы, с", default=300)
    finalize_max_missing = models.PositiveSmallIntegerField("Автофинализация, если не хватает ≤", default=2)
    highlight_club = models.CharField("Подсвечивать клуб", max_length=255, blank=True)

    protocol_title = models.CharField("Название из протокола", max_length=255, blank=True)
    protocol_date = models.CharField("Дата из протокола", max_length=32, blank=True)
    focus = models.JSONField("Фокус трансляции", default=dict, blank=True)
    version = models.PositiveBigIntegerField("Версия состояния", default=0)
    seq = models.PositiveBigIntegerField("Последний номер события", default=0)
    last_polled_at = models.DateTimeField(null=True, blank=True)
    last_changed_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    parse_warnings = models.JSONField(default=list, blank=True)

    public_enabled = models.BooleanField("Публичная ссылка включена", default=True)
    public_token = models.CharField(max_length=64, unique=True, default=_public_token)

    telegram_enabled = models.BooleanField("Публиковать в Telegram", default=False)
    telegram_channel = models.ForeignKey(
        "bot.TelegramChat",
        verbose_name="Telegram-канал",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="results_events",
    )

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Трансляция результатов"
        verbose_name_plural = "Трансляции результатов"
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.title

    def next_seq(self) -> int:
        """Атомарно увеличивает счётчик событий (вызывать внутри транзакции с select_for_update)."""
        self.seq += 1
        return self.seq


class ResultGroup(models.Model):
    event = models.ForeignKey(ResultsEvent, on_delete=models.CASCADE, related_name="groups")
    key = models.CharField(max_length=512)
    sheet_name = models.CharField("Лист", max_length=255)
    name = models.CharField("Группа", max_length=255)
    order_index = models.PositiveIntegerField(default=0)
    last_result_at = models.DateTimeField("Последний результат", null=True, blank=True)
    removed = models.BooleanField("Исчезла из протокола", default=False)

    class Meta:
        verbose_name = "Группа"
        verbose_name_plural = "Группы"
        ordering = ("event", "order_index")
        constraints = [models.UniqueConstraint(fields=("event", "key"), name="results_group_unique_key")]

    def __str__(self) -> str:
        return f"{self.sheet_name} / {self.name}"


class ResultEntry(models.Model):
    """Участник протокола. Значения хранятся «как в источнике»; ручные правки — в *_manual и имеют приоритет."""

    class LinkMethod(models.TextChoices):
        NONE = "", "Не связан"
        AUTO = "auto", "Автоматически"
        MANUAL = "manual", "Вручную"

    event = models.ForeignKey(ResultsEvent, on_delete=models.CASCADE, related_name="entries")
    group = models.ForeignKey(ResultGroup, on_delete=models.CASCADE, related_name="entries")
    key = models.CharField(max_length=767)
    start_number = models.PositiveIntegerField("Ст. №")
    full_name = models.CharField("Фамилия Имя", max_length=255)
    club = models.CharField("Клуб", max_length=255, blank=True)
    birth_year = models.PositiveSmallIntegerField("Г.р.", null=True, blank=True)
    runs_count = models.PositiveSmallIntegerField(default=2)
    sheet_row = models.PositiveIntegerField(default=0)
    judge_note = models.CharField("Пометка судьи", max_length=255, blank=True)

    run1_raw = models.CharField("1 заезд", max_length=32, blank=True)
    run2_raw = models.CharField("2 заезд", max_length=32, blank=True)
    total_raw = models.CharField("Итог", max_length=32, blank=True)
    run1_manual = models.CharField("1 заезд (правка)", max_length=32, null=True, blank=True)
    run2_manual = models.CharField("2 заезд (правка)", max_length=32, null=True, blank=True)
    run1_seen_at = models.DateTimeField(null=True, blank=True)
    run2_seen_at = models.DateTimeField(null=True, blank=True)
    removed = models.BooleanField("Исчез из протокола", default=False)

    athlete = models.ForeignKey(
        "school.Athlete",
        verbose_name="Спортсмен школы",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="result_entries",
    )
    link_method = models.CharField(max_length=8, choices=LinkMethod.choices, default=LinkMethod.NONE, blank=True)

    class Meta:
        verbose_name = "Участник"
        verbose_name_plural = "Участники"
        ordering = ("group__order_index", "sheet_row", "start_number")
        constraints = [models.UniqueConstraint(fields=("event", "key"), name="results_entry_unique_key")]

    def __str__(self) -> str:
        return f"{self.start_number} {self.full_name}"

    @property
    def effective_raw_1(self) -> str:
        return self.effective_raw(1)

    @property
    def effective_raw_2(self) -> str:
        return self.effective_raw(2)

    def effective_raw(self, run_number: int) -> str:
        manual = self.run1_manual if run_number == 1 else self.run2_manual
        if manual is not None:
            return manual
        return self.run1_raw if run_number == 1 else self.run2_raw


class EventLog(models.Model):
    """Журнал трансляции: изменения результатов, фокус, финализация, ручные правки, жизненный цикл."""

    class Kind(models.TextChoices):
        RESULT = "result", "Результат"
        ENTRY = "entry", "Участник"
        FOCUS = "focus", "Фокус"
        LIFECYCLE = "lifecycle", "Статус трансляции"
        MANUAL = "manual", "Ручная правка"
        ERROR = "error", "Ошибка"

    event = models.ForeignKey(ResultsEvent, on_delete=models.CASCADE, related_name="log")
    seq = models.PositiveBigIntegerField()
    kind = models.CharField(max_length=16, choices=Kind.choices)
    message = models.CharField(max_length=500, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "Событие трансляции"
        verbose_name_plural = "Журнал трансляций"
        ordering = ("event", "seq")
        constraints = [models.UniqueConstraint(fields=("event", "seq"), name="results_log_unique_seq")]

    def __str__(self) -> str:
        return f"#{self.seq} {self.kind}: {self.message}"


class RawSnapshot(models.Model):
    """Сжатый сырой снимок листов источника — только при изменении. Нужен для replay и разбора ошибок."""

    event = models.ForeignKey(ResultsEvent, on_delete=models.CASCADE, related_name="snapshots")
    taken_at = models.DateTimeField(default=timezone.now, db_index=True)
    content_hash = models.CharField(max_length=64)
    data = models.BinaryField()

    class Meta:
        verbose_name = "Снимок источника"
        verbose_name_plural = "Снимки источника"
        ordering = ("event", "taken_at", "id")

    def __str__(self) -> str:
        return f"{self.event_id} @ {self.taken_at:%H:%M:%S}"

    @staticmethod
    def hash_sheets(sheets: dict) -> str:
        return hashlib.sha256(json.dumps(sheets, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()

    @classmethod
    def pack(cls, sheets: dict) -> bytes:
        return zlib.compress(json.dumps(sheets, ensure_ascii=False, default=str).encode(), level=6)

    def sheets(self) -> dict:
        return json.loads(zlib.decompress(bytes(self.data)).decode())


class TelegramPublication(models.Model):
    """Состояние постов трансляции в канале: таблица текущей группы, последний финиш, ссылка."""

    event = models.OneToOneField(ResultsEvent, on_delete=models.CASCADE, related_name="telegram")
    table_message_id = models.BigIntegerField(null=True, blank=True)
    table_key = models.CharField(max_length=600, blank=True)
    table_hash = models.CharField(max_length=64, blank=True)
    finisher_message_id = models.BigIntegerField(null=True, blank=True)
    finisher_hash = models.CharField(max_length=64, blank=True)
    link_message_id = models.BigIntegerField(null=True, blank=True)
    link_hash = models.CharField(max_length=64, blank=True)
    published_version = models.PositiveBigIntegerField(default=0)
    last_error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Публикация в Telegram"
        verbose_name_plural = "Публикации в Telegram"

    def __str__(self) -> str:
        return f"Telegram: {self.event}"
