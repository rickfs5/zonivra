"""THE dispatch layer for provider HTTP requests (META ACCEPTANCE RECOVERY 05). Status: PROPOSED, INERT in this build.
Trust boundary (what can and cannot be certified):
- `TestHttpsTransport` may talk to a loopback test server or an injected fake connection. It can NEVER produce an `Attestation`; everything it returns is test/simulated.
- `ProductionHttpsTransport` has NO injectable connection, NO loopback, NO configurable host/path: each provider maps to one hard-coded https endpoint. Only after a complete
  dispatch -> response -> TLS-peer-verified sequence does it mint an `Attestation`. The attestation proves exactly this and no more: "in THIS process, a request with THIS digest,
  authorized for THIS provider/model, was dispatched on a TLS connection whose certificate chain verified against the system CAs for THIS hostname, and a response whose PARSED value
  (the JSON document, or the list of decoded SSE events) has THIS digest came back". The digest binds the parsed value, not the raw bytes: different byte sequences that decode to the
  same value (duplicate keys, escapes, whitespace) have the same digest, and the raw bytes are not kept.
  It does NOT prove that the provider's model generated them (the providers offer no response signature), that the call was billed, or anything after the process ends.
  The HMAC key lives in this process only: an attestation cannot be forged by data alone, and cannot survive a restart. The production dispatch is one closure that captured its
  primitives at import time (no overridable hook methods, no module attribute it consults at call time), so patching `_open`/`_peer`/`_tls_evidence`/`_mint` later does not change it.
  Accepted limitation: code in this same process that calls the private mint, replaces `ProductionHttpsTransport.send` wholesale, or rewrites interpreter/standard-library internals
  can still forge an attestation (this guards against accidents, mocks and data, not against the code's own author).
- An attestation certifies ONE evidence/explanation record: it is redeemed once (`redeem`), bound to the request digest and the response digest, and the resulting `Redemption` can be
  used once for exactly that response (`use_redemption`).
- Authorization is bound to destination: a binding is the exact triple (provider, model, request-body digest) plus a maximum number of uses; `EndpointBoundGuard` refuses anything else
  BEFORE the governor reserves budget, and the transport re-checks it (defence in depth). Redirects are never followed; a 3xx is a refused response.
- Credentials arrive only through the guard's injected callable, are validated against RFC 6750 token68 syntax, and are never logged, returned or kept; a response containing the credential is refused.
Nothing in the application imports this module (enforced by test); the app's `netguard` still refuses non-loopback connections."""
from __future__ import annotations

import hashlib
import hmac
import http.client
import ipaddress
import json
import re
import secrets
import ssl
import threading
import time
import types
from urllib.parse import urlsplit

from . import projection
from .costgov import Denied, GuardedTransport, TransportTimeout, key_digest
from .rawjson import raw_digest

MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_ERROR_CHARS = 200
MAX_ATTESTATION_AGE_S = 300
_LOOPBACK = ("127.0.0.1", "::1", "localhost")
# exact provider -> (host, base path, request path, streaming). Port must be absent or 443. The table is an IMMUTABLE tuple: nothing mutable is consulted for trust. `OFFICIAL_ENDPOINTS`
# is only a read-only VIEW for documentation and tests; the production constructor, the dispatch closure and the attestation check all use copies CAPTURED at import time, so neither an
# in-place change nor rebinding a module attribute later alters which host a production transport dials or which endpoint an attestation must carry.
_OFFICIAL_TABLE = (("meta_sam31", "api.meta.ai", "/v1", "/responses", True), ("baseten_muse_spark_open", "inference.baseten.co", "/v1", "/chat/completions", False))
OFFICIAL_ENDPOINTS = types.MappingProxyType({row[0]: (row[1], row[2], row[3]) for row in _OFFICIAL_TABLE})
STAGES = ("authorized", "dispatched", "tls_verified", "response_complete")
_HTTPS_CONN, _CREATE_CTX = http.client.HTTPSConnection, ssl.create_default_context      # identities captured at import: a replaced primitive is detected at dispatch


class TransportFault(Exception):
    """Connection, TLS or protocol failure. The provider may have processed the request, so the governor counts it as spend."""


# ---- credential format ------------------------------------------------------------------------------------------------------------------------------------------------
CRED_MIN, CRED_MAX = 8, 512
_TOKEN68 = re.compile(r"[A-Za-z0-9\-._~+/]+=*")


