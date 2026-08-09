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
	useSlots,
} from "@/api/hooks/useSlots";
import { useHardware } from "@/api/hooks/useHardware";
import { useModels, usePullJob } from "@/api/hooks/useModels";
import { useProfiles } from "@/api/hooks/useProfiles";
import { useSystemInfo, deviceBackend } from "@/api/hooks/useRuntimes";
import { useSlotLogsStream } from "@/api/hooks/useLogs";
import { ENDPOINTS } from "@/api/endpoints";
import { normalizeApiModel, isUpstreamModel } from "@/lib/normalizeApiModel";
import { stateChipClassForSlot, slotButtonPhase } from "./slot-status.js";
import { npuModalityOn } from "./npu-modality.js";
import { slotModelRow, npuAnchorSlot } from "./slots/slot-shared.js";

// The slot edit drawer's own width, and therefore the offset the stacked model
// drawer docks at so its right edge lands flush against this drawer's left one.
// ONE constant: a width that drifted from the dock offset would leave a gap or
// an overlap with no obvious cause.
const SLOT_DRAWER_WIDTH = 560;

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

// ─── Drawer dirty-tracking seam (#1398) ──────────────────────────
//
// The drawer seeds its form ONCE from the slot payload, but `useSlots`
// re-derives that payload every 5s. Comparing a once-seeded form against a
// live-polled prop let the two sides drift with no operator input, and the
// drawer read that drift as "the operator changed this field":
//
//   #1390 — the ctx baseline fell back to `slot.metrics.ctx`, a RUNTIME
//           metric. A slot that started serving under the open drawer made an
//           untouched Context field dirty, and Save persisted a context window
//           nobody chose.
//   #1391 — a dropped `/api/slots` poll degrades the entry to the bare
//           `/api/status` shape, which carries no config enrichment at all.
//           For that interval EVERY batched field read dirty, so an idle Save
//           rewrote chat_template/binary/NGL and — because a hardware key is
//           in the restart trigger — fired a cold restart.
//
// The fix is structural, not two point patches: snapshot the persisted config
// ONCE from the enriched payload the drawer opened with, freeze it, and derive
// every dirty predicate AND the Save body from that snapshot. A background
// poll then has nothing to move.
//
// Baseline purity rule: every field below is a PERSISTED config value.
// `ctxSeed` is the single concession — it mirrors the *display* seed, which
// may fall back to the live metric when nothing is on disk — but because it is
// frozen at the same instant as the display value, an untouched field can
// never become dirty. Nothing here may read a runtime metric at compare time.
//
// #1379: chat_template, parallel and [server].extra_args are deliberately NOT
// baselined. They are sunset — inert at launch (spec-flags-ownership §1/§4;
// `providers/container.py` does `del profile_flags, slot_parallel,
// extra_args`) — so the drawer no longer edits them. Baselining them would
// re-arm the save path: the desired value of an absent control reads as
// "empty", which against a persisted value looks like a deliberate clear. A
// slot TOML that still carries them is left strictly alone; folding them into
// the bound model is `hal0 slot migrate-flags` (#1396/#1397).
function configBaseline(slot) {
	if (!slot) return null;
	return {
		name: slot.name,
		// The PERSISTED context window ([model].context_size). null = no
		// override on disk; the drawer must not invent one.
		ctxMax: slot.ctx_max ?? null,
		// Frozen display seed: persisted value → live metric at open → the
		// backend's 8192 floor. Mirrors the `setCtx` seed exactly.
		ctxSeed: slot.ctx_max ?? (slot.metrics?.ctx || 8192),
		device: slot.device || "gpu-rocm",
		nGpuLayers: slot.n_gpu_layers ?? -1,
		threads: slot.threads ?? 0,
		binary: slot.binary || "",
		imagePin: slot.image_pin ?? null,
		profile: slot.profile || "",
	};
}

