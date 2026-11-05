"""UNIFIED NIGHT SPRINT 01 (pure logic, no model): provider status, the simulated explanation of an `mg.incident/1` record, and containment of the ported governor's neighbours.
Expectations come from the stated rules (no external call, no spend, severity decided by deterministic rules only, UNKNOWN is never green, a simulation is never presented as real inference), not from output."""
import ast
import json
from pathlib import Path

import pytest

from mineguard import explain, providers

SRC = Path(__file__).resolve().parents[1] / "mineguard"


def test_provider_status_is_fixed_by_construction_and_ignores_the_environment(monkeypatch):
    for k, v in {"MG_META_LIVE_PROVIDER_ENABLED": "true", "MG_META_MAX_REAL_REQUESTS": "9", "MG_META_MAX_AUTHORIZED_SPEND_USD": "50", "META_API_KEY": "x" * 40, "BASETEN_API_KEY": "y" * 40}.items():
        monkeypatch.setenv(k, v)                                           # credentials / flags in the environment are NOT authorization
    doc = providers.status({"verified": True, "model_id": "m", "weights_sha256": "0" * 64})
    by = {p["provider_id"]: p for p in doc["providers"]}
    assert set(by) == {"local_rfdetr", "meta_sam31", "muse_spark", "baseten", "modal"}
    assert by["local_rfdetr"]["states"] == ["LOCAL_READY"] and by["local_rfdetr"]["real_inference"] is True and by["local_rfdetr"]["accuracy_validated"] is False
    for pid in ("meta_sam31", "baseten", "modal"):
        assert by[pid]["real_inference"] is False and by[pid]["external_call_possible"] is False
        assert "CREDENTIALS_NOT_CONFIGURED" in by[pid]["states"] and "BILLING_NOT_APPROVED" in by[pid]["states"]
    assert "META_DISABLED" in by["meta_sam31"]["states"] and "LIVE_NOT_VERIFIED" in by["meta_sam31"]["states"] and "META_DISABLED" not in by["baseten"]["states"]
    assert by["muse_spark"]["states"] == ["SIMULATED"] and by["muse_spark"]["real_inference"] is False and by["muse_spark"]["external_call_possible"] is False
    assert all(s in providers.STATES for p in doc["providers"] for s in p["states"])
    cost = doc["cost"]
    assert cost["real_transport"] == "NullRealTransport" and cost["max_real_requests"] == 0 and cost["max_authorized_spend_usd"] == "0"
    assert cost["ui_can_enable_spending"] is False and cost["zero_charge_guaranteed"] is False and cost["authorized_external_calls"] == 0 and cost["authorized_external_spend_usd"] == "0"
    assert doc["invariants"] == {"external_calls_possible": False, "credentials_read": False, "environment_consulted_for_providers": False}


def test_provider_status_reports_unavailable_local_model_without_inventing_readiness():
    doc = providers.status({"verified": False, "error": "weights missing", "model_id": "m", "weights_sha256": "0" * 64})
    assert doc["providers"][0]["states"] == ["LOCAL_UNAVAILABLE"] and doc["providers"][0]["real_inference"] is False
    assert providers.status({})["providers"][0]["states"] == ["ERROR"]


def test_new_modules_contain_no_network_client():
    banned = {"socket", "http", "urllib", "requests", "httpx", "aiohttp", "ssl", "ftplib", "smtplib", "websockets"}
    for name in ("explain.py", "providers.py", "costgov.py", "rawjson.py"):
        tree = ast.parse((SRC / name).read_text(encoding="utf-8"))
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                mods.add(n.module.split(".")[0])
        assert not (mods & banned), (name, mods & banned)


def _view_inc(classification="synthetic", severity="RED", unknown=True, lifecycle=False, kind="scripted_simulation"):
    return {"incident_id": "alt_abc", "classification": classification, "identity": {"event_id": "evt_ab12", "zone_id": "zone_1"},
            "risk": {"severity": severity, "rule_id": "R_TEST", "engine_version": "mg-risk-kernel/2.0.3", "semantics_version": "1.0"},
            "observed": {"start_us": 1_200_000, "end_us": None}, "provenance": {"provider": {"kind": kind}, "simulation": classification == "synthetic"},
            "unknown_intervals": {"intervals": [{"start_us": 0, "end_us": 100_000, "cause": "identity_unavailable"}] if unknown else []},
            "timeline": {"lifecycle": {"recorded": lifecycle}}}


