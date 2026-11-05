"""Explanation layer for a MineGuard incident (EXPERIMENTAL, SIMULATED). PORTED from the Meta Lab `muse.py` (validator unchanged in logic) and bound to the authoritative `mg.incident/1` record.
A deterministic fake transport stands in for the model; NO model call exists. The explanation is untrusted text: it is parsed into a strict schema and checked against the incident it was generated from.
It cannot change severity, state or status, add evidence, invent detections or numbers, claim a collision, claim video was inspected, or carry actions: any such output is REJECTED, never repaired.
A passing validation is structural and lexical; it does NOT prove the text is semantically true. Severity is decided by the deterministic rules, never by this layer."""
from __future__ import annotations

import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .canon import digest
from .rawjson import raw_digest

REPORT_SCHEMA = "mineguard.explanation.v0"
INPUT_SCHEMA = "mineguard.explanation.input/1"
FAKE_MODEL_ID = "muse-spark (NOT CALLED: deterministic fake transport)"
PROMPT_VERSION = "mineguard-explain-prompt/0.1"
STATES = ("GREEN", "YELLOW", "RED", "UNKNOWN")


_ORIGIN = {"synthetic": "simulacion con guion (sintetica, no es inferencia real)", "restored": "registro restaurado del almacen local (historia, no en vivo)",
           "repeated_source_simulation": "analisis local del modelo sobre un archivo repetido (no son dos camaras)"}
# a LIVE record is described by the provider kind the incident itself carries; an unknown kind gets the generic sentence, never a more specific one
_LIVE_ORIGIN = {"model_inference": "analisis local del modelo sobre un archivo de video registrado",
                "webcam_model_inference": "analisis local del modelo sobre una camara declarada por el navegador (origen no verificado)"}


def origin_label(classification: str, provider_kind: str | None) -> str:
    if classification == "live_feed_metadata":
        return _LIVE_ORIGIN.get(provider_kind or "", "analisis local del modelo (tipo de fuente no especificado)")
    return _ORIGIN.get(classification, "origen no clasificado")


def muse_view(inc: dict) -> dict:
    """The ONLY facts the explanation may use, projected from an `mg.incident/1` record. Nothing is added: a missing fact stays missing."""
    risk, ident, obs, prov = inc["risk"], inc["identity"], inc["observed"], inc["provenance"]
    sev = risk["severity"]
    events = []
    if ident.get("event_id") and risk.get("rule_id") and obs.get("start_us") is not None:
        events.append({"event_id": ident["event_id"], "rule_id": risk["rule_id"], "severity": sev, "start_us": obs["start_us"], "end_us": obs.get("end_us"), "tracks": []})
    unknown = [{"n": n, "start_us": u["start_us"], "end_us": u["end_us"], "causes": [u["cause"]] if u.get("cause") else []}
               for n, u in enumerate((inc.get("unknown_intervals") or {}).get("intervals") or [])]
    life = (inc.get("timeline") or {}).get("lifecycle") or {}
    return {"schema": INPUT_SCHEMA, "incident_id": inc["incident_id"],
            "source": {"classification": inc["classification"], "origin_label": origin_label(inc["classification"], (prov.get("provider") or {}).get("kind")), "provider_kind": (prov.get("provider") or {}).get("kind"),
                       "simulation": bool(prov.get("simulation"))},
            "risk": {"engine_version": str(risk.get("engine_version") or ""), "policy_id": "mineguard-deterministic-policy", "policy_version": str(risk.get("semantics_version") or ""),
                     "zone_id": str(ident["zone_id"]), "final_state": sev, "peak_event_severity": sev},
            "events": events, "unknown_intervals": unknown, "lifecycle_recorded": bool(life.get("recorded"))}


def build_prompt(incident: dict) -> dict:
    p = {"prompt_version": PROMPT_VERSION, "language": "es", "instructions": [
        "Resume el incidente en espanol, solo con los datos del registro.", "No cambies la severidad ni el estado.", "No inventes detecciones, evidencia, numeros ni distancias.",
        "No afirmes colision ni que se reviso video.", "No ejecutes acciones.", "Lista incertidumbres y pasos de revision humana."], "incident": incident}
    p["prompt_digest"] = digest({k: v for k, v in p.items() if k != "prompt_digest"})
    return p


