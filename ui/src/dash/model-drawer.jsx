// hal0 dashboard — Model editor drawer (D1, post-R3 surface rework).
//
// Replaced the pre-drawer RecipeEditorModal (deleted from model-modals.jsx in
// #2105) with a right-side Drawer built around one claim: "the model is the launchable thing." Converged design per
// the R3 canvas — 1b launch-command hero (the flags text leads, framed as the
// resolved launch command) + 1c's inline divergence diff + 1a's form-row rhythm
// for the typed-capability block.
//
// Ratified semantics (docs/rework/hal0-specs/spec-flags-ownership.md):
//   · flags live on the MODEL — model.defaults.extra_args is the materialized
//     tune remainder; what you see is exactly what launches (no inheritance).
//   · profiles are copy-on-stamp TEMPLATES — selecting one COPIES its `flags`
//     text into the model's editor; saving saves to the model; the profile is
//     never mutated. Provenance (which profile seeded it) is model.defaults.profile;
//     divergence is a derived, client-side diff vs that profile's current flags.
//   · typed capabilities (mtp / jinja / chat_template / modality) stay discrete
//     controls, never buried in the freeform text.
//   · managed args (--model --ctx-size --host --port --n-gpu-layers --alias) are
//     computed & rejected — screened inline before save (§21.7).
//
// Save writes through useModelUpdate (PUT /api/models/{id}); the `defaults` bag
// is flat-merged wholesale, so we start from the stored defaults and override
// only the keys we surface (emptying an input deletes just that key).

import {
	useModelUpdate,
	useModelSetDefault,
} from "@/api/hooks/useModels";
import { useModelSeedProfile } from "@/api/hooks/useModelSeedProfile";
import { useChatTemplates } from "@/api/hooks/useChatTemplates";
import { useProfiles } from "@/api/hooks/useProfiles";
import { useMetaEnums } from "@/api/hooks/useMeta";
import { useSlots } from "@/api/hooks/useSlots";
import { slotsUsingModel } from "@/dash/model-usage.js";
import {
	canonicalCapabilities,
	deviceById,
	modelDeviceClasses,
	profileDeviceClass,
} from "@/lib/deviceMeta";
import {
	findManagedFlags,
	findSlotHardwareFlags,
	MANAGED_FLAG_SOURCE,
	highlightSegments,
	diffFlags,
	tokenizeFlags,
} from "@/dash/flags-tune.js";
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

// tri-state: absent (auto) | true (on) | false (off) — for mtp / jinja typed caps.
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
// returns that cap to Auto via the existing save-gated defaults path.
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

// ─── FlagsEditor — the launch-command hero textarea (token-highlighted) ──────
// A real editable <textarea> (the queryable source of truth) with an aria-hidden
// highlight layer behind it: flag tokens amber, values dim. The textarea text is
// transparent so the highlight shows through, caret stays visible. It is TEXT,
// not a form — shlex-tokenised, exactly what launches.
function FlagsEditor({ value, onChange, invalid }) {
	const preRef = useRefMD(null);
	const taRef = useRefMD(null);
	const segs = highlightSegments(value);
	const syncScroll = () => {
		if (preRef.current && taRef.current) {
			preRef.current.scrollTop = taRef.current.scrollTop;
			preRef.current.scrollLeft = taRef.current.scrollLeft;
		}
	};
	const shared = {
		margin: 0,
		padding: "12px 14px",
		fontFamily: "var(--jbm)",
		fontSize: 12.5,
		lineHeight: 1.85,
		letterSpacing: "normal",
		whiteSpace: "pre-wrap",
		overflowWrap: "anywhere",
		wordBreak: "break-word",
		border: "1px solid transparent",
		borderRadius: 6,
		tabSize: 2,
	};
	return (
		<div
			style={{
				position: "relative",
				background: "var(--bg-sunken)",
				border: `1px solid ${invalid ? "var(--err-line)" : "var(--line)"}`,
				borderRadius: 6,
				minHeight: 104,
			}}
		>
			<pre
				ref={preRef}
				aria-hidden="true"
				style={{
					...shared,
					position: "absolute",
					inset: 0,
					color: "var(--fg-3)",
					pointerEvents: "none",
					overflow: "hidden",
				}}
			>
				{segs.map((s, i) =>
					s.kind === "flag" ? (
						<span key={i} style={{ color: "var(--accent)" }}>
							{s.text}
						</span>
					) : (
						<span key={i}>{s.text}</span>
					),
				)}
				{"\n"}
			</pre>
			<textarea
				ref={taRef}
				data-testid="model-flags-input"
				spellCheck={false}
				value={value}
				onChange={(e) => onChange(e.target.value)}
				onScroll={syncScroll}
				placeholder="pick a profile to seed flags, or type your own · e.g. -fa on -b 2048 -ctk q8_0"
				style={{
					...shared,
					position: "relative",
					display: "block",
					width: "100%",
					minHeight: 104,
					resize: "vertical",
					background: "transparent",
					color: "transparent",
					caretColor: "var(--fg)",
					outline: "none",
				}}
			/>
		</div>
	);
}

