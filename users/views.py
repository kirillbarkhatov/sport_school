import logging
import secrets
import re

import httpx
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import TemplateView, RedirectView
from rest_framework import viewsets
from rest_framework.permissions import IsAuthenticated

from .models import User
from config.settings import BOT_NAME, BOT_TOKEN, TELEGRAM_LOG_CHAT_ID, TELEGRAM_ADMIN_IDS
from .services.xfer_tokens import issue_xfer_token, consume_xfer_token


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


def _format_user_roles(user: User) -> str:
    roles = []
    if user.is_superuser:
        roles.append("superuser")
    elif user.is_staff:
        roles.append("staff")
    if user.is_approved:
        roles.append("approved")
    else:
        roles.append("не подтверждён")
    return ", ".join(roles)


def _format_user_login_message(user: User, request) -> str:
    lines = [
        "✅ Успешный вход в систему",
        "",
        f"Пользователь: {user.display_name()}",
        f"Email: {user.email or '—'}",
    ]
    if user.tg_username or user.tg_id:
        username = f"@{user.tg_username}" if user.tg_username else "—"
        tg_id = user.tg_id or "—"
        lines.append(f"Telegram: {username} (id: {tg_id})")
    lines.append(f"Роли: {_format_user_roles(user)}")
    lines.append(f"Время: {timezone.localtime().strftime('%d.%m.%Y %H:%M:%S')}")
    ip_address = request.META.get("REMOTE_ADDR")
    if ip_address:
        lines.append(f"IP: {ip_address}")
    return "\n".join(lines)


class UserViewSet(viewsets.ModelViewSet):
    """Вьюсет для Пользователя"""

    model = User
    queryset = User.objects.all()
    permission_classes = [IsAuthenticated]


class LoginPageView(TemplateView):
    template_name = "users/login.html"

    @staticmethod
    def _extract_competition_id(next_url: str | None) -> int | None:
        if not next_url:
            return None
        match = re.search(r"/competitions/(?P<pk>\d+)/apply/", next_url)
        if match:
            try:
                return int(match.group("pk"))
            except (TypeError, ValueError):
                return None
        return None

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
        next_url = self.request.session.get("next_url")
        comp_id = self._extract_competition_id(next_url)
        if comp_id:
            start_payload = f"comp_{comp_id}_{token}"
            context["is_competition_flow"] = True
            context["competition_id"] = comp_id
        else:
            start_payload = f"auth_{token}"
            context["is_competition_flow"] = False
            context["competition_id"] = None

        context["telegram_link"] = f"https://t.me/{bot_name}?start={start_payload}"
        context["telegram_bot_name"] = bot_name
        self._notify_debug({
            "authenticated": False,
            "generated_token": token,
            "session_token": self.request.session.get("telegram_token"),
            "next_url": self.request.session.get("next_url"),
            "telegram_link": context["telegram_link"],
            "is_competition_flow": context["is_competition_flow"],
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

        logger.info("Login page opened: %s", info)


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
        send_admin_message(_format_user_login_message(user, request))

        next_url = request.session.pop("next_url", None)
        def _is_competition_apply(url: str | None) -> bool:
            if not url:
                return False
            return bool(re.search(r"/competitions/\d+/apply/", url))

        if not user.is_approved and not (user.is_staff or user.is_superuser):
            if _is_competition_apply(next_url):
                return redirect(next_url)
            send_admin_message(
                "🚦 Новый пользователь ожидает подтверждения:\n"
                f"Email: {user.email}\n"
                f"tg_id: {user.tg_id}\n"
                f"Имя: {user.get_full_name() or '-'}"
            )
            return redirect("users:awaiting_approval")

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


class IssueXferTokenView(LoginRequiredMixin, View):
    """Выдать одноразовую ссылку для переноса сессии во внешний браузер."""

    def post(self, request, *args, **kwargs):
        next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or reverse("school:index")
        if not url_has_allowed_host_and_scheme(
            url=next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            next_url = reverse("school:index")

        token = issue_xfer_token(
            user_id=request.user.id,
            next_url=next_url,
            ip=request.META.get("REMOTE_ADDR"),
            ua=request.META.get("HTTP_USER_AGENT"),
        )
        absolute = request.build_absolute_uri(
            reverse("users:xfer_login", kwargs={"token": token})
        )
        return JsonResponse({"url": absolute})


class XferLoginView(View):
    """Поглощает xfer-токен, логинит и редиректит на нужную страницу."""

    def get(self, request, token, *args, **kwargs):
        payload, error = consume_xfer_token(token)
        if not payload or error:
            return HttpResponse(
                "Ссылка устарела или недействительна. Запросите новую в приложении.",
                status=410,
            )

        try:
            user = User.objects.get(pk=payload.user_id)
        except User.DoesNotExist:
            return HttpResponse("Пользователь не найден.", status=404)

        login(request, user)
        next_url = payload.next_url or reverse("school:index")
        if not url_has_allowed_host_and_scheme(
            url=next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ):
            next_url = reverse("school:index")
        return redirect(next_url)
