"""T04 (R2-1, R2-2, D04, D05, D07): единый слой прав, переключение источника ролей, отзыв автоматических ролей."""

import pytest
from asgiref.sync import async_to_sync
from django.contrib.auth.models import Group
from django.test import Client
from django.urls import reverse

from organizations import context
from organizations.legacy import import_legacy
from organizations.models import Membership, Organization, OrgRole, PlatformRole, RoleAssignment, Source
from organizations.permissions import ACTIONS, authorize, has_role, has_site_access, is_privileged, roles_for
from school.models import CompetitionApplicationLink
from tests.factories import CompetitionFactory
from users.constants import ADMIN_GROUP_NAME, COACH_GROUP_NAME, MANAGER_GROUP_NAME
from users.models import User

pytestmark = pytest.mark.django_db

ACTION_NAMES = sorted(set(ACTIONS) - {"site.access"})


def ksclub():
    return Organization.objects.get(slug="ksclub")


def membership(user, organization=None):
    return Membership.objects.get(user=user, organization=organization or ksclub())


def auto_roles(user):
    return set(
        RoleAssignment.objects.filter(membership__user=user, membership__organization=ksclub()).values_list(
            "role__code", flat=True
        )
    )


def group(name):
    return Group.objects.get_or_create(name=name)[0]


def make(email, *, approved=True, groups=(), staff=False, superuser=False, tg_id=None):
    user = User.objects.create(
        email=email, is_approved=approved, is_staff=staff or superuser, is_superuser=superuser, tg_id=tg_id
    )
    for name in groups:
        user.groups.add(group(name))
    return user


def snapshot(users):
    return {
        key: {action: authorize(user, action) for action in ACTION_NAMES} | {"site.access": has_site_access(user)}
        for key, user in users.items()
    }


@pytest.fixture
def population(settings):
    settings.ORGS_ROLES_SOURCE = "legacy"
    settings.TELEGRAM_ADMIN_IDS = []
    settings.TELEGRAM_MANAGER_IDS = []
    settings.TELEGRAM_COACH_IDS = []
    return {
        "parent": make("parent@example.com"),
        "pending": make("pending@example.com", approved=False),
        "coach": make("coach@example.com", groups=[COACH_GROUP_NAME]),
        "manager": make("manager@example.com", groups=[MANAGER_GROUP_NAME]),
        "admin": make("admin@example.com", groups=[ADMIN_GROUP_NAME]),
        "staff": make("staff@example.com", approved=False, staff=True),
        "superuser": make("su@example.com", superuser=True),
    }


# --- Отсутствие повышения доступа при переключении --------------------------------------------------------


def test_switch_to_memberships_never_widens_access(population, settings):
    before = snapshot(population)
    import_legacy()
    settings.ORGS_ROLES_SOURCE = "memberships"
    after = snapshot(population)
    for key, actions in after.items():
        for action, allowed in actions.items():
            assert not (allowed and not before[key][action]), f"{key} получил {action} после переключения"
    # Для всех, кроме staff без глобальной роли, доступ совпадает полностью.
    assert {k: v for k, v in after.items() if k != "staff"} == {k: v for k, v in before.items() if k != "staff"}


def test_staff_without_global_role_gets_nothing_extra_in_memberships(population, settings):
    staff = population["staff"]
    assert is_privileged(staff)  # legacy: is_staff — как раньше
    import_legacy()
    settings.ORGS_ROLES_SOURCE = "memberships"
    assert not is_privileged(staff)
    assert not authorize(staff, "org.full")
    assert not PlatformRole.objects.filter(user=staff).exists()


def test_superuser_with_owner_membership_keeps_full_access_in_both_modes(population, settings):
    su = population["superuser"]
    import_legacy()
    for source in ("legacy", "memberships"):
        settings.ORGS_ROLES_SOURCE = source
        assert all(authorize(su, action) for action in ACTION_NAMES)


def test_unknown_action_is_an_error(population):
    with pytest.raises(KeyError):
        authorize(population["coach"], "competitions.delete_everything")


# --- Неактивный сотрудник и пользователь без членства ---------------------------------------------------


def test_suspended_staff_member_loses_access_in_memberships(population, settings):
    import_legacy()
    settings.ORGS_ROLES_SOURCE = "memberships"
    manager = population["manager"]
    assert authorize(manager, "people.manage")
    Membership.objects.filter(pk=membership(manager).pk).update(status=Membership.Status.SUSPENDED)
    assert not has_site_access(manager)
    assert not authorize(manager, "staff.area")
    assert roles_for(manager) == set()


