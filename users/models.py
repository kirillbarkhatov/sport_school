from django.contrib.auth.models import AbstractUser
from django.db import models

class User(AbstractUser):
    """Модель кастомного пользователя"""

    username = None
    email = models.EmailField(unique=True, blank=True, null=True, verbose_name="Почта")
    phone = models.CharField(
        max_length=15, blank=True, null=True, verbose_name="Телефон"
    )
    tg_id = models.PositiveBigIntegerField(unique=True, blank=True, null=True, verbose_name="ID телеграмм аккаунта")
    tg_first_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="Имя в телеграмме")
    tg_last_name = models.CharField(max_length=100, blank=True, null=True, verbose_name="Фамилия в телеграмме")
    tg_username = models.CharField(max_length=100, blank=True, null=True, verbose_name="Тег в телеграмме")
    city = models.CharField(max_length=30, blank=True, null=True, verbose_name="Город")
    avatar = models.ImageField(
        upload_to="users/avatar", blank=True, null=True, verbose_name="Аватар"
    )
    token = models.CharField(
        max_length=100, blank=True, null=True, verbose_name="Токен"
    )
    is_approved = models.BooleanField(default=False, verbose_name="Пользователь подтверждён")
    person = models.ForeignKey(
        "school.Person",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="linked_users",
        verbose_name="Персона",
    )
    family = models.ForeignKey(
        "school.Family",
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name="users",
        verbose_name="Семья",
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def get_accessible_family_ids(self):
        if self.is_staff or self.is_superuser:
            from school.models import Family
            return list(Family.objects.values_list("id", flat=True))

        ids = []
        if self.family_id:
            ids.append(self.family_id)
        if self.person_id:
            from school.models import FamilyMember
            linked = FamilyMember.objects.filter(person_id=self.person_id).values_list("family_id", flat=True)
            ids.extend(linked)
        return list(set(ids))

    def display_name(self):
        return self.get_full_name() or self.email or str(self.pk)
