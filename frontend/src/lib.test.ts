import { describe, expect, it } from "vitest";
import { es, t } from "./i18n";
import { overlapPhaseId, alertProvenance, nextLabel, oafAcceptanceView, boxStyle, classColor, containRect, formatMs, frameForTimeUs, isStrictlyIncreasing, isTrusted, lastAnalyzedAtOrBefore, originLabelId, scaleBox, summarize, trustNoticeIds, trustState, captureDims, clientToFrame, closedReasonId, evaluationTotalUs, intervalAt, mayCapture, polygonProblem, riskLabelId, timelineSegments, worstState, type Interval } from "./lib";

describe("timestamp mapping", () => {
  const pts = [0, 33367, 66733, 100100]; // 29.97 fps in microseconds
  it("maps time to the frame whose half-open interval covers it", () => {
    expect([0, 33366, 33367, 66732, 66733, 1e9].map((x) => frameForTimeUs(pts, x))).toEqual([0, 0, 1, 1, 2, 3]);
  });
  it("clamps before the first frame", () => expect(frameForTimeUs(pts, -5)).toBe(0));
  it("formats mm:ss.mmm with floor to milliseconds", () => {
    expect(formatMs(13_333_333)).toBe("00:13.333");
    expect(formatMs(61_999_999)).toBe("01:01.999");
  });
});

describe("analyzed-frame lookup", () => {
  const a = [0, 3, 6, 9];
  it("returns the last analyzed frame at or before the index", () => {
    expect([0, 1, 3, 5, 9, 100].map((i) => lastAnalyzedAtOrBefore(a, i))).toEqual([0, 0, 1, 1, 3, 3]);
    expect(lastAnalyzedAtOrBefore([5], 2)).toBe(-1);
    expect(lastAnalyzedAtOrBefore([], 2)).toBe(-1);
  });
});

describe("overlay geometry", () => {
  it("scales analysis-space boxes into the displayed video rectangle", () => {
    const r = containRect(1000, 800, 1280, 720); // letterboxed vertically
    expect(r.w).toBe(1000);
    expect(r.h).toBeCloseTo(562.5);
    expect(r.y).toBeCloseTo(118.75);
    expect(scaleBox({ x: 640, y: 360, w: 128, h: 72 }, 1280, 720, r.w, r.h)).toEqual({ x: 500, y: 281.25, w: 100, h: 56.25 });
  });
});

describe("summary and catalog", () => {
  it("counts detections above the display threshold only", () => {
    const o = (l: string, p: number) => ({ obj_key: "o", cls: { label: "vehicle" as const, detector_label: l, detector_class_id: 8 }, bbox: { x: 0, y: 0, w: 1, h: 1 }, truncated: { left: false, right: false, top: false, bottom: false }, confidence: { permille: p, kind: "model_uncalibrated" } });
    expect(summarize([{ frame_index: 0, pixel_sha256: "", objects: [o("truck", 900), o("dog", 300)] }], 500)).toEqual({ truck: 1 });
  });
  it("has Spanish text for every class and limitation id", () => {
    for (const id of ["mg.cls.person", "mg.cls.vehicle", "mg.cls.other", "mg.lim.image_space_only", "mg.lim.uncalibrated_confidence", "mg.lim.coco_no_excavator_class", "mg.lim.not_safety_system"]) {
      expect(es[id], id).toBeTruthy();
    }
    expect(t("mg.unknown.id")).toBe("mg.unknown.id");
    expect(classColor("person")).not.toBe(classColor("vehicle"));
  });
});

