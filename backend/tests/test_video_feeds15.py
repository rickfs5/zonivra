"""FINAL-ENGINEERING-15-R1. Offline, lightweight (test-double detectors; no model is loaded, no camera, no network).
D1: when the detector cannot be created the webcam start must answer a stable, non-sensitive 503 and leave no session, no lock, no files behind."""
import io
import json
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from mineguard.app import create_app

BODY = {"width": 640, "height": 360, "interval_ms": 300, "threshold": 0.3, "zones": [], "privacy": "on",
        "capture": {"origin": "test_source", "device_label": "unit test", "user_agent": "pytest"}}
SECRET = "/Users/someone/private/weights.pth TOKEN-abc123"


class NullDetector:
    """TEST DOUBLE: sees nothing. Carries the provenance attributes a finished session records."""
    def __init__(self):
        from mineguard.detector import load_manifest_entry
        e = load_manifest_entry()
        self.entry, self.weights_sha256, self.device, self.resolution = e, e["sha256"], "cpu", 384

    def predict(self, rgb, threshold):
        return []


def client_for(tmp_path, factory):
    (tmp_path / "runs").mkdir(exist_ok=True)
    app = create_app(asset_dir=tmp_path / "assets", runs_dir=tmp_path / "runs", detector_factory=factory)
    c = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000), headers={"X-MG-Token": app.state.session_token}, raise_server_exceptions=False)
    return app, c


def finish_session(c, sid):
    """One tiny frame, wait until it is decided, then a clean stop: no session outlives its test."""
    buf = io.BytesIO()
    Image.fromarray(np.full((360, 640, 3), 100, np.uint8)).save(buf, "JPEG", quality=90)
    assert c.post(f"/api/webcam/sessions/{sid}/frames", content=buf.getvalue(), headers={"X-MG-Seq": "0", "X-MG-Capture-Us": "5000000", "Content-Type": "image/jpeg"}).status_code == 202
    t = time.time()
    while time.time() - t < 15:
        k = c.get(f"/api/webcam/sessions/{sid}").json()["counters"]
        if k["analyzed"] + k["adapter_error"] + k["dropped_stale"] + k["dropped_queue_overflow"] >= 1:
            break
        time.sleep(0.05)
    out = c.post(f"/api/webcam/sessions/{sid}/stop")
    assert out.status_code == 200, out.text


def raising(exc):
    def factory():
        raise exc
    return factory


def weights_error():
    from mineguard.detector import WeightsError
    return WeightsError(SECRET)


INIT_FAILURES = {"weights": weights_error, "file": lambda: FileNotFoundError(SECRET), "import": lambda: ImportError(SECRET), "runtime": lambda: RuntimeError(SECRET), "memory": lambda: MemoryError(SECRET)}


@pytest.mark.parametrize("name", sorted(INIT_FAILURES))
def test_d1_model_initialization_failure_is_a_stable_non_sensitive_503(tmp_path, name):
    app, c = client_for(tmp_path, raising(INIT_FAILURES[name]()))
    r = c.post("/api/webcam/sessions", json=BODY)
    assert r.status_code == 503
    assert r.json() == {"detail": {"msg_id": "mg.refusal.model_unavailable"}}      # the application's existing HTTPException convention
    for leak in ("Traceback", "SECRET", "TOKEN", "/Users/", "weights.pth", "Error", "line "):
        assert leak not in r.text
    assert r.headers["content-type"].startswith("application/json")


def test_d1_failure_leaves_no_session_no_lock_no_files_and_no_cached_detector(tmp_path):
    calls = []
    def factory():
        calls.append(1)
        raise RuntimeError(SECRET)
    app, c = client_for(tmp_path, factory)
    st = app.state.st
    assert c.post("/api/webcam/sessions", json=BODY).status_code == 503
    assert not st.infer_lock.locked()                                   # no lock leakage
    assert st.detector is None                                          # nothing half-initialised is cached
    assert st.session_mgr.active is None and st.session_mgr.sessions == {}   # no fake active analysis session
    assert list((tmp_path / "runs").iterdir()) == []                    # no staging directory, no frame, no run
    assert c.post("/api/webcam/sessions", json=BODY).status_code == 503 and len(calls) == 2    # a second attempt reaches the factory again: it is NOT answered 409 job_in_progress
    ov = c.get("/api/overview")
    assert ov.status_code == 200


def test_d1_programming_errors_are_not_misclassified_as_model_unavailable(tmp_path):
    for exc in (TypeError("bug"), AttributeError("bug"), ValueError("bug"), KeyError("bug"), AssertionError("bug")):
        app, c = client_for(tmp_path, raising(exc))
        r = c.post("/api/webcam/sessions", json=BODY)
        assert r.status_code == 500, type(exc).__name__
        assert "model_unavailable" not in r.text
        assert not app.state.st.infer_lock.locked()                     # still released


