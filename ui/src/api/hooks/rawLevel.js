// Pure level heuristic for a raw journald line. Extracted from useLogs so
// the classifier is unit-testable without a DOM/EventSource (same rationale
// as logRing.js).
//
// Per-slot journald lines are streamed with `--output=cat`, so they carry NO
// severity — llama.cpp / ROCm just print free text. To keep the Logs page
// level filter coherent across the structured-events channel and the raw-slot
// channel, we infer a level from the line's wording.

/** @param {string} line @returns {'info'|'warn'|'error'} */
export function parseRawLevel(line) {
  if (/\b(error|errno|failed|failure|fatal|abort|panic|denied|refused)\b/i.test(line))
    return 'error'
  // `deprecat`/`fallback` are matched as stems (no trailing \b) so
  // "deprecated"/"deprecating"/"fallback" all land as warn.
  if (/\bwarn(ing)?\b|deprecat|fallback|\bretry(ing)?\b/i.test(line)) return 'warn'
  return 'info'
}
