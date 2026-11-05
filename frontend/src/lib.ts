import { t } from "./i18n";

export interface Box { x: number; y: number; w: number; h: number }
export interface Obj {
  obj_key: string;
  cls: { label: "person" | "vehicle" | "other"; detector_label: string; detector_class_id: number };
  bbox: Box;
  truncated: { left: boolean; right: boolean; top: boolean; bottom: boolean };
  confidence: { permille: number | null; kind: string };
}
export interface Frame { frame_index: number; pixel_sha256: string; objects: Obj[] }

/** Index of the frame covering t_us: last frame with pts_us <= t_us (frame i covers [pts_us(i), pts_us(i+1))). */
export function frameForTimeUs(ptsUs: number[], tUs: number): number {
  if (ptsUs.length === 0 || tUs < ptsUs[0]) return 0;
  let lo = 0, hi = ptsUs.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (ptsUs[mid] <= tUs) lo = mid; else hi = mid - 1;
  }
  return lo;
}

/** Position in the sorted list of analyzed frame indexes of the last analyzed frame <= frameIndex (-1 if none). */
export function lastAnalyzedAtOrBefore(analyzed: number[], frameIndex: number): number {
  let lo = 0, hi = analyzed.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (analyzed[mid] <= frameIndex) { ans = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return ans;
}

/** Map a box from analysis-frame pixels to displayed pixels. */
export function scaleBox(b: Box, analysisW: number, analysisH: number, shownW: number, shownH: number): Box {
  const sx = shownW / analysisW, sy = shownH / analysisH;
  return { x: b.x * sx, y: b.y * sy, w: b.w * sx, h: b.h * sy };
}

/** Largest rectangle of the given aspect ratio centered in a container (object-fit: contain). */
export function containRect(cw: number, ch: number, aw: number, ah: number): Box {
  const s = Math.min(cw / aw, ch / ah);
  const w = aw * s, h = ah * s;
  return { x: (cw - w) / 2, y: (ch - h) / 2, w, h };
}

export function formatMs(ptsUs: number): string {
  const totalMs = Math.floor(ptsUs / 1000);
  const m = Math.floor(totalMs / 60000), s = Math.floor((totalMs % 60000) / 1000), ms = totalMs % 1000;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}.${String(ms).padStart(3, "0")}`;
}

export const classColor = (label: string): string => (label === "person" ? "#ff4d4f" : label === "vehicle" ? "#ffb020" : "#4cc3ff");

export function summarize(frames: Frame[], minPermille: number): Record<string, number> {
  const out: Record<string, number> = {};
  for (const f of frames) for (const o of f.objects) {
    if ((o.confidence.permille ?? 0) >= minPermille) out[o.cls.detector_label] = (out[o.cls.detector_label] ?? 0) + 1;
  }
  return out;
}

export type Origin = "model_inference" | "synthetic_scripted" | "human_annotated";
export type Trust = "session" | "verified" | "unverified";

/** The three presentation states of a run (MG-ORIGIN-003). Any other or missing value is "unverified".
 * "session": real inference produced by this server process, NOT yet re-decoded and re-run. "verified": passed the local deep verification. */
export function trustState(trust: string | undefined | null): Trust {
  return trust === "session" || trust === "verified" ? trust : "unverified";
}

/** Message ids for the status paragraph and the scope paragraph of a run; the three states never share text. */
export function trustNoticeIds(trust: string | undefined | null): { status: string; scope: string | null } {
  switch (trustState(trust)) {
    case "verified": return { status: "mg.ui.real_inference", scope: "mg.ui.trust_scope" };
    case "session": return { status: "mg.ui.session_run", scope: "mg.ui.trust_scope_session" };
    default: return { status: "mg.ui.unverified_run", scope: null };
  }
}

/** True only for a run this process created ("session") or that passed deep verification ("verified"). Anything else, including a missing value, is untrusted. */
export function isTrusted(trust: string | undefined | null): boolean {
  return trust === "session" || trust === "verified";
}

/** Frame indexes must be strictly increasing; the overlay binary-searches them, so any other order could draw another frame's boxes (MG-DATA-001-005). */
export function isStrictlyIncreasing(xs: number[]): boolean {
  for (let i = 1; i < xs.length; i++) if (!(xs[i] > xs[i - 1])) return false;
  return true;
}

/** How boxes are drawn. Unverified boxes are a provisional inspection view: grey, dotted, and every label starts with the "unverified" marker. */
export function boxStyle(trusted: boolean, classColorValue: string): { color: string; dash: number[]; labelPrefix: string } {
  return trusted ? { color: classColorValue, dash: [], labelPrefix: "" } : { color: "#9aa0a6", dash: [2, 3], labelPrefix: t("mg.xd.lib.unverified_prefix") };
}

/** Message id for the origin label. A stored model_inference claim is shown as real inference ONLY when the run is trusted (MG-ORIGIN-002); "reproduced" wording is reserved for "verified" (MG-ORIGIN-003);
 * an unverified one, a missing trust value and an unrecognised origin are never shown as inference. */
export function originLabelId(origin: string, trust?: string | null): string {
  switch (origin) {
    case "model_inference": {
      const ts = trustState(trust);
      return ts === "verified" ? "mg.origin.model_inference" : ts === "session" ? "mg.origin.model_inference_session" : "mg.origin.model_inference_unverified";
    }
    case "synthetic_scripted": return "mg.origin.synthetic_scripted";
    case "human_annotated": return "mg.origin.human_annotated";
    default: return "mg.origin.unknown";
  }
}

// ---------------------------------------------------------------------------------------------------------------- risk (VS-02)
export type RiskState = "GREEN" | "YELLOW" | "RED" | "UNKNOWN";
export interface Interval { start_us: number; end_us: number; state: RiskState; event_ids: string[]; pending: boolean; causes: string[]; analyzed: boolean }
export interface RiskEvent {
  event_id: string; zone_id: string; rule_id: string; severity: "YELLOW" | "RED"; key: string[]; start_us: number; end_us: number | null; condition_since_us: number;
  dwell_since_us: number | null; closed_reason: string | null; left_censored: boolean; right_censored: boolean; open?: boolean;
  incomplete: { cause: string; from_us: number; to_us: number }[]; absorbed: { from_us: number; to_us: number }[];
}
export interface ZoneResult { zone_id: string; timeline: Interval[]; events: RiskEvent[]; suppressed_candidates: unknown[]; dropped_tracks: unknown[];
  coverage_us: Record<string, number | boolean>; unknown_by_cause_us: Record<string, number> }
export interface Zone { zone_id: string; kind: "restricted" | "monitored"; polygon: number[][]; width: number; height: number; name: string; digest?: string }
export interface EvidenceRef { frame_index: number; file: string; file_sha256: string; requested_index: number }
export interface Evaluation { digest: string; origin: string; evidence_refs?: Record<string, Record<string, EvidenceRef>>; zones: Record<string, ZoneResult>; zones_not_evaluated: { zone_id: string }[]; limitations: string[];
  tracks: { track_id: string; label: string }[]; policy: { semantics_version: number; engine_version: string } }

/** The interval [start,end) containing t, or null. Timelines are gap-free and half-open (MG-RISK-002 section 3). */
export function intervalAt(tl: Interval[], t: number): Interval | null {
  let lo = 0, hi = tl.length - 1;
  while (lo <= hi) {
    const m = (lo + hi) >> 1;
    if (t < tl[m].start_us) hi = m - 1;
    else if (t >= tl[m].end_us) lo = m + 1;
    else return tl[m];
  }
  return null;
}

const SEVERITY: Record<RiskState, number> = { GREEN: 0, UNKNOWN: 1, YELLOW: 2, RED: 3 };
/** RED > YELLOW > UNKNOWN > GREEN (MG-RISK-001 section 3.1). */
export function worstState(states: RiskState[]): RiskState {
  return states.reduce<RiskState>((a, b) => (SEVERITY[b] > SEVERITY[a] ? b : a), "GREEN");
}

/** Message id for a risk state. GREEN never says "safe", and a pending condition is never shown as plain GREEN. A missing interval is UNKNOWN. */
export function riskLabelId(state: RiskState | null | undefined, pending: boolean): string {
  switch (state) {
    case "RED": return "mg.risk.red";
    case "YELLOW": return "mg.risk.yellow";
    case "GREEN": return pending ? "mg.risk.green_pending" : "mg.risk.green";
    default: return "mg.risk.unknown";
  }
}

export function timelineSegments(tl: Interval[], totalUs: number): { left: number; width: number; state: RiskState; pending: boolean }[] {
  if (totalUs <= 0) return [];
  return tl.map((i) => ({ left: (100 * i.start_us) / totalUs, width: Math.max(0.2, (100 * (i.end_us - i.start_us)) / totalUs), state: i.state, pending: i.pending }));
}

export function closedReasonId(reason: string | null, open?: boolean): string {
  if (open || reason === null) return "mg.risk.reason.open";
  return ({ rule_cleared: "mg.risk.reason.rule_cleared", inputs_unknown: "mg.risk.reason.inputs_unknown", track_discontinuity: "mg.risk.reason.track_discontinuity",
    end_of_stream: "mg.risk.reason.end_of_stream" } as Record<string, string>)[reason] ?? "mg.risk.reason.other";
}

/** Maps a pointer position inside the displayed (contain-fitted) frame to integer frame coordinates, or null outside the picture. */
export function clientToFrame(cx: number, cy: number, rect: { x: number; y: number; w: number; h: number }, fw: number, fh: number): [number, number] | null {
  if (rect.w <= 0 || rect.h <= 0 || cx < rect.x || cy < rect.y || cx > rect.x + rect.w || cy > rect.y + rect.h) return null;
  const x = Math.round(((cx - rect.x) / rect.w) * fw), y = Math.round(((cy - rect.y) / rect.h) * fh);
  return [Math.min(fw, Math.max(0, x)), Math.min(fh, Math.max(0, y))];
}

const cross = (a: number[], b: number[], p: number[]) => (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]);
const onSeg = (a: number[], b: number[], p: number[]) => cross(a, b, p) === 0 && Math.min(a[0], b[0]) <= p[0] && p[0] <= Math.max(a[0], b[0]) && Math.min(a[1], b[1]) <= p[1] && p[1] <= Math.max(a[1], b[1]);
function segHit(p1: number[], p2: number[], q1: number[], q2: number[]): boolean {
  const s = (v: number) => Math.sign(v);
  if (s(cross(q1, q2, p1)) !== s(cross(q1, q2, p2)) && s(cross(p1, p2, q1)) !== s(cross(p1, p2, q2))) return true;
  return onSeg(q1, q2, p1) || onSeg(q1, q2, p2) || onSeg(p1, p2, q1) || onSeg(p1, p2, q2);
}
/** Quick client-side zone check mirroring the server rules (MG-DATA-001 5.6). The server stays authoritative. Returns a message id or null. */
export function polygonProblem(pts: number[][], w: number, h: number): string | null {
  if (pts.length < 3 || pts.length > 256) return "mg.refusal.zone_vertex_count";
  if (pts.some((p) => p[0] < 0 || p[0] > w || p[1] < 0 || p[1] > h || !Number.isInteger(p[0]) || !Number.isInteger(p[1]))) return "mg.refusal.zone_out_of_frame";
  if (new Set(pts.map((p) => `${p[0]},${p[1]}`)).size !== pts.length) return "mg.refusal.zone_duplicate_vertex";
  const n = pts.length;
  for (let i = 0; i < n; i++) if (cross(pts[i], pts[(i + 1) % n], pts[(i + 2) % n]) === 0) return "mg.refusal.zone_collinear_vertices";
  for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) {
    const adjacent = j === i + 1 || (i === 0 && j === n - 1);
    if (!adjacent && segHit(pts[i], pts[(i + 1) % n], pts[j], pts[(j + 1) % n])) return "mg.refusal.zone_self_intersection";
  }
  let a2 = 0;
  for (let i = 0; i < n; i++) a2 += pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1];
  return a2 === 0 ? "mg.refusal.zone_zero_area" : null;
}

export function captureDims(vw: number, vh: number, maxW = 640): { w: number; h: number } | null {
  if (!(vw > 0 && vh > 0)) return null;
  const w = Math.min(maxW, vw), h = Math.round((vh * w) / vw);
  return w >= 160 && h >= 160 ? { w, h } : null;
}

/** Client-side bound: never more than `max` frame uploads in flight; a skipped capture is counted, never queued. */
export function mayCapture(inFlight: number, max = 2): boolean {
  return inFlight < max;
}

/** End of the evaluated stream (microseconds): the largest interval end over all zone timelines, or 0. */
export function evaluationTotalUs(ev: Evaluation | null): number {
  if (!ev) return 0;
  return Math.max(0, ...Object.values(ev.zones).flatMap((z) => z.timeline.map((i) => i.end_us)));
}

/** The CURRENT risk to show for a feed: a feed that is not being observed (idle, stopped, failed) has none: its last value is history and must never read as «verde». */
export function currentRisk(feedState: string, last: RiskState): RiskState {
  return feedState === "playing" ? last : "UNKNOWN";   // paused, ended, stopped, idle, failed: nothing is being observed now (review 25 MG-STATE-025)
}

/** Provenance of an incident or alert shown in a list. A record restored from disk is NEVER shown as real inference: the backend marks it `loaded_from_store` because it was not reverified (review 25 MG-TRUST-025). */
export function incidentProvenance(x: { provenance: string; simulation: boolean; loaded_from_store?: boolean }): "real" | "synthetic" | "repeated" | "unverified" {
  if (x.loaded_from_store) return "unverified";
  if (x.simulation && x.provenance === "repeated_source_simulation") return "repeated";
  if (x.simulation || x.provenance === "synthetic_scripted") return "synthetic";
  return x.provenance === "repeated_source_simulation" ? "repeated" : "real";
}

/** Consistent default names: «<base> <n>» with the lowest number not already used by a source; a custom base name is kept. */
export function nextLabel(base: string, n: number, existing: { label: string }[]): string {
  const b = (base.trim() || t("mg.xd.lib.source_default")).slice(0, 40);
  const used = new Set(existing.map((f) => f.label));
  let k = n || 1;
  while (used.has(`${b} ${k}`)) k += 1;
  return `${b} ${k}`;
}

/** What the developer panel may say about a recorded OAF verdict. Authority decisions use STRICT booleans only: a string such as "false" (truthy in JavaScript) can never produce «accepted» (MG-OAF-026).
 *  Even a valid accepted record is only «registered»: this application does not verify the owner's authority. */
export type OafView = { kind: "none" | "invalid" | "not_accepted" | "claimed_accepted" };
export function oafAcceptanceView(o: { recorded?: unknown; validated?: unknown; release_accepted_claimed?: unknown; acceptance?: unknown; verdict?: unknown } | null | undefined): OafView {
  if (!o || o.recorded !== true) return { kind: o && (o as { reason?: unknown }).reason === "invalid_record" ? "invalid" : "none" };
  if (o.validated !== true || typeof o.release_accepted_claimed !== "boolean") return { kind: "invalid" };
  if (o.release_accepted_claimed === true && o.acceptance === "claimed_accepted" && o.verdict === "CONTRACT_PASS") return { kind: "claimed_accepted" };
  return { kind: "not_accepted" };
}

/** ONE canonical trust/provenance classification for every operator-facing alert surface (Overview, Alert Center, incident drawer, operator timeline): restored-from-disk is never «real»
 *  (cycle-26 MG-TRUST-026), a repeated-file concurrency alert is «repeated», a scripted one «synthetic». Acknowledging or resolving never changes it. */
export function alertProvenance(a: { simulation: boolean; loaded_from_store?: boolean; provenance?: string; source?: string | { provenance?: string } }): "real" | "synthetic" | "repeated" | "unverified" {
  return incidentProvenance({ provenance: a.provenance ?? (typeof a.source === "object" ? a.source?.provenance : undefined) ?? "", simulation: a.simulation, loaded_from_store: a.loaded_from_store });
}

/** Message id for the overlap phase of an episode or alert (MG-PROV-028). A missing or unrecognised value is `unknown` — it is NEVER read as «alone». */
export function overlapPhaseId(phase: unknown): string {
  const ok = ["alone", "from_launch", "before_overlap", "during_overlap", "between_overlaps", "after_overlap", "mixed"];
  return `mg.ov.phase.${typeof phase === "string" && ok.includes(phase) ? phase : "unknown"}`;
}
