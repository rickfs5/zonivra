import { describe, expect, it } from "vitest";
import { drain, pruned } from "./alertsync";

const server = (n: number, page: number) => {
  const all = Array.from({ length: n }, (_, i) => ({ rev: i + 1 }));
  return async (after: number) => {
    const rows = all.filter((a) => a.rev > after);
    const p = rows.slice(0, page);
    return { alerts: p, more: rows.length > page, next_after: p.length ? p[p.length - 1].rev : after };
  };
};

describe("alert feed drain", () => {
  it("reads every record of a burst larger than one page, in order, exactly once", async () => {
    const d = await drain(server(450, 100), 0);
    expect(d.alerts.map((a) => a.rev)).toEqual(Array.from({ length: 450 }, (_, i) => i + 1));
    expect(d).toMatchObject({ cursor: 450, complete: true, pages: 5 });
  });
  it("continues from the cursor and reports nothing new when caught up", async () => {
    const d = await drain(server(10, 100), 10);
    expect(d).toMatchObject({ alerts: [], cursor: 10, complete: true });
  });
  it("never advances past what it received when the page budget runs out", async () => {
    const d = await drain(server(450, 100), 0, 2);
    expect(d.complete).toBe(false); expect(d.cursor).toBe(200); expect(d.alerts).toHaveLength(200);
    const rest = await drain(server(450, 100), d.cursor);
    expect(rest.alerts[0].rev).toBe(201);
  });
  it("stops instead of looping when the server claims more but sends nothing", async () => {
    const d = await drain(async () => ({ alerts: [], more: true, next_after: 5 }), 5);
    expect(d.complete).toBe(false); expect(d.pages).toBe(1);
  });
  it("finds the ids the server pruned", () => { expect(pruned(["a", "b", "c"], ["b"])).toEqual(["a", "c"]); });
});
