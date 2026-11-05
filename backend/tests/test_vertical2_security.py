"""MG-VERTICAL-02 review A (cycle 21) MG-PATH-021 / MG-API-021: the new storage and the new JSON endpoints. Synthetic, isolated; the 'external' directories are test-owned decoys."""
import json
import os
import re
import time

import pytest

from mineguard import safedir, webcam
from mineguard.alerts import AlertEngine
from test_vertical2_alerts import evt, src  # noqa: F401
from test_vertical2_feeds import ZONE, act, always, create, fm, view, wait_state  # noqa: F401
from test_vertical2_wcam import bundle, disconnect, mk, sess  # noqa: F401
from test_vs02_webcam_risk import scene  # noqa: F401

HEX16 = "0123456789abcdef"


def decoy(tmp_path, names=(f"feedrun_{HEX16}.json",)):
    d = tmp_path / "external"
    d.mkdir()
    for n in names:
        (d / n).write_text(json.dumps({"schema": "mg.feedrun.v1", "keep": True}))
    return d


# ------------------------------------------------------------------ MG-PATH-021
def test_a_symlinked_feeds_directory_is_never_followed_for_listing_or_deletion(fm):
    env = fm({1: always()})
    ext = decoy(env.tmp)
    env.runs.mkdir(parents=True, exist_ok=True)
    os.symlink(ext, env.runs / "feeds")
    h = env.c.get("/api/feeds/history").json()
    assert h["store_status"] == "unsafe" and h["runs"] == []
    r = env.c.post("/api/feeds/history/delete", json={"ids": [f"feedrun_{HEX16}"], "confirm": "ELIMINAR", "expected_count": 1})
    assert r.status_code == 409 and r.json()["detail"]["msg_id"] == "mg.refusal.store_not_owned"
    assert (ext / f"feedrun_{HEX16}.json").exists()                                           # the external file survived


def test_a_feed_never_writes_its_summary_through_a_symlinked_directory(fm):
    env = fm({1: always()}, frames=40)
    ext = decoy(env.tmp, names=())
    env.runs.mkdir(parents=True, exist_ok=True)
    os.symlink(ext, env.runs / "feeds")
    a = create(env, 1)
    act(env, a, "start")
    (v,) = wait_state(env, [a], ("ended",))
    assert v["error"] == "summary_not_persisted" and v["result_id"] is None
    assert list(ext.iterdir()) == []                                                          # nothing was created outside the owned storage


def test_a_symlinked_alerts_directory_is_neither_read_nor_written(tmp_path):
    ext = tmp_path / "external"
    ext.mkdir()
    forged = {"schema": "mg.alerts.store.v1", "rev": 9, "alerts": []}
    (ext / "alerts.json").write_text(json.dumps(forged))
    (ext / "settings.json").write_text("not settings")
    root = tmp_path / "runs"
    root.mkdir()
    os.symlink(ext, root / "alerts")
    e = AlertEngine(root / "alerts")
    assert e.store_unsafe is True and e.rev == 0
    e.observe_source(src(), [evt("e1", "RED")], {})
    e.flush()
    e.set_settings(json.loads(json.dumps(e.settings)))
    assert json.loads((ext / "alerts.json").read_text()) == forged and (ext / "settings.json").read_text() == "not settings"
    assert sorted(p.name for p in ext.iterdir()) == ["alerts.json", "settings.json"]         # no temp file, no new file
    assert e.summary()["store_status"] == "unsafe_not_persisted" and len(e.alerts) == 1        # the alert still works in memory


def test_pinning_the_directory_defeats_a_swap_between_the_check_and_the_unlink(tmp_path):
    owned = tmp_path / "feeds"
    owned.mkdir()
    (owned / "a.json").write_text("{}")
    ext = decoy(tmp_path, names=("a.json",))
    fd = safedir.open_owned_dir(tmp_path, "feeds", create=False)
    try:
        os.rename(owned, tmp_path / "feeds_moved")                                             # the attacker swaps the directory for a symlink AFTER it was opened
        os.symlink(ext, tmp_path / "feeds")
        safedir.unlink_regular(fd, "a.json")
    finally:
        os.close(fd)
    assert not (tmp_path / "feeds_moved" / "a.json").exists() and (ext / "a.json").exists()    # the operation landed in the verified directory, not in the swapped one


