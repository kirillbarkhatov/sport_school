import logging
import secrets

from django.contrib import messages
from django.contrib.auth import login, logout
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import TemplateView, RedirectView
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from .models import User
from config.settings import BOT_NAME


logger = logging.getLogger("auth.telegram")


class UserViewSet(viewsets.ModelViewSet):
    """Вьюсет для Пользователя"""

    model = User
    queryset = User.objects.all()
    permission_classes = [IsAuthenticated]


class LoginPageView(TemplateView):
    template_name = "users/login.html"

    def get(self, request, *args, **kwargs):
        """Сохраняем целевую страницу перед отображением формы входа."""
        next_url = request.GET.get("next")
        if next_url and url_has_allowed_host_and_scheme(
            url=next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            request.session["next_url"] = next_url
        elif next_url:
            request.session.pop("next_url", None)
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        """Генерация токена и подготовка ссылки для входа через Telegram."""
        context = super().get_context_data(**kwargs)

        if self.request.user.is_authenticated:
            context["user"] = self.request.user
            context["dashboard_url"] = reverse("school:index")
            return context

        token = self._issue_session_token()
        bot_name = BOT_NAME or "your_bot"

        context["telegram_link"] = f"https://t.me/{bot_name}?start={token}"
        context["telegram_bot_name"] = bot_name
        return context

    def _issue_session_token(self) -> str:
        """Сохраняем уникальный токен в сессии и возвращаем его."""
        while True:
            token = secrets.token_urlsafe(32)
            if not User.objects.filter(token=token).exists():
                break

        self.request.session["telegram_token"] = token
        self.request.session.modified = True
        return token


class TelegramCallbackView(View):
    """Обработка обратного вызова Telegram и завершение авторизации."""

    def get(self, request, token, *args, **kwargs):
        if not token:
            return HttpResponseBadRequest("Отсутствует токен авторизации")

        try:
            # Проверяем, существует ли пользователь с указанным токеном
            user = User.objects.get(token=token)
        except User.DoesNotExist:
            logger.warning(
                "Попытка входа с неверным токеном (окончание %s)",
                token[-6:] if token else "unknown",
            )
            return HttpResponse("Неверный токен или пользователь не найден", status=404)

        # Авторизуем пользователя
        login(request, user)

        # Очищаем токен после успешной авторизации
        user.token = None
        user.save(update_fields=["token"])

        request.session.pop("telegram_token", None)

        messages.success(request, "Вы успешно вошли в систему.")

        logger.info(
            "Пользователь %s (tg_id=%s) авторизовался через Telegram",
            user.email or user.pk,
            user.tg_id,
        )

        next_url = request.session.pop("next_url", None)
        if next_url and url_has_allowed_host_and_scheme(
            url=next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            return redirect(next_url)

        # Перенаправляем на главную страницу или другую нужную страницу
        return redirect("school:index")


class LogoutView(RedirectView):
    """Завершение сессии пользователя."""

    pattern_name = "users:login_page"

    def get_redirect_url(self, *args, **kwargs):
        request = self.request
        user_identifier = None
        if getattr(request, "user", None) and request.user.is_authenticated:
            user_identifier = request.user.email or request.user.pk
        logout(request)
        messages.info(request, "Вы вышли из системы.")
        if user_identifier:
            logger.info("Пользователь %s вышел из системы", user_identifier)
        return super().get_redirect_url(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        """Поддержка выхода по POST запросу."""
        return self.get(request, *args, **kwargs)
