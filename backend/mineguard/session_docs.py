"""Single source of truth for the documents of a stored webcam session. `webcam.Session._finalize` BUILDS them with these functions and
`session_verify` REBUILDS them from the primary data (observations, sample log, evidence) and compares, so a coordinated edit of provenance or
manifest (even together with export.json) cannot be verified."""
from __future__ import annotations

import re

from .provenance import runtime_provenance

CAPTURE_ORIGINS = ("getusermedia", "test_source")
CLOSE_REASONS = ("client_stop", "device_ended", "client_disconnected")
LIMITATIONS = ["mg.lim.image_space_only", "mg.lim.uncalibrated_confidence", "mg.lim.coco_no_excavator_class", "mg.lim.not_safety_system",
               "mg.lim.capture_origin_client_declared"]
DECODER_ID = "browser-jpeg-pil"
STATS_KEYS = ("latency_ms", "queue_depth_max_observed", "queue_max", "span_ms", "analyzed_millifps", "capture_interval_ms", "intake_rejected")   # sessions stored before the accounting existed
STATS_KEYS_V2 = STATS_KEYS + ("accounting",)
ACCOUNTING_INTS = ("submitted", "accepted", "analyzed", "dropped", "adapter_error", "never_observed_frames", "prefix_frames", "gap_frames", "tail_frames", "unknown_intervals")
ACCOUNTING_INTS_V2 = ACCOUNTING_INTS + ("rejected_invalid_frames", "invalid_requests")   # sessions stored by the previous build lack these two (they could not tell a refused frame from a lost one)
ACCOUNTING_INTS_V3 = ACCOUNTING_INTS_V2 + ("unclassified_frames",)   # cycle-24 MG-WCAM-024: examined requests the bounded ledger could not record (explicit, never relabelled as never observed)
ACCOUNTING_KEYS = ACCOUNTING_INTS + ("captured_declared", "origin_basis", "tail_basis", "tail_exact", "closure")
ACCOUNTING_KEYS_V2 = ACCOUNTING_INTS_V2 + ("captured_declared", "origin_basis", "tail_basis", "tail_exact", "closure")
ACCOUNTING_KEYS_V3 = ACCOUNTING_INTS_V3 + ("captured_declared", "origin_basis", "tail_basis", "tail_exact", "closure")
TAIL_BASES = ("declared_by_stop", "declared_by_heartbeat", "none_right_censored")


def counters_from_samples(samples: list[dict]) -> dict:
    """Derivable from the sample log alone (intake refusals are not samples and live in live_stats). A `client_loss` stub is not a received frame: it records frames that never arrived."""
    real = [s for s in samples if s.get("reason") != "client_loss"]
    return {"received": len(real), "analyzed": sum(s["status"] == "analyzed" for s in samples),
            "dropped_queue_overflow": sum(s.get("reason") == "queue_overflow" for s in samples),
            "dropped_stale": sum(s.get("reason") in ("stale", "session_stopped") for s in samples),
            "adapter_error": sum(s["status"] == "adapter_error" for s in samples),
            "client_loss_events": sum(s.get("reason") == "client_loss" for s in samples),
            "client_lost_frames": sum(s.get("lost_frames", 0) for s in samples if s.get("reason") == "client_loss")}


RETENTION_MODES = ("frames", "metadata_only")
SESSION_FORMAT = "mg.webcam.session/3"   # MG-WCAM-033: written into the digest-bound identity of every session this build stores. Its absence means «legacy OR downgraded»: the two cannot be told apart by content


def retention_doc(mode: str) -> dict:
    return {"mode": mode, "frames_persisted": mode == "frames"}


def source_digest_payload(samples: list, capture: dict, closed_reason: str, live_stats: dict, retention: dict | None, provenance_sha256: str | None = None, session_format: str | None = None) -> dict:
    """Everything RECORDED rather than derivable is bound into the session identity. `retention` is part of it for sessions that declare one (sessions stored before the
    privacy setting existed have no such member and keep their original identity)."""
    p = {"kind": "webcam_session", "samples": samples, "capture": capture, "closed_reason": closed_reason, "live_stats": live_stats}
    if retention is not None:
        p["retention"] = retention
    if provenance_sha256 is not None:
        p["provenance_sha256"] = provenance_sha256   # the COMPLETE provenance document (runtime packages, device, platform, model record, capture) is part of the session identity
    if session_format is not None:
        p["session_format"] = session_format         # MG-WCAM-033: sessions stored before this member existed keep their original identity
    return p


def build_evidence(frames: list[dict], samples: list[dict], pts_rel: list[int], mode: str = "frames") -> list[dict]:
    if mode == "metadata_only":
        return []   # Privacy ON: no image was persisted, so there is no evidence file to reference
    return [{"frame_index": f["frame_index"], "pts_us": pts_rel[f["frame_index"]], "file": f"frames/{f['frame_index']:06d}.jpg",
             "file_sha256": samples[f["frame_index"]]["jpeg_sha256"], "pixel_sha256": f["pixel_sha256"], "pixels_origin": "client_jpeg_decoded"} for f in frames]


