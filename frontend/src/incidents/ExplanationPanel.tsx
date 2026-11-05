import { useEffect, useRef, useState } from "react";
import { apiV2 } from "../api";
import { t } from "../i18n";
import { mayApply, visibleFor } from "./explanationState";
import { presentBackendText as pb } from "./explanationPresent";
import type { Held } from "./explanationState";

/** The explanation is requested by the operator, is SIMULATED, and never changes the incident or its severity. A real model explanation is not available in this build. */
export function ExplanationPanel({ alertId, rev }: { alertId: string; rev: number }) {
  const [held, setHeld] = useState<Held | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const cur = useRef(alertId);
  cur.current = alertId;
  const seq = useRef(0);                                                   // the latest request wins; a late response of an older request or of another incident is dropped
  useEffect(() => { seq.current += 1; setHeld(null); setErr(null); setBusy(false); }, [alertId]);   // an explanation belongs to ONE incident; never carried to another
  useEffect(() => () => { seq.current += 1; }, []);                         // unmounted: nothing may be applied
  const ask = () => {
    const my = ++seq.current, mineRev = rev, mineId = alertId;
    setBusy(true); setErr(null);
    void apiV2.explanation(alertId).then(
      (r) => { if (mayApply(my, seq.current, mineId, cur.current)) setHeld({ alertId: mineId, atRev: mineRev, x: r }); },
      (e: unknown) => { if (mayApply(my, seq.current, mineId, cur.current)) setErr(String((e as Error)?.message ?? e)); },
    ).finally(() => { if (mayApply(my, seq.current, mineId, cur.current)) setBusy(false); });
  };
  const shown = visibleFor(held, alertId);   // derived per render: another incident's result is never shown, not even transiently
  const x = shown?.x ?? null, atRev = shown?.atRev ?? null;
  return (
    <section data-testid="explanation-panel" aria-label={t("mg.exp.title")}>
      <h3>{t("mg.exp.title")}</h3>
      <p className="muted">{t("mg.exp.real_unavailable")}</p>
      <div className="controls"><button onClick={ask} disabled={busy} data-testid="explanation-ask">{t("mg.exp.ask")}</button></div>
      {err && <div className="error" role="alert">{err}</div>}
      {x && atRev !== null && atRev !== rev && <p className="banner warn" data-testid="explanation-outdated">{t("mg.exp.outdated")}</p>}
      {x && x.status === "REJECTED" && <p className="banner warn" data-testid="explanation-rejected">{t("mg.exp.rejected")} ({(x.codes ?? []).join(", ")}). {t("mg.exp.rejected_tail")}</p>}
      {x && x.status === "ACCEPTED" && x.report && (
        <div data-testid="explanation-result">
          <p><span className="badge badge-sim" data-testid="explanation-badge">{t("mg.exp.badge")}</span> <span className="muted">{t("mg.exp.decided_by")}: <b>{x.severity}</b></span></p>
          <p data-testid="explanation-summary">{pb(x.report.resumen, "summary")}</p>
          <p className="muted">{pb(x.report.explicacion_decision, "decision")}</p>
          <h4>{t("mg.exp.facts")}</h4><ul>{x.report.hechos_observados.map((h) => <li key={h.ref}>{pb(h.texto, "fact")}</li>)}</ul>
          <h4>{t("mg.exp.uncertainty")}</h4><ul>{x.report.incertidumbres.map((u) => <li key={u}>{pb(u, "uncertainty")}</li>)}</ul>
          <h4>{t("mg.exp.steps")}</h4><ul>{x.report.pasos_revision_humana.map((u) => <li key={u}>{pb(u, "step")}</li>)}</ul>
          <p className="muted" data-testid="explanation-validation">{t("mg.exp.validation_note")}</p>
        </div>)}
    </section>
  );
}
