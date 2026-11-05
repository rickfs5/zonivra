"""META LIVE READINESS 04: Muse Spark Open via Baseten: request builder and the REAL-model validation profile, tested with a loopback server only.
The model text is a subordinate layer: it never changes severity, and lexical validation is never presented as truth."""
import json
from decimal import Decimal

import pytest

from mineguard import costgov, explain, metahttp, muse_open as M
from mineguard.costgov import GovernorConfig, SpendGovernor
from mineguard.metahttp import EndpointBoundGuard, TestHttpsTransport

import test_meta_transport as T
from test_meta_transport import no_external_dns  # noqa: F401  (fixture: any external name resolution fails the test)

INC = {"incident_id": "alt_abc", "classification": "synthetic", "identity": {"event_id": "evt_ab12", "zone_id": "zone_1"},
       "risk": {"severity": "RED", "rule_id": "R_TEST", "engine_version": "mg-risk-kernel/2.0.3", "semantics_version": "1.0"}, "observed": {"start_us": 1_200_000, "end_us": None},
       "provenance": {"provider": {"kind": "scripted_simulation"}, "simulation": True}, "unknown_intervals": {"intervals": []}, "timeline": {"lifecycle": {"recorded": True}}}
VIEW = explain.muse_view(INC)
GOOD = {"resumen": "La regla determinista dejo la zona zone_1 en estado RED con 1 evento desde 1.2 s.", "incertidumbres": ["La geometria es en pixeles de imagen."],
        "pasos_revision_humana": ["Revisar los eventos contra la fuente original."]}


def resp(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}], "usage": {"prompt_tokens": 10, "completion_tokens": 20}}


def test_body_has_only_documented_unproblematic_fields_and_no_unsupported_feature():
    b = M.build_body(VIEW, M.OWNER_COMMUNICATED_SLUG)
    assert set(b) == {"model", "messages", "max_tokens"} and "response_format" not in b and "n" not in b and "temperature" not in b and "stream" not in b
    assert b["model"] == "meta-models/muse-spark-open-1026" and [m["role"] for m in b["messages"]] == ["system", "user"]


def test_only_the_incident_projection_is_sent_never_video_paths_tokens_or_keys():
    u = json.loads(M.build_body(VIEW, "meta-models/x-y")["messages"][1]["content"])
    assert u == VIEW and set(u) == {"schema", "incident_id", "source", "risk", "events", "unknown_intervals", "lifecycle_recorded"}
    blob = json.dumps(M.build_body(VIEW, "meta-models/x-y")).replace("max_tokens", "")
    for forbidden in ("/Users", "Bearer", "LLM_", "token", ".mp4", "frame"):
        assert forbidden not in blob


@pytest.mark.parametrize("model", ["", "a b", "x" * 101, None, "../etc", "a;b"])
def test_invalid_model_slug_is_refused(model):
    with pytest.raises(ValueError):
        M.build_body(VIEW, model)


@pytest.mark.parametrize("mt", [0, 49, 1001, True, "500", None])
def test_max_tokens_is_bounded(mt):
    with pytest.raises(ValueError):
        M.build_body(VIEW, "meta-models/x-y", mt)


def test_governed_request_declares_a_conservative_token_estimate_and_no_media():
    r = M.build_governed_request(VIEW, "meta-models/x-y", "req-0000001", 500)
    assert set(r) <= costgov.ALLOWED_REQUEST_KEYS and r["media"] is False and r["est_tokens"] >= 500 and costgov.count_media_parts(r["body"]) == 0


def test_a_faithful_model_text_is_accepted():
    assert M.validate_model_text(json.dumps(GOOD), VIEW)["resumen"].startswith("La regla")
    assert M.validate_model_text("```json\n" + json.dumps(GOOD) + "\n```", VIEW)["incertidumbres"]


@pytest.mark.parametrize("edit,code", [({"resumen": "Se evaluó el video completo."}, "video_inspection_claim"), ({"resumen": "Ocurrio una colision."}, "collision_claim"),
                                       ({"resumen": "El estado es amarillo."}, "state_mentioned_mismatch"), ({"resumen": "Todo bien, sin riesgo."}, "risk_downplayed"),
                                       ({"resumen": "Hay 99 episodios."}, "invented_number"), ({"resumen": "A 5 metros del vehiculo."}, "physical_measure_claim"),
                                       ({"resumen": "Se debe evacuar la zona."}, "action_language"), ({"resumen": "Una persona cruzo."}, "unsupported_entity_claim"),
                                       ({"pasos_revision_humana": ["Enviar una alerta al supervisor."]}, "action_language"), ({"incertidumbres": ["Se reviso la grabacion."]}, "video_inspection_claim")])
def test_each_dishonesty_class_is_caught_by_its_own_check(edit, code):
    with pytest.raises(M.ModelTextRejected) as e:
        M.validate_model_text(json.dumps({**GOOD, **edit}), VIEW)
    assert code in e.value.codes


