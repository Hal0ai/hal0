// hal0 dashboard — launch-flags tune helpers (D1 model drawer).
//
// Pure, dependency-free helpers for the model drawer's flags editor. The model
// owns its full launch-flags text (materialized into `model.defaults.extra_args`
// — the freeform "tune remainder": -fa / -b/-ub / --threads / KV-quant / …).
// These helpers tokenize that text, flag managed args the server will reject,
// highlight flag tokens for the editor overlay, and diff the model's text
// against the profile that seeded it (client-side divergence, per spec §3 —
// both texts are already on the client, so no diff endpoint is needed for v1).
//
// The managed-arg set mirrors hal0.slots.argv.MANAGED_ARGS_DENYLIST (§21.7):
//   --model  --ctx-size  --host  --port  --n-gpu-layers  --alias
// plus the short spellings argv._canon() maps into that set (-ngl, -c). hal0
// computes these from the slot/model config and rejects them in the tune text.

// Canonical (long) managed flags + the short spellings the server canonicalises
// into them. Keep in sync with hal0/slots/argv.py FLAG_ALIASES + the denylist.
export const MANAGED_FLAGS = [
  "--model",
  "--ctx-size",
  "--host",
  "--port",
  "--n-gpu-layers",
  "--alias",
];
const MANAGED_SHORT = { "-ngl": "--n-gpu-layers", "-c": "--ctx-size" };
const MANAGED_SET = new Set([...MANAGED_FLAGS, ...Object.keys(MANAGED_SHORT)]);

// Slot-hardware flags (spec-hw-slot-ownership §5): the grid-owned hardware
// flags a MODEL / PROFILE tune must never carry — they belong on the slot's
// typed hardware grid (device · NGL · THREADS). Mirrors the server
// SLOT_HARDWARE_FLAGS frozenset (hal0.slots.argv), including both long and
// short spellings. The model/profile flag save HARD-REJECTS any of these; this
// client check mirrors that reject inline before the PUT fires.
export const SLOT_HARDWARE_FLAGS = [
  "-ngl",
  "--n-gpu-layers",
  "-dev",
  "--device",
  "--threads",
  "-t",
];
const SLOT_HARDWARE_SET = new Set(SLOT_HARDWARE_FLAGS);

// The slot-hardware flags present in `text` (deduped, order-preserved). Empty
// array = clean. Used by the model + profile flag editors to reject the grid-
// owned hardware flags with a "these belong on the slot" message.
export function findSlotHardwareFlags(text) {
  const { tokens } = tokenizeFlags(text);
  const seen = new Set();
  const offenders = [];
  for (const tok of tokens) {
    if (SLOT_HARDWARE_SET.has(tok) && !seen.has(tok)) {
      seen.add(tok);
      offenders.push(tok);
    }
  }
  return offenders;
}

// Short → long canonicalisation for the hardware set. Mirrors the subset of
// argv.py's FLAG_ALIASES that covers SLOT_HARDWARE_FLAGS.
const SLOT_HARDWARE_CANON = {
  "-ngl": "--n-gpu-layers",
  "-dev": "--device",
  "-t": "--threads",
};

const canonHardware = (flag) => SLOT_HARDWARE_CANON[flag] || flag;

// The slot-hardware flags `text` INTRODUCES relative to `storedText` (#1411).
//
// The §5 screen shipped with no data migration, so a profile authored before it
// carries -dev/--threads in its own stored flags — and the drawer round-trips
// that text verbatim, so a plain re-save tripped the guard on the profile's own
// data. The server now screens what an update ADDS, not what it inherits
// (hal0.profiles.screen_profile_flags), so this inline mirror has to do the same
// or the drawer keeps blocking a save the API would accept. Matching is by
// canonical flag, like the server's, so a stored `-dev` also covers `--device`.
//
// `storedText` empty (create/clone) ⇒ nothing inherited ⇒ identical to
// findSlotHardwareFlags. Models never grandfather; they keep the strict finder.
export function findNewSlotHardwareFlags(text, storedText = "") {
  const inherited = new Set(findSlotHardwareFlags(storedText).map(canonHardware));
  if (!inherited.size) return findSlotHardwareFlags(text);
  return findSlotHardwareFlags(text).filter((f) => !inherited.has(canonHardware(f)));
}

