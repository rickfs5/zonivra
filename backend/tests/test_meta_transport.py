"""META LIVE READINESS 04: the governed dispatch path, exercised ONLY against an in-process loopback server (no external host, no DNS for any real name, no credential but a dummy
test string). What the server received is exactly what would have been sent. Expectations come from the contract and the governor's deny-by-default rules."""
import json
import socket
import threading
import time
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from mineguard import costgov, meta_pipeline, metaclient, metahttp, sam31 as S
from mineguard.costgov import Denied, GovernorConfig, GuardedTransport, SpendGovernor, TransportTimeout
from mineguard.metahttp import EndpointBoundGuard, TestHttpsTransport

KEY = "TEST-KEY-NOT-A-REAL-KEY-0123456789"
URL = "https://example.com/frame.png"
LINE = "<0f>1<|box;x1=10;y1=20;x2=110;y2=220;w=640;h=480;c=0.90|><|mask;x=0;y=0;c=0.90;data=4,5,!aB|cD|>"


def sse(*events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()


OK_STREAM = sse({"type": "response.created"}, {"type": "response.output_text.delta", "delta": LINE}, {"type": "response.completed", "response": {"usage": {"images": 1}}})


class Srv:
    def __init__(self, script):
        self.script, self.hits, self.lock = list(script), [], threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n)
                with outer.lock:
                    outer.hits.append({"path": self.path, "headers": dict(self.headers), "body": body})
                    k = len(outer.hits) - 1
                status, hdrs, data, delay = (outer.script[k] if k < len(outer.script) else outer.script[-1])
                time.sleep(delay)
                try:
                    self.send_response(status)
                    for a, b in hdrs.items():
                        self.send_header(a, b)
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                except OSError:
                    pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def no_external_dns(monkeypatch):
    seen = []
    real = socket.getaddrinfo

    def guard(host, *a, **k):
        if host not in ("127.0.0.1", "localhost", "::1"):
            seen.append(host)
            raise AssertionError(f"external name resolution attempted: {host}")
        return real(host, *a, **k)
    monkeypatch.setattr(socket, "getaddrinfo", guard)
    yield seen
    assert seen == []


@pytest.fixture
def make(tmp_path, no_external_dns):
    servers = []

    def build(script, *, max_uses=50, enabled=True, authorize=True, max_requests=3, concurrency=1, spend="1", upload=True, timeout=5, creds=lambda: KEY, kill_file=""):
        srv = Srv(script)
        servers.append(srv)
        body = S.build_body(URL, "person")
        cfg = GovernorConfig(live_provider_enabled=enabled, external_media_upload_enabled=upload, max_real_requests=max_requests, max_authorized_spend_usd=Decimal(spend),
                             price_usd_per_request=((S.MODEL, S.PRICE_PER_IMAGE_USD),), provider_budget_usd=((S.PROVIDER_ID, Decimal(spend)),), max_tokens=0, max_concurrency=concurrency,
                             request_timeout_s=timeout, authorized_payload_digests=frozenset({costgov.raw_digest(body)}) if authorize else frozenset(),
                             kill_switch_file=kill_file, ledger_path=str(tmp_path / "ledger.jsonl"))
        gov = SpendGovernor(cfg)
        inner = TestHttpsTransport(f"http://127.0.0.1:{srv.port}/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized={(S.PROVIDER_ID, S.MODEL, costgov.raw_digest(body))},
                                   accept_sse=True, max_uses=max_uses, allow_loopback_http=True)
        return srv, gov, inner, EndpointBoundGuard(inner, gov, creds), body
    yield build
    for s in servers:
        s.close()


ZONES = [meta_pipeline.full_frame_zone(640, 480)]


def call(g, key="req-000001", cancel=None):
    return metaclient.SamClient(g).first_call(URL, "person", key, width=640, height=480, zones=ZONES, cancel=cancel)


def test_authorized_call_sends_exactly_the_authorized_body_with_documented_headers_and_no_key_in_the_body(make):
    srv, gov, inner, g, body = make([(200, {"Content-Type": "text/event-stream"}, OK_STREAM, 0)])
    out = call(g)
    assert out["outcome"] == "OK" and len(srv.hits) == 1
    h = srv.hits[0]
    assert h["path"] == "/v1/responses" and json.loads(h["body"]) == body
    assert h["headers"]["Authorization"] == "Bearer " + KEY and h["headers"]["Accept"] == "text/event-stream" and h["headers"]["Content-Type"] == "application/json"
    assert KEY.encode() not in h["body"]
    assert costgov.raw_digest(json.loads(h["body"])) in gov.config.authorized_payload_digests


