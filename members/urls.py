from django.conf import settings
from django.conf.urls.static import static
from django.urls import path

from .apps import MembersConfig

from . import views

app_name = MembersConfig.name

urlpatterns = [
    path("", views.PersonListView.as_view(), name="members_list"),
    path("<int:pk>/", views.PersonDetailView.as_view(), name="members_detail"),
    path("create/", views.PersonCreateView.as_view(), name="members_create"),
    path("<int:pk>/update/", views.PersonUpdateView.as_view(), name="members_update"),
    path("<int:pk>/delete/", views.PersonDeleteView.as_view(), name="members_delete"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)