describe("observation origin labels (MG-ORIGIN-001)", () => {
  it("labels each origin distinctly and never mislabels it", () => {
    const m = originLabelId("model_inference", "verified"), s = originLabelId("synthetic_scripted"), h = originLabelId("human_annotated");
    expect(new Set([m, s, h]).size).toBe(3);
    expect(t(m)).toContain("Inferencia experimental real");
    expect(t(s)).toContain("Reproducción sintética");
    expect(t(h)).toContain("Anotación humana");
  });
  it("human annotation is not shown as synthetic replay or as model inference", () => {
    const text = t(originLabelId("human_annotated"));
    expect(text).not.toContain("sintética");
    expect(text).not.toContain("Inferencia experimental real");
    expect(t(originLabelId("synthetic_scripted"))).not.toContain("Anotación humana");
    expect(t(originLabelId("model_inference", "verified"))).not.toContain("sintética");
  });
  it("an unknown origin is never presented as inference", () => {
    expect(originLabelId("something_else")).toBe("mg.origin.unknown");
    expect(t("mg.origin.unknown")).not.toContain("Inferencia experimental real");
  });
  it("every origin message id exists in the Spanish catalog", () => {
    for (const o of ["model_inference", "synthetic_scripted", "human_annotated", "x"]) expect(es[originLabelId(o)]).toBeTruthy();
    expect(es[originLabelId("model_inference", "unverified")]).toBeTruthy();
    expect(es["mg.banner.human_annotated"]).toContain("anotadas por una persona");
    expect(es["mg.banner.synthetic_replay"]).toContain("guion");
  });
});

describe("unverified inference claims are never shown as trusted real inference (MG-ORIGIN-002)", () => {
  it("only session and verified runs are trusted; a missing or unknown trust value is not", () => {
    expect(isTrusted("session")).toBe(true);
    expect(isTrusted("verified")).toBe(true);
    for (const v of ["unverified", "", undefined, null, "VERIFIED", "trusted"]) expect(isTrusted(v as string | undefined | null)).toBe(false);
  });
  it("a stored model_inference claim without verification gets the unverified label, never the real-inference label", () => {
    for (const trust of ["unverified", undefined, null, "bogus"]) {
      const id = originLabelId("model_inference", trust as string | undefined | null);
      expect(id).toBe("mg.origin.model_inference_unverified");
      expect(t(id)).not.toContain("Inferencia experimental real");
      expect(t(id)).toContain("NO verificada");
    }
    expect(t(originLabelId("model_inference", "verified"))).toContain("Inferencia experimental real");
    expect(t(originLabelId("model_inference", "session"))).toContain("Inferencia experimental real");
  });
  it("the four kinds of observation source have four distinct labels", () => {
    const ids = [originLabelId("model_inference", "verified"), originLabelId("model_inference", "unverified"), originLabelId("synthetic_scripted", "unverified"), originLabelId("human_annotated", "unverified")];
    expect(new Set(ids).size).toBe(4);
  });
  it("replay and annotation labels do not depend on trust", () => {
    for (const trust of ["unverified", "verified"]) {
      expect(originLabelId("synthetic_scripted", trust)).toBe("mg.origin.synthetic_scripted");
      expect(originLabelId("human_annotated", trust)).toBe("mg.origin.human_annotated");
    }
  });
  it("unverified boxes use a grey dotted style with an explicit marker; trusted boxes keep their class colour", () => {
    const u = boxStyle(false, "#ff4d4f"), v = boxStyle(true, "#ff4d4f");
    expect(u.color).not.toBe("#ff4d4f");
    expect(u.dash.length).toBeGreaterThan(0);
    expect(u.labelPrefix).toContain("SIN VERIFICAR");
    expect(v).toEqual({ color: "#ff4d4f", dash: [], labelPrefix: "" });
  });
});

describe("frame order (MG-DATA-001-005)", () => {
  it("accepts only strictly increasing frame indexes", () => {
    expect(isStrictlyIncreasing([])).toBe(true);
    expect(isStrictlyIncreasing([0, 3, 6, 9])).toBe(true);
    expect(isStrictlyIncreasing([0, 6, 3, 9])).toBe(false);
    expect(isStrictlyIncreasing([0, 3, 3, 6])).toBe(false);
    expect(isStrictlyIncreasing([9, 6, 3, 0])).toBe(false);
  });
  it("the lookup is only meaningful on an ordered list, which is why an unordered list is refused", () => {
    expect(lastAnalyzedAtOrBefore([0, 3, 6, 9], 7)).toBe(2);
    expect(isStrictlyIncreasing([0, 9, 3, 6])).toBe(false);
  });
});

