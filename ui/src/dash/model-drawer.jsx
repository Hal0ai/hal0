// hal0 dashboard — Model editor drawer (D1, post-R3 surface rework).
//
// Replaces RecipeEditorModal (model-modals.jsx) with a right-side Drawer built
// around one claim: "the model is the launchable thing." Converged design per
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
	useModelDuplicate,
} from "@/api/hooks/useModels";
import { useChatTemplates } from "@/api/hooks/useChatTemplates";
import { useProfiles } from "@/api/hooks/useProfiles";
import { useMetaEnums } from "@/api/hooks/useMeta";
import {
	canonicalCapabilities,
	modelDeviceClasses,
	profileDeviceClass,
} from "@/lib/deviceMeta";
import {
	MODEL_TYPE_TAGS,
	splitModelTags,
	mergeModelTags,
} from "@/dash/model-types.js";
import {
	findManagedFlags,
	findSlotHardwareFlags,
	MANAGED_FLAG_SOURCE,
	highlightSegments,
	diffFlags,
	tokenizeFlags,
} from "@/dash/flags-tune.js";

const {
	useState: useStateMD,
	useEffect: useEffectMD,
	useMemo: useMemoMD,
	useRef: useRefMD,
} = React;

// Order-insensitive set equality for array fields (capabilities/backends), so a
// defaults-only save doesn't spuriously report those as changed.
function sameSet(a, b) {
	const sa = [...a].sort();
	const sb = [...b].sort();
	return sa.length === sb.length && sa.join(" ") === sb.join(" ");
}

// spec-hw-slot-ownership §1/§8: the model is device-agnostic — it carries no
// device, runner, or image. The former deviceFlavour() chip (and the read-only
// Runner section) were removed; device lives on the slot's HW grid now.

// Modality read-out derived from the model's capabilities/type (typed field —
// shown read-only in the drawer; the capability toggles below are the control).
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

