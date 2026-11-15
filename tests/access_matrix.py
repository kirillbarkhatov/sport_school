"""Матрица доступа: роль × страница/действие → код ответа. Фиксирует ТЕКУЩЕЕ поведение (R1, этап 4).

Любое изменение доступа должно сопровождаться правкой этой таблицы в том же PR — так изменения прав
становятся осознанными. KNOWN_HOLES — известные дыры: текущее поведение записано в EXPECTED (тест проходит),
а желаемое — в KNOWN_HOLES; при исправлении дыры (R2-2) обновите строку EXPECTED и уберите запись из KNOWN_HOLES.
"""

ROLES = ("anonymous", "pending", "approved", "coach", "manager", "admin", "superuser")

# "<МЕТОД> <имя URL>": коды для ролей в порядке ROLES. Снято на фикстурах tests/factories.py.
EXPECTED = {
    "GET api:assistant-upcoming-trainings": (403, 403, 200, 200, 200, 200, 200),
    "GET api:person-list": (403, 403, 403, 403, 403, 403, 200),
    "GET api:user-list": (403, 403, 403, 403, 403, 403, 200),
    "GET classes:class_create": (302, 302, 200, 200, 200, 200, 200),
    "GET classes:class_delete": (302, 302, 404, 404, 404, 404, 200),
    "GET classes:class_detail": (302, 302, 404, 404, 404, 404, 200),
    "GET classes:class_list": (302, 302, 200, 200, 200, 200, 200),
    "GET classes:class_notify": (302, 403, 403, 403, 403, 403, 200),
    "GET classes:class_update": (302, 302, 200, 200, 200, 200, 200),
    "GET groups:group_create": (302, 302, 200, 200, 200, 200, 200),
    "GET groups:group_delete": (302, 302, 404, 404, 404, 404, 200),
    "GET groups:group_detail": (302, 302, 404, 404, 404, 404, 200),
    "GET groups:group_list": (302, 302, 200, 200, 200, 200, 200),
    "GET groups:group_update": (302, 302, 200, 200, 200, 200, 200),
    "GET healthz": (200, 200, 200, 200, 200, 200, 200),
    "GET members:family_detail": (302, 302, 404, 404, 404, 404, 200),
    "GET members:family_finance": (302, 302, 404, 404, 404, 404, 200),
    "GET members:family_list": (302, 302, 200, 200, 200, 200, 200),
    "GET members:family_service_update": (302, 403, 403, 403, 403, 403, 200),
    "GET members:family_update": (302, 403, 403, 403, 403, 403, 200),
    "GET members:members_create": (302, 403, 403, 403, 403, 403, 200),
    "GET members:members_delete": (302, 403, 403, 403, 403, 403, 200),
    "GET members:members_detail": (302, 302, 404, 404, 404, 404, 200),
    "GET members:members_list": (302, 302, 200, 200, 200, 200, 200),
    "GET members:members_update": (302, 403, 403, 403, 403, 403, 200),
    "GET members:person_dedup_center": (302, 403, 403, 403, 403, 403, 200),
    "GET members:telegram_interlocutors": (302, 403, 403, 403, 200, 200, 200),
    "GET notifications:notification_create": (302, 403, 403, 403, 403, 403, 200),
    "GET notifications:notification_list": (302, 302, 200, 200, 200, 200, 200),
    "GET online-results-live": (403, 403, 403, 403, 200, 200, 200),
    "GET online-results-stream-runs": (403, 403, 403, 403, 200, 200, 200),
    "GET online-results-webhook-events": (403, 403, 403, 403, 200, 200, 200),
    "GET results:event_create": (302, 403, 403, 403, 200, 200, 200),
    "GET results:event_list": (302, 403, 403, 403, 200, 200, 200),
    "GET school:athlete_certificate_status": (302, 302, 404, 404, 404, 404, 200),
    "GET school:athlete_edit_compact": (302, 302, 302, 302, 302, 302, 200),
    "GET school:athlete_list": (302, 302, 200, 200, 200, 200, 200),
    "GET school:athlete_simple_list": (302, 302, 200, 200, 200, 200, 200),
    "GET school:competition_create": (302, 302, 200, 200, 200, 200, 200),
    "GET school:competition_edit": (302, 302, 200, 200, 200, 200, 200),
    "GET school:competition_export": (200, 200, 200, 200, 200, 200, 200),
    "GET school:competition_export_type2": (200, 200, 200, 200, 200, 200, 200),
    "GET school:competition_export_word_type1": (200, 200, 200, 200, 200, 200, 200),
    "GET school:competition_export_word_type2": (200, 200, 200, 200, 200, 200, 200),
    "GET school:competition_export_word_type3": (200, 200, 200, 200, 200, 200, 200),
    "GET school:competition_export_word_type4": (200, 200, 200, 200, 200, 200, 200),
    "GET school:competition_groups": (302, 302, 200, 200, 200, 200, 200),
    "GET school:competition_list": (302, 302, 200, 200, 200, 200, 200),
    "GET school:competition_venue_list": (302, 302, 200, 200, 200, 200, 200),
    "GET school:documents_analysis_list": (302, 302, 200, 200, 200, 200, 200),
    "GET school:documents_bulk_upload": (302, 302, 200, 200, 200, 200, 200),
    "GET school:edit_athlete": (302, 302, 302, 302, 302, 302, 200),
    "GET school:index": (302, 302, 200, 200, 200, 200, 200),
    "GET school:user_main_info": (302, 302, 302, 302, 302, 302, 302),
    "GET users:awaiting_approval": (302, 200, 302, 302, 302, 302, 302),
    "GET users:login_page": (200, 200, 200, 200, 200, 200, 200),
    "POST classes:class_create": (302, 302, 200, 200, 200, 200, 200),
    "POST groups:group_create": (302, 302, 200, 200, 200, 200, 200),
    "POST members:members_create": (302, 403, 403, 403, 403, 403, 200),
    "POST notifications:notification_create": (302, 403, 403, 403, 403, 403, 200),
    "POST school:competition_create": (302, 302, 200, 200, 200, 200, 200),
    "POST school:documents_bulk_upload": (302, 302, 200, 200, 200, 200, 200),
}

