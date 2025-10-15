import logging
import secrets

import httpx
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import TemplateView, RedirectView
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from .models import User
from config.settings import BOT_NAME, BOT_TOKEN, TELEGRAM_LOG_CHAT_ID, TELEGRAM_ADMIN_IDS


logger = logging.getLogger("auth.telegram")


def send_admin_message(text: str) -> None:
    targets = [chat_id for chat_id in TELEGRAM_ADMIN_IDS if chat_id]
    if not targets and TELEGRAM_LOG_CHAT_ID:
        targets = [TELEGRAM_LOG_CHAT_ID]

    if not BOT_TOKEN or not targets:
        return
    try:
        for chat_id in targets:
            httpx.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": text,
                },
                timeout=5,
            )
    except Exception as exc:
        logger.warning("Не удалось отправить служебное сообщение: %s", exc)


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
            if self.request.user.is_staff or self.request.user.is_approved:
                context["dashboard_url"] = reverse("school:index")
            else:
                context["dashboard_url"] = reverse("users:awaiting_approval")
            self._notify_debug({
                "authenticated": True,
                "user_id": self.request.user.pk,
                "tg_id": getattr(self.request.user, "tg_id", None),
                "next_url": self.request.session.get("next_url"),
            })
            return context

        token = self._issue_session_token()
        bot_name = BOT_NAME or "your_bot"

        context["telegram_link"] = f"https://t.me/{bot_name}?start={token}"
        context["telegram_bot_name"] = bot_name
        self._notify_debug({
            "authenticated": False,
            "generated_token": token,
            "session_token": self.request.session.get("telegram_token"),
            "next_url": self.request.session.get("next_url"),
            "telegram_link": context["telegram_link"],
        })
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

    def _notify_debug(self, info: dict) -> None:
        print(f"[LOGIN DEBUG] {info}")

        message_lines = [
            "🔍 Login page opened",
            "",
        ]
        message_lines.extend(f"{key}: {value}" for key, value in info.items())
        send_admin_message("\n".join(message_lines))


class AwaitingApprovalView(LoginRequiredMixin, TemplateView):
    template_name = "users/awaiting_approval.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("users:login_page")
        if request.user.is_staff or request.user.is_superuser:
            return redirect("school:index")
        if request.user.is_approved:
            return redirect("school:index")
        return super().dispatch(request, *args, **kwargs)


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

        if not user.is_approved and not (user.is_staff or user.is_superuser):
            send_admin_message(
                "🚦 Новый пользователь ожидает подтверждения:\n"
                f"Email: {user.email}\n"
                f"tg_id: {user.tg_id}\n"
                f"Имя: {user.get_full_name() or '-'}"
            )
            return redirect("users:awaiting_approval")

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
