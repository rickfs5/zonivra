"""Integration tests against the real clip and the real RF-DETR Nano weights (no mocks, no scripted detections)."""
import json
import time

import numpy as np
import pytest

from conftest import VIDEO, VIDEO_SHA256, local_client, needs_video
from mineguard import analysis, decode
from mineguard.app import create_app
from mineguard.canon import file_sha256
from mineguard.detector import WeightsError

pytestmark = needs_video


def test_video_hash_is_the_registered_one():
    assert file_sha256(VIDEO) == VIDEO_SHA256


def test_probe_reports_decoder_measured_metadata():
    i = decode.probe(str(VIDEO))
    assert (i.native_width, i.native_height, i.codec_fourcc, i.timescale) == (3840, 2160, "avc1", 30000)
    assert abs(i.nominal_fps - 29.97) < 0.01 and abs(i.duration_s - 24.758) < 0.01


def test_decode_frame_table_and_determinism():
    first, second, ticks = [], [], []
    for fr in decode.iter_frames(str(VIDEO), 1280, 720):
        ticks.append(fr.pts_ticks)
        if fr.index in (0, 300):
            first.append(analysis.pixel_digest(fr.rgb))
            assert fr.rgb.shape == (720, 1280, 3) and fr.rgb.dtype == np.uint8
    for fr in decode.iter_frames(str(VIDEO), 1280, 720):
        if fr.index in (0, 300):
            second.append(analysis.pixel_digest(fr.rgb))
        if fr.index > 300:
            break
    assert len(ticks) == 742 and ticks[0] == 0
    assert all(b > a for a, b in zip(ticks, ticks[1:]))
    assert set(b - a for a, b in zip(ticks, ticks[1:])) <= {1001, 1002}  # 29.97 fps in a 1/30000 time base
    assert first == second


def test_real_inference_detects_truck_in_decoded_frame(detector):
    fr = next(decode.iter_frames(str(VIDEO), 1280, 720))
    dets = detector.predict(fr.rgb, 0.3)
    trucks = [d for d in dets if d.class_id == 8]
    assert trucks and max(d.confidence for d in trucks) > 0.5
    x1, y1, x2, y2 = max(trucks, key=lambda d: d.confidence).xyxy
    assert 0 <= x1 < x2 <= 1280 and 0 <= y1 < y2 <= 720


def test_negative_controls_show_output_depends_on_pixels(detector):
    black = np.zeros((720, 1280, 3), dtype=np.uint8)
    noise = np.random.default_rng(0).integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    for img in (black, noise):
        assert [d for d in detector.predict(img, 0.5) if d.class_id in (1, 3, 4, 6, 8)] == []


def test_detector_refuses_tampered_weights(tmp_path):
    from mineguard.detector import Detector, load_manifest_entry

    entry = load_manifest_entry()
    (tmp_path / entry["file"]).write_bytes(b"tampered")
    with pytest.raises(WeightsError):
        Detector(entry=entry, models_dir=tmp_path)


@pytest.fixture(scope="module")
def client(detector, tmp_path_factory):
    runs = tmp_path_factory.mktemp("runs")
    app = create_app(runs_dir=runs, detector_factory=lambda: detector)
    return local_client(app)


def wait(client, job_id, timeout=300):
    t = time.time()
    while time.time() - t < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.3)
    raise TimeoutError


@pytest.fixture(scope="module")
def run(client):
    vid = client.get("/api/videos").json()[0]["video_id"]
    r = client.post(f"/api/videos/{vid}/analyze", json={"stride": 30, "threshold": 0.3})
    assert r.status_code == 202
    j = wait(client, r.json()["job_id"])
    assert j["status"] == "done", j
    return j["run_id"]


def test_end_to_end_run_and_export_schema(client, run):
    from mineguard.schema import validate_observation_set

    exp = client.get(f"/api/runs/{run}/export")
    assert exp.status_code == 200 and "attachment" in exp.headers["content-disposition"]
    b = exp.json()
    obs = b["observation_set"]
    validate_observation_set(obs, {**obs["source_binding"]})
    p = obs["producer"]
    assert obs["origin"] == "model_inference" and p["weights_sha256"] == "d8d6b9ee57d4d0ed2b1f305163624712a0532cb7bce0c747317984fc5457440d"
    assert len(obs["frames"]) == 25 and obs["source_binding"]["frame_count"] == 742
    assert b["provenance"]["packages"]["rfdetr"] == "1.11.2" and b["provenance"]["packages"]["torch"]
    assert b["provenance"]["absent_by_policy"] == {"av": True, "cv2": True, "ultralytics": True, "rfdetr_plus": True}
    assert any(o["cls"]["detector_label"] == "truck" for f in obs["frames"] for o in f["objects"])
    assert all(o["confidence"]["kind"] == "model_uncalibrated" for f in obs["frames"] for o in f["objects"])


