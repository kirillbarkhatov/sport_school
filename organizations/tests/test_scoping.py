"""T05 (R2-3, R2-4, D08): организация в данных, изоляция при ORGS_ENFORCE_SCOPING, контекст задач и потоков.

Фикстура two_orgs создаёт полный набор данных в двух организациях; каждая проверка идёт с обеих сторон.
"""

import contextvars
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import transaction
from django.db.models import Count
from django.test import Client
from django.urls import reverse

from organizations import context
from organizations.backfill import backfill_missing, missing_organization, org_owned_models
from organizations.context import NO_ORGANIZATION, organization_context
from organizations.legacy import import_legacy
from organizations.migration_utils import backfill_organization
from organizations.models import Membership, Organization, OrgDomain, OrgRole, RoleAssignment
from organizations.permissions import authorize
from results.models import ResultsEvent
from school.models import Athlete, CompetitionApplicationLink, FamilyService, Group, Person
from tests.factories import (
    AthleteFactory,
    ClassFactory,
    CompetitionFactory,
    FamilyFactory,
    GroupFactory,
    NotificationFactory,
    PersonFactory,
)
from users.models import User

pytestmark = pytest.mark.django_db

HOSTS = {"KS": "ksclub.ru", "OT": "other.example"}


def _build(tag):
    competition = CompetitionFactory(name=f"Кубок{tag}")
    family = FamilyFactory(family_name=f"Семья{tag}")
    return SimpleNamespace(
        person=PersonFactory(surname=f"Фамилия{tag}"),
        athlete=AthleteFactory(person=PersonFactory(surname=f"Спортсмен{tag}")),
        family=family,
        service=FamilyService.objects.create(family=family, name=f"Абонемент{tag}"),
        group=GroupFactory(name=f"Группа{tag}"),
        klass=ClassFactory(),
        competition=competition,
        notification=NotificationFactory(title=f"Уведомление{tag}"),
        link=CompetitionApplicationLink.objects.create(competition=competition, token=f"token-{tag}"),
        event=ResultsEvent.objects.create(
            title=f"Эфир{tag}", source_kind=ResultsEvent.SourceKind.MANUAL, status=ResultsEvent.Status.RUNNING
        ),
    )


@pytest.fixture
def two_orgs(settings):
    settings.ALLOWED_HOSTS = ["*"]
    settings.ORGS_ROLES_SOURCE = "legacy"
    cache.clear()
    ks = Organization.objects.get(slug="ksclub")
    other = Organization.objects.create(slug="otherclub", name="Другой клуб")
    OrgDomain.objects.create(organization=other, host=HOSTS["OT"])
    data = {}
    for org, tag in ((ks, "KS"), (other, "OT")):
        with organization_context(org):
            data[tag] = _build(tag)
    superuser = User.objects.create(email="su-scope@example.com", is_staff=True, is_superuser=True, is_approved=True)
    yield SimpleNamespace(orgs={"KS": ks, "OT": other}, data=data, superuser=superuser)
    cache.clear()


def client_for(user, tag):
    client = Client(HTTP_HOST=HOSTS[tag])
    if user is not None:
        client.force_login(user)
    return client


OTHER = {"KS": "OT", "OT": "KS"}


# --- Перенос старых данных -------------------------------------------------------------------------------


def test_every_org_owned_model_has_organization_field():
    assert len(org_owned_models()) == 45


def test_historical_rows_backfilled_idempotently(two_orgs):
    # R2-6: строк без организации быть не может — база отвергает NULL; заполнение остаётся идемпотентным no-op.
    # Заполнение реальных NULL проверено до R2-6 (T05: staging и прод, 2209 строк → ksclub).
    from django.db import IntegrityError

    with pytest.raises(IntegrityError), transaction.atomic():
        Person.all_objects.filter(organization=two_orgs.orgs["KS"]).update(organization=None)
    assert missing_organization() == {}
    assert backfill_missing() == {}
    assert backfill_missing() == {}
    assert Person.all_objects.filter(organization=two_orgs.orgs["OT"]).count() == 2  # чужие строки не тронуты


