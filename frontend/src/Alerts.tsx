import { useCallback, useEffect, useState, useSyncExternalStore } from "react";
import { apiV2, type AlertRec } from "./api";
import { drain, pruned } from "./alertsync";
import { refusalText } from "./ZoneEditor";
import { sourceLabel, t, tEnum, tf } from "./i18n";
import { alertProvenance } from "./lib";
import { ProvTag } from "./shared/ui";

// ------------------------------------------------------------------ per-browser consent (a convenience stored only in THIS browser; both start OFF)
export interface LocalPrefs { browser: boolean; sound: boolean }
const PREF_KEY = "mg.alert.prefs";
export function readPrefs(): LocalPrefs {
  try { const v = JSON.parse(window.localStorage.getItem(PREF_KEY) ?? "{}"); return { browser: v.browser === true, sound: v.sound === true }; } catch { return { browser: false, sound: false }; }
}
export function writePrefs(p: LocalPrefs) {
  try { window.localStorage.setItem(PREF_KEY, JSON.stringify(p)); } catch { /* storage may be unavailable: the page works without it */ }
  window.dispatchEvent(new Event("mg-prefs"));
}

// ------------------------------------------------------------------ audio (needs a user gesture; never starts by itself)
let audio: AudioContext | null = null;
export function unlockAudio(): boolean {
  try {
    const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctx) return false;
    audio = audio ?? new Ctx();
    void audio.resume();
    return audio.state !== "closed";
  } catch { return false; }
}
export function beep(severity: string): boolean {
  if (!audio || audio.state !== "running") return false;
  try {
    const n = severity === "RED" ? 3 : 1, f = severity === "RED" ? 880 : 520;
    for (let i = 0; i < n; i++) {
      const o = audio.createOscillator(), g = audio.createGain(), t0 = audio.currentTime + i * 0.28;
      o.frequency.value = f; g.gain.setValueAtTime(0.0001, t0); g.gain.exponentialRampToValueAtTime(0.25, t0 + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.22);
      o.connect(g).connect(audio.destination); o.start(t0); o.stop(t0 + 0.25);
    }
    return true;
  } catch { return false; }
}

// ------------------------------------------------------------------ shared alert store (one poller for the whole page)
const store = { alerts: new Map<string, AlertRec>(), rev: 0, error: null as string | null, listeners: new Set<() => void>(), timer: 0, version: 0, attempted: new Set<string>() };
const emit = () => { store.version++; store.listeners.forEach((l) => l()); };

function eligibleTitle(a: AlertRec): string { return a.simulation ? tf("mg.xc.alert.notif_title_sim", { sev: t(`mg.alert.sev.${a.severity}`), sim: t("mg.alert.sim") }) : tf("mg.xc.alert.notif_title", { sev: t(`mg.alert.sev.${a.severity}`) }); }

async function deliver(a: AlertRec) {
  const prefs = readPrefs();
  const report = (ch: "browser" | "sound", st: "delivered" | "denied" | "failed" | "unsupported", d?: string) => { void apiV2.alertDelivery(a.alert_id, ch, st, d).then((r) => { store.alerts.set(r.alert_id, r); emit(); }).catch(() => undefined); };
  if (a.status !== "resolved" && a.delivery.browser.status === "pending" && prefs.browser && !store.attempted.has(a.alert_id + ":browser")) {
    if (!("Notification" in window)) { store.attempted.add(a.alert_id + ":browser"); report("browser", "unsupported", "Notification API missing"); }
    else if (Notification.permission === "denied") { store.attempted.add(a.alert_id + ":browser"); report("browser", "denied", "permission denied"); }
    else if (Notification.permission === "granted") {
      store.attempted.add(a.alert_id + ":browser");
      try { new Notification(eligibleTitle(a), { body: tf("mg.xc.alert.notif_body", { label: sourceLabel(a.source.label), zone: a.zone_id }), tag: a.alert_id, silent: true }); report("browser", "delivered"); } catch (e) { report("browser", "failed", String(e).slice(0, 100)); }
    }   // "default": the user has not decided; it stays pending until they grant or deny in Configuración
  }
  if (a.status !== "resolved" && a.delivery.sound.status === "pending" && prefs.sound && !store.attempted.has(a.alert_id + ":sound")) {
    if (beep(a.severity)) { store.attempted.add(a.alert_id + ":sound"); report("sound", "delivered"); }   // not unlocked yet (no gesture since the page loaded): it stays pending
  }
}

