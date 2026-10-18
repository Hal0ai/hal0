// Pure helpers for the Add-from-Hugging-Face dialog's Drafter row (ADR-0031 §5).
// Dependency-free so vitest can import them without the dashboard's window globals.

// Stable identity of a drafter candidate: the same file name can live in two repos.
export function draftKey(d) {
  return d ? `${d.repo}::${d.id}` : "";
}

// Candidates from a pasted drafter repo come first; duplicates drop.
export function mergeDrafts(manual, found) {
  const seen = new Set();
  const out = [];
  for (const d of [...(manual || []), ...(found || [])]) {
    const k = draftKey(d);
    if (!seen.has(k)) { seen.add(k); out.push(d); }
  }
  return out;
}

export function draftSourceLine(draft, mainRepo) {
  if (!draft) return "none found — paste a repo below";
  return draft.repo === mainRepo ? "found in this repo" : `found in ${draft.repo}`;
}

// "matches: hidden 5120, 6 target layers, block 8" from the header facts that are
// present, or the first refusal reason on a mismatch.
export function draftVerdict(resp) {
  if (!resp) return "";
  if (resp.fit && resp.fit.ok === false) return (resp.fit.reasons && resp.fit.reasons[0]) || "does not fit this model";
  const dr = resp.draft || {};
  const parts = [];
  if (dr.embedding_length != null) parts.push(`hidden ${dr.embedding_length}`);
  if (Array.isArray(dr.target_layers) && dr.target_layers.length) parts.push(`${dr.target_layers.length} target layers`);
  if (dr.block_size != null) parts.push(`block ${dr.block_size}`);
  return parts.length ? `matches: ${parts.join(", ")}` : "matches";
}

// LiLiCorr is a dflash variant but runners list it as its own draft kind.
export function draftKindNeeded(resp) {
  if (!resp) return "";
  return resp.variant === "lilicorr" ? "lilicorr" : resp.kind || "";
}

// `backends` is GET /api/system-info's `backends` map. Informational only.
export function missingRunnerNote(backends, kind) {
  if (!backends || !kind) return "";
  const ok = Object.values(backends).some(
    b => b && b.state === "installed" && Array.isArray(b.supports && b.supports.draft_kinds) && b.supports.draft_kinds.includes(kind),
  );
  return ok ? "" : `no installed runner can use this drafter yet (needs ${kind})`;
}
