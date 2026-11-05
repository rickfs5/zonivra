import { ProvidersPanel } from "./providers/ProvidersPanel";
import { ExternalResultsPanel } from "./external/ExternalResultsPanel";
import { AppearancePanel } from "./shared/ThemeSwitch";
import { useCallback, useEffect, useState } from "react";
import { apiVs02, type AlertRec, type BuildInfo, type EventRow, type Overview } from "./api";
import { t, tEnum, tf } from "./i18n";
import { oafAcceptanceView } from "./lib";
import { AlertCenter } from "./Alerts";
import { AlertSettingsPanel, DeveloperPanel, TrendChart } from "./Dashboards";
import { IncidentDrawer } from "./incidents/IncidentDrawer";
import { useLoad } from "./shared/hooks";
import { Banner, Empty, fmtUs, Loading, Panel } from "./shared/ui";

const NA = () => <span className="muted" data-testid="na">{t("mg.na")}</span>;
const NATURE: Record<EventRow["nature"], string> = { synthetic: "sim", human_annotated: "unv", real_inference: "real", unverified_claim: "unv" };   // CSS class only; the label is the presentation mapping mg.xc.enum.nature.*

/** Incident center: alert inbox (event state, alert state and delivery state are different columns) + stored risk events + a detail drawer. */
export function Eventos({ focusAlert, onFocusHandled, openFeed, openRun, go, remember }: { focusAlert: string | null; onFocusHandled: () => void; openFeed: (id: string) => void; openRun: (id: string, base: string | null, tUs?: number) => void; go: (a: string) => void; remember?: (alertId: string) => void }) {
  const [sev, setSev] = useState(""); const [syn, setSyn] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const fn = useCallback(() => apiVs02.events(`${sev ? `severity=${sev}&` : ""}include_synthetic=${syn}&include_annotated=${syn}`), [sev, syn]);
  const { data, error } = useLoad(fn);
  useEffect(() => { if (focusAlert) { setSelected(focusAlert); onFocusHandled(); } }, [focusAlert, onFocusHandled]);
  return (
    <section data-testid="view-eventos" aria-labelledby="h-eventos">
      <div className="page-head"><div><h2 id="h-eventos">{t("mg.nav.eventos")}</h2><p className="muted">{t("mg.inc.subtitle")}</p></div></div>
      {error && <div className="error" role="alert">{error}</div>}
      <div className="controls">
        <label>{t("mg.xc.ev.severity")}<select value={sev} onChange={(e) => setSev(e.target.value)} data-testid="ev-sev"><option value="">{t("mg.xc.ev.all_f")}</option><option value="RED">{t("mg.xc.ev.sev_red")}</option><option value="YELLOW">{t("mg.xc.ev.sev_yellow")}</option></select></label>{" "}
        <label><input type="checkbox" checked={syn} onChange={(e) => setSyn(e.target.checked)} />{t("mg.xc.ev.include")}</label>
      </div>
      <Panel title={t("mg.alert.title")} testid="alert-panel"><AlertCenter onOpen={(a: AlertRec) => setSelected(a.alert_id)} /></Panel>
      <Panel title={t("mg.xc.ev.panel_title")}>
        <p className="muted">{t("mg.xc.ev.panel_note")}</p>
        {!data ? (error ? null : <Loading what={t("mg.ov.loading")} />) : data.events.length === 0 ? <Empty testid="ev-empty">{t("mg.xc.ev.empty")}</Empty> :
          <div className="table-wrap"><table data-testid="ev-table"><thead><tr><th>{t("mg.xc.ev.th_severity")}</th><th>{t("mg.xc.ev.th_source")}</th><th>{t("mg.xc.ev.th_nature")}</th><th>{t("mg.xc.ev.th_zone")}</th><th>{t("mg.xc.ev.th_start")}</th><th>{t("mg.xc.ev.th_end")}</th><th /></tr></thead>
            <tbody>{data.events.map((e: EventRow) => <tr key={e.event_id}><td><span className={`badge badge-${e.severity}`}>{e.severity === "RED" ? t("mg.xc.ev.sev_red") : t("mg.xc.ev.sev_yellow")}</span></td><td><code>{e.source_id}</code></td>
              <td><span className={`tag ${NATURE[e.nature]}`}>{tEnum("mg.xc.enum.nature", e.nature)}</span></td><td>{e.zone_id}</td><td>{fmtUs(e.start_us)}</td><td>{e.end_us == null ? t("mg.xc.ev.open") : fmtUs(e.end_us)}</td>
              <td>{e.kind === "run" ? <button onClick={() => openRun(e.source_id, null, e.start_us)} data-testid="ev-open-run">{t("mg.inc.go_run")}</button> : <button onClick={() => go("sesiones")}>{t("mg.inc.go_sessions")}</button>}</td></tr>)}</tbody></table></div>}
      </Panel>
      {selected && <IncidentDrawer alertId={selected} onClose={() => setSelected(null)} onSelect={setSelected} nav={{ openFeed, openRun, go, remember }} />}
    </section>
  );
}

