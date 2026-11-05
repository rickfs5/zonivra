// FINAL-ENGINEERING-15-R1: pure presentation helpers for the deterministic scenario replays. No state is decided here: every state shown comes from the backend's recomputation by the risk kernel.
export interface ScnSample { t_ms: number; input: string; state: "GREEN" | "YELLOW" | "RED" | "UNKNOWN" }
export interface ScnTransition { t_ms: number; sample_index: number; from: string | null; to: string; causes: string[]; input: string; pending_rules: number }
export interface ScnEvent { event_id: string; rule_id: string; rule_version: string; severity: "YELLOW" | "RED"; start_ms: number; end_ms: number | null; closed_reason: string | null; tracks: string[]; right_censored: boolean }
export interface Scenario {
  schema: string; id: string; case: string; digest: string;
  provenance: { kind: string; real_footage: boolean; model_inference: boolean; detections: string; evaluated_by: string; persisted: boolean; provider_evidence_used: boolean };
  input: { width: number; height: number; interval_ms: number; duration_ms: number; sample_count: number; vehicle_object_label: string; segments: { from_ms: number; to_ms: number; input: string }[]; coord_space: string };
  zone: { zone_id: string; kind: string; polygon: number[][]; width: number; height: number };
  kernel: { engine_version: string; semantics_version: string; rules: { rule_id: string; severity: string }[] };
  samples: ScnSample[]; transitions: ScnTransition[]; events: ScnEvent[];
  coverage_ms: { total: number; GREEN: number; YELLOW: number; RED: number; UNKNOWN: number };
  caveats: string[]; review: { status: string; reviewed_by: string | null; note: string };
}
export interface ScnList { schema: string; provenance_kind: string; scenarios: { id: string; case: string }[] }

export interface StripRun { state: string; from_ms: number; to_ms: number }
/** Merges consecutive samples of one state into runs. Silent time (no sample) is its own "NO_SAMPLE" run so a gap is never painted as the neighbouring state. */
export function stateStrip(samples: ScnSample[], intervalMs: number, durationMs: number): StripRun[] {
  const out: StripRun[] = [];
  let cursor = 0;
  for (const s of samples) {
    if (s.t_ms > cursor) out.push({ state: "NO_SAMPLE", from_ms: cursor, to_ms: s.t_ms });
    const last = out[out.length - 1];
    if (last && last.state === s.state && last.to_ms === s.t_ms) last.to_ms = s.t_ms + intervalMs;
    else out.push({ state: s.state, from_ms: s.t_ms, to_ms: s.t_ms + intervalMs });
    cursor = s.t_ms + intervalMs;
  }
  if (cursor < durationMs) out.push({ state: "NO_SAMPLE", from_ms: cursor, to_ms: durationMs });
  return out;
}
export const seconds = (ms: number): string => `${(ms / 1000).toFixed(1)} s`;
export const titleId = (sid: string): string => `mg.scn.id.${sid}`;
export const descId = (sid: string): string => `mg.scn.desc.${sid}`;
export const inputId = (spec: string): string => `mg.scn.input.${spec}`;
export const isSynthetic = (s: Scenario): boolean => s.provenance.kind === "SYNTHETIC_SCENARIO_REPLAY" && !s.provenance.real_footage && !s.provenance.model_inference;
export const reviewPending = (s: Scenario): boolean => s.review.status === "PENDING_HUMAN_REVIEW" && s.review.reviewed_by === null;
export function widthPct(run: StripRun, durationMs: number): number { return durationMs > 0 ? ((run.to_ms - run.from_ms) / durationMs) * 100 : 0; }
