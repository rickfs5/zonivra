import { useCallback } from "react";
import { apiVs02, type Overview } from "../api";
import { useMonitor } from "../app/monitorStore";
import { OperatorPanel } from "../Dashboards";
import { useAlerts } from "../Alerts";
import { t, tf } from "../i18n";
import { getLocale } from "../locale";
import { currentRisk, incidentProvenance } from "../lib";
import { useLoad } from "../shared/hooks";
import { Banner, Empty, fmtNum, fmtUs, Kpi, Loading, Panel, ProvTag, RiskBadge, type Provenance, type Risk } from "../shared/ui";
import { EvidenceLegend, OverviewHero } from "./Hero";
import { heroFacts } from "./heroState";

const provOf = (provenance: string, simulation: boolean): Provenance => incidentProvenance({ provenance, simulation });

/** Operations Overview: only values the API returned (nothing is invented); genuine and synthetic incidents are always separate; no zero is presented as proof of safety. */
export function Resumen({ go, openFeed, openAlert }: { go: (s: string) => void; openFeed: (id: string) => void; openAlert: (id: string) => void }) {
  const mon = useMonitor();
  const { list } = useAlerts();
  const fn = useCallback(() => apiVs02.overview(), []);
  const { data: o, error } = useLoad<Overview>(fn);
  const m = mon.data;
  const genuineOpen = list.filter((a) => !a.simulation && !a.loaded_from_store && a.status !== "resolved");     // restored records are never counted as genuine live alerts (MG-TRUST-026)
  const unacked = genuineOpen.filter((a) => a.status === "new" || a.status === "active").length;
  const simOpen = list.filter((a) => a.simulation && !a.loaded_from_store && a.status !== "resolved").length;
  const restoredOpen = list.filter((a) => a.loaded_from_store && a.status !== "resolved").length;
  const dist: Record<Risk, number> = { GREEN: 0, YELLOW: 0, RED: 0, UNKNOWN: 0 };
  const riskOf = (f: { risk: string; state: string }): Risk => currentRisk(f.state, f.risk as Risk);
  (m?.feeds ?? []).forEach((f) => { dist[riskOf(f)] += 1; });
  const r = m?.health.resources;
  return (
    <section data-testid="view-resumen" aria-labelledby="h-resumen">
      <div className="page-head"><div><h2 id="h-resumen">{t("mg.nav.resumen")}</h2><p className="muted">{t("mg.ov.subtitle")}</p></div>
        <span className="muted" data-testid="ov-updated">{mon.at ? `${t("mg.ov.updated")} ${new Date(mon.at).toLocaleTimeString(getLocale())}` : ""}{mon.stale ? ` · ${t("mg.shell.stale")}` : ""}</span></div>
      <p className="banner warn" data-testid="limits">{t("mg.xd.ov.limits")} {t("mg.ov.red_meaning")}</p>
      {(error || mon.error) && <div className="error" role="alert">{error ?? mon.error}</div>}
      {!m ? <><div className="hero hero-skeleton" aria-hidden="true"><i /><i /><i /></div><Loading what={t("mg.ov.loading")} /></> : <>
        <OverviewHero facts={heroFacts({ activeFeeds: m.active_feeds, activeLimit: m.active_limit, validatedFeeds: m.validated_feeds, schedulerAlive: m.health.scheduler_alive, modelLoaded: m.health.model_loaded,
          detectorError: m.health.scheduler_detector_error, activeSession: !!o?.active_session, feedsFailed: m.health.feeds_failed })} modelId={m.detector.model_id} go={go} />
        <div className="grid cols-4" data-testid="ov-kpis">
          <Kpi label={t("mg.ov.kpi.sources")} value={<>{m.active_feeds}<small className="muted"> / {m.active_limit}</small></>} note={`${t("mg.feed.validated")}: ${m.validated_feeds}`} testid="kpi-sources" />
          <Kpi label={t("mg.ov.kpi.unacked")} value={unacked} tone={unacked ? "red" : undefined} note={t("mg.ov.kpi.unacked_note")} testid="kpi-unacked" />
          <Kpi label={t("mg.ov.kpi.sim")} value={simOpen} tone={simOpen ? "yellow" : undefined} note={t("mg.ov.kpi.sim_note")} testid="kpi-sim" />
          <Kpi label={t("mg.ov.kpi.restored")} value={restoredOpen} tone={restoredOpen ? "yellow" : undefined} note={t("mg.ov.kpi.restored_note")} testid="kpi-restored" />
          <Kpi label={t("mg.ov.kpi.worker")} value={m.health.scheduler_alive ? t("mg.shell.status.alive") : m.active_feeds === 0 ? t("mg.shell.status.idle") : t("mg.shell.status.dead")} tone={m.health.scheduler_alive ? "green" : m.active_feeds === 0 ? undefined : "red"}
            note={`${t("mg.shell.status.model")}: ${m.health.model_loaded ? t("mg.shell.status.loaded") : t("mg.shell.status.notloaded")}`} testid="kpi-worker" />
        </div>
        <div className="grid main-side" style={{ marginTop: 14 }}>
          <Panel title={t("mg.ov.sources")} testid="ov-sources" actions={<button onClick={() => go("feeds")}>{t("mg.nav.feeds")}</button>}>
            {m.feeds.length === 0 ? <Empty testid="ov-no-sources">{t("mg.ov.no_sources")}<p className="controls"><button className="primary" onClick={() => go("feeds")} data-testid="ov-go-feeds">{t("mg.ov.go_feeds")}</button><button onClick={() => go("video")} data-testid="ov-go-video">{t("mg.ov.go_video")}</button><button onClick={() => go("simulaciones")} data-testid="ov-go-sim">{t("mg.ov.go_sim")}</button></p></Empty> : (
              <div className="grid cols-2">{m.feeds.map((f) => (
                <button key={f.feed_id} className="chip" style={{ textAlign: "left" }} onClick={() => openFeed(f.feed_id)} data-testid="ov-feed-tile" data-feed-id={f.feed_id} aria-label={`${f.label}: ${t("mg.ov.open_source")}`}>
                  <b>{f.label}</b> <RiskBadge state={riskOf(f)} /> <ProvTag kind={provOf(f.provenance, false)} />
                  <div className="muted">{t(`mg.feed.state.${f.state}`)} · {t("mg.feed.measured_fps")} {fmtNum(f.fps, 2)} · p50/p95 {fmtNum(f.latency_ms.p50, 0)}/{fmtNum(f.latency_ms.p95, 0)} ms</div>
                  {riskOf(f) === "UNKNOWN" && f.unknown_reason && <div className="muted" data-testid="ov-unknown-why">{t(f.unknown_reason)}</div>}
                  {f.unknown_zones.length > 0 && <div className="warn">{t("mg.ov.unobserved")}: {f.unknown_zones.join(", ")}</div>}
                  <dl className="kv tile-facts" data-testid="ov-tile-facts">
                    <dt>{t("mg.ov.t.id")}</dt><dd className="mono" data-testid="ov-tile-source-id">{f.source_id}</dd>
                    <dt>{t("mg.ov.t.kind")}</dt><dd>{f.source_kind === "mp4_file" ? t("mg.ov.t.kind_mp4") : f.source_kind}</dd>
                    <dt>{t("mg.ov.t.detector")}</dt><dd data-testid="ov-tile-detector">{m.detector.model_id ?? t("mg.notmeasured")}{f.provenance === "real_inference" ? "" : ` · ${t("mg.ov.t.sim")}`}</dd>
                    <dt>{t("mg.ov.t.last_obs")}</dt><dd data-testid="ov-tile-fresh" data-fresh={f.observation.fresh ? "yes" : "no"}>{f.last_observation_source_us === null ? t("mg.ov.t.never") : `${fmtUs(f.last_observation_source_us)} · ${f.observation.fresh ? t("mg.ov.t.fresh") : t("mg.ov.t.stale")}`}</dd>
                    <dt>{t("mg.ov.t.counts")}</dt><dd data-testid="ov-tile-counts">{f.counts_latest ? `${f.counts_latest.person} ${t("mg.ov.t.person")} · ${f.counts_latest.vehicle} ${t("mg.ov.t.vehicle")}` : t("mg.ov.t.counts_na")}</dd>
                    <dt>{t("mg.ov.t.zone")}</dt><dd>{f.zone_ids.length ? f.zone_ids.join(", ") : t("mg.ov.t.nozone")}</dd>
                    <dt>{t("mg.ov.t.life")}</dt><dd data-testid="ov-tile-life" data-kind={f.lifecycle_last?.kind ?? "none"}>{f.lifecycle_last ? `${t(`mg.inv.tl.${f.lifecycle_last.kind}`)} · ${f.lifecycle_last.at_utc.slice(11, 19)}` : t("mg.inv.comp.not_recorded")}</dd>
                    <dt>{t("mg.ov.t.incidents")}</dt><dd data-testid="ov-tile-incidents">{f.incidents_open}</dd>
                  </dl>
                  <div className={f.duplicate === "active_overlap" ? "warn" : "muted"} data-testid="ov-tile-dup" data-dup={f.duplicate}>{t(`mg.ov.t.dup.${f.duplicate}`)}</div>
                  {f.state !== "playing" && f.last_observed_risk && <div className="muted" data-testid="ov-last-risk">{t("mg.feed.last_observed")}: {t(`mg.risk.badge.${f.last_observed_risk}`)} ({t("mg.feed.historical")})</div>}
                </button>))}</div>)}
            <p className="muted" style={{ marginTop: 8 }} data-testid="ov-risk-dist">{t("mg.ov.dist")}: {(["RED", "YELLOW", "UNKNOWN", "GREEN"] as Risk[]).map((k) => <span key={k} style={{ marginRight: 8 }}><RiskBadge state={k} /> {dist[k]}</span>)}</p>
            <p className="muted">{t("mg.ov.zero_not_safe")}</p>
          </Panel>
          <Panel title={t("mg.ov.recent")} testid="ov-recent" actions={<button onClick={() => go("eventos")}>{t("mg.nav.eventos")}</button>}>
            {m.recent_timeline.length === 0 ? <Empty testid="ov-recent-empty">{t("mg.ov.no_recent")}</Empty> : (
              <ul className="events">{m.recent_timeline.slice(0, 6).map((e) => (
                <li key={e.alert_id}><button className="link" onClick={() => openAlert(e.alert_id)} data-testid="ov-recent-item" data-alert-id={e.alert_id}>
                  <span className="mono">{e.created_utc.slice(11, 19)}</span></button>{" "}
                  <span className={`badge badge-${e.severity}`}>{t(`mg.risk.badge.${e.severity}`)}</span> {e.source} · {e.zone_id} · {t(`mg.alert.status.${e.status}`)} <ProvTag kind={incidentProvenance(e)} /></li>))}</ul>)}
          </Panel>
        </div>
        <Panel title={t("mg.ov.health")} testid="ov-health">
          {r?.measured ? <p className="muted" data-testid="ov-resources">{t("mg.ov.measured_at")} {r.at_utc}: {t("mg.ov.proc")} {fmtNum(r.process_rss_mb, 0)} MB · {t("mg.ov.free")} {r.host_free_percent ?? t("mg.notmeasured")}% · {t("mg.ov.pressure")} {r.host_pressure_level ?? t("mg.notmeasured")} · swap {fmtNum(r.swap_used_mb, 0)} MB</p> : <p className="muted">{t("mg.notmeasured")}</p>}
          {r?.measured && (() => {
            const rows: [string, string, boolean | null][] = [
              [t("mg.ov.pressure"), `${r.host_pressure_level ?? t("mg.notmeasured")} (${tf("mg.xd.ov.required", { req: "1" })})`, r.host_pressure_level === null ? null : r.host_pressure_level === 1],
              ["swap", `${fmtNum(r.swap_used_mb, 0)} MB (${tf("mg.xd.ov.required", { req: "< 1024" })})`, r.swap_used_mb === null ? null : r.swap_used_mb < 1024],
              [t("mg.ov.free"), `${r.host_free_percent ?? t("mg.notmeasured")} % (${tf("mg.xd.ov.required", { req: "≥ 60" })})`, r.host_free_percent === null ? null : r.host_free_percent >= 60]];
            const blocked = rows.some(([, , ok]) => ok === false);
            return <div data-testid="ov-resource-gate" data-blocked={blocked ? "yes" : "no"}>
              <Banner kind={blocked ? "warn" : "info"}>{blocked ? t("mg.ov.res_blocked") : t("mg.ov.res_ok")}</Banner>
              <ul className="muted">{rows.map(([k, v, ok]) => <li key={k}>{ok === null ? "?" : ok ? "✔" : "✘"} {k}: {v}</li>)}</ul></div>;
          })()}
          {m.health.feeds_failed > 0 && <Banner kind="err">{m.health.feeds_failed} {t("mg.ov.failed_feeds")}</Banner>}
          {m.health.scheduler_detector_error && <Banner kind="err">{t("mg.feed.model_error")}: {m.health.scheduler_detector_error}</Banner>}
        </Panel></>}
      <Panel title={t("mg.ov.inventory")} testid="ov-inventory">
        {!o ? <Loading what={t("mg.ov.loading")} /> : <>
          <p data-testid="ov-privacy">{t("mg.xd.ov.privacy_pre")} <b>{t("mg.xd.ov.privacy_on")}</b> {t("mg.xd.ov.privacy_post")}</p>
          <p data-testid="ov-active">{o.active_session ? <>{t("mg.xd.ov.active_session")} <code>{o.active_session.session_id}</code> — {o.active_session.retention === "frames" ? t("mg.xd.ov.frames_retained") : t("mg.xd.ov.no_images_stored")}</> : t("mg.xd.ov.no_active_session")}</p>
          <ul>
            <li>{tf("mg.xd.ov.inv.runs", { n: o.sources.runs })}</li><li>{tf("mg.xd.ov.inv.sessions", { n: o.sources.sessions })}</li><li>{tf("mg.xd.ov.inv.simulations", { n: o.sources.simulations })}</li><li>{tf("mg.xd.ov.inv.annotated", { n: o.sources.annotated })}</li>
            <li data-testid="ov-real">{tf("mg.xd.ov.inv.real", { red: o.events.real.RED, yellow: o.events.real.YELLOW })}</li>
            <li data-testid="ov-ann">{tf("mg.xd.ov.inv.ann", { red: o.events.human_annotated.RED, yellow: o.events.human_annotated.YELLOW })}</li>
            <li data-testid="ov-unv">{tf("mg.xd.ov.inv.unv", { n: o.sources.unverified, red: o.events.unverified.RED, yellow: o.events.unverified.YELLOW })}</li>
            <li data-testid="ov-syn">{tf("mg.xd.ov.inv.syn", { red: o.events.synthetic.RED, yellow: o.events.synthetic.YELLOW })}</li>
          </ul>
          <p className="muted">{tf("mg.xd.ov.inv.engine", { engine: o.engine.risk_engine, model: o.model.architecture, tracker: o.engine.tracker })}</p></>}
      </Panel>
      <OperatorPanel go={go} />
      <EvidenceLegend />
    </section>
  );
}
