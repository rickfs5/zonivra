"""VS-02 routes: webcam sessions, stored sessions, zones, risk evaluation and export (shared by recorded runs and webcam sessions)."""
from __future__ import annotations

import copy
import io
import json
import os
import threading
import shutil
import re
from pathlib import Path

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from . import __version__
from .detector import load_manifest_entry
from .analysis import artifact_lock
from .risk.tracker import TRACKER_ID, TRACKER_VERSION
from .canon import NotNfcError, digest, digest16, file_sha256, loads_strict, read_json_strict
from .risk import evaluation as ev
from .risk.geometry import InvalidZone
from .risk.kernel import ENGINE_VERSION, SEMANTICS_VERSION
from .risk.tracker import TrackerParams
from .risk.zone_eval import make_zone
from . import snapshot
from .session_verify import session_fingerprint, verify_session
from .schema import LIVE_SCHEMA_ID, ValidationFailure, obsset_digest, validate_observation_set
from .timebase import pts_us, round_div
from .verify import safe_evidence_path, trust_scope
from .webcam import LATE_FILE, MAX_FRAME_BYTES, MAX_MSG_SEQ, SESSION_RE, STORE_LOCK, Session, SessionError, SessionManager

RUN_RE = re.compile(r"^run_[0-9a-f]{16}$")
SESS_RE = re.compile(SESSION_RE)


SESSION_DELETE_ENABLED = False   # MG-DELETE-035 containment switch; see refuse_if_delete_disabled. Only tests of the dormant logic turn it on.


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")   # unknown members are refused, never silently ignored (cycle-22 MG-API-022; every body model of this module, cycle-25 MG-API-025)


class ZoneIn(Strict):
    zone_id: str = Field(min_length=1, max_length=32)
    kind: str = "restricted"
    polygon: list[Annotated[list[int], Field(min_length=2, max_length=2)]] = Field(max_length=256)
    name: str | None = Field(default=None, max_length=60)


class ZonesReq(Strict):
    zones: list[ZoneIn] = Field(max_length=8)
    expected_revision: int = Field(ge=0, le=1_000_000_000)   # compare-and-swap: the revision the editor last saw (cycle-17 MG-ZONE-017)


class SimReq(Strict):
    base_run_id: str = Field(pattern=r"^run_[0-9a-f]{16}$")


