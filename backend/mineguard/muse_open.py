"""Muse Spark Open through Baseten: request builder and REAL-MODEL validation profile (META LIVE READINESS 04). Pure functions: no network, no environment, no credential.
Contract (https://docs.baseten.co/development/model-apis/overview, read 2026-11-01): base URL https://inference.baseten.co/v1, POST /v1/chat/completions, `Authorization: Bearer`,
error JSON with HTTP 400/401/402/404/429/500/529, zero data retention for synchronous requests, and **structured outputs / JSON mode are NOT supported for Muse Spark Open**:
the JSON is requested in the prompt and validated here, never trusted. The Baseten SLUG of the model is NOT stated in the documentation read; the owner's communicated value
`meta-models/muse-spark-open-1026` is UNVERIFIED and is passed in by the caller, never hard-wired as verified.
The model text is a SUBORDINATE layer: it can never change severity, rules, the incident or its evidence. Validation is structural and lexical; it does not prove semantic truth."""
from __future__ import annotations

import json
import re

from . import explain, metahttp
from .rawjson import raw_digest

BASE_URL = "https://inference.baseten.co/v1"
CHAT_PATH = "/chat/completions"
PROVIDER_ID = "baseten_muse_spark_open"
OWNER_COMMUNICATED_SLUG = "meta-models/muse-spark-open-1026"      # UNVERIFIED (owner-supplied; the docs read do not list it)
KIND_REAL = "MODEL_EXPLANATION_UNVERIFIED"
MAX_SUMMARY, MAX_ITEM, MAX_ITEMS = 400, 200, 4
BODY_KEYS = frozenset({"model", "messages", "max_tokens"})        # no response_format (unsupported), no temperature/seed reliance, no n, no stream
SYSTEM = ("Eres un asistente que explica un incidente de seguridad minera YA decidido por reglas deterministas. No cambies la severidad ni el estado. No afirmes colision, distancias, "
          "que se reviso video o imagenes, ni nada que no este en los datos. No indiques acciones. Responde SOLO con un objeto JSON con las claves "
          '"resumen" (texto breve), "incertidumbres" (lista de textos) y "pasos_revision_humana" (lista de textos).')

CONTRACT = [  # (field, value, status, evidence)
    ("base URL", BASE_URL, "VERIFIED", "docs.baseten.co model-apis overview"),
    ("path", "/v1/chat/completions", "VERIFIED", "same"),
    ("auth", "Authorization: Bearer <key>", "VERIFIED", "same"),
    ("model slug", OWNER_COMMUNICATED_SLUG, "UNVERIFIED", "owner's provider email; not listed in the documentation read"),
    ("structured output / JSON mode", "NOT supported for Muse Spark Open", "VERIFIED", "same (supported for all other language models)"),
    ("n > 1", "returns HTTP 400", "VERIFIED", "same"),
    ("temperature 0 / seed", "do not guarantee reproducible output", "VERIFIED", "same"),
    ("stream parameter", "not stated", "UNKNOWN", "same"),
    ("max_tokens in Chat Completions", "not stated (required for Messages API)", "UNKNOWN", "same"),
    ("rate limits / pricing / credits for this model", "not stated", "UNKNOWN", "same"),
    ("error codes", "400, 401, 402, 404, 429, 500, 529 with a JSON body of unspecified schema", "VERIFIED", "same"),
    ("data retention", "synchronous requests are not stored (zero data retention); async inference has its own behaviour", "VERIFIED", "same"),
    ("weights licence", "Muse Model License + Acceptable Use Policy; weights on Hugging Face under `meta-models` (Muse-Spark-Open-816B-A42B), 1,048,576-token context, text output",
     "VERIFIED", "dev.meta.ai/docs/muse-spark-open"),
]


