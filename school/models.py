from django.db import models


# всё из чат-гпт, проверить
class Person(models.Model):
    """Модель «Человек»"""

    GENDER_CHOICES = [
        ("male", "Мужской"),
        ("female", "Женский"),
        ("other", "Другой"),
    ]

    name = models.CharField(max_length=100, verbose_name="Имя")
    surname = models.CharField(max_length=100, verbose_name="Фамилия")
    middlename = models.CharField(max_length=100, verbose_name="Отчество")
    date_of_birth = models.DateField(verbose_name="Дата рождения")
    email = models.EmailField(verbose_name="Электронная почта")
    phone = models.CharField(max_length=15, verbose_name="Телефон")
    telegram = models.CharField(max_length=100, verbose_name="Telegram")
    comment = models.TextField(blank=True, verbose_name="Комментарий")
    gender = models.CharField(max_length=6, choices=GENDER_CHOICES, verbose_name="Пол")

    def __str__(self):
        return f"{self.surname} {self.name} {self.middlename}"

    class Meta:
        verbose_name = "Человек"
        verbose_name_plural = "Люди"


class Athlete(models.Model):
    """Модель «Спортсмен»"""

    person = models.OneToOneField(Person, on_delete=models.CASCADE, verbose_name="Человек")
    level = models.CharField(max_length=50, verbose_name="Уровень подготовки")
    rank = models.CharField(max_length=50, verbose_name="Разряд")
    medical_certificate = models.CharField(max_length=100, verbose_name="Справка-допуск")
    comment = models.TextField(blank=True, verbose_name="Комментарий")

    def __str__(self):
        return f"{self.person.surname} - {self.level}"

    class Meta:
        verbose_name = "Спортсмен"
        verbose_name_plural = "Спортсмены"


class Coach(models.Model):
    """Модель «Тренер»"""

    person = models.OneToOneField(Person, on_delete=models.CASCADE, verbose_name="Человек")
    specialization = models.CharField(max_length=100, verbose_name="Специализация")

    def __str__(self):
        return f"{self.person.surname} - {self.specialization}"

    class Meta:
        verbose_name = "Тренер"
        verbose_name_plural = "Тренеры"


class PotentialClient(models.Model):
    """Модель «Потенциальный клиент»"""

    person = models.OneToOneField(Person, on_delete=models.CASCADE, verbose_name="Человек")
    interested_in = models.TextField(verbose_name="Интересующие услуги")
    source = models.CharField(max_length=100, verbose_name="Откуда узнал")
    trial_lesson = models.BooleanField(default=False, verbose_name="Запись на пробное занятие")
    first_month_paid = models.BooleanField(default=False, verbose_name="Оплатил первый месяц")
    comments = models.TextField(blank=True, verbose_name="Комментарий")

    def __str__(self):
        return f"{self.person.surname} - Потенциальный клиент"

    class Meta:
        verbose_name = "Потенциальный клиент"
        verbose_name_plural = "Потенциальные клиенты"


class Group(models.Model):
    """Модель «Группа»"""

    name = models.CharField(max_length=100, verbose_name="Название группы")
    level = models.CharField(max_length=50, verbose_name="Уровень подготовки")
    coaches = models.ManyToManyField(Coach, related_name="groups", verbose_name="Тренеры")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Группа"
        verbose_name_plural = "Группы"


class GroupEnrollment(models.Model):
    """Модель «Запись на группу»"""

    athlete = models.ForeignKey(Athlete, on_delete=models.CASCADE, related_name="group_enrollments",
                                verbose_name="Спортсмен")
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="enrollments", verbose_name="Группа")

    def __str__(self):
        return f"{self.athlete.person.surname} - {self.group.name}"

    class Meta:
        verbose_name = "Запись на группу"
        verbose_name_plural = "Записи на группы"


class Class(models.Model):
    """Модель «Занятие»"""

    TYPE_CHOICES = [
        ("regular", "Регулярное"),
        ("individual", "Индивидуальное"),
        ("camp", "В ходе сбора"),
    ]

    date = models.DateTimeField(verbose_name="Дата и время занятия")
    duration = models.IntegerField(verbose_name="Продолжительность занятия (мин.)")
    location = models.CharField(max_length=100, verbose_name="Место проведения")
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="classes", verbose_name="Группа")
    type = models.CharField(max_length=10, choices=TYPE_CHOICES, verbose_name="Тип занятия")
    is_during_camp = models.BooleanField(default=False, verbose_name="Проходит ли занятие в ходе сбора")

    def __str__(self):
        return f"{self.get_type_display()} - {self.date}"

    class Meta:
        verbose_name = "Занятие"
        verbose_name_plural = "Занятия"


class ClassEnrollment(models.Model):
    """Модель «Запись на занятие»"""

    athlete = models.ForeignKey(Athlete, on_delete=models.CASCADE, related_name="class_enrollments",
                                verbose_name="Спортсмен")
    class_instance = models.ForeignKey(Class, on_delete=models.CASCADE, related_name="enrollments",
                                       verbose_name="Занятие")

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
    description = models.TextField(blank=True, verbose_name="Описание сбора")
    classes = models.ManyToManyField(Class, related_name="camps", verbose_name="Занятия")

    def __str__(self):
        return f"Сбор с {self.start_date} по {self.end_date}"

    class Meta:
        verbose_name = "Спортивный сбор"
        verbose_name_plural = "Спортивные сборы"


class CampEnrollment(models.Model):
    """Модель «Участие в сборах»"""

    athlete = models.ForeignKey(Athlete, on_delete=models.CASCADE, related_name="camp_enrollments",
                                verbose_name="Спортсмен")
    camp = models.ForeignKey(TrainingCamp, on_delete=models.CASCADE, related_name="enrollments", verbose_name="Сбор")
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
    description = models.TextField(blank=True, verbose_name="Описание соревнования")

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Соревнование"
        verbose_name_plural = "Соревнования"


class CompetitionEntry(models.Model):
    """Модель «Участие в соревнованиях»"""

    athlete = models.ForeignKey(Athlete, on_delete=models.CASCADE, related_name="competition_entries",
                                verbose_name="Спортсмен")
    competition = models.ForeignKey(Competition, on_delete=models.CASCADE, related_name="entries",
                                    verbose_name="Соревнование")
    result = models.CharField(max_length=100, verbose_name="Результат участия")

    def __str__(self):
        return f"{self.athlete.person.surname} - {self.competition.name}"

    class Meta:
        verbose_name = "Участие в соревнованиях"
        verbose_name_plural = "Участия в соревнованиях"


class Family(models.Model):
    """Модель «Семья»"""

    family_name = models.CharField(max_length=100, verbose_name="Фамилия семьи")
    contact_person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name="families",
                                       verbose_name="Контактное лицо")
    comment = models.TextField(blank=True, verbose_name="Комментарий")

    def __str__(self):
        return self.family_name

    class Meta:
        verbose_name = "Семья"
        verbose_name_plural = "Семьи"


class FamilyMember(models.Model):
    """Модель «Член семьи»"""

    family = models.ForeignKey(Family, on_delete=models.CASCADE, related_name="members", verbose_name="Семья")
    person = models.ForeignKey(Person, on_delete=models.CASCADE, verbose_name="Человек")
    relation = models.CharField(max_length=50, verbose_name="Отношение")

    def __str__(self):
        return f"{self.person} - {self.relation}"

    class Meta:
        verbose_name = "Член семьи"
        verbose_name_plural = "Члены семьи"
