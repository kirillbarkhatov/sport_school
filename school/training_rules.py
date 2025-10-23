from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .choices import TrainingEquipment, TrainingKind, TrainingLocation


TRAINING_ORDER: List[str] = [value for value, _ in TrainingKind.choices]
LOCATION_ORDER: List[str] = [value for value, _ in TrainingLocation.choices]
EQUIPMENT_ORDER: List[str] = [value for value, _ in TrainingEquipment.choices]

SKI_TRAININGS: Set[str] = {
    TrainingKind.SKI,
    TrainingKind.SKI_BEGINNERS,
    TrainingKind.SKI_DRILLS,
    TrainingKind.SLALOM,
    TrainingKind.GIANT_SLALOM,
}

SKI_EQUIPMENT: Set[str] = {
    TrainingEquipment.SLALOM_SKI,
    TrainingEquipment.GS_SKI,
}


LOCATION_RULES: Dict[str, Dict[str, Set[str]]] = {
    TrainingLocation.MURINSKY_PARK: {
        TrainingKind.FITNESS: {TrainingEquipment.ATHLETIC},
        TrainingKind.ROLLERS: {TrainingEquipment.ROLLERS},
        TrainingKind.BIKE: {TrainingEquipment.BIKE},
    },
    TrainingLocation.UTC_KAVGOLOVO: {
        TrainingKind.FITNESS: {TrainingEquipment.ATHLETIC},
        TrainingKind.ROLLERS: {TrainingEquipment.ROLLERS},
        TrainingKind.BIKE: {TrainingEquipment.BIKE},
    },
    TrainingLocation.YUKKI: {
        TrainingKind.FITNESS: {TrainingEquipment.ATHLETIC},
        TrainingKind.TRAMPOLINE: {TrainingEquipment.ATHLETIC},
        TrainingKind.MANEZH: {TrainingEquipment.ATHLETIC},
        TrainingKind.SKI: SKI_EQUIPMENT,
        TrainingKind.SKI_BEGINNERS: SKI_EQUIPMENT,
        TrainingKind.SKI_DRILLS: SKI_EQUIPMENT,
        TrainingKind.SLALOM: SKI_EQUIPMENT,
        TrainingKind.GIANT_SLALOM: SKI_EQUIPMENT,
    },
    TrainingLocation.SNEZHNY: {
        training_kind: SKI_EQUIPMENT for training_kind in SKI_TRAININGS
    },
    TrainingLocation.SERVERNY_SLOPE: {
        training_kind: SKI_EQUIPMENT for training_kind in SKI_TRAININGS
    },
    TrainingLocation.OKHTA_PARK: {
        TrainingKind.FITNESS: {TrainingEquipment.ATHLETIC},
        TrainingKind.ROLLERS: {TrainingEquipment.ROLLERS},
        TrainingKind.SKI: SKI_EQUIPMENT,
        TrainingKind.SKI_BEGINNERS: SKI_EQUIPMENT,
        TrainingKind.SKI_DRILLS: SKI_EQUIPMENT,
        TrainingKind.SLALOM: SKI_EQUIPMENT,
        TrainingKind.GIANT_SLALOM: SKI_EQUIPMENT,
    },
    TrainingLocation.PARK_HOUSE: {
        TrainingKind.SKITECH: {TrainingEquipment.ATHLETIC},
    },
    TrainingLocation.SEVER_PARK: {
        TrainingKind.ICE: {TrainingEquipment.SKATES, TrainingEquipment.ICE},
    },
}


DEFAULT_EQUIPMENT_BY_TRAINING: Dict[str, List[str]] = {
    TrainingKind.FITNESS: [TrainingEquipment.ATHLETIC],
    TrainingKind.ROLLERS: [TrainingEquipment.ROLLERS],
    TrainingKind.BIKE: [TrainingEquipment.BIKE],
    TrainingKind.ICE: [TrainingEquipment.SKATES, TrainingEquipment.ICE],
    TrainingKind.SKI: list(SKI_EQUIPMENT),
    TrainingKind.SKI_BEGINNERS: list(SKI_EQUIPMENT),
    TrainingKind.SKI_DRILLS: list(SKI_EQUIPMENT),
    TrainingKind.SLALOM: [TrainingEquipment.SLALOM_SKI],
    TrainingKind.GIANT_SLALOM: [TrainingEquipment.GS_SKI],
    TrainingKind.TRAMPOLINE: [TrainingEquipment.ATHLETIC],
    TrainingKind.MANEZH: [TrainingEquipment.ATHLETIC],
    TrainingKind.SKITECH: [TrainingEquipment.ATHLETIC],
}