def test_migration_backfill_function_is_idempotent(two_orgs):
    from django.apps import apps

    by_org = dict(Athlete.all_objects.values_list("organization__slug").annotate(n=Count("pk")).order_by())
    operation = backfill_organization("school", ["athlete"])
    operation.code(apps, None)
    operation.code(apps, None)
    assert dict(Athlete.all_objects.values_list("organization__slug").annotate(n=Count("pk")).order_by()) == by_org


def test_orgs_backfill_command_and_stage4_check_r2(two_orgs, capsys):
    import_legacy()
    call_command("orgs_backfill")
    assert "Без организации: 0" in capsys.readouterr().out
    call_command("orgs_backfill", "--apply")
    assert "Заполнено строк: 0" in capsys.readouterr().out
    call_command("stage4_check", "--release", "r2")
    assert "строк без организации: 0" in capsys.readouterr().out


def test_new_rows_take_organization_from_owner_then_context(two_orgs):
    ot = two_orgs.data["OT"]
    with organization_context(two_orgs.orgs["KS"]):
        service = FamilyService.objects.create(family=ot.family, name="Наследует")
        loose = Person.objects.create(name="Без", surname="Владельца", gender="male")
        bulk = Person.objects.bulk_create([Person(name="Пакет", surname="Пакетов", gender="male")])
    assert service.organization == two_orgs.orgs["OT"]
    assert loose.organization == two_orgs.orgs["KS"]
    assert bulk[0].organization_id == two_orgs.orgs["KS"].pk


# --- Флаг выключен: один клуб как в R1 --------------------------------------------------------------------


def test_flag_off_keeps_r1_behaviour(two_orgs, settings):
    settings.ORGS_ENFORCE_SCOPING = False
    page = client_for(two_orgs.superuser, "KS").get(reverse("groups:group_list")).content.decode()
    assert "ГруппаKS" in page and "ГруппаOT" in page
    assert {"ГруппаKS", "ГруппаOT"} <= set(Group.objects.values_list("name", flat=True))
    ks_group = two_orgs.data["KS"].group
    ks_group.athletes.add(two_orgs.data["OT"].athlete)  # проверки связей выключены вместе с флагом


# --- Флаг включён: изоляция страниц, форм, API, выгрузок ---------------------------------------------------


@pytest.fixture
def enforced(two_orgs, settings):
    settings.ORGS_ENFORCE_SCOPING = True
    return two_orgs


LIST_PAGES = [
    ("groups:group_list", "Группа{}"),
    ("school:competition_list", "Кубок{}"),
    ("members:family_list", "Семья{}"),
    ("members:members_list", "Фамилия{}"),
    ("school:athlete_list", "Спортсмен{}"),
    ("notifications:notification_list", "Уведомление{}"),
]


@pytest.mark.parametrize("tag", ["KS", "OT"])
@pytest.mark.parametrize("name,label", LIST_PAGES)
def test_list_pages_show_only_own_organization(enforced, tag, name, label):
    page = client_for(enforced.superuser, tag).get(reverse(name)).content.decode()
    assert label.format(tag) in page
    assert label.format(OTHER[tag]) not in page


DETAIL_PAGES = [
    ("members:members_detail", lambda d: {"pk": d.person.pk}),
    ("members:family_detail", lambda d: {"pk": d.family.pk}),
    ("groups:group_detail", lambda d: {"pk": d.group.pk}),
    ("classes:class_detail", lambda d: {"pk": d.klass.pk}),
    ("school:competition_edit", lambda d: {"pk": d.competition.pk}),
    ("school:competition_groups", lambda d: {"pk": d.competition.pk}),
    ("school:competition_export", lambda d: {"pk": d.competition.pk}),
    ("school:competition_export_word_type1", lambda d: {"pk": d.competition.pk}),
    ("members:family_service_update", lambda d: {"pk": d.family.pk, "service_pk": d.service.pk}),
]


