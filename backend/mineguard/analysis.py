"""MP4 -> decoded frames -> RF-DETR -> ObservationSet + evidence frames + export bundle."""
from __future__ import annotations

import os
import threading
import json
import math
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from PIL import Image

from . import __version__, decode, taxonomy
from .canon import digest, digest16, file_sha256, sha256_hex
from .detector import ADAPTER_ID, RawDetection
from .provenance import runtime_provenance
from .schema import SCHEMA_ID, obsset_digest, validate_observation_set
from .canon import read_json_strict
from .timebase import check_strictly_increasing, pts_us

DECODE_W, DECODE_H = 1280, 720
DEFAULT_RUNS_DIR = Path(__file__).resolve().parents[2] / "runs"


def configured_runs_dir(env: dict | None = None) -> Path:
    """Where runs are stored. MG_RUNS_DIR (used by the acceptance scripts to isolate their storage from the user's runs, MG-PROC-002) must be an
    absolute path without '..' that is not a symlink; anything else stops the server instead of silently falling back to the user's runs."""
    raw = (env if env is not None else os.environ).get("MG_RUNS_DIR")
    if not raw:
        return DEFAULT_RUNS_DIR
    p = Path(raw)
    if not p.is_absolute() or ".." in p.parts or (p.exists() and (p.is_symlink() or not p.is_dir())):
        raise RuntimeError("MG_RUNS_DIR must be an absolute path to a real directory (no '..', no symlink)")
    return p


RUNS_DIR = configured_runs_dir()
ADAPTER_VERSION = __version__

# Evidence rendering contract (MG-RC1-007). The evidence image is a lossy JPEG rendering of the decoded RGB frame that was
# given to the model. Equality of bytes or pixels is NOT claimed; deep verification checks that the stored JPEG, once decoded,
# is the same picture as the re-decoded source frame within these tolerances (measured on the clip: PSNR >= 39 dB,
# mean abs error <= 2.1, worst 16x16 tile mean abs error 6.7 levels). It detects substitution and edits larger than the
# tile tolerance; it does not detect subtle local edits below it.
EVIDENCE_CONTRACT = {"id": "mg.evidence.jpeg_q90_v1", "format": "JPEG", "jpeg_quality": 90, "width": DECODE_W, "height": DECODE_H,
                     "max_mean_abs_error": 4.0, "min_psnr_db": 34.0, "tile": 16, "max_tile_mean_abs_error": 14.0}


class Cancelled(Exception):
    pass


_ARTIFACT_LOCKS: dict[str, threading.Lock] = {}
_ARTIFACT_LOCKS_GUARD = threading.Lock()


def artifact_lock(name: str) -> threading.Lock:
    """ONE lock per stored artifact name, shared by every writer of that directory (zone edits, content-addressed installation, deletion): a zone edit can never be lost to a concurrent
    install that copied the previous revision (cycle-18 MG-ZONE-018)."""
    with _ARTIFACT_LOCKS_GUARD:
        return _ARTIFACT_LOCKS.setdefault(name, threading.Lock())


def install_dir(tmp: Path, final: Path, new_obsset_digest: str):
    """Moves a freshly written run/session directory into its content-addressed place. Never follows a symlink, never replaces a directory whose content digest
    differs (a 64-bit id collision or a foreign directory is REFUSED), and never moves the only copy of user data: the user's zone edits in <final>/risk are COPIED
    into the new directory, the old directory is renamed aside, the new one renamed in, and only then is the old one removed (rolled back on failure)."""
    if final.is_symlink() or (final.exists() and not final.is_dir()):
        raise RuntimeError("mg.refusal.run_destination_unsafe")
    with artifact_lock(final.name):
        _install_locked(tmp, final, new_obsset_digest)


