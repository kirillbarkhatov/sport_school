from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("school", "0049_competitionscoringgroup_standard_category_and_more"),
        ("users", "0028_user_bot_access"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PersonDedupJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("queued", "В очереди"), ("running", "Выполняется"), ("completed", "Завершено"), ("failed", "Ошибка"), ("canceled", "Отменено")], default="queued", max_length=16, verbose_name="Статус")),
                ("mode", models.CharField(choices=[("analyze", "Только анализ"), ("auto_safe", "Анализ + автообъединение безопасных")], default="analyze", max_length=16, verbose_name="Режим")),
                ("dry_run", models.BooleanField(default=True, verbose_name="Пробный запуск")),
                ("min_score", models.PositiveSmallIntegerField(default=55, verbose_name="Порог поиска")),
                ("auto_merge_score", models.PositiveSmallIntegerField(default=92, verbose_name="Порог автообъединения")),
                ("total_persons", models.PositiveIntegerField(default=0, verbose_name="Всего персон")),
                ("processed_persons", models.PositiveIntegerField(default=0, verbose_name="Обработано персон")),
                ("duplicate_clusters_found", models.PositiveIntegerField(default=0, verbose_name="Найдено кластеров")),
                ("auto_merged_clusters", models.PositiveIntegerField(default=0, verbose_name="Автообъединено")),
                ("conflicts_count", models.PositiveIntegerField(default=0, verbose_name="Требует решения")),
                ("error_message", models.TextField(blank=True, verbose_name="Ошибка")),
                ("log", models.JSONField(blank=True, default=list, verbose_name="Лог запуска")),
                ("started_at", models.DateTimeField(blank=True, null=True, verbose_name="Начато")),
                ("finished_at", models.DateTimeField(blank=True, null=True, verbose_name="Завершено")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="dedup_jobs", to=settings.AUTH_USER_MODEL, verbose_name="Запущено пользователем")),
            ],
            options={
                "verbose_name": "Задача дедупликации персон",
                "verbose_name_plural": "Задачи дедупликации персон",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="PersonDuplicateCluster",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("open", "Открыт"), ("skipped", "Пропущен"), ("merged", "Объединён"), ("auto_merged", "Автообъединён")], default="open", max_length=16, verbose_name="Статус")),
                ("confidence", models.PositiveSmallIntegerField(default=0, verbose_name="Уверенность")),
                ("reason_summary", models.TextField(blank=True, verbose_name="Краткое объяснение")),
                ("max_pair_score", models.PositiveSmallIntegerField(default=0, verbose_name="Макс. score")),
                ("candidate_pairs", models.PositiveIntegerField(default=0, verbose_name="Связей в кластере")),
                ("requires_manual_review", models.BooleanField(default=True, verbose_name="Нужно ручное решение")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
                ("job", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="clusters", to="members.persondedupjob", verbose_name="Задача")),
            ],
            options={
                "verbose_name": "Кластер дублей персон",
                "verbose_name_plural": "Кластеры дублей персон",
                "ordering": ["-max_pair_score", "-created_at"],
            },
        ),
        migrations.CreateModel(
            name="PersonDuplicateItem",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("aggregate_score", models.PositiveSmallIntegerField(default=0, verbose_name="Суммарный score")),
                ("reasons", models.JSONField(blank=True, default=list, verbose_name="Причины")),
                ("is_suggested_master", models.BooleanField(default=False, verbose_name="Рекомендуемый мастер")),
                ("cluster", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="items", to="members.personduplicatecluster", verbose_name="Кластер")),
                ("person", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="dedup_items", to="school.person", verbose_name="Персона")),
            ],
            options={
                "verbose_name": "Участник кластера дублей",
                "verbose_name_plural": "Участники кластеров дублей",
                "ordering": ["-aggregate_score", "person__surname", "person__name"],
            },
        ),
        migrations.CreateModel(
            name="PersonMergeLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("merged_person_ids", models.JSONField(default=list, verbose_name="ID объединённых персон")),
                ("field_resolution", models.JSONField(blank=True, default=dict, verbose_name="Разрешение полей")),
                ("moved_relations", models.JSONField(blank=True, default=dict, verbose_name="Перенесённые связи")),
                ("notes", models.TextField(blank=True, verbose_name="Комментарий")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("cluster", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="merge_logs", to="members.personduplicatecluster", verbose_name="Кластер")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="person_merge_logs", to=settings.AUTH_USER_MODEL, verbose_name="Исполнитель")),
                ("job", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="merge_logs", to="members.persondedupjob", verbose_name="Задача")),
                ("master_person", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="merge_logs_as_master", to="school.person", verbose_name="Итоговая персона")),
            ],
            options={
                "verbose_name": "Журнал объединения персон",
                "verbose_name_plural": "Журнал объединений персон",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="PersonMergeRedirect",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("is_active", models.BooleanField(default=True, verbose_name="Активен")),
                ("job", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="redirects", to="members.persondedupjob", verbose_name="Задача")),
                ("source_person", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="merge_redirect_source", to="school.person", verbose_name="Исходная персона")),
                ("target_person", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="merge_redirect_targets", to="school.person", verbose_name="Целевая персона")),
            ],
            options={
                "verbose_name": "Редирект объединённой персоны",
                "verbose_name_plural": "Редиректы объединённых персон",
            },
        ),
        migrations.AddConstraint(
            model_name="personduplicateitem",
            constraint=models.UniqueConstraint(fields=("cluster", "person"), name="members_duplicate_item_unique_cluster_person"),
        ),
        migrations.AddConstraint(
            model_name="personmergeredirect",
            constraint=models.CheckConstraint(condition=~models.Q(source_person=models.F("target_person")), name="members_merge_redirect_source_ne_target"),
        ),
    ]
