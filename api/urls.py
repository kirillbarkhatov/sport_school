from django.urls import path
from rest_framework.routers import SimpleRouter

from school.models import Person
from .apps import ApiConfig
from .views import PersonViewSet, UserViewSet, WhatsAppChatSyncView

app_name = ApiConfig.name

router_person = SimpleRouter()
router_person.register(r"person", PersonViewSet)

router_user = SimpleRouter()
router_user.register(r"user", UserViewSet)

urlpatterns = router_person.urls + router_user.urls + [
    path(
        "whatsapp_chat_members/",
        WhatsAppChatSyncView.as_view(),
        name="whatsapp-chat-members",
    ),
]