// ─── SeedProfileButton — "⤵ Seed from profile…" ghost button ────────────────
// Replaces the always-visible template select (panel 12, Fix 2/3): seeding is
// a deliberate action, not a persistent field. Same profile list the old
// select offered (fitProfiles — filtered to what fits this model); picking
// one is the caller's job (onPick), same click-outside/Escape idiom as
// CapOverrideAdd above. `disabled` (Fix 5): a seed POST is in flight —
// suppress opening the menu and picking another option so a double-click
// can't fire a second POST while the first is still pending.
function SeedProfileButton({ options, onPick, disabled = false }) {
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
	return (
		<div ref={wrapRef} style={{ position: "relative", display: "inline-block" }}>
			<button
				type="button"
				className="btn ghost sm"
				data-testid="model-seed-profile-open"
				aria-expanded={open}
				aria-controls="model-seed-profile-popover"
				disabled={!options.length || disabled}
				title={
					!options.length
						? "no profiles fit this model"
						: disabled
							? "seeding…"
							: undefined
				}
				onClick={() => setOpen((o) => !o)}
			>
				⤵ Seed from profile…
			</button>
			{open && !disabled && options.length > 0 && (
				<>
					<div className="mdl-row-menu-backdrop" onClick={() => setOpen(false)} />
					{/* Same honest-roles call as CapOverrideAdd above: no arrow-key
					    traversal backs this list, so it's a labelled popover of
					    buttons, not a role="menu"/menuitem widget. */}
					<div
						id="model-seed-profile-popover"
						role="group"
						aria-label="Seed from profile"
						style={{
							position: "absolute",
							top: "calc(100% + 4px)",
							right: 0,
							zIndex: 60,
							minWidth: 220,
							maxHeight: 280,
							overflowY: "auto",
							background: "var(--bg-2)",
							border: "1px solid var(--line-strong)",
							borderRadius: 8,
							boxShadow: "0 16px 48px -8px rgba(0,0,0,.65)",
						}}
					>
						{options.map((p) => (
							<button
								key={p.name}
								type="button"
								className="mono"
								data-testid={`model-seed-profile-option-${p.name}`}
								style={{
									display: "block",
									width: "100%",
									textAlign: "left",
									padding: "8px 12px",
									fontSize: 12,
									background: "transparent",
									border: "none",
									color: "var(--fg)",
									cursor: "pointer",
								}}
								onClick={() => {
									setOpen(false);
									onPick(p.name);
								}}
							>
								{p.name}
								{p.intent ? (
									<span style={{ color: "var(--fg-4)" }}> · {p.intent}</span>
								) : null}
							</button>
						))}
					</div>
				</>
			)}
		</div>
	);
}

