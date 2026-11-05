"""HTTP surface of MG-VERTICAL-02: multi-feed monitoring, alerts, operator and developer dashboards. Loopback + token + Host/Origin checks are applied by the LocalSecurity middleware;
nothing here reaches the network. Contracts: docs/MG-VERTICAL2-001-multi-source-monitoring.md."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Annotated, Literal

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from . import safedir

from . import alerts as alert_mod
from . import explain
from . import incident as incident_mod
from . import monitor, snapshot
from .api_vs02 import ZoneIn, evaluate_dir
from .risk.kernel import ENGINE_VERSION, SEMANTICS_VERSION
from .feeds import FEED_ID_RE, FEEDRUN_RE, FEEDS_DIR, FeedError, FeedManager
from .webcam import STORE_LOCK


MAX_BODY = 65_536       # every JSON body of this module is small by construction; anything larger is refused BEFORE it is parsed
MAX_SETTINGS_BODY = 8_192
Short = Annotated[str, StringConstraints(max_length=40)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")   # unknown members are refused, never ignored


async def read_body(request: Request, limit: int = MAX_BODY) -> bytes:
    """Reads the request body with a hard cap, whether or not a Content-Length was declared (chunked uploads are counted as they arrive)."""
    cl = request.headers.get("content-length")
    if cl is not None and (not cl.isdigit() or int(cl) > limit):
        raise HTTPException(413, {"msg_id": "mg.refusal.body_too_large"})
    raw = bytearray()
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > limit:
            raise HTTPException(413, {"msg_id": "mg.refusal.body_too_large"})
    return bytes(raw)


async def body_model(request: Request, model, limit: int = MAX_BODY):
    raw = await read_body(request, limit)
    try:
        return model.model_validate_json(raw)
    except (ValidationError, ValueError):
        raise HTTPException(422, {"msg_id": "mg.refusal.invalid_request"})   # the rejected input is never echoed


class FeedCreateReq(Strict):
    video_id: str = Field(min_length=1, max_length=40)
    label: str = Field(default="", max_length=60)
    sample_fps: float = Field(default=2.0, ge=0.2, le=10.0)
    threshold: float = Field(default=0.30, gt=0.0, lt=1.0)
    mode: Literal["realtime", "throttled"] = "realtime"
    speed: float = 1.0
    loop: bool = False
    zones: list[ZoneIn] = Field(default=[], max_length=8)
    source_kind: str = "mp4_file"


class FeedZonesReq(Strict):
    zones: list[ZoneIn] = Field(max_length=8)
    expected_revision: int = Field(ge=0, le=1_000_000_000)


class CapacityReq(Strict):
    limit: int = Field(ge=1, le=10)
    acknowledge_unvalidated: bool = False


class SeenReq(Strict):
    ids: list[Short] = Field(max_length=200)


class DeliveryReq(Strict):
    channel: Literal["browser", "sound"]
    status: Literal["delivered", "denied", "failed", "unsupported"]
    detail: str | None = Field(default=None, max_length=200)


class HistoryDeleteReq(Strict):
    ids: list[Short] = Field(max_length=200)
    confirm: Short
    expected_count: int = Field(ge=0)


def register_v2(app, st, runs_dir: Path, feeds: FeedManager, alerts: alert_mod.AlertEngine, root: Path):
    def err(e: FeedError):
        raise HTTPException(e.status, {"msg_id": e.msg_id})

    def feed_or_404(fid: str):
        if not re.fullmatch(FEED_ID_RE, fid):
            raise HTTPException(404, {"msg_id": "mg.refusal.unknown_feed"})
        try:
            return feeds.get(fid)
        except FeedError as e:
            err(e)

    # ------------------------------------------------------------------ feeds
    @app.get("/api/feeds")
    def feeds_list():
        return feeds.snapshot()

    @app.post("/api/feeds", status_code=201)
    async def feeds_create(request: Request):
        req = await body_model(request, FeedCreateReq)
        try:
            f = feeds.create(req.video_id, req.label, req.sample_fps, req.threshold, req.mode, req.speed, req.loop, [z.model_dump() for z in req.zones], req.source_kind)
        except FeedError as e:
            err(e)
        return f.view()

    @app.post("/api/feeds/capacity")
    async def feeds_capacity(request: Request):
        req = await body_model(request, CapacityReq)
        try:
            return {"active_limit": feeds.set_capacity(req.limit, req.acknowledge_unvalidated)}
        except FeedError as e:
            err(e)

    @app.get("/api/feeds/history")
    def feeds_history():
        rows, status = monitor.feed_history_with_status(runs_dir, 50, feeds.granted)
        return {"runs": rows, "store_status": status, "retention": {"mode": "metadata_only", "frames_persisted": False}}

    @app.post("/api/feeds/history/delete")
    async def feeds_history_delete(request: Request):
        """Deletes saved feed summaries (metadata only: they hold no image). Same contract as session deletion: exact confirmation, exact count, validated ids, regular files only, and every
        unlink is made INSIDE the pinned owned directory (a symlinked `feeds` directory is refused). The answer reports each entry by its position; an id that is not a valid summary id is
        never echoed back."""
        req = await body_model(request, HistoryDeleteReq)
        if req.confirm != "ELIMINAR":
            raise HTTPException(422, {"msg_id": "mg.refusal.delete_confirmation"})
        ids = list(dict.fromkeys(req.ids))
        if len(ids) != req.expected_count:
            raise HTTPException(409, {"msg_id": "mg.refusal.delete_count_mismatch", "actual": len(ids)})
        results = []
        try:
            fd = safedir.open_owned_dir(runs_dir, FEEDS_DIR, create=False)
        except FileNotFoundError:
            fd = None
        except (safedir.UnsafeDir, OSError):
            raise HTTPException(409, {"msg_id": "mg.refusal.store_not_owned"})
        try:
            for n, i in enumerate(ids):
                if not re.fullmatch(FEEDRUN_RE, i):
                    results.append({"index": n, "id": None, "status": "refused_bad_id"})
                    continue
                if fd is None:
                    results.append({"index": n, "id": i, "status": "missing"})
                    continue
                try:
                    with STORE_LOCK:
                        safedir.unlink_regular(fd, f"{i}.json")
                    results.append({"index": n, "id": i, "status": "deleted"})
                except FileNotFoundError:
                    results.append({"index": n, "id": i, "status": "missing"})
                except safedir.UnsafeDir:
                    results.append({"index": n, "id": i, "status": "refused_not_a_regular_file"})
                except OSError:
                    results.append({"index": n, "id": i, "status": "failed"})
        finally:
            if fd is not None:
                os.close(fd)
        return {"results": results, "deleted": sum(1 for r in results if r["status"] == "deleted")}

    @app.get("/api/feeds/{fid}")
    def feeds_get(fid: str):
        return feed_or_404(fid).view()

    @app.get("/api/feeds/{fid}/lifecycle")
    def feeds_lifecycle(fid: str):
        """V07: the lifecycle this process recorded for the CURRENT run of one source. An idle or never-started feed has none: `recorded` is false and nothing is reconstructed."""
        doc = feed_or_404(fid).life_doc()
        return {**doc, "recorded": bool(doc["events"]), "reason": None if doc["events"] else "mg.inc.life.not_recorded"}

    def op(fn, fid: str):
        feed_or_404(fid)
        try:
            return fn(fid).view()
        except FeedError as e:
            err(e)

    @app.post("/api/feeds/{fid}/start")
    def feeds_start(fid: str):
        return op(feeds.start, fid)

    @app.post("/api/feeds/{fid}/pause")
    def feeds_pause(fid: str):
        return op(feeds.pause, fid)

    @app.post("/api/feeds/{fid}/resume")
    def feeds_resume(fid: str):
        return op(feeds.resume, fid)

    @app.post("/api/feeds/{fid}/stop")
    def feeds_stop(fid: str):
        return op(feeds.stop, fid)

    @app.delete("/api/feeds/{fid}")
    def feeds_remove(fid: str):
        feed_or_404(fid)
        try:
            feeds.remove(fid)
        except FeedError as e:
            err(e)
        return {"removed": fid}

    @app.put("/api/feeds/{fid}/zones")
    async def feeds_zones(fid: str, request: Request):
        req = await body_model(request, FeedZonesReq)
        feed_or_404(fid)
        try:
            return feeds.update_zones(fid, req.expected_revision, [z.model_dump() for z in req.zones]).view()
        except FeedError as e:
            err(e)

    @app.get("/api/feeds/{fid}/preview.jpg")
    def feeds_preview(fid: str):
        jpg = feed_or_404(fid).preview_jpeg()
        if jpg is None:
            raise HTTPException(404, {"msg_id": "mg.refusal.feed_no_preview_yet"})
        return Response(jpg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    # ------------------------------------------------------------------ alerts
    @app.get("/api/alerts")
    def alerts_list(status: str | None = None, severity: str | None = None, source_kind: str | None = None, simulated: bool | None = None, after: int = 0, limit: int = 200, order: str = "desc"):
        if status is not None and status not in alert_mod.STATUSES or severity is not None and severity not in alert_mod.SEVERITIES or order not in ("asc", "desc"):
            raise HTTPException(422, {"msg_id": "mg.refusal.bad_filter"})
        return alerts.list(status, severity, source_kind, simulated, max(0, after), max(1, min(limit, 500)), order)

    @app.get("/api/alerts/ids")
    def alerts_ids():
        return {"ids": alerts.ids()}

    @app.get("/api/alerts/summary")
    def alerts_summary():
        return alerts.summary()

    @app.get("/api/alerts/settings")
    def alerts_settings_get():
        return {"settings": alerts.settings, "webhook_contract": alert_mod.webhook_contract()}

    @app.put("/api/alerts/settings")
    async def alerts_settings_put(request: Request):
        raw = await read_body(request, MAX_SETTINGS_BODY)
        try:
            body = json.loads(raw)
        except ValueError:
            raise HTTPException(422, {"msg_id": "mg.refusal.alert_settings_invalid"})
        try:
            return {"settings": alerts.set_settings(body)}
        except PermissionError:
            raise HTTPException(403, {"msg_id": "mg.refusal.webhook_disabled_in_this_increment"})
        except (ValueError, KeyError, TypeError):
            raise HTTPException(422, {"msg_id": "mg.refusal.alert_settings_invalid"})

    @app.post("/api/alerts/seen")
    async def alerts_seen(request: Request):
        req = await body_model(request, SeenReq)
        return {"marked": alerts.mark_seen(req.ids)}

    def alert_or_404(aid: str):
        if not re.fullmatch(r"alt_[0-9a-f]{16}", aid) or aid not in alerts.alerts:
            raise HTTPException(404, {"msg_id": "mg.refusal.unknown_alert"})

    @app.post("/api/alerts/{aid}/ack")
    def alerts_ack(aid: str):
        alert_or_404(aid)
        try:
            return alerts.public(alerts.acknowledge(aid))
        except ValueError:
            raise HTTPException(409, {"msg_id": "mg.refusal.alert_already_resolved"})

    @app.post("/api/alerts/{aid}/resolve")
    def alerts_resolve(aid: str):
        alert_or_404(aid)
        return alerts.public(alerts.resolve(aid))

    @app.post("/api/alerts/{aid}/delivery")
    async def alerts_delivery(aid: str, request: Request):
        """The browser reports what its own notification / sound attempt did (permission granted or denied, error). The server never claims a browser delivery it was not told about."""
        req = await body_model(request, DeliveryReq)
        alert_or_404(aid)
        try:
            return alerts.public(alerts.report_delivery(aid, req.channel, req.status, req.detail))
        except ValueError as e:
            raise HTTPException(409, {"msg_id": "mg.refusal." + str(e)})

    # ------------------------------------------------------------------ incident investigation (MG-PRODUCT-VERTICAL-06): a read-only projection of stored facts
    def incident_for(aid: str) -> dict:
        alert_or_404(aid)
        a = alerts.public(alerts.get(aid))
        src = a["source"]
        feed = None
        if a.get("feed_id") and re.fullmatch(FEED_ID_RE, a["feed_id"]):
            try:
                feed = feeds.get(a["feed_id"]).view()
            except FeedError:
                feed = None                                    # the feed is gone: the record says so, it does not guess
        life = None
        if a.get("feed_id") and src["kind"] == "feed" and not a.get("loaded_from_store") and src["source_id"].startswith(a["feed_id"] + ":"):
            life = feeds.life_for(a["feed_id"], src["source_id"].split(":", 1)[1])     # the recorded lifecycle of THIS run only; a restarted feed never lends its new run to an old alert
        run = None
        if src["kind"] == "simulation":
            m = re.fullmatch(r"sim:(run_[0-9a-f]{16}):\d+:[0-9a-f]+", src["source_id"])
            if m:
                d = runs_dir / m.group(1)
                try:
                    snap = snapshot.take(d)
                    doc = evaluate_dir(d, snap=snap)
                    if not snapshot.matches_disk(snap, d):
                        raise ValueError("source_changed_while_read")
                    run = {"run_id": m.group(1), "evaluation": doc, "consistent": True, "digest": (doc.get("inputs") or {}).get("obsset_digest")}
                except (OSError, ValueError, KeyError, TypeError):
                    run = {"run_id": m.group(1), "evaluation": None, "consistent": False}   # missing, unsafe or self-contradictory: never shown as a coherent run (MG-RISK-035)
        try:
            mid = __import__("mineguard.detector", fromlist=["x"]).load_manifest_entry()["model_id"]
        except Exception:
            mid = None
        if src["kind"] == "simulation":
            provider = {"kind": "scripted_simulation", "model_id": None}
        elif src["kind"] == "webcam_session":
            provider = {"kind": "webcam_model_inference", "model_id": mid}              # a genuine model run on a browser-declared camera (origin is client-declared)
        else:
            provider = {"kind": "repeated_source_simulation" if src["provenance"] == "repeated_source_simulation" else "model_inference", "model_id": mid}
        return incident_mod.build(a, feed=feed, run=run, provider=provider, versions={"semantics_version": SEMANTICS_VERSION, "engine_version": ENGINE_VERSION},
                                  generated_utc=alerts._utc(alerts.wall()), lifecycle=life)

    @app.get("/api/incidents/{aid}")
    def incident_get(aid: str):
        return incident_for(aid)

    @app.get("/api/incidents/{aid}/explanation")
    def incident_explanation(aid: str):
        """SIMULATED explanation of the incident (deterministic fake transport, validated against the incident). It never changes the incident and no model is called."""
        return explain.generate(incident_for(aid))

    @app.get("/api/incidents/{aid}/export")
    def incident_export(aid: str):
        inc = incident_for(aid)
        body = {"schema": "mg.incident.export/1", "incident": inc,
                "projection": {"kind": "generated_projection", "generated_utc": inc["generated_utc"], "is_original_record": False, "original_records": ["alert store entry", "stored run files or live feed state of this process"],
                               "authenticity": "not_independently_verified", "known_open_findings": ["MG-RISK-036", "MG-WCAM-035"]}, "note": "local structured export for later human investigation; no image or footage; not proof of authenticity to third parties"}
        return Response(json.dumps(body, indent=1, ensure_ascii=False), media_type="application/json",
                        headers={"content-disposition": f'attachment; filename="{aid}.mineguard-incident.json"'})

    # ------------------------------------------------------------------ dashboards
    @app.get("/api/monitor/overview")
    def monitor_overview(minutes: int = 60, bucket: int = 5):
        if minutes not in (15, 60, 240, 1440) or bucket not in (1, 5, 15, 60) or bucket > minutes:
            raise HTTPException(422, {"msg_id": "mg.refusal.bad_interval"})
        return monitor.operator_overview(st, feeds, alerts, runs_dir, minutes, bucket)

    @app.get("/api/monitor/developer")
    def monitor_developer():
        return monitor.developer_overview(st, feeds, alerts, runs_dir, root)
