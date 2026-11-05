"""Browser-webcam sessions (VS-02B). The browser captures frames (getUserMedia -> canvas -> JPEG) at a bounded rate and POSTs them; this module
runs the SAME local detector on them through a bounded queue, feeds the SAME tracker / zone evaluation / risk kernel, and on stop writes a
verifiable `session_<digest16>` artifact (live observation set, exact received JPEGs as evidence, manifest, provenance, risk evaluation).

Honesty limits (stated in provenance and the UI): the server cannot know whether the pixels came from a physical camera. Capture origin and device
label are CLIENT-DECLARED. A session fed by a test source or a prerecorded file is labelled as declared and is never a physical-camera acceptance."""
from __future__ import annotations

import io
import json
import os
import queue
import shutil
import threading
import time
import uuid
from pathlib import Path

import numpy as np
from PIL import Image

from . import __version__
from .analysis import build_objects, permille, pixel_digest
from .canon import digest, digest16, file_sha256, read_json_strict, sha256_hex
from .risk import evaluation as ev
from .risk.kernel import Params
from .risk.tracker import IouTracker, TrackerParams
from .risk.zone_eval import make_zone, sample_for
from .risk.kernel import ZoneEngine
from .schema import LIVE_SCHEMA_ID, ValidationFailure, obsset_digest, validate_observation_set
from . import taxonomy
from .detector import ADAPTER_ID
from . import safedir
from . import session_docs as sd

STORE_LOCK = threading.RLock()   # serializes installing a finished session with deleting stored sessions/media (cycle-15 MG-DELETE-015)

SESSION_RE = r"^session_[0-9a-f]{16}$"
QUEUE_MAX = 2  # bounded: a slow detector can never make the backlog grow
MAX_AGE_US = 2_000_000  # a queued frame older than this at inference time is STALE and is recorded as dropped, never analyzed late
WORKER_JOIN_S = 5.0  # how long ONE stop request waits for an in-flight detector call before answering 503 (retryable): short, so a client's total retry time is governed by ITS wall-clock deadline, not by 120 s per attempt (cycle-23 MG-LIFE-023); the reaper finishes a finalizing session by itself when the worker returns
IDLE_FINALIZE_S = 15.0  # no frame for this long => the client is gone; the session is finalized as right-censored
ADDENDUM_WINDOW_S = 300.0  # after an UNEXPECTED finalization (client_disconnected) the server still admits a late loss declaration for this long (MG-WCAM-019). Reason: a tab reload, a laptop
#                            sleep or a Wi-Fi roam typically returns within minutes; beyond 5 min the browser state that issued the ticks is gone and the interval stays unobserved
#                            (exact frame count unknown). The window is bounded, server-measured, and an admitted declaration is a SIDECAR: it never rewrites the digest-bound evidence.
MAX_LEDGER_RANGES = 4096   # cycle-24 MG-WCAM-024: disjoint sequence ISLANDS the interval ledger can hold. Every sequence-bearing request the server EXAMINED and did not accept (refused at the transport, or late/non-monotonic/over a limit at intake) is recorded
#                            there, so a gap is reconciled from what reached the server instead of assuming it never did; consecutive refusals share one range. Beyond the bound the remainder is reported as `unclassified_frames`, never as never-observed.
CTL_CACHE = 64  # remembered control message numbers per session (duplicate / reordered handling is bounded)
MAX_MSG_SEQ = 2_000_000_000
MAX_FRAME_BYTES = 2_000_000
MAX_SAMPLES = 20_000
MIN_DIM, MAX_DIM = 160, 1920
CAPTURE_ORIGINS = sd.CAPTURE_ORIGINS
MAX_SESSION_BYTES = 1_500_000_000  # total JPEG bytes a session may spool to disk; beyond it new frames are refused (409)


class SeqLedger:
    """Bounded set of sequence numbers as sorted disjoint [lo, hi] ranges. A number that would need a new range beyond `cap` is NOT recorded (add returns False); ranges are never merged across a hole,
    so the ledger can never claim a sequence that was not recorded."""

    def __init__(self, cap: int = MAX_LEDGER_RANGES):
        self.cap, self.ranges = cap, []

    def add(self, q: int) -> bool:
        import bisect
        r = self.ranges
        i = bisect.bisect_right(r, [q, float("inf")]) - 1          # the last range starting at or before q
        if i >= 0 and r[i][0] <= q <= r[i][1]:
            return True
        left = i >= 0 and r[i][1] == q - 1
        right = i + 1 < len(r) and r[i + 1][0] == q + 1
        if left and right:
            r[i][1] = r[i + 1][1]
            del r[i + 1]
        elif left:
            r[i][1] = q
        elif right:
            r[i + 1][0] = q
        elif len(r) >= self.cap:
            return False
        else:
            r.insert(i + 1, [q, q])
        return True

    def count_in(self, lo: int, hi: int) -> int:
        return sum(max(0, min(b, hi) - max(a, lo) + 1) for a, b in self.ranges if b >= lo and a <= hi)


class SessionError(Exception):
    def __init__(self, status: int, msg_id: str):
        super().__init__(msg_id)
        self.status, self.msg_id = status, msg_id


def _status_ranges(statuses: dict[int, str], n: int) -> list[dict]:
    out = []
    for i in range(n):
        s = statuses.get(i)
        if s is None:
            continue
        if out and out[-1]["status"] == s and out[-1]["to_frame"] == i - 1:
            out[-1]["to_frame"] = i
        else:
            out.append({"from_frame": i, "to_frame": i, "status": s})
    return out


