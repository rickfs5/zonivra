"""META LIVE GATE 07 — MG-F6-01 (structural output boundary) and the three surviving mutants V04, F2i, F4g.
The guarantee under test is STRUCTURAL: the transport returns a NEW structure built from contract-validated values (`projection`); a field the contract has no place for is dropped, so a
secret cannot leave through it however it is split or encoded. The leak scanner is a second, best-effort layer and is NOT claimed to be universal (see the last test of section B)."""
import base64
import json
from decimal import Decimal

import pytest

from mineguard import costgov, meta_obs, meta_pipeline, metaclient, metahttp, muse_open as M, projection as P, sam31 as S
from mineguard.costgov import Denied, SpendGovernor
from mineguard.metahttp import EndpointBoundGuard, TestHttpsTransport

import test_meta_provenance as TP
from test_meta_provenance import cfg, sam_setup, sam_request, minted, guard_for, _Fake, _muse_setup, _INC, ZONES, EVENTS, stages, GOOD_TLS
from test_meta_transport import Srv, KEY, OK_STREAM, LINE, URL, no_external_dns, sse  # noqa: F401

W, H = 640, 480


# ===== A. projection unit tests ===================================================================================================================================================
def _delta(payload="!aB|cD", extra=""):
    return f"<0f>1<|box;x1=10;y1=20;x2=110;y2=220;w={W};h={H};c=0.91|><|mask;x=0;y=0;c=0.91;data=4,5,{payload}|>{extra}"


def test_A_sam_projection_keeps_only_validated_values_and_drops_everything_else():
    raw = [{"type": "response.created", "id": "resp_1", "secret": "x"}, {"type": "response.in_progress", "z": 1},
           {"type": "response.output_text.delta", "delta": _delta(), "item_id": "i", "output_index": 0, "logprobs": [1]},
           {"type": "response.output_text.done", "text": "<0f>"},
           {"type": "response.completed", "response": {"id": "r", "status": "completed", "usage": {"images": 1, "input_tokens": 5, "note": "text", "details": {"a": 1}, "ok": True, "neg": -1, "Bad": 1}, "output": ["x"]}}]
    out = P.project_sam_events(raw)
    assert out == [{"type": "response.output_text.delta", "delta": f"<0f>1<|box;x1=10;y1=20;x2=110;y2=220;w={W};h={H};c=0.91|><|mask;x=0;y=0;c=0.91;data=4,5,!0|>"},
                   {"type": "response.completed", "response": {"usage": {"images": 1, "input_tokens": 5}}}]
    assert S.parse_stream(out, W, H)["state"] == "PARSED_OBJECTS"
    assert out is not raw and all(a is not b for a, b in zip(out, raw))


def test_A_the_mask_payload_never_survives_the_boundary():
    for payload in ("!aB|cD", "~" + "Z" * 5000, "!SECRETLIKE+/=payload", "!" + "".join(chr(c) for c in range(0x21, 0x7c) if chr(c) != "|")):
        out = P.project_sam_events([{"type": "response.output_text.delta", "delta": _delta(payload)}, {"type": "response.completed"}])
        assert out is not None and out[0]["delta"].endswith("data=4,5," + payload[0] + "0|>")
        assert payload[1:] not in json.dumps(out)


@pytest.mark.parametrize("bad", [[{"type": "response.output_text.delta", "delta": _delta(extra="trailing text")}], [{"type": "response.output_text.delta", "delta": "free text"}],
                                 [{"type": "response.output_text.delta"}], [{"type": "response.output_text.delta", "delta": 5}], ["not a dict"], [{"no_type": 1}], [{"type": 5}], "x", None,
                                 [{"type": "response.output_text.delta", "delta": _delta().replace("w=640", "w=abc")}], [{"type": "response.output_text.delta", "delta": _delta().replace("|><|mask", "|>,<|mask")}]])
def test_A_anything_outside_the_documented_grammar_makes_the_whole_response_unusable(bad):
    assert P.project_sam_events(bad) is None


def test_A_failure_events_keep_only_an_identifier_code_and_the_event_count_is_bounded():
    out = P.project_sam_events([{"type": "error", "code": "server_shutting_down", "message": "leak " + KEY}, {"type": "response.failed", "error": {"code": "bad code!", "message": "x"}},
                                {"type": "response.incomplete", "code": None}])
    assert out == [{"type": "error", "code": "server_shutting_down"}, {"type": "response.failed", "code": None}, {"type": "response.incomplete", "code": None}]
    assert P.project_sam_events([{"type": "response.created"}] * (P.MAX_EVENTS + 1)) is None


