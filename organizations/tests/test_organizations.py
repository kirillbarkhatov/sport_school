"""R1-3, R1-4, R1-5: организации, контекст, перенос ролей, двойная запись, отчёт и проверка релиза."""

from io import StringIO

import pytest
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, override_settings

from organizations import context
from organizations.legacy import find_discrepancies, import_legacy
from organizations.models import AuditLog, Membership, Organization, OrgDomain, OrgRole, PlatformRole, RoleAssignment
from school.models import Club
from users.constants import ADMIN_GROUP_NAME, COACH_GROUP_NAME, MANAGER_GROUP_NAME
from users.models import User

pytestmark = pytest.mark.django_db


def ksclub():
    return Organization.objects.get(slug="ksclub")


def roles_of(user):
    return set(
        RoleAssignment.objects.filter(membership__user=user, membership__organization=ksclub()).values_list(
            "role__code", flat=True
        )
    )


def membership(user):
    return Membership.objects.get(user=user, organization=ksclub())


# --- Миграции и домены --------------------------------------------------------------------------------


def test_migration_created_ksclub_domains_and_system_roles():
    org = ksclub()
    assert org.kind == Organization.Kind.CLUB
    assert set(org.domains.values_list("host", flat=True)) == {"ksclub.ru", "www.ksclub.ru"}
    assert org.domains.get(host="ksclub.ru").is_primary
    codes = set(OrgRole.objects.filter(organization__isnull=True).values_list("code", flat=True))
    assert codes == {"owner", "admin", "manager", "coach", "competition_secretary"}


@pytest.mark.parametrize("host", ["ksclub.ru", "www.ksclub.ru", "KSCLUB.RU:443", "ksclub.ru."])
def test_production_hosts_resolve_to_ksclub(host):
    assert context.organization_for_host(host) == ksclub()


def test_unknown_host_falls_back_to_default_and_can_be_disabled(settings):
    other = Organization.objects.create(slug="demo", name="Демо")
    OrgDomain.objects.create(organization=other, host="demo.ksclub.ru")
    assert context.organization_for_host("demo.ksclub.ru") == other
    assert context.organization_for_host("10.0.0.5") == ksclub()  # запасной вариант R1
    settings.ORGS_DEFAULT_ORGANIZATION_SLUG = ""
    assert context.organization_for_host("10.0.0.5") is None


def test_suspended_organization_is_not_resolved():
    other = Organization.objects.create(slug="off", name="Off", status=Organization.Status.SUSPENDED)
    OrgDomain.objects.create(organization=other, host="off.ksclub.ru")
    assert context.organization_for_host("off.ksclub.ru") == ksclub()


@override_settings(ALLOWED_HOSTS=["*"])
def test_middleware_sets_request_org_and_clears_context_after_response_and_error():
    seen = {}

    def view(request):
        seen["request"] = request.organization
        seen["context"] = context.get_current_organization()
        return "ok"

    middleware = context.OrganizationMiddleware(view)
    request = RequestFactory().get("/", HTTP_HOST="www.ksclub.ru")
    assert middleware(request) == "ok"
    assert seen["request"] == ksclub() == seen["context"]
    assert context.get_current_organization() is None

    def failing(request):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        context.OrganizationMiddleware(failing)(RequestFactory().get("/", HTTP_HOST="ksclub.ru"))
    assert context.get_current_organization() is None


def test_organization_context_manager_restores_previous():
    with context.organization_context("A"):
        with context.organization_context("B"):
            assert context.get_current_organization() == "B"
        assert context.get_current_organization() == "A"
    assert context.get_current_organization() is None


def test_every_celery_task_gets_organization_context():
    other = Organization.objects.create(slug="demo", name="Демо")
    assert context.celery_signals_connected()
    context._task_prerun(task_id="t1", kwargs={})
    assert context.get_current_organization() == ksclub()
    context._task_postrun(task_id="t1")
    assert context.get_current_organization() is None
    context._task_prerun(task_id="t2", kwargs={"organization_id": other.pk})
    assert context.get_current_organization() == other
    context._task_postrun(task_id="t2")
    assert context.get_current_organization() is None


