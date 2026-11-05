"""Runtime provenance: exact versions of everything that touched the inference."""
from __future__ import annotations

import importlib.metadata as md
import platform
import sys

from . import netguard

PACKAGES = [
    "rfdetr", "torch", "torchvision", "transformers", "supervision", "numpy", "pillow", "safetensors", "huggingface_hub",
    "pyobjc-core", "pyobjc-framework-AVFoundation", "pyobjc-framework-CoreMedia", "pyobjc-framework-Quartz",
    "fastapi", "starlette", "uvicorn", "pydantic",
]


def runtime_provenance(device: str, decoder_id: str) -> dict:
    versions = {}
    for name in PACKAGES:
        try:
            versions[name] = md.version(name)
        except md.PackageNotFoundError:
            versions[name] = None
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "macos": platform.mac_ver()[0],
        "machine": platform.machine(),
        "device": device,
        "decoder": {"id": decoder_id, "note": "macOS system decoder; codec exposure is open gate G-6"},
        "packages": versions,
        "absent_by_policy": {name: _installed(name) is False for name in ("av", "cv2", "ultralytics", "rfdetr_plus")},
        "network_guard_installed": netguard.is_installed(),
    }


def _installed(name: str) -> bool:
    import importlib.util

    return importlib.util.find_spec(name) is not None
