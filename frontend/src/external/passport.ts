// Pure facts for the Meta result "evidence passport" (UI-REVAMP-22). Everything is derived from the verified review record; the passport can only say less than the record proves, never more.
import type { ExternalReview } from "../api";

export interface Passport {
  origin: "EXTERNAL_MANUAL_PROVIDER_RESULT";
  state: string;                                  // verification state as the importer reported it (e.g. HASHES_VERIFIED_AUTHENTICITY_NOT_PROVEN, SYNTHETIC_FIXTURE)
  synthetic: boolean;
  authenticity: "not_proven";                     // constant: bytes cannot prove who produced them
  classes: "unclassified";                        // constant: a prompt/mask does not make a segment a class
  confidence: "not_reported" | "provider_score_uncalibrated";
  masks: "structure_checked_not_rendered";
  execution: "manual_external_not_in_app";
  objects: number;
  riskAssessed: false;                            // constant: imported evidence never enters the risk kernel
}

export function passportOf(r: Pick<ExternalReview, "verification" | "objects">): Passport {
  const objs = r.objects ?? [];
  const anyScore = objs.some((o) => o.provider_score_permille !== null && o.provider_score_permille !== undefined);
  return {
    origin: "EXTERNAL_MANUAL_PROVIDER_RESULT", state: r.verification?.state ?? "UNKNOWN", synthetic: r.verification?.state === "SYNTHETIC_FIXTURE", authenticity: "not_proven", classes: "unclassified",
    confidence: anyScore ? "provider_score_uncalibrated" : "not_reported", masks: "structure_checked_not_rendered", execution: "manual_external_not_in_app", objects: objs.length, riskAssessed: false,
  };
}
