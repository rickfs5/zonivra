import { useCallback, useEffect, useRef, useState } from "react";
import { api, apiV2, type FeedView, type FeedsSnapshot, type VideoInfo, type ZoneIn } from "./api";
import { drawDraft, drawLiveBoxes, drawZones } from "./draw";
import { classColor, containRect, currentRisk, nextLabel, overlapPhaseId, worstState, type Zone } from "./lib";
import { t, tf } from "./i18n";
const tr = (id: string, fallback: string) => (t(id) === id ? fallback : t(id));
import { SourceLifecycle } from "./SourceLifecycle";
import { refusalText, useZoneDrawing, ZoneEditor } from "./ZoneEditor";
import { Banner, Empty, Icon, ProvTag, RiskBadge } from "./shared/ui";

const FW = 1280, FH = 720;   // the analysis frame of every feed
const defaultZone = (): ZoneIn => ({ zone_id: "Z1", kind: "restricted", polygon: [[256, 288], [1024, 288], [1024, 684], [256, 684]], name: tf("mg.xd.feeds.zone_name", { n: 1 }) });
const STATE_ID: Record<FeedView["state"], string> = { idle: "mg.feed.state.idle", playing: "mg.feed.state.playing", paused: "mg.feed.state.paused", ended: "mg.feed.state.ended", failed: "mg.feed.state.failed", stopped: "mg.feed.state.stopped" };
const n1 = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "–" : v.toFixed(d));
const STALE_MS = 8000;

