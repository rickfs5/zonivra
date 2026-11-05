import importlib.util
import socket

import pytest

from mineguard import analysis, netguard, taxonomy
from mineguard.detector import RawDetection, WeightsError, load_manifest_entry, verify_weights


def test_integerize_clamps_flags_truncation_and_rounds_outward():
    b, t = analysis.integerize(RawDetection(1, 0.9, (-3.2, 10.4, 20.1, 30.9)), 100, 50)
    assert b == {"x": 0, "y": 10, "w": 21, "h": 21} and t["left"] and not t["right"]
    b, t = analysis.integerize(RawDetection(1, 0.9, (90.0, 0.0, 120.0, 50.0)), 100, 50)
    assert b["x"] + b["w"] == 100 and t["right"] and t["top"] and t["bottom"]


def test_integerize_drops_degenerate_boxes():
    assert analysis.integerize(RawDetection(1, 0.9, (10.0, 10.0, 10.0, 20.0)), 100, 50) is None
    assert analysis.integerize(RawDetection(1, 0.9, (200.0, 10.0, 210.0, 20.0)), 100, 50) is None


def test_permille_rounding_half_up_and_clamp():
    assert [analysis.permille(c) for c in (0.0, 0.0005, 0.9534, 1.2)] == [0, 1, 953, 1000]


def test_status_ranges_partition():
    r = analysis.status_ranges({0, 3, 6}, 8)
    assert r == [{"from_frame": 1, "to_frame": 2, "status": "not_analyzed_stride"},
                 {"from_frame": 4, "to_frame": 5, "status": "not_analyzed_stride"},
                 {"from_frame": 7, "to_frame": 7, "status": "not_analyzed_stride"}]


def test_taxonomy_mapping_and_no_excavator_class():
    assert taxonomy.map_class(1) == "person" and taxonomy.map_class(8) == "vehicle" and taxonomy.map_class(18) == "other"
    assert "excavator" not in {n.lower() for n in taxonomy.COCO_CLASSES.values()}


def test_network_guard_blocks_non_loopback():
    netguard.install()
    s = socket.socket()
    try:
        with pytest.raises(netguard.NetworkBlocked):
            s.connect(("93.184.216.34", 80))
    finally:
        s.close()


def test_weights_hash_mismatch_and_missing_refuse_without_download(tmp_path):
    entry = load_manifest_entry()
    with pytest.raises(WeightsError, match="missing"):
        verify_weights(entry, tmp_path)
    (tmp_path / entry["file"]).write_bytes(b"not the real weights")
    with pytest.raises(WeightsError, match="sha256 mismatch"):
        verify_weights(entry, tmp_path)


def test_prohibited_components_not_installed():
    for name in ("av", "cv2", "ultralytics", "rfdetr_plus"):
        assert importlib.util.find_spec(name) is None, f"{name} must not be installed (license/provenance gate)"