def test_a_response_from_a_non_official_endpoint_is_labelled_simulated_never_real(make):
    srv, gov, inner, g, body = make([(200, {}, OK_STREAM, 0)])
    out = call(g)
    ev = out["evidence"]
    assert inner.is_production is False and out["real_meta_call_completed"] is False
    assert ev["provenance"]["status"] == "SIMULATED" and ev["provenance"]["origin"] == "simulated_provider_response" and ev["provenance"]["attributed_to_local_detector"] is False and ev["provenance"]["authenticity"].startswith("none")


@pytest.mark.parametrize("payload,ctype", [(json.dumps({"error": {"message": "invalid key " + KEY, "type": "authentication_error", "code": "invalid_api_key"}}).encode(), "error-body"), (json.dumps({"echo": KEY}).encode(), "json"), (sse({"type": "response.output_text.delta", "delta": "x " + KEY}, {"type": "response.completed"}), "sse")])
def test_a_successful_response_that_echoes_the_credential_is_refused_not_returned(make, payload, ctype):
    """Review CD-3: only error bodies were redacted; a 200 body could return the key verbatim."""
    srv, gov, inner, g, body = make([(200, {}, payload, 0)])
    out = call(g)
    assert out["outcome"] == "TRANSPORT_FAULT" and "response_echoed_the_credential" in out["code"] and out["evidence"] is None
    assert KEY not in json.dumps(out) and Decimal(gov.snapshot()["locally_estimated_usd"]) > 0


@pytest.mark.parametrize("kw,code", [({"enabled": False}, "live_provider_disabled"), ({"authorize": False}, "payload_not_authorized"), ({"upload": False}, "external_media_upload_disabled"),
                                     ({"max_requests": 0}, "request_budget_exhausted"), ({"spend": "0.001"}, "provider_budget_exhausted"), ({"creds": lambda: ""}, "credentials_missing_or_malformed"),
                                     ({"creds": lambda: "has space in key"}, "credentials_missing_or_malformed"), ({"creds": lambda: KEY + "\r\nX-Evil: 1"}, "credentials_missing_or_malformed"),
                                     ({"creds": lambda: " " + KEY}, "credentials_missing_or_malformed"), ({"creds": lambda: None}, "credentials_missing_or_malformed")])
def test_every_denial_happens_before_anything_reaches_the_server(make, kw, code):
    srv, gov, inner, g, body = make([(200, {}, OK_STREAM, 0)], **kw)
    out = call(g)
    assert out["outcome"] == "DENIED" and out["code"] == code and out["evidence"] is None and out["real_meta_call_completed"] is False
    assert srv.hits == [] and inner.outbound_attempts == 0


def test_default_governor_config_denies_everything(tmp_path, no_external_dns):
    srv = Srv([(200, {}, OK_STREAM, 0)])
    try:
        g0 = SpendGovernor(GovernorConfig())
        body = S.build_body(URL, "person")
        inner = TestHttpsTransport(f"http://127.0.0.1:{srv.port}/v1", path="/responses", provider=S.PROVIDER_ID, governor=g0, authorized={(S.PROVIDER_ID, S.MODEL, costgov.raw_digest(body))},
                                   accept_sse=True, allow_loopback_http=True)
        g = EndpointBoundGuard(inner, g0, lambda: KEY)
        assert call(g)["code"] == "live_provider_disabled" and srv.hits == []
    finally:
        srv.close()


def test_kill_switch_file_stops_the_call_before_dispatch(make, tmp_path):
    k = tmp_path / "kill"
    k.write_text("x")
    srv, gov, inner, g, body = make([(200, {}, OK_STREAM, 0)], kill_file=str(k))
    assert call(g)["code"] == "kill_switch_engaged" and srv.hits == []


def test_cancellation_before_dispatch_sends_nothing(make):
    srv, gov, inner, g, body = make([(200, {}, OK_STREAM, 0)])
    ev = threading.Event()
    ev.set()
    assert call(g, cancel=ev)["code"] == "cancelled" and srv.hits == []


