from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("school", "0015_family_extensions"),
    ]

    operations = [
        migrations.CreateModel(
            name="AthleteContract",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("number", models.CharField(blank=True, max_length=50, verbose_name="Номер договора")),
                ("issue_date", models.DateField(verbose_name="Дата оформления")),
                ("start_date", models.DateField(verbose_name="Дата начала")),
                ("end_date", models.DateField(verbose_name="Дата окончания")),
                ("base_fee", models.DecimalField(decimal_places=2, default=Decimal("12000.00"), max_digits=9, verbose_name="Базовый платёж")),
                ("discount_value", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=7, verbose_name="Скидка")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("profile", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="contracts", to="school.familyathleteprofile", verbose_name="Профиль спортсмена")),
            ],
            options={
                "verbose_name": "Договор",
                "verbose_name_plural": "Договоры",
                "ordering": ["-start_date"],
            },
        ),
        migrations.AddField(
            model_name="familyservice",
            name="contract",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="services", to="school.athletecontract"),
        ),
    ]