let ticks = 0;
async function tick() {
  try {
    const d = await drain((after) => apiV2.alerts(`order=asc&after=${after}&limit=200`).then((r) => ({ alerts: r.alerts, more: r.more, next_after: r.next_after })), store.rev);
    store.error = null;
    for (const a of d.alerts) store.alerts.set(a.alert_id, a);
    store.rev = d.cursor;                                   // only to the last record actually received
    for (const a of d.alerts) void deliver(a);
    if (++ticks % 15 === 0) {                               // every ~30 s: forget what the server has pruned, so this page's memory stays bounded
      const { ids } = await apiV2.alertIds();
      for (const id of pruned(store.alerts.keys(), ids)) store.alerts.delete(id);
      for (const k of [...store.attempted]) if (!store.alerts.has(k.split(":")[0])) store.attempted.delete(k);
    }
    emit();
  } catch (e) { store.error = refusalText(e); emit(); }
}

// a change of the user's consent (notifications / sound) re-examines the alerts still waiting for a channel
if (typeof window !== "undefined") window.addEventListener("mg-prefs", () => store.alerts.forEach((a) => void deliver(a)));

function subscribe(l: () => void) {
  store.listeners.add(l);
  if (store.listeners.size === 1) { void tick(); store.timer = window.setInterval(() => void tick(), 2000); }
  return () => { store.listeners.delete(l); if (!store.listeners.size) window.clearInterval(store.timer); };
}
export function useAlerts() {
  useSyncExternalStore(subscribe, () => store.version);
  const list = Array.from(store.alerts.values()).sort((a, b) => b.rev - a.rev);
  return { list, error: store.error, refresh: tick };
}

/** Acknowledge / resolve through the shared store (used by the alert table and by the incident drawer, so both always show the same record). */
export async function actOnAlert(fn: () => Promise<AlertRec>): Promise<string | null> {
  try { const r = await fn(); store.alerts.set(r.alert_id, r); emit(); return null; } catch (e) { return refusalText(e); }
}
export const ackAlert = (id: string) => actOnAlert(() => apiV2.alertAck(id));
export const resolveAlert = (id: string) => actOnAlert(() => apiV2.alertResolve(id));
export const sourceTagOf = (a: AlertRec) => sourceTag(a);

export function usePrefs(): [LocalPrefs, (p: LocalPrefs) => void] {
  const [p, setP] = useState(readPrefs);
  useEffect(() => { const f = () => setP(readPrefs()); window.addEventListener("mg-prefs", f); return () => window.removeEventListener("mg-prefs", f); }, []);
  return [p, writePrefs];
}

export function AlertBadge({ go }: { go: (a: string) => void }) {
  const { list } = useAlerts();
  const open = list.filter((a) => a.status === "new" || a.status === "active");
  const red = open.filter((a) => a.severity === "RED" && !a.simulation).length;
  return (
    <button className={`alert-badge ${red ? "alert-badge-red" : ""}`} onClick={() => go("eventos")} data-testid="alert-badge" data-count={open.length} data-red={red} aria-live="polite">
      {t("mg.alert.badge")}: <b>{open.length}</b>{red ? tf("mg.xc.alert.badge_red", { n: red }) : ""}
    </button>
  );
}

const SEV_CLASS: Record<string, string> = { RED: "badge-RED", YELLOW: "badge-YELLOW", UNKNOWN: "badge-UNKNOWN" };
const sourceTag = (a: AlertRec) => (a.loaded_from_store ? t("mg.xc.alert.restored") : a.source.provenance === "synthetic_scripted" ? t("mg.alert.sim") : a.source.provenance === "repeated_source_simulation" ? t("mg.alert.repeated") : a.source.kind === "feed" ? t("mg.alert.file") : t("mg.xc.alert.browser_camera"));