@pytest.mark.parametrize("status,hdr,body_err,code,retry", [(429, {"Retry-After": "7"}, {"error": {"message": "slow", "type": "rate_limit_error", "code": "rate_limit_exceeded"}}, "provider_quota_exceeded", True),
                                                            (401, {}, {"error": {"message": "bad key", "type": "authentication_error", "code": "invalid_api_key"}}, "invalid_credentials", False),
                                                            (500, {}, {"error": {"message": "boom", "type": "server_error", "code": None}}, "provider_server_error", True),
                                                            (503, {}, {"error": {"message": "busy", "type": "server_error", "code": "service_overloaded"}}, "provider_server_error", True),
                                                            (402, {}, {"error": {"message": "balance", "type": "billing_error", "code": None}}, "unexpected_provider_response", False),
                                                            (400, {}, {"error": {"message": "bad", "type": "invalid_request_error", "code": None, "param": "input"}}, "unexpected_provider_response", False),
                                                            (413, {}, {"error": {"message": "big", "type": "invalid_request_error", "code": "payload_too_large"}}, "unexpected_provider_response", False)])
def test_provider_errors_are_classified_counted_as_spend_never_retried_and_never_leak_the_key(make, status, hdr, body_err, code, retry):
    srv, gov, inner, g, body = make([(status, hdr, json.dumps(body_err).encode(), 0), (200, {}, OK_STREAM, 0)])
    out = call(g)
    assert out["outcome"] == "DENIED" and out["code"] == code and out["evidence"] is None and out["spend_counted"] is True
    assert len(srv.hits) == 1                                                        # ZERO retries even for retryable codes: a retry is a later, owner-authorized decision
    assert KEY not in json.dumps(out) and gov.snapshot()["real_requests_reserved"] == 1 and Decimal(gov.snapshot()["locally_estimated_usd"]) > 0
    assert (out["retry"] != "no") is retry
    if status == 429:
        assert out["retry_after"] == 7
    assert out["provider_error"]["status"] == status


def test_a_timeout_counts_as_spend_and_is_not_retried(make):
    srv, gov, inner, g, body = make([(200, {}, OK_STREAM, 2.5), (200, {}, OK_STREAM, 0)], timeout=1)
    out = call(g)
    assert out["outcome"] == "TIMEOUT" and out["spend_counted"] is True and out["evidence"] is None
    assert Decimal(gov.snapshot()["locally_estimated_usd"]) > 0 and out["real_meta_call_completed"] is False


@pytest.mark.parametrize("data,why", [(b"data: {not json}\n\n", "sse_unparseable"), (b"data: [1,2]\n\n", "sse_unparseable")])
def test_malformed_sse_is_a_transport_fault_counted_as_spend(make, data, why):
    srv, gov, inner, g, body = make([(200, {}, data, 0)])
    out = call(g)
    assert out["outcome"] == "TRANSPORT_FAULT" and why in out["code"] and Decimal(gov.snapshot()["locally_estimated_usd"]) > 0


def test_an_incomplete_stream_yields_unknown_evidence_not_an_empty_zone(make):
    srv, gov, inner, g, body = make([(200, {}, sse({"type": "response.output_text.delta", "delta": LINE}), 0)])
    out = call(g)
    ev = out["evidence"]
    assert out["outcome"] == "PARSE_UNKNOWN" and ev["severity_by_zone"]["Z1"]["final_state"] == "UNKNOWN" and ev["coverage"]["absence_is_not_safety"] is True
    assert ev["provenance"]["parse_reasons"] == ["stream_not_completed"]


def test_unknown_usage_keeps_the_conservative_estimate_it_never_lowers_spend_to_zero(make):
    srv, gov, inner, g, body = make([(200, {}, sse({"type": "response.output_text.delta", "delta": LINE}, {"type": "response.completed"}), 0)])
    call(g)
    snap = gov.snapshot()
    assert Decimal(snap["locally_estimated_usd"]) == S.PRICE_PER_IMAGE_USD * Decimal("1.25") and "not a provider charge" in snap["note"]


def test_the_same_request_key_cannot_be_submitted_twice_and_the_budget_is_one_request_per_slot(make):
    stream = sse({"type": "response.output_text.delta", "delta": LINE}, {"type": "response.completed", "response": {"usage": {"cost_usd": 0.0025}}})
    srv, gov, inner, g, body = make([(200, {}, stream, 0)], max_requests=1)
    assert call(g)["outcome"] == "OK"
    assert call(g)["code"] == "duplicate_submission" and len(srv.hits) == 1                  # committed: the same key is a duplicate
    assert call(g, key="req-000002")["code"] == "request_budget_exhausted" and len(srv.hits) == 1


