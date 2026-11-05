"""MG-EVIDENCE-10: import and review of an EXTERNAL SAM 3.1 result. Offline only, deterministic. Every stream below is SYNTHETIC test data written from the documented grammar; none was
produced by Meta. The `DECLARED_EXTERNAL_API_RESPONSE` class is exercised only to prove the code path and its refusals -- these tests do NOT demonstrate a genuine provider result."""
import ast
import base64
import hashlib
import json
import re
import socket
from pathlib import Path

import pytest

from conftest import local_client
from mineguard import api_external as ae
from mineguard import meta_import as mi
from mineguard.app import create_app

HERE = Path(__file__).resolve().parents[1] / "mineguard"
W, H = 320, 240


def sse(*events) -> bytes:
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e, separators=(',', ':'))}\n\n" for e in events).encode()


def delta(*recs, frame=0):
    return {"type": "response.output_text.delta", "delta": f"<{frame}f>" + ",".join(recs)}


def rec(i=1, x1=40, y1=90, x2=230, y2=200, c="0.91", w=W, h=H, data="4,5,!aB|cD"):
    cc = "" if c is None else f";c={c}"
    return f"{i}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={w};h={h}{cc}|><|mask;x=0;y=0{cc};data={data}|>"


DONE = {"type": "response.completed"}
GOOD = sse({"type": "response.created"}, delta(rec(1), rec(2, 250, 60, 300, 120, "0.55")), DONE)


def manifest(result: bytes, **over) -> bytes:
    m = {"schema": mi.MANIFEST_SCHEMA, "provenance_class": mi.CLASS_SYNTHETIC, "fixture_label": mi.CLASS_SYNTHETIC, "provider": "meta_sam31", "model": "sam-3.1", "concept": "truck",
         "captured_utc": "2026-11-02T00:00:00Z", "declared_by": "unit test",
         "source_image": {"sha256": "ab" * 32, "width": W, "height": H},
         "response": {"sha256": hashlib.sha256(result).hexdigest(), "bytes": len(result), "http_status": 200, "content_type": "text/event-stream"}}
    m.update(over)
    for k in [k for k, v in m.items() if v is None]:
        del m[k]
    return json.dumps(m, sort_keys=True).encode()


def build(result=GOOD, **over):
    return mi.build_review(result, manifest(result, **over))


def refused(result, code, **over):
    with pytest.raises(mi.ImportRefused) as e:
        mi.build_review(result, manifest(result, **over))
    assert e.value.code == code


# ---- acceptance of a valid fixture ----------------------------------------------------------------------------------------------------------------------------------
def test_valid_fixture_is_accepted_and_labelled_external_synthetic_and_not_live():
    r = build()
    assert r["origin"] == "EXTERNAL_MANUAL_PROVIDER_RESULT" and r["in_app_inference"] is False and r["live_verified"] is False and r["attributed_to_local_detector"] is False
    assert r["verification"]["state"] == "SYNTHETIC_FIXTURE" and r["provider"] == "meta_sam31" and r["model"] == "sam-3.1"
    assert r["verification"]["result_sha256"] == hashlib.sha256(GOOD).hexdigest() and r["verification"]["input_image_sha256"] == "ab" * 32
    assert r["parse"]["state"] == "PARSED_OBJECTS" and [o["object_id"] for o in r["objects"]] == [1, 2]
    assert any("NOT produced by any Meta model" in s for s in r["limitations"])


def test_build_is_deterministic():
    assert build() == build() and build()["digest"] == build()["digest"]


def test_bundled_demo_fixture_verifies_against_its_image():
    res, man, img = ae.demo_files()
    r = mi.build_review(res, man, img)
    assert r["verification"]["image_linkage"] == "VERIFIED_BY_IMAGE_BYTES" and r["verification"]["state"] == "SYNTHETIC_FIXTURE" and len(r["objects"]) == 2


# ---- identity and provenance ----------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("over", [{"model": "sam-3"}, {"model": "sam-3.1 "}, {"provider": "meta_sam3"}, {"provider": "baseten"}, {"model": None}])
def test_wrong_or_missing_model_identity_is_rejected(over):
    with pytest.raises(mi.ImportRefused) as e:
        mi.build_review(GOOD, manifest(GOOD, **over))
    assert e.value.code in ("model_identity_mismatch", "manifest_unknown_or_missing_fields")


def test_hash_and_size_mismatches_are_rejected():
    refused(GOOD, "result_hash_mismatch", response={"sha256": "00" * 32, "bytes": len(GOOD), "http_status": 200, "content_type": "text/event-stream"})
    refused(GOOD, "result_size_mismatch", response={"sha256": hashlib.sha256(GOOD).hexdigest(), "bytes": len(GOOD) + 1, "http_status": 200, "content_type": "text/event-stream"})
    flipped = GOOD.replace(b"0.91", b"0.92")
    with pytest.raises(mi.ImportRefused) as e:
        mi.build_review(flipped, manifest(GOOD))            # manifest declares the ORIGINAL bytes
    assert e.value.code == "result_hash_mismatch"


def test_image_linkage_is_verified_when_the_image_is_supplied():
    res, man, img = ae.demo_files()
    for bad, code in ((img + b"\x00", "image_hash_mismatch"), (b"not an image", "image_unsupported")):
        with pytest.raises(mi.ImportRefused) as e:
            mi.build_review(res, man, bad)
        assert e.value.code == code
    m = json.loads(man)
    m["source_image"]["width"] = 321
    with pytest.raises(mi.ImportRefused) as e:                # declared size disagrees with the image bytes (hash is right)
        mi.build_review(res, json.dumps(m).encode(), img)
    assert e.value.code == "image_dimensions_mismatch"


@pytest.mark.parametrize("over,code", [
    ({"provenance_class": "REAL"}, "provenance_class_invalid"),
    ({"fixture_label": None}, "manifest_unknown_or_missing_fields"),
    ({"fixture_label": "REAL"}, "synthetic_label_missing"),
    ({"extra": 1}, "manifest_unknown_or_missing_fields"),
    ({"schema": "mg.meta.import.manifest/2"}, "manifest_schema_unsupported"),
    ({"concept": "../../etc/passwd"}, "manifest_field_invalid"),
    ({"concept": "a,b"}, "manifest_field_invalid"),
    ({"captured_utc": "yesterday"}, "manifest_field_invalid"),
    ({"source_image": {"sha256": "zz", "width": W, "height": H}}, "manifest_field_invalid"),
    ({"source_image": {"sha256": "ab" * 32, "width": 0, "height": H}}, "manifest_field_invalid"),
])
def test_manifest_is_strict(over, code):
    refused(GOOD, code, **over)


def test_manifest_rejects_http_status_content_type_duplicates_and_garbage():
    refused(GOOD, "manifest_field_invalid", response={"sha256": hashlib.sha256(GOOD).hexdigest(), "bytes": len(GOOD), "http_status": 500, "content_type": "text/event-stream"})
    refused(GOOD, "manifest_field_invalid", response={"sha256": hashlib.sha256(GOOD).hexdigest(), "bytes": len(GOOD), "http_status": 200, "content_type": "application/json"})
    for raw, code in ((b'{"schema":"x","schema":"y"}', "manifest_not_json"), (b"[1]", "manifest_not_an_object"), (b"\xff\xfe", "manifest_not_json"), (b"x" * 20000, "manifest_too_large")):
        with pytest.raises(mi.ImportRefused) as e:
            mi.build_review(GOOD, raw)
        assert e.value.code == code


def test_declared_real_class_requires_a_request_digest_and_never_accepts_bundled_synthetic_bytes():
    res, man, _ = ae.demo_files()
    declared = dict(json.loads(man), provenance_class=mi.CLASS_DECLARED, request_body_sha256="cd" * 32)
    declared.pop("fixture_label")
    with pytest.raises(mi.ImportRefused) as e:
        mi.build_review(res, json.dumps(declared).encode(), synthetic_digests=ae.synthetic_digests())
    assert e.value.code == "synthetic_bytes_declared_real"
    no_req = {k: v for k, v in declared.items() if k != "request_body_sha256"}
    with pytest.raises(mi.ImportRefused) as e:
        mi.build_review(res, json.dumps(no_req).encode())
    assert e.value.code == "manifest_unknown_or_missing_fields"


