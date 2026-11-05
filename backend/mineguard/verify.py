"""Independent re-verification of a stored run: digests, schema, evidence files, and re-decoded pixels."""
from __future__ import annotations

import re
import stat
from pathlib import Path

from . import __version__, decode
from .analysis import (DECODE_H, DECODE_W, EVIDENCE_CONTRACT, build_evidence_entry, build_manifest, build_objects, build_params,
                       build_provenance, build_source_doc, evidence_matches_frame, pixel_digest)
from .canon import digest, digest16, file_sha256, read_json_strict
from .detector import ADAPTER_ID, WeightsError, load_manifest_entry, verify_weights
from .schema import ValidationFailure, obsset_digest, validate_observation_set


EVIDENCE_NAME = re.compile(r"frames/[0-9]{6}\.jpg")


def safe_evidence_path(run_dir: Path, name, index) -> Path | None:
    """The only way evidence is opened: exactly frames/<index>.jpg, directly inside <run>/frames, a regular non-symlink file, where the run
    directory and <run>/frames are themselves real directories (not symlinks, MG-SEC-003) and the resolved file is exactly
    <resolved run>/frames/<leaf>. Returns None for anything else, before any open or hash is attempted."""
    if not isinstance(name, str) or not isinstance(index, int) or isinstance(index, bool) or not EVIDENCE_NAME.fullmatch(name) or name != f"frames/{index:06d}.jpg":
        return None
    frames = run_dir / "frames"
    p = frames / Path(name).name
    try:
        if run_dir.is_symlink() or not stat.S_ISDIR(frames.lstat().st_mode):  # lstat: a symlinked frames/ is S_ISLNK, never a directory here
            return None
        st = p.lstat()
        resolved = p.resolve(strict=True)
    except OSError:
        return None
    return p if stat.S_ISREG(st.st_mode) and resolved == run_dir.resolve() / "frames" / p.name else None


def trust_scope(ok: bool, deep: bool) -> dict:
    """What a passing report does and does not establish. External authenticity is never provided: every input to this
    verification (run files, manifest, weights, source code) is a local file that a local attacker could edit together."""
    return {"local_integrity": bool(ok), "inference_reproduced_locally": bool(ok and deep), "external_authenticity": False}


def _diff_keys(a: dict, b: dict) -> list[str]:
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def _load(run_dir: Path, name: str):
    return read_json_strict(run_dir / name)


def bundle_consistent(run_dir: Path) -> bool:
    """export.json (what the UI and export endpoint serve) must contain exactly the component documents."""
    exp = _load(run_dir, "export.json")
    return (exp.get("schema") == "mg.export.vs01" and exp.get("observation_set") == _load(run_dir, "observations.json")
            and exp.get("source") == _load(run_dir, "source.json") and exp.get("manifest") == _load(run_dir, "manifest.json")
            and exp.get("provenance") == _load(run_dir, "provenance.json"))


def verify_run(run_dir: Path, video_path: str | None, deep: bool = True, detector=None) -> dict:
    """Fail closed: an incomplete or malformed run directory yields ok=False, never an exception."""
    try:
        return _verify_run(run_dir, video_path, deep, detector)
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError, WeightsError) as e:  # ValueError includes duplicate keys
        return {"run_id": run_dir.name, "ok": False, "deep": bool(deep), "trust_scope": trust_scope(False, deep),
                "checks": [{"id": "run_files_readable_and_wellformed", "ok": False, "detail": f"{type(e).__name__}: {e}"}]}


