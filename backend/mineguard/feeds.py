"""Multi-feed video monitoring (MG-VERTICAL-02, checkpoint B).

N independent VIDEO feeds (authorized local MP4 files) are analysed by ONE shared RF-DETR Nano instance through ONE inference worker:
  reader thread per feed  ->  bounded per-feed queue (2)  ->  fair scheduler (least recently served first)  ->  detector  ->  commit (freshness judged at the commit)
  ->  that feed's OWN tracker, zone engines and risk kernel  ->  events  ->  alert engine.
Nothing is shared between feeds except the model and the worker: tracker ids, zone engines, counters, queues, events and failures are per feed, and every published item carries its
feed id. One decoded frame per sampling interval is offered; the rest are counted as skipped by sampling (intentional), never as lost.

Honesty: a feed built from a file that another feed also plays is flagged `repeated_source` (a concurrency simulation, NOT several cameras). Physical/browser camera sources are an
interface only (`BrowserCameraSource`): they are not activated in this increment. No frame is ever written to disk by a feed: the latest ANALYSED frame of a FILE source is kept in
memory for the operator preview and replaced by the next one."""
from __future__ import annotations

import collections
import hashlib
import io
import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Iterator

import numpy as np
from PIL import Image

from . import __version__, decode, overlap, safedir
from .analysis import DECODE_H, DECODE_W, build_objects, permille
from .risk import evaluation as ev
from .risk.kernel import ZoneEngine
from .risk.tracker import IouTracker
from .risk.zone_eval import make_zone, sample_for

MAX_FEEDS = 5                 # feeds that may exist at once (hard cap)
VALIDATED_FEEDS = 3           # the highest feed count with recorded acceptance evidence; more needs an explicit acknowledgement (MG-VERTICAL2 capacity notes)
DEFAULT_ACTIVE_LIMIT = 3
FEED_QUEUE_MAX = 2            # per-feed bounded queue
SOURCE_OPEN_RETRIES = 3       # a source that fails BEFORE its first frame (e.g. the system decoder refusing a concurrent start) is retried this many times, then the feed fails
SOURCE_RETRY_DELAY_S = 0.4
FEED_MAX_AGE_US = 3_000_000   # a decoded frame older than this (wall clock) at the COMMIT is stale and is recorded as dropped, never shown as current
MIN_FPS, MAX_FPS = 0.2, 10.0
SPEEDS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0)
MODES = ("realtime", "throttled")   # realtime: paced by the source clock, overload drops (stale/overflow); throttled: backpressure slows the file, nothing is dropped (deterministic)
SOURCE_KINDS = ("mp4_file", "browser_camera")
FEED_STATES = ("idle", "playing", "paused", "ended", "failed", "stopped")
LATENCY_WINDOW = 512
MAX_TRACK_IDS = 5000
LIFE_CAP_LIFECYCLE = 100       # V07: recorded source lifecycle facts per run (start/pause/resume/stop/fail are few; the cap only bounds a pathological pause loop)
LIFE_CAP_OBSERVED = 200        # V07: recorded observation edges and risk changes per run
LIFE_KEEP_RUNS = 50            # V07: lifecycle records of finished runs kept in memory so an incident of a restarted feed still shows ITS run (not persisted across a server restart)
FEEDS_DIR = "feeds"
FEED_ID_RE = r"^feed_[0-9a-f]{8}$"
FEEDRUN_RE = r"^feedrun_[0-9a-f]{16}$"


class FeedError(Exception):
    def __init__(self, status: int, msg_id: str):
        super().__init__(msg_id)
        self.status, self.msg_id = status, msg_id


class BrowserCameraSource:
    """Interface reserved for several browser camera devices (architecture only). Activating it needs a human-executed browser-permission test: it refuses here."""
    kind = "browser_camera"

    def __init__(self, *a, **k):
        raise FeedError(501, "mg.refusal.feed_source_not_available")


STALE_OBSERVATION_S = 8.0   # a playing feed whose newest analysed sample is older than this has no CURRENT risk


def current_risk(feed_state: str, last: str) -> str:
    """The CURRENT risk of a feed: only a feed that is being observed (playing) has one. A paused, ended, stopped, idle or failed feed is UNKNOWN; its last value is history, reported apart as `last_observed_risk` (cycle-25 MG-STATE-025)."""
    return last if feed_state == "playing" else "UNKNOWN"


def unknown_reason(feed_state: str, has_sample: bool, age_s: float | None) -> str | None:
    """Message id explaining WHY the current risk is UNKNOWN (None when it is observed and fresh). Transport state and risk are separate fields; this only connects them for the operator."""
    if feed_state != "playing":
        return "mg.unknown." + (feed_state if feed_state in ("idle", "paused", "stopped", "ended", "failed") else "other")
    if not has_sample:
        return "mg.unknown.no_sample_yet"
    if age_s is not None and age_s > STALE_OBSERVATION_S:
        return "mg.unknown.stale"
    return None


def _pct(vals, q):
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))] if s else None


