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
 * - options: [{binary, backend, device, specialties, provenance}] — the
 *   dropdown rows; `provenance` is the runner row's build-provenance
 *   object (h0/runner-provenance, from system-info) or undefined
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
		// Build provenance of the runner's effective image (system-info's
		// `provenance` field, h0/runner-provenance) — passed through so the
		// option label can name the llama.cpp build; absent on an older
		// backend payload, and the label then renders exactly as today.
		const provenance = r.provenance || undefined;
		const sups = runnerBackends(r);
		if (sups.length === 0) {
			// Backend-agnostic runner: one option, device untouched.
			if (r.device_class && r.device_class !== devClass) continue;
			options.push({ binary: key, backend: "", device, specialties, provenance });
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
				options.push({
					binary: key,
					backend: be,
					device: target,
					specialties,
					provenance,
				});
			} else {
				// Unmapped token (e.g. npu): only valid when it IS the slot's
				// backend already — never a flip.
				if (be !== devBe) continue;
				if (r.device_class && r.device_class !== devClass) continue;
				options.push({ binary: key, backend: be, device, specialties, provenance });
			}
		}
	}

	return {
		options,
		fallback,
		emptyPin: !!enumerable && options.length === 0,
	};
}

// Repo part of an image ref — strips a @sha256 digest, then a trailing tag
// (a ':' segment with no '/', so a registry port like localhost:5000/x
// survives). FE mirror of the backend's `_repo_of` (api/routes/runner_images).
function repoOfRef(ref) {
	const body = String(ref || "").split("@")[0];
	const i = body.lastIndexOf(":");
	return i > -1 && !body.slice(i + 1).includes("/") ? body.slice(0, i) : body;
}

/**
 * Catalogue rows offerable as an image pin beyond the release catalog.
 *
 * The Runner Image dropdown historically listed ONLY the RUNNER_IMAGES
 * release-catalog refs (system-info `backends`), so a catalogued image with
 * no runner family — e.g. hal0-combined-upstream, which #2118 says is wired
 * to slots ONLY via per-slot image_pin — was reachable solely through the
 * free-text "Custom image ref…" hatch. This enumerates the catalogue rows
 * that are actually pinnable right now:
 *
 * - downloaded on this box (a pin to a missing image just crash-loops), and
 * - not a repo the release catalog already lists (those rows are the same
 *   lineage the catalog options resolve — offering both would show one image
 *   twice under two spellings).
 *
 * Catalogue rows carry no runtime_family, so they're assumed llama-server
 * (every uncatalogued-family row today is a llama.cpp fork) and offered only
 * for the slot types that family serves. A wrong pick degrades to the
 * existing out-of-catalog pin path (cascade fallback + caution), never a
 * dead end.
 *
 * @param rows          GET /api/runner-images rows (RunnerImage[])
 * @param catalogImages the release-catalog refs already in the dropdown
 * @param slotType      slot.type — gates via FAMILY_SLOT_TYPES["llama-server"]
 * @returns [{id, ref, notes}] — ref is the pinnable `repo:tag`
 */
export function cataloguePinOptions({ rows, catalogImages, slotType }) {
	if (slotType && !FAMILY_SLOT_TYPES["llama-server"].includes(slotType))
		return [];
	const catalogRepos = new Set((catalogImages || []).map(repoOfRef));
	const out = [];
	for (const r of rows || []) {
		if (!r || !r.downloaded || !r.image || !r.tag) continue;
		if (catalogRepos.has(r.image)) continue;
		out.push({ id: r.id, ref: `${r.image}:${r.tag}`, notes: r.notes || "" });
	}
	return out;
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
