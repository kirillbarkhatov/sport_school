from django.conf import settings
from django.conf.urls.static import static
from django.urls import path

from .apps import GroupsConfig
from . import views

app_name = GroupsConfig.name

urlpatterns = [
    path("", views.GroupListView.as_view(), name="group_list"),
    path("<int:pk>/", views.GroupDetailView.as_view(), name="group_detail"),
    path("create/", views.GroupCreateView.as_view(), name="group_create"),
    path("<int:pk>/update/", views.GroupUpdateView.as_view(), name="group_update"),
    path(
        "<int:pk>/membership/",
        views.GroupMembershipUpdateView.as_view(),
        name="group_membership",
    ),
    path("<int:pk>/delete/", views.GroupDeleteView.as_view(), name="group_delete"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
