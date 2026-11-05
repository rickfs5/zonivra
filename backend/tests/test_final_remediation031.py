"""MG-ALERT-031 / MG-EVENT-031 (cycle-31 review). Expectations are authored from the documented rules, not from engine output:
- delivery is never assumed: a stored delivery_state must equal the state derived from the recorded channel outcomes, or the record is refused on load (fail closed);
- the events cache must not serve rows for content that changed, even when size, inode and mtime are preserved. Synthetic, test-owned storage only."""
import copy
import json
import os
import shutil

import pytest

from mineguard.alerts import CLIENT_CHANNELS, AlertEngine, valid_alert
from test_vertical2_alerts import Clock, evt, src
from test_night_review1 import run  # noqa: F401
from test_vs02_webcam_risk import mk, scene  # noqa: F401


def engine(tmp_path, browser=True, sound=True):
    e = AlertEngine(tmp_path, wall=Clock(), mono=Clock())
    s = copy.deepcopy(e.settings)
    s["red"].update(browser=browser, sound=sound)
    e.set_settings(s)
    return e


def one(tmp_path, **kw):
    e = engine(tmp_path, **kw)
    e.observe_source(src(), [evt("evt_1", "RED")], {})
    (a,) = e.alerts.values()
    return e, a


def stored(tmp_path):
    return json.loads((tmp_path / "alerts.json").read_text())


def reload(tmp_path):
    return AlertEngine(tmp_path, wall=Clock(), mono=Clock())


# ------------------------------------------------------------------ MG-ALERT-031
def test_review_counterexample_pending_channels_with_forged_delivered_is_refused_on_reload(tmp_path):
    e, a = one(tmp_path)
    assert a["delivery"]["browser"]["status"] == "pending" and a["delivery"]["sound"]["status"] == "pending" and a["delivery_state"] == "pending"
    e._flush(True)
    doc = stored(tmp_path)
    doc["alerts"][0]["delivery_state"] = "delivered"                      # only the aggregate is changed; the content-bound id is untouched
    (tmp_path / "alerts.json").write_text(json.dumps(doc))
    r = reload(tmp_path)
    assert r.alerts == {} and r.rejected_on_load == 1                     # never displayed as «Entregada»
    assert r.summary()["rejected_on_load"] == 1


def test_valid_alert_requires_the_aggregate_to_follow_the_channels(tmp_path):
    _, a = one(tmp_path)
    assert valid_alert(a)
    for forged in ("delivered", "in_app_only", "failed"):
        b = copy.deepcopy(a); b["delivery_state"] = forged
        assert not valid_alert(b), forged


@pytest.mark.parametrize("browser,sound,expected", [
    ("pending", "pending", "pending"),
    ("delivered", "pending", "pending"),
    ("delivered", "failed", "failed"),
    ("failed", "failed", "failed"),
    ("denied", "pending", "pending"),
    ("delivered", "delivered", "delivered"),
    ("denied", "unsupported", "pending"),
])
def test_every_channel_combination_round_trips_only_with_its_derived_aggregate(tmp_path, browser, sound, expected):
    e, a = one(tmp_path)
    for ch, st in (("browser", browser), ("sound", sound)):
        if st != "pending":
            e.report_delivery(a["alert_id"], ch, st, "synthetic")
    assert a["delivery_state"] == expected
    e._flush(True)
    r = reload(tmp_path)
    assert list(r.alerts) == [a["alert_id"]] and r.alerts[a["alert_id"]]["delivery_state"] == expected and r.rejected_on_load == 0
    assert r.alerts[a["alert_id"]]["loaded_from_store"] is True           # restored provenance stays unverified
    bad = copy.deepcopy(a); bad["delivery_state"] = "delivered" if expected != "delivered" else "failed"
    assert not valid_alert(bad)


def test_unavailable_channel_never_reads_as_delivered(tmp_path):
    e, a = one(tmp_path, browser=False, sound=False)
    assert a["delivery_state"] == "pending"
    b = copy.deepcopy(a); b["delivery_state"] = "delivered"
    assert not valid_alert(b)
    e.mark_seen([a["alert_id"]])
    assert a["delivery_state"] == "in_app_only" and valid_alert(a)


@pytest.mark.parametrize("mutate", [
    lambda d: d["browser"].update(status="delivered", at=None),                    # a terminal outcome without its timestamp
    lambda d: d["browser"].update(status="pending", at="2026-10-28T10:00:00Z"),    # pending with a timestamp
    lambda d: d["browser"].update(status="delivered", at="not-a-time"),            # invalid timestamp
    lambda d: d["browser"].update(status="vanished"),                              # invalid outcome
    lambda d: d.pop("sound"),                                                      # missing channel evidence
    lambda d: d["in_app"].update(status="displayed", at=None),                     # displayed without a time
    lambda d: d["in_app"].update(status="stored", at="2026-10-28T10:00:00Z"),
])
def test_malformed_or_incoherent_channel_evidence_is_refused(tmp_path, mutate):
    _, a = one(tmp_path)
    b = copy.deepcopy(a)
    mutate(b["delivery"])
    b["delivery_state"] = "delivered"
    assert not valid_alert(b)
    c = copy.deepcopy(a); mutate(c["delivery"])                                    # even with the original aggregate kept
    assert not valid_alert(c)


def _rejected_files(tmp_path):
    return sorted(p for p in os.listdir(tmp_path) if p.startswith("alerts.rejected."))


def test_rejected_store_is_preserved_byte_for_byte_with_its_sha256(tmp_path):
    import hashlib
    e, a = one(tmp_path)
    e._flush(True)
    doc = stored(tmp_path)
    doc["alerts"][0]["delivery_state"] = "delivered"
    raw = ("\n\n" + json.dumps(doc, indent=3, sort_keys=False, ensure_ascii=False) + "   \n").encode()      # odd formatting and whitespace: must survive exactly
    (tmp_path / "alerts.json").write_bytes(raw)
    r = reload(tmp_path)
    h = hashlib.sha256(raw).hexdigest()
    assert _rejected_files(tmp_path) == [f"alerts.rejected.{h}.json"]
    assert (tmp_path / f"alerts.rejected.{h}.json").read_bytes() == raw
    assert r.preserved == {"sha256": h, "file": f"alerts.rejected.{h}.json", "bytes": len(raw), "rows_rejected": 1, "complete": True}
    assert r.summary()["preserved_rejected_store"]["complete"] is True
    assert r.alerts == {} and r.rejected_on_load == 1
    reload(tmp_path)                                                              # the rewritten store is clean now: nothing new is preserved, nothing is overwritten
    assert _rejected_files(tmp_path) == [f"alerts.rejected.{h}.json"] and (tmp_path / f"alerts.rejected.{h}.json").read_bytes() == raw


