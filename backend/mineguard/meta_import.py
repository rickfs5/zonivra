"""META EVIDENCE INTEGRATION 10: owner-controlled import and REVIEW of an externally obtained SAM 3.1 result (saved stream + provenance manifest).

What this module is: a pure function from three byte strings (the saved provider result, the manifest, an optional source image) to a review record `mg.meta.import/1`, plus a
refusal vocabulary. NO network, NO environment, NO credential, NO file I/O, no import of the risk kernel, of the local detector or of any transport.

What it never does:
  * it never executes SAM and never claims in-app inference: every record says `origin = EXTERNAL_MANUAL_PROVIDER_RESULT`, `in_app_inference = false`;
  * it never invents a class from a mask or from the prompt: segments are UNCLASSIFIED; the prompt concept is shown as "requested", not as a detection;
  * it never assigns RED/YELLOW/GREEN (no severity key exists in the record: `risk.assessed = false`); the record is not an observation set and is not read by the risk engine;
  * it never changes a canonical RF-DETR observation set or a stored run;
  * it never decodes mask pixels: the raster semantics (base85 alphabet, bit order, crop vs full frame) are NOT stated by the provider documentation, so a mask is only structure-checked
    and is `NOT_RENDERED`; boxes (documented semantics, integer source pixels, top-left origin) are shown only after they verify against the image size;
  * it never proves authenticity: the provider offers no signature, so a manifest hash match proves only that these bytes are the bytes the owner declared.
The documented stream grammar is parsed HERE, by a small self-contained strict parser, not by importing the inert Meta stack: the Gate 07 containment invariants (test_meta_invariants) forbid any
application module from importing the Meta modules, and this feature must not weaken them. `test_external_import.py` pins this parser to the Meta-stack parser on every shared case, so the two cannot drift
silently. Anything outside the documented grammar is refused, never repaired."""
from __future__ import annotations

import hashlib
import json
import re
import struct

from .canon import digest, loads_strict

SCHEMA = "mg.meta.import/1"
MANIFEST_SCHEMA = "mg.meta.import.manifest/1"
ORIGIN = "EXTERNAL_MANUAL_PROVIDER_RESULT"
PROVIDER = "meta_sam31"
MODEL = "sam-3.1"
CLASS_SYNTHETIC = "SYNTHETIC_FIXTURE"
CLASS_DECLARED = "DECLARED_EXTERNAL_API_RESPONSE"
PROVENANCE_CLASSES = (CLASS_SYNTHETIC, CLASS_DECLARED)

# verification states: the vocabulary is closed and has NO state meaning "live verified" or "in-app inference verified"
ST_SYNTHETIC = "SYNTHETIC_FIXTURE"
ST_HASHES_OK = "HASHES_VERIFIED_AUTHENTICITY_NOT_PROVEN"

MAX_RESULT_BYTES = 2 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024
MAX_IMAGE_BYTES = 5 * 1024 * 1024          # this project's own conservative limits; the provider documents none for SAM
MAX_SIDE_PX = 4096
MAX_EVENTS = 2000
MAX_LINE_CHARS = 1 << 20
MAX_OBJECTS = 200
MAX_MASK_SIDE = 16384

_HEX64 = re.compile(r"[0-9a-f]{64}")
_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,63}")
_CONF = re.compile(r"(?:0|1)?\.?[0-9]*")

REFUSALS = (
    "result_too_large", "result_empty", "result_not_utf8", "result_has_control_characters", "result_line_too_long", "result_not_a_complete_stream", "result_unsupported_framing",
    "result_event_not_json", "result_event_without_type", "result_event_name_disagrees", "result_too_many_events", "result_provider_failure_event",
    "result_stream_not_parsed", "result_unknown_event_type", "result_unknown_event_field", "result_event_order_invalid",
    "result_done_sentinel_misplaced", "result_observed_schema_violation", "result_observed_order_invalid", "result_observed_sequence_invalid", "result_observed_text_mismatch",
    "result_observed_identity_inconsistent", "result_observed_record_terminator_invalid", "result_confidence_invalid", "result_too_many_objects", "result_mask_structure_invalid",
    "manifest_too_large", "manifest_not_json", "manifest_not_an_object", "manifest_schema_unsupported", "manifest_unknown_or_missing_fields", "manifest_field_invalid",
    "model_identity_mismatch", "provenance_class_invalid", "result_hash_mismatch", "result_size_mismatch", "image_hash_mismatch", "image_dimensions_mismatch",
    "image_unsupported", "image_too_large", "synthetic_bytes_declared_real", "synthetic_label_missing",
)


