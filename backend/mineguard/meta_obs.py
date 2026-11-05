"""Observation edge projection of SAM 3.1 responses (EXPERIMENTAL; ported from the Meta Lab `sam31.py`, whose normalization had no review finding, and extended).
A provider response is normalized into the EXPERIMENTAL extension `mg.obsset.metalab.x1` (shape of every object identical to the canonical `Obj`); the canonical contracts
(`mg.obsset.vs01`, V-09, the risk kernel) are NOT changed and the canonical validator still refuses the extension. A response is NEVER attributed to the local RF-DETR adapter.
Normalization never invents a confidence, never reads a mask as a class, never clips an out-of-frame box, and turns every untrustworthy instance into an explicit UNKNOWN frame
(`adapter_error`): a lost person must never make a zone look empty. Origin: `simulated_provider_response` for fixtures; `provider_response_unverified` only when a local
transport receipt is supplied (it proves a governed call was recorded locally, not that Meta produced the text)."""
from __future__ import annotations

import math
import re
from typing import Any

from . import metahttp, sam31
from .canon import digest

from .rawjson import raw_digest

ADAPTER_ID = "mineguard-meta-sam31-edge"
ADAPTER_VERSION = "0.2.0"
EXT_SCHEMA = "mg.obsset.metalab.x1"
FIXTURE_SCHEMA = "metalab.sam31.fixture.v0"
ORIGIN = "simulated_provider_response"
ORIGIN_REAL = "provider_response_unverified"
PROVIDER_ID = "fixture_meta_sam31"
PROVIDER_ID_REAL = "meta_sam31"
MODEL_ID = "sam-3.1 (NOT CALLED: simulated fixture)"
MODEL_ID_REAL = "sam-3.1"
BOX_FORMATS = ("xyxy_norm", "xywh_px", "xyxy_px")
TIMESTAMP_TOLERANCE_US = 0  # a provider timestamp must equal the source timebase exactly; otherwise the frame is unknown

# concept (prompt) -> mg.cls.v0 label. Explicit and closed: anything else is an unsupported concept (label "other", ignored by the kernel).
CONCEPT_MAP = {"person": "person", "worker": "person", "vehicle": "vehicle", "truck": "vehicle", "haul truck": "vehicle", "car": "vehicle"}

FIELD_STATUS = {
    "image.width/height": "ASSUMED (needed to denormalize boxes; supplied by the lab fixture)",
    "frame_index": "ASSUMED",
    "timestamp_us": "ASSUMED, optional; the SOURCE timebase is authoritative and must match when supplied",
    "request_id": "ASSUMED, kept for provenance only",
    "latency_ms": "ASSUMED, optional",
    "usage": "ASSUMED, optional; absent usage is recorded as absent, never as zero",
    "instances[].prompt": "ASSUMED (the concept the instance answers)",
    "instances[].instance_id": "ASSUMED, optional; NOT trusted as a track identity (video tracking semantics unverified)",
    "instances[].box": "ASSUMED, one of xyxy_norm | xywh_px | xyxy_px",
    "instances[].score": "ASSUMED, optional; never fabricated when absent; stays kind=model_uncalibrated when present",
    "instances[].mask": "ASSUMED, optional metadata only (no pixels); carries no class semantics",
    "error": "ASSUMED, optional provider error object",
    "partial": "ASSUMED, optional flag meaning the provider declared an incomplete response",
}
LIVE_DISCOVERY_REQUIRED = ["endpoint and authentication scheme", "request schema (image/video input, concept prompts)", "response schema and coordinate convention",
                           "whether a score is returned", "mask encoding", "video tracking identity semantics", "usage/billing fields and pricing", "rate limits and quotas"]


