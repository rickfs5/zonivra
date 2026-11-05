import { HEARTBEAT_POLICY, STOP_POLICY, nextMsg, retryBusy, sendReliably } from "./lifecycle";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiVs02, type LiveView, type ModelInfo, type SessionBundle } from "./api";
import { camView, classifyDeviceError, isAnalysing, isModelUnavailable, modelIndicator, modelMsgId, viewHelpId, viewMsgId, type DeviceFailure } from "./camState";
import { drawDraft, drawLiveBoxes, drawZones } from "./draw";
import { t, tEnum, tf } from "./i18n";
import { RiskPanel } from "./RiskPanel";
import { refusalText, useZoneDrawing, ZoneEditor } from "./ZoneEditor";
import { captureDims, classColor, containRect, evaluationTotalUs, mayCapture, originLabelId, trustNoticeIds, trustState, type Evaluation, type Zone } from "./lib";
import type { ZoneIn } from "./api";

type Cam = "off" | "requesting" | "on" | "denied" | "analyzing" | "finalizing";
const looksLikeTestSource = (label: string) => /fake|virtual|test|synthetic/i.test(label);

function toZones(zs: ZoneIn[], w: number, h: number): Zone[] {
  return zs.map((z) => ({ zone_id: z.zone_id, kind: z.kind, polygon: z.polygon, width: w, height: h, name: z.name ?? z.zone_id }));
}