@pytest.mark.parametrize("mode", [m for m in explain.FakeMuseTransport.MODES if m != "ok"])


def test_every_scripted_fault_is_rejected_never_repaired(mode):
    inc = _view_inc()
    out = explain.generate(inc, explain.FakeMuseTransport(mode))
    assert out["status"] == "REJECTED" and out["codes"] and "report" not in out


@pytest.mark.parametrize("classification", ["synthetic", "restored", "repeated_source_simulation", "live_feed_metadata"])


@pytest.mark.parametrize("severity", ["YELLOW", "RED"])


def test_faithful_explanation_is_accepted_for_every_source_class_and_names_the_origin_truthfully(classification, severity):
    out = explain.generate(_view_inc(classification, severity, kind="model_inference" if classification == "live_feed_metadata" else "scripted_simulation"))
    assert out["status"] == "ACCEPTED", out
    res = out["report"]["resumen"]
    if classification == "synthetic":
        assert "no es inferencia real" in res
    else:
        assert "no es inferencia real" not in res and "simulacion con guion" not in res
    assert out["report"]["deterministic_state"] == severity


def test_unknown_intervals_are_stated_as_unknown_and_never_as_ok():
    out = explain.generate(_view_inc(unknown=True))
    facts = " ".join(f["texto"] for f in out["report"]["hechos_observados"])
    assert "desconocido" in facts.lower() and "identity_unavailable" in facts
    blob = json.dumps(out["report"], ensure_ascii=False).lower()
    assert "verde" not in blob and "sin riesgo" not in blob and "seguro" not in blob


def test_missing_lifecycle_is_declared_in_the_uncertainties():
    assert explain.NO_LIFE_SENTENCE in explain.generate(_view_inc(lifecycle=False))["report"]["incertidumbres"]
    assert explain.NO_LIFE_SENTENCE not in explain.generate(_view_inc(lifecycle=True))["report"]["incertidumbres"]


def test_a_text_that_names_people_or_vehicles_is_rejected_as_an_invented_detection():
    inc = _view_inc()
    view = explain.muse_view(inc)
    prompt = explain.build_prompt(view)
    rep = json.loads(explain.FakeMuseTransport().complete(prompt))
    rep["resumen"] = "Se vio una persona cerca del vehiculo."
    with pytest.raises(explain.ReportRejected) as e:
        explain.validate_report(json.dumps(rep), view, prompt)
    assert "unsupported_entity_claim" in e.value.codes


def _reject_with(**edit):
    inc = _view_inc()
    view = explain.muse_view(inc)
    prompt = explain.build_prompt(view)
    rep = json.loads(explain.FakeMuseTransport().complete(prompt))
    for k, v in edit.items():
        rep[k] = v
    with pytest.raises(explain.ReportRejected) as e:
        explain.validate_report(json.dumps(rep), view, prompt)
    return e.value.codes


def test_each_dishonesty_class_is_caught_by_its_OWN_check_not_by_an_overlapping_one():
    assert "collision_claim" in _reject_with(resumen="Ocurrio una colision en la zona Z1.")
    assert "invented_number" in _reject_with(resumen="La zona Z1 tuvo 99 episodios.")
    assert "action_language" in _reject_with(explicacion_decision="Se debe enviar un aviso al responsable.")
    assert "risk_downplayed" in _reject_with(explicacion_decision="Todo bien en la zona Z1.")
    assert "state_changed" in _reject_with(deterministic_state="YELLOW")
    assert "video_inspection_claim" in _reject_with(resumen="Revise el video de la zona Z1.")


