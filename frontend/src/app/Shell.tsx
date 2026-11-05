import { useEffect, useState, type ReactNode } from "react";
import { AlertBadge, useAlerts } from "../Alerts";
import { t, tf } from "../i18n";
import { LOCALES, getLocale, setLocale, useLocale, type Locale } from "../locale";
import { Icon } from "../shared/ui";
import { useMonitor } from "./monitorStore";
import { ThemeSwitch } from "../shared/ThemeSwitch";

export const AREAS = ["resumen", "video", "camara", "feeds", "sesiones", "eventos", "simulaciones", "estadisticas", "desarrollador", "configuracion"] as const;
export type Area = (typeof AREAS)[number];

/** Operations Console shell: persistent sidebar (icon rail below 1100 px, off-canvas drawer below 820 px), top bar with honest system status, skip link, one <main> landmark. */
export function Shell({ area, setArea, children }: { area: Area; setArea: (a: Area) => void; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  useLocale();                                              // re-render this shell when the language changes
  const mon = useMonitor();
  const { list } = useAlerts();
  const unacked = list.filter((a) => !a.simulation && (a.status === "new" || a.status === "active")).length;
  useEffect(() => {
    if (!open) return;
    const k = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [open]);
  const go = (a: Area) => { setArea(a); setOpen(false); window.scrollTo?.(0, 0); };
  const o = mon.data;
  const reachable = o !== null && !mon.error;
  const workerOk = o?.health.scheduler_alive ?? false;
  const status = !o ? "bad" : mon.error ? "warn" : workerOk || o.active_feeds === 0 ? "on" : "bad";
  const statusText = !o ? (mon.error ? t("mg.shell.status.unreachable") : t("mg.shell.status.unreachable")) : mon.error ? t("mg.shell.stale") : t("mg.shell.status.alive");
  const r = o?.health.resources;
  const agg = o ? o.feeds.reduce((n, f) => n + (f.fps ?? 0), 0) : null;
  return (
    <div className="shell">
      <a className="skip-link" href="#contenido">{t("mg.shell.skip")}</a>
      <header className="topbar">
        <button className="menu-btn" aria-label={t("mg.shell.menu")} aria-expanded={open} aria-controls="sidebar" onClick={() => setOpen((v) => !v)}><Icon name="menu" /></button>
        <div className="brand"><span className="brand-mark" aria-hidden="true">{t("mg.brand").charAt(0)}</span><h1><span>{t("mg.brand")}</span> <small>· {t("mg.ui.title").split("·")[1]?.trim() ?? ""}</small></h1></div>
        <span className="chip-status chip-exp" data-testid="top-experimental" title={t("mg.ui.subtitle")} aria-label={t("mg.ui.subtitle")}>{t("mg.ui.subtitle_short")}</span>
        <div className="top-spacer" />
        <ThemeSwitch />
        <LocaleSwitch />
        <span className={`chip-status ${status}`} data-testid="top-status" data-reachable={reachable ? "yes" : "no"}><span className="led" />{statusText}</span>
        <span className="chip-status top-extra" data-testid="top-privacy"><span className="led" style={{ background: "var(--ok)" }} />{t("mg.shell.privacy_chip")}</span>
        <AlertBadge go={(a) => go(a as Area)} />
      </header>
      <div className={`scrim ${open ? "open" : ""}`} onClick={() => setOpen(false)} aria-hidden="true" />
      <aside className={`sidebar ${open ? "open" : ""}`} id="sidebar">
        <nav aria-label={t("mg.shell.nav")} style={{ display: "contents" }}>
          <div role="tablist" aria-orientation="vertical" style={{ display: "contents" }}>
            {AREAS.map((a) => (
              <button key={a} role="tab" className="nav-item" aria-selected={area === a} onClick={() => go(a)} data-testid={`nav-${a}`} title={t(`mg.nav.${a}`)}>
                <Icon name={a} /><span className="nav-label">{t(`mg.nav.${a}`)}</span>
                {a === "eventos" && unacked > 0 ? <span className="nav-count" aria-label={tf("mg.zl.unacked", { n: unacked })}>{unacked}</span> : null}
              </button>))}
          </div>
        </nav>
        <div className="side-status" data-testid="side-status">
          <b>{t("mg.shell.status.title")}</b>
          {!o ? <p className="muted" style={{ margin: "6px 0 0" }}>{mon.error ?? t("mg.shell.status.unreachable")}</p> : (
            <dl>
              <dt>{t("mg.shell.status.sources")}</dt><dd data-testid="side-sources">{o.active_feeds} / {o.active_limit}</dd>
              <dt>{t("mg.shell.status.model")}</dt><dd>{o.health.model_loaded ? t("mg.shell.status.loaded") : "—"}</dd>
              <dt>{t("mg.shell.status.worker")}</dt><dd className={workerOk || o.active_feeds === 0 ? "" : "warn"}>{workerOk ? t("mg.shell.status.alive") : o.active_feeds === 0 ? t("mg.shell.status.idle") : t("mg.shell.status.dead")}</dd>
              <dt>{t("mg.shell.status.fps")}</dt><dd>{agg === null || o.active_feeds === 0 ? t("mg.notmeasured") : `${agg.toFixed(1)} /s`}</dd>
              <dt>RAM</dt><dd>{r?.measured ? `${r.process_rss_mb ?? "–"} MB` : t("mg.notmeasured")}</dd>
            </dl>)}
          {mon.stale && <p className="stale-note" style={{ margin: "6px 0 0" }}>{t("mg.shell.stale")}</p>}
        </div>
      </aside>
      <div className="content" id="contenido" tabIndex={-1}>{children}<footer className="legal muted">{t("mg.ui.subtitle")} {t("mg.ov.red_meaning")}</footer></div>
    </div>
  );
}

const ENDONYM: Record<Locale, string> = { en: "English", es: "Español" };
/** Discoverable, deterministic language switch: each option is labelled in its own language and exposes its state (aria-pressed). The choice is stored locally; `?lang=en|es` overrides it for reproducible recordings. */
function LocaleSwitch() {
  const active = getLocale();
  return (
    <div className="locale-switch" role="group" aria-label={t("mg.zl.group")} data-testid="locale-switch">
      {LOCALES.map((l) => (
        <button key={l} lang={l} aria-pressed={active === l} aria-label={t(`mg.zl.switch_${l}`)} title={t(`mg.zl.switch_${l}`)} data-testid={`locale-${l}`} onClick={() => setLocale(l)}>{ENDONYM[l]}</button>))}
    </div>
  );
}