// One derived comparison, two consumers — the unsaved-changes guard and the
// Save body both read THIS. #1372 was a predicate that existed in two places
// and drifted apart; deriving it once makes that failure unrepresentable
// (#1398, direction 1). Returns null when there is no trustworthy baseline, in
// which case the drawer refuses to compute dirtiness at all.
function deriveChanges(baseline, form) {
	if (!baseline) return null;
	// Normalised "unset" seeds: -1 NGL, 0 THREADS, empty image_pin.
	const nglRaw = String(form.nGpuLayers).trim();
	const nglValue = nglRaw === "" ? -1 : Number(nglRaw);
	const thrRaw = String(form.threads).trim();
	const thrValue = thrRaw === "" ? 0 : Number(thrRaw);
	const pinValue = (form.imagePin || "").trim() || null;
	const ctxValue = Number(String(form.ctx).trim());
	return {
		// Normalised values, so the Save body ships exactly what was compared.
		nglValue,
		thrValue,
		pinValue,
		ctxValue,
		ctx: ctxValue !== Number(baseline.ctxSeed),
		device: form.device !== baseline.device,
		ngl: nglValue !== baseline.nGpuLayers,
		threads: thrValue !== baseline.threads,
		binary: form.binary !== baseline.binary,
		imagePin: pinValue !== baseline.imagePin,
		profile: form.profile !== baseline.profile,
	};
}

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
	// Full slot list — needed only to resolve a shadow's anchor STRUCTURALLY
	// (see the NPU-modalities row below); #1637 already pays this same query
	// in npu-pane.jsx, so react-query serves it from cache here.
	const slotsQuery = useSlots();
	// HW grid (spec-hw-slot-ownership §2): BINARY options + fit-check metadata
	// from system-info (RUNNER_IMAGES). The device ENUM is no longer read here —
	// device rides the model at creation and is not editable post-create, so the
	// drawer only ever *displays* the slot's persisted device.
	const systemInfoQuery = useSystemInfo();

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
	// Runner Image is a catalog dropdown (the RUNNER_IMAGES refs system-info
	// reports — same source as the Runtimes page). `pinCustom` flips the
	// control to a free-text input for the debug-build/A-B/rollback escape
	// hatch the field originally existed for.
	const [pinCustom, setPinCustom] = useStateSM(false);
	// Runtime profile (SlotConfig.profile) — picks the runtime family,
	// device-class gating and MTP draft backend. NOT a flags source at launch:
	// profile flags are copy-on-stamp into model.defaults.extra_args (model
	// drawer). Rides Save (PUT /config {profile}), restart-required; the
	// backend's _reconcile_device_profile keeps device/profile coherent.
	const [profileSel, setProfileSel] = useStateSM(slot?.profile || "");
	// Eviction priority (spec 2026-08-02) — instant-apply on blur/Enter, NOT
	// part of the Save batch (see the pinned-toggle-shaped handler below).
	// Hooks must execute every render (see the note atop this component), so
	// this is declared here rather than next to its handler.
	const [prio, setPrio] = useStateSM(
		Number.isInteger(slot?.priority) ? slot.priority : 50,
	);
	// (#1379: `parallel` and `extra_args` state removed with their controls —
	// both are INERT at launch, so editing them here only wrote TOML nothing
	// reads. See configBaseline above.)
	// #1391: does this payload actually carry the TOML-derived config fields, or
	// is it the bare /api/status shape a dropped /api/slots poll falls back to?
	// Provenance from useSlots — an absent key can't answer it (see
	// `Slot._configEnriched`). `undefined` means "not from useSlots", which is
	// treated as trustworthy so other slot sources keep working.
	const configEnriched = slot?._configEnriched !== false;
	// The frozen persisted-config baseline (see configBaseline above). Never
	// seeded from a degraded payload, never refreshed by a background poll —
	// only on slot identity change, drawer close, or an explicit save success.
	const [baseline, setBaseline] = useStateSM(() =>
		slot && configEnriched ? configBaseline(slot) : null,
	);
	// Name the current baseline belongs to. Guards the re-seed effect so a poll
	// that merely RECOVERS from a degraded interval doesn't wipe the operator's
	// in-flight edits (the enrichment flag flipping false→true re-runs it).
	// `slot` can be undefined here while `baseline` is still set: a save's
	// invalidation can drop the slot from one poll before the cleanup effect
	// below runs, and this initializer argument is evaluated on that render.
	const baselineFor = useRefSM(baseline && slot ? slot.name : null);
	const [submitErr, setSubmitErr] = useStateSM(null);
	// Dirty-close confirms through the shared ConfirmDialog (state-driven),
	// replacing the raw window.confirm. Every dismiss path (Cancel, ✕, Esc,
	// backdrop) funnels through requestClose below.
	const [discardOpen, setDiscardOpen] = useStateSM(false);
	// In-drawer navigation target (anchor link on trio shadows) held while the
	// discard dialog confirms — hash assignment must not bypass the dirty
	// guard, or the re-seed effect silently drops pending Save-batched edits.
	const [pendingNav, setPendingNav] = useStateSM(null);
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
	// (#1379: the per-slot chat_template override state went with its control.
	// `chat_template` is a typed MODEL field in both specs — `resolve_chat_template`
	// says the per-slot key "is no longer consulted" — so the slot-tier editor
	// wrote TOML the launch path ignores, and fired a cold restart to apply it.)
	// Task 3 (NPU modality toggles): asr/embed instant-apply + cold restart for
	// device=npu slots. Seeded from slot.npu ({asr,embed}); optimistic with
	// revert-on-error.
	const [npuAsr, setNpuAsr] = useStateSM(npuModalityOn(slot?.npu, "asr"));
	const [npuEmbed, setNpuEmbed] = useStateSM(npuModalityOn(slot?.npu, "embed"));
	const [npuChat, setNpuChat] = useStateSM(npuModalityOn(slot?.npu, "chat"));
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
		// Anchor only — the trio shadows render no model pickers (their [npu]
		// section is a pointer at the anchor), so skip the catalogue fetch.
		if (device !== "npu" || slot?.type !== "llm") return;
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

	// Seed the form AND snapshot the baseline — deliberately the same effect, so
	// the two can never come from different payloads (which is the #1398 class).
	// Runs on slot identity change only; a degraded payload is skipped outright
	// and a recovery from one is a no-op.
	useEffectSM(() => {
		if (!slot) {
			// Drawer closed — the parent passes slot=undefined. Drop the snapshot
			// so a reopen re-seeds from a fresh payload.
			baselineFor.current = null;
			setBaseline(null);
			return;
		}
		// #1391: a bare /api/status entry's config fields are
		// absent-because-missing, not absent-because-unset. Baselining from it
		// would make every field read dirty. Hold what we have (Save is gated on
		// `degraded` below) and wait for the next good poll.
		if (!configEnriched) return;
		// Already holding this slot's baseline: this run is a degraded→enriched
		// recovery, not a new slot. Re-seeding here would discard live edits.
		if (baselineFor.current === slot.name) return;
		baselineFor.current = slot.name;
		setBaseline(configBaseline(slot));
		setCtx(slot.ctx_max ?? (slot.metrics?.ctx || 8192));
		// HW grid re-seed from the (possibly-updated) slot prop.
		setDevice(slot.device || "gpu-rocm");
		setNGpuLayers(slot.n_gpu_layers != null ? String(slot.n_gpu_layers) : "-1");
		setThreads(slot.threads != null ? String(slot.threads) : "0");
		setBinary(slot.binary || "");
		setImagePin(slot.image_pin || "");
		setPinCustom(false);
		setProfileSel(slot.profile || "");
		setSubmitErr(null);
		setDiscardOpen(false);
		setPendingNav(null);
		setPendingSwap(null);
		setModelEditOpen(false);
		setFieldErrs({});
		setNpuAsr(npuModalityOn(slot.npu, "asr"));
		setNpuEmbed(npuModalityOn(slot.npu, "embed"));
		// Re-seed the chat pill + all three model selects too, so a save +
		// refetch keeps the drawer in sync with server truth instead of
		// drifting until the drawer is remounted.
		setNpuChat(npuModalityOn(slot.npu, "chat"));
		setNpuChatModel(
			slot.modelDefault || slot.model_id || slot.model || "qwen3:4b",
		);
		setNpuPending(false);
		setNpuErr(null);
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [slot?.name, configEnriched]);

	// Bound model row, resolved BEFORE the `!slot` guard below: the effect
	// that follows must run on every render, and anything after an early
	// return does not (React counts hooks positionally). slotModelRow is the
	// shared resolver (dash/slots/slot-shared.js) the slot card uses too.
	const curModelRow = slotModelRow(slot, modelsQuery.data);
	// ModelDrawer renders nothing for a null model and never calls onClose in
	// that path, so a models refetch that drops the bound row would otherwise
	// leave modelEditOpen stuck true (dead ✕/Esc, and a surprise re-stack when
	// the row comes back). Clear the flag whenever the stacked editor has
	// nothing to render — or when this drawer itself closes.
	useEffectSM(() => {
		if (!open || !curModelRow) setModelEditOpen(false);
	}, [open, curModelRow]);
	// Re-seed `prio` from server truth whenever the slot's persisted priority
	// changes (poll refresh, or this drawer's own commitPriority resolving) —
	// same shape as the config-baseline effect above, but for an instant-apply
	// field that never enters `baseline`. Must run every render (see above).
	useEffectSM(() => {
		if (Number.isInteger(slot?.priority)) setPrio(slot.priority);
	}, [slot?.priority]);

	// Screen-reader descriptions for the header toggles — the hover `title`
	// alone is unreachable for keyboard/touch/SR users (Codex review, #1638).
	// Hooks: must sit ABOVE the `!slot` early return (positional counting).
	const autoloadDescId = React.useId();
	const pinDescId = React.useId();

	if (!slot) return null;

	async function onSaveClick() {
		setSubmitErr(null);
		// #1391: with no trustworthy baseline there is no honest answer to "what
		// did the operator change?", so writing anything would be a guess. The
		// button is already disabled — this is the belt to that brace.
		if (!changes) return;
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
		// image_pin: empty is allowed (release default). When set, must look like a
		// registry ref — contains ":" (host:tag or repo:tag) and no whitespace.
		const pinTrim = (imagePin || "").trim();
		if (pinTrim && (!pinTrim.includes(":") || /\s/.test(pinTrim))) {
			errs.imagePin =
				"Must look like a registry ref (e.g. ghcr.io/owner/repo:tag)";
		}
		// (#1379: the extra_args shlex gate is gone with the field. It only ever
		// guarded a value the launch path ignores, and #1389 was that gate vetoing
		// unrelated saves from an unmounted subtree — a whole failure mode that
		// removing the control makes unrepresentable.)
		if (Object.keys(errs).length > 0) {
			setFieldErrs(errs);
			return;
		}
		setFieldErrs({});
		// Every "did this change?" question below is answered by the SAME derived
		// map the unsaved-changes guard reads (`changes`, built by deriveChanges
		// from the frozen baseline). Nothing here re-derives a predicate, and
		// nothing here reads the live `slot` prop — that pairing is the #1398
		// bug class: a once-seeded form compared against a 5s-polled payload.
		// A hardware change (device/NGL/threads/binary/image_pin/profile) needs a
		// cold restart. #1379: `chat_template` is NO LONGER in this trigger — it
		// changed no argv, so the restart was a model-load for nothing, and the
		// operator read the unchanged behaviour as "the template didn't take".
		const hwChanged =
			changes.device ||
			changes.ngl ||
			changes.threads ||
			changes.binary ||
			changes.imagePin ||
			changes.profile;
		try {
			// Two-step: defaults (ctx_size lives under [model]) + slot config for the
			// top-level keys (device / NGL / threads / binary / image_pin). These
			// are fast on-disk writes.
			//
			// #1379: chat_template / parallel / server.extra_args are absent by
			// construction — they are not in `changes`, so a slot TOML that still
			// carries them round-trips untouched. That is deliberate: clearing them
			// here would be this drawer destroying config it no longer displays,
			// and the fold into the bound model belongs to `hal0 slot migrate-flags`.
			const slotBody = {};
			if (changes.device) slotBody.device = device;
			if (changes.ngl) slotBody.n_gpu_layers = changes.nglValue;
			if (changes.threads) slotBody.threads = changes.thrValue;
			if (changes.binary) slotBody.binary = binary;
			if (changes.imagePin) slotBody.image_pin = changes.pinValue;
			if (changes.profile) slotBody.profile = profileSel || null;
			const defaultsBody = {};
			if (changes.ctx) defaultsBody.ctx_size = ctxNum;
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
		// Non-blocking apply: a hardware change requires a cold restart that can
		// take model-load seconds-to-minutes. Fire it in the BACKGROUND (do NOT
		// await) and close the drawer immediately.
		if (hwChanged) {
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

	// (#1379: `onRegenerateClick` is gone with the Extra Args field. It persisted
	// the slot override and cleared the stale-command overlay because the
	// baseline then matched the typed value — but the resolved command it
	// claimed to "regenerate" came back byte-identical, since the launch path
	// stopped reading slot extra_args. The overlay vanishing was the only
	// feedback the operator got, and it was a false positive.)

	// `saving` gates the Save button on the fast config writes only — the
	// restart is fired in the background (see onSaveClick) and must not keep the
	// drawer in a blocked "Saving…" state for the whole model-load.
	const saving = editMut.isPending || defaultsMut.isPending;

	// (curModelRow is resolved above the `!slot` guard, so the
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

	// Instant-apply autoload toggle + priority commit (spec 2026-08-02).
	// Same contract as the pinned toggle above: fire the PUT, toast, let the
	// slots poll re-render from server truth. Excluded from the Save batch —
	// see the `dirty` comment below.
	// NPU trio shadows (flm-stt / flm-embed) never run as their own process,
	// so the process-lifecycle controls (Auto-Load, Eviction priority) are
	// hidden for them — the anchor slot owns the FLM process.
	const isNpuShadow = device === "npu" && slot.type !== "llm";
	const autoload = slot.autoload === true;
	const onToggleAutoload = async (next) => {
		try {
			await editMut.mutateAsync({ name: slot.name, body: { autoload: next } });
			window.__hal0Toast &&
				window.__hal0Toast(
					`${slot.name} auto-load ${next ? "on" : "off"}`,
					"ok",
				);
		} catch (err) {
			window.__hal0Toast &&
				window.__hal0Toast(
					err?.message ? `${slot.name}: ${err.message}` : `${slot.name}: toggle failed`,
					"warn",
				);
		}
	};
	const commitPriority = async () => {
		// An emptied/garbage input must never commit as 0 — that's the most
		// aggressive evict-first priority, not "no input yet". Revert to the
		// last known-good value (server truth, or the 50 default) and bail
		// without a PUT rather than guessing.
		const raw = String(prio).trim();
		if (raw === "" || !Number.isFinite(Number(raw))) {
			setPrio(Number.isInteger(slot.priority) ? slot.priority : 50);
			return;
		}
		const v = Math.max(0, Math.min(100, Math.round(Number(raw))));
		setPrio(v);
		if (v === slot.priority) return;
		try {
			await editMut.mutateAsync({ name: slot.name, body: { priority: v } });
			window.__hal0Toast && window.__hal0Toast(`${slot.name} priority → ${v}`, "ok");
		} catch (err) {
			setPrio(Number.isInteger(slot.priority) ? slot.priority : 50);
			window.__hal0Toast &&
				window.__hal0Toast(
					err?.message ? `${slot.name}: ${err.message}` : `${slot.name}: priority save failed`,
					"warn",
				);
		}
	};

	// THE dirty comparison — derived once, against the FROZEN baseline, and read
	// by every consumer (the unsaved-changes guard, the Save body, the stale-
	// command overlay). No predicate is re-derived anywhere else, and none of
	// them touches the live `slot` prop. See configBaseline/deriveChanges above
	// for why (#1390 · #1391 · #1398).
	const changes = deriveChanges(baseline, {
		ctx,
		device,
		nGpuLayers,
		threads,
		binary,
		imagePin,
		profile: profileSel,
	});
	// #1391: the payload lost its config enrichment (or the drawer opened on one
	// that never had it), so there is no baseline to compare against. Refuse to
	// answer "what changed?" rather than guessing — Save is disabled and the
	// operator is told why. Any in-flight edits and the last good baseline are
	// kept, so the drawer recovers intact on the next successful poll.
	const degraded = !configEnriched || !changes;

	// UI-1: unsaved-changes guard. Aggregate ONLY the Save-batched fields — the
	// HW grid and ctx. The instant-apply ``pinned`` toggle fires its own PUT
	// outside Save and is intentionally excluded: a flipped toggle is already
	// persisted. ``autoload``/``priority`` (spec 2026-08-02) are the same
	// shape — their own PUT fires on toggle/blur above and neither is passed
	// into deriveChanges, so they never enter this dirty aggregate. (Reasoning/
	// MTP/Vision were also instant-apply toggles here; they moved to the model
	// drawer — spec-hw-slot-ownership §1. Template / Parallel / Extra Args
	// left with #1379 — see configBaseline.)
	const dirty =
		!!changes &&
		(changes.ctx ||
			changes.ngl ||
			changes.threads ||
			changes.device ||
			changes.binary ||
			changes.imagePin ||
			changes.profile);
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

	// Runtime profile row — the slot's SlotConfig.profile. Controls the runtime
	// family, device-class gating and MTP draft backend; profile FLAGS are
	// copy-on-stamp into the model tune (model drawer), never read at launch.
	// Rendered inside the Model group right under the model select (it rides
	// the model choice), or under its own group on an NPU slot where the Model
	// group is replaced by the NPU capability matrix.
	const profileRow = (() => {
		const all = Array.isArray(profilesQuery.data) ? profilesQuery.data : [];
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
			!!profileSel && profileSel === (curModelRow?.defaults?.profile || "");
		// The options are filtered against the slot's device, which is no
		// longer editable here — a profile persisted on disk can still be
		// out-of-vocab for it. Saving a
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
							Adopted from the bound model's preference — swapping the model
							may change it.
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
	})();

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
				width={SLOT_DRAWER_WIDTH}
				headRight={
					<>
						{/* Lifecycle pair (spec 2026-08-02 consolidation): Auto-Load =
						    boot start only, Pin = residency only. Side by side so they
						    read as one story instead of competing features. NPU trio
						    shadows (flm-stt / flm-embed) have no unit or container of
						    their own — the anchor's FLM process serves them — so
						    Auto-Load is hidden there: writing { autoload } to a shadow
						    TOML configures a boot start that can never happen. */}
						{!isNpuShadow && (
							<label
								className="slot-enable-toggle drawer-enable"
								data-testid="slot-autoload-toggle"
								title="Auto-Load — start this slot automatically at boot. Only controls startup; eviction protection is the Pin toggle."
							>
								<span className="drawer-enable-label mono">Auto-Load</span>
								<input
									type="checkbox"
									checked={autoload}
									onChange={() => onToggleAutoload(!autoload)}
									aria-label={
										autoload
											? "Disable auto-load on start"
											: "Enable auto-load on start"
									}
									aria-describedby={autoloadDescId}
								/>
								<span className="slot-enable-track" aria-hidden="true" />
								<span id={autoloadDescId} className="sr-only">
									Start this slot automatically at boot. Only controls
									startup; eviction protection is the Pin toggle.
								</span>
							</label>
						)}
						<label
							className="slot-enable-toggle drawer-enable"
							data-testid="slot-pin-toggle"
							title={
								pinned
									? "Unpin slot — idle/pressure eviction applies again (order set by Eviction priority under Advanced)"
									: "Pin slot — once loaded it stays resident: exempt from idle/pressure eviction, and unload/delete require ?force=true. Pinning never starts a slot — boot start is the Auto-Load toggle."
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
								aria-describedby={pinDescId}
							/>
							<span className="slot-enable-track" aria-hidden="true" />
							<span id={pinDescId} className="sr-only">
								A pinned slot stays resident once loaded: exempt from idle
								and pressure eviction, and unload or delete require force.
								Pinning never starts a slot — boot start is Auto-Load.
							</span>
						</label>
					</>
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
							{degraded && (
								<span
									data-testid="slot-drawer-degraded"
									title="The /api/slots poll that carries this slot's saved configuration didn't land. Saving now could overwrite settings that aren't currently visible."
									style={{ color: "var(--warn)", fontSize: 11 }}
								>
									Slot data degraded — reconnecting…
								</span>
							)}
							<button className="btn ghost sm" onClick={requestClose}>
								Cancel
							</button>
							<button
								className="btn sm"
								disabled={saving || degraded}
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
						v={
							<span
								data-testid="slot-state-readonly"
								className={stateChipClass(slot)}
							>
								{slot.state}
							</span>
						}
					/>
				</div>

				{/* Runner + type + image status strip — read-only. Runner (BINARY)
          resolves the launch image (RUNNER_IMAGES[binary]); image_pin overrides
          it (§3). `type` sits between the two: it is fixed at creation (it rides
          the model, exactly like device), so it belongs in the read-only strip
          rather than as a disabled form control down in the Slot group.
          image status keyed to slot_id so the operator knows which slot owns it. */}
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
						k="runner · binary"
						v={slot.binary || `auto · ${deviceBackend(device) || device}`}
					/>
					<ReadOnlyStrip
						k="type"
						v={
							<span
								data-testid="slot-type-readonly"
								title="Type is fixed once created — make a new slot for a different kind."
							>
								{slot.type || "—"}
							</span>
						}
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
					{/* Type moved to the read-only strip above — it is fixed at
					    creation, so a disabled select here was a control that could
					    never do anything. */}
				</FieldGroup>


				{/* Hardware ownership — changes apply on Save and require a restart. */}
				<FieldGroup label="Hardware">
					{(() => {
						const backends = systemInfoQuery.data?.backends ?? {};
						const binaryKeys = Object.keys(backends);
						const devBackend = deviceBackend(device);
						// Runner options filtered down to the ones that fit this slot:
						// device_class exact (runner_matches), the family's slot types
						// (_supported_slot_types — so a gpu llm slot is not offered
						// qwen3tts/comfyui) and the SAME supported_backends list the
						// fit-check below warns on. An out-of-vocab persisted value stays
						// selectable underneath.
						const deviceFitBinaryKeys = binaryKeys.filter((k) => {
							const r = backends[k] || {};
							if (r.device_class && r.device_class !== deviceClass)
								return false;
							const types = FAMILY_SLOT_TYPES[r.runtime_family];
							if (types && slot.type && !types.includes(slot.type))
								return false;
							const sup = runnerBackends(r);
							return !(sup.length > 0 && devBackend && !sup.includes(devBackend));
						});
						// The Runner Binary dropdown offers the binaries BUILT INTO the
						// currently-named Runner Image — one image can ship several
						// (e.g. the shared rocmfpx image serves both `rocmfpx · rocm`
						// and `vulkanfpx · vulkan`). With no image named yet there is
						// nothing to match against, so fall back to the device fit set.
						const pinnedImage = (imagePin || "").trim();
						// Runner Image catalog — the distinct image refs RUNNER_IMAGES
						// resolves to (same system-info source the Runtimes page renders).
						// One image can ship several binaries (rocmfpx/vulkanfpx share
						// one Vulkan-portable image); the map records which, so picking
						// an image repopulates the Runner Binary dropdown below.
						const imageKeysByRef = new Map();
						for (const k of binaryKeys) {
							const img = backends[k]?.image;
							if (!img) continue;
							if (!imageKeysByRef.has(img)) imageKeysByRef.set(img, []);
							imageKeysByRef.get(img).push(k);
						}
						const catalogImages = [...imageKeysByRef.keys()];
						const imageFitKeys = pinnedImage
							? binaryKeys.filter((k) => backends[k]?.image === pinnedImage)
							: [];
						const fitBinaryKeys = pinnedImage
							? imageFitKeys
							: deviceFitBinaryKeys;
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
								{/* Device has no control here on purpose: it rides the model
								    at creation (see the create modal's device-redirect note);
								    the per-slot Profile select above picks this slot's
								    runtime (runner family + image), not its flag tune. The
								    drawer still READS slot.device for the fit filters below. */}
								<div className="form-row">
									<div className="form-lbl">
										<span>Runner Image</span>
										<FieldInfoIcon description="Pin the container image the slot launches. The list is the
											runner-image catalog (the same registry the Runtimes page
											shows); ‘Custom image ref…’ keeps the free-text escape
											hatch for a debug build, A/B test, or rollback. Empty
											uses the release default resolved from Runner Binary." />
									</div>
									<div className="form-ctl">
										{pinCustom ? (
											<div
												style={{ display: "flex", gap: 8, alignItems: "center" }}
											>
												<input
													className={
														"input mono" +
														(fieldErrs.imagePin ? " input-err" : "")
													}
													data-testid="slot-hw-image-pin"
													value={imagePin}
													onChange={(e) => {
														setImagePin(e.target.value);
														setFieldErrs((p) => ({
															...p,
															imagePin: undefined,
														}));
													}}
													placeholder={
														binary && backends[binary]?.image
															? backends[binary].image
															: "will resolve from runner (binary)"
													}
													spellCheck={false}
													style={{ flex: 1 }}
													autoFocus
												/>
												<button
													className="btn ghost sm"
													data-testid="slot-hw-image-pin-catalog"
													title="Back to the runner-image catalog"
													onClick={() => setPinCustom(false)}
												>
													Catalog
												</button>
											</div>
										) : (
											<select
												className={
													"input mono" +
													(fieldErrs.imagePin ? " input-err" : "")
												}
												data-testid="slot-hw-image-pin"
												value={imagePin}
												onChange={(e) => {
													const v = e.target.value;
													if (v === "__custom__") {
														setPinCustom(true);
														return;
													}
													setImagePin(v);
													setFieldErrs((p) => ({ ...p, imagePin: undefined }));
													// Keep BINARY coherent with the picked image: if the
													// current binary doesn't ship in it, hop to the
													// image's sole binary, or clear so the operator picks
													// from the repopulated list.
													const keys = imageKeysByRef.get(v) || [];
													if (v && binary && !keys.includes(binary)) {
														setBinary(keys.length === 1 ? keys[0] : "");
													}
												}}
												style={imagePin ? {} : { color: "var(--fg-4)" }}
											>
												<option value="">
													— default · resolved from Runner Binary —
												</option>
												{/* A persisted pin outside the catalog (older release,
												    hand-edited TOML) keeps its own option so opening the
												    drawer never silently rewrites it. */}
												{pinnedImage && !catalogImages.includes(pinnedImage) && (
													<option value={pinnedImage}>
														{pinnedImage} · custom
													</option>
												)}
												{catalogImages.map((img) => (
													<option key={img} value={img} title={img}>
														{(imageKeysByRef.get(img) || []).join(" / ")} · {img}
													</option>
												))}
												<option value="__custom__">Custom image ref…</option>
											</select>
										)}
										{fieldErrs.imagePin ? (
											<div className="hint" style={{ color: "var(--err)" }}>
												{fieldErrs.imagePin}
											</div>
										) : null}
									</div>
								</div>

								<div className="form-row">
									<div className="form-lbl">
										<span>Runner Binary</span>
										<FieldInfoIcon description="⟳ Which binary inside the Runner Image executes (a
											RUNNER_IMAGES key, not a profile). One image can ship
											several — the list shows exactly what the named Runner
											Image provides." />
									</div>
									<div className="form-ctl">
										<select
											className={"input mono" + (fitWarn ? " input-err" : "")}
											data-testid="slot-hw-binary"
											value={binary}
											onChange={(e) => setBinary(e.target.value)}
										>
											{/* No "— default (from device) —" entry: the dropdown lists
											    the binaries actually built into the Runner Image, and
											    nothing else. An out-of-vocab persisted value keeps its
											    own option so the drawer never silently rewrites it. */}
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
										{!binary && fitBinaryKeys.length > 0 && (
											<div className="hint">
												No binary pinned — the launcher resolves one from the
												device. Pick one to fix it.
											</div>
										)}
										{pinnedImage && fitBinaryKeys.length === 0 && (
											<div className="hint" data-testid="slot-hw-binary-none">
												No known runner binary ships in{" "}
												<span className="mono">{pinnedImage}</span>. Clear the
												Runner Image to see the binaries that fit this device.
											</div>
										)}
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

				{/* NPU slots have no Model group (the capability matrix replaces it),
				    so the profile keeps its own group there. Everywhere else the row
				    lives inside Model, under the model select. */}
				{device === "npu" && (
					<FieldGroup label="Profile">{profileRow}</FieldGroup>
				)}

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
											{/* Inline model edit — the app-wide pencil affordance
											    (Icons.edit), same bare `btn ghost sm` icon-button
											    convention as the slot card. Opens the model drawer
											    DOCKED to the left of this one, so the slot's unsaved
											    edits stay visible and editable underneath. */}
											<button
												className="btn ghost sm"
												data-testid="slot-model-edit-open"
												disabled={!curModelRow}
												title="Edit the bound model's tune (flags, template, caps) in place"
												aria-label={`Edit model ${curModelRow?.longName || curModelRow?.name || ""}`.trim()}
												onClick={(e) => {
													e.stopPropagation();
													setModelEditOpen(true);
												}}
											>
												{Icons.edit}
											</button>
										</div>
										{swapping && <div className="hint">Swapping…</div>}
									</div>
								</div>
							);
						})()}

						{profileRow}

						<div className="form-row">
							<div className="form-lbl">
								<span>Context (ceiling)</span>
								<FieldInfoIcon description="⟳ ctx_size — a hardware CEILING in tokens, not an
									override: the bound model's own default context_size (set on the
									model drawer) is authoritative, and this only clamps it down for
									hardware that can't fit the model's full window. Slot-owned so the
									same model can run capped on lighter hardware elsewhere. PATCHes
									/defaults; takes effect on next request. (~model-load seconds)" />
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

						{/* #1379: Template / Parallel / Extra Args were removed here.
          All three persisted to the slot TOML and reached nothing —
          `providers/container.py` does `del profile_flags, slot_parallel,
          extra_args`, and `resolve_chat_template` no longer consults the
          per-slot key. spec-flags-ownership §1/§4 puts the launch tune on the
          MODEL; spec-hw-slot-ownership §8 prescribes this editor as the HW grid
          + image_pin only. Removed outright rather than left read-only, the
          same call made for Reasoning/MTP/Vision under §1. Anything already on
          disk is folded into the bound model by `hal0 slot migrate-flags`
          (#1396/#1397) — this drawer neither reads nor clears it. */}
						<div className="hint" data-testid="slot-launch-tune-note">
							Chat template and launch flags belong to the model — edit them
							with “Edit model…” above. Flags set on a slot are not applied
							at launch.
						</div>
					</FieldGroup>
				)}
				{/* NPU trio SHADOW (flm-stt / flm-embed): its own [npu] table is
				    inert — the anchor's FLM process owns all three modalities, so
				    editing toggles here wrote config the launcher never reads and
				    nothing outside the drawer reflected it. Point at the anchor
				    instead of rendering write-only controls. */}
				{device === "npu" && slot && slot.type !== "llm" && (
					<div className="form-row">
						<div className="form-lbl">
							<span>NPU modalities</span>
						</div>
						<div className="form-ctl">
							<div className="hint">
								{(() => {
									// Resolved STRUCTURALLY (#1662, mirrors #1637's npu-pane.jsx
									// fix) — never by stripping a `-stt`/`-embed` suffix off THIS
									// slot's own name. `reconcile_trio_slots` names new shadows
									// after the anchor but never renames pre-existing ones, so a
									// renamed anchor ('flm' → 'npu') leaves old shadows pointing
									// at a name-derived '#slots/flm' that no longer exists. Falls
									// back to the old heuristic only if the slot list hasn't
									// loaded yet.
									const anchorSlot = npuAnchorSlot(slotsQuery.data);
									const anchor =
										anchorSlot?.name || slot.name.replace(/-(stt|embed)$/, "") || "flm";
									return (
										<>
											This capability is served by the anchor FLM slot — STT and
											Embed are toggled there.{" "}
											<a
												href={"#slots/" + anchor}
												onClick={(e) => {
													e.preventDefault();
													const target = "#slots/" + anchor;
													// Route through the dirty guard — a direct hash
													// assignment would re-seed the drawer for the anchor
													// and silently drop pending Save-batched edits.
													if (dirty) {
														setPendingNav(target);
														setDiscardOpen(true);
													} else {
														window.location.hash = target;
													}
												}}
											>
												Open {anchor}
											</a>
										</>
									);
								})()}
							</div>
						</div>
					</div>
				)}
				{/* NPU capability matrix — replaces Model+Template for NPU slots.
				    Anchor only (type=llm): the trio shadows have no config of
				    their own (see the block above). */}
				{device === "npu" &&
					slot?.type === "llm" &&
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

					{/* Eviction priority (spec 2026-08-02) lives under Advanced: its
					    lifecycle siblings (Auto-Load / Pin) are header toggles, and it
					    only matters for unpinned slots under memory pressure. Hidden
					    for NPU trio shadows — no process of their own to evict. */}
					{!isNpuShadow && (
					<div className="form-row">
						<div className="form-lbl">
							<span>Eviction priority</span>
							<FieldInfoIcon description="0-100 — lower unloads first when memory is needed.
								Ties go to the least recently used. Pin the slot to exempt
								it entirely." />
						</div>
						<div className="form-ctl">
							<input
								className="input mono"
								data-testid="slot-priority-input"
								type="number"
								min={0}
								max={100}
								step={1}
								value={prio}
								onChange={(e) => setPrio(e.target.value)}
								onBlur={commitPriority}
								onKeyDown={(e) => {
									if (e.key === "Enter") e.currentTarget.blur();
								}}
								style={{ width: 90 }}
							/>
							<div className="hint">lower unloads first</div>
						</div>
					</div>
					)}

					{/* Flags preview — backend-provided resolved_command (real podman argv),
          computed SERVER-SIDE (profile + MTP + image resolution). #1379 removed
          the dim + Regenerate overlay that used to sit on top: it promised to
          "fold your slot extra_args into the resolved command", but the launch
          path stopped reading slot extra_args, so the regenerated command came
          back byte-identical. The preview itself is honest and stays.
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
	        same instance contract as models.jsx). DrawerDock reaches the
	        <Drawer> nested inside ModelDrawer through context, docking it
	        SLOT_DRAWER_WIDTH px inboard so it lands flush against this drawer's
	        left edge: two panels side by side, both fully visible and
	        interactive, instead of the model drawer covering this one at equal
	        z-index. `backdrop="clear"` keeps exactly one dim scrim on the page
	        (this drawer's) while leaving click-outside dismissing the TOP layer.
	        Below 1200px there is no room for both, and the CSS falls back to the
	        overlay stack (see .drawer--docked in dashboard.css). Its own save
	        path closes it and returns here with every slot edit intact. */}
			<DrawerDock rightOf={SLOT_DRAWER_WIDTH} backdrop="clear">
				<ModelDrawer
					open={modelEditOpen && !!curModelRow}
					onClose={() => setModelEditOpen(false)}
					model={curModelRow}
				/>
			</DrawerDock>
			<ConfirmDialog
				open={discardOpen}
				onCancel={() => {
					setDiscardOpen(false);
					setPendingNav(null);
				}}
				onConfirm={() => {
					setDiscardOpen(false);
					if (pendingNav) {
						// Discard confirmed for an in-drawer navigation (shadow ->
						// anchor): switch slots instead of closing.
						const target = pendingNav;
						setPendingNav(null);
						window.location.hash = target;
						return;
					}
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
