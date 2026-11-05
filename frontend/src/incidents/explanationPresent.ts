import { en, es } from "../i18n";
import { getLocale } from "../locale";

/** English view of the backend's Spanish explanation sentences (presentation only; the stored/validated text is never changed). The backend emits a closed set of exact templates; a sentence that matches
 *  none of them is returned AS RECEIVED, so the screen never shows a guessed translation. In Spanish every text is returned untouched. */
const VAR = /\\\{([A-Za-z0-9_]+)\\\}/g;
const cache = new Map<string, RegExp>();
const rx = (template: string): RegExp => {
  let r = cache.get(template);
  if (!r) { r = new RegExp("^" + template.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(VAR, "(.+?)") + "$"); cache.set(template, r); }
  return r;
};
const names = (template: string): string[] => [...template.matchAll(/\{([A-Za-z0-9_]+)\}/g)].map((m) => m[1]);
const NESTED: Record<string, string> = { origin: "origin", causes: "cause" };

export function presentBackendText(text: string, family: "summary" | "decision" | "fact" | "uncertainty" | "step" | "rule" | "origin" | "cause"): string {
  if (getLocale() !== "en") return text;
  const prefix = `mg.xe.${family}.`;
  for (const id of Object.keys(es)) {
    if (!id.startsWith(prefix)) continue;
    const m = rx(es[id]).exec(text);
    if (!m) continue;
    const vars = Object.fromEntries(names(es[id]).map((n, i) => [n, NESTED[n] ? presentBackendText(m[i + 1], NESTED[n] as "origin" | "cause") : m[i + 1]]));
    return en[id].replace(/\{([A-Za-z0-9_]+)\}/g, (all, k: string) => (k in vars ? vars[k] : all));
  }
  return text;
}