def test_run_verification_redecodes_pixels(client, run):
    v = client.get(f"/api/runs/{run}/verify").json()
    assert v["ok"], v
    assert {c["id"] for c in v["checks"]} >= {"redecoded_pixels_match", "redecoded_pts_match", "evidence_files_sha256"}


def test_verification_detects_tampering(client, run):
    st = client.app.state.st
    path = st.runs_dir / run / "frames" / "000000.jpg"
    original = path.read_bytes()
    try:
        path.write_bytes(original + b"x")
        v = client.get(f"/api/runs/{run}/verify?deep=false").json()
        assert not v["ok"] and not next(c for c in v["checks"] if c["id"] == "evidence_files_sha256")["ok"]
    finally:
        path.write_bytes(original)
    assert client.get(f"/api/runs/{run}").json()["trust"] == "unverified"   # a failed SHALLOW verification revoked the grant; restoring the bytes does not bring it back (MG-TRUST-015)


def test_evidence_frame_served(client, run):
    r = client.get(f"/api/runs/{run}/frames/0")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert client.get(f"/api/runs/{run}/frames/1").status_code == 404
    assert client.get("/api/runs/run_../frames/0").status_code == 404


def _scripted_object():
    return {"obj_key": "o1", "cls": {"label": "person", "state": "known", "detector_label": "person", "detector_class_id": 1},
            "bbox": {"x": 10, "y": 10, "w": 50, "h": 100}, "truncated": {"left": False, "right": False, "top": False, "bottom": False},
            "confidence": {"permille": 990, "kind": "model_uncalibrated"}, "track_ref": None}


def test_replay_as_inference_is_rejected(client, run):
    from mineguard.canon import digest

    obs = client.get(f"/api/runs/{run}/export").json()["observation_set"]
    assert client.post("/api/observations/import", json=obs).json()["mode"] == "inference_verified_local_run"
    # (a) tampered params without recomputing params_hash: invalid observation set (V-21)
    bad = json.loads(json.dumps(obs)); bad["producer"]["params"]["stride"] = 29
    r = client.post("/api/observations/import", json=bad)
    assert r.status_code == 422 and "V-21" in r.json()["codes"]
    # (a2) stride changed and params_hash recomputed: the analyzed frames no longer match the declared stride (V-24)
    bad2 = json.loads(json.dumps(obs)); bad2["producer"]["params"]["stride"] = 29
    bad2["producer"]["params_hash"] = digest(bad2["producer"]["params"])
    r = client.post("/api/observations/import", json=bad2)
    assert r.status_code == 422 and "V-24" in r.json()["codes"]
    # (b) internally consistent forgery (one scripted object): passes the schema but matches no verified local run
    forged = json.loads(json.dumps(obs)); forged["frames"][0]["objects"] = [_scripted_object()]
    r = client.post("/api/observations/import", json=forged)
    assert r.status_code == 422 and r.json()["msg_id"] == "mg.refusal.replay_as_inference"


def test_planted_run_directory_cannot_launder_observations(client, run):
    """MG-RC1-002: a run directory holding only a matching observations.json is not a verified run."""
    from mineguard.canon import digest16
    from mineguard.schema import obsset_digest

    st = client.app.state.st
    obs = json.loads((st.runs_dir / run / "observations.json").read_text())
    obs["frames"][0]["objects"] = [_scripted_object()]
    planted = st.runs_dir / ("run_" + digest16(obsset_digest(obs)))
    planted.mkdir()
    try:
        (planted / "observations.json").write_text(json.dumps(obs))
        r = client.post("/api/observations/import", json=obs)
        assert r.status_code == 422 and r.json()["msg_id"] == "mg.refusal.replay_as_inference"
    finally:
        import shutil
        shutil.rmtree(planted)