export function Estadisticas() {
  const fn = useCallback(() => apiVs02.overview(), []);
  const { data: o, error } = useLoad<Overview>(fn);
  return (
    <section data-testid="view-estadisticas" aria-labelledby="h-est">
      <div className="page-head"><div><h2 id="h-est">{t("mg.nav.estadisticas")}</h2><p className="muted">{t("mg.xc.stats.sub")}</p></div></div>
      {error && <div className="error" role="alert">{error}</div>}
      {!o ? <Loading what={t("mg.ov.loading")} /> : <Panel title={t("mg.stats.populations")}>
        <ul><li>{tf("mg.xc.stats.real_red", { n: o.events.real.RED })}</li><li>{tf("mg.xc.stats.real_yellow", { n: o.events.real.YELLOW })}</li><li>{tf("mg.xc.stats.sources", { runs: o.sources.runs, sessions: o.sources.sessions, unverified: o.sources.unverified })}</li></ul>
        <p className="muted">{t("mg.stats.no_double")}</p>
        <div className="table-wrap"><table><thead><tr><th>{t("mg.xc.stats.th_figure")}</th><th>{t("mg.xc.stats.th_population")}</th></tr></thead><tbody>
          <tr><td>{t("mg.xc.stats.r1")}</td><td>{t("mg.xc.stats.r1d")}</td></tr>
          <tr><td>{t("mg.xc.stats.r2")}</td><td>{t("mg.xc.stats.r2d")}</td></tr>
          <tr><td>{t("mg.xc.stats.r3")}</td><td>{t("mg.xc.stats.r3d")}</td></tr>
          <tr><td>{t("mg.xc.stats.r4")}</td><td>{t("mg.xc.stats.r4d")}</td></tr></tbody></table></div>
      </Panel>}
      <TrendChart />
      <Panel title={t("mg.xc.stats.heatmaps")}><p><NA /></p></Panel>
    </section>
  );
}