@pytest.mark.parametrize("tag", ["KS", "OT"])
@pytest.mark.parametrize("name,kwargs", DETAIL_PAGES)
def test_foreign_ids_are_not_found(enforced, tag, name, kwargs):
    client = client_for(enforced.superuser, tag)
    assert client.get(reverse(name, kwargs=kwargs(enforced.data[tag]))).status_code == 200
    assert client.get(reverse(name, kwargs=kwargs(enforced.data[OTHER[tag]]))).status_code == 404


@pytest.mark.parametrize("tag", ["KS", "OT"])
def test_post_with_foreign_id_is_rejected(enforced, tag):
    client = client_for(enforced.superuser, tag)
    url = reverse("groups:group_create")
    foreign = client.post(url, {"name": "Чужая", "athletes": [enforced.data[OTHER[tag]].athlete.pk]})
    assert foreign.status_code == 200  # форма с ошибкой выбора
    assert not Group.all_objects.filter(name="Чужая").exists()
    own = client.post(url, {"name": "Своя", "athletes": [enforced.data[tag].athlete.pk]})
    assert own.status_code == 302
    assert Group.all_objects.get(name="Своя").organization == enforced.orgs[tag]


@pytest.mark.parametrize("tag", ["KS", "OT"])
def test_post_update_of_foreign_object_is_not_found(enforced, tag):
    client = client_for(enforced.superuser, tag)
    foreign = enforced.data[OTHER[tag]].group
    response = client.post(reverse("groups:group_update", kwargs={"pk": foreign.pk}), {"name": "Взлом"})
    assert response.status_code == 404
    assert Group.all_objects.get(pk=foreign.pk).name == f"Группа{OTHER[tag]}"


@pytest.mark.parametrize("tag", ["KS", "OT"])
def test_api_lists_only_own_organization(enforced, tag):
    body = client_for(enforced.superuser, tag).get(reverse("api:person-list")).content.decode()
    assert f"Фамилия{tag}" in body
    assert f"Фамилия{OTHER[tag]}" not in body


def test_model_level_links_across_organizations_are_rejected(enforced):
    ks, ot = enforced.data["KS"], enforced.data["OT"]
    with pytest.raises(ValidationError):
        FamilyService(family=ot.family, name="Связь", organization=enforced.orgs["KS"]).save()
    # add() идёт в atomic(savepoint=False): в тесте нужна своя точка сохранения
    with organization_context(enforced.orgs["KS"]), pytest.raises(ValidationError), transaction.atomic():
        ks.group.athletes.add(ot.athlete)
    with organization_context(enforced.orgs["OT"]), pytest.raises(ValidationError), transaction.atomic():
        ot.group.athletes.add(ks.athlete)
    assert not ks.group.athletes.filter(pk=ot.athlete.pk).exists()


# --- Публичные ссылки -------------------------------------------------------------------------------------


@pytest.mark.parametrize("tag", ["KS", "OT"])
def test_public_results_link_works_only_inside_own_organization(enforced, tag):
    token = enforced.data[tag].event.public_token
    url = reverse("results:public_live", kwargs={"token": token})
    assert Client(HTTP_HOST=HOSTS[tag]).get(url).status_code != 404
    assert Client(HTTP_HOST=HOSTS[OTHER[tag]]).get(url).status_code == 404


@pytest.mark.parametrize("tag", ["KS", "OT"])
def test_application_link_works_only_inside_own_organization(enforced, tag):
    data = enforced.data[tag]
    url = reverse("school:competition_apply", kwargs={"pk": data.competition.pk, "token": data.link.token})
    parent = User.objects.create(email=f"parent-{tag}@example.com", is_approved=True)
    assert client_for(parent, tag).get(url).status_code == 200
    assert client_for(parent, OTHER[tag]).get(url).status_code == 404


def test_suspended_organization_host_does_not_fall_back_to_ksclub(enforced):
    Organization.objects.filter(pk=enforced.orgs["OT"].pk).update(status=Organization.Status.SUSPENDED)
    cache.clear()
    page = client_for(enforced.superuser, "OT").get(reverse("groups:group_list")).content.decode()
    assert "ГруппаKS" not in page and "ГруппаOT" not in page