def test_export_json_tampering_is_detected_and_refused(client, run):
    """MG-RC1-001: export.json is what the UI/export serve; altering only it must fail verification and be refused."""
    st = client.app.state.st
    path = st.runs_dir / run / "export.json"
    original = path.read_text()
    try:
        doc = json.loads(original)
        doc["observation_set"]["frames"][0]["objects"] = []
        doc["provenance"]["packages"]["rfdetr"] = "9.9.9"
        path.write_text(json.dumps(doc))
        v = client.get(f"/api/runs/{run}/verify?deep=false").json()
        assert not v["ok"] and not next(c for c in v["checks"] if c["id"] == "export_matches_components")["ok"]
        assert client.get(f"/api/runs/{run}").status_code == 409
        assert client.get(f"/api/runs/{run}/export").status_code == 409
    finally:
        path.write_text(original)
    assert client.get(f"/api/runs/{run}/export").status_code == 200


def test_scripted_set_cannot_declare_inference(client, obsset):
    r = client.post("/api/observations/import", json=obsset("model_inference"))
    assert r.status_code == 422 and "V-09" in r.json()["codes"]
    ok = client.post("/api/observations/import", json=obsset("human_annotated")).json()
    assert ok["mode"] == "replay" and ok["origin"] == "human_annotated" and ok["banner_msg_id"] == "mg.banner.human_annotated"


def test_failed_model_load_yields_failed_job_and_no_synthetic_fallback(tmp_path):
    def boom():
        raise WeightsError("weights missing")

    c = local_client(create_app(runs_dir=tmp_path, detector_factory=boom))
    vid = c.get("/api/videos").json()[0]["video_id"]
    j = wait(c, c.post(f"/api/videos/{vid}/analyze", json={"stride": 30}).json()["job_id"])
    assert j["status"] == "failed" and "WeightsError" in j["error"] and j["run_id"] is None
    assert c.get("/api/runs").json() == []


