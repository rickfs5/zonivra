"""MG-META-VERTICAL-02 governor hardening. Only fake transports; nothing leaves the process."""
import os
from pathlib import Path
import threading
import time
from decimal import Decimal

import pytest

from mineguard.costgov import (Denied, FakeTransport, GovernorConfig, GuardedTransport, Ledger, LedgerCorrupt, NullRealTransport, SpendGovernor, TransportTimeout, key_digest)
from test_costgov_base import BODY, BODY_DIGEST, KEY, M, P, cfg, code, mk, req


NOUSE = {"status": 200, "body": {}}   # no usage reported -> the conservative estimate stays


def test_default_config_has_every_new_dimension_closed():
    c = GovernorConfig()
    assert (c.provider_budget_usd, c.max_tokens, c.max_concurrency, c.authorized_payload_digests, c.kill_switch_file, c.price_usd_per_request) == ((), 0, 0, frozenset(), "", ())


def test_provider_budget_missing_and_exhausted():
    g, gov, t = mk(cfg(provider_budget_usd=()))
    assert code(lambda: g.send(req())) == "provider_budget_missing" and t.outbound_attempts == 0
    g, gov, t = mk(cfg(provider_budget_usd=((P, Decimal("0.15")),)), FakeTransport(default=NOUSE))
    g.send(req("a"))
    assert code(lambda: g.send(req("b"))) == "provider_budget_exhausted"
    g, gov, t = mk(cfg())
    assert code(lambda: g.send(req("a", provider="other_provider"))) == "provider_budget_missing"


def test_token_quota_and_unknown_token_estimate():
    g, gov, _ = mk(cfg(max_tokens=15), FakeTransport(default=NOUSE))
    assert code(lambda: g.send(req("a", est_tokens=None))) == "unknown_token_estimate"
    assert code(lambda: g.send(req("b", est_tokens=-1))) == "unknown_token_estimate"
    assert code(lambda: g.send(req("c", est_tokens=True))) == "unknown_token_estimate"
    g.send(req("d", est_tokens=10))
    assert code(lambda: g.send(req("e", est_tokens=10))) == "token_budget_exhausted"


def test_payload_must_be_authorized_by_exact_digest():
    g, _, t = mk()
    other = dict(BODY, stream=False)
    assert code(lambda: g.send(req(body=other))) == "payload_not_authorized"
    assert code(lambda: g.send(req(body=None))) == "payload_not_authorized"
    assert code(lambda: g.send(req(body="text"))) == "payload_not_authorized"
    assert t.outbound_attempts == 0
    g2, _, t2 = mk(cfg(authorized_payload_digests=frozenset()))
    assert code(lambda: g2.send(req())) == "payload_not_authorized" and t2.outbound_attempts == 0
    # a caller-supplied digest cannot vouch for a different body
    assert code(lambda: g.send(req(body=other, payload_digest=BODY_DIGEST))) == "unauthorized_request_field"


def test_unknown_request_fields_refused():
    g, _, t = mk()
    assert code(lambda: g.send(req(extra_header="x"))) == "unauthorized_request_field" and t.outbound_attempts == 0


def test_manual_kill_switch_in_process_and_file(tmp_path):
    g, gov, t = mk()
    g.send(req("a"))
    gov.engage_kill_switch()
    assert code(lambda: g.send(req("b"))) == "kill_switch_engaged" and t.outbound_attempts == 1
    flag = tmp_path / "STOP"
    g, gov, t = mk(cfg(kill_switch_file=str(flag)))
    g.send(req("a"))
    flag.write_text("")
    assert code(lambda: g.send(req("b"))) == "kill_switch_engaged"
    flag.unlink()
    g.send(req("c"))   # removing the file releases a file-based switch; the in-process one does not
    gov.engage_kill_switch()
    flag.write_text("")
    flag.unlink()
    assert code(lambda: g.send(req("d"))) == "kill_switch_engaged"


def test_unreadable_kill_switch_is_engaged(tmp_path):
    d = tmp_path / "locked"
    d.mkdir()
    (d / "STOP").write_text("")
    d.chmod(0)
    try:
        g, _, t = mk(cfg(kill_switch_file=str(d / "STOP")))
        assert code(lambda: g.send(req())) == "kill_switch_engaged" and t.outbound_attempts == 0
    finally:
        d.chmod(0o700)


def test_overrun_trips_the_kill_switch():
    g, gov, _ = mk(transport=FakeTransport(default={"status": 200, "body": {}, "usage": {"cost_usd": "0.50", "tokens": 5}}))
    g.send(req("a"))
    assert gov.snapshot()["kill_switch_engaged"] is True
    assert code(lambda: g.send(req("b"))) == "kill_switch_engaged"


