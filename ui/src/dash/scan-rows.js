// Pure helpers for the Scan directory dialog's preview rows. Dependency-free
// so vitest can import them without the dashboard's window globals.

// A speculative-decoding drafter (ADR-0031) is never a registrable model row.
export function isDrafterRow(row) {
  return !!row && row.role === "draft";
}

// Short reason / pairing text for a drafter row; "" for an ordinary model row.
const DRAFTER_REASONS = {
  drafter_without_target: "no model beside it",
  drafter_mismatch: "does not fit the model beside it",
  drafter_unpaired: "a better drafter is paired",
  drafter_unreadable: "header unreadable",
};

export function drafterNote(row) {
  if (!isDrafterRow(row)) return "";
  if (row.pairs_with) return `drafter → ${row.pairs_with}`;
  if (row.reason) return `drafter · ${DRAFTER_REASONS[row.reason] || row.reason}`;
  return "drafter";
}