def test_A_chat_completion_projection_keeps_content_usage_and_identifiers_only():
    doc = {"id": "chatcmpl-1", "model": "meta-models/x-y", "object": "chat.completion", "created": 1, "system_fingerprint": "fp",
           "choices": [{"index": 0, "finish_reason": "stop", "logprobs": {"x": 1}, "message": {"role": "assistant", "content": "hola", "reasoning_content": "secret thoughts", "tool_calls": [{"a": 1}]}},
                       {"index": 1, "message": {"content": "second choice"}}],
           "usage": {"prompt_tokens": 155, "completion_tokens": 171, "total_tokens": 326, "completion_tokens_details": {"reasoning_tokens": 155}, "note": "x"}}
    out = P.project_chat_completion(doc)
    assert out == {"choices": [{"message": {"content": "hola"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 155, "completion_tokens": 171, "total_tokens": 326}}
    assert "secret thoughts" not in json.dumps(out) and "second choice" not in json.dumps(out)
    assert P.project_chat_completion({"choices": [{"message": {"content": None, "reasoning_content": "x"}}]}) == {"choices": [{"message": {"content": ""}}]}
    for bad in (None, [], {}, {"choices": []}, {"choices": [None]}, {"choices": [{}]}, {"choices": [{"message": {"content": 5}}]}, {"choices": [{"message": "x"}]}, {"choices": [{"message": {"content": "x" * (P.MAX_CONTENT_CHARS + 1)}}]}):
        assert P.project_chat_completion(bad) is None
    odd = P.project_chat_completion({"id": "bad id!", "model": "bad model!", "choices": [{"finish_reason": "no way!", "message": {"content": "ok"}}]})
    assert odd == {"choices": [{"message": {"content": "ok"}}]}
    ok_ids = P.project_chat_completion({"id": "chatcmpl-123", "model": "meta-models/x-y", "choices": [{"finish_reason": "stop", "message": {"content": "ok"}}]})
    assert "id" not in ok_ids and "model" not in ok_ids                                  # even well-formed provider strings are not returned


def test_A_unknown_providers_have_no_projector():
    assert P.project("nobody", {"choices": []}) is None and P.project("meta_sam31", {"choices": []}) is None


# ===== B. leak scanner (second layer) ==============================================================================================================================================
@pytest.mark.parametrize("hay", [KEY, "x" + KEY + "y", KEY.replace("-", "%2D"), KEY.replace("-", " - "), " ".join(KEY), "T.E.S.T." + KEY[4:], base64.b64encode(KEY.encode()).decode(),
                                 base64.urlsafe_b64encode(KEY.encode()).decode().rstrip("="), KEY.encode().hex(), KEY.encode().hex().upper(), "prefix " + base64.b64encode(("<" + KEY + ">").encode()).decode() + " suffix",
                                 "".join(f"%{ord(c):02X}" for c in KEY)])
def test_B_the_scanner_recognises_the_known_reconstructions(hay):
    assert P.text_leaks(hay, KEY) and P.leaks({"x": hay}, KEY) and P.leaks([hay], KEY)


@pytest.mark.parametrize("obj", [{"a": "TEST-KEY-NOT", "b": "-A-REAL-KEY-0123456789"}, {"TEST-KEY-NOT-A": "-REAL-KEY-0123456789"}, ["TEST-KEY", "-NOT-A-REAL", "-KEY-0123456789"],
                                 {"a": "TEST-KEY-NOT", "m": 1, "b": "-A-REAL-KEY-0123456789"}, {"a": ["TEST-KEY", {"b": "-NOT-A-REAL-KEY-0123456789"}]}])
def test_B_fragments_in_adjacent_fields_or_separated_only_by_keys_are_found(obj):
    assert P.leaks(obj, KEY)


def test_B_ordinary_text_and_numbers_are_not_flagged_and_edge_cases_fail_closed():
    assert not P.leaks({"a": "hello world", "n": 5, "choices": [{"message": {"content": "La regla determinista dejo la zona en estado RED."}}]}, KEY)
    assert not P.leaks("anything", "") and not P.text_leaks("", KEY)
    deep = cur = []
    for _ in range(60):
        nxt = []
        cur.append(nxt)
        cur = nxt
    assert P.leaks(deep, KEY) is True and P.text_leaks("x" * (P.MAX_SCAN_CHARS + 1), KEY) is True


def test_B_the_scanner_is_documented_as_not_universal_and_the_residual_channels_are_documented_not_denied():
    rot13 = KEY.translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz", "NOPQRSTUVWXYZABCDEFGHIJKLMnopqrstuvwxyzabcdefghijklm"))
    assert not P.text_leaks(rot13, KEY)                                       # NOT detected, by design of the claim: only the structural boundary can contain it
    assert "does NOT claim to detect every reversible" in P.__doc__ and "MINIMISATION" in P.__doc__ and "DOCUMENTED, not denied" in P.__doc__


# ===== C. transport integration (loopback): legitimate responses survive, hostile ones are contained ===============================================================================
def sam_guard(tmp_path, srv, key="req-g07-0001"):
    body = S.build_body(URL, "person")
    gov = SpendGovernor(cfg(tmp_path, body))
    inner = TestHttpsTransport(f"http://127.0.0.1:{srv.port}/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized={(S.PROVIDER_ID, S.MODEL, costgov.raw_digest(body))},
                               accept_sse=True, max_uses=5, allow_loopback_http=True)
    return gov, inner, EndpointBoundGuard(inner, gov, lambda: KEY)


def sse_of(*events):
    return "".join(f"data: {json.dumps(e)}\n\n" for e in events).encode()


def muse_guard(tmp_path, srv):
    view = M.explain.muse_view(_INC)
    body = M.build_body(view, "meta-models/x-y", 500)
    gov = SpendGovernor(cfg(tmp_path, body, provider=M.PROVIDER_ID, model="meta-models/x-y", price=Decimal("0.001")))
    inner = TestHttpsTransport(f"http://127.0.0.1:{srv.port}/v1", path="/chat/completions", provider=M.PROVIDER_ID, governor=gov, authorized={(M.PROVIDER_ID, "meta-models/x-y", costgov.raw_digest(body))},
                               max_uses=5, allow_loopback_http=True)
    return gov, inner, EndpointBoundGuard(inner, gov, lambda: KEY)


GOOD_TEXT = json.dumps({"resumen": "La regla determinista dejo la zona zone_1 en estado RED con 1 evento.", "incertidumbres": ["La geometria es en pixeles de imagen."], "pasos_revision_humana": ["Revisar los eventos contra la fuente original."]})


def test_C_a_legitimate_sam_response_with_unknown_extras_works_and_the_extras_are_not_returned(tmp_path, no_external_dns):
    ev = [{"type": "response.created", "response": {"id": "resp_1", "metadata": {"k": "v"}}},
          {"type": "response.output_text.delta", "delta": LINE, "sequence_number": 3, "extra": {"fragment_a": "TEST-KEY-NOT", "m": "zz", "fragment_b": "-A-REAL-KEY-0123456789"}},
          {"type": "response.completed", "response": {"usage": {"images": 1}, "headers": {"x-secret": "TEST-KEY-"}, "output": ["a"]}}]
    srv = Srv([(200, {"X-Echo": "TEST-KEY-NOT-A-REAL-KEY-0123456789"}, sse_of(*ev), 0)])
    try:
        gov, inner, g = sam_guard(tmp_path, srv)
        resp = g.send(S.build_governed_request(URL, "person", "req-g07-0002"))
        assert set(resp) == {"status", "body", "usage", "origin"} and resp["origin"] == "TEST" and set(resp["body"]) == {"events"}
        assert [e["type"] for e in resp["body"]["events"]] == ["response.output_text.delta", "response.completed"]
        assert set(resp["body"]["events"][1]) == {"type", "response"} and resp["body"]["events"][1]["response"] == {"usage": {"images": 1}} and resp["usage"] == {"images": 1}
        dumped = json.dumps(resp, default=str)
        for trace in ("fragment_a", "TEST-KEY", "x-secret", "metadata", "resp_1", "sequence_number", "X-Echo"):
            assert trace not in dumped, trace                                    # nothing the contract has no place for crossed the boundary (headers were never returned at all)
        assert resp["body"]["events"][0]["delta"].endswith("!0|>")
        assert S.parse_stream(resp["body"]["events"], W, H)["state"] == "PARSED_OBJECTS"
    finally:
        srv.close()


def test_C_interleaved_fragments_in_unknown_fields_cannot_leave_because_those_fields_are_not_returned(tmp_path, no_external_dns):
    """The reviewer's bypass class: the scanner cannot see fragments separated by unrelated values; the structure guarantees they are dropped."""
    ev = [{"type": "response.created", "a": "TEST-KEY-NOT", "m1": "zzz", "b": "-A-REAL-KEY", "m2": "yyy", "c": "-0123456789"},
          {"type": "response.output_text.delta", "delta": LINE}, {"type": "response.completed"}]
    srv = Srv([(200, {}, sse_of(*ev), 0)])
    try:
        gov, inner, g = sam_guard(tmp_path, srv)
        raw_hit = P.leaks(ev, KEY)
        resp = g.send(S.build_governed_request(URL, "person", "req-g07-0003"))
        assert raw_hit is False                                                  # the scanner alone would have let this through ...
        assert "TEST" not in json.dumps(resp["body"]) and len(resp["body"]["events"]) == 2        # ... and the boundary returns none of it
    finally:
        srv.close()


@pytest.mark.parametrize("shape", ["unknown_event_literal", "percent_in_unknown", "base64_in_unknown", "hex_in_unknown", "adjacent_fragments", "delta_with_text", "mask_payload_literal"])
def test_C_hostile_sam_responses_are_refused_and_nothing_is_returned(tmp_path, no_external_dns, shape):
    b64 = base64.b64encode(KEY.encode()).decode()
    ev = {"unknown_event_literal": [{"type": "x.unknown", "v": KEY}], "percent_in_unknown": [{"type": "x.unknown", "v": KEY.replace("-", "%2D")}],
          "base64_in_unknown": [{"type": "x.unknown", "v": b64}], "hex_in_unknown": [{"type": "x.unknown", "v": KEY.encode().hex()}],
          "adjacent_fragments": [{"type": "x.unknown", "a": "TEST-KEY-NOT-A", "b": "-REAL-KEY-0123456789"}],
          "delta_with_text": [{"type": "response.output_text.delta", "delta": LINE + " " + KEY[:8]}],
          "mask_payload_literal": [{"type": "response.output_text.delta", "delta": _delta("!" + KEY.replace("-", "x"))}]}[shape]
    srv = Srv([(200, {}, sse_of(*ev, {"type": "response.completed"}), 0)])
    try:
        gov, inner, g = sam_guard(tmp_path, srv)
        out = metaclient.SamClient(g).first_call(URL, "person", "req-g07-0004", width=W, height=H, zones=ZONES)
        if shape == "mask_payload_literal":                                       # alphanumerics only: the payload is not the credential itself, but it is a hostile delta payload: dropped, call survives
            assert out["outcome"] in ("OK", "TRANSPORT_FAULT")
        else:
            assert out["outcome"] == "TRANSPORT_FAULT" and out["evidence"] is None
        assert KEY not in json.dumps(out, default=str) and "TEST-KEY" not in json.dumps(out, default=str)
    finally:
        srv.close()


def test_C_the_muse_response_is_reduced_to_content_usage_and_identifiers(tmp_path, no_external_dns):
    doc = {"id": "chatcmpl-7", "model": "meta-models/x-y", "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": GOOD_TEXT, "reasoning_content": "a" + "TEST-KEY-NOT", "z": "zzz", "r2": "-A-REAL-KEY-0123456789"}}],
           "usage": {"prompt_tokens": 155, "completion_tokens": 171, "total_tokens": 326, "x": "TEST-KEY"}, "extra": {"k": "TEST-KEY-"}}
    srv = Srv([(200, {}, json.dumps(doc).encode(), 0)])
    try:
        gov, inner, g = muse_guard(tmp_path, srv)
        out = M.MuseOpenClient(g).explain(_INC, "meta-models/x-y", "req-g07-0005")
        assert out["outcome"] == "OK" and out["explanation"]["status"] == "ACCEPTED" and out["explanation"]["severity"] == "RED"
        assert "reasoning" not in json.dumps(out) and "TEST-KEY" not in json.dumps(out, default=str) and "chatcmpl-7" not in json.dumps(out["explanation"]["report"])
    finally:
        srv.close()


