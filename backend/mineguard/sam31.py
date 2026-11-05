"""SAM 3.1 contract, request builder, stream parser and authorization checkpoint (META LIVE READINESS 04). Pure functions: NO network, NO environment, NO credential, NO file I/O.
Contract source: official Meta Model API documentation read on 2026-11-01 (https://dev.meta.ai/docs/media-segmentation, /sam/segmenting, /sam/reading-segmentation, /sam/overview,
/file-handling, /error-handling, /pricing-rate-limits; pages undated). Field statuses are in `CONTRACT` and mirrored in docs/release/META_CONTRACT_MATRIX.md.
Nothing here can send anything: dispatch exists only in `metahttp.py`, behind `costgov.GuardedTransport`, and no application route imports it."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import struct
from decimal import Decimal
from urllib.parse import urlsplit

from .rawjson import raw_digest

BASE_URL = "https://api.meta.ai/v1"
ENDPOINT_PATH = "/responses"      # relative to BASE_URL: the full endpoint is POST https://api.meta.ai/v1/responses
MODEL = "sam-3.1"
PROVIDER_ID = "meta_sam31"
PRICE_PER_IMAGE_USD = Decimal("2.50") / Decimal(1000)      # VERIFIED 2026-11-01 on an undated page; re-read on the day
PRICE_SOURCE = "https://dev.meta.ai/docs/pricing-rate-limits.md read 2026-11-01 (undated page)"
SAFETY_MARGIN = Decimal("1.25")
# INTERNAL conservative limits chosen by this project. The provider documents NO image size/resolution limit for SAM: these are not provider limits.
INTERNAL_MAX_IMAGE_BYTES = 5 * 1024 * 1024
INTERNAL_MAX_SIDE_PX = 4096
INTERNAL_MAX_CONCEPT_CHARS = 60
MAX_CALLS_FIRST_LIVE = 1
RETRIES_FIRST_LIVE = 0
ALLOWED_BODY_KEYS = frozenset({"model", "input", "stream", "metadata"})
ALLOWED_METADATA = {"include_confidence": "true", "mask_encoding": "one_bit"}   # string values, exactly as documented

V, A, U, X = "VERIFIED", "ASSUMED", "UNKNOWN", "UNSUPPORTED"
CONTRACT = [  # (field, value, status, evidence)
    ("endpoint", "POST https://api.meta.ai/v1/responses", V, "media-segmentation, overview"),
    ("model", "sam-3.1", V, "models, media-segmentation"),
    ("auth", "Authorization: Bearer <key>; keys start with LLM_; created/deleted in the Model API dashboard", V, "authentication"),
    ("headers", "Content-Type: application/json; Accept: text/event-stream (streaming examples)", V, "media-segmentation"),
    ("input item", '{"type":"message","role":"user","content":[input_text, input_image]}', V, "media-segmentation curl example"),
    ("input_text", "one short noun phrase; one concept per request", V, "media-segmentation, overview"),
    ("input_image.image_url", "public https URL", V, "all examples"),
    ("input_image base64 / data URL", "not stated for input_image (inline base64 is documented for input_file and input_video only)", U, "file-handling"),
    ("input_image file_id", "Files API upload exists (POST /v1/files, purpose=user_data, optional expires_after 3600..2592000 s); file_id use with input_image is not shown", U, "file-handling"),
    ("image formats / size / resolution for SAM", "not stated (Files API lists PNG, JPEG, GIF, WebP, ICO; 50 MB inline, 1 GiB Files API, generic)", U, "file-handling"),
    ("stream", "true in all raw examples; omitting it returns the complete result in one response (non-stream body shape not shown)", V, "overview, segmenting"),
    ("metadata.include_confidence", 'string "true" (not a boolean); adds ;c= to every box and mask', V, "segmenting"),
    ("metadata.mask_encoding", '"lossless" (default, ~) or "one_bit" (!)', V, "segmenting"),
    ("response (stream)", "SSE: response.output_text.delta (one line per frame), response.completed; failures: response.failed, error; zero matches = empty lane", V, "reading-segmentation, error-handling"),
    ("SSE event JSON envelope", "event field names beyond the event type names are not shown", A, "assumed OpenAI Responses-style: {type, delta}"),
    ("line grammar", "FRAME record (',' record)* ; record = id box mask ; frame marker <Nf>; image = <0f>", V, "reading-segmentation"),
    ("box", "<|box;x1=;y1=;x2=;y2=;w=;h=[;c=]|> integer source pixels, top-left origin; w,h = source size", V, "reading-segmentation"),
    ("box corner inclusivity (output)", "x2/y2 are documented as INCLUSIVE for box PROMPTS; for output boxes not stated", A, "segmenting vs reading-segmentation"),
    ("mask", "<|mask;x=0;y=0[;c=];data=H,W,<~|!>base85|> ; raster mapping beyond the x=0;y=0 origin not stated", V, "reading-segmentation"),
    ("object id", "bare integer, stable within a response, sparse, not a safety track", V, "reading-segmentation"),
    ("confidence", "0..1, only with include_confidence; calibration not stated", V, "segmenting"),
    ("timestamps in response", "none; the source timebase is authoritative", V, "reading-segmentation (absent)"),
    ("usage (SAM)", "usage.video_frames_processed for video; the object on image responses is not specified", U, "pricing-rate-limits"),
    ("error shape", '{"error":{"message","type","param","code"}}; 400/401/402/403/404/413/429(Retry-After)/500/503/504', V, "error-handling"),
    ("retry guidance", "retry 429/500/503 with backoff; do NOT retry 400/401/402/403/404/413; 504: change the request", V, "error-handling"),
    ("failed requests billed?", "not stated", U, "error-handling"),
    ("idempotency", "not stated", U, "error-handling"),
    ("price", "US$2.50 per 1,000 images; video US$0.20 per 1,000 frames, min 50 frames", V, "pricing-rate-limits (undated)"),
    ("rate limit", "sam-3.1: 3,000 RPM, no TPM; per team, keys share limits", V, "pricing-rate-limits"),
    ("credits / spend cap / alerts / prepaid", "not stated", U, "pricing-rate-limits, authentication"),
    ("when credits run out", "not stated (402 billing error exists)", U, "error-handling"),
    ("data retention / training use of SAM inputs", "not stated for SAM; Standard vs Contributor modes are described for Muse models", U, "pricing-rate-limits"),
]


class Refused(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(obj) -> str:
    """The digest the owner authorizes is the governor's own `raw_digest` of the request body, so what is approved offline is exactly what `GuardedTransport` checks."""
    return raw_digest(obj)