class ImportRefused(ValueError):
    """The input is rejected as a whole; nothing is stored, repaired or partially accepted."""

    def __init__(self, code: str):
        assert code in REFUSALS, code
        super().__init__(code)
        self.code = code


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---- manifest -------------------------------------------------------------------------------------------------------------------------------------------------------
_TOP = {"schema", "provenance_class", "provider", "model", "concept", "captured_utc", "declared_by", "source_image", "response", "request_body_sha256", "fixture_label"}
_IMG = {"sha256", "width", "height"}
_RESP = {"sha256", "bytes", "http_status", "content_type"}


def _int(v, lo, hi) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and lo <= v <= hi


def parse_manifest(raw: bytes) -> dict:
    """Strict: unknown or missing members are refused. Nothing in the manifest is ever used as a path, a command, a URL or a configuration value."""
    if len(raw) > MAX_MANIFEST_BYTES:
        raise ImportRefused("manifest_too_large")
    try:
        m = loads_strict(raw)
    except (ValueError, RecursionError):
        raise ImportRefused("manifest_not_json")
    if not isinstance(m, dict):
        raise ImportRefused("manifest_not_an_object")
    if m.get("schema") != MANIFEST_SCHEMA:
        raise ImportRefused("manifest_schema_unsupported")
    synthetic = m.get("provenance_class") == CLASS_SYNTHETIC
    if m.get("provenance_class") not in PROVENANCE_CLASSES:
        raise ImportRefused("provenance_class_invalid")
    required = (_TOP - {"fixture_label"} - ({"request_body_sha256"} if synthetic else set())) | ({"fixture_label"} if synthetic else set())
    optional = {"request_body_sha256"} if synthetic else set()
    keys = set(m)
    if not required <= keys or not keys <= required | optional:
        raise ImportRefused("manifest_unknown_or_missing_fields")
    if m["provider"] != PROVIDER or m["model"] != MODEL:
        raise ImportRefused("model_identity_mismatch")
    if synthetic and m["fixture_label"] != CLASS_SYNTHETIC:
        raise ImportRefused("synthetic_label_missing")
    img, resp = m["source_image"], m["response"]
    ok = (isinstance(m["concept"], str) and _TEXT.fullmatch(m["concept"]) and isinstance(m["declared_by"], str) and _TEXT.fullmatch(m["declared_by"])
          and isinstance(m["captured_utc"], str) and _UTC.fullmatch(m["captured_utc"])
          and isinstance(img, dict) and set(img) == _IMG and isinstance(img["sha256"], str) and _HEX64.fullmatch(img["sha256"])
          and _int(img["width"], 1, MAX_SIDE_PX) and _int(img["height"], 1, MAX_SIDE_PX)
          and isinstance(resp, dict) and set(resp) == _RESP and isinstance(resp["sha256"], str) and _HEX64.fullmatch(resp["sha256"]) and _int(resp["bytes"], 1, MAX_RESULT_BYTES)
          and resp["http_status"] == 200 and resp["content_type"] == "text/event-stream"
          and ("request_body_sha256" not in m or (isinstance(m["request_body_sha256"], str) and _HEX64.fullmatch(m["request_body_sha256"]))))
    if not ok:
        raise ImportRefused("manifest_field_invalid")
    return m


# ---- documented stream grammar (self-contained; pinned to the Meta-stack parser by the tests) -----------------------------------------------------------------------------
_FRAME = re.compile(r"<(\d+)f>")
_ID = re.compile(r"(\d+)")
_BOX = re.compile(r"<\|box;x1=(\d+);y1=(\d+);x2=(\d+);y2=(\d+);w=(\d+);h=(\d+)(?:;c=([0-9]*\.?[0-9]+))?\|>")
_MASK = re.compile(r"<\|mask;x=0;y=0(?:;c=([0-9]*\.?[0-9]+))?;data=([1-9]\d*),([1-9]\d*),([~!])([\x21-\x7e]+?)\|>(?=,\d+<\|box|$)")
ASSUMPTIONS = ["x2/y2 inclusivity of OUTPUT boxes is not stated: both are accepted up to w/h", "object ids are not tracks", "mask payload is never decoded", "SSE event envelope fields are ASSUMED"]