@pytest.mark.parametrize("encode", [lambda k: k.replace("-", "%2D"), lambda k: base64.b64encode(k.encode()).decode(), lambda k: k.encode().hex(), lambda k: k[:12] + "\n" + k[12:], lambda k: " ".join(k)])
def test_C_a_model_text_that_carries_the_credential_in_a_known_encoding_is_refused(tmp_path, no_external_dns, encode):
    text = json.dumps({"resumen": "x " + encode(KEY), "incertidumbres": ["a"], "pasos_revision_humana": ["b"]})
    srv = Srv([(200, {}, json.dumps({"choices": [{"message": {"content": text}}]}).encode(), 0)])
    try:
        gov, inner, g = muse_guard(tmp_path, srv)
        out = M.MuseOpenClient(g).explain(_INC, "meta-models/x-y", "req-g07-0006")
        assert out["outcome"] == "TRANSPORT_FAULT" and "response_echoed_the_credential" in out["code"] and out["explanation"] is None
    finally:
        srv.close()


@pytest.mark.parametrize("doc", [{"choices": []}, {"choices": "x"}, {"nochoices": 1}, {"choices": [{"message": {"content": 5}}]}, [], "string", 5])
def test_C_a_muse_document_outside_the_contract_is_refused(tmp_path, no_external_dns, doc):
    srv = Srv([(200, {}, json.dumps(doc).encode(), 0)])
    try:
        gov, inner, g = muse_guard(tmp_path, srv)
        out = M.MuseOpenClient(g).explain(_INC, "meta-models/x-y", "req-g07-0007")
        assert out["outcome"] == "TRANSPORT_FAULT" and out["explanation"] is None and ("response_outside_contract" in out["code"] or "json_not_an_object" in out["code"])
    finally:
        srv.close()