# ---- validation -------------------------------------------------------------------------------------------------------------------------------------------------------
def image_facts(data: bytes) -> dict:
    """Format, size and dimensions of a PNG or JPEG from its bytes; anything else, truncated or oversized is refused."""
    if len(data) > INTERNAL_MAX_IMAGE_BYTES:
        raise Refused("image_too_large")
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR" and len(data) >= 24:
        fmt, (w, h) = "png", struct.unpack(">II", data[16:24])
    elif data[:3] == b"\xff\xd8\xff":
        fmt, w, h, i = "jpeg", 0, 0, 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            mk = data[i + 1]
            if mk in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                break
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0] if mk not in (0xD8, 0x01) and not 0xD0 <= mk <= 0xD7 else 2
        if not (w and h):
            raise Refused("jpeg_dimensions_unreadable")
    else:
        raise Refused("image_format_not_png_or_jpeg")
    if not (0 < w <= INTERNAL_MAX_SIDE_PX and 0 < h <= INTERNAL_MAX_SIDE_PX):
        raise Refused("image_dimensions_out_of_range")
    return {"format": fmt, "bytes": len(data), "width": w, "height": h, "sha256": hashlib.sha256(data).hexdigest()}


def validate_image_url(url: str) -> str:
    """`image_url` must be a public https URL with no credential, query or fragment (a signed URL would put a secret into the payload and the ledger)."""
    if not isinstance(url, str) or not url or len(url) > 2048 or re.search(r"\s", url):
        raise Refused("url_invalid")
    u = urlsplit(url)
    if u.scheme != "https" or not u.hostname:
        raise Refused("url_not_https")
    if u.username or u.password:
        raise Refused("url_has_userinfo")
    if u.query or u.fragment:
        raise Refused("url_has_query_or_fragment")
    host = u.hostname.lower()
    if host == "localhost" or host.endswith((".local", ".internal", ".localhost", ".lan", ".home")) or "." not in host and not host.startswith("["):
        try:
            ipaddress.ip_address(host)
        except ValueError:
            raise Refused("url_host_not_public")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and not ip.is_global:
        raise Refused("url_host_not_public")
    return url


def validate_concept(text: str) -> str:
    if not isinstance(text, str) or not text.strip() or len(text) > INTERNAL_MAX_CONCEPT_CHARS or re.search(r"[\x00-\x1f\x7f<>|]", text) or "," in text or ";" in text:
        raise Refused("concept_invalid")
    return text.strip()