def test_valid_sibling_rows_are_recovered_apart_from_the_preserved_bytes(tmp_path):
    e = engine(tmp_path)
    e.observe_source(src(), [evt("evt_1", "RED"), evt("evt_2", "RED")], {})
    e._flush(True)
    doc = stored(tmp_path)
    assert len(doc["alerts"]) >= 1
    good = doc["alerts"][0]["alert_id"]
    bad = copy.deepcopy(doc["alerts"][0]); bad["alert_id"] = "alt_" + "0" * 16
    doc["alerts"].append(bad)                                                      # fails identity binding
    raw = json.dumps(doc).encode()
    (tmp_path / "alerts.json").write_bytes(raw)
    r = reload(tmp_path)
    assert good in r.alerts and bad["alert_id"] not in r.alerts and r.rejected_on_load == 1
    assert json.loads(next((tmp_path / p).read_bytes() for p in _rejected_files(tmp_path)))["alerts"][-1]["alert_id"] == bad["alert_id"]   # the original, including the rejected row


def test_an_unparseable_or_foreign_store_is_preserved_before_the_product_starts_empty(tmp_path):
    import hashlib
    tmp_path.mkdir(exist_ok=True)
    for i, raw in enumerate((b"{not json", b'{"schema": "other", "alerts": []}')):
        d = tmp_path / str(i); d.mkdir()
        (d / "alerts.json").write_bytes(raw)
        r = reload(d)
        assert r.alerts == {} and r.preserved["complete"] is True and (d / f"alerts.rejected.{hashlib.sha256(raw).hexdigest()}.json").read_bytes() == raw


def test_preservation_never_follows_a_symlink_or_overwrites_an_earlier_record(tmp_path):
    import hashlib
    e, a = one(tmp_path)
    e._flush(True)
    doc = stored(tmp_path); doc["alerts"][0]["delivery_state"] = "delivered"
    raw = json.dumps(doc).encode()
    (tmp_path / "alerts.json").write_bytes(raw)
    h = hashlib.sha256(raw).hexdigest()
    victim = tmp_path.parent / "victim.txt"; victim.write_text("keep")
    (tmp_path / f"alerts.rejected.{h}.json").symlink_to(victim)                    # an attacker pre-creates the destination as a symlink
    r = reload(tmp_path)
    assert victim.read_text() == "keep"                                            # not followed, not written through
    assert r.preserved["complete"] is False                                        # and never claimed as complete preservation
    (tmp_path / f"alerts.rejected.{h}.json").unlink()
    (tmp_path / f"alerts.rejected.{h}.json").write_bytes(b"older, different bytes")  # an earlier record under the same name
    (tmp_path / "alerts.json").write_bytes(raw)
    r2 = reload(tmp_path)
    assert (tmp_path / f"alerts.rejected.{h}.json").read_bytes() == b"older, different bytes" and r2.preserved["complete"] is False


def test_restart_acknowledge_and_resolve_never_manufacture_delivered(tmp_path):
    e, a = one(tmp_path)
    e._flush(True)
    r = reload(tmp_path)
    (b,) = r.alerts.values()
    assert b["loaded_from_store"] and b["delivery_state"] == "pending"
    assert b["status"] == "resolved"                                               # resolved by the restart
    with pytest.raises(ValueError):
        r.report_delivery(b["alert_id"], "browser", "delivered")                   # a restored record can receive no new client outcome
    r.mark_seen([b["alert_id"]])
    assert b["delivery_state"] == "pending"                                        # browser/sound still pending: displaying cannot deliver
    assert b["delivery"]["browser"]["status"] == "pending" and b["delivery"]["sound"]["status"] == "pending"
    assert valid_alert(b) and b["delivery_state"] != "delivered"


def test_live_alert_is_still_reportable_and_summaries_agree(tmp_path):
    e, a = one(tmp_path)
    e.report_delivery(a["alert_id"], "browser", "delivered")
    e.report_delivery(a["alert_id"], "sound", "delivered")
    assert a["delivery_state"] == "delivered" and e.public(a)["delivery_state"] == "delivered"
    assert e.summary()["by_delivery_state"] == {"delivered": 1}
    r = reload(tmp_path)
    assert r.summary()["by_delivery_state"] == {} and r.summary()["loaded_from_disk_unverified"] == 1   # restored rows never count as deliveries made by this process


# ------------------------------------------------------------------ MG-EVENT-031
def _created(env):
    return {e["source_created_utc"] for e in env.c.get("/api/events").json()["events"]}


def test_review_counterexample_equal_size_edit_with_restored_mtime_and_inode(mk):
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run(env)
    first = env.c.get("/api/events").json()
    assert first["total"] >= 1
    p = env.runs / a / "observations.json"
    st = os.stat(p)
    raw = p.read_bytes()
    created = json.loads(raw)["meta"]["created_utc"]
    assert created and created.encode() in raw
    flipped = created[:-2] + ("00" if not created.endswith("00") else "11")
    new = raw.replace(created.encode(), flipped.encode(), 1)
    assert len(new) == len(raw) and new != raw
    with open(p, "r+b") as f:                                                       # in place: same inode
        f.write(new)
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    after = os.stat(p)
    assert (after.st_size, after.st_ino, after.st_mtime_ns) == (st.st_size, st.st_ino, st.st_mtime_ns)
    second = env.c.get("/api/events").json()
    assert second != first                                                          # not the stale rows
    assert created not in {e["source_created_utc"] for e in second["events"]}        # the old value is gone (the source reflects the new bytes, or is absent)


def test_removed_or_replaced_dependency_and_new_source_invalidate(mk):
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run(env)
    assert env.c.get("/api/events").json()["total"] >= 1
    (env.runs / a / "observations.json").rename(env.runs / a / "observations.json.bak")
    assert env.c.get("/api/events").json()["total"] == 0                            # source unreadable: absent, not stale


def test_repeated_requests_for_unchanged_data_are_identical_and_sources_stay_isolated(mk):
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run(env)
    one_ = env.c.get("/api/events").json()
    assert env.c.get("/api/events").json() == one_
    assert env.c.get(f"/api/events?source={a}").json()["events"] == one_["events"]
    assert env.c.get("/api/events?source=run_ffffffffffffffff").json()["events"] == []


