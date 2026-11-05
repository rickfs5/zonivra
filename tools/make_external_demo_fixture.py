"""Regenerates the SYNTHETIC demo fixture for the external-result importer (MG-EVIDENCE-10). Deterministic and offline: stdlib only.
The stream is hand-written from the documented SAM 3.1 grammar; the image is a drawn placeholder. NOTHING here was produced by a Meta model."""
import hashlib
import json
import struct
import sys
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "backend" / "mineguard" / "demo_external"
W, H = 320, 240
STEM = "SYNTHETIC_FIXTURE_sam31"


def png() -> bytes:
    rows = bytearray()
    for y in range(H):
        rows.append(0)
        for x in range(W):
            px = (200, 205, 210) if y < 150 else (150, 135, 110)                     # sky / ground
            if 40 <= x < 230 and 90 <= y < 200:
                px = (230, 160, 30)                                                  # block standing in for a truck
            if 250 <= x < 300 and 60 <= y < 120:
                px = (60, 90, 160)
            rows += bytes(px)
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")


def stream() -> bytes:
    def rec(i, x1, y1, x2, y2, c):
        return f"{i}<|box;x1={x1};y1={y1};x2={x2};y2={y2};w={W};h={H};c={c}|><|mask;x=0;y=0;c={c};data=4,5,!aB|cD|>"
    delta = "<0f>" + rec(1, 40, 90, 230, 200, "0.91") + "," + rec(2, 250, 60, 300, 120, "0.55")
    ev = [("response.created", {"type": "response.created"}), ("response.output_text.delta", {"type": "response.output_text.delta", "delta": delta}),
          ("response.completed", {"type": "response.completed"})]
    return "".join(f"event: {n}\ndata: {json.dumps(d, separators=(',', ':'))}\n\n" for n, d in ev).encode()


def main():
    OUT.mkdir(exist_ok=True)
    img, res = png(), stream()
    man = {"schema": "mg.meta.import.manifest/1", "provenance_class": "SYNTHETIC_FIXTURE", "fixture_label": "SYNTHETIC_FIXTURE", "provider": "meta_sam31", "model": "sam-3.1",
           "concept": "truck", "captured_utc": "2026-11-02T00:00:00Z", "declared_by": "project fixture generator",
           "source_image": {"sha256": hashlib.sha256(img).hexdigest(), "width": W, "height": H},
           "response": {"sha256": hashlib.sha256(res).hexdigest(), "bytes": len(res), "http_status": 200, "content_type": "text/event-stream"}}
    (OUT / f"{STEM}.png").write_bytes(img)
    (OUT / f"{STEM}.sse").write_bytes(res)
    (OUT / f"{STEM}.manifest.json").write_text(json.dumps(man, indent=1, sort_keys=True) + "\n")
    print(json.dumps({"image": man["source_image"]["sha256"], "result": man["response"]["sha256"]}))


if __name__ == "__main__":
    sys.exit(main())
