"""ObservationSet schema `mg.obsset.vs01`: a VS-01 extension of the PROPOSED mg.obsset.0.2 (adds pixel_sha256 per frame,
detector_label/class_id per object, producer.params, native dimensions). Validation codes follow MG-DATA-001 section 14."""
from __future__ import annotations

import re
import unicodedata
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, ValidationError

from .canon import canonical_bytes, digest

SCHEMA_ID = "mg.obsset.vs01"
LIVE_SCHEMA_ID = "mg.obsset.live.vs02"  # webcam sessions: same objects and rules, but frames may be dropped/stale/gapped (VS-02B)
FORBIDDEN_FIELD_PREFIXES = ("distance", "speed", "ttc", "collision")
FORBIDDEN_FIELD_SUFFIXES = ("_m", "_km", "_kmh", "_mps")
Label = Literal["person", "vehicle", "other"]
Status = Literal["not_analyzed_stride", "not_analyzed_dropped", "source_gap", "adapter_error"]


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


PosInt = Annotated[StrictInt, Field(ge=1)]
NonNegInt = Annotated[StrictInt, Field(ge=0)]
ObjKey = Annotated[StrictStr, Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")]


class Bbox(_M):
    x: StrictInt
    y: StrictInt
    w: StrictInt
    h: StrictInt


class Truncated(_M):
    left: StrictBool = False
    right: StrictBool = False
    top: StrictBool = False
    bottom: StrictBool = False


class Cls(_M):
    label: Label
    state: Literal["known"]
    detector_label: StrictStr
    detector_class_id: StrictInt


class Confidence(_M):
    permille: Optional[StrictInt]
    kind: Literal["not_applicable", "human_asserted", "model_uncalibrated", "model_calibrated"]


class Obj(_M):
    obj_key: ObjKey
    cls: Cls
    bbox: Bbox
    truncated: Truncated
    confidence: Confidence
    track_ref: None = None


class Frame(_M):
    frame_index: NonNegInt
    status: Literal["analyzed"]
    pixel_sha256: StrictStr
    objects: list[Obj]


class StatusRange(_M):
    from_frame: StrictInt
    to_frame: StrictInt
    status: Status


class Producer(_M):
    adapter_id: StrictStr
    adapter_version: StrictStr
    model_id: Optional[StrictStr]
    weights_sha256: Optional[StrictStr]
    params_hash: StrictStr
    label_map_digest: Optional[StrictStr]
    params: dict[str, StrictInt | StrictStr | StrictBool]


class SourceBinding(_M):
    source_digest: StrictStr
    frame_table_digest: StrictStr
    frame_count: PosInt
    width: PosInt
    height: PosInt
    native_width: PosInt
    native_height: PosInt


class Meta(_M):
    """Unhashed metadata. Strict (MG-DATA-001 section 5 lists creation time, tool, notes, provenance_id); VS-01 adds two counters."""
    created_utc: Optional[StrictStr] = None
    tool: Optional[StrictStr] = None
    notes: Optional[StrictStr] = None
    provenance_id: Optional[StrictStr] = None
    elapsed_ms: Optional[NonNegInt] = None
    dropped_degenerate_boxes: Optional[NonNegInt] = None


class ObservationSet(_M):
    schema_: Literal["mg.obsset.vs01", "mg.obsset.live.vs02"] = Field(alias="schema")
    source_binding: SourceBinding
    coord_space: Literal["image_px_v1"]
    taxonomy: Literal["mg.cls.v0"]
    origin: Literal["synthetic_scripted", "human_annotated", "model_inference"]
    producer: Producer
    tracks: list[dict] = Field(default=[], max_length=0)  # VS-01 has no tracker: V-08 track semantics are deferred
    frames: list[Frame]
    frame_status_ranges: list[StatusRange]
    meta: Meta = Meta()


class ValidationFailure(Exception):
    def __init__(self, codes: list[str]):
        super().__init__(", ".join(codes))
        self.codes = codes


def json_schema() -> dict:
    return ObservationSet.model_json_schema(by_alias=True)


def hashed_body(obs: dict) -> dict:
    return {k: v for k, v in obs.items() if k != "meta"}


def obsset_digest(obs: dict) -> str:
    return digest(hashed_body(obs))


def _forbidden_name(key) -> bool:
    low = unicodedata.normalize("NFKC", str(key)).casefold().strip()
    return low.startswith(FORBIDDEN_FIELD_PREFIXES) or low.endswith(FORBIDDEN_FIELD_SUFFIXES)


def _walk_keys(value, found: list[str]):
    """V-11 scan over every key at every depth of the WHOLE document (hashed body and meta alike)."""
    if isinstance(value, dict):
        for k, v in value.items():
            if _forbidden_name(k):
                found.append(str(k))
            _walk_keys(v, found)
    elif isinstance(value, list):
        for v in value:
            _walk_keys(v, found)


def validate_observation_set(obs: dict, expected_binding: dict | None = None, live: bool = False) -> ObservationSet:
    codes: list[str] = []
    keys: list[str] = []
    _walk_keys(obs, keys)  # before structural validation, so a forbidden field is reported even inside an otherwise malformed set
    try:
        model = ObservationSet.model_validate(obs)
    except ValidationError:
        raise ValidationFailure(sorted({"V-20", *(["V-11"] if keys else [])}))
    if (model.schema_ == LIVE_SCHEMA_ID) != live:
        raise ValidationFailure(["V-20"])  # a live (webcam) set is never validated as a file set, nor the reverse
    try:
        canonical_bytes(obs)  # MG-DATA-001-006: the WHOLE document (hashed body AND unhashed meta): every string and member name must be NFC
    except ValueError:
        codes.append("V-17")
    if keys:
        codes.append("V-11")
    b = model.source_binding
    if expected_binding is not None:
        if b.source_digest != expected_binding["source_digest"]:
            codes.append("V-01")
        if (b.width, b.height, b.frame_count) != (expected_binding["width"], expected_binding["height"], expected_binding["frame_count"]):
            codes.append("V-02")
        if b.frame_table_digest != expected_binding["frame_table_digest"]:
            codes.append("V-03")
    covered = [False] * max(b.frame_count, 0)
    overlap = False
    order = [f.frame_index for f in model.frames]
    if any(later <= earlier for earlier, later in zip(order, order[1:])):
        codes.append("V-26")  # MG-DATA-001-005: frames must be strictly increasing by frame_index (never silently sorted, never duplicated)
    for f in model.frames:
        if not 0 <= f.frame_index < b.frame_count or covered[f.frame_index]:
            overlap = True
        else:
            covered[f.frame_index] = True
        keys_seen = set()
        for o in f.objects:
            bb, t = o.bbox, o.truncated
            if bb.w < 1 or bb.h < 1 or bb.x < 0 or bb.y < 0 or bb.x + bb.w > b.width or bb.y + bb.h > b.height:
                codes.append("V-06")
            if (t.left and bb.x != 0) or (t.right and bb.x + bb.w != b.width) or (t.top and bb.y != 0) or (t.bottom and bb.y + bb.h != b.height):
                codes.append("V-07")
            if o.obj_key in keys_seen:
                codes.append("V-08")
            keys_seen.add(o.obj_key)
            c = o.confidence
            if model.origin == "synthetic_scripted" and (c.permille is not None or c.kind != "not_applicable"):
                codes.append("V-10")
            if c.permille is not None and not 0 <= c.permille <= 1000:
                codes.append("V-10")
            if model.origin == "model_inference" and c.permille is None:
                codes.append("V-10")
    for r in model.frame_status_ranges:
        for i in range(max(r.from_frame, 0), min(r.to_frame, b.frame_count - 1) + 1):
            if covered[i]:
                overlap = True
            covered[i] = True
        if r.from_frame > r.to_frame or r.from_frame < 0 or r.to_frame >= b.frame_count:
            overlap = True
    if overlap or not all(covered):
        codes.append("V-05")
    p = model.producer
    if model.origin == "model_inference":
        if not p.model_id or not p.weights_sha256 or p.adapter_id == "replay":
            codes.append("V-09")
    elif p.adapter_id != "replay":
        codes.append("V-09")
    codes += _hash_format_codes(model)
    if model.origin == "model_inference":
        codes += _inference_binding_codes(model, live)
    if codes:
        raise ValidationFailure(sorted(set(codes)))
    return model


HEX64 = re.compile(r"[0-9a-f]{64}")


def _hash_format_codes(model: ObservationSet) -> list[str]:
    """V-22 (VS-01 extension): every digest field is 64 lowercase hex characters."""
    b, p = model.source_binding, model.producer
    values = [b.source_digest, b.frame_table_digest, p.params_hash, *(f.pixel_sha256 for f in model.frames)]
    values += [v for v in (p.weights_sha256, p.label_map_digest) if v is not None]
    return ["V-22"] if not all(HEX64.fullmatch(v) for v in values) else []


def _inference_binding_codes(model: ObservationSet, live: bool = False) -> list[str]:
    """V-09 (approved adapter/model/weights tuple), V-21 (params_hash), V-23 (taxonomy/label map) for model_inference sets."""
    from . import taxonomy
    from .detector import ADAPTER_ID, load_manifest_entry

    codes, p = [], model.producer
    codes += _live_coverage_codes(model) if live else _coverage_codes(model)
    entry = load_manifest_entry()
    if (p.adapter_id, p.model_id, p.weights_sha256) != (ADAPTER_ID, entry["model_id"], entry["sha256"]):
        codes.append("V-09")
    if p.params_hash != digest(p.params):
        codes.append("V-21")
    if p.label_map_digest != taxonomy.label_map_digest():
        codes.append("V-23")
    for f in model.frames:
        for o in f.objects:
            cid = o.cls.detector_class_id
            if cid not in taxonomy.COCO_CLASSES or o.cls.detector_label != taxonomy.coco_label(cid) or o.cls.label != taxonomy.map_class(cid):
                codes.append("V-23")
            if o.confidence.kind != "model_uncalibrated":
                codes.append("V-10")
    return codes


def _live_coverage_codes(model: ObservationSet) -> list[str]:
    """V-27 (VS-02B): a live (webcam) inference set has at least one analyzed frame; frames are strictly increasing; every other frame carries an explicit
    non-analyzed status (dropped / source_gap / adapter_error). Stride semantics do not exist for a live source (a stride-based status is refused)."""
    if not model.frames or any(r.status == "not_analyzed_stride" for r in model.frame_status_ranges):
        return ["V-27"]
    if model.producer.params.get("mode") != "webcam":
        return ["V-27"]
    return []


def _coverage_codes(model: ObservationSet) -> list[str]:
    """V-24 (VS-01 extension, MG-RC1-005): a model_inference set must contain at least one analyzed frame, and its analyzed
    frames must be exactly the frames the declared stride selects, IN INCREASING ORDER (every other frame carries status not_analyzed_stride)."""
    stride = model.producer.params.get("stride")
    if not isinstance(stride, int) or isinstance(stride, bool) or stride < 1:
        return ["V-24"]
    n = model.source_binding.frame_count
    if not model.frames or [f.frame_index for f in model.frames] != list(range(0, n, stride)):
        return ["V-24"]
    return ["V-24"] if any(r.status != "not_analyzed_stride" for r in model.frame_status_ranges) else []
