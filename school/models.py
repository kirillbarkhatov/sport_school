from decimal import Decimal

from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.urls import reverse
from django.templatetags.static import static
from django.utils import timezone
from .choices import (
    ClassCoachStatus,
    ClassCreationSource,
    TrainingEquipment,
    TrainingKind,
    TrainingLocation,
)
from .training_rules import apply_training_rules


class DiscountType(models.TextChoices):
    NONE = "none", "Без скидки"
    SECOND_CHILD = "second_child", "Второй ребёнок"
    THIRD_CHILD = "third_child", "Третий ребёнок"
    PERSONAL = "personal", "Персональная скидка"
    ACHIEVEMENT = "achievement", "За достижения"
    PREPAYMENT = "prepayment", "Оплата абонемента вперёд"


class ServiceType(models.TextChoices):
    MONTHLY = "monthly", "Ежемесячный платеж"
    LODGE = "lodge", "Сервисный домик"
    SKIPASS_SNOW = "skipass_snow", "Скипасс «Снежный»"
    SKIPASS_YUKKI = "skipass_yukki", "Скипасс «Юкки»"
    SKI_PREP = "ski_preparation", "Подготовка лыж"
    FEDERATION = "federation", "Взнос в федерацию"
    CAMP = "camp", "Сборы"
    INDIVIDUAL = "individual_training", "Индивидуальная тренировка"
    EXTRA = "extra_payment", "Дополнительная услуга"
    OTHER = "other", "Прочее"


class PaymentType(models.TextChoices):
    PREPAYMENT = "prepayment", "Предоплата"
    ADDITIONAL = "additional", "Доплата"
    FULL = "full", "Полная оплата"


def default_birth_year_from():
    return timezone.now().year - 7

# всё из чат-гпт, проверить
class Person(models.Model):
    """Модель «Человек»"""

    GENDER_CHOICES = [
        ("male", "Мужской"),
        ("female", "Женский"),
    ]

    name = models.CharField(max_length=100, verbose_name="Имя")
    surname = models.CharField(max_length=100, verbose_name="Фамилия")
    middlename = models.CharField(
        max_length=100, blank=True, null=True, verbose_name="Отчество"
    )
    date_of_birth = models.DateField(
        blank=True,
        null=True,
        verbose_name="Дата рождения",
    )
    email = models.EmailField(blank=True, null=True, verbose_name="Электронная почта")
    phone = models.CharField(
        max_length=15, blank=True, null=True, verbose_name="Телефон"
    )
    telegram = models.CharField(
        max_length=100, blank=True, null=True, verbose_name="Telegram"
    )
    photo = models.ImageField(
        upload_to="person/photos", blank=True, null=True, verbose_name="Фото"
    )
    club = models.ForeignKey(
        "Club",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Клуб",
        related_name="members",
    )
    comment = models.TextField(blank=True, null=True, verbose_name="Комментарий")
    gender = models.CharField(max_length=6, choices=GENDER_CHOICES, verbose_name="Пол")

    def __str__(self):
        parts = [self.surname, self.name]
        return " ".join(part for part in parts if part)

    def get_absolute_url(self):
        return reverse("members:members_detail", args=[self.pk])

    def get_photo_url(self):
        if self.photo and self.photo.name:
            try:
                if self.photo.storage.exists(self.photo.name):
                    return self.photo.url
            except Exception:
                pass
        return static("img/person-placeholder.svg")

    @property
    def is_athlete(self) -> bool:
        return hasattr(self, "athlete")

    class Meta:
        verbose_name = "Человек"
        verbose_name_plural = "Люди"
        ordering = [
            "surname",
        ]


