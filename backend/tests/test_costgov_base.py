import atexit
import os
import shutil
import tempfile
import threading
from decimal import Decimal

import pytest

from mineguard.rawjson import raw_digest
from mineguard.costgov import (key_digest, Denied, FakeTransport, GovernorConfig, GuardedTransport, NullRealTransport, RealTransportNotImplemented, SpendGovernor, TransportTimeout)

KEY = "k-secret-credential-123456"
M = "sam-3.1"
P = "meta_model_api"
BODY = {"model": M, "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "person"}]}], "stream": True}
BODY_DIGEST = raw_digest(BODY)


_TMP = tempfile.mkdtemp(prefix="mg-costgov-ledgers-")
atexit.register(shutil.rmtree, _TMP, True)
_N = [0]


def fresh_ledger() -> str:
    _N[0] += 1
    return os.path.join(_TMP, f"ledger{_N[0]}.jsonl")


def cfg(**kw):
    base = dict(ledger_path=fresh_ledger(), live_provider_enabled=True, external_media_upload_enabled=True, paid_fallback_enabled=False, max_real_requests=3, max_authorized_spend_usd=Decimal("1"),
                price_usd_per_request=((M, Decimal("0.10")),), provider_budget_usd=((P, Decimal("1")),), max_tokens=1000, max_concurrency=8,
                estimate_margin=Decimal(1), authorized_payload_digests=frozenset({BODY_DIGEST}))
    base.update(kw)
    return GovernorConfig(**base)


def mk(config=None, transport=None, cred=KEY):
    g = SpendGovernor(config or cfg())
    t = transport or FakeTransport()
    return GuardedTransport(t, g, credentials=lambda: cred), g, t


def req(key="r1", **kw):
    return {"model": M, "provider": P, "request_key": key, "media": "clip", "est_tokens": 10, "body": BODY, **kw}


def code(fn):
    with pytest.raises(Denied) as e:
        fn()
    return e.value.code


def test_defaults_refuse_everything_and_env_defaults_are_closed():
    c = GovernorConfig.from_env({})
    assert (c.live_provider_enabled, c.external_media_upload_enabled, c.paid_fallback_enabled, c.max_real_requests, c.max_authorized_spend_usd) == (False, False, False, 0, 0)
    g, _, t = mk(GovernorConfig())
    assert code(lambda: g.send(req())) == "live_provider_disabled" and t.outbound_attempts == 0


def test_env_parsing_fails_closed_on_garbage():
    c = GovernorConfig.from_env({"MG_META_LIVE_PROVIDER_ENABLED": "yes", "MG_META_MAX_REAL_REQUESTS": "-3", "MG_META_MAX_AUTHORIZED_SPEND_USD": "NaN"})
    assert (c.live_provider_enabled, c.max_real_requests, c.max_authorized_spend_usd) == (False, 0, 0)
    assert GovernorConfig.from_env({"MG_META_LIVE_PROVIDER_ENABLED": "true"}).live_provider_enabled is True


@pytest.mark.parametrize("cred", ["", "short", " " + "a" * 20, "a" * 10 + " " + "b" * 10, None])
def test_no_or_malformed_credentials(cred):
    g, _, t = mk(cred=cred)
    assert code(lambda: g.send(req())) == "credentials_missing_or_malformed" and t.outbound_attempts == 0


def test_invalid_credentials_rejected_by_provider_still_counts_as_spend():
    g, gov, t = mk(transport=FakeTransport({0: {"status": 401}}))
    assert code(lambda: g.send(req())) == "invalid_credentials"
    assert gov.snapshot()["real_requests_reserved"] == 1 and Decimal(gov.snapshot()["locally_estimated_usd"]) == Decimal("0.10")


def test_zero_spend_authorization_denies():
    g, _, t = mk(cfg(max_authorized_spend_usd=Decimal(0)))
    assert code(lambda: g.send(req())) == "spend_budget_exhausted" and t.outbound_attempts == 0


def test_zero_request_budget_denies():
    g, _, t = mk(cfg(max_real_requests=0))
    assert code(lambda: g.send(req())) == "request_budget_exhausted" and t.outbound_attempts == 0


def test_unknown_model_price_is_never_free():
    g, _, t = mk(cfg(price_usd_per_request=()))
    assert code(lambda: g.send(req())) == "unknown_model_price" and t.outbound_attempts == 0


def test_missing_usage_keeps_conservative_estimate_and_is_flagged():
    g, gov, _ = mk(transport=FakeTransport(default={"status": 200, "body": {}}))
    g.send(req())
    assert Decimal(gov.snapshot()["locally_estimated_usd"]) == Decimal("0.10")
    assert gov.audit[-1] == {"event": "reconciled", "key_digest": key_digest("r1"), "state": "unknown_outcome", "usage_reported": False}


def test_invalid_usage_values_are_not_trusted():
    for usage in ({"cost_usd": "-5"}, {"cost_usd": "abc"}, {"cost_usd": "NaN"}, "junk"):
        g, gov, _ = mk(transport=FakeTransport(default={"status": 200, "body": {}, "usage": usage}))
        g.send(req())
        assert Decimal(gov.snapshot()["locally_estimated_usd"]) == Decimal("0.10")


def test_reported_usage_reconciles_to_actual():
    g, gov, _ = mk(transport=FakeTransport(default={"status": 200, "body": {}, "usage": {"cost_usd": "0.03"}}))
    g.send(req())
    assert Decimal(gov.snapshot()["locally_estimated_usd"]) == Decimal("0.03")


@pytest.mark.parametrize("resp", [{"status": 200, "body": "text"}, {"status": 302, "body": {}}, {"status": 200}, "garbage", {"status": None}])
def test_unexpected_provider_response(resp):
    g, gov, _ = mk(transport=FakeTransport(default=resp))
    assert code(lambda: g.send(req())) == "unexpected_provider_response" and gov.snapshot()["real_requests_reserved"] == 1


def test_quota_exceeded_429():
    g, _, _ = mk(transport=FakeTransport({0: {"status": 429}}))
    assert code(lambda: g.send(req())) == "provider_quota_exceeded"


def test_retry_after_failure_is_allowed_but_consumes_budget_again():
    g, gov, t = mk(cfg(max_real_requests=2), FakeTransport({0: {"status": 500, "body": {}}}))
    assert code(lambda: g.send(req())) == "provider_server_error"
    g.send(req())                      # retry succeeds
    assert gov.snapshot()["real_requests_reserved"] == 2 and t.outbound_attempts == 2
    assert code(lambda: g.send(req("r2"))) == "request_budget_exhausted"   # no free retries


def test_response_timeout_counts_as_spend_and_propagates():
    g, gov, _ = mk(transport=FakeTransport({0: TransportTimeout()}))
    with pytest.raises(TransportTimeout):
        g.send(req())
    assert gov.snapshot()["real_requests_reserved"] == 1 and Decimal(gov.snapshot()["locally_estimated_usd"]) == Decimal("0.10")


def test_duplicate_submission_denied_after_success():
    g, _, t = mk()
    g.send(req())
    assert code(lambda: g.send(req())) == "duplicate_submission" and t.outbound_attempts == 1


def test_paid_fallback_attempt_denied_unless_enabled():
    g, _, t = mk()
    assert code(lambda: g.send(req(tier="paid_fallback"))) == "paid_fallback_disabled" and t.outbound_attempts == 0


def test_media_upload_denied_when_disabled():
    g, _, t = mk(cfg(external_media_upload_enabled=False))
    assert code(lambda: g.send(req())) == "external_media_upload_disabled" and t.outbound_attempts == 0


def test_concurrent_race_never_exceeds_request_budget():
    g, gov, t = mk(cfg(max_real_requests=5, max_authorized_spend_usd=Decimal("100")))
    ok, denied = [], []
    barrier = threading.Barrier(40)

    def work(i):
        barrier.wait()
        try:
            g.send(req(f"c{i}"))
            ok.append(i)
        except Denied:
            denied.append(i)

    ts = [threading.Thread(target=work, args=(i,)) for i in range(40)]
    [x.start() for x in ts]
    [x.join() for x in ts]
    assert len(ok) == 5 and len(denied) == 35 and t.outbound_attempts == 5 == gov.snapshot()["real_requests_reserved"]


def test_concurrent_spend_race_never_exceeds_spend_budget():
    g, gov, t = mk(cfg(max_real_requests=100, max_authorized_spend_usd=Decimal("0.35")), FakeTransport(default={"status": 200, "body": {}, "usage": {"cost_usd": "0.10"}}))
    barrier, ok = threading.Barrier(20), []

    def work(i):
        barrier.wait()
        try:
            g.send(req(f"s{i}"))
            ok.append(i)
        except Denied:
            pass

    ts = [threading.Thread(target=work, args=(i,)) for i in range(20)]
    [x.start() for x in ts]
    [x.join() for x in ts]
    assert len(ok) == 3 and Decimal(gov.snapshot()["locally_estimated_usd"]) <= Decimal("0.35")


def test_credentials_never_appear_in_audit_log_exceptions_or_snapshot():
    g, gov, _ = mk(transport=FakeTransport({0: {"status": 401}}))
    with pytest.raises(Denied) as e:
        g.send(req())
    blob = repr(gov.audit) + repr(gov.snapshot()) + repr(e.value) + str(e.value)
    assert KEY not in blob


def test_the_only_real_transport_never_does_io_and_always_refuses():
    t = NullRealTransport()
    g = GuardedTransport(t, SpendGovernor(cfg()), credentials=lambda: KEY)
    with pytest.raises(RealTransportNotImplemented):
        g.send(req())
    assert t.outbound_attempts == 1   # counted at the boundary, but nothing was sent anywhere


def test_unknown_cost_never_implies_unlimited_budget():
    g, gov, _ = mk(cfg(price_usd_per_request=(), max_authorized_spend_usd=Decimal("1000000")))
    assert code(lambda: g.send(req())) == "unknown_model_price"
