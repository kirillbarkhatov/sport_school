from rest_framework.serializers import ModelSerializer, SerializerMethodField

from school.models import Person


class PersonSerializer(ModelSerializer):
    """Сериализатор для членов клуба"""

    class Meta:
        model = Person
        fields = "__all__"