def test_safedir_refuses_symlink_leaves_directories_and_odd_names(tmp_path):
    d = tmp_path / "store"
    d.mkdir()
    ext = tmp_path / "outside.txt"
    ext.write_text("secret")
    os.symlink(ext, d / "link.json")
    (d / "subdir.json").mkdir()
    fd = safedir.open_owned_dir(tmp_path, "store", create=False)
    try:
        with pytest.raises(OSError):
            safedir.read_regular(fd, "link.json")                                              # a symlink leaf is not followed
        with pytest.raises(safedir.UnsafeDir):
            safedir.unlink_regular(fd, "link.json")
        with pytest.raises(safedir.UnsafeDir):
            safedir.unlink_regular(fd, "subdir.json")
        for bad in ("../x", "a/b", "", ".", "..", "x\x00y"):
            with pytest.raises(safedir.UnsafeDir):
                safedir.write_atomic(fd, bad, "x")
        safedir.write_atomic(fd, "link.json", "replaced")                                      # writing replaces the LINK itself, never its target
    finally:
        os.close(fd)
    assert ext.read_text() == "secret" and not os.path.islink(d / "link.json")
    with pytest.raises(safedir.UnsafeDir):
        safedir.open_owned_dir(tmp_path, "../etc")


def test_history_entries_that_are_not_regular_files_are_refused(fm):
    env = fm({1: always()})
    feeds = env.runs / "feeds"
    feeds.mkdir(parents=True)
    (feeds / f"feedrun_{HEX16}.json").mkdir()
    r = env.c.post("/api/feeds/history/delete", json={"ids": [f"feedrun_{HEX16}"], "confirm": "ELIMINAR", "expected_count": 1}).json()
    assert r["results"][0]["status"] == "refused_not_a_regular_file" and (feeds / f"feedrun_{HEX16}.json").is_dir()


def test_a_late_declaration_never_writes_through_a_session_directory_that_became_a_symlink(mk, monkeypatch):
    env = mk(scene(person=range(0, 40)))
    sid, run = disconnect(env, monkeypatch)
    time.sleep(0.8)
    ext = env.tmp / "external_session"
    ext.mkdir()
    real = env.runs / run
    os.rename(real, env.runs / "moved_away")
    os.symlink(ext, real)
    r = env.c.post(f"/api/webcam/sessions/{sid}/stop", headers={"X-MG-Next-Seq": "6"}).json()
    assert r["late_declaration"] == {"admitted": False, "reason": "session_directory_unavailable"}
    assert list(ext.iterdir()) == []


# ------------------------------------------------------------------ MG-API-021
BIG = 70_000


@pytest.mark.parametrize("method,url", [("post", "/api/feeds"), ("put", "/api/feeds/feed_deadbeef/zones"), ("post", "/api/feeds/capacity"), ("post", "/api/feeds/history/delete"),
                                       ("post", "/api/alerts/seen"), ("post", "/api/alerts/alt_0123456789abcdef/delivery"), ("put", "/api/alerts/settings")])
