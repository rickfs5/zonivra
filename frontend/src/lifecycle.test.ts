import { describe, expect, it } from "vitest";
import { BUSY_DEADLINE_MS, HEARTBEAT_POLICY, STOP_POLICY, StopDeadlineExceeded, nextMsg, realClock, retryBusy, sendReliably, type Clock } from "./lifecycle";

const noSleep = async () => undefined;

describe("capture lifecycle delivery (MG-WCAM-019)", () => {
  it("acknowledges on the first 2xx", async () => {
    const d = await sendReliably(async () => ({ ok: true, status: 202 }), STOP_POLICY, noSleep);
    expect(d).toMatchObject({ outcome: "acked", attempts: 1 });
  });
  it("retries network failures and 5xx a bounded number of times, then reports exhaustion", async () => {
    let calls = 0; const waits: number[] = [];
    const d = await sendReliably(async () => { calls++; return { ok: false, status: calls % 2 ? null : 503 }; }, STOP_POLICY, async (ms) => { waits.push(ms); });
    expect(d.outcome).toBe("exhausted"); expect(calls).toBe(5); expect(waits).toEqual([300, 600, 1200, 2400]);
    expect(waits.reduce((a, b) => a + b, 0)).toBeLessThanOrEqual(4500);
  });
  it("recovers when a later attempt succeeds, with the SAME send function (idempotent message)", async () => {
    let calls = 0;
    const d = await sendReliably(async () => ({ ok: ++calls === 3, status: calls === 3 ? 200 : null }), STOP_POLICY, noSleep);
    expect(d).toMatchObject({ outcome: "acked", attempts: 3 });
  });
  it("never retries a 4xx refusal", async () => {
    let calls = 0;
    const d = await sendReliably(async () => { calls++; return { ok: false, status: 409 }; }, STOP_POLICY, noSleep);
    expect(d.outcome).toBe("terminal"); expect(calls).toBe(1);
  });
  it("treats a thrown send as a network failure and stays bounded", async () => {
    let calls = 0;
    const d = await sendReliably(async () => { calls++; throw new Error("offline"); }, HEARTBEAT_POLICY, noSleep);
    expect(d.outcome).toBe("exhausted"); expect(calls).toBe(HEARTBEAT_POLICY.maxAttempts);
  });
  it("keeps message numbers inside the server's range", () => {
    expect(nextMsg(0)).toBe(1); expect(nextMsg(2_000_000_000)).toBe(2_000_000_000);
  });
});

describe("busy-stop retry is bounded by wall-clock time (MG-LIFE-023)", () => {
  const fake = (attemptCost: number) => { const c = { t: 0, sleeps: 0 }; const clock: Clock = { now: () => c.t, sleep: async (ms) => { c.t += ms; c.sleeps++; }, after: () => () => undefined }; return { c, clock, cost: attemptCost }; };
  const busy = new Error('503 {"detail":{"msg_id":"mg.refusal.session_worker_busy"}}');
  const isBusy = (e: unknown) => /session_worker_busy/.test(String(e));

  it("gives up near the deadline whatever each attempt costs", async () => {
    for (const cost of [0, 1_000, 5_000, 30_000]) {
      const { c, clock } = fake(cost);
      let n = 0;
      await expect(retryBusy(async () => { n++; c.t += cost; throw busy; }, isBusy, clock)).rejects.toBe(busy);
      expect(c.t).toBeLessThanOrEqual(BUSY_DEADLINE_MS + 1_000 + cost);   // deadline + one pause + one attempt: never "121 attempts x 120 s"
      expect(n).toBeGreaterThan(0);
    }
  });
  it("a permanently blocked detector with the server's 5 s wait ends in about two minutes, not hours", async () => {
    const { c, clock } = fake(5_000);
    await expect(retryBusy(async () => { c.t += 5_000; throw busy; }, isBusy, clock)).rejects.toBeDefined();
    expect(c.t).toBeLessThan(130_000);
  });
  it("returns as soon as the server accepts, and rethrows anything that is not busy at once", async () => {
    const { c, clock } = fake(0);
    let n = 0;
    expect(await retryBusy(async () => { if (++n < 3) throw busy; return "ok"; }, isBusy, clock)).toBe("ok");
    expect(n).toBe(3);
    const other = new Error("500 something else");
    await expect(retryBusy(async () => { throw other; }, isBusy, clock)).rejects.toBe(other);
    expect(c.sleeps).toBe(2);
  });
});

describe("a stop request that never settles is cancelled at the wall-clock deadline (MG-LIFE-024)", () => {
  const isBusy = (e: unknown) => /session_worker_busy/.test(String(e));

  it("an attempt that never resolves ends with StopDeadlineExceeded and its AbortSignal fired (virtual clock)", async () => {
    const c = { t: 0 }; const fired: number[] = [];
    const clock: Clock = { now: () => c.t, sleep: async (ms) => { c.t += ms; }, after: (ms, fn) => { fired.push(ms); c.t += ms; queueMicrotask(fn); return () => undefined; } };
    let sig: AbortSignal | undefined;
    await expect(retryBusy((signal) => { sig = signal; return new Promise<never>(() => undefined); }, isBusy, clock)).rejects.toBeInstanceOf(StopDeadlineExceeded);
    expect(sig?.aborted).toBe(true);
    expect(fired).toEqual([BUSY_DEADLINE_MS]);                       // the first attempt may use the whole deadline, never more
  });
  it("the time given to each attempt is only what REMAINS of the deadline", async () => {
    const c = { t: 0 }; const budgets: number[] = [];
    const clock: Clock = { now: () => c.t, sleep: async (ms) => { c.t += ms; }, after: (ms) => { budgets.push(ms); return () => undefined; } };
    const busy = new Error('503 {"detail":{"msg_id":"mg.refusal.session_worker_busy"}}');
    await expect(retryBusy(async () => { c.t += 30_000; throw busy; }, isBusy, clock)).rejects.toBe(busy);
    expect(budgets.length).toBeGreaterThan(2);
    budgets.forEach((b, i) => { expect(b).toBeLessThanOrEqual(BUSY_DEADLINE_MS - i * 30_000); expect(b).toBeGreaterThan(0); });
  });
  it("with the REAL clock a never-settling, signal-ignoring request is cut off at the deadline and the timer is cleaned up", async () => {
    const t0 = Date.now();
    await expect(retryBusy(() => new Promise<never>(() => undefined), isBusy, realClock, 40, 5)).rejects.toBeInstanceOf(StopDeadlineExceeded);
    expect(Date.now() - t0).toBeLessThan(1000);
  });
  it("a request honouring the signal is aborted (fetch semantics)", async () => {
    let aborted = false;
    const attempt = (signal: AbortSignal) => new Promise<never>((_, rej) => signal.addEventListener("abort", () => { aborted = true; rej(new DOMException("aborted", "AbortError")); }));
    await expect(retryBusy(attempt, isBusy, realClock, 30, 5)).rejects.toBeInstanceOf(StopDeadlineExceeded);
    expect(aborted).toBe(true);
  });
  it("a request that settles in time is unaffected and its timer is cancelled", async () => {
    let cancelled = 0;
    const clock: Clock = { now: () => 0, sleep: async () => undefined, after: () => () => { cancelled++; } };
    expect(await retryBusy(async () => "ok", isBusy, clock)).toBe("ok");
    expect(cancelled).toBe(1);
  });
  it("the deadline error carries the message id the UI maps to Spanish", () => {
    expect(String(new StopDeadlineExceeded())).toContain("mg.client.stop_deadline");
  });
});
