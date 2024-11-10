from django.conf import settings
from django.conf.urls.static import static
from django.urls import path

from school.apps import SchoolConfig

from . import views

app_name = SchoolConfig.name

urlpatterns = [
    path("", views.IndexView.as_view(), name="index"),
    path("athlete", views.AthleteListView.as_view(), name="athlete_list"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)