ASSUMPTIONS_OBSERVED = ["grammar profile is OBSERVED from one owner-executed response, not provider-specified", "x2/y2 inclusivity of OUTPUT boxes is not stated: both are accepted up to w/h",
                        "object ids are not tracks", "mask payload is never decoded", "the single trailing LF on the record line is an observed terminator, not a documented one"]


class _Bad(Exception):
    pass


def _parse_line(delta: str) -> tuple[int, list[dict]]:
    """One `response.output_text.delta` line -> (frame index, records). A record is `id box mask`; the mask is REQUIRED; confidences of box and mask must agree."""
    if not isinstance(delta, str):
        raise _Bad("not_text")
    m = _FRAME.match(delta)
    if not m:
        raise _Bad("missing_frame_marker")
    idx, pos, recs = int(m.group(1)), m.end(), []
    while pos < len(delta):
        if recs:
            if delta[pos] != ",":
                raise _Bad("record_separator_expected")
            pos += 1
        mi = _ID.match(delta, pos)
        mb = _BOX.match(delta, mi.end()) if mi else None
        if not mb:
            raise _Bad("record_outside_documented_grammar")
        mm = _MASK.match(delta, mb.end())
        if not mm:
            raise _Bad("mask_missing_or_outside_documented_grammar")
        if mm.group(1) != mb.group(7):
            raise _Bad("box_mask_confidence_disagree")
        pos = mm.end()
        x1, y1, x2, y2, w, h = (int(mb.group(i)) for i in range(1, 7))
        recs.append({"object_id": int(mi.group(1)), "box": {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "w": w, "h": h}, "confidence": mb.group(7),
                     "mask": {"rows": int(mm.group(2)), "cols": int(mm.group(3)), "encoding": mm.group(4), "payload_chars": len(mm.group(5))}})
    return idx, recs


def parse_wire(events: list, width: int, height: int) -> dict:
    """Decoded events of ONE image request -> {"state", "reason", "frames"}. Fail-closed: any undocumented element makes the WHOLE result UNKNOWN. A completed zero-match stream is
    PARSED_NO_MATCH_REPORTED, which is not a statement that nothing is there. Boxes are rejected, never clipped."""
    out = {"state": "UNKNOWN", "reason": None, "frames": {}, "completed": False, "failed": None}
    for ev in events:
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
                idx, recs = _parse_line(d)
            except _Bad as e:
                return {**out, "reason": str(e)}
            if idx in out["frames"]:
                return {**out, "reason": "duplicate_frame_marker"}
            if len(out["frames"]) >= 1 or idx != 0:
                return {**out, "reason": "frame_marker_outside_request"}
            for r in recs:
                b = r["box"]
                if (b["w"], b["h"]) != (width, height):
                    return {**out, "reason": "frame_size_disagrees_with_source"}
                if not (0 <= b["x1"] <= b["x2"] <= width and 0 <= b["y1"] <= b["y2"] <= height):
                    return {**out, "reason": "box_outside_image"}
            ids = [r["object_id"] for r in recs]
            if len(set(ids)) != len(ids):
                return {**out, "reason": "duplicate_object_id"}
            out["frames"][idx] = recs
        # other event types (created, in_progress, ...) carry no data this importer uses
    if out["failed"]:
        return {**out, "reason": out["failed"]}
    if not out["completed"]:
        return {**out, "reason": "stream_not_completed"}
    out["state"] = "PARSED_OBJECTS" if any(out["frames"].values()) else "PARSED_NO_MATCH_REPORTED"
    return out


def image_facts(data: bytes) -> dict | None:
    """Format, size and dimensions of a PNG or JPEG from its header bytes; None for anything else, truncated or out of range."""
    if len(data) > MAX_IMAGE_BYTES:
        return None
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
            return None
    else:
        return None
    if not (0 < w <= MAX_SIDE_PX and 0 < h <= MAX_SIDE_PX):
        return None
    return {"format": fmt, "bytes": len(data), "width": w, "height": h, "sha256": hashlib.sha256(data).hexdigest()}