class NormalizationRefused(ValueError):
    """The whole fixture cannot be evaluated (no trustworthy timebase or frame order). Never silently repaired."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _box_px(box: Any, w: int, h: int) -> tuple[tuple[int, int, int, int] | None, str | None]:
    """-> ((x, y, w, h) integer pixels, None) or (None, rejection code). Explicit conventions: origin top-left, x right, y down, half-up rounding of x0/y0/x1/y1."""
    if not isinstance(box, dict) or box.get("format") not in BOX_FORMATS or not isinstance(box.get("values"), list) or len(box["values"]) != 4:
        return None, "invalid_box"
    v = box["values"]
    if not all(_num(x) for x in v):
        return None, "invalid_box"
    fmt = box["format"]
    if fmt == "xyxy_norm":
        if not all(0.0 <= x <= 1.0 for x in v):
            return None, "box_out_of_frame"
        x0, y0, x1, y1 = (math.floor(v[0] * w + 0.5), math.floor(v[1] * h + 0.5), math.floor(v[2] * w + 0.5), math.floor(v[3] * h + 0.5))
    else:
        if fmt == "xywh_px":
            x0, y0, x1, y1 = v[0], v[1], v[0] + v[2], v[1] + v[3]
        else:
            x0, y0, x1, y1 = v
        if any(float(x) != math.floor(x) for x in (x0, y0, x1, y1)):
            return None, "invalid_box"   # pixel boxes are integral; a fractional pixel box is not guessed at
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
            return None, "box_out_of_frame"
    if x1 - x0 < 1 or y1 - y0 < 1:
        return None, "invalid_box"
    return (x0, y0, x1 - x0, y1 - y0), None


def _iou(a, b) -> float:
    ax1, ay1, bx1, by1 = a[0] + a[2], a[1] + a[3], b[0] + b[2], b[1] + b[3]
    iw, ih = min(ax1, bx1) - max(a[0], b[0]), min(ay1, by1) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    return inter / (a[2] * a[3] + b[2] * b[3] - inter)


MASK_BOX_MIN_IOU = 0.5


def _instance(inst: Any, w: int, h: int) -> tuple[dict | None, str | None, bool]:
    """-> (object-or-None, rejection code, concept_is_supported). Rejections of a SUPPORTED concept make the frame unknown."""
    if not isinstance(inst, dict) or not isinstance(inst.get("prompt"), str) or not inst["prompt"].strip():
        return None, "invalid_instance", True
    concept = inst["prompt"].strip().casefold()
    label = CONCEPT_MAP.get(concept)
    supported = label is not None
    box, code = _box_px(inst.get("box"), w, h)
    if code:
        return None, code, supported
    mask = inst.get("mask")
    if mask is not None:
        mb = mask.get("bbox_xywh_px") if isinstance(mask, dict) else None
        if mb is not None:
            if not (isinstance(mb, list) and len(mb) == 4 and all(_num(x) for x in mb)) or _iou(box, tuple(mb)) < MASK_BOX_MIN_IOU:
                return None, "mask_box_disagreement", supported
    score = inst.get("score")
    if score is not None and (not _num(score) or not 0 <= score <= 1):
        return None, "invalid_score", supported
    x, y, bw, bh = box
    conf = ({"permille": None, "kind": "not_applicable"} if score is None else {"permille": int(math.floor(score * 1000 + 0.5)), "kind": "model_uncalibrated"})
    obj = {"cls": {"label": label or "other", "state": "known", "detector_label": inst["prompt"].strip(), "detector_class_id": -1},
           "bbox": {"x": x, "y": y, "w": bw, "h": bh},
           "truncated": {"left": x == 0, "right": x + bw == w, "top": y == 0, "bottom": y + bh == h},
           "confidence": conf, "track_ref": None}
    return obj, None, supported


def normalize(fixture: dict, provider_id: str = PROVIDER_ID) -> dict:
    """Fixture response -> `mg.obsset.metalab.x1` (EXPERIMENTAL extension of the canonical observation set; origin simulated_provider_response).
    Raises NormalizationRefused for a missing timebase, a malformed envelope, or frames out of order. This public entry point can NEVER produce a real label: a fixture that does not
    declare `simulated: true` is refused, whatever it carries (a receipt, a redemption, a digest). Real labels exist only through `normalize_redeemed`, which derives the observations
    itself from the redeemed events."""
    if not isinstance(fixture, dict) or fixture.get("fixture_schema") != FIXTURE_SCHEMA or not isinstance(fixture.get("simulated"), bool):
        raise NormalizationRefused("not_a_simulated_fixture")
    if fixture["simulated"] is not True:
        raise NormalizationRefused("real_label_not_accepted_here")
    return _normalize_core(fixture, provider_id, real_view=None)


def normalize_redeemed(redemption, *, request: dict, events: list, concept: str, width: int, height: int, pts_us: list[int], source_label: str = "", model_version: str | None = None) -> dict:
    """The ONLY producer of a real-labelled observation set. The observations are derived HERE from `events`; the caller supplies no frames, objects, masks or identifiers. The redemption
    (a sealed, single-use proof that a production attestation was accepted for exactly this request and this response) is consumed only if the digest of THESE events and of THIS request
    body equal the ones it was bound to, and the concept equals the one inside the request. Provenance therefore flows request -> dispatch -> response -> observations. It says the bytes
    came over a verified TLS session to the provider hostname; the providers offer no signature, so it does not say the provider's model produced them."""
    if not isinstance(redemption, metahttp.Redemption) or redemption.provider != PROVIDER_ID_REAL or redemption.model != MODEL_ID_REAL:
        raise NormalizationRefused("real_label_requires_a_redemption_for_this_provider_and_model")
    try:
        text = request["body"]["input"][0]["content"][0]["text"]
        ok = request["provider"] == PROVIDER_ID_REAL and request["model"] == MODEL_ID_REAL and text == concept
    except (KeyError, IndexError, TypeError):
        ok = False
    if not ok or not isinstance(events, list):
        raise NormalizationRefused("request_does_not_match_the_redeemed_call")
    # EVERYTHING that can fail happens first; the single use of the redemption is the LAST step, so a failure (bad dimensions, timebase, concept...) never destroys the only authority.
    parsed = sam31.parse_stream(events, width, height, max_frames=len(pts_us) if isinstance(pts_us, list) else 1)
    env = from_wire(parsed, concept, width, height, pts_us, True, None, None, model_version, fixture_id=source_label)
    obs = _normalize_core(env, PROVIDER_ID_REAL, real_view=redemption.public)
    bad = metahttp.use_redemption(redemption, response_digest=raw_digest({"events": events}), request_digest=raw_digest(request["body"]))
    if bad:
        raise NormalizationRefused("real_label_refused:" + ",".join(bad))
    return obs