def _verify_run(run_dir: Path, video_path: str | None, deep: bool, detector) -> dict:
    checks: list[dict] = []

    def add(cid: str, ok: bool, detail: str = ""):
        checks.append({"id": cid, "ok": bool(ok), "detail": detail})

    obs = read_json_strict(run_dir / "observations.json")
    source = read_json_strict(run_dir / "source.json")
    manifest = read_json_strict(run_dir / "manifest.json")
    prov = _load(run_dir, "provenance.json")
    add("export_matches_components", bundle_consistent(run_dir))
    od = obsset_digest(obs)
    add("obsset_digest", od == manifest["digests"]["obsset"] and run_dir.name == "run_" + digest16(od), od)
    add("frame_table_digest", digest(source["frame_table"]) == source["frame_table_digest"] == obs["source_binding"]["frame_table_digest"])
    try:
        validate_observation_set(obs, {**obs["source_binding"], "source_digest": source["source_digest"],
                                       "frame_table_digest": source["frame_table_digest"]})
        add("schema_valid", True)
    except ValidationFailure as e:
        add("schema_valid", False, ",".join(e.codes))
    ev = manifest["evidence"]
    safe = {e["frame_index"]: safe_evidence_path(run_dir, e["file"], e["frame_index"]) for e in ev}
    names = [e["file"] for e in ev]
    frames_dir_ok = not run_dir.is_symlink() and not (run_dir / "frames").is_symlink() and (run_dir / "frames").is_dir()
    add("run_and_frames_directories_are_real", frames_dir_ok, "the run directory and its frames/ directory are not symlinks")
    on_disk = sorted(q.name for q in (run_dir / "frames").iterdir()) if frames_dir_ok else []
    canonical = (len(safe) == len(ev) and all(v is not None for v in safe.values()) and len(set(names)) == len(names)
                 and on_disk == sorted(n.split("/")[1] for n in names))
    add("evidence_paths_canonical", canonical, f"{len(names)} manifest entries; {len(on_disk)} files on disk")
    if canonical:  # nothing is opened or hashed unless every path in the manifest is valid
        bad = [e["file"] for e in ev if file_sha256(safe[e["frame_index"]]) != e["file_sha256"]]
    else:
        bad = ["<not checked: non-canonical evidence paths>"]
    add("evidence_files_sha256", canonical and not bad, f"{len(ev)} files; mismatches={bad[:3]}")
    entry = load_manifest_entry()
    add("weights_hash_matches_manifest", obs["producer"]["weights_sha256"] == entry["sha256"], entry["sha256"])
    add("origin_is_model_inference", obs["origin"] == "model_inference")
    add("provenance_binds_weights", prov.get("model", {}).get("weights_sha256") == obs["producer"]["weights_sha256"])
    ev_pairs = {(e["frame_index"], e["pixel_sha256"]) for e in manifest["evidence"]}
    add("evidence_matches_observations", ev_pairs == {(f["frame_index"], f["pixel_sha256"]) for f in obs["frames"]} and len(ev_pairs) == len(manifest["evidence"]))
    # MG-DATA-001-005: canonical order. Observations and manifest evidence are in strictly increasing frame order and in the SAME order;
    # a coordinated reordering (even with every digest recomputed) is rejected, never accepted by sorting it into shape.
    fidx, eidx = [f["frame_index"] for f in obs["frames"]], [e["frame_index"] for e in manifest["evidence"]]
    add("frame_order_canonical", all(b > a for a, b in zip(fidx, fidx[1:])) and fidx == eidx,
        f"{len(fidx)} observation frames, {len(eidx)} evidence entries; strictly increasing and identical order required")
    if not deep:
        add("deep_checks_skipped", True, "shallow verification only: pixels, timestamps and model output were NOT re-checked")
    elif not video_path:
        add("source_available_for_deep_verify", False, "the registered source video is not available; cannot re-decode")
    elif detector is None:
        add("detector_available_for_deep_verify", False, "the model could not be loaded; cannot re-run inference")
    elif (src_sha := file_sha256(video_path)) != obs["source_binding"]["source_digest"] or src_sha != source["source_digest"]:
        add("source_file_sha256_matches_binding", False, "the registered source file is not byte-identical to the one analyzed; nothing was re-decoded")
    else:
        add("source_file_sha256_matches_binding", True, "the registered source file is byte-identical to the one analyzed")
        _deep_checks(run_dir, video_path, detector, obs, source, manifest, prov, safe, canonical, src_sha, od, add)
    ok = all(c["ok"] for c in checks)
    return {"run_id": run_dir.name, "ok": ok, "deep": bool(deep), "trust_scope": trust_scope(ok, deep), "checks": checks}


