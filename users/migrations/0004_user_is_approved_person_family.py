from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("school", "0014_person_photo"),
        ("users", "0003_user_token"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="family",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="users",
                to="school.family",
                verbose_name="Семья",
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="is_approved",
            field=models.BooleanField(default=False, verbose_name="Пользователь подтверждён"),
        ),
        migrations.AddField(
            model_name="user",
            name="person",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="linked_users",
                to="school.person",
                verbose_name="Персона",
            ),
        ),
    ]