class Athlete(models.Model):
    """Модель «Спортсмен»"""

    LEVEL_CHOICES = [
        ("unknown", "Не указан"),
        ("2015-2016", "2015/2016"),
        ("2016-2017", "2016/2017"),
        ("2017-2018", "2017/2018"),
        ("2018-2019", "2018/2019"),
        ("2019-2020", "2019/2020"),
        ("2020-2021", "2020/2021"),
        ("2021-2022", "2021/2022"),
        ("2022-2023", "2022/2023"),
        ("2023-2024", "2023/2024"),
        ("2024-2025", "2024/2025"),
        ("2025-2026", "2025/2026"),
    ]

    RANK_CHOICES = [
        ("БР", "БР"),
        ("3ю", "3ю"),
        ("2ю", "2ю"),
        ("1ю", "1ю"),
        ("III", "III"),
        ("II", "II"),
        ("I", "I"),
        ("КМС", "КМС"),
        ("МС", "МС"),
        ("МСМК", "МСМК"),
    ]

    person = models.OneToOneField(
        Person, on_delete=models.CASCADE, verbose_name="Человек"
    )
    level = models.CharField(
        max_length=50,
        choices=LEVEL_CHOICES,
        verbose_name="Уровень подготовки (Первый сезон)",
    )
    rank = models.CharField(
        max_length=50,
        choices=RANK_CHOICES,
        blank=True,
        null=True,
        verbose_name="Разряд",
    )
    medical_certificate = models.CharField(
        max_length=100, blank=True, null=True, verbose_name="Справка-допуск"
    )
    comment = models.TextField(blank=True, null=True, verbose_name="Комментарий")

    def __str__(self):
        return f"{self.person.surname} {self.person.name} - {self.level}"

    class Meta:
        verbose_name = "Спортсмен"
        verbose_name_plural = "Спортсмены"
        ordering = [
            "person__surname",
        ]


class Coach(models.Model):
    """Модель «Тренер»"""

    person = models.OneToOneField(
        Person, on_delete=models.CASCADE, verbose_name="Человек"
    )
    specialization = models.CharField(
        max_length=100, blank=True, null=True, verbose_name="Специализация"
    )

    def __str__(self):
        return f"{self.person.surname} - {self.specialization}"

    class Meta:
        verbose_name = "Тренер"
        verbose_name_plural = "Тренеры"


class PotentialClient(models.Model):
    """Модель «Потенциальный клиент»"""

    person = models.OneToOneField(
        Person, on_delete=models.CASCADE, verbose_name="Человек"
    )
    interested_in = models.TextField(verbose_name="Интересующие услуги")
    source = models.CharField(max_length=100, verbose_name="Откуда узнал")
    trial_lesson = models.BooleanField(
        default=False, verbose_name="Запись на пробное занятие"
    )
    first_month_paid = models.BooleanField(
        default=False, verbose_name="Оплатил первый месяц"
    )
    comments = models.TextField(blank=True, null=True, verbose_name="Комментарий")

    def __str__(self):
        return f"{self.person.surname} - Потенциальный клиент"

    class Meta:
        verbose_name = "Потенциальный клиент"
        verbose_name_plural = "Потенциальные клиенты"


class Group(models.Model):
    """Модель «Группа»"""

    name = models.CharField(max_length=100, verbose_name="Название группы")
    level = models.CharField(
        max_length=50, blank=True, null=True, verbose_name="Уровень подготовки"
    )
    coaches = models.ManyToManyField(
        Coach, blank=True, related_name="groups", verbose_name="Тренеры"
    )
    athletes = models.ManyToManyField(
        Athlete, blank=True, related_name="groups_athletes", verbose_name="Спортсмены"
    )

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Группа"
        verbose_name_plural = "Группы"