def build_simulation(b: dict) -> dict:
    """SIMULACION: a person stands in a restricted zone (YELLOW after the dwell) and a vehicle joins later (RED). Boxes are proportional to the frame; confidences are null
    (synthetic_scripted); the picture contains nobody: the underlying video is only the stage."""
    n, w, h = b["frame_count"], b["width"], b["height"]
    p0, p1, v0 = n * 8 // 100, n * 56 // 100, n * 40 // 100
    def box(label, key, x, y, bw, bh):
        return {"obj_key": key, "cls": {"label": label, "state": "known", "detector_label": "SIMULACION", "detector_class_id": 0},
                "bbox": {"x": w * x // 100, "y": h * y // 100, "w": max(1, w * bw // 100), "h": max(1, h * bh // 100)},
                "truncated": {"left": False, "right": False, "top": False, "bottom": False}, "confidence": {"permille": None, "kind": "not_applicable"}, "track_ref": None}
    frames, analyzed = [], set()
    for i in range(0, n, 3):
        objs = []
        if p0 <= i < p1:
            objs.append(box("person", "o1", 31, 55, 5, 28))
        if v0 <= i < p1:
            objs.append(box("vehicle", "o2", 47, 49, 23, 35))
        frames.append({"frame_index": i, "status": "analyzed", "pixel_sha256": "0" * 64, "objects": objs})
        analyzed.add(i)
    ranges, start = [], None
    for i in range(n + 1):
        miss = i < n and i not in analyzed
        if miss and start is None:
            start = i
        elif not miss and start is not None:
            ranges.append({"from_frame": start, "to_frame": i - 1, "status": "not_analyzed_stride"})
            start = None
    return {"schema": "mg.obsset.vs01", "source_binding": b, "coord_space": "image_px_v1", "taxonomy": "mg.cls.v0", "origin": "synthetic_scripted",
            "producer": {"adapter_id": "replay", "adapter_version": "0", "model_id": None, "weights_sha256": None, "params_hash": "5" * 64, "label_map_digest": None,
                         "params": {}},
            "tracks": [], "frames": frames, "frame_status_ranges": ranges,
            "meta": {"created_utc": "2026-01-01T00:00:00Z", "tool": "mineguard-simulation/1", "notes": "SIMULACION: escenario reproducible persona + vehiculo; sin imagenes personales ni inferencia"}}


class DeleteReq(Strict):
    ids: list[Annotated[str, Field(max_length=40)]] | None = Field(default=None, max_length=500)
    all: bool = False
    confirm: str = Field(max_length=20)
    expected_count: int = Field(ge=0, le=1_000_000)


class MediaReq(Strict):
    confirm: str = Field(max_length=20)
    expected_files: int = Field(ge=0, le=1_000_000)


class CaptureIn(Strict):
    origin: str = Field(max_length=40)
    device_label: str = Field(default="", max_length=80)
    user_agent: str = Field(default="", max_length=160)


class StartReq(Strict):
    width: int = Field(ge=-1_000_000, le=1_000_000)       # the exact accepted range is judged by the session (message ids); this only keeps the arithmetic finite and small
    height: int = Field(ge=-1_000_000, le=1_000_000)
    interval_ms: int = Field(default=300, ge=-1_000_000, le=1_000_000)
    threshold: float = Field(default=0.30, ge=-10.0, le=10.0, allow_inf_nan=False)   # NaN and infinities are refused here; the permitted range is judged by the session
    zones: list[ZoneIn] = Field(default=[], max_length=8)
    capture: CaptureIn
    privacy: Literal["on", "off"] = "on"   # Privacy ON (no image is ever persisted) is the default; "off" must be requested explicitly and is fixed for the session


async def read_limited(request: Request, limit: int, too_large) -> bytes:
    """Reads the body in chunks and refuses as soon as it exceeds the limit, whether or not a Content-Length header was sent (chunked uploads)."""
    n, chunks = 0, []
    async for c in request.stream():
        n += len(c)
        if n > limit:
            raise too_large
        chunks.append(c)
    return b"".join(chunks)


def _atomic_write(path: Path, doc: dict):
    """Writes through a RANDOM temporary name created with O_EXCL|O_NOFOLLOW (mode 0600) in the same directory, fsyncs it and renames it over the destination, so a pre-planted
    symlink or file can never redirect or truncate the write (cycle-16 MG-ZONE-016)."""
    import secrets
    data = json.dumps(doc, indent=1, ensure_ascii=False).encode()
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


ZONE_KEYS = {"zone_id", "kind", "coord_space", "width", "height", "polygon", "name", "digest"}


def validate_zone_doc(doc, width: int, height: int) -> dict:
    """A persisted zone set is data on disk, not trusted input: every zone is REBUILT through the same validator that creation uses (canonical polygon, digest) and must
    equal what is stored, with the observation binding's dimensions (cycle-5 review MG-ZONE-014). Returns the document or raises ValueError."""
    if not isinstance(doc, dict) or set(doc) != {"revision", "zones"} or not isinstance(doc["revision"], int) or isinstance(doc["revision"], bool) or doc["revision"] < 0:
        raise ValueError("zone set shape")
    zs = doc["zones"]
    if not isinstance(zs, list) or len(zs) > 8:
        raise ValueError("zone list")
    seen = set()
    for z in zs:
        if not isinstance(z, dict) or set(z) != ZONE_KEYS or (z["width"], z["height"]) != (width, height):
            raise ValueError("zone shape or dimensions")
        try:
            rebuilt = make_zone(z["zone_id"], z["kind"], z["polygon"], width, height, z["name"])
        except (InvalidZone, TypeError, KeyError) as e:
            raise ValueError(f"zone invalid: {e}")
        if rebuilt != z or z["zone_id"] in seen:
            raise ValueError("zone differs from its canonical rebuild or is duplicated")
        seen.add(z["zone_id"])
    return doc


zone_lock = artifact_lock   # the same per-artifact lock the installer and the deletion routes take


def load_zones(d: Path, strict: bool = True, snap=None) -> dict:
    if snap is not None:   # MG-EVENT-032: the zone set and the binding come from the SAME immutable snapshot as everything else derived from it
        if snap.raw["risk/zones.json"] == snapshot.UNSAFE:
            raise HTTPException(409, {"msg_id": "mg.refusal.run_inconsistent"})
        if not snap.present("risk/zones.json"):
            return {"revision": 0, "zones": []}
        try:
            doc = snap.doc("risk/zones.json")
            if not strict:
                return doc
            b = snap.doc("observations.json")["source_binding"]
            return validate_zone_doc(doc, b["width"], b["height"])
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(409, {"msg_id": "mg.refusal.zones_inconsistent"})
    p = d / "risk" / "zones.json"
    if p.is_symlink() or (d / "risk").is_symlink():
        raise HTTPException(409, {"msg_id": "mg.refusal.run_inconsistent"})
    if not p.is_file():
        return {"revision": 0, "zones": []}
    try:
        doc = read_json_strict(p)
        if not strict:
            return doc
        b = read_json_strict(d / "observations.json")["source_binding"]
        return validate_zone_doc(doc, b["width"], b["height"])
    except (OSError, ValueError, KeyError, TypeError):
        raise HTTPException(409, {"msg_id": "mg.refusal.zones_inconsistent"})


def check_components(snap, obs, source, manifest):
    """MG-RISK-035 containment: the bundle that is exported/shown and the components the evaluation is derived from must be the SAME run. A disagreement is refused (the caller maps it to run_inconsistent),
    never shown as one result. This is a local consistency check, not authenticity: someone who rewrites every file consistently is outside what it claims."""
    bundle = snap.doc("export.json")
    if bundle.get("observation_set") != obs or bundle.get("source") != source or bundle.get("manifest") != manifest or obsset_digest(obs) != manifest["digests"]["obsset"]:
        raise ValueError("run_components_disagree")


def risk_inputs(d: Path, snap=None):
    if snap is not None:
        obs, source, manifest = snap.doc("observations.json"), snap.doc("source.json"), snap.doc("manifest.json")
        check_components(snap, obs, source, manifest)
    else:
        obs, source, manifest = read_json_strict(d / "observations.json"), read_json_strict(d / "source.json"), read_json_strict(d / "manifest.json")
    ft = source["frame_table"]
    num, den = ft["time_base"]["num"], ft["time_base"]["den"]
    pts = [pts_us(ft["pts_ticks"], i, num, den) for i in range(len(ft["pts_ticks"]))]
    last = round_div(ft["last_frame_duration_ticks"] * num * 1_000_000, den) if ft["last_frame_duration_ticks"] else 0   # integer half-up (MG-DATA-001 section 4), never float or ties-to-even
    return obs, source, manifest, pts, (pts[-1] + last)


def evaluate_dir(d: Path, zdoc: dict | None = None, snap=None) -> dict:
    """Evaluation from ONE zone-set snapshot (zdoc): callers pass the snapshot they also return, so a concurrent zone edit can never yield a self-contradictory response."""
    obs, source, manifest, pts, end_us = risk_inputs(d, snap)
    zones = (zdoc if zdoc is not None else load_zones(d, snap=snap))["zones"]
    recs, _ = ev.records_from_observations(obs, pts)
    params, tparams = ev.params_for_observations(obs, recs)
    inputs = {"obsset_digest": manifest["digests"]["obsset"], "source_digest": source["source_digest"], "frame_table_digest": source["frame_table_digest"], "kind": source.get("kind", "file"),
              "frame_table_provider": source.get("frame_table_provider"), "run_config": obs["producer"]["params_hash"]}
    doc = ev.evaluate_records(recs, end_us, zones, params, tparams, inputs, obs["origin"])
    tomb = d / "media_deleted.json"
    if (snap.present("media_deleted.json") if snap is not None else (tomb.is_file() and not tomb.is_symlink())):
        # the retained images were deleted: the historical references must not be rendered as live evidence (cycle-16 MG-EVIDENCE-016)
        return {**ev.attach_evidence(doc, []), "evidence_status": "media_deleted"}
    return ev.attach_evidence(doc, manifest["evidence"])


def register_vs02(app, st, manager: SessionManager, run_dir_or_404, run_trust_of):
    def session_dir_or_404(sid: str) -> Path:
        d = st.runs_dir / sid
        if not SESS_RE.fullmatch(sid) or d.is_symlink() or not (d / "export.json").is_file():
            raise HTTPException(404, {"msg_id": "mg.refusal.unknown_session"})
        return d

    def session_trust(d: Path) -> str:
        t = st.trusted.get(d.name)
        if not t:
            return "unverified"
        try:
            fp = session_fingerprint(d)
            ok = fp == t[1]
        except (OSError, ValueError, KeyError, TypeError):
            ok = False
        if not ok:
            st.trusted.pop(d.name, None)  # monotonic revocation (cycle-5 MG-TRUST-014)
            return "unverified"
        return t[0]

    def resolver(kind: str, rid: str) -> Path:
        return run_dir_or_404(rid) if kind == "runs" else session_dir_or_404(rid)

    def trust_for(kind: str, d: Path) -> str:
        return run_trust_of(d) if kind == "runs" else session_trust(d)

    def err(e: SessionError):
        raise HTTPException(e.status, {"msg_id": e.msg_id})

    # ------------------------------------------------------------------ replay (testing mechanism, never inference)
    def store_replay(b_src: dict, obs: dict, origin: str) -> str:
        """Stores a validated synthetic/annotated observation set as a NEW run directory (no weights, no evidence images, no personal data)."""
        od = obsset_digest(obs)
        rid = "run_" + digest16(od)
        tmp = st.runs_dir / f"_tmp_{__import__('uuid').uuid4().hex[:8]}"
        (tmp / "frames").mkdir(parents=True)
        try:
            prov = {"kind": "replay", "packages": {}, "device": "n/a", "python": "n/a", "macos": "n/a", "model": None,
                    "note": "synthetic/annotated observations: no model was run; no weights, no evidence images"}
            manifest = {"schema": "mg.manifest.vs01", "run_id": rid, "mode": "replay", "digests": {"source": b_src["source_digest"], "frame_table": b_src["frame_table_digest"], "obsset": od},
                        "origin_summary": [origin], "evidence_contract": None, "evidence": [],
                        "limitations": ["mg.lim.image_space_only", "mg.lim.not_safety_system"]}
            bundle = {"schema": "mg.export.vs01", "observation_set": obs, "source": b_src, "provenance": prov, "manifest": manifest}
            for name, doc in (("observations.json", obs), ("source.json", b_src), ("provenance.json", prov), ("manifest.json", manifest), ("export.json", bundle)):
                (tmp / name).write_text(json.dumps(doc, indent=1, ensure_ascii=False))
            from .analysis import install_dir
            install_dir(tmp, st.runs_dir / rid, od)
        except RuntimeError as e:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
            raise HTTPException(409, {"msg_id": str(e)})
        return rid

    @app.post("/api/simulations/person-vehicle", status_code=201)
    def create_simulation(req: SimReq):
        """Reproducible SIMULATION (person, then person + vehicle, in one restricted zone) over the source of an existing MP4 run. Everything is generated here from the
        run's binding: no camera, no photograph, no model, no copy of any image. The base run and every session are only READ; the result is a separate synthetic run."""
        base = run_dir_or_404(req.base_run_id)
        b_obs, b_src = read_json_strict(base / "observations.json"), read_json_strict(base / "source.json")
        if b_src.get("kind", "file") != "file":
            raise HTTPException(409, {"msg_id": "mg.refusal.replay_base_not_file"})
        b = b_obs["source_binding"]
        obs = build_simulation(b)
        binding = {**b, "source_digest": b_src["source_digest"], "frame_table_digest": b_src["frame_table_digest"]}
        try:
            model = validate_observation_set(obs, binding)
        except ValidationFailure as e:
            raise HTTPException(422, {"msg_id": "mg.refusal.invalid_observation_set", "codes": e.codes})
        rid = store_replay(b_src, obs, model.origin)
        d = st.runs_dir / rid
        with artifact_lock(rid):   # check-and-create is one step under the lock shared with zone PUT and installation: a concurrent user edit can never be overwritten (cycle-19 MG-ZONE-019)
            if not (d / "risk" / "zones.json").exists():   # an existing zone set (user edits) is never overwritten
                (d / "risk").mkdir(exist_ok=True)
                w, h = b["width"], b["height"]
                z = make_zone("Z1", "restricted", [[w * 15 // 100, h * 40 // 100], [w * 80 // 100, h * 40 // 100], [w * 80 // 100, h * 95 // 100], [w * 15 // 100, h * 95 // 100]], w, h, "Zona simulada")
                _atomic_write(d / "risk" / "zones.json", {"revision": 1, "zones": [z]})
        al = getattr(st, "alerts", None)
        if al is not None:   # the scripted demonstration exercises the alert chain; its alerts are labelled SIMULACIÓN (synthetic_scripted) everywhere
            try:
                doc = evaluate_dir(d)
                al.ingest_simulation(rid, [e for z in doc["zones"].values() for e in z["events"]])
            except Exception:   # alerting never breaks the simulation
                pass
        return {"run_id": rid, "origin": model.origin, "zone_id": "Z1"}

    @app.post("/api/replays", status_code=201)
    async def create_replay(request: Request):
        """Stores a synthetic_scripted / human_annotated observation set bound to an existing run's source as a NEW run whose origin stays what it declares.
        It carries no weights, no evidence images and no model provenance, so it can never be verified or presented as inference."""
        raw = await read_limited(request, 8_000_000, HTTPException(413, {"msg_id": "mg.refusal.replay_too_large"}))
        try:
            body = loads_strict(raw)
        except ValueError as e:
            return JSONResponse(status_code=422, content={"msg_id": "mg.refusal.invalid_observation_set", "codes": ["V-17" if isinstance(e, NotNfcError) else "V-25"]})
        if not isinstance(body, dict) or not isinstance(body.get("observation_set"), dict) or not isinstance(body.get("base_run_id"), str):
            return JSONResponse(status_code=422, content={"msg_id": "mg.refusal.invalid_observation_set", "codes": ["V-20"]})
        base = run_dir_or_404(body["base_run_id"])
        obs = body["observation_set"]
        if obs.get("origin") == "model_inference":
            return JSONResponse(status_code=422, content={"msg_id": "mg.refusal.replay_as_inference"})
        b_obs, b_src = read_json_strict(base / "observations.json"), read_json_strict(base / "source.json")
        if b_src.get("kind", "file") != "file":
            raise HTTPException(409, {"msg_id": "mg.refusal.replay_base_not_file"})
        binding = {**b_obs["source_binding"], "source_digest": b_src["source_digest"], "frame_table_digest": b_src["frame_table_digest"]}
        try:
            model = validate_observation_set(obs, binding)
        except ValidationFailure as e:
            return JSONResponse(status_code=422, content={"msg_id": "mg.refusal.invalid_observation_set", "codes": e.codes})
        if obs["source_binding"] != b_obs["source_binding"]:
            return JSONResponse(status_code=422, content={"msg_id": "mg.refusal.invalid_observation_set", "codes": ["V-02"]})
        rid = store_replay(b_src, obs, model.origin)
        return {"run_id": rid, "origin": model.origin}

    # ------------------------------------------------------------------ webcam sessions
    @app.post("/api/webcam/sessions", status_code=201)
    def start_session(req: StartReq):
        try:
            return manager.start(req.width, req.height, req.interval_ms, req.threshold, [z.model_dump() for z in req.zones], req.capture.model_dump(), retain_frames=(req.privacy == "off"))
        except SessionError as e:
            err(e)

    @app.post("/api/webcam/sessions/{sid}/frames", status_code=202)
    async def post_frame(sid: str, request: Request):
        s = None
        seq = None
        try:
            s = manager.get(sid)
            try:   # the sequence number is read FIRST: a request the server examines and refuses is then accounted as refused, never mistaken for client loss (cycle-23 MG-WCAM-023)
                seq, cap = int(request.headers["x-mg-seq"]), int(request.headers["x-mg-capture-us"])
                if seq < 0 or cap < 0 or seq > 10_000_000:
                    seq = None
                    raise ValueError
            except (KeyError, ValueError):
                raise SessionError(400, "mg.refusal.frame_headers")
            try:
                if int(request.headers.get("content-length", "0") or 0) > MAX_FRAME_BYTES:
                    raise SessionError(413, "mg.refusal.frame_too_large")
                body = await read_limited(request, MAX_FRAME_BYTES, SessionError(413, "mg.refusal.frame_too_large"))
                if not body:
                    raise SessionError(400, "mg.refusal.frame_not_jpeg_or_wrong_size")
                try:
                    from PIL import Image
                    with Image.open(io.BytesIO(body)) as im:
                        ok = im.format == "JPEG" and im.size == (s.cfg["width"], s.cfg["height"])
                except Exception:
                    ok = False
                if not ok:
                    raise SessionError(400, "mg.refusal.frame_not_jpeg_or_wrong_size")
            except SessionError:
                if isinstance(s, Session):
                    s.note_invalid(seq)
                raise
            idx = s.submit(seq, cap, body)
            return {"accepted": idx, "queue_depth": s.q.qsize()}
        except SessionError as e:
            err(e)

    @app.get("/api/webcam/sessions/{sid}")
    def session_view(sid: str):
        try:
            return manager.get(sid).view()
        except SessionError as e:
            err(e)

    def msg_seq_of(request: Request) -> int | None:
        """Optional control message number (idempotent retries, reordering). Validated; absent for legacy callers."""
        v = request.headers.get("x-mg-msg-seq")
        if v is None:
            return None
        try:
            n = int(v)
            if not 0 <= n <= MAX_MSG_SEQ:
                raise ValueError
        except ValueError:
            raise HTTPException(400, {"msg_id": "mg.refusal.frame_headers"})
        return n

    @app.post("/api/webcam/sessions/{sid}/watermark", status_code=202)
    def watermark(sid: str, request: Request):
        try:
            next_seq = int(request.headers["x-mg-next-seq"])
            if not 0 <= next_seq <= 10_000_000:
                raise ValueError
        except (KeyError, ValueError):
            raise HTTPException(400, {"msg_id": "mg.refusal.frame_headers"})
        msg = msg_seq_of(request)
        try:
            return manager.watermark(sid, next_seq, msg)
        except SessionError as e:
            err(e)

    @app.post("/api/webcam/sessions/{sid}/stop")
    def stop_session(sid: str, request: Request, reason: str = "client_stop"):
        if reason not in ("client_stop", "device_ended"):  # bounded vocabulary: the stored reason is part of the artifact
            raise HTTPException(422, {"msg_id": "mg.refusal.session_bad_stop_reason"})
        try:
            ns = request.headers.get("x-mg-next-seq")
            next_seq = int(ns) if ns is not None else None
            if next_seq is not None and not 0 <= next_seq <= 10_000_000:
                raise ValueError
        except ValueError:
            raise HTTPException(400, {"msg_id": "mg.refusal.frame_headers"})
        msg = msg_seq_of(request)
        try:
            out = manager.stop(sid, reason, next_seq, msg)
        except SessionError as e:
            err(e)
        fresh = out.pop("fresh", False)
        if fresh and out.get("run_id"):   # only the unique running->done transition grants; an idempotent repeat of stop never mutates trust (cycle-17 MG-TRUST-017)
            d = st.runs_dir / out["run_id"]
            st.trusted[d.name] = ("session", session_fingerprint(d))  # produced by THIS process; not yet deeply verified
        return out if msg is not None else {**out, "ack": None}

    # ------------------------------------------------------------------ session retention, deletion (MG privacy P0)
    def retention_of(d: Path, snap=None) -> str:
        """'frames' | 'metadata_only' | 'media_deleted' (read from the digest-bound source record and the deletion tombstone). With `snap` both come from that one immutable snapshot."""
        if (snap.present("media_deleted.json") if snap is not None else ((d / "media_deleted.json").is_file() and not (d / "media_deleted.json").is_symlink())):
            return "media_deleted"
        try:
            r = (snap.doc("source.json") if snap is not None else read_json_strict(d / "source.json")).get("retention")
            return r["mode"] if isinstance(r, dict) and r.get("mode") in ("frames", "metadata_only") else "frames"
        except (OSError, ValueError, KeyError, TypeError):
            return "frames"

    def tree_size(d: Path) -> tuple[int, int]:
        n = b = 0
        for root, dirs, files in os.walk(d, followlinks=False):
            for f in files:
                try:
                    b += os.lstat(os.path.join(root, f)).st_size
                    n += 1
                except OSError:
                    pass
        return n, b

    def structure_problem(d: Path) -> str | None:
        """None when the directory at `d` is a regular (non-symlink) webcam-session tree of this application: kind webcam, required records, no symlink inside, one filesystem, only regular files and directories."""
        import stat as _st
        try:
            ls = os.lstat(d)
        except FileNotFoundError:
            return "missing"
        if not _st.S_ISDIR(ls.st_mode):
            return "symlink_or_not_a_directory"
        try:
            if read_json_strict(d / "source.json").get("kind") != "webcam" or not (d / "export.json").is_file() or not (d / "observations.json").is_file():
                return "not_a_webcam_session"
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return "unreadable_session_record"
        for rt, dirs, files in os.walk(d, followlinks=False):
            for n in dirs + files:
                q = os.lstat(os.path.join(rt, n))
                if _st.S_ISLNK(q.st_mode):
                    return "contains_a_symlink"
                if q.st_dev != ls.st_dev or not (_st.S_ISDIR(q.st_mode) or _st.S_ISREG(q.st_mode)):
                    return "contains_an_unexpected_file_type_or_filesystem"
        return None

    def deletable(d: Path) -> str | None:
        """None when `d` is provably a stored webcam session directory this application owns; otherwise the reason it is refused. Ambiguity is always refused."""
        if not SESS_RE.fullmatch(d.name) or d.parent != st.runs_dir:
            return "not_a_session_id"
        try:
            os.lstat(d)
        except FileNotFoundError:
            return "missing"
        if d.resolve() != st.runs_dir.resolve() / d.name:
            return "symlink_or_not_a_directory"
        return structure_problem(d)

    async def parse_delete_body(request: Request, model):
        raw = await read_limited(request, 65536, HTTPException(413, {"msg_id": "mg.refusal.body_too_large"}))
        try:
            return model.model_validate_json(raw)
        except ValidationError:
            raise HTTPException(422, {"msg_id": "mg.refusal.delete_needs_confirmation"})

    def refuse_if_delete_disabled():
        """MG-DELETE-035 containment: the recursive removal below validates a renamed-aside directory and then removes it BY PATH, so a substitution between the two steps could remove the wrong tree.
        Until that is rebuilt on pinned directory descriptors the destructive routes answer honestly and touch nothing. Stored data is preserved."""
        if not SESSION_DELETE_ENABLED:
            raise HTTPException(503, {"msg_id": "mg.refusal.delete_temporarily_unavailable"})

    def pin(p: Path) -> tuple[int, int]:
        q = os.lstat(p)
        return (q.st_dev, q.st_ino)

    @app.post("/api/sessions/delete")
    async def delete_sessions(request: Request):
        refuse_if_delete_disabled()                                  # MG-DELETE-036: BEFORE the body is read, parsed or validated
        return await run_in_threadpool(delete_sessions_impl, await parse_delete_body(request, DeleteReq))

    def delete_sessions_impl(req: DeleteReq):
        """Deletes ONLY webcam session directories owned by this application. Needs the literal confirmation and the exact count the user saw; every target is
        validated individually and anything ambiguous is refused, never guessed. The result reports what actually happened per session."""
        import shutil
        import uuid
        if req.confirm != "ELIMINAR" or (req.ids is None) == (not req.all):
            raise HTTPException(422, {"msg_id": "mg.refusal.delete_needs_confirmation"})
        with STORE_LOCK, manager.lock:   # no session can be installed while a deletion validates and removes (cycle-15 MG-DELETE-015)
            if any(isinstance(x, Session) and x.state == "finalizing" for x in manager.sessions.values()):
                raise HTTPException(409, {"msg_id": "mg.refusal.session_in_progress"})   # a session being written must not race a deletion
            targets = sorted(p.name for p in st.runs_dir.glob("session_*")) if req.all else list(dict.fromkeys(req.ids or []))
            if len(targets) != req.expected_count:
                raise HTTPException(409, {"msg_id": "mg.refusal.delete_count_changed", "now": len(targets)})
            results = []
            for sid in targets:
                if not isinstance(sid, str) or not SESS_RE.fullmatch(sid):
                    results.append({"session_id": sid, "status": "refused", "reason": "not_a_session_id"})
                    continue
                d = st.runs_dir / sid
                try:
                    pinned = pin(d)   # taken BEFORE any inspection: everything validated below is proven to belong to this inode by the post-rename comparison (cycle-16 MG-DELETE-016)
                except OSError:
                    pinned = None
                why = deletable(d)
                if why:
                    results.append({"session_id": sid, "status": "missing" if why == "missing" else "refused", "reason": why})
                    continue
                files, size = tree_size(d)
                aside = st.runs_dir / f"_tmp_{uuid.uuid4().hex[:8]}"
                try:
                    with artifact_lock(sid):       # no zone edit or install is in flight on this directory while it is renamed aside
                        os.rename(d, aside)        # atomic: from here the session no longer exists under its id
                except OSError as e:
                    results.append({"session_id": sid, "status": "refused", "reason": f"rename_failed:{type(e).__name__}"})
                    continue
                # the renamed-aside entry must be exactly the inode that was validated, and still a valid session tree; otherwise it is put back and nothing is removed
                try:
                    changed = pin(aside) != pinned or structure_problem(aside) is not None
                except OSError:
                    changed = True
                if changed:
                    try:
                        if not d.exists():
                            os.rename(aside, d)
                    except OSError:
                        pass
                    results.append({"session_id": sid, "status": "refused", "reason": "identity_changed_during_deletion" if not aside.exists() else "identity_changed_residue_in_" + aside.name})
                    continue
                shutil.rmtree(aside, ignore_errors=True)
                gone = not d.exists() and not aside.exists()
                st.trusted.pop(sid, None)
                results.append({"session_id": sid, "status": "deleted" if gone else "failed", "files": files, "bytes": size,
                                **({} if gone else {"reason": "residue_remains_in_" + aside.name})})
        return {"requested": len(targets), "deleted": sum(r["status"] == "deleted" for r in results), "results": results}

    @app.post("/api/sessions/{sid}/media/delete")
    async def delete_media(sid: str, request: Request):
        refuse_if_delete_disabled()                                  # MG-DELETE-036: BEFORE the body is read, parsed or validated
        return await run_in_threadpool(delete_media_impl, sid, await parse_delete_body(request, MediaReq))

    def delete_media_impl(sid: str, req: MediaReq):
        """Privacy OFF sessions only: removes every retained frame but keeps the metadata. The whole validated frames/ directory is renamed aside in ONE atomic step, a tombstone
        describing exactly that set is written, and only then the aside copy is removed; any failure before the tombstone puts the frames back (cycle-15 MG-MEDIA-015)."""
        import time as _time
        import uuid
        if req.confirm != "ELIMINAR":
            raise HTTPException(422, {"msg_id": "mg.refusal.delete_needs_confirmation"})
        d = session_dir_or_404(sid)
        with STORE_LOCK, manager.lock:
            if any(isinstance(x, Session) and x.state == "finalizing" for x in manager.sessions.values()):
                raise HTTPException(409, {"msg_id": "mg.refusal.session_in_progress"})
            why = deletable(d)
            if why:
                raise HTTPException(409, {"msg_id": "mg.refusal.delete_refused", "reason": why})
            fd = d / "frames"
            if retention_of(d) != "frames" or not fd.is_dir():
                raise HTTPException(409, {"msg_id": "mg.refusal.no_media_to_delete"})
            fpin = pin(fd)   # pinned BEFORE enumeration (cycle-16 MG-MEDIA-016)
            entries = sorted(fd.iterdir())
            if any(f.is_symlink() or not f.is_file() for f in entries):
                raise HTTPException(409, {"msg_id": "mg.refusal.delete_refused", "reason": "unexpected_entry_in_frames"})
            names = [f.name for f in entries]
            n, b = len(entries), sum(f.stat().st_size for f in entries)
            if n != req.expected_files:
                raise HTTPException(409, {"msg_id": "mg.refusal.delete_count_changed", "now": n})
            evidence_sha = digest(read_json_strict(d / "manifest.json").get("evidence", []))
            aside = d / f"frames_deleting_{uuid.uuid4().hex[:8]}"
            try:
                os.rename(fd, aside)
            except OSError as e:
                raise HTTPException(409, {"msg_id": "mg.refusal.delete_refused", "reason": f"rename_failed:{type(e).__name__}"})

            def put_back(reason: str):
                try:
                    if not fd.exists():
                        os.rename(aside, fd)
                except OSError:
                    pass
                raise HTTPException(409, {"msg_id": "mg.refusal.delete_refused", "reason": reason})
            # the renamed-aside directory must be exactly the one that was pinned and enumerated: same inode, same regular-file set, same byte total
            try:
                aside_files = sorted(aside.iterdir())
                same = (pin(aside) == fpin and [f.name for f in aside_files] == names and all(f.is_file() and not f.is_symlink() for f in aside_files)
                        and sum(f.stat().st_size for f in aside_files) == b)
            except OSError:
                same = False
            if not same:
                put_back("media_set_changed_during_deletion")
            try:
                _atomic_write(d / "media_deleted.json", {"deleted_utc": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()), "files": n, "bytes_reported": b, "evidence_sha256": evidence_sha})
            except Exception:
                put_back("tombstone_write_failed_nothing_removed")
            shutil.rmtree(aside, ignore_errors=True)
            st.trusted.pop(sid, None)
            residue = aside.exists()
        return {"session_id": sid, "files_deleted": 0 if residue else n, "bytes_deleted": 0 if residue else b, "retention": "media_deleted",
                **({"status": "partial", "residue": aside.name} if residue else {"status": "deleted"})}

    # ------------------------------------------------------------------ events aggregation and overview (read-only views over stored data)
    ev_cache = snapshot.EventCache()

    def consistent(d: Path, build):
        """MG-RISK-033 / MG-EVENT-033: ONE immutable snapshot of the source for a whole response. `build(snap)` derives everything (zones, evaluation, bundle, retention, trust) from it; the files on disk
        are compared with the snapshot after the last read of the response, and a response whose inputs moved is refused (409), never published. A bounded read failure or an unsafe path is a refusal too."""
        try:
            snap = snapshot.take(d)
        except OSError:
            raise HTTPException(409, {"msg_id": "mg.refusal.run_inconsistent"})
        try:
            out = build(snap)
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(409, {"msg_id": "mg.refusal.run_inconsistent"})   # a missing, malformed or unsafe dependency is «unavailable», never a stack trace or a guessed answer
        if not snapshot.matches_disk(snap, d):
            raise HTTPException(409, {"msg_id": "mg.refusal.source_changed_while_read"})
        return out

    def source_events(d: Path) -> dict:
        """Risk events of ONE stored source (run or session). MG-EVENT-032: everything is derived from ONE immutable snapshot of the dependency bytes (see snapshot.py); the cache identity is the hash of
        those same bytes. If the files on disk no longer equal the snapshot when the derivation ends, the source is refused (omitted), never cached or shown. Consumers get copies, never the cached objects."""
        snap = snapshot.take(d)
        key = snap.key
        hit = ev_cache.get(d.name, key)
        if hit is not None:
            if not snapshot.matches_disk(snap, d):
                raise ValueError("source_changed_while_read")   # MG-EVENT-033: a cache hit obeys the same publication rule as a miss
            return hit
        obs = snap.doc("observations.json")
        doc = evaluate_dir(d, snap=snap)
        events = [{"event_id": e["event_id"], "zone_id": z, "severity": e["severity"], "start_us": e["start_us"], "end_us": e["end_us"], "closed_reason": e["closed_reason"],
                   "rule_id": e["rule_id"], "left_censored": e["left_censored"], "right_censored": e["right_censored"]} for z, r in doc["zones"].items() for e in r["events"]]
        covered = {state: sum(i["end_us"] - i["start_us"] for r in doc["zones"].values() for i in r["timeline"] if i["state"] == state) for state in ("GREEN", "YELLOW", "RED", "UNKNOWN")}
        out = {"source_id": d.name, "kind": "session" if d.name.startswith("session_") else "run", "origin": obs["origin"], "created_utc": obs["meta"].get("created_utc"),
               "synthetic": obs["origin"] != "model_inference", "events": events, "state_us": covered}
        if not snapshot.matches_disk(snap, d):
            raise ValueError("source_changed_while_read")
        ev_cache.put(d.name, key, out)
        return out

    def nature_of(origin: str, trust: str) -> str:
        """'real_inference' (model output whose trust grant is intact: created by this process or deep-verified) | 'unverified_claim' (stored data that claims model inference but has no
        grant) | 'synthetic' (scripted scenario) | 'human_annotated' (boxes drawn by a person). The four are never merged (cycle-17 MG-ORIGIN-017)."""
        if origin == "synthetic_scripted":
            return "synthetic"
        if origin == "human_annotated":
            return "human_annotated"
        return "real_inference" if trust in ("session", "verified") else "unverified_claim"

    def all_sources() -> list[Path]:
        import stat as _st
        out = []
        for pat in ("session_*", "run_*"):
            for p in sorted(st.runs_dir.glob(pat)):
                try:
                    if _st.S_ISDIR(os.lstat(p).st_mode) and (p / "export.json").is_file():
                        out.append(p)   # lstat: a symlink or dangling link is never followed, and never breaks a listing (cycle-15 MG-LIST-015)
                except OSError:
                    continue
        return out

    @app.get("/api/events")
    def events(severity: str | None = None, source: str | None = None, include_synthetic: bool = True, include_annotated: bool = True, limit: int = 500):
        if severity not in (None, "YELLOW", "RED") or limit < 1 or limit > 5000:
            raise HTTPException(422, {"msg_id": "mg.refusal.bad_filter"})
        rows = []
        for d in all_sources():
            if source and d.name != source:
                continue
            try:
                se = source_events(d)
            except (OSError, ValueError, KeyError, TypeError, HTTPException):
                continue   # an unreadable source never breaks the listing; it is simply absent from it
            if (se["origin"] == "synthetic_scripted" and not include_synthetic) or (se["origin"] == "human_annotated" and not include_annotated):
                continue
            trust = trust_for("sessions" if se["kind"] == "session" else "runs", d)   # computed on every call, never cached: a revoked grant must show at once
            for e in se["events"]:
                if severity and e["severity"] != severity:
                    continue
                rows.append({**e, "source_id": se["source_id"], "kind": se["kind"], "origin": se["origin"], "synthetic": se["origin"] == "synthetic_scripted", "trust": trust, "nature": nature_of(se["origin"], trust), "source_created_utc": se["created_utc"]})
        rows.sort(key=lambda r: (r["source_created_utc"] or "", r["start_us"]), reverse=True)
        return {"events": rows[:limit], "total": len(rows), "note": "real and synthetic events are labelled; counts never mix them silently"}

    @app.get("/api/overview")
    def overview():
        model_entry = load_manifest_entry()
        srcs = []
        for d in all_sources():
            try:
                se = source_events(d)
                srcs.append({**se, "nature": nature_of(se["origin"], trust_for("sessions" if se["kind"] == "session" else "runs", d))})
            except (OSError, ValueError, KeyError, TypeError, HTTPException):
                pass
        act = manager._active()

        def sev(nature, k):
            return sum(1 for x in srcs if x["nature"] == nature for e in x["events"] if e["severity"] == k)

        def cnt(kind):   # only sources whose trust grant is intact count as real; stored claims without a grant are counted separately (cycle-16 MG-TRUST-016)
            return sum(1 for x in srcs if x["kind"] == kind and x["nature"] == "real_inference")
        return {"privacy_default": "on", "active_session": (act.view() if act is not None and isinstance(act, Session) else None),
                "sources": {"runs": cnt("run"), "sessions": cnt("session"), "simulations": sum(x["nature"] == "synthetic" for x in srcs), "annotated": sum(x["nature"] == "human_annotated" for x in srcs),
                            "unverified": sum(x["nature"] == "unverified_claim" for x in srcs)},
                "events": {"real": {"RED": sev("real_inference", "RED"), "YELLOW": sev("real_inference", "YELLOW")},
                           "synthetic": {"RED": sev("synthetic", "RED"), "YELLOW": sev("synthetic", "YELLOW")},
                           "human_annotated": {"RED": sev("human_annotated", "RED"), "YELLOW": sev("human_annotated", "YELLOW")},
                           "unverified": {"RED": sev("unverified_claim", "RED"), "YELLOW": sev("unverified_claim", "YELLOW")}},
                "engine": {"risk_engine": ENGINE_VERSION, "semantics_version": SEMANTICS_VERSION, "app_version": __version__, "tracker": f"{TRACKER_ID} {TRACKER_VERSION}"},
                "model": {k: model_entry[k] for k in ("model_id", "architecture", "sha256")},
                "warning": "mg.lim.not_safety_system"}

    # ------------------------------------------------------------------ stored sessions
    @app.get("/api/sessions")
    def sessions():
        out = []
        rows = []
        for d in st.runs_dir.glob("session_*"):
            try:
                ls = os.lstat(d)   # never follows a symlink; a dangling or odd entry is skipped, never fatal (cycle-15 MG-LIST-015)
            except OSError:
                continue
            import stat as _st
            if _st.S_ISDIR(ls.st_mode):
                rows.append((ls.st_mtime, d))
        for _, d in sorted(rows, key=lambda r: r[0], reverse=True):
            try:
                o, m = read_json_strict(d / "observations.json"), read_json_strict(d / "manifest.json")
                out.append({"session_id": d.name, "created_utc": o["meta"]["created_utc"], "frames_analyzed": len(o["frames"]), "frame_count": o["source_binding"]["frame_count"],
                            "closed_reason": m.get("closed_reason"), "trust": session_trust(d), "retention": retention_of(d),
                            "source_origin": read_json_strict(d / "provenance.json").get("capture", {}).get("origin"), "bytes": tree_size(d)[1]})
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                continue   # a malformed record fails closed for ITS row only
        return out

    @app.get("/api/sessions/{sid}")
    def session_bundle(sid: str):
        d = session_dir_or_404(sid)

        def build(snap):
            bundle = copy.deepcopy(snap.doc("export.json"))                  # bundle, retention and trust of ONE revision (MG-RISK-033 boundary)
            bundle["trust"] = session_trust(d)
            bundle["retention"] = retention_of(d, snap)
            bundle["evidence_available"] = bundle["retention"] == "frames"   # manifest.evidence of a media-deleted session is HISTORICAL: no such file exists any more
            return bundle
        bundle = consistent(d, build)
        try:   # a late loss declaration is a sidecar (never digest-bound): shown next to the evidence, clearly labelled, never merged into it
            lf = d / LATE_FILE
            bundle["late_declaration"] = read_json_strict(lf) if lf.is_file() and not lf.is_symlink() else None
        except (OSError, ValueError):
            bundle["late_declaration"] = {"unreadable": True}
        ft = bundle["source"]["frame_table"]
        bundle["source"]["frame_table"] = {k: v for k, v in ft.items() if k != "pts_ticks"} | {
            "pts_us": [pts_us(ft["pts_ticks"], i, ft["time_base"]["num"], ft["time_base"]["den"]) for i in range(len(ft["pts_ticks"]))]}
        return bundle

    @app.get("/api/sessions/{sid}/frames/{index}")
    def session_frame(sid: str, index: int):
        d = session_dir_or_404(sid)
        if retention_of(d) != "frames":
            raise HTTPException(410, {"msg_id": "mg.refusal.no_frames_retained"})   # Privacy ON, or the retained media were deleted by the user
        p = safe_evidence_path(d, f"frames/{index:06d}.jpg", index) if index >= 0 else None
        if p is None:
            raise HTTPException(404, {"msg_id": "mg.refusal.frame_not_analyzed"})
        manifest = read_json_strict(d / "manifest.json")
        expected = next((e["file_sha256"] for e in manifest["evidence"] if e["frame_index"] == index), None)
        if expected is None or file_sha256(p) != expected:
            st.trusted.pop(d.name, None)   # a detected mismatch revokes at once; restoring the bytes later does not bring the grant back (cycle-17 MG-TRUST-017)
            raise HTTPException(409, {"msg_id": "mg.refusal.evidence_mismatch"})
        return FileResponse(p, media_type="image/jpeg")

    @app.get("/api/sessions/{sid}/verify")
    def session_verify_route(sid: str, deep: bool = True):
        d = session_dir_or_404(sid)
        if not deep or retention_of(d) != "frames":
            # shallow request, or a session WITHOUT images (Privacy ON / media deleted): metadata integrity only. The pixels are gone, so a deep visual verification can neither
            # run nor be claimed, and no "verified" grant is ever given; a failed integrity check still removes an existing grant.
            rep = verify_session(d, None, deep=False if not deep else True)
            if not rep["ok"]:
                st.trusted.pop(d.name, None)   # any failed verification, of any depth or retention mode, removes the grant for good (cycle-15 MG-TRUST-015)
            return rep
        if not st.infer_lock.acquire(blocking=False):
            raise HTTPException(409, {"msg_id": "mg.refusal.job_in_progress"})
        try:
            if st.detector is None:
                try:
                    st.detector = st.detector_factory()
                except Exception:
                    st.detector = None
            before = _safe_fp(d)
            rep = verify_session(d, st.detector, deep=True)
        finally:
            st.infer_lock.release()
        if rep["ok"] and before is not None and _safe_fp(d) == before:
            st.trusted[d.name] = ("verified", before)
        elif rep["ok"]:
            rep = {**rep, "ok": False, "trust_scope": trust_scope(False, True)}
        if not rep["ok"]:
            st.trusted.pop(d.name, None)  # any failed deep verification removes the grant; bytes restored later never resurrect it (cycle-2 MG-TRUST-010)
        return rep

    def _safe_fp(d):
        try:
            return session_fingerprint(d)
        except (OSError, ValueError, KeyError, TypeError):
            return None

    # ------------------------------------------------------------------ zones / risk / export (runs and sessions)
    for kind, pat in (("runs", "run_id"), ("sessions", "session_id")):
        def mk(kind=kind):
            def get_zones(rid: str):
                d = resolver(kind, rid)
                return consistent(d, lambda snap: load_zones(d, snap=snap))

            def put_zones(rid: str, req: ZonesReq):
                d = resolver(kind, rid)
                try:
                    obs = snapshot.take(d).doc("observations.json")      # the frame size comes from one bounded read of the stored observations
                except OSError:
                    raise HTTPException(409, {"msg_id": "mg.refusal.run_inconsistent"})
                W, H = obs["source_binding"]["width"], obs["source_binding"]["height"]
                zones = []
                for z in req.zones:
                    try:
                        zones.append(make_zone(z.zone_id, z.kind, z.polygon, W, H, z.name))
                    except InvalidZone as e:
                        raise HTTPException(422, {"msg_id": e.args[0], "zone_id": z.zone_id})
                if len({z["zone_id"] for z in zones}) != len(zones):
                    raise HTTPException(422, {"msg_id": "mg.refusal.zone_set_invalid"})
                with zone_lock(d.name):   # read-check-write of ONE artifact is serialized: two editors cannot both succeed from the same revision
                    rdir = d / "risk"
                    if rdir.is_symlink():
                        raise HTTPException(409, {"msg_id": "mg.refusal.run_inconsistent"})
                    rdir.mkdir(exist_ok=True)
                    try:
                        old = load_zones(d)
                        if old["revision"] != req.expected_revision:
                            raise HTTPException(409, {"msg_id": "mg.refusal.zones_revision_conflict", "current": old})   # the caller reconciles against the current set
                    except HTTPException as e:
                        if e.detail.get("msg_id") == "mg.refusal.zones_revision_conflict":
                            raise
                        # a corrupted zone file may always be REPLACED by a valid set (the next revision is taken from whatever integer it still holds)
                        try:
                            raw = load_zones(d, strict=False)
                            old = {"revision": raw["revision"] if isinstance(raw, dict) and isinstance(raw.get("revision"), int) and not isinstance(raw.get("revision"), bool) else 0}
                        except HTTPException:
                            old = {"revision": 0}
                    doc = {"revision": old["revision"] + 1, "zones": zones}
                    _atomic_write(rdir / "zones.json", doc)
                return doc

            def risk(rid: str):
                d = resolver(kind, rid)

                def build(snap):
                    zdoc = load_zones(d, snap=snap)                  # one snapshot, used for the evaluation AND returned
                    return {"evaluation": evaluate_dir(d, zdoc, snap), "trust": trust_for(kind, d), "zones": zdoc}
                return consistent(d, build)

            def risk_export(rid: str):
                d = resolver(kind, rid)

                def build(snap):
                    zdoc = load_zones(d, snap=snap)
                    doc = evaluate_dir(d, zdoc, snap)
                    bundle = copy.deepcopy(snap.doc("export.json"))          # the export bundle is the SAME revision the evaluation was derived from
                    if kind == "sessions":
                        bundle["retention"] = retention_of(d, snap)
                        bundle["evidence_available"] = bundle["retention"] == "frames"   # manifest.evidence of a media-deleted session is historical (MG-EVIDENCE-016)
                    return {"schema": "mg.export.vs02", "kind": "session" if kind == "sessions" else "run", "bundle": bundle, "zones": zdoc, "evaluation": doc,
                            "engine": {"version": ENGINE_VERSION, "semantics_version": SEMANTICS_VERSION, "app_version": __version__},
                            "trust_at_export": trust_for(kind, d), "trust_note": "local integrity state at export time; not proof of authenticity to third parties"}
                out = consistent(d, build)
                return Response(json.dumps(out, indent=1, ensure_ascii=False), media_type="application/json",
                                headers={"content-disposition": f'attachment; filename="{rid}.mineguard-risk-export.json"'})
            return get_zones, put_zones, risk, risk_export
        g, p_, r_, x_ = mk()
        app.get(f"/api/{kind}/{{rid}}/zones")(g)
        app.put(f"/api/{kind}/{{rid}}/zones")(p_)
        app.get(f"/api/{kind}/{{rid}}/risk")(r_)
        app.get(f"/api/{kind}/{{rid}}/risk/export")(x_)
