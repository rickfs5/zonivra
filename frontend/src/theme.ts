import { useSyncExternalStore } from "react";

/** NS20 theme state. Three user preferences (light / dark / system); the RESOLVED theme (light | dark) is what gets applied to <html data-theme>. Pure functions are exported for tests; the module
 *  touches only the DOM attribute, localStorage and matchMedia: no network, no backend, no business state. A corrupt or unknown stored value falls back to "system". */
export type ThemePref = "light" | "dark" | "system";
export type ThemeResolved = "light" | "dark";
export const THEMES: readonly ThemePref[] = ["light", "dark", "system"];
export const THEME_KEY = "zonivra.theme";

export const isThemePref = (v: unknown): v is ThemePref => v === "light" || v === "dark" || v === "system";
/** Invalid / missing / non-string -> "system" (fail safe, never throws). */
export const parseThemePref = (v: unknown): ThemePref => (isThemePref(v) ? v : "system");
export const resolveTheme = (pref: ThemePref, systemDark: boolean): ThemeResolved => (pref === "system" ? (systemDark ? "dark" : "light") : pref);

const mq = (): MediaQueryList | null => { try { return typeof matchMedia === "function" ? matchMedia("(prefers-color-scheme: dark)") : null; } catch { return null; } };
const systemDark = (): boolean => mq()?.matches ?? true;                       // no matchMedia (old/blocked): dark, the product default
function readStored(): string | null { try { return globalThis.localStorage?.getItem(THEME_KEY) ?? null; } catch { return null; } }

let pref: ThemePref = parseThemePref(readStored());
const subs = new Set<() => void>();

/** Write the resolved theme to the document. data-theme-pref keeps the user's choice (for tests and styling hooks). */
export function applyTheme(p: ThemePref = pref): ThemeResolved {
  const r = resolveTheme(p, systemDark());
  if (typeof document !== "undefined") {
    const el = document.documentElement;
    el.setAttribute("data-theme", r); el.setAttribute("data-theme-pref", p); el.style.colorScheme = r;
  }
  return r;
}

export const getThemePref = (): ThemePref => pref;
export const getResolvedTheme = (): ThemeResolved => resolveTheme(pref, systemDark());
export const subscribeTheme = (f: () => void): (() => void) => { subs.add(f); return () => { subs.delete(f); }; };

export function setThemePref(next: ThemePref, persist = true): void {
  if (!isThemePref(next)) return;
  if (persist) { try { globalThis.localStorage?.setItem(THEME_KEY, next); } catch { /* storage unavailable: the choice lasts for this page only */ } }
  const changed = next !== pref;
  pref = next; applyTheme(pref);
  if (changed) subs.forEach((f) => f());
}

let started = false;
/** Called once from main.tsx: applies the stored preference and follows OS changes while the preference is "system". Idempotent. */
export function initTheme(): void {
  applyTheme(pref);
  if (started) return;
  started = true;
  const m = mq();
  const onChange = () => { if (pref === "system") { applyTheme(pref); subs.forEach((f) => f()); } };
  try { m?.addEventListener("change", onChange); } catch { try { m?.addListener(onChange); } catch { /* no OS follow */ } }
}

/** Re-render on preference changes. The snapshot is the string preference (stable between renders). */
export const useThemePref = (): ThemePref => useSyncExternalStore(subscribeTheme, getThemePref, getThemePref);
/** Test hook: re-read storage (the module reads it once at load). */
export function __reloadThemeFromStorage(): ThemePref { pref = parseThemePref(readStored()); applyTheme(pref); return pref; }
