# ruff: noqa: F811 — фикстуры двух организаций импортируются из test_scoping
"""T09 (R3-1…R3-5): единый вход, бот и организация, консоли, посетители, проверка релиза."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from asgiref.sync import async_to_sync
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from organizations import context
from organizations.auth import (
    bind_login_token,
    link_user_person,
    person_for,
    resolve_bot_organization,
    sync_identities,
    unlink_user_person,
)
from organizations.models import (
    AuditLog,
    AuthIdentity,
    Invitation,
    LoginTicket,
    Membership,
    Organization,
    OrgRole,
    PlatformRole,
    RoleAssignment,
)
from organizations.tests.test_scoping import HOSTS, client_for, two_orgs  # noqa: F401
from school.models import Person
from users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _quiet_admin_messages():
    with patch("users.views.send_admin_message"):
        yield


@pytest.fixture(autouse=True)
def _isolated_org_context():
    """async_to_sync переносит изменения contextvar в вызывающий поток; в боте контекст ставится на каждое
    обновление заново, а в тестах восстанавливаем его после каждого теста."""
    token = context._current.set(None)
    yield
    context._current.reset(token)


def ksclub():
    return Organization.objects.get(slug="ksclub")


def member(email, org, role=None, status=Membership.Status.ACTIVE, **user_fields):
    user = User.objects.create(email=email, is_approved=True, **user_fields)
    Membership.objects.filter(user=user).delete()
    m = Membership.objects.create(user=user, organization=org, status=status)
    if role:
        RoleAssignment.objects.create(membership=m, role=OrgRole.objects.get(code=role), source="manual")
    return user


# --- R3-1: вход через бота и xfer ---------------------------------------------------------------------------


def _bot_start(tg_id, token):
    from users.services.linking import UserTelegramProfile, ensure_user_for_start

    profile = UserTelegramProfile(tg_id=tg_id, first_name="Тест", last_name="", username=f"u{tg_id}", phone="")
    return ensure_user_for_start(profile, token)[0]


def _login_page_token(client):
    client.get(reverse("users:login_page"))
    return client.session["telegram_token"]


def test_bot_link_login_for_new_user_creates_identity(client):
    token = _login_page_token(client)
    user = _bot_start(700001, token)
    assert AuthIdentity.objects.filter(user=user, kind="telegram", value="700001").exists()
    response = client.get(reverse("users:telegram_callback", kwargs={"token": token}))
    assert response.status_code == 302
    assert int(client.session["_auth_user_id"]) == user.pk


def test_bot_link_login_for_existing_user_found_by_identity(client):
    existing = User.objects.create(email="old@example.com", tg_id=700002, is_approved=True)
    token = _login_page_token(client)
    assert _bot_start(700002, token) == existing
    response = client.get(reverse("users:telegram_callback", kwargs={"token": token}))
    assert response.status_code == 302 and response.url == reverse("school:index")


def test_xfer_second_browser_within_window_then_replay_fails():
    user = User.objects.create(email="x@example.com", tg_id=700003, is_approved=True)
    bind_login_token(user, "tok-xfer")
    url = reverse("users:telegram_callback", kwargs={"token": "tok-xfer"})
    first, second, late = Client(), Client(), Client()
    assert first.get(url).status_code == 302
    assert second.get(url).status_code == 302  # xfer: второй браузер в окне — как раньше
    LoginTicket.objects.update(used_at=timezone.now() - timedelta(seconds=120))
    assert late.get(url).status_code == 404  # повтор после окна
    assert "_auth_user_id" not in late.session


def test_expired_and_wrong_tokens_give_no_session():
    user = User.objects.create(email="e@example.com", tg_id=700004, is_approved=True)
    bind_login_token(user, "tok-expired")
    LoginTicket.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    for token in ("tok-expired", "nope"):
        client = Client()
        assert client.get(reverse("users:telegram_callback", kwargs={"token": token})).status_code == 404
        assert "_auth_user_id" not in client.session


def test_token_bound_before_r3_works_once(client):
    User.objects.create(email="legacy@example.com", is_approved=True, token="pre-r3")
    url = reverse("users:telegram_callback", kwargs={"token": "pre-r3"})
    assert client.get(url).status_code == 302
    assert User.objects.get(email="legacy@example.com").token is None
    LoginTicket.objects.update(used_at=timezone.now() - timedelta(seconds=120))
    assert Client().get(url).status_code == 404


def test_identities_sync_and_migration_idempotent():
    from importlib import import_module

    from django.apps import apps

    user = User.objects.create(email="Real@Example.com", tg_id=700005)
    User.objects.create(email="700006@autogen.local", tg_id=700006)
    assert set(AuthIdentity.objects.filter(user=user).values_list("kind", "value")) == {
        ("telegram", "700005"),
        ("email", "real@example.com"),
    }
    assert not AuthIdentity.objects.filter(kind="email", value="700006@autogen.local").exists()
    user.tg_id = 700007
    user.save()
    assert AuthIdentity.objects.get(user=user, kind="telegram").value == "700007"
    migration = import_module("organizations.migrations.0004_auth_identities_from_users")
    AuthIdentity.objects.all().delete()
    migration.forwards(apps, None)
    migration.forwards(apps, None)
    assert AuthIdentity.objects.filter(kind="telegram").count() == User.objects.filter(tg_id__isnull=False).count()
    migration.backwards(apps, None)
    assert not AuthIdentity.objects.filter(source="legacy_import").exists()
    sync_identities(user)
    assert AuthIdentity.objects.filter(user=user).exists()


# --- R3-1: организация в боте (синтетический клиент Telegram) -----------------------------------------------


def _update(tg_id, text="/classes", callback=None):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    query = SimpleNamespace(data=callback, answer=AsyncMock(), edit_message_text=AsyncMock()) if callback else None
    return SimpleNamespace(effective_user=SimpleNamespace(id=tg_id), effective_message=message, callback_query=query)


def _ctx(user_data=None):
    return SimpleNamespace(user_data={} if user_data is None else user_data, args=[])


@pytest.fixture
def bot_users(two_orgs):
    ks, ot = two_orgs.orgs["KS"], two_orgs.orgs["OT"]
    single = member("bot-single@example.com", ot, tg_id=800001)
    both = member("bot-both@example.com", ks, tg_id=800002)
    Membership.objects.create(user=both, organization=ot, status=Membership.Status.ACTIVE)
    visitor = member("bot-visitor@example.com", ot, status=Membership.Status.PENDING, tg_id=800003)
    return SimpleNamespace(ks=ks, ot=ot, single=single, both=both, visitor=visitor)


def _run(handler, *args):
    from telegram.ext import ApplicationHandlerStop

    token = context.set_current_organization(None)
    try:
        async_to_sync(handler)(*args)
        return context._current.get(), False
    except ApplicationHandlerStop:
        return context._current.get(), True
    finally:
        context.reset_current_organization(token)


def test_bot_context_per_user_without_leak(bot_users):
    from tg_bot.handlers.organization import set_update_organization

    org, stopped = _run(set_update_organization, _update(800001), _ctx())
    assert org == bot_users.ot and not stopped
    org, stopped = _run(set_update_organization, _update(999999), _ctx())  # незнакомый — по умолчанию
    assert org == bot_users.ks and not stopped


def test_bot_asks_to_choose_with_several_memberships(bot_users):
    from tg_bot.handlers.organization import set_update_organization

    update = _update(800002)
    org, stopped = _run(set_update_organization, update, _ctx())
    assert stopped and not org
    update.effective_message.reply_text.assert_awaited()


def test_bot_selection_switch_and_revocation(bot_users):
    from tg_bot.handlers.organization import ORG_KEY, org_callback, set_update_organization

    data = {}
    _run(org_callback, _update(800002, callback=f"org:{bot_users.ot.pk}"), _ctx(data))
    assert data[ORG_KEY] == bot_users.ot.pk
    assert _run(set_update_organization, _update(800002), _ctx(data))[0] == bot_users.ot
    _run(org_callback, _update(800002, callback=f"org:{bot_users.ks.pk}"), _ctx(data))  # переключение
    assert _run(set_update_organization, _update(800002), _ctx(data))[0] == bot_users.ks
    Membership.objects.filter(user=bot_users.both, organization=bot_users.ks).update(status="suspended")
    org, _ = _run(set_update_organization, _update(800002), _ctx(data))
    assert org == bot_users.ot and ORG_KEY not in data  # прежний выбор больше не используется


def test_bot_cannot_select_foreign_or_suspended(bot_users):
    from tg_bot.handlers.organization import ORG_KEY, org_callback

    data = {}
    update = _update(800001, callback=f"org:{bot_users.ks.pk}")  # нет членства в ksclub
    _run(org_callback, update, _ctx(data))
    assert ORG_KEY not in data
    update.callback_query.answer.assert_awaited_with("Эта организация недоступна.", show_alert=True)
    Organization.objects.filter(pk=bot_users.ot.pk).update(status="suspended")
    _run(org_callback, _update(800001, callback=f"org:{bot_users.ot.pk}"), _ctx(data))
    assert ORG_KEY not in data


def test_bot_start_slug_for_member_and_visitor(bot_users):
    from tg_bot.handlers.organization import ORG_KEY, select_organization

    data = {}
    assert async_to_sync(select_organization)(_update(800002, "/start org_otherclub"), _ctx(data), "otherclub")
    assert data[ORG_KEY] == bot_users.ot.pk
    visitor_update, vdata = _update(800003, "/start otherclub"), {}
    assert async_to_sync(select_organization)(visitor_update, _ctx(vdata), "otherclub")
    assert ORG_KEY not in vdata
    assert "Вступить" in visitor_update.effective_message.reply_text.await_args.args[0]
    assert Membership.objects.get(user=bot_users.visitor, organization=bot_users.ot).status == "pending"  # не тронуто
    assert not async_to_sync(select_organization)(_update(800002), _ctx(), "no-such-org")


def test_resolve_never_returns_foreign_organization(bot_users):
    assert resolve_bot_organization(bot_users.single, selected_id=bot_users.ks.pk) == bot_users.ot


# --- Человек по организации ----------------------------------------------------------------------------------


def test_link_in_second_org_keeps_global_person(bot_users):
    with context.organization_context(bot_users.ks):
        ks_person = Person.objects.create(name="Свой", surname="KS", gender="male")
    with context.organization_context(bot_users.ot):
        ot_person = Person.objects.create(name="Свой", surname="OT", gender="male")
    user = bot_users.both
    assert link_user_person(user, ks_person) == ["person"]
    user.save(update_fields=["person"])
    assert link_user_person(user, ot_person) == []  # глобальный остаётся за ksclub
    user.refresh_from_db()
    assert user.person == ks_person
    assert person_for(user, bot_users.ot) == ot_person and person_for(user, bot_users.ks) == ks_person
    assert unlink_user_person(user, bot_users.ot) == []
    user.refresh_from_db()
    assert user.person == ks_person and person_for(user, bot_users.ot) is None


# --- R3-2: консоль платформы и админка -----------------------------------------------------------------------


@pytest.fixture
def platform_admin(two_orgs):
    admin = member("platform-admin@example.com", two_orgs.orgs["KS"], OrgRole.OWNER, is_staff=True)
    PlatformRole.objects.create(user=admin, role=PlatformRole.Role.GLOBAL_ADMIN)
    return admin


def test_platform_console_only_for_global_admin(two_orgs, platform_admin):
    owner = member("ks-owner@example.com", two_orgs.orgs["KS"], OrgRole.OWNER)
    assert client_for(owner, "KS").get(reverse("organizations:platform")).status_code == 403
    assert client_for(None, "KS").get(reverse("organizations:platform")).status_code == 302
    assert client_for(platform_admin, "KS").get(reverse("organizations:platform")).status_code == 200


def test_platform_actions_post_only_with_csrf(two_orgs, platform_admin):
    c = client_for(platform_admin, "KS")
    assert c.get(reverse("organizations:platform_org_create")).status_code == 405
    strict = Client(HTTP_HOST=HOSTS["KS"], enforce_csrf_checks=True)
    strict.force_login(platform_admin)
    assert strict.post(reverse("organizations:platform_org_create"), {"slug": "x", "name": "X"}).status_code == 403
    c.post(reverse("organizations:platform_org_create"), {"slug": "new-club", "name": "Новый", "kind": "club"})
    new = Organization.objects.get(slug="new-club")
    c.post(reverse("organizations:platform_org_status", kwargs={"pk": new.pk}), {"status": "suspended"})
    assert Organization.objects.get(pk=new.pk).status == "suspended"
    c.post(
        reverse("organizations:platform_org_status", kwargs={"pk": two_orgs.orgs["KS"].pk}), {"status": "suspended"}
    )
    assert ksclub().status == "active"  # организацию по умолчанию приостановить нельзя
    assert AuditLog.objects.filter(action__in=["organization_created", "organization_suspended"]).count() == 2


def test_owner_invitation_flow(two_orgs, platform_admin):
    c = client_for(platform_admin, "KS")
    c.post(reverse("organizations:platform_owner_invite", kwargs={"pk": two_orgs.orgs["OT"].pk}))
    url = c.get(reverse("organizations:platform")).context["invite_url"]
    path = url.split(HOSTS["KS"], 1)[-1] if HOSTS["KS"] in url else url[url.index("/join/") :]
    newcomer = User.objects.create(email="new-owner@example.com", is_approved=True)
    nc = client_for(newcomer, "OT")
    assert nc.get(path).status_code == 200
    assert nc.post(path).status_code == 302
    m = Membership.objects.get(user=newcomer, organization=two_orgs.orgs["OT"])
    assert m.status == "active" and m.role_assignments.filter(role__code=OrgRole.OWNER).exists()
    assert client_for(User.objects.create(email="late@example.com"), "OT").post(path).status_code == 404  # повтор


def test_django_admin_only_for_global_admin(two_orgs, platform_admin):
    su = User.objects.create(email="su-no-platform@example.com", is_staff=True, is_superuser=True, is_approved=True)
    PlatformRole.objects.filter(user=su).delete()  # перенос R1 выдаёт суперпользователю global_admin — снимаем
    assert client_for(su, "KS").get("/admin/").status_code == 302  # суперпользователь без глобальной роли
    staff_owner = member("staff-owner@example.com", two_orgs.orgs["KS"], OrgRole.OWNER, is_staff=True)
    assert client_for(staff_owner, "KS").get("/admin/").status_code == 302  # локальный владелец со staff
    assert client_for(platform_admin, "KS").get("/admin/").status_code == 200


# --- R3-3: консоль организации -------------------------------------------------------------------------------


@pytest.fixture
def org_people(two_orgs, settings):
    settings.ORGS_ROLES_SOURCE = "memberships"
    settings.ORGS_ENFORCE_SCOPING = True
    ks, ot = two_orgs.orgs["KS"], two_orgs.orgs["OT"]
    return SimpleNamespace(
        ks=ks,
        ot=ot,
        owner=member("console-owner@example.com", ks, OrgRole.OWNER),
        admin=member("console-admin@example.com", ks, OrgRole.ADMIN),
        manager=member("console-manager@example.com", ks, OrgRole.MANAGER),
        pending=member("console-pending@example.com", ks, status=Membership.Status.PENDING),
        foreign=member("console-foreign@example.com", ot, status=Membership.Status.PENDING),
    )


def test_org_console_access_and_isolation(org_people):
    for user, code in ((org_people.owner, 200), (org_people.admin, 200), (org_people.manager, 403)):
        assert client_for(user, "KS").get(reverse("organizations:org_console")).status_code == code
    page = client_for(org_people.owner, "KS").get(reverse("organizations:org_console")).content.decode()
    assert "console-pending@example.com" in page and "console-foreign@example.com" not in page
    foreign = Membership.objects.get(user=org_people.foreign)
    response = client_for(org_people.owner, "KS").post(
        reverse("organizations:org_member_status", kwargs={"pk": foreign.pk}), {"action": "approve"}
    )
    assert response.status_code == 404
    assert Membership.objects.get(pk=foreign.pk).status == "pending"


def test_org_queue_approve_roles_and_audit(org_people):
    c = client_for(org_people.admin, "KS")
    pending = Membership.objects.get(user=org_people.pending)
    assert c.get(reverse("organizations:org_member_status", kwargs={"pk": pending.pk})).status_code == 405
    c.post(reverse("organizations:org_member_status", kwargs={"pk": pending.pk}), {"action": "approve"})
    pending.refresh_from_db()
    assert pending.status == "active" and pending.approved_by == org_people.admin
    c.post(reverse("organizations:org_member_role", kwargs={"pk": pending.pk}), {"role": "coach", "action": "add"})
    assert pending.role_assignments.filter(role__code="coach").exists()
    assert (
        c.post(
            reverse("organizations:org_member_role", kwargs={"pk": pending.pk}), {"role": "owner", "action": "add"}
        ).status_code
        == 403
    )  # владельца назначает платформа
    owner_m = Membership.objects.get(user=org_people.owner)
    assert (
        c.post(
            reverse("organizations:org_member_status", kwargs={"pk": owner_m.pk}), {"action": "suspend"}
        ).status_code
        == 403
    )  # администратор не приостанавливает владельца
    assert set(AuditLog.objects.filter(organization=org_people.ks).values_list("action", flat=True)) >= {
        "member_approve",
        "role_added",
    }


def test_org_invitation_accept_revoke_expire(org_people):
    c = client_for(org_people.owner, "KS")
    c.post(reverse("organizations:org_invitation_create"), {"role": "coach"})
    url = c.get(reverse("organizations:org_console")).context["invite_url"]
    path = url[url.index("/join/") :]
    guest = User.objects.create(email="invited@example.com", is_approved=True)
    assert client_for(guest, "KS").post(path).status_code == 302
    m = Membership.objects.get(user=guest, organization=org_people.ks)
    assert m.status == "active" and m.role_assignments.filter(role__code="coach").exists()
    assert client_for(guest, "KS").post(path).status_code == 404  # повтор
    c.post(reverse("organizations:org_invitation_create"), {"role": ""})
    path2 = c.get(reverse("organizations:org_console")).context["invite_url"]
    path2 = path2[path2.index("/join/") :]
    Invitation.objects.filter(accepted_at__isnull=True).update(expires_at=timezone.now() - timedelta(seconds=1))
    assert client_for(User.objects.create(email="late2@example.com"), "KS").post(path2).status_code == 404
    assert client_for(None, "KS").get(path2).status_code == 302  # вход обязателен


# --- R3-4: посетитель ----------------------------------------------------------------------------------------


def test_visitor_can_request_join(org_people):
    visitor = member("visitor@example.com", org_people.ot)  # активен только во второй организации
    c = client_for(visitor, "KS")
    page = c.get(reverse("users:awaiting_approval")).content.decode()
    assert "Вступить" in page
    assert c.get(reverse("organizations:join_request")).status_code == 405
    c.post(reverse("organizations:join_request"))
    assert Membership.objects.get(user=visitor, organization=org_people.ks).status == "pending"
    assert "в очереди" in c.get(reverse("users:awaiting_approval")).content.decode()
    assert c.get(reverse("groups:group_list")).status_code == 302  # заявка не даёт доступа
    assert Membership.objects.get(user=visitor, organization=org_people.ot).status == "active"


def test_public_results_by_host_for_anonymous(two_orgs, settings):
    settings.ORGS_ENFORCE_SCOPING = True
    event = two_orgs.data["OT"].event
    url = reverse("results:public_live", kwargs={"token": event.public_token})
    assert Client(HTTP_HOST=HOSTS["OT"]).get(url).status_code != 404
    assert Client(HTTP_HOST=HOSTS["KS"]).get(url).status_code == 404


# --- R3-5: stage4_check --------------------------------------------------------------------------------------


def test_stage4_check_r3(two_orgs, capsys):
    from organizations.legacy import import_legacy

    import_legacy()
    PlatformRole.objects.all().delete()
    with pytest.raises(CommandError):
        call_command("stage4_check", "--release", "r3")  # нет глобального администратора
    admin = User.objects.create(email="ga@example.com", is_staff=True, is_approved=True)
    PlatformRole.objects.create(user=admin, role=PlatformRole.Role.GLOBAL_ADMIN)
    call_command("stage4_check", "--release", "r3")
    out = capsys.readouterr().out
    assert "без способа входа: 0" in out and "✓ stage4_check r3" in out