def test_video_served_with_range_requests(client):
    vid = client.get("/api/videos").json()[0]["video_id"]
    r = client.get(f"/api/videos/{vid}/file", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100


def _forge_consistent_run(src_dir, dst_runs, mutate):
    """Build a run directory whose files are mutually consistent (digests, export, manifest) but whose boxes are scripted."""
    import shutil
    from mineguard.canon import digest, digest16
    from mineguard.schema import obsset_digest

    obs = json.loads((src_dir / "observations.json").read_text())
    mutate(obs)
    od = obsset_digest(obs)
    forged = dst_runs / ("run_" + digest16(od))
    shutil.copytree(src_dir, forged)
    manifest = json.loads((forged / "manifest.json").read_text())
    manifest["run_id"], manifest["digests"]["obsset"] = forged.name, od
    source, prov = (json.loads((forged / n).read_text()) for n in ("source.json", "provenance.json"))
    for name, doc in (("observations.json", obs), ("manifest.json", manifest)):
        (forged / name).write_text(json.dumps(doc))
    (forged / "export.json").write_text(json.dumps({"schema": "mg.export.vs01", "observation_set": obs, "source": source,
                                                     "provenance": prov, "manifest": manifest}))
    return forged


def test_scripted_boxes_in_a_consistent_run_fail_reinference(client, run, detector, tmp_path):
    """MG-RC1-003: all files, digests and pixel hashes are consistent; only the model output was scripted."""
    st = client.app.state.st

    def script_boxes(obs):
        obs["frames"][0]["objects"] = [{"obj_key": "o1", "cls": {"label": "person", "state": "known", "detector_label": "person", "detector_class_id": 1},
                                        "bbox": {"x": 10, "y": 10, "w": 50, "h": 100}, "truncated": {"left": False, "right": False, "top": False, "bottom": False},
                                        "confidence": {"permille": 990, "kind": "model_uncalibrated"}, "track_ref": None}]

    runs2 = tmp_path / "runs"
    runs2.mkdir()
    forged = _forge_consistent_run(st.runs_dir / run, runs2, script_boxes)
    c2 = local_client(create_app(runs_dir=runs2, detector_factory=lambda: detector))
    shallow = c2.get(f"/api/runs/{forged.name}/verify?deep=false").json()
    assert shallow["ok"] and shallow["deep"] is False  # a shallow check cannot see it ...
    deep = c2.get(f"/api/runs/{forged.name}/verify").json()
    assert not deep["ok"] and not next(c for c in deep["checks"] if c["id"] == "reinference_matches_observations")["ok"]
    assert c2.get(f"/api/runs/{forged.name}").json()["trust"] == "unverified"
    forged_obs = json.loads((forged / "observations.json").read_text())
    r = c2.post("/api/observations/import", json=forged_obs)
    assert r.status_code == 422 and r.json()["msg_id"] == "mg.refusal.replay_as_inference"


def test_trust_states_session_unverified_verified(client, run, detector, tmp_path):
    import shutil

    # earlier tests tamper with this shared run and fail shallow verification, which (monotonic revocation, cycle-15 MG-TRUST-015) removes its grant for good;
    # only a later successful deep verification grants again
    assert client.get(f"/api/runs/{run}/verify").json()["ok"]
    assert client.get(f"/api/runs/{run}").json()["trust"] == "verified"
    runs2 = tmp_path / "runs"
    shutil.copytree(client.app.state.st.runs_dir / run, runs2 / run)  # as after a restart: found on disk
    c2 = local_client(create_app(runs_dir=runs2, detector_factory=lambda: detector))
    assert c2.get(f"/api/runs/{run}").json()["trust"] == "unverified"
    assert [r["trust"] for r in c2.get("/api/runs").json()] == ["unverified"]
    rep = c2.get(f"/api/runs/{run}/verify").json()
    assert rep["ok"] and rep["deep"] and "reinference_matches_observations" in {c["id"] for c in rep["checks"]}
    assert c2.get(f"/api/runs/{run}").json()["trust"] == "verified"


def test_deep_verification_fails_closed_without_source_video(client, run, detector, tmp_path):
    """MG-RC1-004: a deep request must never silently degrade to a shallow pass."""
    import shutil

    runs2 = tmp_path / "runs"
    shutil.copytree(client.app.state.st.runs_dir / run, runs2 / run)
    empty_assets = tmp_path / "assets"
    empty_assets.mkdir()
    c2 = local_client(create_app(asset_dir=empty_assets, runs_dir=runs2, detector_factory=lambda: detector))
    rep = c2.get(f"/api/runs/{run}/verify").json()
    assert not rep["ok"] and not next(c for c in rep["checks"] if c["id"] == "source_available_for_deep_verify")["ok"]
    assert c2.get(f"/api/runs/{run}").json()["trust"] == "unverified"


def test_deep_verification_fails_closed_without_model(client, run, tmp_path):
    import shutil

    from mineguard.detector import WeightsError

    runs2 = tmp_path / "runs"
    shutil.copytree(client.app.state.st.runs_dir / run, runs2 / run)

    def boom():
        raise WeightsError("missing")

    c2 = local_client(create_app(runs_dir=runs2, detector_factory=boom))
    rep = c2.get(f"/api/runs/{run}/verify").json()
    assert not rep["ok"] and not next(c for c in rep["checks"] if c["id"] == "detector_available_for_deep_verify")["ok"]


def _decoder_service_mb():
    """Memory attributed to the system decoder service(s) by `top` (the service holds decoded frames on behalf of its clients)."""
    import re
    import subprocess

    out = subprocess.run(["top", "-l", "1", "-o", "mem", "-n", "12", "-stats", "pid,command,mem"], capture_output=True, text=True).stdout
    total = 0.0
    for line in out.splitlines():
        m = re.search(r"VTDecoder.*?([\d.]+)([MG])\s*$", line.strip())
        if m:
            total += float(m.group(1)) * (1024 if m.group(2) == "G" else 1)
    return total


def test_decoder_does_not_leak_decoded_frames_into_the_system_decoder_service():
    """Regression (found in RC-1S): copyNextSampleBuffer returns a +1 CMSampleBuffer that PyObjC does not release, so the decoder
    service kept every decoded frame (~3.5 MB each, ~2.7 GB per pass) until the process exited and memory ran out after ~a dozen passes."""
    before = _decoder_service_mb()
    n = 0
    for fr in decode.iter_frames(str(VIDEO), 1280, 720):
        n += 1
        if n >= 450:
            break
    after = _decoder_service_mb()
    assert n == 450
    assert after - before < 600, f"decoder service grew by {after - before:.0f} MB over 450 frames (the leak gave ~1600 MB)"
