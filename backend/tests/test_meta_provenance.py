"""META ACCEPTANCE RECOVERY 05: the four open defects of Codex review 2 (F-1 mock certified as real, F-2 fabricable receipt, F-3 budget not bound to the endpoint, F-4 credential format).
Every test here fails against the code of commit 35a0132 (it either does not import, or accepts the behaviour). Loopback/in-process only: no external DNS, no real host, dummy credentials.
Scope of the guarantees: an attestation is unforgeable BY DATA and per process; code in the same process that calls the private mint can still forge it (documented limitation)."""
import copy
import http.client
import json
import shutil
import socket
import ssl
import subprocess
import sys
import threading
from decimal import Decimal
from pathlib import Path

import pytest

from mineguard import costgov, meta_obs, meta_pipeline, metahttp, metaclient, muse_open as M, sam31 as S
from mineguard.costgov import Denied, GovernorConfig, SpendGovernor
from mineguard.metahttp import EndpointBoundGuard, ProductionHttpsTransport, TestHttpsTransport

import test_meta_transport as T
from test_meta_transport import Srv, KEY, OK_STREAM, LINE, URL, no_external_dns, sse  # noqa: F401

ZONES = [meta_pipeline.full_frame_zone(640, 480)]


def cfg(tmp_path, body, *, provider=S.PROVIDER_ID, model=S.MODEL, price=S.PRICE_PER_IMAGE_USD, max_requests=20, concurrency=1, live=True, name="l.jsonl"):
    return GovernorConfig(live_provider_enabled=live, external_media_upload_enabled=True, max_real_requests=max_requests, max_authorized_spend_usd=Decimal(1), price_usd_per_request=((model, price),),
                          provider_budget_usd=((provider, Decimal(1)),), max_tokens=100000, max_concurrency=concurrency, authorized_payload_digests=frozenset({costgov.raw_digest(body)}),
                          ledger_path=str(tmp_path / name), request_timeout_s=5)


def sam_setup(tmp_path, **kw):
    body = S.build_body(URL, "person")
    gov = SpendGovernor(cfg(tmp_path, body, **kw))
    auth = {(S.PROVIDER_ID, S.MODEL, costgov.raw_digest(body))}
    return body, gov, auth


def stages(order=("authorized", "dispatched", "tls_verified", "response_complete"), t0=1000):
    return [(n, i, t0 + i) for i, n in enumerate(order)]


GOOD_TLS = {"peer_verified": True, "hostname": "api.meta.ai", "cert_sha256": "c" * 64, "protocol": "TLSv1.3"}


def minted(gov, req, payload, **over):
    """White-box: the only way a test can obtain an attestation is the private mint (the in-process limitation the module documents)."""
    r = gov.reserve(req["request_key"], req["model"], req["provider"], req["est_tokens"], req["units"])
    gov.mark_unknown(r)
    f = dict(provider=req["provider"], model=req["model"], endpoint=metahttp.OFFICIAL_ENDPOINTS[req["provider"]], request_digest=costgov.raw_digest(req["body"]),
             response_digest=costgov.raw_digest(payload), request_key=req["request_key"], reservation_slot=r.slot, stages=stages(), tls=GOOD_TLS)
    f.update(over)
    return metahttp._mint(**f)


def sam_request(key="req-prov-001"):
    return S.build_governed_request(URL, "person", key)


EVENTS = [{"type": "response.output_text.delta", "delta": LINE}, {"type": "response.completed"}]


# ===== F-1 ==========================================================================================================================================================
def test_F1_the_production_transport_has_no_injection_points_and_cannot_be_subclassed(tmp_path):
    body, gov, auth = sam_setup(tmp_path)
    for kw in ({"connection_factory": lambda *a: None}, {"allow_loopback_http": True}, {"base_url": "http://127.0.0.1:1/v1"}, {"path": "/x"}, {"host": "evil.example"}, {"context": ssl.create_default_context()}):
        with pytest.raises(TypeError):
            ProductionHttpsTransport(S.PROVIDER_ID, governor=gov, authorized=auth, **kw)
    with pytest.raises(ValueError):
        ProductionHttpsTransport("unknown_provider", governor=gov, authorized=auth)
    with pytest.raises(TypeError):
        class Evil(ProductionHttpsTransport):                                          # a subclass could override the connection or the TLS check
            pass
    t = ProductionHttpsTransport(S.PROVIDER_ID, governor=gov, authorized=auth)
    assert (t.host, t.base_path, t.path, t.scheme, t.port) == ("api.meta.ai", "/v1", "/responses", "https", None) and t.is_production is True
    for attr, val in (("host", "evil.example"), ("path", "/x"), ("provider", "baseten_muse_spark_open"), ("authorized", frozenset()), ("scheme", "http"), ("max_uses", 99)):
        with pytest.raises(AttributeError):
            setattr(t, attr, val)


class _Fake:
    made = 0

    def __init__(self, *a, **k):
        _Fake.made += 1

    def request(self, *a, **k):
        raise AssertionError("no request may reach a patched connection")

    def close(self):
        pass


def prod_guard(tmp_path, body=None):
    body = body or S.build_body(URL, "person")
    gov = SpendGovernor(cfg(tmp_path, body))
    auth = {(S.PROVIDER_ID, S.MODEL, costgov.raw_digest(body))}
    t = ProductionHttpsTransport(S.PROVIDER_ID, governor=gov, authorized=auth)
    return gov, t, EndpointBoundGuard(t, gov, lambda: KEY)


def test_F1_a_patched_connection_primitive_is_detected_before_any_connection_and_yields_no_attestation(tmp_path, monkeypatch, no_external_dns):
    gov, t, g = prod_guard(tmp_path)
    _Fake.made = 0
    monkeypatch.setattr(http.client, "HTTPSConnection", _Fake)
    with pytest.raises(metahttp.TransportFault) as e:
        g.send(sam_request("req-f1-0101"))
    assert "transport_primitives_replaced" in str(e.value) and _Fake.made == 0
    monkeypatch.undo()
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **k: None)
    with pytest.raises(metahttp.TransportFault):
        g.send(sam_request("req-f1-0102"))


class _DNS:
    def __init__(self, monkeypatch):
        self.seen = []
        real = socket.getaddrinfo

        def fake(host, *a, **k):
            if host in ("127.0.0.1", "localhost", "::1"):
                return real(host, *a, **k)
            self.seen.append(host)
            raise socket.gaierror("blocked by the test: no external resolution")
        monkeypatch.setattr(socket, "getaddrinfo", fake)


def test_F1_patching_the_hooks_modules_or_helpers_of_the_production_transport_changes_nothing(tmp_path, monkeypatch):
    """Review 1 of Recovery 05: `_open`/`_peer`/`_finish` were ordinary class methods; patching them let a fake connection mint an attestation. There are no such methods any more, and the
    dispatch closure captured its primitives at import: patched module functions are never consulted. The real code path then tries the real host (name resolution is blocked here)."""
    dns = _DNS(monkeypatch)
    called = []
    gov, t, g = prod_guard(tmp_path)
    assert not any(hasattr(ProductionHttpsTransport, n) for n in ("_open", "_peer", "_finish", "_open_conn", "_require_governed"))
    for name in ("_open", "_peer", "_finish", "_open_conn"):
        monkeypatch.setattr(ProductionHttpsTransport, name, lambda *a, **k: called.append(name) or (_ for _ in ()).throw(AssertionError("patched hook used")), raising=False)
    fake_tls = {"peer_verified": True, "hostname": "api.meta.ai", "cert_sha256": "f" * 64, "protocol": "TLSv1.3"}
    monkeypatch.setattr(metahttp, "_tls_evidence", lambda *a, **k: called.append("_tls_evidence") or fake_tls)
    monkeypatch.setattr(metahttp, "_mint", lambda **k: called.append("_mint"))
    monkeypatch.setattr(metahttp, "_dispatch", lambda *a, **k: called.append("_dispatch"))
    monkeypatch.setattr(metahttp, "_gate_impl", lambda *a, **k: called.append("_gate_impl"))
    for helper in ("_use_impl", "_parse_sse", "_error_view", "_redact", "_default_fns", "_claim_impl", "_release_impl"):
        if helper != "_claim_impl" and helper != "_release_impl":
            monkeypatch.setattr(metahttp, helper, lambda *a, _h=helper, **k: called.append(_h) or (_ for _ in ()).throw(AssertionError("patched helper used")))
    with pytest.raises(metahttp.TransportFault) as e:
        g.send(sam_request("req-f1-0103"))
    assert called == [] and dns.seen == ["api.meta.ai"]                         # the genuine path ran and reached for the real hostname; no patched function was used
    assert "attestation" not in str(e.value)


def test_F1_a_production_response_without_verified_tls_is_never_returned(tmp_path):
    """White-box seam: a closure built with a FAKE connection class has no real ssl.SSLSocket to show, so it cannot mint (nor return the 200)."""
    import types
    body = S.build_body(URL, "person")
    gov = SpendGovernor(cfg(tmp_path, body))
    auth = {(S.PROVIDER_ID, S.MODEL, costgov.raw_digest(body))}
    t = ProductionHttpsTransport(S.PROVIDER_ID, governor=gov, authorized=auth)

    class FakeConn:
        sock = None
        debuglevel = 0

        def __init__(self, *a, **k): pass
        def set_debuglevel(self, n): pass
        def request(self, *a, **k): pass
        def close(self): pass
        def getresponse(self):
            class R:
                status = 200
                def read(self, n=-1, _d=[OK_STREAM]):
                    return _d.pop() if _d else b""
                def getheader(self, *a): return None
            return R()
    fn = metahttp._make_production_send(https_conn=FakeConn, create_ctx=lambda: None)
    object.__setattr__(t, "send", types.MethodType(fn, t))
    g = EndpointBoundGuard(t, gov, lambda: KEY)
    with pytest.raises(metahttp.TransportFault) as e:
        g.send(sam_request("req-f1-0104"))
    assert "tls_peer_not_verified" in str(e.value)


