import type { Evaluation, Frame, Zone } from "./lib";

export interface VideoInfo { video_id: string; filename: string; sha256: string; bytes: number;
  media: { native_width: number; native_height: number; nominal_fps: number; duration_s: number; codec_fourcc: string } }
export interface ModelInfo { model_id: string; architecture: string; weights_sha256: string; source_url: string; verified: boolean; error?: string;
  license_evidence: Record<string, string> }
export interface Job { job_id: string; status: "queued" | "running" | "done" | "failed" | "cancelled"; done: number; total: number; run_id: string | null; error: string | null }
export interface RunBundle {
  observation_set: { source_binding: { width: number; height: number; frame_count: number }; producer: { weights_sha256: string; model_id: string; params: Record<string, number | string> };
    frames: Frame[]; origin: string };
  source: { frame_table: { pts_us: number[] } };
  provenance: { packages: Record<string, string | null>; device: string; python: string; macos: string };
  manifest: { run_id: string; limitations: string[] };
  trust: "session" | "verified" | "unverified";
}
export interface VerifyReport { ok: boolean; deep: boolean; visual_verification?: string; retention?: string; checks: { id: string; ok: boolean; detail: string }[] }

const token = (): string => document.querySelector<HTMLMetaElement>('meta[name="mg-session-token"]')?.content ?? "";

async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const r = await fetch(url, { ...init, headers: { ...(init?.headers as Record<string, string> | undefined), "X-MG-Token": token() } });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}
export const api = {
  videos: () => j<VideoInfo[]>("/api/videos"),
  model: () => j<ModelInfo>("/api/model"),
  scenarios: () => j<import("./simulations/scenarioPresent").ScnList>("/api/scenarios"),
  scenario: (id: string) => j<import("./simulations/scenarioPresent").Scenario>(`/api/scenarios/${encodeURIComponent(id)}`),
  runs: () => j<{ run_id: string }[]>("/api/runs"),
  run: (id: string) => j<RunBundle>(`/api/runs/${id}`),
  analyze: (vid: string, stride: number, threshold: number) =>
    j<{ job_id: string }>(`/api/videos/${vid}/analyze`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ stride, threshold }) }),
  job: (id: string) => j<Job>(`/api/jobs/${id}`),
  cancel: (id: string) => j<{ ok: boolean }>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  verify: (id: string) => j<VerifyReport>(`/api/runs/${id}/verify`),
  videoUrl: (vid: string) => `/api/videos/${vid}/file`,
  frameUrl: (run: string, idx: number) => `/api/runs/${run}/frames/${idx}`,
  exportUrl: (run: string) => `/api/runs/${run}/export`,
};

export interface ZoneIn { zone_id: string; kind: "restricted" | "monitored"; polygon: number[][]; name?: string }
export interface RiskReply { evaluation: Evaluation; trust: "session" | "verified" | "unverified"; zones: { revision: number; zones: Zone[] } }
export interface LiveView {
  session_id: string; state: string; counters: { received: number; analyzed: number; dropped_queue_overflow: number; dropped_stale: number; adapter_error: number; rejected: number };
  queue_depth: number; queue_max: number; width: number; height: number; interval_ms: number; run_id: string | null; closed_reason: string | null; error: string | null;
  latest: { seq: number | null; capture_us: number | null; objects: { label: string; bbox: { x: number; y: number; w: number; h: number }; permille: number | null; track: string | null }[] };
  risk: Record<string, { state: "GREEN" | "YELLOW" | "RED" | "UNKNOWN"; pending: { rule_id: string }[]; causes: string[]; t_us: number | null }>;
  latency_ms: { p50: number | null; max: number | null }; events: Record<string, import("./lib").RiskEvent[]>; zones: Zone[];
  capture: { origin: string; device_label: string }; retention: "frames" | "metadata_only";
}
export interface SessionBundle { observation_set: RunBundle["observation_set"]; source: { frame_table: { pts_us: number[] }; kind: string; samples: { status: string; reason: string | null }[] };
  provenance: RunBundle["provenance"] & { capture?: { declared_by: string; origin: string; physical_camera_verified_by_server: boolean } };
  manifest: { run_id: string; limitations: string[]; closed_reason: string | null; counters: Record<string, number>; live_stats?: Record<string, unknown> }; trust: RunBundle["trust"]; retention: "frames" | "metadata_only" | "media_deleted" }

