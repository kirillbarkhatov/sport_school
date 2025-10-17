from django.conf import settings
from django.conf.urls.static import static
from django.urls import path

from .apps import MembersConfig

from . import views

app_name = MembersConfig.name

urlpatterns = [
    path("families/", views.FamilyListView.as_view(), name="family_list"),
    path("families/<int:pk>/", views.FamilyDetailView.as_view(), name="family_detail"),
    path("families/<int:pk>/edit/", views.FamilyUpdateView.as_view(), name="family_update"),
    path("families/<int:pk>/finance/", views.FamilyFinanceView.as_view(), name="family_finance"),
    path("families/<int:pk>/toggle-status/", views.FamilyToggleStatusView.as_view(), name="family_toggle_status"),
    path("families/<int:pk>/inline-update/", views.FamilyInlineUpdateView.as_view(), name="family_inline_update"),
    path("families/add-member/new/", views.FamilyMemberCreateModalView.as_view(), name="family_member_create"),
    path("families/<int:pk>/add-member/", views.FamilyAddMemberView.as_view(), name="family_add_member"),
    path("families/<int:pk>/services/<int:service_pk>/edit/", views.FamilyServiceUpdateView.as_view(), name="family_service_update"),
    path("families/<int:pk>/services/<int:service_pk>/delete/", views.FamilyServiceDeleteView.as_view(), name="family_service_delete"),
    path("families/<int:pk>/profiles/<int:profile_pk>/contracts/add/", views.AthleteContractCreateView.as_view(), name="contract_create"),
    path("families/<int:pk>/contracts/<int:contract_pk>/edit/", views.AthleteContractUpdateView.as_view(), name="contract_update"),
    path("", views.PersonListView.as_view(), name="members_list"),
    path("<int:pk>/", views.PersonDetailView.as_view(), name="members_detail"),
    path("create/", views.PersonCreateView.as_view(), name="members_create"),
    path("<int:pk>/update/", views.PersonUpdateView.as_view(), name="members_update"),
    path("<int:pk>/delete/", views.PersonDeleteView.as_view(), name="members_delete"),
    path("<int:pk>/toggle-athlete/", views.PersonToggleAthleteView.as_view(), name="person_toggle_athlete"),
    path("<int:pk>/family/", views.PersonAssignFamilyView.as_view(), name="person_assign_family"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