def credential_problems(c) -> list[str]:
    """Problem CODES only (never the value). The accepted syntax is the RFC 6750 `b64token` (token68) used by `Authorization: Bearer`, which both providers document; no provider prefix
    is assumed. Control characters, CR/LF, whitespace, non-ASCII and header delimiters are rejected, never stripped or repaired."""
    if not isinstance(c, str):
        return ["not_a_string"]
    if not c:
        return ["empty"]
    p = []
    if re.search(r"[\r\n]", c):
        p.append("line_break")
    if re.search(r"[\x00-\x1f\x7f]", c):
        p.append("control_character")
    if re.search(r"\s", c):
        p.append("whitespace")
    if not c.isascii():
        p.append("non_ascii")
    if len(c) < CRED_MIN:
        p.append("too_short")
    if len(c) > CRED_MAX:
        p.append("too_long")
    if not p and not _TOKEN68.fullmatch(c):
        p.append("not_token68")
    return p


def _redact(text: str, secret: str) -> str:
    t = str(text)
    if secret:
        t = t.replace(secret, "[redacted]")
    t = re.sub(r"(?i)bearer\s+\S+", "Bearer [redacted]", t)
    t = re.sub(r"LLM_[0-9]+_[A-Za-z0-9_-]+", "[redacted-key]", t)
    return t[:MAX_ERROR_CHARS]


# ---- attestation (stages are independent facts; only the production path can mint) ----------------------------------------------------------------------------------------
_KEY = secrets.token_bytes(32)
_LOCK = threading.Lock()
_CONSUMED: set[str] = set()       # attempt ids whose single use was spent by `use_redemption`
_TLS = threading.local()          # the binding claim of the current guarded call (same thread: GuardedTransport calls the inner transport synchronously)


class Attestation:
    __slots__ = ("attempt_id", "provider", "model", "endpoint", "request_digest", "response_digest", "request_key", "reservation_slot", "stages", "tls", "issued_ns", "mac", "_sealed")

    def __init__(self, **kw):
        for k in self.__slots__[:-1]:
            object.__setattr__(self, k, kw[k])
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, k, v):
        raise AttributeError("attestation is immutable")

    def public(self) -> dict:
        d = {k: getattr(self, k) for k in self.__slots__[:-2]}
        d["stages"] = [list(s) for s in self.stages]
        d["endpoint"] = list(self.endpoint)
        d["level"] = "PRODUCTION_TLS_PEER_VERIFIED_NO_PROVIDER_SIGNATURE"
        d["proves"] = "this process dispatched this authorized request over a CA-verified TLS session to the provider hostname and received a response whose parsed value has the recorded digest (the digest binds the parsed JSON document or decoded event list, not the raw bytes)"
        d["does_not_prove"] = ["that the provider's model generated the bytes (no response signature exists)", "billing", "anything after this process ends"]
        return d


def _mac(fields: dict) -> str:
    body = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=list)
    return hmac.new(_KEY, body.encode(), hashlib.sha256).hexdigest()


def _fields(a: Attestation) -> dict:
    return {k: getattr(a, k) for k in Attestation.__slots__[:-2]}


def _mint(*, provider, model, endpoint, request_digest, response_digest, request_key, reservation_slot, stages, tls) -> Attestation:
    f = dict(attempt_id=secrets.token_hex(16), provider=provider, model=model, endpoint=tuple(endpoint), request_digest=request_digest, response_digest=response_digest, request_key=request_key,
             reservation_slot=reservation_slot, stages=tuple(tuple(s) for s in stages), tls=dict(tls), issued_ns=time.monotonic_ns())
    return Attestation(**f, mac=_mac(f))


def _make_endpoint_ok(table=_OFFICIAL_TABLE):
    captured = {r[0]: (r[1], r[2], r[3]) for r in table}             # private copy: not reachable from the module namespace
    return lambda provider, endpoint: captured.get(provider) == tuple(endpoint)


_endpoint_ok = _make_endpoint_ok()


