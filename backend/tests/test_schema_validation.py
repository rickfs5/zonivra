import json
from pathlib import Path

import pytest

from mineguard import schema
from mineguard.schema import ValidationFailure, validate_observation_set


def codes(obs, **kw):
    with pytest.raises(ValidationFailure) as e:
        validate_observation_set(obs, **kw)
    return e.value.codes


def test_valid_fixture_passes(obsset):
    validate_observation_set(obsset())


def test_v05_partition_gap_and_overlap(obsset):
    o = obsset(); o["frame_status_ranges"] = [{"from_frame": 1, "to_frame": 1, "status": "not_analyzed_stride"}]
    assert "V-05" in codes(o)
    o = obsset(); o["frame_status_ranges"][0]["from_frame"] = 0
    assert "V-05" in codes(o)


def test_v06_bbox_outside_frame_and_degenerate(obsset):
    o = obsset(); o["frames"][0]["objects"][0]["bbox"]["w"] = 101
    assert "V-06" in codes(o)
    o = obsset(); o["frames"][0]["objects"][0]["bbox"]["h"] = 0
    assert "V-06" in codes(o)


def test_v07_truncated_flag_must_match_geometry(obsset):
    o = obsset(); o["frames"][0]["objects"][0]["bbox"]["x"] = 5
    assert "V-07" in codes(o)


def test_v08_duplicate_obj_key(obsset):
    o = obsset(); o["frames"][0]["objects"].append(json.loads(json.dumps(o["frames"][0]["objects"][0])))
    assert "V-08" in codes(o)


def test_v09_replay_cannot_declare_model_inference(obsset):
    o = obsset("model_inference")
    assert "V-09" in codes(o)


def test_v09_inference_requires_model_and_weights_hash(obsset):
    o = obsset("model_inference"); o["producer"]["adapter_id"] = "rfdetr-nano"
    o["frames"][0]["objects"][0]["confidence"] = {"permille": 500, "kind": "model_uncalibrated"}
    assert "V-09" in codes(o)


def test_v10_confidence_range_and_synthetic_rule(obsset):
    o = obsset(); o["frames"][0]["objects"][0]["confidence"] = {"permille": 1001, "kind": "model_uncalibrated"}
    assert "V-10" in codes(o)
    o = obsset("synthetic_scripted"); o["frames"][0]["objects"][0]["confidence"] = {"permille": 1000, "kind": "model_uncalibrated"}
    assert "V-10" in codes(o)


def test_v11_forbidden_physical_fields(obsset):
    o = obsset(); o["producer"]["params"]["distance_m"] = 3
    assert "V-11" in codes(o)


def test_v17_floats_in_hashed_body(obsset):
    o = obsset(); o["producer"]["params"] = {"x": 1.5}
    assert codes(o)  # strict int/str/bool params reject floats at the schema layer


def test_v20_unknown_fields_rejected(obsset):
    o = obsset(); o["frames"][0]["objects"][0]["distance"] = 3
    assert "V-20" in codes(o)
    o = obsset(); o["unexpected"] = 1
    assert "V-20" in codes(o)


def test_binding_mismatches_v01_v02_v03(obsset):
    o = obsset(); b = dict(o["source_binding"])
    assert "V-01" in codes(o, expected_binding={**b, "source_digest": "e" * 64})
    assert "V-02" in codes(o, expected_binding={**b, "frame_count": 4})
    assert "V-03" in codes(o, expected_binding={**b, "frame_table_digest": "e" * 64})


def test_meta_excluded_from_digest(obsset):
    a, b = obsset(), obsset(); b["meta"] = {"created_utc": "later"}
    assert schema.obsset_digest(a) == schema.obsset_digest(b)


def test_published_json_schema_is_current():
    path = Path(__file__).resolve().parents[1] / "schemas" / "mg.obsset.vs01.schema.json"
    assert json.loads(path.read_text()) == schema.json_schema()


def inference_fixture(obsset):
    """Hand-made structurally valid inference-origin FIXTURE (never stored or presented as a real run)."""
    from mineguard import taxonomy
    from mineguard.canon import digest
    from mineguard.detector import ADAPTER_ID, load_manifest_entry

    entry = load_manifest_entry()
    o = obsset("model_inference")
    params = {"stride": 3}
    o["producer"] = {"adapter_id": ADAPTER_ID, "adapter_version": "0", "model_id": entry["model_id"], "weights_sha256": entry["sha256"],
                     "params_hash": digest(params), "label_map_digest": taxonomy.label_map_digest(), "params": params}
    obj = o["frames"][0]["objects"][0]
    obj["cls"] = {"label": "vehicle", "state": "known", "detector_label": "truck", "detector_class_id": 8}
    obj["confidence"] = {"permille": 900, "kind": "model_uncalibrated"}
    return o


def test_inference_fixture_is_valid(obsset):
    validate_observation_set(inference_fixture(obsset))


def test_v09_requires_the_approved_adapter_model_weights_tuple(obsset):
    for key, val in (("adapter_id", "some-other-adapter"), ("model_id", "yolox-s"), ("weights_sha256", "0" * 64)):
        o = inference_fixture(obsset); o["producer"][key] = val
        assert "V-09" in codes(o), key


