"""Консоль платформы (R3-2), консоль организации (R3-3), приглашения и вступление (R3-3, R3-4).

Все изменения — только POST с CSRF и проверкой прав. Консоль организации работает только с записями текущей
организации (по хосту): чужой id — 404.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.http import Http404, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_POST

from .accounts import is_platform_admin
from .auth import accept_invitation, create_invitation, request_join
from .context import default_organization
from .models import AuditLog, Invitation, Membership, Organization, OrgRole, RoleAssignment
from .permissions import authorize
from .scoping import scope_organization

MANAGED_ROLES = (OrgRole.ADMIN, OrgRole.MANAGER, OrgRole.COACH, OrgRole.SECRETARY)


def _audit(organization, actor, action, target="", target_id="", **payload):
    AuditLog.objects.create(
        organization=organization,
        actor=actor,
        action=action,
        target_type=target,
        target_id=str(target_id),
        payload=payload,
    )


# --- Консоль платформы ---------------------------------------------------------------------------------------


class PlatformAdminRequiredMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not is_platform_admin(request.user):
            return HttpResponseForbidden("Только для администратора платформы.")
        return super().dispatch(request, *args, **kwargs)


class PlatformHomeView(PlatformAdminRequiredMixin, View):
    def get(self, request):
        organizations = Organization.objects.order_by("name")
        counts = {
            row["organization_id"]: row["n"]
            for row in Membership.objects.filter(status=Membership.Status.ACTIVE)
            .values("organization_id")
            .annotate(n=Count("pk"))
        }
        context = {
            "organizations": [(o, counts.get(o.pk, 0)) for o in organizations],
            "audit": AuditLog.objects.select_related("organization", "actor").order_by("-created_at")[:50],
            "kinds": Organization.Kind.choices,
            "invite_url": request.session.pop("platform_invite_url", None),
        }
        return render(request, "organizations/platform.html", context)


class PlatformOrganizationCreateView(PlatformAdminRequiredMixin, View):
    def post(self, request):
        slug = (request.POST.get("slug") or "").strip().lower()
        name = (request.POST.get("name") or "").strip()
        kind = request.POST.get("kind") or Organization.Kind.CLUB
        if not slug or not name or not slug.replace("-", "").isalnum() or kind not in Organization.Kind.values:
            messages.error(request, "Укажите код (латиница, цифры, дефис), название и тип.")
        elif Organization.objects.filter(slug=slug).exists():
            messages.error(request, "Организация с таким кодом уже есть.")
        else:
            organization = Organization.objects.create(slug=slug, name=name, kind=kind)
            _audit(organization, request.user, "organization_created", "organization", organization.pk, slug=slug)
            messages.success(request, f"Организация «{name}» создана.")
        return redirect("organizations:platform")


class PlatformOrganizationStatusView(PlatformAdminRequiredMixin, View):
    def post(self, request, pk):
        organization = get_object_or_404(Organization, pk=pk)
        status = request.POST.get("status")
        if status not in Organization.Status.values:
            return HttpResponseForbidden("Неизвестный статус.")
        if status == Organization.Status.SUSPENDED and organization == default_organization():
            messages.error(request, "Организацию по умолчанию нельзя приостановить.")
            return redirect("organizations:platform")
        organization.status = status
        organization.save(update_fields=["status"])
        _audit(organization, request.user, f"organization_{status}", "organization", organization.pk)
        return redirect("organizations:platform")


class PlatformOwnerInviteView(PlatformAdminRequiredMixin, View):
    """Владелец назначается через приглашение: ссылка показывается один раз, в журнале — факт."""

    def post(self, request, pk):
        organization = get_object_or_404(Organization, pk=pk, status=Organization.Status.ACTIVE)
        _, token = create_invitation(organization, OrgRole.OWNER, request.user)
        request.session["platform_invite_url"] = request.build_absolute_uri(
            reverse("organizations:join", kwargs={"token": token})
        )
        return redirect("organizations:platform")


# --- Консоль организации -------------------------------------------------------------------------------------


class OrgConsoleMixin(LoginRequiredMixin):
    """Владелец и администратор текущей организации (действие org.manage)."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            self.organization = scope_organization()
            if self.organization is None or not authorize(request.user, "org.manage"):
                return HttpResponseForbidden("Недостаточно прав.")
        return super().dispatch(request, *args, **kwargs)

    def membership(self, pk):
        return get_object_or_404(Membership, pk=pk, organization=self.organization)


