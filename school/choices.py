from django.db import models


class TrainingKind(models.TextChoices):
    FITNESS = ("fitness", "ОФП")
    ROLLERS = ("rollers", "Ролики")
    ICE = ("ice", "Коньки")
    SKI = ("ski", "Лыжи")
    SKI_BEGINNERS = ("ski_beginners", "Лыжи (начинающие)")
    SKI_DRILLS = ("ski_drills", "Упражнения (лыжи)")
    SLALOM = ("slalom", "Слалом")
    GIANT_SLALOM = ("giant_slalom", "Гигантский слалом")
    BIKE = ("bike", "Велотренировка")
    SKITECH = ("skitech", "Тренажер SkiTech")
    TRAMPOLINE = ("trampoline", "Батут")
    MANEZH = ("manezh", "Манеж")
    OTHER = ("other", "Уточняется")


class TrainingLocation(models.TextChoices):
    MURINSKY_PARK = ("murinsky_park", "Муринский парк")
    UTC_KAVGOLOVO = ("utc_kavgolovo", "УТЦ Кавголово")
    YUKKI = ("yukki", "Юкки")
    SNEZHNY = ("snezny", "Снежный")
    SERVERNY_SLOPE = ("serverny_slope", "Серверный склон")
    OKHTA_PARK = ("okhta_park", "Охта парк")
    PARK_HOUSE = ("park_house", "Бугры (ТЦ Парк хаус)")
    SEVER_PARK = ("sever_park", "Север парк арена (Руставели 38)")
    OTHER = ("other", "Локация уточняется")


class TrainingEquipment(models.TextChoices):
    ATHLETIC = ("athletic", "Спортивная одежда, кроссовки")
    ROLLERS = ("rollers", "Ролики, защита")
    SKATES = ("skates", "Коньки, защита")
    ICE = ("ice", "Коньки")
    BIKE = ("bike", "Велосипед, защита")
    SLALOM_SKI = ("slalom_ski", "Слаломные лыжи, защита")
    GS_SKI = ("gs_ski", "Лыжи для гигантского слалома, защита")
    OTHER = ("other", "Уточняется")


class ClassCreationSource(models.TextChoices):
    MANUAL = ("manual", "Создано вручную")
    TEMPLATE = ("template", "Создано автоматически по шаблону")


class ClassCoachStatus(models.TextChoices):
    PENDING = ("pending", "Ожидает подтверждения")
    PLANNED = ("planned", "Запланировано")
    CANCELLED = ("cancelled", "Отменено")
