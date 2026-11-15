import pytest
from django.urls import reverse

from school.models import ClassEnrollment, FamilyMember
from users.models import User

from .factories import AthleteFactory, ClassFactory, FamilyFactory, PersonFactory


@pytest.mark.django_db
def test_class_notification_recipients_include_parent_and_athlete(client_for):
    """Регрессия: запрос получателей использовал удалённое поле User.family и падал с 500."""
    athlete = AthleteFactory()
    parent_person = PersonFactory()
    family = FamilyFactory()
    FamilyMember.objects.create(family=family, person=athlete.person, relation="son")
    FamilyMember.objects.create(family=family, person=parent_person, relation="mother")
    training = ClassFactory()
    ClassEnrollment.objects.create(class_instance=training, athlete=athlete)

    parent_user = User.objects.create(email="parent@example.com", person=parent_person, is_approved=True)
    athlete_user = User.objects.create(email="athlete@example.com", person=athlete.person, is_approved=True)
    User.objects.create(email="stranger@example.com", person=PersonFactory(), is_approved=True)
    User.objects.create(email="unapproved@example.com", person=parent_person, is_approved=False)

    response = client_for("superuser").get(reverse("classes:class_notify", kwargs={"pk": training.pk}))

    assert response.status_code == 200
    recipient_ids = set(response.context["form"].fields["recipients"].queryset.values_list("pk", flat=True))
    assert recipient_ids == {parent_user.pk, athlete_user.pk}
