from rest_framework import serializers
from school.models import Person
from users.models import User
from whatsapp.models import WhatsAppChat, WhatsAppChatMember


class PersonSerializer(serializers.ModelSerializer):
    """Сериализатор для членов клуба"""

    class Meta:
        model = Person
        fields = "__all__"


class UserSerializer(serializers.ModelSerializer):
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


class WhatsAppChatMemberPayloadSerializer(serializers.Serializer):
    member_id = serializers.CharField(max_length=255)
    phone = serializers.CharField(max_length=64, allow_blank=True, allow_null=True, default="")
    push_name = serializers.CharField(max_length=255, allow_blank=True, allow_null=True, default="")
    is_admin = serializers.BooleanField(default=False)
    is_super_admin = serializers.BooleanField(default=False)

    def validate_phone(self, value: str | None) -> str:
        return value or ""

    def validate_push_name(self, value: str | None) -> str:
        return value or ""


class WhatsAppChatSyncSerializer(serializers.Serializer):
    group_name = serializers.CharField(max_length=255)
    synced_at = serializers.DateTimeField()
    members = WhatsAppChatMemberPayloadSerializer(many=True)

    def save(self, **kwargs):
        validated = self.validated_data
        synced_at = validated["synced_at"]
        chat, _ = WhatsAppChat.objects.get_or_create(name=validated["group_name"])

        if chat.last_synced_at != synced_at:
            chat.last_synced_at = synced_at
            chat.save(update_fields=["last_synced_at", "updated_at"])

        seen_pks: list[int] = []
        for member_data in validated["members"]:
            defaults = {
                "phone": member_data["phone"],
                "push_name": member_data["push_name"],
                "is_admin": member_data["is_admin"],
                "is_super_admin": member_data["is_super_admin"],
                "last_seen_at": synced_at,
            }
            member, created = WhatsAppChatMember.objects.get_or_create(
                chat=chat,
                member_id=member_data["member_id"],
                defaults=defaults,
            )
            if not created:
                has_changes = False
                for field in ("phone", "push_name", "is_admin", "is_super_admin"):
                    new_value = defaults[field]
                    if getattr(member, field) != new_value:
                        setattr(member, field, new_value)
                        has_changes = True
                if member.last_seen_at != synced_at:
                    member.last_seen_at = synced_at
                    has_changes = True
                if not member.is_active:
                    member.is_active = True
                    has_changes = True
                if member.left_at is not None:
                    member.left_at = None
                    has_changes = True
                if has_changes:
                    member.save()
            else:
                member.is_active = True
                member.left_at = None
                member.save(update_fields=["is_active", "left_at"])

            seen_pks.append(member.pk)

        WhatsAppChatMember.objects.filter(chat=chat).exclude(pk__in=seen_pks).update(
            is_active=False,
            left_at=synced_at,
        )

        return chat


class AssistantGroupSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField()


class AssistantEnrollmentSerializer(serializers.Serializer):
    athlete_id = serializers.IntegerField()
    confirmed = serializers.BooleanField()
    status = serializers.CharField()


class AssistantTrainingSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    start_date = serializers.DateField()
    start_time = serializers.TimeField()
    start_datetime = serializers.DateTimeField()
    duration_minutes = serializers.IntegerField()
    location = serializers.CharField()
    training_type = serializers.CharField()
    format = serializers.CharField()
    format_display = serializers.CharField()
    equipment = serializers.ListField(child=serializers.CharField(), allow_empty=True)
    comment = serializers.CharField(allow_null=True, allow_blank=True)
    group = AssistantGroupSummarySerializer()
    athletes = AssistantEnrollmentSerializer(many=True)


class AssistantPersonSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    name = serializers.CharField(allow_null=True, allow_blank=True)
    surname = serializers.CharField(allow_null=True, allow_blank=True)
    middlename = serializers.CharField(allow_null=True, allow_blank=True)
    phone = serializers.CharField(allow_null=True, allow_blank=True)
    email = serializers.EmailField(allow_null=True, allow_blank=True)
    telegram = serializers.CharField(allow_null=True, allow_blank=True)


class AssistantAthleteSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    full_name = serializers.CharField()
    level = serializers.CharField(allow_null=True, allow_blank=True)
    rank = serializers.CharField(allow_null=True, allow_blank=True)
    comment = serializers.CharField(allow_null=True, allow_blank=True)
    groups = AssistantGroupSummarySerializer(many=True)
    person = AssistantPersonSerializer()
