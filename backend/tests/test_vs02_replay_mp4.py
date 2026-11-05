"""VS-02 recorded-MP4 path: zones + risk over a REAL analysis run (negative control: the registered clip shows a vehicle and no person), and the replay
testing mechanism (synthetic_scripted observations stay synthetic, are never inference, never verifiable, never trusted)."""
from conftest import zones_rev
import json

import pytest

from conftest import needs_video
from test_rc1_closeout import iso  # noqa: F401  (genuine isolated real-inference run fixture)

ZONE = {"zone_id": "Z1", "kind": "restricted", "polygon": [[200, 300], [1000, 300], [1000, 700], [200, 700]], "name": "Zona 1"}


def replay_obs(base, person=range(60, 420), vehicle=range(300, 420), every=3, origin="synthetic_scripted"):
    b = base["observation_set"]["source_binding"]
    n = b["frame_count"]
    frames, analyzed = [], set()
    for i in range(0, n, every):
        objs = []
        if i in person:
            objs.append({"obj_key": "o1", "cls": {"label": "person", "state": "known", "detector_label": "scripted", "detector_class_id": 0},
                         "bbox": {"x": 400, "y": 400, "w": 60, "h": 200}, "truncated": {"left": False, "right": False, "top": False, "bottom": False},
                         "confidence": {"permille": None, "kind": "not_applicable"}, "track_ref": None})
        if i in vehicle:
            objs.append({"obj_key": "o2", "cls": {"label": "vehicle", "state": "known", "detector_label": "scripted", "detector_class_id": 0},
                         "bbox": {"x": 600, "y": 350, "w": 300, "h": 250}, "truncated": {"left": False, "right": False, "top": False, "bottom": False},
                         "confidence": {"permille": None, "kind": "not_applicable"}, "track_ref": None})
        frames.append({"frame_index": i, "status": "analyzed", "pixel_sha256": "0" * 64, "objects": objs})
        analyzed.add(i)
    rngs, start = [], None
    for i in range(n + 1):
        miss = i < n and i not in analyzed
        if miss and start is None:
            start = i
        elif not miss and start is not None:
            rngs.append({"from_frame": start, "to_frame": i - 1, "status": "not_analyzed_stride"})
            start = None
    return {"schema": "mg.obsset.vs01", "source_binding": b, "coord_space": "image_px_v1", "taxonomy": "mg.cls.v0", "origin": origin,
            "producer": {"adapter_id": "replay", "adapter_version": "0", "model_id": None, "weights_sha256": None, "params_hash": "c" * 64, "label_map_digest": None, "params": {}},
            "tracks": [], "frames": frames, "frame_status_ranges": rngs, "meta": {"created_utc": "2026-10-25T00:00:00Z", "tool": "test-replay"}}


def post_replay(iso, obs, base=None):
    return iso.client.post("/api/replays", content=json.dumps({"base_run_id": base or iso.run, "observation_set": obs}))


@needs_video
def test_real_run_zone_risk_negative_control_vehicle_without_person_is_green_no_events(iso):
    assert iso.client.put(f"/api/runs/{iso.run}/zones", json={"expected_revision": zones_rev(iso.client, f"/api/runs/{iso.run}/zones"), "zones": [ZONE]}).status_code == 200
    r = iso.client.get(f"/api/runs/{iso.run}/risk").json()
    z = r["evaluation"]["zones"]["Z1"]
    assert z["events"] == [] and r["evaluation"]["origin"] == "model_inference"
    assert not [e for e in z["events"] if e["severity"] in ("YELLOW", "RED")]
    assert all(iv["state"] in ("GREEN", "UNKNOWN") for iv in z["timeline"])
    exp = iso.client.get(f"/api/runs/{iso.run}/risk/export").json()
    assert exp["schema"] == "mg.export.vs02" and exp["bundle"]["provenance"]["model"]["weights_sha256"] and exp["evaluation"]["digest"] == r["evaluation"]["digest"]


