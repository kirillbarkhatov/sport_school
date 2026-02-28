from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school", "0045_alter_competition_birth_year_from_documentaijob_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="athletedocument",
            name="is_actual",
            field=models.BooleanField(default=False, verbose_name="Актуальный документ"),
        ),
        migrations.AddField(
            model_name="athletedocument",
            name="needs_valid_until_clarification",
            field=models.BooleanField(default=False, verbose_name="Требуется уточнение срока действия"),
        ),
        migrations.AddIndex(
            model_name="athletedocument",
            index=models.Index(fields=["doc_type", "is_actual"], name="school_athle_doc_typ_0ce5f3_idx"),
        ),
        migrations.AddIndex(
            model_name="athletedocument",
            index=models.Index(fields=["needs_valid_until_clarification"], name="school_athle_needs_v_d2de15_idx"),
        ),
        migrations.AddConstraint(
            model_name="athletedocument",
            constraint=models.UniqueConstraint(fields=("athlete", "document"), name="uniq_athlete_document_athlete_document"),
        ),
        migrations.AddConstraint(
            model_name="athletedocument",
            constraint=models.UniqueConstraint(
                condition=models.Q(doc_type="medical_certificate", is_actual=True),
                fields=("athlete", "doc_type"),
                name="uniq_active_med_cert_per_athlete",
            ),
        ),
    ]
