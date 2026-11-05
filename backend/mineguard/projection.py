"""Output boundary of the provider transport (META LIVE GATE 07, MG-F6-01). Pure functions: no network, no environment, no I/O.
Principle: the transport NEVER hands a provider document, event list, header or error body to its callers. What it returns is a NEW structure built here from values that were validated
against an explicit contract; every field the contract does not name is dropped, never preserved. This is structural minimisation: a hostile or buggy provider cannot smuggle a
secret (split across fields, escaped, percent-encoded, base64) to a consumer through a field the contract has no place for, and the places it does have (the grammar-validated numbers of a
SAM line, the identifier-shaped codes, the model's own text) are small and checked. `leaks` is a second, best-effort layer over what was received and what is returned: it covers literal,
JSON-escaped (decoded before the check), adjacent-field concatenation, punctuation-collapsed, percent-encoded/decoded, base64 and hex forms; it does NOT claim to detect every reversible
encoding. What the boundary guarantees is MINIMISATION: no provider free text, unknown field, header, raw document or mask payload crosses; every string that does cross is either from a
CLOSED documented set (codes, types, finish reasons) or one of the two free-text channels that cannot be removed (the model's content and the redacted error message, both scanned); every
number that crosses is bounded and scanned for spelling a credential. Residual covert capacity (a few bounded numbers, the content text) remains and is DOCUMENTED, not denied."""
from __future__ import annotations

import base64
import binascii
import json
import math
import re
from urllib.parse import quote, unquote

from . import sam31

MAX_EVENTS = 20000
MAX_CONTENT_CHARS = 20000
MAX_SCAN_CHARS = 16 * 1024 * 1024
PROVIDER_SSE = {"meta_sam31": True, "baseten_muse_spark_open": False}          # SAM streams SSE; the Baseten chat completion is one JSON document
# CLOSED sets: a provider-controlled string is returned only if it is one of these exact documented values; anything else becomes None (never passed through)
ERROR_CODES = frozenset({"invalid_api_key", "model_not_found", "file_not_found", "rate_limit_exceeded", "server_shutting_down", "service_overloaded", "backend_unavailable", "payload_too_large",
                         "gateway_timeout", "content_policy_violation"})
ERROR_TYPES = frozenset({"invalid_request_error", "authentication_error", "rate_limit_error", "server_error", "billing_error"})
FINISH_REASONS = frozenset({"stop", "length", "content_filter", "tool_calls", "function_call"})
USAGE_KEYS = frozenset({"input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens", "cached_tokens", "reasoning_tokens", "images", "video_frames_processed", "cost_usd", "tokens"})
# numeric channels are bounded (they cannot be removed): every number returned has a small, documented range
MAX_FRAME, MAX_OBJECT_ID, MAX_DIM, MAX_USAGE = 20000, 1_000_000, 100_000, 10 ** 9


def project_usage(u) -> dict | None:
    """Only the EXACT documented counter names survive (`USAGE_KEYS`), each with a finite number in [0, 10^9]. Any other key (a provider could choose keys to spell something), strings,
    nested objects and anything else are dropped."""
    if not isinstance(u, dict):
        return None
    out = {k: v for k, v in u.items() if k in USAGE_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= MAX_USAGE}
    return out or None


def _canonical_confidence(c):
    """None or a number in [0, 1] re-serialised with at most 4 decimals (the provider's own digits are never carried)."""
    if c is None:
        return None
    try:
        v = float(c)
    except ValueError:
        return False
    if not math.isfinite(v) or not 0 <= v <= 1:
        return False
    t = f"{v:.4f}".rstrip("0").rstrip(".")
    return t or "0"


def canonical_delta(idx: int, recs: list[dict]) -> str:
    """Re-serialises a PARSED SAM line from its validated numbers only. The mask payload is NOT carried: masks are metadata only in this build (never decoded), so a fixed one-character
    placeholder replaces it and cannot carry anything."""
    parts = []
    for r in recs:
        b, m = r["box"], r["mask"]
        cc = _canonical_confidence(r["confidence"])
        c = "" if cc is None else f";c={cc}"
        parts.append(f"{r['object_id']}<|box;x1={b['x1']};y1={b['y1']};x2={b['x2']};y2={b['y2']};w={b['w']};h={b['h']}{c}|><|mask;x=0;y=0{c};data={m['rows']},{m['cols']},{m['encoding']}0|>")
    return f"<{idx}f>" + ",".join(parts)


