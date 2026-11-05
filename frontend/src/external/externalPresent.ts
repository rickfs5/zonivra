// Pure presentation helpers for the external-result review (MG-EVIDENCE-10). No component state, no network.
import { hasMsg } from "../i18n";
import type { ExternalReview } from "../api";

export type Banner = "external" | "synthetic" | "unproven" | "no_risk";

/** Banners that MUST accompany a review. "external" and "no_risk" are unconditional: an imported result is never shown without them. */
export function bannersFor(r: Pick<ExternalReview, "verification">): Banner[] {
  const s = r.verification?.state;
  return ["external", ...(s === "SYNTHETIC_FIXTURE" ? ["synthetic" as const] : s === "HASHES_VERIFIED_AUTHENTICITY_NOT_PROVEN" ? ["unproven" as const] : []), "no_risk"];
}

export const stateMsgId = (state: string | undefined) => (state && hasMsg(`mg.ext.state.${state}`) ? `mg.ext.state.${state}` : "mg.ext.refusal.generic");
export const integrityMsgId = (i: string) => (i === "VERIFIED" || i === "STORED_COPY_MODIFIED" ? `mg.ext.integrity.${i}` : "mg.ext.refusal.generic");
export const linkageMsgId = (l: string | undefined) => (l && hasMsg(`mg.ext.linkage.${l}`) ? `mg.ext.linkage.${l}` : "mg.ext.refusal.generic");

/** The refusal message id carried by an API error text (`"<status> {json}"`); anything unexpected maps to the generic refusal and the raw text is never shown. */
export function refusalMsgId(errText: string): string {
  const m = /"msg_id"\s*:\s*"([A-Za-z0-9_.]+)"/.exec(errText);
  return m && (m[1].startsWith("mg.ext.refusal.") || m[1].startsWith("mg.refusal.")) && hasMsg(m[1]) ? m[1] : "mg.ext.refusal.generic";
}

export const scorePercent = (permille: number | null) => (permille === null ? null : `${(permille / 10).toFixed(1)}%`);

/** Rectangle of a verified box in the declared image space (the SVG uses a viewBox equal to the image size, so no scaling or clipping is applied). */
export function boxRect(b: { x1: number; y1: number; x2: number; y2: number }, w: number, h: number): { x: number; y: number; width: number; height: number } | null {
  if (![b.x1, b.y1, b.x2, b.y2, w, h].every(Number.isInteger) || b.x1 < 0 || b.y1 < 0 || b.x2 > w || b.y2 > h || b.x2 <= b.x1 || b.y2 <= b.y1) return null;   // never drawn if it does not verify
  return { x: b.x1, y: b.y1, width: b.x2 - b.x1, height: b.y2 - b.y1 };
}

export function bytesToBase64(bytes: Uint8Array): string {
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}
