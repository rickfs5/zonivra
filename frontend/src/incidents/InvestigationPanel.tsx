import { useEffect, useState } from "react";
import { apiV2, type Incident, type IncidentTimelineEntry } from "../api";
import { sourceLabel, t, tEnum } from "../i18n";
import { fmtUs } from "../shared/ui";

const tr = (id: string, fallback: string) => (t(id) === id ? fallback : t(id));

const label = (e: IncidentTimelineEntry): string => {
  if (e.kind === "risk_entry") return `${t("mg.inv.tl.risk_entry")} ${t(`mg.risk.badge.${e.severity}`)}${e.rule_id ? ` · ${e.rule_id}` : ""}`;
  if (e.kind === "unknown_interval") return `${t("mg.inv.tl.unknown_interval")}${e.cause ? ` · ${e.cause}` : ""}`;
  if (e.kind === "episode_closed") return `${t("mg.inv.tl.episode_closed")} · ${e.closed_reason ?? "—"}${e.right_censored ? ` (${t("mg.inv.censored")})` : ""}`;
  if (e.kind === "risk_changed") {
    const b = (v: string | null | undefined) => (v ? t(`mg.risk.badge.${v}`) : t("mg.inv.tl.first_eval"));
    return `${t("mg.inv.tl.risk_changed")}: ${b(e.from)} → ${b(e.to)}${e.to === "YELLOW" || e.to === "RED" ? ` · ${t(`mg.risk.badge.${e.to}`)} ${t("mg.inv.tl.began")}` : ""}`;
  }
  if (e.kind === "person_detected" || e.kind === "vehicle_detected") return `${t(`mg.inv.tl.${e.kind}`)}${e.count ? ` (${e.count})` : ""} · ${t("mg.inv.tl.scope_frame")}`;
  if (e.kind === "source_failed") return `${t("mg.inv.tl.source_failed")}${e.error ? ` · ${e.error}` : ""}`;
  if (e.kind === "alert_resolved") return `${t("mg.inv.tl.alert_resolved")}${e.by ? ` · ${e.by}` : ""}`;
  if (e.kind === "provenance_changed") return `${t("mg.inv.tl.provenance_changed")} · ${t(`mg.ov.phase.${e.overlap_phase}`) === `mg.ov.phase.${e.overlap_phase}` ? e.overlap_phase : t(`mg.ov.phase.${e.overlap_phase}`)}`;
  if (e.kind.startsWith("delivery_")) return `${tr(`mg.inv.tl.${e.kind}`, e.kind)} (${tEnum("mg.xd.enum.channel", e.channel)})`;
  return tr(`mg.inv.tl.${e.kind}`, e.kind);
};

function Track({ title, rows, testid, time }: { title: string; rows: IncidentTimelineEntry[]; testid: string; time: (e: IncidentTimelineEntry) => string }) {
  return (
    <div data-testid={testid}>
      <h4>{title}</h4>
      {rows.length === 0 ? <p className="muted">{t("mg.inv.tl.empty")}</p> : (
        <ol className="tl-list">{rows.map((e, i) => (
          <li key={`${e.kind}-${i}`} data-kind={e.kind} data-to={e.to ?? undefined}><span className="mono">{time(e)}</span> {label(e)}{" "}
            {e.recorded && <span className="tag-rec" data-testid="tl-recorded">{t("mg.inv.tl.recorded")}</span>}{" "}{e.synthetic && <span className="tag-sim">{t("mg.inv.tl.sim")}</span>}{e.restored && <span className="tag-sim">{t("mg.inv.tl.restored")}</span>}</li>))}</ol>)}
    </div>
  );
}

