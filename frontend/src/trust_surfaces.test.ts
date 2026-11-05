/// <reference types="vite/client" />
import { describe, expect, it } from "vitest";

const sources = import.meta.glob(["./**/*.ts", "./**/*.tsx", "!./**/*.test.ts"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;
const src = (name: string): string => {
  const hit = Object.entries(sources).find(([f]) => f.endsWith(name));
  expect(hit, `source ${name} found`).toBeDefined();
  return hit![1];
};

/** MG-TRUST-026: every operator-facing surface that renders an ALERT must classify its trust through the ONE canonical function (restored-from-disk is never «real»). A surface that renders alerts
 *  without it (as the operator-panel timeline once did) fails here, before any browser is needed. */
describe("alert trust is classified canonically on every surface", () => {
  const surfaces: [string, RegExp][] = [
    ["Dashboards.tsx", /op-timeline-item[\s\S]{0,400}alertProvenance/],
    ["Alerts.tsx", /alert-origin[\s\S]{0,200}alertProvenance|alertProvenance[\s\S]{0,200}alert-origin/],
    ["incidents/IncidentDrawer.tsx", /alertProvenance\(a\)/],
    ["overview/Overview.tsx", /incidentProvenance\(e\)|alertProvenance/],
  ];
  for (const [file, rx] of surfaces) it(`${file} uses the canonical classification`, () => expect(src(file)).toMatch(rx));
  it("no surface re-derives «real» from the simulation flag alone", () => {
    for (const [f, text] of Object.entries(sources)) {
      if (f.endsWith("lib.ts")) continue;
      expect(text, f).not.toMatch(/loaded_from_store\s*\?\s*["']real["']/);
    }
  });
  it("the operator timeline shows the restored qualification", () => expect(src("Dashboards.tsx")).toMatch(/loaded_from_store[\s\S]{0,120}restored_short/));
});
