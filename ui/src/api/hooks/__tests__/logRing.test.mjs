// Dependency-free test for the journal SSE ring-append dedup.
//
// Run: node ui/src/api/hooks/__tests__/logRing.test.mjs
//
// Bug of record: opening the footer journal pane flips `follow` on, which
// reconnects the SSE; the server replays the recent tail, and push() used
// to blind-append every replayed entry — so each open re-rendered a second
// copy of every message (the "hal0 0.5.0a1 starting" line duplicating on
// every open). appendEntry must dedup by content signature so a replay is
// idempotent, regardless of whether the server reassigns numeric ids.

import { appendEntry, entryKey } from "../logRing.js";

let failures = 0;
const ok = (cond, msg) => { if (!cond) { failures += 1; console.error("  ✗ " + msg); } else { console.log("  ✓ " + msg); } };

const E = (id, ts, source, msg, level = "info") => ({ id, ts, source, msg, level });
const MAX = 5;

// 1. a fresh entry appends.
{
  const out = appendEntry([], E(1, "t1", "hal0", "starting"), MAX);
  ok(out.length === 1 && out[0].msg === "starting", "appends a new entry");
}

// 2. THE BUG: re-opening the pane replays the tail — the same line must not
//    double up, even if the server hands it a different numeric id.
{
  const ring = [E(1, "2026-06-16T21:12:56.942581+00:00", "hal0", "hal0 0.5.0a1 starting")];
  const replaySameId = appendEntry(ring, E(1, "2026-06-16T21:12:56.942581+00:00", "hal0", "hal0 0.5.0a1 starting"), MAX);
  ok(replaySameId.length === 1, "replay with same id is not duplicated");
  const replayNewId = appendEntry(ring, E(99, "2026-06-16T21:12:56.942581+00:00", "hal0", "hal0 0.5.0a1 starting"), MAX);
  ok(replayNewId.length === 1, "replay with a reassigned id is still not duplicated (content key)");
}

// 3. genuinely distinct lines (same msg, different ts) are both kept.
{
  let ring = [];
  ring = appendEntry(ring, E(1, "t1", "hal0", "tick"), MAX);
  ring = appendEntry(ring, E(2, "t2", "hal0", "tick"), MAX);
  ok(ring.length === 2, "same message at different timestamps is kept");
}

// 4. ring evicts oldest at max, keeping newest.
{
  let ring = [];
  for (let i = 1; i <= MAX + 2; i++) ring = appendEntry(ring, E(i, "t" + i, "hal0", "m" + i), MAX);
  ok(ring.length === MAX, "ring is capped at max");
  ok(ring[ring.length - 1].msg === "m" + (MAX + 2), "newest entry retained after eviction");
  ok(ring[0].msg === "m3", "oldest entries evicted");
}

// 5. malformed entries (missing ts/msg) are ignored, not appended.
{
  const ring = [E(1, "t1", "hal0", "ok")];
  ok(appendEntry(ring, { id: 2, source: "hal0" }, MAX).length === 1, "entry without ts/msg is ignored");
  ok(appendEntry(ring, null, MAX).length === 1, "null entry is ignored");
}

// 6. entryKey ignores id so replays collapse.
{
  ok(entryKey(E(1, "t", "s", "m")) === entryKey(E(2, "t", "s", "m")), "entryKey is id-independent");
}

if (failures) { console.error(`\n${failures} assertion(s) failed`); process.exit(1); }
console.log("\nall logRing assertions passed");
