"""Governed clients for the two Meta model paths (META ACCEPTANCE RECOVERY 05). Status: PROPOSED, INERT: nothing here is imported by the application, nothing reads credentials, the
environment or files, and a client can only dispatch through an `metahttp.EndpointBoundGuard` the caller builds around a governor that is deny-by-default. No UI or route can enable a
provider. `ZERO_CHARGE_GUARANTEED` is never claimed: local limits do not bind the provider's billing.
Origin is decided ONLY by the transport object: a production attestation (minted by `ProductionHttpsTransport` after a CA-verified TLS exchange and then verified, consumed and bound to
the request, the response and the ledger) yields REAL_PROVIDER_RESPONSE_UNVERIFIED; everything else is SIMULATED/TEST. A response whose origin is ambiguous is rejected, not labelled.
Outcomes are returned as data (never raised for expected conditions) and carry only sanitized provider error views."""
from __future__ import annotations

import threading

from . import meta_obs, meta_pipeline, metahttp, sam31
from .costgov import Denied, TransportTimeout
from .metahttp import EndpointBoundGuard, TransportFault


def classify(exc: BaseException, inner=None) -> dict:
    """Sanitized outcome of a refused/failed call. `retry` follows the documented guidance: 429/500/503 may be retried by a LATER owner decision (the first call uses 0 retries);
    400/401/402/403/404/413 and 504 must not be retried unchanged."""
    if isinstance(exc, Denied):
        e = getattr(inner, "last_error", None)
        return {"outcome": "DENIED", "code": exc.code, "provider_error": e, "retry_after": getattr(inner, "last_retry_after", None),
                "spend_counted": exc.code in ("provider_quota_exceeded", "invalid_credentials", "provider_server_error", "unexpected_provider_response"),
                "retry": "documented_backoff_only_if_owner_allows" if exc.code in ("provider_quota_exceeded", "provider_server_error") else "no"}
    if isinstance(exc, TransportTimeout):
        return {"outcome": "TIMEOUT", "code": "timeout", "spend_counted": True, "retry": "no (the provider may have processed it; changing the request is required for 504)"}
    if isinstance(exc, TransportFault):
        return {"outcome": "TRANSPORT_FAULT", "code": str(exc)[:120], "spend_counted": True, "retry": "no"}
    raise exc


def resolve_origin(guarded: EndpointBoundGuard, resp: dict) -> tuple[object | None, dict | None]:
    """-> (attestation or None, rejection outcome or None). Production transport: an attestation is REQUIRED. Test transport: an attestation is FORBIDDEN. Anything else is ambiguous."""
    att = resp.get("attestation")
    prod = bool(getattr(guarded.inner, "is_production", False))
    origin = resp.get("origin")
    if prod and origin == "PRODUCTION" and att is not None:
        return att, None
    if not prod and origin == "TEST" and att is None:
        return None, None
    return None, {"outcome": "ORIGIN_REJECTED", "code": "ambiguous_or_inconsistent_origin", "spend_counted": True, "retry": "no", "evidence": None, "real_meta_call_completed": False}


class SamClient:
    def __init__(self, guarded: EndpointBoundGuard):
        if not isinstance(guarded, EndpointBoundGuard) or guarded.inner.provider != sam31.PROVIDER_ID:
            raise ValueError("an EndpointBoundGuard bound to the meta_sam31 provider is required")
        self.guarded = guarded

    def first_call(self, image_url: str, concept: str, request_key: str, *, width: int, height: int, zones: list[dict], cancel: threading.Event | None = None,
                   source_label: str = "") -> dict:
        """ONE attempt (no retry). Success returns the evidence record; every other outcome is data."""
        req = sam31.build_governed_request(image_url, concept, request_key)
        try:
            resp = self.guarded.send_with_retry(req, max_attempts=1, cancel=cancel)
        except (Denied, TransportTimeout, TransportFault) as e:
            return {**classify(e, self.guarded.inner), "evidence": None, "real_meta_call_completed": False}
        events = resp["body"].get("events")
        if not isinstance(events, list):
            return {"outcome": "UNEXPECTED_BODY", "code": "no_events", "spend_counted": True, "retry": "no", "evidence": None, "real_meta_call_completed": False}
        att, rej = resolve_origin(self.guarded, resp)
        if rej:
            return rej
        gov = self.guarded.governor
        try:
            ev = meta_pipeline.build_evidence(events, concept=concept, width=width, height=height, pts_us=[0], zones=zones, simulated=att is None, source_label=source_label,
                                              attestation=att, request=req, governor=gov)
        except meta_obs.NormalizationRefused as e:
            return {"outcome": "ATTESTATION_REJECTED", "code": e.code, "spend_counted": True, "retry": "no", "evidence": None, "real_meta_call_completed": False}
        return {"outcome": "OK" if ev["provenance"]["parse_states"][0].startswith("PARSED") else "PARSE_UNKNOWN", "code": None, "spend_counted": True, "retry": "no", "evidence": ev,
                "real_meta_call_completed": att is not None, "usage_reported": resp.get("usage")}
