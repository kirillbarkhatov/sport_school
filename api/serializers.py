from rest_framework.serializers import ModelSerializer, SerializerMethodField

from school.models import Person
from users.models import User


class PersonSerializer(ModelSerializer):
    """Сериализатор для членов клуба"""

    class Meta:
        model = Person
        fields = "__all__"


class UserSerializer(ModelSerializer):
    """Сериализатор для пользователей"""

    def create(self, validated_data):
        print(validated_data)
        raw_password = validated_data.pop("password")
        user = User.objects.create(**validated_data)
        user.set_password(raw_password)
        return user

    class Meta:
        model = User
        fields = "__all__"

