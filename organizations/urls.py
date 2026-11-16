from django.urls import path

from . import views

app_name = "organizations"

urlpatterns = [
    path("platform/", views.PlatformHomeView.as_view(), name="platform"),
    path("platform/orgs/create/", views.PlatformOrganizationCreateView.as_view(), name="platform_org_create"),
    path("platform/orgs/<int:pk>/status/", views.PlatformOrganizationStatusView.as_view(), name="platform_org_status"),
    path(
        "platform/orgs/<int:pk>/invite-owner/", views.PlatformOwnerInviteView.as_view(), name="platform_owner_invite"
    ),
    path("org/", views.OrgConsoleView.as_view(), name="org_console"),
    path("org/members/<int:pk>/role/", views.OrgMemberRoleView.as_view(), name="org_member_role"),
    path("org/members/<int:pk>/status/", views.OrgMemberStatusView.as_view(), name="org_member_status"),
    path("org/invitations/create/", views.OrgInvitationCreateView.as_view(), name="org_invitation_create"),
    path("org/invitations/<int:pk>/revoke/", views.OrgInvitationRevokeView.as_view(), name="org_invitation_revoke"),
    path("join/request/", views.join_request, name="join_request"),
    path("join/<str:token>/", views.JoinByInvitationView.as_view(), name="join"),
]
