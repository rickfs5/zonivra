import { hasMsg } from "../i18n";
import { describe, expect, it } from "vitest";
import { en, es } from "../i18n";
import { inputId, isSynthetic, reviewPending, seconds, stateStrip, descId, titleId, widthPct, type Scenario, type ScnSample } from "./scenarioPresent";

const S = (t: number, state: ScnSample["state"], input = "empty"): ScnSample => ({ t_ms: t, state, input });
const IDS = ["case01_haul_truck_pedestrian", "case02a_forklift_as_vehicle", "case02b_forklift_not_recognised", "case03a_excavator_alone", "case03b_excavator_and_person", "control_source_silent"];
const CAV = ["vehicle_class", "image_space", "human_review", "forklift_class", "forklift_missed", "equipment_alone", "excavator_class", "silence_is_unknown"];

describe("scenario presentation (FINAL-ENGINEERING-15-R1)", () => {
  it("merges consecutive samples of one state and never paints a silent gap as a neighbouring state", () => {
    const runs = stateStrip([S(0, "GREEN"), S(200, "GREEN"), S(400, "YELLOW"), S(1000, "GREEN")], 200, 2000);
    expect(runs).toEqual([{ state: "GREEN", from_ms: 0, to_ms: 400 }, { state: "YELLOW", from_ms: 400, to_ms: 600 }, { state: "NO_SAMPLE", from_ms: 600, to_ms: 1000 },
      { state: "GREEN", from_ms: 1000, to_ms: 1200 }, { state: "NO_SAMPLE", from_ms: 1200, to_ms: 2000 }]);
    expect(stateStrip([], 200, 1000)).toEqual([{ state: "NO_SAMPLE", from_ms: 0, to_ms: 1000 }]);
    expect(runs.reduce((a, r) => a + widthPct(r, 2000), 0)).toBeCloseTo(100, 5);
  });
  it("formats and labels", () => {
    expect(seconds(3800)).toBe("3.8 s");
    expect(titleId("case01_haul_truck_pedestrian")).toBe("mg.scn.id.case01_haul_truck_pedestrian");
    expect(inputId("both")).toBe("mg.scn.input.both");
  });
  it("synthetic and review predicates are strict", () => {
    const base = { provenance: { kind: "SYNTHETIC_SCENARIO_REPLAY", real_footage: false, model_inference: false }, review: { status: "PENDING_HUMAN_REVIEW", reviewed_by: null } } as unknown as Scenario;
    expect(isSynthetic(base)).toBe(true); expect(reviewPending(base)).toBe(true);
    expect(isSynthetic({ ...base, provenance: { ...base.provenance, model_inference: true } })).toBe(false);
    expect(isSynthetic({ ...base, provenance: { ...base.provenance, real_footage: true } })).toBe(false);
    expect(isSynthetic({ ...base, provenance: { ...base.provenance, kind: "MODEL_INFERENCE" } })).toBe(false);
    expect(reviewPending({ ...base, review: { status: "REVIEWED", reviewed_by: "x", note: "" } })).toBe(false);
  });
  it("every scenario title, input label, caveat and banner exists in English and Spanish and says synthetic", () => {
    const ids = [...IDS.map(titleId), ...["empty", "person_in", "person_out", "both", "vehicle_only", "gap"].map(inputId), ...CAV.map((c) => `mg.scn.cav.${c}`),
      "mg.scn.title", "mg.scn.banner", "mg.scn.review.pending", "mg.refusal.scenario_unknown", "mg.scn.f.provenance", "mg.scn.f.zone", "mg.scn.f.events", "mg.scn.f.transitions", "mg.scn.f.caveats"];
    for (const id of ids) { expect(en[id], `en ${id}`).toBeTruthy(); expect(es[id], `es ${id}`).toBeTruthy(); }
    expect(en["mg.scn.banner"]).toMatch(/synthetic/i); expect(en["mg.scn.banner"]).toMatch(/not (real )?footage/i); expect(en["mg.scn.banner"]).toMatch(/not RF-DETR/i);
    expect(es["mg.scn.banner"]).toMatch(/sint[eé]tic/i);
    expect(en["mg.scn.review.pending"]).toMatch(/pending human review/i);
    expect(en["mg.scn.cav.equipment_alone"]).toMatch(/green/i);
    expect(en["mg.scn.cav.forklift_class"]).toMatch(/no forklift class/i);
  });
});

describe("scenario card copy (UI-REVAMP-22)", () => {
  it("every known scenario has a title and a short description in the active catalog and the description id is stable", () => {
    const ids = ["case01_haul_truck_pedestrian", "case02a_forklift_as_vehicle", "case02b_forklift_not_recognised", "case03a_excavator_alone", "case03b_excavator_and_person", "control_source_silent"];
    for (const id of ids) { expect(hasMsg(titleId(id))).toBe(true); expect(hasMsg(descId(id))).toBe(true); }
    expect(descId("x")).toBe("mg.scn.desc.x");
  });
});