def test_removing_or_symlinking_the_zone_set_invalidates_cached_events(mk, tmp_path):
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run(env)
    first = env.c.get("/api/events").json()
    assert first["total"] >= 1
    z = env.runs / a / "risk" / "zones.json"
    keep = tmp_path / "zones.keep"
    z.rename(keep)
    assert env.c.get("/api/events").json()["total"] == 0                            # no zone set: no events, never the cached ones
    z.symlink_to(keep)                                                             # a symlinked zone set is refused by the loader, not followed
    assert env.c.get("/api/events").json()["total"] == 0
    z.unlink()
    keep.rename(z)
    assert env.c.get("/api/events").json() == first                                # the original bytes restored: the original answer, recomputed


def test_two_sources_are_keyed_and_invalidated_independently(mk):
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    sid, a = run(env)
    twin = "session_aaaaaaaaaaaaaaaa"
    shutil.copytree(env.runs / a, env.runs / twin)                                  # a second, independent, test-owned source
    both = env.c.get("/api/events").json()
    ids = {e["source_id"] for e in both["events"]}
    assert ids == {a, twin}
    (env.runs / a / "observations.json").rename(env.runs / a / "observations.json.bak")
    after = env.c.get("/api/events").json()
    assert {e["source_id"] for e in after["events"]} == ids - {a}                  # only the damaged source disappears
    assert [e for e in after["events"]] == [e for e in both["events"] if e["source_id"] != a]


def test_a_source_that_changes_while_it_is_being_read_is_neither_cached_nor_shown(mk, monkeypatch):
    from mineguard import api_vs02
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run(env)
    p = env.runs / a / "observations.json"
    real = api_vs02.evaluate_dir

    def racing(*args, **kw):
        out = real(*args, **kw)
        raw = p.read_bytes()
        created = json.loads(raw)["meta"]["created_utc"].encode()
        p.write_bytes(raw.replace(created, created[:-3] + b"00Z" if not created.endswith(b"00Z") else created[:-3] + b"11Z", 1))   # equal size, different content, after the rows were derived
        return out

    monkeypatch.setattr(api_vs02, "evaluate_dir", racing)
    assert env.c.get("/api/events").json()["total"] == 0                            # an unstable read is absent from the listing, never shown as verified
    monkeypatch.setattr(api_vs02, "evaluate_dir", real)
    # MG-RISK-035 containment: the bytes now on disk disagree with export.json (only observations.json was rewritten), so the source stays refused instead of being re-published as a coherent run
    assert env.c.get("/api/events").json()["total"] == 0


def test_replacing_the_zone_set_by_a_symlink_to_identical_bytes_invalidates_at_once(mk, tmp_path):
    """Directly from the cached state (no request in between): a symlink whose target has the very same bytes must not hit the cache."""
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run(env)
    assert env.c.get("/api/events").json()["total"] >= 1
    z = env.runs / a / "risk" / "zones.json"
    twin = tmp_path / "zones.twin"
    twin.write_bytes(z.read_bytes())
    z.unlink()
    z.symlink_to(twin)
    assert env.c.get("/api/events").json()["total"] == 0                            # refused by the loader; the cached rows are not served


# ------------------------------------------------------------------ MG-EVENT-032 (cycle-32 review): ONE immutable input snapshot; derivation and cache identity come from the same bytes
try:
    from mineguard import snapshot  # noqa: E402
except ImportError:   # the checkpoint before MG-EVENT-032 has no snapshot module: its tests then FAIL at call time (fail-before), they do not break collection
    snapshot = None
from test_night_review1 import restart  # noqa: E402



@pytest.fixture
def snapshot_semantics_only(monkeypatch):
    """These tests change observations.json ALONE to exercise the snapshot/ABA/publication rules. Since MG-RISK-035 such a state is refused earlier by the component check, so it is neutralised here to keep
    testing the snapshot rule itself; the component check has its own tests in test_product_vertical06.py."""
    monkeypatch.setattr(api_vs02, "check_components", lambda *a, **k: None)


def _flip_created(raw: bytes) -> bytes:
    created = json.loads(raw)["meta"]["created_utc"].encode()
    new = created[:-3] + (b"00Z" if not created.endswith(b"00Z") else b"11Z")
    out = raw.replace(created, new, 1)
    assert len(out) == len(raw) and out != raw
    return out


def _events(env, **q):
    return env.c.get("/api/events", params=q).json()


def test_aba_transient_content_read_into_the_snapshot_is_refused_and_never_cached(mk, monkeypatch):
    """The review's counterexample: fabricated bytes are on disk while the inputs are read and the genuine bytes are back before the end. The snapshot holds the transient bytes, the disk the genuine ones."""
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run(env)
    d = env.runs / a
    p = d / "observations.json"
    genuine = p.read_bytes()
    real_take = snapshot.take
    calls = {"n": 0}

    def transient_take(dd):
        calls["n"] += 1
        s = real_take(dd)
        if calls["n"] == 1:                                                       # first read happens inside the attacker's window
            s.raw["observations.json"] = _flip_created(genuine)
        return s

    monkeypatch.setattr(snapshot, "take", transient_take)
    assert _events(env)["total"] == 0                                              # refused: the inputs moved (the transient state is not the stored one)
    monkeypatch.setattr(snapshot, "take", real_take)
    rows = _events(env)["events"]
    assert rows and {e["source_created_utc"] for e in rows} == {json.loads(genuine)["meta"]["created_utc"]}     # nothing derived from the transient bytes was cached


def test_changing_bytes_with_the_same_size_mtime_and_inode_changes_the_answer(mk):
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run(env)
    first = _events(env)
    p = env.runs / a / "observations.json"
    st = os.stat(p)
    with open(p, "r+b") as f:
        f.write(_flip_created(p.read_bytes()))
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    assert (os.stat(p).st_size, os.stat(p).st_ino, os.stat(p).st_mtime_ns) == (st.st_size, st.st_ino, st.st_mtime_ns)
    assert _events(env) != first


def test_atomic_replacement_by_rename_is_followed(mk):
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run(env)
    first = _events(env)
    p = env.runs / a / "observations.json"
    tmp = p.with_suffix(".new")
    tmp.write_bytes(_flip_created(p.read_bytes()))
    os.replace(tmp, p)                                                              # new inode, same size
    second = _events(env)
    assert second != first


