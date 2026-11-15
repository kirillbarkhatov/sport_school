from datetime import date, timedelta

import factory
from django.utils import timezone

from notifications.models import Notification
from school.models import Athlete, Class, Competition, Family, FamilyService, Group, Person


class PersonFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Person

    name = factory.Sequence(lambda n: f"Имя{n}")
    surname = factory.Sequence(lambda n: f"Фамилия{n}")
    gender = "male"
    date_of_birth = date(2015, 5, 20)


class AthleteFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Athlete

    person = factory.SubFactory(PersonFactory)
    level = "unknown"


class FamilyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Family


class FamilyServiceFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = FamilyService

    family = factory.SubFactory(FamilyFactory)
    name = "Абонемент"


class GroupFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Group

    name = factory.Sequence(lambda n: f"Группа {n}")


class ClassFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Class

    date = factory.LazyFunction(lambda: timezone.now() + timedelta(days=1))
    duration = 90
    type = "regular"


class CompetitionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Competition

    name = factory.Sequence(lambda n: f"Кубок {n}")


class NotificationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Notification

    title = "Тест"
    message = "Текст уведомления"
