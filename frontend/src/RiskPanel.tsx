import { t } from "./i18n";
import { closedReasonId, formatMs, intervalAt, riskLabelId, timelineSegments, worstState, type Evaluation, type RiskState } from "./lib";
import { STATE_COLOR } from "./draw";

export interface RiskPanelProps {
  evaluation: Evaluation | null;
  trust: "session" | "verified" | "unverified" | null;
  origin: string | null;
  kind: "runs" | "sessions";
  id: string | null;
  tUs: number | null;               // playhead (recorded video); null => use the end of the timeline (webcam session summary)
  totalUs: number;
  evidenceUrl: (idx: number) => string;
  exportUrl: string | null;
  onSeek?: (us: number) => void;     // jump the video to an instant (events and timeline markers); the caller remembers where to come back to
}

export function RiskPanel(p: RiskPanelProps) {
  const ev = p.evaluation;
  if (!ev) return <div className="card"><h2>{t("mg.risk.title")}</h2><p className="muted" data-testid="risk-none">{t("mg.zone.none")}</p></div>;
  const zoneIds = Object.keys(ev.zones);
  const provisional = p.trust === "unverified" && p.origin === "model_inference";
  const synthetic = p.origin !== null && p.origin !== "model_inference";
  const states: RiskState[] = zoneIds.map((z) => {
    const tl = ev.zones[z].timeline;
    const iv = intervalAt(tl, p.tUs ?? Math.max(0, p.totalUs - 1));
    return iv?.state ?? "UNKNOWN";
  });
  return (
    <div className="card" data-testid="risk-panel" data-overall={worstState(states)}>
      <h2>{t("mg.risk.title")}</h2>
      {provisional && <p className="warn" data-testid="risk-provisional">{t("mg.risk.provisional")}</p>}
      {synthetic && <p className="warn" data-testid="risk-synthetic">{t("mg.risk.synthetic_notice")}</p>}
      {!zoneIds.length && <p className="muted" data-testid="risk-no-zones">{t("mg.zone.none")}</p>}
      {zoneIds.map((zid) => {
        const z = ev.zones[zid];
        const iv = intervalAt(z.timeline, p.tUs ?? Math.max(0, p.totalUs - 1));
        const state = iv?.state ?? "UNKNOWN";
        const segs = timelineSegments(z.timeline, p.totalUs);
        return (
          <div key={zid} className="zone-risk" data-testid={`risk-zone-${zid}`} data-state={state} data-pending={iv?.pending ? "yes" : "no"}>
            <div className="badge-row">
              <span className={`badge badge-${state}`} data-testid="risk-badge">{t(`mg.risk.badge.${state}`)}</span>
              <b>{zid}</b>
              {p.tUs !== null && <span className="muted"> · {formatMs(p.tUs)}</span>}
            </div>
            <p className={state === "UNKNOWN" ? "warn" : "muted"} data-testid="risk-explain">{t(riskLabelId(state, !!iv?.pending))}</p>
            {iv && iv.causes.length > 0 && <p className="muted mono" data-testid="risk-causes">{iv.causes.join(" · ")}</p>}
            <div className="timeline" role="img" aria-label={t("mg.risk.timeline")} data-testid="risk-timeline">
              {segs.map((s, i) => <span key={i} className={`seg seg-${s.state}${s.pending ? " pending" : ""}`} style={{ left: `${s.left}%`, width: `${s.width}%`, background: STATE_COLOR[s.state] }} />)}
              {p.tUs !== null && p.totalUs > 0 && <span className="playhead" style={{ left: `${(100 * p.tUs) / p.totalUs}%` }} />}
              {p.onSeek && p.totalUs > 0 && z.events.map((e) => <button key={e.event_id} className={`marker ${e.severity}`} style={{ left: `${(100 * e.start_us) / p.totalUs}%` }} onClick={() => p.onSeek!(e.start_us)} aria-label={`${t(`mg.risk.badge.${e.severity}`)} ${formatMs(e.start_us)}`} title={`${t(`mg.risk.badge.${e.severity}`)} ${formatMs(e.start_us)}`} data-testid="risk-marker" />)}
            </div>
            <h3>{t("mg.risk.events")}</h3>
            {z.events.length === 0 ? <p className="muted" data-testid="risk-no-events">{t("mg.risk.no_events")}</p> : (
              <ul className="events" data-testid="risk-events">
                {z.events.map((e) => (
                  <li key={e.event_id} data-severity={e.severity} data-reason={e.closed_reason ?? "open"}>
                    <span className={`badge badge-${e.severity}`}>{t(`mg.risk.badge.${e.severity}`)}</span>{" "}
                    {formatMs(e.start_us)} → {e.end_us === null ? "…" : formatMs(e.end_us)} · {t(closedReasonId(e.closed_reason, e.open))}
                    {p.onSeek && <> <button onClick={() => p.onSeek!(e.start_us)} data-testid="risk-event-jump">{t("mg.vid.go_event")}</button></>}
                    {e.left_censored && t("mg.xc.risk.left_censored")}
                    {e.incomplete.length > 0 && <div className="muted">{t("mg.risk.incomplete")}: {[...new Set(e.incomplete.map((x) => x.cause))].join(", ")}</div>}
                    {ev.evidence_refs?.[e.event_id]?.start && <div><a href={p.evidenceUrl(ev.evidence_refs[e.event_id].start.frame_index)} target="_blank" rel="noreferrer" data-testid="risk-evidence-link">{t("mg.risk.evidence")} #{ev.evidence_refs[e.event_id].start.frame_index}</a></div>}
                  </li>))}
              </ul>
            )}
          </div>
        );
      })}
      {ev.zones_not_evaluated.map((z) => <p key={z.zone_id} className="muted">{z.zone_id}: {t("mg.risk.not_evaluated")}</p>)}
      {p.exportUrl && <a className="btn" href={p.exportUrl} download data-testid="risk-export">{t("mg.risk.export")}</a>}
    </div>
  );
}