def test_F1_the_test_transport_can_never_attest_even_on_the_official_url_with_an_injected_connection(tmp_path, no_external_dns):
    body, gov, auth = sam_setup(tmp_path)

    class FakeConn:
        def __init__(self, *a, **k): pass
        def request(self, *a, **k): pass
        def close(self): pass
        def getresponse(self):
            class R:
                status = 200
                def read(self, n=-1, _d=[OK_STREAM]):
                    return _d.pop() if _d else b""
                def getheader(self, *a): return None
            return R()
    inner = TestHttpsTransport("https://api.meta.ai/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized=auth, accept_sse=True, connection_factory=lambda h, p, t: FakeConn())
    g = EndpointBoundGuard(inner, gov, lambda: KEY)
    out = metaclient.SamClient(g).first_call(URL, "person", "req-f1-0001", width=640, height=480, zones=ZONES)
    assert out["outcome"] == "OK" and out["real_meta_call_completed"] is False
    ev = out["evidence"]
    assert ev["provenance"]["status"] == "SIMULATED" and ev["provenance"]["origin"] == "simulated_provider_response" and ev["provenance"]["real_meta_call"] is False


def test_F1_loopback_and_hand_built_responses_stay_simulated_or_are_rejected(tmp_path, no_external_dns):
    body, gov, auth = sam_setup(tmp_path)
    srv = Srv([(200, {}, OK_STREAM, 0)])
    try:
        inner = TestHttpsTransport(f"http://127.0.0.1:{srv.port}/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized=auth, accept_sse=True, allow_loopback_http=True)
        g = EndpointBoundGuard(inner, gov, lambda: KEY)
        out = metaclient.SamClient(g).first_call(URL, "person", "req-f1-0002", width=640, height=480, zones=ZONES)
        assert out["evidence"]["provenance"]["status"] == "SIMULATED"
        forged = metahttp.Attestation(attempt_id="a" * 32, provider=S.PROVIDER_ID, model=S.MODEL, endpoint=metahttp.OFFICIAL_ENDPOINTS[S.PROVIDER_ID], request_digest="a" * 64, response_digest="b" * 64,
                                      request_key="req-x", reservation_slot="s", stages=tuple(stages()), tls=GOOD_TLS, issued_ns=1, mac="0" * 64)
        base = {"status": 200, "body": {"events": EVENTS}, "usage": None}
        for resp in ({**base, "origin": "PRODUCTION", "attestation": forged}, {**base, "origin": "TEST", "attestation": forged}, {**base, "origin": "PRODUCTION"}, {**base, "attestation": forged}, {**base}):
            att, rej = metaclient.resolve_origin(g, resp)
            assert att is None and rej is not None and rej["outcome"] == "ORIGIN_REJECTED" and rej["evidence"] is None, resp.keys()
        att, rej = metaclient.resolve_origin(g, {**base, "origin": "TEST"})
        assert att is None and rej is None
    finally:
        srv.close()


def test_F1_a_client_refuses_a_guard_that_is_not_bound_to_its_provider_or_not_an_endpoint_bound_guard(tmp_path):
    body, gov, auth = sam_setup(tmp_path)
    plain = costgov.GuardedTransport(TestHttpsTransport("http://127.0.0.1:1/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized=auth, accept_sse=True, allow_loopback_http=True), gov, lambda: KEY)
    with pytest.raises(ValueError):
        metaclient.SamClient(plain)
    other = EndpointBoundGuard(TestHttpsTransport("http://127.0.0.1:1/v1", path="/chat/completions", provider=M.PROVIDER_ID, governor=gov, authorized={(M.PROVIDER_ID, "m/x-y", "d")},
                                                  allow_loopback_http=True), gov, lambda: KEY)
    with pytest.raises(ValueError):
        metaclient.SamClient(other)
    with pytest.raises(ValueError):
        M.MuseOpenClient(EndpointBoundGuard(TestHttpsTransport("http://127.0.0.1:1/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized=auth, accept_sse=True, allow_loopback_http=True), gov, lambda: KEY))


def _tls_server(tmp_path):
    openssl = shutil.which("openssl")
    assert openssl, "openssl is required for the TLS evidence test"
    (tmp_path / "san.cnf").write_text("[req]\ndistinguished_name=dn\nx509_extensions=v3\nprompt=no\n[dn]\nCN=localhost\n[v3]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n")
    subprocess.run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(tmp_path / "k.pem"), "-out", str(tmp_path / "c.pem"), "-days", "2", "-config", str(tmp_path / "san.cnf")],
                   check=True, capture_output=True)
    sctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    sctx.load_cert_chain(str(tmp_path / "c.pem"), str(tmp_path / "k.pem"))
    ls = socket.socket()
    ls.bind(("127.0.0.1", 0))
    ls.listen(5)

    def handle(c):
        """One thread per connection: a client that closes without reading (the weak-context cases) must never stop the server from accepting the next handshake (the previous version
        returned from its only loop on the first per-connection error, which made the test fail intermittently)."""
        try:
            c.settimeout(30)
            tc = sctx.wrap_socket(c, server_side=True)
            tc.recv(65536)
            tc.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}")
            tc.close()
        except OSError:
            try:
                c.close()
            except OSError:
                pass

    def serve():
        while True:
            try:
                c, _ = ls.accept()
            except OSError:
                return                                              # the listening socket was closed: the test is over
            threading.Thread(target=handle, args=(c,), daemon=True).start()
    threading.Thread(target=serve, daemon=True).start()
    return ls, ls.getsockname()[1], str(tmp_path / "c.pem")


def test_F1_tls_evidence_needs_a_real_verified_tls_socket_for_the_exact_hostname(tmp_path, no_external_dns):
    ls, port, ca = _tls_server(tmp_path)
    try:
        good = ssl.create_default_context(cafile=ca)
        c = http.client.HTTPSConnection("localhost", port, timeout=30, context=good)
        c.request("GET", "/")
        ev = metahttp._tls_evidence(c, "localhost")                                          # taken right after the request, before the response (a closing connection drops its socket)
        c.getresponse().read()
        assert metahttp._tls_evidence(c, "localhost") is None                                # ...and once the connection is closed there is nothing to certify
        assert ev and ev["peer_verified"] is True and ev["hostname"] == "localhost" and len(ev["cert_sha256"]) == 64
        assert metahttp._tls_evidence(c, "api.meta.ai") is None                              # verified for a DIFFERENT name than the one claimed
        c.close()
        weak = ssl.create_default_context(cafile=ca)
        weak.check_hostname = False
        c2 = http.client.HTTPSConnection("localhost", port, timeout=30, context=weak)
        c2.request("GET", "/")
        assert metahttp._tls_evidence(c2, "localhost") is None                               # a context without hostname checking is not evidence
        c2.close()
        none_ctx = ssl.create_default_context()
        none_ctx.check_hostname = False
        none_ctx.verify_mode = ssl.CERT_NONE
        c3 = http.client.HTTPSConnection("localhost", port, timeout=30, context=none_ctx)
        c3.request("GET", "/")
        assert metahttp._tls_evidence(c3, "localhost") is None                               # no certificate requirement
        c3.close()
        for mock in (object(), type("C", (), {"sock": object()})(), type("C", (), {"sock": None})()):
            assert metahttp._tls_evidence(mock, "localhost") is None
    finally:
        ls.close()


# ===== F-2 ==========================================================================================================================================================
def _real(gov, req, att, events=EVENTS):
    return meta_pipeline.build_evidence(events, concept="person", width=640, height=480, pts_us=[0], zones=ZONES, simulated=False, attestation=att, request=req, governor=gov)


def test_F2_a_reservation_alone_certifies_nothing(tmp_path):
    body, gov, auth = sam_setup(tmp_path)
    req = sam_request()
    r = gov.reserve(req["request_key"], req["model"], req["provider"], 0, 1)
    gov.mark_unknown(r)
    for kw in ({}, {"attestation": None, "request": req, "governor": gov}):
        with pytest.raises(meta_obs.NormalizationRefused) as e:
            meta_pipeline.build_evidence(EVENTS, concept="person", width=640, height=480, pts_us=[0], zones=ZONES, simulated=False, **kw)
        assert e.value.code == "real_evidence_requires_attestation_request_and_governor"
    forged = {"attempt_id": "x", "provider": S.PROVIDER_ID, "model": S.MODEL, "request_digest": "a" * 64, "response_digest": "b" * 64, "request_key": req["request_key"], "reservation_slot": r.slot,
              "stages": [], "tls": GOOD_TLS, "endpoint": ["api.meta.ai", "/v1", "/responses"], "level": "x"}
    with pytest.raises(meta_obs.NormalizationRefused) as e:
        meta_pipeline.build_evidence(EVENTS, concept="person", width=640, height=480, pts_us=[0], zones=ZONES, simulated=False, attestation=forged, request=req, governor=gov)
    assert "not_an_attestation" in e.value.code                                              # a receipt-shaped dict is not an attestation
    env = meta_obs.from_wire(S.parse_stream(EVENTS, 640, 480, 1), "person", 640, 480, [0], simulated=False, receipt=forged)
    env["payload_digest"] = costgov.raw_digest({"events": EVENTS})
    with pytest.raises(meta_obs.NormalizationRefused) as e2:
        meta_obs.normalize(env)
    assert e2.value.code == "real_label_not_accepted_here"


def test_F2_a_complete_attestation_bound_to_this_request_response_and_ledger_is_accepted_once(tmp_path):
    body, gov, auth = sam_setup(tmp_path)
    req = sam_request()
    att = minted(gov, req, {"events": EVENTS})
    assert att.attempt_id not in metahttp._CONSUMED                                          # minted but not yet accepted
    ev = _real(gov, req, att)
    assert ev["provenance"]["status"] == "REAL_PROVIDER_RESPONSE_UNVERIFIED" and ev["provenance"]["origin"] == "provider_response_unverified" and ev["provenance"]["real_meta_call"] is True
    assert ev["provenance"]["authenticity"].startswith("TLS peer verified") and "NO provider signature" in ev["provenance"]["authenticity"]
    assert any("does not prove the provider's model produced them" in x for x in ev["provenance"]["limitations"]) and ev["decided_by"] == "deterministic_policy"
    assert att.attempt_id in metahttp._CONSUMED
    with pytest.raises(meta_obs.NormalizationRefused) as e:
        _real(gov, req, att)                                                                  # duplicate receipt
    assert "already_consumed" in e.value.code


@pytest.mark.parametrize("order,code", [(("authorized", "tls_verified", "response_complete"), "stages_incomplete"), (("dispatched", "tls_verified", "response_complete"), "stages_incomplete"),
                                        (("authorized", "dispatched", "response_complete"), "stages_incomplete"), (("authorized", "dispatched", "tls_verified"), "stages_incomplete"),
                                        (("authorized", "tls_verified", "dispatched", "response_complete"), "stages_incomplete"), (("response_complete", "tls_verified", "dispatched", "authorized"), "stages_incomplete"),
                                        (("authorized", "dispatched", "response_complete", "tls_verified"), "stages_incomplete"), (("authorized", "dispatched", "tls_verified", "response_complete", "response_complete"), "stages_incomplete")])
def test_F2_dispatch_response_and_tls_are_independent_facts_and_a_missing_or_reordered_one_is_rejected(tmp_path, order, code):
    body, gov, auth = sam_setup(tmp_path)
    req = sam_request()
    att = minted(gov, req, {"events": EVENTS}, stages=stages(order))
    p = metahttp.attestation_problems(att, request=req, payload={"events": EVENTS}, governor=gov)
    assert any(x.startswith(code) for x in p), p


def test_F2_non_monotonic_sequence_or_time_is_rejected(tmp_path):
    body, gov, auth = sam_setup(tmp_path)
    req = sam_request()
    names = ("authorized", "dispatched", "tls_verified", "response_complete")
    bad_seq = minted(gov, req, {"events": EVENTS}, stages=[(n, 0, 1000 + i) for i, n in enumerate(names)])
    bad_time = minted(gov, sam_request("req-prov-002"), {"events": EVENTS}, stages=[(n, i, 1000 - i) for i, n in enumerate(names)])
    assert any(x.startswith("stages_incomplete") for x in metahttp.attestation_problems(bad_seq, request=req, payload={"events": EVENTS}, governor=gov))
    assert any(x.startswith("stages_incomplete") for x in metahttp.attestation_problems(bad_time, request=sam_request("req-prov-002"), payload={"events": EVENTS}, governor=gov))


def test_F2_an_edited_or_invented_attestation_is_rejected_and_attestations_are_immutable(tmp_path):
    body, gov, auth = sam_setup(tmp_path)
    req = sam_request()
    att = minted(gov, req, {"events": EVENTS})
    with pytest.raises(AttributeError):
        att.response_digest = "0" * 64
    tampered = metahttp.Attestation(**{**{k: getattr(att, k) for k in metahttp.Attestation.__slots__[:-2]}, "response_digest": costgov.raw_digest({"events": []}), "mac": att.mac})
    assert metahttp.attestation_problems(tampered, request=req, payload={"events": []}, governor=gov) == ["mac_invalid"]
    invented = metahttp.Attestation(**{**{k: getattr(att, k) for k in metahttp.Attestation.__slots__[:-2]}, "mac": "f" * 64})
    assert metahttp.attestation_problems(invented, request=req, payload={"events": EVENTS}, governor=gov) == ["mac_invalid"]
    for not_a_redemption in (invented, None, att.public(), att):
        assert metahttp.use_redemption(not_a_redemption, response_digest="0" * 64) == ["not_a_redemption"]


def test_F2_identity_digests_and_authorization_must_all_match_the_request_and_the_response(tmp_path):
    body, gov, auth = sam_setup(tmp_path)
    req = sam_request()
    att = minted(gov, req, {"events": EVENTS})
    other_events = [{"type": "response.completed"}]
    assert "response_digest_mismatch" in metahttp.attestation_problems(att, request=req, payload={"events": other_events}, governor=gov)
    assert "request_identity_mismatch" in metahttp.attestation_problems(att, request={**req, "request_key": "req-other-99"}, payload={"events": EVENTS}, governor=gov)     # inconsistent request id
    assert "request_identity_mismatch" in metahttp.attestation_problems(att, request={**req, "model": "sam-3"}, payload={"events": EVENTS}, governor=gov)
    other_body = {**req, "body": {**req["body"], "stream": False}}
    assert "request_digest_mismatch" in metahttp.attestation_problems(att, request=other_body, payload={"events": EVENTS}, governor=gov)
    att2 = minted(gov, sam_request("req-prov-003"), {"events": EVENTS}, request_digest="d" * 64)
    p = metahttp.attestation_problems(att2, request=sam_request("req-prov-003"), payload={"events": EVENTS}, governor=gov)
    assert "request_digest_mismatch" in p
    att3 = minted(gov, sam_request("req-prov-004"), {"events": EVENTS}, endpoint=("api.meta.ai", "/v1", "/other"))
    assert "endpoint_not_the_provider_endpoint" in metahttp.attestation_problems(att3, request=sam_request("req-prov-004"), payload={"events": EVENTS}, governor=gov)
    att4 = minted(gov, sam_request("req-prov-005"), {"events": EVENTS}, tls={**GOOD_TLS, "peer_verified": False})
    assert "tls_not_verified" in metahttp.attestation_problems(att4, request=sam_request("req-prov-005"), payload={"events": EVENTS}, governor=gov)
    att5 = minted(gov, sam_request("req-prov-006"), {"events": EVENTS}, tls={**GOOD_TLS, "hostname": "evil.example"})
    assert "tls_not_verified" in metahttp.attestation_problems(att5, request=sam_request("req-prov-006"), payload={"events": EVENTS}, governor=gov)


def test_F2_a_late_response_is_stale(tmp_path):
    body, gov, auth = sam_setup(tmp_path)
    req = sam_request()
    att = minted(gov, req, {"events": EVENTS})
    late = att.issued_ns + (metahttp.MAX_ATTESTATION_AGE_S + 1) * 1_000_000_000
    assert "stale_or_future" in metahttp.attestation_problems(att, request=req, payload={"events": EVENTS}, governor=gov, now_ns=late)
    assert "stale_or_future" in metahttp.attestation_problems(att, request=req, payload={"events": EVENTS}, governor=gov, now_ns=att.issued_ns - 1)


def test_F2_the_ledger_must_contain_the_reservation_and_must_be_intact(tmp_path):
    body, gov, auth = sam_setup(tmp_path)
    req = sam_request()
    att = minted(gov, req, {"events": EVENTS}, reservation_slot="deadbeef#0")
    assert "no_matching_reservation_in_ledger" in metahttp.attestation_problems(att, request=req, payload={"events": EVENTS}, governor=gov)
    req2 = sam_request("req-prov-007")
    att2 = minted(gov, req2, {"events": EVENTS})
    p = Path(gov.config.ledger_path)
    p.write_text(p.read_text().replace('"reserve"', '"reserVe"', 1))                       # tampered ledger
    assert metahttp.attestation_problems(att2, request=req2, payload={"events": EVENTS}, governor=gov) == ["ledger_unreadable_or_tampered"] or "ledger_unreadable_or_tampered" in metahttp.attestation_problems(att2, request=req2, payload={"events": EVENTS}, governor=gov)
    gov_no_ledger = SpendGovernor(GovernorConfig(authorized_payload_digests=gov.config.authorized_payload_digests))
    assert "no_ledger" in metahttp.attestation_problems(att2, request=req2, payload={"events": EVENTS}, governor=gov_no_ledger)


def test_F2_restart_between_stages_an_attestation_cannot_cross_a_process_boundary(tmp_path):
    body, gov, auth = sam_setup(tmp_path)
    req = sam_request()
    att = minted(gov, req, {"events": EVENTS})
    dump = {k: getattr(att, k) for k in metahttp.Attestation.__slots__[:-1]}
    dump["endpoint"], dump["stages"] = list(dump["endpoint"]), [list(s) for s in dump["stages"]]
    src = ("import json,sys; sys.path.insert(0, sys.argv[1]); from mineguard import metahttp as m; d=json.loads(sys.argv[2]); d['endpoint']=tuple(d['endpoint']); d['stages']=tuple(tuple(s) for s in d['stages']); "
           "a=m.Attestation(**d); print(json.dumps(m.attestation_problems(a, request={}, payload={}, governor=None)))")
    out = subprocess.run([sys.executable, "-I", "-c", src, str(Path(metahttp.__file__).resolve().parents[1]), json.dumps(dump)], capture_output=True, text=True, timeout=60)
    assert json.loads(out.stdout) == ["mac_invalid"], out.stderr                              # a new process has a new key: the old attestation certifies nothing
    # the governor, by contrast, survives: an unsettled reservation is replayed as spend
    gov2 = SpendGovernor(cfg(tmp_path, body))
    assert gov2.ledger_error is None and Decimal(gov2.snapshot()["locally_estimated_usd"]) > 0


def test_F2_a_failed_or_timed_out_call_leaves_no_attestation_and_a_late_cancel_discards_it(tmp_path, no_external_dns):
    body, gov, auth = sam_setup(tmp_path)
    srv = Srv([(500, {}, b"{}", 0)])
    try:
        inner = TestHttpsTransport(f"http://127.0.0.1:{srv.port}/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized=auth, accept_sse=True, allow_loopback_http=True)
        out = metaclient.SamClient(EndpointBoundGuard(inner, gov, lambda: KEY)).first_call(URL, "person", "req-f2-0001", width=640, height=480, zones=ZONES)
        assert out["outcome"] == "DENIED" and out["evidence"] is None and out["real_meta_call_completed"] is False
    finally:
        srv.close()


def _muse_setup(tmp_path, incident=None, model="meta-models/x-y", max_tokens=500, key="req-muse-p01"):
    inc = incident or _INC
    view = M.explain.muse_view(inc)
    body = M.build_body(view, model, max_tokens)
    gov = SpendGovernor(cfg(tmp_path, body, provider=M.PROVIDER_ID, model=model, price=Decimal("0.001")))
    req = M.build_governed_request(view, model, key, max_tokens)
    good = {"incertidumbres": ["La geometria es en pixeles de imagen."], "pasos_revision_humana": ["Revisar los eventos contra la fuente original."]}
    payload = {"choices": [{"message": {"content": json.dumps({"resumen": "La regla determinista dejo la zona zone_1 en estado RED con 1 evento.", **good})}}]}
    r = gov.reserve(req["request_key"], req["model"], req["provider"], req["est_tokens"], 1)
    gov.mark_unknown(r)
    att = metahttp._mint(provider=M.PROVIDER_ID, model=model, endpoint=metahttp.OFFICIAL_ENDPOINTS[M.PROVIDER_ID], request_digest=costgov.raw_digest(req["body"]),
                         response_digest=costgov.raw_digest(payload), request_key=req["request_key"], reservation_slot=r.slot, stages=stages(), tls={**GOOD_TLS, "hostname": "inference.baseten.co"})
    return inc, view, gov, req, payload, att


def test_R503_a_real_muse_label_is_bound_to_this_incident_model_view_and_response_and_is_single_use(tmp_path):
    inc, view, gov, req, payload, att = _muse_setup(tmp_path)
    for forged in (att, att.public(), "x", 5, object()):
        with pytest.raises(ValueError):
            M.compose(inc, payload, model="meta-models/x-y", redemption=forged)               # an attestation, a dict or a token is not a redemption
    red, problems = metahttp.redeem(att, request=req, payload=payload, governor=gov)
    assert problems == [] and red is not None
    other_payload = json.loads(json.dumps(payload))
    other_payload["choices"][0]["message"]["content"] = other_payload["choices"][0]["message"]["content"].replace("1 evento", "2 eventos")
    other_inc = {**_INC, "incident_id": "alt_other", "identity": {**_INC["identity"], "event_id": "evt_zz99"}}
    other_view_inc = {**_INC, "unknown_intervals": {"intervals": [{"start_us": 0, "end_us": 100_000, "cause": "identity_unavailable"}]}}     # different facts view
    reordered = {**_INC, "risk": {**_INC["risk"], "rule_id": "R_OTHER"}}
    for label, kwargs, code in [("another response", dict(incident=inc, resp_body=other_payload, model="meta-models/x-y"), "response_not_the_redeemed_one"),
                                ("another incident", dict(incident=other_inc, resp_body=payload, model="meta-models/x-y"), "request_not_the_redeemed_one"),
                                ("another facts view", dict(incident=other_view_inc, resp_body=payload, model="meta-models/x-y"), "request_not_the_redeemed_one"),
                                ("another rule/evidence", dict(incident=reordered, resp_body=payload, model="meta-models/x-y"), "request_not_the_redeemed_one"),
                                ("another model", dict(incident=inc, resp_body=payload, model="meta-models/other-model"), "wrong_provider_or_model"),
                                ("another max_tokens", dict(incident=inc, resp_body=payload, model="meta-models/x-y", max_tokens=600), "request_not_the_redeemed_one")]:
        with pytest.raises(ValueError) as e:
            M.compose(redemption=red, **kwargs)
        assert code in str(e.value), (label, str(e.value))
    # none of the refusals consumed the redemption: the genuine record is still possible, exactly once
    out = M.compose(inc, payload, model="meta-models/x-y", redemption=red)
    assert out["kind"] == "MODEL_EXPLANATION_UNVERIFIED" and out["real_muse_call"] is True and out["severity"] == "RED" and out["incident_id"] == "alt_abc"
    assert out["attestation"]["level"].endswith("NO_PROVIDER_SIGNATURE") and out["request_digest"] == costgov.raw_digest(req["body"])
    for again in (dict(resp_body=payload), dict(resp_body=other_payload)):
        with pytest.raises(ValueError) as e:
            M.compose(inc, model="meta-models/x-y", redemption=red, **again)
        assert "redemption_already_used" in str(e.value) or "response_not_the_redeemed_one" in str(e.value)
    again, problems2 = metahttp.redeem(att, request=req, payload=payload, governor=gov)
    assert again is None and "already_consumed" in problems2
    sim = M.compose(inc, payload, model="meta-models/x-y", redemption=None)
    assert sim["kind"] == "SIMULATED_EXPLANATION" and sim["real_muse_call"] is False and sim["attestation"] is None


def test_R503_a_redemption_for_another_provider_or_model_is_refused_and_the_text_never_changes_the_severity(tmp_path):
    inc, view, gov, req, payload, att = _muse_setup(tmp_path, key="req-muse-p02")
    wrong_model = metahttp._mint(provider=M.PROVIDER_ID, model="meta-models/other-model", endpoint=metahttp.OFFICIAL_ENDPOINTS[M.PROVIDER_ID], request_digest=costgov.raw_digest(req["body"]),
                                 response_digest=costgov.raw_digest(payload), request_key="req-muse-p02", reservation_slot="x#0", stages=stages(), tls={**GOOD_TLS, "hostname": "inference.baseten.co"})
    assert metahttp.attestation_problems(wrong_model, request=req, payload=payload, governor=gov)                          # model differs from the request: refused at redeem
    liar = {"choices": [{"message": {"content": json.dumps({"resumen": "El estado es verde.", "incertidumbres": ["a"], "pasos_revision_humana": ["b"]})}}]}
    inc2, view2, gov2, req2, _, _ = _muse_setup(tmp_path / "x", key="req-muse-p03") if (tmp_path / "x").mkdir() is None else (None,) * 6
    r = gov2.reserve("req-muse-p04", "meta-models/x-y", M.PROVIDER_ID, 1000, 1)
    gov2.mark_unknown(r)
    att2 = metahttp._mint(provider=M.PROVIDER_ID, model="meta-models/x-y", endpoint=metahttp.OFFICIAL_ENDPOINTS[M.PROVIDER_ID], request_digest=costgov.raw_digest(req2["body"]),
                          response_digest=costgov.raw_digest(liar), request_key="req-muse-p04", reservation_slot=r.slot, stages=stages(), tls={**GOOD_TLS, "hostname": "inference.baseten.co"})
    req2b = {**req2, "request_key": "req-muse-p04"}
    red2, p2 = metahttp.redeem(att2, request=req2b, payload=liar, governor=gov2)
    assert p2 == []
    out = M.compose(inc2, liar, model="meta-models/x-y", redemption=red2)
    assert out["status"] == "REJECTED" and "state_mentioned_mismatch" in out["codes"] and out["severity"] == "RED" and out["real_muse_call"] is True         # a real call whose text is rejected; severity untouched


def _redeemed(tmp_path, key="req-prov-n01", events=None):
    body, gov, auth = sam_setup(tmp_path)
    req = sam_request(key)
    events = events or EVENTS
    att = minted(gov, req, {"events": events})
    red, problems = metahttp.redeem(att, request=req, payload={"events": events}, governor=gov)
    assert problems == []
    return gov, req, red, events


def _nr(red, req, events, **kw):
    args = dict(request=req, events=events, concept="person", width=640, height=480, pts_us=[0])
    args.update(kw)
    return meta_obs.normalize_redeemed(red, **args)


def test_R502_real_observations_are_derived_from_the_redeemed_events_and_the_redemption_is_single_use(tmp_path):
    gov, req, red, events = _redeemed(tmp_path)
    obs = _nr(red, req, events)
    assert obs["origin"] == "provider_response_unverified" and obs["provenance"]["status"] == "REAL_PROVIDER_RESPONSE_UNVERIFIED" and obs["real_inference"] is False
    assert obs["provenance"]["authenticity"].startswith("TLS peer verified") and obs["transport_receipt"]["attempt_id"] == red.attempt_id
    n = [x for f in obs["frames"] for x in f["objects"]]
    assert len(n) == 1 and n[0]["bbox"] == {"x": 10, "y": 20, "w": 100, "h": 200}          # exactly what the redeemed events say
    with pytest.raises(meta_obs.NormalizationRefused) as e:
        _nr(red, req, events)                                                                  # duplicate
    assert "redemption_already_used" in e.value.code


def test_R502_fabricated_altered_or_reordered_events_cannot_use_a_valid_redemption(tmp_path):
    other = [{"type": "response.output_text.delta", "delta": LINE.replace("x1=10;y1=20;x2=110;y2=220", "x1=300;y1=300;x2=400;y2=400")}, {"type": "response.completed"}]
    extra = [*EVENTS[:1], {"type": "response.output_text.delta", "delta": LINE.replace("<0f>1", "<0f>7")}, *EVENTS[1:]]
    incomplete = EVENTS[:1]
    reordered = [EVENTS[1], EVENTS[0]]
    for label, ev in [("another response", other), ("added observation", extra), ("incomplete response", incomplete), ("reordered events", reordered), ("empty", []), ("not a list", "x")]:
        gov, req, red, events = _redeemed(tmp_path / label.replace(" ", "_"), key="req-prov-n02") if (tmp_path / label.replace(" ", "_")).mkdir() is None else (None,) * 4
        with pytest.raises(meta_obs.NormalizationRefused) as e:
            _nr(red, req, ev)
        assert "response_not_the_redeemed_one" in e.value.code or "request_does_not_match" in e.value.code, (label, e.value.code)
        obs = _nr(red, req, events)                                                            # the refusal did not consume it: only the genuine events can
        assert obs["provenance"]["status"] == "REAL_PROVIDER_RESPONSE_UNVERIFIED", label


def test_R502_the_request_the_concept_and_the_provider_must_match_the_redeemed_call(tmp_path):
    gov, req, red, events = _redeemed(tmp_path)
    for label, kw in [("another concept", dict(concept="truck")), ("another request body", dict(request={**req, "body": S.build_body(URL, "truck")})), ("another model", dict(request={**req, "model": "sam-3"})),
                      ("another provider", dict(request={**req, "provider": M.PROVIDER_ID})), ("no request", dict(request=None)), ("request without body", dict(request={"provider": req["provider"], "model": req["model"]}))]:
        with pytest.raises(meta_obs.NormalizationRefused):
            _nr(red, req, events, **kw)
    for bad in (None, att_dict := {"attempt_id": "x"}, "x", 5, object()):
        with pytest.raises(meta_obs.NormalizationRefused) as e:
            _nr(bad, req, events)
        assert e.value.code == "real_label_requires_a_redemption_for_this_provider_and_model"
    assert _nr(red, req, events)["origin"] == "provider_response_unverified"


def test_R502_the_public_normalize_and_the_pipeline_expose_no_way_to_hand_in_observations_for_a_real_label(tmp_path):
    import inspect
    assert "frames" not in inspect.signature(meta_obs.normalize_redeemed).parameters and "fixture" not in inspect.signature(meta_obs.normalize_redeemed).parameters
    sig = inspect.signature(meta_pipeline.build_evidence_multi)
    assert "observations" not in sig.parameters and "frames" not in sig.parameters and "fixture" not in sig.parameters
    gov, req, red, events = _redeemed(tmp_path)
    fixture = meta_obs.from_wire(S.parse_stream(events, 640, 480, 1), "person", 640, 480, [0], simulated=False, receipt=red)
    fixture["payload_digest"] = red.response_digest
    with pytest.raises(meta_obs.NormalizationRefused) as e:
        meta_obs.normalize(fixture)                                                            # the review's reproduction: valid redemption + hand-built frames
    assert e.value.code == "real_label_not_accepted_here"


_INC = {"incident_id": "alt_abc", "classification": "synthetic", "identity": {"event_id": "evt_ab12", "zone_id": "zone_1"},
        "risk": {"severity": "RED", "rule_id": "R_TEST", "engine_version": "mg-risk-kernel/2.0.3", "semantics_version": "1.0"}, "observed": {"start_us": 1_200_000, "end_us": None},
        "provenance": {"provider": {"kind": "scripted_simulation"}, "simulation": True}, "unknown_intervals": {"intervals": []}, "timeline": {"lifecycle": {"recorded": True}}}


# ===== F-3 ==========================================================================================================================================================
def guard_for(tmp_path, srv, *, body=None, max_uses=1, model=S.MODEL, provider=S.PROVIDER_ID, gov_provider=None, **kw):
    body = body or S.build_body(URL, "person")
    gov = SpendGovernor(cfg(tmp_path, body, provider=gov_provider or provider, model=model, **kw))
    inner = TestHttpsTransport(f"http://127.0.0.1:{srv.port}/v1", path="/responses", provider=provider, governor=gov, authorized={(provider, model, costgov.raw_digest(body))},
                               accept_sse=True, max_uses=max_uses, allow_loopback_http=True)
    return gov, inner, EndpointBoundGuard(inner, gov, lambda: KEY)


def test_F3_a_changed_outer_provider_is_refused_before_any_reservation_and_nothing_is_sent(tmp_path, no_external_dns):
    srv = Srv([(200, {}, OK_STREAM, 0)])
    try:
        body = S.build_body(URL, "person")
        # the governor only has a budget for the OTHER provider: the review's reproduction
        gov = SpendGovernor(cfg(tmp_path, body, provider=M.PROVIDER_ID))
        inner = TestHttpsTransport(f"http://127.0.0.1:{srv.port}/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized={(S.PROVIDER_ID, S.MODEL, costgov.raw_digest(body))},
                                   accept_sse=True, allow_loopback_http=True)
        g = EndpointBoundGuard(inner, gov, lambda: KEY)
        req = {**S.build_governed_request(URL, "person", "req-f3-0001"), "provider": M.PROVIDER_ID}
        with pytest.raises(Denied) as e:
            g.send(req)
        assert e.value.code == "provider_endpoint_mismatch" and srv.hits == [] and gov.snapshot()["real_requests_reserved"] == 0
        assert Path(gov.config.ledger_path).exists() is False or "reserve" not in Path(gov.config.ledger_path).read_text()
        # and the transport itself refuses the same request if the guard is bypassed
        with pytest.raises(Denied):
            inner.send(req, KEY, 5)
        assert srv.hits == []
    finally:
        srv.close()


@pytest.mark.parametrize("mut,code", [({"model": "sam-3.1x"}, "binding_not_authorized"), ({"body": "stream"}, "binding_not_authorized"), ({"body": "model"}, "binding_not_authorized"),
                                      ({"body": "text"}, "binding_not_authorized"), ({"provider": "other"}, "provider_endpoint_mismatch")])
def test_F3_model_payload_and_provider_changes_after_authorization_are_refused_before_reservation(tmp_path, no_external_dns, mut, code):
    srv = Srv([(200, {}, OK_STREAM, 0)])
    try:
        gov, inner, g = guard_for(tmp_path, srv)
        req = S.build_governed_request(URL, "person", "req-f3-0002")
        if mut.get("body") == "stream":
            req["body"]["stream"] = False
        elif mut.get("body") == "model":
            req["body"]["model"] = "sam-3.1x"
        elif mut.get("body") == "text":
            req["body"]["input"][0]["content"][0]["text"] = "truck"
        elif "model" in mut:
            req["model"] = mut["model"]
        elif "provider" in mut:
            req["provider"] = mut["provider"]
        with pytest.raises(Denied) as e:
            g.send(req)
        assert e.value.code == code and srv.hits == [] and gov.snapshot()["real_requests_reserved"] == 0
    finally:
        srv.close()


def test_F3_the_authorized_binding_is_single_use_and_a_reservation_cannot_be_reused_for_another_body(tmp_path, no_external_dns):
    srv = Srv([(200, {}, OK_STREAM, 0)])
    try:
        gov, inner, g = guard_for(tmp_path, srv, max_uses=1)
        assert metaclient.SamClient(g).first_call(URL, "person", "req-f3-0003", width=640, height=480, zones=ZONES)["outcome"] == "OK"
        again = metaclient.SamClient(g).first_call(URL, "person", "req-f3-0004", width=640, height=480, zones=ZONES)
        assert again["code"] == "binding_exhausted" and len(srv.hits) == 1
        # a reservation made by the governor for one request does not authorize the transport to send a different body
        gov2, inner2, g2 = guard_for(tmp_path / "x", srv, max_uses=5) if (tmp_path / "x").mkdir() is None else (None, None, None)
        req = S.build_governed_request(URL, "person", "req-f3-0005")
        gov2.reserve(req["request_key"], req["model"], req["provider"], 0, 1)
        other = {**req, "body": S.build_body(URL, "truck")}
        with pytest.raises(Denied):
            inner2.send(other, KEY, 5)
        assert len(srv.hits) == 1
    finally:
        srv.close()


def test_F3_a_redirect_is_never_followed_and_the_alternative_destination_receives_nothing(tmp_path, no_external_dns):
    other = Srv([(200, {}, OK_STREAM, 0)])
    srv = Srv([(302, {"Location": f"http://127.0.0.1:{other.port}/v1/responses"}, b"", 0)])
    try:
        gov, inner, g = guard_for(tmp_path, srv)
        out = metaclient.SamClient(g).first_call(URL, "person", "req-f3-0006", width=640, height=480, zones=ZONES)
        assert out["outcome"] == "DENIED" and out["code"] == "unexpected_provider_response" and out["provider_error"]["code"] == "redirect_refused" and out["spend_counted"] is True
        assert len(srv.hits) == 1 and other.hits == []
        for code in (301, 303, 307, 308):
            srv.script, srv.hits = [(code, {"Location": "https://api.meta.ai/v1/responses"}, b"", 0)], []
            gov.config  # same governor; a new key
            r = metaclient.SamClient(g).first_call(URL, "person", f"req-f3-1{code}", width=640, height=480, zones=ZONES)
            assert r["code"] in ("unexpected_provider_response", "binding_exhausted")
    finally:
        srv.close()
        other.close()


@pytest.mark.parametrize("kw", [{"path": "/responses?x=1"}, {"path": "/a/../b"}, {"path": "//responses"}, {"path": "/%2e%2e/x"}, {"path": "/res ponses"}, {"path": ""}, {"path": "responses"},
                                {"base_url": "http://127.0.0.1:1/v1?x=1"}, {"base_url": "http://127.0.0.1:1/v1#f"}, {"base_url": "http://u:p@127.0.0.1:1/v1"}, {"base_url": "http://example.com/v1"},
                                {"base_url": "https://[::1/v1"}, {"base_url": "ftp://127.0.0.1/v1"}])
def test_F3_destination_strings_with_query_dots_userinfo_or_odd_schemes_are_refused(tmp_path, kw):
    body, gov, auth = sam_setup(tmp_path)
    args = dict(base_url="http://127.0.0.1:1/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized=auth, accept_sse=True, allow_loopback_http=True)
    args.update(kw)
    with pytest.raises(ValueError):
        TestHttpsTransport(**args)


def test_F3_the_destination_is_fixed_after_construction_and_production_endpoints_are_constants(tmp_path):
    body, gov, auth = sam_setup(tmp_path)
    t = TestHttpsTransport("http://127.0.0.1:1/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized=auth, accept_sse=True, allow_loopback_http=True)
    for attr in ("host", "port", "path", "base_path", "scheme", "provider", "authorized", "_governor", "_factory"):
        with pytest.raises(AttributeError):
            setattr(t, attr, "x")
    b = ProductionHttpsTransport(M.PROVIDER_ID, governor=gov, authorized={(M.PROVIDER_ID, "m/x-y", "d")})
    assert (b.host, b.base_path, b.path) == metahttp.OFFICIAL_ENDPOINTS[M.PROVIDER_ID] == ("inference.baseten.co", "/v1", "/chat/completions")
    with pytest.raises(ValueError):
        ProductionHttpsTransport(S.PROVIDER_ID, governor=gov, authorized=[])                    # no authorization bindings -> no transport
    with pytest.raises(ValueError):
        ProductionHttpsTransport(S.PROVIDER_ID, governor=gov, authorized=auth, max_uses=0)


def test_F3_concurrent_requests_on_a_single_authorization_send_exactly_one(tmp_path, no_external_dns):
    srv = Srv([(200, {}, OK_STREAM, 0.3)])
    try:
        gov, inner, g = guard_for(tmp_path, srv, max_uses=1, max_requests=1, concurrency=1)
        outs = []
        ts = [threading.Thread(target=lambda i=i: outs.append(metaclient.SamClient(g).first_call(URL, "person", f"req-f3-r{i:03d}", width=640, height=480, zones=ZONES))) for i in range(12)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert len(srv.hits) == 1 and sum(1 for o in outs if o["outcome"] == "OK") == 1 and len(outs) == 12
    finally:
        srv.close()


def test_F3_the_matching_provider_model_payload_and_endpoint_is_sent_exactly_once(tmp_path, no_external_dns):
    srv = Srv([(200, {}, OK_STREAM, 0)])
    try:
        gov, inner, g = guard_for(tmp_path, srv)
        out = metaclient.SamClient(g).first_call(URL, "person", "req-f3-0007", width=640, height=480, zones=ZONES)
        assert out["outcome"] == "OK" and len(srv.hits) == 1 and srv.hits[0]["path"] == "/v1/responses"
        assert json.loads(srv.hits[0]["body"]) == S.build_body(URL, "person")
    finally:
        srv.close()


# ===== F-4 ==========================================================================================================================================================
@pytest.mark.parametrize("cred,codes", [("", {"empty"}), (None, {"not_a_string"}), (5, {"not_a_string"}), ("abc", {"too_short"}), ("a" * 513, {"too_long"}), ("has space in key", {"whitespace"}),
                                        (" LLM_1_abcdefgh", {"whitespace"}), ("LLM_1_abcdefgh ", {"whitespace"}), ("LLM_1_abc\tdefgh", {"whitespace", "control_character"}),
                                        ("LLM_1_abcdefgh\r\nX-Evil: 1", {"line_break", "control_character", "whitespace"}), ("LLM_1_abcdefgh\n", {"line_break", "control_character", "whitespace"}),
                                        ("LLM_1_abcdefgh\r", {"line_break", "control_character", "whitespace"}), ("LLM_1_abc\x00defgh", {"control_character"}), ("LLM_1_abc\x7fdefgh", {"control_character"}),
                                        ("LLM_1_abcdéfgh", {"non_ascii"}), ("LLM_1_abc‮defgh", {"non_ascii"}), ('LLM_1_"abcdefgh"', {"not_token68"}), ("LLM_1_abc,defgh", {"not_token68"}),
                                        ("LLM_1_abc;defgh", {"not_token68"}), ("LLM_1_abc=defgh", {"not_token68"}), ("Bearer abcdefghi", {"whitespace"}), ("LLM_1_abc\\defgh", {"not_token68"})])
def test_F4_malformed_credentials_are_rejected_with_codes_only(cred, codes):
    p = metahttp.credential_problems(cred)
    assert p and codes & set(p), p
    assert all(isinstance(x, str) and "abc" not in x for x in p)                                # the value never appears in a problem code


@pytest.mark.parametrize("cred", ["LLM_12_abcDEF123xyz", "abcd1234.EFGHijkl", "AbCdEfGh12345678==", "a~b+c/d-e.f_g12345", "x" * 512, "A" * 8])
def test_F4_the_documented_bearer_syntax_is_accepted_without_assuming_a_provider_prefix(cred):
    assert metahttp.credential_problems(cred) == []


@pytest.mark.parametrize("bad", ["has space in key", KEY + "\r\nX-Evil: 1", " " + KEY, KEY + "\n", "LLM_1_abc\x00defgh", "short", "LLM_1_abcdéfgh", ""])
def test_F4_an_invalid_credential_never_reaches_the_connection_and_is_never_repaired(tmp_path, no_external_dns, bad):
    body, gov, auth = sam_setup(tmp_path)
    _Fake.made = 0
    inner = TestHttpsTransport("https://example.com/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized=auth, accept_sse=True, connection_factory=lambda *a: _Fake())
    req = sam_request("req-f4-0001")
    gov.reserve(req["request_key"], req["model"], req["provider"], 0, 1)
    with pytest.raises(Denied) as e:
        inner.send(req, bad, 5)                                                                  # the raw transport re-validates (the review's reproduction)
    assert e.value.code == "not_governed" and _Fake.made == 0
    g = EndpointBoundGuard(inner, gov, lambda: bad)
    out = metaclient.SamClient(g).first_call(URL, "person", "req-f4-0002", width=640, height=480, zones=ZONES)
    assert out["outcome"] == "DENIED" and out["code"] == "credentials_missing_or_malformed" and _Fake.made == 0 and g.last_credential_problems
    assert bad.strip() not in json.dumps(out) or not bad.strip()


def test_F4_a_guard_never_returns_logs_or_stores_a_credential_value(tmp_path):
    body, gov, auth = sam_setup(tmp_path)
    inner = TestHttpsTransport("https://example.com/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized=auth, accept_sse=True, connection_factory=lambda *a: _Fake())
    g = EndpointBoundGuard(inner, gov, lambda: KEY + " bad")
    r = metaclient.SamClient(g).first_call(URL, "person", "req-f4-0003", width=640, height=480, zones=ZONES)
    dumped = json.dumps(r) + json.dumps(g.last_credential_problems) + json.dumps(gov.audit, default=str) + Path(gov.config.ledger_path).read_text() if Path(gov.config.ledger_path).exists() else json.dumps(r)
    assert KEY not in dumped
    assert all(KEY not in repr(v) for v in vars(g).values() if not callable(v) and not isinstance(v, (type(g.inner), type(gov))))


# ===== review 1 of Recovery 05: B-2 (race on max_uses) and B-3 (stdlib debug logging) =======================================================================================
def test_B2_max_uses_is_claimed_atomically_so_concurrent_requests_with_different_keys_dispatch_once(tmp_path, no_external_dns):
    srv = Srv([(200, {}, OK_STREAM, 0.4)])
    try:
        body = S.build_body(URL, "person")
        gov = SpendGovernor(cfg(tmp_path, body, max_requests=20, concurrency=20))             # the governor alone would allow them all: only the binding stops them
        inner = TestHttpsTransport(f"http://127.0.0.1:{srv.port}/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized={(S.PROVIDER_ID, S.MODEL, costgov.raw_digest(body))},
                                   accept_sse=True, max_uses=1, allow_loopback_http=True)
        g = EndpointBoundGuard(inner, gov, lambda: KEY)
        barrier = threading.Barrier(12)
        outs = []

        def go(i):
            barrier.wait()
            outs.append(metaclient.SamClient(g).first_call(URL, "person", f"req-b2-{i:04d}", width=640, height=480, zones=ZONES))
        ts = [threading.Thread(target=go, args=(i,)) for i in range(12)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert len(srv.hits) == 1 and sum(1 for o in outs if o["outcome"] == "OK") == 1 and sum(1 for o in outs if o.get("code") == "binding_exhausted") == 11
        assert gov.snapshot()["real_requests_reserved"] == 1                                    # the losers were refused BEFORE reserving: no spend accounted for them
    finally:
        srv.close()


def test_B2_a_claim_without_dispatch_is_released_and_a_direct_send_has_no_claim(tmp_path, no_external_dns):
    srv = Srv([(200, {}, OK_STREAM, 0)])
    try:
        body = S.build_body(URL, "person")
        gov = SpendGovernor(cfg(tmp_path, body, live=False))                                    # live disabled: the governor refuses AFTER the claim was taken
        inner = TestHttpsTransport(f"http://127.0.0.1:{srv.port}/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized={(S.PROVIDER_ID, S.MODEL, costgov.raw_digest(body))},
                                   accept_sse=True, max_uses=1, allow_loopback_http=True)
        g = EndpointBoundGuard(inner, gov, lambda: KEY)
        for i in range(3):                                                                      # three refusals do not consume the single use
            with pytest.raises(Denied) as e:
                g.send(sam_request(f"req-b2-r{i:03d}"))
            assert e.value.code == "live_provider_disabled"
        assert inner._pending == {k: 0 for k in inner._pending} and inner._uses == {} and srv.hits == []
        with pytest.raises(Denied) as e:                                                         # a direct call has no claim at all
            inner.send(sam_request("req-b2-r099"), KEY, 5)
        assert e.value.code == "not_governed" and srv.hits == []
    finally:
        srv.close()


def test_B3_the_stdlib_debug_output_cannot_print_the_authorization_header(tmp_path, monkeypatch, capsys, no_external_dns):
    monkeypatch.setattr(http.client.HTTPConnection, "debuglevel", 1)
    monkeypatch.setattr(http.client.HTTPSConnection, "debuglevel", 1)
    srv = Srv([(200, {}, OK_STREAM, 0)])
    try:
        gov, inner, g = guard_for(tmp_path, srv)
        out = metaclient.SamClient(g).first_call(URL, "person", "req-b3-0001", width=640, height=480, zones=ZONES)
        assert out["outcome"] == "OK" and len(srv.hits) == 1
        captured = capsys.readouterr()
        assert KEY not in captured.out + captured.err and "Authorization" not in captured.out + captured.err
    finally:
        srv.close()


def test_B3_a_connection_that_insists_on_debug_logging_is_refused_before_the_request(tmp_path, no_external_dns):
    body, gov, auth = sam_setup(tmp_path)
    made = []

    class Chatty:
        debuglevel = 2

        def __init__(self, *a, **k): made.append(1)
        def set_debuglevel(self, n): pass                         # ignores the request to be quiet
        def request(self, *a, **k): raise AssertionError("must not be reached")
        def close(self): pass
    inner = TestHttpsTransport("https://example.com/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized=auth, accept_sse=True, connection_factory=lambda *a: Chatty())
    g = EndpointBoundGuard(inner, gov, lambda: KEY)
    out = metaclient.SamClient(g).first_call(URL, "person", "req-b3-0002", width=640, height=480, zones=ZONES)
    assert out["outcome"] == "TRANSPORT_FAULT" and "connection_debug_logging_enabled" in out["code"]


# ===== Recovery 06: MG-R5-01 (immutable endpoints), MG-R5-04 (Retry-After / error exposure), MG-R5-05 (vectors for the surviving mutants) ==================================
def cfg_both(tmp_path, body, model=S.MODEL):
    return GovernorConfig(live_provider_enabled=True, external_media_upload_enabled=True, max_real_requests=20, max_authorized_spend_usd=Decimal(5), price_usd_per_request=((model, S.PRICE_PER_IMAGE_USD),),
                          provider_budget_usd=((S.PROVIDER_ID, Decimal(1)), (M.PROVIDER_ID, Decimal(1))), max_tokens=100000, max_concurrency=5,
                          authorized_payload_digests=frozenset({costgov.raw_digest(body)}), ledger_path=str(tmp_path / "both.jsonl"), request_timeout_s=5)


def test_R501_the_endpoint_table_is_immutable_and_nothing_mutable_is_consulted_for_trust():
    assert isinstance(metahttp._OFFICIAL_TABLE, tuple) and all(isinstance(r, tuple) for r in metahttp._OFFICIAL_TABLE)
    with pytest.raises(TypeError):
        metahttp.OFFICIAL_ENDPOINTS["meta_sam31"] = ("attacker.example", "/v1", "/responses")          # the review's reproduction (in-place mutation)
    with pytest.raises(TypeError):
        del metahttp.OFFICIAL_ENDPOINTS["meta_sam31"]
    for bad in ("update", "pop", "setdefault", "clear"):
        assert not hasattr(metahttp.OFFICIAL_ENDPOINTS, bad)
    assert metahttp.OFFICIAL_ENDPOINTS["meta_sam31"] == ("api.meta.ai", "/v1", "/responses")


def test_R501_rebinding_the_public_view_or_the_table_before_constructing_does_not_change_the_destination(tmp_path, monkeypatch):
    body, gov, auth = sam_setup(tmp_path)
    evil = {"meta_sam31": ("attacker.example", "/v1", "/responses"), "evil_provider": ("attacker.example", "/v1", "/x")}
    monkeypatch.setattr(metahttp, "OFFICIAL_ENDPOINTS", evil)
    monkeypatch.setattr(metahttp, "_OFFICIAL_TABLE", (("meta_sam31", "attacker.example", "/v1", "/responses", True),))
    t = ProductionHttpsTransport(S.PROVIDER_ID, governor=gov, authorized=auth)
    assert (t.host, t.base_path, t.path, t.scheme, t.port) == ("api.meta.ai", "/v1", "/responses", "https", None)          # captured at import
    with pytest.raises(ValueError):
        ProductionHttpsTransport("evil_provider", governor=gov, authorized={("evil_provider", "m", "d")})
    req = sam_request("req-r501-001")
    att = minted(gov, req, {"events": EVENTS}, endpoint=("attacker.example", "/v1", "/responses"))
    assert "endpoint_not_the_provider_endpoint" in metahttp.attestation_problems(att, request=req, payload={"events": EVENTS}, governor=gov)    # the check uses its own captured copy


def test_R501_a_transport_whose_destination_was_forced_to_another_host_is_refused_before_any_connection(tmp_path, no_external_dns):
    body, gov, auth = sam_setup(tmp_path)
    t = ProductionHttpsTransport(S.PROVIDER_ID, governor=gov, authorized=auth)
    g = EndpointBoundGuard(t, gov, lambda: KEY)
    for attr, val in (("host", "attacker.example"), ("path", "/other"), ("base_path", "/v2"), ("scheme", "http"), ("port", 8443)):
        with pytest.raises(AttributeError):
            setattr(t, attr, val)                                                               # frozen
    object.__setattr__(t, "host", "attacker.example")                                           # white-box: even a forced change is caught by the dispatch closure
    _Fake.made = 0
    with pytest.raises(metahttp.TransportFault) as e:
        g.send(sam_request("req-r501-002"))
    assert "unexpected_transport_type_or_destination" in str(e.value) and _Fake.made == 0


def test_R501_alternative_endpoint_host_and_injected_destinations_have_no_entry_point():
    import inspect
    assert list(inspect.signature(ProductionHttpsTransport.__init__).parameters) == ["self", "provider", "governor", "authorized", "max_uses"]
    for provider in ("meta_sam31", "baseten_muse_spark_open"):
        for evil in ("https://attacker.example/v1", "attacker.example", "api.meta.ai:444", "../x", "meta_sam31/../x", "META_SAM31", " meta_sam31", None, 5):
            if evil == provider:
                continue
            with pytest.raises((ValueError, TypeError)):
                ProductionHttpsTransport(evil, governor=SpendGovernor(GovernorConfig()), authorized={(provider, "m", "d")})


@pytest.mark.parametrize("status", [429, 503, 418, 500])
@pytest.mark.parametrize("raw,want", [("7", 7), ("0", 0), ("120", 120), ("999999", 999999), ("1000000", None), ("-1", None), ("1e3", None), ("7.5", None), ("abc", None), ("", None),
                                      ("Wed, 21 Oct 2026 07:28:00 GMT", None), (KEY, None), (KEY[:12], None), ("7 " + KEY, None), ("x" * 5000, None), ("0x10", None), ("７", None)])
def test_R504_retry_after_is_validated_control_data_never_arbitrary_header_text(tmp_path, no_external_dns, status, raw, want):
    srv = Srv([(status, {"Retry-After": raw.encode("latin-1", "ignore").decode("latin-1")} if raw.isascii() else {}, json.dumps({"error": {"message": "slow", "type": "rate_limit_error", "code": "rate_limit_exceeded"}}).encode(), 0)])
    try:
        gov, inner, g = guard_for(tmp_path, srv)
        out = metaclient.SamClient(g).first_call(URL, "person", "req-r504-0001", width=640, height=480, zones=ZONES)
        assert out["outcome"] == "DENIED"
        got = out["retry_after"]
        assert got == (want if raw.isascii() else None) and (got is None or isinstance(got, int))
        assert KEY not in json.dumps(out, default=str) and len(srv.hits) == 1                      # zero retries even for 429/503
    finally:
        srv.close()


def test_R504_error_views_expose_only_allow_listed_fields_without_control_characters_or_secrets(tmp_path, no_external_dns):
    body = {"error": {"message": "line1\r\nSet-Cookie: x\x07\x00end " + "m" * 400, "type": "<script>", "code": "bad code!", "param": KEY[:10], "extra": {"k": "v"}}}
    srv = Srv([(503, {}, json.dumps(body).encode(), 0)])
    try:
        gov, inner, g = guard_for(tmp_path, srv)
        out = metaclient.SamClient(g).first_call(URL, "person", "req-r504-0002", width=640, height=480, zones=ZONES)
        v = out["provider_error"]
        assert set(v) == {"status", "type", "code", "message"} and v["status"] == 503 and v["type"] is None and v["code"] is None
        assert "\r" not in v["message"] and "\n" not in v["message"] and "\x07" not in v["message"] and "\x00" not in v["message"] and len(v["message"]) <= 200
        ok = Srv([(503, {}, json.dumps({"error": {"message": "overloaded", "type": "server_error", "code": "service_overloaded"}}).encode(), 0)])
        try:
            gov2, inner2, g2 = guard_for(tmp_path / "ok", ok) if (tmp_path / "ok").mkdir() is None else (None,) * 3
            v2 = metaclient.SamClient(g2).first_call(URL, "person", "req-r504-0003", width=640, height=480, zones=ZONES)["provider_error"]
            assert v2 == {"status": 503, "type": "server_error", "code": "service_overloaded", "message": "overloaded"}
        finally:
            ok.close()
    finally:
        srv.close()


@pytest.mark.parametrize("where", ["request", "getresponse", "read", "set_debuglevel"])
def test_R504_an_unexpected_exception_never_carries_its_message_or_the_credential_out(tmp_path, no_external_dns, where):
    body, gov, auth = sam_setup(tmp_path)

    class Boom:
        debuglevel = 0

        def __init__(self, *a, **k): pass
        def set_debuglevel(self, n):
            if where == "set_debuglevel": raise RuntimeError("leak " + KEY)
        def request(self, *a, **k):
            if where == "request": raise RuntimeError("leak " + KEY)
        def getresponse(self):
            if where == "getresponse": raise ValueError("leak " + KEY)
            class R:
                status = 200
                def read(self, n=-1): raise KeyError("leak " + KEY)
                def getheader(self, *a): return None
            return R()
        def close(self): pass
    inner = TestHttpsTransport("https://example.com/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized=auth, accept_sse=True, connection_factory=lambda *a: Boom())
    out = metaclient.SamClient(EndpointBoundGuard(inner, gov, lambda: KEY)).first_call(URL, "person", "req-r504-0004", width=640, height=480, zones=ZONES)
    assert out["outcome"] == "TRANSPORT_FAULT" and out["code"].startswith("unexpected_") and KEY not in json.dumps(out, default=str) and "leak" not in json.dumps(out, default=str)


# --- vectors for the three surviving mutants of Recovery 05 (F1i, F2r, F3h) ------------------------------------------------------------------------------------------------------
def test_F1i_a_raw_send_with_a_valid_reservation_but_no_guard_claim_creates_no_connection(tmp_path):
    body, gov, auth = sam_setup(tmp_path)
    _Fake.made = 0
    inner = TestHttpsTransport("https://example.com/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized=auth, accept_sse=True, connection_factory=lambda *a: _Fake())
    req = sam_request("req-f1i-0001")
    gov.reserve(req["request_key"], req["model"], req["provider"], 0, 1)                      # live, authorized body, valid credential, open reservation: ONLY the claim is missing
    with pytest.raises(Denied) as e:
        inner.send(req, KEY, 5)
    assert e.value.code == "not_governed" and _Fake.made == 0


def test_F3h_a_reservation_made_under_another_provider_does_not_authorize_this_transport(tmp_path):
    body = S.build_body(URL, "person")
    gov = SpendGovernor(cfg_both(tmp_path, body))
    _Fake.made = 0
    inner = TestHttpsTransport("https://example.com/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized={(S.PROVIDER_ID, S.MODEL, costgov.raw_digest(body))}, accept_sse=True,
                               connection_factory=lambda *a: _Fake())
    req = sam_request("req-f3h-0001")
    gov.reserve(req["request_key"], req["model"], M.PROVIDER_ID, 0, 1)                         # same key and model, but charged to the OTHER provider's budget
    metahttp._TLS.claim = metahttp._claim_impl(inner, req)
    try:
        with pytest.raises(Denied) as e:
            inner.send(req, KEY, 5)
    finally:
        metahttp._TLS.claim = None
    assert e.value.code == "not_governed" and _Fake.made == 0


def test_F2r_a_redemption_with_correct_fields_but_a_wrong_seal_is_refused(tmp_path):
    body, gov, auth = sam_setup(tmp_path)
    req = sam_request("req-f2r-0001")
    att = minted(gov, req, {"events": EVENTS})
    red, problems = metahttp.redeem(att, request=req, payload={"events": EVENTS}, governor=gov)
    assert problems == []
    fields = {k: getattr(red, k) for k in metahttp.Redemption.__slots__[:-1]}
    for mac in ("0" * 64, "", "f" * 64, red.mac[:-1] + ("0" if red.mac[-1] != "0" else "1")):
        forged = metahttp.Redemption(**{**fields, "mac": mac})
        assert metahttp.use_redemption(forged, response_digest=red.response_digest, request_digest=red.request_digest) == ["redemption_mac_invalid"]
    edited = metahttp.Redemption(**{**fields, "response_digest": "d" * 64})
    assert metahttp.use_redemption(edited, response_digest="d" * 64) == ["redemption_mac_invalid"]
    assert metahttp.use_redemption(red, response_digest=red.response_digest, request_digest=red.request_digest) == []         # the genuine one still works, once


# ===== Recovery 06, review 1: MG-F6-01 (escaped credential), MG-F6-02 (consumption order), MG-F6-04 (vector for F2n) ===========================================================
def _escape_all(text: str) -> str:
    return "".join(f"\\u{ord(c):04x}" for c in text)


@pytest.mark.parametrize("shape", ["json_value", "json_key", "json_nested", "json_list", "sse_delta", "sse_nested", "partial_escape", "error_body"])
def test_F601_a_response_that_echoes_the_credential_with_json_escapes_is_refused(tmp_path, no_external_dns, shape):
    esc = _escape_all(KEY)
    half = KEY[:6] + _escape_all(KEY[6:])
    status, sse = 200, False
    if shape == "json_value":
        payload = ('{"echo":"%s"}' % esc).encode()
    elif shape == "json_key":
        payload = ('{"%s":1}' % esc).encode()
    elif shape == "json_nested":
        payload = ('{"a":{"b":[1,{"c":"x %s y"}]}}' % esc).encode()
    elif shape == "json_list":
        payload = ('{"choices":["%s"]}' % esc).encode()
    elif shape == "sse_delta":
        payload, sse = ('data: {"type":"response.output_text.delta","delta":"%s"}\n\ndata: {"type":"response.completed"}\n\n' % esc).encode(), True
    elif shape == "sse_nested":
        payload, sse = ('data: {"type":"response.completed","response":{"x":["%s"]}}\n\n' % half).encode(), True
    elif shape == "partial_escape":
        payload = ('{"echo":"%s"}' % half).encode()
    else:
        payload, status = ('{"error":{"message":"bad %s","type":"t","code":"c"}}' % esc).encode(), 401
    assert KEY.encode() not in payload                                                       # the literal check alone would NOT see it
    srv = Srv([(status, {}, payload, 0)])
    try:
        if sse:
            body, gov, auth = sam_setup(tmp_path)
            inner = TestHttpsTransport(f"http://127.0.0.1:{srv.port}/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized=auth, accept_sse=True, allow_loopback_http=True)
            out = metaclient.SamClient(EndpointBoundGuard(inner, gov, lambda: KEY)).first_call(URL, "person", "req-f601-0001", width=640, height=480, zones=ZONES)
        else:
            view = M.explain.muse_view(_INC)
            mbody = M.build_body(view, "meta-models/x-y", 500)
            gov = SpendGovernor(cfg(tmp_path, mbody, provider=M.PROVIDER_ID, model="meta-models/x-y", price=Decimal("0.001")))
            inner = TestHttpsTransport(f"http://127.0.0.1:{srv.port}/v1", path="/chat/completions", provider=M.PROVIDER_ID, governor=gov, authorized={(M.PROVIDER_ID, "meta-models/x-y", costgov.raw_digest(mbody))},
                                       allow_loopback_http=True)
            out = M.MuseOpenClient(EndpointBoundGuard(inner, gov, lambda: KEY)).explain(_INC, "meta-models/x-y", "req-f601-0002")
        assert out["outcome"] == "TRANSPORT_FAULT" and "response_echoed_the_credential" in out["code"]
        assert KEY not in json.dumps(out, default=str) and Decimal(gov.snapshot()["locally_estimated_usd"]) > 0
    finally:
        srv.close()


def test_F601_the_secret_scanner_walks_keys_values_lists_and_fails_closed_when_too_deep():
    assert metahttp._secret_in({"a": [1, {"b": "x" + KEY}]}, KEY) and metahttp._secret_in({KEY: 1}, KEY) and metahttp._secret_in([[[KEY]]], KEY)
    assert not metahttp._secret_in({"a": [1, {"b": "x"}], "c": None, "d": 5.5, "e": True}, KEY) and not metahttp._secret_in("anything", "")
    deep = cur = []
    for _ in range(60):
        nxt = []
        cur.append(nxt)
        cur = nxt
    assert metahttp._secret_in(deep, KEY) is True


def _real_inputs(tmp_path, key="req-f602-001"):
    body, gov, auth = sam_setup(tmp_path)
    req = sam_request(key)
    att = minted(gov, req, {"events": EVENTS})
    return gov, req, att


@pytest.mark.parametrize("label,kw", [("another concept", dict(concept="truck")), ("zero width", dict(width=0)), ("height not an int", dict(height="480")), ("empty timebase", dict(pts_us=[])),
                                      ("non-increasing timebase", dict(pts_us=[5, 5])), ("negative timebase", dict(pts_us=[-1])), ("pts not a list", dict(pts_us=None))])
def test_F602_a_refused_or_failed_build_never_consumes_the_only_authority(tmp_path, label, kw):
    gov, req, att = _real_inputs(tmp_path)
    args = dict(events=EVENTS, concept="person", width=640, height=480, pts_us=[0], zones=ZONES, simulated=False, attestation=att, request=req, governor=gov)
    args.update(kw)
    with pytest.raises((meta_obs.NormalizationRefused, Exception)):
        meta_pipeline.build_evidence(**args)
    assert att.attempt_id not in metahttp._CONSUMED, label                                   # nothing was spent
    ev = meta_pipeline.build_evidence(events=EVENTS, concept="person", width=640, height=480, pts_us=[0], zones=ZONES, simulated=False, attestation=att, request=req, governor=gov)
    assert ev["provenance"]["status"] == "REAL_PROVIDER_RESPONSE_UNVERIFIED" and att.attempt_id in metahttp._CONSUMED          # the corrected call works, once
    with pytest.raises(meta_obs.NormalizationRefused) as e:
        meta_pipeline.build_evidence(events=EVENTS, concept="person", width=640, height=480, pts_us=[0], zones=ZONES, simulated=False, attestation=att, request=req, governor=gov)
    assert "already_consumed" in e.value.code


def test_F602_redeem_verifies_but_does_not_consume_and_only_the_label_spends_the_authority(tmp_path):
    gov, req, att = _real_inputs(tmp_path, "req-f602-002")
    for _ in range(3):
        red, problems = metahttp.redeem(att, request=req, payload={"events": EVENTS}, governor=gov)
        assert problems == [] and att.attempt_id not in metahttp._CONSUMED
    assert metahttp.use_redemption(red, response_digest=red.response_digest, request_digest=red.request_digest) == []
    assert att.attempt_id in metahttp._CONSUMED
    again, problems = metahttp.redeem(att, request=req, payload={"events": EVENTS}, governor=gov)
    assert again is None and "already_consumed" in problems
    sibling = metahttp._seal_redemption({k: getattr(red, k) for k in ("attempt_id", "provider", "model", "request_digest", "response_digest", "view", "issued_ns")})
    assert metahttp.use_redemption(sibling, response_digest=red.response_digest) == ["redemption_already_used"]           # a second sealed object for the same attempt is refused too


def test_F602_a_refused_muse_compose_never_consumes_the_redemption(tmp_path):
    inc, view, gov, req, payload, att = _muse_setup(tmp_path, key="req-f602-003")
    red, problems = metahttp.redeem(att, request=req, payload=payload, governor=gov)
    assert problems == []
    for kw in (dict(max_tokens=10), dict(max_tokens=5000), dict(max_tokens=True), dict(model="not a model"), dict(model="meta-models/other-model")):
        with pytest.raises(ValueError):
            M.compose(inc, payload, **{"model": "meta-models/x-y", **kw}, redemption=red)
        assert att.attempt_id not in metahttp._CONSUMED
    assert M.compose(inc, payload, model="meta-models/x-y", redemption=red)["real_muse_call"] is True and att.attempt_id in metahttp._CONSUMED


def test_F604_a_sealed_redemption_of_another_provider_or_model_cannot_label_a_muse_explanation(tmp_path):
    """Review 1 of Recovery 06: no vector made the provider/model check in `compose` necessary (mutant F2n survived by static analysis)."""
    inc, view, gov, req, payload, att = _muse_setup(tmp_path, key="req-f604-001")
    base = dict(attempt_id="f604" + "0" * 28, request_digest=costgov.raw_digest(req["body"]), response_digest=costgov.raw_digest(payload), view={"level": "x"}, issued_ns=1)
    wrong_provider = metahttp._seal_redemption({**base, "provider": S.PROVIDER_ID, "model": "meta-models/x-y"})
    wrong_model = metahttp._seal_redemption({**base, "attempt_id": "f604" + "1" * 28, "provider": M.PROVIDER_ID, "model": "meta-models/other-model"})
    for red in (wrong_provider, wrong_model):
        with pytest.raises(ValueError) as e:
            M.compose(inc, payload, model="meta-models/x-y", redemption=red)
        assert "wrong_provider_or_model" in str(e.value)
        assert red.attempt_id not in metahttp._CONSUMED
    right = metahttp._seal_redemption({**base, "attempt_id": "f604" + "2" * 28, "provider": M.PROVIDER_ID, "model": "meta-models/x-y"})
    assert M.compose(inc, payload, model="meta-models/x-y", redemption=right)["real_muse_call"] is True
