from django.contrib import admin
from django.apps import apps

# Получаем все модели текущего приложения
app = apps.get_app_config('users')  # Замените на имя вашего приложения

for model in app.models.values():
    admin.site.register(model)