// Where each managed flag is actually controlled from — surfaced in the inline
// rejection so the operator knows why it's off-limits and where to set it.
export const MANAGED_FLAG_SOURCE = {
  "--model": "the model's on-disk path (Source · re-pull coords)",
  "--ctx-size": "the context_size field",
  "--host": "the server bind host (authority-owned)",
  "--port": "PortAuthority (shown read-only on the slot)",
  // #2105: NGL is slot-owned hardware (spec-hw-slot-ownership §2) — there is no
  // model-side n_gpu_layers field to point at any more. The slot-hardware check
  // normally answers first for this flag; this entry is the profile drawer's
  // fallback, so it has to name the slot's grid, not a field that was removed.
  "--n-gpu-layers": "the slot's hardware grid (NGL)",
  "--alias": "the model id / dispatch alias (authority-owned)",
};

// True for a --long or -x/-ngl short flag; false for a value or negative number
// (a leading '-' before a digit is a value, e.g. `-ngl -1`). Mirrors argv._is_flag.
export function isFlagToken(tok) {
  if (typeof tok !== "string" || tok.length === 0) return false;
  if (tok.startsWith("--")) return tok.length > 2;
  return tok.length > 1 && tok[0] === "-" && /[a-zA-Z]/.test(tok[1]);
}

// shlex-lite: split on whitespace, honouring single/double quotes so a
// `--chat-template-kwargs '{"enable_thinking":false}'` value survives as one
// token (the container.py JSON-token round-trip this must not break). Returns
// { tokens, error } — error is a message string when a quote is unbalanced.
export function tokenizeFlags(text) {
  const tokens = [];
  const s = String(text || "");
  let i = 0;
  const n = s.length;
  let cur = "";
  let started = false;
  let quote = null;
  while (i < n) {
    const ch = s[i];
    if (quote) {
      if (ch === quote) { quote = null; }
      else { cur += ch; }
      i += 1;
      continue;
    }
    if (ch === '"' || ch === "'") { quote = ch; started = true; i += 1; continue; }
    if (ch === " " || ch === "\t" || ch === "\n" || ch === "\r") {
      if (started) { tokens.push(cur); cur = ""; started = false; }
      i += 1;
      continue;
    }
    cur += ch;
    started = true;
    i += 1;
  }
  if (quote) return { tokens, error: "unbalanced quote in launch flags" };
  if (started) tokens.push(cur);
  return { tokens, error: null };
}

// Canonicalise a flag spelling onto its managed key (or return it unchanged).
function canonManaged(flag) {
  return MANAGED_SHORT[flag] || flag;
}

// Short → long canonicalisation for llama-server's own flag spellings. Mirrors
// hal0.slots.argv.FLAG_ALIASES verbatim (kept in sync by
// tests/ui_contracts/test_flag_aliases_mirror.py, which parses this literal
// as JSON and compares it against the server dict). Written as strict JSON
// (double-quoted keys/values, no trailing comma) so that parse holds.
export const FLAG_ALIASES = {
  "-b": "--batch-size",
  "-ub": "--ubatch-size",
  "-ngl": "--n-gpu-layers",
  "-ctk": "--cache-type-k",
  "-ctv": "--cache-type-v",
  "-t": "--threads",
  "-tb": "--threads-batch",
  "-fa": "--flash-attn",
  "-dev": "--device",
  "-sm": "--split-mode",
  "-c": "--ctx-size",
  "-ts": "--tensor-split",
  "-mg": "--main-gpu",
  "-np": "--parallel",
  "-kvu": "--kv-unified",
  "-ngld": "--n-gpu-layers-draft"
};

// Fold a flag spelling onto its canonical long form via FLAG_ALIASES, or
// return it unchanged when it isn't a known short alias (already long, or an
// unrecognised flag entirely).
export function canonFlag(flag) {
  return FLAG_ALIASES[flag] || flag;
}

// The category a canonical (long) flag belongs to in the model drawer's
// grouped flags editor. Seeded from the flags the repo's own seed profiles +
// panel 03 use; anything absent here falls through to "template-misc" in
// groupFlagPairs. Keyed by canonical long name — callers fold through
// canonFlag before looking up.
export const FLAG_CATEGORIES = {
  "--temp": "sampling",
  "--top-p": "sampling",
  "--top-k": "sampling",
  "--min-p": "sampling",
  "--repeat-penalty": "sampling",
  "--presence-penalty": "sampling",
  "--frequency-penalty": "sampling",
  "--flash-attn": "cache-kv",
  "--cache-type-k": "cache-kv",
  "--cache-type-v": "cache-kv",
  "--cache-reuse": "cache-kv",
  "--batch-size": "memory-batch",
  "--ubatch-size": "memory-batch",
  "--no-mmap": "memory-batch",
  "--mlock": "memory-batch",
  "--parallel": "memory-batch",
};

// Display order + labels for the model drawer's flag-category sections.
export const CATEGORY_ORDER = [
  { id: "sampling", label: "Sampling" },
  { id: "cache-kv", label: "Cache · KV" },
  { id: "memory-batch", label: "Memory · batch" },
  { id: "template-misc", label: "Template · misc" },
];

