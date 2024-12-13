from rest_framework import viewsets

from school.models import Person
from .serializers import PersonSerializer


class PersonViewSet(viewsets.ModelViewSet):
    """Вьюсет для курсов"""

    model = Person
    serializer_class = PersonSerializer
    queryset = Person.objects.all()
