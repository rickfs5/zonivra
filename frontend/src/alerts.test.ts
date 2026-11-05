import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { beep, readPrefs, writePrefs } from "./Alerts";

function stubWindow(storage: Record<string, string> | "throws") {
  const events: string[] = [];
  const ls = storage === "throws"
    ? { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } }
    : { getItem: (k: string) => storage[k] ?? null, setItem: (k: string, v: string) => { storage[k] = v; } };
  (globalThis as unknown as { window: unknown }).window = { localStorage: ls, dispatchEvent: (e: Event) => events.push(e.type), addEventListener: () => undefined };
  return events;
}

describe("local notification consent", () => {
  beforeEach(() => stubWindow({}));
  afterEach(() => { delete (globalThis as unknown as { window?: unknown }).window; });
  it("starts with notifications and sound OFF", () => { expect(readPrefs()).toEqual({ browser: false, sound: false }); });
  it("persists only an explicit choice and announces it", () => {
    const store: Record<string, string> = {};
    const ev = stubWindow(store);
    writePrefs({ browser: true, sound: false });
    expect(readPrefs()).toEqual({ browser: true, sound: false }); expect(ev).toEqual(["mg-prefs"]);
  });
  it("treats corrupt or non-boolean stored values as OFF", () => {
    stubWindow({ "mg.alert.prefs": "{bad json" }); expect(readPrefs()).toEqual({ browser: false, sound: false });
    stubWindow({ "mg.alert.prefs": JSON.stringify({ browser: "yes", sound: 1 }) }); expect(readPrefs()).toEqual({ browser: false, sound: false });
  });
  it("works (OFF) when storage is blocked and never throws on write", () => {
    stubWindow("throws");
    expect(readPrefs()).toEqual({ browser: false, sound: false });
    expect(() => writePrefs({ browser: true, sound: true })).not.toThrow();
  });
  it("cannot play a sound before the user unlocked audio", () => { expect(beep("RED")).toBe(false); });
});
