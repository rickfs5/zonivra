#!/usr/bin/env python3
"""Builds a Y4M test source for Chrome's fake media device (--use-file-for-fake-video-capture) from the REGISTERED clip: 640x360, 10 fps, I420.
A controlled TEST SOURCE: frames from a prerecorded file, never a physical camera. Usage: make_fake_camera.py OUT.y4m [frames]"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from mineguard import decode  # noqa: E402

out, n = Path(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 120
video = Path(__file__).resolve().parents[1] / "test-assets" / "pexels_6565442.mp4"
W, H = 640, 360


def i420(rgb):
    r, g, b = (rgb[..., i].astype(np.float32) for i in range(3))
    y = 0.299 * r + 0.587 * g + 0.114 * b
    u = (b - y) * 0.564 + 128
    v = (r - y) * 0.713 + 128
    sub = lambda c: c.reshape(H // 2, 2, W // 2, 2).mean(axis=(1, 3))
    clip = lambda c: np.clip(c + 0.5, 0, 255).astype(np.uint8)
    return clip(y).tobytes() + clip(sub(u)).tobytes() + clip(sub(v)).tobytes()


with out.open("wb") as f:
    f.write(f"YUV4MPEG2 W{W} H{H} F10:1 Ip A1:1 C420jpeg\n".encode())
    k = 0
    for fr in decode.iter_frames(str(video), 1280, 720):
        if fr.index % 3 == 0:
            f.write(b"FRAME\n" + i420(np.asarray(Image.fromarray(fr.rgb).resize((W, H)))))
            k += 1
        if k >= n:
            break
print(out, k, "frames")
