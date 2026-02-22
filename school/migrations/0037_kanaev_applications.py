import datetime
from django.db import migrations


# Данные вынуты из файлов в папке «примеры заявок»
ATHLETES = [
    {"fio": "Никифорова Александра Евгеньевна", "dob": "17.08.2016", "rank": "БР"},
    {"fio": "Боева Елизавета Юрьевна", "dob": "30.12.2016", "rank": "БР"},
    {"fio": "Минасян Александра Владимировна", "dob": "05.07.2016", "rank": "БР"},
    {"fio": "Бархатов Никон Кириллович", "dob": "02.01.2016", "rank": "БР"},
    {"fio": "Сметанин Тимур Евгеньевич", "dob": "05.03.2016", "rank": "БР"},
    {"fio": "Кирсанов Марк Артурович", "dob": "08.03.2016", "rank": "БР"},
    {"fio": "Вавилов Артём Дмитриевич", "dob": "22.12.2015", "rank": "2ю"},
    {"fio": "Зарезина Вероника Павловна", "dob": "08.04.2013", "rank": "I"},
    {"fio": "Романюкина Анна Викторовна", "dob": "08.10.2012", "rank": "I"},
    {"fio": "Мельников Артём Владимирович", "dob": "30.08.2011", "rank": "II"},
    {"fio": "Кошутин Владислав Вячеславович", "dob": "29.11.2011", "rank": "II"},
    {"fio": "Григоров Егор Дмитриевич", "dob": "16.09.2010", "rank": "II"},
    {"fio": "Кузнецов Лев Евгеньевич", "dob": "05.10.2017", "rank": "БР"},
    {"fio": "Григоров Юрий Дмитриевич", "dob": "11.08.2016", "rank": "3ю"},
    {"fio": "Захарьян Роберт Кириллович", "dob": "28.01.2017", "rank": "БР"},
    {"fio": "Коробов Андрей Константинович", "dob": "12.12.2015", "rank": "1ю"},
    {"fio": "Мельников Роман Владимирович", "dob": "06.02.2018", "rank": "БР"},
    {"fio": "Сметанин Артём Евгеньевич", "dob": "14.05.2018", "rank": "БР"},
    {"fio": "Ботыгин Тимофей Евгеньевич", "dob": "29.04.2018", "rank": "БР"},
    {"fio": "Алексеев Лев Вячеславович", "dob": "20.02.2018", "rank": "БР"},
    {"fio": "Тярина Мирослава Максимовна", "dob": "03.09.2011", "rank": "III"},
    {"fio": "Ильин Мирон Владиславович", "dob": "26.10.2010", "rank": "II"},
]


def _normalize_rank(value: str):
    raw = (value or "").strip().replace(" ", "").replace(".", "").lower()
    mapping = {
        "бр": "БР",
        "б/р": "БР",
        "3юн": "3ю",
        "3ю": "3ю",
        "2юн": "2ю",
        "2ю": "2ю",
        "1юн": "1ю",
        "1ю": "1ю",
        "i": "I",
        "ii": "II",
        "iii": "III",
    }
    return mapping.get(raw, value or None)


def _normalize_key(text: str) -> str:
    return " ".join(text.split()).lower().replace("ё", "е")


def _parse_date(text: str):
    cleaned = (text or "").replace(" ", "")
    try:
        return datetime.datetime.strptime(cleaned, "%d.%m.%Y").date()
    except Exception:
        return None


def _infer_gender(middlename: str):
    base = (middlename or "").lower()
    return "female" if base.endswith("на") else "male"


def import_kanaev(apps, schema_editor):
    Person = apps.get_model("school", "Person")
    Athlete = apps.get_model("school", "Athlete")
    Club = apps.get_model("school", "Club")

    DEFAULT_LEVEL = "unknown"
    club, _ = Club.objects.get_or_create(name="Канаев Ски Клаб")

    person_map = {}
    for person in Person.objects.all():
        full = _normalize_key(
            " ".join(filter(None, [person.surname, person.name, person.middlename or ""]))
        )
        short = _normalize_key(f"{person.surname} {person.name}")
        if full:
            person_map.setdefault(full, person)
        if short:
            person_map.setdefault(short, person)

    for entry in ATHLETES:
        fio = entry["fio"]
        dob = _parse_date(entry["dob"])
        rank = _normalize_rank(entry["rank"])

        parts = fio.split()
        surname = parts[0] if parts else ""
        name = parts[1] if len(parts) > 1 else ""
        middlename = " ".join(parts[2:]) or None

        key_full = _normalize_key(" ".join(filter(None, [surname, name, middlename or ""])))
        key_short = _normalize_key(f"{surname} {name}")

        person = person_map.get(key_full) or person_map.get(key_short)
        if not person:
            person = Person.objects.create(
                surname=surname,
                name=name,
                middlename=middlename,
                date_of_birth=dob,
                club=club,
                gender=_infer_gender(middlename),
            )
            person_map[key_full] = person
            person_map.setdefault(key_short, person)
        else:
            changed = False
            if dob and person.date_of_birth != dob:
                person.date_of_birth = dob
                changed = True
            if middlename and person.middlename != middlename:
                person.middlename = middlename
                changed = True
            if person.club_id != club.id:
                person.club = club
                changed = True
            if changed:
                person.save()

        athlete, created = Athlete.objects.get_or_create(
            person=person,
            defaults={"level": DEFAULT_LEVEL, "rank": rank},
        )
        if not created:
            updates = []
            if rank and athlete.rank != rank:
                athlete.rank = rank
                updates.append("rank")
            if not athlete.level:
                athlete.level = DEFAULT_LEVEL
                updates.append("level")
            if updates:
                athlete.save(update_fields=updates)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("school", "0036_alter_athlete_rank"),
    ]

    operations = [
        migrations.RunPython(import_kanaev, noop_reverse),
    ]
