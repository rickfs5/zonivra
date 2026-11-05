"""META LIVE READINESS 04: SAM 3.1 contract, request builder, stream parser and authorization checkpoint (pure; no network, no credentials).
Expectations come from the documented contract (dev.meta.ai docs read 2026-11-01), not from output."""
import json
import struct
import zlib
from pathlib import Path

import pytest

from mineguard import costgov, sam31 as S

FIX = json.loads((Path(__file__).parent / "fixtures_meta" / "SIMULATED_sam31_stream.json").read_text())
W, H = FIX["image"]["width"], FIX["image"]["height"]


def png(w=64, h=48):
    def ch(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x00\x00\x00" * w for _ in range(h))
    return b"\x89PNG\r\n\x1a\n" + ch(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + ch(b"IDAT", zlib.compress(raw)) + ch(b"IEND", b"")


def test_request_has_exactly_the_documented_shape():
    b = S.build_body("https://example.com/frame.png", "excavator")
    assert b == {"model": "sam-3.1", "stream": True, "metadata": {"include_confidence": "true", "mask_encoding": "one_bit"},
                 "input": [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "excavator"}, {"type": "input_image", "image_url": "https://example.com/frame.png"}]}]}
    assert isinstance(b["metadata"]["include_confidence"], str)          # documented as the STRING "true", not a boolean
    assert set(b) == S.ALLOWED_BODY_KEYS


def test_preview_lists_what_is_transmitted_and_what_is_not_and_never_a_key():
    p = S.preview("https://example.com/frame.png", "excavator")
    assert p["endpoint"] == "https://api.meta.ai/v1/responses" and p["method"] == "POST" and "Accept: text/event-stream" in p["headers"]
    assert "Bearer <redacted>" in " ".join(p["headers"]) and "LLM_" not in json.dumps(p)
    assert p["body"] == S.build_governed_request("https://example.com/frame.png", "excavator", "req-0000001")["body"]
    assert p["body_digest"] == costgov.raw_digest(p["body"])             # the owner authorizes the governor's own digest of the exact body
    assert p["estimate"]["list_price_usd"] == "0.0025" and p["estimate"]["with_margin_usd"] == "0.003125" and p["estimate"]["provider_side_cap"].startswith("UNKNOWN")
    assert any("image bytes" in x for x in p["not_transmitted"])


def test_governed_request_is_accepted_by_the_governor_key_set_and_declares_media_and_units():
    r = S.build_governed_request("https://example.com/frame.png", "excavator", "req-0000001")
    assert set(r) <= costgov.ALLOWED_REQUEST_KEYS and r["media"] is True and r["units"] == 1 and r["est_tokens"] == 0 and r["provider"] == "meta_sam31"
    assert costgov.count_media_parts(r["body"]) == 1
    for bad in ("short", "has space xxxxxx", "x" * 80, None, 5):
        with pytest.raises(S.Refused):
            S.build_governed_request("https://example.com/frame.png", "excavator", bad)


@pytest.mark.parametrize("url,code", [("http://example.com/a.png", "url_not_https"), ("https://user:pw@example.com/a.png", "url_has_userinfo"), ("https://example.com/a.png?sig=abc", "url_has_query_or_fragment"),
                                      ("https://example.com/a.png#x", "url_has_query_or_fragment"), ("https://localhost/a.png", "url_host_not_public"), ("https://192.168.1.5/a.png", "url_host_not_public"),
                                      ("https://127.0.0.1/a.png", "url_host_not_public"), ("https://10.0.0.2/a.png", "url_host_not_public"), ("https://169.254.169.254/a.png", "url_host_not_public"),
                                      ("https://nas.local/a.png", "url_host_not_public"), ("https://intranet/a.png", "url_host_not_public"), ("file:///etc/passwd", "url_not_https"), ("", "url_invalid"),
                                      ("https://a.com/a b.png", "url_invalid"), ("https://example.com/" + "a" * 2100, "url_invalid"), ("data:image/png;base64,AAAA", "url_not_https")])
def test_unsafe_or_undocumented_image_references_are_refused(url, code):
    with pytest.raises(S.Refused) as e:
        S.build_body(url, "excavator")
    assert e.value.code == code