describe("session, verified and unverified runs are presented distinctly (MG-ORIGIN-003)", () => {
  const states = ["unverified", "session", "verified"];
  it("trustState maps anything unknown to unverified", () => {
    expect(states.map((x) => trustState(x))).toEqual(states);
    for (const v of [undefined, null, "", "VERIFIED", "trusted", "ok"]) expect(trustState(v as string | undefined | null)).toBe("unverified");
  });
  it("a newly created session run (before verification) is real inference but is never described as verified or reproduced", () => {
    const origin = t(originLabelId("model_inference", "session")), n = trustNoticeIds("session");
    expect(origin).toContain("Inferencia experimental real");
    expect(origin).toContain("sin reproducir");
    for (const txt of [origin, t(n.status), t(n.scope!)]) {
      expect(txt).not.toContain("verificada localmente");
      expect(txt).not.toContain("ejecutada y reproducida");
    }
    expect(t(n.status)).toContain("aún sin reverificar");
    expect(t(n.scope!)).toContain("todavía no se ha reproducido");
  });
  it("a run that passed deep verification may say it was verified and reproduced locally, and still disclaims authenticity", () => {
    const origin = t(originLabelId("model_inference", "verified")), n = trustNoticeIds("verified");
    expect(origin).toContain("verificada localmente");
    expect(origin).toContain("reproducida");
    expect(t(n.scope!)).toContain("No prueba autenticidad");
  });
  it("a stored unverified run (or a run whose verification failed and was revoked) is not shown as real inference", () => {
    const origin = t(originLabelId("model_inference", "unverified")), n = trustNoticeIds("unverified");
    expect(origin).toContain("NO verificada");
    expect(origin).not.toContain("Inferencia experimental real");
    expect(n.scope).toBeNull();
    expect(t(n.status)).toContain("no verificada");
  });
  it("the three states have three distinct origin labels, status texts, and only two have a scope text", () => {
    expect(new Set(states.map((x) => originLabelId("model_inference", x))).size).toBe(3);
    expect(new Set(states.map((x) => trustNoticeIds(x).status)).size).toBe(3);
    expect(states.map((x) => trustNoticeIds(x).scope !== null)).toEqual([false, true, true]);
    expect(new Set(["session", "verified"].map((x) => t(trustNoticeIds(x).scope!))).size).toBe(2);
  });
  it("synthetic and human origins keep their own labels in every state", () => {
    for (const x of states) {
      expect(originLabelId("synthetic_scripted", x)).toBe("mg.origin.synthetic_scripted");
      expect(originLabelId("human_annotated", x)).toBe("mg.origin.human_annotated");
    }
  });
});


const iv = (a: number, b: number, state: Interval["state"], pending = false): Interval => ({ start_us: a, end_us: b, state, event_ids: [], pending, causes: [], analyzed: true });
describe("risk presentation (VS-02)", () => {
  const tl = [iv(0, 100, "GREEN", true), iv(100, 200, "YELLOW"), iv(200, 300, "UNKNOWN"), iv(300, 400, "RED")];
  it("intervalAt is half-open and null outside the timeline", () => {
    expect([0, 99, 100, 199, 200, 399].map((x) => intervalAt(tl, x)?.state)).toEqual(["GREEN", "GREEN", "YELLOW", "YELLOW", "UNKNOWN", "RED"]);
    expect(intervalAt(tl, 400)).toBeNull();
    expect(intervalAt(tl, -1)).toBeNull();
    expect(intervalAt([], 5)).toBeNull();
  });
  it("worst state follows RED > YELLOW > UNKNOWN > GREEN", () => {
    expect(worstState(["GREEN", "UNKNOWN"])).toBe("UNKNOWN");
    expect(worstState(["UNKNOWN", "YELLOW", "GREEN"])).toBe("YELLOW");
    expect(worstState(["YELLOW", "RED", "UNKNOWN"])).toBe("RED");
    expect(worstState([])).toBe("GREEN");
  });
  it("GREEN never claims safety and a pending condition is never plain GREEN; a missing interval is UNKNOWN", () => {
    expect(riskLabelId("GREEN", false)).toBe("mg.risk.green");
    expect(riskLabelId("GREEN", true)).toBe("mg.risk.green_pending");
    expect(riskLabelId(null, false)).toBe("mg.risk.unknown");
    expect(riskLabelId(undefined, true)).toBe("mg.risk.unknown");
    expect(t("mg.risk.green")).toContain("No significa «seguro»");
    expect(t("mg.risk.green_pending")).toContain("PENDIENTE");
    expect(t("mg.risk.unknown")).toContain("No es verde ni seguro");
    for (const s of ["GREEN", "YELLOW", "RED", "UNKNOWN"]) expect(es[`mg.risk.badge.${s}`]).toBeTruthy();
  });
  it("timeline segments cover the stream and never vanish", () => {
    const seg = timelineSegments(tl, 400);
    expect(seg.map((x) => x.left)).toEqual([0, 25, 50, 75]);
    expect(seg.every((x) => x.width > 0)).toBe(true);
    expect(timelineSegments(tl, 0)).toEqual([]);
  });
  it("closing reasons are explicit; an open event is labelled open", () => {
    expect(closedReasonId(null, true)).toBe("mg.risk.reason.open");
    expect(closedReasonId("inputs_unknown")).toBe("mg.risk.reason.inputs_unknown");
    expect(closedReasonId("weird")).toBe("mg.risk.reason.other");
    for (const id of ["open", "rule_cleared", "inputs_unknown", "track_discontinuity", "end_of_stream", "other"]) expect(es[`mg.risk.reason.${id}`]).toBeTruthy();
  });
  it("evaluationTotalUs is the largest interval end", () => {
    expect(evaluationTotalUs(null)).toBe(0);
    expect(evaluationTotalUs({ zones: { A: { timeline: tl }, B: { timeline: [iv(0, 900, "GREEN")] } } } as never)).toBe(900);
  });
});