def _normalize_core(fixture: dict, provider_id: str, *, real_view) -> dict:
    real = real_view is not None
    if real:
        provider_id = PROVIDER_ID_REAL
    src = fixture.get("source")
    pts = src.get("pts_us") if isinstance(src, dict) else None
    if not isinstance(pts, list) or not pts or not all(isinstance(t, int) and not isinstance(t, bool) and t >= 0 for t in pts):
        raise NormalizationRefused("missing_timebase")
    if any(b <= a for a, b in zip(pts, pts[1:])):
        raise NormalizationRefused("source_timebase_not_increasing")
    w, h = src.get("width"), src.get("height")
    if not all(isinstance(x, int) and not isinstance(x, bool) and x > 0 for x in (w, h)):
        raise NormalizationRefused("invalid_image_dimensions")
    responses = fixture.get("frames")
    if not isinstance(responses, list):
        raise NormalizationRefused("invalid_envelope")
    order = [r.get("frame_index") if isinstance(r, dict) else None for r in responses]
    if any(not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < len(pts) for i in order) or any(b <= a for a, b in zip(order, order[1:])):
        raise NormalizationRefused("frames_out_of_order_or_out_of_range")   # never sorted, never deduplicated
    by_index = dict(zip(order, responses))
    frames, rejections, unsupported, provider_ids, usage_seen = [], [], [], {}, False
    for i, t in enumerate(pts):
        r = by_index.get(i)
        if r is None:
            frames.append({"frame_index": i, "t_us": t, "status": "adapter_error", "objects": [], "cause": "missing_response"})
            continue
        ts = r.get("timestamp_us")
        if ts is not None and (not isinstance(ts, int) or isinstance(ts, bool) or abs(ts - t) > TIMESTAMP_TOLERANCE_US):
            frames.append({"frame_index": i, "t_us": t, "status": "adapter_error", "objects": [], "cause": "timebase_mismatch"})
            rejections.append({"frame_index": i, "code": "timebase_mismatch"})
            continue
        usage_seen = usage_seen or r.get("usage") is not None
        partial = r.get("partial", False)
        if partial is None:
            partial = False
        if r.get("error") is not None or partial is not False:   # True, 1, "true", [] ... anything but an explicit False is untrusted
            frames.append({"frame_index": i, "t_us": t, "status": "adapter_error", "objects": [], "cause": "provider_error" if r.get("error") is not None else
                           ("partial_response" if partial is True else "invalid_response")})
            continue
        insts = r.get("instances")
        if not isinstance(insts, list):
            frames.append({"frame_index": i, "t_us": t, "status": "adapter_error", "objects": [], "cause": "invalid_response"})
            continue
        ids = [x.get("instance_id") for x in insts if isinstance(x, dict) and x.get("instance_id") is not None]
        dup = {x for x in ids if ids.count(x) > 1}
        objs, bad, pids = [], False, {}
        for n, inst in enumerate(insts):
            if isinstance(inst, dict) and inst.get("instance_id") in dup:
                rejections.append({"frame_index": i, "code": "duplicate_track_identity", "instance_id": inst.get("instance_id")})
                bad = True
                continue
            obj, code, supported = _instance(inst, w, h)
            if code:
                rejections.append({"frame_index": i, "code": code})
                bad = bad or supported
                continue
            if obj["cls"]["label"] == "other":
                unsupported.append({"frame_index": i, "concept": obj["cls"]["detector_label"]})
            obj["obj_key"] = f"o{len(objs)}"
            if inst.get("instance_id") is not None:
                pids[obj["obj_key"]] = str(inst["instance_id"])
            objs.append(obj)
        if bad:
            frames.append({"frame_index": i, "t_us": t, "status": "adapter_error", "objects": [], "cause": "untrusted_instance", "objects_discarded": len(objs)})
        else:
            frames.append({"frame_index": i, "t_us": t, "status": "analyzed", "objects": objs})
            if pids:
                provider_ids[i] = pids
    body = {"schema": EXT_SCHEMA, "origin": ORIGIN_REAL if real else ORIGIN, "simulation": not real, "real_inference": False, "provider_inference_unverified": real, "coord_space": "image_px_v1", "taxonomy": "mg.cls.v0",
            "source_binding": {"fixture_id": fixture.get("fixture_id", ""), "width": w, "height": h, "frame_count": len(pts),
                               "source_digest": raw_digest(fixture.get("source")), "pts_us": pts},
            "producer": {"adapter_id": ADAPTER_ID, "adapter_version": ADAPTER_VERSION, "provider_id": provider_id, "model_id": MODEL_ID_REAL if real else MODEL_ID, "weights_sha256": None,
                         "concept_map": CONCEPT_MAP, "box_conventions": "origin top-left; integer px; half-up rounding of corners; out-of-frame boxes rejected, never clipped"},
            "frames": frames, "rejections": rejections, "unsupported_concepts": unsupported}
    if real:
        body["transport_receipt"] = real_view
    body["frame_status_ranges"] = []
    return {**body, "provenance": provenance(fixture, body, usage_seen), "digest": digest(body)}


