# ruff: noqa: F811 — фикстуры двух организаций импортируются из test_scoping
"""T06 (R2-5, R2-7, D15, D16): внешние спортсмены, выпускники, наследие трансляций, глобальные учётки."""

from datetime import date, timedelta
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group as AuthGroup
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from api.models import PublicStreamAccess, StreamRun
from organizations.accounts import set_user_approval
from organizations.alumni import is_history_only, mark_alumni
from organizations.context import organization_context
from organizations.externals import external_rule_q, mailing_recipients, mark_externals, report
from organizations.models import Membership, Organization, OrgRole, PlatformRole, RoleAssignment
from organizations.tests.test_scoping import (  # noqa: F401 — общие фикстуры двух организаций
    HOSTS,
    OTHER,
    _only_member_of,
    accounts,
    client_for,
    enforced,
    two_orgs,
)
from school.models import (
    Athlete,
    AthleteContract,
    Club,
    Competition,
    Family,
    FamilyAthleteProfile,
    FamilyMember,
    Person,
)
from tests.factories import AthleteFactory, CompetitionFactory, FamilyFactory, GroupFactory, PersonFactory
from users.constants import COACH_GROUP_NAME
from users.models import User

pytestmark = pytest.mark.django_db


def ksclub():
    return Organization.objects.get(slug="ksclub")


@pytest.fixture
def clubs():
    own = Club.objects.create(name="Канаев Ски Клаб (тест)", organization=ksclub())
    foreign = Club.objects.create(name="Феникс (тест)")
    return own, foreign


def athlete(surname, club=None):
    return AthleteFactory(person=PersonFactory(surname=surname, club=club))


def is_external(a):
    return Person.objects.get(pk=a.person_id).is_external


# --- Правило «внешнего» --------------------------------------------------------------------------------------


def test_external_rule_cases(clubs):
    own, foreign = clubs
    cases = {
        "foreign_no_links": athlete("Чужой", foreign),
        "empty_club": athlete("БезКлуба", None),
        "own_club": athlete("Свой", own),
        "foreign_family": athlete("ЧужойСемья", foreign),
        "foreign_group": athlete("ЧужойГруппа", foreign),
        "foreign_contract": athlete("ЧужойДоговор", foreign),
        "foreign_account": athlete("ЧужойАккаунт", foreign),
    }
    family = FamilyFactory()
    FamilyMember.objects.create(family=family, person=cases["foreign_family"].person, relation="child")
    GroupFactory().athletes.add(cases["foreign_group"])
    profile = FamilyAthleteProfile.objects.create(family=FamilyFactory(), athlete=cases["foreign_contract"])
    AthleteContract.objects.create(
        profile=profile, issue_date=date.today(), start_date=date.today(), end_date=date.today() + timedelta(days=90)
    )
    User.objects.create(email="acc@example.com", person=cases["foreign_account"].person)
    Person.objects.update(is_external=False)  # сигналы могли не сработать для части случаев — проверяем правило

    mark_externals()
    flags = {key: is_external(a) for key, a in cases.items()}
    assert flags == {
        "foreign_no_links": True,  # клуб чужой, связей нет
        "empty_club": False,  # пустой клуб — неясно чей, в отчёт «без клуба»
        "own_club": False,
        "foreign_family": False,
        "foreign_group": False,
        "foreign_contract": False,
        "foreign_account": False,
    }
    assert mark_externals() == 0  # повтор ничего не меняет
    data = report()
    assert data["external"] >= 1 and data["unmarked_candidates"] == 0 and data["marked_with_basis"] == 0
    assert data["without_club"] >= 1


def test_external_becomes_internal_when_link_appears(clubs):
    _, foreign = clubs
    to_group, to_family = athlete("ВГруппу", foreign), athlete("ВСемью", foreign)
    mark_externals()
    assert is_external(to_group) and is_external(to_family)
    GroupFactory().athletes.add(to_group)
    FamilyMember.objects.create(family=FamilyFactory(), person=to_family.person, relation="child")
    assert not is_external(to_group) and not is_external(to_family)


def test_migration_rule_matches_report(clubs):
    _, foreign = clubs
    athlete("ДляМиграции", foreign)
    candidates = Person.objects.filter(external_rule_q("ksclub")).distinct().count()
    assert mark_externals() == candidates


def test_externals_report_command(clubs, capsys):
    _, foreign = clubs
    athlete("Отчётный", foreign)
    call_command("orgs_externals_report", "--apply")
    out = capsys.readouterr().out
    assert "Помечено: " in out and "внешних: " in out and "подходят под правило, но не помечены: 0" in out


