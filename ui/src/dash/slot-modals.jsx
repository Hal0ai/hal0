// hal0 dashboard — Slot interactive surface
// Create-slot modal, Edit-slot drawer, inline swap popover, overflow menu,
// empty/error SlotCard variants, log drawer. Wired into slots.jsx via
// window globals. All persistence + lifecycle calls go through the typed
// `useSlots` mutation hooks — no toast-only stubs survive in this file.

import {
	useSlotEdit,
	useSlotDefaults,
	useSlotImagePull,
	useSlotRestart,
	useSlotLoad,
	useSlotSwap,
	useSlotResolved,
} from "@/api/hooks/useSlots";
import { useHardware } from "@/api/hooks/useHardware";
import { useModels, usePullJob } from "@/api/hooks/useModels";
import { useProfiles } from "@/api/hooks/useProfiles";
import { useSystemInfo, deviceBackend } from "@/api/hooks/useRuntimes";
import { useChatTemplates } from "@/api/hooks/useChatTemplates";
import { useMetaEnums } from "@/api/hooks/useMeta";
import { useSlotLogsStream } from "@/api/hooks/useLogs";
import { ENDPOINTS } from "@/api/endpoints";
import { normalizeApiModel, isUpstreamModel } from "@/lib/normalizeApiModel";
import { stateChipClassForSlot, slotButtonPhase } from "./slot-status.js";

const {
	useState: useStateSM,
	useEffect: useEffectSM,
	useRef: useRefSM,
	useCallback: useCallbackSM,
} = React;

// Map a slot lifecycle state to a chip color class.
//   running healthy/serving → green (ok); starting/pulling → amber (warn);
//   crashed/error → red (err); stopped/anything else → neutral grey.
//
// N1: accepts either a state string or a full slot object; both delegate
// to stateChipClassForSlot() from slot-status.js (the string overload
// wraps it in a minimal slot shape).
function stateChipClass(stateOrSlot) {
	if (typeof stateOrSlot === "string" || stateOrSlot == null) {
		return stateChipClassForSlot({ state: String(stateOrSlot || "") });
	}
	return stateChipClassForSlot(stateOrSlot);
}

// Model rows come from /api/models and are normalized by the SHARED
// lib/normalizeApiModel (imported above), which tolerates both the
// registry/API shape (capabilities + backends + size_bytes + name +
// hf_repo) and the legacy HAL0_DATA seed shape (labels + device + size +
// longName + repo + type — mock.ts fallback / γ-suite race). This file
// used to carry its own divergent copy whose deriveType missed
// tool-calling/vision → 'llm', hiding those models from every slot's
// model picker. NEVER ship HAL0_DATA model ids to the backend — they're
// fictional (`qwen3.6-27b-mtp` etc.) and the slot orchestrator correctly
// rejects them against the real registry.

// One shared compatible-models filter for all three slot surfaces (create
// modal, edit drawer, swap popover). Takes the raw /api/models list, normalizes
// it, and filters to the requested `type`, hiding ROCmFP4-quantized models
// whenever the target backend isn't rocm (those weights only run on the rocm
// fork binary). Previously the create modal filtered on type ALONE and could
// offer rocmfp4 models that the backend then rejects — this closes that gap.
function compatibleModels(models, { type, backend }) {
	// Upstream-advertised rows are excluded outright: a slot binds a local
	// file path, and these rows have none (they'd render as "will pull" and
	// then 422 — there is no HF source to pull them from).
	return (models ?? [])
		.map(normalizeApiModel)
		.filter(
			(m) =>
				m.type === type &&
				!isUpstreamModel(m) &&
				!(
					Array.isArray(m.tags) &&
					m.tags.includes("rocmfp4") &&
					backend !== "rocm"
				),
		);
}

// ─── Create-slot modal ──────────────────────────────────────────
// Decomposed (D2) into dash/slots/CreateSlotModal.jsx — the create flow is now
// a pure instance: pick a model (it carries tune/device/runner) + name it. The
// old profile/image/device fields moved to the model drawer.

// ─── Edit-slot drawer ───────────────────────────────────────────
// Cheap client-side guard for the freeform extra_args field: catch the one
// error that would make the backend shlex.split() throw — unbalanced quotes.
// Anything subtler (unknown llama-server flags) is the server's job to reject;
// this just stops an obviously-malformed string from being saved/regenerated.
function validateExtraArgs(s) {
	if (!s) return null;
	let inSingle = false;
	let inDouble = false;
	for (let i = 0; i < s.length; i++) {
		const c = s[i];
		if (c === "'" && !inDouble) inSingle = !inSingle;
		else if (c === '"' && !inSingle) inDouble = !inDouble;
	}
	if (inSingle || inDouble) return "Unbalanced quote";
	return null;
}

// ''/null/'auto' all mean "no chat-template override" — the backend
// normalizes them to None everywhere (_chat_template_or_none), so a slot
// TOML that still carries chat_template = "auto" must not render as an
// active override (and gets cleaned off disk on the next template save).
function normTemplate(v) {
	return v && v !== "auto" ? v : "";
}

// Backends a runner image can actually execute — RUNNER_IMAGES.supported_backends
// (the §4 fit-check list), falling back to its single declared `backend` on an
// older system-info payload. Empty = backend-agnostic, i.e. never a veto. ONE
// helper so the Runner select's option filter and the fit-check warning right
// below it can't disagree (rocmfpx serves rocm AND vulkan).
function runnerBackends(runner) {
	const sup = runner?.supported_backends;
	if (Array.isArray(sup) && sup.length > 0) return sup;
	return runner?.backend ? [runner.backend] : [];
}

// Slot types each runner family can serve — mirrors the backend's
// profiles._supported_slot_types(runtime_family). An unknown/absent family
// never vetoes (a new backend runtime shows up rather than vanishing).
const FAMILY_SLOT_TYPES = {
	"llama-server": ["llm", "embedding", "reranking"],
	flm: ["llm", "embedding", "transcription"],
	kokoro: ["tts"],
	qwen3tts: ["tts"],
	comfyui: ["image"],
};