def test_inactive_staff_member_in_legacy_has_no_staff_area(population):
    coach = population["coach"]
    User.objects.filter(pk=coach.pk).update(is_approved=False)
    coach.refresh_from_db()
    assert not authorize(coach, "staff.area")


def test_user_without_membership_has_no_access_in_memberships(settings):
    settings.ORGS_ROLES_SOURCE = "legacy"
    user = make("nomember@example.com", groups=[MANAGER_GROUP_NAME])
    Membership.objects.filter(user=user).delete()
    settings.ORGS_ROLES_SOURCE = "memberships"
    assert not has_site_access(user)
    assert not any(authorize(user, action) for action in ACTION_NAMES)


# --- Разделение прав по активной организации --------------------------------------------------------------


def test_roles_in_other_organization_do_not_grant_access(settings):
    settings.ORGS_ROLES_SOURCE = "memberships"
    other = Organization.objects.create(slug="other-club", name="Другой клуб")
    user = make("other@example.com")
    Membership.objects.filter(user=user).delete()
    m = Membership.objects.create(user=user, organization=other, status=Membership.Status.ACTIVE)
    RoleAssignment.objects.create(membership=m, role=OrgRole.objects.get(code=OrgRole.MANAGER))

    assert not authorize(user, "people.manage")  # контекст по умолчанию — ksclub
    assert authorize(user, "people.manage", organization=other)
    with context.organization_context(other):
        assert authorize(user, "people.manage")
        assert has_role(user, OrgRole.MANAGER)
    assert not has_role(user, OrgRole.MANAGER, organization=ksclub())


# --- D07: повторная синхронизация отзывает роли без основания ---------------------------------------------


def test_reimport_revokes_role_after_group_removed_and_keeps_manual(population):
    import_legacy()
    coach = population["coach"]
    m = membership(coach)
    RoleAssignment.objects.create(membership=m, role=OrgRole.objects.get(code=OrgRole.SECRETARY))
    assert auto_roles(coach) == {OrgRole.COACH, OrgRole.SECRETARY}

    # основание убрано в обход сигналов (как правка базы или отключённая синхронизация)
    User.groups.through.objects.filter(user=coach).delete()
    report = import_legacy()
    assert report.roles_revoked >= 1
    assert auto_roles(coach) == {OrgRole.SECRETARY}  # ручная роль сохранилась


def test_reimport_revokes_owner_and_global_admin_when_superuser_removed(population):
    import_legacy()
    su = population["superuser"]
    assert auto_roles(su) == {OrgRole.OWNER}
    assert PlatformRole.objects.filter(user=su, role=PlatformRole.Role.GLOBAL_ADMIN).exists()

    User.objects.filter(pk=su.pk).update(is_superuser=False, is_staff=False)
    import_legacy()
    assert auto_roles(su) == set()
    assert not PlatformRole.objects.filter(user=su).exists()


def test_reimport_keeps_role_while_alternative_source_remains(population, settings):
    settings.TELEGRAM_COACH_IDS = ["555"]
    coach = population["coach"]
    User.objects.filter(pk=coach.pk).update(tg_id=555)
    coach.refresh_from_db()
    import_legacy()
    User.groups.through.objects.filter(user=coach).delete()
    import_legacy()
    assert auto_roles(coach) == {OrgRole.COACH}  # основание осталось в TELEGRAM_COACH_IDS

    settings.TELEGRAM_COACH_IDS = []
    import_legacy()
    assert auto_roles(coach) == set()


def test_manual_platform_role_is_not_revoked(population):
    parent = population["parent"]
    PlatformRole.objects.create(user=parent, role=PlatformRole.Role.GLOBAL_ADMIN, source=Source.MANUAL)
    import_legacy()
    assert PlatformRole.objects.filter(user=parent).exists()


def test_group_removal_via_signal_revokes_auto_role(population):
    manager = population["manager"]
    import_legacy()
    manager.groups.remove(group(MANAGER_GROUP_NAME))
    assert auto_roles(manager) == set()


def test_telegram_lists_ignored_after_switch(population, settings):
    import_legacy()
    settings.ORGS_ROLES_SOURCE = "memberships"
    settings.TELEGRAM_MANAGER_IDS = ["777"]
    parent = population["parent"]
    User.objects.filter(pk=parent.pk).update(tg_id=777)
    parent.refresh_from_db()
    assert not authorize(parent, "people.manage")
    report = import_legacy()  # после переключения перенос ролей не выполняется
    assert report.roles_created == 0
    assert auto_roles(parent) == set()


# --- Бот ------------------------------------------------------------------------------------------------