def project_sam_events(events) -> list | None:
    """SAM SSE events -> a new list of exactly: grammar-validated `response.output_text.delta` lines (canonical form), `response.completed` with allow-listed numeric usage, and
    `response.failed` / `response.incomplete` / `error` with an identifier-shaped code only. Unknown event types and every unknown field are dropped. A delta outside the documented grammar
    (or a non-dict / typeless event) makes the WHOLE response unusable (None): fail closed, never partially trusted."""
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        return None
    out = []
    for ev in events:
        if not isinstance(ev, dict) or not isinstance(ev.get("type"), str):
            return None
        t = ev["type"]
        if t == "response.output_text.delta":
            d = ev.get("delta")
            if d == "":
                continue
            try:
                idx, recs = sam31.parse_line(d)
            except sam31.Refused:
                return None
            if idx > MAX_FRAME or any(r["object_id"] > MAX_OBJECT_ID or _canonical_confidence(r["confidence"]) is False
                                       or max(r["box"].values()) > MAX_DIM or max(r["mask"]["rows"], r["mask"]["cols"]) > MAX_DIM for r in recs):
                return None                                                      # numbers outside the bounded ranges: the response is not trusted
            out.append({"type": t, "delta": canonical_delta(idx, recs)})
        elif t == "response.completed":
            r = ev.get("response")
            u = project_usage(r.get("usage")) if isinstance(r, dict) else None
            out.append({"type": t, **({"response": {"usage": u}} if u else {})})
        elif t in ("response.failed", "response.incomplete", "error"):
            code = ev.get("code")
            if code is None and isinstance(ev.get("error"), dict):
                code = ev["error"].get("code")
            out.append({"type": t, "code": code if code in ERROR_CODES else None})
        # any other event type: nothing the contract uses -> dropped
    return out


def project_chat_completion(doc) -> dict | None:
    """Baseten chat completion -> {choices:[{message:{content}, finish_reason?}], usage?}. `finish_reason` only from a closed set; reasoning fields, tool calls, extra choices, logprobs, ids,
    model strings, headers and every unknown field are dropped. The content is the only free text and is returned as a plain string (the model's own output, validated later by the Muse
    profile and scanned)."""
    if not isinstance(doc, dict):
        return None
    ch = doc.get("choices")
    if not isinstance(ch, list) or not ch or not isinstance(ch[0], dict):
        return None
    msg = ch[0].get("message")
    content = msg.get("content") if isinstance(msg, dict) else None
    if content is None and isinstance(msg, dict):
        content = ""                                                              # a reasoning-only answer: empty text, rejected later by the Muse profile
    if not isinstance(content, str) or len(content) > MAX_CONTENT_CHARS:
        return None
    first = {"message": {"content": content}}
    fr = ch[0].get("finish_reason")
    if fr in FINISH_REASONS:
        first["finish_reason"] = fr
    out = {"choices": [first]}                                                  # the provider's `id` and `model` strings are NOT returned: the model is the one that was requested
    u = project_usage(doc.get("usage"))
    if u:
        out["usage"] = u
    return out


def project(provider: str, body):
    if provider == "meta_sam31":
        return project_sam_events(body)
    if provider == "baseten_muse_spark_open":
        return project_chat_completion(body)
    return None


# ---- leak detection (second layer; best effort, documented as not universal) -------------------------------------------------------------------------------------------------
class _TooDeep(Exception):
    pass


def _strings(obj, acc: list, depth: int = 0, keys: bool = True) -> None:
    if depth > 50:
        raise _TooDeep
    if isinstance(obj, str):
        acc.append(obj)
        t = obj.lstrip()
        if t[:1] in ("{", "[") and len(obj) <= MAX_CONTENT_CHARS and depth < 8:
            try:
                inner = json.loads(obj)
            except ValueError:
                inner = None
            if inner is not None:
                _strings(inner, acc, depth + 1, keys)                              # JSON text inside a string (the Muse content is JSON): its decoded strings count too
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if keys:
                _strings(k, acc, depth + 1, keys)
            _strings(v, acc, depth + 1, keys)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _strings(v, acc, depth + 1, keys)


