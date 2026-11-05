"""RC-1S: local API security (MG-SEC-002), Unicode NFC (MG-DATA-001-004) and observation origin labels (MG-ORIGIN-001).
No model and no video are needed: the app is created over empty temporary asset/run directories."""
import json
import os
import re
import stat
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import local_client
from mineguard.app import create_app
from mineguard.canon import NotNfcError, canonical_bytes, loads_strict, read_json_strict
from mineguard.schema import ValidationFailure, validate_observation_set

TOKEN = "test-session-token-0123456789abcdefghijklmnop"
PORT_HOST = "127.0.0.1:8000"
INDEX = "<!doctype html><html><head><title>t</title></head><body><div id=root></div></body></html>"


@pytest.fixture()
def app(tmp_path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text(INDEX)
    (dist / "assets" / "a.js").write_text("console.log(1)")
    (tmp_path / "assets").mkdir()
    return create_app(asset_dir=tmp_path / "assets", runs_dir=tmp_path / "runs", dist_dir=dist, session_token=TOKEN)


def raw(app, **kw):
    """A client with NO credentials: loopback peer and an allowed Host only."""
    return TestClient(app, base_url="http://127.0.0.1:8000", client=kw.pop("client", ("127.0.0.1", 50000)), **kw)


# ------------------------------------------------------------------ Host allowlist and DNS rebinding
@pytest.mark.parametrize("host", ["127.0.0.1:8000", "localhost:8000", "localhost", "LOCALHOST:9", "127.0.0.1"])
def test_allowed_hosts_work(app, host):
    assert raw(app).get("/api/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "127.0.0.1.evil.example", "localhost.evil.example:8000",
                                  "[::1]:8000", "0.0.0.0:8000", "192.168.1.5:8000", "127.0.0.1@evil.example", "127.0.0.1, evil.example",
                                  "127.0.0.1/evil", "xlocalhost", "127.0.0.2"])
@pytest.mark.parametrize("path", ["/api/health", "/", "/api/videos"])
def test_hostile_host_is_refused_even_with_valid_credentials(app, host, path):
    c = raw(app, headers={"X-MG-Token": TOKEN}, cookies={"mg_session": TOKEN})
    r = c.get(path, headers={"Host": host})
    assert r.status_code == 403 and r.json()["detail"]["msg_id"] == "mg.refusal.host_not_allowed"
    assert TOKEN not in r.text and "set-cookie" not in r.headers


def test_dns_rebinding_cannot_obtain_the_token(app):
    """The rebound page reaches the loopback socket but carries the attacker's name in Host (and the same name as Origin)."""
    c = raw(app)
    for path in ("/", "/index.html"):
        r = c.get(path, headers={"Host": "rebind.attacker.example:8000", "Origin": "http://rebind.attacker.example:8000"})
        assert r.status_code == 403 and TOKEN not in r.text and "set-cookie" not in r.headers
    r = c.post("/api/videos/vid_x/analyze", json={"stride": 3}, headers={"Host": "rebind.attacker.example:8000",
                                                                         "Origin": "http://rebind.attacker.example:8000"})
    assert r.status_code == 403


