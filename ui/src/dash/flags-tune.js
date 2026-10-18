// hal0 dashboard — launch-flags helpers (ADR-0029: the profile owns the tune).
//
// Pure, dependency-free helpers for the profile drawer's flags field. A
// profile's `flags` text is the freeform llama-server remainder (-fa / -b / -ub
// / KV-quant / …); context window, thinking, vision, speculation and runner are typed
// profile fields, and the model carries file facts only. These helpers
// tokenize the text and screen out the flags the server rejects with a 400, so
// the drawer can say so inline before the PUT fires:
//
//   · managed flags      — hal0 computes them (hal0.slots.argv.MANAGED_ARGS_DENYLIST):
//                          --model/-m --ctx-size/-c --host --port --n-gpu-layers --alias/-a
//   · slot hardware      — the slot's grid owns them: -ngl/--gpu-layers -dev/--device -t/--threads
//   · model-fact flags   — the model row owns them (profiles.model_fact_flag_denied):
//                          --jinja/--no-jinja --chat-template --chat-template-file
//                          --mmproj/-mm --no-mmproj --mmproj-auto/--no-mmproj-auto
//   · --spec-draft-device, --mmproj-device, and --reasoning-format none
//   · host-owned flags   — remote model fetches, host files, listener and auth/TLS
//                          settings, --agent (slot.host_owned_flag_denied)
//   · bare arguments     — a value with no flag (slot.positional_arg_denied)
//
// Every screen matches the canonical name (screenCanon), the way the server
// does, so each spelling llama.cpp accepts is caught: short and alternative
// forms, `flag=value`, and `_` for `-` in a --long flag.
//
// diffFlags compares two profiles' flag texts (the slot preview counts what a
// profile swap changes).

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
// Matched by canonical name (screenCanon), so every spelling FLAG_ALIASES
// knows (-m, -a, -c, -ngl, --gpu-layers, …) is caught.
const MANAGED_CANON_SET = new Set(MANAGED_FLAGS);

// Slot-hardware flags (spec-hw-slot-ownership §5): the grid-owned hardware
// flags a PROFILE tune must never carry — they belong on the slot's typed
// hardware grid (device · NGL · THREADS). Mirrors the server
// SLOT_HARDWARE_FLAGS frozenset (hal0.slots.argv), including both long and
// short spellings. The profile flag save HARD-REJECTS any of these; this
// client check mirrors that reject inline before the PUT fires.
export const SLOT_HARDWARE_FLAGS = [
  "-ngl",
  "--n-gpu-layers",
  "--gpu-layers",
  "-dev",
  "--device",
  "--threads",
  "-t",
];
const SLOT_HARDWARE_CANON_SET = new Set(["--n-gpu-layers", "--device", "--threads"]);

// The slot-hardware flags present in `text` (deduped, order-preserved). Empty
// array = clean. Used by the profile flag editor to reject the grid-owned
// hardware flags with a "these belong on the slot" message.
export function findSlotHardwareFlags(text) {
  return findByCanon(text, SLOT_HARDWARE_CANON_SET);
}

// Model-fact flags (ADR-0029): the chat template and projector are properties
// of the FILE, recorded on the model row, so a profile must not carry them.
// `--spec-type` follows the model's drafter or MTP heads and the profile's
// Speculation (ADR-0031). Mirrors the server's profiles.model_fact_flag_denied
// screen (hal0.slots.argv.MODEL_FACT_FLAGS).
export const MODEL_FACT_FLAGS = [
  "--spec-type",
  "--jinja",
  "--no-jinja",
  "--chat-template",
  "--chat-template-file",
  "--mmproj",
  "--no-mmproj",
  "--mmproj-auto",
  "--no-mmproj-auto",
];
const MODEL_FACT_SET = new Set(MODEL_FACT_FLAGS);

