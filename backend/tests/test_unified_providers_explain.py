"""UNIFIED NIGHT SPRINT 01 (routes, with the real-model stored run fixture): provider status route and the simulated explanation route.
The pure-logic tests of the same additions are in `test_unified_explain_unit.py` (they need no model and run in the mutation harness)."""
import os

from mineguard import providers
from test_rc1_closeout import iso  # noqa: F401
from test_product_vertical06 import sim_incident

def test_provider_route_reports_the_verified_local_model_and_ignores_the_environment(iso, monkeypatch):
    for k, v in {"MG_META_LIVE_PROVIDER_ENABLED": "true", "MG_META_MAX_REAL_REQUESTS": "9", "META_API_KEY": "x" * 40}.items():
        monkeypatch.setenv(k, v)
    doc = iso.client.get("/api/providers").json()
    by = {p["provider_id"]: p for p in doc["providers"]}
    assert by["local_rfdetr"]["states"] == ["LOCAL_READY"] and by["local_rfdetr"]["weights_verified"] is True and by["local_rfdetr"]["real_inference"] is True
    assert doc["cost"]["max_real_requests"] == 0 and doc["invariants"]["external_calls_possible"] is False
    assert "META_DISABLED" in by["meta_sam31"]["states"]


def test_no_route_can_change_provider_or_spend_configuration(iso):
    c = iso.client
    for m in ("POST", "PUT", "PATCH", "DELETE"):
        r = c.request(m, "/api/providers", json={"live": True})
        assert r.status_code in (404, 405, 403), (m, r.status_code)


def test_explanation_of_a_scripted_incident_is_simulated_validated_and_leaves_severity_alone(iso):
    c = iso.client
    rid, aid = sim_incident(iso)
    before = c.get(f"/api/incidents/{aid}").json()
    out = c.get(f"/api/incidents/{aid}/explanation").json()
    after = c.get(f"/api/incidents/{aid}").json()
    assert {k: v for k, v in before.items() if k != "generated_utc"} == {k: v for k, v in after.items() if k != "generated_utc"}   # the explanation cannot alter the record
    assert out["status"] == "ACCEPTED" and out["kind"] == "SIMULATED_EXPLANATION" and out["simulated"] is True and out["real_muse_call"] is False
    assert out["severity"] == before["risk"]["severity"] and out["report"]["deterministic_state"] == before["risk"]["severity"] and out["decided_by"] == "deterministic_policy"
    assert out["report"]["video_inspected"] is False and out["report"]["evidencia"] == []
    assert "simulacion con guion" in out["report"]["resumen"] and "inferencia real" in out["report"]["resumen"] and "no es inferencia real" in out["report"]["resumen"]
    assert "semantically" in out["validation"]


def test_explanation_of_an_unknown_incident_is_404(iso):
    assert iso.client.get("/api/incidents/alt_doesnotexist/explanation").status_code in (404, 422)