def test_missing_or_duplicated_host_header_is_refused(app):
    seen = {}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m):
        if m["type"] == "http.response.start":
            seen["status"] = m["status"]

    import asyncio

    for headers in ([], [(b"host", b"127.0.0.1:8000"), (b"host", b"evil.example")]):
        scope = {"type": "http", "method": "GET", "path": "/api/health", "query_string": b"", "headers": headers,
                 "client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8000), "scheme": "http", "http_version": "1.1", "root_path": ""}
        asyncio.run(app(scope, receive, send))
        assert seen["status"] == 403


# ------------------------------------------------------------------ peer address
@pytest.mark.parametrize("peer", [("10.0.0.5", 1234), ("192.168.1.9", 1), ("testclient", 5), ("", 0)])
def test_non_loopback_peer_is_refused(app, peer):
    r = raw(app, client=peer, headers={"X-MG-Token": TOKEN}).get("/api/health")
    assert r.status_code == 403 and r.json()["detail"]["msg_id"] == "mg.refusal.peer_not_allowed"


def test_ipv6_loopback_peer_is_accepted(app):
    assert raw(app, client=("::1", 1)).get("/api/health").status_code == 200


# ------------------------------------------------------------------ Origin / cross-site
@pytest.mark.parametrize("origin", ["http://evil.example", "http://evil.example:8000", "null", "http://localhost:8000", "https://127.0.0.1:8000",
                                    "http://127.0.0.1:9999", "http://127.0.0.1.evil.example:8000", "file://", ""])
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_foreign_origin_is_refused_even_with_the_token(app, origin, method):
    c = raw(app, headers={"X-MG-Token": TOKEN})
    r = c.request(method, "/api/model" if method == "GET" else "/api/observations/import", headers={"Origin": origin}, json=None if method == "GET" else {})
    assert r.status_code == 403 and r.json()["detail"]["msg_id"] == "mg.refusal.origin_not_allowed"


def test_same_origin_requests_work(app):
    c = raw(app, headers={"X-MG-Token": TOKEN})
    assert c.get("/api/model", headers={"Origin": "http://127.0.0.1:8000"}).status_code != 403
    r = c.post("/api/observations/import", content="{}", headers={"Origin": "http://127.0.0.1:8000"})
    assert r.status_code not in (401, 403)


def test_duplicated_origin_header_is_refused(app):
    r = raw(app, headers={"X-MG-Token": TOKEN}).get("/api/model", headers=[("Origin", "http://127.0.0.1:8000"), ("Origin", "http://evil.example")])
    assert r.status_code == 403


def test_preflight_from_another_origin_is_refused_and_no_cors_headers_exist(app):
    r = raw(app).options("/api/observations/import", headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "POST",
                                                                "Access-Control-Request-Headers": "x-mg-token"})
    assert r.status_code == 403 and not any(k.lower().startswith("access-control-") for k in r.headers)
    ok = raw(app, headers={"X-MG-Token": TOKEN}).get("/api/model")
    assert not any(k.lower().startswith("access-control-") for k in ok.headers)


@pytest.mark.parametrize("site,allowed", [("cross-site", False), ("same-site", False), ("same-origin", True), ("none", True)])
def test_sec_fetch_site(app, site, allowed):
    r = raw(app, headers={"X-MG-Token": TOKEN}).get("/api/model", headers={"Sec-Fetch-Site": site})
    assert (r.status_code != 403) is allowed


# ------------------------------------------------------------------ credentials
PROTECTED = [("GET", "/api/videos"), ("GET", "/api/model"), ("GET", "/api/runs"), ("GET", "/api/jobs/job_x"),
             ("GET", "/api/runs/run_0123456789abcdef"), ("GET", "/api/runs/run_0123456789abcdef/export"), ("GET", "/api/runs/run_0123456789abcdef/verify"),
             ("GET", "/api/runs/run_0123456789abcdef/frames/0"), ("GET", "/api/videos/vid_x/file"),
             ("POST", "/api/videos/vid_x/analyze"), ("POST", "/api/jobs/job_x/cancel"), ("POST", "/api/observations/import")]


@pytest.mark.parametrize("method,path", PROTECTED)
def test_missing_or_wrong_credentials_are_refused(app, method, path):
    c = raw(app)
    for headers in ({}, {"X-MG-Token": "wrong"}, {"X-MG-Token": TOKEN + "x"}, {"X-MG-Token": TOKEN[:-1]}, {"X-MG-Token": ""},
                    {"Authorization": f"Bearer {TOKEN}"}):
        r = c.request(method, path, headers=headers, json={} if method == "POST" else None)
        assert r.status_code == 401 and r.json()["detail"]["msg_id"] == "mg.refusal.unauthorized", (headers, r.status_code)
    assert c.request(method, f"{path}?token={TOKEN}", json={} if method == "POST" else None).status_code == 401  # never accepted in a URL


