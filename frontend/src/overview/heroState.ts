// Pure derivation of the Dashboard hero facts (UI-REVAMP-22). Inputs are values the monitor API already returned; nothing is invented, nothing is rounded up to "healthy" or "safe".
export interface HeroInput { activeFeeds: number; activeLimit: number; validatedFeeds: number; schedulerAlive: boolean; modelLoaded: boolean; detectorError: string | null; activeSession: boolean; feedsFailed: number }
export type ModelFact = "loaded" | "not_loaded" | "error";
export type WorkerFact = "alive" | "idle" | "dead";
export type AnalysisFact = "session_active" | "feeds_active" | "idle";
export interface HeroFacts { model: ModelFact; worker: WorkerFact; analysis: AnalysisFact; sources: { active: number; limit: number; validated: number }; failedFeeds: number }

export function heroFacts(i: HeroInput): HeroFacts {
  const model: ModelFact = i.detectorError ? "error" : i.modelLoaded ? "loaded" : "not_loaded";            // "not_loaded" is not a fault: the model loads on first use
  const worker: WorkerFact = i.schedulerAlive ? "alive" : i.activeFeeds === 0 ? "idle" : "dead";
  const analysis: AnalysisFact = i.activeSession ? "session_active" : i.activeFeeds > 0 && i.schedulerAlive ? "feeds_active" : "idle";
  return { model, worker, analysis, sources: { active: i.activeFeeds, limit: i.activeLimit, validated: i.validatedFeeds }, failedFeeds: i.feedsFailed };
}
export const EVIDENCE_CLASSES = ["real", "synthetic", "external", "unverified"] as const;
export type EvidenceClass = (typeof EVIDENCE_CLASSES)[number];
