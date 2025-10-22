from django.urls import path
from rest_framework.routers import SimpleRouter

from school.models import Person
from .apps import ApiConfig
from .views import (
    AssistantUpcomingTrainingsView,
    PersonViewSet,
    UserViewSet,
    WhatsAppChatSyncView,
)
from assistant.views import (
    AssistantTrainingAttendanceView,
    AssistantTrainingUpdatesView,
    AssistantUnmatchedParticipantsView,
)

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
    path(
        "assistant/upcoming-trainings/",
        AssistantUpcomingTrainingsView.as_view(),
        name="assistant-upcoming-trainings",
    ),
    path(
        "assistant/trainings/updates/",
        AssistantTrainingUpdatesView.as_view(),
        name="assistant-training-updates",
    ),
    path(
        "assistant/trainings/attendance/",
        AssistantTrainingAttendanceView.as_view(),
        name="assistant-training-attendance",
    ),
    path(
        "assistant/trainings/unmatched-participants/",
        AssistantUnmatchedParticipantsView.as_view(),
        name="assistant-unmatched-participants",
    ),
]