@pytest.mark.parametrize("method,path", [p for p in PROTECTED if p[0] == "POST"])
def test_cookie_alone_never_authorizes_state_changing_calls(app, method, path):
    r = raw(app, cookies={"mg_session": TOKEN}).request(method, path, json={})
    assert r.status_code == 401  # CSRF: a cookie is sent automatically by browsers, so it is not accepted for POST


@pytest.mark.parametrize("method,path", [p for p in PROTECTED if p[0] == "GET"])
def test_cookie_authorizes_reads_for_media_and_download_elements(app, method, path):
    r = raw(app, cookies={"mg_session": TOKEN}).get(path)
    assert r.status_code not in (401, 403)  # 200 / 404 / 409 depending on the (empty) fixture


def test_header_token_authorizes_post(app):
    r = raw(app, headers={"X-MG-Token": TOKEN}).post("/api/observations/import", content="{}")
    assert r.status_code == 422  # reached the handler: invalid observation set, not an authorization error


def test_health_is_open_but_leaks_nothing_sensitive(app):
    r = raw(app).get("/api/health")
    assert r.status_code == 200 and set(r.json()) == {"status", "version", "network_guard"} and TOKEN not in r.text


# ------------------------------------------------------------------ token hand-out, headers, websocket, files, binding
def test_index_hands_out_the_token_only_as_meta_and_strict_cookie(app):
    c = raw(app)
    for path in ("/", "/index.html"):
        r = c.get(path)
        assert r.status_code == 200 and f'<meta name="mg-session-token" content="{TOKEN}" />' in r.text
        sc = r.headers["set-cookie"].lower()
        assert "mg_session=" in sc and "httponly" in sc and "samesite=strict" in sc and "path=/" in sc
        assert r.headers["cache-control"] == "no-store"
    assert TOKEN not in c.get("/assets/a.js").text and c.get("/assets/a.js").status_code == 200  # static assets need no credentials
    for path in ("/api/health", "/api/model"):
        assert TOKEN not in raw(app, headers={"X-MG-Token": TOKEN}).get(path).text


def test_cookie_from_index_is_usable_for_reads_and_not_for_posts(app):
    c = raw(app)
    assert c.get("/api/videos").status_code == 401
    c.get("/")  # browser navigation to the shell sets the cookie
    assert c.get("/api/videos").status_code == 200
    assert c.post("/api/observations/import", content="{}").status_code == 401


def test_security_headers_on_every_response(app):
    for r in (raw(app).get("/"), raw(app, headers={"X-MG-Token": TOKEN}).get("/api/model"), raw(app).get("/api/videos")):
        h = r.headers
        assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer"
        assert "frame-ancestors 'none'" in h["content-security-policy"] and "script-src 'self'" in h["content-security-policy"]
        assert h["cross-origin-resource-policy"] == "same-origin"


def test_websocket_upgrades_are_refused_with_and_without_credentials(app):
    for kwargs in ({}, {"headers": {"X-MG-Token": TOKEN}, "cookies": {"mg_session": TOKEN}}):
        c = raw(app, **kwargs)
        with pytest.raises(Exception):
            with c.websocket_connect("/api/ws"):
                pass
        with pytest.raises(Exception):
            with c.websocket_connect("/api/ws", headers={"Origin": "http://evil.example"}):
                pass


def test_token_file_is_private_and_per_process(tmp_path):
    f = tmp_path / "state" / "session_token"
    a = create_app(asset_dir=tmp_path, runs_dir=tmp_path / "r1", token_file=f)
    assert f.read_text() == a.state.session_token and stat.S_IMODE(f.stat().st_mode) == 0o600 and len(a.state.session_token) >= 43
    b = create_app(asset_dir=tmp_path, runs_dir=tmp_path / "r2", token_file=f)
    assert b.state.session_token != a.state.session_token and f.read_text() == b.state.session_token


def test_importing_the_module_does_not_write_a_token_file():
    import mineguard.app as m

    token_file = m.ROOT / "logs" / "session_token"
    before = token_file.read_bytes() if token_file.exists() else None
    import importlib

    os.environ.pop("MG_WRITE_TOKEN_FILE", None)
    importlib.reload(m)
    assert (token_file.read_bytes() if token_file.exists() else None) == before


