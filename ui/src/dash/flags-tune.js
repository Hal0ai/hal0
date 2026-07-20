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

// Where each managed flag is actually controlled from — surfaced in the inline
// rejection so the operator knows why it's off-limits and where to set it.
export const MANAGED_FLAG_SOURCE = {
  "--model": "the model's on-disk path (Source · re-pull coords)",
  "--ctx-size": "the context_size field",
  "--host": "the server bind host (authority-owned)",
  "--port": "PortAuthority (shown read-only on the slot)",
  "--n-gpu-layers": "the n_gpu_layers field",
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

// Group a flat token list into { flag, value } pairs (a flag consumes the next
// token as its value iff that token isn't itself a flag). Bare positionals are
// dropped from the pair view (they never carry a managed key).
function pairs(tokens) {
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
  const mp = pairs(tokenizeFlags(modelText).tokens);
  const pp = pairs(tokenizeFlags(profileText).tokens);
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
