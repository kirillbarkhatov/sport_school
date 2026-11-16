"""R4-1: демонстрационная организация с синтетическими данными. Идемпотентна: повтор не создаёт дублей.

Данные создаются в контексте организации «demo» (изоляция OrgOwned): две семьи (родитель и ребёнок-спортсмен),
группа, занятие, соревнование со ссылкой заявки, трансляция результатов с публичной ссылкой. Внешние каналы
выключены (Telegram-публикация трансляции — нет), производственных ключей нет; метаданные — в настройках
организации (demo, synthetic): по ним демо исключается из расписания и внешних задач (for_each_organization).
Работает только при ORGS_ENFORCE_SCOPING=True; все выборки — в контексте demo, данные ksclub не затрагиваются.

QA-учётки (владелец и родители, qa-demo-*@staging.invalid) — только с --qa-accounts и только при STAGING_MODE.
"""

from datetime import date, timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from organizations.context import organization_context
from organizations.models import Membership, Organization, OrgDomain, OrgRole, RoleAssignment

DEMO_SLUG = "demo"
DEMO_NAME = "Демо-клуб"
DEMO_METADATA = {"demo": True, "synthetic": True, "alumni_history_access": True}
FAMILIES = (
    ("Демо-семья Северовых", "Северова", "Анна", "Северов", "Илья"),
    ("Демо-семья Южаковых", "Южакова", "Мария", "Южаков", "Пётр"),
)


class Command(BaseCommand):
    help = "Создать или обновить демо-организацию с синтетическими данными (R4-1). Идемпотентно."

    def add_arguments(self, parser):
        parser.add_argument("--host", action="append", default=[], help="домен демо (можно несколько)")
        parser.add_argument("--qa-accounts", action="store_true", help="QA-учётки (только STAGING_MODE)")

    @transaction.atomic
    def handle(self, *args, host, qa_accounts, **options):
        from organizations.scoping import scoping_enabled

        if not scoping_enabled():
            # без изоляции менеджеры видят данные всех организаций — демо создаётся только при включённой изоляции
            raise CommandError("Демо создаётся только при ORGS_ENFORCE_SCOPING=True.")
        if qa_accounts and not getattr(settings, "STAGING_MODE", False):
            raise CommandError("QA-учётки демо создаются только на staging (STAGING_MODE=True).")
        org, created = Organization.objects.get_or_create(
            slug=DEMO_SLUG, defaults={"name": DEMO_NAME, "kind": Organization.Kind.CLUB, "settings": DEMO_METADATA}
        )
        if org.settings != {**(org.settings or {}), **DEMO_METADATA}:
            org.settings = {**(org.settings or {}), **DEMO_METADATA}
            org.save(update_fields=["settings"])
        for value in host:
            domain, _ = OrgDomain.objects.get_or_create(host=value.strip().lower(), defaults={"organization": org})
            if domain.organization_id != org.pk:
                raise CommandError(f"Домен {value} уже принадлежит другой организации.")
        with organization_context(org):
            data = self._data(org)
        users = self._qa_accounts(org, data) if qa_accounts else {}
        self.stdout.write(
            f"Демо-организация {'создана' if created else 'обновлена'}: {org.slug}; "
            f"домены: {sorted(OrgDomain.objects.filter(organization=org).values_list('host', flat=True))}; "
            f"семей {len(data['families'])}, спортсменов {len(data['athletes'])}; QA-учёток {len(users)}"
        )

    def _data(self, org):
        from results.models import ResultsEvent
        from school.models import (
            Athlete,
            Class,
            Competition,
            CompetitionApplicationLink,
            Family,
            FamilyMember,
            Group,
            Person,
        )

        def person(surname, name, gender, birth):
            return Person.objects.get_or_create(
                surname=surname, name=name, defaults={"gender": gender, "date_of_birth": birth}
            )[0]

        families, athletes, parents = [], [], []
        for family_name, parent_surname, parent_name, child_surname, child_name in FAMILIES:
            family = Family.objects.get_or_create(family_name=family_name, defaults={"status": "active"})[0]
            parent = person(parent_surname, parent_name, "female", date(1985, 3, 1))
            child = person(child_surname, child_name, "male", date(2015, 6, 1))
            FamilyMember.objects.get_or_create(family=family, person=parent, defaults={"relation": "parent"})
            FamilyMember.objects.get_or_create(family=family, person=child, defaults={"relation": "child"})
            athletes.append(Athlete.objects.get_or_create(person=child)[0])
            families.append(family)
            parents.append(parent)
        group = Group.objects.get_or_create(name="Демо-группа")[0]
        group.athletes.add(*athletes)
        Class.objects.get_or_create(
            group=group,
            defaults={"date": timezone.now() + timedelta(days=2), "duration": 90, "type": "regular"},
        )
        competition = Competition.objects.get_or_create(name="Демо-кубок")[0]
        CompetitionApplicationLink.objects.get_or_create(competition=competition, defaults={"token": "demo-apply"})
        event = ResultsEvent.objects.get_or_create(
            title="Демо-трансляция",
            defaults={"source_kind": ResultsEvent.SourceKind.MANUAL, "status": ResultsEvent.Status.RUNNING},
        )[0]
        if event.telegram_enabled:  # внешние каналы в демо выключены
            event.telegram_enabled = False
            event.save(update_fields=["telegram_enabled"])
        return {"families": families, "athletes": athletes, "parents": parents, "event": event}

    def _qa_accounts(self, org, data):
        User = get_user_model()
        users = {}
        specs = [("owner", None, OrgRole.OWNER)] + [
            (f"parent{i + 1}", parent, None) for i, parent in enumerate(data["parents"])
        ]
        for key, person, role in specs:
            user, _ = User.objects.get_or_create(email=f"qa-demo-{key}@staging.invalid")
            if person is not None and user.person_id != person.pk:
                user.person = person
            user.is_approved = True
            user.save()
            Membership.objects.filter(user=user).exclude(organization=org).delete()  # только демо
            membership, _ = Membership.objects.update_or_create(
                user=user, organization=org, defaults={"status": Membership.Status.ACTIVE}
            )
            if role:
                RoleAssignment.objects.get_or_create(
                    membership=membership, role=OrgRole.objects.get(code=role, organization__isnull=True)
                )
            users[key] = user
        return users