# ---- saved stream ---------------------------------------------------------------------------------------------------------------------------------------------------
def parse_sse(raw: bytes) -> list[dict]:
    """Saved `text/event-stream` bytes -> decoded events. Only `event:` (optional, at most once) and `data:` (exactly once, a JSON object with a string `type`) fields are understood; blank
    lines end an event; the stream MUST end with a blank line (a cut-off capture is truncated and refused). Comments, `id:`, `retry:`, multi-line data and anything else are unsupported."""
    if not raw:
        raise ImportRefused("result_empty")
    if len(raw) > MAX_RESULT_BYTES:
        raise ImportRefused("result_too_large")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ImportRefused("result_not_utf8")
    text = text.replace("\r\n", "\n")
    if re.search(r"[\x00-\x08\x0b-\x1f\x7f]", text):    # CR left over, NUL, other controls (newline and tab are the only ones framing may use)
        raise ImportRefused("result_has_control_characters")
    if not text.endswith("\n\n"):
        raise ImportRefused("result_not_a_complete_stream")
    events = []
    for block in text.rstrip("\n").split("\n\n"):
        name, data = None, None
        for line in block.split("\n"):
            if len(line) > MAX_LINE_CHARS:
                raise ImportRefused("result_line_too_long")
            if line.startswith("event: ") and name is None:
                name = line[7:]
            elif line.startswith("data: ") and data is None:
                data = line[6:]
            else:
                raise ImportRefused("result_unsupported_framing")
        if data is None:
            raise ImportRefused("result_unsupported_framing")
        try:
            ev = loads_strict(data)
        except (ValueError, RecursionError):
            raise ImportRefused("result_event_not_json")
        if not isinstance(ev, dict) or not isinstance(ev.get("type"), str):
            raise ImportRefused("result_event_without_type")
        if name is not None and name != ev["type"]:
            raise ImportRefused("result_event_name_disagrees")
        events.append(ev)
        if len(events) > MAX_EVENTS:
            raise ImportRefused("result_too_many_events")
    return events


# ---- closed event grammar (MG-META-IMPORT-10-R1 / C5) -----------------------------------------------------------------------------------------------------------------
# The envelope is ASSUMED OpenAI-Responses-style ({type} / {type, delta}); only these members of these events are understood. Everything else -- an unknown event type, an extra member
# of a known event, a repeated or misplaced lifecycle event, anything after the terminal event -- is a provider extension this importer does not support and REFUSES, never ignores.
EVENT_FIELDS = {"response.created": frozenset({"type"}), "response.output_text.delta": frozenset({"type", "delta"}), "response.completed": frozenset({"type"})}
FAILURE_EVENTS = ("response.failed", "error")


def check_grammar(events: list) -> None:
    """Closed allowlist of event types and members, plus order: `response.created`? , `response.output_text.delta`* , `response.completed` exactly once and LAST. Raises ImportRefused."""
    for ev in events:
        allowed = EVENT_FIELDS.get(ev["type"])
        if allowed is None:
            raise ImportRefused("result_unknown_event_type")
        if set(ev) != allowed:                       # extra OR missing members (a delta without text is not a delta)
            raise ImportRefused("result_unknown_event_field")
        if "delta" in ev and not isinstance(ev["delta"], str):
            raise ImportRefused("result_unknown_event_field")
    kinds = [ev["type"] for ev in events]
    if "response.completed" in kinds and (kinds[-1] != "response.completed" or kinds.count("response.completed") != 1):
        raise ImportRefused("result_event_order_invalid")       # (no terminal event at all is refused by parse_wire as stream_not_completed)
    if kinds.count("response.created") > 1 or ("response.created" in kinds and kinds[0] != "response.created"):
        raise ImportRefused("result_event_order_invalid")


# ---- observed grammar profile (MG-META-REAL-SSE-12) -----------------------------------------------------------------------------------------------------------------------
# PROFILE_OBSERVED_STATUS says what this is: the shape of ONE owner-executed Meta SAM 3.1 response, nothing more. It is NOT Meta's official schema and NOT a statement about what other
# responses look like. It is selected ONLY by a terminal `data: [DONE]` sentinel; a stream without the sentinel is judged by the fixture grammar above and nothing else. A profile is applied
# whole or not at all: every event, every nested object and the order are checked against exact key sets; there is no "ignore the rest". Provider metadata is validated, never stored or shown
# as evidence (the stored source bytes stay intact; the review keeps only ints from `usage`).
PROFILE_FIXTURE = "FIXTURE_V1"
PROFILE_OBSERVED = "META_SAM31_OBSERVED_1"
PROFILE_OBSERVED_STATUS = "OBSERVED_FROM_ONE_OWNER_EXECUTED_META_RESPONSE"
DONE_LINE = b"data: [DONE]"
OBSERVED_ORDER = ("response.created", "response.in_progress", "response.output_item.added", "response.content_part.added", "response.output_text.delta", "response.output_text.done",
                  "response.content_part.done", "response.output_item.done", "response.completed")
