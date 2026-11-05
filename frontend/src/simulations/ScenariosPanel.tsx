import { useEffect, useState } from "react";
import { api } from "../api";
import { t } from "../i18n";
import { Banner, Panel } from "../shared/ui";
import { refusalText } from "../ZoneEditor";
import { descId, inputId, isSynthetic, reviewPending, seconds, stateStrip, titleId, widthPct, type Scenario, type ScnList } from "./scenarioPresent";

const COLORS: Record<string, string> = { GREEN: "var(--green)", YELLOW: "var(--yellow)", RED: "var(--red)", UNKNOWN: "var(--grey)", NO_SAMPLE: "repeating-linear-gradient(45deg,var(--unk-a),var(--unk-a) 4px,var(--sunken) 4px,var(--sunken) 8px)" };

/** Deterministic industrial scenario replays: SYNTHETIC observations recomputed by the production tracker and risk kernel. Read-only; never footage, never RF-DETR output, never a provider result. */
export function ScenariosPanel() {
  const [list, setList] = useState<ScnList | null>(null);
  const [sel, setSel] = useState<Scenario | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.scenarios().then(setList).catch((e) => setErr(refusalText(e))); }, []);
  const open = async (id: string) => { setErr(null); try { setSel(await api.scenario(id)); } catch (e) { setErr(refusalText(e)); } };
  const strip = sel ? stateStrip(sel.samples, sel.input.interval_ms, sel.input.duration_ms) : [];
  return (
    <Panel title={t("mg.scn.title")} testid="scenarios-panel">
      <Banner kind="warn" testid="scn-banner">{t("mg.scn.banner")}</Banner>
      {err && <p className="error" role="alert" data-testid="scn-error">{err}</p>}
      <p className="muted">{t("mg.scn.cards_note")}</p>
      <div className="scn-cards" role="group" aria-label={t("mg.scn.pick")}>
        {(list?.scenarios ?? []).map((s) => (
          <button key={s.id} className="scn-card" onClick={() => open(s.id)} data-testid={`scn-open-${s.id}`} aria-pressed={sel?.id === s.id}>
            <span className="scn-card-t">{t(titleId(s.id))}</span><span className="scn-card-d">{t(descId(s.id))}</span>
            <span className="scn-card-f"><span className="tag sim">{t("mg.scn.tag").split(":")[0]}</span><span className="scn-card-go">{t("mg.scn.open_card")} →</span></span></button>))}
      </div>
      {sel && (
        <div data-testid="scn-detail" data-scenario={sel.id} data-synthetic={isSynthetic(sel) ? "yes" : "no"}>
          <h4>{t(titleId(sel.id))}</h4>
          <p className="badge badge-YELLOW" data-testid="scn-synthetic-tag">{t("mg.scn.tag")}</p>
          <p className="muted" data-testid="scn-review" data-status={sel.review.status} data-pending={reviewPending(sel) ? "yes" : "no"}>{t("mg.scn.review.pending")}</p>
          <h4>{t("mg.scn.f.provenance")}</h4>
          <table className="table"><tbody>
            <tr><th scope="row">{t("mg.scn.f.kind")}</th><td data-testid="scn-prov-kind">{sel.provenance.kind}</td></tr>
            <tr><th scope="row">{t("mg.scn.f.footage")}</th><td data-testid="scn-prov-footage">{sel.provenance.real_footage ? t("mg.scn.yes") : t("mg.scn.no")}</td></tr>
            <tr><th scope="row">{t("mg.scn.f.inference")}</th><td data-testid="scn-prov-inference">{sel.provenance.model_inference ? t("mg.scn.yes") : t("mg.scn.no")}</td></tr>
            <tr><th scope="row">{t("mg.scn.f.evaluated")}</th><td>{t("mg.scn.evaluated")}</td></tr>
            <tr><th scope="row">{t("mg.scn.f.digest")}</th><td className="mono" data-testid="scn-digest">{sel.digest.slice(0, 16)}…</td></tr>
          </tbody></table>
          <h4>{t("mg.scn.f.zone")}</h4>
          <svg viewBox={`0 0 ${sel.zone.width} ${sel.zone.height}`} role="img" aria-label={t("mg.scn.zone_label")} style={{ maxWidth: 360, width: "100%", background: "var(--sunken)", border: "1px solid var(--line-2)" }} data-testid="scn-zone">
            <polygon points={sel.zone.polygon.map((p) => p.join(",")).join(" ")} fill="var(--err-bg)" fillOpacity={0.7} stroke="var(--red)" strokeWidth={3} />
          </svg>
          <p className="muted">{t("mg.scn.zone_note")} {sel.zone.width}×{sel.zone.height}</p>
          <h4>{t("mg.scn.f.input")}</h4>
          <ul data-testid="scn-segments">{sel.input.segments.map((g, i) => <li key={i}>{seconds(g.from_ms)} → {seconds(g.to_ms)} · {t(inputId(g.input))}</li>)}</ul>
          <h4>{t("mg.scn.f.states")}</h4>
          <div role="img" aria-label={t("mg.scn.strip_label")} data-testid="scn-strip" style={{ display: "flex", height: 22, border: "1px solid var(--line-2)" }}>
            {strip.map((r, i) => <div key={i} data-state={r.state} title={`${r.state} ${seconds(r.from_ms)}–${seconds(r.to_ms)}`} style={{ width: `${widthPct(r, sel.input.duration_ms)}%`, background: COLORS[r.state] }} />)}
          </div>
          <p className="legend" data-testid="scn-legend" aria-hidden="true">{(["GREEN", "YELLOW", "RED", "UNKNOWN"] as const).map((k) => <span key={k} className={`badge badge-${k}`}>{t(`mg.risk.badge.${k}`)}</span>)}</p>
          <h4>{t("mg.scn.f.transitions")}</h4>
          <div className="table-wrap"><table className="table" data-testid="scn-transitions"><thead><tr><th>{t("mg.sim.t")}</th><th>{t("mg.inc.state")}</th><th>{t("mg.inc.causes")}</th><th>{t("mg.scn.f.input")}</th></tr></thead>
            <tbody>{sel.transitions.map((x) => (
              <tr key={x.sample_index} data-state={x.to}><td className="mono">{seconds(x.t_ms)}</td><td><span className={`badge badge-${x.to}`}>{t(`mg.risk.badge.${x.to}`)}</span></td><td>{x.causes.join(", ") || "–"}</td><td>{t(inputId(x.input))}</td></tr>))}</tbody></table></div>
          <h4>{t("mg.scn.f.events")}</h4>
          {sel.events.length === 0 ? <p className="muted" data-testid="scn-no-events">{t("mg.risk.no_events")}</p> : (
            <ul className="events" data-testid="scn-events">{sel.events.map((e) => (
              <li key={e.event_id} data-severity={e.severity}><span className={`badge badge-${e.severity}`}>{t(`mg.risk.badge.${e.severity}`)}</span> {e.rule_id} {e.rule_version} · {seconds(e.start_ms)} → {e.end_ms === null ? "…" : seconds(e.end_ms)}</li>))}</ul>)}
          <h4>{t("mg.scn.f.coverage")}</h4>
          <p className="mono" data-testid="scn-coverage">{(["GREEN", "YELLOW", "RED", "UNKNOWN"] as const).map((k) => `${k} ${seconds(sel.coverage_ms[k])}`).join(" · ")} · {seconds(sel.coverage_ms.total)}</p>
          <h4>{t("mg.scn.f.caveats")}</h4>
          <ul data-testid="scn-caveats">{sel.caveats.map((c) => <li key={c}>{t(c)}</li>)}</ul>
        </div>)}
    </Panel>
  );
}