// Where each model-fact flag is actually controlled from, for the rejection
// copy.
export const MODEL_FACT_FLAG_SOURCE = {
  "--jinja": "the model's Jinja fact",
  "--no-jinja": "the model's Jinja fact",
  "--chat-template": "the model's Chat template",
  "--chat-template-file": "the model's Chat template",
  "--mmproj": "the model's mmproj path (Source)",
  "--no-mmproj": "this profile's Vision field (set it to off)",
  "--mmproj-auto": "this profile's Vision field",
  "--no-mmproj-auto": "this profile's Vision field (set it to off)",
};

// Why `--spec-type` is refused: it is not a file fact the operator sets on the
// model row, so it gets its own copy rather than a MODEL_FACT_FLAG_SOURCE entry.
export const SPEC_TYPE_REFUSAL =
  "hal0 sets it from the model's drafter or MTP heads; set the profile's Speculation instead";

// Other flags the profile screen refuses outright: the draft-model device is
// derived from the slot's device, and `--reasoning-format none` would break
// the reasoning split hal0 relies on.
const DERIVED_PLACEMENT = new Set(["--spec-draft-device", "--mmproj-device"]);

// Offending tokens in `text` for the profile flag screen beyond managed and
// hardware: model-fact flags, --spec-draft-device and `--reasoning-format
// none`. Deduped, order-preserved; empty = clean. Tokens are reported as
// typed. `--flag=value` spellings are matched on the part before the '='.
export function findModelFactFlags(text) {
  const { tokens } = tokenizeFlags(text);
  const seen = new Set();
  const offenders = [];
  const add = (t) => { if (!seen.has(t)) { seen.add(t); offenders.push(t); } };
  for (let i = 0; i < tokens.length; i += 1) {
    const tok = tokens[i];
    if (!isFlagToken(tok)) continue;
    const name = typedName(tok);
    const canon = screenCanon(tok);
    if (MODEL_FACT_SET.has(canon) || DERIVED_PLACEMENT.has(canon)) {
      add(name);
    } else if (canon === "--reasoning-format") {
      const raw = tok.includes("=") ? tok.slice(tok.indexOf("=") + 1) : tokens[i + 1];
      // The server compares value.strip().lower(); so does this check.
      const val = String(raw ?? "").trim().toLowerCase();
      if (val === "none") add(tok.includes("=") ? tok : `${tok} ${raw}`);
    }
  }
  return offenders;
}

// Where each managed flag is actually controlled from — surfaced in the inline
// rejection so the operator knows why it's off-limits and where to set it.
export const MANAGED_FLAG_SOURCE = {
  "--model": "the model's on-disk path (Source — repo · file · mmproj · paths)",
  "--ctx-size": "the profile's Context field (ctx_size)",
  "--host": "the server bind host (authority-owned)",
  "--port": "PortAuthority (shown read-only on the slot)",
  // NGL is slot-owned hardware (spec-hw-slot-ownership §2). The slot-hardware
  // check normally answers first for this flag; this entry is the profile
  // drawer's fallback, so it has to name the slot's grid.
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
  "-ngld": "--n-gpu-layers-draft",
  "-m": "--model",
  "-a": "--alias",
  "--gpu-layers": "--n-gpu-layers",
  "-mm": "--mmproj",
  "-devd": "--spec-draft-device",
  "--device-draft": "--spec-draft-device",
  "-mmdev": "--mmproj-device",
  "-mu": "--model-url",
  "-mmu": "--mmproj-url",
  "-hf": "--hf-repo",
  "-hfr": "--hf-repo",
  "-hff": "--hf-file",
  "-hft": "--hf-token",
  "-dr": "--docker-repo",
  "-hfd": "--spec-draft-hf",
  "-hfrd": "--spec-draft-hf",
  "--hf-repo-draft": "--spec-draft-hf",
  "-md": "--spec-draft-model",
  "--model-draft": "--spec-draft-model",
  "-f": "--file",
  "-sysf": "--system-prompt-file",
  "-jf": "--json-schema-file",
  "-bf": "--binary-file",
  "-lcs": "--lookup-cache-static",
  "-lcd": "--lookup-cache-dynamic",
  "-o": "--output-file",
  "--output": "--output-file",
  "--kl-divergence-base": "--save-all-logits",
  "--audio": "--image",
  "--video": "--image",
  "--ui-config-file": "--webui-config-file",
  "--ui-mcp-proxy": "--webui-mcp-proxy",
  "-ag": "--agent"
};