// ─── DivergenceDiff — client-side model-vs-profile diff (1c, inline) ─────────
function DivergenceDiff({ diff, profileName, onReset }) {
	return (
		<div
			style={{
				marginTop: 12,
				border: "1px solid var(--line)",
				borderRadius: 6,
				overflow: "hidden",
			}}
			data-testid="model-divergence-diff"
		>
			<div
				style={{
					padding: "9px 13px",
					background: "var(--bg-2)",
					borderBottom: "1px solid var(--line)",
					display: "flex",
					alignItems: "center",
					gap: 8,
				}}
			>
				<span
					className="mono"
					style={{
						fontSize: 10,
						letterSpacing: ".06em",
						textTransform: "uppercase",
						color: "var(--fg-3)",
					}}
				>
					divergence · model vs {profileName}
				</span>
				<span style={{ flex: 1 }} />
				<button
					type="button"
					className="mono"
					data-testid="model-reset-profile"
					onClick={onReset}
					style={{
						background: "transparent",
						border: "none",
						fontSize: 10.5,
						color: "var(--fg-3)",
						cursor: "pointer",
						padding: 0,
					}}
				>
					↺ reset to profile
				</button>
			</div>
			<div
				className="mono"
				style={{
					padding: "11px 13px",
					fontSize: 11.5,
					lineHeight: 1.8,
					background: "var(--bg-sunken)",
				}}
			>
				{diff.added.map((p, i) => (
					<div key={`a${i}`} style={{ color: "var(--ok)" }}>
						+{" "}
						<span
							style={{ background: "rgba(111,207,151,.12)", padding: "0 3px" }}
						>
							{p.flag}
							{p.value != null ? ` ${p.value}` : ""}
						</span>{" "}
						<span style={{ color: "var(--fg-5)" }}>added</span>
					</div>
				))}
				{diff.changed.map((p, i) => (
					<div key={`c${i}`} style={{ color: "var(--err)" }}>
						−{" "}
						<span
							style={{ background: "rgba(239,107,107,.12)", padding: "0 3px" }}
						>
							{p.flag} {p.from}
						</span>{" "}
						<span style={{ color: "var(--fg-5)" }}>→</span>{" "}
						<span style={{ color: "var(--ok)" }}>
							{p.flag} {p.to}
						</span>
					</div>
				))}
				{diff.removed.map((p, i) => (
					<div key={`r${i}`} style={{ color: "var(--err)" }}>
						−{" "}
						<span
							style={{ background: "rgba(239,107,107,.12)", padding: "0 3px" }}
						>
							{p.flag}
							{p.value != null ? ` ${p.value}` : ""}
						</span>{" "}
						<span style={{ color: "var(--fg-5)" }}>removed</span>
					</div>
				))}
				<div style={{ color: "var(--fg-5)" }}>
					&nbsp;&nbsp;{diff.unchanged} unchanged
				</div>
			</div>
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
//     `defaults.context_size`; the untouched field went dirty and Save wrote
//     the drawer's stale seed back over it — a lost update caused by doing
//     nothing;
//   * `onSave` starts from `{ ...init }` so the keys this drawer never renders
//     ride along; read live, they rode along as a mid-edit poll left them
//     rather than as the operator saw them.
//
// Snapshot the model ONCE at open, freeze it, and derive both the dirty
// aggregate and the save body from it. `caps` is stored already canonicalised
// so a late `/api/meta/enums` load — which changes `canonicalCapabilities`'
// output from the fallback table to the real one — cannot fabricate a diff
// against a form seeded with the earlier answer.
function modelBaseline(model, enums) {
	if (!model) return null;
	const init = model.defaults || {};
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
		extra: init.extra_args || "",
		profile: init.profile || "",
		ctx: init.context_size != null ? String(init.context_size) : "",
		chatTemplate: init.chat_template ?? "auto",
		mtp: triFromDefault(init.mtp),
		thinking: triFromDefault(init.enable_thinking),
		jinja: triFromDefault(init.jinja),
		vision: triFromDefault(init.vision),
		// The whole defaults block as it stood at open. `onSave` starts from THIS
		// so the keys this drawer doesn't render (rope_freq_base, …) are carried
		// through as the operator saw them. Reading them live meant an unrelated
		// save silently shipped a value that landed after the drawer opened.
		defaults: { ...init },
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
		extra: form.extra !== baseline.extra,
		profile: form.profile !== baseline.profile,
		ctx: form.ctx !== baseline.ctx,
		chatTemplate: form.chatTemplate !== baseline.chatTemplate,
		mtp: form.mtp !== baseline.mtp,
		thinking: form.thinking !== baseline.thinking,
		jinja: form.jinja !== baseline.jinja,
		vision: form.vision !== baseline.vision,
	};
	c.any =
		c.name ||
		c.provider ||
		c.mmproj ||
		c.hfRepo ||
		c.hfFilename ||
		c.extra ||
		c.profile ||
		c.ctx ||
		c.chatTemplate ||
		c.mtp ||
		c.thinking ||
		c.jinja ||
		c.vision;
	return c;
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
	const seedProfile = useModelSeedProfile();
	const templates = useChatTemplates(open);
	const profilesQuery = useProfiles();
	const enums = useMetaEnums();
	const slotsQuery = useSlots();

	// Identity + typed fields (preserve the full RecipeEditor save surface).
	const [name, setName] = useStateMD("");
	// Engine identity (Runner compatibility section) — the write path now;
	// `backends` died with it (Task 3 of PR-1, models.py:update_model drops a
	// client-sent `backends` silently). Empty string = derive server-side.
	const [provider, setProvider] = useStateMD("");
	const [mmproj, setMmproj] = useStateMD("");
	const [hfRepo, setHfRepo] = useStateMD("");
	const [hfFilename, setHfFilename] = useStateMD("");
	// Flags / template (the launch tune).
	const [extra, setExtra] = useStateMD("");
	const [profile, setProfile] = useStateMD("");
	// Typed caps.
	const [ctx, setCtx] = useStateMD("");
	const [chatTemplate, setChatTemplate] = useStateMD("auto");
	const [mtp, setMtp] = useStateMD("auto");
	const [thinking, setThinking] = useStateMD("auto");
	const [jinja, setJinja] = useStateMD("auto");
	// spec-hw-slot-ownership §1: vision moved off the (now-gone) per-slot
	// toggle (#901) onto the model, alongside mtp/jinja/thinking — same
	// tri-state Auto/On/Off pattern. Auto = mmproj loads whenever the model
	// carries one; Off force-suppresses it even when present.
	const [vision, setVision] = useStateMD("auto");
	// Local UI state.
	const [confirm, setConfirm] = useStateMD(null); // {title,message,confirmLabel,onConfirm}
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
		setExtra(b.extra);
		setProfile(b.profile);
		setCtx(b.ctx);
		setChatTemplate(b.chatTemplate);
		setMtp(b.mtp);
		setThinking(b.thinking);
		setJinja(b.jinja);
		setVision(b.vision);
		setDefaultOverride(null);
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [open, model?.id]);

	// Profiles that fit this model (same filter the old RecipeEditor used), so the
	// template dropdown offers device-appropriate seeds.
	const fitProfiles = useMemoMD(() => {
		const all = Array.isArray(profilesQuery.data) ? profilesQuery.data : [];
		if (!model) return all;
		const mClasses = modelDeviceClasses(model.backends, model.device, enums);
		const mBackends = Array.isArray(model.backends) ? model.backends : [];
		const fit = all.filter((p) => {
			const pc = profileDeviceClass(p);
			const classOk = !pc || mClasses.size === 0 || mClasses.has(pc);
			const backendOk =
				!p.backend || mBackends.length === 0 || mBackends.includes(p.backend);
			const typeOk =
				!model.type ||
				!Array.isArray(p.supported_slot_types) ||
				p.supported_slot_types.includes(model.type);
			return classOk && backendOk && typeOk;
		});
		// Always keep the current provenance selectable even if it no longer fits.
		const names = new Set(fit.map((p) => p.name));
		if (profile && !names.has(profile)) {
			const cur = all.find((p) => p.name === profile);
			if (cur) return [cur, ...fit];
		}
		return fit;
	}, [profilesQuery.data, model?.id, profile, enums]);

	const sourceProfile = useMemoMD(
		() =>
			(Array.isArray(profilesQuery.data)
				? profilesQuery.data.find((p) => p.name === profile)
				: null) || null,
		[profilesQuery.data, profile],
	);
	const diff = useMemoMD(
		() => (sourceProfile ? diffFlags(extra, sourceProfile.flags || "") : null),
		[extra, sourceProfile],
	);
	const diverged = !!(diff && diff.diverged);

	// Managed-arg + slot-hardware + shlex validation on the flags text (inline,
	// blocks save). spec-hw-slot-ownership §5: the model is device-agnostic, so
	// the grid-owned hardware flags (-ngl/-dev/--threads) are rejected with a
	// "belongs on the slot" message — mirrors the server hard-reject Lane C adds.
	// Checked BEFORE the managed set so --n-gpu-layers (in both) gets the more
	// specific slot-hardware message.
	const managedOffenders = useMemoMD(() => findManagedFlags(extra), [extra]);
	const hwOffenders = useMemoMD(() => findSlotHardwareFlags(extra), [extra]);
	const shlexErr = useMemoMD(() => tokenizeFlags(extra).error, [extra]);
	const flagsError = shlexErr
		? shlexErr
		: hwOffenders.length
			? slotHardwareFlagMessage(hwOffenders)
			: managedOffenders.length
				? managedFlagMessage(managedOffenders)
				: null;

	// The vision↔mmproj invariant (#1380). A row advertising `vision` with no
	// projector leaves the launch path no `--mmproj` to load. This used to be a
	// decorative red div the save ignored, so it now joins flagsError in the one
	// gate both `onSave` and the Save button consult. Capabilities are no longer
	// editable here (#2193): `vision` comes from the registry row, so the only
	// way an operator can violate the invariant in this drawer is clearing the
	// projector path on a model that advertises vision.
	const visionModel = (baseline?.caps || []).includes("vision");
	const mmprojError =
		visionModel && !mmproj.trim()
			? "this model advertises vision — an mmproj sidecar path is required"
			: null;

	// Context size validation (#1378). A lenient parseInt destroyed data twice
	// over: "32k" landed as 32 (a 1000x context collapse) and "abc" dropped the
	// key entirely, both behind a green "Updated" toast. Demand a clean integer
	// and mirror the slot drawer's ≥ 128 floor. Empty stays an explicit clear
	// (`onSave` sends `null`) — that intent is legitimate — but malformed text is
	// an error, never a silent truncation or clear. Derived like flagsError so
	// correcting the field releases the gate on the next keystroke.
	//
	// The server enforces the SAME floor since #1414 (400
	// model.context_size_out_of_range), so this is now a fast inline mirror of a
	// real backend rule rather than the only guard.
	const ctxError = useMemoMD(() => {
		const raw = ctx.trim();
		if (!raw) return null; // empty = clear the override
		if (!/^\d+$/.test(raw)) return "Must be a whole number of tokens";
		if (Number(raw) < 128) return "Must be an integer ≥ 128";
		return null;
	}, [ctx]);

	// One gate for every inline error: flags (#1379), the vision↔mmproj
	// invariant (#1380) and the context-size rules (#1378). Both `onSave` and
	// the Save button consult this, so a new validation can never be enforced
	// in one place and forgotten in the other.
	const saveBlocked = !!flagsError || !!mmprojError || !!ctxError;

	// Return null when closed — matching the Modal contract the deleted
	// RecipeEditorModal honoured (Modal returns null when !open). The <Drawer>
	// primitive otherwise stays mounted, and `selected` is non-null even when the
	// drawer is shut, so an always-mounted drawer would leave phantom inputs in
	// the DOM (colliding with the AddByHF modal's fields). All hooks run above.
	if (!open || !model) return null;

	// SEED (panel 12, Fix 3 — the server-side seed route): picking a profile
	// from the "⤵ Seed from profile…" menu POSTs /api/models/{id}/seed-profile
	// (useModelSeedProfile, Task 7), which materialises the profile's flags
	// into the model's `defaults` server-side and returns the updated row.
	// Confirm first if the current flags would be clobbered (non-empty and not
	// already the target text) — the write is immediate, there is no Cancel
	// once it lands. On success, splice just the two fields the route owns
	// (profile provenance + extra_args) into the local form AND the frozen
	// baseline, so the provenance/diverged chips read the new truth without
	// the Save-model gate reporting a false "unsaved change" for a value the
	// server already persisted.
	const doSeed = async (nextName) => {
		try {
			const res = await seedProfile.mutateAsync({ id: model.id, profile: nextName });
			// useModelSeedProfile's own onSuccess already invalidates ["models"]
			// (useModelSeedProfile.ts), which prefix-matches ["models", model.id] —
			// a second invalidate here was a no-op duplicate of the hook's job.
			const newProfile = res?.defaults?.profile || "";
			const newExtra = res?.defaults?.extra_args || "";
			setProfile(newProfile);
			setExtra(newExtra);
			setBaseline((prev) =>
				prev
					? {
							...prev,
							profile: newProfile,
							extra: newExtra,
							defaults: {
								...prev.defaults,
								profile: res?.defaults?.profile ?? null,
								extra_args: res?.defaults?.extra_args ?? null,
							},
						}
					: prev,
			);
			setConfirm(null);
			window.__hal0Toast &&
				window.__hal0Toast(
					`Seeded ${model.longName || model.id} from ${nextName}`,
					"ok",
				);
		} catch (e) {
			// Deliberately no setConfirm(null) here: a failed seed leaves the
			// confirm dialog open so the operator can retry the same POST
			// (network blip, transient 5xx) without re-picking the profile from
			// the menu. Only a successful seed (above) closes it.
			window.__hal0Toast &&
				window.__hal0Toast(`Seed failed — ${e?.message || "see logs"}`, "err");
		}
	};

	const seedFromProfile = (nextName) => {
		if (!nextName || seedProfile.isPending) return; // double-click / double-POST guard
		const target = (profilesQuery.data || []).find((p) => p.name === nextName);
		const targetFlags = target ? target.flags || "" : "";
		const wouldClobber = extra.trim() && diffFlags(extra, targetFlags).diverged;
		if (wouldClobber) {
			setConfirm({
				kind: "seed",
				title: "Replace launch flags?",
				message: `Seed from ${nextName}? Unsaved edits to the current flags are lost — this writes immediately.`,
				confirmLabel: "Seed from profile",
				onConfirm: () => doSeed(nextName),
			});
		} else {
			doSeed(nextName);
		}
	};

	const resetToProfile = () => {
		if (!sourceProfile) return;
		setConfirm({
			title: `Re-stamp from ${sourceProfile.name}?`,
			message: `Re-stamp from ${sourceProfile.name}? This replaces the model's launch flags with the profile's current text. Your edits are discarded.`,
			confirmLabel: "Reset to profile",
			onConfirm: () => {
				setExtra(sourceProfile.flags || "");
				setConfirm(null);
			},
		});
	};

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
		extra,
		profile,
		ctx,
		chatTemplate,
		mtp,
		thinking,
		jinja,
		vision,
	});
	const dirty = !!changes && changes.any;

	const onSave = async () => {
		if (saveBlocked) return; // inline errors block; no PUT fires
		// #1441: nothing changed ⇒ nothing to write. This used to fire anyway,
		// and because the whole `defaults` block is rebuilt below it was never
		// the harmless no-op it looked like — it rewrote context_size,
		// extra_args, chat_template, profile, n_gpu_layers and all four
		// tri-state caps. The button is disabled too; this is the belt.
		if (!changes || !changes.any) return;
		// Every surfaced key is sent EXPLICITLY — a value to set, or `null` to
		// clear. The server merges `defaults` one level deep now (#1413), so
		// absent means "keep the stored value" and only `null` deletes; omitting
		// an emptied field would silently keep the old one. We start from the
		// FROZEN snapshot's defaults so the keys this drawer doesn't render
		// (rope_freq_base …) ride along as the operator saw them — reading them
		// live meant an unrelated save shipped whatever a mid-edit poll left.
		const defaults = { ...baseline.defaults };
		// ctxError already guarantees a clean /^\d+$/ integer here, so the only two
		// outcomes are "write the number" and "empty = clear the override".
		defaults.context_size = ctx.trim() ? Number(ctx.trim()) : null;
		// n_gpu_layers is no longer a model default (spec-hw-slot-ownership §2): clear
		// any stored value so a save unsets the sunset key rather than round-tripping it.
		defaults.n_gpu_layers = null;
		defaults.extra_args = extra.trim() ? extra : null;
		defaults.chat_template =
			chatTemplate && chatTemplate !== "auto" ? chatTemplate : null;
		defaults.profile = profile.trim() || null;
		// Typed caps: auto = null (no opinion), on/off = boolean.
		defaults.mtp = mtp === "on" ? true : mtp === "off" ? false : null;
		defaults.enable_thinking =
			thinking === "on" ? true : thinking === "off" ? false : null;
		defaults.jinja = jinja === "on" ? true : jinja === "off" ? false : null;
		defaults.vision = vision === "on" ? true : vision === "off" ? false : null;

		const body = { defaults };
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

	// Overrides ledger (cap-overrides.ts): the four tri-state strings above
	// are still the save-body source of truth (defaults.mtp/enable_thinking/
	// jinja/vision) — the ledger just reads/writes them through the
	// null|true|false shape those defaults already use.
	const capFlags = {
		thinking: thinking === "on" ? true : thinking === "off" ? false : null,
		mtp: mtp === "on" ? true : mtp === "off" ? false : null,
		jinja: jinja === "on" ? true : jinja === "off" ? false : null,
		vision: vision === "on" ? true : vision === "off" ? false : null,
	};
	const CAP_SETTERS = { thinking: setThinking, mtp: setMtp, jinja: setJinja, vision: setVision };
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

	// Context size (model's OWN limit — distinct from a slot's Context
	// ceiling, which is hardware). registry/model.py `Model.metadata` reserves
	// `context_length` (GGUF arch max / curated catalogue) — seed the empty-
	// input placeholder from it when the row carries one, same idea as the
	// slot drawer's ceiling placeholder.
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
	const nativeContext = modelContextLength
		? modelContextLength >= 1024
			? `${Math.round(modelContextLength / 1024)}K`
			: String(modelContextLength)
		: null;
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
			{nativeContext && (
				<div style={factCellStyle}>
					<div style={factLabelStyle}>Native ctx</div>
					<div className="mono" style={factValueStyle}>{nativeContext}</div>
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
				eyebrow="Edit model · the launchable thing"
				title={titleNode}
				foot={
					<>
						<span style={{ color: "var(--warn)" }}>
							⟳ changes require the slot to restart
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
								// and firing anyway rewrote the whole defaults block.
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

				{/* ── Launch-command hero: seed button + flags (1b / panel 12 Fix 2/3) ── */}
				<div
					style={{
						margin: "16px 0 4px",
						border: "1px solid var(--line)",
						borderRadius: 8,
						overflow: "hidden",
					}}
				>
					<div
						style={{
							padding: "11px 14px",
							background: "var(--bg-2)",
							borderBottom: "1px solid var(--line)",
							display: "flex",
							alignItems: "center",
							gap: 10,
						}}
					>
						<span
							className="mono"
							style={{
								fontSize: 10,
								letterSpacing: ".08em",
								textTransform: "uppercase",
								color: "var(--fg-3)",
							}}
						>
							launch flags · tune remainder · exactly what launches
						</span>
						<span style={{ flex: 1 }} />
						<SeedProfileButton
							options={fitProfiles}
							onPick={seedFromProfile}
							disabled={seedProfile.isPending}
						/>
					</div>
					<div style={{ padding: 14, background: "var(--bg-sunken)" }}>
						<FlagsEditor
							value={extra}
							onChange={setExtra}
							invalid={!!flagsError}
						/>
						{flagsError && (
							<div
								className="err"
								data-testid="model-flags-error"
								style={{ marginTop: 8 }}
							>
								{flagsError}
							</div>
						)}
						<div
							style={{
								marginTop: 10,
								paddingTop: 10,
								borderTop: "1px solid var(--line-soft)",
								display: "flex",
								alignItems: "center",
								gap: 8,
								flexWrap: "wrap",
							}}
						>
							<span
								className="mono"
								style={{ fontSize: 10, color: "var(--fg-5)" }}
							>
								+ managed:
							</span>
							<span
								className="m"
								style={{ fontSize: 10, color: "var(--fg-4)" }}
							>
								--model
							</span>
							<span
								className="m"
								style={{ fontSize: 10, color: "var(--fg-4)" }}
							>
								--host
							</span>
							<span
								className="m"
								style={{ fontSize: 10, color: "var(--fg-4)" }}
							>
								--port
							</span>
							<span
								className="mono"
								style={{ fontSize: 10, color: "var(--fg-5)" }}
							>
								· authority-owned, computed &amp; rejected on save
							</span>
						</div>
					</div>
					<div
						style={{
							padding: "9px 14px",
							background: "var(--bg-2)",
							borderTop: "1px solid var(--line)",
							display: "flex",
							alignItems: "center",
							gap: 8,
							flexWrap: "wrap",
						}}
					>
						{profile ? (
							<span
								className="tag"
								data-testid="model-provenance-chip"
								style={{
									color: "var(--fg-3)",
									borderColor: "var(--line)",
									background: "var(--bg-2)",
									fontFamily: "var(--jbm)",
									fontSize: 9,
									letterSpacing: ".05em",
									textTransform: "uppercase",
									padding: "2px 6px",
									borderRadius: 3,
									border: "1px solid var(--line)",
								}}
							>
								seeded from {profile}
							</span>
						) : (
							<span
								className="tag"
								data-testid="model-provenance-chip"
								style={{
									color: "var(--fg-4)",
									borderColor: "var(--line)",
									background: "var(--bg-2)",
									fontFamily: "var(--jbm)",
									fontSize: 9,
									letterSpacing: ".05em",
									textTransform: "uppercase",
									padding: "2px 6px",
									borderRadius: 3,
									border: "1px solid var(--line)",
								}}
							>
								no template
							</span>
						)}
						{diverged && (
							<span
								className="tag"
								data-testid="model-diverged-chip"
								title={`Flags differ from ${profile}'s current text. The model owns these — the profile won't change them.`}
								style={{
									color: "var(--warn)",
									borderColor: "var(--warn-line)",
									background: "var(--warn-soft)",
									fontFamily: "var(--jbm)",
									fontSize: 9,
									letterSpacing: ".05em",
									textTransform: "uppercase",
									padding: "2px 6px",
									borderRadius: 3,
									border: "1px solid var(--warn-line)",
								}}
							>
								◆ diverged from {profile}
							</span>
						)}
					</div>
				</div>
				{diverged && diff && (
					<DivergenceDiff
						diff={diff}
						profileName={profile}
						onReset={resetToProfile}
					/>
				)}

				{/* ── Capabilities (1a form-row rhythm; panel 07) ── */}
				<div className="form-section" style={{ marginTop: 16 }}>
					Capabilities
				</div>
				<div className="form-row">
					<div className="form-lbl">
						<span>Chat template</span>
						<FieldInfoIcon description="auto = use the template embedded in the GGUF" />
					</div>
					<div className="form-ctl">
						<select
							className="input mono chat-template-select"
							data-testid="model-chat-template"
							value={chatTemplate}
							onChange={(e) => setChatTemplate(e.target.value)}
						>
							<option value="auto">Auto (GGUF embedded)</option>
							{(Array.isArray(templates.data) ? templates.data : [])
								.filter((t) => t.id !== "auto")
								.map((t) => (
									<option key={t.id} value={t.id}>
										{t.label}
									</option>
								))}
						</select>
					</div>
				</div>
				{/* Overrides ledger (panel 09 V1): Auto is invisible — only an
            overridden Thinking/MTP/Jinja/Vision renders a chip. Replaces the
            four always-on TypedCapSeg rows the old drawer wore permanently. */}
				<div className="form-row">
					<div className="form-lbl">
						<span>Overrides</span>
						<FieldInfoIcon description="Auto is invisible here — thinking, MTP, jinja and vision default to it. '+ Override…' forces one on or off; ✕ returns it to Auto." />
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

				{/* ── Numeric tune (typed source of the managed --ctx-size) ── */}
				<div className="form-row">
					<div className="form-lbl">
						<span>Context size</span>
						<FieldInfoIcon description="tokens · the model's own limit — a slot's Context ceiling is hardware · empty = launcher default · sets managed --ctx-size" />
					</div>
					<div className="form-ctl">
						<input
							className={"input mono" + (ctxError ? " input-err" : "")}
							data-testid="model-ctx-input"
							inputMode="numeric"
							placeholder={modelContextLength ? String(modelContextLength) : "e.g. 8192"}
							value={ctx}
							onChange={(e) => setCtx(e.target.value)}
						/>
						{ctxError && (
							<div
								className="hint"
								data-testid="model-ctx-error"
								style={{ color: "var(--err)" }}
							>
								{ctxError}
							</div>
						)}
					</div>
				</div>
				{/* n_gpu_layers input removed (spec-hw-slot-ownership §2/§6): NGL is
            slot-owned hardware now (the slot's HW grid), not a model default.
            The one-shot migration folds model.defaults.n_gpu_layers → slot NGL. */}

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
						<select
							className="input mono"
							data-testid="model-provider-select"
							value={provider}
							onChange={(e) => setProvider(e.target.value)}
						>
							<option value="">Auto (derive from tags)</option>
							{enums.runtime_families.map((rf) => (
								<option key={rf} value={rf}>
									{rf}
								</option>
							))}
						</select>
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

				{/* ── Source · re-pull coords ── */}
				<div className="form-section" style={{ marginTop: 16 }}>
					Source · re-pull coords
				</div>
				<div className="form-row">
					<div className="form-lbl">
						<span>MMProj</span>
						<FieldInfoIcon description="vision projector sidecar path" />
					</div>
					<div className="form-ctl">
						<input
							className="input mono"
							data-testid="model-mmproj-input"
							placeholder="/var/lib/hal0/models/…/mmproj-Q8.gguf"
							value={mmproj}
							onChange={(e) => setMmproj(e.target.value)}
						/>
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
						<FieldInfoIcon description="HuggingFace repo · needed to re-pull" />
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

				{update.isError && (
					<div className="err">{update.error?.message || "Save failed"}</div>
				)}
			</Drawer>

			{confirm && (
				<ConfirmDialog
					open={!!confirm}
					onCancel={() => setConfirm(null)}
					onConfirm={confirm.onConfirm}
					title={confirm.title}
					message={confirm.message}
					// Seeding is a real POST — block a second click firing a second
					// one while the first is in flight. Only the seed confirm carries
					// `kind: "seed"`; other confirms (e.g. the local, network-free
					// "Reset to profile") aren't gated on this mutation.
					confirmLabel={
						confirm.kind === "seed" && seedProfile.isPending
							? "Seeding…"
							: confirm.confirmLabel
					}
					confirmDisabled={confirm.kind === "seed" && seedProfile.isPending}
				/>
			)}
		</>
	);
}

// Managed-arg rejection copy (inline, on save) — cause → why → next, naming the
// offending flag + where it's actually controlled from.
function managedFlagMessage(offenders) {
	const first = offenders[0];
	const where =
		MANAGED_FLAG_SOURCE[first] ||
		MANAGED_FLAG_SOURCE[canonManagedForMsg(first)] ||
		"the slot/model configuration";
	const rest =
		offenders.length > 1
			? ` (also managed: ${offenders.slice(1).join(", ")})`
			: "";
	return `${first} is computed by hal0 and can't be set here — it comes from ${where}. Remove it.${rest}`;
}
function canonManagedForMsg(flag) {
	if (flag === "-ngl") return "--n-gpu-layers";
	if (flag === "-c") return "--ctx-size";
	return flag;
}

// Slot-hardware rejection copy (spec-hw-slot-ownership §5): the model is
// device-agnostic — hardware flags belong on the slot's HW grid. Names the
// offending flag(s) and points at where they're set.
function slotHardwareFlagMessage(offenders) {
	const first = offenders[0];
	const rest =
		offenders.length > 1 ? ` (also: ${offenders.slice(1).join(", ")})` : "";
	return `${first} is hardware — it belongs on the slot (device · NGL · THREADS grid), not the model. The model is device-agnostic. Remove it.${rest}`;
}

Object.assign(window, {
	ModelDrawer,
	FlagsEditor,
	DivergenceDiff,
});
