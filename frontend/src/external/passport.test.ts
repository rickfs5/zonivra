import { describe, expect, it } from "vitest";
import { passportOf } from "./passport";

const obj = (score: number | null) => ({ object_id: 1, box: { x1: 0, y1: 0, x2: 1, y2: 1 }, provider_score_permille: score, mask: { rows: 1, cols: 1, encoding: "x" } });
const rec = (state: string, objects: unknown[]) => ({ verification: { state }, objects }) as unknown as Parameters<typeof passportOf>[0];

describe("passportOf: can only say less than the record proves", () => {
  it("authenticity, classes, masks, execution and risk statements are constants", () => {
    for (const st of ["SYNTHETIC_FIXTURE", "HASHES_VERIFIED_AUTHENTICITY_NOT_PROVEN", "ANYTHING"]) {
      const p = passportOf(rec(st, [obj(910)]));
      expect(p.authenticity).toBe("not_proven"); expect(p.classes).toBe("unclassified"); expect(p.masks).toBe("structure_checked_not_rendered");
      expect(p.execution).toBe("manual_external_not_in_app"); expect(p.riskAssessed).toBe(false); expect(p.origin).toBe("EXTERNAL_MANUAL_PROVIDER_RESULT");
    }
  });
  it("confidence is 'not reported' unless a provider score exists, and then 'uncalibrated'", () => {
    expect(passportOf(rec("S", [obj(null), obj(null)])).confidence).toBe("not_reported");
    expect(passportOf(rec("S", [])).confidence).toBe("not_reported");
    expect(passportOf(rec("S", [obj(null), obj(500)])).confidence).toBe("provider_score_uncalibrated");
  });
  it("synthetic only for the labelled fixture state; object count is passed through", () => {
    expect(passportOf(rec("SYNTHETIC_FIXTURE", [])).synthetic).toBe(true);
    expect(passportOf(rec("HASHES_VERIFIED_AUTHENTICITY_NOT_PROVEN", [obj(1)])).synthetic).toBe(false);
    expect(passportOf(rec("S", [obj(1), obj(2), obj(3)])).objects).toBe(3);
  });
});
