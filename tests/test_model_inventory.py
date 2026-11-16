"""Инвентаризация моделей для введения организаций (docs/organizations-intro.md, «Классификация таблиц»).

Каждая модель проекта отнесена ровно к одной группе. Новая модель без группы ломает тест: при её
добавлении нужно решить, принадлежит ли она организации.
"""

from django.apps import apps

# Данные организации: получат поле organization (R2-3)
ORG_OWNED = {
    "school.Person",
    "school.Athlete",
    "school.Coach",
    "school.PotentialClient",
    "school.Group",
    "school.Class",
    "school.ClassEnrollment",
    "school.TrainingCamp",
    "school.CampEnrollment",
    "school.Competition",
    "school.CompetitionScoringGroup",
    "school.CompetitionApplicationLink",
    "school.CompetitionApplication",
    "school.CompetitionEntry",
    "school.Document",
    "school.CompetitionDocument",
    "school.AthleteDocument",
    "school.DocumentAIAnalysis",
    "school.DocumentAIJob",
    "school.DocumentAIJobItem",
    "school.Family",
    "school.FamilyMember",
    "school.FamilyAthleteProfile",
    "school.AthleteContract",
    "school.FamilyService",
    "school.FamilyPayment",
    "classes.TrainingTemplate",
    "notifications.Notification",
    "members.PersonDedupJob",
    "members.PersonDuplicateCluster",
    "members.PersonDuplicateItem",
    "members.PersonMergeRedirect",
    "members.PersonMergeLog",
    "results.ResultsEvent",
    "results.ResultGroup",
    "results.ResultEntry",
    "results.EventLog",
    "results.RawSnapshot",
    "results.TelegramPublication",
    "whatsapp.WhatsAppChat",
    "whatsapp.WhatsAppChatMember",
    "users.TrainingReminderLog",
    "users.UserPersonLink",
    "users.UserAthleteLink",
    "assistant.AssistantUnmatchedParticipant",
}

# Справочники — будущие организации (этап 7): получат необязательную ссылку на организацию (R1-3)
DIRECTORY = {
    "school.Club",
    "school.CompetitionVenue",
}

# Платформа: глобальные сущности
PLATFORM = {
    "users.User",
    "organizations.Organization",
    "organizations.OrgDomain",
    "organizations.OrgRole",
    "organizations.Membership",
    "organizations.RoleAssignment",
    "organizations.PlatformRole",
    "organizations.AuditLog",
    "organizations.AuthIdentity",  # R3-1: способы входа учётной записи платформы
    "organizations.LoginTicket",  # R3-1: одноразовый вход через бота
    "organizations.Invitation",  # R3-3: приглашение в организацию (связь с организацией — FK)
    "bot.TelegramChat",
    "bot.TelegramParticipant",
    "assistant.ServiceAccount",
    "assistant.AssistantSyncLog",
}

# Наследие online_results_script: организация только ради истории
LEGACY = {
    "api.StreamRun",
    "api.WebhookEvent",
    "api.PublicStreamAccess",
}

PROJECT_APPS = {
    "organizations",
    "school",
    "members",
    "classes",
    "groups",
    "users",
    "notifications",
    "bot",
    "whatsapp",
    "assistant",
    "api",
    "results",
}
GROUPS = {"ORG_OWNED": ORG_OWNED, "DIRECTORY": DIRECTORY, "PLATFORM": PLATFORM, "LEGACY": LEGACY}


def _project_models() -> set[str]:
    return {
        f"{model._meta.app_label}.{model.__name__}"
        for model in apps.get_models()
        if model._meta.app_label in PROJECT_APPS
    }


def test_every_model_is_classified():
    classified = set().union(*GROUPS.values())
    missing = _project_models() - classified
    assert not missing, f"Модели без группы (ORG_OWNED/DIRECTORY/PLATFORM/LEGACY): {sorted(missing)}"


def test_classification_has_no_stale_or_duplicate_entries():
    existing = _project_models()
    for name, group in GROUPS.items():
        stale = group - existing
        assert not stale, f"{name}: моделей больше нет: {sorted(stale)}"
    seen = {}
    for name, group in GROUPS.items():
        for model in group:
            assert model not in seen, f"{model} в двух группах: {seen[model]} и {name}"
            seen[model] = name