/** Investigation detail of ONE incident: everything below is the server's incident record (the same one the JSON export contains). The panel adds no fact of its own. `rev` re-reads it after an operator action. */
export function InvestigationPanel({ alertId, rev }: { alertId: string; rev: number }) {
  const [inc, setInc] = useState<Incident | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void apiV2.incident(alertId).then((r) => { if (live) { setInc(r); setErr(null); } }, (e) => { if (live) setErr(String(e)); });
    return () => { live = false; };
  }, [alertId, rev]);
  const exportJson = async () => {
    try {
      const text = await apiV2.incidentExport(alertId);
      const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url; a.download = `${alertId}.mineguard-incident.json`;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
      setDone(`${t("mg.inv.exported")}: ${alertId}.mineguard-incident.json`);
    } catch (e) { setErr(String(e)); }
  };
  if (err) return <div className="error" role="alert" data-testid="inv-error">{t("mg.inv.error")}: {err}</div>;
  if (!inc) return <p className="muted" data-testid="inv-loading">{t("mg.inv.loading")}</p>;
  const o = inc.observed, ev = inc.evidence, pv = inc.provenance;
  const us = (v: number | null) => (v === null ? "—" : fmtUs(v));
  return (
    <section className="investigation" data-testid="investigation" data-classification={inc.classification} aria-label={t("mg.inv.title")}>
      <h3>{t("mg.inv.title")}</h3>
      <p className={`banner ${inc.classification === "live_feed_metadata" ? "info" : "warn"}`} data-testid="inv-class">{t(`mg.inv.class.${inc.classification}`)}</p>

      <p className="inv-summary" data-testid="inv-summary" data-severity={inc.risk.severity}>
        <span className={`badge badge-${inc.risk.severity}`}>{t(`mg.risk.badge.${inc.risk.severity}`)}</span>{" "}
        {t("mg.inv.sum.in_zone")} <b>{inc.identity.zone_id}</b> · {t("mg.inv.sum.source")} <b>{sourceLabel(inc.identity.source_label)}</b> · {o.start_us === null ? t("mg.notmeasured") : `${t("mg.inv.sum.from")} ${fmtUs(o.start_us)}`} · {o.open ? t("mg.inv.sum.open") : t("mg.inv.sum.closed")} · {t(`mg.alert.status.${inc.alert.status}`)}
      </p>

      <h4>{t("mg.inv.s_risk")}</h4>
      <dl className="kv" data-testid="inv-risk">
        <dt>{t("mg.inv.rule")}</dt><dd><span className="mono">{inc.risk.rule_id ?? "—"}</span>{inc.risk.rule_id && <><br />{tr(`mg.rule.${inc.risk.rule_id}`, "")}</>}</dd>
        <dt>{t("mg.inv.semver")}</dt><dd className="mono">{inc.risk.semantics_version ?? "—"}</dd>
        <dt>{t("mg.inv.engine")}</dt><dd className="mono">{inc.risk.engine_version ?? "—"}</dd>
        <dt>&nbsp;</dt><dd className="muted" data-testid="inv-versions-basis">{t(`mg.inv.versions_basis.${inc.risk.versions_basis}`)}</dd>
        {inc.risk.causes.length > 0 && <><dt>{t("mg.inv.causes")}</dt><dd className="mono">{inc.risk.causes.join(", ")}</dd></>}
      </dl>
      <p className="muted" data-testid="inv-decided">{t("mg.inv.decided")}</p>

      <div className="grid cols-2">
        <div><h4>{t("mg.inv.s_observed")}</h4>
          <dl className="kv" data-testid="inv-observed">
            <dt>{t("mg.inc.zone")}</dt><dd>{inc.identity.zone_id}</dd>
            <dt>{t("mg.inv.start")}</dt><dd className="mono">{us(o.start_us)}</dd>
            <dt>{t("mg.inv.end")}</dt><dd className="mono">{o.open ? t("mg.inv.open") : us(o.end_us)}</dd></dl></div>
        <div><h4>{t("mg.inv.s_derived")}</h4>
          <dl className="kv" data-testid="inv-derived">
            <dt>{t("mg.inc.state")}</dt><dd>{t(`mg.risk.badge.${inc.risk.severity}`)}</dd>
            <dt>{t("mg.inv.closed_reason")}</dt><dd className="mono">{o.closed_reason ?? "—"}</dd>
            <dt>&nbsp;</dt><dd>{o.open ? "—" : o.right_censored ? t("mg.inv.censored") : t("mg.inv.not_censored")}</dd></dl></div>
      </div>

      <h4>{t("mg.inv.s_timeline")}</h4>
      <div className="grid cols-2" data-testid="inv-timeline">
        <Track title={t("mg.inv.tl_obs")} rows={inc.timeline.observation} testid="tl-observation" time={(e) => (e.at_us === undefined ? "—" : e.end_us !== undefined ? `${fmtUs(e.at_us)} → ${fmtUs(e.end_us)}` : fmtUs(e.at_us))} />
        <Track title={t("mg.inv.tl_proc")} rows={inc.timeline.processing} testid="tl-processing" time={(e) => (e.at_utc ?? "—").slice(11, 19)} />
      </div>
      <div data-testid="tl-lifecycle" data-recorded={inc.timeline.lifecycle.recorded ? "yes" : "no"}>
        {inc.timeline.lifecycle.recorded
          ? <p className="muted">{t("mg.inv.life.recorded")}{inc.timeline.lifecycle.truncated ? ` ${t("mg.inv.life.truncated")}` : ""}</p>
          : <p className="banner warn" data-testid="tl-not-recorded">{t(inc.timeline.lifecycle.reason ?? "mg.inc.life.not_recorded")}</p>}
        {inc.timeline.unsupported_kinds.length > 0 && <p className="muted" data-testid="tl-unsupported">{t("mg.inv.tl.unsupported")}: {inc.timeline.unsupported_kinds.map((k) => t(`mg.inv.tl.u.${k}`)).join(", ")}.</p>}
        <p className="muted" data-testid="tl-order-note">{t("mg.inv.tl.order_note")}</p>
      </div>

      <h4>{t("mg.inv.s_unknown")}</h4>
      <div data-testid="inv-unknown" data-available={inc.unknown_intervals.available ? "yes" : "no"}>
        {!inc.unknown_intervals.available ? <p className="muted">{t("mg.inv.unknown_na")} {inc.unknown_intervals.reason ? t(inc.unknown_intervals.reason) : ""}</p>
          : inc.unknown_intervals.intervals.length === 0 ? <p className="muted">{t("mg.inv.unknown_none")}</p>
            : <ul className="mono">{inc.unknown_intervals.intervals.map((u, i) => <li key={i}>{fmtUs(u.start_us)} → {fmtUs(u.end_us)}{u.cause ? ` · ${u.cause}` : ""}</li>)}</ul>}
      </div>

      <h4>{t("mg.inv.s_complete")}</h4>
      <dl className="kv" data-testid="inv-completeness" data-complete={inc.completeness.complete ? "yes" : "no"}>
        <dt>{t("mg.inv.comp.lifecycle")}</dt><dd>{t(`mg.inv.comp.${inc.completeness.source_lifecycle}`)}</dd>
        <dt>{t("mg.inv.comp.edges")}</dt><dd>{t(`mg.inv.comp.${inc.completeness.observation_edges}`)}</dd>
        <dt>{t("mg.inv.comp.unknown")}</dt><dd>{t(`mg.inv.comp.${inc.completeness.unknown_intervals}`)}</dd>
        <dt>{t("mg.inv.comp.images")}</dt><dd>{t("mg.inv.comp.excluded")}</dd>
        <dt>&nbsp;</dt><dd className="muted">{inc.completeness.complete ? t("mg.inv.comp.complete") : t("mg.inv.comp.incomplete")}</dd>
      </dl>

      <h4>{t("mg.inv.s_evidence")}</h4>
      <dl className="kv" data-testid="inv-evidence" data-consistency={ev.local_consistency}>
        <dt>{t("mg.inv.ev.avail")}</dt><dd>{t("mg.inv.ev.metadata_only")}</dd>
        <dt>{t("mg.inv.ev.run")}</dt><dd className="mono">{ev.stored_run ?? "—"}</dd>
        <dt>{t("mg.inv.ev.digest")}</dt><dd className="mono">{ev.digest_sha256 ? ev.digest_sha256.replace("sha256:", "").slice(0, 16) + "…" : "—"}</dd>
        <dt>{t("mg.inv.ev.consistency")}</dt><dd>{t(`mg.inv.ev.${ev.local_consistency}`)}</dd>
        <dt>{t("mg.inv.ev.restored")}</dt><dd>{ev.restored ? t("mg.inv.ev.yes") : t("mg.inv.ev.no")}</dd>
        <dt>{t("mg.inv.ev.synthetic")}</dt><dd>{ev.synthetic ? t("mg.inv.ev.yes") : t("mg.inv.ev.no")}</dd>
        <dt>{t("mg.inv.ev.auth")}</dt><dd data-testid="inv-auth">{t("mg.inv.ev.auth_no")}</dd>
      </dl>
      {ev.conflicts.map((c) => <p key={c} className="banner warn" role="alert" data-testid="inv-conflict">{t(c)}</p>)}

      <h4>{t("mg.inv.s_provider")}</h4>
      <dl className="kv" data-testid="inv-provider">
        <dt>{t("mg.inc.source")}</dt><dd>{sourceLabel(inc.identity.source_label)} · <span className="mono">{inc.identity.source_id}</span></dd>
        <dt>{t("mg.inv.model")}</dt><dd data-kind={pv.provider.kind}>{t(`mg.inv.prov.${pv.provider.kind}`)}{pv.provider.model_id ? ` · ${pv.provider.model_id}` : ` · ${t("mg.inv.model_none")}`}</dd>
        {pv.overlap_phase && <><dt>{t("mg.ov.phase.label")}</dt><dd>{t(`mg.ov.phase.${pv.overlap_phase}`) === `mg.ov.phase.${pv.overlap_phase}` ? pv.overlap_phase : t(`mg.ov.phase.${pv.overlap_phase}`)}</dd></>}
        {inc.identity.feed_id && <><dt>{t("mg.inv.feed_now")}</dt><dd data-testid="inv-feed-now">{pv.feed ? `${t(`mg.feed.state.${pv.feed.state}`)} · ${pv.feed.observation_fresh ? t("mg.inv.fresh") : t("mg.inv.stale")}` : t("mg.inv.feed_gone")}</dd></>}
      </dl>

      <h4>{t("mg.inv.s_limits")}</h4>
      <ul className="muted" data-testid="inv-limits">{inc.limitations.map((l) => <li key={l}>{t(l)}</li>)}</ul>

      <div className="controls">
        <button onClick={() => void exportJson()} data-testid="inv-export">{t("mg.inv.export")}</button>
        <span className="muted">{t("mg.inv.export_note")}</span>
      </div>
      {done && <p className="ok" role="status" data-testid="inv-exported">{done}</p>}
    </section>
  );
}
