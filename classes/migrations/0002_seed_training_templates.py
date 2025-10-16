from datetime import datetime

from django.db import migrations

from school.choices import TrainingEquipment, TrainingKind, TrainingLocation


TEMPLATES = [
    {"weekday": 0, "time": "19:00", "season": "may-august", "kind": TrainingKind.ROLLERS, "location": TrainingLocation.MURINSKY_PARK, "group": "Спортивная группа"},
    {"weekday": 0, "time": "19:00", "season": "sep-nov", "kind": TrainingKind.FITNESS, "location": TrainingLocation.YUKKI, "group": "Спортивная группа"},
    {"weekday": 1, "time": "18:00", "season": "dec-apr", "kind": TrainingKind.SKI_BEGINNERS, "location": TrainingLocation.YUKKI, "group": "Средняя группа"},
    {"weekday": 1, "time": "19:00", "season": "may-august", "kind": TrainingKind.ROLLERS, "location": TrainingLocation.MURINSKY_PARK, "group": "Средняя группа"},
    {"weekday": 1, "time": "19:00", "season": "sep-nov", "kind": TrainingKind.FITNESS, "location": TrainingLocation.YUKKI, "group": "Средняя группа"},
    {"weekday": 1, "time": "19:00", "season": "dec-apr", "kind": TrainingKind.SKI, "location": TrainingLocation.YUKKI, "group": "Средняя группа"},
    {"weekday": 2, "time": "19:00", "season": "may-august", "kind": TrainingKind.ROLLERS, "location": TrainingLocation.MURINSKY_PARK, "group": "Спортивная группа"},
    {"weekday": 2, "time": "19:00", "season": "sep-nov", "kind": TrainingKind.FITNESS, "location": TrainingLocation.YUKKI, "group": "Спортивная группа"},
    {"weekday": 2, "time": "19:00", "season": "dec-apr", "kind": TrainingKind.SKI, "location": TrainingLocation.SNEZHNY, "group": "Спортивная группа"},
    {"weekday": 3, "time": "19:00", "season": "may-august", "kind": TrainingKind.ROLLERS, "location": TrainingLocation.MURINSKY_PARK, "group": "Средняя группа"},
    {"weekday": 3, "time": "19:00", "season": "sep-nov", "kind": TrainingKind.FITNESS, "location": TrainingLocation.YUKKI, "group": "Средняя группа"},
    {"weekday": 3, "time": "19:00", "season": "dec-apr", "kind": TrainingKind.SKI, "location": TrainingLocation.YUKKI, "group": "Средняя группа"},
    {"weekday": 3, "time": "18:00", "season": "dec-apr", "kind": TrainingKind.SKI_BEGINNERS, "location": TrainingLocation.YUKKI, "group": "Средняя группа"},
    {"weekday": 4, "time": "19:00", "season": "may-august", "kind": TrainingKind.ROLLERS, "location": TrainingLocation.MURINSKY_PARK, "group": "Спортивная группа"},
    {"weekday": 4, "time": "19:00", "season": "sep-nov", "kind": TrainingKind.FITNESS, "location": TrainingLocation.YUKKI, "group": "Спортивная группа"},
    {"weekday": 4, "time": "19:00", "season": "dec-apr", "kind": TrainingKind.SKI, "location": TrainingLocation.SNEZHNY, "group": "Спортивная группа"},
    {"weekday": 5, "time": "13:00", "season": "dec-apr", "kind": TrainingKind.SKI, "location": TrainingLocation.SNEZHNY, "group": "Спортивная группа"},
    {"weekday": 5, "time": "17:00", "season": "dec-apr", "kind": TrainingKind.SKI, "location": TrainingLocation.SNEZHNY, "group": "Спортивная группа"},
    {"weekday": 6, "time": "12:00", "season": "dec-apr", "kind": TrainingKind.SKI, "location": TrainingLocation.SNEZHNY, "group": "Спортивная группа"},
    {"weekday": 6, "time": "18:00", "season": "sep-nov", "kind": TrainingKind.FITNESS, "location": TrainingLocation.YUKKI, "group": "Средняя группа"},
    {"weekday": 6, "time": "18:00", "season": "dec-apr", "kind": TrainingKind.SKI, "location": TrainingLocation.YUKKI, "group": "Средняя группа"},
    {"weekday": 6, "time": "18:00", "season": "may-august", "kind": TrainingKind.ROLLERS, "location": TrainingLocation.UTC_KAVGOLOVO, "group": "Средняя группа"},
    {"weekday": 6, "time": "18:00", "season": "sep-nov", "kind": TrainingKind.FITNESS, "location": TrainingLocation.YUKKI, "group": "Спортивная группа"},
    {"weekday": 6, "time": "18:00", "season": "dec-apr", "kind": TrainingKind.SKI, "location": TrainingLocation.YUKKI, "group": "Спортивная группа"},
    {"weekday": 6, "time": "18:00", "season": "may-august", "kind": TrainingKind.ROLLERS, "location": TrainingLocation.UTC_KAVGOLOVO, "group": "Спортивная группа"},
]

SEASON_MAP = {
    "may-august": (5, 8, "Май-Август"),
    "sep-nov": (9, 11, "Сентябрь-Ноябрь"),
    "dec-apr": (12, 4, "Декабрь-Апрель"),
}

WEEKDAY_LABEL = {
    0: "Понедельник",
    1: "Вторник",
    2: "Среда",
    3: "Четверг",
    4: "Пятница",
    5: "Суббота",
    6: "Воскресенье",
}

EQUIPMENT_BY_KIND = {
    TrainingKind.ROLLERS: [TrainingEquipment.ROLLERS],
    TrainingKind.FITNESS: [TrainingEquipment.ATHLETIC],
    TrainingKind.SKI: [TrainingEquipment.SLALOM_SKI],
    TrainingKind.SKI_BEGINNERS: [TrainingEquipment.SLALOM_SKI],
    TrainingKind.SKI_DRILLS: [TrainingEquipment.SLALOM_SKI],
    TrainingKind.ICE: [TrainingEquipment.SKATES],
    TrainingKind.BIKE: [TrainingEquipment.BIKE],
}


def seed_templates(apps, schema_editor):
    Group = apps.get_model("school", "Group")
    TrainingTemplate = apps.get_model("classes", "TrainingTemplate")

    for item in TEMPLATES:
        season_start, season_end, season_label = SEASON_MAP[item["season"]]
        group_name = item["group"]
        group = Group.objects.filter(name__iexact=group_name).first()
        if not group:
            group = Group.objects.create(name=group_name)

        start_time = datetime.strptime(item["time"], "%H:%M").time()
        equipment = list(EQUIPMENT_BY_KIND.get(item["kind"], []))
        name = (
            f"{group.name} · {WEEKDAY_LABEL[item['weekday']]} {start_time:%H:%M} ({season_label})"
        )

        TrainingTemplate.objects.update_or_create(
            group=group,
            day_of_week=item["weekday"],
            start_time=start_time,
            season_start_month=season_start,
            season_end_month=season_end,
            defaults={
                "name": name,
                "duration_minutes": 90,
                "training_type": item["kind"],
                "location": item["location"],
                "equipment": equipment,
                "class_type": "regular",
                "comment": "",
                "is_active": True,
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("classes", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed_templates, migrations.RunPython.noop),
    ]