def test_a_fact_text_that_differs_from_the_incident_record_is_rejected_even_with_a_valid_ref():
    inc = _view_inc()
    view = explain.muse_view(inc)
    prompt = explain.build_prompt(view)
    rep = json.loads(explain.FakeMuseTransport().complete(prompt))
    rep["hechos_observados"][0]["texto"] = "Evento R_TEST severidad RED desde t=1.2 s hasta t=1.3 s."
    with pytest.raises(explain.ReportRejected) as e:
        explain.validate_report(json.dumps(rep), view, prompt)
    assert "fact_text_not_bound_to_incident" in e.value.codes


def test_the_output_names_the_deterministic_policy_and_the_incident_severity():
    out = explain.generate(_view_inc(severity="YELLOW"))
    assert out["decided_by"] == "deterministic_policy" and out["severity"] == "YELLOW" and out["real_muse_call"] is False and out["kind"] == "SIMULATED_EXPLANATION"


@pytest.mark.parametrize("kind,must,must_not", [
    ("model_inference", "archivo de video registrado", "camara"),
    ("webcam_model_inference", "camara declarada por el navegador", "archivo de video registrado"),
    ("something_new", "tipo de fuente no especificado", "archivo de video registrado"),
    (None, "tipo de fuente no especificado", "camara declarada"),
])
def test_a_live_record_is_described_by_the_provider_kind_it_carries_never_more_specifically(kind, must, must_not):
    """Integration review 1: a webcam incident was described as a registered video file."""
    out = explain.generate(_view_inc("live_feed_metadata", "RED", kind=kind))
    assert out["status"] == "ACCEPTED", out
    res = out["report"]["resumen"]
    assert must in res and must_not not in res and "no es inferencia real" not in res


@pytest.mark.parametrize("text", ["El video fue inspeccionado completamente.", "Las imagenes fueron revisadas por el modelo.", "Se analizo el video de la zona Z1.",
                                  "Analice la grabacion antes de resumir.", "El video quedo examinado.", "La grabacion fue vista por el sistema."])
def test_every_form_of_a_video_inspection_claim_is_rejected(text):
    assert "video_inspection_claim" in _reject_with(resumen=text)


# ---- Recovery 02 -------------------------------------------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["Se evaluó el video completo.", "Se evaluo el video completo.", "El video fue verificado por el sistema.", "Comprobamos las imagenes de la zona Z1.",
                                  "Se confirmo con la grabacion.", "El material filmado fue estudiado a fondo.", "Las imagenes quedaron auditadas.", "Vimos el clip entero."])
def test_F02_equivalent_and_variant_video_claims_are_rejected(text):
    codes = _reject_with(resumen=text)
    assert "video_inspection_claim" in codes or "narrative_not_template" in codes
    assert "narrative_not_template" in codes                       # whatever the regex knows, free narrative is never accepted


@pytest.mark.parametrize("field,text", [("explicacion_decision", "Se evaluó el video completo."), ("incertidumbres", ["Se evaluó el video completo."]),
                                        ("pasos_revision_humana", ["Se evaluó el video completo."])])
def test_F02_the_claim_is_rejected_in_every_narrative_field(field, text):
    assert "narrative_not_template" in _reject_with(**{field: text})


def test_F02_an_unknown_paraphrase_the_lexical_scan_cannot_know_is_still_rejected_by_the_closed_template():
    codes = _reject_with(resumen="Resumen simulado: el metraje fue ponderado a fondo.")
    assert codes == ["narrative_not_template"]                     # no lexical check fires; only the template binding catches it


def test_F02_the_faithful_template_is_still_accepted_and_the_severity_is_untouched():
    out = explain.generate(_view_inc(severity="RED"))
    assert out["status"] == "ACCEPTED" and out["severity"] == "RED" and out["report"]["deterministic_state"] == "RED"


def test_F02_a_rejected_claim_cannot_change_the_incident_or_its_severity():
    inc = _view_inc(severity="YELLOW")
    before = json.dumps(inc, sort_keys=True)
    class Liar(explain.FakeMuseTransport):
        def complete(self, prompt):
            rep = json.loads(super().complete(prompt))
            rep["resumen"] = "Se evaluó el video completo y la zona es segura."
            return json.dumps(rep)
    out = explain.generate(inc, Liar())
    assert out["status"] == "REJECTED" and out["severity"] == "YELLOW" and json.dumps(inc, sort_keys=True) == before


