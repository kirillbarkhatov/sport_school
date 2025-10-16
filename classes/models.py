from __future__ import annotations

from datetime import date, timedelta

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.utils import timezone

from school.choices import TrainingEquipment, TrainingKind, TrainingLocation
from school.models import Class, Group


class Weekday(models.IntegerChoices):
    MONDAY = 0, "Понедельник"
    TUESDAY = 1, "Вторник"
    WEDNESDAY = 2, "Среда"
    THURSDAY = 3, "Четверг"
    FRIDAY = 4, "Пятница"
    SATURDAY = 5, "Суббота"
    SUNDAY = 6, "Воскресенье"


class TrainingTemplate(models.Model):
    """Шаблон регулярного занятия."""

    name = models.CharField(max_length=200, verbose_name="Название шаблона")
    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="training_templates",
        verbose_name="Группа",
    )
    day_of_week = models.PositiveSmallIntegerField(
        choices=Weekday.choices,
        verbose_name="День недели",
    )
    start_time = models.TimeField(verbose_name="Время начала")
    duration_minutes = models.PositiveSmallIntegerField(
        default=90,
        verbose_name="Продолжительность (мин.)",
    )
    season_start_month = models.PositiveSmallIntegerField(
        verbose_name="Месяц начала сезона",
        help_text="1 — январь, 12 — декабрь",
    )
    season_end_month = models.PositiveSmallIntegerField(
        verbose_name="Месяц окончания сезона",
        help_text="1 — январь, 12 — декабрь",
    )
    training_type = models.CharField(
        max_length=32,
        choices=TrainingKind.choices,
        default=TrainingKind.OTHER,
        verbose_name="Вид тренировки",
    )
    location = models.CharField(
        max_length=32,
        choices=TrainingLocation.choices,
        default=TrainingLocation.OTHER,
        verbose_name="Локация",
    )
    equipment = ArrayField(
        models.CharField(max_length=32, choices=TrainingEquipment.choices),
        default=list,
        blank=True,
        verbose_name="Экипировка",
    )
    class_type = models.CharField(
        max_length=10,
        choices=Class.TYPE_CHOICES,
        default="regular",
        verbose_name="Тип занятия",
    )
    comment = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Комментарий",
    )
    is_active = models.BooleanField(default=True, verbose_name="Активен")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Шаблон занятия"
        verbose_name_plural = "Шаблоны занятий"
        ordering = ("group__name", "day_of_week", "start_time")
        constraints = [
            models.UniqueConstraint(
                fields=(
                    "group",
                    "day_of_week",
                    "start_time",
                    "season_start_month",
                    "season_end_month",
                ),
                name="classes_trainingtemplate_unique_slot",
            )
        ]

    def __str__(self) -> str:
        return (
            f"{self.group.name} · {Weekday(self.day_of_week).label} {self.start_time:%H:%M}"
        )

    def applies_to_date(self, target_date: date) -> bool:
        month = target_date.month
        if self.season_start_month <= self.season_end_month:
            return self.season_start_month <= month <= self.season_end_month
        return month >= self.season_start_month or month <= self.season_end_month

    def next_occurrence(self, start_date: date | None = None) -> date:
        base_date = start_date or timezone.localdate()
        offset = (self.day_of_week - base_date.weekday()) % 7
        candidate = base_date + timedelta(days=offset)
        # if template does not apply to that date, try the following week
        if not self.applies_to_date(candidate):
            candidate += timedelta(days=7)
        return candidate

    def default_equipment(self) -> list[str]:
        return list(self.equipment or [])
