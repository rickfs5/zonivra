"""META LIVE READINESS 04: provider response -> parser -> experimental observations -> deterministic Risk Kernel -> evidence record. Fixtures are SIMULATED and labelled so.
Expectations: the provider never sets severity; a response is never attributed to RF-DETR; UNKNOWN is never green; absence of detections is not safety."""
import copy
import json

import pytest

from mineguard import meta_obs, meta_pipeline as mp, sam31
from mineguard import schema

W, H = 1280, 720
PTS = [i * 100_000 for i in range(30)]
Z = [mp.full_frame_zone(W, H)]


def ln(i, x, y, c="0.90", oid=1):
    return {"type": "response.output_text.delta", "delta": f"<{i}f>{oid}<|box;x1={x};y1={y};x2={x + 200};y2={y + 300};w={W};h={H};c={c}|><|mask;x=0;y=0;c={c};data=4,5,!ab|cd|>"}


def stream(x, y, frames=30, c="0.90"):
    return [ln(i, x, y, c) for i in range(frames)] + [{"type": "response.completed"}]


def ev(responses, **kw):
    return mp.build_evidence_multi(responses, width=W, height=H, pts_us=PTS, zones=Z, simulated=True, source_label="fx", **kw)


def test_person_and_truck_responses_reach_RED_only_through_the_deterministic_kernel_and_stay_labelled_simulated():
    r = ev([{"concept": "person", "events": stream(100, 100)}, {"concept": "truck", "events": stream(150, 150)}])
    z = r["severity_by_zone"]["Z1"]
    assert z["final_state"] == "RED" and z["event_count"] == 1 and z["engine_version"].startswith("mg-risk-kernel/")
    assert r["decided_by"] == "deterministic_policy" and r["provenance"]["status"] == "SIMULATED" and r["provenance"]["origin"] == "simulated_provider_response"
    assert r["provenance"]["attributed_to_local_detector"] is False and r["provenance"]["real_meta_call"] is False


def test_one_concept_alone_cannot_produce_the_two_class_RED_rule():
    r = ev([{"concept": "person", "events": stream(100, 100)}])
    assert r["severity_by_zone"]["Z1"]["final_state"] == "YELLOW" and r["severity_by_zone"]["Z1"]["peak_event_severity"] != "RED" and "ONE concept per request" in " ".join(r["limitations"])


def test_object_ids_of_different_responses_do_not_collide_and_the_concept_map_is_closed():
    r = ev([{"concept": "person", "events": stream(100, 100)}, {"concept": "truck", "events": stream(150, 150)}])
    assert r["rejections"] == [] and r["severity_by_zone"]["Z1"]["final_state"] == "RED"
    r2 = ev([{"concept": "person", "events": stream(100, 100)}, {"concept": "excavator", "events": stream(150, 150)}])
    assert r2["severity_by_zone"]["Z1"]["peak_event_severity"] == "YELLOW"           # an unmapped concept becomes "other": never invented into a vehicle, so no RED


def test_a_concept_response_that_does_not_parse_makes_the_frames_unknown_not_empty():
    bad = [{"type": "response.output_text.delta", "delta": "garbage"}]
    r = ev([{"concept": "person", "events": stream(100, 100)}, {"concept": "truck", "events": bad}])
    assert r["severity_by_zone"]["Z1"]["final_state"] == "UNKNOWN" and r["coverage"]["absence_is_not_safety"] is True
    assert r["provenance"]["parse_reasons"][1] == "missing_frame_marker"


@pytest.mark.parametrize("events,reason", [([{"type": "response.completed"}], None), ([], "stream_not_completed"), ([{"type": "response.failed"}], "response.failed")])
def test_zero_matches_failed_or_empty_streams_are_never_green(events, reason):
    r = ev([{"concept": "person", "events": events}])
    assert r["severity_by_zone"]["Z1"]["final_state"] == "UNKNOWN"
    assert r["coverage"]["absence_is_not_safety"] is True and "NOT evidence that the zone is clear" in r["coverage"]["note"]
    assert r["provenance"]["parse_reasons"] == [reason]


def test_low_or_absent_confidence_never_becomes_a_confident_detection():
    nc = lambda i, x: {"type": "response.output_text.delta", "delta": f"<{i}f>1<|box;x1={x};y1=100;x2={x + 200};y2=400;w={W};h={H}|><|mask;x=0;y=0;data=4,5,!ab|>"}
    evs = lambda x: [nc(i, x) for i in range(30)] + [{"type": "response.completed"}]
    r = ev([{"concept": "person", "events": evs(100)}, {"concept": "truck", "events": evs(150)}])
    o = meta_obs.from_wire(sam31.parse_stream(evs(100), W, H, 30), "person", W, H, PTS, True)
    obs = meta_obs.normalize(o)
    assert all(x["confidence"] == {"permille": None, "kind": "not_applicable"} for f in obs["frames"] for x in f["objects"])   # never fabricated
    assert r["severity_by_zone"]["Z1"]["final_state"] in ("UNKNOWN", "RED", "YELLOW", "GREEN")