def test_bot_and_results_worker_activate_default_organization():
    import threading

    from results.worker import EventRunner

    result = {}

    def thread_body():
        context.activate_default_organization()
        result["org"] = context.get_current_organization()

    thread = threading.Thread(target=thread_body)
    thread.start()
    thread.join()
    assert result["org"] == ksclub()
    assert context.get_current_organization() is None  # контекст потока не протекает
    assert "activate_default_organization" in EventRunner.run.__code__.co_names


def test_new_club_cards_are_not_linked_automatically():
    club = Club.objects.create(name="Тестовый клуб")
    assert club.organization is None  # «Канаев Ски Клаб» связывает миграция 0052, остальные — незанятые карточки


# --- Перенос старых полей ------------------------------------------------------------------------------


@pytest.fixture
def legacy_users(settings):
    settings.TELEGRAM_ADMIN_IDS = ["111"]
    settings.TELEGRAM_MANAGER_IDS = ["222"]
    settings.TELEGRAM_COACH_IDS = ["333", "not-a-number"]
    with override_settings(ORGS_LEGACY_SYNC=False):  # создаём «старые» данные без двойной записи
        users = {
            "approved": User.objects.create(email="a@example.com", is_approved=True),
            "pending": User.objects.create(email="p@example.com", is_approved=False),
            "coach_group": User.objects.create(email="c@example.com", is_approved=True),
            "manager_group": User.objects.create(email="m@example.com", is_approved=True),
            "admin_group": User.objects.create(email="g@example.com", is_approved=True),
            "tg_admin": User.objects.create(email="t1@example.com", is_approved=True, tg_id=111),
            "tg_manager": User.objects.create(email="t2@example.com", is_approved=False, tg_id=222),
            "tg_coach": User.objects.create(email="t3@example.com", is_approved=True, tg_id=333),
            "staff": User.objects.create(email="s@example.com", is_staff=True),
            "superuser": User.objects.create(email="su@example.com", is_staff=True, is_superuser=True),
        }
        users["coach_group"].groups.add(Group.objects.get_or_create(name=COACH_GROUP_NAME)[0])
        users["manager_group"].groups.add(Group.objects.get_or_create(name=MANAGER_GROUP_NAME)[0])
        users["admin_group"].groups.add(Group.objects.get_or_create(name=ADMIN_GROUP_NAME)[0])
        Membership.objects.all().delete()
    return users


def test_import_maps_every_legacy_source(legacy_users):
    report = import_legacy()
    u = legacy_users
    assert report.memberships_created == len(u)
    assert membership(u["approved"]).status == Membership.Status.ACTIVE
    assert membership(u["pending"]).status == Membership.Status.PENDING
    assert membership(u["tg_manager"]).status == Membership.Status.PENDING  # роль не подтверждает членство
    assert membership(u["staff"]).status == Membership.Status.ACTIVE
    assert roles_of(u["coach_group"]) == {"coach"}
    assert roles_of(u["manager_group"]) == {"manager"}
    assert roles_of(u["admin_group"]) == {"admin"}
    assert roles_of(u["tg_admin"]) == {"admin"}
    assert roles_of(u["tg_manager"]) == {"manager"}
    assert roles_of(u["tg_coach"]) == {"coach"}
    assert roles_of(u["staff"]) == {"admin"}
    assert roles_of(u["superuser"]) == {"owner"}
    assert PlatformRole.objects.filter(user=u["superuser"], role="global_admin").exists()
    assert roles_of(u["approved"]) == set() and roles_of(u["pending"]) == set()
    assert AuditLog.objects.filter(action="legacy_import").exists()


def test_import_is_idempotent_and_keeps_explicit_assignments(legacy_users):
    import_legacy()
    counts = (Membership.objects.count(), RoleAssignment.objects.count(), PlatformRole.objects.count())
    pending = membership(legacy_users["pending"])
    pending.status = Membership.Status.SUSPENDED  # явное решение администратора
    pending.save()
    manual = RoleAssignment.objects.create(
        membership=membership(legacy_users["approved"]), role=OrgRole.objects.get(code="competition_secretary")
    )
    report = import_legacy()
    assert report.memberships_created == 0 and report.roles_created == 0 and report.platform_roles_created == 0
    assert (Membership.objects.count(), RoleAssignment.objects.count() - 1, PlatformRole.objects.count()) == counts
    assert membership(legacy_users["pending"]).status == Membership.Status.SUSPENDED
    assert RoleAssignment.objects.filter(pk=manual.pk).exists()


