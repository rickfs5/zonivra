// FINAL-ENGINEERING-15-R1: pure presentation state of the webcam page. It REUSES the panel's existing `cam` machine (off/requesting/on/denied/analyzing/finalizing) and only adds what the
// page must say: why the camera is unavailable (D3) and that analysis could not start (D1). Preview-only is NEVER an analysis state.
export type CamPhase = "off" | "requesting" | "on" | "denied" | "analyzing" | "finalizing";
export type DeviceFailure = "permission_denied" | "no_device" | "device_error";
export type CamView = "stopped" | "requesting" | "preview_only" | "analysis_active" | "finalizing" | "analysis_unavailable" | DeviceFailure;
export type ModelIndicator = "verified" | "unverified" | "unknown";

/** getUserMedia failures by DOMException name. Anything not recognised is a device error, never "denied". */
export function classifyDeviceError(e: unknown): DeviceFailure {
  const name = typeof e === "object" && e !== null && "name" in e ? String((e as { name: unknown }).name) : "";
  if (name === "NotAllowedError" || name === "PermissionDeniedError" || name === "SecurityError") return "permission_denied";
  if (name === "NotFoundError" || name === "DevicesNotFoundError" || name === "OverconstrainedError") return "no_device";
  return "device_error";
}

export function camView(phase: CamPhase, failure: DeviceFailure | null, analysisUnavailable: boolean): CamView {
  switch (phase) {
    case "analyzing": return "analysis_active";
    case "finalizing": return "finalizing";
    case "requesting": return "requesting";
    case "denied": return failure ?? "device_error";
    case "on": return analysisUnavailable ? "analysis_unavailable" : "preview_only";
    default: return "stopped";
  }
}

export const viewMsgId = (v: CamView): string => `mg.cam.view.${v}`;
export const viewHelpId = (v: CamView): string => `mg.cam.view.${v}.help`;
/** Only this view means frames are being analysed. */
export const isAnalysing = (v: CamView): boolean => v === "analysis_active";
export const isDeviceFailure = (v: CamView): v is DeviceFailure => v === "permission_denied" || v === "no_device" || v === "device_error";

/** The backend answers 503 {"detail":{"msg_id":"mg.refusal.model_unavailable"}} when the model cannot be created. */
export function isModelUnavailable(e: unknown): boolean {
  return /mg\.refusal\.model_unavailable/.test(String(e));
}

/** What /api/model can honestly say: the weights file matches the pinned digest. It does NOT prove the model loads (that is only known when an analysis starts). */
export function modelIndicator(m: { verified?: boolean } | null): ModelIndicator {
  if (m === null || typeof m.verified !== "boolean") return "unknown";
  return m.verified ? "verified" : "unverified";
}
export const modelMsgId = (i: ModelIndicator): string => `mg.cam.model.${i}`;