def _unique(values: Iterable[str]) -> List[str]:
    seen: Set[str] = set()
    result: List[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _order_values(options: Iterable[str], order: Sequence[str]) -> List[str]:
    ordered_options = []
    order_index = {value: index for index, value in enumerate(order)}
    for value in options:
        ordered_options.append((order_index.get(value, len(order)), value))
    ordered_options.sort()
    return [value for _, value in ordered_options]


def _choose_preferred(options: Iterable[str], current: Optional[str], order: Sequence[str]) -> Optional[str]:
    options_list = _order_values(options, order)
    if not options_list:
        return current
    if current and current in set(options_list):
        return current
    return options_list[0]


def get_allowed_training_for_location(location: Optional[str]) -> Optional[Set[str]]:
    if not location:
        return None
    rules = LOCATION_RULES.get(location)
    if not rules:
        return None
    return set(rules.keys())


def get_allowed_equipment(location: Optional[str], training_type: Optional[str]) -> Optional[Set[str]]:
    if not location or not training_type:
        return None
    rules = LOCATION_RULES.get(location)
    if not rules:
        return None
    equipment = rules.get(training_type)
    if equipment is None:
        return set()
    return set(equipment)


def get_default_equipment_for_training(training_type: Optional[str]) -> List[str]:
    if not training_type:
        return []
    defaults = DEFAULT_EQUIPMENT_BY_TRAINING.get(training_type)
    if defaults:
        return list(defaults)
    return [TrainingEquipment.OTHER]


def _build_training_locations_map() -> Dict[str, Set[str]]:
    mapping: Dict[str, Set[str]] = {}
    for location, rules in LOCATION_RULES.items():
        for training_type in rules.keys():
            mapping.setdefault(training_type, set()).add(location)
    return mapping


TRAINING_LOCATIONS: Dict[str, Set[str]] = _build_training_locations_map()


def get_locations_for_training(training_type: Optional[str]) -> Optional[Set[str]]:
    if not training_type:
        return None
    locations = TRAINING_LOCATIONS.get(training_type)
    if not locations:
        return None
    return set(locations)


def _build_equipment_maps() -> Tuple[Dict[str, Set[str]], Dict[str, Set[str]]]:
    training_map: Dict[str, Set[str]] = {}
    location_map: Dict[str, Set[str]] = {}
    for location, rules in LOCATION_RULES.items():
        for training_type, equipment_values in rules.items():
            for equipment in equipment_values:
                training_map.setdefault(equipment, set()).add(training_type)
                location_map.setdefault(equipment, set()).add(location)
    return training_map, location_map


EQUIPMENT_TRAINING_MAP, EQUIPMENT_LOCATION_MAP = _build_equipment_maps()


def infer_from_equipment(equipment_values: Iterable[str]) -> Tuple[Optional[str], Set[str]]:
    equipment_list = _unique(equipment_values)
    if not equipment_list:
        return None, set()

    training_candidates: Optional[Set[str]] = None
    location_candidates: Optional[Set[str]] = None

    for value in equipment_list:
        possible_trainings = EQUIPMENT_TRAINING_MAP.get(value)
        if possible_trainings:
            if training_candidates is None:
                training_candidates = set(possible_trainings)
            else:
                training_candidates &= possible_trainings

        possible_locations = EQUIPMENT_LOCATION_MAP.get(value)
        if possible_locations:
            if location_candidates is None:
                location_candidates = set(possible_locations)
            else:
                location_candidates &= possible_locations

    inferred_training = None
    if training_candidates and len(training_candidates) == 1:
        inferred_training = next(iter(training_candidates))

    locations_result = location_candidates if location_candidates else set()
    return inferred_training, locations_result


def normalize_training_selection(
    location: Optional[str],
    training_type: Optional[str],
    equipment_values: Iterable[str],
) -> Tuple[Optional[str], Optional[str], List[str]]:
    location_value = location or None
    training_value = training_type or None
    equipment_list = _unique(equipment_values)

    inferred_training, inferred_locations = infer_from_equipment(equipment_list)
    if inferred_training:
        training_value = inferred_training
    if inferred_locations and location_value not in inferred_locations and len(inferred_locations) == 1:
        location_value = next(iter(inferred_locations))

    allowed_training = get_allowed_training_for_location(location_value)
    if allowed_training is not None:
        training_value = _choose_preferred(allowed_training, training_value, TRAINING_ORDER)

    training_locations = get_locations_for_training(training_value)
    if training_locations:
        location_value = _choose_preferred(training_locations, location_value, LOCATION_ORDER)

    allowed_training = get_allowed_training_for_location(location_value)
    if allowed_training is not None:
        training_value = _choose_preferred(allowed_training, training_value, TRAINING_ORDER)

    allowed_equipment = get_allowed_equipment(location_value, training_value)
    if allowed_equipment is None:
        if equipment_list:
            normalized_equipment = equipment_list
        else:
            normalized_equipment = get_default_equipment_for_training(training_value)
    else:
        normalized_equipment = [value for value in equipment_list if value in allowed_equipment]
        if not normalized_equipment:
            defaults = get_default_equipment_for_training(training_value)
            normalized_equipment = [value for value in defaults if value in allowed_equipment]
        if not normalized_equipment and allowed_equipment:
            normalized_equipment = [_choose_preferred(allowed_equipment, None, EQUIPMENT_ORDER)]

    return location_value or location, training_value or training_type, normalized_equipment


def apply_training_rules(obj) -> bool:
    original_location = getattr(obj, "location", None)
    original_training = getattr(obj, "training_type", None)
    original_equipment = list(getattr(obj, "equipment", []) or [])

    location, training_type, equipment = normalize_training_selection(
        original_location,
        original_training,
        original_equipment,
    )

    changed = (
        location != original_location
        or training_type != original_training
        or original_equipment != equipment
    )

    if location is not None:
        obj.location = location
    if training_type is not None:
        obj.training_type = training_type
    obj.equipment = equipment

    return changed