// Fold a flag spelling onto its canonical long form via FLAG_ALIASES, or
// return it unchanged when it isn't a known short alias (already long, or an
// unrecognised flag entirely).
export function canonFlag(flag) {
  return FLAG_ALIASES[flag] || flag;
}

// The canonical flag a token names, the way the server's argv._canon reads
// it: the `flag=value` join form is split, `_` reads as `-` in a --long flag
// (llama.cpp rewrites it the same way), and every short/alternative spelling
// folds through FLAG_ALIASES. Every ownership screen below matches on this,
// so `-m x`, `-m=x`, `--model=x` and `--api_key x` are all caught.
export function screenCanon(tok) {
  let name = String(tok || "");
  if (name.includes("=")) name = name.slice(0, name.indexOf("="));
  if (name.startsWith("--")) name = name.replace(/_/g, "-");
  return canonFlag(name);
}

// The name a token is reported by: what the operator typed, without a
// joined `=value`.
function typedName(tok) {
  return tok.includes("=") ? tok.slice(0, tok.indexOf("=")) : tok;
}

// Offending flags in `text` whose canonical name is in `set`, by the spelling
// the operator typed (deduped, order-preserved).
function findByCanon(text, set) {
  const { tokens } = tokenizeFlags(text);
  const seen = new Set();
  const offenders = [];
  for (const tok of tokens) {
    if (!isFlagToken(tok) || !set.has(screenCanon(tok))) continue;
    const name = typedName(tok);
    if (!seen.has(name)) { seen.add(name); offenders.push(name); }
  }
  return offenders;
}

// Flags that reach outside the slot's tune: remote model fetches, host file
// reads/writes, listener / CORS / router settings, auth/TLS, and the --agent
// tools shortcut. hal0 manages all of these, and the server refuses them in a
// profile (slot.host_owned_flag_denied). Mirrors hal0.slots.argv.HOST_OWNED_FLAGS
// verbatim (kept in sync by tests/ui_contracts/test_flag_aliases_mirror.py;
// strict JSON so that parse holds).
export const HOST_OWNED_FLAGS = [
  "--agent",
  "--api-key",
  "--api-key-file",
  "--api-prefix",
  "--binary-file",
  "--context-file",
  "--control-vector",
  "--control-vector-scaled",
  "--cors-credentials",
  "--cors-headers",
  "--cors-methods",
  "--cors-origins",
  "--docker-repo",
  "--embd-gemma-default",
  "--file",
  "--fim-qwen-1.5b-default",
  "--fim-qwen-14b-spec",
  "--fim-qwen-30b-default",
  "--fim-qwen-3b-default",
  "--fim-qwen-7b-default",
  "--fim-qwen-7b-spec",
  "--gpt-oss-120b-default",
  "--gpt-oss-20b-default",
  "--grammar-file",
  "--hf-file",
  "--hf-repo",
  "--hf-token",
  "--image",
  "--in-file",
  "--json-schema-file",
  "--log-file",
  "--log-prompts-dir",
  "--logits-output-dir",
  "--lookup-cache-dynamic",
  "--lookup-cache-static",
  "--lora",
  "--lora-scaled",
  "--mcp-servers-config",
  "--mcp-servers-json",
  "--media-path",
  "--mmproj-url",
  "--model-url",
  "--models-autoload",
  "--models-dir",
  "--models-max",
  "--models-preset",
  "--negative-file",
  "--output-file",
  "--path",
  "--positive-file",
  "--prompt-cache",
  "--reuse-port",
  "--rpc",
  "--save-all-logits",
  "--server-base",
  "--slot-save-path",
  "--spec-default",
  "--spec-draft-hf",
  "--spec-draft-model",
  "--ssl-cert-file",
  "--ssl-key-file",
  "--system-prompt-file",
  "--tools",
  "--tools-runtime",
  "--tts-speaker-file",
  "--video-ffmpeg-dir",
  "--vision-gemma-12b-default",
  "--vision-gemma-4b-default",
  "--webui-config-file",
  "--webui-mcp-proxy"
];
const HOST_OWNED_SET = new Set(HOST_OWNED_FLAGS);

