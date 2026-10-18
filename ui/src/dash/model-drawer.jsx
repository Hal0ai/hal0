// hal0 dashboard — Model editor drawer (ADR-0029, profile-centric config).
//
// The model is a FILE: this drawer edits only facts about it. Every tune
// (flags, context window, thinking, vision, MTP on/off) lives on a profile,
// and placement lives on the slot.
//
// What the drawer owns:
//   · identity (name, source coords, mmproj sidecar path, engine/provider);
//   · file facts, top-level on the model — chat_template, jinja, and "this
//     file has MTP heads" (mtp), plus the native context window shown
//     read-only.
//
// Save writes through useModelUpdate (PUT /api/models/{id}) and sends ONLY the
// facts that changed, as top-level keys (null clears one). A `defaults` key is
// refused server-side (422 model.defaults_removed), so it never ships.

import { copyTextToClipboard } from "@/lib/clipboard";
import {
	useModelUpdate,
	useModelSetDefault,
	useModelInspect,
} from "@/api/hooks/useModels";
import { useChatTemplates } from "@/api/hooks/useChatTemplates";
import { useMetaEnums } from "@/api/hooks/useMeta";
import { useSlots } from "@/api/hooks/useSlots";
import { slotsUsingModel, footSummary } from "@/dash/model-usage.js";
import { RichSelect } from "@/dash/rich-select.jsx";
import { canonicalCapabilities, deviceById } from "@/lib/deviceMeta";
import {
	CAP_DEFS,
	overriddenCaps,
	remainingCaps,
	overrideSummary,
} from "@/dash/cap-overrides";

const {
	useState: useStateMD,
	useEffect: useEffectMD,
	useMemo: useMemoMD,
	useRef: useRefMD,
} = React;

// spec-hw-slot-ownership §1/§8: the model is device-agnostic — it carries no
// device, runner, or image. The former deviceFlavour() chip (and the read-only
// Runner section) were removed; device lives on the slot's HW grid now.

// Modality read-out derived from the model's capabilities/type (typed field —
// capabilities themselves are read-only in the drawer since #2193; they come
// from the registry row, not from a control here).
function modalityLabel(caps, type) {
	const c = new Set((caps || []).map((x) => String(x).toLowerCase()));
	if (c.has("vision")) return "vision";
	if (c.has("embed")) return "embed";
	if (c.has("rerank")) return "rerank";
	if (c.has("asr")) return "audio";
	if (c.has("tts")) return "audio";
	if (c.has("image")) return "image";
	return type ? String(type) : "text";
}

// Native-context K-format ("128K") for the read-only Native context row.
function contextLengthLabel(v) {
	if (!v) return null;
	return v >= 1024 ? `${Math.round(v / 1024)}K` : String(v);
}

// tri-state: absent (auto) | true (on) | false (off) — for the mtp / jinja file facts.
function triFromDefault(v) {
	if (v === true) return "on";
	if (v === false) return "off";
	return "auto";
}

// ─── CapOverrideAdd — "+ Override…" menu (overrides ledger, panel 09 V1) ─────
// Auto is invisible; this is the only place an override gets created, and the
// only place its consequence copy renders (decision time, not permanently).
// Same click-outside/Escape idiom as chrome.jsx's NotificationBell popover.
function CapOverrideAdd({ remaining, onPick }) {
	const [open, setOpen] = useStateMD(false);
	const wrapRef = useRefMD(null);
	useEffectMD(() => {
		if (!open) return;
		const onDown = (e) => {
			if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false);
		};
		const onKey = (e) => {
			if (e.key === "Escape") setOpen(false);
		};
		document.addEventListener("mousedown", onDown);
		document.addEventListener("keydown", onKey);
		return () => {
			document.removeEventListener("mousedown", onDown);
			document.removeEventListener("keydown", onKey);
		};
	}, [open]);
	if (!remaining.length) return null; // every cap already overridden
	return (
		<div ref={wrapRef} style={{ position: "relative", display: "inline-block" }}>
			<button
				type="button"
				className="btn ghost sm"
				data-testid="model-cap-override-add"
				aria-expanded={open}
				aria-controls="model-cap-override-popover"
				onClick={() => setOpen((o) => !o)}
			>
				+ Override…
			</button>
			{open && (
				<>
					<div className="mdl-row-menu-backdrop" onClick={() => setOpen(false)} />
					{/* Plain labelled popover, not a menu (#2198 review): the children
					    are toggle buttons with per-cap on/off state, not a linear list
					    of commands, and there is no arrow-key traversal to back a
					    role="menu"/menuitem contract — a dishonest "menu" announcement
					    with no keyboard support is worse than an honest group. */}
					<div
						id="model-cap-override-popover"
						role="group"
						aria-label="Add a capability override"
						style={{
							position: "absolute",
							top: "calc(100% + 4px)",
							left: 0,
							zIndex: 60,
							minWidth: 320,
							background: "var(--bg-2)",
							border: "1px solid var(--line-strong)",
							borderRadius: 8,
							boxShadow: "0 16px 48px -8px rgba(0,0,0,.65)",
							overflow: "hidden",
						}}
					>
						{remaining.map((def, i) => (
							<div
								key={def.id}
								style={{
									padding: "9px 12px",
									borderBottom:
										i < remaining.length - 1 ? "1px solid var(--line)" : "none",
								}}
							>
								<div
									style={{
										display: "flex",
										justifyContent: "space-between",
										alignItems: "center",
										gap: 10,
									}}
								>
									<span className="mono" style={{ fontSize: 12.5 }}>
										{def.label}
									</span>
									<span style={{ display: "flex", gap: 6 }}>
										<button
											type="button"
											className="mdl-chip"
											data-testid={`model-cap-override-add-${def.id}-on`}
											aria-label={`${def.label} on`}
											onClick={() => {
												onPick(def.id, true);
												setOpen(false);
											}}
										>
											on
										</button>
										<button
											type="button"
											className="mdl-chip"
											data-testid={`model-cap-override-add-${def.id}-off`}
											aria-label={`${def.label} off`}
											onClick={() => {
												onPick(def.id, false);
												setOpen(false);
											}}
										>
											off
										</button>
									</span>
								</div>
								<div
									className="mono"
									style={{ fontSize: 10.5, color: "var(--fg-4)", marginTop: 4 }}
								>
									{def.consequence}
								</div>
							</div>
						))}
					</div>
				</>
			)}
		</div>
	);
}

