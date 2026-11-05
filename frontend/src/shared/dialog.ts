import { useEffect, type RefObject } from "react";

/** Modal behaviour for drawers and dialogs: focus moves in on open, Tab stays inside, Escape closes, focus returns to what opened it. */
export function useDialogFocus(ref: RefObject<HTMLElement | null>, onClose: () => void) {
  useEffect(() => {
    const prev = document.activeElement as HTMLElement | null;
    const el = ref.current;
    const focusables = () => Array.from(el?.querySelectorAll<HTMLElement>('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])') ?? []).filter((x) => !x.hasAttribute("disabled"));
    (focusables()[0] ?? el)?.focus();
    const k = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); onClose(); return; }
      if (e.key !== "Tab") return;
      const f = focusables();
      if (!f.length) return;
      const first = f[0], last = f[f.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", k);
    return () => { document.removeEventListener("keydown", k); prev?.focus?.(); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
}