# --- Пользователь с двумя членствами и без членства (режим memberships) ----------------------------------


def test_user_with_two_memberships_gets_roles_per_organization(enforced, settings):
    settings.ORGS_ROLES_SOURCE = "memberships"
    user = User.objects.create(email="two@example.com", is_approved=True)
    for tag, role in (("KS", OrgRole.MANAGER), ("OT", OrgRole.COACH)):
        m, _ = Membership.objects.update_or_create(
            user=user, organization=enforced.orgs[tag], defaults={"status": Membership.Status.ACTIVE}
        )
        RoleAssignment.objects.get_or_create(membership=m, role=OrgRole.objects.get(code=role))
    assert authorize(user, "people.manage", organization=enforced.orgs["KS"])
    assert not authorize(user, "people.manage", organization=enforced.orgs["OT"])
    for tag in ("KS", "OT"):
        page = client_for(user, tag).get(reverse("school:competition_list")).content.decode()
        assert f"Кубок{tag}" in page and f"Кубок{OTHER[tag]}" not in page
    assert client_for(user, "KS").get(reverse("members:members_create")).status_code == 403  # не privileged


def test_user_without_membership_gets_nothing_in_other_organization(enforced, settings):
    settings.ORGS_ROLES_SOURCE = "memberships"
    user = User.objects.create(email="only-ks@example.com", is_approved=True)
    Membership.objects.filter(user=user).update(status=Membership.Status.ACTIVE)
    assert client_for(user, "KS").get(reverse("groups:group_list")).status_code == 200
    response = client_for(user, "OT").get(reverse("groups:group_list"))
    assert response.status_code == 302 and reverse("users:awaiting_approval") in response["Location"]


# --- Бот ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("tag", ["KS", "OT"])
def test_bot_services_see_only_current_organization(enforced, tag):
    from tg_bot.services.family_overview import get_family_overview

    own, foreign = enforced.data[tag].family, enforced.data[OTHER[tag]].family
    with organization_context(enforced.orgs[tag]):
        ids = set(enforced.superuser.get_accessible_family_ids())
        overview = get_family_overview(enforced.superuser)
    assert own.pk in ids and foreign.pk not in ids
    text = repr(overview)
    assert foreign.family_name not in text
    default_ids = set(enforced.superuser.get_accessible_family_ids())  # без контекста — организация по умолчанию
    assert enforced.data["KS"].family.pk in default_ids and enforced.data["OT"].family.pk not in default_ids


# --- Celery: заголовок организации, старые сообщения, периодические задачи (D08) --------------------------


def _fake_task(**request):
    return SimpleNamespace(request=SimpleNamespace(**request))


def test_publish_adds_organization_header_from_context(enforced):
    headers = {}
    context._before_task_publish(headers=headers)
    assert headers == {}  # контекст не задан — заголовка нет
    with organization_context(enforced.orgs["OT"]):
        context._before_task_publish(headers=headers)
    assert headers == {context.ORG_HEADER: enforced.orgs["OT"].pk}


@pytest.mark.parametrize("tag", ["KS", "OT"])
def test_task_with_header_runs_in_its_organization_and_resets(enforced, tag):
    task = _fake_task(organization_id=enforced.orgs[tag].pk)
    context._task_prerun(task_id="t1", task=task, kwargs={})
    try:
        assert context.get_current_organization() == enforced.orgs[tag]
        assert context.task_has_explicit_organization()
        names = set(Group.objects.values_list("name", flat=True))
        assert f"Группа{tag}" in names and f"Группа{OTHER[tag]}" not in names
    finally:
        context._task_postrun(task_id="t1")
    assert not context.context_is_set()
    assert not context.task_has_explicit_organization()


def test_old_message_without_header_gets_default_organization(enforced):
    context._task_prerun(task_id="old", task=_fake_task(), kwargs={})
    try:
        assert context.get_current_organization() == enforced.orgs["KS"]
        assert not context.task_has_explicit_organization()
    finally:
        context._task_postrun(task_id="old")