describe("zone drawing and webcam capture helpers (VS-02)", () => {
  const rect = { x: 10, y: 20, w: 640, h: 360 };
  it("pointer to frame coordinates: rounded integers, clamped, null outside the picture", () => {
    expect(clientToFrame(10, 20, rect, 1280, 720)).toEqual([0, 0]);
    expect(clientToFrame(330, 200, rect, 1280, 720)).toEqual([640, 360]);
    expect(clientToFrame(650, 380, rect, 1280, 720)).toEqual([1280, 720]);
    expect(clientToFrame(9, 100, rect, 1280, 720)).toBeNull();
    expect(clientToFrame(100, 381, rect, 1280, 720)).toBeNull();
    expect(clientToFrame(5, 5, { x: 0, y: 0, w: 0, h: 0 }, 10, 10)).toBeNull();
  });
  it("client-side polygon check mirrors the server codes", () => {
    const sq = [[100, 100], [500, 100], [500, 400], [100, 400]];
    expect(polygonProblem(sq, 1280, 720)).toBeNull();
    expect(polygonProblem(sq.slice(0, 2), 1280, 720)).toBe("mg.refusal.zone_vertex_count");
    expect(polygonProblem([[0, 0], [10, 0], [10, 0], [0, 10]], 1280, 720)).toBe("mg.refusal.zone_duplicate_vertex");
    expect(polygonProblem([[0, 0], [10, 0], [20, 0]], 1280, 720)).toBe("mg.refusal.zone_collinear_vertices");
    expect(polygonProblem([[0, 0], [100, 100], [100, 0], [0, 100]], 1280, 720)).toBe("mg.refusal.zone_self_intersection");
    expect(polygonProblem([[0, 0], [1281, 0], [100, 100]], 1280, 720)).toBe("mg.refusal.zone_out_of_frame");
    expect(polygonProblem([[0, 0], [10.5, 0], [10, 10]], 1280, 720)).toBe("mg.refusal.zone_out_of_frame");
    for (const id of ["vertex_count", "duplicate_vertex", "collinear_vertices", "zero_area", "self_intersection", "out_of_frame", "not_integer"]) expect(es[`mg.refusal.zone_${id}`]).toBeTruthy();
  });
  it("capture dimensions keep the aspect ratio, cap the width, and refuse tiny sources", () => {
    expect(captureDims(1920, 1080)).toEqual({ w: 640, h: 360 });
    expect(captureDims(320, 240)).toEqual({ w: 320, h: 240 });
    expect(captureDims(100, 100)).toBeNull();
    expect(captureDims(0, 480)).toBeNull();
  });
  it("the client never has more than two uploads in flight", () => {
    expect([0, 1, 2, 3].map((n) => mayCapture(n))).toEqual([true, true, false, false]);
  });
});