export function WebcamPanel() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const capRef = useRef<HTMLCanvasElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<number | null>(null);
  const pollRef = useRef<number | null>(null);
  const seqRef = useRef(0);
  const inflightRef = useRef(0);
  const sidRef = useRef<string | null>(null);
  const [cam, setCam] = useState<Cam>("off");
  const camPrev = useRef<Cam>("off");
  const [dims, setDims] = useState<{ w: number; h: number } | null>(null);
  const [intervalMs, setIntervalMs] = useState(300);
  const [view, setView] = useState<LiveView | null>(null);
  const [zonesIn, setZonesIn] = useState<ZoneIn[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [skipped, setSkipped] = useState(0);
  const [activeTracks, setActiveTracks] = useState(0);
  const [origin, setOrigin] = useState<"getusermedia" | "test_source">("getusermedia");
  const [label, setLabel] = useState("");
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [stored, setStored] = useState<{ id: string; bundle: SessionBundle; evaluation: Evaluation; trust: "session" | "verified" | "unverified" } | null>(null);
  const [sessions, setSessions] = useState<{ session_id: string; trust: string; frames_analyzed: number }[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [privacy, setPrivacy] = useState<"on" | "off">("on");   // never persisted: every page load starts at Privacy ON; fixed per session at start
  // Privacy ON is restored whenever a capture ends or the camera is released: an OFF choice never carries over to another session (nothing is persisted).
  useEffect(() => { if (cam === "off" && camPrev.current !== "off") setPrivacy("on"); camPrev.current = cam; }, [cam]);
  const [failure, setFailure] = useState<DeviceFailure | null>(null);                 // D3: why the camera is unavailable
  const [analysisUnavailable, setAnalysisUnavailable] = useState(false);                // D1: analysis could not start (the preview is still on)
  const [model, setModel] = useState<ModelInfo | null>(null);                           // D2: weights verification, shown before any analysis
  useEffect(() => { let live = true; api.model().then((m) => { if (live) setModel(m); }).catch(() => undefined); return () => { live = false; }; }, []);
  const drawing = useZoneDrawing(dims?.w ?? 640, dims?.h ?? 360);

  const refreshSessions = useCallback(async () => { try { setSessions(await apiVs02.sessions()); } catch { /* listing is optional */ } }, []);
  useEffect(() => { refreshSessions(); }, [refreshSessions]);

  const releaseCamera = useCallback(() => {
    if (timerRef.current !== null) { window.clearInterval(timerRef.current); timerRef.current = null; }
    const s = streamRef.current;
    if (s) { s.getTracks().forEach((tr) => { tr.onended = null; tr.stop(); }); streamRef.current = null; }
    if (videoRef.current) videoRef.current.srcObject = null;
    setActiveTracks(0);
  }, []);

  const stopPolling = () => { if (pollRef.current !== null) { window.clearInterval(pollRef.current); pollRef.current = null; } };

  const loadStored = useCallback(async (id: string) => {
    const [bundle, risk] = await Promise.all([apiVs02.session(id), apiVs02.risk("sessions", id)]);
    setStored({ id, bundle, evaluation: risk.evaluation, trust: risk.trust });
    refreshSessions();
  }, [refreshSessions]);

  const msgRef = useRef(0);          // numbered control messages (idempotent on the server)
  const hbBusyRef = useRef(false);   // at most ONE heartbeat in flight: no unbounded background queue
  const [hbFail, setHbFail] = useState(0);
  const finish = useCallback(async (why: string | null, reason: "client_stop" | "device_ended" = "client_stop") => {
    const sid = sidRef.current;
    if (timerRef.current !== null) { window.clearInterval(timerRef.current); timerRef.current = null; }
    stopPolling();
    setCam("finalizing");
    releaseCamera();            // the camera is ALWAYS released as soon as the analysis ends, whatever the server answers
    try {
      if (sid) {
        // ONE message number for every attempt: a retried stop is the same message to the server (idempotent). Two bounded layers: a network failure is retried at most STOP_POLICY.maxAttempts
        // times (<= 4.5 s of back-off); a 503 «the model is still inside a frame» (each answered by the server after <= 5 s) keeps the session and is retried until a WALL-CLOCK deadline of two
        // minutes (lifecycle.ts retryBusy), never a number of attempts and never forever.
        const msg = (msgRef.current = nextMsg(msgRef.current));
        const attemptStop = async (signal: AbortSignal) => {   // the signal fires at the wall-clock deadline: a request that never settles is cancelled, not waited for (cycle-24 MG-LIFE-024)
          let netFails = 0;
          for (;;) {
            try { return await apiVs02.stopSession(sid, reason, seqRef.current, msg, signal); }
            catch (e) {
              if (signal.aborted || !(e instanceof TypeError) || ++netFails >= STOP_POLICY.maxAttempts) throw e;
              await new Promise((r) => setTimeout(r, STOP_POLICY.backoffMs[Math.min(netFails - 1, STOP_POLICY.backoffMs.length - 1)]));
            }
          }
        };
        try {
          const out = await retryBusy(attemptStop, (e) => /session_worker_busy/.test(String(e)));
          sidRef.current = null;
          if (out.run_id) await loadStored(out.run_id);
        } catch (e) { sidRef.current = null; throw e; }
      }
    } catch (e) {
      setError(refusalText(e));
    } finally {
      setCam("off");
      if (why) setNotice(why);
    }
  }, [loadStored, releaseCamera]);

  // leaving the tab releases the camera AND ends the live session with the same loss declaration as «Detener» (a best-effort request that survives the unmount); a timer never outlives the panel
  useEffect(() => () => {
    if (timerRef.current !== null) { window.clearInterval(timerRef.current); timerRef.current = null; }
    stopPolling(); releaseCamera();
    const sid = sidRef.current;
    if (sid) { sidRef.current = null; void fetch(`/api/webcam/sessions/${sid}/stop?reason=client_stop`, { method: "POST", keepalive: true, headers: { "X-MG-Token": document.querySelector<HTMLMetaElement>('meta[name="mg-session-token"]')?.content ?? "", "X-MG-Next-Seq": String(seqRef.current), "X-MG-Msg-Seq": String(nextMsg(msgRef.current)) } }).catch(() => undefined); }
  }, [releaseCamera]);
  // closing or reloading the tab is a best-effort CLEAN stop (one keepalive request, no retry loop can outlive the page); if it is lost the server finalizes as client_disconnected and
  // admits one bounded late declaration (MG-WCAM-019)
  useEffect(() => {
    const onHide = () => {
      const sid = sidRef.current;
      if (!sid) return;
      void fetch(`/api/webcam/sessions/${sid}/stop?reason=client_stop`, { method: "POST", keepalive: true, headers: { "X-MG-Token": document.querySelector<HTMLMetaElement>('meta[name="mg-session-token"]')?.content ?? "", "X-MG-Next-Seq": String(seqRef.current), "X-MG-Msg-Seq": String(nextMsg(msgRef.current)) } }).catch(() => undefined);
    };
    window.addEventListener("pagehide", onHide);
    return () => window.removeEventListener("pagehide", onHide);
  }, []);
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  const startCamera = async () => {
    setError(null); setNotice(null); setStored(null); setFailure(null); setAnalysisUnavailable(false);
    setCam("requesting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: { width: { ideal: 1280 }, height: { ideal: 720 } }, audio: false });
      streamRef.current = stream;
      const track = stream.getVideoTracks()[0];
      setLabel(track?.label ?? "");
      setOrigin(looksLikeTestSource(track?.label ?? "") ? "test_source" : "getusermedia");
      track.onended = () => { if (sidRef.current) finish(t("mg.cam.ended"), "device_ended"); else { releaseCamera(); setCam("off"); setNotice(t("mg.cam.ended")); } };
      const v = videoRef.current!;
      v.srcObject = stream;
      await v.play();
      await new Promise<void>((res) => { if (v.videoWidth > 0) res(); else v.addEventListener("loadedmetadata", () => res(), { once: true }); });
      const d = captureDims(v.videoWidth, v.videoHeight);
      if (!d) throw new Error("camera too small");
      setDims(d);
      setActiveTracks(stream.getTracks().filter((x) => x.readyState === "live").length);
      setCam("on");
    } catch (e) {
      releaseCamera();
      const f = classifyDeviceError(e);
      setFailure(f);
      setCam("denied");
      setError(`${t(viewMsgId(f))}${typeof e === "object" && e !== null && "name" in e ? ` (${String((e as { name: unknown }).name).slice(0, 60)})` : ""}`);
    }
  };

  const tick = useCallback(() => {
    const v = videoRef.current, sid = sidRef.current;
    if (!v || !sid || !dims) return;
    const seq = seqRef.current++;   // EVERY capture tick consumes a sequence number, also a skipped or failed one: the server derives all loss from gaps (cycle-18 MG-WCAM-018)
    if (!mayCapture(inflightRef.current)) { setSkipped((n) => n + 1); return; }   // bounded on the client too: skipped, never queued
    inflightRef.current++;   // the slot is reserved BEFORE the asynchronous encode, so concurrent ticks can never exceed the bound
    const c = (capRef.current ??= document.createElement("canvas"));
    c.width = dims.w; c.height = dims.h;
    c.getContext("2d")!.drawImage(v, 0, 0, dims.w, dims.h);
    const capUs = Math.round(performance.now() * 1000);
    c.toBlob((blob) => {
      if (!blob) { inflightRef.current--; return; }   // the sequence number stays unused: a visible gap
      apiVs02.postFrame(sid, seq, capUs, blob).finally(() => { inflightRef.current--; });
    }, "image/jpeg", 0.8);
  }, [dims]);

  const startAnalysis = async () => {
    if (!dims || cam !== "on") return;
    setError(null); setNotice(null); setStored(null); setView(null); setSkipped(0); seqRef.current = 0; inflightRef.current = 0; setAnalysisUnavailable(false);   // no counters of an earlier session may remain on screen
    try {
      const { session_id } = await apiVs02.startSession({ width: dims.w, height: dims.h, interval_ms: intervalMs, threshold: 0.3, zones: zonesIn, privacy,
        capture: { origin, device_label: label, user_agent: navigator.userAgent } });
      sidRef.current = session_id;
      setCam("analyzing");
      timerRef.current = window.setInterval(tick, intervalMs);
      let lastBeat = 0;
      pollRef.current = window.setInterval(async () => {
        if (Date.now() - lastBeat >= 1000 && !hbBusyRef.current) {   // ~1 Hz, one in flight; the value is cumulative, so a missed beat is superseded by the next
          lastBeat = Date.now(); hbBusyRef.current = true;
          const msg = (msgRef.current = nextMsg(msgRef.current)), tick = seqRef.current;
          void sendReliably(() => apiVs02.watermark(session_id, tick, msg), HEARTBEAT_POLICY).then((d) => { setHbFail(d.outcome === "acked" ? 0 : (n) => n + 1); }).finally(() => { hbBusyRef.current = false; });
        }
        try { const vw = await apiVs02.sessionView(session_id); setView(vw); if (vw.state === "done") { if (vw.run_id) await loadStored(vw.run_id); stopPolling(); sidRef.current = null; releaseCamera(); setCam("off"); setNotice(vw.closed_reason ? tEnum("mg.xd.enum.closed", vw.closed_reason) : null); } }
        catch (e) { setError(refusalText(e)); }
      }, 300);
    } catch (e) {
      if (isModelUnavailable(e)) setAnalysisUnavailable(true);     // the camera stays on as a PREVIEW; no session exists
      setError(refusalText(e));
    }
  };

  useEffect(() => { if (cam === "analyzing" && timerRef.current !== null) { window.clearInterval(timerRef.current); timerRef.current = window.setInterval(tick, intervalMs); } }, [tick, intervalMs, cam]);

  // overlay: zones (coloured by live state), draft polygon, live boxes
  const shownZones: Zone[] = view && cam === "analyzing" ? view.zones : dims ? toZones(zonesIn, dims.w, dims.h) : [];
  useEffect(() => {
    const c = canvasRef.current;
    if (!c || !dims) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = size.w * dpr; c.height = size.h * dpr;
    const ctx = c.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.w, size.h);
    const r = containRect(size.w, size.h, dims.w, dims.h);
    drawZones(ctx, shownZones, r, dims.w, dims.h, (zid) => (cam === "analyzing" ? view?.risk[zid]?.state ?? "UNKNOWN" : null));
    drawDraft(ctx, drawing.draft, r, dims.w, dims.h);
    if (cam === "analyzing" && view) drawLiveBoxes(ctx, view.latest.objects, r, dims.w, dims.h, classColor);
  });

  const onStageClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!drawing.active || !dims) return;
    const b = e.currentTarget.getBoundingClientRect();
    drawing.click(e.clientX - b.left, e.clientY - b.top, containRect(size.w, size.h, dims.w, dims.h));
  };

  const k = view?.counters;
  const view_ = camView(cam, failure, analysisUnavailable);
  const statusId = cam === "off" ? "mg.cam.status.off" : cam === "requesting" ? "mg.cam.status.requesting" : cam === "denied" ? viewMsgId(view_)
    : cam === "analyzing" ? "mg.cam.status.analyzing" : cam === "finalizing" ? "mg.cam.status.finalizing" : "mg.cam.status.on";
  const live = view?.risk ?? {};
  const ts = stored ? trustState(stored.trust) : null;

  return (
    <div>
    <div className="page-head"><div><h2>{t("mg.nav.camara")}</h2><p className="muted">{t("mg.cam.subtitle")}</p></div></div>
    {cam === "denied" && <p className="banner warn" data-testid="cam-denied-help" role="alert">{t(viewHelpId(view_))}</p>}
    <div className="webcam" data-testid="webcam-panel">
      <section className="left">
        <fieldset className="privacy" data-testid="privacy-box" disabled={cam === "analyzing"}>
          <legend>{t("mg.priv.title")}</legend>
          <label><input type="radio" name="privacy" checked={privacy === "on"} onChange={() => setPrivacy("on")} data-testid="privacy-on" /> {t("mg.priv.on")}</label>
          <label><input type="radio" name="privacy" checked={privacy === "off"} onChange={() => setPrivacy("off")} data-testid="privacy-off" /> {t("mg.priv.off")}</label>
          <p className={privacy === "on" ? "ok" : "badge badge-YELLOW"} data-testid="privacy-banner">{privacy === "on" ? t("mg.priv.on_note") : t("mg.priv.off_note")}</p>
          {cam === "analyzing" && <p className="muted" data-testid="privacy-fixed">{t("mg.priv.fixed")} {(view?.retention ?? (privacy === "off" ? "frames" : "metadata_only")) === "frames" ? t("mg.priv.retained") : t("mg.priv.not_retained")}</p>}
        </fieldset>
        <p className="muted">{t("mg.cam.declared")}</p>
        <p className={`cam-view cam-view-${view_}`} role="status" aria-live="polite" data-testid="cam-view" data-view={view_} data-analysing={isAnalysing(view_) ? "yes" : "no"}><b>{t(viewMsgId(view_))}</b></p>
        {view_ === "preview_only" && <p className="banner info" data-testid="cam-preview-banner">{t("mg.cam.preview_banner")}</p>}
        {view_ === "analysis_unavailable" && <p className="banner warn" data-testid="cam-unavailable-banner" role="alert">{t(viewHelpId(view_))}</p>}
        {view_ === "analysis_active" && <p className="banner info" data-testid="cam-analysis-banner">{t("mg.cam.analysis_banner")}</p>}
        <p className="muted" data-testid="cam-model" data-state={modelIndicator(model)}>{t(modelMsgId(modelIndicator(model)))}</p>
        <div className="stage" ref={stageRef} onClick={onStageClick} data-testid="cam-stage" style={{ aspectRatio: dims ? `${dims.w}/${dims.h}` : "16/9" }}>
          <video ref={videoRef} muted playsInline data-testid="cam-video" aria-label={t("mg.cam.preview_label")} />
          <canvas ref={canvasRef} style={{ width: size.w, height: size.h }} data-testid="cam-overlay" aria-hidden="true" />
        </div>
        <div className="controls">
          {(cam === "off" || cam === "denied") && <button className="primary" onClick={startCamera} data-testid="cam-start">{t("mg.cam.start_camera")}</button>}
          {cam === "on" && <>
            <button className="primary" onClick={startAnalysis} data-testid="cam-analyze">{t("mg.cam.start")}</button>{" "}
            <button onClick={() => { releaseCamera(); setCam("off"); setAnalysisUnavailable(false); }} data-testid="cam-release">{t("mg.cam.stop_camera")}</button>
          </>}
          {cam === "analyzing" && <button className="primary" onClick={() => finish(null)} data-testid="cam-stop">{t("mg.cam.stop")}</button>}
          <label>{t("mg.cam.interval")}: <b>{intervalMs} ms</b>
            <input type="range" min={100} max={1000} step={50} value={intervalMs} disabled={cam === "analyzing"} onChange={(e) => setIntervalMs(Number(e.target.value))} data-testid="cam-interval" /></label>
        </div>
        <p data-testid="cam-status" data-state={cam} data-tracks={activeTracks} className={cam === "denied" ? "error" : "muted"}>{t(statusId)}{dims ? ` · ${dims.w}×${dims.h}` : ""}</p>
        {cam === "analyzing" && hbFail > 0 && <p className="muted" data-testid="cam-hb-unacked">{t("mg.cam.hb_unacked")} {hbFail}</p>}
        <p className="muted" data-testid="cam-origin" data-origin={origin}>{t(origin === "test_source" ? "mg.cam.test_source" : "mg.cam.getusermedia")}{label ? ` · «${label}»` : ""}</p>
        {error && <p className="error" role="alert" data-testid="cam-error">{error}</p>}
        {notice && <p className="warn" data-testid="cam-notice">{notice}</p>}
        {cam === "analyzing" && k && view && (
          <div className="card" data-testid="cam-live">
            <p className="mono" data-testid="cam-counters" data-received={k.received} data-analyzed={k.analyzed} data-dropped-queue={k.dropped_queue_overflow} data-dropped-stale={k.dropped_stale} data-errors={k.adapter_error} data-skipped={skipped}>
              {t("mg.cam.counters")}: {k.received} / {k.analyzed} / {k.dropped_queue_overflow} / {k.dropped_stale} / {k.adapter_error} · {tf("mg.xd.cam.skipped", { n: skipped })}</p>
            <p className="muted">{t("mg.cam.queue")}: {view.queue_depth}/{view.queue_max} · {t("mg.cam.latency")}: {view.latency_ms.p50 ?? "–"} / {view.latency_ms.max ?? "–"}</p>
            {Object.entries(live).map(([zid, s]) => (
              <p key={zid} data-testid={`cam-risk-${zid}`} data-state={s.state} data-pending={s.pending.length ? "yes" : "no"}>
                <span className={`badge badge-${s.state}`}>{t(`mg.risk.badge.${s.state}`)}</span> {zid}{s.pending.length ? ` · ${t("mg.xd.cam.pending")}` : ""}{s.causes.length ? ` · ${s.causes.join(", ")}` : ""}</p>))}
            {Object.entries(view.events).flatMap(([zid, evs]) => evs.map((e) => <p key={e.event_id} data-testid="cam-live-event" data-severity={e.severity}>{t(`mg.risk.badge.${e.severity}`)} · {zid} · {e.open ? t("mg.xd.cam.open") : t(`mg.risk.reason.${e.closed_reason ?? "open"}`)}</p>))}
          </div>)}
        {stored && (
          <div className="card" data-testid="cam-stored" data-trust={ts}>
            <h2>{stored.id}</h2>
            <p className="muted" data-testid="origin">{t("mg.origin.label")}: <b>{t(originLabelId(stored.bundle.observation_set.origin, stored.trust))}</b></p>
            <p className={ts === "unverified" ? "warn" : "muted"} data-testid="trust-state" data-trust={ts}>{t(trustNoticeIds(stored.trust).status)}</p>
            <p className="mono">{stored.bundle.provenance.capture ? tf("mg.xd.cam.capture_declared", { origin: stored.bundle.provenance.capture.origin }) : ""}</p>
            <p className={stored.bundle.retention === "frames" ? "badge badge-YELLOW" : "muted"} data-testid="cam-retention" data-retention={stored.bundle.retention}>
              {stored.bundle.retention === "frames" ? t("mg.priv.stored_frames") : stored.bundle.retention === "media_deleted" ? t("mg.priv.stored_deleted") : t("mg.priv.stored_none")}</p>
            <button onClick={async () => { try { await apiVs02.verifySession(stored.id); await loadStored(stored.id); } catch (e) { setError(refusalText(e)); } }} data-testid="cam-verify">{t("mg.ui.verify")}</button>
          </div>)}
      </section>
      <aside className="right">
        <ZoneEditor zones={shownZones} drawing={drawing} fw={dims?.w ?? 640} fh={dims?.h ?? 360} busy={cam === "analyzing"} error={null} dirty={false} hideSave
          onChange={(zs) => setZonesIn(zs)} onSave={() => undefined} />
        {stored && <RiskPanel evaluation={stored.evaluation} trust={stored.trust} origin={stored.bundle.observation_set.origin} kind="sessions" id={stored.id} tUs={null}
          totalUs={evaluationTotalUs(stored.evaluation)}
          evidenceUrl={(i) => apiVs02.sessionFrameUrl(stored.id, i)} exportUrl={apiVs02.riskExportUrl("sessions", stored.id)} />}
        <div className="card">
          <h2>{t("mg.cam.sessions")}</h2>
          <ul data-testid="cam-sessions">{sessions.map((s) => <li key={s.session_id}><button onClick={() => loadStored(s.session_id).catch((e) => setError(refusalText(e)))}>{s.session_id}</button> · {tEnum("mg.xd.enum.trust", s.trust)} · {s.frames_analyzed}</li>)}</ul>
        </div>
        <div className="card notes"><ul>
          {["mg.lim.image_space_only", "mg.lim.uncalibrated_confidence", "mg.lim.coco_no_excavator_class", "mg.lim.not_safety_system", "mg.lim.experimental_thresholds", "mg.lim.tracker_identity_not_guaranteed", "mg.lim.capture_origin_client_declared"]
            .map((id) => <li key={id}>{t(id)}</li>)}</ul></div>
      </aside>
    </div>
    </div>
  );
}
