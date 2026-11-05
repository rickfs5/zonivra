"""FastAPI application for VS-01. Local only; serves the built React UI when frontend/dist exists."""
from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.exceptions import RequestValidationError
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, buildinfo, decode, netguard
from .security import COOKIE_NAME, BodyLimit, LocalSecurity, new_token
from .analysis import RUNS_DIR, Cancelled, run_analysis
from .canon import NotNfcError, digest16, file_sha256, loads_strict, read_json_strict
from .detector import Detector, WeightsError, load_manifest_entry, verify_weights
from .schema import ValidationFailure, obsset_digest, validate_observation_set
from .verify import bundle_consistent, safe_evidence_path, trust_scope, verify_run
from .alerts import AlertEngine
from .api_mvp2 import register_v2
from .api_external import register_external
from .api_scenarios import register_scenarios
from .api_vs02 import register_vs02
from . import providers as providers_mod
from .feeds import FeedManager
from .webcam import SessionManager

netguard.install()

ROOT = Path(__file__).resolve().parents[2]
ASSET_DIR = ROOT / "test-assets"
DIST = ROOT / "frontend" / "dist"
RUN_RE = re.compile(r"^run_[0-9a-f]{16}$")


class AnalyzeRequest(BaseModel):
    stride: int = Field(3, ge=1, le=30)
    threshold: float = Field(0.30, gt=0.0, lt=1.0)


class _State:
    def __init__(self, asset_dir: Path, runs_dir: Path):
        self.asset_dir, self.runs_dir = asset_dir, runs_dir
        self.videos: dict[str, dict] = {}
        self.jobs: dict[str, dict] = {}
        self.detector: Detector | None = None
        self.weights_ok: dict | None = None
        self.lock = threading.Lock()
        self.infer_lock = threading.Lock()  # one model user at a time: analysis jobs and deep verification
        self.trusted: dict[str, tuple[str, dict]] = {}  # run_id -> (kind, fingerprint of observations/export/source/evidence); in-memory only
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.session_mgr = None
        self.detector_factory = None

    def scan(self):
        for p in sorted(self.asset_dir.glob("*.mp4")) if self.asset_dir.is_dir() else []:
            digest_hex = file_sha256(p)
            vid = "vid_" + digest16(digest_hex)
            if vid not in self.videos:
                try:
                    info = decode.probe(str(p))
                except decode.UnsupportedMedia as e:
                    self.videos[vid] = {"video_id": vid, "filename": p.name, "path": str(p), "sha256": digest_hex, "error": str(e)}
                    continue
                self.videos[vid] = {"video_id": vid, "filename": p.name, "path": str(p), "sha256": digest_hex,
                                    "bytes": p.stat().st_size, "media": info.__dict__}


def _evidence_path(d: Path, entry: dict) -> Path:
    p = safe_evidence_path(d, entry["file"], entry["frame_index"])
    if p is None:
        raise ValueError("non-canonical evidence path")
    return p