def _install_locked(tmp: Path, final: Path, new_obsset_digest: str):
    if not final.exists():
        os.rename(tmp, final)
        return
    try:
        old_digest = obsset_digest(read_json_strict(final / "observations.json"))
    except Exception:
        old_digest = None
    if old_digest != new_obsset_digest:
        raise RuntimeError("mg.refusal.run_id_collision")
    old_risk = final / "risk"
    if old_risk.is_symlink():
        raise RuntimeError("mg.refusal.run_destination_unsafe")
    if old_risk.is_dir():
        for root, dirs, files in os.walk(old_risk, followlinks=False):
            if any(os.path.islink(os.path.join(root, n)) for n in dirs + files):
                raise RuntimeError("mg.refusal.run_destination_unsafe")
        if (tmp / "risk").exists():
            shutil.rmtree(tmp / "risk")  # the user's zone edits win over the freshly generated defaults
        shutil.copytree(old_risk, tmp / "risk", symlinks=False)
    aside = final.with_name(f"_tmp_{uuid.uuid4().hex[:8]}")
    os.rename(final, aside)
    try:
        os.rename(tmp, final)
    except BaseException:
        os.rename(aside, final)
        raise
    shutil.rmtree(aside, ignore_errors=True)


def integerize(det: RawDetection, width: int, height: int):
    """Adapter integerization (L-5): floor/ceil outward, clamp to the frame, flag edge truncation, drop degenerate boxes."""
    x1, y1, x2, y2 = det.xyxy
    x0, y0 = max(0, math.floor(x1)), max(0, math.floor(y1))
    xe, ye = min(width, math.ceil(x2)), min(height, math.ceil(y2))
    if xe - x0 < 1 or ye - y0 < 1:
        return None
    bbox = {"x": x0, "y": y0, "w": xe - x0, "h": ye - y0}
    trunc = {"left": x1 <= 0, "right": x2 >= width, "top": y1 <= 0, "bottom": y2 >= height}
    return bbox, trunc


def permille(conf: float) -> int:
    return max(0, min(1000, int(conf * 1000 + 0.5)))


def build_objects(dets: list[RawDetection], width: int, height: int) -> tuple[list[dict], int]:
    items, dropped = [], 0
    for d in dets:
        r = integerize(d, width, height)
        if r is None:
            dropped += 1
            continue
        bbox, trunc = r
        items.append((-permille(d.confidence), bbox["x"], bbox["y"], d.class_id, bbox, trunc, d))
    items.sort(key=lambda t: t[:4])
    objs = []
    for n, (_, _, _, _, bbox, trunc, d) in enumerate(items, 1):
        objs.append({
            "obj_key": f"o{n}",
            "cls": {"label": taxonomy.map_class(d.class_id), "state": "known",
                    "detector_label": taxonomy.coco_label(d.class_id), "detector_class_id": d.class_id},
            "bbox": bbox, "truncated": trunc,
            "confidence": {"permille": permille(d.confidence), "kind": "model_uncalibrated"},
            "track_ref": None,
        })
    return objs, dropped


def status_ranges(analyzed: set[int], frame_count: int) -> list[dict]:
    ranges, start = [], None
    for i in range(frame_count + 1):
        missing = i < frame_count and i not in analyzed
        if missing and start is None:
            start = i
        elif not missing and start is not None:
            ranges.append({"from_frame": start, "to_frame": i - 1, "status": "not_analyzed_stride"})
            start = None
    return ranges


