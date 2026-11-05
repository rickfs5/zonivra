import { useCallback, useState } from "react";
import { apiV2 } from "../api";
import type { ExternalReview } from "../api";
import { t, tf } from "../i18n";
import { useLoad } from "../shared/hooks";
import { Banner, Panel } from "../shared/ui";
import { passportOf } from "./passport";
import { bannersFor, boxRect, bytesToBase64, integrityMsgId, linkageMsgId, refusalMsgId, scorePercent, stateMsgId } from "./externalPresent";

const readB64 = async (f: File) => bytesToBase64(new Uint8Array(await f.arrayBuffer()));

/** Owner-driven import and read-only review of an EXTERNAL SAM 3.1 result. Nothing here calls a provider; the result is never labelled inference, never classified and never scored for risk. */
export function ExternalResultsPanel() {
  const list = useLoad(useCallback(() => apiV2.externalResults(), []));
  const [sel, setSel] = useState<ExternalReview | null>(null);
  const [files, setFiles] = useState<{ manifest?: File; result?: File; image?: File }>({});
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ kind: "info" | "err"; id: string } | null>(null);

  const open = async (id: string) => { try { setSel(await apiV2.externalResult(id)); } catch (e) { setNote({ kind: "err", id: refusalMsgId(String(e)) }); } };
  const done = async (r: ExternalReview) => { setNote({ kind: "info", id: r.newly_imported === false ? "mg.ext.import.already" : "mg.ext.import.done" }); setSel(r); await list.reload(); };
  const demo = async () => { setBusy(true); try { await done(await apiV2.externalDemo()); } catch (e) { setNote({ kind: "err", id: refusalMsgId(String(e)) }); } finally { setBusy(false); } };
  const upload = async () => {
    if (!files.manifest || !files.result) return;
    setBusy(true);
    try {
      await done(await apiV2.importExternal({ manifest_b64: await readB64(files.manifest), result_b64: await readB64(files.result), ...(files.image ? { image_b64: await readB64(files.image) } : {}) }));
    } catch (e) { setNote({ kind: "err", id: refusalMsgId(String(e)) }); } finally { setBusy(false); }
  };

  return (
    <Panel title={t("mg.ext.title")} testid="external-results">
      <p className="muted">{t("mg.ext.intro")}</p>
      <section aria-label={t("mg.ext.map.title")} data-testid="external-map">
        <h4>{t("mg.ext.map.title")}</h4>
        <ul>{(["local", "import", "external", "pending"] as const).map((k) => <li key={k} data-testid={`external-map-${k}`}>{t(`mg.ext.map.${k}`)}</li>)}</ul>
      </section>
      <div className="controls" data-testid="external-demo">
        <b>{t("mg.ext.demo.title")}</b> <span className="muted">{t("mg.ext.demo.note")}</span>
        <button className="primary" disabled={busy} onClick={() => void demo()} data-testid="external-demo-btn">{t("mg.ext.demo.btn")}</button>
      </div>
      <details data-testid="external-import-form">
        <summary>{t("mg.ext.import.title")}</summary>
        <div className="controls">
          <label>{t("mg.ext.import.manifest")} <input type="file" accept=".json,application/json" data-testid="external-file-manifest" onChange={(e) => setFiles({ ...files, manifest: e.target.files?.[0] })} /></label>
          <label>{t("mg.ext.import.result")} <input type="file" data-testid="external-file-result" onChange={(e) => setFiles({ ...files, result: e.target.files?.[0] })} /></label>
          <label>{t("mg.ext.import.image")} <input type="file" accept="image/png,image/jpeg" data-testid="external-file-image" onChange={(e) => setFiles({ ...files, image: e.target.files?.[0] })} /></label>
          <button disabled={busy || !files.manifest || !files.result} onClick={() => void upload()} data-testid="external-import-btn">{busy ? t("mg.ext.import.busy") : t("mg.ext.import.btn")}</button>
        </div>
      </details>
      {note && <Banner kind={note.kind} testid="external-note">{note.kind === "err" ? <>{t("mg.ext.import.rejected")} {t(note.id)}</> : t(note.id)}</Banner>}
      <h4>{t("mg.ext.list.title")}</h4>
      {list.error && <div className="error" role="alert">{list.error}</div>}
      {!list.data && !list.error && <p className="muted">{t("mg.ext.loading")}</p>}
      {list.data && list.data.results.length === 0 && <p className="muted" data-testid="external-none">{t("mg.ext.list.none")}</p>}
      {list.data && list.data.results.length > 0 && (
        <div className="table-wrap"><table className="table" data-testid="external-list">
          <thead><tr><th>{t("mg.ext.col.id")}</th><th>{t("mg.ext.col.state")}</th><th>{t("mg.ext.col.objects")}</th><th>{t("mg.ext.col.when")}</th><th /></tr></thead>
          <tbody>{list.data.results.map((r) => (
            <tr key={r.import_id} data-testid={`external-row-${r.import_id}`}>
              <td className="mono">{r.import_id}</td><td>{r.integrity === "VERIFIED" ? t(stateMsgId(r.verification_state ?? undefined)) : t(integrityMsgId(r.integrity))}</td>
              <td>{r.objects}</td><td className="mono">{r.imported_utc ?? "—"}</td>
              <td><button onClick={() => void open(r.import_id)} data-testid={`external-open-${r.import_id}`}>{t("mg.ext.list.open")}</button></td>
            </tr>))}</tbody>
        </table></div>
      )}
      {sel && <Review r={sel} />}
    </Panel>
  );
}

