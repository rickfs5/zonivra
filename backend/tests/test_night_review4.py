"""Regression vectors for the night-sprint review 4 findings (MG-ZONE-018, MG-TRACK-018, MG-VERSION-018). WCAM-018 vectors live in test_night_review2/3. Synthetic isolated storage only."""
import json
import shutil
import threading
import time

from mineguard import analysis
from mineguard.risk import evaluation as ev
from mineguard.risk.kernel import ENGINE_VERSION
from mineguard.risk.tracker import TRACKER_VERSION, IouTracker
from test_privacy import run
from test_vs02_webcam_risk import ZONE, mk, scene  # noqa: F401


# ------------------------------------------------------------------ MG-ZONE-018
def test_a_zone_edit_cannot_be_lost_to_a_concurrent_content_addressed_install(mk, monkeypatch):
    env = mk(scene(person=range(3, 32)))
    _, rid = run(env, 8)
    final = env.runs / rid
    tmp = env.runs / "_tmp_aaaaaaaa"
    shutil.copytree(final, tmp)                                              # the freshly produced twin of the same content
    od = analysis.obsset_digest(json.loads((final / "observations.json").read_text()))
    url = f"/api/sessions/{rid}/zones"
    rev = env.c.get(url).json()["revision"]
    copying, go = threading.Event(), threading.Event()
    real_copytree = shutil.copytree
    def slow_copytree(src, dst, *a, **k):
        out = real_copytree(src, dst, *a, **k)
        if str(src).endswith(f"{rid}/risk"):
            copying.set(); go.wait(10)                                        # the installer has copied revision N and is about to swap directories
        return out
    monkeypatch.setattr("mineguard.analysis.shutil.copytree", slow_copytree)
    inst = threading.Thread(target=lambda: analysis.install_dir(tmp, final, od))
    inst.start(); assert copying.wait(10)
    res = []
    put = threading.Thread(target=lambda: res.append(env.c.put(url, json={"expected_revision": rev, "zones": [{**ZONE, "zone_id": "Z7"}]}).status_code))
    put.start(); time.sleep(0.4)
    assert not res                                                            # the edit waits for the installer: they share the artifact lock
    go.set(); inst.join(15); put.join(15)
    assert res in ([200], [409])
    cur = env.c.get(url).json()
    if res == [200]:
        assert [z["zone_id"] for z in cur["zones"]] == ["Z7"] and cur["revision"] == rev + 1     # applied to the installed directory: not lost
    assert not list(env.runs.glob("_tmp_*"))


# ------------------------------------------------------------------ MG-TRACK-018
def box(x, y=0, w=100, h=100):
    return {"x": x, "y": y, "w": w, "h": h}


def test_an_ambiguous_association_ends_the_tracks_and_never_transfers_identity():
    t = IouTracker()
    assert t.update(0, [("person", box(0))]) == [None]
    assert t.update(100_000, [("person", box(0))]) == ["T1"]                       # confirmed
    ids = t.update(200_000, [("person", box(0)), ("person", box(30))])             # both detections overlap T1: crossing / merge / split
    assert ids == [None, None]                                                     # nobody inherits T1
    assert t.update(300_000, [("person", box(0)), ("person", box(30))]) == [None, None]       # still overlapping each other: still ambiguous, still nobody is tracked
    apart = t.update(400_000, [("person", box(0)), ("person", box(500))])                    # once separated, each continues its own NEW trajectory
    assert apart.count(None) == 2 or ("T1" not in apart and len({i for i in apart if i}) == len([i for i in apart if i]))
    later = t.update(500_000, [("person", box(2)), ("person", box(502))])
    assert "T1" not in later and all(i is not None for i in later) and len(set(later)) == 2
    assert any(e["track_id"] == "T1" and e["reason"] == "ambiguous_association" for e in t.ended)


def test_an_unambiguous_association_keeps_the_trajectory():
    t = IouTracker()
    t.update(0, [("person", box(0)), ("person", box(400))])
    assert t.update(100_000, [("person", box(5)), ("person", box(405))]) == ["T1", "T2"]
    assert t.update(200_000, [("person", box(10)), ("person", box(410))]) == ["T1", "T2"]


def test_the_documented_residual_risk_is_stated_not_hidden():
    from pathlib import Path
    ui = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n.ts").read_text()
    doc = (Path(__file__).resolve().parents[2] / "docs" / "MG-RISK-002-kernel-reference.md").read_text()
    assert "trayectoria" in ui.split('"mg.lim.tracker_identity_not_guaranteed"')[1].split("\n")[0]
    assert "IoU trajectory" in doc and "replaces" in doc


# ------------------------------------------------------------------ MG-VERSION-018
def test_corrected_kernel_and_tracker_carry_new_versions():
    assert ENGINE_VERSION == "mg-risk-kernel/2.0.3" and TRACKER_VERSION == "0.3.0"


def test_a_session_stored_by_an_older_engine_still_verifies_and_says_it_was_not_compared(mk):
    env = mk(scene(person=range(3, 32)))
    _, rid = run(env, 8)
    p = env.runs / rid / "risk" / "evaluation.json"
    doc = json.loads(p.read_text())
    doc["policy"]["engine_version"], doc["policy"]["tracker"]["version"] = "mg-risk-kernel/2.0.0", "0.1.0"
    p.write_text(json.dumps(doc, indent=1))
    rep = env.c.get(f"/api/sessions/{rid}/verify?deep=false").json()
    ids = {c["id"]: c for c in rep["checks"]}
    assert rep["ok"] and "risk_evaluation_engine_version_differs_from_current_not_compared" in ids and "risk_evaluation_reproduces_from_stored_observations" not in ids
    assert "mg-risk-kernel/2.0.0" in ids["risk_evaluation_engine_version_differs_from_current_not_compared"]["detail"]
    doc2 = json.loads(p.read_text()); doc2["policy"]["engine_version"] = ev.ENGINE_VERSION; doc2["policy"]["tracker"]["version"] = ev.TRACKER_VERSION; doc2["digest"] = "0" * 64
    p.write_text(json.dumps(doc2, indent=1))                                         # same version, wrong digest: still a failure
    assert not env.c.get(f"/api/sessions/{rid}/verify?deep=false").json()["ok"]