describe("simulation labelling (no personal images)", () => {
  it("every simulation string exists, says SIMULACIÓN and never claims real inference", () => {
    for (const id of ["mg.sim.title", "mg.sim.help", "mg.sim.run", "mg.sim.badge", "mg.sim.note", "mg.sim.back", "mg.sim.hint"]) expect(es[id]).toBeTruthy();
    expect(es["mg.sim.badge"]).toBe("SIMULACIÓN");
    expect(es["mg.sim.note"]).toContain("no hay inferencia ni personas reales");
    expect(es["mg.sim.help"]).toContain("No usa cámara, fotografías ni modelo");
    expect(`${es["mg.sim.title"]} ${es["mg.sim.note"]}`).not.toContain("Inferencia experimental real");
  });
});


describe("current risk of a feed (MG-PRODUCT-VERTICAL-03 + review 25 MG-STATE-025: only an observed feed has a current state)", () => {
  it("keeps the observed state only while the feed is playing", async () => {
    const { currentRisk } = await import("./lib");
    expect(currentRisk("playing", "GREEN")).toBe("GREEN");
    expect(currentRisk("playing", "RED")).toBe("RED");
  });
  it("turns every other lifecycle state into UNKNOWN, whatever its last value was (paused and ended included)", async () => {
    const { currentRisk } = await import("./lib");
    for (const s of ["idle", "stopped", "failed", "paused", "ended"]) for (const last of ["GREEN", "YELLOW", "RED", "UNKNOWN"] as const) expect(currentRisk(s, last)).toBe("UNKNOWN");
  });
});

describe("provenance of an incident shown in lists (review 25 MG-TRUST-025)", () => {
  it("a record restored from disk is never shown as real inference", async () => {
    const { incidentProvenance } = await import("./lib");
    expect(incidentProvenance({ provenance: "real_inference", simulation: false, loaded_from_store: true })).toBe("unverified");
    expect(incidentProvenance({ provenance: "synthetic_scripted", simulation: true, loaded_from_store: true })).toBe("unverified");
  });
  it("live records keep their own provenance", async () => {
    const { incidentProvenance } = await import("./lib");
    expect(incidentProvenance({ provenance: "real_inference", simulation: false, loaded_from_store: false })).toBe("real");
    expect(incidentProvenance({ provenance: "real_inference", simulation: false })).toBe("real");
    expect(incidentProvenance({ provenance: "repeated_source_simulation", simulation: true })).toBe("repeated");
    expect(incidentProvenance({ provenance: "synthetic_scripted", simulation: true })).toBe("synthetic");
  });
});

describe("every i18n id used by the new console exists", () => {
  it("has the keys the shell, overview, incident, simulation and feed views need", () => {
    for (const id of ["mg.shell.skip", "mg.ov.kpi.sources", "mg.inc.title", "mg.sim.explain", "mg.feed.flag.stale", "mg.dev.build_unknown", "mg.cfg.limits", "mg.vid.go_event", "mg.shell.status.idle"]) expect(es[id]).toBeTruthy();
  });
});

describe("nextLabel (consistent default source names)", () => {
  it("numbers every source, never a bare «Fuente»", () => {
    expect(nextLabel("Fuente", 1, [])).toBe("Fuente 1");
    expect(nextLabel("", 1, [])).toBe("Fuente 1");
  });
  it("skips numbers already used and keeps a custom base", () => {
    expect(nextLabel("Fuente", 1, [{ label: "Fuente 1" }, { label: "Fuente 2" }])).toBe("Fuente 3");
    expect(nextLabel("Rampa norte", 2, [{ label: "Fuente 1" }])).toBe("Rampa norte 2");
  });
});