@needs_video
def test_synthetic_replay_is_stored_as_synthetic_never_inference_and_drives_the_same_engine(iso):
    base = iso.client.get(f"/api/runs/{iso.run}").json()
    obs = replay_obs(base)
    r = post_replay(iso, obs)
    assert r.status_code == 201 and r.json()["origin"] == "synthetic_scripted"
    rid = r.json()["run_id"]
    b = iso.client.get(f"/api/runs/{rid}").json()
    assert b["observation_set"]["origin"] == "synthetic_scripted" and b["trust"] == "unverified" and b["provenance"]["model"] is None
    assert iso.client.put(f"/api/runs/{rid}/zones", json={"expected_revision": zones_rev(iso.client, f"/api/runs/{rid}/zones"), "zones": [ZONE]}).status_code == 200
    z = iso.client.get(f"/api/runs/{rid}/risk").json()
    assert z["evaluation"]["origin"] == "synthetic_scripted"
    ev = z["evaluation"]["zones"]["Z1"]["events"]
    sev = [e["severity"] for e in sorted(ev, key=lambda e: e["start_us"])]
    assert sev[:2] == ["YELLOW", "RED"], sev                        # person dwells, then a vehicle joins
    yel = next(e for e in ev if e["severity"] == "YELLOW")
    assert yel["start_us"] - yel["dwell_since_us"] >= 1_500_000
    # a synthetic run can never be verified into "inference" or gain trust
    rep = iso.client.get(f"/api/runs/{rid}/verify").json()
    assert not rep["ok"] and iso.client.get(f"/api/runs/{rid}").json()["trust"] == "unverified"
    assert iso.client.get(f"/api/runs/{rid}/frames/60").status_code == 404   # no evidence images exist for a replay


@needs_video
def test_replay_refusals(iso):
    base = iso.client.get(f"/api/runs/{iso.run}").json()
    inf = replay_obs(base, origin="model_inference")
    assert post_replay(iso, inf).status_code == 422 and post_replay(iso, inf).json()["msg_id"] == "mg.refusal.replay_as_inference"
    bad = replay_obs(base)
    bad["source_binding"] = {**bad["source_binding"], "source_digest": "f" * 64}
    assert post_replay(iso, bad).status_code == 422
    assert post_replay(iso, replay_obs(base), base="run_0000000000000000").status_code == 404
    assert iso.client.post("/api/replays", content=b'{"base_run_id": "x"}').status_code == 422
    assert iso.client.post("/api/replays", content=b'{"base_run_id": "%s", "base_run_id": "y"}' % iso.run.encode()).status_code == 422  # duplicate keys
    withsc = replay_obs(base)
    withsc["frames"][0]["objects"] = [{"obj_key": "o1", "cls": {"label": "person", "state": "known", "detector_label": "x", "detector_class_id": 0}, "bbox": {"x": 0, "y": 0, "w": 5, "h": 5},
                                       "truncated": {"left": False, "right": False, "top": False, "bottom": False}, "confidence": {"permille": 1000, "kind": "model_uncalibrated"}, "track_ref": None}]
    assert post_replay(iso, withsc).status_code == 422                                  # confidence on a scripted set (V-10)


@needs_video
def test_replay_does_not_overwrite_or_alter_the_real_run(iso):
    before = iso.client.get(f"/api/runs/{iso.run}").json()["observation_set"]["origin"]
    base = iso.client.get(f"/api/runs/{iso.run}").json()
    post_replay(iso, replay_obs(base))
    assert iso.client.get(f"/api/runs/{iso.run}").json()["observation_set"]["origin"] == before == "model_inference"
    assert not [p.name for p in iso.runs.iterdir() if p.name.startswith("_tmp_")]


@needs_video
def test_a_failed_post_check_never_installs_nor_deletes_anything_and_keeps_user_zones(detector, tmp_path):
    """MG-DATA-LOSS-010: the source check runs BEFORE installation; an identical re-analysis whose check fails must leave the existing run (with user zones) intact."""
    from mineguard.analysis import run_analysis
    from conftest import VIDEO
    from mineguard.canon import file_sha256
    runs = tmp_path / "runs"
    runs.mkdir()
    sha = file_sha256(VIDEO)
    first = run_analysis(str(VIDEO), sha, detector, 150, 0.3, runs_dir=runs)
    d = runs / first["run_id"]
    (d / "risk").mkdir()
    (d / "risk" / "zones.json").write_text('{"revision": 7, "zones": []}')
    before = sorted(str(p.relative_to(d)) for p in d.rglob("*"))

    def refuse():
        raise RuntimeError("mg.refusal.source_digest_mismatch")
    with pytest.raises(RuntimeError, match="source_digest_mismatch"):
        run_analysis(str(VIDEO), sha, detector, 150, 0.3, runs_dir=runs, post_check=refuse)
    assert (d / "risk" / "zones.json").read_text() == '{"revision": 7, "zones": []}' and sorted(str(p.relative_to(d)) for p in d.rglob("*")) == before
    assert [p.name for p in runs.iterdir()] == [first["run_id"]]                      # no staging directory left, nothing else installed
    again = run_analysis(str(VIDEO), sha, detector, 150, 0.3, runs_dir=runs)          # and a passing re-analysis still keeps the user's zones
    assert again["run_id"] == first["run_id"] and (d / "risk" / "zones.json").read_text() == '{"revision": 7, "zones": []}'