// ─── CapOverridesLedger — resting-state chips + the add menu (panel 09 V1) ──
// Auto (null) is invisible: only overridden caps render, one chip each, ✕
// returns that cap to Auto via the existing save-gated path.
function CapOverridesLedger({ flags, onSet, onClear }) {
	const overridden = overriddenCaps(flags);
	const remaining = remainingCaps(flags);
	return (
		<div>
			<div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
				{overridden.map(({ id, value }) => {
					const def = CAP_DEFS.find((d) => d.id === id);
					return (
						<span
							key={id}
							className="mdl-chip on"
							data-testid={`model-cap-override-${id}`}
							style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
						>
							{def ? def.label : id} · {value ? "on" : "off"}
							<button
								type="button"
								aria-label={`Remove ${def ? def.label : id} override`}
								onClick={() => onClear(id)}
								style={{
									background: "transparent",
									border: "none",
									color: "inherit",
									font: "inherit",
									cursor: "pointer",
									padding: 0,
									lineHeight: 1,
								}}
							>
								✕
							</button>
						</span>
					);
				})}
				<CapOverrideAdd remaining={remaining} onPick={onSet} />
			</div>
			{overridden.length > 0 && (
				<div style={{ marginTop: 6, display: "flex", flexDirection: "column", gap: 2 }}>
					{overridden.map(({ id, value }) => (
						<div
							key={id}
							className="hint"
							data-testid={`model-cap-override-summary-${id}`}
						>
							{overrideSummary(id, value)}
						</div>
					))}
				</div>
			)}
		</div>
	);
}

// ─── Drawer dirty-tracking seam (#1398) ──────────────────────────────────────
//
// Same seam the slot drawer got in #1447, for the same reason. This drawer
// seeds its form ONCE (effect keyed on `[open, model?.id]`) but answered "did
// this field change?" against the LIVE `model` prop. Both callers hand it a
// live-polled value — models.jsx does `modelList.find(m => m.id === selId)` off
// `modelsQuery.data` (`useModels`: 30s poll, plus an invalidation on every
// model mutation), and the slot drawer's stacked editor does
// `(modelsQuery.data ?? []).find(m => m.id === curModelId)`. The old comment on
// `defaultOverride` claiming this prop is "a SNAPSHOT captured when the drawer
// opened" was true of neither.
//
// So the two sides drifted with nothing the operator did, and the drawer read
// that drift as an edit:
//   * a concurrent write (another operator, a slot-drawer save, the CLI) moved
//     `chat_template`; the untouched field went dirty and Save wrote
//     the drawer's stale seed back over it — a lost update caused by doing
//     nothing;
//   * the save body was built from the live row, so a mid-edit poll changed
//     what it carried rather than what the operator saw.
//
// Snapshot the model ONCE at open, freeze it, and derive both the dirty
// aggregate and the save body from it. `caps` is stored already canonicalised
// so a late `/api/meta/enums` load — which changes `canonicalCapabilities`'
// output from the fallback table to the real one — cannot fabricate a diff
// against a form seeded with the earlier answer.
function modelBaseline(model, enums) {
	if (!model) return null;
	const init = model;
	return {
		id: model.id,
		name: model.name || "",
		caps: canonicalCapabilities(model.capabilities, enums),
		// Task 3 (PR-1): `backends` is retired as an editable/authoritative
		// surface — the server drops a client-sent `backends` key silently
		// (models.py:update_model). `provider` is the write path now (engine
		// identity — see the Runner compatibility section below); `runs_on`
		// (derived, read-only) is served alongside it on every row.
		provider: model.provider || "",
		mmproj: model.mmproj || "",
		hfRepo: model.hf_repo || "",
		hfFilename: model.hf_filename || "",
		chatTemplate: init.chat_template ?? "auto",
		mtp: triFromDefault(init.mtp),
		jinja: triFromDefault(init.jinja),
	};
}

// One derived comparison, two consumers — the unsaved-changes guard and the
// save body both read this, so a predicate can never exist in two places and
// drift. Returns null without a baseline, in which case the drawer refuses to
// call anything dirty.
function deriveModelChanges(baseline, form) {
	if (!baseline) return null;
	const trimmedName = form.name.trim();
	const c = {
		// Diff on the value alone (#1381): a truthiness guard here once collapsed
		// "unchanged" and "deliberately emptied" into the same skip branch, so the
		// name could never be cleared.
		name: trimmedName !== baseline.name,
		trimmedName,
		provider: form.provider !== baseline.provider,
		mmproj: form.mmproj.trim() !== baseline.mmproj,
		trimmedMmproj: form.mmproj.trim(),
		hfRepo: form.hfRepo.trim() !== baseline.hfRepo,
		trimmedRepo: form.hfRepo.trim(),
		hfFilename: form.hfFilename.trim() !== baseline.hfFilename,
		trimmedFile: form.hfFilename.trim(),
		chatTemplate: form.chatTemplate !== baseline.chatTemplate,
		mtp: form.mtp !== baseline.mtp,
		jinja: form.jinja !== baseline.jinja,
	};
	c.any =
		c.name ||
		c.provider ||
		c.mmproj ||
		c.hfRepo ||
		c.hfFilename ||
		c.chatTemplate ||
		c.mtp ||
		c.jinja;
	return c;
}

// Build the chat-template RichSelect's options from useChatTemplates rows
// (`{id, label, valid, error}` — chat_templates.py's `_entry`). `auto` is
// hand-rolled first with its own blurb (the catalog's own "Auto (GGUF
// embedded)" label rides the chip instead of the row name, matching the
// mockup); every other row chips a lint failure inline — `valid: false`
// carries the render-check's error string — so a broken template dropped
// into the store dir reads as a warning here, not only as a slot crash.
function chatTemplateRichOptions(templates) {
	const rows = Array.isArray(templates) ? templates : [];
	const opts = [
		{
			id: "auto",
			row: "Auto",
			right: <span className="chip ok">GGUF embedded</span>,
			desc: "use the template the model file ships — right for ~every GGUF",
		},
	];
	for (const t of rows) {
		if (t.id === "auto") continue;
		opts.push({
			id: t.id,
			row: t.label,
			right: t.valid === false ? <span className="chip warn">⚠ broken</span> : null,
			desc: t.valid === false ? t.error : null,
		});
	}
	return opts;
}

