from __future__ import annotations

from django.db import transaction
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from school.models import Athlete, Class, ClassEnrollment, Family, FamilyMember, Group, Person

from .tasks import trigger_members_sync, trigger_trainings_sync

MEMBERSHIP_COUNTDOWN = 5
TRAININGS_COUNTDOWN = 5


def _schedule(task_callable, *, countdown: int = 0) -> None:
    transaction.on_commit(lambda: task_callable(countdown=countdown))


@receiver(post_save, sender=Person)
@receiver(post_delete, sender=Person)
@receiver(post_save, sender=Family)
@receiver(post_delete, sender=Family)
@receiver(post_save, sender=FamilyMember)
@receiver(post_delete, sender=FamilyMember)
@receiver(post_save, sender=Athlete)
@receiver(post_delete, sender=Athlete)
def members_changed(sender, **kwargs):
    _schedule(trigger_members_sync, countdown=MEMBERSHIP_COUNTDOWN)


@receiver(post_save, sender=Class)
@receiver(post_delete, sender=Class)
def class_changed(sender, **kwargs):
    _schedule(trigger_trainings_sync, countdown=TRAININGS_COUNTDOWN)


@receiver(post_save, sender=ClassEnrollment)
@receiver(post_delete, sender=ClassEnrollment)
def class_enrollment_changed(sender, **kwargs):
    _schedule(trigger_trainings_sync, countdown=TRAININGS_COUNTDOWN)


@receiver(post_save, sender=Group)
def group_changed(sender, **kwargs):
    _schedule(trigger_trainings_sync, countdown=TRAININGS_COUNTDOWN)