def build_manifest(sid: str, source_digest: str, ft_digest: str, od: str, evidence: list[dict], samples: list[dict], closed_reason: str, live_stats: dict,
                   retention: dict | None = None) -> dict:
    m = {"schema": "mg.manifest.vs02.session", "run_id": sid, "mode": "webcam_session",
            "digests": {"source": source_digest, "frame_table": ft_digest, "obsset": od}, "origin_summary": ["model_inference"],
            "evidence_contract": {"id": "mg.evidence.client_jpeg_exact_v1", "note": "the exact JPEG bytes received and decoded for inference"},
            "evidence": evidence, "drops": [s for s in samples if s["status"] != "analyzed"], "closed_reason": closed_reason,
            "counters": counters_from_samples(samples), "live_stats": live_stats, "limitations": list(LIMITATIONS)}
    if retention is not None:
        m["retention"] = retention
    return m


def capture_ok(c) -> bool:
    return (isinstance(c, dict) and set(c) == {"origin", "device_label", "user_agent", "first_capture_clock_us"} and c["origin"] in CAPTURE_ORIGINS
            and isinstance(c["device_label"], str) and len(c["device_label"]) <= 80 and isinstance(c["user_agent"], str) and len(c["user_agent"]) <= 160
            and isinstance(c["first_capture_clock_us"], int) and not isinstance(c["first_capture_clock_us"], bool) and c["first_capture_clock_us"] >= 0)


def build_provenance(device: str, entry: dict, weights_sha256: str, capture: dict) -> dict:
    p = runtime_provenance(device, DECODER_ID)
    p["model"] = {"model_id": entry["model_id"], "architecture": entry["architecture"], "weights_sha256": weights_sha256,
                  "source_url": entry["source_url"], "license_evidence": entry["license_evidence"]}
    p["capture"] = {"declared_by": "client", **capture, "physical_camera_verified_by_server": False}
    return p


def accounting_ok(a) -> bool:
    nat = lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0
    if not isinstance(a, dict) or set(a) not in (set(ACCOUNTING_KEYS), set(ACCOUNTING_KEYS_V2), set(ACCOUNTING_KEYS_V3)):
        return False
    ints = ACCOUNTING_INTS_V3 if "unclassified_frames" in a else (ACCOUNTING_INTS_V2 if "rejected_invalid_frames" in a else ACCOUNTING_INTS)
    return (all(nat(a[k]) for k in ints) and (a["captured_declared"] is None or nat(a["captured_declared"]))
            and a["origin_basis"] in ("measured", "cadence_extrapolation") and a["tail_basis"] in TAIL_BASES and isinstance(a["tail_exact"], bool)
            and a["closure"] in ("clean_stop", "device_ended", "unexpected_disconnect"))


def accounting_from_samples(samples: list[dict]) -> dict:
    """What the sample log alone proves about the accounting (the rest is a client declaration or a measured counter)."""
    pos = lambda p: sum(s.get("lost_frames", 0) for s in samples if s.get("reason") == "client_loss" and s.get("loss_position") == p)
    c = counters_from_samples(samples)
    rej = sum(s.get("rejected_frames", 0) for s in samples if s.get("reason") == "client_loss")
    return {"accepted": c["received"], "analyzed": c["analyzed"], "dropped": c["dropped_queue_overflow"] + c["dropped_stale"], "adapter_error": c["adapter_error"],
            "never_observed_frames": c["client_lost_frames"], "rejected_invalid_frames": rej,
            "unclassified_frames": sum(s.get("unclassified_frames", 0) for s in samples if s.get("reason") == "client_loss"), "prefix_frames": pos("prefix"), "gap_frames": pos("gap"), "tail_frames": pos("tail"), "unknown_intervals": c["client_loss_events"]}


SAMPLE_KEYS = {"seq", "capture_us", "status", "reason", "jpeg_sha256"}
STUB_KEYS = SAMPLE_KEYS | {"lost_frames", "rejected_frames", "unclassified_frames", "loss_position"}
DROP_REASONS = ("queue_overflow", "stale", "session_stopped")
LOSS_POSITIONS = ("prefix", "gap", "tail")
_ERR_REASON = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}")
_SHA = re.compile(r"[0-9a-f]{64}")
_ni = lambda v, lo=0: isinstance(v, int) and not isinstance(v, bool) and v >= lo