def test_an_unobserved_replay_is_an_entirely_unknown_timeline_not_an_empty_one(iso):
    """MG-RISK-013 through the accepted replay path: a replay with zero analyzed frames is accepted by the schema and must evaluate to UNKNOWN, not to nothing."""
    base = iso.client.get(f"/api/runs/{iso.run}").json()
    b = base["observation_set"]["source_binding"]
    obs = replay_obs(base)
    obs["frames"] = []
    obs["frame_status_ranges"] = [{"from_frame": 0, "to_frame": b["frame_count"] - 1, "status": "not_analyzed_stride"}]
    rid = post_replay(iso, obs).json()["run_id"]
    iso.client.put(f"/api/runs/{rid}/zones", json={"expected_revision": zones_rev(iso.client, f"/api/runs/{rid}/zones"), "zones": [ZONE]})
    z = iso.client.get(f"/api/runs/{rid}/risk").json()["evaluation"]["zones"]["Z1"]
    assert [iv["state"] for iv in z["timeline"]] == ["UNKNOWN"] and z["timeline"][0]["start_us"] == 0 and z["coverage_us"]["analyzed"] == 0


# ------------------------------------------------------------------ simulation button (no personal images, nothing existing is modified)
def _tree(d):
    import hashlib
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()}


@needs_video
def test_simulation_endpoint_makes_a_labelled_yellow_then_red_scenario_without_images(iso):
    r = iso.client.post("/api/simulations/person-vehicle", json={"base_run_id": iso.run})
    assert r.status_code == 201 and r.json()["origin"] == "synthetic_scripted" and r.json()["zone_id"] == "Z1"
    rid = r.json()["run_id"]
    assert rid != iso.run
    d = iso.runs / rid
    assert list((d / "frames").iterdir()) == [] and json.loads((d / "manifest.json").read_text())["evidence"] == []      # no image of any kind
    obs = iso.client.get(f"/api/runs/{rid}").json()
    assert obs["observation_set"]["origin"] == "synthetic_scripted" and obs["trust"] == "unverified" and obs["provenance"]["model"] is None
    assert "SIMULACION" in obs["observation_set"]["meta"]["notes"] and {o["cls"]["detector_label"] for f in obs["observation_set"]["frames"] for o in f["objects"]} <= {"SIMULACION"}
    z = iso.client.get(f"/api/runs/{rid}/risk").json()["evaluation"]["zones"]["Z1"]
    ev = sorted(z["events"], key=lambda e: e["start_us"])
    assert [e["severity"] for e in ev] == ["YELLOW", "RED"] and ev[0]["start_us"] < ev[1]["start_us"]
    assert iso.client.get(f"/api/runs/{rid}/verify").json()["ok"] is False                                                 # can never be verified as inference


@needs_video
def test_simulation_never_modifies_the_base_run_or_any_session_and_is_idempotent_keeping_user_zones(iso):
    sess = iso.runs / "session_0123456789abcdef"                    # a decoy webcam session (stands for personal data): must stay byte-identical
    (sess / "frames").mkdir(parents=True)
    (sess / "frames" / "000000.jpg").write_bytes(b"personal-image-stand-in")
    (sess / "observations.json").write_text("{}")
    before_base, before_sess = _tree(iso.dir), _tree(sess)
    rid = iso.client.post("/api/simulations/person-vehicle", json={"base_run_id": iso.run}).json()["run_id"]
    assert _tree(iso.dir) == before_base and _tree(sess) == before_sess
    mine = {"zone_id": "Mine", "polygon": [[10, 10], [200, 10], [200, 200]]}
    assert iso.client.put(f"/api/runs/{rid}/zones", json={"expected_revision": zones_rev(iso.client, f"/api/runs/{rid}/zones"), "zones": [mine]}).status_code == 200
    again = iso.client.post("/api/simulations/person-vehicle", json={"base_run_id": iso.run})
    assert again.status_code == 201 and again.json()["run_id"] == rid
    assert iso.client.get(f"/api/runs/{rid}/zones").json()["zones"][0]["zone_id"] == "Mine"          # the user's zone edit survived the second run
    assert _tree(iso.dir) == before_base and _tree(sess) == before_sess
    assert not [p for p in iso.runs.iterdir() if p.name.startswith("_tmp_")]


@needs_video
def test_simulation_refusals(iso):
    assert iso.client.post("/api/simulations/person-vehicle", json={"base_run_id": "run_0000000000000000"}).status_code == 404
    assert iso.client.post("/api/simulations/person-vehicle", json={"base_run_id": "session_0123456789abcdef"}).status_code == 422
    assert iso.client.post("/api/simulations/person-vehicle", json={"base_run_id": "../x"}).status_code == 422
    assert iso.client.post("/api/simulations/person-vehicle", json={}).status_code == 422