function Review({ r }: { r: ExternalReview }) {
  if (r.integrity !== "VERIFIED" || !r.verification || !r.source_image) {
    return <div data-testid="external-review"><Banner kind="err" testid="external-integrity">{t(integrityMsgId(r.integrity))}</Banner></div>;
  }
  const v = r.verification, w = r.source_image.width, h = r.source_image.height, objs = r.objects ?? [];
  const row = (label: string, value: React.ReactNode, testid: string) => <tr><th scope="row">{label}</th><td data-testid={testid}>{value}</td></tr>;
  return (
    <div data-testid="external-review" data-origin={r.origin} data-import-id={r.import_id}>
      {bannersFor(r).map((b) => <Banner key={b} kind={b === "unproven" || b === "synthetic" ? "warn" : "info"} testid={`external-banner-${b}`}>{t(`mg.ext.banner.${b}`)}</Banner>)}
      <Passport r={r} />
      <details className="ext-verification" open><summary>{t("mg.ext.verification")}</summary>
      <table className="table"><tbody>
        {row(t("mg.ext.f.origin"), <b>EXTERNAL_MANUAL_PROVIDER_RESULT</b>, "external-origin")}
        {row(t("mg.ext.f.provider"), r.provider, "external-provider")}
        {row(t("mg.ext.f.model"), r.model, "external-model")}
        {row(t("mg.ext.f.state"), t(stateMsgId(v.state)), "external-state")}
        {row(t("mg.ext.f.integrity"), t(integrityMsgId(r.integrity)), "external-integrity-state")}
        {row(t("mg.ext.f.schema"), <span data-profile={r.grammar?.profile ?? "FIXTURE_V1"}>{t(r.grammar ? "mg.ext.schema.observed" : "mg.ext.schema.fixture")}</span>, "external-schema")}
        {row(t("mg.ext.f.input"), <span className="mono">{v.input_image_sha256}</span>, "external-input-digest")}
        {row(t("mg.ext.f.linkage"), t(linkageMsgId(v.image_linkage)), "external-linkage")}
        {row(t("mg.ext.f.response"), <span className="mono">{v.result_sha256}</span>, "external-response-digest")}
        {row(t("mg.ext.f.manifest"), <span className="mono">{v.manifest_sha256}</span>, "external-manifest-digest")}
        {row(t("mg.ext.f.request"), v.request_body_sha256 ? <span className="mono">{v.request_body_sha256}</span> : t("mg.ext.f.not_declared"), "external-request-digest")}
        {row(t("mg.ext.f.captured"), <span className="mono">{r.capture?.captured_utc}</span>, "external-captured")}
        {row(t("mg.ext.f.declared_by"), r.capture?.declared_by, "external-declared-by")}
        {row(t("mg.ext.f.concept"), r.concept_requested, "external-concept")}
      </tbody></table></details>
      <h4>{t("mg.ext.seg.title")}</h4>
      <p className="muted">{t("mg.ext.seg.note")}</p>
      {objs.length === 0 ? <p data-testid="external-no-segments">{t("mg.ext.seg.none")}</p> : (
        <>
          <figure data-testid="external-overlay" style={{ margin: "8px 0" }}>
            <svg viewBox={`0 0 ${w} ${h}`} role="img" aria-label={t("mg.ext.seg.overlay")} style={{ maxWidth: 480, width: "100%", background: "var(--sunken)", border: "1px solid var(--line-2)" }}>
              {r.has_image && <image href={apiV2.externalImageUrl(r.import_id)} x={0} y={0} width={w} height={h} />}
              {objs.map((o) => { const b = boxRect(o.box, w, h); return b && <g key={o.object_id}><rect {...b} fill="none" stroke="#38bdf8" strokeWidth={2} data-testid={`external-box-${o.object_id}`} /><text x={b.x + 3} y={b.y + 13} fill="#38bdf8" stroke="#0b1020" strokeWidth={3} paintOrder="stroke" fontSize={12}>#{o.object_id}</text></g>; })}
            </svg>
            <figcaption className="muted">{r.has_image ? t("mg.ext.seg.overlay") : t("mg.ext.seg.overlay_noimg")}</figcaption>
          </figure>
          <div className="table-wrap"><table className="table" data-testid="external-segments">
            <thead><tr><th>{t("mg.ext.seg.id")}</th><th>{t("mg.ext.seg.box")}</th><th>{t("mg.ext.seg.score")}</th><th>{t("mg.ext.seg.class")}</th><th>{t("mg.ext.seg.mask")}</th></tr></thead>
            <tbody>{objs.map((o) => (
              <tr key={o.object_id} data-testid={`external-seg-${o.object_id}`}>
                <td>{o.object_id}</td><td className="mono">{o.box.x1}, {o.box.y1}, {o.box.x2}, {o.box.y2}</td>
                <td>{scorePercent(o.provider_score_permille) ?? t("mg.ext.seg.score_none")}</td>
                <td data-testid={`external-class-${o.object_id}`}>{t("mg.ext.seg.unclassified")}</td>
                <td>{tf("mg.ext.seg.mask_not_rendered", { rows: o.mask.rows, cols: o.mask.cols, enc: o.mask.encoding })}</td>
              </tr>))}</tbody>
          </table></div>
        </>
      )}
      <h4>{t("mg.ext.limits.title")}</h4>
      <ul data-testid="external-limits">{[0, 1, 2, 3, 4].map((i) => <li key={i}>{t(`mg.ext.limit.${i}`)}</li>)}{v.state === "SYNTHETIC_FIXTURE" && <li>{t("mg.ext.limit.5")}</li>}</ul>
    </div>
  );
}

/** Evidence passport: a glanceable, deliberately conservative summary derived from the verified record (see passport.ts). It never says more than the record proves. */
function Passport({ r }: { r: ExternalReview }) {
  const p = passportOf(r);
  const items: [string, string, string][] = [
    ["origin", "ok", `${t("mg.ext.pp.origin")}`], ["trust", "warn", t("mg.ext.pp.trust")], ["classes", "warn", t("mg.ext.pp.classes")],
    ["conf", "warn", t(p.confidence === "not_reported" ? "mg.ext.pp.conf_none" : "mg.ext.pp.conf_score")], ["masks", "idle", t("mg.ext.pp.masks")], ["exec", "idle", t("mg.ext.pp.exec")], ["norisk", "idle", t("mg.ext.pp.norisk")]];
  return (
    <div className="ext-passport" data-testid="external-passport" data-synthetic={p.synthetic ? "yes" : "no"} data-confidence={p.confidence}>
      <div className="ext-passport-head"><h4>{t("mg.ext.pp.title")}</h4><span className="mono">{t("mg.ext.pp.model")}: {r.model} · {t("mg.ext.pp.segments")}: {p.objects}</span></div>
      {p.synthetic && <p className="pp-synthetic" data-testid="external-passport-synthetic">{t("mg.ext.pp.synthetic")}</p>}
      <ul className="pp-list">{items.map(([k, tone, text]) => <li key={k} className={`pp-item pp-${tone}`} data-pp={k}><span className="pp-glyph" aria-hidden="true">{tone === "ok" ? "●" : tone === "warn" ? "▲" : "○"}</span>{text}</li>)}</ul>
    </div>
  );
}
