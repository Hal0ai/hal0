// Pure ring-append for the journal SSE tail. Extracted from useLogs so the
// dedup invariant is unit-testable without a DOM/EventSource.
//
// Why dedup: opening the footer journal pane flips `follow` on, which
// reconnects the SSE; the server replays the recent tail. A blind append
// then renders a SECOND copy of every replayed line on every open (the
// "hal0 0.5.0a1 starting" line piling up). We key on the entry's CONTENT
// signature — microsecond timestamp + source + message — not the numeric
// id, so a replay is idempotent even if the server reassigns ids per stream.

/** Stable, id-independent signature for a journal line. */
export function entryKey(e) {
  return `${e.ts}|${e.source ?? ''}|${e.msg}`;
}

/**
 * Append `entry` to the bounded ring `prev` (newest last), skipping a line
 * already present (SSE replay) and evicting the oldest past `max`. Pure:
 * returns `prev` unchanged when the entry is malformed or a duplicate.
 */
export function appendEntry(prev, entry, max) {
  if (entry == null || entry.ts == null || entry.msg == null) return prev;
  const key = entryKey(entry);
  if (prev.some((e) => entryKey(e) === key)) return prev;
  const next = prev.length >= max ? prev.slice(prev.length - max + 1) : prev.slice();
  next.push(entry);
  return next;
}