def provenance(fixture: dict, body: dict, usage_seen: bool) -> dict:
    real = body["origin"] == ORIGIN_REAL
    lat = [r.get("latency_ms") for r in fixture.get("frames", []) if isinstance(r, dict) and _num(r.get("latency_ms"))]
    return {"status": "REAL_PROVIDER_RESPONSE_UNVERIFIED" if real else "SIMULATED", "real_meta_call": real, "provider_id": body["producer"]["provider_id"],
            "model_id": MODEL_ID_REAL if real else MODEL_ID, "model_version": fixture.get("model_version") if real else None, "wire_format": fixture.get("wire_format"),
            "input_digest": raw_digest(fixture.get("source")), "response_digest": raw_digest(fixture.get("frames")),
            "request_ids": [r.get("request_id") for r in fixture.get("frames", []) if isinstance(r, dict) and r.get("request_id")],
            "latency_ms_reported": lat, "usage_reported": usage_seen, "field_status": FIELD_STATUS, "live_discovery_required": LIVE_DISCOVERY_REQUIRED,
            "authenticity": ("TLS peer verified in this process; NO provider signature exists, so it is not proven that the provider's model produced the bytes" if real else "none (simulated or test)"),
            "limitations": ["an attestation proves this process dispatched an authorized request over a CA-verified TLS session and received these bytes; it does not prove the provider's model produced them, nor billing", "accuracy is not validated", "object ids are not tracks",
                            "an empty frame list is not a statement of safety unless coverage is sufficient"]}


def from_wire(parsed: dict, concept: str, width: int, height: int, pts_us: list[int], simulated: bool, receipt: dict | None = None, request_id: str | None = None,
              model_version: str | None = None, fixture_id: str = "", id_prefix: str = "") -> dict:
    """Parsed stream (`sam31.parse_stream`) -> the envelope `normalize` consumes. The timebase comes ONLY from the source (`pts_us`): the provider supplies none.
    A parse that is not PARSED_* yields NO frame at all, so every frame of the source becomes UNKNOWN (missing response), never empty."""
    frames = []
    if parsed.get("state") in ("PARSED_OBJECTS", "PARSED_NO_MATCH_REPORTED"):
        for idx, recs in sorted(parsed["frames"].items()):
            insts = [{"prompt": concept, "instance_id": (f"{id_prefix}:{r['object_id']}" if id_prefix else r["object_id"]), "box": {"format": "xyxy_px", "values": [r["box"]["x1"], r["box"]["y1"], r["box"]["x2"], r["box"]["y2"]]},
                      "score": None if r["confidence"] is None else float(r["confidence"]), "mask": {"encoding": "wire_" + r["mask"]["encoding"], "geometry": "UNVERIFIED",
                                                                                                     "rows": r["mask"]["rows"], "cols": r["mask"]["cols"]}} for r in recs]
            frames.append({"frame_index": idx, "timestamp_us": None, "request_id": request_id, "latency_ms": None, "usage": None, "error": None, "partial": False, "instances": insts})
        # a zero-match stream reports NO line: the frame exists in the source but the provider said nothing about it -> it stays a missing response (UNKNOWN), not an empty frame
    env = {"fixture_schema": FIXTURE_SCHEMA, "fixture_id": fixture_id, "simulated": simulated, "wire_format": "sam31.stream.documented-2026-11-01",
           "source": {"width": width, "height": height, "pts_us": list(pts_us)}, "frames": frames}
    if not simulated:
        env["transport_receipt"] = receipt
        env["model_version"] = model_version
    return env