class Class(models.Model):
    """Модель «Занятие»"""

    TYPE_CHOICES = [
        ("regular", "Регулярное"),
        ("individual", "Индивидуальное"),
        ("camp", "В ходе сбора"),
    ]

    date = models.DateTimeField(verbose_name="Дата и время занятия")
    duration = models.IntegerField(verbose_name="Продолжительность занятия (мин.)")
    location = models.CharField(
        max_length=32,
        choices=TrainingLocation.choices,
        default=TrainingLocation.OTHER,
        verbose_name="Место проведения",
        help_text="Ключевое место тренировки, например из предложенного списка",
    )
    training_type = models.CharField(
        max_length=32,
        choices=TrainingKind.choices,
        default=TrainingKind.OTHER,
        verbose_name="Вид тренировки",
        help_text="Например ОФП, ролики или другое направление из списка",
    )
    equipment = ArrayField(
        models.CharField(max_length=32, choices=TrainingEquipment.choices),
        default=list,
        blank=True,
        verbose_name="Необходимое снаряжение",
        help_text="Список экипировки, можно выбрать несколько вариантов",
    )
    group = models.ForeignKey(
        Group,
        on_delete=models.CASCADE,
        related_name="classes",
        verbose_name="Группа",
        blank=True,
        null=True,
    )
    type = models.CharField(
        max_length=10, choices=TYPE_CHOICES, verbose_name="Тип занятия"
    )
    comment = models.TextField(blank=True, null=True, verbose_name="Комментарий")
    creation_source = models.CharField(
        max_length=16,
        choices=ClassCreationSource.choices,
        default=ClassCreationSource.MANUAL,
        verbose_name="Источник создания",
    )
    coach_status = models.CharField(
        max_length=16,
        choices=ClassCoachStatus.choices,
        default=ClassCoachStatus.PENDING,
        verbose_name="Статус у тренера",
    )
    coach_status_set_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name="Обработано тренером",
    )
    coach_comment = models.TextField(
        blank=True,
        null=True,
        verbose_name="Комментарий тренера",
    )
    assistant_comment = models.TextField(
        blank=True,
        default="",
        verbose_name="Комментарий ассистента",
    )

    def __str__(self):
        local_dt = timezone.localtime(self.date)
        main_part = f"{self.get_training_type_display()} · {local_dt:%d.%m %H:%M}"
        group_name = self.group.name if self.group_id else "Без группы"
        return f"{group_name}: {main_part}"

    class Meta:
        verbose_name = "Занятие"
        verbose_name_plural = "Занятия"

    def save(self, *args, **kwargs):
        apply_training_rules(self)
        if (
            self.pk is None
            and self.creation_source == ClassCreationSource.MANUAL
            and self.coach_status == ClassCoachStatus.PENDING
        ):
            self.coach_status = ClassCoachStatus.PLANNED
            self.coach_status_set_at = timezone.now()
        super().save(*args, **kwargs)

    @property
    def start_date(self):
        """Дата старта занятия в локальной таймзоне."""
        return timezone.localdate(self.date)

    @property
    def start_time(self):
        """Время старта занятия в локальной таймзоне."""
        return timezone.localtime(self.date).time().replace(microsecond=0)

    def get_equipment_display(self) -> str:
        """Экипировка в виде строки, удобно для шаблонов и уведомлений."""
        labels = []
        choice_map = dict(TrainingEquipment.choices)
        for code in self.equipment or []:
            labels.append(choice_map.get(code, code))
        return ", ".join(labels)

    @property
    def is_coach_processed(self) -> bool:
        return self.coach_status != ClassCoachStatus.PENDING


class ClassEnrollment(models.Model):
    """Модель «Запись на занятие»"""

    ASSISTANT_STATUS_UNKNOWN = "unknown"
    ASSISTANT_STATUS_CONFIRMED = "confirmed"
    ASSISTANT_STATUS_DECLINED = "declined"
    ASSISTANT_STATUS_PENDING = "pending"
    ASSISTANT_STATUS_CHOICES = [
        (ASSISTANT_STATUS_UNKNOWN, "Неизвестно"),
        (ASSISTANT_STATUS_CONFIRMED, "Придёт"),
        (ASSISTANT_STATUS_DECLINED, "Не придёт"),
        (ASSISTANT_STATUS_PENDING, "Ожидает подтверждения"),
    ]

    athlete = models.ForeignKey(
        Athlete,
        on_delete=models.CASCADE,
        related_name="class_enrollments",
        verbose_name="Спортсмен",
    )
    class_instance = models.ForeignKey(
        Class,
        on_delete=models.CASCADE,
        related_name="enrollments",
        verbose_name="Занятие",
    )
    confirmed = models.BooleanField(default=False, verbose_name="Подтверждено")
    assistant_status = models.CharField(
        max_length=16,
        choices=ASSISTANT_STATUS_CHOICES,
        default=ASSISTANT_STATUS_UNKNOWN,
        verbose_name="Статус от ассистента",
    )
    assistant_comment = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name="Комментарий ассистента",
    )

    def __str__(self):
        return f"{self.athlete.person.surname} - {self.class_instance.id}"

    class Meta:
        verbose_name = "Запись на занятие"
        verbose_name_plural = "Записи на занятия"