# --- Рабочие списки, фильтр, заявки и результаты --------------------------------------------------------------


@pytest.fixture
def staff_client(client):
    user = User.objects.create(email="staff-ext@example.com", is_staff=True, is_superuser=True, is_approved=True)
    client.force_login(user)
    return client


@pytest.mark.parametrize("name", ["school:athlete_list", "school:athlete_simple_list", "members:members_list"])
def test_working_lists_hide_externals_and_filter_shows_them(staff_client, clubs, name):
    _, foreign = clubs
    athlete("Внешнийтест", foreign)
    athlete("Своитест", clubs[0])
    mark_externals()
    default = staff_client.get(reverse(name)).content.decode()
    assert "Своитест" in default and "Внешнийтест" not in default
    only = staff_client.get(reverse(name), {"external": "only"}).content.decode()
    assert "Внешнийтест" in only and "Своитест" not in only
    assert "Внешние спортсмены (из протоколов)" in default


def test_competition_entries_and_results_still_see_externals(staff_client, clubs):
    _, foreign = clubs
    ext = athlete("Заявочный", foreign)
    mark_externals()
    assert is_external(ext)
    competition = CompetitionFactory()
    response = staff_client.post(
        reverse("school:competition_entries_toggle", kwargs={"pk": competition.pk}),
        {"athlete_id": ext.pk, "action": "add"},
    )
    assert response.status_code == 200 and response.json()["success"]
    assert Competition.objects.get(pk=competition.pk).entries.filter(athlete=ext).exists()
    assert Athlete.objects.filter(pk=ext.pk).exists()  # индекс связывания результатов (results.linking) их видит


# --- Рассылки ----------------------------------------------------------------------------------------------


def test_mailings_skip_externals_and_alumni(clubs):
    _, foreign = clubs
    regular = User.objects.create(email="reg@example.com", is_approved=True, tg_id=1001)
    external_user = User.objects.create(email="ext@example.com", is_approved=True, tg_id=1002)
    ext_person = PersonFactory(club=foreign)
    User.objects.filter(pk=external_user.pk).update(person=ext_person)
    Person.objects.filter(pk=ext_person.pk).update(is_external=True)
    alumni_user = User.objects.create(email="alm@example.com", is_approved=True, tg_id=1003)
    Membership.objects.filter(user=alumni_user, organization=ksclub()).update(status=Membership.Status.LEFT)

    recipients = set(mailing_recipients(User.objects.filter(pk__in=[regular.pk, external_user.pk, alumni_user.pk])))
    assert recipients == {regular}

    from notifications.models import Notification
    from notifications.services import deliver_notification

    sent = []
    with patch("notifications.services.BOT_TOKEN", "x"), patch("notifications.services.Bot") as bot:
        bot.return_value.send_message.side_effect = lambda chat_id, text: sent.append(chat_id)
        with patch("notifications.services.async_to_sync", lambda f: f):
            deliver_notification(
                Notification(title="t", message="m", send_to_telegram=True, send_to_email=False),
                users=User.objects.filter(pk__in=[regular.pk, external_user.pk, alumni_user.pk]),
            )
    assert sent == [1001]


# --- Выпускники ---------------------------------------------------------------------------------------------


@pytest.fixture
def families():
    alumni = FamilyFactory(family_name="Выпускники", status=Family.STATUS_ALUMNI)
    active = FamilyFactory(family_name="Действующие", status="active")

    def parent(email, *family_list):
        person = PersonFactory()
        for family in family_list:
            FamilyMember.objects.create(family=family, person=person, relation="parent")
        user = User.objects.create(email=email, is_approved=True, person=person)
        Membership.objects.filter(user=user).update(status=Membership.Status.ACTIVE)
        return user

    return {
        "graduate": parent("grad@example.com", alumni),
        "both": parent("both-fam@example.com", alumni, active),
        "coach_role": parent("coach-grad@example.com", alumni),
        "active_only": parent("active@example.com", active),
        "alumni": alumni,
    }


