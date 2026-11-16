"""T11 (R4-1 минимальный): демо-организация, изоляция в обе стороны, stage4_check r4."""

from io import StringIO

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client
from django.urls import reverse

from organizations.auth import bind_login_token
from organizations.backfill import org_owned_models
from organizations.models import Membership, Organization, OrgRole, RoleAssignment
from results.models import ResultsEvent
from school.models import Family, FamilyMember, Group
from users.models import User

pytestmark = pytest.mark.django_db
KS_HOST, DEMO_HOST = "ksclub.ru", "demo.example"


@pytest.fixture
def demo(settings):
    settings.ALLOWED_HOSTS = ["*"]
    settings.STAGING_MODE = True
    settings.ORGS_ROLES_SOURCE = "memberships"
    settings.ORGS_ENFORCE_SCOPING = True
    cache.clear()
    call_command("orgs_create_demo", "--host", DEMO_HOST, "--qa-accounts", stdout=StringIO())
    ks = Organization.objects.get(slug="ksclub")
    with __import__("organizations.context", fromlist=["organization_context"]).organization_context(ks):
        Group.objects.create(name="Группа ksclub")
    ks_owner = User.objects.create(email="ks-owner-r4@example.com", is_approved=True)
    m = Membership.objects.get(user=ks_owner, organization=ks)
    Membership.objects.filter(pk=m.pk).update(status="active")
    RoleAssignment.objects.create(membership=m, role=OrgRole.objects.get(code="owner"), source="manual")
    yield {
        "demo": Organization.objects.get(slug="demo"),
        "ks": ks,
        "demo_owner": User.objects.get(email="qa-demo-owner@staging.invalid"),
        "parent1": User.objects.get(email="qa-demo-parent1@staging.invalid"),
        "ks_owner": ks_owner,
    }
    cache.clear()


def client(user, host):
    c = Client(HTTP_HOST=host)
    if user is not None:
        c.force_login(user)
    return c


def test_demo_command_idempotent(demo):
    before = {m._meta.label: m.all_objects.filter(organization=demo["demo"]).count() for m in org_owned_models()}
    users = User.objects.count()
    call_command("orgs_create_demo", "--host", DEMO_HOST, "--qa-accounts", stdout=StringIO())
    after = {m._meta.label: m.all_objects.filter(organization=demo["demo"]).count() for m in org_owned_models()}
    assert before == after and User.objects.count() == users
    assert before["school.Family"] == 2 and before["school.Athlete"] == 2 and before["results.ResultsEvent"] == 1
    assert demo["demo"].settings["synthetic"] is True
    assert not ResultsEvent.all_objects.filter(organization=demo["demo"], telegram_enabled=True).exists()


def test_qa_accounts_only_on_staging(settings):
    settings.STAGING_MODE = False
    with pytest.raises(CommandError):
        call_command("orgs_create_demo", "--qa-accounts", stdout=StringIO())
    call_command("orgs_create_demo", stdout=StringIO())  # данные без учёток — можно
    assert not User.objects.filter(email__startswith="qa-demo-").exists()


def test_demo_qa_accounts_belong_only_to_demo(demo):
    for key in ("demo_owner", "parent1"):
        assert set(Membership.objects.filter(user=demo[key]).values_list("organization__slug", flat=True)) == {"demo"}


@pytest.mark.parametrize("side", ["demo", "ks"])
def test_owners_isolated_both_ways(demo, side):
    own_user, own_host, own_group, foreign_host, foreign_group = (
        (demo["demo_owner"], DEMO_HOST, "Демо-группа", KS_HOST, "Группа ksclub")
        if side == "demo"
        else (demo["ks_owner"], KS_HOST, "Группа ksclub", DEMO_HOST, "Демо-группа")
    )
    page = client(own_user, own_host).get(reverse("groups:group_list")).content.decode()
    assert own_group in page and foreign_group not in page
    assert client(own_user, own_host).get(reverse("organizations:org_console")).status_code == 200
    assert client(own_user, foreign_host).get(reverse("groups:group_list")).status_code == 302  # нет членства
    assert client(own_user, foreign_host).get(reverse("organizations:org_console")).status_code == 403


def test_demo_families_and_parent_scope(demo):
    own = Family.all_objects.get(organization=demo["demo"], family_name__contains="Северовых")
    other = Family.all_objects.get(organization=demo["demo"], family_name__contains="Южаковых")
    c = client(demo["parent1"], DEMO_HOST)
    page = c.get(reverse("members:family_list")).content.decode()
    assert "Северовых" in page and "Южаковых" not in page
    assert c.get(reverse("members:family_detail", kwargs={"pk": own.pk})).status_code == 200
    assert c.get(reverse("members:family_detail", kwargs={"pk": other.pk})).status_code == 404
    assert (
        client(demo["ks_owner"], KS_HOST).get(reverse("members:family_detail", kwargs={"pk": own.pk})).status_code
        == 404
    )


def test_demo_public_results_login_and_health(demo):
    event = ResultsEvent.all_objects.get(organization=demo["demo"])
    url = reverse("results:public_live", kwargs={"token": event.public_token})
    assert client(None, DEMO_HOST).get(url).status_code == 200
    assert client(None, KS_HOST).get(url).status_code == 404
    bind_login_token(demo["parent1"], "demo-login-token")
    response = client(None, DEMO_HOST).get(reverse("users:telegram_callback", kwargs={"token": "demo-login-token"}))
    assert response.status_code == 302
    for host in (DEMO_HOST, KS_HOST):
        assert client(None, host).get(reverse("healthz")).status_code == 200


def test_stage4_check_r4_and_cross_link_detection(demo):
    from organizations.models import PlatformRole

    admin = User.objects.create(email="ga-r4@example.com", is_staff=True, is_approved=True)
    PlatformRole.objects.get_or_create(user=admin, role="global_admin")
    out = StringIO()
    call_command("stage4_check", "--release", "r4", stdout=out)
    text = out.getvalue()
    assert "связей между организациями (FK/OneToOne/M2M): 0" in text and "демо: статус active" in text
    member = FamilyMember.all_objects.filter(organization=demo["demo"]).first()
    FamilyMember.all_objects.filter(pk=member.pk).update(organization=demo["ks"])  # имитация утечки
    with pytest.raises(CommandError):
        call_command("stage4_check", "--release", "r4", stdout=StringIO(), stderr=StringIO())
