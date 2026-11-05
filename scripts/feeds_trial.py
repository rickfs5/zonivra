#!/usr/bin/env python3
"""Genuine multi-feed capacity trial with the REAL RF-DETR Nano model on the authorized clip (MG-VERTICAL-02). Not part of the unit suite.
Several feeds replay the same file: this is a CONCURRENCY simulation (repeated source), never several cameras. It runs through the product's own HTTP API in an isolated
storage directory it creates and removes (ownership: only the directory this process made), and never touches the project's runs/.

Own watchdog (it only ever stops THIS process's feeds; no other process is signalled): the trial aborts and records why when, at any 1 s sample,
host free memory < 35 %, kernel pressure level >= 3, swap used grew by more than --max-swap-growth-mb over the starting value, or this process RSS > --max-rss-mb.
Usage: feeds_trial.py --feeds N --seconds S [--mode realtime|throttled] [--fps F] [--speed X] --out FILE.json"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

from mineguard.app import create_app  # noqa: E402
from mineguard.monitor import resource_sample  # noqa: E402


def pct(v, q):
    s = sorted(v)
    return s[min(len(s) - 1, int(q * len(s)))] if s else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--feeds", type=int, required=True)
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--mode", default="realtime")
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--max-swap-growth-mb", type=float, default=1500)
    ap.add_argument("--max-rss-mb", type=float, default=7000)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    scratch = Path(tempfile.mkdtemp(prefix="mg-feed-trial-", dir=os.environ.get("MG_TRIAL_TMP") or None))   # the system temporary directory unless told otherwise
    app = create_app(asset_dir=ROOT / "test-assets", runs_dir=scratch / "runs")
    c = TestClient(app, base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000), headers={"X-MG-Token": app.state.session_token})
    st = app.state.st
    vid = next((v for v in st.videos.values() if "error" not in v), None)
    if vid is None:
        print("no authorized clip registered")
        shutil.rmtree(scratch, ignore_errors=True)
        return 2
    base = resource_sample(0)
    samples, abort = [], {"reason": None}
    stop_watch = threading.Event()

    def watch():
        while not stop_watch.wait(1.0):
            r = resource_sample(0)
            samples.append({"t": round(time.monotonic() - t0, 1), **{k: r.get(k) for k in ("process_rss_mb", "host_pressure_level", "host_free_percent", "swap_used_mb", "decoder_service_mb")}})
            why = None
            if r["host_free_percent"] is not None and r["host_free_percent"] < 35:
                why = f"host free memory {r['host_free_percent']}% < 35%"
            elif r["host_pressure_level"] is not None and r["host_pressure_level"] >= 3:
                why = f"kernel pressure level {r['host_pressure_level']} >= 3"
            elif r["swap_used_mb"] is not None and base["swap_used_mb"] is not None and r["swap_used_mb"] - base["swap_used_mb"] > a.max_swap_growth_mb:
                why = f"swap grew {r['swap_used_mb'] - base['swap_used_mb']:.0f} MB > {a.max_swap_growth_mb:.0f} MB"
            elif r["process_rss_mb"] is not None and r["process_rss_mb"] > a.max_rss_mb:
                why = f"process RSS {r['process_rss_mb']} MB > {a.max_rss_mb} MB"
            if why:
                abort["reason"] = why
                st.feeds.stop_all()
                return

    zone = {"zone_id": "Z1", "kind": "restricted", "polygon": [[192, 288], [1024, 288], [1024, 684], [192, 684]], "name": "Zona de ensayo"}
    fids = []
    for n in range(a.feeds):
        r = c.post("/api/feeds", json={"video_id": vid["video_id"], "label": f"Ensayo {n + 1}", "sample_fps": a.fps, "threshold": 0.3, "mode": a.mode, "speed": a.speed, "loop": True, "zones": [zone]})
        assert r.status_code == 201, r.text
        fids.append(r.json()["feed_id"])
    if a.feeds > 3:
        assert c.post("/api/feeds/capacity", json={"limit": a.feeds, "acknowledge_unvalidated": True}).status_code == 200
    t0 = time.monotonic()
    th = threading.Thread(target=watch, daemon=True)
    th.start()
    started = time.time()
    for f in fids:
        r = c.post(f"/api/feeds/{f}/start")
        assert r.status_code == 200, r.text
    first_frame_s = None
    snaps = []
    while time.monotonic() - t0 < a.seconds and not abort["reason"]:
        time.sleep(1.0)
        v = c.get("/api/feeds").json()
        snaps.append({"t": round(time.monotonic() - t0, 1), "analyzed": [x["counters"]["analyzed"] for x in v["feeds"]], "depth": [x["queue_depth"] for x in v["feeds"]]})
        if first_frame_s is None and any(x["counters"]["analyzed"] for x in v["feeds"]):
            first_frame_s = round(time.monotonic() - t0, 1)
    wall = time.monotonic() - t0
    final = c.get("/api/feeds").json()
    dev = c.get("/api/monitor/developer").json()
    for f in fids:
        c.post(f"/api/feeds/{f}/stop")
    stop_watch.set()
    th.join(timeout=3)
    rows = []
    for x in final["feeds"]:
        k = x["counters"]
        rows.append({"feed_id": x["feed_id"], "state_at_end": x["state"], "analyzed": k["analyzed"], "offered": k["offered"], "dropped_overflow": k["dropped_queue_overflow"], "dropped_stale": k["dropped_stale"],
                     "adapter_error": k["adapter_error"], "analyzed_fps": round(k["analyzed"] / wall, 2) if wall else None, "fps_window": x["fps"], "latency_ms": x["latency_ms"],
                     "queue_wait_ms": x["queue_wait_ms"], "max_queue_depth": x["max_queue_depth"], "risk": {z: s["state"] for z, s in x["risk"].items()}, "error": x["error"], "closed_reason": x["closed_reason"], "passes": x["passes"], "class_hist": x["class_hist"],
                     "tracks_unique": x["tracks_unique"]})
    an = [r["analyzed"] for r in rows]
    out = {"schema": "mg.feeds-trial.v1", "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "feeds": a.feeds, "mode": a.mode, "sample_fps": a.fps, "speed": a.speed, "seconds_requested": a.seconds,
           "wall_seconds": round(wall, 1), "source": {"kind": "mp4_file", "repeated_source_simulation": a.feeds > 1, "physical_cameras": 0, "clip": vid["filename"]}, "seconds_to_first_analyzed_frame": first_frame_s,
           "aborted_by_watchdog": abort["reason"], "aggregate_analyzed": sum(an), "aggregate_analyzed_fps": round(sum(an) / wall, 2) if wall else None,
           "fairness": {"analyzed_per_feed": an, "max_over_min": round(max(an) / min(an), 2) if an and min(an) else None, "scheduler": final["scheduler"]},
           "per_feed": rows, "resource_start": base, "resource_peak": {"process_rss_mb": max((s["process_rss_mb"] or 0) for s in samples) if samples else None,
                                                                         "decoder_service_mb": max((s["decoder_service_mb"] or 0) for s in samples) if samples else None,
                                                                         "swap_used_mb_max": max((s["swap_used_mb"] or 0) for s in samples) if samples else None,
                                                                         "host_free_percent_min": min((s["host_free_percent"] for s in samples if s["host_free_percent"] is not None), default=None),
                                                                         "pressure_level_max": max((s["host_pressure_level"] or 0) for s in samples) if samples else None},
           "resource_samples": samples, "progress": snaps, "developer_dashboard_model": dev["model"]}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, indent=1))
    st.feeds.stop_all()
    shutil.rmtree(scratch, ignore_errors=True)   # the directory THIS process created
    print(json.dumps({k: out[k] for k in ("feeds", "mode", "wall_seconds", "aborted_by_watchdog", "aggregate_analyzed", "aggregate_analyzed_fps", "fairness")}, indent=1)[:1500])
    return 3 if abort["reason"] else 0


if __name__ == "__main__":
    sys.exit(main())
