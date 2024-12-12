from django.conf import settings
from django.conf.urls.static import static
from django.urls import path

from .apps import ClassesConfig

from . import views

app_name = ClassesConfig.name

urlpatterns = [
    path("", views.ClassListView.as_view(), name="class_list"),
    path("<int:pk>/", views.ClassDetailView.as_view(), name="class_detail"),
    path("create/", views.ClassCreateView.as_view(), name="class_create"),
    path("<int:pk>/update/", views.ClassUpdateView.as_view(), name="class_update"),
    path("<int:pk>/delete/", views.ClassDeleteView.as_view(), name="class_delete"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)