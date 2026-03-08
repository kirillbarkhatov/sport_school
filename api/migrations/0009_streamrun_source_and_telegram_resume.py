from __future__ import annotations

import re
from urllib import parse

from django.db import migrations, models
from django.utils import timezone


GOOGLE_ID_PATTERN = re.compile(r"/d/([a-zA-Z0-9_-]{20,})")
PLAIN_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{20,}$")


def _extract_source_id(link_or_id: str) -> str:
    raw = (link_or_id or "").strip()
    if not raw:
        return ""
    if PLAIN_ID_PATTERN.match(raw):
        return raw
    match = GOOGLE_ID_PATTERN.search(raw)
    if match:
        return match.group(1)
    parsed_url = parse.urlparse(raw)
    query = parse.parse_qs(parsed_url.query)
    query_id = (query.get("id") or [""])[0]
    if query_id and PLAIN_ID_PATTERN.match(query_id):
        return query_id
    return raw.lower()


def _populate_source_ids(apps, schema_editor):
    StreamRun = apps.get_model("api", "StreamRun")
    for run in StreamRun.objects.order_by("created_at", "id"):
        source_id = _extract_source_id(getattr(run, "protocol_link", ""))
        run.source_id = source_id
        run.last_requested_at = run.created_at or timezone.now()
        run.save(update_fields=["source_id", "last_requested_at", "updated_at"])


def _noop(apps, schema_editor):
    return


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("api", "0008_streamrun_telegram_link_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="streamrun",
            name="source_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="streamrun",
            name="telegram_resume_from_event_id",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="streamrun",
            name="last_requested_at",
            field=models.DateTimeField(db_index=True, default=timezone.now),
        ),
        migrations.RunPython(_populate_source_ids, reverse_code=_noop),
    ]