def test_bot_role_check_reads_env_only_in_legacy(settings):
    from tg_bot.services.notifications import _user_has_role

    check = async_to_sync(_user_has_role)
    settings.ORGS_ROLES_SOURCE = "legacy"
    assert check(999, MANAGER_GROUP_NAME, ["999"]) is True
    settings.ORGS_ROLES_SOURCE = "memberships"
    assert check(999, MANAGER_GROUP_NAME, ["999"]) is False

    settings.ORGS_ROLES_SOURCE = "legacy"
    coach = make("botcoach@example.com", groups=[COACH_GROUP_NAME], tg_id=4242)
    import_legacy()
    for source in ("legacy", "memberships"):
        settings.ORGS_ROLES_SOURCE = source
        assert check(coach.tg_id, COACH_GROUP_NAME, []) is True
        assert check(coach.tg_id, MANAGER_GROUP_NAME, []) is False


# --- D04 / D05: регрессии на страницах -------------------------------------------------------------------

EXPORTS = [
    "school:competition_export",
    "school:competition_export_type2",
    "school:competition_export_word_type1",
    "school:competition_export_word_type2",
    "school:competition_export_word_type3",
    "school:competition_export_word_type4",
]


@pytest.fixture(params=["legacy", "memberships"])
def roles_mode(request, population, settings):
    import_legacy()
    settings.ORGS_ROLES_SOURCE = request.param
    return population


def _client(user=None):
    client = Client()
    if user is not None:
        client.force_login(user)
    return client


@pytest.mark.parametrize("name", EXPORTS)
def test_d04_exports_closed_to_guest_and_parent(roles_mode, name):
    url = reverse(name, kwargs={"pk": CompetitionFactory().pk})
    assert _client().get(url).status_code == 302
    assert _client(roles_mode["parent"]).get(url).status_code == 403
    assert _client(roles_mode["coach"]).get(url).status_code == 200


@pytest.mark.parametrize(
    "name,kwargs",
    [
        ("school:competition_create", None),
        ("school:competition_edit", "competition"),
        ("school:competition_groups", "competition"),
        ("classes:class_create", None),
        ("groups:group_create", None),
        ("school:documents_bulk_upload", None),
        ("school:documents_analysis_list", None),
    ],
)
def test_d05_staff_forms_closed_to_parent(roles_mode, name, kwargs):
    url = reverse(name, kwargs={"pk": CompetitionFactory().pk} if kwargs else None)
    assert _client(roles_mode["parent"]).get(url).status_code == 403
    assert _client(roles_mode["parent"]).post(url, {}).status_code == 403
    assert _client(roles_mode["coach"]).get(url).status_code == 200


def test_apply_link_page_not_blocked_by_staff_checks(roles_mode):
    """Страница подачи заявки по ссылке: без входа — обычный редирект на вход (как до T04), не 403/500;
    родитель подаёт заявку без роли сотрудника. Анонимная подача без входа — R3-4."""
    competition = CompetitionFactory()
    link = CompetitionApplicationLink.objects.create(competition=competition, token="synthetic-token")
    url = reverse("school:competition_apply", kwargs={"pk": competition.pk, "token": link.token})
    guest = _client().get(url)
    # R3-4: гостю — общая информация о соревновании и вход (до R3 был редирект на вход)
    assert guest.status_code == 200 and reverse("users:login_page") in guest.content.decode()
    assert _client().post(url, {}).status_code == 302
    assert _client(roles_mode["parent"]).get(url).status_code == 200
    assert _client(roles_mode["coach"]).get(url).status_code == 200


# --- D11: в memberships членство обязательно для всех, включая is_superuser --------------------------------


def _org(slug):
    return Organization.objects.create(slug=slug, name=f"Клуб {slug}")


def _member(user, organization, *, status=Membership.Status.ACTIVE, roles=()):
    m, _ = Membership.objects.update_or_create(user=user, organization=organization, defaults={"status": status})
    for code in roles:
        RoleAssignment.objects.get_or_create(membership=m, role=OrgRole.objects.get(code=code))
    return m


def test_d11_superuser_without_membership_has_no_crm_access(settings):
    settings.ORGS_ROLES_SOURCE = "legacy"
    su = make("lone-su@example.com", superuser=True)
    Membership.objects.filter(user=su).delete()
    assert authorize(su, "org.full")  # legacy: как раньше
    settings.ORGS_ROLES_SOURCE = "memberships"
    assert not has_site_access(su)
    assert not is_privileged(su)
    assert not any(authorize(su, action) for action in ACTION_NAMES)
    response = _client(su).get(reverse("members:members_list"))
    assert response.status_code == 302 and reverse("users:awaiting_approval") in response["Location"]
    assert _client(su).get(reverse("api:person-list")).status_code == 403


