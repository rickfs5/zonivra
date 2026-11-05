"""Zone Evaluation: observations (+ tracker ids) + one zone -> kernel Samples. Knows labels and geometry, not the detector or the risk rules."""
from __future__ import annotations

from . import geometry as g
from .kernel import F, T, U, Sample, k_or

ZONE_KINDS = ("restricted", "monitored")


def zone_digest(zone: dict) -> str:
    from ..canon import digest
    return digest({k: zone[k] for k in ("zone_id", "kind", "width", "height", "polygon", "coord_space")})


def make_zone(zone_id: str, kind: str, polygon, width: int, height: int, name: str | None = None) -> dict:
    """Validated, canonical zone (raises InvalidZone). The digest covers the geometry only, not the display name."""
    import re
    if not isinstance(zone_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", zone_id):
        raise g.InvalidZone("mg.refusal.zone_id")
    if kind not in ZONE_KINDS:
        raise g.InvalidZone("mg.refusal.zone_kind")
    poly = g.validate_polygon(polygon, width, height)
    if name is not None:   # the name is part of the canonical evaluation input (CJ-1 NFC): a non-NFC or unencodable name would be stored and then break every risk view (cycle-19 MG-ZONE-020)
        import unicodedata
        try:
            nfc = isinstance(name, str) and unicodedata.is_normalized("NFC", name)
            name.encode("utf-8")
        except (UnicodeEncodeError, AttributeError):
            nfc = False
        if not nfc:
            raise g.InvalidZone("mg.refusal.zone_name_not_nfc")
    z = {"zone_id": zone_id, "kind": kind, "coord_space": "image_px_v1", "width": width, "height": height, "polygon": poly,
         "name": (name or zone_id)[:60]}
    z["digest"] = zone_digest(z)
    return z


def sample_for(zone: dict, index: int, t_us: int, status: str, objects: list[dict], track_ids: list, min_conf_permille: int | None = None,
               below_threshold: str = "unknown", identity_breaks=()) -> Sample:
    """objects: observation objects (cls.label, bbox, truncated, confidence.permille); track_ids: parallel list of confirmed track id or None; identity_breaks: ids of tracks whose continuity the
    tracker ended by ambiguity at this sample (the kernel records a track discontinuity for those it knows)."""
    if status != "analyzed":
        return Sample(t_us=t_us, index=index, status={"not_analyzed_dropped": "not_analyzed", "not_analyzed_stride": "not_analyzed",
                                                      "source_gap": "source_gap", "adapter_error": "adapter_error"}.get(status, "not_analyzed"))
    persons: dict[str, tuple] = {}
    untracked, vehicle = (F, None), (F, None)
    notes: list[str] = []
    for o, tid in zip(objects, track_ids):
        label = o["cls"]["label"]
        if label not in ("person", "vehicle"):
            continue
        v, cause = g.occupancy(zone["polygon"], o["bbox"], o["truncated"], zone["width"])
        conf = o["confidence"]["permille"]
        if min_conf_permille is not None and conf is not None and conf < min_conf_permille and v != F:
            if below_threshold == "ignore":
                continue
            v, cause = U, "below_confidence_threshold"
        if label == "vehicle":
            nv = k_or(vehicle[0], v)
            vehicle = (nv, (vehicle[1] or (cause if v == U else None)) if nv == U else None)
            if v == U:
                notes.append(cause)
        elif tid is None:
            untracked = (k_or(untracked[0], v), cause if v == U else untracked[1])
        else:
            old = persons.get(tid)
            persons[tid] = (k_or(old[0], v) if old else v, cause if v == U else (old[1] if old else None))
            if v == U:
                notes.append(cause)
    return Sample(t_us=t_us, index=index, status="analyzed", persons=persons, untracked_person=untracked, vehicle=vehicle, notes=tuple(notes), identity_breaks=frozenset(identity_breaks))
