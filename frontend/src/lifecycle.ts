// Capture lifecycle messages (MG-WCAM-019): numbered, acknowledged by the server, retried a BOUNDED number of times while the page is alive.
// The server stays the authority: it decides whether a declaration is admissible. Nothing here queues without bound or retries forever.

export type SendResult = { ok: boolean; status: number | null };           // status null = the request never completed (network)
export interface RetryPolicy { maxAttempts: number; backoffMs: number[] }

// A heartbeat is cumulative (it carries the highest tick count), so a lost one is also superseded by the next: one quick retry, then give up until the next beat.
export const HEARTBEAT_POLICY: RetryPolicy = { maxAttempts: 2, backoffMs: [200] };
// A stop is the one message that must arrive: 5 attempts, 300+600+1200+2400 ms of back-off = at most 4.5 s of waiting, the same message number every time (idempotent on the server).
export const STOP_POLICY: RetryPolicy = { maxAttempts: 5, backoffMs: [300, 600, 1200, 2400] };

export type Outcome = "acked" | "terminal" | "exhausted";
export interface Delivery { outcome: Outcome; attempts: number; last: SendResult }

const defaultSleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

/** 2xx = acknowledged. 4xx is terminal (a retry cannot change a refusal; 409 means the server already has a final state). Network failures and 5xx are retried within the policy. */
export async function sendReliably(send: () => Promise<SendResult>, policy: RetryPolicy, sleep: (ms: number) => Promise<void> = defaultSleep): Promise<Delivery> {
  let last: SendResult = { ok: false, status: null };
  for (let attempt = 1; attempt <= policy.maxAttempts; attempt++) {
    try { last = await send(); } catch { last = { ok: false, status: null }; }
    if (last.ok) return { outcome: "acked", attempts: attempt, last };
    if (last.status !== null && last.status >= 400 && last.status < 500) return { outcome: "terminal", attempts: attempt, last };
    if (attempt < policy.maxAttempts) await sleep(policy.backoffMs[Math.min(attempt - 1, policy.backoffMs.length - 1)] ?? 0);
  }
  return { outcome: "exhausted", attempts: policy.maxAttempts, last };
}

export const nextMsg = (n: number) => (n >= 2_000_000_000 ? n : n + 1);   // never exceeds the server's accepted range

// A stop that finds the model still inside a frame is answered 503 `session_worker_busy` after at most ~5 s. The client keeps the session and retries until a WALL-CLOCK deadline, never a number of
// attempts: total waiting is the deadline plus at most one server wait, whatever each attempt costs (cycle-23 MG-LIFE-023).
export const BUSY_DEADLINE_MS = 120_000;
export const BUSY_PAUSE_MS = 1_000;
export interface Clock { now(): number; sleep(ms: number): Promise<void>; after(ms: number, fn: () => void): () => void }   // `after` returns its cancel function
export const realClock: Clock = {
  now: () => Date.now(), sleep: defaultSleep,
  after: (ms, fn) => { const h = setTimeout(fn, ms); return () => clearTimeout(h); },
};

/** Thrown when ONE stop request does not settle inside what is left of the wall-clock deadline (cycle-24 MG-LIFE-024). The message carries the id the UI maps to Spanish text. */
export class StopDeadlineExceeded extends Error {
  constructor() { super('stop deadline exceeded {"detail":{"msg_id":"mg.client.stop_deadline"}}'); this.name = "StopDeadlineExceeded"; }
}

/** Retries `attempt` while the server says «busy», for at most `deadlineMs` of WALL-CLOCK time in total. Every attempt receives an AbortSignal and a timer sized to the time that is LEFT: a request that
 *  never settles (a stalled network, a fetch without timeout) is aborted and abandoned at the deadline instead of holding the UI in finalization forever. */
export async function retryBusy<T>(attempt: (signal: AbortSignal) => Promise<T>, isBusy: (e: unknown) => boolean, clock: Clock = realClock, deadlineMs = BUSY_DEADLINE_MS, pauseMs = BUSY_PAUSE_MS): Promise<T> {
  const start = clock.now();
  for (;;) {
    const remaining = deadlineMs - (clock.now() - start);
    if (remaining <= 0) throw new StopDeadlineExceeded();
    const ac = new AbortController();
    let cancel = () => undefined as void;
    // reject FIRST: the deadline, not the abort it causes, is the outcome
    const timeout = new Promise<never>((_, reject) => { cancel = clock.after(remaining, () => { reject(new StopDeadlineExceeded()); ac.abort(); }); });
    try { return await Promise.race([attempt(ac.signal), timeout]); }
    catch (e) {
      if (e instanceof StopDeadlineExceeded || !isBusy(e) || clock.now() - start + pauseMs >= deadlineMs) throw e;   // deadline hit, not busy, or the next pause would pass the deadline: give up with the real error
      await clock.sleep(pauseMs);
    } finally { cancel(); }
  }
}