// Engine identity (Runner compatibility · Task 6). UI copy ONLY — the family
// VOCABULARY always comes from enums.runtime_families (see engineRichOptions
// below); this map never grows a family enums doesn't already list, and a
// family enums adds before this map catches up still renders a plain row
// with no description rather than being dropped.
const ENGINE_BLURBS = {
	"llama-server": "GGUF chat · vision · embeddings · the default engine",
	flm: "FastLLM · NPU-lane models",
	kokoro: "TTS voices",
	qwen3tts: "TTS voices · Qwen",
	moonshine: "streaming ASR",
	comfyui: "image gen graphs — rows owned by the ComfyUI catalog",
};

function mutedChip(text) {
	return <span className="chip">{text}</span>;
}

// Build the engine RichSelect's options. `providerEffective` is the row's
// server-computed provider_effective (model_to_dict, Task 6 of the drawer
// overhaul) — the Auto row's closed-control preview and blurb. Mock fixtures
// predating that field won't carry it, so "llama-server" (the resolver's own
// total default — hal0.model_meta.derive_model_provider never returns
// anything else for an empty/unrecognised tag set) is the fallback, never a
// blank chip.
function engineRichOptions(runtimeFamilies, providerEffective) {
	const families = Array.isArray(runtimeFamilies) ? runtimeFamilies : [];
	const resolved = providerEffective || "llama-server";
	return [
		{
			id: "",
			row: "Auto",
			right: mutedChip("→ " + resolved),
			desc: "derived from the stored backend tags",
		},
		...families.map((rf) => ({
			id: rf,
			row: rf,
			desc: ENGINE_BLURBS[rf],
		})),
	];
}

// ── MMProj: the shape the registry actually stores (Task 9) ──────────────────
//
// `Model.mmproj` is an ABSOLUTE HOST PATH, never a bare filename. Three
// independent places in the source say so:
//
//   · the field itself — "Absolute path to a multimodal projector (mmproj)
//     GGUF sidecar … surfaced verbatim to the llama-server provider as
//     --mmproj" (src/hal0/registry/model.py:249-256);
//   · the write screen — `screen_vision_mmproj` rejects a projector that is
//     not `Path(projector).is_file()` on this host, and its docstring names
//     every registry-bound value "a caller-supplied host path"
//     (src/hal0/services/models_service.py:1440-1475);
//   · the pull worker — the `mmproj_filename` the Add-from-HF modal sends is
//     an HF-side FILENAME and is resolved before it reaches the registry:
//     `mmproj_final = final.parent / Path(mm_rec.hf_filename).name`
//     (src/hal0/registry/pull.py:1071), persisted as `str(mmproj_final)`
//     (pull.py:1095); the fileset branch does the same with the installed
//     destination (`updates["mmproj"] = str(mmproj_dest)`, pull.py:1588), as
//     does the discover scan (`model.mmproj = str(candidate.mmproj)`,
//     src/hal0/registry/discover.py:404).
//
// So the picker lists repo files but must write `<dir>/<basename>`, with
// `<dir>` the directory the sidecar sits in beside the model — exactly what
// the pull worker would have produced. `mmprojDirOf` prefers the stored
// projector's own directory (a row whose sidecar lives elsewhere keeps it) and
// falls back to the model file's directory. No derivable directory ⇒ no
// picker: this drawer resolves a path, it never invents one.
//
// BASENAME, not the variant id: an inspect variant's `id` is the repo-RELATIVE
// PATH (`"id": rel` from the recursive tree walk,
// src/hal0/upstreams/huggingface.py:357), and repos really do nest projectors
// and quants in subdirectories — the same file's own comment cites
// `vision/Foo.mmproj` (huggingface.py:270-274), and `recursive=true` exists for
// exactly that (registry/update_check.py:39-40, registry/fileset.py:208-210).
// The pull worker flattens that away (`Path(mm_rec.hf_filename).name`,
// pull.py:1071), so joining a variant id verbatim would write
// `<dir>/UD-Q4_K_XL/mmproj-F16.gguf` — a path no pull ever produces, and one
// that fails `screen_vision_mmproj`'s on-disk check on save.
function mmprojDirOf(storedMmproj, modelPath) {
	const src = String(storedMmproj || modelPath || "").trim();
	const cut = src.lastIndexOf("/");
	if (cut < 0) return null;
	return cut === 0 ? "/" : src.slice(0, cut);
}

function joinMmprojPath(dir, filename) {
	return dir === "/" ? `/${filename}` : `${dir}/${filename}`;
}

function basenameOf(p) {
	const s = String(p || "");
	const cut = s.lastIndexOf("/");
	return cut < 0 ? s : s.slice(cut + 1);
}

function sizeChipText(variant) {
	if (variant && variant.size) return String(variant.size);
	const b = Number(variant && variant.size_bytes);
	return b > 0 ? `${(b / 1024 ** 3).toFixed(2)} GB` : "";
}

// Build the MMProj RichSelect's options. Option ids are the VALUE the picker
// stores (the resolved absolute path), so `onChange` is a plain `setMmproj`
// and the control's `value` compares against the stored string directly —
// there is no second place where a filename could be re-derived into a
// different path than the one that was saved. The ROW keeps the variant's full
// repo-relative id, so two projectors that flatten onto the same filename are
// still told apart by eye; `seen` keeps the first (smallest, the list arrives
// size-sorted), which is the same one-file-per-directory outcome a pull of both
// would leave behind.
//
// `extras` are values the row already carries — the stored projector and the
// current draft — that this repo's listing doesn't offer (a hand-placed
// sidecar, or a repo that has since dropped the file). Each gets its own row,
// verbatim. A value this picker cannot explain is still a value the operator
// chose: it is never silently rewritten, and never disappears out from under a
// pick that could otherwise not be undone without closing the drawer.
function mmprojRichOptions(dir, variants, extras) {
	const opts = [
		{
			id: "",
			row: "— none —",
			desc: "no projector paired — a row advertising vision cannot save without one",
		},
	];
	const seen = new Set([""]);
	for (const v of variants) {
		const id = joinMmprojPath(dir, basenameOf(v.id));
		if (seen.has(id)) continue;
		seen.add(id);
		const chip = sizeChipText(v);
		opts.push({ id, row: v.id, right: chip ? mutedChip(chip) : null });
	}
	for (const extra of extras) {
		const cur = String(extra || "").trim();
		if (!cur || seen.has(cur)) continue;
		seen.add(cur);
		opts.push({
			id: cur,
			row: basenameOf(cur),
			right: mutedChip("stored"),
			desc: "paired on this model · not among this repo's files",
		});
	}
	return opts;
}

