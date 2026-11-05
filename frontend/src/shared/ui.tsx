import type { ReactNode } from "react";
import { t } from "../i18n";

/** Small stroke icons (24x24, drawn here: no icon dependency). Decorative: the label is always text next to them. */
const PATHS: Record<string, string> = {
  resumen: "M3 12l9-8 9 8M5 10v10h5v-6h4v6h5V10",
  video: "M4 6h12a2 2 0 012 2v8a2 2 0 01-2 2H4a2 2 0 01-2-2V8a2 2 0 012-2zM18 10l4-2v8l-4-2",
  camara: "M4 8h3l2-3h6l2 3h3a1 1 0 011 1v9a1 1 0 01-1 1H4a1 1 0 01-1-1V9a1 1 0 011-1zM12 17a4 4 0 100-8 4 4 0 000 8z",
  feeds: "M3 4h8v7H3zM13 4h8v7h-8zM3 13h8v7H3zM13 13h8v7h-8z",
  sesiones: "M4 5h16M4 12h16M4 19h16",
  eventos: "M12 3l9 16H3L12 3zM12 10v4M12 17h.01",
  simulaciones: "M9 3h6M10 3v6L4 19a2 2 0 001.7 3h12.6A2 2 0 0020 19l-6-10V3",
  estadisticas: "M4 20V10M10 20V4M16 20v-8M22 20H2",
  desarrollador: "M8 8l-5 4 5 4M16 8l5 4-5 4M14 5l-4 14",
  configuracion: "M12 15a3 3 0 100-6 3 3 0 000 6zM19 12a7 7 0 00-.1-1.2l2-1.6-2-3.4-2.4 1a7 7 0 00-2-1.2L14 3h-4l-.5 2.6a7 7 0 00-2 1.2l-2.4-1-2 3.4 2 1.6A7 7 0 005 12c0 .4 0 .8.1 1.2l-2 1.6 2 3.4 2.4-1a7 7 0 002 1.2L10 21h4l.5-2.6a7 7 0 002-1.2l2.4 1 2-3.4-2-1.6c.1-.4.1-.8.1-1.2z",
  menu: "M3 6h18M3 12h18M3 18h18", expand: "M4 14v6h6M20 10V4h-6M4 20l7-7M20 4l-7 7", close: "M6 6l12 12M18 6L6 18", back: "M15 18l-6-6 6-6",
};
export function Icon({ name, size = 18 }: { name: string; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={PATHS[name] ?? PATHS.resumen} /></svg>;
}

export type Risk = "GREEN" | "YELLOW" | "RED" | "UNKNOWN";
/** A risk state, always as WORDS (never colour alone). UNKNOWN is hatched and never reads as safe. */
export function RiskBadge({ state, testid }: { state: Risk; testid?: string }) {
  return <span className={`badge badge-${state}`} data-testid={testid} data-state={state}>{t(`mg.risk.badge.${state}`)}</span>;
}

export type Provenance = "real" | "synthetic" | "repeated" | "unverified" | "annotated";
const PROV: Record<Provenance, [string, string]> = {
  real: ["real", "mg.prov.real"], synthetic: ["sim", "mg.prov.synthetic"], repeated: ["rep", "mg.prov.repeated"], unverified: ["unv", "mg.prov.unverified"], annotated: ["unv", "mg.prov.annotated"],
};
export function ProvTag({ kind }: { kind: Provenance }) {
  const [cls, id] = PROV[kind];
  return <span className={`tag ${cls}`} data-prov={kind}>{t(id)}</span>;
}

export function Kpi({ label, value, note, tone, testid }: { label: string; value: ReactNode; note?: ReactNode; tone?: "red" | "yellow" | "green"; testid?: string }) {
  return <div className={`kpi ${tone ?? ""}`} data-testid={testid}><span className="kpi-l">{label}</span><span className="kpi-v">{value}</span>{note ? <span className="kpi-n">{note}</span> : null}</div>;
}

export function Panel({ title, actions, children, testid }: { title: ReactNode; actions?: ReactNode; children: ReactNode; testid?: string }) {
  return <section className="card" data-testid={testid}><div className="panel-head"><h3>{title}</h3>{actions}</div>{children}</section>;
}

export function Banner({ kind = "info", children, testid }: { kind?: "info" | "warn" | "err"; children: ReactNode; testid?: string }) {
  return <div className={`banner ${kind}`} role={kind === "err" ? "alert" : "note"} data-testid={testid}>{children}</div>;
}
export const Empty = ({ children, testid }: { children: ReactNode; testid?: string }) => <div className="empty" data-testid={testid}>{children}</div>;
export const Loading = ({ what }: { what: string }) => <p className="loading" role="status">{what}</p>;

export const fmtNum = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? t("mg.notmeasured") : v.toFixed(d));
export const fmtUs = (us: number) => `${(us / 1e6).toFixed(1)} s`;
