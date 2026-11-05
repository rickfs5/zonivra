import { useState } from "react";
import { t, tf } from "./i18n";
import { clientToFrame, polygonProblem, type Zone } from "./lib";
import type { ZoneIn } from "./api";

type Rect = { x: number; y: number; w: number; h: number };

export function refusalText(e: unknown): string {
  const m = /"msg_id"\s*:\s*"([^"]+)"/.exec(String(e));
  return m ? t(m[1]) : String(e).slice(0, 200);
}

export interface ZoneDrawing { active: boolean; draft: number[][]; start: () => void; cancel: () => void; undo: () => void; click: (cx: number, cy: number, rect: Rect) => void; take: () => number[][] }

export function useZoneDrawing(fw: number, fh: number): ZoneDrawing {
  const [active, setActive] = useState(false);
  const [draft, setDraft] = useState<number[][]>([]);
  return {
    active, draft,
    start: () => { setDraft([]); setActive(true); },
    cancel: () => { setDraft([]); setActive(false); },
    undo: () => setDraft((d) => d.slice(0, -1)),
    click: (cx, cy, rect) => {
      if (!active) return;
      const p = clientToFrame(cx, cy, rect, fw, fh);
      if (p) setDraft((d) => [...d, p]);
    },
    take: () => { const d = draft; setDraft([]); setActive(false); return d; },
  };
}

export function ZoneEditor(props: { zones: Zone[]; drawing: ZoneDrawing; fw: number; fh: number; busy: boolean; error: string | null; dirty: boolean;
  onChange: (zones: ZoneIn[]) => void | Promise<void>; onSave: () => void; hideSave?: boolean }) {
  const { zones, drawing, fw, fh } = props;
  const [typed, setName] = useState<string | null>(null);       // null = the operator has not edited the name: the default follows the active language
  const name = typed ?? tf("mg.xd.feeds.zone_name", { n: 1 });
  const [kind, setKind] = useState<"restricted" | "monitored">("restricted");
  const [local, setLocal] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const busy = props.busy || saving;
  const toIn = (z: Zone): ZoneIn => ({ zone_id: z.zone_id, kind: z.kind, polygon: z.polygon, name: z.name });
  /** Applies a change. A synchronous consumer (staged, saved later) is done at once; an asynchronous one (saved NOW, compare-and-swap) keeps the draft until the server accepted it, so a conflict
   *  or a transient failure never erases the operator's work (cycle-22 MG-ZONE-022); further edits are blocked while the save is in flight. */
  const apply = async (next: ZoneIn[], afterOk: () => void) => {
    const r = props.onChange(next);
    if (r && typeof (r as Promise<void>).then === "function") {
      setSaving(true);
      try { await r; setLocal(null); afterOk(); } catch (e) { setLocal(refusalText(e)); } finally { setSaving(false); }
    } else afterOk();
  };
  const close = () => {
    const pts = drawing.draft.map((p) => [...p]);          // validate a COPY: a refused polygon keeps every entered vertex so it can be corrected
    const problem = polygonProblem(pts, fw, fh);
    if (problem) { setLocal(t(problem)); return; }
    setLocal(null);
    let n = 1;
    const ids = new Set(zones.map((z) => z.zone_id));
    while (ids.has(`Z${n}`)) n++;
    void apply([...zones.map(toIn), { zone_id: `Z${n}`, kind, polygon: pts, name: name || tf("mg.xd.feeds.zone_name", { n }) }], () => drawing.cancel());
  };
  return (
    <div className="card" data-testid="zone-editor">
      <h2>{t("mg.zone.title")}</h2>
      {zones.length === 0 && <p className="muted" data-testid="zone-none">{t("mg.zone.none")}</p>}
      <ul className="zones">
        {zones.map((z) => (
          <li key={z.zone_id} data-testid="zone-item">
            <b>{z.zone_id}</b> {z.name} · {t(`mg.zone.kind.${z.kind}`)} · {z.polygon.length} {t("mg.xc.zone.vertices")}{" "}
            <button onClick={() => void apply(zones.filter((q) => q.zone_id !== z.zone_id).map(toIn), () => undefined)} disabled={busy}>{t("mg.zone.delete")}</button>
          </li>))}
      </ul>
      {!drawing.active ? (
        <>
          <label>{t("mg.zone.name")}: <input value={name} maxLength={60} onChange={(e) => setName(e.target.value)} data-testid="zone-name" /></label>
          <label>{t("mg.zone.kind")}: <select value={kind} onChange={(e) => setKind(e.target.value as "restricted" | "monitored")}>
            <option value="restricted">{t("mg.zone.kind.restricted")}</option><option value="monitored">{t("mg.zone.kind.monitored")}</option></select></label>
          <button onClick={drawing.start} data-testid="zone-draw" disabled={busy || zones.length >= 8}>{t("mg.zone.draw")}</button>
        </>
      ) : (
        <>
          <p className="muted" data-testid="zone-hint">{t("mg.zone.hint")} ({drawing.draft.length})</p>
          <button onClick={close} data-testid="zone-close" disabled={drawing.draft.length < 3 || saving}>{t("mg.zone.close")}</button>{" "}
          <button onClick={drawing.undo} disabled={!drawing.draft.length || saving}>{t("mg.zone.undo")}</button>{" "}
          <button onClick={drawing.cancel} data-testid="zone-cancel" disabled={saving}>{t("mg.zone.cancel")}</button>
        </>
      )}
      {(local || props.error) && <p className="error" data-testid="zone-error">{local ?? props.error}</p>}
      {!props.hideSave && <button className="primary" onClick={props.onSave} disabled={props.busy || !props.dirty} data-testid="zone-save">{t("mg.zone.save")}</button>}
    </div>
  );
}