// One read-only path line for the Source disclosure's paths block: mono text
// that wraps mid-token (#2210's overflow-wrap:anywhere pattern — a projector
// path is long, unbreakable, and must not push the drawer sideways) plus the
// house copy affordance. The copy goes through the sturdier of the two house
// idioms (command-palette.jsx's `cpCopy`, :35-42): a clipboard write can throw
// (no permission, insecure origin, no API at all), and reporting "copied" for
// a write that never happened is worse than saying nothing.
//
// `note` is for a line whose value is NOT on-disk truth — the projector line
// shows the unsaved pick, and says so rather than implying the file is there.
function SourcePathLine({ label, value, testId, empty, note }) {
	const has = !!String(value || "").trim();
	return (
		<div style={{ marginTop: 4 }}>
			<div style={{ display: "flex", gap: 8, alignItems: "baseline" }}>
				<span className="hint" style={{ minWidth: 52, flex: "0 0 auto" }}>
					{label}
				</span>
				<span
					className="mono"
					style={{ flex: 1, minWidth: 0, overflowWrap: "anywhere", fontSize: 12 }}
				>
					{has ? value : <span className="hint">{empty}</span>}
				</span>
				{has && (
					<button
						type="button"
						className="btn ghost sm"
						data-testid={testId}
						onClick={async () => {
							const ok = await copyTextToClipboard(String(value));
							if (ok)
								window.__hal0Toast &&
									window.__hal0Toast(`${label} path copied to clipboard`, "ok");
							else
								window.__hal0Toast &&
									window.__hal0Toast("Copy failed — clipboard unavailable", "err");
						}}
					>
						Copy
					</button>
				)}
			</div>
			{note && (
				<div className="hint" style={{ marginLeft: 60, marginTop: 2 }}>
					{note}
				</div>
			)}
		</div>
	);
}