def test_d11_global_admin_gets_nothing_in_other_organization(population, settings):
    import_legacy()  # суперпользователь получил owner в ksclub и global_admin
    settings.ORGS_ROLES_SOURCE = "memberships"
    su = population["superuser"]
    assert PlatformRole.objects.filter(user=su, role=PlatformRole.Role.GLOBAL_ADMIN).exists()
    other = _org("other-org")
    assert authorize(su, "org.full")  # ksclub — по членству
    assert not has_site_access(su, organization=other)
    assert not any(authorize(su, action, organization=other) for action in ACTION_NAMES)
    with context.organization_context(other):
        assert not is_privileged(su)
        assert su.get_accessible_family_ids() == []


def test_d11_suspended_owner_split_by_organization(settings):
    settings.ORGS_ROLES_SOURCE = "memberships"
    a, b = _org("org-a"), _org("org-b")
    owner = make("owner-ab@example.com", superuser=True)
    Membership.objects.filter(user=owner).delete()
    _member(owner, a, status=Membership.Status.SUSPENDED, roles=[OrgRole.OWNER])
    _member(owner, b, roles=[OrgRole.OWNER])
    assert not any(authorize(owner, action, organization=a) for action in ACTION_NAMES)
    assert all(authorize(owner, action, organization=b) for action in ACTION_NAMES)
    with context.organization_context(a):
        assert not has_site_access(owner)
    with context.organization_context(b):
        assert is_privileged(owner)


# --- D12: Telegram-списки не дают веб-прав; бот сохраняет свою ветку --------------------------------------


@pytest.mark.parametrize("env", ["TELEGRAM_ADMIN_IDS", "TELEGRAM_MANAGER_IDS", "TELEGRAM_COACH_IDS"])
def test_d12_telegram_list_only_account_has_no_web_rights_in_legacy(settings, env):
    from tg_bot.services.notifications import _user_has_role

    settings.ORGS_ROLES_SOURCE = "legacy"
    setattr(settings, env, ["8800"])
    user = make(f"tg-only-{env.lower()}@example.com", tg_id=8800)
    assert not user.groups.exists()
    assert roles_for(user) == set()
    assert not any(authorize(user, action) for action in ACTION_NAMES)
    assert _client(user).get(reverse("school:competition_create")).status_code == 403
    assert _client(user).get(reverse("members:telegram_interlocutors")).status_code == 403

    group_name = {
        "TELEGRAM_ADMIN_IDS": ADMIN_GROUP_NAME,
        "TELEGRAM_MANAGER_IDS": MANAGER_GROUP_NAME,
        "TELEGRAM_COACH_IDS": COACH_GROUP_NAME,
    }[env]
    assert async_to_sync(_user_has_role)(8800, group_name, ["8800"]) is True  # бот — как раньше


def test_d12_policy_comparison_web_rights_ignore_telegram_lists(population, settings):
    before = snapshot(population)
    settings.TELEGRAM_ADMIN_IDS = settings.TELEGRAM_MANAGER_IDS = settings.TELEGRAM_COACH_IDS = ["1", "2", "3"]
    for n, user in enumerate(population.values(), start=1):
        User.objects.filter(pk=user.pk).update(tg_id=n)
        user.refresh_from_db()
    assert snapshot(population) == before


def test_d12_env_groups_on_telegram_link_only_before_switch(settings):
    from users.services.linking import assign_groups_from_env

    settings.TELEGRAM_COACH_IDS = ["9100"]
    settings.ORGS_ROLES_SOURCE = "memberships"
    user = make("link@example.com", tg_id=9100)
    assign_groups_from_env(user)
    assert not user.groups.exists()
    settings.ORGS_ROLES_SOURCE = "legacy"
    assign_groups_from_env(user)  # legacy-поведение привязки сохранено
    assert set(user.groups.values_list("name", flat=True)) == {COACH_GROUP_NAME}


def test_bot_manager_scope_is_in_memory_only(population, settings):
    from organizations.permissions import BOT_MANAGER_SCOPE

    import_legacy()
    settings.ORGS_ROLES_SOURCE = "memberships"
    manager = population["manager"]
    assert not is_privileged(manager)
    setattr(manager, BOT_MANAGER_SCOPE, True)
    assert is_privileged(manager)
    assert not is_privileged(User.objects.get(pk=manager.pk))
