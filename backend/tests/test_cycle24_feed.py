"""Cycle-24 remediation vectors for MG-FEED-024 (OAF-VERTICAL-01): a sample is consumed by the tracker and EVERY zone engine of a feed, or by none; every offered frame has exactly one outcome.
Synthetic only. Each vector asserts the CORRECT behaviour; they failed on 147f139."""
import copy
import random

import pytest

from mineguard import feeds as feeds_mod
from mineguard.risk import kernel
from mineguard.risk.kernel import Sample, ZoneEngine
from mineguard.risk.tracker import IouTracker
from test_vertical2_feeds import PERSON_IN, ZONE, act, always, create, fm, wait_state  # noqa: F401


def snap(o, depth=0):
    """Structural, identity-free picture of an object graph (the kernel's helper classes define no __eq__)."""
    if depth > 12:
        return "<deep>"
    if isinstance(o, dict):
        return {k: snap(v, depth + 1) for k, v in sorted(o.items(), key=lambda kv: str(kv[0]))}
    if isinstance(o, (list, tuple)):
        return [snap(v, depth + 1) for v in o]
    if isinstance(o, (set, frozenset)):
        return sorted(snap(v, depth + 1) for v in o) if all(isinstance(v, (str, int)) for v in o) else [snap(v, depth + 1) for v in o]
    if hasattr(o, "__dict__") and not isinstance(o, type):
        return {"__cls__": type(o).__name__, **{k: snap(v, depth + 1) for k, v in sorted(vars(o).items())}}
    return o


def rnd_sample(rng, t, i):
    st = rng.choice(["analyzed"] * 6 + ["not_analyzed", "adapter_error"])
    persons = {f"T{k}": (rng.choice([kernel.T, kernel.T, kernel.F, kernel.U]), None) for k in range(rng.randint(0, 3))}
    persons = {k: (v, ("track_absent" if v == kernel.U else None)) for k, (v, _) in persons.items()}
    return Sample(t_us=t, index=i, status=st, persons=persons if st == "analyzed" else {}, vehicle=(rng.choice([kernel.F, kernel.F, kernel.T, kernel.U]), None) if st == "analyzed" else (kernel.F, None),
                  terminated=frozenset({"T0"}) if rng.random() < 0.05 else frozenset(), identity_breaks=frozenset({"T1"}) if rng.random() < 0.05 else frozenset())


@pytest.mark.parametrize("seed", range(12))
def test_zone_engine_rollback_restores_the_exact_pre_sample_state(seed):
    rng = random.Random(seed)
    e = ZoneEngine("Z1", id_context=("in", "zd"))
    t = 0
    for i in range(80):
        t += rng.choice([100_000, 100_000, 100_000, 2_000_000])
        s = rnd_sample(rng, t, i)
        before = snap(e.__dict__)
        cp = e.checkpoint()
        e.push(s)
        e.rollback(cp)
        assert snap(e.__dict__) == before, (seed, i)
        e.push(s)                                              # and the rolled-back engine accepts the very same sample afterwards


def test_a_rolled_back_engine_produces_the_same_result_as_one_that_never_failed():
    rng = random.Random(99)
    a, b = ZoneEngine("Z1", id_context=("in", "zd")), ZoneEngine("Z1", id_context=("in", "zd"))
    t = 0
    for i in range(120):
        t += 100_000
        s = rnd_sample(rng, t, i)
        a.push(s)
        cp = b.checkpoint()
        b.push(s)
        if i % 3 == 0:
            b.rollback(cp)
            b.push(s)
    a.finish(t + 100_000)
    b.finish(t + 100_000)
    assert snap(a.result()) == snap(b.result())


@pytest.mark.parametrize("seed", range(8))
def test_tracker_rollback_restores_the_exact_state(seed):
    rng = random.Random(seed)
    tr = IouTracker()
    t = 0
    for i in range(60):
        t += 200_000
        dets = [("person", {"x": rng.randint(0, 400), "y": rng.randint(0, 400), "w": 80, "h": 160}) for _ in range(rng.randint(0, 3))]
        before = snap(tr.__dict__)
        cp = tr.checkpoint()
        tr.update(t, dets)
        tr.rollback(cp)
        assert snap(tr.__dict__) == before
        tr.update(t, dets)


Z2 = {"zone_id": "Z2", "kind": "restricted", "polygon": [[10, 10], [300, 10], [300, 300], [10, 300]], "name": "Dos"}


