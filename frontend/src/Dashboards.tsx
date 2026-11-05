import { useCallback, useEffect, useState } from "react";
import { apiV2, type AlertSettings, type DeveloperOverview, type OperatorOverview } from "./api";
import { beep, unlockAudio, usePrefs } from "./Alerts";
import { refusalText } from "./ZoneEditor";
import { t, tEnum, tf } from "./i18n";
import { alertProvenance } from "./lib";
import { ProvTag } from "./shared/ui";

const dash = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "–" : v.toFixed(d));
const RISK_CLASS: Record<string, string> = { RED: "badge-RED", YELLOW: "badge-YELLOW", GREEN: "badge-GREEN", UNKNOWN: "badge-UNKNOWN" };

function usePoll<T>(fn: () => Promise<T>, ms: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => { try { setData(await fn()); setError(null); } catch (e) { setError(refusalText(e)); } }, [fn]);
  useEffect(() => { void load(); const h = window.setInterval(() => void load(), ms); return () => window.clearInterval(h); }, [load, ms]);
  return { data, error };
}

/** Counts that must never be added together: repeated per-frame detections, unique tracks, risk events, notification deliveries. */
export function OperatorPanel({ go }: { go: (a: string) => void }) {
  const fn = useCallback(() => apiV2.overviewMonitor(60, 5), []);
  const { data: o, error } = usePoll<OperatorOverview>(fn, 2000);
  if (error) return <div className="error" role="alert">{error}</div>;
  if (!o) return <p className="muted">{t("mg.xc.dash.op_loading")}</p>;
  const k = o.counts_kept_apart;
  return (
    <div className="card" data-testid="operator-panel">
      <h3>{t("mg.xc.dash.op_title")}</h3>
      <p className="muted">{t("mg.xc.dash.op_active_prefix")}<b data-testid="op-active">{o.active_feeds}</b>{tf("mg.xc.dash.op_active_suffix", { limit: o.active_limit, validated: o.validated_feeds, proc: o.health.scheduler_alive ? t("mg.xc.dash.op_worker_alive") : t("mg.xc.dash.op_inactive"), model: o.health.model_loaded ? t("mg.xc.dash.op_model_loaded") : t("mg.xc.dash.op_model_unloaded"), failed: o.health.feeds_failed })}</p>
      {o.feeds.length === 0 ? <p className="muted" data-testid="op-no-feeds">{t("mg.xc.dash.op_no_feeds")}<button onClick={() => go("feeds")}>{t("mg.nav.feeds")}</button></p> :
        <div className="chips" data-testid="op-feeds">{o.feeds.map((f) => (
          <div key={f.feed_id} className="chip" data-testid="op-feed" data-feed-id={f.feed_id} data-risk={f.risk} data-state={f.state}>
            <b>{f.label}</b> <span className={`badge ${RISK_CLASS[f.risk]}`}>{t(`mg.risk.badge.${f.risk}`)}</span>
            <div className="muted">{tEnum("mg.xc.enum.feed_state", f.state)} · FPS {dash(f.fps, 2)} · p50/p95 {dash(f.latency_ms.p50, 0)}/{dash(f.latency_ms.p95, 0)} ms</div>
            <div className="muted">{tf("mg.xc.dash.op_feed_events", { red: f.events.RED, yellow: f.events.YELLOW })}{f.unknown_zones.length ? tf("mg.xc.dash.op_feed_unobserved", { zones: f.unknown_zones.join(", ") }) : ""}</div>
            <div className={f.repeated_source ? "warn" : "muted"}>{f.repeated_source ? t("mg.alert.repeated") : t("mg.alert.file")}</div>
            {f.error && <div className="error">{f.error}</div>}
          </div>))}</div>}
      <h4>{t("mg.xc.dash.op_four_title")}</h4>
      <table data-testid="op-counts"><tbody>
        <tr><td>{t("mg.xc.dash.op_c_detections")}</td><td data-testid="op-detections">{k.per_frame_detections}</td></tr>
        <tr><td>{t("mg.xc.dash.op_c_tracks")}</td><td data-testid="op-tracks">{k.unique_tracks}</td></tr>
        <tr><td>{t("mg.xc.dash.op_c_events_live")}</td><td data-testid="op-events">{k.risk_events_live.RED} / {k.risk_events_live.YELLOW}</td></tr>
        <tr><td>{t("mg.xc.dash.op_c_events_sim")}</td><td data-testid="op-events-sim">{k.risk_events_from_simulated_sources.RED} / {k.risk_events_from_simulated_sources.YELLOW}</td></tr>
        <tr><td>{t("mg.xc.dash.op_c_deliveries")}</td><td data-testid="op-deliveries">{k.notification_deliveries.in_app_stored} / {k.notification_deliveries.in_app_displayed} / {k.notification_deliveries.browser} / {k.notification_deliveries.sound} · {k.notification_deliveries.failed}{k.notification_deliveries.loaded_from_disk_unverified ? tf("mg.xc.dash.op_deliveries_restored", { n: k.notification_deliveries.loaded_from_disk_unverified }) : ""}</td></tr>
        <tr><td>{t("mg.xc.dash.op_c_unverified")}</td><td data-testid="op-events-unverified">{k.risk_events_in_saved_feed_history_unverified.RED} / {k.risk_events_in_saved_feed_history_unverified.YELLOW}</td></tr>
      </tbody></table>
      <p className="muted">{k.note}</p>
      <h4>{t("mg.xc.dash.op_alerts_title")}</h4>
      {o.alerts.capacity?.degraded && <p className="error" role="alert" data-testid="op-alerts-degraded">{tf("mg.xc.dash.op_alerts_degraded", { dropped: o.alerts.capacity.overflow_dropped, hard: o.alerts.capacity.hard })}</p>}
      <p data-testid="op-alerts" data-open={o.alerts.open}>{t("mg.xc.dash.op_open_prefix")}<b>{o.alerts.open}</b>{tf("mg.xc.dash.op_open_suffix", { unack: o.alerts.active_unacknowledged, ack: o.alerts.acknowledged_open, sev: Object.entries(o.alerts.by_severity).map(([s, n]) => `${tEnum("mg.xc.enum.severity", s)} ${n}`).join(", ") || t("mg.xc.dash.none_f"), prov: Object.entries(o.alerts.by_provenance).map(([s, n]) => `${tEnum("mg.xc.enum.provenance", s)} ${n}`).join(", ") || t("mg.xc.dash.none_m") })}</p>
      <h4>{t("mg.xc.dash.op_eps_title")}</h4>
      {Object.keys(o.events_per_source).length === 0 ? <p className="muted">{t("mg.xc.dash.op_eps_empty")}</p> :
        <ul data-testid="op-events-per-source">{Object.entries(o.events_per_source).map(([k2, v]) => <li key={k2}>{tf("mg.xc.dash.op_eps_line", { src: k2, red: v.RED, yellow: v.YELLOW })}<span className="muted">({tEnum("mg.xc.enum.provenance", v.provenance)})</span></li>)}</ul>}
      <h4>{t("mg.xc.dash.op_timeline_title")}</h4>
      {o.recent_timeline.length === 0 ? <p className="muted" data-testid="op-timeline-empty">{t("mg.xc.dash.op_timeline_empty")}</p> :
        <ul data-testid="op-timeline">{o.recent_timeline.map((e) => <li key={e.alert_id} data-testid="op-timeline-item" data-alert-id={e.alert_id} data-prov={alertProvenance(e)}><span className="mono">{e.created_utc.slice(11, 19)}</span> <span className={`badge ${RISK_CLASS[e.severity]}`}>{tEnum("mg.xc.enum.severity", e.severity)}</span> {e.source} · {e.zone_id} · {tEnum("mg.xc.enum.alert_status", e.status)} <ProvTag kind={alertProvenance(e)} />{e.loaded_from_store ? <span className="warn"> · {t("mg.inc.restored_short")}</span> : null}</li>)}</ul>}
      <p className="muted" data-testid="op-retention">{tf("mg.xc.dash.op_retention", { frames: o.sessions_retention.frames, meta: o.sessions_retention.metadata_only, deleted: o.sessions_retention.media_deleted, saved: o.saved_feed_runs })}</p>
      {o.health.resources.measured && <p className="muted">{tf("mg.xc.dash.op_resources", { at: o.health.resources.at_utc, rss: dash(o.health.resources.process_rss_mb, 0), free: o.health.resources.host_free_percent ?? "–", lvl: o.health.resources.host_pressure_level ?? "–", swap: dash(o.health.resources.swap_used_mb, 0) })}</p>}
    </div>
  );
}