def attestation_problems(att, *, request: dict, payload, governor, now_ns: int | None = None) -> list[str]:
    """Every check is independent and returns a CODE. No check consumes the attestation. `request` is the governed request that was sent, `payload` the response body the caller holds."""
    if not isinstance(att, Attestation):
        return ["not_an_attestation"]
    p = []
    f = _fields(att)
    if not hmac.compare_digest(att.mac, _mac(f)):
        return ["mac_invalid"]                                      # forged, edited, or minted by another process (e.g. before a restart)
    now = time.monotonic_ns() if now_ns is None else now_ns
    if not 0 <= now - att.issued_ns <= MAX_ATTESTATION_AGE_S * 1_000_000_000:
        p.append("stale_or_future")
    names = [s[0] for s in att.stages]
    seqs = [s[1] for s in att.stages]
    ts = [s[2] for s in att.stages]
    if tuple(names) != STAGES or seqs != sorted(set(seqs)) or ts != sorted(ts):
        p.append("stages_incomplete_reordered_or_inconsistent")
    if not (isinstance(att.tls, dict) and att.tls.get("peer_verified") is True and att.tls.get("hostname") == att.endpoint[0]):
        p.append("tls_not_verified")
    if not _endpoint_ok(att.provider, tuple(att.endpoint)):
        p.append("endpoint_not_the_provider_endpoint")
    if not isinstance(request, dict) or att.provider != request.get("provider") or att.model != request.get("model") or att.request_key != request.get("request_key"):
        p.append("request_identity_mismatch")
    else:
        d = raw_digest(request.get("body"))
        if att.request_digest != d:
            p.append("request_digest_mismatch")
        if d not in governor.config.authorized_payload_digests:
            p.append("request_not_authorized")
    if att.response_digest != raw_digest(payload):
        p.append("response_digest_mismatch")
    p += _ledger_problems(att, governor)
    with _LOCK:
        if att.attempt_id in _CONSUMED:
            p.append("already_consumed")
    return p


def _ledger_problems(att, governor) -> list[str]:
    from .costgov import Ledger
    path = governor.config.ledger_path
    if not path:
        return ["no_ledger"]
    try:
        recs = Ledger(path).replay()
    except Exception:
        return ["ledger_unreadable_or_tampered"]
    kd = key_digest(str(att.request_key))
    if not any(r.get("event") == "reserve" and r.get("slot") == att.reservation_slot and r.get("key_digest") == kd and r.get("provider") == att.provider and r.get("model") == att.model for r in recs):
        return ["no_matching_reservation_in_ledger"]
    return []


class Redemption:
    """The proof that ONE attestation was accepted for ONE (request, response) pair. Sealed like an attestation; usable once; carries no payload, only the digests it is bound to."""
    __slots__ = ("attempt_id", "provider", "model", "request_digest", "response_digest", "view", "issued_ns", "mac", "_sealed")

    def __init__(self, **kw):
        for k in self.__slots__[:-1]:
            object.__setattr__(self, k, kw[k])
        object.__setattr__(self, "_sealed", True)

    def __setattr__(self, k, v):
        raise AttributeError("redemption is immutable")

    @property
    def public(self) -> dict:
        return json.loads(json.dumps(self.view))


def _rfields(r: Redemption) -> dict:
    return {"attempt_id": r.attempt_id, "provider": r.provider, "model": r.model, "request_digest": r.request_digest, "response_digest": r.response_digest, "issued_ns": r.issued_ns,
            "view_digest": raw_digest(r.view)}


def _seal_redemption(f: dict) -> "Redemption":
    return Redemption(**f, mac=hmac.new(_KEY, json.dumps(_rfields_of(f), sort_keys=True).encode(), hashlib.sha256).hexdigest())


def redeem(att, *, request: dict, payload, governor):
    """-> (Redemption | None, problems). The ONLY way to obtain the authority to label a record real. It VERIFIES the attestation against the request, the response, the ledger and its own
    stage order but does NOT consume anything: the single use happens in `use_redemption`, as the LAST step of building a record, so a refusal or a failure while building (a wrong concept,
    bad dimensions, a rejected input) never destroys the only authority and the corrected call can still be made."""
    p = attestation_problems(att, request=request, payload=payload, governor=governor)
    if p:
        return None, p
    f = dict(attempt_id=att.attempt_id, provider=att.provider, model=att.model, request_digest=att.request_digest, response_digest=att.response_digest, view=att.public(), issued_ns=time.monotonic_ns())
    return _seal_redemption(f), []


