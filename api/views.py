from rest_framework import status, viewsets
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from school.models import Person
from users.models import User
from classes.services import get_assistant_schedule_payload
from school.constants import DEFAULT_EQUIPMENT, DEFAULT_LOCATIONS, DEFAULT_TRAINING_TYPES
from .serializers import (
    AssistantAthleteSerializer,
    AssistantTrainingSerializer,
    PersonSerializer,
    UserSerializer,
    WhatsAppChatSyncSerializer,
)


class PersonViewSet(viewsets.ModelViewSet):
    """Вьюсет для курсов"""

    model = Person
    serializer_class = PersonSerializer
    queryset = Person.objects.all()
    permission_classes = [IsAuthenticated]


class UserViewSet(viewsets.ModelViewSet):
    """Вьюсет для Пользователя"""

    model = User
    serializer_class = UserSerializer
    queryset = User.objects.all()
    permission_classes = [IsAuthenticated]


class WhatsAppChatSyncView(APIView):
    """Приём данных о составе чатов WhatsApp"""

    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = WhatsAppChatSyncSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chat = serializer.save()
        data = serializer.validated_data
        active_members = chat.members.filter(is_active=True).count()
        return Response(
            {
                "chat_id": chat.id,
                "group_name": chat.name,
                "synced_at": data["synced_at"],
                "processed_members": len(data["members"]),
                "active_member_count": active_members,
            },
            status=status.HTTP_200_OK,
        )


class AssistantUpcomingTrainingsView(APIView):
    """Возвращает ближайшие тренировки и список спортсменов для AI-ассистента."""

    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        limit_param = request.query_params.get("limit")
        limit: int | None = 5

        if limit_param is not None:
            try:
                limit_value = int(limit_param)
            except (TypeError, ValueError):
                return Response(
                    {"detail": "Параметр limit должен быть целым числом."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if limit_value <= 0:
                return Response(
                    {"detail": "Параметр limit должен быть положительным."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            limit = limit_value

        trainings_payload, athletes_payload = get_assistant_schedule_payload(
            request.user,
            limit=limit,
        )

        trainings = AssistantTrainingSerializer(trainings_payload, many=True).data
        athletes = AssistantAthleteSerializer(athletes_payload, many=True).data

        return Response(
            {
                "trainings": trainings,
                "athletes": athletes,
                "defaults": {
                    "training_types": DEFAULT_TRAINING_TYPES,
                    "equipment": DEFAULT_EQUIPMENT,
                    "locations": DEFAULT_LOCATIONS,
                },
            },
            status=status.HTTP_200_OK,
        )