def test_a_public_ip_literal_and_a_normal_host_are_accepted():
    assert S.validate_image_url("https://93.184.216.34/a.png") and S.validate_image_url("https://cdn.example.org/a/b/c.jpg")


@pytest.mark.parametrize("c", ["", "   ", "a" * 61, "person, truck", "a;b", "line\nbreak", "a<b", "a|b", None, 5])
def test_concept_must_be_one_short_plain_noun_phrase(c):
    with pytest.raises(S.Refused):
        S.build_body("https://example.com/a.png", c)


def test_image_validation_accepts_png_and_rejects_everything_else():
    f = S.image_facts(png(64, 48))
    assert (f["format"], f["width"], f["height"]) == ("png", 64, 48) and len(f["sha256"]) == 64
    for bad, code in [(b"GIF89a....", "image_format_not_png_or_jpeg"), (b"", "image_format_not_png_or_jpeg"), (png(5000, 10), "image_dimensions_out_of_range"), (png(0, 10), "image_dimensions_out_of_range"),
                      (b"\x00" * (S.INTERNAL_MAX_IMAGE_BYTES + 1), "image_too_large"), (b"\xff\xd8\xff\xe0\x00\x02", "jpeg_dimensions_unreadable")]:
        with pytest.raises(S.Refused) as e:
            S.image_facts(bad)
        assert e.value.code == code


def test_jpeg_dimensions_are_read_from_the_frame_header():
    jpg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00" + b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", 480, 640) + b"\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
    assert (S.image_facts(jpg)["width"], S.image_facts(jpg)["height"]) == (640, 480)


@pytest.mark.parametrize("name", list(FIX["cases"]))
def test_every_stream_case_parses_as_declared_and_unknown_never_means_empty(name):
    c = FIX["cases"][name]
    r = S.parse_stream(c["events"], W, H)
    assert r["state"] == c["expect"], (name, r["reason"])
    if c["expect"] == "UNKNOWN":
        assert r["frames"] == {} or r["reason"], r
        assert r["reason"]                                             # a failed parse always says why and never reads as "no objects"


def test_boxes_and_optional_confidence_are_kept_exactly_and_masks_are_metadata_only():
    r = S.parse_stream(FIX["cases"]["two_objects"]["events"], W, H)
    recs = r["frames"][0]
    assert [(x["object_id"], x["box"]["x1"], x["box"]["x2"], x["confidence"]) for x in recs] == [(1, 10, 110, "0.91"), (3, 300, 500, "0.55")]
    assert all(set(x["mask"]) == {"rows", "cols", "encoding", "payload_chars"} for x in recs)   # the payload itself is never kept or decoded
    assert S.parse_stream(FIX["cases"]["no_confidence"]["events"], W, H)["frames"][0][0]["confidence"] is None


def test_a_payload_may_contain_pipe_and_greater_than_characters_without_ending_the_token_early():
    ev = [{"type": "response.output_text.delta", "delta": "<0f>1<|box;x1=1;y1=1;x2=9;y2=9;w=640;h=480|><|mask;x=0;y=0;data=2,2,!a|>b|c>d|>,2<|box;x1=2;y1=2;x2=8;y2=8;w=640;h=480|><|mask;x=0;y=0;data=1,1,~zz|>"},
          {"type": "response.completed"}]
    r = S.parse_stream(ev, W, H)
    assert r["state"] == "PARSED_OBJECTS" and [x["object_id"] for x in r["frames"][0]] == [1, 2]


def test_zero_matches_is_reported_as_no_match_not_as_safe():
    r = S.parse_stream([{"type": "response.completed"}], W, H)
    assert r["state"] == "PARSED_NO_MATCH_REPORTED" and r["frames"] == {}


def test_the_stream_parser_does_not_accept_lookalike_event_types_or_a_late_failure():
    ok = FIX["cases"]["two_objects"]["events"]
    assert S.parse_stream(ok + [{"type": "response.failed"}], W, H)["state"] == "UNKNOWN"
    assert S.parse_stream(ok + [{"type": "error"}], W, H)["reason"] == "error"
    assert S.parse_stream([{"type": "response.output_text.delta.extra", "delta": "<0f>"}] + ok[-1:], W, H)["state"] == "PARSED_NO_MATCH_REPORTED"   # unknown event types carry no data
    assert S.parse_stream("nope", W, H)["state"] == "UNKNOWN"


