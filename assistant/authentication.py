from __future__ import annotations

from typing import Optional

import jwt
from django.utils.translation import gettext_lazy as _
from rest_framework import authentication, exceptions

from .models import ServiceAccount


class ServiceAccountJWTAuthentication(authentication.BaseAuthentication):
    """JWT-аутентификация для сервисных аккаунтов."""

    www_authenticate_realm = "assistant"
    keyword = "Bearer"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).split()
        if not header:
            return None

        if header[0].lower() != self.keyword.lower().encode("utf-8"):
            return None

        if len(header) == 1:
            raise exceptions.AuthenticationFailed(_("Неверный формат заголовка Authorization."))
        if len(header) > 2:
            raise exceptions.AuthenticationFailed(_("Неверный формат заголовка Authorization."))

        token = header[1]
        if isinstance(token, bytes):
            token = token.decode("utf-8")

        account = self._resolve_account(token)
        payload = self._decode_token(token, account)
        request.service_account = account
        return account, payload

    def _resolve_account(self, token: str) -> ServiceAccount:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.InvalidTokenError as exc:
            raise exceptions.AuthenticationFailed(_("Некорректный JWT токен.")) from exc

        kid = header.get("kid")
        account: Optional[ServiceAccount] = None

        if kid:
            account = ServiceAccount.objects.filter(key=kid, is_active=True).first()

        if account:
            return account

        try:
            payload = jwt.decode(token, options={"verify_signature": False})
        except jwt.InvalidTokenError as exc:
            raise exceptions.AuthenticationFailed(_("Не удалось декодировать JWT токен.")) from exc

        subject = payload.get("sub")
        if not subject:
            raise exceptions.AuthenticationFailed(_("В JWT токене отсутствует sub."))

        account = ServiceAccount.objects.filter(slug=subject, is_active=True).first()
        if not account:
            raise exceptions.AuthenticationFailed(_("Сервисный аккаунт не найден или выключен."))
        return account

    def _decode_token(self, token: str, account: ServiceAccount) -> dict:
        options = {
            "verify_aud": bool(account.audience),
            "verify_iss": bool(account.issuer),
        }
        try:
            payload = jwt.decode(
                token,
                key=account.secret,
                algorithms=["HS256"],
                audience=account.audience or None,
                issuer=account.issuer or None,
                options=options,
            )
        except jwt.ExpiredSignatureError as exc:
            raise exceptions.AuthenticationFailed(_("Срок действия токена истёк.")) from exc
        except jwt.InvalidAudienceError as exc:
            raise exceptions.AuthenticationFailed(_("Неверная аудитория токена.")) from exc
        except jwt.InvalidIssuerError as exc:
            raise exceptions.AuthenticationFailed(_("Неверный издатель токена.")) from exc
        except jwt.InvalidTokenError as exc:
            raise exceptions.AuthenticationFailed(_("Невозможно подтвердить JWT токен.")) from exc
        return payload

    def authenticate_header(self, request):
        return f'{self.keyword} realm="{self.www_authenticate_realm}"'