def _s(t: int | None) -> str:
    return "?" if t is None else f"{t / 1_000_000:.1f}"


NO_EVENTS_FACT = "El motor determinista no registro eventos de riesgo en la zona."
NO_LIFE_SENTENCE = "El registro del ciclo de vida de la fuente no esta disponible para este incidente."
SIM_SENTENCE = "Esta explicacion es simulada: no se llamo a ningun modelo y no se inspecciono video ni imagenes."
TEMPLATE_SENTENCES = frozenset({
    "La severidad la fija el motor de reglas determinista, no el modelo.", SIM_SENTENCE, NO_LIFE_SENTENCE,
    "La geometria es en pixeles de imagen; no hay distancias fisicas.",
    "Revisar los eventos listados contra la fuente original antes de actuar.", "Confirmar la zona y la politica con el responsable."})


def expected_resumen(inc: dict) -> str:
    return f"Resumen simulado: la regla determinista dejo la zona {inc['risk']['zone_id']} en estado {inc['risk']['final_state']}. Origen: {inc['source']['origin_label']}."


def expected_decision(inc: dict) -> str:
    return f"El estado final {inc['risk']['final_state']} proviene del motor de reglas determinista" + (f" {inc['risk']['engine_version']}." if inc["risk"]["engine_version"] else ".")


GEOMETRY_SENTENCE = "La geometria es en pixeles de imagen; no hay distancias fisicas."
STEPS = ["Revisar los eventos listados contra la fuente original antes de actuar.", "Confirmar la zona y la politica con el responsable."]


def expected_uncertainties(inc: dict) -> list[str]:
    return [SIM_SENTENCE, *([] if inc["lifecycle_recorded"] else [NO_LIFE_SENTENCE]), GEOMETRY_SENTENCE]


RULE_SENTENCE = "La severidad la fija el motor de reglas determinista, no el modelo."


def canonical_facts(incident: dict) -> dict[str, str]:
    """ref -> the ONLY text a fact with that ref may carry. A fact is bound to the incident record, not merely to an existing reference string."""
    out = {f"event:{e['event_id']}": f"Evento {e['rule_id']} severidad {e['severity']} desde t={_s(e['start_us'])} s hasta t={_s(e['end_us'])} s." for e in incident["events"]}
    for u in incident["unknown_intervals"]:
        out[f"interval:{u['n']}"] = f"Intervalo desconocido de t={_s(u['start_us'])} s a t={_s(u['end_us'])} s (causas: {', '.join(u['causes']) or 'no indicadas'})."
    out["provenance"] = NO_EVENTS_FACT
    return out


