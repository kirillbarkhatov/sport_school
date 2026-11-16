# ruff: noqa: F811 — фикстуры двух организаций импортируются из test_scoping
"""T08 (R2-6): обязательная организация у всех OrgOwned и уникальности внутри организации."""

import threading

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.forms import modelform_factory
from django.urls import reverse

from organizations.backfill import org_owned_models
from organizations.context import NO_ORGANIZATION, organization_context
from organizations.tests.test_scoping import HOSTS, client_for, enforced, two_orgs  # noqa: F401
from school.models import FamilyAthleteProfile, Person
from tests.factories import AthleteFactory, FamilyFactory
from whatsapp.models import WhatsAppChat

pytestmark = pytest.mark.django_db


# --- NOT NULL ------------------------------------------------------------------------------------------------


def test_organization_required_in_models_and_database():
    models = org_owned_models()
    assert len(models) == 45
    assert all(not m._meta.get_field("organization").null for m in models)
    from organizations.management.commands.stage4_check import _required_organization_columns

    assert _required_organization_columns(models) == 45


def test_row_without_organization_is_rejected_by_database():
    with organization_context(NO_ORGANIZATION), pytest.raises(IntegrityError), transaction.atomic():
        Person.objects.create(name="Без", surname="Организации", gender="male")


def test_owner_or_context_still_fills_organization(two_orgs):
    ot = two_orgs.orgs["OT"]
    with organization_context(ot):
        person = Person.objects.create(name="Есть", surname="Контекст", gender="male")
        family = FamilyFactory()
    with organization_context(NO_ORGANIZATION):
        profile = FamilyAthleteProfile.objects.create(family=family, athlete=AthleteFactory(person=person))
    assert person.organization == ot and profile.organization == ot  # из владельца, даже без контекста


def test_stage4_check_reports_r26(capsys):
    from organizations.legacy import import_legacy

    import_legacy()
    call_command("stage4_check", "--release", "r2")
    assert "R2-6: обязательная организация (NOT NULL) у 45 из 45 моделей" in capsys.readouterr().out


# --- Уникальность внутри организации -------------------------------------------------------------------------


def test_same_chat_name_allowed_in_different_organizations(two_orgs):
    for tag in ("KS", "OT"):
        with organization_context(two_orgs.orgs[tag]):
            WhatsAppChat.objects.create(name="Родители 2015")
    assert WhatsAppChat.all_objects.filter(name="Родители 2015").count() == 2


def test_duplicate_chat_name_inside_organization_is_rejected(two_orgs):
    with organization_context(two_orgs.orgs["KS"]):
        WhatsAppChat.objects.create(name="Тренеры")
        with pytest.raises(ValidationError):
            WhatsAppChat(name="Тренеры").full_clean()  # проверка до базы, с полем organization
        with pytest.raises(IntegrityError), transaction.atomic():
            WhatsAppChat.objects.create(name="Тренеры")  # и ограничение в базе


@pytest.mark.parametrize("scoping", [False, True])
def test_modelform_reports_duplicate_instead_of_500(two_orgs, settings, scoping):
    settings.ORGS_ENFORCE_SCOPING = scoping
    Form = modelform_factory(WhatsAppChat, fields=["name"])
    with organization_context(two_orgs.orgs["KS"]):
        WhatsAppChat.objects.create(name="Общий чат")
        form = Form(data={"name": "Общий чат"})
        assert not form.is_valid() and form.errors
    with organization_context(two_orgs.orgs["OT"]):
        assert Form(data={"name": "Общий чат"}).is_valid()


def test_parent_scoped_constraints_still_validated(two_orgs):
    with organization_context(two_orgs.orgs["KS"]):
        family, athlete = FamilyFactory(), AthleteFactory()
        FamilyAthleteProfile.objects.create(family=family, athlete=athlete)
        with pytest.raises(ValidationError):
            FamilyAthleteProfile(family=family, athlete=athlete).full_clean()


def _sync(client, name):
    payload = {"group_name": name, "synced_at": "2026-11-16T10:00:00Z", "members": []}
    return client.post(reverse("api:whatsapp-chat-members"), payload, content_type="application/json")


def test_whatsapp_sync_same_group_name_in_second_organization(enforced, settings):
    """До R2-6: при изоляции второй клуб с тем же именем группы получал IntegrityError (глобальная уникальность),
    а повторный get в своей организации не находил запись — 500."""
    settings.WHATSAPP_SYNC_TOKEN = ""
    for tag in ("KS", "OT", "OT"):  # повтор — идемпотентно
        response = _sync(client_for(None, tag), "Родители")
        assert response.status_code == 200, response.content
    assert set(WhatsAppChat.all_objects.filter(name="Родители").values_list("organization__slug", flat=True)) == {
        "ksclub",
        "otherclub",
    }


# --- Конкурентные записи -------------------------------------------------------------------------------------
# Без transaction=True (он очищает таблицы и данные миграций): все операции гонки идут в отдельных потоках
# со своими соединениями и коммитами; подготовку и очистку тоже выполняет отдельный поток.


def _in_thread(fn, *args):
    box = {}

    def run():
        try:
            box["value"] = fn(*args)
        except Exception as exc:  # noqa: BLE001 — возвращаем исключение вызывающему
            box["error"] = exc
        finally:
            connection.close()

    thread = threading.Thread(target=run)
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _race(orgs, action):
    barrier = threading.Barrier(len(orgs))
    results = []

    def worker(org):
        try:
            with organization_context(org):
                barrier.wait()
                results.append(("ok", org.slug, action()))
        except IntegrityError:
            results.append(("dup", org.slug, None))
        finally:
            close_old_connections()
            connection.close()

    threads = [threading.Thread(target=worker, args=(org,)) for org in orgs]
    [t.start() for t in threads]
    [t.join() for t in threads]
    return results


def _setup_orgs():
    from organizations.models import Organization

    ks = Organization.objects.get(slug="ksclub")
    other, _ = Organization.objects.get_or_create(slug="race-other", defaults={"name": "Гонка"})
    return ks, other


def _cleanup():
    from organizations.models import Organization

    WhatsAppChat.all_objects.filter(name__in=["Гонка", "Параллельно"]).delete()
    Organization.objects.filter(slug="race-other").delete()


def test_concurrent_inserts_one_wins_inside_organization_both_across():
    ks, other = _in_thread(_setup_orgs)

    def create():
        with transaction.atomic():
            return WhatsAppChat.objects.create(name="Гонка").pk

    try:
        same = _race([ks, ks], create)
        assert sorted(r[0] for r in same) == ["dup", "ok"]  # внутри организации побеждает один
        _in_thread(lambda: WhatsAppChat.all_objects.filter(name="Гонка").delete())
        across = _race([ks, other], create)
        assert sorted((r[0], r[1]) for r in across) == [("ok", "ksclub"), ("ok", "race-other")]  # между — оба
    finally:
        _in_thread(_cleanup)


def test_concurrent_get_or_create_returns_single_chat():
    ks, _ = _in_thread(_setup_orgs)
    try:
        results = _race([ks, ks, ks], lambda: WhatsAppChat.objects.get_or_create(name="Параллельно")[0].pk)
        assert [r[0] for r in results] == ["ok", "ok", "ok"]
        assert len({r[2] for r in results}) == 1  # get_or_create ловит IntegrityError и возвращает ту же запись
    finally:
        _in_thread(_cleanup)