def test_provider_timebase_is_never_trusted_and_a_missing_frame_is_unknown():
    r = ev([{"concept": "person", "events": stream(100, 100, frames=10)}])         # a response covering 10 of the 30 source frames
    assert r["coverage"]["frames_not_analyzed"] == 20 and r["severity_by_zone"]["Z1"]["unknown_us"] >= 2_000_000


def test_extension_is_refused_by_the_canonical_validator_and_never_carries_the_local_detector_identity():
    o = meta_obs.normalize(meta_obs.from_wire(sam31.parse_stream(stream(100, 100), W, H, 30), "person", W, H, PTS, True))
    assert o["schema"] == "mg.obsset.metalab.x1" and o["origin"] == "simulated_provider_response" and o["origin"] != "model_inference"
    assert o["producer"]["adapter_id"] != "rfdetr" and "rf-detr" not in json.dumps(o["producer"]).lower() and o["producer"]["weights_sha256"] is None
    with pytest.raises(Exception):
        schema.validate_observation_set(o)                              # the canonical validator refuses the extension: the canonical contract is untouched
    for f in o["frames"]:
        for x in f["objects"]:
            assert x["track_ref"] is None


def test_the_public_normalizer_can_never_produce_a_real_label_whatever_the_fixture_carries():
    """Recovery 06 (MG-R5-02): a valid redemption plus hand-built frames used to reach a real label through `normalize`; there is no such path any more."""
    good = {"attempt_id": "a1", "request_digest": "a" * 64, "response_digest": "b" * 64, "provider": "meta_sam31", "model": "sam-3.1", "request_key": "req-000001", "reservation_slot": "s#0",
            "stages": [], "tls": {"peer_verified": True, "hostname": "api.meta.ai"}, "endpoint": ["api.meta.ai", "/v1", "/responses"], "level": "x"}
    for receipt in (None, good, "redemption", 5, object()):
        env = meta_obs.from_wire(sam31.parse_stream(stream(100, 100), W, H, 30), "person", W, H, PTS, simulated=False, receipt=receipt)
        env["payload_digest"] = "c" * 64
        with pytest.raises(meta_obs.NormalizationRefused) as e:
            meta_obs.normalize(env)
        assert e.value.code == "real_label_not_accepted_here", receipt
    sim = meta_obs.from_wire(sam31.parse_stream(stream(100, 100), W, H, 30), "person", W, H, PTS, simulated=True, receipt=good)
    sim["transport_receipt"], sim["payload_digest"] = good, "c" * 64          # extra keys on a simulated fixture never change its origin
    o = meta_obs.normalize(sim)
    assert o["origin"] == "simulated_provider_response" and o["provenance"]["status"] == "SIMULATED" and "transport_receipt" not in o
    assert not hasattr(meta_obs, "_REAL_TOKEN") and "real_view" not in meta_obs.normalize.__code__.co_varnames


def test_a_concept_that_reported_nothing_makes_the_merged_frames_unknown_not_analyzed():
    """Review CD-6: a zero-match response vanished in the merge and the other concept's frames stayed analyzed (the kernel could say YELLOW)."""
    r = ev([{"concept": "person", "events": stream(100, 100)}, {"concept": "truck", "events": [{"type": "response.completed"}]}])
    assert r["coverage"]["frames_not_analyzed"] == 30 and r["severity_by_zone"]["Z1"]["final_state"] == "UNKNOWN" and r["severity_by_zone"]["Z1"]["event_count"] == 0
    assert r["coverage"]["absence_is_not_safety"] is True
    partial = ev([{"concept": "person", "events": stream(100, 100)}, {"concept": "truck", "events": stream(150, 150, frames=10)}])
    assert partial["coverage"]["frames_not_analyzed"] == 20                                    # frames where one concept was silent are UNKNOWN, the rest are analyzed


def test_evidence_carries_the_required_provenance_and_a_stable_digest():
    r = ev([{"concept": "person", "events": stream(100, 100)}, {"concept": "truck", "events": stream(150, 150)}], model_version=None)
    p = r["provenance"]
    for k in ("model_id", "provider_id", "input_digest", "response_digest", "observations_digest", "evaluation_digest", "origin", "limitations", "concepts"):
        assert k in p, k
    assert r["schema"] == "mg.meta.evidence/1" and len(r["digest"]) == 64
    assert r["digest"] == ev([{"concept": "person", "events": stream(100, 100)}, {"concept": "truck", "events": stream(150, 150)}])["digest"]
    assert "accuracy not validated" in r["limitations"]


def test_a_box_outside_the_image_is_rejected_not_clipped_and_the_frame_is_unknown():
    r = ev([{"concept": "person", "events": [{"type": "response.output_text.delta", "delta": f"<0f>1<|box;x1=1;y1=1;x2={W + 5};y2=9;w={W};h={H}|><|mask;x=0;y=0;data=1,1,!a|>"}, {"type": "response.completed"}]}])
    assert r["provenance"]["parse_reasons"] == ["box_outside_image"] and r["severity_by_zone"]["Z1"]["final_state"] == "UNKNOWN"