def test_C_error_bodies_expose_only_the_allow_listed_view_even_with_hostile_fields(tmp_path, no_external_dns):
    body = {"error": {"message": "overloaded", "type": "server_error", "code": "service_overloaded", "param": "TEST-KEY-NOT", "extra": ["-A-REAL-KEY"], "details": {"k": "v"}}, "request_id": "r"}
    srv = Srv([(503, {}, json.dumps(body).encode(), 0)])
    try:
        gov, inner, g = sam_guard(tmp_path, srv)
        out = metaclient.SamClient(g).first_call(URL, "person", "req-g07-0008", width=W, height=H, zones=ZONES)
        assert out["provider_error"] == {"status": 503, "type": "server_error", "code": "service_overloaded", "message": "overloaded"}
        assert "TEST-KEY" not in json.dumps(out, default=str) and "request_id" not in json.dumps(out, default=str)
    finally:
        srv.close()


def test_C_the_boundary_is_applied_before_the_attestation_so_the_certified_digest_is_the_projected_value(tmp_path):
    ev_raw = [{"type": "response.output_text.delta", "delta": LINE}, {"type": "response.completed"}]
    proj = P.project_sam_events(ev_raw)
    assert proj != ev_raw and proj[0]["delta"].endswith("!0|>")
    body, gov, auth = sam_setup(tmp_path)
    req = sam_request("req-g07-0009")
    att = minted(gov, req, {"events": proj})
    assert metahttp.attestation_problems(att, request=req, payload={"events": proj}, governor=gov) == []
    assert "response_digest_mismatch" in metahttp.attestation_problems(att, request=req, payload={"events": ev_raw}, governor=gov)


