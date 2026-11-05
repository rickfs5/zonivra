"""FINAL-ENGINEERING-15-R1: deterministic INDUSTRIAL SCENARIO replays.

What this is: HAND-WRITTEN observations (a person and/or one vehicle-class object, as boxes in a 640x360 image space, 5 samples per second) pushed through the SAME tracker, zone sampling and
deterministic risk kernel that a live webcam session uses (`evaluation.live_params`). The states, events and coverage are RECOMPUTED on every request; nothing is stored or hard-coded.
What this is not: footage, a camera, RF-DETR output, a model run, or an incident. Every result says so (`provenance`). Imported/provider evidence plays no part. The kernel is not modified
and has no scenario-specific behaviour; if a rule gives GREEN, the scenario shows GREEN.
No detector, taxonomy, model, file, network or environment access: pure computation (tests assert the module imports none of them)."""
from __future__ import annotations

from .canon import digest
from .risk import evaluation as ev
from .risk.kernel import ZoneEngine
from .risk.tracker import IouTracker
from .risk.zone_eval import make_zone, sample_for

SCHEMA = "mg.scenario/1"
WIDTH, HEIGHT, INTERVAL_MS = 640, 360, 200
ZONE_POLYGON = [[200, 100], [600, 100], [600, 340], [200, 340]]
PERSON_IN, PERSON_OUT = (300, 160), (20, 160)
VEHICLE_AT = (360, 150)

# (seconds, input spec). Specs: empty | person_in | person_out | both | vehicle_only | gap (the source is silent: no sample at all)
SCENARIOS = {
    "case01_haul_truck_pedestrian": {"case": "01", "vehicle_label": "vehicle", "caveats": ["mg.scn.cav.vehicle_class", "mg.scn.cav.image_space", "mg.scn.cav.human_review"],
        "plan": [(2, "empty"), (3, "person_in"), (3, "both"), (2, "person_in"), (2, "person_out"), (5, "empty")]},
    "case02a_forklift_as_vehicle": {"case": "02", "vehicle_label": "vehicle", "caveats": ["mg.scn.cav.forklift_class", "mg.scn.cav.image_space", "mg.scn.cav.human_review"],
        "plan": [(2, "empty"), (3, "person_in"), (3, "both"), (2, "empty")]},
    "case02b_forklift_not_recognised": {"case": "02", "vehicle_label": "other", "caveats": ["mg.scn.cav.forklift_missed", "mg.scn.cav.forklift_class", "mg.scn.cav.human_review"],
        "plan": [(2, "empty"), (3, "person_in"), (3, "both"), (2, "empty")]},
    "case03a_excavator_alone": {"case": "03", "vehicle_label": "vehicle", "caveats": ["mg.scn.cav.equipment_alone", "mg.scn.cav.excavator_class", "mg.scn.cav.human_review"],
        "plan": [(2, "empty"), (4, "vehicle_only"), (2, "empty")]},
    "case03b_excavator_and_person": {"case": "03", "vehicle_label": "vehicle", "caveats": ["mg.scn.cav.excavator_class", "mg.scn.cav.image_space", "mg.scn.cav.human_review"],
        "plan": [(2, "empty"), (3, "vehicle_only"), (3, "both"), (2, "empty")]},
    "control_source_silent": {"case": "control", "vehicle_label": "vehicle", "caveats": ["mg.scn.cav.silence_is_unknown", "mg.scn.cav.human_review"],
        "plan": [(3, "empty"), (6, "gap")]},
}
SPECS = ("empty", "person_in", "person_out", "both", "vehicle_only", "gap")


def _obj(label: str, x: int, y: int, w: int, h: int) -> dict:
    return {"cls": {"label": label}, "bbox": {"x": x, "y": y, "w": w, "h": h}, "truncated": {"left": False, "right": False, "top": False, "bottom": False}, "confidence": {"permille": 900}}


def _objects(spec: str, vehicle_label: str, k: int) -> list[dict]:
    j = (k % 3) * 2                                                  # fixed jitter so the tracker sees slightly moving boxes (deterministic)
    person_in, person_out = _obj("person", PERSON_IN[0] + j, PERSON_IN[1], 40, 100), _obj("person", PERSON_OUT[0] + j, PERSON_OUT[1], 40, 100)
    vehicle = _obj(vehicle_label, VEHICLE_AT[0] + j, VEHICLE_AT[1] + 40, 160, 110)
    return {"empty": [], "person_in": [person_in], "person_out": [person_out], "both": [person_in, vehicle], "vehicle_only": [vehicle]}[spec]