export function TrendChart() {
  const [m, setM] = useState(60);
  const bucket = m === 15 ? 1 : m === 60 ? 5 : m === 240 ? 15 : 60;
  const fn = useCallback(() => apiV2.overviewMonitor(m, bucket), [m, bucket]);
  const { data: o, error } = usePoll<OperatorOverview>(fn, 5000);
  const max = Math.max(1, ...(o?.trend.buckets ?? []).map((b) => b.RED + b.YELLOW + b.UNKNOWN));
  return (
    <div className="card" data-testid="trend">
      <h3>{t("mg.xc.dash.trend_title")}</h3>
      {error && <div className="error" role="alert">{error}</div>}
      <label>{t("mg.xc.dash.trend_interval")}<select value={m} onChange={(e) => setM(Number(e.target.value))} data-testid="trend-interval">{[[15, "15 min"], [60, "1 h"], [240, "4 h"], [1440, "24 h"]].map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
      {o && (!o.trend.has_data ? <p className="muted" data-testid="trend-empty">{t("mg.xc.dash.trend_empty")}</p> :
        <svg viewBox={`0 0 ${o.trend.buckets.length * 14} 70`} role="img" aria-label={t("mg.xc.dash.trend_aria")} data-testid="trend-svg" style={{ width: "100%", maxWidth: 640 }}>
          {o.trend.buckets.map((b, i) => {
            const hr = (b.RED / max) * 60, hy = (b.YELLOW / max) * 60, hu = (b.UNKNOWN / max) * 60;   // genuine live alerts only; simulated and restored ones are reported below, never drawn as incidents
            return <g key={b.from_utc} transform={`translate(${i * 14 + 2},0)`}><rect y={66 - hr} height={hr} width={10} fill="#d64545" /><rect y={66 - hr - hy} height={hy} width={10} fill="#e0a800" /><rect y={66 - hr - hy - hu} height={hu} width={10} fill="#8a8f98" /></g>;
          })}
        </svg>)}
      {o && <p className="muted">{tf("mg.xc.dash.trend_population", { minutes: o.trend.minutes, bucket: o.trend.bucket_minutes, sim: o.trend.simulated_total, restored: o.trend.restored_total, basis: o.trend.basis })}</p>}
    </div>
  );
}

export function DeveloperPanel() {
  const { data: d, error } = usePoll<DeveloperOverview>(apiV2.developer, 2000);
  if (error) return <div className="error" role="alert">{error}</div>;
  if (!d) return <p className="muted">{t("mg.xc.dash.dev_loading")}</p>;
  const a = d.acceptance_state;
  return (
    <div className="card" data-testid="developer-panel">
      <h3>{t("mg.xc.dash.dev_title")}</h3>
      <p className="muted" data-testid="dev-policy">{t("mg.dev.metrics_policy")}</p>
      <ul>
        <li data-testid="dev-model">{tf("mg.xc.dash.dev_model", { id: d.model.model_id ?? "–", arch: d.model.architecture ?? "–", sha: d.model.weights_sha256_prefix ?? "–", state: d.model.loaded ? tf("mg.xc.dash.dev_model_loaded_on", { device: d.model.device ?? "?" }) : t("mg.xc.dash.dev_model_unloaded") })}</li>
        <li data-testid="dev-versions">{tf("mg.xc.dash.dev_versions", { engine: d.versions.risk_kernel_engine, sem: d.versions.risk_kernel_semantics, tracker: d.versions.tracker, api: d.versions.alert_api, app: d.versions.app })}</li>
        <li data-testid="dev-scheduler">{tf("mg.xc.dash.dev_scheduler", { policy: d.scheduler.policy.startsWith("one shared model") ? t("mg.dev.sched_policy") : d.scheduler.policy, state: d.scheduler.alive ? t("mg.xc.dash.dev_alive") : t("mg.xc.dash.dev_inactive"), served: d.scheduler.served, wait: d.scheduler.max_queue_wait_ms, fps: dash(d.aggregate_inference_fps, 2) })}</li>
        <li data-testid="dev-resources">{tf("mg.xc.dash.dev_resources", { at: d.resources.at_utc, rss: dash(d.resources.process_rss_mb, 0), free: d.resources.host_free_percent ?? "–", lvl: d.resources.host_pressure_level ?? "–", swap: dash(d.resources.swap_used_mb, 0), dec: dash(d.resources.decoder_service_mb, 0) })}</li>
      </ul>
      <h4>{t("mg.xc.dash.dev_by_source")}</h4>
      {d.feeds.length === 0 ? <p className="muted" data-testid="dev-no-feeds">{t("mg.xc.dash.dev_no_feeds")}</p> :
        <table data-testid="dev-feeds"><thead><tr><th>{t("mg.xc.dash.dev_th_source")}</th><th>{t("mg.xc.dash.dev_th_state_trust")}</th><th>{t("mg.xc.dash.dev_th_frames")}</th><th>{t("mg.xc.dash.dev_th_fps")}</th><th>{t("mg.xc.dash.dev_th_queue")}</th><th>{t("mg.xc.dash.dev_th_classes")}</th><th>{t("mg.xc.dash.dev_th_tracks")}</th><th>{t("mg.xc.dash.dev_th_error")}</th></tr></thead>
          <tbody>{d.feeds.map((f) => <tr key={f.feed_id} data-testid="dev-feed" data-feed-id={f.feed_id}><td><code>{f.feed_id}</code></td><td>{t(`mg.feed.state.${f.state}`)}<div className="muted">{tEnum("mg.xc.enum.trust", f.trust)}</div></td>
            <td className="mono">{tf("mg.xc.dash.dev_frames", { d: f.frames.decoded, s: f.frames.submitted, a: f.frames.analyzed, x: f.frames.dropped, e: f.frames.adapter_errors, k: f.frames.skipped_by_sampling_intentional })}</td>
            <td>{dash(f.inference_fps, 2)} · {dash(f.latency_ms.p50, 0)}/{dash(f.latency_ms.p95, 0)} ms</td><td>{tf("mg.xc.dash.dev_queue", { depth: f.queue_depth, max: f.queue_max, peak: f.max_queue_depth })}</td>
            <td>{Object.entries(f.class_distribution).map(([k, v]) => `${tEnum("mg.xc.enum.class", k)} ${v}`).join(", ") || "–"}</td><td>{Object.entries(f.tracks_unique).map(([k, v]) => `${tEnum("mg.xc.enum.class", k)} ${v}`).join(", ") || "–"}{tf("mg.xc.dash.dev_discont", { n: f.tracking_discontinuities })}</td><td>{f.error ?? "–"}</td></tr>)}</tbody></table>}
      {d.webcam_live_sessions.length > 0 && <><h4>{t("mg.xc.dash.dev_webcam_title")}</h4><pre className="mono" data-testid="dev-webcam">{JSON.stringify(d.webcam_live_sessions[0].accounting ?? d.webcam_live_sessions[0].counters, null, 1)}</pre></>}
      <h4>{t("mg.xc.dash.dev_acc_title")}</h4>
      <p data-testid="dev-acceptance" className="muted">{a.available === false ? t("mg.xc.dash.dev_acc_none") : tf("mg.xc.dash.dev_acc_line", { c: a.commit ? tf("mg.xc.dash.dev_acc_commit", { commit: a.commit }) : t("mg.xc.dash.dev_acc_nocommit"), gate: a.gate ?? t("mg.xc.dash.dev_acc_gate_not_run"), n: a.totals?.collected ?? t("mg.xc.dash.dev_acc_not_measured") })}. {a.note}</p>
    </div>
  );
}

export function AlertSettingsPanel() {
  const [prefs, setPrefs] = usePrefs();
  const [perm, setPerm] = useState<string>("Notification" in window ? Notification.permission : "unsupported");
  const [s, setS] = useState<AlertSettings | null>(null);
  const [contract, setContract] = useState<{ status: string; destination_rules: string[]; provider_limits: string } | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { void apiV2.alertSettings().then((r) => { setS(r.settings); setContract(r.webhook_contract); }).catch((e) => setErr(refusalText(e))); }, []);
  const enableBrowser = async () => {
    if (!("Notification" in window)) { setMsg("mg.xc.dash.msg_no_api"); return; }
    const p = Notification.permission === "default" ? await Notification.requestPermission() : Notification.permission;
    setPerm(p); setPrefs({ ...prefs, browser: true });   // the user's WISH is recorded; if the browser denies, each affected alert records «denied» honestly
    setMsg(p === "granted" ? "mg.xc.dash.msg_granted" : p === "denied" ? "mg.xc.dash.msg_denied" : "mg.xc.dash.msg_pending");
  };
  const enableSound = () => { const ok = unlockAudio(); setPrefs({ ...prefs, sound: ok }); setMsg(ok ? "mg.xc.dash.msg_sound_on" : "mg.xc.dash.msg_sound_fail"); };
  const save = async (next: AlertSettings) => { try { const r = await apiV2.putAlertSettings(next); setS(r.settings); setErr(null); } catch (e) { setErr(refusalText(e)); } };
  return (
    <div className="card" data-testid="alert-settings">
      <h3>{t("mg.cfg.notify")}</h3>
      <p className="muted">{t("mg.cfg.local_prefs")}</p>
      {err && <div className="error" role="alert">{err}</div>}
      <p>{t("mg.cfg.notify_state")}: <b data-testid="notify-permission">{tEnum("mg.xc.enum.notif_perm", perm)}</b>{t("mg.xc.dash.notify_here")}<b data-testid="notify-pref">{prefs.browser ? t("mg.xc.dash.yes") : t("mg.xc.dash.no")}</b>{" "}
        <button onClick={() => void enableBrowser()} data-testid="notify-enable">{t("mg.cfg.notify_enable")}</button>{" "}{prefs.browser && <button onClick={() => setPrefs({ ...prefs, browser: false })} data-testid="notify-disable">{t("mg.xc.dash.disable")}</button>}</p>
      <p>{t("mg.cfg.sound")}: <b data-testid="sound-pref">{prefs.sound ? t("mg.xc.dash.on") : t("mg.xc.dash.off")}</b>{" "}
        <button onClick={enableSound} data-testid="sound-enable">{t("mg.cfg.sound_enable")}</button>{" "}<button onClick={() => { unlockAudio(); beep("YELLOW"); }} data-testid="sound-test">{t("mg.cfg.sound_test")}</button>{" "}
        {prefs.sound && <button onClick={() => setPrefs({ ...prefs, sound: false })}>{t("mg.xc.dash.disable")}</button>}</p>
      {msg && <p className="muted" data-testid="notify-msg">{t(msg)}</p>}
      {s && <>
        <h4>{t("mg.cfg.policy")}</h4>
        <table data-testid="policy"><thead><tr><th>{t("mg.xc.dash.th_severity")}</th><th>{t("mg.xc.dash.th_browser")}</th><th>{t("mg.xc.dash.th_sound")}</th><th>{t("mg.xc.dash.th_cooldown")}</th></tr></thead><tbody>
          {(["red", "yellow", "unknown"] as const).map((k) => (
            <tr key={k}><td>{k === "red" ? t("mg.xc.dash.pol_red") : k === "yellow" ? t("mg.xc.dash.pol_yellow") : t("mg.xc.dash.pol_unknown")}</td>
              <td><input type="checkbox" checked={s[k].browser} aria-label={`${k.toUpperCase()}: ${t("mg.cfg.notify")}`} onChange={(e) => void save({ ...s, [k]: { ...s[k], browser: e.target.checked } })} data-testid={`pol-${k}-browser`} /></td>
              <td><input type="checkbox" checked={s[k].sound} aria-label={`${k.toUpperCase()}: ${t("mg.cfg.sound")}`} onChange={(e) => void save({ ...s, [k]: { ...s[k], sound: e.target.checked } })} data-testid={`pol-${k}-sound`} /></td>
              <td><input type="number" min={0} max={3600} value={s[k].cooldown_s} aria-label={tf("mg.xc.dash.aria_cooldown", { sev: k.toUpperCase() })} onChange={(e) => void save({ ...s, [k]: { ...s[k], cooldown_s: Math.max(0, Math.min(3600, Number(e.target.value) || 0)) } })} data-testid={`pol-${k}-cool`} style={{ width: 70 }} /></td></tr>))}
        </tbody></table>
        <p className="muted">{t("mg.xc.dash.policy_note")}</p></>}
      <h4>{t("mg.cfg.webhook")}</h4>
      <p data-testid="webhook-status" className="warn">{t("mg.cfg.webhook_off")}</p>
      {contract && <details><summary>{t("mg.xc.dash.contract_summary")}</summary><ul>{contract.destination_rules.map((r) => <li key={r}>{r}</li>)}</ul><p className="muted">{contract.provider_limits}</p></details>}
      <h4>{t("mg.cfg.multicam")}</h4>
      <p data-testid="multicam-na" className="muted">{t("mg.na")}: {t("mg.feed.cameras_note")}</p>
    </div>
  );
}
