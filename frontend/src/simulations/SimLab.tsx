import { useCallback, useEffect, useState } from "react";
import { apiVs02, type RiskReply } from "../api";
import { useAlerts } from "../Alerts";
import { t } from "../i18n";
import { formatMs } from "../lib";
import { Banner, Empty, Panel, ProvTag, RiskBadge, type Risk } from "../shared/ui";
import { refusalText } from "../ZoneEditor";
import { ScenariosPanel } from "./ScenariosPanel";

interface Done { runId: string; base: string; risk: RiskReply }

/** Simulation Lab: the scripted RED scenario with its explanation, its YELLOW → RED progression read from the REAL risk engine, the alerts it derived, and links to the incident and to the MP4 workspace. Synthetic at every surface. */
export function SimLab({ openRun, go, openAlert }: { openRun: (id: string, base: string | null, tUs?: number) => void; go: (a: string) => void; openAlert: (id: string) => void }) {
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const [base, setBase] = useState<string | null | undefined>(undefined);   // undefined = loading, null = no real run to base the scenario on
  const [done, setDone] = useState<Done | null>(null);
  const { list, refresh } = useAlerts();
  useEffect(() => {
    void apiVs02.runsList().then((runs) => setBase(runs.filter((r) => r.origin === "model_inference").sort((a, b) => b.created_utc.localeCompare(a.created_utc))[0]?.run_id ?? null)).catch((e) => { setErr(refusalText(e)); setBase(null); });
  }, []);
  const run = useCallback(async () => {
    if (!base) { setErr(t("mg.sim.need_run")); return; }
    setBusy(true); setErr(null);
    try {
      const { run_id } = await apiVs02.simulate(base);
      const risk = await apiVs02.risk("runs", run_id);
      setDone({ runId: run_id, base, risk });
      await refresh();
    } catch (e) { setErr(refusalText(e)); } finally { setBusy(false); }
  }, [base, refresh]);
  const zone = done ? Object.values(done.risk.evaluation.zones)[0] : null;
  const derived = done ? list.filter((a) => a.source.source_id.startsWith(`sim:${done.runId}`)) : [];
  return (
    <section data-testid="view-simulaciones" aria-labelledby="h-sim">
      <div className="page-head"><div><h2 id="h-sim">{t("mg.nav.simulaciones")}</h2><p className="muted">{t("mg.sim.lab_sub")}</p></div><ProvTag kind="synthetic" /></div>
      <Banner kind="warn" testid="sim-banner">{t("mg.xc.sim.banner_a")}<b>{t("mg.xc.sim.banner_b")}</b>{t("mg.xc.sim.banner_c")}<b>{t("mg.xc.sim.banner_d")}</b>{t("mg.xc.sim.banner_e")}</Banner>
      <Panel title={t("mg.sim.scenario")}>
        <p>{t("mg.sim.explain")}</p>
        <ol className="steps"><li><b>1.</b> {t("mg.sim.step1")}</li><li><b>2.</b> {t("mg.sim.step2")}</li><li><b>3.</b> {t("mg.sim.step3")}</li></ol>
        {base === undefined ? <p className="loading">{t("mg.ov.loading")}</p> : base === null ? <Banner kind="warn" testid="sim-no-base">{t("mg.sim.need_run")}</Banner> : <p className="muted">{t("mg.sim.base")}: <code>{base}</code></p>}
        {err && <div className="error" role="alert">{err}</div>}
        <div className="controls">
          <button className="primary" onClick={() => void run()} disabled={busy || !base} data-testid="sim-red-view">{busy ? t("mg.sim.running") : t("mg.xc.sim.red_button")}</button>
          <button onClick={() => go("feeds")} data-testid="sim-back-real">{t("mg.sim.back_real")}</button>
        </div>
      </Panel>
      {!done ? <Empty testid="sim-empty">{t("mg.sim.not_run")}</Empty> : (
        <div className="grid main-side" data-testid="sim-result" data-run-id={done.runId}>
          <Panel title={t("mg.sim.progress")} testid="sim-progress">
            <p><ProvTag kind="synthetic" /> <span className="muted">{t("mg.risk.synthetic_notice")}</span></p>
            <div className="table-wrap"><table><thead><tr><th>{t("mg.sim.t")}</th><th>{t("mg.inc.state")}</th><th>{t("mg.inc.causes")}</th></tr></thead>
              <tbody>{(zone?.timeline ?? []).map((iv, i) => <tr key={i} data-testid="sim-interval" data-state={iv.state}><td className="mono">{formatMs(iv.start_us)} → {formatMs(iv.end_us)}</td><td><RiskBadge state={iv.state as Risk} />{iv.pending ? t("mg.xc.sim.pending") : ""}</td><td className="muted">{iv.causes.join(", ") || "—"}</td></tr>)}</tbody></table></div>
            <h4>{t("mg.risk.events")}</h4>
            <ul className="events">{(zone?.events ?? []).map((e) => <li key={e.event_id} data-testid="sim-event" data-severity={e.severity}><span className={`badge badge-${e.severity}`}>{t(`mg.risk.badge.${e.severity}`)}</span> {formatMs(e.start_us)} → {e.end_us === null ? "…" : formatMs(e.end_us)} · {e.rule_id}</li>)}</ul>
            <div className="controls"><button className="primary" onClick={() => openRun(done.runId, done.base)} data-testid="sim-open-video">{t("mg.sim.open_video")}</button></div>
          </Panel>
          <Panel title={t("mg.sim.derived")} testid="sim-derived">
            {derived.length === 0 ? <p className="muted" data-testid="sim-no-alerts">{t("mg.sim.no_alerts")}</p> : (
              <ul className="events">{derived.map((a) => <li key={a.alert_id} data-testid="sim-alert"><span className={`badge badge-${a.severity}`}>{t(`mg.risk.badge.${a.severity}`)}</span> <ProvTag kind="synthetic" /> {t(`mg.alert.status.${a.status}`)}{" "}
                <button onClick={() => openAlert(a.alert_id)} data-testid="sim-alert-detail">{t("mg.inc.detail")}</button></li>)}</ul>)}
          </Panel>
        </div>)}
      <ScenariosPanel />
    </section>
  );
}
