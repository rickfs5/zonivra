/// <reference types="vite/client" />
import { describe, expect, it } from "vitest";
import { es } from "./i18n";

const sources = import.meta.glob(["./**/*.ts", "./**/*.tsx", "!./**/*.test.ts"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;

/** Every literal message id passed to t("…") anywhere in the UI exists in the Spanish catalog (a missing id would show the raw id to the operator). */
describe("i18n catalog coverage", () => {
  it("has every literal id used by t()", () => {
    expect(Object.keys(sources).length).toBeGreaterThan(15);   // the glob really found the UI sources (a vacuous pass would hide missing ids)
    const missing: string[] = [];
    for (const [f, text] of Object.entries(sources)) for (const m of text.matchAll(/\bt\(\s*["'](mg\.[A-Za-z0-9_.]+)["']\s*\)/g)) if (!(m[1] in es)) missing.push(`${f}: ${m[1]}`);
    expect(missing).toEqual([]);
  });
  it("has the ids built from a fixed set (areas, risk states, alert statuses, channels)", () => {
    const need = [...["resumen", "video", "camara", "feeds", "sesiones", "eventos", "simulaciones", "estadisticas", "desarrollador", "configuracion"].map((a) => `mg.nav.${a}`),
      ...["GREEN", "YELLOW", "RED", "UNKNOWN"].map((s) => `mg.risk.badge.${s}`), ...["new", "active", "acknowledged", "resolved"].map((s) => `mg.alert.status.${s}`),
      ...["pending", "delivered", "in_app_only", "failed"].map((s) => `mg.alert.delivery.${s}`), ...["idle", "playing", "paused", "ended", "failed", "stopped"].map((s) => `mg.feed.state.${s}`),
      ...["stored", "displayed", "pending", "disabled", "delivered", "denied", "failed", "unsupported"].map((s) => `mg.inc.ch.${s}`)];
    expect(need.filter((id) => !(id in es))).toEqual([]);
  });
});
