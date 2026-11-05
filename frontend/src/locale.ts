import { useSyncExternalStore } from "react";

export type Locale = "en" | "es";
export const LOCALES: readonly Locale[] = ["en", "es"];
/** The hackathon demo language. Spanish stays fully available through the switcher, `?lang=es` or a stored choice. */
export const DEFAULT_LOCALE: Locale = "en";
export const STORAGE_KEY = "zonivra.locale";

const isLocale = (v: unknown): v is Locale => v === "en" || v === "es";

/** Deterministic order: 1) `?lang=en|es` (reproducible recordings, never persisted), 2) the choice stored by the switcher, 3) DEFAULT_LOCALE. Storage failures (private mode, blocked) are ignored. */
export function resolveInitialLocale(search?: string, stored?: string | null): Locale {
  const q = new URLSearchParams(search ?? "").get("lang");
  if (isLocale(q)) return q;
  return isLocale(stored) ? stored : DEFAULT_LOCALE;
}

function readStored(): string | null { try { return globalThis.localStorage?.getItem(STORAGE_KEY) ?? null; } catch { return null; } }

let current: Locale = resolveInitialLocale(typeof location === "undefined" ? "" : location.search, readStored());
const subs = new Set<() => void>();

/** Keeps the document in step with the active language: <html lang> (screen readers, hyphenation, spellcheck) and the tab title. */
export function applyDocumentLanguage(locale: Locale = current, title?: string): void {
  if (typeof document === "undefined") return;
  document.documentElement.lang = locale;
  if (title) document.title = title;
}

export const getLocale = (): Locale => current;
export const subscribeLocale = (f: () => void): (() => void) => { subs.add(f); return () => { subs.delete(f); }; };

/** Switch the language. `persist=false` is for tests and the URL override. Subscribers (the React tree) re-render; component state (e.g. a running analysis) is not reset. */
export function setLocale(next: Locale, persist = true): void {
  if (!isLocale(next)) return;
  if (persist) { try { globalThis.localStorage?.setItem(STORAGE_KEY, next); } catch { /* storage unavailable: the choice lasts for this page only */ } }   // an explicit choice is stored even when it equals the active (e.g. ?lang) language
  if (next === current) return;
  current = next;
  subs.forEach((f) => f());
}

export const useLocale = (): Locale => useSyncExternalStore(subscribeLocale, getLocale, getLocale);