_OBS_TOP = {
    "response.created": {"response", "sequence_number", "type"}, "response.in_progress": {"response", "sequence_number", "type"},
    "response.output_item.added": {"item", "output_index", "sequence_number", "type"},
    "response.content_part.added": {"content_index", "item_id", "output_index", "part", "sequence_number", "type"},
    "response.output_text.delta": {"content_index", "delta", "item_id", "logprobs", "output_index", "sequence_number", "type"},
    "response.output_text.done": {"content_index", "item_id", "logprobs", "output_index", "sequence_number", "text", "type"},
    "response.content_part.done": {"content_index", "item_id", "output_index", "part", "sequence_number", "type"},
    "response.output_item.done": {"item", "output_index", "sequence_number", "type"},
    "response.completed": {"response", "sequence_number", "type"},
}
_RESP_KEYS = {"background", "created_at", "error", "id", "incomplete_details", "instructions", "model", "object", "output", "parallel_tool_calls", "service_tier", "status", "store",
              "temperature", "tool_choice", "tools", "top_logprobs", "top_p", "truncation"}
_USAGE = {"images_processed", "input_tokens", "input_tokens_details", "output_tokens", "output_tokens_details", "total_tokens"}
_RESP_ID = re.compile(r"resp_[A-Za-z0-9_-]{8,64}")
_MSG_ID = re.compile(r"msg_[A-Za-z0-9_-]{8,64}")
MAX_OBSERVED_TEXT = 1 << 20


def _bad():
    return ImportRefused("result_observed_schema_violation")


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and (not isinstance(v, int) or abs(v) <= 2**53 - 1)


def _keys(v, keys) -> dict:
    if not isinstance(v, dict) or set(v) != set(keys):
        raise _bad()
    return v