@pytest.mark.parametrize("text", ["", "   ", "not json", "[]", '{"resumen":"x"}', json.dumps({**GOOD, "extra": 1}), json.dumps({**GOOD, "incertidumbres": []}), json.dumps({**GOOD, "resumen": "a" * 401}),
                                  json.dumps({**GOOD, "pasos_revision_humana": ["x"] * 5}), json.dumps({**GOOD, "resumen": 5}), "x" * 7000, None, 5])
def test_malformed_or_oversized_output_is_rejected_never_repaired(text):
    with pytest.raises(M.ModelTextRejected):
        M.validate_model_text(text, VIEW)


def test_compose_keeps_the_incident_severity_and_labels_a_mock_as_simulated():
    out = M.compose(INC, resp(json.dumps(GOOD)), model=M.OWNER_COMMUNICATED_SLUG)
    assert out["status"] == "ACCEPTED" and out["kind"] == "SIMULATED_EXPLANATION" and out["real_muse_call"] is False and out["severity"] == "RED" and out["decided_by"] == "deterministic_policy"
    assert out["model_slug_status"] == "UNVERIFIED" and "does not prove" in out["validation"] and out["attestation"] is None
    liar = M.compose(INC, resp(json.dumps({**GOOD, "resumen": "El estado es verde."})), model="meta-models/x-y")
    assert liar["status"] == "REJECTED" and liar["severity"] == "RED" and "report" not in liar


def test_the_incident_is_never_mutated_by_any_explanation_outcome():
    before = json.dumps(INC, sort_keys=True)
    M.compose(INC, resp("garbage"), model="meta-models/x-y")
    assert json.dumps(INC, sort_keys=True) == before


def _guarded(tmp_path, srv, model="meta-models/x-y", enabled=True):
    body = M.build_body(VIEW, model)
    cfg = GovernorConfig(live_provider_enabled=enabled, max_real_requests=1, max_authorized_spend_usd=Decimal(1), price_usd_per_request=((model, Decimal("0.001")),),
                         provider_budget_usd=((M.PROVIDER_ID, Decimal(1)),), max_tokens=5000, max_concurrency=1, authorized_payload_digests=frozenset({costgov.raw_digest(body)}),
                         ledger_path=str(tmp_path / "l.jsonl"), request_timeout_s=5)
    gov = SpendGovernor(cfg)
    inner = TestHttpsTransport(f"http://127.0.0.1:{srv.port}/v1", path="/chat/completions", provider=M.PROVIDER_ID, governor=gov, authorized={(M.PROVIDER_ID, model, costgov.raw_digest(body))}, allow_loopback_http=True)
    return EndpointBoundGuard(inner, gov, lambda: T.KEY), inner


def test_loopback_round_trip_validates_the_text_and_labels_the_mock_simulated(tmp_path, no_external_dns):
    srv = T.Srv([(200, {}, json.dumps(resp(json.dumps(GOOD))).encode(), 0)])
    try:
        g, inner = _guarded(tmp_path, srv)
        out = M.MuseOpenClient(g).explain(INC, "meta-models/x-y", "req-muse-01")
        assert out["outcome"] == "OK" and out["explanation"]["status"] == "ACCEPTED" and out["explanation"]["kind"] == "SIMULATED_EXPLANATION" and len(srv.hits) == 1
        h = srv.hits[0]
        sent = json.loads(h["body"])
        assert h["path"] == "/v1/chat/completions" and h["headers"]["Authorization"] == "Bearer " + T.KEY and T.KEY.encode() not in h["body"]
        assert set(sent) == {"model", "messages", "max_tokens"} and h["headers"]["Accept"] == "application/json"
    finally:
        srv.close()


@pytest.mark.parametrize("status,code", [(402, "unexpected_provider_response"), (429, "provider_quota_exceeded"), (529, "provider_server_error"), (500, "provider_server_error"), (401, "invalid_credentials")])
def test_baseten_errors_are_denials_counted_once_and_not_retried(tmp_path, no_external_dns, status, code):
    srv = T.Srv([(status, {}, json.dumps({"error": {"message": "x", "type": "t", "code": None}}).encode(), 0), (200, {}, b"{}", 0)])
    try:
        g, inner = _guarded(tmp_path, srv)
        out = M.MuseOpenClient(g).explain(INC, "meta-models/x-y", "req-muse-02")
        assert out["outcome"] == "DENIED" and out["code"] == code and out["explanation"] is None and len(srv.hits) == 1 and T.KEY not in json.dumps(out)
    finally:
        srv.close()


def test_disabled_provider_sends_nothing(tmp_path, no_external_dns):
    srv = T.Srv([(200, {}, b"{}", 0)])
    try:
        g, inner = _guarded(tmp_path, srv, enabled=False)
        out = M.MuseOpenClient(g).explain(INC, "meta-models/x-y", "req-muse-03")
        assert out["code"] == "live_provider_disabled" and srv.hits == []
    finally:
        srv.close()


def test_contract_marks_the_slug_unverified_and_structured_output_unsupported():
    by = {f: s for f, _, s, _ in M.CONTRACT}
    assert by["model slug"] == "UNVERIFIED" and by["structured output / JSON mode"] == "VERIFIED" and by["rate limits / pricing / credits for this model"] == "UNKNOWN"
