import { useEffect, useRef, useState } from "react";
import { api, type AlertRec } from "../api";
import { useMonitor } from "../app/monitorStore";
import { ackAlert, resolveAlert, sourceTagOf, useAlerts } from "../Alerts";
import { sourceLabel, t } from "../i18n";
import { alertProvenance, overlapPhaseId } from "../lib";
import { useDialogFocus } from "../shared/dialog";
import { InvestigationPanel } from "./InvestigationPanel";
import { ExplanationPanel } from "./ExplanationPanel";
import { Icon, ProvTag, fmtUs, type Provenance } from "../shared/ui";

const provOf = (a: AlertRec): Provenance => alertProvenance(a);

const ch = (s: string) => t(`mg.inc.ch.${s}`) === `mg.inc.ch.${s}` ? s : t(`mg.inc.ch.${s}`);

export interface IncidentNav { openFeed: (id: string) => void; openRun: (id: string, base: string | null, tUs?: number) => void; go: (a: string) => void; remember?: (alertId: string) => void }

/** Incident detail: EVENT state, ALERT state and DELIVERY state are three different things and are shown apart. Metadata only: no image is shown unless the stored run itself provides one. */
export function IncidentDrawer({ alertId, onClose, nav, onSelect }: { alertId: string; onClose: () => void; nav: IncidentNav; onSelect?: (alertId: string) => void }) {
  const { list } = useAlerts();
  const a = list.find((x) => x.alert_id === alertId);
  const ref = useRef<HTMLDivElement>(null);
  const [err, setErr] = useState<string | null>(null);
  const mon = useMonitor();
  const [runIds, setRunIds] = useState<string[] | null>(null);          // null = not known yet
  useEffect(() => { let live = true; void api.runs().then((r) => { if (live) setRunIds(r.map((x) => x.run_id)); }, () => { if (live) setRunIds(null); }); return () => { live = false; }; }, [alertId]);
  useDialogFocus(ref, onClose);
  if (!a) return <div className="drawer" role="dialog" aria-modal="true" aria-label={t("mg.inc.title")} ref={ref} data-testid="incident-drawer" tabIndex={-1}><p className="muted">{t("mg.inc.gone")}</p><button onClick={onClose}>{t("mg.inc.close")}</button></div>;
  const prov = provOf(a);
  const runId = a.source.source_id.startsWith("sim:") ? a.source.source_id.split(":")[1] : null;
  const act = async (fn: () => Promise<string | null>) => setErr(await fn());
  // other alerts of EXACTLY the same source/run identity (never another run of the same feed, never another source), oldest first
  const related = list.filter((x) => x.source.source_id === a.source.source_id).sort((p, q) => p.created_utc.localeCompare(q.created_utc) || p.alert_id.localeCompare(q.alert_id));
  const pos = related.findIndex((x) => x.alert_id === a.alert_id);
  const jump = () => {
    nav.remember?.(a.alert_id);
    if (runId) { nav.openRun(runId, null, a.event?.start_us); onClose(); }
    else if (a.feed_id) { nav.openFeed(a.feed_id); onClose(); }
    else if (a.source.kind === "webcam_session") { nav.go("sesiones"); onClose(); }
  };
  // the source may be gone (feed removed, server restarted, run deleted): say so instead of offering a dead link
  const feedGone = !!a.feed_id && !!mon.data && !mon.data.feeds.some((f) => f.feed_id === a.feed_id);
  const runGone = !!runId && runIds !== null && !runIds.includes(runId);
  const unavailable = feedGone || runGone;
  const canJump = (!!runId || !!a.feed_id || a.source.kind === "webcam_session") && !unavailable;
  const jumpLabel = runId ? t("mg.inc.go_run") : a.feed_id ? t("mg.inc.go_feed") : t("mg.inc.go_sessions");
  return (
    <>
      <div className="drawer-scrim" onClick={onClose} aria-hidden="true" />
      <div className="drawer" role="dialog" aria-modal="true" aria-label={t("mg.inc.title")} ref={ref} data-testid="incident-drawer" data-alert-id={a.alert_id} tabIndex={-1}>
        <div className="panel-head"><h2><span className={`badge badge-${a.severity}`}>{t(`mg.risk.badge.${a.severity}`)}</span> {t("mg.inc.title")}</h2>
          <button onClick={onClose} aria-label={t("mg.inc.close")} data-testid="incident-close"><Icon name="close" /></button></div>
        <p><ProvTag kind={prov} /> {!a.simulation && <span className="muted">{sourceTagOf(a)}</span>}</p>
        {a.simulation && <p className="banner warn" data-testid="incident-sim">{t("mg.inc.sim_note")}</p>}
        {a.source.kind === "feed" && a.kind === "risk_event" && <p className="muted" data-testid="incident-overlap" data-phase={overlapPhaseId(a.overlap_phase).split(".").pop()}><b>{t("mg.ov.phase.label")}:</b> {t(overlapPhaseId(a.overlap_phase))}</p>}
        {a.kind === "diagnostic" && <p className="banner info">{t("mg.alert.notice_unknown")}</p>}
        {a.loaded_from_store && <p className="banner warn">{t("mg.inc.restored")}</p>}
        {related.length > 1 && onSelect && <p className="controls" data-testid="incident-related" aria-label={t("mg.inc.related")}>
          <button onClick={() => onSelect(related[pos - 1].alert_id)} disabled={pos <= 0} data-testid="incident-prev">{t("mg.inc.prev")}</button>
          <span className="muted">{t("mg.inc.related")}: {pos + 1} / {related.length}</span>
          <button onClick={() => onSelect(related[pos + 1].alert_id)} disabled={pos < 0 || pos >= related.length - 1} data-testid="incident-next">{t("mg.inc.next")}</button></p>}
        <h3>{t("mg.inc.s_event")}</h3>
        <dl className="kv" data-testid="incident-event">
          <dt>{t("mg.inc.zone")}</dt><dd>{a.zone_id}</dd>
          <dt>{t("mg.inc.rule")}</dt><dd>{a.event?.rule_id ?? "—"}</dd>
          <dt>{t("mg.inc.event_id")}</dt><dd className="mono" data-testid="incident-event-id">{a.event_id ?? "—"}</dd>
          <dt>{t("mg.inc.when")}</dt><dd>{a.event ? `${fmtUs(a.event.start_us)} → ${a.event.end_us === null ? t("mg.inc.open") : fmtUs(a.event.end_us)}` : t("mg.notmeasured")}</dd>
          <dt>{t("mg.inc.state")}</dt><dd>{a.event ? (a.event.open ? t("mg.inc.event_open") : t("mg.inc.event_closed")) : "—"}</dd>
          {a.causes.length > 0 && <><dt>{t("mg.inc.causes")}</dt><dd className="mono">{a.causes.join(", ")}</dd></>}
        </dl>
        <h3>{t("mg.inc.s_alert")}</h3>
        <dl className="kv" data-testid="incident-alert">
          <dt>{t("mg.inc.state")}</dt><dd>{t(`mg.alert.status.${a.status}`)}</dd>
          <dt>{t("mg.inc.created")}</dt><dd className="mono">{a.created_utc}</dd>
          {a.acknowledged_utc && <><dt>{t("mg.inc.acked")}</dt><dd className="mono">{a.acknowledged_utc}</dd></>}
          {a.resolved_utc && <><dt>{t("mg.inc.resolved")}</dt><dd className="mono">{a.resolved_utc} · {a.resolved_by}</dd></>}
          <dt>{t("mg.inc.merged")}</dt><dd>{a.suppressed_count}</dd>
        </dl>
        {a.occurrences && a.occurrences.length > 0 && <details><summary>{t("mg.inc.occurrences")} ({a.occurrence_total ?? a.occurrences.length})</summary>
          <ul className="mono">{a.occurrences.map((o) => <li key={o.event_id}>{o.event_id} · {o.open ? t("mg.inc.open") : t("mg.inc.event_closed")} · {o.source_id ?? t("mg.inc.unattributed")}</li>)}</ul></details>}
        <h3>{t("mg.inc.s_delivery")}</h3>
        <dl className="kv" data-testid="incident-delivery">
          <dt>{t("mg.inc.state")}</dt><dd>{t(`mg.alert.delivery.${a.delivery_state}`)}</dd>
          <dt>{t("mg.inc.in_app")}</dt><dd>{ch(a.delivery.in_app.status)}</dd>
          <dt>{t("mg.inc.browser")}</dt><dd>{ch(a.delivery.browser.status)}{a.delivery.browser.detail ? ` (${a.delivery.browser.detail})` : ""}</dd>
          <dt>{t("mg.inc.sound")}</dt><dd>{ch(a.delivery.sound.status)}{a.delivery.sound.detail ? ` (${a.delivery.sound.detail})` : ""}</dd>
        </dl>
        <p className="muted">{t("mg.inc.delivery_note")}</p>
        <h3>{t("mg.inc.s_source")}</h3>
        <dl className="kv"><dt>{t("mg.inc.source")}</dt><dd>{sourceLabel(a.source.label)}</dd><dt>ID</dt><dd className="mono">{a.source.source_id}</dd><dt>{t("mg.inc.physical")}</dt><dd>{t("mg.inc.not_physical")}</dd></dl>
        <p className="muted" data-testid="incident-evidence">{t("mg.inc.evidence_note")}</p>
        <p data-testid="incident-availability" data-unavailable={unavailable ? "yes" : "no"} className={unavailable ? "warn" : "muted"}>{unavailable ? t("mg.inc.source_gone") : t("mg.inc.source_available")}</p>
        <InvestigationPanel alertId={a.alert_id} rev={a.rev} />
        <ExplanationPanel key={a.alert_id} alertId={a.alert_id} rev={a.rev} />
        {err && <div className="error" role="alert">{err}</div>}
        <div className="controls">
          {a.status !== "resolved" && a.status !== "acknowledged" && <button className="primary" onClick={() => void act(() => ackAlert(a.alert_id))} data-testid="incident-ack">{t("mg.alert.ack")}</button>}
          {a.status !== "resolved" && <button onClick={() => void act(() => resolveAlert(a.alert_id))} data-testid="incident-resolve">{t("mg.alert.resolve")}</button>}
          <button onClick={() => { onClose(); nav.go("resumen"); }} data-testid="incident-back-monitor">{t("mg.inc.back_monitor")}</button>
          {canJump && <button onClick={jump} data-testid="incident-jump"><Icon name="expand" size={14} /> {jumpLabel}</button>}
        </div>
      </div>
    </>
  );
}