class TrainingCamp(models.Model):
    """Модель «Спортивный сбор»"""

    start_date = models.DateField(verbose_name="Дата начала сбора")
    end_date = models.DateField(verbose_name="Дата окончания сбора")
    location = models.CharField(max_length=100, verbose_name="Место проведения")
    description = models.TextField(blank=True, null=True, verbose_name="Описание сбора")
    classes = models.ManyToManyField(
        Class, blank=True, related_name="camps", verbose_name="Занятия"
    )

    def __str__(self):
        return f"Сбор с {self.start_date} по {self.end_date}"

    class Meta:
        verbose_name = "Спортивный сбор"
        verbose_name_plural = "Спортивные сборы"


class CampEnrollment(models.Model):
    """Модель «Участие в сборах»"""

    athlete = models.ForeignKey(
        Athlete,
        on_delete=models.CASCADE,
        related_name="camp_enrollments",
        verbose_name="Спортсмен",
    )
    camp = models.ForeignKey(
        TrainingCamp,
        on_delete=models.CASCADE,
        related_name="enrollments",
        verbose_name="Сбор",
    )
    attendance_start = models.DateTimeField(verbose_name="Дата и время начала участия")
    attendance_end = models.DateTimeField(verbose_name="Дата и время окончания участия")

    def __str__(self):
        return f"{self.athlete.person.surname} - {self.camp.start_date}"

    class Meta:
        verbose_name = "Участие в сборах"
        verbose_name_plural = "Участия в сборах"


class Competition(models.Model):
    """Модель «Соревнование»"""

    TYPE_CHOICES = [
        ("sport", "Спортивные"),
        ("physical", "Физкультурные"),
    ]

    name = models.CharField(max_length=100, verbose_name="Название соревнования")
    date = models.DateField(verbose_name="Дата проведения", blank=True, null=True)
    start_date = models.DateField(verbose_name="Дата начала", blank=True, null=True)
    end_date = models.DateField(verbose_name="Дата окончания", blank=True, null=True)
    location = models.CharField(max_length=100, verbose_name="Место проведения")
    competition_type = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default="sport",
        verbose_name="Тип соревнований",
    )
    discipline = models.CharField(
        max_length=100, blank=True, null=True, verbose_name="Дисциплина"
    )
    description = models.TextField(
        blank=True, null=True, verbose_name="Описание соревнования"
    )
    birth_year_from = models.PositiveIntegerField(
        verbose_name="Год рождения (с)",
        help_text="Самый ранний год рождения участников (старшие). Обязательно.",
        default=default_birth_year_from,
    )
    birth_year_to = models.PositiveIntegerField(
        verbose_name="Год рождения (по)",
        help_text="Самый поздний допустимый год рождения (младшие). Оставьте пустым, если без верхней границы.",
        blank=True,
        null=True,
    )

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Соревнование"
        verbose_name_plural = "Соревнования"


class CompetitionApplicationLink(models.Model):
    """Публичная ссылка для подачи заявок на соревнование."""

    competition = models.OneToOneField(
        Competition,
        on_delete=models.CASCADE,
        related_name="application_link",
        verbose_name="Соревнование",
    )
    token = models.CharField(max_length=100, unique=True, verbose_name="Токен")
    expires_at = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name="Приём заявок до",
    )
    is_active = models.BooleanField(default=True, verbose_name="Активна")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Ссылка на заявку"
        verbose_name_plural = "Ссылки на заявки"

    def __str__(self):
        return f"{self.competition.name} ({self.token[:6]})"