export function AlertCenter({ onOpen }: { onOpen?: (a: AlertRec) => void } = {}) {
  const { list, error } = useAlerts();
  const [status, setStatus] = useState("");
  const [sim, setSim] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const shown = list.filter((a) => (!status || a.status === status) && (sim || !a.simulation));
  useEffect(() => {
    const ids = shown.filter((a) => a.status === "new").map((a) => a.alert_id);
    if (ids.length) void apiV2.alertSeen(ids).then(() => undefined).catch(() => undefined);
  });   // displaying an alert marks it «Activa»; acknowledging is a separate, explicit act
  const act = useCallback(async (fn: () => Promise<AlertRec>) => { try { const r = await fn(); store.alerts.set(r.alert_id, r); emit(); setErr(null); } catch (e) { setErr(refusalText(e)); } }, []);
  const sum = { open: list.filter((a) => a.status !== "resolved").length, merged: list.reduce((n, a) => n + a.suppressed_count, 0) };
  return (
    <div data-testid="alert-center">
      {(error || err) && <div className="error" role="alert">{err ?? error}</div>}
      <div className="controls">
        <label>{t("mg.xc.alert.status")}<select value={status} onChange={(e) => setStatus(e.target.value)} data-testid="alert-filter-status"><option value="">{t("mg.xc.alert.all_m")}</option>{["new", "active", "acknowledged", "resolved"].map((s) => <option key={s} value={s}>{t(`mg.alert.status.${s}`)}</option>)}</select></label>{" "}
        <label><input type="checkbox" checked={sim} onChange={(e) => setSim(e.target.checked)} data-testid="alert-filter-sim" />{t("mg.xc.alert.include_sim")}</label>{" "}
        <span className="muted" data-testid="alert-summary" data-open={sum.open}>{tf("mg.xc.alert.summary", { open: sum.open, merged_label: t("mg.alert.merged"), merged: sum.merged })}</span>
      </div>
      {shown.length === 0 ? <p data-testid="alert-empty" className="muted">{t("mg.alert.none")}</p> :
        <table data-testid="alert-table"><thead><tr><th>{t("mg.xc.alert.th_severity")}</th><th>{t("mg.xc.alert.th_origin")}</th><th>{t("mg.xc.alert.th_zone")}</th><th>{t("mg.xc.alert.th_created")}</th><th>{t("mg.xc.alert.th_status")}</th><th>{t("mg.xc.alert.th_delivery")}</th><th /></tr></thead>
          <tbody>{shown.map((a) => (
            <tr key={a.alert_id} className={onOpen ? "clickable" : undefined} onClick={onOpen ? (e) => { if (!(e.target as HTMLElement).closest("button,a,input,select")) onOpen(a); } : undefined} data-testid="alert-row" data-alert-id={a.alert_id} data-severity={a.severity} data-status={a.status} data-simulation={a.simulation ? "yes" : "no"} data-feed={a.feed_id ?? ""}>
              <td><span className={`badge ${SEV_CLASS[a.severity]}`}>{t(`mg.alert.sev.${a.severity}`)}</span>{a.kind === "diagnostic" && <div className="muted">{t("mg.alert.notice_unknown")}</div>}</td>
              <td>{sourceLabel(a.source.label)}<div><ProvTag kind={alertProvenance(a)} /> <span className={a.simulation || a.loaded_from_store ? "badge badge-YELLOW" : "muted"} data-testid="alert-origin" data-prov={alertProvenance(a)}>{sourceTag(a)}</span></div></td>
              <td>{a.zone_id}{a.suppressed_count ? <div className="muted">+{a.suppressed_count} {t("mg.alert.merged")}</div> : null}</td>
              <td className="mono">{a.created_utc.replace("T", " ").replace("Z", "")}</td>
              <td>{t(`mg.alert.status.${a.status}`)}{a.resolved_by ? <div className="muted">{a.resolved_by}</div> : null}</td>
              <td data-testid="alert-delivery" data-state={a.delivery_state} className={a.delivery_state === "failed" ? "error" : "muted"}>{t(`mg.alert.delivery.${a.delivery_state}`)}
                <div className="muted">{tf("mg.xc.alert.channels", { browser: tEnum("mg.xc.enum.channel_status", a.delivery.browser.status), sound: tEnum("mg.xc.enum.channel_status", a.delivery.sound.status) })}</div></td>
              <td>{onOpen && <><button onClick={() => onOpen(a)} data-testid="alert-detail" aria-label={`${t("mg.inc.detail")} ${a.alert_id}`}>{t("mg.inc.detail")}</button>{" "}</>}{a.status !== "resolved" && <>
                {a.status !== "acknowledged" && <button onClick={() => void act(() => apiV2.alertAck(a.alert_id))} data-testid="alert-ack">{t("mg.alert.ack")}</button>}{" "}
                <button onClick={() => void act(() => apiV2.alertResolve(a.alert_id))} data-testid="alert-resolve">{t("mg.alert.resolve")}</button></>}</td>
            </tr>))}</tbody></table>}
    </div>
  );
}