def accounted(c):
    return c["analyzed"] + c["dropped_queue_overflow"] + c["dropped_stale"] + c["adapter_error"] + c["dropped_stopped"]


def test_failure_after_the_first_of_two_zone_pushes_leaves_identical_zone_histories_and_exact_accounting(fm, monkeypatch):
    env = fm({1: always(person=PERSON_IN)}, frames=150, delay={1: 0.05})                  # a slow detector keeps frames queued when the failure happens
    a = create(env, 1, zones=[ZONE, Z2], mode="throttled")
    real = ZoneEngine.push
    n = {"c": 0}

    def flaky(self, s):
        if self.zone_id == "Z2":
            n["c"] += 1
            if n["c"] == 4:
                raise ValueError("injected after Z1 already consumed this sample")
        return real(self, s)
    monkeypatch.setattr(ZoneEngine, "push", flaky)
    act(env, a, "start")
    (v,) = wait_state(env, [a], ("failed", "ended"))
    c = v["counters"]
    f = env.feeds.feeds[a]
    z1, z2 = f.engines["Z1"], f.engines["Z2"]
    assert v["state"] == "failed" and v["closed_reason"] == "internal_error" and v["error"] == "ValueError"
    assert c["offered"] == accounted(c), c                                               # every offered frame has exactly one outcome (queued frames are not counted twice)
    assert z1._prev.t_us == z2._prev.t_us                                                # both zones hold the SAME sample history: the failing sample reached neither
    assert z1._finished and z2._finished
    assert z1.coverage_us["total"] == z2.coverage_us["total"] and z1.intervals[-1]["end_us"] == z2.intervals[-1]["end_us"]


def test_failure_in_the_tracker_leaves_zone_engines_untouched(fm, monkeypatch):
    env = fm({1: always(person=PERSON_IN)}, frames=120)
    a = create(env, 1, zones=[ZONE, Z2])
    real = IouTracker.update
    n = {"c": 0}

    def flaky(self, t, dets):
        n["c"] += 1
        out = real(self, t, dets)
        if n["c"] == 3:
            raise ValueError("injected after the tracker mutated itself")
        return out
    monkeypatch.setattr(IouTracker, "update", flaky)
    act(env, a, "start")
    (v,) = wait_state(env, [a], ("failed", "ended"))
    f = env.feeds.feeds[a]
    assert v["state"] == "failed" and v["counters"]["offered"] == accounted(v["counters"])
    assert f.engines["Z1"]._prev.t_us == f.engines["Z2"]._prev.t_us
    assert len(f.tracker.summary) == 0 or all(s["last_us"] <= f.last_t_us for s in f.tracker.summary.values())   # the failing sample left no trace in the tracker


def test_unexpected_error_before_settlement_with_queued_frames_counts_each_frame_once(fm, monkeypatch):
    env = fm({1: always(person=PERSON_IN)}, frames=150, delay={1: 0.05})
    a = create(env, 1, mode="throttled")
    real = feeds_mod.Feed.process

    def boom(self, item, det):
        if self.counters["analyzed"] >= 3:
            raise RuntimeError("injected before settlement")
        return real(self, item, det)
    monkeypatch.setattr(feeds_mod.Feed, "process", boom)
    act(env, a, "start")
    (v,) = wait_state(env, [a], ("failed", "ended"))
    c = v["counters"]
    assert v["state"] == "failed" and c["offered"] == accounted(c), c


def test_a_failed_consumption_reclassifies_its_frame_instead_of_leaving_it_counted_as_analyzed(fm, monkeypatch):
    env = fm({1: always(person=PERSON_IN)}, frames=120)
    a = create(env, 1, zones=[ZONE, Z2])
    real = ZoneEngine.push
    n = {"c": 0}

    def flaky(self, s):
        if self.zone_id == "Z2":
            n["c"] += 1
            if n["c"] == 3:
                raise ValueError("x")
        return real(self, s)
    monkeypatch.setattr(ZoneEngine, "push", flaky)
    act(env, a, "start")
    (v,) = wait_state(env, [a], ("failed", "ended"))
    f = env.feeds.feeds[a]
    consumed = len([1 for _ in f.engines["Z1"].intervals])   # the engines saw exactly the samples that were really consumed
    assert v["counters"]["analyzed"] <= 2 and v["counters"]["offered"] == accounted(v["counters"]) and consumed >= 1