_LABEL = {"synthetic": "no es inferencia real", "restored": "registro restaurado del almacen local", "repeated_source_simulation": "archivo repetido (no son dos camaras)",
          "live_feed_metadata": "archivo de video registrado"}


@pytest.mark.parametrize("classification", list(_LABEL))
def test_F04_every_source_class_names_exactly_its_own_origin_and_no_other(classification):
    out = explain.generate(_view_inc(classification, "RED", kind="model_inference" if classification == "live_feed_metadata" else "scripted_simulation"))
    assert out["status"] == "ACCEPTED", out
    res = out["report"]["resumen"]
    assert _LABEL[classification] in res
    for other, label in _LABEL.items():
        if other != classification:
            assert label not in res, (classification, other)


@pytest.mark.parametrize("real,claimed", [("restored", "live_feed_metadata"), ("repeated_source_simulation", "live_feed_metadata"), ("restored", "synthetic"),
                                          ("repeated_source_simulation", "restored"), ("synthetic", "live_feed_metadata")])
def test_F04_a_report_that_misdescribes_the_provenance_is_rejected(real, claimed):
    inc = _view_inc(real, "RED")
    view = explain.muse_view(inc)
    prompt = explain.build_prompt(view)
    rep = json.loads(explain.FakeMuseTransport().complete(prompt))
    rep["resumen"] = explain.expected_resumen(explain.muse_view(_view_inc(claimed, "RED", kind="model_inference")))
    with pytest.raises(explain.ReportRejected) as e:
        explain.validate_report(json.dumps(rep), view, prompt)
    assert "narrative_not_template" in e.value.codes


def test_F04_a_repeated_source_pair_keeps_two_distinct_incidents_with_the_same_origin_sentence():
    a = _view_inc("repeated_source_simulation", "RED"); b = dict(_view_inc("repeated_source_simulation", "RED"), incident_id="alt_other")
    ra, rb = explain.generate(a), explain.generate(b)
    assert ra["incident_id"] != rb["incident_id"] and ra["report"]["incident_id"] == "alt_abc" and rb["report"]["incident_id"] == "alt_other"
    assert ra["report"]["resumen"] == rb["report"]["resumen"] and "no son dos camaras" in ra["report"]["resumen"]


def _reject_with_for(inc, **edit):
    view = explain.muse_view(inc)
    prompt = explain.build_prompt(view)
    rep = json.loads(explain.FakeMuseTransport().complete(prompt))
    rep.update(edit)
    try:
        explain.validate_report(json.dumps(rep), view, prompt)
    except explain.ReportRejected as e:
        return e.codes
    return None


def test_F02b_a_template_sentence_that_is_false_for_THIS_incident_is_rejected():
    """Review 1 of Recovery 02: NO_LIFE_SENTENCE passed set membership even when the lifecycle WAS recorded."""
    rec = _view_inc(lifecycle=True)
    assert _reject_with_for(rec, incertidumbres=[explain.SIM_SENTENCE, explain.NO_LIFE_SENTENCE, explain.GEOMETRY_SENTENCE]) == ["narrative_not_template"]
    nolife = _view_inc(lifecycle=False)
    assert _reject_with_for(nolife, incertidumbres=[explain.SIM_SENTENCE, explain.GEOMETRY_SENTENCE]) == ["narrative_not_template"]   # omitting a required disclosure is also rejected


def test_F02b_sentences_cannot_be_moved_between_fields_or_reordered_or_dropped():
    inc = _view_inc(lifecycle=True)
    assert _reject_with_for(inc, pasos_revision_humana=[explain.SIM_SENTENCE]) == ["narrative_not_template"]
    assert _reject_with_for(inc, pasos_revision_humana=list(reversed(explain.STEPS))) == ["narrative_not_template"]
    assert _reject_with_for(inc, pasos_revision_humana=explain.STEPS[:1]) == ["narrative_not_template"]
    assert _reject_with_for(inc, incertidumbres=[explain.STEPS[0]]) == ["narrative_not_template"]
    assert _reject_with_for(inc) is None                                                              # the faithful report is still accepted