class OrgConsoleView(OrgConsoleMixin, View):
    def get(self, request):
        members = (
            Membership.objects.filter(organization=self.organization)
            .select_related("user", "person")
            .prefetch_related("role_assignments__role")
            .order_by("status", "user__last_name", "user__email")
        )
        context = {
            "organization": self.organization,
            "members": [m for m in members if m.status != Membership.Status.PENDING],
            "queue": [m for m in members if m.status == Membership.Status.PENDING],
            "roles": OrgRole.objects.filter(code__in=MANAGED_ROLES, organization__isnull=True),
            "invitations": Invitation.objects.filter(
                organization=self.organization, accepted_at__isnull=True, revoked=False, expires_at__gte=timezone.now()
            ).select_related("role"),
            "audit": AuditLog.objects.filter(organization=self.organization)
            .select_related("actor")
            .order_by("-created_at")[:50],
            "invite_url": request.session.pop("org_invite_url", None),
        }
        return render(request, "organizations/org_console.html", context)


class OrgMemberRoleView(OrgConsoleMixin, View):
    def post(self, request, pk):
        membership = self.membership(pk)
        code = request.POST.get("role")
        if code not in MANAGED_ROLES:
            return HttpResponseForbidden("Эту роль нельзя назначить здесь.")
        role = OrgRole.objects.get(code=code, organization__isnull=True)
        if request.POST.get("action") == "remove":
            RoleAssignment.objects.filter(membership=membership, role=role).delete()
            _audit(self.organization, request.user, "role_removed", "membership", membership.pk, role=code)
        else:
            RoleAssignment.objects.get_or_create(membership=membership, role=role)
            _audit(self.organization, request.user, "role_added", "membership", membership.pk, role=code)
        return redirect("organizations:org_console")


class OrgMemberStatusView(OrgConsoleMixin, View):
    ALLOWED = {
        "approve": Membership.Status.ACTIVE,
        "activate": Membership.Status.ACTIVE,
        "suspend": Membership.Status.SUSPENDED,
        "reject": Membership.Status.SUSPENDED,
    }

    def post(self, request, pk):
        membership = self.membership(pk)
        action = request.POST.get("action")
        if action not in self.ALLOWED:
            return HttpResponseForbidden("Неизвестное действие.")
        if membership.user_id == request.user.pk and action in ("suspend", "reject"):
            messages.error(request, "Нельзя приостановить собственное членство.")
            return redirect("organizations:org_console")
        if (
            RoleAssignment.objects.filter(membership=membership, role__code=OrgRole.OWNER).exists()
            and action in ("suspend", "reject")
            and not authorize(request.user, "org.full")
        ):
            return HttpResponseForbidden("Владельца приостанавливает только владелец.")
        membership.status = self.ALLOWED[action]
        if membership.status == Membership.Status.ACTIVE:
            membership.approved_at, membership.approved_by = timezone.now(), request.user
        membership.save(update_fields=["status", "approved_at", "approved_by"])
        _audit(self.organization, request.user, f"member_{action}", "membership", membership.pk)
        return redirect("organizations:org_console")


class OrgInvitationCreateView(OrgConsoleMixin, View):
    def post(self, request):
        code = request.POST.get("role") or None
        if code is not None and code not in MANAGED_ROLES:
            return HttpResponseForbidden("Эту роль нельзя выдать приглашением.")
        _, token = create_invitation(self.organization, code, request.user)
        request.session["org_invite_url"] = request.build_absolute_uri(
            reverse("organizations:join", kwargs={"token": token})
        )
        return redirect("organizations:org_console")


class OrgInvitationRevokeView(OrgConsoleMixin, View):
    def post(self, request, pk):
        invitation = get_object_or_404(Invitation, pk=pk, organization=self.organization)
        invitation.revoked = True
        invitation.save(update_fields=["revoked"])
        _audit(self.organization, request.user, "invitation_revoked", "invitation", invitation.pk)
        return redirect("organizations:org_console")


# --- Приглашение и вступление --------------------------------------------------------------------------------


class JoinByInvitationView(LoginRequiredMixin, View):
    """GET — страница с кнопкой; принятие только POST (CSRF)."""

    def get(self, request, token):
        return render(request, "organizations/join.html", {"token": token})

    def post(self, request, token):
        membership = accept_invitation(token, request.user)
        if membership is None:
            raise Http404("Приглашение недействительно, истекло или уже использовано.")
        if membership.organization != scope_organization():
            messages.info(request, f"Вы вступили в «{membership.organization.name}». Откройте её адрес.")
        else:
            messages.success(request, f"Вы вступили в «{membership.organization.name}».")
        return redirect("school:index")


@require_POST
def join_request(request):
    """Посетитель без членства нажал «Вступить»: заявка в очередь текущей организации (R3-4)."""
    if not request.user.is_authenticated:
        return redirect("users:login_page")
    organization = scope_organization()
    if organization is None:
        raise Http404
    _, created = request_join(request.user, organization)
    messages.success(
        request, "Заявка отправлена в организацию." if created else "Заявка или членство в этой организации уже есть."
    )
    return redirect("users:awaiting_approval")
