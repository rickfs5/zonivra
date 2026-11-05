import { useCallback, useEffect, useMemo, useState } from "react";
import { apiVs02, type DeleteResult, type SessionRow } from "./api";
import { refusalText } from "./ZoneEditor";
import { t, tEnum, tf } from "./i18n";
import { useRef, type ReactNode } from "react";
import { useDialogFocus } from "./shared/dialog";
import { Empty, Loading } from "./shared/ui";

/** Destructive-action dialog: focus moves in, Tab stays inside, Escape cancels, focus returns to the button that opened it. */
function Dialog({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useDialogFocus(ref, onClose);
  return <><div className="drawer-scrim" onClick={onClose} aria-hidden="true" /><div className="modal" role="dialog" aria-modal="true" aria-label={t("mg.xc.sess.dialog_aria")} data-testid="del-dialog" ref={ref} tabIndex={-1}>{children}</div></>;
}

const RET_KEYS = ["frames", "metadata_only", "media_deleted"] as const;   // labels: presentation mapping mg.xc.enum.retention.*
const kb = (b: number) => (b < 1024 * 1024 ? `${Math.max(1, Math.round(b / 1024))} KB` : `${(b / 1048576).toFixed(1)} MB`);

/** Session management: list, filter, inspect, verify, delete. Deletion always shows a confirmation with the exact count and data types, then reports the REAL per-session outcome. */
export function Sessions() {
  const [rows, setRows] = useState<SessionRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sel, setSel] = useState<Set<string>>(new Set());
  const [filter, setFilter] = useState<"all" | SessionRow["retention"]>("all");
  const [confirm, setConfirm] = useState<{ ids: string[]; all: boolean; media?: string } | null>(null);
  const [typed, setTyped] = useState("");
  const [result, setResult] = useState<DeleteResult | null>(null);
  const [info, setInfo] = useState<{ f: () => string } | null>(null);   // a thunk, so the message follows the active language
  const load = useCallback(async () => { try { setRows(await apiVs02.sessionsList()); setError(null); } catch (e) { setError(refusalText(e)); } }, []);
  useEffect(() => { void load(); }, [load]);
  const shown = useMemo(() => (rows ?? []).filter((r) => filter === "all" || r.retention === filter), [rows, filter]);
  const toggle = (id: string) => setSel((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const exec = async () => {
    if (!confirm) return;
    try {
      if (confirm.media) {
        const row = (rows ?? []).find((x) => x.session_id === confirm.media);
        const r = await apiVs02.deleteMedia(confirm.media, row?.frames_analyzed ?? 0);
        setInfo({ f: () => (r.status === "deleted" ? tf("mg.xc.sess.info_deleted", { n: r.files_deleted, size: kb(r.bytes_deleted) }) : tf("mg.xc.sess.info_partial", { residue: String(r.residue) })) }); setResult(null);
      } else {
        const r = await apiVs02.deleteSessions(confirm.all ? { all: true, expected_count: (rows ?? []).length } : { ids: confirm.ids, expected_count: confirm.ids.length });
        setResult(r); setInfo(null);
      }
      setSel(new Set());
    } catch (e) { setError(refusalText(e)); setResult(null); }
    setConfirm(null); setTyped(""); await load();
  };
  const verify = async (id: string) => {
    try { const r = await apiVs02.verifySession(id); setInfo({ f: () => tf("mg.xc.sess.verify_info", { id, integrity: t(r.ok ? "mg.xc.sess.integrity_ok" : "mg.xc.sess.integrity_fail"), visual: t(r.visual_verification === "performed" ? "mg.xc.sess.vis_performed" : r.visual_verification === "unavailable_no_frames" ? "mg.xc.sess.vis_unavailable" : "mg.xc.sess.vis_not_requested") }) }); await load(); }
    catch (e) { setError(refusalText(e)); }
  };
  const count = confirm ? (confirm.media ? 1 : confirm.all ? (rows ?? []).length : confirm.ids.length) : 0;
  const withFrames = confirm ? (confirm.media ? 1 : (rows ?? []).filter((r) => (confirm.all || confirm.ids.includes(r.session_id)) && r.retention === "frames").length) : 0;
  return (
    <section data-testid="view-sesiones">
      <div className="page-head"><div><h2>{t("mg.nav.sesiones")}</h2><p className="muted">{t("mg.sess.subtitle")}</p></div></div>
      <p className="banner warn" role="status" data-testid="del-unavailable">{t("mg.sess.delete_unavailable")}</p>
      {error && <div className="error" role="alert">{error}</div>}
      {info && <p className="ok" role="status" data-testid="sess-info">{info.f()}</p>}
      {result && <div role="status" data-testid="del-result"><b>{tf("mg.xc.sess.result", { deleted: result.deleted, requested: result.requested })}</b>
        <ul>{result.results.map((r) => <li key={r.session_id}>{r.session_id}: {tEnum("mg.xc.enum.delete_status", r.status)}{r.reason ? ` (${r.reason})` : ""}</li>)}</ul></div>}
      <p className="muted">{t("mg.xc.sess.local_note")}</p>
      <div className="controls">
        <label>{t("mg.xc.sess.filter")}<select value={filter} onChange={(e) => setFilter(e.target.value as typeof filter)} data-testid="sess-filter">
          <option value="all">{t("mg.xc.sess.all_f")}</option>{RET_KEYS.map((k) => <option key={k} value={k}>{tEnum("mg.xc.enum.retention", k)}</option>)}</select></label>{" "}
        <button disabled onClick={() => setConfirm({ ids: [...sel], all: false })} data-testid="del-selected">{tf("mg.xc.sess.del_selected", { n: sel.size })}</button>{" "}
        <button disabled onClick={() => setConfirm({ ids: [], all: true })} data-testid="del-all">{t("mg.xc.sess.del_all")}</button>
      </div>
      {rows === null ? <Loading what={t("mg.xc.sess.loading")} /> : shown.length === 0 ? <Empty testid="sess-empty">{t("mg.xc.sess.empty")}</Empty> :
        <div className="table-wrap"><table data-testid="sess-table"><thead><tr><th></th><th>{t("mg.xc.sess.th_session")}</th><th>{t("mg.xc.sess.th_created")}</th><th>{t("mg.xc.sess.th_frames")}</th><th>{t("mg.xc.sess.th_origin")}</th><th>{t("mg.xc.sess.th_retention")}</th><th>{t("mg.xc.sess.th_size")}</th><th>{t("mg.xc.sess.th_trust")}</th><th></th></tr></thead>
          <tbody>{shown.map((r) => <tr key={r.session_id}>
            <td><input type="checkbox" aria-label={tf("mg.xc.sess.aria_select", { id: r.session_id })} checked={sel.has(r.session_id)} onChange={() => toggle(r.session_id)} /></td>
            <td><code>{r.session_id}</code></td><td>{r.created_utc}</td><td>{r.frames_analyzed}/{r.frame_count}</td><td>{r.source_origin ?? "—"}</td>
            <td data-testid={`ret-${r.session_id}`}>{tEnum("mg.xc.enum.retention", r.retention)}</td><td>{kb(r.bytes)}</td><td>{tEnum("mg.xc.enum.sess_trust", r.trust)}</td>
            <td><button onClick={() => void verify(r.session_id)}>{t("mg.xc.sess.verify")}</button>{" "}
              {r.retention === "frames" && <button disabled onClick={() => setConfirm({ ids: [r.session_id], all: false, media: r.session_id })}>{t("mg.xc.sess.del_images")}</button>}{" "}
              <button disabled onClick={() => setConfirm({ ids: [r.session_id], all: false })} data-testid={`del-${r.session_id}`} title={t("mg.sess.delete_unavailable")}>{t("mg.xc.sess.del")}</button></td></tr>)}</tbody></table></div>}
      {confirm && <Dialog onClose={() => { setConfirm(null); setTyped(""); }}>
        <h3>{t("mg.xc.sess.confirm_title")}</h3>
        <p><b>{count}</b> {t(confirm.media ? "mg.xc.sess.del_head_media" : count === 1 ? "mg.xc.sess.del_head_one" : "mg.xc.sess.del_head_many")}</p>
        <ul>{confirm.media ? <li>{t("mg.xc.sess.li_frames_only")}</li> : <>
          <li>{t("mg.xc.sess.li_all")}</li>
          <li>{tf("mg.xc.sess.li_with_frames", { n: withFrames })}</li></>}</ul>
        <p>{t("mg.xc.sess.untouched")}</p>
        <label>{tf("mg.xc.sess.type_prompt", { word: t("mg.xc.sess.confirm_word") })}<input value={typed} onChange={(e) => setTyped(e.target.value)} data-testid="del-typed" /></label>{" "}
        <button disabled={typed !== t("mg.xc.sess.confirm_word")} onClick={() => void exec()} data-testid="del-confirm">{t("mg.xc.sess.confirm_btn")}</button>{" "}
        <button onClick={() => { setConfirm(null); setTyped(""); }} data-testid="del-cancel">{t("mg.ui.cancel")}</button></Dialog>}
    </section>
  );
}