def _rec(d, **kw):
    r = {"approved_by": "owner", "approved_utc": "2026-11-02T10:00:00Z", "expires_utc": "2026-11-02T12:00:00Z", "body_digest": d, "max_calls": 1, "max_usd": "0.01", "retries": 0,
         "provider_side_limit_confirmed": True, "key_is_dedicated": True, "revocation_planned": True, "image_rights_confirmed": True, "image_url_is_nonpersonal": True,
         "image_url_expires_or_is_public_by_design": True, "provider": "meta_sam31", "model": "sam-3.1"}
    r.update(kw)
    return r


def test_authorization_record_must_be_complete_exact_and_unexpired():
    d = S.preview("https://example.com/a.png", "excavator")["body_digest"]
    est, now = "0.003125", "2026-11-02T11:00:00Z"
    assert S.check_owner_authorization(_rec(d), d, est, now) == []
    assert S.check_owner_authorization(None, d, est, now) == ["record_missing_or_not_an_object"]
    assert "missing:max_usd" in S.check_owner_authorization({k: v for k, v in _rec(d).items() if k != "max_usd"}, d, est, now)
    for edit, code in [({"body_digest": "0" * 64}, "body_digest_mismatch"), ({"max_calls": 2}, "max_calls_must_be_1"), ({"max_calls": True}, "max_calls_must_be_1"), ({"retries": 1}, "retries_must_be_0"),
                       ({"max_usd": "0.001"}, "max_usd_outside_estimate_to_0.05"), ({"max_usd": "5"}, "max_usd_outside_estimate_to_0.05"), ({"max_usd": "abc"}, "max_usd_invalid"),
                       ({"provider": "baseten"}, "provider_or_model_mismatch"), ({"model": "muse-spark-1.3"}, "provider_or_model_mismatch"),
                       ({"provider_side_limit_confirmed": False}, "not_confirmed:provider_side_limit_confirmed"), ({"key_is_dedicated": "yes"}, "not_confirmed:key_is_dedicated"),
                       ({"revocation_planned": False}, "not_confirmed:revocation_planned"), ({"image_rights_confirmed": False}, "not_confirmed:image_rights_confirmed"),
                       ({"image_url_expires_or_is_public_by_design": False}, "not_confirmed:image_url_expires_or_is_public_by_design"),
                       ({"approved_by": " "}, "approved_by_empty"), ({"expires_utc": "2026-11-02T10:30:00Z"}, "expired_or_invalid_expiry")]:
        assert code in S.check_owner_authorization(_rec(d, **edit), d, est, now), edit


def test_contract_statuses_never_overstate_the_documentation():
    by = {f: s for f, _, s, _ in S.CONTRACT}
    assert by["input_image base64 / data URL"] == "UNKNOWN" and by["input_image file_id"] == "UNKNOWN" and by["credits / spend cap / alerts / prepaid"] == "UNKNOWN"
    assert by["usage (SAM)"] == "UNKNOWN" and by["failed requests billed?"] == "UNKNOWN" and by["SSE event JSON envelope"] == "ASSUMED"
    assert by["box corner inclusivity (output)"] == "ASSUMED" and by["metadata.include_confidence"] == "VERIFIED"
    assert S.MAX_CALLS_FIRST_LIVE == 1 and S.RETRIES_FIRST_LIVE == 0


def test_the_digest_the_owner_authorizes_is_the_governors_own_digest_even_for_values_a_naive_hash_would_treat_differently():
    for x in ({"a": 1.5}, {"a": float("nan")}, {"a": float("inf")}, {"b": [1, {"c": 2.25}]}, {"model": "sam-3.1", "n": 1}):
        assert S.digest(x) == costgov.raw_digest(x)
    assert S.digest({"a": float("nan")}) != __import__("hashlib").sha256(S.canonical({"a": float("nan")}).encode()).hexdigest()
