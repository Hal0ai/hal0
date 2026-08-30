// Hardware cascade for the slot edit drawer: Runner Image → Backend.
//
// The drawer used to expose three coupled controls (Device, Runner Image,
// Runner Binary) that could be driven into mismatched states — a pinned image
// shipping no binary for the picked device dead-ended the form. This module
// makes the mismatch unrepresentable: the operator picks an image (or the
// release-catalog default), and the Backend dropdown enumerates exactly the
// (binary, backend) pairs that image can actually launch for this slot. The
// slot's persisted `device` becomes a derived fact of the chosen pair.
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

function deviceBackend(device) {
	const d = String(device || "").toLowerCase();
	if (!d) return "";
	return d.startsWith("gpu-") ? d.slice(4) : d;
}

// Backends a runner can actually execute — supported_backends (the §4
// fit-check list), falling back to its single declared `backend`. Empty =
// backend-agnostic, never a veto.
function runnerBackends(runner) {
	const sup = runner?.supported_backends;
	if (Array.isArray(sup) && sup.length > 0) return sup;
	return runner?.backend ? [runner.backend] : [];
}

/** Stable option value encoding one (binary, backend) pair. */
export function optionValue(binary, backend) {
	return `${binary}::${backend || ""}`;
}

/**
 * Enumerate the Backend dropdown for a slot.
 *
 * @param backends    system-info RUNNER_IMAGES map (key → runner row)
 * @param pinnedImage trimmed image_pin ('' = release-catalog default)
 * @param device      the slot's (pending) device enum, e.g. "gpu-rocm"
 * @param slotType    slot.type — gates runner families via FAMILY_SLOT_TYPES
 *
 * Returns `{ options, fallback, emptyPin }`:
 * - options: [{binary, backend, device, specialties}] — the dropdown rows
 * - fallback: pinned ref is outside the catalog, so options are the
 *   device-fit union, not an enumeration of the image
 * - emptyPin: a catalog image is pinned, but nothing in it fits this slot
 */
export function backendOptions({ backends, pinnedImage, device, slotType }) {
	const cat = backends || {};
	const pin = String(pinnedImage || "").trim();
	const devClass = deviceClassOf(device);
	const devBe = deviceBackend(device);
	const pinInCatalog =
		!!pin && Object.values(cat).some((r) => r?.image === pin);
	// A pinned ref the catalog doesn't know (debug build, rollback, hand-edited
	// TOML) can't be enumerated — fall back to the device-fit union so the
	// operator is never dead-ended, and let the caller show a caution.
	const fallback = !!pin && !pinInCatalog;
	const enumerable = pinInCatalog ? pin : null;

	const options = [];
	for (const [key, r] of Object.entries(cat)) {
		if (!r) continue;
		if (enumerable && r.image !== enumerable) continue;
		const types = FAMILY_SLOT_TYPES[r.runtime_family];
		if (types && slotType && !types.includes(slotType)) continue;
		const specialties = Array.isArray(r.supports?.specialties)
			? r.supports.specialties
			: undefined;
		const sups = runnerBackends(r);
		if (sups.length === 0) {
			// Backend-agnostic runner: one option, device untouched.
			if (r.device_class && r.device_class !== devClass) continue;
			options.push({ binary: key, backend: "", device, specialties });
			continue;
		}
		for (const be of sups) {
			const target = BACKEND_DEVICE[be];
			if (target) {
				// A mapped backend may flip the device — but only within the
				// slot's device class (rocm↔vulkan on gpu). Crossing classes is
				// a re-create, not an edit.
				if (deviceClassOf(target) !== devClass) continue;
				if (r.device_class && r.device_class !== devClass) continue;
				options.push({ binary: key, backend: be, device: target, specialties });
			} else {
				// Unmapped token (e.g. npu): only valid when it IS the slot's
				// backend already — never a flip.
				if (be !== devBe) continue;
				if (r.device_class && r.device_class !== devClass) continue;
				options.push({ binary: key, backend: be, device, specialties });
			}
		}
	}

	return {
		options,
		fallback,
		emptyPin: !!enumerable && options.length === 0,
	};
}

/**
 * Resolve a Backend dropdown pick to the state it drives.
 * Unknown values (the out-of-vocab persisted pair rendered as its own option)
 * set the binary and leave the device alone.
 */
export function applyBackendChoice(options, value, currentDevice) {
	const hit = (options || []).find(
		(o) => optionValue(o.binary, o.backend) === value,
	);
	if (hit) return { binary: hit.binary, device: hit.device };
	const binary = String(value || "").split("::")[0];
	return { binary, device: currentDevice };
}

/**
 * The option value matching the slot's persisted (binary, device) pair.
 * '' = no binary pinned (auto); null = pair exists but no option matches
 * (out-of-vocab — the caller prepends a self-option so the drawer never
 * silently rewrites a persisted value).
 */
export function selectedBackendValue({ binary, device, options }) {
	if (!binary) return "";
	const devBe = deviceBackend(device);
	const exact = (options || []).find(
		(o) => o.binary === binary && (o.backend || "") === devBe,
	);
	if (exact) return optionValue(exact.binary, exact.backend);
	const agnostic = (options || []).find(
		(o) => o.binary === binary && !o.backend,
	);
	if (agnostic) return optionValue(agnostic.binary, agnostic.backend);
	return null;
}