class FakeMuseTransport:
    """Deterministic. `mode` selects a scripted FAULT to exercise containment; 'ok' is the faithful template."""
    MODES = ("ok", "malformed_json", "changes_state", "invented_evidence", "claims_collision", "unknown_to_green", "claims_video_inspected", "action_field",
             "invented_detection", "wrong_rule", "invented_fact_ref", "extra_field", "wrong_prompt_digest")

    def __init__(self, mode: str = "ok"):
        assert mode in self.MODES
        self.mode, self.calls = mode, 0

    def complete(self, prompt: dict) -> str:
        self.calls += 1
        inc = prompt["incident"]
        st = inc["risk"]["final_state"]
        ev = inc["events"]
        rule = ev[0]["rule_id"] if ev else "ninguna"
        cf = canonical_facts(inc)
        facts = [{"texto": cf[f"event:{e['event_id']}"], "ref": f"event:{e['event_id']}"} for e in ev] + [{"texto": cf[f"interval:{u['n']}"], "ref": f"interval:{u['n']}"} for u in inc["unknown_intervals"]]
        facts = facts or [{"texto": NO_EVENTS_FACT, "ref": "provenance"}]
        rep = {"schema": REPORT_SCHEMA, "simulated": True, "incident_id": inc["incident_id"], "deterministic_state": st,
               "resumen": expected_resumen(inc),
               "hechos_observados": facts, "regla_aplicada": {"rule_id": rule, "explicacion": RULE_SENTENCE},
               "explicacion_decision": expected_decision(inc),
               "evidencia": [], "incertidumbres": expected_uncertainties(inc),
               "pasos_revision_humana": list(STEPS),
               "video_inspected": False, "provenance": {"model_id": FAKE_MODEL_ID, "prompt_digest": prompt["prompt_digest"], "transport": "fake"}}
        m = self.mode
        if m == "malformed_json":
            return '{"schema": "mineguard.explanation.v0", '
        if m == "changes_state":
            rep["deterministic_state"] = "GREEN" if st != "GREEN" else "RED"
        elif m == "invented_evidence":
            rep["evidencia"] = ["frame:0007.png"]
        elif m == "claims_collision":
            rep["resumen"] = "Ocurrio una colision entre la persona y el vehiculo."
        elif m == "unknown_to_green":
            rep["deterministic_state"] = "GREEN"
            rep["resumen"] = "La zona esta en verde, sin riesgo."
        elif m == "claims_video_inspected":
            rep["resumen"] = "Revise el video completo y confirmo los hechos."
        elif m == "action_field":
            rep["acciones"] = [{"tipo": "enviar_notificacion"}]
        elif m == "invented_detection":
            rep["hechos_observados"].append({"texto": "Se detectaron 7 personas adicionales a 12 metros.", "ref": "provenance"})
        elif m == "wrong_rule":
            rep["regla_aplicada"]["rule_id"] = "R_INVENTED"
        elif m == "invented_fact_ref":
            rep["hechos_observados"][0]["ref"] = "event:evt_0000000000000000"
        elif m == "extra_field":
            rep["nuevo_estado_incidente"] = "cerrado"
        elif m == "wrong_prompt_digest":
            rep["provenance"]["prompt_digest"] = "0" * 64
        return json.dumps(rep, ensure_ascii=False)


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Fact(_M):
    texto: str = Field(min_length=1, max_length=300)
    ref: str


class Rule(_M):
    rule_id: str
    explicacion: str = Field(min_length=1, max_length=300)


class Prov(_M):
    model_id: str
    prompt_digest: str
    transport: Literal["fake"]


class Report(_M):
    schema_: Literal["mineguard.explanation.v0"] = Field(alias="schema")
    simulated: Literal[True]
    incident_id: str
    deterministic_state: Literal["GREEN", "YELLOW", "RED", "UNKNOWN"]
    resumen: str = Field(min_length=1, max_length=400)
    hechos_observados: list[Fact] = Field(min_length=1, max_length=20)
    regla_aplicada: Rule
    explicacion_decision: str = Field(min_length=1, max_length=400)
    evidencia: list[str] = Field(max_length=0)   # no evidence files exist in the lab; the model may not create or cite any
    incertidumbres: list[str] = Field(min_length=1, max_length=10)
    pasos_revision_humana: list[str] = Field(min_length=1, max_length=10)
    video_inspected: Literal[False]
    provenance: Prov


class ReportRejected(ValueError):
    def __init__(self, codes: list[str]):
        super().__init__(",".join(codes))
        self.codes = codes


_COLLISION = re.compile(r"\b(colisi\w*|choque\w*|choc\w*|atropell\w*|impact\w*|embisti\w*)\b", re.I)
_VIDEO_NOUN = r"(?:video|v[ií]deo|grabaci[oó]n\w*|im[aá]genes?|filmaci[oó]n\w*|filmad\w+|clips?|metraje|footage|fotogramas?|cuadros?|frames?|material)"
_VIDEO_VERB = (r"(?:(?:revis|inspeccion|inspecc|analiz|analic|analis|examin|visualiz|visualic|evalu|verific|comprob|estudi|escane|repas|audit|chequ|proces|cotej|confirm|obser|"
               r"contrast|valid|vigil|monitor|rastre|escrut|supervis|mir)\w*|vi|vio|vimos|vist[oa]s?|ve|ven)")
