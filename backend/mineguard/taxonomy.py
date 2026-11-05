"""Detector label -> mg.cls.v0 mapping (MG-DATA-001 section 7). COCO has no excavator or haul-truck class."""
from rfdetr.assets.coco_classes import COCO_CLASSES

from .canon import digest

TAXONOMY = "mg.cls.v0"
PERSON_IDS = {1}
VEHICLE_IDS = {3, 4, 6, 8}  # car, motorcycle, bus, truck (COCO category ids)


def coco_label(class_id: int) -> str:
    return COCO_CLASSES[int(class_id)]


def map_class(class_id: int) -> str:
    if class_id in PERSON_IDS:
        return "person"
    if class_id in VEHICLE_IDS:
        return "vehicle"
    return "other"


def label_map() -> dict[str, str]:
    return {str(i): map_class(i) for i in sorted(COCO_CLASSES)}


def label_map_digest() -> str:
    return digest({"taxonomy": TAXONOMY, "map": label_map(), "names": {str(i): n for i, n in sorted(COCO_CLASSES.items())}})