describe("oafAcceptanceView (MG-OAF-026: strict booleans, never truthiness)", () => {
  const ok = { recorded: true, validated: true, release_accepted_claimed: false, acceptance: "not_accepted", verdict: "OWNER_DECISION_REQUIRED" };
  it("a valid non-accepted record is not accepted", () => expect(oafAcceptanceView(ok).kind).toBe("not_accepted"));
  it("the STRING «false» (truthy) is never acceptance", () => {
    expect(oafAcceptanceView({ ...ok, release_accepted_claimed: "false" as unknown as boolean }).kind).toBe("invalid");
    expect(oafAcceptanceView({ ...ok, release_accepted_claimed: "true" as unknown as boolean, acceptance: "claimed_accepted", verdict: "CONTRACT_PASS" }).kind).toBe("invalid");
  });
  it("missing, null and numeric flags are invalid", () => {
    expect(oafAcceptanceView({ ...ok, release_accepted_claimed: undefined as unknown as boolean }).kind).toBe("invalid");
    expect(oafAcceptanceView({ ...ok, release_accepted_claimed: null as unknown as boolean }).kind).toBe("invalid");
    expect(oafAcceptanceView({ ...ok, release_accepted_claimed: 1 as unknown as boolean }).kind).toBe("invalid");
  });
  it("MG-OAF-028: the API answer for a forged rank-6 «CONTRACT_PASS» record is NOT VERIFIED, and a payload that merely claims acceptance without the evaluator's PASS is not shown as accepted", () => {
    expect(oafAcceptanceView({ recorded: false, reason: "invalid_record", invalid_why: "reasons" } as never).kind).toBe("invalid");
    expect(oafAcceptanceView({ ...ok, release_accepted_claimed: true, acceptance: "claimed_accepted", verdict: "OWNER_DECISION_REQUIRED" }).kind).toBe("not_accepted");
    expect(oafAcceptanceView({ ...ok, release_accepted_claimed: true, acceptance: "not_accepted", verdict: "CONTRACT_PASS" }).kind).toBe("not_accepted");
  });
  it("an unvalidated record, an unrecorded one and an invalid one are never accepted", () => {
    expect(oafAcceptanceView({ ...ok, validated: false }).kind).toBe("invalid");
    expect(oafAcceptanceView({ recorded: false }).kind).toBe("none");
    expect(oafAcceptanceView({ recorded: false, reason: "invalid_record" } as never).kind).toBe("invalid");
    expect(oafAcceptanceView(null).kind).toBe("none");
  });
  it("only an internally consistent accepted PASS is shown as recorded-accepted", () => {
    expect(oafAcceptanceView({ ...ok, release_accepted_claimed: true, acceptance: "claimed_accepted", verdict: "CONTRACT_PASS" }).kind).toBe("claimed_accepted");
    expect(oafAcceptanceView({ ...ok, release_accepted_claimed: true, acceptance: "claimed_accepted" }).kind).toBe("not_accepted");   // OWNER_DECISION_REQUIRED is never acceptance
  });
});

describe("alertProvenance (MG-TRUST-026: one classification on every surface)", () => {
  const live = { simulation: false, provenance: "real_inference" };
  it("genuine live", () => expect(alertProvenance(live)).toBe("real"));
  it("restored is never real, whatever its stored provenance says", () => {
    expect(alertProvenance({ ...live, loaded_from_store: true })).toBe("unverified");
    expect(alertProvenance({ simulation: true, provenance: "synthetic_scripted", loaded_from_store: true })).toBe("unverified");
  });
  it("simulated and repeated-file concurrency alerts", () => {
    expect(alertProvenance({ simulation: true, provenance: "synthetic_scripted" })).toBe("synthetic");
    expect(alertProvenance({ simulation: true, provenance: "repeated_source_simulation" })).toBe("repeated");
    expect(alertProvenance({ simulation: false, source: { provenance: "repeated_source_simulation" } })).toBe("repeated");
  });
  it("acknowledging or resolving carries no provenance input at all", () => expect(alertProvenance({ ...live, loaded_from_store: true, ...({ status: "resolved" } as object) })).toBe("unverified"));
});

describe("overlapPhaseId (MG-PROV-028: an unrecognised or missing phase is unknown, never «alone»)", () => {
  it("maps every known phase to its own message", () => {
    for (const p of ["alone", "from_launch", "before_overlap", "during_overlap", "between_overlaps", "after_overlap", "mixed"]) expect(overlapPhaseId(p)).toBe(`mg.ov.phase.${p}`);
  });
  it("never reads a missing, forged or non-string value as alone", () => {
    for (const v of [undefined, null, "genuine", 3, {}, "ALONE"]) expect(overlapPhaseId(v)).toBe("mg.ov.phase.unknown");
  });
});