// Group a flat token list into { flag, value } pairs (a flag consumes the next
// token as its value iff that token isn't itself a flag). Bare positionals are
// dropped from the pair view (they never carry a managed key).
export function flagPairs(tokens) {
  const out = [];
  let i = 0;
  while (i < tokens.length) {
    const tok = tokens[i];
    if (isFlagToken(tok)) {
      if (i + 1 < tokens.length && !isFlagToken(tokens[i + 1])) {
        out.push({ flag: tok, canon: canonManaged(tok), value: tokens[i + 1] });
        i += 2;
      } else {
        out.push({ flag: tok, canon: canonManaged(tok), value: null });
        i += 1;
      }
    } else {
      i += 1;
    }
  }
  return out;
}

// The managed flags present in `text`, by the spelling the operator typed
// (deduped, order-preserved). Empty array = clean.
export function findManagedFlags(text) {
  const { tokens } = tokenizeFlags(text);
  const seen = new Set();
  const offenders = [];
  for (const tok of tokens) {
    if (MANAGED_SET.has(tok) && !seen.has(tok)) {
      seen.add(tok);
      offenders.push(tok);
    }
  }
  return offenders;
}

// Segments for the editor's token-highlight overlay: flags → amber, values →
// dim. Returns [{ text, kind: "flag"|"value"|"space" }] preserving raw spacing
// so an overlaid highlight aligns 1:1 with the underlying textarea.
export function highlightSegments(text) {
  const s = String(text || "");
  const segs = [];
  const re = /(\s+)|(\S+)/g;
  let m;
  while ((m = re.exec(s)) !== null) {
    if (m[1] != null) { segs.push({ text: m[1], kind: "space" }); continue; }
    const word = m[2];
    segs.push({ text: word, kind: isFlagToken(word) ? "flag" : "value" });
  }
  return segs;
}

// Client-side divergence: diff the model's tune text against the profile text
// that seeded it. Keyed by canonical flag so a reordered `-b 2048` isn't a
// spurious change. Returns { added, removed, changed, unchanged, diverged }.
//   added:     [{ flag, value }]  present in model, not in profile
//   removed:   [{ flag, value }]  present in profile, not in model
//   changed:   [{ flag, from, to }] same flag, different value
//   unchanged: number             identical pairs
export function diffFlags(modelText, profileText) {
  const mp = flagPairs(tokenizeFlags(modelText).tokens);
  const pp = flagPairs(tokenizeFlags(profileText).tokens);
  const key = (p) => p.canon;
  const pByFlag = new Map();
  for (const p of pp) pByFlag.set(key(p), p);
  const mByFlag = new Map();
  for (const p of mp) mByFlag.set(key(p), p);

  const added = [];
  const changed = [];
  let unchanged = 0;
  for (const p of mp) {
    const other = pByFlag.get(key(p));
    if (!other) { added.push({ flag: p.flag, value: p.value }); continue; }
    if ((other.value || "") !== (p.value || "")) {
      changed.push({ flag: p.flag, from: other.value, to: p.value });
    } else {
      unchanged += 1;
    }
  }
  const removed = [];
  for (const p of pp) {
    if (!mByFlag.has(key(p))) removed.push({ flag: p.flag, value: p.value });
  }
  const diverged = added.length > 0 || removed.length > 0 || changed.length > 0;
  return { added, removed, changed, unchanged, diverged };
}

// Whether two flag texts are equivalent modulo whitespace/order (the divergence
// predicate). Cheaper than a full diff when only the boolean is needed.
export function flagsEquivalent(a, b) {
  const d = diffFlags(a, b);
  return !d.diverged;
}

// Group `text`'s flag pairs by FLAG_CATEGORIES for the model drawer's grouped
// flags editor. Unknown flags (not in FLAG_CATEGORIES) fall through to
// "template-misc". Empty groups are omitted; the remaining groups keep
// CATEGORY_ORDER's order. `error` mirrors tokenizeFlags's — groups are still
// returned best-effort (from whatever tokens were accumulated before the
// error) so a mid-edit unbalanced quote doesn't blank the editor.
export function groupFlagPairs(text) {
  const { tokens, error } = tokenizeFlags(text);
  const byCategory = new Map(CATEGORY_ORDER.map((c) => [c.id, []]));
  for (const p of flagPairs(tokens)) {
    const canon = canonFlag(p.flag);
    const catId = FLAG_CATEGORIES[canon] || "template-misc";
    byCategory.get(catId).push({ flag: p.flag, canon, value: p.value });
  }
  const groups = CATEGORY_ORDER
    .map((c) => ({ id: c.id, label: c.label, pairs: byCategory.get(c.id) }))
    .filter((g) => g.pairs.length > 0);
  return { groups, error };
}