def test_d1_recovery_when_the_model_becomes_available(tmp_path):
    state = {"n": 0}
    def factory():
        state["n"] += 1
        if state["n"] == 1:
            raise OSError(SECRET)
        return NullDetector()
    app, c = client_for(tmp_path, factory)
    assert c.post("/api/webcam/sessions", json=BODY).status_code == 503
    r = c.post("/api/webcam/sessions", json=BODY)
    assert r.status_code == 201 and r.json()["session_id"].startswith("sess_")
    sid = r.json()["session_id"]
    assert c.post("/api/webcam/sessions", json=BODY).status_code == 409           # one session at a time (existing behaviour)
    finish_session(c, sid)


def test_shared_inference_lock_still_refuses_a_second_model_user(tmp_path):
    app, c = client_for(tmp_path, lambda: NullDetector())
    st = app.state.st
    assert st.infer_lock.acquire(blocking=False)                                   # stands for a running feed / MP4 job
    try:
        r = c.post("/api/webcam/sessions", json=BODY)
        assert r.status_code == 409 and r.json() == {"detail": {"msg_id": "mg.refusal.job_in_progress"}}
        assert st.session_mgr.active is None
    finally:
        st.infer_lock.release()
    r = c.post("/api/webcam/sessions", json=BODY)
    assert r.status_code == 201
    finish_session(c, r.json()["session_id"])


# ---- deterministic industrial scenarios (synthetic replay through the production tracker + kernel) -----------------------------------------------------------------------
import ast
import hashlib

from conftest import local_client
from mineguard import scenarios as sc

EXPECTED = {   # the transitions recorded by VIDEO-FEEDS-13 with the same kernel: a regression oracle, recomputed here, not a way to force an outcome
    "case01_haul_truck_pedestrian": [(0, "GREEN"), (2000, "UNKNOWN"), (2200, "GREEN"), (3800, "YELLOW"), (5600, "RED"), (8600, "YELLOW"), (10600, "UNKNOWN"), (12800, "GREEN")],
    "case02a_forklift_as_vehicle": [(0, "GREEN"), (2000, "UNKNOWN"), (2200, "GREEN"), (3800, "YELLOW"), (5600, "RED"), (8600, "UNKNOWN")],
    "case02b_forklift_not_recognised": [(0, "GREEN"), (2000, "UNKNOWN"), (2200, "GREEN"), (3800, "YELLOW"), (8600, "UNKNOWN")],
    "case03a_excavator_alone": [(0, "GREEN")],
    "case03b_excavator_and_person": [(0, "GREEN"), (5000, "UNKNOWN"), (5200, "GREEN"), (5600, "RED"), (8600, "UNKNOWN")],
    "control_source_silent": [(0, "GREEN")],
}


def trans(d):
    return [(t["t_ms"], t["to"]) for t in d["transitions"]]


@pytest.mark.parametrize("sid", sorted(EXPECTED))
def test_scenario_is_deterministic_and_reproduces_the_recorded_kernel_transitions(sid):
    a, b = sc.run_scenario(sid), sc.run_scenario(sid)
    assert a == b and a["digest"] == b["digest"]
    assert trans(a) == EXPECTED[sid]
    assert a["coverage_ms"]["total"] == a["input"]["duration_ms"] and a["coverage_partition_check"] is True


@pytest.mark.parametrize("sid", sorted(EXPECTED))
def test_scenario_provenance_is_truthfully_synthetic_and_unreviewed(sid):
    d = sc.run_scenario(sid)
    p = d["provenance"]
    assert p["kind"] == "SYNTHETIC_SCENARIO_REPLAY" and p["real_footage"] is False and p["model_inference"] is False and p["persisted"] is False and p["provider_evidence_used"] is False
    assert p["detections"] == "HAND_WRITTEN_SYNTHETIC_OBSERVATIONS_NOT_RF_DETR_OUTPUT"
    assert d["review"]["status"] == "PENDING_HUMAN_REVIEW" and d["review"]["reviewed_by"] is None
    assert "mg.scn.cav.human_review" in d["caveats"]
    assert d["zone"]["kind"] == "restricted" and d["zone"]["polygon"] and d["input"]["coord_space"] == "image_px_v1"
    blob = json.dumps(d)
    assert "model_inference\": true" not in blob and "rf-detr" not in blob.lower()


