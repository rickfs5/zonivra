#!/usr/bin/env python3
"""Generates the ORIGINAL, synthetic, non-personal probe image proposed for the first SAM 3.1 call (a flat illustration of one stylised person on a plain scene; no photograph, no real
person, no third-party content). Offline and deterministic. Usage: python3 scripts/make_sam_probe_image.py [out.png]. Status: PROPOSED; nothing is sent anywhere."""
import hashlib, os, sys
os.environ.setdefault("MPLBACKEND", "Agg")
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyBboxPatch, Polygon, Rectangle

out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "..", "docs", "release", "sam_probe", "sam_probe_person_640x480.png")
fig = plt.figure(figsize=(6.4, 4.8), dpi=100)
ax = fig.add_axes([0, 0, 1, 1])
ax.set_xlim(0, 640); ax.set_ylim(480, 0); ax.axis("off")
ax.add_patch(Rectangle((0, 0), 640, 300, color="#cfe3f2"))                    # sky
ax.add_patch(Rectangle((0, 300), 640, 180, color="#b9a98a"))                  # ground
ax.add_patch(Polygon([(380, 300), (640, 300), (640, 210), (520, 150)], color="#9aa5ad"))   # a plain hill
ax.add_patch(Circle((320, 130), 32, color="#f1c9a5"))                          # head
ax.add_patch(FancyBboxPatch((284, 166), 72, 120, boxstyle="round,pad=2,rounding_size=14", color="#2d6cdf"))   # torso
ax.add_patch(Rectangle((290, 286), 24, 100, color="#2a2f45"))                  # legs
ax.add_patch(Rectangle((326, 286), 24, 100, color="#2a2f45"))
ax.add_patch(Rectangle((236, 176), 48, 20, color="#2d6cdf"))                   # arms
ax.add_patch(Rectangle((356, 176), 48, 20, color="#2d6cdf"))
ax.add_patch(Rectangle((282, 380), 34, 14, color="#222"))                      # feet
ax.add_patch(Rectangle((324, 380), 34, 14, color="#222"))
fig.savefig(out, dpi=100, format="png", metadata={"Software": None})
data = open(out, "rb").read()
print(out, len(data), "bytes sha256", hashlib.sha256(data).hexdigest())
