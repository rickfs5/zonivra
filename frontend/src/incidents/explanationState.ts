import type { Explanation } from "../api";

/** An explanation result is stored WITH the incident it was requested for and is visible only while that same incident is selected. */
export type Held = { alertId: string; atRev: number; x: Explanation };

/** What may be rendered for `alertId` right now. Derived at render time, so a selection change can never show the previous incident's explanation, not even for one render. */
export function visibleFor(held: Held | null, alertId: string): Held | null {
  return held !== null && held.alertId === alertId ? held : null;
}

/** A late response is applied only if it is still the latest request AND still belongs to the selected incident. */
export function mayApply(reqSeq: number, curSeq: number, reqAlertId: string, curAlertId: string): boolean {
  return reqSeq === curSeq && reqAlertId === curAlertId;
}