class Session:
    def __init__(self, sid: str, tmp: Path, cfg: dict, zones: list[dict], detector, runs_dir: Path):
        self.sid, self.tmp, self.cfg, self.zones, self.detector, self.runs_dir = sid, tmp, cfg, zones, detector, runs_dir
        self.q: queue.Queue = queue.Queue(maxsize=QUEUE_MAX)
        self.lock = threading.Lock()
        self.state = "running"  # running | finalizing | done | failed
        self.params, self.tparams = ev.live_params(cfg["interval_ms"])
        self.tracker = IouTracker(self.tparams)
        self.engines = {z["zone_id"]: ZoneEngine(z["zone_id"], self.params, id_context=("live-" + sid, z["digest"])) for z in zones if z["kind"] == "restricted"}   # live ids are PROVISIONAL
        self.records: list[dict] = []  # one per accepted sample, in order: index, t_us(capture), status, objects, reason
        self.jpeg_sha: dict[int, str] = {}   # sha256 of each spooled JPEG; the bytes themselves are on disk, never accumulated in memory
        self.bytes_accepted = 0
        self.retain = bool(cfg["retain_frames"])   # fixed when the session starts; Privacy ON (False) is the default everywhere
        if self.retain:
            (tmp / "frames").mkdir(parents=True, exist_ok=True)
        self.pixel: dict[int, str] = {}
        self.t0 = None  # client capture clock at the first accepted frame; all stored times are relative to it
        self.last_capture_us = -1
        self.last_seq = -1
        self.watermark = 0   # highest "next sequence number" the browser has DECLARED (heartbeat or stop); kept independently of the accepted frames so a vanished client's loss is still recorded
        self.shift_us = 0    # session time of the first accepted frame: seq0 x declared cadence when the session does not begin at sequence 0 (a cadence EXTRAPOLATION, flagged in the accounting)
        self.tail_us = 0     # duration of the final unobserved interval when the session ends with declared lost ticks (declared extent, cadence based)
        self.tail_basis = "none_right_censored"
        self.invalid_requests = 0                # sequence-bearing requests refused before intake (invalid JPEG / size / oversize): they reached the server, they are NOT client loss
        self.ledger = SeqLedger()                # examined-and-not-accepted sequence numbers (cycle-24 MG-WCAM-024)
        self.unrecorded = 0                      # examined requests the ledger had no room for ...
        self.unrecorded_span: list[int] | None = None   # ... and the [min, max] sequence envelope they fall in
        self._spans: dict[int, tuple[int, int]] = {}    # stub index -> the sequence span it stands for (not part of any digest-bound document)
        self.finalized_mono: float | None = None
        self.counters = {"received": 0, "analyzed": 0, "dropped_queue_overflow": 0, "dropped_stale": 0, "adapter_error": 0, "rejected": 0, "client_loss_events": 0, "client_lost_frames": 0}
        self.latency_ms: list[int] = []
        self.max_depth = 0
        self.last_activity = time.monotonic()
        self.latest: dict = {"seq": None, "objects": [], "tracks": [], "capture_us": None, "analyzed_at": None}
        self.risk_now: dict = {}
        self.result_run: str | None = None
        self.error: str | None = None
        self.closed_reason: str | None = None
        self.stop_flag = threading.Event()
        self._final_lock = threading.Lock()  # finalization is single-owner: concurrent stop calls wait for the same completion
        self.worker = threading.Thread(target=self._work, name=f"mg-{sid}", daemon=True)
        self.worker.start()

    # ------------------------------------------------------------------ intake (request thread)
    def submit(self, seq: int, capture_us: int, jpeg: bytes):
        try:
            return self._admit(seq, capture_us, jpeg)
        except SessionError as e:
            if e.msg_id != "mg.refusal.session_not_running":   # the request reached intake and was examined: it is a refusal with an outcome, never client loss (cycle-24 MG-WCAM-024)
                with self.lock:
                    self.counters["rejected"] += 1
                    self._examined(seq)
            raise

    def _examined(self, seq: int | None):
        """Caller holds the lock. Records the outcome «examined, not accepted» of a sequence-bearing request; if the bounded ledger has no room the fact is kept as an explicit unrecorded count."""
        if seq is None:
            return
        if not self.ledger.add(seq):
            self.unrecorded += 1
            u = self.unrecorded_span
            self.unrecorded_span = [seq, seq] if u is None else [min(u[0], seq), max(u[1], seq)]

    def _admit(self, seq: int, capture_us: int, jpeg: bytes):
        with self.lock:
            if self.state != "running":
                raise SessionError(409, "mg.refusal.session_not_running")
            iu = self.cfg["interval_ms"] * 1000
            first = self.t0 is None
            t0 = capture_us if first else self.t0
            shift = seq * iu if first else self.shift_us   # a session that does not begin at sequence 0 starts its clock at the declared cadence position of its first frame
            rel = capture_us - t0 + shift
            if seq <= self.last_seq or rel <= self.last_capture_us:
                raise SessionError(409, "mg.refusal.session_non_monotonic_frame")
            lost = max(0, seq - self.last_seq - 1)   # every capture tick of the browser consumes a sequence number (also skipped and failed ones), so ALL loss is a gap; for the first frame this is the missing PREFIX
            position = "prefix" if first else "gap"
            # the unknown interval starts when the last observation stops being valid (one declared cadence interval after it) and lasts until the next observation
            stub_t = 0 if first else (self.last_capture_us + iu if self.last_capture_us + iu < rel else (self.last_capture_us + rel) // 2)
            if lost > 0 and not (self.last_capture_us < stub_t < rel or (first and stub_t == 0 < rel)):
                raise SessionError(409, "mg.refusal.session_loss_not_representable")   # a declared loss that cannot be recorded as a strictly timed unknown sample is refused, never dropped silently
            if len(self.records) + (2 if lost else 1) > MAX_SAMPLES:
                raise SessionError(409, "mg.refusal.session_sample_limit")
            if self.retain and self.bytes_accepted + len(jpeg) > MAX_SESSION_BYTES:
                raise SessionError(409, "mg.refusal.session_storage_limit")
            self.bytes_accepted += len(jpeg)
            if first:
                self.t0, self.shift_us = capture_us, shift
            # Frames that never reached the server (a gap in the sequence numbers: failed upload, skipped under load, failed encode, or frames before the first accepted one) are an
            # UNDISCLOSED omission unless recorded: a loss stub (not analysed, so the zone state is UNKNOWN there) is inserted so that no hold or dwell continues silently across them.
            if lost > 0:
                self._record_loss(lost, stub_t, position, (self.last_seq + 1, seq - 1))
            self.last_seq, self.last_capture_us = seq, rel
            self.last_activity = time.monotonic()
            self.counters["received"] += 1
            idx = len(self.records)
            self.records.append({"index": idx, "seq": seq, "t_us": rel, "status": "pending", "objects": [], "reason": None})
            item = (idx, jpeg, time.monotonic())
            try:
                self.q.put_nowait(item)
            except queue.Full:
                old = self.q.get_nowait()  # drop the OLDEST queued frame, keep the newest
                self._mark_dropped(old[0], "queue_overflow")
                self.q.put_nowait(item)
            self.max_depth = max(self.max_depth, self.q.qsize())
            return idx

    def declare_watermark(self, next_seq: int) -> dict:
        """Heartbeat: the browser tells the server how many capture ticks it has issued. It does NOT count as activity (a client that only sends heartbeats is still idle) and it is
        monotonic (cumulative: a lost heartbeat is superseded by the next one). Message numbering, duplicates and reordering are handled by the manager's control cache (one place for
        heartbeats and stops, live sessions and finished ones). The idle reaper and any later stop use the watermark to record the loss after the last accepted frame (cycle-19/20 MG-WCAM-019)."""
        with self.lock:
            if self.state != "running":
                raise SessionError(409, "mg.refusal.session_not_running")
            self.watermark = max(self.watermark, next_seq)
            return {"watermark": self.watermark}

    def note_invalid(self, seq: int):
        """A sequence-bearing request the server examined and refused before intake (invalid JPEG, wrong size, oversize). It consumed a capture tick that REACHED the server, so the interval is
        still UNKNOWN (no frame was analysed) but is accounted as `rejected_invalid`, never as loss on the client side (cycle-23 MG-WCAM-023). Recorded in the bounded interval ledger (cycle-24 MG-WCAM-024)."""
        with self.lock:
            if self.state != "running":
                return
            self.invalid_requests += 1
            self._examined(seq)

    def _record_loss(self, lost: int, t_us: int, position: str, span: tuple[int, int]):
        """Caller holds the lock. Frames that never reached the server become one `client_loss` stub (not analysed => the zone state is UNKNOWN there). `position` says where the interval
        lies (prefix: before the first accepted frame; gap: between two accepted frames; tail: after the last one)."""
        self.counters["client_loss_events"] += 1
        self.counters["client_lost_frames"] += lost
        stub = {"index": len(self.records), "seq": None, "t_us": t_us, "status": "not_analyzed_dropped", "reason": "client_loss", "lost_frames": lost, "rejected_frames": 0, "unclassified_frames": 0, "loss_position": position, "objects": []}
        self._spans[stub["index"]] = span
        self.records.append(stub)
        self._feed(stub, [])

    def _mark_dropped(self, idx: int, reason: str):
        r = self.records[idx]
        r["status"], r["reason"] = "not_analyzed_dropped", reason
        self.counters["dropped_queue_overflow" if reason == "queue_overflow" else "dropped_stale"] += 1
        self._feed(r, [])

    # ------------------------------------------------------------------ worker
    def _work(self):
        try:
            while True:
                try:
                    idx, jpeg, recv = self.q.get(timeout=0.2)
                except queue.Empty:
                    if self.stop_flag.is_set():
                        break
                    continue
                with self.lock:
                    r = self.records[idx]
                    if r["status"] != "pending":
                        continue
                    if (time.monotonic() - recv) * 1_000_000 > MAX_AGE_US:
                        self._mark_dropped(idx, "stale")
                        continue
                t0 = time.monotonic()
                try:
                    with Image.open(io.BytesIO(jpeg)) as im:
                        if im.format != "JPEG" or im.size != (self.cfg["width"], self.cfg["height"]):
                            raise ValueError("unexpected image format or size")
                        rgb = np.asarray(im.convert("RGB"))
                    objs, _ = build_objects(self.detector.predict(rgb, self.cfg["threshold_permille"] / 1000), self.cfg["width"], self.cfg["height"])
                    px = pixel_digest(rgb)
                except Exception as e:  # reported as an explicit adapter_error sample; never replaced by synthetic output
                    with self.lock:
                        r["status"], r["reason"] = "adapter_error", f"{type(e).__name__}"
                        self.counters["adapter_error"] += 1
                        self._feed(r, [])
                    continue
                sha = None
                part = final_jpg = None
                if self.retain:
                    try:  # Privacy OFF: the bytes are spooled to a PRIVATE temporary name before the commit; they become the evidence file only INSIDE the commit (below)
                        part = self.tmp / "frames" / f"{idx:06d}.part"
                        final_jpg = self.tmp / "frames" / f"{idx:06d}.jpg"
                        part.write_bytes(jpeg)
                        sha = sha256_hex(jpeg)
                    except OSError as e:
                        self._discard_spool(part)
                        with self.lock:
                            r["status"], r["reason"] = "adapter_error", f"spool_{type(e).__name__}"
                            self.counters["adapter_error"] += 1
                            self._feed(r, [])
                        continue
                del jpeg  # Privacy ON: the encoded frame is dropped here; nothing of it is ever written
                with self.lock:
                    # COMMIT BOUNDARY (cycle-20 MG-WCAM-020): freshness is judged here, under the session lock, immediately before the sample becomes an accepted current observation.
                    # Whatever happened since intake (queueing, inference, spooling, being descheduled) counts. One critical section makes the accepted sample, its frame identity, the
                    # inference result, the tracker/kernel update and the evidence file appear together or not at all.
                    if r["status"] != "pending" or self.state not in ("running", "finalizing"):
                        self._discard_spool(part)
                        continue
                    if (time.monotonic() - recv) * 1_000_000 > MAX_AGE_US:
                        self._discard_spool(part)    # a rejected result leaves no evidence file behind
                        self._mark_dropped(idx, "stale")
                        continue
                    if part is not None:
                        try:
                            os.replace(part, final_jpg)
                        except OSError as e:
                            self._discard_spool(part)
                            r["status"], r["reason"] = "adapter_error", f"spool_{type(e).__name__}"
                            self.counters["adapter_error"] += 1
                            self._feed(r, [])
                            continue
                        self.jpeg_sha[idx] = sha
                    r["status"], r["objects"] = "analyzed", objs
                    self.pixel[idx] = px
                    self.counters["analyzed"] += 1
                    self.latency_ms.append(int((time.monotonic() - t0) * 1000))
                    self._feed(r, objs)
        except Exception as e:  # pragma: no cover - defensive
            self.error = f"{type(e).__name__}: {e}"
            self.state = "failed"

    @staticmethod
    def _discard_spool(part):
        """Removes a test-owned temporary JPEG of a result that was not committed (Privacy OFF only; Privacy ON never has one)."""
        if part is not None:
            try:
                part.unlink()
            except OSError:
                pass

    def _feed(self, r: dict, objs: list[dict]):
        """Records are fed to the tracker and kernel in index order; samples are always completed in capture order because the single worker
        processes the queue FIFO and drops are applied at intake/pop time (the dropped frame's record is fed when its fate is decided)."""
        r["_fed"] = True
        # feed in order: only advance over the contiguous prefix of decided records
        while True:
            nxt = getattr(self, "_next", 0)
            if nxt >= len(self.records) or self.records[nxt]["status"] == "pending":
                break
            rec = self.records[nxt]
            ids = self.tracker.update(rec["t_us"], [(o["cls"]["label"], o["bbox"]) for o in rec["objects"]]) if rec["status"] == "analyzed" else [None] * len(rec["objects"])
            rec["track_ids"] = ids
            brk = frozenset(self.tracker.breaks) if rec["status"] == "analyzed" else frozenset()   # identity breaks of THIS sample, exactly as the offline evaluation derives them
            for z in self.zones:
                if z["zone_id"] in self.engines:
                    self.engines[z["zone_id"]].push(sample_for(z, rec["index"], rec["t_us"], rec["status"], rec["objects"], ids, identity_breaks=brk))
            if rec["status"] == "analyzed":
                self.latest = {"seq": rec["seq"], "index": rec["index"], "capture_us": rec["t_us"],
                               "objects": [{"label": o["cls"]["label"], "bbox": o["bbox"], "permille": o["confidence"]["permille"], "track": t}
                                           for o, t in zip(rec["objects"], ids)], "analyzed_at": time.time()}
            self.risk_now = {zid: e.last_state for zid, e in self.engines.items()}
            self._next = nxt + 1
            self._notify(False)

    def _notify(self, final: bool):
        """Hands the kernel's events of this live session to the alert engine (caller holds the lock). An alerting problem never stops the analysis."""
        al = getattr(self, "alerts", None)
        if al is None:
            return
        try:
            al.observe_session(self.sid, self.cfg["capture"]["device_label"], self.cfg["capture"]["origin"], [e for eng in self.engines.values() for e in eng.snapshot()["events"]], self.risk_now, final)
        except Exception:   # pragma: no cover - defensive
            pass

    # ------------------------------------------------------------------ views
    def view(self) -> dict:
        with self.lock:
            lat = sorted(self.latency_ms)
            return {"session_id": self.sid, "state": self.state, "counters": dict(self.counters), "queue_depth": self.q.qsize(), "queue_max": QUEUE_MAX,
                    "latest": self.latest, "risk": self.risk_now, "interval_ms": self.cfg["interval_ms"], "width": self.cfg["width"], "height": self.cfg["height"],
                    "latency_ms": {"p50": lat[len(lat) // 2] if lat else None, "max": lat[-1] if lat else None},
                    "events": {zid: e.snapshot()["events"] for zid, e in self.engines.items()},
                    "run_id": self.result_run, "error": self.error, "closed_reason": self.closed_reason,
                    "capture": self.cfg["capture"], "zones": self.zones, "retention": "frames" if self.retain else "metadata_only", "accounting": self.accounting()}

    # ------------------------------------------------------------------ finalize
    def stop(self, reason: str = "client_stop", next_seq: int | None = None) -> dict:
        """Exactly-once, idempotent finalization. Never finalizes (and so never lets the inference lock go) while the worker can still mutate state.
        `next_seq` is the first sequence number the browser did NOT issue: everything between the last accepted frame and it is loss AFTER the last frame (failed or unfinished uploads,
        skipped captures, encodes in progress) and is recorded as one final unknown sample inside the SAME critical section that stops intake, so no later frame can duplicate it."""
        with self.lock:
            if self.state == "done":
                return self.view_done()
            if self.state == "running":
                declared = max(self.watermark, next_seq if next_seq is not None else 0)
                self.watermark = declared
                if next_seq is not None:
                    self.tail_basis = "declared_by_stop"           # the browser said how many ticks it issued: the tail count is exact as declared
                elif self.watermark > 0:
                    self.tail_basis = "declared_by_heartbeat"      # only the last heartbeat is known: the tail count is a lower bound
                if declared > 0 and self.records:
                    lost = max(0, declared - self.last_seq - 1)
                    if lost > 0:
                        iu = self.cfg["interval_ms"] * 1000
                        self.tail_us = lost * iu            # the declared extent of the missing ticks (cadence based): NOT compressed into one short stub
                        self._record_loss(lost, self.last_capture_us + iu, "tail", (self.last_seq + 1, declared - 1))
                self.state = "finalizing"
                self.closed_reason = self.closed_reason or reason
        self.stop_flag.set()
        with self._final_lock:
            with self.lock:
                if self.state == "done":
                    return self.view_done()
                if self.state == "failed":
                    raise SessionError(500, "mg.refusal.session_failed")
            self.worker.join(timeout=WORKER_JOIN_S)
            if self.worker.is_alive():
                raise SessionError(503, "mg.refusal.session_worker_busy")  # retryable: the detector call has not returned yet; nothing was written
            with self.lock:
                if self.state == "failed":
                    raise SessionError(500, "mg.refusal.session_failed")
                try:
                    self._finalize()
                except SessionError:
                    self.state = "failed"
                    raise
                except Exception as e:  # disk full, permission, unexpected error: the session must never stay "finalizing" (cycle-4 review MG-WCAM-015)
                    self.state, self.error = "failed", f"{type(e).__name__}"
                    raise SessionError(500, "mg.refusal.session_failed")
                self.state = "done"
                return {**self.view_done(), "fresh": True}   # the unique transition that produced the stored artifact (the only call allowed to grant trust)

    def _live_stats(self, pts: list[int]) -> dict:
        """MEASURED behaviour of this session (not a promise): inference latency, deepest queue, effective analysis rate."""
        lat = sorted(self.latency_ms)
        q = lambda f: lat[min(len(lat) - 1, int(f * len(lat)))] if lat else None
        span_ms = (pts[-1] - pts[0]) // 1000 if len(pts) > 1 else 0   # integers only: this document is hashed (canonical JSON forbids floats)
        return {"latency_ms": {"p50": q(0.5), "p95": q(0.95), "max": lat[-1] if lat else None}, "queue_depth_max_observed": self.max_depth, "queue_max": QUEUE_MAX,
                "span_ms": span_ms, "analyzed_millifps": (self.counters["analyzed"] * 1_000_000) // span_ms if span_ms > 0 else None,
                "capture_interval_ms": self.cfg["interval_ms"], "intake_rejected": self.counters["rejected"], "accounting": self.accounting()}

    def _reconcile_stubs(self):
        """Classifies, from the interval ledger, the ticks each unknown stub stands for (idempotent; the ledger only grows, so a request that arrives AFTER the stub was created is still found):
        ticks the server examined and refused -> `rejected_frames`; examined requests the full ledger could not record -> `unclassified_frames` (at most the unrecorded count, only inside their envelope);
        the rest never arrived. An examined request is never classed as never observed (cycle-24 MG-WCAM-024). Caller holds the lock or the session is closed."""
        left = self.unrecorded
        for r in self.records:
            span = self._spans.get(r["index"]) if r.get("reason") == "client_loss" else None
            if span is None:
                continue
            lo, hi = span
            rej = min(r["lost_frames"], self.ledger.count_in(lo, hi))
            unc = 0
            if left and self.unrecorded_span:
                inter = max(0, min(hi, self.unrecorded_span[1]) - max(lo, self.unrecorded_span[0]) + 1)
                unc = min(left, r["lost_frames"] - rej, inter)
                left -= unc
            r["rejected_frames"], r["unclassified_frames"] = rej, unc

    def accounting(self, closed: str | None = None) -> dict:
        """Where every capture tick went (integers/strings only: this document is hashed). captured_declared is what the browser SAYS it issued (None if it never said);
        submitted = sequence-bearing requests that reached the server and were examined (accepted + refused at intake + refused at the transport); accepted = frames admitted to the queue;
        analysed / dropped as counted. Ticks that produced no accepted frame are the prefix, gap and tail intervals (UNKNOWN); of those, `rejected_invalid_frames` reached the server and were
        REFUSED (invalid JPEG, wrong size, oversize) and `never_observed_frames` never arrived at all, so prefix + gap + tail = never_observed + rejected_invalid. The tail is exact only when
        a clean stop declared it."""
        c = self.counters
        self._reconcile_stubs()
        loss = lambda pos: sum(r["lost_frames"] for r in self.records if r.get("reason") == "client_loss" and r.get("loss_position") == pos)
        stubs = sum(1 for r in self.records if r.get("reason") == "client_loss")
        rej = sum(r.get("rejected_frames", 0) for r in self.records if r.get("reason") == "client_loss")
        unc = sum(r.get("unclassified_frames", 0) for r in self.records if r.get("reason") == "client_loss")
        closed = closed or self.closed_reason or "client_stop"
        return {"captured_declared": (max(self.watermark, self.last_seq + 1) if self.watermark > 0 else None), "submitted": c["received"] + c["rejected"] + self.invalid_requests, "accepted": c["received"],
                "analyzed": c["analyzed"], "dropped": c["dropped_queue_overflow"] + c["dropped_stale"], "adapter_error": c["adapter_error"],
                "never_observed_frames": c["client_lost_frames"] - rej - unc, "rejected_invalid_frames": rej, "unclassified_frames": unc, "invalid_requests": self.invalid_requests,
                "prefix_frames": loss("prefix"), "gap_frames": loss("gap"), "tail_frames": loss("tail"), "unknown_intervals": stubs,
                "origin_basis": "cadence_extrapolation" if self.shift_us > 0 else "measured", "tail_basis": self.tail_basis, "tail_exact": self.tail_basis == "declared_by_stop",
                "closure": "clean_stop" if closed == "client_stop" else ("device_ended" if closed == "device_ended" else "unexpected_disconnect")}

    def view_done(self) -> dict:
        return {"session_id": self.sid, "state": "done", "run_id": self.result_run, "closed_reason": self.closed_reason, "counters": dict(self.counters)}

    def _finalize(self):
        self._reconcile_stubs()
        for r in self.records:
            if r["status"] == "pending":  # accepted but never decided (stop raced the queue): recorded honestly as dropped
                r["status"], r["reason"] = "not_analyzed_dropped", "session_stopped"
                self.counters["dropped_stale"] += 1
        self._feed(self.records[-1], []) if self.records else None
        n = len(self.records)
        analyzed = [r for r in self.records if r["status"] == "analyzed"]
        if not analyzed:
            raise SessionError(422, "mg.refusal.session_no_analyzed_frames")
        W, H = self.cfg["width"], self.cfg["height"]
        pts = [r["t_us"] for r in self.records]
        statuses = {r["index"]: r["status"] for r in self.records if r["status"] != "analyzed"}
        sample_log = [{"seq": r["seq"], "capture_us": r["t_us"], "status": r["status"], "reason": r["reason"],
                       "jpeg_sha256": self.jpeg_sha.get(r["index"]), **({"lost_frames": r["lost_frames"], "rejected_frames": r["rejected_frames"], "unclassified_frames": r["unclassified_frames"], "loss_position": r["loss_position"]} if r.get("reason") == "client_loss" else {})} for r in self.records]
        if not sd.samples_valid(sample_log, "frames" if self.retain else "metadata_only"):
            raise SessionError(500, "mg.refusal.session_bad_sample_log")   # MG-WCAM-032: never store a log that verification would refuse
        capture_decl = {**self.cfg["capture"], "first_capture_clock_us": self.t0}   # client-declared, but bound into the source digest: editing it changes the run's identity
        closed = self.closed_reason or "client_stop"
        if closed not in sd.CLOSE_REASONS:
            raise SessionError(500, "mg.refusal.session_bad_close_reason")
        live_stats = self._live_stats(pts)
        # everything that is RECORDED rather than derivable (capture declaration, close reason, measured stats) is bound into the source digest
        retention = sd.retention_doc("frames" if self.retain else "metadata_only")
        provenance = sd.build_provenance(self.detector.device, self.detector.entry, self.detector.weights_sha256, capture_decl)
        prov_sha = digest(provenance)
        source_digest = digest(sd.source_digest_payload(sample_log, capture_decl, closed, live_stats, retention, prov_sha, sd.SESSION_FORMAT))
        frame_table = {"time_base": {"num": 1, "den": 1_000_000}, "start_ticks": pts[0], "pts_ticks": pts,
                       "last_frame_duration_ticks": self.tail_us if self.tail_us else ((pts[-1] - pts[-2]) if n > 1 else self.cfg["interval_ms"] * 1000), "width": W, "height": H, "rotation": 0}
        source = {"source_id": "src_" + digest16(source_digest), "kind": "webcam", "source_digest": source_digest,
                  "media": {"container": "browser_jpeg_stream", "codec_fourcc": "JPEG", "native_width": W, "native_height": H, "analysis_width": W,
                            "analysis_height": H, "nominal_fps": round(1000 / self.cfg["interval_ms"], 3), "frame_count": n},
                  "provenance_sha256": prov_sha, "frame_table": frame_table, "frame_table_provider": {"id": "browser-capture-clock", "version": "client-declared"},
                  "frame_table_digest": digest(frame_table), "calibration": None, "samples": sample_log, "capture": capture_decl,
                  "closed_reason": closed, "live_stats": live_stats, "retention": retention, "session_format": sd.SESSION_FORMAT}
        params = {"mode": "webcam", "threshold_permille": self.cfg["threshold_permille"], "resolution": int(self.detector.resolution), "decode_width": W,
                  "decode_height": H, "device": self.detector.device, "capture_interval_ms": self.cfg["interval_ms"], "queue_max": QUEUE_MAX,
                  "max_age_ms": MAX_AGE_US // 1000}
        frames = [{"frame_index": r["index"], "status": "analyzed", "pixel_sha256": self.pixel[r["index"]], "objects": r["objects"]} for r in analyzed]
        obs = {"schema": LIVE_SCHEMA_ID,
               "source_binding": {"source_digest": source_digest, "frame_table_digest": source["frame_table_digest"], "frame_count": n, "width": W, "height": H,
                                  "native_width": W, "native_height": H},
               "coord_space": "image_px_v1", "taxonomy": taxonomy.TAXONOMY, "origin": "model_inference",
               "producer": {"adapter_id": ADAPTER_ID, "adapter_version": __version__, "model_id": self.detector.entry["model_id"],
                            "weights_sha256": self.detector.weights_sha256, "params_hash": digest(params),
                            "label_map_digest": taxonomy.label_map_digest(), "params": params},
               "tracks": [], "frames": frames, "frame_status_ranges": _status_ranges(statuses, n),
               "meta": {"created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tool": f"mineguard-vs02/{__version__}",
                        "elapsed_ms": int(pts[-1] / 1000)}}
        try:
            validate_observation_set(obs, live=True)
        except ValidationFailure as e:
            raise SessionError(500, "mg.refusal.session_invalid_observations:" + ",".join(e.codes))
        od = obsset_digest(obs)
        sid = "session_" + digest16(od)
        evidence = sd.build_evidence(frames, sample_log, pts, retention["mode"])
        end_us = pts[-1] + frame_table["last_frame_duration_ticks"]
        manifest = sd.build_manifest(sid, source_digest, source["frame_table_digest"], od, evidence, sample_log, closed, live_stats, retention)
        # risk evaluation: recomputed offline from the stored records (and compared with the live engines: they must agree)
        records = [{"index": r["index"], "t_us": r["t_us"], "status": r["status"], "objects": r["objects"]} for r in self.records]
        inputs = {"obsset_digest": od, "source_digest": source_digest, "frame_table_digest": source["frame_table_digest"], "kind": "webcam",
                  "frame_table_provider": source["frame_table_provider"], "run_config": obs["producer"]["params_hash"]}
        evaluation = ev.attach_evidence(ev.evaluate_records(records, end_us, self.zones, self.params, self.tparams, inputs, "model_inference"), evidence)
        bundle = {"schema": "mg.export.vs02.session", "observation_set": obs, "source": source, "provenance": provenance, "manifest": manifest}
        final = self.runs_dir / sid
        tmp = self.tmp
        (tmp / "risk").mkdir(exist_ok=True)   # frames/ was spooled to disk as each frame was analyzed
        for name, doc in (("observations.json", obs), ("source.json", source), ("provenance.json", provenance), ("manifest.json", manifest), ("export.json", bundle)):
            (tmp / name).write_text(json.dumps(doc, indent=1, ensure_ascii=False))
        (tmp / "risk" / "zones.json").write_text(json.dumps({"revision": 1, "zones": self.zones}, indent=1))
        (tmp / "risk" / "evaluation.json").write_text(json.dumps(evaluation, indent=1))
        from .analysis import install_dir
        try:
            with STORE_LOCK:
                install_dir(tmp, final, od)
        except RuntimeError as e:
            raise SessionError(409, str(e))
        self.result_run = sid
        self.finalized_mono = time.monotonic()
        self._notify(True)


LATE_SCHEMA = "mg.addendum.late_declaration.v1"
LATE_FILE = "late_declaration.json"
MAX_LATE_DECLARATIONS = 8


class Tombstone:
    """What remains of a finished session in memory: its outcome only. The records, tracker, engines, latency samples and frame hashes are released
    (the artifact is on disk); a bounded number of tombstones is kept (cycle-3 review MG-WCAM-014). It also keeps what the bounded late-declaration window needs."""

    def __init__(self, s: "Session"):
        self.sid, self.state, self.result_run, self.closed_reason, self.error = s.sid, s.state, s.result_run, s.closed_reason, s.error
        self.counters = dict(s.counters)
        self.cfg = {k: s.cfg[k] for k in ("width", "height", "interval_ms", "retain_frames")} | {"capture": s.cfg["capture"]}
        self.worker = None
        self.last_seq, self.last_activity, self.finalized_mono = s.last_seq, s.last_activity, s.finalized_mono
        self.accounting_doc = s.accounting() if s.state == "done" else None

    def view(self) -> dict:
        return {"session_id": self.sid, "state": self.state, "counters": self.counters, "queue_depth": 0, "queue_max": QUEUE_MAX, "latest": {"seq": None, "objects": []},
                "risk": {}, "interval_ms": self.cfg["interval_ms"], "width": self.cfg["width"], "height": self.cfg["height"], "latency_ms": {"p50": None, "max": None},
                "events": {}, "run_id": self.result_run, "error": self.error, "closed_reason": self.closed_reason, "capture": self.cfg["capture"], "zones": [],
                "retention": "frames" if self.cfg["retain_frames"] else "metadata_only", "accounting": self.accounting_doc}

    def view_done(self) -> dict:
        return {"session_id": self.sid, "state": "done", "run_id": self.result_run, "closed_reason": self.closed_reason, "counters": self.counters}

    def submit(self, *a, **k):
        raise SessionError(409, "mg.refusal.session_not_running")

    def stop(self, reason: str = "client_stop", next_seq: int | None = None) -> dict:
        if self.state == "done":
            return self.view_done()
        raise SessionError(500, "mg.refusal.session_failed")


MAX_TOMBSTONES = 50


class SessionManager:
    def __init__(self, st, runs_dir: Path, detector_factory):
        self.st, self.runs_dir, self.detector_factory = st, runs_dir, detector_factory
        self.sessions: dict[str, Session] = {}
        self.active: str | None = None
        self.lock = threading.Lock()
        self._reaper: threading.Thread | None = None
        self.ctl: dict[str, dict] = {}        # sid -> {message number: (payload, answer)}
        self.ctl_high: dict[str, int] = {}
        self.ctl_locks: dict[str, threading.Lock] = {}
        self.alerts = None   # set by the application: the alert engine that receives the kernel events of live sessions

    def _reap_loop(self):
        """Autonomous idle handling: a client that vanished is finalized (right-censored, reason client_disconnected) without any further request.
        Nothing that goes wrong for one session may stop the reaper: every iteration is guarded."""
        while True:
            time.sleep(0.25)
            for s in list(self.sessions.values()):
                try:
                    if not isinstance(s, Session):
                        continue
                    if s.state == "running" and time.monotonic() - s.last_activity > IDLE_FINALIZE_S:
                        s.closed_reason = s.closed_reason or "client_disconnected"
                        s.stop("client_disconnected")
                    elif s.state == "finalizing" and not s.worker.is_alive():
                        s.stop(s.closed_reason or "client_stop")   # a retryable 503 left it finalizing: finish it now that the detector call has returned
                except SessionError:
                    pass
                except Exception:  # pragma: no cover - defensive: the session is marked failed below, the reaper keeps running
                    if s.state not in ("done", "failed"):
                        s.state = "failed"
                with self.lock:
                    if s.state in ("done", "failed"):
                        self._release(s)

    def _active(self) -> Session | None:
        s = self.sessions.get(self.active) if self.active else None
        if s and s.state in ("done", "failed"):
            self._release(s)
            return None
        return s

    def _release(self, s: Session):
        """Gives the inference lock back only when the session is over AND its worker thread has really exited."""
        if self.active == s.sid and s.state in ("done", "failed") and not s.worker.is_alive():
            self.active = None
            if self.st.infer_lock.locked():
                try:
                    self.st.infer_lock.release()
                except RuntimeError:
                    pass
        if isinstance(s, Session) and s.state in ("done", "failed") and not s.worker.is_alive() and self.sessions.get(s.sid) is s:
            if s.state == "failed":
                shutil.rmtree(s.tmp, ignore_errors=True)   # a failed finalization never leaves its staging directory behind, whichever path got here
            self.sessions[s.sid] = Tombstone(s)            # every heavy structure becomes unreachable
            old = [k for k, v in self.sessions.items() if isinstance(v, Tombstone)]
            for k in old[:-MAX_TOMBSTONES]:
                del self.sessions[k]
                self.ctl.pop(k, None); self.ctl_high.pop(k, None); self.ctl_locks.pop(k, None)   # the control state never outlives the session it belongs to

    def start(self, width: int, height: int, interval_ms: int, threshold: float, zones_in: list[dict], capture: dict, retain_frames: bool = False) -> dict:
        with self.lock:
            cur = self._active()
            if cur is not None:
                raise SessionError(409, "mg.refusal.session_in_progress")
            if not (MIN_DIM <= width <= MAX_DIM and MIN_DIM <= height <= MAX_DIM):
                raise SessionError(422, "mg.refusal.session_bad_dimensions")
            if not 100 <= interval_ms <= 5000:
                raise SessionError(422, "mg.refusal.session_bad_interval")
            thr = permille(threshold)
            if not 1 <= thr <= 999:
                raise SessionError(422, "mg.refusal.session_bad_threshold")
            origin = capture.get("origin")
            if origin not in CAPTURE_ORIGINS:
                raise SessionError(422, "mg.refusal.session_bad_capture_origin")
            zones = []
            for z in zones_in:
                try:
                    zones.append(make_zone(z["zone_id"], z.get("kind", "restricted"), z["polygon"], width, height, z.get("name")))
                except Exception as e:
                    raise SessionError(422, getattr(e, "args", ["mg.refusal.zone_invalid"])[0] if e.args else "mg.refusal.zone_invalid")
            if len({z["zone_id"] for z in zones}) != len(zones) or len(zones) > 8:
                raise SessionError(422, "mg.refusal.zone_set_invalid")
            if not self.st.infer_lock.acquire(blocking=False):
                raise SessionError(409, "mg.refusal.job_in_progress")
            try:
                if self.st.detector is None:
                    self.st.detector = self.detector_factory()
                sid = "sess_" + uuid.uuid4().hex[:12]
                tmp = self.runs_dir / f"_tmp_{uuid.uuid4().hex[:8]}"
                tmp.mkdir(parents=True)
                cfg = {"width": width, "height": height, "interval_ms": interval_ms, "threshold_permille": thr, "retain_frames": bool(retain_frames),
                       "capture": {"origin": origin, "device_label": str(capture.get("device_label", ""))[:80], "user_agent": str(capture.get("user_agent", ""))[:160]}}
                s = Session(sid, tmp, cfg, zones, self.st.detector, self.runs_dir)
                s.alerts = self.alerts
            except BaseException:
                self.st.infer_lock.release()
                raise
            self.sessions[sid] = s
            self.active = sid
            if self._reaper is None:
                self._reaper = threading.Thread(target=self._reap_loop, name="mg-session-reaper", daemon=True)
                self._reaper.start()
            return {"session_id": sid, "queue_max": QUEUE_MAX, "max_frame_bytes": MAX_FRAME_BYTES, "interval_ms": interval_ms, "zones": zones}

    def get(self, sid: str) -> "Session | Tombstone":
        s = self.sessions.get(sid)
        if not s:
            raise SessionError(404, "mg.refusal.unknown_session")
        return s

    # ------------------------------------------------------------------ numbered control messages (MG-WCAM-019 / 022)
    def _sid_lock(self, sid: str) -> threading.Lock:
        with self.lock:
            return self.ctl_locks.setdefault(sid, threading.Lock())

    def control(self, sid: str, kind: str, next_seq: int | None, msg_seq: int | None = None, reason: str = "client_stop") -> dict:
        """ONE entry point for heartbeats and stops, for live sessions and finished ones. A message number makes the call idempotent: a retry of the same message (same payload) gets the
        ORIGINAL answer with `duplicate: true` and changes nothing; the same number with a DIFFERENT payload is refused (409); a number lower than one already seen is `reordered`
        (cumulative watermarks and the terminal stop make that harmless). The cache is bounded (CTL_CACHE) and per session; errors are never cached, so a retryable 503 can be retried."""
        payload = (kind, next_seq, reason if kind == "stop" else None)
        with self._sid_lock(sid):
            cache = self.ctl.setdefault(sid, {})
            if msg_seq is not None and msg_seq in cache:
                key, resp = cache[msg_seq]
                if key != payload:
                    raise SessionError(409, "mg.refusal.control_message_conflict")
                return {**resp, "duplicate": True}
            high = self.ctl_high.get(sid, -1)
            out = self._stop(sid, reason, next_seq) if kind == "stop" else self._watermark(sid, next_seq)
            if msg_seq is not None:
                out = {**out, "ack": msg_seq, "reordered": msg_seq < high, "duplicate": False}
                self.ctl_high[sid] = max(high, msg_seq)
                cache[msg_seq] = (payload, {k: v for k, v in out.items() if k != "fresh"})   # `fresh` (the one-time trust grant) is never replayed
                while len(cache) > CTL_CACHE:
                    del cache[min(cache)]
            return out

    def stop(self, sid: str, reason: str = "client_stop", next_seq: int | None = None, msg_seq: int | None = None) -> dict:
        return self.control(sid, "stop", next_seq, msg_seq, reason)

    def watermark(self, sid: str, next_seq: int, msg_seq: int | None = None) -> dict:
        return self.control(sid, "watermark", next_seq, msg_seq)

    def _stop(self, sid: str, reason: str, next_seq: int | None) -> dict:
        s = self.get(sid)
        try:
            out = s.stop(reason, next_seq) if isinstance(s, Session) else s.stop(reason, next_seq)
            if isinstance(s, Tombstone) and next_seq is not None:
                out = {**out, "late_declaration": self.late_declare(s, next_seq)}
            return out
        finally:
            with self.lock:
                if s.state in ("done", "failed"):
                    self._release(s)

    def _watermark(self, sid: str, next_seq: int) -> dict:
        s = self.get(sid)
        if isinstance(s, Session):
            return s.declare_watermark(next_seq)
        if s.state == "done" and s.closed_reason == "client_disconnected":
            return {"watermark": next_seq, "late": True, "late_declaration": self.late_declare(s, next_seq)}
        raise SessionError(409, "mg.refusal.session_not_running")

    def late_declare(self, t: "Tombstone", next_seq: int) -> dict:
        """Server-side admissibility of a loss declaration that arrives AFTER an unexpected finalization (cycle-20 MG-WCAM-019). Admitted only when ALL hold: the session was closed as
        client_disconnected (a clean stop is final); the window ADDENDUM_WINDOW_S since finalization is still open; the declaration is plausible (the browser cannot have issued more ticks
        than the time since its last accepted frame allows at the declared cadence); it is monotonic. The result is a SIDECAR file that is not part of any digest: the stored evidence is
        never rewritten. Anything else is refused with a reason and the interval stays unobserved with an unknown exact frame count."""
        def refuse(why):
            return {"admitted": False, "reason": why}
        if t.state != "done" or not t.result_run or t.closed_reason != "client_disconnected":
            return refuse("session_was_closed_cleanly") if t.closed_reason in ("client_stop", "device_ended") else refuse("session_not_finalized")
        now = time.monotonic()
        if t.finalized_mono is None or now - t.finalized_mono > ADDENDUM_WINDOW_S:
            return refuse("declaration_window_closed")
        iu = t.cfg["interval_ms"] / 1000.0
        tail = next_seq - t.last_seq - 1
        if tail < 0:
            return refuse("declaration_precedes_last_accepted_frame")
        if tail > int((now - t.last_activity) / iu) + 2:
            return refuse("declaration_not_plausible_for_elapsed_time")
        with STORE_LOCK:
            try:
                # the session directory is opened ONCE without following a symlink and every read/write below is relative to that descriptor: replacing the path afterwards cannot redirect them
                with safedir.owned_dir(self.runs_dir, t.result_run, create=False) as fd:
                    try:
                        cur = json.loads(safedir.read_regular(fd, LATE_FILE, 200_000))
                        if cur.get("schema") != LATE_SCHEMA or not isinstance(cur.get("declarations"), list):
                            return refuse("addendum_not_writable")
                    except FileNotFoundError:
                        cur = {"schema": LATE_SCHEMA, "declarations": []}
                    decls = cur["declarations"]
                    if decls and next_seq <= decls[-1]["declared_next_seq"]:
                        return {"admitted": False, "reason": "not_monotonic", "current": decls[-1]}
                    if len(decls) >= MAX_LATE_DECLARATIONS:
                        return refuse("too_many_declarations")
                    decls.append({"declared_next_seq": next_seq, "last_accepted_seq": t.last_seq, "unobserved_tail_frames_declared": tail,
                                  "received_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "seconds_after_finalization": int(now - t.finalized_mono)})
                    doc = {"schema": LATE_SCHEMA, "digest_bound": False, "session": t.result_run,
                           "note": "client declaration received after the session was finalized as client_disconnected; informational, unverifiable, not part of any digest; the stored evidence was not rewritten",
                           "window_s": int(ADDENDUM_WINDOW_S), "declarations": decls}
                    safedir.write_atomic(fd, LATE_FILE, json.dumps(doc, indent=1))
            except (FileNotFoundError, safedir.UnsafeDir):
                return refuse("session_directory_unavailable")
            except (OSError, ValueError, KeyError, TypeError):
                return refuse("addendum_not_writable")
        return {"admitted": True, "unobserved_tail_frames_declared": tail, "digest_bound": False}


def session_digest_check(source: dict) -> bool:
    return source["source_digest"] == digest(sd.source_digest_payload(source["samples"], source["capture"], source["closed_reason"], source["live_stats"], source.get("retention"), source.get("provenance_sha256"), source.get("session_format")))
