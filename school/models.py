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
    date_of_birth = models.DateField(default="1970-01-01", verbose_name="Дата рождения")
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
    comment = models.TextField(blank=True, null=True, verbose_name="Комментарий")
    gender = models.CharField(max_length=6, choices=GENDER_CHOICES, verbose_name="Пол")

    def __str__(self):
        return f"{self.surname} {self.name} - {self.date_of_birth}"

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

    person = models.OneToOneField(
        Person, on_delete=models.CASCADE, verbose_name="Человек"
    )
    level = models.CharField(
        max_length=50,
        choices=LEVEL_CHOICES,
        verbose_name="Уровень подготовки (Первый сезон)",
    )
    rank = models.CharField(max_length=50, blank=True, null=True, verbose_name="Разряд")
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
        Group, on_delete=models.CASCADE, related_name="classes", verbose_name="Группа"
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

    def __str__(self):
        local_dt = timezone.localtime(self.date)
        main_part = f"{self.get_training_type_display()} · {local_dt:%d.%m %H:%M}"
        return f"{self.group.name}: {main_part}"

    class Meta:
        verbose_name = "Занятие"
        verbose_name_plural = "Занятия"

    def save(self, *args, **kwargs):
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

    name = models.CharField(max_length=100, verbose_name="Название соревнования")
    date = models.DateField(verbose_name="Дата проведения")
    location = models.CharField(max_length=100, verbose_name="Место проведения")
    description = models.TextField(
        blank=True, null=True, verbose_name="Описание соревнования"
    )

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Соревнование"
        verbose_name_plural = "Соревнования"


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
    result = models.CharField(max_length=100, verbose_name="Результат участия")

    def __str__(self):
        return f"{self.athlete.person.surname} - {self.competition.name}"

    class Meta:
        verbose_name = "Участие в соревнованиях"
        verbose_name_plural = "Участия в соревнованиях"


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
        on_delete=models.CASCADE,
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
        ("mother", "Мать"),
        ("father", "Отец"),
        ("son", "Сын"),
        ("daughter", "Дочь"),
        ("grandfather", "Дед"),
        ("grandmother", "Бабушка"),
        ("representative", "Представитель"),
        ("guardian", "Опекун"),
        ("grandson", "Внук"),
        ("granddaughter", "Внучка"),
    ]

    family = models.ForeignKey(
        Family, on_delete=models.CASCADE, related_name="members", verbose_name="Семья"
    )
    person = models.ForeignKey(Person, on_delete=models.CASCADE, verbose_name="Человек")
    relation = models.CharField(
        max_length=50, choices=FAMILY_RELATION, verbose_name="Отношение"
    )

    def __str__(self):
        return f"{self.person} - {self.relation}"

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