# ---- request builder (offline) ---------------------------------------------------------------------------------------------------------------------------------------
def build_body(image_url: str, concept: str) -> dict:
    body = {"model": MODEL, "stream": True,
            "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": validate_concept(concept)},
                                                                       {"type": "input_image", "image_url": validate_image_url(image_url)}]}],
            "metadata": dict(ALLOWED_METADATA)}
    assert set(body) == ALLOWED_BODY_KEYS
    return body


def build_governed_request(image_url: str, concept: str, request_key: str) -> dict:
    """The dict `costgov.GuardedTransport.send` accepts (and nothing else): keys limited to its ALLOWED_REQUEST_KEYS; the body digest is what the owner authorizes."""
    if not isinstance(request_key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,64}", request_key):
        raise Refused("request_key_invalid")
    return {"model": MODEL, "request_key": request_key, "provider": PROVIDER_ID, "media": True, "units": 1, "est_tokens": 0, "body": build_body(image_url, concept)}


def preview(image_url: str, concept: str) -> dict:
    """Everything the owner must inspect and approve, computed offline: what would be transmitted, the digest to authorize, the estimate."""
    body = build_body(image_url, concept)
    return {"endpoint": BASE_URL + ENDPOINT_PATH, "method": "POST", "body": body, "body_digest": digest(body),
            "headers": ["Authorization: Bearer <redacted>", "Content-Type: application/json", "Accept: text/event-stream"],
            "transmitted_fields": ["model", "stream", "input[0].type/role", "input[0].content[0] (input_text: the concept)", "input[0].content[1] (input_image: the https URL)",
                                   "metadata.include_confidence", "metadata.mask_encoding"],
            "not_transmitted": ["image bytes (the provider fetches the URL)", "any incident, zone, video, track or personal data", "the key inside the body"],
            "estimate": estimate_cost(1)}


def estimate_cost(images: int) -> dict:
    base = PRICE_PER_IMAGE_USD * images
    return {"images": images, "unit_price_usd": str(PRICE_PER_IMAGE_USD), "list_price_usd": str(base), "with_margin_usd": str(base * SAFETY_MARGIN), "price_source": PRICE_SOURCE,
            "price_status": "VERIFIED on an undated page; re-read on the day", "provider_side_cap": "UNKNOWN (no spend cap, credit or alert is documented)",
            "failed_requests_billed": "UNKNOWN"}


# ---- stream parser (offline; input is text the caller supplies) ----------------------------------------------------------------------------------------------------------
_FRAME = re.compile(r"<(\d+)f>")
_ID = re.compile(r"(\d+)")
_BOX = re.compile(r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)(?:;c=([0-9]*\.?[0-9]+))?\|>")
_MASK = re.compile(r"<\|mask;x=0;y=0(?:;c=([0-9]*\.?[0-9]+))?;data=([1-9]\d*),([1-9]\d*),([~!])([\x21-\x7e]+?)\|>(?=,\d+<\|box|$)")


def parse_line(delta: str) -> tuple[int, list[dict]]:
    """One `response.output_text.delta` line -> (frame index, records). Raises Refused for anything outside the documented grammar. A mask is REQUIRED (record = id box mask)."""
    if not isinstance(delta, str):
        raise Refused("not_text")
    m = _FRAME.match(delta)
    if not m:
        raise Refused("missing_frame_marker")
    idx, pos, recs = int(m.group(1)), m.end(), []
    while pos < len(delta):
        if recs:
            if delta[pos] != ",":
                raise Refused("record_separator_expected")
            pos += 1
        mi = _ID.match(delta, pos)
        mb = _BOX.match(delta, mi.end()) if mi else None
        if not mb:
            raise Refused("record_outside_documented_grammar")
        mm = _MASK.match(delta, mb.end())
        if not mm:
            raise Refused("mask_missing_or_outside_documented_grammar")
        if mm.group(1) != mb.group(7):
            raise Refused("box_mask_confidence_disagree")
        pos = mm.end()
        x1, y1, x2, y2, w, h = (int(mb.group(i)) for i in range(1, 7))
        recs.append({"object_id": int(mi.group(1)), "box": {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "w": w, "h": h}, "confidence": None if mb.group(7) is None else mb.group(7),
                     "mask": {"rows": int(mm.group(2)), "cols": int(mm.group(3)), "encoding": mm.group(4), "payload_chars": len(mm.group(5))}})
    return idx, recs