function FeedPanel({ f, onAction, onRemove, onZones, onError, expanded, onExpand }: { f: FeedView; onAction: (id: string, a: "start" | "pause" | "resume" | "stop") => void; onRemove: (id: string) => void;
  onZones: (f: FeedView, z: ZoneIn[]) => Promise<void>; onError: (e: string | null) => void; expanded: boolean; onExpand: () => void }) {
  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 320, h: 180 });
  const [shown, setShown] = useState<string | null>(null);   // the preview currently on screen: the NEXT frame is loaded off-screen first, so the panel never flickers or shows a half-loaded picture
  useEffect(() => {
    if (f.latest.index === null || !(f.state === "playing" || f.state === "paused" || f.state === "ended")) { setShown(null); return; }
    const url = apiV2.previewUrl(f.feed_id, f.latest.index);
    let live = true;
    const im = new Image();
    im.onload = () => { if (live) setShown(url); };
    im.onerror = () => undefined;                           // keep the previous picture; the next analysed frame is tried again
    im.src = url;
    return () => { live = false; };
  }, [f.latest.index, f.state, f.feed_id]);
  const [editing, setEditing] = useState(false);
  const [lifeOpen, setLifeOpen] = useState(false);
  const drawing = useZoneDrawing(FW, FH);
  const lastMove = useRef({ index: null as number | null, at: Date.now() });
  if (lastMove.current.index !== f.latest.index) lastMove.current = { index: f.latest.index, at: Date.now() };
  const stale = f.state === "playing" && !f.model_loading && Date.now() - lastMove.current.at > STALE_MS;   // playing but no new analysed frame for a while: the picture on screen is OLD
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setSize({ w: el.clientWidth, h: el.clientHeight }));
    ro.observe(el); setSize({ w: el.clientWidth, h: el.clientHeight });
    return () => ro.disconnect();
  }, []);
  const running = f.state === "playing" || f.state === "paused";
  useEffect(() => {
    const c = canvasRef.current;
    if (!c) return;
    const dpr = window.devicePixelRatio || 1;
    c.width = size.w * dpr; c.height = size.h * dpr;
    const ctx = c.getContext("2d")!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, size.w, size.h);
    const r = containRect(size.w, size.h, FW, FH);
    drawZones(ctx, f.zones, r, FW, FH, (zid) => (f.state === "playing" ? (f.risk_current ? f.risk_current[zid]?.state : f.risk[zid]?.state) ?? "UNKNOWN" : null));   // only an observed feed has a current state (review 25)
    drawDraft(ctx, drawing.draft, r, FW, FH);
    if (running) drawLiveBoxes(ctx, f.latest.objects, r, FW, FH, classColor);
  });
  // a feed that is not being observed (idle, stopped, failed) has NO current risk state: its last value is history, never shown as «verde»
  const worst = f.risk_state ?? currentRisk(f.state, Object.keys(f.risk).length ? worstState(Object.values(f.risk).map((s) => s.state)) : "UNKNOWN");
  const unknownWhy = worst === "UNKNOWN" ? (f.unknown_reason ?? (f.state === "playing" ? "mg.unknown.no_sample_yet" : `mg.unknown.${f.state}`)) : null;
  const c = f.counters;
  const dropped = c.dropped_queue_overflow + c.dropped_stale + c.dropped_stopped;
  const onStageClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!drawing.active) return;
    const b = e.currentTarget.getBoundingClientRect();
    drawing.click(e.clientX - b.left, e.clientY - b.top, containRect(size.w, size.h, FW, FH));
  };
  const zonesAsZone: Zone[] = f.zones;
  return (
    <article className="feed card" data-testid={`feed-${f.feed_id}`} data-feed-id={f.feed_id}>
      <header className="feed-head">
        <h3>{f.label}</h3>
        <span className="tag" data-testid="feed-state" data-state={f.state} title={t("mg.feed.src_state")}>{t("mg.feed.src_state")}: {t(STATE_ID[f.state])}</span>
        <span className={`badge badge-${worst}`} data-testid="feed-risk" data-state={worst} title={t("mg.feed.risk_now")}>{t("mg.feed.risk_now")}: {t(`mg.risk.badge.${worst}`)}</span>
        <span style={{ marginLeft: "auto" }}><button onClick={onExpand} data-testid="feed-expand" aria-label={`${expanded ? t("mg.feed.collapse") : t("mg.feed.expand")}: ${f.label}`}><Icon name={expanded ? "back" : "expand"} size={14} /> {expanded ? t("mg.feed.collapse") : t("mg.feed.expand")}</button></span>
      </header>
      <p className="muted feed-src" data-testid="feed-source" data-provenance={f.source.provenance}>
        {f.source.repeated_source ? <span className="tag rep">{t("mg.feed.repeated")}</span> : <span className="tag">{t("mg.feed.file_source")}</span>} {f.source.filename}</p>
      <div className="stage" ref={stageRef} onClick={onStageClick} data-testid="feed-stage" style={{ aspectRatio: "16/9" }}>
        {shown !== null
          ? <img src={shown} alt="" data-testid="feed-preview" />
          : <div className="muted feed-empty">{running ? (f.model_loading ? t("mg.feed.model_loading") : t("mg.feed.waiting")) : t("mg.feed.no_image")}</div>}
        <canvas ref={canvasRef} style={{ width: size.w, height: size.h }} data-testid="feed-overlay" />
        <span className={`live-flag ${stale ? "stale" : f.state === "playing" ? "on" : ""}`} data-testid="feed-flag" data-flag={f.state === "failed" ? "error" : stale ? "stale" : f.state === "playing" ? "live" : f.state}>
          {f.state === "failed" ? t("mg.feed.flag.error") : stale ? t("mg.feed.flag.stale") : f.state === "playing" ? t("mg.feed.flag.live") : f.state === "paused" ? t("mg.feed.flag.paused") : t("mg.feed.flag.stopped")}</span>
      </div>
      {stale && <p className="stale-note" data-testid="feed-stale">{t("mg.feed.stale_note")}</p>}
      {unknownWhy && <p className="muted unknown-why" data-testid="feed-unknown-why" data-reason={unknownWhy}>{t(unknownWhy)}{f.last_observed_risk && f.last_observed_risk !== "UNKNOWN" ? ` ${t("mg.feed.last_observed")}: ${t(`mg.risk.badge.${f.last_observed_risk}`)} (${t("mg.feed.historical")}).` : ""}</p>}
      <div className="controls">
        {f.state === "idle" || f.state === "ended" || f.state === "stopped" || f.state === "failed"
          ? <button className="primary" onClick={() => onAction(f.feed_id, "start")} data-testid="feed-start">{f.state === "idle" ? t("mg.feed.start") : t("mg.feed.restart")}</button> : null}
        {f.state === "playing" && <button onClick={() => onAction(f.feed_id, "pause")} data-testid="feed-pause">{t("mg.feed.pause")}</button>}
        {f.state === "paused" && <button onClick={() => onAction(f.feed_id, "resume")} data-testid="feed-resume">{t("mg.feed.resume")}</button>}
        {running && <button onClick={() => onAction(f.feed_id, "stop")} data-testid="feed-stop">{t("mg.feed.stop")}</button>}
        <button onClick={() => setEditing((v) => !v)} data-testid="feed-zones-toggle">{t("mg.feed.zones")}</button>
        {!running && <button onClick={() => onRemove(f.feed_id)} data-testid="feed-remove">{t("mg.feed.remove")}</button>}
      </div>
      {f.state === "failed" && <p className="error" role="alert" data-testid="feed-error">{t("mg.feed.failed")}: {f.error_detail ?? f.error}{f.source_retries ? ` · ${t("mg.feed.retries")}: ${f.source_retries}` : ""}</p>}
      {f.state !== "failed" && f.source_retries > 0 && <p className="warn" data-testid="feed-retries">{t("mg.feed.recovered")} ({f.source_retries})</p>}
      <div className="metric-row" data-testid="feed-stats" data-analyzed={c.analyzed} data-dropped={dropped} data-fps={f.fps ?? ""}>
        <div className="metric"><b>{f.config.sample_fps}/s</b><span>{t("mg.feed.m.sampling")}</span></div>
        <div className="metric"><b>{n1(f.fps, 2)}/s</b><span>{t("mg.feed.m.inference")}</span></div>
      </div>
      {f.config.mode === "throttled" && <p className="muted">{t("mg.feed.note_throttled")}</p>}
      {dropped > 0 && f.config.mode === "realtime" && <p className="warn" data-testid="feed-backpressure">{t("mg.feed.note_backpressure")} ({dropped})</p>}
      <details className="eng" data-testid="feed-eng"><summary>{t("mg.feed.eng")}</summary>
      <div className="metric-row">
        <div className="metric"><b>{n1(f.latency_ms.p50, 0)} / {n1(f.latency_ms.p95, 0)} ms</b><span>{t("mg.feed.m.latency")}</span></div>
        <div className="metric"><b>{f.queue_depth}/{f.queue_max}</b><span>{t("mg.feed.m.queue")}</span></div>
      </div>
      <p className="muted" data-testid="feed-counters">{t("mg.feed.counters")}: {c.decoded} / {c.offered} / {c.analyzed} · {t("mg.feed.dropped")} {c.dropped_queue_overflow}+{c.dropped_stale} · {t("mg.feed.skipped")} {c.skipped_by_sampling} · {t("mg.feed.errors")} {c.adapter_error}</p></details>
      <details className="eng" data-testid="feed-life" onToggle={(e) => setLifeOpen((e.target as HTMLDetailsElement).open)}><summary>{t("mg.feed.life")}{f.lifecycle_last ? ` · ${tr(`mg.inv.tl.${f.lifecycle_last.kind}`, f.lifecycle_last.kind)} ${f.lifecycle_last.at_utc.slice(11, 19)}` : ` · ${t("mg.inv.comp.not_recorded")}`}</summary>
        {lifeOpen && <SourceLifecycle feedId={f.feed_id} state={f.state} runKey={`${f.lifecycle_last?.at_utc ?? ""}|${worst}|${f.counters.analyzed > 0 ? 1 : 0}`} />}</details>
      {f.latest_event && <p data-testid="feed-latest-event"><span className={`badge badge-${f.latest_event.severity}`}>{t(`mg.risk.badge.${f.latest_event.severity}`)}</span> {f.latest_event.zone_id} · {(f.latest_event.start_us / 1e6).toFixed(1)} s {f.latest_event.open ? `· ${t("mg.xd.feeds.open")}` : ""}</p>}
      {Object.entries(f.risk).map(([zid, s]) => { const cur = currentRisk(f.state, s.state); return <p key={zid} className="muted" data-testid={`feed-zone-${zid}`} data-state={cur}><span className={`badge badge-${cur}`}>{t(`mg.risk.badge.${cur}`)}</span> {zid}{cur !== s.state ? ` · ${t("mg.feed.last_observed")}: ${t(`mg.risk.badge.${s.state}`)} (${t("mg.feed.historical")})` : ""}{s.causes.length ? ` · ${s.causes.join(", ")}` : ""}</p>; })}
      {editing && <ZoneEditor zones={zonesAsZone} drawing={drawing} fw={FW} fh={FH} busy={false} error={null} dirty={false} hideSave
        onChange={(z) => { onError(null); return onZones(f, z); }} onSave={() => undefined} />}
    </article>
  );
}