# ===== D. the three surviving mutants ===========================================================================================================================================
def test_V04_an_attestation_for_one_response_cannot_certify_several_responses(tmp_path):
    body, gov, auth = sam_setup(tmp_path)
    req = sam_request("req-v04-0001")
    att = minted(gov, req, {"events": EVENTS})
    two = [{"concept": "person", "events": EVENTS}, {"concept": "truck", "events": EVENTS}]
    for responses in (two, two[:1] * 2, [two[0]] + [{"concept": "person", "events": [{"type": "response.completed"}]}]):
        with pytest.raises(meta_obs.NormalizationRefused) as e:
            meta_pipeline.build_evidence_multi(responses, width=W, height=H, pts_us=[0], zones=ZONES, simulated=False, attestation=att, request=req, governor=gov)
        assert e.value.code == "real_evidence_supports_exactly_one_response"
    assert att.attempt_id not in metahttp._CONSUMED                                          # the refusal spent nothing
    with pytest.raises(meta_obs.NormalizationRefused):
        meta_pipeline.build_evidence_multi([], width=W, height=H, pts_us=[0], zones=ZONES, simulated=False, attestation=att, request=req, governor=gov)
    ok = meta_pipeline.build_evidence_multi(two[:1], width=W, height=H, pts_us=[0], zones=ZONES, simulated=False, attestation=att, request=req, governor=gov)
    assert ok["provenance"]["status"] == "REAL_PROVIDER_RESPONSE_UNVERIFIED"


def test_F2i_an_attestation_for_a_body_the_owner_never_authorized_is_not_accepted(tmp_path):
    body, gov, auth = sam_setup(tmp_path)                                                    # the governor authorizes ONLY the digest of the "person" body
    unauthorized = S.build_governed_request(URL, "truck", "req-f2i-0001")
    assert costgov.raw_digest(unauthorized["body"]) not in gov.config.authorized_payload_digests
    att = minted(gov, unauthorized, {"events": EVENTS})                                      # everything else about it is consistent: identity, digests, ledger reservation, stages, TLS
    p = metahttp.attestation_problems(att, request=unauthorized, payload={"events": EVENTS}, governor=gov)
    assert p == ["request_not_authorized"]
    red, problems = metahttp.redeem(att, request=unauthorized, payload={"events": EVENTS}, governor=gov)
    assert red is None and problems == ["request_not_authorized"]
    with pytest.raises(meta_obs.NormalizationRefused) as e:
        meta_pipeline.build_evidence(EVENTS, concept="truck", width=W, height=H, pts_us=[0], zones=ZONES, simulated=False, attestation=att, request=unauthorized, governor=gov)
    assert "request_not_authorized" in e.value.code and att.attempt_id not in metahttp._CONSUMED


