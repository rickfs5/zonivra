import { beforeEach, describe, expect, it, vi } from "vitest";
import { THEME_KEY, THEMES, __reloadThemeFromStorage, applyTheme, getResolvedTheme, getThemePref, isThemePref, parseThemePref, resolveTheme, setThemePref } from "./theme";

describe("theme pure functions", () => {
  it("accepts exactly light|dark|system", () => {
    expect(THEMES).toEqual(["light", "dark", "system"]);
    for (const v of THEMES) expect(isThemePref(v)).toBe(true);
    for (const v of ["", "Light", "DARK", "auto", null, undefined, 1, {}, ["dark"]]) expect(isThemePref(v)).toBe(false);
  });
  it("an invalid persisted value fails safe to system", () => {
    for (const v of ["", "blue", "LIGHT", null, undefined, 0, "{}"]) expect(parseThemePref(v)).toBe("system");
    expect(parseThemePref("dark")).toBe("dark");
  });
  it("resolves explicit choices regardless of the OS and system by the OS", () => {
    expect(resolveTheme("light", true)).toBe("light"); expect(resolveTheme("dark", false)).toBe("dark");
    expect(resolveTheme("system", true)).toBe("dark"); expect(resolveTheme("system", false)).toBe("light");
  });
});

function mockMatchMedia(dark: boolean) {
  const listeners = new Set<() => void>();
  const mql = { matches: dark, addEventListener: (_: string, f: () => void) => listeners.add(f), removeEventListener: (_: string, f: () => void) => listeners.delete(f) };
  vi.stubGlobal("matchMedia", () => mql);
  return { flip: (v: boolean) => { mql.matches = v; listeners.forEach((f) => f()); } };
}

/** The vitest environment is node: minimal in-memory stand-ins for localStorage and <html>. */
function fakeDom(opts: { throwOnSet?: boolean } = {}) {
  const store = new Map<string, string>(); const attrs = new Map<string, string>();
  vi.stubGlobal("localStorage", { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { if (opts.throwOnSet) throw new Error("blocked"); store.set(k, v); }, removeItem: (k: string) => store.delete(k), clear: () => store.clear() });
  const documentElement = { setAttribute: (k: string, v: string) => attrs.set(k, v), getAttribute: (k: string) => attrs.get(k) ?? null, style: {} as Record<string, string> };
  vi.stubGlobal("document", { documentElement });
  return { store, attr: (k: string) => attrs.get(k) ?? null };
}

describe("theme state and persistence", () => {
  beforeEach(() => { vi.unstubAllGlobals(); });

  it("setThemePref applies data-theme, stores the choice and needs no reload", () => {
    mockMatchMedia(true); const d = fakeDom();
    setThemePref("light");
    expect(d.attr("data-theme")).toBe("light"); expect(d.attr("data-theme-pref")).toBe("light");
    expect(d.store.get(THEME_KEY)).toBe("light"); expect(getThemePref()).toBe("light"); expect(getResolvedTheme()).toBe("light");
    setThemePref("dark"); expect(d.attr("data-theme")).toBe("dark"); expect(d.store.get(THEME_KEY)).toBe("dark");
  });
  it("system follows the OS preference at apply time and when the OS changes", () => {
    const m = mockMatchMedia(false); const d = fakeDom();
    setThemePref("system"); expect(d.attr("data-theme")).toBe("light");
    m.flip(true); applyTheme("system"); expect(d.attr("data-theme")).toBe("dark");
    expect(getThemePref()).toBe("system");
  });
  it("an invalid stored value is ignored on load; an invalid setter input is ignored", () => {
    mockMatchMedia(true); const d = fakeDom();
    d.store.set(THEME_KEY, "neon"); expect(__reloadThemeFromStorage()).toBe("system"); expect(d.attr("data-theme")).toBe("dark");
    // @ts-expect-error deliberately invalid
    setThemePref("neon"); expect(getThemePref()).toBe("system"); expect(d.store.get(THEME_KEY)).toBe("neon");
  });
  it("a stored valid value is restored on load", () => {
    mockMatchMedia(false); const d = fakeDom(); d.store.set(THEME_KEY, "dark");
    expect(__reloadThemeFromStorage()).toBe("dark"); expect(d.attr("data-theme")).toBe("dark");
  });
  it("storage failures never throw and the choice still applies for the page", () => {
    mockMatchMedia(true); const d = fakeDom({ throwOnSet: true });
    expect(() => setThemePref("light")).not.toThrow(); expect(d.attr("data-theme")).toBe("light");
  });
  it("missing matchMedia falls back to dark for system", () => {
    vi.stubGlobal("matchMedia", undefined); const d = fakeDom();
    setThemePref("system"); expect(d.attr("data-theme")).toBe("dark");
  });
  it("never issues a network request", () => {
    mockMatchMedia(true); fakeDom();
    const f = vi.fn(); vi.stubGlobal("fetch", f);
    setThemePref("light"); setThemePref("dark"); setThemePref("system"); expect(f).not.toHaveBeenCalled();
  });
});
