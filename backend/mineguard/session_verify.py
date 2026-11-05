"""Independent re-verification of a stored webcam session: digests, live schema, evidence files, and (deep) re-decoded stored JPEGs plus re-run inference.
Establishes local integrity and reproducibility of the inference FROM THE STORED FRAMES. It cannot establish that the frames came from a physical camera."""
from __future__ import annotations

import io
from pathlib import Path

import numpy as np
from PIL import Image

from .analysis import build_objects, pixel_digest
from .canon import digest, digest16, file_sha256, read_json_strict
from .detector import WeightsError, load_manifest_entry, verify_weights
from .risk import evaluation as ev
from .schema import LIVE_SCHEMA_ID, ValidationFailure, obsset_digest, validate_observation_set
from .verify import safe_evidence_path, trust_scope
from . import session_docs as sd
from .webcam import LATE_FILE, LATE_SCHEMA, MAX_LATE_DECLARATIONS, session_digest_check


def session_consistent(d: Path) -> bool:
    exp = read_json_strict(d / "export.json")
    return (exp.get("schema") == "mg.export.vs02.session" and exp.get("observation_set") == read_json_strict(d / "observations.json")
            and exp.get("source") == read_json_strict(d / "source.json") and exp.get("manifest") == read_json_strict(d / "manifest.json")
            and exp.get("provenance") == read_json_strict(d / "provenance.json"))


def session_fingerprint(d: Path) -> dict:
    """What a session trust grant depends on, re-read from disk: observations digest, export bundle and every stored frame (re-hashed)."""
    obs, manifest = read_json_strict(d / "observations.json"), read_json_strict(d / "manifest.json")
    ev_files = {}
    for e in manifest["evidence"]:
        p = safe_evidence_path(d, e["file"], e["frame_index"])
        if p is None:
            raise ValueError("non-canonical evidence path")
        ev_files[e["file"]] = file_sha256(p)
    return {"obsset": obsset_digest(obs), "export": file_sha256(d / "export.json"), "consistent": session_consistent(d), "evidence": ev_files}


def verify_session(d: Path, detector, deep: bool = True) -> dict:
    try:
        return _verify(d, detector, deep)
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError, WeightsError) as e:
        return {"run_id": d.name, "ok": False, "deep": bool(deep), "trust_scope": trust_scope(False, deep),
                "checks": [{"id": "run_files_readable_and_wellformed", "ok": False, "detail": f"{type(e).__name__}: {e}"}]}


