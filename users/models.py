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

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"