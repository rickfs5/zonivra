/// <reference types="vite/client" />
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { catalogs, en, es, hasMsg, sourceLabel, t, tEnum, tf, tIn } from "./i18n";
import { DEFAULT_LOCALE, STORAGE_KEY, applyDocumentLanguage, getLocale, resolveInitialLocale, setLocale } from "./locale";

const sources = import.meta.glob(["./**/*.ts", "./**/*.tsx", "!./**/*.test.ts"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const html = import.meta.glob("../index.html", { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const tokens = (s: string) => [...s.matchAll(/\{[A-Za-z0-9_]+\}|\$\{[^}]*\}|%[sd]/g)].map((m) => m[0]).sort();
const isCatalogFile = (f: string) => /\/i18n(_[a-z_]+)?\.ts$/.test(f) || /\/locale\.ts$/.test(f) || /\/test_setup\.ts$/.test(f);
const stripComments = (s: string) => s.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");

beforeEach(() => setLocale("es", false));
afterEach(() => setLocale("es", false));

describe("locale selection is deterministic", () => {
  it("defaults to English (the hackathon demo language)", () => { expect(DEFAULT_LOCALE).toBe("en"); expect(resolveInitialLocale("", null)).toBe("en"); expect(resolveInitialLocale(undefined, undefined)).toBe("en"); });
  it("?lang= beats the stored choice, which beats the default", () => {
    expect(resolveInitialLocale("?lang=es", "en")).toBe("es");
    expect(resolveInitialLocale("?lang=en", "es")).toBe("en");
    expect(resolveInitialLocale("", "es")).toBe("es");
    expect(resolveInitialLocale("?x=1&lang=es", null)).toBe("es");
  });
  it("ignores unsupported or hostile values", () => {
    for (const bad of ["?lang=fr", "?lang=", "?lang=EN", "?lang=es%00", "?lang=<script>"]) expect(resolveInitialLocale(bad, null)).toBe("en");
    expect(resolveInitialLocale("", "fr")).toBe("en"); expect(resolveInitialLocale("", "")).toBe("en");
  });
  it("setLocale switches t() and persists the choice; a failing storage does not break it", () => {
    const store = new Map<string, string>();
    (globalThis as { localStorage?: unknown }).localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
    try {
      setLocale("en"); expect(getLocale()).toBe("en"); expect(store.get(STORAGE_KEY)).toBe("en"); expect(t("mg.nav.resumen")).toBe("Overview");
      setLocale("es"); expect(store.get(STORAGE_KEY)).toBe("es"); expect(t("mg.nav.resumen")).toBe("Resumen");
      setLocale("en", false); expect(store.get(STORAGE_KEY)).toBe("es");                         // persist=false never writes
      (globalThis as { localStorage?: unknown }).localStorage = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
      expect(() => setLocale("es")).not.toThrow(); expect(getLocale()).toBe("es");
    } finally { delete (globalThis as { localStorage?: unknown }).localStorage; }
  });
  it("an explicit choice is stored even when it equals the active language (e.g. ?lang=en over a stored es)", () => {
    const store = new Map<string, string>([[STORAGE_KEY, "es"]]);
    (globalThis as { localStorage?: unknown }).localStorage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
    try { setLocale("en", false); setLocale("en"); expect(store.get(STORAGE_KEY)).toBe("en"); } finally { delete (globalThis as { localStorage?: unknown }).localStorage; }
  });
  it("an unsupported locale is rejected", () => { setLocale("es", false); setLocale("fr" as never, false); expect(getLocale()).toBe("es"); });
});

describe("document language follows the active locale", () => {
  it("sets <html lang> and the title in English and in Spanish", () => {
    const doc = { documentElement: { lang: "" }, title: "" };
    (globalThis as { document?: unknown }).document = doc;
    try {
      setLocale("en", false); applyDocumentLanguage(getLocale(), t("mg.doc.title")); expect(doc.documentElement.lang).toBe("en"); expect(doc.title).toBe("Zonivra · AI-assisted video review");
      setLocale("es", false); applyDocumentLanguage(getLocale(), t("mg.doc.title")); expect(doc.documentElement.lang).toBe("es"); expect(doc.title).toContain("Revisión de video");
    } finally { delete (globalThis as { document?: unknown }).document; }
  });
  it("is a no-op without a DOM", () => expect(() => applyDocumentLanguage("en", "x")).not.toThrow());
  it("index.html ships with lang=\"en\" and the Zonivra title (the default locale), and App wires the effect", () => {
    const page = Object.values(html)[0]; expect(page).toMatch(/<html lang="en">/); expect(page).toMatch(/<title>Zonivra/);
    const app = Object.entries(sources).find(([f]) => f.endsWith("/App.tsx"))![1];
    expect(app).toMatch(/applyDocumentLanguage\(locale, t\("mg\.doc\.title"\)\)/); expect(app).toMatch(/useLocale\(\)/);
  });
  it("the language switch is rendered, labelled in each language and exposes its state", () => {
    const shell = Object.entries(sources).find(([f]) => f.endsWith("app/Shell.tsx"))![1];
    expect(shell).toMatch(/data-testid=\{`locale-\$\{l\}`\}/); expect(shell).toMatch(/aria-pressed=\{active === l\}/); expect(shell).toMatch(/lang=\{l\}/); expect(shell).toMatch(/Español/); expect(shell).toMatch(/English/);
  });
});

describe("English catalog is complete and genuinely English", () => {
  it("has exactly the Spanish ids (nothing missing, nothing orphaned)", () => {
    const miss = Object.keys(es).filter((k) => !(k in en)), extra = Object.keys(en).filter((k) => !(k in es));
    expect({ miss, extra }).toEqual({ miss: [], extra: [] });
    expect(Object.keys(es).length).toBeGreaterThan(700);
  });
  it("keeps every placeholder and has no empty values", () => {
    const bad: string[] = [];
    for (const k of Object.keys(es)) { if (!en[k]?.trim()) bad.push(`${k}: empty`); else if (JSON.stringify(tokens(es[k])) !== JSON.stringify(tokens(en[k]))) bad.push(`${k}: placeholders differ`); }
    expect(bad).toEqual([]);
  });
  it("contains no Spanish accents or Spanish function words", () => {
    const words = /\b(para|los|las|del|una|con|sin|por|esta|este|que|desde|hasta|cuando|donde|según|pero|también|puede|deben|fuente|fuentes|zona|zonas|sesión|sesiones|eliminar|guardar|cancelar|cerrar|cargando)\b/i;
    const allow = new Set<string>([]);                                                               // add an id here ONLY with a stated reason
    const bad: string[] = [];
    for (const [k, v] of Object.entries(en)) { if (allow.has(k)) continue; if (/[áéíóúñ¿¡]/i.test(v)) bad.push(`${k}: accent`); else if (words.test(v)) bad.push(`${k}: ${words.exec(v)![0]}`); }
    expect(bad).toEqual([]);
  });
  it("English differs from Spanish for every message longer than a short technical token", () => {
    const identicalByNature = new Set(["mg.xc.dash.dev_acc_commit", "mg.xc.alert.notif_title", "mg.xc.alert.notif_title_sim"]);   // "Commit {commit}" and "Zonivra — {sev}[ — {sim}]": brand + placeholders, the same in both languages
    const same = Object.keys(es).filter((k) => es[k] === en[k] && es[k].length > 14 && /[a-záéíóú]{4,}/.test(es[k]) && !identicalByNature.has(k));
    expect(same).toEqual([]);
  });
  it("every literal t()/tf()/tEnum id used by the UI exists in BOTH catalogs", () => {
    const missing: string[] = [];
    for (const [f, text] of Object.entries(sources)) for (const m of text.matchAll(/\b(?:t|tf)\(\s*["'](mg\.[A-Za-z0-9_.]+)["']/g)) for (const [n, c] of Object.entries(catalogs)) if (!(m[1] in c)) missing.push(`${n} ${f}: ${m[1]}`);
    expect(missing).toEqual([]);
  });
  it("ids built from a fixed set exist in both catalogs", () => {
    const need = [...["resumen", "video", "camara", "feeds", "sesiones", "eventos", "simulaciones", "estadisticas", "desarrollador", "configuracion"].map((a) => `mg.nav.${a}`),
      ...["GREEN", "YELLOW", "RED", "UNKNOWN"].map((s) => `mg.risk.badge.${s}`), ...["new", "active", "acknowledged", "resolved"].map((s) => `mg.alert.status.${s}`),
      ...["pending", "delivered", "in_app_only", "failed"].map((s) => `mg.alert.delivery.${s}`), ...["idle", "playing", "paused", "ended", "failed", "stopped"].map((s) => `mg.feed.state.${s}`),
      ...["stored", "displayed", "pending", "disabled", "delivered", "denied", "failed", "unsupported"].map((s) => `mg.inc.ch.${s}`),
      ...["LOCAL_READY", "LOCAL_UNAVAILABLE", "META_DISABLED", "CREDENTIALS_NOT_CONFIGURED", "BILLING_NOT_APPROVED", "LIVE_NOT_VERIFIED", "SIMULATED", "ERROR"].map((s) => `mg.prov.state.${s}`)];
    for (const [n, c] of Object.entries(catalogs)) expect({ n, missing: need.filter((id) => !(id in c)) }).toEqual({ n, missing: [] });
  });
});

describe("no hard-coded Spanish remains in the UI sources", () => {
  it("has no accented/inverted Spanish characters outside the catalogs and comments", () => {
    const hits: string[] = [];
    for (const [f, text] of Object.entries(sources)) { if (isCatalogFile(f)) continue; stripComments(text).split("\n").forEach((l, i) => { if (/[áéíóúñ¿¡ÁÉÍÓÚÑ]/.test(l) && !/^const ENDONYM\b/.test(l.trim())) hits.push(`${f}:${i + 1}: ${l.trim().slice(0, 100)}`); }); }
    expect(hits).toEqual([]);
  });
  it("has no unaccented Spanish UI words in string literals or JSX text", () => {
    const word = /(["'`>]\s*)(Cargando|Eliminar|Guardar|Cancelar|Cerrar|Aceptar|Sesiones|Sesión|Fuentes?|Zonas?|Resumen|Eventos?|Alertas?|Configuración|Iniciar|Detener|Pausar|Reanudar|Sin |Hay |No hay|Error de|Cámara|Simulación|Estadísticas)\b/;
    const hits: string[] = [];
    for (const [f, text] of Object.entries(sources)) { if (isCatalogFile(f)) continue; stripComments(text).split("\n").forEach((l, i) => { if (word.test(l)) hits.push(`${f}:${i + 1}: ${l.trim().slice(0, 100)}`); }); }
    expect(hits).toEqual([]);
  });
});

describe("representative strings, in both languages", () => {
  const rows: [string, string, string][] = [
    ["mg.nav.eventos", "Eventos y alertas", "Events and alerts"], ["mg.nav.configuracion", "Configuración", "Settings"], ["mg.risk.badge.UNKNOWN", "DESCONOCIDO", "UNKNOWN"],
    ["mg.risk.badge.RED", "ROJO", "RED"], ["mg.risk.badge.GREEN", "VERDE", "GREEN"], ["mg.prov.state.META_DISABLED", "META DESHABILITADO", "META DISABLED"],
    ["mg.prov.state.LIVE_NOT_VERIFIED", "EN VIVO SIN VERIFICAR", "LIVE NOT VERIFIED"], ["mg.prov.real", "INFERENCIA REAL (local)", "REAL INFERENCE (local)"],
  ];
  for (const [id, spanish, english] of rows) it(`${id}`, () => { expect(tIn("es", id)).toBe(spanish); expect(tIn("en", id)).toBe(english); });
  it("error ids resolve to readable English, never to the raw id", () => {
    setLocale("en", false);
    for (const id of Object.keys(es).filter((k) => k.startsWith("mg.refusal."))) { expect(t(id)).not.toBe(id); expect(t(id)).toBe(en[id]); }
    expect(t("mg.refusal.zone_name_not_nfc")).toMatch(/NFC/);
  });
  it("tf interpolates and leaves unknown placeholders visible; missing ids show the id", () => {
    setLocale("en", false); expect(tf("mg.zl.unacked", { n: 3 })).toBe("3 unacknowledged"); setLocale("es", false); expect(tf("mg.zl.unacked", { n: 3 })).toBe("3 sin reconocer");
    expect(tf("mg.zl.unacked", {})).toContain("{n}"); expect(t("mg.no.such.id")).toBe("mg.no.such.id"); expect(hasMsg("mg.no.such.id")).toBe(false);
  });
});

describe("canonical values and evidence metadata are never altered by presentation", () => {
  it("tEnum maps display only: unknown values come back exactly as received; empty is a dash", () => {
    setLocale("en", false);
    for (const v of ["person", "vehicle", "other", "RED", "future_value_x", "0123abc…sha256"]) { const out = tEnum("mg.nonexistent.group", v); expect(out).toBe(v); }
    expect(tEnum("mg.nonexistent.group", null)).toBe("—"); expect(tEnum("mg.nonexistent.group", "")).toBe("—");
  });
  it("presentation helpers do not mutate their input", () => {
    const obj = Object.freeze({ cls: Object.freeze({ label: "person" }), state: "RED", digest: "d8d6b9ee57d4d0ed2b1f305163624712a0532cb7bce0c747317984fc5457440d" });
    const before = JSON.stringify(obj);
    for (const l of ["en", "es"] as const) { setLocale(l, false); tEnum("mg.xd.enum.cls", obj.cls.label); tEnum("mg.risk.badge", obj.state); t(`mg.risk.badge.${obj.state}`); }
    expect(JSON.stringify(obj)).toBe(before);
  });
  it("risk state ids keep their canonical vocabulary in both languages (only the label text changes)", () => {
    for (const s of ["GREEN", "YELLOW", "RED", "UNKNOWN"]) { expect(`mg.risk.badge.${s}` in es && `mg.risk.badge.${s}` in en).toBe(true); expect(en[`mg.risk.badge.${s}`]).toBe(s); }
    const ui = Object.entries(sources).find(([f]) => f.endsWith("shared/ui.tsx"))![1];
    expect(ui).toMatch(/data-state=\{state\}/); expect(ui).toMatch(/export type Risk = "GREEN" \| "YELLOW" \| "RED" \| "UNKNOWN"/);
  });
  it("the API layer sends and compares canonical values: it does not translate request payloads", () => {
    const api = Object.entries(sources).find(([f]) => f.endsWith("/api.ts"))![1];
    expect(api).not.toMatch(/JSON\.stringify\([^)]*\bt\(/); expect(api).not.toMatch(/body:[^\n]*\bt(f|Enum)?\(/);
  });
});

describe("provider screen tells the truth in both languages", () => {
  const claim = (k: string, l: "en" | "es") => tIn(l, `mg.zp.claims.${k}`);
  it("discloses the operational local engine, the disabled/unverified in-app Meta integration, simulated Muse and the frozen transport", () => {
    expect(claim("rfdetr", "en")).toMatch(/operational local inference/); expect(claim("rfdetr", "en")).toMatch(/not been validated/);
    expect(claim("sam", "en")).toMatch(/external test/); expect(claim("sam", "en")).toMatch(/disabled and unverified/); expect(claim("sam", "en")).toMatch(/no request is sent/);
    expect(claim("muse", "en")).toMatch(/simulated/); expect(claim("muse", "en")).toMatch(/do not demonstrate in-app integration/);
    expect(claim("transport", "en")).toMatch(/frozen/); expect(claim("transport", "en")).toMatch(/no active Meta client/);
    expect(claim("risk", "en")).toMatch(/not a certified safety system/);
    expect(claim("sam", "es")).toMatch(/deshabilitada y sin verificar/); expect(claim("muse", "es")).toMatch(/simuladas/); expect(claim("transport", "es")).toMatch(/congelado/);
  });
  it("the panel renders every claim", () => {
    const p = Object.entries(sources).find(([f]) => f.endsWith("providers/ProvidersPanel.tsx"))![1];
    expect(p).toMatch(/\["rfdetr", "sam", "muse", "transport", "risk"\]/); expect(p).toMatch(/providers-claim-\$\{k\}/);
  });
  it("no English or Spanish text claims a live, connected or verified Meta integration", () => {
    const risky = /(?<!not |un|no |sin |NO )\b(connected|conectad[oa]s?|live[- ]verified|verified live|verificad[oa] en vivo|integrated with meta|integración activa)\b/i;
    const bad = Object.entries({ ...en, ...es }).filter(([, v]) => /meta|sam 3\.1|muse/i.test(v) && risky.test(v)).map(([k, v]) => `${k}: ${v}`);
    expect(bad).toEqual([]);
  });
  it("provider state labels keep their NOT-verified qualifiers in English", () => {
    expect(en["mg.prov.state.LIVE_NOT_VERIFIED"]).toMatch(/NOT VERIFIED/); expect(en["mg.prov.state.BILLING_NOT_APPROVED"]).toMatch(/NOT APPROVED/); expect(en["mg.prov.state.CREDENTIALS_NOT_CONFIGURED"]).toMatch(/NO CREDENTIALS|NOT CONFIGURED/);
    expect(en["mg.prov.state.SIMULATED"]).toMatch(/SIMULATED/);
  });
  it("Spanish provider wording is unchanged by the localization", () => {
    expect(es["mg.prov.note.meta"]).toBe("Adaptador preparado solo en el laboratorio experimental; ejecución real bloqueada.");
    expect(es["mg.prov.note.muse"]).toBe("La explicación es simulada con un transporte falso determinista; no hay llamada a ningún modelo.");
  });
});

describe("EN-LOCALE-02 brand: the commercial name is Zonivra in both locales", () => {
  it("brand, document title, UI title and notification titles start with Zonivra in English and Spanish", () => {
    for (const [name, c] of [["en", en], ["es", es]] as const) {
      for (const id of ["mg.brand", "mg.doc.title", "mg.ui.title", "mg.xc.alert.notif_title", "mg.xc.alert.notif_title_sim"]) expect(c[id], `${name}:${id}`).toMatch(/^Zonivra/);
    }
  });
  it("no user-visible catalog string in either locale still names the predecessor product", () => {
    for (const [name, c] of [["en", en], ["es", es]] as const) for (const [id, v] of Object.entries(c)) expect(v, `${name}:${id}`).not.toMatch(/MineGuard/i);
  });
});

describe("EN-LOCALE-02 backend drill label: presentation-only mapping of the fixed backend template", () => {
  it("shows the scripted-drill source label in the active language and leaves every other label exactly as stored", () => {
    setLocale("en", false); expect(sourceLabel("Simulación ROJO")).toBe("RED simulation"); expect(sourceLabel("Fuente 1")).toBe("Fuente 1"); expect(sourceLabel("pexels_6565442.mp4")).toBe("pexels_6565442.mp4");
    setLocale("es", false); expect(sourceLabel("Simulación ROJO")).toBe("Simulación ROJO");
  });
  it("every place that renders a backend source label goes through sourceLabel", () => {
    for (const [f, s] of Object.entries(sources)) { if (/\.test\.|i18n/.test(f)) continue; expect(s, f).not.toMatch(/\{(a\.source\.label|inc\.identity\.source_label)\}/); }
  });
});