def test_declared_class_reaches_only_hashes_verified_never_a_live_state():
    other = sse(delta(rec(7, 5, 5, 60, 60, "0.80")), DONE)
    m = dict(json.loads(manifest(other)), provenance_class=mi.CLASS_DECLARED, request_body_sha256="cd" * 32)
    del m["fixture_label"]
    r = mi.build_review(other, json.dumps(m).encode(), synthetic_digests=ae.synthetic_digests())
    assert r["verification"]["state"] == "HASHES_VERIFIED_AUTHENTICITY_NOT_PROVEN" and r["verification"]["authenticity"] == "NOT_PROVEN_NO_PROVIDER_SIGNATURE"
    assert r["live_verified"] is False and r["in_app_inference"] is False and r["origin"] == "EXTERNAL_MANUAL_PROVIDER_RESULT"
    blob = json.dumps(r)
    assert "LIVE_VERIFIED" not in blob.replace('"live_verified"', "") and "IN_APP_INFERENCE_VERIFIED" not in blob and "REAL_META_RESULT_IMPORTED" not in blob


# ---- malformed / truncated / unsupported streams --------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("name,raw,code", [
    ("truncated_no_final_blank_line", GOOD[:-1], "result_not_a_complete_stream"),
    ("truncated_mid_event", GOOD[: len(GOOD) // 2], "result_not_a_complete_stream"),
    ("no_completed_event", sse(delta(rec())), "result_stream_not_parsed"),
    ("empty", b"", "manifest_field_invalid"),   # a manifest cannot declare 0 bytes; parse_sse itself also refuses it (below)
    ("not_utf8", b"data: \xff\n\n", "result_not_utf8"),
    ("nul_byte", b"data: {\"type\":\"a\"}\x00\n\n", "result_has_control_characters"),
    ("comment_line", b": keepalive\n\n" + GOOD, "result_unsupported_framing"),
    ("id_field", b"id: 1\ndata: {\"type\":\"response.completed\"}\n\n", "result_unsupported_framing"),
    ("two_data_lines", b"data: {\"type\":\"a\"}\ndata: {\"type\":\"b\"}\n\n", "result_unsupported_framing"),
    ("event_without_data", b"event: response.completed\n\n", "result_unsupported_framing"),
    ("data_not_json", b"data: nope\n\n", "result_event_not_json"),
    ("data_not_object", b"data: [1]\n\n", "result_event_without_type"),
    ("event_name_disagrees", b"event: response.created\ndata: {\"type\":\"response.completed\"}\n\n", "result_event_name_disagrees"),
    ("duplicate_json_keys", b"data: {\"type\":\"a\",\"type\":\"response.completed\"}\n\n", "result_event_not_json"),
    ("provider_failed", sse({"type": "response.failed"}), "result_provider_failure_event"),
    ("provider_error", sse(delta(rec()), {"type": "error"}, DONE), "result_provider_failure_event"),
    ("bad_grammar", sse(delta("1<|box;x1=1|>"), DONE), "result_stream_not_parsed"),
    ("mask_missing", sse(delta("1<|box;x1=1;y1=1;x2=9;y2=9;w=320;h=240|>"), DONE), "result_stream_not_parsed"),
    ("no_frame_marker", sse({"type": "response.output_text.delta", "delta": rec()}, DONE), "result_stream_not_parsed"),
    ("frame_outside_request", sse(delta(rec(), frame=3), DONE), "result_stream_not_parsed"),
    ("duplicate_frame", sse(delta(rec()), delta(rec()), DONE), "result_stream_not_parsed"),
    ("duplicate_object_id", sse(delta(rec(1), rec(1, 5, 5, 9, 9)), DONE), "result_stream_not_parsed"),
    ("frame_size_disagrees", sse(delta(rec(w=640)), DONE), "result_stream_not_parsed"),
    ("box_outside_image", sse(delta(rec(x2=321)), DONE), "result_stream_not_parsed"),
    ("box_inverted", sse(delta(rec(x1=100, x2=50)), DONE), "result_stream_not_parsed"),
    ("box_y_outside_image", sse(delta(rec(y2=241)), DONE), "result_stream_not_parsed"),
    ("confidence_above_one", sse(delta(rec(c="1.5")), DONE), "result_confidence_invalid"),
    ("confidence_mismatch_box_mask", sse({"type": "response.output_text.delta", "delta": "<0f>1<|box;x1=1;y1=1;x2=9;y2=9;w=320;h=240;c=0.5|><|mask;x=0;y=0;c=0.6;data=4,5,!ab|cd|>"}, DONE), "result_stream_not_parsed"),
    ("mask_zero_rows", sse(delta(rec(data="0,5,!ab")), DONE), "result_stream_not_parsed"),
    ("mask_unknown_encoding", sse(delta(rec(data="4,5,?ab")), DONE), "result_stream_not_parsed"),
    ("mask_huge_side", sse(delta(rec(data="99999,5,!ab")), DONE), "result_mask_structure_invalid"),
])
def test_malformed_truncated_or_unsupported_results_are_rejected(name, raw, code):
    refused(raw, code)


def test_parse_sse_refuses_empty_input_directly():
    with pytest.raises(mi.ImportRefused) as e:
        mi.parse_sse(b"")
    assert e.value.code == "result_empty"


def test_too_many_events_and_oversize_are_rejected():
    refused(sse(*([{"type": "response.created"}] * (mi.MAX_EVENTS + 1)), DONE), "result_too_many_events")
    with pytest.raises(mi.ImportRefused) as e:
        mi.build_review(b"x" * (mi.MAX_RESULT_BYTES + 1), manifest(GOOD))
    assert e.value.code == "result_too_large"


def test_crlf_framing_is_accepted_but_lone_cr_is_not():
    crlf = GOOD.replace(b"\n", b"\r\n")
    assert build(crlf)["parse"]["state"] == "PARSED_OBJECTS"
    refused(GOOD.replace(b"0.91", b"0.9\r1", 1), "result_has_control_characters")


# ---- documented box / mask semantics --------------------------------------------------------------------------------------------------------------------------------
def test_boxes_follow_documented_semantics_and_are_never_clipped_or_reordered():
    r = build()
    assert r["objects"][0]["box"] == {"x1": 40, "y1": 90, "x2": 230, "y2": 200}
    assert r["objects"][1]["box"] == {"x1": 250, "y1": 60, "x2": 300, "y2": 120}
    assert [o["provider_score_permille"] for o in r["objects"]] == [910, 550] and all(o["provider_score_kind"] == "provider_uncalibrated" for o in r["objects"])
    edge = build(sse(delta(rec(x1=0, y1=0, x2=W, y2=H)), DONE))                   # edges inclusive up to the image size
    assert edge["objects"][0]["box"] == {"x1": 0, "y1": 0, "x2": W, "y2": H}


def test_missing_confidence_is_reported_as_not_reported_never_fabricated():
    o = build(sse(delta(rec(c=None)), DONE))["objects"][0]
    assert o["provider_score_permille"] is None and o["provider_score_kind"] == "not_reported"


def test_masks_are_structure_checked_but_never_decoded_or_rendered():
    m = build()["objects"][0]["mask"]
    assert m == {"status": "STRUCTURE_CHECKED_NOT_RENDERED", "reason": "RASTER_SEMANTICS_UNDOCUMENTED", "rows": 4, "cols": 5, "encoding": "one_bit", "payload_chars": 5}
    assert not any(k in m for k in ("pixels", "bits", "png", "data", "polygon"))
    assert build(sse(delta(rec(data="4,5,~xy")), DONE))["objects"][0]["mask"]["encoding"] == "lossless"


