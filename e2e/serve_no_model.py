#!/usr/bin/env python3
"""Loopback test server for the SIMULATED camera acceptance (FINAL-ENGINEERING-15-R1). The real application with a DETECTOR FACTORY THAT FAILS for the first N creations and then returns a TEST DOUBLE that sees
nothing. No model is ever loaded, no frame is ever really analysed. Storage is a throwaway temp directory removed at exit. Usage: serve_no_model.py PORT [FAIL_FIRST]"""
import atexit
import shutil
import sys
import tempfile
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from mineguard.app import create_app  # noqa: E402
from mineguard.detector import load_manifest_entry  # noqa: E402

port, fail_first = int(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 10**9
td = Path(tempfile.mkdtemp(prefix="mg-sim-cam-"))
atexit.register(shutil.rmtree, td, ignore_errors=True)
(td / "runs").mkdir()
calls = {"n": 0}


class NullDetector:
    """TEST DOUBLE: returns no detections. Carries the provenance attributes a finished session records."""
    def __init__(self):
        e = load_manifest_entry()
        self.entry, self.weights_sha256, self.device, self.resolution = e, e["sha256"], "cpu", 384

    def predict(self, rgb, threshold):
        return []


def factory():
    calls["n"] += 1
    if calls["n"] <= fail_first:
        raise RuntimeError("model unavailable (simulated)")
    return NullDetector()


app = create_app(asset_dir=Path(__file__).resolve().parents[1] / "test-assets", runs_dir=td / "runs", detector_factory=factory)
uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
