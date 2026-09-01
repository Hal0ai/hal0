// Hardware cascade for the slot edit drawer: Runtime → Lane.
//
// The drawer used to expose three coupled controls (Device, Runner Image,
// Runner Binary) that could be driven into mismatched states — a pinned image
// shipping no binary for the picked device dead-ended the form, and the
// image-first Backend cascade this module used to hold (backendOptions/
// cataloguePinOptions/selectedBackendValue/applyBackendChoice/optionValue,
// removed with D3) enumerated (binary, backend) PAIRS rather than runners.
// Runtime-first (below) makes the pick itself unrepresentable-wrong: the
// operator picks a runner directly (runnerOptions), and the lane — when that
// runner actually offers more than one — is a secondary choice within it
// (laneValues/applyRunnerChoice). The slot's persisted `device` is a derived
// fact of the runner + lane chosen, never a separately-editable field here.
//
// Pure functions only — the JSX in slot-modals.jsx owns state and rendering.

// backend token → the device it drives (map_backend_to_device's FE mirror,
// same table slot-modals.jsx keeps for the cross-device profile picker).
const BACKEND_DEVICE = {
	rocm: "gpu-rocm",
	vulkan: "gpu-vulkan",
	cuda: "gpu-cuda",
	cpu: "cpu",
};

// Slot types each runner family can serve — mirrors the backend's
// profiles._supported_slot_types(runtime_family). Unknown/absent family never
// vetoes (a new backend runtime shows up rather than vanishing).
const FAMILY_SLOT_TYPES = {
	"llama-server": ["llm", "embedding", "reranking"],
	flm: ["llm", "embedding", "transcription"],
	kokoro: ["tts"],
	qwen3tts: ["tts"],
	comfyui: ["image"],
};

function deviceClassOf(device) {
	const d = String(device || "");
	return d.startsWith("gpu")
		? "gpu"
		: ["npu", "cpu", "img"].includes(d)
			? d
			: "cpu";
}

// Lane token → the hw capability flag that gates it (systemInfo.hardware's
// computeCapable/vulkanCapable, threaded through as `hw`). A lane with no
// entry here (e.g. cpu) is never hardware-vetoed.
const LANE_HW = { rocm: "rocm", vulkan: "vulkan", cuda: "cuda" };

/**
 * Enumerate the runner-first Backend dropdown for a slot: one option per
 * runner (not per lane), each carrying the lanes (backends) it can serve.
 * This is the replacement for the image-first backendOptions() cascade —
 * the operator now picks a runner directly, and the lane (when the runner
 * offers more than one) is a secondary choice within it.
 *
 * @param backends system-info `backends` map (key → row; Task 2 shape)
 * @param device   the slot's (pending) device enum, e.g. "gpu-rocm"
 * @param slotType slot.type — gates runner families via FAMILY_SLOT_TYPES
 * @param hw       host capability flags, e.g. { rocm, vulkan, cuda } — from
 *                 systemInfo.hardware (computeCapable/vulkanCapable). Missing
 *                 flags never veto (unknown hw applies no filter).
 *
 * Returns `{ options }`, where each option is
 * `{ key, title, blurb, lanes, state, isDefault, provenance }`.
 */
export function runnerOptions({ backends, device, slotType, hw }) {
	const devClass = deviceClassOf(device);
	const options = [];
	for (const [key, r] of Object.entries(backends || {})) {
		if (!r) continue;
		if (r.device_class && r.device_class !== devClass) continue;
		const types = FAMILY_SLOT_TYPES[r.runtime_family];
		if (types && slotType && !types.includes(slotType)) continue;
		const lanes = Array.isArray(r.supported_backends)
			? r.supported_backends
			: [];
		// Hardware filter: hide a runtime when hw is known and NONE of its
		// lanes is feasible on this box. Unknown hw ({}) never vetoes.
		if (
			hw &&
			lanes.length > 0 &&
			lanes.every((l) => LANE_HW[l] && hw[LANE_HW[l]] === false)
		)
			continue;
		options.push({
			key,
			title: r.title || key,
			blurb: r.blurb || "",
			lanes,
			state: r.state,
			isDefault: !!r.is_default,
			provenance: r.provenance || undefined,
		});
	}
	options.sort(
		(a, b) => b.isDefault - a.isDefault || a.title.localeCompare(b.title),
	);
	return { options };
}

