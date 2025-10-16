from django.contrib import admin
from django.db import models as django_models

from bot.models import TelegramChat, TelegramParticipant


@admin.register(TelegramChat)
class TelegramChatAdmin(admin.ModelAdmin):
    list_display = [field.name for field in TelegramChat._meta.fields]
    search_fields = [
        field.name
        for field in TelegramChat._meta.fields
        if isinstance(field, (django_models.CharField, django_models.TextField))
    ]
    list_filter = [
        field.name
        for field in TelegramChat._meta.fields
        if field.choices
        or isinstance(field, (django_models.BooleanField, django_models.DateField, django_models.DateTimeField))
    ]
    ordering = ("-last_seen",)


@admin.register(TelegramParticipant)
class TelegramParticipantAdmin(admin.ModelAdmin):
    list_display = [field.name for field in TelegramParticipant._meta.fields]
    search_fields = [
        field.name
        for field in TelegramParticipant._meta.fields
        if isinstance(field, (django_models.CharField, django_models.TextField))
    ]
    list_filter = [
        field.name
        for field in TelegramParticipant._meta.fields
        if field.choices
        or isinstance(field, (django_models.BooleanField, django_models.DateField, django_models.DateTimeField))
    ]
    ordering = ("-last_seen", "-first_seen")
    autocomplete_fields = ("chat",)
