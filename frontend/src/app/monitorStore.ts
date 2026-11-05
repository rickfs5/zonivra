import { useSyncExternalStore } from "react";
import { apiV2, type OperatorOverview } from "../api";
import { refusalText } from "../ZoneEditor";

/** ONE shared poller of the operator overview for the whole page (sidebar, top bar and Overview read the same snapshot). `at` is when the snapshot was fetched; a failed refresh keeps the last snapshot and sets `error`. */
const s = { data: null as OperatorOverview | null, error: null as string | null, at: null as number | null, version: 0, listeners: new Set<() => void>(), timer: 0 };
const emit = () => { s.version++; s.listeners.forEach((l) => l()); };
async function tick() {
  try { s.data = await apiV2.overviewMonitor(60, 5); s.error = null; s.at = Date.now(); }
  catch (e) { s.error = refusalText(e); }
  emit();
}
function subscribe(l: () => void) {
  s.listeners.add(l);
  if (s.listeners.size === 1) { void tick(); s.timer = window.setInterval(() => void tick(), 2500); }
  return () => { s.listeners.delete(l); if (!s.listeners.size) window.clearInterval(s.timer); };
}
export function useMonitor() {
  useSyncExternalStore(subscribe, () => s.version);
  return { data: s.data, error: s.error, at: s.at, stale: s.error !== null && s.data !== null, refresh: tick };
}