def test_truncated_removed_added_and_symlinked_dependencies(mk, tmp_path):
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run(env)
    d = env.runs / a
    first = _events(env)
    assert first["total"] >= 1
    raw = (d / "observations.json").read_bytes()
    (d / "observations.json").write_bytes(raw[: len(raw) // 2])                    # truncated: unparseable -> the source is absent, never stale
    assert _events(env)["total"] == 0
    (d / "observations.json").write_bytes(raw)
    assert _events(env) == first                                                    # restored bytes: the identical answer, recomputed
    (d / "source.json").rename(tmp_path / "source.keep")                           # removed dependency
    assert _events(env)["total"] == 0
    (d / "source.json").symlink_to(tmp_path / "source.keep")                       # symlink substitution (same bytes)
    assert _events(env)["total"] == 0
    (d / "source.json").unlink()
    (tmp_path / "source.keep").rename(d / "source.json")
    assert _events(env) == first
    (d / "media_deleted.json").write_text("{}")                                    # NEW dependency: the tombstone changes the answer's evidence status, so the key must change
    key_a = snapshot.take(d).key
    (d / "media_deleted.json").unlink()
    assert snapshot.take(d).key != key_a


def test_a_source_with_similar_metadata_is_a_different_source(snapshot_semantics_only, mk):
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run(env)
    twin = env.runs / "session_bbbbbbbbbbbbbbbb"
    shutil.copytree(env.runs / a, twin, copy_function=shutil.copy2)                 # same sizes and mtimes
    p = twin / "observations.json"
    st = os.stat(p)
    p.write_bytes(_flip_created(p.read_bytes()))
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    by = {}
    for e in _events(env)["events"]:
        by.setdefault(e["source_id"], set()).add(e["source_created_utc"])
    assert set(by) == {a, twin.name} and by[a] != by[twin.name]


def test_stable_input_is_identical_across_requests_and_a_restart(mk):
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run(env)
    one_ = _events(env)
    assert _events(env) == one_
    fresh = restart(env)
    again = fresh.get("/api/events").json()
    assert [{k: v for k, v in e.items() if k not in ("trust", "nature")} for e in again["events"]] == [{k: v for k, v in e.items() if k not in ("trust", "nature")} for e in one_["events"]]


def test_an_oversized_dependency_is_refused_not_read(mk, monkeypatch):
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run(env)
    monkeypatch.setattr(snapshot, "MAX_FILE_BYTES", 100)
    assert _events(env)["total"] == 0


def test_aba_swap_during_evaluation_that_restores_the_original_bytes_derives_nothing_from_the_transient_content(mk, monkeypatch):
    """Independent of the implementation: while evaluate_dir runs, the attacker installs fabricated observations and restores the genuine bytes before it returns."""
    from mineguard import api_vs02
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run(env)
    p = env.runs / a / "observations.json"
    genuine = p.read_bytes()
    assert b'"person"' in genuine
    first = _events(env)
    assert first["total"] >= 1
    env.app.state  # noqa: B018  (the cache is per app instance: build the transient case on a fresh app)
    env2 = restart(env)
    real = api_vs02.evaluate_dir

    def attacked(*args, **kw):
        p.write_bytes(genuine.replace(b'"person"', b'"xerson"'))                       # transient fabricated bytes (no person), same size
        try:
            return real(*args, **kw)
        finally:
            p.write_bytes(genuine)                                                  # restored before the caller's second look

    monkeypatch.setattr(api_vs02, "evaluate_dir", attacked)
    env2.get("/api/events")                                                          # may be refused or genuine; it must never leave transient-derived rows behind
    monkeypatch.setattr(api_vs02, "evaluate_dir", real)
    again = env2.get("/api/events").json()
    assert again["total"] == first["total"] and [e["event_id"] for e in again["events"]] == [e["event_id"] for e in first["events"]]


# ------------------------------------------------------------------ MG-ALERT-032 (cycle-32 review): an identical terminal delivery report is idempotent
def test_identical_terminal_report_changes_nothing(tmp_path):
    clock = Clock()
    e = AlertEngine(tmp_path, wall=clock, mono=Clock())
    s = copy.deepcopy(e.settings); s["red"].update(browser=True, sound=True); e.set_settings(s)
    e.observe_source(src(), [evt("evt_1", "RED")], {})
    (a,) = e.alerts.values()
    e.report_delivery(a["alert_id"], "browser", "delivered", "first")
    before = copy.deepcopy(a)
    clock.t += 10 if hasattr(clock, "t") else 0
    e.report_delivery(a["alert_id"], "browser", "delivered", "replacement")
    assert a == before                                                             # timestamp, detail, revision, ids: all untouched
    assert e.rev == before["rev"]


def test_conflicting_and_restored_reports_are_refused_and_distinct_channels_stay_independent(tmp_path):
    e, a = one(tmp_path)
    e.report_delivery(a["alert_id"], "browser", "denied", "perm")
    snap_ = copy.deepcopy(a)
    for st in ("delivered", "failed", "unsupported"):
        with pytest.raises(ValueError):
            e.report_delivery(a["alert_id"], "browser", st)                        # a denied outcome cannot be rewritten
    assert a == snap_
    e.report_delivery(a["alert_id"], "sound", "delivered")
    assert a["delivery"]["browser"]["status"] == "denied" and a["delivery"]["sound"]["status"] == "delivered" and a["delivery_state"] == "delivered"
    before = copy.deepcopy(a)
    e.report_delivery(a["alert_id"], "sound", "delivered")
    assert a == before
    e._flush(True)
    r = reload(tmp_path)
    (b,) = r.alerts.values()
    with pytest.raises(ValueError):
        r.report_delivery(b["alert_id"], "sound", "delivered")                     # restored: even an identical report is refused


def test_a_pending_channel_still_accepts_its_first_report(tmp_path):
    e, a = one(tmp_path)
    assert a["delivery"]["browser"]["status"] == "pending"
    e.report_delivery(a["alert_id"], "browser", "failed", "x")
    assert a["delivery"]["browser"]["status"] == "failed" and a["delivery"]["browser"]["at"] is not None and a["delivery_state"] == "failed"


# ------------------------------------------------------------------ MG-WCAM-032 (cycle-32 review): exact sample-log and accounting validation
from mineguard import session_docs as sd  # noqa: E402
from test_vs02_webcam_risk import _rewrite, run_session  # noqa: E402

SHA = "a" * 64


def smp(seq, status="analyzed", reason=None, t=0, sha=SHA):
    return {"seq": seq, "capture_us": t, "status": status, "reason": reason, "jpeg_sha256": sha}


def stub(t, lost=2, pos="gap", rej=0, unc=0):
    return {"seq": None, "capture_us": t, "status": "not_analyzed_dropped", "reason": "client_loss", "jpeg_sha256": None, "lost_frames": lost, "rejected_frames": rej, "unclassified_frames": unc, "loss_position": pos}


def acct(samples, **over):
    base = sd.accounting_from_samples(samples)
    a = {"submitted": base["accepted"], "accepted": base["accepted"], "analyzed": base["analyzed"], "dropped": base["dropped"], "adapter_error": base["adapter_error"],
         "never_observed_frames": base["never_observed_frames"] - base["rejected_invalid_frames"] - base["unclassified_frames"], "prefix_frames": base["prefix_frames"], "gap_frames": base["gap_frames"],
         "tail_frames": base["tail_frames"], "unknown_intervals": base["unknown_intervals"], "rejected_invalid_frames": base["rejected_invalid_frames"], "invalid_requests": 0,
         "unclassified_frames": base["unclassified_frames"], "captured_declared": None, "origin_basis": "measured", "tail_basis": "none_right_censored", "tail_exact": False, "closure": "clean_stop"}
    a.update(over)
    return {"accounting": a, "intake_rejected": 0}


GOOD = [smp(0, t=0), smp(1, "not_analyzed_dropped", "queue_overflow", t=100, sha=None), stub(150), smp(2, "adapter_error", "RuntimeError", t=200, sha=None), smp(3, "not_analyzed_dropped", "stale", t=300, sha=None), smp(4, "not_analyzed_dropped", "session_stopped", t=400, sha=None)]


def test_a_legitimate_sample_log_is_valid_and_its_accounting_is_consistent():
    assert sd.samples_valid(GOOD) and sd.samples_valid([])
    assert sd.accounting_consistent(acct(GOOD), GOOD)


def test_review_counterexample_unknown_reason_with_accounting_that_discards_the_frame():
    bad = [smp(0, "not_analyzed_dropped", "mystery")]
    assert not sd.samples_valid(bad)
    assert not sd.accounting_consistent({"accounting": {**acct(bad)["accounting"], "accepted": 1, "analyzed": 0, "dropped": 0, "adapter_error": 0}, "intake_rejected": 0}, bad)


@pytest.mark.parametrize("mutate", [
    lambda s: s[1].update(reason="mystery"),                         # unknown reason
    lambda s: s[0].update(status="pending"),                         # status that is never final
    lambda s: s[0].update(reason="stale"),                           # analyzed with a reason
    lambda s: s[1].update(reason=None),                              # dropped without one
    lambda s: s[3].update(reason="bad reason!"),                     # adapter error that is not an exception class
    lambda s: s[0].update(seq=-1),
    lambda s: s[0].update(seq=True),
    lambda s: s[1].update(seq=0),                                    # duplicate identity
    lambda s: s[3].update(seq=1),                                    # out of order
    lambda s: s[0].update(capture_us=-5),
    lambda s: s[3].update(capture_us=50),                            # time going backwards
    lambda s: s[0].update(jpeg_sha256="xyz"),
    lambda s: s[0].pop("jpeg_sha256"),                               # missing member
    lambda s: s[0].update(extra=1),                                  # unexpected member
    lambda s: s[2].update(seq=7),                                    # stub with a sequence
    lambda s: s[2].update(lost_frames=0),
    lambda s: s[2].update(lost_frames=-3),
    lambda s: s[2].update(loss_position="middle"),
    lambda s: s[2].update(jpeg_sha256=SHA),                          # stub with an image
    lambda s: s[2].update(status="analyzed"),
    lambda s: s[2].pop("unclassified_frames"),
])
def test_malformed_or_contradictory_samples_are_refused(mutate):
    s = copy.deepcopy(GOOD)
    mutate(s)
    assert not sd.samples_valid(s)
    assert not sd.accounting_consistent(acct(GOOD), s)


@pytest.mark.parametrize("over", [{"accepted": 7}, {"accepted": 4, "submitted": 5}, {"analyzed": -1}, {"dropped": "2"}, {"adapter_error": 1.5}, {"prefix_frames": 3}, {"unknown_intervals": 2}])
def test_accounting_that_is_not_exactly_what_the_log_proves_is_refused(over):
    assert not sd.accounting_consistent(acct(GOOD, **over), GOOD)


def test_accepted_larger_than_the_sum_of_the_outcome_categories_is_refused():
    assert not sd.accounting_consistent(acct(GOOD, accepted=6, submitted=6), GOOD)           # the old `>=` rule accepted an unaccounted frame


def test_a_stored_session_verifies_and_a_coordinated_unknown_reason_is_caught_at_both_checks(mk):
    env = mk(scene(person=range(3, 20)))
    _, run = run_session(env, 24)
    ids = {c["id"]: c["ok"] for c in env.c.get(f"/api/sessions/{run}/verify").json()["checks"]}
    assert ids["sample_log_shape_is_exact"] and ids["coverage_accounting_matches_the_sample_log"]       # positive control
    def edit(src_doc):
        i = next(k for k, x in enumerate(src_doc["samples"]) if x["status"] == "analyzed")
        src_doc["samples"][i].update(status="not_analyzed_dropped", reason="mystery")
    _rewrite(env, run, "source", edit)
    rep = env.c.get(f"/api/sessions/{run}/verify?deep=false").json()
    got = {c["id"]: c["ok"] for c in rep["checks"]}
    assert not got["sample_log_shape_is_exact"] and not got["coverage_accounting_matches_the_sample_log"] and not rep["ok"]
    assert env.c.get(f"/api/sessions/{run}").json()["trust"] == "unverified"


def test_privacy_on_metadata_only_and_empty_capture_remain_valid(mk):
    from test_privacy import run as run_default
    env = mk(scene(person=range(3, 12)))
    _, run = run_default(env, 8)                                                     # Privacy ON by default: metadata only, no images
    ids = {c["id"]: c["ok"] for c in env.c.get(f"/api/sessions/{run}/verify").json()["checks"]}
    assert ids["sample_log_shape_is_exact"] and ids["coverage_accounting_matches_the_sample_log"]
    assert sd.samples_valid([])                                                        # an empty / interrupted capture has an empty log: valid, with nothing to account


# ------------------------------------------------------------------ cross-cutting matrix: the SAME event through every representation (MG-EVENT-015, MG-EVAL-012, MG-VERTICAL2-001 alert contract)
def test_one_event_is_the_same_in_the_events_list_the_risk_evaluation_an_alert_and_after_restoration(mk, tmp_path):
    from test_vs02_webcam_risk import ev_of
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run_session(env, 34)
    listed = {(e["zone_id"], e["event_id"]): e for e in _events(env)["events"] if e["source_id"] == a}
    evaluated = {(z, e["event_id"]): e for z, r in ev_of(env, a)["evaluation"]["zones"].items() for e in r["events"]}
    assert listed and set(listed) == set(evaluated)
    for k, e in listed.items():                                                      # the representations agree field by field
        assert all(e[f] == evaluated[k][f] for f in ("severity", "start_us", "end_us", "closed_reason", "rule_id", "left_censored", "right_censored"))
    # the same events feed the alert engine; the alert identity is bound to (source, zone, event) and a restored copy is history, never delivery
    al = AlertEngine(tmp_path, wall=Clock(), mono=Clock())
    s_ = copy.deepcopy(al.settings); s_["red"].update(browser=True, sound=True); al.set_settings(s_)
    kernel_events = [{"event_id": e["event_id"], "severity": e["severity"], "start_us": e["start_us"], "end_us": e["end_us"], "open": False, "tracks": [], "closed_reason": e["closed_reason"],
                      "right_censored": e["right_censored"], "rule_id": e["rule_id"], "zone_id": e["zone_id"]} for e in listed.values()]
    al.observe_source(src(), [x for x in kernel_events if x["severity"] == "RED"], {})
    assert all(v["delivery_state"] == "pending" for v in al.alerts.values())
    al._flush(True)
    restored = reload(tmp_path)
    assert all(v["loaded_from_store"] and v["delivery_state"] == "pending" and v["status"] == "resolved" for v in restored.alerts.values())
    assert restored.summary()["by_delivery_state"] == {}                              # restored rows never count as deliveries


def test_trust_is_computed_per_request_and_never_taken_from_the_event_cache(mk):
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run_session(env, 34)
    assert {e["nature"] for e in _events(env)["events"] if e["source_id"] == a} == {"real_inference"}
    fresh = restart(env)                                                             # the grant belongs to the first process
    assert {e["nature"] for e in fresh.get("/api/events").json()["events"] if e["source_id"] == a} == {"unverified_claim"}
    assert {e["nature"] for e in _events(env)["events"] if e["source_id"] == a} == {"real_inference"}   # isolated per app instance: no cross-contamination


def test_the_event_cache_hands_out_and_stores_copies_only():
    c = snapshot.EventCache()
    v = {"events": [{"event_id": "e1"}]}
    c.put("s", ("k",), v)
    v["events"][0]["event_id"] = "mutated-after-put"                                # the caller's own object is not the cached one
    got = c.get("s", ("k",))
    assert got == {"events": [{"event_id": "e1"}]}
    got["events"].append({"event_id": "x"})                                          # a consumer cannot alter what is cached
    assert c.get("s", ("k",)) == {"events": [{"event_id": "e1"}]}
    assert c.get("s", ("other",)) is None and c.get("t", ("k",)) is None


def test_defence_in_depth_accounting_equality_holds_even_if_the_shape_check_were_bypassed(monkeypatch):
    bad = [smp(0, "not_analyzed_dropped", "mystery")]
    monkeypatch.setattr(sd, "samples_valid", lambda s, r=None: True)
    assert not sd.accounting_consistent(acct(bad), bad)                              # accepted (1) != analyzed + dropped + adapter_error (0)


def test_finalization_refuses_to_store_a_sample_log_that_verification_would_refuse(mk, monkeypatch):
    from mineguard import webcam
    from test_vs02_webcam_risk import start, post, wait_decided
    env = mk(scene(person=range(3, 12)))
    sid = start(env.c).json()["session_id"]
    for i in range(4):
        post(env.c, sid, i); wait_decided(env.c, sid, i + 1)
    monkeypatch.setattr(sd, "samples_valid", lambda s, r=None: False)
    r = env.c.post(f"/api/webcam/sessions/{sid}/stop")
    assert r.status_code != 200 or r.json().get("state") != "done"
    assert not [p for p in env.runs.iterdir() if p.name.startswith("session_")]      # nothing unverifiable was stored


# ------------------------------------------------------------------ MG-RISK-033 / MG-EVENT-033 / MG-WCAM-033 / MG-WCAM-034 (cycle-33 review): ONE snapshot policy for every reader; format-bound sessions
import threading  # noqa: E402
from concurrent.futures import ThreadPoolExecutor  # noqa: E402
from mineguard import api_vs02  # noqa: E402


def _person_variant(raw: bytes) -> bytes:
    assert b'"person"' in raw
    return raw.replace(b'"person"', b'"xerson"')


def _session(mk):
    env = mk(scene(person=range(3, 32), vehicle=range(15, 32)))
    _, a = run_session(env, 34)
    return env, a


def _risk(env, a):
    return env.c.get(f"/api/sessions/{a}/risk")


def _export(env, a):
    return env.c.get(f"/api/sessions/{a}/risk/export")


def test_stable_evidence_gives_identical_events_risk_and_export_and_the_export_equals_the_displayed_risk(mk):
    env, a = _session(mk)
    r1, r2 = _risk(env, a).json(), _risk(env, a).json()
    assert r1 == r2 and r1["trust"] in ("session", "verified")
    x = _export(env, a).json()
    assert x["evaluation"] == r1["evaluation"] and x["zones"] == r1["zones"]                           # export and display: one revision
    assert x["bundle"] == {**json.loads((env.runs / a / "export.json").read_text()), "retention": x["bundle"]["retention"], "evidence_available": x["bundle"]["evidence_available"]}
    assert _events(env) == _events(env)


def test_overview_events_and_risk_agree_on_the_same_events(mk):
    env, a = _session(mk)
    listed = [e for e in _events(env)["events"] if e["source_id"] == a]
    ov = env.c.get("/api/overview").json()["events"]["real"]
    assert ov["RED"] == sum(e["severity"] == "RED" for e in listed) and ov["YELLOW"] == sum(e["severity"] == "YELLOW" for e in listed)
    assert {e["event_id"] for e in listed} == {e["event_id"] for r in _risk(env, a).json()["evaluation"]["zones"].values() for e in r["events"]}


def test_same_size_in_place_mutation_is_reflected_by_risk_and_export(snapshot_semantics_only, mk):
    env, a = _session(mk)
    base = _risk(env, a).json()
    p = env.runs / a / "observations.json"
    st = os.stat(p)
    with open(p, "r+b") as f:
        f.write(_person_variant(p.read_bytes()))
    os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    after = _risk(env, a)
    assert after.status_code == 200 and after.json()["evaluation"] != base["evaluation"] and after.json()["trust"] == "unverified"
    assert _export(env, a).json()["evaluation"] == after.json()["evaluation"]


@pytest.mark.parametrize("route", ["risk", "export", "events", "zones", "bundle"])
def test_aba_during_derivation_never_publishes_transient_content(mk, monkeypatch, route):
    """The reviewer's counterexample for every reader: fabricated, event-affecting bytes are on disk while the response is being derived and the genuine bytes are back before it ends."""
    env, a = _session(mk)
    p = env.runs / a / "observations.json"
    genuine = p.read_bytes()
    url = {"risk": f"/api/sessions/{a}/risk", "export": f"/api/sessions/{a}/risk/export", "events": "/api/events", "zones": f"/api/sessions/{a}/zones", "bundle": f"/api/sessions/{a}"}[route]
    first = env.c.get(url)
    assert first.status_code == 200
    base = first.json()
    real = api_vs02.evaluate_dir

    def attacked(*args, **kw):
        p.write_bytes(_person_variant(genuine))
        try:
            return real(*args, **kw)
        finally:
            p.write_bytes(genuine)

    monkeypatch.setattr(api_vs02, "evaluate_dir", attacked)
    fresh = restart(env)                                                                # a cold cache for the events route
    r = fresh.get(url)
    monkeypatch.setattr(api_vs02, "evaluate_dir", real)
    if r.status_code == 200 and route in ("risk", "export"):
        assert r.json()["evaluation"] == base["evaluation"]                              # either refused or exactly the genuine revision
    if route == "events" and r.status_code == 200:
        assert r.json()["total"] in (0, base["total"]) and (r.json()["total"] == 0 or [e["event_id"] for e in r.json()["events"]] == [e["event_id"] for e in base["events"]])
    again = fresh.get(url)
    assert again.status_code == 200
    if route in ("risk", "export"):
        assert again.json()["evaluation"] == base["evaluation"]


@pytest.mark.parametrize("route", ["risk", "export"])
def test_transient_bytes_read_into_the_snapshot_are_refused(snapshot_semantics_only, mk, monkeypatch, route):
    env, a = _session(mk)
    p = env.runs / a / "observations.json"
    genuine = p.read_bytes()
    real_take = snapshot.take
    n = {"i": 0}

    def transient(dd):
        n["i"] += 1
        s = real_take(dd)
        if n["i"] == 1:
            s.raw["observations.json"] = _person_variant(genuine)
        return s

    monkeypatch.setattr(snapshot, "take", transient)
    url = f"/api/sessions/{a}/risk" + ("/export" if route == "export" else "")
    r = env.c.get(url)
    assert r.status_code == 409 and r.json()["detail"]["msg_id"] == "mg.refusal.source_changed_while_read"


def test_aba_on_a_cache_hit_is_refused_not_served_stale(mk, monkeypatch):
    env, a = _session(mk)
    populated = _events(env)
    assert populated["total"] >= 1                                                      # the cache is populated
    p = env.runs / a / "observations.json"
    genuine = p.read_bytes()
    real_get = snapshot.EventCache.get

    def changing_get(self, name, key):
        out = real_get(self, name, key)
        p.write_bytes(_person_variant(genuine))                                         # the source changes AFTER the snapshot and before the hit is published
        return out

    monkeypatch.setattr(snapshot.EventCache, "get", changing_get)
    stale = _events(env)
    monkeypatch.setattr(snapshot.EventCache, "get", real_get)
    assert all(e["source_id"] != a or e["trust"] == "unverified" for e in stale["events"]) and stale["total"] == 0     # refused: the stale rows were NOT served
    p.write_bytes(genuine)
    assert _events(env)["total"] == populated["total"]                                   # restored bytes: the genuine answer again


def test_atomic_replacement_removed_and_new_dependencies_for_risk(snapshot_semantics_only, mk, tmp_path):
    env, a = _session(mk)
    d = env.runs / a
    base = _risk(env, a).json()
    raw = (d / "observations.json").read_bytes()
    new = d / "observations.json.new"
    new.write_bytes(_person_variant(raw))
    os.replace(new, d / "observations.json")                                            # atomic replacement, same size
    assert _risk(env, a).json()["evaluation"] != base["evaluation"]
    (d / "observations.json").write_bytes(raw)
    assert _risk(env, a).json()["evaluation"] == base["evaluation"]
    (d / "source.json").rename(tmp_path / "source.keep")                                # removed dependency
    assert _risk(env, a).status_code >= 400 and _export(env, a).status_code >= 400
    (tmp_path / "source.keep").rename(d / "source.json")
    (d / "media_deleted.json").write_text("{}")                                         # NEW dependency: the evaluation's evidence status changes
    assert _risk(env, a).json()["evaluation"].get("evidence_status") == "media_deleted"
    (d / "media_deleted.json").unlink()
    assert "evidence_status" not in _risk(env, a).json()["evaluation"]


def test_symlinked_dependencies_are_refused_by_every_route(mk, tmp_path):
    env, a = _session(mk)
    d = env.runs / a
    twin = tmp_path / "zones.twin"
    twin.write_bytes((d / "risk" / "zones.json").read_bytes())
    (d / "risk" / "zones.json").unlink()
    (d / "risk" / "zones.json").symlink_to(twin)
    assert env.c.get(f"/api/sessions/{a}/zones").status_code == 409 and _risk(env, a).status_code == 409 and _export(env, a).status_code == 409
    (d / "risk" / "zones.json").unlink()
    (d / "risk" / "zones.json").write_bytes(twin.read_bytes())
    obs = d / "observations.json"
    keep = tmp_path / "obs.keep"
    keep.write_bytes(obs.read_bytes())
    obs.unlink()
    obs.symlink_to(keep)
    assert _risk(env, a).status_code >= 400 and _events(env)["total"] == 0


def test_concurrent_events_and_risk_requests_never_return_a_mixed_revision(snapshot_semantics_only, mk):
    env, a = _session(mk)
    p = env.runs / a / "observations.json"
    genuine = p.read_bytes()
    variant = _person_variant(genuine)
    base_ev = _risk(env, a).json()["evaluation"]
    p.write_bytes(variant)
    var_ev = _risk(env, a).json()["evaluation"]
    p.write_bytes(genuine)
    assert base_ev != var_ev
    stop = threading.Event()

    def writer():
        k = 0
        while not stop.is_set():
            p.write_bytes(variant if k % 2 else genuine)
            k += 1

    def reader(i):
        out = []
        for _ in range(12):
            r = _risk(env, a) if i % 2 else _export(env, a)
            if r.status_code == 200:
                j = r.json()
                out.append(j["evaluation"])
            else:
                assert r.status_code == 409                                              # a torn or moved read is refused, never published
        return out

    t = threading.Thread(target=writer); t.start()
    try:
        with ThreadPoolExecutor(4) as ex:
            results = [x for rs in ex.map(reader, range(4)) for x in rs]
    finally:
        stop.set(); t.join()
    assert all(e == base_ev or e == var_ev for e in results)                              # every published evaluation is exactly one whole revision


# -------- webcam format and retention semantics
def test_the_format_marker_is_part_of_the_digest_bound_identity():
    base = dict(samples=[], capture={}, closed_reason="client_stop", live_stats={}, retention={"mode": "frames", "frames_persisted": True}, provenance_sha256="a" * 64)
    from mineguard.canon import digest
    cur = digest(sd.source_digest_payload(**base, session_format=sd.SESSION_FORMAT))
    old = digest(sd.source_digest_payload(**base))
    assert cur != old and "session_format" not in sd.source_digest_payload(**base)        # sessions stored before the marker keep their original identity


def test_a_new_session_carries_the_marker_and_verifies_with_the_current_format(mk):
    env, a = _session(mk)
    src_ = json.loads((env.runs / a / "source.json").read_text())
    assert src_["session_format"] == sd.SESSION_FORMAT and "accounting" in src_["live_stats"]
    ids = {c["id"]: c["ok"] for c in env.c.get(f"/api/sessions/{a}/verify").json()["checks"]}
    assert ids["session_format_is_current"] and ids["sample_log_shape_is_exact"] and ids["coverage_accounting_matches_the_sample_log"]


def test_downgrade_to_a_legacy_shape_is_reported_as_unavailable_and_unverified(mk):
    env, a = _session(mk)
    def edit(src_doc):
        src_doc.pop("session_format")
        src_doc["live_stats"].pop("accounting")
    _rewrite(env, a, "source", edit)
    rep = env.c.get(f"/api/sessions/{a}/verify?deep=false").json()
    c = {x["id"]: x for x in rep["checks"]}
    assert not c["session_format_is_current"]["ok"] and not c["coverage_accounting_matches_the_sample_log"]["ok"] and not rep["ok"]
    assert "unavailable" in c["coverage_accounting_matches_the_sample_log"]["detail"]
    assert env.c.get(f"/api/sessions/{a}").json()["trust"] == "unverified"


def test_current_format_without_complete_accounting_is_refused_while_legacy_shapes_stay_supported():
    s_ = [smp(0, t=0)]
    good = acct(s_)
    full = {**{k: None for k in sd.STATS_KEYS}, **good}                                                 # a complete current live_stats: every STATS key + accounting
    assert sd.accounting_consistent(full, s_, current=True)
    assert not sd.accounting_consistent({"latency_ms": None}, s_, current=True)                       # accounting absent in a current session
    older = {"accounting": {k: v for k, v in good["accounting"].items() if k not in ("rejected_invalid_frames", "invalid_requests", "unclassified_frames")}, "intake_rejected": 0}
    assert sd.accounting_consistent(older, s_) and not sd.accounting_consistent(older, s_, current=True)   # genuine older shape: supported only WITHOUT the current claim


@pytest.mark.parametrize("mode,mutate,ok", [
    ("frames", lambda s: None, True),
    ("frames", lambda s: s[1].update(jpeg_sha256=SHA), False),                          # a dropped frame never has an image hash
    ("frames", lambda s: s[3].update(jpeg_sha256=SHA), False),                          # nor an adapter error
    ("frames", lambda s: s[0].update(jpeg_sha256=None), False),                         # an analyzed Privacy-OFF frame always has one
    ("frames", lambda s: s[2].update(jpeg_sha256=SHA), False),                          # nor a lost-frames stub
    ("metadata_only", lambda s: s[0].update(jpeg_sha256=None), True),
    ("metadata_only", lambda s: s[0].update(jpeg_sha256=SHA), False),                                           # Privacy ON never acquires pixel evidence because a field was supplied
])
def test_image_hashes_exist_only_where_the_producer_writes_them(mode, mutate, ok):
    s_ = copy.deepcopy(GOOD)
    if mode == "metadata_only":
        for x in s_:
            x["jpeg_sha256"] = None
    mutate(s_)
    s_[0]["jpeg_sha256"] = s_[0]["jpeg_sha256"]
    assert sd.samples_valid(s_, mode) is ok


def test_privacy_on_session_has_no_hashes_and_a_supplied_hash_is_refused(mk):
    from test_privacy import run as run_default
    env = mk(scene(person=range(3, 12)))
    _, a = run_default(env, 8)
    src_ = json.loads((env.runs / a / "source.json").read_text())
    assert src_["retention"]["mode"] == "metadata_only" and all(x["jpeg_sha256"] is None for x in src_["samples"])
    ids = {c["id"]: c["ok"] for c in env.c.get(f"/api/sessions/{a}/verify").json()["checks"]}
    assert ids["session_format_is_current"] and ids["sample_log_shape_is_exact"] and ids["coverage_accounting_matches_the_sample_log"]
    _rewrite(env, a, "source", lambda d_: d_["samples"][0].update(jpeg_sha256=SHA))
    got = {c["id"]: c["ok"] for c in env.c.get(f"/api/sessions/{a}/verify?deep=false").json()["checks"]}
    assert not got["sample_log_shape_is_exact"]


@pytest.mark.parametrize("route", ["risk", "export", "zones", "bundle"])
def test_these_routes_read_the_stored_source_only_through_the_snapshot(mk, monkeypatch, route):
    """Poison the path-based reader: a response that still reads a mutable path after the snapshot (the MG-RISK-033 defect class) would carry the poison or fail."""
    env, a = _session(mk)
    url = {"risk": f"/api/sessions/{a}/risk", "export": f"/api/sessions/{a}/risk/export", "zones": f"/api/sessions/{a}/zones", "bundle": f"/api/sessions/{a}"}[route]
    ok = env.c.get(url)
    assert ok.status_code == 200
    calls = []

    def poisoned(path, *_a, **_k):
        calls.append(str(path))
        raise AssertionError(f"mutable path read after the snapshot: {path}")

    monkeypatch.setattr(api_vs02, "read_json_strict", poisoned)
    again = env.c.get(url)
    assert again.status_code == 200 and not [c for c in calls if not c.endswith("late_declaration.json") and "late" not in c]
    assert again.json() == ok.json()
