from __future__ import annotations

from django.db.models.signals import post_save
from django.dispatch import receiver

from users.models import User, UserPersonLink


@receiver(post_save, sender=User)
def ensure_person_link(sender, instance: User, created: bool, **kwargs) -> None:  # noqa: ANN001
    if created:
        UserPersonLink.objects.get_or_create(user=instance)
