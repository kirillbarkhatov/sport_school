from django.db import models
from django.conf import settings


class Notification(models.Model):
    title = models.CharField(max_length=200, verbose_name="Заголовок")
    message = models.TextField(verbose_name="Сообщение")
    users = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, verbose_name="Получатели"
    )
    send_to_telegram = models.BooleanField(default=False, verbose_name="В телеграм")
    send_to_email = models.BooleanField(default=False, verbose_name="На почту")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Уведомление"
        verbose_name_plural = "Уведомления"

    def __str__(self):
        return self.title
