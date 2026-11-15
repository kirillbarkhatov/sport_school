"""Smoke-тесты: каждая страница открывается под каждой ролью без 500.

Цель — ловить поломки при обновлении зависимостей и рефакторинге, а не проверять бизнес-логику.
"""

import pytest
from django.urls import reverse

from .conftest import ROLES

# Страницы без параметров в URL (GET).
STATIC_PAGES = [
    "healthz",
    "school:index",
    "school:user_main_info",
    "school:athlete_list",
    "school:athlete_simple_list",
    "school:competition_list",
    "school:competition_venue_list",
    "school:competition_create",
    "school:documents_bulk_upload",
    "school:documents_analysis_list",
    "users:login_page",
    "users:awaiting_approval",
    "api:person-list",
    "api:user-list",
    "api:assistant-upcoming-trainings",
    "members:telegram_interlocutors",
    "members:person_dedup_center",
    "members:family_list",
    "members:members_list",
    "members:members_create",
    "classes:class_list",
    "classes:class_create",
    "groups:group_list",
    "groups:group_create",
    "notifications:notification_list",
    "notifications:notification_create",
    "online-results-stream-runs",
    "online-results-webhook-events",
    "online-results-live",
]

# Страницы с объектом: (имя URL, функция sample_data -> kwargs).
DETAIL_PAGES = [
    ("school:edit_athlete", lambda d: {"athlete_id": d["athlete"].pk}),
    ("school:athlete_edit_compact", lambda d: {"pk": d["athlete"].pk}),
    ("school:athlete_certificate_status", lambda d: {"pk": d["athlete"].pk}),
    ("school:competition_edit", lambda d: {"pk": d["competition"].pk}),
    ("school:competition_groups", lambda d: {"pk": d["competition"].pk}),
    ("school:competition_export", lambda d: {"pk": d["competition"].pk}),
    ("school:competition_export_type2", lambda d: {"pk": d["competition"].pk}),
    ("school:competition_export_word_type1", lambda d: {"pk": d["competition"].pk}),
    ("school:competition_export_word_type2", lambda d: {"pk": d["competition"].pk}),
    ("school:competition_export_word_type3", lambda d: {"pk": d["competition"].pk}),
    ("school:competition_export_word_type4", lambda d: {"pk": d["competition"].pk}),
    ("members:family_detail", lambda d: {"pk": d["family"].pk}),
    ("members:family_update", lambda d: {"pk": d["family"].pk}),
    ("members:family_finance", lambda d: {"pk": d["family"].pk}),
    ("members:family_service_update", lambda d: {"pk": d["family"].pk, "service_pk": d["service"].pk}),
    ("members:members_detail", lambda d: {"pk": d["person"].pk}),
    ("members:members_update", lambda d: {"pk": d["person"].pk}),
    ("members:members_delete", lambda d: {"pk": d["person"].pk}),
    ("classes:class_detail", lambda d: {"pk": d["class"].pk}),
    ("classes:class_update", lambda d: {"pk": d["class"].pk}),
    ("classes:class_delete", lambda d: {"pk": d["class"].pk}),
    ("classes:class_notify", lambda d: {"pk": d["class"].pk}),
    ("groups:group_detail", lambda d: {"pk": d["group"].pk}),
    ("groups:group_update", lambda d: {"pk": d["group"].pk}),
    ("groups:group_delete", lambda d: {"pk": d["group"].pk}),
]

# Основные разделы, которые администратор должен видеть с кодом 200.
ADMIN_MUST_SEE = [
    "school:index",
    "school:athlete_list",
    "school:competition_list",
    "members:family_list",
    "members:members_list",
    "classes:class_list",
    "groups:group_list",
    "notifications:notification_list",
]


@pytest.mark.django_db
@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("url_name", STATIC_PAGES)
def test_static_page_does_not_crash(client_for, role, url_name):
    response = client_for(role).get(reverse(url_name))
    assert response.status_code < 500, f"{url_name} [{role}] -> {response.status_code}"


@pytest.mark.django_db
@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(("url_name", "kwargs_fn"), DETAIL_PAGES, ids=[name for name, _ in DETAIL_PAGES])
def test_detail_page_does_not_crash(client_for, sample_data, role, url_name, kwargs_fn):
    response = client_for(role).get(reverse(url_name, kwargs=kwargs_fn(sample_data)))
    assert response.status_code < 500, f"{url_name} [{role}] -> {response.status_code}"


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ADMIN_MUST_SEE)
def test_admin_sees_main_sections(client_for, url_name):
    response = client_for("admin").get(reverse(url_name))
    assert response.status_code == 200, f"{url_name} -> {response.status_code}"


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ADMIN_MUST_SEE)
def test_anonymous_is_redirected_to_login(client_for, url_name):
    response = client_for("anonymous").get(reverse(url_name))
    assert response.status_code in (302, 403), f"{url_name} -> {response.status_code}"


def test_healthz_reports_ok(client, db):
    response = client.get(reverse("healthz"))
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