_VIDEO = re.compile(rf"\b{_VIDEO_VERB}\b[^.]{{0,40}}\b{_VIDEO_NOUN}\b|\b{_VIDEO_NOUN}\b[^.]{{0,40}}\b{_VIDEO_VERB}\b", re.I)   # any verb of looking/checking near a video noun, in either order; the closed templates below are the real guarantee
_OK_WORDS = re.compile(r"\b(verde|sin\s+riesgo|seguro|sin\s+peligro|todo\s+bien)\b", re.I)
_NUM = re.compile(r"\d+(?:\.\d+)?")
_ACTION = re.compile(r"\b(envi\w+|alert\w+|notific\w+|cerr\w+|apag\w+|deten\w+|detien\w+|par[ae]r?|frena\w*|evacu\w+|bloque\w+|activ\w+|desactiv\w+|ejecut\w+|llam\w+|orden\w*|avis\w+|despej\w+|retir\w+|suspend\w+|dispar\w+)\b", re.I)
_DISTANCE = re.compile(r"\b(metros?|mts?|centimetros?|distanci\w+|cerca\w*|lej[oa]s?|junto\s+a|pegad\w+|proxim\w+|separad\w+|adyacent\w+|contiguo\w*)\b", re.I)
_ENTITY = re.compile(r"\b(personas?|vehiculos?|camion\w*|trabajador\w*|operador\w*|persona|humano\w*|peaton\w*|maquin\w+|equipo)\b", re.I)
_STATE_WORDS = {"GREEN": re.compile(r"\b(GREEN|verde)\b", re.I), "YELLOW": re.compile(r"\b(YELLOW|amarill\w+)\b", re.I), "RED": re.compile(r"\b(RED|roj[oa]s?)\b", re.I),
                "UNKNOWN": re.compile(r"\b(UNKNOWN|desconocid\w+)\b", re.I)}


def _allowed_numbers(incident: dict) -> set[str]:
    """Only numbers a faithful report may cite: version strings, the zone id, rule ids and formatted event/interval times. Digits inside hex ids and digests are NOT allowed."""
    r = incident["risk"]
    text = " ".join([r["engine_version"], r["policy_version"], r["policy_id"], r["zone_id"], *(e["rule_id"] for e in incident["events"])])
    nums = set(re.findall(r"\d+(?:\.\d+)?", text)) | set(re.findall(r"\d+", text))
    for e in incident["events"]:
        nums |= {_s(e["start_us"]), _s(e["end_us"])}
    for u in incident["unknown_intervals"]:
        nums |= {_s(u["start_us"]), _s(u["end_us"])}
    return nums


