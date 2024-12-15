import secrets

from django.contrib.auth import login
from django.http import HttpResponse
from django.shortcuts import redirect
from django.views import View
from django.views.generic import TemplateView, RedirectView
from rest_framework import viewsets

from .models import User
from config.settings import BOT_NAME


class UserViewSet(viewsets.ModelViewSet):
    """Вьюсет для Пользователя"""

    model = User
    queryset = User.objects.all()


class LoginPageView(TemplateView):
    template_name = "login.html"

    def get_context_data(self, **kwargs):
        """Генерация токена и создание ссылки для входа через Telegram."""
        context = super().get_context_data(**kwargs)
        if self.request.user.is_authenticated:
            # Если пользователь уже вошел, показать страницу приветствия
            context['user'] = self.request.user
            return context

        # Генерация уникального токена для сессии
        token = secrets.token_urlsafe(16)

        self.request.session['telegram_token'] = token
        telegram_bot_username = BOT_NAME  # Замените на имя вашего бота
        context['telegram_link'] = f"https://t.me/{telegram_bot_username}?start={token}"
        return context


class TelegramCallbackView(View):
    """Обработка обратного вызова Telegram и завершение авторизации."""
    def get(self, request, token, *args, **kwargs):
        try:
            # Проверяем, существует ли пользователь с указанным токеном
            user = User.objects.get(token=token)
        except User.DoesNotExist:
            return HttpResponse("Неверный токен или пользователь не найден", status=404)

        # Авторизуем пользователя
        login(request, user)

        # Очищаем токен после успешной авторизации
        user.token = None  # Теперь мы можем удалить токен
        user.save()

        # Перенаправляем на главную страницу или другую нужную страницу
        return redirect('users:login_page')  # Замените 'home' на вашу основную страницу
