"""Deterministic risk kernel, semantics_version 2 (MG-RISK-002). Pure: event time only (sample timestamps in integer microseconds); no clock, I/O,
randomness, detector or tracker imports. One ZoneEngine evaluates ONE zone for the built-in rules R_PERSON_AND_VEHICLE_IN_ZONE 1.0.0 (RED) and
R_PERSON_DWELL_NO_VEHICLE 1.0.0 (YELLOW). It is causal: a decision at sample t uses samples <= t only (no look-ahead), so live and replayed evaluation agree.

Values are Kleene three-valued: "T", "F", "U" (unknown). Unknown input never silently becomes F or GREEN."""
from __future__ import annotations

from dataclasses import dataclass, field

SEMANTICS_VERSION = 2
ENGINE_VERSION = "mg-risk-kernel/2.0.3"   # 2.0.3: MG-RISK-029 a trailing silence is UNKNOWN whatever the episode state; 2.0.2: MG-RISK-028 closing holds that expire inside an unobserved silence close at their boundary; 2.0.1: cycle-17 conformance corrections (dwell restart with a pending bridge, pending vehicle-free hold, dwell_us >= 1); semantics_version unchanged
RULE_R1 = ("R_PERSON_AND_VEHICLE_IN_ZONE", "1.0.0")
RULE_R2 = ("R_PERSON_DWELL_NO_VEHICLE", "1.0.0")
T, F, U = "T", "F", "U"
GREEN, YELLOW, RED, UNKNOWN = "GREEN", "YELLOW", "RED", "UNKNOWN"
HOLE_CAUSE = {"not_analyzed": "frame_not_analyzed", "source_gap": "source_gap", "adapter_error": "adapter_error"}
BRIDGEABLE = {"frame_not_analyzed", "source_gap", "class_unknown_or_ambiguous", "below_confidence_threshold", "object_truncated_at_frame_edge", "track_absent"}
UNTRACKED_KEY = "*untracked*"


def k_and(a: str, b: str) -> str:
    return F if F in (a, b) else (U if U in (a, b) else T)


def k_or(a: str, b: str) -> str:
    return T if T in (a, b) else (U if U in (a, b) else F)


def k_not(a: str) -> str:
    return {T: F, F: T, U: U}[a]


class PolicyError(ValueError):
    pass


@dataclass(frozen=True)
class Params:
    gap_tolerance_us: int = 1_000_000
    track_gap_tolerance_us: int = 500_000
    track_unknown_hold_us: int = 3_000_000
    debounce_us: int = 500_000
    dwell_us: int = 1_500_000
    max_sample_gap_us: int = 100_000  # a longer silence between two samples is an unknown interval (source_gap), never "nothing happened"

    def __post_init__(self):
        for k, v in self.__dict__.items():
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 3_600_000_000:
                raise PolicyError(f"{k} must be an integer number of microseconds in [0, 3.6e9]")
        if self.max_sample_gap_us < 1:
            raise PolicyError("max_sample_gap_us must be >= 1")
        if self.dwell_us < 1:
            raise PolicyError("dwell_us must be >= 1")   # a zero dwell would confirm presence with no observation time (cycle-17 MG-RISK-017)
        if self.track_gap_tolerance_us > self.track_unknown_hold_us:
            raise PolicyError("track_gap_tolerance_us must be <= track_unknown_hold_us")

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class Sample:
    """What Zone Evaluation gives the kernel for ONE zone at ONE instant. (value, cause) pairs; cause is None unless the value is U."""
    t_us: int
    index: int = 0
    status: str = "analyzed"  # analyzed | not_analyzed | source_gap | adapter_error
    persons: dict = field(default_factory=dict)  # track_id -> (V, cause): tracked person items present in this sample
    untracked_person: tuple = (F, None)
    person_other: tuple = (F, None)  # ambiguous objects that may be persons
    vehicle: tuple = (F, None)  # class-level vehicle occupancy
    terminated: frozenset = frozenset()  # tracks the producer declares to have left the view
    identity_breaks: frozenset = frozenset()  # tracks whose identity continuity the producer does not vouch for at this sample
    notes: tuple = ()  # unknown causes that must stay visible even when they do not change a value (e.g. an ambiguous object next to a determinate one)


