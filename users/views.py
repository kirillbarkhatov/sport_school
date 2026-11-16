import logging
import re
import secrets

import httpx
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import RedirectView, TemplateView

from config.settings import (
    BOT_NAME,
    BOT_TOKEN,
    TELEGRAM_ADMIN_IDS,
    TELEGRAM_LOG_CHAT_ID,
)
from organizations.auth import consume_login_token
from organizations.permissions import has_site_access

from .models import User

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
            except TypeError, ValueError:
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
            if has_site_access(self.request.user):
                context["dashboard_url"] = reverse("school:index")
            else:
                context["dashboard_url"] = reverse("users:awaiting_approval")
            self._notify_debug(
                {
                    "authenticated": True,
                    "user_id": self.request.user.pk,
                    "tg_id": getattr(self.request.user, "tg_id", None),
                    "next_url": self.request.session.get("next_url"),
                }
            )
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
        self._notify_debug(
            {
                "authenticated": False,
                "generated_token": token,
                "session_token": self.request.session.get("telegram_token"),
                "next_url": self.request.session.get("next_url"),
                "telegram_link": context["telegram_link"],
                "is_competition_flow": context["is_competition_flow"],
            }
        )
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

    def get_context_data(self, **kwargs):
        """R3-4: организация по адресу, статус членства, «Вступить» для посетителя без членства.
        ORGS_LEGACY_APPROVAL_PAGE (до проверки очереди на проде) сохраняет прежний текст страницы."""
        from organizations.models import Membership
        from organizations.scoping import scope_organization

        context = super().get_context_data(**kwargs)
        organization = scope_organization()
        membership = (
            Membership.objects.filter(user=self.request.user, organization=organization).first()
            if organization
            else None
        )
        context.update(
            organization=organization,
            membership_status=membership.status if membership else "",
            membership_status_display=membership.get_status_display() if membership else "",
            legacy_approval_page=getattr(settings, "ORGS_LEGACY_APPROVAL_PAGE", True),
        )
        return context

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect("users:login_page")
        if has_site_access(request.user):
            return redirect("school:index")
        return super().dispatch(request, *args, **kwargs)


class TelegramCallbackView(View):
    """Обработка обратного вызова Telegram и завершение авторизации."""

    def get(self, request, token, *args, **kwargs):
        if not token:
            return HttpResponseBadRequest("Отсутствует токен авторизации")

        # R3-1: одноразовый вход через organizations.auth (тикет, окно xfer для второго браузера, срок жизни)
        user = consume_login_token(token)
        if user is None:
            logger.warning("Попытка входа с неверным, истёкшим или повторным токеном")  # части токена не пишем
            response = HttpResponse("Неверный токен или пользователь не найден", status=404)
            response._has_been_logged = True  # django.request иначе запишет путь с токеном («Not Found: …»)
            return response

        login(request, user)

        request.session.pop("telegram_token", None)

        messages.success(request, "Вы успешно вошли в систему.")

        logger.info(
            "Пользователь %s (tg_id=%s) авторизовался через Telegram",
            user.email or user.pk,
            user.tg_id,
        )
        send_admin_message(_format_user_login_message(user, request))

        raw_next = request.session.pop("next_url", None) or request.GET.get("next")
        # next проверяется на разрешённый хост ДО всех веток редиректа, в том числе для неподтверждённых
        next_url = (
            raw_next
            if raw_next
            and url_has_allowed_host_and_scheme(
                url=raw_next, allowed_hosts={request.get_host()}, require_https=request.is_secure()
            )
            else None
        )

        def _is_competition_apply(url: str | None) -> bool:
            if not url:
                return False
            return bool(re.search(r"/competitions/\d+/apply/", url))

        if not has_site_access(user):
            if _is_competition_apply(next_url):
                return redirect(next_url)
            send_admin_message(
                "🚦 Новый пользователь ожидает подтверждения:\n"
                f"Email: {user.email}\n"
                f"tg_id: {user.tg_id}\n"
                f"Имя: {user.get_full_name() or '-'}"
            )
            return redirect("users:awaiting_approval")

        if next_url:
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
    pass  # legacy stub, no longer used


class XferLoginView(View):
    pass  # legacy stub, no longer used