// The host-owned flags in `text`, by the spelling typed. Empty = clean.
export function findHostOwnedFlags(text) {
  return findByCanon(text, HOST_OWNED_SET);
}

// Every flag spelling llama.cpp (b11574) declares with no value argument: the
// token after one of these is the next argument, never its value. Mirrors
// hal0.slots.argv.VALUELESS_FLAGS verbatim (kept in sync by
// tests/ui_contracts/test_flag_aliases_mirror.py; strict JSON).
export const VALUELESS_FLAGS = [
  "--agent",
  "--backend-sampling",
  "--cache-idle-slots",
  "--cache-list",
  "--cache-prompt",
  "--check",
  "--check-tensors",
  "--completion-bash",
  "--cont-batching",
  "--context-shift",
  "--conversation",
  "--cors-credentials",
  "--cpu-moe",
  "--cpu-moe-draft",
  "--dflash",
  "--diffusion-visual",
  "--display-prompt",
  "--eagle3",
  "--embd-gemma-default",
  "--embedding",
  "--embeddings",
  "--escape",
  "--fim-qwen-1.5b-default",
  "--fim-qwen-14b-spec",
  "--fim-qwen-30b-default",
  "--fim-qwen-3b-default",
  "--fim-qwen-7b-default",
  "--fim-qwen-7b-spec",
  "--gpt-oss-120b-default",
  "--gpt-oss-20b-default",
  "--hellaswag",
  "--help",
  "--ids",
  "--ignore-eos",
  "--in-prefix-bos",
  "--interactive",
  "--interactive-first",
  "--jinja",
  "--kl-divergence",
  "--kv-offload",
  "--kv-unified",
  "--list-devices",
  "--log-disable",
  "--log-jsonl",
  "--log-prefix",
  "--log-timestamps",
  "--log-verbose",
  "--lora-init-without-apply",
  "--metrics",
  "--mmproj-auto",
  "--mmproj-offload",
  "--models-autoload",
  "--mtp",
  "--multiline-input",
  "--multiple-choice",
  "--nextn",
  "--no-agent",
  "--no-bos",
  "--no-cache-idle-slots",
  "--no-cache-prompt",
  "--no-cont-batching",
  "--no-context-shift",
  "--no-conversation",
  "--no-cors-credentials",
  "--no-display-prompt",
  "--no-escape",
  "--no-host",
  "--no-jinja",
  "--no-kv-offload",
  "--no-kv-unified",
  "--no-log-jsonl",
  "--no-log-prefix",
  "--no-log-timestamps",
  "--no-mmproj",
  "--no-mmproj-auto",
  "--no-mmproj-offload",
  "--no-models-autoload",
  "--no-op-offload",
  "--no-parse-special",
  "--no-perf",
  "--no-ppl",
  "--no-prefill-assistant",
  "--no-reasoning-preserve",
  "--no-repack",
  "--no-show-timings",
  "--no-skip-chat-parsing",
  "--no-slots",
  "--no-spec-draft-backend-sampling",
  "--no-ui",
  "--no-ui-mcp-proxy",
  "--no-warmup",
  "--no-webui",
  "--no-webui-mcp-proxy",
  "--offline",
  "--op-offload",
  "--parse-special",
  "--perf",
  "--ppl",
  "--prefill-assistant",
  "--process-output",
  "--prompt-cache-all",
  "--prompt-cache-ro",
  "--props",
  "--reasoning-preserve",
  "--repack",
  "--rerank",
  "--reranking",
  "--reuse-port",
  "--save-logits",
  "--show-count",
  "--show-statistics",
  "--show-timings",
  "--simple-io",
  "--single-turn",
  "--skip-chat-parsing",
  "--slots",
  "--spec-default",
  "--spec-draft-backend-sampling",
  "--spec-draft-cpu-moe",
  "--special",
  "--spm-infill",
  "--stdin",
  "--swa-full",
  "--ui",
  "--ui-mcp-proxy",
  "--usage",
  "--verbose",
  "--verbose-prompt",
  "--version",
  "--vision-gemma-12b-default",
  "--vision-gemma-4b-default",
  "--warmup",
  "--webui",
  "--webui-mcp-proxy",
  "--winogrande",
  "-ag",
  "-bs",
  "-cb",
  "-cl",
  "-cmoe",
  "-cmoed",
  "-cnv",
  "-e",
  "-h",
  "-i",
  "-if",
  "-kvo",
  "-kvu",
  "-mli",
  "-nkvo",
  "-no-ag",
  "-no-cnv",
  "-no-kvu",
  "-nocb",
  "-nr",
  "-pps",
  "-sp",
  "-st",
  "-tgs",
  "-v"
];
const VALUELESS_SET = new Set(VALUELESS_FLAGS);