def test_estimate_margin_and_units_are_conservative():
    g, gov, _ = mk(cfg(estimate_margin=Decimal("1.5"), max_authorized_spend_usd=Decimal("10"), provider_budget_usd=((P, Decimal("10")),)), FakeTransport(default={"status": 200, "body": {}}))
    g.send(req("a", units=4))
    assert Decimal(gov.snapshot()["locally_estimated_usd"]) == Decimal("0.60")   # 0.10 * 4 * 1.5
    assert code(lambda: g.send(req("b", units=0))) == "invalid_units"
    assert code(lambda: g.send(req("c", units=True))) == "invalid_units"
    assert GovernorConfig.from_env({"MG_META_ESTIMATE_MARGIN": "0.2"}).estimate_margin == Decimal("1.25")


def test_concurrency_cap_holds_under_threads():
    state = {"now": 0, "peak": 0}
    lk = threading.Lock()

    class Slow(FakeTransport):
        def send(self, request, credentials, timeout_s=30):
            with lk:
                state["now"] += 1
                state["peak"] = max(state["peak"], state["now"])
            time.sleep(0.02)
            with lk:
                state["now"] -= 1
            return {"status": 200, "body": {}, "usage": {"cost_usd": "0.01", "tokens": 1}}

    g, gov, _ = mk(cfg(max_concurrency=2, max_real_requests=100, max_authorized_spend_usd=Decimal("100"), provider_budget_usd=((P, Decimal("100")),), max_tokens=10_000), Slow())
    results = []

    def go(i):
        try:
            g.send(req(f"k{i}"))
            results.append("ok")
        except Denied as e:
            results.append(e.code)

    ts = [threading.Thread(target=go, args=(i,)) for i in range(30)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert state["peak"] <= 2 and "ok" in results and set(results) <= {"ok", "concurrency_limit"}


def test_in_flight_reservation_blocks_concurrency_without_a_transport():
    gov = SpendGovernor(cfg(max_concurrency=1))
    gov.reserve("a", M, P, 1)
    with pytest.raises(Denied) as e:
        gov.reserve("b", M, P, 1)
    assert e.value.code == "concurrency_limit"


# ---- retries, timeouts, cancellation
def test_retry_consumes_budget_each_attempt_and_passes_timeout():
    t = FakeTransport({0: {"status": 503, "body": {}}, 1: {"status": 200, "body": {}, "usage": {"cost_usd": "0.10", "tokens": 1}}})
    g, gov, _ = mk(cfg(request_timeout_s=7), t)
    assert g.send_with_retry(req())["status"] == 200
    assert t.calls == 2 and gov.snapshot()["real_requests_reserved"] == 2 and t.seen_timeouts == [7, 7]
    assert Decimal(gov.snapshot()["locally_estimated_usd"]) == Decimal("0.20")


def test_retry_never_exceeds_budget():
    t = FakeTransport(default={"status": 503, "body": {}})
    g, gov, _ = mk(cfg(max_real_requests=2), t)
    assert code(lambda: g.send_with_retry(req(), max_attempts=3)) == "request_budget_exhausted" and t.calls == 2


@pytest.mark.parametrize("resp,expect", [({"status": 429, "body": {}}, "provider_quota_exceeded"), ({"status": 401}, "invalid_credentials"), ({"status": 200, "body": "x"}, "unexpected_provider_response")])
def test_non_retryable_failures_do_not_retry(resp, expect):
    t = FakeTransport(default=resp)
    g, _, _ = mk(transport=t)
    assert code(lambda: g.send_with_retry(req(), max_attempts=3)) == expect and t.calls == 1


def test_timeout_is_retried_then_propagates_and_both_attempts_count():
    t = FakeTransport(default=TransportTimeout())
    g, gov, _ = mk(transport=t)
    with pytest.raises(TransportTimeout):
        g.send_with_retry(req(), max_attempts=9)   # capped at 3
    assert t.calls == 3 and gov.snapshot()["real_requests_reserved"] == 3


def test_abort_midflight_counts_as_spend_and_is_not_retried():
    t = FakeTransport(default=KeyboardInterrupt())
    g, gov, _ = mk(transport=t)
    with pytest.raises(KeyboardInterrupt):
        g.send_with_retry(req())
    assert t.calls == 1 and gov.snapshot()["real_requests_reserved"] == 1 and Decimal(gov.snapshot()["locally_estimated_usd"]) == Decimal("0.10")


def test_cancel_before_send_reserves_nothing():
    ev = threading.Event()
    ev.set()
    g, gov, t = mk()
    assert code(lambda: g.send(req(), cancel=ev)) == "cancelled" and t.outbound_attempts == 0 and gov.snapshot()["real_requests_reserved"] == 0


def test_cancel_after_send_keeps_the_spend():
    ev = threading.Event()

    class T(FakeTransport):
        def send(self, request, credentials, timeout_s=30):
            ev.set()
            return {"status": 200, "body": {}, "usage": {"cost_usd": "0.05", "tokens": 1}}

    g, gov, _ = mk(transport=T())
    assert code(lambda: g.send(req(), cancel=ev)) == "cancelled" and Decimal(gov.snapshot()["locally_estimated_usd"]) == Decimal("0.05")


# ---- durable ledger
def test_ledger_never_contains_secrets_or_payloads(tmp_path):
    lp = tmp_path / "ledger.jsonl"
    secret_key = "sk-live-SECRET-REQUEST-KEY-9999"
    g, gov, _ = mk(cfg(ledger_path=str(lp)))
    g.send(req(secret_key))
    text = lp.read_text()
    assert KEY not in text and secret_key not in text and "person" not in text and "Bearer" not in text
    assert key_digest(secret_key) in text
    assert oct(os.stat(lp).st_mode & 0o777) == "0o600"
    assert all("SECRET" not in str(a) and KEY not in str(a) for a in gov.audit)


def test_restart_recovers_spend_and_counts_inflight_as_unknown(tmp_path):
    lp = tmp_path / "ledger.jsonl"
    c = cfg(ledger_path=str(lp), max_real_requests=3)
    g1 = SpendGovernor(c)
    r1 = g1.reserve("a", M, P, 1)
    g1.reconcile(r1, {"cost_usd": "0.10", "tokens": 1})
    g1.reserve("b", M, P, 1)          # process "dies" here: never reconciled
    g2 = SpendGovernor(c)             # restart
    snap = g2.snapshot()
    assert snap["real_requests_reserved"] == 2 and Decimal(snap["locally_estimated_usd"]) == Decimal("0.20") and snap["in_flight"] == 0
    with pytest.raises(Denied) as e:
        g2.reserve("a", M, P, 1)      # the committed key is still a duplicate after restart
    assert e.value.code == "duplicate_submission"
    g2.reserve("b", M, P, 1)          # unknown-outcome key may be retried, at new cost
    with pytest.raises(Denied) as e:
        g2.reserve("c", M, P, 1)
    assert e.value.code == "request_budget_exhausted"


def test_restart_preserves_the_kill_switch(tmp_path):
    c = cfg(ledger_path=str(tmp_path / "l.jsonl"))
    SpendGovernor(c).engage_kill_switch()
    assert SpendGovernor(c).snapshot()["kill_switch_engaged"] is True


def test_corrupt_or_tampered_ledger_fails_closed(tmp_path):
    lp = tmp_path / "l.jsonl"
    c = cfg(ledger_path=str(lp))
    g = SpendGovernor(c)
    g.reserve("a", M, P, 1)
    g.reserve("b", M, P, 1)
    text = lp.read_text()
    for bad in (text.replace("0.1", "0.0", 1), text + "not json\n", "\n".join(reversed(text.splitlines())) + "\n"):
        lp.write_text(bad)
        g2 = SpendGovernor(c)
        with pytest.raises(Denied) as e:
            g2.reserve("z", M, P, 1)
        assert e.value.code == "ledger_unreadable" and g2.snapshot()["ledger_ok"] is False


def test_ledger_write_failure_blocks_the_send(tmp_path):
    g, gov, t = mk(cfg(ledger_path=str(tmp_path / "missing_dir" / "l.jsonl")))
    assert code(lambda: g.send(req())) == "ledger_unavailable" and t.outbound_attempts == 0


def test_ledger_replay_detects_truncated_chain(tmp_path):
    lp = tmp_path / "l.jsonl"
    L = Ledger(str(lp))
    for i in range(3):
        L.append({"event": "kill", "code": str(i)})
    lines = lp.read_text().splitlines()
    lp.write_text("\n".join([lines[0], lines[2]]) + "\n")   # a removed record breaks the chain
    with pytest.raises(LedgerCorrupt):
        Ledger(str(lp)).replay()


def test_env_maps_fail_closed_on_any_garbage():
    e = {"MG_META_PRICES_USD_PER_UNIT": "sam-3.1=0.0002,bad", "MG_META_PROVIDER_BUDGETS_USD": "p=NaN", "MG_META_MAX_TOKENS": "x"}
    c = GovernorConfig.from_env(e)
    assert c.price_usd_per_request == () and c.provider_budget_usd == () and c.max_tokens == 0
    ok = GovernorConfig.from_env({"MG_META_PRICES_USD_PER_UNIT": "sam-3.1=0.0002", "MG_META_PROVIDER_BUDGETS_USD": "meta_model_api=0.50", "MG_META_AUTHORIZED_PAYLOAD_DIGESTS": "abc, def"})
    assert ok.price("sam-3.1") == Decimal("0.0002") and ok.provider_budget("meta_model_api") == Decimal("0.50") and ok.authorized_payload_digests == {"abc", "def"}


def test_null_transport_still_refuses_with_new_signature():
    with pytest.raises(Exception):
        NullRealTransport().send({}, "x" * 10, 5)
def test_request_mutated_after_authorization_cannot_reach_transport():
    """TOCTOU: the body that passed the digest check is the body that is sent, even if the caller's object changes while the call is in flight."""
    from mineguard.costgov import SpendGovernor
    from test_costgov_base import BODY, cfg, req
    seen = []

    class Spy(FakeTransport):
        def send(self, request, credentials, timeout_s=30):
            seen.append(request["body"])
            return super().send(request, credentials, timeout_s)

    class Mutating(SpendGovernor):
        def reserve(self, *a, **k):
            r = super().reserve(*a, **k)
            shared["body"] = {"model": BODY.get("model"), "input": "UNAUTHORIZED"}
            return r

    shared = req()
    g = Mutating(cfg())
    t = GuardedTransport(Spy(), g, credentials=lambda: "k" * 20)
    t.send(shared)
    assert seen == [BODY] and shared["body"] != BODY


def test_head_marker_one_record_behind_must_still_carry_that_records_hash(tmp_path):
    """Integration review 1: the documented crash artefact (marker one record behind) was accepted with ANY hash."""
    lp = tmp_path / "l.jsonl"
    L = Ledger(str(lp))
    L.append({"event": "kill", "code": "a"})
    first_hash = L._prev
    L.append({"event": "kill", "code": "b"})
    head = Path(str(lp) + ".head")
    head.write_text(f"1 {'deadbeef' * 8}")
    with pytest.raises(LedgerCorrupt):
        Ledger(str(lp)).replay()
    head.write_text(f"1 {first_hash}")                        # the genuine crash artefact: still accepted
    assert len(Ledger(str(lp)).replay()) == 2


def test_head_marker_two_behind_or_ahead_is_corrupt(tmp_path):
    lp = tmp_path / "l.jsonl"
    L = Ledger(str(lp))
    for i in range(3):
        L.append({"event": "kill", "code": str(i)})
    for bad in ("1 " + "0" * 64, "9 " + L._prev):
        Path(str(lp) + ".head").write_text(bad)
        with pytest.raises(LedgerCorrupt):
            Ledger(str(lp)).replay()


def test_absent_ledger_accepts_only_an_initial_or_missing_head_marker(tmp_path):
    """Recovery 02 / F03: with the ledger file absent, a marker `0 <non-zero hash>` was accepted as an empty history."""
    lp = tmp_path / "l.jsonl"
    head = Path(str(lp) + ".head")
    assert Ledger(str(lp)).replay() == []                        # nothing at all: a fresh install
    head.write_text(f"0 {'0' * 64}")
    assert Ledger(str(lp)).replay() == []                        # the initial marker: consistent with an empty history
    for bad in (f"0 {'deadbeef' * 8}", f"1 {'0' * 64}", f"3 {'ab' * 32}"):
        head.write_text(bad)
        with pytest.raises(LedgerCorrupt):
            Ledger(str(lp)).replay()


def test_deleted_ledger_with_a_genuine_head_is_corrupt_and_never_reset_to_zero_spend(tmp_path):
    lp = tmp_path / "l.jsonl"
    L = Ledger(str(lp))
    L.append({"event": "kill", "code": "a"})
    lp.unlink()                                                  # whoever deletes the ledger must not obtain a clean slate
    with pytest.raises(LedgerCorrupt):
        Ledger(str(lp)).replay()


def test_restart_replays_an_intact_ledger_and_continues_the_chain(tmp_path):
    lp = tmp_path / "l.jsonl"
    L = Ledger(str(lp))
    L.append({"event": "kill", "code": "a"})
    L.append({"event": "kill", "code": "b"})
    L2 = Ledger(str(lp))
    assert len(L2.replay()) == 2
    L2.append({"event": "kill", "code": "c"})
    assert len(Ledger(str(lp)).replay()) == 3