class CompetitionApplication(models.Model):
    """Заявка пользователя на соревнование."""

    competition = models.ForeignKey(
        Competition,
        on_delete=models.CASCADE,
        related_name="applications",
        verbose_name="Соревнование",
    )
    user = models.ForeignKey(
        "users.User",
        on_delete=models.CASCADE,
        related_name="competition_applications",
        verbose_name="Пользователь",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    class Meta:
        verbose_name = "Заявка пользователя"
        verbose_name_plural = "Заявки пользователей"
        constraints = [
            models.UniqueConstraint(
                fields=("competition", "user"),
                name="school_competitionapplication_unique_competition_user",
            )
        ]

    def __str__(self):
        return f"{self.competition.name} ← {self.user.display_name()}"


class CompetitionEntry(models.Model):
    """Модель «Участие в соревнованиях»"""

    athlete = models.ForeignKey(
        Athlete,
        on_delete=models.CASCADE,
        related_name="competition_entries",
        verbose_name="Спортсмен",
    )
    competition = models.ForeignKey(
        Competition,
        on_delete=models.CASCADE,
        related_name="entries",
        verbose_name="Соревнование",
    )
    application = models.ForeignKey(
        CompetitionApplication,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="entries",
        verbose_name="Заявка пользователя",
    )
    result = models.CharField(max_length=100, verbose_name="Результат участия", blank=True, null=True)

    def __str__(self):
        return f"{self.athlete.person.surname} - {self.competition.name}"

    class Meta:
        verbose_name = "Участие в соревнованиях"
        verbose_name_plural = "Участия в соревнованиях"


class DocumentType(models.TextChoices):
    REGULATION = "regulation", "Регламент/Положение"
    SCHEDULE = "schedule", "Расписание/Распорядок"
    START_LIST = "start_list", "Стартовый лист"
    START_LIST_SECOND = "start_list_second", "Стартовый лист второй попытки"
    INTERMEDIATE_RESULTS = "intermediate_results", "Промежуточные результаты"
    PRELIM_RESULTS = "prelim_results", "Результаты предварительные"
    OFFICIAL_RESULTS = "official_results", "Результаты официальные"
    APPLICATION_FORM = "application_form", "Форма заявки"
    PARENT_CONSENT = "parent_consent", "Согласие родителей"
    MED_CERT = "medical_certificate", "Медицинская справка"
    INSURANCE = "insurance", "Страховка"
    OTHER = "other", "Прочее"
class Document(models.Model):
    """Базовый загружаемый файл."""

    file = models.FileField(upload_to="documents/%Y/%m/", max_length=255, verbose_name="Файл")
    original_name = models.CharField(max_length=255, verbose_name="Исходное имя")
    mime_type = models.CharField(max_length=100, blank=True, verbose_name="MIME-тип")
    size = models.PositiveIntegerField(default=0, verbose_name="Размер, байт")
    uploaded_by = models.ForeignKey(
        "users.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="Кем загружено",
        related_name="uploaded_documents",
    )
    description = models.CharField(max_length=255, blank=True, verbose_name="Описание")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Загружено")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    def save(self, *args, **kwargs):
        if self.file:
            self.original_name = self.original_name or getattr(self.file, "name", "")
            try:
                self.size = self.file.size
            except Exception:
                pass
        super().save(*args, **kwargs)

    def __str__(self):
        return self.original_name or "Документ"

    class Meta:
        verbose_name = "Документ"
        verbose_name_plural = "Документы"
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["updated_at"]),
        ]


class CompetitionDocument(models.Model):
    """Файлы, относящиеся к соревнованию в целом."""

    competition = models.ForeignKey(
        Competition,
        on_delete=models.CASCADE,
        related_name="documents",
        verbose_name="Соревнование",
    )
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="competition_links",
        verbose_name="Документ",
    )
    doc_type = models.CharField(
        max_length=50,
        choices=DocumentType.choices,
        verbose_name="Тип документа",
    )
    title = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Название/подпись",
        help_text="Отображается в списке документов",
    )
    is_public = models.BooleanField(default=False, verbose_name="Доступно участникам")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Добавлено")

    def __str__(self):
        return self.title or dict(DocumentType.choices).get(self.doc_type, "Документ")

    class Meta:
        verbose_name = "Документ соревнования"
        verbose_name_plural = "Документы соревнования"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["competition", "doc_type"]),
            models.Index(fields=["document"]),
        ]