// ─── TypedCapSeg — Auto / On / Off segmented control (1a rhythm) ─────────────
function TypedCapSeg({ id, value, onChange }) {
	const OPTS = [
		{ key: "auto", label: "auto" },
		{ key: "on", label: "on" },
		{ key: "off", label: "off" },
	];
	return (
		<div style={{ display: "flex", gap: 6 }} role="radiogroup" aria-label={id}>
			{OPTS.map((o) => {
				const on = value === o.key;
				return (
					<button
						key={o.key}
						type="button"
						role="radio"
						aria-checked={on}
						data-testid={`cap-${id}-${o.key}`}
						className={"mdl-chip" + (on ? " on" : "")}
						onClick={() => onChange(o.key)}
					>
						{o.label}
					</button>
				);
			})}
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
				placeholder="pick a profile to seed flags, or type your own · e.g. -fa on -b 2048 --threads 8"
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

// ─── TemplatePicker — profile dropdown grouped by device flavour ─────────────
// Selecting a profile is the STAMP gesture (copy its flags into the editor) and
// the device gesture (templates are device-flavoured). Grouped options, filtered
// to profiles that fit this model.
function TemplatePicker({ value, options, onPick }) {
	return (
		<select
			className="input mono"
			data-testid="model-template-select"
			value={value || ""}
			onChange={(e) => onPick(e.target.value)}
			style={{ width: "100%" }}
		>
			<option value="">— no template —</option>
			{options.map((p) => (
				<option key={p.name} value={p.name}>
					{p.name}
					{p.intent ? ` · ${p.intent}` : ""}
				</option>
			))}
		</select>
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

// ─── DuplicateModelDialog — duplicate for a second device (1dup) ─────────────
// Wired to the real POST /api/models/{id}/duplicate route (UI-API-1,
// models.py:674 `duplicate_model`): weights are refcounted (no re-download,
// no byte copy) and the new row copies the source's metadata/defaults/
// capabilities/backends. Picking a device template stamps that profile's
// flags into the new row's defaults server-side (copy-not-layer — the
// profile itself is never mutated).
function DuplicateModelDialog({ open, onClose, model, profiles }) {
	const duplicate = useModelDuplicate();
	const [pick, setPick] = useStateMD("");
	const [newId, setNewId] = useStateMD("");
	// Tracks whether the operator has hand-edited the id field so the
	// suggested-id effect below stops clobbering their typing once they start
	// (same "don't stomp user input" convention as the flags-stamp confirm).
	const [idTouched, setIdTouched] = useStateMD(false);
	const [err, setErr] = useStateMD(null);
	// Device-flavoured templates the operator can stamp the duplicate with.
	const devProfiles = useMemoMD(() => {
		const all = Array.isArray(profiles) ? profiles : [];
		// one representative profile per device class, excluding the model's current.
		const seen = new Set();
		const out = [];
		for (const p of all) {
			const cls = profileDeviceClass(p) || "";
			if (!cls || seen.has(cls)) continue;
			seen.add(cls);
			out.push(p);
		}
		return out;
	}, [profiles]);
	useEffectMD(() => {
		if (open) {
			setPick(devProfiles[0]?.name || "");
			setIdTouched(false);
			setErr(null);
		}
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [open, model?.id, devProfiles]);

	// Suggested new_id: `<source id>-<device class or profile name>`, re-derived
	// as the template pick changes unless the operator has typed their own.
	const suggestedId = useMemoMD(() => {
		if (!model) return "";
		const prof = (profiles || []).find((p) => p.name === pick);
		const suffix = prof ? profileDeviceClass(prof) || prof.name : "copy";
		return `${model.id}-${suffix}`;
	}, [model, profiles, pick]);
	useEffectMD(() => {
		if (open && !idTouched) setNewId(suggestedId);
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [open, suggestedId, idTouched]);

	if (!open || !model) return null;

	const trimmedId = newId.trim();
	const idInvalid = !trimmedId || trimmedId === model.id;

	const onConfirm = async () => {
		if (duplicate.isPending) return; // guard against double-submit
		if (idInvalid) {
			setErr("pick a new model id, different from the source");
			return;
		}
		setErr(null);
		try {
			const result = await duplicate.mutateAsync({
				id: model.id,
				new_id: trimmedId,
				profile: pick || undefined,
			});
			window.__hal0Toast &&
				window.__hal0Toast(`Duplicated → ${result?.id || trimmedId}`, "ok");
			onClose();
		} catch (e) {
			// Server 409s on a taken id, 404s on an unknown profile/source — surface
			// the envelope message inline (RenameSlotDialog's pattern) AND toast.
			const msg = e?.message || "duplicate failed — see logs";
			setErr(msg);
			window.__hal0Toast &&
				window.__hal0Toast(`Duplicate failed — ${msg}`, "err");
		}
	};
	return (
		<ConfirmDialog
			open={open}
			onCancel={onClose}
			onConfirm={onConfirm}
			title={`Duplicate ${model.longName || model.name || model.id}?`}
			confirmLabel={duplicate.isPending ? "Duplicating…" : "Duplicate"}
			footerNote="The duplicate can be deleted at any time; weights are shared."
			message={
				<span>
					A new model row shares the same weights (refcounted — no re-download)
					and can be stamped with a device template. You can tune its flags
					independently.
					<br />
					<br />
					<span className="mono" style={{ fontSize: 11, color: "var(--fg-3)" }}>
						device template
					</span>
					<select
						className="input mono"
						data-testid="model-duplicate-device"
						value={pick}
						onChange={(e) => setPick(e.target.value)}
						style={{ width: "100%", marginTop: 6 }}
					>
						<option value="">— no template —</option>
						{devProfiles.map((p) => (
							<option key={p.name} value={p.name}>
								{p.name}
								{p.intent ? ` · ${p.intent}` : ""}
							</option>
						))}
					</select>
					<div style={{ marginTop: 10 }}>
						<span
							className="mono"
							style={{ fontSize: 11, color: "var(--fg-3)" }}
						>
							new model id
						</span>
						<input
							className="input mono"
							data-testid="model-duplicate-id"
							value={newId}
							onChange={(e) => {
								setNewId(e.target.value);
								setIdTouched(true);
							}}
							style={{ width: "100%", marginTop: 6 }}
						/>
					</div>
					{err && (
						<div
							className="err"
							data-testid="model-duplicate-error"
							style={{ marginTop: 8 }}
						>
							{err}
						</div>
					)}
				</span>
			}
		/>
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
	const split = splitModelTags(model.tags);
	return {
		id: model.id,
		name: model.name || "",
		types: split.selected,
		otherTags: split.other,
		tags: Array.isArray(model.tags) ? model.tags : [],
		caps: canonicalCapabilities(model.capabilities, enums),
		backends: Array.isArray(model.backends) ? model.backends : [],
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
	const nextTags = mergeModelTags(baseline.otherTags, form.types);
	const c = {
		// Diff on the value alone (#1381): a truthiness guard here once collapsed
		// "unchanged" and "deliberately emptied" into the same skip branch, so the
		// name could never be cleared.
		name: trimmedName !== baseline.name,
		trimmedName,
		nextTags,
		tags: !sameSet(nextTags, baseline.tags),
		caps: !sameSet(form.caps, baseline.caps),
		backends: !sameSet(form.backends, baseline.backends),
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
		c.tags ||
		c.caps ||
		c.backends ||
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
function ModelDrawer({ open, onClose, model }) {
	const update = useModelUpdate();
	const setDefault = useModelSetDefault();
	const templates = useChatTemplates(open);
	const profilesQuery = useProfiles();
	const enums = useMetaEnums();

	// Identity + typed fields (preserve the full RecipeEditor save surface).
	const [name, setName] = useStateMD("");
	const [types, setTypes] = useStateMD([]);
	// (Non-curated tags are not editable here; they live on the frozen baseline
	// as `otherTags` and are re-merged into the write by deriveModelChanges. A
	// second mutable copy in component state would be exactly the drift this
	// seam exists to prevent.)
	const [caps, setCaps] = useStateMD([]);
	const [backends, setBackends] = useStateMD([]);
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
	const [dupOpen, setDupOpen] = useStateMD(false);
	const [confirm, setConfirm] = useStateMD(null); // {title,message,confirmLabel,onConfirm}
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
		setTypes(b.types);
		setCaps(b.caps);
		setBackends(b.backends);
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

	const toggleType = (t) =>
		setTypes((p) => (p.includes(t) ? p.filter((x) => x !== t) : [...p, t]));
	const toggleCap = (c) =>
		setCaps((p) => (p.includes(c) ? p.filter((x) => x !== c) : [...p, c]));
	const toggleBackend = (b) =>
		setBackends((p) => (p.includes(b) ? p.filter((x) => x !== b) : [...p, b]));

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
	// gate both `onSave` and the Save button consult.
	const mmprojError =
		caps.includes("vision") && !mmproj.trim()
			? "vision capability requires an mmproj sidecar path"
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

	// Return null when closed — matching the Modal contract the old
	// RecipeEditorModal honoured (Modal returns null when !open). The <Drawer>
	// primitive otherwise stays mounted, and `selected` is non-null even when the
	// drawer is shut, so an always-mounted drawer would leave phantom inputs in
	// the DOM (colliding with the AddByHF modal's fields). All hooks run above.
	if (!open || !model) return null;

	// STAMP: selecting a profile copies its flags into the editor. Confirm if the
	// current flags would be clobbered (non-empty and not already the target text).
	const stampProfile = (nextName) => {
		const target = (profilesQuery.data || []).find((p) => p.name === nextName);
		const targetFlags = target ? target.flags || "" : "";
		const doStamp = () => {
			setProfile(nextName);
			setExtra(targetFlags);
			setConfirm(null);
		};
		if (!nextName) {
			setProfile("");
			setConfirm(null);
			return;
		}
		const wouldClobber = extra.trim() && diffFlags(extra, targetFlags).diverged;
		if (wouldClobber) {
			setConfirm({
				title: "Replace launch flags?",
				message: `Replace flags with ${nextName}'s template? Unsaved edits to the current flags are lost.`,
				confirmLabel: "Replace flags",
				onConfirm: doStamp,
			});
		} else {
			doStamp();
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

	// THE comparison — derived once, against the FROZEN baseline, read by both
	// the unsaved-changes guard and the save body. Nothing below re-derives a
	// predicate and nothing touches the live `model` prop.
	const changes = deriveModelChanges(baseline, {
		name,
		types,
		caps,
		backends,
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
		if (changes.tags) body.tags = changes.nextTags;
		if (changes.caps) body.capabilities = caps;
		if (changes.backends) body.backends = backends;
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

	const modality = modalityLabel(caps, model.type);

	return (
		<>
			<Drawer
				open={open}
				onClose={onClose}
				width={600}
				dirty={dirty}
				eyebrow="Edit model · the launchable thing"
				title={model.longName || model.name || model.id}
				foot={
					<>
						<span style={{ color: "var(--warn)" }}>
							⟳ changes require the slot to restart
						</span>
						<span style={{ display: "inline-flex", gap: 8 }}>
							<button
								className="btn ghost sm"
								data-testid="model-duplicate-open"
								onClick={() => setDupOpen(true)}
							>
								⋯ Duplicate for device
							</button>
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
				{/* ── Identity ── */}
				<div className="form-row">
					<div className="form-lbl">
						<span>Display name</span>
						<FieldInfoIcon description="empty keeps the model id" />
					</div>
					<div className="form-ctl">
						<input
							className="input"
							data-testid="model-name-input"
							placeholder={model.id}
							value={name}
							onChange={(e) => setName(e.target.value)}
						/>
					</div>
				</div>
				<div className="form-row">
					<div className="form-lbl">
						<span>Types</span>
						<FieldInfoIcon description="capability tags · drive routing &amp; slot features" />
					</div>
					<div
						className="form-ctl"
						style={{ display: "flex", flexWrap: "wrap", gap: 6 }}
					>
						{MODEL_TYPE_TAGS.map((tag) => {
							const on = types.includes(tag);
							return (
								<button
									key={tag}
									type="button"
									role="switch"
									aria-checked={on}
									data-testid={`type-toggle-${tag}`}
									className={"mdl-chip" + (on ? " on" : "")}
									onClick={() => toggleType(tag)}
								>
									{tag}
								</button>
							);
						})}
					</div>
				</div>

				{/* ── Per-type default marker (Set / Remove) ── */}
				<div className="form-row">
					<div className="form-lbl">
						<span>Default for {typeLabel}</span>
						<FieldInfoIcon description="the model this type falls back to · one per type" />
					</div>
					<div
						className="form-ctl"
						style={{
							display: "flex",
							alignItems: "center",
							gap: 10,
							flexWrap: "wrap",
						}}
					>
						{isTypeDefault ? (
							<span
								className="tag"
								data-testid="model-default-badge"
								style={{
									color: "var(--ok)",
									borderColor: "var(--ok)",
									background: "var(--bg-2)",
									fontFamily: "var(--jbm)",
									fontSize: 9,
									letterSpacing: ".05em",
									textTransform: "uppercase",
									padding: "2px 6px",
									borderRadius: 3,
									border: "1px solid var(--ok)",
								}}
							>
								✓ {typeLabel} default
							</span>
						) : (
							<span
								className="tag"
								data-testid="model-default-none"
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
								not the default
							</span>
						)}
						<button
							type="button"
							className="btn ghost sm"
							data-testid="model-default-toggle"
							onClick={onToggleDefault}
							disabled={setDefault.isPending}
						>
							{setDefault.isPending
								? "Saving…"
								: isTypeDefault
									? "Remove default"
									: "Set as default"}
						</button>
					</div>
				</div>

				{/* ── Launch-command hero: template + flags (1b) ── */}
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
						<div style={{ minWidth: 190 }}>
							<TemplatePicker
								value={profile}
								options={fitProfiles}
								onPick={stampProfile}
							/>
						</div>
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

				{/* ── Typed capabilities (1a form-row rhythm) ── */}
				<div className="form-section" style={{ marginTop: 16 }}>
					Typed capabilities
				</div>
				<div className="form-row">
					<div className="form-lbl">
						<span>Thinking</span>
						<FieldInfoIcon description="Show reasoning steps before the answer. Auto defers to profile." />
					</div>
					<div className="form-ctl">
						<TypedCapSeg
							id="thinking"
							value={thinking}
							onChange={setThinking}
						/>
					</div>
				</div>
				<div className="form-row">
					<div className="form-lbl">
						<span>MTP</span>
						<FieldInfoIcon description="Speculative decode. Auto defers to eligibility." />
					</div>
					<div className="form-ctl">
						<TypedCapSeg id="mtp" value={mtp} onChange={setMtp} />
					</div>
				</div>
				<div className="form-row">
					<div className="form-lbl">
						<span>Jinja</span>
						<FieldInfoIcon description="jinja chat-template rendering" />
					</div>
					<div className="form-ctl">
						<TypedCapSeg id="jinja" value={jinja} onChange={setJinja} />
					</div>
				</div>
				<div className="form-row">
					<div className="form-lbl">
						<span>Vision</span>
						<FieldInfoIcon description="Load the mmproj vision projector when the model ships one.
							Auto = on whenever a sidecar is present. Off force-suppresses it
							(saves ~0.9 GB VRAM) even on a vision-capable model." />
					</div>
					<div className="form-ctl">
						<TypedCapSeg id="vision" value={vision} onChange={setVision} />
					</div>
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
				<div className="form-row">
					<div className="form-lbl">
						<span>Modality</span>
						<FieldInfoIcon description="derived from capabilities" />
					</div>
					<div className="form-ctl">
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
					</div>
				</div>

				{/* ── Numeric tune (typed source of the managed --ctx-size) ── */}
				<div className="form-row">
					<div className="form-lbl">
						<span>Context size</span>
						<FieldInfoIcon description="tokens · empty = launcher default · sets managed --ctx-size" />
					</div>
					<div className="form-ctl">
						<input
							className={"input mono" + (ctxError ? " input-err" : "")}
							data-testid="model-ctx-input"
							inputMode="numeric"
							placeholder="e.g. 8192"
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

				{/* ── Routing (capabilities + backends) ── */}
				<div className="form-section" style={{ marginTop: 16 }}>
					Routing
				</div>
				<div className="form-row">
					<div className="form-lbl">
						<span>Capabilities</span>
						<FieldInfoIcon description="dispatch / omni eligibility · canonical vocab" />
					</div>
					<div
						className="form-ctl"
						style={{ display: "flex", flexWrap: "wrap", gap: 6 }}
					>
						{enums.model_capabilities.map((cap) => {
							const on = caps.includes(cap);
							return (
								<button
									key={cap}
									type="button"
									role="switch"
									aria-checked={on}
									data-testid={`cap-toggle-${cap}`}
									className={"mdl-chip" + (on ? " on" : "")}
									onClick={() => toggleCap(cap)}
								>
									{cap}
								</button>
							);
						})}
					</div>
				</div>
				<div className="form-row">
					<div className="form-lbl">
						<span>Backends</span>
						<FieldInfoIcon description="runners this model can bind · drives compatible-runner filtering" />
					</div>
					<div
						className="form-ctl"
						style={{ display: "flex", flexWrap: "wrap", gap: 6 }}
					>
						{enums.model_backends.map((b) => {
							const on = backends.includes(b);
							return (
								<button
									key={b}
									type="button"
									role="switch"
									aria-checked={on}
									data-testid={`backend-toggle-${b}`}
									className={"mdl-chip" + (on ? " on" : "")}
									onClick={() => toggleBackend(b)}
								>
									{b}
								</button>
							);
						})}
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

			<DuplicateModelDialog
				open={dupOpen}
				onClose={() => setDupOpen(false)}
				model={model}
				profiles={profilesQuery.data}
			/>

			{confirm && (
				<ConfirmDialog
					open={!!confirm}
					onCancel={() => setConfirm(null)}
					onConfirm={confirm.onConfirm}
					title={confirm.title}
					message={confirm.message}
					confirmLabel={confirm.confirmLabel}
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
	DuplicateModelDialog,
});
