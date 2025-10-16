from django.contrib import admin
from django.db import models as django_models

from .models import WhatsAppChat, WhatsAppChatMember


@admin.register(WhatsAppChat)
class WhatsAppChatAdmin(admin.ModelAdmin):
    list_display = [field.name for field in WhatsAppChat._meta.fields]
    search_fields = [
        field.name
        for field in WhatsAppChat._meta.fields
        if isinstance(field, (django_models.CharField, django_models.TextField))
    ]
    list_filter = [
        field.name
        for field in WhatsAppChat._meta.fields
        if field.choices
        or isinstance(field, (django_models.BooleanField, django_models.DateField, django_models.DateTimeField))
    ]
    ordering = ("-last_synced_at", "-updated_at")


@admin.register(WhatsAppChatMember)
class WhatsAppChatMemberAdmin(admin.ModelAdmin):
    list_display = [field.name for field in WhatsAppChatMember._meta.fields]
    search_fields = [
        field.name
        for field in WhatsAppChatMember._meta.fields
        if isinstance(field, (django_models.CharField, django_models.TextField))
    ]
    list_filter = [
        field.name
        for field in WhatsAppChatMember._meta.fields
        if field.choices
        or isinstance(field, (django_models.BooleanField, django_models.DateField, django_models.DateTimeField))
    ]
    ordering = ("-last_seen_at", "-first_seen_at")
    autocomplete_fields = ("chat",)