def evidence_matches_frame(jpeg_path: Path, rgb) -> tuple[bool, str]:
    """Rendering-contract check: the decoded evidence JPEG must be the re-decoded source frame within EVIDENCE_CONTRACT."""
    import numpy as np

    c = EVIDENCE_CONTRACT
    with Image.open(jpeg_path) as im:
        if im.format != c["format"]:
            return False, f"format {im.format}"
        img = np.asarray(im.convert("RGB"), dtype=np.int16)
    if img.shape != rgb.shape or img.shape != (c["height"], c["width"], 3):
        return False, f"shape {img.shape}"
    d = np.abs(img - rgb.astype(np.int16))
    mse = float((d.astype(np.float64) ** 2).mean())
    psnr = 99.0 if mse == 0 else 10 * math.log10(255 ** 2 / mse)
    t = c["tile"]
    tile_mae = float(d.reshape(c["height"] // t, t, c["width"] // t, t, 3).mean(axis=(1, 3, 4)).max())
    ok = float(d.mean()) <= c["max_mean_abs_error"] and psnr >= c["min_psnr_db"] and tile_mae <= c["max_tile_mean_abs_error"]
    return ok, f"mae={d.mean():.2f} psnr={psnr:.1f} worst_tile_mae={tile_mae:.2f}"


def pixel_digest(rgb) -> str:
    return sha256_hex(rgb.tobytes())  # RGB24, row-major, top-left origin


def build_params(thr_permille: int, stride: int, resolution: int, device: str) -> dict:
    return {"threshold_permille": thr_permille, "stride": stride, "resolution": int(resolution),
            "decode_width": DECODE_W, "decode_height": DECODE_H, "device": device}


def build_frame_table(pts_ticks: list[int], timescale: int) -> dict:
    n = len(pts_ticks)
    return {"time_base": {"num": 1, "den": timescale}, "start_ticks": pts_ticks[0], "pts_ticks": pts_ticks,
            "last_frame_duration_ticks": (pts_ticks[-1] - pts_ticks[-2]) if n > 1 else 0,
            "width": DECODE_W, "height": DECODE_H, "rotation": 0}


def build_source_doc(info, pts_ticks: list[int], timescale: int, source_digest: str) -> dict:
    """The source document is a pure function of the decoder's output (probe + frame table) and the file digest."""
    ft = build_frame_table(pts_ticks, timescale)
    return {"source_id": "src_" + digest16(source_digest), "kind": "file", "source_digest": source_digest,
            "media": {"container": info.container, "codec_fourcc": info.codec_fourcc, "native_width": info.native_width,
                      "native_height": info.native_height, "analysis_width": DECODE_W, "analysis_height": DECODE_H,
                      "nominal_fps": round(info.nominal_fps, 3), "duration_s": round(info.duration_s, 3), "frame_count": len(pts_ticks)},
            "frame_table": ft, "frame_table_provider": {"id": decode.DECODER_ID, "version": "macOS"},
            "frame_table_digest": digest(ft), "calibration": None}


def build_evidence_entry(frame_index: int, pts_ticks: list[int], timescale: int, file_sha256_hex: str, pixel_sha256: str) -> dict:
    return {"frame_index": frame_index, "pts_us": pts_us(pts_ticks, frame_index, 1, timescale),
            "file": f"frames/{frame_index:06d}.jpg", "file_sha256": file_sha256_hex, "pixel_sha256": pixel_sha256,
            "pixels_origin": "source_frame_decoded"}


def build_manifest(run_id: str, source_digest: str, ft_digest: str, od: str, evidence: list[dict]) -> dict:
    return {"schema": "mg.manifest.vs01", "run_id": run_id, "mode": "batch_inference",
            "digests": {"source": source_digest, "frame_table": ft_digest, "obsset": od},
            "origin_summary": ["model_inference"], "evidence_contract": EVIDENCE_CONTRACT, "evidence": evidence,
            "limitations": ["mg.lim.image_space_only", "mg.lim.uncalibrated_confidence", "mg.lim.coco_no_excavator_class",
                            "mg.lim.not_safety_system"]}


def build_provenance(device: str, entry: dict) -> dict:
    prov = runtime_provenance(device, decode.DECODER_ID)
    prov["model"] = {"model_id": entry["model_id"], "architecture": entry["architecture"], "weights_sha256": entry["sha256"],
                     "source_url": entry["source_url"], "license_evidence": entry["license_evidence"]}
    return prov


def run_analysis(video_path: str, source_digest: str, detector, stride: int, threshold: float,
                 progress: Callable[[int, int], None] | None = None, cancelled: Callable[[], bool] | None = None,
                 runs_dir: Path = RUNS_DIR, post_check: Callable[[], None] | None = None) -> dict:
    if stride < 1 or not 0.0 < threshold < 1.0:
        raise ValueError("stride >= 1 and 0 < threshold < 1 required")
    thr_permille = permille(threshold)  # quantized ONCE: this exact value is used for inference, storage, hashing and verification
    if not 1 <= thr_permille <= 999:
        raise ValueError("threshold must round to 1..999 permille")
    threshold = thr_permille / 1000
    info = decode.probe(video_path)
    est_total = int(round(info.duration_s * info.nominal_fps))
    tmp = runs_dir / f"_tmp_{uuid.uuid4().hex[:8]}"
    (tmp / "frames").mkdir(parents=True)
    t0 = time.time()
    pts_ticks: list[int] = []
    frames: list[dict] = []
    dropped_degenerate = 0
    timescale = info.timescale
    try:
        for fr in decode.iter_frames(video_path, DECODE_W, DECODE_H):
            if cancelled and cancelled():
                raise Cancelled()
            timescale = fr.timescale
            pts_ticks.append(fr.pts_ticks)
            if fr.index % stride == 0:
                h, w, _ = fr.rgb.shape
                objs, dropped = build_objects(detector.predict(fr.rgb, threshold), w, h)
                dropped_degenerate += dropped
                Image.fromarray(fr.rgb).save(tmp / "frames" / f"{fr.index:06d}.jpg", quality=EVIDENCE_CONTRACT["jpeg_quality"])
                frames.append({"frame_index": fr.index, "status": "analyzed", "pixel_sha256": pixel_digest(fr.rgb), "objects": objs})
            if progress:
                progress(fr.index + 1, max(est_total, fr.index + 1))
        n = len(pts_ticks)
        if n == 0:
            raise decode.UnsupportedMedia("no frames decoded")
        check_strictly_increasing(pts_ticks)
        source = build_source_doc(info, pts_ticks, timescale, source_digest)
        ft_digest = source["frame_table_digest"]
        params = build_params(thr_permille, stride, detector.resolution, detector.device)
        obs = {
            "schema": SCHEMA_ID,
            "source_binding": {"source_digest": source_digest, "frame_table_digest": ft_digest, "frame_count": n,
                               "width": DECODE_W, "height": DECODE_H,
                               "native_width": info.native_width, "native_height": info.native_height},
            "coord_space": "image_px_v1", "taxonomy": taxonomy.TAXONOMY, "origin": "model_inference",
            "producer": {"adapter_id": ADAPTER_ID, "adapter_version": ADAPTER_VERSION, "model_id": detector.entry["model_id"],
                         "weights_sha256": detector.weights_sha256, "params_hash": digest(params),
                         "label_map_digest": taxonomy.label_map_digest(), "params": params},
            "tracks": [], "frames": frames, "frame_status_ranges": status_ranges({f["frame_index"] for f in frames}, n),
            "meta": {"created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "tool": f"mineguard-vs01/{__version__}",
                     "elapsed_ms": int((time.time() - t0) * 1000), "dropped_degenerate_boxes": dropped_degenerate},
        }
        validate_observation_set(obs)
        od = obsset_digest(obs)
        run_id = "run_" + digest16(od)
        evidence = [build_evidence_entry(f["frame_index"], pts_ticks, timescale, file_sha256(tmp / "frames" / f"{f['frame_index']:06d}.jpg"),
                                         f["pixel_sha256"]) for f in frames]
        provenance = build_provenance(detector.device, detector.entry)
        manifest = build_manifest(run_id, source_digest, ft_digest, od, evidence)
        bundle = {"schema": "mg.export.vs01", "observation_set": obs, "source": source, "provenance": provenance, "manifest": manifest}
        for name, doc in (("observations.json", obs), ("source.json", source), ("provenance.json", provenance),
                          ("manifest.json", manifest), ("export.json", bundle)):
            (tmp / name).write_text(json.dumps(doc, indent=1, ensure_ascii=False))
        final = runs_dir / run_id
        if post_check:
            post_check()  # e.g. the source file must still be the analyzed one; raising here leaves NOTHING installed and never touches <final>
        install_dir(tmp, final, od)
        return {"run_id": run_id, "obsset_digest": od, "frames_decoded": n, "frames_analyzed": len(frames),
                "elapsed_ms": obs["meta"]["elapsed_ms"]}
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
