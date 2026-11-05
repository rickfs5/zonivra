"""RF-DETR Nano adapter. Verifies the weights SHA-256 against models/MANIFEST.json before any load and refuses to download."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .canon import file_sha256, read_json_strict

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "models" / "MANIFEST.json"
MODEL_ID = "rf-detr-nano-coco"
ADAPTER_ID = "rfdetr-nano"
# Approved-model record (complete: identity, checkpoint, source and licence evidence), kept in tracked source instead of only in the
# editable data file. models/MANIFEST.json must equal it exactly, so changing the approved checkpoint or any asserted metadata needs
# a visible source change. This is a LOCAL CONSISTENCY anchor, not authenticity: whoever can edit this file can edit the weights,
# the manifest and every run file too. Signing or an external trust root is deliberately out of scope (owner decision, Night Run 03).
APPROVED_MODEL = {'model_id': 'rf-detr-nano-coco',
 'architecture': 'RF-DETR Nano (rfdetr==1.11.2, class RFDETRNano)',
 'file': 'rf-detr-nano.pth',
 'sha256': 'd8d6b9ee57d4d0ed2b1f305163624712a0532cb7bce0c747317984fc5457440d',
 'md5_published_by_package': 'fb6504cce7fbdc783f7a46991f07639f',
 'bytes': 366287238,
 'source_url': 'https://storage.googleapis.com/rfdetr/nano_coco/checkpoint_best_regular.pth',
 'source_url_origin': 'rfdetr/assets/model_weights.py (RF_DETR_NANO) in the installed rfdetr==1.11.2 package',
 'retrieved_utc': '2026-10-24T06:52:38Z',
 'license_evidence': {'weights_license_claim': 'Apache-2.0 (RF-DETR-N row of the upstream README model table)',
                      'readme_url': 'https://github.com/roboflow/rf-detr/blob/develop/README.md',
                      'code_license': 'Apache-2.0 (upstream LICENSE; PyPI metadata rfdetr 1.11.2)',
                      'excluded': 'rfdetr_plus and PML 1.0 variants (Atto, Femto, Pico, XL, 2XL) are not used',
                      'backbone': 'DINOv2 vision transformer; facebookresearch/dinov2 README states code and model '
                                  'weights are Apache-2.0',
                      'training_data': 'COCO; image and annotation terms not independently reviewed (OPEN)',
                      'status': 'APPROVED-WITH-CONDITIONS by Night Run 01 authorization; not legal clearance; patent '
                                'freedom-to-operate not assessed'}}


class WeightsError(Exception):
    msg_id = "mg.refusal.weights_unverified"


@dataclass(frozen=True)
class RawDetection:
    class_id: int
    confidence: float
    xyxy: tuple[float, float, float, float]


def load_manifest_entry(model_id: str = MODEL_ID, manifest_path: Path | None = None) -> dict:
    data = read_json_strict(manifest_path or MANIFEST_PATH)
    for entry in data["models"]:
        if entry["model_id"] == model_id:
            if model_id == APPROVED_MODEL["model_id"] and entry != APPROVED_MODEL:
                raise WeightsError("models/MANIFEST.json does not equal the approved-model record in detector.py")
            return entry
    raise WeightsError(f"model {model_id} not in manifest")


def verify_weights(entry: dict, models_dir: Path | None = None) -> Path:
    path = (models_dir or MANIFEST_PATH.parent) / entry["file"]
    if not path.is_file():
        raise WeightsError(f"weights file missing: {path.name} (automatic download is disabled)")
    actual = file_sha256(path)
    if actual != entry["sha256"]:
        raise WeightsError(f"weights sha256 mismatch: expected {entry['sha256']}, got {actual}")
    return path


class Detector:
    def __init__(self, entry: dict | None = None, device: str | None = None, models_dir: Path | None = None):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        self.entry = entry or load_manifest_entry()
        self.weights_path = verify_weights(self.entry, models_dir)
        self.weights_sha256 = self.entry["sha256"]
        import torch
        from rfdetr import RFDETRNano

        self.device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
        self.model = RFDETRNano(pretrain_weights=str(self.weights_path), device=self.device)
        self.resolution = int(getattr(self.model.model_config, "resolution", 0) or 0)

    def predict(self, rgb: np.ndarray, threshold: float) -> list[RawDetection]:
        from PIL import Image

        det = self.model.predict(Image.fromarray(rgb), threshold=threshold)
        return [
            RawDetection(int(k), float(c), tuple(float(v) for v in b))
            for k, c, b in zip(det.class_id, det.confidence, det.xyxy)
        ]