def parse_stream(events: list, width: int, height: int, max_frames: int = 1) -> dict:
    """events: decoded SSE events (dicts with `type`, `delta`, ...). -> {"state", "reason", "frames": {idx: [records]}, "completed", "failed"}. Fail-closed: any undocumented element makes
    the WHOLE result UNKNOWN (never 'empty'). A zero-match stream that did complete is PARSED_NO_MATCH_REPORTED, which is not a statement of safety."""
    out = {"state": "UNKNOWN", "reason": None, "frames": {}, "completed": False, "failed": None, "assumptions": ["x2/y2 inclusivity of OUTPUT boxes is not stated: both are accepted up to w/h", "object ids are not tracks",
                                                                                                                       "mask payload is never decoded", "SSE event envelope fields are ASSUMED"]}
    if not isinstance(events, list):
        return {**out, "reason": "events_not_a_list"}
    for ev in events:
        if not isinstance(ev, dict) or not isinstance(ev.get("type"), str):
            return {**out, "reason": "event_without_type"}
        t = ev["type"]
        if t == "response.completed":
            out["completed"] = True
        elif t in ("response.failed", "error"):
            out["failed"] = t
        elif t == "response.output_text.delta":
            d = ev.get("delta")
            if d == "":
                continue
            try:
                idx, recs = parse_line(d)
            except Refused as e:
                return {**out, "reason": e.code}
            if idx in out["frames"]:
                return {**out, "reason": "duplicate_frame_marker"}
            if len(out["frames"]) >= max_frames or not 0 <= idx < max_frames:
                return {**out, "reason": "frame_marker_outside_request"}
            for r in recs:
                b = r["box"]
                if (b["w"], b["h"]) != (width, height):
                    return {**out, "reason": "frame_size_disagrees_with_source"}
                if not (0 <= b["x1"] <= b["x2"] <= width and 0 <= b["y1"] <= b["y2"] <= height):
                    return {**out, "reason": "box_outside_image"}                  # rejected, never clipped
            ids = [r["object_id"] for r in recs]
            if len(set(ids)) != len(ids):
                return {**out, "reason": "duplicate_object_id"}
            out["frames"][idx] = recs
        # other documented event types (created, in_progress, ...) carry no data this parser uses
    if out["failed"]:
        return {**out, "reason": out["failed"]}
    if not out["completed"]:
        return {**out, "reason": "stream_not_completed"}
    out["state"] = "PARSED_OBJECTS" if any(out["frames"].values()) else "PARSED_NO_MATCH_REPORTED"
    return out


# ---- explicit authorization checkpoint (cannot send) ----------------------------------------------------------------------------------------------------------------------
AUTH_REQUIRED = ("approved_by", "approved_utc", "expires_utc", "body_digest", "max_calls", "max_usd", "retries", "provider_side_limit_confirmed", "key_is_dedicated", "revocation_planned",
                 "image_rights_confirmed", "image_url_is_nonpersonal", "image_url_expires_or_is_public_by_design", "provider", "model")


def check_owner_authorization(record, body_digest: str, estimated_usd: str, now_utc: str) -> list[str]:
    """-> problems; EMPTY means only that the record is complete and consistent with this exact body. It enables nothing: the governor still needs its own configuration."""
    if not isinstance(record, dict):
        return ["record_missing_or_not_an_object"]
    p = [f"missing:{k}" for k in AUTH_REQUIRED if k not in record]
    if p:
        return p
    if record["body_digest"] != body_digest:
        p.append("body_digest_mismatch")
    if record["provider"] != PROVIDER_ID or record["model"] != MODEL:
        p.append("provider_or_model_mismatch")
    if record["max_calls"] != MAX_CALLS_FIRST_LIVE or isinstance(record["max_calls"], bool):
        p.append("max_calls_must_be_1")
    if record["retries"] != RETRIES_FIRST_LIVE or isinstance(record["retries"], bool):
        p.append("retries_must_be_0")
    try:
        mu = Decimal(str(record["max_usd"]))
        if mu < Decimal(estimated_usd) or mu > Decimal("0.05"):
            p.append("max_usd_outside_estimate_to_0.05")
    except Exception:
        p.append("max_usd_invalid")
    for k in ("provider_side_limit_confirmed", "key_is_dedicated", "revocation_planned", "image_rights_confirmed", "image_url_is_nonpersonal", "image_url_expires_or_is_public_by_design"):
        if record[k] is not True:
            p.append(f"not_confirmed:{k}")
    if not str(record["approved_by"]).strip():
        p.append("approved_by_empty")
    if not (isinstance(record["expires_utc"], str) and isinstance(now_utc, str) and record["expires_utc"] > now_utc):
        p.append("expired_or_invalid_expiry")
    return p
