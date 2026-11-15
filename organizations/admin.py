from django.contrib import admin

from .models import AuditLog, Membership, Organization, OrgDomain, OrgRole, PlatformRole, RoleAssignment


class OrgDomainInline(admin.TabularInline):
    model = OrgDomain
    extra = 0


@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "kind", "status", "created_at")
    inlines = [OrgDomainInline]


@admin.register(Membership)
class MembershipAdmin(admin.ModelAdmin):
    list_display = ("user", "organization", "status", "source", "created_at")
    list_filter = ("organization", "status", "source")
    raw_id_fields = ("user", "approved_by")


@admin.register(RoleAssignment)
class RoleAssignmentAdmin(admin.ModelAdmin):
    list_display = ("membership", "role", "source", "created_at")
    list_filter = ("role", "source")


admin.site.register(OrgRole)
admin.site.register(PlatformRole)


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "organization", "actor", "action", "target_type", "target_id")
    list_filter = ("action", "organization")
