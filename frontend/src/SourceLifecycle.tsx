import { useEffect, useState } from "react";
import { apiV2, type FeedLifecycle } from "./api";
import { t } from "./i18n";
import { fmtUs } from "./shared/ui";

const tr = (id: string, fallback: string) => (t(id) === id ? fallback : t(id));

/** V07: what this application itself recorded for the CURRENT run of one source (start / pause / resume / stop / failure and the first sightings). It adds no fact of its own; a source that has not run says so. */
export function SourceLifecycle({ feedId, state, runKey }: { feedId: string; state: string; runKey: string }) {
  const [doc, setDoc] = useState<FeedLifecycle | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void apiV2.feedLifecycle(feedId).then((r) => { if (live) { setDoc(r); setErr(null); } }, (e) => { if (live) setErr(String(e)); });
    return () => { live = false; };
  }, [feedId, state, runKey]);
  if (err) return <p className="error" role="alert" data-testid="feed-life-error">{err}</p>;
  if (!doc) return <p className="muted" data-testid="feed-life-loading">{t("mg.inv.loading")}</p>;
  if (!doc.recorded) return <p className="muted" data-testid="feed-life-none">{t(doc.reason ?? "mg.inc.life.not_recorded")}</p>;
  const label = (e: FeedLifecycle["events"][number]) => e.kind === "risk_changed"
    ? `${t("mg.inv.tl.risk_changed")} ${e.zone_id}: ${e.from ? t(`mg.risk.badge.${e.from}`) : t("mg.inv.tl.first_eval")} → ${e.to ? t(`mg.risk.badge.${e.to}`) : "—"}`
    : `${tr(`mg.inv.tl.${e.kind}`, e.kind)}${e.count ? ` (${e.count})` : ""}${e.error ? ` · ${e.error}` : ""}`;
  return (
    <div data-testid="feed-life-list">
      <ol className="tl-list">{doc.events.map((e) => (
        <li key={e.seq} data-kind={e.kind} data-group={e.group}><span className="mono">{e.at_utc.slice(11, 23)}</span>{e.t_us !== null && e.group === "observed" ? <span className="mono muted"> · {fmtUs(e.t_us)}</span> : null} {label(e)}</li>))}</ol>
      {doc.truncated && <p className="muted">{t("mg.inv.life.truncated")}</p>}
    </div>
  );
}