class AthleteDocument(models.Model):
    """Личные документы спортсмена (база для будущего использования)."""

    athlete = models.ForeignKey(
        Athlete,
        on_delete=models.CASCADE,
        related_name="documents",
        verbose_name="Спортсмен",
    )
    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="athlete_links",
        verbose_name="Документ",
    )
    doc_type = models.CharField(max_length=50, choices=DocumentType.choices, verbose_name="Тип документа")
    issued_at = models.DateField(blank=True, null=True, verbose_name="Дата выдачи")
    valid_until = models.DateField(blank=True, null=True, verbose_name="Действителен до")
    is_default = models.BooleanField(
        default=False,
        verbose_name="Использовать по умолчанию",
        help_text="Будет подставляться в заявки, если тип совпадает",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Добавлено")

    def __str__(self):
        return f"{self.athlete} — {self.get_doc_type_display()}"

    class Meta:
        verbose_name = "Документ спортсмена"
        verbose_name_plural = "Документы спортсменов"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["athlete", "doc_type"]),
            models.Index(fields=["document"]),
        ]


class DocumentAIAnalysis(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает"
        OK = "ok", "Успешно"
        PARTIAL = "partial", "Частично"
        ERROR = "error", "Ошибка"
        FAILED_VALIDATION = "failed_validation", "Ошибка валидации"
        FAILED_OPENAI = "failed_openai", "Ошибка AI"
        FAILED_DOWNLOAD = "failed_download", "Ошибка загрузки документа"

    class DocType(models.TextChoices):
        COMPETITION_GENERAL = "competition_general", "Соревнование (общий)"
        ATHLETE_SPECIFIC = "athlete_specific", "Спортсмен (личный)"
        OTHER = "other", "Другое"

    class ErrorStage(models.TextChoices):
        DOWNLOAD = "download", "Загрузка"
        OPENAI = "openai", "AI"
        VALIDATION = "validation", "Валидация"

    document = models.OneToOneField(
        Document,
        on_delete=models.CASCADE,
        related_name="ai_analysis",
        verbose_name="Документ",
    )
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.PENDING,
        verbose_name="Статус анализа",
    )
    request_id = models.CharField(max_length=100, blank=True, verbose_name="Request ID")
    doc_type = models.CharField(
        max_length=32,
        choices=DocType.choices,
        blank=True,
        null=True,
        verbose_name="Тип документа AI",
    )
    confidence = models.FloatField(blank=True, null=True, verbose_name="Уверенность")
    title = models.CharField(max_length=255, blank=True, null=True, verbose_name="Заголовок")
    extracted = models.JSONField(default=dict, blank=True, verbose_name="Нормализованные данные")
    issues = models.JSONField(default=list, blank=True, verbose_name="Проблемы")
    raw_response = models.JSONField(default=dict, blank=True, verbose_name="Сырой ответ")
    error_stage = models.CharField(
        max_length=16,
        choices=ErrorStage.choices,
        blank=True,
        null=True,
        verbose_name="Стадия ошибки",
    )
    error_message = models.TextField(blank=True, null=True, verbose_name="Текст ошибки")
    source_persistent_url = models.URLField(max_length=1000, blank=True, verbose_name="Постоянный URL источника")
    source_signed_url_hash = models.CharField(
        max_length=64,
        blank=True,
        verbose_name="Hash signed URL",
    )
    is_analyzed_successfully = models.BooleanField(default=False, verbose_name="Успешно проанализирован")
    document_updated_at_snapshot = models.DateTimeField(
        blank=True,
        null=True,
        verbose_name="Снимок updated_at документа",
    )
    analyzed_at = models.DateTimeField(blank=True, null=True, verbose_name="Время анализа")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Создано")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="Обновлено")

    def __str__(self):
        return f"AI {self.document_id}: {self.status}"

    class Meta:
        verbose_name = "AI-анализ документа"
        verbose_name_plural = "AI-анализ документов"
        indexes = [
            models.Index(fields=["status"]),
            models.Index(fields=["doc_type"]),
            models.Index(fields=["is_analyzed_successfully"]),
            models.Index(fields=["analyzed_at"]),
            models.Index(fields=["document_updated_at_snapshot"]),
            models.Index(fields=["request_id"]),
        ]


