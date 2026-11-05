"""Read-only aggregates for the operator dashboard and the developer dashboard (MG-VERTICAL-02).

Nothing here is estimated or invented: every figure comes from a counter that is maintained by the feed manager, the session manager or the alert engine, or from a
local OS measurement taken by `resource_sample()` (marked with its time). A metric that was not collected is reported as `null` with `measured: false`.
Four things are always kept apart and never added together: per-frame detections, unique tracks, risk events and notification deliveries.
No frame, thumbnail, token, credential or environment dump is ever included."""
from __future__ import annotations

import os
import subprocess
import threading
import time
from pathlib import Path

from . import __version__, overlap, safedir
from .alerts import API_VERSION as ALERT_API
from .feeds import current_risk, FEEDS_DIR, FEED_QUEUE_MAX, MAX_FEEDS, MAX_FPS, MIN_FPS, SPEEDS, VALIDATED_FEEDS
from .risk import evaluation as ev
from .risk.kernel import SEMANTICS_VERSION

_RES = {"at": 0.0, "doc": None}
_RES_LOCK = threading.Lock()
RISK_ORDER = {"RED": 3, "YELLOW": 2, "UNKNOWN": 1, "GREEN": 0}


def _run(cmd: list[str]) -> str | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return None


def resource_sample(max_age_s: float = 5.0) -> dict:
    """Local, read-only OS measurements (no network): this process RSS, host memory pressure level, free-memory percentage, swap, and the memory of the macOS decoder service.
    Cached for a few seconds so that polling dashboards cannot become a load themselves."""
    with _RES_LOCK:
        if _RES["doc"] is not None and time.monotonic() - _RES["at"] < max_age_s:
            return _RES["doc"]
        doc: dict = {"measured": True, "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        out = _run(["ps", "-o", "rss=", "-p", str(os.getpid())])
        doc["process_rss_mb"] = round(int(out.split()[0]) / 1024, 1) if out and out.split() else None
        lvl = _run(["sysctl", "-n", "kern.memorystatus_vm_pressure_level"])
        doc["host_pressure_level"] = int(lvl.strip()) if lvl and lvl.strip().isdigit() else None
        mp = _run(["memory_pressure"])
        free = None
        if mp:
            for line in mp.splitlines():
                if "free percentage" in line:
                    try:
                        free = int(line.split(":")[1].strip().rstrip("%"))
                    except (ValueError, IndexError):
                        pass
        doc["host_free_percent"] = free
        sw = _run(["sysctl", "-n", "vm.swapusage"])
        doc["swap_used_mb"] = None
        if sw:
            try:
                doc["swap_used_mb"] = round(float(sw.split("used =")[1].split("M")[0]), 1)
            except (IndexError, ValueError):
                pass
        ps = _run(["ps", "-axo", "rss=,comm="])
        dec = None
        if ps:
            dec = round(sum(int(l.split(None, 1)[0]) for l in ps.splitlines() if "VTDecoderXPCService" in l and l.split(None, 1)[0].isdigit()) / 1024, 1)
        doc["decoder_service_mb"] = dec
        _RES["at"], _RES["doc"] = time.monotonic(), doc
        return doc


def retention_counts(runs_dir: Path) -> dict:
    """Session privacy/retention status by lstat + the small JSON records only (no image is opened)."""
    import json
    import re
    out = {"frames": 0, "metadata_only": 0, "media_deleted": 0, "total": 0}
    try:
        names = [p for p in runs_dir.iterdir() if re.fullmatch(r"session_[0-9a-f]{16}", p.name)]
    except OSError:
        return out
    for p in names:
        try:
            if os.path.islink(p) or not p.is_dir():
                continue
            if (p / "media_deleted.json").is_file():
                k = "media_deleted"
            else:
                r = json.loads((p / "source.json").read_text()).get("retention")
                k = r["mode"] if isinstance(r, dict) and r.get("mode") in ("frames", "metadata_only") else "frames"
            out[k] += 1
            out["total"] += 1
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return out


_FEEDRUN_KEYS = {"schema", "id", "feed_id", "label", "source", "config", "counters", "latency_ms", "queue_wait_ms", "max_queue_depth", "tracks_unique", "class_hist", "events", "zones", "zones_revision",
                 "closed_reason", "error", "passes", "source_time_us", "producer", "origin", "trust", "retention", "started_utc", "ended_utc"}
_EV_KEYS = {"event_id", "zone_id", "rule_id", "severity", "start_us", "end_us", "closed_reason", "tracks", "right_censored", "left_censored"}
_SRC_KEYS = {"kind", "source_id", "video_id", "filename", "sha256_prefix", "repeated_source", "provenance", "physical_camera", "note"}


def _si(v, hi=10**12, none=False, lo=None):
    return (v is None and none) or (isinstance(v, int) and not isinstance(v, bool) and (-hi if lo is None else lo) <= v <= hi)


def _num(v, lo, hi):
    """A finite, in-range int or float (cycle-24 MG-STORE-024: NaN, infinities and absurd magnitudes are refused, not projected)."""
    import math
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and lo <= v <= hi


def _ss(v, n, none=False):
    return (v is None and none) or (isinstance(v, str) and len(v) <= n)


def _hist(d) -> bool:
    return isinstance(d, dict) and len(d) <= 32 and all(isinstance(k, str) and len(k) <= 40 and _si(v, 10**9) and v >= 0 for k, v in d.items())


def valid_feedrun(d) -> bool:
    """Strict, bounded, NO-EXTRA form of a saved feed summary read back from disk (cycle-22 MG-STORE-022, deepened in cycle 23 MG-STORE-023): every nested record has its exact members,
    closed vocabularies and bounds, so nothing that was not written by this program can come back out through the history API."""
    import re
    try:
        if not isinstance(d, dict) or not (_FEEDRUN_KEYS <= set(d) <= _FEEDRUN_KEYS | {"overlap"}) or d["schema"] != "mg.feedrun.v1" or not re.fullmatch(r"feedrun_[0-9a-f]{16}", d["id"]):
            return False
        sc = d["source"]
        if not (isinstance(sc, dict) and set(sc) == _SRC_KEYS and sc["kind"] == "mp4_file" and sc["provenance"] in ("real_inference", "repeated_source_simulation")
                and sc["physical_camera"] is False and isinstance(sc["repeated_source"], bool) and sc["repeated_source"] == (sc["provenance"] == "repeated_source_simulation")
                and all(_ss(sc[k], n) for k, n in (("source_id", 80), ("video_id", 60), ("filename", 120), ("sha256_prefix", 32), ("note", 300)))):
            return False
        if "overlap" in d and not overlap.valid_doc(d["overlap"]):          # MG-PROV-028: optional; a summary saved before it existed carries no interval evidence
            return False
        if d["origin"] != "model_inference_live_feed" or d["trust"] != "live_summary" or d["retention"] != {"mode": "metadata_only", "frames_persisted": False}:
            return False
        ev = d["events"]
        if not (isinstance(ev, list) and len(ev) <= 20000 and all(
                isinstance(e, dict) and set(e) == _EV_KEYS and e["severity"] in ("RED", "YELLOW") and _ss(e["event_id"], 40) and _ss(e["zone_id"], 32) and _ss(e["rule_id"], 40) and _si(e["start_us"])
                and _si(e["end_us"], none=True) and _ss(e["closed_reason"], 60, True) and isinstance(e["tracks"], list) and len(e["tracks"]) <= 20 and all(_ss(t, 60) for t in e["tracks"])
                and all(e[k] is None or isinstance(e[k], bool) for k in ("right_censored", "left_censored")) for e in ev)):
            return False
        c = d["counters"]
        cfg = d["config"]
        lat, qw, pr = d["latency_ms"], d["queue_wait_ms"], d["producer"]
        return (isinstance(c, dict) and len(c) <= 16 and all(isinstance(k, str) and 0 < len(k) <= 40 and _si(v, lo=0) for k, v in c.items())
                and isinstance(cfg, dict) and set(cfg) == {"sample_fps", "interval_ms", "threshold_permille", "mode", "speed", "loop"} and cfg["mode"] in ("realtime", "throttled") and isinstance(cfg["loop"], bool)
                and _num(cfg["sample_fps"], MIN_FPS, MAX_FPS) and _num(cfg["speed"], min(SPEEDS), max(SPEEDS)) and cfg["speed"] in SPEEDS
                and _si(cfg["interval_ms"], 10**6) and _si(cfg["threshold_permille"], 1000)
                and isinstance(lat, dict) and set(lat) == {"p50", "p95", "max"} and all(_si(v, 10**9, True, lo=0) for v in lat.values())
                and isinstance(qw, dict) and set(qw) == {"p50", "p95"} and all(_si(v, 10**9, True, lo=0) for v in qw.values())
                and isinstance(pr, dict) and set(pr) == {"adapter", "kernel", "tracker", "app"} and all(_ss(v, 60) for v in pr.values())
                and _hist(d["tracks_unique"]) and _hist(d["class_hist"]) and _si(d["max_queue_depth"], 1000) and _si(d["zones_revision"], 10**6) and _si(d["passes"], 10**9) and _si(d["source_time_us"], none=True)
                and isinstance(d["zones"], list) and len(d["zones"]) <= 8 and all(isinstance(z, dict) and set(z) == {"zone_id", "digest"} and _ss(z["zone_id"], 32) and _ss(z["digest"], 80) for z in d["zones"])
                and _ss(d["label"], 60) and isinstance(d["feed_id"], str) and re.fullmatch(r"feed_[0-9a-f]{8}", d["feed_id"]) is not None
                and _ss(d["closed_reason"], 60, True) and _ss(d["error"], 80, True) and _ss(d["started_utc"], 24, True) and _ss(d["ended_utc"], 24))
    except (KeyError, TypeError, AttributeError):
        return False


def project_feedrun(d: dict) -> dict:
    """The fixed projection of a VALIDATED saved summary that the API returns: members rebuilt one by one (nothing else can pass through)."""
    return {"schema": d["schema"], "id": d["id"], "feed_id": d["feed_id"], "label": d["label"], "source": {k: d["source"][k] for k in _SRC_KEYS}, "config": dict(d["config"]),
            "counters": dict(d["counters"]), "latency_ms": dict(d["latency_ms"]), "queue_wait_ms": dict(d["queue_wait_ms"]), "max_queue_depth": d["max_queue_depth"],
            "tracks_unique": dict(d["tracks_unique"]), "class_hist": dict(d["class_hist"]), "events": [{k: e[k] for k in _EV_KEYS} | {"tracks": list(e["tracks"]), "overlap_phase": (overlap.phase_of(e["start_us"], d["overlap"]) if "overlap" in d else overlap.legacy_phase(d["source"]["repeated_source"]))} for e in d["events"]],
            "zones": [{"zone_id": z["zone_id"], "digest": z["digest"]} for z in d["zones"]], "zones_revision": d["zones_revision"], "closed_reason": d["closed_reason"], "error": d["error"],
            "passes": d["passes"], "source_time_us": d["source_time_us"], "producer": dict(d["producer"]), "origin": d["origin"], "trust": d["trust"], "retention": dict(d["retention"]),
            "started_utc": d["started_utc"], "ended_utc": d["ended_utc"], "overlap": d.get("overlap")}


def feed_history_with_status(runs_dir: Path, limit: int = 50, granted: dict | None = None) -> tuple[list[dict], str]:
    """Saved feed summaries, read through the pinned owned directory. Status: ok | absent | unsafe (a symlinked or foreign `feeds` directory is reported, never followed).
    Every row is validated strictly and carries `trust`: `live_summary` ONLY when this process wrote exactly these bytes (its grant, like a session trust grant); anything else — written
    before a restart, edited, or planted — is `unverified` and is never counted as real history."""
    import hashlib
    import json
    import re
    try:
        fd = safedir.open_owned_dir(runs_dir, FEEDS_DIR, create=False)
    except FileNotFoundError:
        return [], "absent"
    except (safedir.UnsafeDir, OSError):
        return [], "unsafe"
    rows = []
    try:
        names = []
        for n in safedir.list_names(fd):
            if re.fullmatch(r"feedrun_[0-9a-f]{16}\.json", n):
                try:
                    st = safedir.lstat_leaf(fd, n)
                    names.append((st.st_mtime, n))
                except OSError:
                    continue
        for _, n in sorted(names, reverse=True)[:limit]:
            try:
                text = safedir.read_regular(fd, n)
                doc = json.loads(text)
                if valid_feedrun(doc) and n == doc["id"] + ".json":
                    row = project_feedrun(doc)
                    row["trust"] = "live_summary" if granted and granted.get(doc["id"]) == hashlib.sha256(text.encode()).hexdigest() else "unverified"
                    rows.append(row)
            except (OSError, ValueError, safedir.UnsafeDir):
                continue
    finally:
        os.close(fd)
    return rows, "ok"


def feed_history(runs_dir: Path, limit: int = 50, granted: dict | None = None) -> list[dict]:
    return feed_history_with_status(runs_dir, limit, granted)[0]


def worst(states: dict) -> str:
    best = "GREEN" if states else "UNKNOWN"
    for s in states.values():
        st = s.get("state", "UNKNOWN") if isinstance(s, dict) else s
        if RISK_ORDER.get(st, 1) > RISK_ORDER.get(best, 0):
            best = st
    return best


def trend(alerts: list[dict], minutes: int, bucket_min: int, now: float | None = None) -> dict:
    now = now or time.time()
    n = max(1, minutes // bucket_min)
    start = now - n * bucket_min * 60
    buckets = [{"from_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start + i * bucket_min * 60)), "RED": 0, "YELLOW": 0, "UNKNOWN": 0, "simulated": 0, "restored": 0} for i in range(n)]
    for a in alerts:
        t = a["created_epoch"]
        if start <= t < now:
            b = buckets[min(n - 1, int((t - start) // (bucket_min * 60)))]
            sim, restored = bool(a["simulation"]), bool(a.get("loaded_from_store"))
            b["simulated"] += 1 if sim else 0
            b["restored"] += 1 if restored else 0
            if not sim and not restored:
                b[a["severity"]] += 1                     # the severity series count GENUINE LIVE alerts only; simulated and restored-unverified ones are reported apart (cycle-25 MG-TREND-025)
    has = any(b["RED"] or b["YELLOW"] or b["UNKNOWN"] for b in buckets)
    return {"minutes": minutes, "bucket_minutes": bucket_min, "buckets": buckets, "has_data": has, "simulated_total": sum(b["simulated"] for b in buckets), "restored_total": sum(b["restored"] for b in buckets),
            "basis": "alert creation time (wall clock of this server); severity series = genuine live alerts only"}


def detector_label() -> dict:
    """The detector the feeds actually use, from the signed-off weights manifest; absent (None) if the manifest cannot be read. Says nothing about accuracy."""
    try:
        from .detector import load_manifest_entry
        e = load_manifest_entry()
        return {"model_id": e["model_id"], "architecture": e["architecture"]}
    except Exception:
        return {"model_id": None, "architecture": None}


def feed_identity(v: dict) -> dict:
    """MG-PRODUCT-VERTICAL-06: per-source facts for the operator tile, all taken from the feed's own view. Counts are those of the LAST analysed sample and only while that observation is fresh;
    otherwise they are absent (None), never zero and never carried over from an older sample."""
    obs = v["observation"]
    objs = v["latest"].get("objects") or []
    counts = None
    if obs["fresh"] and v["latest"].get("index") is not None and v["state"] in ("playing", "paused"):
        counts = {"person": sum(1 for o in objs if o["label"] == "person"), "vehicle": sum(1 for o in objs if o["label"] == "vehicle")}
    ov = v["overlap"]
    return {"source_id": v["source"]["source_id"], "source_kind": v["source"]["kind"], "filename": v["source"]["filename"], "observation": {"fresh": bool(obs["fresh"]), "age_ms": obs["age_ms"]},
            "lifecycle_last": v.get("lifecycle_last"), "last_observation_source_us": v["latest"].get("t_us"), "counts_latest": counts, "zone_ids": [z["zone_id"] for z in v["zones"] if z.get("kind") == "restricted"],
            "duplicate": overlap.duplicate_status(v["state"], bool(v["shared_now"]), v["launch_repeated"], bool(v["shared_during_run"]), bool(ov.get("truncated"))),
            }


def operator_overview(st, feeds, alerts, runs_dir: Path, minutes: int = 60, bucket_min: int = 5) -> dict:
    snap = feeds.snapshot()
    with alerts.lock:
        open_by_feed: dict[str, int] = {}
        for a in alerts.alerts.values():
            if a.get("feed_id") and a["status"] != "resolved" and not a.get("loaded_from_store"):
                open_by_feed[a["feed_id"]] = open_by_feed.get(a["feed_id"], 0) + 1      # grounded in the alert store; a restored record is history, never a live incident
    rows, ev_by_source, totals = [], {}, {"detections": 0, "tracks_unique": 0, "risk_events": {"RED": 0, "YELLOW": 0}, "risk_events_simulated_source": {"RED": 0, "YELLOW": 0}}
    for v in snap["feeds"]:
        sev = {"RED": 0, "YELLOW": 0}
        for e in v["events"]:
            if e["severity"] in sev:
                sev[e["severity"]] += 1
        sim = v["source"]["provenance"] != "real_inference"
        for k, n in sev.items():
            totals["risk_events_simulated_source" if sim else "risk_events"][k] += n
        totals["detections"] += v["counters"]["detections"]
        totals["tracks_unique"] += sum(v["tracks_unique"].values())
        unknown_zones = [z for z, s in v["risk_current"].items() if s["state"] == "UNKNOWN"]
        rows.append({"feed_id": v["feed_id"], "label": v["label"], "state": v["state"], "risk": v["risk_state"], "unknown_reason": v["unknown_reason"], "last_observed_risk": v["last_observed_risk"], "zones": {z: s["state"] for z, s in v["risk_current"].items()},
                     "unknown_zones": unknown_zones, "fps": v["fps"], "latency_ms": v["latency_ms"], "counters": {k: v["counters"][k] for k in ("analyzed", "dropped_queue_overflow", "dropped_stale", "adapter_error")},
                     "events": sev, "provenance": v["source"]["provenance"], "repeated_source": v["source"]["repeated_source"], "latest_event": v["latest_event"], "error": v["error"],
                     "unobserved_note": "UNKNOWN = not observed or not analysable; it is never GREEN",
                     **feed_identity(v), "incidents_open": open_by_feed.get(v["feed_id"], 0)})
        ev_by_source[v["label"] + " (" + v["feed_id"] + ")"] = {**sev, "provenance": v["source"]["provenance"]}
    hist = feed_history(runs_dir, 50, feeds.granted)
    hist_events, hist_unverified = {"RED": 0, "YELLOW": 0}, {"RED": 0, "YELLOW": 0}
    for h in hist:
        for e in h["events"]:
            if e["severity"] in hist_events:
                if h["trust"] == "live_summary" and h["source"]["provenance"] == "real_inference":
                    hist_events[e["severity"]] += 1
                else:
                    hist_unverified[e["severity"]] += 1          # written before a restart, edited or planted: history, never counted as real
    summary = alerts.summary()
    al = alerts.list(limit=500)["alerts"]
    allr = [a for a in alerts.alerts.values()]
    raw = [a for a in allr if not a.get("loaded_from_store")]                     # records read back from disk carry no delivery this process made
    deliveries = {"in_app_stored": len(raw), "in_app_displayed": sum(1 for a in raw if a["delivery"]["in_app"]["status"] == "displayed"),
                  **{ch: sum(1 for a in raw if a["delivery"][ch]["status"] == "delivered") for ch in ("browser", "sound")},
                  "failed": sum(1 for a in raw if a["delivery_state"] == "failed"), "loaded_from_disk_unverified": len(allr) - len(raw)}
    return {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "feeds": rows, "detector": detector_label(), "active_feeds": snap["active"], "active_limit": snap["active_limit"], "validated_feeds": VALIDATED_FEEDS,
            "counts_kept_apart": {"per_frame_detections": totals["detections"], "unique_tracks": totals["tracks_unique"], "risk_events_live": totals["risk_events"],
                                  "risk_events_from_simulated_sources": totals["risk_events_simulated_source"], "risk_events_in_saved_feed_history_real": hist_events, "risk_events_in_saved_feed_history_unverified": hist_unverified,
                                  "notification_deliveries": deliveries, "note": "detections are repeated per frame; a track is one trajectory; a risk event is one episode decided by the kernel; a delivery is one notification attempt"},
            "events_per_source": ev_by_source, "alerts": summary, "recent_timeline": [{k: a[k] for k in ("alert_id", "created_utc", "severity", "kind", "zone_id", "status", "simulation", "delivery_state", "loaded_from_store")} | {"source": a["source"]["label"], "provenance": a["source"]["provenance"]}
                                                                                       for a in al[:20]],
            "trend": trend(allr, minutes, bucket_min), "sessions_retention": retention_counts(runs_dir), "health": health(st, feeds, alerts), "saved_feed_runs": len(hist)}


def health(st, feeds, alerts) -> dict:
    snap = feeds.snapshot()
    errors = sum(1 for f in snap["feeds"] if f["state"] == "failed")
    return {"scheduler_alive": snap["scheduler"]["alive"], "scheduler_detector_error": snap["scheduler"]["detector_error"], "feeds_failed": errors, "alert_engine_counters": dict(alerts.counters),
            "alert_errors": feeds.sched_stats.get("alert_errors", 0), "model_loaded": st.detector is not None, "resources": resource_sample()}


def developer_overview(st, feeds, alerts, runs_dir: Path, doc_root: Path) -> dict:
    import json
    from .detector import load_manifest_entry
    snap = feeds.snapshot()
    d = st.detector
    try:
        entry = d.entry if d is not None else load_manifest_entry()
        model = {"model_id": entry["model_id"], "architecture": entry["architecture"], "weights_sha256_prefix": entry["sha256"][:16], "source_url": entry["source_url"],
                 "license_evidence": entry["license_evidence"], "loaded": d is not None, "device": getattr(d, "device", None), "resolution": int(getattr(d, "resolution", 0)) or None}
    except Exception:
        model = {"loaded": d is not None, "error": "model record unavailable"}
    per_feed = []
    for v in snap["feeds"]:
        c = v["counters"]
        per_feed.append({"feed_id": v["feed_id"], "state": v["state"], "source": v["source"], "frames": {"decoded": c["decoded"], "submitted": c["offered"], "accepted": c["offered"], "analyzed": c["analyzed"],
                                                                                                   "dropped": c["dropped_queue_overflow"] + c["dropped_stale"] + c["dropped_stopped"], "adapter_errors": c["adapter_error"],
                                                                                                   "skipped_by_sampling_intentional": c["skipped_by_sampling"]},
                         "inference_fps": v["fps"], "latency_ms": v["latency_ms"], "queue_wait_ms": v["queue_wait_ms"], "queue_depth": v["queue_depth"], "queue_max": v["queue_max"], "max_queue_depth": v["max_queue_depth"],
                         "class_distribution": v["class_hist"], "tracks_unique": v["tracks_unique"], "tracking_discontinuities": v["discontinuities"], "error": v["error"],
                         "trust": "live_summary (a feed summary is metadata only: it cannot be re-verified from images)", "config": v["config"], "zones_revision": v["zones_revision"]})
    state_file = doc_root / "docs" / "MG-ACCEPTANCE-STATE.json"
    try:
        acc = json.loads(state_file.read_text())
        acc["note"] = "recorded by scripts/acceptance_counts.py for the named commit; it can be older than the running code"
    except (OSError, ValueError):
        acc = {"available": False, "note": "no recorded acceptance state"}
    sess = st.session_mgr
    live = [s.view() for s in sess.sessions.values() if hasattr(s, "q") and getattr(s, "state", None) == "running"]
    return {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "model": model,
            "versions": {"app": __version__, "risk_kernel_engine": ev.ENGINE_VERSION, "risk_kernel_semantics": SEMANTICS_VERSION, "tracker": ev.TRACKER_VERSION, "alert_api": ALERT_API,
                         "feed_api": "mg.feed-api/1", "limits": {"max_feeds": MAX_FEEDS, "validated_feeds": VALIDATED_FEEDS, "feed_queue_max": FEED_QUEUE_MAX}},
            "scheduler": snap["scheduler"], "aggregate_inference_fps": snap["scheduler"]["aggregate_fps"], "feeds": per_feed,
            "webcam_live_sessions": [{"session_id": v["session_id"], "counters": v["counters"], "accounting": v.get("accounting"), "queue_depth": v["queue_depth"], "latency_ms": v["latency_ms"], "retention": v["retention"]} for v in live],
            "alerts": alerts.summary(), "resources": resource_sample(),
            "metrics_policy": "figures are counters kept by the running process since it started, or OS measurements stamped with their time; anything not collected is null", "acceptance_state": acc}
