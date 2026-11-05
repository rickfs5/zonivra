"""Evaluation orchestration shared by recorded-MP4 runs and webcam sessions: frame records -> tracker -> Zone Evaluation -> risk kernel -> events
with evidence references. Pure (no I/O, no detector). The evaluation document is a deterministic function of its inputs and carries their digests."""
from __future__ import annotations

from ..canon import digest
from . import kernel
from .kernel import ENGINE_VERSION, SEMANTICS_VERSION, Params, ZoneEngine
from .tracker import TRACKER_ID, TRACKER_VERSION, IouTracker, TrackerParams
from .zone_eval import sample_for

EVAL_SCHEMA = "mg.risk.eval.vs02"
LIMITATIONS = ["mg.lim.image_space_only", "mg.lim.uncalibrated_confidence", "mg.lim.coco_no_excavator_class", "mg.lim.not_safety_system",
               "mg.lim.experimental_thresholds", "mg.lim.tracker_identity_not_guaranteed", "mg.lim.sampled_state_assumed_between_samples"]
POLICY_ID = "mg-policy-vs02"
POLICY_VERSION = "0.2.0"


def default_params(frame_dts_us: list[int]) -> Params:
    """Params derived deterministically from the stream: a silence longer than 2.5x the typical analyzed-sample interval is an unknown interval."""
    dts = sorted(frame_dts_us)
    med = dts[len(dts) // 2] if dts else 33_333
    return Params(max_sample_gap_us=max(100_000, (med * 5 + 1) // 2))


def live_params(interval_ms: int) -> tuple[Params, TrackerParams]:
    """Webcam sessions: a silence longer than 2.5 sample periods is an unknown interval; a track unseen for 4 periods (min 1 s) ends."""
    return Params(max_sample_gap_us=max(100_000, interval_ms * 2500 + 500)), TrackerParams(max_age_us=max(1_000_000, interval_ms * 4000))


def params_for_observations(obs: dict, recs: list[dict]) -> tuple[Params, TrackerParams]:
    p = obs["producer"]["params"]
    if p.get("mode") == "webcam":
        return live_params(p["capture_interval_ms"])
    an = [r["t_us"] for r in recs if r["status"] == "analyzed"]
    return default_params([b - a for a, b in zip(an, an[1:])]), TrackerParams()


def policy_doc(params: Params, tracker: TrackerParams) -> dict:
    return {"policy_id": POLICY_ID, "version": POLICY_VERSION, "semantics_version": SEMANTICS_VERSION, "engine_version": ENGINE_VERSION,
            "rules": [{"rule_id": kernel.RULE_R1[0], "rule_version": kernel.RULE_R1[1], "severity": "RED", "params": {"debounce_us": params.debounce_us, "zone_kinds": ["restricted"]}},
                      {"rule_id": kernel.RULE_R2[0], "rule_version": kernel.RULE_R2[1], "severity": "YELLOW",
                       "params": {"dwell_us": params.dwell_us, "debounce_us": params.debounce_us, "zone_kinds": ["restricted"]}}],
            "common": params.as_dict(), "tracker": {"id": TRACKER_ID, "version": TRACKER_VERSION, "params": tracker.as_dict()}}


def run_tracker(records: list[dict], tparams: TrackerParams) -> tuple[list[list], list[dict], list[frozenset]]:
    """records: [{index, t_us, status, objects}] in time order. Returns per-record track id lists (parallel to objects), the track summaries and, per record, the ids of the tracks the
    tracker ended by ambiguity at that record (identity breaks for the kernel)."""
    tr = IouTracker(tparams)
    ids, breaks = [], []
    for r in records:
        if r["status"] == "analyzed":
            ids.append(tr.update(r["t_us"], [(o["cls"]["label"], o["bbox"]) for o in r["objects"]]))
            breaks.append(frozenset(tr.breaks))
        else:
            ids.append([None] * len(r["objects"]))
            breaks.append(frozenset())
    return ids, tr.tracks(), breaks


def evaluate_records(records: list[dict], end_us: int, zones: list[dict], params: Params, tparams: TrackerParams, inputs: dict, origin: str) -> dict:
    ids, tracks, breaks = run_tracker(records, tparams)
    out_zones, skipped = {}, []
    pol = policy_doc(params, tparams)
    # the FULL canonical input of an evaluation: source and frame table, observations, policy (rules, parameters, engine and semantics versions, tracker) and run
    # configuration. Event ids are bound to it, so distinct evaluations can never share ids (cycle-4 review MG-EVENT-013).
    input_digest = digest({"schema": "mg.risk.input.v2", "inputs": inputs, "policy": pol, "end_us": end_us, "origin": origin})   # EVERY stored input field participates
    for z in zones:
        if z["kind"] != "restricted":
            skipped.append({"zone_id": z["zone_id"], "reason": "kind_not_evaluated_by_any_rule"})
            continue
        eng = ZoneEngine(z["zone_id"], params, id_context=(input_digest, z["digest"]))
        for r, tid, br in zip(records, ids, breaks):
            eng.push(sample_for(z, r["index"], r["t_us"], r["status"], r["objects"], tid, identity_breaks=br))
        eng.finish(end_us)
        out_zones[z["zone_id"]] = eng.result()
    doc = {"schema": EVAL_SCHEMA, "origin": origin, "policy": pol, "input_digest": input_digest, "inputs": {**inputs, "zones": [{"zone_id": z["zone_id"], "digest": z["digest"]} for z in zones]},
           "zone_defs": zones, "tracks": tracks, "zones": out_zones, "zones_not_evaluated": skipped, "limitations": LIMITATIONS}
    doc["digest"] = eval_digest(doc)
    return doc


def eval_digest(doc: dict) -> str:
    """The evaluation is a deterministic function of its inputs. Evidence references (file names and hashes of encoded images) are OUTSIDE that contract
    (MG-DATA-001 sections 2 and 3): they are attached beside the evaluation and never enter its digest (cycle-3 review MG-EVAL-012)."""
    return digest({k: v for k, v in doc.items() if k not in ("digest", "evidence_refs")})


def records_from_observations(obs: dict, pts_us: list[int]) -> tuple[list[dict], int]:
    """Frame records from a stored observation set. Stride-skipped frames are NOT samples (the stream is sampled; time between samples is assumed
    to continue the last observed state, a documented limitation); dropped / gap / error frames are explicit unknown samples."""
    status = {}
    for rg in obs["frame_status_ranges"]:
        for i in range(rg["from_frame"], rg["to_frame"] + 1):
            status[i] = rg["status"]
    by_index = {f["frame_index"]: f for f in obs["frames"]}
    recs = []
    for i in range(len(pts_us)):
        if i in by_index:
            recs.append({"index": i, "t_us": pts_us[i], "status": "analyzed", "objects": by_index[i]["objects"]})
        elif status.get(i) and status[i] != "not_analyzed_stride":
            recs.append({"index": i, "t_us": pts_us[i], "status": status[i], "objects": []})
    return recs, 0


def attach_evidence(doc: dict, evidence: list[dict]) -> dict:
    """Evidence references for every event, keyed by event id in `doc["evidence_refs"]` (outside the evaluation digest): the analyzed frame at or immediately
    before each key instant (start, end), with the manifest's file hash. Evidence images are pictures of frames given to the model, not proof that the situation was real."""
    import bisect
    by_idx = sorted((e["frame_index"], e) for e in evidence)
    idxs = [i for i, _ in by_idx]
    refs_all = {}
    for z in doc["zones"].values():
        for ev in z["events"]:
            refs = {}
            for role, idx in (("start", ev["start_index"]), ("end", ev.get("end_index"))):
                if idx is None or not by_idx:
                    continue
                e = by_idx[max(bisect.bisect_right(idxs, idx) - 1, 0)][1]
                refs[role] = {"frame_index": e["frame_index"], "file": e["file"], "file_sha256": e["file_sha256"], "requested_index": idx}
            refs_all[ev["event_id"]] = refs
    doc["evidence_refs"] = refs_all
    return doc