def test_a_key_whose_outcome_is_unknown_is_a_retry_that_still_consumes_budget(make):
    srv, gov, inner, g, body = make([(200, {}, OK_STREAM, 0)], max_requests=1)               # no usage reported -> unknown outcome (spend kept)
    assert call(g)["outcome"] == "OK"
    assert call(g)["code"] == "request_budget_exhausted" and len(srv.hits) == 1


def test_concurrent_callers_cannot_exceed_the_concurrency_or_request_budget(make):
    srv, gov, inner, g, body = make([(200, {}, OK_STREAM, 0.3)], max_requests=1, concurrency=1)
    outs = []

    def go(i):
        outs.append(call(g, key=f"req-race-{i:03d}"))
    ts = [threading.Thread(target=go, args=(i,)) for i in range(12)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(srv.hits) == 1 and sum(1 for o in outs if o["outcome"] == "OK") == 1 and len(outs) == 12


def _second(srv, gov2, body):
    inner2 = TestHttpsTransport(f"http://127.0.0.1:{srv.port}/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov2, authorized={(S.PROVIDER_ID, S.MODEL, costgov.raw_digest(body))},
                                accept_sse=True, max_uses=50, allow_loopback_http=True)
    return EndpointBoundGuard(inner2, gov2, lambda: KEY)


def test_restart_replays_the_ledger_and_an_unsettled_reservation_stays_counted(make, tmp_path):
    srv, gov, inner, g, body = make([(200, {}, OK_STREAM, 0)], max_requests=1)
    r = gov.reserve("req-crash-01", S.MODEL, S.PROVIDER_ID, 0, 1)                      # the process "dies" after the write-ahead record, before any settlement
    del gov
    gov2 = SpendGovernor(GovernorConfig(live_provider_enabled=True, external_media_upload_enabled=True, max_real_requests=1, max_authorized_spend_usd=Decimal(1),
                                        price_usd_per_request=((S.MODEL, S.PRICE_PER_IMAGE_USD),), provider_budget_usd=((S.PROVIDER_ID, Decimal(1)),), max_concurrency=1,
                                        authorized_payload_digests=frozenset({costgov.raw_digest(body)}), ledger_path=str(tmp_path / "ledger.jsonl")))
    assert gov2.ledger_error is None and Decimal(gov2.snapshot()["locally_estimated_usd"]) > 0                 # NOT forgotten
    g2 = _second(srv, gov2, body)
    assert call(g2, key="req-after-01")["code"] == "request_budget_exhausted" and srv.hits == []


def test_a_tampered_ledger_denies_everything(make, tmp_path):
    srv, gov, inner, g, body = make([(200, {}, OK_STREAM, 0)])
    call(g)
    p = tmp_path / "ledger.jsonl"
    p.write_text(p.read_text().replace('"reserve"', '"reserVe"', 1))
    gov2 = SpendGovernor(GovernorConfig(live_provider_enabled=True, external_media_upload_enabled=True, max_real_requests=5, max_authorized_spend_usd=Decimal(1),
                                        price_usd_per_request=((S.MODEL, S.PRICE_PER_IMAGE_USD),), provider_budget_usd=((S.PROVIDER_ID, Decimal(1)),), max_concurrency=1,
                                        authorized_payload_digests=frozenset({costgov.raw_digest(body)}), ledger_path=str(p)))
    assert gov2.ledger_error and call(_second(srv, gov2, body), key="req-after-02")["code"] == "ledger_unreadable"
    assert len(srv.hits) == 1


def test_oversized_responses_are_cut_off_and_counted(make, monkeypatch):
    monkeypatch.setattr(metahttp, "MAX_RESPONSE_BYTES", 100)
    srv, gov, inner, g, body = make([(200, {}, b"data: " + b"x" * 400 + b"\n\n", 0)])
    out = call(g)
    assert out["outcome"] == "TRANSPORT_FAULT" and "response_too_large" in out["code"]


def test_connection_failure_never_exposes_the_credential_and_is_counted(make):
    srv, gov, inner, g, body = make([(200, {}, OK_STREAM, 0)])
    srv.close()
    out = call(g)
    assert out["outcome"] == "TRANSPORT_FAULT" and KEY not in json.dumps(out) and Decimal(gov.snapshot()["locally_estimated_usd"]) > 0


def test_redaction_removes_the_key_and_bearer_tokens_from_any_error_text():
    t = metahttp._redact(f"bad {KEY} and Bearer abcDEF123456 and " + "LLM_" + "12_" + "secretvalue", KEY)
    assert KEY not in t and "abcDEF123456" not in t and "secretvalue" not in t