def test_every_new_json_route_refuses_an_oversize_body_before_parsing_it(fm, method, url):
    env = fm({1: always()})
    body = b'{"ids":["' + b"a" * BIG + b'"]}'
    r = getattr(env.c, method)(url, content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["detail"]["msg_id"] == "mg.refusal.body_too_large" and len(r.content) < 200
    chunked = getattr(env.c, method)(url, content=iter([b"x" * 30_000, b"y" * 30_000, b"z" * 30_000]), headers={"Content-Type": "application/json"})   # no Content-Length: counted as it arrives
    assert chunked.status_code == 413


def test_settings_have_a_smaller_cap_and_malformed_json_is_a_plain_422(fm):
    env = fm({1: always()})
    assert env.c.put("/api/alerts/settings", content=b" " * 9_000 + b"{}", headers={"Content-Type": "application/json"}).status_code == 413
    assert env.c.put("/api/alerts/settings", content=b"{not json", headers={"Content-Type": "application/json"}).status_code == 422
    assert env.c.put("/api/alerts/settings", content=b"[1,2]", headers={"Content-Type": "application/json"}).status_code == 422


def test_unknown_members_and_overlong_strings_are_refused_without_echo(fm):
    env = fm({1: always()})
    secret = "Z" * 39
    r = env.c.post("/api/alerts/seen", json={"ids": [secret + "Q"], "extra": 1})
    assert r.status_code == 422 and secret not in r.text and r.json()["detail"] == {"msg_id": "mg.refusal.invalid_request"}
    r = env.c.post("/api/alerts/seen", json={"ids": ["Z" * 500]})
    assert r.status_code == 422 and "ZZZZ" not in r.text
    r = env.c.post("/api/feeds/capacity", json={"limit": 3, "acknowledge_unvalidated": False, "surprise": True})
    assert r.status_code == 422
    r = env.c.post("/api/feeds/history/delete", json={"ids": ["Z" * 41], "confirm": "ELIMINAR", "expected_count": 1})
    assert r.status_code == 422 and "ZZZZ" not in r.text


def test_the_history_delete_response_never_reproduces_a_client_supplied_id(fm):
    env = fm({1: always()})
    odd = "x" * 39 + "'<script>"[:1]
    r = env.c.post("/api/feeds/history/delete", json={"ids": [odd, "../../etc/passwd"], "confirm": "ELIMINAR", "expected_count": 2})
    assert r.status_code == 200 and odd not in r.text and "passwd" not in r.text and [x["status"] for x in r.json()["results"]] == ["refused_bad_id", "refused_bad_id"]


def test_validation_errors_do_not_echo_the_rejected_input_on_the_feed_routes(fm):
    env = fm({1: always()})
    r = env.c.post("/api/feeds", json={"video_id": "vid_test1", "label": "L" * 80, "zones": []})
    assert r.status_code == 422 and "LLLL" not in r.text
    r = env.c.post("/api/feeds", json={"video_id": "vid_test1", "mode": "<img onerror=x>", "zones": []})
    assert r.status_code == 422 and "onerror" not in r.text


# ------------------------------------------------------------------ review B (cycle 22): MG-PATH-022 / MG-API-022
def test_a_pre_created_temporary_name_hard_linked_to_a_foreign_file_is_never_written_through(tmp_path):
    root = tmp_path / "runs"
    (root / "alerts").mkdir(parents=True)
    foreign = tmp_path / "someone_elses_file.txt"
    foreign.write_text("PRECIOUS")
    for predictable in (".alerts.json.part", ".settings.json.part", ".alerts.json.0000000000000000.part"):
        os.link(foreign, root / "alerts" / predictable)                                       # the attacker plants hard links under every name an implementation might use
    e = AlertEngine(root / "alerts")
    e.observe_source(src(), [evt("e1", "RED")], {})
    e.flush()
    e.set_settings(json.loads(json.dumps(e.settings)))
    assert foreign.read_text() == "PRECIOUS"                                                   # the foreign inode was never opened for writing
    assert json.loads((root / "alerts" / "alerts.json").read_text())["alerts"]


def test_the_temporary_name_is_unpredictable_exclusive_and_cleaned_up_on_failure(tmp_path, monkeypatch):
    d = tmp_path / "s"
    d.mkdir()
    fd = safedir.open_owned_dir(tmp_path, "s", create=False)
    try:
        names = set()
        real = os.rename
        monkeypatch.setattr(safedir.os, "rename", lambda a, b, **k: (names.add(a), real(a, b, **k))[1])
        for _ in range(5):
            safedir.write_atomic(fd, "x.json", "{}")
        assert len(names) == 5 and all(re.fullmatch(r"\.x\.json\.[0-9a-f]{16}\.part", n) for n in names)
        monkeypatch.setattr(safedir.os, "rename", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
        with pytest.raises(OSError):
            safedir.write_atomic(fd, "y.json", "{}")
    finally:
        os.close(fd)
    assert sorted(p.name for p in d.iterdir()) == ["x.json"]                                    # no stray temporary file after a failed write


def test_nested_zone_members_are_strict_too(fm):
    env = fm({1: always()})
    z = dict(ZONE, surprise=1)
    r = env.c.post("/api/feeds", json={"video_id": "vid_test1", "zones": [z]})
    assert r.status_code == 422 and "surprise" not in r.text
    a = create(env, 1)
    assert env.c.put(f"/api/feeds/{a}/zones", json={"zones": [z], "expected_revision": 1}).status_code == 422
    body = {"width": 320, "height": 180, "interval_ms": 100, "threshold": 0.3, "zones": [z], "capture": {"origin": "test_source", "device_label": "x", "user_agent": "t"}}
    assert env.c.post("/api/webcam/sessions", json=body).status_code == 422                    # the older webcam start route shares the model
