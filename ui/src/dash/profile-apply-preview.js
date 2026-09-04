// profile-apply-preview.js — "what does applying this profile actually do?",
// as one pure structure.
//
// Extracted from slot-modals.jsx (where it grew alongside the slot drawer's
// Profile row) so the two surfaces that ask the question — the drawer's
// `slot-profile-preview` box and the slot card's `infer-profile-preview`
// confirm — import ONE definition. A view module is the wrong home for it:
// the card pane would have had to import the drawer god-module to reach it,
// inverting the layering (and dragging the whole modal graph into the pane's
// bundle). It sits beside flags-tune.js / hw-cascade.js, the other pure
// derivations it is built out of.
//
// No React, no hooks, no window globals — everything here is a function of
// its arguments.

import { applyRunnerChoice } from "./hw-cascade.js";
import { deviceBackend } from "@/api/hooks/useRuntimes";
import { diffFlags } from "./flags-tune.js";

// Lane token → display title (hw-cascade.js's runnerOptions/laneValues deal
// only in the raw backend tokens — rocm/vulkan/cuda/cpu — so the display copy
// lives here). Exported because the slot drawer names lanes in its own prose
// with the same table.
export const LANE_TITLE = {
	rocm: "ROCm",
	vulkan: "Vulkan",
	cuda: "CUDA",
	cpu: "CPU",
};
export function laneTitle(lane) {
	return LANE_TITLE[lane] || lane;
}

// Concatenate a flag base with an overlay the way the launcher does: the
// profile's tune is appended AFTER the model tune, and diffFlags' pair map is
// last-wins, so the joined text reads as the effective launch tune.
function joinFlags(base, overlay) {
	return [base, overlay]
		.map((s) => String(s || "").trim())
		.filter(Boolean)
		.join(" ");
}

/**
 * Apply preview for a profile pick — every consequence of "profile wins" in
 * one structure, computed BEFORE anything is written (mockup panel 12 for the
 * drawer's Save; panel 02 for the slot card's apply confirm).
 *
 * The lines are DERIVED, never re-guessed: the runtime and lane come out of
 * the same hw-cascade.js `applyRunnerChoice` the Hardware group's Runtime
 * select drives (and that the server's profile-wins reconcile mirrors, Task
 * 5), and the flag count comes out of flags-tune.js's shlex-lite `diffFlags`
 * — the same tokenizer the model drawer's divergence hint uses.
 *
 * A line with nothing true to say is OMITTED rather than faked (`lane: null`,
 * `flags: 0`), with ONE exception the mockup calls out: a profile pinning no
 * runtime (Auto) reports runtime/lane as `unchanged` instead of dropping
 * them, so "this profile has no runtime opinion" can't be misread as "the
 * preview failed to load".
 *
 * @param profile             the SELECTED profile row (null → no preview)
 * @param backends            system-info `backends` catalog (key → row)
 * @param options             runnerOptions() rows for this slot
 * @param baselineRunner      the slot's FROZEN persisted binary ('' = auto)
 * @param currentDevice       the slot's pending device enum, e.g. "gpu-rocm"
 * @param modelFlags          the bound model's stamped tune (defaults.extra_args)
 * @param currentProfileFlags the OUTGOING profile's tune ('' = none)
 *
 * Returns `{ runtime: { unchanged, title, lanes }, lane, flags, restart }`
 * or null.
 */
export function profileApplyPreview({
	profile,
	backends,
	options,
	baselineRunner,
	currentDevice,
	modelFlags,
	currentProfileFlags,
}) {
	if (!profile) return null;
	const key = profile.runner || "";
	const cat = (backends || {})[key] || null;
	const hit = (options || []).find((o) => o.key === key) || null;
	// Lanes from the cascade row first, the raw catalog row second; an
	// out-of-catalog key claims NO lane rather than borrowing one.
	const lanes = key
		? hit?.lanes ||
			(Array.isArray(cat?.supported_backends) ? cat.supported_backends : [])
		: [];
	const runtime = {
		unchanged: !key || key === (baselineRunner || ""),
		title: key ? hit?.title || cat?.title || key : null,
		lanes,
	};

	// Post-save device truth. applyRunnerChoice moves the device only for a
	// single-lane runner inside the same device class, and returns the current
	// device untouched for an unknown key — so an invented lane move is
	// unrepresentable here.
	const nextDevice = key
		? applyRunnerChoice({ options, key, currentDevice }).device
		: currentDevice;
	const curLane = deviceBackend(currentDevice);
	const nextLane = deviceBackend(nextDevice);
	let lane = null;
	if (nextLane !== curLane) {
		lane = {
			unchanged: false,
			from: laneTitle(curLane),
			to: laneTitle(nextLane),
		};
	} else if (curLane && (!key || lanes.length !== 1)) {
		// Auto, a multi-lane runtime (the lane stays a live choice) or an
		// unknown key: say the lane holds. A single-lane runtime already
		// sitting on its one lane — or a slot with no lane to name at all —
		// has nothing to report, and an empty "lane → unchanged ()" is
		// exactly the faked line the panel forbids.
		lane = { unchanged: true, from: laneTitle(curLane), to: laneTitle(curLane) };
	}

	// "replaces N flags": the pairs in THIS profile's tune that the slot does
	// not already launch with — diffed against the effective current tune
	// (model stamp + the outgoing profile's overlay), so switching between two
	// profiles that agree on a flag doesn't bill it as a change.
	const d = diffFlags(
		profile.flags || "",
		joinFlags(modelFlags, currentProfileFlags),
	);
	return {
		runtime,
		lane,
		flags: d.added.length + d.changed.length,
		restart: true,
	};
}
