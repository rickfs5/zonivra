"""MineGuard IoU tracker v0 (tracking adapter). Original, dependency-free, deterministic: greedy same-class association by integer IoU.
It is NOT ByteTrack and makes no appearance-based identity claim: a track id is an IoU TRAJECTORY (boxes that keep overlapping across consecutive samples), not a verified person. Two
different people whose boxes overlap in consecutive samples WITHOUT any ambiguity signal (one replaces the other in the same place) are indistinguishable to it; when an association
is ambiguous (a detection overlaps several tracks, or a track overlaps several detections of its class) every track involved is ended and the detections start new tentative tracks.
A track is TENTATIVE until `confirm_hits` consecutive-enough hits; tentative detections are reported with track_ref = None (the risk kernel then
treats a person as untracked => UNKNOWN identity_unavailable, never YELLOW). A track unseen for `max_age_us` ends and its id is never reused."""
from __future__ import annotations

from dataclasses import dataclass

TRACKER_ID = "mg-iou-tracker"
TRACKER_VERSION = "0.3.0"   # 0.2.0: ambiguous associations end the tracks involved (no identity transfer through crossings or merges); 0.3.0: those endings are REPORTED per update (`breaks`) so that the kernel receives them as identity breaks


@dataclass(frozen=True)
class TrackerParams:
    iou_min_permille: int = 300
    confirm_hits: int = 2
    max_age_us: int = 1_000_000

    def as_dict(self) -> dict:
        return dict(self.__dict__)


def iou_permille(a: dict, b: dict) -> int:
    ax2, ay2, bx2, by2 = a["x"] + a["w"], a["y"] + a["h"], b["x"] + b["w"], b["y"] + b["h"]
    iw, ih = min(ax2, bx2) - max(a["x"], b["x"]), min(ay2, by2) - max(a["y"], b["y"])
    if iw <= 0 or ih <= 0:
        return 0
    inter = iw * ih
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter * 1000 // union


class _T:
    def __init__(self, tid, label, bbox, t):
        self.tid, self.label, self.bbox, self.last_t, self.first_t, self.hits = tid, label, bbox, t, t, 1


class IouTracker:
    def __init__(self, params: TrackerParams | None = None):
        self.p = params or TrackerParams()
        self.live: list[_T] = []
        self.n = 0
        self.ended: list[dict] = []
        self.breaks: list[str] = []   # confirmed-or-not track ids ended by an AMBIGUOUS association in the LAST update: an explicit identity discontinuity for the risk kernel
        self.summary: dict[str, dict] = {}

    def checkpoint(self) -> dict:
        """State captured before an update so that a failed multi-part commit can be undone exactly (cycle-24 MG-FEED-024)."""
        import copy
        return {"live": copy.deepcopy(self.live), "summary": copy.deepcopy(self.summary), "n": self.n, "breaks": list(self.breaks), "ended": len(self.ended)}

    def rollback(self, cp: dict):
        self.live, self.summary, self.n, self.breaks = cp["live"], cp["summary"], cp["n"], cp["breaks"]
        del self.ended[cp["ended"]:]

    def update(self, t_us: int, dets: list[tuple[str, dict]]) -> list[str | None]:
        """dets: [(label, bbox)] in a fixed order. Returns, per detection, a confirmed track id or None (tentative / not tracked). `self.breaks` lists the tracks this update ended by ambiguity."""
        self.breaks = []
        for tr in [x for x in self.live if t_us - x.last_t > self.p.max_age_us]:
            self.live.remove(tr)
            self.ended.append({"track_id": tr.tid, "last_seen_us": tr.last_t})
        pairs = []
        for di, (label, box) in enumerate(dets):
            for tr in self.live:
                if tr.label == label:
                    v = iou_permille(tr.bbox, box)
                    if v >= self.p.iou_min_permille:
                        pairs.append((-v, int(tr.tid[1:]), di, tr))
        # ambiguity: a detection with several candidate tracks, or a track with several candidate detections (crossing, merging, splitting): identity cannot be carried over
        cand_t: dict[str, set] = {}
        cand_d: dict[int, set] = {}
        for _, _, di, tr in pairs:
            cand_t.setdefault(tr.tid, set()).add(di)
            cand_d.setdefault(di, set()).add(tr.tid)
        amb_t = {tid for tid, ds in cand_t.items() if len(ds) > 1} | {tid for tid in cand_t if any(len(cand_d[d]) > 1 for d in cand_t[tid])}
        for tr in [x for x in self.live if x.tid in amb_t]:
            self.live.remove(tr)
            self.ended.append({"track_id": tr.tid, "last_seen_us": tr.last_t, "reason": "ambiguous_association"})
            self.breaks.append(tr.tid)
        pairs = [p for p in pairs if p[3].tid not in amb_t]
        pairs.sort(key=lambda x: x[:3])
        used_t, used_d, out = set(), set(), [None] * len(dets)
        for _, _, di, tr in pairs:
            if tr.tid in used_t or di in used_d:
                continue
            used_t.add(tr.tid)
            used_d.add(di)
            tr.bbox, tr.last_t, tr.hits = dets[di][1], t_us, tr.hits + 1
            out[di] = tr
        for di, (label, box) in enumerate(dets):
            if di not in used_d:
                self.n += 1
                out[di] = _T(f"T{self.n}", label, box, t_us)
                self.live.append(out[di])
        res = []
        for tr in out:
            if tr.hits >= self.p.confirm_hits:
                s = self.summary.setdefault(tr.tid, {"track_id": tr.tid, "label": tr.label, "first_us": tr.first_t, "last_us": tr.last_t, "hits": 0})
                s["last_us"], s["hits"] = tr.last_t, tr.hits
                res.append(tr.tid)
            else:
                res.append(None)
        return res

    def tracks(self) -> list[dict]:
        return [self.summary[k] for k in sorted(self.summary, key=lambda x: int(x[1:]))]