@pytest.mark.parametrize("bad", ["has space in key", KEY + "\r\nX-Evil: 1", " " + KEY, KEY + "\n", "short", "LLM_1_abcdéfgh", "", "LLM_1_abc,defgh", 'LLM_1_"abcdefgh"'])
def test_F4g_the_raw_transport_with_a_reservation_and_a_guard_claim_still_refuses_a_malformed_credential(tmp_path, no_external_dns, bad):
    """The earlier vector never held a claim, so the claim check refused first and the credential check was never the deciding one."""
    body, gov, auth = sam_setup(tmp_path)
    _Fake.made = 0
    inner = TestHttpsTransport("https://example.com/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized=auth, accept_sse=True, connection_factory=lambda *a: _Fake())
    req = sam_request("req-f4g-0001")
    gov.reserve(req["request_key"], req["model"], req["provider"], 0, 1)                      # open reservation, live, authorized body: ONLY the credential is wrong
    metahttp._TLS.claim = metahttp._claim_impl(inner, req)                                    # and the guard's claim is present
    try:
        with pytest.raises(Denied) as e:
            inner.send(req, bad, 5)
    finally:
        metahttp._TLS.claim = None
    assert e.value.code == "not_governed" and _Fake.made == 0
    # control: the same state with a valid credential DOES reach the connection (so the refusal above was the credential's)
    gov2 = SpendGovernor(cfg(tmp_path / "c", body)) if (tmp_path / "c").mkdir() is None else None
    inner2 = TestHttpsTransport("https://example.com/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov2, authorized=auth, accept_sse=True, connection_factory=lambda *a: _Fake())
    gov2.reserve(req["request_key"], req["model"], req["provider"], 0, 1)
    metahttp._TLS.claim = metahttp._claim_impl(inner2, req)
    try:
        with pytest.raises(metahttp.TransportFault) as ef:                                      # _Fake.request raises: the request WAS handed to the connection (the type name is all that leaves)
            inner2.send(req, KEY, 5)
        assert "unexpected_AssertionError" in str(ef.value)
    finally:
        metahttp._TLS.claim = None
    assert _Fake.made == 1


def test_E_every_test_transport_has_exactly_one_response_contract_and_a_projector(tmp_path):
    body, gov, auth = sam_setup(tmp_path)
    common = dict(governor=gov, authorized=auth, allow_loopback_http=True)
    TestHttpsTransport("http://127.0.0.1:1/v1", path="/responses", provider=S.PROVIDER_ID, accept_sse=True, **common)
    TestHttpsTransport("http://127.0.0.1:1/v1", path="/chat/completions", provider=M.PROVIDER_ID, **common)
    for kw in (dict(provider=S.PROVIDER_ID, accept_sse=False), dict(provider=M.PROVIDER_ID, accept_sse=True), dict(provider="nobody", accept_sse=False), dict(provider="nobody", accept_sse=True),
               dict(provider="", accept_sse=False), dict(provider=None, accept_sse=False)):
        with pytest.raises(ValueError) as e:
            TestHttpsTransport("http://127.0.0.1:1/v1", path="/responses", **kw, **common)
        assert "provider_unknown_or_mode_mismatch" in str(e.value)


def test_G_the_first_call_package_script_is_offline_blank_and_sends_nothing():
    import subprocess, sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "sam31_package.py"
    if not script.exists():
        pytest.skip("script not in this scratch copy")
    src = script.read_text()
    for banned in ("import socket", "import http", "import urllib", "import requests", "import ssl", "os.environ", "getenv", "metahttp", "metaclient", "GuardedTransport"):
        assert banned not in src, banned
    ok = subprocess.run([sys.executable, "-I", str(script), "--image-url", "https://example.com/p.png", "--concept", "person"], capture_output=True, text=True, timeout=60, cwd=str(root))
    assert ok.returncode == 0, ok.stderr
    doc = json.loads(ok.stdout)
    assert doc["status"] == "PROPOSED_FOR_HUMAN_APPROVAL_NOT_EXECUTED" and doc["request"]["body_digest"] == costgov.raw_digest(S.build_body("https://example.com/p.png", "person"))
    rec = doc["authorization_record_template"]
    assert rec["max_calls"] == 1 and rec["retries"] == 0 and rec["max_usd"] == "0.01" and all(rec[k] is False for k in rec if k.endswith("confirmed") or k.startswith("key_") or k.startswith("image_url") or k == "revocation_planned")
    assert doc["problems_with_the_record_if_submitted_as_is"] and len(doc["separate_approvals_required"]) == 5
    for bad in ("http://example.com/p.png", "https://user:pw@example.com/p.png", "https://example.com/p.png?sig=x", "https://localhost/p.png", "https://10.0.0.1/p.png"):
        r = subprocess.run([sys.executable, "-I", str(script), "--image-url", bad], capture_output=True, text=True, timeout=60, cwd=str(root))
        assert r.returncode != 0 and "REFUSED" in (r.stderr + r.stdout)


# ===== Review 1 of Live Gate 07: G7-01 numeric covert channels, G7-02 a test transport must never reach a real host ==================================================================
CRED = "ABCDEFGH"                                                                         # a valid 8-character token68 credential


def _line_with_ids(ids):
    recs = ",".join(f"{i}<|box;x1=10;y1=20;x2=110;y2=220;w=640;h=480|><|mask;x=0;y=0;data=4,5,!a|>" for i in ids)
    return f"<0f>{recs}"


def test_G701_usage_keys_are_a_closed_set_so_a_provider_cannot_spell_a_credential_with_its_own_key_names():
    usage = {c: ord(x) for c, x in zip("abcdefgh", CRED)}                                 # the review's reproduction
    assert P.project_usage(usage) is None
    out = P.project_sam_events([{"type": "response.completed", "response": {"usage": usage}}])
    assert out == [{"type": "response.completed"}]
    assert P.project_usage({"input_tokens": 5, "output_tokens": 6, "images": 1, "bogus": 3}) == {"input_tokens": 5, "output_tokens": 6, "images": 1}
    for bad in ({"input_tokens": -1}, {"input_tokens": 10 ** 9 + 1}, {"input_tokens": float("nan")}, {"input_tokens": "5"}, {"input_tokens": True}, {"input_tokens": [5]}):
        assert P.project_usage(bad) is None


def test_G701_even_with_the_allowed_key_names_a_credential_spelled_by_the_numbers_is_refused(tmp_path, no_external_dns):
    keys = ["input_tokens", "output_tokens", "total_tokens", "prompt_tokens", "completion_tokens", "cached_tokens", "reasoning_tokens", "images"]
    usage = {k: ord(x) for k, x in zip(keys, CRED)}
    assert P.leaks({"usage": usage}, CRED) is True and P.numeric_leaks({"u": usage}, CRED) is True
    assert P.leaks({"usage": dict(reversed(list(usage.items())))}, CRED) is True            # reversed document order
    by_sorted = {k: ord(x) for k, x in zip(sorted(keys), CRED)}
    assert P.leaks({"usage": dict(reversed(list(by_sorted.items())))}, CRED) is True       # spelled in KEY-SORTED order while the document order is scrambled
    body = S.build_body(URL, "person")
    gov = SpendGovernor(cfg(tmp_path, body))
    srv = Srv([(200, {}, sse_of({"type": "response.output_text.delta", "delta": LINE}, {"type": "response.completed", "response": {"usage": usage}}), 0)])
    try:
        inner = TestHttpsTransport(f"http://127.0.0.1:{srv.port}/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized={(S.PROVIDER_ID, S.MODEL, costgov.raw_digest(body))},
                                   accept_sse=True, max_uses=5, allow_loopback_http=True)
        g = EndpointBoundGuard(inner, gov, lambda: CRED)
        out = metaclient.SamClient(g).first_call(URL, "person", "req-g701-0001", width=W, height=H, zones=ZONES)
        assert out["outcome"] == "TRANSPORT_FAULT" and "response_echoed_the_credential" in out["code"] and out["evidence"] is None
    finally:
        srv.close()


def test_G701_numbers_carried_by_a_sam_line_are_bounded_and_a_credential_spelled_by_object_ids_is_refused():
    ids = [ord(c) for c in CRED]
    assert P.leaks(P.project_sam_events([{"type": "response.output_text.delta", "delta": _line_with_ids(ids)}]), CRED) is True        # 65,66,...,72 as the ids of 8 records
    assert P.leaks(P.project_sam_events([{"type": "response.output_text.delta", "delta": _line_with_ids([1, 2, 3, 4])}]), CRED) is False  # ordinary ids
    d = lambda **kw: [{"type": "response.output_text.delta", "delta": _delta().replace("<0f>1", f"<{kw.get('frame', 0)}f>{kw.get('oid', 1)}").replace("x2=110", f"x2={kw.get('x2', 110)}").replace("w=640", f"w={kw.get('w', 640)}")}]
    assert P.project_sam_events(d()) is not None
    for bad in (dict(frame=P.MAX_FRAME + 1), dict(oid=P.MAX_OBJECT_ID + 1), dict(x2=P.MAX_DIM + 1, w=P.MAX_DIM + 5), dict(w=P.MAX_DIM + 1)):
        assert P.project_sam_events(d(**bad)) is None, bad


@pytest.mark.parametrize("given,want", [("0.91", "0.91"), ("0.9100", "0.91"), ("1", "1"), ("0", "0"), (".5", "0.5"), ("0.123456789012345678", "0.1235"), ("0.00001", "0")])
def test_G701_confidence_is_reserialised_with_at_most_four_decimals(given, want):
    line = _delta().replace("c=0.91", f"c={given}")
    out = P.project_sam_events([{"type": "response.output_text.delta", "delta": line}])
    assert out is not None and f";c={want}|>" in out[0]["delta"]
    assert P.project_sam_events([{"type": "response.output_text.delta", "delta": _delta().replace("c=0.91", "c=1.5")}]) is None


def test_G701_strings_that_cross_are_from_closed_sets_so_a_provider_cannot_spell_with_codes_types_or_reasons():
    out = P.project_sam_events([{"type": "error", "code": "ABCDEFGH"}, {"type": "response.failed", "code": "rate_limit_exceeded"}])
    assert out == [{"type": "error", "code": None}, {"type": "response.failed", "code": "rate_limit_exceeded"}]
    ch = P.project_chat_completion({"choices": [{"finish_reason": "ABCDEFGH", "message": {"content": "x"}}]})
    assert "finish_reason" not in ch["choices"][0]
    assert P.project_chat_completion({"choices": [{"finish_reason": "length", "message": {"content": "x"}}]})["choices"][0]["finish_reason"] == "length"


def test_G701_error_view_type_and_code_are_closed_sets_message_is_the_one_scanned_free_text_channel(tmp_path, no_external_dns):
    srv = Srv([(401, {}, json.dumps({"error": {"message": "bad key", "type": "ABCDEFGH", "code": "ABCDEFGH"}}).encode(), 0)])
    try:
        body = S.build_body(URL, "person")
        gov = SpendGovernor(cfg(tmp_path, body))
        inner = TestHttpsTransport(f"http://127.0.0.1:{srv.port}/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized={(S.PROVIDER_ID, S.MODEL, costgov.raw_digest(body))},
                                   accept_sse=True, max_uses=5, allow_loopback_http=True)
        out = metaclient.SamClient(EndpointBoundGuard(inner, gov, lambda: KEY)).first_call(URL, "person", "req-g701-0002", width=W, height=H, zones=ZONES)
        assert out["provider_error"] == {"status": 401, "type": None, "code": None, "message": "bad key"}
    finally:
        srv.close()


@pytest.mark.parametrize("url", ["https://attacker.example/v1", "https://api.meta.ai/v1", "https://inference.baseten.co/v1", "https://localhost.attacker.example/v1", "https://127.0.0.1.attacker.example/v1",
                                 "https://example.com/v1", "https://10.0.0.5/v1", "https://[2001:db8::1]/v1"])
def test_G702_a_test_transport_without_an_injected_fake_refuses_every_non_loopback_host(tmp_path, url):
    body, gov, auth = sam_setup(tmp_path)
    with pytest.raises(ValueError) as e:
        TestHttpsTransport(url, path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized=auth, accept_sse=True, allow_loopback_http=True)
    assert "test_transport_requires_loopback" in str(e.value)
    TestHttpsTransport(url, path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized=auth, accept_sse=True, connection_factory=lambda *a: _Fake())       # an injected fake is fine


@pytest.mark.parametrize("url", ["http://127.0.0.1:1/v1", "https://127.0.0.1:1/v1", "http://localhost:1/v1", "http://[::1]:1/v1"])
def test_G702_loopback_hosts_are_accepted(tmp_path, url):
    body, gov, auth = sam_setup(tmp_path)
    t = TestHttpsTransport(url, path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized=auth, accept_sse=True, allow_loopback_http=True)
    assert t.is_production is False


def test_G702_even_a_forced_non_loopback_host_never_resolves_a_name_or_opens_a_socket(tmp_path, no_external_dns):
    body, gov, auth = sam_setup(tmp_path)
    t = TestHttpsTransport("http://127.0.0.1:1/v1", path="/responses", provider=S.PROVIDER_ID, governor=gov, authorized=auth, accept_sse=True, allow_loopback_http=True)
    object.__setattr__(t, "host", "attacker.example")                                      # white-box: bypass the constructor
    g = EndpointBoundGuard(t, gov, lambda: KEY)
    out = metaclient.SamClient(g).first_call(URL, "person", "req-g702-0001", width=W, height=H, zones=ZONES)
    assert out["outcome"] == "TRANSPORT_FAULT" and "test_transport_refuses_a_non_loopback_host" in out["code"]
    assert no_external_dns == []                                                           # no name resolution was attempted