def _verify(d: Path, detector, deep: bool) -> dict:
    checks: list[dict] = []

    def add(cid, ok, detail=""):
        checks.append({"id": cid, "ok": bool(ok), "detail": detail})

    obs, source, manifest = read_json_strict(d / "observations.json"), read_json_strict(d / "source.json"), read_json_strict(d / "manifest.json")
    prov = read_json_strict(d / "provenance.json")
    add("export_matches_components", session_consistent(d))
    od = obsset_digest(obs)
    add("obsset_digest", od == manifest["digests"]["obsset"] and d.name == "session_" + digest16(od), od)
    add("frame_table_digest", digest(source["frame_table"]) == source["frame_table_digest"] == obs["source_binding"]["frame_table_digest"])
    add("session_digest_matches_sample_log", session_digest_check(source) and source["source_digest"] == obs["source_binding"]["source_digest"])
    try:
        validate_observation_set(obs, {**obs["source_binding"], "source_digest": source["source_digest"], "frame_table_digest": source["frame_table_digest"]}, live=True)
        add("schema_valid", obs["schema"] == LIVE_SCHEMA_ID)
    except ValidationFailure as e:
        add("schema_valid", False, ",".join(e.codes))
    ret = source.get("retention")
    mode = ret["mode"] if isinstance(ret, dict) and ret.get("mode") in sd.RETENTION_MODES else "frames"
    tomb = d / "media_deleted.json"
    media_deleted = tomb.is_file() and not tomb.is_symlink()
    no_images = mode == "metadata_only" or media_deleted   # Privacy ON, or the user deleted the retained media afterwards
    samples = source["samples"]
    analyzed = [i for i, s in enumerate(samples) if s["status"] == "analyzed"]
    add("sample_log_matches_observations", analyzed == [f["frame_index"] for f in obs["frames"]] and len(samples) == obs["source_binding"]["frame_count"]
        and [s["capture_us"] for s in samples] == source["frame_table"]["pts_ticks"])
    ev_ = manifest["evidence"]
    if media_deleted:
        try:   # the tombstone is part of what is claimed ("these images existed and were deleted"): it must describe exactly the historical evidence set (cycle-16 MG-EVIDENCE-016)
            t = read_json_strict(tomb)
            tomb_ok = (mode == "frames" and isinstance(t, dict) and set(t) == {"deleted_utc", "files", "bytes_reported", "evidence_sha256"} and isinstance(t["deleted_utc"], str)
                       and all(type(t[k]) is int and t[k] >= 0 for k in ("files", "bytes_reported")) and t["files"] == len(manifest["evidence"]) and t["evidence_sha256"] == digest(manifest["evidence"]))
        except (OSError, ValueError, KeyError, TypeError):
            tomb_ok = False
        add("media_deletion_tombstone_matches_the_historical_evidence_set", tomb_ok,
            "verified: the number of deleted files and the digest of the historical evidence list; NOT verifiable after deletion: deleted_utc and bytes_reported (recorded values, informational only)")
    if no_images:
        fd = d / "frames"
        none_on_disk = not d.is_symlink() and (not fd.exists() or (fd.is_dir() and not fd.is_symlink() and not any(fd.iterdir())))
        add("no_frames_persisted_on_disk", none_on_disk, "Privacy ON or media deleted by the user: no image file may exist in the session directory")
        add("evidence_list_matches_retention", (ev_ == []) if mode == "metadata_only" else True)
        canonical, safe = True, {}
    else:
        safe = {e["frame_index"]: safe_evidence_path(d, e["file"], e["frame_index"]) for e in ev_}
        frames_ok = not d.is_symlink() and not (d / "frames").is_symlink() and (d / "frames").is_dir()
        add("run_and_frames_directories_are_real", frames_ok)
        on_disk = sorted(q.name for q in (d / "frames").iterdir()) if frames_ok else []
        canonical = all(v is not None for v in safe.values()) and on_disk == sorted(e["file"].split("/")[1] for e in ev_) and len({e["file"] for e in ev_}) == len(ev_)
        add("evidence_paths_canonical", canonical, f"{len(ev_)} entries; {len(on_disk)} files")
        bad = [e["file"] for e in ev_ if file_sha256(safe[e["frame_index"]]) != e["file_sha256"]] if canonical else ["<not checked>"]
        add("evidence_files_sha256", canonical and not bad, f"mismatches={bad[:3]}")
        add("evidence_file_hashes_match_sample_log", all(samples[e["frame_index"]]["jpeg_sha256"] == e["file_sha256"] for e in ev_))
    if source.get("provenance_sha256") is not None:   # sessions stored with the provenance bound into their identity: ANY change to the complete document (also a coordinated change of provenance.json and export.json) is detected
        add("provenance_document_equals_the_digest_bound_into_the_session_identity", digest(prov) == source["provenance_sha256"])
    elif no_images:
        add("provenance_runtime_environment_not_independently_verifiable", False, "this session predates provenance binding and has no images: runtime package/device fields cannot be checked")
    entry = load_manifest_entry()
    add("weights_hash_matches_manifest", obs["producer"]["weights_sha256"] == entry["sha256"])
    add("origin_is_model_inference", obs["origin"] == "model_inference")
    add("provenance_binds_weights", prov.get("model", {}).get("weights_sha256") == obs["producer"]["weights_sha256"])
    cap = prov.get("capture") if isinstance(prov.get("capture"), dict) else {}
    cap_fields = {k: cap.get(k) for k in ("origin", "device_label", "user_agent", "first_capture_clock_us")}
    add("provenance_capture_equals_the_source_bound_declaration", cap_fields == source.get("capture"))
    add("provenance_declares_client_capture_origin", sd.capture_ok(cap_fields) and cap.get("declared_by") == "client" and cap.get("physical_camera_verified_by_server") is False
        and set(cap) == {"declared_by", "physical_camera_verified_by_server", *cap_fields})
    pts_rel = [x["capture_us"] for x in samples]
    cur = source.get("session_format") == sd.SESSION_FORMAT
    add("session_format_is_current", cur, "the digest-bound session format of this build" if cur else
        "no current format marker: a legacy session and a current one downgraded by removing members and recomputing hashes cannot be told apart by content, so the sample log and coverage accounting are reported as unavailable and the session stays UNVERIFIED")
    add("sample_log_shape_is_exact", sd.samples_valid(samples, mode), "every sample has exactly its members; status/reason pairs are the recorded ones; sequences increase; each accepted frame has exactly one recorded outcome; an image hash exists only for an analyzed frame of a Privacy-OFF session")
    add("coverage_accounting_matches_the_sample_log", cur and sd.accounting_consistent(source.get("live_stats") or {}, samples, mode, current=True),
        "prefix, gap and tail losses, drops and the unknown intervals are recomputed from the sample log; the capture count and the tail basis remain client declarations" if cur else "accounting unavailable for a session without the current format marker")
    lf = d / LATE_FILE
    if lf.exists() or lf.is_symlink():
        try:
            doc = read_json_strict(lf) if lf.is_file() and not lf.is_symlink() else None
            dl = doc["declarations"]
            seqs = [x["declared_next_seq"] for x in dl]
            late_ok = (doc is not None and doc.get("schema") == LATE_SCHEMA and doc.get("digest_bound") is False and doc.get("session") == d.name
                       and source.get("closed_reason") == "client_disconnected" and 1 <= len(dl) <= MAX_LATE_DECLARATIONS
                       and all(isinstance(q, int) and not isinstance(q, bool) and q > 0 for q in seqs) and all(b > a for a, b in zip(seqs, seqs[1:]))
                       and all(x["unobserved_tail_frames_declared"] == x["declared_next_seq"] - x["last_accepted_seq"] - 1 >= 0 for x in dl)
                       and all(x["last_accepted_seq"] == dl[0]["last_accepted_seq"] for x in dl))
        except (OSError, ValueError, KeyError, TypeError):
            late_ok = False
        add("late_declaration_sidecar_is_wellformed_and_not_part_of_the_identity", late_ok, "informational client declaration received after an unexpected finalization; never part of any digest")
    exp_model = {"model_id": entry["model_id"], "architecture": entry["architecture"], "weights_sha256": entry["sha256"], "source_url": entry["source_url"],
                 "license_evidence": entry["license_evidence"]}
    add("provenance_model_record_equals_the_approved_manifest_entry", prov.get("model") == exp_model)
    add("manifest_rebuilt_from_primary_data_equals_stored",
        source.get("closed_reason") in sd.CLOSE_REASONS and sd.live_stats_ok(source.get("live_stats"))
        and manifest == sd.build_manifest(d.name, source["source_digest"], source["frame_table_digest"], od, sd.build_evidence(obs["frames"], samples, pts_rel, mode), samples,
                                          source.get("closed_reason"), source.get("live_stats"), ret),
        "counters, drops, evidence list, limitations, digests and close reason are recomputed from the sample log and observations")
    fidx = [f["frame_index"] for f in obs["frames"]]
    if mode == "metadata_only":
        add("frame_order_canonical", all(b > a for a, b in zip(fidx, fidx[1:])))
    else:
        add("evidence_matches_observations", {(e["frame_index"], e["pixel_sha256"]) for e in ev_} == {(f["frame_index"], f["pixel_sha256"]) for f in obs["frames"]})
        add("frame_order_canonical", all(b > a for a, b in zip(fidx, fidx[1:])) and fidx == [e["frame_index"] for e in ev_])
    stored = d / "risk" / "evaluation.json"
    if stored.is_file():
        ft = source["frame_table"]
        recs = [{"index": i, "t_us": ft["pts_ticks"][i] - ft["pts_ticks"][0], "status": s["status"], "objects": next((f["objects"] for f in obs["frames"] if f["frame_index"] == i), [])}
                for i, s in enumerate(samples)]
        sj = read_json_strict(stored)
        zones = sj["zone_defs"]  # the stored evaluation is a function of (observations, the zone definitions IT recorded); later zone edits do not make it stale
        add("stored_evaluation_zone_definitions_consistent", [{"zone_id": z["zone_id"], "digest": z["digest"]} for z in zones] == sj["inputs"]["zones"]
            and all(z["digest"] == __import__("mineguard.risk.zone_eval", fromlist=["x"]).zone_digest(z) for z in zones))
        end = recs[-1]["t_us"] + ft["last_frame_duration_ticks"]
        params, tparams = ev.params_for_observations(obs, recs)
        base_inputs = {k: v for k, v in sj["inputs"].items() if k != "zones"}   # `zones` is derived (added by the evaluation itself), never an input
        again = ev.attach_evidence(ev.evaluate_records(recs, end, zones, params, tparams, base_inputs, "model_inference"), manifest["evidence"])
        stored_pol = (sj.get("policy") or {}) if isinstance(sj, dict) else {}
        if stored_pol.get("engine_version") != ev.ENGINE_VERSION or (stored_pol.get("tracker") or {}).get("version") != ev.TRACKER_VERSION:
            # the stored evaluation is a derived cache produced by another engine/tracker implementation: it is NOT compared (a different implementation legitimately yields a different digest);
            # the risk views always recompute from the observations with the current engine (cycle-18 MG-VERSION-018)
            add("risk_evaluation_engine_version_differs_from_current_not_compared", True,
                f"stored by {stored_pol.get('engine_version')} / tracker {(stored_pol.get('tracker') or {}).get('version')}, current {ev.ENGINE_VERSION} / {ev.TRACKER_VERSION}: reproduction was not checked")
        else:
            add("risk_evaluation_reproduces_from_stored_observations", again["digest"] == sj["digest"] and again["evidence_refs"] == sj.get("evidence_refs"),
                "stored evaluation and its (separate) evidence references recomputed from observations, zones and the manifest")
    visual = "performed" if deep else "not_requested"
    if no_images:
        visual, deep = "unavailable_no_frames", False   # the pixels were discarded: a deep visual verification can neither be run nor claimed
        add("deep_visual_verification_unavailable", True, "no frames exist (Privacy ON or media deleted): only metadata integrity was checked; inference cannot be reproduced")
    elif not deep:
        add("deep_checks_skipped", True, "shallow verification only: stored frames were NOT re-decoded and the model was NOT re-run")
    elif detector is None:
        add("detector_available_for_deep_verify", False, "the model could not be loaded; cannot re-run inference")
    elif not canonical:
        add("deep_checks_skipped_noncanonical", False, "evidence paths are not canonical; nothing was decoded")
    else:
        try:
            verify_weights(detector.entry)  # the executing weights file is re-hashed before any trust can be granted
            weights_ok = detector.weights_sha256 == entry["sha256"] == obs["producer"]["weights_sha256"]
        except WeightsError as e:
            weights_ok = False
        add("executing_weights_file_rehashed_and_equal", weights_ok)
        exp_prov = sd.build_provenance(detector.device, detector.entry, detector.weights_sha256, cap_fields)
        add("provenance_rebuilt_from_executing_environment_equals_stored", prov == exp_prov, "runtime packages, platform, device, model record and capture declaration")
        add("producer_params_match_executing_detector", obs["producer"]["params"].get("device") == detector.device and obs["producer"]["params"].get("resolution") == int(detector.resolution))
        W, H = obs["source_binding"]["width"], obs["source_binding"]["height"]
        thr = obs["producer"]["params"]["threshold_permille"] / 1000
        mism_px, mism_obj = [], []
        for f in obs["frames"]:
            with Image.open(safe[f["frame_index"]]) as im:
                rgb = np.asarray(im.convert("RGB"))
            if rgb.shape != (H, W, 3) or pixel_digest(rgb) != f["pixel_sha256"]:
                mism_px.append(f["frame_index"])
                continue
            objs, _ = build_objects(detector.predict(rgb, thr), W, H)
            if objs != f["objects"]:
                mism_obj.append(f["frame_index"])
        add("stored_frames_decode_to_recorded_pixels", not mism_px, f"mismatches={mism_px[:5]}")
        add("reinference_matches_observations", not mism_obj and not mism_px, f"mismatches={mism_obj[:5]}")
    ok = all(c["ok"] for c in checks)
    return {"run_id": d.name, "ok": ok, "deep": bool(deep), "trust_scope": trust_scope(ok, deep), "checks": checks,
            "retention": "media_deleted" if media_deleted else mode, "visual_verification": visual}
