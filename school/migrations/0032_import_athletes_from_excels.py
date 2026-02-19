import datetime
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

from django.db import migrations
from django.conf import settings


def _read_shared_strings(zf, ns):
    strings = []
    if "xl/sharedStrings.xml" not in zf.namelist():
        return strings
    sst = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    for si in sst.findall("main:si", ns):
        text = "".join(node.text or "" for node in si.iter() if node.tag.endswith("}t"))
        strings.append(text.strip())
    return strings


def _read_sheet_rows(path):
    ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as zf:
        strings = _read_shared_strings(zf, ns)
        workbook = ET.fromstring(zf.read("xl/workbook.xml"))
        sheets = workbook.find("main:sheets", ns)
        sheet_rel = sheets.find("main:sheet", ns).attrib[
            "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
        ]
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
        rel_map = {r.attrib["Id"]: r.attrib["Target"] for r in rels}
        sheet_path = "xl/" + rel_map[sheet_rel]
        sheet = ET.fromstring(zf.read(sheet_path))
        rows = []
        for row in sheet.findall("main:sheetData/main:row", ns):
            values = []
            for cell in row.findall("main:c", ns):
                v = cell.find("main:v", ns)
                text = v.text if v is not None else ""
                if cell.attrib.get("t") == "s":
                    try:
                        text = strings[int(text)]
                    except Exception:
                        pass
                values.append((text or "").strip())
            rows.append(values)
        return rows


def _excel_serial_to_date(value):
    try:
        serial = int(value)
    except Exception:
        return None
    try:
        base = datetime.date(1899, 12, 30)
        return base + datetime.timedelta(days=serial)
    except Exception:
        return None


def import_athletes(apps, schema_editor):
    Person = apps.get_model("school", "Person")
    Athlete = apps.get_model("school", "Athlete")
    Club = apps.get_model("school", "Club")
    DEFAULT_LEVEL = "2025-2026"

    files = [
        "1. Заявка Юный горнолыжник 04.02.xlsx",
        "Заявка Кубок 47 рег 25.02.xlsx",
        "Заявка открытие сезона.xlsx",
    ]
    base_dir = Path(settings.BASE_DIR)

    for fname in files:
        path = base_dir / fname
        if not path.exists():
            continue
        try:
            rows = _read_sheet_rows(path)
        except Exception:
            continue
        # skip first two rows (title + header)
        for row in rows[2:]:
            if len(row) < 2:
                continue
            full_name = row[1].strip()
            if not full_name:
                continue
            parts = full_name.split()
            surname = parts[0]
            name = parts[1] if len(parts) > 1 else ""
            middlename = " ".join(parts[2:]) if len(parts) > 2 else ""
            dob_raw = row[2] if len(row) > 2 else ""
            rank = row[3] if len(row) > 3 else ""
            club_name = row[5] if len(row) > 5 else ""
            dob_date = None
            if dob_raw:
                if dob_raw.isdigit() and len(dob_raw) == 4:
                    dob_date = datetime.date(int(dob_raw), 1, 1)
                else:
                    dob_date = _excel_serial_to_date(dob_raw)
            club = None
            if club_name:
                club, _ = Club.objects.get_or_create(name=club_name)
            person_qs = Person.objects.filter(surname=surname, name=name)
            if dob_date:
                person_qs = person_qs.filter(date_of_birth=dob_date)
            person = person_qs.first()
            if not person:
                person = Person.objects.create(
                    surname=surname,
                    name=name,
                    middlename=middlename or None,
                    date_of_birth=dob_date,
                    club=club,
                    gender="male",
                )
            else:
                changed = False
                if middlename and person.middlename != middlename:
                    person.middlename = middlename
                    changed = True
                if club and person.club_id != club.id:
                    person.club = club
                    changed = True
                if dob_date and not person.date_of_birth:
                    person.date_of_birth = dob_date
                    changed = True
                if changed:
                    person.save()

            athlete, created = Athlete.objects.get_or_create(
                person=person,
                defaults={"level": DEFAULT_LEVEL, "rank": rank or None},
            )
            if not created and rank and athlete.rank != rank:
                athlete.rank = rank
                athlete.save(update_fields=["rank"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("school", "0031_club_person_club_competition_dates"),
    ]

    operations = [
        migrations.RunPython(import_athletes, noop_reverse),
    ]