export function Feeds({ focusFeed, onFocusHandled }: { focusFeed?: string | null; onFocusHandled?: () => void } = {}) {
  const [snap, setSnap] = useState<FeedsSnapshot | null>(null);
  const [videos, setVideos] = useState<VideoInfo[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [vid, setVid] = useState("");
  const [labelEdit, setLabel] = useState<string | null>(null);   // null = the default name of the ACTIVE language
  const label = labelEdit ?? t("mg.xd.lib.source_default");
  const [fps, setFps] = useState(2);
  const [mode, setMode] = useState<"realtime" | "throttled">("realtime");
  const [loop, setLoop] = useState(true);
  const [defZone, setDefZone] = useState(true);
  const [ack, setAck] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  useEffect(() => { if (focusFeed) { setExpanded(focusFeed); onFocusHandled?.(); } }, [focusFeed, onFocusHandled]);
  const chain = useRef<Promise<unknown>>(Promise.resolve());   // operator commands run one after another; none is dropped
  const poll = useCallback(async () => { try { setSnap(await apiV2.feeds()); } catch (e) { setError(refusalText(e)); } }, []);
  useEffect(() => { void poll(); const h = window.setInterval(() => void poll(), 800); return () => window.clearInterval(h); }, [poll]);
  useEffect(() => { void api.videos().then((v) => { setVideos(v); setVid((cur) => cur || v[0]?.video_id || ""); }).catch((e) => setError(refusalText(e))); }, []);
  const guard = (fn: () => Promise<unknown>) => {
    const run = chain.current.then(async () => { try { await fn(); setError(null); } catch (e) { setError(refusalText(e)); } finally { await poll(); } });
    chain.current = run;
    return run;
  };
  const body = (taken: { label: string }[]) => ({ video_id: vid, label: nextLabel(label, taken.length + 1, taken), sample_fps: fps, threshold: 0.3, mode, speed: 1, loop, zones: defZone ? [defaultZone()] : [] });
  const addOne = () => guard(async () => { await apiV2.createFeed(body(snap?.feeds ?? [])); });
  const addThree = () => guard(async () => { const taken: { label: string }[] = [...(snap?.feeds ?? [])]; for (let i = 0; i < 3; i++) { const b = body(taken); await apiV2.createFeed(b); taken.push({ label: b.label }); } });
  const action = (id: string, a: "start" | "pause" | "resume" | "stop") => guard(async () => { await apiV2.feedAction(id, a); });
  const remove = (id: string) => guard(async () => { await apiV2.removeFeed(id); });
  // A zone save REJECTS on failure (the editor keeps the drawn polygon); it runs in the same ordered chain as every other command and refreshes the feed afterwards, so a retry after a
  // revision conflict uses the new revision.
  const zones = (f: FeedView, z: ZoneIn[]): Promise<void> => {
    const run = chain.current.then(() => apiV2.putFeedZones(f.feed_id, z, f.zones_revision)).then(() => poll(), async (e) => { await poll(); throw e; });
    chain.current = run.catch(() => undefined);
    return run as Promise<void>;
  };
  const startAll = () => guard(async () => { for (const f of snap?.feeds ?? []) if (f.state === "idle" || f.state === "ended" || f.state === "stopped") await apiV2.feedAction(f.feed_id, "start"); });
  const stopAll = () => guard(async () => { for (const f of snap?.feeds ?? []) if (f.state === "playing" || f.state === "paused") await apiV2.feedAction(f.feed_id, "stop"); });
  const capacity = (n: number) => guard(async () => { await apiV2.setCapacity(n, ack); });
  const feeds = snap?.feeds ?? [];
  const repeated = feeds.some((f) => f.source.repeated_source);
  return (
    <section data-testid="view-feeds" aria-labelledby="h-feeds">
      <div className="page-head"><div><h2 id="h-feeds">{t("mg.nav.feeds")}</h2><p className="muted">{t("mg.feed.intro")}</p></div><ProvTag kind={repeated ? "repeated" : "real"} /></div>
      <Banner kind="info">{t("mg.feed.fps_note")}</Banner>
      {error && <div className="error" role="alert" data-testid="feeds-error">{error}</div>}
      {!expanded && <div className="card controls" data-testid="feeds-form">
        <label>{t("mg.feed.video")}: <select value={vid} onChange={(e) => setVid(e.target.value)} data-testid="feeds-video">{videos.map((v) => <option key={v.video_id} value={v.video_id}>{v.filename}</option>)}</select></label>{" "}
        <label>{t("mg.feed.label")}: <input value={label} maxLength={40} onChange={(e) => setLabel(e.target.value)} data-testid="feeds-label" /></label>{" "}
        <label>{t("mg.feed.rate")}: <b>{fps} /s</b> <input type="range" min={0.5} max={5} step={0.5} value={fps} onChange={(e) => setFps(Number(e.target.value))} data-testid="feeds-fps" /></label>{" "}
        <label>{t("mg.feed.mode")}: <select value={mode} onChange={(e) => setMode(e.target.value as "realtime" | "throttled")} data-testid="feeds-mode"><option value="realtime">{t("mg.feed.mode.realtime")}</option><option value="throttled">{t("mg.feed.mode.throttled")}</option></select></label>{" "}
        <label><input type="checkbox" checked={loop} onChange={(e) => setLoop(e.target.checked)} /> {t("mg.feed.loop")}</label>{" "}
        <label><input type="checkbox" checked={defZone} onChange={(e) => setDefZone(e.target.checked)} /> {t("mg.feed.default_zone")}</label>{" "}
        <button className="primary" onClick={() => void addOne()} disabled={!vid || feeds.length >= (snap?.max_feeds ?? 5)} data-testid="feeds-add">{t("mg.feed.add")}</button>{" "}
        <button onClick={() => void addThree()} disabled={!vid || feeds.length > 2} data-testid="feeds-create3">{t("mg.feed.add3")}</button>
        <p className="muted">{t("mg.feed.cameras_note")}</p>
      </div>}
      {snap && (
        <div className="controls" data-testid="feeds-bar">
          <span data-testid="feeds-capacity" data-active={snap.active} data-limit={snap.active_limit}>{t("mg.feed.active")}: <b>{snap.active}</b> / {snap.active_limit} · {t("mg.feed.validated")}: {snap.validated_feeds} · {t("mg.feed.aggregate")}: {n1(snap.scheduler.aggregate_fps, 2)} /s</span>{" "}
          <button onClick={() => void startAll()} disabled={!feeds.length} data-testid="feeds-start-all">{t("mg.feed.start_all")}</button>{" "}
          <button onClick={() => void stopAll()} disabled={!snap.active} data-testid="feeds-stop-all">{t("mg.feed.stop_all")}</button>{" "}
          <label className="muted"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} data-testid="feeds-ack" /> {t("mg.feed.unvalidated_ack")}</label>{" "}
          {[4, 5].map((n) => <button key={n} onClick={() => void capacity(n)} disabled={!ack || snap.active_limit === n} data-testid={`feeds-cap-${n}`}>{t("mg.feed.allow")} {n}</button>)}{" "}
          {snap.active_limit !== 3 && <button onClick={() => void capacity(3)} data-testid="feeds-cap-3">{t("mg.feed.back_to")} 3</button>}
        </div>)}
      {repeated && <p className="banner warn" data-testid="feeds-repeated-banner">{t("mg.feed.repeated_banner")}</p>}
      {snap?.scheduler.detector_error && <p className="error" role="alert">{t("mg.feed.model_error")}: {snap.scheduler.detector_error}</p>}
      {!snap && !error && <p className="loading">{t("mg.ov.loading")}</p>}
      {feeds.length === 0 ? (snap ? <Empty testid="feeds-empty">{t("mg.feed.none")}</Empty> : null) : (() => {
        const shownFeeds = expanded ? feeds.filter((f) => f.feed_id === expanded) : feeds;
        const ex = expanded ? feeds.find((f) => f.feed_id === expanded) : undefined;
        return <>
          {expanded && !ex && <Banner kind="warn">{t("mg.feed.gone")}</Banner>}
          {ex && <p className="controls"><button onClick={() => setExpanded(null)} data-testid="feeds-back-grid"><Icon name="back" size={14} /> {t("mg.feed.back_grid")}</button><span className="muted">{ex.label}</span></p>}
          <div className={`feed-grid ${ex ? "expanded" : ""}`} data-testid="feeds-grid">{(ex ? [ex] : shownFeeds).map((f) => <FeedPanel key={f.feed_id} f={f} expanded={!!ex} onExpand={() => setExpanded(ex ? null : f.feed_id)} onAction={action} onRemove={remove} onZones={zones} onError={setError} />)}</div>
          {ex && <FeedDetail f={ex} />}</>;
      })()}
    </section>
  );
}