_STAFF_ONLY = {"anonymous": 302, "pending": 302, "approved": 403}
_NOT_PUBLIC = {"anonymous": 302, "pending": 302, "approved": 403}

# Желаемое поведение там, где текущее слишком открыто. Исправляется в R2-2 (docs/stage4-releases.md).
KNOWN_HOLES = {
    # Родитель (подтверждённый пользователь без роли) открывает и отправляет формы сотрудников
    "GET school:competition_create": _STAFF_ONLY,
    "POST school:competition_create": _STAFF_ONLY,
    "GET school:competition_edit": _STAFF_ONLY,
    "GET school:competition_groups": _STAFF_ONLY,
    "GET classes:class_create": _STAFF_ONLY,
    "POST classes:class_create": _STAFF_ONLY,
    "GET classes:class_update": _STAFF_ONLY,
    "GET groups:group_create": _STAFF_ONLY,
    "POST groups:group_create": _STAFF_ONLY,
    "GET groups:group_update": _STAFF_ONLY,
    "GET school:documents_bulk_upload": _STAFF_ONLY,
    "POST school:documents_bulk_upload": _STAFF_ONLY,
    "GET school:documents_analysis_list": _STAFF_ONLY,
    # Выгрузки заявок на соревнования доступны без входа
    "GET school:competition_export": _NOT_PUBLIC,
    "GET school:competition_export_type2": _NOT_PUBLIC,
    "GET school:competition_export_word_type1": _NOT_PUBLIC,
    "GET school:competition_export_word_type2": _NOT_PUBLIC,
    "GET school:competition_export_word_type3": _NOT_PUBLIC,
    "GET school:competition_export_word_type4": _NOT_PUBLIC,
}