def test_red_happens_only_when_the_kernel_saw_a_person_and_a_vehicle_together_recently():
    """RED is held for the kernel's tolerance after the last joint sample (hysteresis, gap_tolerance_us = 1 s); it never appears without a recent person+vehicle sample."""
    for sid in sc.scenario_ids():
        d = sc.run_scenario(sid)
        tol_ms = d["kernel"]["params_us"]["gap_tolerance_us"] // 1000
        joint = [s["t_ms"] for s in d["samples"] if s["input"] == "both"] if d["input"]["vehicle_object_label"] == "vehicle" else []
        for s in d["samples"]:
            if s["state"] == "RED":
                assert any(0 <= s["t_ms"] - j <= tol_ms for j in joint), (sid, s)
        if d["coverage_ms"]["RED"]:
            assert any(e["severity"] == "RED" and e["rule_id"] == "R_PERSON_AND_VEHICLE_IN_ZONE" for e in d["events"])
        else:
            assert all(e["severity"] != "RED" for e in d["events"])


def test_case01_walks_green_yellow_red_yellow_unknown_green():
    d = sc.run_scenario("case01_haul_truck_pedestrian")
    states = [t["to"] for t in d["transitions"]]
    assert "YELLOW" in states and "RED" in states and "UNKNOWN" in states and states[-1] == "GREEN"
    assert states.index("YELLOW") < states.index("RED")
    assert [(e["severity"], e["rule_id"]) for e in d["events"]] == [("YELLOW", "R_PERSON_DWELL_NO_VEHICLE"), ("RED", "R_PERSON_AND_VEHICLE_IN_ZONE"), ("YELLOW", "R_PERSON_DWELL_NO_VEHICLE")]


def test_equipment_alone_stays_green_and_an_unrecognised_forklift_never_reaches_red():
    alone = sc.run_scenario("case03a_excavator_alone")
    assert {s["state"] for s in alone["samples"]} == {"GREEN"} and alone["events"] == [] and "mg.scn.cav.equipment_alone" in alone["caveats"]
    missed = sc.run_scenario("case02b_forklift_not_recognised")
    assert missed["coverage_ms"]["RED"] == 0 and [e["severity"] for e in missed["events"]] == ["YELLOW"] and "mg.scn.cav.forklift_missed" in missed["caveats"]
    assert sc.run_scenario("case03b_excavator_and_person")["coverage_ms"]["RED"] > 0


def test_a_silent_source_is_unknown_never_green():
    d = sc.run_scenario("control_source_silent")
    cov = d["coverage_ms"]
    assert cov["UNKNOWN"] > cov["GREEN"] and cov["RED"] == 0 and cov["YELLOW"] == 0
    assert cov["GREEN"] <= 3600                                                   # green only for the observed 3 s plus the kernel's one-sample tolerance
    assert d["input"]["sample_count"] == 15 and d["input"]["duration_ms"] == 9000


def test_scenario_module_is_pure_and_does_not_touch_the_kernel_detector_model_or_disk():
    p = Path(sc.__file__)
    tree = ast.parse(p.read_text())
    mods = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} | {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not any(any(b in m for b in ("detector", "taxonomy", "rfdetr", "torch", "meta", "sam31", "providers", "costgov", "socket", "subprocess", "api_external")) for m in mods), mods
    names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} | {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert not names & {"open", "environ", "getenv", "write_text", "write_bytes", "mkdir"}


def test_scenario_api_schema_unknown_ids_token_and_no_state_change(tmp_path):
    (tmp_path / "runs").mkdir()
    (tmp_path / "runs" / "run_0123456789abcdef").mkdir()
    (tmp_path / "runs" / "run_0123456789abcdef" / "observations.json").write_text('{"canonical":true}')
    app = create_app(asset_dir=tmp_path / "assets", runs_dir=tmp_path / "runs", detector_factory=lambda: (_ for _ in ()).throw(AssertionError("no model may be created")))
    c = local_client(app)
    snap = lambda: {str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((tmp_path / "runs").rglob("*")) if p.is_file()}
    before = snap()
    lst = c.get("/api/scenarios").json()
    assert lst["schema"] == "mg.scenario.list/1" and {s["id"] for s in lst["scenarios"]} == set(EXPECTED) and lst["provenance_kind"] == "SYNTHETIC_SCENARIO_REPLAY"
    for sid in EXPECTED:
        r = c.get(f"/api/scenarios/{sid}")
        assert r.status_code == 200 and r.json() == json.loads(json.dumps(sc.run_scenario(sid)))
    for bad in ("nope", "..%2F..%2Fetc", "CASE01", "x" * 80, "case01_haul_truck_pedestrian%00"):
        assert c.get(f"/api/scenarios/{bad}").status_code in (404, 400, 422)
    assert c.get("/api/scenarios/nope").json() == {"detail": {"msg_id": "mg.refusal.scenario_unknown"}}
    assert TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000)).get("/api/scenarios").status_code in (401, 403)     # token required like every route
    assert snap() == before                                                       # canonical runs untouched; nothing written
    assert not app.state.st.infer_lock.locked() and app.state.st.detector is None