def validate_report(text: str, incident: dict, prompt: dict) -> dict:
    """-> validated report dict, or ReportRejected(codes). Never repairs."""
    try:
        raw = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        raise ReportRejected(["malformed_json"])
    try:
        rep = Report.model_validate(raw)
    except ValidationError as e:
        codes = sorted({"schema_violation:" + ".".join(str(p) for p in err["loc"][:1]) for err in e.errors()})
        raise ReportRejected(codes or ["schema_violation"])
    codes = []
    st = incident["risk"]["final_state"]
    if rep.incident_id != incident["incident_id"]:
        codes.append("incident_mismatch")
    if rep.deterministic_state != st:
        codes.append("state_changed")
    rules = {e["rule_id"] for e in incident["events"]} or {"ninguna"}
    if rep.regla_aplicada.rule_id not in rules:
        codes.append("rule_not_in_incident")
    valid_refs = {f"event:{e['event_id']}" for e in incident["events"]} | {f"interval:{u['n']}" for u in incident["unknown_intervals"]} | {"provenance"}
    if any(f.ref not in valid_refs for f in rep.hechos_observados):
        codes.append("fact_ref_not_in_incident")
    canon = canonical_facts(incident)
    if any(f.ref in canon and f.texto != canon[f.ref] for f in rep.hechos_observados) or ("provenance" in {f.ref for f in rep.hechos_observados} and (incident["events"] or incident["unknown_intervals"])):
        codes.append("fact_text_not_bound_to_incident")   # free-form facts are how invented detections get in
    if rep.provenance.prompt_digest != prompt["prompt_digest"] or rep.provenance.model_id != FAKE_MODEL_ID:
        codes.append("provenance_mismatch")
    if (rep.resumen != expected_resumen(incident) or rep.explicacion_decision != expected_decision(incident) or rep.regla_aplicada.explicacion != RULE_SENTENCE
            or list(rep.incertidumbres) != expected_uncertainties(incident) or list(rep.pasos_revision_humana) != STEPS):   # exact and incident-specific: a true-but-wrong-for-this-incident sentence (e.g. "no lifecycle record") is also rejected
        codes.append("narrative_not_template")   # closed templates bound to the incident's facts: free narrative is never accepted, so an unknown paraphrase of "I reviewed the video" cannot pass
    narrative = [rep.resumen, rep.explicacion_decision, rep.regla_aplicada.explicacion, *rep.incertidumbres, *rep.pasos_revision_humana]
    # exact template disclaimers are exempt from the dishonesty scans (they legitimately say "no hay distancias fisicas"); everything else, including facts, is scanned
    exempt = TEMPLATE_SENTENCES | {expected_resumen(incident), expected_decision(incident)}   # the origin label legitimately says 'analisis ... archivo de video registrado'
    free = [t for t in narrative if t not in exempt]
    texts = [*free, *(f.texto for f in rep.hechos_observados)]
    blob = "\n".join(texts)
    for other, rx in _STATE_WORDS.items():
        if other != st and any(rx.search(t) for t in free):
            codes.append("state_mentioned_mismatch")   # a RED incident narrated as "amarilla" is a changed severity even if the structured field is intact
    if any(_ENTITY.search(t) for t in free):
        codes.append("unsupported_entity_claim")        # the incident carries no per-object facts; naming people or vehicles would be an invented detection
    if _DISTANCE.search(blob):
        codes.append("physical_measure_claim")
    if _COLLISION.search(blob):
        codes.append("collision_claim")
    if _VIDEO.search(blob):
        codes.append("video_inspection_claim")
    if st in ("UNKNOWN", "YELLOW", "RED") and _OK_WORDS.search(blob):
        codes.append("risk_downplayed")
    if st == "UNKNOWN" and _OK_WORDS.search(blob):
        codes.append("unknown_to_green")
    allowed = _allowed_numbers(incident)
    if any(n not in allowed for n in _NUM.findall(blob)):
        codes.append("invented_number")
    if re.search(r"\b\d+\s*(m|km|metros|kilometros|km/h)\b", blob, re.I):
        codes.append("physical_measure_claim")
    if _ACTION.search("\n".join(texts)):
        codes.append("action_language")
    if codes:
        raise ReportRejected(sorted(set(codes)))
    return rep.model_dump(by_alias=True)


def generate(incident: dict, transport: FakeMuseTransport | None = None) -> dict:
    """`incident` is an `mg.incident/1` record. -> {status: ACCEPTED|REJECTED, report|codes, ...}. The incident is never mutated; the severity shown is the incident's own."""
    transport = transport or FakeMuseTransport()
    view = muse_view(incident)
    prompt = build_prompt(view)
    text = transport.complete(prompt)
    out = {"kind": "SIMULATED_EXPLANATION", "simulated": True, "real_muse_call": False, "model_id": FAKE_MODEL_ID, "incident_id": incident["incident_id"], "decided_by": "deterministic_policy",
           "severity": incident["risk"]["severity"], "prompt_digest": prompt["prompt_digest"], "response_digest": raw_digest(text),
           "validation": "structural and lexical only; it does not prove the text is semantically true"}
    try:
        out.update(status="ACCEPTED", report=validate_report(text, view, prompt))
    except ReportRejected as e:
        out.update(status="REJECTED", codes=e.codes)
    return out
