// fit-verdicts.js — the GTT fit verdicts' tone and word, in one place.
//
// `gtt_feasibility_verdict` answers fits / tight / exceeds / exceeds_total for a
// model that is not loaded yet. The model picker's fit chip (fit-chip.jsx) and
// the slot drawer's halogen memory readout (halogen-footprint.js) both word
// them, so the two can never drift. "unknown" has no entry: the host gave no
// reading, and a missing signal must not read as a verdict (warn, never block).
export const FIT_VERDICTS = {
	fits: { tone: "ok", word: "fits" },
	tight: { tone: "warn", word: "tight" },
	exceeds: { tone: "err", word: "won't fit" },
	exceeds_total: { tone: "err", word: "won't fit" },
};