// ─── ModelDrawer ─────────────────────────────────────────────────────────────
// `onOpenSlot` is optional (Task 3, facts-band used-by cell): when a host
// passes it, each slot name in the used-by list is a jump button; absent, the
// names render as plain text. jump wiring: models.jsx passes onOpenSlot — NOT
// wired yet (the standalone Models page has no slot-drawer opener nearby to
// reach trivially); tracked as a follow-up rather than faked here.
export function ModelDrawer({ open, onClose, model, onOpenSlot = undefined }) {
	const update = useModelUpdate();
	const setDefault = useModelSetDefault();
	const templates = useChatTemplates(open);
	const enums = useMetaEnums();
	const slotsQuery = useSlots();
	// Repo listing behind the Source disclosure's mmproj picker (Task 9) —
	// POST /api/models/inspect, server-cached, fired lazily (see srcOpen below).
	const inspect = useModelInspect();

	// Identity + typed fields (preserve the full RecipeEditor save surface).
	const [name, setName] = useStateMD("");
	// Engine identity (Runner compatibility section) — the write path now;
	// `backends` died with it (Task 3 of PR-1, models.py:update_model drops a
	// client-sent `backends` silently). Empty string = derive server-side.
	const [provider, setProvider] = useStateMD("");
	const [mmproj, setMmproj] = useStateMD("");
	const [hfRepo, setHfRepo] = useStateMD("");
	const [hfFilename, setHfFilename] = useStateMD("");
	// File facts (ADR-0029): chat template, jinja and "the file has MTP heads".
	const [chatTemplate, setChatTemplate] = useStateMD("auto");
	const [mtp, setMtp] = useStateMD("auto");
	const [jinja, setJinja] = useStateMD("auto");
	// The Source section is a disclosure ("▸ Source — repo · file · mmproj ·
	// paths"), closed at rest. Re-pull coords and the projector are reference
	// material for an operator who came looking.
	const [srcOpen, setSrcOpen] = useStateMD(false);
	// Which repo the mmproj picker's listing was fetched for. The probe is
	// lazy — one POST on the disclosure's first open, never on drawer mount —
	// and this guards the re-open from re-firing it for the same coord.
	const inspectedRepo = useRefMD(null);
	// Inline title editor (Task 3): the ✎ button swaps the name span for an
	// input seeded from the CURRENT draft (`name`), never from the live model
	// prop, so a prior uncommitted edit survives reopening the editor. Escape
	// must revert without ever calling setName — the 735b6291 bug class (a
	// blur fired by the input's own removal from the DOM once landed a
	// "cancelled" edit anyway). titleCancelRef is the synchronous guard: Escape
	// flags it before closing, and the shared commit path checks the flag
	// first, so even a stray blur from unmounting can't sneak the draft
	// through. Enter and a real click-away both funnel through the same
	// commit path — "blur commits like Enter".
	const [editingTitle, setEditingTitle] = useStateMD(false);
	const [titleDraft, setTitleDraft] = useStateMD("");
	const titleCancelRef = useRefMD(false);
	// Per-type default: the `model` prop is live-polled (see the seam note
	// above), but the models-query invalidation this POST fires can land after
	// the drawer has already read the row, so track the POST response as the
	// local authority and let the badge flip immediately; null = defer to the
	// baseline snapshot.
	const [defaultOverride, setDefaultOverride] = useStateMD(null);
	// The frozen open-time snapshot (see modelBaseline). Every dirty predicate
	// and the whole save body derive from this, never from the live prop.
	const [baseline, setBaseline] = useStateMD(null);
	// Which model id the current baseline belongs to — guards the effect so a
	// re-render caused by a poll can't re-seed over the operator's edits.
	const baselineFor = useRefMD(null);

	// Seed the form AND snapshot the baseline in the SAME effect, so the two can
	// never come from different payloads (which is the whole #1398 class).
	useEffectMD(() => {
		if (!open || !model) {
			baselineFor.current = null;
			setBaseline(null);
			return;
		}
		if (baselineFor.current === model.id) return;
		baselineFor.current = model.id;
		const b = modelBaseline(model, enums);
		setBaseline(b);
		setName(b.name);
		setProvider(b.provider);
		setMmproj(b.mmproj);
		setHfRepo(b.hfRepo);
		setHfFilename(b.hfFilename);
		setChatTemplate(b.chatTemplate);
		setMtp(b.mtp);
		setJinja(b.jinja);
		setDefaultOverride(null);
		// A different row means a different repo listing — close the disclosure
		// and forget the probe, so the next open fetches THIS model's projectors.
		setSrcOpen(false);
		inspectedRepo.current = null;
		inspect.reset();
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [open, model?.id]);

	// The lazy repo probe (Task 9). Fires on the transition into `open` only,
	// so editing the HF repo field while the disclosure is open doesn't fire a
	// POST per keystroke; closing and re-opening picks the new coord up.
	useEffectMD(() => {
		if (!srcOpen) return;
		const repo = hfRepo.trim();
		if (!repo || inspectedRepo.current === repo) return;
		inspectedRepo.current = repo;
		inspect.mutate({ hf_repo: repo });
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [srcOpen]);

	// The vision↔mmproj invariant (#1380). A row advertising `vision` with no
	// projector leaves the launch path no `--mmproj` to load. This used to be a
	// decorative red div the save ignored, so it is now the one input to the
	// gate both `onSave` and the Save button consult. Capabilities are no longer
	// editable here (#2193): `vision` comes from the registry row, so the only
	// way an operator can violate the invariant in this drawer is clearing the
	// projector path on a model that advertises vision.
	const visionModel = (baseline?.caps || []).includes("vision");
	const mmprojError =
		visionModel && !mmproj.trim()
			? "this model advertises vision — an mmproj sidecar path is required"
			: null;

	// MMProj picker feed (Task 9). The directory comes off the BASELINE, not the
	// live draft: picking "— none —" empties `mmproj`, and re-deriving from the
	// draft would make the picker's own options disappear mid-interaction.
	const mmprojDir = mmprojDirOf(baseline?.mmproj, model?.path);
	const mmprojVariants = useMemoMD(() => {
		return Array.isArray(inspect.data?.mmproj) ? inspect.data.mmproj : [];
	}, [inspect.data]);
	// Four ways to have nothing to pick FROM — no repo, a failed probe, a repo
	// that ships no projector, or no directory to resolve a filename against.
	// Each degrades to the free-text path input rather than to a dead control.
	const mmprojPicker =
		!!hfRepo.trim() && !inspect.isError && !!mmprojDir && mmprojVariants.length > 0;
	// Both the STORED projector and the current draft ride as extra rows: a
	// stored value the repo doesn't list must stay pickable after the operator
	// tries "— none —", or the only way back to it is closing the drawer and
	// losing every other unsaved edit.
	const mmprojOptions = useMemoMD(
		() =>
			mmprojPicker
				? mmprojRichOptions(mmprojDir, mmprojVariants, [baseline?.mmproj, mmproj])
				: [],
		[mmprojPicker, mmprojDir, mmprojVariants, baseline?.mmproj, mmproj],
	);

	// One gate for every inline error. Today that is the vision↔mmproj invariant
	// (#1380); `onSave` and the Save button both consult it, so a new validation
	// can never be enforced in one place and forgotten in the other.
	const saveBlocked = !!mmprojError;

	// Return null when closed — matching the Modal contract the deleted
	// RecipeEditorModal honoured (Modal returns null when !open). The <Drawer>
	// primitive otherwise stays mounted, and `selected` is non-null even when the
	// drawer is shut, so an always-mounted drawer would leave phantom inputs in
	// the DOM (colliding with the AddByHF modal's fields). All hooks run above.
	if (!open || !model) return null;

	// Per-type default marker toggle. Server-side single chokepoint enforces
	// "one default per type" (promoting demotes the current holder). The list's
	// badges refresh via the models-query invalidation; THIS drawer's badge
	// flips from the POST response (the `model` prop is an open-time snapshot).
	const isTypeDefault = defaultOverride ?? !!model.default;
	const typeLabel = model.type || "type";
	const onToggleDefault = async () => {
		const next = !isTypeDefault;
		try {
			const res = await setDefault.mutateAsync({ id: model.id, default: next });
			setDefaultOverride(typeof res.default === "boolean" ? res.default : next);
			window.__hal0Toast &&
				window.__hal0Toast(
					next
						? `${model.longName || model.id} is now the ${res.type} default` +
								(res.demoted && res.demoted.length
									? ` (demoted ${res.demoted.join(", ")})`
									: "")
						: `Removed ${model.longName || model.id} as the ${res.type} default`,
					"ok",
				);
		} catch (e) {
			window.__hal0Toast &&
				window.__hal0Toast(
					`Default change failed — ${e?.message || "see logs"}`,
					"err",
				);
		}
	};

	// Inline title editor (Task 3, see the state comment above). One commit
	// path for both Enter and a real click-away blur; Escape never reaches it
	// with the cancel flag unset, so it can never carry the draft into `name`.
	const openTitleEdit = () => {
		titleCancelRef.current = false;
		setTitleDraft(name);
		setEditingTitle(true);
	};
	const commitTitle = () => {
		if (titleCancelRef.current) {
			titleCancelRef.current = false;
			setEditingTitle(false);
			return;
		}
		setName(titleDraft);
		setEditingTitle(false);
	};
	const cancelTitleEdit = (e) => {
		// Consumed here so the Drawer's document-level Escape→close listener
		// never sees this press — Escape in the field cancels the rename, it
		// must not also close the drawer (same reasoning as the slot rename
		// field, slot-modals.jsx).
		e.stopPropagation();
		titleCancelRef.current = true;
		setEditingTitle(false);
	};

	// THE comparison — derived once, against the FROZEN baseline, read by both
	// the unsaved-changes guard and the save body. Nothing below re-derives a
	// predicate and nothing touches the live `model` prop.
	const changes = deriveModelChanges(baseline, {
		name,
		provider,
		mmproj,
		hfRepo,
		hfFilename,
		chatTemplate,
		mtp,
		jinja,
	});
	const dirty = !!changes && changes.any;

	const onSave = async () => {
		if (saveBlocked) return; // inline errors block; no PUT fires
		// #1441: nothing changed ⇒ nothing to write. The button is disabled too;
		// this is the belt.
		if (!changes || !changes.any) return;
		// ADR-0029 §6: the file facts are TOP-LEVEL keys on the model, and a PUT
		// is a plain top-level replace — an absent key keeps the stored value,
		// an explicit `null` clears it. So only the facts that changed ship
		// (an untouched tokenizer_repo is never re-sent). Tunes — extra_args,
		// context_size, profile, enable_thinking, vision, n_gpu_layers,
		// rope_freq_base — belong on a profile, and a `defaults` key at all is
		// a 422.
		const body = {};
		if (changes.chatTemplate)
			body.chat_template =
				chatTemplate && chatTemplate !== "auto" ? chatTemplate : null;
		// Tri-state: auto = null (no opinion), on/off = boolean.
		if (changes.mtp) body.mtp = mtp === "on" ? true : mtp === "off" ? false : null;
		if (changes.jinja)
			body.jinja = jinja === "on" ? true : jinja === "off" ? false : null;
		// Identity fields ship only when they actually changed, each answered by
		// the one derived comparison above (`changes`) — the same values the
		// dirty aggregate and the Save gate read.
		if (changes.name) body.name = changes.trimmedName;
		if (changes.provider) body.provider = provider || null;
		if (changes.mmproj) body.mmproj = changes.trimmedMmproj || null;
		if (changes.hfRepo) body.hf_repo = changes.trimmedRepo;
		if (changes.hfFilename) body.hf_filename = changes.trimmedFile;
		try {
			await update.mutateAsync({ id: model.id, body });
			window.__hal0Toast &&
				window.__hal0Toast(`Updated ${model.longName || model.id}`, "ok");
			onClose();
		} catch (e) {
			// Surface the server envelope inline (managed-arg rejection etc.).
			window.__hal0Toast &&
				window.__hal0Toast(`Save failed — ${e?.message || "see logs"}`, "err");
		}
	};

	const modality = modalityLabel(baseline?.caps || [], model.type);

	// File-fact overrides ledger (cap-overrides.ts): the two tri-state strings
	// above are the save-body source of truth (model.mtp / model.jinja) —
	// the ledger just reads/writes them through the null|true|false shape those
	// facts already use.
	const capFlags = {
		mtp: mtp === "on" ? true : mtp === "off" ? false : null,
		jinja: jinja === "on" ? true : jinja === "off" ? false : null,
	};
	const CAP_SETTERS = { mtp: setMtp, jinja: setJinja };
	const setCapOverride = (id, value) => CAP_SETTERS[id](value ? "on" : "off");
	const clearCapOverride = (id) => CAP_SETTERS[id]("auto");

	// Runner compatibility · Runs on (derived, read-only — Task 3 of PR-1
	// retired the editable Backends chips). `runs_on` is served on every
	// /api/models row (services/models_service.py:model_to_dict, via
	// capabilities.catalog.runs_on_for_model) as host-backend lane ids
	// (gpu-rocm | gpu-vulkan | cpu | npu) — the same vocabulary as
	// MetaEnums.devices, so deviceById() resolves the display label.
	const runsOnLanes = (Array.isArray(model.runs_on) ? model.runs_on : []).map(
		(id) => ({ id, label: deviceById(id, enums)?.label || id }),
	);
	// Distinguish "the row never carried this key" (older/legacy rows, or a
	// mock fixture that predates runs_on_for_model) from "the server computed
	// it and there really is nothing compatible" — those are different facts
	// and read very differently in the empty state below.
	const runsOnReported = model.runs_on !== undefined;

	// Native context window (read-only fact). registry/model.py
	// `Model.metadata` reserves `context_length` (GGUF arch max / curated
	// catalogue). The window a slot actually launches with comes from its
	// profile's ctx_size, falling back to this.
	const modelContextLength = Number(model?.metadata?.context_length) || null;

	// Title row (Task 3): inline-editable name + modality tag + ONE default
	// chip ride the header — facts/actions, not field rows, so the old
	// "Modality" and "Default for {type}" rows stay gone below, and the old
	// badge+ghost-chip+button trio collapses into the single toggle chip.
	// Shows the live draft (`name`), never the frozen `model` prop, so a
	// committed-but-unsaved edit sticks even if the editor is reopened.
	const titleText = name.trim() || model.longName || model.id;
	const titleNode = (
		<span style={{ display: "inline-flex", alignItems: "center", gap: 10, flexWrap: "wrap", minWidth: 0 }}>
			{editingTitle ? (
				<input
					className="input mono"
					data-testid="model-title-input"
					autoFocus
					placeholder={model.id}
					value={titleDraft}
					onChange={(e) => setTitleDraft(e.target.value)}
					onBlur={commitTitle}
					onKeyDown={(e) => {
						if (e.key === "Enter") {
							e.preventDefault();
							commitTitle();
						} else if (e.key === "Escape") {
							e.preventDefault();
							cancelTitleEdit(e);
						}
					}}
					style={{ fontSize: 14, minWidth: 140 }}
				/>
			) : (
				<>
					<span>{titleText}</span>
					<button
						type="button"
						className="btn ghost sm"
						data-testid="model-title-edit"
						onClick={openTitleEdit}
						aria-label="Edit name"
						title="Edit name"
						style={{ fontSize: 11, padding: "2px 7px", lineHeight: 1 }}
					>
						✎
					</button>
				</>
			)}
			<span
				className="tag"
				data-testid="model-modality"
				style={{
					color: "var(--fg-3)",
					fontFamily: "var(--jbm)",
					fontSize: 11,
					padding: "3px 9px",
					borderRadius: 4,
					border: "1px solid var(--line)",
					background: "var(--bg-2)",
				}}
			>
				{modality}
			</span>
			<button
				type="button"
				className="btn ghost sm"
				data-testid="model-default-toggle"
				onClick={onToggleDefault}
				disabled={setDefault.isPending}
				style={
					isTypeDefault
						? {
								fontSize: 10.5,
								color: "var(--ok)",
								borderColor: "var(--ok)",
								background: "var(--ok-soft)",
							}
						: { fontSize: 10.5 }
				}
			>
				{isTypeDefault ? `✓ ${typeLabel} default` : `${typeLabel} default`}
			</button>
		</span>
	);

	// Facts band (Task 3): read-only quant/size/arch/context/sha256/used-by
	// strip under the header. Each cell renders ONLY when its fact exists on
	// the row — an absent fact is an absent cell, never a blank one.
	const sizeGb =
		Number(model.size_bytes) > 0
			? (Number(model.size_bytes) / 1024 ** 3).toFixed(1)
			: null;
	const nativeContext = contextLengthLabel(modelContextLength);
	const modelSha256 =
		typeof model?.metadata?.sha256 === "string" && model.metadata.sha256
			? model.metadata.sha256
			: null;
	const usedBySlots = slotsUsingModel(slotsQuery.data, model.id);
	const factCellStyle = { minWidth: 0 };
	const factLabelStyle = {
		fontSize: 9,
		letterSpacing: ".05em",
		textTransform: "uppercase",
		color: "var(--fg-4)",
		marginBottom: 2,
	};
	const factValueStyle = { fontSize: 12, color: "var(--fg-2)" };
	const factsNode = (
		<div
			data-testid="model-facts"
			style={{
				display: "grid",
				gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))",
				gap: "10px 16px",
				margin: "4px 0 16px",
				padding: "11px 14px",
				border: "1px solid var(--line)",
				borderRadius: 8,
				background: "var(--bg-2)",
			}}
		>
			{model.quant && (
				<div style={factCellStyle}>
					<div style={factLabelStyle}>Quant</div>
					<div className="mono" style={factValueStyle}>{model.quant}</div>
				</div>
			)}
			{sizeGb && (
				<div style={factCellStyle}>
					<div style={factLabelStyle}>Size</div>
					<div className="mono" style={factValueStyle}>{sizeGb} GB</div>
				</div>
			)}
			{model.architecture && (
				<div style={factCellStyle}>
					<div style={factLabelStyle}>Architecture</div>
					<div className="mono" style={factValueStyle}>{model.architecture}</div>
				</div>
			)}
			{modelSha256 && (
				<div style={factCellStyle}>
					<div style={factLabelStyle}>sha256</div>
					<div className="mono" style={factValueStyle} title={modelSha256}>
						{modelSha256.slice(0, 8)}
					</div>
				</div>
			)}
			<div style={factCellStyle} data-testid="model-facts-usedby">
				<div style={factLabelStyle}>Used by</div>
				<div className="mono" style={factValueStyle}>
					{usedBySlots.length === 0 ? (
						"0 slots"
					) : (
						<>
							{usedBySlots.length} slot{usedBySlots.length === 1 ? "" : "s"} —{" "}
							{usedBySlots.map((s, i) => (
								<React.Fragment key={s.name}>
									{i > 0 && " · "}
									{onOpenSlot ? (
										<button
											type="button"
											className="link"
											onClick={() => onOpenSlot(s.name)}
										>
											{s.name}
										</button>
									) : (
										s.name
									)}
								</React.Fragment>
							))}
						</>
					)}
				</div>
			</div>
		</div>
	);

	return (
		<>
			<Drawer
				open={open}
				onClose={onClose}
				width={600}
				dirty={dirty}
				eyebrow="Edit model · file facts"
				title={titleNode}
				foot={
					<>
						<span
							style={{
								color: dirty ? "var(--warn)" : "var(--muted)",
							}}
							data-testid="model-foot-summary"
						>
							{footSummary(changes, slotsUsingModel(slotsQuery.data, model.id))}
						</span>
						<span style={{ display: "inline-flex", gap: 8 }}>
							<button className="btn ghost sm" onClick={onClose}>
								Cancel
							</button>
							<button
								className="btn sm"
								data-testid="model-save"
								onClick={onSave}
								// #1441: gate on the dirty aggregate, like the slot
								// drawer. With zero edits there is nothing to persist,
								// and firing anyway re-sent every fact.
								disabled={update.isPending || saveBlocked || !dirty}
								title={
									!dirty && !saveBlocked && !update.isPending
										? "No changes to save"
										: undefined
								}
							>
								{update.isPending ? "Saving…" : "Save model"}
							</button>
						</span>
					</>
				}
			>
				{/* ── Facts band (Task 3) — name/default moved onto the header title;
				    quant/size/arch/context/sha256/used-by ride here instead. ── */}
				{factsNode}

				{/* ── Tune pointer: the model carries no tune. ── */}
				<div
					className="hint"
					data-testid="model-tune-note"
					style={{ margin: "16px 0 4px" }}
				>
					Flags, context window, thinking, vision and MTP on/off are set on a
					profile, and a slot picks the profile. This drawer only records facts
					about the file.
				</div>

				{/* ── File facts (1a form-row rhythm; panel 07) ── */}
				<div className="form-section" style={{ marginTop: 16 }}>
					File facts
				</div>
				<div className="form-row">
					<div className="form-lbl">
						<span>Chat template</span>
						<FieldInfoIcon description="auto = use the template embedded in the GGUF" />
					</div>
					<div className="form-ctl">
						<RichSelect
							data-testid="model-chat-template"
							aria-label="Chat template"
							value={chatTemplate}
							options={chatTemplateRichOptions(templates.data)}
							onChange={setChatTemplate}
						/>
					</div>
				</div>
				{/* Overrides ledger (panel 09 V1): Auto is invisible — only an
            overridden MTP-heads/Jinja fact renders a chip. */}
				<div className="form-row">
					<div className="form-lbl">
						<span>File facts</span>
						<FieldInfoIcon description="Auto is invisible here. MTP heads: does this file carry MTP heads (Auto reads the 'mtp' tag). Jinja: does its chat template work with --jinja. '+ Override…' sets one on or off; ✕ returns it to Auto. Whether a slot USES MTP is the profile's call." />
					</div>
					<div className="form-ctl">
						<CapOverridesLedger
							flags={capFlags}
							onSet={setCapOverride}
							onClear={clearCapOverride}
						/>
					</div>
				</div>
				<div className="form-row">
					<div className="form-lbl">
						<span>Capabilities</span>
						<FieldInfoIcon description="capabilities come from the registry row (pull metadata / auto-detect) — read-only here" />
					</div>
					<div
						className="form-ctl"
						style={{ display: "flex", gap: 6, flexWrap: "wrap" }}
					>
						<span
							data-testid="model-caps-readout"
							style={{ display: "inline-flex", gap: 6, flexWrap: "wrap" }}
						>
							{(baseline?.caps || []).map((cap) => (
								<span
									key={cap}
									className="mdl-chip"
									style={{ pointerEvents: "none", opacity: 0.7 }}
								>
									{cap}
								</span>
							))}
						</span>
					</div>
				</div>

				{/* ── Native context window (read-only fact) ── */}
				<div className="form-row">
					<div className="form-lbl">
						<span>Native context</span>
						<FieldInfoIcon description="the context window this file was trained for · read-only · the window a slot launches with is its profile's ctx_size, which falls back to this" />
					</div>
					<div className="form-ctl">
						<span className="mono" data-testid="model-ctx-native">
							{modelContextLength
								? `${nativeContext} (${modelContextLength.toLocaleString("en-US")} tokens)`
								: "not recorded on this row"}
						</span>
					</div>
				</div>

				{/* ── Runner compatibility (Engine + Runs on; panel 07/13) ── */}
				<div className="form-section" style={{ marginTop: 16 }}>
					Runner compatibility
				</div>
				<div className="form-row">
					<div className="form-lbl">
						<span>Engine</span>
						<FieldInfoIcon description="engine identity that serves this model's launch (llama-server · flm · kokoro · qwen3tts · moonshine · comfyui) · empty = derive from the stored backend tags" />
					</div>
					<div className="form-ctl">
						<RichSelect
							data-testid="model-provider-select"
							aria-label="Engine"
							value={provider}
							options={engineRichOptions(enums.runtime_families, model.provider_effective)}
							onChange={setProvider}
						/>
					</div>
				</div>
				<div className="form-row">
					<div className="form-lbl">
						<span>Runs on</span>
						<FieldInfoIcon description="host-backend lanes this model can run under · derived from architecture × the runner catalogue — computed, not editable" />
					</div>
					<div
						className="form-ctl"
						data-testid="model-runs-on"
						style={{ display: "flex", flexWrap: "wrap", gap: 6 }}
					>
						{runsOnLanes.length ? (
							runsOnLanes.map((lane) => (
								<span
									key={lane.id}
									className="mdl-chip"
									style={{
										borderStyle: "dashed",
										pointerEvents: "none",
										opacity: 0.85,
									}}
								>
									{lane.label}
								</span>
							))
						) : (
							<span className="hint">
								{runsOnReported
									? "no compatible runner lanes detected"
									: "not reported"}
							</span>
						)}
					</div>
				</div>

				{/* Runner / image section removed (spec-hw-slot-ownership §8): the model
            is device-agnostic and no longer resolves to a runner or image. The
            runner is chosen on the slot (BINARY → RUNNER_IMAGES); the Runtimes
            page (Settings → Runtimes) shows which slots resolve to each runner. */}

				{/* ── Source (disclosure) ── Where this row came from and where its
            files sit: coords, projector, paths. Reference material, so it
            rests closed — same controlled "▸"/"▾" idiom the slot drawer's
            Advanced disclosure uses (slot-modals.jsx's slot-hw-advanced),
            not a native <details>, so the glyph and label stay ours. */}
				<div
					className="form-section"
					data-testid="model-source-disclosure"
					role="button"
					tabIndex={0}
					aria-expanded={srcOpen}
					style={{ marginTop: 16, cursor: "pointer", userSelect: "none" }}
					onClick={() => setSrcOpen((o) => !o)}
					onKeyDown={(e) => {
						if (e.key === "Enter" || e.key === " ") {
							e.preventDefault();
							setSrcOpen((o) => !o);
						}
					}}
				>
					<span className="mono" style={{ marginRight: 6, color: "var(--fg-4)" }}>
						{srcOpen ? "▾" : "▸"}
					</span>
					Source — repo · file · mmproj · paths
				</div>
				{srcOpen && (
					<>
						<div className="form-row">
							<div className="form-lbl">
								<span>MMProj</span>
								<FieldInfoIcon
									description={
										mmprojPicker
											? "vision projector sidecar · picked from the repo's own files and stored as the absolute path beside the model"
											: "vision projector sidecar path — absolute, handed to the runner as --mmproj"
									}
								/>
							</div>
							<div className="form-ctl">
								{mmprojPicker ? (
									<RichSelect
										data-testid="model-mmproj-select"
										aria-label="MMProj"
										value={mmproj}
										options={mmprojOptions}
										onChange={setMmproj}
									/>
								) : (
									<input
										className="input mono"
										data-testid="model-mmproj-input"
										placeholder="/var/lib/hal0/models/…/mmproj-Q8.gguf"
										value={mmproj}
										onChange={(e) => setMmproj(e.target.value)}
									/>
								)}
								{mmprojError && (
									<div
										className="err"
										data-testid="model-mmproj-error"
										style={{ marginTop: 6 }}
									>
										{mmprojError}
									</div>
								)}
							</div>
						</div>
						<div className="form-row">
							<div className="form-lbl">
								<span>HF repo</span>
								<FieldInfoIcon description="HuggingFace repo · needed to re-pull · feeds the projector list above" />
							</div>
							<div className="form-ctl">
								<input
									className="input mono"
									data-testid="model-hfrepo-input"
									placeholder="unsloth/Qwen3-8B-GGUF"
									value={hfRepo}
									onChange={(e) => setHfRepo(e.target.value)}
								/>
							</div>
						</div>
						<div className="form-row">
							<div className="form-lbl">
								<span>HF filename</span>
								<FieldInfoIcon description="variant filename within the repo" />
							</div>
							<div className="form-ctl">
								<input
									className="input mono"
									data-testid="model-hffile-input"
									placeholder="qwen3-8b-q4_k_m.gguf"
									value={hfFilename}
									onChange={(e) => setHfFilename(e.target.value)}
								/>
							</div>
						</div>
						<div className="form-row">
							<div className="form-lbl">
								<span>Paths</span>
								<FieldInfoIcon description="read-only · the model path is the registry's stored value; the mmproj line reads back whatever the control above currently has selected, saved or not" />
							</div>
							<div className="form-ctl" data-testid="model-source-paths">
								<SourcePathLine
									label="model"
									value={model.path}
									testId="model-source-copy-path"
									empty="not recorded on this row"
								/>
								{/* The DRAFT projector, not the stored one: this line is
                    where a pick made above reads back as the resolved
                    absolute path it WOULD save as — so an unsaved pick says
                    so rather than passing itself off as a file on disk. */}
								<SourcePathLine
									label="mmproj"
									value={mmproj}
									testId="model-source-copy-mmproj"
									empty="no projector paired"
									note={
										baseline && mmproj.trim() !== baseline.mmproj
											? "unsaved selection — this is the path Save will write, not what is stored today"
											: null
									}
								/>
							</div>
						</div>
					</>
				)}

				{update.isError && (
					<div className="err">{update.error?.message || "Save failed"}</div>
				)}
			</Drawer>

		</>
	);
}

Object.assign(window, {
	ModelDrawer,
});