/**
 * Lane values for a runner option's backend picker: '' (Auto) plus each
 * declared lane, but only when the runner actually offers a choice — a
 * single-lane or backend-agnostic runner has nothing to pick between.
 */
export function laneValues(option) {
	const lanes = option?.lanes || [];
	return lanes.length > 1 ? ["", ...lanes] : [];
}

/**
 * Resolve a runner pick to the state it drives. A single-lane GPU runner
 * maps its one lane to the device (same BACKEND_DEVICE table as the image-
 * first cascade); a multi-lane or backend-agnostic runner leaves the
 * slot's current device untouched (the lane, if any, is a separate pick).
 */
export function applyRunnerChoice({ options, key, currentDevice }) {
	const hit = (options || []).find((o) => o.key === key);
	if (!hit) return { binary: key, device: currentDevice };
	const lanes = hit.lanes || [];
	if (lanes.length === 1) {
		const target = BACKEND_DEVICE[lanes[0]];
		if (target && deviceClassOf(target) === deviceClassOf(currentDevice))
			return { binary: key, device: target };
	}
	return { binary: key, device: currentDevice };
}

/**
 * The option key matching the slot's persisted binary.
 * '' = no binary pinned (auto); null = a binary is persisted but no option
 * matches it (out-of-vocab — the caller renders it as its own self-option
 * so the drawer never silently rewrites a persisted value).
 */
export function selectedRunnerKey({ binary, options }) {
	if (!binary) return "";
	return (options || []).some((o) => o.key === binary) ? binary : null;
}

// Backend token of a device enum ("gpu-rocm" → "rocm") — small local mirror
// kept for archFitWarning's HW-gated-default derivation.
function deviceBackendToken(device) {
	const d = String(device || "").toLowerCase();
	if (!d) return "";
	return d.startsWith("gpu-") ? d.slice(4) : d;
}

/**
 * Model↔runner GGUF-arch fit-check (hal0#2118) — FE mirror of the backend's
 * `_arch_fit_warning` in api/routes/slots.py. WARN, never block: returns a
 * message string when the bound model's detected `general.architecture` is on
 * the effective runner's `unsupported_archs` denylist (system-info runner
 * rows), else null. An image_pin disarms the check — the pin IS the #2118
 * escape hatch, and the catalog can't know a pinned image's arch table.
 * `altRef` (an image ref known to load the arch) turns the warning into an
 * actionable hint.
 *
 * @param arch     model row's `architecture` (GGUF general.architecture id)
 * @param device   the slot's (pending) device enum, e.g. "gpu-rocm"
 * @param binary   the slot's BINARY runner key ('' = HW-gated default)
 * @param imagePin trimmed image_pin ('' = release-catalog default)
 * @param backends system-info RUNNER_IMAGES map (key → runner row)
 * @param altRef   optional catalogued image ref that CAN load the arch
 */
export function archFitWarning({ arch, device, binary, imagePin, backends, altRef = "" }) {
	if (!arch || String(imagePin || "").trim()) return null;
	const cat = backends || {};
	let key = String(binary || "");
	if (!key) {
		// No BINARY = the HW-gated default the launcher derives from the
		// device — runner_for_backend's FE mirror (cuda → cuda, cpu → cpu,
		// any other GPU lane → rocmfpx).
		const be = deviceBackendToken(device);
		key = be === "cuda" ? "cuda" : be === "cpu" ? "cpu" : "rocmfpx";
	}
	const runner = cat[key];
	// Unknown runner key, a non-GGUF runtime lane, or an older system-info
	// payload without the denylist: no opinion, never a warning.
	if (!runner || runner.format_arch !== "gguf") return null;
	const deny = runner.unsupported_archs;
	if (!Array.isArray(deny) || !deny.includes(arch)) return null;
	let msg =
		`⚠ Model architecture "${arch}" is not supported by the ${key} ` +
		"runner's llama.cpp build — the model will fail at load and the slot " +
		"will crash-loop.";
	if (altRef) msg += ` Pin "${altRef}" (Runner Image) to serve it.`;
	return msg;
}