class _Track:
    def __init__(self):
        self.run_start = None
        self.last_t = None  # last sample time with value T
        self.u_since = None
        self.u_causes: set = set()
        self.last_seen_t = None
        self.last_value = None
        self.terminated = False
        self.dropped = False
        self.left_censored = False
        self.discontinuity = False
        self.lost_run = False  # the dwell run was dropped by an unbridgeable gap; a return starts a NEW identity run (flagged)


class _Machine:
    def __init__(self, rule, key, open_hold, close_hold):
        self.rule, self.key, self.open_hold, self.close_hold = rule, key, open_hold, close_hold
        self.mode = "INACTIVE"
        self.cand_start = self.cand_idx = None
        self.ev = None  # the current episode (dict) while ACTIVE/CLOSING
        self.closing_start = self.closing_idx = None
        self.closing_vals: list = []
        self.closing_causes: set = set()
        self.closing_unknown = self.closing_disc = False
        self.cand_last = None

    @property
    def live(self):
        return self.mode in ("ACTIVE", "CLOSING")


def _hash16(obj) -> str:
    """First 16 hex of SHA-256 over the canonical JSON encoding (no whitespace, UTF-8, integers and strings only) of `obj`."""
    import hashlib
    import json
    return hashlib.sha256(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()[:16]


class ZoneEngine:
    def __init__(self, zone_id: str, params: Params | None = None, epoch: int = 0, id_context: tuple = ("", "")):
        """id_context = (input_digest, zone_digest): event ids are bound to the stored input and the exact zone geometry (MG-DATA-001 event id tuple)."""
        self.zone_id, self.p, self.epoch, self.id_context = zone_id, params or Params(), epoch, tuple(id_context)
        self.tracks: dict[str, _Track] = {}
        self.machines: dict[tuple, _Machine] = {}
        self.events: list[dict] = []  # closed episodes
        self.suppressed: list[dict] = []
        self.dropped_tracks: list[dict] = []
        self.intervals: list[dict] = []
        self.coverage_us = {"total": 0, "analyzed": 0, GREEN: 0, YELLOW: 0, RED: 0, UNKNOWN: 0}
        self._prev: Sample | None = None
        self._prev_step_t: int | None = None
        self._first_analyzed_t = None
        self._nv_start = None
        self._cur = None
        self._cur_start = None
        self._last_t = None
        self._finished = False
        self._unknown_causes: dict[str, int] = {}
        self.last_state: dict = {"state": UNKNOWN, "pending": [], "causes": ["no_samples"], "t_us": None}

    # ------------------------------------------------------------------ transactional sample (cycle-24 MG-FEED-024)
    _LIVE = ("tracks", "machines", "coverage_us", "_unknown_causes", "last_state", "_cur", "_cur_doc")       # mutated in place by a sample: copied
    _APPEND_ONLY = ("events", "suppressed", "dropped_tracks", "intervals")                                  # only appended to (and the LAST interval is extended in place)

    def checkpoint(self) -> dict:
        """The state a sample can change, captured BEFORE it is pushed: small live state is copied, long histories are only measured. `rollback` returns the engine to exactly this state."""
        import copy
        d = self.__dict__
        return {"live": {k: copy.deepcopy(d[k]) for k in self._LIVE if k in d}, "lens": {k: len(d[k]) for k in self._APPEND_ONLY},
                "last_interval": copy.deepcopy(self.intervals[-1]) if self.intervals else None,
                "scalars": {k: v for k, v in d.items() if k not in self._LIVE and k not in self._APPEND_ONLY}}

    def rollback(self, cp: dict):
        d = self.__dict__
        for k in [k for k in self._LIVE if k in d and k not in cp["live"]]:
            del d[k]
        d.update(cp["scalars"])
        d.update(cp["live"])
        for k, n in cp["lens"].items():
            del d[k][n:]
        if cp["last_interval"] is not None:
            d["intervals"][-1].clear()
            d["intervals"][-1].update(cp["last_interval"])

    # ------------------------------------------------------------------ public
    def push(self, s: Sample):
        """Consume the next sample (strictly increasing t_us). A silence longer than max_sample_gap_us first inserts one pseudo-sample (source_gap)."""
        if self._finished:
            raise PolicyError("engine already finished")
        if self._prev is None and s.t_us > 0 and self._cur is None:
            self._open_unknown_prefix()  # nothing was observed before the first sample: UNKNOWN, never silently absent
        if self._prev is not None:
            if s.t_us <= self._prev.t_us:
                raise PolicyError("sample times must be strictly increasing")
            if s.t_us - self._prev.t_us > self.p.max_sample_gap_us:
                self._gap_step()
                self._expire_unobserved(s.t_us, inclusive=False)   # a hold that ran out INSIDE the silence ends there, not at the returning sample (MG-RISK-028)
        self._step(s)
        self._prev = s

    def _gap_step(self):
        g = Sample(t_us=self._prev.t_us + self.p.max_sample_gap_us, index=self._prev.index, status="source_gap")
        self._step(g, pseudo=True)

    def _expire_unobserved(self, upto: int, inclusive: bool):
        """MG-RISK-028. An episode in CLOSING whose close hold expires at a time with no sample (inside a silence, or before the stream end) closes AT that boundary
        (`closing_start + close_hold`), deterministically and causally: nothing is observed there, so no sample is fabricated and no later sample is consulted. From the
        boundary the interval is UNKNOWN (`source_gap`) until the next sample. Dwell/hold clocks are not touched; a returning sample starts any new candidate itself."""
        while True:
            due = [m for m in self.machines.values() if m.mode == "CLOSING" and (m.closing_start + m.close_hold <= upto if inclusive else m.closing_start + m.close_hold < upto)]
            if not due:
                return
            tb = min(m.closing_start + m.close_hold for m in due)
            for m in sorted((m for m in due if m.closing_start + m.close_hold == tb), key=lambda m: m.key):
                reason = "track_discontinuity" if m.closing_disc else "inputs_unknown" if m.closing_unknown else "rule_cleared"
                self._close(m, tb, self._prev.index, reason, condition_until=m.closing_start, unknown_causes=sorted(m.closing_causes) if m.closing_unknown else None)
                m.mode = "INACTIVE"
            self._flush_interval(tb)
            live = [m for m in self.machines.values() if m.live]
            state = RED if any(m.rule == RULE_R1 for m in live) else YELLOW if live else UNKNOWN
            ids = tuple(sorted(m.ev["event_id"] for m in live))
            self._cur, self._cur_start = (state, ids, False, ("source_gap",)), tb
            self._cur_doc = {"state": state, "event_ids": list(ids), "pending": False, "causes": ["source_gap"], "analyzed": False}

    def finish(self, end_us: int):
        """End of stream: open episodes close with end_of_stream (right-censored); candidates are recorded as suppressed."""
        if self._finished:
            return
        if self._last_t is not None and end_us < self._last_t:
            raise PolicyError("end_us precedes the last sample")
        if self._prev is None and self._cur is None and end_us > 0:
            self._open_unknown_prefix()  # no sample at all: the whole stream is unobserved
        if self._prev is not None:
            if end_us - self._prev.t_us > self.p.max_sample_gap_us:
                self._gap_step()   # no further sample arrived: a trailing silence is UNKNOWN whatever the episode state (H-6; MG-RISK-028 for open episodes, MG-RISK-029 for the rest)
            self._expire_unobserved(end_us, inclusive=True)
        for m in self.machines.values():
            if m.mode == "CAND":
                self._suppress(m, end_us, self._prev.index if self._prev else 0, "stream_ended_before_open_hold")
            elif m.live:
                self._close(m, end_us, self._prev.index if self._prev else 0, "end_of_stream", condition_until=m.closing_start if m.mode == "CLOSING" else None, right_censored=True)
            m.mode = "INACTIVE"
        self._flush_interval(end_us)
        self._finished = True

    def _open_unknown_prefix(self):
        self._cur = (UNKNOWN, (), False, ("frame_not_analyzed",))
        self._cur_start = 0
        self._cur_doc = {"state": UNKNOWN, "event_ids": [], "pending": False, "causes": ["frame_not_analyzed"], "analyzed": False}

    def snapshot(self) -> dict:
        """Current evaluation including episodes still open (end = null) and the interval in progress."""
        open_events = [self._event_doc(m.ev, None, None, open_=True) for m in self.machines.values() if m.live]
        return {"last": self.last_state, "events": self.events + open_events, "suppressed": list(self.suppressed), "dropped_tracks": list(self.dropped_tracks)}

    def result(self) -> dict:
        assert self._finished, "call finish() first"
        total = self.coverage_us["total"]
        return {"zone_id": self.zone_id, "engine_version": ENGINE_VERSION, "semantics_version": SEMANTICS_VERSION, "params": self.p.as_dict(),
                "timeline": self.intervals, "events": self.events, "suppressed_candidates": self.suppressed, "dropped_tracks": self.dropped_tracks,
                "coverage_us": {**self.coverage_us, "partition_check": total == sum(self.coverage_us[k] for k in (GREEN, YELLOW, RED, UNKNOWN))},
                "unknown_by_cause_us": dict(sorted(self._unknown_causes.items()))}

    # ------------------------------------------------------------------ one sample
    def _tol(self, causes: set) -> int:
        return self.p.track_gap_tolerance_us if "track_absent" in causes else self.p.gap_tolerance_us

    def _track_value(self, tid: str, tr: _Track, s: Sample, analyzed: bool, hole: str | None):
        """Returns (value, cause) for this track at this sample, or None if the track no longer contributes."""
        if tr.terminated or tr.dropped:
            return None
        if tid in s.terminated:
            tr.terminated = True
            tr.run_start = tr.u_since = None
            tr.discontinuity = True
            return (F, None)
        if not analyzed:
            return (U, hole)
        if tid in s.persons:
            v, c = s.persons[tid]
            tr.last_seen_t = s.t_us
            return (v, c)
        if tr.last_value == F or tr.last_value is None:
            return (F, None)
        if tr.last_seen_t is not None and s.t_us - tr.last_seen_t >= self.p.track_unknown_hold_us:
            tr.dropped = True
            self.dropped_tracks.append({"track_id": tid, "last_seen_us": tr.last_seen_t, "dropped_at_us": s.t_us, "reason": "hold_expired"})
            return None
        return (U, "track_absent")

    def _dwell(self, tr: _Track, v: str, cause, t: int, first_t: int, dt: int | None) -> str:
        if v == T:
            if tr.run_start is not None and dt is not None and dt > self.p.dwell_us:
                # samples further apart than the dwell itself cannot confirm continuous presence (MG-RISK-002 H-6), whether or not an unknown run is pending: with a pending bridge
                # the identity continuity is not observed either, so the new run is a discontinuity (cycle-17 MG-RISK-017)
                if tr.u_since is not None:
                    tr.discontinuity = True
                tr.run_start, tr.left_censored, tr.u_since, tr.u_causes = t, False, None, set()
            if tr.run_start is None:
                tr.run_start = t
                tr.left_censored = t == first_t
                if tr.lost_run:
                    tr.discontinuity, tr.lost_run = True, False
            elif tr.u_since is not None and t - tr.last_t > self._tol(tr.u_causes):
                tr.run_start, tr.left_censored, tr.discontinuity = t, False, True
            tr.u_since, tr.u_causes, tr.last_t = None, set(), t
            return T if t - tr.run_start >= self.p.dwell_us else F
        if v == F:
            tr.run_start = tr.u_since = None
            tr.u_causes, tr.lost_run = set(), False
            return F
        if tr.run_start is not None:
            if tr.u_since is None:
                tr.u_since = t
            tr.u_causes.add(cause)
            if cause not in BRIDGEABLE or t - tr.last_t > self._tol(tr.u_causes):
                tr.lost_run = cause != "adapter_error"
                tr.run_start, tr.u_since, tr.u_causes = None, None, set()
        return U

    def _step(self, s: Sample, pseudo: bool = False):
        t = s.t_us
        dt = None if self._prev_step_t is None else t - self._prev_step_t
        self._prev_step_t = t
        analyzed = s.status == "analyzed"
        hole = None if analyzed else HOLE_CAUSE[s.status]
        if analyzed and self._first_analyzed_t is None:
            self._first_analyzed_t = t
        first_t = self._first_analyzed_t if self._first_analyzed_t is not None else t
        step_flags: set = set()
        # ---- tracks
        for tid in sorted(set(self.tracks) | set(s.persons) | set(s.terminated)):
            self.tracks.setdefault(tid, _Track())
        items: dict[str, tuple] = {}
        dwell: dict[str, str] = {}
        for tid in sorted(self.tracks):
            tr = self.tracks[tid]
            if tid in s.identity_breaks and not (tr.terminated or tr.dropped):
                tr.run_start = tr.u_since = None
                tr.u_causes = set()
                tr.discontinuity = True
                step_flags.add(("track_discontinuity", tid))
            tv = self._track_value(tid, tr, s, analyzed, hole)
            if tv is None:
                continue
            v, c = tv
            items[tid] = tv
            dwell[tid] = self._dwell(tr, v, c, t, first_t, dt)
            if analyzed and tid in s.persons:
                tr.last_value = v
            if tr.discontinuity:
                step_flags.add(("track_discontinuity", tid))
                tr.discontinuity = False
            if tid in s.terminated:
                step_flags.add(("track_discontinuity", tid))
        # ---- class-level occupancy
        if analyzed:
            person, vehicle = F, s.vehicle[0]
            causes_p = []
            for tid, (v, c) in items.items():
                person = k_or(person, v)
                if v == U:
                    causes_p.append(c)
            for v, c in (s.untracked_person, s.person_other):
                person = k_or(person, v)
                if v == U:
                    causes_p.append(c or "class_unknown_or_ambiguous")
            veh_cause = s.vehicle[1] if vehicle == U else None
        else:
            person, vehicle, causes_p, veh_cause = U, U, [hole], hole
        nv = k_not(vehicle)
        if nv == T:
            if self._nv_start is None or (dt is not None and dt > self.p.debounce_us):  # a hold cannot be confirmed across a longer silence
                self._nv_start = t
        else:
            self._nv_start = None
        hold = U if nv == U else (T if self._nv_start is not None and t - self._nv_start >= self.p.debounce_us else F)
        # ---- instances
        inst: dict[tuple, tuple] = {}  # key -> (open, sustain, causes)
        notes = [c for c in s.notes if c]
        r1_causes = sorted({c for c in causes_p + [veh_cause] + notes if c})
        inst[("R1",)] = (k_and(person, vehicle), k_and(person, vehicle), r1_causes)
        for tid, d in dwell.items():
            cs = []
            if items[tid][0] == U:
                cs.append(items[tid][1])
            if nv == U:
                cs.append(veh_cause)
            inst[("R2", tid)] = (k_and(d, hold), k_and(items[tid][0], nv), sorted({c for c in cs + notes if c}))  # sustain: person still inside AND no vehicle; dwell is not re-earned
        if not analyzed:
            ut, ut_cause = U, hole
        else:  # a person that is untracked (tentative / no id) OR only possibly a person (ambiguous class) cannot satisfy per-track dwell: UNKNOWN, never complete GREEN
            ut = k_or(s.untracked_person[0], s.person_other[0])
            ut_cause = "identity_unavailable" if s.untracked_person[0] in (T, U) else (s.person_other[1] or "class_unknown_or_ambiguous")
        if ut != F:
            inst[("R2", UNTRACKED_KEY)] = (U, U, [ut_cause])
        # ---- incomplete inputs of THIS sample are recorded on every episode live at its start BEFORE any transition, so the unknown sample that closes an episode
        # (a zero close hold closes it at the very first unknown sample) never disappears from its evidence (cycle-5 review MG-RISK-014). The range may then be zero-length
        # ([t, t): the closing sample is the first one outside the half-open episode).
        for m in self.machines.values():
            if m.live:
                self._note_incomplete(m.ev, t, s.index, cs_for(inst, m))
        # ---- machines
        for key in sorted(set(inst) | set(self.machines)):
            m = self.machines.get(key)
            if m is None:
                rule, hold_us = (RULE_R1, self.p.debounce_us) if key[0] == "R1" else (RULE_R2, 0)
                m = self.machines[key] = _Machine(rule, key, hold_us, self.p.debounce_us)
            o, sus, cs = inst.get(key, (F, F, []))
            flags = {tid for (f, tid) in step_flags if len(key) > 1 and tid == key[1]}
            if key not in inst and m.live:
                flags.add("track_dropped")
            self._machine_step(m, t, s.index, o, sus, cs, bool(flags), pseudo, dt)
        # ---- state
        red = any(m.live for m in self.machines.values() if m.rule == RULE_R1)
        yellow = any(m.live for m in self.machines.values() if m.rule == RULE_R2)
        unknown_causes = sorted({c for (o, _, cs) in inst.values() if o == U for c in cs} | ({hole} if hole else set()))
        pending = []
        for key, m in self.machines.items():
            if m.mode == "CAND":
                pending.append({"rule_id": m.rule[0], "key": list(key[1:]), "reason": "open_hold_in_progress"})
        for tid, d in dwell.items():
            tr = self.tracks[tid]
            live = self.machines.get(("R2", tid), _Machine(RULE_R2, 0, 0, 0)).live
            # pending while the dwell is accumulating OR the dwell is satisfied and the vehicle-free hold is accumulating (cycle-17 MG-PENDING-017)
            if items[tid][0] == T and not live and (d == F or (d == T and nv == T and hold == F)):
                pending.append({"rule_id": RULE_R2[0], "key": [tid], "reason": "dwell_or_vehicle_hold_in_progress"})
        state = RED if red else YELLOW if yellow else UNKNOWN if (unknown_causes or not analyzed) else GREEN
        active_ids = tuple(sorted(m.ev["event_id"] for m in self.machines.values() if m.live))
        causes_t = tuple(unknown_causes)
        sig = (state, active_ids, bool(pending), causes_t)
        if self._cur != sig:
            self._flush_interval(t)
            self._cur, self._cur_start = sig, t
            self._cur_doc = {"state": state, "event_ids": list(active_ids), "pending": bool(pending), "causes": list(causes_t), "analyzed": analyzed}
        self._last_t = t
        self.last_state = {"state": state, "pending": pending, "causes": list(causes_t), "t_us": t, "event_ids": list(active_ids), "analyzed": analyzed}
        for m in self.machines.values():
            if m.live:
                self._note_incomplete(m.ev, t, s.index, cs_for(inst, m))

    # ------------------------------------------------------------------ interval bookkeeping
    def _flush_interval(self, end_t: int):
        if self._cur is None:
            return
        if end_t > self._cur_start:
            doc = {"start_us": self._cur_start, "end_us": end_t, **self._cur_doc}
            if self.intervals and {k: v for k, v in self.intervals[-1].items() if k not in ("start_us", "end_us")} == {k: v for k, v in doc.items() if k not in ("start_us", "end_us")} and self.intervals[-1]["end_us"] == doc["start_us"]:
                self.intervals[-1]["end_us"] = end_t
            else:
                self.intervals.append(doc)
            d = end_t - self._cur_start
            self.coverage_us["total"] += d
            self.coverage_us[doc["state"]] += d
            if doc["analyzed"]:
                self.coverage_us["analyzed"] += d
            if doc["state"] == UNKNOWN:
                for c in doc["causes"] or ["no_cause_recorded"]:
                    self._unknown_causes[c] = self._unknown_causes.get(c, 0) + d
        self._cur = None

    # ------------------------------------------------------------------ episode machine (MG-RISK-002 section 5)
    def _note_incomplete(self, ev: dict, t: int, idx: int, causes: list):
        """Incomplete-input ranges (MG-RISK-002 step 7): one range per CONTIGUOUS run of samples that carry a cause. A range is half-open in time
        [from_us, to_us): it ends at the time of the first sample WITHOUT the cause (or at the episode end); from_index/to_index are the first and last
        sample index that carried it. A cause that disappears and returns opens a NEW range (cycle-4 review MG-RISK-013)."""
        opened = ev.setdefault("_open", {})
        for c in list(opened):
            if c not in causes:
                opened.pop(c)["to_us"] = t
        for c in causes:
            if c in opened:
                opened[c]["to_index"] = idx
            else:
                r = {"cause": c, "from_us": t, "to_us": None, "from_index": idx, "to_index": idx}
                opened[c] = r
                ev["incomplete"].append(r)

    def _close_incomplete(self, ev: dict, t: int):
        for r in ev.pop("_open", {}).values():
            r["to_us"] = t

    def _machine_step(self, m: _Machine, t: int, idx: int, o: str, sus: str, cs: list, discontinuity: bool, pseudo: bool, dt: int | None = None):
        if m.mode == "INACTIVE" and o == T:
            if m.open_hold == 0:
                self._open(m, t, idx, t, idx)
            else:
                m.mode, m.cand_start, m.cand_idx, m.cand_last = "CAND", t, idx, (t, idx)
        elif m.mode == "CAND":
            if o == T and dt is not None and dt > m.open_hold:
                # consecutive samples further apart than the confirmation hold: the hold was NOT observed (a source slower than the hold cannot confirm it)
                self._suppress(m, t, idx, "sample_spacing_exceeds_hold", terminal=o)
                m.cand_start, m.cand_idx, m.cand_last = t, idx, (t, idx)
            elif o == T:  # opening requires CONTINUOUS determinate T: any F or U drops the candidate (no absorption before confirmation)
                m.cand_last = (t, idx)
                if t - m.cand_start >= m.open_hold:
                    self._open(m, t, idx, m.cand_start, m.cand_idx)
            else:
                self._suppress(m, t, idx, "below_open_hold", terminal=o)
                m.mode = "INACTIVE"
        if discontinuity and m.live:
            sus = F  # identity continuity is not vouched for: the old identity's episode cannot continue
        if m.mode == "ACTIVE" and m.ev["start_us"] != t and sus != T:
            m.mode, m.closing_start, m.closing_idx = "CLOSING", t, idx
            m.closing_vals, m.closing_causes, m.closing_unknown, m.closing_disc = [], set(), False, False
        if m.mode == "CLOSING":
            expired = t - m.closing_start >= m.close_hold
            if sus == T and m.closing_start != t and not m.closing_disc and not expired:  # absorption only BEFORE the hold boundary (MG-RISK-002 step 6)
                m.ev["absorbed"].append({"from_us": m.closing_start, "to_us": t, "values": sorted(set(m.closing_vals)),
                                         "from_index": m.closing_idx, "to_index": idx})
                m.mode = "ACTIVE"
                return
            if sus != T or m.closing_start == t:
                m.closing_vals.append(sus)
                m.closing_causes |= set(cs)
                m.closing_unknown |= sus == U
            m.closing_disc |= discontinuity
            if expired:
                reason = "track_discontinuity" if m.closing_disc else "inputs_unknown" if m.closing_unknown else "rule_cleared"
                self._close(m, t, idx, reason, condition_until=m.closing_start, unknown_causes=sorted(m.closing_causes) if m.closing_unknown else None)
                m.mode = "INACTIVE"
                if o == T:  # the condition is true again at the very sample that closed the episode: a NEW candidate starts here, never a continuation
                    if m.open_hold == 0:
                        self._open(m, t, idx, t, idx)
                    else:
                        m.mode, m.cand_start, m.cand_idx, m.cand_last = "CAND", t, idx, (t, idx)

    def _open(self, m: _Machine, t: int, idx: int, cond_since: int, cond_idx: int):
        m.mode = "ACTIVE"
        r = self.tracks.get(m.key[1]) if len(m.key) > 1 else None
        left = bool(r and r.left_censored and r.run_start == self._first_analyzed_t)
        ev = {"event_id": "evt_" + _hash16(["mg.event.id.v2", *self.id_context, self.zone_id, m.rule[0], m.rule[1], self.epoch, list(m.key[1:]), idx]),
              "zone_id": self.zone_id, "rule_id": m.rule[0], "rule_version": m.rule[1], "severity": RED if m.rule == RULE_R1 else YELLOW,
              "key": list(m.key[1:]), "condition_since_us": cond_since, "start_us": t, "start_index": idx, "condition_since_index": cond_idx,
              "dwell_since_us": r.run_start if r else None, "left_censored": left, "right_censored": False,
              "incomplete": [], "absorbed": [], "tracks": [], "closed_reason": None}
        if len(m.key) > 1:
            ev["tracks"] = [m.key[1]]
        m.ev = ev

    def _suppress(self, m: _Machine, t: int, idx: int, reason: str, terminal=None):
        last_t, last_idx = m.cand_last or (m.cand_start, m.cand_idx)
        self.suppressed.append({"rule_id": m.rule[0], "key": list(m.key[1:]), "from_us": m.cand_start, "to_us": t, "from_index": m.cand_idx,
                                "last_true_index": last_idx, "reason": reason, "terminal_value": terminal})

    def _close(self, m: _Machine, t: int, idx: int, reason: str, condition_until=None, unknown_causes=None, right_censored=False):
        ev = m.ev
        self._close_incomplete(ev, t)
        ev["closed_reason"], ev["end_us"], ev["end_index"] = reason, t, idx
        ev["condition_until_us"] = condition_until
        ev["right_censored"] = right_censored
        if unknown_causes:
            ev["closed_unknown_reasons"] = unknown_causes
        self.events.append(ev)
        m.ev = None

    def _event_doc(self, ev: dict, end, reason, open_: bool) -> dict:
        d = {k: v for k, v in ev.items() if k != "_open"}
        d["end_us"], d["open"] = end, open_
        return d


def cs_for(inst: dict, m: _Machine) -> list:
    """Causes of unknown values currently affecting the episode's instance (incomplete inputs stay visible, MG-RISK-002 section 6)."""
    key = m.key
    out = list(inst.get(key, (None, None, []))[2]) if key in inst else []
    for k2, (o, _, cs) in inst.items():
        if k2 != key and o == U:
            out += cs
    return sorted(set(out))


def evaluate(zone_id: str, samples: list[Sample], end_us: int, params: Params | None = None) -> dict:
    """Offline evaluation of a complete sample list. Identical to feeding the same samples to push() one by one (conformance requirement)."""
    e = ZoneEngine(zone_id, params)
    for s in samples:
        e.push(s)
    e.finish(end_us)
    return e.result()
