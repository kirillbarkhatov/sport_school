from rest_framework import viewsets

from .models import User


class UserViewSet(viewsets.ModelViewSet):
    """Вьюсет для Пользователя"""

    model = User
    queryset = User.objects.all()