def user_content(view: dict) -> str:
    """The ONLY incident data that would be sent: the `muse_view` projection of the incident (no video, frames, detections of individuals, paths, tokens or keys)."""
    return json.dumps(view, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def build_body(view: dict, model: str, max_tokens: int = 500) -> dict:
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{2,99}", model) or ".." in model or "//" in model:
        raise ValueError("model_slug_invalid")
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or not 50 <= max_tokens <= 1000:
        raise ValueError("max_tokens_out_of_range")
    body = {"model": model, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user_content(view)}], "max_tokens": max_tokens}
    assert set(body) == BODY_KEYS
    return body


def build_governed_request(view: dict, model: str, request_key: str, max_tokens: int = 500) -> dict:
    if not isinstance(request_key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,64}", request_key):
        raise ValueError("request_key_invalid")
    body = build_body(view, model, max_tokens)
    est = (len(body["messages"][0]["content"]) + len(body["messages"][1]["content"])) // 2 + max_tokens      # deliberately high: characters/2 + the output cap
    return {"model": model, "request_key": request_key, "provider": PROVIDER_ID, "media": False, "units": 1, "est_tokens": est, "body": body}


class ModelTextRejected(Exception):
    def __init__(self, codes: list[str]):
        super().__init__(",".join(codes))
        self.codes = codes


def validate_model_text(text, view: dict) -> dict:
    """REAL-model profile. Unlike the closed templates of the simulated path, the model writes the narrative, so the guarantees are: a strict JSON shape and size, the deterministic
    state is never restated differently, no collision / physical measure / video-inspection / action / downplaying language, numbers only from the incident, and no named entities
    (the incident view carries no per-object facts). Anything else is REJECTED, never repaired. This is NOT a proof that the text is true."""
    if not isinstance(text, str) or not text.strip() or len(text) > 6000:
        raise ModelTextRejected(["empty_or_oversized"])
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)           # a code fence is formatting, not content; nothing else is stripped
    try:
        raw = json.loads(t)
    except ValueError:
        raise ModelTextRejected(["malformed_json"])
    if not isinstance(raw, dict) or set(raw) != {"resumen", "incertidumbres", "pasos_revision_humana"}:
        raise ModelTextRejected(["schema_violation"])
    res, inc, steps = raw["resumen"], raw["incertidumbres"], raw["pasos_revision_humana"]
    if not isinstance(res, str) or not res.strip() or len(res) > MAX_SUMMARY or not all(isinstance(x, list) and 0 < len(x) <= MAX_ITEMS and all(isinstance(i, str) and 0 < len(i) <= MAX_ITEM for i in x) for x in (inc, steps)):
        raise ModelTextRejected(["schema_violation"])
    texts = [res, *inc, *steps]
    blob = "\n".join(texts)
    st = view["risk"]["final_state"]
    codes = []
    for other, rx in explain._STATE_WORDS.items():
        if other != st and any(rx.search(x) for x in texts):
            codes.append("state_mentioned_mismatch")
    if any(explain._ENTITY.search(x) for x in texts):
        codes.append("unsupported_entity_claim")
    if explain._DISTANCE.search(blob) or re.search(r"\b\d+\s*(m|km|metros|kilometros|km/h)\b", blob, re.I):
        codes.append("physical_measure_claim")
    if explain._COLLISION.search(blob):
        codes.append("collision_claim")
    if explain._VIDEO.search(blob):
        codes.append("video_inspection_claim")
    if st in ("UNKNOWN", "YELLOW", "RED") and explain._OK_WORDS.search(blob):
        codes.append("risk_downplayed")
    if any(n not in explain._allowed_numbers(view) for n in explain._NUM.findall(blob)):
        codes.append("invented_number")
    if explain._ACTION.search(blob):
        codes.append("action_language")
    if codes:
        raise ModelTextRejected(sorted(set(codes)))
    return {"resumen": res.strip(), "incertidumbres": [x.strip() for x in inc], "pasos_revision_humana": [x.strip() for x in steps]}


def extract_text(resp_body: dict) -> str:
    """choices[0].message.content of a Chat Completions response; anything else is an empty text (-> rejected)."""
    try:
        c = resp_body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    return c if isinstance(c, str) else ""