def test_import_command_runs_twice(legacy_users):
    out = StringIO()
    call_command("orgs_import_legacy", stdout=out)
    call_command("orgs_import_legacy", stdout=out)
    assert "создано членств: 0" in out.getvalue().splitlines()[-3]


# --- Двойная запись ------------------------------------------------------------------------------------


def test_new_user_gets_pending_membership_via_sync():
    user = User.objects.create(email="new@example.com")
    assert membership(user).status == Membership.Status.PENDING
    user.is_approved = True
    user.save()
    assert membership(user).status == Membership.Status.ACTIVE


def test_legacy_groups_change_updates_roles_and_back():
    user = User.objects.create(email="x@example.com", is_approved=True)
    coach_group = Group.objects.get_or_create(name=COACH_GROUP_NAME)[0]
    user.groups.add(coach_group)
    assert roles_of(user) == {"coach"}
    user.groups.remove(coach_group)
    assert roles_of(user) == set()

    # новая модель → старые поля
    RoleAssignment.objects.create(membership=membership(user), role=OrgRole.objects.get(code="manager"))
    assert set(user.groups.values_list("name", flat=True)) == {MANAGER_GROUP_NAME}
    RoleAssignment.objects.filter(membership__user=user).delete()
    assert not user.groups.exists()


def test_membership_status_pushes_is_approved_without_recursion():
    user = User.objects.create(email="y@example.com")
    m = membership(user)
    m.status = Membership.Status.ACTIVE
    m.save()
    user.refresh_from_db()
    assert user.is_approved is True
    m.status = Membership.Status.SUSPENDED
    m.save()
    user.refresh_from_db()
    assert user.is_approved is False
    assert membership(user).status == Membership.Status.SUSPENDED  # обратная синхронизация не перетёрла статус


def test_manual_role_survives_group_removal():
    user = User.objects.create(email="z@example.com", is_approved=True)
    RoleAssignment.objects.create(membership=membership(user), role=OrgRole.objects.get(code="coach"))
    user.groups.remove(*user.groups.all())
    assert roles_of(user) == {"coach"}


@override_settings(ORGS_LEGACY_SYNC=False)
def test_sync_disabled_does_nothing():
    user = User.objects.create(email="off@example.com", is_approved=True)
    assert not Membership.objects.filter(user=user).exists()
    user.groups.add(Group.objects.get_or_create(name=COACH_GROUP_NAME)[0])
    assert not RoleAssignment.objects.exists()


def test_login_like_save_does_not_touch_membership():
    user = User.objects.create(email="l@example.com")
    m = membership(user)
    m.status = Membership.Status.INVITED
    m.save()
    user.save(update_fields=["last_login"])
    assert membership(user).status == Membership.Status.INVITED


# --- Отчёт и проверка релиза ---------------------------------------------------------------------------


def test_report_and_stage4_check_pass_after_import(legacy_users):
    import_legacy()
    out = StringIO()
    call_command("orgs_report", stdout=out)
    text = out.getvalue()
    assert f"Пользователей: {len(legacy_users)}; членств: {len(legacy_users)}" in text
    assert "всего 0" in text and "@" not in text  # без персональных данных
    check = StringIO()
    call_command("stage4_check", "--release", "r1", stdout=check)
    assert "всё в порядке" in check.getvalue()
    assert find_discrepancies().total == 0


def test_stage4_check_fails_on_missing_membership(legacy_users):
    import_legacy()
    Membership.objects.filter(user=legacy_users["approved"]).delete()
    with pytest.raises(CommandError):
        call_command("stage4_check", "--release", "r1", stdout=StringIO(), stderr=StringIO())


def test_stage4_check_fails_on_wrong_flags(legacy_users, settings):
    import_legacy()
    settings.ORGS_ROLES_SOURCE = "memberships"
    with pytest.raises(CommandError):
        call_command("stage4_check", "--release", "r1", stdout=StringIO(), stderr=StringIO())
