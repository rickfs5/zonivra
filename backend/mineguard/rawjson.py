"""Deterministic digest for provider payloads that may contain floats (the canonical profile of mineguard.canon refuses floats)."""
from __future__ import annotations

import hashlib
import json
import math


def _san(v):
    """Non-finite floats (a hostile or broken provider response) are digested as tagged strings instead of crashing or being silently dropped."""
    if isinstance(v, float) and not math.isfinite(v):
        return {"__nonfinite_float__": repr(v)}
    if isinstance(v, dict):
        return {k: _san(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_san(x) for x in v]
    return v


def raw_bytes(value) -> bytes:
    return json.dumps(_san(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def raw_digest(value) -> str:
    return hashlib.sha256(raw_bytes(value)).hexdigest()
