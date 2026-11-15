"""Сквозные сценарии этапа 4 (docs/organizations-intro.md, «Сквозные сценарии»), применимые к R1.

Автоматизированы 1, 3, 4, 5, 8, 9, 13, 14. Остальные — ручная проверка (tests/scenarios/MANUAL.md).
Для будущих возможностей указан этап и проверяемая сейчас часть.
"""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from results import services as results_services
from results.models import ResultsEvent
from results.tests.fixtures import protocols
from school.models import Class, ClassEnrollment, CompetitionApplicationLink, FamilyMember
from users.models import User

from ..factories import AthleteFactory, CompetitionFactory, FamilyFactory, FamilyServiceFactory, PersonFactory

pytestmark = pytest.mark.django_db


class FakeRedis:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = str(value)


@pytest.fixture
def fake_redis():
    redis = FakeRedis()
    with patch("users.views.redis.Redis.from_url", return_value=redis), patch("users.views.send_admin_message"):
        yield redis


# 1. Вход через Telegram-бота (одноразовый токен) и повторно по тому же токену во втором браузере; выход
def test_s01_login_via_bot_token_second_browser_and_logout(client, fake_redis):
    user = User.objects.create(email="parent@example.com", is_approved=True, token="one-time-token")
    response = client.get(reverse("users:telegram_callback", kwargs={"token": "one-time-token"}))
    assert response.status_code == 302 and response.url == reverse("school:index")
    user.refresh_from_db()
    assert user.token is None and client.get(reverse("school:index")).status_code == 200

    from django.test import Client

    second = Client()  # второй браузер в течение 60 секунд — по кэшу токена
    assert second.get(reverse("users:telegram_callback", kwargs={"token": "one-time-token"})).status_code == 302
    assert second.get(reverse("school:index")).status_code == 200

    client.post(reverse("users:logout"))
    assert client.get(reverse("school:index")).status_code == 302


def test_s01_unapproved_user_goes_to_awaiting_approval(client, fake_redis):
    User.objects.create(email="new@example.com", is_approved=False, token="t2")
    response = client.get(reverse("users:telegram_callback", kwargs={"token": "t2"}))
    assert response.url == reverse("users:awaiting_approval")


# 3. Спортсмены: список, карточка, редактирование
def test_s03_athletes_list_card_edit(client_for):
    athlete = AthleteFactory()
    client = client_for("superuser")
    listing = client.get(reverse("school:athlete_list"))
    assert listing.status_code == 200 and athlete.person.surname in listing.content.decode()
    assert client.get(reverse("members:members_detail", kwargs={"pk": athlete.person.pk})).status_code == 200
    assert client.get(reverse("school:edit_athlete", kwargs={"athlete_id": athlete.pk})).status_code == 200
    assert client.get(reverse("members:members_update", kwargs={"pk": athlete.person.pk})).status_code == 200


# 4. Семья: состав, договоры, услуги, платежи, финансы
def test_s04_family_composition_services_finance(client_for):
    family = FamilyFactory(family_name="Тестовые")
    athlete = AthleteFactory()
    FamilyMember.objects.create(family=family, person=athlete.person, relation="son")
    service = FamilyServiceFactory(family=family, name="Абонемент ноябрь")
    client = client_for("superuser")
    detail = client.get(reverse("members:family_detail", kwargs={"pk": family.pk})).content.decode()
    assert athlete.person.surname in detail
    finance = client.get(reverse("members:family_finance", kwargs={"pk": family.pk}))
    assert finance.status_code == 200 and "Абонемент ноябрь" in finance.content.decode()
    assert (
        client.get(
            reverse("members:family_service_update", kwargs={"pk": family.pk, "service_pk": service.pk})
        ).status_code
        == 200
    )


# 5. Расписание: занятие, шаблоны недели, уведомление о занятии, посещаемость
def test_s05_schedule_class_notify_and_enrollment(client_for):
    athlete = AthleteFactory()
    training = Class.objects.create(date=timezone.now() + timedelta(days=1), duration=90, type="regular")
    ClassEnrollment.objects.create(class_instance=training, athlete=athlete)
    client = client_for("superuser")
    assert client.get(reverse("classes:class_detail", kwargs={"pk": training.pk})).status_code == 200
    assert client.get(reverse("classes:class_notify", kwargs={"pk": training.pk})).status_code == 200
    assert client.get(reverse("classes:class_list")).status_code == 200


def test_s05_weekly_templates_task_runs_without_telegram(settings):
    from classes.tasks import ensure_weekly_schedule_task

    settings.BOT_TOKEN = ""
    with patch("classes.tasks.BOT_TOKEN", ""):
        assert isinstance(ensure_weekly_schedule_task(), int)


# 8. Соревнования: создание, заявка по ссылке, выгрузки
def test_s08_competition_apply_link_and_exports(client_for):
    competition = CompetitionFactory()
    client = client_for("superuser")
    assert client.get(reverse("school:competition_edit", kwargs={"pk": competition.pk})).status_code == 200
    link = CompetitionApplicationLink.objects.filter(competition=competition).first()
    if link is not None:
        apply_url = reverse("school:competition_apply", kwargs={"pk": competition.pk, "token": link.token})
        assert client.get(apply_url).status_code in (200, 302)
    export = client.get(reverse("school:competition_export", kwargs={"pk": competition.pk}))
    assert export.status_code == 200 and ("excel" in export["Content-Type"] or "spreadsheet" in export["Content-Type"])
    word = client.get(reverse("school:competition_export_word_type1", kwargs={"pk": competition.pk}))
    assert word.status_code == 200 and "wordprocessing" in word["Content-Type"]


# 9. Онлайн-результаты: загрузка, ручная правка, публичная страница
def test_s09_results_ingest_manual_edit_public_page(client):
    event = ResultsEvent.objects.create(title="Кубок", source_kind="manual", status="running")
    results_services.ingest_snapshot(event.pk, protocols.format_a_two_runs())
    anna = event.entries.get(full_name="Альфа Анна")
    results_services.set_manual_result(anna.pk, 1, "30.00")
    page = client.get(reverse("results:public_live", kwargs={"token": event.public_token})).content.decode()
    assert "1:06.00" in page


# 13. Права: матрица доступа — tests/test_access_matrix.py (обязательные ворота релиза)
def test_s13_access_matrix_exists():
    from tests.access_matrix import EXPECTED, ROLES

    assert len(ROLES) == 7 and len(EXPECTED) >= 60


# 14. Изоляция организаций — появится в R2-4 (фильтрация по организации).
# Сейчас проверяемая часть: публичные ссылки трансляций не раскрывают чужие трансляции.
def test_s14_now_public_tokens_are_isolated(client):
    first = ResultsEvent.objects.create(title="Первый", source_kind="manual", status="running")
    ResultsEvent.objects.create(title="Второй старт", source_kind="manual", status="running")
    page = client.get(reverse("results:public_live", kwargs={"token": first.public_token})).content.decode()
    assert "Первый" in page and "Второй старт" not in page


@pytest.mark.skip(reason="R2-4: изоляция данных двух организаций — после появления поля organization")
def test_s14_organizations_isolated():
    pass


def test_family_member_person_factory_smoke():
    assert PersonFactory().pk