def test_v21_params_hash_must_match_params(obsset):
    o = inference_fixture(obsset); o["producer"]["params"]["stride"] = 4
    assert "V-21" in codes(o)


def test_v22_hash_format(obsset):
    o = inference_fixture(obsset); o["frames"][0]["pixel_sha256"] = "not-a-hash"
    assert "V-22" in codes(o)
    o = inference_fixture(obsset); o["source_binding"]["source_digest"] = "A" * 64  # uppercase is not canonical
    assert "V-22" in codes(o)


def test_v23_taxonomy_and_label_map_consistency(obsset):
    o = inference_fixture(obsset); o["frames"][0]["objects"][0]["cls"]["label"] = "person"
    assert "V-23" in codes(o)
    o = inference_fixture(obsset); o["frames"][0]["objects"][0]["cls"]["detector_label"] = "car"
    assert "V-23" in codes(o)
    o = inference_fixture(obsset); o["frames"][0]["objects"][0]["cls"]["detector_class_id"] = 9999
    assert "V-23" in codes(o)
    o = inference_fixture(obsset); o["producer"]["label_map_digest"] = "f" * 64
    assert "V-23" in codes(o)


def test_inference_confidence_must_be_marked_uncalibrated(obsset):
    o = inference_fixture(obsset); o["frames"][0]["objects"][0]["confidence"]["kind"] = "model_calibrated"
    assert "V-10" in codes(o)


def test_tracks_unsupported_and_identifier_constraints(obsset):
    o = obsset(); o["tracks"] = [{"track_id": "P1"}]
    assert codes(o)  # VS-01 has no tracker: V-08 track semantics are deferred, tracks must be empty
    o = obsset(); o["frames"][0]["objects"][0]["obj_key"] = "bad key!"
    assert codes(o)
    o = obsset(); o["source_binding"]["width"] = 0
    assert codes(o)
    o = obsset(); o["frames"][0]["frame_index"] = -1
    assert codes(o)


# ---- MG-RC1-005: coverage of a model_inference set (V-24) ----
def test_v24_inference_set_needs_analyzed_frames_matching_the_stride(obsset):
    o = inference_fixture(obsset); o["frames"] = []; o["frame_status_ranges"] = [{"from_frame": 0, "to_frame": 2, "status": "not_analyzed_stride"}]
    assert "V-24" in codes(o)  # zero analyzed frames: no model call could have happened
    o = inference_fixture(obsset); o["producer"]["params"]["stride"] = 2
    o["producer"]["params_hash"] = schema.digest(o["producer"]["params"])
    assert "V-24" in codes(o)  # frame 0 analyzed, but stride 2 also requires frame 2
    o = inference_fixture(obsset); del o["producer"]["params"]["stride"]; o["producer"]["params_hash"] = schema.digest(o["producer"]["params"])
    assert "V-24" in codes(o)
    o = inference_fixture(obsset); o["frame_status_ranges"][0]["status"] = "source_gap"
    assert "V-24" in codes(o)  # VS-01 inference never produces gaps or dropped frames
    # the zero-frame shape is legitimate for a replay set, which is never presented as inference
    r = obsset(); r["frames"] = []; r["frame_status_ranges"] = [{"from_frame": 0, "to_frame": 2, "status": "not_analyzed_stride"}]
    validate_observation_set(r)


# ---- MG-DATA-001-001: V-11 over the whole document, including meta and nested structures ----
@pytest.mark.parametrize("path,key", [
    (("meta",), "collision_prediction"),
    (("meta",), "distance_m"),
    (("meta",), "Speed_kmh"),
    (("meta",), " TTC_s"),
    (("meta",), "ｄistance"),          # fullwidth letter: NFKC-normalised before matching
    (("meta", "nested"), "ttc_estimate"),
    (("producer", "params"), "speed_kmh"),
    (("source_binding",), "distance_px"),
])
def test_v11_forbidden_fields_in_meta_and_nested_structures(obsset, path, key):
    o = obsset()
    target = o
    for p in path:
        target = target.setdefault(p, {})
    target[key] = 1
    assert "V-11" in codes(o)


def test_v11_forbidden_field_inside_lists_and_objects(obsset):
    o = obsset(); o["frames"][0]["objects"][0]["meta"] = [{"collision_risk": 1}]
    assert "V-11" in codes(o)
    o = obsset(); o["tracks"] = [{"x": [{"distance": 1}]}]
    assert "V-11" in codes(o)
    o = obsset(); o["frame_status_ranges"][0]["speed_mps"] = 3
    assert "V-11" in codes(o)


def test_meta_is_a_strict_schema(obsset):
    o = obsset(); o["meta"] = {"created_utc": "2026-10-24T00:00:00Z", "tool": "t", "notes": "n", "provenance_id": "prov_1",
                               "elapsed_ms": 5, "dropped_degenerate_boxes": 0}
    validate_observation_set(o)
    o = obsset(); o["meta"] = {"free_text_field": "anything"}
    assert "V-20" in codes(o)
    o = obsset(); o["meta"] = {"elapsed_ms": 1.5}
    assert "V-20" in codes(o)
