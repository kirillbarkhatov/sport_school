from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ("school", "0038_merge_20260222_1530"),
        ("users", "0028_user_bot_access"),
    ]

    operations = [
        migrations.CreateModel(
            name="Document",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("file", models.FileField(max_length=255, upload_to="documents/%Y/%m/", verbose_name="Файл")),
                ("original_name", models.CharField(max_length=255, verbose_name="Исходное имя")),
                ("mime_type", models.CharField(blank=True, max_length=100, verbose_name="MIME-тип")),
                ("size", models.PositiveIntegerField(default=0, verbose_name="Размер, байт")),
                ("description", models.CharField(blank=True, max_length=255, verbose_name="Описание")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Загружено")),
                (
                    "uploaded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="uploaded_documents",
                        to=settings.AUTH_USER_MODEL,
                        verbose_name="Кем загружено",
                    ),
                ),
            ],
            options={
                "verbose_name": "Документ",
                "verbose_name_plural": "Документы",
            },
        ),
        migrations.CreateModel(
            name="AthleteDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "doc_type",
                    models.CharField(
                        choices=[
                            ("regulation", "Регламент/Положение"),
                            ("schedule", "Расписание/Распорядок"),
                            ("start_list", "Стартовый лист"),
                            ("start_list_second", "Стартовый лист второй попытки"),
                            ("intermediate_results", "Промежуточные результаты"),
                            ("prelim_results", "Результаты предварительные"),
                            ("official_results", "Результаты официальные"),
                            ("application_form", "Форма заявки"),
                            ("parent_consent", "Согласие родителей"),
                            ("medical_certificate", "Медицинская справка"),
                            ("insurance", "Страховка"),
                            ("other", "Прочее"),
                        ],
                        max_length=50,
                        verbose_name="Тип документа",
                    ),
                ),
                ("issued_at", models.DateField(blank=True, null=True, verbose_name="Дата выдачи")),
                ("valid_until", models.DateField(blank=True, null=True, verbose_name="Действителен до")),
                (
                    "is_default",
                    models.BooleanField(
                        default=False,
                        help_text="Будет подставляться в заявки, если тип совпадает",
                        verbose_name="Использовать по умолчанию",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Добавлено")),
                (
                    "athlete",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="documents",
                        to="school.athlete",
                        verbose_name="Спортсмен",
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="athlete_links",
                        to="school.document",
                        verbose_name="Документ",
                    ),
                ),
            ],
            options={
                "verbose_name": "Документ спортсмена",
                "verbose_name_plural": "Документы спортсменов",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="CompetitionDocument",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "doc_type",
                    models.CharField(
                        choices=[
                            ("regulation", "Регламент/Положение"),
                            ("schedule", "Расписание/Распорядок"),
                            ("start_list", "Стартовый лист"),
                            ("start_list_second", "Стартовый лист второй попытки"),
                            ("intermediate_results", "Промежуточные результаты"),
                            ("prelim_results", "Результаты предварительные"),
                            ("official_results", "Результаты официальные"),
                            ("application_form", "Форма заявки"),
                            ("parent_consent", "Согласие родителей"),
                            ("medical_certificate", "Медицинская справка"),
                            ("insurance", "Страховка"),
                            ("other", "Прочее"),
                        ],
                        max_length=50,
                        verbose_name="Тип документа",
                    ),
                ),
                (
                    "title",
                    models.CharField(
                        blank=True,
                        help_text="Отображается в списке документов",
                        max_length=255,
                        verbose_name="Название/подпись",
                    ),
                ),
                ("is_public", models.BooleanField(default=False, verbose_name="Доступно участникам")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Добавлено")),
                (
                    "competition",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="documents",
                        to="school.competition",
                        verbose_name="Соревнование",
                    ),
                ),
                (
                    "document",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="competition_links",
                        to="school.document",
                        verbose_name="Документ",
                    ),
                ),
            ],
            options={
                "verbose_name": "Документ соревнования",
                "verbose_name_plural": "Документы соревнования",
                "ordering": ["-created_at"],
            },
        ),
    ]