@pytest.mark.parametrize("status", ["suspended", "missing"])
def test_task_for_inactive_or_unknown_organization_does_not_fall_back(enforced, status):
    org_id = enforced.orgs["OT"].pk
    if status == "suspended":
        Organization.objects.filter(pk=org_id).update(status=Organization.Status.SUSPENDED)
    else:
        org_id = 987654
    context._task_prerun(task_id="bad", task=_fake_task(organization_id=org_id), kwargs={})
    try:
        assert context.get_current_organization() is None
        assert context.context_is_set()
        assert Group.objects.count() == 0
    finally:
        context._task_postrun(task_id="bad")


def test_periodic_task_fans_out_per_active_organization(enforced):
    from classes.tasks import ensure_weekly_schedule_task

    Organization.objects.create(slug="sleeping", name="Спит", status=Organization.Status.SUSPENDED)
    with patch.object(ensure_weekly_schedule_task, "apply_async") as apply_async:
        result = ensure_weekly_schedule_task.apply().get()
    assert result == {"per_organization": ["ksclub", "otherclub"]}
    sent = [call.kwargs["headers"][context.ORG_HEADER] for call in apply_async.call_args_list]
    assert sent == [enforced.orgs["KS"].pk, enforced.orgs["OT"].pk]


def test_periodic_task_called_directly_runs_in_current_context(enforced):
    from classes.tasks import ensure_weekly_schedule_task

    with patch.object(ensure_weekly_schedule_task, "apply_async") as apply_async:
        ensure_weekly_schedule_task()
    apply_async.assert_not_called()


# --- Воркер results и потоки ------------------------------------------------------------------------------


def test_results_supervisor_sees_all_and_runner_uses_event_organization(enforced):
    from results.worker import EventRunner, Supervisor

    started = set()
    supervisor = Supervisor()
    with (
        patch.object(EventRunner, "start", lambda self: started.add(self.event_id)),
        patch("results.worker.close_old_connections"),
        organization_context(enforced.orgs["KS"]),
    ):
        supervisor.scan()
    assert {enforced.data["KS"].event.pk, enforced.data["OT"].event.pk} <= started

    seen = {}

    def tick(self):
        seen[self.event_id] = (context.get_current_organization(), ResultsEvent.objects.count())
        self.stop_event.set()
        return 0

    for tag in ("KS", "OT"):
        with (
            patch.object(EventRunner, "tick", tick),
            patch.object(EventRunner, "wait", lambda self, pause: None),
            patch("results.worker.close_old_connections"),  # не закрывать соединение тестовой транзакции
        ):
            # run() задаёт контекст потока без сброса — в тесте выполняем в копии контекста
            contextvars.copy_context().run(EventRunner(enforced.data[tag].event.pk).run)
        assert seen[enforced.data[tag].event.pk] == (enforced.orgs[tag], 1)


def test_context_does_not_leak_into_new_threads(enforced):
    seen = {}
    with organization_context(enforced.orgs["OT"]):
        thread = threading.Thread(target=lambda: seen.update(set=context.context_is_set()))
        thread.start()
        thread.join()
    assert seen == {"set": False}


def test_requests_do_not_leak_context(enforced):
    client_for(enforced.superuser, "OT").get(reverse("groups:group_list"))
    assert not context.context_is_set()
    assert context.get_current_organization() is None
    assert NO_ORGANIZATION is not None


# --- D13: учётные записи в CRM-выборках и API ограничены членством в текущей организации ---------------


def _only_member_of(email, organization, role=None):
    user = User.objects.create(email=email, is_approved=True)
    Membership.objects.filter(user=user).delete()  # синхронизация создала членство в ksclub
    m = Membership.objects.create(user=user, organization=organization, status=Membership.Status.ACTIVE)
    if role:
        RoleAssignment.objects.create(membership=m, role=OrgRole.objects.get(code=role))
    return user


