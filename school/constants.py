from .choices import TrainingEquipment, TrainingKind, TrainingLocation


DEFAULT_TRAINING_TYPES = [choice.label for choice in TrainingKind]
DEFAULT_EQUIPMENT = [choice.label for choice in TrainingEquipment]
DEFAULT_LOCATIONS = [choice.label for choice in TrainingLocation]
