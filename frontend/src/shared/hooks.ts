import { useCallback, useEffect, useRef, useState } from "react";
import { refusalText } from "../ZoneEditor";

/** One-shot load with an explicit error and a reload; `stale` is true when the LAST refresh failed but an older value is still shown. */
export function useLoad<T>(fn: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => { try { setData(await fn()); setError(null); } catch (e) { setError(refusalText(e)); } }, [fn]);
  useEffect(() => { void load(); }, [load]);
  return { data, error, reload: load, stale: error !== null && data !== null };
}

/** Polling that never hides a failure: the previous value stays visible and is flagged stale with the time it was last refreshed. */
export function usePoll<T>(fn: () => Promise<T>, ms: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [at, setAt] = useState<number | null>(null);
  const alive = useRef(true);
  const load = useCallback(async () => {
    try { const v = await fn(); if (alive.current) { setData(v); setError(null); setAt(Date.now()); } }
    catch (e) { if (alive.current) setError(refusalText(e)); }
  }, [fn]);
  useEffect(() => { alive.current = true; void load(); const h = window.setInterval(() => void load(), ms); return () => { alive.current = false; window.clearInterval(h); }; }, [load, ms]);
  return { data, error, at, stale: error !== null && data !== null };
}
