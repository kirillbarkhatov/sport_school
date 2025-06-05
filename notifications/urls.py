from django.conf import settings
from django.conf.urls.static import static
from django.urls import path

from .apps import NotificationsConfig
from . import views

app_name = NotificationsConfig.name

urlpatterns = [
    path("", views.NotificationListView.as_view(), name="notification_list"),
    path("create/", views.NotificationCreateView.as_view(), name="notification_create"),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