def _rfields_of(f: dict) -> dict:
    return {"attempt_id": f["attempt_id"], "provider": f["provider"], "model": f["model"], "request_digest": f["request_digest"], "response_digest": f["response_digest"], "issued_ns": f["issued_ns"],
            "view_digest": raw_digest(f["view"])}


def use_redemption(red, *, response_digest: str, request_digest: str | None = None) -> list[str]:
    """-> problems. CONSUMES the attestation behind the redemption: ONE record per attestation, and only for the response (and request) it was bound to. Callers invoke it as the LAST step,
    after every validation and after the record is fully built. A second use, another payload, a forged or edited redemption or a plain dict is refused."""
    if not isinstance(red, Redemption):
        return ["not_a_redemption"]
    if not hmac.compare_digest(red.mac, hmac.new(_KEY, json.dumps(_rfields(red), sort_keys=True).encode(), hashlib.sha256).hexdigest()):
        return ["redemption_mac_invalid"]
    if red.response_digest != response_digest:
        return ["response_not_the_redeemed_one"]
    if request_digest is not None and red.request_digest != request_digest:
        return ["request_not_the_redeemed_one"]
    with _LOCK:
        if red.attempt_id in _CONSUMED:
            return ["redemption_already_used"]
        _CONSUMED.add(red.attempt_id)
    return []


