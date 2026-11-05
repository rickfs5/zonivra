import copy
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
VIDEO = ROOT / "test-assets" / "pexels_6565442.mp4"
VIDEO_SHA256 = "7e46386505f9dd8bf1221e3eaba4eb7cd5a99ddc9ebd00f9aaef4a028cf4e3f6"
needs_video = pytest.mark.skipif(not VIDEO.is_file(), reason="test video not present")


@pytest.fixture(scope="session")
def detector():
    from mineguard.detector import Detector

    return Detector()


def fixture_obsset(origin="human_annotated"):
    """Hand-made FIXTURE for validator tests only. It is a replay-style set, never presented as inference."""
    base = {
        "schema": "mg.obsset.vs01",
        "source_binding": {"source_digest": "a" * 64, "frame_table_digest": "b" * 64, "frame_count": 3, "width": 100, "height": 50,
                           "native_width": 200, "native_height": 100},
        "coord_space": "image_px_v1", "taxonomy": "mg.cls.v0", "origin": origin,
        "producer": {"adapter_id": "replay", "adapter_version": "0", "model_id": None, "weights_sha256": None,
                     "params_hash": "c" * 64, "label_map_digest": None, "params": {}},
        "tracks": [],
        "frames": [{"frame_index": 0, "status": "analyzed", "pixel_sha256": "d" * 64, "objects": [
            {"obj_key": "o1", "cls": {"label": "person", "state": "known", "detector_label": "person", "detector_class_id": 1},
             "bbox": {"x": 0, "y": 10, "w": 20, "h": 30}, "truncated": {"left": True, "right": False, "top": False, "bottom": False},
             "confidence": {"permille": None, "kind": "human_asserted"}, "track_ref": None}]}],
        "frame_status_ranges": [{"from_frame": 1, "to_frame": 2, "status": "not_analyzed_stride"}],
        "meta": {},
    }
    return copy.deepcopy(base)


@pytest.fixture
def obsset():
    return fixture_obsset


def local_client(app):
    """TestClient that behaves like a legitimate local browser/script: loopback peer, allowed Host, session token header."""
    from fastapi.testclient import TestClient

    return TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000), headers={"X-MG-Token": app.state.session_token})


def zones_rev(client, url):
    """Current zone revision of an artifact (0 when there is none or the stored file is unreadable): tests that edit zones pass it as `expected_revision`."""
    r = client.get(url)
    return r.json().get("revision", 0) if r.status_code == 200 else 0


@pytest.fixture
def legacy_delete_enabled(monkeypatch):
    """MG-DELETE-035: the destructive session routes are disabled in the product. Tests of the DORMANT removal logic switch it on for their own isolated server only; test_product_vertical06 pins the disabled behaviour."""
    from mineguard import api_vs02
    monkeypatch.setattr(api_vs02, "SESSION_DELETE_ENABLED", True)