class Feed:
    def __init__(self, mgr: "FeedManager", fid: str, label: str, video: dict, cfg: dict, zones: list[dict], repeated_source: bool):
        self.mgr, self.fid, self.label, self.video, self.cfg = mgr, fid, label, video, cfg
        self.zones, self.zones_revision = zones, 1
        self._shared_run = False                    # sticky for THIS run: the file was played by another ACTIVE feed at some moment of the run (cycle-26 MG-PROV-026)
        self._overlaps: list[dict] = []             # WHEN the overlap was present, bounded by sample times (MG-PROV-028); the sticky flag above stays the conservative run-level label
        self._overlaps_truncated = False
        self._ov_lock = threading.Lock()
        self._repeated_frozen: bool | None = None   # PROVENANCE of a run is frozen when the run starts (cycle-22 MG-PROV-022); before that it follows the feeds that exist
        self.lock = threading.RLock()
        self.state = "idle"
        self._init_life()
        self.run_uid = uuid.uuid4().hex[:12]                       # identity of THIS RUN (alert source, saved summary): stable until the feed is restarted
        self.engine_uid = uuid.uuid4().hex[:12]                    # identity of the current zone engines: rotates with every zone edit (new engines, new event ids) without touching the run identity
        self.params, self.tparams = ev.live_params(cfg["interval_ms"])
        self.tracker = IouTracker(self.tparams)
        self.engines: dict[str, ZoneEngine] = {}
        self.closed_events: list[dict] = []                         # events of engines replaced by a zone edit (kept, right-censored)
        self.q: collections.deque = collections.deque()
        self.pending: collections.OrderedDict[int, dict] = collections.OrderedDict()
        self.next_index = 0
        self.last_served = 0
        self.counters = {"decoded": 0, "offered": 0, "skipped_by_sampling": 0, "analyzed": 0, "dropped_queue_overflow": 0, "dropped_stale": 0, "adapter_error": 0,
                         "dropped_stopped": 0, "detections": 0}
        self.latency_ms: collections.deque = collections.deque(maxlen=LATENCY_WINDOW)
        self.wait_ms: collections.deque = collections.deque(maxlen=LATENCY_WINDOW)
        self.commit_times: collections.deque = collections.deque(maxlen=256)
        self.max_depth = 0
        self.class_hist: collections.Counter = collections.Counter()
        self.track_ids: dict[str, set] = {}
        self.discontinuities = 0
        self.latest: dict = {"index": None, "t_us": None, "objects": [], "analyzed_at": None}
        self.last_frame: np.ndarray | None = None
        self.risk_now: dict = {}
        self.error: str | None = None
        self.error_detail: str | None = None
        self.source_retries = 0
        self.closed_reason: str | None = None
        self.result_id: str | None = None
        self.started_wall: float | None = None
        self.last_t_us = -1
        self.pass_no = 0
        self.stop_flag = threading.Event()
        self.resume_flag = threading.Event()
        self.resume_flag.set()
        self.reader: threading.Thread | None = None
        self.reader_done = threading.Event()
        self._closed = False
        self._build_engines()

    def _init_life(self):
        self.life: list[dict] = []                                  # MG-PRODUCT-VERTICAL-07: what the application itself did / saw during THIS run, in the order it happened
        self.life_n = {"lifecycle": 0, "observed": 0}
        self.life_truncated = False
        self._seen = {"observation": False, "person": False, "vehicle": False}
        self._risk_prev: dict[str, str] = {}

    def _life(self, kind: str, group: str, t_us: int | None = None, **extra):
        """Appends one RECORDED fact (never inferred). `lifecycle` kinds are always kept (within a generous cap); `observed` kinds (first observation, presence edges, risk changes) have their own cap
        so a long run can never push the stop/fail record out. Caller holds the feed lock."""
        if self.life_n[group] >= (LIFE_CAP_LIFECYCLE if group == "lifecycle" else LIFE_CAP_OBSERVED):
            self.life_truncated = True
            return
        self.life_n[group] += 1
        now = time.time()
        self.life.append({"seq": len(self.life), "kind": kind, "group": group, "at_utc": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now * 1000) % 1000:03d}Z",
                          "t_us": t_us, "run_uid": self.run_uid, **extra})

    def _life_last(self) -> dict | None:
        """The most recent recorded SOURCE lifecycle fact of this run (start / pause / resume / stop / end / fail); None when this run has recorded none (idle, or a feed never started)."""
        for e in reversed(self.life):
            if e["group"] == "lifecycle":
                return {"kind": e["kind"], "at_utc": e["at_utc"], "reason": e.get("reason")}
        return None

    def life_doc(self) -> dict:
        with self.lock:
            return {"run_uid": self.run_uid, "events": [dict(e) for e in self.life], "truncated": self.life_truncated}

    def reset(self):
        """A finished feed starts a NEW run of the same configuration and zones (independent restart): fresh tracker, engines, counters and event identities; nothing of the previous run
        survives except its saved summary on disk (caller holds the manager lock)."""
        self.run_uid = uuid.uuid4().hex[:12]
        self.engine_uid = uuid.uuid4().hex[:12]
        self._init_life()
        self._repeated_frozen = None
        self._shared_run = False
        with self._ov_lock:
            self._overlaps, self._overlaps_truncated = [], False
        self.tracker = IouTracker(self.tparams)
        self.closed_events = []
        self.q.clear()
        self.pending.clear()
        self.next_index = 0
        self.counters = {k: 0 for k in self.counters}
        for dq in (self.latency_ms, self.wait_ms, self.commit_times):
            dq.clear()
        self.max_depth = 0
        self.class_hist = collections.Counter()
        self.track_ids = {}
        self.latest = {"index": None, "t_us": None, "objects": [], "analyzed_at": None}
        self.last_frame = None
        self.risk_now = {}
        self.error = self.error_detail = self.closed_reason = self.result_id = None
        self.source_retries = 0
        self.last_t_us = -1
        self.pass_no = 0
        self.stop_flag, self.resume_flag, self.reader_done = threading.Event(), threading.Event(), threading.Event()
        self.resume_flag.set()
        self.reader = None
        self._closed = False
        self.state = "idle"
        self._build_engines()

    # ------------------------------------------------------------------ engines / zones
    def _build_engines(self):
        self.engines = {z["zone_id"]: ZoneEngine(z["zone_id"], self.params, id_context=(f"feed-{self.fid}-{self.engine_uid}", z["digest"])) for z in self.zones if z["kind"] == "restricted"}

    def replace_zones(self, zones: list[dict]):
        """Independent zone edit (caller holds the lock): the old engines are closed at the last sample time (their open events stay, right-censored), the new ones start fresh."""
        for e in self.engines.values():
            e.finish(max(self.last_t_us, 0) + 1)
            self.closed_events += [{**x, "closed_by": "zone_edit"} for x in e.events]
        self.zones = zones
        self.zones_revision += 1
        self.engine_uid = uuid.uuid4().hex[:12]                     # NOT run_uid: the alerts of this run keep their source, the new engines get distinct event ids
        self._build_engines()
        self.risk_now = {}

    # ------------------------------------------------------------------ reader (one thread per feed)
    def shared_now(self) -> bool:
        """CURRENT relationship: another feed of the same file is playing or paused right now. Independent of how this run was launched."""
        return any(g is not self and g.video["video_id"] == self.video["video_id"] and g.state in ("playing", "paused") for g in self.mgr.feeds.values())

    def note_sharing(self):
        """Records (sticky, per run) that this ACTIVE run overlapped with another feed of the same file. Never cleared during the run; a restart (reset) starts a new run."""
        if self.state in ("playing", "paused") and self.shared_now():
            self._shared_run = True
            self._overlap_open()

    def _overlap_open(self):
        with self._ov_lock:
            if not self._overlaps or self._overlaps[-1]["until_before_us"] is not None:
                if len(self._overlaps) < overlap.MAX_INTERVALS:
                    self._overlaps.append({"from_after_us": self.last_t_us if self.last_t_us >= 0 else None, "until_before_us": None})
                else:
                    self._overlaps_truncated = True      # the bound is reached: say so, never drop an overlap silently (MG-PROV-029)

    def _overlap_commit(self, t_us: int):
        """At every committed sample (called BEFORE `last_t_us` moves): an overlap found present opens an interval, an open one found over closes at this sample (bounded by sample times)."""
        sharing = self.state in ("playing", "paused") and self.shared_now()
        if sharing:
            self._shared_run = True
            self._overlap_open()
            return
        with self._ov_lock:
            if self._overlaps and self._overlaps[-1]["until_before_us"] is None:
                self._overlaps[-1]["until_before_us"] = t_us

    def overlap_doc(self) -> dict:
        """The overlap history of this run (IMMUTABLE facts only grow): launch provenance, known intervals, and the evidence basis. `shared_now` is a separate, present-time fact."""
        with self._ov_lock:
            return {"launch_repeated": self._repeated_frozen, "intervals": [dict(i) for i in self._overlaps], "evidence": "sample_time_bounds", "truncated": self._overlaps_truncated}

    def is_repeated(self) -> bool:
        """EVIDENTIARY provenance of this run (what events, alerts, aggregates and the saved summary are classified by). Before a start: whether another feed of the same file exists. Once started:
        launched as a repeat OR the file was played by another ACTIVE feed at ANY moment of the run (sticky: a run is never reclassified as genuine from the present state, cycle-27 MG-PROV-027).
        The PRESENT relationship is a separate fact (`shared_now`); a restart is a new run and decides again. No claim is ever made that several physical cameras exist."""
        if self._repeated_frozen is None:
            # MG-PROV-035: only a peer that is PLAYING or PAUSED overlaps this launch; an idle/stopped/failed duplicate does not make a single active playback look like simultaneous monitoring
            return any(g is not self and g.video["video_id"] == self.video["video_id"] and g.state in ("playing", "paused") for g in self.mgr.feeds.values())
        self.note_sharing()
        return bool(self._repeated_frozen or self._shared_run)

    def launch_repeated(self) -> bool | None:
        """IMMUTABLE for the run: was the file already played by another feed when this run started (None before the first start)."""
        return self._repeated_frozen

    def start_reader(self):
        self._repeated_frozen = self.is_repeated()
        self.state = "playing"
        self.started_wall = time.time()
        with self.lock:
            self._life("source_started", "lifecycle", source_kind=self.video.get("kind", "mp4_file"), repeated_at_start=bool(self._repeated_frozen))
        self.reader = threading.Thread(target=self._read_loop, name=f"mg-{self.fid}-reader", daemon=True)
        self.reader.start()

    def _read_loop(self):
        try:
            interval_us = self.cfg["interval_ms"] * 1000
            base = 0
            wall0 = mono0 = None
            first_pts = None
            last_offer = None
            last_pts = 0
            while not self.stop_flag.is_set() and not self.mgr.model_ready.wait(0.1):
                pass                                   # the model is loading (cold start): nothing is decoded, offered or aged meanwhile
            while not self.stop_flag.is_set():
                got_any = False
                try:
                    for fr in self.mgr.frames_factory(self.video["path"]):
                        if self.stop_flag.is_set():
                            break
                        if not self.resume_flag.is_set():     # paused: when it resumes the pacing clock restarts from the next frame, so the time spent paused is never "caught up" in a burst
                            self.resume_flag.wait()
                            mono0 = None
                        if self.stop_flag.is_set():
                            break
                        got_any = True
                        pts_us = (fr.pts_ticks * 1_000_000) // fr.timescale
                        if first_pts is None:
                            first_pts = pts_us
                        src_t = base + pts_us - first_pts
                        last_pts = src_t
                        with self.lock:
                            self.counters["decoded"] += 1
                        if last_offer is not None and src_t - last_offer < interval_us:
                            with self.lock:
                                self.counters["skipped_by_sampling"] += 1
                            continue
                        if self.cfg["mode"] == "realtime":   # pace by the source clock x speed; a paused feed re-bases the clock on resume
                            if mono0 is None:
                                mono0, wall0 = time.monotonic(), src_t
                            due = mono0 + (src_t - wall0) / 1_000_000 / self.cfg["speed"]
                            lag = due - time.monotonic()
                            if lag > 0:
                                self.stop_flag.wait(lag)
                                if self.stop_flag.is_set():
                                    break
                        last_offer = src_t
                        self._offer(src_t, fr.rgb)
                except Exception:
                    if got_any or self.source_retries >= SOURCE_OPEN_RETRIES or self.stop_flag.is_set():
                        raise
                    with self.lock:
                        self.source_retries += 1       # visible in the feed status; bounded
                    self.stop_flag.wait(SOURCE_RETRY_DELAY_S)
                    first_pts = None
                    continue
                if not got_any and not self.stop_flag.is_set():
                    raise decode.UnsupportedMedia("the source produced no frames")
                if self.stop_flag.is_set() or not self.cfg["loop"]:
                    break
                base = last_pts + interval_us          # looping keeps SOURCE TIME strictly increasing across passes
                self.pass_no += 1
                last_offer = None
                first_pts = None
        except BaseException as e:   # a failing source ends THIS feed only
            with self.lock:
                self.error = f"{type(e).__name__}"
                self.error_detail = str(e).replace(os.path.expanduser("~"), "~")[:120]
                self.state = "failed"
                self.closed_reason = "source_failed"
                self._life("source_failed", "lifecycle", self.last_t_us if self.last_t_us >= 0 else None, reason="source_failed", error=self.error)
        finally:
            self.reader_done.set()
            self.mgr.wake()

    def _offer(self, t_us: int, rgb: np.ndarray):
        with self.lock:
            if self.state not in ("playing", "paused"):
                return
            idx = self.next_index
            self.next_index += 1
            self.pending[idx] = {"index": idx, "t_us": t_us, "status": "pending", "objects": [], "reason": None}
            self.counters["offered"] += 1
        dropped, dropped_old = None, None
        with self.mgr.cv:
            if self.cfg["mode"] == "throttled":
                while len(self.q) >= FEED_QUEUE_MAX and not self.stop_flag.is_set():
                    self.mgr.cv.wait(0.1)   # backpressure: the FILE waits for the model; nothing is dropped
                if self.stop_flag.is_set():
                    dropped = (idx, "feed_stopped")
            elif len(self.q) >= FEED_QUEUE_MAX:
                dropped_old = self.q.popleft()[0]      # realtime: drop the OLDEST queued frame, keep the newest
            if dropped is None:
                self.q.append((idx, t_us, rgb, time.monotonic()))
                self.max_depth = max(self.max_depth, len(self.q))
                self.mgr.cv.notify_all()
        if dropped_old is not None:
            self._settle(dropped_old, "not_analyzed_dropped", [], "queue_overflow")
        if dropped:
            self._settle(dropped[0], "not_analyzed_dropped", [], dropped[1])

    # ------------------------------------------------------------------ worker side (called by the manager's single scheduler thread)
    def process(self, item, detector):
        idx, t_us, rgb, enq = item
        t0 = time.monotonic()
        try:
            objs, _ = build_objects(detector.predict(rgb, self.cfg["threshold_permille"] / 1000), DECODE_W, DECODE_H)
        except Exception as e:      # explicit adapter_error sample; never replaced by synthetic output; affects THIS feed only
            self._settle(idx, "adapter_error", [], type(e).__name__)
            return
        infer_ms = int((time.monotonic() - t0) * 1000)
        with self.lock:
            # COMMIT BOUNDARY: freshness is judged here, under the feed lock, immediately before the sample becomes the current observation
            if self.state not in ("playing", "paused") and not self._draining():
                self._settle_locked(idx, "not_analyzed_dropped", [], "feed_stopped")
                return
            if (time.monotonic() - enq) * 1_000_000 > FEED_MAX_AGE_US:
                self._settle_locked(idx, "not_analyzed_dropped", [], "stale")
                return
            self.latency_ms.append(infer_ms)
            self.wait_ms.append(int((time.monotonic() - enq) * 1000))
            self._settle_locked(idx, "analyzed", objs, None, frame=rgb)

    def _draining(self) -> bool:
        return self.reader_done.is_set() and self.state == "ended"

    def _settle(self, idx: int, status: str, objs: list, reason: str | None):
        with self.lock:
            self._settle_locked(idx, status, objs, reason)

    def _settle_locked(self, idx: int, status: str, objs: list, reason: str | None, frame=None):
        rec = self.pending.get(idx)
        if rec is None or rec["status"] != "pending":
            return
        rec["status"], rec["objects"], rec["reason"] = status, objs, reason
        if status == "analyzed":
            self.counters["analyzed"] += 1
            self.counters["detections"] += len(objs)
            self.commit_times.append(time.monotonic())
            rec["_frame"] = frame
        elif reason == "queue_overflow":
            self.counters["dropped_queue_overflow"] += 1
        elif reason == "stale":
            self.counters["dropped_stale"] += 1
        elif status == "adapter_error":
            self.counters["adapter_error"] += 1
        else:
            self.counters["dropped_stopped"] += 1
        while self.pending:
            first = next(iter(self.pending))
            r = self.pending[first]
            if r["status"] == "pending":
                break
            del self.pending[first]
            self._consume(r)

    def _consume(self, rec: dict):
        """Feeds decided samples to the tracker and the kernel strictly in source order (caller holds the lock). TRANSACTIONAL (cycle-24 MG-FEED-024): the sample reaches the tracker and
        EVERY zone engine of this feed, or none of them. If anything fails midway the tracker and the engines return to exactly their pre-sample state, the frame stops being counted as
        analyzed (it never reached the risk record) and the error propagates, which ends the feed explicitly. Publication happens only after the commit."""
        cps = (self.tracker.checkpoint(), {zid: e.checkpoint() for zid, e in self.engines.items()})
        try:
            self._apply(rec)
        except BaseException:
            self.tracker.rollback(cps[0])
            for zid, e in self.engines.items():
                e.rollback(cps[1][zid])
            self._unsettle(rec)
            raise
        self.mgr.publish(self)

    def _unsettle(self, rec: dict):
        """A frame whose consumption failed is moved ONCE from `analyzed` to `dropped_stopped` (reason internal_error): offered == sum of outcomes stays exact."""
        if rec["status"] != "analyzed":
            return
        self.counters["analyzed"] -= 1
        self.counters["detections"] -= len(rec["objects"])
        self.counters["dropped_stopped"] += 1
        rec["status"], rec["objects"], rec["reason"] = "not_analyzed_dropped", [], "internal_error"

    def _apply(self, rec: dict):
        analyzed = rec["status"] == "analyzed"
        ids = self.tracker.update(rec["t_us"], [(o["cls"]["label"], o["bbox"]) for o in rec["objects"]]) if analyzed else [None] * len(rec["objects"])
        ids = [f"{self.fid}/{i}" if i is not None else None for i in ids]     # per-feed namespace: a track id can never be mistaken for another feed's
        brk = frozenset(f"{self.fid}/{b}" for b in self.tracker.breaks) if analyzed else frozenset()    # ids the tracker ended by ambiguity at this sample
        for z in self.zones:
            if z["zone_id"] in self.engines:
                self.engines[z["zone_id"]].push(sample_for(z, rec["index"], rec["t_us"], rec["status"], rec["objects"], ids, identity_breaks=brk))
        # ---- commit: nothing below can fail halfway in a way that matters (plain counters and assignments)
        self.discontinuities += sum(1 for b in self.tracker.breaks if b in self.tracker.summary)        # a MEASURED count: only tracks that had been confirmed (known to the kernel)
        self._overlap_commit(rec["t_us"])
        self.last_t_us = rec["t_us"]
        if analyzed:
            for o, i in zip(rec["objects"], ids):
                self.class_hist[o["cls"]["label"]] += 1
                if i is not None and len(self.track_ids.setdefault(o["cls"]["label"], set())) < MAX_TRACK_IDS:
                    self.track_ids[o["cls"]["label"]].add(i)
            self.latest = {"index": rec["index"], "t_us": rec["t_us"], "analyzed_at": time.time(),
                           "objects": [{"label": o["cls"]["label"], "bbox": o["bbox"], "permille": o["confidence"]["permille"], "track": t} for o, t in zip(rec["objects"], ids)]}
            self.last_frame = rec.pop("_frame", None)
        self.risk_now = {zid: e.last_state for zid, e in self.engines.items()}
        with self.lock:
            self._record_observed(rec, analyzed)

    def _record_observed(self, rec: dict, analyzed: bool):
        """Presence EDGES and risk CHANGES at the samples the application really analysed; nothing is recorded between two samples."""
        t = rec["t_us"]
        if analyzed:
            if not self._seen["observation"]:
                self._seen["observation"] = True
                self._life("observation_received", "observed", t, index=rec["index"])
            for lab in ("person", "vehicle"):
                n = sum(1 for o in rec["objects"] if o["cls"]["label"] == lab)
                if n and not self._seen[lab]:
                    self._life(lab + "_detected", "observed", t, index=rec["index"], count=n, scope="frame")
                self._seen[lab] = bool(n)
        for zid, st in self.risk_now.items():
            cur = st.get("state", "UNKNOWN")
            prev = self._risk_prev.get(zid)
            if prev != cur:
                self._life("risk_changed", "observed", t, zone_id=zid, **{"from": prev, "to": cur})
                self._risk_prev[zid] = cur

    # ------------------------------------------------------------------ views
    def events(self) -> list[dict]:
        out = list(self.closed_events)
        for e in self.engines.values():
            out += e.snapshot()["events"]
        return out

    def fps(self) -> float | None:
        c = list(self.commit_times)
        if len(c) < 2 or c[-1] - c[0] <= 0:
            return None
        return round((len(c) - 1) / (c[-1] - c[0]), 2)

    def view(self) -> dict:
        with self.lock:
            lat, wt = list(self.latency_ms), list(self.wait_ms)
            ov = self.overlap_doc()
            ev_all = [{**e, "overlap_phase": overlap.phase_of(e.get("start_us"), ov)} for e in self.events()]
            analysed_at = self.latest.get("analyzed_at")
            age_s = (time.time() - analysed_at) if analysed_at else None
            reason = unknown_reason(self.state, bool(self.risk_now) and analysed_at is not None, age_s)
            risk_cur = {zid: {"state": ("UNKNOWN" if reason else current_risk(self.state, s.get("state", "UNKNOWN"))), "reason": reason} for zid, s in self.risk_now.items()}
            risk_worst = "UNKNOWN" if (reason or not self.risk_now) else max((c["state"] for c in risk_cur.values()), key=lambda x: {"UNKNOWN": 1, "GREEN": 0, "YELLOW": 2, "RED": 3}.get(x, 1))
            if reason is None and risk_worst == "UNKNOWN" and self.risk_now:
                reason = "mg.unknown.insufficient"          # observed and fresh, but the risk kernel itself could not evaluate the latest sample (e.g. a frame that was not analysed)
            last_obs = (max((s.get("state", "UNKNOWN") for s in self.risk_now.values()), key=lambda x: {"UNKNOWN": 1, "GREEN": 0, "YELLOW": 2, "RED": 3}.get(x, 1)) if self.risk_now else None)
            return {"feed_id": self.fid, "label": self.label, "state": self.state,
                    "launch_repeated": self.launch_repeated(), "overlap": ov, "shared_now": self.shared_now() if self.state in ("playing", "paused") else False, "shared_during_run": self._shared_run,
                    "risk_current": risk_cur, "risk_state": risk_worst, "unknown_reason": reason, "last_observed_risk": last_obs,
                    "observation": {"fresh": reason in (None, "mg.unknown.insufficient") and analysed_at is not None, "age_ms": int(age_s * 1000) if age_s is not None else None, "stale_after_ms": int(STALE_OBSERVATION_S * 1000)}, "error": self.error, "error_detail": self.error_detail, "source_retries": self.source_retries, "closed_reason": self.closed_reason, "result_id": self.result_id,
                    "source": self.source_doc(), "config": {k: self.cfg[k] for k in ("sample_fps", "interval_ms", "threshold_permille", "mode", "speed", "loop")},
                    "counters": dict(self.counters), "model_loading": self.state in ("playing", "paused") and not self.mgr.model_ready.is_set(), "queue_depth": len(self.q), "queue_max": FEED_QUEUE_MAX, "max_queue_depth": self.max_depth,
                    "latency_ms": {"p50": _pct(lat, 0.5), "p95": _pct(lat, 0.95), "max": max(lat) if lat else None},
                    "queue_wait_ms": {"p50": _pct(wt, 0.5), "p95": _pct(wt, 0.95)}, "fps": self.fps(), "latest": self.latest, "risk": self.risk_now,
                    "zones": self.zones, "zones_revision": self.zones_revision, "events": ev_all,
                    "lifecycle_last": self._life_last(),
                    "latest_event": (max(ev_all, key=lambda e: e["start_us"]) if ev_all else None),
                    "tracks_unique": {k: len(v) for k, v in self.track_ids.items()}, "class_hist": dict(self.class_hist), "discontinuities": self.discontinuities,
                    "passes": self.pass_no, "source_time_us": self.last_t_us if self.last_t_us >= 0 else None}

    def source_doc(self) -> dict:
        rep = self.is_repeated()
        return {"kind": "mp4_file", "source_id": f"src_{self.video['video_id']}", "video_id": self.video["video_id"], "filename": self.video["filename"],
                "sha256_prefix": self.video["sha256"][:16], "repeated_source": rep,
                "provenance": "repeated_source_simulation" if rep else "real_inference", "physical_camera": False,
                "note": "a file replayed as a feed; several feeds of the same file are a concurrency simulation, not several cameras" if rep else "a file replayed as a feed; not a physical camera"}

    def preview_jpeg(self) -> bytes | None:
        with self.lock:
            fr = self.last_frame
        if fr is None:
            return None
        buf = io.BytesIO()
        Image.fromarray(fr).resize((640, 360)).save(buf, "JPEG", quality=70)
        return buf.getvalue()

    # ------------------------------------------------------------------ closing / persistence
    def fail_unexpected(self, exc: BaseException):
        """Containment of an UNEXPECTED internal error while this feed was being processed (cycle-23 MG-FEED-023). The run is ended explicitly and atomically — state `failed`, reason
        `internal_error`, undecided records discarded and counted, queue drained, summary saved — instead of leaving a half-applied sample or a feed that never closes. Other feeds are untouched."""
        with self.lock:
            if self._closed:
                return
            self.error, self.error_detail = type(exc).__name__, str(exc).replace(os.path.expanduser("~"), "~")[:120]
            self.state, self.closed_reason = "failed", "internal_error"
            # every queued frame is ALSO a pending record: count the undecided ones once, here; frames already settled keep the outcome they were counted with (cycle-24 MG-FEED-024)
            self.counters["dropped_stopped"] += sum(1 for r in self.pending.values() if r["status"] == "pending")
            self.pending.clear()
        self.stop_flag.set()
        self.resume_flag.set()
        with self.mgr.cv:
            self.q.clear()
            self.mgr.cv.notify_all()
        self.close("internal_error")

    def close(self, reason: str):
        """Single-owner: finishes the engines, harvests the events and persists a METADATA-ONLY summary. Idempotent. The final state is published only AFTER the summary was written (or its
        failure recorded), all under the feed lock, so a reader never sees `ended` / `stopped` without the summary's identity."""
        with self.lock:
            if self._closed:
                return
            self._closed = True
            for idx in list(self.pending):
                self._settle_locked(idx, "not_analyzed_dropped", [], "feed_stopped")
            end = max(self.last_t_us, 0) + self.cfg["interval_ms"] * 1000
            for e in self.engines.values():
                try:
                    e.finish(end)
                except Exception:   # an engine that an internal error left inconsistent must not prevent the feed from closing
                    pass
            self.closed_reason = self.closed_reason or reason
            kind = {"end_of_stream": "source_ended", "operator_stop": "source_stopped"}.get(reason, "source_failed")
            if not (kind == "source_failed" and any(e["kind"] == "source_failed" for e in self.life)):      # a reader failure was already recorded when it happened
                self._life(kind, "lifecycle", self.last_t_us if self.last_t_us >= 0 else None, reason=reason, **({"error": self.error} if kind == "source_failed" else {}))
            self.mgr.remember_life(self)
            self.last_frame = None                                    # the preview frame is released with the feed
            self.risk_now = {zid: {"state": e.last_state["state"], "pending": [], "causes": ["feed_closed"], "t_us": e.last_state.get("t_us")} for zid, e in self.engines.items()}
            self.mgr.publish(self, final=True)
            self.mgr.persist(self)
            if self.state not in ("failed",):
                self.state = "ended" if reason == "end_of_stream" else "stopped"

    def summary_doc(self) -> dict:
        v = self.view()
        events = [{k: e.get(k) for k in ("event_id", "zone_id", "rule_id", "severity", "start_us", "end_us", "closed_reason", "tracks", "right_censored", "left_censored")} for e in v["events"]]
        return {"schema": "mg.feedrun.v1", "feed_id": self.fid, "label": self.label, "source": v["source"], "config": v["config"], "counters": v["counters"],
                "latency_ms": v["latency_ms"], "queue_wait_ms": v["queue_wait_ms"], "max_queue_depth": v["max_queue_depth"], "tracks_unique": v["tracks_unique"], "class_hist": v["class_hist"],
                "events": events, "zones": [{"zone_id": z["zone_id"], "digest": z["digest"]} for z in self.zones], "zones_revision": self.zones_revision,
                "closed_reason": self.closed_reason, "error": self.error, "passes": self.pass_no, "source_time_us": v["source_time_us"], "overlap": v["overlap"],
                "producer": {"adapter": "rf-detr-nano", "kernel": ev.ENGINE_VERSION, "tracker": ev.TRACKER_VERSION, "app": __version__},
                "origin": "model_inference_live_feed", "trust": "live_summary", "retention": {"mode": "metadata_only", "frames_persisted": False},
                "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.started_wall)) if self.started_wall else None,
                "ended_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}


class FeedManager:
    def __init__(self, st, runs_dir: Path, detector_factory: Callable, alerts=None, frames_factory: Callable[[str], Iterator] | None = None):
        self.st, self.runs_dir, self.detector_factory, self.alerts = st, runs_dir, detector_factory, alerts
        self.frames_factory = frames_factory or (lambda path: decode.iter_frames(path, DECODE_W, DECODE_H))
        self.feeds: dict[str, Feed] = {}
        self.cv = threading.Condition(threading.Lock())
        self.lock = threading.RLock()
        self.active_limit = DEFAULT_ACTIVE_LIMIT
        self.sched: threading.Thread | None = None
        self.holds_infer = False
        self.tick = 0
        self.sched_stats = {"served": 0, "idle_waits": 0, "max_wait_ms": 0, "started_mono": None, "detector_error": None}
        self.closing = False
        self.detector = None
        self.finished_life: collections.OrderedDict[str, dict] = collections.OrderedDict()   # run_uid -> lifecycle record of a FINISHED run (bounded, in memory only)
        self._life_lock = threading.Lock()
        self.granted: dict[str, str] = {}      # saved-summary id -> sha256 of the exact bytes THIS process wrote (its integrity grant; never persisted)
        self.model_ready = threading.Event()   # readers offer nothing until the shared model is loaded: the cold start is never counted as stale frames

    # ------------------------------------------------------------------ plumbing
    def wake(self):
        with self.cv:
            self.cv.notify_all()

    def publish(self, feed: Feed, final: bool = False):
        if self.alerts is not None:
            try:
                self.alerts.observe_feed(feed, final)
            except Exception:   # an alerting problem never stops analysis
                self.sched_stats["alert_errors"] = self.sched_stats.get("alert_errors", 0) + 1

    def remember_life(self, feed: Feed):
        doc = feed.life_doc()
        with self._life_lock:
            self.finished_life[feed.run_uid] = doc
            while len(self.finished_life) > LIFE_KEEP_RUNS:
                self.finished_life.popitem(last=False)

    def life_for(self, fid: str, run_uid: str) -> dict | None:
        """The recorded lifecycle of ONE run of ONE feed: the live run if it is current, else the remembered finished run; None when this process never recorded it."""
        with self.lock:
            f = self.feeds.get(fid)
        if f is not None and f.run_uid == run_uid:
            return f.life_doc()
        with self._life_lock:
            d = self.finished_life.get(run_uid)
        return dict(d) if d else None

    def persist(self, feed: Feed):
        """Writes the metadata-only summary inside the PINNED, owned `runs/feeds` directory (a symlink there is refused, never followed: cycle-21 MG-PATH-021)."""
        try:
            doc = feed.summary_doc()
            rid = "feedrun_" + hashlib.sha256(json.dumps([doc, feed.run_uid], sort_keys=True, default=str).encode()).hexdigest()[:16]   # identity of THIS summary (it holds measured floats, so no canonical-JSON digest)
            text = json.dumps({**doc, "id": rid}, indent=1, ensure_ascii=False)
            with safedir.owned_dir(self.runs_dir, FEEDS_DIR) as fd:
                safedir.write_atomic(fd, f"{rid}.json", text)
            self.granted[rid] = hashlib.sha256(text.encode()).hexdigest()
            while len(self.granted) > 500:
                del self.granted[next(iter(self.granted))]
            feed.result_id = rid
        except (OSError, ValueError, TypeError, safedir.UnsafeDir):
            feed.error = feed.error or "summary_not_persisted"

    # ------------------------------------------------------------------ scheduler (the only thread that calls the detector)
    def _pick(self) -> Feed | None:
        ready = [f for f in self.feeds.values() if f.q]
        if not ready:
            return None
        return min(ready, key=lambda f: f.last_served)       # fairness: the least recently served feed goes next

    def _sched_loop(self):
        try:
            if self.detector is None:
                self.detector = self.st.detector or self.detector_factory()
                self.st.detector = self.detector
            self.model_ready.set()
        except Exception as e:
            self.model_ready.set()   # release the readers: they are stopped right below
            # the model is unavailable: every feed ends as failed (explicit status), nothing is analysed, nothing is invented, and the shared lock is given back
            self.sched_stats["detector_error"] = type(e).__name__
            for f in list(self.feeds.values()):
                with f.lock:
                    if f.state in ("playing", "paused"):
                        f.error, f.state, f.closed_reason = "model_unavailable", "failed", "model_unavailable"   # state and reason change together: no reader ever sees one without the other
                f.stop_flag.set()
                f.resume_flag.set()
                if f.reader:
                    f.reader.join(timeout=5)
                with self.cv:
                    f.q.clear()
                f.close("model_unavailable")
            with self.cv:
                self._release_model()
            return
        self.sched_stats["started_mono"] = time.monotonic()
        try:
            while not self.closing:
                try:
                    with self.cv:
                        f = self._pick()
                        if f is None:
                            self.sched_stats["idle_waits"] += 1
                            self.cv.wait(0.1)
                            self._reap_locked()
                            continue
                        item = f.q.popleft()
                        self.tick += 1
                        f.last_served = self.tick
                        self.cv.notify_all()
                    self.sched_stats["served"] += 1
                    self.sched_stats["max_wait_ms"] = max(self.sched_stats["max_wait_ms"], int((time.monotonic() - item[3]) * 1000))
                    try:
                        f.process(item, self.detector)
                    except Exception as e:   # one feed's bug ends THAT feed explicitly; the scheduler and the other feeds go on
                        self.sched_stats["process_errors"] = self.sched_stats.get("process_errors", 0) + 1
                        f.fail_unexpected(e)
                except Exception:   # nothing in an iteration may kill the only inference thread
                    self.sched_stats["loop_errors"] = self.sched_stats.get("loop_errors", 0) + 1
                    time.sleep(0.05)
        finally:
            if not self.closing:   # the thread is leaving for a reason other than a shutdown: no feed may be left waiting for it and the shared model lock must not stay held
                for f in list(self.feeds.values()):
                    if f.state in ("playing", "paused"):
                        f.fail_unexpected(RuntimeError("scheduler_stopped"))
                with self.cv:
                    self._release_model()

    def _reap_locked(self):
        """Closes feeds whose reader ended (end of stream / failure) and whose queue is drained; releases the model when no feed is active. Caller holds cv."""
        done = [f for f in self.feeds.values() if f.reader_done.is_set() and not f.q and not f._closed and f.state in ("playing", "paused", "failed")]
        for f in done:
            if f.state == "failed" or not [i for i, r in f.pending.items() if r["status"] == "pending"]:
                # the reason follows what actually ended the feed: a stop requested by the operator wins over a reader that merely finished because of it
                f.close("source_failed" if f.state == "failed" else "operator_stop" if f.stop_flag.is_set() else "end_of_stream")   # nothing in close() needs the condition lock
        if self.holds_infer and not any(f.state in ("playing", "paused") for f in self.feeds.values()):
            self._release_model()

    def _acquire_model(self):
        """Takes the single shared model lock (a webcam session, an MP4 analysis or a verification holds it otherwise: 409)."""
        if not self.holds_infer:
            if not self.st.infer_lock.acquire(blocking=False):
                raise FeedError(409, "mg.refusal.job_in_progress")
            self.holds_infer = True

    def _ensure_scheduler(self):
        """Starts the single inference worker AFTER the feed is marked playing, so that an immediate model failure can see (and fail) the feeds that asked for it."""
        if self.sched is None or not self.sched.is_alive():
            self.closing = False
            self.sched = threading.Thread(target=self._sched_loop, name="mg-feed-scheduler", daemon=True)
            self.sched.start()

    def _release_model(self):
        if self.holds_infer:
            self.holds_infer = False
            try:
                self.st.infer_lock.release()
            except RuntimeError:
                pass

    # ------------------------------------------------------------------ API operations
    def get(self, fid: str) -> Feed:
        f = self.feeds.get(fid)
        if f is None:
            raise FeedError(404, "mg.refusal.unknown_feed")
        return f

    def _zones_from(self, zones_in: list[dict]) -> list[dict]:
        zones = []
        for z in zones_in:
            try:
                zones.append(make_zone(z["zone_id"], z.get("kind", "restricted"), z["polygon"], DECODE_W, DECODE_H, z.get("name")))
            except Exception as e:
                raise FeedError(422, getattr(e, "args", ["mg.refusal.zone_invalid"])[0] if e.args else "mg.refusal.zone_invalid")
        if len({z["zone_id"] for z in zones}) != len(zones) or len(zones) > 8:
            raise FeedError(422, "mg.refusal.zone_set_invalid")
        return zones

    def create(self, video_id: str, label: str, sample_fps: float, threshold: float, mode: str, speed: float, loop: bool, zones_in: list[dict], source_kind: str = "mp4_file") -> Feed:
        if source_kind not in SOURCE_KINDS:
            raise FeedError(422, "mg.refusal.feed_source_kind")
        if source_kind == "browser_camera":
            BrowserCameraSource()
        v = self.st.videos.get(video_id)
        if not v or "error" in v:
            raise FeedError(404, "mg.refusal.unknown_video")
        if not MIN_FPS <= sample_fps <= MAX_FPS:
            raise FeedError(422, "mg.refusal.feed_bad_sample_rate")
        thr = permille(threshold)
        if not 1 <= thr <= 999:
            raise FeedError(422, "mg.refusal.session_bad_threshold")
        if mode not in MODES or speed not in SPEEDS:
            raise FeedError(422, "mg.refusal.feed_bad_mode")
        label = (label or "").strip()[:60] or "Fuente"
        zones = self._zones_from(zones_in)
        with self.lock:
            if len(self.feeds) >= MAX_FEEDS:
                raise FeedError(409, "mg.refusal.feed_capacity")
            repeated = any(f.video["video_id"] == video_id for f in self.feeds.values())
            fid = "feed_" + uuid.uuid4().hex[:8]
            cfg = {"sample_fps": sample_fps, "interval_ms": max(100, min(5000, int(round(1000 / sample_fps)))), "threshold_permille": thr, "mode": mode, "speed": speed, "loop": bool(loop)}
            f = Feed(self, fid, label, v, cfg, zones, repeated)
            self.feeds[fid] = f
        return f

    def active_count(self) -> int:
        return sum(1 for f in self.feeds.values() if f.state in ("playing", "paused"))

    def start(self, fid: str) -> Feed:
        with self.lock:
            f = self.get(fid)
            if f.state in ("playing", "paused"):
                return f
            if self.active_count() >= self.active_limit:
                raise FeedError(409, "mg.refusal.feed_capacity")
            if f._closed or f.state in ("ended", "stopped", "failed"):
                f.reset()                                    # independent restart: a new run of this feed
            self.sched_stats["detector_error"] = None if not (self.sched and self.sched.is_alive()) else self.sched_stats["detector_error"]
            self._acquire_model()
            f.start_reader()
            peers = [g for g in self.feeds.values() if g.video["video_id"] == f.video["video_id"] and g.state in ("playing", "paused")]
            for g in peers:
                g.note_sharing()
            self._ensure_scheduler()
        for g in peers:
            if g is not f and len(peers) > 1:
                self.publish(g)                # the label of the feed that was already running changes now: alerts of its run are re-labelled monotonic (never upgraded)
        return f

    def pause(self, fid: str) -> Feed:
        f = self.get(fid)
        with f.lock:
            if f.state != "playing":
                raise FeedError(409, "mg.refusal.feed_not_playing")
            f.state = "paused"
            f.resume_flag.clear()
            f._life("source_paused", "lifecycle", f.last_t_us if f.last_t_us >= 0 else None)
        return f

    def resume(self, fid: str) -> Feed:
        f = self.get(fid)
        with f.lock:
            if f.state != "paused":
                raise FeedError(409, "mg.refusal.feed_not_paused")
            f.state = "playing"
            f.resume_flag.set()
            f._life("source_resumed", "lifecycle", f.last_t_us if f.last_t_us >= 0 else None)
        return f

    def stop(self, fid: str) -> Feed:
        f = self.get(fid)
        with f.lock:
            if f._closed:
                return f
            if f.state not in ("playing", "paused", "idle", "failed"):
                return f
        f.stop_flag.set()
        f.resume_flag.set()
        with self.cv:                       # per-feed cancellation: this feed's queued frames are discarded and recorded
            queued = [it[0] for it in f.q]
            f.q.clear()
            self.cv.notify_all()
        for idx in queued:
            f._settle(idx, "not_analyzed_dropped", [], "feed_stopped")
        if f.reader:
            f.reader.join(timeout=10)
        f.close("operator_stop")
        with self.cv:
            self._reap_locked()
        return f

    def remove(self, fid: str):
        with self.lock:
            f = self.get(fid)
            if f.state in ("playing", "paused"):
                raise FeedError(409, "mg.refusal.feed_running")
            del self.feeds[fid]

    def stop_all(self):
        for fid in list(self.feeds):
            try:
                self.stop(fid)
            except FeedError:
                pass
        self.closing = True
        self.wake()
        if self.sched is not None:
            self.sched.join(timeout=5)
        with self.cv:
            self._release_model()

    def set_capacity(self, limit: int, acknowledge_unvalidated: bool) -> int:
        if not 1 <= limit <= MAX_FEEDS:
            raise FeedError(422, "mg.refusal.feed_bad_capacity")
        if limit > VALIDATED_FEEDS and not acknowledge_unvalidated:
            raise FeedError(409, "mg.refusal.feed_capacity_not_validated")
        self.active_limit = limit
        return limit

    def update_zones(self, fid: str, expected_revision: int, zones_in: list[dict]) -> Feed:
        f = self.get(fid)
        zones = self._zones_from(zones_in)
        with f.lock:
            if expected_revision != f.zones_revision:
                raise FeedError(409, "mg.refusal.zones_revision_conflict")
            f.replace_zones(zones)
        return f

    def snapshot(self) -> dict:
        views = [f.view() for f in self.feeds.values()]
        total_fps = sum((v["fps"] or 0) for v in views)
        served = [f.last_served for f in self.feeds.values()]
        return {"feeds": views, "active": self.active_count(), "active_limit": self.active_limit, "validated_feeds": VALIDATED_FEEDS, "max_feeds": MAX_FEEDS,
                "scheduler": {"alive": bool(self.sched and self.sched.is_alive()), "served": self.sched_stats["served"], "max_queue_wait_ms": self.sched_stats["max_wait_ms"],
                              "detector_error": self.sched_stats["detector_error"], "aggregate_fps": round(total_fps, 2), "policy": "one shared model, one worker, least-recently-served first"}}