export interface SessionRow { session_id: string; created_utc: string; frames_analyzed: number; frame_count: number; closed_reason: string | null; trust: string;
  retention: "frames" | "metadata_only" | "media_deleted"; source_origin: string | null; bytes: number }
export interface EventRow { event_id: string; source_id: string; kind: string; origin: string; synthetic: boolean; zone_id: string; severity: "YELLOW" | "RED"; start_us: number;
  trust: string; nature: "synthetic" | "human_annotated" | "real_inference" | "unverified_claim"; end_us: number | null; closed_reason: string | null; source_created_utc: string | null; rule_id: string }
export interface DeleteResult { requested: number; deleted: number; results: { session_id: string; status: "deleted" | "refused" | "missing" | "failed"; reason?: string; files?: number; bytes?: number }[] }
export interface Overview { privacy_default: string; active_session: LiveView | null; sources: { runs: number; sessions: number; simulations: number; annotated: number; unverified: number };
  events: { real: { RED: number; YELLOW: number }; synthetic: { RED: number; YELLOW: number }; unverified: { RED: number; YELLOW: number }; human_annotated: { RED: number; YELLOW: number } }; engine: { risk_engine: string; semantics_version: number; app_version: string; tracker: string };
  model: { model_id: string; architecture: string; sha256: string } }

