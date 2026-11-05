import { describe, expect, it } from "vitest";
import { heroFacts } from "./heroState";

const base = { activeFeeds: 0, activeLimit: 3, validatedFeeds: 3, schedulerAlive: false, modelLoaded: false, detectorError: null, activeSession: false, feedsFailed: 0 };
describe("heroFacts", () => {
  it("an empty, idle system is idle with the model not loaded (loads on first use) - never 'healthy'", () => {
    const f = heroFacts(base);
    expect(f).toEqual({ model: "not_loaded", worker: "idle", analysis: "idle", sources: { active: 0, limit: 3, validated: 3 }, failedFeeds: 0 });
  });
  it("a detector error wins over model_loaded", () => { expect(heroFacts({ ...base, modelLoaded: true, detectorError: "weights_missing" }).model).toBe("error"); });
  it("feeds present but scheduler dead is 'dead' and not analysing", () => {
    const f = heroFacts({ ...base, activeFeeds: 2 }); expect(f.worker).toBe("dead"); expect(f.analysis).toBe("idle");
  });
  it("feeds + live scheduler is feeds_active; a camera session takes precedence", () => {
    expect(heroFacts({ ...base, activeFeeds: 2, schedulerAlive: true, modelLoaded: true }).analysis).toBe("feeds_active");
    expect(heroFacts({ ...base, activeFeeds: 2, schedulerAlive: true, activeSession: true }).analysis).toBe("session_active");
  });
  it("passes counts through unchanged (no invention)", () => { const f = heroFacts({ ...base, activeFeeds: 1, feedsFailed: 4 }); expect(f.sources.active).toBe(1); expect(f.failedFeeds).toBe(4); });
});