// Split raw flag text into an alternating sequence of whitespace-run and
// token segments, quote-aware like tokenizeFlags: a `'...'`/`"..."` run keeps
// its enclosing quotes and any embedded whitespace as ONE segment, so a
// `--chat-template-kwargs '{"enable_thinking":false}'`- or `--alias "a b"`-
// style value round-trips as a single unit instead of being torn into
// fragments that corrupt everything after them. The segment's `text` keeps
// the raw quote characters verbatim — callers that replace a segment's text
// (spliceFlagValue) intentionally drop them, since the replacement value is
// inserted unquoted.
function rawSegments(text) {
  const s = String(text || "");
  const segs = [];
  const n = s.length;
  let i = 0;
  while (i < n) {
    if (/\s/.test(s[i])) {
      let j = i;
      while (j < n && /\s/.test(s[j])) j += 1;
      segs.push({ text: s.slice(i, j), word: false });
      i = j;
      continue;
    }
    let j = i;
    let quote = null;
    while (j < n) {
      const ch = s[j];
      if (quote) {
        j += 1;
        if (ch === quote) quote = null;
        continue;
      }
      if (ch === '"' || ch === "'") { quote = ch; j += 1; continue; }
      if (/\s/.test(ch)) break;
      j += 1;
    }
    segs.push({ text: s.slice(i, j), word: true });
    i = j;
  }
  return segs;
}

// Locate the { flag, value } pair in `segs` (as produced by rawSegments)
// whose canonFlag(flag) === canon, returning the segment indices of the flag
// token and its value token (null when the flag carries no value, i.e. the
// next word is itself a flag or there is no next word). Returns null when no
// pair matches.
function findFlagPairSegIndices(segs, canon) {
  const wordIdx = [];
  segs.forEach((seg, i) => { if (seg.word) wordIdx.push(i); });
  for (let k = 0; k < wordIdx.length; k += 1) {
    const flagIdx = wordIdx[k];
    const tok = segs[flagIdx].text;
    if (!isFlagToken(tok) || canonFlag(tok) !== canon) continue;
    const next = wordIdx[k + 1];
    if (next != null && !isFlagToken(segs[next].text)) {
      return { flagIdx, valueIdx: next };
    }
    return { flagIdx, valueIdx: null };
  }
  return null;
}

// Replace the value token of the pair whose canonFlag(flag) === canon with
// `nextValue`, preserving the operator's original flag spelling, token order,
// and every other token's surrounding whitespace verbatim. A no-op (returns
// `text` unchanged) when no pair matches canon, or the matched flag carries no
// value token to replace.
export function spliceFlagValue(text, canon, nextValue) {
  const s = String(text || "");
  const segs = rawSegments(s);
  const found = findFlagPairSegIndices(segs, canon);
  if (!found || found.valueIdx == null) return s;
  segs[found.valueIdx] = { text: String(nextValue), word: true };
  return segs.map((seg) => seg.text).join("");
}

// Drop the flag token + its value token (or just the flag, for a boolean
// flag) for the pair whose canonFlag(flag) === canon, collapsing the
// resulting double space so the surrounding tokens read as if the pair had
// never been there. A no-op when no pair matches canon.
export function removeFlagFromText(text, canon) {
  const s = String(text || "");
  const segs = rawSegments(s);
  const found = findFlagPairSegIndices(segs, canon);
  if (!found) return s;
  const { flagIdx, valueIdx } = found;
  const endIdx = valueIdx != null ? valueIdx : flagIdx;
  const drop = new Set();
  for (let i = flagIdx; i <= endIdx; i += 1) drop.add(i);
  if (flagIdx > 0 && !segs[flagIdx - 1].word) {
    drop.add(flagIdx - 1);
  } else if (endIdx + 1 < segs.length && !segs[endIdx + 1].word) {
    drop.add(endIdx + 1);
  }
  return segs.filter((_, i) => !drop.has(i)).map((seg) => seg.text).join("");
}

// Append `flag` (and `value`, when given) to `text`, space-separated from
// whatever's already there. A value containing whitespace is wrapped in
// double quotes so it round-trips through tokenizeFlags as one token; a
// null/undefined/empty value is omitted entirely (a boolean flag).
export function addFlagToText(text, flag, value) {
  const base = String(text || "");
  let piece = flag;
  if (value !== null && value !== undefined && value !== "") {
    const v = String(value);
    piece += /\s/.test(v) ? ` "${v}"` : ` ${v}`;
  }
  return base ? `${base} ${piece}` : piece;
}