/** Per-feed detail for the expanded view: everything is THIS feed's own state (a feed never shares counters, zones or tracks with another). */
function FeedDetail({ f }: { f: FeedView }) {
  const c = f.counters;
  return (
    <div className="grid cols-2" data-testid="feed-detail" data-feed-id={f.feed_id}>
      <section className="card"><h3>{t("mg.feed.d.config")}</h3>
        <dl className="kv"><dt>{t("mg.feed.m.sampling")}</dt><dd>{tf("mg.xd.feeds.d.sampling", { fps: f.config.sample_fps, ms: f.config.interval_ms })}</dd><dt>{t("mg.feed.mode")}</dt><dd>{f.config.mode === "realtime" ? t("mg.feed.mode.realtime") : t("mg.feed.mode.throttled")}</dd>
          <dt>{t("mg.xd.feeds.d.speed")}</dt><dd>×{f.config.speed}</dd><dt>{t("mg.xd.feeds.d.threshold")}</dt><dd>{tf("mg.xd.feeds.d.threshold_val", { pct: f.config.threshold_permille / 10 })}</dd><dt>{t("mg.xd.feeds.d.repeat")}</dt><dd>{tf("mg.xd.feeds.d.loop_val", { v: f.config.loop ? t("mg.xd.common.yes") : t("mg.xd.common.no"), passes: f.passes })}</dd>
          <dt>{t("mg.xd.feeds.d.source")}</dt><dd><ProvTag kind={f.source.repeated_source ? "repeated" : "real"} /> {f.source.filename}</dd></dl>
        <p className="muted">{t("mg.feed.source_note")}</p></section>
      <section className="card"><h3>{t("mg.feed.d.accounting")}</h3>
        <dl className="kv"><dt>{t("mg.xd.feeds.d.decoded")}</dt><dd>{c.decoded}</dd><dt>{t("mg.xd.feeds.d.skipped")}</dt><dd>{c.skipped_by_sampling} {t("mg.xd.feeds.d.intentional")}</dd><dt>{t("mg.xd.feeds.d.offered")}</dt><dd>{c.offered}</dd><dt>{t("mg.xd.feeds.d.analyzed")}</dt><dd>{c.analyzed}</dd>
          <dt>{t("mg.xd.feeds.d.dropped")}</dt><dd>{c.dropped_queue_overflow} / {c.dropped_stale} / {c.dropped_stopped}</dd><dt>{t("mg.xd.feeds.d.adapter_errors")}</dt><dd>{c.adapter_error}</dd><dt>{t("mg.xd.feeds.d.detections")}</dt><dd>{c.detections}</dd>
          <dt>{t("mg.xd.feeds.d.max_queue")}</dt><dd>{f.max_queue_depth}/{f.queue_max}</dd><dt>{t("mg.xd.feeds.d.queue_wait")}</dt><dd>{n1(f.queue_wait_ms.p50, 0)} / {n1(f.queue_wait_ms.p95, 0)} ms</dd></dl></section>
      <section className="card" data-testid="feed-provenance"><h3>{t("mg.feed.d.provenance")}</h3>
        <dl className="kv"><dt>{t("mg.feed.d.launch")}</dt><dd data-testid="feed-launch">{f.launch_repeated === null || f.launch_repeated === undefined ? "—" : f.launch_repeated ? t("mg.feed.d.launch_rep") : t("mg.feed.d.launch_alone")}</dd>
          <dt>{t("mg.feed.d.now")}</dt><dd data-testid="feed-shared-now">{f.shared_now ? t("mg.feed.d.now_shared") : t("mg.feed.d.now_alone")}</dd>
          <dt>{t("mg.feed.d.evidence")}</dt><dd data-testid="feed-evidence-prov" data-prov={f.source.provenance}>{f.source.provenance === "repeated_source_simulation" ? t("mg.feed.d.evidence_rep") : t("mg.feed.d.evidence_real")}</dd>
          <dt>{t("mg.feed.d.during")}</dt><dd data-testid="feed-shared-run">{f.shared_during_run ? t("mg.feed.d.during_yes") : t("mg.feed.d.during_no")}</dd>
          <dt>{t("mg.ov.history")}</dt><dd data-testid="feed-overlap-history">{!f.overlap || f.overlap.intervals.length === 0 ? t("mg.ov.none") : <ul>{f.overlap.intervals.map((i, k) => <li key={k}>{i.from_after_us === null ? t("mg.ov.from_start") : `${t("mg.ov.interval")} ${(i.from_after_us / 1e6).toFixed(1)} s`} → {i.until_before_us === null ? t("mg.ov.ongoing") : `${t("mg.ov.until")} ${(i.until_before_us / 1e6).toFixed(1)} s`}</li>)}</ul>}{f.overlap?.truncated && <p className="warn" data-testid="feed-overlap-truncated">{t("mg.ov.truncated")}</p>}</dd></dl>
        <p className="muted">{t("mg.feed.source_note")}</p></section>
      <section className="card"><h3>{t("mg.feed.d.tracks")}</h3>
        <p className="muted">{tf("mg.xd.feeds.d.classes", { list: Object.entries(f.class_hist).map(([k, v]) => `${k} ${v}`).join(", ") || "—" })}</p><p className="muted">{tf("mg.xd.feeds.d.tracks_unique", { list: Object.entries(f.tracks_unique).map(([k, v]) => `${k} ${v}`).join(", ") || "—" })}</p></section>
      <section className="card"><h3>{t("mg.risk.events")}</h3>
        {f.events.length === 0 ? <p className="muted">{t("mg.risk.no_events")}</p> : <ul className="events">{f.events.slice(-8).map((e) => <li key={e.event_id} data-phase={(e.overlap_phase ?? "unknown")}><RiskBadge state={e.severity} /> {e.zone_id} · {(e.start_us / 1e6).toFixed(1)} s{e.open ? ` · ${t("mg.xd.feeds.open")}` : ""} <span className="muted">· {t(overlapPhaseId(e.overlap_phase))}</span></li>)}</ul>}</section>
    </div>
  );
}
