from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="WhatsAppChat",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255, unique=True, verbose_name="Название группы")),
                ("last_synced_at", models.DateTimeField(blank=True, null=True, verbose_name="Последняя синхронизация")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("updated_at", models.DateTimeField(auto_now=True, verbose_name="Обновлено")),
            ],
            options={
                "verbose_name": "Группа WhatsApp",
                "verbose_name_plural": "Группы WhatsApp",
                "ordering": ("-last_synced_at", "-updated_at"),
            },
        ),
        migrations.CreateModel(
            name="WhatsAppChatMember",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("member_id", models.CharField(max_length=255, verbose_name="Идентификатор участника")),
                ("phone", models.CharField(blank=True, max_length=64, verbose_name="Телефон")),
                ("push_name", models.CharField(blank=True, max_length=255, verbose_name="Отображаемое имя")),
                ("is_admin", models.BooleanField(default=False, verbose_name="Администратор")),
                ("is_super_admin", models.BooleanField(default=False, verbose_name="Главный администратор")),
                ("is_active", models.BooleanField(default=True, verbose_name="Активный участник")),
                ("first_seen_at", models.DateTimeField(auto_now_add=True, verbose_name="Впервые замечен")),
                ("last_seen_at", models.DateTimeField(blank=True, null=True, verbose_name="Последний раз замечен")),
                ("left_at", models.DateTimeField(blank=True, null=True, verbose_name="Дата выхода")),
                (
                    "chat",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="members",
                        to="whatsapp.whatsappchat",
                        verbose_name="Группа",
                    ),
                ),
            ],
            options={
                "verbose_name": "Участник группы WhatsApp",
                "verbose_name_plural": "Участники групп WhatsApp",
                "ordering": ("chat", "-last_seen_at", "-first_seen_at"),
            },
        ),
        migrations.AddIndex(
            model_name="whatsappchatmember",
            index=models.Index(fields=["chat", "member_id"], name="wa_mem_chat_member_id_idx"),
        ),
        migrations.AddIndex(
            model_name="whatsappchatmember",
            index=models.Index(fields=["chat", "phone"], name="wa_mem_chat_phone_idx"),
        ),
        migrations.AddIndex(
            model_name="whatsappchatmember",
            index=models.Index(fields=["is_active"], name="wa_mem_active_idx"),
        ),
        migrations.AlterUniqueTogether(
            name="whatsappchatmember",
            unique_together={("chat", "member_id")},
        ),
    ]
