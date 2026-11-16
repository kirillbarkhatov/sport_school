"""Единый вход и членство (этап 4, R3-1, R3-3, R3-4).

- Способы входа (AuthIdentity): Telegram ID и email учётной записи платформы. Учётная запись создаётся на
  уровне платформы; выбор организации членства не создаёт.
- Одноразовый вход через бота (LoginTicket): страница входа выдаёт токен в сессии, бот привязывает его к
  пользователю (bind_login_token), ссылка /telegram-callback/<token>/ его гасит (consume_login_token).
  После первого входа действует окно xfer для второго браузера (XFER_TOKEN_POST_USE_TTL_SECONDS, как раньше);
  повтор после окна, истёкший (ORGS_LOGIN_TOKEN_TTL_SECONDS) и неизвестный токен сессии не дают.
  Вход на адресе самой организации (same-domain). Междоменные коды (/auth/handoff/, ORGS_CENTRAL_LOGIN_HOST)
  отложены: пилот на одном хосте на организацию.
- Организация в боте: /start <slug> или org_<slug>, выбор при нескольких активных членствах; выбранная
  организация проверяется на каждом обновлении (активна, есть активное членство).
- Связь с человеком по организации: во второй организации глобальный User.person не меняется, человек
  хранится в Membership.person.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import AuditLog, AuthIdentity, Invitation, LoginTicket, Membership, Organization, OrgRole, RoleAssignment

AUTOGEN_EMAIL_SUFFIX = "@autogen.local"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# --- Способы входа ------------------------------------------------------------------------------------------


def sync_identities(user) -> None:
    """Telegram ID и настоящий email учётной записи → AuthIdentity (идемпотентно, без перехвата чужих)."""
    wanted = []
    if user.tg_id:
        wanted.append((AuthIdentity.Kind.TELEGRAM, str(user.tg_id)))
    email = (user.email or "").strip().lower()
    if email and not email.endswith(AUTOGEN_EMAIL_SUFFIX):
        wanted.append((AuthIdentity.Kind.EMAIL, email))
    for kind, value in wanted:
        existing = AuthIdentity.objects.filter(kind=kind, value=value).first()
        if existing is None:
            try:
                with transaction.atomic():
                    AuthIdentity.objects.create(user=user, kind=kind, value=value)
            except IntegrityError:
                pass
    # сменился Telegram ID или email — старые способы этой учётной записи снимаются
    for identity in AuthIdentity.objects.filter(user=user):
        if (identity.kind, identity.value) not in wanted:
            identity.delete()


def user_by_telegram(tg_id):
    from django.contrib.auth import get_user_model

    identity = (
        AuthIdentity.objects.select_related("user").filter(kind=AuthIdentity.Kind.TELEGRAM, value=str(tg_id)).first()
    )
    if identity is not None:
        return identity.user
    return get_user_model().objects.filter(tg_id=tg_id).first()


# --- Одноразовый вход ---------------------------------------------------------------------------------------


def new_token() -> str:
    return secrets.token_urlsafe(32)


def _ttl() -> timedelta:
    return timedelta(seconds=int(getattr(settings, "ORGS_LOGIN_TOKEN_TTL_SECONDS", 1800)))


def _xfer_window() -> timedelta:
    return timedelta(seconds=int(getattr(settings, "XFER_TOKEN_POST_USE_TTL_SECONDS", 60)))


def bind_login_token(user, token: str) -> None:
    """Бот получил /start auth_<token>: токен закрепляется за пользователем на ORGS_LOGIN_TOKEN_TTL_SECONDS."""
    if not token:
        return
    LoginTicket.objects.update_or_create(
        token_hash=_hash(token),
        defaults={"user": user, "expires_at": timezone.now() + _ttl(), "used_at": None},
    )


def consume_login_token(token: str):
    """Пользователь по токену входа или None (неизвестный, истёкший, повтор после окна xfer)."""
    if not token:
        return None
    now = timezone.now()
    with transaction.atomic():
        ticket = (
            LoginTicket.objects.select_for_update(of=("self",))
            .select_related("user")
            .filter(token_hash=_hash(token))
            .first()
        )
        if ticket is None:
            return _consume_legacy_token(token, now)
        if ticket.used_at is None:
            if ticket.expires_at < now:
                return None
            ticket.used_at = now
            ticket.save(update_fields=["used_at"])
            get_user_model().objects.filter(pk=ticket.user_id, token=token).update(token=None)
            return ticket.user
        if now - ticket.used_at <= _xfer_window():
            return ticket.user  # xfer: второй браузер в течение окна — как раньше
        return None


def _consume_legacy_token(token: str, now):
    """Токен, привязанный ботом до выкладки R3 (User.token без тикета): один раз, затем окно xfer."""
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(token=token).first()
    if user is None:
        return None
    LoginTicket.objects.create(token_hash=_hash(token), user=user, expires_at=now, used_at=now)
    get_user_model().objects.filter(pk=user.pk).update(token=None)
    return user


# --- Организация в боте ------------------------------------------------------------------------------------


def active_memberships(user):
    return (
        Membership.objects.filter(user=user, status=Membership.Status.ACTIVE, organization__status="active")
        .select_related("organization")
        .order_by("organization__name")
    )


def organization_by_slug(slug: str):
    return Organization.objects.filter(slug=slug, status=Organization.Status.ACTIVE).first() if slug else None


def can_select(user, organization) -> bool:
    """Выбрать организацию в боте можно только активную и только с активным членством."""
    return bool(
        organization is not None
        and organization.status == Organization.Status.ACTIVE
        and active_memberships(user).filter(organization=organization).exists()
    )


def resolve_bot_organization(user, selected_id=None):
    """Организация обработчиков бота для пользователя: выбранная (если ещё допустима) → единственное активное
    членство → организация по умолчанию (посетитель). Несколько членств без выбора → None (нужно выбрать)."""
    from .context import default_organization

    if user is None:
        return default_organization()
    if selected_id:
        organization = Organization.objects.filter(pk=selected_id).first()
        if can_select(user, organization):
            return organization
    memberships = list(active_memberships(user))
    if len(memberships) == 1:
        return memberships[0].organization
    if not memberships:
        return default_organization()
    return None


# --- Человек организации -----------------------------------------------------------------------------------


def person_for(user, organization):
    """Человек пользователя в организации: Membership.person или глобальный User.person той же организации."""
    if user is None or organization is None:
        return None
    membership = Membership.objects.filter(user=user, organization=organization).select_related("person").first()
    if membership is not None and membership.person_id:
        return membership.person
    person = getattr(user, "person", None)
    if person is not None and person.organization_id in (None, organization.pk):
        return person
    return None


def link_user_person(user, person) -> list[str]:
    """Связать пользователя с человеком организации person. Возвращает поля User для сохранения.

    Глобальный User.person меняется, только если он пуст или относится к той же организации; иначе связь
    хранится в членстве этой организации (прежняя организация не теряет своего человека)."""
    organization_id = person.organization_id
    current = getattr(user, "person", None)
    Membership.objects.filter(user=user, organization_id=organization_id).update(person=person)
    if current is None or current.organization_id in (None, organization_id):
        user.person = person
        return ["person"]
    return []


# --- Заявка на вступление и приглашения --------------------------------------------------------------------


def request_join(user, organization):
    """Посетитель нажал «Вступить»: заявка (PENDING) в очередь организации. Существующее членство не меняется."""
    membership, created = Membership.objects.get_or_create(
        user=user, organization=organization, defaults={"status": Membership.Status.PENDING}
    )
    if created:
        AuditLog.objects.create(
            organization=organization,
            actor=user,
            action="join_requested",
            target_type="membership",
            target_id=str(membership.pk),
        )
    return membership, created


def create_invitation(organization, role_code: str | None, actor, days: int = 7):
    token = new_token()
    role = OrgRole.objects.filter(code=role_code, organization__isnull=True).first() if role_code else None
    invitation = Invitation.objects.create(
        organization=organization,
        role=role,
        token_hash=_hash(token),
        created_by=actor,
        expires_at=timezone.now() + timedelta(days=days),
    )
    AuditLog.objects.create(
        organization=organization,
        actor=actor,
        action="invitation_created",
        target_type="invitation",
        target_id=str(invitation.pk),
        payload={"role": role_code or ""},
    )
    return invitation, token


def accept_invitation(token: str, user):
    """Принять приглашение: членство ACTIVE (+ роль). Повтор, истечение, отзыв, чужая ссылка — None."""
    now = timezone.now()
    with transaction.atomic():
        invitation = (
            Invitation.objects.select_for_update(of=("self",))  # роль — nullable JOIN, блокируем только приглашение
            .select_related("organization", "role")
            .filter(token_hash=_hash(token or ""))
            .first()
        )
        if (
            invitation is None
            or invitation.revoked
            or invitation.accepted_at is not None
            or invitation.expires_at < now
            or invitation.organization.status != Organization.Status.ACTIVE
        ):
            return None
        membership, _ = Membership.objects.get_or_create(user=user, organization=invitation.organization)
        membership.status = Membership.Status.ACTIVE
        membership.approved_at = now
        membership.approved_by = invitation.created_by
        membership.save(update_fields=["status", "approved_at", "approved_by"])
        if invitation.role_id:
            RoleAssignment.objects.get_or_create(membership=membership, role=invitation.role)
        invitation.accepted_by, invitation.accepted_at = user, now
        invitation.save(update_fields=["accepted_by", "accepted_at"])
        AuditLog.objects.create(
            organization=invitation.organization,
            actor=user,
            action="invitation_accepted",
            target_type="membership",
            target_id=str(membership.pk),
        )
        return membership


def unlink_user_person(user, organization=None) -> list[str]:
    """Снять связь с человеком в организации. Глобальный User.person снимается, только если он этой организации."""
    from .scoping import scope_organization

    organization = organization or scope_organization()
    if organization is None:
        return []
    Membership.objects.filter(user=user, organization=organization).update(person=None)
    current = getattr(user, "person", None)
    if current is not None and current.organization_id in (None, organization.pk):
        user.person = None
        return ["person"]
    return []
