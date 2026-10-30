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

// Device token → device class (gpu | npu | cpu | img). Mirrors the server's
// slots/profile_adopt.py: gpu-* is "gpu"; npu/cpu/img are themselves; anything
// else reads as cpu.
function deviceClassOf(device) {
	const d = String(device || "");
	if (d.startsWith("gpu")) return "gpu";
	return ["npu", "cpu", "img"].includes(d) ? d : "cpu";
}

/**
 * Does `profile` fit a slot? The client twin of the server's
 * `resolved_profile_fits_slot` (slots/profile_adopt.py), so the pickers never
 * offer a profile the server would refuse:
 *   · the slot's type must be one the profile supports
 *     (`supported_slot_types`, which follows its runtime family);
 *   · a profile that pins a `runner` must pin one whose device class matches
 *     the slot's device. The runner's class comes from the system-info
 *     `backends` catalog; an empty catalog (not loaded yet) vetoes nothing, and
 *     a key the loaded catalog does not carry never fits.
 * A profile pinning no runner fits any device.
 */
export function profileFitsSlot(profile, { type, device } = {}, backends = {}) {
	if (!profile) return false;
	const types = profile.supported_slot_types;
	if (type && Array.isArray(types) && !types.includes(type)) return false;
	if (device && profile.runner) {
		const cat = backends || {};
		if (Object.keys(cat).length === 0) return true;
		const row = cat[profile.runner];
		if (!row) return false;
		if (row.device_class && row.device_class !== deviceClassOf(device)) return false;
	}
	return true;
}

// A profile's context window as a short label ("32K"); a profile that sets
// none defers to the model's native window.
function ctxText(p) {
	const n = p?.ctx_size;
	if (n == null || n === "") return "model native";
	const v = Number(n);
	if (v >= 1024 && v % 1024 === 0) return `${v / 1024}K`;
	return String(v);
}
function thinkingText(p) {
	return p?.enable_thinking === true ? "on" : p?.enable_thinking === false ? "off" : "auto";
}
const effortText = (p) => p?.reasoning_effort || "default";
const budgetText = (p) =>
	p?.reasoning_budget == null || p.reasoning_budget === -1 ? "unlimited" : String(p.reasoning_budget);
const offAuto = (v) => (v === "off" ? "off" : "auto");
const specText = (p) => (p?.speculation === "off" || p?.speculation === "ngram" ? p.speculation : "auto");

// The typed tune fields that differ between the outgoing and incoming profile,
// as [{ label, from, to }] — only the ones that actually change.
function tuneChanges(next, prev) {
	const rows = [
		["context", ctxText(prev), ctxText(next)],
		["thinking", thinkingText(prev), thinkingText(next)],
		["effort", effortText(prev), effortText(next)],
		["budget", budgetText(prev), budgetText(next)],
		["vision", offAuto(prev?.vision), offAuto(next?.vision)],
		["speculation", specText(prev), specText(next)],
	];
	return rows
		.filter(([, from, to]) => from !== to)
		.map(([label, from, to]) => ({ label, from, to }));
}

/**
 * Apply preview for a profile pick — every consequence of switching a slot to
 * another profile, in one structure, computed BEFORE anything is written
 * (mockup panel 12 for the drawer's Save; panel 02 for the slot card's apply
 * confirm). ADR-0029: the profile IS the tune, so a swap replaces the whole
 * tune — flags, context, thinking, vision, speculation and runtime — and nothing from
 * the model is layered underneath.
 *
 * The lines are DERIVED, never re-guessed: the runtime and lane come out of
 * the same hw-cascade.js `applyRunnerChoice` the Hardware group's Runtime
 * select drives (and that the server's profile-wins reconcile mirrors, Task
 * 5), and the flag count comes out of flags-tune.js's shlex-lite `diffFlags`.
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
 * @param currentProfile      the OUTGOING profile row (null = none)
 *
 * Returns `{ runtime: { unchanged, title, lanes }, lane, flags, tune,
 * restart }` or null. `flags` counts the flag pairs the swap adds, changes or
 * drops; `tune` lists the typed fields (context / thinking / effort / budget / vision / speculation)
 * that differ.
 */
export function profileApplyPreview({
	profile,
	backends,
	options,
	baselineRunner,
	currentDevice,
	currentProfile,
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

	// "changes N flags": the flag pairs that differ between the outgoing and
	// the incoming profile. The profile is the whole tune now, so a pair the
	// outgoing profile had and the incoming one lacks counts too.
	const d = diffFlags(profile.flags || "", currentProfile?.flags || "");
	return {
		runtime,
		lane,
		flags: d.added.length + d.changed.length + d.removed.length,
		tune: tuneChanges(profile, currentProfile),
		restart: true,
	};
}