def test_launch_scripts_bind_only_to_loopback():
    scripts = sorted((Path(__file__).resolve().parents[2] / "scripts").glob("*.sh"))
    text = "\n".join(p.read_text() for p in scripts)
    assert "0.0.0.0" not in text and "--host ::" not in text
    hosts = set(re.findall(r"--host\s+(\S+)", text))
    assert hosts == {"127.0.0.1"}, hosts


# ------------------------------------------------------------------ MG-DATA-001-004: NFC
def test_canonical_bytes_rejects_non_nfc_object_keys():
    assert canonical_bytes({"é": 1}) == '{"é":1}'.encode()  # composed form is accepted
    for bad in ({"é": 1}, {"a": {"é": 1}}, {"a": [{"x": {"ö": 2}}]}):
        with pytest.raises(NotNfcError):
            canonical_bytes(bad)
    with pytest.raises(NotNfcError):
        canonical_bytes({"é": 1, "é": 2})  # the two equivalent spellings never coexist: the decomposed one is rejected, not merged
    with pytest.raises(NotNfcError):
        canonical_bytes({"k": "é"})  # values were already covered


def test_validation_reports_v17_for_a_decomposed_key_and_does_not_normalize_it(obsset):
    o = obsset(); o["producer"]["params"] = {"é": 1}
    with pytest.raises(ValidationFailure) as e:
        validate_observation_set(o)
    assert "V-17" in e.value.codes and list(o["producer"]["params"]) == ["é"]  # input untouched
    o = obsset(); o["producer"]["params"] = {"é": 1}
    validate_observation_set(o)


def test_strict_loader_rejects_non_nfc_keys_in_both_spellings_of_the_json():
    for text in ('{"é": 1}', '{"e\\u0301": 1}', '{"a": [{"o\\u0308": 1}]}'):
        with pytest.raises(NotNfcError):
            loads_strict(text)
    assert loads_strict('{"\\u00e9": 1}') == {"é": 1}
    assert loads_strict('{"k": "e\\u0301"}') == {"k": "é"}  # string VALUES are validated where the contract canonicalizes them (V-17), not here


def test_stored_artifact_with_a_decomposed_key_is_rejected(tmp_path):
    p = tmp_path / "observations.json"
    p.write_text('{"producer": {"params": {"e\\u0301": 1}}}')
    with pytest.raises(NotNfcError):
        read_json_strict(p)


def test_import_rejects_decomposed_keys_as_received(app, obsset):
    body = json.dumps(obsset("human_annotated"))
    bad = body.replace('"params": {}', '"params": {"e\\u0301": 1}')
    assert bad != body
    c = local_client(app)
    r = c.post("/api/observations/import", content=bad, headers={"content-type": "application/json"})
    assert r.status_code == 422 and r.json()["codes"] == ["V-17"]
    good = body.replace('"params": {}', '"params": {"\\u00e9": 1}')
    assert c.post("/api/observations/import", content=good).json()["mode"] == "replay"


# ------------------------------------------------------------------ MG-ORIGIN-001: backend labels
def synthetic(obsset):
    o = obsset("synthetic_scripted")
    o["frames"][0]["objects"][0]["confidence"] = {"permille": None, "kind": "not_applicable"}
    return o


def test_each_origin_gets_its_own_banner(app, obsset):
    c = local_client(app)
    h = c.post("/api/observations/import", json=obsset("human_annotated")).json()
    s = c.post("/api/observations/import", json=synthetic(obsset)).json()
    assert (h["origin"], h["banner_msg_id"]) == ("human_annotated", "mg.banner.human_annotated")
    assert (s["origin"], s["banner_msg_id"]) == ("synthetic_scripted", "mg.banner.synthetic_replay")
    assert "synthetic" not in json.dumps(h) and "annotated" not in json.dumps(s)


def test_model_inference_is_never_granted_to_an_import_without_a_local_verified_run(app, obsset):
    r = local_client(app).post("/api/observations/import", json=obsset("model_inference"))
    assert r.status_code == 422 and "banner_msg_id" not in r.json()
