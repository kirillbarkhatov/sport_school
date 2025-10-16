from decimal import Decimal

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("school", "0014_person_photo"),
    ]

    operations = [
        migrations.AddField(
            model_name="family",
            name="status",
            field=models.CharField(
                choices=[("active", "Действующий член клуба"), ("alumni", "Бывший член клуба")],
                default="active",
                max_length=20,
                verbose_name="Статус семьи",
            ),
        ),
        migrations.AddField(
            model_name="family",
            name="base_monthly_fee",
            field=models.DecimalField(
                max_digits=9,
                decimal_places=2,
                default=Decimal("0.00"),
                verbose_name="Базовая стоимость месяца",
                help_text="Используется как значение по умолчанию для спортсменов семьи",
            ),
        ),
        migrations.AddField(
            model_name="family",
            name="discount_type",
            field=models.CharField(
                choices=[
                    ("none", "Без скидки"),
                    ("second_child", "Второй ребёнок"),
                    ("third_child", "Третий ребёнок"),
                    ("personal", "Персональная скидка"),
                    ("achievement", "За достижения"),
                    ("prepayment", "Оплата абонемента вперёд"),
                ],
                default="none",
                max_length=20,
                verbose_name="Тип скидки семьи",
            ),
        ),
        migrations.AddField(
            model_name="family",
            name="discount_value",
            field=models.DecimalField(
                max_digits=7,
                decimal_places=2,
                default=Decimal("0.00"),
                verbose_name="Размер скидки семьи",
            ),
        ),
        migrations.AddField(
            model_name="family",
            name="current_month_paid",
            field=models.BooleanField(default=False, verbose_name="Оплата текущего месяца получена"),
        ),
        migrations.AlterModelOptions(
            name="family",
            options={"ordering": ["family_name"], "verbose_name": "Семья", "verbose_name_plural": "Семьи"},
        ),
        migrations.CreateModel(
            name="FamilyAthleteProfile",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("contract_active", models.BooleanField(default=True, verbose_name="Договор активен")),
                ("monthly_fee", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=9, verbose_name="Оплата в месяц")),
                ("discount_type", models.CharField(
                    choices=[
                        ("none", "Без скидки"),
                        ("second_child", "Второй ребёнок"),
                        ("third_child", "Третий ребёнок"),
                        ("personal", "Персональная скидка"),
                        ("achievement", "За достижения"),
                        ("prepayment", "Оплата абонемента вперёд"),
                    ],
                    default="none",
                    max_length=20,
                    verbose_name="Тип скидки",
                )),
                ("discount_value", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=7, verbose_name="Размер скидки")),
                ("current_month_paid", models.BooleanField(default=False, verbose_name="Оплата текущего месяца")),
                ("notes", models.TextField(blank=True, null=True, verbose_name="Комментарии")),
                ("athlete", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="family_profiles", to="school.athlete")),
                ("family", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="athlete_profiles", to="school.family")),
            ],
            options={
                "verbose_name": "Настройки спортсмена семьи",
                "verbose_name_plural": "Настройки спортсменов семьи",
                "ordering": ["athlete__person__surname"],
            },
        ),
        migrations.CreateModel(
            name="FamilyService",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150, verbose_name="Название услуги")),
                ("service_type", models.CharField(
                    choices=[
                        ("monthly", "Ежемесячный платеж"),
                        ("lodge", "Сервисный домик"),
                        ("skipass_snow", "Скипасс «Снежный»"),
                        ("skipass_yukki", "Скипасс «Юкки»"),
                        ("ski_preparation", "Подготовка лыж"),
                        ("federation", "Взнос в федерацию"),
                        ("camp", "Сборы"),
                        ("individual_training", "Индивидуальная тренировка"),
                        ("extra_payment", "Дополнительная услуга"),
                        ("other", "Прочее"),
                    ],
                    default="monthly",
                    max_length=30,
                    verbose_name="Тип услуги",
                )),
                ("amount", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=9, verbose_name="Сумма к оплате")),
                ("discount_type", models.CharField(
                    choices=[
                        ("none", "Без скидки"),
                        ("second_child", "Второй ребёнок"),
                        ("third_child", "Третий ребёнок"),
                        ("personal", "Персональная скидка"),
                        ("achievement", "За достижения"),
                        ("prepayment", "Оплата абонемента вперёд"),
                    ],
                    default="none",
                    max_length=20,
                    verbose_name="Тип скидки",
                )),
                ("discount_value", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=7, verbose_name="Размер скидки")),
                ("is_recurring", models.BooleanField(default=False, verbose_name="Повторяющаяся")),
                ("is_closed", models.BooleanField(default=False, verbose_name="Закрыта")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("due_date", models.DateField(blank=True, null=True, verbose_name="Дата оплаты")),
                ("notes", models.TextField(blank=True, null=True, verbose_name="Комментарии")),
                ("family", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="services", to="school.family")),
                ("profile", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="services", to="school.familyathleteprofile")),
            ],
            options={"ordering": ["-created_at"], "verbose_name": "Услуга семьи", "verbose_name_plural": "Услуги семьи"},
        ),
        migrations.CreateModel(
            name="FamilyPayment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("amount", models.DecimalField(decimal_places=2, default=Decimal("0.00"), max_digits=9, verbose_name="Сумма платежа")),
                ("payment_type", models.CharField(
                    choices=[
                        ("prepayment", "Предоплата"),
                        ("additional", "Доплата"),
                        ("full", "Полная оплата"),
                    ],
                    default="full",
                    max_length=20,
                    verbose_name="Тип платежа",
                )),
                ("paid_at", models.DateField(auto_now_add=True, verbose_name="Дата оплаты")),
                ("note", models.TextField(blank=True, null=True, verbose_name="Комментарий")),
                ("service", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="payments", to="school.familyservice")),
            ],
            options={"ordering": ["-paid_at"], "verbose_name": "Оплата", "verbose_name_plural": "Оплаты"},
        ),
        migrations.AlterUniqueTogether(
            name="familyathleteprofile",
            unique_together={("family", "athlete")},
        ),
    ]
