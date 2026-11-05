"""Night Run 02: tampering and zero-inference attacks (MG-RC1-005/006/007), real clip and real weights, no scripted detections.
Each attack mutates an isolated copy of the source and run, then restores it, so the module-scoped run stays valid."""
import contextlib
import json
import os
import shutil
import time
from types import SimpleNamespace

import pytest
from PIL import Image

from conftest import VIDEO, local_client, needs_video
from mineguard import verify as verify_mod
from mineguard.app import create_app
from mineguard.canon import digest, digest16, file_sha256
from mineguard.schema import obsset_digest

pytestmark = needs_video


class Spy:
    """Counts model calls; every call is forwarded to the real detector."""

    def __init__(self, real):
        self._real, self.calls = real, 0

    def predict(self, *a, **k):
        self.calls += 1
        return self._real.predict(*a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


@pytest.fixture(scope="module")
def iso(detector, tmp_path_factory):
    base = tmp_path_factory.mktemp("iso")
    assets, runs = base / "assets", base / "runs"
    assets.mkdir()
    shutil.copy(VIDEO, assets / VIDEO.name)
    spy = Spy(detector)
    client = local_client(create_app(asset_dir=assets, runs_dir=runs, detector_factory=lambda: spy))
    vid = client.get("/api/videos").json()[0]["video_id"]
    job = client.post(f"/api/videos/{vid}/analyze", json={"stride": 30, "threshold": 0.3}).json()["job_id"]
    t = time.time()
    while time.time() - t < 300:
        j = client.get(f"/api/jobs/{job}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            break
        time.sleep(0.3)
    assert j["status"] == "done", j
    return SimpleNamespace(client=client, spy=spy, vid=vid, video=assets / VIDEO.name, runs=runs, run=j["run_id"], dir=runs / j["run_id"])


def trust(iso):
    return iso.client.get(f"/api/runs/{iso.run}").json()["trust"]


def check(report, cid):
    return next(c for c in report["checks"] if c["id"] == cid)


def regrant(iso):
    """A successful DEEP verification is the only way to (re)gain trust (cycle-2 MG-TRUST-010): used to set up and to prove the end state."""
    rep = iso.client.get(f"/api/runs/{iso.run}/verify").json()
    assert rep["ok"], [c for c in rep["checks"] if not c["ok"]]
    assert trust(iso) == "verified"


@contextlib.contextmanager
def restoring(*paths):
    saved = {p: p.read_bytes() for p in paths}
    try:
        yield
    finally:
        for p, b in saved.items():
            p.write_bytes(b)


def rewrite_manifest_and_export(d, mutate_manifest):
    """The strongest attacker: changes the manifest and re-derives export.json so every internal consistency check holds."""
    m = json.loads((d / "manifest.json").read_text())
    mutate_manifest(m)
    (d / "manifest.json").write_text(json.dumps(m, indent=1))
    exp = json.loads((d / "export.json").read_text())
    exp["manifest"] = m
    (d / "export.json").write_text(json.dumps(exp, indent=1))


def swap_evidence(d, index, new_image):
    p = d / "frames" / f"{index:06d}.jpg"
    new_image.save(p, quality=90)
    rewrite_manifest_and_export(d, lambda m: next(e for e in m["evidence"] if e["frame_index"] == index).update(file_sha256=file_sha256(p)))


def snapshot(d):
    return [d / n for n in ("manifest.json", "export.json")] + sorted((d / "frames").glob("*.jpg"))


# ---------- MG-RC1-005: the model must actually be invoked ----------
def test_deep_verification_calls_the_model_once_per_analyzed_frame(iso):
    iso.spy.calls = 0
    rep = iso.client.get(f"/api/runs/{iso.run}/verify").json()
    assert rep["ok"], rep
    assert iso.spy.calls == 25  # stride 30 over 742 frames
    assert check(rep, "model_invoked_on_every_analyzed_frame")["ok"]
    assert trust(iso) == "verified"


def forge_zero_frame_run(iso):
    d = iso.dir

    def mutate(obs):
        obs["frames"] = []
        obs["frame_status_ranges"] = [{"from_frame": 0, "to_frame": obs["source_binding"]["frame_count"] - 1, "status": "not_analyzed_stride"}]

    obs = json.loads((d / "observations.json").read_text())
    mutate(obs)
    od = obsset_digest(obs)
    forged = iso.runs / ("run_" + digest16(od))
    shutil.copytree(d, forged)
    for jpg in (forged / "frames").glob("*.jpg"):
        jpg.unlink()
    m = json.loads((forged / "manifest.json").read_text())
    m["run_id"], m["digests"]["obsset"], m["evidence"] = forged.name, od, []
    source, prov = (json.loads((forged / n).read_text()) for n in ("source.json", "provenance.json"))
    (forged / "observations.json").write_text(json.dumps(obs))
    (forged / "manifest.json").write_text(json.dumps(m))
    (forged / "export.json").write_text(json.dumps({"schema": "mg.export.vs01", "observation_set": obs, "source": source, "provenance": prov, "manifest": m}))
    return forged, obs


def test_zero_frame_inference_set_is_rejected_and_never_trusted(iso):
    forged, obs = forge_zero_frame_run(iso)
    try:
        iso.spy.calls = 0
        rep = iso.client.get(f"/api/runs/{forged.name}/verify").json()
        assert not rep["ok"]
        assert not check(rep, "schema_valid")["ok"] and "V-24" in check(rep, "schema_valid")["detail"]
        assert not check(rep, "model_invoked_on_every_analyzed_frame")["ok"] and iso.spy.calls == 0  # no frame entered the model
        assert iso.client.get(f"/api/runs/{forged.name}").json()["trust"] == "unverified"
        r = iso.client.post("/api/observations/import", json=obs)
        assert r.status_code == 422 and "V-24" in r.json()["codes"]
    finally:
        shutil.rmtree(forged)


def test_second_line_of_defence_even_if_the_schema_check_were_bypassed(iso, monkeypatch):
    forged, _ = forge_zero_frame_run(iso)
    try:
        monkeypatch.setattr(verify_mod, "validate_observation_set", lambda *a, **k: None)
        iso.spy.calls = 0
        rep = verify_mod.verify_run(forged, str(iso.video), deep=True, detector=iso.spy)
        assert check(rep, "schema_valid")["ok"]  # bypassed on purpose
        assert not rep["ok"] and not check(rep, "model_invoked_on_every_analyzed_frame")["ok"]
    finally:
        shutil.rmtree(forged)


# ---------- MG-RC1-006: trust is bound to the exact source and evidence bytes ----------
def test_trust_is_dropped_when_the_source_is_modified_and_restored_when_identical(iso):
    regrant(iso)
    assert iso.client.get(f"/api/runs/{iso.run}/verify").json()["ok"] and trust(iso) == "verified"
    size = iso.video.stat().st_size
    with open(iso.video, "ab") as f:
        f.write(b"x")
    try:
        assert trust(iso) == "unverified"
        r = iso.client.get(f"/api/videos/{iso.vid}/file", headers={"Range": "bytes=0-99"})
        assert r.status_code == 409 and r.json()["detail"]["msg_id"] == "mg.refusal.source_digest_mismatch"
        iso.spy.calls = 0
        rep = iso.client.get(f"/api/runs/{iso.run}/verify").json()
        assert not rep["ok"] and not check(rep, "source_file_sha256_matches_binding")["ok"] and iso.spy.calls == 0
        assert trust(iso) == "unverified"
    finally:
        os.truncate(iso.video, size)
    assert file_sha256(iso.video) == file_sha256(VIDEO)
    # a FAILED deep verification removed the grant; restoring the original bytes must NOT resurrect it (cycle-2 MG-TRUST-010)
    assert trust(iso) == "unverified"
    regrant(iso)


def test_trust_is_dropped_when_the_source_is_removed(iso):
    regrant(iso)
    moved = iso.video.with_suffix(".moved")
    iso.video.rename(moved)
    try:
        assert trust(iso) == "unverified"
        assert iso.client.get(f"/api/videos/{iso.vid}/file").status_code == 404
        rep = iso.client.get(f"/api/runs/{iso.run}/verify").json()
        assert not rep["ok"]
    finally:
        moved.rename(iso.video)
    # a FAILED deep verification removed the grant; restoring the original bytes must NOT resurrect it (cycle-2 MG-TRUST-010)
    assert trust(iso) == "unverified"
    regrant(iso)


def test_consistent_evidence_swap_drops_trust_and_fails_deep_verification(iso):
    regrant(iso)
    """The attacker replaces a JPEG AND rewrites the manifest hash and export.json: every self-consistency check passes."""
    with restoring(*snapshot(iso.dir)):
        swap_evidence(iso.dir, 0, Image.new("RGB", (1280, 720), (0, 0, 0)))
        assert iso.client.get(f"/api/runs/{iso.run}/verify?deep=false").json()["ok"]  # shallow verification cannot see it
        assert trust(iso) == "unverified"
        # the grant was already removed when the mismatch was first SEEN (trust(iso) above); the swapped image is consistent with the rewritten manifest, so it is served
        # only as part of an UNVERIFIED run (the UI marks every box and table of such a run provisional); it can never regain trust without a successful deep verification
        assert iso.client.get(f"/api/runs/{iso.run}/frames/0").status_code == 200 and trust(iso) == "unverified"
        rep = iso.client.get(f"/api/runs/{iso.run}/verify").json()
        assert not rep["ok"] and not check(rep, "evidence_images_match_source_frames")["ok"]
        assert trust(iso) == "unverified"
    # a FAILED deep verification removed the grant; restoring the original bytes must NOT resurrect it (cycle-2 MG-TRUST-010)
    assert trust(iso) == "unverified"
    regrant(iso)


def test_localized_edit_of_an_evidence_image_is_detected(iso):
    with restoring(*snapshot(iso.dir)):
        img = Image.open(iso.dir / "frames" / "000030.jpg").convert("RGB")
        img.paste((255, 0, 255), (400, 200, 720, 520))  # 320x320 patch
        swap_evidence(iso.dir, 30, img)
        rep = iso.client.get(f"/api/runs/{iso.run}/verify").json()
        assert not rep["ok"] and not check(rep, "evidence_images_match_source_frames")["ok"]
        assert "30" in check(rep, "evidence_images_match_source_frames")["detail"]
        assert check(rep, "redecoded_pixels_match")["ok"]  # the declared pixel hashes were untouched: only the picture differs


def test_removed_evidence_drops_trust_and_fails_verification(iso):
    regrant(iso)
    p = iso.dir / "frames" / "000060.jpg"
    with restoring(*snapshot(iso.dir)):
        p.unlink()
        assert trust(iso) == "unverified"
        assert iso.client.get(f"/api/runs/{iso.run}/frames/60").status_code == 404
        assert not iso.client.get(f"/api/runs/{iso.run}/verify?deep=false").json()["ok"]
    # the grant was removed the moment the mismatch was SEEN (cycle-5 MG-TRUST-014, monotonic revocation): restoring the bytes does not bring it back
    assert trust(iso) == "unverified"
    regrant(iso)


def test_non_canonical_evidence_paths_are_rejected(iso):
    extra = iso.dir / "frames" / "extra.jpg"
    with restoring(*snapshot(iso.dir)):
        shutil.copy(iso.dir / "frames" / "000000.jpg", extra)
        try:
            rep = iso.client.get(f"/api/runs/{iso.run}/verify?deep=false").json()
            assert not rep["ok"] and not check(rep, "evidence_paths_canonical")["ok"]
        finally:
            extra.unlink()
    with restoring(*snapshot(iso.dir)):
        rewrite_manifest_and_export(iso.dir, lambda m: m["evidence"][0].update(file="frames/../manifest.json", file_sha256=file_sha256(iso.dir / "manifest.json")))
        rep = iso.client.get(f"/api/runs/{iso.run}/verify?deep=false").json()
        assert not check(rep, "evidence_paths_canonical")["ok"]
    link = iso.dir / "frames" / "000090.jpg"
    original = link.read_bytes()
    try:
        link.unlink(); link.symlink_to(iso.dir / "frames" / "000000.jpg")
        assert iso.client.get(f"/api/runs/{iso.run}/frames/90").status_code == 404
        assert not check(iso.client.get(f"/api/runs/{iso.run}/verify?deep=false").json(), "evidence_paths_canonical")["ok"]
    finally:
        link.unlink(); link.write_bytes(original)
    assert iso.client.get(f"/api/runs/{iso.run}/verify?deep=false").json()["ok"]


# ---------- Codex cycle 4: MG-SEC-001, MG-RC1-008, MG-RC1-009, MG-RC1-010 ----------
def test_invalid_evidence_paths_are_never_opened_or_hashed(iso, monkeypatch):
    opened = []
    real = verify_mod.file_sha256
    monkeypatch.setattr(verify_mod, "file_sha256", lambda p: (opened.append(str(p)), real(p))[1])
    for bad in ("/etc/hosts", "frames/../observations.json", "../outside.jpg", "frames/000000.jpg\\x00"):
        with restoring(*snapshot(iso.dir)):
            rewrite_manifest_and_export(iso.dir, lambda m, bad=bad: m["evidence"][0].update(file=bad))
            opened.clear()
            rep = verify_mod.verify_run(iso.dir, None, deep=False)
            assert not rep["ok"] and not check(rep, "evidence_paths_canonical")["ok"] and not check(rep, "evidence_files_sha256")["ok"]
            assert opened == [], (bad, opened)  # rejected before any open or hash
            assert trust(iso) == "unverified"


def test_special_file_as_evidence_does_not_block_the_server(iso):
    regrant(iso)
    p = iso.dir / "frames" / "000120.jpg"
    original = p.read_bytes()
    p.unlink()
    os.mkfifo(p)  # opening a FIFO for reading would block forever
    try:
        assert trust(iso) == "unverified"
        assert not check(iso.client.get(f"/api/runs/{iso.run}/verify?deep=false").json(), "evidence_paths_canonical")["ok"]
    finally:
        p.unlink(); p.write_bytes(original)
    assert trust(iso) == "unverified"    # monotonic revocation: the mismatch was observed while the FIFO stood in for the file
    regrant(iso)


def test_fabricated_provenance_cannot_be_promoted(iso):
    d = iso.dir
    for mutate in (lambda pv: pv["packages"].update(torch="9.9.9"), lambda pv: pv.update(device="cuda"),
                   lambda pv: pv["model"].update(architecture="something else"), lambda pv: pv.update(extra_claim="x")):
        with restoring(d / "provenance.json", d / "export.json"):
            pv = json.loads((d / "provenance.json").read_text())
            mutate(pv)
            (d / "provenance.json").write_text(json.dumps(pv, indent=1))
            exp = json.loads((d / "export.json").read_text()); exp["provenance"] = pv
            (d / "export.json").write_text(json.dumps(exp, indent=1))
            assert iso.client.get(f"/api/runs/{iso.run}/verify?deep=false").json()["ok"] is True  # consistent: shallow cannot see it
            assert trust(iso) == "unverified"
            rep = iso.client.get(f"/api/runs/{iso.run}/verify").json()
            assert not rep["ok"] and not check(rep, "provenance_equals_executing_environment")["ok"]
    assert iso.client.get(f"/api/runs/{iso.run}/verify").json()["ok"]


def test_approved_model_anchor_in_source_must_agree_with_the_manifest(tmp_path):
    from mineguard.detector import WeightsError, load_manifest_entry

    doc = json.loads((VIDEO.parents[1] / "models" / "MANIFEST.json").read_text())
    for key, val in (("sha256", "0" * 64), ("bytes", 1), ("file", "other.pth")):
        bad = json.loads(json.dumps(doc)); bad["models"][0][key] = val
        (tmp_path / "MANIFEST.json").write_text(json.dumps(bad))
        with pytest.raises(WeightsError):
            load_manifest_entry(manifest_path=tmp_path / "MANIFEST.json")
    (tmp_path / "MANIFEST.json").write_text(json.dumps(doc))
    assert load_manifest_entry(manifest_path=tmp_path / "MANIFEST.json")["sha256"] == doc["models"][0]["sha256"]


def test_threshold_is_quantized_once_and_reverifies(iso):
    r = iso.client.post(f"/api/videos/{iso.vid}/analyze", json={"stride": 29, "threshold": 0.3004})
    job = r.json()["job_id"]
    t = time.time()
    while time.time() - t < 300:
        j = iso.client.get(f"/api/jobs/{job}").json()
        if j["status"] in ("done", "failed"):
            break
        time.sleep(0.3)
    assert j["status"] == "done", j
    obs = json.loads((iso.runs / j["run_id"] / "observations.json").read_text())
    assert obs["producer"]["params"]["threshold_permille"] == 300  # the value actually used for inference
    assert iso.client.get(f"/api/runs/{j['run_id']}/verify").json()["ok"]


# ---------- Night Run 03: executing-environment and decoder authority, strict JSON ----------
@contextlib.contextmanager
def forged(iso, mutate_obs=None, mutate_source=None, mutate_manifest=None, mutate_prov=None):
    """Yield the id of a run whose digests, run id, params_hash, frame-table digest and export.json are ALL recomputed after the
    mutation, so it is internally consistent: only the content (device, media facts, time base, timestamps...) is false.
    If the mutation leaves the observation set unchanged the genuine run is edited in place and restored afterwards."""
    from mineguard.schema import obsset_digest as od_of

    d = iso.dir
    names = ("observations.json", "source.json", "manifest.json", "provenance.json", "export.json")
    obs, source, manifest, prov = (json.loads((d / n).read_text()) for n in names[:4])
    if mutate_source:
        mutate_source(source)
    source["frame_table_digest"] = digest(source["frame_table"])
    obs["source_binding"]["frame_table_digest"] = source["frame_table_digest"]
    if mutate_obs:
        mutate_obs(obs)
    obs["producer"]["params_hash"] = digest(obs["producer"]["params"])
    od = od_of(obs)
    target = iso.runs / ("run_" + digest16(od))
    in_place = target == d
    saved = {n: (d / n).read_bytes() for n in names} if in_place else None
    if not in_place:
        shutil.copytree(d, target)
    manifest["run_id"], manifest["digests"]["obsset"], manifest["digests"]["frame_table"] = target.name, od, source["frame_table_digest"]
    if mutate_manifest:
        mutate_manifest(manifest)
    if mutate_prov:
        mutate_prov(prov)
    try:
        for n, doc in (("observations.json", obs), ("source.json", source), ("manifest.json", manifest), ("provenance.json", prov)):
            (target / n).write_text(json.dumps(doc))
        (target / "export.json").write_text(json.dumps({"schema": "mg.export.vs01", "observation_set": obs, "source": source, "provenance": prov, "manifest": manifest}))
        yield target.name
    finally:
        if in_place:
            for n, b in saved.items():
                (d / n).write_bytes(b)
        else:
            shutil.rmtree(target)


def deep(iso, **mutations):
    with forged(iso, **mutations) as rid:
        rep = iso.client.get(f"/api/runs/{rid}/verify").json()
        assert iso.client.get(f"/api/runs/{rid}").json()["trust"] == "unverified"
        return rep


def failed_ids(rep):
    return {c["id"] for c in rep["checks"] if not c["ok"]}


def test_genuine_run_passes_every_authority_check_and_reports_its_trust_scope(iso):
    rep = iso.client.get(f"/api/runs/{iso.run}/verify").json()
    assert rep["ok"], rep
    ids = {c["id"] for c in rep["checks"]}
    assert ids >= {"executing_model_matches_stored_claims", "params_match_executing_environment", "provenance_equals_executing_environment",
                   "source_document_equals_decoder_output", "source_binding_equals_decoder_output", "manifest_equals_decoder_derived_record"}
    assert rep["trust_scope"] == {"local_integrity": True, "inference_reproduced_locally": True, "external_authenticity": False}
    shallow = iso.client.get(f"/api/runs/{iso.run}/verify?deep=false").json()
    assert shallow["trust_scope"]["inference_reproduced_locally"] is False and shallow["trust_scope"]["external_authenticity"] is False


@pytest.mark.parametrize("key,value", [("device", "cuda"), ("resolution", 640), ("decode_width", 640)])
def test_coordinated_forgery_of_device_and_parameters_fails(iso, key, value):
    def mo(o):
        o["producer"]["params"][key] = value

    def mp(pv):
        if key == "device":
            pv["device"] = value
    rep = deep(iso, mutate_obs=mo, mutate_prov=mp)
    assert not rep["ok"] and "params_match_executing_environment" in failed_ids(rep)
    if key == "device":  # the forged provenance agrees with the forged claim; only the executing environment contradicts both
        assert "provenance_equals_executing_environment" in failed_ids(rep)


def test_coordinated_forgery_of_model_metadata_fails(iso, monkeypatch, tmp_path):
    import mineguard.detector as det

    doc = json.loads((det.MANIFEST_PATH).read_text())
    doc["models"][0]["architecture"] = "RF-DETR Large (claimed)"
    doc["models"][0]["source_url"] = "https://example.invalid/other.pth"
    mf = tmp_path / "MANIFEST.json"
    mf.write_text(json.dumps(doc))
    monkeypatch.setattr(det, "MANIFEST_PATH", mf)

    d = iso.dir  # same observations, so same run id: edit the provenance in place (restored afterwards)
    with restoring(d / "provenance.json", d / "export.json"):
        pv = json.loads((d / "provenance.json").read_text())
        pv["model"]["architecture"], pv["model"]["source_url"] = doc["models"][0]["architecture"], doc["models"][0]["source_url"]
        (d / "provenance.json").write_text(json.dumps(pv))
        exp = json.loads((d / "export.json").read_text()); exp["provenance"] = pv
        (d / "export.json").write_text(json.dumps(exp))
        rep = iso.client.get(f"/api/runs/{iso.run}/verify").json()
        assert not rep["ok"]  # the manifest no longer equals the approved record: fails closed, nothing is promoted
        assert trust(iso) == "unverified"
    monkeypatch.undo()
    assert iso.client.get(f"/api/runs/{iso.run}/verify").json()["ok"]


def test_stored_adapter_identity_must_equal_the_executing_detector(iso):
    rep = deep(iso, mutate_obs=lambda o: o["producer"].update(adapter_version="9.9.9"))
    assert not rep["ok"] and "executing_model_matches_stored_claims" in failed_ids(rep)


def test_forged_media_facts_fail_even_when_everything_is_internally_consistent(iso):
    rep = deep(iso, mutate_source=lambda s: s["media"].update(native_width=1920))
    assert not rep["ok"] and "source_document_equals_decoder_output" in failed_ids(rep)
    both = dict(mutate_source=lambda s: s["media"].update(native_width=1920, native_height=1080),
                     mutate_obs=lambda o: o["source_binding"].update(native_width=1920, native_height=1080))
    rep = deep(iso, **both)
    assert not rep["ok"] and {"source_document_equals_decoder_output", "source_binding_equals_decoder_output"} <= failed_ids(rep)
    rep = deep(iso, mutate_source=lambda s: s["media"].update(codec_fourcc="hvc1", container="mov"))
    assert not rep["ok"] and "source_document_equals_decoder_output" in failed_ids(rep)


def test_forged_time_base_and_timestamps_fail(iso):
    def double(s):  # same wall-clock times (ticks and timescale both doubled), different decoder facts
        ft = s["frame_table"]
        ft["time_base"]["den"] *= 2
        ft["pts_ticks"] = [t * 2 for t in ft["pts_ticks"]]
        ft["start_ticks"] *= 2
        ft["last_frame_duration_ticks"] *= 2
    rep = deep(iso, mutate_source=double)
    assert not rep["ok"] and {"redecoded_pts_match", "source_document_equals_decoder_output"} <= failed_ids(rep)
    rep = deep(iso, mutate_source=lambda s: s["frame_table"].update(start_ticks=5))
    assert not rep["ok"] and "source_document_equals_decoder_output" in failed_ids(rep)


def test_forged_evidence_timestamp_fails_and_shallow_cannot_see_it(iso):
    with forged(iso, mutate_manifest=lambda m: m["evidence"][3].update(pts_us=m["evidence"][3]["pts_us"] + 1000)) as rid:
        assert iso.client.get(f"/api/runs/{rid}/verify?deep=false").json()["ok"] is True
    rep = deep(iso, mutate_manifest=lambda m: m["evidence"][3].update(pts_us=m["evidence"][3]["pts_us"] + 1000))
    assert not rep["ok"] and "manifest_equals_decoder_derived_record" in failed_ids(rep)
    rep = deep(iso, mutate_manifest=lambda m: m.update(note_distance_m=3))
    assert not rep["ok"] and "manifest_equals_decoder_derived_record" in failed_ids(rep)  # unknown/forbidden fields cannot ride along


# ---- duplicate keys (CJ-1) ----
def test_loads_strict_rejects_duplicates_at_every_depth():
    from mineguard.canon import DuplicateKeyError, loads_strict

    assert loads_strict('{"a": {"b": [1, {"c": 2}]}}') == {"a": {"b": [1, {"c": 2}]}}
    for bad in ('{"a": 1, "a": 1}', '{"x": {"a": 1, "a": 2}}', '{"x": [{"y": {"k": 1, "k": 2}}]}', '{"x": [1, {"z": 1, "z": 1}]}'):
        with pytest.raises(DuplicateKeyError):
            loads_strict(bad)
    for bad in ('{"a": NaN}', '{"a": Infinity}'):
        with pytest.raises(ValueError):
            loads_strict(bad)


def test_import_rejects_duplicate_keys_in_the_raw_body(iso, obsset):
    body = json.dumps(obsset("human_annotated"))
    assert iso.client.post("/api/observations/import", content=body, headers={"content-type": "application/json"}).json()["mode"] == "replay"
    nested = body.replace('"bbox": {"x": 0,', '"bbox": {"x": 999, "x": 0,')
    top = body.replace('{"schema": "mg.obsset.vs01",', '{"schema": "other", "schema": "mg.obsset.vs01",', 1)
    assert nested != body and top != body
    for bad in (nested, top):
        r = iso.client.post("/api/observations/import", content=bad, headers={"content-type": "application/json"})
        assert r.status_code == 422 and r.json()["codes"] == ["V-25"]


def test_stored_artifacts_with_duplicate_keys_are_rejected_everywhere(iso):
    d = iso.dir
    exp_text = (d / "export.json").read_text()
    obs_text = (d / "observations.json").read_text()
    with restoring(d / "export.json"):
        # a first, fabricated "observation_set" followed by the genuine one: a first-key-wins consumer would read the fake
        (d / "export.json").write_text('{"observation_set": {"fabricated": 1}, ' + exp_text.lstrip()[1:])
        rep = iso.client.get(f"/api/runs/{iso.run}/verify?deep=false").json()
        assert not rep["ok"] and not check(rep, "run_files_readable_and_wellformed")["ok"] and "DuplicateKeyError" in check(rep, "run_files_readable_and_wellformed")["detail"]
        assert iso.client.get(f"/api/runs/{iso.run}").status_code == 409
        assert iso.client.get(f"/api/runs/{iso.run}/export").status_code == 409
        assert isinstance(iso.client.get("/api/runs").json(), list)
    with restoring(d / "observations.json", d / "export.json"):
        dup = obs_text.replace('"bbox": {', '"bbox": {"x": 1, ', 1)
        assert dup != obs_text
        (d / "observations.json").write_text(dup)
        rep = iso.client.get(f"/api/runs/{iso.run}/verify?deep=false").json()
        assert not rep["ok"] and not check(rep, "run_files_readable_and_wellformed")["ok"]
        assert iso.client.get(f"/api/runs/{iso.run}/verify").json()["ok"] is False  # deep path fails closed too, without a 500
        assert all(r["run_id"] != iso.run for r in iso.client.get("/api/runs").json())  # ambiguous runs are not listed
    assert iso.client.get(f"/api/runs/{iso.run}/verify?deep=false").json()["ok"]