def scenario_ids() -> list[str]:
    return list(SCENARIOS)


def run_scenario(sid: str) -> dict:
    """Recomputes one scenario with the production tracker and kernel. Raises KeyError for an unknown id."""
    spec = SCENARIOS[sid]
    zone = make_zone("Z1", "restricted", ZONE_POLYGON, WIDTH, HEIGHT, "Restricted zone (synthetic)")
    params, tparams = ev.live_params(INTERVAL_MS)
    engine, tracker = ZoneEngine("Z1", params), IouTracker(tparams)
    t_ms, index, samples, segments, transitions, last = 0, 0, [], [], [], None
    for seconds, kind in spec["plan"]:
        assert kind in SPECS
        seg_from, persons, vehicles = t_ms, 0, 0
        for k in range(seconds * 1000 // INTERVAL_MS):
            if kind == "gap":
                t_ms += INTERVAL_MS                                   # the source is silent: the kernel is not fed at all
                continue
            objs = _objects(kind, spec["vehicle_label"], k)
            ids = tracker.update(t_ms * 1000, [(o["cls"]["label"], o["bbox"]) for o in objs])
            engine.push(sample_for(zone, index, t_ms * 1000, "analyzed", objs, ids, identity_breaks=frozenset(tracker.breaks)))
            state, causes = engine.last_state["state"], sorted(engine.last_state.get("causes", []))[:3]
            samples.append({"t_ms": t_ms, "input": kind, "state": state})
            if state != last:
                transitions.append({"t_ms": t_ms, "sample_index": index, "from": last, "to": state, "causes": causes, "input": kind, "pending_rules": len(engine.last_state.get("pending", []))})
                last = state
            persons += sum(1 for o in objs if o["cls"]["label"] == "person")
            vehicles += sum(1 for o in objs if o["cls"]["label"] == spec["vehicle_label"] and spec["vehicle_label"] == "vehicle")
            t_ms += INTERVAL_MS
            index += 1
        segments.append({"from_ms": seg_from, "to_ms": t_ms, "input": kind})
    engine.finish(t_ms * 1000)
    res = engine.result()
    us = lambda v: None if v is None else v // 1000
    doc = {
        "schema": SCHEMA, "id": sid, "case": spec["case"],
        "provenance": {"kind": "SYNTHETIC_SCENARIO_REPLAY", "real_footage": False, "model_inference": False, "detections": "HAND_WRITTEN_SYNTHETIC_OBSERVATIONS_NOT_RF_DETR_OUTPUT",
                       "evaluated_by": "PRODUCTION_TRACKER_AND_RISK_KERNEL_RECOMPUTED_ON_REQUEST", "persisted": False, "provider_evidence_used": False},
        "input": {"width": WIDTH, "height": HEIGHT, "interval_ms": INTERVAL_MS, "duration_ms": t_ms, "sample_count": len(samples), "vehicle_object_label": spec["vehicle_label"],
                  "segments": segments, "coord_space": "image_px_v1"},
        "zone": {"zone_id": "Z1", "kind": "restricted", "polygon": ZONE_POLYGON, "width": WIDTH, "height": HEIGHT},
        "kernel": {"engine_version": res["engine_version"], "semantics_version": res["semantics_version"], "params_us": res["params"],
                   "rules": [{"rule_id": "R_PERSON_AND_VEHICLE_IN_ZONE", "severity": "RED"}, {"rule_id": "R_PERSON_DWELL_NO_VEHICLE", "severity": "YELLOW"}]},
        "samples": samples,
        "transitions": transitions,
        "events": [{"event_id": e["event_id"], "rule_id": e["rule_id"], "rule_version": e["rule_version"], "severity": e["severity"], "start_ms": us(e["start_us"]), "end_ms": us(e["end_us"]),
                    "closed_reason": e["closed_reason"], "tracks": e["tracks"], "right_censored": e["right_censored"]} for e in res["events"]],
        "coverage_ms": {k: v // 1000 for k, v in res["coverage_us"].items() if k in ("total", "GREEN", "YELLOW", "RED", "UNKNOWN")},
        "coverage_partition_check": bool(res["coverage_us"]["partition_check"]),
        "unknown_by_cause_ms": {k: v // 1000 for k, v in res["unknown_by_cause_us"].items()},
        "caveats": spec["caveats"],
        "review": {"status": "PENDING_HUMAN_REVIEW", "reviewed_by": None, "note": "Not reviewed by a person. A synthetic drill is a demonstration of the rules, not evidence of an incident."},
    }
    doc["digest"] = digest(doc)
    return doc