def _collapse(t: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", t)


def _forms(secret: str) -> set[str]:
    b = secret.encode("utf-8")
    f = {secret, quote(secret, safe=""), b.hex(), b.hex().upper(), base64.b64encode(b).decode(), base64.urlsafe_b64encode(b).decode()}
    f |= {x.rstrip("=") for x in list(f)}
    return {x for x in f if x}


def text_leaks(text: str, secret: str) -> bool:
    """True if `secret` appears in `text` in any of: literal, percent-encoded or base64/hex-encoded form, after removing punctuation/whitespace between its characters, or after
    percent-decoding / base64-decoding the text's own tokens. Not a universal detector."""
    if not secret or not text:
        return False
    if len(text) > MAX_SCAN_CHARS:
        return True                                                              # too large to inspect: fail closed
    forms = _forms(secret)
    cforms = {c for c in (_collapse(x) for x in forms) if len(c) >= 6}
    variants = {text, unquote(text)}
    variants |= {_collapse(v) for v in list(variants)}
    if any(f in v for f in forms for v in variants) or any(c in v for c in cforms for v in variants):
        return True
    for tok in re.findall(r"[A-Za-z0-9+/_-]{8,}={0,2}", text):
        for dec in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                raw = dec(tok + "=" * (-len(tok) % 4))
            except (binascii.Error, ValueError):
                continue
            s = raw.decode("latin-1")
            if secret in s or (len(_collapse(secret)) >= 6 and _collapse(secret) in _collapse(s)):
                return True
    return False


def _numbers(obj, acc: list, depth: int = 0, sort_keys: bool = False) -> None:
    if depth > 50:
        raise _TooDeep
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        if isinstance(obj, int) or (math.isfinite(obj) and obj == int(obj)):
            acc.append(int(obj))
    elif isinstance(obj, dict):
        for k in (sorted(obj, key=str) if sort_keys else obj):
            _numbers(obj[k], acc, depth + 1, sort_keys)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _numbers(v, acc, depth + 1, sort_keys)
    elif isinstance(obj, str):
        if len(acc) < 200_000:
            acc.extend(int(x) for x in re.findall(r"\d{1,9}", obj))          # integers written inside strings (a SAM line carries its ids and coordinates as text)


_SAM_FIELDS = tuple(re.compile(x) for x in (r"(?:f>|,)(\d{1,9})<\|box", r"x1=(\d{1,9})", r"y1=(\d{1,9})", r"x2=(\d{1,9})", r"y2=(\d{1,9})", r"w=(\d{1,9})", r"h=(\d{1,9})", r"data=(\d{1,9}),", r",(\d{1,9}),[~!]"))


def _spell(nums: list[int]) -> list[str]:
    """The ways a sequence of integers can spell text: character codes (all, or bytes only), concatenated decimals, big-endian bytes; each also reversed."""
    raw = b"".join(n.to_bytes(max(1, (n.bit_length() + 7) // 8), "big") for n in nums if n >= 0).decode("latin-1")
    base = ["".join(chr(n) for n in nums if 0 <= n <= 0x10FFFF), "".join(chr(n) for n in nums if 0 <= n < 256), "".join(str(n) for n in nums), raw]
    return base + [b[::-1] for b in base]


def numeric_leaks(obj, secret: str) -> bool:
    """A credential spelled by numbers: the integers of the document (document order and key-sorted order), the integers written inside strings, and each FIELD of the SAM lines taken
    across records (ids, x1, y1, x2, y2, w, h, mask sizes), read as character codes, bytes, concatenated decimals or big-endian bytes, forwards or reversed. Contiguous only; not universal."""
    if not secret:
        return False
    cands: list[str] = []
    for sort_keys in (False, True):
        nums: list[int] = []
        try:
            _numbers(obj, nums, sort_keys=sort_keys)
        except _TooDeep:
            return True
        if len(nums) >= 4:
            cands += _spell(nums)
    strs: list[str] = []
    try:
        _strings(obj, strs, keys=False)
    except _TooDeep:
        return True
    blob = "\n".join(strs)
    for rx in _SAM_FIELDS:
        seq = [int(x) for x in rx.findall(blob)]
        if len(seq) >= 4:
            cands += _spell(seq)
    return any(text_leaks(c, secret) for c in cands)


def leaks(obj, secret: str) -> bool:
    """True if the credential is recoverable from ANY decoded string or key of `obj`, from the concatenation of all of them (adjacent fields, with or without a separator), or from the
    encodings `text_leaks` knows. Too deep or too large -> True (fail closed)."""
    if not secret:
        return False
    acc: list[str] = []
    vals: list[str] = []
    try:
        _strings(obj, acc)
        _strings(obj, vals, keys=False)
    except _TooDeep:
        return True
    if any(text_leaks(s, secret) for s in acc):
        return True
    if numeric_leaks(obj, secret):
        return True
    # adjacent fields: all strings in document order, and the VALUES alone (fragments separated only by key names are still adjacent once the keys are set aside)
    return any(text_leaks(sep.join(group), secret) for group in (acc, vals) for sep in ("", " "))
