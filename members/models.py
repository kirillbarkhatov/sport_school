from django.conf import settings
from django.db import models
from django.utils import timezone


class PersonDedupJob(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CANCELED = "canceled"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "В очереди"),
        (STATUS_RUNNING, "Выполняется"),
        (STATUS_COMPLETED, "Завершено"),
        (STATUS_FAILED, "Ошибка"),
        (STATUS_CANCELED, "Отменено"),
    ]

    MODE_ANALYZE = "analyze"
    MODE_AUTO_SAFE = "auto_safe"
    MODE_CHOICES = [
        (MODE_ANALYZE, "Только анализ"),
        (MODE_AUTO_SAFE, "Анализ + автообъединение безопасных"),
    ]

    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_QUEUED,
        verbose_name="Статус",
    )
    mode = models.CharField(
        max_length=16,
        choices=MODE_CHOICES,
        default=MODE_ANALYZE,
        verbose_name="Режим",
    )
    dry_run = models.BooleanField(default=True, verbose_name="Пробный запуск")
    min_score = models.PositiveSmallIntegerField(default=55, verbose_name="Порог поиска")
    auto_merge_score = models.PositiveSmallIntegerField(default=92, verbose_name="Порог автообъединения")
    total_persons = models.PositiveIntegerField(default=0, verbose_name="Всего персон")
    processed_persons = models.PositiveIntegerField(default=0, verbose_name="Обработано персон")
    duplicate_clusters_found = models.PositiveIntegerField(default=0, verbose_name="Найдено кластеров")
    auto_merged_clusters = models.PositiveIntegerField(default=0, verbose_name="Автообъединено")
    conflicts_count = models.PositiveIntegerField(default=0, verbose_name="Требует решения")
    error_message = models.TextField(blank=True, verbose_name="Ошибка")
    log = models.JSONField(default=list, blank=True, verbose_name="Лог запуска")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="dedup_jobs",
        verbose_name="Запущено пользователем",
    )
    started_at = models.DateTimeField(null=True, blank=True, verbose_name="Начато")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="Завершено")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Задача дедупликации персон"
        verbose_name_plural = "Задачи дедупликации персон"

    def mark_running(self):
        self.status = self.STATUS_RUNNING
        self.started_at = timezone.now()

    def mark_finished(self, *, status: str, error_message: str = ""):
        self.status = status
        self.error_message = error_message
        self.finished_at = timezone.now()


class PersonDuplicateCluster(models.Model):
    STATUS_OPEN = "open"
    STATUS_SKIPPED = "skipped"
    STATUS_MERGED = "merged"
    STATUS_AUTO_MERGED = "auto_merged"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Открыт"),
        (STATUS_SKIPPED, "Пропущен"),
        (STATUS_MERGED, "Объединён"),
        (STATUS_AUTO_MERGED, "Автообъединён"),
    ]

    job = models.ForeignKey(
        PersonDedupJob,
        on_delete=models.CASCADE,
        related_name="clusters",
        verbose_name="Задача",
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_OPEN,
        verbose_name="Статус",
    )
    confidence = models.PositiveSmallIntegerField(default=0, verbose_name="Уверенность")
    reason_summary = models.TextField(blank=True, verbose_name="Краткое объяснение")
    max_pair_score = models.PositiveSmallIntegerField(default=0, verbose_name="Макс. score")
    candidate_pairs = models.PositiveIntegerField(default=0, verbose_name="Связей в кластере")
    requires_manual_review = models.BooleanField(default=True, verbose_name="Нужно ручное решение")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        ordering = ["-max_pair_score", "-created_at"]
        verbose_name = "Кластер дублей персон"
        verbose_name_plural = "Кластеры дублей персон"


class PersonDuplicateItem(models.Model):
    cluster = models.ForeignKey(
        PersonDuplicateCluster,
        on_delete=models.CASCADE,
        related_name="items",
        verbose_name="Кластер",
    )
    person = models.ForeignKey(
        "school.Person",
        on_delete=models.CASCADE,
        related_name="dedup_items",
        verbose_name="Персона",
    )
    aggregate_score = models.PositiveSmallIntegerField(default=0, verbose_name="Суммарный score")
    reasons = models.JSONField(default=list, blank=True, verbose_name="Причины")
    is_suggested_master = models.BooleanField(default=False, verbose_name="Рекомендуемый мастер")

    class Meta:
        verbose_name = "Участник кластера дублей"
        verbose_name_plural = "Участники кластеров дублей"
        constraints = [
            models.UniqueConstraint(
                fields=("cluster", "person"),
                name="members_duplicate_item_unique_cluster_person",
            )
        ]
        ordering = ["-aggregate_score", "person__surname", "person__name"]


class PersonMergeRedirect(models.Model):
    source_person = models.OneToOneField(
        "school.Person",
        on_delete=models.CASCADE,
        related_name="merge_redirect_source",
        verbose_name="Исходная персона",
    )
    target_person = models.ForeignKey(
        "school.Person",
        on_delete=models.CASCADE,
        related_name="merge_redirect_targets",
        verbose_name="Целевая персона",
    )
    job = models.ForeignKey(
        PersonDedupJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="redirects",
        verbose_name="Задача",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    is_active = models.BooleanField(default=True, verbose_name="Активен")

    class Meta:
        verbose_name = "Редирект объединённой персоны"
        verbose_name_plural = "Редиректы объединённых персон"
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(source_person=models.F("target_person")),
                name="members_merge_redirect_source_ne_target",
            )
        ]


class PersonMergeLog(models.Model):
    cluster = models.ForeignKey(
        PersonDuplicateCluster,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merge_logs",
        verbose_name="Кластер",
    )
    job = models.ForeignKey(
        PersonDedupJob,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="merge_logs",
        verbose_name="Задача",
    )
    master_person = models.ForeignKey(
        "school.Person",
        on_delete=models.CASCADE,
        related_name="merge_logs_as_master",
        verbose_name="Итоговая персона",
    )
    merged_person_ids = models.JSONField(default=list, verbose_name="ID объединённых персон")
    field_resolution = models.JSONField(default=dict, blank=True, verbose_name="Разрешение полей")
    moved_relations = models.JSONField(default=dict, blank=True, verbose_name="Перенесённые связи")
    notes = models.TextField(blank=True, verbose_name="Комментарий")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="person_merge_logs",
        verbose_name="Исполнитель",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Журнал объединения персон"
        verbose_name_plural = "Журнал объединений персон"