def compose(incident: dict, resp_body: dict, *, model: str, max_tokens: int = 500, redemption=None) -> dict:
    """Final record: the severity is the INCIDENT's; the model text is shown beside the deterministic explanation, never instead of it. The facts view and the request digest are DERIVED
    here from the incident, the model and `max_tokens` (the caller supplies neither). The record is real-labelled ONLY with a `metahttp.Redemption` (a production attestation accepted once
    for exactly this request body and this response): the redemption must carry this provider and model and the digest of THIS incident's request, and is consumed here, so another
    incident, view, model or payload, a second record, a forged object or a dict is refused (ValueError). No redemption means SIMULATED/TEST."""
    view = explain.muse_view(incident)
    request_digest = raw_digest(build_body(view, model, max_tokens))
    real = redemption is not None
    if real and (not isinstance(redemption, metahttp.Redemption) or redemption.provider != PROVIDER_ID or redemption.model != model):
        raise ValueError("redemption_refused:" + ("not_a_redemption" if not isinstance(redemption, metahttp.Redemption) else "wrong_provider_or_model"))
    out = {"kind": KIND_REAL if real else "SIMULATED_EXPLANATION", "provider": PROVIDER_ID, "model": model, "model_slug_status": "UNVERIFIED" if model == OWNER_COMMUNICATED_SLUG else "caller_supplied",
           "real_muse_call": real, "decided_by": "deterministic_policy", "severity": incident["risk"]["severity"], "incident_id": incident["incident_id"],
           "request_digest": request_digest, "response_digest": raw_digest(resp_body), "attestation": redemption.public if real else None,
           "validation": "structural and lexical only; it does not prove the text is semantically true",
           "separate_from": ["deterministic explanation", "simulated explanation (SIMULATED_EXPLANATION)", "Muse Spark via Meta Model API"]}
    try:
        out.update(status="ACCEPTED", report=validate_model_text(extract_text(resp_body), view))
    except ModelTextRejected as e:
        out.update(status="REJECTED", codes=e.codes)
    if real:
        # the single use is the LAST step: any refusal above (wrong provider/model, bad max_tokens, ...) leaves the redemption intact; the digests are re-checked HERE against this request and this response
        bad = metahttp.use_redemption(redemption, response_digest=raw_digest(resp_body), request_digest=request_digest)
        if bad:
            raise ValueError("redemption_refused:" + ",".join(bad))
    return out


class MuseOpenClient:
    def __init__(self, guarded):
        if not isinstance(guarded, metahttp.EndpointBoundGuard) or guarded.inner.provider != PROVIDER_ID:
            raise ValueError("an EndpointBoundGuard bound to the baseten_muse_spark_open provider is required")
        self.guarded = guarded

    def explain(self, incident: dict, model: str, request_key: str, *, max_tokens: int = 500, cancel=None) -> dict:
        from . import metaclient
        from .costgov import Denied, TransportTimeout
        from .metahttp import TransportFault
        view = explain.muse_view(incident)
        req = build_governed_request(view, model, request_key, max_tokens)
        try:
            resp = self.guarded.send_with_retry(req, max_attempts=1, cancel=cancel)          # zero retries for the first live use
        except (Denied, TransportTimeout, TransportFault) as e:
            return {**metaclient.classify(e, self.guarded.inner), "explanation": None}
        att, rej = metaclient.resolve_origin(self.guarded, resp)
        if rej:
            return {**rej, "explanation": None}
        red = None
        if att is not None:
            red, problems = metahttp.redeem(att, request=req, payload=resp["body"], governor=self.guarded.governor)
            if problems:
                return {"outcome": "ATTESTATION_REJECTED", "code": ",".join(problems), "spend_counted": True, "retry": "no", "explanation": None}
        try:
            explanation = compose(incident, resp["body"], model=model, max_tokens=max_tokens, redemption=red)
        except ValueError as e:                                                                # the redemption does not fit this incident/model/response: no record at all
            return {"outcome": "ATTESTATION_REJECTED", "code": str(e)[:160], "spend_counted": True, "retry": "no", "explanation": None}
        return {"outcome": "OK", "spend_counted": True, "explanation": explanation}
