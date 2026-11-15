"""Двойная запись (ORGS_LEGACY_SYNC): сигналы с обеих сторон, защита от рекурсии — в legacy._sync_guard."""

from django.contrib.auth import get_user_model
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver

from . import legacy
from .models import Membership, RoleAssignment

User = get_user_model()
_WATCHED_USER_FIELDS = {"is_approved", "is_staff", "is_superuser"}


@receiver(post_save, sender=Membership, dispatch_uid="orgs_membership_to_legacy")
def membership_saved(sender, instance, **kwargs):
    legacy.push_membership_to_legacy(instance)


@receiver(post_save, sender=RoleAssignment, dispatch_uid="orgs_role_saved_to_legacy")
@receiver(post_delete, sender=RoleAssignment, dispatch_uid="orgs_role_deleted_to_legacy")
def role_changed(sender, instance, **kwargs):
    membership = Membership.objects.filter(pk=instance.membership_id).select_related("user").first()
    if membership is not None:
        legacy.push_roles_to_legacy(membership)


@receiver(post_save, sender=User, dispatch_uid="orgs_user_to_membership")
def user_saved(sender, instance, created, update_fields=None, **kwargs):
    if not created and update_fields is not None and not (set(update_fields) & _WATCHED_USER_FIELDS):
        return  # например, обновление last_login при входе
    legacy.pull_user_to_membership(instance)


@receiver(m2m_changed, sender=User.groups.through, dispatch_uid="orgs_groups_to_roles")
def user_groups_changed(sender, instance, action, reverse, **kwargs):
    if reverse or action not in {"post_add", "post_remove", "post_clear"}:
        return
    legacy.pull_groups_to_roles(instance)