// Whether a flag token may take the next token as its value, as the server's
// argv._takes_value decides: a joined `--flag=value` already carries it, and
// a valueless switch never takes one.
export function takesValue(tok) {
  if (tok.includes("=")) return false;
  const name = tok.startsWith("--") ? tok.replace(/_/g, "-") : tok;
  return !VALUELESS_SET.has(name);
}

// Bare arguments in `text` — a value with no flag in front of it, including a
// value after a joined `--flag=value` or after a valueless switch
// (`--metrics /etc/shadow`). The server refuses them
// (slot.positional_arg_denied). Empty = clean.
export function findPositionalArgs(text) {
  const { tokens } = tokenizeFlags(text);
  const out = [];
  let i = 0;
  while (i < tokens.length) {
    const tok = tokens[i];
    if (isFlagToken(tok)) {
      const takesNext = takesValue(tok) && i + 1 < tokens.length && !isFlagToken(tokens[i + 1]);
      i += takesNext ? 2 : 1;
    } else {
      out.push(tok);
      i += 1;
    }
  }
  return out;
}

// Group a flat token list into { flag, value } pairs (a flag consumes the next
// token as its value iff that token isn't itself a flag). Bare positionals are
// dropped from the pair view (they never carry a managed key).
export function flagPairs(tokens) {
  const out = [];
  let i = 0;
  while (i < tokens.length) {
    const tok = tokens[i];
    if (isFlagToken(tok)) {
      if (takesValue(tok) && i + 1 < tokens.length && !isFlagToken(tokens[i + 1])) {
        out.push({ flag: tok, canon: screenCanon(tok), value: tokens[i + 1] });
        i += 2;
      } else {
        out.push({ flag: tok, canon: screenCanon(tok), value: null });
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
  return findByCanon(text, MANAGED_CANON_SET);
}

// Diff two flag texts. `nextText` is where we are going, `prevText` where we
// are coming from. Keyed by canonical flag so a reordered `-b 2048` isn't a
// spurious change. Matching folds through canonFlag (the full FLAG_ALIASES
// mirror) so `-b 2048` against `--batch-size 1024` reads as ONE changed pair,
// not added+removed (#2211). Reported `flag` spellings stay the operator's own
// text; only the matching canonicalises.
// Returns { added, removed, changed, unchanged, diverged }.
//   added:     [{ flag, value }]  present in next, not in prev
//   removed:   [{ flag, value }]  present in prev, not in next
//   changed:   [{ flag, from, to }] same flag, different value
//   unchanged: number             identical pairs
export function diffFlags(nextText, prevText) {
  const mp = flagPairs(tokenizeFlags(nextText).tokens);
  const pp = flagPairs(tokenizeFlags(prevText).tokens);
  const key = (p) => canonFlag(p.flag);
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

// Whether two flag texts are equivalent modulo whitespace/order. Cheaper than a
// full diff when only the boolean is needed.
export function flagsEquivalent(a, b) {
  const d = diffFlags(a, b);
  return !d.diverged;
}
