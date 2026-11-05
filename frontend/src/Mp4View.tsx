import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, apiVs02, type Job, type ModelInfo, type RiskReply, type RunBundle, type VerifyReport, type VideoInfo, type ZoneIn } from "./api";
import { drawDraft, drawZones } from "./draw";
import { RiskPanel } from "./RiskPanel";
import { refusalText, useZoneDrawing, ZoneEditor } from "./ZoneEditor";
import { t, tf } from "./i18n";
import { boxStyle, classColor, containRect, evaluationTotalUs, formatMs, frameForTimeUs, intervalAt, isStrictlyIncreasing, isTrusted, lastAnalyzedAtOrBefore, originLabelId, scaleBox, summarize, trustNoticeIds, trustState, type Zone } from "./lib";

type Mode = "video" | "frame";

export interface OpenRun { id: string; base: string | null; jumpRed: boolean; tUs?: number; n: number }

/** The recorded-MP4 workspace (previously the whole app). `open` lets another area load a run into it (e.g. a simulation). */
export function Mp4View({ open }: { open?: OpenRun }) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [videos, setVideos] = useState<VideoInfo[]>([]);
  const [model, setModel] = useState<ModelInfo | null>(null);
  const [bundle, setBundle] = useState<RunBundle | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  const [stride, setStride] = useState(3);
  const [recordPct, setRecordPct] = useState(30);
  const [displayPct, setDisplayPct] = useState(30);
  const [mode, setMode] = useState<Mode>("video");
  const [timeUs, setTimeUs] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [report, setReport] = useState<VerifyReport | null>(null);
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [risk, setRisk] = useState<RiskReply | null>(null);
  const [zonesLocal, setZonesLocal] = useState<ZoneIn[] | null>(null);   // unsaved edits; null = no pending edit
  const [zoneBusy, setZoneBusy] = useState(false);
  const [zoneErr, setZoneErr] = useState<string | null>(null);
  const [simBusy, setSimBusy] = useState(false);
  const [simHint, setSimHint] = useState(false);
  const [simBase, setSimBase] = useState<string | null>(null);

  const video = videos[0];
  const runId = bundle?.manifest.run_id ?? null;

  const loadRisk = useCallback(async (id: string) => {
    try { setRisk(await apiVs02.risk("runs", id)); } catch (e) { setRisk(null); setZoneErr(refusalText(e)); }
  }, []);
  const loadRun = useCallback(async (id: string) => {
    setBundle(await api.run(id));
    setReport(null);
    setZonesLocal(null);
    await loadRisk(id);
  }, [loadRisk]);

  useEffect(() => {
    (async () => {
      try {
        const [v, m, runs] = await Promise.all([api.videos(), api.model(), api.runs()]);
        setVideos(v);
        setModel(m);
        if (runs.length) await loadRun(runs[0].run_id);
      } catch (e) {
        setError(String(e));
      }
    })();
  }, [loadRun]);

  useEffect(() => {
    if (!job || job.status === "done" || job.status === "failed" || job.status === "cancelled") return;
    const id = setInterval(async () => {
      try {
        const j = await api.job(job.job_id);
        setJob(j);
        if (j.status === "done" && j.run_id) await loadRun(j.run_id);
        if (j.status === "failed") setError(j.error);
      } catch (e) {
        setError(String(e));
      }
    }, 500);
    return () => clearInterval(id);
  }, [job, loadRun]);

  // Keep the displayed time in sync with the decoded frame the browser presents.
  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    let handle = 0;
    const onFrame = (_now: number, md: { mediaTime: number }) => {
      setTimeUs(Math.round(md.mediaTime * 1e6));
      handle = v.requestVideoFrameCallback(onFrame);
    };
    handle = v.requestVideoFrameCallback(onFrame);
    const sync = () => setTimeUs(Math.round(v.currentTime * 1e6));
    const onPlay = () => setPlaying(true), onPause = () => setPlaying(false);
    v.addEventListener("seeked", sync);
    v.addEventListener("timeupdate", sync);
    v.addEventListener("play", onPlay);
    v.addEventListener("pause", onPause);
    return () => {
      v.cancelVideoFrameCallback(handle);
      v.removeEventListener("seeked", sync);
      v.removeEventListener("timeupdate", sync);
      v.removeEventListener("play", onPlay);
      v.removeEventListener("pause", onPause);
    };
  }, [video]);

  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el);
    setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);

  const ptsUs = bundle?.source.frame_table.pts_us ?? [];
  const frames = bundle?.observation_set.frames ?? [];
  const analyzed = useMemo(() => frames.map((f) => f.frame_index), [frames]);
  const frameIdx = frameForTimeUs(ptsUs, timeUs);
  const orderOk = isStrictlyIncreasing(analyzed); // MG-DATA-001-005: with any other order the binary search could pick another frame's boxes
  const trusted = isTrusted(bundle?.trust); // MG-ORIGIN-002: boxes of an unverified run are only ever a provisional view
  const pos = orderOk ? lastAnalyzedAtOrBefore(analyzed, frameIdx) : -1;
  const shown = pos >= 0 ? frames[pos] : null;
  const exact = shown !== null && shown.frame_index === frameIdx;
  const minPermille = displayPct * 10;
  const binding = bundle?.observation_set.source_binding;
  const drawing = useZoneDrawing(binding?.width ?? 1280, binding?.height ?? 720);
  const savedZones: Zone[] = risk?.zones.zones ?? [];
  const shownZones: Zone[] = zonesLocal ? zonesLocal.map((z) => ({ zone_id: z.zone_id, kind: z.kind, polygon: z.polygon, width: binding?.width ?? 1280, height: binding?.height ?? 720, name: z.name ?? z.zone_id })) : savedZones;

  useEffect(() => {
    const c = canvasRef.current;
    if (!c || !binding) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = size.w * dpr;
    c.height = size.h * dpr;
    const ctx = c.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.w, size.h);
    const r = containRect(size.w, size.h, binding.width, binding.height);
    drawZones(ctx, shownZones, r, binding.width, binding.height, (zid) => (zonesLocal ? null : intervalAt(risk?.evaluation.zones[zid]?.timeline ?? [], timeUs)?.state ?? null));
    drawDraft(ctx, drawing.draft, r, binding.width, binding.height);
    if (!shown) return;
    const solid = mode === "frame" || exact;
    ctx.lineWidth = 2;
    ctx.font = "13px system-ui, sans-serif";
    for (const o of shown.objects) {
      if ((o.confidence.permille ?? 0) < minPermille) continue;
      const b = scaleBox(o.bbox, binding.width, binding.height, r.w, r.h);
      const st = boxStyle(trusted, classColor(o.cls.label));
      const col = st.color;
      ctx.setLineDash(trusted ? (solid ? [] : [6, 4]) : st.dash);
      ctx.strokeStyle = col;
      ctx.strokeRect(r.x + b.x, r.y + b.y, b.w, b.h);
      const label = `${st.labelPrefix}${o.cls.detector_label} ${((o.confidence.permille ?? 0) / 10).toFixed(0)}%`;
      const tw = ctx.measureText(label).width + 8;
      ctx.fillStyle = col;
      ctx.fillRect(r.x + b.x, Math.max(0, r.y + b.y - 18), tw, 18);
      ctx.fillStyle = "#000";
      ctx.fillText(label, r.x + b.x + 4, Math.max(13, r.y + b.y - 5));
    }
  }, [shown, binding, size, minPermille, exact, mode, trusted, shownZones, drawing.draft, risk, timeUs, zonesLocal]);

  const seekToFrame = (idx: number) => {
    const v = videoRef.current;
    if (!v || !ptsUs.length) return;
    v.pause();
    v.currentTime = (ptsUs[idx] + 500) / 1e6; // +0.5 ms stays inside frame idx (frames are >= 33 ms long)
  };
  const stepAnalyzed = (dir: -1 | 1) => {
    if (!analyzed.length) return;
    // next: first analyzed frame after the current one; prev: the analyzed frame before it (or the last analyzed one if the current is not analyzed)
    const target = dir === 1 ? pos + 1 : exact ? pos - 1 : pos;
    seekToFrame(analyzed[Math.min(analyzed.length - 1, Math.max(0, target))]);
  };

  const start = async () => {
    if (!video) return;
    setError(null);
    try {
      const { job_id } = await api.analyze(video.video_id, stride, recordPct / 100);
      setJob(await api.job(job_id));
    } catch (e) {
      setError(String(e));
    }
  };
  const running = job?.status === "queued" || job?.status === "running";
  const doVerify = async () => {
    if (!runId) return;
    setVerifying(true);
    try {
      const r = await api.verify(runId);
      setReport(r);
      setBundle(await api.run(runId)); // refresh the trust state, also after a failure (a failed deep verification revokes the session grant)
      await loadRisk(runId);
    } catch (e) {
      setError(String(e));
    } finally {
      setVerifying(false);
    }
  };

  const saveZones = async () => {
    if (!runId || !zonesLocal) return;
    setZoneBusy(true); setZoneErr(null);
    try { await apiVs02.putZones("runs", runId, zonesLocal, risk?.zones.revision ?? 0); setZonesLocal(null); await loadRisk(runId); }
    catch (e) {
      setZoneErr(refusalText(e));
      if (/zones_revision_conflict/.test(String(e))) await loadRisk(runId);   // the stored set changed elsewhere: refresh the revision, KEEP the unsaved geometry so the user can reconcile and save again
    }
    finally { setZoneBusy(false); }
  };
  const onStageClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!drawing.active || !binding) return;
    const b = e.currentTarget.getBoundingClientRect();
    drawing.click(e.clientX - b.left, e.clientY - b.top, containRect(size.w, size.h, binding.width, binding.height));
  };
  const riskTotalUs = evaluationTotalUs(risk?.evaluation ?? null);
  const jumpToRed = async (run_id: string) => {
    const r = await apiVs02.risk("runs", run_id);
    const red = r.evaluation.zones["Z1"]?.events.find((e) => e.severity === "RED");
    if (red && videoRef.current) { videoRef.current.pause(); videoRef.current.currentTime = (red.start_us + 300_000) / 1e6; setSimHint(true); }
  };
  useEffect(() => {
    if (!open) return;
    (async () => {
      try {
        setError(null); setSimHint(false);
        if (open.base) setSimBase(open.base);
        await loadRun(open.id);
        if (open.jumpRed) await jumpToRed(open.id);
        else if (open.tUs !== undefined && videoRef.current) { videoRef.current.pause(); videoRef.current.currentTime = open.tUs / 1e6; }   // an event opened from the incident center or the simulation lab
      } catch (e) { setError(refusalText(e)); }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open?.n]);
  const runSimulation = async () => {
    if (!runId || bundle?.observation_set.origin !== "model_inference") return;   // the base is only READ for its source binding; a simulation is never the base of another
    setSimBusy(true); setError(null); setSimHint(false);
    try {
      const { run_id } = await apiVs02.simulate(runId);
      setSimBase(runId);
      await loadRun(run_id);
      await jumpToRed(run_id);
    } catch (e) {
      setError(refusalText(e));
    } finally {
      setSimBusy(false);
    }
  };

  const [returnUs, setReturnUs] = useState<number | null>(null);   // where the playhead was before jumping to an event, so the operator can go back
  const seekToUs = (us: number) => {
    const v = videoRef.current;
    if (!v) return;
    setReturnUs(Math.round(v.currentTime * 1e6));
    v.pause();
    v.currentTime = us / 1e6;
  };
  const backFromEvent = () => { const v = videoRef.current; if (v && returnUs !== null) { v.pause(); v.currentTime = returnUs / 1e6; setReturnUs(null); } };
  const summary = bundle ? summarize(frames, minPermille) : {};
  const personCount = Object.entries(summary).find(([k]) => k === "person")?.[1] ?? 0;

  return (
    <div data-testid="view-video">
      <div className="page-head"><div><h2>{t("mg.nav.video")}</h2><p className="muted">{t("mg.vid.subtitle")}</p></div>
        {returnUs !== null && <button onClick={backFromEvent} data-testid="event-back">{t("mg.vid.back")} ({formatMs(returnUs)})</button>}</div>
      {error && <div className="error" role="alert"><b>{t("mg.ui.error")}:</b> {error}</div>}
      <div className="ws-strip" data-testid="ws-strip" role="status">
        <span className="ws-chip"><span className="ws-l">{t("mg.ws.source")}</span><b>{video ? video.filename : t("mg.ws.none")}</b></span>
        <span className="ws-chip"><span className="ws-l">{t("mg.ws.analysis")}</span><b>{running ? tf("mg.ws.analysis_running", { done: job!.done, total: job!.total }) : bundle ? t("mg.ws.analysis_ready") : t("mg.ws.analysis_none")}</b></span>
        <span className="ws-chip"><span className="ws-l">{t("mg.ws.model")}</span><b>{model?.verified ? t("mg.ws.model_ok") : t("mg.ws.model_bad")}</b></span>
      </div>
      <main>
        <section className="left">
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={mode === "video"} onClick={() => setMode("video")}>{t("mg.ui.video")}</button>
            <button role="tab" aria-selected={mode === "frame"} disabled={!shown} onClick={() => setMode("frame")}>{t("mg.ui.analyzed_frame")}</button>
          </div>
          <div className="stage" ref={stageRef} data-testid="stage" onClick={onStageClick}>
            {video && <video ref={videoRef} src={api.videoUrl(video.video_id)} preload="auto" muted playsInline style={{ visibility: mode === "video" ? "visible" : "hidden" }} />}
            {mode === "frame" && shown && runId && <img src={api.frameUrl(runId, shown.frame_index)} alt={`${t("mg.ui.frame")} ${shown.frame_index}`} />}
            <canvas ref={canvasRef} style={{ width: size.w, height: size.h }} data-testid="overlay" />
            {!video && <div className="stage-empty" data-testid="stage-empty"><p>{t("mg.ws.stage_empty")}</p></div>}
          </div>
          <div className="controls">
            <button onClick={() => (playing ? videoRef.current?.pause() : videoRef.current?.play())}>{playing ? t("mg.ui.pause") : t("mg.ui.play")}</button>
            <button onClick={() => stepAnalyzed(-1)} disabled={!analyzed.length}>{t("mg.ui.prev")}</button>
            <button onClick={() => stepAnalyzed(1)} disabled={!analyzed.length}>{t("mg.ui.next")}</button>
            <input type="range" aria-label={t("mg.xd.vid.seek")} min={0} max={Math.max(0, ptsUs.length - 1)} value={frameIdx} disabled={!ptsUs.length} onChange={(e) => seekToFrame(Number(e.target.value))} />
          </div>
          <div className="readout" data-testid="readout">
            {t("mg.ui.time")} {formatMs(timeUs)} · {t("mg.ui.frame")} {frameIdx}{binding ? ` / ${binding.frame_count - 1}` : ""}
            {bundle && (shown ? <span className={exact ? "ok" : "warn"}> · {exact ? t("mg.ui.exact") : `${t("mg.ui.stale")} (#${shown.frame_index})`}</span> : null)}
          </div>
        </section>
        <aside className="right">
          <div className="card">
            <h2>{t("mg.ui.analyze")}</h2>
            <label>{t("mg.ui.stride")}: <b>{stride}</b>
              <input type="range" min={1} max={15} value={stride} disabled={running} onChange={(e) => setStride(Number(e.target.value))} /></label>
            <label>{t("mg.ui.record_threshold")}: <b>{recordPct}%</b>
              <input type="range" min={5} max={95} step={5} value={recordPct} disabled={running} onChange={(e) => setRecordPct(Number(e.target.value))} /></label>
            {running ? (
              <>
                <progress value={job!.done} max={Math.max(job!.total, 1)} />
                <button onClick={() => api.cancel(job!.job_id)}>{t("mg.ui.cancel")}</button>
                <span className="muted"> {t("mg.ui.analyzing")} {job!.done}/{job!.total}</span>
              </>
            ) : (
              <button className="primary" onClick={start} disabled={!video || !model?.verified}>{t("mg.ui.analyze")}</button>
            )}
          </div>
          {bundle ? (
            <div className="card">
              <h2>{t("mg.ui.summary")}</h2>
              {bundle.observation_set.origin === "synthetic_scripted" && <p className="banner warn" data-testid="sim-badge" style={{ fontSize: 15, fontWeight: 700 }}>{t("mg.sim.badge")} <span style={{ fontWeight: 400 }}>{t("mg.sim.note")}</span>{simBase && <> <button onClick={() => { setSimHint(false); loadRun(simBase); }} data-testid="sim-back">{t("mg.sim.back")}</button></>}</p>}
              <p className="muted" data-testid="origin">{t("mg.origin.label")}: <b>{t(originLabelId(bundle.observation_set.origin, bundle.trust))}</b></p>
              {!orderOk && <p className="error" data-testid="bad-frame-order">{t("mg.ui.bad_frame_order")}</p>}
              {bundle.observation_set.origin !== "model_inference"
                ? <p className="warn" data-testid="origin-banner">{t(bundle.observation_set.origin === "synthetic_scripted" ? "mg.banner.synthetic_replay" : "mg.banner.human_annotated")}</p>
                : (() => {
                  const ts = trustState(bundle.trust), n = trustNoticeIds(bundle.trust);
                  return <div data-testid="trust-state" data-trust={ts}>
                    <p className={ts === "unverified" ? "warn" : ts === "session" ? "muted" : "ok"} data-testid={ts === "unverified" ? "unverified" : "trusted"}>{t(n.status)}</p>
                    {n.scope && <p className="muted" data-testid="trust-scope">{t(n.scope)}</p>}
                  </div>;
                })()}
              <label>{t("mg.ui.display_threshold")}: <b>{displayPct}%</b>
                <input type="range" min={5} max={95} step={5} value={displayPct} onChange={(e) => setDisplayPct(Number(e.target.value))} /></label>
              <ul className="summary">{Object.entries(summary).map(([k, n]) => <li key={k}>{k}: {n}</li>)}</ul>
              <p className="muted">{runId} · {frames.length} / {binding?.frame_count} {t("mg.xd.vid.frames_word")}</p>
              <a className="btn" href={api.exportUrl(runId!)} download>{t("mg.ui.export")}</a>{" "}
              <button onClick={doVerify} disabled={verifying}>{verifying ? t("mg.ui.verifying") : t("mg.ui.verify")}</button>
              {report && (
                <div className={report.ok ? "ok" : "error"} data-testid="verify">
                  <b>{report.ok ? t("mg.ui.verify_ok") : t("mg.ui.verify_fail")}</b>
                  <ul>{report.checks.map((c) => <li key={c.id}>{c.ok ? "✔" : "✘"} {c.id}</li>)}</ul>
                </div>
              )}
            </div>
          ) : <p className="muted">{t("mg.ui.no_run")}</p>}
          {bundle && bundle.observation_set.origin === "model_inference" && (
            <div className="card" data-testid="sim-card">
              <h2>{t("mg.sim.title")}</h2>
              <p className="muted">{t("mg.sim.help")}</p>
              <button onClick={runSimulation} disabled={simBusy} data-testid="sim-red">{simBusy ? t("mg.sim.running") : t("mg.sim.run")}</button>
            </div>)}
          {simHint && <p className="ok" data-testid="sim-hint">{t("mg.sim.hint")}</p>}
          {bundle && (<>
            <ZoneEditor zones={shownZones} drawing={drawing} fw={binding?.width ?? 1280} fh={binding?.height ?? 720} busy={zoneBusy} error={zoneErr} dirty={zonesLocal !== null}
              onChange={(zs) => setZonesLocal(zs)} onSave={saveZones} />
            <RiskPanel onSeek={seekToUs} evaluation={risk?.evaluation ?? null} trust={bundle.trust} origin={bundle.observation_set.origin} kind="runs" id={runId} tUs={timeUs} totalUs={riskTotalUs}
              evidenceUrl={(i) => api.frameUrl(runId!, i)} exportUrl={runId ? apiVs02.riskExportUrl("runs", runId) : null} />
          </>)}
          {shown && (
            <div className="card">
              <h2 data-testid="detections-title" data-trusted={trusted ? "yes" : "no"}>{trusted ? t("mg.ui.detections") : t("mg.ui.detections_unverified")} #{shown.frame_index}</h2>
              <table data-testid="detections" data-trusted={trusted ? "yes" : "no"}><tbody>
                {shown.objects.filter((o) => (o.confidence.permille ?? 0) >= minPermille).map((o) => (
                  <tr key={o.obj_key}>
                    <td><span className="dot" style={{ background: classColor(o.cls.label) }} />{o.cls.detector_label}</td>
                    <td>{t(`mg.cls.${o.cls.label}`)}</td>
                    <td>{((o.confidence.permille ?? 0) / 10).toFixed(1)}%</td>
                    <td className="muted">{o.bbox.x},{o.bbox.y} {o.bbox.w}×{o.bbox.h}</td>
                  </tr>))}
              </tbody></table>
              {!shown.objects.some((o) => (o.confidence.permille ?? 0) >= minPermille) && <p className="muted">{t("mg.ui.no_detections")}</p>}
            </div>
          )}
          {model && (
            <div className="card">
              <h2>{t("mg.ui.model")}</h2>
              <p className={model.verified ? "ok" : "error"}>{model.verified ? t("mg.ui.weights_verified") : t("mg.ui.weights_unverified")}</p>
              <p className="mono">{model.architecture}<br />sha256 {model.weights_sha256}</p>
              {bundle && (<>
                <h3>{t("mg.ui.runtime")}</h3>
                <p className="mono">{Object.entries(bundle.provenance.packages).filter(([, v]) => v).map(([k, v]) => `${k} ${v}`).join(" · ")}<br />
                  Python {bundle.provenance.python} · macOS {bundle.provenance.macos} · {bundle.provenance.device}</p>
              </>)}
            </div>
          )}
          <div className="card notes">
            <ul>
              {["mg.lim.image_space_only", "mg.lim.uncalibrated_confidence", "mg.lim.coco_no_excavator_class", "mg.lim.not_safety_system", "mg.lim.person_untested"]
                .map((id) => <li key={id}>{t(id)}</li>)}
            </ul>
            {personCount > 0 && <p className="warn">{tf("mg.xd.vid.person_count", { n: personCount })}</p>}
          </div>
        </aside>
      </main>
    </div>
  );
}