def _deep_checks(run_dir, video_path, detector, obs, source, manifest, prov, safe, canonical, src_sha, od, add):
    """Everything that can be re-derived from the executing environment is re-derived and compared with the stored claim:
    the decoder (media facts, time base, timestamps, pixels), the loaded detector (model, weights, device, resolution) and
    the model (boxes). A stored document is accepted only if it equals what this environment produces."""
    p = obs["producer"]
    # --- the executing model and runtime, taken from the loaded detector and the process, not from stored claims
    weights_ok = True
    try:
        verify_weights(detector.entry)
    except WeightsError:
        weights_ok = False
    add("executing_model_matches_stored_claims",
        weights_ok and load_manifest_entry() == detector.entry and (p["adapter_id"], p["adapter_version"], p["model_id"], p["weights_sha256"])
        == (ADAPTER_ID, __version__, detector.entry["model_id"], detector.weights_sha256),
        "adapter, model id and weights hash equal the loaded detector; weights file re-hashed; manifest equals the loaded record")
    thr = p["params"].get("threshold_permille")
    params_ok = isinstance(thr, int) and not isinstance(thr, bool) and 1 <= thr <= 999 and isinstance(p["params"].get("stride"), int)
    expected_params = build_params(thr, p["params"].get("stride"), detector.resolution, detector.device) if params_ok else None
    add("params_match_executing_environment", params_ok and p["params"] == expected_params,
        f"device={detector.device} resolution={detector.resolution} decode={DECODE_W}x{DECODE_H}; stored="
        f"{ {k: v for k, v in p['params'].items() if k in ('device', 'resolution', 'decode_width', 'decode_height')} }")
    want_prov = build_provenance(detector.device, detector.entry)
    add("provenance_equals_executing_environment", prov == want_prov,
        "provenance equals the freshly collected runtime and model record" if prov == want_prov else
        f"differs in: {', '.join(_diff_keys(prov, want_prov))} (a run made in another environment must be re-analyzed)")
    # --- decode again, re-run the model
    expected = {e["frame_index"]: e["pixel_sha256"] for e in manifest["evidence"]}
    stored = {f["frame_index"]: f["objects"] for f in obs["frames"]}
    threshold = (thr if params_ok else 0) / 1000
    ticks, timescales, mismatched, reinfer_mismatch, render_bad, seen, predictions = [], set(), [], [], [], 0, 0
    for fr in decode.iter_frames(video_path, DECODE_W, DECODE_H):
        ticks.append(fr.pts_ticks)
        timescales.add(fr.timescale)
        if fr.index in expected:
            seen += 1
            if pixel_digest(fr.rgb) != expected[fr.index]:
                mismatched.append(fr.index)
            h, w, _ = fr.rgb.shape
            dets = detector.predict(fr.rgb, threshold)
            predictions += 1
            if build_objects(dets, w, h)[0] != stored.get(fr.index):
                reinfer_mismatch.append(fr.index)
            ok_img, _detail = evidence_matches_frame(safe[fr.index], fr.rgb) if safe.get(fr.index) else (False, "")
            if not ok_img:
                render_bad.append(fr.index)
    add("redecoded_pixels_match", not mismatched and seen == len(expected), f"{seen}/{len(expected)} frames; mismatched={mismatched[:5]}")
    add("redecoded_pts_match", ticks == source["frame_table"]["pts_ticks"])
    add("reinference_matches_observations", not reinfer_mismatch and seen == len(stored),
        f"model re-run on {seen}/{len(stored)} analyzed frames; mismatched={reinfer_mismatch[:5]}")
    add("model_invoked_on_every_analyzed_frame", predictions > 0 and predictions == len(stored) == len(expected),
        f"{predictions} model calls for {len(stored)} analyzed frames (at least one is required)")
    add("evidence_images_match_source_frames", not render_bad and seen == len(expected),
        f"rendering contract {EVIDENCE_CONTRACT['id']}; failing frames={render_bad[:5]}")
    # --- decoder authority: media facts, time base, timestamps and the binding are rebuilt from decoder output and must equal the stored documents
    if len(timescales) != 1 or not ticks:
        add("source_document_equals_decoder_output", False, "decoder reported no frames or a mixed time base")
        return
    info = decode.probe(video_path)
    want_source = build_source_doc(info, ticks, next(iter(timescales)), src_sha)
    add("source_document_equals_decoder_output", source == want_source,
        "media facts, time base, start ticks, frame table and its digest equal the decoder's output" if source == want_source
        else f"differs in: {', '.join(_diff_keys(source, want_source))}")
    b = want_source
    want_binding = {"source_digest": src_sha, "frame_table_digest": b["frame_table_digest"], "frame_count": len(ticks), "width": DECODE_W,
                    "height": DECODE_H, "native_width": info.native_width, "native_height": info.native_height}
    add("source_binding_equals_decoder_output", obs["source_binding"] == want_binding,
        "dimensions, frame count and frame-table digest in the observation set equal the decoder's output")
    if canonical:
        evidence = [build_evidence_entry(f["frame_index"], ticks, next(iter(timescales)), file_sha256(safe[f["frame_index"]]), f["pixel_sha256"])
                    for f in obs["frames"] if f["frame_index"] < len(ticks)]
        want_manifest = build_manifest(run_dir.name, src_sha, b["frame_table_digest"], od, evidence)
        add("manifest_equals_decoder_derived_record", manifest == want_manifest,
            "every evidence timestamp (pts_us), file hash, digest and fixed field equals the reconstruction" if manifest == want_manifest
            else f"differs in: {', '.join(_diff_keys(manifest, want_manifest))}")
    else:
        add("manifest_equals_decoder_derived_record", False, "not checked: non-canonical evidence paths")