def _int0(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and v == 0


def _part(p, text: str) -> None:
    _keys(p, {"annotations", "logprobs", "text", "type"})
    if p["annotations"] != [] or p["logprobs"] != [] or p["type"] != "output_text" or not isinstance(p["text"], str) or p["text"] != text:
        raise _bad()


def _item(i, mid: str, status: str, text: str) -> None:
    _keys(i, {"content", "id", "role", "status", "type"})
    if i["id"] != mid or i["role"] != "assistant" or i["status"] != status or i["type"] != "message" or not isinstance(i["content"], list) or len(i["content"]) != 1:
        raise _bad()
    _part(i["content"][0], text)


def _response(r, *, completed: bool, text: str, mid: str) -> None:
    _keys(r, _RESP_KEYS | ({"completed_at", "usage"} if completed else set()))
    exact = {"background": False, "error": None, "incomplete_details": None, "instructions": None, "model": MODEL, "object": "response", "service_tier": "auto",
             "tool_choice": "auto", "truncation": "disabled", "tools": [], "status": "completed" if completed else "in_progress"}
    for k, v in exact.items():
        if r[k] != v or type(r[k]) is not type(v):
            raise _bad()
    if not (isinstance(r["id"], str) and _RESP_ID.fullmatch(r["id"])) or not (_num(r["created_at"]) and _num(r["temperature"]) and _num(r["top_p"])):
        raise _bad()
    if not (isinstance(r["parallel_tool_calls"], bool) and isinstance(r["store"], bool)) or not _int0(r["top_logprobs"]):
        raise _bad()
    if not completed:
        if r["output"] != []:
            raise _bad()
        return
    if not (_num(r["completed_at"]) and r["completed_at"] >= r["created_at"]):
        raise _bad()
    u = _keys(r["usage"], _USAGE)
    _keys(u["input_tokens_details"], {"cached_tokens"})
    _keys(u["output_tokens_details"], {"reasoning_tokens"})
    flat = [u["images_processed"], u["input_tokens"], u["output_tokens"], u["total_tokens"], u["input_tokens_details"]["cached_tokens"], u["output_tokens_details"]["reasoning_tokens"]]
    if not all(isinstance(x, int) and not isinstance(x, bool) and 0 <= x <= 10**9 for x in flat):
        raise _bad()
    if not isinstance(r["output"], list) or len(r["output"]) != 1:
        raise _bad()
    _item(r["output"][0], mid, "completed", text)


def split_done_sentinel(raw: bytes) -> bytes | None:
    """None when the stream has no `data: [DONE]` line (fixture grammar applies). Otherwise the stream WITHOUT its sentinel. Exactly one sentinel, as the very last block, LF framing, nothing after it."""
    lines = raw.split(b"\n")
    n = sum(1 for ln in lines if ln.rstrip(b"\r") == DONE_LINE)
    if n == 0:
        return None
    tail = DONE_LINE + b"\n\n"
    if n != 1 or not raw.endswith(b"\n\n" + tail) or lines[-3:] != [DONE_LINE, b"", b""]:
        raise ImportRefused("result_done_sentinel_misplaced")
    return raw[:-len(tail)]


def check_observed(events: list) -> str:
    """Events (sentinel already removed) -> the single text delta, validated against OBSERVED_ORDER and the exact nested schemas. Raises ImportRefused."""
    for ev in events:
        if ev["type"] not in _OBS_TOP:
            raise ImportRefused("result_unknown_event_type")
        if set(ev) != _OBS_TOP[ev["type"]]:
            raise ImportRefused("result_unknown_event_field")
    kinds = tuple(e["type"] for e in events)
    if kinds != OBSERVED_ORDER:
        raise ImportRefused("result_observed_order_invalid")
    for i, ev in enumerate(events):
        sn = ev["sequence_number"]
        if not isinstance(sn, int) or isinstance(sn, bool) or sn != i:
            raise ImportRefused("result_observed_sequence_invalid")
    created, progress, added, pa, delta, tdone, pdone, idone, completed = events
    mid, text = added["item"].get("id") if isinstance(added["item"], dict) else None, delta["delta"]
    if not (isinstance(mid, str) and _MSG_ID.fullmatch(mid)) or not isinstance(text, str):
        raise _bad()
    if not text or len(text) > MAX_OBSERVED_TEXT:
        raise _bad()
    for ev in events[2:8]:
        if "output_index" in ev and not _int0(ev["output_index"]):
            raise _bad()
        if "content_index" in ev and not _int0(ev["content_index"]):
            raise _bad()
        if "item_id" in ev and ev["item_id"] != mid:
            raise ImportRefused("result_observed_identity_inconsistent")
    if delta["logprobs"] != [] or tdone["logprobs"] != []:
        raise _bad()
    if not (isinstance(tdone["text"], str) and tdone["text"] == text):
        raise ImportRefused("result_observed_text_mismatch")
    _response(created["response"], completed=False, text="", mid=mid)
    _response(progress["response"], completed=False, text="", mid=mid)
    _item(added["item"], mid, "in_progress", "")
    _part(pa["part"], "")
    if created["response"]["id"] != progress["response"]["id"] or created["response"]["created_at"] != progress["response"]["created_at"]:
        raise ImportRefused("result_observed_identity_inconsistent")
    if (pdone["part"].get("text") if isinstance(pdone["part"], dict) else None) != text:
        raise ImportRefused("result_observed_text_mismatch")
    _part(pdone["part"], text)
    if not isinstance(idone["item"], dict) or not isinstance(idone["item"].get("content"), list) or len(idone["item"]["content"]) != 1 or \
            not isinstance(idone["item"]["content"][0], dict) or idone["item"]["content"][0].get("text") != text:
        raise ImportRefused("result_observed_text_mismatch")
    _item(idone["item"], mid, "completed", text)
    cr = completed["response"]
    if not isinstance(cr, dict) or not isinstance(cr.get("output"), list) or len(cr["output"]) != 1 or not isinstance(cr["output"][0], dict) \
            or not isinstance(cr["output"][0].get("content"), list) or len(cr["output"][0]["content"]) != 1 or not isinstance(cr["output"][0]["content"][0], dict) \
            or cr["output"][0]["content"][0].get("text") != text:
        raise ImportRefused("result_observed_text_mismatch")
    _response(cr, completed=True, text=text, mid=mid)
    if cr["id"] != created["response"]["id"] or cr["created_at"] != created["response"]["created_at"] or cr["output"][0] != idone["item"]:
        raise ImportRefused("result_observed_identity_inconsistent")
    # the one place where the record line differs from the fixture grammar: exactly ONE trailing LF terminator, no other LF
    if not text.endswith("|>\n") or text.count("\n") != 1 or "\r" in text:
        raise ImportRefused("result_observed_record_terminator_invalid")
    return text[:-1]


def observed_usage(events: list) -> dict:
    u = events[-1]["response"]["usage"]
    return {"images_processed": u["images_processed"], "input_tokens": u["input_tokens"], "output_tokens": u["output_tokens"], "total_tokens": u["total_tokens"]}


def _mask_structure(mask: dict, w: int, h: int) -> dict:
    """STRUCTURE only: the declared rows/cols are positive and bounded and a payload exists. No pixel is decoded and none is ever shown (raster semantics undocumented)."""
    rows, cols = mask["rows"], mask["cols"]
    if not (_int(rows, 1, MAX_MASK_SIDE) and _int(cols, 1, MAX_MASK_SIDE)) or mask["encoding"] not in ("~", "!") or mask["payload_chars"] < 1:
        raise ImportRefused("result_mask_structure_invalid")
    return {"status": "STRUCTURE_CHECKED_NOT_RENDERED", "reason": "RASTER_SEMANTICS_UNDOCUMENTED", "rows": rows, "cols": cols,
            "encoding": "one_bit" if mask["encoding"] == "!" else "lossless", "payload_chars": mask["payload_chars"]}


def _objects(parsed: dict, w: int, h: int) -> list[dict]:
    recs = parsed["frames"].get(0, [])
    if len(recs) > MAX_OBJECTS:
        raise ImportRefused("result_too_many_objects")
    out = []
    for r in recs:
        b = r["box"]
        c = r["confidence"]
        permille = None
        if c is not None:
            if not _CONF.fullmatch(c):
                raise ImportRefused("result_confidence_invalid")
            v = float(c)
            if not 0.0 <= v <= 1.0:
                raise ImportRefused("result_confidence_invalid")
            permille = int(v * 1000 + 0.5)
        out.append({"object_id": r["object_id"],
                    "box": {"x1": b["x1"], "y1": b["y1"], "x2": b["x2"], "y2": b["y2"]},     # verified inside the image by the parser; never clipped
                    "provider_score_permille": permille, "provider_score_kind": "provider_uncalibrated" if permille is not None else "not_reported",
                    "class_assigned": None, "label_state": "UNCLASSIFIED",
                    "mask": _mask_structure(r["mask"], w, h)})
    return out


# ---- the review record ----------------------------------------------------------------------------------------------------------------------------------------------
def build_review(result: bytes, manifest_raw: bytes, image: bytes | None = None, *, synthetic_digests: frozenset = frozenset(), imported_utc: str | None = None) -> dict:
    """-> `mg.meta.import/1`. Raises ImportRefused. Deterministic: same bytes, same record (`imported_utc` is outside the digest)."""
    man = parse_manifest(manifest_raw)
    if len(result) > MAX_RESULT_BYTES:
        raise ImportRefused("result_too_large")
    rsha = sha256(result)
    if rsha != man["response"]["sha256"]:
        raise ImportRefused("result_hash_mismatch")
    if len(result) != man["response"]["bytes"]:
        raise ImportRefused("result_size_mismatch")
    if man["provenance_class"] == CLASS_DECLARED and rsha in synthetic_digests:
        raise ImportRefused("synthetic_bytes_declared_real")           # a bundled synthetic stream can never be relabelled as a provider result
    w, h = man["source_image"]["width"], man["source_image"]["height"]
    checks = [{"id": "manifest_schema", "ok": True}, {"id": "model_identity", "ok": True}, {"id": "result_sha256", "ok": True}, {"id": "result_size", "ok": True}]
    image_sha, linkage = man["source_image"]["sha256"], "DECLARED_ONLY_IMAGE_NOT_SUPPLIED"
    if image is not None:
        if len(image) > MAX_IMAGE_BYTES:
            raise ImportRefused("image_too_large")
        facts = image_facts(image)
        if facts is None:
            raise ImportRefused("image_unsupported")
        if facts["sha256"] != image_sha:
            raise ImportRefused("image_hash_mismatch")
        if (facts["width"], facts["height"]) != (w, h):
            raise ImportRefused("image_dimensions_mismatch")
        linkage = "VERIFIED_BY_IMAGE_BYTES"
        checks += [{"id": "image_sha256", "ok": True}, {"id": "image_dimensions", "ok": True}]
    body = split_done_sentinel(result)                                   # None: fixture grammar. A `data: [DONE]` line selects the observed profile (and only that profile)
    events = parse_sse(result if body is None else body)
    if any(e["type"] in ("response.failed", "error") for e in events):
        raise ImportRefused("result_provider_failure_event")            # a provider failure is not a result
    profile, usage = PROFILE_FIXTURE, None
    if body is None:
        check_grammar(events)                                            # closed allowlist: unknown events/members/order are refused, never ignored
        wire = events
    else:
        profile = PROFILE_OBSERVED
        line = check_observed(events)                                    # the review parses the validated record line; the stored source bytes are never altered
        usage = observed_usage(events)
        wire = [{"type": "response.output_text.delta", "delta": line}, {"type": "response.completed"}]
    parsed = parse_wire(wire, w, h)
    if parsed["state"] not in ("PARSED_OBJECTS", "PARSED_NO_MATCH_REPORTED"):
        raise ImportRefused("result_stream_not_parsed")
    checks += [{"id": "stream_complete", "ok": True}, {"id": "event_allowlist_and_order", "ok": True}, {"id": "grammar_and_boxes_in_image", "ok": True}]
    if usage is not None:
        checks.append({"id": "observed_profile_schema", "ok": True})
    objects = _objects(parsed, w, h)
    synthetic = man["provenance_class"] == CLASS_SYNTHETIC
    rec = {
        "schema": SCHEMA, "origin": ORIGIN, "in_app_inference": False, "attributed_to_local_detector": False, "live_verified": False,
        "provenance_class": man["provenance_class"],
        "provider": PROVIDER, "model": MODEL,
        "verification": {"state": ST_SYNTHETIC if synthetic else ST_HASHES_OK, "authenticity": "NOT_PROVEN_NO_PROVIDER_SIGNATURE" if not synthetic else "NONE_SYNTHETIC",
                         "result_sha256": rsha, "result_bytes": len(result), "manifest_sha256": sha256(manifest_raw), "input_image_sha256": image_sha, "image_linkage": linkage,
                         "request_body_sha256": man.get("request_body_sha256"), "checks": checks},
        "capture": {"captured_utc": man["captured_utc"], "declared_by": man["declared_by"]},
        "source_image": {"width": w, "height": h},
        "concept_requested": man["concept"],
        "parse": {"state": parsed["state"], "frames_reported": sorted(parsed["frames"]), "assumptions": ASSUMPTIONS},
        "objects": objects,
        "risk": {"assessed": False, "reason": "IMPORTED_EVIDENCE_NEVER_ENTERS_THE_RISK_KERNEL"},
        "canonical_effect": "NONE",
        "limitations": ["authenticity of the provider origin is not provable from the bytes", "masks are not rendered: raster semantics are undocumented", "segments are not classes",
                        "accuracy not validated; one image proves nothing about performance", "absence of segments is not evidence of absence"]
        + (["SYNTHETIC test data written by this project: NOT produced by any Meta model"] if synthetic else []),
    }
    if usage is not None:          # observed profile only: fixture records keep exactly the keys they always had
        rec["grammar"] = {"profile": PROFILE_OBSERVED, "status": PROFILE_OBSERVED_STATUS, "official_provider_schema": False, "terminal_sentinel": "DONE", "events": len(OBSERVED_ORDER),
                          "provider_usage": usage}
        rec["parse"] = {**rec["parse"], "assumptions": ASSUMPTIONS_OBSERVED}
        rec["limitations"] = rec["limitations"] + ["grammar profile derived from ONE owner-executed response: it is not Meta's official schema; structural validity is not proof of provider identity"]
    rec["digest"] = digest({k: v for k, v in rec.items()})
    return {**rec, "imported_utc": imported_utc}


def import_id(result_sha: str, manifest_sha: str, image_sha: str | None) -> str:
    return "ext_" + hashlib.sha256(json.dumps([result_sha, manifest_sha, image_sha], separators=(",", ":")).encode()).hexdigest()[:16]