@pytest.fixture
def accounts(enforced, settings):
    """Режим memberships: владельцы обеих организаций, участники только одной и обеих."""
    settings.ORGS_ROLES_SOURCE = "memberships"
    users = {
        "owner_KS": _only_member_of("owner-ks@example.com", enforced.orgs["KS"], OrgRole.OWNER),
        "owner_OT": _only_member_of("owner-ot@example.com", enforced.orgs["OT"], OrgRole.OWNER),
        "member_KS": _only_member_of("member-ks@example.com", enforced.orgs["KS"]),
        "member_OT": _only_member_of("member-ot@example.com", enforced.orgs["OT"]),
        "both": _only_member_of("both@example.com", enforced.orgs["KS"]),
    }
    Membership.objects.create(user=users["both"], organization=enforced.orgs["OT"], status=Membership.Status.ACTIVE)
    return SimpleNamespace(**vars(enforced), users=users)


def _own_and_foreign(accounts, tag):
    own = {accounts.users[f"owner_{tag}"], accounts.users[f"member_{tag}"], accounts.users["both"]}
    foreign = {accounts.users[f"owner_{OTHER[tag]}"], accounts.users[f"member_{OTHER[tag]}"]}
    return own, foreign


@pytest.mark.parametrize("tag", ["KS", "OT"])
def test_d13_api_user_list_only_accounts_of_current_organization(accounts, tag):
    response = client_for(accounts.users[f"owner_{tag}"], tag).get(reverse("api:user-list"))
    assert response.status_code == 200
    body = response.content.decode()
    own, foreign = _own_and_foreign(accounts, tag)
    assert all(user.email in body for user in own)
    assert not any(user.email in body for user in foreign)


@pytest.mark.parametrize("tag", ["KS", "OT"])
def test_d13_api_user_detail_of_foreign_account_is_not_found(accounts, tag):
    client = client_for(accounts.users[f"owner_{tag}"], tag)
    own, foreign = _own_and_foreign(accounts, tag)
    for user in own:
        assert client.get(reverse("api:user-detail", kwargs={"pk": user.pk})).status_code == 200
    for user in foreign:
        assert client.get(reverse("api:user-detail", kwargs={"pk": user.pk})).status_code == 404
        assert (
            client.patch(
                reverse("api:user-detail", kwargs={"pk": user.pk}),
                {"first_name": "Взлом"},
                content_type="application/json",
            ).status_code
            in (403, 404)  # запись глобальной учётки закрыта для CRM-ролей (D16) раньше фильтра (D13)
        )
        user.refresh_from_db()
        assert user.first_name != "Взлом"


@pytest.mark.parametrize("tag", ["KS", "OT"])
def test_d13_owner_has_no_api_access_in_foreign_organization(accounts, tag):
    client = client_for(accounts.users[f"owner_{tag}"], OTHER[tag])
    assert client.get(reverse("api:user-list")).status_code == 403
    assert client.get(reverse("api:person-list")).status_code == 403


@pytest.mark.parametrize("tag", ["KS", "OT"])
def test_d13_crm_user_selections_follow_organization(accounts, tag):
    from asgiref.sync import async_to_sync

    from organizations.scoping import users_in_organization
    from tg_bot.handlers.manager import _load_user_by_id, _search_users

    own, foreign = _own_and_foreign(accounts, tag)
    with organization_context(accounts.orgs[tag]):
        selected = set(users_in_organization())
        found = set(async_to_sync(_search_users)("", limit=1000))
        loaded_foreign = [async_to_sync(_load_user_by_id)(user.pk) for user in foreign]
    assert own <= selected and not (foreign & selected)
    assert own <= found and not (foreign & found)
    assert loaded_foreign == [None] * len(foreign)
    with organization_context(NO_ORGANIZATION):
        assert not users_in_organization().exists()


def test_d13_flag_off_keeps_r1_user_lists(accounts, settings):
    from organizations.scoping import users_in_organization

    settings.ORGS_ENFORCE_SCOPING = False
    settings.ORGS_ROLES_SOURCE = "legacy"
    with organization_context(accounts.orgs["KS"]):
        assert set(accounts.users.values()) <= set(users_in_organization())
    body = client_for(accounts.superuser, "KS").get(reverse("api:user-list")).content.decode()
    assert all(user.email in body for user in accounts.users.values())
