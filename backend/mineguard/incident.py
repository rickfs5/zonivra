"""MG-PRODUCT-VERTICAL-06: the INCIDENT record. A pure projection of facts the application already stores (one alert, optionally its source feed and the stored run it came from).
Nothing here is measured, inferred or invented: a fact that is not stored is reported as unavailable, with a reason, and never filled in.

Two time bases are kept apart: OBSERVATION time (microseconds of the source's own timeline) and PROCESSING time (UTC wall clock of this application). An unobserved interval is UNKNOWN, never GREEN.
The record is the same object the screen shows and the JSON export contains. It holds no image and no footage; a local digest is a consistency aid and never proof of authenticity."""
from __future__ import annotations

SCHEMA = "mg.incident/1"
MAX_UNKNOWN = 50
# what this record can NOT contain WITHOUT a recorded source lifecycle, said once so no screen has to guess (V07: a run recorded by this application carries them; older and scripted records do not)
UNSUPPORTED_TIMELINE = ["source_started", "source_stopped", "source_paused", "source_resumed", "source_failed", "observation_received", "person_detected", "vehicle_detected", "risk_changed"]
LIMITATIONS = ["mg.lim.image_space_only", "mg.lim.not_safety_system", "mg.inc.lim.no_distance", "mg.inc.lim.local_digest_only"]


def _intervals(evaluation: dict | None, zone_id: str) -> list[dict] | None:
    """UNKNOWN intervals of ONE zone from a stored run evaluation (observation time). None: no evaluation to read them from."""
    if not evaluation:
        return None
    z = (evaluation.get("zones") or {}).get(zone_id)
    if not isinstance(z, dict):
        return None
    out = [{"start_us": i["start_us"], "end_us": i["end_us"], "cause": i.get("cause")} for i in z.get("timeline", []) if i.get("state") == "UNKNOWN"]
    return out


_OPENING = {"source_started", "source_resumed"}
_CLOSING = {"source_paused", "source_stopped", "source_ended", "source_failed"}
_CAUSAL = {"alert_created": 1, "provenance_changed": 2, "alert_acknowledged": 4, "alert_resolved": 5}      # delivery_* = 3 (after creation, before the operator acts)


def _ukey(u: str) -> str:
    """Orders UTC stamps that do not share a resolution (alerts: seconds, lifecycle: milliseconds) by their instant, not by their text."""
    return u[:19] + (u[19:].rstrip("Z") or ".000").ljust(4, "0")


def _pkey(e: dict) -> tuple:
    """Processing-track order. Alert stamps have SECOND resolution, lifecycle stamps MILLISECOND resolution: inside one second the position of an alert fact is not known, so the order there is the causal
    one (a source opens before its alert exists; it closes after) and `time_resolution` says so. Across seconds the order is exact."""
    u, k = e["at_utc"], e["kind"]
    cat = 0 if k in _OPENING else (6 if k in _CLOSING else _CAUSAL.get(k, 3))
    return (u[:19], cat, u[19:].rstrip("Z").ljust(4, "0"), e.get("seq", 10**9), k)


def _life_tracks(lifecycle: dict | None, zone: str, flags: dict) -> tuple[list[dict], list[dict]]:
    """(observation-track, processing-track) entries from a RECORDED lifecycle. Source lifecycle facts are application (processing) time; presence edges and risk changes carry the source time of the
    sample the application analysed. Nothing is produced between two samples."""
    obs, proc = [], []
    for e in (lifecycle or {}).get("events", []):
        if e["group"] == "lifecycle":
            proc.append({"kind": e["kind"], "at_utc": e["at_utc"], "source_time_us": e.get("t_us"), "reason": e.get("reason"), "error": e.get("error"), "recorded": True, "seq": e["seq"], **flags})
        elif e["kind"] == "risk_changed":
            if e.get("zone_id") == zone:
                obs.append({"kind": "risk_changed", "at_us": e["t_us"], "at_utc": e["at_utc"], "from": e.get("from"), "to": e.get("to"), "zone_id": zone, "recorded": True, "seq": e["seq"], **flags})
        else:
            obs.append({"kind": e["kind"], "at_us": e["t_us"], "at_utc": e["at_utc"], "count": e.get("count"), "scope": e.get("scope"), "recorded": True, "seq": e["seq"], **flags})
    return obs, proc