# ---- no invented class or risk --------------------------------------------------------------------------------------------------------------------------------------
def test_no_class_and_no_severity_is_invented():
    r = build()
    assert all(o["class_assigned"] is None and o["label_state"] == "UNCLASSIFIED" for o in r["objects"])
    assert r["concept_requested"] == "truck" and "label" not in r["objects"][0] and "cls" not in r["objects"][0]
    assert r["risk"] == {"assessed": False, "reason": "IMPORTED_EVIDENCE_NEVER_ENTERS_THE_RISK_KERNEL"}
    blob = json.dumps(r)
    for word in (r"\bRED\b", r"\bYELLOW\b", r"\bGREEN\b", "severity", "vehicle", "person"):
        assert not re.search(word, blob), word
    assert r["canonical_effect"] == "NONE"


def test_zero_match_stream_keeps_unknown_semantics():
    r = build(sse(DONE))
    assert r["parse"]["state"] == "PARSED_NO_MATCH_REPORTED" and r["objects"] == []
    assert any("absence of segments is not evidence of absence" in s for s in r["limitations"])


# ---- isolation from the canonical pipeline, the transport and secrets -----------------------------------------------------------------------------------------------------
def _imports(path: Path) -> set[str]:
    out = set()
    for n in ast.walk(ast.parse(path.read_text())):
        if isinstance(n, ast.ImportFrom):
            out |= {(n.module or "") if n.level == 0 else "." * n.level + (n.module or "")} | {"." * n.level + (n.module or "") + "." + a.name for a in n.names}
        elif isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
    return out


def test_importer_imports_no_transport_detector_risk_kernel_or_network_module():
    banned = ("sam31", "muse_open", "projection", "metahttp", "metaclient", "costgov", "detector", "risk", "meta_pipeline", "meta_obs", "analysis", "alerts", "feeds", "http", "urllib", "socket", "requests", "httpx", "os.environ")
    for f in ("meta_import.py", "api_external.py"):
        for imp in _imports(HERE / f):
            assert not any(re.search(rf"(^|[.]){b}($|[.])", imp) for b in banned), (f, imp)
        used = {n.attr for n in ast.walk(ast.parse((HERE / f).read_text())) if isinstance(n, ast.Attribute)} | {n.id for n in ast.walk(ast.parse((HERE / f).read_text())) if isinstance(n, ast.Name)}
        assert not used & {"environ", "getenv", "socket", "urlopen", "connect", "HTTPSConnection", "requests"}, (f, used)


@pytest.fixture
def server(tmp_path, monkeypatch):
    calls = []
    real = socket.socket.connect
    monkeypatch.setattr(socket.socket, "connect", lambda self, *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("network connect attempted")))
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "run_0123456789abcdef").mkdir()
    (runs / "run_0123456789abcdef" / "observations.json").write_text('{"canonical": true}')
    app = create_app(asset_dir=tmp_path / "assets", runs_dir=runs, detector_factory=lambda: (_ for _ in ()).throw(AssertionError("detector must not be created")))
    yield local_client(app), runs, calls
    assert calls == []


