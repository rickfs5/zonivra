// Incremental alert feed (MG-ALERT-022): the cursor moves ONLY to the revision of the last record actually received, page by page, so a burst larger than one page can never skip a record.
export interface AlertPage<T> { alerts: T[]; more: boolean; next_after: number | null }
export interface Drained<T> { alerts: T[]; cursor: number; complete: boolean; pages: number }

export async function drain<T>(fetchPage: (after: number) => Promise<AlertPage<T>>, after: number, maxPages = 20): Promise<Drained<T>> {
  const out: T[] = [];
  let cursor = after, pages = 0;
  for (; pages < maxPages;) {
    const p = await fetchPage(cursor);
    pages++;
    out.push(...p.alerts);
    if (p.next_after !== null && p.next_after > cursor) cursor = p.next_after;
    if (!p.more) return { alerts: out, cursor, complete: true, pages };
    if (!p.alerts.length) break;   // the server says there is more but sent nothing: stop instead of looping
  }
  return { alerts: out, cursor, complete: false, pages };   // the next poll continues from `cursor`
}

/** Ids the client still holds that the server no longer has (pruned): they are dropped so the page's memory stays bounded. */
export const pruned = (held: Iterable<string>, serverIds: Iterable<string>): string[] => { const s = new Set(serverIds); return [...held].filter((i) => !s.has(i)); };