function EditSlotDrawer({ open, slot, onClose }) {
	// Hooks must execute every render — early `return null` would skip
	// them; render the drawer shell with a sentinel slot instead.
	const editMut = useSlotEdit();
	const defaultsMut = useSlotDefaults();
	// Delete + rename are owned by their extracted dialogs (D2 decomposition):
	// dash/slots/DeleteSlotDialog.jsx and dash/slots/RenameSlotDialog.jsx.
	const [renameOpen, setRenameOpen] = useStateSM(false);
	// Inline model edit — stacks the reusable ModelDrawer over this drawer
	// (equal z-index; later DOM order wins) so model-tune edits don't force a
	// close → Models page → reopen round-trip. The slot drawer and its
	// unsaved edits stay mounted underneath.
	const [modelEditOpen, setModelEditOpen] = useStateSM(false);
	const restartMut = useSlotRestart();
	const swapMut = useSlotSwap();
	const profilesQuery = useProfiles();
	const modelsQuery = useModels();
	const chatTemplatesQuery = useChatTemplates(open);
	// HW grid (spec-hw-slot-ownership §2): device enum from meta, BINARY options +
	// fit-check metadata from system-info (RUNNER_IMAGES).
	const metaEnums = useMetaEnums();
	const systemInfoQuery = useSystemInfo();

	// Seed from the slot list payload when available (PR #587 — same fix
	// class as #584). llamacpp_args (profile base flags) is read-only;
	// n_gpu_layers is an editable per-slot override (PATCH /defaults →
	// [model].n_gpu_layers).
	const initialExtraArgs =
		slot?.llamacpp_args != null ? slot.llamacpp_args : "";

	// Seed from the PERSISTED context window (slot.ctx_max, from
	// [model].context_size) first — NOT the live runtime metric, which is 0
	// whenever the slot isn't actively serving and would otherwise snap the
	// field to a fabricated 16k on every cold (re)load. Fall back to the live
	// metric, then the backend's safe 8192 floor.
	const [ctx, setCtx] = useStateSM(
		slot?.ctx_max ?? (slot?.metrics?.ctx || 8192),
	);
	// n_gpu_layers rides the Save button through PATCH /defaults
	// ([model].n_gpu_layers; -1/empty = unset → sends null); seeds from the
	// slot list payload. (Reasoning/MTP/Vision instant-apply pills removed —
	// spec-hw-slot-ownership §1: those are model-owned tri-state caps now,
	// edited in the model drawer instead of here.)
	// ── Hardware grid (spec-hw-slot-ownership §2) ──────────────────────────
	// The slot owns the physical layer as typed fields: device (enum) · NGL ·
	// THREADS · BINARY (runner image ref) + an optional image_pin escape hatch.
	// NGL rides the Save button as a TOP-LEVEL slot config key (reversing the §5
	// fold into [model].n_gpu_layers).
	const [device, setDevice] = useStateSM(slot?.device || "gpu-rocm");
	const [nGpuLayers, setNGpuLayers] = useStateSM(
		slot?.n_gpu_layers != null ? String(slot.n_gpu_layers) : "-1",
	);
	const [threads, setThreads] = useStateSM(
		slot?.threads != null ? String(slot.threads) : "0",
	);
	const [binary, setBinary] = useStateSM(slot?.binary || "");
	// image_pin — optional escape hatch. Empty = release default
	// (RUNNER_IMAGES[binary]). A non-default pin is shown on the slot card.
	const [imagePin, setImagePin] = useStateSM(slot?.image_pin || "");
	// Runtime profile (SlotConfig.profile) — picks the runtime family,
	// device-class gating and MTP draft backend. NOT a flags source at launch:
	// profile flags are copy-on-stamp into model.defaults.extra_args (model
	// drawer). Rides Save (PUT /config {profile}), restart-required; the
	// backend's _reconcile_device_profile keeps device/profile coherent.
	const [profileSel, setProfileSel] = useStateSM(slot?.profile || "");
	// Continuous batching: --parallel sequence slots. Empty = inherit the
	// profile (today: 1). Rides the Save button (PUT /config {parallel}),
	// restart-required. See the concurrency-batching plan.
	const [parallel, setParallel] = useStateSM(
		slot?.parallel != null ? String(slot.parallel) : "",
	);
	const [extraArgs, setExtraArgs] = useStateSM(initialExtraArgs);
	const [submitErr, setSubmitErr] = useStateSM(null);
	// Dirty-close confirms through the shared ConfirmDialog (state-driven),
	// replacing the raw window.confirm. Every dismiss path (Cancel, ✕, Esc,
	// backdrop) funnels through requestClose below.
	const [discardOpen, setDiscardOpen] = useStateSM(false);
	// UI-5 (state-driven): swapping the model on a LIVE container slot
	// cold-restarts it — stash the picked {id, label} here and confirm through
	// ConfirmDialog before firing the swap. null = no confirm pending.
	const [pendingSwap, setPendingSwap] = useStateSM(null);
	// Enable/disable is instant-apply via its own PUT (mirrors the slot card's
	// pill toggle, which the redesigned cards dropped). `enableBusy` gates the
	// header toggle against a double-trigger while the mutation is in flight.
	const [enableBusy, setEnableBusy] = useStateSM(false);
	// UI-16: destructive delete confirms through the shared ConfirmDialog
	// (type-to-confirm the slot name), mirroring DeleteModelDialog — replaces
	// the raw window.confirm that used to gate onDeleteClick.
	const [delOpen, setDelOpen] = useStateSM(false);
	// Per-field validation errors for numeric inputs (#548).
	const [fieldErrs, setFieldErrs] = useStateSM({});
	// Task 5: per-slot chat_template override.
	// chatTemplate seeds from slot.chat_template (empty/'auto' = no override).
	// overrideOpen tracks whether the user has clicked [Override] to reveal the select.
	const [chatTemplate, setChatTemplate] = useStateSM(
		normTemplate(slot?.chat_template),
	);
	const [overrideOpen, setOverrideOpen] = useStateSM(
		!!normTemplate(slot?.chat_template),
	);
	// Task 3 (NPU modality toggles): asr/embed instant-apply + cold restart for
	// device=npu slots. Seeded from slot.npu ({asr,embed}); optimistic with
	// revert-on-error.
	const [npuAsr, setNpuAsr] = useStateSM(slot?.npu?.asr === true);
	const [npuEmbed, setNpuEmbed] = useStateSM(slot?.npu?.embed === true);
	const [npuChat, setNpuChat] = useStateSM(slot?.npu?.chat !== false);
	// #1388: seed from the CONFIGURED tag ([model].default, lifted by
	// config_enrichment and normalised to `modelDefault`), not the live
	// `model_id`. useSlots documents model_id as stale for exactly this slot
	// class — trio slots never load as their own process, so it never
	// reconciles off the pre-trio GGUF. Every modality toggle re-sends this
	// value, so seeding it wrong meant an ASR/Embed flip silently rewrote
	// [model].default to an unrelated GGUF id and cold-restarted the slot.
	// Live id stays in the chain as a fallback for a slot with no configured
	// default on disk.
	const [npuChatModel, setNpuChatModel] = useStateSM(
		slot?.modelDefault || slot?.model_id || slot?.model || "qwen3:4b",
	);
	const [npuPending, setNpuPending] = useStateSM(false);
	const [npuErr, setNpuErr] = useStateSM(null);
	const [flmModels, setFlmModels] = useStateSM([]);
	// Pull-on-select: usePullJob owns one FLM download (SSE progress). pullTarget
	// remembers which role+tag is downloading so we auto-apply it on completion.
	const pull = usePullJob();
	const [pullTarget, setPullTarget] = useStateSM(null); // {role, field, tag} | null

	// Fetch the full FLM catalogue (installed + downloadable) when the NPU slot
	// editor is open. Extracted so we can re-fetch after a download completes
	// (the tag flips installed=true).
	const refreshFlmModels = React.useCallback(() => {
		fetch("/api/slots/flm/models")
			.then((r) => r.json())
			.then((d) => {
				// Defensive: an empty/degraded payload ({} from a proxy or an API
				// hiccup) must never leave a non-array here — flmModels.filter on an
				// object crashed the entire slots view behind the error boundary (C7d).
				const models = Array.isArray(d?.models)
					? d.models
					: Array.isArray(d)
						? d
						: [];
				setFlmModels(models);
			})
			.catch(() => {});
	}, []);
	React.useEffect(() => {
		if (device !== "npu") return;
		refreshFlmModels();
	}, [slot?.name, device]);

	// Apply an NPU modality/model change and cold-restart the container. Lifted
	// to component scope (out of the render IIFE) so the pull-completion effect
	// can call it too. `over` carries only the changed field(s); the rest fall
	// back to current state — which is also what we revert to on error.
	const applyNpu = async (over = {}, field = "modality") => {
		const chat = over.chat ?? npuChat;
		const asr = over.asr ?? npuAsr;
		const embed = over.embed ?? npuEmbed;
		const chatModel = over.chatModel ?? npuChatModel;
		setNpuPending(true);
		setNpuErr(null);
		// [npu] carries the modality toggles (asr/embed are boolean — FLM loads its
		// one bundled whisper / embed-gemma, no per-role model choice). The chat
		// model is FLM's positional tag, sent as a nested [model] table so the
		// backend merge preserves sibling keys (context_size, n_gpu_layers)
		// instead of clobbering them with a bare string.
		const body = { npu: { chat, asr, embed } };
		if (chat && chatModel) body.model = { default: chatModel };
		try {
			await editMut.mutateAsync({ name: slot.name, body });
			restartMut.mutate(slot.name, {
				onError: (err) =>
					window.__hal0Toast &&
					window.__hal0Toast(
						`NPU restart failed — ${err?.message || "see logs"}`,
						"err",
					),
			});
			window.__hal0Toast &&
				window.__hal0Toast(
					`${slot.name} NPU ${field} updated — restarting`,
					"info",
				);
		} catch (err) {
			setNpuChat(npuChat);
			setNpuAsr(npuAsr);
			setNpuEmbed(npuEmbed);
			setNpuChatModel(npuChatModel);
			setNpuErr(err?.message || "NPU toggle failed");
		} finally {
			setNpuPending(false);
		}
	};

	// Pick the chat model. Installed → apply immediately. Not-yet-downloaded →
	// start the FLM pull and remember the target; the completion effect below
	// auto-applies it once the weights land. (ASR/Embed have no model choice.)
	const onPickNpuModel = (role, field, tag) => {
		setNpuChatModel(tag);
		const entry = flmModels.find((m) => m.model === tag);
		if (entry && entry.installed === false) {
			setNpuErr(null);
			setPullTarget({ role, field, tag });
			pull.start(tag).catch(() => {}); // failure surfaces via the effect below
		} else {
			applyNpu({ [field]: tag }, role);
		}
	};

	// Auto-apply a freshly-downloaded model, or surface a failed/cancelled pull.
	React.useEffect(() => {
		if (!pullTarget || pull.modelId !== pullTarget.tag) return;
		if (pull.state === "completed") {
			refreshFlmModels();
			applyNpu({ [pullTarget.field]: pullTarget.tag }, pullTarget.role);
			setPullTarget(null);
			pull.reset();
		} else if (pull.state === "failed" || pull.state === "cancelled") {
			setNpuErr(pull.error?.message || `Download ${pull.state}`);
			setPullTarget(null);
			pull.reset();
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [pull.state, pull.modelId, pullTarget]);

	// Resolved command provenance — only fetched while the drawer is open.
	// Falls back gracefully when null (non-llama slots) or on error.
	const resolvedQuery = useSlotResolved(slot?.name, { enabled: !!open });

	useEffectSM(() => {
		if (slot) {
			setCtx(slot.ctx_max ?? (slot.metrics?.ctx || 8192));
			// HW grid re-seed from the (possibly-updated) slot prop.
			setDevice(slot.device || "gpu-rocm");
			setNGpuLayers(
				slot.n_gpu_layers != null ? String(slot.n_gpu_layers) : "-1",
			);
			setThreads(slot.threads != null ? String(slot.threads) : "0");
			setBinary(slot.binary || "");
			setImagePin(slot.image_pin || "");
			setProfileSel(slot.profile || "");
			setParallel(slot.parallel != null ? String(slot.parallel) : "");
			// #587: re-seed from the slot prop so the drawer tracks the real
			// on-disk values.
			setExtraArgs(slot.llamacpp_args != null ? slot.llamacpp_args : "");
			setSubmitErr(null);
			setDiscardOpen(false);
			setPendingSwap(null);
			setModelEditOpen(false);
			setFieldErrs({});
			// Task 5: re-seed chat_template override from the slot prop.
			setChatTemplate(normTemplate(slot.chat_template));
			setOverrideOpen(!!normTemplate(slot.chat_template));
			setNpuAsr(slot.npu?.asr === true);
			setNpuEmbed(slot.npu?.embed === true);
			// Re-seed the chat pill + all three model selects too, so a save +
			// refetch keeps the drawer in sync with server truth instead of
			// drifting until the drawer is remounted.
			setNpuChat(slot.npu?.chat !== false);
			setNpuChatModel(
				slot.modelDefault || slot.model_id || slot.model || "qwen3:4b",
			);
			setNpuPending(false);
			setNpuErr(null);
		}
	}, [slot?.name]);

	// Bound model row, resolved BEFORE the `!slot` guard below: the effect
	// that follows must run on every render, and anything after an early
	// return does not (React counts hooks positionally).
	const curModelId = slot?.model_id || slot?.model || "";
	const curModelRow =
		(modelsQuery.data ?? []).find((m) => m.id === curModelId) || null;
	// ModelDrawer renders nothing for a null model and never calls onClose in
	// that path, so a models refetch that drops the bound row would otherwise
	// leave modelEditOpen stuck true (dead ✕/Esc, and a surprise re-stack when
	// the row comes back). Clear the flag whenever the stacked editor has
	// nothing to render — or when this drawer itself closes.
	useEffectSM(() => {
		if (!open || !curModelRow) setModelEditOpen(false);
	}, [open, curModelRow]);

	if (!slot) return null;

	async function onSaveClick() {
		setSubmitErr(null);
		// Issue #548: validate numeric fields before any network call.
		// Invalid values surface inline and block Save.
		const ctxNum = Number(ctx);
		const errs = {};
		if (!Number.isFinite(ctxNum) || !Number.isInteger(ctxNum) || ctxNum < 128) {
			errs.ctx = "Must be an integer ≥ 128";
		}
		// NGL (HW grid, spec-hw-slot-ownership §2): a slot-owned TOP-LEVEL int.
		// -1 or empty = "all layers" default; otherwise an integer ≥ -1.
		const nglRaw = String(nGpuLayers).trim();
		const nglNum = nglRaw === "" ? null : Number(nglRaw);
		if (
			nglRaw !== "" &&
			(!Number.isFinite(nglNum) || !Number.isInteger(nglNum) || nglNum < -1)
		) {
			errs.ngl = "Must be an integer ≥ -1 (or empty)";
		}
		// THREADS (HW grid): 0 = unset (runtime default); otherwise integer ≥ 0.
		const thrRaw = String(threads).trim();
		const thrNum = thrRaw === "" ? 0 : Number(thrRaw);
		if (
			thrRaw !== "" &&
			(!Number.isFinite(thrNum) || !Number.isInteger(thrNum) || thrNum < 0)
		) {
			errs.threads = "Must be an integer ≥ 0 (0 = runtime default)";
		}
		// parallel (--parallel/-np): empty = inherit default; else integer ≥ 1.
		const parRaw = String(parallel).trim();
		const parNum = parRaw === "" ? null : Number(parRaw);
		if (
			parRaw !== "" &&
			(!Number.isFinite(parNum) || !Number.isInteger(parNum) || parNum < 1)
		) {
			errs.parallel =
				"Must be an integer ≥ 1 (or empty to inherit the default)";
		}
		// image_pin: empty is allowed (release default). When set, must look like a
		// registry ref — contains ":" (host:tag or repo:tag) and no whitespace.
		const pinTrim = (imagePin || "").trim();
		if (pinTrim && (!pinTrim.includes(":") || /\s/.test(pinTrim))) {
			errs.imagePin =
				"Must look like a registry ref (e.g. ghcr.io/owner/repo:tag)";
		}
		// Block Save on malformed extra_args (unbalanced quotes) the same way
		// numeric fields block — the resolved command can't be built from it.
		// Only when the field is actually mounted (`device !== "npu"` gates the
		// Model group): a malformed PERSISTED value on an NPU slot must not
		// veto unrelated saves with the error surface unmounted (#1389).
		if (extraArgsErr && device !== "npu") {
			errs.extraArgs = extraArgsErr;
		}
		if (Object.keys(errs).length > 0) {
			setFieldErrs(errs);
			return;
		}
		setFieldErrs({});
		// Task 5: baseline-vs-desired comparison so BOTH directions persist —
		// setting a real override AND clearing one (Clear override sets
		// overrideOpen=false, desired ""). Two comparisons, deliberately:
		//   * raw → what the PUT carries, so a stale chat_template = "auto" is
		//     cleaned off disk instead of lingering as an inert key;
		//   * normalized → what needs a cold restart, since 'auto' and absent
		//     produce the identical argv (no --chat-template flag).
		const templateDesired = overrideOpen ? normTemplate(chatTemplate) : "";
		const chatTemplateChanged = templateDesired !== (slot.chat_template ?? "");
		const templateEffectiveChanged =
			templateDesired !== normTemplate(slot.chat_template);
		// Per-slot extra_args override — ship only when changed, nested under
		// [server] so the backend one-level merge preserves sibling server keys.
		const extraArgsChanged = extraArgs !== extraArgsBaseline;
		// Only write ctx_size when the operator actually changed it. Gate on the
		// persisted baseline (ctxBaseline).
		const ctxChanged = ctxNum !== Number(ctxBaseline);
		// HW grid dirty-tracking (spec-hw-slot-ownership §2). NGL/THREADS are
		// top-level slot config ints now (reversing the §5 fold). -1/empty NGL and
		// 0/empty THREADS normalize to the "unset" defaults.
		const nglValue = nglRaw === "" ? -1 : nglNum;
		const thrValue = thrRaw === "" ? 0 : thrNum;
		const pinValue = pinTrim === "" ? null : pinTrim;
		const deviceChanged = device !== (slot.device || "gpu-rocm");
		const nglChanged = nglValue !== (slot.n_gpu_layers ?? -1);
		const threadsChanged = thrValue !== (slot.threads ?? 0);
		const binaryChanged = binary !== (slot.binary || "");
		const imagePinChanged = pinValue !== (slot.image_pin ?? null);
		const profileChanged = profileSel !== (slot.profile || "");
		// A hardware change (device/NGL/threads/binary/image_pin/profile) needs
		// a cold restart, same as a chat_template change.
		const hwChanged =
			deviceChanged ||
			nglChanged ||
			threadsChanged ||
			binaryChanged ||
			imagePinChanged ||
			profileChanged;
		try {
			// Two-step: defaults (ctx_size lives under [model]) + slot config for the
			// top-level keys (device / NGL / threads / binary / image_pin /
			// chat_template / server). These are fast on-disk writes.
			const slotBody = {};
			if (deviceChanged) slotBody.device = device;
			if (nglChanged) slotBody.n_gpu_layers = nglValue;
			if (threadsChanged) slotBody.threads = thrValue;
			if (binaryChanged) slotBody.binary = binary;
			if (imagePinChanged) slotBody.image_pin = pinValue;
			if (profileChanged) slotBody.profile = profileSel || null;
			if (chatTemplateChanged) {
				// null rides the backend's None-means-delete merge — clearing an
				// override (or picking Auto) removes the key from the slot TOML.
				slotBody.chat_template = templateDesired || null;
			}
			if (extraArgsChanged) {
				slotBody.server = { extra_args: extraArgs };
			}
			// parallel is a top-level slot field. Empty → null (inherit; the
			// None-means-delete merge clears any persisted override).
			const parValue = parRaw === "" ? null : parNum;
			if (parValue !== (slot.parallel ?? null)) {
				slotBody.parallel = parValue;
			}
			const defaultsBody = {};
			if (ctxChanged) defaultsBody.ctx_size = ctxNum;
			if (Object.keys(defaultsBody).length > 0) {
				await defaultsMut.mutateAsync({
					name: slot.name,
					body: defaultsBody,
				});
			}
			if (Object.keys(slotBody).length > 0) {
				await editMut.mutateAsync({
					name: slot.name,
					body: slotBody,
				});
			}
		} catch (err) {
			setSubmitErr(err?.message || "save failed");
			return;
		}
		// Non-blocking apply: a hardware or effective chat_template change
		// requires a cold restart that can take model-load seconds-to-minutes.
		// Fire it in the BACKGROUND (do NOT await) and close the drawer
		// immediately. A pure 'auto' → absent cleanup changes no argv, so it
		// deliberately does NOT restart.
		if (hwChanged || templateEffectiveChanged) {
			restartMut.mutate(slot.name, {
				onError: (err) =>
					window.__hal0Toast &&
					window.__hal0Toast(
						`Slot "${slot.name}" restart failed — ${err?.message || "see logs"}`,
						"err",
					),
			});
			window.__hal0Toast &&
				window.__hal0Toast(
					`Slot "${slot.name}" saved — restarting in the background`,
					"info",
				);
		} else {
			window.__hal0Toast &&
				window.__hal0Toast(
					`Slot "${slot.name}" saved — restart required to apply changes`,
					"warn",
				);
		}
		onClose();
	}

	// Regenerate: persist the slot's freeform extra_args overlay (NOT the
	// profile) and let useSlotEdit's invalidation refetch the slot, which
	// recomputes resolved_command server-side. The drawer's `slot` prop is
	// derived live from the slots query, so on refetch the dirty overlay clears
	// (baseline now equals the typed value) and the fresh command renders. Does
	// NOT restart — a running slot keeps its old flags until the next restart.
	async function onRegenerateClick() {
		setSubmitErr(null);
		if (extraArgsErr) return;
		try {
			await editMut.mutateAsync({
				name: slot.name,
				body: { server: { extra_args: extraArgs } },
			});
		} catch (err) {
			setSubmitErr(err?.message || "regenerate failed");
			return;
		}
		window.__hal0Toast &&
			window.__hal0Toast(
				`Slot "${slot.name}" extra_args saved — restart to run with the new flags`,
				"info",
			);
	}

	// `saving` gates the Save button on the fast config writes only — the
	// restart is fired in the background (see onSaveClick) and must not keep the
	// drawer in a blocked "Saving…" state for the whole model-load.
	const saving = editMut.isPending || defaultsMut.isPending;

	// (curModelId / curModelRow are resolved above the `!slot` guard, so the
	// modelEditOpen effect can run on every render.)

	// Device-class token for profile/runner fit filters — mirrors the
	// backend derivation in profile_adopt (gpu-* → gpu; npu/cpu/img as-is).
	const deviceClass = device.startsWith("gpu")
		? "gpu"
		: ["npu", "cpu", "img"].includes(device)
			? device
			: "cpu";

	// Instant-apply pin/unpin for the drawer header toggle (§21.10, #1367).
	// Fires the PUT, toasts the result, and lets the slots poll re-render from
	// server truth. `slot.pinned` is the *effective* pin lifted by slot_view
	// (explicit config value overlaid on the agent/utility/npu anchor set), so
	// a fresh-install anchor seeds the toggle on. Pinning never starts/stops
	// anything — it guards unload/delete (409 slot.pinned without ?force=true)
	// and exempts the slot from idle/pressure eviction.
	const pinned = slot.pinned === true;
	const onTogglePinned = async (next) => {
		setEnableBusy(true);
		try {
			await editMut.mutateAsync({ name: slot.name, body: { pinned: next } });
			window.__hal0Toast &&
				window.__hal0Toast(
					`${slot.name} ${next ? "pinned" : "unpinned"}`,
					"ok",
				);
		} catch (err) {
			window.__hal0Toast &&
				window.__hal0Toast(
					err?.message
						? `${slot.name}: ${err.message}`
						: `${slot.name}: toggle failed`,
					"warn",
				);
		} finally {
			setEnableBusy(false);
		}
	};

	// extra_args dirty-tracking: the resolved command is server-computed from the
	// persisted config, so any unsaved edit makes the displayed command stale.
	// Baseline is the on-disk value surfaced as `llamacpp_args` (wire key for
	// [server].extra_args). `validateExtraArgs` is a cheap client guard (balanced
	// quotes) — the backend shlex parse is the real validator.
	const extraArgsBaseline =
		slot.llamacpp_args != null ? slot.llamacpp_args : "";
	const extraArgsDirty = extraArgs !== extraArgsBaseline;
	const extraArgsErr = validateExtraArgs(extraArgs);
	// ctx dirty-tracking baseline: the PERSISTED context window (slot.ctx_max),
	// mirroring how the seed value is derived. Falls back to the live metric then
	// the 8192 floor only when nothing is persisted, so an untouched field on a
	// cold slot is never counted dirty (and never written — see ctxChanged).
	const ctxBaseline = slot.ctx_max ?? (slot.metrics?.ctx || 8192);
	// HW grid dirty-tracking (spec-hw-slot-ownership §2). NGL/THREADS normalize
	// their "unset" seeds (-1 NGL, 0 THREADS) so an untouched field never counts
	// dirty. Baselines compare against the slot's persisted top-level HW fields.
	const nglRawNow = String(nGpuLayers).trim();
	const nglValueNow = nglRawNow === "" ? -1 : Number(nglRawNow);
	const nglDirty = nglValueNow !== (slot.n_gpu_layers ?? -1);
	const thrRawNow = String(threads).trim();
	const thrValueNow = thrRawNow === "" ? 0 : Number(thrRawNow);
	const threadsDirty = thrValueNow !== (slot.threads ?? 0);
	const deviceDirty = device !== (slot.device || "gpu-rocm");
	const binaryDirty = binary !== (slot.binary || "");
	const imagePinDirty = (imagePin.trim() || null) !== (slot.image_pin ?? null);
	const profileDirty = profileSel !== (slot.profile || "");

	// UI-1: unsaved-changes guard. Aggregate ONLY the Save-batched fields (HW
	// grid, extra_args, ctx, parallel, chat_template override). The instant-apply
	// ``enable`` toggle fires its own PUT/POST outside Save and is intentionally
	// excluded — a flipped toggle is already persisted. (Reasoning/MTP/Vision
	// were also instant-apply toggles here; they moved to the model drawer —
	// spec-hw-slot-ownership §1.)
	// ctx dirty test matches the SAVE path's numeric comparison (ctxChanged in
	// onSaveClick: Number(ctx) !== Number(ctxBaseline)).
	const ctxDirty = Number(String(ctx).trim()) !== Number(ctxBaseline);
	const parRawNow = String(parallel).trim();
	const parValueNow = parRawNow === "" ? null : Number(parRawNow);
	const parallelDirty = parValueNow !== (slot.parallel ?? null);
	const dirty =
		extraArgsDirty ||
		ctxDirty ||
		nglDirty ||
		threadsDirty ||
		deviceDirty ||
		binaryDirty ||
		imagePinDirty ||
		profileDirty ||
		parallelDirty ||
		(overrideOpen ? normTemplate(chatTemplate) : "") !==
			normTemplate(slot.chat_template);
	const requestClose = () => {
		// While the ModelDrawer is stacked on top, one Esc press fires BOTH
		// drawers' document-level keydown listeners — swallow ours so only the
		// top layer closes (and no spurious discard dialog pops underneath).
		// The guard mirrors what is ACTUALLY rendered (`modelEditOpen &&
		// curModelRow`, below): ModelDrawer self-unmounts on a null model
		// WITHOUT calling onClose, so gating on modelEditOpen alone would wedge
		// this drawer shut for good the moment a models refetch drops the bound
		// row (deleted/renamed elsewhere, short registry reload).
		if (modelEditOpen && curModelRow) return;
		if (dirty) {
			setDiscardOpen(true);
			return;
		}
		onClose();
	};

	// Fire the model swap (POST /slots/{name}/swap). Non-blocking: a swap
	// cold-restarts container slots to load the model (slow) — fire it and let
	// the slots poll reflect the transitional phase; never freeze the drawer on
	// the load. Called directly for non-live swaps, or from the ConfirmDialog
	// once the operator confirms a live-container cold restart.
	const fireSwap = (id, label) => {
		setSubmitErr(null);
		swapMut.mutate(
			{ name: slot.name, model_id: id },
			{
				onError: (err) => setSubmitErr(err?.message || "model swap failed"),
			},
		);
		window.__hal0Toast &&
			window.__hal0Toast(
				slot.runtime === "container"
					? `Restarting ${slot.name} to load ${label} — loading in the background`
					: `${slot.name} → ${label}`,
				"info",
			);
	};

	return (
		<>
			{/* The Drawer's Esc/backdrop/✕ paths call onClose — routed through
        requestClose so the dirty guard runs through this drawer's own
        ConfirmDialog copy below. (The primitive's `dirty` prop is now also
        dialog-based — DiscardGuardDialog in primitives.jsx — but this drawer
        keeps its slot-specific discard copy.) */}
			<Drawer
				open={open}
				onClose={requestClose}
				eyebrow={`Slots · /slots/${slot.name}`}
				title={`Edit ${slot.name}`}
				width={560}
				headRight={
					<label
						className="slot-enable-toggle drawer-enable"
						title={
							pinned
								? "Unpin slot"
								: "Pin slot — exempt from idle/pressure eviction; unload/delete require ?force=true"
						}
					>
						<span className="drawer-enable-label mono">
							{pinned ? "Pinned" : "Unpinned"}
						</span>
						<input
							type="checkbox"
							checked={pinned}
							disabled={enableBusy}
							onChange={() => onTogglePinned(!pinned)}
							aria-label={pinned ? "Unpin slot" : "Pin slot"}
						/>
						<span className="slot-enable-track" aria-hidden="true" />
					</label>
				}
				foot={
					<>
						<button
							className="btn danger sm"
							data-testid="slot-delete-open"
							onClick={() => setDelOpen(true)}
						>
							{Icons.unload} Delete slot
						</button>
						<span
							style={{ display: "inline-flex", gap: 8, alignItems: "center" }}
						>
							{submitErr && (
								<span style={{ color: "var(--err)", fontSize: 11 }}>
									{submitErr}
								</span>
							)}
							<button className="btn ghost sm" onClick={requestClose}>
								Cancel
							</button>
							<button
								className="btn sm"
								disabled={saving}
								onClick={onSaveClick}
							>
								{saving ? "Saving…" : "Save"}
							</button>
						</span>
					</>
				}
			>
				{/* Identity strip — read-only: stable slot id, PortAuthority-assigned
          port, and state. The id is the API key for debugging (stable across a
          rename); the port is display-only (assigned by PortAuthority). */}
				<div
					style={{
						display: "grid",
						gridTemplateColumns: "1fr 1fr 1fr",
						gap: 0,
						border: "1px solid var(--line-soft)",
						borderRadius: "var(--rad-sm)",
						overflow: "hidden",
						marginBottom: 16,
					}}
				>
					<ReadOnlyStrip
						k="slot_id"
						v={
							<span data-testid="slot-id-readonly">
								{(slot.id != null ? slot.id : slot.slot_id) != null
									? `#${slot.id != null ? slot.id : slot.slot_id}`
									: "—"}
							</span>
						}
					/>
					<ReadOnlyStrip
						k="port · PortAuthority"
						v={
							<span
								data-testid="slot-port-readonly"
								title="assigned by PortAuthority"
							>{`:${slot.port || "—"}`}</span>
						}
					/>
					<ReadOnlyStrip
						k="state"
						v={<span className={stateChipClass(slot)}>{slot.state}</span>}
					/>
				</div>

				{/* Runner + image status strip — read-only. Runner (BINARY) resolves the
          launch image (RUNNER_IMAGES[binary]); image_pin overrides it (§3).
          image status keyed to slot_id so the operator knows which slot owns it. */}
				<div
					style={{
						display: "grid",
						gridTemplateColumns: "1fr 1fr",
						gap: 0,
						border: "1px solid var(--line-soft)",
						borderRadius: "var(--rad-sm)",
						overflow: "hidden",
						marginBottom: 16,
					}}
				>
					<ReadOnlyStrip
						k="runner · binary"
						v={slot.binary || `auto · ${deviceBackend(device) || device}`}
					/>
					<ReadOnlyStrip
						k="image status"
						v={
							<span data-testid="slot-image-status">
								{slotButtonPhase(slot) === "running"
									? (slot.actual_image ||
											slot.image_pin ||
											slot.image ||
											"present") +
										(slot.id != null
											? ` · #${slot.id}`
											: slot.slot_id != null
												? ` · #${slot.slot_id}`
												: "")
									: "—"}
							</span>
						}
					/>
				</div>

				{/* Slot identity — names are mutable labels; the stable slot id is not. */}
				<FieldGroup label="Slot">
					<div className="form-row">
						<div className="form-lbl">
							<span>Name</span>
							<FieldInfoIcon description="A display label. Rename any time — the stable slot number never
								changes." />
						</div>
						<div
							className="form-ctl"
							style={{ display: "flex", gap: 8, alignItems: "center" }}
						>
							<input
								className="input mono"
								value={slot.name}
								disabled
								style={{ flex: 1 }}
							/>
							<button
								className="btn ghost sm"
								data-testid="slot-rename-open"
								onClick={() => setRenameOpen(true)}
							>
								Rename…
							</button>
						</div>
					</div>

					<div className="form-row">
						<div className="form-lbl">
							<span>Type</span>
						</div>
						<div className="form-ctl">
							<select className="input mono" defaultValue={slot.type} disabled>
								<option>{slot.type}</option>
							</select>
							<FieldInfoIcon description="Type is fixed once created. Make a new slot for a different kind." />
						</div>
					</div>
				</FieldGroup>

				{/* Runtime profile — the slot's SlotConfig.profile. Controls the
	          runtime family, device-class gating and MTP draft backend; profile
	          FLAGS are copy-on-stamp into the model tune (model drawer), never
	          read at launch. */}
				<FieldGroup label="Profile">
					{(() => {
						const all = Array.isArray(profilesQuery.data)
							? profilesQuery.data
							: [];
						const devBackend = deviceBackend(device);
						// Mirror backend profile_fits_slot: slot type supported +
						// device_class match + backend match (when both declared).
						const fit = all.filter(
							(p) =>
								(!Array.isArray(p.supported_slot_types) ||
									p.supported_slot_types.includes(slot.type)) &&
								(!p.device_class || p.device_class === deviceClass) &&
								(!p.backend || !devBackend || p.backend === devBackend),
						);
						const fitNames = fit.map((p) => p.name);
						const adoptedFromModel =
							!!profileSel &&
							profileSel === (curModelRow?.defaults?.profile || "");
						// The options are filtered against the CURRENT device, but the
						// device select sits below and can flip afterwards. Saving a
						// profile+device pair with conflicting backends is a hard
						// SlotConfigError (_reconcile_device_profile), so warn instead of
						// silently PUTting the conflict.
						const profileFitWarn =
							profileSel && all.length > 0 && !fitNames.includes(profileSel)
								? `Profile "${profileSel}" does not fit device "${device}" — saving both together is rejected. Pick a listed profile or revert the device.`
								: null;
						return (
							<div className="form-row">
								<div className="form-lbl">
									<span>Profile</span>
									<FieldInfoIcon description="⟳ Runtime profile — picks the runtime family, device-class
										gating and the MTP draft backend. Flags are NOT read from here
										at launch; they are stamped into the model's launch flags on
										the model drawer." />
								</div>
								<div className="form-ctl">
									<select
										className="input mono"
										data-testid="slot-profile"
										value={profileSel}
										onChange={(e) => setProfileSel(e.target.value)}
									>
										{/* keep a none/out-of-vocab persisted profile selectable */}
										{!slot.profile && <option value="">— none —</option>}
										{profileSel && !fitNames.includes(profileSel) && (
											<option value={profileSel}>{profileSel}</option>
										)}
										{fit.map((p) => (
											<option key={p.name} value={p.name} title={p.intent}>
												{p.name}
												{p.intent ? ` · ${p.intent}` : ""}
											</option>
										))}
									</select>
									{adoptedFromModel && (
										<div className="hint">
											Adopted from the bound model's preference — swapping the
											model may change it.
										</div>
									)}
									{profileFitWarn && (
										<div
											className="hint"
											data-testid="slot-profile-fit-warning"
											style={{
												marginTop: 6,
												padding: "6px 10px",
												borderRadius: "var(--rad-sm)",
												color: "var(--warn)",
												border: "1px solid var(--warn-line)",
												background: "var(--warn-soft)",
											}}
										>
											⚠ {profileFitWarn}
										</div>
									)}
								</div>
							</div>
						);
					})()}
				</FieldGroup>

				{/* Hardware ownership — changes apply on Save and require a restart. */}
				<FieldGroup label="Hardware">
					{(() => {
						const devices = Array.isArray(metaEnums?.devices)
							? metaEnums.devices
							: [];
						const backends = systemInfoQuery.data?.backends ?? {};
						const binaryKeys = Object.keys(backends);
						const devBackend = deviceBackend(device);
						// Runner options filtered down to the ones that fit this slot:
						// device_class exact (runner_matches), the family's slot types
						// (_supported_slot_types — so a gpu llm slot is not offered
						// qwen3tts/comfyui) and the SAME supported_backends list the
						// fit-check below warns on. An out-of-vocab persisted value stays
						// selectable underneath.
						const fitBinaryKeys = binaryKeys.filter((k) => {
							const r = backends[k] || {};
							if (r.device_class && r.device_class !== deviceClass)
								return false;
							const types = FAMILY_SLOT_TYPES[r.runtime_family];
							if (types && slot.type && !types.includes(slot.type))
								return false;
							const sup = runnerBackends(r);
							return !(sup.length > 0 && devBackend && !sup.includes(devBackend));
						});
						// Fit-check (§4): the selected device's backend must be in the chosen
						// BINARY's supported_backends. WARN at assignment, never block. Only
						// when a BINARY is explicitly picked (empty = HW-gated default).
						const selRunner = binary ? backends[binary] : null;
						const supported = selRunner ? runnerBackends(selRunner) : null;
						const fitWarn =
							binary &&
							devBackend &&
							supported &&
							supported.length > 0 &&
							!supported.includes(devBackend)
								? `Device backend "${devBackend}" is not in ${binary}'s supported backends (${supported.join(", ")}). The slot may fall back or fail at spawn.`
								: null;
						return (
							<>
								<div className="form-row">
									<div className="form-lbl">
										<span>Device</span>
										<FieldInfoIcon description="Changing device changes the hardware class/backend and requires a restart." />
									</div>
									<div className="form-ctl">
										<select
											className="input mono"
											data-testid="slot-hw-device"
											value={device}
											onChange={(e) => setDevice(e.target.value)}
										>
											{/* keep an out-of-vocab persisted device selectable */}
											{device && !devices.some((d) => d.id === device) && (
												<option value={device}>{device}</option>
											)}
											{devices.map((d) => (
												<option key={d.id} value={d.id} title={d.description}>
													{d.label}
													{d.recommended ? " ★" : ""}
												</option>
											))}
										</select>
									</div>
								</div>

								<div className="form-row">
									<div className="form-lbl">
										<span>Image pin</span>
										<FieldInfoIcon description="Optional container image override for a debug build, A/B test, or rollback. Empty uses the release default." />
									</div>
									<div className="form-ctl">
										<input
											className={
												"input mono" + (fieldErrs.imagePin ? " input-err" : "")
											}
											data-testid="slot-hw-image-pin"
											value={imagePin}
											onChange={(e) => {
												setImagePin(e.target.value);
												setFieldErrs((p) => ({ ...p, imagePin: undefined }));
											}}
											placeholder={
												binary && backends[binary]?.image
													? backends[binary].image
													: "will resolve from runner (binary)"
											}
											spellCheck={false}
											style={imagePin ? {} : { color: "var(--fg-4)" }}
										/>
										{fieldErrs.imagePin ? (
											<div className="hint" style={{ color: "var(--err)" }}>
												{fieldErrs.imagePin}
											</div>
										) : null}
									</div>
								</div>

								<div className="form-row">
									<div className="form-lbl">
										<span>Runner</span>
										<FieldInfoIcon description="⟳ Which runner build/image executes on the selected
											device (a RUNNER_IMAGES key, not a profile). Empty = auto
											from device." />
									</div>
									<div className="form-ctl">
										<select
											className={"input mono" + (fitWarn ? " input-err" : "")}
											data-testid="slot-hw-binary"
											value={binary}
											onChange={(e) => setBinary(e.target.value)}
										>
											<option value="">— default (from device) —</option>
											{/* keep an out-of-vocab persisted binary selectable */}
											{binary && !fitBinaryKeys.includes(binary) && (
												<option value={binary}>{binary}</option>
											)}
											{fitBinaryKeys.map((k) => (
												<option key={k} value={k}>
													{k}
													{backends[k]?.backend
														? ` · ${backends[k].backend}`
														: ""}
												</option>
											))}
										</select>
										{fitWarn && (
											<div
												className="hint"
												data-testid="slot-hw-fit-warning"
												style={{
													marginTop: 6,
													padding: "6px 10px",
													borderRadius: "var(--rad-sm)",
													color: "var(--warn)",
													border: "1px solid var(--warn-line)",
													background: "var(--warn-soft)",
												}}
											>
												⚠ {fitWarn}
											</div>
										)}
									</div>
								</div>

								<div className="form-row">
									<div className="form-lbl">
										<span>Threads</span>
										<FieldInfoIcon description="CPU thread count for the runner. 0 = let the runtime
											decide." />
									</div>
									<div className="form-ctl">
										<input
											className={
												"input mono" + (fieldErrs.threads ? " input-err" : "")
											}
											data-testid="slot-hw-threads"
											value={threads}
											onChange={(e) => {
												setThreads(e.target.value);
												setFieldErrs((p) => ({ ...p, threads: undefined }));
											}}
											placeholder="0"
											inputMode="numeric"
										/>
										{fieldErrs.threads && (
											<div className="hint" style={{ color: "var(--err)" }}>
												{fieldErrs.threads}
											</div>
										)}
									</div>
								</div>

								<div className="form-row">
									<div className="form-lbl">
										<span>NGL</span>
										<FieldInfoIcon description="GPU layers to offload — emits -ngl to the runner.
											-1 = all layers, 0 = CPU only." />
									</div>
									<div className="form-ctl">
										<input
											className={
												"input mono" + (fieldErrs.ngl ? " input-err" : "")
											}
											data-testid="slot-hw-ngl"
											value={nGpuLayers}
											onChange={(e) => {
												setNGpuLayers(e.target.value);
												setFieldErrs((p) => ({ ...p, ngl: undefined }));
											}}
											placeholder="-1"
											inputMode="numeric"
										/>
										{fieldErrs.ngl && (
											<div className="hint" style={{ color: "var(--err)" }}>
												{fieldErrs.ngl}
											</div>
										)}
									</div>
								</div>
							</>
						);
					})()}
				</FieldGroup>

				{device !== "npu" && (
					<FieldGroup label="Model">
						{/* Task 1: live model swap — mirrors the card's ModelPicker but with the
          full type+rocmfp4 compatibility filter (same as InlineSwapPopover).
          Swap is its own POST /slots/{name}/swap (not part of the batched
          Save); container slots cold-restart to load, so we toast like the
          popover does. */}
						{(() => {
							const isContainer = slot.runtime === "container";
							// Derive the backend from the SELECTED device (reactive) so the rocmfp4
							// filter re-evaluates immediately when the operator switches the HW-grid
							// device — before Save is clicked. (Device is the single owner of the
							// rocm/vulkan axis now — spec-hw-slot-ownership §2.)
							const selBackend = deviceBackend(device) || slot.backend;
							const compatible = compatibleModels(modelsQuery.data, {
								type: slot.type,
								backend: selBackend,
							});
							const cur = slot.model_id || slot.model || "";
							const has = compatible.some((m) => m.id === cur);
							// A background swap is in flight — the select stays usable, but show a
							// "Swapping…" hint so the operator knows the load is happening.
							const swapping = swapMut.isPending;
							return (
								<div className="form-row">
									<div className="form-lbl">
										<span>Model</span>
										<FieldInfoIcon description={isContainer ? "Swap restarts the container to load the new model" : "Applies immediately"} />
									</div>
									<div className="form-ctl">
										<div
											style={{ display: "flex", gap: 8, alignItems: "center" }}
										>
											<select
												className="input mono"
												style={{ flex: 1 }}
												value={cur}
												disabled={saving}
												aria-label={`Model for ${slot.name}`}
												onChange={(e) => {
													const id = e.target.value;
													if (!id || id === cur) return;
													const picked = compatible.find((m) => m.id === id);
													const label = picked?.longName || id;
													// UI-5: swapping the model on a LIVE container slot cold-restarts
													// it (~model-load seconds). Confirm through the shared
													// ConfirmDialog before firing — stashing the pick re-renders the
													// select back to `cur` (value={cur}), so cancel needs no manual
													// revert. Mirrors the delete/dirty-close confirm gates.
													const live = slotButtonPhase(slot) === "running";
													if (isContainer && live) {
														setPendingSwap({ id, label });
														return;
													}
													fireSwap(id, label);
												}}
											>
												{cur && !has && (
													<option value={cur}>
														{slot.modelLong || slot.model || cur}
													</option>
												)}
												{!cur && <option value="">—</option>}
												{compatible.map((m) => (
													<option key={m.id} value={m.id}>
														{m.longName || m.id}
													</option>
												))}
											</select>
											<button
												className="btn ghost sm"
												data-testid="slot-model-edit-open"
												disabled={!curModelRow}
												title="Edit the bound model's tune (flags, template, caps) in place"
												onClick={() => setModelEditOpen(true)}
											>
												Edit model…
											</button>
										</div>
										{swapping && <div className="hint">Swapping…</div>}
									</div>
								</div>
							);
						})()}

						<div className="form-row">
							<div className="form-lbl">
								<span>Context (override)</span>
								<FieldInfoIcon description="⟳ ctx_size — context window in tokens, an OVERRIDE of the
									bound model's own default context_size (set on the model drawer).
									Slot-owned so the same model can run a bigger/smaller window on
									different hardware. PATCHes /defaults; takes effect on next
									request. (~model-load seconds)" />
							</div>
							<div className="form-ctl">
								<input
									className={"input mono" + (fieldErrs.ctx ? " input-err" : "")}
									value={ctx}
									onChange={(e) => {
										setCtx(e.target.value);
										setFieldErrs((p) => ({ ...p, ctx: undefined }));
									}}
								/>
								{fieldErrs.ctx && (
									<div className="hint" style={{ color: "var(--err)" }}>
										{fieldErrs.ctx}
									</div>
								)}
							</div>
						</div>

						{/* Task 5: per-slot chat_template override.
          Shows the model-level default template (from model.defaults.chat_template)
          read-only, with an [Override] button to reveal a select for a per-slot
          override. Override is dirty-tracked against slot.chat_template and
          included in the config PUT only when changed. A template change requires
          a cold restart (it changes llama-server --chat-template arg). */}
						{(() => {
							const modelTemplate =
								curModelRow?.defaults?.chat_template || "auto";
							const templates = Array.isArray(chatTemplatesQuery.data)
								? chatTemplatesQuery.data
								: [];
							return (
								<div className="form-row">
									<div className="form-lbl">
										<span>Template</span>
										<FieldInfoIcon description="⟳ Pick a chat format for this model. Auto uses the
											built-in template." />
									</div>
									<div className="form-ctl">
										{!overrideOpen ? (
											<div
												style={{
													display: "flex",
													alignItems: "center",
													gap: 8,
												}}
											>
												<span
													className="input mono"
													style={{
														flex: 1,
														padding: "6px 10px",
														background: "var(--bg)",
														border: "1px solid var(--line-soft)",
														borderRadius: "var(--rad-sm)",
														fontSize: 12,
														color: "var(--fg-3)",
													}}
												>
													{modelTemplate}{" "}
													<span style={{ color: "var(--fg-5)", fontSize: 11 }}>
														(from model)
													</span>
												</span>
												<button
													type="button"
													className="btn ghost sm"
													onClick={() => {
														setChatTemplate(chatTemplate || modelTemplate);
														setOverrideOpen(true);
													}}
												>
													Override
												</button>
											</div>
										) : (
											<>
												<select
													className="input mono"
													value={chatTemplate}
													onChange={(e) => setChatTemplate(e.target.value)}
												>
													<option value="auto">Auto (GGUF embedded)</option>
													{/* Filter out the backend's own "auto" entry — it's rendered
                        above as a fixed first option. A template the render-lint
                        flagged invalid is disabled so it can't be pinned (it would
                        only crash the slot at cold-start). */}
													{templates
														.filter((t) => t.id !== "auto")
														.map((t) => (
															<option
																key={t.id}
																value={t.id}
																disabled={t.valid === false}
															>
																{(t.label || t.id) +
																	(t.valid === false ? "  ⚠ invalid" : "")}
															</option>
														))}
												</select>
												{(() => {
													const sel = templates.find(
														(t) => t.id === chatTemplate,
													);
													return sel && sel.valid === false ? (
														<div
															className="hint"
															style={{ color: "var(--err)", marginTop: 4 }}
														>
															⚠ Template failed to render: {sel.error}
														</div>
													) : null;
												})()}
												<button
													type="button"
													className="btn ghost sm"
													style={{ marginTop: 4 }}
													onClick={() => {
														setChatTemplate("");
														setOverrideOpen(false);
													}}
												>
													Clear override
												</button>
											</>
										)}
									</div>
								</div>
							);
						})()}

						{/* Continuous batching — the --parallel / -np sequence-slot count. Rides
          Save + a cold restart. */}
						{(() => {
							const t = slot.type || "llm";
							if (
								!["llm", "embedding", "reranking"].includes(t) ||
								device === "npu"
							)
								return null;
							const parNum = Number(String(parallel).trim());
							const showPool = Number.isInteger(parNum) && parNum > 1;
							const ctxNow = Number(String(ctx).trim()) || 0;
							return (
								<div className="form-row">
									<div className="form-lbl">
										<span>Parallel</span>
										<FieldInfoIcon description="⟳ How many requests can run at once. Empty = use the
											profile default." />
									</div>
									<div className="form-ctl">
										<input
											className={
												"input mono" + (fieldErrs.parallel ? " input-err" : "")
											}
											type="number"
											min="1"
											step="1"
											placeholder="1 (profile default)"
											value={parallel}
											onChange={(e) => {
												setParallel(e.target.value);
												setFieldErrs((p) => ({ ...p, parallel: undefined }));
											}}
										/>
										{showPool && (
											<div className="hint mono">
												{parNum} slots share the{" "}
												{ctxNow ? `${ctxNow.toLocaleString()}-token ` : ""}
												context pool (--kv-unified); worst case, {parNum}{" "}
												simultaneous full-context requests get ~
												{ctxNow
													? Math.floor(ctxNow / parNum).toLocaleString()
													: `ctx/${parNum}`}{" "}
												each.
											</div>
										)}
										{fieldErrs.parallel && (
											<div className="hint" style={{ color: "var(--err)" }}>
												{fieldErrs.parallel}
											</div>
										)}
									</div>
								</div>
							);
						})()}

						{/* Per-slot freeform override. Persisted to [server].extra_args. */}
						<div className="form-row">
							<div className="form-lbl">
								<span>Extra Args</span>
								<FieldInfoIcon description="extra_args — per-slot override flags appended to the runner
									argv. Takes precedence over the profile defaults." />
							</div>
							<div className="form-ctl">
								<input
									className="input mono"
									value={extraArgs}
									onChange={(e) => setExtraArgs(e.target.value)}
									placeholder="--flag value  (one-off, no new profile)"
									spellCheck={false}
									data-testid="extra-args-input"
								/>
								{extraArgsErr && (
									<div
										style={{
											color: "var(--err)",
											fontSize: 11,
											paddingTop: 4,
											fontFamily: "var(--jbm)",
										}}
									>
										{extraArgsErr}
									</div>
								)}
							</div>
						</div>
					</FieldGroup>
				)}
				{/* NPU capability matrix — replaces Model+Template for NPU slots */}
				{device === "npu" &&
					(() => {
						// Full catalogue per lane (installed + downloadable) — NOT filtered by
						// `installed`, so any tag can be picked and pulled on demand. Lane
						// split by tag family (whisper → ASR, embed → Embed, else Chat).
						const chatModels = flmModels.filter(
							(m) =>
								m.model &&
								!m.model?.toLowerCase().includes("whisper") &&
								!m.model?.toLowerCase().includes("embed"),
						);
						// Non-installed options carry a ⬇ marker; picking one downloads first.
						const optLabel = (m) =>
							m.installed ? m.model : `${m.model}  ⬇ download`;
						// True while THIS tag is downloading (used to gate + show progress).
						const pulling = (tag) => pull.inFlight && pull.modelId === tag;
						const pullPct = pull.pct != null ? `${pull.pct}%` : "…";
						return (
							<>
								<div className="form-row">
									<div className="form-lbl">
										<span>NPU · Chat</span>
										<FieldInfoIcon description="NPU language model. Pick any model — downloads
											automatically." />
									</div>
									<div className="form-ctl">
										<span
											style={{ display: "flex", alignItems: "center", gap: 8 }}
										>
											<PillToggle
												on={npuChat}
												disabled={npuPending || saving}
												label="Chat"
												stateText={npuChat ? "On" : "Off"}
												onToggle={(next) => {
													setNpuChat(next);
													applyNpu({ chat: next }, "chat");
												}}
											/>
											<select
												className="input mono"
												style={{ width: 200 }}
												value={npuChatModel}
												onChange={(e) =>
													onPickNpuModel("chat", "chatModel", e.target.value)
												}
												disabled={
													npuPending || saving || !npuChat || pull.inFlight
												}
											>
												{chatModels.map((m) => (
													<option key={m.model} value={m.model}>
														{optLabel(m)}
													</option>
												))}
											</select>
											{pulling(npuChatModel) && (
												<span style={{ fontSize: 11, color: "var(--accent)" }}>
													⬇ {pullPct}
												</span>
											)}
										</span>
									</div>
								</div>
								<div className="form-row">
									<div className="form-lbl">
										<span>NPU · ASR</span>
										<FieldInfoIcon description="Speech-to-text on the NPU (whisper). Shares the NPU
											process." />
									</div>
									<div className="form-ctl">
										<span
											style={{ display: "flex", alignItems: "center", gap: 8 }}
										>
											<PillToggle
												on={npuAsr}
												disabled={npuPending || saving || pull.inFlight}
												label="ASR"
												stateText={npuAsr ? "On" : "Off"}
												onToggle={(next) => {
													setNpuAsr(next);
													applyNpu({ asr: next }, "ASR");
												}}
											/>
											<span style={{ fontSize: 11, color: "var(--fg-5)" }}>
												whisper-v3 (fixed)
											</span>
										</span>
									</div>
								</div>
								<div className="form-row">
									<div className="form-lbl">
										<span>NPU · Embed</span>
										<FieldInfoIcon description="Text embeddings on the NPU (embed-gemma). Shares the NPU
											process." />
									</div>
									<div className="form-ctl">
										<span
											style={{ display: "flex", alignItems: "center", gap: 8 }}
										>
											<PillToggle
												on={npuEmbed}
												disabled={npuPending || saving || pull.inFlight}
												label="Embed"
												stateText={npuEmbed ? "On" : "Off"}
												onToggle={(next) => {
													setNpuEmbed(next);
													applyNpu({ embed: next }, "Embed");
												}}
											/>
											<span style={{ fontSize: 11, color: "var(--fg-5)" }}>
												embed-gemma (fixed)
											</span>
										</span>
									</div>
								</div>
								{npuErr && (
									<div className="hint" style={{ color: "var(--err)" }}>
										{npuErr}
									</div>
								)}
							</>
						);
					})()}

				{/* Task 4: Advanced fields (mostly read-only, profile-owned) are
          collapsed by default — minimal native <details> disclosure (no
          disclosure primitive exists in primitives.jsx). */}
				<details className="adv-disclosure">
					<summary
						className="form-section"
						style={{ cursor: "pointer", listStyle: "revert" }}
					>
						Advanced
					</summary>

					{/* Ngl, Parallel, and Extra Args moved to the Model section above.
          (rope_freq_base was removed — deprecated; set it via extra_args.) */}

					{/* Flags preview — backend-provided resolved_command (real podman argv).
          The resolved command is computed SERVER-SIDE (profile + MTP + image
          resolution), so when extra_args is dirty the displayed command is
          stale: dim it and overlay a Regenerate prompt that persists the slot
          override and refetches the freshly-resolved command.
          When the /resolved endpoint returns provenance data, we enhance this
          view with per-flag source badges and a duplicate-collapse note. */}
					<div className="form-section">Resolved command</div>
					<div style={{ position: "relative" }}>
						<div
							style={{
								padding: 12,
								background: "var(--bg)",
								border: "1px solid var(--line-soft)",
								borderRadius: "var(--rad-sm)",
								fontFamily: "var(--jbm)",
								fontSize: 11,
								color: "var(--fg-3)",
								lineHeight: 1.6,
								whiteSpace: "pre-wrap",
								opacity: extraArgsDirty ? 0.28 : 1,
								filter: extraArgsDirty ? "grayscale(1)" : "none",
								transition: "opacity .15s ease",
							}}
						>
							{(() => {
								// Prefer deduped argv from /resolved when available; fall back to
								// slot.resolved_command (list-payload) then a "not yet" sentinel.
								const resolvedData = resolvedQuery.data;
								const argv = resolvedData?.argv ?? null;
								if (Array.isArray(argv) && argv.length > 0) {
									return argv.join(" \\\n  ");
								}
								if (Array.isArray(slot.resolved_command)) {
									return slot.resolved_command.join(" \\\n  ");
								}
								return (
									slot.resolved_command ||
									"— not yet available (slot not loaded)"
								);
							})()}
						</div>
						{extraArgsDirty && (
							<div
								style={{
									position: "absolute",
									inset: 0,
									display: "flex",
									flexDirection: "column",
									alignItems: "center",
									justifyContent: "center",
									gap: 10,
									textAlign: "center",
									padding: 12,
								}}
								data-testid="resolved-stale-overlay"
							>
								<div
									style={{
										maxWidth: 360,
										padding: "12px 16px",
										background: "var(--bg-2)",
										border: "1px solid var(--line-soft)",
										borderRadius: "var(--rad-sm)",
										boxShadow: "0 4px 16px rgba(0,0,0,0.25)",
										display: "flex",
										flexDirection: "column",
										alignItems: "center",
										gap: 10,
									}}
								>
									<div
										style={{
											fontSize: 11.5,
											color: "var(--fg-2)",
											lineHeight: 1.5,
										}}
									>
										Flags changed. Slot{" "}
										<code style={{ fontFamily: "var(--jbm)" }}>extra_args</code>{" "}
										take precedence over the profile — regenerate to fold them
										into the resolved command.
									</div>
									<button
										className="btn sm"
										disabled={!!extraArgsErr || editMut.isPending}
										onClick={onRegenerateClick}
										data-testid="regenerate-resolved"
									>
										{editMut.isPending ? "Regenerating…" : "Regenerate"}
									</button>
								</div>
							</div>
						)}
					</div>
					{/* Provenance legend + per-flag badges — only when the /resolved endpoint
          returns data with at least one provenance entry. Gracefully absent for
          non-llama slots (argv null) or when the endpoint hasn't loaded yet. */}
					{(() => {
						const resolvedData = resolvedQuery.data;
						if (
							!resolvedData ||
							!Array.isArray(resolvedData.provenance) ||
							resolvedData.provenance.length === 0
						) {
							return null;
						}
						// Source → display label + CSS variable colour. These are the
						// segments the backend argv assembler actually emits today (the
						// old profile/extra_args segments are gone — profile flags are
						// copy-on-stamp into model defaults). metaFor() falls back to a
						// generic neutral badge carrying the raw label text, so a new
						// backend source renders instead of breaking the drawer.
						const SOURCE_META = {
							base: { label: "base", color: "var(--fg-4)" },
							model_extra_args: {
								label: "model_extra_args",
								color: "var(--accent)",
							},
							slot_hardware: { label: "slot_hardware", color: "var(--info)" },
							chat_template: { label: "chat_template", color: "var(--ok)" },
							mmproj: { label: "mmproj", color: "var(--warn)" },
						};
						const metaFor = (source) =>
							SOURCE_META[source] || {
								label: String(source || "unknown"),
								color: "var(--fg-3)",
							};
						// Legend: only the sources actually present in this slot's
						// provenance (deduped) — never a hardcoded trio the backend no
						// longer emits.
						const legendSources = [
							...new Set(resolvedData.provenance.map((e) => e.source)),
						];
						const badgeStyle = (source) => {
							const meta = metaFor(source);
							return {
								display: "inline-block",
								padding: "1px 5px",
								borderRadius: "var(--rad-sm)",
								border: `1px solid ${meta.color}`,
								color: meta.color,
								fontFamily: "var(--jbm)",
								fontSize: 9,
								lineHeight: 1.5,
								letterSpacing: "0.04em",
								verticalAlign: "middle",
								whiteSpace: "nowrap",
							};
						};
						return (
							<div style={{ marginTop: 8 }}>
								{/* Legend */}
								<div
									style={{
										display: "flex",
										alignItems: "center",
										gap: 8,
										paddingBottom: 6,
										flexWrap: "wrap",
									}}
								>
									<span
										style={{
											fontSize: 10,
											color: "var(--fg-5)",
											fontFamily: "var(--jbm)",
										}}
									>
										source:
									</span>
									{legendSources.map((src) => (
										<span key={src} style={badgeStyle(src)}>
											{metaFor(src).label}
										</span>
									))}
								</div>
								{/* Per-flag provenance rows */}
								<div
									style={{
										display: "flex",
										flexDirection: "column",
										gap: 2,
										padding: "8px 10px",
										background: "var(--bg)",
										border: "1px solid var(--line-soft)",
										borderRadius: "var(--rad-sm)",
									}}
								>
									{resolvedData.provenance.map((entry, i) => (
										<div
											key={i}
											style={{
												display: "flex",
												alignItems: "center",
												gap: 6,
												fontFamily: "var(--jbm)",
												fontSize: 10.5,
											}}
										>
											<span
												style={{
													color: "var(--fg-3)",
													minWidth: 120,
													flexShrink: 0,
												}}
											>
												{entry.flag}
												{entry.value != null && (
													<span style={{ color: "var(--fg-5)" }}>
														{" "}
														{entry.value}
													</span>
												)}
											</span>
											<span style={badgeStyle(entry.source)}>
												{metaFor(entry.source).label}
											</span>
										</div>
									))}
								</div>
								{/* Duplicate-collapse note */}
								{resolvedData.removed > 0 && (
									<div
										style={{
											marginTop: 5,
											fontSize: 10,
											color: "var(--fg-5)",
											fontFamily: "var(--jbm)",
										}}
									>
										{resolvedData.removed} duplicate flag
										{resolvedData.removed !== 1 ? "s" : ""} collapsed
									</div>
								)}
							</div>
						);
					})()}
					<div
						className="hint"
						style={{
							paddingTop: 6,
							fontSize: 10.5,
							color: "var(--fg-5)",
							fontFamily: "var(--jbm)",
						}}
					>
						Real podman argv: runner image (binary / image_pin) + model tune
						flags + slot hardware flags. Restart the slot to run with new
						flags.
					</div>
				</details>
			</Drawer>
			<DeleteSlotDialog
				open={delOpen}
				slot={slot}
				onClose={() => setDelOpen(false)}
				onDeleted={onClose}
			/>
			<RenameSlotDialog
				open={renameOpen}
				slot={slot}
				onClose={() => setRenameOpen(false)}
			/>
			{/* Stacked model editor — the reusable ModelDrawer (window-global,
	        same instance contract as models.jsx). Rendered later in the DOM at
	        equal z-index so it fully overlays this drawer; its own save path
	        closes it and returns here with every slot edit intact. */}
			<ModelDrawer
				open={modelEditOpen && !!curModelRow}
				onClose={() => setModelEditOpen(false)}
				model={curModelRow}
			/>
			<ConfirmDialog
				open={discardOpen}
				onCancel={() => setDiscardOpen(false)}
				onConfirm={() => {
					setDiscardOpen(false);
					onClose();
				}}
				title="Discard unsaved changes?"
				message={
					<span>
						<span className="mono" style={{ color: "var(--fg)" }}>
							{slot.name}
						</span>{" "}
						has unsaved edits — closing the drawer discards them.
					</span>
				}
				confirmLabel="Discard"
			/>
			<ConfirmDialog
				open={!!pendingSwap}
				onCancel={() => setPendingSwap(null)}
				onConfirm={() => {
					const p = pendingSwap;
					setPendingSwap(null);
					if (p) fireSwap(p.id, p.label);
				}}
				title={`Swap model on running slot ${slot.name}?`}
				message={
					<span>
						Loading{" "}
						<span className="mono" style={{ color: "var(--fg)" }}>
							{pendingSwap?.label || ""}
						</span>{" "}
						cold-restarts the container (~model-load seconds). The slot is
						unavailable while it reloads.
					</span>
				}
				confirmLabel="Swap model"
			/>
		</>
	);
}

function ReadOnlyStrip({ k, v }) {
	return (
		<div
			style={{
				padding: "10px 12px",
				borderRight: "1px solid var(--line-soft)",
				background: "var(--bg)",
			}}
		>
			<div
				className="mono"
				style={{
					fontSize: 9,
					color: "var(--fg-4)",
					textTransform: "uppercase",
					letterSpacing: "0.08em",
					marginBottom: 3,
				}}
			>
				{k}
			</div>
			<div className="mono" style={{ fontSize: 12, color: "var(--fg)" }}>
				{v}
			</div>
		</div>
	);
}

// ─── Inline swap popover ────────────────────────────────────────
function InlineSwapPopover({ slot, open, onClose, onPick }) {
	// Hooks first — React rules-of-hooks forbid an early return before
	// them. The popover is mounted unconditionally and toggles via `open`;
	// useQuery's own caching means useModels() costs ~nothing when closed.
	const modelsQuery = useModels();
	const hwQuery = useHardware();
	// UI-5 (state-driven): a live-container pick is stashed here and confirmed
	// through the shared ConfirmDialog before committing. Must be declared
	// before the early return (rules-of-hooks).
	const [pendingPick, setPendingPick] = useStateSM(null);
	if (!open) return null;

	const isContainer = slot.runtime === "container";
	const ramFreeGb = hwQuery.data?.ram?.free ?? 0;
	// ROCmFP4-quantized models only run on the custom rocm fork binary — don't
	// offer them when swapping a non-rocm slot (shared compatibleModels filter).
	const compatible = compatibleModels(modelsQuery.data, {
		type: slot.type,
		backend: slot.backend,
	});

	// N2: container swap = cold systemctl restart (not a hot in-place swap).
	// Intercept onPick for container slots: toast the restart and fire the same
	// onPick (which drives restart), so the parent card drives to "starting"
	// state immediately. The parent's onSwapPick calls useSlotSwap which
	// triggers a restart for container slots server-side.
	const commitPick = (m) => {
		if (isContainer) {
			window.__hal0Toast &&
				window.__hal0Toast(
					`Restarting ${slot.name} to load ${m.longName || m.id} — ~model-load seconds`,
					"info",
				);
		}
		onPick(m);
		onClose();
	};
	const handlePick = (m) => {
		// UI-5: confirm before cold-restarting a LIVE container slot. Cancelling
		// the dialog leaves the popover open, same as declining the old
		// window.confirm.
		const live = slotButtonPhase(slot) === "running";
		if (isContainer && live) {
			setPendingPick(m);
			return;
		}
		commitPick(m);
	};

	return (
		<div className="swap-pop" onClick={(e) => e.stopPropagation()}>
			{/* N2: container cold-restart notice in popover header */}
			<div className="swap-pop-h">
				Swap model · type {slot.type}
				{isContainer && (
					<span
						className="chip"
						style={{
							marginLeft: 8,
							fontSize: 9,
							color: "var(--warn)",
							borderColor: "var(--warn-line)",
							background: "var(--warn-soft)",
						}}
						title="Container runtime — model swap requires a container restart (~model-load seconds)"
					>
						· cold restart
					</span>
				)}
			</div>
			{compatible.map((m) => {
				const isCur = slot.model_id === m.id;
				const fits = ramFreeGb > parseSizeGB(m.size);
				return (
					// The whole row is a mouse-click target (convenience) but the
					// nested chevron button is the single keyboard/AT-accessible
					// affordance — making the row also a role=button creates a
					// double-announce for screen readers (a11y review 2026-05-27).
					<div
						key={m.id}
						className={"swap-pop-item" + (isCur ? " cur" : "")}
						onClick={() => handlePick(m)}
					>
						<div className="nm">
							{m.longName}
							<FieldInfoIcon description={m.repo} />
						</div>
						<div className="sz num">{m.size}</div>
						<div className={"fit" + (fits ? "" : " no")}>
							{m.installed ? (fits ? "fits ✓" : "tight") : "will pull"}
						</div>
						<button
							type="button"
							className="swap-arrow"
							aria-label={`Load ${m.longName || m.id}`}
							onClick={(e) => {
								e.stopPropagation();
								handlePick(m);
							}}
						>
							{Icons.chevR}
						</button>
					</div>
				);
			})}
			<div
				className="swap-pop-h"
				style={{ cursor: "pointer", color: "var(--accent)" }}
				onClick={() => {
					onClose();
					window.location.hash = "#models";
				}}
			>
				+ Browse all models →
			</div>
			<ConfirmDialog
				open={!!pendingPick}
				onCancel={() => setPendingPick(null)}
				onConfirm={() => {
					const m = pendingPick;
					setPendingPick(null);
					if (m) commitPick(m);
				}}
				title={`Swap model on running slot ${slot.name}?`}
				message={
					<span>
						Loading{" "}
						<span className="mono" style={{ color: "var(--fg)" }}>
							{pendingPick?.longName || pendingPick?.id || ""}
						</span>{" "}
						cold-restarts the container (~model-load seconds). The slot is
						unavailable while it reloads.
					</span>
				}
				confirmLabel="Swap model"
			/>
		</div>
	);
}

// ─── Slot logs drawer ────────────────────────────────────────────
// Raw per-slot journald tail, backed by the shared `useSlotLogsStream`
// hook (same transport the Logs page "slot" channel uses). The hook now
// owns backfill (so the one-shot model-loading lines are visible even when
// the drawer opens after the slot is up), idle-spam filtering, capped
// backoff reconnect, and the `degraded` frame — replacing the old inline
// EventSource with a no-op onerror.
function SlotLogsDrawer({ open, slot, onClose }) {
	const { ring, disconnected, degraded } = useSlotLogsStream(
		open && slot ? slot.name : null,
		{ follow: open, max: 2000 },
	);

	const viewRef = useRefSM(null);
	const [autoScroll, setAutoScroll] = useStateSM(true);
	const [wrap, setWrap] = useStateSM(true);
	const [atBottom, setAtBottom] = useStateSM(true);
	const [copied, setCopied] = useStateSM(false);

	const lines = ring.map((r) => r.msg);
	const text = lines.join("\n");
	const count = lines.length;

	// Reset the follow/scroll state each time the drawer opens for a slot so a
	// freshly-opened drawer always starts pinned to the newest line.
	useEffectSM(() => {
		if (open) {
			setAutoScroll(true);
			setAtBottom(true);
		}
	}, [open, slot && slot.name]);

	// Pin to the newest line whenever the ring grows (or the wrap mode changes,
	// which reflows and shifts scrollHeight) — but only while following.
	useEffectSM(() => {
		if (!autoScroll) return;
		const el = viewRef.current;
		if (el) el.scrollTop = el.scrollHeight;
	}, [count, autoScroll, wrap]);

	// Following is driven by scroll position: scrolling away from the bottom
	// pauses auto-scroll; scrolling back resumes it.
	const onScroll = useCallbackSM(() => {
		const el = viewRef.current;
		if (!el) return;
		const bottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
		setAtBottom(bottom);
		setAutoScroll(bottom);
	}, []);

	const jumpToLatest = () => {
		const el = viewRef.current;
		if (el) el.scrollTop = el.scrollHeight;
		setAutoScroll(true);
		setAtBottom(true);
	};

	const copyAll = async () => {
		if (!text) return;
		try {
			await navigator.clipboard.writeText(text);
			setCopied(true);
			setTimeout(() => setCopied(false), 1500);
		} catch {
			window.__hal0Toast && window.__hal0Toast("Clipboard unavailable", "warn");
		}
	};

	if (!slot) return null;

	const toggleStyle = (on) => ({
		display: "inline-flex",
		alignItems: "center",
		gap: 5,
		padding: "3px 9px",
		fontSize: 11,
		borderRadius: "var(--rad-sm)",
		border: "1px solid " + (on ? "var(--accent-line)" : "var(--line)"),
		background: on ? "var(--accent-soft)" : "transparent",
		color: on ? "var(--accent)" : "var(--fg-3)",
		cursor: "pointer",
	});

	return (
		<Drawer
			open={open}
			onClose={onClose}
			eyebrow={`Slots · /slots/${slot.name}/logs`}
			title={`Logs — ${slot.name}`}
			width={720}
			foot={
				<span style={{ display: "inline-flex", gap: 8, marginLeft: "auto" }}>
					<button className="btn ghost sm" onClick={onClose}>
						Close
					</button>
				</span>
			}
		>
			<div
				style={{
					height: "100%",
					display: "flex",
					flexDirection: "column",
					minHeight: 0,
				}}
			>
				{degraded && (
					<div
						className="mono"
						data-testid="slot-logs-degraded"
						style={{
							background: "var(--warn-soft)",
							border: "1px solid var(--warn-line)",
							borderRadius: "var(--rad-sm)",
							padding: "8px 10px",
							fontSize: 11.5,
							color: "var(--warn)",
							lineHeight: 1.5,
							marginBottom: 8,
							flex: "0 0 auto",
						}}
					>
						{degraded}
					</div>
				)}

				{/* ─── Controls toolbar ─── */}
				<div
					className="mono"
					style={{
						display: "flex",
						alignItems: "center",
						gap: 8,
						flexWrap: "wrap",
						marginBottom: 8,
						flex: "0 0 auto",
					}}
				>
					<button
						type="button"
						data-testid="slot-logs-follow"
						aria-pressed={autoScroll}
						title="Auto-scroll to the newest line"
						style={toggleStyle(autoScroll)}
						onClick={() => {
							if (autoScroll) {
								setAutoScroll(false);
							} else {
								jumpToLatest();
							}
						}}
					>
						{Icons.download} {autoScroll ? "Following" : "Follow"}
					</button>
					<button
						type="button"
						data-testid="slot-logs-wrap"
						aria-pressed={wrap}
						title="Wrap long lines"
						style={toggleStyle(wrap)}
						onClick={() => setWrap((w) => !w)}
					>
						{Icons.logs} Wrap
					</button>
					<button
						type="button"
						data-testid="slot-logs-copy"
						title="Copy all log lines"
						disabled={count === 0}
						style={{
							...toggleStyle(false),
							opacity: count === 0 ? 0.5 : 1,
							cursor: count === 0 ? "default" : "pointer",
						}}
						onClick={copyAll}
					>
						{copied ? Icons.check : Icons.copy} {copied ? "Copied" : "Copy"}
					</button>
					<span
						style={{
							marginLeft: "auto",
							display: "inline-flex",
							alignItems: "center",
							gap: 10,
							fontSize: 11,
						}}
					>
						<span style={{ color: "var(--fg-4)" }}>
							{count} line{count === 1 ? "" : "s"}
						</span>
						<span
							style={{
								display: "inline-flex",
								alignItems: "center",
								gap: 5,
								color: degraded
									? "var(--warn)"
									: disconnected
										? "var(--warn)"
										: "var(--ok)",
							}}
						>
							<span
								style={{
									width: 6,
									height: 6,
									borderRadius: "50%",
									background: "currentColor",
									boxShadow:
										"0 0 0 2px color-mix(in srgb, currentColor 24%, transparent)",
								}}
							/>
							{degraded
								? "unavailable"
								: disconnected
									? "reconnecting"
									: "live"}
						</span>
					</span>
				</div>

				{/* ─── Log viewport (fills remaining height) ─── */}
				<div style={{ position: "relative", flex: "1 1 auto", minHeight: 0 }}>
					<div
						ref={viewRef}
						onScroll={onScroll}
						className="mono"
						data-testid="slot-logs-view"
						style={{
							background: "var(--bg)",
							border: "1px solid var(--line-soft)",
							borderRadius: "var(--rad-sm)",
							padding: 10,
							fontSize: 11.5,
							color: "var(--fg-2)",
							lineHeight: 1.5,
							height: "100%",
							overflow: "auto",
							whiteSpace: wrap ? "pre-wrap" : "pre",
							wordBreak: wrap ? "break-word" : "normal",
						}}
					>
						{count === 0 ? (
							<span style={{ color: "var(--fg-4)", fontStyle: "italic" }}>
								{degraded
									? "No log lines — see the notice above."
									: disconnected
										? "Reconnecting to log stream…"
										: "waiting for log lines…"}
							</span>
						) : (
							text
						)}
					</div>

					{/* Jump-to-latest pill — only while scrolled away from the bottom. */}
					{!atBottom && count > 0 && (
						<button
							type="button"
							data-testid="slot-logs-jump"
							onClick={jumpToLatest}
							className="mono"
							style={{
								position: "absolute",
								bottom: 12,
								left: "50%",
								transform: "translateX(-50%)",
								display: "inline-flex",
								alignItems: "center",
								gap: 6,
								padding: "5px 12px",
								fontSize: 11,
								borderRadius: 999,
								border: "1px solid var(--accent-line)",
								background: "var(--accent)",
								color: "var(--bg)",
								cursor: "pointer",
								boxShadow: "0 4px 14px -4px rgba(0,0,0,0.5)",
							}}
						>
							{Icons.download} Jump to latest
						</button>
					)}
				</div>
			</div>
		</Drawer>
	);
}

// ─── Empty SlotCard (no model loaded) ────────────────────────────
function EmptySlotCard({ name, type, device, onConfigure }) {
	return (
		<div
			className="slot"
			style={{ borderStyle: "dashed", borderColor: "var(--line)" }}
		>
			<div className="slot-h">
				<span className="dot empty" />
				<div className="slot-name">
					<span className="nm" style={{ color: "var(--fg-3)" }}>
						{name}
					</span>
				</div>
			</div>
			<div
				style={{
					padding: "8px 10px",
					background: "var(--bg)",
					border: "1px dashed var(--line-soft)",
					borderRadius: "var(--rad-sm)",
					fontFamily: "var(--jbm)",
					fontSize: 12,
					color: "var(--fg-4)",
					fontStyle: "italic",
				}}
			>
				no model loaded
			</div>
			<div className="slot-chips">
				<span className="chip">{type}</span>
				<span className={"chip dev-" + (device || "cpu").replace("gpu-", "")}>
					{device}
				</span>
			</div>
			<div
				style={{
					padding: "10px 12px",
					background: "var(--accent-soft)",
					border: "1px solid var(--accent-line)",
					borderRadius: "var(--rad-sm)",
					display: "flex",
					alignItems: "center",
					gap: 8,
				}}
			>
				<span
					className="mono"
					style={{ fontSize: 11, color: "var(--accent)", flex: 1 }}
				>
					seeded · ready to configure
				</span>
				<button className="btn sm" onClick={onConfigure}>
					{Icons.plus} Configure
				</button>
			</div>
		</div>
	);
}

// ─── Image pull progress bar ─────────────────────────────────────
function ImagePullBar({ pull }) {
	// pull: ImagePullSnapshot from useSlotImagePull()
	const { state, layer, totalLayers, image, error } = pull;
	if (state !== "pulling" && state !== "completed" && state !== "failed")
		return null;
	const pct = totalLayers > 0 ? Math.round((layer / totalLayers) * 100) : null;
	// Truncate the image tag to the last segment for display.
	const imgShort = image ? image.split("/").pop() : null;
	const label =
		state === "completed"
			? `Image ready`
			: state === "failed"
				? `Pull failed${error ? `: ${error}` : ""}`
				: totalLayers > 0
					? `Pulling image${imgShort ? ` ${imgShort}` : ""}… (layer ${layer}/${totalLayers})`
					: `Pulling image${imgShort ? ` ${imgShort}` : ""}…`;
	const barColor =
		state === "failed"
			? "var(--err)"
			: state === "completed"
				? "var(--ok)"
				: "var(--accent)";
	return (
		<div style={{ marginTop: 6 }}>
			<div
				aria-live="polite"
				aria-label={label}
				style={{
					fontFamily: "var(--jbm)",
					fontSize: 11,
					color: state === "failed" ? "var(--err)" : "var(--fg-2)",
					marginBottom: 4,
				}}
			>
				{label}
			</div>
			<div
				style={{
					height: 3,
					background: "var(--bg-2)",
					borderRadius: 2,
					overflow: "hidden",
				}}
			>
				{/* Correct ARIA pattern: omit aria-valuenow entirely while the
            layer count is unknown (indeterminate progressbar). */}
				<div
					role="progressbar"
					{...(pct !== null ? { "aria-valuenow": pct } : {})}
					aria-valuemin={0}
					aria-valuemax={100}
					style={{
						height: "100%",
						width: pct !== null ? `${pct}%` : "40%",
						background: barColor,
						borderRadius: 2,
						transition: "width 0.3s ease",
						// Indeterminate animation when layer count unknown.
						animation:
							pct === null && state === "pulling"
								? "hal0-indeterminate 1.4s ease infinite"
								: "none",
					}}
				/>
			</div>
		</div>
	);
}

// ─── Error SlotCard ─────────────────────────────────────────────
function ErrorSlotCardBanner({ slot, message }) {
	const pull = useSlotImagePull();
	const loadMut = useSlotLoad();
	const isPulling = pull.slotName === slot?.name && pull.inFlight;

	// Retry was toast-only. A "load failed" banner means the slot's child never
	// came up, so Retry re-attempts the load (POST /api/slots/{name}/load) —
	// the same mutation the SlotCard's Start uses. Query invalidation refreshes
	// the card on success.
	const handleRetry = async () => {
		if (!slot?.name) return;
		try {
			await loadMut.mutateAsync(slot.name);
			window.__hal0Toast &&
				window.__hal0Toast(`Retrying load for ${slot.name}`, "info");
		} catch (err) {
			window.__hal0Toast &&
				window.__hal0Toast(
					`Retry failed for ${slot.name}: ${err?.message || err}`,
					"warn",
				);
		}
	};

	const handleRePull = async () => {
		if (!slot?.name) return;
		try {
			await pull.start(slot.name);
		} catch (err) {
			window.__hal0Toast &&
				window.__hal0Toast(
					`Re-pull failed for ${slot.name}: ${err?.message || err}`,
					"warn",
				);
		}
	};

	return (
		<div
			style={{
				padding: "10px 12px",
				background: "var(--err-soft)",
				border: "1px solid var(--err-line)",
				borderRadius: "var(--rad-sm)",
				display: "flex",
				alignItems: "flex-start",
				gap: 8,
			}}
		>
			<span style={{ color: "var(--err)", display: "inline-flex" }}>
				{Icons.warn}
			</span>
			<div
				style={{
					flex: 1,
					fontFamily: "var(--jbm)",
					fontSize: 11.5,
					color: "var(--fg-2)",
					lineHeight: 1.5,
				}}
			>
				<div style={{ color: "var(--err)", fontWeight: 500, marginBottom: 2 }}>
					load failed
				</div>
				<div>{message}</div>
				{(isPulling || pull.state === "completed" || pull.state === "failed") &&
					pull.slotName === slot?.name && <ImagePullBar pull={pull} />}
				<div style={{ display: "flex", gap: 6, marginTop: 6 }}>
					<button
						className="btn ghost sm"
						disabled={loadMut.isPending}
						onClick={handleRetry}
					>
						{Icons.restart} {loadMut.isPending ? "Retrying…" : "Retry"}
					</button>
					<button
						className="btn ghost sm"
						disabled={isPulling}
						onClick={handleRePull}
						title="Re-pull the container image from the registry"
					>
						{Icons.download} {isPulling ? "Pulling…" : "Re-pull"}
					</button>
				</div>
			</div>
		</div>
	);
}

// CreateSlotModal is now dash/slots/CreateSlotModal.jsx (D2 decomposition).
Object.assign(window, {
	EditSlotDrawer,
	InlineSwapPopover,
	EmptySlotCard,
	ErrorSlotCardBanner,
	SlotLogsDrawer,
});