def test_alumni_marking_is_conservative_and_idempotent(families):
    families["coach_role"].groups.add(AuthGroup.objects.get_or_create(name=COACH_GROUP_NAME)[0])
    dry = mark_alumni(apply=False)
    assert dry.candidates == 1 and dry.marked == 0
    assert Membership.objects.get(user=families["graduate"]).status == Membership.Status.ACTIVE

    applied = mark_alumni(apply=True)
    assert applied.marked == 1
    statuses = {
        k: Membership.objects.get(user=families[k], organization=ksclub()).status for k in families if k != "alumni"
    }
    assert statuses == {
        "graduate": Membership.Status.LEFT,
        "both": Membership.Status.ACTIVE,  # есть действующая семья
        "coach_role": Membership.Status.ACTIVE,  # есть роль
        "active_only": Membership.Status.ACTIVE,
    }
    assert mark_alumni(apply=True).marked == 0


def test_alumni_keep_is_approved_in_legacy_without_implicit_revoke(families, client):
    mark_alumni(apply=True)
    graduate = User.objects.get(pk=families["graduate"].pk)
    assert graduate.is_approved is True
    client.force_login(graduate)
    assert client.get(reverse("members:family_list")).status_code == 200  # история видна
    assert is_history_only(graduate)
    assert client.post(reverse("members:family_list"), {}).status_code == 403  # но только просмотр


def test_alumni_history_access_setting_off_revokes(families):
    org = ksclub()
    org.settings = {**(org.settings or {}), "alumni_history_access": False}
    org.save(update_fields=["settings"])
    mark_alumni(apply=True)
    assert User.objects.get(pk=families["graduate"].pk).is_approved is False


def test_alumni_in_memberships_mode_read_only_history(families, client, settings):
    mark_alumni(apply=True)
    settings.ORGS_ROLES_SOURCE = "memberships"
    graduate = families["graduate"]
    client.force_login(graduate)
    assert client.get(reverse("members:family_list")).status_code == 200
    assert client.post(reverse("groups:group_create"), {"name": "x"}).status_code == 403
    assert client.get(reverse("groups:group_create")).status_code == 403  # роли сотрудника нет


# --- D15: наследие трансляций только в ksclub -------------------------------------------------------------


def _legacy_run():
    run = StreamRun.objects.create(
        stream_id="legacy-stream",
        protocol_link="https://docs.google.com/spreadsheets/d/test",
        callback_url="https://example.com/callback",
        external_response_json={"stream_output": {"overall_stats_lines": ["x"]}},
    )
    access = PublicStreamAccess.objects.create(
        stream_run=run, token="legacy-public", expires_at=timezone.now() + timedelta(days=2), is_active=True
    )
    return run, access


LEGACY_STAFF_PAGES = ["online-results-live", "online-results-stream-runs", "online-results-webhook-events"]


@pytest.mark.parametrize("name", LEGACY_STAFF_PAGES)
def test_d15_legacy_admin_pages_only_on_ksclub(enforced, name):
    manager = enforced.superuser
    assert client_for(manager, "KS").get(reverse(name)).status_code == 200
    assert client_for(manager, "OT").get(reverse(name)).status_code == 404


@patch("api.views._probe_online_results_stream_state", return_value={"ok": True, "found": True})
def test_d15_legacy_public_link_only_on_ksclub(_probe, enforced):
    _, access = _legacy_run()
    for name in ("online-results-live-public", "online-results-live-public-state"):
        url = reverse(name, kwargs={"token": access.token})
        assert client_for(None, "KS").get(url).status_code == 200
        assert client_for(None, "OT").get(url).status_code == 404


def test_d15_member_of_ksclub_has_no_legacy_access_via_other_host(accounts):
    owner = accounts.users["owner_KS"]
    assert client_for(owner, "KS").get(reverse("online-results-live")).status_code == 200
    # без членства в чужой организации: отказ правами представления (403) или изоляцией наследия (404)
    assert client_for(owner, "OT").get(reverse("online-results-live")).status_code in (403, 404)


def test_d15_webhook_only_on_ksclub(enforced):
    url = reverse("online-results-webhook")
    assert client_for(None, "OT").post(url, b"{}", content_type="application/json").status_code == 404
    assert client_for(None, "KS").post(url, b"{}", content_type="application/json").status_code != 404


@patch("api.views._probe_online_results_stream_state", return_value={"ok": True, "found": True})
def test_d15_flag_off_keeps_r1(_probe, two_orgs, settings):
    settings.ORGS_ENFORCE_SCOPING = False
    _, access = _legacy_run()
    url = reverse("online-results-live-public", kwargs={"token": access.token})
    assert client_for(None, "OT").get(url).status_code == 200


# --- D16: глобальная учётная запись не меняется CRM-ролью ----------------------------------------------------


