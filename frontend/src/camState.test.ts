import { describe, expect, it } from "vitest";
import { camView, classifyDeviceError, isAnalysing, isDeviceFailure, isModelUnavailable, modelIndicator, viewHelpId, viewMsgId, type CamPhase, type CamView } from "./camState";
import { en, es } from "./i18n";

const dom = (name: string) => Object.assign(new Error("x"), { name });

describe("camera state (FINAL-ENGINEERING-15-R1)", () => {
  it("classifies getUserMedia failures: permission, no device, device error", () => {
    for (const n of ["NotAllowedError", "PermissionDeniedError", "SecurityError"]) expect(classifyDeviceError(dom(n))).toBe("permission_denied");
    for (const n of ["NotFoundError", "DevicesNotFoundError", "OverconstrainedError"]) expect(classifyDeviceError(dom(n))).toBe("no_device");
    for (const n of ["NotReadableError", "AbortError", "TrackStartError", "TypeError"]) expect(classifyDeviceError(dom(n))).toBe("device_error");
    expect(classifyDeviceError(new Error("camera too small"))).toBe("device_error");
    expect(classifyDeviceError("boom")).toBe("device_error");
    expect(classifyDeviceError(null)).toBe("device_error");
    expect(classifyDeviceError(undefined)).toBe("device_error");
  });

  it("maps the existing phases to exactly one view; only 'analyzing' is analysis", () => {
    const table: [CamPhase, boolean, CamView][] = [
      ["off", false, "stopped"], ["off", true, "stopped"], ["requesting", false, "requesting"], ["on", false, "preview_only"], ["on", true, "analysis_unavailable"],
      ["analyzing", false, "analysis_active"], ["analyzing", true, "analysis_active"], ["finalizing", false, "finalizing"],
    ];
    for (const [p, un, v] of table) expect(camView(p, null, un)).toBe(v);
    expect(camView("denied", "permission_denied", false)).toBe("permission_denied");
    expect(camView("denied", "no_device", false)).toBe("no_device");
    expect(camView("denied", "device_error", true)).toBe("device_error");
    expect(camView("denied", null, false)).toBe("device_error");
  });

  it("preview-only is never presented as analysis, and analysis is never reachable from the preview phase", () => {
    for (const un of [false, true]) expect(isAnalysing(camView("on", null, un))).toBe(false);
    for (const p of ["off", "requesting", "denied", "finalizing"] as CamPhase[]) expect(isAnalysing(camView(p, "device_error", false))).toBe(false);
    expect(isAnalysing(camView("analyzing", null, false))).toBe(true);
    expect(isAnalysing("preview_only")).toBe(false);
    expect(isDeviceFailure("no_device")).toBe(true);
    expect(isDeviceFailure("preview_only")).toBe(false);
  });

  it("recognises the model-unavailable refusal only", () => {
    expect(isModelUnavailable(new Error('503 {"detail":{"msg_id":"mg.refusal.model_unavailable"}}'))).toBe(true);
    expect(isModelUnavailable(new Error('409 {"detail":{"msg_id":"mg.refusal.job_in_progress"}}'))).toBe(false);
    expect(isModelUnavailable(new Error("500 Internal Server Error"))).toBe(false);
  });

  it("model indicator is honest: verified weights, unverified weights, or unknown", () => {
    expect(modelIndicator({ verified: true })).toBe("verified");
    expect(modelIndicator({ verified: false })).toBe("unverified");
    expect(modelIndicator(null)).toBe("unknown");
    expect(modelIndicator({} as { verified?: boolean })).toBe("unknown");
  });

  it("every view, its help text, the model indicator and the refusal exist in English AND Spanish", () => {
    const views: CamView[] = ["stopped", "requesting", "preview_only", "analysis_active", "finalizing", "analysis_unavailable", "permission_denied", "no_device", "device_error"];
    const ids = [...views.flatMap((v) => [viewMsgId(v), viewHelpId(v)]), "mg.cam.model.verified", "mg.cam.model.unverified", "mg.cam.model.unknown", "mg.refusal.model_unavailable",
      "mg.cam.preview_banner", "mg.cam.analysis_banner", "mg.cam.preview_label"];
    for (const id of ids) { expect(en[id], `en ${id}`).toBeTruthy(); expect(es[id], `es ${id}`).toBeTruthy(); expect(en[id]).not.toBe(es[id]); }
  });

  it("the preview wording says nothing is analysed; the analysis wording says frames are analysed locally", () => {
    expect(en["mg.cam.preview_banner"]).toMatch(/nothing is being analy[sz]ed/i);
    expect(en["mg.cam.view.preview_only"]).toMatch(/preview only/i);
    expect(en["mg.cam.view.preview_only"]).not.toMatch(/analy[sz]ing|analysis active/i);
    expect(en["mg.cam.analysis_banner"]).toMatch(/analy[sz]ed locally/i);
    expect(en["mg.cam.view.analysis_unavailable"]).toMatch(/unavailable/i);
  });
});
