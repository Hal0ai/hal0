// halogen-footprint.js — the slot drawer's live memory readout for a halogen slot.
//
// `GET /api/slots/{name}/resolved` carries `footprint` for a halogen slot: the
// server's own figure for what the layout holds once loaded (ADR-0039: every
// memory figure has one owner, so nothing here recomputes one), the lookup-table
// cache the operator is advised to leave free, the host's free pool, and the
// advisory verdict of the one against the other. This turns that into the line
// under the engine settings. `null` or an absent footprint is "no readout"
// (another runtime, nothing bound, or no figure), never an error.
//
// Pure: a function of its argument, no React, no window globals.

const GIB = 1024;

// Whole GiB from 10 up, one decimal below it (a 3.5 GiB layout must not read
// as "~4"), and no trailing ".0".
function gib(mb) {
	const g = mb / GIB;
	return g >= 10 ? String(Math.round(g)) : g.toFixed(1).replace(/\.0$/, "");
}

const finite = (n) => typeof n === "number" && Number.isFinite(n);

// The words match the model picker's fit chip (fit-chip.jsx). "unknown" has no
// entry: the host gave no reading, and a missing signal must not read as a
// verdict (warn, never block).
const VERDICTS = {
	fits: { tone: "ok", word: "fits", title: "Fits in the memory the box has free now" },
	tight: {
		tone: "warn",
		word: "tight",
		title: "Within 10% of the free memory: it may load with swap pressure",
	},
	exceeds: {
		tone: "err",
		word: "won't fit",
		title: "More than is free now: free memory or unload a slot first",
	},
	exceeds_total: {
		tone: "err",
		word: "won't fit",
		title: "More than the box has in total: it will not load here",
	},
};

/**
 * The readout for one footprint.
 *
 * @param {{resident_mb?: number, advisory_cache_mb?: number | null,
 *   host_free_mb?: number | null, verdict?: string} | null | undefined} footprint
 *   the `/resolved` payload's `footprint`.
 * @returns {{text: string, verdict: {tone: string, word: string, title: string} | null} | null}
 *   `null` when there is no figure to show.
 */
export function footprintLine(footprint) {
	if (!footprint || !finite(footprint.resident_mb) || footprint.resident_mb <= 0) return null;
	const parts = [`This layout: ~${gib(footprint.resident_mb)} GiB resident`];
	if (finite(footprint.advisory_cache_mb) && footprint.advisory_cache_mb > 0) {
		parts.push(`+${gib(footprint.advisory_cache_mb)} GiB lookup-table cache advised`);
	}
	if (finite(footprint.host_free_mb)) {
		parts.push(`box has ${gib(footprint.host_free_mb)} GiB free`);
	}
	return { text: parts.join(" · "), verdict: VERDICTS[footprint.verdict] ?? null };
}