# ---- transports ---------------------------------------------------------------------------------------------------------------------------------------------------------
def _is_loopback_ip(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _tls_evidence(conn, host: str) -> dict | None:
    """Evidence that the bytes arrived over a TLS session verified against the system CAs for the exact hostname. No TLS socket, a weakened context (no certificate requirement or no
    hostname check) or a different server name -> None. A mock connection has no `ssl.SSLSocket`, so it can never produce this. Call it right after the request: a closing connection
    drops its socket."""
    sock = getattr(conn, "sock", None)
    if not isinstance(sock, ssl.SSLSocket):
        return None
    ctx = sock.context
    if ctx.verify_mode != ssl.CERT_REQUIRED or not ctx.check_hostname or sock.server_hostname != host:
        return None
    der = sock.getpeercert(binary_form=True)
    if not der or not sock.getpeercert():
        return None
    return {"peer_verified": True, "hostname": host, "cert_sha256": hashlib.sha256(der).hexdigest(), "protocol": sock.version()}


class _Claim:
    """One authorized use of a binding, taken atomically BEFORE the governor reserves budget and converted to a use only when the request is handed to the connection."""
    __slots__ = ("core", "trip", "state")

    def __init__(self, core, trip):
        self.core, self.trip, self.state = core, trip, "pending"


def _precheck_impl(core, request) -> tuple:
    if not isinstance(request, dict) or not isinstance(request.get("body"), dict):
        raise Denied("not_governed")
    if request.get("provider") != core.provider:
        raise Denied("provider_endpoint_mismatch")                       # the budget of one provider can never pay for another provider's endpoint
    trip = (core.provider, str(request.get("model")), raw_digest(request["body"]))
    if trip not in core.authorized or request["body"].get("model") != request.get("model"):
        raise Denied("binding_not_authorized")
    return trip


def _claim_impl(core, request) -> _Claim:
    trip = _precheck_impl(core, request)
    with core._blk:
        if core._uses.get(trip, 0) + core._pending.get(trip, 0) >= core.max_uses:
            raise Denied("binding_exhausted")
        core._pending[trip] = core._pending.get(trip, 0) + 1
    return _Claim(core, trip)


def _release_impl(claim: _Claim) -> None:
    core = claim.core
    with core._blk:
        if claim.state == "pending":
            claim.state = "released"
            core._pending[claim.trip] = max(core._pending.get(claim.trip, 0) - 1, 0)


def _use_impl(core, trip) -> None:
    """Called inside `send`, right before the request is handed to the connection: a call that did not go through the guard has no pending claim and is refused."""
    claim = getattr(_TLS, "claim", None)
    with core._blk:
        if not isinstance(claim, _Claim) or claim.core is not core or claim.trip != trip or claim.state != "pending":
            raise Denied("not_governed")
        claim.state = "used"
        core._pending[trip] = max(core._pending.get(trip, 0) - 1, 0)
        core._uses[trip] = core._uses.get(trip, 0) + 1


def _require_claim(core, trip) -> None:
    claim = getattr(_TLS, "claim", None)
    with core._blk:
        if not isinstance(claim, _Claim) or claim.core is not core or claim.trip != trip or claim.state != "pending":
            raise Denied("not_governed")


def _gate_impl(core, request: dict, credentials: str) -> tuple:
    g, c = core._governor, core._governor.config
    trip = _precheck_impl(core, request)
    body = request["body"]
    if g.kill_engaged() or not c.live_provider_enabled or raw_digest(body) not in c.authorized_payload_digests or credential_problems(credentials):
        raise Denied("not_governed")
    kd = key_digest(str(request.get("request_key", "")))
    with g._lock:
        slot = next((s_ for s_, r in g._res.items() if r.key == kd and r.state == "reserved" and r.model_id == request.get("model") and r.provider == core.provider), None)
    if slot is None:
        raise Denied("not_governed")
    _require_claim(core, trip)                       # no pending claim from the guard -> refused BEFORE any connection exists
    return slot, trip


def _parse_sse(text: str):
    """Server-sent events -> list of decoded JSON `data:` payloads. `[DONE]` is ignored. Any undecodable data line -> None (fail closed)."""
    events = []
    for block in re.split(r"\r?\n\r?\n", text):
        data = [line[5:].lstrip(" ") for line in block.splitlines() if line.startswith("data:")]
        if not data:
            continue
        s = "\n".join(data).strip()
        if not s or s == "[DONE]":
            continue
        try:
            ev = json.loads(s)
        except ValueError:
            return None
        if not isinstance(ev, dict):
            return None
        events.append(ev)
    return events


class _Fns:
    """The helper functions one dispatch uses. The production closure captures its own instance at import time, so patching a module-level helper later cannot alter it."""
    __slots__ = ("gate", "use", "parse_sse", "error_view", "redact", "secret_in", "project")

    def __init__(self, gate, use, parse_sse, error_view, redact, secret_in, project):
        self.gate, self.use, self.parse_sse, self.error_view, self.redact, self.secret_in, self.project = gate, use, parse_sse, error_view, redact, secret_in, project


_secret_in = projection.leaks      # kept as the module-level name used by tests and mutants; the scanner lives in `projection` (second layer: the STRUCTURE returned is the guarantee)


def _default_fns() -> "_Fns":
    return _Fns(_gate_impl, _use_impl, _parse_sse, _error_view, _redact, projection.leaks, projection.project)


def _dispatch(core, request: dict, credentials: str, timeout_s: int, open_fn, peer_fn, finish_fn, fns: "_Fns") -> dict:
    slot, trip = fns.gate(core, request, credentials)
    stages = [("authorized", 0, time.monotonic_ns())]
    body = request["body"]
    payload_bytes = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    if json.loads(payload_bytes) != body:
        raise TransportFault("body_not_round_trippable")
    headers = {"Authorization": "Bearer " + credentials, "Content-Type": "application/json", "Accept": "text/event-stream" if core.accept_sse else "application/json"}
    core.last_error, core.last_retry_after = None, None
    deadline = time.monotonic() + max(int(timeout_s), 1)
    conn = open_fn(core, max(int(timeout_s), 1))
    try:
        try:
            if hasattr(conn, "set_debuglevel"):
                conn.set_debuglevel(0)                                         # stdlib debug output would print the Authorization header
            if getattr(conn, "debuglevel", 0) != 0:
                raise TransportFault("connection_debug_logging_enabled")
            fns.use(core, trip)                                               # a use is consumed when the request is actually handed to the connection
            stages.append(("dispatched", 1, time.monotonic_ns()))
            conn.request("POST", core.base_path + core.path, body=payload_bytes, headers=headers)
            tls = peer_fn(core, conn)                                         # the session exists after the handshake; read it BEFORE the response
            if tls is not None:
                stages.append(("tls_verified", 2, time.monotonic_ns()))
            resp = conn.getresponse()
            status, raw = resp.status, b""
            while len(raw) <= MAX_RESPONSE_BYTES:
                if time.monotonic() > deadline:
                    raise TimeoutError("deadline")
                chunk = resp.read(65536)
                if not chunk:
                    break
                raw += chunk
            else:
                raise TransportFault("response_too_large")
            core.last_retry_after = _retry_after_seconds(resp.getheader("Retry-After"), credentials)
            stages.append(("response_complete", 3, time.monotonic_ns()))
        except (TimeoutError, OSError) as e:
            if isinstance(e, TimeoutError) or "timed out" in str(e).lower():
                raise TransportTimeout("provider request timed out") from None
            raise TransportFault(fns.redact(f"{type(e).__name__}: {e}", credentials)) from None
        except http.client.HTTPException as e:
            raise TransportFault(fns.redact(f"{type(e).__name__}", credentials)) from None
        except (Denied, TransportFault, TransportTimeout):
            raise
        except Exception as e:                                                  # unexpected: only the TYPE name leaves, never the message (it could echo a secret)
            raise TransportFault(f"unexpected_{type(e).__name__}"[:80]) from None
    finally:
        try:
            conn.close()
        except Exception:
            pass
    text = raw.decode("utf-8", "replace")
    if credentials and credentials in text:
        raise TransportFault("response_echoed_the_credential")      # refused outright: nothing derived from it may be returned, stored or parsed
    if 300 <= status < 400:
        core.last_error = {"status": status, "type": None, "code": "redirect_refused", "message": None}   # redirects are never followed and their Location is never read
        return {"status": status, "body": {"error": core.last_error}, "usage": None}
    if status != 200:
        try:
            err_doc = json.loads(text)
        except ValueError:
            err_doc = None
        if err_doc is not None and fns.secret_in(err_doc, credentials):
            raise TransportFault("response_echoed_the_credential")              # also when it was written with JSON escapes
        core.last_error = fns.error_view(status, text, credentials)
        return {"status": status, "body": {"error": core.last_error}, "usage": None}
    # OUTPUT BOUNDARY: what is returned is a NEW structure built from contract-validated values (`projection`); the provider's document, events, unknown fields and mask payloads are never
    # handed to a caller. The leak scan runs on what was received AND on what is about to be returned.
    if core.accept_sse:
        events = fns.parse_sse(text)
        if events is None:
            raise TransportFault("sse_unparseable")
        if fns.secret_in(events, credentials):
            raise TransportFault("response_echoed_the_credential")
        body = fns.project(core.provider, events)
    else:
        try:
            doc = json.loads(text)
        except ValueError:
            raise TransportFault("json_unparseable") from None
        if not isinstance(doc, dict):
            raise TransportFault("json_not_an_object")
        if fns.secret_in(doc, credentials):
            raise TransportFault("response_echoed_the_credential")
        body = fns.project(core.provider, doc)
    if body is None:
        raise TransportFault("response_outside_contract")                       # not in the documented grammar/shape: nothing from it is returned
    if fns.secret_in(body, credentials):
        raise TransportFault("response_echoed_the_credential")                  # fragments that became adjacent once the dropped fields were removed
    if core.accept_sse:
        usage = next((e["response"]["usage"] for e in reversed(body) if e.get("type") == "response.completed" and "response" in e), None)
        out = {"status": 200, "body": {"events": body}, "usage": usage}
    else:
        out = {"status": 200, "body": body, "usage": body.get("usage")}
    out.update(finish_fn(core, request, status, out["body"], stages, slot, tls))
    return out


_RETRY_AFTER = re.compile(r"[0-9]{1,6}")
_IDENT = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def _retry_after_seconds(raw, credentials: str):
    """`Retry-After` is CONTROL DATA, never text for the user: only 1-6 ASCII digits (delta-seconds) are accepted and returned as an int. HTTP-dates, anything with a credential, CR/LF,
    control characters, spaces or long values are dropped (None)."""
    if not isinstance(raw, str) or (credentials and credentials in raw) or not _RETRY_AFTER.fullmatch(raw):
        return None
    return int(raw)


def _safe_text(v, credentials: str, redact) -> str | None:
    if v is None:
        return None
    t = redact(str(v), credentials)
    return re.sub(r"[\x00-\x1f\x7f]", " ", t)[:MAX_ERROR_CHARS]


def _error_view(status: int, text: str, credentials: str, _redact=_redact) -> dict:
    """Allow-list: `type` and `code` only if they are one of the documented values (closed sets); `message` (the one free-text channel, scanned) is redacted, stripped of control characters and
    truncated to 200 characters; nothing else from the response is exposed."""
    view = {"status": status, "type": None, "code": None, "message": None}
    try:
        e = json.loads(text).get("error")
        if isinstance(e, dict):
            for k, allowed in (("type", projection.ERROR_TYPES), ("code", projection.ERROR_CODES)):
                v = e.get(k)
                view[k] = v if v in allowed else None                           # closed documented sets only: no provider string is passed through
            view["message"] = _safe_text(e.get("message"), credentials, _redact)
    except (ValueError, AttributeError):
        pass
    return view


class _Core:
    """Shared, immutable-after-construction state. Not instantiable on its own and with NO dispatch hooks of its own."""
    _MUTABLE = frozenset({"outbound_attempts", "last_error", "last_retry_after", "_uses", "_pending"})
    provider: str

    def __setattr__(self, k, v):
        if getattr(self, "_frozen", False) and k not in self._MUTABLE:
            raise AttributeError(f"{k} is fixed after construction")
        object.__setattr__(self, k, v)

    def _init_common(self, governor, authorized, max_uses, accept_sse):
        if governor is None or not all(hasattr(governor, a) for a in ("config", "kill_engaged", "_res", "_lock")):
            raise ValueError("governor_required")
        triples = frozenset(tuple(t) for t in authorized)
        if not triples or any(len(t) != 3 or not all(isinstance(x, str) and x for x in t) for t in triples) or not isinstance(max_uses, int) or isinstance(max_uses, bool) or max_uses < 1:
            raise ValueError("authorization_bindings_required")
        self._governor, self.authorized, self.max_uses, self.accept_sse = governor, triples, max_uses, bool(accept_sse)
        self.outbound_attempts, self.last_error, self.last_retry_after, self._uses, self._pending = 0, None, None, {}, {}
        self._blk = threading.Lock()

    def precheck(self, request: dict) -> None:
        _precheck_impl(self, request)


class TestHttpsTransport(_Core):
    """Loopback/fake transport for tests. It can never return an attestation: whatever it returns is TEST evidence."""
    __test__ = False                       # not a pytest class
    is_production = False

    def __init__(self, base_url: str, *, path: str, provider: str, governor, authorized, accept_sse: bool = False, max_uses: int = 1, allow_loopback_http: bool = False, connection_factory=None):
        u = urlsplit(base_url)
        host = (u.hostname or "").lower()
        loop = host in _LOOPBACK or (host and _is_loopback_ip(host))
        if u.scheme == "http" and not (allow_loopback_http and loop):
            raise ValueError("https_required")
        if u.scheme not in ("http", "https") or not host or u.username or u.password or u.query or u.fragment:
            raise ValueError("base_url_invalid")
        if not re.fullmatch(r"/[A-Za-z0-9_./-]{1,100}", path or "") or ".." in path or "//" in path:
            raise ValueError("path_invalid")
        if not (loop or connection_factory is not None):
            raise ValueError("test_transport_requires_loopback_or_an_injected_fake_connection")     # a test transport can NEVER open a real connection to a non-loopback host
        if provider not in projection.PROVIDER_SSE or bool(accept_sse) != projection.PROVIDER_SSE[provider]:
            raise ValueError("provider_unknown_or_mode_mismatch")       # every provider has ONE response contract (streaming SAM / JSON chat completion) and its projector
        self.provider = provider
        self.scheme, self.host, self.port, self.base_path, self.path = u.scheme, host, u.port, u.path.rstrip("/"), path
        self._factory = connection_factory
        self._init_common(governor, authorized, max_uses, accept_sse)
        self._frozen = True

    def _open_conn(self, timeout_s: int):
        if self._factory:
            return self._factory(self.host, self.port, timeout_s)
        if self.host not in _LOOPBACK and not _is_loopback_ip(self.host):
            raise TransportFault("test_transport_refuses_a_non_loopback_host")                       # defence in depth (the constructor already refuses it)
        return (http.client.HTTPSConnection if self.scheme == "https" else http.client.HTTPConnection)(self.host, self.port, timeout=timeout_s)

    def send(self, request: dict, credentials: str, timeout_s: int = 30) -> dict:
        return _dispatch(self, request, credentials, timeout_s, lambda c, t: c._open_conn(t), lambda c, conn: None, lambda c, rq, st, pl, sg, sl, tl: {"origin": "TEST"}, _default_fns())   # never an attestation


def _make_production_send(*, https_conn=None, create_ctx=None):
    """Builds the ONLY dispatch that can mint an attestation. Everything it needs is captured HERE, at import time: patching a module attribute, a class method or `_tls_evidence`
    afterwards does not change what this closure calls. A patched connection class or `create_default_context` is detected at call time.
    The keyword arguments are a test seam (white-box, module-private): a closure built with a fake connection class skips the identity check, but it still needs a real verified
    `ssl.SSLSocket` to mint, which a fake cannot provide; the production closure is built at import with the real primitives and no arguments."""
    strict = https_conn is None and create_ctx is None
    https_conn, create_ctx = https_conn or _HTTPS_CONN, create_ctx or _CREATE_CTX
    dispatch, tls_ev, mint, fns = _dispatch, _tls_evidence, _mint, _default_fns()
    endpoint_ok = _make_endpoint_ok()
    http_mod, ssl_mod = http.client, ssl

    def open_fn(core, timeout_s):
        if strict and (http_mod.HTTPSConnection is not https_conn or ssl_mod.create_default_context is not create_ctx):
            raise TransportFault("transport_primitives_replaced")           # a patched connection class can never obtain provenance
        c = https_conn(core.host, None, timeout=timeout_s, context=create_ctx())
        if hasattr(c, "set_debuglevel"):
            c.set_debuglevel(0)
        return c

    def peer_fn(core, conn):
        return tls_ev(conn, core.host)

    def finish_fn(core, request, status, payload, stages, slot, tls):
        if tls is None:
            raise TransportFault("tls_peer_not_verified")                   # a 200 without verified TLS evidence is never returned as a provider response
        att = mint(provider=core.provider, model=str(request["model"]), endpoint=(core.host, core.base_path, core.path), request_digest=raw_digest(request["body"]),
                   response_digest=raw_digest(payload), request_key=str(request["request_key"]), reservation_slot=slot, stages=stages, tls=tls)
        return {"origin": "PRODUCTION", "attestation": att}

    def send(self, request: dict, credentials: str, timeout_s: int = 30) -> dict:
        if not isinstance(self, ProductionHttpsTransport) or not endpoint_ok(self.provider, (self.host, self.base_path, self.path)) or self.scheme != "https" or self.port is not None:
            raise TransportFault("unexpected_transport_type_or_destination")
        return dispatch(self, request, credentials, timeout_s, open_fn, peer_fn, finish_fn, fns)
    return send


def _make_production_init(table=_OFFICIAL_TABLE):
    """The constructor's view of the endpoints is a private copy captured here, at import time."""
    rows = {r[0]: r for r in table}
    init_common = _Core._init_common

    def __init__(self, provider: str, *, governor, authorized, max_uses: int = 1):
        row = rows.get(provider)
        if row is None:
            raise ValueError("unknown_provider")
        self.provider = provider
        self.host, self.base_path, self.path, self.scheme, self.port = row[1], row[2], row[3], "https", None
        init_common(self, governor, authorized, max_uses, row[4])
        self._frozen = True
    return __init__


class ProductionHttpsTransport(_Core):
    """The only transport that can mint an attestation. No factory, no loopback, no configurable host, no hook methods: the endpoint is the constant of the provider. Not subclassable."""
    is_production = True

    def __init_subclass__(cls, **kw):
        raise TypeError("ProductionHttpsTransport cannot be subclassed")

    __init__ = _make_production_init()

    send = _make_production_send()


class EndpointBoundGuard(GuardedTransport):
    """`GuardedTransport` plus: an atomic claim on the (provider, model, payload) binding and credential-format validation BEFORE any reservation. The claim is released if nothing was
    dispatched. The credential callable is wrapped so an invalid value is never passed on (the governor then denies with credentials_missing_or_malformed); no problem code ever contains
    the value."""

    def __init__(self, inner: _Core, governor, credentials, cost_fn=None):
        if not isinstance(inner, _Core):
            raise TypeError("inner must be a Test or Production transport of this module")
        self._raw_credentials = credentials
        self.last_credential_problems: list[str] = []
        super().__init__(inner, governor, self._checked, cost_fn)

    def _checked(self):
        c = self._raw_credentials()
        self.last_credential_problems = credential_problems(c)
        return c if not self.last_credential_problems else ""

    def send(self, request: dict, cancel=None) -> dict:
        claim = _claim_impl(self.inner, request)                           # atomic, before the governor reserves any budget
        _TLS.claim = claim
        try:
            return super().send(request, cancel)
        finally:
            _TLS.claim = None
            _release_impl(claim)                                           # no-op once the request was handed to the connection