def _write_token_file(path: Path, token: str):
    """Per-process session token for local scripts (same-user, mode 0600, rewritten on every start)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    os.chmod(path, 0o600)


def create_app(asset_dir: Path = ASSET_DIR, runs_dir: Path = RUNS_DIR, detector_factory=Detector, dist_dir: Path = DIST,
               session_token: str | None = None, token_file: Path | None = None) -> FastAPI:
    buildinfo.started_commit()        # the build identity is captured when the process starts
    app = FastAPI(title="MineGuard VS-01", version=__version__)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request, exc: RequestValidationError):
        """422 WITHOUT echoing the rejected input: the default handler repeats it in the response, and input that cannot be encoded (a lone surrogate in a JSON string) turned a plain
        validation failure into a server error (cycle-19 MG-ZONE-020)."""
        return JSONResponse(status_code=422, content={"detail": [{"type": e.get("type"), "loc": [str(x) for x in e.get("loc", ())], "msg": e.get("msg")} for e in exc.errors()]})
    st = _State(asset_dir, runs_dir)
    st.detector_factory = detector_factory
    app.state.st = st
    token = session_token or new_token()
    app.state.session_token = token
    if token_file is not None:
        _write_token_file(token_file, token)
    app.add_middleware(BodyLimit, per_path={"/api/observations/import": 32 * 1024 * 1024, "/api/replays": 8_000_000, "/api/external-results": 12 * 1024 * 1024})               # added first = inner: the security checks still run before a body is looked at
    app.add_middleware(LocalSecurity, token=token)
    runs_dir.mkdir(parents=True, exist_ok=True)
    st.scan()

    def video_or_404(video_id: str) -> dict:
        v = st.videos.get(video_id)
        if not v or "error" in v:
            raise HTTPException(404, {"msg_id": "mg.refusal.unknown_video"})
        return v

    def run_dir_or_404(run_id: str) -> Path:
        d = st.runs_dir / run_id
        if not RUN_RE.match(run_id) or d.is_symlink() or not (d / "export.json").is_file():  # a symlinked run directory is never served (MG-SEC-003)
            raise HTTPException(404, {"msg_id": "mg.refusal.unknown_run"})
        return d

    def _require_consistent(d: Path):
        try:
            consistent = bundle_consistent(d)
        except (OSError, ValueError, KeyError, TypeError):  # includes duplicate keys and malformed JSON
            consistent = False
        if not consistent:
            raise HTTPException(409, {"msg_id": "mg.refusal.run_inconsistent"})

    def fingerprint(d: Path) -> dict:
        """Everything a trust claim depends on, re-read from disk: observations, export bundle, the registered source file
        (re-hashed, not stat-cached) and every evidence file named in the manifest (re-hashed)."""
        obs = read_json_strict(d / "observations.json")
        manifest = read_json_strict(d / "manifest.json")
        video = video_for(obs)
        if video is None:
            raise FileNotFoundError("source video not registered")
        return {"obsset": obsset_digest(obs), "export": file_sha256(d / "export.json"), "consistent": bundle_consistent(d),
                "source": file_sha256(video["path"]),
                "evidence": {e["file"]: file_sha256(_evidence_path(d, e)) for e in manifest["evidence"]}}

    def trust_of(d: Path) -> str:
        """'session' = created by this process; 'verified' = passed deep verification in this process; else 'unverified'.
        A run found on disk is never presented as real inference until it passes deep verification (decode + re-inference).
        The grant is bound to the exact bytes of the source MP4 and of every evidence image: any change, replacement or removal
        drops it."""
        t = st.trusted.get(d.name)
        if not t:
            return "unverified"
        try:
            fp = fingerprint(d)
            ok = fp == t[1] and fp["consistent"]
        except (OSError, ValueError, KeyError, TypeError):
            ok = False
        if not ok:
            st.trusted.pop(d.name, None)  # monotonic revocation (cycle-5 MG-TRUST-014): a mismatch seen by ANY read removes the grant; restoring the bytes later does not bring it back
            return "unverified"
        return t[0]

    def mark_trusted(d: Path, kind: str, fp: dict | None = None):
        fp = fp or fingerprint(d)
        st.trusted[d.name] = (kind, fp)

    def video_for(obs: dict):
        return next((v for v in st.videos.values() if v["sha256"] == obs["source_binding"]["source_digest"]), None)

    def deep_verify(d: Path) -> dict:
        rep = _deep_verify(d)
        if not rep["ok"]:
            st.trusted.pop(d.name, None)  # every failed deep verification, including early refusals, removes the grant (cycle-2 MG-TRUST-010)
        return rep

    def _deep_verify(d: Path) -> dict:
        """Decode again and re-run the model on every analyzed frame; fails closed when the source or the model is unavailable."""
        try:
            obs = read_json_strict(d / "observations.json")
            video = video_for(obs)
        except (OSError, ValueError, KeyError, TypeError) as e:
            return {"run_id": d.name, "ok": False, "deep": True, "checks": [
                {"id": "run_files_readable_and_wellformed", "ok": False, "detail": f"{type(e).__name__}: {e}"}], "trust_scope": trust_scope(False, True)}
        if not st.infer_lock.acquire(blocking=False):
            raise HTTPException(409, {"msg_id": "mg.refusal.job_in_progress"})
        try:
            if st.detector is None:
                try:
                    st.detector = detector_factory()
                except Exception:
                    st.detector = None
            try:
                before = fingerprint(d)
            except (OSError, ValueError, KeyError, TypeError):
                before = None
            report = verify_run(d, video["path"] if video else None, deep=True, detector=st.detector)
        finally:
            st.infer_lock.release()
        if report["ok"] and before is not None:
            try:
                after = fingerprint(d)
            except (OSError, ValueError, KeyError, TypeError):
                after = None
            if after == before:  # nothing changed while verification was running
                mark_trusted(d, "verified", after)
            else:
                report = {**report, "ok": False, "trust_scope": trust_scope(False, True), "checks": report["checks"] + [
                    {"id": "inputs_unchanged_during_verification", "ok": False, "detail": "source or run files changed during verification"}]}
        elif report["ok"]:
            report = {**report, "ok": False, "trust_scope": trust_scope(False, True), "checks": report["checks"] + [
                {"id": "inputs_unchanged_during_verification", "ok": False, "detail": "fingerprint unavailable"}]}
        if not report["ok"]:
            # MG-ORIGIN-003 / cycle-2 MG-TRUST-010: ANY failed deep verification removes the grant, whatever the current bytes are. Restoring the original bytes later
            # never resurrects "session" or "verified": only a later SUCCESSFUL deep verification grants trust again.
            st.trusted.pop(d.name, None)
        return report

    @app.get("/api/health")
    def health():
        return {"status": "ok", "version": __version__, "network_guard": netguard.is_installed()}

    @app.get("/api/build")
    def build():
        return buildinfo.build_info()

    @app.get("/api/model")
    def model():
        entry = load_manifest_entry()
        if st.weights_ok is None:
            try:
                verify_weights(entry)
                st.weights_ok = {"verified": True}
            except WeightsError as e:
                st.weights_ok = {"verified": False, "error": str(e)}
        return {"model_id": entry["model_id"], "architecture": entry["architecture"], "weights_sha256": entry["sha256"],
                "source_url": entry["source_url"], "license_evidence": entry["license_evidence"], **st.weights_ok}

    @app.get("/api/providers")
    def providers():
        return providers_mod.status(model())

    @app.get("/api/videos")
    def videos():
        return [{k: v for k, v in x.items() if k != "path"} for x in st.videos.values()]

    @app.get("/api/videos/{video_id}/file")
    def video_file(video_id: str):
        v = video_or_404(video_id)
        try:
            if file_sha256(v["path"]) != v["sha256"]:  # never serve media that is not the registered, hashed file
                raise HTTPException(409, {"msg_id": "mg.refusal.source_digest_mismatch"})
        except OSError:
            raise HTTPException(404, {"msg_id": "mg.refusal.unknown_video"})
        return FileResponse(v["path"], media_type="video/mp4", filename=v["filename"], content_disposition_type="inline")

    @app.post("/api/videos/{video_id}/analyze", status_code=202)
    def analyze(video_id: str, req: AnalyzeRequest):
        v = video_or_404(video_id)
        if st.session_mgr._active() is not None:  # one inference-heavy process at a time: a webcam session owns the detector
            raise HTTPException(409, {"msg_id": "mg.refusal.session_in_progress"})
        with st.lock:
            if any(j["status"] in ("queued", "running") for j in st.jobs.values()):
                raise HTTPException(409, {"msg_id": "mg.refusal.job_in_progress"})
            job_id = "job_" + uuid.uuid4().hex[:12]
            job = {"job_id": job_id, "status": "queued", "done": 0, "total": 0, "run_id": None, "error": None,
                   "cancel": False, "video_id": video_id, "stride": req.stride, "threshold": req.threshold}
            st.jobs[job_id] = job

        def work():
            job["status"] = "running"
            try:
                st.infer_lock.acquire()
                if st.detector is None:
                    st.detector = detector_factory()
                if file_sha256(v["path"]) != v["sha256"]:
                    raise RuntimeError("mg.refusal.source_digest_mismatch")

                def prog(done, total):
                    job["done"], job["total"] = done, total

                def post_check():  # runs BEFORE anything is installed: a changed source can never delete or replace an existing run
                    if file_sha256(v["path"]) != v["sha256"]:
                        raise RuntimeError("mg.refusal.source_digest_mismatch")

                res = run_analysis(v["path"], v["sha256"], st.detector, req.stride, req.threshold, prog,
                                   lambda: job["cancel"], st.runs_dir, post_check)
                mark_trusted(st.runs_dir / res["run_id"], "session")
                job.update(status="done", run_id=res["run_id"], result=res)
            except Cancelled:
                job["status"] = "cancelled"
            except Exception as e:  # reported verbatim to the client; never silently replaced by synthetic output
                job.update(status="failed", error=f"{type(e).__name__}: {e}")
            finally:
                if st.infer_lock.locked():
                    st.infer_lock.release()

        st.pool.submit(work)
        return {"job_id": job_id}

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        j = st.jobs.get(job_id)
        if not j:
            raise HTTPException(404, {"msg_id": "mg.refusal.unknown_job"})
        return {k: v for k, v in j.items() if k != "cancel"}

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(job_id: str):
        j = st.jobs.get(job_id)
        if not j:
            raise HTTPException(404, {"msg_id": "mg.refusal.unknown_job"})
        j["cancel"] = True
        return {"ok": True}

    @app.get("/api/runs")
    def runs():
        import stat as _st
        out = []
        cand = []
        for d in st.runs_dir.glob("run_*"):
            try:
                ls = os.lstat(d)   # never follows a symlink; a dangling link never breaks the listing (cycle-16 MG-LIST-016)
            except OSError:
                continue
            if _st.S_ISDIR(ls.st_mode):
                cand.append((ls.st_mtime, d))
        for d in [c[1] for c in sorted(cand, key=lambda c: c[0], reverse=True)]:
            if (d / "manifest.json").is_file():
                try:
                    o = read_json_strict(d / "observations.json")
                except (OSError, ValueError):
                    continue  # unreadable or ambiguous (duplicate keys) run directories are not listed
                try:
                    out.append({"run_id": d.name, "source_digest": o["source_binding"]["source_digest"], "created_utc": o["meta"]["created_utc"],
                                "frames_analyzed": len(o["frames"]), "params": o["producer"]["params"], "trust": trust_of(d), "origin": o["origin"]})
                except (KeyError, TypeError, AttributeError):
                    continue   # a malformed record fails closed for its own row only
        return out

    @app.get("/api/runs/{run_id}")
    def run_bundle(run_id: str):
        d = run_dir_or_404(run_id)
        _require_consistent(d)
        bundle = read_json_strict(d / "export.json")
        bundle["trust"] = trust_of(d)
        bundle["source"]["frame_table"] = {k: v for k, v in bundle["source"]["frame_table"].items() if k != "pts_ticks"} | {
            "pts_us": _pts_us_list(bundle["source"]["frame_table"])}
        return bundle

    @app.get("/api/runs/{run_id}/export")
    def export(run_id: str):
        d = run_dir_or_404(run_id)
        _require_consistent(d)
        return FileResponse(d / "export.json", media_type="application/json", filename=f"{run_id}.mineguard-export.json")

    @app.get("/api/runs/{run_id}/frames/{index}")
    def frame(run_id: str, index: int):
        d = run_dir_or_404(run_id)
        p = safe_evidence_path(d, f"frames/{index:06d}.jpg", index) if index >= 0 else None  # regular file directly in a real <run>/frames (MG-SEC-003)
        if p is None:
            raise HTTPException(404, {"msg_id": "mg.refusal.frame_not_analyzed"})
        try:
            manifest = read_json_strict(d / "manifest.json")
            expected = next(e["file_sha256"] for e in manifest["evidence"] if e["file"] == f"frames/{index:06d}.jpg")
            t = st.trusted.get(d.name)
            if file_sha256(p) != expected or (t and file_sha256(p) != t[1]["evidence"].get(f"frames/{index:06d}.jpg")):
                st.trusted.pop(d.name, None)   # a detected mismatch revokes at once (cycle-17 MG-TRUST-017)
                raise HTTPException(409, {"msg_id": "mg.refusal.evidence_mismatch"})
        except (OSError, ValueError, KeyError, StopIteration):
            st.trusted.pop(d.name, None)
            raise HTTPException(409, {"msg_id": "mg.refusal.evidence_mismatch"})
        return FileResponse(p, media_type="image/jpeg")

    @app.get("/api/runs/{run_id}/verify")
    def verify(run_id: str, deep: bool = True):
        d = run_dir_or_404(run_id)
        if deep:
            return deep_verify(d)
        rep = verify_run(d, None, deep=False)
        if not rep["ok"]:
            st.trusted.pop(d.name, None)   # ANY failed verification, of any depth, removes the grant for good (cycle-15 MG-TRUST-015)
        return rep

    @app.post("/api/observations/import")
    async def import_observations(request: Request):
        """Replay path. Observation sets are never promoted to 'inference' by import. The raw body is parsed strictly
        (duplicate keys at any depth are rejected) before any framework deserialization can collapse them."""
        try:
            obs = loads_strict(await request.body())
        except ValueError as e:  # V-25 duplicate key / unparsable; V-17 canonicalization (non-NFC member name): rejected as received
            code = "V-17" if isinstance(e, NotNfcError) else "V-25"
            return JSONResponse(status_code=422, content={"msg_id": "mg.refusal.invalid_observation_set", "codes": [code], "detail": type(e).__name__})
        if not isinstance(obs, dict):
            return JSONResponse(status_code=422, content={"msg_id": "mg.refusal.invalid_observation_set", "codes": ["V-20"]})
        try:
            model = validate_observation_set(obs)
        except ValidationFailure as e:
            return JSONResponse(status_code=422, content={"msg_id": "mg.refusal.invalid_observation_set", "codes": e.codes})
        if model.origin == "model_inference":
            od = obsset_digest(obs)
            run_dir = st.runs_dir / ("run_" + digest16(od))
            refusal = JSONResponse(status_code=422, content={"msg_id": "mg.refusal.replay_as_inference",
                                                             "detail": "origin=model_inference without a fully verified local inference run"})
            try:
                stored_ok = (run_dir / "observations.json").is_file() and obsset_digest(read_json_strict(run_dir / "observations.json")) == od
            except (OSError, ValueError):
                stored_ok = False
            if not stored_ok:
                return refusal
            if not deep_verify(run_dir)["ok"]:  # fail closed: needs the source video and the model to re-run inference
                return refusal
            return {"accepted": True, "mode": "inference_verified_local_run", "run_id": run_dir.name}
        banner = {"synthetic_scripted": "mg.banner.synthetic_replay", "human_annotated": "mg.banner.human_annotated"}[model.origin]
        return {"accepted": True, "mode": "replay", "origin": model.origin, "banner_msg_id": banner}

    st.session_mgr = SessionManager(st, runs_dir, detector_factory)
    register_vs02(app, st, st.session_mgr, run_dir_or_404, trust_of)
    st.alerts = AlertEngine(runs_dir / "alerts")
    st.session_mgr.alerts = st.alerts
    st.feeds = FeedManager(st, runs_dir, detector_factory, st.alerts)
    register_v2(app, st, runs_dir, st.feeds, st.alerts, ROOT)
    register_scenarios(app)                          # FINAL-ENGINEERING-15-R1: read-only deterministic scenario replays (synthetic, recomputed, nothing stored)
    register_external(app, runs_dir)               # MG-EVIDENCE-10: explicit import + read-only review of an EXTERNAL provider result; touches no run, alert, feed or risk state
    app.router.on_shutdown.append(st.feeds.stop_all)

    if dist_dir.is_dir():
        @app.get("/", include_in_schema=False)
        @app.get("/index.html", include_in_schema=False)
        def index():
            """The UI shell is the only place the session token is handed out (Host/Origin/peer already validated by the middleware):
            as a meta tag for fetch calls and as an HttpOnly SameSite=Strict cookie for media, image and download requests."""
            html = (dist_dir / "index.html").read_text()
            html = html.replace("</head>", f'<meta name="mg-session-token" content="{token}" /></head>', 1)
            resp = HTMLResponse(html, headers={"cache-control": "no-store"})
            resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="strict", path="/")
            return resp

        app.mount("/", StaticFiles(directory=dist_dir, html=False), name="ui")
    return app


def _pts_us_list(frame_table: dict) -> list[int]:
    from .timebase import pts_us

    num, den = frame_table["time_base"]["num"], frame_table["time_base"]["den"]
    ticks = frame_table["pts_ticks"]
    return [pts_us(ticks, i, num, den) for i in range(len(ticks))]


# the token file is written only by a real server launch (scripts set MG_WRITE_TOKEN_FILE=1), never by importing this module in tests
app = create_app(token_file=ROOT / "logs" / "session_token" if os.environ.get("MG_WRITE_TOKEN_FILE") == "1" else None)
