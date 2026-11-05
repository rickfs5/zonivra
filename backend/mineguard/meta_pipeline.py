"""Provider response -> experimental observations -> deterministic Risk Kernel -> evidence record (META LIVE READINESS 04).
Chain: stream events -> `sam31.parse_stream` -> `meta_obs.from_wire` -> `meta_obs.normalize` (edge projection, mg.obsset.metalab.x1) -> `risk.evaluation.evaluate_records`
(the existing kernel, UNMODIFIED; the provider never sets severity) -> `mg.meta.evidence/1`.
The record is NOT an `mg.incident/1` and is not stored by the application: it is the evidence a later, authorized sprint would attach to an incident. It never claims RF-DETR,
never claims accuracy, and says explicitly when the absence of detections is not a statement of safety. Pure functions: no I/O."""
from __future__ import annotations

from .canon import digest
from .risk import evaluation as ev
from .risk import zone_eval
from .risk.tracker import TrackerParams

from . import meta_obs, sam31
from . import metahttp
from .rawjson import raw_digest

SCHEMA = "mg.meta.evidence/1"
SEVERITY_ORDER = {"GREEN": 0, "UNKNOWN": 1, "YELLOW": 2, "RED": 3}


def records_from_extension(obs: dict) -> list[dict]:
    return [{"index": f["frame_index"], "t_us": f["t_us"], "status": f["status"], "objects": f["objects"]} for f in obs["frames"]]


def evaluate_extension(obs: dict, zones: list[dict]) -> dict:
    pts = obs["source_binding"]["pts_us"]
    dts = [b - a for a, b in zip(pts, pts[1:])]
    params = ev.default_params(dts)
    end_us = pts[-1] + (dts[-1] if dts else 33_333)
    inputs = {"observations_digest": obs["digest"], "producer": {"adapter_id": obs["producer"]["adapter_id"], "adapter_version": obs["producer"]["adapter_version"],
                                                                  "provider_id": obs["producer"]["provider_id"]}, "source_digest": obs["source_binding"]["source_digest"]}
    return ev.evaluate_records(records_from_extension(obs), end_us, zones, params, TrackerParams(), inputs, obs["origin"])


def full_frame_zone(width: int, height: int, zone_id: str = "Z1") -> dict:
    return zone_eval.make_zone(zone_id, "restricted", [[0, 0], [width, 0], [width, height], [0, height]], width, height, "Zona de cuadro completo")


def build_evidence(events: list, *, concept: str, width: int, height: int, pts_us: list[int], zones: list[dict], simulated: bool,
                   request_id: str | None = None, model_version: str | None = None, source_label: str = "", attestation=None, request=None, governor=None) -> dict:
    """One response (one concept: the provider takes ONE concept per request)."""
    return build_evidence_multi([{"concept": concept, "events": events, "request_id": request_id}], width=width, height=height, pts_us=pts_us, zones=zones, simulated=simulated,
                                model_version=model_version, source_label=source_label, attestation=attestation, request=request, governor=governor)