@pytest.mark.parametrize("tag", ["KS", "OT"])
def test_d16_owner_cannot_write_shared_account(accounts, tag):
    owner = client_for(accounts.users[f"owner_{tag}"], tag)
    shared = accounts.users["both"]
    url = reverse("api:user-detail", kwargs={"pk": shared.pk})
    assert owner.get(url).status_code == 200  # чтение по членству
    assert owner.patch(url, {"is_active": False}, content_type="application/json").status_code == 403
    assert owner.put(url, {"email": "x@example.com"}, content_type="application/json").status_code == 403
    assert owner.delete(url).status_code == 403
    assert owner.post(reverse("api:user-list"), {"email": "new@example.com"}).status_code == 403
    shared.refresh_from_db()
    assert shared.is_active and User.objects.filter(pk=shared.pk).exists()


def test_d16_local_owner_is_not_platform_admin(accounts):
    owner = accounts.users["owner_KS"]
    assert not PlatformRole.objects.filter(user=owner).exists()


def test_d16_platform_admin_can_write(accounts):
    admin = _only_member_of("platform@example.com", accounts.orgs["KS"], OrgRole.OWNER)
    PlatformRole.objects.create(user=admin, role=PlatformRole.Role.GLOBAL_ADMIN)
    url = reverse("api:user-detail", kwargs={"pk": accounts.users["both"].pk})
    response = client_for(admin, "KS").patch(url, {"first_name": "Платформа"}, content_type="application/json")
    assert response.status_code == 200


def test_d16_flag_off_keeps_r1_writes(two_orgs, settings):
    settings.ORGS_ENFORCE_SCOPING = False
    target = User.objects.create(email="r1-target@example.com", is_approved=True)
    url = reverse("api:user-detail", kwargs={"pk": target.pk})
    response = client_for(two_orgs.superuser, "KS").patch(url, {"first_name": "R1"}, content_type="application/json")
    assert response.status_code == 200


def test_d16_approval_in_one_organization_does_not_touch_other(accounts):
    shared = accounts.users["both"]
    with organization_context(accounts.orgs["OT"]):
        assert set_user_approval(shared, False) == []
    assert Membership.objects.get(user=shared, organization=accounts.orgs["OT"]).status == Membership.Status.SUSPENDED
    assert Membership.objects.get(user=shared, organization=accounts.orgs["KS"]).status == Membership.Status.ACTIVE
    shared.refresh_from_db()
    assert shared.is_approved is True  # глобальный флаг не тронут
    assert not RoleAssignment.objects.filter(membership__user=shared).exists()


# --- R2-7: stage4_check --release r2 ---------------------------------------------------------------------------


def _check(capsys):
    from organizations.legacy import import_legacy

    import_legacy()
    call_command("stage4_check", "--release", "r2")
    return capsys.readouterr().out


@pytest.mark.parametrize("roles,scoping", [("legacy", False), ("memberships", False), ("memberships", True)])
def test_stage4_check_r2_reports_modes_and_passes(capsys, settings, roles, scoping):
    settings.ORGS_ROLES_SOURCE, settings.ORGS_ENFORCE_SCOPING = roles, scoping
    out = _check(capsys)
    assert f"ORGS_ROLES_SOURCE={roles}, ORGS_ENFORCE_SCOPING={scoping}" in out
    assert "без фильтра по умолчанию: 0" in out and "✓ stage4_check r2" in out


def test_stage4_check_r2_fails_on_scoping_before_memberships(capsys, settings):
    from django.core.management.base import CommandError

    settings.ORGS_ROLES_SOURCE, settings.ORGS_ENFORCE_SCOPING = "legacy", True
    with pytest.raises(CommandError):
        _check(capsys)


def test_stage4_check_r2_fails_when_external_flag_diverges(capsys, clubs):
    from django.core.management.base import CommandError

    athlete("НеПомечен", clubs[1])  # подходит под правило, но не помечен
    with pytest.raises(CommandError):
        _check(capsys)
    mark_externals()
    assert "✓ stage4_check r2" in _check(capsys)


def test_stage4_check_r2_fails_on_left_with_role(capsys, families):
    from django.core.management.base import CommandError

    m = Membership.objects.get(user=families["graduate"], organization=ksclub())
    m.status = Membership.Status.LEFT
    m.save()
    RoleAssignment.objects.create(membership=m, role=OrgRole.objects.get(code=OrgRole.COACH), source="manual")
    with pytest.raises(CommandError):
        _check(capsys)
