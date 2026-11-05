import { describe, expect, it } from "vitest";
import { mayApply, visibleFor } from "./explanationState";
import type { Held } from "./explanationState";

const heldA: Held = { alertId: "alt_A", atRev: 1, x: { status: "ACCEPTED" } as never };

describe("explanation state belongs to one incident (F01)", () => {
  it("shows the result only for the incident it was requested for", () => {
    expect(visibleFor(heldA, "alt_A")).toBe(heldA);
    expect(visibleFor(heldA, "alt_B")).toBeNull();       // the first render after selecting B must not show A's explanation
    expect(visibleFor(null, "alt_A")).toBeNull();
  });
  it("drops a late response whose incident is no longer selected, even if it is the latest request", () => {
    expect(mayApply(3, 3, "alt_A", "alt_A")).toBe(true);
    expect(mayApply(3, 3, "alt_A", "alt_B")).toBe(false);
    expect(mayApply(2, 3, "alt_A", "alt_A")).toBe(false);
  });
});