class Club(models.Model):
    """Модель «Клуб»"""

    name = models.CharField(max_length=150, unique=True, verbose_name="Название клуба")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Клуб"
        verbose_name_plural = "Клубы"


class Family(models.Model):
    """Модель «Семья»"""

    STATUS_ACTIVE = "active"
    STATUS_ALUMNI = "alumni"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Действующий член клуба"),
        (STATUS_ALUMNI, "Бывший член клуба"),
    ]

    contact_person = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        related_name="families",
        verbose_name="Контактное лицо",
        blank=True,
        null=True,
    )
    family_name = models.CharField(
        max_length=100,
        blank=True,
        null=True,
        verbose_name="Фамилия семьи",
        help_text="Заполнится автоматически",
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_ACTIVE,
        verbose_name="Статус семьи",
    )
    comment = models.TextField(blank=True, null=True, verbose_name="Комментарий")

    def __str__(self):
        return self.family_name or f"Семья #{self.pk}"

    def save(self, *args, **kwargs):
        # Если фамилия семьи не указана, берем её из фамилии контактного лица
        if not self.family_name and self.contact_person:
            self.family_name = self.contact_person.surname
        super(Family, self).save(*args, **kwargs)

    class Meta:
        verbose_name = "Семья"
        verbose_name_plural = "Семьи"
        ordering = ["family_name"]


class FamilyMember(models.Model):
    """Модель «Член семьи»"""

    FAMILY_RELATION = [
        ("mother", "Мама"),
        ("father", "Папа"),
        ("son", "Сын"),
        ("daughter", "Дочь"),
        ("grandfather", "Дедушка"),
        ("grandmother", "Бабушка"),
        ("representative", "Представитель"),
        ("guardian", "Опекун"),
        ("grandson", "Внук"),
        ("granddaughter", "Внучка"),
    ]

    family = models.ForeignKey(
        Family, on_delete=models.CASCADE, related_name="members", verbose_name="Семья"
    )
    person = models.ForeignKey(
        Person,
        on_delete=models.SET_NULL,
        verbose_name="Человек",
        null=True,
        blank=True,
    )
    relation = models.CharField(
        max_length=50, choices=FAMILY_RELATION, verbose_name="Отношение"
    )

    def __str__(self):
        person_display = str(self.person) if self.person else "Удалённый участник"
        return f"{person_display} - {self.relation}"

    class Meta:
        verbose_name = "Член семьи"
        verbose_name_plural = "Члены семьи"


class FamilyAthleteProfile(models.Model):
    """Дополнительные настройки спортсмена внутри семьи."""

    family = models.ForeignKey(
        Family,
        on_delete=models.CASCADE,
        related_name="athlete_profiles",
    )
    athlete = models.ForeignKey(
        Athlete,
        on_delete=models.CASCADE,
        related_name="family_profiles",
    )
    contract_active = models.BooleanField(default=True, verbose_name="Договор активен")
    monthly_fee = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Оплата в месяц",
    )
    discount_type = models.CharField(
        max_length=20,
        choices=DiscountType.choices,
        default=DiscountType.NONE,
        verbose_name="Тип скидки",
    )
    discount_value = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Размер скидки",
    )
    current_month_paid = models.BooleanField(
        default=False,
        verbose_name="Оплата текущего месяца",
    )
    notes = models.TextField(blank=True, null=True, verbose_name="Комментарии")

    class Meta:
        unique_together = ("family", "athlete")
        ordering = ["athlete__person__surname"]
        verbose_name = "Настройки спортсмена семьи"
        verbose_name_plural = "Настройки спортсменов семьи"

    def __str__(self):
        return f"{self.family}: {self.athlete.person}"


