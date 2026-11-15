from datetime import timedelta

import pytest
from django.utils import timezone

from school.models import AthleteDocument, Document, DocumentType
from school.tasks import reconcile_athlete_certificate_validity

from .factories import AthleteFactory


@pytest.mark.django_db
def test_reconciliation_replaces_current_certificate_with_newer_valid_one():
    athlete = AthleteFactory()
    valid_until = timezone.localdate() + timedelta(days=30)
    previous = AthleteDocument.objects.create(
        athlete=athlete,
        document=Document.objects.create(original_name="previous.pdf"),
        doc_type=DocumentType.MED_CERT,
        valid_until=valid_until,
        is_actual=True,
    )
    replacement = AthleteDocument.objects.create(
        athlete=athlete,
        document=Document.objects.create(original_name="replacement.pdf"),
        doc_type=DocumentType.MED_CERT,
        valid_until=valid_until,
    )

    reconcile_athlete_certificate_validity()

    previous.refresh_from_db()
    replacement.refresh_from_db()
    assert not previous.is_actual
    assert replacement.is_actual