export function Desarrollador() {
  const fnO = useCallback(() => apiVs02.overview(), []);
  const { data: o } = useLoad<Overview>(fnO);
  const { data: h } = useLoad(apiVs02.health);
  const fnB = useCallback(() => apiVs02.build(), []);
  const { data: b } = useLoad<BuildInfo>(fnB);
  return (
    <section data-testid="view-desarrollador" aria-labelledby="h-dev">
      <div className="page-head"><div><h2 id="h-dev">{t("mg.nav.desarrollador")}</h2><p className="muted">{t("mg.xc.vdev.sub")}</p></div></div>
      <Panel title={t("mg.dev.identity")}>
        {h && <p>{tf("mg.xc.vdev.service", { status: h.status, version: h.version, guard: h.network_guard ? t("mg.xc.vdev.guard_on") : t("mg.xc.vdev.guard_off") })}</p>}
        {o && <ul><li>{t("mg.xc.vdev.risk_engine")}<code>{o.engine.risk_engine}</code>{tf("mg.xc.vdev.risk_sem", { sem: o.engine.semantics_version })}</li><li>{t("mg.xc.vdev.tracking")}{o.engine.tracker}</li>
          <li>{tf("mg.xc.vdev.model_line", { id: o.model.model_id, arch: o.model.architecture })}</li><li>{t("mg.xc.vdev.weights_sha")}<code>{o.model.sha256}</code></li></ul>}
        <div data-testid="dev-build" data-known={b ? String(b.commit_known) : "loading"}>
          {!b ? <p className="muted">{t("mg.ov.loading")}</p> : b.commit_known
            ? <p>{t("mg.dev.build_known")}: <code data-testid="dev-build-sha">{b.commit}</code> · {b.tree_clean === null ? "" : b.tree_clean ? t("mg.dev.tree_clean") : t("mg.dev.tree_dirty")} · <span className="muted">{tf("mg.xc.vdev.measured", { utc: b.measured_utc })}</span></p>
            : <p className="muted">{t("mg.dev.build_unknown")}</p>}
          {b?.checkout_moved_since_start && <Banner kind="warn">{t("mg.dev.moved")}</Banner>}
        </div>
        <div data-testid="dev-oaf" data-kind={oafAcceptanceView(b?.oaf).kind}>
          {(() => {
            if (!b) return <p className="muted">{t("mg.dev.oaf")}</p>;
            const k = oafAcceptanceView(b.oaf).kind;
            if (k === "none") return <p className="muted">{t("mg.dev.oaf_none")}</p>;
            if (k === "invalid") return <p className="warn" data-testid="dev-oaf-invalid">{t("mg.dev.oaf_invalid")} ({b.oaf.invalid_why ?? "?"})</p>;
            return <p>{t("mg.dev.oaf_recorded")}: <b data-testid="dev-oaf-verdict">{b.oaf.verdict}</b>{tf("mg.xc.vdev.oaf_contract", { id: b.oaf.contract_id, rev: b.oaf.contract_revision })}{t("mg.dev.oaf_authority")}: {b.oaf.owner_authority_claimed_enforced === true ? t("mg.xc.vdev.oaf_enforced") : t("mg.xc.vdev.oaf_not_enforced")} ·{" "}
              <b data-testid="dev-oaf-acceptance">{k === "claimed_accepted" ? t("mg.dev.oaf_rec_acc") : t("mg.dev.oaf_not_acc")}</b><br /><span className="muted">{t("mg.dev.oaf_not_accepted")}{t("mg.xc.vdev.oaf_digest")}<code>{b.oaf.verdict_digest}</code></span></p>;
          })()}
        </div>
        <p className="muted">{t("mg.xc.vdev.no_secrets")}</p>
      </Panel>
      <DeveloperPanel />
    </section>
  );
}

export function Configuracion() {
  return (
    <section data-testid="view-configuracion" aria-labelledby="h-cfg">
      <div className="page-head"><div><h2 id="h-cfg">{t("mg.nav.configuracion")}</h2><p className="muted">{t("mg.xc.cfg.sub")}</p></div></div>
      <Panel title={t("mg.xc.cfg.priv_title")}>
        <p>{t("mg.xc.cfg.priv_a")}<b>{t("mg.xc.cfg.priv_b")}</b>{t("mg.xc.cfg.priv_c")}</p>
        <p>{t("mg.xc.cfg.zones")}</p>
        <p className="muted">{t("mg.cfg.metadata_note")}</p>
      </Panel>
      <AppearancePanel />
      <AlertSettingsPanel />
      <ProvidersPanel />
      <ExternalResultsPanel />
      <Panel title={t("mg.cfg.limits")} testid="cfg-limits">
        <ul>{["mg.lim.image_space_only", "mg.lim.uncalibrated_confidence", "mg.lim.coco_no_excavator_class", "mg.lim.not_safety_system", "mg.lim.person_untested"].map((id) => <li key={id}>{t(id)}</li>)}</ul>
        <Banner kind="info">{t("mg.cfg.resource_note")}</Banner>
        <h4>{t("mg.cfg.future")}</h4>
        <ul className="muted"><li>{t("mg.xc.cfg.fut_rtsp")}<b>{t("mg.na")}</b></li><li>{t("mg.xc.cfg.fut_cloud")}<b>{t("mg.na")}</b></li><li>{t("mg.xc.cfg.fut_msg")}<b>{t("mg.na")}</b></li><li>{t("mg.xc.cfg.fut_adapters")}<b>{t("mg.na")}</b></li></ul>
      </Panel>
    </section>
  );
}