const jb = <T,>(url: string, method: string, body: unknown) => j<T>(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
export const apiVs02 = {
  simulate: (baseRunId: string) => jb<{ run_id: string; origin: string; zone_id: string }>("/api/simulations/person-vehicle", "POST", { base_run_id: baseRunId }),
  overview: () => j<Overview>("/api/overview"),
  events: (q: string = "") => j<{ events: EventRow[]; total: number }>(`/api/events${q ? "?" + q : ""}`),
  runsList: () => j<{ run_id: string; origin: string; trust: string; created_utc: string }[]>("/api/runs"),
  sessionsList: () => j<SessionRow[]>("/api/sessions"),
  deleteSessions: (body: { ids?: string[]; all?: boolean; expected_count: number }) =>
    jb<DeleteResult>("/api/sessions/delete", "POST", { ...body, confirm: "ELIMINAR" }),
  deleteMedia: (sid: string, expected_files: number) => jb<{ files_deleted: number; bytes_deleted: number; status: string; residue?: string }>(`/api/sessions/${sid}/media/delete`, "POST", { confirm: "ELIMINAR", expected_files }),
  health: () => j<{ status: string; version: string; network_guard: boolean }>("/api/health"),
  build: () => j<BuildInfo>("/api/build"),
  zones: (kind: "runs" | "sessions", id: string) => j<{ revision: number; zones: Zone[] }>(`/api/${kind}/${id}/zones`),
  putZones: (kind: "runs" | "sessions", id: string, zones: ZoneIn[], expectedRevision: number) => jb<{ revision: number; zones: Zone[] }>(`/api/${kind}/${id}/zones`, "PUT", { zones, expected_revision: expectedRevision }),
  risk: (kind: "runs" | "sessions", id: string) => j<RiskReply>(`/api/${kind}/${id}/risk`),
  riskExportUrl: (kind: "runs" | "sessions", id: string) => `/api/${kind}/${id}/risk/export`,
  startSession: (body: unknown) => jb<{ session_id: string }>("/api/webcam/sessions", "POST", body),
  sessionView: (sid: string) => j<LiveView>(`/api/webcam/sessions/${sid}`),
  /** Heartbeat (numbered): how many capture ticks have been issued, so a loss after the last accepted frame is recorded even if the client later vanishes. Resolves with the HTTP outcome (status null = network failure). */
  watermark: async (sid: string, nextSeq: number, msgSeq: number): Promise<{ ok: boolean; status: number | null }> => {
    try {
      const r = await fetch(`/api/webcam/sessions/${sid}/watermark`, { method: "POST", headers: { "X-MG-Token": token(), "X-MG-Next-Seq": String(nextSeq), "X-MG-Msg-Seq": String(msgSeq) } });
      return { ok: r.ok, status: r.status };
    } catch { return { ok: false, status: null }; }
  },
  stopSession: (sid: string, reason: "client_stop" | "device_ended" = "client_stop", nextSeq?: number, msgSeq?: number, signal?: AbortSignal) =>
    j<{ run_id: string | null; state: string; closed_reason: string | null; ack?: number | null }>(`/api/webcam/sessions/${sid}/stop?reason=${reason}`,
      { method: "POST", signal, headers: { ...(nextSeq === undefined ? {} : { "X-MG-Next-Seq": String(nextSeq) }), ...(msgSeq === undefined ? {} : { "X-MG-Msg-Seq": String(msgSeq) }) } } as RequestInit),
  /** Resolves true only for an accepted frame (HTTP 202). Every capture tick consumes a sequence number, so a failure needs no separate bookkeeping: the server derives all loss from gaps. */
  postFrame: async (sid: string, seq: number, captureUs: number, blob: Blob): Promise<boolean> => {
    try {
      const r = await fetch(`/api/webcam/sessions/${sid}/frames`, { method: "POST", body: blob, headers: { "X-MG-Token": token(), "X-MG-Seq": String(seq), "X-MG-Capture-Us": String(captureUs), "Content-Type": "image/jpeg" } });
      return r.ok;
    } catch { return false; }
  },
  sessions: () => j<{ session_id: string; created_utc: string; frames_analyzed: number; frame_count: number; closed_reason: string | null; trust: string }[]>("/api/sessions"),
  session: (sid: string) => j<SessionBundle>(`/api/sessions/${sid}`),
  verifySession: (sid: string) => j<VerifyReport>(`/api/sessions/${sid}/verify`),
  sessionFrameUrl: (sid: string, idx: number) => `/api/sessions/${sid}/frames/${idx}`,
};

// ------------------------------------------------------------------ MG-VERTICAL-02: multi-feed monitoring, alerts, dashboards
export interface BuildInfo { commit: string | null; commit_known: boolean; tree_clean: boolean | null; checkout_moved_since_start: boolean | null; measured_utc: string; source: string;
  oaf: { recorded: boolean; reason?: string; invalid_why?: string; validated?: boolean; verdict?: string; contract_id?: string; contract_revision?: number; contract_digest?: string; verdict_digest?: string;
    release_accepted_claimed?: boolean; acceptance?: "not_accepted" | "claimed_accepted"; authority_verified_by_app?: boolean; owner_authority_claimed_enforced?: boolean; bound_to_commit?: string; note?: string } }
export type RiskStateName = "GREEN" | "YELLOW" | "RED" | "UNKNOWN";
/** Where an episode started relative to the KNOWN overlap of its run with another feed of the same file (MG-PROV-028). `unknown` = no interval evidence; `mixed` = an alert that merged episodes of different phases. */
export type OverlapPhase = "alone" | "from_launch" | "before_overlap" | "during_overlap" | "between_overlaps" | "after_overlap" | "unknown" | "mixed";
export interface OverlapDoc { launch_repeated: boolean | null; intervals: { from_after_us: number | null; until_before_us: number | null }[]; evidence: "sample_time_bounds"; truncated: boolean }
export interface FeedEvent { event_id: string; zone_id: string; severity: "YELLOW" | "RED"; start_us: number; end_us: number | null; open: boolean; tracks: string[]; closed_reason: string | null; closed_by?: string; overlap_phase?: OverlapPhase }
export interface FeedView {
  feed_id: string; label: string; state: "idle" | "playing" | "paused" | "ended" | "failed" | "stopped"; error: string | null; error_detail: string | null; source_retries: number; closed_reason: string | null; result_id: string | null;
  source: { kind: string; video_id: string; filename: string; repeated_source: boolean; provenance: "real_inference" | "repeated_source_simulation"; physical_camera: boolean; note: string };
  config: { sample_fps: number; interval_ms: number; threshold_permille: number; mode: "realtime" | "throttled"; speed: number; loop: boolean };
  counters: { decoded: number; offered: number; skipped_by_sampling: number; analyzed: number; dropped_queue_overflow: number; dropped_stale: number; adapter_error: number; dropped_stopped: number; detections: number };
  model_loading: boolean; queue_depth: number; queue_max: number; max_queue_depth: number; fps: number | null;
  latency_ms: { p50: number | null; p95: number | null; max: number | null }; queue_wait_ms: { p50: number | null; p95: number | null };
  latest: { index: number | null; t_us: number | null; objects: { label: string; bbox: { x: number; y: number; w: number; h: number }; permille: number | null; track: string | null }[] };
  risk: Record<string, { state: RiskStateName; pending: { rule_id: string }[]; causes: string[]; t_us: number | null }>;
  /** CURRENT risk as the server publishes it (UNKNOWN unless the feed is playing with a fresh sample) vs the LAST OBSERVED value (history). */
  launch_repeated?: boolean | null; overlap?: OverlapDoc; shared_now?: boolean; shared_during_run?: boolean;
  risk_current?: Record<string, { state: RiskStateName; reason: string | null }>; risk_state?: RiskStateName; unknown_reason?: string | null; last_observed_risk?: RiskStateName | null;
  observation?: { fresh: boolean; age_ms: number | null; stale_after_ms: number };
  zones: import("./lib").Zone[]; zones_revision: number; events: FeedEvent[]; latest_event: FeedEvent | null; lifecycle_last?: { kind: string; at_utc: string; reason: string | null } | null; tracks_unique: Record<string, number>; class_hist: Record<string, number>; passes: number; source_time_us: number | null;
}
export interface FeedsSnapshot { feeds: FeedView[]; active: number; active_limit: number; validated_feeds: number; max_feeds: number;
  scheduler: { alive: boolean; served: number; max_queue_wait_ms: number; detector_error: string | null; aggregate_fps: number; policy: string } }
export interface FeedCreateBody { video_id: string; label: string; sample_fps: number; threshold: number; mode: "realtime" | "throttled"; speed: number; loop: boolean; zones: ZoneIn[] }
export interface AlertRec {
  alert_id: string; kind: "risk_event" | "diagnostic"; event_id: string | null; feed_id: string | null; zone_id: string; severity: "RED" | "YELLOW" | "UNKNOWN"; priority: "high" | "warning" | "diagnostic";
  created_utc: string; status: "new" | "active" | "acknowledged" | "resolved"; acknowledged_utc: string | null; resolved_utc: string | null; resolved_by: string | null; causes: string[]; suppressed_count: number;
  reopened_count?: number; loaded_from_store?: boolean; overlap_phase?: OverlapPhase | null; simulation: boolean; rev: number; delivery_state: "pending" | "delivered" | "in_app_only" | "failed";
  source: { source_id: string; kind: string; label: string; provenance: "real_inference" | "repeated_source_simulation" | "synthetic_scripted"; incident_class: string; physical_camera: boolean };
  delivery: { in_app: { status: string }; browser: { status: string; detail: string | null }; sound: { status: string; detail: string | null } };
  event: { start_us: number; end_us: number | null; open: boolean; rule_id: string; closed_reason?: string | null } | null;
  occurrences?: { event_id: string; open: boolean; source_id: string | null }[]; occurrence_total?: number; pinned_open?: boolean;
}
export interface IncidentTimelineEntry { kind: string; at_us?: number; end_us?: number; at_utc?: string; severity?: string; rule_id?: string | null; event_id?: string | null; cause?: string | null;
  closed_reason?: string | null; right_censored?: boolean; channel?: string; by?: string | null; overlap_phase?: string; synthetic: boolean; restored: boolean;
  /** V07: recorded lifecycle / observation-edge facts */ recorded?: boolean; source_time_us?: number | null; reason?: string | null; error?: string | null; from?: string | null; to?: string | null; zone_id?: string; count?: number | null; scope?: string | null; seq?: number }
export interface FeedLifecycleEvent { seq: number; kind: string; group: "lifecycle" | "observed"; at_utc: string; t_us: number | null; run_uid: string; reason?: string | null; error?: string | null; zone_id?: string; from?: string | null; to?: string | null; count?: number | null }
export interface FeedLifecycle { run_uid: string; events: FeedLifecycleEvent[]; truncated: boolean; recorded: boolean; reason: string | null }
/** MG-PRODUCT-VERTICAL-06: the incident record exactly as the server projects it (and as the JSON export contains it). */
export interface Incident {
  schema: string; incident_id: string; generated_utc: string; classification: "synthetic" | "restored" | "repeated_source_simulation" | "live_feed_metadata";
  identity: { alert_id: string; event_id: string | null; zone_id: string; source_id: string; source_kind: string; source_label: string; feed_id: string | null; run_id: string | null; physical_camera: boolean };
  provenance: { provenance: string; simulation: boolean; restored_from_disk: boolean; repeated_source: boolean; overlap_phase: string | null; provider: { kind: string; model_id: string | null };
    feed: { feed_id: string; state: string; observation_fresh: boolean; last_observation_age_ms: number | null; shared_now: boolean; launch_repeated: boolean | null; shared_during_run: boolean; current_risk: string; unknown_reason: string | null } | null };
  risk: { severity: string; rule_id: string | null; causes: string[]; semantics_version: string | null; engine_version: string | null; versions_basis: string; decided_by: string; ai_observations_decide_severity: boolean };
  observed: { start_us: number | null; end_us: number | null; open: boolean | null; closed_reason: string | null; right_censored: boolean | null; time_basis: string };
  unknown_intervals: { available: boolean; intervals: { start_us: number; end_us: number; cause: string | null }[]; truncated: boolean; reason: string | null };
  alert: { status: string; created_utc: string; acknowledged_utc: string | null; resolved_utc: string | null; resolved_by: string | null; merged: number; reopened_count: number };
  delivery: { state: string; channels: Record<string, { status: string; at: string | null; detail: string | null }>; note: string };
  evidence: { availability: string; images_included: boolean; stored_run: string | null; digest_sha256: string | null; local_consistency: "pass" | "fail" | "not_checked"; restored: boolean; synthetic: boolean;
    authenticity: string; conflicts: string[]; reason: string };
  timeline: { observation: IncidentTimelineEntry[]; processing: IncidentTimelineEntry[]; unsupported_kinds: string[];
    lifecycle: { recorded: boolean; run_uid: string | null; truncated: boolean; reason: string | null }; time_resolution: { alert: string; lifecycle: string; order_within_a_second: string } };
  completeness: { source_lifecycle: "recorded" | "not_recorded"; observation_edges: "recorded" | "not_recorded"; unknown_intervals: "available" | "not_available"; stored_run: string; images: string; complete: boolean };
  limitations: string[];
}
export interface AlertSettings { red: { browser: boolean; sound: boolean; cooldown_s: number }; yellow: { browser: boolean; sound: boolean; cooldown_s: number }; unknown: { browser: boolean; sound: boolean; cooldown_s: number; after_s: number }; webhook: { enabled: false } }
export interface AlertSummary { total: number; open: number; by_status: Record<string, number>; by_severity: Record<string, number>; by_provenance: Record<string, number>; by_delivery_state: Record<string, number>;
  active_unacknowledged: number; acknowledged_open: number; merged_suppressed: number; loaded_from_disk_unverified?: number; rejected_on_load?: number; store_status?: string;
  capacity?: { soft: number; hard: number; stored: number; overflow_dropped: number; degraded: boolean; open_drills: number } }
export interface OperatorOverview {
  generated_utc: string; active_feeds: number; active_limit: number; validated_feeds: number;
  feeds: { feed_id: string; label: string; state: string; risk: RiskStateName; unknown_reason?: string | null; last_observed_risk: RiskStateName | null; zones: Record<string, RiskStateName>; unknown_zones: string[]; fps: number | null; latency_ms: { p50: number | null; p95: number | null };
    counters: Record<string, number>; events: { RED: number; YELLOW: number }; provenance: string; repeated_source: boolean; latest_event: FeedEvent | null; error: string | null;
    source_id: string; source_kind: string; filename: string; observation: { fresh: boolean; age_ms: number | null }; last_observation_source_us: number | null; counts_latest: { person: number; vehicle: number } | null;
    zone_ids: string[]; duplicate: "none" | "inactive" | "active_overlap" | "historical_repeated" | "unknown"; incidents_open: number;
    lifecycle_last: { kind: string; at_utc: string; reason: string | null } | null }[];
  detector: { model_id: string | null; architecture: string | null };
  counts_kept_apart: { per_frame_detections: number; unique_tracks: number; risk_events_live: { RED: number; YELLOW: number }; risk_events_from_simulated_sources: { RED: number; YELLOW: number };
    risk_events_in_saved_feed_history_real: { RED: number; YELLOW: number }; risk_events_in_saved_feed_history_unverified: { RED: number; YELLOW: number };
    notification_deliveries: { in_app_stored: number; in_app_displayed: number; browser: number; sound: number; failed: number; loaded_from_disk_unverified: number }; note: string };
  events_per_source: Record<string, { RED: number; YELLOW: number; provenance: string }>; alerts: AlertSummary;
  recent_timeline: { alert_id: string; created_utc: string; severity: string; kind: string; zone_id: string; status: string; simulation: boolean; loaded_from_store?: boolean; delivery_state: string; source: string; provenance: string }[];
  trend: { minutes: number; bucket_minutes: number; has_data: boolean; basis: string; simulated_total: number; restored_total: number; buckets: { from_utc: string; RED: number; YELLOW: number; UNKNOWN: number; simulated: number; restored: number }[] };
  sessions_retention: { frames: number; metadata_only: number; media_deleted: number; total: number }; saved_feed_runs: number;
  health: { scheduler_alive: boolean; scheduler_detector_error: string | null; feeds_failed: number; model_loaded: boolean; alert_errors: number; resources: Resources };
}
export interface Resources { measured: boolean; at_utc: string; process_rss_mb: number | null; host_pressure_level: number | null; host_free_percent: number | null; swap_used_mb: number | null; decoder_service_mb: number | null }
export interface DeveloperOverview {
  generated_utc: string; model: { model_id?: string; architecture?: string; weights_sha256_prefix?: string; loaded: boolean; device?: string | null; resolution?: number | null; error?: string };
  versions: { app: string; risk_kernel_engine: string; risk_kernel_semantics: number; tracker: string; alert_api: string; feed_api: string; limits: { max_feeds: number; validated_feeds: number; feed_queue_max: number } };
  scheduler: FeedsSnapshot["scheduler"]; aggregate_inference_fps: number; resources: Resources; metrics_policy: string;
  feeds: { feed_id: string; state: string; source: FeedView["source"]; frames: Record<string, number>; inference_fps: number | null; latency_ms: { p50: number | null; p95: number | null; max: number | null }; queue_wait_ms: { p50: number | null; p95: number | null };
    queue_depth: number; queue_max: number; max_queue_depth: number; class_distribution: Record<string, number>; tracks_unique: Record<string, number>; tracking_discontinuities: number; error: string | null; trust: string }[];
  webcam_live_sessions: { session_id: string; counters: Record<string, number>; accounting: Record<string, unknown> | null; queue_depth: number; retention: string }[];
  alerts: AlertSummary; acceptance_state: { available?: boolean; commit?: string; generated_utc?: string; note: string; gate?: string; totals?: Record<string, number>; modules?: { module: string; collected: number; passed: number | null; exit: number | null }[]; e2e?: Record<string, { pass: number; fail: number }>; vitest?: number | null };
}

export const apiV2 = {
  feeds: () => j<FeedsSnapshot>("/api/feeds"),
  feed: (id: string) => j<FeedView>(`/api/feeds/${id}`),
  createFeed: (b: FeedCreateBody) => jb<FeedView>("/api/feeds", "POST", b),
  feedAction: (id: string, a: "start" | "pause" | "resume" | "stop") => j<FeedView>(`/api/feeds/${id}/${a}`, { method: "POST" }),
  removeFeed: (id: string) => j<{ removed: string }>(`/api/feeds/${id}`, { method: "DELETE" }),
  putFeedZones: (id: string, zones: ZoneIn[], expectedRevision: number) => jb<FeedView>(`/api/feeds/${id}/zones`, "PUT", { zones, expected_revision: expectedRevision }),
  setCapacity: (limit: number, ack: boolean) => jb<{ active_limit: number }>("/api/feeds/capacity", "POST", { limit, acknowledge_unvalidated: ack }),
  previewUrl: (id: string, n: number) => `/api/feeds/${id}/preview.jpg?n=${n}`,
  feedHistory: () => j<{ runs: { id: string; label: string; ended_utc: string; closed_reason: string | null; counters: Record<string, number>; events: { severity: string }[]; source: FeedView["source"] }[]; retention: { mode: string } }>("/api/feeds/history"),
  deleteFeedHistory: (ids: string[]) => jb<{ deleted: number }>("/api/feeds/history/delete", "POST", { ids, confirm: "ELIMINAR", expected_count: ids.length }),
  alerts: (q = "") => j<{ api_version: string; rev: number; alerts: AlertRec[]; total_matching: number; more: boolean; next_after: number | null; count: number }>(`/api/alerts${q ? "?" + q : ""}`),
  incident: (id: string) => j<Incident>(`/api/incidents/${id}`),
  explanation: (id: string) => j<Explanation>(`/api/incidents/${id}/explanation`),
  providers: () => j<ProvidersDoc>("/api/providers"),
  externalResults: () => j<ExternalList>("/api/external-results"),
  externalResult: (id: string) => j<ExternalReview>(`/api/external-results/${id}`),
  externalImageUrl: (id: string) => `/api/external-results/${id}/image`,
  externalDemo: () => j<ExternalReview>("/api/external-results/demo", { method: "POST" }),
  importExternal: (b: { manifest_b64: string; result_b64: string; image_b64?: string }) => jb<ExternalReview>("/api/external-results", "POST", b),
  feedLifecycle: (fid: string) => j<FeedLifecycle>(`/api/feeds/${fid}/lifecycle`),
  incidentExport: async (id: string) => { const r = await fetch(`/api/incidents/${id}/export`, { headers: { "X-MG-Token": token() } }); if (!r.ok) throw new Error(`${r.status} ${await r.text()}`); return r.text(); },
  alertIds: () => j<{ ids: string[] }>("/api/alerts/ids"),
  alertsSummary: () => j<AlertSummary>("/api/alerts/summary"),
  alertSeen: (ids: string[]) => jb<{ marked: number }>("/api/alerts/seen", "POST", { ids }),
  alertAck: (id: string) => j<AlertRec>(`/api/alerts/${id}/ack`, { method: "POST" }),
  alertResolve: (id: string) => j<AlertRec>(`/api/alerts/${id}/resolve`, { method: "POST" }),
  alertDelivery: (id: string, channel: "browser" | "sound", status: "delivered" | "denied" | "failed" | "unsupported", detail?: string) =>
    jb<AlertRec>(`/api/alerts/${id}/delivery`, "POST", { channel, status, detail }),
  alertSettings: () => j<{ settings: AlertSettings; webhook_contract: { status: string; delivery_enabled: boolean; destination_rules: string[]; provider_limits: string } }>("/api/alerts/settings"),
  putAlertSettings: (s: AlertSettings) => jb<{ settings: AlertSettings }>("/api/alerts/settings", "PUT", s),
  overviewMonitor: (minutes: number, bucket: number) => j<OperatorOverview>(`/api/monitor/overview?minutes=${minutes}&bucket=${bucket}`),
  developer: () => j<DeveloperOverview>("/api/monitor/developer"),
};

/** Provider status and the SIMULATED explanation of an incident (backend `providers.py`, `explain.py`). Neither can change risk, severity or spending. */
export interface ProviderRow { provider_id: string; kind: string; label_id: string; states: string[]; real_inference: boolean; external_call_possible: boolean; note_id: string; model_id?: string | null; weights_sha256?: string | null; weights_verified?: boolean | null; detail?: string | null; accuracy_validated?: boolean }
export interface ProvidersDoc { schema: string; providers: ProviderRow[]; states_vocabulary: string[];
  cost: { real_requests_reserved: number; max_real_requests: number; authorized_external_calls: number; authorized_external_spend_usd: string; real_transport: string; kill_switch_engaged: boolean; ui_can_enable_spending: boolean; zero_charge_guaranteed: boolean };
  invariants: { external_calls_possible: boolean; credentials_read: boolean; environment_consulted_for_providers: boolean } }
export interface Explanation { kind: "SIMULATED_EXPLANATION"; status: "ACCEPTED" | "REJECTED"; simulated: true; real_muse_call: false; severity: string; decided_by: string; validation: string; codes?: string[];
  report?: { resumen: string; explicacion_decision: string; hechos_observados: { texto: string; ref: string }[]; incertidumbres: string[]; pasos_revision_humana: string[]; video_inspected: false } }

/** MG-EVIDENCE-10: review record of an EXTERNAL provider result (backend `meta_import.py`). It has NO class and NO severity member by construction. */
export interface ExternalObject { object_id: number; box: { x1: number; y1: number; x2: number; y2: number }; provider_score_permille: number | null; provider_score_kind: string;
  class_assigned: null; label_state: "UNCLASSIFIED"; mask: { status: string; reason: string; rows: number; cols: number; encoding: string; payload_chars: number } }
export interface ExternalReview { schema: string; import_id: string; origin: "EXTERNAL_MANUAL_PROVIDER_RESULT"; integrity: "VERIFIED" | "STORED_COPY_MODIFIED"; newly_imported?: boolean; has_image?: boolean;
  in_app_inference?: false; live_verified?: false; provenance_class?: string; provider?: string; model?: string; imported_utc?: string | null; digest?: string;
  verification?: { state: string; authenticity: string; result_sha256: string; result_bytes: number; manifest_sha256: string; input_image_sha256: string; image_linkage: string; request_body_sha256: string | null };
  capture?: { captured_utc: string; declared_by: string }; source_image?: { width: number; height: number }; concept_requested?: string;
  parse?: { state: string }; grammar?: { profile: string; status: string; official_provider_schema: false; events: number }; objects?: ExternalObject[]; risk?: { assessed: false }; canonical_effect?: "NONE"; limitations?: string[] }
export interface ExternalList { schema: string; origin: string; results: { import_id: string; integrity: string; provenance_class: string | null; verification_state: string | null; objects: number; imported_utc: string | null }[] }