def snapshot(runs: Path) -> dict:
    return {str(p.relative_to(runs)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(runs.rglob("*")) if p.is_file() and "external_results" not in p.parts}


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def post(c, result, man, image=None):
    body = {"manifest_b64": b64(man), "result_b64": b64(result)}
    if image is not None:
        body["image_b64"] = b64(image)
    return c.post("/api/external-results", json=body)


def test_api_import_stores_source_bytes_intact_and_leaves_canonical_data_untouched(server):
    c, runs, _ = server
    before, res_before = snapshot(runs), c.get("/api/runs").json()
    res, man, img = ae.demo_files()
    r = post(c, res, man, img)
    assert r.status_code == 201, r.text
    rv = r.json()
    d = runs / "external_results" / rv["import_id"]
    assert (d / "result.bin").read_bytes() == res and (d / "manifest.json").read_bytes() == man and (d / "image.bin").read_bytes() == img
    assert hashlib.sha256((d / "result.bin").read_bytes()).hexdigest() == rv["verification"]["result_sha256"]
    assert rv["origin"] == "EXTERNAL_MANUAL_PROVIDER_RESULT" and rv["integrity"] == "VERIFIED" and rv["newly_imported"] is True
    assert snapshot(runs) == before and c.get("/api/runs").json() == res_before          # canonical RF-DETR runs unchanged
    assert not (d.parent.parent / "alerts").exists() and not (d.parent.parent / "feeds").exists()


def test_api_is_idempotent_and_serves_list_detail_and_the_verified_image(server):
    c, runs, _ = server
    res, man, img = ae.demo_files()
    a, b = post(c, res, man, img), post(c, res, man, img)
    assert (a.status_code, b.status_code, b.json()["newly_imported"]) == (201, 200, False) and a.json()["import_id"] == b.json()["import_id"]
    iid = a.json()["import_id"]
    lst = c.get("/api/external-results").json()
    assert lst["origin"] == "EXTERNAL_MANUAL_PROVIDER_RESULT" and [x["import_id"] for x in lst["results"]] == [iid] and lst["results"][0]["verification_state"] == "SYNTHETIC_FIXTURE"
    det = c.get(f"/api/external-results/{iid}").json()
    assert det["digest"] == a.json()["digest"] and det["has_image"] is True
    img_r = c.get(f"/api/external-results/{iid}/image")
    assert img_r.status_code == 200 and img_r.headers["content-type"] == "image/png" and hashlib.sha256(img_r.content).hexdigest() == det["verification"]["input_image_sha256"]


def test_api_demo_action_works_offline_and_is_idempotent(server):
    c, runs, _ = server
    a, b = c.post("/api/external-results/demo"), c.post("/api/external-results/demo")
    assert (a.status_code, b.status_code) == (201, 200) and a.json()["verification"]["state"] == "SYNTHETIC_FIXTURE" and a.json()["has_image"] is True


def test_api_refusals_store_nothing_and_never_echo_the_input(server):
    c, runs, _ = server
    res, man, img = ae.demo_files()
    for result, manifest_b, code in ((res[:-1], man, "result_hash_mismatch"), (b"hello SECRET-MARKER", manifest(b"hello SECRET-MARKER"), "result_not_a_complete_stream")):
        r = post(c, result, manifest_b)
        assert r.status_code == 422 and r.json() == {"msg_id": f"mg.ext.refusal.{code}", "code": code} and "SECRET-MARKER" not in r.text
    assert post(c, res, man, img + b"x").json()["code"] == "image_hash_mismatch"
    assert not (runs / "external_results").exists() or not any((runs / "external_results").iterdir())
    assert c.post("/api/external-results", json={"manifest_b64": "!!", "result_b64": "!!"}).status_code == 422
    assert c.post("/api/external-results", json={"manifest_b64": b64(man), "result_b64": b64(res), "path": "/etc/passwd"}).status_code == 422   # unknown members refused


def test_api_oversized_body_is_refused_before_parsing(server):
    c, _, _ = server
    r = c.post("/api/external-results", content=b"{" + b" " * (13 * 1024 * 1024) + b"}", headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_modified_stored_copy_is_detected_not_trusted(server):
    c, runs, _ = server
    res, man, img = ae.demo_files()
    iid = post(c, res, man, img).json()["import_id"]
    (runs / "external_results" / iid / "result.bin").write_bytes(res.replace(b"0.91", b"0.99"))
    d = c.get(f"/api/external-results/{iid}").json()
    assert d["integrity"] == "STORED_COPY_MODIFIED" and "objects" not in d
    assert c.get(f"/api/external-results/{iid}/image").status_code == 404
    assert c.get("/api/external-results").json()["results"][0]["integrity"] == "STORED_COPY_MODIFIED"


def test_ids_and_routes_cannot_escape_the_store(server):
    c, _, _ = server
    for bad in ("..", "ext_../x", "ext_0123456789abcdeg", "run_0123456789abcdef", "ext_0123456789abcdef"):
        assert c.get(f"/api/external-results/{bad}").status_code == 404
    for m in ("PUT", "PATCH", "DELETE"):
        assert c.request(m, "/api/external-results").status_code in (404, 405)


def test_requests_without_the_session_token_are_refused(server):
    c, _, _ = server
    assert c.post("/api/external-results/demo", headers={"X-MG-Token": "wrong"}).status_code in (401, 403)


def test_no_provider_state_changes(server):
    c, _, _ = server
    before = c.get("/api/providers").json()
    c.post("/api/external-results/demo")
    after = c.get("/api/providers").json()
    assert before["cost"] == after["cost"] and after["invariants"]["external_calls_possible"] is False and "META_DISABLED" in {s for p in after["providers"] for s in p["states"]}


# ---- the self-contained parser is pinned to the Meta-stack parser (tests may import it; application modules may not) ------------------------------------------------------
def _stream_events(raw: bytes):
    try:
        return mi.parse_sse(raw)
    except mi.ImportRefused:
        return None


def test_self_contained_parser_agrees_with_the_meta_stack_parser_on_every_stream_case():
    from mineguard import sam31
    cases = [GOOD, sse(DONE), sse(delta(rec())), sse(delta(rec(c=None)), DONE), sse(delta(rec(x2=321)), DONE), sse(delta(rec(x1=100, x2=50)), DONE), sse(delta(rec(y2=241)), DONE),
             sse(delta(rec(w=640)), DONE), sse(delta(rec(1), rec(1, 5, 5, 9, 9)), DONE), sse(delta(rec()), delta(rec()), DONE), sse(delta(rec(), frame=3), DONE),
             sse({"type": "response.output_text.delta", "delta": rec()}, DONE), sse(delta("1<|box;x1=1|>"), DONE), sse(delta("1<|box;x1=1;y1=1;x2=9;y2=9;w=320;h=240|>"), DONE),
             sse(delta(rec(data="0,5,!ab")), DONE), sse(delta(rec(data="4,5,?ab")), DONE), sse(delta(rec(x1=0, y1=0, x2=W, y2=H)), DONE), sse(delta(rec(x1=7, y1=7, x2=7, y2=7)), DONE),
             sse({"type": "response.failed"}), sse(delta(rec()), {"type": "error"}, DONE), sse({"type": "response.output_text.delta", "delta": ""}, DONE),
             sse({"type": "response.output_text.delta", "delta": "<0f>1<|box;x1=1;y1=1;x2=9;y2=9;w=320;h=240;c=0.5|><|mask;x=0;y=0;c=0.6;data=4,5,!ab|cd|>"}, DONE)]
    for raw in cases:
        ev = _stream_events(raw)
        mine, theirs = mi.parse_wire(ev, W, H), sam31.parse_stream(ev, W, H, max_frames=1)
        assert (mine["state"], mine["reason"], mine["frames"]) == (theirs["state"], theirs["reason"], theirs["frames"]), raw


def test_self_contained_image_facts_agree_with_the_meta_stack_on_png_jpeg_and_garbage():
    from mineguard import sam31
    res, man, png = ae.demo_files()
    jpeg = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffc0000b080010002001011100ffd9")      # SOF0 header declaring 32x16
    for data in (png, jpeg, b"", b"GIF89a", png[:20], b"\xff\xd8\xff", png[:16] + (5000).to_bytes(4, "big") + png[20:]):
        try:
            theirs = sam31.image_facts(data)
        except sam31.Refused:
            theirs = None
        assert mi.image_facts(data) == theirs


# ---- MG-META-IMPORT-10-R1 / C5: closed SSE grammar (unknown events, unknown members, order) -------------------------------------------------------------------------
DECL = dict(provenance_class=mi.CLASS_DECLARED, fixture_label=None, request_body_sha256="cd" * 32)
_GOOD_EVENTS = [{"type": "response.created"}, delta(rec(1, c="0.92"), rec(2, 250, 60, 300, 120, "0.55")), DONE]    # NOT byte-identical to the bundled synthetic stream


def _declared(events):
    raw = sse(*events)
    return raw, manifest(raw, **DECL)


def _refused_declared(events, code):
    raw, man = _declared(events)
    with pytest.raises(mi.ImportRefused) as e:
        mi.build_review(raw, man)
    assert e.value.code == code


def test_c5_exact_codex_reproducer_is_refused_in_both_variants_and_via_the_api(server):
    c, runs, _ = server
    a = [dict(e) for e in _GOOD_EVENTS]
    a[0]["severity"], a[0]["path"] = "RED", "../../canonical/runs"                                     # case A: extra members on response.created
    b = [{"type": "attacker.control", "severity": "RED", "payload": "unexpected"}, *_GOOD_EVENTS]       # case B: arbitrary event in front of a valid stream
    before = snapshot(runs)
    for events, code in ((a, "result_unknown_event_field"), (b, "result_unknown_event_type")):
        _refused_declared(events, code)
        raw, man = _declared(events)
        r = post(c, raw, man)
        assert r.status_code == 422 and r.json() == {"msg_id": f"mg.ext.refusal.{code}", "code": code}
        assert "integrity" not in r.text and "VERIFIED" not in r.text and "RED" not in r.text.replace("REDACTED", "")
    assert not (runs / "external_results").exists() or not list((runs / "external_results").iterdir())      # nothing, not even a temp directory
    assert snapshot(runs) == before


@pytest.mark.parametrize("junk", ["id: 7", "retry: 100", ": comment", "x-custom: 1", "data: {}", "event: response.created"])
def test_unknown_or_repeated_sse_fields_are_refused(junk):
    raw = (b"event: response.created\ndata: {\"type\":\"response.created\"}\n" + junk.encode() + b"\n\n" + sse(*_GOOD_EVENTS[1:]))
    refused(raw, "result_unsupported_framing")


@pytest.mark.parametrize("etype", ["attacker.control", "response.in_progress", "response.output_text.done", "response.reasoning.delta", "response.incomplete", "", "RESPONSE.COMPLETED", "response.created "])
def test_arbitrary_event_types_are_refused_wherever_they_appear(etype):
    for pos in (0, 1, 3):
        events = list(_GOOD_EVENTS)
        events.insert(pos, {"type": etype})
        _refused_declared(events, "result_unknown_event_type")


@pytest.mark.parametrize("idx,extra", [(0, {"severity": "RED"}), (1, {"logprobs": []}), (1, {"content_index": 0}), (2, {"response": {"id": "x"}}), (2, {"usage": {}})])
def test_unknown_members_of_supported_events_are_refused(idx, extra):
    events = [dict(e) for e in _GOOD_EVENTS]
    events[idx].update(extra)
    _refused_declared(events, "result_unknown_event_field")


@pytest.mark.parametrize("bad", [{"type": "response.output_text.delta"}, {"type": "response.output_text.delta", "delta": None}, {"type": "response.output_text.delta", "delta": 5},
                                 {"type": "response.output_text.delta", "delta": ["<0f>"]}])
def test_a_delta_without_a_text_delta_is_refused(bad):
    _refused_declared([{"type": "response.created"}, bad, DONE], "result_unknown_event_field")


@pytest.mark.parametrize("name,events", [
    ("duplicate_terminal", [*_GOOD_EVENTS, DONE]),
    ("event_after_terminal", [*_GOOD_EVENTS, {"type": "response.created"}]),
    ("delta_after_terminal", [*_GOOD_EVENTS, delta(rec(3))]),
    ("terminal_first", [DONE, *_GOOD_EVENTS[:-1]]),
    ("terminal_in_the_middle", [_GOOD_EVENTS[0], DONE, _GOOD_EVENTS[1], DONE]),
    ("created_twice", [{"type": "response.created"}, *_GOOD_EVENTS]),
    ("created_after_data", [_GOOD_EVENTS[1], {"type": "response.created"}, DONE]),
    ("no_terminal", _GOOD_EVENTS[:-1]),
])
def test_unsupported_event_order_and_duplicate_terminal_events_are_refused(name, events):
    raw, man = _declared(events)
    with pytest.raises(mi.ImportRefused) as e:
        mi.build_review(raw, man)
    assert e.value.code in ("result_event_order_invalid", "result_stream_not_parsed"), (name, e.value.code)
    if name != "no_terminal":
        assert e.value.code == "result_event_order_invalid", name


def test_supported_stream_variants_are_still_accepted_control_case():
    ok = {
        "full": _GOOD_EVENTS,
        "without_created": _GOOD_EVENTS[1:],
        "zero_match": [{"type": "response.created"}, DONE],
        "empty_delta_lane": [{"type": "response.created"}, {"type": "response.output_text.delta", "delta": ""}, DONE],
        "only_terminal": [DONE],
    }
    for name, events in ok.items():
        raw, man = _declared(events)
        r = mi.build_review(raw, man)
        assert r["parse"]["state"] in ("PARSED_OBJECTS", "PARSED_NO_MATCH_REPORTED"), name
        assert {"id": "event_allowlist_and_order", "ok": True} in r["verification"]["checks"]
    assert len(mi.build_review(*_declared(_GOOD_EVENTS))["objects"]) == 2
    res, man, img = ae.demo_files()
    assert mi.build_review(res, man, img, synthetic_digests=ae.synthetic_digests())["verification"]["state"] == mi.ST_SYNTHETIC


def test_rejected_streams_never_leave_a_record_a_listing_or_a_verified_flag(server):
    c, runs, _ = server
    before = snapshot(runs)
    for events in ([{"type": "attacker.control"}, *_GOOD_EVENTS], [*_GOOD_EVENTS, DONE], [dict(_GOOD_EVENTS[0], x=1), *_GOOD_EVENTS[1:]]):
        for man_over in (DECL, {}):
            raw = sse(*events)
            r = post(c, raw, manifest(raw, **man_over))
            assert r.status_code == 422 and "integrity" not in r.json() and set(r.json()) == {"msg_id", "code"}
    assert c.get("/api/external-results").json()["results"] == []
    root = runs / "external_results"
    assert not root.exists() or list(root.iterdir()) == []
    assert snapshot(runs) == before                                                    # canonical (RF-DETR) run data byte-identical


def test_verification_semantics_integrity_is_byte_scope_only_and_authenticity_stays_unproven(server):
    c, runs, _ = server
    before = snapshot(runs)
    res, man, img = ae.demo_files()
    syn = post(c, res, man, img).json()
    assert syn["integrity"] == "VERIFIED" and syn["integrity_scope"] == ae.INTEGRITY_SCOPE == "STORED_BYTES_REVALIDATED_NOT_AUTHENTICITY"
    assert syn["verification"]["state"] == "SYNTHETIC_FIXTURE" and syn["provenance_class"] == "SYNTHETIC_FIXTURE" and syn["live_verified"] is False
    raw, dman = _declared(_GOOD_EVENTS)
    dec = post(c, raw, dman).json()
    assert dec["integrity"] == "VERIFIED" and dec["verification"]["state"] == "HASHES_VERIFIED_AUTHENTICITY_NOT_PROVEN"
    assert dec["verification"]["authenticity"] == "NOT_PROVEN_NO_PROVIDER_SIGNATURE" and dec["in_app_inference"] is False and dec["live_verified"] is False
    assert dec["risk"]["assessed"] is False and dec["canonical_effect"] == "NONE" and all(o["class_assigned"] is None for o in dec["objects"])
    assert {mi.ST_SYNTHETIC, mi.ST_HASHES_OK} == {syn["verification"]["state"], dec["verification"]["state"]}
    assert get_all_states_are_closed(c)
    assert snapshot(runs) == before


def get_all_states_are_closed(c) -> bool:
    rows = c.get("/api/external-results").json()["results"]
    return bool(rows) and all(r["verification_state"] in (mi.ST_SYNTHETIC, mi.ST_HASHES_OK) and r["integrity"] == "VERIFIED" for r in rows)


def test_a_stored_copy_swapped_for_an_unsupported_stream_is_not_shown_as_verified(server):
    c, runs, _ = server
    raw, man = _declared(_GOOD_EVENTS)
    iid = post(c, raw, man).json()["import_id"]
    bad = sse({"type": "attacker.control"}, *_GOOD_EVENTS)
    (runs / "external_results" / iid / "result.bin").write_bytes(bad)
    d = c.get(f"/api/external-results/{iid}").json()
    assert d["integrity"] == "STORED_COPY_MODIFIED" and "objects" not in d and "verification" not in d


# ---- MG-META-REAL-SSE-12: observed grammar profile (META_SAM31_OBSERVED_1) ---------------------------------------------------------------------------------------------
# The profile was derived from ONE owner-executed Meta response that is PRIVATE and not in Git. These tests use a SYNTHETIC stream with the same structure (fake ids, tiny mask, no provider
# data); one more test runs the exact private response when the file is present and is skipped otherwise.
import copy
import os

OW, OH = 640, 384
OBS_LINE = "<0f>0<|box;x1=40;y1=150;x2=559;y2=331;w=640;h=384|><|mask;x=0;y=0;data=3,4,~abc|>\n"
SRC_IMG = {"sha256": "ab" * 32, "width": OW, "height": OH}


def obs_events(text=OBS_LINE):
    rid, mid = "resp_" + "a1b2c3d4" * 3 + "e5f6", "msg_" + "0123abcd-" * 4 + "ef"[:0] + "0123"
    resp = {"background": False, "created_at": 1700000000.0, "error": None, "id": rid, "incomplete_details": None, "instructions": None, "model": "sam-3.1", "object": "response",
            "output": [], "parallel_tool_calls": True, "service_tier": "auto", "status": "in_progress", "store": True, "temperature": 1.0, "tool_choice": "auto", "tools": [],
            "top_logprobs": 0, "top_p": 1.0, "truncation": "disabled"}
    part = lambda t: {"annotations": [], "logprobs": [], "text": t, "type": "output_text"}
    item = lambda st, t: {"content": [part(t)], "id": mid, "role": "assistant", "status": st, "type": "message"}
    done_resp = {**copy.deepcopy(resp), "completed_at": 1700000001.5, "status": "completed", "output": [item("completed", text)],
                 "usage": {"images_processed": 1, "input_tokens": 0, "input_tokens_details": {"cached_tokens": 0}, "output_tokens": 0, "output_tokens_details": {"reasoning_tokens": 0}, "total_tokens": 0}}
    return [
        {"response": copy.deepcopy(resp), "sequence_number": 0, "type": "response.created"},
        {"response": copy.deepcopy(resp), "sequence_number": 1, "type": "response.in_progress"},
        {"item": item("in_progress", ""), "output_index": 0, "sequence_number": 2, "type": "response.output_item.added"},
        {"content_index": 0, "item_id": mid, "output_index": 0, "part": part(""), "sequence_number": 3, "type": "response.content_part.added"},
        {"content_index": 0, "delta": text, "item_id": mid, "logprobs": [], "output_index": 0, "sequence_number": 4, "type": "response.output_text.delta"},
        {"content_index": 0, "item_id": mid, "logprobs": [], "output_index": 0, "sequence_number": 5, "text": text, "type": "response.output_text.done"},
        {"content_index": 0, "item_id": mid, "output_index": 0, "part": part(text), "sequence_number": 6, "type": "response.content_part.done"},
        {"item": item("completed", text), "output_index": 0, "sequence_number": 7, "type": "response.output_item.done"},
        {"response": done_resp, "sequence_number": 8, "type": "response.completed"},
    ]


def obs_bytes(events=None, done=b"data: [DONE]\n\n"):
    ev = obs_events() if events is None else events
    return "".join(f"event: {e['type']}\ndata: {json.dumps(e, separators=(',', ':'))}\n\n" for e in ev).encode() + done


def obs_man(raw, declared=True, **over):
    if declared:
        over = {**DECL, **over}
    return manifest(raw, source_image=SRC_IMG, **over)


def obs_review(raw=None, **over):
    raw = obs_bytes() if raw is None else raw
    return mi.build_review(raw, obs_man(raw, **over))


def obs_refused(raw, code, **over):
    with pytest.raises(mi.ImportRefused) as e:
        mi.build_review(raw, obs_man(raw, **over))
    assert e.value.code == code, (e.value.code, code)


def mutated(path, value=None, delete=False):
    """Observed events with one value changed/deleted; path = [event index, key, key...]."""
    ev = obs_events()
    cur = ev[path[0]]
    for k in path[1:-1]:
        cur = cur[k]
    if delete:
        del cur[path[-1]]
    else:
        cur[path[-1]] = value
    return ev


def test_observed_synthetic_stream_is_accepted_with_profile_honestly_labelled():
    r = obs_review()
    assert r["parse"]["state"] == "PARSED_OBJECTS" and r["verification"]["state"] == "HASHES_VERIFIED_AUTHENTICITY_NOT_PROVEN"
    g = r["grammar"]
    assert g["profile"] == "META_SAM31_OBSERVED_1" and g["status"] == "OBSERVED_FROM_ONE_OWNER_EXECUTED_META_RESPONSE" and g["official_provider_schema"] is False and g["events"] == 9
    assert {"id": "observed_profile_schema", "ok": True} in r["verification"]["checks"]
    o = r["objects"][0]
    assert o["box"] == {"x1": 40, "y1": 150, "x2": 559, "y2": 331} and o["provider_score_permille"] is None and o["provider_score_kind"] == "not_reported"      # c= absent: nothing inferred
    assert o["class_assigned"] is None and o["label_state"] == "UNCLASSIFIED" and o["mask"]["status"] == "STRUCTURE_CHECKED_NOT_RENDERED"
    assert r["risk"]["assessed"] is False and r["canonical_effect"] == "NONE" and r["live_verified"] is False and r["in_app_inference"] is False and r["origin"] == "EXTERNAL_MANUAL_PROVIDER_RESULT"
    assert "abc" not in json.dumps(r) and "resp_" not in json.dumps(r) and "msg_" not in json.dumps(r)            # mask payload and provider ids are never carried into the review
    assert "grammar" not in build() and "grammar" not in mi.build_review(*ae.demo_files()[:2], synthetic_digests=ae.synthetic_digests())     # fixture records are unchanged


def test_observed_sentinel_selects_the_profile_only_and_fixture_grammar_is_unchanged():
    assert obs_review(provenance_class=mi.CLASS_SYNTHETIC, fixture_label=mi.CLASS_SYNTHETIC, request_body_sha256=None)["verification"]["state"] == "SYNTHETIC_FIXTURE"
    assert mi.split_done_sentinel(GOOD) is None
    # the observed stream WITHOUT its sentinel is judged by the fixture grammar and refused: the profile is never inferred from event names
    obs_refused(obs_bytes(done=b""), "result_unknown_event_field")
    # a fixture-grammar stream with a sentinel is judged by the observed profile and refused
    obs_refused(GOOD + b"data: [DONE]\n\n", "result_unknown_event_field")


@pytest.mark.parametrize("raw_fn", [
    lambda: obs_bytes(done=b"data: [DONE]\n\ndata: [DONE]\n\n"),                        # duplicate
    lambda: obs_bytes() + b"data: trailing\n\n",                                       # content after the sentinel
    lambda: obs_bytes() + b"\n",                                                       # extra blank line after it
    lambda: obs_bytes(done=b"event: done\ndata: [DONE]\n\n"),                         # sentinel with an event field
    lambda: obs_bytes(done=b"data: [DONE]\r\n\r\n"),                                  # CRLF sentinel
    lambda: obs_bytes(done=b"data: [DONE]\n"),                                        # no terminating blank line
    lambda: obs_bytes(done=b"data: [DONE]"),                                          # truncated sentinel
    lambda: b"data: [DONE]\n\n" + obs_bytes(),                                         # sentinel first
])
def test_misplaced_duplicate_or_malformed_sentinels_are_refused(raw_fn):
    raw = raw_fn()
    with pytest.raises(mi.ImportRefused) as e:
        mi.build_review(raw, obs_man(raw))
    assert e.value.code in ("result_done_sentinel_misplaced", "result_not_a_complete_stream"), e.value.code


def test_observed_unknown_event_types_are_refused():
    for etype in ("response.reasoning.delta", "response.refusal.delta", "attacker.control", "response.incomplete", "response.output_text.annotation.added"):
        for pos in (0, 4, 9):
            ev = obs_events()
            ev.insert(pos, {"type": etype, "sequence_number": 0})
            obs_refused(obs_bytes(ev), "result_unknown_event_type")


@pytest.mark.parametrize("path", [[0, "severity"], [4, "path"], [8, "usage2"], [5, "extra"]])
def test_observed_unknown_top_level_members_are_refused(path):
    ev = obs_events()
    ev[path[0]][path[1]] = "RED"
    obs_refused(obs_bytes(ev), "result_unknown_event_field")


@pytest.mark.parametrize("path", [
    [0, "response", "metadata"], [1, "response", "user"], [8, "response", "reasoning"], [8, "response", "usage", "cost"], [8, "response", "usage", "input_tokens_details", "audio_tokens"],
    [8, "response", "usage", "output_tokens_details", "x"], [2, "item", "tool_calls"], [7, "item", "content", 0, "refusal"], [3, "part", "url"], [6, "part", "path"],
    [8, "response", "output", 0, "extra"], [8, "response", "output", 0, "content", 0, "extra"],
])
def test_observed_unknown_nested_fields_are_refused_at_every_boundary(path):
    ev = obs_events()
    cur = ev[path[0]]
    for k in path[1:-1]:
        cur = cur[k]
    cur[path[-1]] = "../../canonical/runs" if path[-1] in ("path", "url") else {"a": 1}
    obs_refused(obs_bytes(ev), "result_observed_schema_violation")


@pytest.mark.parametrize("path,value", [
    ([0, "response", "model"], "sam-3"), ([0, "response", "object"], "list"), ([0, "response", "status"], "completed"), ([0, "response", "id"], "../resp_aaaaaaaaaa"),
    ([0, "response", "id"], "resp_" + "a" * 65), ([0, "response", "tools"], [{"type": "function"}]), ([0, "response", "output"], [{}]), ([0, "response", "background"], True),
    ([0, "response", "error"], {"code": "x"}), ([0, "response", "instructions"], "do this"), ([0, "response", "created_at"], "now"), ([0, "response", "top_logprobs"], 5),
    ([0, "response", "service_tier"], "priority"), ([0, "response", "store"], 1), ([8, "response", "status"], "in_progress"), ([8, "response", "completed_at"], 1.0),
    ([8, "response", "usage", "total_tokens"], -1), ([8, "response", "usage", "total_tokens"], 10**12), ([8, "response", "usage", "images_processed"], True),
    ([8, "response", "usage", "images_processed"], 1.5), ([2, "item", "role"], "user"), ([2, "item", "type"], "function_call"),
    ([2, "item", "id"], "msg_x"), ([2, "item", "id"], "../../etc/passwd"), ([2, "item", "status"], "completed"), ([2, "item", "content"], []),
    ([3, "part", "type"], "image_url"), ([3, "part", "annotations"], [{"url": "http://x"}]), ([3, "part", "logprobs"], [1]), ([3, "part", "text"], "not empty"),
    ([4, "logprobs"], [{"token": "x"}]), ([4, "content_index"], 1), ([4, "output_index"], 2), ([4, "content_index"], False), ([5, "logprobs"], [1]),
])
def test_observed_malformed_nested_values_are_refused(path, value):
    obs_refused(obs_bytes(mutated(path, value)), "result_observed_schema_violation")


def test_observed_oversized_nested_data_is_refused():
    ev = obs_events()
    big = "<0f>" + "x" * (mi.MAX_OBSERVED_TEXT + 1)
    ev[4]["delta"] = ev[5]["text"] = ev[6]["part"]["text"] = ev[7]["item"]["content"][0]["text"] = ev[8]["response"]["output"][0]["content"][0]["text"] = big
    raw = obs_bytes(ev)
    with pytest.raises(mi.ImportRefused):
        mi.build_review(raw, obs_man(raw))
    ev = obs_events()
    ev[2]["item"]["content"] = [ev[2]["item"]["content"][0]] * 1000
    obs_refused(obs_bytes(ev), "result_observed_schema_violation")
    deep = {"a": None}
    cur = deep
    for _ in range(2000):
        cur["a"] = {"a": None}
        cur = cur["a"]
    ev = obs_events()
    ev[0]["response"]["metadata"] = deep
    raw = obs_bytes(ev)
    with pytest.raises((mi.ImportRefused, RecursionError)):
        mi.build_review(raw, obs_man(raw))


def test_observed_event_order_violations_are_refused():
    base = obs_events()
    cases = {
        "swap_created_progress": [base[1], base[0], *base[2:]], "swap_delta_done": [*base[:4], base[5], base[4], *base[6:]], "missing_progress": [base[0], *base[2:]],
        "missing_item_added": [*base[:2], *base[3:]], "missing_delta": [*base[:4], *base[5:]], "missing_completed": base[:-1], "duplicate_delta": [*base[:5], base[4], *base[5:]],
        "duplicate_completed": [*base, base[-1]], "completed_first": [base[-1], *base[:-1]], "event_after_completed": [*base, base[0]], "reversed": base[::-1],
    }
    for name, ev in cases.items():
        raw = obs_bytes(ev)
        with pytest.raises(mi.ImportRefused) as e:
            mi.build_review(raw, obs_man(raw))
        assert e.value.code in ("result_observed_order_invalid", "result_unknown_event_type"), (name, e.value.code)
        assert e.value.code == "result_observed_order_invalid", name


@pytest.mark.parametrize("seqs", [[0, 1, 2, 3, 4, 5, 6, 8, 7], [1, 2, 3, 4, 5, 6, 7, 8, 9], [0, 1, 2, 3, 3, 5, 6, 7, 8], [0, 1, 2, 3, 4, 5, 6, 7, 7], [0, 1, 2, 3, 4, 6, 7, 8, 9], [8, 7, 6, 5, 4, 3, 2, 1, 0],
                                  [0, 0, 0, 0, 0, 0, 0, 0, 0], [-1, 0, 1, 2, 3, 4, 5, 6, 7]])
def test_observed_nonmonotonic_or_non_consecutive_sequence_numbers_are_refused(seqs):
    ev = obs_events()
    for e, n in zip(ev, seqs):
        e["sequence_number"] = n
    obs_refused(obs_bytes(ev), "result_observed_sequence_invalid")


@pytest.mark.parametrize("bad", [True, "3", 3.0, None])
def test_observed_sequence_numbers_must_be_integers(bad):
    ev = obs_events()
    ev[3]["sequence_number"] = bad
    obs_refused(obs_bytes(ev), "result_observed_sequence_invalid")


def test_observed_completed_response_without_the_output_is_refused():
    obs_refused(obs_bytes(mutated([8, "response", "output"], [])), "result_observed_text_mismatch")


@pytest.mark.parametrize("path", [[5, "text"], [6, "part", "text"], [7, "item", "content", 0, "text"], [8, "response", "output", 0, "content", 0, "text"]])
def test_observed_done_text_must_equal_the_delta(path):
    other = OBS_LINE.replace("x1=40", "x1=41")
    ev = obs_events()
    cur = ev[path[0]]
    for k in path[1:-1]:
        cur = cur[k]
    cur[path[-1]] = other
    obs_refused(obs_bytes(ev), "result_observed_text_mismatch")


def test_observed_identity_must_be_consistent_across_events():
    for path, v in (([3, "item_id"], "msg_" + "z" * 20), ([4, "item_id"], "msg_" + "z" * 20), ([6, "item_id"], "msg_" + "z" * 20), ([1, "response", "id"], "resp_" + "z" * 20),
                    ([8, "response", "id"], "resp_" + "z" * 20), ([8, "response", "created_at"], 1699999999.0)):
        obs_refused(obs_bytes(mutated(path, v)), "result_observed_identity_inconsistent")
    ev = obs_events()
    ev[8]["response"]["output"][0]["id"] = "msg_" + "z" * 20
    obs_refused(obs_bytes(ev), "result_observed_schema_violation")
    obs_refused(obs_bytes(mutated([8, "response", "completed_at"], 1.0)), "result_observed_schema_violation")


@pytest.mark.parametrize("line", [OBS_LINE[:-1], OBS_LINE + "\n", OBS_LINE[:-1] + "\r\n", OBS_LINE[:-1] + " \n", "\n" + OBS_LINE[:-1], OBS_LINE.replace("|><|mask", "|>\n<|mask")])
def test_observed_record_terminator_rule_is_exactly_one_trailing_lf(line):
    obs_refused(obs_bytes(obs_events(line)), "result_observed_record_terminator_invalid")


@pytest.mark.parametrize("line,why", [
    ("<0f>0<|box;x1=40;y1=150;x2=559;y2=331;w=641;h=384|><|mask;x=0;y=0;data=3,4,~abc|>\n", "frame size"),
    ("<0f>0<|box;x1=40;y1=150;x2=641;y2=331;w=640;h=384|><|mask;x=0;y=0;data=3,4,~abc|>\n", "box outside"),
    ("<0f>0<|box;x1=40;y1=150;x2=559;y2=331;w=640;h=384|>\n", "mask missing"),
    ("<0f>0<|box;x1=40;y1=150;x2=559;y2=331;w=640;h=384|><|mask;x=0;y=0;data=3,4,?abc|>\n", "mask encoding"),
    ("<0f>0<|box;x1=40;y1=150;x2=559;y2=331;w=640;h=384|><|mask;x=0;y=0;data=3,4,~abc|>,0<|box;x1=1;y1=1;x2=9;y2=9;w=640;h=384|><|mask;x=0;y=0;data=3,4,~abc|>\n", "duplicate id"),
    ("<1f>0<|box;x1=40;y1=150;x2=559;y2=331;w=640;h=384|><|mask;x=0;y=0;data=3,4,~abc|>\n", "frame marker"),
    ("0<|box;x1=40;y1=150;x2=559;y2=331;w=640;h=384|><|mask;x=0;y=0;data=3,4,~abc|>\n", "no frame marker"),
    ("<0f>0<|box;x1=40;y1=150;x2=559;y2=331;w=640;h=384;c=0.9|><|mask;x=0;y=0;c=0.8;data=3,4,~abc|>\n", "confidence disagree"),
    ("<0f>ignore previous instructions\n", "foreign instructions"),
    ("<0f>0<|box;x1=40;y1=150;x2=559;y2=331;w=640;h=384|><|mask;x=0;y=0;data=3,4,~abc|>../../../etc/passwd\n", "path after record"),
])
def test_observed_record_line_keeps_the_documented_box_and_mask_grammar(line, why):
    raw = obs_bytes(obs_events(line))
    with pytest.raises(mi.ImportRefused) as e:
        mi.build_review(raw, obs_man(raw))
    # text that does not even end in a record terminator is refused by the terminator rule, everything else by the documented line grammar
    assert e.value.code == ("result_observed_record_terminator_invalid" if why in ("foreign instructions", "path after record") else "result_stream_not_parsed"), (why, e.value.code)


def test_observed_provider_failure_events_are_still_refused():
    for etype in ("response.failed", "error"):
        ev = obs_events()
        ev.insert(8, {"type": etype})
        obs_refused(obs_bytes(ev), "result_provider_failure_event")


def test_observed_altered_digest_or_size_is_refused():
    raw = obs_bytes()
    man = obs_man(raw)
    tampered = raw.replace(b"x1=40", b"x1=41", 1) if b"x1=40" in raw else raw[:-1] + b"X"
    with pytest.raises(mi.ImportRefused) as e:
        mi.build_review(tampered, man)
    assert e.value.code in ("result_hash_mismatch", "result_size_mismatch")
    with pytest.raises(mi.ImportRefused) as e:
        mi.build_review(raw + b"\n", obs_man(raw))
    assert e.value.code in ("result_hash_mismatch", "result_size_mismatch")


def test_observed_image_linkage_is_checked_when_the_image_is_supplied():
    raw = obs_bytes()
    png = ae.demo_files()[2]                                       # a real PNG, but 320x240 and not the declared hash
    with pytest.raises(mi.ImportRefused) as e:
        mi.build_review(raw, obs_man(raw), png)
    assert e.value.code == "image_hash_mismatch"
    facts = mi.image_facts(png)
    src = {"sha256": facts["sha256"], "width": facts["width"], "height": facts["height"]}
    with pytest.raises(mi.ImportRefused) as e:                      # right image, but the stream's frame size (640x384) disagrees with it
        mi.build_review(raw, manifest(raw, source_image=src, **DECL), png)
    assert e.value.code == "result_stream_not_parsed"
    with pytest.raises(mi.ImportRefused) as e:
        mi.build_review(raw, manifest(raw, source_image={**src, "width": 321}, **DECL), png)
    assert e.value.code == "image_dimensions_mismatch"


def test_observed_rejections_persist_nothing_and_leave_canonical_data_untouched(server):
    c, runs, _ = server
    before = snapshot(runs)
    bad = [obs_bytes(mutated([0, "response", "metadata"], 1)), obs_bytes(done=b""), obs_bytes(obs_events(OBS_LINE[:-1])), obs_bytes() + b"data: x\n\n",
           obs_bytes(mutated([5, "text"], "other\n"))]
    for raw in bad:
        r = post(c, raw, obs_man(raw))
        assert r.status_code == 422 and set(r.json()) == {"msg_id", "code"} and "integrity" not in r.text
    assert c.get("/api/external-results").json()["results"] == []
    root = runs / "external_results"
    assert not root.exists() or list(root.iterdir()) == []
    assert snapshot(runs) == before


def test_observed_accepted_import_through_the_api_keeps_bytes_provenance_and_risk_authority(server):
    c, runs, _ = server
    before = snapshot(runs)
    raw = obs_bytes()
    r = post(c, raw, obs_man(raw))
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["integrity"] == "VERIFIED" and d["integrity_scope"] == ae.INTEGRITY_SCOPE and d["grammar"]["profile"] == "META_SAM31_OBSERVED_1"
    assert (runs / "external_results" / d["import_id"] / "result.bin").read_bytes() == raw                      # source bytes stored exactly, sentinel included
    assert d["verification"]["state"] == "HASHES_VERIFIED_AUTHENTICITY_NOT_PROVEN" and d["risk"]["assessed"] is False and d["canonical_effect"] == "NONE"
    text = json.dumps(d)
    assert not re.search(r"\b(RED|YELLOW|GREEN)\b", text) and "severity" not in text and d["objects"][0]["class_assigned"] is None
    assert "payload" not in json.dumps(d["objects"][0]["mask"]).replace("payload_chars", "")
    assert c.get(f"/api/external-results/{d['import_id']}").json()["digest"] == d["digest"]                    # re-derived from the stored bytes
    assert snapshot(runs) == before
    # the stored copy swapped for an unsupported stream is not shown as verified
    (runs / "external_results" / d["import_id"] / "result.bin").write_bytes(obs_bytes(mutated([0, "response", "metadata"], 1)))
    assert c.get(f"/api/external-results/{d['import_id']}").json()["integrity"] == "STORED_COPY_MODIFIED"


def test_observed_profile_cannot_promote_synthetic_bytes_to_a_real_result():
    # a bundled synthetic stream stays unpromotable; the observed profile does not change the label rules
    res, man, img = ae.demo_files()
    declared = manifest(res, source_image={"sha256": mi.image_facts(img)["sha256"], "width": 320, "height": 240}, **DECL)
    with pytest.raises(mi.ImportRefused) as e:
        mi.build_review(res, declared, img, synthetic_digests=ae.synthetic_digests())
    assert e.value.code == "synthetic_bytes_declared_real"
    raw = obs_bytes()
    with pytest.raises(mi.ImportRefused) as e:                      # declared real needs the request digest; a fixture label cannot ride along
        mi.build_review(raw, obs_man(raw, request_body_sha256=None))
    assert e.value.code == "manifest_unknown_or_missing_fields"
    with pytest.raises(mi.ImportRefused) as e:
        mi.build_review(raw, obs_man(raw, fixture_label="SYNTHETIC_FIXTURE"))
    assert e.value.code == "manifest_unknown_or_missing_fields"
    assert obs_review()["verification"]["state"] != "SYNTHETIC_FIXTURE"


def test_c5_counterexamples_still_refused_with_and_without_a_sentinel():
    for done in (b"", b"data: [DONE]\n\n"):
        for events, code in (([dict(_GOOD_EVENTS[0], severity="RED", path="../../canonical/runs"), *_GOOD_EVENTS[1:]], None),
                             ([{"type": "attacker.control", "severity": "RED", "payload": "unexpected"}, *_GOOD_EVENTS], None)):
            raw = sse(*events) + done
            with pytest.raises(mi.ImportRefused):
                mi.build_review(raw, manifest(raw, **DECL))


REAL_RESPONSE = Path(os.environ.get("MG_REAL_SSE_RESPONSE", "__private_genuine_response_not_distributed__"))
REAL_IMAGE = Path(os.environ.get("MG_REAL_SSE_IMAGE", "__private_genuine_source_image_not_distributed__"))
REAL_SHA = "698b0b6314780550ec41859bdabbdf1f19400612906155827fec7bf509bf3036"


@pytest.mark.skipif(not (REAL_RESPONSE.is_file() and REAL_IMAGE.is_file()), reason="private genuine response not present on this machine (it is deliberately not in Git)")
def test_exact_genuine_owner_executed_response_is_accepted_unchanged(server):
    c, runs, _ = server
    raw, img = REAL_RESPONSE.read_bytes(), REAL_IMAGE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == REAL_SHA and len(raw) == 165441
    facts = mi.image_facts(img)
    assert (facts["width"], facts["height"]) == (640, 384) and facts["sha256"] == "721c3589325faad0ec927da224caf9bfbad41b393a9222cae39f59575d1d2e1d"
    man = manifest(raw, source_image={"sha256": facts["sha256"], "width": 640, "height": 384}, concept="truck", declared_by="owner manual run", captured_utc="2026-11-02T23:24:00Z",
                   **{**DECL, "request_body_sha256": "5bdee92fa6ed36bb99c83d9e1ec7d172781746bd5617645e09c7e97d48a817f5"})
    before = snapshot(runs)
    r = post(c, raw, man, img)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["verification"]["state"] == "HASHES_VERIFIED_AUTHENTICITY_NOT_PROVEN" and d["verification"]["result_sha256"] == REAL_SHA and d["verification"]["image_linkage"] == "VERIFIED_BY_IMAGE_BYTES"
    assert d["origin"] == "EXTERNAL_MANUAL_PROVIDER_RESULT" and d["grammar"]["profile"] == "META_SAM31_OBSERVED_1" and d["live_verified"] is False and d["in_app_inference"] is False
    assert len(d["objects"]) == 1 and d["objects"][0]["box"] == {"x1": 40, "y1": 150, "x2": 559, "y2": 331}
    assert d["objects"][0]["provider_score_permille"] is None and d["objects"][0]["class_assigned"] is None and d["risk"]["assessed"] is False
    stored = (runs / "external_results" / d["import_id"] / "result.bin").read_bytes()
    assert stored == raw and hashlib.sha256(stored).hexdigest() == REAL_SHA
    assert snapshot(runs) == before
    assert hashlib.sha256(REAL_RESPONSE.read_bytes()).hexdigest() == REAL_SHA                   # the source evidence was not touched