def build_evidence_multi(responses: list[dict], *, width: int, height: int, pts_us: list[int], zones: list[dict], simulated: bool,
                         model_version: str | None = None, source_label: str = "", attestation=None, request=None, governor=None) -> dict:
    """Several responses over the SAME source, one per concept (e.g. 'person' and 'truck'), merged per frame. Any response that does not parse makes the whole frame set it
    covers UNKNOWN (fail closed): a missing concept must never look like 'nothing there'. Rules needing two classes (person + vehicle) need both responses."""
    if not simulated:
        # A REAL label needs a production attestation verified against THIS request, THIS response, the governor's ledger and its own stage order, redeemed once; the observations are then
        # derived by `meta_obs.normalize_redeemed` from the redeemed events themselves. Nothing here hands pre-built frames to a real label.
        if len(responses) != 1:
            raise meta_obs.NormalizationRefused("real_evidence_supports_exactly_one_response")
        if attestation is None or request is None or governor is None:
            raise meta_obs.NormalizationRefused("real_evidence_requires_attestation_request_and_governor")
        r0 = responses[0]
        redemption, problems = metahttp.redeem(attestation, request=request, payload={"events": r0["events"]}, governor=governor)
        if problems:
            raise meta_obs.NormalizationRefused("attestation_not_verified:" + ",".join(problems))
        obs = meta_obs.normalize_redeemed(redemption, request=request, events=r0["events"], concept=r0["concept"], width=width, height=height, pts_us=pts_us, source_label=source_label,
                                          model_version=model_version)
        parsed_all = [sam31.parse_stream(r0["events"], width, height, max_frames=len(pts_us))]
    else:
        parsed_all, envs = [], []
        for n, r in enumerate(responses):
            parsed = sam31.parse_stream(r["events"], width, height, max_frames=len(pts_us))
            parsed_all.append(parsed)
            envs.append(meta_obs.from_wire(parsed, r["concept"], width, height, pts_us, True, None, r.get("request_id"), model_version, fixture_id=source_label,
                                           id_prefix=str(n) if len(responses) > 1 else ""))   # object ids are per response: namespace them
        env = envs[0]
        if len(envs) > 1:
            by = {}
            for e in envs:
                for fr in e["frames"]:
                    by.setdefault(fr["frame_index"], {**fr, "instances": []})["instances"].extend(fr["instances"])
            for i in range(len(pts_us)):
                # a frame is analyzed ONLY if EVERY concept response has a line for it: the provider emits no line for "no match" and the docs do not say how that differs from an
                # unprocessed frame, so a silent concept makes the frame UNKNOWN (absence is not safety). An unparsed response covers every frame.
                have = sum(1 for e in envs if any(fr["frame_index"] == i for fr in e["frames"]))
                if have < len(envs) or any(p["state"] not in ("PARSED_OBJECTS", "PARSED_NO_MATCH_REPORTED") for p in parsed_all):
                    by.setdefault(i, {"frame_index": i, "timestamp_us": None, "request_id": None, "latency_ms": None, "usage": None, "error": None, "partial": False, "instances": []})
                    by[i]["error"] = {"code": "concept_missing_for_frame"}
            env = {**envs[0], "frames": [by[i] for i in sorted(by)]}
        obs = meta_obs.normalize(env)
    doc = evaluate_extension(obs, zones)
    zsum = {}
    for zid, z in doc["zones"].items():
        peak = "GREEN"
        for e in z["events"]:
            if SEVERITY_ORDER[e["severity"]] > SEVERITY_ORDER[peak]:
                peak = e["severity"]
        tl = z["timeline"]
        zsum[zid] = {"final_state": tl[-1]["state"] if tl else "UNKNOWN", "peak_event_severity": peak, "event_count": len(z["events"]), "unknown_us": z["coverage_us"]["UNKNOWN"],
                     "engine_version": z["engine_version"]}
    n_unknown = sum(1 for f in obs["frames"] if f["status"] != "analyzed")
    states = [p["state"] for p in parsed_all]
    all_objects = all(s == "PARSED_OBJECTS" for s in states)
    rec = {"schema": SCHEMA, "decided_by": "deterministic_policy", "severity_by_zone": zsum,
           "provenance": {**obs["provenance"], "origin": obs["origin"], "observations_digest": obs["digest"], "evaluation_digest": doc["digest"], "parse_states": states,
                          "parse_reasons": [p["reason"] for p in parsed_all], "attributed_to_local_detector": False, "concepts": [r["concept"] for r in responses], "source": source_label},
           "coverage": {"frames_in_source": len(obs["frames"]), "frames_not_analyzed": n_unknown,
                        "absence_is_not_safety": bool(n_unknown or not all_objects),
                        "note": "no detection from this provider is NOT evidence that the zone is clear: unanalyzed frames and zero-match responses stay UNKNOWN"},
           "limitations": ["accuracy not validated", "ONE concept per request: a rule needing two classes needs two responses", "object ids are not tracks", "masks are metadata only",
                           "edge projection is provisional (canonical schema unchanged)"],
           "rejections": obs["rejections"]}
    rec["digest"] = digest({k: v for k, v in rec.items() if k != "digest"})
    return rec