def samples_valid(samples, retention: str | None = None) -> bool:
    """MG-WCAM-032: the sample log is exactly what `Session._finalize` writes. Every sample has exactly the members of its kind; a received frame is `analyzed` (no reason), `not_analyzed_dropped` with one of the
    three recorded drop reasons, or `adapter_error` with an exception-class reason; a `client_loss` stub stands for frames that never arrived (no sequence, no image, a position). Sequences strictly increase
    among received frames and capture times never go backwards. Anything else (an unknown reason or status, a missing or extra member, a contradictory status/evidence pair) is refused, so no accepted
    frame can fall outside every outcome category. MG-WCAM-034: when the retention mode is given, an image hash exists exactly where the producer writes one: `frames` mode and an `analyzed` sample (the spooled JPEG);
    never for a dropped, errored or lost sample and never in `metadata_only` mode, where no image is ever persisted."""
    if not isinstance(samples, list):
        return False
    last_seq, last_t = -1, -1
    for x in samples:
        if not isinstance(x, dict) or not _ni(x.get("capture_us")) or x["capture_us"] < last_t:
            return False
        last_t = x["capture_us"]
        if x.get("reason") == "client_loss":
            if (set(x) != STUB_KEYS or x["seq"] is not None or x["status"] != "not_analyzed_dropped" or x["jpeg_sha256"] is not None or x["loss_position"] not in LOSS_POSITIONS
                    or not _ni(x["lost_frames"], 1) or not _ni(x["rejected_frames"]) or not _ni(x["unclassified_frames"])):
                return False
            continue
        if set(x) != SAMPLE_KEYS or not _ni(x["seq"]) or x["seq"] <= last_seq or not (x["jpeg_sha256"] is None or (isinstance(x["jpeg_sha256"], str) and _SHA.fullmatch(x["jpeg_sha256"]))):
            return False
        last_seq = x["seq"]
        st, rs = x["status"], x["reason"]
        if retention is not None and (x["jpeg_sha256"] is not None) != (retention == "frames" and st == "analyzed"):
            return False
        if not ((st == "analyzed" and rs is None) or (st == "not_analyzed_dropped" and rs in DROP_REASONS) or (st == "adapter_error" and isinstance(rs, str) and _ERR_REASON.fullmatch(rs))):
            return False
    return True


def accounting_consistent(ls: dict, samples: list[dict], retention: str | None = None, current: bool = False) -> bool:
    """The recorded accounting must equal what the sample log proves, and the parts must add up (prefix + gap + tail = never observed + refused-invalid)."""
    a = ls.get("accounting")
    if not samples_valid(samples, retention):
        return False                                                       # MG-WCAM-032: the log itself must be well formed before any count derived from it means anything
    if current and (a is None or set(a) != set(ACCOUNTING_KEYS_V3) or set(ls) != set(STATS_KEYS_V2)):
        return False                                                       # MG-WCAM-033: a current-format session carries the complete accounting; it cannot be read as an older shape
    if a is None:
        return "accounting" not in ls
    base = accounting_from_samples(samples)
    v2 = "rejected_invalid_frames" in a
    v3 = "unclassified_frames" in a
    if not accounting_ok(a):
        return False
    if v2 and not all(isinstance(s.get("rejected_frames"), int) and isinstance(s.get("unclassified_frames", 0), int) and s["rejected_frames"] >= 0 and s.get("unclassified_frames", 0) >= 0
                      and s["rejected_frames"] + s.get("unclassified_frames", 0) <= s.get("lost_frames", 0) for s in samples if s.get("reason") == "client_loss"):
        return False                                                       # per stub: examined + unclassified ticks can never exceed the ticks the stub stands for
    same = {k: base[k] for k in ("accepted", "analyzed", "dropped", "adapter_error", "prefix_frames", "gap_frames", "tail_frames", "unknown_intervals")}
    if not all(a[k] == v for k, v in same.items()):
        return False
    if v2:
        unc = base["unclassified_frames"] if v3 else 0
        if v3 and a["unclassified_frames"] != unc:
            return False
        if (a["rejected_invalid_frames"] != base["rejected_invalid_frames"] or a["never_observed_frames"] != base["never_observed_frames"] - base["rejected_invalid_frames"] - unc
                or a["rejected_invalid_frames"] + unc > a["invalid_requests"] + ls.get("intake_rejected", 0)):    # examined = refused at the transport + refused at intake
            return False
        if a["submitted"] < a["accepted"] + a["invalid_requests"]:
            return False
    elif a["never_observed_frames"] != base["never_observed_frames"] or a["submitted"] < a["accepted"]:
        return False
    return (a["prefix_frames"] + a["gap_frames"] + a["tail_frames"] == a["never_observed_frames"] + (a["rejected_invalid_frames"] if v2 else 0) + (a["unclassified_frames"] if v3 else 0)
            and a["accepted"] == a["analyzed"] + a["dropped"] + a["adapter_error"] and (a["captured_declared"] is None or a["captured_declared"] >= a["accepted"])
            and (a["prefix_frames"] > 0) == (a["origin_basis"] == "cadence_extrapolation"))


def live_stats_ok(ls) -> bool:
    if not isinstance(ls, dict) or set(ls) not in (set(STATS_KEYS), set(STATS_KEYS_V2)):
        return False
    if "accounting" in ls and not accounting_ok(ls["accounting"]):
        return False
    num = lambda v: v is None or (isinstance(v, int) and not isinstance(v, bool) and v >= 0)  # integers only (hashed, canonical JSON)
    lat = ls["latency_ms"]
    return (isinstance(lat, dict) and set(lat) == {"p50", "p95", "max"} and all(num(v) for v in lat.values()) and all(num(ls[k]) for k in STATS_KEYS if k != "latency_ms"))
