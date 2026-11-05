import { apiV2 } from "../api";
import type { ProviderRow } from "../api";
import { t } from "../i18n";
import { usePoll } from "../shared/hooks";

const tr = (id: string, fallback: string) => (t(id) === id ? fallback : t(id));

/** Read-only inspection of what this build can run. There is NO control here that enables a provider or spending: the external states are fixed by the server. */
export function ProvidersPanel() {
  const { data: d, error } = usePoll(apiV2.providers, 5000);
  if (error) return <div className="error" role="alert">{error}</div>;
  if (!d) return <p className="muted">{t("mg.prov.loading")}</p>;
  const c = d.cost;
  return (
    <div className="card" data-testid="providers-panel">
      <h3>{t("mg.prov.title")}</h3>
      <p className="banner info" data-testid="providers-note">{t("mg.prov.intro")}</p>
      <section data-testid="providers-claims" aria-label={t("mg.zp.claims.title")}>
        <h4>{t("mg.zp.claims.title")}</h4>
        <ul>{(["rfdetr", "sam", "muse", "transport", "risk"] as const).map((k) => <li key={k} data-testid={`providers-claim-${k}`}>{t(`mg.zp.claims.${k}`)}</li>)}</ul>
      </section>
      <table className="table" data-testid="providers-table">
        <thead><tr><th>{t("mg.prov.col.provider")}</th><th>{t("mg.prov.col.state")}</th><th>{t("mg.prov.col.inference")}</th><th>{t("mg.prov.col.detail")}</th></tr></thead>
        <tbody>
          {d.providers.map((p: ProviderRow) => (
            <tr key={p.provider_id} data-testid={`provider-${p.provider_id}`}>
              <td>{tr(p.label_id, p.provider_id)}</td>
              <td>{p.states.map((s) => <span key={s} className={`chip chip-${s}`} data-state={s} style={{ marginRight: 4 }}>{tr(`mg.prov.state.${s}`, s)}</span>)}</td>
              <td>{p.real_inference ? t("mg.prov.inf.real_local") : t("mg.prov.inf.none")}</td>
              <td className="muted">{tr(p.note_id, "")}{p.provider_id === "local_rfdetr" && p.weights_sha256 ? <> · <span className="mono">{p.weights_sha256.slice(0, 12)}…</span> · {t("mg.prov.acc_not_validated")}</> : null}{p.detail ? ` · ${p.detail}` : ""}</td>
            </tr>))}
        </tbody>
      </table>
      <h4>{t("mg.prov.cost.title")}</h4>
      <ul data-testid="providers-cost">
        <li>{t("mg.prov.cost.calls")}: <b>{c.authorized_external_calls}</b> · {t("mg.prov.cost.spend")}: <b>US$ {c.authorized_external_spend_usd}</b></li>
        <li>{t("mg.prov.cost.transport")}: <span className="mono">{c.real_transport}</span> · {t("mg.prov.cost.kill")}: {c.kill_switch_engaged ? t("mg.prov.yes") : t("mg.prov.no")}</li>
        <li className="muted">{t("mg.prov.cost.note")}</li>
        <li className="muted">{t("mg.prov.cost.no_ui")}</li>
      </ul>
    </div>
  );
}