class AthleteContract(models.Model):
    profile = models.ForeignKey(
        FamilyAthleteProfile,
        on_delete=models.CASCADE,
        related_name="contracts",
        verbose_name="Профиль спортсмена",
    )
    number = models.CharField(max_length=50, blank=True, verbose_name="Номер договора")
    issue_date = models.DateField(verbose_name="Дата оформления")
    start_date = models.DateField(verbose_name="Дата начала")
    end_date = models.DateField(verbose_name="Дата окончания")
    base_fee = models.DecimalField(max_digits=9, decimal_places=2, default=Decimal("12000.00"), verbose_name="Базовый платёж")
    discount_value = models.DecimalField(max_digits=7, decimal_places=2, default=Decimal("0.00"), verbose_name="Скидка")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start_date"]
        verbose_name = "Договор"
        verbose_name_plural = "Договоры"

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = self._generate_number()
        super().save(*args, **kwargs)

    def _generate_number(self) -> str:
        family = self.profile.family
        last_contract = AthleteContract.objects.filter(profile__family=family).order_by("-created_at").first()
        if not last_contract or not last_contract.number or not last_contract.number.isdigit():
            return "1"
        try:
            return str(int(last_contract.number) + 1)
        except ValueError:
            return f"{last_contract.number}-2"

    @property
    def amount_due(self) -> Decimal:
        return max(Decimal("0.00"), self.base_fee - self.discount_value)

    @property
    def status(self) -> str:
        today = timezone.now().date()
        if self.start_date <= today <= self.end_date:
            return "active"
        if today < self.start_date:
            return "pending"
        return "expired"

    def __str__(self):
        return f"Договор {self.number} ({self.profile.athlete.person})"


class FamilyService(models.Model):
    """Услуги и начисления для семьи."""

    family = models.ForeignKey(
        Family,
        on_delete=models.CASCADE,
        related_name="services",
    )
    profile = models.ForeignKey(
        FamilyAthleteProfile,
        on_delete=models.CASCADE,
        related_name="services",
        blank=True,
        null=True,
    )
    contract = models.ForeignKey(
        AthleteContract,
        on_delete=models.SET_NULL,
        related_name="services",
        blank=True,
        null=True,
    )
    name = models.CharField(max_length=150, verbose_name="Название услуги")
    service_type = models.CharField(
        max_length=30,
        choices=ServiceType.choices,
        default=ServiceType.MONTHLY,
        verbose_name="Тип услуги",
    )
    amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Сумма к оплате",
    )
    discount_type = models.CharField(
        max_length=20,
        choices=DiscountType.choices,
        default=DiscountType.NONE,
        verbose_name="Тип скидки",
    )
    discount_value = models.DecimalField(
        max_digits=7,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Размер скидки",
    )
    is_recurring = models.BooleanField(default=False, verbose_name="Повторяющаяся")
    is_closed = models.BooleanField(default=False, verbose_name="Закрыта")
    created_at = models.DateTimeField(auto_now_add=True)
    due_date = models.DateField(blank=True, null=True, verbose_name="Дата оплаты")
    notes = models.TextField(blank=True, null=True, verbose_name="Комментарии")

    class Meta:
        verbose_name = "Услуга семьи"
        verbose_name_plural = "Услуги семьи"
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.name} ({self.family})"


class FamilyPayment(models.Model):
    """Оплаты по услугам семьи."""

    service = models.ForeignKey(
        FamilyService,
        on_delete=models.CASCADE,
        related_name="payments",
    )
    amount = models.DecimalField(
        max_digits=9,
        decimal_places=2,
        default=Decimal("0.00"),
        verbose_name="Сумма платежа",
    )
    payment_type = models.CharField(
        max_length=20,
        choices=PaymentType.choices,
        default=PaymentType.FULL,
        verbose_name="Тип платежа",
    )
    paid_at = models.DateField(auto_now_add=True, verbose_name="Дата оплаты")
    note = models.TextField(blank=True, null=True, verbose_name="Комментарий")

    class Meta:
        verbose_name = "Оплата"
        verbose_name_plural = "Оплаты"
        ordering = ["-paid_at"]

    def __str__(self):
        return f"{self.service} — {self.amount}"
