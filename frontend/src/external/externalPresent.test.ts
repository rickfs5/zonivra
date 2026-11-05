/// <reference types="vite/client" />
import { afterEach, describe, expect, it } from "vitest";
import { catalogs, en, es, t, tIn } from "../i18n";
import { setLocale } from "../locale";
import { bannersFor, boxRect, bytesToBase64, integrityMsgId, linkageMsgId, refusalMsgId, scorePercent, stateMsgId } from "./externalPresent";

const raw = import.meta.glob(["./ExternalResultsPanel.tsx", "../../../backend/mineguard/meta_import.py"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const panel = Object.entries(raw).find(([f]) => f.endsWith(".tsx"))![1];
const backend = Object.entries(raw).find(([f]) => f.endsWith(".py"))![1];
afterEach(() => setLocale("es", false));

const ids = (c: Record<string, string>) => Object.keys(c).filter((k) => k.startsWith("mg.ext.") || k === "mg.refusal.unknown_external_result").sort();

describe("external-result catalogs (English + Spanish)", () => {
  it("both languages carry exactly the same ids", () => { expect(ids(en)).toEqual(ids(es)); expect(ids(en).length).toBeGreaterThan(80); });
  it("every refusal code of the backend has a message in BOTH languages", () => {
    const block = /REFUSALS = \(([\s\S]*?)\n\)/.exec(backend)![1];
    const codes = [...block.matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
    expect(codes.length).toBeGreaterThan(25);
    for (const c of codes) for (const [n, cat] of Object.entries(catalogs)) expect({ n, c, has: `mg.ext.refusal.${c}` in cat }).toEqual({ n, c, has: true });
  });
  it("the external origin is announced in English and in Spanish", () => {
    expect(tIn("en", "mg.ext.banner.external")).toMatch(/EXTERNAL MANUAL PROVIDER RESULT.*not live in-app inference/);
    expect(tIn("es", "mg.ext.banner.external")).toMatch(/RESULTADO MANUAL EXTERNO DE PROVEEDOR.*ni es inferencia en vivo/);
    expect(tIn("en", "mg.ext.banner.synthetic")).toMatch(/SYNTHETIC FIXTURE.*NOT produced by any Meta model/);
    expect(tIn("es", "mg.ext.banner.synthetic")).toMatch(/FIXTURE SINTÉTICO.*NO lo produjo ningún modelo de Meta/);
  });
  it("no message gives an imported result a risk level, a class, or a live-verified claim", () => {
    for (const [n, cat] of [["en", en], ["es", es]] as const) for (const id of ids(cat)) {
      const v = cat[id];
      expect({ n, id, risk: /\b(RED|YELLOW|GREEN|ROJO|AMARILLO|VERDE)\b/.test(v) }).toEqual({ n, id, risk: false });
      expect({ n, id, live: /LIVE[ _]VERIFIED|IN[ _]APP[ _]INFERENCE[ _]VERIFIED|verified live|verificad[oa] en vivo/i.test(v) }).toEqual({ n, id, live: false });
    }
  });
  it("English reads as English for the key review labels", () => {
    setLocale("en", false);
    expect(t("mg.ext.f.input")).toMatch(/Input image digest/); expect(t("mg.ext.f.response")).toMatch(/Response digest/); expect(t(stateMsgId("SYNTHETIC_FIXTURE"))).toBe("SYNTHETIC FIXTURE");
    expect(t(stateMsgId("HASHES_VERIFIED_AUTHENTICITY_NOT_PROVEN"))).toMatch(/AUTHENTICITY NOT PROVEN/);
    setLocale("es", false);
    expect(t("mg.ext.f.input")).toMatch(/Huella de la imagen de entrada/); expect(t(stateMsgId("SYNTHETIC_FIXTURE"))).toBe("FIXTURE SINTÉTICO");
  });
});

describe("presentation rules", () => {
  it("the external and no-risk banners are unconditional; the state adds synthetic or unproven", () => {
    expect(bannersFor({ verification: { state: "SYNTHETIC_FIXTURE" } as never })).toEqual(["external", "synthetic", "no_risk"]);
    expect(bannersFor({ verification: { state: "HASHES_VERIFIED_AUTHENTICITY_NOT_PROVEN" } as never })).toEqual(["external", "unproven", "no_risk"]);
    expect(bannersFor({})).toEqual(["external", "no_risk"]);
  });
  it("unknown states and values never render as a raw string or as a verified state", () => {
    expect(stateMsgId("LIVE_VERIFIED")).toBe("mg.ext.refusal.generic"); expect(stateMsgId(undefined)).toBe("mg.ext.refusal.generic");
    expect(integrityMsgId("OK")).toBe("mg.ext.refusal.generic"); expect(linkageMsgId("x")).toBe("mg.ext.refusal.generic");
  });
  it("API error text maps to a known refusal id and never echoes the raw text", () => {
    expect(refusalMsgId('Error: 422 {"msg_id":"mg.ext.refusal.result_hash_mismatch","code":"result_hash_mismatch"}')).toBe("mg.ext.refusal.result_hash_mismatch");
    expect(refusalMsgId('Error: 422 {"msg_id":"mg.ext.refusal.nonexistent"}')).toBe("mg.ext.refusal.generic");
    expect(refusalMsgId('Error: 500 {"msg_id":"evil.<script>"}')).toBe("mg.ext.refusal.generic"); expect(refusalMsgId("boom")).toBe("mg.ext.refusal.generic");
  });
  it("a box is drawn only if it verifies against the declared image; it is never clipped", () => {
    expect(boxRect({ x1: 40, y1: 90, x2: 230, y2: 200 }, 320, 240)).toEqual({ x: 40, y: 90, width: 190, height: 110 });
    for (const b of [{ x1: -1, y1: 0, x2: 5, y2: 5 }, { x1: 0, y1: 0, x2: 321, y2: 5 }, { x1: 5, y1: 5, x2: 5, y2: 9 }, { x1: 9, y1: 9, x2: 5, y2: 12 }, { x1: 0.5, y1: 0, x2: 5, y2: 5 }]) expect(boxRect(b, 320, 240)).toBeNull();
  });
  it("scores are shown as an uncalibrated percentage only when the provider reported one", () => { expect(scorePercent(910)).toBe("91.0%"); expect(scorePercent(null)).toBeNull(); });
  it("base64 helper round-trips arbitrary bytes", () => {
    const b = new Uint8Array(70000).map((_, i) => (i * 31) % 256);
    const back = Uint8Array.from(atob(bytesToBase64(b)), (c) => c.charCodeAt(0));
    expect(back.length).toBe(b.length); expect(back.every((x, i) => x === b[i])).toBe(true);
  });
});

describe("the review panel source", () => {
  it("never renders a risk badge, a severity, a class label from the provider, or a provider call", () => {
    expect(panel).not.toMatch(/mg\.risk\.|RiskPanel|severity|risk_badge|\.cls\b|label_state|detector_label/);
    expect(panel).not.toMatch(/https?:\/\/|api\.meta\.ai|fetch\(|XMLHttpRequest|localStorage/);
    expect(panel).toMatch(/EXTERNAL_MANUAL_PROVIDER_RESULT/); expect(panel).toMatch(/external-banner-/);
    for (const tid of ["external-input-digest", "external-response-digest", "external-state", "external-provider", "external-model"]) expect(panel).toContain(tid);
  });
  it("boxes use a neutral colour, never a risk colour", () => { expect(panel).not.toMatch(/#(ff0000|f00|ef4444|dc2626|eab308|22c55e|16a34a)/i); });
});