def evidence_class(alert: dict) -> str:
    """restored | synthetic (scripted drill only) | repeated_source_simulation (REAL model inference over a replayed file that another feed also played) | live_feed_metadata.
    A restored record is history, never live; a scripted one is never real evidence; a repeated-source one is never called scripted (MG-INCIDENT-036)."""
    if alert.get("loaded_from_store"):
        return "restored"
    prov = alert["source"].get("provenance")
    if prov == "synthetic_scripted":
        return "synthetic"
    if prov == "repeated_source_simulation":
        return "repeated_source_simulation"
    return "live_feed_metadata"


def event_matches_run(alert: dict, evaluation: dict | None) -> bool:
    """MG-INCIDENT-037: the alert's own event must be the SAME episode the coherent stored evaluation holds (same id, rule and start), and the alert's top-level event id must equal its nested one."""
    ev = alert.get("event")
    if ev is None or alert.get("event_id") != ev.get("event_id") or not evaluation:
        return False
    z = (evaluation.get("zones") or {}).get(alert["zone_id"]) or {}
    return any(e.get("event_id") == ev["event_id"] and e.get("rule_id") == ev.get("rule_id") and e.get("start_us") == ev["start_us"] for e in z.get("events", []))


def build(alert: dict, *, feed: dict | None, run: dict | None, provider: dict, versions: dict, generated_utc: str, lifecycle: dict | None = None) -> dict:
    """`alert` is `AlertEngine.public(...)`. `feed` is the source feed's `view()` (None: not running here / gone). `run` is {"run_id","evaluation","consistent"} for a simulation run, or None.
    `lifecycle` is the RECORDED lifecycle of the exact run the alert belongs to ({"run_uid","events","truncated"}) or None: not recorded (scripted run, restored record, older run, other process). `provider` is {"kind","label"}; `versions` carries the risk semantics/engine versions. The output is JSON-safe and deterministic for equal inputs."""
    src, ev = alert["source"], alert.get("event")
    zone = alert["zone_id"]
    synthetic = bool(alert.get("simulation"))
    restored = bool(alert.get("loaded_from_store"))
    unknown = _intervals(run["evaluation"], zone) if run and run.get("consistent") else None
    obs_track: list[dict] = []
    proc_track: list[dict] = []
    flags = {"synthetic": synthetic, "restored": restored}
    if ev:
        obs_track.append({"kind": "risk_entry", "at_us": ev["start_us"], "severity": alert["severity"], "rule_id": ev.get("rule_id"), "event_id": ev.get("event_id"), **flags})
    if unknown:
        for u in unknown[:MAX_UNKNOWN]:
            obs_track.append({"kind": "unknown_interval", "at_us": u["start_us"], "end_us": u["end_us"], "cause": u["cause"], **flags})
    if ev and not ev.get("open") and ev.get("end_us") is not None:
        obs_track.append({"kind": "episode_closed", "at_us": ev["end_us"], "closed_reason": ev.get("closed_reason"), "right_censored": bool(ev.get("right_censored")), **flags})
    has_life = bool(lifecycle and lifecycle.get("events"))                              # an empty record is "nothing recorded", never a recorded lifecycle
    lo, lp = _life_tracks(lifecycle, zone, flags)
    obs_track += lo
    proc_track += lp
    obs_track.sort(key=lambda e: (e["at_us"], e["kind"], e.get("seq", -1)))
    proc_track.append({"kind": "alert_created", "at_utc": alert["created_utc"], **flags})
    phase = alert.get("overlap_phase")
    if phase and phase != "alone":
        proc_track.append({"kind": "provenance_changed", "at_utc": alert["created_utc"], "overlap_phase": phase, **flags})
    for ch in ("in_app", "browser", "sound"):
        d = alert["delivery"][ch]
        if d.get("at"):
            proc_track.append({"kind": "delivery_" + str(d["status"]), "channel": ch, "at_utc": d["at"], **flags})
    if alert.get("acknowledged_utc"):
        proc_track.append({"kind": "alert_acknowledged", "at_utc": alert["acknowledged_utc"], **flags})
    if alert.get("resolved_utc"):
        proc_track.append({"kind": "alert_resolved", "at_utc": alert["resolved_utc"], "by": alert.get("resolved_by"), **flags})
    proc_track.sort(key=_pkey)
    conflicts: list[str] = []
    if run is not None and not run.get("consistent"):
        conflicts.append("mg.inc.conflict.run_unavailable_or_inconsistent")
    if src["kind"] == "simulation" and run is None:
        conflicts.append("mg.inc.conflict.run_not_found")
    ecls = evidence_class(alert)
    local = "pass" if run and run.get("consistent") else ("fail" if run is not None else "not_checked")
    if run is not None and run.get("consistent") and not event_matches_run(alert, run["evaluation"]):
        local = "fail"
        conflicts.append("mg.inc.conflict.event_not_in_run")                        # the alert and the stored evaluation disagree about the episode: never reported as consistent
    if alert.get("event") is not None and alert.get("event_id") != alert["event"].get("event_id"):
        conflicts.append("mg.inc.conflict.alert_event_mismatch")
    feed_ctx = None
    if feed is not None:
        feed_ctx = {"feed_id": feed["feed_id"], "state": feed["state"], "observation_fresh": bool(feed["observation"]["fresh"]), "last_observation_age_ms": feed["observation"]["age_ms"],
                    "shared_now": bool(feed["shared_now"]), "launch_repeated": feed["launch_repeated"], "shared_during_run": bool(feed["shared_during_run"]),
                    "current_risk": feed["risk_state"], "unknown_reason": feed["unknown_reason"]}
    inc = {
        "schema": SCHEMA, "incident_id": alert["alert_id"], "generated_utc": generated_utc, "classification": ecls,
        "identity": {"alert_id": alert["alert_id"], "event_id": alert.get("event_id"), "zone_id": zone, "source_id": src["source_id"], "source_kind": src["kind"], "source_label": src["label"],
                     "feed_id": alert.get("feed_id"), "run_id": run["run_id"] if run else None, "physical_camera": False},
        "provenance": {"provenance": src["provenance"], "simulation": synthetic, "restored_from_disk": restored, "repeated_source": bool(src.get("repeated_source")),
                       "overlap_phase": phase, "provider": provider, "feed": feed_ctx},
        "risk": {"severity": alert["severity"], "rule_id": ev.get("rule_id") if ev else None, "causes": list(alert["causes"]),
                 "semantics_version": None if restored else versions["semantics_version"], "engine_version": None if restored else versions["engine_version"],
                 "versions_basis": "unavailable_for_restored_record" if restored else "running_build_at_projection", "decided_by": "deterministic_policy", "ai_observations_decide_severity": False},
        "observed": {"start_us": ev["start_us"] if ev else None, "end_us": ev.get("end_us") if ev else None, "open": bool(ev.get("open")) if ev else None,
                     "closed_reason": ev.get("closed_reason") if ev else None, "right_censored": bool(ev.get("right_censored")) if ev else None, "time_basis": "source_time_us"},
        "unknown_intervals": {"available": unknown is not None, "intervals": (unknown or [])[:MAX_UNKNOWN], "truncated": bool(unknown and len(unknown) > MAX_UNKNOWN),
                              "reason": None if unknown is not None else "mg.inc.unknown.no_stored_evaluation"},
        "alert": {"status": alert["status"], "created_utc": alert["created_utc"], "acknowledged_utc": alert.get("acknowledged_utc"), "resolved_utc": alert.get("resolved_utc"),
                  "resolved_by": alert.get("resolved_by"), "merged": alert["suppressed_count"], "reopened_count": alert.get("reopened_count", 0)},
        "delivery": {"state": alert["delivery_state"], "channels": {c: {"status": alert["delivery"][c]["status"], "at": alert["delivery"][c].get("at"), "detail": alert["delivery"][c].get("detail")} for c in ("in_app", "browser", "sound")},
                     "note": "acknowledge and resolve are operator actions; they are not delivery"},
        "evidence": {"availability": "metadata_only", "images_included": False, "stored_run": run["run_id"] if run else None, "digest_sha256": (run or {}).get("digest"),
                     "local_consistency": local, "restored": restored, "synthetic": alert["source"].get("provenance") == "synthetic_scripted", "authenticity": "not_independently_verified", "conflicts": conflicts,
                     "reason": "mg.inc.evidence.metadata_only"},
        "timeline": {"observation": obs_track, "processing": proc_track, "unsupported_kinds": [] if has_life else UNSUPPORTED_TIMELINE,
                     "lifecycle": {"recorded": has_life, "run_uid": (lifecycle or {}).get("run_uid") if has_life else None, "truncated": bool((lifecycle or {}).get("truncated")) and has_life,
                                   "reason": None if has_life else "mg.inc.life.not_recorded"},
                     "time_resolution": {"alert": "second", "lifecycle": "millisecond", "order_within_a_second": "causal_not_measured"}},
        "completeness": {"source_lifecycle": "recorded" if has_life else "not_recorded", "observation_edges": "recorded" if has_life else "not_recorded",
                         "unknown_intervals": "available" if unknown is not None else "not_available", "stored_run": "consistent_locally" if local == "pass" else ("inconsistent" if local == "fail" else "not_applicable"),
                         "images": "excluded_by_design", "complete": has_life and not bool(lifecycle.get("truncated"))},
        "limitations": LIMITATIONS,
    }
    return inc
